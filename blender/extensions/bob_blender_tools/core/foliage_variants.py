"""Baking a tuned tree into N variants and a LOD ladder (docs/FOLIAGE.md 2.5, 2.6; BobFoliage).

This is the last hop of the track: one authored tree becomes the pool a scatter layer instances, so
a forest is a scatter layer over `BOB_Assets_<Kind>` and not four hundred hand-placed objects.
`core/foliage_build.py` is the layer below (what makes an object a tree); this is what turns one of
those into a stand. bpy-only, imports no ui, reads no PropertyGroup -- the panel operator resolves
its own state and calls in here with plain arguments, exactly as the wind pass established.

**A variant is a LIVE GN object, and that was the phase's one real hazard.** Freezing a variant to a
static mesh is the obvious reading of "bake", and it would have silently deleted the whole of
docs/FOLIAGE.md 2.4: the sway is a `Set Position` driven by Scene Time, so an applied mesh is a tree
stopped at whatever frame it was baked on. Three measurements decided it, and none of them was
settled earlier (which only established that two instances agree at ONE frame, which is phase and not
motion):

- an instanced live-GN tree still MOVES: 1.322786 m between frame 1 and frame 31, the same figure
  its source object moves, so `Collection Info` -> `Instance on Points` re-evaluates it per frame;
- an applied copy moves exactly 0.0 m over the same 30 frames;
- the cost is per VARIANT and FLAT in instance count -- 400 instances of one variant cost 5.79
  ms/frame against 5.87 for 100, and eight variants cost 7.65 ms whether they are instanced 100
  times or 400. One tree alone is 5.67 ms, so eight is not eight times one (32 cores, evaluated in
  parallel). An applied stand costs nothing per frame, because a mesh with no time dependency is
  not re-evaluated at all.

So the price of a stand that moves is a fixed handful of milliseconds that does not grow with the
forest, which is the shape that makes the choice easy. The frozen route still exists and is exactly
what the pack writer does, because glTF cannot carry a node group -- see `write_variant_pack`, which
says so in the manifest rather than leaving someone to find out.

**Variants are SPREAD OUT in the pool, and that is not cosmetic.** A tree's wind phase is read from
its own world location (wind and season, `_tree_phase`), and a pool is authored at the origin, so eight variants
stacked at (0,0,0) share one phase and the whole stand pulses in unison -- measured, two same-seed
variants at the origin differ by 9.54e-07 m, and 40 m apart by 0.7189 m. `Collection Info`'s Reset
Children means the spread costs nothing: an instance still lands on its point (measured, a variant
authored at x=40 instanced on a point at x=25 arrives centred on x=24.97).

**The ladder is a rebuild and the card enlargement is measured, not chosen** -- see `lod_shape` and
`fit_ladder`.
"""

import json
import math
import os

import bpy

from . import assets, foliage_build, gen_assets, proxies
from .geonodes.recipes import foliage as recipe

DEFAULT_VARIANTS = 8        # docs/FOLIAGE.md 2.5: enough that a repeat is not findable in a frame
LOD_LEVELS = (0, 1, 2)

# How far apart consecutive variants are authored inside the pool. Only the phase reads it, so the
# number just has to be incommensurate with the stand spacing an artist is likely to use and large
# enough to matter: `_tree_phase` weights x by 0.7, so 40 m is 28 radians between neighbours.
VARIANT_SPACING = 40.0

# How much wider than LOD0 a rung's crown may get. The card enlargement below has two constraints
# and this is the second; see `fit_ladder` for why the first one alone is not enough.
WIDTH_TOLERANCE = 1.15

# The scratch object every fit probe is built under. ONE name, so the probes share one pair of
# materials instead of leaving a pair behind per rung per species.
_FIT_OBJECT = "BOB_Foliage_Fit"


