"""filter_band.py — the excitation band, in nm: longpass + shortpass Zaber stages.

Two linear variable filters on Zaber stages cut the SuperK down to a band:

    longpass  -- passes wavelengths ABOVE its cutoff  -> the band's blue edge
    shortpass -- passes wavelengths BELOW its cutoff  -> the band's red edge

This driver wraps the Zaber chain (sharing zaber_stage.py's chain connection,
so it and Zaber stage devices on the same port coexist) and does the
wavelength <-> position conversion, so you set a band (center + width, nm)
instead of two positions (mm).

The calibration is read from `calibration/excitation_filters.json` each time
the device opens — the points logged with `scripts/calibrate_excitation_filters.py`
(position mm, cutoff nm, per filter) — with a least-squares line through each
filter's points (the QY GUI's "linear" mode). Without that file (or with under
2 points for a filter) it falls back to CALIBRATION below, the points from the
Quantum Yield GUI. A linear variable filter really is linear, so the line is
extrapolated past the points; the stage's travel, and the SPAN the QY GUI
works in (400-800 nm), limit the band. The file also says which stage is which
filter (serial, or chain index for sim).

devices.json options:
  port              the chain's COM port ("COM3"), "auto" (Zaber's USB id only),
                    or "sim://" (a pretend chain: longpass = stage 2, shortpass = 1)
  longpass_serial   the longpass stage's serial number (default: the calibration
  shortpass_serial  file's, else 137126 / 137120). Stages are found by serial, since
                    chain order changes when devices are re-plugged
  longpass_stage    chain index, used only when a serial is 0 / not given (sim)
  shortpass_stage
  baud              115200

params:   center (nm), width (nm). A request the filters can't reach is pulled
          in to the nearest band they can: setting center keeps the width,
          setting width keeps the center.
readings: actual_center, actual_width (from where the stages are),
          longpass, shortpass (each filter's cutoff, nm), longpass_mm,
          shortpass_mm, moving (1/0), homed (1/0, both stages)
actions:  home, stop (both stages)

Connecting never moves the stages: center / width start at the band they're
already making.
"""

import json
import os
from datetime import datetime

from drivers import zaber_stage
from drivers.base import Driver, Param, Reading

CALIBRATION_FILE = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                                "..", "calibration", "excitation_filters.json"))
ROLES = ("longpass", "shortpass")

# The fallback when there's no calibration file: (position mm, cutoff nm), copied
# from Quantum Yield Experiment/filter_calibration.json (measured 2026-09-14).
CALIBRATION = {
    "longpass": [(13.703, 439.0), (27.911, 524.0), (50.892, 665.0)],
    "shortpass": [(12.683, 496.5), (42.523, 679.5), (53.462, 746.0)],
}
SERIALS = {"longpass": 137126, "shortpass": 137120}
SIM_STAGES = {"longpass": 2, "shortpass": 1}
MIN_WIDTH = 2.0          # nm; narrower than this and the edges' slop swamps the band
SPAN = (400.0, 800.0)    # nm; the working span the QY GUI uses — the line runs far past what
                         # the filters' coatings do (355-1040 nm over full travel)


def load_calibration(path=CALIBRATION_FILE):
    """The calibration file as {"longpass": {"serial", "stage", "points": [...]}, …},
    or None if there isn't one. Points are {"position_mm", "cutoff_nm", "logged"}."""
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        return None
    for role in ROLES:
        data.setdefault(role, {}).setdefault("points", [])
    return data


def save_calibration(data, path=CALIBRATION_FILE):
    """Write the calibration file (via a temp file, so a crash can't leave half of one)."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    data = dict(data, saved=datetime.now().isoformat(timespec="seconds"))
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp, path)


def calibration_points(data, role):
    """[(position mm, cutoff nm)] for a filter from the file, or the built-in
    fallback when the file has fewer than 2."""
    points = [(float(p["position_mm"]), float(p["cutoff_nm"]))
              for p in ((data or {}).get(role) or {}).get("points", [])]
    return points if len(points) >= 2 else CALIBRATION[role]


class LinearFilter:
    """Position (mm) <-> cutoff (nm) for one linear variable filter: the
    least-squares line through its calibration points."""

    def __init__(self, points):
        n = len(points)
        if n < 2:
            raise ValueError("a filter calibration needs at least 2 points")
        mx = sum(p for p, _ in points) / n
        my = sum(w for _, w in points) / n
        sxx = sum((p - mx) ** 2 for p, _ in points)
        if sxx == 0:
            raise ValueError("calibration points are all at one position")
        self.slope = sum((p - mx) * (w - my) for p, w in points) / sxx      # nm per mm
        self.intercept = my - self.slope * mx
        ss_tot = sum((w - my) ** 2 for _, w in points)
        ss_res = sum((w - self.nm_at(p)) ** 2 for p, w in points)
        self.r2 = 1.0 - ss_res / ss_tot if ss_tot else 1.0

    def nm_at(self, mm):
        return self.slope * mm + self.intercept

    def mm_for(self, nm):
        return (nm - self.intercept) / self.slope

    def reach(self, travel):
        """(lowest, highest) cutoff over the stage's travel (low_mm, high_mm), within SPAN."""
        ends = [self.nm_at(mm) for mm in travel]
        return max(SPAN[0], min(ends)), min(SPAN[1], max(ends))


