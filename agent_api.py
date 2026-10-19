"""agent_api.py — what an agent (Claude, through claude_world.py) may ask the
running world. The world server answers these for a connection that said
"role": "agent" in its hello; each request carries a seq and gets one
{"op": "reply", "seq", "ok": bool, ...} back (an "error" when ok is false).

Looking:
    {"op": "zones"}                        -> {"zones": [zone, ...]}      the Claude zones and their briefs
    {"op": "catalog", "type"?}             -> {"types": [type, ...]}      what can be built, from the classes
    {"op": "query", "zone"? | "id"? | "near"?}  -> {"objects": [object, ...]}
    {"op": "check", "zone"}                -> {"problems": [...], "used"}   overlaps, crowding, poking out
Building — only inside a Claude zone (canvas.ClaudeZone), only on what it made
there (the zone's `made` list), never lab hardware (things.Device):
    {"op": "create", "type", "at": [x, y], "name"?, "settings"?: {key: value}}  -> {"id", "object"}
    {"op": "set", "id", "settings": {key: value}}       any setting or state (a function's expression, …)
    {"op": "link", "target", "port", "src", "key"}      target's input follows src's output
    {"op": "unlink", "target", "port"}
    {"op": "move", "id", "to": [x, y]}
    {"op": "delete", "ids": [...]}
    {"op": "resize_zone", "zone", "w", "h", "x"?, "y"?}   never cutting through anything
A refused change answers why (`Refused`); anything else from an agent is refused.
"""

import inspect

import canvas
import world_link

AGENT_OPS = ("zones", "catalog", "query", "check", "create", "set", "link", "unlink", "move", "delete",
             "resize_zone")
AGENT_NAME = "Claude"


def _plain(value):
    """value as JSON (what world_link sends); anything it can't send, as text."""
    try:
        return world_link.encode(value)
    except Exception:
        return str(value)


def _doc(cls):
    """The first paragraph of a class's docstring, on one line."""
    text = inspect.getdoc(cls) or ""
    return " ".join(text.split("\n\n")[0].split())


def is_hardware(cls):
    """Lab hardware (a Device, the instrument server): never an agent's to make or touch."""
    return any(k.__name__ in ("Device", "InstrumentServer") for k in cls.__mro__)


# --- zones ---------------------------------------------------------------------------------
def zone_info(zone):
    return {"id": zone.id, "name": zone.display_name, "x": zone.x, "y": zone.y, "w": zone.w, "h": zone.h,
            "brief": zone.brief}


def zones(scene):
    return [zone_info(o) for o in scene.objects if isinstance(o, canvas.ClaudeZone)]


def find_zone(scene, name_or_id):
    """A Claude zone by id, or by name (case doesn't matter), or None."""
    key = str(name_or_id).strip().lower()
    for o in scene.objects:
        if isinstance(o, canvas.ClaudeZone) and (o.id == name_or_id or o.display_name.lower() == key):
            return o
    return None


# --- catalog -------------------------------------------------------------------------------
def _sample(cls):
    try:
        return cls.icon_sample()
    except Exception:
        return None


def type_info(cls):
    """One buildable type: its settings, inputs and outputs, from the class itself."""
    sample = _sample(cls)
    props = [{"key": p.key, "label": p.label, "kind": p.kind, "default": _plain(p.default),
              **({"choices": p.choices} if p.choices else {}),
              **({"min": p.min} if p.min is not None else {}), **({"max": p.max} if p.max is not None else {}),
              **({"unit": p.unit} if p.unit else {})}
             for p in cls.props() if not p.hidden and p.key != "links"]
    takes = gives = []
    if sample is not None:
        try:
            takes = [{"key": t.key, "label": t.label, "kind": t.kind} for t in sample.takes()]
            gives = [{"key": g.key, "label": g.label, "kind": g.kind, **({"unit": g.unit} if g.unit else {})}
                     for g in sample.gives()]
        except Exception:
            pass
    return {"type": cls.TYPE, "name": cls.type_name(), "category": getattr(cls, "CATEGORY", None),
            "hardware": is_hardware(cls), "about": _doc(cls), "settings": props, "inputs": takes,
            "outputs": gives}


def catalog(type_name=None):
    """Every type you can place (E menu / Add menu), or just `type_name`."""
    out = []
    for key, cls in sorted(canvas.OBJECT_TYPES.items()):
        if type_name is not None and key != type_name:
            continue
        if type_name is None and not getattr(cls, "MENU_LABEL", None):
            continue
        out.append(type_info(cls))
    return out


