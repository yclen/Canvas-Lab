"""calibrate_excitation_filters.py — log position -> cutoff points for the two
excitation filters (longpass + shortpass linear variable filters on Zaber stages).

    python scripts/calibrate_excitation_filters.py              # the port from devices.json
    python scripts/calibrate_excitation_filters.py --port sim://   # pretend stages, to try it

A small window with one panel per filter. Move a filter with its ◀ ▶ buttons
(or the arrow keys — Shift = 10× the step), look at the spectrometer, type
the cutoff you see, press Log point (or Enter). Each point (the stage's
position + your cutoff) is written straight to calibration/excitation_filters.json;
log as many as you like, then do the other filter. The fit (a straight line —
these filters are linear) and how far each point sits off it update as you
go, so a mistyped point stands out. Clear all archives the old file to
calibration/archive/ first. Read the cutoff the same way every time (e.g.
where the edge is at half height).

The canvas's Excitation filters device reads the file when it connects. The
instrument server holds the Zaber port while it runs, so stop it first (Stop
server on the canvas's Instrument server) — this script says so if the port
is busy. Stage 0 on the chain is the ND filter; the stage pickers default to
the filters by serial number (longpass 137126, shortpass 137120).
"""

import argparse
import json
import os
import shutil
import sys
import tkinter as tk
from datetime import datetime
from tkinter import messagebox, ttk

ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from drivers import zaber_stage                                   # noqa: E402
from drivers.filter_band import (CALIBRATION_FILE, ROLES, SERIALS, SIM_STAGES,  # noqa: E402
                                 LinearFilter, load_calibration, save_calibration)

STEPS = (0.01, 0.1, 0.5, 1.0, 5.0)      # mm per ◀ ▶ press
DEFAULT_STEP = 0.5
FLAG_NM = 2.0                           # a point this far off the line is probably mistyped
CUTOFF_RANGE = (300.0, 1100.0)          # nm a typed cutoff must be in
POLL_MS = 150
ROLE_TITLES = {"longpass": "Longpass  —  passes longer wavelengths: the band's BLUE edge",
               "shortpass": "Shortpass  —  passes shorter wavelengths: the band's RED edge"}
ARCHIVE_DIR = os.path.join(os.path.dirname(CALIBRATION_FILE), "archive")


def default_port():
    """The Excitation filters device's port from devices.json (else auto-detect)."""
    try:
        with open(os.path.join(ROOT, "devices.json"), encoding="utf-8") as f:
            devices = json.load(f)["devices"]
        port = next(d["options"].get("port", "auto") for d in devices if d.get("driver") == "filter_band")
    except (OSError, ValueError, KeyError, StopIteration):
        port = "auto"
    if str(port).lower() == "auto":
        port = zaber_stage._find_zaber_port() or ""
    return port


def empty_calibration():
    return {"version": 1,
            "about": "Excitation filter calibration: each filter's stage position (mm) vs. its cutoff (nm). "
                     "Logged with scripts/calibrate_excitation_filters.py; read by drivers/filter_band.py "
                     "when the device opens.",
            **{role: {"serial": SERIALS[role], "stage": SIM_STAGES[role], "points": []} for role in ROLES}}


def worst_off(points):
    """How far the furthest point is from the line through all of them (nm)."""
    fit = LinearFilter(points)
    return max(abs(nm - fit.nm_at(mm)) for mm, nm in points)


def suspect(points):
    """The index of the one point that looks mistyped — the one whose removal
    leaves the others fitting within FLAG_NM — or None. Needs 4+ points: with
    3, any one of them could be the odd one out."""
    if len(points) < 4 or worst_off(points) <= FLAG_NM:
        return None
    rest = {i: points[:i] + points[i + 1:] for i in range(len(points))}
    rest = {i: r for i, r in rest.items() if len({mm for mm, _ in r}) >= 2}
    best = min(rest, key=lambda i: worst_off(rest[i]), default=None)
    return best if best is not None and worst_off(rest[best]) <= FLAG_NM else None


