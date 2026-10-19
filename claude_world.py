"""claude_world.py — how Claude (an agent) talks to the running world.

It joins the world as "Claude" with the agent role (see agent_api.py: the
world checks everything an agent asks), does one thing, prints the answer as
JSON, and leaves. The world must be running (Launch world on the home screen);
it never touches the save file. Claude builds only inside a Claude zone, only
on what it made there, never lab hardware — the world refuses anything else
and says why.

Looking:
    python claude_world.py status                  is a world running? who's in it?
    python claude_world.py zones                   the Claude zones: name, box, brief
    python claude_world.py catalog [--type T]      what can be built: settings, inputs, outputs
    python claude_world.py query --zone NAME       what's in a zone (settings, links, live values)
    python claude_world.py query --id ID           one object
    python claude_world.py query --near X,Y,R      what's around a point
    python claude_world.py query                   a summary of everything (type, id, name, where)
    python claude_world.py render --zone NAME [--out file.png] [--width 1600] [--margin 0.2] [--clean]
                                                   draw the zone (and a margin around it) to a PNG,
                                                   layout problems outlined in red and numbered
    python claude_world.py check --zone NAME       layout problems: overlaps, crowding, poking out
Building (--zone NAME: --at / --to are from the zone's top-left corner; a negative one
needs the = form, --at=-200,100):
    python claude_world.py create TYPE --at X,Y [--zone NAME] [--name N] [--set key=value ...]
    python claude_world.py set ID key=value ...    a setting or state (e.g. expression=2*sin(t))
    python claude_world.py link TARGET.PORT SRC.KEY     TARGET's input PORT follows SRC's output KEY
    python claude_world.py unlink TARGET.PORT
    python claude_world.py move ID --to X,Y [--zone NAME]
    python claude_world.py delete ID [ID ...]
    python claude_world.py resize-zone --zone NAME --size W,H [--at X,Y]   never cutting through anything
    python claude_world.py build PLAN.json [--pace 0.25] [--render out.png] [--dry-run]
        a whole design in one go, step by step (you see Claude's cursor at each), e.g.
        {"zone": "Noise rig", "steps": [
            {"create": "function", "as": "wave", "at": [200, 250], "settings": {"expression": "3*sin(t)"}},
            {"create": "random", "as": "noise", "at": [520, 250], "name": "Noise",
             "settings": {"distribution": "normal", "draws": "every frame", "spread": 0.4}},
            {"link": "noise.mean", "from": "wave.y"},
            {"set": "noise", "settings": {"spread": 0.2}},
            {"move": "wave", "to": [200, 300]},
            {"unlink": "noise.mean"},
            {"delete": ["wave"]}]}
        "as" names an object for later steps (an id works too); with "zone", positions are
        from the zone's top-left. It stops at the first step the world refuses. After it, the
        layout check runs ("problems"). --dry-run builds on a private copy of the world instead:
        the same answers, problems and render, nothing changed — try a layout there first.

Every answer is {"ok": true, ...} or {"ok": false, "error": "..."}; the exit
code is 0 / 1 (2: no world running).
"""

import argparse
import json
import os
import sys
import time

import warnings  # noqa: E402

warnings.filterwarnings("ignore", category=UserWarning)   # pygame's pkg_resources notice: stdout stays JSON
os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")      # renders off screen; no window
import pygame  # noqa: E402

import agent_api  # noqa: E402
import canvas  # noqa: E402
import things  # noqa: E402,F401 — defining the catalog's classes registers them
import world_link  # noqa: E402

RENDER_MARGIN = 0.2             # around the zone in a render, as a share of its size: enough to see what pokes out
PROBLEM_COLOR = (255, 70, 70)    # a render outlines layout problems (agent_api.layout_check) in this
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


def value_of(text):
    """key=value's value: JSON if it reads as JSON (numbers, true, lists), else the text."""
    try:
        return json.loads(text)
    except ValueError:
        return text


