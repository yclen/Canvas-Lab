"""pm100d_meter.py — a Thorlabs PM100D optical power meter (read-only display).

Wraps the lab driver, `hardware drivers/pm100d.py` (next to this project), so
there's one PM100D driver to maintain. It talks VISA (USB), not a COM port.

devices.json options:
  address  "auto" (find the Thorlabs meter among the VISA resources) or a VISA
           resource, e.g. "USB0::0x1313::0x8078::P0012345::INSTR".
           "sim://" is a pretend meter, for trying things without hardware.

readings: power (W), power_mW (mW — handy for a readout)
params:   wavelength — the meter's correction wavelength (nm), clamped to the
          sensor's range. It starts at the meter's current setting, so
          connecting never changes the meter.

Only one program can hold the meter: close Thorlabs Optical Power Monitor
(or any script using it) first.
"""

import math
import os
import random
import sys
import time

from drivers.base import Driver, Param, Reading

LAB_DRIVERS = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                            "..", "..", "hardware drivers"))


def _lab_pm100d():
    if LAB_DRIVERS not in sys.path:
        sys.path.insert(0, LAB_DRIVERS)
    import pm100d
    return pm100d


class PM100DMeter(Driver):
    params = {"wavelength": Param(unit="nm")}       # the sensor's real range is set in open()
    readings = {"power": Reading(unit="W"), "power_mW": Reading(unit="mW")}
    poll_hz = 10

    def open(self):
        address = str(self.options.get("address") or "auto")
        if address.startswith("sim://"):
            self.meter = SimMeter()
        else:
            self.meter = _lab_pm100d().PM100D(address=None if address.lower() == "auto" else address)
            if not self.meter.connect():
                raise IOError(f"couldn't open the PM100D at {self.meter.address} (in use by another program?)")
        low, high = self.meter.correction_wavelength_range()
        self.params = {"wavelength": Param(low, high, unit="nm")}
        self.values["wavelength"] = self.meter.get_correction_wavelength()   # don't change the meter
        print(f"  [pm100d] {self.meter.idn.strip() if self.meter.idn else address} "
              f"wavelength {self.values['wavelength']:g} nm (range {low:g}-{high:g})", flush=True)

    def set_wavelength(self, nm):
        self.meter.set_correction_wavelength(nm)

    def read(self):
        watts = self.meter.power()
        return {"power": watts, "power_mW": watts * 1e3}

    def close(self):
        meter = getattr(self, "meter", None)
        if meter is not None:
            meter.disconnect()


DRIVER = PM100DMeter


class SimMeter:
    """A pretend PM100D: a few mW, drifting slowly, with a little noise."""

    def __init__(self):
        self.idn = "Thorlabs,PM100D,SIM0001,sim"
        self.address = "sim://"
        self._wavelength = 532.0

    def correction_wavelength_range(self):
        return (400.0, 1100.0)

    def get_correction_wavelength(self):
        return self._wavelength

    def set_correction_wavelength(self, nm):
        self._wavelength = float(nm)

    def power(self):
        t = time.monotonic()
        return 2.5e-3 * (1 + 0.3 * math.sin(t * 0.7)) * (1 + random.uniform(-0.01, 0.01))

    def disconnect(self):
        pass