# --- query ---------------------------------------------------------------------------------
def object_info(obj, scene):
    """One object as an agent sees it: where it is, its settings, links and live values."""
    c = obj.center
    info = {"id": obj.id, "type": obj.TYPE, "name": obj.panel_name() if hasattr(obj, "panel_name") else None,
            "at": [round(c[0], 1), round(c[1], 1)] if c is not None else None}
    for attr in ("world_width", "world_height"):
        if hasattr(obj, attr):
            info["size"] = [round(getattr(obj, "world_width"), 1), round(getattr(obj, "world_height"), 1)]
            break
    if isinstance(obj, canvas.Wall):
        info["box"] = [obj.x, obj.y, obj.w, obj.h]
    info["settings"] = {p.key: _plain(getattr(obj, p.key, None)) for p in obj.props()
                        if not p.hidden and p.key != "links"}
    hidden = {p.key: _plain(getattr(obj, p.key, None)) for p in obj.props() if p.hidden and p.key != "links"}
    if hidden:
        info["state"] = hidden          # saved but not in the panel's Settings (an expression, a range, …)
    try:
        takes = obj.takes()
        info["inputs"] = [{"key": t.key, "kind": t.kind,
                           "from": ({"id": obj.links[t.key].get("id"), "key": obj.links[t.key].get("key")}
                                    if t.key in obj.links else None)} for t in takes]
        values = obj.outputs()
        info["outputs"] = [{"key": g.key, "value": _plain(values.get(g.key))} for g in obj.gives()]
    except Exception:
        pass
    zone = next((z for z in scene.objects if isinstance(z, canvas.ClaudeZone) and c is not None
                 and z is not obj and z.contains(*c)), None)
    info["zone"] = zone.display_name if zone is not None else None
    info["hardware"] = is_hardware(type(obj))
    return info


def query(scene, zone=None, id=None, near=None):
    """Objects in a zone (by name or id), one object (id), or those near a point
    ([x, y, radius]); with none of these, a summary of everything (type, id,
    name, where) so an agent can find its way around."""
    if id is not None:
        obj = scene.by_id.get(id)
        if obj is None:
            raise LookupError(f"no object with id {id}")
        return [object_info(obj, scene)]
    if zone is not None:
        z = find_zone(scene, zone)
        if z is None:
            raise LookupError(f"no Claude zone called {zone!r}")
        inside = [o for o in scene.objects if o is not z and o.center is not None and z.contains(*o.center)]
        return [object_info(o, scene) for o in inside]
    if near is not None:
        x, y, r = (float(v) for v in near)
        return [object_info(o, scene) for o in scene.objects
                if o.center is not None and (o.center[0] - x) ** 2 + (o.center[1] - y) ** 2 <= r * r]
    return [{"id": o.id, "type": o.TYPE, "name": o.panel_name() if hasattr(o, "panel_name") else None,
             "at": [round(o.center[0], 1), round(o.center[1], 1)] if o.center is not None else None}
            for o in scene.objects]


# --- building (milestone 2): every change checked by the world -----------------------------
NOT_BUILDABLE = ("claude_zone", "wall", "train", "button", "nametag")   # areas and launchers: yours to place


class Refused(Exception):
    """An agent's change the world won't make (the message says why)."""


def _bounds(obj):
    """(left, top, right, bottom) of obj in world units."""
    if isinstance(obj, canvas.Wall):
        return obj.x, obj.y, obj.x + obj.w, obj.y + obj.h
    c = obj.center
    w, h = getattr(obj, "world_width", 0), getattr(obj, "world_height", 0)
    return c[0] - w / 2, c[1] - h / 2, c[0] + w / 2, c[1] + h / 2


def zone_holding(scene, obj):
    """The Claude zone obj sits wholly inside, or None."""
    left, top, right, bottom = _bounds(obj)
    for z in scene.objects:
        if isinstance(z, canvas.ClaudeZone) and z is not obj and z.contains(left, top) and z.contains(right, bottom):
            return z
    return None


def zone_owning(scene, obj):
    """The Claude zone that lists obj as Claude's and has obj's center inside it, or None.
    (Only the center: one Claude made that pokes out — a zone resized smaller, a change
    that grew it — is still Claude's to repair; one you've dragged out is yours.)"""
    c = obj.center
    if c is None:
        return None
    for z in scene.objects:
        if isinstance(z, canvas.ClaudeZone) and obj.id in z.made and z.contains(c[0], c[1]):
            return z
    return None


