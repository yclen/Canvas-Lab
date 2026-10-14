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

import math
import time

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


class PushButton(Input):
    """A momentary push button: an arcade-style square cap that lifts when you
    hover and sinks into its housing when you press it."""

    TYPE = "push_button"
    MENU_LABEL = "Add push button"
    NAME = "Push button"
    DOT_COLOR = (215, 60, 60)
    PROPS = [Prop("color", "Cap color", "color", (215, 60, 60))]

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

        if self.text:                                  # name printed on the cap, if it fits
            size = round(face.height * 0.32)
            if size >= 9:
                font = get_font(size)
                light = sum(face_color) > 450
                ts = font.render(self.text, True, (25, 25, 30) if light else (245, 245, 245))
                if ts.get_width() <= face.width - 6:
                    surface.blit(ts, ts.get_rect(center=face.center))


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
    def create(cls, app):
        def typed(text):
            app.start_placing(lambda x, y: cls(x, y, expression=text.strip() or "x"))
        app.ask("Equation  y =", typed, q_pastes=False)

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



# ---- Excitation band: a color picker over the longpass / shortpass filters ------
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
    """The excitation band (driver: filter_band) as a color picker: a spectrum
    strip over what the longpass + shortpass filters can reach. Click or drag
    on it to set the band's center; the width is set (or linked) in the panel.
    The part of the spectrum the filters are actually passing — read back from
    the stage positions — stays lit, the rest is dimmed, and an orange outline
    shows the requested band while the stages catch up. Home / Stop are buttons
    once it's connected (the stages must be homed before they'll move)."""

    TYPE = "filter_band"
    MENU_LABEL = "Add excitation band"
    NAME = "Excitation band"
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
            title = self.text or self.device or "band"
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
