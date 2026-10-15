"""fit_nd_filter.py — power (% of max) vs. ND stage position, from a calibrate_nd_filter.py run.

    python scripts/fit_nd_filter.py                       # the newest run in calibration/nd_filter/
    python scripts/fit_nd_filter.py calibration/nd_filter/nd_550nm_20261014_191744.csv
    python scripts/fit_nd_filter.py --tolerance 0.01      # stricter "straight" (dex)

A variable ND filter's optical density grows linearly with position, so in its
working range log10(power) is a straight line in position. Outside it the line
breaks: near 0 mm the beam misses the graded part (flat, with dips), and at the
far end the meter reaches its noise floor or the beam walks off the filter.

The valid range is the longest run of consecutive points whose straight-line
fit of log10(power) leaves every point within --tolerance (dex; 0.02 dex = ±4.7 %).
Power is taken as a percentage of the run's maximum measured power, so the
fit is

    percent(x) = 10 ** (intercept + slope * x)          (x in mm, within the range)
    x(percent) = (log10(percent) - intercept) / slope

Writes <run>_fit.json (the coefficients, range and residuals) and <run>_fit.png
(data, the fit and the valid range on a log axis, plus residuals) next to the
CSV. Other scripts can use load_fit() / percent_at() / position_for() from here.
"""

import argparse
import csv
import glob
import json
import math
import os
import sys
from datetime import datetime

import numpy as np

ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
RUN_DIR = os.path.join(ROOT, "calibration", "nd_filter")
DEFAULT_TOL_DEX = 0.02
MIN_POINTS = 10


def newest_run():
    runs = [p for p in glob.glob(os.path.join(RUN_DIR, "nd_*.csv")) if not p.endswith("_fit.csv")]
    if not runs:
        sys.exit(f"No runs in {os.path.relpath(RUN_DIR, ROOT)} — run calibrate_nd_filter.py first.")
    return max(runs, key=os.path.getmtime)


def load_run(path):
    with open(path, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    x = np.array([float(r["position_mm"]) for r in rows])
    p = np.array([float(r["power_mW"]) for r in rows])
    wavelength = float(rows[0]["wavelength_nm"]) if rows else float("nan")
    order = np.argsort(x)
    return x[order], p[order], wavelength


def line_fit(x, y):
    slope, intercept = np.polyfit(x, y, 1)
    return slope, intercept, y - (intercept + slope * x)


def valid_range(x, y, tol, min_points=MIN_POINTS):
    """(i, j): the longest window x[i:j] whose line fit keeps every point within tol."""
    best = None
    n = len(x)
    for i in range(n - min_points + 1):
        for j in range(n, i + min_points - 1, -1):          # longest first: stop at the first that fits
            if best is not None and j - i <= best[1] - best[0]:
                break
            _s, _b, resid = line_fit(x[i:j], y[i:j])
            if np.max(np.abs(resid)) <= tol:
                best = (i, j)
                break
    return best


def fit(path, tol=DEFAULT_TOL_DEX):
    x, p, wavelength = load_run(path)
    ok = p > 0
    if ok.sum() < MIN_POINTS:
        raise SystemExit(f"{path}: fewer than {MIN_POINTS} positive power readings")
    x, p = x[ok], p[ok]
    p_max = float(p.max())
    y = np.log10(100.0 * p / p_max)                          # log10(% of max)
    window = valid_range(x, y, tol)
    if window is None:
        raise SystemExit(f"No {MIN_POINTS}+ consecutive points lie on a line within {tol} dex — "
                         f"try a larger --tolerance")
    i, j = window
    slope, intercept, resid = line_fit(x[i:j], y[i:j])
    lo, hi = float(x[i]), float(x[j - 1])
    pct_lo, pct_hi = 10 ** (intercept + slope * lo), 10 ** (intercept + slope * hi)
    return {
        "source": os.path.relpath(path, ROOT),
        "fitted": datetime.now().isoformat(sep=" ", timespec="seconds"),
        "wavelength_nm": wavelength,
        "max_power_mW": p_max,
        "max_power_position_mm": float(x[np.argmax(p)]),
        "formula": "percent = 10 ** (intercept + slope * position_mm)",
        "slope_dex_per_mm": float(slope),
        "intercept_dex": float(intercept),
        "od_per_mm": float(-slope),
        "mm_per_decade": float(-1.0 / slope),
        "valid_mm": [lo, hi],
        "valid_percent": [float(max(pct_lo, pct_hi)), float(min(pct_lo, pct_hi))],
        "points_in_range": int(j - i),
        "tolerance_dex": tol,
        "rms_residual_dex": float(np.sqrt(np.mean(resid ** 2))),
        "max_residual_dex": float(np.max(np.abs(resid))),
        "_plot": (x, y, i, j),
    }


def percent_at(fit_data, mm):
    """Power at position mm, % of max (only meaningful inside valid_mm)."""
    return 10 ** (fit_data["intercept_dex"] + fit_data["slope_dex_per_mm"] * mm)


def position_for(fit_data, percent):
    """The position (mm) that gives `percent` of max; ValueError outside the valid range."""
    hi, lo = fit_data["valid_percent"]
    if not lo <= percent <= hi:
        raise ValueError(f"{percent:g} % is outside the fitted range {lo:.3g}–{hi:.3g} %")
    return (math.log10(percent) - fit_data["intercept_dex"]) / fit_data["slope_dex_per_mm"]


def load_fit(path=None):
    """A saved <run>_fit.json (default: the newest one)."""
    if path is None:
        fits = glob.glob(os.path.join(RUN_DIR, "nd_*_fit.json"))
        if not fits:
            raise FileNotFoundError("no ND fit yet — run scripts/fit_nd_filter.py")
        path = max(fits, key=os.path.getmtime)
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def save_plot(result, path):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    x, y, i, j = result["_plot"]
    lo, hi = result["valid_mm"]
    pct = 10 ** y
    xs = np.linspace(lo, hi, 200)
    fig, (ax, res) = plt.subplots(2, 1, figsize=(9, 6.5), sharex=True,
                                  gridspec_kw={"height_ratios": [3, 1]})
    ax.axvspan(lo, hi, color="tab:green", alpha=0.08, label="valid range")
    out = np.ones(len(x), bool)
    out[i:j] = False
    ax.semilogy(x[i:j], pct[i:j], "o", ms=3.5, color="tab:blue", label="in range")
    ax.semilogy(x[out], pct[out], "o", ms=3.5, color="tab:gray", alpha=0.6, label="excluded")
    ax.semilogy(xs, [percent_at(result, v) for v in xs], "-", color="tab:red", lw=1.2,
                label=f"10^({result['intercept_dex']:.4f} {result['slope_dex_per_mm']:+.5f}·x)")
    ax.set_ylabel("power (% of max)")
    ax.grid(True, which="both", alpha=0.25)
    ax.legend(loc="upper right", fontsize=9)
    ax.set_title(f"ND filter at {result['wavelength_nm']:g} nm  ·  valid {lo:.1f}–{hi:.1f} mm "
                 f"({result['valid_percent'][0]:.3g}–{result['valid_percent'][1]:.2g} %)  ·  "
                 f"{result['mm_per_decade']:.2f} mm/decade", fontsize=10)

    slope, intercept = result["slope_dex_per_mm"], result["intercept_dex"]
    resid_pct = 100 * (10 ** (y - (intercept + slope * x)) - 1)
    res.axvspan(lo, hi, color="tab:green", alpha=0.08)
    res.axhline(0, color="tab:red", lw=1)
    res.plot(x[i:j], resid_pct[i:j], "o", ms=3, color="tab:blue")
    tol_pct = 100 * (10 ** result["tolerance_dex"] - 1)
    res.axhline(tol_pct, color="gray", ls=":", lw=1)
    res.axhline(-tol_pct, color="gray", ls=":", lw=1)
    res.set_ylim(-3 * tol_pct, 3 * tol_pct)
    res.set_ylabel("data / fit − 1 (%)")
    res.set_xlabel("ND stage position (mm)")
    res.grid(True, alpha=0.25)
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)


