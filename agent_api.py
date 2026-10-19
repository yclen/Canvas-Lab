"""agent_api.py — what an agent (Claude, through claude_world.py) may ask the
running world. The world server answers these for a connection that said
"role": "agent" in its hello; each request carries a seq and gets one
{"op": "reply", "seq", "ok": bool, ...} back (an "error" when ok is false).

Milestone 1 is read-only — an agent can look, not touch:
    {"op": "zones"}                        -> {"zones": [zone, ...]}      the Claude zones and their briefs
    {"op": "catalog", "type"?}             -> {"types": [type, ...]}      what can be built, from the classes
    {"op": "query", "zone"? | "id"? | "near"?}  -> {"objects": [object, ...]}
Anything else from an agent is refused (`refuse`).

Where an agent may build later is a Claude zone (canvas.ClaudeZone): inside
one, on what it made there, never lab hardware (things.Device).
"""

import inspect

import canvas
import world_link

AGENT_OPS = ("zones", "catalog", "query")
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
    except (LookupError, ValueError, TypeError) as e:
        return {"ok": False, "error": str(e)}
    return refuse(op)


def refuse(op):
    return {"ok": False, "error": f"'{op}' isn't something an agent can do (yet): it can look — "
                                  f"{', '.join(AGENT_OPS)} — but not change anything"}
