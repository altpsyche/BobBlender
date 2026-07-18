"""Geometry-node recipes and the build_geonodes entry point.

Layers: scaffold (group plumbing), blocks (composable sub-graphs), recipes
(named compositions), place (object or library placement). build_geonodes looks
up a recipe, builds the node group, and places it.
"""

import bpy

from . import recipes
from .place import place
from .scaffold import new_group


def _clear_existing(name: str):
    """Drop a prior object and orphaned node group of this name.

    Makes a build idempotent: re-running the same named recipe replaces its own
    output instead of piling up name.001 duplicates, and keeps the clean name so
    references by name (a scatter's emitter) still resolve to the fresh build.
    Removing the object first frees the group, which is then removed only if no
    other object still uses it.
    """
    obj = bpy.data.objects.get(name)
    if obj is not None:
        bpy.data.objects.remove(obj, do_unlink=True)
    group = bpy.data.node_groups.get(name)
    if group is not None and group.users == 0:
        bpy.data.node_groups.remove(group)


def _gn_object(name):
    """An existing object of this name plus its Nodes modifier, or (None, None)."""
    obj = bpy.data.objects.get(name)
    if obj is None:
        return None, None
    mod = next((m for m in obj.modifiers if m.type == "NODES"), None)
    return obj, mod


# Structural inputs define the mesh topology, so a rebuild must take them from the
# op (a full-res bake needs the full-res grid), not preserve a stale tuned value.
_STRUCTURAL = {"Size", "Resolution"}


def _input_sockets(ng):
    for item in ng.interface.items_tree:
        if getattr(item, "item_type", None) == "SOCKET" and item.in_out == "INPUT":
            yield item


def _snapshot_knobs(mod):
    """Read tuned live knob values, keyed by socket name.

    In Blender 5.2 a Nodes modifier stores its input values on
    mod.properties.inputs.<identifier>.value (a GeometryNodesModifierInterface),
    not as IDProperties and not on the node group interface default_value. The
    interface default only seeds a fresh bind; editing it post-build does not
    re-evaluate, so a tuned knob lives on the modifier input, and that is what
    must be snapshotted or a rebuild drops the user's live edits.

    Datablock/geometry inputs have no scalar value and are skipped (the recipe
    sets those on nodes). Structural inputs are skipped too, so the rebuild's
    params win for them.
    """
    ng = mod.node_group
    inputs = mod.properties.inputs
    snap = {}
    for item in _input_sockets(ng):
        if item.name in _STRUCTURAL:
            continue
        inp = getattr(inputs, item.identifier, None)
        if inp is None:
            continue
        try:
            value = inp.value
        except (AttributeError, TypeError):
            continue
        if hasattr(value, "__len__") and not isinstance(value, str):
            value = tuple(value)  # copy vectors/colors past the rebuild
        snap[item.name] = value
    return snap


def _restore_knobs(mod, snap):
    ng = mod.node_group
    inputs = mod.properties.inputs
    for item in _input_sockets(ng):
        if item.name in snap:
            inp = getattr(inputs, item.identifier, None)
            if inp is None:
                continue
            try:
                inp.value = snap[item.name]
            except (AttributeError, TypeError, ValueError):
                pass


def build_geonodes(op: dict) -> dict:
    recipe_name = op.get("recipe", "wave_grid")
    build = recipes.get(recipe_name)
    if build is None:
        raise ValueError(
            f"unknown geonodes recipe: {recipe_name!r} (have: {recipes.names()})"
        )

    params = op.get("params", {})
    name = op.get("name") or recipe_name
    target = op.get("target", "new_object")
    reset = op.get("reset", False)

    # Rebuild in place: if a named object with a Nodes modifier already exists,
    # refill its group instead of respawning. The object, its transform, and
    # selection survive. Tuned knobs are preserved by socket name unless reset is
    # asked, in which case the recipe's fresh defaults from params take over.
    if target == "new_object":
        obj, mod = _gn_object(name)
        if obj is not None and mod is not None and mod.node_group is not None:
            old = mod.node_group
            old_name = old.name
            snap = {} if reset else _snapshot_knobs(mod)
            # Build a fresh group and give the object a fresh modifier pointing at
            # it. Reusing the group or the modifier leaves Blender evaluating a
            # stale result (empty geometry, or the old resolution) because it
            # caches the compiled tree and mesh; a new group plus a new modifier
            # forces a clean re-eval. The object, transform, selection, and
            # (restored) knobs still survive. Knobs restore onto the new modifier's
            # inputs after it binds, since that is where a live value lives.
            new_ng, out = new_group(old_name)
            build(new_ng, out, params)
            obj.modifiers.remove(mod)
            new_mod = obj.modifiers.new(name="GeometryNodes", type="NODES")
            new_mod.node_group = new_ng
            if snap:
                _restore_knobs(new_mod, snap)
            if old.users == 0:
                bpy.data.node_groups.remove(old)
            new_ng.name = old_name  # reclaim the clean name
            obj.update_tag()
            info = recipe_name + (" (in place, reset)" if reset else " (in place)")
            return {"op": "build_geonodes", "created": [new_ng.name, obj.name], "info": info}
        _clear_existing(name)

    ng, out = new_group(name)
    build(ng, out, params)
    created = place(ng, name, target=target, mark_asset=op.get("mark_asset", False))
    return {"op": "build_geonodes", "created": created, "info": recipe_name}
