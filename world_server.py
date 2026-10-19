"""world_server.py — the world, running on its own.

Usually launched from the canvas's home screen (Launch world); you can also
run it by hand:

    python world_server.py

It loads Nodes.json, ticks every object 60 times a second whether or not
anyone is looking, saves, and talks to the instrument server (devices). Player
windows join it over the network (see world_link.py) and see the same live
world. What a player does (clicks, drags, panel clicks, menus, placing, …) is
run here, by that player's WorldApp — the same object code a window on its own
runs — and whatever it would show (a text box, a menu, a pick) is sent back to
that player's window to show. Closing a player window never stops the world; closing this window (or
Stop world) does — players are then sent back to their home screen.

It has a small window of its own (and its own taskbar icon) so you can always
see that it's running and who's in it.

It listens on every address of this PC, so windows on other computers on the
same network can join too (no password: it's for people you work with). While
it runs, saves/world.json says where it is, so other computers running the app
from this shared folder find it on their home screen.
"""

import ctypes
import os
import platform
import queue
import socket
import sys
import threading
import time
import tkinter as tk
from tkinter import messagebox

os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")
import pygame  # noqa: E402

import agent_api  # noqa: E402
import canvas  # noqa: E402
import instruments  # noqa: E402
import things  # noqa: E402,F401 — defining the catalog's classes registers them
import world_link  # noqa: E402

TICK_HZ = 60                # world ticks per second
SEND_HZ = 30                # changes sent to players per second
INSTRUMENTS_HZ = 10         # how often the devices' state is passed on (when it changed)
PRESENCE_HZ = 20            # how often players' cursors / views are passed on (when they moved)
# Each player's color (cursor, view box, name tags) — the first one nobody in the world has.
PLAYER_COLORS = [(80, 170, 255), (255, 120, 90), (110, 220, 120), (240, 200, 70), (200, 120, 255),
                 (60, 210, 210), (255, 110, 190), (180, 220, 90), (255, 160, 60), (150, 150, 255)]


_MOUSE = [(0, 0)]                       # the mouse of the player whose action is running


def _mouse_pos():
    return _MOUSE[0]


pygame.mouse.get_pos = _mouse_pos       # object code asks pygame where the mouse is: tell it the player's


class RemoteMode:
    """Stands in for a build mode (train / wall) in a WorldApp: begin() starts
    it in the player's window, which sends the finished object back (add)."""

    active = False

    def __init__(self, app, kind):
        self.app, self.kind = app, kind

    def begin(self, type_name=None):
        self.app.ui(self.kind, **({"type": type_name} if type_name else {}))

    def cancel(self):
        pass


