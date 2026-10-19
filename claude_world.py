"""claude_world.py — how Claude (an agent) talks to the running world.

It joins the world as "Claude" with the agent role (see agent_api.py: the
world only lets an agent ask), asks one thing, prints the answer as JSON, and
leaves. The world must be running (Launch world on the home screen); it never
touches the save file.

    python claude_world.py status                  is a world running? who's in it?
    python claude_world.py zones                   the Claude zones: name, box, brief
    python claude_world.py catalog [--type T]      what can be built: settings, inputs, outputs
    python claude_world.py query --zone NAME       what's in a zone (settings, links, live values)
    python claude_world.py query --id ID           one object
    python claude_world.py query --near X,Y,R      what's around a point
    python claude_world.py query                   a summary of everything (type, id, name, where)
    python claude_world.py render --zone NAME [--out file.png] [--width 1600]
                                                   draw the zone (and what's in it) to a PNG

Every answer is {"ok": true, ...} or {"ok": false, "error": "..."}; the exit
code is 0 / 1 (2: no world running).
"""

import argparse
import json
import os
import sys

import warnings  # noqa: E402

warnings.filterwarnings("ignore", category=UserWarning)   # pygame's pkg_resources notice: stdout stays JSON
os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")      # renders off screen; no window
import pygame  # noqa: E402

import agent_api  # noqa: E402
import canvas  # noqa: E402
import things  # noqa: E402,F401 — defining the catalog's classes registers them
import world_link  # noqa: E402

RENDER_MARGIN = 0.06            # around the zone in a render, as a share of its size
RENDER_WIDTH = 1600
RENDER_TAG_PX = 40              # px kept above the zone for its name tag


def emit(answer):
    sys.stdout.reconfigure(encoding="utf-8")
    print(json.dumps(answer, indent=2, ensure_ascii=False))
    return 0 if answer.get("ok") else 1


def join():
    """(link, welcome) for the running world, or raises LookupError / ConnectionError."""
    host, _status, _pc = world_link.find_world()
    if host is None:
        raise LookupError("no world is running — launch it from the canvas's home screen (Launch world)")
    link = world_link.WorldLink(agent_api.AGENT_NAME, host, role="agent")
    welcome = link.connect()
    return link, welcome


def ask(link, message):
    reply = link.request(message, timeout=10.0)
    if reply is None:
        return {"ok": False, "error": "the world didn't answer"}
    return {k: v for k, v in reply.items() if k not in ("op", "seq")}


def world_copy(welcome):
    """The world as the welcome described it: a Scene of objects with their live state."""
    objects = [canvas.object_from_dict(d) for d in welcome.get("objects", [])]
    states = welcome.get("state", {})
    for o in objects:
        if o.id in states:
            world_link.apply_state(o, states[o.id])
    return canvas.Scene(objects, path=None)


def render(welcome, zone_name, out, width):
    pygame.init()
    pygame.display.set_mode((1, 1))
    scene = world_copy(welcome)
    zone = agent_api.find_zone(scene, zone_name)
    if zone is None:
        return {"ok": False, "error": f"no Claude zone called {zone_name!r}"}
    mx, my = zone.w * RENDER_MARGIN, zone.h * RENDER_MARGIN
    w = zone.w + 2 * mx
    zoom = width / w
    tag = RENDER_TAG_PX / zoom                     # room above for the zone's name tag (fixed screen size)
    left, top, h = zone.x - mx, zone.y - my - tag, zone.h + 2 * my + tag
    height = max(1, round(width * h / w))
    camera = canvas.Camera()
    camera.zoom = zoom
    camera.offset.update(left, top)
    surface = pygame.Surface((width, height))
    surface.fill(canvas.BG_COLOR)
    drawn = sorted(scene.objects, key=lambda o: o.draw_layer)
    for o in drawn:
        o.draw_links(surface, camera)
    for o in drawn:
        o.draw(surface, camera)
    out = os.path.abspath(out or f"{zone.display_name.replace(' ', '_')}.png")
    pygame.image.save(surface, out)
    return {"ok": True, "zone": agent_api.zone_info(zone), "image": out, "size": [width, height],
            "world_per_px": round(1 / camera.zoom, 4)}


def main(argv=None):
    parser = argparse.ArgumentParser(description="Claude's link to the running world (read-only for now).")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("status", help="is a world running, and who's in it")
    sub.add_parser("zones", help="the Claude zones and their briefs")
    cat = sub.add_parser("catalog", help="what can be built")
    cat.add_argument("--type", help="just this type (e.g. function, time_series)")
    q = sub.add_parser("query", help="what's in a zone, one object, or around a point")
    q.add_argument("--zone")
    q.add_argument("--id")
    q.add_argument("--near", help="x,y,radius")
    r = sub.add_parser("render", help="draw a zone to a PNG")
    r.add_argument("--zone", required=True)
    r.add_argument("--out")
    r.add_argument("--width", type=int, default=RENDER_WIDTH)
    args = parser.parse_args(argv)

    if args.command == "status":
        host, status, pc = world_link.find_world()
        if host is None:
            emit({"ok": False, "error": "no world is running"})
            return 2
        return emit({"ok": True, "host": host, "pc": pc or "this PC", **{k: v for k, v in status.items()
                                                                       if k != "op"}})
    try:
        link, welcome = join()
    except LookupError as e:
        emit({"ok": False, "error": str(e)})
        return 2
    except (OSError, ValueError) as e:
        return emit({"ok": False, "error": f"couldn't join the world: {e}"})
    try:
        if args.command == "zones":
            return emit(ask(link, {"op": "zones"}))
        if args.command == "catalog":
            return emit(ask(link, {"op": "catalog", **({"type": args.type} if args.type else {})}))
        if args.command == "query":
            message = {"op": "query"}
            if args.zone:
                message["zone"] = args.zone
            elif args.id:
                message["id"] = args.id
            elif args.near:
                message["near"] = [float(v) for v in args.near.split(",")]
            return emit(ask(link, message))
        if args.command == "render":
            return emit(render(welcome, args.zone, args.out, args.width))
    finally:
        link.close()
    return 1


if __name__ == "__main__":
    sys.exit(main())
