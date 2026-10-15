"""calibrate_nd_filter.py — sweep the ND filter stage and log power vs. position.

    python scripts/calibrate_nd_filter.py --wavelength 550
    python scripts/calibrate_nd_filter.py --wavelength 550 --points 100 --start 0 --stop 50

At one wavelength: sets the excitation band's center (and the power meter's
correction wavelength) to it, then moves the ND filter stage through --points
evenly spaced positions, and at each one averages a few power meter readings.
Every point is written straight to a CSV in calibration/nd_filter/ (so a run
stopped half way still keeps what it measured), and at the end a plot of power
vs. position is saved next to it as a PNG.

It goes through the instrument server, like the canvas does — so leave the
server (and the canvas) running; this script starts the server if it isn't.
Turn the laser's emission on first. Home the ND stage (canvas, or --home)
before the first run after a power cycle.

The PM100D only sees the beam with the detection flip mirror UP (see DESIGN.md,
"Bench notes"), so the script flips it up before measuring and puts it back
where it was afterwards. The mirror has no readback: its position must be known
on the canvas ("It's at …" or Find position) — the script stops if it isn't.

Devices are named as in devices.json: --stage (default "zaber", the ND filter,
stage 0 on the chain), --meter ("pm100d"), --mirror ("flipper", the detection
flip mirror; "" to leave it alone), --band ("band", the excitation filters;
--band "" to leave the band where it is).
"""

import argparse
import csv
import os
import sys
import time
from datetime import datetime

ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from instruments import InstrumentClient                          # noqa: E402

for _stream in (sys.stdout, sys.stderr):        # a console that can't show a character prints a stand-in
    try:
        _stream.reconfigure(errors="replace")
    except (AttributeError, ValueError):
        pass

OUT_DIR = os.path.join(ROOT, "calibration", "nd_filter")
POSITION_TOL_MM = 0.01          # "arrived" = this close to the target and not moving
MOVE_TIMEOUT_S = 60.0
BAND_TIMEOUT_S = 60.0
BAND_TOL_NM = 1.0
CONNECT_TIMEOUT_S = 20.0
READ_PERIOD_S = 0.11            # the PM100D is polled at 10 Hz: one fresh reading per sample
MIRROR_TIMEOUT_S = 10.0
MIRROR_SETTLE_S = 0.75          # flipper.py's DEFAULT_SETTLE_S: the flip itself takes this long


class Abort(Exception):
    pass


def wait_for(check, timeout, what):
    """Poll check() until it returns something truthy; Abort after timeout."""
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        result = check()
        if result:
            return result
        time.sleep(0.05)
    raise Abort(f"timed out waiting for {what}")


def device_ok(client, name):
    """The device's snapshot once the server has it running."""
    def ready():
        d = client.device(name)
        return d if d and d["status"] == "ok" and d["values"] else None
    try:
        return wait_for(ready, CONNECT_TIMEOUT_S, name)
    except Abort:
        d = client.device(name)
        if d is None:
            raise Abort(f"the instrument server has no device {name!r} "
                        f"(devices: {', '.join(client.device_names()) or 'none'})")
        raise Abort(f"{name} isn't ready: {d['status']} {d['message']}".strip())


def connect():
    client = InstrumentClient()
    client.enabled = True
    client.start()
    try:
        wait_for(lambda: client.connected and client.device_names(), CONNECT_TIMEOUT_S,
                 "the instrument server")
    except Abort:
        raise Abort("couldn't reach the instrument server (it should start on its own; "
                    "check its console window for errors)")
    return client


def reading(client, device, name):
    return client.device(device)["values"].get(name)


def move_stage(client, stage, mm):
    client.set(stage, "target", mm)

    def arrived():
        v = client.device(stage)["values"]
        return (v.get("moving") == 0 and v.get("position") is not None
                and abs(v["position"] - mm) <= POSITION_TOL_MM)
    try:
        wait_for(arrived, MOVE_TIMEOUT_S, f"{stage} to reach {mm:.3f} mm")
    except Abort as exc:
        msg = client.device(stage)["message"]
        raise Abort(f"{exc}{' — ' + msg if msg else ''}")
    return reading(client, stage, "position")


