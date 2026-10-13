"""arduino_servo.py — a hobby servo on an Arduino, driven over a serial port.

The sketch on the board reads a line of text with an angle and moves the servo:
sending b"90\n" does exactly what typing 90 + Enter in the Arduino Serial
Monitor does. One-way: the board doesn't report anything back, so there are
no readings — the device's status (ok / error) shows whether the port is open.

devices.json options:
  port   "COM11", or "auto" to find the Arduino (only boards with Arduino's own
         USB IDs are matched, so other serial equipment is never touched)
  baud   9600 (must match Serial.begin(...) in the sketch)

Notes:
- Opening the port resets an Uno, and it ignores input while it boots, so
  open() waits BOOT_SECONDS and then re-sends the current angle.
- Only one program can hold a COM port: close the Arduino IDE's Serial
  Monitor first, or the device shows an "Access is denied" error (and the
  server keeps retrying, so it connects as soon as the port is free).
- A pulled USB cable shows up as an error on the next write; the server then
  retries every few seconds and reconnects when it's plugged back in.
"""

import time

import serial
from serial.tools import list_ports

from drivers.base import Driver, Param

ARDUINO_USB_VENDORS = {0x2341, 0x2A03}    # Arduino LLC / Arduino SRL (official boards)
BOOT_SECONDS = 2.0


def find_arduino():
    """The COM port of the first board with an Arduino USB vendor id, or None."""
    for p in list_ports.comports():
        if p.vid in ARDUINO_USB_VENDORS:
            return p.device
    return None


class ArduinoServo(Driver):
    params = {"angle": Param(0, 180, unit="deg", default=90)}
    readings = {}
    poll_hz = 5                      # nothing to read; just how often the worker wakes

    def open(self):
        port = self.options.get("port", "auto")
        if not port or port.lower() == "auto":
            port = find_arduino()
            if port is None:
                raise IOError("no Arduino found (is it plugged in?)")
        self.port = port
        # serial_for_url also takes "loop://" (a fake port that echoes back), for testing.
        self.serial = serial.serial_for_url(port, baudrate=int(self.options.get("baud", 9600)),
                                            timeout=1, write_timeout=1)
        if not port.startswith("loop://"):
            time.sleep(BOOT_SECONDS)     # the Uno resets when the port opens
            self.serial.reset_input_buffer()
        self._last_sent = None
        self.set_angle(self.values["angle"])
        print(f"  [arduino servo] opened {port}", flush=True)

    def set_angle(self, value):
        angle = int(round(value))
        if angle == self._last_sent:
            return                   # the servo's already there; don't flood the line
        self.serial.write(f"{angle}\n".encode())
        self._last_sent = angle

    def read(self):
        # Nothing to read, but notice an unplugged cable even when idle.
        if not self.serial.is_open:
            raise IOError("port closed")
        self.serial.in_waiting     # raises if the device has gone away
        return {}

    def close(self):
        s = getattr(self, "serial", None)
        if s is not None and s.is_open:
            s.close()


DRIVER = ArduinoServo