class WorldApp(canvas.App):
    """A player's App inside the world. Object code runs with it exactly as it
    does in a window on its own — app.scene, app.player (this player's
    selection), app.camera (theirs, from the view each message carries),
    app.save() — but whatever it would show is sent to the player's window as a
    "ui" request; the answer comes back (answer) and the callback runs here."""

    def __init__(self, world, conn):
        super().__init__(canvas.Player(world.scene, conn.name, path=None), (1280, 800))
        self.world, self.conn = world, conn
        self.mouse = (0, 0)
        self.train_builder = RemoteMode(self, "train")
        self.wall_builder = RemoteMode(self, "wall")
        self._answers = {}          # token -> callback(answer message); a pending ui request
        self._token = 0

    # --- Running a player's action --------------------------------------------------
    def set_view(self, view):
        if not view:
            return
        x, y, zoom = view.get("camera", (self.camera.offset.x, self.camera.offset.y, self.camera.zoom))
        self.camera.offset.update(x, y)
        self.camera.zoom = zoom
        self.screen_size = tuple(view.get("screen", self.screen_size))
        self.mouse = tuple(view.get("mouse", self.mouse))
        if "selection" in view:
            self.player.select_ids(view["selection"])

    def run(self, action, view=None):
        """Run action() as this player (their camera and mouse), then tell their
        window if it moved their camera or changed their selection. Returns what
        action returns."""
        self.set_view(view)
        cam = self.camera
        before = (cam.offset.x, cam.offset.y, cam.zoom, frozenset(self.player.selected_ids))
        _MOUSE[0] = self.mouse
        result = action()
        if (cam.offset.x, cam.offset.y, cam.zoom) != before[:3] or cam.ride is not None:
            msg = {"camera": [cam.offset.x, cam.offset.y, cam.zoom]}
            if cam.ride is not None:
                msg["ride"] = {"points": [[p.x, p.y] for p in cam.ride.points], "speed": cam.ride.speed}
                cam.ride = None
            self.ui("camera", **msg)
        if frozenset(self.player.selected_ids) != before[3]:
            self.ui("select", ids=sorted(self.player.selected_ids))
        return result

    def ui(self, kind, on_answer=None, **fields):
        """Ask the player's window to show something; on_answer(message) runs
        (as this player) when it answers."""
        msg = {"op": "ui", "kind": kind, **fields}
        if on_answer is not None:
            self._token += 1
            msg["token"] = self._token
            self._answers[self._token] = on_answer
        self.world.send(self.conn, msg)

    def answer(self, msg):
        on_answer = self._answers.pop(msg.get("token"), None)
        if on_answer is not None:
            self.run(lambda: on_answer(msg), msg.get("view"))

    # --- What object code shows: sent to the window -------------------------------------
    def ask(self, prompt, on_submit, *, value="", q_pastes=True):
        self.ui("ask", lambda m: on_submit(str(m.get("value", ""))), prompt=prompt, value=value,
                q_pastes=q_pastes)

    def confirm(self, message, sub, on_confirm, ok_label="Delete"):
        self.ui("confirm", lambda m: on_confirm(), message=message, sub=sub, ok_label=ok_label)

    def context_menu(self, pos, items):
        items = [tuple(item) for item in items]

        def chosen(m):
            i = m.get("index")
            if isinstance(i, int) and 0 <= i < len(items) and items[i][1] is not None:
                items[i][1]()
        self.ui("menu", chosen, pos=list(pos),
                items=[[it[0], it[1] is not None, it[2] if len(it) > 2 else None, *it[3:]] for it in items])

    def start_picking(self, prompt, on_pick, accept=lambda obj: True, origin=None, on_empty=None,
                      on_give=None):
        def picked(m):
            obj = self.scene.by_id.get(m.get("id"))
            if obj is not None and accept(obj):
                if m.get("key") is not None and on_give is not None:
                    on_give(obj, m["key"])               # clicked one of its output dots
                else:
                    on_pick(obj)
            elif m.get("point") is not None and on_empty is not None:
                on_empty(tuple(m["point"]))
        self.ui("pick", picked, prompt=prompt, accept=[o.id for o in self.scene.objects if accept(o)],
                origin=list(origin) if origin is not None else None, empty=on_empty is not None,
                gives=on_give is not None)

    def pick_link(self, obj, port):
        self.ui("wire", end="in", id=obj.id, key=port)       # the window holds the wire (WireMode)

    def pick_target(self, src, key):
        self.ui("wire", end="out", id=src.id, key=key)

    def start_placing(self, factory):
        """Something to place with the mouse: the window holds a see-through copy
        (a sample made here) and sends back where it was dropped."""
        sample = factory(*self.camera.screen_to_world(self.mouse))
        members = sample.members if isinstance(sample, canvas.MachineGroup) else [sample]
        name = sample.place_name() if hasattr(sample, "place_name") else sample.type_name().lower()

        def dropped(m):
            x, y = m.get("point") or (0, 0)
            obj = factory(x, y)
            c = obj.center
            obj.move(x - c[0], y - c[1])
            placed = obj.members if isinstance(obj, canvas.MachineGroup) else [obj]
            self.scene.add_many(placed)
            if m.get("keep") and not obj.SINGLETON:
                self._answers[m.get("token")] = dropped      # still placing (Shift+click)
            else:
                if m.get("keep"):
                    self.ui("place_done")
                self.player.select_many(placed)
        self.ui("place", dropped, sample={"objects": [o.to_dict() for o in members], "name": name})

    def open_inventory(self):
        self.ui("inventory")

    def cancel_modes(self):
        pass                    # the window drops its own modes when it starts a new one