def average_power(client, meter, samples):
    """Mean and standard deviation of `samples` power readings (W)."""
    values = []
    for _ in range(samples):
        time.sleep(READ_PERIOD_S)
        values.append(reading(client, meter, "power"))
    values = [v for v in values if v is not None]
    if not values:
        raise Abort(f"{meter} gave no power readings")
    mean = sum(values) / len(values)
    std = (sum((v - mean) ** 2 for v in values) / max(1, len(values) - 1)) ** 0.5
    return mean, std


def set_band(client, band, nm):
    if reading(client, band, "homed") != 1:
        raise Abort(f"{band} (the excitation filters) isn't homed — home it on the canvas first")
    client.set(band, "center", nm)

    def there():
        v = client.device(band)["values"]
        return v.get("moving") == 0 and abs((v.get("actual_center") or 0) - nm) <= BAND_TOL_NM
    try:
        wait_for(there, BAND_TIMEOUT_S, f"the band to center on {nm:g} nm")
    except Abort:
        got = reading(client, band, "actual_center")
        raise Abort(f"the band didn't reach {nm:g} nm (it's at {got} nm) — "
                    f"{client.device(band)['message'] or 'out of the filters range?'}")


def set_mirror(client, mirror, up):
    """Flip the mirror up (power meter) or down (sphere) and wait for it to settle."""
    want = 1.0 if up else 0.0
    if reading(client, mirror, "is_up") == want:
        return
    client.set(mirror, "up", want)
    try:
        wait_for(lambda: reading(client, mirror, "is_up") == want, MIRROR_TIMEOUT_S,
                 f"{mirror} to flip {'up' if up else 'down'}")
    except Abort as exc:
        msg = client.device(mirror)["message"]
        raise Abort(f"{exc}{' — ' + msg if msg else ''}")
    time.sleep(MIRROR_SETTLE_S)


def save_plot(rows, wavelength, path):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    mm = [r["position_mm"] for r in rows]
    mw = [r["power_mW"] for r in rows]
    err = [r["power_std_mW"] for r in rows]
    fig, (lin, log) = plt.subplots(1, 2, figsize=(11, 4.2))
    for ax in (lin, log):
        ax.errorbar(mm, mw, yerr=err, fmt="o-", ms=3, lw=1, capsize=2)
        ax.set_xlabel("ND stage position (mm)")
        ax.set_ylabel("power (mW)")
        ax.grid(True, alpha=0.3)
    log.set_yscale("log")
    lin.set_title("linear")
    log.set_title("log")
    fig.suptitle(f"ND filter: power vs. position at {wavelength:g} nm  ·  "
                 f"{datetime.now():%Y-%m-%d %H:%M}")
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)


def main():
    p = argparse.ArgumentParser(description="Sweep the ND filter stage and log power vs. position.")
    p.add_argument("--wavelength", type=float, required=True, help="nm: band center + meter correction")
    p.add_argument("--points", type=int, default=100, help="positions to measure (default 100)")
    p.add_argument("--start", type=float, default=None, help="first position, mm (default: travel min)")
    p.add_argument("--stop", type=float, default=None, help="last position, mm (default: travel max)")
    p.add_argument("--samples", type=int, default=5, help="power readings averaged per point (default 5)")
    p.add_argument("--settle", type=float, default=0.3, help="s to wait after each move (default 0.3)")
    p.add_argument("--stage", default="zaber", help="the ND stage's device name (default zaber)")
    p.add_argument("--meter", default="pm100d", help="the power meter's device name (default pm100d)")
    p.add_argument("--mirror", default="flipper",
                   help='the detection flip mirror\'s device name ("" = leave it alone)')
    p.add_argument("--band", default="band", help='the excitation band\'s device name ("" = leave it)')
    p.add_argument("--laser", default="superk", help='the laser\'s device name, to check emission ("" = skip)')
    p.add_argument("--home", action="store_true", help="home the ND stage first")
    args = p.parse_args()
    if args.points < 2:
        p.error("--points must be at least 2")

    client = connect()
    try:
        run(client, args)
    except Abort as exc:
        print(f"\nStopped: {exc}")
        sys.exit(1)
    except KeyboardInterrupt:
        print("\nStopped (Ctrl+C). The points so far are in the CSV.")
        sys.exit(1)
    finally:
        client.enabled = False
        time.sleep(0.2)                 # let queued requests go out