def main():
    ap = argparse.ArgumentParser(description="Fit power (% of max) vs. ND position in its log-linear range.")
    ap.add_argument("csv", nargs="?", help="a calibrate_nd_filter.py CSV (default: the newest)")
    ap.add_argument("--tolerance", type=float, default=DEFAULT_TOL_DEX,
                    help=f"max distance from the line, dex (default {DEFAULT_TOL_DEX} = ±4.7 %%)")
    args = ap.parse_args()

    path = os.path.abspath(args.csv) if args.csv else newest_run()
    result = fit(path, args.tolerance)
    base = os.path.splitext(path)[0]
    save_plot(result, base + "_fit.png")
    with open(base + "_fit.json", "w", encoding="utf-8") as f:
        json.dump({k: v for k, v in result.items() if not k.startswith("_")}, f, indent=2)

    lo, hi = result["valid_mm"]
    hi_pct, lo_pct = result["valid_percent"]
    print(f"{result['source']}  ({result['wavelength_nm']:g} nm, max {result['max_power_mW']:.4g} mW "
          f"at {result['max_power_position_mm']:.2f} mm)")
    print(f"Valid range: {lo:.2f} – {hi:.2f} mm  ({result['points_in_range']} points), "
          f"{hi_pct:.3g} % down to {lo_pct:.2g} % of max")
    print(f"  percent(x) = 10 ** ({result['intercept_dex']:.5f} {result['slope_dex_per_mm']:+.6f} * x)")
    print(f"  x(percent) = (log10(percent) - {result['intercept_dex']:.5f}) / {result['slope_dex_per_mm']:.6f}")
    print(f"  = {result['od_per_mm']:.5f} OD/mm, {result['mm_per_decade']:.2f} mm per decade")
    print(f"  residuals: rms {result['rms_residual_dex']:.4f} dex, max {result['max_residual_dex']:.4f} dex "
          f"(tolerance {result['tolerance_dex']})")
    print(f"Saved {os.path.relpath(base, ROOT)}_fit.json and _fit.png")


if __name__ == "__main__":
    main()