def _mine(scene, id):
    """An object the agent made, its center still inside its Claude zone."""
    obj = scene.by_id.get(id)
    if obj is None:
        raise Refused(f"no object with id {id}")
    zone = zone_owning(scene, obj)
    if zone is None:
        raise Refused(f"{obj.TYPE} {id} isn't Claude's to change: only what Claude made, inside a Claude zone")
    return obj, zone


def _forget(scene, obj_id):
    for z in scene.objects:
        if isinstance(z, canvas.ClaudeZone) and obj_id in z.made:
            z.made = [i for i in z.made if i != obj_id]


def _coerce(prop, value):
    """A setting's value as the agent sent it -> what the object holds (typed text is parsed the panel's way)."""
    if prop.kind == "data":
        return value
    if isinstance(value, str) and prop.kind != "str":
        return prop.parse(value)
    if prop.kind == "str":
        return str(value)
    if prop.kind == "bool":
        return bool(value)
    if prop.kind == "color":
        return tuple(int(c) for c in value)
    if prop.kind == "choice":
        if value not in prop.choices:
            raise ValueError(f"not one of {prop.choices}")
        return value
    number = float(value) if prop.kind == "float" else int(round(float(value)))
    if prop.min is not None:
        number = max(prop.min, number)
    if prop.max is not None:
        number = min(prop.max, number)
    return number


def _apply_settings(obj, settings, undo=None):
    """Set settings / state by key (any Prop but links); the object's on_prop_changed runs for
    each. undo (a dict), if given, collects each key's old value so the change can be taken back."""
    done = {}
    for key, value in (settings or {}).items():
        if key in ("links", "made"):
            raise Refused(f"'{key}' isn't set this way (links: use link)")
        try:
            prop = obj.prop(key)
        except StopIteration:
            raise Refused(f"{obj.TYPE} has no setting '{key}' — see catalog / query") from None
        try:
            new = _coerce(prop, value)
        except (ValueError, TypeError) as e:
            raise Refused(f"{key}: {e}") from None
        if undo is not None and key not in undo:
            undo[key] = getattr(obj, key)
        setattr(obj, key, new)
        obj.on_prop_changed(key)
        done[key] = _plain(getattr(obj, key))
    return done


def create(scene, type_name, at, name=None, settings=None):
    """A new object of type_name centered at `at`, wholly inside a Claude zone."""
    cls = canvas.OBJECT_TYPES.get(type_name)
    if cls is None or not getattr(cls, "MENU_LABEL", None):
        raise Refused(f"no buildable type '{type_name}' — see catalog")
    if is_hardware(cls):
        raise Refused(f"{type_name} is lab hardware: devices are yours to place and connect")
    if type_name in NOT_BUILDABLE:
        raise Refused(f"{type_name} isn't something Claude builds")
    x, y = (float(v) for v in at)
    try:
        obj = cls(x, y)
    except TypeError:
        raise Refused(f"{type_name} can't be placed at a point") from None
    if name is not None and hasattr(obj, "text"):
        obj.text = str(name)
    _apply_settings(obj, settings)
    obj.scene = scene                              # (a size can depend on the scene: the zone check needs it)
    zone = zone_holding(scene, obj)
    if zone is None:
        raise Refused(f"{type_name} at {x:g}, {y:g} wouldn't be wholly inside a Claude zone")
    scene.add(obj)
    zone.made = zone.made + [obj.id]
    scene.save()
    return obj, zone


def set_settings(scene, id, settings):
    """Change settings; one that would leave the object poking out of its zone (it grew past an
    edge) is taken back whole and refused — move it somewhere with room first."""
    obj, zone = _mine(scene, id)
    was_inside = zone_holding(scene, obj) is not None
    undo = {}
    try:
        done = _apply_settings(obj, settings, undo)
    except Refused:
        _restore(obj, undo)
        raise
    if was_inside and zone_holding(scene, obj) is None:
        _restore(obj, undo)
        raise Refused("that would push it out of its zone, so nothing changed — move it somewhere with room first")
    scene.save()
    return obj, done


def _restore(obj, undo):
    for key, value in undo.items():
        setattr(obj, key, value)
        obj.on_prop_changed(key)


