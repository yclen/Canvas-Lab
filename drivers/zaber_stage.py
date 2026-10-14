"""zaber_stage.py — one stage of a Zaber chain (a linear stage), in mm.

Wraps the lab driver, `hardware drivers/zaber.py` (next to this project), so
there's one Zaber driver to maintain. A chain of stages shares one serial
port, and only one program can hold it, so every stage on a port shares one
chain connection here: the first stage to open it connects, the last to close
it disconnects.

devices.json options:
  port   the chain's COM port, e.g. "COM3". "auto" picks a port only if it has
         Zaber's own USB id (0x2939) — it never tries other ports, since an
         FTDI port on this PC may be something else (e.g. the laser).
         "sim://" is a pretend 3-stage chain, for trying things without hardware.
  stage  the stage's position in the chain, 0 = the first device
  baud   115200 (Zaber ASCII default)

params:   target   — where to move (mm), clamped to the stage's travel range
readings: position — where it is (mm), moving (1/0), homed (1/0)
actions:  home, stop

Moves don't wait to finish, so a dragged slider keeps re-targeting the stage.
The target starts at the stage's current position — connecting never moves it.
A refused move (e.g. not homed yet) shows as the device's message.
"""

import os
import sys
import threading
import time

from drivers.base import Driver, Param, Reading

LAB_DRIVERS = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                            "..", "..", "hardware drivers"))
ZABER_USB_VENDOR = 0x2939

# Open chains: port -> [chain, number of stages using it]. The server re-imports
# drivers to pick up edits, so these must survive a reload (reload re-runs this
# file in the same module namespace) — a fresh {} would forget a port that's
# still open, and the next stage on it would fail with "port in use".
_chains = globals().get("_chains", {})
_chains_lock = globals().get("_chains_lock") or threading.Lock()


def _lab_zaber():
    if LAB_DRIVERS not in sys.path:
        sys.path.insert(0, LAB_DRIVERS)
    import zaber
    return zaber


def _find_zaber_port():
    """A port with Zaber's own USB id, or None. Never guesses beyond that."""
    try:
        from serial.tools import list_ports
    except ImportError:
        return None
    return next((p.device for p in list_ports.comports() if p.vid == ZABER_USB_VENDOR), None)


def _open_chain(port, baud):
    with _chains_lock:
        if port in _chains:
            _chains[port][1] += 1
            return _chains[port][0]
        if port.startswith("sim://"):
            chain = SimChain(port)
        else:
            chain = _lab_zaber().Zaber(address=port, baud_rate=baud)
            if not chain.connect():
                raise IOError(f"no Zaber chain answered on {port} (wrong port, or in use?)")
        _chains[port] = [chain, 1]
        return chain


def _release_chain(port):
    with _chains_lock:
        entry = _chains.get(port)
        if entry is None:
            return
        entry[1] -= 1
        if entry[1] <= 0:
            del _chains[port]
            try:
                entry[0].disconnect()
            except Exception:
                pass


class ZaberStage(Driver):
    params = {"target": Param(unit="mm")}            # the real range is set in open()
    readings = {"position": Reading(unit="mm"), "moving": Reading(), "homed": Reading()}
    actions = ["home", "stop"]
    poll_hz = 10

    def open(self):
        port = str(self.options.get("port") or "auto")
        if port.lower() == "auto":
            port = _find_zaber_port()
            if port is None:
                raise IOError("set the Zaber's COM port (no port with a Zaber USB id to pick automatically)")
        self.port = None
        chain = _open_chain(port, int(self.options.get("baud", 115200)))
        self.port = port
        self.stage = chain.stage(int(self.options.get("stage", 0)))
        low, high = self.stage.travel_range()
        self.params = {"target": Param(low, high, unit="mm")}   # this stage's own range
        self.values["target"] = round(self.stage.get_position(), 4)   # connecting never moves it
        print(f"  [zaber] {port} stage {self.stage.index}: {self.stage.info()['name']} "
              f"travel {low:.1f}-{high:.1f} mm", flush=True)

    def set_target(self, mm):
        self.stage.move_to(mm, wait=False)

    def read(self):
        return {"position": round(self.stage.get_position(), 4),
                "moving": 1.0 if self.stage.is_busy() else 0.0,
                "homed": 1.0 if self.stage.is_homed() else 0.0}

    def home(self):
        self.stage.home(wait=False)

    def stop(self):
        self.stage.stop()

    def close(self):
        if getattr(self, "port", None):
            _release_chain(self.port)
            self.port = None


DRIVER = ZaberStage


# ---- A pretend chain (port "sim://"), shaped like the lab driver's Zaber ----
_sim_ports_open = globals().get("_sim_ports_open", set())   # like real ports: one opener at a time


class SimChain:
    """Three pretend stages (0-50, 0-101.6, 0-101.6 mm) that move at 20 mm/s
    and start un-homed; same methods as zaber.Zaber's stage handles. Like a
    real port, a sim:// port can only be open once at a time."""

    SPEED = 20.0

    def __init__(self, port="sim://"):
        if port in _sim_ports_open:
            raise IOError(f"could not open port {port!r}: already in use")
        _sim_ports_open.add(port)
        self.port = port
        self.stages = [_SimStage(i, travel) for i, travel in enumerate((50.0, 101.6, 101.6))]

    def stage(self, index):
        if not 0 <= index < len(self.stages):
            raise IndexError(f"Stage index {index} out of range; chain has {len(self.stages)} device(s)")
        return self.stages[index]

    def disconnect(self):
        _sim_ports_open.discard(self.port)


class _SimStage:
    def __init__(self, index, travel):
        self.index, self.travel = index, travel
        self._pos, self._goal, self._last = travel / 3, travel / 3, time.monotonic()
        self._homed = False

    def _update(self):
        now = time.monotonic()
        step = SimChain.SPEED * (now - self._last)
        self._last = now
        self._pos += max(-step, min(step, self._goal - self._pos))

    def info(self):
        return {"index": self.index, "name": f"Sim stage {self.index}"}

    def travel_range(self):
        return (0.0, self.travel)

    def get_position(self):
        self._update()
        return self._pos

    def is_busy(self):
        self._update()
        return abs(self._goal - self._pos) > 1e-6

    def is_homed(self):
        return self._homed

    def home(self, wait=True):
        self._update()
        self._goal, self._homed = 0.0, True

    def move_to(self, mm, wait=True):
        if not self._homed:
            raise RuntimeError("stage isn't homed — press Home first")
        self._update()
        self._goal = float(mm)

    def stop(self):
        self._update()
        self._goal = self._pos