def lod_shape(params, level):
    """The STRUCTURAL params of one rung of the ladder. Card size is not set here -- `fit_ladder`
    measures it.

    A rebuild, never a decimate, and that is the one thing docs/FOLIAGE.md 2.6 insists on:
    `gen_assets.build_lods` runs a Decimate, which collapses twigs into spikes and destroys the card
    quads outright, whereas rebuilding the same recipe at a lower branch depth is both cheaper and
    better-looking. The recipe is procedural, so another build IS the LOD.

    - **LOD1 drops the last branch level and one profile side.** Measured on the shipped conifer:
      12,642 verts to 3,320, which is 26.3% -- and the level is where nearly all of it comes from,
      since the profile is exactly linear (5 sides to 4 is 1,546 verts of 12,642).
    - **LOD2 is one level on a three-sided tube**, with the trunk and branch resampling capped.
      `Cards` and `L1 Branches` are deliberately left AS AUTHORED, which is not the first rule this
      tried: capping them at 2 and 8 gave a 168-vert conifer holding **7.5%** of its canopy area,
      which at distance is a forest you can see through, and that pop is the exact thing the rung
      exists to prevent. Leaving them alone costs 490 verts and holds 29.8%, four times the canopy
      for three times the vertices, and it is also the simpler rule.

    A rung that comes back identical to LOD0 is a species already at the floor (`grass_tuft` is one
    level on a 3-sided profile before anything is dropped); `fit_ladder` drops it rather than
    shipping a second copy of LOD0 under a name that promises it is cheaper.
    """
    p = dict(params)
    if level <= 0:
        return p
    if level == 1:
        p["levels"] = max(1, int(p.get("levels", 3)) - 1)
        p["profile_segments"] = max(3, int(p.get("profile_segments", 6)) - 1)
        return p
    p["levels"] = 1
    p["profile_segments"] = 3
    p["segments"] = min(int(p.get("segments", 14)), 8)
    p["branch_segments"] = min(int(p.get("branch_segments", 6)), 3)
    return p


class evaluable:
    """Make an object reachable by the depsgraph for the duration of a `with` block.

    **A baked variant is not evaluated where it lives, and that is a trap with no symptom.**
    `BOB_Assets_<Kind>` is deliberately not linked to the scene -- the pool shows up only as
    scattered instances -- and an object outside the view layer is not evaluated at all, so
    `obj.evaluated_get(depsgraph).to_mesh()` on a pooled variant returns an EMPTY mesh rather than
    raising. Measured the first time this module ran end to end: four freshly baked variants
    reported 0 verts each and the pack writer exported four meshes with no primitives, having said
    nothing at all.

    Instancing is unaffected, which is why nothing else caught it: `Collection Info` depends on the
    object, so the depsgraph pulls it in and the stand renders correctly (25,284 verts, measured).
    Only a DIRECT read of a pooled object needs this.
    """

    def __init__(self, obj):
        self.obj = obj
        self.linked = False

    def __enter__(self):
        scene = bpy.context.scene
        if scene is not None and self.obj.name not in scene.collection.objects:
            in_layer = any(self.obj.name in c.all_objects for c in scene.collection.children_recursive)
            if not in_layer and self.obj.name not in scene.objects:
                scene.collection.objects.link(self.obj)
                self.linked = True
                bpy.context.view_layer.update()
        return self.obj

    def __exit__(self, *exc):
        if self.linked:
            bpy.context.scene.collection.objects.unlink(self.obj)
            self.linked = False
        return False


def measure(obj):
    """What a rung is judged on: verts, faces, cards, total card AREA, and the silhouette.

    The card area is the number the ladder is fitted against, and it is a real polygon area off the
    evaluated mesh rather than `cards * size^2 * width` arithmetic -- the droop and the spread tilt
    every card, so the formula and the mesh disagree, and only one of them is what a camera sees.
    """
    with evaluable(obj):
        return _measure_linked(obj)


def _measure_linked(obj):
    ev = obj.evaluated_get(bpy.context.evaluated_depsgraph_get())
    mesh = ev.to_mesh()
    flag = mesh.attributes.get("bbt_fol_leaf")
    faces = [i for i, d in enumerate(flag.data) if d.value] if flag else []
    out = {"verts": len(mesh.vertices), "faces": len(mesh.polygons), "cards": len(faces),
           "area": float(sum(mesh.polygons[i].area for i in faces))}
    if mesh.vertices:
        xs = [v.co.x for v in mesh.vertices]
        ys = [v.co.y for v in mesh.vertices]
        zs = [v.co.z for v in mesh.vertices]
        out.update(width=max(max(xs) - min(xs), max(ys) - min(ys)),
                   height=max(zs) - min(zs), base_z=min(zs))
    else:
        out.update(width=0.0, height=0.0, base_z=0.0)
    ev.to_mesh_clear()
    return out


