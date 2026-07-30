"""Small bpy-side helpers to keep builders robust to whatever state the user's
Blender is in when an op lands."""

import bpy


def nodes_mod(obj, name=None):
    """The object's Nodes (geometry-nodes) modifier, or the one called `name`. None when absent.

    Nine copies of this one-liner existed across core and ui, which is a lot of places to hold one
    fact about what "the GN modifier" means: the first NODES modifier in stack order, and by name
    when a subsystem attaches more than one (the snow pass, the Set-Material pass). A named lookup
    that fell back to the first one would be a different function, so `name` is exact.
    """
    if obj is None:
        return None
    return next((m for m in obj.modifiers
                 if m.type == "NODES" and (name is None or m.name == name)), None)


def object_of(name, expect="MESH", *, label=None, required=True):
    """Resolve an object an op named, or raise saying exactly what is wrong.

    Ops bind objects BY NAME across a process line, so a name is the one thing an agent gets wrong
    routinely: a typo, a stale name from an earlier op, or the right name on the wrong kind of
    datablock (a curve where a mesh belongs). Every one of those used to be either a bare KeyError or
    a silent no-op, and a no-op that reports success is the expensive one -- it costs a whole
    generate-and-render cycle before anybody sees an empty frame.

    `required=False` makes an EMPTY name a legitimate "not asked for" (an optional camera), while a
    name that does not resolve still raises. `label` names the role in the message ("emitter" reads
    better than "object" when that is what it is).
    """
    label = label or expect.lower()
    if not name:
        if required:
            raise ValueError(f"no {label} name given")
        return None
    obj = bpy.data.objects.get(name)
    if obj is None:
        raise ValueError(f"no {label} named {name!r} in the scene")
    if expect and obj.type != expect:
        raise ValueError(f"{label} {name!r} is a {obj.type}, not a {expect}")
    return obj


def ensure_object_mode() -> None:
    """Leave Edit/Sculpt/etc. mode so object-level ops behave predictably.

    A no-op headless (already OBJECT) and safe if there's no valid context.
    """
    if bpy.context.mode != "OBJECT":
        try:
            bpy.ops.object.mode_set(mode="OBJECT")
        except RuntimeError:
            pass