class FilterPanel(ttk.LabelFrame):
    """One filter: stage picker, position + jog, log a cutoff, the points and the fit."""

    def __init__(self, app, role):
        super().__init__(app.root, text=ROLE_TITLES[role], padding=8)
        self.app, self.role = app, role
        self.stage = None            # the chain's stage handle once connected
        self.target = None           # where we last asked it to go (mm)
        self.step = tk.DoubleVar(value=DEFAULT_STEP)

        row = ttk.Frame(self)
        row.pack(fill="x")
        ttk.Label(row, text="Stage").pack(side="left")
        self.picker = ttk.Combobox(row, state="readonly", width=34)
        self.picker.pack(side="left", padx=(6, 12))
        self.picker.bind("<<ComboboxSelected>>", lambda e: self.pick_stage())
        self.home_btn = ttk.Button(row, text="Home", command=self.home)
        self.home_btn.pack(side="right")

        row = ttk.Frame(self)
        row.pack(fill="x", pady=(8, 2))
        self.left = ttk.Button(row, text="◀", width=4, command=lambda: self.jog(-1))
        self.left.pack(side="left")
        self.position = ttk.Label(row, text="—", width=14, anchor="center", font=("Consolas", 16, "bold"))
        self.position.pack(side="left", padx=6)
        self.right = ttk.Button(row, text="▶", width=4, command=lambda: self.jog(+1))
        self.right.pack(side="left")
        steps = ttk.Frame(row)
        steps.pack(side="left", padx=(14, 0))
        ttk.Label(steps, text="step (mm)").pack(side="left")
        for mm in STEPS:
            ttk.Radiobutton(steps, text=f"{mm:g}", value=mm, variable=self.step).pack(side="left")
        self.goto = ttk.Entry(row, width=8)
        self.goto.pack(side="right")
        self.goto.bind("<Return>", lambda e: self.go_to())
        ttk.Button(row, text="Go to mm", command=self.go_to).pack(side="right", padx=(0, 4))

        self.predicted = ttk.Label(self, text="", foreground="#666")
        self.predicted.pack(anchor="w")

        row = ttk.Frame(self)
        row.pack(fill="x", pady=(8, 4))
        ttk.Label(row, text="Cutoff on the spectrometer (nm)").pack(side="left")
        self.cutoff = ttk.Entry(row, width=10, font=("Consolas", 12))
        self.cutoff.pack(side="left", padx=6)
        self.cutoff.bind("<Return>", lambda e: self.log_point())
        self.log_btn = ttk.Button(row, text="Log point", command=self.log_point)
        self.log_btn.pack(side="left")

        self.table = ttk.Treeview(self, columns=("mm", "nm", "off", "logged"), show="headings", height=6)
        for col, title, width in (("mm", "position (mm)", 110), ("nm", "cutoff (nm)", 100),
                                  ("off", "off the line (nm)", 120), ("logged", "logged", 220)):
            self.table.heading(col, text=title)
            self.table.column(col, width=width, anchor="center")
        self.table.tag_configure("bad", foreground="#c0392b")
        self.table.pack(fill="x")

        row = ttk.Frame(self)
        row.pack(fill="x", pady=(4, 0))
        self.fit = ttk.Label(row, text="")
        self.fit.pack(side="left")
        ttk.Button(row, text="Clear all…", command=self.clear_points).pack(side="right")
        ttk.Button(row, text="Delete point", command=self.delete_point).pack(side="right", padx=4)

        stack = [self]                                    # clicking in a panel makes the arrow keys move it
        while stack:
            widget = stack.pop()
            widget.bind("<Button-1>", lambda e: app.set_active(role), add="+")
            stack.extend(widget.winfo_children())

    # --- Data ------------------------------------------------------------------------
    @property
    def entry(self):
        return self.app.data[self.role]

    def line(self):
        points = [(p["position_mm"], p["cutoff_nm"]) for p in self.entry["points"]]
        return LinearFilter(points) if len({mm for mm, _ in points}) >= 2 else None

    def refresh_points(self):
        self.table.delete(*self.table.get_children())
        fit = self.line()
        points = [(p["position_mm"], p["cutoff_nm"]) for p in self.entry["points"]]
        odd = suspect(points) if fit else None
        worst = 0.0
        for i, p in enumerate(self.entry["points"]):
            off = p["cutoff_nm"] - fit.nm_at(p["position_mm"]) if fit else None
            worst = max(worst, abs(off or 0.0))
            bad = i == odd if odd is not None else (off is not None and abs(off) > FLAG_NM)
            self.table.insert("", "end", iid=str(i), tags=("bad",) if bad else (),
                              values=(f"{p['position_mm']:.3f}", f"{p['cutoff_nm']:.1f}",
                                      "—" if off is None else f"{off:+.1f}", p.get("logged", "")))
        n = len(points)
        if fit is None:
            self.fit.config(text=f"{n} point{'s' if n != 1 else ''} — log at least 2, at different positions",
                            foreground="#666")
            return
        if n == 2:
            note = " · 2 points always fit exactly: log a 3rd to check"
        elif odd is not None:
            rest = points[:odd] + points[odd + 1:]
            note = (f" · the red point ({points[odd][0]:.3f} mm) looks mistyped — without it the others"
                    f" fit within {worst_off(rest):.1f} nm")
        elif worst > FLAG_NM:
            note = (f" · points up to {worst:.1f} nm off the line — re-measure one"
                    + (", or log another to tell which" if n == 3 else ""))
        else:
            note = f" · every point within {worst:.1f} nm"
        self.fit.config(text=f"cutoff = {fit.slope:.3f} nm/mm × position + {fit.intercept:.1f} nm"
                             f" · R² {fit.r2:.5f}{note}",
                        foreground="#c0392b" if worst > FLAG_NM else "#1e7e34")

    def log_point(self):
        if self.stage is None:
            return self.app.say("Connect first.", error=True)
        try:
            nm = float(self.cutoff.get().strip())
        except ValueError:
            return self.app.say("Type the cutoff in nm (a number), then Log point.", error=True)
        if not CUTOFF_RANGE[0] <= nm <= CUTOFF_RANGE[1]:
            return self.app.say(f"{nm:g} nm is outside {CUTOFF_RANGE[0]:.0f}–{CUTOFF_RANGE[1]:.0f} nm — "
                                "typo?", error=True)
        if self.stage.is_busy():
            return self.app.say("Still moving — wait until it stops, then log.", error=True)
        mm = round(self.stage.get_position(), 4)
        self.entry["points"].append({"position_mm": mm, "cutoff_nm": nm,
                                     "logged": datetime.now().isoformat(sep=" ", timespec="seconds")})
        self.app.save()
        self.cutoff.delete(0, "end")
        self.refresh_points()
        self.app.say(f"Logged {self.role}: {mm:.3f} mm → {nm:g} nm")

    def delete_point(self):
        chosen = sorted((int(i) for i in self.table.selection()), reverse=True)
        if not chosen:
            return self.app.say("Click a point in the table first, then Delete point.", error=True)
        for i in chosen:
            del self.entry["points"][i]
        self.app.save()
        self.refresh_points()

    def clear_points(self):
        if not self.entry["points"]:
            return
        if not messagebox.askyesno("Clear all points",
                                   f"Remove all {len(self.entry['points'])} {self.role} points?\n\n"
                                   f"The current file is copied to calibration/archive/ first."):
            return
        self.app.archive()
        self.entry["points"] = []
        self.app.save()
        self.refresh_points()

    # --- Stage --------------------------------------------------------------------------
    def set_choices(self, stages):
        """stages: [(index, label, serial or None)]. Picks the saved stage (by serial,
        else index), else the default for this role."""
        self.choices = stages
        self.picker.config(values=[label for _i, label, _s in stages])
        saved_serial, saved_index = self.entry.get("serial"), self.entry.get("stage")
        pick = next((k for k, (_i, _l, s) in enumerate(stages) if s and s == saved_serial), None)
        if pick is None:
            pick = next((k for k, (i, _l, _s) in enumerate(stages) if i == saved_index), None)
        if pick is None:
            pick = next((k for k, (i, _l, _s) in enumerate(stages) if i == SIM_STAGES[self.role]), 0)
        self.picker.current(pick)
        self.pick_stage(save=False)

    def pick_stage(self, save=True):
        index, _label, serial = self.choices[self.picker.current()]
        self.stage = self.app.chain.stage(index)
        self.target = None
        if save:
            self.entry["stage"], self.entry["serial"] = index, serial
            self.app.save()
            self.app.check_stages()

    def jog(self, direction, factor=1.0):
        if self.stage is None:
            return
        low, high = self.stage.travel_range()
        here = self.target if (self.target is not None and self.stage.is_busy()) else self.stage.get_position()
        self.move(max(low, min(high, here + direction * self.step.get() * factor)))

    def go_to(self):
        try:
            mm = float(self.goto.get().strip())
        except ValueError:
            return self.app.say("Type a position in mm to go to.", error=True)
        if self.stage is not None:
            low, high = self.stage.travel_range()
            self.move(max(low, min(high, mm)))

    def move(self, mm):
        try:
            self.stage.move_to(mm, wait=False)
            self.target = mm
        except Exception as exc:
            hint = "" if self.stage.is_homed() or "home" in str(exc).lower() else " — press Home"
            self.app.say(f"{self.role}: {exc}{hint}", error=True)

    def home(self):
        if self.stage is not None and messagebox.askyesno(
                "Home", f"Home the {self.role} stage? It moves to its home end (0 mm)."):
            self.stage.home(wait=False)
            self.target = None

    def poll(self):
        if self.stage is None:
            self.position.config(text="—")
            self.predicted.config(text="")
            return
        try:
            mm = self.stage.get_position()
            busy, homed = self.stage.is_busy(), self.stage.is_homed()
        except Exception as exc:
            self.position.config(text="error")
            self.predicted.config(text=str(exc))
            return
        self.position.config(text=f"{mm:.3f} mm", foreground="#b8860b" if busy else "")
        fit = self.line()
        bits = ["moving…" if busy else "", "" if homed else "NOT HOMED — press Home before moving"]
        if fit is not None:
            bits.append(f"the current line says {fit.nm_at(mm):.1f} nm here")
        self.predicted.config(text="   ".join(b for b in bits if b),
                              foreground="#c0392b" if not homed else "#666")