def run(client, args):
    stage = device_ok(client, args.stage)
    device_ok(client, args.meter)

    if args.laser:
        laser = client.device(args.laser)
        if laser is None or laser["status"] != "ok":
            print(f"Note: can't see {args.laser!r}'s emission — make sure the laser is on.")
        elif laser["values"].get("emission") != 1:
            raise Abort(f"{args.laser} emission is off — turn it on first")

    if args.home:
        print("Homing the ND stage…")
        client.send({"op": "action", "device": args.stage, "name": "home"})
        time.sleep(0.5)
        wait_for(lambda: reading(client, args.stage, "homed") == 1
                 and reading(client, args.stage, "moving") == 0, MOVE_TIMEOUT_S, "homing")
    elif reading(client, args.stage, "homed") != 1:
        raise Abort(f"{args.stage} isn't homed — home it on the canvas, or run with --home")

    low, high = stage["params"]["target"]["min"], stage["params"]["target"]["max"]
    start = low if args.start is None else max(low, min(high, args.start))
    stop = high if args.stop is None else max(low, min(high, args.stop))
    positions = [start + (stop - start) * i / (args.points - 1) for i in range(args.points)]

    if args.band:
        device_ok(client, args.band)
        print(f"Centering the band on {args.wavelength:g} nm…")
        set_band(client, args.band, args.wavelength)
    client.set(args.meter, "wavelength", args.wavelength)

    was_up = None
    if args.mirror:
        mirror = device_ok(client, args.mirror)
        was_up = mirror["values"].get("is_up")
        if was_up is None:
            raise Abort(f"{args.mirror}'s position is unknown (no readback) — on the canvas, "
                        f"use It's at … or Find position first")
        set_mirror(client, args.mirror, up=True)
        print("Detection mirror up: the beam goes to the power meter")
    try:
        sweep(client, args, positions, start, stop)
    finally:
        if was_up == 0:
            set_mirror(client, args.mirror, up=False)
            print("Detection mirror back down (sphere)")


def sweep(client, args, positions, start, stop):
    os.makedirs(OUT_DIR, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    base = os.path.join(OUT_DIR, f"nd_{args.wavelength:g}nm_{stamp}")
    csv_path, png_path = base + ".csv", base + ".png"
    print(f"{args.points} points, {start:.3f} → {stop:.3f} mm, at {args.wavelength:g} nm")
    print(f"Writing {os.path.relpath(csv_path, ROOT)}\n")

    rows = []
    fields = ["index", "time", "wavelength_nm", "target_mm", "position_mm",
              "power_W", "power_mW", "power_std_mW", "samples"]
    try:
        with open(csv_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fields)
            writer.writeheader()
            for i, target in enumerate(positions, 1):
                at = move_stage(client, args.stage, round(target, 4))
                time.sleep(args.settle)
                watts, std = average_power(client, args.meter, args.samples)
                row = {"index": i, "time": datetime.now().isoformat(sep=" ", timespec="seconds"),
                       "wavelength_nm": args.wavelength, "target_mm": round(target, 4),
                       "position_mm": at, "power_W": watts, "power_mW": watts * 1e3,
                       "power_std_mW": std * 1e3, "samples": args.samples}
                writer.writerow(row)
                f.flush()
                rows.append(row)
                print(f"  {i:3d}/{args.points}  {at:8.3f} mm   {watts * 1e3:10.5g} mW  ± {std * 1e3:.2g}")
    finally:
        if len(rows) >= 2:
            save_plot(rows, args.wavelength, png_path)
            print(f"\nPlot: {os.path.relpath(png_path, ROOT)}")
    print(f"Done: {len(rows)} points in {os.path.relpath(csv_path, ROOT)}")


if __name__ == "__main__":
    main()
