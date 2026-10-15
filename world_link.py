"""world_link.py — the link between the world (world_server.py) and players.

The world runs in its own process: it owns the objects, ticks them, saves
Nodes.json and talks to the instrument server. A player window joins it over a
local socket, gets a copy of every object, and from then on a stream of
changes — and draws its copy. Players never tick the world themselves, so two
windows can never drift apart (agents, neurons, counters all move only in the
world).

Mirroring is generic, so no object type needs its own networking code: the
world sends each object's *instance attributes* — everything in vars(obj) that
can be written as JSON (numbers, text, lists, dicts, vectors, …), skipping
what can't (fonts, surfaces, compiled equations) and the view's own flags
(VIEW_ATTRS). After the first copy only attributes whose value changed are sent.

Local only for now: the world listens on 127.0.0.1.

Protocol: JSON, one object per line, both ways.
  player -> world
    {"op": "status"}                          -> {"op": "status", players, objects} (then it hangs up)
    {"op": "hello", "name": "Yaakov", "version": 1}
                                              -> {"op": "welcome", name, objects: [to_dict],
                                                  state: {id: attrs}, players, instruments}
    {"op": "select", "ids": [...]}            what this player has selected (the world reacts:
                                              an agent holds still while anyone has it selected)
    {"op": "bye"}
    {"op": "presence", "cursor": [x, y] or None, "view": [x, y, w, h], "down": bool}
                                              where this window is, in world units (~20 a second,
                                              when it moved): its cursor, the area it shows
    Changes — the world runs them with its App for this player (world_server.WorldApp). Each
    carries "view": {"camera": [x, y, zoom], "screen": [w, h], "mouse": [x, y]}, so object
    code there sees this window's camera and mouse:
    {"op": "click", "id", "pos", "seq"}       -> {"op": "reply", "seq", "handled": bool}
    {"op": "drag" / "release", "id", "pos"}    after a handled click
    {"op": "panel", "id", "path"}             a panel click (canvas.panel_callback)
    {"op": "menu", "ids", "pos"}              right-click / ⋯ menu
    {"op": "command", "command"}              menu bar (App.menu_command)
    {"op": "create", "type"} / {"op": "create_machine", "machine"}    E menu / Add menu
    {"op": "choose_take", "target", "src", "key", "port"}            a wire dropped on an input
    {"op": "add", "objects": [to_dict], "select"}                    made here (copy, train, wall)
    {"op": "move", "ids", "dx", "dy"} / {"op": "moved"}               arrow keys / let go (save)
    {"op": "answer", "token", ...}            the answer to a "ui" request (value / index / id /
                                              point, keep)
  world -> player
    {"op": "changes", "add": [{"object": to_dict, "state": attrs}], "remove": [ids],
                      "state": {id: {attr: value}}}           (~30 a second, only what changed)
    {"op": "players", "players": [{"name", "color"}]}
    {"op": "presence", "players": [{"name", "color", "cursor", "view", "down", "selection"}]}
                                              (everyone's, ~20 a second when something moved)
    {"op": "instruments", ...}                (the instrument server's devices, as the world sees them)
    {"op": "stopping"}                        (the world is closing)
    {"op": "refused", "message": ...}
    {"op": "ui", "kind", "token", ...}        show something for object code running in the world:
        kind ask (prompt, value) / confirm (message, sub, ok_label) / menu (pos, items:
        [label, clickable, style, detail?]) / pick (prompt, accept: [ids], origin, empty) /
        place (sample: {objects, name}) / place_done / inventory / train / wall —
        answered with {"op": "answer", "token"}; and, with no token, camera (camera, ride?)
        and select (ids): the world's App moved this player's camera / selection
"""

import getpass
import json
import os
import queue
import socket
import subprocess
import sys
import threading

import pygame

HOST = "127.0.0.1"
PORT = int(os.environ.get("CANVAS_WORLD_PORT", 47810))
SOLO_PORT = PORT + 1        # held (bound) while a canvas works on its own — the world won't start then
VERSION = 1
HERE = os.path.dirname(os.path.abspath(__file__))
WORLD_SCRIPT = os.path.join(HERE, "world_server.py")

VIEW_ATTRS = {"scene", "is_hovered", "is_clicked", "is_selected"}   # never sent: the player's own


# ---- Mirroring: object attributes <-> JSON ---------------------------------------
class _Skip(Exception):
    """An attribute that can't be sent (a font, a surface, a compiled equation)."""


