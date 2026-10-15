"""canvas.py — everything about the world and the app.

This holds all the *classes* (canvas objects, camera, scene, UI) and the helper
functions that serve them (fonts, actions, persistence). If it's substantive app
logic, it lives here — game_loop.py is only the thin runner that wires this together.

Section order is top-to-bottom by dependency, so nothing references something defined
below it. game_loop.py imports from here; this file never imports from game_loop.py
(that would be a circular import).
"""

import ast
import copy
import ctypes
import json
import math
import os
import random
import re
import subprocess
import time
import traceback
import uuid

import pygame


# ============================================================================
# CONFIG — constants shared across the app (colors, sizes, zoom, timings, speeds)
# ============================================================================
# Anchor all file paths here, to this file's own folder — never bare relative
# paths (they resolve against the process's working dir, not the script).
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
NODES_FILE = os.path.join(SCRIPT_DIR, "Nodes.json")
ERRORS_FILE = os.path.join(SCRIPT_DIR, "errors.log")   # the app has no console: errors go here

# Scene
BG_COLOR = (25, 25, 30)
SELECTION_COLOR = (255, 210, 60)

# Nodes
NODE_WIDTH = 200
NODE_HEIGHT = 60

# Button look
BUTTON_BASE = (50, 50, 60)
BUTTON_HOVER = (70, 70, 90)
BUTTON_CLICK = (30, 30, 40)
BUTTON_BORDER = (200, 200, 210)
BUTTON_TEXT = (255, 255, 255)
BUTTON_BASE_FONT_SIZE = 32   # label px at zoom 1; world units == px at zoom 1
BUTTON_TEXT_PADDING = 40     # min clearance kept around the label when auto-widening
BUTTON_MIN_SCALE = 0.05      # at/below this zoom a button draws as a plain dot (LOD)

# NameTag look (box + text never scale with zoom)
NAMETAG_BASE = (50, 50, 60)
NAMETAG_HOVER = (70, 70, 90)
NAMETAG_CLICK = (30, 30, 40)
NAMETAG_TEXT = (170, 210, 255)   # kept distinct from a button's white label
NAMETAG_FONT_SIZE = 30
NAMETAG_PADDING = (24, 14)

# Interaction / camera
SCROLL_STEP = 0.1
MOVE_SPEED = 400            # world units/sec applied to the selected node
DOUBLE_CLICK_MS = 400
GOTO_ZOOM = 1.0            # zoom "Go To" / double-click jumps to (readable)


def new_id():
    """Short, stable, unique id for a canvas object."""
    return "n_" + uuid.uuid4().hex[:8]


_reported = set()


def report_error(where, exc=None):
    """Append an error's traceback to errors.log — once per distinct
    (where, error) — since the app runs without a console."""
    key = (where, repr(exc))
    if key in _reported:
        return
    _reported.add(key)
    try:
        with open(ERRORS_FILE, "a", encoding="utf-8") as f:
            stamp = time.strftime("%Y-%m-%d %H:%M:%S")
            trace = ("".join(traceback.format_exception(exc)) if exc is not None
                     else traceback.format_exc())
            f.write(f"--- {stamp}  {where}\n{trace}\n")
    except OSError:
        pass


# ============================================================================
# FONTS — cached pygame fonts so per-frame re-rendering doesn't rebuild them
# ============================================================================
_font_cache = {}


FONT_CACHE_MAX = 300        # zooming makes a font per size; past this, the least recently used go
FONT_EXACT_MAX = 30         # sizes up to this are exact; above, they're snapped to ~5% steps
TEXT_CACHE_MAX = 128        # remembered renders per font
TEXT_CACHE_MAX_PIXELS = 600_000   # don't keep huge renders (deep zoom) — just redraw those


class CachedFont(pygame.font.Font):
    """A Font that remembers what it rendered and measured. Most text on the
    canvas is the same frame after frame (labels, readouts, the inspector), so
    this turns per-frame text rendering into a dictionary lookup. Every font
    from get_font is one of these; callers must not draw onto what render()
    returns (blitting it elsewhere is fine)."""

    def __init__(self, *args):
        super().__init__(*args)
        self._renders = {}
        self._sizes = {}

    def render(self, text, antialias, color, bgcolor=None):
        key = (text, antialias, tuple(color), None if bgcolor is None else tuple(bgcolor))
        surf = self._renders.get(key)
        if surf is None:
            surf = super().render(text, antialias, color, bgcolor)
            if surf.get_width() * surf.get_height() <= TEXT_CACHE_MAX_PIXELS:
                if len(self._renders) >= TEXT_CACHE_MAX:
                    self._renders.clear()
                self._renders[key] = surf
        return surf

    def size(self, text):
        found = self._sizes.get(text)
        if found is None:
            if len(self._sizes) >= TEXT_CACHE_MAX * 4:
                self._sizes.clear()
            found = self._sizes[text] = super().size(text)
        return found


def _make_cached_font(path, size, bold, italic):
    font = CachedFont(path, size)
    font.set_bold(bool(bold))
    font.set_italic(bool(italic))
    return font


def font_px(size):
    """The pixel size get_font actually uses for `size`: exact up to
    FONT_EXACT_MAX, above that snapped to ~5% steps, so a zoom reuses fonts
    and renders instead of making new ones every frame. Key text caches on it."""
    size = max(1, int(size))
    if size > FONT_EXACT_MAX:
        step = 1.05
        size = round(FONT_EXACT_MAX * step ** round(math.log(size / FONT_EXACT_MAX, step)))
    return size


def get_font(size, name=None, bold=False):
    """A cached font: pygame's default, or a system font by name ("georgia",
    "bahnschrift", …). A name that isn't installed falls back to the default.
    Fonts remember their renders (see CachedFont)."""
    key = (font_px(size), name, bold)
    font = _font_cache.pop(key, None)       # re-inserted below: the dict stays in use order
    if font is None:
        if len(_font_cache) >= FONT_CACHE_MAX:
            del _font_cache[next(iter(_font_cache))]     # drop the least recently used
        font = pygame.font.SysFont(name, key[0], bold=bold, constructor=_make_cached_font)
    _font_cache[key] = font
    return font


# ============================================================================
# DRAWING — pygame.draw stand-ins that stay fast when zoomed far in
# ============================================================================
# pygame draws rounded corners and thick arcs without skipping what's off
# screen, so their cost grows with their size: a rounded rect 20,000 px across
# takes ~40 ms, 200,000 px ~4 s — and zoomed into an object, its shapes get
# that big. These build big curves out of polygons (which pygame clips
# cheaply) and only curve the corners that are on screen. Small shapes go
# straight to pygame, so they look exactly as before.
BIG_CURVE_PX = 48           # radii above this are drawn as polygons


def _arc_points(cx, cy, rx, ry, a0, a1):
    """Points along an ellipse from angle a0 to a1 (radians, in screen
    coordinates: y points down, so increasing angles run clockwise). Enough
    of them that the straight pieces stay within ~½ px of the true curve."""
    steps = max(2, min(1200, math.ceil(0.9 * math.sqrt(max(rx, ry)) * abs(a1 - a0) / (math.pi / 2))))
    return [(cx + rx * math.cos(a0 + (a1 - a0) * k / steps),
             cy + ry * math.sin(a0 + (a1 - a0) * k / steps)) for k in range(steps + 1)]


