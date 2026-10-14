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
  (saved + inspector-editable fields), inspector rows returned by the object
  (`inspector()` / `buttons()` / `@panel_button`), callback dialogs via `App.ask` /
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
  tabs ("All" + one per `CATEGORY` any type sets). Icons draw themselves: the default
  `draw_icon` draws a sample object (`icon_sample()`) scaled to fit, so a new type gets an
  icon for free (Train/Wall hand-draw theirs; a crashing icon shows a "?" tile instead).
  Picking a tile runs the type's `create`. Remembers its tab + scroll
- **Inputs** (E menu ▸ Inputs, in `things.py`): an `Input` base — a click *presses* it
  (`press(app)`, default: run its command) instead of selecting it; it tracks hover /
  held / a short press flash for drawing and draws the shared housing; subclasses draw
  `draw_face`. First member: **PushButton**, an arcade-style square cap that brightens +
  glows on hover and sinks into its housing when pressed (cap color + name-on-cap).
  **Ctrl+click** selects any object without interacting with it (drag-box works too)
- **Switch** (Inputs): an on/off slide switch — each click flips it (saved `on`), the knob
  slides across (`SLIDE_TIME`) and the track fills with its color; ON/OFF shows beside the
  knob when it fits. Size, color, and the On row are editable; a flip also runs its command
- **Slider** (Inputs): a vertical fader — drag the cap (or click the track to jump) to set a
  saved `value` between `min` (bottom) and `max` (top), snapped to `step`; the slot fills
  with color below the cap and a readout shows the value. Drag is handled in the object's own
  `update()` (it gets the mouse + camera), saving once on release. Size, length, min, max,
  step, value, color editable. `value` is the parameter other objects will read later