def link(scene, target_id, port, src_id, key):
    """target's input `port` follows src's output `key`. The target must be Claude's;
    the source may be anything inside a Claude zone (yours too, e.g. a slider you put there)."""
    target, _zone = _mine(scene, target_id)
    src = scene.by_id.get(src_id)
    if src is None:
        raise Refused(f"no object with id {src_id}")
    if zone_holding(scene, src) is None:
        raise Refused("the source must be inside a Claude zone — wiring Claude's design to your layout is yours to do")
    take = target.find_take(port)
    if take is None:
        raise Refused(f"{target.TYPE} has no input '{port}' (inputs: {[t.key for t in target.takes()]})")
    give = next((g for g in src.gives() if g.key == key), None)
    if give is None:
        raise Refused(f"{src.TYPE} has no output '{key}' (outputs: {[g.key for g in src.gives()]})")
    if not canvas.kinds_match(take.kind, give.kind):
        raise Refused(f"'{port}' takes {take.kind}, '{key}' gives {give.kind}")
    target.link(port, src, key)
    return target


def unlink(scene, target_id, port):
    target, _zone = _mine(scene, target_id)
    target.unlink(port)
    return target


def move(scene, id, to):
    """Center obj at `to` (wholly inside a Claude zone; into another one moves it over)."""
    obj, zone = _mine(scene, id)
    if not getattr(obj, "movable", False):
        raise Refused(f"{obj.TYPE} can't be moved")
    c = tuple(obj.center)                          # a copy: center is the live position
    x, y = (float(v) for v in to)
    obj.move(x - c[0], y - c[1])
    landed = zone_holding(scene, obj)
    if landed is None:
        obj.move(c[0] - x, c[1] - y)
        raise Refused(f"{x:g}, {y:g} isn't wholly inside a Claude zone")
    if landed is not zone:
        _forget(scene, obj.id)
        landed.made = landed.made + [obj.id]
    scene.save()
    return obj


def delete(scene, ids):
    """Delete Claude's own objects (they can be brought back like any delete: Claude's Ctrl+Z is the world's)."""
    objs = [_mine(scene, i)[0] for i in ids]
    for o in objs:
        _forget(scene, o.id)
    scene.delete_many(objs, by=AGENT_NAME)
    return [o.id for o in objs]


def change(scene, msg):
    """The reply body for an agent's change (op: create / set / link / unlink / move / delete)."""
    op = msg.get("op")
    if op == "create":
        obj, zone = create(scene, msg.get("type"), msg.get("at") or (0, 0), msg.get("name"), msg.get("settings"))
        return {"ok": True, "id": obj.id, "zone": zone.display_name, "object": object_info(obj, scene)}
    if op == "set":
        obj, done = set_settings(scene, msg.get("id"), msg.get("settings"))
        return {"ok": True, "id": obj.id, "set": done}
    if op == "link":
        target = link(scene, msg.get("target"), msg.get("port"), msg.get("src"), msg.get("key"))
        return {"ok": True, "id": target.id, "links": target.links}
    if op == "unlink":
        target = unlink(scene, msg.get("target"), msg.get("port"))
        return {"ok": True, "id": target.id, "links": target.links}
    if op == "move":
        obj = move(scene, msg.get("id"), msg.get("to") or (0, 0))
        c = obj.center
        return {"ok": True, "id": obj.id, "at": [round(c[0], 1), round(c[1], 1)]}
    if op == "delete":
        return {"ok": True, "deleted": delete(scene, msg.get("ids") or [])}
    return None


# --- layout: what's wrong with a zone's layout, and resizing a zone --------------------------
LAYOUT_GAP = 20                 # world units objects should keep between them (closer is "crowded")
ZONE_MIN = 200                  # a zone can't be made smaller than this (each side)


def _members(scene, zone):
    """What touches a zone at all (so something hanging off an edge counts), and everything
    Claude made for it (even if it's wandered right out) — but not areas (walls, other zones)."""
    out = []
    for o in scene.objects:
        if o is zone or isinstance(o, canvas.Wall) or o.center is None:
            continue
        l, t, r, b = _bounds(o)
        if (l < zone.x + zone.w and r > zone.x and t < zone.y + zone.h and b > zone.y) or o.id in zone.made:
            out.append(o)
    return out


def _label(o):
    name = o.panel_name() if hasattr(o, "panel_name") else None
    return f"{o.TYPE} {name!r} ({o.id})" if name else f"{o.TYPE} ({o.id})"


