"""pda45.py — the Thorlabs PDA45 photodetector, read two ways at once.

The PDA45's output goes to two instruments, and this driver reads both over the
same gate so the two numbers describe the same stretch of light:

  Swabian Time Tagger   counts the pulses -> count_rate (counts/s)
  NI USB-6363, ai0      averages the voltage -> voltage (V, the mean)

Wraps the lab drivers `hardware drivers/timetagger.py` and `ni6363.py` (next to
this project). Per ni6363.py, the DAQ can't resolve a single ns pulse: the mean
voltage is a charge-like signal that rises with the pulse rate, and it's the
only voltage number worth keeping (min / max are noise, not pulse heights).

The two halves connect independently: if one is missing (unplugged, held by
another program) the other keeps reading, and the missing one is retried every
few seconds. Their state is in the text readings tagger_status / daq_status.

devices.json options:
  tagger      "auto" (the first tagger found), a serial number, or "sim://"
  tt_channel  input to count on (default 2 — the PDA45 on this bench; negative
              = falling edge)
  trigger_v   discriminator level, V (default 2.5, timetagger.py's bench value)
  daq         "auto" (the first NI device with analog inputs), a device name
              like "Dev1", or "sim://"
  ai_channel  analog input (default "ai0")
  ai_range    input range ±V (default 0.5); the card snaps to its own ranges
              (±0.1 … ±10 V), and a narrow one resolves small shifts

params:   gate — how long each reading integrates, s
readings: count_rate (counts/s), voltage (V), overflows (events the tagger lost
          — while > 0 the count rate is a lower bound), clipped (samples that
          hit the ends of the input range — while > 0 the voltage is wrong),
          tagger_status, daq_status (text)

Only one program can hold the Time Tagger: close Swabian's Time Tagger Lab (or
any script using it) first.
"""

import math
import os
import random
import sys
import time

from drivers.base import Driver, Param, Reading

LAB_DRIVERS = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                            "..", "..", "hardware drivers"))
RETRY_SECONDS = 3.0          # how often a half that's down tries to connect again


def _lab(module):
    if LAB_DRIVERS not in sys.path:
        sys.path.insert(0, LAB_DRIVERS)
    return __import__(module)


def _short(err):
    return f"{type(err).__name__}: {err}".splitlines()[0][:160]


