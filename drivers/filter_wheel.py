"""filter_wheel.py — a Thorlabs FW102C / FW212C motorized filter wheel.

Wraps the lab driver, `hardware drivers/fw102c.py` (next to this project). A
6- or 12-position wheel on a USB virtual COM port; positions are 1-based, as on
the unit's own display.

devices.json options:
  port  "auto" picks a port only if its USB serial number starts with "TP"
        (Thorlabs' prefix on its FTDI bridge). It never probes other ports:
        the lab driver's search sends *idn? to every FTDI port, and here those
        include the Zaber chain and maybe the laser. Else a port like "COM10",
        or "sim://" for a pretend wheel.

params:   position — the slot to go to, 1 … slots. Any number is accepted and
          rounded to a slot, so it can follow a slider or a function. It starts
          at where the wheel already is: connecting never moves it.
readings: position (the slot the wheel reports), slots (6 or 12)

A move blocks for ~1 s per slot (~2.5 s across the wheel); the server applies
only the newest requested position, so a dragged slider can't queue moves.
Connecting turns the position sensors off while idle (the lab driver's default)
— they're LEDs inside the housing and leak light into the beam.

Which aperture the numbers refer to (A at 12 o'clock, B at 7 o'clock) is a
switch on the side of the unit that software can't read: if every filter is
off by a fixed number of slots, check it. The unit must be powered on before
USB is plugged in, or the port never appears.
"""

import os
import sys
import time

from drivers.base import Driver, Param, Reading

LAB_DRIVERS = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                            "..", "..", "hardware drivers"))
THORLABS_SERIAL_PREFIX = "TP"


def _lab_fw102c():
    if LAB_DRIVERS not in sys.path:
        sys.path.insert(0, LAB_DRIVERS)
    import fw102c
    return fw102c


def _wheel_class():
    """The lab FW102C, but clearing the line before it identifies the unit: the
    first command after the port opens comes back CMD_NOT_DEFINED (whatever
    the open left in the controller's buffer is glued to it), so the lab
    driver's *idn? never matches. A bare CR first takes that error instead."""
    fw102c = _lab_fw102c()

    class Wheel(fw102c.FW102C):
        def _identify(self, ser):
            time.sleep(0.2)
            ser.reset_input_buffer()
            ser.write(b"\r")
            ser.read_until(self.PROMPT)
            return super()._identify(ser)

    return Wheel


def find_port():
    """The COM port whose USB serial number is Thorlabs' ("TP…"), or None."""
    try:
        from serial.tools import list_ports
    except ImportError:
        return None
    return next((p.device for p in list_ports.comports()
                 if (p.serial_number or "").startswith(THORLABS_SERIAL_PREFIX)), None)


class FilterWheel(Driver):
    params = {"position": Param(1, 6, default=1)}     # the real count is set in open()
    readings = {"position": Reading(), "slots": Reading()}
    poll_hz = 2

    def open(self):
        port = str(self.options.get("port") or "auto")
        if port.startswith("sim://"):
            self.wheel = SimWheel()
        else:
            if port.lower() == "auto":
                port = find_port()
                if port is None:
                    raise IOError("set the filter wheel's COM port (no port with a Thorlabs "
                                  "\"TP\" serial number to pick automatically)")
            self.wheel = _wheel_class()(address=port)
            if not self.wheel.connect():
                raise IOError(f"no filter wheel answered on {port} (in use by another program?)")
        self.slots = self.wheel.get_position_count()
        self.params = {"position": Param(1, self.slots, default=1)}
        self.values["position"] = self.wheel.get_position()        # don't move it
        print(f"  [filter_wheel] {getattr(self.wheel, 'idn', '') or port}: {self.slots} slots, "
              f"at {self.values['position']}", flush=True)

    def set_position(self, value):
        slot = int(round(value))
        if slot != self.wheel.get_position():
            self.wheel.move_to(slot)

    def apply(self, name, value):
        """Round to a whole slot before it's remembered, so `position` reads 3, not 2.6."""
        return super().apply(name, round(float(value)))

    def read(self):
        return {"position": self.wheel.get_position(), "slots": self.slots}

    def close(self):
        wheel = getattr(self, "wheel", None)
        if wheel is not None:
            wheel.disconnect()


DRIVER = FilterWheel


class SimWheel:
    """A pretend 6-slot wheel: moves take ~0.8 s a slot, the shorter way round."""

    idn = "THORLABS FW102C/FW212C SIM"

    def __init__(self):
        self._position = 1

    def get_position_count(self):
        return 6

    def get_position(self):
        return self._position

    def move_to(self, position):
        steps = abs(position - self._position)
        time.sleep(0.8 * min(steps, 6 - steps))
        self._position = position
        return position

    def disconnect(self):
        pass