def layout_check(scene, zone_name):
    """A zone's layout problems: objects that overlap or nearly touch, and objects poking out of
    the zone. Each with the ids and boxes involved, so they can be fixed."""
    zone = find_zone(scene, zone_name)
    if zone is None:
        raise LookupError(f"no Claude zone called {zone_name!r}")
    members = _members(scene, zone)
    boxes = {o.id: _bounds(o) for o in members}
    problems = []
    for o in members:
        l, t, r, b = boxes[o.id]
        if l < zone.x or t < zone.y or r > zone.x + zone.w or b > zone.y + zone.h:
            problems.append({"kind": "out of zone", "ids": [o.id], "what": f"{_label(o)} pokes out of the zone",
                             "box": [round(v) for v in (l, t, r, b)]})
    for i, a in enumerate(members):
        al, at, ar, ab = boxes[a.id]
        for b_ in members[i + 1:]:
            bl, bt, br, bb = boxes[b_.id]
            dx = max(bl - ar, al - br)              # gap between them (negative: they overlap)
            dy = max(bt - ab, at - bb)
            if dx < 0 and dy < 0:
                kind = "overlap"
            elif max(dx, dy) < LAYOUT_GAP:
                kind = "crowded"
            else:
                continue
            problems.append({"kind": kind, "ids": [a.id, b_.id],
                             "what": f"{_label(a)} and {_label(b_)} {'overlap' if kind == 'overlap' else 'nearly touch'}",
                             "boxes": [[round(v) for v in boxes[a.id]], [round(v) for v in boxes[b_.id]]]})
    used = None
    if members:
        ls, ts_, rs, bs = zip(*boxes.values())
        used = [round(min(ls) - zone.x), round(min(ts_) - zone.y), round(max(rs) - zone.x), round(max(bs) - zone.y)]
    return {"zone": zone_info(zone), "objects": len(members), "problems": problems,
            "used": used}          # the box the objects take up, from the zone's top-left


def resize_zone(scene, zone_name, w, h, x=None, y=None):
    """Resize (and optionally move) a Claude zone. Refused if the new box would cut through
    anything — something poking out of it, Claude's or yours — or be smaller than ZONE_MIN."""
    zone = find_zone(scene, zone_name)
    if zone is None:
        raise Refused(f"no Claude zone called {zone_name!r}")
    nx = zone.x if x is None else float(x)
    ny = zone.y if y is None else float(y)
    nw, nh = float(w), float(h)
    if nw < ZONE_MIN or nh < ZONE_MIN:
        raise Refused(f"a zone can't be smaller than {ZONE_MIN} x {ZONE_MIN}")
    cut = []
    for o in scene.objects:
        if o is zone or isinstance(o, canvas.Wall) or o.center is None:
            continue
        l, t, r, b = _bounds(o)
        touches = l < nx + nw and r > nx and t < ny + nh and b > ny
        inside = l >= nx and t >= ny and r <= nx + nw and b <= ny + nh
        if touches and not inside:
            cut.append(_label(o))
    if cut:
        raise Refused("that box would cut through " + ", ".join(cut[:6]) + (" …" if len(cut) > 6 else ""))
    zone.x, zone.y, zone.w, zone.h = nx, ny, nw, nh
    scene.save()
    return zone


# --- answering -----------------------------------------------------------------------------
def answer(scene, msg):
    """The reply body for an agent's request (without op / seq)."""
    op = msg.get("op")
    try:
        if op == "zones":
            return {"ok": True, "zones": zones(scene)}
        if op == "catalog":
            return {"ok": True, "types": catalog(msg.get("type"))}
        if op == "query":
            return {"ok": True, "objects": query(scene, zone=msg.get("zone"), id=msg.get("id"),
                                                 near=msg.get("near"))}
        if op == "check":
            return {"ok": True, **layout_check(scene, msg.get("zone"))}
        if op == "resize_zone":
            zone = resize_zone(scene, msg.get("zone"), msg.get("w"), msg.get("h"), msg.get("x"), msg.get("y"))
            return {"ok": True, "zone": zone_info(zone)}
        changed = change(scene, msg)
        if changed is not None:
            return changed
    except Refused as e:
        return {"ok": False, "refused": True, "error": str(e)}
    except (LookupError, ValueError, TypeError) as e:
        return {"ok": False, "error": str(e)}
    return refuse(op)


def refuse(op):
    return {"ok": False, "refused": True,
            "error": f"'{op}' isn't something an agent can do — it can: {', '.join(AGENT_OPS)}"}
