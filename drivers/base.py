"""base.py — what every driver is built from.

A driver describes its device in two lists and implements a few methods:

    class Servo(Driver):
        params = {"angle": Param(0, 180, unit="deg", default=90)}     # things you SET
        readings = {"position": Reading(unit="deg")}                  # things you READ
        poll_hz = 20                                                  # read() calls per second

        def open(self):                 # connect (e.g. open the serial port)
            ...
        def set_angle(self, value):     # one set_<param> per param; value is already clamped
            ...
        def read(self):                 # -> {"position": 87.3}
            ...
        def close(self):                # disconnect
            ...

The server calls these from one worker thread per device, so a driver never
needs locks of its own. It clamps every value to the Param's min/max before
calling set_<name>, so out-of-range values never reach the hardware.
"""


class Param:
    """Something you set on a device: a servo angle, an LED brightness."""

    def __init__(self, min=None, max=None, unit="", default=0.0):
        self.min, self.max, self.unit, self.default = min, max, unit, default

    def clamp(self, value):
        if self.min is not None:
            value = max(self.min, value)
        if self.max is not None:
            value = min(self.max, value)
        return value

    def describe(self):
        return {"min": self.min, "max": self.max, "unit": self.unit}


class Reading:
    """Something a device reports back: a sensor value, an actual position."""

    def __init__(self, unit=""):
        self.unit = unit

    def describe(self):
        return {"unit": self.unit}


class Driver:
    params = {}       # name -> Param
    readings = {}     # name -> Reading
    poll_hz = 20      # how often the server calls read()

    def __init__(self, **options):
        self.options = options                          # from devices.json
        self.values = {name: p.default for name, p in self.params.items()}

    def open(self):
        pass

    def close(self):
        pass

    def read(self):
        return {}

    def apply(self, name, value):
        """Called by the server: clamp, hand to set_<name>, remember. Returns
        the value actually applied."""
        value = self.params[name].clamp(float(value))
        getattr(self, "set_" + name)(value)
        self.values[name] = value
        return value
