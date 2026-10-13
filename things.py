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

import pygame

import instruments
from canvas import (OBJECT_TYPES, WARN_COLOR, Btn, Field, Gap, Prop, Row, Thing, clip_text,
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
            if halo.width < 2000:         # skip the halo when zoomed way in (huge surface)
                layer = pygame.Surface(halo.size, pygame.SRCALPHA)
                pygame.draw.ellipse(layer, (*self.color, int(70 * glow)), layer.get_rect())
                surface.blit(layer, halo.topleft)
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
        pygame.draw.rect(surface, (14, 14, 18), rect.move(0, max(1, s // 22)),
                         border_radius=radius)                      # drop shadow
        pygame.draw.rect(surface, INPUT_HOUSING, rect, border_radius=radius)
        pygame.draw.rect(surface, shade(INPUT_HOUSING, 1.35), rect,
                         width=max(1, s // 40), border_radius=radius)   # housing edge
        well = self.well_rect(rect)
        pygame.draw.rect(surface, INPUT_WELL, well, border_radius=max(2, radius - 2))
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
            pygame.draw.rect(surface, shade(self.color, 0.9), well.inflate(4, 4),
                             width=max(1, s // 30), border_radius=radius + 3)
        pygame.draw.rect(surface, shade(self.color, 0.5), side, border_radius=radius)   # cap's side
        pygame.draw.rect(surface, face_color, face, border_radius=radius)               # cap's top
        if not self.pressed:                                                            # gloss
            gloss = pygame.Rect(0, 0, round(face.width * 0.7), max(1, round(face.height * 0.16)))
            gloss.midtop = (face.centerx, face.y + max(1, round(face.height * 0.1)))
            pygame.draw.rect(surface, shade(face_color, 1.3), gloss, border_radius=max(1, gloss.height // 2))
        pygame.draw.rect(surface, shade(self.color, 0.4), face,
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
        pygame.draw.rect(surface, (40, 40, 48), track, border_radius=tr)
        if knob_at > 0:             # the color fills in behind the knob as it slides on
            fill = track.copy()
            fill.width = round(track.height + (track.width - track.height) * knob_at)
            pygame.draw.rect(surface, on_color if knob_at > 0.5 else shade(on_color, 0.6),
                             fill, border_radius=tr)
        pygame.draw.rect(surface, (12, 12, 15), track, width=max(1, h // 25), border_radius=tr)

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
        pygame.draw.rect(surface, (120, 120, 130), knob.move(0, depth), border_radius=kr)  # side
        pygame.draw.rect(surface, base, knob, border_radius=kr)
        shine = pygame.Rect(0, 0, round(knob.width * 0.6), max(1, round(knob.height * 0.18)))
        shine.midtop = (knob.centerx, knob.y + max(1, round(knob.height * 0.12)))
        pygame.draw.rect(surface, (255, 255, 255), shine, border_radius=max(1, shine.height // 2))
        pygame.draw.rect(surface, (90, 90, 100), knob, width=max(1, h // 30), border_radius=kr)


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
        pygame.draw.rect(surface, (6, 6, 8), slot, border_radius=sw // 2)
        level = pygame.Rect(slot.x, cap.centery, sw, slot.bottom - cap.centery)
        if level.height > 0:
            glow = shade(self.color, 1.1) if (self.is_hovered or self._dragging) else self.color
            pygame.draw.rect(surface, glow, level, border_radius=sw // 2)

        # The cap: raised, with a side, a top shine and a colored grip line.
        depth = max(1, round(cap_h * (0.06 if self._dragging else 0.16)))
        face = cap.move(0, -depth // 2)
        base = (222, 222, 230)
        if self._dragging:
            base = shade(base, 0.88)
        elif self.is_hovered:
            base = (242, 242, 250)
        radius = max(2, cap_h // 4)
        pygame.draw.rect(surface, (10, 10, 12), cap.move(0, depth + 1).inflate(2, 0),
                         border_radius=radius)                                         # shadow
        pygame.draw.rect(surface, (115, 115, 126), face.move(0, depth), border_radius=radius)  # side
        pygame.draw.rect(surface, base, face, border_radius=radius)
        shine = pygame.Rect(0, 0, round(face.width * 0.7), max(1, round(face.height * 0.16)))
        shine.midtop = (face.centerx, face.y + max(1, round(face.height * 0.12)))
        pygame.draw.rect(surface, (255, 255, 255), shine, border_radius=max(1, shine.height // 2))
        grip = pygame.Rect(0, 0, round(face.width * 0.78), max(1, round(face.height * 0.14)))
        grip.center = (face.centerx, face.centery + max(1, face.height // 10))
        pygame.draw.rect(surface, shade(self.color, 0.9), grip, border_radius=max(1, grip.height // 2))
        pygame.draw.rect(surface, (80, 80, 92), face, width=max(1, s // 40), border_radius=radius)

        # Readout: the value in a small dark display.
        corner = max(2, readout.height // 4)
        pygame.draw.rect(surface, (8, 9, 11), readout, border_radius=corner)
        pygame.draw.rect(surface, (45, 45, 54), readout, width=1, border_radius=corner)
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
            draw_wire(surface, camera, src.center, self.center, self.WIRE_COLOR)


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
            pygame.draw.rect(surface, color if name in lit else ghost, box, border_radius=r)
        dp = pygame.Rect(round(x + cw + gap * 0.5 - t / 2), round(y + ch - t), max(1, round(t)), max(1, round(t)))
        pygame.draw.rect(surface, color if dot else ghost, dp, border_radius=r)


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
        pygame.draw.rect(surface, (14, 14, 18), rect.move(0, max(1, s // 22)), border_radius=radius)
        pygame.draw.rect(surface, INPUT_HOUSING, rect, border_radius=radius)
        pygame.draw.rect(surface, shade(INPUT_HOUSING, 1.35), rect, width=max(1, s // 40),
                         border_radius=radius)
        if s < 16:                                      # too small for detail: just a lit screen
            pygame.draw.rect(surface, shade(self.color, 0.5), rect.inflate(-max(1, s // 3), -max(1, s // 3)))
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
        pygame.draw.rect(surface, glass, screen, border_radius=max(2, radius - 3))
        pygame.draw.rect(surface, (8, 8, 10), screen, width=max(1, s // 45),
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
            layer = pygame.Surface(shine.size, pygame.SRCALPHA)
            layer.fill((255, 255, 255, 14))
            surface.blit(layer, shine.topleft)


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

    _cache = {}             # (text, px, font, caps, color[, "glow"]) -> rendered surface

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

    @staticmethod
    def _remember(key, surf):
        if len(Label._cache) > 400:
            Label._cache.clear()
        Label._cache[key] = surf

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
            pygame.draw.rect(surface, shade(accent, 0.7), rect, border_radius=max(2, rect.height // 5))
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
            pygame.draw.rect(surface, (12, 12, 15), rect.move(0, max(1, h // 14)), border_radius=r)
            pygame.draw.rect(surface, (34, 35, 44), rect, border_radius=r)
            shine_y = rect.y + max(1, h // 14)
            pygame.draw.line(surface, (54, 56, 70), (rect.x + r, shine_y), (rect.right - r, shine_y),
                             max(1, h // 30))
            pygame.draw.rect(surface, shade(accent, 0.45), rect, width=line, border_radius=r)
            bar = pygame.Rect(rect.x + round(h * 0.22), rect.y + round(h * 0.2),
                              max(2, round(h * 0.1)), rect.height - 2 * round(h * 0.2))
            pygame.draw.rect(surface, accent, bar, border_radius=max(1, bar.width // 2))
            return light
        if st == "bevel":                           # a raised block, like a button cap
            depth = max(2, round(h * 0.16))
            face = rect.copy()
            face.height -= depth
            body = shade(accent, 0.42)
            pygame.draw.rect(surface, (10, 10, 12), rect.move(0, max(1, h // 14)), border_radius=r)
            pygame.draw.rect(surface, shade(accent, 0.2), rect, border_radius=r)          # side
            pygame.draw.rect(surface, body, face, border_radius=r)                        # top
            shine = pygame.Rect(face.x + r, face.y + max(1, h // 12), face.width - 2 * r, max(1, h // 12))
            pygame.draw.rect(surface, shade(body, 1.45), shine, border_radius=max(1, shine.height // 2))
            pygame.draw.rect(surface, shade(accent, 0.14), face, width=line, border_radius=r)
            return light
        if st == "inset":                           # sunk into a housing, like the inputs
            pygame.draw.rect(surface, (14, 14, 18), rect.move(0, max(1, h // 18)), border_radius=r)
            pygame.draw.rect(surface, INPUT_HOUSING, rect, border_radius=r)
            pygame.draw.rect(surface, shade(INPUT_HOUSING, 1.35), rect, width=line, border_radius=r)
            b = max(2, round(h * 0.14))
            well = rect.inflate(-2 * b, -2 * b)
            wr = max(2, r - b // 2)
            pygame.draw.rect(surface, (13, 13, 17), well, border_radius=wr)
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
            pygame.draw.rect(surface, shade(accent, 1.25), top_shine)
            return (22, 22, 28)
        if st == "neon":                            # a glowing tube
            pygame.draw.rect(surface, (13, 13, 17), rect, border_radius=r)
            glow = max(3, h // 6)
            layer = pygame.Surface((rect.width + 4 * glow, rect.height + 4 * glow), pygame.SRCALPHA)
            inner = layer.get_rect().inflate(-4 * glow, -4 * glow)
            for i in range(4, 0, -1):
                pygame.draw.rect(layer, (*accent, 22 + 10 * (4 - i)), inner.inflate(i * glow, i * glow),
                                 width=glow, border_radius=r + i * glow // 2)
            surface.blit(layer, (rect.x - 2 * glow, rect.y - 2 * glow))
            pygame.draw.rect(surface, accent, rect, width=max(2, h // 16), border_radius=r)
            pygame.draw.rect(surface, shade(accent, 1.6), rect, width=max(1, h // 40), border_radius=r)
            return shade(accent, 1.3)
        if st == "brackets":                        # HUD corner brackets
            arm, t = max(4, round(h * 0.38)), max(2, h // 14)
            for cx, cy, dx, dy in ((rect.left, rect.top, 1, 1), (rect.right, rect.top, -1, 1),
                                   (rect.left, rect.bottom, 1, -1), (rect.right, rect.bottom, -1, -1)):
                x0 = cx if dx > 0 else cx - t
                y0 = cy if dy > 0 else cy - t
                pygame.draw.rect(surface, accent, (x0 if dx > 0 else cx - arm, y0, arm, t))
                pygame.draw.rect(surface, accent, (x0, y0 if dy > 0 else cy - arm, t, arm))
            return light
        if st == "double":
            pygame.draw.rect(surface, (28, 29, 37), rect, border_radius=r)
            pygame.draw.rect(surface, accent, rect, width=max(1, h // 22), border_radius=r)
            gap = max(3, round(h * 0.1))
            pygame.draw.rect(surface, shade(accent, 0.75), rect.inflate(-2 * gap, -2 * gap),
                             width=max(1, h // 40), border_radius=max(2, r - gap))
            return light
        if st == "outline":
            pygame.draw.rect(surface, accent, rect, width=max(1, round(h * 0.06)), border_radius=r)
            return accent
        # underline
        rule = pygame.Rect(rect.x, rect.bottom - max(2, round(h * 0.09)), rect.width, max(2, round(h * 0.09)))
        pygame.draw.rect(surface, accent, rule, border_radius=max(1, rule.height // 2))
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
            steps = max(2, round(px * 0.13))
            for i in range(steps, 0, -1):
                tone = shade(accent, 0.35 + 0.45 * (steps - i) / steps)
                surface.blit(self._render_text(px, tone), pos.move(i, i))
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
# runs (by name, from devices.json): its params can be set by hand or linked
# to any object's output (a slider's value), and its readings are outputs that
# readouts (or anything else) can link to.
DEVICE_STATUS_COLORS = {"ok": (90, 220, 120), "starting": (240, 190, 60),
                        "error": (235, 80, 80), "missing": (235, 80, 80), "offline": (110, 110, 120)}
DEVICE_STATUS_TEXT = {"ok": "connected", "starting": "starting…", "error": "error",
                      "missing": "not on the server", "offline": "server offline"}


class Device(Thing):
    """One device on the instrument server, on the canvas. Shows its name, a
    status light, and a small screen with its param values (what it's set to)
    and readings (what it reports). Each param can be linked to a source —
    e.g. a slider — and then follows it live; readings are outputs, so a
    readout can show them. Its params and links are saved; the device itself
    keeps running (and holds its last values) when the canvas closes."""

    TYPE = "device"
    MENU_LABEL = "Add device"
    NAME = "Device"
    CATEGORY = "Devices"
    DRIVER = None                 # a subclass for one driver sets its module name (e.g. "fake_arduino")
    SIZE = (230, 132)
    DOT_COLOR = (90, 220, 120)
    PROPS = [Prop("scale", "Size", "float", 1.0, min=0.25, max=50, unit="×",
                  prompt="Size (1 = normal, 2 = twice as big):"),
             Prop("device", kind="str", default="", hidden=True),     # name on the server
             Prop("links", kind="data", default={}, hidden=True)]     # param -> {"id", "key"}
    WIRE_COLOR = (120, 220, 150)

    @classmethod
    def create(cls, app):
        def make(x, y):
            obj = cls(x, y)
            used = {o.device for o in app.scene.objects if isinstance(o, Device)}
            names = instruments.client.device_names()
            obj.device = next((n for n in names if n not in used), names[0] if names else "")
            return obj
        app.start_placing(make)

    # --- The server's view of this device ----------------------------------------
    def info(self):
        return instruments.client.device(self.device) if self.device else None

    def status(self):
        return instruments.client.status(self.device) if self.device else "missing"

    def outputs(self):
        info = self.info()
        if info is None:
            return {}
        out = {p: spec["value"] for p, spec in info["params"].items() if spec.get("value") is not None}
        out.update(info["values"])
        return out

    def tick(self, dt):
        """Push each linked param's source value to the server (the client
        skips repeats and caps the rate)."""
        if not self.device or self.scene is None:
            return
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
        rows = [Row("Device", self.device or "— (none)", on_click=self.next_device),
                Row("Status", DEVICE_STATUS_TEXT.get(status, status),
                    color=DEVICE_STATUS_COLORS.get(status))]
        info = self.info()
        if info is not None:
            rows.append(Row("Driver", info["driver"]))
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
                rows.append(Row(name, "—" if value is None else f"{value:g} {spec.get('unit', '')}"))
        return rows + [Gap(6)] + self.prop_rows()

    def connect(self, app):
        server = server_object(app.scene)
        if server is not None:
            server.connect_now(app)
        else:
            InstrumentServer.create(app)            # none yet: pick one up to place

    def remove_from_server(self, app):
        def remove():
            instruments.client.send({"op": "remove_device", "name": self.device})
            app.scene.delete(self)
        app.confirm(f"Remove '{self.device}'?", "From the server and the canvas.", remove)

    def buttons(self, app):
        out = []
        if not instruments.client.connected:
            label = ("Connect instrument server" if server_object(app.scene) is not None
                     else "Place instrument server")
            out.append(Btn(label, self.connect))
        if self.links:
            out.append(Btn("Unlink all", self.unlink_all))
        if instruments.client.connected and self.info() is not None:
            out.append(Btn("Remove from server", self.remove_from_server))
        return out

    # --- Drawing ----------------------------------------------------------------------
    def draw_links(self, surface, camera):
        if self.scene is None:
            return
        for link in self.links.values():
            src = self.scene.by_id.get(link.get("id"))
            if src is not None and src.center is not None:
                draw_wire(surface, camera, src.center, self.center, self.WIRE_COLOR)

    def draw_shape(self, surface, rect, camera):
        s = min(rect.width, rect.height)
        radius = max(2, s // 8)
        pygame.draw.rect(surface, (14, 14, 18), rect.move(0, max(1, s // 26)), border_radius=radius)
        pygame.draw.rect(surface, INPUT_HOUSING, rect, border_radius=radius)
        pygame.draw.rect(surface, shade(INPUT_HOUSING, 1.35), rect, width=max(1, s // 50), border_radius=radius)
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
        pygame.draw.rect(surface, (12, 14, 16), screen, border_radius=max(2, radius - 2))
        pygame.draw.rect(surface, (6, 6, 8), screen, width=1, border_radius=max(2, radius - 2))
        info = self.info()
        if info is None or status in ("offline", "missing"):
            lines = [(DEVICE_STATUS_TEXT.get(status, status), "", (130, 130, 142))]
        else:
            lines = []
            for p, spec in info["params"].items():
                v = spec.get("value")
                lines.append((f"{p} →", "—" if v is None else f"{v:.1f} {spec.get('unit', '')}", (255, 190, 90)))
            for r, spec in info["readings"].items():
                v = info["values"].get(r)
                lines.append((r, "—" if v is None else f"{v:.1f} {spec.get('unit', '')}", (120, 230, 150)))
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
        self._launched = False      # tried launching the server for this Connect?

    # --- Connection -------------------------------------------------------------
    def state(self):
        if not self.connect:
            return "off"
        return "connected" if instruments.client.connected else "connecting"

    def tick(self, dt):
        self._phase += dt
        client = instruments.client
        client.enabled = self.connect             # this object is the on/off switch
        if self.connect and not client.connected and not self._launched:
            self._launched = True
            if not instruments.server_running():
                instruments.start_server()

    def on_removed(self):
        instruments.client.enabled = False        # no server object: nothing connects

    def connect_now(self, app):
        self.connect, self._launched = True, False
        app.save()

    def disconnect(self, app):
        self.connect = False
        app.save()

    def stop_server(self, app):
        def stop():
            instruments.client.send({"op": "shutdown"})
            self.connect = False                  # after the shutdown goes out, stay off
            app.save()
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
            obj.device = name
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
        if not self.connect:
            return [Btn("Connect", self.connect_now, style="go")]
        out = [Btn("Disconnect", self.disconnect)]
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
            pygame.draw.rect(surface, INPUT_HOUSING, body, border_radius=radius)
            pygame.draw.circle(surface, light, body.center, max(1, s // 6))
            return

        # Antenna: a mast from the top-right of the body, a ball on top, and —
        # while connected — signal waves rippling out from the tip.
        ax = body.right - round(s * 0.17)
        tip = (ax, rect.y + round(s * 0.07))
        mast_w = max(2, s // 55)
        pygame.draw.rect(surface, (34, 34, 42), (ax - mast_w * 2, body.top - max(2, s // 40),
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
                pygame.draw.arc(surface, col, box, -0.75, 0.75, width)                 # right
                pygame.draw.arc(surface, col, box, math.pi - 0.75, math.pi + 0.75, width)   # left
        ball = max(2, s // 32)
        blink = state != "connecting" or (self._phase * 2) % 1 < 0.6
        tip_color = light if blink else shade(light, 0.4)
        if state == "connected":
            pygame.draw.circle(surface, shade(light, 0.35), tip, round(ball * 2))
        pygame.draw.circle(surface, tip_color, tip, ball)

        # Body: housing, a speaker grille, a display, and a name plate.
        pygame.draw.rect(surface, (14, 14, 18), body.move(0, max(1, s // 45)), border_radius=radius)
        pygame.draw.rect(surface, INPUT_HOUSING, body, border_radius=radius)
        pygame.draw.rect(surface, shade(INPUT_HOUSING, 1.35), body, width=max(1, s // 80), border_radius=radius)
        pad = max(2, round(s * 0.055))
        grille = pygame.Rect(body.x + pad, body.y + pad, round(body.width * 0.27), body.height - 2 * pad)
        pygame.draw.rect(surface, (30, 30, 37), grille, border_radius=max(2, radius - 2))
        hole = max(1, s // 90)
        step = max(3, s // 30)
        for gy in range(grille.y + step // 2 + 1, grille.bottom - step // 2, step):
            for gx in range(grille.x + step // 2 + 1, grille.right - step // 2, step):
                pygame.draw.circle(surface, (12, 12, 15), (gx, gy), hole)

        display = pygame.Rect(grille.right + pad, body.y + pad,
                              body.right - pad - (grille.right + pad), round(body.height * 0.48))
        pygame.draw.rect(surface, shade(light, 0.08), display, border_radius=max(2, radius - 3))
        pygame.draw.rect(surface, (8, 8, 10), display, width=1, border_radius=max(2, radius - 3))
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