class Connection:
    """One connected window. send() is safe from any thread."""

    def __init__(self, sock, addr):
        self.sock, self.addr = sock, addr
        self.name = None            # set once it's joined (hello)
        self.color = None           # theirs, given when they join
        self.role = "player"        # or "agent" (Claude, through claude_world.py): may only ask (agent_api)
        self.presence = {}          # where they are: {"cursor", "view", "down"} (see world_link)
        self._lock = threading.Lock()

    def send(self, message):
        with self._lock:
            world_link.send_line(self.sock, message)

    def close(self):
        try:
            self.sock.close()
        except OSError:
            pass


class World:
    """The scene plus the players in it. Only the world thread (run) touches
    the scene; network threads just put messages in `inbox`."""

    def __init__(self):
        pygame.font.init()                  # a Button measures its text when it's made (no window needed)
        self.scene = canvas.Scene.load()
        self.mirror = world_link.Mirror(self.scene)
        self.players = []                   # joined Connections (world thread only)
        self.apps = {}                      # Connection -> its player's WorldApp
        self.player_names = []              # a copy for the window (any thread)
        self.inbox = queue.Queue()          # (connection, message)
        self.running = True
        self.started = time.time()
        self._instruments = None            # last devices state sent
        self._presence_sent = None          # last players' presence sent

    # --- Network threads -------------------------------------------------------------
    def listen(self, listener):
        while self.running:
            try:
                sock, addr = listener.accept()
            except OSError:
                return
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            threading.Thread(target=self._read, args=(Connection(sock, addr),), daemon=True).start()

    def _read(self, conn):
        def on_message(msg):
            if msg.get("op") == "status":     # the home screen asking: answer right here
                conn.send({"op": "status", "players": list(self.player_names),
                           "objects": len(self.scene.objects)})
                raise ConnectionAbortedError()
            self.inbox.put((conn, msg))
        try:
            world_link.read_lines(conn.sock, on_message)
        except (OSError, ValueError):
            pass
        self.inbox.put((conn, {"op": "gone"}))

    # --- The world thread ----------------------------------------------------------------
    def run(self):
        last = time.monotonic()
        next_send = next_instruments = next_presence = last
        while self.running:
            now = time.monotonic()
            dt, last = min(now - last, 0.25), now     # a stall (e.g. a long save) doesn't jump the world
            self._handle_inbox()
            self.scene.tick(dt)
            if now >= next_send:
                next_send = now + 1 / SEND_HZ
                changes = self.mirror.changes()
                if changes:
                    changes["clock"] = time.monotonic()     # players line their clocks up with it
                    self.broadcast(changes)
            if now >= next_presence:
                next_presence = now + 1 / PRESENCE_HZ
                presence = self._presence()
                if presence != self._presence_sent:
                    self._presence_sent = presence
                    self.broadcast({"op": "presence", "players": presence})
            if now >= next_instruments:
                next_instruments = now + 1 / INSTRUMENTS_HZ
                snap = instruments.client.snapshot()
                if snap != self._instruments:
                    self._instruments = snap
                    self.broadcast(dict(snap, op="instruments"))
            time.sleep(max(0.0, last + 1 / TICK_HZ - time.monotonic()))
        self.broadcast({"op": "stopping"})
        self.scene.flush()                      # (saves are batched: write what's pending now)
        for p in list(self.players):
            p.close()

    def _handle_inbox(self):
        while True:
            try:
                conn, msg = self.inbox.get_nowait()
            except queue.Empty:
                return
            try:
                self._on_message(conn, msg)
            except Exception as e:                  # a bad message never takes the world down
                canvas.report_error(f"world {msg.get('op')}", e)

    def _on_message(self, conn, msg):
        op = msg.get("op")
        if op == "hello":
            if msg.get("version") != world_link.VERSION:
                conn.send({"op": "refused", "message": "This window's code is different from the world's — "
                                                       "make sure both computers have the latest files, then "
                                                       "restart the world and this window."})
                conn.close()
                return
            conn.role = "agent" if msg.get("role") == "agent" else "player"
            default = agent_api.AGENT_NAME if conn.role == "agent" else "Player"
            conn.name = self._free_name(str(msg.get("name") or default).strip()[:40] or default)
            taken = {p.color for p in self.players}
            conn.color = (canvas.CLAUDE_COLOR if conn.role == "agent" else
                          next((c for c in PLAYER_COLORS if c not in taken),
                               PLAYER_COLORS[len(self.players) % len(PLAYER_COLORS)]))
            pending, objects, states = self.mirror.snapshot()
            if pending:
                self.broadcast(pending)             # the players already here catch up first
            conn.send({"op": "welcome", "name": conn.name, "color": conn.color, "objects": objects, "state": states,
                       "clock": time.monotonic(),
                       "players": self._roster(conn), "instruments": instruments.client.snapshot()})
            self.players.append(conn)
            self.apps[conn] = WorldApp(self, conn)
            self._players_changed()
        elif op in ("bye", "gone"):
            self._leave(conn)
        elif conn.role == "agent":                  # it may only ask (agent_api), and show where it's looking
            if op == "presence":
                self._on_action(self.apps[conn], conn, msg)
            elif "seq" in msg:
                conn.send({"op": "reply", "seq": msg["seq"], **agent_api.answer(self.scene, msg)})
        elif conn in self.apps:
            self._on_action(self.apps[conn], conn, msg)

    def _on_action(self, app, conn, msg):
        """Something a player did: run it as them (WorldApp.run)."""
        op, view, scene = msg.get("op"), msg.get("view"), self.scene
        obj = scene.by_id.get(msg.get("id"))
        if op == "select":
            app.player.select_ids(msg.get("ids") or [])
        elif op == "presence":
            conn.presence = {k: msg.get(k) for k in ("cursor", "view", "down")}
        elif op == "answer":
            app.answer(msg)
        elif op == "click":
            handled = bool(obj is not None and app.run(lambda: obj.on_click(tuple(msg["pos"]), app), view))
            self.send(conn, {"op": "reply", "seq": msg.get("seq"), "handled": handled})
        elif op == "drag" and obj is not None:
            app.run(lambda: obj.on_drag(tuple(msg["pos"]), app), view)
        elif op == "release" and obj is not None:
            app.run(lambda: obj.on_release(tuple(msg["pos"]), app), view)
        elif op == "panel" and obj is not None:
            callback = app.run(lambda: canvas.panel_callback(obj, app, msg.get("path") or []), view)
            if callback is not None:
                app.run(lambda: callback(app), view)
        elif op == "menu":
            objs = [scene.by_id[i] for i in msg.get("ids", []) if i in scene.by_id]
            pos = tuple(msg.get("pos", (0, 0)))
            if len(objs) == 1:
                app.run(lambda: app.object_menu(objs[0], pos), view)
            elif objs:
                app.run(lambda: app.context_menu(pos, app.selection_menu_items(objs)), view)
        elif op == "command":
            app.run(lambda: app.menu_command(str(msg.get("command", ""))), view)
        elif op == "create" and msg.get("type") in canvas.OBJECT_TYPES:
            app.run(lambda: canvas.OBJECT_TYPES[msg["type"]].create(app), view)
        elif op == "create_machine" and isinstance(msg.get("machine"), dict):
            app.run(lambda: canvas.MachineTile(msg["machine"]).create(app), view)
        elif op == "choose_take":
            target, src = scene.by_id.get(msg.get("target")), scene.by_id.get(msg.get("src"))
            if target is not None and src is not None:
                app.run(lambda: app.choose_take(target, src, msg.get("key"), port=msg.get("port")), view)
        elif op == "unlink":
            obj = scene.by_id.get(msg.get("id"))
            if obj is not None and msg.get("port") in obj.links:
                app.run(lambda: obj.unlink(msg["port"], app), view)
        elif op == "add":
            objs = [canvas.object_from_dict(d) for d in msg.get("objects", [])
                    if isinstance(d, dict) and d.get("id") not in scene.by_id]
            if objs:
                scene.add_many(objs)
                if msg.get("select"):
                    app.run(lambda: app.player.select_many(objs), view)
        elif op == "move":
            for i in msg.get("ids", []):
                o = scene.by_id.get(i)
                if o is not None and o.movable:
                    o.move(float(msg.get("dx", 0)), float(msg.get("dy", 0)))
        elif op == "moved":
            scene.save()

    def _free_name(self, name):
        taken = {p.name for p in self.players}
        if name not in taken:
            return name
        return next(f"{name} {i}" for i in range(2, 1000) if f"{name} {i}" not in taken)

    def _roster(self, extra=None):
        return [{"name": p.name, "color": p.color} for p in self.players + ([extra] if extra else [])]

    def _presence(self):
        """Every player: name, color, where their cursor and window are, and what
        they have selected — for the others to draw."""
        return [dict(p.presence, name=p.name, color=p.color,
                     selection=sorted(self.scene.selections.get(p.name, ())))
                for p in self.players]

    def _leave(self, conn):
        conn.close()
        if conn not in self.players:
            return
        self.players.remove(conn)
        self.apps.pop(conn, None)
        self.scene.selections.pop(conn.name, None)
        self._players_changed()
        if not self.players:                        # nobody's watching: safety hooks (a laser's emission)
            for o in list(self.scene.objects):
                try:
                    o.on_no_players()
                except Exception as e:
                    canvas.report_error(f"{o.TYPE}.on_no_players", e)

    def _players_changed(self):
        self.player_names = [p.name for p in self.players]
        self.broadcast({"op": "players", "players": self._roster()})

    def broadcast(self, message):
        for p in list(self.players):
            self.send(p, message)

    def send(self, conn, message):
        try:
            conn.send(message)
        except OSError:
            self.inbox.put((conn, {"op": "gone"}))

    def stop(self):
        self.running = False


