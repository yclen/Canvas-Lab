# Canvas Lab — Architecture

> How the app works now: the pieces, how they fit, and why they're built the way they
> are. The reasoning behind the big decisions is in [DESIGN.md](DESIGN.md); what each
> object type does is in [OBJECTS.md](OBJECTS.md).

---

## Core pieces

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
- **Error safety net**: the app runs without a console, so errors go to `saves/errors.log` (git-
  ignored) via `report_error` — once per distinct error. Each object's `update` / `draw` /
  `draw_links` is guarded in `Scene`: a failing object is logged and drawn as a red X box
  instead of crashing the app. Anything else uncaught is logged by `sys.excepthook` (in
  `game_loop.py`), which also saves the canvas before the app closes. (Found via a Readout
  crash at ~4% zoom: its glass-shine strip got a negative width. Objects drawn at tiny sizes
  should bail out to a simple shape, as Thing's LOD dot and the Readout now do.)

- Level-of-detail draw (buttons shrink to a dot; no Button↔Rectangle swap machinery)
- JSON persistence (`{"objects": [...]}`) behind `load_objects`/`save_objects`; stable ids, backfilled
- Menu bar (Add / Edit / Go To) + unified modal slot (text input, confirm)
- Inspector side panel: view a node's data, **Set command…**, **Activate** (runs detached)
- ACTIONS registry: `run_command` (shell, detached) + `goto` (internal camera jump)

---

## Working on the canvas

- **E menu (inventory)**: **E** (or *Objects (E)* in the menu bar) opens a window with a
  tile per placeable type — its icon + name — wheel-scrollable for a long catalog, with
  tabs ("All" + one per `CATEGORY` any type sets, + "Machines" — see *Machines*). Icons draw themselves: the default
  `draw_icon` draws a sample object (`icon_sample()`) scaled to fit, so a new type gets an
  icon for free (Train/Wall hand-draw theirs; a crashing icon shows a "?" tile instead).
  Picking a tile runs the type's `create`. Remembers its tab + scroll

- **Ctrl+click** selects any object without interacting with it (drag-box works too)

- **Place mode**: picking a point type (E menu or Add menu) puts a see-through copy on the
  cursor — click drops it there and selects it, **Shift+click** keeps placing more, **Esc**
  cancels. Pan/zoom stay live. Button/NameTag ask for their text first, then place

- Generalized selection + movement: any object can be selected/deleted; `movable`/`move()`
  capability (nodes + walls + agents move, trains don't); node-only actions guard for `Node`
- **Drag-box multi-select**: left-drag on empty canvas selects every node whose center is in
  the box (+ walls fully inside). Arrow keys (or WASD) move all of them; Edit ▸ Delete / **Delete** key
  removes all at once — no confirm. **Ctrl+Z** brings back your last delete (selected again),
  up to `UNDO_DELETES` (20) back: `Scene.delete_many(objs, by=player name)` keeps each player's
  deletes (saved data + place in the draw order + other neurons' W's to them; not saved to disk),
  `Scene.undo_delete(name)` restores them — links from other objects reconnect by id. `scene.selection` is the list; `scene.selected` is the single
  object or None when 0 or 2+, so rename/copy/goto/inspector only apply to one object
- **Right-click menu**: a right-*click* (the mouse moves ≤ `RIGHT_CLICK_SLOP` px between press
  and release; more is a right-drag pan) opens a `ContextMenu` at the cursor for the selection
  — right-clicking an unselected object selects just it first; nothing selected, no menu. One
  object gets the same menu as its panel's ⋯ button. Several get *Align horizontally (same X)*
  — their centers line up in a column at their average X, each keeping its Y — and *Align
  vertically (same Y)* (a row), for the movable ones (`App.align`); then *Create machine…*
  (below), then *Delete N objects* (red), which deletes at once like the Delete key (Ctrl+Z undoes). `App.context_menu(pos, [(label, callback, style)])` opens
  one; it's a modal, so a click elsewhere or Esc closes it. Not while a mode (placing, picking,
  building) is active
- **Machines** (`canvas.py`, "Machines" section; the object is `Machine` in `things.py`, see
  OBJECTS.md *Machines*): objects packaged as one. Select objects, right-click ▸ *Create
  machine…*, name it: `Machine.package` takes them off the canvas into a new Machine where they
  sat (they keep running inside it, in its own `MachineScene` — no `on_removed`, so devices stay
  connected), picks its ports, moves wires that crossed the edge onto it, and saves it to
  `saves/machines.json` next to Nodes.json (git-ignored, like it): the parts' saved data (`to_dict`,
  minus each type's `COPY_SKIP`), the center, its ports and a `uid`. Ctrl+Z undoes it (the
  parts come back with their wires; `Scene.push_undo` batches with `added` / `links`). Only
  copyable, non-`SINGLETON` objects go in — trains are left out (a note says which). A taken name
  gets " (2)". The E menu's **Machines** tab (always shown; empty, it says how to make one) has a
  tile per machine — its icon is its skin (`draw_machine_preview`) — and right-click on one renames
  / deletes it (`App.machine_menu`, back to the E menu after). Picking one places a fresh Machine
  (`Machine.from_definition`: every part gets a new id — `_remap_ids` swaps every old-id string, in
  the parts and in the port specs). A machine with lab hardware inside (a device, or a machine
  holding one) is placed only once and can't be copied (`Machine.can_place`, `copyable`).
  **Older machines** (saved before ports, no `ports_in` key — `is_packaged`) still place the old
  way: a `MachineGroup` in hand drops all their objects as ordinary objects (`build_machine`;
  links to objects *outside* are kept and reconnect if that object exists)
