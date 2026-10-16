"""ccs200_spectrometer.py — the Thorlabs CCS200 spectrometer (200-1000 nm CCD).

Wraps the lab driver, `hardware drivers/ccs200.py` (next to this project), so
there's one CCS200 driver to maintain — its amplitude correction, wavelength
window and background handling are used as they are. It talks to Thorlabs'
TLCCS DLL (VISA), not a COM port.

devices.json options:
  address  "auto" (the first CCS-series spectrometer among the VISA resources)
           or a VISA resource, e.g. "USB0::0x1313::0x8089::M01068543::RAW".
           "sim://" is a pretend spectrometer, for trying things without hardware.
  correction_mode  "multiply" (default) or "divide": how amplitude correction
           applies the factory curve (the lab driver's correction_mode; its own
           default is "divide", which came out the wrong way round on the canvas).

params:   integration_ms (0.01 ms - 60 s), min_nm / max_nm (the wavelength
          window), correction (1 = amplitude correction on, 0 = off).
          Changing the integration time or the correction clears the
          background — a dark taken under other settings doesn't fit.
readings: spectrum (kind "data": the whole curve, for the canvas object to
          draw — see SPECTRUM below), peak_nm, peak, total, saturated (1/0),
          live (1/0), background (1/0: a dark is being subtracted), points,
          saved (text: the last file written).
actions:  go_live, stop, single (one scan), take_background (block the light
          first), clear_background, save (the last spectrum, full precision,
          as a CSV in saves/data/)

Nothing is scanned until go_live (or single): connecting only opens it. While
live, each poll takes one scan (so a long integration time is also how long
Stop takes to land). When the last client goes away it stops scanning.

SPECTRUM — what goes to the canvas. The world sends every player all device
data whenever it changes, so a scan is packed small: the intensities as 16-bit
integers (base64; `scale` turns them back into the driver's units — 1/32767 of
the scan's largest value, finer than any screen), and the wavelength axis as a
polynomial over the point index (the CCD's own calibration is one), not 2,800
numbers. About 8 KB a scan at full resolution; the object decides how much of it
to draw for its size on screen. {"seq", "n", "axis": [poly coeffs, highest
power first, of t = i/(n-1)], "lo", "hi" (nm at the ends), "scale", "y"}.

Only one program can hold the spectrometer: close ThorSpectra (or any script
using it) first.
"""

import base64
import csv
import math
import os
import sys
import time
from datetime import datetime

import numpy as np

from drivers.base import Driver, Param, Reading

HERE = os.path.dirname(os.path.abspath(__file__))
LAB_DRIVERS = os.path.normpath(os.path.join(HERE, "..", "..", "hardware drivers"))
DATA_DIR = os.path.normpath(os.path.join(HERE, "..", "saves", "data"))
SATURATED = 0.98          # raw (uncorrected) level that counts as a full well
AXIS_FIT_NM = 0.01        # the axis polynomial must match the real wavelengths this closely


def _lab_ccs200():
    if LAB_DRIVERS not in sys.path:
        sys.path.insert(0, LAB_DRIVERS)
    import ccs200
    return ccs200


def axis_poly(wavelengths):
    """Polynomial coefficients (highest power first) of nm over t = i/(n-1),
    the lowest degree that matches every point within AXIS_FIT_NM."""
    n = len(wavelengths)
    t = np.linspace(0.0, 1.0, n) if n > 1 else np.zeros(1)
    for degree in range(1, 8):
        if degree >= n:
            break
        coeffs = np.polyfit(t, wavelengths, degree)
        if np.max(np.abs(np.polyval(coeffs, t) - wavelengths)) <= AXIS_FIT_NM:
            return [float(c) for c in coeffs]
    return [float(c) for c in np.polyfit(t, wavelengths, min(7, max(1, n - 1)))]


def pack(values):
    """(scale, base64 text) of 16-bit integers: values ≈ ints * scale."""
    scale = max(float(np.max(np.abs(values))) if len(values) else 0.0, 1e-30) / 32767.0
    ints = np.clip(np.round(np.asarray(values) / scale), -32767, 32767).astype("<i2")
    return scale, base64.b64encode(ints.tobytes()).decode("ascii")


