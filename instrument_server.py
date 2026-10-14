"""instrument_server.py — runs the device drivers and talks to the Canvas app.

Usually launched from the canvas (the Instrument server object's Connect
button opens it in its own console window). You can also run it by hand:

    python instrument_server.py

It opens every device listed in devices.json (each through its driver in
drivers/), keeps those connections open — so restarting the canvas never
resets an Arduino, and devices hold their last value when the canvas closes —
and serves the canvas (or any other script) over a local socket.

LOCAL ONLY, by design: it listens on 127.0.0.1, so only programs on this PC
can connect. The devices are plugged into this PC.

Protocol: JSON, one object per line, both ways.
  client -> server
    {"op": "hello"}                                   -> replies with "catalog"
    {"op": "set", "device": "arduino", "param": "angle", "value": 120}
    {"op": "add_device", "name": "arm", "driver": "fake_arduino", "options": {"port": "COM4"}}
    {"op": "remove_device", "name": "arm"}
    {"op": "configure_device", "name": "servo", "options": {"port": "COM11"}}   (restarts it)
    {"op": "action", "device": "zaber", "name": "home"}       (runs a driver action, e.g. home / stop)
    {"op": "shutdown"}                                -> closes every device and exits
  server -> client
    {"op": "catalog", "devices": [{name, driver, status, message,
                                   params: {angle: {min, max, unit, value}},
                                   readings: {position: {unit}}, values: {...}, options,
                                   actions: ["home", "stop"]}],
                      "drivers": [{module, title, params: [...], readings: [...], error}],
                      "ports": [{device: "COM11", description, arduino: true}]}
    {"op": "readings", "device": ..., "values": {"position": 87.3, ...}}   (each poll)
    {"op": "param", "device": ..., "param": ..., "value": ...}           (after a set)
    {"op": "status", "device": ..., "status": "ok"|"starting"|"error", "message": ...}
    {"op": "error", "message": ...}                                      (bad request)

Each device runs on its own worker thread: it applies the newest requested
value per param (older ones are dropped — a dragged slider sends many), then
polls read() at the driver's poll_hz. If a driver fails, the device shows
"error" and is retried every few seconds, with the driver module re-imported,
so you can fix a driver while the server keeps running. Devices added or
removed from the canvas are written back to devices.json.
"""

import importlib
import json
import os
import re
import socket
import sys
import threading
import time
import traceback

HOST = "127.0.0.1"
PORT = int(os.environ.get("CANVAS_INSTRUMENT_PORT", 47800))   # keep in sync with instruments.py
                                       # (tests set the variable so they never touch the real server)
HERE = os.path.dirname(os.path.abspath(__file__))
CONFIG = os.path.join(HERE, "devices.json")
DRIVERS_DIR = os.path.join(HERE, "drivers")
RETRY_SECONDS = 3
NO_CLIENTS = "__no_clients__"          # internal action: the last client (canvas) went away

sys.path.insert(0, HERE)               # so "drivers" imports from this folder


for _stream in (sys.stdout, sys.stderr):        # a console that can't show a character (λ, µ…)
    try:                                        # prints a stand-in instead of raising
        _stream.reconfigure(errors="replace")
    except (AttributeError, ValueError):
        pass


def log(message):
    # Never let logging take a device down: this runs inside error handlers.
    try:
        print(time.strftime("%H:%M:%S"), message, flush=True)
    except Exception:
        pass


def load_driver_module(name):
    module = importlib.import_module(f"drivers.{name}")
    return importlib.reload(module)     # pick up edits every time


def list_ports():
    """The serial ports on this PC (empty if pyserial isn't installed)."""
    try:
        from serial.tools import list_ports as lp
    except ImportError:
        return []
    return [{"device": p.device, "description": p.description,
             "arduino": p.vid in (0x2341, 0x2A03)} for p in sorted(lp.comports(), key=lambda p: p.device)]


def list_drivers():
    """Every driver module in drivers/ (not base.py), described for the canvas."""
    out = []
    for filename in sorted(os.listdir(DRIVERS_DIR)):
        name, ext = os.path.splitext(filename)
        if ext != ".py" or name.startswith("_") or name == "base":
            continue
        entry = {"module": name, "title": name, "params": [], "readings": [], "error": ""}
        try:
            module = load_driver_module(name)
            cls = module.DRIVER
            doc = (module.__doc__ or "").strip().splitlines()
            if doc:
                entry["title"] = doc[0].split("—", 1)[-1].strip() if "—" in doc[0] else doc[0]
            entry["params"], entry["readings"] = list(cls.params), list(cls.readings)
        except Exception as e:
            entry["error"] = f"{type(e).__name__}: {e}"
        out.append(entry)
    return out