def settings_of(pairs):
    out = {}
    for pair in pairs or []:
        key, sep, value = pair.partition("=")
        if not sep:
            raise ValueError(f"expected key=value, got {pair!r}")
        out[key.strip()] = value_of(value)
    return out


def port_of(text, refs=None):
    """"ID.PORT" (or "name.PORT" from a plan) -> (id, port)."""
    obj, sep, port = str(text).rpartition(".")
    if not sep:
        raise ValueError(f"expected OBJECT.PORT, got {text!r}")
    return (refs or {}).get(obj, obj), port


def zone_origin(send, zone_name):
    """The zone's top-left corner (positions with --zone are from there), and its box.
    send: a link, or a send(message) -> reply function."""
    if not zone_name:
        return (0.0, 0.0), None
    reply = send({"op": "zones"}) if callable(send) else ask(send, {"op": "zones"})
    for z in reply.get("zones", []):
        if z["name"].lower() == zone_name.strip().lower() or z["id"] == zone_name:
            return (z["x"], z["y"]), z
    raise LookupError(f"no Claude zone called {zone_name!r}")


def point(text_or_list, origin):
    x, y = ([float(v) for v in text_or_list.split(",")] if isinstance(text_or_list, str)
            else [float(v) for v in text_or_list])
    return [origin[0] + x, origin[1] + y]


def show_cursor(link, at, zone=None):
    """Claude's cursor (and the zone it's looking at) for everyone in the world."""
    view = [zone["x"], zone["y"], zone["w"], zone["h"]] if zone else None
    link.send({"op": "presence", "cursor": [round(at[0], 1), round(at[1], 1)], "view": view, "down": False})


def build(link, plan, pace, send=None):
    """Run a plan's steps in order; stop at the first the world refuses. send(message) -> reply
    talks to the world (default) — or to a private copy of it, for a dry run."""
    send = send or (lambda message: ask(link, message))
    origin, zone = zone_origin(send, plan.get("zone"))
    refs, results = {}, []
    ref = lambda name: refs.get(name, name)        # noqa: E731
    for i, step in enumerate(plan.get("steps", [])):
        if "create" in step:
            at = point(step.get("at", [0, 0]), origin)
            message = {"op": "create", "type": step["create"], "at": at, "settings": step.get("settings") or {}}
            if step.get("name") is not None:
                message["name"] = step["name"]
        elif "set" in step:
            message = {"op": "set", "id": ref(step["set"]), "settings": step.get("settings") or {}}
        elif "link" in step:
            target, port = port_of(step["link"], refs)
            src, key = port_of(step["from"], refs)
            message = {"op": "link", "target": target, "port": port, "src": src, "key": key}
        elif "unlink" in step:
            target, port = port_of(step["unlink"], refs)
            message = {"op": "unlink", "target": target, "port": port}
        elif "move" in step:
            message = {"op": "move", "id": ref(step["move"]), "to": point(step["to"], origin)}
        elif "delete" in step:
            names = step["delete"] if isinstance(step["delete"], list) else [step["delete"]]
            message = {"op": "delete", "ids": [ref(n) for n in names]}
        else:
            return {"ok": False, "error": f"step {i + 1}: not a step I know: {step}", "done": results, "refs": refs}
        cursor = message.get("at") or message.get("to")
        if cursor is not None and link is not None:
            show_cursor(link, cursor, zone)
        reply = send(message)
        results.append({"step": i + 1, **{k: v for k, v in reply.items() if k != "object"}})
        if not reply.get("ok"):
            return {"ok": False, "error": f"step {i + 1}: {reply.get('error')}", "done": results, "refs": refs}
        if "create" in step and step.get("as"):
            refs[step["as"]] = reply["id"]
        if pace:
            time.sleep(pace)
    return {"ok": True, "steps": len(results), "refs": refs, "done": results}


