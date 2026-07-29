"""Geometry-node recipes and the build_geonodes entry point.

Layers: scaffold (group plumbing), blocks (composable sub-graphs), recipes
(named compositions), place (object or library placement). build_geonodes looks
up a recipe, builds the node group, and places it.
"""

import bpy

from .. import util
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
    return obj, util.nodes_mod(obj)


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


# A heightmap_terrain build's params, recorded on the object as `bbt_*` custom props so everything
# downstream can READ the numbers the terrain was actually built with instead of being handed them
# again. (op param -> object prop, default). Two things went wrong without this: `drape_curve` had
# to be passed the same heightmap/size/height/sea_level by hand with nothing checking they matched,
# and `_has_bake` read False, so `curve_build` carved at the curve's own Z and cut a trench through
# rising ground. The panel bake has always stamped these; the op is now the same.
_TERRAIN_STAMP = (("heightmap", "bbt_heightmap", ""),
                  ("size", "bbt_terrain_size", 90.0),
                  ("resolution", "bbt_terrain_res", 256),
                  ("height", "bbt_terrain_height", 22.0),
                  ("sea_level", "bbt_terrain_sea", 0.22))


def _stamp_terrain_params(obj, recipe_name, params):
    """Record a heightmap_terrain build's params on the object. A no-op for every other recipe."""
    if obj is None or recipe_name != "heightmap_terrain":
        return
    for key, prop, default in _TERRAIN_STAMP:
        val = params.get(key, default)
        obj[prop] = str(val) if prop == "bbt_heightmap" else (
            int(val) if isinstance(default, int) else float(val))


def _keep_set_material_last(obj):
    """Move the Set-Material modifier back to the end of the stack.

    A GN-generated mesh ignores its material slot, so a terrain shades through a BOB_SetMat
    modifier that must run AFTER the recipe that generates the geometry. A rebuild appends a fresh
    recipe modifier and then moves it back to the recipe's old index -- which, on a terrain shaded
    before the rebuild, is BEHIND the Set-Material modifier, leaving the material applied to
    geometry that is then replaced. That is the "rebuilt with reset:true came back unshaded" defect:
    nothing was lost, the stack order was wrong. Idempotent and a no-op when there is no such
    modifier.
    """
    if obj is None:
        return
    from ..materials import SET_MATERIAL_MOD

    mod = next((m for m in obj.modifiers if m.name == SET_MATERIAL_MOD), None)
    if mod is None:
        return
    i = list(obj.modifiers).index(mod)
    last = len(obj.modifiers) - 1
    if i != last:
        obj.modifiers.move(i, last)


def _result(created, info):
    """The build_geonodes op result, carrying any warnings the recipe recorded about its params.

    A recipe binds objects and collections BY NAME, and a name that resolves to nothing used to be
    silent: the layer built, reported success, and scattered nothing. `recipes.warn` collects those
    and they surface here, in `info` for a human and in `data` for an agent.
    """
    warnings = recipes.drain_warnings()
    if warnings:
        info = f"{info} -- {'; '.join(warnings)}"
    return {"op": "build_geonodes", "created": created, "info": info,
            "data": {"warnings": warnings} if warnings else {}}


