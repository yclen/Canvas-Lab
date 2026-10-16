"""base.py — what every driver is built from.

A driver describes its device in two lists and implements a few methods:

    class Servo(Driver):
        params = {"angle": Param(0, 180, unit="deg", default=90)}     # things you SET
        readings = {"position": Reading(unit="deg")}                  # things you READ
        actions = ["home"]                                            # buttons: methods to call
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
calling set_<name>, so out-of-range values never reach the hardware. If
set_<name> or an action raises, the error is shown as the device's message
and the device keeps running (only open() / read() failures restart it).
A driver can replace `params` / `readings` per instance in open() — e.g. a
stage whose travel range is only known once it's connected.

How many devices a driver runs is the driver's call, not the canvas's: one
(the default), or one per value of its `device_key` option. Canvas objects
never make devices of their own — each finds the one for its driver.
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
    actions = []      # method names the canvas shows as buttons (e.g. "home", "stop")
    poll_hz = 20      # how often the server calls read()
    one_device = True     # a driver drives one piece of hardware: the server runs at most one
                          # device with it, and every canvas object for it shows that device …
    device_key = None     # … or, naming an option here, one device per value of that option
                          # (flip_mirror: "mirror" — one device per mirror)
    opens_first = False   # True: open before the other devices at startup (it may scan the
                          # COM ports, which would grab ports the others are opening)

    def __init__(self, **options):
        self.options = options                          # from devices.json
        self.values = {name: p.default for name, p in self.params.items()}

    @classmethod
    def device_key_value(cls, options):
        """Which piece of hardware a device with these options drives, by its
        device_key option (as text; a driver fills in its own default)."""
        return str(options.get(cls.device_key)) if cls.device_key else ""

    def open(self):
        pass

    def close(self):
        pass

    def read(self):
        return {}

    # Optional: on_no_clients(self) — called when the last client (the canvas)
    # disconnects or crashes; e.g. a laser turns its emission off. Readings may
    # also be text (shown as-is, never linked: only numbers are outputs).

    def apply(self, name, value):
        """Called by the server: clamp, hand to set_<name>, remember. Returns
        the value actually applied."""
        value = self.params[name].clamp(float(value))
        getattr(self, "set_" + name)(value)
        self.values[name] = value
        return value
