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


def ensure_object_mode() -> None:
    """Leave Edit/Sculpt/etc. mode so object-level ops behave predictably.

    A no-op headless (already OBJECT) and safe if there's no valid context.
    """
    if bpy.context.mode != "OBJECT":
        try:
            bpy.ops.object.mode_set(mode="OBJECT")
        except RuntimeError:
            pass