def _probe(params):
    """Build the scratch tree at these params and measure it. Not left behind."""
    from .dispatch import apply_op

    apply_op({"op": "build_geonodes", "recipe": "foliage", "name": _FIT_OBJECT,
              "params": dict(params), "reset": True})
    obj = bpy.data.objects.get(_FIT_OBJECT)
    return measure(obj) if obj is not None else None


def _drop_probe():
    obj = bpy.data.objects.get(_FIT_OBJECT)
    if obj is None:
        return
    group = bpy.data.node_groups.get(_FIT_OBJECT)
    bpy.data.objects.remove(obj, do_unlink=True)
    if group is not None and group.users == 0:
        bpy.data.node_groups.remove(group)
    for mat in list(bpy.data.materials):
        if _FIT_OBJECT in mat.name and mat.users == 0:
            bpy.data.materials.remove(mat)


def fit_ladder(params, levels=LOD_LEVELS):
    """[(level, params)] for the ladder, with each rung's `Card Size` MEASURED rather than chosen.

    Dropping a branch level removes most of the tips, and a card grows on a tip, so a plain
    `levels-1` rebuild thins the canopy to a fraction of its coverage -- which is precisely the thing
    a LOD must not change, because coverage at distance IS the tree. The compensation is derivable:
    total card area goes as the square of `Card Size`, so scaling by sqrt(area_LOD0 / area_rung)
    restores it exactly, for any species, with no per-species constant to tune or to drift.

    That rule alone is right at LOD1 and unusable at LOD2. Holding the conifer's 447 m2 of canopy on
    the 18 cards the first LOD2 rule left would need each card 7.4 m across, and the tree comes back
    **253% as wide** -- a distant conifer that is suddenly a sphere. So the enlargement is also
    capped at the point where the crown reaches `WIDTH_TOLERANCE` of LOD0's, which needs no third
    measurement: crown width is very nearly linear in card size, so the two builds already taken
    solve it.

    Measured on the four shipped species, as (verts, canopy area, crown width) against LOD0:

        conifer     LOD1  3,320  26.3%   100.0%  113.7%      LOD2   490   3.9%   29.8%  115.0%
        broadleaf   LOD1  1,740  22.0%   100.0%  107.2%      LOD2   218   2.8%  100.0%  114.1%
        shrub       LOD1    364  19.7%    91.5%  115.0%      LOD2   270  14.6%   91.5%  115.0%
        grass_tuft  LOD1   (already at the floor)            LOD2   156  52.0%   95.6%  115.0%

    The conifer's 29.8% at LOD2 is the honest limit of a rung with no impostor bake behind it: a
    narrow crown of 1,228 small cards cannot be carried by 76 large ones without becoming a sphere,
    and the width is the constraint that must win. It is recorded rather than hidden.
    """
    base_size = float(params.get("card_size", 0.7))
    out = [(0, dict(lod_shape(params, 0)))]
    try:
        zero = _probe(out[0][1])
        if zero is None:
            return out
        for level in levels:
            if level <= 0:
                continue
            shape = lod_shape(params, level)
            if shape == out[0][1]:
                continue    # already at the floor: a second copy of LOD0 is not a LOD
            at_one = _probe(dict(shape, card_size=base_size))
            if at_one is None or not at_one["cards"] or at_one["area"] <= 0.0:
                out.append((level, dict(shape, card_size=base_size)))
                continue
            factor = math.sqrt(zero["area"] / at_one["area"])
            limit = zero["width"] * WIDTH_TOLERANCE
            if factor > 1.0:
                wide = _probe(dict(shape, card_size=base_size * factor))
                if wide is not None and wide["width"] > limit and wide["width"] > at_one["width"]:
                    slope = (wide["width"] - at_one["width"]) / (factor - 1.0)
                    factor = max(1.0, min(factor, 1.0 + (limit - at_one["width"]) / slope))
            out.append((level, dict(shape, card_size=base_size * factor)))
    finally:
        _drop_probe()
    return out


