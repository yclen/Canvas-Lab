"""flip_mirror.py — a New Focus motorized flip mirror on an NI 6363 output line.

Wraps the lab driver, `hardware drivers/flipper.py` (next to this project). Which
DAQ channel each mirror is on, and which optical path is "up" / "down", come
from `flipper.SETUP` — read at connect time, never copied here, so the mapping
lives in one place (as in the Quantum Yield control GUI).

The rules, from flipper.py:
- THERE IS NO READBACK: the position is the last one *commanded* — an
  assumption. The handpad's rocker switch overrides the TTL line silently.
- The handpad is EDGE-TRIGGERED: commanding the position it's believed to be
  in sends no edge and moves nothing.

So connecting here does NOT move the mirror (the output keeps its level; the
position starts unknown) — the server starts every device in devices.json when
it launches, and a mirror must never move just because something connected.
Then either:
  sync_up / sync_down   "it's at <path>" — you looked; nothing moves
  find_position         flips down, then up, to land somewhere known (MOVES)
and after that flip / the `up` param move it.

Closing the device (server stop, device restart or removal) parks the line at
0 V — a falling edge, so the mirror lowers. That's flipper.py's deliberate
known end state.

devices.json options:
  mirror   a name from flipper.SETUP: "excitation" or "detection"
  channel  "auto" (the channel flipper.SETUP gives that mirror), an explicit one
           like "ao2", or "sim://" to pretend

params:   up — 1 = the up path, 0 = the down path (needs a known position)
readings: position (the path's name, or "unknown"), is_up (1/0, absent while
          unknown), up_label, down_label (text), flips
actions:  flip, find_position, sync_up, sync_down
"""

import os
import sys
import time

from drivers.base import Driver, Param, Reading

LAB_DRIVERS = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                            "..", "..", "hardware drivers"))


def _lab_flipper():
    if LAB_DRIVERS not in sys.path:
        sys.path.insert(0, LAB_DRIVERS)
    import flipper
    return flipper


class FlipMirror(Driver):
    params = {"up": Param(0, 1)}
    readings = {"position": Reading(), "is_up": Reading(), "up_label": Reading(),
                "down_label": Reading(), "flips": Reading()}
    actions = ["flip", "find_position", "sync_up", "sync_down"]
    poll_hz = 2          # nothing is measured; this just reports the assumed position

    def open(self):
        name = str(self.options.get("mirror") or "detection")
        channel = str(self.options.get("channel") or "auto")
        if channel.startswith("sim://"):
            up, down = SIM_PATHS.get(name, ("up", "down"))
            self.mirror = SimFlipper(up, down, name)
        else:
            flipper = _lab_flipper()
            config = next((c for c in flipper.SETUP if c["name"] == name), None)
            if config is None:
                names = ", ".join(c["name"] for c in flipper.SETUP)
                raise ValueError(f"no mirror {name!r} in flipper.SETUP (there's {names})")
            self.mirror = flipper.Flipper(channel=config["channel"] if channel.lower() == "auto" else channel,
                                          up=config["up"], down=config["down"], name=name)
            if not self.mirror.connect(initialize=False):        # opens the line; does NOT move it
                raise IOError(f"the {name} mirror's output wouldn't open (held by another program?)")
        self.values["up"] = None                                  # unknown until synced / found
        print(f"  [flip mirror] {name}: up = {self.mirror.up_label}, down = {self.mirror.down_label}, "
              f"position unknown (sync it, or find its position)", flush=True)

    def _note_position(self):
        pos = self.mirror.position
        self.values["up"] = None if pos is None else (1.0 if pos == self.mirror.up_label else 0.0)

    def set_up(self, value):
        self.mirror.go(self.mirror.up_label if value >= 0.5 else self.mirror.down_label)

    def apply(self, name, value):
        applied = super().apply(name, value)
        self._note_position()
        return self.values["up"] if name == "up" else applied

    def flip(self):
        self.mirror.toggle()
        self._note_position()

    def find_position(self):
        self.mirror.connect(initialize=True)                      # down, then up: MOVES the mirror
        self._note_position()

    def sync_up(self):
        self.mirror.assume_position(self.mirror.up_label)
        self._note_position()

    def sync_down(self):
        self.mirror.assume_position(self.mirror.down_label)
        self._note_position()

    def read(self):
        pos = self.mirror.position
        values = {"position": pos or "unknown", "up_label": self.mirror.up_label,
                  "down_label": self.mirror.down_label, "flips": float(self.mirror.flips)}
        if pos is not None:
            values["is_up"] = 1.0 if pos == self.mirror.up_label else 0.0
        return values

    def close(self):
        mirror = getattr(self, "mirror", None)
        if mirror is not None:
            mirror.disconnect()                                   # parks at 0 V: the mirror lowers


DRIVER = FlipMirror

# The sim's path names — only for "sim://"; the real ones come from flipper.SETUP.
SIM_PATHS = {"excitation": ("SuperK", "CW"), "detection": ("PM100D", "sphere")}


class SimFlipper:
    """A pretend flipper with flipper.Flipper's methods: same rules (unknown
    until synced or found; go() to where it already is does nothing)."""

    SETTLE = 0.3

    def __init__(self, up, down, name):
        self.up_label, self.down_label, self.name = up, down, name
        self.position, self.flips = None, 0

    def connect(self, initialize=True):
        if initialize:
            self.flips += 2
            time.sleep(2 * self.SETTLE)
            self.position = self.up_label
        return True

    def assume_position(self, label):
        self.position = label

    def go(self, label, force=False):
        if self.position is None:
            raise RuntimeError(f"Flipper {self.name} does not know where it is. Sync it, or find its position.")
        if label != self.position or force:
            self.flips += 1
            time.sleep(self.SETTLE)
            self.position = label
        return label

    def toggle(self):
        if self.position is None:
            raise RuntimeError(f"Flipper {self.name} does not know where it is. Sync it, or find its position.")
        return self.go(self.down_label if self.position == self.up_label else self.up_label)

    def disconnect(self):
        if self.position is not None:
            self.position = self.down_label
