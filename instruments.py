"""instruments.py — the Canvas app's link to the instrument server.

The server (instrument_server.py) runs the device drivers in its own process;
this module keeps a connection to it on a background thread, so the canvas
never waits on hardware:
- it only connects while `client.enabled` is on (the Instrument server
  object's Connect / Disconnect), and then reconnects every second whenever
  the server isn't up,
- `client.set(device, param, value)` queues a value — only the newest per
  (device, param) is kept, and they go out at most SEND_HZ times a second, so
  a dragged slider can't flood a slow serial line,
- `client.send({...})` queues any other request (add / remove a device, shutdown),
- incoming readings / param values / statuses are kept per device and read by
  the canvas through `client.device(name)` (a snapshot, safe to use); the
  drivers the server offers are in `client.drivers`.

Local only: the server listens on 127.0.0.1 — the devices are on this PC.
"""

import json
import os
import select
import socket
import subprocess
import sys
import threading
import time

HOST = "127.0.0.1"
PORT = int(os.environ.get("CANVAS_INSTRUMENT_PORT", 47800))   # keep in sync with instrument_server.py
                                       # (tests set the variable so they never touch the real server)
SEND_HZ = 30                            # max sends per second, per (device, param)
RECONNECT_SECONDS = 1.0
SERVER_SCRIPT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "instrument_server.py")


class InstrumentClient:
    def __init__(self):
        self.enabled = False            # connect at all? (the Instrument server object decides)
        self.connected = False
        self.drivers = []               # driver modules the server offers (from the catalog)
        self.last_error = ""            # the server's last complaint (e.g. a duplicate name)
        self._devices = {}              # name -> catalog entry (+ live "values")
        self._pending = {}              # (device, param) -> newest value to send
        self._sent = {}                 # (device, param) -> last value sent (skip repeats)
        self._outbox = []               # other requests to send
        self._lock = threading.Lock()
        self._thread = None

    # --- Used by the canvas (main thread) ---------------------------------------
    def start(self):
        if self._thread is None:
            self._thread = threading.Thread(target=self._run, name="instruments", daemon=True)
            self._thread.start()

    def device_names(self):
        with self._lock:
            return list(self._devices)

    def device(self, name):
        """A snapshot of one device: {driver, status, message, params: {p: {min,
        max, unit, value}}, readings: {r: {unit}}, values: {r: number}}, or None."""
        with self._lock:
            d = self._devices.get(name)
            if d is None:
                return None
            return dict(d, params={k: dict(v) for k, v in d["params"].items()},
                        values=dict(d["values"]))

    def status(self, name):
        """"offline" (not connected), "missing" (the server has no such device),
        or the device's own status: "starting", "ok", "error"."""
        if not self.connected:
            return "offline"
        d = self.device(name)
        return "missing" if d is None else d["status"]

    def set(self, device, param, value):
        """Queue a value for a device param (sent soon; repeats are skipped)."""
        key = (device, param)
        with self._lock:
            if self._sent.get(key) != value:
                self._pending[key] = value

    def send(self, message):
        """Queue any other request for the server, e.g. {"op": "add_device", ...}."""
        with self._lock:
            self._outbox.append(message)

    # --- Background thread --------------------------------------------------------
    def _run(self):
        while True:
            if not self.enabled:
                self.connected = False
                time.sleep(0.2)
                continue
            try:
                with socket.create_connection((HOST, PORT), timeout=1.0) as sock:
                    sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                    sock.settimeout(2.0)
                    with self._lock:
                        self._sent.clear()          # a (re)started server: resend everything
                    self.connected = True
                    self._serve(sock)
            except (OSError, ValueError):
                pass
            self.connected = False
            if self.enabled:
                time.sleep(RECONNECT_SECONDS)

    def _serve(self, sock):
        sock.sendall(b'{"op": "hello"}\n')
        buf = b""
        last_send = 0.0
        while self.enabled:                         # Disconnect: leave (closing the socket)
            readable, _, _ = select.select([sock], [], [], 1.0 / SEND_HZ)
            if readable:
                data = sock.recv(65536)
                if not data:
                    raise ConnectionError("server closed the connection")
                buf += data
                while b"\n" in buf:
                    line, buf = buf.split(b"\n", 1)
                    if line.strip():
                        self._on_message(json.loads(line))
            now = time.monotonic()
            if now - last_send >= 1.0 / SEND_HZ:
                with self._lock:
                    pending, self._pending = self._pending, {}
                    self._sent.update(pending)
                    outbox, self._outbox = self._outbox, []
                messages = outbox + [{"op": "set", "device": d, "param": p, "value": v}
                                     for (d, p), v in pending.items()]
                if messages:
                    sock.sendall("".join(json.dumps(m) + "\n" for m in messages).encode())
                last_send = now
        with self._lock:                            # leaving: still deliver queued requests
            outbox, self._outbox = self._outbox, []     # (e.g. the shutdown sent just before)
        if outbox:
            sock.sendall("".join(json.dumps(m) + "\n" for m in outbox).encode())

    def _on_message(self, msg):
        op = msg.get("op")
        with self._lock:
            if op == "catalog":
                self._devices = {d["name"]: d for d in msg["devices"]}
                self.drivers = msg.get("drivers", [])
                return
            if op == "error":
                self.last_error = msg.get("message", "")
                return
            d = self._devices.get(msg.get("device"))
            if d is None:
                return
            if op == "readings":
                d["values"] = msg["values"]
            elif op == "param":
                if msg["param"] in d["params"]:
                    d["params"][msg["param"]]["value"] = msg["value"]
            elif op == "status":
                d["status"], d["message"] = msg["status"], msg.get("message", "")


def server_running():
    """Is something listening on the server's port right now? (Instant: a
    local connection is accepted or refused straight away.)"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.2)
        return s.connect_ex((HOST, PORT)) == 0


def start_server():
    """Launch instrument_server.py in its own console window (with python.exe,
    even if the canvas itself runs under pythonw.exe)."""
    exe = sys.executable
    console_python = os.path.join(os.path.dirname(exe), "python.exe")
    if os.path.exists(console_python):
        exe = console_python
    flags = getattr(subprocess, "CREATE_NEW_CONSOLE", 0)
    subprocess.Popen([exe, SERVER_SCRIPT], cwd=os.path.dirname(SERVER_SCRIPT), creationflags=flags)


client = InstrumentClient()   # the one the canvas uses; game_loop.py starts it (off until Connect)