def world_copy(welcome):
    """The world as the welcome described it: a Scene of objects with their live state."""
    objects = [canvas.object_from_dict(d) for d in welcome.get("objects", [])]
    states = welcome.get("state", {})
    for o in objects:
        if o.id in states:
            world_link.apply_state(o, states[o.id])
    return canvas.Scene(objects, path=None)


def render(welcome, zone_name, out, width, margin=RENDER_MARGIN, problems=True, scene=None):
    """The zone (and `margin` of its size around it) drawn to a PNG; with problems, the
    layout check's overlaps / crowding / poking-out outlined in red and numbered. scene:
    draw this one (a dry run's) instead of the welcome's."""
    pygame.init()
    pygame.display.set_mode((1, 1))
    scene = scene or world_copy(welcome)
    zone = agent_api.find_zone(scene, zone_name)
    if zone is None:
        return {"ok": False, "error": f"no Claude zone called {zone_name!r}"}
    mx, my = zone.w * margin, zone.h * margin
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
    check = agent_api.layout_check(scene, zone.id)
    if problems:
        font = canvas.get_font(18, "bahnschrift")
        for n, p in enumerate(check["problems"], 1):
            for box in p.get("boxes") or [p["box"]]:
                tl = camera.world_to_screen(box[:2])
                br = camera.world_to_screen(box[2:])
                r = pygame.Rect(round(tl[0]), round(tl[1]), max(2, round(br[0] - tl[0])), max(2, round(br[1] - tl[1])))
                pygame.draw.rect(surface, PROBLEM_COLOR, r.inflate(6, 6), 2)
                tag = font.render(str(n), True, (255, 255, 255))
                badge = tag.get_rect(topleft=(r.x - 3, r.y - 3)).inflate(8, 2)
                pygame.draw.rect(surface, PROBLEM_COLOR, badge, border_radius=4)
                surface.blit(tag, tag.get_rect(center=badge.center))
    out = os.path.abspath(out or f"{zone.display_name.replace(' ', '_')}.png")
    pygame.image.save(surface, out)
    return {"ok": True, "zone": agent_api.zone_info(zone), "image": out, "size": [width, height],
            "world_per_px": round(1 / camera.zoom, 4), "used": check["used"],
            "problems": [f"{n}. {p['kind']}: {p['what']}" for n, p in enumerate(check["problems"], 1)]}


