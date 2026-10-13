"""canvas.py — everything about the world and the app.

This holds all the *classes* (canvas objects, camera, scene, UI) and the helper
functions that serve them (fonts, actions, persistence). If it's substantive app
logic, it lives here — game_loop.py is only the thin runner that wires this together.

Section order is top-to-bottom by dependency, so nothing references something defined
below it. game_loop.py imports from here; this file never imports from game_loop.py
(that would be a circular import).
"""

import ast
import ctypes
import json
import math
import os
import random
import re
import subprocess
import uuid

import pygame


# ============================================================================
# CONFIG — constants shared across the app (colors, sizes, zoom, timings, speeds)
# ============================================================================
# Anchor all file paths here, to this file's own folder — never bare relative
# paths (they resolve against the process's working dir, not the script).
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
NODES_FILE = os.path.join(SCRIPT_DIR, "Nodes.json")

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


# ============================================================================
# FONTS — cached pygame fonts so per-frame re-rendering doesn't rebuild them
# ============================================================================
_font_cache = {}


def get_font(size):
    size = max(1, int(size))
    if size not in _font_cache:
        _font_cache[size] = pygame.font.SysFont(None, size)
    return _font_cache[size]


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
# CANVAS OBJECTS — the thin master class + the object families
# ============================================================================
# The master imposes only the shared "socket": id, TYPE, draw_layer, draw(),
# hit(), to_dict(). Everything else lives on the subclass.
#
#   CanvasObject (thin master)
#   └── Node        → point-launcher family (world_center, action/params, select)
#       ├── Button
#       └── NameTag


class CanvasObject:
    """The thin contract every drawable object exposes to the canvas: an id, a
    type string, a draw order, and the ability to draw itself, be hit-tested,
    and serialize. Subclasses add whatever else they need."""

    TYPE = None        # each concrete class sets this; keys the type registry
    draw_layer = 0     # paint order (higher = on top)
    selectable = False
    movable = False    # can arrow keys move it? (see move())
    scene = None       # back-ref set by Scene, so connectors can resolve nodes
    is_selected = False

    def __init__(self, id=None):
        self.id = id or new_id()

    @property
    def display_name(self):
        return self.TYPE or "object"

    def move(self, dx, dy):
        """Shift by a world-space delta (only called when movable)."""
        pass

    def update(self, mouse_pos, mouse_pressed, camera, dt):
        pass

    def hit(self, pos, camera):
        return False

    def on_click(self, pos, ctx):
        """Object-specific click behavior. Return True if the click was handled
        (so the loop skips normal selection). Default: not handled."""
        return False

    def draw(self, surface, camera):
        raise NotImplementedError

    def to_dict(self):
        raise NotImplementedError


def draw_box(surface, rect, text, font, *, base_color, hover_color, click_color,
             border_color, text_color, is_selected, is_hovered, is_clicked):
    """Shared boxed-face rendering for Button/NameTag: selection ring → border →
    state-colored face → centered text. The one place that layering lives."""
    if is_selected:
        pygame.draw.rect(surface, SELECTION_COLOR, rect.inflate(14, 14),
                         width=3, border_radius=10)

    pygame.draw.rect(surface, border_color, rect.inflate(6, 6), border_radius=8)

    if is_clicked:
        face_color, inner_rect = click_color, rect.inflate(-4, -4)
    elif is_hovered:
        face_color, inner_rect = hover_color, rect
    else:
        face_color, inner_rect = base_color, rect
    pygame.draw.rect(surface, face_color, inner_rect, border_radius=6)

    text_surf = font.render(text, True, text_color)
    surface.blit(text_surf, text_surf.get_rect(center=inner_rect.center))