def variant_params(tree, overrides=None):
    """Everything needed to rebuild THIS tree: its stamped build params plus its TUNED live knobs.

    Both halves are load-bearing and they come from different places. The structural params are
    Python arguments to the recipe and survive only on the object's own stamp
    (`foliage_build.BUILD_STAMP`); the live knobs are on the modifier and are the whole point of the
    panel. Baking from the species preset alone -- which is what `foliage_build.build_params`
    returns and what the obvious implementation would have used -- would discard every slider the
    artist moved and hand back eight variants of a tree nobody authored.

    The knobs are read through `recipe.param_socket`, so the two vocabularies are tied together in
    the recipe that owns them both and a renamed socket is a gate failure rather than a silent drop.
    `Wind` and `Wind Direction` are not among them by construction: they carry no param key because
    they belong to the world, and a variant picks the live world's wind up at build time anyway
    (`_env_wind`) and keeps it through the applier after that.
    """
    params = foliage_build.build_params(tree)
    for key in assets.FOLIAGE_PARAM_KEYS:
        socket = recipe.param_socket(key)
        if socket is None:
            continue
        inp = foliage_build.live_input(tree, socket)
        if inp is None:
            continue
        try:
            params[key] = inp.value
        except (AttributeError, TypeError):
            continue
    if overrides:
        params.update({k: v for k, v in overrides.items() if v is not None})
    return params


def _tree_materials(obj):
    """(bark, card) as the object's OWN graph assigns them, in Set Material order."""
    mod = next((m for m in obj.modifiers if m.type == "NODES"), None)
    if mod is None or mod.node_group is None:
        return []
    return [n.inputs["Material"].default_value
            for n in mod.node_group.nodes if n.bl_idname == "GeometryNodeSetMaterial"]


def _share_materials(obj, wanted):
    """Point this variant's Set Material nodes at the SOURCE tree's materials and bin its own.

    The recipe names a tree's two BobShaders after its node group, so eight variants at three rungs
    would arrive with 48 materials, each with its own copy of the bark set's image nodes -- and
    retuning the bark of a species would then be twenty-four edits, which nobody would do, so a
    stand would drift away from the tree it was baked from. They are the same tree at the same
    seed's worth of difference; they wear one pair.
    """
    mod = next((m for m in obj.modifiers if m.type == "NODES"), None)
    if mod is None or mod.node_group is None or not wanted:
        return 0
    dropped = []
    nodes = [n for n in mod.node_group.nodes if n.bl_idname == "GeometryNodeSetMaterial"]
    for node, mat in zip(nodes, wanted):
        own = node.inputs["Material"].default_value
        if own is not None and own is not mat:
            dropped.append(own)
        node.inputs["Material"].default_value = mat
    gone = 0
    for mat in dropped:
        if mat.users == 0:
            bpy.data.materials.remove(mat)
            gone += 1
    return gone


def lod_collection():
    """`BOB_Foliage_LODs`, created UNLINKED, the way an asset pool is.

    Separate from the scatter pool for `gen_assets.LOD_COLLECTION`'s reason: a GN instancer takes
    the WHOLE collection, so a lower rung left in `BOB_Assets_<Kind>` would scatter three copies of
    every variant at three budgets. Unlinked rather than linked-and-excluded, which is where this
    parts company with `gen_assets`: these rungs are live GN objects, and an unlinked collection is
    not in the scene at all, so a ladder nobody is pointing a layer at is not evaluated and costs
    nothing per frame. `proxies.collection` is the closer precedent anyway -- both are asset pools
    that exist to be instanced, not to be looked at.
    """
    coll = bpy.data.collections.get(foliage_build.LOD_COLL)
    return coll if coll is not None else bpy.data.collections.new(foliage_build.LOD_COLL)


def _file(obj, coll):
    for c in list(obj.users_collection):
        c.objects.unlink(obj)
    coll.objects.link(obj)


def variant_kind(tree, kind=None):
    """Which `BOB_Assets_<Kind>` this tree's variants join: the caller's, else its species' own.

    Per-species and not per-bake (docs/FOLIAGE.md 4.3), so a pine goes to Trees and a shrub authored
    beside it goes to Plants with nothing to set.
    """
    if kind:
        return kind
    spec = assets.foliage_species(foliage_build.species_of(tree))
    got = str((spec.get("meta") or {}).get("kind", "")) if spec else ""
    return got if got in assets.FOLIAGE_KINDS else "trees"


def variant_stem(tree):
    """The name every variant of this tree is built from: the TREE's name, not its species'.

    The species name reads better in a pool and is wrong. Two conifers tuned differently are two
    trees, and a species stem would give them the same variant names -- so baking the second would
    silently wipe the first's eight variants out of the pool that a scatter layer was already
    instancing. The object name is unique by construction and stable across re-presses, and for the
    ordinary single-tree case it IS the species name, because that is what Add named it.
    """
    return tree.name.replace(" ", "_").replace(".", "_")


