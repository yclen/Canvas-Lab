"""things.py — the catalog: simple kinds of objects you can drop on the canvas.

Each class here is one object type. Defining the class is all it takes — it
registers itself, appears in the Add menu, saves/loads, copies, and gets an
inspector. No changes to game_loop.py or canvas.py. (game_loop.py imports this
file; canvas.py never does.)

The recipe (subclass canvas.Thing):

    class Rock(Thing):
        TYPE = "rock"              # the saved type name — don't rename once you've placed some
        MENU_LABEL = "Add rock"    # Add-menu entry (leave it out to hide the type)
        SIZE = (70, 50)            # world units (w, h)
        PROPS = [Prop("color", "Color", "color", (130, 130, 140))]   # self.color

        def draw_shape(self, surface, rect, camera):   # rect = on-screen box, already zoomed
            pygame.draw.ellipse(surface, self.color, rect)

Optional extras:
    PROPS        saved fields, editable in the inspector. kinds: "float", "int",
                 "str", "bool" (click toggles), "color", "choice" (click cycles).
    @panel_button("Label")  a method (self, app) shown as an inspector button.
    tick(dt)     called every frame (dt = seconds) — animate here.
    DOT_COLOR    the color it shows as when zoomed far out.
    CATEGORY     an E-menu tab to also list it under (e.g. "Lights"); NAME = tile name.
    icon_sample() / draw_icon(surface)   customize its E-menu icon (default: draws
                 a sample of it with draw_shape, scaled to fit).
    draw_layer   paint order (walls -2, trains -1, things 0, name tags 1).
    inspector(app) / buttons(app)   override for a fully custom panel (see Neuron).
    Subclass another Thing to share its drawing/props/behavior.

`app` (in buttons and callbacks) has: app.scene, app.camera, app.save(),
app.ask(prompt, on_submit), app.confirm(...), app.place(obj).
See "Adding an object type" in DESIGN.md.
"""

import csv
import math
import os
import time
from datetime import datetime

import numpy as np
import pygame

import expressions
import instruments
from canvas import (OBJECT_TYPES, WARN_COLOR, Btn, Field, Gap, Prop, Row, Thing, alpha_layer,
                    clip_text, draw_arc, draw_rect, font_px,
                    get_font, panel_button, run_action)


def shade(color, factor):
    """A color scaled lighter (>1) or darker (<1)."""
    return tuple(max(0, min(255, int(c * factor))) for c in color)


