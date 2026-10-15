"""power_to_position.py — ND filter: power <-> stage position, from the fitted calibration.

    from power_to_position import percent_to_position, position_to_percent
    percent_to_position(1)          # -> 49.27   (mm that give 1 % of max)
    position_to_percent(49.27)      # -> 1.0     (% of max at 49.27 mm)
    power_to_position(0.5)          # -> mm for 0.5 mW (uses MAX_MW below, or pass max_mw=)
    position_to_power(30)           # -> mW at 30 mm

    python scripts/power_to_position.py 1%          # % of max -> mm
    python scripts/power_to_position.py 0.5mW       # mW (also W, uW, nW) -> mm
    python scripts/power_to_position.py 49.3mm      # mm -> % of max and mW

The formula (a straight line in log10(power) — the ND filter's density grows
linearly with position):

    percent = 10 ** (INTERCEPT + SLOPE * mm)
    mm      = (log10(percent) - INTERCEPT) / SLOPE

It holds only inside VALID_MM; outside it the functions raise ValueError
(pass clamp=True to pull the request in to the nearest end instead). Accuracy
is about ±3 % (the filter's own non-uniformity), ±5 % at worst.

Percentages are of the power with the ND filter out of the beam, so they don't
depend on the laser setting. The mW versions assume MAX_MW, the max measured
in the calibration run — pass max_mw= if the laser power has changed since.

To update after a new calibration: run calibrate_nd_filter.py, then
fit_nd_filter.py, and copy the numbers it prints into the constants below.
"""

import math
import re
import sys

# From calibration/nd_filter/nd_550nm_20261014_191744_fit.json (fit_nd_filter.py, tolerance 0.02 dex)
WAVELENGTH_NM = 550.0
INTERCEPT = 2.56653         # log10(% of max) at 0 mm
SLOPE = -0.052096           # log10(% of max) per mm  (0.0521 OD/mm, 19.2 mm per decade)
VALID_MM = (22.58, 96.47)
MAX_MW = 18.76              # power with the ND filter out of the beam, in that run


def position_to_percent(mm, clamp=False):
    """Power at stage position `mm`, as % of max."""
    mm = _check(mm, *VALID_MM, "mm", clamp)
    return 10 ** (INTERCEPT + SLOPE * mm)


def percent_to_position(percent, clamp=False):
    """The stage position (mm) that gives `percent` % of max."""
    if percent <= 0:
        raise ValueError(f"percent must be > 0 (got {percent:g})")
    low, high = sorted(10 ** (INTERCEPT + SLOPE * mm) for mm in VALID_MM)
    percent = _check(percent, low, high, "%", clamp)
    return (math.log10(percent) - INTERCEPT) / SLOPE


def position_to_power(mm, max_mw=MAX_MW, clamp=False):
    """Power at stage position `mm`, in mW."""
    return position_to_percent(mm, clamp) / 100.0 * max_mw


def power_to_position(mw, max_mw=MAX_MW, clamp=False):
    """The stage position (mm) that gives `mw` mW."""
    return percent_to_position(100.0 * mw / max_mw, clamp)


def valid_percent():
    """(highest, lowest) % of max the calibrated range covers."""
    return position_to_percent(VALID_MM[0]), position_to_percent(VALID_MM[1])


def _check(value, low, high, unit, clamp):
    if low <= value <= high:
        return value
    if clamp:
        return min(high, max(low, value))
    raise ValueError(f"{value:g} {unit} is outside the calibrated range {low:.4g}–{high:.4g} {unit}")


UNITS_MW = {"w": 1e3, "mw": 1.0, "uw": 1e-3, "µw": 1e-3, "nw": 1e-6}


def main(argv):
    if not argv:
        print(__doc__.split("\n\n")[1])
        hi, lo = valid_percent()
        print(f"Calibrated: {VALID_MM[0]:g}–{VALID_MM[1]:g} mm = {hi:.3g}–{lo:.2g} % of max "
              f"({MAX_MW:g} mW at {WAVELENGTH_NM:g} nm)")
        return
    for arg in argv:
        m = re.fullmatch(r"\s*([-+0-9.eE]+)\s*(%|mm|w|mw|uw|µw|nw)\s*", arg, re.IGNORECASE)
        if not m:
            print(f"{arg}: give a number with a unit — 1%, 0.5mW, 49.3mm")
            continue
        value, unit = float(m.group(1)), m.group(2).lower()
        try:
            if unit == "mm":
                pct = position_to_percent(value)
                print(f"{value:g} mm -> {pct:.4g} % of max = {pct / 100 * MAX_MW:.4g} mW")
            else:
                pct = value if unit == "%" else 100.0 * value * UNITS_MW[unit] / MAX_MW
                print(f"{arg} ({pct:.4g} % of max) -> {percent_to_position(pct):.2f} mm")
        except ValueError as exc:
            print(f"{arg}: {exc}")


if __name__ == "__main__":
    main(sys.argv[1:])