class DeviceRunner:
    """One configured device: its driver, a worker thread, and the newest
    value requested for each param."""

    def __init__(self, name, driver, options, server):
        self.name, self.driver_name, self.options = name, driver, options or {}
        self.server = server
        self.driver = None
        self.driver_values = None         # param values to start from (kept across restarts)
        self.status, self.message = "starting", ""
        self.values = {}                  # latest readings
        self._pending = {}                # param -> newest requested value
        self._actions = []                # driver actions requested (run in order)
        self._lock = threading.Lock()
        self._wake = threading.Event()
        self._stop = threading.Event()

    def config(self):
        return {"name": self.name, "driver": self.driver_name, "options": self.options}

    def describe(self):
        # Status first: the worker sets it to "ok" only after the driver is open, so
        # reading it before the driver means "ok" always comes with the driver's params.
        status, message = self.status, self.message
        d = self.driver                   # params / readings may be set per instance (e.g. a travel range)
        params = {}
        if d is not None:
            for pname, p in d.params.items():
                params[pname] = dict(p.describe(), value=d.values.get(pname))
        return {"name": self.name, "driver": self.driver_name, "status": status,
                "message": message, "params": params, "options": dict(self.options),
                "readings": {r: spec.describe() for r, spec in d.readings.items()} if d else {},
                "actions": list(getattr(d, "actions", [])) if d else [],
                "values": dict(self.values)}

    def request(self, param, value):
        with self._lock:
            self._pending[param] = value
        self._wake.set()

    def request_action(self, name):
        with self._lock:
            self._actions.append(name)
        self._wake.set()

    def stop(self):
        self._stop.set()
        self._wake.set()

    def _warn(self, message):
        """Show (or clear) a message without changing the status — the device is still running."""
        if message:
            log(f"{self.name}: {message}")
        if message != self.message:
            self._set_status(self.status, message)

    def _set_status(self, status, message=""):
        self.status, self.message = status, message
        self.server.broadcast({"op": "status", "device": self.name, "status": status, "message": message})

    def _close(self):
        try:
            if self.driver is not None:
                self.driver.close()
        except Exception:
            pass

    def run(self):
        while not self._stop.is_set():
            try:
                self._set_status("starting")
                if self.driver is not None:       # a retry: keep the last values (no jump to defaults)
                    self.driver_values = dict(self.driver.values)
                self.driver = load_driver_module(self.driver_name).DRIVER(**self.options)
                if self.driver_values:
                    self.driver.values.update({k: v for k, v in self.driver_values.items()
                                               if k in self.driver.params})
                self.driver.open()
                self.status, self.message = "ok", ""
                self.server.broadcast_catalog()      # status + params/readings arrive together
                log(f"{self.name}: running ({self.driver_name})")
                self._loop()
            except Exception as e:
                log(f"{self.name}: ERROR {type(e).__name__}: {e}\n{traceback.format_exc()}")
                self._set_status("error", f"{type(e).__name__}: {e}")
                self._close()
                self._stop.wait(RETRY_SECONDS)
        self._close()
        log(f"{self.name}: stopped")

    def _loop(self):
        period = 1.0 / max(1, self.driver.poll_hz)
        next_poll = time.monotonic()
        while not self._stop.is_set():
            with self._lock:
                pending, self._pending = self._pending, {}
                actions, self._actions = self._actions, []
            for param, value in pending.items():
                if param not in self.driver.params:
                    continue
                try:
                    applied = self.driver.apply(param, value)
                except Exception as e:            # a refused move etc.: report it, keep running
                    self._warn(f"{param}: {type(e).__name__}: {e}")
                    continue
                self.server.broadcast({"op": "param", "device": self.name, "param": param,
                                       "value": applied})
            for name in actions:
                if name == NO_CLIENTS:            # the canvas closed / crashed: let the driver react
                    hook = getattr(self.driver, "on_no_clients", None)
                    if hook is not None:
                        try:
                            hook()
                        except Exception as e:
                            self._warn(f"on_no_clients: {type(e).__name__}: {e}")
                    continue
                if name not in getattr(self.driver, "actions", []):
                    continue
                try:
                    getattr(self.driver, name)()
                    self._warn("")                 # an action that worked clears an old warning
                except Exception as e:
                    self._warn(f"{name}: {type(e).__name__}: {e}")
            now = time.monotonic()
            if now >= next_poll:
                self.values = self.driver.read() or {}
                self.server.broadcast({"op": "readings", "device": self.name, "values": self.values})
                next_poll = now + period
            self._wake.wait(max(0.0, next_poll - time.monotonic()))
            self._wake.clear()


class Client:
    """One connected program. send() is safe from any thread."""

    def __init__(self, sock, addr):
        self.sock, self.addr = sock, addr
        self._send_lock = threading.Lock()

    def send(self, message):
        data = (json.dumps(message) + "\n").encode()
        with self._send_lock:
            self.sock.sendall(data)