class Rock(Thing):
    """The smallest useful object: a shape with one property."""

    TYPE = "rock"
    MENU_LABEL = "Add rock"
    SIZE = (70, 50)
    DOT_COLOR = (130, 130, 140)
    PROPS = [Prop("color", "Color", "color", (130, 130, 140))]

    def draw_shape(self, surface, rect, camera):
        pygame.draw.ellipse(surface, self.color, rect)
        pygame.draw.ellipse(surface, (60, 60, 66), rect, width=max(1, rect.width // 20))


class Lamp(Thing):
    """Props, a button and animation: switch it on and it glows and pulses."""

    TYPE = "lamp"
    MENU_LABEL = "Add lamp"
    SIZE = (50, 50)
    DOT_COLOR = (255, 220, 120)
    PROPS = [
        Prop("on", "On", "bool", False),
        Prop("color", "Light color", "color", (255, 220, 120)),
        Prop("pulse", "Pulses per second", "float", 0.5, min=0),
    ]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.phase = 0.0                  # live-only state (not a Prop, so not saved)

    def tick(self, dt):
        self.phase += self.pulse * dt

    def draw_shape(self, surface, rect, camera):
        if self.on:
            glow = 0.75 + 0.25 * math.sin(self.phase * 2 * math.pi)
            halo = rect.inflate(rect.width, rect.height)
            layer, area = alpha_layer(surface, halo)      # just the part on screen
            if layer is not None:
                pygame.draw.ellipse(layer, (*self.color, int(70 * glow)), halo.move(-area.x, -area.y))
                surface.blit(layer, area)
            body = tuple(int(c * glow) for c in self.color)
        else:
            body = (70, 70, 78)
        pygame.draw.ellipse(surface, body, rect)
        pygame.draw.ellipse(surface, (30, 30, 34), rect, width=max(1, rect.width // 16))

    def outputs(self):
        return {"on": 1.0 if self.on else 0.0}

    @panel_button("Switch on / off")
    def toggle(self, app):
        self.on = not self.on
        app.save()


# ============================================================================
# INPUTS — things you click to make something happen (E menu ▸ Inputs)
# ============================================================================
INPUT_HOUSING = (52, 52, 62)
INPUT_WELL = (18, 18, 22)
INPUT_FLASH = 0.12          # seconds a press stays visibly down, even for a quick click


class Input(Thing):
    """Base for the input family: a click *presses* it (runs press()) instead
    of selecting it — Ctrl+click or a drag-box selects it for editing. Tracks
    `is_hovered` / `pressed` for drawing, draws the shared housing, and is a
    launcher, so it has a command (Set command…) that press() runs by default.
    Subclasses override draw_face() and, if they do more, press()."""

    CATEGORY = "Inputs"
    LAUNCHER = True
    SHOW_PRESSES = True         # list "Presses (this session)" in the inspector
    SIZE = (64, 64)
    PROPS = [Prop("scale", "Size", "float", 1.0, min=0.25, max=50, unit="×",
                  prompt="Size (1 = normal, 2 = twice as big):")]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.presses = 0          # live count, not saved — handy to see it working
        self._held = False        # mouse went down on it and is still down
        self._flash = 0.0

    @property
    def pressed(self):
        """Drawn pushed in: held down on it, or just tapped."""
        return (self._held and self.is_hovered) or self._flash > 0

    def on_release(self, pos, app):
        self._held = False

    def tick(self, dt):
        self._flash = max(0.0, self._flash - dt)

    def on_click(self, pos, app):
        self._held = True
        self._flash = INPUT_FLASH
        self.presses += 1
        self.press(app)
        return True               # handled: a click presses it, it doesn't select it

    def press(self, app):
        """What one press does. Default: run its command, if one is set."""
        run_action(self, app)

    def outputs(self):
        return {"pressed": 1.0 if self.pressed else 0.0, "presses": float(self.presses)}

    def inspector(self, app):
        rows = super().inspector(app)
        if self.SHOW_PRESSES:
            rows.append(Row("Presses (this session)", self.presses))
        return rows

    @staticmethod
    def well_rect(rect):
        """The recessed area inside the housing, for an on-screen rect."""
        inset = round(min(rect.width, rect.height) * 0.16)
        return rect.inflate(-inset, -inset)

    def draw_shape(self, surface, rect, camera):
        s = min(rect.width, rect.height)
        radius = max(2, s // 6)
        draw_rect(surface, (14, 14, 18), rect.move(0, max(1, s // 22)),
                  border_radius=radius)                      # drop shadow
        draw_rect(surface, INPUT_HOUSING, rect, border_radius=radius)
        draw_rect(surface, shade(INPUT_HOUSING, 1.35), rect,
                  width=max(1, s // 40), border_radius=radius)   # housing edge
        well = self.well_rect(rect)
        draw_rect(surface, INPUT_WELL, well, border_radius=max(2, radius - 2))
        self.draw_face(surface, well, camera)

    def draw_face(self, surface, well, camera):
        """Draw the moving part inside the housing's well (a screen Rect)."""
        pass


BUTTON_ICONS = ["none", "up", "down", "left", "right", "play", "pause", "stop", "record",
                "plus", "minus", "check", "cross", "power", "reset"]


def _stroke(surface, color, points, width):
    """A thick polyline with round ends and joins (pygame's own wide lines leave gaps)."""
    width = max(1, round(width))
    for a, b in zip(points, points[1:]):
        pygame.draw.line(surface, color, a, b, width)
    for p in points:
        pygame.draw.circle(surface, color, p, width / 2)


def draw_button_icon(surface, name, rect, color):
    """One of BUTTON_ICONS, drawn in `color` to fit a square in the middle of rect."""
    if name == "none":
        return
    c = pygame.Vector2(rect.center)
    r = min(rect.width, rect.height) / 2           # icon coordinates run -1 … 1 across this

    def pt(x, y):
        return (c.x + x * r, c.y + y * r)

    w = r * 0.26                                   # stroke width
    if name in ("up", "down", "left", "right"):    # an arrow: a head and a shaft, drawn pointing up, then turned
        shape = [(0, -0.9), (0.72, -0.12), (0.26, -0.12), (0.26, 0.85), (-0.26, 0.85), (-0.26, -0.12),
                 (-0.72, -0.12)]
        turn = {"up": lambda x, y: (x, y), "down": lambda x, y: (x, -y),
                "left": lambda x, y: (y, x), "right": lambda x, y: (-y, x)}[name]
        pygame.draw.polygon(surface, color, [pt(*turn(x, y)) for x, y in shape])
    elif name == "play":
        pygame.draw.polygon(surface, color, [pt(-0.55, -0.75), pt(0.8, 0), pt(-0.55, 0.75)])
    elif name == "pause":
        for x in (-0.55, 0.15):
            pygame.draw.rect(surface, color, pygame.Rect(*pt(x, -0.72), round(0.4 * r), round(1.44 * r)))
    elif name == "stop":
        pygame.draw.rect(surface, color, pygame.Rect(*pt(-0.65, -0.65), round(1.3 * r), round(1.3 * r)),
                         border_radius=max(1, round(r * 0.12)))
    elif name == "record":
        pygame.draw.circle(surface, color, c, r * 0.72)
    elif name in ("plus", "minus"):
        _stroke(surface, color, [pt(-0.7, 0), pt(0.7, 0)], w * 1.15)
        if name == "plus":
            _stroke(surface, color, [pt(0, -0.7), pt(0, 0.7)], w * 1.15)
    elif name == "check":
        _stroke(surface, color, [pt(-0.7, 0.02), pt(-0.22, 0.52), pt(0.72, -0.55)], w * 1.15)
    elif name == "cross":
        _stroke(surface, color, [pt(-0.6, -0.6), pt(0.6, 0.6)], w * 1.15)
        _stroke(surface, color, [pt(0.6, -0.6), pt(-0.6, 0.6)], w * 1.15)
    elif name == "power":                          # a ring open at the top, and a bar through the gap
        ring = [pt(0.7 * math.sin(math.radians(a)), -0.7 * math.cos(math.radians(a))) for a in range(40, 321, 10)]
        _stroke(surface, color, ring, w)
        _stroke(surface, color, [pt(0, -0.88), pt(0, -0.12)], w)
    elif name == "reset":                          # a circular arrow, clockwise
        ring = [pt(0.66 * math.sin(math.radians(a)), -0.66 * math.cos(math.radians(a))) for a in range(30, 331, 10)]
        _stroke(surface, color, ring, w)
        tip = math.radians(330)
        end = pygame.Vector2(pt(0.66 * math.sin(tip), -0.66 * math.cos(tip)))
        along = pygame.Vector2(math.cos(tip), math.sin(tip))      # the way the arc is heading at its end
        across = pygame.Vector2(-along.y, along.x)
        head = r * 0.36
        pygame.draw.polygon(surface, color, [end + along * head, end - across * head * 0.9,
                                             end + across * head * 0.9])


class PushButton(Input):
    """A momentary push button: an arcade-style square cap that lifts when you
    hover and sinks into its housing when you press it. It can carry an icon on
    its cap (arrows, play / stop / …, Icon in the panel; none by default)."""

    TYPE = "push_button"
    MENU_LABEL = "Add push button"
    NAME = "Push button"
    DOT_COLOR = (215, 60, 60)
    PROPS = [Prop("color", "Cap color", "color", (215, 60, 60)),
             Prop("icon", "Icon", "choice", "none", choices=BUTTON_ICONS)]

    def draw_face(self, surface, well, camera):
        s = well.width
        depth = max(1, round(s * 0.11))             # how far the cap travels
        inner = well.inflate(-max(2, round(s * 0.08)), -max(2, round(s * 0.08)))
        radius = max(2, inner.width // 6)
        if self.pressed:
            face = pygame.Rect(inner.x, inner.y + depth // 2, inner.width, inner.height - depth)
            side = face.copy()
            side.height += max(1, depth // 4)        # barely any side showing: it's down
            face_color = shade(self.color, 0.8)
        else:
            face = pygame.Rect(inner.x, inner.y - depth // 2, inner.width, inner.height - depth)
            side = pygame.Rect(face.x, face.y, face.width, face.height + depth)
            face_color = shade(self.color, 1.15) if self.is_hovered else self.color

        if self.is_hovered and not self.pressed:     # soft glow around the well
            draw_rect(surface, shade(self.color, 0.9), well.inflate(4, 4),
                      width=max(1, s // 30), border_radius=radius + 3)
        draw_rect(surface, shade(self.color, 0.5), side, border_radius=radius)   # cap's side
        draw_rect(surface, face_color, face, border_radius=radius)               # cap's top
        if not self.pressed:                                                            # gloss
            gloss = pygame.Rect(0, 0, round(face.width * 0.7), max(1, round(face.height * 0.16)))
            gloss.midtop = (face.centerx, face.y + max(1, round(face.height * 0.1)))
            draw_rect(surface, shade(face_color, 1.3), gloss, border_radius=max(1, gloss.height // 2))
        draw_rect(surface, shade(self.color, 0.4), face,
                  width=max(1, s // 45), border_radius=radius)                   # cap outline

        light = sum(face_color) > 450
        ink = (25, 25, 30) if light else (245, 245, 245)
        label = None                                   # the name, if it fits on the cap
        if self.text:
            size = round(face.height * (0.22 if self.icon != "none" else 0.32))
            if size >= 9:
                ts = get_font(size).render(self.text, True, ink)
                if ts.get_width() <= face.width - 6:
                    label = ts
        if self.icon != "none":                        # the icon, embossed; above the name when both fit
            area = face.inflate(-round(face.width * 0.36), -round(face.height * 0.36))
            if label is not None:
                area = pygame.Rect(area.x, face.y + round(face.height * 0.12), area.width,
                                   round(face.height * 0.52))
            if area.width >= 6:
                draw_button_icon(surface, self.icon, area.move(0, max(1, s // 60)), shade(face_color, 0.55))
                draw_button_icon(surface, self.icon, area, ink)
        if label is not None:
            at = (face.centerx, face.bottom - round(face.height * 0.2)) if self.icon != "none" else face.center
            surface.blit(label, label.get_rect(center=at))


class Switch(Input):
    """An on/off slide switch: each click flips it, and the knob slides across
    a pill-shaped track that fills with the switch's color when it's on. The
    state is saved; press() flips it and then runs its command (if set)."""

    TYPE = "switch"
    MENU_LABEL = "Add switch"
    NAME = "Switch"
    SIZE = (100, 60)
    DOT_COLOR = (70, 200, 110)
    PROPS = [Prop("on", "On", "bool", False),
             Prop("color", "Color", "color", (70, 200, 110))]
    SLIDE_TIME = 0.12               # seconds for the knob to travel across

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._knob = None           # 0 = off side, 1 = on side; follows `on` (None = snap)

    @classmethod
    def icon_sample(cls):
        sample = cls(0, 0)
        sample.on = True
        return sample

    def outputs(self):
        return {"on": 1.0 if self.on else 0.0, "flips": float(self.presses)}

    def press(self, app):
        self.on = not self.on
        app.save()
        super().press(app)          # then run its command, if one is set

    def tick(self, dt):
        super().tick(dt)
        target = 1.0 if self.on else 0.0
        if self._knob is None:
            self._knob = target
        step = dt / self.SLIDE_TIME
        self._knob += max(-step, min(step, target - self._knob))

    def draw_face(self, surface, well, camera):
        knob_at = self._knob if self._knob is not None else float(self.on)
        h = well.height
        track = well.inflate(-max(2, round(h * 0.14)), -max(2, round(h * 0.14)))
        tr = track.height // 2
        on_color = self.color if not self.is_hovered else shade(self.color, 1.12)
        draw_rect(surface, (40, 40, 48), track, border_radius=tr)
        if knob_at > 0:             # the color fills in behind the knob as it slides on
            fill = track.copy()
            fill.width = round(track.height + (track.width - track.height) * knob_at)
            draw_rect(surface, on_color if knob_at > 0.5 else shade(on_color, 0.6),
                      fill, border_radius=tr)
        draw_rect(surface, (12, 12, 15), track, width=max(1, h // 25), border_radius=tr)

        # The knob's spot (drawn last, on top).
        inset = max(1, round(track.height * 0.1))
        d = track.height - 2 * inset
        depth = max(1, round(d * 0.12))
        squish = round(d * 0.12) if self.pressed else 0
        kx = track.x + inset + (track.width - 2 * inset - d) * knob_at
        knob = pygame.Rect(round(kx) - squish // 2, track.y + inset, d + squish, d - depth)

        # ON / OFF centered in the open part of the track beside the knob, if it fits.
        free = (pygame.Rect(track.x + tr // 2, track.y, knob.left - track.x - tr // 2, track.height)
                if knob_at > 0.5 else
                pygame.Rect(knob.right, track.y, track.right - tr // 2 - knob.right, track.height))
        label = "ON" if self.on else "OFF"
        color = (((20, 30, 24) if sum(self.color) > 450 else (245, 245, 245))
                 if self.on else (150, 150, 160))
        size = round(track.height * 0.42)
        while size >= 9 and get_font(size).size(label)[0] > free.width - 4:
            size -= 1
        if size >= 9 and knob_at in (0.0, 1.0):        # hidden mid-slide
            ts = get_font(size).render(label, True, color)
            surface.blit(ts, ts.get_rect(center=free.center))

        kr = max(2, knob.height // 3)
        base = (235, 235, 240) if not self.is_hovered else (250, 250, 255)
        if self.pressed:
            base = shade(base, 0.85)
        draw_rect(surface, (120, 120, 130), knob.move(0, depth), border_radius=kr)  # side
        draw_rect(surface, base, knob, border_radius=kr)
        shine = pygame.Rect(0, 0, round(knob.width * 0.6), max(1, round(knob.height * 0.18)))
        shine.midtop = (knob.centerx, knob.y + max(1, round(knob.height * 0.12)))
        draw_rect(surface, (255, 255, 255), shine, border_radius=max(1, shine.height // 2))
        draw_rect(surface, (90, 90, 100), knob, width=max(1, h // 30), border_radius=kr)


class Slider(Input):
    """A vertical fader: drag the cap up and down (or click the track to jump
    there) to set `value` between `min` (bottom) and `max` (top), snapped to
    `step` if it's above 0. The track fills with color below the cap and a
    readout at the bottom shows the value. `value` is saved — it's the
    parameter other objects can read later."""

    TYPE = "slider"
    MENU_LABEL = "Add slider"
    NAME = "Slider"
    LAUNCHER = False                # it holds a value; it doesn't launch a command
    SHOW_PRESSES = False
    SIZE = (56, 0)                  # width; the height is the `length` prop
    DOT_COLOR = (90, 170, 240)
    PROPS = [Prop("value", "Value", "float", 0.5),
             Prop("min", "Min", "float", 0.0),
             Prop("max", "Max", "float", 1.0),
             Prop("step", "Step (0 = smooth)", "float", 0.0, min=0),
             Prop("length", "Length", "float", 220, min=120, max=20000, unit=" units"),
             Prop("color", "Color", "color", (90, 170, 240))]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._dragging = False
        self._grab = 0              # cursor-to-cap offset (px) when grabbed, so the cap stays put

    @classmethod
    def icon_sample(cls):
        sample = cls(0, 0)
        sample.length, sample.value = 130, 0.7    # short and stout, so it reads as an icon
        return sample

    @property
    def world_height(self):
        return self.length * self.scale

    @property
    def pressed(self):
        return self._dragging

    def outputs(self):
        return {"value": self.value}

    # --- Value ------------------------------------------------------------------
    def clamp(self, value):
        lo, hi = sorted((self.min, self.max))
        if self.step > 0:
            value = self.min + round((value - self.min) / self.step) * self.step
        return max(lo, min(hi, value))

    def on_prop_changed(self, key):
        self.value = self.clamp(self.value)     # keep it inside a new min/max/step

    def fraction(self):
        """0 at the bottom (min), 1 at the top (max)."""
        span = self.max - self.min
        return 0.0 if span == 0 else max(0.0, min(1.0, (self.value - self.min) / span))

    def value_text(self):
        span = abs(self.max - self.min)
        if self.step > 0 and float(self.step).is_integer() and float(self.min).is_integer():
            return f"{self.value:.0f}"
        decimals = 0 if span >= 100 else 1 if span >= 10 else 2 if span >= 1 else 3
        return f"{self.value:.{decimals}f}"

    # --- Layout (shared by drawing and dragging) --------------------------------
    def _layout(self, rect):
        """Screen geometry: (well, travel top y, travel bottom y, cap size, readout rect)."""
        well = self.well_rect(rect)
        s = well.width
        pad = max(1, round(s * 0.08))
        cap_w, cap_h = round(s * 0.84), max(4, round(s * 0.40))
        ro_h = max(6, round(s * 0.40))
        readout = pygame.Rect(well.x + pad, well.bottom - pad - ro_h, well.width - 2 * pad, ro_h)
        top = well.y + pad + cap_h // 2
        bottom = max(top + 1, readout.top - pad - cap_h // 2)
        return well, top, bottom, (cap_w, cap_h), readout

    def _cap_rect(self, layout):
        well, top, bottom, (cap_w, cap_h), _r = layout
        cap = pygame.Rect(0, 0, cap_w, cap_h)
        cap.center = (well.centerx, round(bottom - (bottom - top) * self.fraction()))
        return cap

    def _drag_to(self, y, layout):
        _w, top, bottom, _c, _r = layout
        t = max(0.0, min(1.0, (bottom - y) / (bottom - top)))
        self.value = self.clamp(self.min + t * (self.max - self.min))

    # --- Interaction ------------------------------------------------------------
    def on_click(self, pos, app):
        layout = self._layout(self.screen_rect(app.camera))
        cap = self._cap_rect(layout)
        if cap.inflate(4, 4).collidepoint(pos):
            self._grab = pos[1] - cap.centery      # grabbed the cap: keep the offset
        else:
            self._grab = 0                          # clicked the track: jump there
            self._drag_to(pos[1], layout)
        self._dragging = True
        return True                                 # the loop now sends drags here

    def on_drag(self, pos, app):
        self._drag_to(pos[1] - self._grab, self._layout(self.screen_rect(app.camera)))

    def on_release(self, pos, app):
        self._dragging = False                      # let go: save once, not every frame
        app.save()

    # --- Drawing ----------------------------------------------------------------
    def draw_face(self, surface, well, camera):
        layout = self._layout(self.screen_rect(camera))
        well, top, bottom, (cap_w, cap_h), readout = layout
        s = well.width
        cx = well.centerx
        cap = self._cap_rect(layout)

        # Tick marks down both sides of the travel, longer every fifth.
        if bottom - top > 30 and s >= 24:
            for i in range(11):
                y = round(bottom - (bottom - top) * i / 10)
                long = i % 5 == 0
                half = round(s * (0.38 if long else 0.3))
                inner = round(s * 0.14)
                color = (85, 85, 98) if long else (58, 58, 68)
                pygame.draw.line(surface, color, (cx - half, y), (cx - inner, y), max(1, s // 40))
                pygame.draw.line(surface, color, (cx + inner, y), (cx + half, y), max(1, s // 40))

        # The slot, filled with color from the bottom up to the cap.
        sw = max(2, round(s * 0.12))
        slot = pygame.Rect(cx - sw // 2, top - sw // 2, sw, bottom - top + sw)
        draw_rect(surface, (6, 6, 8), slot, border_radius=sw // 2)
        level = pygame.Rect(slot.x, cap.centery, sw, slot.bottom - cap.centery)
        if level.height > 0:
            glow = shade(self.color, 1.1) if (self.is_hovered or self._dragging) else self.color
            draw_rect(surface, glow, level, border_radius=sw // 2)

        # The cap: raised, with a side, a top shine and a colored grip line.
        depth = max(1, round(cap_h * (0.06 if self._dragging else 0.16)))
        face = cap.move(0, -depth // 2)
        base = (222, 222, 230)
        if self._dragging:
            base = shade(base, 0.88)
        elif self.is_hovered:
            base = (242, 242, 250)
        radius = max(2, cap_h // 4)
        draw_rect(surface, (10, 10, 12), cap.move(0, depth + 1).inflate(2, 0),
                  border_radius=radius)                                         # shadow
        draw_rect(surface, (115, 115, 126), face.move(0, depth), border_radius=radius)  # side
        draw_rect(surface, base, face, border_radius=radius)
        shine = pygame.Rect(0, 0, round(face.width * 0.7), max(1, round(face.height * 0.16)))
        shine.midtop = (face.centerx, face.y + max(1, round(face.height * 0.12)))
        draw_rect(surface, (255, 255, 255), shine, border_radius=max(1, shine.height // 2))
        grip = pygame.Rect(0, 0, round(face.width * 0.78), max(1, round(face.height * 0.14)))
        grip.center = (face.centerx, face.centery + max(1, face.height // 10))
        draw_rect(surface, shade(self.color, 0.9), grip, border_radius=max(1, grip.height // 2))
        draw_rect(surface, (80, 80, 92), face, width=max(1, s // 40), border_radius=radius)

        # Readout: the value in a small dark display.
        corner = max(2, readout.height // 4)
        draw_rect(surface, (8, 9, 11), readout, border_radius=corner)
        draw_rect(surface, (45, 45, 54), readout, width=1, border_radius=corner)
        size = round(readout.height * 0.72)
        text = self.value_text()
        while size >= 8 and get_font(size).size(text)[0] > readout.width - 4:
            size -= 1
        if size >= 8:
            ts = get_font(size).render(text, True, shade(self.color, 1.2))
            surface.blit(ts, ts.get_rect(center=readout.center))


# ============================================================================
# OUTPUTS — things that show a value read live from another object (E ▸ Outputs)
# ============================================================================
def draw_wire(surface, camera, start, end, color):
    """A link between two world points: a thin line with dots flowing from
    start toward end (the direction the value travels)."""
    a = pygame.Vector2(camera.world_to_screen(start))
    b = pygame.Vector2(camera.world_to_screen(end))
    if (b - a).length() < 2:
        return
    pygame.draw.line(surface, shade(color, 0.45), a, b, 2)
    t = pygame.time.get_ticks() / 1000.0
    dots = max(2, min(12, int((b - a).length() / 60)))
    for k in range(dots):
        f = (t * 0.6 + k / dots) % 1.0
        pygame.draw.circle(surface, color, a.lerp(b, f), 3)


class Output(Thing):
    """Base for the output family: it's linked to a *source* — any object with
    outputs() (a slider's value, a switch's on, a neuron's firing, …) — and
    reads one of those values every frame into `self.reading` (None when
    unlinked). Link it with "Link to source…" (then click the source); a wire
    with flowing dots shows the link. The link is saved (source id + which
    value). Subclasses just draw `self.reading`."""

    CATEGORY = "Outputs"
    PROPS = [Prop("scale", "Size", "float", 1.0, min=0.25, max=50, unit="×",
                  prompt="Size (1 = normal, 2 = twice as big):"),
             Prop("source_id", kind="str", default="", hidden=True),
             Prop("source_key", kind="str", default="value", hidden=True)]
    WIRE_COLOR = (120, 220, 150)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.reading = None

    def source(self):
        return self.scene.by_id.get(self.source_id) if self.scene and self.source_id else None

    def tick(self, dt):
        src = self.source()
        self.reading = src.outputs().get(self.source_key) if src is not None else None

    def outputs(self):
        return {} if self.reading is None else {"value": self.reading}   # so outputs can chain

    # --- Linking ------------------------------------------------------------------
    def link_to(self, src, app):
        self.source_id = src.id
        keys = list(src.outputs())
        if self.source_key not in keys:
            self.source_key = keys[0]
        app.save()

    def pick_source(self, app):
        app.start_picking("Click the object to read from (slider, switch, neuron, …)",
                          lambda src: self.link_to(src, app),
                          accept=lambda obj: obj is not self and bool(obj.outputs()),
                          origin=self.center)

    def next_key(self, app):
        """Cycle which of the source's values this reads."""
        src = self.source()
        keys = list(src.outputs()) if src is not None else []
        if keys:
            i = keys.index(self.source_key) if self.source_key in keys else -1
            self.source_key = keys[(i + 1) % len(keys)]
            app.save()

    def unlink(self, app):
        self.source_id = ""
        app.save()

    def inspector(self, app):
        src = self.source()
        if src is not None:
            source_row = Row("Source", src.display_name, on_click=self.pick_source)
            reads_row = Row("Reads", self.source_key, on_click=self.next_key)
        else:
            source_row = Row("Source", "— (source deleted)" if self.source_id else "— (not linked)",
                             on_click=self.pick_source)
            reads_row = None
        rows = super().inspector(app) + [Gap(6), source_row]
        if reads_row is not None:
            rows.append(reads_row)
        rows.append(Row("Reading", "—" if self.reading is None else f"{self.reading:g}"))
        return rows

    def buttons(self, app):
        out = super().buttons(app) + [Btn("Link to source…", self.pick_source)]
        if self.source_id:
            out.append(Btn("Unlink", self.unlink))
        return out

    # --- Drawing ------------------------------------------------------------------
    def draw_links(self, surface, camera):
        src = self.source()
        if src is not None and src.center is not None:
            draw_wire(surface, camera, output_point(src), self.center, self.WIRE_COLOR)


# ---- Seven-segment digits -----------------------------------------------------
#   aaa
#  f   b
#   ggg
#  e   c
#   ddd
SEGMENTS = {"0": "abcdef", "1": "bc", "2": "abged", "3": "abgcd", "4": "fgbc",
            "5": "afgcd", "6": "afgedc", "7": "abc", "8": "abcdefg", "9": "abfgcd",
            "-": "g", " ": "", "E": "afged", "r": "eg", "o": "cdeg"}


def seven_segment_cells(text):
    """'-12.5' -> [('-', False), ('1', False), ('2', True), ('5', False)]: a
    decimal point lights the dot of the digit before it, taking no cell."""
    cells = []
    for ch in text:
        if ch == "." and cells:
            cells[-1] = (cells[-1][0], True)
        elif ch != ".":
            cells.append((ch, False))
    return cells


def draw_seven_segment(surface, cells, rect, color, ghost, slots):
    """Right-aligned digits filling rect's height (shrunk to fit its width),
    in `slots` cells at least. Unlit segments draw in `ghost`, like a real LED."""
    n = max(slots, len(cells))
    ch = rect.height
    cw, gap = ch * 0.52, ch * 0.2
    total = n * cw + (n - 1) * gap
    if total > rect.width:
        k = rect.width / total
        ch, cw, gap = ch * k, cw * k, gap * k
        total = rect.width
    t = max(1.0, ch * 0.13)
    y = rect.y + (rect.height - ch) / 2
    x0 = rect.right - total
    cells = [(" ", False)] * (n - len(cells)) + cells
    r = max(1, round(t / 2))
    for i, (char, dot) in enumerate(cells):
        x = x0 + i * (cw + gap)
        half = ch / 2
        seg = {"a": (x + t * 0.6, y, cw - t * 1.2, t),
               "g": (x + t * 0.6, y + half - t / 2, cw - t * 1.2, t),
               "d": (x + t * 0.6, y + ch - t, cw - t * 1.2, t),
               "f": (x, y + t * 0.6, t, half - t * 0.9),
               "b": (x + cw - t, y + t * 0.6, t, half - t * 0.9),
               "e": (x, y + half + t * 0.3, t, half - t * 0.9),
               "c": (x + cw - t, y + half + t * 0.3, t, half - t * 0.9)}
        lit = SEGMENTS.get(char, "")
        for name, box in seg.items():
            box = pygame.Rect(round(box[0]), round(box[1]), max(1, round(box[2])), max(1, round(box[3])))
            draw_rect(surface, color if name in lit else ghost, box, border_radius=r)
        dp = pygame.Rect(round(x + cw + gap * 0.5 - t / 2), round(y + ch - t), max(1, round(t)), max(1, round(t)))
        draw_rect(surface, color if dot else ghost, dp, border_radius=r)


class Readout(Output):
    """A little LED display: shows the value it reads in glowing seven-segment
    digits (unlit segments faintly visible behind), with a unit beside it —
    e.g. "60.00 mW". Shows dashes when it isn't linked to anything."""

    TYPE = "readout"
    MENU_LABEL = "Add readout"
    NAME = "Readout"
    SIZE = (220, 84)
    DOT_COLOR = (255, 170, 50)
    PROPS = [Prop("unit", "Unit", "str", "", prompt="Unit (e.g. mW, ms, V, Hz, %):"),
             Prop("decimals", "Decimals", "int", 2, min=0, max=6),
             Prop("digits", "Digit slots", "int", 5, min=1, max=12),
             Prop("color", "Color", "color", (255, 170, 50))]

    @classmethod
    def icon_sample(cls):
        sample = cls(0, 0)
        sample.reading, sample.digits = 42, 3
        return sample

    def text_value(self):
        if self.reading is None:
            return "-" * max(1, min(self.digits, 4))
        return f"{self.reading:.{self.decimals}f}"

    def draw_shape(self, surface, rect, camera):
        s = min(rect.width, rect.height)
        radius = max(2, s // 6)
        draw_rect(surface, (14, 14, 18), rect.move(0, max(1, s // 22)), border_radius=radius)
        draw_rect(surface, INPUT_HOUSING, rect, border_radius=radius)
        draw_rect(surface, shade(INPUT_HOUSING, 1.35), rect, width=max(1, s // 40),
                  border_radius=radius)
        if s < 16:                                      # too small for detail: just a lit screen
            draw_rect(surface, shade(self.color, 0.5), rect.inflate(-max(1, s // 3), -max(1, s // 3)))
            return

        bezel = max(2, round(s * 0.12))
        top = bezel
        if self.text:                                   # name printed on the bezel
            size = round(s * 0.17)
            if size >= 9:
                ts = get_font(size).render(self.text, True, (170, 170, 182))
                if ts.get_width() <= rect.width - 2 * bezel:
                    surface.blit(ts, (rect.x + bezel, rect.y + max(1, round(bezel * 0.6))))
                    top = round(bezel * 0.6) + ts.get_height() + max(1, bezel // 3)
        screen = pygame.Rect(rect.x + bezel, rect.y + top, rect.width - 2 * bezel,
                             max(4, rect.height - top - bezel))
        glass = shade(self.color, 0.07)
        draw_rect(surface, glass, screen, border_radius=max(2, radius - 3))
        draw_rect(surface, (8, 8, 10), screen, width=max(1, s // 45),
                  border_radius=max(2, radius - 3))

        pad = max(2, round(screen.height * 0.16))
        inner = screen.inflate(-2 * pad, -2 * pad)
        unit_w = 0
        if self.unit:
            usize = round(inner.height * 0.5)
            if usize >= 8:
                us = get_font(usize).render(self.unit, True, self.color)
                unit_w = us.get_width() + pad
                if unit_w < inner.width * 0.5:
                    surface.blit(us, us.get_rect(bottomright=(inner.right, inner.bottom + 1)))
                else:
                    unit_w = 0
        digits_rect = pygame.Rect(inner.x, inner.y, inner.width - unit_w, inner.height)
        if digits_rect.height >= 6:
            draw_seven_segment(surface, seven_segment_cells(self.text_value()), digits_rect,
                               self.color, shade(self.color, 0.16), self.digits)
        # glass shine along the top
        shine = pygame.Rect(screen.x + 3, screen.y + 2, screen.width - 6, max(1, screen.height // 10))
        if shine.width > 0:
            layer, area = alpha_layer(surface, shine)     # just the part on screen
            if layer is not None:
                layer.fill((255, 255, 255, 14))
                surface.blit(layer, area)


# ============================================================================
# MATH — things that compute a value from other objects' values (E menu ▸ Math)
# ============================================================================
FUNCTION_T0 = time.monotonic()  # `t` in equations: seconds since the canvas started
FN_PAD = 12                     # Function layout, in world units at Size 1
FN_EQ_SIZE = 18                 # equation text size
FN_LOWER = 44                   # where the variable chips and result screen start
FN_CHIP_H, FN_CHIP_GAP = 24, 8
FN_CHIP_TEXT = 15
FN_MIN_W, FN_MAX_W = 220, 800


def output_point(obj):
    """World point a wire from obj's output starts at: its output_point() if
    it has one (a Function's output nub), else its center."""
    point = getattr(obj, "output_point", None)
    return point() if point is not None else obj.center


class Function(Thing):
    """y = an equation you type (click it). Every name in the equation that
    isn't built in (sin, sqrt, pi, t, … — see expressions.py) becomes a little
    variable chip on the left edge; click a chip, then click a slider, switch,
    readout, device or another function, and that variable follows its value.
    The result `y` is an output, so readouts, devices and other functions can
    link to it. Links are kept by variable name, so editing the equation never
    loses wiring (a name that comes back reconnects). Unlinked variables, bad
    syntax or 1/0 give no value (readouts show dashes; devices hold their last
    one) and the reason, in red. Ctrl+click selects it."""

    TYPE = "function"
    MENU_LABEL = "Add function"
    NAME = "Function"
    CATEGORY = "Math"
    DOT_COLOR = (175, 140, 255)
    PROPS = [Prop("scale", "Size", "float", 1.0, min=0.25, max=50, unit="×",
                  prompt="Size (1 = normal, 2 = twice as big):"),
             Prop("color", "Color", "color", (175, 140, 255)),
             Prop("expression", kind="str", default="", hidden=True),
             Prop("links", kind="data", default={}, hidden=True)]     # variable -> {"id", "key"}
    WIRE_COLOR = (120, 220, 150)

    @classmethod
    def icon_sample(cls):
        sample = cls(0, 0, expression="2*P + 1")
        sample.result = 7.0
        return sample

    def __init__(self, x, y, text="", *, expression=None, **kwargs):
        super().__init__(x, y, text, **kwargs)
        if expression is not None:
            self.expression = expression
        self.result = None          # last value of y, or None
        self.problem = ""           # why there's no value ("" when there is one)
        self._eq = None             # Equation for self._eq.text, rebuilt when the text changes
        self._size_key = None       # what the cached layout widths were measured for
        self._widths = (FN_MIN_W, [])

    # --- Equation ---------------------------------------------------------------
    def equation(self):
        if self._eq is None or self._eq.text != self.expression:
            self._eq = expressions.Equation(self.expression)
        return self._eq

    def source(self, name):
        link = self.links.get(name)
        return self.scene.by_id.get(link.get("id")) if link and self.scene else None

    def tick(self, dt):
        eq = self.equation()
        self.result, self.problem = None, ""
        if eq.error:
            self.problem = eq.error
            return
        values = {"t": time.monotonic() - FUNCTION_T0}
        unlinked = []
        for name in eq.variables:
            src = self.source(name)
            if src is None:
                unlinked.append(name)
                continue
            value = src.outputs().get(self.links[name].get("key"))
            if value is None:
                self.problem = f"{name}: its source has no value"
                return
            values[name] = value
        if unlinked:
            self.problem = f"link {', '.join(unlinked)} (click {'its' if len(unlinked) == 1 else 'a'} chip)"
            return
        try:
            self.result = eq.evaluate(values)
        except expressions.EquationError as exc:
            self.problem = str(exc)

    def outputs(self):
        return {"y": self.result}       # None while there's no value: readers show "—" / hold

    def result_text(self):
        return "—" if self.result is None else f"{self.result:.6g}"

    # --- Editing and linking --------------------------------------------------------
    def edit_equation(self, app):
        def submit(text):
            self.expression = text.strip()
            app.save()
        app.ask("Equation  y =", submit, value=self.expression, q_pastes=False)

    def pick_source(self, app, name):
        def link(src):
            if src is self:                         # clicked itself: unlink
                self.links.pop(name, None)
            else:
                keys = list(src.outputs())
                self.links[name] = {"id": src.id, "key": "value" if "value" in keys else keys[0]}
            app.save()
        linked = self.source(name) is not None
        hint = f" — or click this function to unlink {name}" if linked else ""
        app.start_picking(f"Click what {name} should follow (a slider, switch, …){hint}", link,
                          accept=lambda obj: (obj is self and linked) or
                                             (obj is not self and bool(obj.outputs())),
                          origin=self.chip_point(name))

    def next_key(self, app, name):
        """Cycle which of the source's values a variable reads."""
        src = self.source(name)
        keys = list(src.outputs()) if src is not None else []
        if keys:
            key = self.links[name].get("key")
            i = keys.index(key) if key in keys else -1
            self.links[name]["key"] = keys[(i + 1) % len(keys)]
            app.save()

    def unlink_all(self, app):
        self.links = {}
        app.save()

    def on_click(self, pos, app):
        name = self.chip_at(pos, app.camera)
        if name is not None:
            self.pick_source(app, name)
        else:
            self.edit_equation(app)
        return True                 # a click edits/links; Ctrl+click selects

    # --- Inspector ------------------------------------------------------------------
    def inspector(self, app):
        rows = [Field("Equation", f"y = {self.expression}" if self.expression else "— click to type one",
                      on_click=self.edit_equation),
                Row("y", self.result_text(), color=None if self.result is not None else WARN_COLOR)]
        if self.problem:
            rows.append(Field("Problem", self.problem, color=WARN_COLOR))
        for name in self.equation().variables:
            rows.append(Gap(6))
            src = self.source(name)
            if src is None:
                rows.append(Row(name, "— click to link", on_click=lambda app, n=name: self.pick_source(app, n)))
                continue
            value = src.outputs().get(self.links[name].get("key"))
            rows.append(Row(name, "—" if value is None else f"{value:g}"))
            rows.append(Row("   follows", src.display_name,
                            on_click=lambda app, n=name: self.pick_source(app, n)))
            rows.append(Row("   reads", self.links[name].get("key"),
                            on_click=lambda app, n=name: self.next_key(app, n)))
        return rows + [Gap(6)] + self.prop_rows()

    def buttons(self, app):
        out = super().buttons(app) + [Btn("Edit equation…", self.edit_equation)]
        if self.links:
            out.append(Btn("Unlink all", self.unlink_all))
        return out

    # --- Layout (world units at Size 1; shared by drawing, clicks and wires) ----------
    def _measure(self):
        """(width, [chip width per variable]), re-measured when the text changes."""
        names = self.equation().variables
        key = (self.expression, tuple(names))
        if key != self._size_key:
            font = get_font(100, "consolas", True)
            eq_w = font.size(f"y = {self.expression}")[0] * FN_EQ_SIZE / 100
            chips = [max(FN_CHIP_H, font.size(n)[0] * FN_CHIP_TEXT / 100 + 14) for n in names]
            col = max(chips, default=0)
            width = max(FN_MIN_W, min(FN_MAX_W, eq_w + 2 * FN_PAD), FN_PAD * 2 + col + 10 + 120)
            self._size_key, self._widths = key, (width, chips)
        return self._widths

    @property
    def world_width(self):
        return self._measure()[0] * self.scale

    @property
    def world_height(self):
        n = len(self.equation().variables)
        chips = n * (FN_CHIP_H + FN_CHIP_GAP) - FN_CHIP_GAP if n else 0
        return (FN_LOWER + max(44, chips) + FN_PAD) * self.scale

    def _chip_boxes(self):
        """[(name, x, y, w, h)] in world units at Size 1 from the top-left corner."""
        _w, chips = self._measure()
        return [(name, FN_PAD, FN_LOWER + i * (FN_CHIP_H + FN_CHIP_GAP), cw, FN_CHIP_H)
                for i, (name, cw) in enumerate(zip(self.equation().variables, chips))]

    def _screen_box(self):
        width, chips = self._measure()
        x = FN_PAD + (max(chips) + 10 if chips else 0)
        return x, FN_LOWER, width - FN_PAD - x, self.world_height / self.scale - FN_LOWER - FN_PAD

    def _to_world(self, x, y):
        c = self.world_center
        return (c.x - self.world_width / 2 + x * self.scale, c.y - self.world_height / 2 + y * self.scale)

    def chip_point(self, name):
        """World point where a variable's wire ends: its chip's left edge."""
        for n, x, y, _w, h in self._chip_boxes():
            if n == name:
                return self._to_world(x, y + h / 2)
        return self.center

    def output_point(self):
        """World point the output wire leaves from: the nub on the right edge."""
        x, y, w, h = self._screen_box()
        return self._to_world(x + w + FN_PAD, y + h / 2)

    def _chip_rects(self, rect):
        k = rect.width / self._measure()[0]          # screen px per layout unit
        return [(name, pygame.Rect(rect.x + round(x * k), rect.y + round(y * k),
                                   max(1, round(w * k)), max(1, round(h * k))))
                for name, x, y, w, h in self._chip_boxes()], k

    def chip_at(self, pos, camera):
        chips, _k = self._chip_rects(self.screen_rect(camera))
        return next((name for name, r in chips if r.inflate(6, 6).collidepoint(pos)), None)

    # --- Drawing ----------------------------------------------------------------------
    def draw_links(self, surface, camera):
        if self.scene is None:
            return
        for name in self.equation().variables:     # links for names no longer in it stay hidden
            src = self.source(name)
            if src is not None and src.center is not None:
                draw_wire(surface, camera, output_point(src), self.chip_point(name), self.WIRE_COLOR)

    def draw_shape(self, surface, rect, camera):
        s = min(rect.width, rect.height)
        radius = max(2, s // 8)
        draw_rect(surface, (14, 14, 18), rect.move(0, max(1, s // 22)), border_radius=radius)
        draw_rect(surface, INPUT_HOUSING, rect, border_radius=radius)
        draw_rect(surface, shade(INPUT_HOUSING, 1.35), rect, width=max(1, s // 40),
                  border_radius=radius)
        if s < 16:                                   # too small for detail
            draw_rect(surface, shade(self.color, 0.5), rect.inflate(-max(1, s // 3), -max(1, s // 3)))
            return
        chips, k = self._chip_rects(rect)
        bad_eq = bool(self.equation().error)

        # The equation along the top: "y =" in the accent color, then the text.
        size = round(FN_EQ_SIZE * k)
        if size >= 7:
            font = get_font(size, "consolas", True)
            x, y = rect.x + round(FN_PAD * k), rect.y + round(FN_PAD * k)
            head = font.render("y = ", True, self.color)
            surface.blit(head, (x, y))
            body = clip_text(self.expression or "(click to type)", font,
                             rect.right - round(FN_PAD * k) - x - head.get_width())
            surface.blit(font.render(body, True, WARN_COLOR if bad_eq else (215, 215, 225)),
                         (x + head.get_width(), y))

        # Variable chips: hollow until linked, filled once linked, brighter under the mouse.
        mouse = pygame.mouse.get_pos() if self.is_hovered else None
        csize = round(FN_CHIP_TEXT * k)
        for name, r in chips:
            linked = self.source(name) is not None
            hot = mouse is not None and r.inflate(6, 6).collidepoint(mouse)
            color = shade(self.color, 1.2) if hot else self.color
            cr = max(2, r.height // 2)
            if linked:
                draw_rect(surface, color, r, border_radius=cr)
            else:
                draw_rect(surface, INPUT_WELL, r, border_radius=cr)
                draw_rect(surface, color, r, width=max(1, round(2 * k)), border_radius=cr)
            if csize >= 7:
                ts = get_font(csize, "consolas", True).render(name, True, INPUT_WELL if linked else color)
                surface.blit(ts, ts.get_rect(center=r.center))

        # The result screen, with the output nub on the right edge.
        x, y, w, h = self._screen_box()
        screen = pygame.Rect(rect.x + round(x * k), rect.y + round(y * k), round(w * k), round(h * k))
        corner = max(2, round(6 * k))
        draw_rect(surface, shade(self.color, 0.07), screen, border_radius=corner)
        draw_rect(surface, (8, 8, 10), screen, width=max(1, s // 45), border_radius=corner)
        nub = (rect.right, screen.centery)
        pygame.draw.circle(surface, self.color if self.result is not None else shade(self.color, 0.4),
                           nub, max(2, round(5 * k)))
        pad = round(8 * k)
        if self.result is not None:
            text, color, size = self.result_text(), shade(self.color, 1.2), round(h * 0.55 * k)
        else:
            text, color, size = self.problem or "—", WARN_COLOR, round(13 * k)
        room = screen.width - 2 * pad
        while self.result is not None and size > 7 and get_font(size, "consolas", True).size(text)[0] > room:
            size = max(7, int(size * 0.9))          # the number shrinks to fit; an error clips
        if size >= 7:
            font = get_font(size, "consolas", True)
            ts = font.render(clip_text(text, font, room), True, color)
            if self.result is not None:
                surface.blit(ts, ts.get_rect(midright=(screen.right - pad, screen.centery)))
            else:
                surface.blit(ts, ts.get_rect(midleft=(screen.x + pad, screen.centery)))


# ---- Counter: a value that buttons step up and down ----------------------------------
CT_W = 240                      # Counter layout, in world units at Size 1
CT_TITLE = 30                   # the name row
CT_CHIP_W = 62
CT_INPUTS = ("up", "down", "reset")


class Counter(Thing):
    """A saved value that steps: each press of whatever's linked to its `up` /
    `down` chip (a push button — a 0 → 1 change counts once) adds / takes away
    the Step, and `reset` puts it back to Start. Optional Minimum / Maximum: it
    stops there (presses past a limit do nothing, so nothing gets "stored up").
    Its `value` is an output for devices, functions, tags, … Click a chip, then
    the button; click the number to type a value. Ctrl+click selects it."""

    TYPE = "counter"
    MENU_LABEL = "Add counter"
    NAME = "Counter"
    CATEGORY = "Math"
    DOT_COLOR = (255, 170, 80)
    PROPS = [Prop("scale", "Size", "float", 1.0, min=0.25, max=50, unit="×",
                  prompt="Size (1 = normal, 2 = twice as big):"),
             Prop("color", "Color", "color", (255, 170, 80)),
             Prop("step", "Step", "float", 1.0, prompt="Step (added by up, taken away by down):"),
             Prop("start", "Start", "float", 0.0, prompt="Start (what reset goes back to):"),
             Prop("value", kind="float", default=0.0, hidden=True),
             Prop("low", kind="data", default=None, hidden=True),      # minimum, or None
             Prop("high", kind="data", default=None, hidden=True),     # maximum, or None
             Prop("links", kind="data", default={}, hidden=True)]      # input -> {"id", "key"}
    WIRE_COLOR = (120, 220, 150)

    @classmethod
    def icon_sample(cls):
        sample = cls(0, 0)
        sample.value = 42
        return sample

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._last_high = {}        # input -> high last frame (missing = not read yet: no edge)
        self._flash = 0.0           # seconds the number stays bright after a change

    @property
    def title(self):
        return self.text or "Counter"

    # --- Stepping ---------------------------------------------------------------------
    def clamp(self, value):
        if self.low is not None:
            value = max(self.low, value)
        if self.high is not None:
            value = min(self.high, value)
        return value

    def set_value(self, value, app=None):
        value = self.clamp(round(float(value), 12))      # round away float drift (0.1 + 0.2)
        if value != self.value:
            self.value = value
            self._flash = 0.25
            if app is not None:
                app.save()
            elif self.scene is not None:
                self.scene.save()

    def up(self, app=None):
        self.set_value(self.value + self.step, app)

    def down(self, app=None):
        self.set_value(self.value - self.step, app)

    def reset(self, app=None):
        self.set_value(self.start, app)

    def source(self, name):
        link = self.links.get(name)
        return self.scene.by_id.get(link.get("id")) if link and self.scene else None

    def tick(self, dt):
        self._flash = max(0.0, self._flash - dt)
        for name in CT_INPUTS:
            src = self.source(name)
            on = src.outputs().get(self.links[name].get("key")) if src is not None else None
            if on is None:
                self._last_high.pop(name, None)
                continue
            high = on > DC_EDGE
            if high and self._last_high.get(name) is False:       # a press: 0 → 1
                getattr(self, name)()
            self._last_high[name] = high

    def outputs(self):
        return {"value": self.value}

    # --- Editing and linking ------------------------------------------------------------
    def type_value(self, app):
        def submit(text):
            try:
                self.set_value(float(text), app)
            except ValueError:
                pass
        app.ask("Value:", submit, value=f"{self.value:g}")

    def set_limit(self, app, which):
        def submit(text):
            text = text.strip()
            try:
                setattr(self, which, None if not text or text.lower() == "none" else float(text))
            except ValueError:
                return
            self.set_value(self.value, app)                  # pull the value inside a new limit
            app.save()
        label = "Minimum" if which == "low" else "Maximum"
        current = getattr(self, which)
        app.ask(f"{label} (blank = none):", submit, value="" if current is None else f"{current:g}")

    def rename(self, app):
        def submit(text):
            if text.strip():
                self.text = text.strip()
                app.save()
        app.ask("Name:", submit, value=self.title)

    def pick_source(self, app, name):
        linked = self.source(name) is not None
        def link(src):
            if src is self:                                  # clicked itself: unlink
                self.links.pop(name, None)
            else:
                keys = list(src.outputs())
                key = next((k for k in ("pressed", "on", "value") if k in keys), keys[0])
                self.links[name] = {"id": src.id, "key": key}
            self._last_high.pop(name, None)
            app.save()
        hint = " — or click this counter to unlink it" if linked else ""
        what = {"up": "adds a step", "down": "takes a step away", "reset": "resets it"}[name]
        app.start_picking(f"Click what {what} (a push button: once per press){hint}", link,
                          accept=lambda obj: (obj is self and linked) or (obj is not self and bool(obj.outputs())),
                          origin=self.chip_point(name))

    def next_key(self, app, name):
        src = self.source(name)
        keys = list(src.outputs()) if src is not None else []
        if keys:
            key = self.links[name].get("key")
            self.links[name]["key"] = keys[(keys.index(key) + 1) % len(keys) if key in keys else 0]
            self._last_high.pop(name, None)
            app.save()

    def unlink_all(self, app):
        self.links = {}
        self._last_high = {}
        app.save()

    def on_click(self, pos, app):
        name = self.chip_at(pos, app.camera)
        if name is not None:
            self.pick_source(app, name)
        else:
            self.type_value(app)
        return True

    def inspector(self, app):
        rows = [Row("Name", self.title, on_click=self.rename),
                Row("Value", f"{self.value:.6g}", on_click=self.type_value),
                Row("Minimum", "none" if self.low is None else f"{self.low:g}",
                    on_click=lambda app: self.set_limit(app, "low")),
                Row("Maximum", "none" if self.high is None else f"{self.high:g}",
                    on_click=lambda app: self.set_limit(app, "high"))]
        for name in CT_INPUTS:
            rows.append(Gap(6))
            src = self.source(name)
            rows.append(Row(name, src.display_name if src else "— click to link",
                            on_click=lambda app, n=name: self.pick_source(app, n)))
            if src is not None:
                rows.append(Row("   reads", self.links[name].get("key"),
                                on_click=lambda app, n=name: self.next_key(app, n)))
        return rows + [Gap(6)] + self.prop_rows()

    def buttons(self, app):
        out = super().buttons(app) + [Btn(f"+ {self.step:g}", self.up, style="go"),
                                      Btn(f"- {self.step:g}", self.down), Btn("Reset", self.reset)]
        if self.links:
            out.append(Btn("Unlink all", self.unlink_all))
        return out

    # --- Layout (world units at Size 1 from the top-left; shared by drawing, clicks, wires) --
    @property
    def world_width(self):
        return CT_W * self.scale

    @property
    def world_height(self):
        return (FN_PAD + CT_TITLE + 3 * FN_CHIP_H + 2 * FN_CHIP_GAP + FN_PAD) * self.scale

    def _to_world(self, x, y):
        c = self.world_center
        return (c.x - self.world_width / 2 + x * self.scale, c.y - self.world_height / 2 + y * self.scale)

    def _chip_boxes(self):
        top = FN_PAD + CT_TITLE
        return [(name, FN_PAD, top + i * (FN_CHIP_H + FN_CHIP_GAP), CT_CHIP_W, FN_CHIP_H)
                for i, name in enumerate(CT_INPUTS)]

    def _screen_box(self):
        x = FN_PAD + CT_CHIP_W + 10
        y = FN_PAD + CT_TITLE
        return x, y, CT_W - FN_PAD - x, self.world_height / self.scale - FN_PAD - y

    def chip_point(self, name):
        for n, x, y, _w, h in self._chip_boxes():
            if n == name:
                return self._to_world(x, y + h / 2)
        return self.center

    def output_point(self):
        x, y, w, h = self._screen_box()
        return self._to_world(x + w + FN_PAD, y + h / 2)

    def _chip_rects(self, rect):
        k = rect.width / CT_W
        return [(name, pygame.Rect(rect.x + round(x * k), rect.y + round(y * k),
                                   max(1, round(w * k)), max(1, round(h * k))))
                for name, x, y, w, h in self._chip_boxes()], k

    def chip_at(self, pos, camera):
        chips, _k = self._chip_rects(self.screen_rect(camera))
        return next((name for name, r in chips if r.inflate(6, 6).collidepoint(pos)), None)

    # --- Drawing ----------------------------------------------------------------------
    def draw_links(self, surface, camera):
        if self.scene is None:
            return
        for name in CT_INPUTS:
            src = self.source(name)
            if src is not None and src.center is not None:
                draw_wire(surface, camera, output_point(src), self.chip_point(name), self.WIRE_COLOR)

    def draw_shape(self, surface, rect, camera):
        s = min(rect.width, rect.height)
        radius = max(2, s // 8)
        draw_rect(surface, (14, 14, 18), rect.move(0, max(1, s // 22)), border_radius=radius)
        draw_rect(surface, INPUT_HOUSING, rect, border_radius=radius)
        draw_rect(surface, shade(INPUT_HOUSING, 1.35), rect, width=max(1, s // 40), border_radius=radius)
        if s < 16:
            draw_rect(surface, shade(self.color, 0.5), rect.inflate(-max(1, s // 3), -max(1, s // 3)))
            return
        chips, k = self._chip_rects(rect)

        # The name, and the step / limits beside it.
        size = round(16 * k)
        if size >= 7:
            font = get_font(size, "bahnschrift")
            title = font.render(clip_text(self.title, font, rect.width * 0.5), True, (226, 228, 236))
            surface.blit(title, (rect.x + round(FN_PAD * k), rect.y + round(FN_PAD * k)))
            span = ""
            if self.low is not None or self.high is not None:
                span = f"  {'' if self.low is None else f'{self.low:g}'}…{'' if self.high is None else f'{self.high:g}'}"
            info = get_font(max(7, round(13 * k)), "consolas", True).render(
                f"step {self.step:g}{span}", True, shade(self.color, 0.85))
            room = rect.right - round(FN_PAD * k) - (rect.x + round(FN_PAD * k) + title.get_width() + round(8 * k))
            if info.get_width() <= room:
                surface.blit(info, info.get_rect(midright=(rect.right - round(FN_PAD * k),
                                                           rect.y + round(FN_PAD * k) + title.get_height() // 2)))

        # Input chips: hollow until linked, filled once linked, brighter under the mouse.
        mouse = pygame.mouse.get_pos() if self.is_hovered else None
        csize = round(FN_CHIP_TEXT * k)
        for name, r in chips:
            linked = self.source(name) is not None
            hot = mouse is not None and r.inflate(6, 6).collidepoint(mouse)
            color = shade(self.color, 1.2) if hot else self.color
            cr = max(2, r.height // 2)
            if linked:
                draw_rect(surface, color, r, border_radius=cr)
            else:
                draw_rect(surface, INPUT_WELL, r, border_radius=cr)
                draw_rect(surface, color, r, width=max(1, round(2 * k)), border_radius=cr)
            if csize >= 7:
                ts = get_font(csize, "consolas", True).render(name, True, INPUT_WELL if linked else color)
                surface.blit(ts, ts.get_rect(center=r.center))

        # The value, Function-style, with the output nub on the right edge.
        x, y, w, h = self._screen_box()
        screen = pygame.Rect(rect.x + round(x * k), rect.y + round(y * k), round(w * k), round(h * k))
        corner = max(2, round(6 * k))
        draw_rect(surface, shade(self.color, 0.07), screen, border_radius=corner)
        draw_rect(surface, (8, 8, 10), screen, width=max(1, s // 45), border_radius=corner)
        pygame.draw.circle(surface, self.color, (rect.right, screen.centery), max(2, round(5 * k)))
        pad = round(8 * k)
        text = f"{self.value:.6g}"
        at_limit = self.value in (self.low, self.high)
        color = shade(self.color, 1.2 + 0.4 * (self._flash / 0.25)) if not at_limit else shade(self.color, 0.85)
        size = round(h * 0.42 * k)
        while size > 7 and get_font(size, "consolas", True).size(text)[0] > screen.width - 2 * pad:
            size = max(7, int(size * 0.9))
        if size >= 7:
            ts = get_font(size, "consolas", True).render(text, True, color)
            surface.blit(ts, ts.get_rect(midright=(screen.right - pad, screen.centery)))


# ============================================================================
# DATA — things that record other objects' values (E menu ▸ Data)
# ============================================================================
DC_PAD = 10                     # Data collector layout, in world units at Size 1
DC_TITLE_H = 40                 # title bar: name, row count, Collect button
DC_CELL_H = 26                  # column-header and data row height
DC_NUM_W, DC_TIME_W = 44, 92    # the built-in # and time columns
DC_COL_W = 120                  # each named column
DC_ADD_W = 34                   # the "+" cell that adds a column
DC_CHIP_W = 80                  # the collect input chip, left of the title bar
DC_TEXT = 14
DC_BUTTON_W = 96
DC_SCROLL_W = 14                # the scrollbar, right of the sheet
DC_WHEEL_ROWS = 3               # rows per wheel notch
DC_EDGE = 0.5                   # collect input above this = high (a rising edge collects)
DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")


class DataCollector(Thing):
    """A spreadsheet that records values. Each column follows a source (a
    readout, device, function, …): click its header, then the source. Press
    Collect — or give its `collect` input a rising edge (0 → 1, e.g. from a
    push button) — and a new row is added with every column's value right then,
    plus the time. Rows keep growing (all are saved): the sheet follows the
    newest, and the scrollbar or the mouse wheel over it scrolls back. Name the
    data set in the panel; Export CSV writes it to the data folder under that
    name. "+" adds a column; names, links and removing are in the panel.
    Ctrl+click selects it."""

    TYPE = "data_collector"
    MENU_LABEL = "Add data collector"
    NAME = "Data collector"
    CATEGORY = "Data"
    DOT_COLOR = (240, 200, 90)
    PROPS = [Prop("scale", "Size", "float", 1.0, min=0.25, max=50, unit="×",
                  prompt="Size (1 = normal, 2 = twice as big):"),
             Prop("color", "Color", "color", (240, 200, 90)),
             Prop("shown_rows", "Rows shown", "int", 8, min=1, max=60,
                  prompt="How many rows to show (the latest; all are kept):"),
             Prop("columns", kind="data", default=[], hidden=True),  # [{"cid", "name", "id", "key"}]
             Prop("rows", kind="data", default=[], hidden=True),     # [{"time": iso, "values": {cid: v}}]
             Prop("trigger", kind="data", default={}, hidden=True)]  # collect input: {"id", "key"}
    WIRE_COLOR = (120, 220, 150)
    COPY_SKIP = ("rows",)           # a copy (C key, or a placed machine) starts with no data

    @classmethod
    def create(cls, app):
        def make(x, y):
            used = {o.text for o in app.scene.objects if isinstance(o, DataCollector)}
            name = next(f"Data set {i}" for i in range(1, 1000) if f"Data set {i}" not in used)
            return cls(x, y, name, columns=[{"cid": 1, "name": "A"}, {"cid": 2, "name": "B"}])
        app.start_placing(make)

    @classmethod
    def icon_sample(cls):
        sample = cls(0, 0, columns=[{"cid": 1, "name": "power"}, {"cid": 2, "name": "counts"}])
        sample.rows = [{"time": "", "values": {"1": 1.5 * i, "2": 300 * i}} for i in range(1, 4)]
        sample.shown_rows = 3
        return sample

    def __init__(self, x, y, text="", *, columns=None, **kwargs):
        super().__init__(x, y, text, **kwargs)
        if columns is not None:
            self.columns = columns
        self._last_high = None      # the collect input last frame (None = not read yet: no edge)
        self._flash = 0.0           # seconds the Collect button stays lit after a collect
        self._top = None            # first row shown (0-based); None = follow the newest rows
        self._grab = None           # while dragging the scrollbar thumb: the click's offset in it

    @property
    def title(self):
        return self.text or "Data set"

    def rename(self, app):
        def submit(text):
            if text.strip():
                self.text = text.strip()
                app.save()
        app.ask("Data set name:", submit, value=self.title)

    # --- Sources and collecting -----------------------------------------------------
    def _source(self, link):
        return self.scene.by_id.get(link.get("id")) if link and link.get("id") and self.scene else None

    def column_value(self, col):
        src = self._source(col)
        return src.outputs().get(col.get("key")) if src is not None else None

    def trigger_value(self):
        src = self._source(self.trigger)
        return src.outputs().get(self.trigger.get("key")) if src is not None else None

    def collect(self, app=None):
        """Add a row: every column's value right now (None where it has none), and the time."""
        self.rows = self.rows + [{"time": datetime.now().isoformat(timespec="milliseconds"),
                                  "values": {str(c["cid"]): self.column_value(c) for c in self.columns}}]
        self._flash = 0.25
        if app is not None:
            app.save()
        elif self.scene is not None:
            self.scene.save()

    def tick(self, dt):
        self._flash = max(0.0, self._flash - dt)
        value = self.trigger_value()
        high = None if value is None else value > DC_EDGE
        if high and self._last_high is False:        # a rising edge (not just "high when loaded")
            self.collect()
        self._last_high = high if high is not None else self._last_high

    def outputs(self):
        return {"rows": float(len(self.rows))}

    # --- Editing --------------------------------------------------------------------------
    def _column(self, cid):
        return next((c for c in self.columns if c["cid"] == cid), None)

    def add_column(self, app):
        def named(text):
            cid = max((c["cid"] for c in self.columns), default=0) + 1
            name = text.strip() or chr(ord("A") + (cid - 1) % 26)
            self.columns = self.columns + [{"cid": cid, "name": name}]
            app.save()
            self.pick_source(app, cid)
        app.ask("New column name:", named)

    def rename_column(self, app, cid):
        col = self._column(cid)
        def submit(text):
            if text.strip():
                col["name"] = text.strip()
                app.save()
        app.ask("Column name:", submit, value=col["name"])

    def remove_column(self, app, cid):
        col = self._column(cid)
        def remove():
            self.columns = [c for c in self.columns if c["cid"] != cid]
            app.save()                 # its values stay in the saved rows, just not shown / exported
        app.confirm(f"Remove column '{col['name']}'?", "Its recorded values are no longer shown.",
                    remove, ok_label="Remove")

    def _pick(self, app, prompt, current, set_link, origin):
        def link(src):
            if src is self:                          # clicked itself: unlink
                set_link({})
            else:
                keys = list(src.outputs())
                set_link({"id": src.id, "key": "value" if "value" in keys else keys[0]})
            app.save()
        linked = self._source(current) is not None
        hint = " — or click this collector to unlink" if linked else ""
        app.start_picking(prompt + hint, link,
                          accept=lambda obj: (obj is self and linked) or (obj is not self and bool(obj.outputs())),
                          origin=origin)

    def pick_source(self, app, cid):
        col = self._column(cid)
        def set_link(link):
            col.pop("id", None)
            col.pop("key", None)
            col.update(link)
        self._pick(app, f"Click what column '{col['name']}' records (a readout, device, …)", col,
                   set_link, self.column_point(cid))

    def pick_trigger(self, app):
        def set_link(link):
            self.trigger = link
            self._last_high = None
        self._pick(app, "Click what triggers Collect (a push button, switch, …) — 0 → 1 collects",
                   self.trigger, set_link, self.trigger_point())

    def _next_key(self, app, link):
        src = self._source(link)
        keys = list(src.outputs()) if src is not None else []
        if keys:
            i = keys.index(link.get("key")) if link.get("key") in keys else -1
            link["key"] = keys[(i + 1) % len(keys)]
            if link is self.trigger:
                self._last_high = None
            app.save()

    def clear_rows(self, app):
        def clear():
            self.rows = []
            self._top = None
            app.save()
        app.confirm(f"Clear all {len(self.rows)} rows?", "The recorded data is deleted (export it first).",
                    clear, ok_label="Clear")

    def export_csv(self, app):
        os.makedirs(DATA_DIR, exist_ok=True)
        safe = "".join(ch if ch.isalnum() or ch in "-_ " else "_" for ch in self.title).strip() or "data"
        path = os.path.join(DATA_DIR, f"{safe} {datetime.now():%Y-%m-%d %H-%M-%S}.csv")
        with open(path, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(["row", "time"] + [c["name"] for c in self.columns])
            for i, row in enumerate(self.rows, 1):
                values = row.get("values", {})
                writer.writerow([i, row.get("time", "")] +
                                ["" if values.get(str(c["cid"])) is None else repr(values[str(c["cid"])])
                                 for c in self.columns])
        self.last_export = path
        app.confirm("Exported", f"{len(self.rows)} rows to {path}", lambda: None, ok_label="OK")

    # --- Scrolling ------------------------------------------------------------------------
    def first_shown(self):
        """Index of the first row on the sheet (the newest rows unless scrolled back)."""
        last = max(0, len(self.rows) - self.shown_rows)
        return last if self._top is None else max(0, min(last, self._top))

    def scroll_to(self, first):
        last = max(0, len(self.rows) - self.shown_rows)
        first = max(0, min(last, round(first)))
        self._top = None if first >= last else first     # at the bottom: follow new rows again

    def _thumb(self, track):
        """The scrollbar thumb's screen rect within the track rect."""
        n, shown = len(self.rows), self.shown_rows
        if n <= shown:
            return track.copy()
        h = max(min(track.height, round(track.width * 1.5)), round(track.height * shown / n))
        y = track.y + round((track.height - h) * self.first_shown() / (n - shown))
        return pygame.Rect(track.x, y, track.width, h)

    def _drag_scroll(self, y, track):
        n, shown = len(self.rows), self.shown_rows
        room = track.height - self._thumb(track).height
        if n > shown and room > 0:
            self.scroll_to((y - self._grab - track.y) / room * (n - shown))

    def on_wheel(self, pos, steps, app):
        """The wheel over the sheet scrolls its rows (when there are more than fit)."""
        if len(self.rows) <= self.shown_rows:
            return False
        boxes, _k = self._screen_boxes(self.screen_rect(app.camera))
        if not boxes["sheet"].union(boxes["scroll"]).collidepoint(pos):
            return False
        self.scroll_to(self.first_shown() - steps * DC_WHEEL_ROWS)
        return True

    # --- Panel ------------------------------------------------------------------------
    def inspector(self, app):
        rows = [Row("Data set", self.title, on_click=self.rename),
                Row("Rows", str(len(self.rows)))]
        src = self._source(self.trigger)
        rows.append(Row("Collect input", src.display_name if src else "— click to link (optional)",
                        on_click=self.pick_trigger))
        if src is not None:
            rows.append(Row("   reads", self.trigger.get("key"), on_click=lambda app: self._next_key(app, self.trigger)))
        for col in self.columns:
            cid = col["cid"]
            rows.append(Gap(6))
            rows.append(Row("Column", col["name"], on_click=lambda app, c=cid: self.rename_column(app, c)))
            src = self._source(col)
            rows.append(Row("   records", src.display_name if src else "— click to link",
                            on_click=lambda app, c=cid: self.pick_source(app, c)))
            if src is not None:
                value = self.column_value(col)
                rows.append(Row("   reads", f"{col.get('key')}  ({'—' if value is None else f'{value:.6g}'})",
                                on_click=lambda app, c=col: self._next_key(app, c)))
            rows.append(Row("   remove", "✕", on_click=lambda app, c=cid: self.remove_column(app, c)))
        return rows + [Gap(6)] + self.prop_rows()

    def buttons(self, app):
        out = super().buttons(app) + [Btn("Collect", self.collect, style="go"),
                                      Btn("Add column…", self.add_column)]
        if self.rows:
            out += [Btn("Export CSV", self.export_csv), Btn("Clear rows…", self.clear_rows, style="stop")]
        return out

    # --- Layout (world units at Size 1 from the top-left; shared by drawing, clicks, wires) --
    def _layout_w(self):
        return (DC_CHIP_W + DC_PAD + DC_NUM_W + DC_TIME_W + DC_COL_W * len(self.columns) + DC_ADD_W
                + DC_SCROLL_W + DC_PAD)

    @property
    def world_width(self):
        return max(DC_CHIP_W + DC_PAD + DC_BUTTON_W * 2.4 + DC_PAD, self._layout_w()) * self.scale

    @property
    def world_height(self):
        return (DC_PAD + DC_TITLE_H + DC_CELL_H * (1 + self.shown_rows) + DC_PAD) * self.scale

    def _to_world(self, x, y):
        c = self.world_center
        return (c.x - self.world_width / 2 + x * self.scale, c.y - self.world_height / 2 + y * self.scale)

    def _boxes(self):
        """{name: (x, y, w, h)} at Size 1: 'chip', 'button', 'add', 'scroll' (the scrollbar
        track), 'sheet' (header + rows), and each column header by cid."""
        table_x = DC_CHIP_W + DC_PAD
        head_y = DC_PAD + DC_TITLE_H
        width = self.world_width / self.scale
        boxes = {"chip": (DC_PAD * 0.4, DC_PAD + 8, DC_CHIP_W - DC_PAD * 0.8, DC_TITLE_H - 16),
                 "button": (width - DC_PAD - DC_BUTTON_W, DC_PAD + 6, DC_BUTTON_W, DC_TITLE_H - 12)}
        x = table_x + DC_NUM_W + DC_TIME_W
        for col in self.columns:
            boxes[col["cid"]] = (x, head_y, DC_COL_W, DC_CELL_H)
            x += DC_COL_W
        boxes["add"] = (x, head_y, DC_ADD_W, DC_CELL_H)
        body_h = DC_CELL_H * self.shown_rows
        boxes["scroll"] = (x + DC_ADD_W, head_y + DC_CELL_H, DC_SCROLL_W, body_h)
        boxes["sheet"] = (table_x, head_y, x + DC_ADD_W - table_x, DC_CELL_H + body_h)
        return boxes

    def column_point(self, cid):
        """World point a column's wire ends at: the top of its header."""
        x, y, w, _h = self._boxes().get(cid, (0, 0, 0, 0))
        return self._to_world(x + w / 2, y)

    def trigger_point(self):
        x, y, _w, h = self._boxes()["chip"]
        return self._to_world(x, y + h / 2)

    def _screen_boxes(self, rect):
        k = rect.width / (self.world_width / self.scale)
        return {name: pygame.Rect(rect.x + round(x * k), rect.y + round(y * k), max(1, round(w * k)),
                                  max(1, round(h * k)))
                for name, (x, y, w, h) in self._boxes().items()}, k

    def on_click(self, pos, app):
        boxes, _k = self._screen_boxes(self.screen_rect(app.camera))
        track = boxes["scroll"]
        if track.inflate(4, 0).collidepoint(pos):
            thumb = self._thumb(track)
            # On the thumb: drag it from where it was grabbed. On the track: jump there.
            self._grab = pos[1] - thumb.y if thumb.collidepoint(pos) else thumb.height // 2
            self._drag_scroll(pos[1], track)
            return True                                  # the loop sends the drag here
        hit = next((name for name, r in boxes.items()
                    if name not in ("sheet", "scroll") and r.inflate(4, 4).collidepoint(pos)), None)
        if hit == "button":
            self.collect(app)
        elif hit == "chip":
            self.pick_trigger(app)
        elif hit == "add":
            self.add_column(app)
        elif hit is not None:
            self.pick_source(app, hit)
        return hit is not None

    def on_drag(self, pos, app):
        if self._grab is not None:
            boxes, _k = self._screen_boxes(self.screen_rect(app.camera))
            self._drag_scroll(pos[1], boxes["scroll"])

    def on_release(self, pos, app):
        self._grab = None

    # --- Drawing ----------------------------------------------------------------------
    def draw_links(self, surface, camera):
        if self.scene is None:
            return
        for col in self.columns:
            src = self._source(col)
            if src is not None and src.center is not None:
                draw_wire(surface, camera, output_point(src), self.column_point(col["cid"]), self.WIRE_COLOR)
        src = self._source(self.trigger)
        if src is not None and src.center is not None:
            draw_wire(surface, camera, output_point(src), self.trigger_point(), self.color)

    def draw_shape(self, surface, rect, camera):
        s = min(rect.width, rect.height)
        radius = max(2, s // 14)
        draw_rect(surface, (14, 14, 18), rect.move(0, max(1, s // 40)), border_radius=radius)
        draw_rect(surface, INPUT_HOUSING, rect, border_radius=radius)
        draw_rect(surface, shade(INPUT_HOUSING, 1.35), rect, width=max(1, s // 80), border_radius=radius)
        boxes, k = self._screen_boxes(rect)
        if DC_CELL_H * k < 6:                         # too small for a table: a grid hint
            inner = rect.inflate(-max(2, s // 6), -max(2, s // 6))
            draw_rect(surface, (238, 238, 232), inner)
            return
        mouse = pygame.mouse.get_pos() if self.is_hovered else None
        text = round(DC_TEXT * k)
        font = get_font(text, "consolas", True) if text >= 7 else None

        # Title bar: the collect input chip, the name and row count, the Collect button.
        chip = boxes["chip"]
        linked = self._source(self.trigger) is not None
        hot = mouse is not None and chip.inflate(4, 4).collidepoint(mouse)
        color = shade(self.color, 1.2) if hot else self.color
        cr = max(2, chip.height // 2)
        if linked:
            draw_rect(surface, color, chip, border_radius=cr)
        else:
            draw_rect(surface, INPUT_WELL, chip, border_radius=cr)
            draw_rect(surface, color, chip, width=max(1, round(2 * k)), border_radius=cr)
        if font is not None:
            ts = font.render(clip_text("collect", font, chip.width - 4), True, INPUT_WELL if linked else color)
            surface.blit(ts, ts.get_rect(center=chip.center))
        button = boxes["button"]
        lit = self._flash > 0
        hot = mouse is not None and button.collidepoint(mouse)
        draw_rect(surface, shade(self.color, 1.25) if lit else shade(self.color, 0.85 if hot else 0.65),
                  button, border_radius=max(2, button.height // 4))
        if font is not None:
            ts = get_font(round(text * 1.1), "bahnschrift").render("Collect", True, (24, 22, 16))
            surface.blit(ts, ts.get_rect(center=button.center))
        table_x = boxes[self.columns[0]["cid"]].x - round((DC_NUM_W + DC_TIME_W) * k) if self.columns \
            else boxes["add"].x - round((DC_NUM_W + DC_TIME_W) * k)
        title_size = round(DC_TEXT * 1.3 * k)
        if title_size >= 7:
            tfont = get_font(title_size, "bahnschrift")
            n = len(self.rows)
            if self._top is not None:                # scrolled back: which rows are showing
                a = self.first_shown() + 1
                count = f"rows {a}-{min(n, a + self.shown_rows - 1)} of {n}"
            else:
                count = f"{n} row{'' if n == 1 else 's'}"
            ts = tfont.render(clip_text(f"{self.title}  ·  {count}", tfont,
                                        button.x - table_x - round(DC_PAD * k)), True, (226, 228, 236))
            surface.blit(ts, ts.get_rect(midleft=(table_x, button.centery)))

        # The sheet.
        head = boxes["add"]
        sheet = boxes["sheet"]
        draw_rect(surface, (244, 244, 238), sheet)
        cell_h = head.height
        header = pygame.Rect(sheet.x, head.y, sheet.width, cell_h)
        draw_rect(surface, (214, 216, 222), header)
        line = (190, 192, 198)
        cols = [("#", DC_NUM_W, None), ("time", DC_TIME_W, None)] + \
               [(c["name"], DC_COL_W, c) for c in self.columns]
        x = sheet.x
        lines_x = []
        for name, w, col in cols:
            cell = pygame.Rect(x, head.y, round(w * k), cell_h)
            if col is not None:
                hot = mouse is not None and cell.collidepoint(mouse)
                if hot:
                    draw_rect(surface, (198, 204, 220), cell)
                dot_r = max(2, round(4 * k))
                dot = (cell.right - dot_r * 2 - 1, cell.centery)
                if self._source(col) is not None:
                    pygame.draw.circle(surface, (60, 170, 90), dot, dot_r)
                else:
                    pygame.draw.circle(surface, (150, 152, 160), dot, dot_r, max(1, dot_r // 2))
            if font is not None:
                room = cell.width - round(6 * k) - (round(12 * k) if col is not None else 0)
                ts = font.render(clip_text(name, font, room), True, (40, 42, 50))
                surface.blit(ts, ts.get_rect(midleft=(cell.x + round(4 * k), cell.centery)))
            x = cell.right
            lines_x.append(x)
        add = boxes["add"]
        hot = mouse is not None and add.collidepoint(mouse)
        draw_rect(surface, (198, 204, 220) if hot else (226, 228, 234), add)
        if font is not None:
            ts = get_font(round(text * 1.3), "consolas", True).render("+", True, (70, 74, 90))
            surface.blit(ts, ts.get_rect(center=add.center))

        # The latest rows, oldest at the top.
        start = self.first_shown()
        shown = self.rows[start:start + self.shown_rows]
        first = start + 1
        y = header.bottom
        for i, row in enumerate(shown):
            r = pygame.Rect(sheet.x, y, sheet.width, cell_h)
            if (first + i) % 2 == 0:
                draw_rect(surface, (232, 234, 238), r)
            if font is not None:
                values = row.get("values", {})
                cells = [str(first + i), row.get("time", "")[11:19]] + \
                        ["—" if values.get(str(c["cid"])) is None else f"{values[str(c['cid'])]:.6g}"
                         for c in self.columns]
                x = sheet.x
                for (name, w, col), value in zip(cols, cells):
                    cw = round(w * k)
                    ts = font.render(clip_text(value, font, cw - round(6 * k)), True,
                                     (120, 122, 132) if col is None else (24, 26, 32))
                    if col is None:
                        surface.blit(ts, ts.get_rect(midleft=(x + round(4 * k), r.centery)))
                    else:
                        surface.blit(ts, ts.get_rect(midright=(x + cw - round(4 * k), r.centery)))
                    x += cw
            y += cell_h
        for i in range(self.shown_rows + 1):        # grid
            gy = header.bottom + i * cell_h
            if gy <= sheet.bottom:
                pygame.draw.line(surface, line, (sheet.x, gy), (sheet.right - 1, gy), 1)
        for gx in lines_x:
            pygame.draw.line(surface, line, (gx, sheet.y), (gx, sheet.bottom - 1), 1)
        draw_rect(surface, (120, 122, 130), sheet, width=1)

        # The scrollbar: a thumb sized to the share of rows on the sheet.
        track = boxes["scroll"]
        draw_rect(surface, INPUT_WELL, track, border_radius=max(1, track.width // 2))
        if len(self.rows) > self.shown_rows:
            thumb = self._thumb(track).inflate(-max(2, track.width // 4), -2)
            hot = self._grab is not None or (mouse is not None and track.collidepoint(mouse))
            draw_rect(surface, shade(self.color, 1.15 if hot else 0.8), thumb,
                      border_radius=max(1, thumb.width // 2))
        if not self.rows and font is not None:
            hint = "press Collect to add a row" if self.columns else "click + to add a column"
            ts = font.render(clip_text(hint, font, sheet.width - 8), True, (140, 142, 150))
            at = ts.get_rect(center=(sheet.centerx, header.bottom + cell_h))
            draw_rect(surface, (244, 244, 238), at.inflate(round(8 * k), 2))
            surface.blit(ts, at)


# ---- Data relay: a junction that routes one value -------------------------------------
RELAY_BODY = (48, 52, 62)
RELAY_CORE = (120, 220, 150)       # the same green as the wires
RELAY_IDLE = (110, 114, 126)       # unlinked
RELAY_NO_VALUE = (240, 190, 60)    # linked, but its source has no value
RELAY_LOOP = (235, 80, 80)         # its chain of relays comes back to itself
RELAY_BLINK = 0.35                 # seconds the core flares after the value changes
RELAY_READERS_EVERY = 0.25         # how often it re-counts what reads from it
_RELAYS_RESOLVING = set()          # relay ids mid-lookup (a loop guard for outputs())


def links_to(obj, target_id):
    """True if obj reads from target_id: an Output's source, or any {"id": …} link
    in its data props (a function's variables, a device's params, a collector's columns)."""
    def has(value):
        if isinstance(value, dict):
            return value.get("id") == target_id or any(has(v) for v in value.values())
        if isinstance(value, list):
            return any(has(v) for v in value)
        return False
    for p in obj.props():
        value = getattr(obj, p.key, None)
        if (p.key == "source_id" and value == target_id) or (p.kind == "data" and has(value)):
            return True
    return False


class DataRelay(Thing):
    """A small junction for routing one value across the canvas: click it, then
    a source (an excitation filter's center, a readout, another relay, …), and
    it passes that value on as `value`. Link anything to it — a device param, a
    function variable, a collector column, another relay — and as many things
    as you like, so the value can travel along a path of relays and split. The
    core flares when the value changes. No delay along a chain;
    a chain that loops back on itself has no value (red core). Ctrl+click
    selects it."""

    TYPE = "data_relay"
    MENU_LABEL = "Add data relay"
    NAME = "Data relay"
    CATEGORY = "Data"
    SIZE = (34, 34)
    DOT_COLOR = RELAY_CORE
    PROPS = [Prop("scale", "Size", "float", 1.0, min=0.25, max=50, unit="×",
                  prompt="Size (1 = normal, 2 = twice as big):"),
             Prop("source", kind="data", default={}, hidden=True)]     # {"id", "key"}
    WIRE_COLOR = RELAY_CORE

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._last = None           # the value last frame (for the flare)
        self._blink = 0.0
        self._readers = []          # objects that read from this relay (re-counted every so often)
        self._recount = 0.0
        self._loop = False

    # --- The value ------------------------------------------------------------------
    def source_obj(self):
        sid = self.source.get("id")
        return self.scene.by_id.get(sid) if sid and self.scene else None

    def value(self):
        """The source's value now, read straight through any chain of relays (so no
        frame of delay per hop). None when unlinked, when the source has none, or in a loop."""
        src = self.source_obj()
        if src is None or self.id in _RELAYS_RESOLVING:
            return None
        _RELAYS_RESOLVING.add(self.id)
        try:
            return src.outputs().get(self.source.get("key"))
        finally:
            _RELAYS_RESOLVING.discard(self.id)

    def outputs(self):
        return {"value": self.value()}     # always offered, so it can be linked before it's fed

    def _in_loop(self):
        seen, relay = set(), self
        while isinstance(relay, DataRelay):
            if relay.id in seen:
                return relay is self             # a loop further up just leaves it without a value
            seen.add(relay.id)
            relay = relay.source_obj()
        return False

    def tick(self, dt):
        value = self.value()
        if value != self._last and value is not None:
            self._blink = RELAY_BLINK
        self._last = value
        self._blink = max(0.0, self._blink - dt)
        self._recount -= dt
        if self._recount <= 0 and self.scene is not None:
            self._recount = RELAY_READERS_EVERY
            self._readers = [o for o in self.scene.objects if o is not self and links_to(o, self.id)]
            self._loop = self._in_loop()

    # --- Linking ------------------------------------------------------------------------
    def pick_source(self, app):
        linked = self.source_obj() is not None
        def link(src):
            if src is self:                          # clicked itself: unlink
                self.source = {}
            else:
                keys = list(src.outputs())
                self.source = {"id": src.id, "key": "value" if "value" in keys else keys[0]}
            self._recount = 0.0
            app.save()
        what = self.NAME.lower()
        hint = f" — or click this {what} to unlink" if linked else ""
        app.start_picking(f"Click what this {what} passes on (a device, readout, relay, …){hint}", link,
                          accept=lambda obj: (obj is self and linked) or (obj is not self and bool(obj.outputs())),
                          origin=self.input_point())

    def next_key(self, app):
        src = self.source_obj()
        keys = list(src.outputs()) if src is not None else []
        if keys:
            key = self.source.get("key")
            self.source["key"] = keys[(keys.index(key) + 1) % len(keys) if key in keys else 0]
            app.save()

    def unlink(self, app):
        self.source = {}
        app.save()

    def on_click(self, pos, app):
        self.pick_source(app)
        return True

    def inspector(self, app):
        src = self.source_obj()
        value = self.value()
        rows = [Row("Passes on", src.display_name if src else "— click to link", on_click=self.pick_source)]
        if src is not None:
            rows.append(Row("   reads", self.source.get("key"), on_click=self.next_key))
        rows.append(Row("Value", "—" if value is None else f"{value:.6g}",
                        color=RELAY_LOOP if self._loop else None if value is not None else WARN_COLOR))
        if self._loop:
            rows.append(Field("Problem", "this chain of relays loops back on itself", color=WARN_COLOR))
        names = ", ".join(o.display_name for o in self._readers[:4]) + (" …" if len(self._readers) > 4 else "")
        rows.append(Row("Feeds", f"{len(self._readers)}" + (f"  ({names})" if names else "")))
        return rows + [Gap(6)] + self.prop_rows()

    def buttons(self, app):
        out = super().buttons(app)
        if self.source_obj() is not None:
            out.append(Btn("Unlink", self.unlink))
        return out

    # --- Drawing ----------------------------------------------------------------------
    def input_point(self):
        """World point the incoming wire ends at."""
        return self.center

    def draw_links(self, surface, camera):
        src = self.source_obj()
        if src is not None and src.center is not None:
            draw_wire(surface, camera, output_point(src), self.input_point(), self.WIRE_COLOR)

    def _state_color(self, value):
        if self.source_obj() is None:
            return RELAY_IDLE
        if self._loop:
            return RELAY_LOOP
        return RELAY_NO_VALUE if value is None else RELAY_CORE

    def draw_shape(self, surface, rect, camera):
        value = self._last
        color = self._state_color(value)
        c = pygame.Vector2(rect.center)
        r = min(rect.width, rect.height) / 2
        if r < 5:                                   # tiny: just the core
            pygame.draw.circle(surface, color, c, max(1, r))
            return

        def hexagon(radius, offset=(0, 0)):
            return [(c.x + offset[0] + radius * math.cos(math.radians(60 * i)),
                     c.y + offset[1] + radius * math.sin(math.radians(60 * i))) for i in range(6)]

        src = self.source_obj()
        # The body: a hex nut with a bevel, rivets on the corners, and a well for the core.
        pygame.draw.polygon(surface, (14, 14, 18), hexagon(r * 0.95, (0, max(1, r * 0.08))))
        pygame.draw.polygon(surface, RELAY_BODY, hexagon(r * 0.95))
        pygame.draw.polygon(surface, shade(RELAY_BODY, 1.45), hexagon(r * 0.95), max(1, round(r * 0.07)))
        pygame.draw.polygon(surface, (18, 20, 24), hexagon(r * 0.6))
        pygame.draw.polygon(surface, shade(RELAY_BODY, 0.75), hexagon(r * 0.6), max(1, round(r * 0.05)))
        if r >= 10:
            for x, y in hexagon(r * 0.78):
                pygame.draw.circle(surface, shade(RELAY_BODY, 1.7), (x, y), max(1, r * 0.07))

        # The core: hollow when unlinked; lit (and flaring when the value changes) when it carries one.
        core_r = r * 0.34
        if src is None:
            pygame.draw.circle(surface, color, c, core_r, max(1, round(r * 0.08)))
        else:
            flare = self._blink / RELAY_BLINK
            if value is not None and r >= 8:
                pygame.draw.circle(surface, shade(color, 0.25 + 0.3 * flare), c, core_r * (1.45 + 0.35 * flare))
            pygame.draw.circle(surface, shade(color, 0.85 + 0.35 * flare), c, core_r)
            pygame.draw.circle(surface, shade(color, 1.6), c - (core_r * 0.3, core_r * 0.3), max(1, core_r * 0.3))


# ---- Data tag: a named, displayed relay -----------------------------------------------
TAG_W, TAG_H = 270, 96             # world size at Size 1
TAG_TIP = 26                       # how far the pointed left end reaches in
TAG_HOLE = (17, TAG_H / 2, 6.5)    # the punched hole the wire ties into: x, y, radius
TAG_PAD = 12
TAG_NAME_H = 30                    # the name row
TAG_STREAM = (TAG_W - TAG_PAD - 12, TAG_PAD + TAG_NAME_H / 2, 11)   # the streaming icon: x, y, radius
TAG_STREAM_WIRE = (240, 200, 90)   # the wire from what turns streaming on
TAG_HELD = (128, 132, 146)         # the icon and label while holding


class DataTag(DataRelay):
    """A named data point: a relay you can read. Shaped like a luggage tag —
    the incoming wire ties into the hole in its pointed end, and its name is
    printed on it ("Time Tagger output"), so a value carried far across the
    canvas still says what it is. It shows the value the way a Function does
    (6 significant digits, shrinking to fit) with your unit beside it, and
    passes it on as `value` — link anything to it, as many as you like, with
    no delay. Click the name to rename it; click anywhere else to choose its
    source. Ctrl+click selects it.

    Streaming: the ((•)) icon. Unlinked, the tag streams — live, always. Click
    it, then a button or switch, and it streams only while that's on and
    otherwise HOLDS the last value it took (saved with the canvas): a push
    button takes one fresh reading per press, a switch streams while it's on.
    Click the icon, then the tag itself, to go back to always streaming."""

    TYPE = "data_tag"
    MENU_LABEL = "Add data tag"
    NAME = "Data tag"
    SIZE = (TAG_W, TAG_H)
    DOT_COLOR = (110, 200, 255)
    PROPS = [Prop("unit", "Unit", "str", "", prompt="Unit to show after the number (e.g. counts/s, nm, mW):"),
             Prop("color", "Color", "color", (110, 200, 255)),
             Prop("stream", kind="data", default={}, hidden=True),    # what turns streaming on: {"id", "key"}
             Prop("held", kind="data", default=None, hidden=True)]    # the last value taken (shown while off)

    @classmethod
    def create(cls, app):
        def make(x, y):
            used = {o.text for o in app.scene.objects if isinstance(o, DataTag)}
            return cls(x, y, next(f"Tag {i}" for i in range(1, 1000) if f"Tag {i}" not in used))
        app.start_placing(make)

    @classmethod
    def icon_sample(cls):
        sample = cls(0, 0, "Power")
        sample.unit = "mW"
        sample._last = 2.513
        sample._icon = True                          # drawn as linked, with that value
        return sample

    _icon = False
    _was_streaming = True

    @property
    def title(self):
        return self.text or "Data tag"

    # --- Streaming ------------------------------------------------------------------------
    def stream_source(self):
        sid = self.stream.get("id")
        return self.scene.by_id.get(sid) if sid and self.scene else None

    def streaming(self):
        """True while the tag passes its source's value live: always when nothing is
        linked to its streaming icon, else while that source is on (> 0.5)."""
        src = self.stream_source()
        guard = self.id + "#stream"
        if src is None or guard in _RELAYS_RESOLVING:
            return True
        _RELAYS_RESOLVING.add(guard)
        try:
            on = src.outputs().get(self.stream.get("key"))
        finally:
            _RELAYS_RESOLVING.discard(guard)
        return on is not None and on > DC_EDGE

    def value(self):
        """Live while streaming (and remembered); otherwise the value it last took."""
        if self._icon:
            return self._last
        if self.streaming():
            self.held = super().value()
        return self.held

    def tick(self, dt):
        super().tick(dt)
        streaming = self.streaming()
        if self._was_streaming and not streaming and self.scene is not None:
            self.scene.save()                         # a reading was just taken: keep it
        self._was_streaming = streaming

    def pick_stream(self, app):
        linked = self.stream_source() is not None
        def link(src):
            if src is self:                          # clicked itself: always stream again
                self.stream = {}
            else:
                keys = list(src.outputs())
                key = next((k for k in ("pressed", "on", "value") if k in keys), keys[0])
                self.stream = {"id": src.id, "key": key}
            self._recount = 0.0
            app.save()
        hint = " — or click this tag to always stream" if linked else ""
        app.start_picking("Click what turns streaming on (a push button: one reading per press; "
                          f"a switch: live while on){hint}", link,
                          accept=lambda obj: (obj is self and linked) or (obj is not self and bool(obj.outputs())),
                          origin=self.stream_point())

    def next_stream_key(self, app):
        src = self.stream_source()
        keys = list(src.outputs()) if src is not None else []
        if keys:
            key = self.stream.get("key")
            self.stream["key"] = keys[(keys.index(key) + 1) % len(keys) if key in keys else 0]
            app.save()

    def always_stream(self, app):
        self.stream = {}
        app.save()

    def rename(self, app):
        def submit(text):
            if text.strip():
                self.text = text.strip()
                app.save()
        app.ask("Name:", submit, value=self.title)

    # --- Layout (world units at Size 1 from the top-left) ---------------------------------
    def _to_world(self, x, y):
        c = self.world_center
        return (c.x - self.world_width / 2 + x * self.scale, c.y - self.world_height / 2 + y * self.scale)

    def input_point(self):
        return self._to_world(TAG_HOLE[0], TAG_HOLE[1])

    def output_point(self):
        return self._to_world(TAG_W, TAG_H / 2)

    def stream_point(self):
        return self._to_world(TAG_STREAM[0], TAG_STREAM[1])

    def _stream_rect(self, rect):
        k = rect.width / TAG_W
        x, y, r = TAG_STREAM
        return pygame.Rect(round(rect.x + (x - r * 1.4) * k), round(rect.y + (y - r * 1.4) * k),
                           round(r * 2.8 * k), round(r * 2.8 * k))

    def _name_rect(self, rect):
        k = rect.width / TAG_W
        return pygame.Rect(rect.x + round(TAG_TIP * 1.3 * k), rect.y, round((TAG_W - TAG_TIP * 1.3) * k),
                           round((TAG_PAD + TAG_NAME_H) * k))

    def on_click(self, pos, app):
        rect = self.screen_rect(app.camera)
        if self._stream_rect(rect).collidepoint(pos):
            self.pick_stream(app)
        elif self._name_rect(rect).collidepoint(pos):
            self.rename(app)
        else:
            self.pick_source(app)
        return True

    def inspector(self, app):
        rows = [Row("Name", self.title, on_click=self.rename)]
        src = self.stream_source()
        if src is None:
            rows.append(Row("Streaming", "live — click to link a button", on_click=self.pick_stream))
        else:
            rows.append(Row("Streaming", f"{src.display_name} · {'on' if self.streaming() else 'off, holding'}",
                            on_click=self.pick_stream))
            rows.append(Row("   reads", self.stream.get("key"), on_click=self.next_stream_key))
        return rows + super().inspector(app)

    def buttons(self, app):
        out = super().buttons(app)
        if self.stream_source() is not None:
            out.append(Btn("Always stream", self.always_stream))
        return out

    def draw_links(self, surface, camera):
        super().draw_links(surface, camera)
        src = self.stream_source()
        if src is not None and src.center is not None:
            draw_wire(surface, camera, output_point(src), self.stream_point(), TAG_STREAM_WIRE)

    # --- Drawing ----------------------------------------------------------------------
    def draw_shape(self, surface, rect, camera):
        value = self._last
        k = rect.width / TAG_W
        src = self if self._icon else self.source_obj()
        state = RELAY_CORE if self._icon else self._state_color(value)

        def pt(x, y):
            return (rect.x + x * k, rect.y + y * k)

        outline = [pt(TAG_TIP, 0), pt(TAG_W, 0), pt(TAG_W, TAG_H), pt(TAG_TIP, TAG_H),
                   pt(0, TAG_H - TAG_TIP), pt(0, TAG_TIP)]
        shadow = [(x, y + max(1, 3 * k)) for x, y in outline]
        pygame.draw.polygon(surface, (14, 14, 18), shadow)
        pygame.draw.polygon(surface, INPUT_HOUSING, outline)
        if rect.height < 16:                        # tiny: just the tag shape
            return
        # A stripe of the tag's color down the pointed end, then the rim.
        stripe = [pt(TAG_TIP, 0), pt(TAG_TIP * 1.3, 0), pt(TAG_TIP * 1.3, TAG_H), pt(TAG_TIP, TAG_H),
                  pt(0, TAG_H - TAG_TIP), pt(0, TAG_TIP)]
        pygame.draw.polygon(surface, shade(self.color, 0.55), stripe)
        pygame.draw.polygon(surface, shade(INPUT_HOUSING, 1.35), outline, max(1, round(2 * k)))
        # The punched hole with a metal grommet; it glows with the relay's state color.
        hx, hy, hr = TAG_HOLE
        hole = pygame.Vector2(pt(hx, hy))
        pygame.draw.circle(surface, (196, 200, 210), hole, hr * k * 1.45)
        pygame.draw.circle(surface, (120, 124, 134), hole, hr * k * 1.45, max(1, round(k)))
        pygame.draw.circle(surface, (12, 12, 15), hole, hr * k)
        if src is not None:
            flare = self._blink / RELAY_BLINK
            pygame.draw.circle(surface, shade(state, 0.7 + 0.5 * flare), hole, max(1, hr * k * 0.55))
        # The output nub on the right edge.
        nub_on = value is not None
        pygame.draw.circle(surface, self.color if nub_on else shade(self.color, 0.4),
                           pt(TAG_W, TAG_H / 2), max(2, round(5 * k)))

        # The name.
        name = self._name_rect(rect)
        size = round(TAG_NAME_H * 0.62 * k)
        if size >= 7:
            font = get_font(size, "bahnschrift")
            room = round((TAG_STREAM[0] - TAG_STREAM[2] * 1.6) * k) - (name.x - rect.x)   # up to the icon
            ts = font.render(clip_text(self.title, font, room), True, (226, 228, 236))
            surface.blit(ts, ts.get_rect(midleft=(name.x, rect.y + round((TAG_PAD + TAG_NAME_H / 2) * k))))

        # The streaming icon, ((•)): lit while streaming, grey while holding; in a well once linked.
        live = True if self._icon else self.streaming()
        stream_linked = self.stream_source() is not None
        sx, sy, sr = TAG_STREAM
        center, radius = pygame.Vector2(pt(sx, sy)), sr * k
        if radius >= 4:
            if stream_linked:
                pygame.draw.circle(surface, INPUT_WELL, center, radius * 1.3)
                pygame.draw.circle(surface, TAG_STREAM_WIRE if live else TAG_HELD, center, radius * 1.3,
                                   max(1, round(1.5 * k)))
            ink = RELAY_CORE if live else TAG_HELD
            pygame.draw.circle(surface, ink, center, max(1, radius * 0.22))
            width = max(1, round(radius * 0.17))
            pulse = 0.5 + 0.5 * math.sin(pygame.time.get_ticks() / 1000.0 * 5) if live else 1.0
            for i, rr in enumerate((radius * 0.55, radius * 0.92)):
                arc_rect = pygame.Rect(0, 0, round(2 * rr), round(2 * rr))
                arc_rect.center = (round(center.x), round(center.y))
                color = shade(ink, 0.55 + 0.45 * pulse) if live and i == 1 else ink
                pygame.draw.arc(surface, color, arc_rect, -0.75, 0.75, width)
                pygame.draw.arc(surface, color, arc_rect, math.pi - 0.75, math.pi + 0.75, width)

        # The screen: the value (Function-style digits, shrinking to fit) and the unit.
        screen = pygame.Rect(rect.x + round(TAG_TIP * 1.3 * k), rect.y + round((TAG_PAD + TAG_NAME_H) * k),
                             round((TAG_W - TAG_TIP * 1.3 - TAG_PAD) * k),
                             round((TAG_H - TAG_PAD * 2 - TAG_NAME_H) * k))
        corner = max(2, round(6 * k))
        draw_rect(surface, shade(self.color, 0.07), screen, border_radius=corner)
        draw_rect(surface, (8, 8, 10), screen, width=max(1, round(2 * k)), border_radius=corner)
        pad = round(8 * k)
        if src is None or self._loop:
            text = "click to link" if src is None else "loop"
            size = round(13 * k)
            if size >= 7:
                font = get_font(size, "consolas", True)
                ts = font.render(clip_text(text, font, screen.width - 2 * pad), True,
                                 (130, 132, 144) if src is None else RELAY_LOOP)
                surface.blit(ts, ts.get_rect(midleft=(screen.x + pad, screen.centery)))
            return
        number = "—" if value is None else f"{value:.6g}"
        color = shade(self.color, 1.2 if live else 0.85) if value is not None else WARN_COLOR
        held_w = 0
        if not live:                                 # holding: say so, at the left of the screen
            size = round(screen.height * 0.3)
            if size >= 7:
                hs = get_font(size, "bahnschrift").render("HELD", True, TAG_HELD)
                surface.blit(hs, hs.get_rect(midleft=(screen.x + pad, screen.centery)))
                held_w = hs.get_width() + pad
        unit_w = 0
        usize = round(screen.height * 0.42)
        if self.unit and usize >= 7:
            us = get_font(usize, "consolas", True).render(self.unit, True, shade(self.color, 0.85))
            if us.get_width() < screen.width * 0.45:
                unit_w = us.get_width() + round(5 * k)
                surface.blit(us, us.get_rect(midright=(screen.right - pad, screen.centery + round(2 * k))))
        size = round(screen.height * 0.62)
        room = screen.width - 2 * pad - unit_w - held_w
        while size > 7 and get_font(size, "consolas", True).size(number)[0] > room:
            size = max(7, int(size * 0.9))
        if size >= 7:
            ts = get_font(size, "consolas", True).render(number, True, color)
            surface.blit(ts, ts.get_rect(midright=(screen.right - pad - unit_w, screen.centery)))


# ============================================================================
# ANNOTATIONS — things that label and organize the canvas (E menu ▸ Annotations)
# ============================================================================
LABEL_BASE_FONT = 40        # text size (world units) at Size 1
LABEL_FONTS = {             # Font choice -> (system font name, bold)
    "Bahnschrift": ("bahnschrift", False),
    "Segoe UI": ("segoeuisemibold", False),
    "Georgia": ("georgia", True),
    "Consolas": ("consolas", True),
    "Default": (None, False),
}
LABEL_MAX_FONT = 480        # don't render bigger than this (px) when zoomed way in
LABEL_CACHE_PIXELS = 25_000_000   # rendered label text kept for reuse (~100 MB); emptied past this


LABEL_STYLES = {            # frame Style -> padding (left, right, top, bottom) as fractions of text height
    "plate": (0.83, 0.55, 0.32, 0.32),
    "bevel": (0.6, 0.6, 0.3, 0.46),
    "inset": (0.7, 0.7, 0.42, 0.42),
    "ribbon": (0.95, 0.95, 0.26, 0.38),
    "neon": (0.65, 0.65, 0.36, 0.36),
    "brackets": (0.55, 0.55, 0.3, 0.3),
    "double": (0.7, 0.7, 0.42, 0.42),
    "outline": (0.55, 0.55, 0.32, 0.32),
    "underline": (0.1, 0.1, 0.12, 0.38),
}
LABEL_EFFECTS = ["flat", "shadow", "raised", "engraved", "3D", "glow"]


def _shift_diagonal(a, s):
    """An [x, y] array moved s px down-right, zeros moved in (for 3D text)."""
    out = np.zeros_like(a)
    out[s:, s:] = a[:a.shape[0] - s, :a.shape[1] - s]
    return out


class Label(Thing):
    """A title for a region or group — "CONTROL PANEL". Unlike a name tag it
    lives in the world: it scales with zoom like a node, and Size makes it
    bigger or smaller. Its width follows its text. Two independent looks to
    click through: the frame Style (LABEL_STYLES) and the text Effect
    (LABEL_EFFECTS). Spaced capitals by default."""

    TYPE = "label"
    MENU_LABEL = "Add label"
    NAME = "Label"
    CATEGORY = "Annotations"
    DOT_COLOR = (110, 200, 225)
    PROPS = [Prop("scale", "Size", "float", 1.0, min=0.1, max=100, unit="×",
                  prompt="Size (1 = normal, 2 = twice as big):"),
             Prop("style", "Style", "choice", "plate", choices=list(LABEL_STYLES)),
             Prop("effect", "Text effect", "choice", "shadow", choices=list(LABEL_EFFECTS)),
             Prop("font", "Font", "choice", "Bahnschrift", choices=list(LABEL_FONTS)),
             Prop("caps", "Spaced capitals", "bool", True),
             Prop("color", "Accent color", "color", (110, 200, 225))]

    _cache = {}             # (text, px, font, caps, color[, "glow" / "3D"]) -> rendered surface
    _cache_pixels = 0       # their total size, kept under LABEL_CACHE_PIXELS

    @classmethod
    def create(cls, app):
        def named(text):
            text = text.strip() or "Label"
            app.start_placing(lambda x, y: cls(x, y, text))
        app.ask("Label text:", named)

    @classmethod
    def icon_sample(cls):
        return cls(0, 0, "Label")

    def edit_text(self, app):
        def submit(text):
            if text.strip():
                self.text = text.strip()
                app.save()
        app.ask("Label text:", submit, value=self.text)

    def inspector(self, app):
        pos = f"{self.world_center.x:.0f}, {self.world_center.y:.0f}"
        return [Row("Text", self.text or "—", on_click=self.edit_text),
                Row("ID", self.id), Row("Position", pos)] + self.prop_rows()

    # --- Text ---------------------------------------------------------------------
    def shown_text(self):
        return (self.text or " ").upper() if self.caps else (self.text or " ")

    def _font(self, px):
        name, bold = LABEL_FONTS.get(self.font, (None, False))
        return get_font(px, name, bold)

    def _render_text(self, px, color):
        """The text as a surface at font size px (letter-spaced when caps is on),
        cached so it's only rebuilt when something about it changes."""
        px = font_px(px)                    # the size get_font really uses, so a zoom hits the cache
        key = (self.shown_text(), px, self.font, self.caps, color)
        surf = Label._cache.get(key)
        if surf is None:
            font = self._font(px)
            text = self.shown_text()
            if not self.caps:
                surf = font.render(text, True, color)
            else:
                spacing = round(px * 0.12)
                glyphs = [font.render(ch, True, color) for ch in text]
                width = sum(g.get_width() for g in glyphs) + spacing * (len(glyphs) - 1)
                surf = pygame.Surface((max(1, width), font.get_height()), pygame.SRCALPHA)
                x = 0
                for g in glyphs:
                    surf.blit(g, (x, 0))
                    x += g.get_width() + spacing
            self._remember(key, surf)
        return surf

    def _render_glow(self, px, color):
        """A soft blurred copy of the text (shrink then grow back), on black so
        it can be added onto the canvas (black adds nothing), padded so the blur
        has room to spread."""
        px = font_px(px)
        key = (self.shown_text(), px, self.font, self.caps, color, "glow")
        surf = Label._cache.get(key)
        if surf is None:
            ts = self._render_text(px, color)
            pad = max(2, px // 3)
            big = pygame.Surface((ts.get_width() + 2 * pad, ts.get_height() + 2 * pad))
            big.fill((0, 0, 0))
            big.blit(ts, (pad, pad))
            w, h = big.get_size()
            small = pygame.transform.smoothscale(big, (max(1, w // 6), max(1, h // 6)))
            surf = pygame.transform.smoothscale(small, (w, h))
            self._remember(key, surf)
        return surf

    def _render_3d(self, px, accent):
        """The extruded sides of 3D text — the text repeated `steps` times, each
        copy 1 px further down-right and darker — as one cached surface; blit it
        1 px down-right of the text. Built with numpy in one pass (stacking that
        many big tinted copies took up to ~350 ms at each new zoom size): each
        pixel gets the tone of the front-most copy covering it, and the alpha of
        the copy covering it most."""
        px = font_px(px)
        key = (self.shown_text(), px, self.font, self.caps, accent, "3D")
        surf = Label._cache.get(key)
        if surf is None:
            white = self._render_text(px, (255, 255, 255))
            steps = max(2, round(px * 0.13))
            w, h = white.get_size()
            cover = np.zeros((w + steps - 1, h + steps - 1), np.uint8)    # [x, y], as surfarray
            cover[:w, :h] = pygame.surfarray.array_alpha(white)
            # Alpha: the max over the `steps` copies — doubled up along the
            # diagonal, then two overlapping windows that together span `steps`.
            span, alpha = 1, cover
            while span * 2 <= steps:
                alpha = np.maximum(alpha, _shift_diagonal(alpha, span))
                span *= 2
            alpha = np.maximum(alpha, _shift_diagonal(alpha, steps - span))
            # Tone: the front-most copy at each pixel (0 = front), row by row:
            # 0 where the text itself is, else one more than the pixel up-left.
            front = np.empty(cover.shape, np.int32)
            above = np.full(cover.shape[0], steps - 1, np.int32)
            for y in range(cover.shape[1]):
                moved = np.empty_like(above)
                moved[0], moved[1:] = steps - 1, np.minimum(above[:-1] + 1, steps - 1)
                above = front[:, y] = np.where(cover[:, y] > 0, 0, moved)
            tones = np.array([shade(accent, 0.35 + 0.45 * (steps - 1 - i) / steps)
                              for i in range(steps)], np.uint8)
            surf = pygame.Surface(cover.shape, pygame.SRCALPHA)
            pygame.surfarray.pixels3d(surf)[:] = tones[front]
            pygame.surfarray.pixels_alpha(surf)[:] = alpha
            self._remember(key, surf)
        return surf

    @staticmethod
    def _remember(key, surf):
        pixels = surf.get_width() * surf.get_height()
        if len(Label._cache) > 400 or Label._cache_pixels + pixels > LABEL_CACHE_PIXELS:
            Label._cache.clear()
            Label._cache_pixels = 0
        Label._cache[key] = surf
        Label._cache_pixels += pixels

    def _text_size(self, px):
        font = self._font(px)
        text = self.shown_text()
        if not self.caps:
            return font.size(text)
        spacing = round(px * 0.12)
        return (sum(font.size(ch)[0] for ch in text) + spacing * (len(text) - 1), font.get_height())

    def _padding(self):
        """(left, right, top, bottom) around the text, as fractions of its height."""
        return LABEL_STYLES.get(self.style, LABEL_STYLES["plate"])

    @property
    def world_width(self):
        tw, th = self._text_size(LABEL_BASE_FONT)
        left, right, _t, _b = self._padding()
        return (tw + (left + right) * th) * self.scale

    @property
    def world_height(self):
        _tw, th = self._text_size(LABEL_BASE_FONT)
        _l, _r, top, bottom = self._padding()
        return th * (1 + top + bottom) * self.scale

    # --- Drawing ------------------------------------------------------------------
    def draw_shape(self, surface, rect, camera):
        px = round(LABEL_BASE_FONT * self.scale * camera.zoom)
        accent = self.color if not self.is_hovered else shade(self.color, 1.15)
        if px < 7 or px > LABEL_MAX_FONT:          # too small to read (or absurdly big): just the shape
            draw_rect(surface, shade(accent, 0.7), rect, border_radius=max(2, rect.height // 5))
            return
        text_color = self._draw_frame(surface, rect, accent)
        th = self._render_text(px, text_color).get_height()
        left, right, top, bottom = self._padding()
        area = pygame.Rect(rect.x + round(left * th), rect.y + round(top * th),
                           rect.width - round((left + right) * th), th)
        self._draw_text(surface, px, text_color, accent, area.center)

    def _draw_frame(self, surface, rect, accent):
        """Draw the Style's frame; returns the text color that suits it."""
        h, st = rect.height, self.style
        r = max(2, round(h * 0.18))
        light, line = (236, 238, 245), max(1, h // 28)
        if st == "plate":
            draw_rect(surface, (12, 12, 15), rect.move(0, max(1, h // 14)), border_radius=r)
            draw_rect(surface, (34, 35, 44), rect, border_radius=r)
            shine_y = rect.y + max(1, h // 14)
            pygame.draw.line(surface, (54, 56, 70), (rect.x + r, shine_y), (rect.right - r, shine_y),
                             max(1, h // 30))
            draw_rect(surface, shade(accent, 0.45), rect, width=line, border_radius=r)
            bar = pygame.Rect(rect.x + round(h * 0.22), rect.y + round(h * 0.2),
                              max(2, round(h * 0.1)), rect.height - 2 * round(h * 0.2))
            draw_rect(surface, accent, bar, border_radius=max(1, bar.width // 2))
            return light
        if st == "bevel":                           # a raised block, like a button cap
            depth = max(2, round(h * 0.16))
            face = rect.copy()
            face.height -= depth
            body = shade(accent, 0.42)
            draw_rect(surface, (10, 10, 12), rect.move(0, max(1, h // 14)), border_radius=r)
            draw_rect(surface, shade(accent, 0.2), rect, border_radius=r)          # side
            draw_rect(surface, body, face, border_radius=r)                        # top
            shine = pygame.Rect(face.x + r, face.y + max(1, h // 12), face.width - 2 * r, max(1, h // 12))
            draw_rect(surface, shade(body, 1.45), shine, border_radius=max(1, shine.height // 2))
            draw_rect(surface, shade(accent, 0.14), face, width=line, border_radius=r)
            return light
        if st == "inset":                           # sunk into a housing, like the inputs
            draw_rect(surface, (14, 14, 18), rect.move(0, max(1, h // 18)), border_radius=r)
            draw_rect(surface, INPUT_HOUSING, rect, border_radius=r)
            draw_rect(surface, shade(INPUT_HOUSING, 1.35), rect, width=line, border_radius=r)
            b = max(2, round(h * 0.14))
            well = rect.inflate(-2 * b, -2 * b)
            wr = max(2, r - b // 2)
            draw_rect(surface, (13, 13, 17), well, border_radius=wr)
            pygame.draw.line(surface, (3, 3, 5), (well.x + wr, well.y + 1), (well.right - wr, well.y + 1),
                             max(1, h // 20))                                              # inner shadow
            pygame.draw.line(surface, (46, 46, 58), (well.x + wr, well.bottom - 1),
                             (well.right - wr, well.bottom - 1), 1)                        # lit lip
            return accent
        if st == "ribbon":                          # a swallowtail banner
            notch = round(h * 0.42)
            drop = max(2, round(h * 0.12))
            def banner(box):
                return [(box.x, box.y), (box.right, box.y), (box.right - notch, box.centery),
                        (box.right, box.bottom), (box.x, box.bottom), (box.x + notch, box.centery)]
            body = rect.copy()
            body.height -= drop
            pygame.draw.polygon(surface, (10, 10, 12), banner(body.move(0, drop + max(1, h // 20))))
            pygame.draw.polygon(surface, shade(accent, 0.5), banner(body.move(0, drop)))   # underside
            pygame.draw.polygon(surface, accent, banner(body))
            top_shine = pygame.Rect(body.x + notch, body.y + max(1, h // 14), body.width - 2 * notch,
                                    max(1, h // 16))
            draw_rect(surface, shade(accent, 1.25), top_shine)
            return (22, 22, 28)
        if st == "neon":                            # a glowing tube
            draw_rect(surface, (13, 13, 17), rect, border_radius=r)
            glow = max(3, h // 6)
            layer, area = alpha_layer(surface, rect.inflate(4 * glow, 4 * glow))   # just the part on screen
            if layer is not None:
                for i in range(4, 0, -1):
                    draw_rect(layer, (*accent, 22 + 10 * (4 - i)),
                              rect.inflate(i * glow, i * glow).move(-area.x, -area.y),
                              width=glow, border_radius=r + i * glow // 2)
                surface.blit(layer, area)
            draw_rect(surface, accent, rect, width=max(2, h // 16), border_radius=r)
            draw_rect(surface, shade(accent, 1.6), rect, width=max(1, h // 40), border_radius=r)
            return shade(accent, 1.3)
        if st == "brackets":                        # HUD corner brackets
            arm, t = max(4, round(h * 0.38)), max(2, h // 14)
            for cx, cy, dx, dy in ((rect.left, rect.top, 1, 1), (rect.right, rect.top, -1, 1),
                                   (rect.left, rect.bottom, 1, -1), (rect.right, rect.bottom, -1, -1)):
                x0 = cx if dx > 0 else cx - t
                y0 = cy if dy > 0 else cy - t
                draw_rect(surface, accent, (x0 if dx > 0 else cx - arm, y0, arm, t))
                draw_rect(surface, accent, (x0, y0 if dy > 0 else cy - arm, t, arm))
            return light
        if st == "double":
            draw_rect(surface, (28, 29, 37), rect, border_radius=r)
            draw_rect(surface, accent, rect, width=max(1, h // 22), border_radius=r)
            gap = max(3, round(h * 0.1))
            draw_rect(surface, shade(accent, 0.75), rect.inflate(-2 * gap, -2 * gap),
                      width=max(1, h // 40), border_radius=max(2, r - gap))
            return light
        if st == "outline":
            draw_rect(surface, accent, rect, width=max(1, round(h * 0.06)), border_radius=r)
            return accent
        # underline
        rule = pygame.Rect(rect.x, rect.bottom - max(2, round(h * 0.09)), rect.width, max(2, round(h * 0.09)))
        draw_rect(surface, accent, rule, border_radius=max(1, rule.height // 2))
        return light

    def _draw_text(self, surface, px, color, accent, center):
        """The text, centered at `center`, with the chosen Effect."""
        ts = self._render_text(px, color)
        pos = ts.get_rect(center=center)
        effect = self.effect if px >= 12 else "flat"     # effects turn to mush when tiny
        o = max(1, round(px * 0.05))
        if effect == "shadow":                    # lifted off the surface
            surface.blit(self._render_text(px, (6, 6, 8)), pos.move(o, round(o * 1.3)))
        elif effect == "raised":                  # embossed: lit top-left, shaded bottom-right
            k = max(1, o // 2 + 1)
            surface.blit(self._render_text(px, (6, 6, 8)), pos.move(k, k))
            surface.blit(self._render_text(px, shade(color, 1.5) if sum(color) < 600 else (255, 255, 255)),
                         pos.move(-max(1, k // 2), -max(1, k // 2)))
        elif effect == "engraved":                # carved in: shaded top-left, lit bottom-right
            k = max(1, o // 2 + 1)
            surface.blit(self._render_text(px, (4, 4, 6)), pos.move(-k, -k))
            surface.blit(self._render_text(px, (90, 92, 108)), pos.move(max(1, k // 2), max(1, k // 2)))
            ts = self._render_text(px, shade(color, 0.62))
        elif effect == "3D":                      # extruded block letters in the accent color
            surface.blit(self._render_3d(px, accent), pos.move(1, 1))
        elif effect == "glow":
            glow = self._render_glow(px, accent)
            gpos = glow.get_rect(center=center)
            surface.blit(glow, gpos, special_flags=pygame.BLEND_RGB_ADD)
            surface.blit(glow, gpos, special_flags=pygame.BLEND_RGB_ADD)
        surface.blit(ts, pos)


# ============================================================================
# DEVICES — real (or simulated) hardware run by the instrument server
# ============================================================================
# The drivers live in another process (instrument_server.py); instruments.py
# is the canvas's link to it. A Device object stands for one device the server
# runs (by name): its params can be set by hand or linked to any object's
# output (a slider's value), and its readings are outputs that readouts (or
# anything else) can link to. Its Connect button opens the link (launching the
# server if needed); a driver-specific object (DRIVER set, e.g. ArduinoServo)
# also sets up its own device on the server, with its own options (port …).
DEVICE_STATUS_COLORS = {"ok": (90, 220, 120), "starting": (240, 190, 60), "connecting": (240, 190, 60),
                        "error": (235, 80, 80), "missing": (235, 80, 80), "offline": (110, 110, 120),
                        "off": (110, 110, 120)}
DEVICE_STATUS_TEXT = {"ok": "connected", "starting": "starting…", "connecting": "connecting…",
                      "error": "error", "missing": "not on the server", "offline": "server offline",
                      "off": "not connected"}
DEVICE_RETRY_SECONDS = 3.0      # how often a driver-specific object re-asks the server to set it up


class Device(Thing):
    """One device on the instrument server, on the canvas. Shows its name, a
    status light, and a small screen with its param values (what it's set to)
    and readings (what it reports). Each param can be linked to a source —
    e.g. a slider — and then follows it live; readings are outputs, so a
    readout can show them. Its params and links are saved; the device itself
    keeps running (and holds its last values) when the canvas closes."""

    TYPE = "device"
    MENU_LABEL = None             # not in the menus: placed from the Instrument server's device list
    NAME = "Device"
    CATEGORY = "Devices"
    DRIVER = None                 # a subclass for one driver sets its module name (e.g. "fake_arduino")
    DEFAULT_DEVICE = ""           # server device name to use when none of that driver is found
    SIZE = (230, 132)
    DOT_COLOR = (90, 220, 120)
    PROPS = [Prop("scale", "Size", "float", 1.0, min=0.25, max=50, unit="×",
                  prompt="Size (1 = normal, 2 = twice as big):"),
             Prop("device", kind="str", default="", hidden=True),     # name on the server
             Prop("connect", kind="bool", default=False, hidden=True),  # Connect pressed (saved)
             Prop("links", kind="data", default={}, hidden=True)]     # param -> {"id", "key"}
    WIRE_COLOR = (120, 220, 150)

    @classmethod
    def create(cls, app):
        client = instruments.client

        def make(x, y):
            obj = cls(x, y)
            used = {o.device for o in app.scene.objects if isinstance(o, Device)}
            base = cls.DEFAULT_DEVICE or "device"           # a name no other object here uses
            obj.device = next(n for n in [base] + [f"{base}{i}" for i in range(2, 1000)] if n not in used)
            return obj
        app.start_placing(make)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._next_request = 0.0    # when to (re)ask the server to set this device up

    # --- The server's view of this device ----------------------------------------
    def info(self):
        return instruments.client.device(self.device) if self.device else None

    def status(self):
        """off (not connected) / connecting / starting / ok / error / missing."""
        if not self.connect or not self.device:
            return "off"
        status = instruments.client.status(self.device)
        if status == "offline":
            return "connecting"
        if status == "missing" and self.DRIVER:
            return "starting"            # being set up on the server
        return status

    def device_options(self):
        """Options this object wants its device run with (e.g. {"port": "COM11"})."""
        return {}

    def _provision(self):
        """Driver-specific objects set up their own device: add it to the
        server if it isn't there, or reconfigure it if its options changed."""
        client = instruments.client
        if not (self.DRIVER and self.connect and self.device and client.connected):
            return
        now = time.monotonic()
        if now < self._next_request:
            return
        info = client.device(self.device)
        options = self.device_options()
        if info is None:
            client.send({"op": "add_device", "name": self.device, "driver": self.DRIVER,
                         "options": options})
            self._next_request = now + DEVICE_RETRY_SECONDS
        elif any(info.get("options", {}).get(k) != v for k, v in options.items()):
            client.send({"op": "configure_device", "name": self.device, "options": options})
            self._next_request = now + DEVICE_RETRY_SECONDS

    def connect_now(self, app):
        self.connect = True
        instruments.client.want(self.id, True)
        instruments.client.retry_launch()
        self._next_request = 0.0
        app.save()

    def disconnect(self, app):
        self.connect = False
        app.save()

    def on_removed(self):
        instruments.client.want(self.id, False)

    def outputs(self):
        """Readings first (what it reports — a readout links to the first one by
        default), then params (what it's set to)."""
        info = self.info()
        if info is None:
            return {}
        out = {k: v for k, v in info["values"].items()
               if isinstance(v, (int, float)) and not isinstance(v, bool)}   # numbers only (text is shown, not linked)
        for p, spec in info["params"].items():
            if spec.get("value") is not None:
                out.setdefault(p, spec["value"])
        return out

    def tick(self, dt):
        """Keep the connection wanted (while Connect is on), set up the device
        if needed, and push each linked param's source value to the server
        (the client skips repeats and caps the rate)."""
        instruments.client.want(self.id, self.connect)
        if not self.connect or not self.device or self.scene is None:
            return
        self._provision()
        for param, link in self.links.items():
            src = self.scene.by_id.get(link.get("id"))
            value = src.outputs().get(link.get("key")) if src is not None else None
            if value is not None:
                instruments.client.set(self.device, param, value)

    # --- Inspector ------------------------------------------------------------------
    def next_device(self, app):
        names = instruments.client.device_names()
        if names:
            i = names.index(self.device) if self.device in names else -1
            self.device = names[(i + 1) % len(names)]
            app.save()

    def set_by_hand(self, app, param, spec):
        def submit(text):
            try:
                instruments.client.set(self.device, param, float(text))
            except ValueError:
                pass
        lo, hi = spec.get("min"), spec.get("max")
        span = f" ({lo:g}–{hi:g} {spec.get('unit', '')})" if lo is not None and hi is not None else ""
        app.ask(f"Set {param}{span}:", submit, value=f"{spec.get('value', 0):g}")

    def change_port(self, app):
        """Type a new port (or "auto"); the server restarts the device on it.
        Only Arduino ports are suggested, so other serial equipment isn't hit by mistake."""
        info = self.info() or {}
        arduinos = [p["device"] for p in instruments.client.ports if p.get("arduino")]
        hint = f"Arduino on {', '.join(arduinos)}" if arduinos else "no Arduino detected"
        def submit(text):
            port = text.strip()
            if port:
                instruments.client.send({"op": "configure_device", "name": self.device,
                                         "options": {"port": port}})
        app.ask(f"Port, or auto ({hint}):", submit,
                value=str(info.get("options", {}).get("port", "auto")))

    def pick_source(self, app, param):
        def link(src):
            keys = list(src.outputs())
            self.links[param] = {"id": src.id, "key": "value" if "value" in keys else keys[0]}
            app.save()
        app.start_picking(f"Click what {param} should follow (a slider, …)", link,
                          accept=lambda obj: obj is not self and bool(obj.outputs()),
                          origin=self.center)

    def unlink_all(self, app):
        self.links = {}
        app.save()

    def inspector(self, app):
        status = self.status()
        rows = [Row("Device", self.device or "— (none)",
                    on_click=None if self.DRIVER else self.next_device),
                Row("Status", DEVICE_STATUS_TEXT.get(status, status),
                    color=DEVICE_STATUS_COLORS.get(status))]
        info = self.info() if self.connect else None
        if info is not None:
            rows.append(Row("Driver", info["driver"]))
            if not self.DRIVER and "port" in info.get("options", {}):
                rows.append(Row("Port", info["options"]["port"], on_click=self.change_port))
            if info.get("message"):
                rows.append(Field("Message", info["message"], color=WARN_COLOR))
            for param, spec in info["params"].items():
                rows.append(Gap(6))
                value = spec.get("value")
                rows.append(Row(f"{param} (set)", "—" if value is None else f"{value:g} {spec.get('unit', '')}",
                                on_click=lambda app, p=param, s=spec: self.set_by_hand(app, p, s)))
                link = self.links.get(param)
                src = self.scene.by_id.get(link["id"]) if link and self.scene else None
                rows.append(Row("   follows", f"{src.display_name} · {link['key']}" if src else
                                "— click to link", on_click=lambda app, p=param: self.pick_source(app, p)))
            if info["readings"]:
                rows.append(Gap(6))
            for name, spec in info["readings"].items():
                value = info["values"].get(name)
                rows.append(Row(name, "—" if value is None else
                                str(value) if isinstance(value, str) else f"{value:g} {spec.get('unit', '')}"))
        return rows + [Gap(6)] + self.prop_rows()

    def remove_from_server(self, app):
        def remove():
            instruments.client.send({"op": "remove_device", "name": self.device})
            app.scene.delete(self)
        app.confirm(f"Remove '{self.device}'?", "From the server and the canvas.", remove)

    def run_action(self, app, name):
        instruments.client.send({"op": "action", "device": self.device, "name": name})

    def buttons(self, app):
        out = []
        info = self.info() if self.connect else None
        if info is not None and info.get("status") == "ok":
            for name in info.get("actions", []):          # driver actions: Home, Stop, …
                out.append(Btn(name.capitalize(), lambda app, n=name: self.run_action(app, n)))
        if self.links:
            out.append(Btn("Unlink all", self.unlink_all))
        if not self.DRIVER and self.connect and instruments.client.connected and self.info() is not None:
            out.append(Btn("Remove from server", self.remove_from_server))
        out.append(Btn("Disconnect", self.disconnect) if self.connect
                   else Btn("Connect", self.connect_now, style="go"))
        return out

    # --- Drawing ----------------------------------------------------------------------
    def draw_links(self, surface, camera):
        if self.scene is None:
            return
        for link in self.links.values():
            src = self.scene.by_id.get(link.get("id"))
            if src is not None and src.center is not None:
                draw_wire(surface, camera, output_point(src), self.center, self.WIRE_COLOR)

    def draw_shape(self, surface, rect, camera):
        s = min(rect.width, rect.height)
        radius = max(2, s // 8)
        draw_rect(surface, (14, 14, 18), rect.move(0, max(1, s // 26)), border_radius=radius)
        draw_rect(surface, INPUT_HOUSING, rect, border_radius=radius)
        draw_rect(surface, shade(INPUT_HOUSING, 1.35), rect, width=max(1, s // 50), border_radius=radius)
        status = self.status()
        led_color = DEVICE_STATUS_COLORS.get(status, (110, 110, 120))
        if s < 24:                                   # tiny: just the status light
            pygame.draw.circle(surface, led_color, rect.center, max(1, s // 4))
            return

        pad = max(2, round(s * 0.09))
        header_h = max(6, round(s * 0.2))
        # Status light (with a soft halo when lit) on the right of the header.
        led_r = max(2, header_h // 3)
        led = (rect.right - pad - led_r, rect.y + pad + header_h // 2)
        if status in ("ok", "starting", "error", "missing"):
            pygame.draw.circle(surface, shade(led_color, 0.35), led, round(led_r * 1.8))
        pygame.draw.circle(surface, led_color, led, led_r)
        pygame.draw.circle(surface, shade(led_color, 1.5), (led[0] - led_r // 3, led[1] - led_r // 3),
                           max(1, led_r // 3))
        # Name in the header.
        size = round(header_h * 0.85)
        if size >= 8:
            title = self.text or self.device or "device"
            font = get_font(size, "bahnschrift")
            ts = font.render(clip_text(title, font, rect.width - 3 * pad - 2 * led_r), True, (226, 228, 236))
            surface.blit(ts, ts.get_rect(midleft=(rect.x + pad, led[1])))

        # The screen: params (what it's set to) and readings (what it reports).
        screen = pygame.Rect(rect.x + pad, rect.y + pad + header_h + pad // 2,
                             rect.width - 2 * pad, rect.bottom - pad - (rect.y + pad + header_h + pad // 2))
        draw_rect(surface, (12, 14, 16), screen, border_radius=max(2, radius - 2))
        draw_rect(surface, (6, 6, 8), screen, width=1, border_radius=max(2, radius - 2))
        info = self.info() if self.connect else None
        if info is None or status in ("offline", "missing", "off", "connecting"):
            lines = [(DEVICE_STATUS_TEXT.get(status, status), "", (130, 130, 142))]
        else:
            lines = []
            for p, spec in info["params"].items():
                v = spec.get("value")
                lines.append((f"{p} →", "—" if v is None else f"{v:.1f} {spec.get('unit', '')}", (255, 190, 90)))
            for r, spec in info["readings"].items():
                v = info["values"].get(r)
                lines.append((r, "—" if v is None else str(v) if isinstance(v, str)
                              else f"{v:.1f} {spec.get('unit', '')}", (120, 230, 150)))
        line_h = screen.height / max(3, len(lines) + 0.6)
        size = round(line_h * 0.8)
        if size >= 8:
            font = get_font(size, "consolas")
            y = screen.y + line_h * 0.3
            for left, right, color in lines:
                if y + line_h > screen.bottom + 1:
                    break
                surface.blit(font.render(left, True, shade(color, 0.8)), (screen.x + pad, round(y)))
                if right:
                    rs = font.render(right, True, color)
                    surface.blit(rs, (screen.right - pad - rs.get_width(), round(y)))
                y += line_h



# ---- Arduino servo: a Device with its own look ------------------------------
SERVO_BLUE = (40, 110, 215)
SERVO_HORN = (238, 238, 242)
SERVO_SWING = 420.0          # deg/sec the drawn horn swings toward the set angle


class ArduinoServo(Device):
    """A hobby servo on an Arduino (driver: arduino_servo), drawn from above:
    a blue servo body with mounting ears, and a white horn that swings to the
    angle it's set to, over a 0–180° scale. Link its angle to a slider (0–180)
    and the horn — and the real servo — follow. The status light shows whether
    the server has the serial port open."""

    TYPE = "arduino_servo"
    MENU_LABEL = "Add Arduino servo"
    NAME = "Arduino servo"
    DRIVER = "arduino_servo"
    DEFAULT_DEVICE = "servo"
    SIZE = (230, 180)
    DOT_COLOR = SERVO_BLUE
    PROPS = [Prop("port", "Port", "str", "auto",
                  prompt="Serial port, e.g. COM11 (or auto to find the Arduino):"),
             Prop("baud", "Baud", "int", 9600, min=300, max=2_000_000)]

    def device_options(self):
        return {"port": self.port or "auto", "baud": self.baud}

    def inspector(self, app):
        rows = super().inspector(app)
        arduinos = [p["device"] for p in instruments.client.ports if p.get("arduino")]
        if instruments.client.connected:
            rows.append(Row("Arduino found on", ", ".join(arduinos) or "— none plugged in"))
        return rows

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._shown = None           # the angle the drawn horn is at (eases toward the set one)

    def set_angle(self):
        """The angle the server says it's set to (None if not known)."""
        info = self.info() if self.connect else None
        spec = (info or {}).get("params", {}).get("angle")
        return None if spec is None else spec.get("value")

    def tick(self, dt):
        super().tick(dt)             # pushes a linked slider's value to the server
        target = self.set_angle()
        if target is None:
            return                   # unknown (offline): keep the horn where it was
        if self._shown is None:
            self._shown = target
        step = SERVO_SWING * dt
        self._shown += max(-step, min(step, target - self._shown))

    def draw_shape(self, surface, rect, camera):
        status = self.status()
        led_color = DEVICE_STATUS_COLORS.get(status, (110, 110, 120))
        s = rect.width
        body_h = round(rect.height * 0.5)
        body = pygame.Rect(rect.x + round(s * 0.07), rect.bottom - body_h,
                           rect.width - 2 * round(s * 0.07), body_h)
        radius = max(2, s // 28)
        if s < 30:                                   # tiny: the body and its light
            draw_rect(surface, SERVO_BLUE, body, border_radius=radius)
            pygame.draw.circle(surface, led_color, body.center, max(1, s // 8))
            return

        # Mounting ears (with screw holes) behind the body, then the body.
        ears = pygame.Rect(rect.x, body.y + round(body_h * 0.3), rect.width, round(body_h * 0.4))
        draw_rect(surface, (12, 12, 16), body.move(0, max(1, s // 50)).union(ears.move(0, max(1, s // 50))),
                         border_radius=radius)                                  # shadow
        draw_rect(surface, shade(SERVO_BLUE, 0.72), ears, border_radius=radius)
        hole = max(1, round(ears.height * 0.17))
        for hx in (ears.x + round(s * 0.035), ears.right - round(s * 0.035)):
            pygame.draw.circle(surface, (18, 18, 24), (hx, ears.centery), hole)
        draw_rect(surface, SERVO_BLUE, body, border_radius=radius)
        draw_rect(surface, shade(SERVO_BLUE, 1.25), (body.x + radius, body.y + 1, body.width - 2 * radius,
                                                            max(1, body_h // 14)))          # top shine
        draw_rect(surface, shade(SERVO_BLUE, 0.5), body, width=max(1, s // 90), border_radius=radius)

        # Shaft (on the round raised part of the case), the 0–180° scale, and the horn.
        # The shaft sits on the body's top edge, so the whole scale arcs above the body.
        shaft = (body.x + round(body.width * 0.3), body.y + max(1, body_h // 16))
        pygame.draw.circle(surface, shade(SERVO_BLUE, 0.85), shaft, round(body_h * 0.28))
        reach = min(rect.height * 0.42, shaft[0] - rect.x - s * 0.03, shaft[1] - rect.y - s * 0.07)
        for deg in range(0, 181, 15):
            a = math.radians(deg)
            d = (-math.cos(a), -math.sin(a))
            major = deg % 45 == 0
            r0 = reach * (0.86 if major else 0.92)
            pygame.draw.line(surface, (110, 112, 128) if major else (70, 72, 84),
                             (shaft[0] + d[0] * r0, shaft[1] + d[1] * r0),
                             (shaft[0] + d[0] * reach, shaft[1] + d[1] * reach), max(1, s // 110))
        size = round(s * 0.05)
        if size >= 8:
            font = get_font(size, "bahnschrift")
            for deg, at in ((0, 14), (90, 90), (180, 166)):     # 0 and 180 lifted off the body's edge
                a = math.radians(at)
                ts = font.render(str(deg), True, (120, 122, 138))
                pos = (shaft[0] - math.cos(a) * reach * 1.12, shaft[1] - math.sin(a) * reach * 1.12)
                surface.blit(ts, ts.get_rect(center=(round(pos[0]), round(pos[1]))))

        angle = 90.0 if self._shown is None else self._shown
        a = math.radians(angle)
        d = (-math.cos(a), -math.sin(a))
        horn_w = max(3, round(s * 0.05))
        length = reach * 0.78
        tip = (shaft[0] + d[0] * length, shaft[1] + d[1] * length)
        back = (shaft[0] - d[0] * length * 0.22, shaft[1] - d[1] * length * 0.22)
        pygame.draw.line(surface, (20, 20, 26), (back[0] + 2, back[1] + 3), (tip[0] + 2, tip[1] + 3), horn_w)  # shadow
        pygame.draw.line(surface, SERVO_HORN, back, tip, horn_w)
        for end in (back, tip):
            pygame.draw.circle(surface, SERVO_HORN, (round(end[0]), round(end[1])), horn_w // 2)
        for k in range(1, 5):                                       # the horn's holes
            f = 0.2 + 0.19 * k
            pygame.draw.circle(surface, (150, 150, 160),
                               (round(shaft[0] + d[0] * length * f), round(shaft[1] + d[1] * length * f)),
                               max(1, horn_w // 5))
        pygame.draw.circle(surface, SERVO_HORN, shaft, round(horn_w * 0.95))
        pygame.draw.circle(surface, (120, 120, 132), shaft, max(1, horn_w // 3))  # screw

        # Name, angle and status on the body's right side; the status light in its corner.
        pad = max(2, round(body_h * 0.1))
        led_r = max(2, s // 45)
        led = (body.right - pad - led_r, body.y + pad + led_r)
        if status in ("ok", "starting", "error", "missing"):
            pygame.draw.circle(surface, shade(led_color, 0.4), led, round(led_r * 1.8))
        pygame.draw.circle(surface, led_color, led, led_r)
        text_x = body.x + round(body.width * 0.52)
        room = led[0] - led_r * 2 - text_x
        set_angle = self.set_angle()
        lines = [(self.text or self.device or "servo", round(body_h * 0.2), (236, 240, 255)),
                 ("—" if set_angle is None else f"{set_angle:.0f}°", round(body_h * 0.3), (255, 255, 255)),
                 (DEVICE_STATUS_TEXT.get(status, status) if status != "ok" else
                  (self.info() or {}).get("options", {}).get("port", self.port), round(body_h * 0.15),
                  shade(led_color, 1.1) if status != "ok" else (190, 205, 240))]
        y = body.y + pad
        for text, size, color in lines:
            if size >= 8 and room > 10:
                font = get_font(size, "bahnschrift")
                surface.blit(font.render(clip_text(text, font, room), True, color), (text_x, y))
            y += round(size * 1.12)



# ---- Zaber stage: a Device with its own look ---------------------------------
ZABER_RAIL = (58, 60, 70)
ZABER_CARRIAGE = (196, 200, 210)
ZABER_MOTOR = (34, 35, 40)
ZABER_TARGET = (255, 170, 60)


class ZaberStage(Device):
    """One stage of a Zaber chain (driver: zaber_stage), drawn from above: a
    stepper motor on the end of a rail, and the carriage sitting at the real
    position (read back from the stage) along its travel. An orange marker
    shows the target; the carriage glows while moving. Link target to a slider
    and a readout to position. Home / Stop are buttons once it's connected."""

    TYPE = "zaber_stage"
    MENU_LABEL = "Add Zaber stage"
    NAME = "Zaber stage"
    DRIVER = "zaber_stage"
    DEFAULT_DEVICE = "zaber"
    SIZE = (300, 130)
    DOT_COLOR = ZABER_CARRIAGE
    PROPS = [Prop("port", "Port", "str", "COM3",
                  prompt="The Zaber chain's COM port (e.g. COM3), or sim:// to simulate:"),
             Prop("stage", "Stage # in chain", "int", 0, min=0, max=31,
                  prompt="Which stage on the chain (0 = the first device):"),
             Prop("baud", "Baud", "int", 115200, min=300, max=2_000_000)]

    def device_options(self):
        return {"port": self.port or "auto", "stage": self.stage, "baud": self.baud}

    def _numbers(self):
        """(position, target, low, high, moving, homed) from the server, None where unknown."""
        info = self.info() if self.connect else None
        if not info:
            return None, None, None, None, False, False
        spec = info["params"].get("target", {})
        values = info["values"]
        return (values.get("position"), spec.get("value"), spec.get("min"), spec.get("max"),
                bool(values.get("moving")), bool(values.get("homed")))

    def inspector(self, app):
        rows = super().inspector(app)
        position, target, low, high, moving, homed = self._numbers()
        if low is not None and high is not None:
            rows.append(Row("Travel", f"{low:g} – {high:g} mm"))
        return rows

    def draw_shape(self, surface, rect, camera):
        status = self.status()
        led_color = DEVICE_STATUS_COLORS.get(status, (110, 110, 120))
        position, target, low, high, moving, homed = self._numbers()
        s = rect.height
        radius = max(2, s // 12)
        if s < 24:                                   # tiny: rail and light
            draw_rect(surface, ZABER_RAIL, rect, border_radius=radius)
            pygame.draw.circle(surface, led_color, rect.center, max(1, s // 5))
            return

        # Base plate (the whole object), a header strip with the name / position / light.
        draw_rect(surface, (14, 14, 18), rect.move(0, max(1, s // 26)), border_radius=radius)
        draw_rect(surface, INPUT_HOUSING, rect, border_radius=radius)
        draw_rect(surface, shade(INPUT_HOUSING, 1.35), rect, width=max(1, s // 60), border_radius=radius)
        pad = max(2, round(s * 0.08))
        header_h = round(s * 0.26)
        led_r = max(2, s // 26)
        led = (rect.right - pad - led_r, rect.y + pad + header_h // 2)
        if status in ("ok", "starting", "error", "missing", "connecting"):
            pygame.draw.circle(surface, shade(led_color, 0.4), led, round(led_r * 1.8))
        pygame.draw.circle(surface, led_color, led, led_r)
        size = round(header_h * 0.72)
        if size >= 8:
            font = get_font(size, "bahnschrift")
            if status == "ok" and position is not None:
                right_text, right_color = f"{position:.3f} mm", (236, 240, 255)
            else:
                right_text, right_color = DEVICE_STATUS_TEXT.get(status, status), shade(led_color, 1.1)
            rs = font.render(right_text, True, right_color)
            right_x = led[0] - 2 * led_r - pad - rs.get_width()
            surface.blit(rs, (right_x, led[1] - rs.get_height() // 2))
            title = self.text or f"{self.device} #{self.stage}"
            ts = font.render(clip_text(title, font, max(10, right_x - rect.x - 2 * pad)), True, (170, 172, 186))
            surface.blit(ts, (rect.x + pad, led[1] - ts.get_height() // 2))

        # The stage: a stepper motor on the left end of a rail, a lead screw, the carriage.
        area = pygame.Rect(rect.x + pad, rect.y + pad + header_h + pad // 2,
                           rect.width - 2 * pad, rect.bottom - pad - (rect.y + pad + header_h + pad // 2))
        motor = pygame.Rect(area.x, area.y, area.height, area.height)          # square, like a NEMA motor
        draw_rect(surface, ZABER_MOTOR, motor, border_radius=max(2, motor.width // 8))
        draw_rect(surface, (70, 72, 82), motor, width=max(1, motor.width // 20), border_radius=max(2, motor.width // 8))
        boss = max(2, round(motor.width * 0.26))
        pygame.draw.circle(surface, (60, 62, 72), motor.center, boss)
        pygame.draw.circle(surface, (120, 122, 134), motor.center, max(1, boss // 3))
        inset = round(motor.width * 0.17)
        for cx, cy in ((motor.x + inset, motor.y + inset), (motor.right - inset, motor.y + inset),
                       (motor.x + inset, motor.bottom - inset), (motor.right - inset, motor.bottom - inset)):
            pygame.draw.circle(surface, (16, 16, 20), (cx, cy), max(1, motor.width // 18))
        rail = pygame.Rect(motor.right, area.y + round(area.height * 0.18), area.right - motor.right,
                           area.height - 2 * round(area.height * 0.18))
        draw_rect(surface, ZABER_RAIL, rail, border_radius=max(2, rail.height // 6))
        draw_rect(surface, shade(ZABER_RAIL, 0.6), rail, width=max(1, s // 70), border_radius=max(2, rail.height // 6))
        pygame.draw.line(surface, (110, 112, 124), (rail.x, rail.centery), (rail.right - pad, rail.centery),
                         max(1, rail.height // 9))                                              # lead screw

        # Positions along the travel: carriage at the real position, marker at the target.
        car_w = max(6, round(rail.width * 0.16))
        track = (rail.x + car_w // 2 + pad // 2, rail.right - car_w // 2 - pad // 2)

        def along(mm):
            if mm is None or low is None or high is None or high == low:
                return None
            f = max(0.0, min(1.0, (mm - low) / (high - low)))
            return round(track[0] + f * (track[1] - track[0]))

        tx = along(target)
        if tx is not None:
            tri = max(3, rail.height // 4)
            pygame.draw.polygon(surface, ZABER_TARGET, [(tx, rail.y - 1), (tx - tri, rail.y - tri - 1),
                                                       (tx + tri, rail.y - tri - 1)])
            pygame.draw.line(surface, ZABER_TARGET, (tx, rail.y), (tx, rail.bottom), max(1, s // 80))
        cx = along(position)
        if cx is None:
            cx = (track[0] + track[1]) // 2
        carriage = pygame.Rect(0, 0, car_w, rail.height + round(area.height * 0.2))
        carriage.center = (cx, rail.centery)
        if moving:
            draw_rect(surface, shade(led_color, 0.6), carriage.inflate(6, 6), border_radius=max(2, car_w // 5))
        body = ZABER_CARRIAGE if status == "ok" else shade(ZABER_CARRIAGE, 0.6)
        draw_rect(surface, body, carriage, border_radius=max(2, car_w // 6))
        draw_rect(surface, shade(body, 1.12), (carriage.x + 2, carriage.y + 2, carriage.width - 4,
                                               max(1, carriage.height // 8)), border_radius=2)
        draw_rect(surface, shade(body, 0.5), carriage, width=max(1, s // 70), border_radius=max(2, car_w // 6))
        hole = max(1, car_w // 12)
        for hx in (carriage.x + car_w // 4, carriage.right - car_w // 4):
            for hy in (carriage.y + carriage.height // 4, carriage.bottom - carriage.height // 4):
                pygame.draw.circle(surface, shade(body, 0.45), (hx, hy), hole)
        if status == "ok" and not homed:
            size = round(area.height * 0.22)
            if size >= 8:
                ns = get_font(size, "bahnschrift").render("NOT HOMED", True, WARN_COLOR)
                surface.blit(ns, ns.get_rect(midbottom=(rail.centerx + motor.width // 2, rail.y - 2)))



# ---- Excitation filters: a color picker over the longpass / shortpass filters ---
def wavelength_rgb(nm):
    """Roughly the color of light at `nm` (Bruton's approximation); a dim deep
    red past the visible, so the IR end of the strip still reads as 'there'."""
    if nm < 380:
        r, g, b = 0.38, 0.0, 0.55
    elif nm < 440:
        r, g, b = (440 - nm) / 60, 0.0, 1.0
    elif nm < 490:
        r, g, b = 0.0, (nm - 440) / 50, 1.0
    elif nm < 510:
        r, g, b = 0.0, 1.0, (510 - nm) / 20
    elif nm < 580:
        r, g, b = (nm - 510) / 70, 1.0, 0.0
    elif nm < 645:
        r, g, b = 1.0, (645 - nm) / 65, 0.0
    else:
        r, g, b = 1.0, 0.0, 0.0
    # Fade toward the ends of what the eye sees.
    fade = 0.35 + 0.65 * (nm - 380) / 40 if nm < 420 else 0.35 + 0.65 * (780 - nm) / 80 if nm > 700 else 1.0
    fade = max(0.3, min(1.0, fade))
    return tuple(round(255 * (c * fade) ** 0.8) for c in (r, g, b))


BAND_DEFAULT_SPAN = (400.0, 800.0)     # the strip, until the driver says what the filters reach


class FilterBand(Device):
    """The excitation filters (driver: filter_band) as a color picker: a spectrum
    strip over what the longpass + shortpass filters can reach. Click or drag
    on it to set the band's center; the width is set (or linked) in the panel.
    The part of the spectrum the filters are actually passing — read back from
    the stage positions — stays lit, the rest is dimmed, and an orange outline
    shows the requested band while the stages catch up. Home / Stop are buttons
    once it's connected (the stages must be homed before they'll move). The
    position -> cutoff calibration is calibration/excitation_filters.json, logged
    with scripts/calibrate_excitation_filters.py (read when it connects).
    (TYPE stays "filter_band" so saved canvases still load.)"""

    TYPE = "filter_band"
    MENU_LABEL = "Add excitation filters"
    NAME = "Excitation filters"
    DRIVER = "filter_band"
    DEFAULT_DEVICE = "band"
    SIZE = (380, 140)
    DOT_COLOR = (90, 200, 120)
    PROPS = [Prop("port", "Port", "str", "COM3",
                  prompt="The Zaber chain's COM port (e.g. COM3), or sim:// to simulate:"),
             Prop("baud", "Baud", "int", 115200, min=300, max=2_000_000)]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._drag_nm = None        # center being dragged to (shown at once; the server lags a little)
        self._strip_cache = None    # (key, surface): the visible part of the spectrum, rendered

    def device_options(self):
        return {"port": self.port or "auto", "baud": self.baud}

    def _band(self):
        """{center, width, lo, hi (the strip's span), actual_lp, actual_sp, moving, homed}."""
        info = self.info() if self.connect else None
        params = (info or {}).get("params", {})
        values = (info or {}).get("values", {})
        center, width = params.get("center", {}), params.get("width", {})
        lo, hi = center.get("min"), center.get("max")
        if lo is None or hi is None or hi <= lo:
            lo, hi = BAND_DEFAULT_SPAN
        return {"center": self._drag_nm if self._drag_nm is not None else center.get("value"),
                "width": width.get("value"), "lo": lo, "hi": hi,
                "actual_lp": values.get("longpass"), "actual_sp": values.get("shortpass"),
                "moving": bool(values.get("moving")), "homed": bool(values.get("homed"))}

    def live(self):
        return self.connect and self.status() == "ok"

    # --- Layout (shared by drawing and clicking) -------------------------------------
    def _layout(self, rect):
        s = rect.height
        pad = max(2, round(s * 0.08))
        header_h = round(s * 0.24)
        label_h = round(s * 0.16)
        top = rect.y + pad + header_h + pad // 2
        strip = pygame.Rect(rect.x + pad, top, rect.width - 2 * pad, rect.bottom - pad - label_h - top)
        return {"pad": pad, "header_h": header_h, "strip": strip, "label_h": label_h}

    def _x_to_nm(self, x, strip, band):
        t = max(0.0, min(1.0, (x - strip.x) / max(1, strip.width)))
        return band["lo"] + t * (band["hi"] - band["lo"])

    # --- Picking ---------------------------------------------------------------------
    def on_click(self, pos, app):
        """A click on the spectrum sets the center (and drags it); anywhere else
        selects as usual. Not while center follows a link — the link would win."""
        if not self.live() or "center" in self.links:
            return False
        strip = self._layout(self.screen_rect(app.camera))["strip"]
        if not strip.inflate(0, 6).collidepoint(pos):
            return False
        self._pick(pos, app)
        return True

    def on_drag(self, pos, app):
        self._pick(pos, app)

    def on_release(self, pos, app):
        self._drag_nm = None

    def _pick(self, pos, app):
        band = self._band()
        nm = round(self._x_to_nm(pos[0], self._layout(self.screen_rect(app.camera))["strip"], band), 1)
        self._drag_nm = nm
        instruments.client.set(self.device, "center", nm)

    def inspector(self, app):
        rows = super().inspector(app)
        band = self._band()
        if self.live():
            rows.append(Row("Reach", f"{band['lo']:g} – {band['hi']:g} nm"))
        rows.append(Field("Calibration", "calibration/excitation_filters.json — recalibrate with "
                                         "scripts/calibrate_excitation_filters.py (Stop server first; "
                                         "reconnect to load it)"))
        return rows

    # --- Drawing -----------------------------------------------------------------------
    def _draw_spectrum(self, surface, strip, band):
        """The visible part of the strip: rendered small, scaled up, cached."""
        area = strip.clip(surface.get_clip())
        if area.width <= 0 or area.height <= 0:
            return
        a = self._x_to_nm(area.x, strip, band)
        b = self._x_to_nm(area.right, strip, band)
        key = (area.size, round(a, 2), round(b, 2))
        if self._strip_cache is None or self._strip_cache[0] != key:
            n = max(1, min(area.width, 400))
            small = pygame.Surface((n, 1))
            for i in range(n):
                small.set_at((i, 0), wavelength_rgb(a + (b - a) * (i + 0.5) / n))
            self._strip_cache = (key, pygame.transform.scale(small, area.size))   # (smoothscale crashes on 1-px-high)
        surface.blit(self._strip_cache[1], area)

    def draw_shape(self, surface, rect, camera):
        status = self.status()
        led_color = DEVICE_STATUS_COLORS.get(status, (110, 110, 120))
        band = self._band()
        s = rect.height
        radius = max(2, s // 12)
        if s < 24:                                   # tiny: a sliver of spectrum and the light
            self._draw_spectrum(surface, rect, band)
            pygame.draw.circle(surface, led_color, rect.center, max(1, s // 5))
            return

        draw_rect(surface, (14, 14, 18), rect.move(0, max(1, s // 26)), border_radius=radius)
        draw_rect(surface, INPUT_HOUSING, rect, border_radius=radius)
        draw_rect(surface, shade(INPUT_HOUSING, 1.35), rect, width=max(1, s // 60), border_radius=radius)
        layout = self._layout(rect)
        pad, header_h, strip = layout["pad"], layout["header_h"], layout["strip"]
        live = status == "ok"

        # Header: name, the band (with a swatch of its color), the status light.
        led_r = max(2, s // 26)
        led = (rect.right - pad - led_r, rect.y + pad + header_h // 2)
        if status in ("ok", "starting", "error", "missing", "connecting"):
            pygame.draw.circle(surface, shade(led_color, 0.4), led, round(led_r * 1.8))
        pygame.draw.circle(surface, led_color, led, led_r)
        size = round(header_h * 0.72)
        if size >= 8:
            font = get_font(size, "bahnschrift")
            if live and band["center"] is not None and band["width"] is not None:
                right_text, right_color = f"{band['center']:.1f} nm  ±{band['width'] / 2:g}", (236, 240, 255)
            else:
                right_text, right_color = DEVICE_STATUS_TEXT.get(status, status), shade(led_color, 1.1)
            rs = font.render(right_text, True, right_color)
            right_x = led[0] - 2 * led_r - pad - rs.get_width()
            surface.blit(rs, (right_x, led[1] - rs.get_height() // 2))
            if live and band["center"] is not None:
                sw = rs.get_height()
                swatch = pygame.Rect(right_x - pad - sw, led[1] - sw // 2, sw, sw)
                draw_rect(surface, wavelength_rgb(band["center"]), swatch, border_radius=max(2, sw // 4))
                draw_rect(surface, (20, 20, 24), swatch, width=1, border_radius=max(2, sw // 4))
                right_x = swatch.x
            title = self.text or self.NAME
            ts = font.render(clip_text(title, font, max(10, right_x - rect.x - 2 * pad)), True, (170, 172, 186))
            surface.blit(ts, (rect.x + pad, led[1] - ts.get_height() // 2))

        # The spectrum, dimmed outside what the filters are passing right now.
        self._draw_spectrum(surface, strip, band)

        def x_at(nm):
            return round(strip.x + (nm - band["lo"]) / (band["hi"] - band["lo"]) * strip.width)

        lp, sp = band["actual_lp"], band["actual_sp"]
        layer, area = alpha_layer(surface, strip)
        if layer is not None:
            if live and lp is not None and sp is not None:
                x0, x1 = max(strip.x, x_at(lp)), min(strip.right, x_at(sp))
                dim = (0, 0, 0, 175)
                if x1 <= x0:                         # edges crossed: no light gets through
                    pygame.draw.rect(layer, dim, strip.move(-area.x, -area.y))
                else:
                    pygame.draw.rect(layer, dim, (strip.x - area.x, strip.y - area.y, x0 - strip.x, strip.height))
                    pygame.draw.rect(layer, dim, (x1 - area.x, strip.y - area.y, strip.right - x1, strip.height))
            elif not live:
                pygame.draw.rect(layer, (30, 30, 36, 190), strip.move(-area.x, -area.y))
            surface.blit(layer, area)
        if live and lp is not None and sp is not None:
            for x in (x_at(lp), x_at(sp)):           # the actual edges
                if strip.x <= x <= strip.right:
                    pygame.draw.line(surface, (245, 245, 250), (x, strip.y), (x, strip.bottom - 1), max(1, s // 70))
        if live and band["center"] is not None and band["width"] is not None:
            target = pygame.Rect(x_at(band["center"] - band["width"] / 2), strip.y - 2, 0, strip.height + 4)
            target.width = max(2, x_at(band["center"] + band["width"] / 2) - target.x)
            if band["moving"] or self._drag_nm is not None:
                pygame.draw.rect(surface, ZABER_TARGET, target, width=max(1, s // 60), border_radius=2)
            tri = max(3, strip.height // 6)
            cx = x_at(band["center"])
            pygame.draw.polygon(surface, ZABER_TARGET, [(cx, strip.y + tri), (cx - tri, strip.y - 1),
                                                       (cx + tri, strip.y - 1)])
        draw_rect(surface, (8, 8, 10), strip, width=1)

        # Wavelength ticks under the strip.
        size = round(layout["label_h"] * 0.8)
        if size >= 8:
            font = get_font(size, "consolas")
            step = 50 if strip.width >= 300 else 100
            nm = math.ceil(band["lo"] / step) * step
            while nm <= band["hi"]:
                x = x_at(nm)
                pygame.draw.line(surface, (110, 112, 124), (x, strip.bottom), (x, strip.bottom + pad // 2))
                ls = font.render(f"{nm:g}", True, (140, 142, 156))
                lr = ls.get_rect(midtop=(x, strip.bottom + pad // 2))
                if lr.x >= rect.x + 2 and lr.right <= rect.right - 2:
                    surface.blit(ls, lr)
                nm += step

        if live and not band["homed"]:
            size = round(strip.height * 0.32)
            if size >= 8:
                ns = get_font(size, "bahnschrift").render("NOT HOMED — press Home", True, WARN_COLOR)
                back = ns.get_rect(center=strip.center).inflate(pad, pad // 2)
                draw_rect(surface, (14, 14, 18), back, border_radius=max(2, pad // 2))
                surface.blit(ns, ns.get_rect(center=strip.center))



# ---- PM100D power meter: a Device that's a display ---------------------------
PM_BODY = (44, 45, 52)
PM_LCD = (178, 198, 172)
PM_LCD_INK = (24, 34, 26)
PM_RED = (200, 40, 45)


def format_power(watts):
    """Power with an SI unit and 4 significant digits: '2.513 mW', '845.2 nW'."""
    if watts is None:
        return "—", ""
    magnitude = abs(watts)
    for scale, unit in ((1.0, "W"), (1e-3, "mW"), (1e-6, "µW"), (1e-9, "nW")):
        if magnitude >= scale * 0.99995:
            break
    value = watts / scale
    decimals = max(0, min(3, 3 - int(math.floor(math.log10(abs(value))))) if value else 3)
    return f"{value:.{decimals}f}", unit


class PowerMeter(Device):
    """A Thorlabs PM100D optical power meter (driver: pm100d_meter), drawn as
    the handheld console: a pale LCD with the power in big digits (auto-scaled
    nW / µW / mW / W), the correction wavelength, and a log-scale level bar
    (1 nW – 1 W). Its readings — power (W) and power_mW — can feed readouts;
    the wavelength can be set by hand or linked."""

    TYPE = "pm100d"
    MENU_LABEL = "Add PM100D power meter"
    NAME = "PM100D"
    DRIVER = "pm100d_meter"
    DEFAULT_DEVICE = "pm100d"
    SIZE = (240, 170)
    DOT_COLOR = PM_LCD
    PROPS = [Prop("address", "Address", "str", "auto",
                  prompt="VISA address (auto finds the meter; sim:// to simulate):")]

    def device_options(self):
        return {"address": self.address or "auto"}

    def _reading(self):
        """(watts, wavelength) from the server, None where unknown."""
        info = self.info() if self.connect else None
        if not info:
            return None, None
        return info["values"].get("power"), info["params"].get("wavelength", {}).get("value")

    def draw_shape(self, surface, rect, camera):
        status = self.status()
        led_color = DEVICE_STATUS_COLORS.get(status, (110, 110, 120))
        watts, wavelength = self._reading()
        s = min(rect.width, rect.height)
        radius = max(2, s // 10)
        if s < 24:                                   # tiny: the console and its display
            draw_rect(surface, PM_BODY, rect, border_radius=radius)
            draw_rect(surface, PM_LCD, rect.inflate(-rect.width // 3, -rect.height // 3))
            return

        # Console body, with the red stripe along the bottom.
        draw_rect(surface, (14, 14, 18), rect.move(0, max(1, s // 30)), border_radius=radius)
        draw_rect(surface, PM_BODY, rect, border_radius=radius)
        stripe = pygame.Rect(rect.x, rect.bottom - max(2, round(s * 0.07)), rect.width, max(2, round(s * 0.07)))
        prev_clip = surface.get_clip()              # the body's rounded shape, cut to the stripe:
        surface.set_clip(stripe.clip(prev_clip))    # rounded bottom corners via the fast draw_rect
        draw_rect(surface, PM_RED, rect, border_radius=radius)
        surface.set_clip(prev_clip)
        draw_rect(surface, shade(PM_BODY, 1.4), rect, width=max(1, s // 70), border_radius=radius)

        pad = max(2, round(s * 0.07))
        header_h = round(s * 0.14)
        led_r = max(2, s // 32)
        led = (rect.right - pad - led_r, rect.y + pad + header_h // 2)
        if status in ("ok", "starting", "error", "missing", "connecting"):
            pygame.draw.circle(surface, shade(led_color, 0.4), led, round(led_r * 1.8))
        pygame.draw.circle(surface, led_color, led, led_r)
        size = round(header_h * 0.8)
        if size >= 8:
            font = get_font(size, "bahnschrift")
            title = self.text or "PM100D"
            ts = font.render(clip_text(title, font, rect.width - 4 * pad - 2 * led_r), True, (200, 202, 214))
            surface.blit(ts, (rect.x + pad, led[1] - ts.get_height() // 2))

        # The LCD.
        lcd = pygame.Rect(rect.x + pad, rect.y + pad + header_h + pad // 2, rect.width - 2 * pad,
                          stripe.y - pad - (rect.y + pad + header_h + pad // 2))
        live = status == "ok"
        glass = PM_LCD if live else shade(PM_LCD, 0.45)
        draw_rect(surface, (20, 20, 24), lcd.inflate(4, 4), border_radius=max(2, radius - 2))   # bezel
        draw_rect(surface, glass, lcd, border_radius=max(2, radius - 4))
        ink = PM_LCD_INK if live else shade(PM_LCD, 0.3)
        inner = lcd.inflate(-2 * pad, -pad)
        if not live:
            size = round(inner.height * 0.2)
            if size >= 8:
                font = get_font(size, "bahnschrift")
                ts = font.render(clip_text(DEVICE_STATUS_TEXT.get(status, status), font, inner.width), True,
                                 shade(led_color, 0.6) if status == "error" else ink)
                surface.blit(ts, ts.get_rect(center=inner.center))
            return

        number, unit = format_power(watts)
        big = round(inner.height * 0.46)
        unit_size = round(inner.height * 0.22)
        if big >= 8:
            nfont, ufont = get_font(big, "bahnschrift"), get_font(max(8, unit_size), "bahnschrift")
            us = ufont.render(unit, True, ink)
            room = inner.width - us.get_width() - pad
            while big >= 8 and nfont.size(number)[0] > room:
                big -= 1
                nfont = get_font(big, "bahnschrift")
            ns = nfont.render(number, True, ink)
            base_y = inner.y + round(inner.height * 0.56)
            surface.blit(ns, ns.get_rect(bottomright=(inner.right - us.get_width() - pad // 2, base_y)))
            surface.blit(us, us.get_rect(bottomright=(inner.right, base_y - max(1, big // 10))))
        small = round(inner.height * 0.16)
        if small >= 8 and wavelength is not None:
            ts = get_font(small, "bahnschrift").render(f"λ {wavelength:g} nm", True, ink)
            surface.blit(ts, (inner.x, inner.y))
        # Level bar: log scale, 1 nW to 1 W.
        bar = pygame.Rect(inner.x, inner.bottom - max(3, round(inner.height * 0.13)), inner.width,
                          max(3, round(inner.height * 0.13)))
        draw_rect(surface, shade(glass, 0.85), bar, border_radius=max(1, bar.height // 3))
        if watts is not None and watts > 0:
            f = max(0.0, min(1.0, (math.log10(watts) + 9) / 9))
            if f > 0:
                draw_rect(surface, ink, (bar.x, bar.y, max(1, round(bar.width * f)), bar.height),
                          border_radius=max(1, bar.height // 3))
        for k in range(10):                          # decade ticks
            x = bar.x + round(bar.width * k / 9)
            pygame.draw.line(surface, shade(glass, 0.6), (x, bar.y - max(1, bar.height // 2)), (x, bar.y - 1), 1)



# ---- PDA45 photodetector: one detector, read by the Time Tagger and the NI DAQ ----
PDA_BODY = (36, 38, 46)
PDA_SCREEN = (10, 12, 16)
PDA_COUNTS = (110, 200, 255)    # count rate (Time Tagger)
PDA_VOLTS = (120, 230, 150)     # mean voltage (NI DAQ)
PDA_HALF_COLORS = {"connected": (90, 220, 120), "connecting…": (240, 190, 60)}   # else: an error (red)


def format_si(value, unit, digits=4):
    """A number with an SI prefix and `digits` significant digits: ('-4.598', 'mV'),
    ('295.6', 'k counts/s') — a prefix on a word unit gets a space."""
    if value is None:
        return "—", unit
    magnitude = abs(value)
    for scale, prefix in ((1e9, "G"), (1e6, "M"), (1e3, "k"), (1.0, ""), (1e-3, "m"), (1e-6, "µ"), (1e-9, "n")):
        if magnitude >= scale * 0.99995:
            break
    if not magnitude:
        scale, prefix = 1.0, ""
    scaled = value / scale
    decimals = max(0, digits - 1 - int(math.floor(math.log10(abs(scaled))))) if scaled else digits - 1
    space = " " if prefix and len(unit) > 2 else ""
    return f"{scaled:.{decimals}f}", prefix + space + unit


class PDA45(Device):
    """The Thorlabs PDA45 photodetector (driver: pda45), whose output goes to
    two instruments read over the same gate: the Swabian Time Tagger counts its
    pulses (count_rate, counts/s) and the NI 6363 averages its voltage on ai0
    (voltage, V — the mean, a charge-like signal; see ni6363.py). Each half has
    its own status light and connects on its own, so one keeps reading while the
    other is missing. Both numbers are outputs for readouts, functions, …; an
    overflow (counts lost) or a clipped sample (input range too narrow) is
    flagged on the screen. The gate (s) can be set by hand or linked."""

    TYPE = "pda45"
    MENU_LABEL = "Add PDA45 photodetector"
    NAME = "PDA45"
    DRIVER = "pda45"
    DEFAULT_DEVICE = "pda45"
    SIZE = (250, 180)
    DOT_COLOR = PDA_COUNTS
    PROPS = [Prop("tagger", "Time Tagger", "str", "auto",
                  prompt="Time Tagger serial (auto finds it; sim:// to simulate):"),
             Prop("daq", "NI DAQ", "str", "auto",
                  prompt="NI device name, e.g. Dev1 (auto finds it; sim:// to simulate):"),
             Prop("ai_range", "AI range", "float", 0.5, min=0.1, max=10, unit="± V",
                  prompt="Analog input range, ± volts (the card snaps to ±0.1 … ±10 V):")]

    def device_options(self):
        return {"tagger": self.tagger or "auto", "daq": self.daq or "auto", "ai_range": self.ai_range}

    def draw_shape(self, surface, rect, camera):
        status = self.status()
        led_color = DEVICE_STATUS_COLORS.get(status, (110, 110, 120))
        info = self.info() if self.connect else None
        live = status == "ok" and info is not None
        values = info["values"] if live else {}
        if live and not (values.get("tagger_status") == values.get("daq_status") == "connected"):
            led_color = DEVICE_STATUS_COLORS["starting"]    # running, but one half isn't reading
        s = min(rect.width, rect.height)
        radius = max(2, s // 10)
        if s < 24:                                   # tiny: the body and its status
            draw_rect(surface, PDA_BODY, rect, border_radius=radius)
            pygame.draw.circle(surface, led_color, rect.center, max(1, s // 4))
            return

        draw_rect(surface, (14, 14, 18), rect.move(0, max(1, s // 30)), border_radius=radius)
        draw_rect(surface, PDA_BODY, rect, border_radius=radius)
        draw_rect(surface, shade(PDA_BODY, 1.45), rect, width=max(1, s // 70), border_radius=radius)

        pad = max(2, round(s * 0.06))
        header_h = round(s * 0.13)
        led_r = max(2, s // 34)
        led = (rect.right - pad - led_r, rect.y + pad + header_h // 2)
        if status in ("ok", "starting", "error", "missing", "connecting"):
            pygame.draw.circle(surface, shade(led_color, 0.4), led, round(led_r * 1.8))
        pygame.draw.circle(surface, led_color, led, led_r)
        size = round(header_h * 0.8)
        if size >= 8:
            font = get_font(size, "bahnschrift")
            ts = font.render(clip_text(self.text or "PDA45", font, rect.width - 4 * pad - 2 * led_r),
                             True, (214, 216, 228))
            surface.blit(ts, (rect.x + pad, led[1] - ts.get_height() // 2))

        gate = info["params"].get("gate", {}).get("value") if live else None
        footer_h = round(s * 0.09)
        body = pygame.Rect(rect.x + pad, rect.y + pad + header_h + pad // 2, rect.width - 2 * pad,
                           rect.bottom - pad - footer_h - (rect.y + pad + header_h + pad // 2))
        gap = max(1, pad // 2)
        panels = [pygame.Rect(body.x, body.y, body.width, (body.height - gap) // 2),
                  pygame.Rect(body.x, body.y + (body.height + gap) // 2, body.width, (body.height - gap) // 2)]
        halves = [("TIME TAGGER", "tagger_status", "count_rate", "counts/s", PDA_COUNTS,
                   "OVERFLOW" if values.get("overflows") else ""),
                  ("NI DAQ · mean", "daq_status", "voltage", "V", PDA_VOLTS,
                   "OVER RANGE" if values.get("clipped") else "")]
        for panel, (label, status_key, key, unit, color, warning) in zip(panels, halves):
            self._draw_half(surface, panel, pad, label, live, status, values.get(status_key),
                            values.get(key), unit, color, warning)

        size = round(footer_h * 0.75)
        if size >= 8:
            text = f"gate {gate:g} s" if gate is not None else DEVICE_STATUS_TEXT.get(status, status)
            ts = get_font(size, "consolas").render(text, True, (150, 152, 166))
            surface.blit(ts, ts.get_rect(midleft=(rect.x + pad, rect.bottom - pad - footer_h // 2)))

    def _draw_half(self, surface, panel, pad, label, live, status, half_status, value, unit, color, warning):
        """One instrument's screen: its own status light and label, then its number."""
        radius = max(2, panel.height // 8)
        draw_rect(surface, PDA_SCREEN, panel, border_radius=radius)
        draw_rect(surface, (4, 4, 6), panel, width=1, border_radius=radius)
        inner = panel.inflate(-2 * pad, -pad)
        if live:
            half_color = PDA_HALF_COLORS.get(half_status, DEVICE_STATUS_COLORS["error"])
        else:
            half_color = DEVICE_STATUS_COLORS.get(status, (110, 110, 120))
        label_h = round(inner.height * 0.28)
        dot_r = max(1, label_h // 3)
        dot = (inner.x + dot_r, inner.y + label_h // 2)
        pygame.draw.circle(surface, half_color, dot, dot_r)
        if label_h >= 8:
            font = get_font(label_h, "bahnschrift")
            ls = font.render(label, True, shade(color, 0.7))
            surface.blit(ls, (dot[0] + 2 * dot_r, dot[1] - ls.get_height() // 2))
            if warning:
                ws = font.render(warning, True, (255, 150, 60))
                surface.blit(ws, ws.get_rect(midright=(inner.right, dot[1])))

        area = pygame.Rect(inner.x, inner.y + label_h, inner.width, inner.height - label_h)
        if not live or half_status != "connected":   # why there's no number
            text = (half_status or "—") if live else DEVICE_STATUS_TEXT.get(status, status)
            size = round(area.height * 0.32)
            if size >= 8:
                font = get_font(size, "bahnschrift")
                ts = font.render(clip_text(text, font, area.width), True,
                                 shade(half_color, 0.8) if live else (130, 130, 142))
                surface.blit(ts, ts.get_rect(center=area.center))
            return
        number, unit_text = format_si(value, unit)
        big = round(area.height * 0.78)
        if big >= 8:
            ufont = get_font(max(8, round(area.height * 0.34)), "bahnschrift")
            us = ufont.render(unit_text, True, shade(color, 0.8))
            room = area.width - us.get_width() - pad
            nfont = get_font(big, "bahnschrift")
            while big > 8 and nfont.size(number)[0] > room:
                big -= 1
                nfont = get_font(big, "bahnschrift")
            ns = nfont.render(number, True, color)
            base = area.bottom
            surface.blit(us, us.get_rect(bottomright=(area.right, base - max(1, big // 8))))
            surface.blit(ns, ns.get_rect(bottomright=(area.right - us.get_width() - pad // 2, base)))



# ---- SuperK supercontinuum laser: a Device with an emission switch ------------
SK_BODY = (30, 34, 44)
SK_PANEL = (14, 16, 22)
SK_WARN = (250, 200, 40)
SK_RED = (235, 60, 55)
SK_RAINBOW = [(150, 60, 220), (70, 90, 255), (40, 200, 230), (60, 220, 90),
              (240, 230, 60), (250, 150, 40), (240, 60, 50)]


SK_PENDING_SECONDS = 1.5        # longest a clicked switch shows where it was asked to go (2 Hz polling)
SK_STARTING_SECONDS = 30.0      # an unknown state this soon after "on" reads as STARTING…


class SuperK(Device):
    """The NKT SuperK supercontinuum laser (driver: superk_laser): a laser head
    with a warning triangle, a big EMITTING / IDLE / UNKNOWN state, a built-in
    emission switch, the power setpoint as a bar, the interlock + status lines,
    and — while emitting — a rainbow beam out of its output port.

    The safety is in the object: emission changes only through its own switch
    (click it on the canvas) or its panel button, both of which
    - show the laser's *actual* state, read back from it — never a remembered
      position, so they can't disagree with the laser;
    - turn emission OFF when the state is unknown (they never guess);
    - can't turn it on while the interlock is open (the driver refuses and the
      panel says why; "Reset interlock" appears when it needs one).
    Disconnect turns emission off first; the server also turns it off if the
    canvas closes. Emission can't be linked; the power level (0–100 %) can."""

    TYPE = "superk"
    MENU_LABEL = "Add SuperK laser"
    NAME = "SuperK laser"
    DRIVER = "superk_laser"
    DEFAULT_DEVICE = "superk"
    SIZE = (340, 150)
    DOT_COLOR = SK_RED
    PROPS = [Prop("port", "Port", "str", "auto",
                  prompt="The laser's COM port (auto scans for it, as the lab GUIs do; sim:// to simulate):")]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._phase = 0.0
        self._pending = None        # (asked-for emission, when, message then) after a click, until confirmed
        self._on_asked = 0.0        # when emission was last asked to go on (for "STARTING…")
        self._switch_hover = False

    def device_options(self):
        return {"port": self.port or "auto"}

    def laser_state(self):
        """(emitting: True / False / None=unknown, interlock_ok, interlock text, status text, power %)."""
        info = self.info() if self.connect else None
        if not info or info.get("status") != "ok":
            return None, None, "", "", None
        v = info["values"]
        emission = v.get("emission")
        return (None if emission is None else bool(emission), v.get("interlock_ok"),
                v.get("interlock", ""), v.get("status", ""),
                info["params"].get("power_level", {}).get("value"))

    def live(self):
        return self.connect and self.status() == "ok"

    def tick(self, dt):
        super().tick(dt)
        self._phase += dt
        if self._pending is not None:
            wanted, since, message_then = self._pending
            message = (self.info() or {}).get("message", "")
            refused = message != message_then and message.startswith("emission_")
            if (self.laser_state()[0] == wanted or refused
                    or time.monotonic() - since > SK_PENDING_SECONDS):
                self._pending = None              # confirmed — or refused / timed out: show the truth

    def update(self, mouse_pos, mouse_pressed, camera, dt):
        super().update(mouse_pos, mouse_pressed, camera, dt)
        self._switch_hover = (self.live() and self.is_hovered and
                              self._layout(self.screen_rect(camera))["switch"].collidepoint(mouse_pos))

    # --- Emission (the only ways it changes) ---------------------------------------
    def toggle_emission(self, app):
        emitting = self.laser_state()[0]
        wanted = not (emitting is None or emitting)       # unknown counts as on: never guess
        self._pending = (wanted, time.monotonic(), (self.info() or {}).get("message", ""))
        self._on_asked = time.monotonic() if wanted else 0.0
        self.run_action(app, "emission_on" if wanted else "emission_off")   # on is refused if interlocked

    def on_click(self, pos, app):
        """A click on the built-in switch toggles emission (and doesn't select);
        anywhere else selects as usual."""
        if self.live() and self._layout(self.screen_rect(app.camera))["switch"].inflate(6, 6).collidepoint(pos):
            self.toggle_emission(app)
            return True
        return False

    def disconnect(self, app):
        if self.connect and self.info() is not None:
            self.run_action(app, "emission_off")  # no beam left running without its controls
        super().disconnect(app)

    def buttons(self, app):
        out = []
        emitting, interlock_ok, _i, _s, _p = self.laser_state()
        if self.live():
            if emitting is None or emitting:
                out.append(Btn("Turn emission OFF", self.toggle_emission, style="stop"))
            else:
                out.append(Btn("Turn emission ON", self.toggle_emission, style="go"))
            if interlock_ok == 0:
                out.append(Btn("Reset interlock", lambda app: self.run_action(app, "reset_interlock")))
        if self.links:
            out.append(Btn("Unlink all", self.unlink_all))
        out.append(Btn("Disconnect", self.disconnect) if self.connect
                   else Btn("Connect", self.connect_now, style="go"))
        return out

    # --- Layout (shared by drawing and clicking) -----------------------------------------
    def _layout(self, rect):
        s = rect.height
        body = pygame.Rect(rect.x, rect.y, round(rect.width * 0.8), rect.height)
        pad = max(2, round(s * 0.08))
        header_h = round(s * 0.18)
        port_r = max(2, s // 16)
        panel = pygame.Rect(body.x + pad, body.y + pad + header_h + pad // 2, body.width - 2 * pad - port_r,
                            body.bottom - pad - (body.y + pad + header_h + pad // 2))
        tri_h = round(panel.height * 0.5)
        sw_h = max(4, round(panel.height * 0.34))
        switch = pygame.Rect(0, 0, round(sw_h * 2.1), sw_h)
        switch.midright = (panel.right - pad, panel.y + pad + tri_h // 2)
        return {"body": body, "pad": pad, "header_h": header_h, "port_r": port_r, "panel": panel,
                "tri": pygame.Rect(panel.x + pad, panel.y + pad, tri_h, tri_h), "switch": switch}

    # --- Drawing ----------------------------------------------------------------------
    def draw_shape(self, surface, rect, camera):
        status = self.status()
        led_color = DEVICE_STATUS_COLORS.get(status, (110, 110, 120))
        emitting, interlock_ok, interlock, issues, power = self.laser_state()
        s = rect.height
        lay = self._layout(rect)
        body, pad, panel, port_r = lay["body"], lay["pad"], lay["panel"], lay["port_r"]
        radius = max(2, s // 12)
        if s < 24:                                   # tiny: the head, red if emitting
            draw_rect(surface, SK_RED if emitting else SK_BODY, body, border_radius=radius)
            return

        # The beam first (under the head): a rainbow fan out of the output port.
        port = (body.right, body.centery)
        if emitting:
            length = rect.right - body.right
            spread = max(2, round(s * 0.12))
            flicker = 0.85 + 0.15 * math.sin(self._phase * 9)
            for i, color in enumerate(SK_RAINBOW):
                off = (i - (len(SK_RAINBOW) - 1) / 2) / (len(SK_RAINBOW) - 1) * spread
                pygame.draw.line(surface, shade(color, flicker), port, (rect.right, round(port[1] + off)),
                                 max(1, s // 45))
            pygame.draw.line(surface, (255, 255, 255), port, (port[0] + max(4, length // 3), port[1]),
                             max(1, s // 60))

        # Head: housing, the output port, a header with name + light.
        draw_rect(surface, (12, 12, 16), body.move(0, max(1, s // 30)), border_radius=radius)
        draw_rect(surface, SK_BODY, body, border_radius=radius)
        draw_rect(surface, shade(SK_BODY, 1.6), body, width=max(1, s // 70), border_radius=radius)
        pygame.draw.circle(surface, (70, 74, 86), port, port_r + max(1, s // 40))
        pygame.draw.circle(surface, (255, 250, 240) if emitting else (10, 10, 12), port, port_r)
        led_r = max(2, s // 28)
        led = (body.right - pad - led_r - port_r, body.y + pad + lay["header_h"] // 2)
        if status in ("ok", "starting", "error", "missing", "connecting"):
            pygame.draw.circle(surface, shade(led_color, 0.4), led, round(led_r * 1.8))
        pygame.draw.circle(surface, led_color, led, led_r)
        size = round(lay["header_h"] * 0.8)
        if size >= 8:
            font = get_font(size, "bahnschrift")
            ts = font.render(clip_text(self.text or "SuperK", font, body.width - 4 * pad), True, (200, 204, 218))
            surface.blit(ts, (body.x + pad, led[1] - ts.get_height() // 2))

        # Panel: warning triangle, state word, the emission switch.
        draw_rect(surface, SK_PANEL, panel, border_radius=max(2, radius - 2))
        tri_r = lay["tri"]
        tri = [(tri_r.centerx, tri_r.y), (tri_r.right, tri_r.bottom), (tri_r.x, tri_r.bottom)]
        pygame.draw.polygon(surface, SK_WARN if emitting else shade(SK_WARN, 0.3), tri)
        pygame.draw.polygon(surface, (20, 20, 20), tri, max(1, s // 70))
        bang = max(8, round(tri_r.height * 0.6))
        bs = get_font(bang, "bahnschrift", True).render("!", True, (20, 20, 20))
        surface.blit(bs, bs.get_rect(center=(tri_r.centerx, tri_r.y + round(tri_r.height * 0.62))))

        if status != "ok":
            word, color = DEVICE_STATUS_TEXT.get(status, status).upper(), shade(led_color, 1.1)
        elif emitting is None:                    # between states: right after "on" it's ramping up
            starting = time.monotonic() - self._on_asked < SK_STARTING_SECONDS
            word, color = ("STARTING…" if starting else "UNKNOWN"), (240, 190, 60)
        else:
            word, color = ("EMITTING", SK_RED) if emitting else ("IDLE", (150, 152, 166))
        text_x = tri_r.right + pad
        room = lay["switch"].x - pad - text_x if status == "ok" else panel.right - pad - text_x
        size = round(tri_r.height * 0.62)          # one size for every state: fit the longest word
        longest = max(("EMITTING", "STARTING…", word), key=lambda w: get_font(size, "bahnschrift", True).size(w)[0])
        while size >= 8 and get_font(size, "bahnschrift", True).size(longest)[0] > room:
            size -= 1
        if size >= 8:
            ws = get_font(size, "bahnschrift", True).render(word, True, color)
            surface.blit(ws, ws.get_rect(midleft=(text_x, tri_r.centery)))
        if status == "ok":
            self._draw_switch(surface, lay["switch"], emitting, s)

        # Power bar, interlock / status lines.
        small = round(panel.height * 0.13)
        if small >= 8 and status == "ok":
            font = get_font(small, "bahnschrift")
            bar = pygame.Rect(panel.x + pad, tri_r.bottom + pad // 2, panel.width - 2 * pad, max(3, small // 2))
            draw_rect(surface, (40, 42, 52), bar, border_radius=max(1, bar.height // 2))
            if power is not None:
                draw_rect(surface, (90, 170, 240), (bar.x, bar.y, max(1, round(bar.width * power / 100)), bar.height),
                          border_radius=max(1, bar.height // 2))
            y = bar.bottom + max(2, small // 4)
            surface.blit(font.render("power —" if power is None else f"power {power:.1f} %", True,
                                     (170, 190, 220)), (panel.x + pad, y))
            ls = font.render(clip_text(f"interlock: {interlock or '?'}", font, panel.width // 2), True,
                             (150, 152, 166) if interlock_ok else (240, 190, 60))
            surface.blit(ls, (panel.right - pad - ls.get_width(), y))
            if issues and issues != "ok":
                y += round(small * 1.1)
                if y + small <= panel.bottom:
                    surface.blit(font.render(clip_text(issues, font, panel.width - 2 * pad), True, (240, 190, 60)),
                                 (panel.x + pad, y))

    def _draw_switch(self, surface, sw, emitting, s):
        """The emission switch: red track + knob right = emitting, dark + knob left =
        idle, amber "?" in the middle = unknown. Just clicked: the knob shows where
        it was asked to go (amber ring) until the laser confirms."""
        pending = self._pending[0] if self._pending is not None else None
        shown = pending if pending is not None else emitting
        r = sw.height // 2
        if shown is None:
            track = (120, 90, 30)
        elif shown:
            track = SK_RED
        else:
            track = (44, 46, 56)
        if self._switch_hover:
            track = shade(track, 1.2)
        draw_rect(surface, track, sw, border_radius=r)
        draw_rect(surface, (8, 8, 10), sw, width=max(1, s // 80), border_radius=r)
        size = round(sw.height * 0.48)
        if size >= 8:
            font = get_font(size, "bahnschrift", True)
            if shown is None:
                label, x = "?", sw.centerx
            elif shown:
                label, x = "ON", sw.x + (sw.width - sw.height) // 2
            else:
                label, x = "OFF", sw.right - (sw.width - sw.height) // 2
            ts = font.render(label, True, (250, 240, 230) if shown else (150, 152, 166))
            surface.blit(ts, ts.get_rect(center=(x, sw.centery)))
        knob_r = max(2, r - max(2, sw.height // 8))
        if shown is None:
            return                                  # unknown: no knob position to claim
        kx = sw.right - r if shown else sw.x + r
        pygame.draw.circle(surface, (120, 120, 130), (kx, sw.centery + max(1, knob_r // 5)), knob_r)   # side
        pygame.draw.circle(surface, (240, 240, 244), (kx, sw.centery), knob_r)
        if pending is not None:
            pygame.draw.circle(surface, (240, 190, 60), (kx, sw.centery), knob_r, max(1, knob_r // 4))



# ---- Flip mirror: a New Focus flipper on the NI 6363 --------------------------
FM_BODY = (40, 42, 50)
FM_BEAM = (110, 230, 160)
FM_MIRROR = (205, 215, 230)
FM_PENDING_SECONDS = 3.0


class FlipMirror(Device):
    """A New Focus motorized flip mirror (driver: flip_mirror), drawn as a flip
    mount with the beam: up = the mirror at 45° sending the beam to the up path,
    down = the mirror lying flat and the beam going straight on to the down
    path. Its name (Flipper 1, Flipper 2, …) and the two path names (up /
    down) are yours to change — they're only labels here. Which mirror it
    drives (its DAQ channel, and which side flipper.py calls up) is the
    "flipper.py mirror" setting.

    There's no readback, so every position is ASSUMED (the last one commanded),
    and connecting doesn't move the mirror — its position starts unknown. Then:
    "It's at …" (you looked; nothing moves) or "Find position" (flips down then
    up — it moves, so it asks first). After that, click the mirror (or Flip) to
    flip it; the `up` param (1/0) can also be linked."""

    TYPE = "flip_mirror"
    MENU_LABEL = "Add flip mirror"
    NAME = "Flip mirror"
    DRIVER = "flip_mirror"
    DEFAULT_DEVICE = "flipper"
    SIZE = (250, 170)
    DOT_COLOR = FM_MIRROR
    PROPS = [Prop("up_name", "Up path", "str", "up", prompt="Name of the up path (mirror in the beam):"),
             Prop("down_name", "Down path", "str", "down",
                  prompt="Name of the down path (mirror out of the beam):"),
             Prop("mirror", "flipper.py mirror", "choice", "detection", choices=["detection", "excitation"]),
             Prop("channel", "Channel", "str", "auto",
                  prompt="DAQ channel (auto = the one flipper.py gives this mirror; sim:// to simulate):")]

    @classmethod
    def create(cls, app):
        def make(x, y):
            obj = cls(x, y)
            used = {o.device for o in app.scene.objects if isinstance(o, Device)}
            obj.device = next(n for n in [cls.DEFAULT_DEVICE] + [f"{cls.DEFAULT_DEVICE}{i}" for i in range(2, 1000)]
                              if n not in used)
            names = {o.text for o in app.scene.objects if isinstance(o, FlipMirror)}
            obj.text = next(f"Flipper {i}" for i in range(1, 1000) if f"Flipper {i}" not in names)
            if any(isinstance(o, FlipMirror) and o.mirror == "detection" for o in app.scene.objects):
                obj.mirror = "excitation"            # the second one is most likely the other mirror
            return obj
        app.start_placing(make)

    def load_props(self, d):
        """Saved before names were editable: keep showing what it showed then —
        the mirror's name, and flipper.py's path names once it's connected."""
        super().load_props(d)
        if "up_name" not in d:
            self.text = self.text or self.mirror.capitalize()
            self._adopt_paths = True

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._pending = None       # (asked-for is_up, when) after a click, until it reports back
        self._adopt_paths = False  # an old save: take the path names from the driver once live

    def device_options(self):
        return {"mirror": self.mirror, "channel": self.channel or "auto"}

    def mirror_state(self):
        """(is_up: True / False / None=unknown, up name, down name, flips) — is_up and
        flips are None when it isn't live. The names are this object's own."""
        info = self.info() if self.connect else None
        if not info or info.get("status") != "ok":
            return None, self.up_name, self.down_name, None
        v = info["values"]
        if self._adopt_paths and v.get("up_label") and v.get("down_label"):
            self.up_name, self.down_name = v["up_label"], v["down_label"]
            self._adopt_paths = False
            if self.scene is not None:
                self.scene.save()
        is_up = v.get("is_up")
        return (None if is_up is None else bool(is_up), self.up_name, self.down_name, v.get("flips"))

    @property
    def title(self):
        return self.text or "Flip mirror"

    def rename(self, app):
        def submit(text):
            if text.strip():
                self.text = text.strip()
                app.save()
        app.ask("Name:", submit, value=self.text)

    def live(self):
        return self.connect and self.status() == "ok"

    def tick(self, dt):
        super().tick(dt)
        if self._pending is not None:
            wanted, since = self._pending
            message = (self.info() or {}).get("message", "")
            if self.mirror_state()[0] == wanted or message or time.monotonic() - since > FM_PENDING_SECONDS:
                self._pending = None

    # --- Moving it -------------------------------------------------------------------
    def flip(self, app):
        is_up = self.mirror_state()[0]
        if is_up is None:
            return                                   # unknown: sync or find it first
        self._pending = (not is_up, time.monotonic())
        self.run_action(app, "flip")

    def find_position(self, app):
        up = self.mirror_state()[1] or "up"
        app.confirm(f"Find {self.title}'s position?",
                    f"It will MOVE: flip down, then up, ending at {up}.",
                    lambda: self.run_action(app, "find_position"), ok_label="Flip it")

    def on_click(self, pos, app):
        """A click on the mirror itself flips it (when its position is known)."""
        if self.live() and self.mirror_state()[0] is not None:
            hub, reach = self._mirror_spot(self.screen_rect(app.camera))
            if (pygame.Vector2(pos) - hub).length() <= reach:
                self.flip(app)
                return True
        return False

    def buttons(self, app):
        out = []
        is_up, up, down, _f = self.mirror_state()
        if self.live():
            if is_up is not None:
                out.append(Btn(f"Flip (to {down if is_up else up})", self.flip, style="go"))
            out.append(Btn(f"It's at {up}", lambda app: self.run_action(app, "sync_up")))
            out.append(Btn(f"It's at {down}", lambda app: self.run_action(app, "sync_down")))
            out.append(Btn("Find position (moves it)", self.find_position))
        if self.links:
            out.append(Btn("Unlink all", self.unlink_all))
        out.append(Btn("Disconnect", self.disconnect) if self.connect
                   else Btn("Connect", self.connect_now, style="go"))
        return out

    def inspector(self, app):
        rows = [Row("Name", self.title, on_click=self.rename)] + super().inspector(app)
        if self.live():
            is_up = self.mirror_state()[0]
            rows.append(Field("Note", "position unknown: say where it is, or Find position" if is_up is None
                              else "assumed — there's no readback (the handpad rocker overrides it)",
                              color=WARN_COLOR if is_up is None else None))
        return rows

    # --- Drawing ----------------------------------------------------------------------
    def _mirror_spot(self, rect):
        """(hub, reach): where the mirror's hinge sits on screen, and the click radius."""
        s = rect.height
        pad = max(2, round(s * 0.08))
        header_h = round(s * 0.16)
        area = pygame.Rect(rect.x + pad, rect.y + pad + header_h + pad // 2, rect.width - 2 * pad,
                           rect.bottom - pad - (rect.y + pad + header_h + pad // 2))
        hub = pygame.Vector2(area.x + area.width * 0.42, area.y + area.height * 0.62)
        return hub, area.height * 0.3

    def draw_shape(self, surface, rect, camera):
        status = self.status()
        led_color = DEVICE_STATUS_COLORS.get(status, (110, 110, 120))
        is_up, up, down, flips = self.mirror_state()
        if self._pending is not None:
            is_up = self._pending[0]                     # show the asked-for side until it reports
        s = rect.height
        radius = max(2, s // 12)
        if s < 24:
            draw_rect(surface, FM_BODY, rect, border_radius=radius)
            return
        draw_rect(surface, (12, 12, 16), rect.move(0, max(1, s // 30)), border_radius=radius)
        draw_rect(surface, FM_BODY, rect, border_radius=radius)
        draw_rect(surface, shade(FM_BODY, 1.45), rect, width=max(1, s // 70), border_radius=radius)

        pad = max(2, round(s * 0.08))
        header_h = round(s * 0.16)
        led_r = max(2, s // 28)
        led = (rect.right - pad - led_r, rect.y + pad + header_h // 2)
        if status in ("ok", "starting", "error", "missing", "connecting"):
            pygame.draw.circle(surface, shade(led_color, 0.4), led, round(led_r * 1.8))
        pygame.draw.circle(surface, led_color, led, led_r)
        size = round(header_h * 0.85)
        if size >= 8:
            font = get_font(size, "bahnschrift")
            title = self.title.upper()
            ts = font.render(clip_text(title, font, rect.width - 4 * pad), True, (200, 204, 218))
            surface.blit(ts, (rect.x + pad, led[1] - ts.get_height() // 2))

        hub, reach = self._mirror_spot(rect)
        area_top = rect.y + pad + header_h + pad // 2
        live = status == "ok"
        small = round(s * 0.085)
        font = get_font(max(8, small), "bahnschrift")
        beam_w = max(2, s // 45)
        up, down = up or " ", down or " "
        up_end = pygame.Vector2(hub.x, area_top + small * 1.6)
        down_end = pygame.Vector2(rect.right - pad, hub.y)
        start = pygame.Vector2(rect.x + pad, hub.y)

        # The two paths (faint), then the beam on the one in use.
        pygame.draw.line(surface, (60, 64, 76), hub, up_end, max(1, beam_w // 2))
        pygame.draw.line(surface, (60, 64, 76), hub, down_end, max(1, beam_w // 2))
        if live:
            pygame.draw.line(surface, FM_BEAM, start, hub, beam_w)
            if is_up is True:
                pygame.draw.line(surface, FM_BEAM, hub, up_end, beam_w)
            elif is_up is False:
                pygame.draw.line(surface, FM_BEAM, hub, down_end, beam_w)

        # Path names: the one in use bright, the other dim.
        if small >= 8:
            for label, at, active, anchor in ((up, up_end, is_up is True, "midbottom"),
                                              (down, down_end, is_up is False, "bottomright")):
                room = rect.width - 2 * pad if anchor == "bottomright" else 2 * (hub.x - rect.x - pad)
                ts = font.render(clip_text(label, font, max(1, room)), True,
                                 (230, 240, 236) if (live and active) else (110, 114, 128))
                r = ts.get_rect(**{anchor: (round(at.x), round(at.y) - max(2, small // 3))})
                surface.blit(ts, r)

        # The mount and its mirror: 45° in the beam (up), flat below it (down), grey "?" (unknown).
        base = pygame.Rect(0, 0, round(reach * 1.6), round(reach * 0.45))
        base.midtop = (round(hub.x), round(hub.y + reach * 0.55))
        draw_rect(surface, (28, 30, 36), base, border_radius=max(2, base.height // 3))
        length = reach * 1.15
        if is_up is True:
            d = pygame.Vector2(1, -1).normalize()
            a, b = hub - d * length / 2, hub + d * length / 2
            color = FM_MIRROR
        elif is_up is False:
            a = pygame.Vector2(hub.x - length / 2, hub.y + reach * 0.5)
            b = pygame.Vector2(hub.x + length / 2, hub.y + reach * 0.5)
            color = FM_MIRROR
        else:
            d = pygame.Vector2(1, -0.4).normalize()
            a, b = hub - d * length / 2, hub + d * length / 2
            color = (110, 112, 124)
        if self._pending is not None:
            pygame.draw.line(surface, (240, 190, 60), a, b, max(4, s // 14))      # amber: on its way
        pygame.draw.line(surface, color, a, b, max(2, s // 22))
        pygame.draw.circle(surface, (90, 94, 108), (round(hub.x), round(hub.y)), max(2, s // 40))
        if live and is_up is None and small >= 8:
            q = get_font(round(reach * 0.9), "bahnschrift", True).render("?", True, (240, 190, 60))
            surface.blit(q, q.get_rect(center=(round(hub.x + reach * 0.9), round(hub.y - reach * 0.2))))

        # Bottom line: "assumed" (no readback) / unknown / not connected.
        if small >= 8:
            if not live:
                note, color = DEVICE_STATUS_TEXT.get(status, status), shade(led_color, 1.1)
            elif is_up is None:
                note, color = "position unknown", (240, 190, 60)
            else:
                n = int(flips or 0)
                note, color = f"assumed · {n} flip{'' if n == 1 else 's'}", (130, 134, 148)
            ns = font.render(note, True, color)
            surface.blit(ns, ns.get_rect(bottomleft=(rect.x + pad, rect.bottom - pad // 2)))



# ---- Filter wheel: a Device you click a slot on ---------------------------------------
FW_BODY = (38, 40, 48)
FW_WHEEL = (66, 70, 82)
FW_FILTER = (120, 170, 235)      # a slot with a name (a filter in it)
FW_BEAM = (235, 70, 70)
FW_PENDING_SECONDS = 4.0         # longest a clicked slot shows before the wheel must report it
FW_TURN_SPEED = 240.0            # deg/sec the drawn wheel turns toward its slot


class FilterWheel(Device):
    """A Thorlabs FW102C motorized filter wheel (driver: filter_wheel), drawn
    face-on with the slot in the beam at the top. Click a slot to move there.
    Each slot has a name of your own ("OD 1", "550 LP"), and a blank one is just
    its number (an open slot). Its input is the `position` param (1–6, rounded
    to a slot): link it to a slider, switch or function in the panel and the
    wheel follows (clicking slots is off while linked). Its outputs are the
    position the wheel reports and the slot count. Connecting never moves it.

    If every filter looks off by a fixed number of slots, check the A/B
    aperture switch on the side of the unit (software can't read it)."""

    TYPE = "filter_wheel"
    MENU_LABEL = "Add filter wheel"
    NAME = "Filter wheel"
    DRIVER = "filter_wheel"
    DEFAULT_DEVICE = "wheel"
    SIZE = (250, 170)
    DOT_COLOR = FW_FILTER
    PROPS = [Prop("port", "Port", "str", "auto",
                  prompt="COM port (auto finds the Thorlabs wheel; sim:// to simulate):"),
             Prop("names", kind="data", default=[""] * 6, hidden=True)]   # slot names, "" = blank

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._pending = None       # (asked-for slot, when) after a click, until it reports back
        self._angle = None         # drawn wheel rotation, degrees (None = not drawn yet)

    def device_options(self):
        return {"port": self.port or "auto"}

    @property
    def title(self):
        return self.text or "Filter wheel"

    def rename(self, app):
        def submit(text):
            if text.strip():
                self.text = text.strip()
                app.save()
        app.ask("Name:", submit, value=self.title)

    def live(self):
        return self.connect and self.status() == "ok"

    def slot_count(self):
        info = self.info() if self.live() else None
        n = (info or {}).get("values", {}).get("slots")
        return int(n) if n else 6

    def position(self):
        """The slot the wheel reports (1-based), or None when it isn't live."""
        info = self.info() if self.live() else None
        p = (info or {}).get("values", {}).get("position")
        return int(p) if p is not None else None

    def shown_slot(self):
        """Where to draw it: the clicked slot until the wheel reports, else where it is."""
        return self._pending[0] if self._pending is not None else self.position()

    def slot_name(self, slot):
        return self.names[slot - 1] if 0 < slot <= len(self.names) else ""

    def label(self, slot):
        """A slot's name, or 'slot 3' when it's blank."""
        return self.slot_name(slot) or f"slot {slot}"

    # --- Moving it -------------------------------------------------------------------
    def go_to(self, app, slot):
        if not self.live() or "position" in self.links:
            return
        self._pending = (slot, time.monotonic())
        instruments.client.set(self.device, "position", slot)

    def tick(self, dt):
        super().tick(dt)
        if self._pending is not None:
            wanted, since = self._pending
            message = (self.info() or {}).get("message", "")
            if self.position() == wanted or message or time.monotonic() - since > FW_PENDING_SECONDS:
                self._pending = None
        slot = self.shown_slot()
        if slot is not None:                  # turn the drawn wheel (the short way) toward its slot
            target = -(slot - 1) * 360.0 / self.slot_count()
            if self._angle is None:
                self._angle = target
            diff = (target - self._angle + 180.0) % 360.0 - 180.0
            step = FW_TURN_SPEED * dt
            self._angle = target if abs(diff) <= step else self._angle + math.copysign(step, diff)

    # --- Panel --------------------------------------------------------------------------
    def rename_slot(self, app, slot):
        def submit(text):
            names = list(self.names) + [""] * max(0, slot - len(self.names))
            names[slot - 1] = text.strip()
            self.names = names
            app.save()
        app.ask(f"Name of slot {slot} (blank = open / unnamed):", submit, value=self.slot_name(slot))

    def inspector(self, app):
        rows = [Row("Name", self.title, on_click=self.rename)] + super().inspector(app)
        rows.append(Gap(6))
        here = self.position()
        for slot in range(1, self.slot_count() + 1):
            name = self.slot_name(slot)
            rows.append(Row(f"Slot {slot}" + ("  ◀ here" if slot == here else ""), name or "— (blank)",
                            on_click=lambda app, s=slot: self.rename_slot(app, s)))
        return rows

    def buttons(self, app):
        out = []
        if self.live() and "position" not in self.links:
            here = self.position()
            if here is not None:
                n = self.slot_count()
                out.append(Btn("Next slot", lambda app: self.go_to(app, here % n + 1)))
        return out + super().buttons(app)

    # --- Drawing / clicking --------------------------------------------------------------
    def _layout(self, rect):
        """(header_h, pad, wheel center, wheel radius, hole radius, hole orbit)."""
        s = rect.height
        pad = max(2, round(s * 0.07))
        header_h = round(s * 0.15)
        top = rect.y + pad + header_h + pad // 2
        radius = (rect.bottom - pad - top) / 2
        center = pygame.Vector2(rect.x + pad + radius, top + radius)
        n = self.slot_count()
        orbit = radius * 0.62
        hole = min(radius * 0.27, orbit * math.sin(math.pi / n) * 0.82)
        return header_h, pad, center, radius, hole, orbit

    def _hole_centers(self, center, orbit):
        n = self.slot_count()
        angle = self._angle or 0.0
        for slot in range(1, n + 1):
            a = math.radians(90.0 + (slot - 1) * 360.0 / n + angle)
            yield slot, pygame.Vector2(center.x + orbit * math.cos(a), center.y - orbit * math.sin(a))

    def on_click(self, pos, app):
        """A click on a slot moves the wheel there (live, and not following a link)."""
        if not self.live() or "position" in self.links:
            return False
        rect = self.screen_rect(app.camera)
        _h, _p, center, _r, hole, orbit = self._layout(rect)
        for slot, at in self._hole_centers(center, orbit):
            if (pygame.Vector2(pos) - at).length() <= hole * 1.25:
                self.go_to(app, slot)
                return True
        return False

    def draw_shape(self, surface, rect, camera):
        status = self.status()
        led_color = DEVICE_STATUS_COLORS.get(status, (110, 110, 120))
        s = min(rect.width, rect.height)
        radius = max(2, s // 12)
        if s < 24:
            draw_rect(surface, FW_BODY, rect, border_radius=radius)
            pygame.draw.circle(surface, FW_WHEEL, rect.center, max(1, s // 3))
            return
        draw_rect(surface, (12, 12, 16), rect.move(0, max(1, s // 30)), border_radius=radius)
        draw_rect(surface, FW_BODY, rect, border_radius=radius)
        draw_rect(surface, shade(FW_BODY, 1.45), rect, width=max(1, s // 70), border_radius=radius)

        header_h, pad, center, wheel_r, hole, orbit = self._layout(rect)
        led_r = max(2, s // 28)
        led = (rect.right - pad - led_r, rect.y + pad + header_h // 2)
        if status in ("ok", "starting", "error", "missing", "connecting"):
            pygame.draw.circle(surface, shade(led_color, 0.4), led, round(led_r * 1.8))
        pygame.draw.circle(surface, led_color, led, led_r)
        size = round(header_h * 0.8)
        if size >= 8:
            font = get_font(size, "bahnschrift")
            ts = font.render(clip_text(self.title, font, rect.width - 4 * pad - 2 * led_r), True, (214, 216, 228))
            surface.blit(ts, (rect.x + pad, led[1] - ts.get_height() // 2))

        # The wheel, with its slots; the one at the top is in the beam.
        live = self.live()
        shown = self.shown_slot()
        moving = self._pending is not None
        pygame.draw.circle(surface, (16, 16, 20), center + (0, max(1, s // 40)), wheel_r)
        pygame.draw.circle(surface, FW_WHEEL if live else shade(FW_WHEEL, 0.7), center, wheel_r)
        pygame.draw.circle(surface, shade(FW_WHEEL, 1.4), center, wheel_r, max(1, s // 80))
        pygame.draw.circle(surface, shade(FW_WHEEL, 0.6), center, max(2, wheel_r * 0.14))
        num_size = round(hole * 0.95)
        num_font = get_font(num_size, "bahnschrift") if num_size >= 8 else None
        for slot, at in self._hole_centers(center, orbit):
            named = bool(self.slot_name(slot))
            fill = shade(FW_FILTER, 0.9 if live else 0.45) if named else (14, 15, 19)
            pygame.draw.circle(surface, fill, at, hole)
            ring = (240, 190, 60) if moving and slot == shown else shade(FW_WHEEL, 1.5)
            pygame.draw.circle(surface, ring, at, hole, max(1, round(hole * 0.14)))
            if num_font is not None:
                ns = num_font.render(str(slot), True, (20, 24, 32) if named else (150, 154, 168))
                surface.blit(ns, ns.get_rect(center=(round(at.x), round(at.y))))
        # The beam: a red mark through the top of the wheel.
        top = pygame.Vector2(center.x, center.y - orbit)
        beam_w = max(1, round(s * 0.012))
        pygame.draw.line(surface, FW_BEAM, (top.x, center.y - wheel_r - pad * 0.6),
                         (top.x, top.y - hole - 1), beam_w)
        pygame.draw.circle(surface, FW_BEAM, top, hole + max(2, round(hole * 0.2)), beam_w)

        # Right: which slot is in the beam, big, and its name.
        text_x = round(center.x + wheel_r + pad * 1.4)
        area = pygame.Rect(text_x, round(center.y - wheel_r), rect.right - pad - text_x, round(2 * wheel_r))
        if area.width < 12:
            return
        if not live:
            lines = [(DEVICE_STATUS_TEXT.get(status, status), 0.16, shade(led_color, 1.1))]
        elif shown is None:
            lines = [("position unknown", 0.16, (240, 190, 60))]
        else:
            name = self.slot_name(shown)
            lines = [("MOVING TO" if moving else "IN THE BEAM", 0.12, (240, 190, 60) if moving else (140, 144, 160)),
                     (f"slot {shown}", 0.3, (232, 234, 242)),
                     (name or "(blank)", 0.18, FW_FILTER if name else (120, 124, 138))]
            link = self.links.get("position")
            src = self.scene.by_id.get(link.get("id")) if link and self.scene else None
            if src is not None:
                lines.append((f"follows {src.display_name}", 0.11, (120, 220, 150)))
        y = area.y + area.height * 0.08
        for text, frac, color in lines:
            size = round(area.height * frac)
            if size < 8:
                continue
            font = get_font(size, "bahnschrift")
            ts = font.render(clip_text(text, font, area.width), True, color)
            surface.blit(ts, (area.x, round(y)))
            y += ts.get_height() * 1.05


def server_object(scene):
    """The canvas's Instrument server object, or None."""
    return next((o for o in scene.objects if isinstance(o, InstrumentServer)), None)


def device_class_for(driver):
    """The canvas object for a driver's devices: a Device subclass that names
    this driver in DRIVER (custom look/controls), else the generic Device."""
    for cls in OBJECT_TYPES.values():
        if issubclass(cls, Device) and cls.DRIVER == driver:
            return cls
    return Device


def find_device_object(scene, name):
    return next((o for o in scene.objects if isinstance(o, Device) and o.device == name), None)


class InstrumentServer(Thing):
    """The instrument server, on the canvas: a little radio with an antenna.
    It's the on/off switch for the whole hardware side — nothing connects (or
    launches) until you press Connect here; then it starts instrument_server.py
    if it isn't running and stays connected (also after a canvas restart).
    Its panel lists the server's devices (click one to place it / go to it)
    and its drivers (click one to add a new device from it). Only one allowed."""

    TYPE = "instrument_server"
    MENU_LABEL = "Add instrument server"
    NAME = "Instrument server"
    CATEGORY = "Devices"
    SINGLETON = True
    copyable = False
    SIZE = (220, 170)               # the radio body is the bottom ~60%; the antenna rises above
    DOT_COLOR = (120, 220, 150)
    PROPS = [Prop("scale", "Size", "float", 1.0, min=0.25, max=50, unit="×",
                  prompt="Size (1 = normal, 2 = twice as big):"),
             Prop("connect", kind="bool", default=False, hidden=True)]   # Connect pressed (saved)
    STATE_COLORS = {"connected": (90, 220, 120), "connecting": (240, 190, 60), "off": (110, 110, 120)}

    @classmethod
    def create(cls, app):
        existing = server_object(app.scene)
        if existing is not None:                # only one: go to it instead
            app.scene.select_only(existing)
            app.camera.look_at(existing.center, app.camera.zoom, app.screen_size)
            return
        app.start_placing(lambda x, y: cls(x, y))

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._phase = 0.0

    # --- Connection -------------------------------------------------------------
    def state(self):
        """The link as a whole (this object or any device may have opened it)."""
        client = instruments.client
        if client.connected:
            return "connected"
        return "connecting" if client.enabled else "off"

    def tick(self, dt):
        self._phase += dt
        instruments.client.want(self.id, self.connect)

    def on_removed(self):
        instruments.client.want(self.id, False)

    def connect_now(self, app):
        self.connect = True
        instruments.client.want(self.id, True)
        instruments.client.retry_launch()
        app.save()

    def _all_off(self, app):
        """Master off: this and every device stop wanting the link."""
        self.connect = False
        instruments.client.want(self.id, False)
        for o in app.scene.objects:
            if isinstance(o, Device):
                o.connect = False
                instruments.client.want(o.id, False)
        app.save()

    def disconnect(self, app):
        self._all_off(app)

    def stop_server(self, app):
        def stop():
            instruments.client.send({"op": "shutdown"})
            self._all_off(app)                    # otherwise a device would relaunch it
        app.confirm("Stop the instrument server?", "Its devices will be closed.", stop)

    # --- Devices & drivers --------------------------------------------------------
    def show_device(self, app, name, driver):
        """Go to the canvas object for a server device, or pick one up to place."""
        existing = find_device_object(app.scene, name)
        if existing is not None:
            app.scene.select_only(existing)
            app.camera.look_at(existing.center, app.camera.zoom, app.screen_size)
            return
        cls = device_class_for(driver)

        def make(x, y):
            obj = cls(x, y)
            obj.device, obj.connect = name, True
            return obj
        app.start_placing(make)

    def add_device(self, app, driver):
        """New device from a driver: ask a name, then a port, then add it to
        the server (and devices.json) and pick up its object to place."""
        taken = set(instruments.client.device_names())
        base = driver.split("_")[-1] if driver.startswith("fake_") else driver
        default = next(n for n in [base] + [f"{base}{i}" for i in range(2, 100)] if n not in taken)

        def named(text):
            name = text.strip() or default

            def ported(port):
                options = {"port": port.strip()} if port.strip() else {}
                instruments.client.last_error = ""
                instruments.client.send({"op": "add_device", "name": name, "driver": driver,
                                         "options": options})
                self.show_device(app, name, driver)
            app.ask(f"Port for {name} (e.g. COM3 — leave empty if none):", ported)
        app.ask(f"Name for the new {driver} device:", named, value=default)

    # --- Inspector ------------------------------------------------------------------
    def inspector(self, app):
        state = self.state()
        client = instruments.client
        text = {"off": "off — press Connect", "connecting": "connecting…", "connected": "connected"}
        rows = [Row("Status", text[state], color=self.STATE_COLORS[state]),
                Row("Address", f"{instruments.HOST}:{instruments.PORT} (this PC only)")]
        if client.last_error:
            rows.append(Field("Server said", client.last_error, color=WARN_COLOR))
        if state == "connected":
            rows += [Gap(8), Row("Devices", "click: place / go to")]
            names = client.device_names()
            for name in names:
                info = client.device(name) or {}
                status = info.get("status", "?")
                rows.append(Row(f"   {name}", f"{info.get('driver', '?')} · {status}",
                                color=DEVICE_STATUS_COLORS.get(status),
                                on_click=lambda app, n=name, d=info.get("driver"): self.show_device(app, n, d)))
            if not names:
                rows.append(Row("   —", "none yet"))
            rows += [Gap(8), Row("Drivers", "click: add a device")]
            for drv in client.drivers:
                rows.append(Row(f"   {drv['module']}", drv["error"] or drv["title"],
                                color=WARN_COLOR if drv["error"] else None,
                                on_click=None if drv["error"] else
                                lambda app, m=drv["module"]: self.add_device(app, m)))
        return rows + [Gap(6)] + self.prop_rows()

    def buttons(self, app):
        if not instruments.client.enabled:
            return [Btn("Connect", self.connect_now, style="go")]
        out = [Btn("Disconnect all", self.disconnect)]
        if instruments.client.connected:
            out.append(Btn("Stop server", self.stop_server))
        return out

    # --- Drawing ----------------------------------------------------------------------
    def draw_shape(self, surface, rect, camera):
        state = self.state()
        light = self.STATE_COLORS[state]
        s = rect.width
        body_h = round(rect.height * 0.62)
        body = pygame.Rect(rect.x, rect.bottom - body_h, rect.width, body_h)
        radius = max(2, s // 14)
        if s < 30:                                    # tiny: just the box and its light
            draw_rect(surface, INPUT_HOUSING, body, border_radius=radius)
            pygame.draw.circle(surface, light, body.center, max(1, s // 6))
            return

        # Antenna: a mast from the top-right of the body, a ball on top, and —
        # while connected — signal waves rippling out from the tip.
        ax = body.right - round(s * 0.17)
        tip = (ax, rect.y + round(s * 0.07))
        mast_w = max(2, s // 55)
        draw_rect(surface, (34, 34, 42), (ax - mast_w * 2, body.top - max(2, s // 40),
                                                 mast_w * 4, max(2, s // 40) + 2), border_radius=2)
        pygame.draw.line(surface, (150, 152, 166), (ax, body.top), tip, mast_w)
        if state == "connected":
            for k in range(3):
                f = (self._phase * 0.6 + k / 3) % 1.0
                r = round(s * (0.05 + 0.2 * f))
                fade = 1.0 - f
                col = tuple(round(bg + (c - bg) * fade) for c, bg in zip(light, (25, 25, 30)))   # fade into the background
                box = pygame.Rect(0, 0, 2 * r, 2 * r)
                box.center = tip
                width = max(1, s // 70)
                draw_arc(surface, col, box, -0.75, 0.75, width)                 # right
                draw_arc(surface, col, box, math.pi - 0.75, math.pi + 0.75, width)   # left
        ball = max(2, s // 32)
        blink = state != "connecting" or (self._phase * 2) % 1 < 0.6
        tip_color = light if blink else shade(light, 0.4)
        if state == "connected":
            pygame.draw.circle(surface, shade(light, 0.35), tip, round(ball * 2))
        pygame.draw.circle(surface, tip_color, tip, ball)

        # Body: housing, a speaker grille, a display, and a name plate.
        draw_rect(surface, (14, 14, 18), body.move(0, max(1, s // 45)), border_radius=radius)
        draw_rect(surface, INPUT_HOUSING, body, border_radius=radius)
        draw_rect(surface, shade(INPUT_HOUSING, 1.35), body, width=max(1, s // 80), border_radius=radius)
        pad = max(2, round(s * 0.055))
        grille = pygame.Rect(body.x + pad, body.y + pad, round(body.width * 0.27), body.height - 2 * pad)
        draw_rect(surface, (30, 30, 37), grille, border_radius=max(2, radius - 2))
        hole = max(1, s // 90)
        step = max(3, s // 30)
        for gy in range(grille.y + step // 2 + 1, grille.bottom - step // 2, step):
            for gx in range(grille.x + step // 2 + 1, grille.right - step // 2, step):
                pygame.draw.circle(surface, (12, 12, 15), (gx, gy), hole)

        display = pygame.Rect(grille.right + pad, body.y + pad,
                              body.right - pad - (grille.right + pad), round(body.height * 0.48))
        draw_rect(surface, shade(light, 0.08), display, border_radius=max(2, radius - 3))
        draw_rect(surface, (8, 8, 10), display, width=1, border_radius=max(2, radius - 3))
        if state == "connected":
            n = len(instruments.client.device_names())
            lines = ["ONLINE", f"{n} device{'s' if n != 1 else ''}"]
        else:
            lines = ["CONNECTING…" if state == "connecting" else "OFFLINE", "127.0.0.1"]
        size = round(display.height * 0.32)
        if size >= 8:
            font = get_font(size, "consolas", True)
            for i, line in enumerate(lines):
                ts = font.render(clip_text(line, font, display.width - 2 * pad), True,
                                 light if i == 0 else shade(light, 0.7))
                surface.blit(ts, (display.x + pad, display.y + round(display.height * (0.12 + 0.44 * i))))

        # Status light and the name plate under the display.
        led_r = max(2, s // 40)
        led = (display.right - led_r * 2, display.bottom + pad + led_r)
        pygame.draw.circle(surface, light if blink else shade(light, 0.4), led, led_r)
        size = round(body.height * 0.13)
        room = display.width - 4 * led_r
        while size >= 8 and get_font(size, "bahnschrift").size("INSTRUMENT SERVER")[0] > room:
            size -= 1
        if size >= 8:
            ts = get_font(size, "bahnschrift").render("INSTRUMENT SERVER", True, (170, 172, 186))
            surface.blit(ts, (display.x, display.bottom + pad))