def build_geonodes(op: dict) -> dict:
    recipe_name = op.get("recipe", "wave_grid")
    recipes.drain_warnings()  # discard anything a previous build left behind
    build = recipes.get(recipe_name)
    if build is None:
        raise ValueError(
            f"unknown geonodes recipe: {recipe_name!r} (have: {recipes.names()})"
        )

    params = op.get("params", {})
    name = op.get("name") or recipe_name
    target = op.get("target", "new_object")
    reset = op.get("reset", False)
    # Where the object goes, and which collection it joins. Both live in `params` rather than being
    # op fields so they need no contract change, and both are honoured on a rebuild as well as on a
    # first build: an op list that says where a building stands has to put it there every time it is
    # replayed, or the second run quietly keeps wherever the first one left it. See `place`.
    location = params.get("location")
    collection = params.get("collection")

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
            # Preserve the modifier's position in the stack. modifiers.new appends to
            # the end, so on an object that carries a later modifier (e.g. a terrain
            # with the BOB_Snow coverage pass after it) a naive remove+new would reorder
            # the stack and evaluate this modifier last, breaking downstream passes that
            # depend on running after it. Move the fresh modifier back to the old index.
            old_index = list(obj.modifiers).index(mod)
            obj.modifiers.remove(mod)
            new_mod = obj.modifiers.new(name="GeometryNodes", type="NODES")
            new_mod.node_group = new_ng
            if new_mod != obj.modifiers[old_index]:
                obj.modifiers.move(len(obj.modifiers) - 1, old_index)
            if snap:
                _restore_knobs(new_mod, snap)
            if old.users == 0:
                bpy.data.node_groups.remove(old)
            new_ng.name = old_name  # reclaim the clean name
            # The recipe modifier came back at the OLD index, which on an already-shaded terrain
            # sits behind the Set-Material modifier. Put that back at the end or the rebuild renders
            # grey.
            _keep_set_material_last(obj)
            _stamp_terrain_params(obj, recipe_name, params)
            if location is not None:
                obj.location = tuple(location)
            obj.update_tag()
            info = recipe_name + (" (in place, reset)" if reset else " (in place)")
            # De-dup: the object and its node group usually share a name, so return
            # each once rather than the readable-but-noisy "Terrain, Terrain" pair.
            created = list(dict.fromkeys([obj.name, new_ng.name]))
            return _result(created, info)
        _clear_existing(name)

    ng, out = new_group(name)
    build(ng, out, params)
    created = place(ng, name, target=target, mark_asset=op.get("mark_asset", False),
                    location=location, collection=collection)
    _stamp_terrain_params(bpy.data.objects.get(name), recipe_name, params)
    return _result(created, recipe_name)


def build_geonodes_on_object(obj, recipe_name, mod_name, params, reset=False):
    """Attach a recipe as a Nodes modifier on an existing object, non-destructively.

    Unlike build_geonodes (which spawns its own object), this augments an object that
    already exists: the snow-coverage pass runs as a modifier on the terrain so its
    snow_cover attribute lands on the shaded mesh. A recipe that adds a Geometry INPUT
    socket (like `snow`) receives the object's own geometry from the modifier stack.

    Rebuild in place: if the named modifier already exists its tuned live knobs are
    preserved by socket name (a fresh group and modifier force a clean re-eval, the
    same reason build_geonodes does), so re-pressing Add Snow keeps a tuned slope or
    altitude. Pass reset=True to discard the tuned knobs and reapply the params fresh.
    """
    build = recipes.get(recipe_name)
    if build is None:
        raise ValueError(
            f"unknown geonodes recipe: {recipe_name!r} (have: {recipes.names()})"
        )

    old_mod = next((m for m in obj.modifiers
                    if m.type == "NODES" and m.name == mod_name), None)
    snap = ({} if reset else _snapshot_knobs(old_mod)) \
        if (old_mod is not None and old_mod.node_group) else {}
    group_name = mod_name + "_" + obj.name
    old_group = old_mod.node_group if old_mod is not None else None

    new_ng, out = new_group(group_name)
    build(new_ng, out, params)
    # Preserve the modifier's position in the stack. modifiers.new appends to the end,
    # so on an object carrying a later modifier (e.g. a snow_shell that runs after the
    # snow-coverage pass) a naive remove+new would reorder the stack and evaluate this
    # modifier last, breaking downstream passes that must run after it (the shell would
    # read a snow_cover the coverage pass has not written yet). Mirror build_geonodes and
    # move the fresh modifier back to the old index.
    old_index = list(obj.modifiers).index(old_mod) if old_mod is not None else len(obj.modifiers)
    if old_mod is not None:
        obj.modifiers.remove(old_mod)
    if old_group is not None and old_group.users == 0:
        bpy.data.node_groups.remove(old_group)
    new_ng.name = group_name

    mod = obj.modifiers.new(name=mod_name, type="NODES")
    mod.node_group = new_ng
    if old_index < len(obj.modifiers) and mod != obj.modifiers[old_index]:
        obj.modifiers.move(len(obj.modifiers) - 1, old_index)
    if snap:
        _restore_knobs(mod, snap)
    _keep_set_material_last(obj)  # a GN-generated mesh shades through the last modifier
    obj.update_tag()
    return {"op": "build_geonodes_on_object", "created": [new_ng.name],
            "object": obj.name, "modifier": mod_name, "info": recipe_name}