- **Remembers your view**: on close, the world point at screen center + zoom are written to
  `saves/player.json` (git-ignored — it's the player's, not the world's); on launch the camera is
  restored there. (It used to be `"view"` in `Nodes.json`; the first launch moves it over)

- **Text boxes** have a movable cursor: Left/Right, Home/End, Backspace/Delete,
  Ctrl+Backspace (word); long text scrolls sideways. Key-repeat (`TEXT_KEY_REPEAT`) is
  switched on only while a text box is open, so held keys don't repeat on the canvas
- **Copy** (Edit menu) for buttons/name tags/walls; **C** key drops a copy of the selection
  centered at the mouse cursor (works for any node type + walls); clipboard paste (**q** or Ctrl+V) in text fields

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

## The panel

Every panel has the same parts, always in this order (a section with nothing in it is left out):

| Part | Holds | Comes from |
|---|---|---|
| **Header** | type name; the object's name if it has one (click to rename); a status line (device status, an equation's problem, a relay loop); the **⋯** menu | `panel_name()`, `rename(app)`, `panel_status(app)` |
| **State** | what it is / shows now, and the main thing you edit (equation, counter value, slot names) | `state_rows(app)` + Props with `state=True` |
| **Inputs / takes** | every input: its name, then **link** (and the value set by hand, if any) or *source · output* in green with an x to unlink, and the live value under it | `takes()` (+ `input_rows(app)`, e.g. *+ add column*) |
| **Outputs / gives** | a plain list of what it gives, with live values; click one to link it into another object's input (its name runs `on_label`, if the Give has one) | `gives()` / `outputs()` (+ `output_rows(app)`, e.g. *+ add output*) |
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

## Inputs and outputs — takes / gives

**An output (Give) is a named value an object offers. An input (Take) is a named slot that
follows one output.** Every wire runs from an output to an input; an output can feed any
number of inputs, an input has at most one source. Every value is just a number; on/off is 0
or 1 (anything that isn't 0 counts as on). Text stays display-only. The one exception is
**data**: a `Give(kind="data")` offers a dict (with a `"label"` for the panel) that only a
`Take(kind="data")` takes — a Script's `runs` into a Graph.

### The port rules — how inputs and outputs look and connect

The Machine set these; every object with inputs or outputs should follow them, so a wire can be
read at a glance (which output feeds which input) and linked by hand without menus.

1. **Inputs on the left.** One chip per input down the left edge, labeled with its name —
   hollow until linked, filled once linked, brighter under the mouse. A wire ends at its chip
   (`input_point(port)`), and a wire dropped on a chip links that input (`take_at`).
2. **Outputs on the right.** One screen per output showing its live value (its name small in
   the corner, the value big), each with **its own dot on the right edge**, level with its
   screen — lit while it has a value. An object with one output may use a single dot
   (`output_point()`); with more, `output_point_of(key)` gives each output's dot.
3. **A wire runs from its output's dot to its input's chip.** Never from an object's center,
   and never two outputs from one point — so the wire itself says what it carries.
4. **The dot is the handle.** Drag from a dot to lay that output's wire (`give_at` returns the
   output under the mouse); while linking an input, click a dot to take that output with no menu
   (the rubber band snaps to it and names it). Clicking the body still opens the menu.
