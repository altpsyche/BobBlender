"""BobFoliage orchestration, shared by the BobFoliage panel, the Scatter panel's Grow button,
and the world applier (docs/FOLIAGE.md 4.2, 4.3).

The GN `foliage` recipe lives in core/geonodes/recipes/; this module holds the layer above it: what
makes an object a tree, where the authored trees live, how a species preset reaches one, and how the
shared world's wind reaches all of them. `core/scatter_build.py` is the same shape one capability
over, and this follows it deliberately -- one builder per action, called by the panel operators and
by anything else that wants the same result, so a tree grown from the Scatter panel and a tree added
in the BobFoliage panel are the same object built the same way.

bpy-only, and it never imports ui/: the panel operators import THIS module and keep only their
context resolution. That is also what makes every check here runnable in a headless gate, which the
panel's own operators are not.

**No operator reads a PropertyGroup to build a tree** (docs/MCP.md, the known gap every curve op is
on). The functions here take plain arguments and hand plain params to `build_geonodes`; the panel
resolves its own state into those arguments before calling. Panel state that never reaches a recipe
is what keeps this track off the live-bridge-only list.
"""

import json

import bpy

from . import assets
from . import env as bbt_env

# What marks an object as a BobFoliage tree, and how the panel, the wind applier and (later, the
# variant baker all find them. A stamped custom property rather than a scene-level list of pointers:
# a list drifts when an object is deleted or renamed behind it, and a stamp cannot. Written by every
# build path, so a tree grown over MCP is as findable as one added in the panel.
FOLIAGE_STAMP = "bbt_foliage"

# Which species a tree was last loaded from, for the panel's header and for the variant pass's variant naming.
# Empty on a bare `build_geonodes(recipe="foliage")`, which is a legitimate state: a tree tuned by
# hand from the defaults belongs to no species.
SPECIES_STAMP = "bbt_foliage_species"

# The params this tree was last BUILT with, as JSON (variants and LODs). The live knobs live on the modifier and
# need no copy, but the STRUCTURAL ones -- `levels`, `profile_segments`, `bark_set`, `atlas` -- are
# Python arguments to the recipe and are recoverable from nothing afterwards. Without this a tree
# the artist rebuilt at two levels forgot it on the next rebuild (the panel's staged choice was the
# only record, and it is UI state), and a baked variant would come out at the species' depth rather
# than at the tree's. `heightmap_terrain` stamps its own build params for the same reason
# (core/geonodes/__init__.py `_TERRAIN_STAMP`); this is that idiom, kept local to foliage.
BUILD_STAMP = "bbt_foliage_build"

# Where authored trees live. One collection so the panel can list them with a template_list over
# real objects (the Scatter panel's model) instead of a CollectionProperty of pointers that can go
# stale. A tree is not required to be in it -- `is_foliage` keys off the stamp, not the collection --
# so a hero tree dragged into a set-dress collection stays a tree.
FOLIAGE_COLL = "BOB_Foliage"

# The live knobs the world drives. Names must match the recipe's interface (recipes/foliage.py).
_WIND_INPUTS = (("Wind", "wind_strength"), ("Wind Direction", "wind_direction"))


def _apply_op(op):
    """Run one bbmcp op in-process. Imported lazily, as scatter_build does, so the dispatch registry
    can import modules that import this one without a cycle at load."""
    from .dispatch import apply_op

    return apply_op(op)


def is_foliage(obj):
    """True when this object is a BobFoliage tree: stamped, and still carrying a Nodes modifier."""
    return (obj is not None and getattr(obj, "type", None) == "MESH"
            and FOLIAGE_STAMP in obj and any(m.type == "NODES" for m in obj.modifiers))


def foliage_objects(scene=None):
    """Every AUTHORED BobFoliage tree in the scene, in name order. What the panel lists.

    Scene objects only, which is the distinction `wind_targets` exists to bridge: a baked variant
    lives in `BOB_Assets_<Kind>`, and that collection is deliberately not linked to the scene (it
    shows up only as scattered instances), so a variant is not in `scene.objects` at all.
    """
    scene = scene or bpy.context.scene
    if scene is None:
        return []
    pools = set(pool_names())
    return sorted((o for o in scene.objects if is_foliage(o)
                   and not pools.intersection(c.name for c in o.users_collection)),
                  key=lambda o: o.name)


# The unlinked collections a baked variant lives in (variants and LODs). `BOB_Assets_<Kind>` is the pool a scatter
# layer instances; `BOB_Foliage_LODs` holds the lower rungs, out of the pool for the reason
# `gen_assets.LOD_COLLECTION` exists -- a GN instancer takes the WHOLE collection, so leaving LOD1
# and LOD2 in the pool would scatter three copies of every variant at three budgets.
LOD_COLL = "BOB_Foliage_LODs"


def pool_names():
    return tuple(f"BOB_Assets_{kind.capitalize()}" for kind in assets.FOLIAGE_KINDS) + (LOD_COLL,)


