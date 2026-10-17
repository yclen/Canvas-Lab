"""canvas_run.py — what a script needs so a Script object on the canvas can run it.

A script keeps its argparse parser and stays a normal terminal script; it hands
the parser to start() instead of calling parse_args(), and reports as it goes:

    import canvas_run

    p = argparse.ArgumentParser(description="…")
    p.add_argument("--wavelength", type=float, required=True, help="nm")
    …
    run = canvas_run.start(p, data_folder="detector_sweep",
                           plot={"x": ["power_mW"], "y": ["count_rate", "voltage_V"],
                                 "labels": {"power_mW": "power (mW)", "count_rate": "count rate (counts/s)"}},
                           show=["wavelength"], units={"wavelength": "nm"})   # drawn on the object
    args = run.args
    out_dir = canvas_run.data_folder("detector_sweep")      # experiments/detector_sweep/
    …
    run.dataset(csv_path, label=f"{args.wavelength:g} nm")  # this run's file, and its legend name
    for i, x in enumerate(points, 1):
        canvas_run.check_stop()               # raises canvas_run.Stopped if Stop was pressed
        …measure…
        canvas_run.progress(i, len(points), "power pass")
    run.complete()                            # or run.fail("why"), run.stopped()

Run from a terminal, all of this does nothing extra: progress and stop checks
are no-ops and Ctrl+C works as always. Two command-line flags are the canvas's:

  --canvas-describe          print the script's inputs (its parser) as JSON and
                             exit — nothing else in the script runs
  --canvas-run STATUS.json   run, writing state / progress / dataset to
                             STATUS.json; Stop is the file STATUS.json.stop

The status file (rewritten whole, atomically) holds: state ("running",
"complete", "stopped", "failed"), pid, started, updated, progress [done, total],
stage, dataset (a path), message. If the script ends without saying how, the
exit hook records it as failed, with the error that ended it if there was one.

Data folders: every script writes its runs to experiments/<name>/ — one CSV per
run, named with when it started — so a Graph can list them and follow the latest.
Next to them, canvas.json says how to graph them (written by start() and
dataset(), from a terminal run too, so it covers old runs and unlinked graphs):

    {"plot": {"x": [columns for x], "y": [columns for y], "labels": {column: "axis name"}},
     "runs": {"<file>.csv": "<legend label>"}}

A Graph cycles only those x / y columns (it can show all of them too) and names
the axes and the legend with the labels.
"""

import argparse
import atexit
import json
import threading
import os
import sys
import time
import traceback
from datetime import datetime

ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
EXPERIMENTS = os.path.join(ROOT, "experiments")
WRITE_EVERY_S = 0.25        # progress is written at most this often (the end is always written)


FOLDER_FILE = "canvas.json"
_folder_lock = threading.Lock()


def plot_spec(plot):
    """A script's plot= as {"x": [...], "y": [...], "labels": {...}}; an (x, y)
    pair is one choice each; None stays None."""
    if not plot:
        return None
    if isinstance(plot, dict):
        listed = lambda v: [v] if isinstance(v, str) else list(v or [])   # noqa: E731
        return {"x": listed(plot.get("x")), "y": listed(plot.get("y")), "labels": dict(plot.get("labels") or {})}
    x, y = plot
    return {"x": [x], "y": [y], "labels": {}}


def update_folder_file(folder, **changes):
    """Merge changes into <folder>/canvas.json ("plot" replaces; "runs" adds).
    Never fails a run: a problem here only costs the Graph its hints."""
    path = os.path.join(folder, FOLDER_FILE)
    with _folder_lock:
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError):
            data = {}
        if "plot" in changes:
            data["plot"] = changes["plot"]
        if "runs" in changes:
            data["runs"] = dict(data.get("runs") or {}, **changes["runs"])
        try:
            os.makedirs(folder, exist_ok=True)
            tmp = path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=1)
            os.replace(tmp, path)
        except OSError:
            pass


class Stopped(Exception):
    """Stop was pressed on the canvas: stop after the current point, cleaning up as usual."""


def data_folder(name):
    """experiments/<name>/, made if it isn't there: where a script's runs go."""
    path = os.path.join(EXPERIMENTS, name)
    os.makedirs(path, exist_ok=True)
    return path


