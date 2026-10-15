# Inspector panel + Takes / Gives — design plan

> A proposal, not built yet. Two linked questions: (1) one standard layout for the
> inspector panel, and (2) one model for wiring values between objects. They're
> one plan because most of today's panel mess *is* linking UI, done differently by
> each type. Once decided, the outcome moves into DESIGN.md.

---

## Where things stand

### The panel

`InspectorPanel` draws whatever `obj.inspector(app)` returns, with `obj.buttons(app)`
stacked at the bottom. That's a good seam, but each type builds its own list, so
nothing lines up between types:

| | What varies today |
|---|---|
| **Name** | First row on some (Counter, Tag, Flip mirror). Called "Data set" on the collector. Missing on Function and Device. Name + ID + Position on plain Things. |
| **Props** (Size, Color…) | At the end on most, in the middle on Readout (Size comes before Source). |
| **Links** | "Source / Reads", "follows / reads", "records / reads", "Passes on / reads", "Streaming / reads", "Collect input". Five different labels for the same idea. |
| **Unlinking** | Click the object itself while picking (Function, Counter, Collector, Relay), an *Unlink* button (Readout, Relay), or *Unlink all* (Function, Counter, Device). On a Device you can't unlink *one* param, only all of them. |
| **Choosing which value** | You click "reads" and it cycles through the source's values one by one. You can't see the options. |
| **Buttons** | A mix of main actions (Collect, Activate, + step), link management (Link to source…, Unlink all), editing (Edit equation…, Add column…, Set command…), connection (Connect / Disconnect), and destructive actions (Clear rows…, Stop server). No fixed order: Filter wheel puts *Next slot* first, Device puts *Connect* last. |
| **Overflow** | The panel doesn't scroll. A collector with many columns or a device with many params loses its bottom rows behind the buttons. |
| **Sub-rows** | Shown by indenting with spaces (`"   reads"`). |

### Linking (today's 7 styles)

Every link is really the same thing, `{"id": source, "key": which output}`, stored seven ways:

| Object | Stored as | Behavior |
|---|---|---|
| Readout (`Output`) | `source_id` + `source_key` (two str props) | follows the value |
| Function | `links[var]` | follows; chips on the left |
| Counter | `links[up/down/reset]` | acts each time it turns on; prefers `pressed` → `on` → `value` |
| Data collector | `columns[i].id/key`, `trigger` | columns read when a row is collected; `trigger` collects each time it turns on |
| Data relay | `source` | follows, no delay through chains, loop guard |
| Data tag | `source`, `stream` | follows; `stream` passes values while on, holds while off |
| Device | `links[param]` | follows, pushed to the server |

Gives are already uniform: `outputs()` returns `{name: number}`. The rough edges:

- A Device offline returns `{}`, so you can't link to it until it's connected.
- A Readout with no reading returns `{}`. A Function returns `{"y": None}`. A relay
  always offers `value`. That's three different "no value" conventions.
- When a device has a reading and a param with the same name, only one survives
  (`setdefault`).
- Edge detection (`_last_high`) is written separately in Counter and Collector.
- `links_to()` has to scan every data prop to find readers, because links have no common shape.

---

## Part 1: Takes and Gives

**A Give is a named value an object offers. A Take is a named slot that follows one Give.**
Every wire runs from a Give to a Take. A Give can feed any number of Takes. A Take has
at most one source.

### Gives

```python
def gives(self):            # default: one Give per outputs() key
    return [Give("value", unit="mm")]
```

- `outputs()` stays the way values are read. `gives()` *declares* them (name, unit, label),
  so the panel and the picker can list them **even while the value is None**.
- Every Give is always listed. No value = `None`, never a missing key. (This fixes the
  offline Device: it lists the gives from its last known catalog.)
- A Device's gives are named `position` (reading) and `position (set)` (param), so a
  shared name no longer hides one of them.
- Every value is just a number. On/off is **0 or 1**: a push button's `pressed`, a
  switch's `on` and a device's `moving` are 0 or 1 like any other number. Text stays
  display-only.

### Takes

```python
TAKES = [Take("up", kind="press"), Take("down", kind="press"), Take("reset", kind="press")]

def takes(self):            # override when the list is dynamic (function variables, columns, device params)
    return self.TAKES
```

Each Take has a **kind**, which is the only real difference between today's link styles:

| Take kind | What it does | Today's users |
|---|---|---|
| `number` | follows the value | Readout, Function var, Device param, Relay, Tag value, Collector columns |
| `onoff` | 1 = on, 0 = off | Tag *stream* |
| `press` | acts once each time it goes 0 → 1 | Counter up / down / reset, Collector *collect* |

