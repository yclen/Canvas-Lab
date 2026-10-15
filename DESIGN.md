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

**Structure: split by concern, not by construct.**
- `canvas.py` — everything about the world and the app: all the classes (canvas objects,
  camera, scene, UI) plus the helper functions that serve them (fonts, actions,
  persistence), and the object toolkit (`Prop`, inspector rows, `Thing`, `App`).
- `things.py` — the **catalog**: the many simple object types, one small class each.
  Imports from `canvas.py`; `canvas.py` never imports it.
- `game_loop.py` — the thin runner: pygame/window setup + the main-loop wiring, nothing
  else, and nothing that names a specific object type. It imports `canvas` and `things`.

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
- **Object toolkit** — a new type is one class, no loop/menu/inspector edits (see *Adding an
  object type*): self-registering types, an Add menu built from the registry, `Prop`
  (saved + inspector-editable fields), the standard panel filled in by the object
  (`state_rows()` / `info_rows()` / `actions()` / `@panel_button` — see *The panel*),
  inputs / outputs (`takes()` / `gives()` — see *Inputs and outputs*), callback dialogs via `App.ask` /
  `App.confirm`, the `Thing` base for simple drawn objects, and the `things.py` catalog
  (examples: Rock, Lamp). Clicks hit the topmost object; a saved object whose type no
  longer exists is kept as an inert `UnknownObject` (written back unchanged)
- **Off-screen culling**: `Scene.draw` skips objects whose `screen_bounds(camera)` (padded by
  their own size + 40px, for glows / halos / selection rings) doesn't touch the window. Nodes
  return their `screen_rect`, walls include their title tag, trains their route + arrows;
  `None` (the default) means always draw. Updates still run for everything (neurons keep
  firing off-screen). Measured: 3,000 objects zoomed in went from ~100 ms to ~11 ms a frame
  (the rest is per-object `update`); drawing everything zoomed-out is unchanged (~24 ms).
  Next steps if needed: cache rendered text / per-frame surfaces, a spatial grid, then GPU
  compositing of cached per-object textures
- **Pan / zoom speed-ups**: every font from `get_font` is a `CachedFont` that remembers its
  renders and text measurements (most text is identical frame to frame), with an LRU cap on
  fonts; big text (> 30 px, i.e. zoomed in) snaps to ~5% size steps so zooming reuses fonts.
  The neuron mesh is drawn once per zoom onto a layer covering the visible network (+ half a
  window of margin) and just moved when panning. Measured at 2560×1440: panning near devices
  / the server ~5–7 ms → 3–4 ms a frame; zooming near a device ~24 ms → ~16 ms. What's left
  while zooming is re-rendering big shapes and text at every new size — a software-rendering
  limit; the real fix is GPU drawing (see above)
