# Multiplayer — feasibility study

> **Study written 2026-10-14. Built since:** step 1, the world / player split (ARCHITECTURE.md,
> *The world and the player's view*), and — reordered ahead of the command layer — the world
> as its own program with player windows on this PC (ARCHITECTURE.md, *The world server and
> players*; table step 4), and players making changes (table step 2 — done as "the world
> runs the same object code for you, and sends its dialogs to your window", not one command
> per change, so object types didn't need rewriting). The two-views-in-one-process step was
> skipped: two real windows on one PC test the split better. Presence too: each player's
> color, cursor, view box and selection, drawn in every other window. Other computers on the
> same network (2026-10-15): the world listens on the LAN and is found through saves/world.json
> or a typed address — no password, by choice (people you work with). **Next:** who may hold /
> grab what.
>
> **The question:** what would it take to run
> the world as its own **server**, with players joining from other computers, each
> with their own view, all working in the same world?
>
> **Short answer:** it can be done without a rewrite, but it's a big refactor. Most of the
> code (the drawing, which is ~30% of the lines and most of the look) carries over almost
> unchanged. The hard part is that today every object is *world + view + controls in one
> class*, and every change to the world happens through a direct method call or a closure
> that then saves the whole file. Those ~100 places where the world gets changed have to
> become **messages**. The networking itself is the easy part — `instrument_server.py`
> already shows the pattern.

---

## Where things stand

One process, one pygame window, one 60 fps loop (`game_loop.py`):

```
events ──► App / modes / inspector / object.on_click ──► mutate objects directly ──► scene.save() (whole file)
            │                                                                          
            └─► scene.update(mouse_pos, mouse_pressed, camera, dt)  ← sim + hover in one call
                scene.draw(screen, camera)
```

| Piece | What it is | World or view? |
|---|---|---|
| `Scene` | object list, `by_id`, selection, camera, save/load, the neuron mesh | **both** (camera + selection are view; objects are world) |
| `CanvasObject` + 34 subclasses | each one holds its data, simulates (`tick`), draws, hit-tests, handles clicks, builds its panel | **all three in one class** |
| `App` | what object code gets handed: `ask`, `confirm`, `context_menu`, `place`, `save`, linking pickers | view / controls, but its callbacks change the world |
| Modes (`PlaceMode`, `PickMode`, `WireMode`, `TrainBuilder`, `WallBuilder`, `MarqueeSelect`), `InspectorPanel`, `MenuBar`, `InventoryPanel`, modals, `CameraRide` | UI | **view** — already separate classes, good |
| `Nodes.json`, `machines.json` | saved world; machine library | world; library is debatable (shared or per player?) |
| `instruments.client` → `instrument_server.py` | hardware on this PC | world-side (the hardware is in one place) |

Rough line count by kind of code (from the source, functions only):

| | canvas.py | things.py |
|---|---|---|
| drawing (`draw*`, `screen_rect`, layouts) | ~860 | ~1,810 |
| panel (`state_rows`, `actions`, `menu_items`, …) | ~120 | ~215 |
| input handling (`on_click`, `on_drag`, `hit`, `update`, …) | ~80 | ~140 |
| simulation (`tick`, `outputs`, `takes`, `on_press`) | ~80 | ~175 |
| everything else (UI classes, setup, helpers, persistence) | ~2,070 | ~1,260 |

---

## What's already in our favor

- **Everything serializes.** Every type has `to_dict` / `from_dict`, including `UnknownObject`.
  A new player can be sent the whole world as one `{"objects": [...]}` blob — the same thing
  `Nodes.json` holds today.
- **Stable ids and `by_id`.** Links are `{"id", "key"}`, trains are `from_id` / `to_id`.
  Nothing points at Python objects across the save, so a remote copy can be rebuilt and
  re-linked by id. Machines' `_remap_ids` already handles "same world data, new ids".
- **Props are declared.** `PROPS` says what's saved and editable per type. That's most of the
  "what does a remote player need to know" list, already written down.
- **There's a working server/client in the repo.** `instrument_server.py` + `instruments.py`:
  JSON lines over TCP, a thread per client, broadcast, a full catalog on connect, then
  small updates (`readings`, `param`, `status`), "newest value wins" so a dragged slider can't
  flood it, and a hook for "the last client left" (`on_no_clients`). The world server can be
  built the same way.
- **The UI is already mostly classes of its own.** The modes, inspector, menus and modals
  don't hold world data — they'd move to the client side as-is.
- **The world is small.** Hundreds to a few thousand objects, JSON. Sending whole objects
  when they change is fine; no clever diffing needed at first.

---

## What's in the way

### 1. View state stored on world objects

These are per-*player*, but they live on the shared objects today:

| State | Where | In multiplayer |
|---|---|---|
| `is_selected`, `scene.selection` | every object / `Scene` | each player has their own selection |
| `is_hovered`, `is_clicked` | `Node.update` (from *this* window's mouse) | per player |
| `_held`, `_dragging`, `_grab` | `Input`, `Slider` | per player — and who's holding it matters (below) |
| collector scroll position, panel folds | `DataCollector`, `InspectorPanel` | per player |
| `camera`, `view` | `Scene` | per player (each one's own "remembers your view") |

`Scene.update(mouse_pos, mouse_pressed, camera, dt)` does hover *and* simulation in one pass,
so the first step is splitting it into `scene.tick(dt)` (world) and a per-view hover pass.

### 2. World values that depend on the mouse

A couple of outputs are computed from view state, which means the world's logic depends on
one window's mouse:

- `PushButton` / `Input.pressed` = "held down **and hovered** in this window, or just tapped".
  Its `pressed` output feeds Counters and collectors.
- `Slider.pressed` = `_dragging`.

In multiplayer these become world state: "pressed by player B" (`press` / `release`
messages). It also raises the first real *rule* question: two people holding the same
button, or dragging the same slider at once (see *Rules between players*).

### 3. Changes happen by direct calls and closures

This is the biggest piece of work. Today a click calls straight into the object, which
changes itself and saves:

- `Slider._drag_to` sets `self.value`, `on_release` calls `app.save()`.
- Panel rows and buttons carry closures: `Row(..., on_click=lambda app: p.edit(self, app))`,
  `Btn(label, method)` — about 19 `Btn(` and 19 `on_click=` sites, plus 37 `app.ask` /
  `app.confirm` / `app.context_menu` calls whose callbacks then change the object.
- `scene.save()` / `app.save()` is called from ~41 places and writes the whole file.

A closure can't be sent to another computer. Every world change needs to become a named,
serializable **command** that the server applies — e.g.:

| Command | Covers |
|---|---|
| `add {objects: [dicts]}` | place, copy, C key, machines, walls, trains |
| `delete {ids}` | Delete key, menus |
| `move {ids, dx, dy}`, `align` | arrow keys, align |
| `set_prop {id, key, value}` | every Settings / State row (`Prop.edit`) |
| `link {id, port, src, key}`, `unlink`, `unlink_all` | all wiring |
| `press {id, down}`, `drag {id, value}` | inputs, sliders, the SuperK switch, filter-band strip, filter-wheel slots, mirror clicks |
| `call {id, name, args}` | type-specific buttons (Collect, Export, Clear rows, Connect, home, …) |

The good news is the split is natural: the *asking* part (text box, confirm, menu) stays on
the client, only the final "do it" goes to the server. `app.ask("Name:", submit)` stays,
`submit` becomes `app.send({"op": "set_prop", ...})`. A cheap trick for the long tail:
make `call {id, name, args}` invoke a whitelisted method of the same name on the server's
copy, so most type-specific buttons don't need their own command.

### 4. Things that happen "somewhere" — which machine?

| Effect | Today | In multiplayer |
|---|---|---|
| `run_command` (launchers, Activate, Inputs' command) | runs a shell command on this PC | **must decide per action**: on the *clicking player's* PC (opening their files, their browser) or on the *server* (lab scripts). Running it on the server from a remote click is remote code execution — needs a permission rule |
| Devices (`instruments.client`) | canvas talks to `instrument_server.py` on 127.0.0.1 | the **world server** should be the only thing talking to the instrument server (it runs on the lab PC with the hardware). Players see readings through the world, never connect to hardware directly |
| SuperK emission, flip mirror, stage moves | safety rules live in the object (`on_no_clients` turns emission off when the canvas goes) | "no clients" now means *no players* in the world, or the world server stopping. Who may turn a laser on is a permission question |
| CSV export (`data/`) | written next to the script | on the server (it has the rows) and/or downloaded to the player |
| `machines.json` | one library | shared library on the server, or each player's own — a choice |
| Camera rides, Go To, view memory | the one camera | purely per player; no change in meaning |

### 5. Time and randomness

`Agent` wandering uses `random`, neurons use `poisson()` / `random()`, Function's `t` is
`time.monotonic()` since *this process* started. If only the server simulates, this is a
non-issue — clients just display what the server says. (It rules out the "every computer
runs its own copy in lockstep" design; see options.)

### 6. Live values every frame

Some world state changes every frame without anyone touching it: agent positions, neuron
spikes, Function / Relay outputs, device readings, Counter edges. Clients need these to
draw, so besides "object changed" messages the server needs a steady **live packet**
(say 20–30 times a second): each object's `outputs()` plus whatever moving state it draws
from (agent position, `is_firing`, a mirror's pending state …). Nothing declares that
"moving state" today — each type would list it, like `PROPS` (e.g. a `LIVE = [...]` list).

---

## Options

| Option | How | Verdict |
|---|---|---|
| **A. Authoritative server, view-only clients** | A headless process owns the `Scene`, runs `tick`, applies commands, saves, talks to hardware. Clients get the world on join, then changes + live packets; they draw, hit-test, run all UI, and send commands. | **Recommended.** Matches "the world exists on its own", fits the hardware, one place for rules |
| B. Lockstep (every PC runs the full sim, they swap inputs) | Like old RTS games | No — `random`, wall-clock time, hardware readings and shell commands all break it |
| C. Server renders, streams pictures | Remote-desktop style | Easy but heavy and laggy; not really "players in a world" |
| D. "Listen server" — one player's canvas is also the server | Same as A, but the host runs both in one program | A good **first deployment** of A: the lab PC's canvas hosts, others join. Later the server can run on its own (always-on, no window) |

With A, a client keeps a **replica** of each object: the same class, built from `to_dict`,
used only for `draw`, `hit`, panel rows and local UI — its `tick` doesn't run (or runs only
for pure visuals like glow pulses). The server keeps the real one. Because both sides are
built from the same classes, the object catalog doesn't fork into "server" and "client"
versions — each type just learns which of its methods are world-side.

---

## Rules between players (the interesting part)

Once the split exists, these are the things the world server decides:

- **Presence:** each player has a name and color; others see their cursor and what they've
  selected (Figma-style rings). Cheap: cursor + selection ids in a small per-player packet.
- **Grabbing:** while A drags a slider or holds a button, it's *held by A*; B's grab is
  refused (or B sees A's hand on it). Released automatically if A disconnects.
- **Conflicts:** last write wins per `(object, prop)` is fine for this kind of world. Moving
  the same object at once: the holder wins.
- **Permissions:** who may delete, run commands, touch hardware (the laser above all),
  stop the instrument server. Simplest first version: an *owner* player (full rights) and
  *guests* (no shell commands, no laser emission, no server stop). Later: per-object or
  per-Wall (a Wall as a "room" with its own rules fits the world nicely).
- **Undo:** per player, as commands with inverses — the command layer makes this possible
  (there's no undo at all today).
- **Joining / leaving:** join = full snapshot; leave = release grabs, drop presence;
  last player leaves = the `on_no_clients` safety hooks run.

---

## Networking and safety

- Today both servers listen on **127.0.0.1 only, by design**. A world others can join from
  another computer means listening on the network, which needs at least a join password /
  token, and ideally staying on the LAN or a private network (e.g. Tailscale) rather than
  opening a port to the internet — the world can run shell commands and drive a laser.
- Transport: JSON lines over TCP, like the instrument server, is fine to start. Messages are
  small; the full snapshot on join is the size of `Nodes.json`. If it ever lags, the live
  packet can go to a binary format or only include on-screen objects — not needed at first.
- Lag: a remote player's slider drag goes to the server and back (~1 ms on a LAN, 20–100 ms
  over the internet). Showing the dragged value locally right away and letting the server's
  answer overwrite it keeps it feeling instant.

---

## Plan, in steps that each leave a working app

Each step is useful on its own and the app keeps working single-player throughout.

| # | Step | What changes | Size |
|---|---|---|---|
| 1 | **Split view state off the world** | A `View` / `Player` object holds camera, selection, hover, held/dragging, scroll; `Scene.update` → `scene.tick(dt)` + a hover pass; `PushButton` / `Slider` `pressed` become world state set by press / release | medium — touches the base classes and the Input family |
| 2 | **Command layer, still one process** | Every world change goes through `scene.apply(command)`; saving moves there (one place, could be debounced); panel rows / buttons / dialogs send commands instead of mutating | **large** — the bulk of the work: every type's click, drag, panel and menu code |
| 3 | **Two views, one process** | Open a second view (a second window, or a test harness) on the same `Scene` to prove two players work: separate cameras, selections, grabs | small–medium; mostly testing step 1–2 |
| 4 | **Headless world server + network client** | `world_server.py` (modeled on `instrument_server.py`) owns the Scene, ticks at a fixed rate, talks to the instrument server; the canvas becomes a client: snapshot on join, object changes, live packets, sends commands. Types declare their live state | medium–large |
| 5 | **Player rules** | Names, cursors, selections of others, grabs, owner / guest permissions, a join password, where `run_command` runs | medium, and open-ended (this is where the "governing interaction" design lives) |
| 6 | **Nice-to-haves** | undo, per-Wall rooms / permissions, only sending what's on screen, a server with no window that's always on | later |

Steps 1–3 are worth doing even if multiplayer never ships: they give undo, one save path,
and a cleaner object model. Step 2 is the one to estimate carefully — it's roughly "visit
every one of the 34 object types once", about the size of the panel + takes / gives
refactor that was just finished (docs/archive/PANEL_PLAN.md), maybe a bit bigger.

---

## Open questions (yours to decide)

1. **Where do launcher commands run** — the clicking player's PC, the server, or a per-button
   setting?
2. **Is the machine library shared** (everyone sees everyone's machines) or per player?
3. **Who can touch hardware** — only players at the lab PC, only the owner, or anyone
   with the password?
4. **Is the server the lab PC** (hardware there, listen-server first), or somewhere always-on
   with the lab PC as one player that *also* hosts the instrument server?
5. **Players' views:** does each player get their own remembered view? (Yes seems natural —
   it'd live with the player, not in `Nodes.json`.)
