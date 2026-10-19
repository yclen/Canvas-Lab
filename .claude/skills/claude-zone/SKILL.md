---
name: claude-zone
description: Build or change things in the user's running canvas world inside a Claude zone (the orange areas), with claude_world.py. Use when asked to "build zone 1", "update the Noise rig zone", "put a … in my zone", or to fix/tidy a zone's layout.
---

# Building in a Claude zone

Claude works in the **running** world (never the save file) through `python claude_world.py`,
run from the repo root. Every command prints JSON (`{"ok": true, ...}` or `{"ok": false, "error"}`);
exit code 2 means no world is running — tell the user, don't start one. Full reference:
`docs/ARCHITECTURE.md`, "Claude in the world — agents".

**The user wants to watch Claude work** — live, in the zone: building a piece, looking at it,
testing it, fixing it in place (moving, rewiring, deleting what didn't work), and leaving the
finished product there. So work in the open, in stages, not in private: no dry run unless the user
asks for one (see the end of section 3).

## 0. Attach first, detach last

```bash
python claude_world.py attach        # run in the BACKGROUND (Bash run_in_background) — it stays
...                                  # every command now goes through it
python claude_world.py detach        # when the work is finished
```

While attached, Claude is in the world as a player: its name in the player list, its view box
around the zone, and a cursor that **glides** to each thing before acting — clicking to create,
dragging what it moves (you see it travel), drawing each wire from the output dot to the input,
going to each thing it deletes. Without attach every command joins for a moment and there's
nothing to watch, so always attach. If `attach` says it couldn't listen, Claude's already attached
(use it). It leaves by itself after 15 idle minutes, or if the world stops.

## 1. Look

```bash
python claude_world.py zones                  # every zone: name, box (size), brief
python claude_world.py query --zone "Zone 1"  # what's already there (ids, at, size, settings, state, links)
python claude_world.py catalog                # every buildable type: settings, inputs, outputs
python claude_world.py catalog --type function
```

- The zone's **brief** is the user's request; its box gives the room you have.
- The user's objects in the zone may be **link sources**, never changed or moved.
- `query` puts hidden settings under `state` (a Function's `expression`, a Sequence's `items`,
  a Random's range). In a plan or `set` they go in `settings` like any other key.
- A Label's words are its `name`, not a setting.

## 2. Plan (JSON)

```json
{"zone": "Zone 1", "steps": [
  {"create": "label", "as": "title", "at": [300, 40], "name": "Noisy sine"},
  {"create": "function", "as": "wave", "at": [150, 160], "settings": {"expression": "3*sin(t)"}},
  {"create": "random", "as": "noise", "at": [450, 160], "name": "Noise",
   "settings": {"distribution": "normal", "draws": "every frame", "spread": 0.4}},
  {"link": "noise.mean", "from": "wave.y"},
  {"set": "noise", "settings": {"spread": 0.2}},
  {"move": "wave", "to": [150, 180]},
  {"unlink": "noise.mean"},
  {"delete": ["wave"]}]}
```

- With `"zone"`, every `at` / `to` is the object's **center**, from the zone's top-left.
- `as` names an object for later steps (`name.port`); a real id works too, e.g. a user's slider
  as a `from`.
- Write plans to the scratchpad, not the repo.

Layout rules:
- **Measure first.** Sizes differ a lot by type and settings: create a part and `query --id` it
  (`size`), or check the size in the build result's object, before placing its neighbours.
- Lay out in **rows, left to right**: inputs on the left, outputs on the right, wires flowing
  right. One row per idea, with a Label heading above each row (and a title Label at the top).
- No overlaps, and **generous gaps** — well over the 20-unit "crowded" limit (aim ~60+ between
  parts, more between rows). Keep everything wholly inside the zone, with margin from its edges.
- Center the whole design in the zone: the `used` box from `check` / the build result should
  have about equal space left/right and top/bottom.

## 3. Build in stages: build, look, test, fix

Work one idea at a time (one row / sub-circuit), each stage a small plan or a few commands:

```bash
python claude_world.py build stage1.json --pace 0.25        # create + wire one sub-circuit
python claude_world.py render --zone "Zone 1" --out look.png  # LOOK: open the PNG (Read tool)
python claude_world.py check --zone "Zone 1"                  # overlaps / crowding / poking out
```

1. **Look** after every stage: open the render. Problems are outlined in red and numbered; also
   judge balance, alignment, and whether headings sit over their rows.
2. **Test** what you built, visibly — nudge its controls and read the results:
   ```bash
   python claude_world.py sweep SLIDER_ID value 0 2.5 5 10 --read READOUT_ID.value FN_ID.y --hold 0.5
   ```
   It sets the value in turn (the cursor on the control), waits, reads the outputs (the cursor goes
   to each). Check the numbers make sense; `query --id` reads any one object's live values.
   Leave controls at a sensible value afterwards.
3. **Fix in place**: `move` it (it's dragged there), `set` it, re-`link` it, `delete` what didn't
   work — the user sees each change. Then look again.
4. Next stage. At the end: `check` shows **zero problems**, the render looks balanced (the `used`
   box roughly centered), everything has been tested — then `detach`.

Single commands (positions with `--zone` are zone-relative centers; negative values as `--at=-5,10`):

```bash
python claude_world.py create function --zone "Zone 1" --at 150,160 --name Wave --set expression=2*sin(t)
python claude_world.py set ID expression=2*sin(t) spread=0.3
python claude_world.py link TARGET.PORT SRC.KEY
python claude_world.py unlink TARGET.PORT
python claude_world.py move ID --zone "Zone 1" --to 300,200
python claude_world.py delete ID [ID ...]
```

**Dry run (only when asked, or before a big risky rearrangement):** `build plan.json --dry-run
--render dry.png` runs a plan on a private copy of the world — same answers, `problems` and render,
nothing changed, nothing to watch.

## 4. Rules the world enforces (and habits that avoid refusals)

- Only inside a Claude zone; only Claude's own objects (the zone's `made` list, center still in
  the zone). A refused change answers `"refused": true` with why — read it, don't retry blindly.
- **Never hardware** (`hardware: true` in `catalog`: Devices, the instrument server). Where a
  device is needed, put a Label placeholder (e.g. "Servo goes here → link its angle to Out")
  and tell the user what to place and connect.
- A `set` that would grow an object past the zone edge is refused whole: **move it first, then
  scale it**.
- Don't resize the zone the user is working with unless asked (`resize-zone --zone Z --size W,H
  [--at X,Y]` exists; it refuses to cut through anything or go under 200 a side).
- If the zone's size or contents change mid-work (the user is editing too), run `zones` /
  `query` again and re-measure before the next build.

## 5. Expression tips (Function objects)

- `t` is time in **seconds**.
- A clock / square wave at F Hz: `1 if (t*F % 1) < 0.5 else 0`.
- Press-type inputs fire on a **0 → 1 rise**, not while high.
- Values are sampled about **60 frames/s**: a pulse narrower than ~3 frames (~50 ms) can be
  missed — make pulses at least that wide.

## 6. Report

`detach`, then tell the user what you built, how it's wired, what you tested (the sweep numbers),
any placeholders they need to fill (hardware), and show the final render (SendUserFile if they
may be on another device).