# ---- The window ---------------------------------------------------------------------------
BG, FG, DIM, GREEN = "#19191e", "#e6e6eb", "#8c8c9b", "#5adc78"


WORLD_ICONS = ("world.ico", "world.png")   # the world window's own icon, if one is in assets/


def set_icon(root):
    """world.ico / world.png from assets/ (the canvas's icon.ico until there is one)."""
    for name in WORLD_ICONS + ("icon.ico",):
        path = os.path.join(canvas.ASSETS_DIR, name)
        if not os.path.exists(path):
            continue
        try:
            if name.endswith(".ico"):
                root.iconbitmap(default=path)       # title bar + taskbar
            else:
                root._icon = tk.PhotoImage(file=path)   # kept, or Tk drops the image
                root.iconphoto(True, root._icon)
            return
        except tk.TclError:
            continue


def run_window(world, listener, world_thread):
    root = tk.Tk()
    root.title(f"{canvas.APP_NAME} — World")
    root.configure(bg=BG)
    root.resizable(False, False)
    set_icon(root)
    pad = {"padx": 18, "anchor": "w"}
    tk.Label(root, text="● The world is running", fg=GREEN, bg=BG,
             font=("Segoe UI", 13, "bold")).pack(pady=(16, 2), **pad)
    where = ", ".join([world_link.THIS_PC] + world_link.this_pc_addresses()[:1])
    tk.Label(root, text=f"Others on this network can join: {where}", fg=DIM, bg=BG,
             font=("Segoe UI", 9)).pack(**pad)
    players = tk.Label(root, fg=FG, bg=BG, font=("Segoe UI", 10), justify="left")
    players.pack(pady=(12, 0), **pad)
    details = tk.Label(root, fg=DIM, bg=BG, font=("Segoe UI", 9), justify="left")
    details.pack(pady=(6, 0), **pad)
    tk.Label(root, text="Closing a player window leaves the world running.\n"
                        "Closing this window stops it.", fg=DIM, bg=BG,
             font=("Segoe UI", 9), justify="left").pack(pady=(12, 0), **pad)

    def stop():
        names = list(world.player_names)
        if names and not messagebox.askyesno(
                "Stop the world?",
                f"{len(names)} player{'' if len(names) == 1 else 's'} in it ({', '.join(names)}) "
                "will be sent back to the home screen.", parent=root):
            return
        world.stop()
        try:
            listener.close()
        except OSError:
            pass
        world_thread.join(timeout=3)
        root.destroy()

    tk.Button(root, text="Stop world", command=stop, bg="#b43c3c", fg="white", activebackground="#d24646",
              activeforeground="white", relief="flat", font=("Segoe UI", 10, "bold"),
              padx=14, pady=4).pack(pady=16, padx=18, anchor="e")
    root.protocol("WM_DELETE_WINDOW", stop)

    def refresh():
        names = world.player_names
        players.config(text=("Players: " + ", ".join(names)) if names else "Players: nobody yet")
        up = int(time.time() - world.started)
        details.config(text=f"{len(world.scene.objects)} objects  ·  running {up // 3600}h {up // 60 % 60}m"
                            f"  ·  port {world_link.PORT}")
        if not world_thread.is_alive() and world.running:      # the world thread died: say so
            messagebox.showerror("The world stopped", "Something went wrong — see errors.log.", parent=root)
            root.destroy()
            return
        root.after(500, refresh)

    refresh()
    root.mainloop()