class Node(CanvasObject):
    """Point-anchored family: has a single world_center, is selectable and
    movable, and carries an optional action + params describing what it does."""

    selectable = True
    movable = True

    def __init__(self, x, y, text, *, id=None, action=None, params=None,
                 description=""):
        super().__init__(id=id)
        self.world_center = pygame.Vector2(x, y)  # x, y are the CENTER
        self._text = text
        self.action = action
        self.params = params or {}
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
        return self.text

    def screen_rect(self, camera):
        raise NotImplementedError

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
        d = {"type": self.TYPE, "id": self.id, "name": self.text,
             "x": self.world_center.x, "y": self.world_center.y}
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
    draw_layer = 0

    def __init__(self, x, y, text, width=NODE_WIDTH, height=NODE_HEIGHT, **kwargs):
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
            pygame.draw.rect(surface, BUTTON_BORDER, rect)
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
    draw_layer = 1

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
    draw_layer = -1
    selectable = True     # click its line to select it (then Edit ▸ Delete Node)

    def __init__(self, from_id, to_id, waypoints=None, *, id=None, speed=None):
        super().__init__(id=id)
        self.from_id = from_id
        self.to_id = to_id
        self.waypoints = [pygame.Vector2(p) for p in (waypoints or [])]
        self.speed = float(speed) if speed else TRAIN_DEFAULT_SPEED

    def _endpoints(self):
        s = self.scene
        return ((s.by_id.get(self.from_id), s.by_id.get(self.to_id))
                if s else (None, None))

    def world_points(self):
        """[from-node center, *waypoints, to-node center], or [] if an endpoint
        node is missing."""
        a, b = self._endpoints()
        if a is None or b is None:
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
                pygame.draw.rect(surface, TRAIN_SELECT_COLOR, r)
        for r in self._segment_rects(camera):
            pygame.draw.rect(surface, TRAIN_COLOR, r)
        for rect, direction, _t in self._arrows(camera):
            self._draw_arrow(surface, rect, direction)

    @staticmethod
    def _draw_arrow(surface, rect, direction):
        if rect.width < ARROW_MIN_PX:
            return
        pygame.draw.rect(surface, TRAIN_COLOR, rect)
        pygame.draw.rect(surface, ARROW_FILL, rect, width=max(1, rect.width // 12))

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
        pygame.draw.rect(surface, ARROW_FILL, shaft)
        pygame.draw.polygon(surface, ARROW_FILL, head)

    def to_dict(self):
        return {"type": self.TYPE, "id": self.id,
                "from_id": self.from_id, "to_id": self.to_id,
                "waypoints": [[w.x, w.y] for w in self.waypoints],
                "speed": self.speed}

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
    draw_layer = -2
    selectable = True
    movable = True

    def __init__(self, x, y, w, h, name="", *, id=None, color=None):
        super().__init__(id=id)
        self.x, self.y, self.w, self.h = x, y, w, h
        self._text = name
        self.color = tuple(color) if color else WALL_COLOR

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
            pygame.draw.rect(surface, WALL_SELECT_COLOR,
                             r.inflate(border * 2, border * 2),
                             width=max(1, border), border_radius=8)
        pygame.draw.rect(surface, self.color, r, width=border, border_radius=8)

        title = self._title_rect(camera)
        if title is not None:
            pygame.draw.rect(surface, (45, 45, 55), title, border_radius=6)
            pygame.draw.rect(surface, self.color, title, width=2, border_radius=6)
            ts = get_font(WALL_TITLE_FONT_SIZE).render(self._text, True, self.color)
            surface.blit(ts, ts.get_rect(center=title.center))

    def to_dict(self):
        return {"type": self.TYPE, "id": self.id, "name": self._text,
                "x": self.x, "y": self.y, "w": self.w, "h": self.h,
                "color": list(self.color)}

    @classmethod
    def from_dict(cls, d):
        return cls(d["x"], d["y"], d["w"], d["h"], d.get("name", ""),
                   id=d.get("id"), color=d.get("color"))


# ---- Agent: a little critter that wanders inside its wall ------------------
AGENT_W, AGENT_H = 74, 56           # world size of the body
AGENT_COLOR = (240, 130, 30)        # orange
AGENT_EYE = (12, 12, 12)
AGENT_SPEED = 70                    # world units/sec while walking
AGENT_PAUSE = (0.4, 1.8)           # random pause range (seconds)
AGENT_MARGIN = 24                  # keep this far inside the wall's border


class Agent(Node):
    """A point object that idly wanders — walk to a random spot, pause, repeat —
    staying inside whatever Wall contains it (or a small area if it's in none).
    Just for looks right now; real behaviour can come later. It's a Node, so it's
    selectable/movable/deletable; while selected it holds still so you can grab it."""

    TYPE = "agent"
    draw_layer = 0                 # on top of walls, under name tags

    def __init__(self, x, y, text="", *, id=None, **kwargs):
        super().__init__(x, y, text, id=id, **kwargs)
        self.world_width, self.world_height = AGENT_W, AGENT_H
        self._home = pygame.Vector2(x, y)   # fixed anchor for wandering (moves with move())
        self._target = pygame.Vector2(self.world_center)
        self._state = "pause"
        self._pause = random.uniform(*AGENT_PAUSE)

    def move(self, dx, dy):
        super().move(dx, dy)                # shift the body
        self._home.x += dx                  # and its home, so it wanders where you drop it
        self._home.y += dy

    @property
    def display_name(self):
        return self._text or "agent"

    def screen_rect(self, camera):
        return camera.world_rect_to_screen(self.world_center, self.world_width, self.world_height)

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

    def update(self, mouse_pos, mouse_pressed, camera, dt):
        super().update(mouse_pos, mouse_pressed, camera, dt)   # hover/click
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

    def draw(self, surface, camera):
        rect = self.screen_rect(camera)
        radius = max(2, rect.width // 8)
        if self.is_selected:
            pygame.draw.rect(surface, SELECTION_COLOR, rect.inflate(12, 12),
                             width=3, border_radius=radius + 4)
        pygame.draw.rect(surface, AGENT_COLOR, rect, border_radius=radius)
        ew, eh = max(2, rect.width // 6), max(2, rect.height // 3)
        ey = rect.centery - rect.height // 14
        for ex in (rect.centerx - rect.width // 6, rect.centerx + rect.width // 6):
            eye = pygame.Rect(0, 0, ew, eh)
            eye.center = (ex, ey)
            pygame.draw.ellipse(surface, AGENT_EYE, eye)

    def to_dict(self):
        return {"type": self.TYPE, "id": self.id,
                "x": self.world_center.x, "y": self.world_center.y}

    @classmethod
    def from_dict(cls, d):
        return cls(d["x"], d["y"], d.get("name", ""), id=d.get("id"))


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

    @property
    def display_name(self):
        return self._text or "neuron"

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

    def update(self, mouse_pos, mouse_pressed, camera, dt):
        Node.update(self, mouse_pos, mouse_pressed, camera, dt)   # hover/click only, no wandering
        if self._fire_frames > 0:                # count down the current spike, one per frame
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

    def draw(self, surface, camera):
        rect = self.screen_rect(camera)
        radius = max(2, rect.width // 8)
        if self.is_selected:
            pygame.draw.rect(surface, SELECTION_COLOR, rect.inflate(12, 12),
                             width=3, border_radius=radius + 4)
        body = NEURON_FIRE_COLOR if self.is_firing else NEURON_COLOR
        pygame.draw.rect(surface, body, rect, border_radius=radius)
        spot = pygame.Rect(0, 0, max(2, rect.width // 6), max(2, rect.height // 3))
        spot.center = rect.center                      # same proportions as an agent eye
        pygame.draw.ellipse(surface, NEURON_SPOT, spot)

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
    so no pair is drawn twice). That's n(n-1)/2 lines, so the result is cached
    on a layer and only redrawn when a neuron's on-screen spot (pan, zoom,
    move, add/delete) or the window size changes. Drawn before the objects so
    the lines sit underneath the neurons."""

    def __init__(self):
        self._key = None
        self._layer = None

    def draw(self, surface, camera, neurons):
        if len(neurons) < 2:
            return
        points = [(round(p[0]), round(p[1]))
                  for p in (camera.world_to_screen(n.world_center) for n in neurons)]
        key = (surface.get_size(), tuple(points))
        if key != self._key:
            if self._layer is None or self._layer.get_size() != surface.get_size():
                self._layer = pygame.Surface(surface.get_size())
                self._layer.set_colorkey((0, 0, 0))     # black = see-through
            self._layer.fill((0, 0, 0))
            for i, a in enumerate(points):
                for b in points[i + 1:]:
                    pygame.draw.line(self._layer, NEURON_LINK_COLOR, a, b)
            self._key = key
        surface.blit(self._layer, (0, 0))


# ---- Type registry: maps a "type" string to its class ----------------------
# The place-menu reads this ("what can I drop?") and the loader reads it
# (JSON "type" -> which class). Register a class and it's placeable + loadable
# with no changes to the loop.
OBJECT_TYPES = {
    Button.TYPE: Button,
    NameTag.TYPE: NameTag,
    Train.TYPE: Train,
    Wall.TYPE: Wall,
    Agent.TYPE: Agent,
    Neuron.TYPE: Neuron,
}


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


class ActionContext:
    """Handed to an action so it can reach the app: camera (for internal actions
    like goto), scene (for id lookups), and the current screen size."""

    def __init__(self, scene, camera, screen_size):
        self.scene = scene
        self.camera = camera
        self.screen_size = screen_size


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


def _object_from_dict(d):
    # Missing type defaults to button (old data written before types existed).
    cls = OBJECT_TYPES.get(d.get("type", "button"), Button)
    return cls.from_dict(d)


def load_objects(path=NODES_FILE):
    """Return a list of live canvas objects. Reads the new {"objects": [...]}
    shape and the legacy {"nodes": [...]} shape; backfills ids and rewrites the
    file once if it was in the old shape or missing ids."""
    if not os.path.exists(path):
        objects = [_object_from_dict(d) for d in DEFAULT_OBJECTS]
        save_objects(objects, path)
        return objects

    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    raw = data.get("objects")
    legacy = raw is None
    if legacy:
        raw = data.get("nodes", [])

    objects = [_object_from_dict(d) for d in raw]

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
        """What a drag-box over world rect (x, y, w, h) selects: nodes whose
        center is inside it, and walls entirely inside it."""
        found = []
        for o in self.objects:
            if isinstance(o, Node):
                c = o.world_center
                if x <= c.x <= x + w and y <= c.y <= y + h:
                    found.append(o)
            elif isinstance(o, Wall):
                if x <= o.x and y <= o.y and o.x + o.w <= x + w and o.y + o.h <= y + h:
                    found.append(o)
        return found

    def add(self, obj):
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
                removed = True
        if removed:
            self.sync_weights()
            self.save()

    def update(self, mouse_pos, mouse_pressed, camera, dt):
        for o in self.objects:
            o.update(mouse_pos, mouse_pressed, camera, dt)

    def draw(self, surface, camera):
        self.mesh.draw(surface, camera, self.neurons)
        for o in sorted(self.objects, key=lambda o: o.draw_layer):
            o.draw(surface, camera)


# ---- Object construction helpers -------------------------------------------
def spawn_button(text, camera, screen_size):
    """A new Button centered on whatever the camera is currently framing."""
    cx, cy = camera.screen_to_world((screen_size[0] / 2, screen_size[1] / 2))
    return Button(cx, cy, text.strip() or "New Button")


def spawn_nametag(text, camera, screen_size):
    cx, cy = camera.screen_to_world((screen_size[0] / 2, screen_size[1] / 2))
    return NameTag(cx, cy, text.strip() or "Label")


def spawn_agent(camera, screen_size):
    cx, cy = camera.screen_to_world((screen_size[0] / 2, screen_size[1] / 2))
    return Agent(cx, cy)


def spawn_neuron(camera, screen_size):
    cx, cy = camera.screen_to_world((screen_size[0] / 2, screen_size[1] / 2))
    return Neuron(cx, cy)


COPY_OFFSET = 40   # world units to nudge a copy so it doesn't sit exactly on top


def copy_object(obj, at=None):
    """Duplicate a Button/NameTag/Wall (new id, carrying its data — command,
    name, size, etc.). With `at` (a world point) the copy is centered there;
    otherwise it's nudged over so it's visible. Returns None for types that
    can't be sensibly copied (e.g. a Train, which is defined by two node ids)."""
    if not isinstance(obj, (Node, Wall)):
        return None
    data = dict(obj.to_dict())
    data.pop("id", None)                       # from_dict assigns a fresh id
    data.pop("weights", None)                  # a copied neuron starts with fresh (0) W's
    new = OBJECT_TYPES[data["type"]].from_dict(data)
    if at is None:
        new.move(COPY_OFFSET, COPY_OFFSET)
    else:
        if isinstance(new, Wall):
            cx, cy = new.x + new.w / 2, new.y + new.h / 2
        else:
            cx, cy = new.world_center.x, new.world_center.y
        new.move(at[0] - cx, at[1] - cy)
    return new


# ============================================================================
# UI — hand-rolled chrome: menu bar, modals  (inspector side panel: TODO)
# ============================================================================
# pygame/SDL has no native menu bar, so the top strip and the modal dialogs are
# drawn and hit-tested by hand. The prototype's scattered *_active booleans are
# consolidated here into modal objects; the loop keeps a single `active_modal`.

MENU_BAR_HEIGHT = 28
MENU_FONT_SIZE = 22
MENU_BG = (40, 40, 48)
MENU_HOVER = (60, 60, 72)
MENU_BORDER = (90, 90, 100)
MENU_TEXT = (230, 230, 235)


class MenuBar:
    """The top strip: Add / Edit dropdowns + a flat Go To action. Owns its own
    layout, open/closed state, and hit-testing. `handle_click(pos)` returns
    (consumed, command): `consumed` tells the loop to skip canvas selection for
    this click; `command` (or None) is the chosen action to dispatch."""

    def __init__(self):
        self.open_menu = None  # None / "add" / "edit"
        h = MENU_BAR_HEIGHT
        self.add_rect = pygame.Rect(0, 0, 70, h)
        self.edit_rect = pygame.Rect(self.add_rect.right, 0, 70, h)
        self.goto_rect = pygame.Rect(self.edit_rect.right, 0, 80, h)
        # dropdown items: (rect, label, command)
        self.items = {
            "add": [
                (pygame.Rect(self.add_rect.left, h, 150, h), "Add node", "add_button"),
                (pygame.Rect(self.add_rect.left, h * 2, 150, h), "Add name tag", "add_nametag"),
                (pygame.Rect(self.add_rect.left, h * 3, 150, h), "Add train", "add_train"),
                (pygame.Rect(self.add_rect.left, h * 4, 150, h), "Add wall", "add_wall"),
                (pygame.Rect(self.add_rect.left, h * 5, 150, h), "Add agent", "add_agent"),
                (pygame.Rect(self.add_rect.left, h * 6, 150, h), "Add neuron", "add_neuron"),
            ],
            "edit": [
                (pygame.Rect(self.edit_rect.left, h, 150, h), "Edit Name", "edit_name"),
                (pygame.Rect(self.edit_rect.left, h * 2, 150, h), "Copy", "copy"),
                (pygame.Rect(self.edit_rect.left, h * 3, 150, h), "Delete Node", "delete_node"),
            ],
        }

    def handle_click(self, pos):
        if self.add_rect.collidepoint(pos):
            self.open_menu = None if self.open_menu == "add" else "add"
            return True, None
        if self.edit_rect.collidepoint(pos):
            self.open_menu = None if self.open_menu == "edit" else "edit"
            return True, None
        if self.goto_rect.collidepoint(pos):
            self.open_menu = None
            return True, "goto"
        if self.open_menu:
            for rect, _label, command in self.items[self.open_menu]:
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
                                 (self.goto_rect, "Go To", "goto")):
            color = MENU_HOVER if self.open_menu == key else MENU_BG
            pygame.draw.rect(surface, color, rect)
            ts = font.render(label, True, MENU_TEXT)
            surface.blit(ts, ts.get_rect(center=rect.center))
        for rect, label, _command in self.items.get(self.open_menu, []):
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
    """Add-node / add-name-tag / edit-name all share this. `purpose` and
    `target` are metadata the loop set at creation so it knows what to do on
    submit; the modal itself just manages the text buffer, its cursor, and its
    rendering. `handle_event` returns None / "submit" / "cancel".

    Editing keys: Left/Right move the cursor, Home/End jump to the ends,
    Backspace/Delete remove around it (Ctrl+Backspace removes a word). Holding a
    key repeats it — the loop turns on key-repeat while one of these is open."""

    def __init__(self, prompt, *, purpose, target=None, value="", q_pastes=True):
        self.prompt = prompt
        self.purpose = purpose
        self.target = target
        self.value = value
        self.cursor = len(value)   # insertion point: index into value
        self.q_pastes = q_pastes   # False where 'q' must be typable (e.g. sqrt)
        self._last_key = 0         # ticks of the last edit, so the caret shows while typing

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
    """Delete confirmation. `layout` is shared by draw and hit-testing so they
    can't drift. `handle_event` returns None / "confirm" / "cancel"."""

    def __init__(self, message, sub="", *, purpose="delete", target=None):
        self.message = message
        self.sub = sub
        self.purpose = purpose
        self.target = target

    def layout(self, screen_size):
        box = pygame.Rect(0, 0, 300, 130)
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
        dl = font.render("Delete", True, (255, 255, 255))
        surface.blit(dl, dl.get_rect(center=delete.center))


# ---- Inspector panel (right-side overlay; shows the selected node) ----------
INSPECTOR_WIDTH = 280
INSPECTOR_BG = (34, 34, 42)
INSPECTOR_BORDER = (70, 70, 84)
INSPECTOR_TITLE = (235, 235, 240)
INSPECTOR_LABEL = (150, 150, 165)
INSPECTOR_VALUE = (228, 228, 234)


def clip_text(text, font, max_width):
    """Trim text with an ellipsis so it fits within max_width pixels."""
    if font.size(text)[0] <= max_width:
        return text
    while text and font.size(text + "…")[0] > max_width:
        text = text[:-1]
    return text + "…"


class InspectorPanel:
    """Right-edge overlay showing the selected node's data (mirrors the JSON:
    type, name, position, id, description, command) plus a 'Set command…' button
    and an 'Activate' button. Drawn only when a node is selected, and it never
    shifts the canvas — it just sits on top of the right side.

    Data-driven: it reads whatever fields the node carries, so a new object type
    needs no changes here. handle_click returns 'activate' / 'set_command' / None
    (a bare panel click); the caller consumes the click either way."""

    def rect(self, screen_size):
        w, h = screen_size
        return pygame.Rect(w - INSPECTOR_WIDTH, MENU_BAR_HEIGHT,
                           INSPECTOR_WIDTH, h - MENU_BAR_HEIGHT)

    def contains(self, pos, screen_size):
        return self.rect(screen_size).collidepoint(pos)

    def _buttons(self, obj, screen_size):
        """(rect, label, action) for each button, laid out bottom-up. Buttons
        depend on the object type: nodes get Activate + Set command, trains get
        Adjust speed."""
        panel = self.rect(screen_size)
        pad, bh, gap = 16, 36, 10
        if isinstance(obj, Neuron):
            specs = [("Set firing equation…", "set_firing"),     # bottom
                     ("Clock trigger equation…", "set_trigger")]  # above it
        elif isinstance(obj, Node):
            specs = [("Activate", "activate"), ("Set command…", "set_command")]
        elif isinstance(obj, Train):
            specs = [("Adjust speed…", "adjust_speed")]
        else:
            specs = []
        rects, y = [], panel.bottom - pad - bh
        for lbl, action in specs:
            rects.append((pygame.Rect(panel.x + pad, y, panel.width - 2 * pad, bh), lbl, action))
            y -= bh + gap
        return rects

    _hot = ()   # extra clickable spots (rect, action) placed by the last draw
    _list_rect = None     # the scrollable W list's area (set by draw), for wheel routing
    _list_obj = None      # whose list is showing; selecting another resets the scroll
    _scroll = 0           # rows scrolled past at the top of the W list
    _max_scroll = 0
    clicked_weight = None # other-neuron id of the W row last clicked (with "edit_weight")

    def scroll_list(self, pos, wheel_y):
        """Mouse-wheel over the W list scrolls it. Returns True if it did (so
        the loop doesn't also zoom the canvas)."""
        if self._list_rect is None or not self._list_rect.collidepoint(pos):
            return False
        self._scroll = max(0, min(self._max_scroll, self._scroll - wheel_y * 3))
        return True

    def _draw_weights(self, surface, neuron, x, y, max_w, screen_size):
        """The neuron's outgoing W to each other neuron, as a scrollable list
        that fills the space down to the buttons."""
        label, row_font = get_font(20), get_font(20)
        surface.blit(label.render("Output W's (to each other neuron)", True, INSPECTOR_LABEL), (x, y))
        y += 24
        buttons = self._buttons(neuron, screen_size)
        bottom = min(r.top for r, _l, _a in buttons) - 12 if buttons else self.rect(screen_size).bottom
        row_h = 22
        area = pygame.Rect(x, y, max_w, max(0, bottom - y))
        self._list_rect = area

        scene = neuron.scene
        rows = []
        for other_id, w in neuron.weights.items():
            other = scene.by_id.get(other_id) if scene else None
            name = (other.text if other is not None and other.text else other_id)
            rows.append((other_id, name, w))
        if not rows:
            surface.blit(row_font.render("— (no other neurons)", True, INSPECTOR_VALUE), (x, y))
            return

        if neuron is not self._list_obj:
            self._list_obj, self._scroll = neuron, 0
        visible = max(1, area.height // row_h)
        self._max_scroll = max(0, len(rows) - visible)
        self._scroll = min(self._scroll, self._max_scroll)

        prev_clip = surface.get_clip()
        surface.set_clip(area)
        mouse = pygame.mouse.get_pos()
        for i, (other_id, name, w) in enumerate(rows[self._scroll:self._scroll + visible]):
            ry = area.y + i * row_h
            hit = pygame.Rect(area.x - 4, ry - 1, area.width + 8, row_h)
            if hit.collidepoint(mouse):              # hover highlight: rows are clickable
                pygame.draw.rect(surface, (58, 58, 72), hit, border_radius=4)
            self._hot.append((hit, ("edit_weight", other_id)))
            val = row_font.render(f"{w:.2f}", True, INSPECTOR_VALUE)
            name_w = area.width - val.get_width() - 12
            surface.blit(row_font.render(clip_text(name, row_font, name_w), True, INSPECTOR_LABEL), (x, ry))
            surface.blit(val, (area.right - val.get_width(), ry))
        surface.set_clip(prev_clip)

        if self._max_scroll:                       # thin scrollbar on the right edge
            track = pygame.Rect(area.right + 6, area.y, 3, visible * row_h)
            thumb_h = max(12, track.height * visible // len(rows))
            thumb_y = track.y + (track.height - thumb_h) * self._scroll // self._max_scroll
            pygame.draw.rect(surface, (55, 55, 64), track)
            pygame.draw.rect(surface, (130, 130, 145), (track.x, thumb_y, 3, thumb_h))

    def handle_click(self, pos, obj, screen_size):
        for rect, _lbl, action in self._buttons(obj, screen_size):
            if rect.collidepoint(pos):
                return action
        for rect, action in self._hot:
            if rect.collidepoint(pos):
                if isinstance(action, tuple):        # ("edit_weight", other neuron id)
                    action, self.clicked_weight = action
                return action
        return None

    def draw(self, surface, obj, screen_size):
        self._hot = []
        self._list_rect = None
        if obj is None:
            return
        panel = self.rect(screen_size)
        pygame.draw.rect(surface, INSPECTOR_BG, panel)
        pygame.draw.line(surface, INSPECTOR_BORDER, panel.topleft, panel.bottomleft, 2)

        pad = 16
        x = panel.x + pad
        y = panel.y + pad
        max_w = panel.width - 2 * pad
        title_font, body, label = get_font(30), get_font(22), get_font(20)

        surface.blit(title_font.render(obj.TYPE.capitalize(), True, INSPECTOR_TITLE), (x, y))
        y += 44

        def field(lbl, val):
            nonlocal y
            surface.blit(label.render(lbl, True, INSPECTOR_LABEL), (x, y))
            y += 22
            surface.blit(body.render(clip_text(val, body, max_w), True, INSPECTOR_VALUE), (x, y))
            y += 32

        if isinstance(obj, Train):
            def node_name(nid):
                n = obj.scene.by_id.get(nid) if obj.scene else None
                return n.display_name if n is not None else (nid or "—")
            field("From", node_name(obj.from_id))
            field("To", node_name(obj.to_id))
            field("Speed", f"{obj.speed:.0f} units/sec")
            field("Corners", str(len(obj.waypoints)))
        elif isinstance(obj, Neuron):
            # Compact one-line rows (label left, value right) so the W list
            # below gets as much room as possible.
            def row(lbl, val, color=INSPECTOR_VALUE, action=None):
                """action: make the row clickable (hover highlight + hit rect)."""
                nonlocal y
                if action:
                    hit = pygame.Rect(x - 4, y - 2, max_w + 8, 24)
                    if hit.collidepoint(pygame.mouse.get_pos()):
                        pygame.draw.rect(surface, (58, 58, 72), hit, border_radius=4)
                    self._hot.append((hit, action))
                ls = label.render(lbl, True, INSPECTOR_LABEL)
                surface.blit(ls, (x, y + 2))
                room = max_w - ls.get_width() - 12
                vs = body.render(clip_text(val, body, room), True, color)
                surface.blit(vs, (x + max_w - vs.get_width(), y))
                y += 26

            row("Name", obj.display_name)
            row("ID", obj.id)
            row("Position", f"{obj.world_center.x:.0f}, {obj.world_center.y:.0f}")
            y += 6
            hit = pygame.Rect(x - 4, y - 2, max_w + 8, 50)     # label + value: clickable
            if hit.collidepoint(pygame.mouse.get_pos()):
                pygame.draw.rect(surface, (58, 58, 72), hit, border_radius=4)
            self._hot.append((hit, "set_firing"))
            field("Firing equation (fires per second)", obj.equation or "— (never fires)")
            if obj.fire_error:
                field("Error", obj.fire_error)
            else:
                state = ("  (waiting)" if obj.waiting else
                         "  (stopped)" if obj.clock_stopped else "")
                row("Clock", f"t = {obj.t:.1f} s{state}")
            row("Clock end", "t = INF" if math.isinf(obj.t_end) else f"t = {obj.t_end:g} s",
                action="edit_t_end")
            if obj.trigger_error:
                row("Clock trigger", f"error: {obj.trigger_error}", (240, 150, 90),
                    action="set_trigger")
            else:
                row("Clock trigger", obj.trigger or "— (none, runs freely)",
                    action="set_trigger")

            # Spike duration: label, then a [<] value [>] stepper on the right.
            # Arrow rects are remembered so handle_click can hit-test them.
            surface.blit(label.render("Spike duration", True, INSPECTOR_LABEL), (x, y + 2))
            frames = obj.spike_frames
            text = body.render(f"{frames} fr ({frames * 1000 / 60:.0f} ms)", True, INSPECTOR_VALUE)
            size = 22
            up = pygame.Rect(x + max_w - size, y, size, size)
            down = pygame.Rect(up.x - 8 - text.get_width() - 8 - size, y, size, size)
            for rect, enabled in ((down, frames > 1), (up, True)):
                pygame.draw.rect(surface, (70, 70, 88) if enabled else (55, 55, 64),
                                 rect, border_radius=4)
            tri = (INSPECTOR_TITLE, (120, 120, 130))
            cy = down.centery
            pygame.draw.polygon(surface, tri[frames <= 1],
                                [(down.right - 7, cy - 5), (down.right - 7, cy + 5), (down.x + 6, cy)])
            pygame.draw.polygon(surface, tri[0],
                                [(up.x + 7, cy - 5), (up.x + 7, cy + 5), (up.right - 6, cy)])
            surface.blit(text, text.get_rect(midleft=(down.right + 8, cy)))
            self._hot += [(down, "spike_down"), (up, "spike_up")]
            y += 28

            count = len(obj.scene.neurons) if obj.scene else 1
            over = count > NEURON_SOFT_LIMIT
            row("Network", f"{count} / ~{NEURON_SOFT_LIMIT} neurons",
                (240, 150, 90) if over else INSPECTOR_VALUE)    # orange = past the smooth limit
            # Σ (other's W to this) × (other is firing). O(n) lookups for the
            # selected neuron only — negligible.
            row("Input W (from firing others)", f"{obj.input_w():.2f}")
            y += 8
            self._draw_weights(surface, obj, x, y, max_w, screen_size)
        else:
            field("Name", obj.text)
            field("Position", f"{obj.world_center.x:.0f}, {obj.world_center.y:.0f}")
            field("ID", obj.id)
            field("Description", obj.description or "—")
            field("Command", obj.params.get("command") or "— (none set)")

        for rect, lbl, action in self._buttons(obj, screen_size):
            enabled = not (action == "activate" and not getattr(obj, "action", None))
            if action == "activate":
                bcolor = (60, 130, 70) if enabled else (55, 55, 64)
                tcolor = (240, 240, 240) if enabled else (120, 120, 130)
            else:
                bcolor, tcolor = (70, 70, 88), INSPECTOR_TITLE
            pygame.draw.rect(surface, bcolor, rect, border_radius=6)
            ts = body.render(lbl, True, tcolor)
            surface.blit(ts, ts.get_rect(center=rect.center))


# ---- Train build mode (interactive placement) ------------------------------
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
        font = get_font(MENU_FONT_SIZE)
        hint = ("Add train — click the starting node    (Esc to cancel)"
                if self.start is None else
                "Click to drop a corner, or click the ending node     "
                "Backspace: undo     Esc: cancel")
        ts = font.render(hint, True, (245, 245, 250))
        bg = pygame.Rect(10, MENU_BAR_HEIGHT + 8, ts.get_width() + 16, ts.get_height() + 10)
        overlay = pygame.Surface(bg.size, pygame.SRCALPHA)
        overlay.fill((0, 0, 0, 175))
        surface.blit(overlay, bg.topleft)
        surface.blit(ts, (bg.x + 8, bg.y + 5))

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
        font = get_font(MENU_FONT_SIZE)
        ts = font.render("Add wall — drag to draw the box    (Esc to cancel)",
                         True, (245, 245, 250))
        bg = pygame.Rect(10, MENU_BAR_HEIGHT + 8, ts.get_width() + 16, ts.get_height() + 10)
        overlay = pygame.Surface(bg.size, pygame.SRCALPHA)
        overlay.fill((0, 0, 0, 175))
        surface.blit(overlay, bg.topleft)
        surface.blit(ts, (bg.x + 8, bg.y + 5))

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