def clear_variants(stem, kind):
    """Remove any earlier bake of this stem, so re-pressing refreshes instead of doubling the pool.

    `add_layer(reuse=True)` is the same rule one capability over: a second press of a build button
    must not silently double what a scatter layer instances.
    """
    gone = 0
    for coll in (proxies.collection(kind), lod_collection()):
        for obj in list(coll.objects):
            if obj.name.split(".")[0].startswith(stem + "_v"):
                group = bpy.data.node_groups.get(obj.name)
                bpy.data.objects.remove(obj, do_unlink=True)
                if group is not None and group.users == 0:
                    bpy.data.node_groups.remove(group)
                gone += 1
    return gone


def make_variants(tree, *, count=DEFAULT_VARIANTS, kind=None, levels=LOD_LEVELS, scene=None,
                  overrides=None, seed_base=1000, pack_dir=None):
    """Bake `count` seeds of `tree` into `BOB_Assets_<Kind>` as live GN objects. Returns a report.

    The whole of the variant pass's first half. Each variant is the SAME tree at a different `Seed` -- the tuned
    knobs and the structural choices come off the source object, not off its species preset -- so a
    stand is the tree the artist authored, eight times, rather than eight of the preset it started
    from.

    `levels` is the LOD ladder: rung 0 lands in the scatter pool and the rest in `BOB_Foliage_LODs`,
    which is excluded from the view layer and therefore costs nothing until a layer points at it.
    `pack_dir` additionally writes the bake out as portable assets (`write_variant_pack`), which is
    the only place anything is frozen.
    """
    scene = scene or bpy.context.scene
    if tree is None or not foliage_build.is_foliage(tree):
        raise ValueError("Make Variants needs a BobFoliage tree")
    count = max(1, int(count))
    kind = variant_kind(tree, kind)
    stem = variant_stem(tree)
    base = variant_params(tree, overrides)
    shared = _tree_materials(tree)

    replaced = clear_variants(stem, kind)
    ladder = fit_ladder(base, levels)
    pool, lods = proxies.collection(kind), lod_collection()

    species = foliage_build.species_of(tree)
    made, report_lods, dropped = [], {}, 0
    for i in range(count):
        # Spread along +X so each variant reads a different `_tree_phase`, and every rung of one
        # variant shares that variant's place so its ladder does not swap phase as it switches.
        where = (i * VARIANT_SPACING, 0.0, 0.0)
        seed = int(seed_base) + i
        for level, rung in ladder:
            name = f"{stem}_v{i + 1:02d}_LOD{level}"
            obj = foliage_build.grow(name, dict(rung, seed=seed), species=species,
                                     scene=scene, location=where, collection=False)
            if obj is None:
                continue
            dropped += _share_materials(obj, shared)
            _file(obj, pool if level == 0 else lods)
            if level == 0:
                made.append(obj)
            report_lods.setdefault(level, []).append(measure(obj)["verts"])
    foliage_build.apply_wind(scene)

    report = {"kind": kind, "collection": pool.name, "stem": stem, "count": len(made),
              "variants": [o.name for o in made], "replaced": replaced,
              "lods": sorted(report_lods), "materials_shared": dropped,
              "lod_verts": {level: verts[0] for level, verts in sorted(report_lods.items())},
              "card_size": {level: round(float(rung.get("card_size", 0.0)), 4)
                            for level, rung in ladder}}
    if pack_dir:
        report["pack"] = write_variant_pack(tree, made, pack_dir, kind=kind, stem=stem,
                                            ladder=ladder, seed_base=seed_base)
    return report


# -- The narrow pack writer ------------------------------------------------------------------
# What `gen_assets.finish_asset` would have done to a tree, and why none of it applies: it bakes
# dense-to-low (a procedural tree has no dense version), decimates (which is the one thing
# docs/FOLIAGE.md 2.6 forbids -- it spikes the twigs and destroys the card quads), Smart-UV-unwraps
# (the bark UV is metres-based and measured, and the card UV indexes an atlas cell, so an unwrap
# would throw both away) and converts to a BobShader (the tree already has two). Confirmed before
# building rather than after, as the open question asked: what a variant actually needs is the last
# three lines of that function, which is what this is.
def _freeze(obj, name):
    """A real mesh object from a live GN one, at the current frame. The frozen copy keeps the
    12,642 verts, the UV layer and both materials -- measured, so this loses nothing but time.

    Through `evaluable`, because the source is in an unlinked pool by the time this runs and a
    pooled object evaluates to an empty mesh without a word."""
    with evaluable(obj):
        dg = bpy.context.evaluated_depsgraph_get()
        mesh = bpy.data.meshes.new_from_object(obj.evaluated_get(dg))
    mesh.name = name
    copy = bpy.data.objects.new(name, mesh)
    bpy.context.scene.collection.objects.link(copy)
    copy.matrix_world = obj.matrix_world.copy()
    return copy