- **Outputs + links**: any object can publish live values with `outputs()` → `{name: number}`
  (Slider `value`, Switch `on`/`flips`, PushButton `pressed`/`presses`, Lamp `on`, Neuron
  `firing` / `clock t` / `input W`). The `Output` base (E menu ▸ Outputs) links to one source
  — *Link to source…*, then click it (`App.start_picking` / `PickMode`: green ring + rubber
  band, Esc cancels) — and reads `source.outputs()[source_key]` into `reading` every frame.
  *Reads* cycles which value; *Unlink* clears it. The link is saved as hidden Props
  (`Prop(..., hidden=True)`: saved, not shown). A wire with flowing dots is drawn via the
  `draw_links` hook (all objects' links are drawn before any object, so wires sit under them).
  First output: **Readout**, an LED display — seven-segment digits with unlit "ghost" segments,
  a unit beside them (free text: mW, ms, V, …), decimals, digit slots, color, size, and the
  object's name printed on the bezel. Dashes when unlinked
- **Function** (E menu ▸ Math, `things.py`; evaluator in `expressions.py`): `y = <equation>`.
  Placing one asks for the equation first; a click on it reopens the text box (Ctrl+click
  selects). Every name that isn't built in (`expressions.RESERVED`: sin cos tan asin acos atan
  atan2 sinh cosh tanh sqrt exp log log10 log2 abs min max round floor ceil sign clamp, pi, e,
  and `t` = seconds since the canvas started) becomes a variable *chip* on the left edge, in
  the order it first appears — hollow until linked, filled once linked. Click a chip, then a
  source (anything with outputs); clicking the function itself unlinks that variable. Links
  are a hidden `links` dict keyed by variable name, so editing the equation never loses wiring
  (a name that comes back reconnects; links for absent names are kept but not drawn). Output
  is `{"y": value}`; it's `None` while there's no value (unlinked variable, bad syntax, 1/0,
  sqrt(-1), NaN/inf), with the reason in red on its screen and in the inspector — a Readout
  then shows dashes and a Device skips it (holds its last value). Range checking is the
  drivers' job. The equation is parsed with `ast` against a whitelist and compiled once per
  text change; numbers are floats (so `9^9^9` overflows instead of hanging). `^` = power,
  `2P` = 2*P (not inside names like `log10`, not `1e-3`), a leading `y =` is ignored. Wires
  from a source end at the chip and leave a Function from its output nub: `output_point(obj)`
  uses an object's `output_point()` if it has one, else its center (Output and Device wires
  use it too). Chains and loops are fine — each Function computes once a frame in `tick` and
  `outputs()` returns the stored value, so a loop just runs a frame behind
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
    set a param by hand, or link it (*follows* → click a source, e.g. a slider); its params and
    readings are `outputs()`, so a Readout can show e.g. the servo's actual position. Links are
    saved as a hidden `"data"` Prop (`Prop(kind="data")` holds any JSON; defaults are deep-copied
    per object). When offline it offers *Connect instrument server* (or *Place* one, if none).
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
  - **Confirm dialogs** take an `ok_label` ("Delete" by default) and grow to fit their text.
  - **Buttons:** `Btn(style="stop")` is red.
  - **Server robustness:** the server's console replaces characters it can't print (λ, µ …)
    instead of raising, and `log()` never raises — a print inside a driver's error used to kill
    that device's worker thread, leaving it stuck at "starting".
  - **Excitation band** (`drivers/filter_band.py`, canvas object *Excitation band*): the longpass +
    shortpass linear variable filters as one device in **nm** — params `center`, `width`; readings
    the actual band and each cutoff, read back from the stage positions. Wraps the Zaber chain
    through `zaber_stage.py`'s shared chain registry (so Zaber stage devices on the same port
    coexist), finding each stage by **serial** (the chain index isn't stable; `sim://` uses indices).
    The calibration is **hard-coded** (`CALIBRATION`): the points from the Quantum Yield GUI's
    `filter_calibration.json`, a least-squares line per filter (that GUI's *linear* mode),
    extrapolated over the stage travel but held to its 400–800 nm working span. Unreachable
    requests are pulled in to the nearest reachable band (setting center keeps the width and vice
    versa, so only the param that was set changes); nothing moves until both stages are homed, so
    a band is never half-applied. The object is a **color picker**: a spectrum strip — click / drag
    sets the center (unless center is linked), the passed band stays lit, the requested band shows
    as an orange outline while the stages move. Editing the calibration on the canvas is for later.
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
| `PROPS` | saved, inspector-editable fields | none (inherited lists merge) |
| `inspector(app)` | rows for the panel | name + every Prop |
| `buttons(app)` / `@panel_button` | panel buttons | every `@panel_button` method |
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

The panel is **object-driven**: it shows whatever rows the selected object returns from
`inspector(app)` — `Field` (label over value), `Row` (label left, value right), `Stepper`
(◀ value ▶), `Gap`, `ScrollList` (wheel-scrolled, fills to the buttons) — each optionally
clickable with an `on_click(app)` callback, then the object's `buttons(app)` stacked at
the bottom. The panel knows no types; the loop just runs whichever callback was clicked.
Launchers (Button / NameTag) show name, position, id, description, command, with
**Set command…** and **Activate** (green when the node has a command). Double-click any
object with a `center` is a shortcut for Go To.

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
  `unit=`, `prompt=`, `editable=False`.
- **Values:** return `{name: number}` from `outputs()` and any Output (e.g. a Readout) can
  link to it and show it live. Subclass `Output` to make a new kind of display.
- **Resizable:** every Thing has `scale` (world size = `SIZE × scale`). List
  `Prop("scale", "Size", "float", 1.0, min=…, unit="×")` to make it editable + saved
  (the Inputs family does).
- **Live state** that shouldn't be saved (an animation phase) is a plain attribute set in
  `__init__` after `super().__init__`.
- **Families:** subclass another Thing to share its look/props/behavior; add props with a
  `PROPS` list of its own (lists merge down the hierarchy; same key overrides).
- **Custom panel:** override `inspector(app)` / `buttons(app)` (Neuron is the full example:
  compact rows, clickable rows, a stepper, a scroll list). `self.prop_rows()` gives the
  default Prop rows to mix in.
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

## What's next (open threads)

- **E menu extras** — a search box (type to filter), a hotbar of recent picks, and
  categories on the catalog classes once there are enough to group.
- **New object types** — the catalog in `things.py` (see the recipe above). Once it's long,
  give classes a category so the Add menu / palette can group them, and split `things.py`
  into a folder of themed modules.
- **Animated connectors** — would need time passed into `draw`/`update` (flowing route,
  marching ants). Not plumbed yet.
- **Inspector editing** — edit description (and maybe params) inline; a collapse/close control.
- **Nice-to-haves** — Esc-to-deselect, build-preview refinements.