- Any input can link to any output. Wiring a slider into an on/off input is allowed;
  you just won't usually do it.
- Collector columns are `number` takes that are read only when a row is collected. The
  panel says "records" instead of "follows" so that's clear.
- A `press` that's already 1 when the canvas loads doesn't count. Only a real 0 → 1 does.

### One storage shape

All links live in one hidden data prop on the base class:

```python
self.links = {"up": {"id": "n_ab12cd34", "key": "pressed"}, ...}
```

- Port keys: Readout / Relay / Tag `in`, Tag `stream`, Function `<variable>`, Counter
  `up/down/reset`, Collector `col<cid>` + `collect`, Device `<param>`.
- Saved only when not empty.
- **Migration** in `load_props`: `source_id/source_key`, `source`, `stream`, `trigger`,
  and the `id/key` inside collector columns move into `links`. Old saves load unchanged.
- `links_to()` becomes "is `target_id` anywhere in `obj.links`". Machine id remapping
  (`_remap_ids`) keeps working without changes.

### One set of helpers (base class, written once)

| Helper | Does |
|---|---|
| `self.take(port)` | the source's value, or None (unlinked / deleted / no value) |
| `self.take_source(port)` | the source object, or None |
| `link(port, src, key)` / `unlink(port)` | set or clear one Take, then save |
| `pick(app, port)` | the one linking interaction (below) |
| `on_press(port, app)` | called by the framework each time a `press` take goes 0 → 1 |
| `take_on(port)` | is an `onoff` take on (True / False, None if unlinked) |

Watching for off → on (including "already on when loaded doesn't count") moves into the
framework, so Counter and Collector lose their copies.

### One linking interaction, everywhere

1. **Start:** in the panel's *Inputs / takes* section, click **link** on the input's row.
   That's the main way. Clicking an input's chip on the canvas (Function, Counter,
   Collector) is a shortcut that does the same thing.
2. **Pick a source:** the same PickMode as today (rubber band, green ring). Only objects
   with gives are accepted.
3. **Pick the give:** if the source has one give, it's linked right away. With several,
   a small menu opens **at the source** listing each give with its live value. This
   replaces cycling through "reads".
4. **Unlink:** the ✕ on the Take's row, or right-click the chip ▸ Unlink. (Clicking the
   object itself while picking also keeps working.)
5. **Change which give:** click the give name on the row to reopen the menu from step 3.

Later, the reverse direction: drag from a Give's nub onto a Take's chip.

### Wires

The framework draws every wire from `links`, so types stop drawing their own:
`output_point(key)` on the source to `input_point(port)` on the reader. These hooks
exist today without a port argument. The style shows the kind:

- number: today's green flowing dots
- on / off: lit at 1, dim at 0
- press: dim, with a flash each time it goes 0 → 1

### Out of scope

- **Neuron W's.** They're an all-to-all weight matrix, not wires. They stay as they are
  and show up as their own panel section.
- **Text values.** Device status strings etc. stay display-only for now.

---

## Part 2: The standard panel

Every panel has the same sections, always in this order. A section with nothing in it is
left out.

```
┌─────────────────────────────┐
│ FUNCTION                ⋯   │  header: type (small), ⋯ = object menu
│ Beam power fit              │          name (large, click to rename)
│ ● ok                        │          status line: only if the type has one
├─────────────────────────────┤
│ STATE                       │  what it is / shows right now
│   y = 2P + 0.5     (click)  │    the main editable thing, if there is one
│   y            3.214        │    live values
├─────────────────────────────┤
│ INPUTS / TAKES              │  one row per Take (every settable thing)
│   P   ← Slider 2 · value  ✕ │    port, source · give, unlink
│        1.357                │    live value under it
│   Q   — link                │    unlinked: click to link
├─────────────────────────────┤
│ OUTPUTS / GIVES             │  one row per Give (a plain list)
│   y   3.214     → 2 readers │    value + how many read it (click: list them)
├─────────────────────────────┤
│ SETTINGS               ▾    │  Props: Size, Color, Unit, Decimals…
├─────────────────────────────┤
│ INFO                   ▸    │  collapsed: ID, position, driver, notes
├─────────────────────────────┤
│ [  Primary action  ]        │  footer buttons
│ [  Secondary       ]        │
└─────────────────────────────┘
```

### Section rules