def refuse(title, message):
    root = tk.Tk()
    root.withdraw()
    messagebox.showerror(title, message)
    root.destroy()
    sys.exit(1)


def main():
    if platform.system() == "Windows":      # its own taskbar group, apart from the player windows
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("Yaakov.CanvasWorld")
    if world_link.solo_running():
        refuse("Can't start the world", "A canvas window is open working on its own (not in the world).\n"
                                        "Close it first, so only one program writes the canvas.")
    host, _status, pc = world_link.find_world()
    if host is not None and pc:
        refuse("The world is already running", f"It's running on {pc}. Join it from a canvas window's "
                                               "home screen, so only one world writes the canvas.")
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        listener.bind((world_link.LISTEN_HOST, world_link.PORT))   # every address: other PCs can join
    except OSError:
        refuse("The world is already running", "Join it from a canvas window's home screen.")
    listener.listen()
    world_link.announce_world()

    world = World()

    def on_crash(exc_type, exc, tb):
        canvas.report_error("world crash", exc)
        try:
            world.scene.flush()
        except Exception:
            pass
        sys.__excepthook__(exc_type, exc, tb)
    sys.excepthook = on_crash
    threading.excepthook = lambda args: on_crash(args.exc_type, args.exc_value, args.exc_traceback)

    instruments.client.start()          # devices: only the world talks to the instrument server
    world_thread = threading.Thread(target=world.run, name="world", daemon=True)
    world_thread.start()
    threading.Thread(target=world.listen, args=(listener,), name="listen", daemon=True).start()
    try:
        run_window(world, listener, world_thread)
    finally:
        world_link.unannounce_world()


if __name__ == "__main__":
    main()
