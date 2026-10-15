# Canvas Lab

An infinite pan / zoom canvas where you place **objects** — launchers, inputs, displays,
math, data collectors, and the lab's real hardware (stages, power meter, laser, flip
mirrors, filter wheel, detector) — wire their values together, and work in the same live
**world** from several windows at once.

## Install

Python 3.13 (Anaconda on the lab PCs).

```bash
pip install -r requirements.txt
```

A player PC only needs the first section of [requirements.txt](requirements.txt); the lab PC,
which runs the hardware, needs all of it.

## Run

- **From the taskbar:** run `make_shortcut.ps1` once (it's safe to re-run after updates), then
  pin *Canvas App* from the Start menu.
- **By hand:** `python game_loop.py`

The window opens to a **home screen**:

- **Launch world** starts the world (`world_server.py`, its own small window) and joins it.
  The world keeps running when player windows close; closing its window stops it.
- **Join world** when one is already running. Each player has a name and a color, and sees
  the others' cursors, views and selections.
- **Work on my own**: the canvas by itself, no world (only while no world is running).

The instrument server (`instrument_server.py`, the hardware) starts on its own when a device
object connects.

## Controls

| | |
|---|---|
| Wheel / right-drag | zoom / pan |
| **E** | object menu (place a new object; *Machines* tab for saved groups) |
| Click | select, or use the object (press a button, drag a slider, …) |
| Ctrl+click, drag a box | select without using it |
| Right-click | menu for the selection (align, create machine, delete, …) |
| Arrow keys | move the selection |
| **C** / **Delete** | copy the selection at the mouse / delete it |
| Shift+click while placing | keep placing more |
| **Esc** | cancel placing, linking or building |

## Files

| | |
|---|---|
| `game_loop.py` | the app: window, home screen, main loop |
| `canvas.py` | the world, the player's view, the UI, the object toolkit |
| `things.py` | the catalog of object types |
| `world_server.py`, `world_link.py` | the world as its own program, and the link to player windows |
| `instrument_server.py`, `instruments.py`, `drivers/`, `devices.json` | lab hardware |
| `expressions.py` | the safe equation evaluator (Function objects) |
| `scripts/`, `calibration/` | calibration procedures and their results |
| `Nodes.json`, `player.json`, `machines.json` | your saved world, view and machines (not in git) |

## Docs

- [docs/DESIGN.md](docs/DESIGN.md) — the vision and the reasoning behind the big decisions
- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) — how it works: the panel, inputs / outputs,
  the world server, devices, adding an object type
- [docs/OBJECTS.md](docs/OBJECTS.md) — what each object type does
- [docs/MULTIPLAYER_STUDY.md](docs/MULTIPLAYER_STUDY.md) — the multiplayer plan (partly built)
- [docs/archive/](docs/archive/) — plans that are fully built, kept as a record
