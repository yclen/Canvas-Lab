"""superk_laser.py — the NKT SuperK (Fianium) supercontinuum laser.

Wraps the lab driver, `hardware drivers/superk.py` (next to this project),
which talks through NKT's DLL via nkt_tools. Safety rules carried over from
the Quantum Yield control GUI:
- emission is only ever changed by an explicit action (never a linked param,
  which the canvas re-sends after a reconnect);
- "emission_on" is refused while the interlock is open, with the reason;
- emission goes OFF when: the canvas closes or crashes (no clients left), the
  device is restarted / removed, a read fails, or the server stops (the lab
  driver's own disconnect turns it off).

devices.json options:
  port                    "auto" (nkt_tools scans the COM ports for the laser,
                          as the lab GUIs do) or e.g. "COM7"; "sim://" pretends.
  off_when_canvas_closes  true (default): emission off when no canvas is
                          connected any more.

params:   power_level — the laser's own setpoint, % of max (0.1 % steps);
          starts at the laser's current setting, so connecting changes nothing
readings: emission (1 / 0, or missing while the laser is between states —
          e.g. ramping up after "on"; normal, not an error), interlock_ok (1/0),
          interlock (text), status (text), port (text)
actions:  emission_on, emission_off, reset_interlock

The NKT DLL takes the port exclusively: close NKT's own Control Software first.
"""

import os
import sys
import time

from drivers.base import Driver, Param, Reading

LAB_DRIVERS = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                            "..", "..", "hardware drivers"))


def _lab_superk():
    if LAB_DRIVERS not in sys.path:
        sys.path.insert(0, LAB_DRIVERS)
    import superk
    return superk


class SuperKLaser(Driver):
    params = {"power_level": Param(0, 100, unit="%")}
    readings = {"emission": Reading(), "interlock_ok": Reading(), "interlock": Reading(),
                "status": Reading(), "port": Reading()}
    actions = ["emission_on", "emission_off", "reset_interlock"]
    poll_hz = 2          # emission changes when somebody does something; each read crosses the DLL

    def open(self):
        port = str(self.options.get("port") or "auto")
        if port.startswith("sim://"):
            self.laser = SimLaser()
            self.actions = SuperKLaser.actions + ["sim_open_interlock"]   # for testing refusals
        else:
            self.laser = _lab_superk().SuperK(address=None if port.lower() == "auto" else port)
            if not self.laser.connect():
                raise IOError("couldn't open the SuperK (is NKT's Control Software holding the port?)")
        self.values["power_level"] = round(self.laser.get_power_level(), 1)   # don't change it
        print(f"  [superk] connected @ {self.laser.address}", flush=True)

    def apply(self, name, value):
        if name == "power_level":
            value = round(float(value), 1)    # the laser's resolution: report what it really got
        return super().apply(name, value)

    def set_power_level(self, percent):
        self.laser.set_power_level(percent)

    def emission_on(self):
        code, description = self.laser.interlock()
        if code != 2:
            raise RuntimeError(f"interlock is open ({description}); close it, then Reset interlock")
        self.laser.on()

    def emission_off(self):
        self.laser.off()

    def reset_interlock(self):
        self.laser.reset_interlock()

    def sim_open_interlock(self):
        self.laser.sim_open_interlock()

    def on_no_clients(self):
        if self.options.get("off_when_canvas_closes", True):
            self.laser.off()
            print("  [superk] no canvas connected: emission off", flush=True)

    def emission(self):
        """1.0 / 0.0, or None while the laser is between states. Its emission
        register reads 0 (off) or 3 (on), and something else while it ramps up
        after "on"; nkt_tools raises for that, and the Quantum Yield GUI shows it
        as Unknown and carries on. So do we — failing the read would restart the
        device, and a restart turns emission off."""
        try:
            return 1.0 if self.laser.get_emission() else 0.0
        except RuntimeError as e:
            if "Unknown emission state" in str(e):
                return None
            raise

    def read(self):
        code, description = self.laser.interlock()
        issues = [s for s in self.laser.status() if s != "Emission on"]
        values = {"interlock_ok": 1.0 if code == 2 else 0.0,
                  "interlock": str(description),
                  "status": "; ".join(issues) if issues else "ok",
                  "port": str(self.laser.address)}
        emission = self.emission()
        if emission is not None:
            values["emission"] = emission
        return values

    def close(self):
        laser = getattr(self, "laser", None)
        if laser is not None:
            laser.disconnect()            # the lab driver turns emission off first


DRIVER = SuperKLaser


class SimLaser:
    """A pretend SuperK: emission, a power setpoint, and an interlock that
    starts closed. `sim_open_interlock()` opens it, for testing refusals."""

    def __init__(self):
        self.address = "sim://"
        self._emission, self._power, self._interlock = False, 40.0, 2

    def get_power_level(self):
        return self._power

    def set_power_level(self, percent):
        self._power = float(percent)

    def interlock(self):
        return self._interlock, {2: "OK", 1: "waiting for reset", 0: "interlock loop open"}[self._interlock]

    def reset_interlock(self):
        if self._interlock == 1:
            self._interlock = 2

    def sim_open_interlock(self):
        """Open the interlock (and so stop emission); it then waits for a reset."""
        self._interlock, self._emission = 1, False

    RAMP_SECONDS = 1.5          # like the real one: "between states" for a moment after on()

    def on(self):
        self._emission = True
        self._ramp_until = time.monotonic() + self.RAMP_SECONDS

    def off(self):
        self._emission = False
        self._ramp_until = 0.0

    def get_emission(self):
        if self._emission and time.monotonic() < getattr(self, "_ramp_until", 0.0):
            raise RuntimeError("Unknown emission state detected")   # what nkt_tools raises
        return self._emission

    def status(self):
        return ["Emission on"] if self._emission else []

    def disconnect(self):
        self._emission = False
