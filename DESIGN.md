# Canvas App — Design Notes

> A record of the thinking behind this app, not a manual — what we're building, the
> options we weighed for each design question, and where we landed. Kept alongside the
> code so decisions keep their context. Much of what's below is now **built**; see the
> Build status section for what's done vs. still ahead.

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

**Structure: two files, split by concern, not by construct.**
- `canvas.py` — everything about the world and the app: all the classes (canvas objects,
  camera, scene, UI) plus the helper functions that serve them (fonts, actions,
  persistence).
- `game_loop.py` — the thin runner: pygame/window setup + the main-loop wiring, nothing
  else. It imports from `canvas.py`; `canvas.py` never imports back.

We considered splitting "all classes in one file, all non-class glue in the loop," but
that cuts across concerns — it would exile actions/persistence into the loop file and turn
both files into grab-bags. So the line is *concern*, with helper functions living next to
the classes they serve. If `canvas.py` gets unwieldy later, the natural next split is
*within* it (objects vs. UI vs. scene), not along the class/non-class line.

---

## Build status

**Built and working:**
- Camera (pan / wheel-zoom / `look_at`); Scene state container with an id index
- `CanvasObject` thin master → `Node` → `Button` / `NameTag`; `OBJECT_TYPES` registry
- Level-of-detail draw (buttons shrink to a dot; no Button↔Rectangle swap machinery)
- JSON persistence (`{"objects": [...]}`) behind `load_objects`/`save_objects`; stable ids, backfilled
- Menu bar (Add / Edit / Go To) + unified modal slot (text input, confirm delete)
- Inspector side panel: view a node's data, **Set command…**, **Activate** (runs detached)
- ACTIONS registry: `run_command` (shell, detached) + `goto` (internal camera jump)
- **Train** object: orthogonal connector between two nodes, camera-ride on arrow click,
  adjustable speed, interactive build mode, route auto-simplify (heals old overhangs on load)
- **Wall** object: outline-only rectangular zone, drag-to-draw, fixed-size corner name tag,
  border scales with zoom
- **Agent** object: a little orange square with eyes that idly wanders (walk/pause/walk),
  confined to whichever Wall contains it — the first *animated* object, so `update()` now
  carries frame-time (`dt`)
- **Neuron** object: a stationary Agent subclass drawn as a white square with one black
  oval center spot; Add ▸ Add neuron. Has a **firing equation** (inspector ▸ Set firing
  equation…) evaluated each frame against its own clock `t` (reset when the equation is set):
  a number = rate in fires/sec, fired at steady intervals (`1`, `2t`); true/false = fire on
  true frames. **Clock end** (`t_end`, default INF, clickable row): the clock pauses at
  t = t_end and the neuron stops firing; raising it resumes, INF never stops. **Clock
  trigger** (`trigger`, button "Clock trigger equation…" + clickable row): a condition that
  may use `W` (= Input W), e.g. `W > 3` (a leading `if` is ignored). With one set, the clock
  waits at t = 0 until it's true, then runs to Clock end. Every trigger — the condition
  going false → true (edge, so a multi-frame spike counts once) — restarts the clock from 0,
  even mid-run. Empty = no trigger, runs freely. Helpers: `every(s)`, `poisson(rate)`, `random()`, `dt`, math funcs. Firing =
  yellow body for the neuron's **spike duration** (`spike_frames`, default 1, ◀ ▶ stepper in
  the inspector, saved per neuron); max one fire per frame. Parsed with an `ast`
  whitelist (no attributes/imports); `2t`→`2*t`, `^`→`**`. Errors show in the inspector
- **Neuron mesh**: every neuron is linked to every other by a faint 1px line
  (`NEURON_LINK_COLOR`). Links aren't stored — derived from positions each frame, one per
  pair — and `NeuronMesh` caches the drawn layer, redrawing only when a neuron's screen
  position or the window size changes. Cost is n²: ~100 neurons stays smooth while panning.