def draw_rect(surface, color, rect, width=0, border_radius=0):
    """pygame.draw.rect, except that big rounded corners don't stall the frame."""
    rect = pygame.Rect(rect)
    r = min(border_radius, rect.width // 2, rect.height // 2)
    if r <= BIG_CURVE_PX or width < 0:
        return pygame.draw.rect(surface, color, rect, width, border_radius)
    view = surface.get_clip()
    if not rect.colliderect(view):
        return pygame.Rect(rect.topleft, (0, 0))
    x0, y0, x1, y1 = rect.left, rect.top, rect.right - 1, rect.bottom - 1
    corners = (   # (arc center, start angle, the square corner it rounds off, that corner's box)
        ((x0 + r, y0 + r), math.pi, (x0, y0), pygame.Rect(x0, y0, r, r)),
        ((x1 - r, y0 + r), math.pi * 1.5, (x1, y0), pygame.Rect(x1 - r + 1, y0, r, r)),
        ((x1 - r, y1 - r), 0.0, (x1, y1), pygame.Rect(x1 - r + 1, y1 - r + 1, r, r)),
        ((x0 + r, y1 - r), math.pi * 0.5, (x0, y1), pygame.Rect(x0, y1 - r + 1, r, r)),
    )
    if width == 0 or 2 * width >= min(rect.size):          # filled: one polygon
        points = []
        for (cx, cy), a0, square, box in corners:
            if box.colliderect(view):
                points += _arc_points(cx, cy, r, r, a0, a0 + math.pi / 2)
            else:
                points.append(square)       # off screen: its curve can't be seen anyway
        return pygame.draw.polygon(surface, color, points)
    # Border: four straight bands, plus a curved band at each visible corner.
    w = width
    pygame.draw.rect(surface, color, (x0 + r, y0, rect.width - 2 * r, w))
    pygame.draw.rect(surface, color, (x0 + r, rect.bottom - w, rect.width - 2 * r, w))
    pygame.draw.rect(surface, color, (x0, y0 + r, w, rect.height - 2 * r))
    pygame.draw.rect(surface, color, (rect.right - w, y0 + r, w, rect.height - 2 * r))
    for (cx, cy), a0, _square, box in corners:
        if box.colliderect(view):
            outer = _arc_points(cx, cy, r, r, a0, a0 + math.pi / 2)
            ri = r - w + 1                  # +1: polygons include their edge pixels
            inner = _arc_points(cx, cy, ri, ri, a0 + math.pi / 2, a0) if ri > 0 else [(cx, cy)]
            pygame.draw.polygon(surface, color, outer + inner)
    return rect.clip(view)


def draw_arc(surface, color, rect, start_angle, stop_angle, width=1):
    """pygame.draw.arc (radians, counterclockwise from 3 o'clock), except that
    big thick arcs don't stall the frame."""
    rect = pygame.Rect(rect)
    rx, ry = rect.width / 2, rect.height / 2
    if max(rx, ry) <= BIG_CURVE_PX or width < 1:
        return pygame.draw.arc(surface, color, rect, start_angle, stop_angle, width)
    if not rect.colliderect(surface.get_clip()):
        return pygame.Rect(rect.center, (0, 0))
    if stop_angle < start_angle:
        stop_angle += 2 * math.pi
    cx, cy = rect.center
    # pygame measures angles counterclockwise; screen y points down, so negate.
    outer = _arc_points(cx, cy, rx, ry, -start_angle, -stop_angle)
    inner = _arc_points(cx, cy, max(0, rx - width), max(0, ry - width), -stop_angle, -start_angle)
    return pygame.draw.polygon(surface, color, outer + inner)


def alpha_layer(surface, rect):
    """A clear see-through layer covering just the on-screen part of `rect` —
    one the full size of a zoomed-in glow would be huge. Returns (layer, area):
    draw on the layer with shapes moved by (-area.x, -area.y), then
    `surface.blit(layer, area)`. (None, None) when none of `rect` shows."""
    area = pygame.Rect(rect).clip(surface.get_clip())
    if area.width <= 0 or area.height <= 0:
        return None, None
    return pygame.Surface(area.size, pygame.SRCALPHA), area


def read_clipboard():
    """Read the system clipboard as text (Windows). Returns '' on any failure.
    Used for Ctrl+V paste, since SDL doesn't reliably deliver pasted text as
    input events here. restype/argtypes are set so 64-bit handles aren't
    truncated to 32 bits."""
    try:
        CF_UNICODETEXT = 13
        user32, kernel32 = ctypes.windll.user32, ctypes.windll.kernel32
        user32.GetClipboardData.restype = ctypes.c_void_p
        kernel32.GlobalLock.restype = ctypes.c_void_p
        kernel32.GlobalLock.argtypes = [ctypes.c_void_p]
        kernel32.GlobalUnlock.argtypes = [ctypes.c_void_p]
        if not user32.OpenClipboard(0):
            return ""
        try:
            handle = user32.GetClipboardData(CF_UNICODETEXT)
            if not handle:
                return ""
            ptr = kernel32.GlobalLock(handle)
            if not ptr:
                return ""
            try:
                return ctypes.c_wchar_p(ptr).value or ""
            finally:
                kernel32.GlobalUnlock(handle)
        finally:
            user32.CloseClipboard()
    except Exception:
        return ""


# ============================================================================
# CAMERA — the view transform (source of truth for all draw/hit-test geometry)
# ============================================================================
class Camera:
    """One zoom + pan applied to every object's world-space position/size at
    draw/hit-test time, so panning and zooming move and scale the whole scene
    together (distances included) rather than each shape scaling on its own."""

    MIN_ZOOM = 0.001
    MAX_ZOOM = 30.0

    def __init__(self):
        self.zoom = 1.0
        self.offset = pygame.Vector2(0, 0)  # world point mapped to screen (0, 0)
        self.ride = None                    # active CameraRide, or None

    def world_to_screen(self, world_pos):
        return ((world_pos[0] - self.offset.x) * self.zoom,
                (world_pos[1] - self.offset.y) * self.zoom)

    def screen_to_world(self, screen_pos):
        return (screen_pos[0] / self.zoom + self.offset.x,
                screen_pos[1] / self.zoom + self.offset.y)

    def pan(self, dx, dy):
        self.offset.x -= dx / self.zoom
        self.offset.y -= dy / self.zoom

    def zoom_at(self, screen_pos, factor):
        """Change zoom while keeping the world point under screen_pos fixed."""
        world_before = self.screen_to_world(screen_pos)
        self.zoom = max(self.MIN_ZOOM, min(self.MAX_ZOOM, self.zoom * factor))
        self.offset.x = world_before[0] - screen_pos[0] / self.zoom
        self.offset.y = world_before[1] - screen_pos[1] / self.zoom

    def world_rect_to_screen(self, world_center, world_width, world_height):
        w = max(1, round(world_width * self.zoom))
        h = max(1, round(world_height * self.zoom))
        rect = pygame.Rect(0, 0, w, h)
        rect.center = self.world_to_screen(world_center)
        return rect

    def look_at(self, world_pos, zoom, screen_size):
        """Jump straight to a given zoom, centered on world_pos."""
        self.zoom = max(self.MIN_ZOOM, min(self.MAX_ZOOM, zoom))
        self.offset.x = world_pos[0] - (screen_size[0] / 2) / self.zoom
        self.offset.y = world_pos[1] - (screen_size[1] / 2) / self.zoom

    # --- Rides: smoothly follow a path at a set speed, keeping current zoom ---
    def start_ride(self, points, speed):
        self.ride = CameraRide(points, speed)

    def cancel_ride(self):
        self.ride = None

    def update_ride(self, dt, screen_size):
        """Advance an active ride by dt and recenter on it; clears when done."""
        if self.ride is None:
            return
        pos, done = self.ride.advance(dt)
        self.look_at(pos, self.zoom, screen_size)   # keep whatever zoom we're at
        if done:
            self.ride = None


class CameraRide:
    """Walks a polyline (world points) at a constant world-units/sec speed,
    yielding the current position each frame until it reaches the end."""

    def __init__(self, points, speed):
        self.points = [pygame.Vector2(p) for p in points] or [pygame.Vector2(0, 0)]
        self.speed = max(1.0, speed)
        self.dist = 0.0
        self.seglens = [(self.points[i + 1] - self.points[i]).length()
                        for i in range(len(self.points) - 1)]
        self.total = sum(self.seglens)

    def advance(self, dt):
        """Return (world_pos, done)."""
        self.dist += self.speed * dt
        if self.total <= 0 or self.dist >= self.total:
            return self.points[-1], True
        d = self.dist
        for i, seg in enumerate(self.seglens):
            if d <= seg:
                return self.points[i].lerp(self.points[i + 1], d / seg if seg else 0), False
            d -= seg
        return self.points[-1], True


# ============================================================================
# OBJECT PLUMBING — properties, inspector rows, and panel buttons
# ============================================================================
# The pieces an object type uses to describe itself, so the game loop and the
# inspector never need to know what type it is:
#   Prop            a saved, inspector-editable field (listed in a class's PROPS)
#   Section / Field / Row / Stepper / Gap / ScrollList / PortRow / GiveRow
#                   what the inspector shows (built by obj.inspector(app))
#   Take / Give     an object's inputs and outputs, and the links between them
#   Btn, @panel_button
#                   the buttons at the bottom of the inspector (obj.actions(app))
# Every callback (on_click=…, a Btn, a @panel_button method) is called with the
# App (see the APP section at the bottom): scene, camera, ask(), save(), …

OBJECT_TYPES = {}   # {"type" string: class} — filled in as each class is defined
WARN_COLOR = (240, 150, 90)   # orange text for errors / limits in the inspector


def parse_color(text):
    """'240, 130, 30' or '#f0821e' -> (r, g, b). Raises ValueError."""
    text = text.strip()
    if re.fullmatch(r"#[0-9a-fA-F]{6}", text):
        return tuple(int(text[i:i + 2], 16) for i in (1, 3, 5))
    parts = [p for p in re.split(r"[\s,]+", text.strip("()[] ")) if p]
    if len(parts) != 3:
        raise ValueError("expected r, g, b")
    return tuple(max(0, min(255, int(float(p)))) for p in parts)


class Prop:
    """One saved, editable field on an object type, declared in its PROPS:

        PROPS = [Prop("speed", "Speed", "float", 700, min=1)]

    The object then has `self.speed` (set to the default when created), it's
    saved to / loaded from Nodes.json under "speed", and the inspector shows it
    as a clickable row: a text box for numbers / text / colors, a toggle for a
    bool, a cycle for a choice. After an edit, obj.on_prop_changed(key) runs.

    kind: "float", "int", "str", "bool", "color" (r, g, b), "choice"
    (needs choices=[...]), or "data" (any JSON — a dict/list; use with
    hidden=True, it can't be typed in). min/max clamp numbers. editable=False shows it
    read-only; hidden=True saves it without showing it at all (e.g. a link's
    target id). unit is appended when displayed ("700 units/sec"). state=True
    lists it under the panel's State section (a switch's On, a slider's Value)
    instead of Settings."""

    KINDS = ("float", "int", "str", "bool", "color", "choice", "data")

    def __init__(self, key, label=None, kind="float", default=0.0, *, min=None,
                 max=None, choices=None, editable=True, unit="", prompt=None, hidden=False,
                 state=False):
        if kind not in self.KINDS:
            raise ValueError(f"Prop {key!r}: unknown kind {kind!r}")
        if kind == "choice" and not choices:
            raise ValueError(f"Prop {key!r}: a choice needs choices=[...]")
        self.key = key
        self.label = label or key.replace("_", " ").capitalize()
        self.kind = kind
        self.default = default
        self.min, self.max = min, max
        self.choices = list(choices or [])
        self.editable = editable
        self.unit = unit
        self.prompt = prompt or f"{self.label}:"
        self.hidden = hidden            # saved, but not shown in the inspector
        self.state = state              # shown under State (what it is now), not Settings

    def parse(self, text):
        """Text typed into the edit box -> a value. Raises ValueError."""
        text = text.strip()
        if self.kind == "str":
            return text
        if self.kind == "color":
            return parse_color(text)
        if self.kind == "bool":
            if text.lower() in ("1", "true", "yes", "on"):
                return True
            if text.lower() in ("0", "false", "no", "off"):
                return False
            raise ValueError("expected on/off")
        if self.kind == "data":
            raise ValueError("data props aren't typed in")
        if self.kind == "choice":
            if text not in self.choices:
                raise ValueError("not one of the choices")
            return text
        value = float(text)
        if not math.isfinite(value):
            raise ValueError("must be a finite number")
        if self.min is not None and value < self.min:
            value = self.min
        if self.max is not None and value > self.max:
            value = self.max
        return int(round(value)) if self.kind == "int" else value

    def format(self, value, unit=True):
        if self.kind == "bool":
            text = "on" if value else "off"
        elif self.kind == "color":
            text = ", ".join(str(int(c)) for c in value)
        elif self.kind == "float":
            text = f"{value:g}"
        else:
            text = str(value)
        return text + self.unit if unit else text

    def to_json(self, value):
        if self.kind == "data":
            return copy.deepcopy(value)      # a copy (C key) must not share the original's dicts/lists
        return list(value) if self.kind == "color" else value

    def from_json(self, value):
        if self.kind == "color":
            return tuple(int(c) for c in value)
        if self.kind == "float":
            return float(value)
        if self.kind == "int":
            return int(value)
        if self.kind == "bool":
            return bool(value)
        return value

    def set(self, obj, value, app):
        setattr(obj, self.key, value)
        obj.on_prop_changed(self.key)
        app.save()

    def edit(self, obj, app):
        """What clicking this prop's inspector row does."""
        if not self.editable:
            return
        value = getattr(obj, self.key)
        if self.kind == "bool":
            self.set(obj, not value, app)
        elif self.kind == "choice":
            i = self.choices.index(value) if value in self.choices else -1
            self.set(obj, self.choices[(i + 1) % len(self.choices)], app)
        else:
            def submit(text):
                try:
                    new = self.parse(text)
                except ValueError:
                    return                  # not valid: leave it unchanged
                self.set(obj, new, app)
            app.ask(self.prompt, submit, value=self.format(value, unit=False))


# ---- Inspector rows: what obj.inspector(app) returns, drawn top to bottom ----
class Field:
    """Label with the value on the line below it (room for long values)."""

    def __init__(self, label, value, *, on_click=None, color=None):
        self.label, self.value, self.on_click, self.color = label, value, on_click, color


class Row:
    """One compact line: label on the left, value on the right. sub=True draws
    it indented, as a detail of the row above."""

    def __init__(self, label, value, *, on_click=None, color=None, sub=False):
        self.label, self.value, self.on_click, self.color = label, value, on_click, color
        self.sub = sub


class Stepper:
    """Label, then a [<] value [>] control on the right."""

    def __init__(self, label, value, on_down, on_up, *, down_enabled=True, up_enabled=True):
        self.label, self.value = label, value
        self.on_down, self.on_up = on_down, on_up
        self.down_enabled, self.up_enabled = down_enabled, up_enabled


class Gap:
    """Vertical space."""

    def __init__(self, px=8):
        self.px = px


class ScrollList:
    """A titled, wheel-scrollable list (up to SCROLL_LIST_ROWS rows tall).
    items: [(name, value, on_click or None), ...]. `key` identifies whose list
    it is; showing a different key resets the scroll."""

    def __init__(self, title, items, *, empty="—", key=None):
        self.title, self.items, self.empty, self.key = title, items, empty, key


class Section:
    """A titled group of rows in the panel. Clicking the title folds it (the
    panel remembers that per object type). collapsed = folded by default."""

    def __init__(self, title, rows, *, collapsed=False):
        self.title, self.rows, self.collapsed = title, rows, collapsed


class PortRow:
    """One input (a Take) of obj. The panel draws it from the take: its name,
    then either "link" (and the value set by hand, if any) or the source ·
    give it follows with an x to unlink, and the live value under that."""

    def __init__(self, obj, take):
        self.obj, self.take = obj, take


class GiveRow:
    """One output (a Give) of obj: its name and live value. Clicking it links
    it to an input of another object."""

    def __init__(self, obj, give, value):
        self.obj, self.give, self.value = obj, give, value


class Btn:
    """A button at the bottom of the inspector. style "go" = green, "stop" = red."""

    def __init__(self, label, on_click, *, style="normal", enabled=True):
        self.label, self.on_click, self.style, self.enabled = label, on_click, style, enabled


def panel_button(label):
    """Decorator: show this method as an inspector button.

        @panel_button("Switch on / off")
        def toggle(self, app): ...
    """
    def mark(fn):
        fn.panel_button = label
        return fn
    return mark


# ---- Inputs and outputs: how values flow between objects ---------------------
# An object *gives* named values (outputs(), declared by gives()) and *takes*
# them on named inputs (takes()). A link — saved in obj.links as
# {port: {"id": source id, "key": which give}} — makes one input follow one
# output. An output can feed any number of inputs; an input has one source.
# On/off is just 0 or 1 (anything that isn't 0 counts as on).
class Take:
    """One input of an object: something it can follow from another object.

    kind: "number" (follows the value), "onoff" (on while the source isn't 0),
    or "press" (acts once each time the source goes 0 → 1: the object's
    on_press(port) runs). verb is what the panel says it does ("follows",
    "records"; default by kind). current() is what it uses while unlinked (a
    device param's setting — a number, or text like "always"),
    set_by_hand(app) lets you type one, and on_label(app) runs when its name is
    clicked (e.g. a collector column's menu)."""

    KINDS = ("number", "onoff", "press")

    def __init__(self, key, label=None, kind="number", *, unit="", verb=None,
                 current=None, set_by_hand=None, on_label=None):
        if kind not in self.KINDS:
            raise ValueError(f"Take {key!r}: unknown kind {kind!r}")
        self.key = key
        self.label = label or key
        self.kind = kind
        self.unit = unit
        self.verb = verb
        self.current = current
        self.set_by_hand = set_by_hand
        self.on_label = on_label


class Give:
    """One output of an object: a named live value (a number, or None while it
    has none). The value itself comes from obj.outputs()[key]."""

    def __init__(self, key, label=None, unit=""):
        self.key = key
        self.label = label or key
        self.unit = unit


LINK_COLOR = (120, 220, 150)        # number wires (flowing dots)
LINK_ONOFF_COLOR = (240, 200, 90)   # on/off and press wires
PRESS_FLASH = 0.35                  # seconds a press wire stays lit after it fires


def format_value(value, unit=""):
    """A live value for the panel: 6 significant digits + unit, or "—"."""
    if value is None:
        return "—"
    return f"{value:.6g}" + (f" {unit}" if unit else "")


def output_point(obj):
    """World point a wire from obj's outputs starts at: its output_point() if
    it has one (a Function's output nub), else its center."""
    point = getattr(obj, "output_point", None)
    return point() if point is not None else obj.center


def draw_wire(surface, camera, start, end, color, *, flow=True, width=2):
    """A link between two world points: a thin line, with dots flowing from
    start toward end (the direction the value travels) when flow is on."""
    a = pygame.Vector2(camera.world_to_screen(start))
    b = pygame.Vector2(camera.world_to_screen(end))
    if (b - a).length() < 2:
        return
    pygame.draw.line(surface, tuple(int(c * 0.45) for c in color), a, b, width)
    if not flow:
        return
    t = pygame.time.get_ticks() / 1000.0
    dots = max(2, min(12, int((b - a).length() / 60)))
    for k in range(dots):
        f = (t * 0.6 + k / dots) % 1.0
        pygame.draw.circle(surface, color, a.lerp(b, f), 3)


def short_name(obj):
    """What a link row calls an object: its own name if it has one, else its type."""
    return obj.panel_name() or obj.type_name()


# ============================================================================
# CANVAS OBJECTS — the thin master class + the object families
# ============================================================================
# The master imposes only the shared "socket" (id, TYPE, draw/hit/update,
# to_dict/from_dict) and supplies working defaults for everything else — menu
# entry, PROPS, inspector, buttons, copy — so a subclass overrides only what's
# different about it. Defining a class with a TYPE registers it.
#
#   CanvasObject (thin master)
#   ├── Node        → point family (world_center, select, move, copy)
#   │   ├── Button / NameTag   (launchers: command + Activate)
#   │   └── Thing   → the easy base for simple drawn objects (things.py)
#   │       └── Agent → Neuron
#   ├── Train       → connector between two nodes
#   └── Wall        → rectangular zone


class CanvasObject:
    """The contract every drawable object exposes to the canvas: an id, a type
    string, a draw order, and the ability to draw itself, be hit-tested, and
    serialize — plus overridable hooks (create, inspector, buttons, …) with
    sensible defaults. Subclasses add whatever else they need."""

    TYPE = None        # each concrete class sets this; keys the type registry + the saved JSON
    MENU_LABEL = None  # Add-menu entry ("Add lamp"); None = not offered in the menu / E menu
    NAME = None        # name under its icon in the E menu (default: MENU_LABEL minus "Add ")
    CATEGORY = None    # E-menu tab it also appears under (every type is in "All")
    PROPS = [Prop("links", kind="data", default={}, hidden=True)]   # input -> {"id", "key"}
                       # saved, inspector-editable fields (see Prop); inherited + extended
    TAKES = ()         # its inputs (see Take); override takes() when they change
    COPY_SKIP = ()     # saved keys a copy should NOT carry over (e.g. a neuron's weights)
    draw_layer = 0     # paint order (higher = on top)
    selectable = False
    movable = False    # can arrow keys move it? (see move())
    copyable = False   # can Edit ▸ Copy / the C key duplicate it?
    SINGLETON = False  # only one allowed on the canvas (its create() should find the existing one)
    scene = None       # back-ref set by Scene, so connectors can resolve nodes
    is_selected = False

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        if cls.__dict__.get("TYPE"):      # concrete type: register it
            if cls.TYPE in OBJECT_TYPES:
                raise ValueError(f"two object types named {cls.TYPE!r}")
            OBJECT_TYPES[cls.TYPE] = cls

    def __init__(self, id=None):
        self.id = id or new_id()
        for p in self.props():
            setattr(self, p.key, copy.deepcopy(p.default))   # own copy: never share a dict/list default
        self._press_last = {}       # press input -> on last frame (missing = not read yet: no press)
        self._press_flash = {}      # press input -> seconds its wire stays lit

    @classmethod
    def props(cls):
        """Every Prop on this class, base classes' first. A subclass can list a
        Prop with the same key to change its label/default/etc."""
        found = {}
        for klass in reversed(cls.__mro__):
            for p in klass.__dict__.get("PROPS", ()):
                found[p.key] = p
        return list(found.values())

    @classmethod
    def prop(cls, key):
        return next(p for p in cls.props() if p.key == key)

    @classmethod
    def create(cls, app):
        """What picking this type (E menu / Add menu) does. Default: pick one
        up to place with the mouse (fits any class whose constructor takes x, y)."""
        app.start_placing(lambda x, y: cls(x, y))

    @classmethod
    def type_name(cls):
        """The name shown under this type's icon in the E menu."""
        if cls.NAME:
            return cls.NAME
        label = cls.MENU_LABEL or cls.TYPE or "?"
        return label[4:].capitalize() if label.startswith("Add ") else label

    @classmethod
    def icon_sample(cls):
        """The example object draw_icon draws. Override to give it, say, a label."""
        return cls(0, 0)

    @classmethod
    def draw_icon(cls, surface):
        """Draw this type's picture for the E menu, filling `surface`. Default:
        draw icon_sample() scaled to fit. Override for a hand-drawn icon."""
        sample = cls.icon_sample()
        w = getattr(sample, "world_width", 60)
        h = getattr(sample, "world_height", 60)
        size = surface.get_size()
        camera = Camera()
        camera.look_at((0, 0), min(size[0] / w, size[1] / h) * 0.8, size)
        sample.draw(surface, camera)

    @property
    def display_name(self):
        return self.TYPE or "object"

    @property
    def center(self):
        """World point that Go To / double-click / copy-at-cursor use, or None."""
        return None

    def in_box(self, x, y, w, h):
        """Does a drag-box over world rect (x, y, w, h) select this?"""
        return False

    def move(self, dx, dy):
        """Shift by a world-space delta (only called when movable)."""
        pass

    def on_prop_changed(self, key):
        """Runs after a Prop is edited in the inspector."""
        pass

    def on_removed(self):
        """Runs after this object is deleted from the scene."""
        pass

    def update(self, mouse_pos, mouse_pressed, camera, dt):
        pass

    def hit(self, pos, camera):
        return False

    def on_click(self, pos, app):
        """Object-specific click behavior. Return True if the click was handled
        (so the loop skips normal selection). Default: not handled. A handled
        click also captures the mouse until the button comes up: every motion
        goes to on_drag, and the release to on_release."""
        return False

    def on_drag(self, pos, app):
        """The mouse moved (to screen pos) while held down after a handled click."""
        pass

    def on_release(self, pos, app):
        """The button came up after a handled click."""
        pass

    def on_wheel(self, pos, steps, app):
        """The mouse wheel turned over this object (steps > 0 = away from you).
        Return True to use it (e.g. scroll a list) instead of zooming. Default: zoom."""
        return False

    def draw(self, surface, camera):
        raise NotImplementedError

    def screen_bounds(self, camera):
        """Roughly where this object is on screen (a Rect), so Scene.draw can
        skip it when it's off-screen. None = always draw it. Glows, halos and
        selection rings may spill past it — Scene.draw allows a wide margin."""
        return None

    def draw_links(self, surface, camera):
        """Draw the wires into this object's inputs — called for every object
        before any object is drawn, so wires sit underneath. Each wire runs
        from the source's output_point() to this object's input_point(port);
        number wires flow green, on/off wires are amber (lit while on), press
        wires flash when they fire."""
        if self.scene is None or not self.links:
            return
        for take in self.takes():
            src = self.take_source(take.key)
            if src is None or src.center is None:
                continue
            start, end = output_point(src), self.input_point(take.key)
            if take.kind == "number":
                draw_wire(surface, camera, start, end, LINK_COLOR)
            elif take.kind == "onoff":
                on = self.take_on(take.key)
                draw_wire(surface, camera, start, end, LINK_ONOFF_COLOR, flow=bool(on))
            else:
                lit = self._press_flash.get(take.key, 0) > 0
                draw_wire(surface, camera, start, end, LINK_ONOFF_COLOR, flow=lit, width=3 if lit else 2)

    def input_point(self, port):
        """World point where the wire into input `port` ends. Default: the center."""
        return self.center

    def take_at(self, pos, camera):
        """The input whose chip is under screen pos (a wire dropped there links
        to it), or None. Default: none."""
        return None

    def give_at(self, pos, camera):
        """The output whose nub is under screen pos (dragging from it lays a
        wire), or None. Default: the first output, if it has an output_point()
        nub and pos is on it."""
        point = getattr(self, "output_point", None)
        gives = self.gives() if point is not None else []
        if not gives:
            return None
        nub = pygame.Vector2(camera.world_to_screen(point()))
        reach = max(8, 8 * getattr(self, "scale", 1.0) * camera.zoom)
        return gives[0].key if nub.distance_to(pos) <= reach else None

    # --- Outputs (gives) ------------------------------------------------------------
    def outputs(self):
        """Live values other objects can read from this one: {name: number or
        None}. Every output it has is listed, with None while it has no value."""
        return {}

    def gives(self):
        """Its outputs, declared (name, unit) — so the panel and the link picker
        can list them even while a value is None. Default: one per outputs() key."""
        return [Give(key) for key in self.outputs()]

    # --- Inputs (takes) and links ------------------------------------------------------
    def takes(self):
        """Its inputs (Take), top to bottom. Default: TAKES."""
        return list(self.TAKES)

    def find_take(self, port):
        return next((t for t in self.takes() if t.key == port), None)

    def take_source(self, port):
        """The object input `port` follows, or None (unlinked, or it was deleted)."""
        link = self.links.get(port)
        if not link or not link.get("id") or self.scene is None:
            return None
        return self.scene.by_id.get(link["id"])

    def take(self, port):
        """The value input `port` follows right now, or None (unlinked, source
        deleted, or the source has no value)."""
        src = self.take_source(port)
        return src.outputs().get(self.links[port].get("key")) if src is not None else None

    def take_on(self, port):
        """An on/off input: True / False, or None while it has no value."""
        value = self.take(port)
        return None if value is None else value != 0

    def link(self, port, src, key, app=None):
        """Make input `port` follow src's output `key`."""
        self.links = dict(self.links, **{port: {"id": src.id, "key": key}})
        self._press_last.pop(port, None)
        self.on_links_changed()
        self._save(app)

    def unlink(self, port, app=None):
        if port in self.links:
            self.links = {k: v for k, v in self.links.items() if k != port}
            self._press_last.pop(port, None)
            self.on_links_changed()
            self._save(app)

    def unlink_all(self, app=None):
        self.links = {}
        self._press_last = {}
        self.on_links_changed()
        self._save(app)

    def on_links_changed(self):
        """Runs after an input is linked or unlinked."""
        pass

    def pick(self, app, port):
        """Start linking input `port`: click a source, then (if it has several)
        choose which output. Override to pass extras to app.pick_link."""
        app.pick_link(self, port)

    def on_press(self, port):
        """A "press" input just went 0 → 1."""
        pass

    def watch_presses(self, dt):
        """Called every frame by the scene: fires on_press for press inputs. A
        source that's already on when first read doesn't count — only 0 → 1."""
        for port in list(self._press_flash):
            self._press_flash[port] -= dt
            if self._press_flash[port] <= 0:
                del self._press_flash[port]
        if type(self).on_press is CanvasObject.on_press or not self.links:
            return
        for take in self.takes():
            if take.kind != "press":
                continue
            on = self.take_on(take.key)
            if on is None:
                self._press_last.pop(take.key, None)
                continue
            if on and self._press_last.get(take.key) is False:
                self._press_flash[take.key] = PRESS_FLASH
                self.on_press(take.key)
            self._press_last[take.key] = on

    def _save(self, app):
        if app is not None:
            app.save()
        elif self.scene is not None:
            self.scene.save()

    # --- Inspector -------------------------------------------------------------
    # The panel is the same for every type, in this order: a header (type, name,
    # status), then State, Inputs / takes, Outputs / gives, Settings, Info, and
    # the footer buttons. A type fills in the pieces below; the inputs, outputs
    # and settings come from takes(), gives() and PROPS on their own.
    def panel_name(self):
        """The name in the panel's header, or None for an unnamed object."""
        name = getattr(self, "title", None) or getattr(self, "text", None)
        return name or None

    @property
    def can_rename(self):
        return getattr(self, "text", None) is not None

    def rename(self, app):
        def submit(text):
            self.text = text.strip()
            app.save()
        app.ask("Name:", submit, value=getattr(self, "text", "") or "")

    def panel_status(self, app):
        """(text, color) for the header's status line, or None."""
        return None

    def state_rows(self, app):
        """The State section: what it is / shows right now, and the main thing
        you edit. (Props marked state=True are added after these.)"""
        return []

    def input_rows(self, app):
        """Extra rows at the end of Inputs / takes (e.g. "+ add column")."""
        return []

    def more_sections(self, app):
        """Extra Sections, shown before Settings (e.g. a neuron's W's)."""
        return []

    def info_rows(self, app):
        """The Info section (folded by default): ID, position, and type extras."""
        rows = [Row("ID", self.id)]
        c = self.center
        if c is not None:
            rows.append(Row("Position", f"{c[0]:.0f}, {c[1]:.0f}"))
        return rows

    def inspector(self, app):
        """The panel's sections, top to bottom (the header and footer are drawn
        around them). Types fill in state_rows / info_rows / … rather than
        overriding this."""
        out = []
        state = self.state_rows(app) + self.prop_rows(state=True)
        if state:
            out.append(Section("State", state))
        inputs = [PortRow(self, t) for t in self.takes()] + self.input_rows(app)
        if inputs:
            out.append(Section("Inputs / takes", inputs))
        gives = self.gives()
        if gives:
            values = self.outputs()
            out.append(Section("Outputs / gives", [GiveRow(self, g, values.get(g.key)) for g in gives]))
        out += self.more_sections(app)
        settings = self.prop_rows()
        if settings:
            out.append(Section("Settings", settings))
        out.append(Section("Info", self.info_rows(app), collapsed=True))
        return out

    def prop_rows(self, state=False):
        """One clickable Row per visible Prop (the Settings section; state=True:
        the ones listed under State)."""
        return [Row(p.label, p.format(getattr(self, p.key)),
                    on_click=(lambda app, p=p: p.edit(self, app)) if p.editable else None)
                for p in self.props() if not p.hidden and p.state == state]

    def actions(self, app):
        """The footer buttons: doing the object's job. Main action first (green),
        connection last; past 4 the rest go in the ⋯ menu. Default: every method
        marked @panel_button, in the order they're defined."""
        found = {}
        for klass in reversed(type(self).__mro__):
            for name, fn in vars(klass).items():
                if hasattr(fn, "panel_button"):
                    found[name] = fn.panel_button
        return [Btn(label, getattr(self, name)) for name, label in found.items()]

    def menu_items(self, app):
        """This type's own entries for the ⋯ / right-click menu: [(label,
        callback(), style)]. Destructive ones use style "danger"."""
        return []

    # --- Saving ------------------------------------------------------------------
    def to_dict(self):
        """Base: type, id and every Prop (links only when there are some).
        Subclasses add their own keys."""
        d = {"type": self.TYPE, "id": self.id}
        for p in self.props():
            if p.key == "links" and not self.links:
                continue
            d[p.key] = p.to_json(getattr(self, p.key))
        return d

    @classmethod
    def migrate(cls, d):
        """Bring older saved data up to date before loading (e.g. links stored
        the old way). Returns the dict to load. Default: unchanged."""
        return d

    @classmethod
    def from_dict(cls, d):
        """Build from saved data. Props are filled in after, by load_props."""
        raise NotImplementedError

    def load_props(self, d):
        for p in self.props():
            if p.key in d:
                try:
                    setattr(self, p.key, p.from_json(d[p.key]))
                except (TypeError, ValueError):
                    pass                    # bad saved value: keep the default


class UnknownObject(CanvasObject):
    """Stand-in for a saved object whose type no longer exists (its class was
    renamed or removed). Invisible and inert, but written back unchanged on
    save, so its data isn't lost — restore the class and it comes back."""

    def __init__(self, data):
        super().__init__(id=data.get("id"))
        self.data = data

    def draw(self, surface, camera):
        pass

    def to_dict(self):
        return dict(self.data, id=self.id)


def draw_box(surface, rect, text, font, *, base_color, hover_color, click_color,
             border_color, text_color, is_selected, is_hovered, is_clicked):
    """Shared boxed-face rendering for Button/NameTag: selection ring → border →
    state-colored face → centered text. The one place that layering lives."""
    if is_selected:
        draw_rect(surface, SELECTION_COLOR, rect.inflate(14, 14),
                  width=3, border_radius=10)

    draw_rect(surface, border_color, rect.inflate(6, 6), border_radius=8)

    if is_clicked:
        face_color, inner_rect = click_color, rect.inflate(-4, -4)
    elif is_hovered:
        face_color, inner_rect = hover_color, rect
    else:
        face_color, inner_rect = base_color, rect
    draw_rect(surface, face_color, inner_rect, border_radius=6)

    text_surf = font.render(text, True, text_color)
    surface.blit(text_surf, text_surf.get_rect(center=inner_rect.center))


class Node(CanvasObject):
    """Point-anchored family: has a single world_center, is selectable, movable
    and copyable, and carries an optional action + params describing what it
    does. LAUNCHER types (Button, NameTag) show the command + Activate."""

    selectable = True
    movable = True
    copyable = True
    LAUNCHER = False

    def __init__(self, x, y, text="", *, id=None, action=None, params=None,
                 description=""):
        super().__init__(id=id)
        self.world_center = pygame.Vector2(x, y)  # x, y are the CENTER
        self._text = text
        self.action = action
        self.params = dict(params or {})   # own copy, so a copy never shares it
        self.description = description
        self.is_hovered = self.is_clicked = self.is_selected = False

    @property
    def text(self):
        return self._text

    @text.setter
    def text(self, value):
        self._text = value

    @property
    def display_name(self):
        return self.text or self.TYPE or "node"

    @property
    def center(self):
        return self.world_center

    def in_box(self, x, y, w, h):
        c = self.world_center
        return x <= c.x <= x + w and y <= c.y <= y + h

    def state_rows(self, app):
        if not self.LAUNCHER:
            return []
        rows = [Field("Command", self.params.get("command") or "— click to set one",
                      on_click=self.edit_command)]
        if self.description:
            rows.append(Field("Description", self.description))
        return rows

    def actions(self, app):
        out = super().actions(app)
        if self.LAUNCHER:
            out.insert(0, Btn("Activate", lambda app: run_action(self, app), style="go",
                              enabled=bool(self.action)))
        return out

    def edit_command(self, app):
        def submit(text):
            command = text.strip()
            if command:
                self.action = "run_command"
                self.params = dict(self.params, command=command)
            else:                       # cleared: node no longer launches anything
                self.action = None
                self.params = {}
            app.save()
        app.ask("Command to run:", submit, value=self.params.get("command", ""))

    def screen_rect(self, camera):
        raise NotImplementedError

    def screen_bounds(self, camera):
        return self.screen_rect(camera)

    def update(self, mouse_pos, mouse_pressed, camera, dt):
        rect = self.screen_rect(camera)
        self.is_hovered = rect.collidepoint(mouse_pos)
        self.is_clicked = self.is_hovered and mouse_pressed

    def hit(self, pos, camera):
        return self.screen_rect(camera).collidepoint(pos)

    def move(self, dx, dy):
        self.world_center.x += dx
        self.world_center.y += dy

    def to_dict(self):
        d = super().to_dict()
        d.update(name=self.text, x=self.world_center.x, y=self.world_center.y)
        if self.action:
            d["action"] = self.action
        if self.params:
            d["params"] = self.params
        if self.description:
            d["description"] = self.description
        return d

    @classmethod
    def from_dict(cls, d):
        return cls(d["x"], d["y"], d.get("name", ""), id=d.get("id"),
                   action=d.get("action"), params=d.get("params"),
                   description=d.get("description", ""))


class Button(Node):
    """A clickable node whose box + label scale with the camera. Auto-widens to
    fit its text, and draws as a plain dot once zoomed small enough (LOD)."""

    TYPE = "button"
    MENU_LABEL = "Add node"
    LAUNCHER = True
    draw_layer = 0

    @classmethod
    def create(cls, app):
        def named(text):
            name = text.strip() or "New Button"
            app.start_placing(lambda x, y: cls(x, y, name))
        app.ask("New button text:", named)

    @classmethod
    def icon_sample(cls):
        return cls(0, 0, "Button")

    def __init__(self, x, y, text="", width=NODE_WIDTH, height=NODE_HEIGHT, **kwargs):
        self.world_width = width
        self.world_height = height
        super().__init__(x, y, text, **kwargs)
        self._fit_width_to_text()   # runs after _text is set

    @Node.text.setter
    def text(self, value):
        self._text = value
        self._fit_width_to_text()

    def _fit_width_to_text(self):
        # Label renders at BASE_FONT_SIZE*zoom and world units == px at zoom 1,
        # so measuring at BASE_FONT_SIZE gives the label's world-unit width.
        text_width = get_font(BUTTON_BASE_FONT_SIZE).size(self._text)[0]
        min_width = text_width + BUTTON_TEXT_PADDING
        if getattr(self, "world_width", 0) < min_width:
            self.world_width = min_width   # only ever grows

    def screen_rect(self, camera):
        return camera.world_rect_to_screen(self.world_center,
                                           self.world_width, self.world_height)

    def draw(self, surface, camera):
        rect = self.screen_rect(camera)
        if camera.zoom <= BUTTON_MIN_SCALE:
            # LOD: too small to read — just a dot, which can shrink to 1px.
            draw_rect(surface, BUTTON_BORDER, rect)
            return
        font = get_font(round(BUTTON_BASE_FONT_SIZE * camera.zoom))
        draw_box(surface, rect, self.text, font,
                 base_color=BUTTON_BASE, hover_color=BUTTON_HOVER,
                 click_color=BUTTON_CLICK, border_color=BUTTON_BORDER,
                 text_color=BUTTON_TEXT, is_selected=self.is_selected,
                 is_hovered=self.is_hovered, is_clicked=self.is_clicked)


class NameTag(Node):
    """A label whose box + text stay a fixed SCREEN size at any zoom, so it keeps
    marking a location even after nearby nodes have shrunk to a pixel. Drawn on
    top of everything else via a higher draw_layer."""

    TYPE = "nametag"
    MENU_LABEL = "Add name tag"
    LAUNCHER = True
    draw_layer = 1

    @classmethod
    def create(cls, app):
        def named(text):
            name = text.strip() or "Label"
            app.start_placing(lambda x, y: cls(x, y, name))
        app.ask("New name tag text:", named)

    @classmethod
    def icon_sample(cls):
        return cls(0, 0, "Tag")

    def screen_rect(self, camera):
        text_surf = get_font(NAMETAG_FONT_SIZE).render(self.text, True, NAMETAG_TEXT)
        rect = text_surf.get_rect().inflate(NAMETAG_PADDING)
        rect.center = camera.world_to_screen(self.world_center)
        return rect

    def draw(self, surface, camera):
        rect = self.screen_rect(camera)
        # Border matches the text color, so the two stay in sync by construction.
        draw_box(surface, rect, self.text, get_font(NAMETAG_FONT_SIZE),
                 base_color=NAMETAG_BASE, hover_color=NAMETAG_HOVER,
                 click_color=NAMETAG_CLICK, border_color=NAMETAG_TEXT,
                 text_color=NAMETAG_TEXT, is_selected=self.is_selected,
                 is_hovered=self.is_hovered, is_clicked=self.is_clicked)


# ---- Train: a connector between two nodes (relationship-anchored) -----------
TRAIN_COLOR = (245, 225, 20)      # yellow route
TRAIN_SELECT_COLOR = (130, 200, 255)   # halo when a train is selected (blue, off the yellow)
TRAIN_DEFAULT_SPEED = 700         # world units/sec the camera rides along the route
TRAIN_WORLD_WIDTH = 14            # line thickness in world units (scales w/ zoom)
TRAIN_ARROW_WORLD = 46            # arrow box size in world units (scales w/ zoom)
ARROW_FILL = (10, 10, 10)         # black arrow glyph
ARROW_MIN_PX = 6                  # don't draw/hit arrows smaller than this


def _node_halfsize(node):
    """Half width/height of a node in world units, for clipping the route to
    its edge. Buttons expose world_width/height; others get a small default."""
    return (getattr(node, "world_width", 40) / 2,
            getattr(node, "world_height", 40) / 2)


class Train(CanvasObject):
    """An orthogonal 'transport route' connecting two nodes by id, with a
    clickable arrow at each end that teleports the camera to the far node.
    Stores only the two node ids + the corner waypoints; the endpoints are read
    from the live nodes each frame (so the route follows them when they move).
    Draws under nodes and, being world-space, collapses to a ~1px line zoomed
    out. Not selectable; its arrows are its only interactive parts."""

    TYPE = "train"
    MENU_LABEL = "Add train"
    PROPS = [Prop("speed", "Speed", "float", TRAIN_DEFAULT_SPEED, min=1,
                  unit=" units/sec", prompt="Train speed (units/sec):")]
    draw_layer = -1
    selectable = True     # click its line to select it (then Edit ▸ Delete Node)

    @classmethod
    def create(cls, app):
        app.cancel_modes()
        app.scene.select_only(None)     # nothing selected while drawing a route
        app.train_builder.begin()

    @classmethod
    def draw_icon(cls, surface):
        w, h = surface.get_size()
        t = max(3, w // 12)
        pts = [(w * 0.18, h * 0.82), (w * 0.18, h * 0.32), (w * 0.72, h * 0.32)]
        for (x1, y1), (x2, y2) in zip(pts, pts[1:]):
            draw_rect(surface, TRAIN_COLOR,
                      (min(x1, x2) - t / 2, min(y1, y2) - t / 2, abs(x2 - x1) + t, abs(y2 - y1) + t))
        arrow = pygame.Rect(0, 0, w // 3, w // 3)
        arrow.center = (round(w * 0.72), round(h * 0.32))
        cls._draw_arrow(surface, arrow, pygame.Vector2(1, 0))

    def __init__(self, from_id, to_id, waypoints=None, *, id=None, speed=None):
        super().__init__(id=id)
        self.from_id = from_id
        self.to_id = to_id
        self.waypoints = [pygame.Vector2(p) for p in (waypoints or [])]
        if speed:
            self.speed = float(speed)

    def state_rows(self, app):
        def node_name(nid):
            n = self.scene.by_id.get(nid) if self.scene else None
            return n.display_name if n is not None else (nid or "—")
        return [Row("From", node_name(self.from_id)), Row("To", node_name(self.to_id)),
                Row("Corners", str(len(self.waypoints)))]

    def _endpoints(self):
        s = self.scene
        return ((s.by_id.get(self.from_id), s.by_id.get(self.to_id))
                if s else (None, None))

    def world_points(self):
        """[from-node center, *waypoints, to-node center], or [] if an endpoint
        is missing (or isn't a point object)."""
        a, b = self._endpoints()
        if getattr(a, "center", None) is None or getattr(b, "center", None) is None:
            return []
        return ([pygame.Vector2(a.world_center)]
                + [pygame.Vector2(w) for w in self.waypoints]
                + [pygame.Vector2(b.world_center)])

    def simplify_waypoints(self):
        """Re-simplify the route against the current endpoints, dropping any
        redundant/backtrack corners. Cleans up trains built before the simplify
        pass existed. Returns True if it changed anything."""
        pts = self.world_points()
        if len(pts) < 2:
            return False
        new = self._simplify(pts)[1:-1]
        if len(new) != len(self.waypoints):
            self.waypoints = new
            return True
        return False

    @staticmethod
    def _attach(center, toward, node):
        """Point on `node`'s edge along the axis-dominant direction to `toward`."""
        hw, hh = _node_halfsize(node)
        dx, dy = toward.x - center.x, toward.y - center.y
        if abs(dx) >= abs(dy):
            return pygame.Vector2(center.x + (hw if dx >= 0 else -hw), center.y)
        return pygame.Vector2(center.x, center.y + (hh if dy >= 0 else -hh))

    @staticmethod
    def _simplify(points, eps=0.5):
        """Drop any interior point collinear with its kept neighbours (all three
        sharing an x or a y). Removes redundant corners and the little overhang
        stub left when a drawn corner didn't line up with the end node."""
        if len(points) <= 2:
            return list(points)
        out = [points[0]]
        for i in range(1, len(points) - 1):
            prev, cur, nxt = out[-1], points[i], points[i + 1]
            same_x = abs(prev.x - cur.x) < eps and abs(cur.x - nxt.x) < eps
            same_y = abs(prev.y - cur.y) < eps and abs(cur.y - nxt.y) < eps
            if not (same_x or same_y):
                out.append(cur)
        out.append(points[-1])
        return out

    def _arrows(self, camera):
        """[(screen_rect, direction, target_node), ...] for the two end arrows."""
        pts = self.world_points()
        if len(pts) < 2:
            return []
        a, b = self._endpoints()
        size = max(ARROW_MIN_PX, round(TRAIN_ARROW_WORLD * camera.zoom))
        out = []
        for node, center_pt, toward_pt, target in ((a, pts[0], pts[1], b),
                                                    (b, pts[-1], pts[-2], a)):
            attach = self._attach(center_pt, toward_pt, node)
            # Outward = node center -> its edge. Always non-zero (unlike the
            # segment direction, which can vanish in degenerate cases), so the
            # arrow is reliably pushed OUT of the node and points along the route.
            outward = attach - pygame.Vector2(node.world_center)
            dxn = (outward.x > 0) - (outward.x < 0)
            dyn = (outward.y > 0) - (outward.y < 0)
            sp = camera.world_to_screen(attach)
            rect = pygame.Rect(0, 0, size, size)
            rect.center = (round(sp[0] + dxn * size / 2), round(sp[1] + dyn * size / 2))
            out.append((rect, pygame.Vector2(dxn, dyn), target))
        return out

    def _segment_rects(self, camera, pad=0):
        """Screen rects for each drawn segment (endpoints clipped to node edges),
        inflated by half the line width plus `pad`. Shared by draw and hit."""
        pts = self.world_points()
        if len(pts) < 2:
            return []
        a, b = self._endpoints()
        draw_pts = [self._attach(pts[0], pts[1], a)] + pts[1:-1] \
            + [self._attach(pts[-1], pts[-2], b)]
        scr = [camera.world_to_screen(p) for p in draw_pts]
        half = max(1, round(TRAIN_WORLD_WIDTH * camera.zoom)) / 2 + pad
        rects = []
        for (x1, y1), (x2, y2) in zip(scr, scr[1:]):
            rects.append(pygame.Rect(round(min(x1, x2) - half), round(min(y1, y2) - half),
                                     round(abs(x2 - x1) + 2 * half), round(abs(y2 - y1) + 2 * half)))
        return rects

    def screen_bounds(self, camera):
        pts = [camera.world_to_screen(p) for p in self.world_points()]
        if not pts:
            return None
        xs, ys = [p[0] for p in pts], [p[1] for p in pts]
        arrow = TRAIN_ARROW_WORLD * camera.zoom          # arrows stick out past the ends
        return pygame.Rect(min(xs) - arrow, min(ys) - arrow,
                           max(xs) - min(xs) + 2 * arrow, max(ys) - min(ys) + 2 * arrow)

    def hit(self, pos, camera):
        if any(rect.collidepoint(pos) for rect, _d, _t in self._arrows(camera)):
            return True
        return any(r.collidepoint(pos) for r in self._segment_rects(camera, pad=4))

    def on_click(self, pos, ctx):
        for rect, _d, target in self._arrows(ctx.camera):
            if rect.collidepoint(pos) and target is not None:
                # Ride the route toward the far node (reverse the path if the
                # clicked arrow is at the "to" end), keeping the current zoom.
                a, b = self._endpoints()
                pts = self.world_points()
                ride_pts = pts if target is b else list(reversed(pts))
                ctx.camera.start_ride(ride_pts, self.speed)
                return True
        return False

    def draw(self, surface, camera):
        # Each axis-aligned segment as a rect inflated by half the line width;
        # the overlaps at turns form perfectly square corners.
        if self.is_selected:
            for r in self._segment_rects(camera, pad=5):
                draw_rect(surface, TRAIN_SELECT_COLOR, r)
        for r in self._segment_rects(camera):
            draw_rect(surface, TRAIN_COLOR, r)
        for rect, direction, _t in self._arrows(camera):
            self._draw_arrow(surface, rect, direction)

    @staticmethod
    def _draw_arrow(surface, rect, direction):
        if rect.width < ARROW_MIN_PX:
            return
        draw_rect(surface, TRAIN_COLOR, rect)
        draw_rect(surface, ARROW_FILL, rect, width=max(1, rect.width // 12))

        cx, cy = rect.center
        size = rect.width
        half = size * 0.36     # tail/tip distance from center along the axis
        head_len = size * 0.30
        head_half = size * 0.20
        shaft_half = size * 0.09
        dxn = (direction.x > 0) - (direction.x < 0)
        dyn = (direction.y > 0) - (direction.y < 0)

        if dxn:   # horizontal: full arrow (shaft + head)
            tip = cx + dxn * half
            base = tip - dxn * head_len
            shaft = pygame.Rect(0, 0, abs(base - (cx - dxn * half)), shaft_half * 2)
            shaft.center = ((base + (cx - dxn * half)) / 2, cy)
            head = [(tip, cy), (base, cy - head_half), (base, cy + head_half)]
        else:     # vertical
            tip = cy + dyn * half
            base = tip - dyn * head_len
            shaft = pygame.Rect(0, 0, shaft_half * 2, abs(base - (cy - dyn * half)))
            shaft.center = (cx, (base + (cy - dyn * half)) / 2)
            head = [(cx, tip), (cx - head_half, base), (cx + head_half, base)]
        draw_rect(surface, ARROW_FILL, shaft)
        pygame.draw.polygon(surface, ARROW_FILL, head)

    def to_dict(self):
        d = super().to_dict()                    # type, id, speed
        d.update(from_id=self.from_id, to_id=self.to_id,
                 waypoints=[[w.x, w.y] for w in self.waypoints])
        return d

    @classmethod
    def from_dict(cls, d):
        return cls(d["from_id"], d["to_id"], d.get("waypoints", []),
                   id=d.get("id"), speed=d.get("speed"))


# ---- Wall: an outline zone drawn around an area (area-anchored) -------------
WALL_COLOR = (120, 180, 240)          # border + title color (blue)
WALL_SELECT_COLOR = (255, 210, 60)
WALL_WORLD_BORDER = 8                  # border thickness in world units (scales w/ zoom)
WALL_MIN_WORLD = 40                    # min drag size to create one
WALL_TITLE_FONT_SIZE = NAMETAG_FONT_SIZE   # fixed screen size — title never scales


class Wall(CanvasObject):
    """A rectangular 'wall'/zone drawn around a group of nodes. Outline only (no
    fill); the border scales with zoom like nodes, but the title is a fixed-size
    name tag pinned to the top-left corner (never scales, like a NameTag). Draws
    under everything; selectable on its border or title for move/rename/delete.
    Purely visual — it doesn't own or contain the nodes inside it."""

    TYPE = "wall"
    MENU_LABEL = "Add wall"
    PROPS = [Prop("color", "Color", "color", WALL_COLOR)]
    draw_layer = -2
    selectable = True
    movable = True
    copyable = True

    @classmethod
    def create(cls, app):
        app.cancel_modes()
        app.scene.select_only(None)
        app.wall_builder.begin()       # drag a box; the loop then asks for its name

    @classmethod
    def draw_icon(cls, surface):
        w, h = surface.get_size()
        box = pygame.Rect(w * 0.1, h * 0.28, w * 0.8, h * 0.6)
        draw_rect(surface, WALL_COLOR, box, width=max(2, w // 20), border_radius=6)
        tag = pygame.Rect(box.x, box.y - h * 0.18, w * 0.42, h * 0.18)
        draw_rect(surface, (45, 45, 55), tag, border_radius=3)
        draw_rect(surface, WALL_COLOR, tag, width=2, border_radius=3)

    def __init__(self, x, y, w, h, name="", *, id=None, color=None):
        super().__init__(id=id)
        self.x, self.y, self.w, self.h = x, y, w, h
        self._text = name
        if color:
            self.color = tuple(color)

    @property
    def center(self):
        return pygame.Vector2(self.x + self.w / 2, self.y + self.h / 2)

    def in_box(self, x, y, w, h):
        return x <= self.x and y <= self.y and self.x + self.w <= x + w and self.y + self.h <= y + h

    @property
    def text(self):
        return self._text

    @text.setter
    def text(self, value):
        self._text = value

    @property
    def display_name(self):
        return self._text or "wall"

    def move(self, dx, dy):
        self.x += dx
        self.y += dy

    def screen_rect(self, camera):
        tl = camera.world_to_screen((self.x, self.y))
        return pygame.Rect(round(tl[0]), round(tl[1]),
                           max(1, round(self.w * camera.zoom)),
                           max(1, round(self.h * camera.zoom)))

    def _title_rect(self, camera):
        """Fixed-size name-tag box sitting just above the top edge, left-aligned
        with the box (its bottom-left corner at the wall's top-left corner)."""
        if not self._text:
            return None
        tw, th = get_font(WALL_TITLE_FONT_SIZE).size(self._text)
        rect = pygame.Rect(0, 0, tw + NAMETAG_PADDING[0], th + NAMETAG_PADDING[1])
        rect.bottomleft = tuple(round(v) for v in camera.world_to_screen((self.x, self.y)))
        return rect

    def screen_bounds(self, camera):
        rect = self.screen_rect(camera)
        title = self._title_rect(camera)
        return rect.union(title) if title is not None else rect

    def hit(self, pos, camera):
        r = self.screen_rect(camera)
        band = max(3, round(WALL_WORLD_BORDER * camera.zoom)) + 4
        on_border = (r.inflate(band, band).collidepoint(pos)
                     and not r.inflate(-band, -band).collidepoint(pos))
        title = self._title_rect(camera)
        return on_border or (title is not None and title.collidepoint(pos))

    def draw(self, surface, camera):
        r = self.screen_rect(camera)
        border = max(1, round(WALL_WORLD_BORDER * camera.zoom))
        if self.is_selected:
            draw_rect(surface, WALL_SELECT_COLOR,
                      r.inflate(border * 2, border * 2),
                      width=max(1, border), border_radius=8)
        draw_rect(surface, self.color, r, width=border, border_radius=8)

        title = self._title_rect(camera)
        if title is not None:
            draw_rect(surface, (45, 45, 55), title, border_radius=6)
            draw_rect(surface, self.color, title, width=2, border_radius=6)
            ts = get_font(WALL_TITLE_FONT_SIZE).render(self._text, True, self.color)
            surface.blit(ts, ts.get_rect(center=title.center))

    def to_dict(self):
        d = super().to_dict()                    # type, id, color
        d.update(name=self._text, x=self.x, y=self.y, w=self.w, h=self.h)
        return d

    @classmethod
    def from_dict(cls, d):
        return cls(d["x"], d["y"], d["w"], d["h"], d.get("name", ""),
                   id=d.get("id"), color=d.get("color"))


# ---- Thing: the easy base for simple drawn objects -------------------------
THING_DOT_PX = 4                    # at/below this on-screen size, draw a plain dot (LOD)


class Thing(Node):
    """The quick way to make a new kind of object: a point object with a fixed
    world SIZE that you draw yourself. A subclass sets TYPE (+ MENU_LABEL to
    appear in the Add menu) and overrides draw_shape(); it can also list PROPS,
    mark methods @panel_button, and override tick(dt) to animate. Selection
    ring, hover, zoom-out dot, moving, copying, saving and the inspector all
    come from here. See things.py for examples."""

    SIZE = (60, 60)                 # world units (w, h) at scale 1
    DOT_COLOR = (200, 200, 210)     # what it draws as when zoomed far out
    scale = 1.0                     # size multiplier; make it editable + saved with
                                    # Prop("scale", "Size", "float", 1.0, min=…, unit="×")

    @property
    def world_width(self):
        return self.SIZE[0] * self.scale

    @property
    def world_height(self):
        return self.SIZE[1] * self.scale

    def screen_rect(self, camera):
        return camera.world_rect_to_screen(self.world_center, self.world_width, self.world_height)

    def update(self, mouse_pos, mouse_pressed, camera, dt):
        super().update(mouse_pos, mouse_pressed, camera, dt)   # hover/click
        self.tick(dt)

    def tick(self, dt):
        """Called every frame with the frame time in seconds. Animate here."""
        pass

    def draw(self, surface, camera):
        rect = self.screen_rect(camera)
        if self.is_selected:
            draw_rect(surface, SELECTION_COLOR, rect.inflate(12, 12),
                      width=3, border_radius=max(2, min(rect.size) // 8) + 4)
        if max(rect.size) <= THING_DOT_PX:
            draw_rect(surface, self.DOT_COLOR, rect)
            return
        self.draw_shape(surface, rect, camera)

    def draw_shape(self, surface, rect, camera):
        """Draw the object into `rect` (its on-screen box, already scaled by
        the zoom). Default: a plain rounded box."""
        draw_rect(surface, self.DOT_COLOR, rect, border_radius=max(2, rect.width // 8))


# ---- Agent: a little critter that wanders inside its wall ------------------
AGENT_W, AGENT_H = 74, 56           # world size of the body
AGENT_COLOR = (240, 130, 30)        # orange
AGENT_EYE = (12, 12, 12)
AGENT_SPEED = 70                    # world units/sec while walking
AGENT_PAUSE = (0.4, 1.8)           # random pause range (seconds)
AGENT_MARGIN = 24                  # keep this far inside the wall's border


class Agent(Thing):
    """A point object that idly wanders — walk to a random spot, pause, repeat —
    staying inside whatever Wall contains it (or a small area if it's in none).
    Just for looks right now; real behaviour can come later. While selected it
    holds still so you can grab it."""

    TYPE = "agent"
    MENU_LABEL = "Add agent"
    SIZE = (AGENT_W, AGENT_H)
    DOT_COLOR = AGENT_COLOR
    draw_layer = 0                 # on top of walls, under name tags

    def __init__(self, x, y, text="", **kwargs):
        super().__init__(x, y, text, **kwargs)
        self._home = pygame.Vector2(x, y)   # fixed anchor for wandering (moves with move())
        self._target = pygame.Vector2(self.world_center)
        self._state = "pause"
        self._pause = random.uniform(*AGENT_PAUSE)

    def move(self, dx, dy):
        super().move(dx, dy)                # shift the body
        self._home.x += dx                  # and its home, so it wanders where you drop it
        self._home.y += dy

    def _bounds(self):
        """The region to wander in: the containing wall (inset), or a small box
        around the current spot if not inside any wall."""
        s = self.scene
        cx, cy = self.world_center.x, self.world_center.y
        if s is not None:
            for o in s.objects:
                if isinstance(o, Wall) and o.x <= cx <= o.x + o.w and o.y <= cy <= o.y + o.h:
                    m = AGENT_MARGIN
                    return (o.x + m, o.y + m, o.x + o.w - m, o.y + o.h - m)
        # Not in a wall: wander around a FIXED home (not the current position, or
        # it random-walks and drifts off in one direction).
        hx, hy = self._home.x, self._home.y
        return (hx - 140, hy - 140, hx + 140, hy + 140)

    def _pick_target(self):
        minx, miny, maxx, maxy = self._bounds()
        self._target = pygame.Vector2(random.uniform(minx, max(minx, maxx)),
                                      random.uniform(miny, max(miny, maxy)))

    def tick(self, dt):
        if self.is_selected:
            return                                             # hold still when grabbed
        if self._state == "pause":
            self._pause -= dt
            if self._pause <= 0:
                self._pick_target()
                self._state = "move"
        else:
            to = self._target - self.world_center
            step = AGENT_SPEED * dt
            if to.length() <= max(step, 1.0):
                self.world_center.update(self._target)
                self._state, self._pause = "pause", random.uniform(*AGENT_PAUSE)
            else:
                self.world_center += to.normalize() * step

    def draw_shape(self, surface, rect, camera):
        draw_rect(surface, AGENT_COLOR, rect, border_radius=max(2, rect.width // 8))
        ew, eh = max(2, rect.width // 6), max(2, rect.height // 3)
        ey = rect.centery - rect.height // 14
        for ex in (rect.centerx - rect.width // 6, rect.centerx + rect.width // 6):
            eye = pygame.Rect(0, 0, ew, eh)
            eye.center = (ex, ey)
            pygame.draw.ellipse(surface, AGENT_EYE, eye)


# ---- Neuron: an Agent look-alike — white body, one black spot -------------
NEURON_COLOR = (245, 245, 245)      # white
NEURON_SPOT = (12, 12, 12)
NEURON_FIRE_COLOR = (255, 225, 40)  # yellow flash while firing
NEURON_FIRE_FRAMES = 1              # default spike duration: frames a fire stays lit


# Firing equations: a tiny, safe expression language evaluated every frame.
#   number result  -> a firing RATE (fires/sec), fired at steady intervals
#                     ("1" = once a second, "2t" = speeds up over time)
#   true/false     -> fire on any frame where it's true
# Names available: t (seconds on this neuron's clock), dt (frame length),
# every(s) (true once every s seconds), poisson(r) (random, r fires/sec on
# average), random(), and the usual math functions.
FIRE_MATH = {name: getattr(math, name) for name in
             ("sin", "cos", "tan", "exp", "log", "sqrt", "floor", "ceil", "pi", "e")}
FIRE_MATH.update(abs=abs, min=min, max=max)
FIRE_NAMES = set(FIRE_MATH) | {"t", "dt", "every", "poisson", "random"}
# Clock-trigger equations may also use W (= this neuron's Input W), e.g. "W > 3".
TRIGGER_NAMES = FIRE_NAMES | {"W", "w"}
FIRE_NODES = (ast.Expression, ast.BinOp, ast.UnaryOp, ast.BoolOp, ast.Compare,
              ast.IfExp, ast.Call, ast.Name, ast.Load, ast.Constant,
              ast.operator, ast.unaryop, ast.boolop, ast.cmpop)


def compile_firing_equation(text, names=FIRE_NAMES):
    """Equation text -> code object, or raise ValueError with a short reason.
    Accepts math-style shorthand: '2t' -> 2*t, '^' -> '**', and a leading
    'if' is ignored ("if W > 3" == "W > 3")."""
    src = re.sub(r"^\s*if\s+", "", text).strip().replace("^", "**")
    # implicit multiply ("2t", "3(t+1)"), but never before a keyword ("3 and t")
    src = re.sub(r"(\d|\))\s*(?=(?!(?:and|or|not|if|else)\b)[A-Za-z(])", r"\1*", src)
    try:
        tree = ast.parse(src, mode="eval")
    except SyntaxError:
        raise ValueError("can't read that equation")
    for node in ast.walk(tree):
        if not isinstance(node, FIRE_NODES):
            raise ValueError(f"'{type(node).__name__}' isn't allowed")
        if isinstance(node, ast.Name) and node.id not in names:
            raise ValueError(f"unknown name '{node.id}'")
        if isinstance(node, ast.Call) and not isinstance(node.func, ast.Name):
            raise ValueError("only plain function calls are allowed")
        if isinstance(node, ast.Constant) and not isinstance(node.value, (int, float)):
            raise ValueError("only numbers are allowed")
    return compile(tree, "<firing equation>", "eval")


class Neuron(Agent):
    """Built on Agent (selectable/movable/saveable) but stationary, drawn as a
    white square with a single black oval spot in the center. It can carry a
    firing equation (see compile_firing_equation) that makes it flash yellow.

    `weights` holds this neuron's outgoing W to every other neuron, keyed by
    the other neuron's id. Links are directed: A's W to B (on A) is separate
    from B's W to A (on B). Scene.sync_weights keeps every neuron's dict
    complete as neurons come and go."""

    TYPE = "neuron"
    MENU_LABEL = "Add neuron"
    DOT_COLOR = NEURON_COLOR
    COPY_SKIP = ("weights",)        # a copied neuron starts with fresh (0) W's

    def __init__(self, x, y, text="", *, id=None, equation="", trigger="",
                 spike_frames=NEURON_FIRE_FRAMES, weights=None, t_end=math.inf, **kwargs):
        super().__init__(x, y, text, id=id, **kwargs)
        self._fire_frames = 0
        self.spike_frames = max(1, int(spike_frames))   # spike duration, in frames
        self.weights = {k: float(v) for k, v in (weights or {}).items()}
        self.t_end = float(t_end)    # clock stops (and firing with it) at t = t_end; inf = never
        self.set_trigger(trigger)
        self.set_equation(equation)

    @property
    def clock_stopped(self):
        return self.t >= self.t_end

    @property
    def waiting(self):
        """True while a clock trigger is set and hasn't fired yet (clock at 0)."""
        return self._waiting

    def _reset_clock(self):
        """Back to t = 0. With a clock trigger, the clock then waits for it."""
        self.t = 0.0
        self._phase = 0.0            # accumulates rate*dt; fires each time it passes 1
        self._waiting = bool(self.trigger)
        self._trigger_was_true = False   # for edge detection: a trigger = false -> true

    def set_equation(self, text):
        """Install a firing equation and restart this neuron's clock at t = 0.
        A bad equation is kept (so you can see/fix it) but never fires."""
        self.equation = text.strip()
        self.fire_error = None
        self._code = None
        self._reset_clock()
        if self.equation:
            try:
                self._code = compile_firing_equation(self.equation)
            except ValueError as e:
                self.fire_error = str(e)

    def set_trigger(self, text):
        """Install a clock-trigger condition (e.g. "W > 3") and reset the clock.
        With one set, the clock waits at t = 0 until the condition is true,
        then runs to Clock end. Every later trigger (condition going false ->
        true) restarts it from 0, whether it's running or ended. Empty = no
        trigger: the clock just runs on its own."""
        self.trigger = text.strip()
        self.trigger_error = None
        self._trigger_code = None
        if self.trigger:
            try:
                self._trigger_code = compile_firing_equation(self.trigger, TRIGGER_NAMES)
            except ValueError as e:
                self.trigger_error = str(e)
        self._reset_clock()

    def fire(self):
        self._fire_frames = self.spike_frames

    @property
    def is_firing(self):
        """The firing Boolean: True for the `spike_frames` frames of a spike
        (while it's drawn yellow). Gates this neuron's W's into others' input."""
        return self._fire_frames > 0

    def outputs(self):
        return {"firing": 1.0 if self.is_firing else 0.0, "clock t": self.t,
                "input W": self.input_w()}

    def input_w(self):
        """Sum over every other neuron of (its W to this one) × (is it firing
        right now). Only neurons mid-spike contribute, so it's 0 between spikes."""
        if self.scene is None:
            return 0.0
        return sum(n.weights.get(self.id, 0.0) for n in self.scene.neurons
                   if n is not self and n.is_firing)

    def _evaluate(self, code, dt, **extra):
        t = self.t
        def every(s):
            return s > 0 and math.floor(t / s) != math.floor((t - dt) / s)
        def poisson(rate):
            return random.random() < 1 - math.exp(-max(0.0, rate) * dt)
        names = dict(FIRE_MATH, t=t, dt=dt, every=every, poisson=poisson,
                     random=random.random, **extra)
        return eval(code, {"__builtins__": {}}, names)

    def _check_trigger(self, dt):
        """Test the trigger every frame. Each time it *becomes* true (false ->
        true), the clock restarts from 0 — even mid-run. Holding true doesn't
        keep re-resetting it. Returns True if the clock may run now."""
        if not self.trigger:
            return True                          # no trigger: the clock just runs
        if self._trigger_code is None or self.trigger_error:
            return not self._waiting
        w = self.input_w()
        try:
            hit = bool(self._evaluate(self._trigger_code, dt, W=w, w=w))
        except Exception as e:
            self.trigger_error = str(e) or type(e).__name__
            return not self._waiting
        if hit and not self._trigger_was_true:   # rising edge = a trigger
            self.t, self._phase, self._waiting = 0.0, 0.0, False
        self._trigger_was_true = hit
        return not self._waiting

    def tick(self, dt):                          # replaces Agent's wandering: neurons stay put
        if self._fire_frames > 0:              # count down the current spike, one per frame
            self._fire_frames -= 1
        if dt <= 0 or not self._check_trigger(dt):
            return                               # waiting for the trigger: no clock, no firing
        if self._code is None or self.fire_error or self.clock_stopped:
            return                               # stopped clock = paused, no firing
        dt = min(dt, self.t_end - self.t)        # don't run past the end
        self.t += dt
        try:
            result = self._evaluate(self._code, dt)
        except Exception as e:                   # e.g. log(0): stop and show why
            self.fire_error = str(e) or type(e).__name__
            return
        if isinstance(result, bool):
            if result:
                self.fire()
        else:                                    # a rate in fires/sec
            self._phase += max(0.0, float(result)) * dt
            if self._phase >= 1:
                self.fire()
                self._phase %= 1                 # at most one fire per frame

    def draw_shape(self, surface, rect, camera):
        body = NEURON_FIRE_COLOR if self.is_firing else NEURON_COLOR
        draw_rect(surface, body, rect, border_radius=max(2, rect.width // 8))
        spot = pygame.Rect(0, 0, max(2, rect.width // 6), max(2, rect.height // 3))
        spot.center = rect.center                      # same proportions as an agent eye
        pygame.draw.ellipse(surface, NEURON_SPOT, spot)

    # --- Inspector -------------------------------------------------------------
    def panel_status(self, app):
        if self.fire_error:
            return f"firing equation: {self.fire_error}", WARN_COLOR
        return None

    def state_rows(self, app):
        rows = [Field("Firing equation (fires per second)", self.equation or "— click to set one",
                      on_click=self.edit_equation)]
        if not self.fire_error:
            state = ("  (waiting)" if self.waiting else
                     "  (stopped)" if self.clock_stopped else "")
            rows.append(Row("Clock", f"t = {self.t:.1f} s{state}"))
        rows.append(Row("Clock end", "t = INF" if math.isinf(self.t_end) else f"t = {self.t_end:g} s",
                        on_click=self.edit_t_end))
        if self.trigger_error:
            rows.append(Row("Clock trigger", f"error: {self.trigger_error}", color=WARN_COLOR,
                            on_click=self.edit_trigger))
        else:
            rows.append(Row("Clock trigger", self.trigger or "— (none, runs freely)",
                            on_click=self.edit_trigger))
        frames = self.spike_frames
        rows.append(Stepper("Spike duration", f"{frames} fr ({frames * 1000 / 60:.0f} ms)",
                            on_down=lambda app: self.step_spike(app, -1),
                            on_up=lambda app: self.step_spike(app, +1),
                            down_enabled=frames > 1))
        count = len(self.scene.neurons) if self.scene else 1
        rows.append(Row("Network", f"{count} / ~{NEURON_SOFT_LIMIT} neurons",   # orange = past the smooth limit
                        color=WARN_COLOR if count > NEURON_SOFT_LIMIT else None))
        # Σ (other's W to this) × (other is firing). O(n), selected neuron only.
        rows.append(Row("Input W (from firing others)", f"{self.input_w():.2f}"))
        return rows

    def more_sections(self, app):
        items = []
        for other_id, w in self.weights.items():
            other = self.scene.by_id.get(other_id) if self.scene else None
            name = other.text if other is not None and other.text else other_id
            items.append((name, f"{w:.2f}",
                          lambda app, oid=other_id, name=name: self.edit_weight(app, oid, name)))
        return [Section("Weights", [ScrollList("Output W's (to each other neuron)", items,
                                               empty="— (no other neurons)", key=self)])]

    def edit_equation(self, app):
        def submit(text):
            self.set_equation(text)              # restarts the clock at t = 0
            app.save()
        app.ask("Firing equation (e.g. every(1), poisson(0.5)):", submit,
                value=self.equation, q_pastes=False)   # so 'sqrt' can be typed

    def edit_trigger(self, app):
        def submit(text):
            self.set_trigger(text)               # resets the clock; waits for the trigger
            app.save()
        app.ask("Clock trigger, e.g. W > 3 (empty = none):", submit,
                value=self.trigger, q_pastes=False)

    def edit_t_end(self, app):
        def submit(text):
            text = text.strip().lower()
            if text in ("", "inf", "infinity"):
                value = math.inf
            else:
                try:
                    value = float(text)
                except ValueError:
                    return                       # not a number: leave it unchanged
            if value >= 0 and not math.isnan(value):
                self.t_end = value               # raising it past t resumes the clock
                app.save()
        app.ask("Clock end, in seconds (or INF for never):", submit,
                value="INF" if math.isinf(self.t_end) else f"{self.t_end:g}")

    def edit_weight(self, app, other_id, name):
        def submit(text):
            try:
                value = float(text.strip())
            except ValueError:
                return                           # not a number: leave the W unchanged
            if math.isfinite(value) and other_id in self.weights:
                self.weights[other_id] = value
                app.save()
        app.ask(f"W to {name}:", submit, value=f"{self.weights.get(other_id, 0.0):g}")

    def step_spike(self, app, step):
        self.spike_frames = max(1, self.spike_frames + step)
        app.save()

    def to_dict(self):
        d = super().to_dict()
        if self.equation:
            d["equation"] = self.equation
        if self.trigger:
            d["trigger"] = self.trigger
        if self.spike_frames != NEURON_FIRE_FRAMES:
            d["spike_frames"] = self.spike_frames
        if not math.isinf(self.t_end):
            d["t_end"] = self.t_end              # INF (the default) isn't written
        if self.weights:
            d["weights"] = dict(self.weights)
        return d

    @classmethod
    def from_dict(cls, d):
        return cls(d["x"], d["y"], d.get("name", ""), id=d.get("id"),
                   equation=d.get("equation", ""), trigger=d.get("trigger", ""),
                   spike_frames=d.get("spike_frames", NEURON_FIRE_FRAMES),
                   weights=d.get("weights"), t_end=d.get("t_end", math.inf))


NEURON_W_DEFAULT = 0.0             # every new connection's W starts here
NEURON_SOFT_LIMIT = 100             # past ~this many neurons the mesh gets choppy while panning


def new_weight():
    return NEURON_W_DEFAULT


NEURON_LINK_COLOR = (48, 48, 56)    # barely lighter than BG_COLOR — meant to be subtle


class NeuronMesh:
    """Every neuron is linked to every other. The links aren't stored anywhere —
    they follow entirely from neuron positions: one thin line per pair (i < j,
    so no pair is drawn twice). That's n(n-1)/2 lines, so they're drawn onto a
    cached layer covering the network (cut to the window plus a margin), and
    panning just moves it. It's redrawn when the zoom changes, a neuron moves /
    comes / goes, or a pan runs past the margin. The layer's memory is reused
    between redraws. Drawn before the objects, so lines sit under the neurons."""

    MARGIN = 0.5                # extra layer around the window, in window sizes (cheap panning)

    def __init__(self):
        self._key = None
        self._buffer = None      # reused pixel memory; the layer is its top-left w x h
        self._size = (0, 0)
        self._world_tl = (0.0, 0.0)   # world point at the layer's top-left
        self._world_box = None        # the network's world bounds (minx, miny, maxx, maxy)

    def draw(self, surface, camera, neurons):
        if len(neurons) < 2:
            return
        key = (camera.zoom, surface.get_size(),
               tuple((n.world_center.x, n.world_center.y) for n in neurons))
        if key != self._key or not self._covers_visible(surface, camera):
            self._rebuild(surface, camera, neurons, key)
        tl = camera.world_to_screen(self._world_tl)
        w, h = self._size
        if w > 0 and h > 0:
            surface.blit(self._buffer, (round(tl[0]), round(tl[1])), pygame.Rect(0, 0, w, h))

    def _covers_visible(self, surface, camera):
        """Does the layer still hold every part of the network that's on screen?
        (Panning past its margin means a redraw.)"""
        x0, y0 = camera.world_to_screen(self._world_box[:2])
        x1, y1 = camera.world_to_screen(self._world_box[2:])
        needed = pygame.Rect(math.floor(x0), math.floor(y0), math.ceil(x1 - x0) + 1,
                             math.ceil(y1 - y0) + 1).clip(surface.get_rect())
        tl = camera.world_to_screen(self._world_tl)
        layer = pygame.Rect(round(tl[0]), round(tl[1]), *self._size)
        return needed.width == 0 or needed.height == 0 or layer.contains(needed)

    def _rebuild(self, surface, camera, neurons, key):
        sw, sh = surface.get_size()
        points = [camera.world_to_screen(n.world_center) for n in neurons]
        xs, ys = [p[0] for p in points], [p[1] for p in points]
        # The network's box, cut to the window plus a margin on every side.
        mx, my = sw * self.MARGIN, sh * self.MARGIN
        left = math.floor(max(min(xs), -mx)) - 1
        top = math.floor(max(min(ys), -my)) - 1
        right = math.ceil(min(max(xs), sw + mx)) + 2
        bottom = math.ceil(min(max(ys), sh + my)) + 2
        w, h = max(0, right - left), max(0, bottom - top)
        if self._buffer is None or self._buffer.get_width() < w or self._buffer.get_height() < h:
            bw = max(w, self._buffer.get_width() if self._buffer else 0)
            bh = max(h, self._buffer.get_height() if self._buffer else 0)
            self._buffer = pygame.Surface((bw, bh))
            self._buffer.set_colorkey((0, 0, 0))            # black = see-through
        area = pygame.Rect(0, 0, w, h)
        self._buffer.fill((0, 0, 0), area)
        prev_clip = self._buffer.get_clip()
        self._buffer.set_clip(area)
        local = [(p[0] - left, p[1] - top) for p in points]
        for i, a in enumerate(local):
            for b in local[i + 1:]:
                pygame.draw.line(self._buffer, NEURON_LINK_COLOR, a, b)
        self._buffer.set_clip(prev_clip)
        self._size = (w, h)
        self._world_tl = camera.screen_to_world((left, top))
        wx = [n.world_center.x for n in neurons]
        wy = [n.world_center.y for n in neurons]
        self._world_box = (min(wx), min(wy), max(wx), max(wy))
        self._key = key


# (The type registry, OBJECT_TYPES, fills itself: CanvasObject.__init_subclass__
#  adds every class that sets a TYPE. The Add menu and the loader both read it.)


# ============================================================================
# ACTIONS — what a node's "Activate" runs (a small registry, keyed by name)
# ============================================================================
# A node stores action (a name) + params (its data). run_action looks the name
# up here and calls it. Add a behavior = one decorated function; the loop and
# inspector never change.
ACTIONS = {}


def action(name):
    def register(fn):
        ACTIONS[name] = fn
        return fn
    return register


# Actions are called as fn(node, app): the App (bottom of this file) gives them
# the camera (for internal actions like goto), scene (id lookups), screen size.


@action("run_command")
def _run_command(node, ctx):
    """The workhorse: launch a shell command detached (non-blocking) so a
    long-running command never freezes the canvas. Covers agents / URLs /
    scripts / opening files & folders — anything a shell can do."""
    command = node.params.get("command")
    if command:
        subprocess.Popen(command, cwd=node.params.get("cwd") or None, shell=True)


@action("goto")
def _goto(node, ctx):
    """Internal action: jump the camera to another node by id (a teleport)."""
    target = ctx.scene.by_id.get(node.params.get("target"))
    if target is not None:
        ctx.camera.look_at(target.world_center, GOTO_ZOOM, ctx.screen_size)


def run_action(node, ctx):
    fn = ACTIONS.get(getattr(node, "action", None))
    if fn is not None:
        fn(node, ctx)


# ============================================================================
# PERSISTENCE — load/save the scene (the clean seam; swap-able for a DB later)
# ============================================================================
DEFAULT_OBJECTS = [
    {"type": "button", "name": "Finances", "x": 320, "y": 230},
    {"type": "button", "name": "Taxes", "x": 320, "y": 330},
]


def object_from_dict(d):
    """Saved dict -> live object. A missing type means button (old data written
    before types existed); an unknown type is kept as an UnknownObject."""
    cls = OBJECT_TYPES.get(d.get("type", "button"))
    if cls is None:
        return UnknownObject(d)
    d = cls.migrate(d)
    obj = cls.from_dict(d)
    obj.load_props(d)
    return obj


def load_objects(path=NODES_FILE):
    """Return a list of live canvas objects. Reads the new {"objects": [...]}
    shape and the legacy {"nodes": [...]} shape; backfills ids and rewrites the
    file once if it was in the old shape or missing ids."""
    if not os.path.exists(path):
        objects = [object_from_dict(d) for d in DEFAULT_OBJECTS]
        save_objects(objects, path)
        return objects

    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    raw = data.get("objects")
    legacy = raw is None
    if legacy:
        raw = data.get("nodes", [])

    objects = [object_from_dict(d) for d in raw]

    needs_migration = legacy or any(d.get("id") is None for d in raw)
    if needs_migration:
        save_objects(objects, path, view=data.get("view"))
    return objects


def load_view(path=NODES_FILE):
    """The camera view saved at last close — {"x", "y", "zoom"}, where x/y is the
    world point at the center of the screen — or None if there isn't one."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            view = json.load(f).get("view")
        return {k: float(view[k]) for k in ("x", "y", "zoom")}
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        return None


def save_objects(objects, path=NODES_FILE, view=None):
    data = {"objects": [o.to_dict() for o in objects]}
    if view is not None:
        data["view"] = view
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


# ============================================================================
# SCENE — the live state container (objects, camera, selection, id index)
# ============================================================================
class Scene:
    """Holds the live objects + camera and owns mutation/persistence, so the
    game loop stays wiring-only. `by_id` is an O(1) index for lookups (e.g. a
    future connector resolving its two endpoints)."""

    def __init__(self, objects, camera=None, view=None):
        self.objects = objects
        self.camera = camera or Camera()
        self.view = view        # last saved camera view (see load_view); kept on every save
        self.mesh = NeuronMesh()  # the faint all-to-all lines between neurons
        self.by_id = {o.id: o for o in objects}
        for o in objects:
            o.scene = self      # so connectors (Train) can resolve their nodes
        for o in objects:       # heal any legacy overhang corners in old trains
            if isinstance(o, Train):
                o.simplify_waypoints()
        self._weights_filled = self.sync_weights()   # True if W's were just created/dropped

    @property
    def neurons(self):
        return [o for o in self.objects if isinstance(o, Neuron)]

    def sync_weights(self):
        """Make every neuron hold exactly one W per *other* neuron: give new
        pairs a W of NEURON_W_DEFAULT (0), drop W's to neurons that no longer exist. Existing
        W's are never changed. Returns True if anything changed."""
        neurons = self.neurons
        ids = {n.id for n in neurons}
        changed = False
        for n in neurons:
            stale = [k for k in n.weights if k not in ids or k == n.id]
            for k in stale:
                del n.weights[k]
            for other in neurons:
                if other is not n and other.id not in n.weights:
                    n.weights[other.id] = new_weight()
                    changed = True
            changed = changed or bool(stale)
        return changed

    @classmethod
    def load(cls):
        scene = cls(load_objects(), view=load_view())
        if scene._weights_filled:
            scene.save()     # persist any W's just created, so they're stable from now on
        return scene

    def save(self):
        save_objects(self.objects, view=self.view)

    def restore_view(self, screen_size):
        """Put the camera back where it was at last close (if anything was saved)."""
        if self.view:
            self.camera.look_at((self.view["x"], self.view["y"]), self.view["zoom"], screen_size)

    def remember_view(self, screen_size):
        """Record where the camera is now (screen-center world point + zoom), so
        the next save writes it and the next launch reopens here."""
        cx, cy = self.camera.screen_to_world((screen_size[0] / 2, screen_size[1] / 2))
        self.view = {"x": cx, "y": cy, "zoom": self.camera.zoom}

    @property
    def selection(self):
        """Every selected object (a drag-box can select many)."""
        return [o for o in self.objects if o.selectable and o.is_selected]

    @property
    def selected(self):
        """The one selected object, or None if nothing — or several — are
        selected. Single-object actions (rename, inspector, copy, goto) use this,
        so they simply don't apply to a multi-selection."""
        sel = self.selection
        return sel[0] if len(sel) == 1 else None

    def select_only(self, obj):
        """Select obj (or deselect everything if obj is None)."""
        self.select_many([obj] if obj is not None else [])

    def select_many(self, objs):
        objs = set(map(id, objs))
        for o in self.objects:
            if o.selectable:
                o.is_selected = id(o) in objs

    def objects_in_box(self, x, y, w, h):
        """What a drag-box over world rect (x, y, w, h) selects — each object
        decides via in_box (nodes: center inside; walls: entirely inside)."""
        return [o for o in self.objects if o.in_box(x, y, w, h)]

    def hit_test(self, pos, camera):
        """The topmost object under a screen point, or None — the same order
        it's drawn in, reversed, so you click what you see on top."""
        for o in reversed(sorted(self.objects, key=lambda o: o.draw_layer)):
            if o.hit(pos, camera):
                return o
        return None

    def give_at(self, pos, camera):
        """(object, output) whose output nub is under a screen point, or None —
        checked before hit_test, since a nub sits on (or past) its object's edge."""
        for o in reversed(sorted(self.objects, key=lambda o: o.draw_layer)):
            key = o.give_at(pos, camera)
            if key is not None:
                return o, key
        return None

    def add(self, obj):
        self.add_many([obj])

    def add_many(self, objs):
        """Add several objects (a placed machine) with one save."""
        for obj in objs:
            obj.scene = self
            self.objects.append(obj)
            self.by_id[obj.id] = obj
        self.sync_weights()
        self.save()

    def delete(self, obj):
        self.delete_many([obj])

    def delete_many(self, objs):
        removed = False
        for obj in objs:
            if obj in self.objects:
                self.objects.remove(obj)
                self.by_id.pop(obj.id, None)
                obj.on_removed()
                removed = True
        if removed:
            self.sync_weights()
            self.save()

    # Each object's update/draw is guarded: one object's bug (say, at an odd
    # zoom) is logged to errors.log and marked on screen instead of crashing
    # the whole app.
    def update(self, mouse_pos, mouse_pressed, camera, dt):
        for o in self.objects:
            try:
                o.update(mouse_pos, mouse_pressed, camera, dt)
                o.watch_presses(dt)
            except Exception as e:
                report_error(f"{o.TYPE}.update", e)

    def draw(self, surface, camera):
        self.mesh.draw(surface, camera, self.neurons)
        for o in self.objects:
            try:
                o.draw_links(surface, camera)
            except Exception as e:
                report_error(f"{o.TYPE}.draw_links", e)
        view = surface.get_rect()
        for o in sorted(self.objects, key=lambda o: o.draw_layer):
            try:
                if not self._on_screen(o, camera, view):
                    continue                    # off-screen: skip the (costly) drawing
                o.draw(surface, camera)
            except Exception as e:
                report_error(f"{o.TYPE}.draw", e)
                self._draw_broken(surface, camera, o)

    @staticmethod
    def _on_screen(obj, camera, view):
        """Is obj (plus a margin of its own size + 40px, for glows / halos /
        selection rings) touching the window? Objects without bounds always draw."""
        bounds = obj.screen_bounds(camera)
        if bounds is None:
            return True
        return bounds.inflate(bounds.width + 40, bounds.height + 40).colliderect(view)

    @staticmethod
    def _draw_broken(surface, camera, obj):
        """A red box with an X where an object failed to draw (see errors.log)."""
        try:
            rect = obj.screen_rect(camera).inflate(4, 4)
        except Exception:
            return
        if rect.width > 4000 or rect.height > 4000:
            return
        pygame.draw.rect(surface, (220, 60, 60), rect, width=2)
        pygame.draw.line(surface, (220, 60, 60), rect.topleft, rect.bottomright, 2)
        pygame.draw.line(surface, (220, 60, 60), rect.topright, rect.bottomleft, 2)


# ---- Copying ----------------------------------------------------------------
COPY_OFFSET = 40   # world units to nudge a copy so it doesn't sit exactly on top


def copy_object(obj, at=None):
    """Duplicate any copyable object (new id, carrying its saved data — name,
    command, props, size, …, minus its COPY_SKIP keys). With `at` (a world
    point) the copy is centered there; otherwise it's nudged over so it's
    visible. Returns None if nothing's given or the type isn't copyable (e.g.
    a Train, which is defined by two node ids)."""
    if obj is None or not obj.copyable:
        return None
    data = obj.to_dict()
    data.pop("id", None)                       # from_dict assigns a fresh id
    for key in obj.COPY_SKIP:
        data.pop(key, None)
    new = object_from_dict(data)
    if at is None:
        new.move(COPY_OFFSET, COPY_OFFSET)
    else:
        c = new.center
        new.move(at[0] - c[0], at[1] - c[1])
    return new


# ---- Machines: a saved group of objects, placed again as a whole ------------
# Select objects, right-click ▸ Create machine…: their saved data goes into
# machines.json (next to Nodes.json). The E menu's Machines tab lists them;
# placing one builds fresh objects — new ids, with links between its own
# objects rewired to the new copies (links to anything outside stay as they
# were). Once placed they're ordinary objects, selected together.
MACHINES_FILE = os.path.join(SCRIPT_DIR, "machines.json")
_machines = None        # loaded on first use, then kept in step with the file


def machines():
    """The saved machines: [{"name", "created", "center": [x, y], "objects": [saved dicts]}]."""
    global _machines
    if _machines is None:
        try:
            with open(MACHINES_FILE, "r", encoding="utf-8") as f:
                _machines = list(json.load(f).get("machines", []))
        except (OSError, ValueError, AttributeError):
            _machines = []
    return _machines


def _save_machines():
    with open(MACHINES_FILE, "w", encoding="utf-8") as f:
        json.dump({"machines": machines()}, f, indent=2)


def group_bounds(objs):
    """World (left, top, right, bottom) around the objects: a wall's own rect, else
    its center ± half its size."""
    boxes = []
    for o in objs:
        if all(hasattr(o, a) for a in ("x", "y", "w", "h")) and not hasattr(o, "world_width"):
            boxes.append((o.x, o.y, o.x + o.w, o.y + o.h))
            continue
        c = o.center
        if c is None:
            continue
        hw, hh = getattr(o, "world_width", 0) / 2, getattr(o, "world_height", 0) / 2
        boxes.append((c[0] - hw, c[1] - hh, c[0] + hw, c[1] + hh))
    if not boxes:
        return 0.0, 0.0, 0.0, 0.0
    return (min(b[0] for b in boxes), min(b[1] for b in boxes),
            max(b[2] for b in boxes), max(b[3] for b in boxes))


def machine_parts(objs):
    """(kept, skipped): what can go in a machine — anything copyable that isn't one-only."""
    kept = [o for o in objs if o.copyable and not o.SINGLETON]
    return kept, [o for o in objs if o not in kept]


def save_machine(name, objs):
    """Save the copyable objects among objs as a new machine (a taken name gets
    " (2)", …). Returns the machine dict, or None if none of them can go in one."""
    kept, _skipped = machine_parts(objs)
    if not kept:
        return None
    taken = {m["name"] for m in machines()}
    base, n = name.strip() or "Machine", 2
    name = base
    while name in taken:
        name, n = f"{base} ({n})", n + 1
    left, top, right, bottom = group_bounds(kept)
    saved = []
    for o in kept:
        d = o.to_dict()
        for key in o.COPY_SKIP:
            d.pop(key, None)
        saved.append(d)
    machine = {"name": name, "created": time.strftime("%Y-%m-%d %H:%M:%S"),
               "center": [(left + right) / 2, (top + bottom) / 2], "objects": saved}
    machines().append(machine)
    _save_machines()
    return machine


def rename_machine(machine, name):
    if name.strip() and name.strip() not in {m["name"] for m in machines() if m is not machine}:
        machine["name"] = name.strip()
        _save_machines()
        return True
    return False


def delete_machine(machine):
    if machine in machines():
        machines().remove(machine)
        _save_machines()


def _remap_ids(value, mapping):
    """value with every string that's an old id swapped for its new one."""
    if isinstance(value, dict):
        return {k: _remap_ids(v, mapping) for k, v in value.items()}
    if isinstance(value, list):
        return [_remap_ids(v, mapping) for v in value]
    if isinstance(value, str):
        return mapping.get(value, value)
    return value


def build_machine(machine, at):
    """Fresh objects for a machine, centered on world point `at`."""
    mapping = {d["id"]: new_id() for d in machine["objects"] if d.get("id")}
    objs = [object_from_dict(_remap_ids(d, mapping)) for d in machine["objects"]]
    cx, cy = machine.get("center", (0, 0))
    for o in objs:
        if o.movable:
            o.move(at[0] - cx, at[1] - cy)
    return objs


class MachineGroup:
    """A machine in hand while placing: its objects, moved and drawn together
    (PlaceMode adds them all when it's dropped)."""

    SINGLETON = False

    def __init__(self, machine, at):
        self.machine = machine
        self.members = build_machine(machine, at)

    @property
    def center(self):
        left, top, right, bottom = group_bounds(self.members)
        return ((left + right) / 2, (top + bottom) / 2)

    def move(self, dx, dy):
        for o in self.members:
            if o.movable:
                o.move(dx, dy)

    def draw(self, surface, camera):
        for o in self.members:
            try:
                o.draw(surface, camera)
            except Exception:          # one odd object shouldn't break the preview
                pass

    def place_name(self):
        return f"machine '{self.machine['name']}'"


def draw_machine_preview(machine, surface):
    """Draw the machine's objects scaled to fit `surface` (its E menu icon)."""
    objs = build_machine(machine, (0, 0))
    left, top, right, bottom = group_bounds(objs)
    w, h = max(1.0, right - left), max(1.0, bottom - top)
    size = surface.get_size()
    camera = Camera()
    camera.look_at(((left + right) / 2, (top + bottom) / 2), min(size[0] / w, size[1] / h) * 0.9, size)
    for o in objs:
        try:
            o.draw(surface, camera)
        except Exception:
            pass


# ============================================================================
# UI — hand-rolled chrome: menu bar, modals, inspector panel, build modes
# ============================================================================
# pygame/SDL has no native menu bar, so the top strip and the modal dialogs are
# drawn and hit-tested by hand. The prototype's scattered *_active booleans are
# consolidated here into modal objects; the App keeps a single `modal`.

MENU_BAR_HEIGHT = 28
MENU_FONT_SIZE = 22
MENU_BG = (40, 40, 48)
MENU_HOVER = (60, 60, 72)
MENU_BORDER = (90, 90, 100)
MENU_TEXT = (230, 230, 235)


EDIT_MENU = [("Edit Name", "edit_name"), ("Copy", "copy"), ("Delete Node", "delete_node")]


class MenuBar:
    """The top strip: Add / Edit dropdowns + a flat Go To action. Owns its own
    layout, open/closed state, and hit-testing. `handle_click(pos, screen_size)`
    returns (consumed, command): `consumed` tells the loop to skip canvas
    selection for this click; `command` (or None) is the chosen action.

    The Add menu is built from OBJECT_TYPES (every type with a MENU_LABEL, in
    the order they were defined), giving "add:<type>" commands; it wraps into
    more columns when it's taller than the window."""

    def __init__(self):
        self.open_menu = None  # None / "add" / "edit"
        h = MENU_BAR_HEIGHT
        self.add_rect = pygame.Rect(0, 0, 70, h)
        self.edit_rect = pygame.Rect(self.add_rect.right, 0, 70, h)
        self.goto_rect = pygame.Rect(self.edit_rect.right, 0, 80, h)
        self.objects_rect = pygame.Rect(self.goto_rect.right, 0, 110, h)   # opens the E menu

    def _items(self, menu, screen_size):
        """[(rect, label, command), ...] for an open dropdown."""
        h = MENU_BAR_HEIGHT
        if menu == "add":
            entries = [(cls.MENU_LABEL, "add:" + cls.TYPE)
                       for cls in OBJECT_TYPES.values() if cls.MENU_LABEL]
            left = self.add_rect.left
        elif menu == "edit":
            entries, left = EDIT_MENU, self.edit_rect.left
        else:
            return []
        font = get_font(MENU_FONT_SIZE)
        width = max([150] + [font.size(label)[0] + 24 for label, _c in entries])
        per_col = max(1, screen_size[1] // h - 2)
        items = []
        for i, (label, command) in enumerate(entries):
            col, row = divmod(i, per_col)
            items.append((pygame.Rect(left + col * width, h * (row + 1), width, h), label, command))
        return items

    def handle_click(self, pos, screen_size):
        if self.add_rect.collidepoint(pos):
            self.open_menu = None if self.open_menu == "add" else "add"
            return True, None
        if self.edit_rect.collidepoint(pos):
            self.open_menu = None if self.open_menu == "edit" else "edit"
            return True, None
        if self.goto_rect.collidepoint(pos):
            self.open_menu = None
            return True, "goto"
        if self.objects_rect.collidepoint(pos):
            self.open_menu = None
            return True, "inventory"
        if self.open_menu:
            for rect, _label, command in self._items(self.open_menu, screen_size):
                if rect.collidepoint(pos):
                    self.open_menu = None
                    return True, command
            self.open_menu = None   # clicked off an open menu: close, consume
            return True, None
        return False, None

    def draw(self, surface):
        font = get_font(MENU_FONT_SIZE)
        pygame.draw.rect(surface, MENU_BG, (0, 0, surface.get_width(), MENU_BAR_HEIGHT))
        for rect, label, key in ((self.add_rect, "Add", "add"),
                                 (self.edit_rect, "Edit", "edit"),
                                 (self.goto_rect, "Go To", "goto"),
                                 (self.objects_rect, "Objects (E)", "inventory")):
            color = MENU_HOVER if self.open_menu == key else MENU_BG
            pygame.draw.rect(surface, color, rect)
            ts = font.render(label, True, MENU_TEXT)
            surface.blit(ts, ts.get_rect(center=rect.center))
        for rect, label, _command in self._items(self.open_menu, surface.get_size()):
            pygame.draw.rect(surface, MENU_BG, rect)
            pygame.draw.rect(surface, MENU_BORDER, rect, width=1)
            ts = font.render(label, True, MENU_TEXT)
            surface.blit(ts, ts.get_rect(center=rect.center))


def _draw_modal_frame(surface, box):
    """Dim the screen and draw a centered dialog box; shared by both modals."""
    overlay = pygame.Surface(surface.get_size(), pygame.SRCALPHA)
    overlay.fill((0, 0, 0, 150))
    surface.blit(overlay, (0, 0))
    pygame.draw.rect(surface, (45, 45, 55), box, border_radius=8)
    pygame.draw.rect(surface, (200, 200, 210), box, width=2, border_radius=8)


TEXT_KEY_REPEAT = (400, 35)   # (delay ms, interval ms) for held keys while typing


class TextInputModal:
    """Every "type some text" prompt (usually opened via app.ask). It manages
    the text buffer, its cursor, and its rendering; `handle_event` returns
    None / "submit" / "cancel", and on submit the loop calls commit(), which
    hands the text to `on_submit`.

    Editing keys: Left/Right move the cursor, Home/End jump to the ends,
    Backspace/Delete remove around it (Ctrl+Backspace removes a word). Holding a
    key repeats it — the loop turns on key-repeat while one of these is open."""

    def __init__(self, prompt, on_submit, *, value="", q_pastes=True):
        self.prompt = prompt
        self.on_submit = on_submit
        self.value = value
        self.cursor = len(value)   # insertion point: index into value
        self.q_pastes = q_pastes   # False where 'q' must be typable (e.g. sqrt)
        self._last_key = 0         # ticks of the last edit, so the caret shows while typing

    def commit(self):
        self.on_submit(self.value)

    def _insert(self, text):
        self.value = self.value[:self.cursor] + text + self.value[self.cursor:]
        self.cursor += len(text)

    def handle_event(self, event, screen_size):
        if event.type in (pygame.TEXTINPUT, pygame.KEYDOWN):
            self._last_key = pygame.time.get_ticks()
        if event.type == pygame.TEXTINPUT:
            if not (self.q_pastes and event.text in ("q", "Q")):   # 'q' pastes, so isn't typed
                self._insert(event.text)
        elif event.type == pygame.KEYDOWN:
            ctrl = event.mod & pygame.KMOD_CTRL
            if (self.q_pastes and event.key == pygame.K_q) or (event.key == pygame.K_v and ctrl):
                # Paste the clipboard (q, or Ctrl+V); collapsed to one line.
                self._insert(read_clipboard().replace("\r", " ").replace("\n", " "))
            elif event.key == pygame.K_BACKSPACE:
                if ctrl:                                   # delete the word before the cursor
                    start = len(self.value[:self.cursor].rstrip())
                    while start > 0 and not self.value[start - 1].isspace():
                        start -= 1
                else:
                    start = max(0, self.cursor - 1)
                self.value = self.value[:start] + self.value[self.cursor:]
                self.cursor = start
            elif event.key == pygame.K_DELETE:
                self.value = self.value[:self.cursor] + self.value[self.cursor + 1:]
            elif event.key == pygame.K_LEFT:
                self.cursor = max(0, self.cursor - 1)
            elif event.key == pygame.K_RIGHT:
                self.cursor = min(len(self.value), self.cursor + 1)
            elif event.key == pygame.K_HOME:
                self.cursor = 0
            elif event.key == pygame.K_END:
                self.cursor = len(self.value)
            elif event.key in (pygame.K_RETURN, pygame.K_KP_ENTER):
                return "submit"
            elif event.key == pygame.K_ESCAPE:
                return "cancel"
        return None

    def draw(self, surface):
        box = pygame.Rect(0, 0, 440, 112)
        box.center = surface.get_rect().center
        _draw_modal_frame(surface, box)

        font = get_font(MENU_FONT_SIZE)
        surface.blit(font.render(self.prompt, True, MENU_TEXT), (box.x + 16, box.y + 14))

        field = pygame.Rect(box.x + 16, box.y + 44, box.width - 32, 32)
        pygame.draw.rect(surface, (25, 25, 30), field, border_radius=4)
        pygame.draw.rect(surface, (120, 120, 130), field, width=1, border_radius=4)

        # Scroll long text sideways so the cursor always stays inside the field.
        inner = field.inflate(-12, 0)
        caret_x = font.size(self.value[:self.cursor])[0]
        scroll = max(0, caret_x - inner.width + 2)
        prev_clip = surface.get_clip()
        surface.set_clip(inner)
        surface.blit(font.render(self.value, True, (255, 255, 255)),
                     (inner.x - scroll, field.y + 6))
        now = pygame.time.get_ticks()
        if now - self._last_key < 500 or now % 1000 < 500:   # solid while typing, then blink
            cx = inner.x - scroll + caret_x
            pygame.draw.line(surface, (255, 255, 255), (cx, field.y + 6), (cx, field.bottom - 6), 2)
        surface.set_clip(prev_clip)

        paste = "Q: paste" if self.q_pastes else "Ctrl+V: paste"
        hint = get_font(18).render(f"Enter: confirm     Esc: cancel     {paste}",
                                   True, (150, 150, 160))
        surface.blit(hint, (box.x + 16, box.bottom - 24))


class ConfirmModal:
    """Delete confirmation (usually opened via app.confirm). `layout` is shared
    by draw and hit-testing so they can't drift. `handle_event` returns None /
    "confirm" / "cancel"; on confirm the loop calls commit() -> on_confirm()."""

    def __init__(self, message, sub, on_confirm, ok_label="Delete"):
        self.message = message
        self.sub = sub
        self.on_confirm = on_confirm
        self.ok_label = ok_label                # the confirming button ("Delete", "Flip it", …)

    def commit(self):
        self.on_confirm()

    def layout(self, screen_size):
        font = get_font(MENU_FONT_SIZE)                  # wide enough for its text
        width = max(300, font.size(self.message)[0] + 32, font.size(self.sub)[0] + 32)
        box = pygame.Rect(0, 0, min(width, screen_size[0] - 20), 130)
        box.center = (screen_size[0] // 2, screen_size[1] // 2)
        bw, bh, gap = 100, 32, 16
        start_x = box.centerx - (bw * 2 + gap) // 2
        by = box.bottom - 46
        cancel = pygame.Rect(start_x, by, bw, bh)
        delete = pygame.Rect(start_x + bw + gap, by, bw, bh)
        return box, cancel, delete

    def handle_event(self, event, screen_size):
        if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            box, cancel, delete = self.layout(screen_size)
            if delete.collidepoint(event.pos):
                return "confirm"
            if cancel.collidepoint(event.pos) or not box.collidepoint(event.pos):
                return "cancel"
        elif event.type == pygame.KEYDOWN:
            if event.key == pygame.K_RETURN:
                return "confirm"
            if event.key == pygame.K_ESCAPE:
                return "cancel"
        return None

    def draw(self, surface):
        box, cancel, delete = self.layout(surface.get_size())
        _draw_modal_frame(surface, box)

        font = get_font(MENU_FONT_SIZE)
        surface.blit(font.render(self.message, True, MENU_TEXT), (box.x + 16, box.y + 16))
        if self.sub:
            surface.blit(font.render(self.sub, True, (170, 170, 180)), (box.x + 16, box.y + 42))

        pygame.draw.rect(surface, (70, 70, 82), cancel, border_radius=4)
        cl = font.render("Cancel", True, MENU_TEXT)
        surface.blit(cl, cl.get_rect(center=cancel.center))

        pygame.draw.rect(surface, (150, 50, 50), delete, border_radius=4)
        dl = font.render(self.ok_label, True, (255, 255, 255))
        surface.blit(dl, dl.get_rect(center=delete.center))


CONTEXT_DANGER = (255, 120, 110)     # a destructive context-menu item's text


class ContextMenu:
    """A small right-click menu at the cursor (usually opened via
    app.context_menu). items: [(label, callback, style)] or (label, callback,
    style, detail) — detail is grey text on the right (a live value). style
    "danger" (red text), "title" (a grey heading, not clickable) or None. A
    click on an item picks it — the loop calls commit(), which runs its
    callback (it may open another modal, e.g. a confirm). A click anywhere
    else, or Esc, closes it."""

    def __init__(self, pos, items):
        self.pos = pos
        self.items = [tuple(item) + (None,) * (4 - len(item)) for item in items]
        self.chosen = None

    def commit(self):
        if self.chosen is not None:
            self.chosen[1]()

    def layout(self, screen_size):
        """[(rect, item)] — opens down-right of the cursor, kept on screen."""
        font = get_font(MENU_FONT_SIZE)
        h = MENU_BAR_HEIGHT
        width = max([140] + [font.size(label)[0] + 28 + (font.size(detail)[0] + 24 if detail else 0)
                             for label, _cb, _st, detail in self.items])
        width = min(width, screen_size[0] - 4)
        x = max(2, min(self.pos[0], screen_size[0] - width - 2))
        y = max(2, min(self.pos[1], screen_size[1] - h * len(self.items) - 2))
        return [(pygame.Rect(x, y + i * h, width, h), item) for i, item in enumerate(self.items)]

    def handle_event(self, event, screen_size):
        if event.type == pygame.MOUSEBUTTONDOWN and event.button in (1, 3):
            for rect, item in self.layout(screen_size):
                if rect.collidepoint(event.pos) and event.button == 1:
                    if item[2] == "title":
                        return None                 # a heading: not an item
                    self.chosen = item
                    return "submit"
            return "cancel"
        if event.type == pygame.KEYDOWN and event.key == pygame.K_ESCAPE:
            return "cancel"
        return None

    def draw(self, surface):
        font = get_font(MENU_FONT_SIZE)
        rows = self.layout(surface.get_size())
        if not rows:
            return
        frame = rows[0][0].unionall([r for r, _i in rows])
        pygame.draw.rect(surface, (14, 14, 18), frame.move(2, 3), border_radius=4)   # shadow
        mouse = pygame.mouse.get_pos()
        for rect, (label, _cb, style, detail) in rows:
            hot = rect.collidepoint(mouse) and style != "title"
            pygame.draw.rect(surface, MENU_HOVER if hot else MENU_BG, rect)
            color = (CONTEXT_DANGER if style == "danger" else
                     (140, 140, 155) if style == "title" else MENU_TEXT)
            room = rect.width - 28
            if detail:
                ds = font.render(clip_text(detail, font, room // 2), True, (140, 140, 155))
                surface.blit(ds, ds.get_rect(midright=(rect.right - 14, rect.centery)))
                room -= ds.get_width() + 16
            ts = font.render(clip_text(label, font, room), True, color)
            surface.blit(ts, ts.get_rect(midleft=(rect.x + 14, rect.centery)))
        pygame.draw.rect(surface, MENU_BORDER, frame, width=1, border_radius=4)


# ---- Inspector panel (right-side overlay; shows the selected object) --------
INSPECTOR_WIDTH = 300
INSPECTOR_BG = (34, 34, 42)
INSPECTOR_BORDER = (70, 70, 84)
INSPECTOR_TITLE = (235, 235, 240)
INSPECTOR_LABEL = (150, 150, 165)
INSPECTOR_VALUE = (228, 228, 234)
INSPECTOR_SECTION = (128, 128, 146)    # section titles
INSPECTOR_DIVIDER = (54, 54, 66)
INSPECTOR_HOVER = (58, 58, 72)
INSPECTOR_ACCENT = (120, 175, 255)     # "link", "+ add column": things that start something
INSPECTOR_PAD = 16
PANEL_SCROLL_STEP = 40                 # px the body scrolls per wheel notch
PANEL_MAX_BUTTONS = 4                  # footer buttons; the rest go in the ⋯ menu
SCROLL_LIST_ROWS = 10                  # a ScrollList's height, in rows
VERBS = {"number": "follows", "onoff": "on while it's on", "press": "acts each time it turns on"}


def clip_text(text, font, max_width):
    """Trim text with an ellipsis so it fits within max_width pixels."""
    if font.size(text)[0] <= max_width:
        return text
    while text and font.size(text + "…")[0] > max_width:
        text = text[:-1]
    return text + "…"


def wrap_text(text, font, max_width, max_lines=4):
    """Split text into lines that fit max_width (the last one clipped)."""
    lines, line = [], ""
    for word in str(text).split(" "):
        trial = f"{line} {word}" if line else word
        if font.size(trial)[0] <= max_width or not line:
            line = trial
        else:
            lines.append(line)
            line = word
    lines.append(line)
    if len(lines) > max_lines:
        lines = lines[:max_lines - 1] + [" ".join(lines[max_lines - 1:])]
    return [clip_text(s, font, max_width) for s in lines]


def split_actions(actions):
    """(footer buttons, overflow for the ⋯ menu): at most PANEL_MAX_BUTTONS in
    the footer — the first ones (main actions) and the last (connection)."""
    if len(actions) <= PANEL_MAX_BUTTONS:
        return actions, []
    keep = PANEL_MAX_BUTTONS - 1
    return actions[:keep] + actions[-1:], actions[keep:-1]


class InspectorPanel:
    """Right-edge overlay for the selected object. It knows nothing about
    object types: it draws a header (type, the object's name — click to rename
    — its status line and the ⋯ menu), then the Sections obj.inspector(app)
    returns (State, Inputs / takes, Outputs / gives, Settings, Info), scrolling
    as a whole when they don't fit, then obj.actions(app) as buttons at the
    bottom. Clicking a section's title folds it, remembered per object type.

    Every clickable spot it draws is remembered with its callback; handle_click
    returns the callback under the mouse (or None) for the loop to call with
    the app. The caller consumes the click either way."""

    def __init__(self):
        self._hot = []            # (rect, callback) for each clickable spot from the last draw
        self._panel = None        # the panel's rect while it's showing
        self._obj_id = None       # whose panel is showing (another object starts at the top)
        self._scroll = 0          # px the body is scrolled down
        self._max_scroll = 0
        self._list_rect = None    # a ScrollList's area (set by draw), for wheel routing
        self._list_key = None     # whose list is showing; a different one resets its scroll
        self._list_scroll = 0     # rows scrolled past at the top of the list
        self._list_max = 0
        self._collapsed = {}      # (object type, section title) -> folded
        self._hover_src = None    # the source of the input row under the mouse (ringed on the canvas)

    def rect(self, screen_size):
        w, h = screen_size
        return pygame.Rect(w - INSPECTOR_WIDTH, MENU_BAR_HEIGHT,
                           INSPECTOR_WIDTH, h - MENU_BAR_HEIGHT)

    def contains(self, pos, screen_size):
        return self.rect(screen_size).collidepoint(pos)

    def scroll(self, pos, wheel_y):
        """The mouse wheel over the panel scrolls a list under it, else the whole
        body. Returns True if the panel took it (so the canvas doesn't zoom)."""
        if self._panel is None or not self._panel.collidepoint(pos):
            return False
        if self._list_rect is not None and self._list_rect.collidepoint(pos):
            self._list_scroll = max(0, min(self._list_max, self._list_scroll - wheel_y * 3))
        else:
            self._scroll = max(0, min(self._max_scroll, self._scroll - wheel_y * PANEL_SCROLL_STEP))
        return True

    def handle_click(self, pos):
        for rect, callback in reversed(self._hot):     # the last drawn is on top
            if rect.collidepoint(pos):
                return callback
        return None

    def _clickable(self, surface, rect, callback, mouse):
        """Hover highlight + remember the hit rect, if the spot has a callback."""
        if callback is None:
            return
        if rect.collidepoint(mouse):
            pygame.draw.rect(surface, INSPECTOR_HOVER, rect, border_radius=4)
        self._hot.append((rect, callback))

    @staticmethod
    def _ring(surface, obj, camera):
        """Mark an object on the canvas (the source of the input row under the mouse)."""
        screen_rect = getattr(obj, "screen_rect", None)
        if screen_rect is None:
            return
        try:
            rect = screen_rect(camera)
        except Exception:
            return
        if rect.width < 4000 and rect.height < 4000:
            pygame.draw.rect(surface, LINK_COLOR, rect.inflate(16, 16), width=3, border_radius=10)

    def draw(self, surface, obj, app):
        self._hot = []
        self._list_rect = None
        self._panel = None
        if obj is None:
            self._hover_src = None
            return
        if obj.id != self._obj_id:
            self._obj_id, self._scroll, self._hover_src = obj.id, 0, None
        if self._hover_src is not None and self._hover_src.scene is not None:
            self._ring(surface, self._hover_src, app.camera)
        self._hover_src = None

        panel = self.rect(surface.get_size())
        self._panel = panel
        pygame.draw.rect(surface, INSPECTOR_BG, panel)
        pygame.draw.line(surface, INSPECTOR_BORDER, panel.topleft, panel.bottomleft, 2)
        mouse = pygame.mouse.get_pos()
        x, max_w = panel.x + INSPECTOR_PAD, panel.width - 2 * INSPECTOR_PAD

        # Footer buttons, listed top to bottom and stacked up from the panel's bottom.
        footer, _overflow = split_actions(obj.actions(app))
        bh, gap = 36, 10
        buttons, by = [], panel.bottom - INSPECTOR_PAD - bh
        for b in reversed(footer):
            buttons.append((pygame.Rect(x, by, max_w, bh), b))
            by -= bh + gap
        bottom = (by + bh + gap - 10) if buttons else panel.bottom - 4

        y = self._draw_header(surface, obj, app, x, panel.y + 12, max_w, mouse)

        # The body: every section, scrolled as a whole, between the header and the footer.
        body = pygame.Rect(panel.x + 2, y, panel.width - 2, max(0, bottom - y))
        inside = mouse if body.collidepoint(mouse) else (-1, -1)    # rows hidden under the footer aren't hovered
        header_hot, self._hot = self._hot, []
        prev_clip = surface.get_clip()
        surface.set_clip(body)
        top = body.y + 4 - self._scroll
        cy = top
        for row in obj.inspector(app):
            cy = self._draw_row(surface, row, x, cy, max_w, inside, obj)
        surface.set_clip(prev_clip)
        content = cy - top + 8
        self._max_scroll = max(0, content - body.height)
        self._scroll = min(self._scroll, self._max_scroll)
        self._hot = header_hot + [(r.clip(body), cb) for r, cb in self._hot if r.colliderect(body)]
        if self._list_rect is not None:
            self._list_rect = self._list_rect.clip(body)
        if self._max_scroll:                         # a thin scrollbar on the right edge
            track = pygame.Rect(panel.right - 6, body.y + 2, 3, max(1, body.height - 4))
            thumb_h = max(16, track.height * body.height // max(1, content))
            thumb_y = track.y + (track.height - thumb_h) * self._scroll // self._max_scroll
            pygame.draw.rect(surface, (50, 50, 60), track)
            pygame.draw.rect(surface, (130, 130, 145), (track.x, thumb_y, 3, thumb_h))

        if buttons:
            pygame.draw.line(surface, INSPECTOR_DIVIDER, (panel.x + 2, bottom), (panel.right, bottom))
        font = get_font(22)
        for rect, b in buttons:
            if not b.enabled:
                bcolor, tcolor = (55, 55, 64), (120, 120, 130)
            elif b.style == "go":
                bcolor, tcolor = (60, 130, 70), (240, 240, 240)
            elif b.style == "stop":
                bcolor, tcolor = (170, 48, 48), (250, 240, 240)
            else:
                bcolor, tcolor = (70, 70, 88), INSPECTOR_TITLE
            if b.enabled and rect.collidepoint(mouse):
                bcolor = tuple(min(255, c + 14) for c in bcolor)
            pygame.draw.rect(surface, bcolor, rect, border_radius=6)
            ts = font.render(clip_text(b.label, font, rect.width - 12), True, tcolor)
            surface.blit(ts, ts.get_rect(center=rect.center))
            if b.enabled:
                self._hot.append((rect, b.on_click))

    def _draw_header(self, surface, obj, app, x, y, max_w, mouse):
        """The type, the object's name (click to rename), its status line and the
        ⋯ menu button. Returns the y the body starts at."""
        dots = pygame.Rect(x + max_w - 30, y, 34, 26)
        self._clickable(surface, dots, lambda app: app.object_menu(obj, dots.bottomleft), mouse)
        for dx in (-7, 0, 7):
            pygame.draw.circle(surface, INSPECTOR_TITLE, (dots.centerx + dx, dots.centery), 2)
        room = max_w - dots.width - 8
        name = obj.panel_name()
        if name:
            font = get_font(18)
            surface.blit(font.render(clip_text(obj.type_name(), font, room), True, INSPECTOR_LABEL), (x, y + 4))
            y += 26
            font = get_font(28)
            if obj.can_rename:
                self._clickable(surface, pygame.Rect(x - 4, y - 2, max_w + 8, 34), obj.rename, mouse)
            surface.blit(font.render(clip_text(str(name), font, max_w), True, INSPECTOR_TITLE), (x, y))
            y += 36
        else:
            font = get_font(30)
            surface.blit(font.render(clip_text(obj.type_name(), font, room), True, INSPECTOR_TITLE), (x, y))
            y += 40
        status = obj.panel_status(app)
        if status:
            text, color = status
            font = get_font(19)
            pygame.draw.circle(surface, color, (x + 5, y + 10), 5)
            surface.blit(font.render(clip_text(text, font, max_w - 16), True, color), (x + 16, y))
            y += 26
        y += 4
        pygame.draw.line(surface, INSPECTOR_DIVIDER, (x - INSPECTOR_PAD + 2, y),
                         (x + max_w + INSPECTOR_PAD, y))
        return y + 1

    def _draw_row(self, surface, row, x, y, max_w, mouse, obj):
        """Draw one panel row at y; returns the y for the next one."""
        body, label = get_font(22), get_font(20)
        if isinstance(row, Section):
            return self._draw_section(surface, row, x, y, max_w, mouse, obj)
        if isinstance(row, PortRow):
            return self._draw_port(surface, row, x, y, max_w, mouse)
        if isinstance(row, GiveRow):
            g = row.give
            self._clickable(surface, pygame.Rect(x - 4, y - 2, max_w + 8, 24),
                            lambda app: app.pick_target(row.obj, g.key), mouse)
            return self._label_value(surface, x, y, max_w, g.label, format_value(row.value, g.unit),
                                     label, body, INSPECTOR_VALUE)
        if isinstance(row, Gap):
            return y + row.px
        if isinstance(row, Field):
            lines = wrap_text(str(row.value), body, max_w)
            h = 22 + 24 * len(lines)
            self._clickable(surface, pygame.Rect(x - 4, y - 2, max_w + 8, h + 2), row.on_click, mouse)
            surface.blit(label.render(row.label, True, INSPECTOR_LABEL), (x, y))
            for i, line in enumerate(lines):
                surface.blit(body.render(line, True, row.color or INSPECTOR_VALUE), (x, y + 22 + 24 * i))
            return y + h + 6
        if isinstance(row, Row):
            indent = 14 if row.sub else 0
            self._clickable(surface, pygame.Rect(x - 4, y - 2, max_w + 8, 24), row.on_click, mouse)
            return self._label_value(surface, x + indent, y, max_w - indent, row.label, row.value,
                                     get_font(18) if row.sub else label, body, row.color or INSPECTOR_VALUE)
        if isinstance(row, Stepper):
            surface.blit(label.render(row.label, True, INSPECTOR_LABEL), (x, y + 2))
            text = body.render(row.value, True, INSPECTOR_VALUE)
            size = 22
            up = pygame.Rect(x + max_w - size, y, size, size)
            down = pygame.Rect(up.x - 8 - text.get_width() - 8 - size, y, size, size)
            cy = down.centery
            for rect, enabled, points in (
                    (down, row.down_enabled,
                     [(down.right - 7, cy - 5), (down.right - 7, cy + 5), (down.x + 6, cy)]),
                    (up, row.up_enabled,
                     [(up.x + 7, cy - 5), (up.x + 7, cy + 5), (up.right - 6, cy)])):
                pygame.draw.rect(surface, (70, 70, 88) if enabled else (55, 55, 64),
                                 rect, border_radius=4)
                pygame.draw.polygon(surface, INSPECTOR_TITLE if enabled else (120, 120, 130), points)
            surface.blit(text, text.get_rect(midleft=(down.right + 8, cy)))
            if row.down_enabled:
                self._hot.append((down, row.on_down))
            if row.up_enabled:
                self._hot.append((up, row.on_up))
            return y + 28
        if isinstance(row, ScrollList):
            return self._draw_list(surface, row, x, y, max_w, mouse)
        raise TypeError(f"not an inspector row: {row!r}")

    @staticmethod
    def _label_value(surface, x, y, max_w, label_text, value_text, label_font, value_font, color):
        """A label on the left and a value on the right of one line, sharing the
        width (the value gets at least half when both are long)."""
        label_text, value_text = str(label_text), str(value_text)
        lw, vw = label_font.size(label_text)[0], value_font.size(value_text)[0]
        if lw + vw + 12 > max_w:
            vw = max(max_w - lw - 12, max_w // 2)
            lw = max_w - vw - 12
        surface.blit(label_font.render(clip_text(label_text, label_font, lw), True, INSPECTOR_LABEL),
                     (x, y + 2))
        vs = value_font.render(clip_text(value_text, value_font, vw), True, color)
        surface.blit(vs, (x + max_w - vs.get_width(), y))
        return y + 26

    def _draw_section(self, surface, section, x, y, max_w, mouse, obj):
        """A section title (click: fold / unfold) and, unless folded, its rows."""
        key = (obj.TYPE, section.title)
        folded = self._collapsed.get(key, section.collapsed)
        y += 4
        head = pygame.Rect(x - 4, y, max_w + 8, 24)
        self._clickable(surface, head, lambda app: self._collapsed.__setitem__(key, not folded), mouse)
        font = get_font(17)
        ts = font.render(section.title.upper(), True, INSPECTOR_SECTION)
        surface.blit(ts, ts.get_rect(midleft=(x, head.centery)))
        cx, cy = head.right - 12, head.centery
        points = ([(cx - 2, cy - 5), (cx - 2, cy + 5), (cx + 4, cy)] if folded else
                  [(cx - 5, cy - 2), (cx + 5, cy - 2), (cx, cy + 4)])
        pygame.draw.polygon(surface, INSPECTOR_SECTION, points)
        y = head.bottom + 4
        if not folded:
            for row in section.rows:
                y = self._draw_row(surface, row, x, y, max_w, mouse, obj)
        y += 4
        pygame.draw.line(surface, INSPECTOR_DIVIDER, (x - INSPECTOR_PAD + 2, y), (x + max_w + INSPECTOR_PAD, y))
        return y + 2

    def _draw_port(self, surface, row, x, y, max_w, mouse):
        """One input: its name, then "link" (and its value set by hand) while
        unlinked, or "source · give  x" (green) and the live value under it."""
        obj, take = row.obj, row.take
        body, small = get_font(22), get_font(18)
        link = obj.links.get(take.key) or {}
        src = obj.take_source(take.key)
        line = pygame.Rect(x - 4, y - 2, max_w + 8, 24)
        name = body.render(clip_text(take.label, body, max_w * 0.45), True, INSPECTOR_VALUE)
        if take.on_label is not None:
            self._clickable(surface, pygame.Rect(x - 4, y - 2, name.get_width() + 8, 24), take.on_label, mouse)
        surface.blit(name, (x, y))
        right = x + max_w
        verb = small.render(take.verb or VERBS[take.kind], True, INSPECTOR_LABEL)

        if src is None:
            word = small.render("link", True, INSPECTOR_ACCENT)
            pill = pygame.Rect(0, 0, word.get_width() + 18, 22)
            pill.midright = (right, line.centery)
            self._clickable(surface, pill, lambda app: obj.pick(app, take.key), mouse)
            pygame.draw.rect(surface, INSPECTOR_ACCENT, pill, width=1, border_radius=11)
            surface.blit(word, word.get_rect(center=pill.center))
            if take.current is not None:
                current = take.current()
                text = current if isinstance(current, str) else format_value(current, take.unit)
                room = pill.x - 10 - (x + name.get_width() + 10)
                vs = body.render(clip_text(text, body, max(10, room)), True, INSPECTOR_VALUE)
                vr = vs.get_rect(midright=(pill.x - 10, line.centery))
                self._clickable(surface, vr.inflate(10, 4), take.set_by_hand, mouse)
                surface.blit(vs, vr)
            y += 26
            if link.get("id"):                       # it was linked to something that's gone
                ws = small.render("its source was deleted", True, WARN_COLOR)
                surface.blit(ws, (x + 14, y))
                y += 22
            return y

        xr = pygame.Rect(0, 0, 22, 22)                 # ✕: unlink
        xr.midright = (right + 3, line.centery)
        self._clickable(surface, xr, lambda app: obj.unlink(take.key, app), mouse)
        c = xr.center
        for dx in (-4, 4):
            pygame.draw.line(surface, INSPECTOR_LABEL, (c[0] - dx, c[1] - 4), (c[0] + dx, c[1] + 4), 2)
        room = xr.x - 6 - (x + name.get_width() + 12)
        ks = small.render(clip_text(f" · {link.get('key')}", small, max(10, room // 2)), True, LINK_COLOR)
        ns = small.render(clip_text(short_name(src), small, max(10, room - ks.get_width())),
                          True, LINK_COLOR)
        kr = ks.get_rect(midright=(xr.x - 6, line.centery))
        nr = ns.get_rect(midright=(kr.x, line.centery))
        self._clickable(surface, nr.inflate(6, 4), lambda app: obj.pick(app, take.key), mouse)
        self._clickable(surface, kr.inflate(6, 4), lambda app: app.choose_give(obj, take.key, src), mouse)
        surface.blit(ns, nr)
        surface.blit(ks, kr)
        if line.collidepoint(mouse):
            self._hover_src = src
        y += 24
        value = obj.take(take.key)
        if take.kind == "number":
            text = format_value(value, take.unit)
        else:
            text = "—" if value is None else "on" if value != 0 else "off"
        surface.blit(verb, (x + 14, y + 1))
        vs = small.render(clip_text(text, small, max_w - 14 - verb.get_width() - 10), True, INSPECTOR_VALUE)
        surface.blit(vs, (right - vs.get_width(), y + 1))
        return y + 24

    def _draw_list(self, surface, lst, x, y, max_w, mouse):
        """A titled list, up to SCROLL_LIST_ROWS rows tall; the wheel scrolls it."""
        font = get_font(20)
        surface.blit(font.render(lst.title, True, INSPECTOR_LABEL), (x, y))
        y += 24
        if not lst.items:
            surface.blit(font.render(lst.empty, True, INSPECTOR_VALUE), (x, y))
            return y + 26
        row_h = 22
        visible = min(len(lst.items), SCROLL_LIST_ROWS)
        area = pygame.Rect(x, y, max_w, visible * row_h)
        self._list_rect = area.copy()
        if lst.key is not self._list_key:
            self._list_key, self._list_scroll = lst.key, 0
        self._list_max = max(0, len(lst.items) - visible)
        self._list_scroll = min(self._list_scroll, self._list_max)

        prev_clip = surface.get_clip()
        surface.set_clip(area.clip(prev_clip))
        for i, (name, value, on_click) in enumerate(lst.items[self._list_scroll:self._list_scroll + visible]):
            ry = area.y + i * row_h
            self._clickable(surface, pygame.Rect(area.x - 4, ry - 1, area.width + 8, row_h),
                            on_click, mouse)
            val = font.render(str(value), True, INSPECTOR_VALUE)
            name_w = area.width - val.get_width() - 12
            surface.blit(font.render(clip_text(str(name), font, name_w), True, INSPECTOR_LABEL), (x, ry))
            surface.blit(val, (area.right - val.get_width(), ry))
        surface.set_clip(prev_clip)

        if self._list_max:                         # thin scrollbar beside it
            track = pygame.Rect(area.right + 6, area.y, 3, area.height)
            thumb_h = max(12, track.height * visible // len(lst.items))
            thumb_y = track.y + (track.height - thumb_h) * self._list_scroll // self._list_max
            pygame.draw.rect(surface, (55, 55, 64), track)
            pygame.draw.rect(surface, (130, 130, 145), (track.x, thumb_y, 3, thumb_h))
        return area.bottom + 6


# ---- Build / place modes ----------------------------------------------------
def draw_hint(surface, text):
    """The dark instruction banner under the menu bar while a mode is active."""
    ts = get_font(MENU_FONT_SIZE).render(text, True, (245, 245, 250))
    bg = pygame.Rect(10, MENU_BAR_HEIGHT + 8, ts.get_width() + 16, ts.get_height() + 10)
    overlay = pygame.Surface(bg.size, pygame.SRCALPHA)
    overlay.fill((0, 0, 0, 175))
    surface.blit(overlay, bg.topleft)
    surface.blit(ts, (bg.x + 8, bg.y + 5))


class PlaceMode:
    """Holding a new object to drop (picked from the E menu or Add menu): a
    see-through copy follows the cursor. Click places it there and selects it;
    Shift+click places it and keeps going with another; Esc puts it back.
    Like the build modes, it claims left-click + Esc and lets pan/zoom through."""

    GHOST_ALPHA = 140

    def __init__(self):
        self.factory = None      # factory(x, y) -> a new object at a world point
        self.obj = None          # the one in hand (not in the scene yet)
        self._layer = None

    @property
    def active(self):
        return self.obj is not None

    def begin(self, factory, world):
        self.factory = factory
        self.obj = factory(*world)
        self.follow(world)

    def cancel(self):
        self.__init__()

    def follow(self, world):
        """Move the held object so it's centered on a world point."""
        c = self.obj.center
        self.obj.move(world[0] - c[0], world[1] - c[1])

    def place(self, world, scene, keep_going=False):
        self.follow(world)
        obj = self.obj
        placed = obj.members if isinstance(obj, MachineGroup) else [obj]   # a machine drops all of its objects
        scene.add_many(placed)
        if keep_going and not obj.SINGLETON:
            self.obj = self.factory(*world)
        else:
            self.cancel()
            scene.select_many(placed)
        return obj

    def draw(self, surface, camera):
        if not self.active:
            return
        if self._layer is None or self._layer.get_size() != surface.get_size():
            self._layer = pygame.Surface(surface.get_size(), pygame.SRCALPHA)
        self._layer.fill((0, 0, 0, 0))
        self.obj.draw(self._layer, camera)
        self._layer.set_alpha(self.GHOST_ALPHA)
        surface.blit(self._layer, (0, 0))
        name = self.obj.place_name() if hasattr(self.obj, "place_name") else self.obj.type_name().lower()
        draw_hint(surface, f"Place {name} — click to drop     Shift+click: keep placing     Esc: cancel")


class PickMode:
    """Waiting for a click on an object — e.g. choosing what a readout reads.
    accept(obj) says which objects count; a valid click calls on_pick(obj),
    anything else (or Esc) cancels — except that, given on_empty, a click on
    empty canvas calls on_empty(world point) (a relay places another there).
    Draws a hint, a rubber-band line from `origin` to the cursor, and a ring
    around a pickable object under it (or a small ring on empty canvas)."""

    def __init__(self):
        self.prompt = ""
        self.on_pick = None
        self.accept = None
        self.origin = None       # world point the rubber band starts from, or None
        self.on_empty = None     # on_empty(world point) for a click on empty canvas, or None

    @property
    def active(self):
        return self.on_pick is not None

    def begin(self, prompt, on_pick, accept, origin=None, on_empty=None):
        self.prompt, self.on_pick, self.accept, self.origin = prompt, on_pick, accept, origin
        self.on_empty = on_empty

    def cancel(self):
        self.__init__()

    def target(self, scene, pos, camera):
        obj = scene.hit_test(pos, camera)
        return obj if obj is not None and self.accept(obj) else None

    def click(self, scene, pos, camera):
        """Pick what's under pos (if allowed), then end the mode either way."""
        obj = self.target(scene, pos, camera)
        on_pick, on_empty = self.on_pick, self.on_empty
        empty = on_empty is not None and scene.hit_test(pos, camera) is None
        self.cancel()
        if obj is not None:
            on_pick(obj)
        elif empty:
            on_empty(camera.screen_to_world(pos))

    def draw(self, surface, camera, scene, mouse):
        if not self.active:
            return
        obj = self.target(scene, mouse, camera)
        if self.origin is not None:
            start = camera.world_to_screen(self.origin)
            end = camera.world_to_screen(obj.center) if obj is not None and obj.center is not None else mouse
            pygame.draw.line(surface, (120, 220, 150), start, end, 2)
        if obj is not None and hasattr(obj, "screen_rect"):
            pygame.draw.rect(surface, (120, 220, 150), obj.screen_rect(camera).inflate(14, 14),
                             width=3, border_radius=10)
        elif self.on_empty is not None and scene.hit_test(mouse, camera) is None:
            pygame.draw.circle(surface, (120, 220, 150), mouse, 9, 2)      # a click here places something
        draw_hint(surface, f"{self.prompt}     Esc: cancel")


class LinkDrag:
    """Dragging a wire out of an object's output nub (see give_at). Let go on
    an input chip (take_at) to link that input, or anywhere else on an object
    with inputs to choose one from a menu. Esc cancels."""

    def __init__(self):
        self.src = None
        self.key = None

    @property
    def active(self):
        return self.src is not None

    def begin(self, src, key):
        self.src, self.key = src, key

    def cancel(self):
        self.__init__()

    def target(self, scene, pos, camera):
        obj = scene.hit_test(pos, camera)
        return obj if obj is not None and obj is not self.src and obj.takes() else None

    def finish(self, app, pos):
        src, key = self.src, self.key
        target = self.target(app.scene, pos, app.camera)
        self.cancel()
        if target is not None:
            app.choose_take(target, src, key, port=target.take_at(pos, app.camera))

    def draw(self, surface, camera, scene, mouse):
        if not self.active:
            return
        start = camera.world_to_screen(output_point(self.src))
        pygame.draw.line(surface, LINK_COLOR, start, mouse, 2)
        pygame.draw.circle(surface, LINK_COLOR, mouse, 4)
        target = self.target(scene, mouse, camera)
        if target is not None and hasattr(target, "screen_rect"):
            pygame.draw.rect(surface, LINK_COLOR, target.screen_rect(camera).inflate(14, 14),
                             width=3, border_radius=10)
        draw_hint(surface, f"Drop '{self.key}' on an input     Esc: cancel")


class TrainBuilder:
    """Interactive placement for a Train: click a start node, drop orthogonal
    corners with each click (auto-snapped H/V from the last point), click an end
    node to finish. Non-blocking for the camera — the loop keeps panning/zooming
    while this is active; the builder only claims left-click, mouse-move, Esc and
    Backspace. `on_left_click` returns a finished Train (to be added), or None."""

    def __init__(self):
        self.active = False
        self.start = None        # start node
        self.corners = []        # committed corner points (world)
        self.preview = None      # snapped point under the cursor (world)
        self.hover_node = None   # selectable node under the cursor, if any

    def begin(self):
        self.__init__()
        self.active = True

    def cancel(self):
        self.__init__()

    def _last(self):
        if self.corners:
            return self.corners[-1]
        return pygame.Vector2(self.start.world_center) if self.start else None

    def _snap(self, world):
        """Project the cursor onto a horizontal or vertical segment from the last
        point, whichever axis it moved along more."""
        last = self._last()
        if last is None:
            return None
        if abs(world[0] - last.x) >= abs(world[1] - last.y):
            return pygame.Vector2(world[0], last.y)
        return pygame.Vector2(last.x, world[1])

    def on_mouse_move(self, world, hover_node=None):
        if self.start is not None:
            self.preview = self._snap(world)
        self.hover_node = hover_node

    def on_left_click(self, node, world):
        if self.start is None:
            if node is not None:
                self.start = node
            return None
        if node is not None and node is not self.start:
            return self._finish(node)
        snapped = self._snap(world)
        if snapped is not None:
            self.corners.append(snapped)
        return None

    def undo(self):
        if self.corners:
            self.corners.pop()
        else:
            self.start = None        # back to "click the starting node"

    def _finish(self, end_node):
        corners = list(self.corners)
        last = self._last()
        end = pygame.Vector2(end_node.world_center)
        # If the last point isn't already aligned with the end node, add one
        # elbow so the final segment enters it axis-aligned.
        if last is not None and abs(last.x - end.x) > 0.5 and abs(last.y - end.y) > 0.5:
            # Enter the node along the approach axis, so the final segment (and
            # thus the arrow's side) matches the direction you came from.
            ref = self.preview if self.preview is not None else end
            enter_horizontally = abs(ref.x - last.x) >= abs(ref.y - last.y)
            corners.append(pygame.Vector2(last.x, end.y) if enter_horizontally
                           else pygame.Vector2(end.x, last.y))
        # Simplify with the node centers as anchors, then keep the interior as
        # waypoints — this removes any overhang/backtrack left at the ends.
        start_c = pygame.Vector2(self.start.world_center)
        full = Train._simplify([start_c] + corners + [end])
        return Train(self.start.id, end_node.id, full[1:-1])

    @staticmethod
    def _seg_rects(points, w):
        for (x1, y1), (x2, y2) in zip(points, points[1:]):
            yield pygame.Rect(round(min(x1, x2) - w / 2), round(min(y1, y2) - w / 2),
                              round(abs(x2 - x1) + w), round(abs(y2 - y1) + w))

    def draw(self, surface, camera, scene):
        if not self.active:
            return
        draw_hint(surface, "Add train — click the starting node    (Esc to cancel)"
                  if self.start is None else
                  "Click to drop a corner, or click the ending node     "
                  "Backspace: undo     Esc: cancel")

        if self.start is None:
            return

        # Hovering a candidate end node -> preview the EXACT finished train
        # (edge-attached, with both arrows), so what you see is what you'll get.
        if self.hover_node is not None and self.hover_node is not self.start:
            preview_train = self._finish(self.hover_node)
            preview_train.scene = scene
            preview_train.draw(surface, camera)
            return

        # Otherwise: in-progress route, with the start attached to the node edge
        # (not its center) toward the first point.
        after_start = self.corners[0] if self.corners else self.preview
        start_pt = (Train._attach(pygame.Vector2(self.start.world_center), after_start, self.start)
                    if after_start is not None else pygame.Vector2(self.start.world_center))
        committed = [start_pt] + list(self.corners)
        scr = [tuple(round(v) for v in camera.world_to_screen(p)) for p in committed]
        width = max(1, round(TRAIN_WORLD_WIDTH * camera.zoom))
        for r in self._seg_rects(scr, width):
            pygame.draw.rect(surface, TRAIN_COLOR, r)

        dots = list(committed)
        if self.preview is not None:
            pv = [scr[-1], tuple(round(v) for v in camera.world_to_screen(self.preview))]
            for r in self._seg_rects(pv, width):
                pygame.draw.rect(surface, (255, 240, 130), r)   # lighter = preview
            dots.append(self.preview)
        for p in dots:
            sp = camera.world_to_screen(p)
            pygame.draw.circle(surface, (20, 20, 20),
                               (round(sp[0]), round(sp[1])), max(2, width // 2), 1)


# ---- Wall build mode (drag to draw a box) ----------------------------------
class WallBuilder:
    """Drag-to-draw placement for a Wall: press at one corner, drag to the
    opposite, release. Claims left down/move/up and Esc; wheel-zoom falls
    through. `rect()` returns the world (x, y, w, h) if the drag is big enough."""

    def __init__(self):
        self.active = False
        self.start = None        # world Vector2 of the first corner
        self.current = None      # world Vector2 under the cursor

    def begin(self):
        self.__init__()
        self.active = True

    def cancel(self):
        self.__init__()

    def on_press(self, world):
        self.start = pygame.Vector2(world)
        self.current = pygame.Vector2(world)

    def on_move(self, world):
        if self.start is not None:
            self.current = pygame.Vector2(world)

    def rect(self):
        if self.start is None or self.current is None:
            return None
        x, y = min(self.start.x, self.current.x), min(self.start.y, self.current.y)
        w, h = abs(self.current.x - self.start.x), abs(self.current.y - self.start.y)
        if w < WALL_MIN_WORLD or h < WALL_MIN_WORLD:
            return None
        return (x, y, w, h)

    def draw(self, surface, camera):
        if not self.active:
            return
        draw_hint(surface, "Add wall — drag to draw the box    (Esc to cancel)")

        if self.start is None or self.current is None:
            return
        x, y = min(self.start.x, self.current.x), min(self.start.y, self.current.y)
        w, h = abs(self.current.x - self.start.x), abs(self.current.y - self.start.y)
        tl = camera.world_to_screen((x, y))
        r = pygame.Rect(round(tl[0]), round(tl[1]),
                        max(1, round(w * camera.zoom)), max(1, round(h * camera.zoom)))
        pygame.draw.rect(surface, WALL_COLOR, r,
                         width=max(1, round(WALL_WORLD_BORDER * camera.zoom)), border_radius=8)

# ---- Drag-box (marquee) multi-select ---------------------------------------
MARQUEE_MIN_PX = 5                   # drags shorter than this count as a plain click
MARQUEE_FILL = (255, 210, 60, 40)    # translucent selection yellow
MARQUEE_BORDER = (255, 210, 60)


class MarqueeSelect:
    """Left-drag on empty canvas draws a box; on release everything inside is
    selected. Corners are kept in world space so zooming mid-drag stays put."""

    def __init__(self):
        self.start = None        # world Vector2 where the drag began
        self.current = None      # world Vector2 under the cursor
        self.start_screen = None
        self.dragged = False     # moved far enough to be a real drag?

    @property
    def active(self):
        return self.start is not None

    def begin(self, screen_pos, camera):
        self.start = pygame.Vector2(camera.screen_to_world(screen_pos))
        self.current = pygame.Vector2(self.start)
        self.start_screen = pygame.Vector2(screen_pos)
        self.dragged = False

    def on_move(self, screen_pos, camera):
        if not self.active:
            return
        self.current = pygame.Vector2(camera.screen_to_world(screen_pos))
        if (pygame.Vector2(screen_pos) - self.start_screen).length() >= MARQUEE_MIN_PX:
            self.dragged = True

    def finish(self, scene):
        """End the drag; select what's inside if it was a real drag."""
        if self.active and self.dragged:
            x, y = min(self.start.x, self.current.x), min(self.start.y, self.current.y)
            w, h = abs(self.current.x - self.start.x), abs(self.current.y - self.start.y)
            scene.select_many(scene.objects_in_box(x, y, w, h))
        self.__init__()

    def draw(self, surface, camera):
        if not (self.active and self.dragged):
            return
        a = camera.world_to_screen(self.start)
        b = camera.world_to_screen(self.current)
        r = pygame.Rect(round(min(a[0], b[0])), round(min(a[1], b[1])),
                        max(1, round(abs(b[0] - a[0]))), max(1, round(abs(b[1] - a[1]))))
        fill = pygame.Surface(r.size, pygame.SRCALPHA)
        fill.fill(MARQUEE_FILL)
        surface.blit(fill, r.topleft)
        pygame.draw.rect(surface, MARQUEE_BORDER, r, width=1)


# ---- The E menu: an inventory-style grid of every placeable type ------------
INV_MAX_SIZE = (760, 540)            # window size cap (shrinks to fit the screen)
INV_TILE = (108, 118)                # one tile: icon + name underneath
INV_ICON = 72                        # icon square, px
INV_GAP = 10
INV_TAB_H = 28
INV_TILE_BG = (56, 56, 70)
INV_TILE_HOVER = (74, 74, 94)


class MachineTile:
    """A saved machine's tile in the E menu: stands in for a class there (a name,
    an icon — a miniature of its objects — and create(), which picks it up)."""

    CATEGORY = "Machines"

    def __init__(self, machine):
        self.machine = machine

    def __eq__(self, other):
        return isinstance(other, MachineTile) and self._key() == other._key()

    def __hash__(self):
        return hash(self._key())

    def _key(self):
        return ("machine", self.machine["name"], self.machine.get("created"))

    def type_name(self):
        return self.machine["name"]

    def draw_icon(self, surface):
        draw_machine_preview(self.machine, surface)

    def create(self, app):
        machine = self.machine
        app.start_placing(lambda x, y: MachineGroup(machine, (x, y)))


class InventoryPanel:
    """Press E (or Objects (E) in the menu bar): a window with a tile per
    placeable object type — its icon (drawn by the type's own draw_icon) and
    name. Wheel scrolls; clicking a tile picks that type (the loop then calls
    commit() -> on_pick(cls), which runs cls.create). E / Esc / a click outside
    closes it. Tabs: "All", plus one per CATEGORY that any type sets, plus
    "Machines" (saved groups of objects; right-click one to rename / delete it).

    It's a modal (same handle_event/commit/draw interface as the dialogs), and
    the App keeps one instance, so the tab and scroll position stick around."""

    def __init__(self, on_pick, on_context=None):
        self.on_pick = on_pick
        self.on_context = on_context   # on_context(machine, pos): a right-click on a machine
        self.context = None
        self.tab = "All"
        self.scroll = 0          # px scrolled down in the grid
        self.picked = None
        self._icons = {}         # cls -> rendered icon surface (drawn once)

    # --- Contents ---------------------------------------------------------------
    @staticmethod
    def _all_types():
        return [cls for cls in OBJECT_TYPES.values() if cls.MENU_LABEL]

    def tabs(self):
        cats = []
        for cls in self._all_types():
            if cls.CATEGORY and cls.CATEGORY not in cats:
                cats.append(cls.CATEGORY)
        return ["All"] + cats + ["Machines"]

    def types(self):
        if self.tab not in self.tabs():
            self.tab = "All"
        if self.tab == "Machines":
            return [MachineTile(m) for m in machines()]
        return [cls for cls in self._all_types()
                if self.tab == "All" or cls.CATEGORY == self.tab]

    def _icon(self, cls):
        if cls not in self._icons:
            surf = pygame.Surface((INV_ICON, INV_ICON), pygame.SRCALPHA)
            try:
                cls.draw_icon(surf)
            except Exception:            # a broken icon shouldn't break the menu
                surf.fill((0, 0, 0, 0))
                pygame.draw.rect(surf, (150, 70, 70), surf.get_rect(), width=2, border_radius=6)
                q = get_font(40).render("?", True, (220, 120, 120))
                surf.blit(q, q.get_rect(center=surf.get_rect().center))
            self._icons[cls] = surf
        return self._icons[cls]

    # --- Layout (shared by draw and hit-testing) --------------------------------
    def layout(self, screen_size):
        """(box, tab rects [(rect, name)], grid area, tile rects [(rect, cls)], content height)."""
        w = min(INV_MAX_SIZE[0], screen_size[0] - 40)
        h = min(INV_MAX_SIZE[1], screen_size[1] - MENU_BAR_HEIGHT - 40)
        box = pygame.Rect(0, 0, max(200, w), max(200, h))
        box.center = (screen_size[0] // 2, (screen_size[1] + MENU_BAR_HEIGHT) // 2)

        font = get_font(MENU_FONT_SIZE)
        tabs, tx = [], box.x + 16
        for name in self.tabs():
            r = pygame.Rect(tx, box.y + 48, font.size(name)[0] + 24, INV_TAB_H)
            tabs.append((r, name))
            tx = r.right + 6

        area = pygame.Rect(box.x + 16, box.y + 48 + INV_TAB_H + 10,
                           box.width - 32, box.bottom - 34 - (box.y + 48 + INV_TAB_H + 10))
        tw, th = INV_TILE
        cols = max(1, (area.width + INV_GAP) // (tw + INV_GAP))
        left = area.x + (area.width - (cols * tw + (cols - 1) * INV_GAP)) // 2   # center the grid
        types = self.types()
        rows = -(-len(types) // cols)
        content_h = max(0, rows * th + (rows - 1) * INV_GAP)
        self.scroll = max(0, min(self.scroll, content_h - area.height))
        tiles = []
        for i, cls in enumerate(types):
            row, col = divmod(i, cols)
            tiles.append((pygame.Rect(left + col * (tw + INV_GAP),
                                      area.y + row * (th + INV_GAP) - self.scroll, tw, th), cls))
        return box, tabs, area, tiles, content_h

    # --- Modal interface --------------------------------------------------------
    def handle_event(self, event, screen_size):
        if event.type == pygame.KEYDOWN and event.key in (pygame.K_e, pygame.K_ESCAPE):
            return "cancel"
        if event.type == pygame.MOUSEWHEEL:
            self.scroll = max(0, self.scroll - event.y * 40)     # clamped by layout()
            return None
        if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            box, tabs, area, tiles, _h = self.layout(screen_size)
            if not box.collidepoint(event.pos):
                return "cancel"
            for rect, name in tabs:
                if rect.collidepoint(event.pos):
                    self.tab, self.scroll = name, 0
                    return None
            if area.collidepoint(event.pos):
                for rect, cls in tiles:
                    if rect.collidepoint(event.pos):
                        self.picked = cls
                        return "submit"
        if event.type == pygame.MOUSEBUTTONDOWN and event.button == 3 and self.on_context is not None:
            _box, _tabs, area, tiles, _h = self.layout(screen_size)
            if area.collidepoint(event.pos):
                for rect, tile in tiles:
                    if isinstance(tile, MachineTile) and rect.collidepoint(event.pos):
                        self.context = (tile.machine, event.pos)
                        return "submit"
        return None

    def commit(self):
        if self.context is not None:
            (machine, pos), self.context = self.context, None
            self.on_context(machine, pos)
            return
        cls, self.picked = self.picked, None
        if cls is not None:
            self.on_pick(cls)

    def draw(self, surface):
        box, tabs, area, tiles, content_h = self.layout(surface.get_size())
        _draw_modal_frame(surface, box)
        font = get_font(MENU_FONT_SIZE)
        surface.blit(get_font(30).render("Objects", True, INSPECTOR_TITLE), (box.x + 16, box.y + 14))
        text = ("Click to pick up     Right-click: rename / delete     E / Esc: close"
                if self.tab == "Machines" else "Click to pick up     Wheel: scroll     E / Esc: close")
        hint = get_font(18).render(text, True, (150, 150, 160))
        surface.blit(hint, (box.x + 16, box.bottom - 24))

        for rect, name in tabs:
            active = name == self.tab
            pygame.draw.rect(surface, (70, 70, 88) if active else (50, 50, 60), rect, border_radius=5)
            ts = font.render(name, True, INSPECTOR_TITLE if active else INSPECTOR_LABEL)
            surface.blit(ts, ts.get_rect(center=rect.center))

        mouse = pygame.mouse.get_pos()
        prev_clip = surface.get_clip()
        surface.set_clip(area)
        name_font = get_font(19)
        for rect, cls in tiles:
            if rect.bottom < area.top or rect.top > area.bottom:
                continue                                     # scrolled out of view
            hovered = rect.collidepoint(mouse) and area.collidepoint(mouse)
            pygame.draw.rect(surface, INV_TILE_HOVER if hovered else INV_TILE_BG, rect, border_radius=8)
            if hovered:
                pygame.draw.rect(surface, SELECTION_COLOR, rect, width=2, border_radius=8)
            icon = self._icon(cls)
            surface.blit(icon, icon.get_rect(midtop=(rect.centerx, rect.y + 10)))
            name = clip_text(cls.type_name(), name_font, rect.width - 10)
            ns = name_font.render(name, True, INSPECTOR_VALUE)
            surface.blit(ns, ns.get_rect(midbottom=(rect.centerx, rect.bottom - 8)))
        surface.set_clip(prev_clip)

        if not tiles:
            text = ("— no machines yet: select some objects, right-click ▸ Create machine…"
                    if self.tab == "Machines" else "— nothing here yet")
            es = font.render(clip_text(text, font, area.width), True, INSPECTOR_LABEL)
            surface.blit(es, (area.x, area.y))
        elif content_h > area.height:                        # scrollbar
            track = pygame.Rect(box.right - 10, area.y, 4, area.height)
            thumb_h = max(20, area.height * area.height // content_h)
            thumb_y = track.y + (area.height - thumb_h) * self.scroll // (content_h - area.height)
            pygame.draw.rect(surface, (55, 55, 64), track, border_radius=2)
            pygame.draw.rect(surface, (130, 130, 145), (track.x, thumb_y, 4, thumb_h), border_radius=2)


# ============================================================================
# APP — the handle every object hook, inspector callback and action receives
# ============================================================================
class App:
    """What object code gets handed (as `app`): the scene, camera and screen
    size, plus the ways an object can reach the UI — ask for text, confirm,
    place a new object, start a build mode, save. The game loop owns one and
    shows/clears `modal`. Also the context ACTIONS run with (.scene, .camera,
    .screen_size)."""

    def __init__(self, scene, screen_size):
        self.scene = scene
        self.camera = scene.camera
        self.screen_size = screen_size      # the loop keeps this current
        self.modal = None                   # the open dialog / E menu, or None
        self.train_builder = TrainBuilder()
        self.wall_builder = WallBuilder()
        self.placer = PlaceMode()
        self.picker = PickMode()
        self.link_drag = LinkDrag()
        self.inventory = InventoryPanel(on_pick=lambda cls: cls.create(self),
                                        on_context=lambda machine, pos: self.machine_menu(machine, pos))

    @property
    def building(self):
        """True while a mode (train / wall / placing) is claiming the mouse."""
        return (self.train_builder.active or self.wall_builder.active
                or self.placer.active or self.picker.active or self.link_drag.active)

    def cancel_modes(self):
        self.train_builder.cancel()
        self.wall_builder.cancel()
        self.placer.cancel()
        self.picker.cancel()
        self.link_drag.cancel()

    def start_picking(self, prompt, on_pick, accept=lambda obj: True, origin=None, on_empty=None):
        """Wait for a click on an object that accept(obj) allows, then call
        on_pick(obj). Esc or a click elsewhere cancels — or, with on_empty, a
        click on empty canvas calls on_empty(world point). `origin` (a world
        point) draws a line from there to the cursor while picking."""
        self.cancel_modes()
        self.picker.begin(prompt, on_pick, accept, origin, on_empty)

    def open_inventory(self):
        """Show the E menu (dropping whatever mode was active)."""
        self.cancel_modes()
        self.modal = self.inventory

    def start_placing(self, factory):
        """Pick up a new object to drop with the mouse; factory(x, y) builds one
        at a world point. (The default for CanvasObject.create.)"""
        self.cancel_modes()
        self.scene.select_only(None)
        self.placer.begin(factory, self.camera.screen_to_world(pygame.mouse.get_pos()))

    def center_world(self):
        """The world point at the center of the screen."""
        w, h = self.screen_size
        return self.camera.screen_to_world((w / 2, h / 2))

    def place(self, obj):
        """Add a new object to the scene (saved) and select it."""
        self.scene.add(obj)
        self.scene.select_only(obj)

    def save(self):
        self.scene.save()

    def ask(self, prompt, on_submit, *, value="", q_pastes=True):
        """Open a text box; on Enter, on_submit(text) is called. Esc cancels.
        q_pastes=False when the user needs to type a 'q' (e.g. 'sqrt')."""
        self.modal = TextInputModal(prompt, on_submit, value=value, q_pastes=q_pastes)

    def confirm(self, message, sub, on_confirm, ok_label="Delete"):
        """Open a Cancel / <ok_label> dialog; on_confirm() runs if they confirm."""
        self.modal = ConfirmModal(message, sub, on_confirm, ok_label)

    def context_menu(self, pos, items):
        """Open a right-click menu at screen pos; items: [(label, callback, style)]."""
        self.modal = ContextMenu(pos, items)

    # --- Linking: an input (take) of one object follows an output (give) of another --
    def pick_link(self, obj, port, *, on_empty=None, note=""):
        """Link obj's input `port`: click a source, then choose which of its outputs
        (a menu, if it has several). Clicking obj itself unlinks it. on_empty /
        note: what a click on empty canvas does, and the hint saying so."""
        take = obj.find_take(port)
        label = take.label if take is not None else port
        linked = obj.take_source(port) is not None
        hint = f" — or click this {obj.type_name().lower()} to unlink" if linked else ""

        def picked(src):
            if src is obj:
                obj.unlink(port, self)
            else:
                self.choose_give(obj, port, src)
        self.start_picking(f"Click what '{label}' takes from{hint}{note}", picked,
                           accept=lambda o: (o is obj and linked) or (o is not obj and bool(o.gives())),
                           origin=obj.input_point(port), on_empty=on_empty)

    def choose_give(self, obj, port, src):
        """Link obj's input `port` to one of src's outputs: right away if it has
        one, else from a menu listing each with its live value (the likeliest first)."""
        gives = src.gives()
        if not gives:
            return
        if len(gives) == 1:
            obj.link(port, src, gives[0].key, self)
            return
        take = obj.find_take(port)
        prefer = ("value",) if take is None or take.kind == "number" else ("pressed", "on", "value")
        gives = sorted(gives, key=lambda g: prefer.index(g.key) if g.key in prefer else len(prefer))
        values = src.outputs()
        label = take.label if take is not None else port
        items = [(f"'{label}' takes from {short_name(src)}:", None, "title")]
        items += [(g.label, lambda g=g: obj.link(port, src, g.key, self), None,
                   format_value(values.get(g.key), g.unit)) for g in gives]
        self.context_menu(pygame.mouse.get_pos(), items)

    def pick_target(self, src, key):
        """The other way round: link src's output `key` into an input of an
        object you click (a menu, if it has several inputs)."""
        def picked(target):
            self.choose_take(target, src, key)
        self.start_picking(f"Click what should take '{key}' from this {src.type_name().lower()}", picked,
                           accept=lambda o: o is not src and bool(o.takes()), origin=output_point(src))

    def choose_take(self, target, src, key, port=None):
        """Link src's output `key` into target's input `port`, or into one picked
        from a menu of target's inputs (right away if it has just one)."""
        takes = target.takes()
        if port is not None or len(takes) == 1:
            target.link(port or takes[0].key, src, key, self)
            return
        if not takes:
            return
        items = [(f"Which input of {short_name(target)} takes '{key}'?", None, "title")]
        for t in takes:
            cur = target.take_source(t.key)
            items.append((t.label, lambda t=t: target.link(t.key, src, key, self), None,
                          f"from {short_name(cur)}" if cur is not None else "unlinked"))
        self.context_menu(pygame.mouse.get_pos(), items)

    # --- The object menu (⋯ in the panel, or a right-click) -----------------------------
    def object_menu(self, obj, pos):
        """The menu for one object: footer buttons that didn't fit, its type's own
        items, then Rename / Copy / Go to / Unlink all / Create machine / Delete."""
        _footer, overflow = split_actions(obj.actions(self))
        items = [(b.label, lambda b=b: b.on_click(self), "danger" if b.style == "stop" else None)
                 for b in overflow if b.enabled]
        items += obj.menu_items(self)
        items += self.selection_menu_items([obj])
        self.context_menu(pos, items)

    def selection_menu_items(self, objs):
        """Menu items for the selection (one object or several)."""
        items = []
        if len(objs) == 1:
            obj = objs[0]
            if obj.can_rename:
                items.append(("Rename…", lambda: obj.rename(self), None))
            if obj.copyable:
                items.append(("Copy", lambda: self.copy(obj), None))
            if obj.center is not None:
                items.append(("Go to", lambda: self.go_to(obj), None))
            if obj.links:
                items.append(("Unlink all inputs", lambda: obj.unlink_all(self), None))
        if len([o for o in objs if o.movable and o.center is not None]) > 1:
            items.append(("Align horizontally (same X)", lambda: self.align(objs, 0), None))
            items.append(("Align vertically (same Y)", lambda: self.align(objs, 1), None))
        items.append(("Create machine…", lambda: self.create_machine(objs), None))
        label = f"Delete '{objs[0].display_name}'" if len(objs) == 1 else f"Delete {len(objs)} objects"
        items.append((label, lambda: self.delete_objects(objs), "danger"))
        return items

    def align(self, objs, axis):
        """Line up the movable objects' centers on one axis (0: same X, so they
        stand in a column; 1: same Y, a row), at their average. The other axis
        is left alone."""
        movers = [o for o in objs if o.movable and o.center is not None]
        if len(movers) < 2:
            return
        target = sum(o.center[axis] for o in movers) / len(movers)
        for o in movers:
            delta = target - o.center[axis]
            if axis == 0:
                o.move(delta, 0)
            else:
                o.move(0, delta)
        self.save()

    def delete_objects(self, objs):
        """Delete objects after one confirm."""
        if not objs:
            return
        title = f"Delete '{objs[0].display_name}'?" if len(objs) == 1 else f"Delete {len(objs)} objects?"
        self.confirm(title, "Are you sure you want to delete?", lambda: self.scene.delete_many(objs))

    def copy(self, obj, at=None):
        """Drop a copy of obj (at a world point, or nudged beside it) and select it."""
        new = copy_object(obj, at=at)
        if new is not None:
            self.place(new)

    def go_to(self, obj):
        if obj is not None and obj.center is not None:
            self.camera.look_at(obj.center, GOTO_ZOOM, self.screen_size)

    def create_machine(self, objs):
        """Name the objects as a machine and save it (E menu ▸ Machines)."""
        kept, skipped = machine_parts(objs)
        if not kept:
            self.confirm("Can't make a machine from these",
                         "Trains and one-only objects (the instrument server) can't go in one.",
                         lambda: None, ok_label="OK")
            return

        def named(text):
            machine = save_machine(text, kept)
            note = f"{len(kept)} object{'' if len(kept) == 1 else 's'} — place it from the E menu ▸ Machines."
            if skipped:
                kinds = sorted({o.type_name().lower() for o in skipped})
                note += f" Left out {len(skipped)}: {', '.join(kinds)} can't go in a machine."
            self.confirm(f"Saved machine '{machine['name']}'", note, lambda: None, ok_label="OK")
        self.ask(f"Name for this machine ({len(kept)} object{'' if len(kept) == 1 else 's'}):", named)

    def machine_menu(self, machine, pos):
        """Right-click on a machine in the E menu: rename or delete it (then back to the menu)."""
        def rename():
            def submit(text):
                rename_machine(machine, text)
                self.open_inventory()
            self.ask("Machine name:", submit, value=machine["name"])

        def delete():
            def gone():
                delete_machine(machine)
                self.open_inventory()
            self.confirm(f"Delete machine '{machine['name']}'?",
                         "The saved machine only — objects already placed stay.", gone)
        self.context_menu(pos, [("Rename…", rename, None), (f"Delete '{machine['name']}'", delete, "danger")])