class FilterBand(Driver):
    params = {"center": Param(unit="nm"), "width": Param(unit="nm")}   # ranges set in open()
    readings = {"actual_center": Reading(unit="nm"), "actual_width": Reading(unit="nm"),
                "longpass": Reading(unit="nm"), "shortpass": Reading(unit="nm"),
                "longpass_mm": Reading(unit="mm"), "shortpass_mm": Reading(unit="mm"),
                "moving": Reading(), "homed": Reading()}
    actions = ["home", "stop"]
    poll_hz = 10

    def open(self):
        port = str(self.options.get("port") or "auto")
        if port.lower() == "auto":
            port = zaber_stage._find_zaber_port()
            if port is None:
                raise IOError("set the Zaber's COM port (no port with a Zaber USB id to pick automatically)")
        self.port = None
        chain = zaber_stage._open_chain(port, int(self.options.get("baud", 115200)))
        self.port = port
        self.calibration = load_calibration()
        self.filters = {role: LinearFilter(calibration_points(self.calibration, role)) for role in ROLES}
        self.stages = {role: chain.stage(self._index(chain, role)) for role in ROLES}
        self.reach = {role: self.filters[role].reach(self.stages[role].travel_range())
                      for role in ROLES}
        (lp_lo, lp_hi), (sp_lo, sp_hi) = self.reach["longpass"], self.reach["shortpass"]
        self.params = {"center": Param(round(lp_lo, 1), round(sp_hi, 1), unit="nm", default=550.0),
                       "width": Param(MIN_WIDTH, round(sp_hi - lp_lo, 1), unit="nm", default=20.0)}
        lp, sp = self._cutoffs()                       # connecting never moves the stages
        self.values = {"center": round((lp + sp) / 2, 2), "width": round(sp - lp, 2)}
        source = "calibration file" if self.calibration else "built-in calibration (no file)"
        print(f"  [filter band] {source}: longpass {self.filters['longpass'].slope:.3f} nm/mm, "
              f"shortpass {self.filters['shortpass'].slope:.3f} nm/mm", flush=True)
        print(f"  [filter band] {port}: longpass stage {self.stages['longpass'].index} "
              f"({lp_lo:.0f}-{lp_hi:.0f} nm), shortpass stage {self.stages['shortpass'].index} "
              f"({sp_lo:.0f}-{sp_hi:.0f} nm); band now {lp:.1f}-{sp:.1f} nm", flush=True)

    def _index(self, chain, role):
        """The role's chain index: by serial number on a real chain (the index
        isn't stable), by the *_stage option otherwise."""
        saved = (self.calibration or {}).get(role, {})
        serial = int(self.options.get(f"{role}_serial", saved.get("serial") or SERIALS[role]) or 0)
        if serial and hasattr(chain, "devices_info"):
            for info in chain.devices_info():
                if info["serial_number"] == serial:
                    return info["index"]
            raise IOError(f"the {role} stage (serial {serial}) isn't on the {self.port} chain")
        return int(self.options.get(f"{role}_stage", saved.get("stage", SIM_STAGES[role])))

    def _cutoffs(self):
        return tuple(self.filters[r].nm_at(self.stages[r].get_position()) for r in ("longpass", "shortpass"))

    # --- Setting the band ---------------------------------------------------------
    def apply(self, name, value):
        """Pull the request in to a band both filters can reach, then move.
        Only the param being set changes; the other is held."""
        (lp_lo, lp_hi), (sp_lo, sp_hi) = self.reach["longpass"], self.reach["shortpass"]
        center, width = self.values["center"], self.values["width"]
        if name == "center":
            half = width / 2
            center = max(lp_lo + half, sp_lo - half, min(lp_hi + half, sp_hi - half, float(value)))
        elif name == "width":
            widest = 2 * min(center - lp_lo, sp_hi - center)
            narrowest = max(MIN_WIDTH, 2 * (center - lp_hi), 2 * (sp_lo - center))
            if widest < narrowest:
                raise ValueError(f"no band around {center:.1f} nm can be made — change the center first")
            width = max(narrowest, min(widest, float(value)))
        else:
            raise KeyError(name)
        self._move(center - width / 2, center + width / 2)
        self.values["center"], self.values["width"] = round(center, 2), round(width, 2)
        return self.values[name]

    def _move(self, longpass_nm, shortpass_nm):
        # Both stages must be able to move before either does: a half-applied band
        # (one edge moved, the other refused) would be a band nobody asked for.
        if not all(stage.is_homed() for stage in self.stages.values()):
            raise RuntimeError("the filter stages aren't homed — press Home first")
        for role, nm in (("longpass", longpass_nm), ("shortpass", shortpass_nm)):
            low, high = self.stages[role].travel_range()
            mm = max(low, min(high, self.filters[role].mm_for(nm)))   # rounding at the very ends
            self.stages[role].move_to(mm, wait=False)

    # --- Reading it back ----------------------------------------------------------
    def read(self):
        lp_mm = self.stages["longpass"].get_position()
        sp_mm = self.stages["shortpass"].get_position()
        lp, sp = self.filters["longpass"].nm_at(lp_mm), self.filters["shortpass"].nm_at(sp_mm)
        return {"actual_center": round((lp + sp) / 2, 2), "actual_width": round(sp - lp, 2),
                "longpass": round(lp, 2), "shortpass": round(sp, 2),
                "longpass_mm": round(lp_mm, 4), "shortpass_mm": round(sp_mm, 4),
                "moving": 1.0 if any(s.is_busy() for s in self.stages.values()) else 0.0,
                "homed": 1.0 if all(s.is_homed() for s in self.stages.values()) else 0.0}

    def home(self):
        for stage in self.stages.values():
            stage.home(wait=False)

    def stop(self):
        for stage in self.stages.values():
            stage.stop()

    def close(self):
        if getattr(self, "port", None):
            zaber_stage._release_chain(self.port)
            self.port = None


DRIVER = FilterBand