def pool_objects():
    """Every baked variant, across the asset pools and the LOD collection, in name order."""
    seen = {}
    for name in pool_names():
        coll = bpy.data.collections.get(name)
        if coll is None:
            continue
        for obj in coll.objects:
            if is_foliage(obj):
                seen[obj.name] = obj
    return [seen[k] for k in sorted(seen)]


def wind_targets(scene=None):
    """Every tree the world's wind has to reach: the authored ones AND the baked variants.

    Baking the variants is what makes a stand, and a stand that does not feel the weather is the
    whole of docs/FOLIAGE.md 2.4 undone at the last step. The pools are not in `scene.objects`, so
    walking the scene alone reaches the hero tree in the viewport and none of the four hundred
    instances behind it -- which renders, and looks exactly like wind that works.
    """
    trees = foliage_objects(scene)
    known = {o.name for o in trees}
    return trees + [o for o in pool_objects() if o.name not in known]


def stamp(obj, species="", params=None):
    """Mark an object as a BobFoliage tree, and record the species and the params it was built with.

    `params` is written only when given, so a caller that only wants to re-stamp the species (a
    rebuild) does not erase the structural record with an empty one.
    """
    if obj is None:
        return
    obj[FOLIAGE_STAMP] = 1
    obj[SPECIES_STAMP] = str(species or "")
    if params is not None:
        obj[BUILD_STAMP] = json.dumps({k: v for k, v in params.items()}, sort_keys=True)


def species_of(obj):
    return str(obj.get(SPECIES_STAMP, "")) if obj is not None else ""


def built_params(obj):
    """The params this tree was last built with, off its own stamp ({} when it carries none)."""
    try:
        got = json.loads(str(obj.get(BUILD_STAMP, "") or "{}"))
    except (ValueError, TypeError):
        return {}
    return got if isinstance(got, dict) else {}


def foliage_collection(scene=None, create=True):
    """The BOB_Foliage collection, created and scene-linked on demand (None when create is off)."""
    scene = scene or bpy.context.scene
    coll = bpy.data.collections.get(FOLIAGE_COLL)
    if coll is None:
        if not create:
            return None
        coll = bpy.data.collections.new(FOLIAGE_COLL)
    if scene is not None and not _in_scene(scene.collection, coll):
        scene.collection.children.link(coll)
    return coll


def _in_scene(parent, coll):
    """Is `coll` linked anywhere under `parent`? Nested, because an artist may have filed
    BOB_Foliage inside a set-dress collection and re-linking it at the root would show it twice."""
    if coll.name in parent.children:
        return True
    return any(_in_scene(child, coll) for child in parent.children)


def _move_to_collection(obj, coll):
    for c in list(obj.users_collection):
        c.objects.unlink(obj)
    coll.objects.link(obj)


def unique_name(base):
    name, i = base, 1
    while name in bpy.data.objects:
        i += 1
        name = f"{base}.{i:03d}"
    return name


def grow(name, params, *, species="", scene=None, location=None, collection=True):
    """Build a foliage object and return it, or None if the build produced nothing.

    The one place a tree is created, so every caller gets the same thing: the recipe params go
    straight to `build_geonodes` (no PropertyGroup in sight), the object is stamped so the panel and
    the wind applier can find it, it lands in BOB_Foliage, and it picks the live world's wind up
    immediately rather than at the next slider drag.
    """
    scene = scene or bpy.context.scene
    _apply_op({"op": "build_geonodes", "recipe": "foliage", "name": name,
               "params": dict(params), "reset": True})
    obj = bpy.data.objects.get(name)
    if obj is None:
        return None
    stamp(obj, species, params=params)
    if location is not None:
        obj.location = location
    if collection:
        _move_to_collection(obj, foliage_collection(scene))
    apply_wind(scene, only=obj)
    return obj


def load_species(obj, species, *, scene=None, extra=None):
    """Apply a species preset to an EXISTING tree, in place. Returns the object, or None.

    A preset is params applied to a tree, not a new tree: `build_geonodes` rebuilds in place under
    the same name, so the object datablock, its transform, its collections and anything pointing at
    it all survive. That is the difference between loading a species and growing one, and it is what
    lets an artist try three species on a tree they have already positioned.

    `reset` is on inside `grow`/here on purpose: a species is a complete shape description, so
    loading one must overwrite the knobs the last species left rather than blend with them.
    """
    if obj is None:
        return None
    spec = assets.foliage_species(species)
    if not spec:
        return None
    params = dict(spec["params"])
    if extra:
        params.update(extra)
    _apply_op({"op": "build_geonodes", "recipe": "foliage", "name": obj.name,
               "params": params, "reset": True})
    obj = bpy.data.objects.get(obj.name)
    stamp(obj, species, params=params)
    apply_wind(scene or bpy.context.scene, only=obj)
    return obj