class CalibrationApp:
    def __init__(self, port):
        self.root = tk.Tk()
        self.root.title("Excitation filter calibration")
        self.data = load_calibration() or empty_calibration()
        self.chain = None
        self.port = None
        self.active = ROLES[0]

        top = ttk.Frame(self.root, padding=8)
        top.pack(fill="x")
        ttk.Label(top, text="Zaber port").pack(side="left")
        self.port_entry = ttk.Entry(top, width=12)
        self.port_entry.insert(0, port)
        self.port_entry.pack(side="left", padx=6)
        self.connect_btn = ttk.Button(top, text="Connect", command=self.toggle_connection)
        self.connect_btn.pack(side="left")
        shown = CALIBRATION_FILE[len(ROOT) + 1:] if CALIBRATION_FILE.startswith(ROOT) else CALIBRATION_FILE
        ttk.Label(top, text=f"Points go to {shown}",
                  foreground="#666").pack(side="right")

        self.panels = {role: FilterPanel(self, role) for role in ROLES}
        for panel in self.panels.values():
            panel.pack(fill="x", padx=8, pady=4)
            panel.refresh_points()

        self.status = ttk.Label(self.root, text="", padding=(8, 4))
        self.status.pack(fill="x")
        self.say("Stop the canvas's instrument server first (it holds the Zaber port), then Connect.")

        for key, direction in (("<Left>", -1), ("<Right>", 1)):
            self.root.bind(key, lambda e, d=direction: self.arrow(e, d, 1.0))
            self.root.bind(f"<Shift-{key[1:-1]}>", lambda e, d=direction: self.arrow(e, d, 10.0))
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self.set_active(self.active)
        self.root.after(POLL_MS, self.poll)

    # --- Connection ---------------------------------------------------------------------
    def toggle_connection(self):
        if self.chain is not None:
            return self.disconnect()
        port = self.port_entry.get().strip()
        if not port:
            return self.say("Type the Zaber's COM port (e.g. COM3).", error=True)
        try:
            self.chain = zaber_stage._open_chain(port, 115200)
            self.port = port
        except Exception as exc:
            self.chain = None
            messagebox.showerror("Couldn't connect",
                                 f"Couldn't open {port}:\n{exc}\n\nIf the canvas's instrument server is "
                                 f"running, it holds this port: press Stop server on the Instrument server "
                                 f"object, then Connect again.")
            return
        stages = self.list_stages()
        for panel in self.panels.values():
            panel.set_choices(stages)
        self.connect_btn.config(text="Disconnect")
        self.port_entry.config(state="disabled")
        self.say(f"Connected to {port}: {len(stages)} stages. Click a panel, move it, log points.")
        self.check_stages()

    def list_stages(self):
        """[(index, label, serial or None)] for every stage on the chain."""
        if hasattr(self.chain, "devices_info"):
            return [(d["index"], f"{d['index']} · {d['name']} · SN {d['serial_number']}"
                     + ("  (ND)" if d["index"] == 0 else ""), d["serial_number"])
                    for d in self.chain.devices_info()]
        return [(s.index, f"{s.index} · {s.info()['name']}" + ("  (ND)" if s.index == 0 else ""), None)
                for s in self.chain.stages]

    def check_stages(self):
        picked = [p.picker.current() for p in self.panels.values()]
        if len(set(picked)) < len(picked):
            self.say("Both filters are set to the same stage — pick the other one for one of them.", error=True)
        elif self.status.cget("text").startswith("Both filters"):
            self.say("Stages set.")

    def disconnect(self):
        if self.port:
            zaber_stage._release_chain(self.port)
        self.chain, self.port = None, None
        for panel in self.panels.values():
            panel.stage = None
        self.connect_btn.config(text="Connect")
        self.port_entry.config(state="normal")
        self.say("Disconnected.")

    def close(self):
        self.disconnect()
        self.root.destroy()

    # --- Helpers --------------------------------------------------------------------------
    def save(self):
        save_calibration(self.data)

    def archive(self):
        if os.path.exists(CALIBRATION_FILE):
            os.makedirs(ARCHIVE_DIR, exist_ok=True)
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            shutil.copy2(CALIBRATION_FILE, os.path.join(ARCHIVE_DIR, f"excitation_filters_{stamp}.json"))

    def say(self, text, error=False):
        self.status.config(text=text, foreground="#c0392b" if error else "")

    def set_active(self, role):
        self.active = role
        for r, panel in self.panels.items():
            panel.config(text=("▶ " if r == role else "") + ROLE_TITLES[r])

    def arrow(self, event, direction, factor):
        if isinstance(event.widget, (tk.Entry, ttk.Entry)):
            return                                   # arrows move the text cursor while typing
        self.panels[self.active].jog(direction, factor)

    def poll(self):
        for panel in self.panels.values():
            panel.poll()
        self.root.after(POLL_MS, self.poll)

    def run(self):
        self.root.mainloop()


def main():
    parser = argparse.ArgumentParser(description="Log position -> cutoff points for the excitation filters.")
    parser.add_argument("--port", default=None, help="Zaber COM port (default: devices.json's), or sim://")
    args = parser.parse_args()
    CalibrationApp(args.port if args.port is not None else default_port()).run()


if __name__ == "__main__":
    main()