def main(argv=None):
    parser = argparse.ArgumentParser(description="Claude's link to the running world.")
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
    r.add_argument("--margin", type=float, default=RENDER_MARGIN, help="room around the zone, as a share of it")
    r.add_argument("--clean", action="store_true", help="don't outline the layout problems")
    ck = sub.add_parser("check", help="a zone's layout problems: overlaps, crowding, poking out")
    ck.add_argument("--zone", required=True)
    rz = sub.add_parser("resize-zone", help="resize (and move) a Claude zone — never cutting through anything")
    rz.add_argument("--zone", required=True)
    rz.add_argument("--size", required=True, help="w,h")
    rz.add_argument("--at", help="x,y of its new top-left corner (world)")
    c = sub.add_parser("create", help="a new object, inside a Claude zone")
    c.add_argument("type")
    c.add_argument("--at", required=True, help="x,y (from the zone's top-left with --zone; negative: --at=-5,10)")
    c.add_argument("--zone")
    c.add_argument("--name")
    c.add_argument("--set", nargs="*", metavar="KEY=VALUE")
    st = sub.add_parser("set", help="settings / state of one of Claude's objects")
    st.add_argument("id")
    st.add_argument("pairs", nargs="+", metavar="KEY=VALUE")
    lk = sub.add_parser("link", help="TARGET.PORT follows SRC.KEY")
    lk.add_argument("target")
    lk.add_argument("source")
    ul = sub.add_parser("unlink", help="TARGET.PORT follows nothing")
    ul.add_argument("target")
    mv = sub.add_parser("move", help="move one of Claude's objects (center to x,y)")
    mv.add_argument("id")
    mv.add_argument("--to", required=True)
    mv.add_argument("--zone")
    dl = sub.add_parser("delete", help="delete Claude's objects")
    dl.add_argument("ids", nargs="+")
    bd = sub.add_parser("build", help="run a build plan (JSON) step by step")
    bd.add_argument("plan")
    bd.add_argument("--pace", type=float, default=0.25, help="seconds between steps (to watch it build)")
    bd.add_argument("--render", help="then draw the plan's zone to this PNG")
    bd.add_argument("--dry-run", action="store_true",
                    help="build on a private copy of the world instead: see the result (and --render it) "
                         "without changing anything")
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
            return emit(render(welcome, args.zone, args.out, args.width, args.margin, not args.clean))
        if args.command == "check":
            return emit(ask(link, {"op": "check", "zone": args.zone}))
        if args.command == "resize-zone":
            w, h = (float(v) for v in args.size.split(","))
            message = {"op": "resize_zone", "zone": args.zone, "w": w, "h": h}
            if args.at:
                message["x"], message["y"] = (float(v) for v in args.at.split(","))
            return emit(ask(link, message))
        if args.command == "create":
            origin, zone = zone_origin(link, args.zone)
            at = point(args.at, origin)
            show_cursor(link, at, zone)
            message = {"op": "create", "type": args.type, "at": at, "settings": settings_of(args.set)}
            if args.name is not None:
                message["name"] = args.name
            return emit(ask(link, message))
        if args.command == "set":
            return emit(ask(link, {"op": "set", "id": args.id, "settings": settings_of(args.pairs)}))
        if args.command == "link":
            target, port = port_of(args.target)
            src, key = port_of(args.source)
            return emit(ask(link, {"op": "link", "target": target, "port": port, "src": src, "key": key}))
        if args.command == "unlink":
            target, port = port_of(args.target)
            return emit(ask(link, {"op": "unlink", "target": target, "port": port}))
        if args.command == "move":
            origin, zone = zone_origin(link, args.zone)
            to = point(args.to, origin)
            show_cursor(link, to, zone)
            return emit(ask(link, {"op": "move", "id": args.id, "to": to}))
        if args.command == "delete":
            return emit(ask(link, {"op": "delete", "ids": args.ids}))
        if args.command == "build":
            with open(args.plan, encoding="utf-8") as f:
                plan = json.load(f)
            if args.dry_run:
                pygame.init()
                pygame.display.set_mode((1, 1))
                copy = world_copy(welcome)
                copy.save = lambda: None                   # (a copy has no file; never write one)
                send = lambda message: agent_api.answer(copy, message)     # noqa: E731
                result = build(None, plan, 0, send)
                result["dry_run"] = True
                if plan.get("zone"):
                    check = send({"op": "check", "zone": plan["zone"]})
                    result["problems"] = [f"{p['kind']}: {p['what']}" for p in check.get("problems", [])]
                    result["used"] = check.get("used")
                    if args.render:
                        result["render"] = render(None, plan["zone"], args.render, RENDER_WIDTH, scene=copy)
                return emit(result)
            result = build(link, plan, max(0.0, args.pace))
            if plan.get("zone"):                       # and how the layout came out
                check = ask(link, {"op": "check", "zone": plan["zone"]})
                result["problems"] = [f"{p['kind']}: {p['what']}" for p in check.get("problems", [])]
            if result.get("ok") and args.render and plan.get("zone"):
                link.close()
                link, welcome = join()                 # a fresh look, with what was just built
                result["render"] = render(welcome, plan["zone"], args.render, RENDER_WIDTH)
            return emit(result)
    except (LookupError, ValueError) as e:
        return emit({"ok": False, "error": str(e)})
    finally:
        link.close()
    return 1


if __name__ == "__main__":
    sys.exit(main())