def rebuild(obj, *, overrides=None, scene=None):
    """Rebuild an existing tree with new STRUCTURAL params, keeping its tuned live knobs.

    `reset` is off, which is the whole difference from `load_species`: `build_geonodes` restores
    every live knob by socket name across the rebuild, so changing the branch depth or assigning a
    bark set costs nothing an artist has tuned. The structural params are Python arguments to the
    recipe rather than modifier inputs, so they take effect regardless.
    """
    if obj is None:
        return None
    name = obj.name
    params = build_params(obj, overrides=overrides)
    _apply_op({"op": "build_geonodes", "recipe": "foliage", "name": name, "params": params,
               "reset": False})
    obj = bpy.data.objects.get(name)
    stamp(obj, species_of(obj), params=params)
    apply_wind(scene or bpy.context.scene, only=obj)
    return obj


def build_params(obj, *, species="", overrides=None):
    """The structural params a rebuild of THIS tree needs, read off the object rather than off any
    panel state: its species preset, then what it was last actually built with, then the caller's
    overrides.

    The middle layer is what makes a rebuild idempotent. Without it a tree the artist rebuilt at two
    levels went back to the species' three on the next press, because the panel's staged choice is
    UI state and nothing else recorded it -- and a variant baked from that tree came out a different
    shape from the tree it was baked from. Asking for a species explicitly skips the stamp, since
    loading a species IS the instruction to forget what this tree was.

    The live knobs are deliberately absent. `build_geonodes` restores those by socket name across a
    rebuild (unless reset is asked), so passing them would be a second copy of a value that already
    has a home on the modifier -- the panel-versus-modifier drift `core/scatter_build.py` avoids the
    same way.
    """
    spec = assets.foliage_species(species or species_of(obj))
    params = dict(spec.get("params", {})) if spec else {}
    if not species:
        params.update(built_params(obj))
    if overrides:
        params.update({k: v for k, v in overrides.items() if v is not None})
    return params


# -- The live world feed ----------------------------------------------------------------------
def live_input(obj, socket_name):
    """The modifier input struct for a socket name (it has a live `.value`), or None.

    `mod.properties.inputs.<identifier>.value` is where Blender 5.2 keeps a Nodes modifier's input
    values. Not `mod[identifier]` (a Nodes modifier has no IDProperties and raises TypeError) and
    not the interface `default_value` (which only seeds a fresh bind, so writing it would change
    nothing on an already-bound modifier while looking exactly like it had worked).
    """
    mod = next((m for m in obj.modifiers if m.type == "NODES"), None)
    if mod is None or mod.node_group is None:
        return None
    ident = next((s.identifier for s in mod.node_group.interface.items_tree
                  if getattr(s, "in_out", "") == "INPUT" and s.name == socket_name), None)
    if ident is None:
        return None
    return getattr(mod.properties.inputs, ident, None)


def apply_wind(scene=None, only=None):
    """Push the world's wind onto every foliage tree's live knobs. Returns how many it reached.

    Values, not drivers, and that is the choice worth recording. Firmament drives its wind knobs
    with real drivers (`atmosphere._install_wind_drivers`) because a cloud layer is one object with
    a stable modifier; a stand of trees is N objects that each get a fresh modifier and a fresh set
    of socket identifiers on every structural rebuild, so N drivers would have to be reinstalled by
    every rebuild anyway. Writing values through the world applier costs one pass over the trees on
    a world change, needs no cleanup when a tree is deleted, and is measurable headlessly -- a
    driver's value is only correct on the evaluated copy, which is exactly the kind of thing that
    passes a check and renders wrong.

    A tree therefore holds its last-applied wind when Firmament is absent, which is the standalone
    behaviour the rest of the suite has: the value is real, it is just not being updated.

    It walks `wind_targets` and not the scene, which is the variant pass's correction: a baked variant lives in an
    unlinked `BOB_Assets_<Kind>` and would otherwise never be reached, so the hero tree in the
    viewport would blow and the four hundred instances behind it would stand still.
    """
    scene = scene or bpy.context.scene
    world = bbt_env.get_env(scene)
    if world is None:
        return 0
    targets = [only] if only is not None else wind_targets(scene)
    reached = 0
    for obj in targets:
        if not is_foliage(obj):
            continue
        hit = False
        for socket, field in _WIND_INPUTS:
            inp = live_input(obj, socket)
            if inp is None:
                continue
            inp.value = float(getattr(world, field, 0.0))
            hit = True
        if hit:
            obj.update_tag()
            reached += 1
    return reached


def apply_world_wind(scene):
    """The World-applier hook (`ui/world.register_applier`): re-feed every tree's wind when the
    world state changes, so raising Wind Strength moves a stand with no rebuild and no per-tree
    press. Gated by the one master Live Environment toggle, like every other consumer's applier."""
    if getattr(getattr(scene, "bbt_world", None), "live_env", True):
        apply_wind(scene)