def describe(parser, data_folder=None, plot=None, show=(), units=None):
    """The script's inputs, from its parser: what a Script object shows as fields.
    show: the inputs drawn on the object itself (number inputs, linkable there);
    units: {input: unit} for the canvas to show beside a value."""
    inputs = []
    units = units or {}
    for action in parser._actions:
        if isinstance(action, argparse._HelpAction) or action.dest in ("canvas_run", "canvas_describe"):
            continue
        flag = next((s for s in action.option_strings if s.startswith("--")), None)
        if isinstance(action, argparse._StoreTrueAction):
            kind = "bool"
        elif action.type in (int, float):
            kind = action.type.__name__
        else:
            kind = "str"
        default = action.default if action.default is not argparse.SUPPRESS else None
        inputs.append({"name": action.dest, "flag": flag, "kind": kind, "default": default,
                       "required": bool(action.required) or not action.option_strings,
                       "choices": list(action.choices) if action.choices else None,
                       "help": (action.help or "").replace("%%", "%"),
                       "unit": units.get(action.dest, ""), "show": action.dest in show})
    return {"description": parser.description or "", "inputs": inputs,
            "data_folder": data_folder, "plot": plot_spec(plot)}


class Run:
    """One run of a script. Without --canvas-run, a quiet stand-in."""

    def __init__(self, args, status_path=None, folder=None):
        self.args = args
        self.path = status_path
        self.folder = folder                   # experiments/<name>/, if the script said
        self.status = {"state": "running", "pid": os.getpid(),
                       "started": datetime.now().isoformat(sep=" ", timespec="seconds"),
                       "progress": None, "stage": "", "dataset": None, "message": ""}
        self._last_write = 0.0
        self._error = None
        if self.path:
            try:                               # a Stop left over from an earlier run doesn't count
                os.remove(self.path + ".stop")
            except OSError:
                pass
            self._write(force=True)
            atexit.register(self._at_exit)
            hook = sys.excepthook

            def remember(kind, value, tb):    # what ended the script, for the status file
                self._error = "".join(traceback.format_exception_only(kind, value)).strip()
                hook(kind, value, tb)
            sys.excepthook = remember

    @property
    def on_canvas(self):
        return bool(self.path)

    def _write(self, force=False):
        if not self.path:
            return
        now = time.monotonic()
        if not force and now - self._last_write < WRITE_EVERY_S:
            return
        self._last_write = now
        self.status["updated"] = datetime.now().isoformat(sep=" ", timespec="seconds")
        tmp = self.path + ".tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self.status, f, indent=1)
            os.replace(tmp, self.path)
        except OSError:
            pass                               # the canvas can't see it this time; next write

    # --- What a script calls ------------------------------------------------------
    def dataset(self, path, label=None):
        """The file this run writes (a Graph following the latest run shows it),
        and its name in a Graph's legend (e.g. "550 nm · sampleA")."""
        self.status["dataset"] = os.path.abspath(path)
        if label:
            self.status["label"] = label
            update_folder_file(os.path.dirname(os.path.abspath(path)),
                               runs={os.path.basename(path): str(label)})
        self._write(force=True)

    def progress(self, done, total=None, stage=None):
        self.status["progress"] = [done, total]
        if stage is not None:
            self.status["stage"] = stage
        self._write(force=total is not None and done >= total)

    def stage(self, text):
        self.status["stage"] = text
        self._write(force=True)

    def stop_requested(self):
        return bool(self.path) and os.path.exists(self.path + ".stop")

    def check_stop(self):
        if self.stop_requested():
            raise Stopped("stopped from the canvas")

    def _end(self, state, message=""):
        self.status.update(state=state, message=message)
        self._write(force=True)

    def complete(self, message=""):
        self._end("complete", message)

    def fail(self, message):
        self._end("failed", message)

    def stopped(self, message="stopped"):
        self._end("stopped", message)

    def _at_exit(self):
        if self.status["state"] == "running":           # ended without saying how
            self._end("failed", self._error or "the script ended without finishing")
        try:
            os.remove(self.path + ".stop")
        except OSError:
            pass


_run = Run(None)                                         # the current run (a stand-in until start())


def start(parser, data_folder=None, plot=None, show=(), units=None):
    """Parse the arguments (or, with --canvas-describe, print the inputs and
    exit) and begin the run. data_folder: the script's experiments/ folder name;
    plot: the (x, y) columns a Graph shows by default; show: the inputs (argparse
    dest names) the Script object draws on itself, where they can be linked or
    typed; units: {dest: unit} shown beside their values."""
    global _run
    parser.add_argument("--canvas-run", metavar="STATUS.json", default=None, help=argparse.SUPPRESS)
    parser.add_argument("--canvas-describe", action="store_true", help=argparse.SUPPRESS)
    if "--canvas-describe" in sys.argv[1:]:
        print(json.dumps(describe(parser, data_folder, plot, show, units)))
        sys.exit(0)
    args = parser.parse_args()
    folder = os.path.join(EXPERIMENTS, data_folder) if data_folder else None
    if folder and plot:
        update_folder_file(folder, plot=plot_spec(plot))
    _run = Run(args, args.canvas_run, folder)
    return _run


def dataset(path, label=None):
    _run.dataset(path, label)


def progress(done, total=None, stage=None):
    _run.progress(done, total, stage)


def stage(text):
    _run.stage(text)


def check_stop():
    _run.check_stop()
