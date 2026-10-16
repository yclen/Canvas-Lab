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
  port                    "auto" or e.g. "COM7"; "sim://" pretends. "auto" tries,
                          in order: the port the laser was last found on, the
                          port with that USB adapter's serial number (if Windows
                          gave it a new COM number), and only then nkt_tools'
                          full scan, which opens every COM port (as the lab GUIs
                          do). Where it was found is kept in saves/superk_port.json.
  off_when_canvas_closes  true (default): emission off when no canvas is
                          connected any more.

params:   power_level — the laser's own setpoint, % of max (0.1 % steps);
          starts at the laser's current setting, so connecting changes nothing
readings: emission (1 / 0, or missing while the laser is between states —
          e.g. ramping up after "on"; normal, not an error), interlock_ok (1/0),
          interlock (text), status (text), port (text)
actions:  emission_on, emission_off, reset_interlock

The NKT DLL takes the port exclusively: close NKT's own Control Software first.
There's one laser and one DLL, so the server runs one SuperK device (as for
every driver; two would call the DLL from two threads) — every SuperK object
on the canvas shows that one — and opens
it before the other devices (`opens_first`), so a full scan can't grab a port
another device is opening.
"""

import json
import os
import sys
import time

from drivers.base import Driver, Param, Reading

HERE = os.path.dirname(os.path.abspath(__file__))
LAB_DRIVERS = os.path.normpath(os.path.join(HERE, "..", "..", "hardware drivers"))
LAST_PORT_FILE = os.path.normpath(os.path.join(HERE, "..", "saves", "superk_port.json"))


def _lab_superk():
    if LAB_DRIVERS not in sys.path:
        sys.path.insert(0, LAB_DRIVERS)
    import superk
    return superk


def _comports():
    """{port: USB serial number} for this PC's serial ports (empty without pyserial)."""
    try:
        from serial.tools import list_ports
    except ImportError:
        return {}
    return {p.device: p.serial_number for p in list_ports.comports()}


def load_last_port():
    """{"port", "serial"} where the laser was last found, or {}."""
    try:
        with open(LAST_PORT_FILE, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save_last_port(port):
    try:
        os.makedirs(os.path.dirname(LAST_PORT_FILE), exist_ok=True)
        with open(LAST_PORT_FILE, "w", encoding="utf-8") as f:
            json.dump({"port": port, "serial": _comports().get(port)}, f, indent=2)
    except OSError as e:
        print(f"  [superk] couldn't remember the port: {e}", flush=True)


def auto_candidates(last, ports):
    """The ports "auto" tries before a full scan: the last one, then the one
    with the last one's USB serial number. Only ports that exist right now."""
    out = []
    if last.get("port") in ports:
        out.append(last["port"])
    serial = last.get("serial")
    if serial:
        out += [p for p, s in ports.items() if s == serial and p not in out]
    return out


class SuperKLaser(Driver):
    params = {"power_level": Param(0, 100, unit="%")}
    readings = {"emission": Reading(), "interlock_ok": Reading(), "interlock": Reading(),
                "status": Reading(), "port": Reading()}
    actions = ["emission_on", "emission_off", "reset_interlock"]
    poll_hz = 2          # emission changes when somebody does something; each read crosses the DLL
    opens_first = True   # "auto" may scan every COM port

    def open(self):
        port = str(self.options.get("port") or "auto")
        if port.startswith("sim://"):
            self.laser = SimLaser()
            self.actions = SuperKLaser.actions + ["sim_open_interlock"]   # for testing refusals
        elif port.lower() == "auto":
            self.laser = self._find()
        else:
            self.laser = self._connect(port)
            if self.laser is None:
                raise IOError(f"couldn't open the SuperK on {port} (is it on? is NKT's "
                              f"Control Software holding the port? \"auto\" would look for it)")
        self.values["power_level"] = round(self.laser.get_power_level(), 1)   # don't change it
        print(f"  [superk] connected @ {self.laser.address}", flush=True)

    def _connect(self, address):
        """The lab driver's SuperK, connected on `address` (None: a full scan), or None."""
        laser = _lab_superk().SuperK(address=address)
        return laser if laser.connect() else None

    def _find(self):
        """port "auto": the remembered port first, a full scan only if that fails."""
        tried = auto_candidates(load_last_port(), _comports())
        for port in tried:
            laser = self._connect(port)
            if laser is not None:
                save_last_port(laser.address)
                return laser
        print(f"  [superk] not on {', '.join(tried) or 'a remembered port'}: scanning every COM port",
              flush=True)
        laser = self._connect(None)
        if laser is None:
            raise IOError("couldn't find the SuperK on any COM port (is it on and plugged in? "
                          "is NKT's Control Software holding the port?)")
        save_last_port(laser.address)
        return laser

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