- **Error safety net**: the app runs without a console, so errors go to `errors.log` (git-
  ignored) via `report_error` — once per distinct error. Each object's `update` / `draw` /
  `draw_links` is guarded in `Scene`: a failing object is logged and drawn as a red X box
  instead of crashing the app. Anything else uncaught is logged by `sys.excepthook` (in
  `game_loop.py`), which also saves the canvas before the app closes. (Found via a Readout
  crash at ~4% zoom: its glass-shine strip got a negative width. Objects drawn at tiny sizes
  should bail out to a simple shape, as Thing's LOD dot and the Readout now do.)
- **E menu (inventory)**: **E** (or *Objects (E)* in the menu bar) opens a window with a
  tile per placeable type — its icon + name — wheel-scrollable for a long catalog, with
  tabs ("All" + one per `CATEGORY` any type sets, + "Machines" — see *Machines*). Icons draw themselves: the default
  `draw_icon` draws a sample object (`icon_sample()`) scaled to fit, so a new type gets an
  icon for free (Train/Wall hand-draw theirs; a crashing icon shows a "?" tile instead).
  Picking a tile runs the type's `create`. Remembers its tab + scroll
- **Inputs** (E menu ▸ Inputs, in `things.py`): an `Input` base — a click *presses* it
  (`press(app)`, default: run its command) instead of selecting it; it tracks hover /
  held / a short press flash for drawing and draws the shared housing; subclasses draw
  `draw_face`. First member: **PushButton**, an arcade-style square cap that brightens +
  glows on hover and sinks into its housing when pressed (cap color + name-on-cap). An
  **Icon** on the cap (`icon`, a choice — click cycles): none, up / down / left / right arrows, play,
  pause, stop, record, plus, minus, check, cross, power, reset — simple bold shapes drawn by
  `draw_button_icon` (polygons + round-capped strokes, so they're crisp at any size), dark on a
  light cap, light on a dark one, with a 1px emboss shadow; with a name too, the icon sits above
  a smaller name.
  **Ctrl+click** selects any object without interacting with it (drag-box works too)
- **Switch** (Inputs): an on/off slide switch — each click flips it (saved `on`), the knob
  slides across (`SLIDE_TIME`) and the track fills with its color; ON/OFF shows beside the
  knob when it fits. Size, color, and the On row are editable; a flip also runs its command
- **Slider** (Inputs): a vertical fader — drag the cap (or click the track to jump) to set a
  saved `value` between `min` (bottom) and `max` (top), snapped to `step`; the slot fills
  with color below the cap and a readout shows the value. Drag is handled in the object's own
  `update()` (it gets the mouse + camera), saving once on release. Size, length, min, max,
  step, value, color editable. `value` is the parameter other objects will read later
- **Inputs and outputs (takes / gives)** — one model for every link; see *Inputs and outputs*
  below. Objects give live values (`outputs()`: Slider `value`, Switch `on`/`flips`, PushButton
  `pressed`/`presses`, Lamp `on`, Neuron `firing` / `clock t` / `input W`, …) and take them on
  named inputs (`takes()`). The `Output` base (E menu ▸ **Displays**, was *Outputs*) has one
  input, `in`, read into `reading` every frame and passed on as `value`. First display:
  **Readout**, an LED display — seven-segment digits with unlit "ghost" segments, a unit
  beside them (free text: mW, ms, V, …), decimals, digit slots, color, size, and the object's
  name printed on the bezel. Dashes when unlinked
- **Function** (E menu ▸ Math, `things.py`; evaluator in `expressions.py`): `y = <equation>`.
  It's placed empty ("(click to type)"); a click on it opens the equation's text box (Ctrl+click
  selects). Every name that isn't built in (`expressions.RESERVED`: sin cos tan asin acos atan
  atan2 sinh cosh tanh sqrt exp log log10 log2 abs min max round floor ceil sign clamp, pi, e,
  and `t` = seconds since the canvas started) becomes a variable *chip* on the left edge, in
  the order it first appears — hollow until linked, filled once linked. Each variable is an
  input: click its chip (or **link** in the panel), then a source; clicking the function itself
  unlinks it. Links are keyed by variable name, so editing the equation never loses wiring
  (a name that comes back reconnects; links for absent names are kept but not drawn). Output
  is `{"y": value}`; it's `None` while there's no value (unlinked variable, bad syntax, 1/0,
  sqrt(-1), NaN/inf), with the reason in red on its screen and in the inspector — a Readout
  then shows dashes and a Device skips it (holds its last value). Range checking is the
  drivers' job. The equation is parsed with `ast` against a whitelist and compiled once per
  text change; numbers are floats (so `9^9^9` overflows instead of hanging). `^` = power,
  `2P` = 2*P (not inside names like `log10`, not `1e-3`), a leading `y =` is ignored. Wires
  end at a variable's chip (`input_point(port)`) and leave from the output nub on the right
  (`output_point()`); drag from the nub to lay a wire to another object's input. Chains and
  loops are fine — each Function computes once a frame in `tick` and
  `outputs()` returns the stored value, so a loop just runs a frame behind
- **Counter** (E menu ▸ Math, `things.py`): a saved value that buttons step. Three *press* inputs,
  chips on the left — **up** / **down** add / take away the Step, **reset** returns to Start —
  linked like a Function's chips (the output menu lists `pressed`, `on`, `value` first). Each acts
  once when its source goes 0 → 1 (on/off is just 0 or 1; anything not 0 is on), so holding a
  button counts once and a switch counts each off → on; a source already on when the canvas loads
  or the link is made doesn't count. Optional Minimum / Maximum (hidden
  `low` / `high`, blank = none): the value stops there, so presses past a limit do nothing (no
  "stored-up" presses, unlike `presses` → Function), and a new limit pulls the value inside it.
  Values are rounded to 12 places (0.1 × 3 = 0.3). Click the number to type one; the panel has
  + / - / Reset buttons (and Value / Minimum / Maximum under State). The value is saved (a `value` Prop, saved on each change) and is the
  output `value`. Drawn Function-style: chips, a screen with the number (brightens on a change,
  dims at a limit), an output nub, and the step / limits beside the name