def encode(value):
    if value is None or isinstance(value, (bool, str)):
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float):          # (numpy floats too)
        return float(value)
    if isinstance(value, pygame.Vector2):
        return {"__v2": [value.x, value.y]}
    if isinstance(value, tuple):
        return {"__t": [encode(v) for v in value]}
    if isinstance(value, (set, frozenset)):
        return {"__s": [encode(v) for v in value]}
    if isinstance(value, list):
        return [encode(v) for v in value]
    if isinstance(value, dict):
        if not all(isinstance(k, str) for k in value):
            raise _Skip()
        return {k: encode(v) for k, v in value.items()}
    raise _Skip()


def decode(value):
    if isinstance(value, list):
        return [decode(v) for v in value]
    if isinstance(value, dict):
        if len(value) == 1:
            (k, v), = value.items()
            if k == "__v2":
                return pygame.Vector2(v)
            if k == "__t":
                return tuple(decode(x) for x in v)
            if k == "__s":
                return set(decode(x) for x in v)
        return {k: decode(v) for k, v in value.items()}
    return value


def object_state(obj):
    """Everything about obj a player needs to draw it: {attr: JSON value}."""
    out = {}
    for key, value in vars(obj).items():
        if key in VIEW_ATTRS:
            continue
        try:
            out[key] = encode(value)
        except _Skip:
            pass
    return out


def apply_state(obj, state):
    """Set obj's attributes from the world (straight into its __dict__, so
    property setters don't run — the world already did whatever they do)."""
    obj.__dict__.update({k: decode(v) for k, v in state.items() if k not in VIEW_ATTRS})


class Mirror:
    """The world's side: remembers what each object looked like when last
    sent, so changes() returns only what's new. One per world (every player
    gets the same changes; a new player gets a full copy from snapshot())."""

    def __init__(self, scene):
        self.scene = scene
        self._sent = {}         # id -> (state as JSON text, state dict)

    def _state(self, obj):
        state = object_state(obj)
        return json.dumps(state, sort_keys=True), state

    def snapshot(self):
        """For a player joining now: (changes, objects, states). `changes` is
        what the players already in the world haven't been sent yet (send it to
        them first); objects / states are every object in full, as of now."""
        pending = self.changes()
        objects = [o.to_dict() for o in self.scene.objects]
        states = {o.id: self._sent[o.id][1] for o in self.scene.objects}
        return pending, objects, states

    def changes(self):
        """What changed since the last call: a "changes" message, or None."""
        add, state_changes = [], {}
        current = set()
        for o in self.scene.objects:
            current.add(o.id)
            text, state = self._state(o)
            old = self._sent.get(o.id)
            if old is None:
                add.append({"object": o.to_dict(), "state": state})
            elif old[0] != text:
                diff = {k: v for k, v in state.items() if old[1].get(k, _MISSING) != v}
                if diff:
                    state_changes[o.id] = diff
            self._sent[o.id] = (text, state)
        removed = [i for i in self._sent if i not in current]
        for i in removed:
            del self._sent[i]
        if not (add or removed or state_changes):
            return None
        return {"op": "changes", "add": add, "remove": removed, "state": state_changes}


_MISSING = object()


def apply_changes(scene, msg, object_from_dict):
    """The player's side: bring its copy of the world up to date."""
    for item in msg.get("add", []):
        existing = scene.by_id.get(item["object"].get("id"))
        if existing is not None:            # already have it: just catch up
            apply_state(existing, item["state"])
            continue
        obj = object_from_dict(item["object"])
        apply_state(obj, item["state"])
        scene.add_many([obj])
    gone = [scene.by_id[i] for i in msg.get("remove", []) if i in scene.by_id]
    if gone:
        scene.delete_many(gone)
    for oid, state in msg.get("state", {}).items():
        obj = scene.by_id.get(oid)
        if obj is not None:
            apply_state(obj, state)


# ---- Sending lines ----------------------------------------------------------------------
def send_line(sock, message):
    sock.sendall((json.dumps(message) + "\n").encode())


def read_lines(sock, on_message):
    """Read JSON lines until the other side hangs up."""
    buf = b""
    while True:
        data = sock.recv(65536)
        if not data:
            return
        buf += data
        while b"\n" in buf:
            line, buf = buf.split(b"\n", 1)
            if line.strip():
                on_message(json.loads(line))


# ---- The player's side ----------------------------------------------------------------------
def world_status(timeout=0.3):
    """{"players": [...], "objects": n} if a world is running on this PC, else None."""
    try:
        with socket.create_connection((HOST, PORT), timeout=timeout) as sock:
            sock.settimeout(1.0)
            send_line(sock, {"op": "status"})
            replies = []
            read_lines(sock, replies.append)
            return next((r for r in replies if r.get("op") == "status"), None)
    except (OSError, ValueError):
        return None