class CCS200Spectrometer(Driver):
    params = {"integration_ms": Param(0.01, 60000, unit="ms", default=100.0),
              "min_nm": Param(195, 1017, unit="nm", default=360.0),       # the CCD's real span: open()
              "max_nm": Param(195, 1017, unit="nm", default=1000.0),
              "correction": Param(0, 1, default=1.0)}
    readings = {"spectrum": Reading(kind="data"), "peak_nm": Reading(unit="nm"), "peak": Reading(),
                "total": Reading(), "saturated": Reading(), "live": Reading(), "background": Reading(),
                "points": Reading(), "saved": Reading(kind="text")}
    actions = ["go_live", "stop", "single", "take_background", "clear_background", "save"]
    poll_hz = 5          # at most this many scans a second go to the canvas

    def open(self):
        address = str(self.options.get("address") or "auto")
        mode = str(self.options.get("correction_mode") or "multiply")
        if address.startswith("sim://"):
            self.spec = SimCCS(mode)
        else:
            ccs200 = _lab_ccs200()
            self.spec = ccs200.CCS200(address=None if address.lower() == "auto" else address,
                                      integration_time=self.values["integration_ms"] / 1e3,
                                      amplitude_correction=self.values["correction"] >= 0.5,
                                      wavelength_range=(self.values["min_nm"], self.values["max_nm"]),
                                      correction_mode=mode)
            if not self.spec.connect():
                raise IOError(f"couldn't open the spectrometer at {self.spec.address} "
                              f"(is ThorSpectra or a script holding it?)")
        full = self.spec.full_wavelengths
        low, high = math.floor(float(full[0])), math.ceil(float(full[-1]))
        self.params = {"integration_ms": Param(0.01, 60000, unit="ms", default=100.0),
                       "min_nm": Param(low, high, unit="nm", default=360.0),
                       "max_nm": Param(low, high, unit="nm", default=1000.0),
                       "correction": Param(0, 1, default=1.0)}
        lo_nm, hi_nm = self.spec.wavelength_range
        self.values.update(integration_ms=round(self.spec.get_integration_time() * 1e3, 4),
                           min_nm=lo_nm, max_nm=hi_nm,
                           correction=1.0 if self.spec.amplitude_correction else 0.0)
        self.live, self._single, self._seq = False, False, 0
        self._last = None                    # (wavelengths, intensities) of the last scan
        self._saved = ""
        self._window_changed()
        self._readings = self._status_readings()
        print(f"  [ccs200] {self.spec.idn or address}: {self.values['min_nm']:g}-{self.values['max_nm']:g} nm, "
              f"{self.values['integration_ms']:g} ms, correction "
              f"{'on (' + mode + ')' if self.values['correction'] else 'off'}", flush=True)

    def _window_changed(self):
        """The window (or the correction) moved: new axis, new multipliers."""
        self._wl = np.asarray(self.spec.wavelengths, dtype=float)
        self._axis = axis_poly(self._wl)
        self._mult = (np.asarray(self.spec.amplitude_multipliers, dtype=float)
                      if self.spec.amplitude_correction else None)
        self._last = None

    # --- Settings ---------------------------------------------------------------------
    def set_integration_ms(self, ms):
        if ms != self.values.get("integration_ms"):
            self.spec.set_integration_time(ms / 1e3)
            self._clear_dark("integration time changed")

    def set_min_nm(self, nm):
        if nm >= self.values["max_nm"] - 1:
            raise ValueError(f"the window must start below its end ({self.values['max_nm']:g} nm)")
        self.spec.set_wavelength_range(nm, self.values["max_nm"])
        self._window_changed()

    def set_max_nm(self, nm):
        if nm <= self.values["min_nm"] + 1:
            raise ValueError(f"the window must end above its start ({self.values['min_nm']:g} nm)")
        self.spec.set_wavelength_range(self.values["min_nm"], nm)
        self._window_changed()

    def set_correction(self, value):
        on = value >= 0.5
        if on != self.spec.amplitude_correction:
            self.spec.amplitude_correction = on
            self._window_changed()
            self._clear_dark("amplitude correction changed")

    def apply(self, name, value):
        if name == "correction":
            value = 1.0 if float(value) >= 0.5 else 0.0
        applied = super().apply(name, value)
        if name == "integration_ms":           # the device quantises it: report what it uses
            self.values[name] = round(self.spec.get_integration_time() * 1e3, 4)
            applied = self.values[name]
        self._readings = self._status_readings(self._readings)
        return applied

    def _clear_dark(self, why):
        if self.spec.background is not None:
            self.spec.clear_background()
            print(f"  [ccs200] background cleared ({why}): take a new one", flush=True)

    # --- Actions ----------------------------------------------------------------------
    def go_live(self):
        self.live = True

    def stop(self):
        self.live = False
        self._readings = self._status_readings(self._readings)

    def single(self):
        self._single = True

    def take_background(self):
        self.spec.acquire_background(scans=10)
        self._readings = self._status_readings(self._readings)

    def clear_background(self):
        self.spec.clear_background()
        self._readings = self._status_readings(self._readings)

    def save(self):
        if self._last is None:
            raise RuntimeError("nothing to save yet — take a scan (Live or Single) first")
        os.makedirs(DATA_DIR, exist_ok=True)
        path = os.path.join(DATA_DIR, f"spectrum {datetime.now():%Y-%m-%d %H-%M-%S}.csv")
        wl, y = self._last
        with open(path, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow([f"# {self.spec.idn or 'CCS200'}"])
            writer.writerow([f"# integration {self.values['integration_ms']:g} ms, amplitude correction "
                             f"{'on (' + self.spec.correction_mode + ')' if self.spec.amplitude_correction else 'off'}"
                             f", background "
                             f"{'subtracted' if self.spec.background is not None else 'none'}"])
            writer.writerow(["wavelength_nm", "intensity"])
            writer.writerows(zip((repr(float(w)) for w in wl), (repr(float(v)) for v in y)))
        self._saved = os.path.basename(path)
        print(f"  [ccs200] saved {path}", flush=True)
        self._readings = self._status_readings(self._readings)

    def on_no_clients(self):
        self.live = False                     # nobody's watching: stop scanning

    # --- Scanning ---------------------------------------------------------------------
    def read(self):
        if self.live or self._single:
            self._single = False
            self._scan()
        return self._readings

    def _scan(self):
        y = np.asarray(self.spec.get_spectrum(), dtype=float)
        if len(y) != len(self._wl):          # the window moved under us (shouldn't): catch up
            self._window_changed()
        raw = y
        if self.spec.background is not None:
            raw = raw + self.spec.background
        if self._mult is not None:
            raw = raw / self._mult
        i = int(np.argmax(y)) if len(y) else 0
        self._seq += 1
        scale, packed = pack(y)
        spectrum = {"seq": self._seq, "n": len(y), "axis": self._axis,
                    "lo": float(self._wl[0]), "hi": float(self._wl[-1]), "scale": scale, "y": packed}
        self._last = (self._wl, y)
        self._readings = self._status_readings(
            {"spectrum": spectrum, "peak_nm": round(float(self._wl[i]), 2), "peak": float(y[i]),
             "total": float(np.sum(y)),
             "saturated": 1.0 if len(raw) and float(np.max(raw)) >= SATURATED else 0.0})

    def _status_readings(self, readings=None):
        out = dict(readings or {})
        out.update(live=1.0 if self.live else 0.0,
                   background=0.0 if self.spec.background is None else 1.0,
                   points=float(len(self._wl)))
        if self._saved:                       # text: shown in the object's panel
            out["saved"] = self._saved
        return out

    def close(self):
        spec = getattr(self, "spec", None)
        if spec is not None:
            spec.disconnect()


DRIVER = CCS200Spectrometer


class SimCCS:
    """A pretend CCS200, shaped like the lab driver: a lamp continuum, a narrow
    532 nm laser line and a fluorescence band that breathes, with noise;
    raw counts saturate at 1.0 and scale with the integration time."""

    def __init__(self, correction_mode="multiply"):
        self.idn = "Thorlabs CCS200 s/n SIM0001 (simulated)"
        self.correction_mode = correction_mode
        self.address = "sim://"
        px = np.arange(3648)
        self.full_wavelengths = 195.67 + 0.2400 * px - 4.1e-6 * px ** 2      # ≈ 195.7-1016 nm
        self._range = (360.0, 1000.0)
        self._integration = 0.1
        self.amplitude_correction = True
        self._curve = 0.05 + 0.71 * np.exp(-((self.full_wavelengths - 420.0) / 260.0) ** 2)
        self._background = None
        self._window()

    def _window(self):
        inside = np.where((self.full_wavelengths >= self._range[0]) &
                          (self.full_wavelengths <= self._range[1]))[0]
        self._lo, self._hi = int(inside[0]), int(inside[-1])
        self._background = None

    @property
    def wavelength_range(self):
        return self._range

    @property
    def wavelengths(self):
        return self.full_wavelengths[self._lo:self._hi + 1].copy()

    @property
    def amplitude_multipliers(self):
        curve = self._curve[self._lo:self._hi + 1]
        return curve if self.correction_mode == "multiply" else 1.0 / curve

    @property
    def background(self):
        return None if self._background is None else self._background.copy()

    def set_wavelength_range(self, lo, hi):
        self._range = (float(lo), float(hi))
        self._window()

    def set_integration_time(self, seconds):
        self._integration = float(seconds)

    def get_integration_time(self):
        return self._integration

    def _raw(self):
        wl = self.full_wavelengths
        breathe = 0.6 + 0.4 * math.sin(time.monotonic() * 0.8)
        light = (0.25 * np.exp(-((wl - 640.0) / 150.0) ** 2)                 # lamp
                 + 0.6 * np.exp(-((wl - 532.0) / 0.6) ** 2)                  # laser line
                 + 0.35 * breathe * np.exp(-((wl - 610.0) / 25.0) ** 2))     # fluorescence
        time.sleep(min(self._integration, 2.0))                             # the exposure
        raw = light * self._integration / 0.1 + 0.004 + np.random.normal(0, 0.002, wl.size)
        return np.minimum(raw, 1.0)

    def get_spectrum(self, scans=1, subtract_background=True):
        data = self._raw()
        data = data[self._lo:self._hi + 1]
        if self.amplitude_correction:
            data = data * self.amplitude_multipliers
        if subtract_background and self._background is not None:
            data = data - self._background
        return data

    def acquire_background(self, scans=10):
        self._background = None
        self._background = np.full(self._hi - self._lo + 1, 0.004) * (
            self.amplitude_multipliers if self.amplitude_correction else 1.0)

    def clear_background(self):
        self._background = None

    def disconnect(self):
        pass
