# Canvas Lab — Objects

> What each object type does and how it looks, grouped like the E menu. How the shared
> machinery works (the panel, inputs / outputs, the world server, devices) is in
> [ARCHITECTURE.md](ARCHITECTURE.md); adding a new type: *Adding an object type* there.

---

## Inputs

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

- **Switch** (Inputs): an on/off slide switch — each click flips it (saved `on`), the knob
  slides across (`SLIDE_TIME`) and the track fills with its color; ON/OFF shows beside the
  knob when it fits. Size, color, and the On row are editable; a flip also runs its command
- **Slider** (Inputs): a vertical fader — drag the cap (or click the track to jump) to set a
  saved `value` between `min` (bottom) and `max` (top), snapped to `step`; the slot fills
  with color below the cap and a readout shows the value. Drag is handled in the object's own
  `update()` (it gets the mouse + camera), saving once on release. Size, length, min, max,
  step, value, color editable. `value` is the parameter other objects will read later

---

## Displays

- **Displays** show one linked value (how values are linked: *Inputs and outputs* in
  ARCHITECTURE.md). Objects give live values (`outputs()`: Slider `value`, Switch `on`, PushButton
  `pressed`, Lamp `on`, Neuron `firing` / `clock t` / `input W`, …) and take them on
  named inputs (`takes()`). The `Output` base (E menu ▸ **Displays**, was *Outputs*) has one
  input, `in`, read into `reading` every frame and passed on as `value`. First display:
  **Readout**, an LED display — seven-segment digits with unlit "ghost" segments, a unit
  beside them (free text: mW, ms, V, …), decimals, digit slots, color, size, and the object's
  name printed on the bezel. Dashes when unlinked

---

## Math

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
  "stored-up" presses), and a new limit pulls the value inside it. (Buttons and switches
  don't count their own presses any more — link a Counter to count them.)
  Values are rounded to 12 places (0.1 × 3 = 0.3). Click the number to type one; the panel has
  + / - / Reset buttons (and Value / Minimum / Maximum under State). The value is saved (a `value` Prop, saved on each change) and is the
  output `value`. Drawn Function-style: chips, a screen with the number (brightens on a change,
  dims at a limit), an output nub, and the step / limits beside the name

---

## Data

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
  *Export CSV* writes `saves/data/<name> <date time>.csv` (row, time, columns; git-ignored); *Clear rows…*
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

---

## Annotations

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

---

## Trains — the first connector object

- **Train** object: orthogonal connector between two nodes, camera-ride on arrow click,
  adjustable speed, interactive build mode, route auto-simplify (heals old overhangs on load)

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

## Walls — the first area object

- **Wall** object: outline-only rectangular zone, drag-to-draw, fixed-size corner name tag,
  border scales with zoom

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

## Agents — the first animated object

- **Agent** object: a little orange square with eyes that idly wanders (walk/pause/walk),
  confined to whichever Wall contains it — the first *animated* object, so objects now
  get frame-time (`dt`, in `tick`). It holds still while any player has it selected

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

## Neurons

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

---

## Lab devices

Each one is a Device object for one driver in `drivers/`; how devices connect is in
[ARCHITECTURE.md](ARCHITECTURE.md) (*Devices and the instrument server*).

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