class PDA45(Driver):
    params = {"gate": Param(0.01, 10.0, unit="s", default=0.2)}
    readings = {"count_rate": Reading(unit="counts/s"), "voltage": Reading(unit="V"),
                "overflows": Reading(), "clipped": Reading(),
                "tagger_status": Reading(), "daq_status": Reading()}
    poll_hz = 20         # read() itself takes a gate, so this just means "back to back"

    def open(self):
        self.tagger = self.daq = None
        self.tagger_status = self.daq_status = "connecting…"
        self._next_try = 0.0
        self._connect_missing()

    # --- The two halves ------------------------------------------------------------
    def _connect_missing(self):
        """Open whichever half isn't connected (at most every RETRY_SECONDS)."""
        now = time.monotonic()
        if now < self._next_try:
            return
        self._next_try = now + RETRY_SECONDS
        if self.tagger is None:
            try:
                self.tagger = self._open_tagger()
                self.tagger_status = "connected"
            except Exception as e:
                self.tagger, self.tagger_status = None, _short(e)
        if self.daq is None:
            try:
                self.daq = self._open_daq()
                self.daq_status = "connected"
            except Exception as e:
                self.daq, self.daq_status = None, _short(e)

    def _open_tagger(self):
        where = str(self.options.get("tagger") or "auto")
        channel = int(self.options.get("tt_channel", 2))
        level = float(self.options.get("trigger_v", 2.5))
        if where.startswith("sim://"):
            return SimTagger()
        tt = _lab("timetagger").TimeTagger(address=None if where.lower() == "auto" else where,
                                           channel=channel, trigger_level_v=level)
        if not tt.connect():
            raise IOError(f"couldn't open the Time Tagger {tt.address} (in use by another program?)")
        try:
            tt.set_trigger_level(level)      # connect() swallows a refused level; this raises
        except Exception:
            tt.disconnect()
            raise
        print(f"  [pda45] tagger {tt} at {level:+.3f} V", flush=True)
        return tt

    def _open_daq(self):
        where = str(self.options.get("daq") or "auto")
        span = abs(float(self.options.get("ai_range", 0.5)))
        if where.startswith("sim://"):
            return SimDAQ()
        daq = _lab("ni6363").NI6363(address=None if where.lower() == "auto" else where,
                                    channel=str(self.options.get("ai_channel") or "ai0"),
                                    v_min=-span, v_max=span)
        if not daq.connect():
            raise IOError(f"couldn't open {daq.physical_channel}")
        low, high = daq.ai_range()
        print(f"  [pda45] daq {daq.physical_channel} range {low:+g} to {high:+g} V", flush=True)
        return daq

    def _drop(self, half, err):
        """A half failed mid-reading: close it, show why, and retry later."""
        dev = getattr(self, half)
        try:
            dev.disconnect()
        except Exception:
            pass
        setattr(self, half, None)
        setattr(self, half + "_status", _short(err))
        self._next_try = time.monotonic() + RETRY_SECONDS

    # --- Server hooks ------------------------------------------------------------------
    def set_gate(self, seconds):
        pass                                 # used from the next reading on

    def read(self):
        self._connect_missing()
        gate = self.values["gate"]
        started = []
        for half in ("tagger", "daq"):       # start both, so they gate the same light
            dev = getattr(self, half)
            if dev is None:
                continue
            try:
                dev.start_for(gate)
                started.append(half)
            except Exception as e:
                self._drop(half, e)
        out = {}
        if "tagger" in started:
            try:
                if not self.tagger.wait(timeout_s=gate + 2.0):
                    raise TimeoutError(f"no count after {gate + 2.0:g} s")
                r = self.tagger.read()
                out["count_rate"], out["overflows"] = r["rate_hz"], r["overflows"]
            except Exception as e:
                self._drop("tagger", e)
        if "daq" in started:
            try:
                self.daq.wait()
                r = self.daq.read()
                out["voltage"], out["clipped"] = r["mean_v"], r["clipped_samples"]
            except Exception as e:
                self._drop("daq", e)
        if not started:
            time.sleep(0.2)                  # nothing to wait on: don't spin
        out["tagger_status"], out["daq_status"] = self.tagger_status, self.daq_status
        return out

    def close(self):
        for half in ("tagger", "daq"):
            dev = getattr(self, half, None)
            if dev is not None:
                try:
                    dev.disconnect()
                except Exception:
                    pass
                setattr(self, half, None)


DRIVER = PDA45


# --- Pretend hardware ("sim://") ---------------------------------------------------------
def _sim_rate():
    """The light both sims see: ~50 k counts/s, drifting slowly."""
    return 5e4 * (1 + 0.4 * math.sin(time.monotonic() * 0.5))


class SimTagger:
    def start_for(self, seconds):
        self._seconds, self._end = seconds, time.monotonic() + seconds

    def wait(self, timeout_s=None):
        time.sleep(max(0.0, self._end - time.monotonic()))
        return True

    def read(self):
        counts = max(0, round(random.gauss(_sim_rate() * self._seconds,
                                           math.sqrt(_sim_rate() * self._seconds))))
        return {"rate_hz": counts / self._seconds, "overflows": 0}

    def disconnect(self):
        pass


class SimDAQ:
    def start_for(self, seconds):
        self._end = time.monotonic() + seconds

    def wait(self, timeout_s=None):
        time.sleep(max(0.0, self._end - time.monotonic()))
        return True

    def read(self):
        return {"mean_v": 2e-6 * _sim_rate() + random.gauss(0, 2e-4), "clipped_samples": 0}

    def disconnect(self):
        pass