def solo_running():
    """Is a canvas working on its own (not in the world) right now?"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.2)
        return s.connect_ex((HOST, SOLO_PORT)) == 0


def hold_solo_lock():
    """Mark this canvas as working on its own until it closes (a bound port is
    released even if it crashes). Returns the socket to keep, or None if
    another canvas already holds it."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind((HOST, SOLO_PORT))
        s.listen()
        return s
    except OSError:
        s.close()
        return None


def launch_world():
    """Start world_server.py in its own window (pythonw: no console)."""
    exe = sys.executable
    windowed = os.path.join(os.path.dirname(exe), "pythonw.exe")
    if os.path.exists(windowed):
        exe = windowed
    subprocess.Popen([exe, WORLD_SCRIPT], cwd=HERE)


def default_name():
    try:
        return getpass.getuser() or "Player"
    except Exception:
        return "Player"


class WorldLink:
    """A player's connection to the world. A background thread reads
    messages into `inbox`; the game loop takes them out (poll()) and applies
    them on its own thread, so the world copy is only touched there."""

    def __init__(self, name):
        self.name = name                # the world may change it ("Yaakov 2") — see welcome
        self.inbox = queue.Queue()
        self.closed = False
        self.reason = ""                # why it closed, for the player
        self.players = []               # names of everyone in the world (from the welcome)
        self.roster = []                # everyone, with their colors
        self.color = (90, 220, 120)     # this player's color (the world gives it)
        self._sock = None
        self._lock = threading.Lock()
        self._seq = 0
        self._waiting = {}              # seq -> [Event, reply] for request()

    def connect(self, timeout=3.0):
        """Join: returns the welcome message, or raises OSError / ValueError."""
        sock = socket.create_connection((HOST, PORT), timeout=timeout)
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        send_line(sock, {"op": "hello", "name": self.name, "version": VERSION})
        sock.settimeout(10.0)
        buf = b""
        while b"\n" not in buf:                 # the welcome (the whole world) comes first
            data = sock.recv(1 << 20)
            if not data:
                raise ConnectionError("the world hung up")
            buf += data
        line, buf = buf.split(b"\n", 1)
        welcome = json.loads(line)
        if welcome.get("op") == "refused":
            sock.close()
            raise ConnectionError(welcome.get("message", "the world said no"))
        self.name = welcome.get("name", self.name)
        self.players = [p.get("name") for p in welcome.get("players", [])]
        self.roster = list(welcome.get("players", []))     # [{"name", "color"}], kept up to date by the loop
        self.color = tuple(welcome.get("color") or (90, 220, 120))
        sock.settimeout(None)
        self._sock = sock
        threading.Thread(target=self._read, args=(buf,), daemon=True, name="world-link").start()
        return welcome

    def _read(self, buf):
        try:
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                if line.strip():
                    self._arrived(json.loads(line))
            read_lines(self._sock, self._arrived)
            self.reason = self.reason or "The world closed the connection."
        except (OSError, ValueError) as e:
            self.reason = self.reason or f"Lost the world: {e}"
        self.closed = True
        for waiting in list(self._waiting.values()):
            waiting[0].set()                    # nobody waits on a world that's gone

    def _arrived(self, msg):
        if msg.get("op") == "reply":            # an answer to request(): hand it to whoever waits
            waiting = self._waiting.get(msg.get("seq"))
            if waiting is not None:
                waiting[1] = msg
                waiting[0].set()
            return
        self.inbox.put(msg)

    def request(self, message, timeout=2.0):
        """Send and wait for the world's reply (None if none comes in time).
        Other messages keep arriving in the inbox meanwhile."""
        with self._lock:
            self._seq += 1
            seq = self._seq
        waiting = self._waiting[seq] = [threading.Event(), None]
        self.send(dict(message, seq=seq))
        waiting[0].wait(timeout)
        return self._waiting.pop(seq, waiting)[1]

    def poll(self):
        """Every message that's arrived, oldest first."""
        out = []
        while True:
            try:
                out.append(self.inbox.get_nowait())
            except queue.Empty:
                return out

    def send(self, message):
        if self.closed or self._sock is None:
            return
        try:
            with self._lock:
                send_line(self._sock, message)
        except OSError:
            self.closed = True

    def close(self):
        self.send({"op": "bye"})
        self.closed = True
        try:
            self._sock.close()
        except (OSError, AttributeError):
            pass