| Section | Contains | Never contains |
|---|---|---|
| **Header** | type name, object name (only types that have one, e.g. Counter, Tag, Flip mirror, Data collector; click to rename), status line (devices, server, errors / problems in red) | values |
| **State** | the object's own values and the main thing you edit (equation, counter value, slot names, device params when unlinked) | links |
| **Inputs / takes** | every settable thing (device params included): name, then **link** (unlinked) or source · give + ✕ (linked), live value; `+ add column` as the last row where that applies | settings |
| **Outputs / gives** | a plain list of what it gives: name, live value, unit | — |
| **Settings** | Props: how it looks and behaves (Size, Color, Unit, Rows shown…) | live values |
| **Info** | ID, position, driver, port, file paths, notes; collapsed by default | anything you need often |

- A **device param** is listed under Takes either way: "set by hand · 45 mm" (click to
  type) when unlinked, "← Slider · value" when linked. Its readings are Gives. (This is
  decision 1 below.)
- Sections can be collapsed by clicking their header. Which ones are collapsed is
  remembered **per type**, not per object.
- **The whole panel body scrolls** (mouse wheel). Rows never get cut off behind the
  buttons. `ScrollList` stays for long lists inside one section (neuron W's).
- Sub-rows are drawn indented by the row type, not with leading spaces.

### Footer rules (the buttons at the bottom)

The footer is for **doing the object's job**, nothing else:

1. **Primary action** first, green (`go`): Collect, Activate, Flip, Turn emission ON.
2. **Other actions** for its job: + / − / Reset, Home / Stop, It's at …, Next slot, Export CSV.
3. **Connection** last: Connect (green) / Disconnect. Devices and the server only.
4. **At most 4 buttons.** More go behind the ⋯ menu.

Things that **leave the footer**:

| Was a button | Moves to |
|---|---|
| Link to source…, Unlink, Unlink all, Always stream | Takes rows (click / ✕) |
| Edit equation…, Set command…, Add column… | rows in State / Takes |
| Clear rows…, Remove from server, Stop server | ⋯ menu, red, with confirmation |

**The ⋯ menu** (header, same items as the right-click menu): Rename, Copy, Go to,
Create machine…, Unlink all, the type's destructive actions, Delete. That puts Delete
in the panel for the first time.

Safety-critical controls (SuperK emission) stay as the primary button **and** on the
object, as now.

### How a type fills it in (API)

The default `inspector()` builds the whole panel. A type overrides only the pieces
it needs:

```python
def status(self, app):        # → (text, color) or None
def state_rows(self, app):    # → [Row / Field / Stepper ...]  the State section
def info_rows(self, app):     # → extra Info rows (ID + position are automatic)
def actions(self, app):       # → [Btn ...]  footer (replaces buttons())
def menu_items(self, app):    # → [(label, callback, style)]  extra ⋯ items
# Takes, Gives and Settings come from takes(), gives(), PROPS. No code needed.
```

New row types: `Section(title, rows, collapsible=True)` and `PortRow(port)` (drawn by
the panel from the Take, with the ✕, source, give and value).

---

## Rollout (each step leaves the app working)

1. **Panel shell.** Section headers, whole-panel scrolling, ⋯ menu, header with name +
   status. The default `inspector()` puts the existing rows into sections. No linking changes.
2. **Takes / Gives core.** `Take`, `Give`, `links` storage with migration, helpers,
   framework edge tracking, give-picker menu, framework-drawn wires. Port **Readout**
   and **Relay** first (simplest), then check old saves load.
3. **Port the rest**, one type per commit: Function → Counter → Tag → Collector → Device
   (+ subclasses). Each one deletes its own link code.
4. **Footer cleanup.** Move link / edit / destructive buttons out, following the rules above.
5. **Extras.** Drag Give → Take, reader list on Gives, per-type collapse memory.

---

## Decisions (2026-10-14)

1. **Device params → Inputs / takes, always.** Every settable thing on any object is an
   input. When it's unlinked, its row shows the value (click to type one) and **link**.
   Outputs / gives is just a list of what the object gives.
2. **Linking starts from the panel.** Click **link** on an input's row, then the source.
   Canvas chips stay as a shortcut.
3. **UI words:** the section headers say **Inputs / takes** and **Outputs / gives**. The
   E menu's *Outputs* category (Readout) gets renamed to *Displays* so it doesn't clash.
4. **Names stay optional.** Only types that have a name show one (Counter, Tag, Flip
   mirror, Data collector, devices…). A row like "← Slider · value" uses the type name
   when the source has no name of its own. Hovering the row highlights the source on the
   canvas, which tells apart two sliders.
5. **On/off is just 0 or 1.** No separate kind of value and no cutoff setting. A tag's
   streaming input is on at 1 and off at 0. A counter's up / down / reset act each time
   their input goes 0 → 1. (In code, anything that isn't 0 counts as on.)
