"""fake_arduino.py — a pretend Arduino with a servo and a light sensor.

No hardware needed: it behaves like the real thing would, so the whole chain
(canvas -> server -> driver -> readings back) can be built and tested first.
- angle (set):    where to send the servo, 0-180 deg
- position (read): where the servo actually is — it travels toward the angle
                   at a hobby servo's speed, so you can watch it catch up
- light (read):    a slowly drifting, slightly noisy light sensor

A real driver would do the same with a serial port: open() opens it,
set_angle writes e.g. b"120\n", and read() parses the line the sketch sends back.
"""

import math
import random
import time

from drivers.base import Driver, Param, Reading


class FakeArduino(Driver):
    params = {"angle": Param(0, 180, unit="deg", default=90)}
    readings = {"position": Reading(unit="deg"), "light": Reading(unit="lux")}
    poll_hz = 20
    SLEW = 120.0                  # deg/sec the pretend servo moves

    def open(self):
        self._position = float(self.values["angle"])
        self._target = self._position
        self._last = time.monotonic()
        print(f"  [fake arduino] pretending to open {self.options.get('port', 'COM?')}", flush=True)

    def set_angle(self, value):
        self._target = value      # real driver: self.serial.write(f"{round(value)}\n".encode())

    def read(self):
        now = time.monotonic()
        step = self.SLEW * (now - self._last)
        self._last = now
        self._position += max(-step, min(step, self._target - self._position))
        light = 400 + 150 * math.sin(now * 0.8) + random.uniform(-8, 8)
        return {"position": round(self._position, 2), "light": round(light, 1)}


DRIVER = FakeArduino