class Server:
    def __init__(self, config):
        self.clients = set()
        self._clients_lock = threading.Lock()
        self._catalog_lock = threading.Lock()   # build + send catalogs one at a time, in order
        self.devices = {}
        for d in config.get("devices", []):
            self.devices[d["name"]] = DeviceRunner(d["name"], d["driver"], d.get("options"), self)

    def catalog(self):
        return {"op": "catalog", "devices": [r.describe() for r in self.devices.values()],
                "drivers": list_drivers(), "ports": list_ports()}

    def broadcast(self, message):
        with self._clients_lock:
            clients = list(self.clients)
        for c in clients:
            try:
                c.send(message)
            except OSError:
                self._drop(c)

    def broadcast_catalog(self):
        # Built and sent under one lock, so an older catalog (from another
        # thread) can never arrive after a newer one.
        with self._catalog_lock:
            self.broadcast(self.catalog())

    def save_config(self):
        with open(CONFIG, "w", encoding="utf-8") as f:
            json.dump({"devices": [r.config() for r in self.devices.values()]}, f, indent=2)

    def _drop(self, client):
        last = False
        with self._clients_lock:
            if client in self.clients:
                self.clients.discard(client)
                log(f"client {client.addr[1]} disconnected")
                last = not self.clients
        if last:                                  # nobody's watching: e.g. a laser turns its emission off
            log("no clients left")
            for runner in list(self.devices.values()):
                runner.request_action(NO_CLIENTS)
        try:
            client.sock.close()
        except OSError:
            pass

    def handle(self, client):
        buf = b""
        try:
            client.send(self.catalog())
            while True:
                data = client.sock.recv(65536)
                if not data:
                    break
                buf += data
                while b"\n" in buf:
                    line, buf = buf.split(b"\n", 1)
                    if line.strip():
                        self._on_message(client, line)
        except OSError:
            pass
        finally:
            self._drop(client)

    def start_device(self, runner):
        self.devices[runner.name] = runner
        threading.Thread(target=runner.run, name=runner.name, daemon=True).start()

    def _on_message(self, client, line):
        try:
            msg = json.loads(line)
            op = msg.get("op")
            if op == "hello":
                client.send(self.catalog())
            elif op == "set":
                runner = self.devices.get(msg.get("device"))
                if runner is None:
                    raise ValueError(f"no device {msg.get('device')!r}")
                runner.request(msg["param"], float(msg["value"]))
            elif op == "action":
                runner = self.devices.get(msg.get("device"))
                if runner is None:
                    raise ValueError(f"no device {msg.get('device')!r}")
                runner.request_action(str(msg["name"]))
            elif op == "add_device":
                name, driver = str(msg["name"]).strip(), str(msg["driver"])
                if not re.fullmatch(r"[A-Za-z0-9_\- ]{1,40}", name):
                    raise ValueError("device names: letters, digits, spaces, - and _ (max 40)")
                if name in self.devices:
                    raise ValueError(f"there's already a device called {name!r}")
                if driver not in {d["module"] for d in list_drivers()}:
                    raise ValueError(f"no driver {driver!r}")
                self.start_device(DeviceRunner(name, driver, msg.get("options") or {}, self))
                self.save_config()
                log(f"added {name} ({driver})")
                self.broadcast_catalog()
            elif op == "remove_device":
                runner = self.devices.pop(msg.get("name"), None)
                if runner is None:
                    raise ValueError(f"no device {msg.get('name')!r}")
                runner.stop()
                self.save_config()
                log(f"removed {runner.name}")
                self.broadcast_catalog()
            elif op == "configure_device":
                old = self.devices.get(msg.get("name"))
                if old is None:
                    raise ValueError(f"no device {msg.get('name')!r}")
                old.stop()
                options = dict(old.options, **(msg.get("options") or {}))
                runner = DeviceRunner(old.name, old.driver_name, options, self)
                runner.driver_values = dict(old.driver.values) if old.driver else None
                self.start_device(runner)
                self.save_config()
                log(f"reconfigured {old.name}: {options}")
                self.broadcast_catalog()
            elif op == "shutdown":
                log("shutting down (asked by a client)")
                for runner in self.devices.values():
                    runner.stop()
                time.sleep(0.3)                       # let drivers close their ports
                os._exit(0)
            else:
                raise ValueError(f"unknown op {op!r}")
        except (ValueError, KeyError, TypeError) as e:
            client.send({"op": "error", "message": str(e)})

    def serve(self):
        for runner in list(self.devices.values()):
            self.start_device(runner)
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.bind((HOST, PORT))      # 127.0.0.1: this PC only
        listener.listen()
        log(f"instrument server listening on {HOST}:{PORT} - devices: {', '.join(self.devices) or 'none'}")
        while True:
            sock, addr = listener.accept()
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            client = Client(sock, addr)
            with self._clients_lock:
                self.clients.add(client)
            log(f"client {addr[1]} connected")
            threading.Thread(target=self.handle, args=(client,), daemon=True).start()


def load_config():
    if not os.path.exists(CONFIG):                 # none yet: start empty (canvas objects add devices)
        with open(CONFIG, "w", encoding="utf-8") as f:
            json.dump({"devices": []}, f, indent=2)
        log("created an empty devices.json")
    with open(CONFIG, "r", encoding="utf-8") as f:
        return json.load(f)


if __name__ == "__main__":
    try:
        Server(load_config()).serve()
    except OSError as e:
        log(f"can't listen on {HOST}:{PORT} - is the server already running? ({e})")
        time.sleep(3)
        sys.exit(1)
    except KeyboardInterrupt:
        log("stopped")