5. **Inside a machine, input objects become inputs.** A switch, push button or slider can't be
   clicked in there, so it becomes an input chip that stands in for it ("Switch 1", …). When
   packaging, the left side gives the inputs and the right side the outputs.
6. **Kinds show on the wire, not the port:** number = green flowing dots, on/off = amber while on,
   press = amber flash, data = thick blue (see *Wires* below).

Built for it: the Machine (`things.py`) is the reference — chips, a screen and dot per output,
per-output picking and dragging. **Exceptions by design:** the Data relay is a junction (a wire
in and out at its center, fanned out); logic gates draw schematic pins instead of chips and
screens, but keep inputs left and the output right.

**Who follows the rules** (as of the Timer's two-output rework):

| Object | Inputs | Outputs | Status |
|---|---|---|---|
| Machine, Timer | chips, left | a screen + dot each | follows |
| Function, Counter, Sequence | chips, left | one output: screen + dot | follows |
| AND gate | pins, left | pin, right | follows (schematic look) |
| Data tag | hole, left ✓; *streaming* input is the icon top-right ✗ | dot, right | mostly |
| Data collector | *collect* chip left ✓; columns on top of their headers ✗ | `rows` — no dot, center ✗ | partly |
| Readout | `in` at its center ✗ | `value` — no dot, center ✗ | no |
| Graph | *Runs from* at its center ✗ | — | no |
| Time series | `in`, a dot on the left | — | follows |
| Push button, Switch, Slider, Lamp | — | one output, no dot, center ✗ | no |
| Neuron | — | 3 outputs, center ✗ | no |
| Script | RUN / STOP on the button, field chips beside it ✗ | 4 outputs (one data), center ✗ | no |
| PDA45 | `gate`, at its center | a dot each for `count_rate` / `voltage`, right, level with its screen (no labels: the screens are the labels) | its own look, on purpose |
| PM100D | `wavelength`, at its center | one dot (`power`), right, level with the LCD | its own look, on purpose |
| Flip mirror, Filter wheel, Zaber | one dot (`up` / `position` / `target`), left — a ring until linked | each reading + setting, center | its own look, on purpose |
| Excitation filters | `center` / `width`, at its center | one dot (`actual_center`), right, level with the strip | its own look, on purpose |
| Every other device (servo, SuperK, spectrometer) | each setting, at its center | each reading + setting, center | its own look, on purpose |

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
  | `data` | takes a data output (a dict), drawn as a thick blue wire, flowing while the data's `live` | Graph *Runs from* (a Script's `runs`) |

  Any input can link to any output — except across data: `link()` ignores a data output into a
  number / on/off / press input or the reverse (`kinds_match`), and the pickers only offer
  matching ends. `format_value` shows a data value by its label.
- **One storage shape:** every object's links are one hidden Prop, `links = {port: {"id", "key"}}`
  (saved only when not empty). Older saves are converted on load by each type's `migrate(d)`:
  a readout's `source_id` / `source_key`, a relay's `source`, a tag's `stream`, and a
  collector's `trigger` and per-column `id` / `key`. Machines remap ids inside it like any
  other saved data.
- **Helpers (base class):** `take(port)` (the value, or None), `take_source(port)`,
  `take_on(port)`, `link(port, src, key)`, `unlink(port)`, `unlink_all()`, `pick(app, port)`.
  The scene calls `watch_presses(dt)` every frame, which fires `on_press` — so no type keeps
  its own edge-tracking.
- **Linking, one way everywhere — a wire in hand** (`canvas.WireMode`, `App.hold_wire`): pick
  up a wire from any port — press an output's dot (`give_at`), or click an input's chip / dot
  (its `on_click` → `pick` → `App.pick_link`), or *link* on an input's row, or an output's row in
  *Outputs / gives* (`App.pick_target`). **Drag** it and it links where you let go (over nothing,
  it stays in hand); **click** (no drag, `WIRE_DRAG_SLOP`) and it stays in hand until the next
  click: on the other end — an input while holding an output, an output's dot while holding an
  input — or an object with one (a menu of its inputs / outputs, with live values) it links and
  lets go; **Shift+click** keeps an output's wire in hand to feed more inputs. On **empty canvas**
  it places a Data relay, wired in, and carries on from it (from an output the relay is fed by
  the wire; from an input it feeds the input), so a path is laid hop by hop. A click on the
  *same* end (an output holding an output, …) does nothing and the hint says why — an input only
  ever takes an output, so the two ends can't be crossed; holding an input, a click on its own
  object unlinks it. **Right-click** (or Esc) lets go; relays placed stay. While held, the wire's
  dots flow the way the value will, its free end is a dot (an output's) or a ring (an input's),
  it snaps to a valid port under the mouse, and the hint names what's held — or "Replaces …" over
  an input that's already linked. **Hover:** a ring shows on the port under the mouse
  (`draw_port_hover`); `PORT_REACH` (12 px) around a dot counts as on it, and an input's dot
  just off its object's edge is found by `Scene.take_at`. In a world the window holds the wire:
  the world's `pick_link` / `pick_target` send `ui` *wire*, links go as `choose_take`, relays as
  `add`, an unlink as `unlink`. Click the output's name on a linked row to choose a different one.