- **Neuron W's**: each neuron holds `weights = {other_id: W}` — its outgoing W to every other
  neuron (directed: A→B lives on A, B→A on B). `Scene.sync_weights()` runs on load/add/delete:
  new pairs get W = `NEURON_W_DEFAULT` (0), W's to deleted neurons are dropped,
  existing W's never change. Saved in `Nodes.json`; copies get fresh (0) W's. n neurons = n(n-1)
  W's. The inspector shows them as a wheel-scrollable list plus `count / NEURON_SOFT_LIMIT`
  (100, orange when over — the mesh drawing, not memory, is the real limit). Right under
  Network (above the "Output W's" list): **Input W** = Σ over other neurons of (their W to this one) × `is_firing` (1 during
  their spike frames, else 0) — `Neuron.input_w()`, live, O(n), selected neuron only. So it's 0
  between spikes. The spike countdown lives in `update()` (not `draw()`), so the yellow flash,
  `is_firing`, and the inspector all agree within a frame. Output W's are unaffected by firing. Each Output W row is clickable (hover highlight) → a text box to
  edit that W; any finite number is accepted, non-numbers leave it unchanged
- Generalized selection + movement: any object can be selected/deleted; `movable`/`move()`
  capability (nodes + walls + agents move, trains don't); node-only actions guard for `Node`
- **Drag-box multi-select**: left-drag on empty canvas selects every node whose center is in
  the box (+ walls fully inside). Arrow keys move all of them; Edit ▸ Delete / **Delete** key
  removes all after one confirm. `scene.selection` is the list; `scene.selected` is the single
  object or None when 0 or 2+, so rename/copy/goto/inspector only apply to one object
- **Remembers your view**: on close, the world point at screen center + zoom are written to
  `Nodes.json` as `"view"`; on launch the camera is restored there. Normal saves keep it
- **Text boxes** have a movable cursor: Left/Right, Home/End, Backspace/Delete,
  Ctrl+Backspace (word); long text scrolls sideways. Key-repeat (`TEXT_KEY_REPEAT`) is
  switched on only while a text box is open, so held keys don't repeat on the canvas
- **Copy** (Edit menu) for buttons/name tags/walls; **C** key drops a copy of the selection
  centered at the mouse cursor (works for any node type + walls); clipboard paste (**q** or Ctrl+V) in text fields

**Not built yet:** left-side palette · more object types (Chest, freeform region, …) ·
richer agent behaviour · animated *connectors* · inline editing of description in the panel ·
wall resize handles · wall→node containment.

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
├── Node            ← point-launcher family: world_center, action/params, activate
│   ├── Button
│   ├── NameTag
│   └── Agent        ← point object, but wanders on its own (not a launcher)
│       └── Neuron   ← Agent look-alike, stationary: white body, one oval spot
├── Train           ← connector: two node ids + waypoints, no single center
└── Wall            ← area zone: rectangle (x, y, w, h), no single center
   (future siblings: Chest, freeform region, …)
```

Rule: add an intermediate layer *only when* two subclasses clearly duplicate. Two levels
deep is plenty. **The one thing plain inheritance can't do** is a capability shared *across*
branches (e.g. both a Node and a future Chest being activatable) — when that shows up, the
fix is a small flag/mixin for just that capability, added reactively. Not built yet.

**The type registry** (`OBJECT_TYPES`, a `{type-string: class}` dict) is what keeps it
open-ended: the loader reads it (JSON `"type"` → class) and a future palette will too.
Register a class and it's placeable + loadable with no loop changes.

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

The panel is **data-driven**: it shows whatever fields the selected node carries
(type, name, live position, id, description, command) and has **Set command…** (a text
modal that sets `action=run_command` + `params.command`) and **Activate** (green when the
node has a command). A new object type or action needs no panel changes. Double-click a node
is a shortcut for Go To.

---

## Trains (built) — the first connector object

A **Train** is an orthogonal "transport route" connecting two nodes, with a clickable arrow
at each end. Clicking an arrow **rides the camera along the route** to the far node — a
smooth follow at the train's speed, keeping your current zoom, interruptible by any input
(pan/zoom/click/move). It validated the connector idea we'd left fuzzy, and stress-tested
the architecture (relationship anchoring + ids).

- **Data:** `from_id`, `to_id`, and a list of corner `waypoints`. Endpoints are read from
  the live nodes each frame, so the route follows them when they move. It owns and draws its
  own two arrows (not separate objects). Attaches to node *edges*, auto-picking the side from
  the route direction.
- **Rendering:** each axis-aligned segment as an inflated rect (square corners); line width
  scales with zoom down to ~1px (LOD), arrows disappear below a pixel threshold. Draws under
  nodes (`draw_layer = -1`).
- **Build mode (`TrainBuilder`):** a *non-blocking* placement mode — it claims left-click,
  mouse-move, Esc and Backspace, but lets pan/zoom stay live. Click a start node → drop
  corners (auto-snapped H/V) → click an end node. Hovering the end node previews the exact
  finished train (WYSIWYG). An auto-elbow keeps the final segment aligned with your approach
  direction, so the end arrow lands on the side you came from.
- **Speed:** each train has its own ride speed (world-units/sec, default 700, persisted).
  Selecting the train opens its inspector variant, which shows From/To/Speed/Corners and an
  **Adjust speed…** button instead of Set command.
- **Ride mechanics:** `CameraRide` walks the polyline at constant speed; `camera.update_ride`
  advances it each frame and clears it at the end. Any direct camera input cancels it.
- **Route cleanup:** on finish, the polyline is simplified (collinear/redundant points
  dropped) so a corner drawn past the end node doesn't leave an overhang stub.
- **Selection/deletion:** clicking the line selects the train (blue halo); Edit ▸ Delete Node
  removes it. Clicking an arrow rides instead of selecting.

This is why **selection got generalized**: any object can now be selected/deleted, while the
node-only actions (arrow-move, Edit Name, Go To, inspector) check `isinstance(Node)` first —
so selecting a train can't crash them. That's the groundwork for selecting any future type.

---

## Walls (built) — the first area object

A **Wall** is a rectangular zone drawn around a group of nodes to organize the map. It's the
third anchoring family (after point-nodes and relationship-trains): area-anchored, stored as
`(x, y, w, h)`, no single center.

- **Look:** outline only (no fill); the border scales with zoom like a node, but the title is
  a **fixed-size name tag pinned to the top-left corner** (never scales, like a NameTag).
- **Create:** Add ▸ Add wall → **drag** a box (press-move-release) → name it. (The mouse-up
  handler ignores the menu-click's own release, so the mode doesn't self-cancel.)
- **Hit-test on the border/title only**, not the interior — so nodes inside stay clickable.
- **Purely visual:** selectable (blue halo) to move (arrow keys)/rename/delete; no containment
  — it doesn't own the nodes inside it.
- Draws under everything (`draw_layer = -2`).

Adding it drove the **movement generalization** (a `movable` flag + `move(dx, dy)` on objects,
so the loop moves anything movable) — the clean way to let a non-node object be dragged around.

---

## Agents (built) — the first animated object

An **Agent** is a little orange square with eyes that idly wanders — walk to a random point,
pause, pick a new one — to *look* alive. No real behaviour yet; that's the future.

- It's a **Node** (point-anchored, selectable/movable/deletable/copyable), but not a launcher.
  Spawned from Add ▸ Add agent at screen center.
- **Confined to its box:** each frame it finds whichever `Wall` contains it and keeps its
  wander target inside that wall (with a margin). Drag it into a different wall and it's
  restricted there; with no wall it wanders around a **fixed home point** (anchored so it
  doesn't random-walk and drift away — the home moves only when you `move()` the agent).
- **Holds still while selected**, so you can grab it.
- Its live position isn't force-saved each frame; it persists at whatever settle-point save
  happens to catch (so the saved spot drifts a little — harmless).

This is the object that made us **thread frame-time (`dt`) through `update()`** —
`Scene.update(..., dt)` → each object's `update(mouse, pressed, camera, dt)`. That's the
animation plumbing the design had flagged as missing; animated *connectors* can reuse it.

---

## Level of detail (built)

Instead of swapping a `Button` object for a `Rectangle` when zoomed out, each type just
*draws a simpler version of itself when tiny*: a button becomes a plain dot below a zoom
threshold; a name tag stays fixed screen-size; a train collapses to a thin line. This
deleted the whole swap/sync machinery (`Rectangle`, `shrink_to_rectangle`,
`sync_button_scale`, and the "remember to call sync after every zoom" bug class).

---

## Object ids (built)

Every object has a short stable id (`n_xxxxxxxx`), assigned once and saved. Trains reference
two of them; `goto` targets one; future grouping/undo will need them. Old data without ids
gets them backfilled on first load (which also migrates the legacy `{"nodes": [...]}` shape
to `{"objects": [...]}`).

---

## What's next (open threads)

- **Left-side palette** — the Factorio "toolbar" of placeable object types (reads
  `OBJECT_TYPES`). Right side is inspect; left side is place.
- **New object types** — Wall/Region (area-anchored, a list of points), or others. The
  thin-master + registry means whatever they are, they drop in if they can `draw` and `hit`.
- **Animated connectors** — would need time passed into `draw`/`update` (flowing route,
  marching ants). Not plumbed yet.
- **Inspector editing** — edit description (and maybe params) inline; a collapse/close control.
- **Nice-to-haves** — Esc-to-deselect, build-preview refinements.
