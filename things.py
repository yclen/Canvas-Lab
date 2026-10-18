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
    tick(dt)     called every frame (dt = seconds) — animate here. It's the
                 world, so it never reads the mouse, camera or is_selected /
                 is_hovered (those belong to one player's view, see hover()).
    DOT_COLOR    the color it shows as when zoomed far out.
    CATEGORY     an E-menu tab to also list it under (e.g. "Lights"); NAME = tile name.
    icon_sample() / draw_icon(surface)   customize its E-menu icon (default: draws
                 a sample of it with draw_shape, scaled to fit).
    draw_layer   paint order (walls -2, trains -1, things 0, name tags 1).
    outputs() / gives()   values it offers other objects ({name: number}).
    TAKES / takes()       its inputs (Take): each can follow another object's
                 output; read one with self.take(port). See Function, Counter.
    state_rows / info_rows / actions / menu_items / panel_status (app)
                 fill in the standard panel (see "The panel" in docs/ARCHITECTURE.md).
    Subclass another Thing to share its drawing/props/behavior.

`app` (in buttons and callbacks) has: app.scene (the world), app.player
(this window's view: camera, selection), app.camera, app.save(),
app.ask(prompt, on_submit), app.confirm(...), app.place(obj).
See "Adding an object type" in docs/ARCHITECTURE.md.
"""

import base64
import copy
import csv
import json
import math
import os
import random
import re
import subprocess
import sys
import threading
import time
from datetime import datetime

import numpy as np
import pygame

import expressions
import instruments
import plotting
from canvas import (OBJECT_TYPES, PORT_REACH, SAVES_DIR, WARN_COLOR, Btn, CanvasObject, Field, Give, Prop, Row,
                    Scene, ScrollList, Section, Stepper, Take, Thing, _remap_ids, alpha_layer, clip_text, draw_arc,
                    draw_rect, font_px, format_value, get_font, group_bounds, machines, new_id, object_from_dict,
                    panel_button, run_action, save_machine, short_name, _save_machines)
from plotting import nice_step
from world_link import world_time


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
        Prop("on", "On", "bool", False, state=True),
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
    SIZE = (64, 64)
    PROPS = [Prop("scale", "Size", "float", 1.0, min=0.25, max=50, unit="×",
                  prompt="Size (1 = normal, 2 = twice as big):")]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._held = False        # mouse went down on it and is still down
        self._flash = 0.0

    @property
    def pressed(self):
        """Pushed in: held down, or just tapped. World state (it's an output),
        so it comes from presses and releases, never from where the mouse is."""
        return self._held or self._flash > 0

    def on_drag(self, pos, app):
        if self._held and not self.hit(pos, app.camera):
            self._held = False        # slid off it: that lets go

    def on_release(self, pos, app):
        self._held = False

    def tick(self, dt):
        self._flash = max(0.0, self._flash - dt)

    def on_click(self, pos, app):
        self._held = True
        self._flash = INPUT_FLASH
        self.press(app)
        return True               # handled: a click presses it, it doesn't select it

    def press(self, app):
        """What one press does. Default: run its command, if one is set."""
        run_action(self, app)

    def outputs(self):
        return {"pressed": 1.0 if self.pressed else 0.0}    # to count presses, link a Counter

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
    PROPS = [Prop("on", "On", "bool", False, state=True),
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
        return {"on": 1.0 if self.on else 0.0}

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
    SIZE = (56, 0)                  # width; the height is the `length` prop
    DOT_COLOR = (90, 170, 240)
    PROPS = [Prop("value", "Value", "float", 0.5, state=True),
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
# DISPLAYS — things that show a value read live from another object (E ▸ Displays)
# ============================================================================
class Output(Thing):
    """Base for the display family: one input, `in`, that follows a value from
    another object (a slider's value, a switch's on, a neuron's firing, …) —
    link it from the panel's Inputs / takes. The value is read every frame into
    `self.reading` (None while unlinked, or while the source has none), and
    passed on as `value`, so displays can chain. Subclasses just draw
    `self.reading`."""

    CATEGORY = "Displays"
    PROPS = [Prop("scale", "Size", "float", 1.0, min=0.25, max=50, unit="×",
                  prompt="Size (1 = normal, 2 = twice as big):")]
    TAKES = (Take("in", "shows"),)

    @classmethod
    def migrate(cls, d):
        """Older saves kept the link as source_id / source_key."""
        if "source_id" in d or "source_key" in d:
            d = dict(d)
            sid, key = d.pop("source_id", ""), d.pop("source_key", "value")
            if sid:
                d["links"] = dict(d.get("links") or {}, **{"in": {"id": sid, "key": key}})
        return d

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.reading = None

    def tick(self, dt):
        self.reading = self.take("in")

    def outputs(self):
        return {"value": self.reading}       # so displays can chain


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


# ---- Graph: one column of a data file against another ------------------------
APP_DIR = os.path.dirname(os.path.abspath(__file__))
GRAPH_BODY = (32, 34, 42)
GRAPH_SCREEN = (8, 10, 14)
GRAPH_GRID = (28, 34, 46)
GRAPH_INK = (140, 146, 160)
GRAPH_CHECK_SECONDS = 1.0        # how often it looks for new / rewritten files
GRAPH_MAX_ROWS = 200_000         # rows read from one file


def resolve_folder(folder):
    """A data folder as typed: absolute, or relative to the app's folder."""
    folder = os.path.expanduser(str(folder or "").strip())
    return os.path.normpath(folder if os.path.isabs(folder) else os.path.join(APP_DIR, folder))


def list_datasets(folder):
    """[(file name, path, modified time)] of the CSV files in folder, newest first."""
    out = []
    for name in os.listdir(folder):
        path = os.path.join(folder, name)
        if name.lower().endswith(".csv") and os.path.isfile(path):
            out.append((name, path, os.path.getmtime(path)))
    return sorted(out, key=lambda f: f[2], reverse=True)


def read_csv_columns(path):
    """(headers, {header: numpy array}) for a CSV's number columns: the first
    row is the headers, lines starting with # are comments, a blank cell is
    NaN. A text column (a cell that isn't a number) is left out; an all-blank
    one stays (all NaN) — a script fills it in as it goes."""
    with open(path, newline="", encoding="utf-8-sig") as f:
        rows = list(csv.reader(line for line in f if not line.lstrip().startswith("#")))
    if not rows:
        return [], {}
    headers = [h.strip() or f"column {i + 1}" for i, h in enumerate(rows[0])]
    body = rows[1:GRAPH_MAX_ROWS + 1]
    columns = {}
    for i, header in enumerate(headers):
        values, text = [], False
        for row in body:
            cell = row[i].strip() if i < len(row) else ""
            try:
                values.append(float(cell) if cell else math.nan)
            except ValueError:
                values.append(math.nan)
                text = True
        if not text or np.isfinite(values).any():       # text cells among numbers: those are gaps
            columns[header] = np.array(values, dtype=float)
    return [h for h in headers if h in columns], columns


class YScale:
    """Auto-scale on / off for an object that plots (the Graph, the
    Spectrometer's screen). On, the y axis fits the data; turning it off
    freezes the axis where it is (into Y from / Y to), and typing either of
    those turns it off too. The range is in the data's own units, also on a
    log axis. A class using it provides auto_y_range() — (low, high) fitted
    to the current data, or None — and calls y_scale_changed(key) from
    on_prop_changed."""

    PROPS = [Prop("autoscale", "Auto-scale y", "bool", True),
             Prop("y_min", "Y from", "float", 0.0,
                  prompt="Bottom of the y axis (sets auto-scale off):"),
             Prop("y_max", "Y to", "float", 1.0,
                  prompt="Top of the y axis (sets auto-scale off):")]

    def auto_y_range(self):
        return None

    def y_range(self, auto):
        """The y range to draw, given the one fitted to the data: that one
        while auto-scaling, else the fixed one (in order, never empty)."""
        if self.autoscale or auto is None and not self.y_max > self.y_min:
            return auto
        lo, hi = float(self.y_min), float(self.y_max)
        if hi < lo:
            lo, hi = hi, lo
        if hi == lo:
            hi = lo + (abs(lo) * 0.1 or 1.0)
        return lo, hi

    def y_scale_changed(self, key):
        if key == "autoscale" and not self.autoscale:          # freeze where it is now
            now = self.auto_y_range()
            if now is not None:
                self.y_min, self.y_max = (float(f"{v:.4g}") for v in now)
        elif key in ("y_min", "y_max"):
            self.autoscale = False                             # a typed range means: hold this


GRAPH_MAX_SERIES = 8             # runs overlaid on one graph at most
GRAPH_PALETTE = [(120, 200, 255), (255, 170, 60), (120, 230, 140), (240, 110, 170),
                 (190, 150, 255), (240, 220, 90), (90, 220, 220), (250, 120, 100)]
FOLDER_FILE = "canvas.json"      # a data folder's graphing hints (scripts/canvas_run.py writes it)
_STAMP = re.compile(r"[_ -]?\d{8}[_ -]?\d{6}$")


def read_folder_file(folder):
    """A data folder's canvas.json: {"plot": {x, y, labels}, "runs": {file: label}}, or {}."""
    try:
        with open(os.path.join(folder, FOLDER_FILE), encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def plot_choices(plot):
    """A plot hint as {"x": [...], "y": [...], "labels": {...}}; an older (x, y) pair works too."""
    if isinstance(plot, dict):
        listed = lambda v: [v] if isinstance(v, str) else [c for c in (v or []) if isinstance(c, str)]  # noqa: E731
        return {"x": listed(plot.get("x")), "y": listed(plot.get("y")), "labels": dict(plot.get("labels") or {})}
    if isinstance(plot, (list, tuple)) and len(plot) == 2:
        return {"x": [plot[0]], "y": [plot[1]], "labels": {}}
    return {"x": [], "y": [], "labels": {}}


ARCHIVE = "archive"              # the group listed last (old runs out of the way)


def list_groups(folder):
    """[(subfolder, number of CSV files)] in a data folder: its groups, by
    name, with "archive" last. Hidden / underscore folders are skipped."""
    out = []
    try:
        entries = sorted(os.scandir(folder), key=lambda e: (e.name.lower() == ARCHIVE, e.name.lower()))
    except OSError:
        return out
    for entry in entries:
        if entry.is_dir() and not entry.name.startswith((".", "_")):
            try:
                count = sum(1 for f in os.scandir(entry.path) if f.is_file() and f.name.lower().endswith(".csv"))
            except OSError:
                count = 0
            out.append((entry.name, count))
    return out


def merged_hints(root, folder):
    """A group's graphing hints: the data folder's canvas.json (where scripts
    write it), with the group's own, if it has one, on top."""
    hints = read_folder_file(root)
    if folder != root:
        own = read_folder_file(folder)
        if own.get("plot"):
            hints["plot"] = own["plot"]
        runs = dict(hints["runs"]) if isinstance(hints.get("runs"), dict) else {}
        if isinstance(own.get("runs"), dict):
            runs.update(own["runs"])
        hints["runs"] = runs
    return hints


def run_label(name, labels):
    """A run's legend name: the label its script gave it, else its file name
    without the extension and the trailing date_time stamp."""
    return labels.get(name) or _STAMP.sub("", os.path.splitext(name)[0]) or os.path.splitext(name)[0]


class _GraphWatch:
    """The world's bookkeeping for a Graph (not sent to players, not saved)."""

    def __init__(self):
        self.next_check = 0.0
        self.key = None              # what's on screen: (files + modified times, columns, hints)
        self.files = {}              # path -> (modified time, headers, columns): each file read once per change
        self.seq = 0


class Graph(YScale, Thing):
    """A graph of one column of a data folder's runs against another — one
    run, or several overlaid. Link **Runs from** to a Script's runs (or set
    Settings ▸ Data folder): the folder's CSV files are the runs, newest first,
    in the panel's Datasets list — click a run to add it to the graph (it gets
    the next color and a line in the legend), again to take it off. **Latest**
    is one of them: on (the default), the newest run is shown too, and while a
    linked Script runs, the run it's writing, growing point by point.

    Groups: subfolders of the data folder (e.g. archive/, a series of
    runs) — the Group stepper picks one; the Datasets list then holds only
    its runs, Latest is its newest, and each group keeps its own selection.
    Scripts write to the data folder itself, "(main folder)".

    X / Y cycle the columns the folder's script declared (its canvas.json —
    e.g. power against count rate or voltage for the detector sweep), with
    their axis names; Settings ▸ All columns offers every number column.
    Legends use the run labels the script gave (e.g. "550 nm · sampleA").
    Log axes, auto-scale (over all the runs shown) and lines / points are
    Settings.

    The world reads the files (on the PC with the data) and sends the two
    columns of each run to every window, packed as float32; nothing is saved
    with the canvas. Drawing uses the shared plotter (plotting.py)."""

    TYPE = "graph"
    MENU_LABEL = "Add graph"
    NAME = "Graph"
    CATEGORY = "Displays"
    TAKES = (Take("runs", "Runs from", kind="data", verb="shows"),)    # a Script's runs
    SIZE = (440, 290)
    DOT_COLOR = (120, 200, 255)
    PROPS = [Prop("scale", "Size", "float", 1.0, min=0.25, max=50, unit="×",
                  prompt="Size (1 = normal, 2 = twice as big):"),
             Prop("folder", "Data folder", "str", "experiments/detector_sweep",
                  prompt="Folder of CSV files (relative to the app's folder, or a full path):"),
             Prop("latest", kind="bool", default=True, hidden=True),   # show the newest run
             Prop("group", kind="str", default="", hidden=True),       # a subfolder; "" = the folder itself
             Prop("pinned", kind="data", default=[], hidden=True),     # runs added by hand: "group/file" paths
             Prop("x_col", kind="str", default="", hidden=True),
             Prop("y_col", kind="str", default="", hidden=True),
             Prop("all_columns", "All columns", "bool", False),
             Prop("log_x", "Log x axis", "bool", False),
             Prop("log_y", "Log y axis", "bool", False),
             Prop("style", "Draw", "choice", "both", choices=["both", "lines", "points"])]

    @classmethod
    def migrate(cls, d):
        """Before overlays a graph showed one run: `dataset` ("" = the latest)."""
        if "dataset" in d and "pinned" not in d:
            d = dict(d)
            name = d.pop("dataset") or ""
            d["latest"], d["pinned"] = not name, [name] if name else []
        return d

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Sent to players (plain values): what's in the folder, and what's on screen.
        self.datasets = []           # [[file name, "YYYY-MM-DD HH:MM", legend label]], newest first
        self.groups = []             # [[subfolder, number of runs]]
        self.x_choices = []          # the columns X / Y cycle through
        self.y_choices = []
        self.shown = None            # {"x_col", "y_col", "x_label", "y_label", "series": [{"name", "label",
        #                              "latest", "n", "seq", "x", "y"}]} — x / y packed
        self.problem = ""            # why nothing (or not everything) is shown
        self._watch = _GraphWatch()  # the world's own (skipped when sending: not plain data)
        self._curves = []            # one CurveImage per series
        self._decoded = {}           # series name -> (seq, xs, ys)

    @classmethod
    def icon_sample(cls):
        sample = cls(0, 0)
        x = np.linspace(0, 1, 30)
        series = [{"name": f"r{k}", "label": "", "latest": False, "n": 30, "seq": 0,
                   "x": plotting.pack_floats(x), "y": plotting.pack_floats((1 - np.exp(-3 * x)) * (1 - 0.3 * k))}
                  for k in range(2)]
        sample.shown = {"x_col": "x", "y_col": "y", "x_label": "", "y_label": "", "series": series}
        return sample

    # --- The world: watch the folder, read the files ------------------------------------
    def on_prop_changed(self, key):
        self.y_scale_changed(key)
        self.recheck()

    def on_links_changed(self):
        self.recheck()

    def source_runs(self):
        """The linked Script's runs ({folder, latest, live, plot, label, …}), or None."""
        value = self.take("runs")
        return value if isinstance(value, dict) and value.get("folder") else None

    def recheck(self):
        self._watch.next_check = 0.0
        self._watch.key = None

    def _read(self, path, mtime):
        """(headers, columns) of a file, read again only when it changed."""
        cached = self._watch.files.get(path)
        if cached is not None and cached[0] == mtime:
            return cached[1], cached[2]
        headers, columns = read_csv_columns(path)
        if columns:                                    # empty: caught mid-write, keep the last good read
            self._watch.files[path] = (mtime, headers, columns)
        elif cached is not None:
            return cached[1], cached[2]
        return headers, columns

    def tick(self, dt):
        w = self._watch
        now = time.monotonic()
        if now < w.next_check:
            return
        w.next_check = now + GRAPH_CHECK_SECONDS
        runs = self.source_runs()                      # linked to a Script: its folder, its run
        root = resolve_folder(runs["folder"] if runs else self.folder)
        groups = [[name, count] for name, count in list_groups(root)]
        if groups != self.groups:
            self.groups = groups
        group = self.group if self.group in [g[0] for g in groups] else ""
        folder = os.path.join(root, group) if group else root
        try:
            files = list_datasets(folder)
        except OSError:
            self._set_problem(f"no folder {folder}")
            return
        hints = merged_hints(root, folder)
        labels = hints.get("runs") if isinstance(hints.get("runs"), dict) else {}
        listing = [[name, datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M"), run_label(name, labels)]
                   for name, _p, mtime in files]
        if listing != self.datasets:
            self.datasets = listing
        if self.group and not group:
            note = f"no group {self.group} any more — showing the main folder"
        else:
            note = ""
        if not files:
            self._set_problem(note or ("no CSV files in this group yet" if group else "no CSV files in this folder yet"))
            return

        # Which runs: this group's pinned ones (in the order added), then Latest.
        by_name = {f[0]: f for f in files}
        chosen = [[by_name[n], False] for n in (self._pin_name(p, group) for p in self.pinned) if n in by_name]
        if self.latest:
            live = os.path.basename(runs.get("latest") or "") if runs and runs.get("live") else ""
            newest = by_name[live] if live in by_name else files[0]
            for entry in chosen:
                if entry[0] is newest:
                    entry[1] = True                    # pinned and the latest: shown once
                    break
            else:
                chosen.append([newest, True])
        dropped = max(0, len(chosen) - GRAPH_MAX_SERIES)
        if dropped:                                    # keep Latest; drop the last pinned
            latest = [c for c in chosen if c[1]]
            chosen = [c for c in chosen if not c[1]][:GRAPH_MAX_SERIES - len(latest)] + latest
        if not chosen:
            self._set_problem("no run selected — pick one in Datasets (or turn Latest on)")
            return

        plot = plot_choices(hints.get("plot") or (runs or {}).get("plot"))
        key = (tuple((f[1], f[2], latest) for f, latest in chosen), self.x_col, self.y_col, self.all_columns,
               json.dumps(plot, sort_keys=True), json.dumps(labels, sort_keys=True))
        if key == w.key:
            return
        read, problems = [], []
        for (name, path, mtime), latest in chosen:
            try:
                headers, columns = self._read(path, mtime)
            except (OSError, UnicodeDecodeError, csv.Error) as e:
                problems.append(f"can't read {name}: {e}")
                continue
            if columns:
                read.append((name, latest, headers, columns))
            else:
                problems.append(f"no number columns in {name} (yet)")
        if not read:
            self._set_problem("; ".join(problems) or "nothing to show")
            return

        # Columns: the script's choices (those this run has), or every number column.
        headers = read[-1][2]
        mine_x = [c for c in plot["x"] if c in headers]
        mine_y = [c for c in plot["y"] if c in headers]
        x_choices = headers if self.all_columns or not mine_x else mine_x
        y_choices = headers if self.all_columns or not mine_y else mine_y
        x = self.x_col if self.x_col in x_choices else (mine_x or headers)[0]
        y = (self.y_col if self.y_col in y_choices else mine_y[0] if mine_y
             else next((c for c in headers if c != x), headers[0]))
        series = []
        for name, latest, _h, columns in read:
            if x in columns and y in columns:
                w.seq += 1
                series.append({"name": name, "label": run_label(name, labels), "latest": latest,
                               "n": int(len(columns[x])), "seq": w.seq,
                               "x": plotting.pack_floats(columns[x]), "y": plotting.pack_floats(columns[y])})
            else:
                problems.append(f"{name} has no {x if x not in columns else y}")
        if dropped:
            problems.append(f"showing {GRAPH_MAX_SERIES} runs at most")
        if note:
            problems.insert(0, note)
        w.key = key
        self.x_choices, self.y_choices = list(x_choices), list(y_choices)
        self.shown = {"x_col": x, "y_col": y, "x_label": plot["labels"].get(x, x),
                      "y_label": plot["labels"].get(y, y), "series": series}
        self.problem = "; ".join(problems)

    def _set_problem(self, text):
        self.problem, self.shown = text, None
        self._watch.key = None

    # --- Choosing what to show (inspector) ----------------------------------------------
    @staticmethod
    def _pin_name(pin, group):
        """A pin ("group/file", or "file" in the main folder) -> the file name, if it's in `group`."""
        g, _, name = pin.rpartition("/")
        return name if g == group else None

    def _pin(self, name):
        return f"{self.group}/{name}" if self.group else name

    def toggle_run(self, app, name):
        pinned, pin = list(self.pinned), self._pin(name)
        if pin in pinned:
            pinned.remove(pin)
        else:
            pinned.append(pin)
        self.pinned = pinned
        self.recheck()
        app.save()

    def toggle_latest(self, app):
        self.latest = not self.latest
        self.recheck()
        app.save()

    def only_latest(self, app):
        """This group back to just its latest run (other groups keep theirs)."""
        self.pinned = [p for p in self.pinned if self._pin_name(p, self.group) is None]
        self.latest = True
        self.recheck()
        app.save()

    def step_column(self, app, which, direction):
        choices = self.x_choices if which == "x_col" else self.y_choices
        if not choices:
            return
        current = (self.shown or {}).get(which) or getattr(self, which)
        i = choices.index(current) if current in choices else -1
        setattr(self, which, choices[(i + direction) % len(choices)])
        self.recheck()
        app.save()

    def step_group(self, app, direction):
        names = [""] + [g[0] for g in self.groups]
        i = names.index(self.group) if self.group in names else 0
        self.group = names[(i + direction) % len(names)]
        self.recheck()
        app.save()

    def edit_folder(self, app):
        def submit(text):
            if text.strip():
                self.folder = text.strip()
                self.pinned, self.latest, self.group = [], True, ""   # a new folder starts on its latest run
                self.recheck()
                app.save()
        app.ask("Folder of CSV files (relative to the app's folder, or a full path):", submit, value=self.folder)

    def series(self):
        return (self.shown or {}).get("series") or []

    def state_rows(self, app):
        shown = self.shown or {}
        runs = self.source_runs()
        rows = [Row("Folder", runs["label"] if runs else self.folder, on_click=None if runs else self.edit_folder)]
        if self.groups:
            counts = dict((g, n) for g, n in self.groups)
            here = (f"{self.group} ({counts[self.group]})" if self.group in counts else
                    f"(main folder) ({len(self.datasets)})" if not self.group else self.group)
            rows.append(Stepper("Group", here, lambda app: self.step_group(app, -1),
                                lambda app: self.step_group(app, +1)))
        series = self.series()
        if series:
            names = ", ".join(s["label"] + (" (latest)" if s["latest"] else "") for s in series)
            rows.append(Row("Showing", names))
        if shown:
            rows += [Stepper("X", shown.get("x_label") or shown.get("x_col", "—"),
                             lambda app: self.step_column(app, "x_col", -1),
                             lambda app: self.step_column(app, "x_col", +1),
                             down_enabled=len(self.x_choices) > 1, up_enabled=len(self.x_choices) > 1),
                     Stepper("Y", shown.get("y_label") or shown.get("y_col", "—"),
                             lambda app: self.step_column(app, "y_col", -1),
                             lambda app: self.step_column(app, "y_col", +1),
                             down_enabled=len(self.y_choices) > 1, up_enabled=len(self.y_choices) > 1)]
        if self.problem:
            rows.append(Field("Note" if series else "Nothing to show", self.problem, color=WARN_COLOR))
        return rows

    def more_sections(self, app):
        items = [("Latest — follows new runs", "on" if self.latest else "off", self.toggle_latest)]
        items += [(label or name, "shown" if self._pin(name) in self.pinned else when,
                   lambda app, n=name: self.toggle_run(app, n)) for name, when, label in self.datasets]
        where = self.group or ("main folder" if self.groups else "")
        title = f"{where} — click to add / remove" if where else "Click a run to add / remove it"
        return [Section("Datasets", [ScrollList(title, items, empty="— (no CSV files)",
                                                key=(self.id, self.group))])]

    def actions(self, app):
        mine = any(self._pin_name(p, self.group) for p in self.pinned)
        return [Btn("Only latest", self.only_latest)] if mine or not self.latest else []

    def panel_status(self, app):
        series = self.series()
        if series:
            points = sum(s["n"] for s in series)
            return (f"{len(series)} runs, {points} points" if len(series) > 1 else f"{points} points"), (120, 200, 150)
        if self.problem:
            return self.problem, WARN_COLOR
        return "loading…", (150, 152, 166)

    # --- Drawing ------------------------------------------------------------------------
    def data(self):
        """[(key, label, latest, xs, ys)] for the series shown, each decoded once per new load."""
        out = []
        for s in self.series():
            cached = self._decoded.get(s["name"])
            if cached is None or cached[0] != s.get("seq"):
                try:
                    cached = (s.get("seq"), plotting.unpack_floats(s["x"]), plotting.unpack_floats(s["y"]))
                except (ValueError, KeyError, TypeError):
                    continue
                self._decoded[s["name"]] = cached
            out.append(((s["name"], cached[0]), s["label"], s["latest"], cached[1], cached[2]))
        if len(self._decoded) > 2 * GRAPH_MAX_SERIES:      # forget runs no longer shown
            keep = {s["name"] for s in self.series()}
            self._decoded = {k: v for k, v in self._decoded.items() if k in keep}
        return out

    def auto_y_range(self):
        """The y range fitted to all the runs shown, in the data's units."""
        ys = [self._plot_ys(y)[np.isfinite(self._plot_xs(x))] for _k, _l, _t, x, y in self.data()]
        if not ys:
            return None
        fitted = plotting.data_range(np.concatenate(ys), self.log_y)
        if fitted is None:
            return None
        return (10 ** fitted[0], 10 ** fitted[1]) if self.log_y else fitted

    def _plot_xs(self, xs):
        """x values on the plot's scale: log10 of the positive ones on a log axis."""
        return np.where(xs > 0, np.log10(np.where(xs > 0, xs, 1.0)), np.nan) if self.log_x else xs

    def _plot_ys(self, ys):
        """y values on the plot's scale: log10 of the positive ones on a log axis."""
        return np.where(ys > 0, np.log10(np.where(ys > 0, ys, 1.0)), np.nan) if self.log_y else ys

    def draw_shape(self, surface, rect, camera):
        s = min(rect.width, rect.height)
        radius = max(2, s // 14)
        draw_rect(surface, (12, 12, 16), rect.move(0, max(1, s // 40)), border_radius=radius)
        draw_rect(surface, GRAPH_BODY, rect, border_radius=radius)
        draw_rect(surface, shade(GRAPH_BODY, 1.5), rect, width=max(1, s // 90), border_radius=radius)
        if s < 24:
            return
        pad = max(2, round(s * 0.045))
        header_h = round(s * 0.1)
        seen = surface.get_clip()
        size = round(header_h * 0.8)
        series = self.series()
        if size >= 8 and seen.colliderect((rect.x, rect.y, rect.width, pad + header_h)):
            font = get_font(size, "bahnschrift")
            title = self.text or "Graph"
            ts = font.render(clip_text(title, font, rect.width // 2), True, (200, 204, 218))
            surface.blit(ts, (rect.x + pad, rect.y + pad + (header_h - ts.get_height()) // 2))
            if series:                           # which run(s), on the right
                small = get_font(max(8, round(size * 0.75)), "bahnschrift")
                name = (("latest · " if series[0]["latest"] else "") + series[0]["label"] if len(series) == 1
                        else f"{len(series)} runs")
                ns = small.render(clip_text(name, small, rect.width - ts.get_width() - 4 * pad), True, (110, 116, 130))
                surface.blit(ns, ns.get_rect(midright=(rect.right - pad, rect.y + pad + header_h // 2)))
        screen = pygame.Rect(rect.x + pad, rect.y + pad + header_h, rect.width - 2 * pad,
                             rect.height - 2 * pad - header_h)
        draw_rect(surface, GRAPH_SCREEN, screen, border_radius=max(2, radius - 3))
        prev_clip = surface.get_clip()
        surface.set_clip(screen.clip(prev_clip))
        try:
            self._draw_plot(surface, screen)
        finally:
            surface.set_clip(prev_clip)

    def _draw_plot(self, surface, screen):
        inner = screen.inflate(-max(4, screen.width // 30), -max(4, screen.height // 22))
        font_size = round(inner.height * 0.06)
        labels = font_size >= 8
        data = self.data()
        if not data:
            size = round(inner.height * 0.08)
            if size >= 8:
                font = get_font(size, "bahnschrift")
                text = self.problem or "loading…"
                ts = font.render(clip_text(text, font, inner.width), True, (90, 96, 110))
                surface.blit(ts, ts.get_rect(center=inner.center))
            return
        shown = self.shown
        # Log axes: plot log10 of the positive values (the rest can't be shown).
        plotted = [(key, label, self._plot_xs(xs), self._plot_ys(ys)) for key, label, _l, xs, ys in data]
        both = [px[np.isfinite(py)] for _k, _l, px, py in plotted]           # points with both: a run filling in
        x_range = plotting.data_range(np.concatenate(both), self.log_x) if both else None
        y_range = self.y_range(self.auto_y_range())             # fitted, or the fixed one
        if y_range is not None and self.log_y:                  # the plot's scale is log10
            fitted = plotting.data_range(np.concatenate([py for *_r, py in plotted]), True)
            y_range = (math.log10(y_range[0]) if y_range[0] > 0 else (fitted or (0, 1))[0],
                       math.log10(y_range[1]) if y_range[1] > 0 else (fitted or (0, 1))[1])
            if y_range[1] <= y_range[0]:
                y_range = (y_range[0], y_range[0] + 1.0)
        font = get_font(font_size, "bahnschrift") if labels else None

        # Margins for the labels: y tick labels on the left, x ticks + the x axis's name below.
        if labels:
            left = font.size("-0.0000")[0] + font_size
            bottom = round(font_size * 2.6)
        else:
            left = bottom = 0
        plot = pygame.Rect(inner.x + left, inner.y + round(font_size * 0.6 if labels else 0),
                           inner.width - left - (font_size if labels else 0),
                           inner.height - bottom - round(font_size * 0.6 if labels else 0))
        if plot.width < plotting.MIN_PLOT_PX or plot.height < 16:
            return                                            # too small to read: a blank screen
        if x_range is None or y_range is None:
            if labels:
                xs_all = np.concatenate([d[3] for d in data])
                ys_all = np.concatenate([d[4] for d in data])
                waiting = (shown["y_label"] if not np.isfinite(ys_all).any() else
                           shown["x_label"] if not np.isfinite(xs_all).any() else None)   # a column still empty
                ts = font.render(clip_text(f"no {waiting} yet" if waiting else
                                           f"no {'positive ' if self.log_x or self.log_y else ''}values to show",
                                           font, plot.width), True, (90, 96, 110))
                surface.blit(ts, ts.get_rect(center=plot.center))
            return
        vis = plot.clip(surface.get_clip())
        (xlo, xhi), (ylo, yhi) = x_range, y_range
        to_x = lambda v: plot.x + (v - xlo) / (xhi - xlo) * (plot.width - 1)      # noqa: E731
        to_y = lambda v: plot.y + (yhi - v) / (yhi - ylo) * (plot.height - 1)     # noqa: E731
        ticks = plotting.log_ticks if self.log_x else plotting.linear_ticks
        x_ticks = ticks(xlo, xhi, max(2, plot.width // 70))
        ticks = plotting.log_ticks if self.log_y else plotting.linear_ticks
        y_ticks = ticks(ylo, yhi, max(2, plot.height // 45))

        pygame.draw.rect(surface, (40, 46, 60), plot, 1)
        for pos, _v in x_ticks:
            x = round(to_x(pos))
            if plot.left < x < plot.right - 1:
                pygame.draw.line(surface, GRAPH_GRID, (x, plot.y + 1), (x, plot.bottom - 2))
        for pos, _v in y_ticks:
            y = round(to_y(pos))
            if plot.top < y < plot.bottom - 1:
                pygame.draw.line(surface, GRAPH_GRID, (plot.x + 1, y), (plot.right - 2, y))

        if vis.width > 0 and vis.height > 0:
            while len(self._curves) < len(plotted):
                self._curves.append(plotting.CurveImage())
            for i, (key, _label, px, py) in enumerate(plotted):
                finite = px[np.isfinite(px)]
                sorted_x = len(finite) > 1 and bool(np.all(np.diff(finite) >= 0))
                surf = self._curves[i].get((key, self.log_x, self.log_y), plot, vis, px, py, x_range, y_range,
                                           color=GRAPH_PALETTE[i % len(GRAPH_PALETTE)], style=self.style,
                                           sorted_x=sorted_x)
                surface.blit(surf, vis.topleft)

        if not labels:
            return
        seen = surface.get_clip()
        if seen.colliderect((inner.x, plot.bottom, inner.width, bottom)):
            for pos, value in x_ticks:
                x = round(to_x(pos))
                pygame.draw.line(surface, (90, 96, 110), (x, plot.bottom), (x, plot.bottom + font_size // 3))
                ts = font.render(plotting.format_tick(value), True, GRAPH_INK)
                r = ts.get_rect(midtop=(x, plot.bottom + font_size // 3))
                r.clamp_ip(pygame.Rect(inner.x, r.y, inner.width, r.height))
                surface.blit(ts, r)
            ts = font.render(clip_text(shown["x_label"] + ("  (log)" if self.log_x else ""), font, plot.width),
                             True, (110, 116, 130))
            surface.blit(ts, ts.get_rect(midbottom=(plot.centerx, inner.bottom)))
        if seen.colliderect((inner.x, plot.y, left, plot.height)):
            for pos, value in y_ticks:
                y = round(to_y(pos))
                pygame.draw.line(surface, (90, 96, 110), (plot.x - font_size // 3, y), (plot.x, y))
                ts = font.render(plotting.format_tick(value), True, GRAPH_INK)
                r = ts.get_rect(midright=(plot.x - font_size // 2, y))
                r.clamp_ip(pygame.Rect(r.x, inner.y, r.width, inner.height))
                surface.blit(ts, r)
        name = font.render(clip_text(shown["y_label"] + ("  (log)" if self.log_y else ""), font, plot.width - 8),
                           True, (110, 116, 130))
        surface.blit(name, (plot.x + 4, plot.y + 2))           # the y axis's name, top-left inside
        if len(plotted) > 1:
            points = [(to_x(px), to_y(py)) for _k, _l, pxs, pys in plotted
                      for px, py in zip(pxs.tolist(), pys.tolist()) if px == px and py == py]
            self._draw_legend(surface, plot, [(label, latest) for _k, label, latest, _x, _y in data], font_size,
                              points, name.get_height() + 4)

    def _draw_legend(self, surface, plot, entries, font_size, points, below_name):
        """A color swatch and each run's label (as many as fit), in the plot's
        corner with the fewest points under it: top right, top left (under the
        y axis's name) or bottom right."""
        size = max(8, round(font_size * 0.9))
        font = get_font(size, "bahnschrift")
        line = round(size * 1.3)
        room = max(0, (plot.height - 2 * line) // line)
        if not room:
            return
        texts = [label + (" (latest)" if latest else "") for label, latest in entries[:room]]
        swatch = round(size * 1.2)
        width = min(plot.width // 2, max(font.size(t)[0] for t in texts) + swatch + size)
        box = pygame.Rect(0, 0, width + size, line * len(texts) + size // 2)
        corners = []
        for place, at in (("topright", (plot.right - 4, plot.y + 4)),
                          ("topleft", (plot.x + 4, plot.y + below_name)),
                          ("bottomright", (plot.right - 4, plot.bottom - 4))):
            r = box.copy()
            setattr(r, place, at)
            if plot.contains(r):
                corners.append((sum(1 for p in points if r.collidepoint(p)), len(corners), r))
        if not corners:
            return
        box = min(corners)[2]                          # fewest points hidden (ties: the order above)
        layer, area = alpha_layer(surface, box)
        if layer is not None:
            layer.fill((10, 12, 16, 200))
            surface.blit(layer, area)
        y = box.y + size // 4
        for i, text in enumerate(texts):
            color = GRAPH_PALETTE[i % len(GRAPH_PALETTE)]
            cy = y + line // 2
            pygame.draw.line(surface, color, (box.x + size // 2, cy), (box.x + size // 2 + swatch, cy), 3)
            pygame.draw.circle(surface, color, (box.x + size // 2 + swatch // 2, cy), max(2, size // 5))
            ts = font.render(clip_text(text, font, width - swatch - size // 2), True, (190, 196, 210))
            surface.blit(ts, ts.get_rect(midleft=(box.x + size + swatch, cy)))
            y += line


# ---- Script: run an experiment script from the canvas ------------------------
SCRIPTS_DIR = os.path.join(APP_DIR, "scripts")
RUNS_DIR = os.path.join(SAVES_DIR, "runs")           # each Script object's status file: <id>.json
SCRIPT_CHECK_SECONDS = 0.5                          # how often a run's status file is read
SCRIPT_RESCAN_SECONDS = 5.0                         # how often the scripts folder / the script are re-read
SCRIPT_DESCRIBE_TIMEOUT = 30.0
SCRIPT_STATE_COLORS = {"idle": (120, 124, 140), "starting": (240, 190, 60), "running": (90, 170, 255),
                       "complete": (90, 220, 120), "stopped": (240, 190, 60), "failed": (235, 80, 80)}
SCRIPT_BODY = (30, 32, 40)
SCRIPT_SCREEN = (10, 12, 16)
SCRIPT_FIELD_COLOR = (255, 170, 80)                 # an input drawn on the object (its chip, its value)
SCRIPT_RENAMES = {"detector_power_sweep.py": "detector_spectral_response.py"}   # old file -> new: saves follow
SCRIPT_PRESSES = {"_run": "RUN", "_stop": "STOP"}  # its press inputs (keys no argparse dest uses) -> labels


def canvas_scripts():
    """The scripts in scripts/ that a Script object can run: the ones using canvas_run."""
    out = []
    try:
        names = sorted(os.listdir(SCRIPTS_DIR))
    except OSError:
        return out
    for name in names:
        if not name.endswith(".py") or name == "canvas_run.py":
            continue
        try:
            with open(os.path.join(SCRIPTS_DIR, name), encoding="utf-8", errors="replace") as f:
                if "canvas_run.start(" in f.read():
                    out.append(name)
        except OSError:
            pass
    return out


def console_python():
    """python.exe (with a console) even if the canvas runs under pythonw.exe."""
    exe = sys.executable
    console = os.path.join(os.path.dirname(exe), "python.exe")
    return console if os.path.exists(console) else exe


def pid_alive(pid):
    """Is process `pid` still running? (Asks Windows; never signals it.)"""
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return False
    if os.name == "nt":
        import ctypes
        kernel = ctypes.windll.kernel32
        handle = kernel.OpenProcess(0x1000, False, pid)       # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return False
        code = ctypes.c_ulong()
        ok = kernel.GetExitCodeProcess(handle, ctypes.byref(code))
        kernel.CloseHandle(handle)
        return bool(ok) and code.value == 259                  # STILL_ACTIVE
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


class _ScriptWatch:
    """The world's bookkeeping for a Script object (not sent to players, not saved)."""

    def __init__(self):
        self.next_check = 0.0
        self.next_rescan = 0.0
        self.status_mtime = None
        self.described = None        # (script, file modified time) the inputs were read for
        self.describing = None       # the thread asking the script, while it runs
        self.result = None           # what it found: ("ok", spec) / ("error", text)
        self.proc = None             # the Popen of a run started this session


class Script(Thing):
    """An experiment script, on the canvas. Pick one of the scripts in scripts/
    that use canvas_run (Script ▸ the stepper); its inputs — its command-line
    arguments, read from the script itself — become rows in the panel: click
    one to type a value (blank = the script's default), a choice cycles, an
    on/off toggles. RUN (on the object, or the panel) starts it in its own
    console window on the PC running the world (the lab PC, with the
    hardware); the object shows its stage and progress, and how it ended:
    complete, stopped or failed (with the reason). STOP asks it to stop after
    the current point — its own cleanup still runs (files saved, laser off).

    Each object's run is followed through saves/runs/<id>.json, so a run
    survives a canvas restart. Its runs are the datasets of the script's
    experiments/ folder (for a Graph). Outputs: running (1/0), progress (0-1),
    complete (1 from a run's successful end until the next run starts).

    Every number input is also an input on the canvas: link it (to a slider, a
    Function, a Counter, …) and a run uses that value — taken when RUN is
    pressed — instead of the typed one. The inputs the script names in
    canvas_run.start(show=[…]) are drawn on the object as fields: click the
    chip to link it, the value to type one.

    RUN and STOP are press inputs too: link RUN to a push button, a Timer's
    `done`, … and each 0 → 1 runs it (while
    it isn't running already); STOP's press asks it to stop."""

    TYPE = "script"
    MENU_LABEL = "Add script"
    NAME = "Script"
    CATEGORY = "Data"
    SIZE = (360, 210)
    DOT_COLOR = (90, 170, 255)
    PROPS = [Prop("scale", "Size", "float", 1.0, min=0.25, max=50, unit="×",
                  prompt="Size (1 = normal, 2 = twice as big):"),
             Prop("script", kind="str", default="", hidden=True),      # a file in scripts/
             Prop("values", kind="data", default={}, hidden=True),     # input name -> value typed
             Prop("ran_with", kind="data", default={}, hidden=True)]   # the number inputs the last run used

    @classmethod
    def migrate(cls, d):
        d = super().migrate(d)
        if d.get("script") in SCRIPT_RENAMES:
            d = dict(d, script=SCRIPT_RENAMES[d["script"]])
        return d

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Sent to players (plain values).
        self.scripts = []            # the runnable scripts in scripts/
        self.spec = None             # the script's --canvas-describe: {description, inputs, data_folder, plot}
        self.status = {"state": "idle"}    # the run's status file, as last read
        self.problem = ""            # why it can't run / describe, if so
        self._watch = _ScriptWatch()
        self._button_hover = False
        self._field_hover = None     # (input name, "chip" / "value") under the mouse

    def status_path(self):
        return os.path.join(RUNS_DIR, f"{self.id}.json")

    def state(self):
        return self.status.get("state", "idle")

    def running(self):
        return self.state() in ("starting", "running")

    # --- The world: the scripts folder, the script's inputs, the run ----------------------
    def tick(self, dt):
        w = self._watch
        now = time.monotonic()
        if w.result is not None:                       # the script answered --canvas-describe
            kind, found = w.result
            w.result = None
            if kind == "ok":
                self.spec, self.problem = found, ""
            else:
                self.spec, self.problem = None, found
        if now >= w.next_rescan:
            w.next_rescan = now + SCRIPT_RESCAN_SECONDS
            scripts = canvas_scripts()
            if scripts != self.scripts:
                self.scripts = scripts
            if not self.script and scripts:
                self.script = scripts[0]
            self._describe_if_changed()
        if now >= w.next_check:
            w.next_check = now + SCRIPT_CHECK_SECONDS
            self._read_status()

    def _describe_if_changed(self):
        """Ask the script for its inputs (in the background) when it's new or edited."""
        w = self._watch
        path = os.path.join(SCRIPTS_DIR, self.script) if self.script else ""
        try:
            mtime = os.path.getmtime(path)
        except OSError:
            if self.script:
                self.spec, self.problem = None, f"no script {self.script} in scripts/"
            return
        if w.described == (self.script, mtime) or w.describing is not None:
            return
        w.described = (self.script, mtime)

        def ask():
            try:
                done = subprocess.run([console_python(), path, "--canvas-describe"], cwd=APP_DIR,
                                      capture_output=True, text=True, encoding="utf-8", errors="replace",
                                      timeout=SCRIPT_DESCRIBE_TIMEOUT,
                                      creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                lines = [ln for ln in done.stdout.splitlines() if ln.startswith("{")]
                if done.returncode == 0 and lines:
                    w.result = ("ok", json.loads(lines[-1]))
                else:
                    last = (done.stderr or done.stdout).strip().splitlines()[-1:] or ["no answer"]
                    w.result = ("error", f"couldn't read its inputs: {last[0]}")
            except (OSError, ValueError, subprocess.TimeoutExpired) as e:
                w.result = ("error", f"couldn't read its inputs: {e}")
            finally:
                w.describing = None
        w.describing = threading.Thread(target=ask, name=f"describe {self.script}", daemon=True)
        w.describing.start()

    def _read_status(self):
        w = self._watch
        path = self.status_path()
        try:
            mtime = os.path.getmtime(path)
        except OSError:
            mtime = None
        if mtime is not None and mtime != w.status_mtime:
            try:
                with open(path, encoding="utf-8") as f:
                    status = json.load(f)
                w.status_mtime = mtime
                if status != self.status:
                    self.status = status
            except (OSError, ValueError):
                pass                                   # caught mid-write: next check
        if self.state() == "running":                  # still there? (a crash / a closed console)
            exited = w.proc.poll() if w.proc is not None else None
            if exited is not None or (w.proc is None and not pid_alive(self.status.get("pid"))):
                time.sleep(0.05)                       # its last word may have just landed
                try:
                    with open(path, encoding="utf-8") as f:
                        self.status = json.load(f)
                except (OSError, ValueError):
                    pass
                if self.state() == "running":
                    self.status = dict(self.status, state="failed",
                                       message="ended unexpectedly" + (f" (exit code {exited})"
                                                                       if exited is not None else ""))
        elif self.state() == "starting" and w.proc is not None and w.proc.poll() is not None:
            self.status = {"state": "failed", "message": f"didn't start (exit code {w.proc.returncode})"}

    # --- Inputs ------------------------------------------------------------------------
    def inputs(self):
        return (self.spec or {}).get("inputs", [])

    def value_of(self, item):
        """(value, set by you?) — your value, else the script's default."""
        if item["name"] in self.values:
            return self.values[item["name"]], True
        return item.get("default"), False

    def number_inputs(self):
        """The inputs a link can set (int / float): each is a Take, keyed by name."""
        return [i for i in self.inputs() if i["kind"] in ("int", "float")]

    def shown_inputs(self):
        """The number inputs drawn on the object (the script's show=[…])."""
        return [i for i in self.number_inputs() if i.get("show")]

    def label_of(self, item):
        return (item["flag"] or item["name"]).lstrip("-")

    def is_linked(self, item):
        return item["kind"] in ("int", "float") and self.take_source(item["name"]) is not None

    def run_value(self, item):
        """What a run would use for item now: the linked source's value (None
        while it has none), else value_of. An int input rounds a linked value."""
        if not self.is_linked(item):
            return self.value_of(item)[0]
        value = self.take(item["name"])
        if value is None:
            return None
        return int(round(value)) if item["kind"] == "int" else float(value)

    def takes(self):
        """One number input per int / float argument (typed while unlinked, or
        linked), plus any linked one the script no longer has, so it can be unlinked."""
        items = self.number_inputs()
        names = {i["name"] for i in items}

        def current(item):
            value = self.value_of(item)[0]
            return "required" if value in (None, "") and item.get("required") else value

        out = [Take(key, label, kind="press") for key, label in SCRIPT_PRESSES.items()]
        out += [Take(i["name"], self.label_of(i), unit=i.get("unit", ""), verb="sets",
                     current=lambda i=i: current(i),
                     set_by_hand=None if self.running() else (lambda app, i=i: self.edit_input(app, i)))
                for i in items]
        return out + [Take(port) for port in self.links if port not in names and port not in SCRIPT_PRESSES]

    def on_press(self, port):
        """RUN / STOP pressed through a link (in the world)."""
        if port == "_run" and not self.running():
            self.run_script(None)
        elif port == "_stop":
            self.stop_script(None)

    def missing(self):
        out = []
        for i in self.inputs():
            if self.is_linked(i):
                if self.run_value(i) is None:
                    out.append(f"{self.label_of(i)} (its source has no value)")
            elif i.get("required") and self.value_of(i)[0] in (None, ""):
                out.append(i["flag"] or i["name"])
        return out

    def edit_input(self, app, item):
        name, kind = item["name"], item["kind"]
        if kind == "bool":
            self.values = dict(self.values, **{name: not bool(self.value_of(item)[0])})
            app.save()
            return
        if item.get("choices"):
            choices = item["choices"]
            current = self.value_of(item)[0]
            i = choices.index(current) if current in choices else -1
            self.values = dict(self.values, **{name: choices[(i + 1) % len(choices)]})
            app.save()
            return

        def submit(text):
            text = text.strip()
            values = dict(self.values)
            if not text:
                values.pop(name, None)                 # blank: back to the script's default
            else:
                try:
                    values[name] = int(text) if kind == "int" else float(text) if kind == "float" else text
                except ValueError:
                    return
            self.values = values
            app.save()
        default = item.get("default")
        hint = f" — blank = default ({default})" if default not in (None, "") else ""
        value = self.values.get(name, "")
        app.ask(f"{item['flag'] or name}: {item.get('help') or ''}{hint}", submit,
                value="" if value is None else str(value))

    def command(self):
        """The script's command line: your values (only those you set) + --canvas-run."""
        args = [console_python(), os.path.join(SCRIPTS_DIR, self.script)]
        for item in self.inputs():
            linked = self.is_linked(item)
            if not linked and item["name"] not in self.values:
                continue
            value = self.run_value(item) if linked else self.values[item["name"]]
            if item["kind"] == "bool":
                if value:
                    args.append(item["flag"])
            elif item["flag"]:
                args += [item["flag"], str(value)]
            else:
                args.append(str(value))
        return args + ["--canvas-run", self.status_path()]

    # --- Run / stop ----------------------------------------------------------------------
    def run_script(self, app):
        if self.running():
            return
        if not self.spec:
            self.problem = self.problem or "the script's inputs aren't read yet"
            return
        missing = self.missing()
        if missing:
            self.problem = f"fill in {', '.join(missing)} first"
            return
        os.makedirs(RUNS_DIR, exist_ok=True)
        for leftover in (self.status_path(), self.status_path() + ".stop"):
            try:
                os.remove(leftover)
            except OSError:
                pass
        try:
            self._watch.proc = subprocess.Popen(self.command(), cwd=APP_DIR,
                                                creationflags=getattr(subprocess, "CREATE_NEW_CONSOLE", 0))
        except OSError as e:
            self.problem = f"couldn't start it: {e}"
            return
        self.problem = ""
        self._watch.status_mtime = None
        self.status = {"state": "starting", "pid": self._watch.proc.pid}
        self.ran_with = {i["name"]: self.run_value(i) for i in self.number_inputs()}
        self._save(app)

    def stop_script(self, app):
        if self.running():
            try:
                os.makedirs(RUNS_DIR, exist_ok=True)
                open(self.status_path() + ".stop", "w").close()
            except OSError as e:
                self.problem = f"couldn't ask it to stop: {e}"
                return
            self.status = dict(self.status, stage=(self.status.get("stage") or "") + " — stopping…")

    def toggle(self, app):
        (self.stop_script if self.running() else self.run_script)(app)

    def step_script(self, app, direction):
        if self.running() or not self.scripts:
            return
        i = self.scripts.index(self.script) if self.script in self.scripts else -1
        self.script = self.scripts[(i + direction) % len(self.scripts)]
        self.values, self.spec, self.problem, self.ran_with = {}, None, "", {}
        self._watch.described = None
        self._watch.next_rescan = 0.0
        app.save()

    # --- Outputs and the panel ------------------------------------------------------------
    def progress_fraction(self):
        p = self.status.get("progress")
        if p and p[1]:
            return max(0.0, min(1.0, p[0] / p[1]))
        return 1.0 if self.state() == "complete" else 0.0

    def gives(self):
        return [Give("running"), Give("complete"), Give("progress"), Give("runs", kind="data")]

    def outputs(self):
        return {"running": 1.0 if self.running() else 0.0,
                "complete": 1.0 if self.state() == "complete" else 0.0,
                "progress": self.progress_fraction(), "runs": self.runs()}

    def runs(self):
        """What a Graph linked to `runs` follows: the script's data folder, the
        dataset of this object's current (or last) run, whether it's running,
        and the columns the script suggests plotting. None until the script's
        inputs are read (they say where its data goes)."""
        folder = (self.spec or {}).get("data_folder")
        if not folder:
            return None
        return {"label": f"experiments/{folder}" + (" — running" if self.running() else ""),
                "folder": os.path.join(APP_DIR, "experiments", folder), "script": self.script,
                "latest": self.status.get("dataset"), "live": self.running(),
                "plot": (self.spec or {}).get("plot")}

    def panel_status(self, app):
        state = self.state()
        text = {"idle": "ready", "starting": "starting…"}.get(state, state)
        return text, SCRIPT_STATE_COLORS.get(state, (150, 152, 166))

    def state_rows(self, app):
        rows = [Stepper("Script", self.script or "— (none in scripts/)",
                        lambda app: self.step_script(app, -1), lambda app: self.step_script(app, +1),
                        down_enabled=len(self.scripts) > 1 and not self.running(),
                        up_enabled=len(self.scripts) > 1 and not self.running())]
        st = self.status
        if st.get("progress"):
            done, total = st["progress"]
            rows.append(Row("Progress", f"{done}/{total}" + (f"  {st['stage']}" if st.get("stage") else "")))
        elif st.get("stage"):
            rows.append(Row("Stage", st["stage"]))
        if st.get("message"):
            rows.append(Field("Result" if not self.running() else "Message", st["message"],
                              color=WARN_COLOR if self.state() == "failed" else None))
        if st.get("dataset"):
            rows.append(Row("Dataset", os.path.basename(st["dataset"])))
        if self.problem:
            rows.append(Field("Can't run", self.problem, color=WARN_COLOR))
        return rows

    def more_sections(self, app):
        if not self.spec:
            return [Section("Options", [Row("—", "reading the script…" if self.script and not self.problem
                                            else "pick a script")])]
        rows = []
        for item in self.inputs():
            if item["kind"] in ("int", "float"):
                continue                               # a number input: under Inputs / takes
            value, mine = self.value_of(item)
            if item["kind"] == "bool":
                shown = "on" if value else "off"
            else:
                shown = "—" if value in (None, "") else str(value)
            needed = item.get("required") and value in (None, "")
            label = self.label_of(item)
            rows.append(Row(label, ("required" if needed else shown) + ("" if mine or needed else "  (default)"),
                            color=WARN_COLOR if needed else None if mine else (130, 134, 148),
                            on_click=None if self.running() else (lambda app, it=item: self.edit_input(app, it))))
        folder = self.spec.get("data_folder")
        rows.append(Row("Data folder", f"experiments/{folder}" if folder else "—"))
        return [Section("Options", rows)]

    def actions(self, app):
        if self.running():
            return [Btn("Stop", self.stop_script, style="stop")]
        return [Btn("Run", self.run_script, style="go", enabled=bool(self.spec) and not self.missing())]

    # --- On the canvas -------------------------------------------------------------------
    def _layout(self, rect):
        s = min(rect.width, rect.height)
        pad = max(2, round(s * 0.06))
        header_h = round(s * 0.13)
        btn_h = max(4, round(s * 0.17))
        button = pygame.Rect(rect.right - pad - round(btn_h * 3.0), rect.bottom - pad - btn_h, round(btn_h * 3.0), btn_h)
        screen = pygame.Rect(rect.x + pad, rect.y + pad + header_h, rect.width - 2 * pad,
                             button.y - pad - (rect.y + pad + header_h))
        # The shown inputs, side by side left of the button: a chip (link) and the value (type).
        fields = []
        shown = self.shown_inputs()[:3]
        if shown:
            gap = max(1, pad // 2)
            room = button.x - pad - (rect.x + pad)
            w = (room - gap * (len(shown) - 1)) // len(shown)
            for n, item in enumerate(shown):
                box = pygame.Rect(rect.x + pad + n * (w + gap), button.y, w, btn_h)
                chip = pygame.Rect(box.x, box.y, min(box.width, btn_h), btn_h)
                fields.append((item, box, chip))
        return {"s": s, "pad": pad, "header_h": header_h, "button": button, "screen": screen,
                "fields": fields}

    def _world_point(self, rect_point, k):
        """A point of a _layout made on a rect k × the world size, back in world units."""
        c = self.world_center
        return (c.x - self.world_width / 2 + rect_point[0] / k, c.y - self.world_height / 2 + rect_point[1] / k)

    def input_point(self, port):
        """RUN / STOP's wires end on top of the button; a shown input's at its
        chip; the others at the left edge."""
        k = 4
        lay = self._layout(pygame.Rect(0, 0, round(self.world_width * k), round(self.world_height * k)))
        button = lay["button"]
        if port in SCRIPT_PRESSES:
            third = button.width / 3 * (1 if port == "_run" else 2)
            return self._world_point((button.x + third, button.y), k)
        for item, _box, chip in lay["fields"]:
            if item["name"] == port:
                return self._world_point(chip.center, k)
        c = self.world_center
        return (c.x - self.world_width / 2, c.y)

    def field_at(self, pos, camera):
        """(input, "chip" / "value") of the shown input under screen pos, or None."""
        for item, box, chip in self._layout(self.screen_rect(camera))["fields"]:
            if box.inflate(4, 4).collidepoint(pos):
                return item, "chip" if pos[0] < chip.right else "value"
        return None

    def take_at(self, pos, camera):
        found = self.field_at(pos, camera)
        return found[0]["name"] if found else None

    def hover(self, mouse_pos, mouse_pressed, camera):
        super().hover(mouse_pos, mouse_pressed, camera)
        self._button_hover = (self.is_hovered and
                              self._layout(self.screen_rect(camera))["button"].collidepoint(mouse_pos))
        found = self.field_at(mouse_pos, camera) if self.is_hovered else None
        self._field_hover = (found[0]["name"], found[1]) if found else None

    def on_click(self, pos, app):
        """A click on RUN / STOP runs or stops the script; on a shown input's
        chip links it (a linked one's value too), on its value types one. None
        of these select it."""
        if self._layout(self.screen_rect(app.camera))["button"].inflate(6, 6).collidepoint(pos):
            self.toggle(app)
            return True
        found = self.field_at(pos, app.camera)
        if found:
            item, part = found
            if part == "chip" or self.is_linked(item):
                self.pick(app, item["name"])
            elif not self.running():
                self.edit_input(app, item)
            return True
        return False

    def field_text(self, item):
        """(value text, color) a shown input's field reads: the value the running
        run used, else the linked source's live value, else the typed / default one."""
        if self.running() and item["name"] in self.ran_with:
            value = self.ran_with[item["name"]]
        else:
            value = self.run_value(item)
        if value in (None, ""):
            if self.is_linked(item):
                return "—", WARN_COLOR
            return ("required", WARN_COLOR) if item.get("required") else ("—", (130, 136, 150))
        text = f"{value:.6g}" if isinstance(value, (int, float)) else str(value)
        unit = item.get("unit", "")
        mine = self.is_linked(item) or item["name"] in self.values
        return text + (f" {unit}" if unit else ""), (236, 238, 244) if mine else (150, 156, 170)

    def draw_shape(self, surface, rect, camera):
        lay = self._layout(rect)
        s, pad = lay["s"], lay["pad"]
        state = self.state()
        color = SCRIPT_STATE_COLORS.get(state, (150, 152, 166))
        radius = max(2, s // 10)
        draw_rect(surface, (12, 12, 16), rect.move(0, max(1, s // 40)), border_radius=radius)
        draw_rect(surface, SCRIPT_BODY, rect, border_radius=radius)
        draw_rect(surface, shade(color, 0.8) if self.running() else shade(SCRIPT_BODY, 1.6), rect,
                  width=max(1, s // 70), border_radius=radius)
        if s < 24:
            pygame.draw.circle(surface, color, rect.center, max(1, s // 4))
            return
        seen = surface.get_clip()

        # Header: "▶ script name" and the state badge.
        size = round(lay["header_h"] * 0.78)
        if size >= 8 and seen.colliderect((rect.x, rect.y, rect.width, pad + lay["header_h"])):
            font = get_font(size, "bahnschrift")
            badge_text = {"idle": "READY", "starting": "STARTING"}.get(state, state.upper())
            bf = get_font(max(8, round(size * 0.72)), "bahnschrift", True)
            bs = bf.render(badge_text, True, (16, 16, 20))
            badge = pygame.Rect(0, 0, bs.get_width() + size, round(size * 1.05))
            badge.midright = (rect.right - pad, rect.y + pad + lay["header_h"] // 2)
            if self.running():
                glow = 0.75 + 0.25 * math.sin(world_time() * 5)   # the world's clock: no per-frame state
                draw_rect(surface, shade(color, glow), badge, border_radius=badge.height // 2)
            else:
                draw_rect(surface, color, badge, border_radius=badge.height // 2)
            surface.blit(bs, bs.get_rect(center=badge.center))
            title = self.text or os.path.splitext(self.script)[0].replace("_", " ") or "Script"
            ts = font.render(clip_text(title, font, badge.x - rect.x - 2 * pad), True, (204, 208, 222))
            surface.blit(ts, ts.get_rect(midleft=(rect.x + pad, badge.centery)))

        # Screen: what it's doing / how it ended, and a progress bar.
        screen = lay["screen"]
        draw_rect(surface, SCRIPT_SCREEN, screen, border_radius=max(2, radius - 3))
        line = round(screen.height * 0.17)
        if line >= 8 and screen.height > 20 and seen.colliderect(screen):
            font = get_font(line, "consolas")
            inner = screen.inflate(-2 * pad, -pad)
            st = self.status
            if self.running():
                lines = [(st.get("stage") or "starting…", (180, 200, 230))]
            elif state == "idle":
                missing = self.missing() if self.spec else []
                lines = [(self.problem or (f"needs {', '.join(missing)}" if missing else
                                           "ready — press RUN" if self.spec else "reading the script…"),
                          WARN_COLOR if self.problem or missing else (130, 136, 150))]
            else:
                lines = [(st.get("message") or state, shade(color, 1.1))]
            if st.get("dataset") and not self.running():
                lines.append((os.path.basename(st["dataset"]), (110, 116, 130)))
            elif self.spec and state == "idle":
                summary = [f"{self.label_of(i)}={self.values[i['name']]}" for i in self.inputs()
                           if i["name"] in self.values and not i.get("show") and not self.is_linked(i)]
                if summary:
                    lines.append((" ".join(summary), (110, 116, 130)))
            y = inner.y
            for text, ink in lines:
                if y + line > inner.bottom - line:
                    break
                surface.blit(font.render(clip_text(text, font, inner.width), True, ink), (inner.x, y))
                y += round(line * 1.25)
            bar = pygame.Rect(inner.x, inner.bottom - max(3, line // 2), inner.width, max(3, line // 2))
            draw_rect(surface, (34, 38, 48), bar, border_radius=bar.height // 2)
            frac = self.progress_fraction()
            if frac > 0:
                draw_rect(surface, color, (bar.x, bar.y, max(bar.height, round(bar.width * frac)), bar.height),
                          border_radius=bar.height // 2)
            p = st.get("progress")
            if p and p[1]:
                ps = font.render(f"{p[0]}/{p[1]}", True, (150, 156, 170))
                surface.blit(ps, ps.get_rect(bottomright=(inner.right, bar.y - 2)))

        # RUN / STOP.
        button = lay["button"]
        if seen.colliderect(button):
            running = self.running()
            can_run = bool(self.spec) and not self.missing()
            fill = (200, 60, 55) if running else (40, 150, 85) if can_run else (52, 56, 66)
            if self._button_hover and (running or can_run):
                fill = shade(fill, 1.2)
            draw_rect(surface, fill, button, border_radius=button.height // 3)
            draw_rect(surface, (8, 8, 10), button, width=max(1, s // 90), border_radius=button.height // 3)
            size = round(button.height * 0.55)
            if size >= 8:
                font = get_font(size, "bahnschrift", True)
                ink = (250, 250, 250) if running or can_run else (110, 112, 124)
                ts = font.render("STOP" if running else "RUN", True, ink)
                icon = round(size * 0.62)                  # a drawn ■ / ▶ (the font has neither)
                gap = round(size * 0.35)
                x = button.centerx - (icon + gap + ts.get_width()) // 2
                cy = button.centery
                if running:
                    draw_rect(surface, ink, (x, cy - icon // 2, icon, icon), border_radius=max(1, icon // 6))
                else:
                    pygame.draw.polygon(surface, ink, [(x, cy - icon // 2), (x, cy + icon // 2), (x + icon, cy)])
                surface.blit(ts, ts.get_rect(midleft=(x + icon + gap, cy)))

        # The shown inputs: chip (hollow until linked), name, value.
        for item, box, chip in lay["fields"]:
            if not seen.colliderect(box):
                continue
            hot = self._field_hover[1] if self._field_hover and self._field_hover[0] == item["name"] else None
            linked = self.is_linked(item)
            draw_rect(surface, SCRIPT_SCREEN, box, border_radius=box.height // 3)
            edge = shade(SCRIPT_FIELD_COLOR, 0.55) if hot == "value" and not linked else shade(SCRIPT_BODY, 1.6)
            draw_rect(surface, edge, box, width=max(1, s // 90), border_radius=box.height // 3)
            dot = shade(SCRIPT_FIELD_COLOR, 1.2) if hot else SCRIPT_FIELD_COLOR
            r = max(2, round(chip.height * 0.2))
            if linked:
                pygame.draw.circle(surface, dot, chip.center, r)
            else:
                pygame.draw.circle(surface, dot, chip.center, r, max(1, r // 2))
            text, ink = self.field_text(item)
            inner_x, right = chip.right, box.right - max(2, chip.height // 4)
            size = round(box.height * 0.44)
            while size > 7 and get_font(size, "consolas", True).size(text)[0] > (right - inner_x) * 0.62:
                size = max(7, int(size * 0.9))
            if size >= 7:
                vs = get_font(size, "consolas", True).render(text, True, ink)
                surface.blit(vs, vs.get_rect(midright=(right, box.centery)))
                lf = get_font(max(7, round(box.height * 0.34)), "bahnschrift")
                room = right - vs.get_width() - max(2, chip.height // 5) - inner_x
                if room > lf.size("ab")[0]:
                    ls = lf.render(clip_text(self.label_of(item), lf, room), True, (150, 156, 170))
                    surface.blit(ls, ls.get_rect(midleft=(inner_x, box.centery)))


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
             Prop("expression", kind="str", default="", hidden=True)]

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

    def takes(self):
        """One input per variable, in the order they first appear. (Links for
        names no longer in the equation are kept, just not shown.)"""
        return [Take(name) for name in self.equation().variables]

    def tick(self, dt):
        eq = self.equation()
        self.result, self.problem = None, ""
        if eq.error:
            self.problem = eq.error
            return
        values = {"t": time.monotonic() - FUNCTION_T0}
        unlinked = []
        for name in eq.variables:
            if self.take_source(name) is None:
                unlinked.append(name)
                continue
            value = self.take(name)
            if value is None:
                self.problem = f"{name}: its source has no value"
                return
            values[name] = value
        if unlinked:
            self.problem = f"link {', '.join(unlinked)}"
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

    def on_click(self, pos, app):
        name = self.chip_at(pos, app.camera)
        if name is not None:
            self.pick(app, name)
        else:
            self.edit_equation(app)
        return True                 # a click edits/links; Ctrl+click selects

    # --- Panel ----------------------------------------------------------------------
    def panel_status(self, app):
        return (self.problem, WARN_COLOR) if self.problem else None

    def state_rows(self, app):
        return [Field("Equation", f"y = {self.expression}" if self.expression else "— click to type one",
                      on_click=self.edit_equation),
                Row("y", self.result_text(), color=None if self.result is not None else WARN_COLOR)]

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

    def input_point(self, port):
        """World point where a variable's wire ends: its chip's left edge."""
        for n, x, y, _w, h in self._chip_boxes():
            if n == port:
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

    def take_at(self, pos, camera):
        return self.chip_at(pos, camera)

    # --- Drawing ----------------------------------------------------------------------
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
            linked = self.take_source(name) is not None
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
             Prop("high", kind="data", default=None, hidden=True)]     # maximum, or None
    TAKES = tuple(Take(name, kind="press") for name in CT_INPUTS)

    @classmethod
    def icon_sample(cls):
        sample = cls(0, 0)
        sample.value = 42
        return sample

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
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

    def on_press(self, port):
        getattr(self, port)()          # up / down / reset, once per 0 → 1

    def tick(self, dt):
        self._flash = max(0.0, self._flash - dt)

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

    def on_click(self, pos, app):
        name = self.chip_at(pos, app.camera)
        if name is not None:
            self.pick(app, name)
        else:
            self.type_value(app)
        return True

    def state_rows(self, app):
        return [Row("Value", f"{self.value:.6g}", on_click=self.type_value),
                Row("Minimum", "none" if self.low is None else f"{self.low:g}",
                    on_click=lambda app: self.set_limit(app, "low")),
                Row("Maximum", "none" if self.high is None else f"{self.high:g}",
                    on_click=lambda app: self.set_limit(app, "high"))]

    def actions(self, app):
        return [Btn(f"+ {self.step:g}", self.up, style="go"), Btn(f"- {self.step:g}", self.down),
                Btn("Reset", self.reset)]

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

    def input_point(self, port):
        for n, x, y, _w, h in self._chip_boxes():
            if n == port:
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

    def take_at(self, pos, camera):
        return self.chip_at(pos, camera)

    # --- Drawing ----------------------------------------------------------------------
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
            linked = self.take_source(name) is not None
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


# ---- Timer: counts down, then says it's done ------------------------------------------
TM_W = 260                      # Timer layout, in world units at Size 1 (Counter's chips / title)
TM_INPUTS = ("start", "reset")
TM_DONE_COLOR = (110, 220, 130)
TM_IDLE_COLOR = (150, 152, 166)
TM_FLASH = 0.6                  # seconds the screen flares when it finishes
TM_OUTPUTS = ("remaining", "done")   # a screen each, top to bottom, each with its own dot
TM_SCREEN_H = 50                # each output's screen
TM_SCREEN_GAP = 8
TM_RETRIGGER = ("restarts", "is ignored")
TM_UNITS = {"ms": 0.001, "s": 1, "sec": 1, "secs": 1, "second": 1, "seconds": 1,
            "m": 60, "min": 60, "mins": 60, "minute": 60, "minutes": 60,
            "h": 3600, "hr": 3600, "hrs": 3600, "hour": 3600, "hours": 3600}
TM_PART = re.compile(r"(\d+(?:\.\d*)?|\.\d+)\s*([a-z]*)")


def parse_duration(text):
    """Typed text -> seconds: '90', '90 s', '1:30', '1:02:03', '5m', '1.5 min',
    '1h 30m', '250 ms'. Raises ValueError."""
    text = text.strip().lower()
    if ":" in text:
        parts = [float(p) for p in text.split(":")]
        if len(parts) > 3 or any(not math.isfinite(p) or p < 0 for p in parts):
            raise ValueError("expected m:ss or h:mm:ss")
        seconds = 0.0
        for p in parts:
            seconds = seconds * 60 + p
        return seconds
    found = TM_PART.findall(text)
    if not found or TM_PART.sub("", text).strip() or any(u and u not in TM_UNITS for _n, u in found):
        raise ValueError("expected e.g. 90, 1:30, 5m or 1h 30m")
    return sum(float(n) * TM_UNITS[u or "s"] for n, u in found)


def format_duration(seconds, tenths=False):
    """Seconds -> '45 s', '1:30', '1:02:03' (a setting); tenths=True (a countdown):
    '45.3', '1:30.0'. Rounds down to tenths, so a countdown reads 0.0 only at the end."""
    seconds = max(0.0, math.floor(seconds * 10 + 1e-6) / 10)
    if seconds < 60:
        return f"{seconds:.1f}" if tenths else f"{seconds:g} s"
    h, rest = divmod(seconds, 3600)
    m, s = divmod(rest, 60)
    s = f"{s:04.1f}" if tenths or s != int(s) else f"{int(s):02d}"
    return f"{int(h)}:{int(m):02d}:{s}" if h else f"{int(m)}:{s}"


class DurationProp(Prop):
    """A float Prop in seconds that's typed and shown as a duration (1:30, 5m)."""

    def parse(self, text):
        value = parse_duration(text)
        return max(self.min, value) if self.min is not None else value

    def format(self, value, unit=True):
        return format_duration(value)


class Timer(Thing):
    """Counts down its Duration, then says it's done. A press on its `start` chip
    (a push button, another timer's `done`, … — a 0 → 1 change counts once)
    starts it; `reset` stops it and clears done. Two outputs, each with its own
    screen and dot: `remaining` (seconds left; the Duration while idle) and
    `done` (1 from the end until the next start / reset, so whatever it's
    linked to fires once). Click a chip, then the source; click the timer to
    start it (or reset it while it runs). Ctrl+click selects it.

    A run keeps the world-clock moment it started (`_started`, drawn with
    world_time() on every PC, so nothing changes per frame for the world to
    resend) and the wall-clock moment it ends (`ends_at`, saved), so it keeps
    going across a restart — and one that ran out while the world was closed
    loads as done, without firing anything (already on when first read)."""

    TYPE = "timer"
    MENU_LABEL = "Add timer"
    NAME = "Timer"
    CATEGORY = "Math"
    DOT_COLOR = (255, 190, 90)
    PROPS = [Prop("scale", "Size", "float", 1.0, min=0.25, max=50, unit="×",
                  prompt="Size (1 = normal, 2 = twice as big):"),
             Prop("color", "Color", "color", (255, 190, 90)),
             DurationProp("duration", "Duration", "float", 10.0, min=0.0,
                          prompt="Duration (e.g. 90, 1:30, 5m, 1h 30m):"),
             Prop("retrigger", "Start while running", "choice", TM_RETRIGGER[0], choices=TM_RETRIGGER),
             Prop("ends_at", kind="data", default=None, hidden=True),   # time.time() it ends at, while running
             Prop("run_s", kind="data", default=None, hidden=True),     # the duration this run started with
             Prop("done", kind="bool", default=False, hidden=True)]
    TAKES = tuple(Take(name, kind="press") for name in TM_INPUTS)

    @classmethod
    def create(cls, app):
        def make(x, y):
            used = {o.text for o in app.scene.objects if isinstance(o, Timer)}
            return cls(x, y, next(f"Timer {i}" for i in range(1, 1000) if f"Timer {i}" not in used))
        app.start_placing(make)

    @classmethod
    def icon_sample(cls):
        sample = cls(0, 0)
        sample.duration = 90.0
        return sample

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._started = None        # world_time() the run started (None: not running, or not yet known)
        self._done_at = 0.0         # world_time() it finished: the flare

    @property
    def title(self):
        return self.text or "Timer"

    # --- Running ----------------------------------------------------------------------
    def running(self):
        return self.ends_at is not None

    def elapsed(self):
        if not self.running():
            return (self.run_s or 0.0) if self.done else 0.0
        if self._started is None:               # loaded mid-run, not ticked yet
            return max(0.0, self.run_s - (self.ends_at - time.time()))
        return min(self.run_s, max(0.0, world_time() - self._started))

    def remaining(self):
        if self.running():
            return max(0.0, self.run_s - self.elapsed())
        return 0.0 if self.done else self.duration

    def progress(self):
        if self.running():
            return min(1.0, self.elapsed() / self.run_s) if self.run_s > 0 else 1.0
        return 1.0 if self.done else 0.0

    def start(self, app=None):
        if self.running() and self.retrigger == "is ignored":
            return
        self.run_s = float(self.duration)
        self._started = world_time()
        self.ends_at = time.time() + self.run_s
        self.done = False
        self._save(app)

    def reset(self, app=None):
        self.ends_at = self.run_s = self._started = None
        self.done = False
        self._save(app)

    def finish(self):
        self.ends_at = self._started = None
        self.done = True
        self._done_at = world_time()
        self._save(None)

    def on_press(self, port):
        getattr(self, port)()          # start / reset, once per 0 → 1

    def tick(self, dt):
        if not self.running():
            return
        if self.run_s is None:                  # a hand-edited save: run the current duration
            self.run_s = float(self.duration)
        if self._started is None:               # loaded mid-run: pick up where the wall clock says
            self._started = world_time() - max(0.0, self.run_s - (self.ends_at - time.time()))
        if world_time() - self._started >= self.run_s:
            self.finish()

    def gives(self):
        return [Give("remaining", unit="s"), Give("done")]

    def outputs(self):
        return {"remaining": self.remaining(), "done": 1.0 if self.done else 0.0}

    # --- Panel and clicks ---------------------------------------------------------------
    def on_click(self, pos, app):
        name = self.chip_at(pos, app.camera)
        if name is not None:
            self.pick(app, name)
        elif self.running():
            self.reset(app)
        else:
            self.start(app)
        return True

    def panel_status(self, app):
        if self.running():
            return f"running — {format_duration(self.remaining(), True)} left", self.color
        return ("done", TM_DONE_COLOR) if self.done else ("ready", TM_IDLE_COLOR)

    def state_rows(self, app):
        return [Row("Remaining", format_duration(self.remaining(), True)),
                Row("Elapsed", format_duration(self.elapsed(), True))]

    def actions(self, app):
        ignored = self.running() and self.retrigger == "is ignored"
        return [Btn("Restart" if self.running() else "Start", self.start, style="go", enabled=not ignored),
                Btn("Reset", self.reset)]

    # --- Layout (world units at Size 1 from the top-left; shared by drawing, clicks, wires) --
    @property
    def world_width(self):
        return TM_W * self.scale

    @property
    def world_height(self):
        n = len(TM_OUTPUTS)
        return (FN_PAD + CT_TITLE + n * TM_SCREEN_H + (n - 1) * TM_SCREEN_GAP + FN_PAD) * self.scale

    def _to_world(self, x, y):
        c = self.world_center
        return (c.x - self.world_width / 2 + x * self.scale, c.y - self.world_height / 2 + y * self.scale)

    def _chip_boxes(self):
        top = FN_PAD + CT_TITLE
        return [(name, FN_PAD, top + i * (FN_CHIP_H + FN_CHIP_GAP), CT_CHIP_W, FN_CHIP_H)
                for i, name in enumerate(TM_INPUTS)]

    def _screen_boxes(self):
        """[(output, x, y, w, h)]: a screen per output, beside the chips."""
        x = FN_PAD + CT_CHIP_W + 10
        top = FN_PAD + CT_TITLE
        return [(key, x, top + i * (TM_SCREEN_H + TM_SCREEN_GAP), TM_W - FN_PAD - x, TM_SCREEN_H)
                for i, key in enumerate(TM_OUTPUTS)]

    def input_point(self, port):
        for n, x, y, _w, h in self._chip_boxes():
            if n == port:
                return self._to_world(x, y + h / 2)
        return self.center

    def output_point_of(self, key):
        """Each output's own dot, on the right edge level with its screen."""
        for k, _x, y, _w, h in self._screen_boxes():
            if k == key:
                return self._to_world(TM_W, y + h / 2)
        return self.center

    def output_point(self):
        return self.output_point_of(TM_OUTPUTS[0])

    def give_at(self, pos, camera):
        for key in TM_OUTPUTS:
            dot = pygame.Vector2(camera.world_to_screen(self.output_point_of(key)))
            if dot.distance_to(pos) <= max(PORT_REACH, 8 * self.scale * camera.zoom):
                return key
        return None

    def _chip_rects(self, rect):
        k = rect.width / TM_W
        return [(name, pygame.Rect(rect.x + round(x * k), rect.y + round(y * k),
                                   max(1, round(w * k)), max(1, round(h * k))))
                for name, x, y, w, h in self._chip_boxes()], k

    def chip_at(self, pos, camera):
        chips, _k = self._chip_rects(self.screen_rect(camera))
        return next((name for name, r in chips if r.inflate(6, 6).collidepoint(pos)), None)

    def take_at(self, pos, camera):
        return self.chip_at(pos, camera)

    # --- Drawing ----------------------------------------------------------------------
    def draw_shape(self, surface, rect, camera):
        s = min(rect.width, rect.height)
        radius = max(2, s // 8)
        draw_rect(surface, (14, 14, 18), rect.move(0, max(1, s // 22)), border_radius=radius)
        draw_rect(surface, INPUT_HOUSING, rect, border_radius=radius)
        draw_rect(surface, shade(INPUT_HOUSING, 1.35), rect, width=max(1, s // 40), border_radius=radius)
        running = self.running()
        state_color = self.color if running else TM_DONE_COLOR if self.done else TM_IDLE_COLOR
        if s < 16:
            draw_rect(surface, shade(state_color, 0.5), rect.inflate(-max(1, s // 3), -max(1, s // 3)))
            return
        chips, k = self._chip_rects(rect)

        # The name, and the duration beside it.
        size = round(16 * k)
        if size >= 7:
            font = get_font(size, "bahnschrift")
            title = font.render(clip_text(self.title, font, rect.width * 0.55), True, (226, 228, 236))
            surface.blit(title, (rect.x + round(FN_PAD * k), rect.y + round(FN_PAD * k)))
            info = get_font(max(7, round(13 * k)), "consolas", True).render(
                format_duration(self.duration), True, shade(self.color, 0.85))
            room = rect.right - round(FN_PAD * k) - (rect.x + round(FN_PAD * k) + title.get_width() + round(8 * k))
            if info.get_width() <= room:
                surface.blit(info, info.get_rect(midright=(rect.right - round(FN_PAD * k),
                                                           rect.y + round(FN_PAD * k) + title.get_height() // 2)))

        # Input chips: hollow until linked, filled once linked, brighter under the mouse.
        mouse = pygame.mouse.get_pos() if self.is_hovered else None
        csize = round(FN_CHIP_TEXT * k)
        for name, r in chips:
            linked = self.take_source(name) is not None
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

        # Two screens, each with its own dot: the time left (the duration while idle) over a
        # progress bar, and done — YES (flaring as it finishes) or NO.
        flare = max(0.0, 1 - (world_time() - self._done_at) / TM_FLASH) if self.done else 0.0
        corner = max(2, round(6 * k))
        pad = round(8 * k)
        lsize = round(12 * k)
        for key, x, y, w, h in self._screen_boxes():
            screen = pygame.Rect(rect.x + round(x * k), rect.y + round(y * k), round(w * k), round(h * k))
            if key == "remaining":
                color = state_color
                glow = 0.07
            else:
                color = TM_DONE_COLOR if self.done else TM_IDLE_COLOR
                glow = 0.07 + 0.25 * flare
            draw_rect(surface, shade(color, glow), screen, border_radius=corner)
            draw_rect(surface, (8, 8, 10), screen, width=max(1, s // 45), border_radius=corner)
            lit = (running or self.done) if key == "remaining" else self.done
            pygame.draw.circle(surface, color if lit else shade(color, 0.55),
                               (rect.right, screen.centery), max(2, round(5 * k)))
            if lsize >= 7:
                ls = get_font(lsize, "consolas", True).render(key, True, shade(color, 0.75))
                surface.blit(ls, (screen.x + pad, screen.y + max(1, pad // 2)))
            bottom = screen.bottom - max(1, pad // 2)
            if key == "remaining":
                bar_h = max(2, round(4 * k))
                bar = pygame.Rect(screen.x + pad, screen.bottom - pad - bar_h, screen.width - 2 * pad, bar_h)
                if running or self.done:
                    draw_rect(surface, shade(color, 0.25), bar, border_radius=bar_h // 2)
                    filled = bar.copy()
                    filled.width = round(bar.width * self.progress())
                    if filled.width > 0:
                        draw_rect(surface, color, filled, border_radius=bar_h // 2)
                bottom = bar.top - max(1, pad // 3)
                if running:
                    text, ink = format_duration(self.remaining(), True), shade(self.color, 1.2)
                elif self.done:
                    text, ink = format_duration(0, True), shade(TM_DONE_COLOR, 1.1)
                else:
                    text, ink = format_duration(self.duration, True), shade(TM_IDLE_COLOR, 0.9)
            else:
                text = "YES" if self.done else "NO"
                ink = shade(TM_DONE_COLOR, 1.1 + 0.4 * flare) if self.done else shade(TM_IDLE_COLOR, 0.75)
            size = round(h * 0.5 * k)
            while size > 7 and get_font(size, "consolas", True).size(text)[0] > screen.width - 2 * pad - round(40 * k):
                size = max(7, int(size * 0.9))
            if size >= 7:
                ts = get_font(size, "consolas", True).render(text, True, ink)
                surface.blit(ts, ts.get_rect(bottomright=(screen.right - pad, bottom)))


# ---- Time series: one input, sampled every few frames, scrolling by ------------------
TS_MAX_SAMPLES = 20000          # kept at most (a long window at every frame): the oldest go first
TS_PUBLISH_S = 0.1              # how often the samples are packed for the players' windows
TS_COLOR = (120, 200, 255)


class _Samples:
    """A TimeSeries' own (not sent to players as it is, not saved): the world's
    samples and counters, and a window's decoded copy of what the world sent."""

    def __init__(self):
        self.t = np.zeros(0)         # world_time() of each sample
        self.v = np.zeros(0)         # its value (NaN: the input had none)
        self.frames = 0              # frames since the last sample
        self.seq = 0                 # bumps on every change (the curve's cache key)
        self.published = (-1, 0.0)   # (seq, monotonic time) last packed into `trace`
        self.decoded = (None, np.zeros(0), np.zeros(0))
        self.curve = plotting.CurveImage()


class TimeSeries(YScale, Thing):
    """A scrolling plot of one value over time: link its input (a function's
    output, a device reading, …) and it samples it every *Sample every* frames
    (1 = every frame), plotting the samples against time over the last
    *Window* (10 s by default) — the newest at the right edge, older ones
    sliding off to the left. Auto-scale y (or a fixed Y from / Y to) and
    lines / points are Settings, as on the Graph; Clear starts over. A value
    the input doesn't have (None) is a gap in the line.

    The samples live in the world (not saved with the canvas): times are the
    world's clock, so every window scrolls together; they're packed for the
    players' windows TS_PUBLISH_S apart."""

    TYPE = "time_series"
    MENU_LABEL = "Add time series"
    NAME = "Time series"
    CATEGORY = "Displays"
    TAKES = (Take("in", "Samples", verb="samples"),)
    SIZE = (440, 250)
    DOT_COLOR = TS_COLOR
    PROPS = [Prop("scale", "Size", "float", 1.0, min=0.25, max=50, unit="×",
                  prompt="Size (1 = normal, 2 = twice as big):"),
             Prop("every", "Sample every", "int", 1, min=1, max=100000, unit=" frames",
                  prompt="Sample the input every how many frames (1 = every frame):"),
             DurationProp("window", "Window", "float", 10.0, min=0.1,
                          prompt="How much time the graph shows (e.g. 10, 30 s, 5m, 1:30):"),
             Prop("y_label", "Y label", "str", "", prompt="The y axis's name (e.g. power (mW)); blank for none:"),
             Prop("x_label", "X label", "str", "", prompt="The x axis's name (e.g. time (s)); blank for none:"),
             Prop("style", "Draw", "choice", "lines", choices=["lines", "points", "both"])]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.trace = None           # sent to players: {"t0", "t", "v", "n", "seq"} — t / v packed, t from t0
        self._s = _Samples()

    @classmethod
    def create(cls, app):
        """New ones are named Time series 1, 2, … (shown on top; click the name in the panel to change it)."""
        def make(x, y):
            used = {o.text for o in app.scene.objects if isinstance(o, TimeSeries)}
            return cls(x, y, next(f"Time series {i}" for i in range(1, 1000) if f"Time series {i}" not in used))
        app.start_placing(make)

    @classmethod
    def icon_sample(cls):
        sample = cls(0, 0)
        now = world_time()
        t = np.linspace(now - sample.window, now, 60)
        sample._s.t, sample._s.v = t, np.sin(np.linspace(0, 9, 60)) * np.exp(-np.linspace(0, 1.5, 60))
        return sample

    # --- Sampling (the world) ------------------------------------------------------------
    def tick(self, dt):
        s = self._s
        s.frames += 1
        if s.frames >= max(1, int(self.every)):
            s.frames = 0
            value = self.take("in")
            self._add(world_time(), math.nan if value is None else float(value))
        now = time.monotonic()
        if s.published[0] != s.seq and now - s.published[1] >= TS_PUBLISH_S:
            self._publish(now)

    def _add(self, t, v):
        """One sample; the ones that have slid out of the window go."""
        s = self._s
        keep = s.t > t - self.window
        s.t = np.append(s.t[keep], t)[-TS_MAX_SAMPLES:]
        s.v = np.append(s.v[keep], v)[-TS_MAX_SAMPLES:]
        s.seq += 1

    def _publish(self, now):
        s = self._s
        t0 = float(s.t[0]) if len(s.t) else 0.0
        self.trace = {"t0": t0, "t": plotting.pack_floats(s.t - t0), "v": plotting.pack_floats(s.v),
                      "n": len(s.t), "seq": s.seq}
        s.published = (s.seq, now)

    @panel_button("Clear")
    def clear(self, app):
        self._s.t, self._s.v = np.zeros(0), np.zeros(0)
        self._s.seq += 1
        self._publish(time.monotonic())

    def on_links_changed(self):
        self._s.frames = 0

    # --- What's shown (the world's own samples, or what it sent) -------------------------
    def samples(self):
        """(seq, times, values): the world's samples, or — in a player's window — the
        ones it packed."""
        s, trace = self._s, self.trace
        if len(s.t) or trace is None or s.published[0] >= 0:     # the world (it packs its own)
            return s.seq, s.t, s.v
        if s.decoded[0] != trace["seq"]:
            s.decoded = (trace["seq"], plotting.unpack_floats(trace["t"]) + trace["t0"],
                         plotting.unpack_floats(trace["v"]))
        return s.decoded

    def _visible(self, now):
        seq, t, v = self.samples()
        keep = t > now - self.window
        return seq, t[keep] - now, v[keep]

    def auto_y_range(self):
        _seq, _t, v = self._visible(world_time())
        return plotting.data_range(v)

    def on_prop_changed(self, key):
        super().on_prop_changed(key)
        self.y_scale_changed(key)

    def state_rows(self, app):
        _seq, t, v = self._visible(world_time())
        last = v[-1] if len(v) else None
        # The rate from the samples' own time span (steady as old ones slide off), whole numbers.
        rate = f"{(len(t) - 1) / (t[-1] - t[0]):.0f} / s" if len(t) > 1 and t[-1] > t[0] else "—"
        return [Row("Latest", format_value(None if last is None or not math.isfinite(last) else float(last))),
                Row("Shown", f"{len(t)} samples"),
                Row("Rate", rate)]

    # --- The input's dot: on the left edge, level with the plot -----------------------------
    def input_point(self, port):
        c = self.world_center
        return (c.x - self.world_width / 2, c.y + self.world_height * 0.05)

    def take_at(self, pos, camera):
        dot = pygame.Vector2(camera.world_to_screen(self.input_point("in")))
        return "in" if dot.distance_to(pos) <= max(PORT_REACH, 8 * self.scale * camera.zoom) else None

    def on_click(self, pos, app):
        """A click on the input dot picks up its wire (anywhere else selects)."""
        if self.take_at(pos, app.camera):
            self.pick(app, "in")
            return True
        return False

    # --- Drawing ------------------------------------------------------------------------
    def draw_shape(self, surface, rect, camera):
        s = min(rect.width, rect.height)
        radius = max(2, s // 14)
        draw_rect(surface, (12, 12, 16), rect.move(0, max(1, s // 40)), border_radius=radius)
        draw_rect(surface, GRAPH_BODY, rect, border_radius=radius)
        draw_rect(surface, shade(GRAPH_BODY, 1.5), rect, width=max(1, s // 90), border_radius=radius)
        if s < 24:
            return
        draw_input_dot(surface, camera.world_to_screen(self.input_point("in")), self.take_source("in") is not None, s)
        pad = max(2, round(s * 0.045))
        header_h = round(s * 0.11)
        now = world_time()
        seq, t, v = self._visible(now)
        size = round(header_h * 0.8)
        if size >= 8:
            font = get_font(size, "bahnschrift")
            ts = font.render(clip_text(self.text or "Time series", font, rect.width // 2), True, (200, 204, 218))
            surface.blit(ts, (rect.x + pad, rect.y + pad + (header_h - ts.get_height()) // 2))
            last = float(v[-1]) if len(v) and math.isfinite(v[-1]) else None
            right = format_value(last) if last is not None else ("no input" if not self.links else "—")
            small = get_font(max(8, round(size * 0.8)), "bahnschrift")
            rs = small.render(clip_text(right, small, rect.width - ts.get_width() - 4 * pad), True,
                              TS_COLOR if last is not None else (110, 116, 130))
            surface.blit(rs, rs.get_rect(midright=(rect.right - pad, rect.y + pad + header_h // 2)))
        screen = pygame.Rect(rect.x + pad, rect.y + pad + header_h, rect.width - 2 * pad,
                             rect.height - 2 * pad - header_h)
        draw_rect(surface, GRAPH_SCREEN, screen, border_radius=max(2, radius - 3))
        prev_clip = surface.get_clip()
        surface.set_clip(screen.clip(prev_clip))
        try:
            self._draw_plot(surface, screen, seq, t, v)
        finally:
            surface.set_clip(prev_clip)

    def _draw_plot(self, surface, screen, seq, t, v):
        inner = screen.inflate(-max(4, screen.width // 30), -max(4, screen.height // 22))
        font_size = round(inner.height * 0.07)
        labels = font_size >= 8
        font = get_font(font_size, "bahnschrift") if labels else None
        # Margins: the axis names (when set) in their own column / row, outside the tick labels.
        y_name = self.y_label.strip() if labels else ""
        x_name = self.x_label.strip() if labels else ""
        name_w = round(font_size * 1.4) if y_name else 0
        left = name_w + font.size("-0.0000")[0] + font_size if labels else 0
        bottom = round(font_size * 1.8) + (round(font_size * 1.3) if x_name else 0) if labels else 0
        top = round(font_size * 0.6) if labels else 0
        plot = pygame.Rect(inner.x + left, inner.y + top, inner.width - left - (font_size if labels else 0),
                           inner.height - bottom - top)
        if plot.width < plotting.MIN_PLOT_PX or plot.height < 16:
            return
        x_range = (-float(self.window), 0.0)
        y_range = self.y_range(plotting.data_range(v))
        if y_range is None:
            if labels:
                text = "link an input to sample it" if not self.links else "waiting for values…"
                ts = font.render(clip_text(text, font, plot.width), True, (90, 96, 110))
                surface.blit(ts, ts.get_rect(center=plot.center))
            y_range = (0.0, 1.0)
        (xlo, xhi), (ylo, yhi) = x_range, y_range
        to_x = lambda val: plot.x + (val - xlo) / (xhi - xlo) * (plot.width - 1)      # noqa: E731
        to_y = lambda val: plot.y + (yhi - val) / (yhi - ylo) * (plot.height - 1)     # noqa: E731
        x_ticks = plotting.linear_ticks(xlo, xhi, max(2, plot.width // 70))
        y_ticks = plotting.linear_ticks(ylo, yhi, max(3, plot.height // 25))
        name_color = (110, 116, 130)
        if y_name:          # sideways, centered on the plot, just left of the widest tick label (the plot stays put)
            widest = max((font.size(plotting.format_tick(val))[0] for _p, val in y_ticks), default=0)
            ts = pygame.transform.rotate(font.render(clip_text(y_name, font, plot.height), True, name_color), 90)
            x = max(inner.x + name_w // 2, plot.x - font_size // 2 - widest - round(font_size * 0.4) - name_w // 2)
            surface.blit(ts, ts.get_rect(center=(x, plot.centery)))
        if x_name:                                   # under the time ticks, centered on the plot
            ts = font.render(clip_text(x_name, font, plot.width), True, name_color)
            surface.blit(ts, ts.get_rect(midbottom=(plot.centerx, inner.bottom)))
        pygame.draw.rect(surface, (40, 46, 60), plot, 1)
        for pos, _v in x_ticks:
            x = round(to_x(pos))
            if plot.left < x < plot.right - 1:
                pygame.draw.line(surface, GRAPH_GRID, (x, plot.y + 1), (x, plot.bottom - 2))
        for pos, _v in y_ticks:
            y = round(to_y(pos))
            if plot.top < y < plot.bottom - 1:
                pygame.draw.line(surface, GRAPH_GRID, (plot.x + 1, y), (plot.right - 2, y))
        vis = plot.clip(surface.get_clip())
        if len(t) and vis.width > 0 and vis.height > 0:
            # The key holds the newest sample's time, so the image follows the scroll.
            key = (seq, float(t[-1]) if len(t) else 0.0, self.style)
            surf = self._s.curve.get(key, plot, vis, t, v, x_range, y_range, color=TS_COLOR, style=self.style,
                                   sorted_x=True)
            surface.blit(surf, vis.topleft)
        if not labels:
            return
        for pos, value in x_ticks:
            x = round(to_x(pos))
            pygame.draw.line(surface, (90, 96, 110), (x, plot.bottom), (x, plot.bottom + font_size // 3))
            ts = font.render(f"{plotting.format_tick(value)} s" if value == xlo and not x_name
                             else plotting.format_tick(value),
                             True, GRAPH_INK)
            r = ts.get_rect(midtop=(x, plot.bottom + font_size // 3))
            r.clamp_ip(pygame.Rect(inner.x, r.y, inner.width, r.height))
            surface.blit(ts, r)
        for pos, value in y_ticks:
            y = round(to_y(pos))
            pygame.draw.line(surface, (90, 96, 110), (plot.x - font_size // 3, y), (plot.x, y))
            ts = font.render(plotting.format_tick(value), True, GRAPH_INK)
            r = ts.get_rect(midright=(plot.x - font_size // 2, y))
            r.clamp_ip(pygame.Rect(r.x, inner.y, r.width, inner.height))
            surface.blit(ts, r)


# ---- Random: a random number, drawn on a press, every frame, or on a timer -------------
RN_W = 250                      # Random layout, in world units at Size 1 (Counter-style)
RN_COLOR = (120, 220, 200)
RN_RANGE = {"uniform": ("min", "max"), "integer": ("min", "max"), "normal": ("mean", "spread")}
RN_DRAWS = ("on press", "every frame", "on a timer")
RN_FLASH = 0.25                 # seconds the number stays bright after a draw
RN_SETTING = {"min": "low", "max": "high", "mean": "mean", "spread": "spread"}   # range input -> its setting


class _Dice:
    """A Random's generator and timer (not plain: not sent to players, not saved)."""

    def __init__(self, seed):
        self.seed = seed
        self.rng = random.Random(seed if seed != "" else None)
        self.waited = 0.0            # seconds since the last timed draw


class Random(Thing):
    """A random number, as its `value` output. **Distribution**: uniform (any
    number from min to max), integer (whole numbers min…max, both included —
    1…6 is a die) or normal (a bell curve: mean ± spread, the standard
    deviation). **Draws**: on press — each press on its `next` chip (only
    there in this mode), a click on it, or the panel's Draw rolls a new one;
    every frame — fresh noise each frame (feed a Time series, or add it to a
    signal in a Function); on a timer — every *Every* seconds. The panel's
    Reset starts the sequence over and draws its first number: with a
    **Seed** set, the same numbers come in the same order every time (for
    testing a script twice on identical data); blank, it's truly random. The range — min / max, or mean / spread — are inputs too:
    set each in the panel's input rows, or link one (a Function into mean
    makes a noisy signal). Ctrl+click selects it."""

    TYPE = "random"
    MENU_LABEL = "Add random number"
    NAME = "Random"
    CATEGORY = "Math"
    DOT_COLOR = RN_COLOR
    PROPS = [Prop("scale", "Size", "float", 1.0, min=0.25, max=50, unit="×",
                  prompt="Size (1 = normal, 2 = twice as big):"),
             Prop("color", "Color", "color", RN_COLOR),
             Prop("distribution", "Distribution", "choice", "uniform", choices=list(RN_RANGE)),
             Prop("draws", "Draws", "choice", "on press", choices=list(RN_DRAWS)),
             DurationProp("every_s", "Every", "float", 1.0, min=0.01,
                          prompt="Draw a new number every (e.g. 0.5, 2 s, 1m) — when Draws is on a timer:"),
             Prop("seed", "Seed", "str", "", prompt="Seed (blank = truly random; set one to repeat the sequence):"),
             Prop("value", kind="data", default=None, hidden=True),
             Prop("low", kind="float", default=0.0, hidden=True),        # the range, when its input isn't linked
             Prop("high", kind="float", default=1.0, hidden=True),
             Prop("mean", kind="float", default=0.0, hidden=True),
             Prop("spread", kind="float", default=1.0, hidden=True)]

    @classmethod
    def create(cls, app):
        def make(x, y):
            used = {o.text for o in app.scene.objects if isinstance(o, Random)}
            return cls(x, y, next(f"Random {i}" for i in range(1, 1000) if f"Random {i}" not in used))
        app.start_placing(make)

    @classmethod
    def icon_sample(cls):
        sample = cls(0, 0)
        sample.value = 0.42
        return sample

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._flash = 0.0           # seconds the number stays bright after a draw
        self._dice = None           # the generator (made on first use, remade when the seed changes)

    @property
    def title(self):
        return self.text or "Random"

    # --- The range: inputs that fall back on their own setting -----------------------------
    def range_keys(self):
        return RN_RANGE.get(self.distribution, RN_RANGE["uniform"])

    def bound(self, key):
        """A range input's value: what it's linked to (while that has one), else its setting."""
        if self.take_source(key) is not None:
            value = self.take(key)
            if value is not None:
                return float(value)
        return float(getattr(self, RN_SETTING[key]))

    def set_bound(self, app, key):
        def submit(text):
            try:
                setattr(self, RN_SETTING[key], float(text))
            except ValueError:
                return
            app.save()
        app.ask(f"{key.capitalize()}:", submit, value=f"{getattr(self, RN_SETTING[key]):g}")

    def takes(self):
        presses = [Take("next", kind="press")] if self.draws == "on press" else []
        bounds = [Take(key, current=lambda key=key: getattr(self, RN_SETTING[key]),
                       set_by_hand=lambda app, key=key: self.set_bound(app, key))
                  for key in self.range_keys()]
        return presses + bounds

    # --- Drawing a number ---------------------------------------------------------------
    def dice(self):
        if self._dice is None or self._dice.seed != self.seed.strip():
            self._dice = _Dice(self.seed.strip())
        return self._dice

    def roll(self):
        """One number from the distribution (the range as it is right now)."""
        rng = self.dice().rng
        if self.distribution == "normal":
            return rng.gauss(self.bound("mean"), abs(self.bound("spread")))
        lo, hi = sorted((self.bound("min"), self.bound("max")))
        if self.distribution == "integer":
            first, last = math.ceil(lo), math.floor(hi)
            return float(rng.randint(first, last)) if first <= last else float(round(lo))
        return rng.uniform(lo, hi)

    def new_number(self, app=None):
        """Roll a new value."""
        self.value = self.roll()
        if self.draws != "every frame":
            self._flash = RN_FLASH

    def reset(self, app=None):
        """Back to the start of the sequence (the seed's first number, or a fresh random one)."""
        self._dice = None
        self.new_number()

    def on_press(self, port):
        self.new_number()                             # `next`, the only press input

    def on_prop_changed(self, key):
        super().on_prop_changed(key)
        if key in ("seed", "distribution"):
            self.reset()

    def tick(self, dt):
        self._flash = max(0.0, self._flash - dt)
        if self.value is None:
            self.new_number()                         # a new one starts with a number
        elif self.draws == "every frame":
            self.new_number()
        elif self.draws == "on a timer":
            dice, every = self.dice(), max(0.01, float(self.every_s))
            dice.waited += dt
            if dice.waited >= every - 1e-6:              # (frames of 1/60 s add up a hair short)
                dice.waited = min(dice.waited - every, every)    # keeps the pace; no burst after a stall
                self.new_number()

    def outputs(self):
        return {"value": self.value}

    # --- Panel and clicks -----------------------------------------------------------------
    def rename(self, app):
        def submit(text):
            if text.strip():
                self.text = text.strip()
                app.save()
        app.ask("Name:", submit, value=self.title)

    def on_click(self, pos, app):
        """A chip starts linking it; anywhere else rolls a new number."""
        name = self.chip_at(pos, app.camera)
        if name is not None:
            self.pick(app, name)
        else:
            self.new_number(app)
        return True

    def state_rows(self, app):
        return [Row("Value", format_value(self.value))]

    def actions(self, app):
        return [Btn("Draw", self.new_number, style="go"), Btn("Reset", self.reset)]

    def range_text(self):
        if self.distribution == "normal":
            return f"normal {self.bound('mean'):.4g} ± {abs(self.bound('spread')):.4g}"
        lo, hi = sorted((self.bound("min"), self.bound("max")))
        return f"{self.distribution} {lo:.4g}…{hi:.4g}"

    # --- Layout (world units at Size 1 from the top-left; shared by drawing, clicks, wires) --
    def _chip_names(self):
        return (["next"] if self.draws == "on press" else []) + list(self.range_keys())

    @property
    def world_width(self):
        return RN_W * self.scale

    @property
    def world_height(self):
        n = len(self._chip_names())
        return (FN_PAD + CT_TITLE + n * FN_CHIP_H + (n - 1) * FN_CHIP_GAP + FN_PAD) * self.scale

    def _to_world(self, x, y):
        c = self.world_center
        return (c.x - self.world_width / 2 + x * self.scale, c.y - self.world_height / 2 + y * self.scale)

    def _chip_boxes(self):
        top = FN_PAD + CT_TITLE
        return [(name, FN_PAD, top + i * (FN_CHIP_H + FN_CHIP_GAP), CT_CHIP_W, FN_CHIP_H)
                for i, name in enumerate(self._chip_names())]

    def _screen_box(self):
        x = FN_PAD + CT_CHIP_W + 10
        y = FN_PAD + CT_TITLE
        return x, y, RN_W - FN_PAD - x, self.world_height / self.scale - FN_PAD - y

    def input_point(self, port):
        for n, x, y, _w, h in self._chip_boxes():
            if n == port:
                return self._to_world(x, y + h / 2)
        return self.center

    def output_point(self):
        x, y, w, h = self._screen_box()
        return self._to_world(x + w + FN_PAD, y + h / 2)

    def _chip_rects(self, rect):
        k = rect.width / RN_W
        return [(name, pygame.Rect(rect.x + round(x * k), rect.y + round(y * k),
                                   max(1, round(w * k)), max(1, round(h * k))))
                for name, x, y, w, h in self._chip_boxes()], k

    def chip_at(self, pos, camera):
        chips, _k = self._chip_rects(self.screen_rect(camera))
        return next((name for name, r in chips if r.inflate(6, 6).collidepoint(pos)), None)

    def take_at(self, pos, camera):
        return self.chip_at(pos, camera)

    # --- Drawing ----------------------------------------------------------------------
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

        # The name, and the distribution / range beside it.
        size = round(16 * k)
        if size >= 7:
            font = get_font(size, "bahnschrift")
            title = font.render(clip_text(self.title, font, rect.width * 0.45), True, (226, 228, 236))
            surface.blit(title, (rect.x + round(FN_PAD * k), rect.y + round(FN_PAD * k)))
            info_font = get_font(max(7, round(13 * k)), "consolas", True)
            room = rect.right - round(FN_PAD * k) - (rect.x + round(FN_PAD * k) + title.get_width() + round(8 * k))
            info = info_font.render(clip_text(self.range_text(), info_font, room), True, shade(self.color, 0.85))
            if room > 0:
                surface.blit(info, info.get_rect(midright=(rect.right - round(FN_PAD * k),
                                                           rect.y + round(FN_PAD * k) + title.get_height() // 2)))

        # Input chips: hollow until linked, filled once linked, brighter under the mouse.
        mouse = pygame.mouse.get_pos() if self.is_hovered else None
        csize = round(FN_CHIP_TEXT * k)
        for name, r in chips:
            linked = self.take_source(name) is not None
            hot = mouse is not None and r.inflate(6, 6).collidepoint(mouse)
            color = shade(self.color, 1.2) if hot else self.color
            cr = max(2, r.height // 2)
            if linked:
                draw_rect(surface, color, r, border_radius=cr)
            else:
                draw_rect(surface, INPUT_WELL, r, border_radius=cr)
                draw_rect(surface, color, r, width=max(1, round(2 * k)), border_radius=cr)
            if csize >= 7:
                font = get_font(csize, "consolas", True)
                ts = font.render(clip_text(name, font, r.width - 6), True, INPUT_WELL if linked else color)
                surface.blit(ts, ts.get_rect(center=r.center))

        # The value, with the output dot on the right edge; it flares on each new draw.
        x, y, w, h = self._screen_box()
        screen = pygame.Rect(rect.x + round(x * k), rect.y + round(y * k), round(w * k), round(h * k))
        corner = max(2, round(6 * k))
        flare = self._flash / RN_FLASH
        draw_rect(surface, shade(self.color, 0.07 + 0.08 * flare), screen, border_radius=corner)
        draw_rect(surface, (8, 8, 10), screen, width=max(1, s // 45), border_radius=corner)
        pygame.draw.circle(surface, self.color if self.value is not None else shade(self.color, 0.4),
                           (rect.right, screen.centery), max(2, round(5 * k)))
        pad = round(8 * k)
        lsize = round(13 * k)
        if lsize >= 7:
            ls = get_font(lsize, "consolas", True).render(self.draws, True, shade(self.color, 0.6))
            surface.blit(ls, (screen.x + pad, screen.y + max(1, pad // 2)))
        if self.value is None:
            text = "—"
        elif self.distribution == "integer":
            text = f"{self.value:.0f}"
        else:
            text = f"{self.value:.6g}"
        color = shade(self.color, 1.2 + 0.4 * flare)
        size = round(h * 0.36 * k)
        while size > 7 and get_font(size, "consolas", True).size(text)[0] > screen.width - 2 * pad:
            size = max(7, int(size * 0.9))
        if size >= 7:
            ts = get_font(size, "consolas", True).render(text, True, color)
            surface.blit(ts, ts.get_rect(bottomright=(screen.right - pad, screen.bottom - pad)))


# ---- Sequence: a list of numbers, stepped through like a for loop's i --------------------
SQ_W = 330                      # Sequence layout, in world units at Size 1 (Counter's chips / title)
SQ_INPUTS = ("next", "reset")
SQ_CELL_W = 54                  # one number on the screen
SQ_AT_END = ("stops", "wraps around")
SQ_MAX_ITEMS = 10000


def parse_sequence(text):
    """Typed text -> a list of numbers: '450, 470, 490, 500' (commas, spaces or
    semicolons), and ranges 'start:stop:step' with the stop included
    ('450:500:10' = 450, 460 … 500; 'start:stop' steps by 1). Raises ValueError."""
    out = []
    for part in re.split(r"[,;\s]+", text.strip()):
        if not part:
            continue
        if ":" in part:
            bits = [float(b) for b in part.split(":")]
            if len(bits) not in (2, 3):
                raise ValueError("a range is start:stop or start:stop:step")
            start, stop = bits[0], bits[1]
            step = bits[2] if len(bits) == 3 else (1.0 if stop >= start else -1.0)
            if step == 0 or (stop - start) * step < 0 or not all(map(math.isfinite, bits)):
                raise ValueError("the step must go from start toward stop")
            n = int(math.floor((stop - start) / step + 1e-9)) + 1
            if len(out) + n > SQ_MAX_ITEMS:
                raise ValueError(f"at most {SQ_MAX_ITEMS} numbers")
            out += [round(start + i * step, 12) for i in range(n)]
        else:
            value = float(part)
            if not math.isfinite(value):
                raise ValueError("numbers must be finite")
            out.append(value)
    if len(out) > SQ_MAX_ITEMS:
        raise ValueError(f"at most {SQ_MAX_ITEMS} numbers")
    return out


class Sequence(Thing):
    """A list of numbers, stepped through like the i of a for loop. Its `value`
    is the selected number; each press on its `next` chip (a push button, a
    Script's `complete`, … — a 0 → 1 change counts once) moves to the next one,
    `reset` goes back to the first. At the end it stops (or wraps around).
    Its one output is `value`. Click a number to select it; anywhere else on
    the screen to type the list. Ctrl+click selects it."""

    TYPE = "sequence"
    MENU_LABEL = "Add sequence"
    NAME = "Sequence"
    CATEGORY = "Math"
    DOT_COLOR = (150, 200, 255)
    PROPS = [Prop("scale", "Size", "float", 1.0, min=0.25, max=50, unit="×",
                  prompt="Size (1 = normal, 2 = twice as big):"),
             Prop("color", "Color", "color", (150, 200, 255)),
             Prop("at_end", "At the end it", "choice", SQ_AT_END[0], choices=SQ_AT_END),
             Prop("items", kind="data", default=[], hidden=True),      # the numbers
             Prop("index", kind="int", default=0, hidden=True)]        # the selected one (from 0)
    TAKES = tuple(Take(name, kind="press") for name in SQ_INPUTS)

    @classmethod
    def create(cls, app):
        def make(x, y):
            used = {o.text for o in app.scene.objects if isinstance(o, Sequence)}
            seq = cls(x, y, next(f"Sequence {i}" for i in range(1, 1000) if f"Sequence {i}" not in used))
            seq.items = [450.0, 470.0, 490.0, 500.0]
            return seq
        app.start_placing(make)

    @classmethod
    def icon_sample(cls):
        sample = cls(0, 0)
        sample.items, sample.index = [1.0, 2.0, 3.0, 4.0], 1
        return sample

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._stepped_at = -1e9         # world_time() of the last next: the screen's flare

    @property
    def title(self):
        return self.text or "Sequence"

    # --- Stepping ---------------------------------------------------------------------
    def current(self):
        return self.items[self.index] if 0 <= self.index < len(self.items) else None

    def is_last(self):
        return bool(self.items) and self.index >= len(self.items) - 1

    def select(self, index, app=None):
        index = max(0, min(len(self.items) - 1, index)) if self.items else 0
        if index != self.index:
            self.index = index
            self._save(app)

    def next(self, app=None):
        if not self.items:
            return
        if self.is_last():
            if self.at_end != "wraps around":
                return                      # stays on the last: nothing to step to
            self.select(0, app)
        else:
            self.select(self.index + 1, app)
        self._stepped_at = world_time()

    def reset(self, app=None):
        self.select(0, app)

    def on_press(self, port):
        getattr(self, port)()          # next / reset, once per 0 → 1

    def gives(self):
        return [Give("value")]

    def outputs(self):
        return {"value": self.current()}

    # --- Editing, clicks and the panel ----------------------------------------------------
    def list_text(self):
        return ", ".join(f"{v:g}" for v in self.items)

    def type_list(self, app):
        def submit(text):
            try:
                items = parse_sequence(text)
            except ValueError:
                return
            self.items = items
            self.index = max(0, min(self.index, len(items) - 1))
            app.save()
        app.ask("Numbers (e.g. 450, 470, 490 — or a range, 450:500:10):", submit, value=self.list_text())

    def on_click(self, pos, app):
        name = self.chip_at(pos, app.camera)
        if name is not None:
            self.pick(app, name)
            return True
        rect = self.screen_rect(app.camera)
        cell = next((i for i, r in self._cell_rects(rect) if r.collidepoint(pos)), None)
        if cell is not None:
            self.select(cell, app)
        else:
            self.type_list(app)
        return True

    def panel_status(self, app):
        if not self.items:
            return "empty — type a list", WARN_COLOR
        return f"{self.index + 1} of {len(self.items)}" + (" (last)" if self.is_last() else ""), self.color

    def state_rows(self, app):
        shown = self.list_text()
        return [Row("Value", "—" if self.current() is None else f"{self.current():g}"),
                Field("List", (shown[:200] + "…") if len(shown) > 200 else shown or "(empty)",
                      on_click=self.type_list)]

    def actions(self, app):
        return [Btn("Next", self.next, style="go", enabled=bool(self.items) and
                    (not self.is_last() or self.at_end == "wraps around")),
                Btn("Reset", self.reset), Btn("Edit list…", self.type_list)]

    # --- Layout (world units at Size 1 from the top-left; shared by drawing, clicks, wires) --
    @property
    def world_width(self):
        return SQ_W * self.scale

    @property
    def world_height(self):
        return (FN_PAD + CT_TITLE + 2 * FN_CHIP_H + FN_CHIP_GAP + FN_PAD) * self.scale

    def _to_world(self, x, y):
        c = self.world_center
        return (c.x - self.world_width / 2 + x * self.scale, c.y - self.world_height / 2 + y * self.scale)

    def _chip_boxes(self):
        top = FN_PAD + CT_TITLE
        return [(name, FN_PAD, top + i * (FN_CHIP_H + FN_CHIP_GAP), CT_CHIP_W, FN_CHIP_H)
                for i, name in enumerate(SQ_INPUTS)]

    def _screen_box(self):
        x = FN_PAD + CT_CHIP_W + 10
        y = FN_PAD + CT_TITLE
        return x, y, SQ_W - FN_PAD - x, self.world_height / self.scale - FN_PAD - y

    def input_point(self, port):
        for n, x, y, _w, h in self._chip_boxes():
            if n == port:
                return self._to_world(x, y + h / 2)
        return self.center

    def output_point(self):
        x, y, w, h = self._screen_box()
        return self._to_world(x + w + FN_PAD, y + h / 2)

    def _chip_rects(self, rect):
        k = rect.width / SQ_W
        return [(name, pygame.Rect(rect.x + round(x * k), rect.y + round(y * k),
                                   max(1, round(w * k)), max(1, round(h * k))))
                for name, x, y, w, h in self._chip_boxes()], k

    def _screen_rect(self, rect):
        k = rect.width / SQ_W
        x, y, w, h = self._screen_box()
        return pygame.Rect(rect.x + round(x * k), rect.y + round(y * k), round(w * k), round(h * k)), k

    def _cell_rects(self, rect):
        """(item index, on-screen rect) of the numbers that fit on the screen: a
        window around the selected one."""
        screen, k = self._screen_rect(rect)
        pad = round(6 * k)
        cell_w = SQ_CELL_W * k
        fits = max(1, int((screen.width - 2 * pad) // max(1, cell_w)))
        n = len(self.items)
        first = max(0, min(self.index - fits // 2, n - fits))
        cells = []
        cell_h = round(screen.height * 0.62)
        y = screen.y + (screen.height - cell_h) // 2
        x0 = screen.x + pad + ((screen.width - 2 * pad) - min(fits, n) * cell_w) / 2
        for j, i in enumerate(range(first, min(n, first + fits))):
            cells.append((i, pygame.Rect(round(x0 + j * cell_w) + 1, y, max(1, round(cell_w) - 2), cell_h)))
        return cells

    def chip_at(self, pos, camera):
        chips, _k = self._chip_rects(self.screen_rect(camera))
        return next((name for name, r in chips if r.inflate(6, 6).collidepoint(pos)), None)

    def take_at(self, pos, camera):
        return self.chip_at(pos, camera)

    # --- Drawing ----------------------------------------------------------------------
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

        # The name, and "2 / 4" beside it.
        size = round(16 * k)
        if size >= 7:
            font = get_font(size, "bahnschrift")
            title = font.render(clip_text(self.title, font, rect.width * 0.6), True, (226, 228, 236))
            surface.blit(title, (rect.x + round(FN_PAD * k), rect.y + round(FN_PAD * k)))
            where = f"{self.index + 1} / {len(self.items)}" if self.items else "empty"
            if self.items and self.is_last():
                where = "last  " + where
            info = get_font(max(7, round(13 * k)), "consolas", True).render(where, True, shade(self.color, 0.85))
            room = rect.right - round(FN_PAD * k) - (rect.x + round(FN_PAD * k) + title.get_width() + round(8 * k))
            if info.get_width() <= room:
                surface.blit(info, info.get_rect(midright=(rect.right - round(FN_PAD * k),
                                                           rect.y + round(FN_PAD * k) + title.get_height() // 2)))

        # Input chips: hollow until linked, filled once linked, brighter under the mouse.
        mouse = pygame.mouse.get_pos() if self.is_hovered else None
        csize = round(FN_CHIP_TEXT * k)
        for name, r in chips:
            linked = self.take_source(name) is not None
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

        # The screen: the numbers, the selected one lit (the ones done dimmer), and the nub.
        screen, _k = self._screen_rect(rect)
        corner = max(2, round(6 * k))
        draw_rect(surface, shade(self.color, 0.07), screen, border_radius=corner)
        draw_rect(surface, (8, 8, 10), screen, width=max(1, s // 45), border_radius=corner)
        pygame.draw.circle(surface, self.color, (rect.right, screen.centery), max(2, round(5 * k)))
        cells = self._cell_rects(rect)
        if not cells:
            fs = round(14 * k)
            if fs >= 7:
                ts = get_font(fs, "consolas", True).render("click to type a list", True, shade(self.color, 0.6))
                surface.blit(ts, ts.get_rect(center=screen.center))
            return
        flare = max(0.0, 1 - (world_time() - self._stepped_at) / 0.4)
        for i, r in cells:
            text = f"{self.items[i]:g}"
            if i == self.index:
                draw_rect(surface, shade(self.color, 1.0 + 0.3 * flare), r.inflate(0, round(4 * k)),
                          border_radius=max(2, round(5 * k)))
                ink = INPUT_WELL
            else:
                draw_rect(surface, shade(self.color, 0.16), r, border_radius=max(2, round(5 * k)))
                ink = shade(self.color, 0.55 if i < self.index else 1.0)
            size = round(r.height * 0.5)
            while size > 7 and get_font(size, "consolas", True).size(text)[0] > r.width - round(4 * k):
                size = max(7, int(size * 0.9))
            if size >= 7:
                ts = get_font(size, "consolas", True).render(text, True, ink)
                surface.blit(ts, ts.get_rect(center=r.center))
        # More numbers off either end: a small "…" there.
        fs = max(7, round(12 * k))
        if cells[0][0] > 0:
            ts = get_font(fs, "consolas", True).render("…", True, shade(self.color, 0.7))
            surface.blit(ts, ts.get_rect(midleft=(screen.x + round(2 * k), screen.centery)))
        if cells[-1][0] < len(self.items) - 1:
            ts = get_font(fs, "consolas", True).render("…", True, shade(self.color, 0.7))
            surface.blit(ts, ts.get_rect(midright=(screen.right - round(2 * k), screen.centery)))


# ============================================================================
# LOGIC — gates that combine on/off values (E menu ▸ Logic)
# ============================================================================
GATE_PINS = ("a", "b")
GATE_PIN_Y = (0.33, 0.67)       # the input pins, as a fraction of the height
GATE_LEAD_IN, GATE_LEAD_OUT = 0.2, 0.16    # the pin leads, as a fraction of the width
GATE_IN_ON = (240, 200, 90)     # an input pin while on (the on/off wires' amber)
GATE_ON = (110, 220, 130)       # the output pin while on
GATE_OFF = (96, 100, 114)


class LogicGate(Thing):
    """Base of the logic gates: on/off inputs `a` and `b` (on while the source
    isn't 0 — a push button's pressed, a switch, a Timer's done, a Script's
    running …; unlinked or no value counts as off) and one output `out`, 1 or
    0, from rule(). Click a pin, then the source; drag from the output pin to
    lay a wire. Computed each frame in tick, so a gate wired into a loop just
    runs a frame behind. A gate type sets TYPE / NAME / LABEL and rule()."""

    CATEGORY = "Logic"
    SIZE = (150, 90)
    DOT_COLOR = (170, 180, 255)
    LABEL = "?"
    PROPS = [Prop("scale", "Size", "float", 1.0, min=0.25, max=50, unit="×",
                  prompt="Size (1 = normal, 2 = twice as big):"),
             Prop("color", "Color", "color", (170, 180, 255))]
    TAKES = tuple(Take(pin, kind="onoff") for pin in GATE_PINS)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.ins = [False] * len(GATE_PINS)     # each input, as last read (drawn on every PC)
        self.on = False                         # the output

    def rule(self, ins):
        raise NotImplementedError

    def tick(self, dt):
        ins = [bool(self.take_on(pin)) for pin in GATE_PINS]
        on = bool(self.rule(ins))
        if ins != self.ins:
            self.ins = ins
        if on != self.on:
            self.on = on

    def outputs(self):
        return {"out": 1.0 if self.on else 0.0}

    def panel_status(self, app):
        return ("on", GATE_ON) if self.on else ("off", GATE_OFF)

    # --- Pins (world points, and what's under the mouse) ------------------------------------
    def _pin_world(self, i):
        c = self.world_center
        return (c.x - self.world_width / 2, c.y - self.world_height / 2 + GATE_PIN_Y[i] * self.world_height)

    def input_point(self, port):
        return self._pin_world(GATE_PINS.index(port)) if port in GATE_PINS else self.center

    def output_point(self):
        c = self.world_center
        return (c.x + self.world_width / 2, c.y)

    def take_at(self, pos, camera):
        rect = self.screen_rect(camera)
        reach = max(8, rect.height * 0.18)
        for i, pin in enumerate(GATE_PINS):
            x, y = rect.x, rect.y + GATE_PIN_Y[i] * rect.height
            if abs(pos[0] - x) <= max(reach, rect.width * GATE_LEAD_IN) and abs(pos[1] - y) <= reach:
                return pin
        return None

    def on_click(self, pos, app):
        """A click on an input pin links it; anywhere else selects as usual."""
        pin = self.take_at(pos, app.camera)
        if pin is None:
            return False
        self.pick(app, pin)
        return True

    # --- Drawing ----------------------------------------------------------------------
    def body_points(self, body):
        """The outline of the gate's body inside rect `body` (the AND's D)."""
        r = body.height / 2
        flat = body.right - r
        arc = [(flat + r * math.cos(t), body.centery + r * math.sin(t))
               for t in np.linspace(-math.pi / 2, math.pi / 2, 24)]
        return [(body.x, body.y)] + arc + [(body.x, body.bottom)]

    def draw_shape(self, surface, rect, camera):
        s = min(rect.width, rect.height)
        lead_in, lead_out = round(rect.width * GATE_LEAD_IN), round(rect.width * GATE_LEAD_OUT)
        inset = round(rect.height * 0.08)
        body = pygame.Rect(rect.x + lead_in, rect.y + inset, rect.width - lead_in - lead_out, rect.height - 2 * inset)
        if s < 14:
            draw_rect(surface, GATE_ON if self.on else shade(self.color, 0.5), rect.inflate(-s // 3, -s // 3))
            return
        line = max(1, round(s * 0.045))
        dot = max(2, round(s * 0.07))

        # Leads: the inputs (amber while on), the output (green while on).
        for i, pin in enumerate(GATE_PINS):
            y = round(rect.y + GATE_PIN_Y[i] * rect.height)
            ink = GATE_IN_ON if self.ins[i] else GATE_OFF
            pygame.draw.line(surface, ink, (rect.x, y), (body.x, y), line)
            if self.take_source(pin) is not None:
                pygame.draw.circle(surface, ink, (rect.x, y), dot)
            else:
                pygame.draw.circle(surface, INPUT_WELL, (rect.x, y), dot)
                pygame.draw.circle(surface, ink, (rect.x, y), dot, max(1, dot // 2))
        out_ink = GATE_ON if self.on else GATE_OFF
        pygame.draw.line(surface, out_ink, (body.right, rect.centery), (rect.right, rect.centery), line)
        pygame.draw.circle(surface, out_ink, (rect.right, rect.centery), dot)

        # The body: shadow, fill (lit while on), outline.
        points = self.body_points(body)
        pygame.draw.polygon(surface, (14, 14, 18), [(x, y + max(1, s // 22)) for x, y in points])
        pygame.draw.polygon(surface, shade(GATE_ON, 0.22) if self.on else INPUT_HOUSING, points)
        pygame.draw.polygon(surface, GATE_ON if self.on else self.color, points, max(1, round(s * 0.035)))

        size = round(body.height * 0.27)
        if size >= 7:
            font = get_font(size, "bahnschrift", True)
            ts = font.render(self.LABEL, True, (236, 248, 238) if self.on else shade(self.color, 1.1))
            surface.blit(ts, ts.get_rect(center=(body.x + body.width * 0.54, body.centery)))
        lsize = round(body.height * 0.2)
        if lsize >= 7:
            lf = get_font(lsize, "consolas", True)
            for i, pin in enumerate(GATE_PINS):
                ls = lf.render(pin, True, shade(self.color, 0.75))
                surface.blit(ls, ls.get_rect(midleft=(body.x + max(2, s // 20), rect.y + GATE_PIN_Y[i] * rect.height)))


class AndGate(LogicGate):
    """AND: `out` is on only while both `a` and `b` are on."""

    TYPE = "and_gate"
    MENU_LABEL = "Add AND gate"
    NAME = "AND"
    LABEL = "AND"

    def rule(self, ins):
        return all(ins)


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
DATA_DIR = os.path.join(SAVES_DIR, "data")    # CSV exports


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
             Prop("columns", kind="data", default=[], hidden=True),  # [{"cid", "name"}]
             Prop("rows", kind="data", default=[], hidden=True)]     # [{"time": iso, "values": {cid: v}}]
    COPY_SKIP = ("rows",)           # a copy (C key, or a placed machine) starts with no data

    @classmethod
    def create(cls, app):
        def make(x, y):
            used = {o.text for o in app.scene.objects if isinstance(o, DataCollector)}
            name = next(f"Data set {i}" for i in range(1, 1000) if f"Data set {i}" not in used)
            return cls(x, y, name, columns=[{"cid": 1, "name": "A"}, {"cid": 2, "name": "B"}])
        app.start_placing(make)

    @classmethod
    def migrate(cls, d):
        """Older saves kept the links on the columns ({"cid", "name", "id", "key"})
        and the collect input as `trigger`; they're inputs col<cid> / collect now."""
        if "trigger" not in d and not any("id" in c for c in d.get("columns", [])):
            return d
        d = dict(d)
        links = dict(d.get("links") or {})
        trigger = d.pop("trigger", None) or {}
        if trigger.get("id"):
            links["collect"] = {"id": trigger["id"], "key": trigger.get("key", "value")}
        columns = []
        for col in d.get("columns", []):
            if col.get("id"):
                links[f"col{col['cid']}"] = {"id": col["id"], "key": col.get("key", "value")}
            columns.append({k: v for k, v in col.items() if k not in ("id", "key")})
        d["columns"], d["links"] = columns, links
        return d

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

    # --- Inputs and collecting ------------------------------------------------------
    def takes(self):
        """The collect input (a press: 0 → 1 adds a row), then one per column."""
        return [Take("collect", kind="press")] + [
            Take(f"col{c['cid']}", c["name"], verb="records",
                 on_label=lambda app, cid=c["cid"]: self.column_menu(app, cid))
            for c in self.columns]

    def column_value(self, col):
        return self.take(f"col{col['cid']}")

    def on_press(self, port):
        self.collect()

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
            self.pick(app, f"col{cid}")
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
            self.unlink(f"col{cid}")
            app.save()                 # its values stay in the saved rows, just not shown / exported
        app.confirm(f"Remove column '{col['name']}'?", "Its recorded values are no longer shown.",
                    remove, ok_label="Remove")

    def column_menu(self, app, cid):
        """Clicking a column's name in the panel: rename or remove it."""
        col = self._column(cid)
        app.context_menu(pygame.mouse.get_pos(),
                         [(f"Column '{col['name']}'", None, "title"),
                          ("Rename…", lambda: self.rename_column(app, cid), None),
                          ("Remove column…", lambda: self.remove_column(app, cid), "danger")])

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
    def state_rows(self, app):
        return [Row("Rows", str(len(self.rows)))]

    def input_rows(self, app):
        return [Row("+ add column", "", on_click=self.add_column, color=None)]

    def actions(self, app):
        out = [Btn("Collect", self.collect, style="go")]
        if self.rows:
            out.append(Btn("Export CSV", self.export_csv))
        return out

    def menu_items(self, app):
        return [("Clear rows…", lambda: self.clear_rows(app), "danger")] if self.rows else []

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

    def input_point(self, port):
        """World point an input's wire ends at: the collect chip's left edge, or
        the top of a column's header."""
        if port == "collect":
            x, y, _w, h = self._boxes()["chip"]
            return self._to_world(x, y + h / 2)
        x, y, w, _h = self._boxes().get(int(port[3:]), (0, 0, 0, 0))
        return self._to_world(x + w / 2, y)

    def take_at(self, pos, camera):
        boxes, _k = self._screen_boxes(self.screen_rect(camera))
        if boxes["chip"].inflate(4, 4).collidepoint(pos):
            return "collect"
        return next((f"col{c['cid']}" for c in self.columns if boxes[c["cid"]].collidepoint(pos)), None)

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
            self.pick(app, "collect")
        elif hit == "add":
            self.add_column(app)
        elif hit is not None:
            self.pick(app, f"col{hit}")
        return hit is not None

    def on_drag(self, pos, app):
        if self._grab is not None:
            boxes, _k = self._screen_boxes(self.screen_rect(app.camera))
            self._drag_scroll(pos[1], boxes["scroll"])

    def on_release(self, pos, app):
        self._grab = None

    # --- Drawing ----------------------------------------------------------------------
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
        linked = self.take_source("collect") is not None
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
                if self.take_source(f"col{col['cid']}") is not None:
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
    """True if one of obj's inputs follows target_id."""
    if not obj.links:
        return False
    ports = {t.key for t in obj.takes()}
    return any(link.get("id") == target_id for port, link in obj.links.items() if port in ports)


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
                  prompt="Size (1 = normal, 2 = twice as big):")]
    TAKES = (Take("in", "passes on"),)

    @classmethod
    def migrate(cls, d):
        """Older saves kept the input as `source` (and a tag's streaming input
        as `stream`); they're the inputs in / stream now."""
        if "source" not in d and "stream" not in d:
            return d
        d = dict(d)
        links = dict(d.get("links") or {})
        for old, port in (("source", "in"), ("stream", "stream")):
            link = d.pop(old, None) or {}
            if link.get("id"):
                links[port] = {"id": link["id"], "key": link.get("key", "value")}
        d["links"] = links
        return d

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._last = None           # the value last frame (for the flare)
        self._blink = 0.0
        self._readers = []          # objects that read from this relay (re-counted every so often)
        self._recount = 0.0
        self._loop = False

    # --- The value ------------------------------------------------------------------
    def source_obj(self):
        return self.take_source("in")

    def value(self):
        """The source's value now, read straight through any chain of relays (so no
        frame of delay per hop). None when unlinked, when the source has none, or in a loop."""
        src = self.source_obj()
        if src is None or self.id in _RELAYS_RESOLVING:
            return None
        _RELAYS_RESOLVING.add(self.id)
        try:
            return src.outputs().get(self.links["in"].get("key"))
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

    # --- Linking (clicking it picks up its input's wire; empty canvas then lays relays) ---
    def on_links_changed(self):
        self._recount = 0.0

    def on_click(self, pos, app):
        self.pick(app, "in")
        return True

    def panel_status(self, app):
        if self._loop:
            return "this chain of relays loops back on itself", RELAY_LOOP
        return None

    def state_rows(self, app):
        value = self.value()
        names = ", ".join(short_name(o) for o in self._readers[:4]) + (" …" if len(self._readers) > 4 else "")
        return [Row("Value", format_value(value),
                    color=RELAY_LOOP if self._loop else None if value is not None else WARN_COLOR),
                Row("Feeds", f"{len(self._readers)}" + (f"  ({names})" if names else ""))]

    # --- Drawing ----------------------------------------------------------------------
    def input_point(self, port):
        """World point the incoming wire ends at."""
        return self.center

    def take_at(self, pos, camera):
        return "in"

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
             Prop("held", kind="data", default=None, hidden=True)]    # the last value taken (shown while off)
    TAKES = (Take("in", "value"),
             Take("stream", "streaming", kind="onoff", current=lambda: "always"))

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
        return self.take_source("stream")

    def streaming(self):
        """True while the tag passes its source's value live: always when nothing is
        linked to its streaming input, else while that is on (not 0)."""
        guard = self.id + "#stream"
        if self.stream_source() is None or guard in _RELAYS_RESOLVING:
            return True
        _RELAYS_RESOLVING.add(guard)
        try:
            return bool(self.take_on("stream"))
        finally:
            _RELAYS_RESOLVING.discard(guard)

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

    def input_point(self, port):
        if port == "stream":
            return self.stream_point()
        return self._to_world(TAG_HOLE[0], TAG_HOLE[1])

    def take_at(self, pos, camera):
        return "stream" if self._stream_rect(self.screen_rect(camera)).collidepoint(pos) else "in"

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
            self.pick(app, "stream")
        elif self._name_rect(rect).collidepoint(pos):
            self.rename(app)
        else:
            self.pick(app, "in")
        return True

    def state_rows(self, app):
        rows = super().state_rows(app)
        if self.stream_source() is not None:
            rows.append(Row("Streaming", "on" if self.streaming() else "off, holding the last value"))
        return rows

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

    def rename(self, app):
        self.edit_text(app)          # its name in the panel is its text

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
DEVICE_IN_DOT = (120, 220, 150)   # an input dot: a ring until linked, filled once linked


def draw_input_dot(surface, at, linked, s):
    """A device's input dot at `at` (on its left edge); s = the object's on-screen size."""
    r = max(2, s // 45)
    if linked:
        pygame.draw.circle(surface, DEVICE_IN_DOT, at, r)
    else:
        pygame.draw.circle(surface, INPUT_WELL, at, r)
        pygame.draw.circle(surface, shade(DEVICE_IN_DOT, 0.7), at, r, max(1, r // 2))


class Device(Thing):
    """One device on the instrument server, on the canvas. Shows its name, a
    status light, and a small screen with its param values (what it's set to)
    and readings (what it reports). Its params are its inputs: set one by hand,
    or link it to follow something (e.g. a slider). Its number readings — and
    its params' settings — are its outputs. Its links and the names of its
    params / readings are saved (so it can be wired up while it's offline);
    the device itself keeps running (and holds its last values) when the
    canvas closes."""

    TYPE = "device"
    MENU_LABEL = None             # not in the menus: placed from the Instrument server's device list
    NAME = "Device"
    CATEGORY = "Devices"
    DRIVER = None                 # a subclass for one driver sets its module name (e.g. "fake_arduino")
    COPY_SKIP = ("connect",)      # a copy starts as a view; Connect on it takes control
    DEFAULT_DEVICE = ""           # server device name to use when none of that driver is found
    SIZE = (230, 132)
    DOT_COLOR = (90, 220, 120)
    PROPS = [Prop("scale", "Size", "float", 1.0, min=0.25, max=50, unit="×",
                  prompt="Size (1 = normal, 2 = twice as big):"),
             Prop("device", kind="str", default="", hidden=True),     # name on the server
             Prop("connect", kind="bool", default=False, hidden=True),  # Connect pressed (saved)
             Prop("catalog", kind="data", default={}, hidden=True)]   # last seen params / readings (see _catalog)

    @classmethod
    def create(cls, app):
        client = instruments.client

        def make(x, y):
            obj = cls(x, y)
            obj.device = obj.find_device()                  # another view of the one device
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
        if not (self.DRIVER and self.connect and self.device and client.connected and client.catalog_in):
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

    # --- Which device: the driver's, never one of its own ---------------------------
    def is_my_device(self, options):
        """Is a server device of this DRIVER (with these options) the one this
        object shows? A driver runs one device (so: yes); a driver with a
        device_key (flip_mirror: one per mirror) overrides this to match it."""
        return True

    def default_device_name(self):
        """The name to add the device under when the server has none for it yet."""
        return self.DEFAULT_DEVICE or self.DRIVER

    def find_device(self):
        """The server device this (driver-specific) object shows: the one the
        server runs for its driver; else, while the server's catalog is in, the
        name to add it under; else (offline) the one it showed last."""
        if not self.DRIVER:
            return self.device                     # the generic Device: picked by name
        client = instruments.client
        for name, options in client.devices_for(self.DRIVER):
            if self.is_my_device(options):
                return name
        if (client.connected and client.catalog_in) or not self.device:
            return self.default_device_name()
        return self.device

    def connect_now(self, app):
        client = instruments.client
        if self.DRIVER:
            self.device = self.find_device()
            if self.take_control(app):
                info = client.device(self.device)       # it's running: keep its settings (a
                if info is not None:                    # different port would restart it)
                    self.adopt_device(info)
        self.connect = True
        client.want(self.id, True)
        client.retry_launch()
        if client.device(self.device) is not None:
            client.send({"op": "retry", "device": self.device})   # in error: try again now
        self._next_request = 0.0
        app.save()

    def take_control(self, app):
        """Disconnect the other objects showing this device — only
        this object's view: the device keeps running, nothing is turned off.
        True if one of them had it."""
        taken = False
        for o in app.scene.objects:
            if o is not self and isinstance(o, Device) and o.device == self.device and o.connect:
                o.connect = False
                instruments.client.want(o.id, False)
                o.on_control_taken()
                taken = True
        return taken

    def on_control_taken(self):
        """Another object took this device over."""

    def adopt_device(self, info):
        """Taking control of a running device: take on its settings rather than
        push this object's (options here; a subclass may add params)."""
        self.adopt_options(info.get("options", {}))

    def adopt_options(self, options):
        """Take on the running device's options (props named like them)."""
        for key in self.device_options():
            if key in options:
                setattr(self, key, options[key])

    def disconnect(self, app):
        self.connect = False
        app.save()

    def on_removed(self):
        instruments.client.want(self.id, False)

    # --- Inputs (params) and outputs (readings) ---------------------------------------
    def _catalog(self):
        """{"params": {name: unit}, "readings": {name: unit}, "text": [names]} —
        from the server while it's there (number readings only: text readings
        are shown, not linked), else the last one seen (saved), so it can be
        wired up while offline."""
        info = self.info()
        if info is None:
            return {"params": self.catalog.get("params", {}), "readings": self.catalog.get("readings", {}),
                    "text": self.catalog.get("text", [])}
        text = set(self.catalog.get("text", []))
        text |= {k for k, v in info["values"].items() if isinstance(v, str)}
        text |= {r for r, spec in info["readings"].items() if spec.get("kind") == "text"}
        return {"params": {p: spec.get("unit", "") for p, spec in info["params"].items()},
                "readings": {r: spec.get("unit", "") for r, spec in info["readings"].items()
                             if r not in text and spec.get("kind") != "data"},   # data: drawn, not linked
                "text": sorted(text)}

    @staticmethod
    def _param_give(param, readings):
        """A param's output name: the param's own, or "<param> (set)" when a
        reading has that name (e.g. a filter wheel's position)."""
        return f"{param} (set)" if param in readings else param

    def param_value(self, param):
        info = self.info()
        spec = info["params"].get(param) if info is not None else None
        return spec.get("value") if spec is not None else None

    def outputs(self):
        """Readings first (what it reports), then params (what they're set to)."""
        cat = self._catalog()
        info = self.info()
        values = info["values"] if info is not None else {}
        out = {}
        for r in cat["readings"]:
            v = values.get(r)
            out[r] = v if isinstance(v, (int, float)) and not isinstance(v, bool) else None
        for p in cat["params"]:
            out[self._param_give(p, cat["readings"])] = self.param_value(p)
        return out

    def gives(self):
        cat = self._catalog()
        return ([Give(r, unit=u) for r, u in cat["readings"].items()] +
                [Give(self._param_give(p, cat["readings"]), unit=u) for p, u in cat["params"].items()])

    def takes(self):
        """One input per param (set it by hand while it's live, or link it), plus
        any linked one the device no longer has, so it can be unlinked."""
        params = self._catalog()["params"]
        live = self.connect and self.info() is not None
        out = [Take(p, unit=u, current=lambda p=p: self.param_value(p),
                    set_by_hand=(lambda app, p=p: self.set_by_hand(app, p)) if live else None)
               for p, u in params.items()]
        return out + [Take(port) for port in self.links if port not in params]

    def tick(self, dt):
        """Keep the connection wanted (while Connect is on), set up the device
        if needed, remember its params / readings, and push each linked param's
        source value to the server (the client skips repeats and caps the rate)."""
        instruments.client.want(self.id, self.connect)
        if self.DRIVER:
            self.device = self.find_device()         # follows the server (an old save's "pda452" too)
        if not self.connect or not self.device or self.scene is None:
            return
        self._provision()
        if self.info() is not None:
            cat = self._catalog()
            if cat != self.catalog:
                self.catalog = cat
                self.scene.save()
        for param in self.links:
            value = self.take(param)
            if value is not None:
                instruments.client.set(self.device, param, value)

    # --- Inspector ------------------------------------------------------------------
    def next_device(self, app):
        names = instruments.client.device_names()
        if names:
            i = names.index(self.device) if self.device in names else -1
            self.device = names[(i + 1) % len(names)]
            app.save()

    def set_by_hand(self, app, param):
        info = self.info()
        spec = (info["params"].get(param) if info is not None else None) or {}

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

    def panel_name(self):
        return super().panel_name() or self.device or None

    def panel_status(self, app):
        status = self.status()
        return DEVICE_STATUS_TEXT.get(status, status), DEVICE_STATUS_COLORS.get(status, (110, 110, 120))

    def state_rows(self, app):
        rows = []
        if not self.DRIVER:
            rows.append(Row("Server device", self.device or "— (none)", on_click=self.next_device))
        info = self.info() if self.connect else None
        if info is not None:
            if info.get("message"):
                rows.append(Field("Message", info["message"], color=WARN_COLOR))
            for name, value in info["values"].items():          # text readings (status, interlock, …)
                if isinstance(value, str):
                    rows.append(Row(name, value))
        return rows

    def info_rows(self, app):
        rows = super().info_rows(app)
        if self.DRIVER:
            rows.append(Row("Server device", self.device or "—"))
        info = self.info() if self.connect else None
        if info is not None:
            rows.append(Row("Driver", info["driver"]))
            if not self.DRIVER and "port" in info.get("options", {}):
                rows.append(Row("Port", info["options"]["port"], on_click=self.change_port))
        return rows

    def remove_from_server(self, app):
        def remove():
            instruments.client.send({"op": "remove_device", "name": self.device})
            app.scene.delete(self)
        app.confirm(f"Remove '{self.device}'?", "From the server and the canvas.", remove)

    def run_action(self, app, name):
        instruments.client.send({"op": "action", "device": self.device, "name": name})

    def actions(self, app):
        """Driver actions (Home, Stop, …) while it's running, then Connect / Disconnect."""
        out = []
        info = self.info() if self.connect else None
        if info is not None and info.get("status") == "ok":
            for name in info.get("actions", []):
                out.append(Btn(name.capitalize(), lambda app, n=name: self.run_action(app, n)))
        return out + [self.connect_button()]

    def connect_button(self):
        return (Btn("Disconnect", self.disconnect) if self.connect
                else Btn("Connect", self.connect_now, style="go"))

    def menu_items(self, app):
        if not self.DRIVER and self.connect and instruments.client.connected and self.info() is not None:
            return [("Remove from server…", lambda: self.remove_from_server(app), "danger")]
        return []

    # --- Drawing ----------------------------------------------------------------------
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
                if spec.get("kind") == "data" or isinstance(v, (dict, list)):
                    continue                        # bulk data (a spectrum): its own object draws it
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

    def info_rows(self, app):
        rows = super().info_rows(app)
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
    and a readout to position. Home / Stop are buttons once it's connected.
    "Stage # in chain" picks which stage it shows: the server runs one device
    per stage, and every object set to that stage is a view of it. Stages the
    filter band drives (its longpass / shortpass) can't also be a stage device."""

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

    def is_my_device(self, options):
        """The driver runs one device per stage: this object's is the one for its stage."""
        return str(options.get("stage", 0)) == str(self.stage)

    def default_device_name(self):
        return f"{self.DEFAULT_DEVICE}{self.stage}"          # zaber0, zaber1, …

    def _numbers(self):
        """(position, target, low, high, moving, homed) from the server, None where unknown."""
        info = self.info() if self.connect else None
        if not info:
            return None, None, None, None, False, False
        spec = info["params"].get("target", {})
        values = info["values"]
        return (values.get("position"), spec.get("value"), spec.get("min"), spec.get("max"),
                bool(values.get("moving")), bool(values.get("homed")))

    def state_rows(self, app):
        rows = super().state_rows(app)
        position, target, low, high, moving, homed = self._numbers()
        if low is not None and high is not None:
            rows.append(Row("Travel", f"{low:g} – {high:g} mm"))
        return rows

    # --- Input dot: `target`, on the left edge level with the motor (draw_shape's layout) ---
    def input_point(self, port):
        if port != "target":
            return self.center
        c, w, h = self.world_center, self.world_width, self.world_height
        top = c.y - h / 2 + h * (0.08 * 1.5 + 0.26)              # the stage area, below the header
        return (c.x - w / 2, (top + c.y + h / 2 - h * 0.08) / 2)

    def take_at(self, pos, camera):
        dot = pygame.Vector2(camera.world_to_screen(self.input_point("target")))
        return "target" if dot.distance_to(pos) <= max(PORT_REACH, 8 * self.scale * camera.zoom) else None

    def on_click(self, pos, app):
        """A click on the input dot links it (anywhere else selects)."""
        if self.take_at(pos, app.camera):
            self.pick(app, "target")
            return True
        return False

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
        draw_input_dot(surface, (rect.x, round(rect.y + rect.height * (0.08 * 1.5 + 0.26 + 1 - 0.08) / 2)),
                       self.take_source("target") is not None, s)
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

    def output_point(self):
        """Its output dot (actual_center, its first output): on the right edge,
        level with the spectrum strip (as in _layout)."""
        c, w, h = self.world_center, self.world_width, self.world_height
        top = c.y - h / 2
        return (c.x + w / 2, top + h * ((0.08 * 1.5 + 0.24) + (1 - 0.08 - 0.16)) / 2)

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

    def state_rows(self, app):
        rows = super().state_rows(app)
        band = self._band()
        if self.live():
            rows.append(Row("Reach", f"{band['lo']:g} – {band['hi']:g} nm"))
        return rows

    def info_rows(self, app):
        return super().info_rows(app) + [
            Field("Calibration", "calibration/excitation_filters.json — recalibrate with "
                                 "scripts/calibrate_excitation_filters.py (Stop server first; "
                                 "reconnect to load it)")]

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
        center = self.outputs().get("actual_center")            # the output dot, in the band's color
        pygame.draw.circle(surface, wavelength_rgb(center) if center is not None else (70, 72, 82),
                           (rect.right, strip.centery), max(2, s // 32))

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

    def output_point(self):
        """Its output dot: on the right edge, level with the LCD (draw_shape's layout, in world units)."""
        c, w, h = self.world_center, self.world_width, self.world_height
        s = min(w, h)
        top = c.y - h / 2 + s * 0.07 * 1.5 + s * 0.14              # below the header
        bottom = c.y + h / 2 - s * 0.07 * 2                         # above the red stripe
        return (c.x + w / 2, (top + bottom) / 2)

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
        pygame.draw.circle(surface, PM_LCD if watts is not None else shade(PM_LCD, 0.4),
                           (rect.right, lcd.centery), max(2, s // 45))   # the output dot (power)
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
PDA_DOTS = ("count_rate", "voltage")    # the outputs with a dot on the right edge, top to bottom


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

    # --- Output dots: count_rate's level with the Time Tagger screen, voltage's with the NI DAQ one ---
    def output_point_of(self, key):
        """A dot on the right edge, level with the screen that shows that output
        (the same layout as draw_shape, in world units); the others: the center."""
        if key not in PDA_DOTS:
            return self.center
        c, w, h = self.world_center, self.world_width, self.world_height
        s = min(w, h)
        pad = s * 0.06
        top = c.y - h / 2 + pad + s * 0.13 + pad / 2              # the screens' top (below the header)
        body_h = c.y + h / 2 - pad - s * 0.09 - top                # down to the footer
        panel_h = (body_h - pad / 2) / 2
        y = top + panel_h / 2 + PDA_DOTS.index(key) * (panel_h + pad / 2)
        return (c.x + w / 2, y)

    def output_point(self):
        return self.output_point_of(PDA_DOTS[0])

    def give_at(self, pos, camera):
        for key in PDA_DOTS:
            dot = pygame.Vector2(camera.world_to_screen(self.output_point_of(key)))
            if dot.distance_to(pos) <= max(PORT_REACH, 8 * self.scale * camera.zoom):
                return key
        return None

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
            dot_color = color if values.get(key) is not None else shade(color, 0.4)
            pygame.draw.circle(surface, dot_color, (rect.right, panel.centery), max(2, s // 45))

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

    def hover(self, mouse_pos, mouse_pressed, camera):
        super().hover(mouse_pos, mouse_pressed, camera)
        self._switch_hover = (self.live() and self.is_hovered and
                              self._layout(self.screen_rect(camera))["switch"].collidepoint(mouse_pos))

    # --- Emission (the only ways it changes) ---------------------------------------
    def toggle_emission(self, app):
        emitting = self.laser_state()[0]
        wanted = not (emitting is None or emitting)       # unknown counts as on: never guess
        self._pending = (wanted, time.monotonic(), (self.info() or {}).get("message", ""))
        self._on_asked = world_time() if wanted else 0.0     # world_time: drawn on other PCs too
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

    def on_control_taken(self):
        self._pending = None          # its click is the new controller's business now

    def on_no_players(self):
        """The world keeps running when everyone leaves — but not the beam:
        nobody's left watching it (unless the device's off_when_canvas_closes
        option is off, as with the instrument server's own no-clients rule)."""
        info = self.info()
        if self.connect and info is not None and info.get("options", {}).get("off_when_canvas_closes", True):
            self.run_action(None, "emission_off")

    def actions(self, app):
        out = []
        emitting, interlock_ok, _i, _s, _p = self.laser_state()
        if self.live():
            if emitting is None or emitting:
                out.append(Btn("Turn emission OFF", self.toggle_emission, style="stop"))
            else:
                out.append(Btn("Turn emission ON", self.toggle_emission, style="go"))
            if interlock_ok == 0:
                out.append(Btn("Reset interlock", lambda app: self.run_action(app, "reset_interlock")))
        return out + [self.connect_button()]

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
            starting = world_time() - self._on_asked < SK_STARTING_SECONDS
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



# ---- Spectrometer: a Thorlabs CCS200 with a live plot ------------------------
SPEC_BODY = (36, 38, 46)
SPEC_SCREEN = (7, 9, 13)
SPEC_GRID = (28, 34, 46)
SPEC_CURVE = (150, 230, 255)
SPEC_LIVE = (235, 70, 60)
SPEC_FIBER = (240, 150, 40)          # an orange multimode fiber jacket
SPEC_BRASS = (196, 162, 92)          # the SMA connector
SPEC_MIN_PLOT_PX = 48                # a plot narrower than this shows a blank screen
SPEC_PENDING_SECONDS = 2.0           # longest a clicked Live button shows where it was asked to go


def wavelength_rgb(nm):
    """Roughly the color of light at nm (380-750 nm); a dim grey outside it."""
    if nm < 380 or nm > 750:
        return (70, 72, 84)
    if nm < 440:
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
    fade = 0.3 + 0.7 * (nm - 380) / 40 if nm < 420 else 0.3 + 0.7 * (750 - nm) / 50 if nm > 700 else 1.0
    return tuple(int(255 * (c * fade) ** 0.8) for c in (r, g, b))


def decode_spectrum(spec):
    """A driver's packed spectrum (see drivers/ccs200_spectrometer.py) ->
    (wavelengths, intensities) as numpy arrays, at full resolution."""
    y = np.frombuffer(base64.b64decode(spec["y"]), dtype="<i2").astype(float) * float(spec["scale"])
    t = np.linspace(0.0, 1.0, len(y)) if len(y) > 1 else np.zeros(len(y))
    return np.polyval(spec["axis"], t), y


def format_ms(ms):
    if ms >= 1000:
        return f"{ms / 1000:g} s"
    return f"{ms:.3g} ms" if ms < 1 else f"{ms:g} ms"


class Spectrometer(YScale, Device):
    """A Thorlabs CCS200 spectrometer (driver: ccs200_spectrometer), drawn as
    the instrument: an orange fiber into its SMA port, and a screen with the
    live spectrum over a wavelength axis with a rainbow strip.

    GO LIVE (the button on the object, or the panel) starts scanning; click it
    again to stop — the last scan stays on screen. Integration time,
    wavelength window and amplitude correction are Settings: this object sends
    them to the device (and again after a server restart). The integration time
    is shown on the object. Peak wavelength / height, total and saturated are
    outputs; the spectrum itself is drawn, not linked.

    Zoom: the whole spectrum comes over at full resolution; the screen draws
    one column per pixel — every point, joined, once there's room for them,
    and each column's lowest-to-highest value when there isn't, so a narrow
    line never drops out. Too small to read, the screen stays blank."""

    TYPE = "ccs200_spectrometer"
    MENU_LABEL = "Add spectrometer"
    NAME = "Spectrometer"
    DRIVER = "ccs200_spectrometer"
    DEFAULT_DEVICE = "spectrometer"
    SIZE = (380, 240)
    DOT_COLOR = SPEC_CURVE
    PROPS = [Prop("integration_ms", "Integration time", "float", 100.0, min=0.01, max=60000, unit="ms",
                  prompt="Integration time in ms (0.01 – 60000):"),
             Prop("min_nm", "Window from", "float", 360.0, min=190, max=1020, unit="nm",
                  prompt="Start of the wavelength window (nm):"),
             Prop("max_nm", "Window to", "float", 1000.0, min=190, max=1020, unit="nm",
                  prompt="End of the wavelength window (nm):"),
             Prop("correction", "Amplitude correction", "bool", True),
             Prop("address", "Address", "str", "auto",
                  prompt="The spectrometer's VISA address (auto finds it; sim:// to simulate):")]
    SETTINGS = ("integration_ms", "min_nm", "max_nm", "correction")    # props sent as the device's params

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._pending = None         # (asked-for live, when) after a click, until the device confirms
        self._button_hover = False
        self._decoded = None         # (key, wavelengths, intensities) of the scan last decoded
        self._curve = plotting.CurveImage()  # the curve's image, redrawn only when it changes
        self._strip_cache = (None, None)

    # --- The device ---------------------------------------------------------------------
    def device_options(self):
        return {"address": self.address or "auto"}

    def settings(self):
        return {"integration_ms": float(self.integration_ms), "min_nm": float(self.min_nm),
                "max_nm": float(self.max_nm), "correction": 1.0 if self.correction else 0.0}

    def adopt_device(self, info):
        """Taking control: keep the running spectrometer's settings."""
        super().adopt_device(info)
        for key in self.SETTINGS:
            value = info.get("params", {}).get(key, {}).get("value")
            if value is not None:
                setattr(self, key, value >= 0.5 if key == "correction" else float(value))

    def takes(self):
        return []                    # its settings are Settings (kept, and re-sent), not inputs

    def on_prop_changed(self, key):
        super().on_prop_changed(key)
        self.y_scale_changed(key)

    def auto_y_range(self):
        """Fitted to the latest scan: from 0 (or below, for negative dark
        pixels) to a little over its top."""
        data = self.spectrum()
        if data is None:
            return None
        y = data[2]
        low = min(0.0, float(np.min(y)))
        return low, max(float(np.max(y)) * 1.08, low + 1e-9)

    def running(self):
        return self.connect and self.status() == "ok"

    def readings_now(self):
        info = self.info() if self.connect else None
        return info["values"] if info and info.get("status") == "ok" else {}

    def is_live(self):
        if self._pending is not None:
            return self._pending[0]
        return bool(self.readings_now().get("live"))

    def tick(self, dt):
        super().tick(dt)
        if self.running():
            for key, value in self.settings().items():      # the client skips repeats
                instruments.client.set(self.device, key, value)
        if self._pending is not None:
            wanted, since = self._pending
            if (bool(self.readings_now().get("live")) == wanted
                    or time.monotonic() - since > SPEC_PENDING_SECONDS):
                self._pending = None

    def spectrum(self):
        """(key, wavelengths, intensities) of the latest scan, decoded once; or None."""
        spec = self.readings_now().get("spectrum")
        if not isinstance(spec, dict):
            return None
        key = (spec.get("seq"), spec.get("scale"), spec.get("n"))
        if self._decoded is None or self._decoded[0] != key:
            try:
                nm, y = decode_spectrum(spec)
            except (ValueError, KeyError, TypeError):
                return None
            if len(y) < 2:
                return None
            self._decoded = (key, nm, y)
        return self._decoded

    # --- Live / stop and the panel ------------------------------------------------------
    def toggle_live(self, app):
        wanted = not self.is_live()
        self._pending = (wanted, time.monotonic())
        self.run_action(app, "go_live" if wanted else "stop")

    def on_control_taken(self):
        self._pending = None

    def hover(self, mouse_pos, mouse_pressed, camera):
        super().hover(mouse_pos, mouse_pressed, camera)
        self._button_hover = (self.running() and self.is_hovered and
                              self._layout(self.screen_rect(camera))["button"].collidepoint(mouse_pos))

    def on_click(self, pos, app):
        """A click on the Live button toggles scanning (and doesn't select)."""
        if self.running() and self._layout(self.screen_rect(app.camera))["button"].inflate(6, 6).collidepoint(pos):
            self.toggle_live(app)
            return True
        return False

    def take_background(self, app):
        app.confirm("Take a background?", "Block the light into the spectrometer first — 10 scans are "
                    "averaged and subtracted from every scan after.",
                    lambda: self.run_action(app, "take_background"), ok_label="Take it")

    def actions(self, app):
        out = []
        if self.running():
            live = self.is_live()
            out.append(Btn("Stop" if live else "Go live", self.toggle_live, style="stop" if live else "go"))
            out.append(Btn("Single scan", lambda app: self.run_action(app, "single"), enabled=not live))
            out.append(Btn("Take background…", self.take_background))
            if self.readings_now().get("background"):
                out.append(Btn("Clear background", lambda app: self.run_action(app, "clear_background")))
            out.append(Btn("Save spectrum", lambda app: self.run_action(app, "save"),
                           enabled=self.spectrum() is not None))
        return out + [self.connect_button()]

    def state_rows(self, app):
        rows = super().state_rows(app)            # the device's message, the last saved file
        v = self.readings_now()
        if v:
            if v.get("peak_nm") is not None:
                rows.append(Row("Peak", f"{v.get('peak', 0):.4g} at {v['peak_nm']:.1f} nm"))
            rows.append(Row("Background", "subtracted" if v.get("background") else "none"))
            if v.get("saturated"):
                rows.append(Field("Detector", "saturated — shorten the integration time", color=WARN_COLOR))
        return rows

    # --- Layout (shared by drawing and clicking) ----------------------------------------
    def _layout(self, rect):
        s = rect.height
        pad = max(2, round(s * 0.05))
        port_w = max(3, round(rect.width * 0.07))            # the fiber + SMA port, left of the body
        body = pygame.Rect(rect.x + port_w, rect.y, rect.width - port_w, rect.height)
        header_h = round(s * 0.11)
        footer_h = round(s * 0.14)
        screen = pygame.Rect(body.x + pad, body.y + pad + header_h, body.width - 2 * pad,
                             body.height - 2 * pad - header_h - footer_h)
        btn_h = max(4, footer_h - pad)
        button = pygame.Rect(body.x + pad, screen.bottom + pad, round(btn_h * 3.4), btn_h)
        return {"body": body, "pad": pad, "port_w": port_w, "header_h": header_h, "screen": screen,
                "button": button}

    # --- Drawing ------------------------------------------------------------------------
    def draw_shape(self, surface, rect, camera):
        status = self.status()
        led_color = DEVICE_STATUS_COLORS.get(status, (110, 110, 120))
        s = rect.height
        lay = self._layout(rect)
        body, pad, screen = lay["body"], lay["pad"], lay["screen"]
        radius = max(2, s // 14)
        if s < 24:                                            # tiny: the box and its light
            draw_rect(surface, SPEC_BODY, body, border_radius=radius)
            pygame.draw.circle(surface, led_color, body.center, max(1, s // 5))
            return

        # Fiber: an orange jacket into a brass SMA connector on the body's left face.
        cy = body.y + round(body.height * 0.42)
        conn_h = max(3, round(s * 0.1))
        conn = pygame.Rect(body.x - round(lay["port_w"] * 0.55), cy - conn_h // 2, round(lay["port_w"] * 0.55), conn_h)
        pygame.draw.line(surface, shade(SPEC_FIBER, 0.6), (rect.x, cy + max(1, s // 60)), (conn.x, cy + max(1, s // 60)),
                         max(2, s // 28))
        pygame.draw.line(surface, SPEC_FIBER, (rect.x, cy), (conn.x, cy), max(2, s // 32))
        draw_rect(surface, SPEC_BRASS, conn, border_radius=max(1, conn_h // 5))
        draw_rect(surface, shade(SPEC_BRASS, 0.6), conn, width=max(1, s // 90), border_radius=max(1, conn_h // 5))

        # Body: housing with a light top edge, name + status light in the header.
        draw_rect(surface, (12, 12, 16), body.move(0, max(1, s // 40)), border_radius=radius)
        draw_rect(surface, SPEC_BODY, body, border_radius=radius)
        draw_rect(surface, shade(SPEC_BODY, 1.6), body, width=max(1, s // 90), border_radius=radius)
        led_r = max(2, s // 36)
        led = (body.right - pad - led_r, body.y + pad + lay["header_h"] // 2)
        if status in ("ok", "starting", "error", "missing", "connecting"):
            pygame.draw.circle(surface, shade(led_color, 0.4), led, round(led_r * 1.8))
        pygame.draw.circle(surface, led_color, led, led_r)
        size = round(lay["header_h"] * 0.78)
        seen = surface.get_clip()                 # zoomed far in, most of it is off screen: skip its text
        if size >= 8 and seen.colliderect((body.x, body.y, body.width, pad + lay["header_h"])):
            font = get_font(size, "bahnschrift")
            ts = font.render(clip_text(self.text or "CCS200 spectrometer", font, body.width - 4 * pad - 2 * led_r),
                             True, (200, 204, 218))
            surface.blit(ts, (body.x + pad, led[1] - ts.get_height() // 2))

        # Screen.
        bezel = max(1, s // 70)
        draw_rect(surface, (16, 16, 20), screen.inflate(2 * bezel, 2 * bezel), border_radius=max(2, radius - 2))
        draw_rect(surface, SPEC_SCREEN, screen, border_radius=max(2, radius - 4))
        prev_clip = surface.get_clip()
        surface.set_clip(screen.clip(prev_clip))
        try:
            self._draw_screen(surface, screen, status, led_color)
        finally:
            surface.set_clip(prev_clip)

        # Footer: the Live button, and the integration time (+ BG while a dark is subtracted).
        if not seen.colliderect((body.x, screen.bottom, body.width, body.bottom - screen.bottom)):
            return
        self._draw_button(surface, lay["button"], s)
        size = round(lay["button"].height * 0.62)
        if size >= 8:
            font = get_font(size, "bahnschrift")
            v = self.readings_now()
            info = self.info() if self.running() else None
            ms = (info or {}).get("params", {}).get("integration_ms", {}).get("value") or self.integration_ms
            text = format_ms(ms) + ("   BG" if v.get("background") else "")
            ts = font.render(text, True, (170, 190, 220) if self.running() else (110, 112, 126))
            surface.blit(ts, ts.get_rect(midright=(body.right - pad, lay["button"].centery)))

    def _draw_button(self, surface, button, s):
        """GO LIVE (dark) / LIVE (red, with a pulsing dot); an amber ring while a
        click waits for the device; greyed out while it isn't running."""
        running = self.running()
        live = running and self.is_live()
        r = max(2, button.height // 3)
        fill = SPEC_LIVE if live else (52, 56, 68) if running else (34, 36, 44)
        if self._button_hover:
            fill = shade(fill, 1.2)
        draw_rect(surface, fill, button, border_radius=r)
        draw_rect(surface, (8, 8, 10), button, width=max(1, s // 90), border_radius=r)
        if self._pending is not None:
            draw_rect(surface, (240, 190, 60), button.inflate(2, 2), width=max(1, s // 100), border_radius=r)
        size = round(button.height * 0.58)
        if size < 8:
            return
        font = get_font(size, "bahnschrift", True)
        label = "LIVE" if live else "GO LIVE"
        ts = font.render(label, True, (250, 240, 236) if live else (180, 186, 200) if running else (90, 92, 104))
        dot_r = max(2, button.height // 6)
        total = ts.get_width() + (3 * dot_r if live else 0)
        x = button.centerx - total // 2
        if live:
            glow = 0.6 + 0.4 * math.sin(world_time() * 6)   # the world's clock: no per-frame state
            pygame.draw.circle(surface, shade((255, 240, 240), glow), (x + dot_r, button.centery), dot_r)
            x += 3 * dot_r
        surface.blit(ts, ts.get_rect(midleft=(x, button.centery)))

    def _draw_screen(self, surface, screen, status, led_color):
        inner = screen.inflate(-max(4, screen.width // 30), -max(4, screen.height // 22))
        if status != "ok":                                    # what it's doing instead
            size = round(inner.height * 0.12)
            if size >= 8:
                font = get_font(size, "bahnschrift")
                ts = font.render(clip_text(DEVICE_STATUS_TEXT.get(status, status), font, inner.width), True,
                                 shade(led_color, 0.7) if status == "error" else (90, 96, 110))
                surface.blit(ts, ts.get_rect(center=inner.center))
            return
        axis_h = round(inner.height * 0.16) if inner.height >= 70 else 0     # rainbow strip + labels
        plot = pygame.Rect(inner.x, inner.y, inner.width, inner.height - axis_h)
        if plot.width < SPEC_MIN_PLOT_PX or plot.height < 16:
            return                                            # too small to read: a blank screen
        vis = plot.clip(surface.get_clip())
        if vis.width <= 0 or vis.height <= 0:
            return
        data = self.spectrum()
        lo, hi = (float(data[1][0]), float(data[1][-1])) if data else (float(self.min_nm), float(self.max_nm))
        if hi <= lo:
            hi = lo + 1.0
        to_x = lambda nm: plot.x + (nm - lo) / (hi - lo) * (plot.width - 1)

        # Grid: a line at each labelled wavelength, four across.
        step = nice_step(hi - lo, max(1, plot.width // 60))
        ticks = [t for t in np.arange(math.ceil(lo / step) * step, hi + 1e-9, step)]
        for t in ticks:
            x = round(to_x(t))
            if vis.left <= x < vis.right:
                pygame.draw.line(surface, SPEC_GRID, (x, plot.y), (x, plot.bottom - 1))
        for k in range(1, 4):
            y = plot.y + round(plot.height * k / 4)
            pygame.draw.line(surface, SPEC_GRID, (vis.left, y), (vis.right - 1, y))

        if axis_h:
            self._draw_axis(surface, plot, vis, axis_h, lo, hi, ticks, to_x)

        if data is None:                                      # nothing scanned yet
            size = round(plot.height * 0.1)
            if size >= 8 and vis.collidepoint(plot.center):
                font = get_font(size, "bahnschrift")
                ts = font.render("press GO LIVE", True, (80, 88, 104))
                surface.blit(ts, ts.get_rect(center=plot.center))
            return

        _key, nm, y = data
        ymin, ymax = self.y_range(self.auto_y_range())       # fitted to the scan, or the fixed one
        live = self.is_live()
        surf = self._curve.get(data[0], plot, vis, nm, y, (lo, hi), (ymin, ymax),
                               color=SPEC_CURVE if live else shade(SPEC_CURVE, 0.7), sorted_x=True,
                               fill_colors=self._column_colors(lo, hi, plot.width, vis.x - plot.x, vis.width),
                               fill_alpha=70 if live else 45)
        surface.blit(surf, vis.topleft)

        # Peak marker + label; SATURATED / the plot's top value in the corner.
        i = int(np.argmax(y))
        px, py = to_x(float(nm[i])), plot.y + (ymax - float(y[i])) / (ymax - ymin) * (plot.height - 1)
        mk = max(3, plot.height // 30)
        if vis.left - mk <= px <= vis.right + mk:
            top = max(plot.y + mk, round(py) - mk)
            pygame.draw.polygon(surface, (255, 236, 160), [(px, top + mk), (px - mk, top - mk), (px + mk, top - mk)])
            size = round(plot.height * 0.075)
            if size >= 8 and plot.width >= 160:
                font = get_font(size, "bahnschrift")
                ts = font.render(f"{float(nm[i]):.1f} nm", True, (255, 236, 160))
                r = ts.get_rect(midbottom=(px, top - mk - 1))
                if r.top < plot.top:                 # no room above the marker: beside it
                    r = ts.get_rect(midleft=(px + 2 * mk, top))
                    if r.right > plot.right:
                        r.midright = (px - 2 * mk, top)
                r.clamp_ip(plot)
                surface.blit(ts, r)
        size = round(plot.height * 0.07)
        if size >= 8 and vis.colliderect((plot.x, plot.y, size * 6, size + 4)):
            font = get_font(size, "bahnschrift", self.readings_now().get("saturated", 0) > 0)
            if self.readings_now().get("saturated"):
                ts = font.render("SATURATED", True, SPEC_LIVE)
            else:
                ts = font.render(f"{ymax:.3g}", True, (80, 88, 104))
            surface.blit(ts, (plot.x + 3, plot.y + 2))

    def _column_colors(self, lo, hi, w, ox, count):
        """The fill color (each column's wavelength, a little dimmed) of the
        visible columns, as a (count, 3) array — kept until the window, size or pan moves."""
        key = (lo, hi, w, ox, count)
        if getattr(self, "_colors_cache", (None,))[0] != key:
            nms = lo + (np.arange(ox, ox + count)) / max(1, w - 1) * (hi - lo)
            colors = np.array([shade(wavelength_rgb(float(v)), 0.8) for v in nms], dtype=np.uint8).reshape(-1, 3)
            self._colors_cache = (key, colors)
        return self._colors_cache[1]

    def _draw_axis(self, surface, plot, vis, axis_h, lo, hi, ticks, to_x):
        """Under the plot: a strip in the color of each wavelength, then the labels."""
        strip_h = max(2, axis_h // 4)
        strip = pygame.Rect(vis.x, plot.bottom + 1, vis.width, strip_h)
        key = (plot.width, vis.x - plot.x, vis.width, strip_h, lo, hi)
        if self._strip_cache[0] != key:
            s = pygame.Surface(strip.size)
            for j in range(vis.width):
                nm = lo + (vis.x - plot.x + j) / max(1, plot.width - 1) * (hi - lo)
                pygame.draw.line(s, wavelength_rgb(nm), (j, 0), (j, strip_h - 1))
            self._strip_cache = (key, s)
        surface.blit(self._strip_cache[1], strip.topleft)
        size = round((axis_h - strip_h) * 0.72)
        if size < 8 or not surface.get_clip().colliderect((plot.x, strip.bottom, plot.width, axis_h)):
            return
        font = get_font(size, "bahnschrift")
        label_y = strip.bottom + max(1, size // 4)
        us = font.render("nm", True, (100, 106, 120))              # the unit, at the row's right end
        unit = us.get_rect(topright=(plot.right, label_y))
        surface.blit(us, unit)
        for t in ticks:
            x = round(to_x(t))
            if vis.left - 40 <= x < vis.right + 40:
                pygame.draw.line(surface, (110, 116, 130), (x, strip.bottom), (x, label_y))
                ts = font.render(f"{t:g}", True, (140, 146, 160))
                r = ts.get_rect(midtop=(x, label_y))
                r.clamp_ip(pygame.Rect(plot.x, r.y, plot.width, r.height))
                if not r.colliderect(unit.inflate(size // 2, 0)):
                    surface.blit(ts, r)



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
            names = {o.text for o in app.scene.objects if isinstance(o, FlipMirror)}
            obj.text = next(f"Flipper {i}" for i in range(1, 1000) if f"Flipper {i}" not in names)
            if any(isinstance(o, FlipMirror) and o.mirror == "detection" for o in app.scene.objects):
                obj.mirror = "excitation"            # the second one is most likely the other mirror
            obj.device = obj.find_device()           # that mirror's device
            return obj
        app.start_placing(make)

    def is_my_device(self, options):
        """The driver runs one device per mirror: this object's is the one for its mirror."""
        return (options.get("mirror") or "detection") == self.mirror

    def default_device_name(self):
        return f"{self.DEFAULT_DEVICE}_{self.mirror}"

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

    def _in_dot_y(self, top, h):
        """The input dot's height: level with the beam (the hub, as in _mirror_spot)."""
        return top + h * (0.08 * 1.5 + 0.16) + h * (1 - 0.08 * 2.5 - 0.16) * 0.62

    def input_point(self, port):
        if port != "up":
            return self.center
        c, w, h = self.world_center, self.world_width, self.world_height
        return (c.x - w / 2, self._in_dot_y(c.y - h / 2, h))

    def take_at(self, pos, camera):
        dot = pygame.Vector2(camera.world_to_screen(self.input_point("up")))
        return "up" if dot.distance_to(pos) <= max(PORT_REACH, 8 * self.scale * camera.zoom) else None

    def on_click(self, pos, app):
        """A click on the input dot links it; on the mirror itself flips it (when its position is known)."""
        if self.take_at(pos, app.camera):
            self.pick(app, "up")
            return True
        if self.live() and self.mirror_state()[0] is not None:
            hub, reach = self._mirror_spot(self.screen_rect(app.camera))
            if (pygame.Vector2(pos) - hub).length() <= reach:
                self.flip(app)
                return True
        return False

    def actions(self, app):
        out = []
        is_up, up, down, _f = self.mirror_state()
        if self.live():
            if is_up is not None:
                out.append(Btn(f"Flip (to {down if is_up else up})", self.flip, style="go"))
            out.append(Btn(f"It's at {up}", lambda app: self.run_action(app, "sync_up")))
            out.append(Btn(f"It's at {down}", lambda app: self.run_action(app, "sync_down")))
            out.append(Btn("Find position (moves it)", self.find_position))
        return out + [self.connect_button()]

    def state_rows(self, app):
        rows = super().state_rows(app)
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
        draw_input_dot(surface, (rect.x, round(self._in_dot_y(rect.y, rect.height))),
                       self.take_source("up") is not None, s)

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

    def state_rows(self, app):
        rows = super().state_rows(app)
        here = self.position()
        for slot in range(1, self.slot_count() + 1):
            name = self.slot_name(slot)
            rows.append(Row(f"Slot {slot}" + ("  ◀ here" if slot == here else ""), name or "— (blank)",
                            on_click=lambda app, s=slot: self.rename_slot(app, s)))
        return rows

    def actions(self, app):
        out = []
        if self.live() and "position" not in self.links:
            here = self.position()
            if here is not None:
                n = self.slot_count()
                out.append(Btn("Next slot", lambda app: self.go_to(app, here % n + 1)))
        return out + super().actions(app)

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

    def input_point(self, port):
        """The input dot: on the left edge, level with the wheel's center (as in _layout)."""
        if port != "position":
            return self.center
        c, w, h = self.world_center, self.world_width, self.world_height
        top = c.y - h / 2 + h * (0.07 * 1.5 + 0.15)
        return (c.x - w / 2, (top + c.y + h / 2 - h * 0.07) / 2)

    def take_at(self, pos, camera):
        dot = pygame.Vector2(camera.world_to_screen(self.input_point("position")))
        return "position" if dot.distance_to(pos) <= max(PORT_REACH, 8 * self.scale * camera.zoom) else None

    def on_click(self, pos, app):
        """A click on the input dot links it; on a slot moves the wheel there (live, and not following a link)."""
        if self.take_at(pos, app.camera):
            self.pick(app, "position")
            return True
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
        draw_input_dot(surface, (rect.x, round(center.y)), self.take_source("position") is not None, s)
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
    and its drivers (click one to add a new device from it). Place as many as
    you like: there's one server, and each object is a view of the one link
    (they all show its state). Connect on one takes over from the one that had
    it; Disconnect all / Stop server turn every one off."""

    TYPE = "instrument_server"
    MENU_LABEL = "Add instrument server"
    NAME = "Instrument server"
    CATEGORY = "Devices"
    copyable = True
    COPY_SKIP = ("connect",)        # a copy is a view; Connect on it takes over
    SIZE = (220, 170)               # the radio body is the bottom ~60%; the antenna rises above
    DOT_COLOR = (120, 220, 150)
    PROPS = [Prop("scale", "Size", "float", 1.0, min=0.25, max=50, unit="×",
                  prompt="Size (1 = normal, 2 = twice as big):"),
             Prop("connect", kind="bool", default=False, hidden=True)]   # Connect pressed (saved)
    STATE_COLORS = {"connected": (90, 220, 120), "connecting": (240, 190, 60), "off": (110, 110, 120)}

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
        for o in app.scene.objects:             # take over from the one that had it
            if isinstance(o, InstrumentServer) and o is not self and o.connect:
                o.connect = False
                instruments.client.want(o.id, False)
        self.connect = True
        instruments.client.want(self.id, True)
        instruments.client.retry_launch()
        app.save()

    def _all_off(self, app):
        """Master off: every Instrument server object and every device stop wanting the link."""
        for o in app.scene.objects:
            if isinstance(o, (Device, InstrumentServer)):
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
            app.player.select_only(existing)
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

    # --- Panel ----------------------------------------------------------------------
    def panel_status(self, app):
        state = self.state()
        text = {"off": "off — press Connect", "connecting": "connecting…", "connected": "connected"}
        return text[state], self.STATE_COLORS[state]

    def state_rows(self, app):
        client = instruments.client
        return [Field("Server said", client.last_error, color=WARN_COLOR)] if client.last_error else []

    def more_sections(self, app):
        client = instruments.client
        if self.state() != "connected":
            return []
        devices = []
        for name in client.device_names():
            info = client.device(name) or {}
            status = info.get("status", "?")
            devices.append(Row(name, f"{info.get('driver', '?')} · {status}",
                               color=DEVICE_STATUS_COLORS.get(status),
                               on_click=lambda app, n=name, d=info.get("driver"): self.show_device(app, n, d)))
        drivers = [Row(drv["module"], drv["error"] or drv["title"],
                       color=WARN_COLOR if drv["error"] else None,
                       on_click=None if drv["error"] else lambda app, m=drv["module"]: self.add_device(app, m))
                   for drv in client.drivers]
        return [Section("Devices (click to place)", devices or [Row("—", "none yet")]),
                Section("Add a device", drivers or [Row("—", "no drivers found")])]

    def info_rows(self, app):
        return super().info_rows(app) + [Row("Address", f"{instruments.HOST}:{instruments.PORT} (this PC only)")]

    def actions(self, app):
        if not instruments.client.enabled:
            return [Btn("Connect", self.connect_now, style="go")]
        return [Btn("Disconnect all", self.disconnect)]

    def menu_items(self, app):
        if instruments.client.connected:
            return [("Stop server…", lambda: self.stop_server(app), "danger")]
        return []

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


# ============================================================================
# MACHINES — a circuit of objects packaged behind one skin (E menu ▸ Machines)
# ============================================================================
# Select objects, right-click ▸ Create machine…: they leave the canvas and go
# inside a Machine, which keeps running them. Its inputs (chips on the left)
# feed inner inputs, its outputs (nubs on the right) read inner outputs. Which
# ones: Machine.package picks them by where the objects sat (see there).
MC_PAD = 12                     # Machine layout, in world units at Size 1 (Counter-style)
MC_TITLE = 30                   # the name row
MC_CHIP_H, MC_GAP = 24, 8       # an input chip; the gap between chips / screens
MC_CHIP_MIN_W = 62
MC_TEXT = 15                    # chip text
MC_SCREEN_MIN_H = 56            # an output's screen
MC_SCREEN_MIN_W = 150
MC_MIN_W = 240
MC_COLOR = (110, 200, 235)
PORT_PREFIX = "port:"           # id of the stand-in an inner input links to (MachinePort)


STAND_IN_KIND = {"push_button": "press", "switch": "onoff", "slider": "number"}
STAND_IN_NAME = {"push_button": "Button"}


def stand_in(o):
    """(output, kind) if o is an input object (a push button, switch, slider):
    inside a machine it can't be clicked, so it becomes a machine input that
    stands in for it — else None."""
    if not isinstance(o, Input):
        return None
    gives = o.gives()
    return (gives[0].key, STAND_IN_KIND.get(o.TYPE, "number")) if gives else None


def is_hardware(objs):
    """Is there lab hardware among objs (a device, the instrument server — or a
    machine with some inside)?"""
    return any(isinstance(o, (Device, InstrumentServer)) or (isinstance(o, Machine) and o.has_hardware())
               for o in objs)


class MachineScene(Scene):
    """A machine's inner circuit: an ordinary Scene (its objects tick, link
    and press as on the canvas), never drawn and never saved on its own — a
    change inside saves the canvas the machine is on."""

    def __init__(self, objects, owner):
        super().__init__(objects, path=None)
        self.owner = owner

    def save(self):
        if self.owner.scene is not None:
            self.owner.scene.save()


class MachinePort(CanvasObject):
    """Inside a machine, stands in for one of its inputs: while that input is
    linked outside, the inner input it feeds is linked to this, and this gives
    whatever the outside source gives. In the inner scene's by_id only (not
    its objects), so it's never ticked or saved."""

    def __init__(self, machine, key, kind):
        super().__init__(id=PORT_PREFIX + key)
        self.machine, self.key, self.kind = machine, key, kind

    def outputs(self):
        return {"value": self.machine.take(self.key)}

    def gives(self):
        return [Give("value", kind="data" if self.kind == "data" else "number")]

    def draw(self, surface, camera):
        pass


class Machine(Thing):
    """Objects packaged as one: their circuit runs inside it (a MachineScene),
    off the canvas. Each port is a spec (a dict, saved in ports_in /
    ports_out): {"key", "label", "kind", "unit", "obj": inner object id} plus
    "port" (the inner input an input feeds), "source" (an input that stands
    in for an inner switch / button / slider: everything inside that read
    that output reads the machine input instead) or "out" (the inner output
    an output reads). A machine input that's unlinked leaves the inside as it
    was (an inner input unlinked or set by hand; a switch's own state). Placed from the E menu ▸
    Machines; `definition` is the uid of the saved machine it came from."""

    TYPE = "machine"
    NAME = "Machine"
    DOT_COLOR = MC_COLOR
    PROPS = [Prop("scale", "Size", "float", 1.0, min=0.25, max=50, unit="×",
                  prompt="Size (1 = normal, 2 = twice as big):"),
             Prop("color", "Color", "color", MC_COLOR),
             Prop("ports_in", kind="data", default=[], hidden=True),
             Prop("ports_out", kind="data", default=[], hidden=True),
             Prop("definition", kind="str", default="", hidden=True)]

    def __init__(self, x, y, text="", *, parts=None, **kwargs):
        super().__init__(x, y, text or "Machine", **kwargs)
        self.values = {}            # output key -> its value as of the last tick (players draw these)
        self._inner = MachineScene(list(parts or []), self)
        self._size_key, self._widths = None, (MC_MIN_W, 0, 0)

    # --- Saving, copying, placing -----------------------------------------------------
    def to_dict(self):
        d = super().to_dict()
        d["parts"] = [o.to_dict() for o in self._inner.objects]
        return d

    @classmethod
    def from_dict(cls, d):
        obj = super().from_dict(d)
        data = {"parts": d.get("parts", []), "ports_in": d.get("ports_in", []),
                "ports_out": d.get("ports_out", [])}
        if d.get("id") is None:          # a copy: its parts get fresh ids too (devices, scripts key on them)
            data = _remap_ids(data, {p["id"]: new_id() for p in data["parts"] if p.get("id")})
        obj._inner = MachineScene([object_from_dict(p) for p in data["parts"]], obj)
        d["ports_in"], d["ports_out"] = data["ports_in"], data["ports_out"]   # load_props reads these next
        return obj

    @classmethod
    def from_definition(cls, machine, at):
        """A fresh machine from a saved one (E menu ▸ Machines), centered on `at`."""
        data = {"parts": machine.get("objects", []), "ports_in": machine.get("ports_in", []),
                "ports_out": machine.get("ports_out", [])}
        data = _remap_ids(data, {p["id"]: new_id() for p in data["parts"] if p.get("id")})
        obj = cls(at[0], at[1], machine.get("name", "Machine"),
                  parts=[object_from_dict(p) for p in data["parts"]])
        obj.ports_in, obj.ports_out = data["ports_in"], data["ports_out"]
        obj.definition = machine.get("uid", "")
        return obj

    @classmethod
    def can_place(cls, machine, app):
        """A machine with lab hardware inside goes on the canvas once: two would
        fight over one instrument."""
        uid = machine.get("uid")
        if machine.get("hardware") and any(isinstance(o, cls) and o.definition == uid
                                           for o in app.scene.objects):
            app.confirm(f"'{machine.get('name')}' is already on the canvas",
                        "It has lab hardware in it, so it can only be placed once.",
                        lambda: None, ok_label="OK")
            return False
        return True

    @property
    def parts(self):
        return self._inner.objects

    def has_hardware(self):
        return is_hardware(self.parts)

    @property
    def copyable(self):
        return not self.has_hardware()

    def on_removed(self):
        for o in self.parts:
            o.on_removed()

    def on_no_players(self):
        for o in self.parts:
            o.on_no_players()

    # --- Ports ----------------------------------------------------------------------
    def takes(self):
        return [Take(s["key"], s["label"], kind=s.get("kind", "number"), unit=s.get("unit", ""),
                     on_label=lambda app, key=s["key"]: self.port_menu(app, "in", key))
                for s in self.ports_in]

    def gives(self):
        return [Give(s["key"], s["label"], unit=s.get("unit", ""), kind=s.get("kind", "number"),
                     on_label=lambda app, key=s["key"]: self.port_menu(app, "out", key))
                for s in self.ports_out]

    # --- Editing the ports (the panel: click a port's name, or + add input / output) -----
    def _ports(self, side):
        return self.ports_in if side == "in" else self.ports_out

    def _spec(self, side, key):
        return next((s for s in self._ports(side) if s["key"] == key), None)

    def input_rows(self, app):
        return [Row("+ add input", "", on_click=lambda app: self.add_port_menu(app, "in"))]

    def output_rows(self, app):
        return [Row("+ add output", "", on_click=lambda app: self.add_port_menu(app, "out"))]

    def port_menu(self, app, side, key):
        """Clicking a port's name in the panel: rename, move it up / down the skin, or remove it."""
        spec, ports = self._spec(side, key), self._ports(side)
        if spec is None:
            return
        i = ports.index(spec)
        part = self._inner.by_id.get(spec["obj"])
        if part is None:
            inner = "?"
        elif "source" in spec:
            inner = f"stands in for {short_name(part)}"
        else:
            inner = f"{short_name(part)} · {spec['port' if side == 'in' else 'out']}"
        items = [(f"{'Input' if side == 'in' else 'Output'} '{spec['label']}' ({inner})", None, "title"),
                 ("Rename…", lambda: self.rename_port(app, side, key), None)]
        if i > 0:
            items.append(("Move up", lambda: self.move_port(app, side, key, -1), None))
        if i < len(ports) - 1:
            items.append(("Move down", lambda: self.move_port(app, side, key, 1), None))
        items.append(("Remove from the machine", lambda: self.remove_port(app, side, key), "danger"))
        app.context_menu(pygame.mouse.get_pos(), items)

    def rename_port(self, app, side, key):
        def submit(text):
            spec = self._spec(side, key)
            if spec is not None and text.strip():
                spec["label"] = text.strip()
                self._ports_changed(app)
        spec = self._spec(side, key)
        app.ask(f"Name of this {'input' if side == 'in' else 'output'}:", submit, value=spec["label"] if spec else "")

    def move_port(self, app, side, key, step):
        ports = self._ports(side)
        spec = self._spec(side, key)
        i = ports.index(spec) if spec is not None else -1
        if 0 <= i + step < len(ports) and i >= 0:
            ports[i], ports[i + step] = ports[i + step], ports[i]
            self._ports_changed(app)

    def remove_port(self, app, side, key):
        """Take a port off the skin. A wire on it goes too; Ctrl+Z puts both back."""
        spec = self._spec(side, key)
        scene = self.scene
        if spec is None or scene is None:
            return
        rewired = [o for o in scene.objects if o is self or
                   (side == "out" and any(l.get("id") == self.id and l.get("key") == key for l in o.links.values()))]
        scene.push_undo(app.player.name, {
            "objects": [], "weights": {},
            "links": {o.id: copy.deepcopy(o.links) for o in rewired},
            "props": {self.id: {"ports_in": copy.deepcopy(self.ports_in),
                                "ports_out": copy.deepcopy(self.ports_out)}}})
        if side == "in":
            self.links = {k: v for k, v in self.links.items() if k != key}
            target, pid = self._inner.by_id.get(spec["obj"]), PORT_PREFIX + key
            if "source" in spec:
                self._stand_in(spec, False)
            elif target is not None and (target.links.get(spec["port"]) or {}).get("id") == pid:
                target.links = {k: v for k, v in target.links.items() if k != spec["port"]}
                target.on_links_changed()
            self._inner.by_id.pop(pid, None)
            self.ports_in = [s for s in self.ports_in if s["key"] != key]
        else:
            for o in rewired:
                if o is not self:
                    o.links = {k: v for k, v in o.links.items()
                               if not (v.get("id") == self.id and v.get("key") == key)}
                    o.on_links_changed()
            self.ports_out = [s for s in self.ports_out if s["key"] != key]
        self._ports_changed(app)

    def port_candidates(self, side):
        """[(label, object, inner input / output)] that could become a port: every
        inner input that isn't wired from another part (inputs), every inner
        output (outputs) — minus the ones that already are."""
        taken = {(s["obj"], s.get("port", s.get("source")) if side == "in" else s["out"])
                 for s in self._ports(side)}
        found = []
        for o in self.parts:
            if side == "in":
                si = stand_in(o)
                if si is not None and (o.id, si[0]) not in taken:
                    found.append(("(stand in for it)", o, None))
                for take in o.takes():
                    src = (o.links.get(take.key) or {}).get("id")
                    if (o.id, take.key) not in taken and (src is None or src not in self._inner.by_id):
                        found.append((take.label, o, take))
            else:
                for give in o.gives():
                    if (o.id, give.key) not in taken:
                        found.append((give.label, o, give))
        return found

    def add_port_menu(self, app, side):
        found = self.port_candidates(side)
        what = "input" if side == "in" else "output"
        if not found:
            app.confirm(f"No more {what}s to add", f"Every inner {what} is already on the machine"
                        + (" (or wired inside it)." if side == "in" else "."), lambda: None, ok_label="OK")
            return
        items = [(f"Add an {what}", None, "title")]
        items += [(f"{short_name(o)} · {label}", lambda o=o, p=p: self.add_port(app, side, o, p), None)
                  for label, o, p in found]
        app.context_menu(pygame.mouse.get_pos(), items)

    def add_port(self, app, side, obj, port):
        """Put an inner input (a Take) / output (a Give) of obj on the skin, at the bottom."""
        ports = self._ports(side)
        prefix = "i" if side == "in" else "o"
        used = {s["key"] for s in ports}
        key = next(f"{prefix}{n}" for n in range(1, 10000) if f"{prefix}{n}" not in used)
        if port is None:                                  # a switch / button / slider: stand in for it
            ports.append(self._stand_in_spec(key, obj, ports, [o for o in self.parts if stand_in(o)]))
            self._ports_changed(app)
            return
        label = port.label if all(s["label"] != port.label for s in ports) else f"{short_name(obj)} {port.label}"
        spec = {"key": key, "label": label, "kind": port.kind, "unit": port.unit, "obj": obj.id}
        spec["port" if side == "in" else "out"] = port.key
        ports.append(spec)
        self._ports_changed(app)

    def _ports_changed(self, app):
        """The ports were edited: save, and keep the saved machine (E menu) in step, so
        the next one placed has them too. Its parts are in the same order as this
        machine's, so ids are matched by position."""
        defn = next((m for m in machines() if m.get("uid") == self.definition and self.definition), None)
        if defn is not None and len(defn.get("objects", [])) == len(self.parts):
            to_defn = {o.id: d.get("id") for o, d in zip(self.parts, defn["objects"])}
            defn["ports_in"] = _remap_ids(copy.deepcopy(self.ports_in), to_defn)
            defn["ports_out"] = _remap_ids(copy.deepcopy(self.ports_out), to_defn)
            _save_machines()
        app.save()

    def outputs(self):
        return {s["key"]: self.values.get(s["key"]) for s in self.ports_out}

    def on_press(self, port):
        pass                        # nothing to do itself (the inner object acts); this makes its press wires flash

    def tick(self, dt):
        inner = self._inner
        for spec in self.ports_in:
            target = inner.by_id.get(spec["obj"])
            if target is None:
                continue
            pid = PORT_PREFIX + spec["key"]
            if not isinstance(inner.by_id.get(pid), MachinePort):
                inner.by_id[pid] = MachinePort(self, spec["key"], spec.get("kind", "number"))
            if "source" in spec:
                self._stand_in(spec, self.take_source(spec["key"]) is not None)
                continue
            link = {"id": pid, "key": "value"}
            current = target.links.get(spec["port"])
            if self.take_source(spec["key"]) is not None:
                if current != link:
                    target.links = dict(target.links, **{spec["port"]: link})
                    target._press_last.pop(spec["port"], None)
                    target.on_links_changed()
            elif current is not None and current.get("id") == pid:
                target.links = {k: v for k, v in target.links.items() if k != spec["port"]}
                target._press_last.pop(spec["port"], None)
                target.on_links_changed()
        inner.tick(dt)
        values = {}
        for spec in self.ports_out:
            src = inner.by_id.get(spec["obj"])
            values[spec["key"]] = src.outputs().get(spec["out"]) if src is not None else None
        self.values = values

    @staticmethod
    def _stand_in_spec(key, o, ports, among):
        """The input spec standing in for switch / button / slider o: named after
        it, or "Switch 1", "Switch 2", … (numbered among the unnamed ones of its
        type in `among`, top to bottom) when it has no name."""
        source, kind = stand_in(o)
        label = o.panel_name()
        if not label:
            same = [p for p in among if p.TYPE == o.TYPE and not p.panel_name()]
            label = f"{STAND_IN_NAME.get(o.TYPE, o.type_name())} {same.index(o) + 1 if o in same else 1}"
        taken = {s["label"] for s in ports}
        base, n = label, 2
        while label in taken:
            label, n = f"{base} ({n})", n + 1
        return {"key": key, "label": label, "kind": kind, "unit": "", "obj": o.id, "source": source}

    def _stand_in(self, spec, on):
        """Point everything inside that reads the switch / button / slider of a
        "source" input at the machine input (on), or back at it (off)."""
        pid = PORT_PREFIX + spec["key"]
        own = {"id": spec["obj"], "key": spec["source"]}
        old, new = (own, {"id": pid, "key": "value"}) if on else ({"id": pid, "key": "value"}, own)
        for o in self.parts:
            ports = [k for k, l in o.links.items() if l.get("id") == old["id"] and l.get("key") == old["key"]]
            if ports:
                o.links = dict(o.links, **{k: dict(new) for k in ports})
                for k in ports:
                    o._press_last.pop(k, None)
                o.on_links_changed()

    # --- Unfolding: the parts back on the canvas ------------------------------------------
    @panel_button("Unfold machine")
    def unfold(self, app):
        """Put the parts back on the canvas, centered where the machine is, with
        every wire kept: an outside source on a machine input now feeds what that
        input fed inside (for a stand-in: what the switch fed), and outside readers
        of an output read the inner output. Ctrl+Z folds it back up."""
        scene = self.scene
        if scene is None or self not in scene.objects:
            return
        parts = list(self.parts)
        readers = [o for o in scene.objects
                   if o is not self and any(l.get("id") == self.id for l in o.links.values())]
        batch = scene._deleted_batch([self])
        batch["links"] = {o.id: copy.deepcopy(o.links) for o in readers}

        # Inside wires from the machine's inputs: to the outside source (or back as they were).
        for spec in self.ports_in:
            outer = self.links.get(spec["key"])
            if outer is not None and outer.get("id") not in scene.by_id:
                outer = None
            pid = PORT_PREFIX + spec["key"]
            if "source" in spec:
                own = {"id": spec["obj"], "key": spec["source"]}
                follows = (lambda l: l.get("id") == pid or l == own) if outer is not None else \
                          (lambda l: l.get("id") == pid)
                to = dict(outer) if outer is not None else own
                for o in parts:
                    if any(follows(l) for l in o.links.values()):
                        o.links = {k: (dict(to) if follows(l) else l) for k, l in o.links.items()}
                        o.on_links_changed()
                continue
            target = next((o for o in parts if o.id == spec["obj"]), None)
            if target is None:
                continue
            current = target.links.get(spec["port"])
            if outer is not None:
                target.links = dict(target.links, **{spec["port"]: dict(outer)})
            elif current is not None and current.get("id") == pid:
                target.links = {k: v for k, v in target.links.items() if k != spec["port"]}
            target.on_links_changed()
        # Outside readers of its outputs: to the inner outputs.
        outs = {s["key"]: {"id": s["obj"], "key": s["out"]} for s in self.ports_out}
        for o in readers:
            o.links = {k: (dict(outs[l.get("key")]) if l.get("id") == self.id and l.get("key") in outs else l)
                       for k, l in o.links.items() if not (l.get("id") == self.id and l.get("key") not in outs)}
            o.on_links_changed()

        # Where the machine is (its parts kept the spot they had when it was made).
        left, top, right, bottom = group_bounds(parts)
        c = self.center
        dx, dy = c[0] - (left + right) / 2, c[1] - (top + bottom) / 2
        for o in parts:
            if o.movable:
                o.move(dx, dy)

        # Swap: the machine off (no on_removed — its parts carry on), the parts in its place.
        index = scene.objects.index(self)
        scene.objects[index:index + 1] = parts
        scene.by_id.pop(self.id, None)
        for sel in scene.selections.values():
            sel.discard(self.id)
        for o in parts:
            o.scene = scene
            scene.by_id[o.id] = o
        scene.sync_weights()
        scene.save()
        batch["added"] = [o.id for o in parts]
        scene.push_undo(app.player.name, batch)
        app.player.select_many(parts)

    # --- Packaging a selection ----------------------------------------------------------
    @classmethod
    def package(cls, app, objs, name):
        """Create machine…: take objs off the canvas into a new machine where
        they sat, save it to the E menu ▸ Machines, and select it. Ctrl+Z puts
        the objects back.

        Ports, by where the objects sat — inputs on the left, outputs on the
        right: every input of an object in the left half that isn't wired from
        another part becomes a machine input, and every output of an object in
        the right half that no other part reads becomes a machine output.
        Wires crossing the edge always become ports, so nothing comes unplugged:
        the outside source now feeds the machine's input, and outside readers
        read the machine's output."""
        scene = app.scene
        objs = [o for o in objs if o in scene.objects]
        if not objs:
            return None
        ids = {o.id for o in objs}
        readers = [o for o in scene.objects
                   if o.id not in ids and any(l.get("id") in ids for l in o.links.values())]
        batch = scene._deleted_batch(objs)
        batch["links"] = {o.id: copy.deepcopy(o.links) for o in readers}

        xs = [o.center[0] for o in objs if o.center is not None] or [0.0]
        mid, span = (min(xs) + max(xs)) / 2, max(xs) - min(xs)
        on_left = lambda o: o.center is None or span < 1 or o.center[0] <= mid
        on_right = lambda o: o.center is None or span < 1 or o.center[0] >= mid

        ins, outs, moved_links = [], [], {}
        for o in objs:
            if stand_in(o) is not None:                       # a switch / button / slider: an input
                c = o.center or (0, 0)
                ins.append(((c[1], c[0]), o, None))
            for take in o.takes():
                link = o.links.get(take.key) or {}
                src = link.get("id")
                if src in ids:
                    continue                                  # wired inside: stays inside
                from_outside = src is not None and src in scene.by_id
                if from_outside or on_left(o):
                    p = o.input_point(take.key) or o.center or (0, 0)
                    ins.append(((p[1], p[0]), o, take))
                    if from_outside:
                        moved_links[(o.id, take.key)] = dict(link)
        for o in objs:
            read_inside = {l.get("key") for p in objs for l in p.links.values() if l.get("id") == o.id}
            read_outside = {l.get("key") for p in readers for l in p.links.values() if l.get("id") == o.id}
            for i, give in enumerate(o.gives()):
                if give.key in read_outside or (on_right(o) and give.key not in read_inside
                                                and stand_in(o) is None):
                    c = o.center or (0, 0)
                    outs.append(((c[1], c[0], i), o, give))
        ins.sort(key=lambda item: item[0])
        outs.sort(key=lambda item: item[0])

        def labels(items):
            plain = [item[2].label if item[2] is not None else None for item in items]
            return [f"{short_name(o)} {lab}" if lab is not None and plain.count(lab) > 1 else lab
                    for (_k, o, _p), lab in zip(items, plain)]
        ports_in = []
        for n, ((_k, o, take), lab) in enumerate(zip(ins, labels(ins))):
            if take is None:
                ports_in.append(cls._stand_in_spec(f"i{n + 1}", o, ports_in, [i[1] for i in ins]))
            else:
                ports_in.append({"key": f"i{n + 1}", "label": lab, "kind": take.kind, "unit": take.unit,
                                 "obj": o.id, "port": take.key})
        ports_out = [{"key": f"o{n + 1}", "label": lab, "kind": give.kind, "unit": give.unit,
                      "obj": o.id, "out": give.key}
                     for n, ((_k, o, give), lab) in enumerate(zip(outs, labels(outs)))]

        # Off the canvas (no on_removed: they keep running, inside) ...
        for o in objs:
            scene.objects.remove(o)
            scene.by_id.pop(o.id, None)
            for sel in scene.selections.values():
                sel.discard(o.id)
        # ... with the wires that came in from outside moved onto the machine.
        machine_links = {}
        for spec in ports_in:
            link = moved_links.get((spec["obj"], spec.get("port")))
            if link is not None:
                machine_links[spec["key"]] = link
                o = next(p for p in objs if p.id == spec["obj"])
                o.links = {k: v for k, v in o.links.items() if k != spec["port"]}

        definition = save_machine(name, objs, uid=new_id(), ports_in=ports_in, ports_out=ports_out,
                                  hardware=is_hardware(objs))
        left, top, right, bottom = group_bounds(objs)
        machine = cls((left + right) / 2, (top + bottom) / 2, definition["name"], parts=objs)
        machine.ports_in, machine.ports_out = ports_in, ports_out
        machine.definition = definition["uid"]
        machine.links = machine_links

        by_port = {(s["obj"], s["out"]): s["key"] for s in ports_out}
        for o in readers:
            o.links = {k: ({"id": machine.id, "key": by_port[(l["id"], l.get("key"))]}
                           if (l.get("id"), l.get("key")) in by_port else l)
                       for k, l in o.links.items()}
        scene.add_many([machine])
        batch["added"] = [machine.id]
        scene.push_undo(app.player.name, batch)
        app.player.select_many([machine])
        return machine

    # --- Layout (world units at Size 1; shared by drawing, clicks and wires) ----------
    def _measure(self):
        """(width, chip width, screen-area height), re-measured when the ports change."""
        key = (tuple(s["label"] for s in self.ports_in), tuple(s["label"] for s in self.ports_out), self.text)
        if key != self._size_key:
            font = get_font(100, "consolas", True)
            text_w = lambda t: font.size(t)[0] * MC_TEXT / 100
            chip_w = max((max(MC_CHIP_MIN_W, text_w(s["label"]) + 16) for s in self.ports_in), default=0)
            label_w = max((text_w(s["label"]) for s in self.ports_out), default=0)
            screen_w = max(MC_SCREEN_MIN_W, label_w + 110)
            title_w = get_font(100, "bahnschrift").size(self.text or "Machine")[0] * 16 / 100 + 80
            left = MC_PAD + (chip_w + 10 if self.ports_in else 0)
            width = max(MC_MIN_W, min(900, max(left + screen_w + MC_PAD, title_w + 2 * MC_PAD)))
            n_in, n_out = len(self.ports_in), max(1, len(self.ports_out))
            chips_h = n_in * (MC_CHIP_H + MC_GAP) - MC_GAP if n_in else 0
            area_h = max(chips_h, n_out * (MC_SCREEN_MIN_H + MC_GAP) - MC_GAP)
            self._size_key, self._widths = key, (width, chip_w, area_h)
        return self._widths

    @property
    def world_width(self):
        return self._measure()[0] * self.scale

    @property
    def world_height(self):
        return (MC_PAD + MC_TITLE + self._measure()[2] + MC_PAD) * self.scale

    def _in_boxes(self):
        """[(key, x, y, w, h)] of the input chips, world units at Size 1 from the top-left."""
        _w, chip_w, _h = self._measure()
        top = MC_PAD + MC_TITLE
        return [(s["key"], MC_PAD, top + i * (MC_CHIP_H + MC_GAP), chip_w, MC_CHIP_H)
                for i, s in enumerate(self.ports_in)]

    def _out_boxes(self):
        """[(key, x, y, w, h)] of the output screens, sharing the height beside the chips."""
        width, chip_w, area_h = self._measure()
        x = MC_PAD + (chip_w + 10 if self.ports_in else 0)
        n = max(1, len(self.ports_out))
        h = (area_h - (n - 1) * MC_GAP) / n
        top = MC_PAD + MC_TITLE
        return [(s["key"], x, top + i * (h + MC_GAP), width - MC_PAD - x, h) for i, s in enumerate(self.ports_out)]

    def _to_world(self, x, y):
        c = self.world_center
        return (c.x - self.world_width / 2 + x * self.scale, c.y - self.world_height / 2 + y * self.scale)

    def input_point(self, port):
        for key, x, y, _w, h in self._in_boxes():
            if key == port:
                return self._to_world(x, y + h / 2)
        return self.center

    def output_point_of(self, key):
        """Each output has its own nub, on the right edge level with its screen."""
        width = self._measure()[0]
        for k, _x, y, _w, h in self._out_boxes():
            if k == key:
                return self._to_world(width, y + h / 2)
        return self.center

    def output_point(self):
        return self.output_point_of(self.ports_out[0]["key"]) if self.ports_out else self.center

    def _rects(self, rect, boxes):
        k = rect.width / self._measure()[0]          # screen px per layout unit
        return [(key, pygame.Rect(rect.x + round(x * k), rect.y + round(y * k),
                                  max(1, round(w * k)), max(1, round(h * k))))
                for key, x, y, w, h in boxes], k

    def take_at(self, pos, camera):
        chips, _k = self._rects(self.screen_rect(camera), self._in_boxes())
        return next((key for key, r in chips if r.inflate(6, 6).collidepoint(pos)), None)

    def give_at(self, pos, camera):
        for s in self.ports_out:
            nub = pygame.Vector2(camera.world_to_screen(self.output_point_of(s["key"])))
            if nub.distance_to(pos) <= max(PORT_REACH, 8 * self.scale * camera.zoom):
                return s["key"]
        return None

    def on_click(self, pos, app):
        key = self.take_at(pos, app.camera)
        if key is not None:
            self.pick(app, key)
            return True
        return False                # anywhere else selects it

    # --- Panel ----------------------------------------------------------------------
    def state_rows(self, app):
        n = len(self.parts)
        hw = " — has lab hardware, so it's placed once" if self.has_hardware() else ""
        return [Row("Inside", f"{n} object{'' if n == 1 else 's'}{hw}")]

    # --- Drawing ----------------------------------------------------------------------
    def draw_shape(self, surface, rect, camera):
        s = min(rect.width, rect.height)
        radius = max(2, s // 8)
        draw_rect(surface, (14, 14, 18), rect.move(0, max(1, s // 22)), border_radius=radius)
        draw_rect(surface, INPUT_HOUSING, rect, border_radius=radius)
        draw_rect(surface, shade(INPUT_HOUSING, 1.35), rect, width=max(1, s // 40), border_radius=radius)
        if s < 16:
            draw_rect(surface, shade(self.color, 0.5), rect.inflate(-max(1, s // 3), -max(1, s // 3)))
            return
        k = rect.width / self._measure()[0]          # screen px per layout unit

        # The name, and "machine" beside it in its color.
        size = round(16 * k)
        if size >= 7:
            font = get_font(size, "bahnschrift")
            title = font.render(clip_text(self.text or "Machine", font, rect.width * 0.7), True, (226, 228, 236))
            surface.blit(title, (rect.x + round(MC_PAD * k), rect.y + round(MC_PAD * k)))
            tag = get_font(max(7, round(13 * k)), "consolas", True).render("machine", True, shade(self.color, 0.85))
            room = rect.right - round(MC_PAD * k) - (rect.x + round(MC_PAD * k) + title.get_width() + round(8 * k))
            if tag.get_width() <= room:
                surface.blit(tag, tag.get_rect(midright=(rect.right - round(MC_PAD * k),
                                                         rect.y + round(MC_PAD * k) + title.get_height() // 2)))

        # Inputs: chips on the left — hollow until linked, filled once linked, brighter under the mouse.
        mouse = pygame.mouse.get_pos() if self.is_hovered else None
        csize = round(MC_TEXT * k)
        chips, _k = self._rects(rect, self._in_boxes())
        for (key, r), spec in zip(chips, self.ports_in):
            linked = self.take_source(key) is not None
            hot = mouse is not None and r.inflate(6, 6).collidepoint(mouse)
            color = shade(self.color, 1.2) if hot else self.color
            cr = max(2, r.height // 2)
            if linked:
                draw_rect(surface, color, r, border_radius=cr)
            else:
                draw_rect(surface, INPUT_WELL, r, border_radius=cr)
                draw_rect(surface, color, r, width=max(1, round(2 * k)), border_radius=cr)
            if csize >= 7:
                font = get_font(csize, "consolas", True)
                ts = font.render(clip_text(spec["label"], font, r.width - 6), True, INPUT_WELL if linked else color)
                surface.blit(ts, ts.get_rect(center=r.center))

        # Outputs: a screen each (its name small in the corner, the value big), each with its own nub.
        if not self.ports_out:
            _key, x, y, w, h = ("", *self._out_boxes_empty())
            screen = pygame.Rect(rect.x + round(x * k), rect.y + round(y * k), round(w * k), round(h * k))
            draw_rect(surface, shade(self.color, 0.07), screen, border_radius=max(2, round(6 * k)))
            if csize >= 7:
                ts = get_font(csize, "consolas", True).render("no outputs", True, (110, 114, 126))
                surface.blit(ts, ts.get_rect(center=screen.center))
            return
        screens, _k = self._rects(rect, self._out_boxes())
        pad = round(8 * k)
        for (key, screen), spec in zip(screens, self.ports_out):
            value = self.values.get(key)
            corner = max(2, round(6 * k))
            draw_rect(surface, shade(self.color, 0.07), screen, border_radius=corner)
            draw_rect(surface, (8, 8, 10), screen, width=max(1, s // 45), border_radius=corner)
            pygame.draw.circle(surface, self.color if value is not None else shade(self.color, 0.4),
                               (rect.right, screen.centery), max(2, round(5 * k)))
            lsize = round(13 * k)
            if lsize >= 7:
                lf = get_font(lsize, "consolas", True)
                ls = lf.render(clip_text(spec["label"], lf, screen.width - 2 * pad), True, shade(self.color, 0.75))
                surface.blit(ls, (screen.x + pad, screen.y + max(1, pad // 2)))
            text = format_value(value, spec.get("unit", ""))
            color = shade(self.color, 1.2) if value is not None else (110, 114, 126)
            vsize = round(screen.height * 0.5)
            while vsize > 7 and get_font(vsize, "consolas", True).size(text)[0] > screen.width - 2 * pad:
                vsize = max(7, int(vsize * 0.9))
            if vsize >= 7:
                vs = get_font(vsize, "consolas", True).render(text, True, color)
                surface.blit(vs, vs.get_rect(bottomright=(screen.right - pad, screen.bottom - max(1, pad // 2))))

    def _out_boxes_empty(self):
        """Where the (empty) screen goes when it has no outputs."""
        width, chip_w, area_h = self._measure()
        x = MC_PAD + (chip_w + 10 if self.ports_in else 0)
        return x, MC_PAD + MC_TITLE, width - MC_PAD - x, area_h