- **Wires** are drawn by the base class from `links`, from the source's output point to the
  reader's `input_point(port)` — `canvas.output_point(obj, key)`: the output's own nub when the
  source has one per output (`output_point_of(key)`, a Machine), else its `output_point()`, else
  its center: number = green flowing dots; on/off = amber, flowing while on;
  press = dim amber that flashes when it fires.
- **Devices:** every param is an input (set by hand while unlinked, or linked); number
  readings and param settings are outputs. Their names are saved (`catalog`), so an offline
  device can still be wired.
- **Out of scope:** neuron W's (an all-to-all weight matrix, shown as their own section) and
  text values.

**Dialogs are callbacks.** `app.ask(prompt, on_submit)` opens a text box and calls
`on_submit(text)` on Enter; `app.confirm(message, sub, on_confirm)` is the confirm dialog.
That replaced a `purpose`-string switch in the loop that every new prompt had to extend.

---

## Level of detail

Instead of swapping a `Button` object for a `Rectangle` when zoomed out, each type just
*draws a simpler version of itself when tiny*: a button becomes a plain dot below a zoom
threshold; a name tag stays fixed screen-size; a train collapses to a thin line. This
deleted the whole swap/sync machinery (`Rectangle`, `shrink_to_rectangle`,
`sync_button_scale`, and the "remember to call sync after every zoom" bug class).

---

## Object ids

Every object has a short stable id (`n_xxxxxxxx`), assigned once and saved. Trains reference
two of them; `goto` targets one; future grouping/undo will need them. Old data without ids
gets them backfilled on first load (which also migrates the legacy `{"nodes": [...]}` shape
to `{"objects": [...]}`).

---

## The world and the player's view

Step 1 toward multiplayer (docs/MULTIPLAYER_STUDY.md): the world and what one person sees of
it are now two things, still in one program.

- **`Scene` is the world:** the objects, `by_id`, add / delete, `tick(dt)`, saving
  `Nodes.json`. It has no camera and no selection list, so it can run with nobody looking
  — checked: every object type ticks with no window and no `pygame.init()`.