- **Data collector** (E menu ▸ Data, `things.py`): a spreadsheet that records values. Columns
  (built-in `#` and `time`, then named ones) are inputs `col<cid>` that *record* a source — click
  a header (or **link** in the panel), then the source (a green dot once linked); **+** (or
  *+ add column* in the panel) adds one (name, then pick its source); click a column's name in
  the panel to rename / remove it. **Collect** (the drawn button, or the panel's) adds a
  row with every column's value right then (`None` → "—") and an ISO timestamp. The **collect**
  chip on the left edge is an optional *press* input: each time it goes 0 → 1 (e.g. a push
  button's `pressed`) it collects once; a source that's already on when the canvas loads or the
  link is made doesn't count. Rows are saved with the canvas (hidden `rows`, values keyed by a
  stable column id `cid`, so renaming keeps data; a removed column's values stay saved but aren't
  shown or exported); the sheet shows *Rows shown* (default 8) — the newest, unless scrolled back
  with the **scrollbar** (drag the thumb, or click the track to jump) or the **mouse wheel** over the
  sheet (3 rows a notch; the title then says "rows a-b of n"). Scrolling to the bottom follows new
  rows again; the scroll position isn't saved. The wheel gets there through a general hook,
  `on_wheel(pos, steps, app)`: the loop asks the object under the mouse (after the inspector's
  list) and zooms only if it returns False — the collector takes it only over a sheet with more
  rows than fit, so zooming over anything else is unchanged. Each one is a named **data set** ("Data set 1", …,
  renamed from the panel's first row); the name is on the title bar and in the export's file name.
  *Export CSV* writes `data/<name> <date time>.csv` (row, time, columns; `data/` is git-ignored); *Clear rows…*
  (in the ⋯ menu) confirms first. Output: `rows` (the count). Each collect saves the canvas — fine for hand /
  button collects; a very fast trigger on a big table would mean big, frequent saves
- **Data relay** (E menu ▸ Data, `things.py`): a small junction for routing one value — so a wire
  can go where you want and split. Click it, then a source; it offers that value as `value`
  (always offered, `None` until fed, so it can be wired up before its source). Anything that links
  — device params, function variables, readouts, collector columns, other relays — can read it,
  any number of them, so relays chain into a path and fan out. `outputs()` reads **straight
  through** the chain (no frame of delay per hop), with a loop guard (`_RELAYS_RESOLVING`): a chain
  that loops back has no value, and the relay in the loop shows a red core. It doesn't store the
  value. Looks: a hex nut (bevel, corner rivets) with a core that's hollow when unlinked, amber when
  the source has no value, green when carrying one, and flares when the value changes. Nothing
  else: tried a value tag and port nozzles with arrows toward its source / readers — both cluttered
  the paths (the wires' flowing dots already show direction). The panel shows the value and what it
  **Feeds**: readers are found every 0.25 s by `links_to(obj, id)` (does one of obj's inputs
  follow id?). **Laying a path:** while linking a relay (or a tag), a click on
  *empty* canvas places a new relay there fed by it (`extend`) — the clicked one keeps its own
  source, the new one is selected, and clicking it then empty canvas again adds the next hop. A
  click on an object that can't be linked, or Esc, still just cancels. This is PickMode's
  `on_empty(world point)` (via `App.pick_link(..., on_empty=)`), which draws a small ring at
  the cursor over empty canvas; other picks (a function's chips, …) don't pass it
- **Data tag** (E menu ▸ Data, `things.py`): a named data point — a `DataRelay` subclass, so it passes
  its value through the same way (no delay, loop guard, any number of readers), but it has a body
  you can read. Named after SCADA/PLC "tags" (a named live value with units). Shaped like a luggage
  tag: the incoming wire ties into the grommeted hole in its pointed end (`input_point("in")`; the
  center for a plain relay), the output leaves from a nub on the right
  (`output_point()`), the name is printed across the top, and a screen shows the value
  Function-style (`.6g`, shrinking to fit) with its **Unit** (free text, display only) beside it.
  New ones are "Tag 1", "Tag 2", … — placed without asking; click the name to rename, anywhere else
  to link. The hole glows in the relay's state color; unlinked says "click to link", no value shows
  an amber dash, a loop says so in red. Color and Size are editable. 270 × 96 at Size 1
  - **Streaming** (the ((•)) icon, top right): an *on/off* input, `stream`. Unlinked, the tag
    streams live ("always"). Click the icon (or **link** in the panel), then a button / switch, and
    it streams only while that's on (not 0), otherwise **holds** the value it last took — a push button takes one fresh reading per
    press, a switch streams while on. The held value is a hidden `held` Prop, saved when streaming
    stops (so the reading survives a restart); readers downstream get the held value too, so a
    tag can gate a whole chain. Icon: green waves (outer one pulsing) while streaming, grey while
    holding (the screen then says HELD and the number dims); a ring (amber / grey) once linked,
    with an amber wire to it. Clicking the icon then the tag itself goes back to always streaming
    (or the x on its panel row). The stream lookup uses the same loop guard as the value
- **Label** (E menu ▸ Annotations): a world-space title for a region — scales with zoom like a
  node (unlike a NameTag), with **Size** and a width that follows its text. Two independent
  looks to click through — frame **Style** (`LABEL_STYLES`, each with its own padding): *plate*
  (nameplate + accent bar), *bevel* (raised 3D block like a button cap), *inset* (sunk in an
  input-style housing), *ribbon* (swallowtail banner), *neon* (glowing tube), *brackets* (HUD
  corners), *double*, *outline*, *underline* — and text **Effect** (`LABEL_EFFECTS`): flat,
  shadow, raised (emboss), engraved, 3D (extruded in the accent color), glow (blurred copy
  added onto the canvas); fonts Bahnschrift / Segoe UI / Georgia / Consolas / default (`get_font` now takes a
  system font name, falling back to the default); spaced capitals on by default. Rendered
  text is cached per (text, px, font, …), and very small/huge zooms draw just the shape
- **Place mode**: picking a point type (E menu or Add menu) puts a see-through copy on the
  cursor — click drops it there and selects it, **Shift+click** keeps placing more, **Esc**
  cancels. Pan/zoom stay live. Button/NameTag ask for their text first, then place
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
- **Right-click menu**: a right-*click* (the mouse moves ≤ `RIGHT_CLICK_SLOP` px between press
  and release; more is a right-drag pan) opens a `ContextMenu` at the cursor for the selection
  — right-clicking an unselected object selects just it first; nothing selected, no menu. One
  object gets the same menu as its panel's ⋯ button. Several get *Align horizontally (same X)*
  — their centers line up in a column at their average X, each keeping its Y — and *Align
  vertically (same Y)* (a row), for the movable ones (`App.align`); then *Create machine…*
  (below), then *Delete N objects* (red), which goes through the same confirm as the Delete key. `App.context_menu(pos, [(label, callback, style)])` opens
  one; it's a modal, so a click elsewhere or Esc closes it. Not while a mode (placing, picking,
  building) is active
- **Machines** (`canvas.py`, "Machines" section): a saved group of objects, placed again as a
  whole. Select objects, right-click ▸ *Create machine…*, name it: the selection's saved data
  (`to_dict`, minus each type's `COPY_SKIP` — a data collector's `rows`, a neuron's weights) goes
  into `machines.json` next to Nodes.json (git-ignored, like it) with the group's center. Only
  copyable, non-`SINGLETON` objects go in — trains and the instrument server are left out (the
  confirmation says which). A taken name gets " (2)". The E menu's **Machines** tab (always shown;
  empty, it says how to make one) has a tile per machine — its icon a miniature of its objects
  (`draw_machine_preview`) — and right-click on one renames / deletes it (`App.machine_menu`,
  back to the E menu after). Picking one puts a `MachineGroup` in hand: PlaceMode moves and ghosts
  all its objects together and drops them all with one save (`Scene.add_many`), selected as a group
  (Shift+click keeps placing more copies). `build_machine` gives every object a new id and rewires
  links between the machine's own objects to the new copies by swapping every old-id string in the
  saved data (`_remap_ids`) — so any link style works without each type listing its links; links
  to objects *outside* the machine are kept (they reconnect if that object exists, else show as
  unlinked). Once placed they're ordinary objects — there's no lasting "machine" object. Device
  objects keep their server device name, so a placed copy drives the same hardware
- **Remembers your view**: on close, the world point at screen center + zoom are written to
  `Nodes.json` as `"view"`; on launch the camera is restored there. Normal saves keep it
- **Text boxes** have a movable cursor: Left/Right, Home/End, Backspace/Delete,
  Ctrl+Backspace (word); long text scrolls sideways. Key-repeat (`TEXT_KEY_REPEAT`) is
  switched on only while a text box is open, so held keys don't repeat on the canvas
- **Copy** (Edit menu) for buttons/name tags/walls; **C** key drops a copy of the selection
  centered at the mouse cursor (works for any node type + walls); clipboard paste (**q** or Ctrl+V) in text fields

- **Devices (instrument server)** — real hardware, run outside the canvas:
  - `instrument_server.py` (launched by the Instrument server object, or by hand) opens
    every device in `devices.json` through its driver in `drivers/`, keeps the connections
    open (so restarting the canvas never resets an Arduino, and devices **hold their last
    value** when the canvas closes), and serves JSON-lines over a socket. One worker thread per
    device applies the *newest* requested value per param, then polls `read()` at the driver's
    `poll_hz`; a failing driver shows "error" and is retried (module re-imported) every 3 s.
  - **Local only, by design:** it listens on `127.0.0.1:47800` — the devices are plugged into
    this PC, and only programs on this PC can reach the server.
  - Drivers (`drivers/base.py`): `params` (things you set: `Param(min, max, unit, default)`,
    clamped by the server before `set_<name>` is called), `readings` (things you read:
    `Reading(unit)`), `open` / `read` / `close`. `drivers/fake_arduino.py` simulates a servo
    (`angle` → `position`, which travels at ~120°/s) and a light sensor, for testing without
    hardware. Each module names its class with `DRIVER = …`.
  - `instruments.py` is the canvas's side: a background thread that (re)connects every second,
    sends only the newest value per (device, param) at most 30×/s (a dragged slider can't
    flood a serial line), and keeps the latest catalog / readings / statuses for the canvas
    (`client.device(name)` returns a snapshot). `start_server()` opens the server in a console.
  - **Device** object (E menu ▸ Devices): name + status light (green connected, amber starting,
    red error / not on the server, grey server offline) + a screen of params (orange, what it's
    set to) and readings (green, what it reports). Inspector: cycle which server device it is,
    set a param by hand, or link it (each param is an input: **link** → click a source, e.g. a
    slider); its number readings and its params' settings are outputs, so a Readout can show
    e.g. the servo's actual position (a param that shares a reading's name gives
    `<param> (set)`; text readings are shown under State, not linked). The names of its params /
    readings are saved (hidden `catalog`), so it can be wired up while offline. Links are
    saved as a hidden `"data"` Prop (`Prop(kind="data")` holds any JSON; defaults are deep-copied
    per object, and so is the value written by `to_json` — before that, a copy made with **C**
    shared the original's link dicts, so relinking one rewired both).
  - **Instrument server object** (E menu ▸ Devices, one only — `SINGLETON`, picking it again
    goes to it): a radio with an antenna (signal waves while connected, blinking amber while
    connecting). It's the on/off switch for the hardware side: `instruments.client` only
    connects while its saved `connect` flag is on. **Connect** launches `instrument_server.py`
    in its own console if nothing is listening yet (also after a canvas restart, if it was left
    connected), **Disconnect** drops the link but leaves the server (and devices) running,
    **Stop server** sends `shutdown`. Deleting it disconnects (`on_removed` hook). Its panel
    lists the server's **devices** (click: place a Device object for it, or go to the one
    already placed) and **drivers** (click: name + port → `add_device`, which the server
    writes to devices.json, then place its object). A Device can *Remove from server*.
  - **Per-driver canvas objects:** a `Device` subclass that sets `DRIVER = "<module>"` is used
    for that driver's devices (`device_class_for`) — the place for custom looks / controls.
  - **Connecting is per object now.** `instruments.client.want(key, on)`: the link is open while
    anything wants it (a Device's **Connect**, or the Instrument server's), and the client itself
    launches `instrument_server.py` once if nothing is listening. A driver-specific Device
    (`DRIVER` set) **provisions its own device**: it picks a free name (`servo`, `servo2`, …),
    owns its options (`device_options()` — e.g. the servo's Port / Baud props), and asks the
    server to `add_device` / `configure_device` until the server's copy matches. Status: off →
    connecting → starting → connected (or error, with the driver's message). The generic Device
    is no longer in the E menu (placed from the Instrument server's device list). The Instrument
    server object is optional; its *Disconnect all* / *Stop server* turn every device off.
    devices.json starts empty — the fake Arduino device was removed (its driver stays, for tests).
  - **Arduino servo** (`drivers/arduino_servo.py`, canvas object *Arduino servo*): writes the
    angle as text + newline (`b"90
"` — what typing 90 + Enter in the Serial Monitor sends) at
    the configured baud (9600), rounded, skipping repeats. One-way, so no readings: the status
    light shows whether the port is open. `port` can be `"auto"` — only boards with Arduino's
    own USB vendor ids (0x2341 / 0x2A03) are matched, never the PC's other serial equipment.
    Opening the port resets an Uno, so `open()` waits 2 s, then re-sends the current angle.
    The server keeps param values across driver restarts (reconnects, port changes), so a
    replugged servo returns to where it was rather than to the default. A Device's panel has a
    **Port** row (only Arduino ports are suggested) → `configure_device`, which restarts it.
    Only one program can hold a COM port: close the Arduino IDE's Serial Monitor first.
    The canvas object draws the servo from above — blue case with ears, a 0–180° scale, and a
    white horn that swings to the set angle — over the generic Device's panel.
  - **Zaber stage** (`drivers/zaber_stage.py`, canvas object *Zaber stage*): wraps the lab driver
    `../hardware drivers/zaber.py` (imported, not copied). One device per stage; stages on the
    same port share one chain connection (ref-counted: first opens, last closes). The registry
    survives the server re-importing drivers (`globals().get(...)`) — a reset registry made a
    second stage try to reopen the port ("in use"). `sim://` refuses a second open, like a
    real port, so tests catch that. The server reads a device's status before its driver and
    sends catalogs one at a time, so "ok" always arrives with the device's params. Options:
    `port` (never scans — `auto` only matches Zaber's own USB id 0x2939, since an FTDI port here
    may be the laser), `stage` (0-based chain index), `baud`. Param `target` (mm) gets the
    stage's real travel range in `open()` (drivers can set `params` per instance); readings
    `position`, `moving`, `homed`; actions `home`, `stop`. Moves don't wait, so a slider keeps
    re-targeting; connecting never moves (target starts at the current position). `sim://` is a
    built-in pretend 3-stage chain. The canvas object draws a stepper motor on a rail with the
    carriage at the real position, an orange target marker, a glow while moving, NOT HOMED.
  - **PM100D power meter** (`drivers/pm100d_meter.py`, canvas object *PM100D*): wraps the lab
    driver `../hardware drivers/pm100d.py` (pyvisa / NI-VISA, USB — not a COM port). Option
    `address`: `auto` (the lab driver finds the Thorlabs VID 0x1313 resource), a VISA resource
    string, or `sim://`. Readings `power` (W) and `power_mW`; param `wavelength` (nm, the
    sensor's correction range, starting at the meter's own setting — connecting never changes
    it). The object is a display: the handheld console with a backlit LCD, power auto-scaled to
    nW / µW / mW / W (4 significant digits, `format_power`), the wavelength, and a log level
    bar from 1 nW to 1 W.
  - **PDA45 photodetector** (`drivers/pda45.py`, canvas object *PDA45*): one detector, two
    instruments — wraps the lab drivers `../hardware drivers/timetagger.py` (Swabian, counts
    pulses) and `ni6363.py` (`NI6363`, analog in). Each reading starts both over the same
    `gate` (param, s, default 0.2), then reads `count_rate` (counts/s — counts over the
    tagger's *measured* capture time) and `voltage` (V, the mean on ai0 — per ni6363.py the
    only meaningful voltage number for ns pulses). Also `overflows` (lost events: the rate is a
    lower bound) and `clipped` (samples at the range ends: the mean is wrong); text
    `tagger_status` / `daq_status`. The halves connect **independently** — a missing one is
    retried every 3 s inside `read()` while the other keeps reading (a failure never restarts
    the device), so its number just drops out (readouts show dashes). Options: `tagger` /
    `daq` (`auto`, serial / device name, or `sim://`), `ai_range` (±V, default 0.5),
    `tt_channel` (2), `trigger_v` (2.5), `ai_channel` (ai0). The object has a status light
    per half (the header light is amber while only one is reading) and big SI-scaled numbers
    (`format_si`: 295.6 k counts/s, -4.598 mV), with OVERFLOW / OVER RANGE flags. The
    `pda45.py` the lab drivers mention never existed; this is it, for the canvas.
  - **SuperK laser** (`drivers/superk_laser.py`, canvas object *SuperK laser*): wraps the lab driver
    `../hardware drivers/superk.py` (nkt_tools / NKT DLL). Safety rules from the Quantum Yield
    control GUI: emission changes **only** through actions (`emission_on` / `emission_off`) —
    never a linkable param, since linked params are re-sent after a reconnect; `emission_on` is
    refused while the interlock is open (with the reason); the object's button is green "Turn
    emission ON" when idle and red "Turn emission OFF" when emitting **or unknown** (never
    guesses); the object also has a built-in switch (below). Emission goes off on the object's Disconnect, when the server loses its last
    client (`on_no_clients` hook — the canvas closed or crashed; option
    `off_when_canvas_closes`, default on), and on any driver close (device restart / removal /
    read error / server stop — the lab driver turns it off on disconnect). Param `power_level`
    (0–100 %, 0.1 % steps, starts at the laser's own setpoint). Text readings `interlock`,
    `status`, `port` are shown but not outputs (Device outputs are numbers only). Port `auto`
    scans like the lab GUIs; set it explicitly to avoid opening other ports. `sim://` pretends.
    The object: laser head with a warning triangle, EMITTING / IDLE / UNKNOWN, power bar,
    interlock / status lines, and a rainbow beam from the output port while emitting.
  - **Built-in emission switch** (SuperK): the safety lives in the object. The SuperK draws its
    own slide switch next to EMITTING / IDLE / UNKNOWN; clicking it (on the canvas — it doesn't
    select) or the panel button toggles emission. It always shows the laser's *actual* state, read
    back — never a remembered position — so it can't disagree with the laser; unknown is an amber
    "?" and a click turns emission off. After a click the knob shows the asked-for side with an
    amber ring until the laser confirms (≤ 1.5 s), snapping back at once if the driver refuses
    (interlock open — the panel shows why). This replaced linking emission to an external switch
    (which could end up showing ON while the laser was off). Emission still isn't linkable.
  - **SuperK start-up state:** the emission register reads 0 (off) / 3 (on) and something else
    while the laser ramps up after "on"; nkt_tools raises "Unknown emission state" for that. The
    driver treats it as *unknown* (no `emission` reading) and carries on, as the Quantum Yield GUI
    does — letting it fail the read restarted the device, and the restart turned emission off.
    The object shows STARTING… for an unknown state within 30 s of an "on" request (UNKNOWN
    otherwise); the switch is an amber "?" (a click turns emission off). `sim://` ramps for 1.5 s.
  - **Flip mirror** (`drivers/flip_mirror.py`, canvas object *Flip mirror*): wraps the lab driver
    `../hardware drivers/flipper.py` (New Focus flipper on an NI 6363 output; nidaqmx). The mirror's
    channel and its up / down path names come from `flipper.SETUP` at connect time (option `mirror`:
    excitation | detection; `channel` auto | explicit | `sim://`). No readback, edge-triggered (see
    flipper.py), so: **connecting never moves it** (`connect(initialize=False)` — the NI output keeps
    its level; position starts unknown), since the server opens every device in devices.json when it
    launches. Then "It's at <path>" (`sync_up` / `sync_down` — nothing moves) or "Find position"
    (`find_position` — flips down then up; asks first: `App.confirm(..., ok_label=...)`). Known →
    click the mirror (or Flip) to toggle; param `up` (1/0) is linkable. Every position is labelled
    *assumed*. Closing the device (server stop / restart / removal, or switching `mirror`) parks
    the line at 0 V, so the mirror lowers — flipper.py's deliberate end state. The object draws the
    beam: up = mirror at 45° reflecting to the up path, down = mirror flat and the beam straight on,
    unknown = grey mirror and a "?".
    **Names are canvas-side labels:** the object's name (click *Name* in the inspector; new ones
    are *Flipper 1*, *Flipper 2*, …) and its *Up path* / *Down path* names (default up / down)
    are its own Props — shown on the drawing, the Flip / It's at buttons and the confirm. The
    driver's labels (`up_label` / `down_label` from flipper.SETUP) only identify positions inside
    the driver. *flipper.py mirror* (`mirror`) is the hardware choice — which SETUP entry, so
    which DAQ channel and which side is up; a second new mirror defaults to the other one. Old
    saves (no `up_name`) keep what they showed: the name becomes the mirror's (*Detection*) and
    the path names are taken from the driver the first time it's live, then saved.
  - **Confirm dialogs** take an `ok_label` ("Delete" by default) and grow to fit their text.
  - **Buttons:** `Btn(style="stop")` is red.
  - **Server robustness:** the server's console replaces characters it can't print (λ, µ …)
    instead of raising, and `log()` never raises — a print inside a driver's error used to kill
    that device's worker thread, leaving it stuck at "starting".
  - **Excitation filters** (`drivers/filter_band.py`, canvas object *Excitation filters* — was *Excitation
    band*; TYPE stays `filter_band`): the longpass +
    shortpass linear variable filters as one device in **nm** — params `center`, `width`; readings
    the actual band and each cutoff, read back from the stage positions. Wraps the Zaber chain
    through `zaber_stage.py`'s shared chain registry (so Zaber stage devices on the same port
    coexist), finding each stage by **serial** (the chain index isn't stable; `sim://` uses indices).
    The calibration is read from **`calibration/excitation_filters.json`** when the device opens
    (`load_calibration`): per filter, its stage (`serial`, or chain `stage` index for sim) and its
    logged points (`position_mm`, `cutoff_nm`, `logged`); a least-squares line per filter (the QY
    GUI's *linear* mode; `LinearFilter` also has `r2`). Under 2 points, or no file → the built-in
    `CALIBRATION` (the QY GUI's points; the file was seeded with them). Explicit
    `longpass_serial` options still win over the file. Extrapolated over the stage travel but held to its 400–800 nm working span. Unreachable
    requests are pulled in to the nearest reachable band (setting center keeps the width and vice
    versa, so only the param that was set changes); nothing moves until both stages are homed, so
    a band is never half-applied. The object is a **color picker**: a spectrum strip — click / drag
    sets the center (unless center is linked), the passed band stays lit, the requested band shows
    as an orange outline while the stages move. Its panel says where the calibration lives.
  - **Filter wheel** (`drivers/filter_wheel.py`, canvas object *Filter wheel*): wraps the lab driver
    `../hardware drivers/fw102c.py` (Thorlabs FW102C, 6 slots, USB serial). Param `position` (1 …
    slots, read from the unit; any number is rounded to a slot, so it can follow a slider or a
    Function) starts where the wheel is — connecting never moves it. Readings `position` (what the
    wheel reports), `slots`. A move blocks ~1 s a slot; the server applies only the newest request.
    Port `auto` only takes a port whose USB serial starts "TP" (Thorlabs) — never the lab driver's
    *idn? sweep over every FTDI port (the Zaber chain, maybe the laser); `sim://` pretends. **Quirk:**
    on this unit the first command after the port opens comes back CMD_NOT_DEFINED, so the lab
    driver's identify always failed; the canvas driver sends a bare CR first (a subclass —
    fw102c.py is unchanged). Slot **names** live on the object (a hidden `names` list, blank =
    unnamed / open), set from the panel. The object draws the wheel face-on with the slot in the
    beam at the top (it turns there, the short way); click a slot to go (amber ring until the wheel
    reports, as the flip mirror does), off while `position` is linked ("follows …" shown instead).
  - **Scripts** (`scripts/`): standalone procedures with their own window, run from a terminal —
    not wired to canvas objects yet. **`calibrate_excitation_filters.py`** (tkinter; `--port
    sim://` to try it): a panel per filter — stage picker (by serial; stage 0 is the ND), ◀ ▶ jog
    with a step choice (arrow keys move the panel last clicked, Shift = 10×), Go to mm, Home —
    then type the cutoff seen on the spectrometer and **Log point** (Enter). Every change is
    written straight to the calibration file (temp file + replace). The table shows each point's
    distance from the fitted line; with 4+ points, the one whose removal makes the rest fit within
    2 nm is flagged red as a likely typo (with 3 it only says they disagree). *Clear all* copies
    the file to `calibration/archive/` first. It opens the Zaber chain itself (through
    `zaber_stage._open_chain`), so the instrument server must be stopped first — it holds COM3;
    a failed Connect says so. The canvas picks up a new calibration when the device reconnects.
  - **Driver actions** (general): a driver's `actions = ["home", …]` become buttons on its Device
    (`{"op": "action"}`). Errors from `set_<param>` / actions show as the device's message without
    restarting it. A Device's outputs list readings first, so a Readout links to e.g. `position`.
  - pyserial is required (`pip install pyserial`); the unrelated `serial` package (a JSON/YAML
    library, same import name) was removed from this PC's miniconda for that reason.

**Not built yet:** E-menu search / hotbar · more object types (Chest, freeform region, …) ·
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

The panel is **object-driven** and the same for every type (see *The panel* below); it
knows no types, and the loop just runs whichever callback was clicked. Launchers (Button /
NameTag) show their Command under State (click to set it) and **Activate** (green when the
node has a command). Double-click any object with a `center` is a shortcut for Go To.

---

## The panel (built)

Every panel has the same parts, always in this order (a section with nothing in it is left out):

| Part | Holds | Comes from |
|---|---|---|
| **Header** | type name; the object's name if it has one (click to rename); a status line (device status, an equation's problem, a relay loop); the **⋯** menu | `panel_name()`, `rename(app)`, `panel_status(app)` |
| **State** | what it is / shows now, and the main thing you edit (equation, counter value, slot names) | `state_rows(app)` + Props with `state=True` |
| **Inputs / takes** | every input: its name, then **link** (and the value set by hand, if any) or *source · output* in green with an x to unlink, and the live value under it | `takes()` (+ `input_rows(app)`, e.g. *+ add column*) |
| **Outputs / gives** | a plain list of what it gives, with live values; click one to link it into another object's input | `gives()` / `outputs()` |
| (extra) | e.g. a neuron's W's, the instrument server's device / driver lists | `more_sections(app)` |
| **Settings** | visible Props: size, color, unit, … | `PROPS` |
| **Info** | ID, position, driver, port, file paths — folded by default | `info_rows(app)` |
| **Footer** | buttons for doing the object's job: the main one first (green), connection last (Connect / Disconnect); past 4, the rest go in the ⋯ menu | `actions(app)` / `@panel_button` |

- Rows: `Field` (label over value, wraps), `Row` (label left, value right; `sub=True` indents
  it), `Stepper`, `Gap`, `ScrollList` (up to 10 rows, wheel-scrolled), inside `Section`s; plus
  `PortRow` / `GiveRow`, which the panel draws itself from a Take / Give.
- Clicking a section's title folds it; the panel remembers that **per type** (for this run).
- The whole body scrolls with the wheel (a list under the mouse scrolls first), so rows never
  get cut off behind the footer.
- Hovering a linked input's row rings its source on the canvas.
- **The ⋯ menu** (the same menu a right-click on the object opens): footer buttons that didn't
  fit, the type's own items (`menu_items`: *Clear rows…*, *Remove from server…*, *Stop
  server…* — red), then Rename…, Copy, Go to, Unlink all inputs, Create machine…, Delete.
- Linking, editing and destructive actions are not footer buttons any more: *Link to source…*,
  *Unlink*, *Unlink all*, *Always stream* became input rows; *Edit equation…*, *Set command…*,
  *Add column…*, *Adjust speed…* became clickable rows; *Clear rows…*, *Remove from server*,
  *Stop server* moved to the ⋯ menu.

---

## Inputs and outputs — takes / gives (built)

**An output (Give) is a named value an object offers. An input (Take) is a named slot that
follows one output.** Every wire runs from an output to an input; an output can feed any
number of inputs, an input has at most one source. Every value is just a number; on/off is 0
or 1 (anything that isn't 0 counts as on). Text stays display-only.

- **Outputs:** `outputs()` → `{name: number or None}` is how values are read; `gives()`
  declares them (name, unit), so the panel and the pickers can list them even while a value is
  None. Every output is always listed — no value is `None`, never a missing key.
- **Inputs:** `takes()` → `[Take(key, label, kind, unit=, current=, set_by_hand=, on_label=)]`.
  The kind is the only real difference between the old link styles:

  | Kind | What it does | Used by |
  |---|---|---|
  | `number` | follows the value | Readout, Function variables, device params, Relay, Tag value, collector columns ("records") |
  | `onoff` | on while the source isn't 0 | Tag *streaming* |
  | `press` | acts once each time the source goes 0 → 1 (`on_press(port)`); already on when first read doesn't count | Counter up / down / reset, collector *collect* |

  Any input can link to any output.
- **One storage shape:** every object's links are one hidden Prop, `links = {port: {"id", "key"}}`
  (saved only when not empty). Older saves are converted on load by each type's `migrate(d)`:
  a readout's `source_id` / `source_key`, a relay's `source`, a tag's `stream`, and a
  collector's `trigger` and per-column `id` / `key`. Machines remap ids inside it like any
  other saved data.
- **Helpers (base class):** `take(port)` (the value, or None), `take_source(port)`,
  `take_on(port)`, `link(port, src, key)`, `unlink(port)`, `unlink_all()`, `pick(app, port)`.
  The scene calls `watch_presses(dt)` every frame, which fires `on_press` — so no type keeps
  its own edge-tracking.
- **Linking, one way everywhere:** click **link** on an input's row (or its chip on the
  canvas), then click a source (`App.pick_link`, PickMode: rubber band + green ring; clicking
  the object itself unlinks); if the source has several outputs, a menu at the cursor lists
  them with live values (for press / on-off inputs `pressed`, `on`, `value` come first). Click
  the output's name on a linked row to choose a different one. The other way round: click an
  output in *Outputs / gives*, then the object to feed (`App.pick_target`, a menu of its
  inputs), or **drag from an output nub** (Function, Counter, Tag — `give_at`) and let go on an
  input chip (`take_at`) or anywhere on an object with inputs (`LinkDrag`).
- **Wires** are drawn by the base class from `links`, from the source's `output_point()` to the
  reader's `input_point(port)`: number = green flowing dots; on/off = amber, flowing while on;
  press = dim amber that flashes when it fires.
- **Devices:** every param is an input (set by hand while unlinked, or linked); number
  readings and param settings are outputs. Their names are saved (`catalog`), so an offline
  device can still be wired.
- **Out of scope:** neuron W's (an all-to-all weight matrix, shown as their own section) and
  text values.

**Dialogs are callbacks.** `app.ask(prompt, on_submit)` opens a text box and calls
`on_submit(text)` on Enter; `app.confirm(message, sub, on_confirm)` is the delete dialog.
That replaced a `purpose`-string switch in the loop that every new prompt had to extend.

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

## Adding an object type (the recipe)

The goal is lots of variety cheaply: most new objects are a drawing with a few properties
and maybe an action or two. So the common case is one small class in `things.py`:

```python
class Lamp(Thing):
    TYPE = "lamp"                  # saved type name — don't rename once you've placed some
    MENU_LABEL = "Add lamp"        # Add-menu entry (leave out to hide it)
    SIZE = (50, 50)                # world units
    DOT_COLOR = (255, 220, 120)    # what it shows as when zoomed far out
    PROPS = [Prop("on", "On", "bool", False),              # → self.on, saved, click toggles
             Prop("color", "Light color", "color", (255, 220, 120))]

    def draw_shape(self, surface, rect, camera):           # rect: on-screen box, zoomed
        pygame.draw.ellipse(surface, self.color if self.on else (70, 70, 78), rect)

    def tick(self, dt):                                    # optional: animate (dt in seconds)
        ...

    @panel_button("Switch on / off")                       # optional: an inspector button
    def toggle(self, app):
        self.on = not self.on
        app.save()
```

That's in the E menu (with an icon drawn from `draw_shape`), placeable, saved, loaded,
selectable, movable, copyable and inspectable — nothing else to touch. `Thing` supplies
the selection ring, hover, zoom-out dot and the rest.

- **E menu:** `CATEGORY = "Lights"` adds a tab; `NAME` overrides the tile name;
  override `icon_sample()` (e.g. return one switched on) or `draw_icon(surface)` for a
  custom icon.

- **Prop kinds:** `"float"`, `"int"` (min/max clamp), `"str"`, `"bool"` (click toggles),
  `"color"` (`r, g, b` or `#rrggbb`), `"choice"` (`choices=[…]`, click cycles). Extra
  `unit=`, `prompt=`, `editable=False`, `state=True` (listed under State, not Settings).
- **Outputs:** return `{name: number or None}` from `outputs()` and any input (a Readout's,
  a device param, …) can link to it. Override `gives()` to give them units. Subclass
  `Output` to make a new kind of display.
- **Inputs:** list `TAKES = (Take("up", kind="press"), …)` (or override `takes()` when they
  change), read one with `self.take(port)`, and for press inputs override `on_press(port)`.
  Override `input_point(port)` / `take_at(pos, camera)` if it has chips. The panel rows,
  linking and wires come for free.
- **Resizable:** every Thing has `scale` (world size = `SIZE × scale`). List
  `Prop("scale", "Size", "float", 1.0, min=…, unit="×")` to make it editable + saved
  (the Inputs family does).
- **Live state** that shouldn't be saved (an animation phase) is a plain attribute set in
  `__init__` after `super().__init__`.
- **Families:** subclass another Thing to share its look/props/behavior; add props with a
  `PROPS` list of its own (lists merge down the hierarchy; same key overrides).
- **Panel:** fill in the standard sections rather than building your own — `state_rows`,
  `info_rows`, `panel_status`, `actions`, `menu_items`, `more_sections` (Neuron uses most of
  them: clickable rows, a stepper, a Weights section with a scroll list).
- **Not a point object?** Subclass `CanvasObject` directly (like Wall or Train) and
  implement `draw`, `hit`, `to_dict` / `from_dict`, plus `center` / `in_box` / `move` if
  it should Go To / drag-select / move.
- **Renaming or deleting a type** never loses data: saved objects of an unknown type load
  as invisible `UnknownObject`s and are written back as they were.

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

## Bench notes (the optics the software has to respect)

- **The power meter only sees light with the detection flip mirror UP.** The detection
  mirror (device `flipper` in devices.json, `mirror: detection`; on the canvas *Power meter
  flip*, paths *Power meter* / *Sphere*) sends the beam to the PM100D when up and on to the
  sphere when down (`flipper.SETUP`: `DETECTION = up "PM100D", down "sphere"`). Anything that
  reads the power meter — a canvas setup or a script — must flip it up first, wait for it to
  settle (~0.75 s, flipper.py's `DEFAULT_SETTLE_S`), and put it back down afterwards if the
  detector needs the light. With it down the PM100D reads ~0 and nothing errors, so a
  forgotten flip only shows up as bad data. The position has no readback (*assumed*): sync or
  find it on the canvas before a script relies on it. `scripts/calibrate_nd_filter.py` does
  all of this (`--mirror`).

## What's next (open threads)

- **E menu extras** — a search box (type to filter), a hotbar of recent picks, and
  categories on the catalog classes once there are enough to group.
- **New object types** — the catalog in `things.py` (see the recipe above). Once it's long,
  give classes a category so the Add menu / palette can group them, and split `things.py`
  into a folder of themed modules.
- **Animated connectors** — would need time passed into `draw`/`update` (flowing route,
  marching ants). Not plumbed yet.
- **Inspector editing** — edit description inline; a close control for the panel.
- **Panel extras** — remember folded sections across restarts; list an output's readers.
- **Nice-to-haves** — Esc-to-deselect, build-preview refinements.