def _export_materials(copy, params, stem):
    """Swap the frozen copy's two BobShaders for plain Principleds built from the same sets.

    Two reasons, and the second one is not optional. glTF carries a Principled and nothing else, so
    the wrapper's season layer and its translucency lobe cannot travel whatever this does -- the
    export would flatten them anyway, and building the flat material here means the packed asset is
    what it says it is rather than a silent reduction.

    And **the glTF exporter segfaults on the card material.** Isolated: exporting the frozen mesh
    with both BobShaders exits 139 at teardown, with the bark material alone exits 0, with the card
    material alone exits 139, and stripping the card material's image nodes does not help -- so it
    is the Mix Shader chain (`_wire_translucency` mattes a Translucent against a Transparent and
    mixes that into the Principled) that the exporter's tree walk cannot survive. A gate that
    crashed after printing its verdict would read as a clean run to an exit code, which is exactly
    how one generation gate hid a crash for three releases (docs/GENERATION.md), so this is a fix and
    not a
    workaround.

    Slots are replaced IN PLACE and slot 0 (the base mesh's empty implicit slot, which no face
    references) is left alone: `material_index` is an index, and re-ordering the list would put bark
    on the leaves.
    """
    from .geonodes.recipes import foliage as fol

    # The atlas goes through the recipe's own resolver, not straight off the params. A species may
    # name a GENERATED atlas that this machine has not generated, and the recipe falls back to the
    # shipped block-out one for it (`_atlas_set`) -- so reading the raw name here would build the
    # export material from a set that resolves nothing, and a card with no cutout exports as an
    # opaque rectangle while the live tree beside it is cut out correctly. Bark needs no such call:
    # an unresolved bark set is a solid tint on both sides, which is the same answer.
    sets = ((str(params.get("bark_set", "")), "Bark"),
            (fol._atlas_set(params), "Leaf"))
    made = []
    for i, slot in enumerate(copy.material_slots):
        if slot.material is None:
            continue
        which = 1 if "Leaf" in slot.material.name else 0
        set_name, label = sets[which]
        name = f"M_{stem}_export_{label}"
        mat = bpy.data.materials.get(name)
        if mat is None:
            mat = gen_assets.baked_material(assets.texture_set_maps(set_name) or {}, name)
            if which == 1:
                gen_assets.cutout_render_method(mat)
        copy.data.materials[i] = mat
        made.append(mat)
    return made