- **`Player` is one view of it:** its own `Camera`, selection (ids), `hover()`, `draw()`
  (and the neuron mesh's cached drawing), and the view it remembers in `player.json`.
  `App` gets the player (`app.player`, `app.camera`), and the world through it (`app.scene`).
  Each player window will have its own Player.
- **The rule:** an object's `tick` (world) never reads the mouse, the camera, or
  `is_selected` / `is_hovered` — those are one view's drawing hints, never saved. `hover` /
  `draw` (view) may read anything but change only view state. `Scene.update(mouse, camera,
  dt)` became `scene.tick(dt)` + `player.hover(mouse, pressed)`.
- **When the world does care about players**, it's told explicitly: `scene.selections`
  (player name → ids) — an agent holds still while *anyone* has it selected
  (`scene.selected_by_anyone`), not when "this window" does.
- **Push button `pressed` is world state now:** held (from the click until release, or
  until the mouse slides off it) or just tapped — it no longer checks hover, which made the
  output depend on one window's mouse. Sliding off lets go, and sliding back on doesn't
  press again (it used to show pressed again, which a Counter would count a second time).
- `scene.hit_test` / `give_at` stay on the Scene: they take the camera as an argument and
  keep no view state — a view asking the world what's under a point.
- A `Button` measures its text with a font when it's made, so the world server calls
  `pygame.font.init()` (no window needed).

---

## The world server and players

Step 2 (docs/MULTIPLAYER_STUDY.md): the world runs as its own program, and canvas windows join it
— from this PC or from other computers on the same network.

**The name:** the app is **Canvas Lab** (`canvas.APP_NAME`: window titles, the home screen;
the world window is "Canvas Lab — World"). The Start-menu shortcut made by make_shortcut.ps1 is
still called "Canvas App" (same AppUserModelID, so a pinned icon keeps working).

**The taskbar pin's icon** goes blank whenever the pinned .lnk is rewritten: the taskbar caches
the pin's image and doesn't reload it. So make_shortcut.ps1 (safe to re-run after every update)
leaves the pin alone unless what it runs, its icon or its AppUserModelID changed — compared by
those fields, not the .lnk's bytes, which hold ever-changing timestamps. When it does change the
pin, it restarts Explorer to redraw the taskbar. The local icon copy is named by its content
(`taskbar-<hash>.ico`) since Explorer caches icons by path, and lives in
`%LOCALAPPDATA%\Programs\CanvasApp` (the per-user install folder): from `%LOCALAPPDATA%\CanvasApp`
the Start menu and taskbar drew it blank even for a brand-new shortcut, with the same file and
permissions, while File Explorer drew it fine. Explorer restarts, clearing the icon cache and
re-pinning didn't help; moving the folder did. `taskbar.ico` keeps PNG only for 256 px, with
classic bitmaps for the smaller sizes.

**The home screen's look** stays plain pygame shapes, like the rest of the app: the name, a
status card, the buttons and the name row — tried a drifting backdrop and a live logo, but
plain fits the app better. The window opens at 1180×740 (or 85% / 80% of a smaller screen).

**Using it.** The taskbar canvas opens to a **home screen** (`canvas.HomeScreen`): the world's
status (running or not, on which PC, who's in it, how many objects), then
- **Join world** (green, Enter) when it's running — here or on another PC;
- **Launch world** when it isn't: starts `world_server.py` (pythonw, no console), waits up to
  20 s for it, then joins by itself;
- **Work on my own**: the canvas as it always was — loads, ticks and saves Nodes.json itself.
  Only while no world is running, so two programs never write the file;
- **Playing as** *name* (click *change*): what other players see; saved in player.json;
- **World on another PC?** *enter its address*: a PC name or IP, for a world the home screen
  doesn't find by itself (see *Other computers* below).

**The world app** (`world_server.py`, also runnable by hand) has a small window and its own
taskbar icon (AppUserModelID `Yaakov.CanvasWorld`): *The world is running*, the players in it,
object count, uptime, and **Stop world**. **Closing a player window never stops the world;
closing the world's window (or Stop world) does** — it asks first if anyone's in it, saves, and
sends players back to their home screen ("The world was stopped."). It refuses to start while
a window on this PC is working on its own, and if a world is already running (here, or on the
PC saves/world.json points to). Its window says how others can join (this PC's name and address).

**In the world, everything works as on your own** — pressing buttons, flipping switches,
dragging sliders, typing equations, linking (panel *link*, chips, dragging a wire out of a
nub), the E / Add menus and placing, C copies, Delete, arrow-key moves, right-click and ⋯
menus, panel rows and buttons, trains and walls, machines — and everyone in the world sees
the result. A list at the top-left (`draw_world_roster`, under a mode's hint while one shows)
says *Connected to world* and lists everyone in it — a dot in their color and their name, yours
first and marked *you*; the window title has your name.

**Seeing the other players** (`canvas.OtherPlayers`). Each player gets a **color** when they
join (`PLAYER_COLORS` in world_server.py, the first one nobody in the world has). In that
color, every window draws each *other* player's
- **view**: an outlined box (no fill) around the area their window shows, tagged "*name*'s view"
  at its top-left corner — only when that corner is on screen (no tag when their view is bigger
  than yours: the player list says who's here);
- **cursor**: an arrow with a name tag, and a ring around its tip while their mouse button is
  down — so you see them click, drag a slider, press a button;
- **selection**: a ring around each object they have selected (what they're working on).

Each window reports where it is (`{"op": "presence"}`: cursor in world units — None while the
mouse is outside the window — the area it shows `[x, y, w, h]`, and whether the button is
down), at most every `PRESENCE_SEND_S` (0.05 s) and only when it changed. The world passes
everyone's on (`{"op": "presence", "players": [...]}`, 20×/s when something moved, with their
selection from `scene.selections`). Drawn positions glide toward the newest ones
(`PRESENCE_SMOOTH`), so cursors move smoothly between updates. Name tags use dark text on
light colors.

**How your actions reach the world.** Object code runs in one place only: the world. The
game loop is the same in both modes because it does every change through an `App` method —
`click` / `drag` / `release`, `panel_click`, `object_menu` / `selection_menu`, `menu_command`
(was `handle_menu_command` in game_loop.py), `move_objects` / `moved`, `add`, `place`,
`choose_take`. On your own, `App` does them. In the world, `JoinedApp` sends each to the
world with this window's **view** (camera, screen size, mouse, selection), and there that
player's **`WorldApp`** (world_server.py) runs the very same object code — `obj.on_click(pos,
app)`, the panel callback, `cls.create(app)` — with *that player's* camera and mouse
(`pygame.mouse.get_pos` is pointed at it while it runs). Whatever object code would show is
sent back as a `ui` request and the answer comes back with its token:
- `app.ask` / `app.confirm` / `app.context_menu` → the text box / confirm / menu opens in the
  player's window; Enter / OK / the item chosen goes back, and the callback runs in the world.
- `app.start_picking` (linking: "click what X takes from") → the window's pick mode, with the
  ids the world's `accept` allows; the clicked object (or empty-canvas point) goes back.
- `app.start_placing(factory)` → the world makes a sample, the window holds a see-through copy
  (`GhostGroup`); the drop point goes back and the world makes the real one with the factory
  (Shift+click keeps placing; a one-only type ends it with `place_done`).
- `app.open_inventory`, the train / wall builders (`RemoteMode`) → start in the window; a
  finished train / wall comes back as `add`.
- If object code moved the player's camera (Go To, a train ride) or changed their selection
  (a placed object is selected), the world tells the window (`camera` / `select`).
- **Panel clicks** can't send the callback itself, so each clickable spot is drawn with a
  **path** (`InspectorPanel.hot_at` → `(callback, view, path)`): `("button", i, label)`,
  `("row", n, label[, part])` — n counts every row, sections' rows included, folded or not
  (`flat_rows`), part is `pick` / `unlink` / `choose_give` / `set_by_hand` / `on_label` /
  `down` / `up` / `item k` — or `("menu", pos)`, `("rename",)`. The world rebuilds the panel for
  that object and finds the same spot (`panel_callback`, via `row_callback`, which the panel
  draws with too), checking the label so a click is never run on a different row. Folding a
  section (`view=True`) stays in the window.
- A **click** asks the world and waits for its answer (`request` → `reply`): did the object
  take it (then drags / release follow) or should it just select? Same PC: a few ms; over a
  LAN, a few more.

**Other computers** (same network). The world listens on every address of its PC
(`world_link.LISTEN_HOST`, `0.0.0.0`; port 47810), with no password — it's for people you work
with. How a window finds it (`world_link.find_world`, every half second on the home screen):
1. this PC (127.0.0.1);
2. **saves/world.json**: the world writes it when it starts (`announce_world`: PC name,
   addresses, start time) and removes it when it stops. Computers running the app from this
   shared Drive folder all see it, so they find the world without typing anything. If it names
   a PC that doesn't answer (crashed, or blocked), the home screen says so in amber;
3. the address typed under *World on another PC?* (kept per PC in player.json), for a PC that
   doesn't share the folder.

- **Same code or no entry:** the version a window sends in `hello` is a hash of the code files
  (`world_link.CODE_FILES`), so a window whose files differ from the world's — another PC whose
  Drive copy hasn't synced an edit yet, or a window opened before one — is refused with a message,
  instead of half-working. After editing code, restart the world and the windows.
- **Clocks:** objects keep moments as `time.monotonic()` values set in the world, and every PC's
  clock starts somewhere else. The world sends its clock with the welcome and every change, and
  object code that compares a stored moment with "now" where players draw it uses
  `world_link.world_time()` — this PC's clock shifted to the world's (0 shift in the world and on
  your own). Today that's the SuperK's "STARTING…" timer.
- **Shared folder:** saves/ is shared too when PCs run from the Drive folder, so player.json keeps
  each PC's name and typed address under its PC name (`{"names": {pc: name}, "join": {pc: address},
  "views": {name: view}}`).
- **Windows Firewall** on the world's PC must let `pythonw.exe` (Launch world) or `python.exe`
  (by hand) accept connections on the network type it's on (Private / Public); Windows asks the
  first time. A guest Wi-Fi often keeps devices from reaching each other at all.

**How it works** (`world_link.py`, protocol in its docstring; JSON lines over TCP, port 47810):
- The world thread (`World.run`) ticks the Scene 60×/s and, 30×/s, sends what changed. Network
  threads only queue messages; only the world thread touches the scene.
- **Mirroring is generic** (`Mirror`): an object's state is its instance attributes —
  everything in `vars(obj)` that can be JSON (numbers, text, lists, str-keyed dicts, vectors
  `{"__v2"}`, tuples `{"__t"}`, sets `{"__s"}`), skipping what can't (fonts, surfaces, compiled
  equations) and the view's own flags (`VIEW_ATTRS`: is_hovered / is_clicked / is_selected,
  scene). On join a player gets every object (`to_dict` + state, in the welcome); after that
  only changed attributes, and added / removed objects. So no object type has networking code,
  and new types are mirrored for free. A player's copy (`Scene.copy_of_world`, `path=None`)
  never saves and is never ticked — it only draws what the world sends, so windows can't drift
  apart (agents, neurons, counters move only in the world).
- **Selections go to the world** (`{"op": "select"}` when yours changes, and with every action)
  into `scene.selections[name]` — so an agent holds still while anyone in the world has it
  selected, and Delete / Copy act on what you have selected right now.
- **Devices:** only the world talks to the instrument server. Players see the devices through
  it: the world sends `instruments.client.snapshot()` (10×/s, when it changed) and the player's
  `instruments.client.mirror()`s it — never started, so it never connects anywhere.
- **Names:** a second window with the same name gets "Yaakov 2". Views are kept per name in
  player.json (`"views": {name: view}`), so two windows each reopen where they were.
- **Laser safety:** the world keeps running when everyone leaves, so the instrument server's
  "no clients" rule (emission off when the canvas goes) would no longer fire. Instead, when the
  last player leaves the world calls `on_no_players()` on every object; the SuperK turns
  emission off (unless its device's `off_when_canvas_closes` option is off). Stopping the world
  still disconnects it from the instrument server, so that rule fires too.
- **"Work on my own" lock:** that window holds 127.0.0.1:47811 (a bound port is let go even
  on a crash); the world won't start while it's held, and the home screen greys out Launch. It's
  per PC: a world started on another PC doesn't see it (both would then write Nodes.json).
- **Tests** point everything elsewhere with `CANVAS_WORLD_PORT`, `CANVAS_NODES_FILE`,
  `CANVAS_PLAYER_FILE`, `CANVAS_WORLD_FILE` (and `CANVAS_INSTRUMENT_PORT`), so they never touch
  the real world; `CANVAS_WORLD_LISTEN=127.0.0.1` keeps a test world off the network.
- **Cost:** 500 objects ≈ 15 ms per send (tick + compare), ~8 KB a message — fine at today's
  sizes. If worlds grow: send less often, or only what's near each player's view.
- **Known rough edges:** movement is updated 30×/s, so agents step slightly (smoothing
  between updates could fix it); a Switch's knob slide is mirrored, not animated locally;
  a collector's scroll position is
  shared when it's moved with the scrollbar (that runs in the world), not with the wheel
  (that's the window's own). Launchers' commands run on the PC the world runs on. Each action
  waits for the next world tick (≤ 16 ms) and its result shows with the next send (≤ 33 ms).
- **World icon:** the world window uses `world.ico` (or `world.png`) from `assets/`
  if there is one, else the canvas's icon.ico.

---

## Devices and the instrument server

Real hardware runs outside the canvas. Each device type's canvas object is described in
[OBJECTS.md](OBJECTS.md) (*Lab devices*).

- `instrument_server.py` (launched by the Instrument server object, or by hand) opens
  every device in `devices.json` through its driver in `drivers/`, keeps the connections
  open (so restarting the canvas never resets an Arduino, and devices **hold their last
  value** when the canvas closes), and serves JSON-lines over a socket. One worker thread per
  device applies the *newest* requested value per param, then polls `read()` at the driver's
  `poll_hz`; a failing driver shows "error" (with when it'll retry) and is retried (module
  re-imported) after 3 s, then 6, 12 … up to 60 s; the full traceback is logged only when the
  error changes. A `retry` request (sent by a Device's **Connect**) tries at once.
- **Startup order:** the server listens first, then opens devices **one at a time** (an open
  lock — a port-scanning driver must never overlap another device's open), drivers marked
  `opens_first` (the SuperK's scan) before the rest.
- **One device per piece of hardware — the driver decides.** A driver runs one device
  (`one_device`, the default), or one per value of its `device_key` option (`flip_mirror`:
  `"mirror"` — detection, excitation; `zaber_stage`: `"stage"` — the chain index). Zaber
  stages are also claimed across drivers (`zaber_stage.claim_stage`): the filter band owns its
  longpass / shortpass stages, so a Zaber stage device on one of them fails, saying why. A devices.json entry that would be a second one is
  skipped (logged), and `add_device` / `configure_device` refuse to make one.
- **Local only, by design:** it listens on `127.0.0.1:47800` — the devices are plugged into
  this PC, and only programs on this PC can reach the server.
- Drivers (`drivers/base.py`): `params` (things you set: `Param(min, max, unit, default)`,
  clamped by the server before `set_<name>` is called), `readings` (things you read:
  `Reading(unit)`; `kind="text"` for text, `kind="data"` for bulk data such as a spectrum — drawn
  by its own object, never a text row or a linkable output), `open` / `read` / `close`, and
  `one_device` / `device_key` / `opens_first`.
  `drivers/fake_arduino.py` simulates a servo
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
- **Instrument server object** (E menu ▸ Devices; place as many as you like, or copy one with
  **C** — each is a view of the one link and shows its state; **Connect** on one takes over
  from the one that had it, *Disconnect all* / *Stop server* turn every one off): a radio with an antenna (signal waves while connected, blinking amber while
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
  (`DRIVER` set) **never makes a device of its own**: every object of it is a view of the
  one device the server runs for its driver (`find_device`, matched by driver — and by
  `is_my_device` for a keyed driver: a Flip mirror's `mirror`, a Zaber stage's `stage`), so placing or copying more
  objects adds views, not devices. Only one object per device is connected at a time:
  **Connect** on one takes control (`take_control` — the others just show "not connected";
  the device keeps running, nothing is switched off, and the new one adopts the running
  device's options). A copy starts disconnected (`COPY_SKIP`). The connected object owns
  the device's options (`device_options()` — e.g. the servo's Port / Baud props) and asks
  the server to `add_device` (under `DEFAULT_DEVICE`, if the server has none for the driver
  yet — only once the catalog is in, `client.catalog_in`) / `configure_device` until the
  server's copy matches. Status: off →
  connecting → starting → connected (or error, with the driver's message). The generic Device
  is no longer in the E menu (placed from the Instrument server's device list). The Instrument
  server object is optional; its *Disconnect all* / *Stop server* turn every device off.
  devices.json starts empty — the fake Arduino device was removed (its driver stays, for tests).

- **Confirm dialogs** take an `ok_label` ("Delete" by default) and grow to fit their text.
- **Buttons:** `Btn(style="stop")` is red.
- **Server robustness:** the server's console replaces characters it can't print (λ, µ …)
  instead of raising, and `log()` never raises — a print inside a driver's error used to kill
  that device's worker thread, leaving it stuck at "starting".

- **Driver actions** (general): a driver's `actions = ["home", …]` become buttons on its Device
  (`{"op": "action"}`). Errors from `set_<param>` / actions show as the device's message without
  restarting it. A Device's outputs list readings first, so a Readout links to e.g. `position`.
- pyserial is required (`pip install pyserial`); the unrelated `serial` package (a JSON/YAML
  library, same import name) was removed from this PC's miniconda for that reason.
- **Experiment scripts and the canvas** (`scripts/canvas_run.py`): a script keeps its argparse
  parser and stays a terminal script, but hands the parser to `canvas_run.start()` (instead of
  `parse_args()`) and reports as it goes — `dataset(csv)`, `progress(i, n, stage)`,
  `check_stop()` between points (raises `Stopped`, so the script's own cleanup runs: files
  saved, laser off). `--canvas-describe` prints its inputs (from the parser) as JSON and exits;
  `--canvas-run STATUS.json` makes it write state / progress / dataset there and treat
  `STATUS.json.stop` as Stop. From a terminal it all does nothing. Each script's runs go to
  `experiments/<name>/`, one CSV per run — a Graph's datasets. `start(show=[…], units={…})` names the
  inputs the Script object draws on itself (linkable / typed there) and the units shown beside values.
  `detector_spectral_response.py` (was `detector_power_sweep.py`; saves follow the rename through
  `SCRIPT_RENAMES`, and it still writes to `experiments/detector_sweep/`) is the first (it shows its
  wavelength on the object, declares x = power, y = count rate or voltage, and labels each run with its wavelength and --name; both go to the folder's `canvas.json` for Graphs). The **Script** object (OBJECTS.md, *Data*) runs
  them from the canvas: number inputs linkable, inputs as panel rows, RUN / STOP, progress; its `runs` data output links
  to Graphs (the latest run, followed live while it runs).

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

---

## Scripts

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
