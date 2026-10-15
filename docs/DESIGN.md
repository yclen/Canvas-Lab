# Canvas Lab — Design Notes

> A record of the thinking behind this app, not a manual — what we're building, the
> options we weighed for each design question, and where we landed. Kept alongside the
> code so decisions keep their context. How things work now is in
> [ARCHITECTURE.md](ARCHITECTURE.md); what each object does is in [OBJECTS.md](OBJECTS.md).

---

## The vision

An infinite pan/zoom 2D canvas where you place **objects** spatially — a personal "life
map" you navigate by moving through space instead of clicking through menus.

The headline object is a **launcher node**: you click it, a panel opens on the right
showing what it does, and an **Activate** button runs it — a terminal command, a URL, an
agent launch. Beyond launchers there's an open-ended set of *other* kinds of objects.

The mental model we keep coming back to is **Factorio**: a palette of many different
object types you can drop onto a shared world — chests, walls, connectors/roads, etc. —
each looking and behaving very differently, but all living on the same canvas. The whole
design question is: *how do we keep adding new kinds of objects without the code turning
to spaghetti?*

**Structure: split by concern, not by construct.**
- `canvas.py` — everything about the world and the app: all the classes (canvas objects,
  camera, scene = the world, player = one view of it, UI) plus the helper functions that serve them (fonts, actions,
  persistence), and the object toolkit (`Prop`, inspector rows, `Thing`, `App`).
- `things.py` — the **catalog**: the many simple object types, one small class each.
  Imports from `canvas.py`; `canvas.py` never imports it.
- `game_loop.py` — the thin runner: pygame/window setup, the home screen, + the main-loop
  wiring, nothing else, and nothing that names a specific object type. It imports `canvas`
  and `things`.
- `world_server.py` — the world running on its own (its own small window); `world_link.py`
  — the link between it and player windows (see *The world server and players* in ARCHITECTURE.md).

We considered splitting "all classes in one file, all non-class glue in the loop," but
that cuts across concerns — it would exile actions/persistence into the loop file and turn
both files into grab-bags. So the line is *concern*, with helper functions living next to
the classes they serve. If `canvas.py` gets unwieldy later, the natural next split is
*within* it (objects vs. UI vs. scene), not along the class/non-class line.

---

## Concepts that keep guiding us

Checked against every new idea:

- **What an object *looks like* is separate from what it *does*.** Look/shape is a *type*;
  behavior is *data* attached to it. Two buttons that look identical but launch different
  things are the same type with different data — not two classes.
- **The game loop is just wiring** — handle-events / update / draw; real logic lives in the
  objects and functions above it.
- **The loop doesn't ask "what exact type is this?"** to decide behavior — it trusts a small
  shared interface, or checks a *capability* (`selectable`, `isinstance(Node)` for node-only
  actions, an `on_click` hook) rather than a concrete type.
- **Keep future doors open cheaply.** Small near-free hooks (a stable id, a storage seam)
  beat both over-building and painful retrofits. Leave the socket, don't build the feature.
- **Start simple; add structure only when duplication actually appears.**

---

## JSON or a database? → JSON