def write_variant_pack(tree, variants, pack_dir, *, kind, stem, ladder, seed_base=1000):
    """Write the bake into a pack as ordinary generated assets. Returns [(name, file)].

    Reuses exactly three things from `gen_assets` -- `origin_to_base`, `unique_asset_name` /
    `generated_dir`, and `write_manifest_entry` / `write_sidecar` -- and nothing else, which is the
    answer to the open question at the end of docs/FOLIAGE.md 5.

    **A packed variant is FROZEN, and the entry says so.** glTF carries meshes and PBR materials; it
    cannot carry a node group, so the export is the evaluated mesh at the current frame and the
    round trip loses the two things the wind pass built -- the wind (an applied mesh moved
    0.0 m over 30 frames, measured) and the leaf shader's season and translucency, which come back
    as a plain Principled. The round trip also splits vertices at the UV seams: 12,642 out, 14,188
    back, which is glTF's own normal/UV split and not a change to the mesh.

    So every entry carries a `foliage` block naming the species, the seed and the rung's params,
    which makes the frozen mesh the FALLBACK rather than the record: a Bob file that has the species
    preset can regrow the exact variant, alive, from two numbers. That is strictly better than the
    GLB and costs one dict.
    """
    pack_dir = str(pack_dir)
    out_dir = gen_assets.generated_dir(pack_dir, kind)
    species = foliage_build.species_of(tree)
    rung_params = dict(ladder)
    written = []
    for i, lod0 in enumerate(variants):
        chain, frozen = [], []
        for level, _rung in ladder:
            live = bpy.data.objects.get(f"{stem}_v{i + 1:02d}_LOD{level}")
            if live is None:
                continue
            copy = _freeze(live, f"{stem}_v{i + 1:02d}_LOD{level}_frozen")
            _export_materials(copy, rung_params.get(level, {}), stem)
            gen_assets.origin_to_base(copy)
            chain.append((level, copy))
            frozen.append(copy)
        if not chain:
            continue
        name = gen_assets.unique_asset_name(pack_dir, kind, f"{stem.lower()}_v{i + 1:02d}")
        for obj in bpy.data.objects:
            obj.select_set(obj in frozen)
        bpy.context.view_layer.objects.active = frozen[0]
        glb = os.path.join(out_dir, name + ".glb")
        bpy.ops.export_scene.gltf(filepath=glb, export_format="GLB", use_selection=True)

        lo, hi = gen_assets.bbox_world(frozen[0])
        entry = {"file": f"{kind}/{name}.glb", "height_m": round(hi[2] - lo[2], 4),
                 "lod": [level for level, _o in chain[1:]], "origin": "base",
                 "faces": gen_assets.face_count(frozen[0]),
                 # The record, as opposed to the fallback: two numbers regrow this variant alive.
                 "foliage": {"species": species, "seed": int(seed_base) + i,
                             "params": {str(level): rung_params[level] for level, _o in chain}}}
        gen_assets.write_manifest_entry(pack_dir, kind, entry)
        gen_assets.write_sidecar(os.path.join(out_dir, name + ".json"), {
            "file": entry["file"], "kind": kind, "origin": "base",
            "generator": "BobFoliage", "recipe": "foliage", "species": species,
            "seed": entry["foliage"]["seed"], "lod": entry["lod"],
            "frozen": "the mesh is evaluated at one frame: the wind and the leaf season live in the "
                      "recipe and the shader, and glTF carries neither. Regrow from species+seed to "
                      "get them back.",
            "license": "CC0", "license_note": "Procedural geometry; the textures follow their sets."})
        written.append((name, glb))
        for obj in frozen:
            bpy.data.objects.remove(obj, do_unlink=True)
    return written


def regrow(entry, name, *, scene=None, location=None):
    """Rebuild a packed variant ALIVE from its manifest entry's `foliage` block, or None.

    The other side of `write_variant_pack`'s argument: a variant is a species and a seed, so a file
    that can resolve the species does not need the frozen mesh at all.
    """
    block = (entry or {}).get("foliage") or {}
    species = str(block.get("species", ""))
    spec = assets.foliage_species(species)
    if not spec:
        return None
    params = dict(spec["params"])
    rungs = block.get("params") or {}
    params.update(rungs.get("0") or rungs.get(0) or {})
    params["seed"] = int(block.get("seed", 0))
    return foliage_build.grow(name, params, species=species, scene=scene, location=location)


def stand_report(host):
    """(instances, source objects) a scatter host is drawing, off the dependency graph.

    What "a real stand at a real density" reduces to as a number, and the reason it is read from the
    depsgraph rather than from the layer's own density knob: the knob is what was asked for and this
    is what arrived, and every mask in the scatter recipe sits between the two.
    """
    dg = bpy.context.evaluated_depsgraph_get()
    total, sources = 0, set()
    for inst in dg.object_instances:
        if not inst.is_instance or inst.parent is None:
            continue
        if inst.parent.original.name != host.name:
            continue
        total += 1
        if inst.object is not None:
            sources.add(inst.object.original.name)
    return total, sorted(sources)


def variant_summary(kind):
    """One line per pooled variant, for an operator report and for the gate's own printout."""
    pool = bpy.data.collections.get(f"BOB_Assets_{kind.capitalize()}")
    if pool is None:
        return []
    return [(o.name, measure(o)["verts"]) for o in sorted(pool.objects, key=lambda o: o.name)
            if foliage_build.is_foliage(o)]


def manifest_variants(pack_dir, kind):
    """The `foliage` entries a pack's generated manifest carries for this kind."""
    path = os.path.join(gen_assets.generated_dir(str(pack_dir)), "manifest.json")
    try:
        with open(path) as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return []
    models = (data or {}).get("models") or {}
    return [e for e in models.get(kind, []) if isinstance(e, dict) and e.get("foliage")]