At a few hundred objects everything fits in memory; a database buys nothing. JSON is
human-editable and git-diffable, and each object's `params`/style fields are *different per
type* (a URL command vs. a train's waypoints), which fits JSON's flexible shape but fights
SQL's rigid columns. Load/save sit behind one seam (`load_objects`/`save_objects`), so a
future swap would be two functions, not a rewrite.

**Revisit SQLite only if:** thousands of objects we won't hold in memory, full
change-history/versioning, concurrent writers, or heavy relational queries. None true today.

---

## How objects are structured in code (the big one)

The tension was: a **too-fat** master class forces a wall to fake a `world_center`; **too
separate** classes make the loop special-case every type. We landed on a *thin* master +
a subclass per object.

`CanvasObject` imposes only the socket every object plugs in with: `id`, `TYPE`,
`draw_layer`, `is_selected`, `draw()`, `hit()`, `on_click()`, `to_dict()`, and a
`display_name`. Everything else lives on the subclass, free to differ from its siblings.

A middle `Node` layer groups the point-anchored launcher family, because Button and NameTag
genuinely share substance (a `world_center`, boxed rendering, selection, an `action`).
`Train` is a sibling of `Node`, not under it — it's relationship-anchored, not a point.

```
CanvasObject (thin master)
├── Node            ← point family: world_center, select / move / copy, action/params
│   ├── Button       ← launchers (LAUNCHER = True): command + Activate
│   ├── NameTag
│   └── Thing        ← the easy base for simple drawn objects: SIZE + draw_shape + tick
│       ├── Agent    ← wanders on its own (not a launcher)
│       │   └── Neuron ← Agent look-alike, stationary: white body, one oval spot
│       └── (things.py: Rock, Lamp, … — the catalog)
├── Train           ← connector: two node ids + waypoints, no single center
├── Wall            ← area zone: rectangle (x, y, w, h), no single center
└── UnknownObject   ← placeholder for a saved type whose class is gone (inert, preserved)
```

Rule: add an intermediate layer *only when* two subclasses clearly duplicate. Two levels
deep is plenty. **The one thing plain inheritance can't do** is a capability shared *across*
branches (e.g. both a Node and a future Chest being activatable) — when that shows up, the
fix is a small flag/mixin for just that capability, added reactively. Not built yet.

**The type registry** (`OBJECT_TYPES`, a `{type-string: class}` dict) is what keeps it
open-ended: the loader reads it (JSON `"type"` → class), the Add menu reads it, and a
future palette will too. It **fills itself** — `CanvasObject.__init_subclass__` registers
any class that sets a `TYPE` — so defining the class is the whole registration.

**Everything type-specific lives on the class.** The thin master grew *defaults*, not
requirements: each hook below has a working default, and a subclass overrides only what's
different about it. The loop, menu and inspector just call the hooks.

| Hook | What it's for | Default |
|---|---|---|
| `TYPE`, `MENU_LABEL` | saved type name; Add-menu + E-menu entry | not in menus |
| `NAME`, `CATEGORY` | E-menu tile name; E-menu tab | label minus "Add "; just "All" |
| `draw_icon(surface)`, `icon_sample()` | E-menu icon | draw a sample object, scaled |
| `create(app)` | what picking it does | `app.start_placing(...)`: drop with the mouse |
| `PROPS` | saved, inspector-editable fields (`state=True`: under State) | none (inherited lists merge) |
| `outputs()` / `gives()` | live values it offers; declared with units | none / one per `outputs()` key |
| `TAKES` / `takes()` | its inputs (`Take`) | none |
| `on_press(port)` | a *press* input went 0 → 1 | nothing |
| `input_point(port)`, `output_point()` | where wires end / start | center |
| `take_at` / `give_at(pos, camera)` | input chip / output nub under the mouse (wire drag) | none / the output_point nub |
| `panel_status`, `state_rows`, `input_rows`, `more_sections`, `info_rows` | the panel's pieces | none / ID + position |
| `actions(app)` / `@panel_button` | footer buttons | every `@panel_button` method |
| `menu_items(app)` | its own ⋯ / right-click menu items | none |
| `migrate(d)` | update an older save before loading | unchanged |
| `center`, `in_box`, `copyable`, `COPY_SKIP` | Go To, drag-box select, copy | per family |
| `on_prop_changed(key)` | react to an inspector edit | nothing |
| `tick(dt)` | the world moving on its own, every frame (never reads the mouse, camera or selection) | nothing |
| `hover(mouse_pos, mouse_pressed, camera)` | a player's view: hover / pressed *looks* only | nodes set `is_hovered` |

---

## What an object *does* vs. how it *looks* → behavior is data

A node carries an `action` (a name) and `params` (its data). One `Button` class, unlimited
behaviors. The `ACTIONS` registry maps the name to a function via `run_action(node, ctx)`:

- **`run_command`** — the workhorse. Launches a shell command **detached / non-blocking**
  (`subprocess.Popen`, so a long command never freezes the canvas). Covers agents, URLs,
  scripts, opening files/folders — anything a shell does.
- **`goto`** — internal: jump the camera to another node by id (can't be a shell command,
  which is why the `action` stays a named type rather than a bare command string).

Adding a behavior is one decorated function; the loop and inspector never change.

---

## How you interact → click to select, Activate to run

Two-step, so a stray click can never fire a terminal command:
- **Single click a node** → selects it and opens the **inspector panel** (right overlay).
- **Activate** in the panel → runs the node's action.

The panel is **object-driven** and the same for every type (see *The panel* in ARCHITECTURE.md); it
knows no types, and the loop just runs whichever callback was clicked. Launchers (Button /
NameTag) show their Command under State (click to set it) and **Activate** (green when the
node has a command). Double-click any object with a `center` is a shortcut for Go To.

---

## What's next (open threads)

- **E menu extras** — a search box (type to filter), a hotbar of recent picks, and
  categories on the catalog classes once there are enough to group.
- **New object types** — the catalog in `things.py` (see the recipe in ARCHITECTURE.md). Once it's long,
  give classes a category so the Add menu / palette can group them, and split `things.py`
  into a folder of themed modules.
- **Animated connectors** — would need time passed into `draw`/`update` (flowing route,
  marching ants). Not plumbed yet.
- **Inspector editing** — edit description inline; a close control for the panel.
- **Panel extras** — remember folded sections across restarts; list an output's readers.
- **Nice-to-haves** — Esc-to-deselect, build-preview refinements.

**Not built yet:** E-menu search / hotbar · more object types (Chest, freeform region, …) ·
richer agent behaviour · animated *connectors* · inline editing of description in the panel ·
wall resize handles · wall→node containment.
