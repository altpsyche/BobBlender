"""Scatter orchestration, shared by the Scatter panel and the biome/MCP path.

The GN `scatter` / `scatter_along` recipes themselves live in core/geonodes/; this module holds the
layer above them: the layer-type presets, the small collection/naming helpers, the structural params,
the build functions (`add_layer`, `rebuild`, `biome_scatter`), the `scatter_layer` op, AND the layer's
own config PropertyGroup. One builder serves the Add-Layer button, the biome scatter and the op, so
what stands a layer up lives in exactly one place.

This module OWNS the `scatter` and `scatter_along` recipes (`geonodes.OWNED_RECIPES`): the generic
`build_geonodes` op refuses them and points at `scatter_layer`, because a layer built through the
generic op recorded no kind and no emitter, which is a mesh the panel cannot list and nothing can
rebuild.

bpy-only, and it never imports ui/: the panel operators import THIS module and keep only their context
resolution (active emitter, UI-state writes, self.report). It also REGISTERS the per-object layer
config, for the reason in `CONFIG_PROP`: a layer is built on paths where no addon is registered, so a
group the panel owned made the typed op fail exactly where the op exists to work. `make_proxies` still
goes through the in-process dispatch, imported lazily so dispatch can register the biome handlers that
import this module without a cycle.
"""

import bpy

from . import util

# Where a layer's structural config lives, and where an emitter points at its scatter collection.
# Both are declared and REGISTERED at the bottom of this module, for the reason
# `foliage_build.CONFIG_PROP` is: this module is the one place a layer is built, and it is built
# without the addon registered -- by a headless gate, and by the `build` MCP tool, which runs
# `core.dispatch` in a bare Blender. Registered by `ui/scatter` instead, the `scatter_layer` op
# raised `AttributeError: 'Object' object has no attribute 'bbt_scatter_coll'` on that path, measured
# on the agent-surface gate, while the raw recipe it replaced worked -- so the typed op would have
# been the less capable of the two.
CONFIG_PROP = "bbt_scatter_layer"
COLL_PROP = "bbt_scatter_coll"

# Whether an along-curve layer orients its instances to the path. One constant, read by the
# PropertyGroup's default AND by `add_layer`, because those two disagreeing means a layer's first
# build and its first rebuild place instances differently -- with nothing saying which is right.
ALONG_ALIGN_DEFAULT = True

# What marks an object as a scatter LAYER, the exact counterpart of `foliage_build.FOLIAGE_STAMP` and
# for the same reason: `Object.bbt_scatter_layer` exists on every object in the file once the addon is
# registered, so a PropertyGroup cannot answer "is this a layer" -- only a custom property that is
# absent by default can. Layer identity used to be "is it inside some emitter's scatter collection",
# which is a fact about a collection rather than about the object, and which an agent-built layer
# never satisfied.
LAYER_STAMP = "bbt_scatter"


def is_layer(obj):
    """True when this object is a Bob scatter layer: stamped, and still carrying a Nodes modifier."""
    return (obj is not None and getattr(obj, "type", None) == "MESH"
            and LAYER_STAMP in obj and util.nodes_mod(obj) is not None)


def layers_of(emitter):
    """Every scatter layer bound to this emitter, in name order. What the panel lists.

    Walks the objects and filters on the STAMP plus the layer's own emitter pointer, rather than
    listing a collection's contents: a layer moved out of the emitter's scatter collection by hand is
    still that emitter's layer, and a layer an agent built was never in the collection at all.
    """
    if emitter is None:
        return []
    return sorted((o for o in bpy.data.objects
                   if is_layer(o) and o.bbt_scatter_layer.emitter == emitter),
                  key=lambda o: o.name)


def emitters():
    """Every mesh that has at least one scatter layer bound to it, in name order.

    What lets the panel show an agent's work with no emitter picked: the scene can be asked which
    meshes are scattered on instead of being told.
    """
    seen = {}
    for obj in bpy.data.objects:
        if not is_layer(obj):
            continue
        emitter = obj.bbt_scatter_layer.emitter
        if emitter is not None:
            seen[emitter.name] = emitter
    return [seen[k] for k in sorted(seen)]


# Layer type presets: a Blender-side dict (no codegen; no second interpreter reads it, unlike the
# heightfield presets). Each seeds the structural config and the initial live-knob values, and
# points assets at BOB_Assets_<Kind>. Icons are standard mesh-add icons, so they are always present.
LAYER_TYPES = {
    "trees": {
        "label": "Trees", "icon": "MESH_CONE", "align": "up",
        "desc": "Upright, sparse; pulls back further from a path",
        "knobs": {"density": 3.0, "distance_min": 1.1, "min_normal_z": 0.6,
                  "min_scale": 0.8, "max_scale": 1.3},
    },
    "rocks": {
        "label": "Rocks", "icon": "MESH_ICOSPHERE", "align": "normal",
        "desc": "Tilted to the surface, allows slopes",
        "knobs": {"density": 2.0, "distance_min": 0.7, "min_normal_z": 0.25,
                  "min_scale": 0.4, "max_scale": 1.2},
    },
    "plants": {
        "label": "Plants", "icon": "MESH_CIRCLE", "align": "normal",
        "desc": "Denser, tilted to the surface, lightly clumped",
        "knobs": {"density": 8.0, "distance_min": 0.35, "min_normal_z": 0.4,
                  "min_scale": 0.6, "max_scale": 1.1,
                  "noise_strength": 0.35, "noise_scale": 0.12, "noise_contrast": 0.5},
    },
    "grass": {
        "label": "Grass", "icon": "MESH_PLANE", "align": "normal",
        "desc": "Dense and small, tilted to the surface, clumped",
        "knobs": {"density": 24.0, "distance_min": 0.12, "min_normal_z": 0.35,
                  "min_scale": 0.5, "max_scale": 1.0,
                  "noise_strength": 0.55, "noise_scale": 0.22, "noise_contrast": 0.6},
    },
    "empty": {
        "label": "Empty", "icon": "DOT", "align": "up",
        "desc": "Recipe defaults, no assets (bring your own collection)",
        "knobs": {},
    },
}


# -- In-process op path -----------------------------------------------------------------------
def _apply_op(op):
    """Run one bbmcp op in-process, the path the panel's build step uses. Imported lazily: the
    dispatch registry imports the biome handlers that import this module, so a top-level import
    here would be a cycle at load."""
    from .dispatch import apply_op

    return apply_op(op)


def layer_recipe(lay):
    """Which recipe a layer builds: along-curve placement, or the surface Poisson scatter.

    Here rather than in the panel because both the panel's rebuild and `add_layer` need it, and a
    layer that picked one recipe when it was added and the other when it was rebuilt is a layer that
    changes what it means on a button press."""
    return "scatter_along" if lay.curve_mode == "along" else "scatter"


def build_recipe(recipe, name, params, *, reset=False):
    """Build a scatter recipe under `name`. The one place this module reaches the recipes it owns.

    `geonodes.build_recipe` and not the `build_geonodes` op: this module owns `scatter` and
    `scatter_along` (`geonodes.OWNED_RECIPES`), so the op refuses them and points at `scatter_layer`.
    `reset` off is the default because the tuned knobs on an existing layer are the artist's work and
    a structural rebuild has no business discarding them; a caller that is REPLACING a layer's whole
    knob set (a gate measuring one configuration after another) asks for it explicitly.
    """
    from .geonodes import build_recipe as build_gn

    return build_gn({"op": "build_geonodes", "recipe": recipe, "name": name, "params": params,
                     "reset": reset})


# -- Naming / collection helpers (shared by panel + biome) -----------------------------------
def _assets_name(kind):
    return f"BOB_Assets_{kind.capitalize()}"


def _coll_in_scene(parent, coll):
    if coll.name in parent.children:
        return True
    return any(_coll_in_scene(child, coll) for child in parent.children)


def _ensure_scatter_coll(emitter, scene):
    """The emitter's scatter collection, created and scene-linked if needed."""
    coll = emitter.bbt_scatter_coll
    if coll is None:
        coll = bpy.data.collections.new(f"{emitter.name} Scatter")
        emitter.bbt_scatter_coll = coll
    if not _coll_in_scene(scene.collection, coll):
        scene.collection.children.link(coll)
    return coll


def _move_to_collection(obj, coll):
    for c in list(obj.users_collection):
        c.objects.unlink(obj)
    coll.objects.link(obj)


def _unique_object_name(base):
    name, i = base, 1
    while name in bpy.data.objects:
        i += 1
        name = f"{base}.{i:03d}"
    return name


def edge_attr_name(curve):
    """The per-curve edge-ring attribute a Verge layer reads for ONE path (BobSplines, the verge band).
    The curve overlay writes the same name; both derive it from the curve's name (resolved at
    build, like the along-curve binding), so a rename is picked up on the next build. Verge needs
    a curve: an unbound layer reads a name nothing writes, so it scatters nothing."""
    return f"bbt_curve_edge_{curve.name}"


def parse_exclude(text):
    """A comma-separated asset-name list as a clean list of names ([] for blank).

    The layer stores it as one string because it is a short list of names typed in a panel field,
    and a CollectionProperty of one-name items would need its own UIList for no gain."""
    return [n.strip() for n in (text or "").split(",") if n.strip()]


def _count_instances(context, objs):
    """Total GN instances parented to any of objs, via the dependency graph."""
    names = {o.name for o in objs}
    dg = context.evaluated_depsgraph_get()
    return sum(1 for i in dg.object_instances
               if i.is_instance and i.parent is not None
               and i.parent.original.name in names)


# -- Asset weathering (bpy.ops-free; mirrors Shaders' Convert, Collection scope) --------------
def _convert_layer_assets(lay, scene=None):
    """Weather a scatter layer's assets: convert every material in its assets collection to a
    BobShader so the instances react to the world and are editable per-layer, then install the env
    feed. Idempotent (bobshade_material skips a material that is already a BobShader). A no-op for
    a layer with no assets set yet. It follows the layer's OWN collection, so a user's imported
    assets are covered too, not only the BOB_Assets_* proxies. Returns the count converted.

    Direct core calls, not the Shaders Convert operator: core must not depend on a ui operator
    being registered (the bridge reload purges core.*, not the addon's ui operators)."""
    coll = getattr(lay, "assets", None)
    if coll is None:
        return 0
    from . import materials, shading

    seen, done = set(), 0
    for o in coll.all_objects:
        if o.type != "MESH":
            continue
        for slot in o.material_slots:
            m = slot.material
            if m is None or m.name in seen:
                continue
            seen.add(m.name)
            if materials.bobshade_material(m):
                done += 1
    shading.feed_env(scene or bpy.context.scene)
    return done


# -- Structural params (panel rebuilds) ------------------------------------------------------
def _build_params(obj):
    """Structural params for a layer rebuild, read ENTIRELY from its object-native config.

    The live knobs are intentionally omitted: build_geonodes restores them from the old modifier
    by socket name, so a structural rebuild keeps the tuned values.

    It used to take the Scene's `bbt_scatter` UI state for the emitter and the camera, and that was a
    real defect rather than untidiness: a layer rebuilt while the scene pointer named a DIFFERENT mesh
    silently re-bound the scatter to that mesh, with nothing in the result saying so. The layer
    records its own emitter now, so a rebuild binds to what this layer actually scatters on -- and a
    layer an agent built is rebuildable at all, which it was not while the answer lived in a panel.
    """
    lay = obj.bbt_scatter_layer
    emitter = lay.emitter.name if lay.emitter is not None else ""
    if lay.curve_mode == "along":
        # scatter_along places instances along the curve, then projects them DOWN onto the emitter
        # so they sit on the terrain following the curve's route (needs both the curve and emitter).
        params = {"align": lay.curve_align, "emitter": emitter}
        if lay.assets is not None:
            params["assets"] = lay.assets.name
        if lay.curve is not None:
            params["curve"] = lay.curve.name
        return params
    params = {"emitter": emitter, "align": lay.align}
    if lay.assets is not None:
        params["assets"] = lay.assets.name
    excluded = parse_exclude(getattr(lay, "assets_exclude", ""))
    if excluded:
        params["assets_exclude"] = excluded
    if lay.vgroup:
        params["vgroup"] = lay.vgroup
    # clear/keep/verge read a terrain curve mask the overlay baked (BobSplines, the scatter mask/the
    # per-role surfaces); no proximity. verge keeps to ONE path's edge ring, so it needs a curve;
    # with none bound it reads a name nothing writes, so the layer scatters nothing (pick a path)
    # rather than covering every verge.
    if lay.curve_mode in ("clear", "keep", "verge"):
        params["curve_mode"] = "clear" if lay.curve_mode == "clear" else "keep"
        if lay.curve_mode == "verge":
            params["curve_attr"] = edge_attr_name(lay.curve) if lay.curve is not None \
                else "bbt_curve_none"
    if lay.camera is not None:
        params["camera"] = lay.camera.name
    return params


# -- Biome params merge ----------------------------------------------------------------------
def _biome_layer_params(kind, cfg):
    """Merge a biome scatter cfg onto the recipe's knob params over the LAYER_TYPES defaults.

    Returns (knobs, align). The biome cfg speaks the manifest vocabulary (density, scale [min,max],
    min_normal_z, max_normal_z, align); LAYER_TYPES fills anything it omits, so a partial recipe
    still builds a sensible layer."""
    spec = LAYER_TYPES[kind]
    knobs = dict(spec["knobs"])
    if "density" in cfg:
        knobs["density"] = cfg["density"]
    sc = cfg.get("scale")
    if isinstance(sc, (list, tuple)) and len(sc) == 2:
        knobs["min_scale"], knobs["max_scale"] = sc[0], sc[1]
    for k in ("min_normal_z", "max_normal_z", "distance_min", "z_offset"):
        if k in cfg:
            knobs[k] = cfg[k]
    return knobs, cfg.get("align", spec["align"])


# -- Builders (shared by the panel operators + the biome handler) ----------------------------
def add_layer(emitter, kind, *, scene, knobs=None, align=None, camera=None,
              coll=None, name=None, reuse=False, convert=True, curve_mode=None,
              curve=None, curve_align=None, assets_exclude=None):
    """Build one scatter layer object on `emitter` and file it in the emitter's scatter collection.

    Ensures the kind's block-out proxy assets (make_proxies is idempotent: it only fills an empty
    collection), builds the layer's recipe, moves the result into the emitter's scatter collection,
    and stamps its object-native structural config. Shared by the panel's Add Layer, by
    biome_scatter, and by the `scatter_layer` op.

    knobs/align default to the kind's LAYER_TYPES preset; pass merged biome values to override.
    reuse rebuilds an existing layer of this kind in place (the recipe build is non-destructive) so
    a re-run refreshes rather than stacking a `.001` duplicate. convert weathers the layer's assets
    (Add Layer wants this; biome scatter defers it to its own weather step). curve_mode
    ("clear"/"keep"/"verge") makes the layer read the terrain curve mask so it pulls off / confines to
    the carved paths, and "along" switches it to `scatter_along`, which places instances ON the curve;
    every one of those is also stamped on the layer so a later structural rebuild keeps it.
    `curve` binds the path the curve modes need. Returns (layer_object, assets_collection | None).

    The params assembled here are NOT `_build_params`: a first build carries the kind's live knob
    values, which a rebuild deliberately omits so it cannot overwrite tuned ones. The structural half
    is what the two share, and the `scene seams` gate asserts they agree by rebuilding a fresh layer
    and comparing.
    """
    if emitter is None:
        raise ValueError("no emitter to scatter on")
    if kind not in LAYER_TYPES:
        raise ValueError(f"unknown scatter kind {kind!r} (have: {sorted(LAYER_TYPES)})")
    if curve_mode == "along" and curve is None:
        raise ValueError("curve_mode='along' places instances ALONG a curve and needs one")
    if curve_align is None:
        curve_align = ALONG_ALIGN_DEFAULT
    spec = LAYER_TYPES[kind]
    if knobs is None:
        knobs = dict(spec["knobs"])
    if align is None:
        align = spec["align"]
    register()  # the layer config has to exist before anything writes one (a gate, the `build` tool)
    if coll is None:
        coll = _ensure_scatter_coll(emitter, scene)

    assets = None
    if kind != "empty":
        _apply_op({"op": "make_proxies", "kinds": [kind]})
        assets = bpy.data.collections.get(_assets_name(kind))

    if name is None:
        # Reuse looks for a layer of this kind on THIS EMITTER, not one in this collection: the
        # emitter is what "the trees layer" is a layer of, and a re-run has to find the same object
        # whether or not a hand edit moved it out of the collection.
        existing = next((o for o in layers_of(emitter)
                         if o.bbt_scatter_layer.kind == kind), None) if reuse else None
        name = existing.name if existing is not None \
            else _unique_object_name(f"{emitter.name} {spec['label']}")

    if curve_mode == "along":
        # scatter_along takes a different socket set from the surface scatter -- the placement is the
        # curve's, so the kind's density/spacing knobs have nowhere to land. `align` here is the
        # along-curve BOOLEAN (`curve_align`), not the surface enum.
        params = {"emitter": emitter.name, "align": bool(curve_align), "curve": curve.name}
        if assets is not None:
            params["assets"] = assets.name
    else:
        params = {"emitter": emitter.name, "align": align, **knobs}
        if assets is not None:
            params["assets"] = assets.name
        if assets_exclude:
            params["assets_exclude"] = list(assets_exclude)
        if camera is not None:
            params["camera"] = camera.name
        if curve_mode in ("clear", "keep", "verge"):
            params["curve_mode"] = "clear" if curve_mode == "clear" else "keep"
            if curve_mode == "verge":
                params["curve_attr"] = edge_attr_name(curve) if curve is not None \
                    else "bbt_curve_none"
    build_recipe("scatter_along" if curve_mode == "along" else "scatter", name, params)

    obj = bpy.data.objects.get(name)
    if obj is None:
        raise ValueError(f"scatter build did not create the layer object {name!r}")
    _move_to_collection(obj, coll)
    obj[LAYER_STAMP] = 1  # the marker: what makes this findable as a layer at all
    lay = obj.bbt_scatter_layer
    lay.kind = kind
    lay.assets = assets
    lay.align = align
    # The emitter and camera on the LAYER, so a rebuild reads what this layer scatters on instead of
    # whatever the panel happens to point at. `camera` stays None-able: no camera is a valid layer.
    lay.emitter = emitter
    lay.camera = camera
    if assets_exclude is not None and hasattr(lay, "assets_exclude"):
        lay.assets_exclude = ", ".join(assets_exclude)  # so a later structural rebuild keeps it
    # The curve binding, on the layer for the same reason the emitter is: every curve mode resolves
    # its path at BUILD time, so a rebuild that could not find the curve would silently fall back to
    # scattering everywhere (clear/keep) or to nothing at all (verge/along).
    if curve is not None:
        lay.curve = curve
    lay.curve_align = bool(curve_align)
    if curve_mode is not None and hasattr(lay, "curve_mode"):
        try:
            lay.curve_mode = curve_mode
        except (TypeError, ValueError):
            pass
    if convert:
        _convert_layer_assets(lay, scene)
    return obj, assets


def rebuild(obj, *, scene=None):
    """Rebuild one layer from its OWN structural config, keeping its tuned live knobs.

    The peer of `foliage_build.rebuild`, and here rather than in the panel because it was written
    twice there (Build This Layer, Build All) and once would have been enough: the recipe choice, the
    params and the asset re-weathering are properties of a layer, not of a button. A layer with no
    emitter recorded raises, because a scatter with no surface to scatter on builds an empty mesh and
    reports success.
    """
    if obj is None:
        raise ValueError("no layer to rebuild")
    lay = obj.bbt_scatter_layer
    if lay.emitter is None:
        raise ValueError(f"{obj.name} records no emitter to scatter on")
    build_recipe(layer_recipe(lay), obj.name, _build_params(obj))
    obj = bpy.data.objects.get(obj.name)
    if obj is not None:
        _convert_layer_assets(obj.bbt_scatter_layer, scene)  # weather its assets (custom or proxy)
    return obj


def biome_scatter(emitter, recipe, *, scene, camera=None, convert=False, curve_mode=None):
    """Build every layer in a biome scatter recipe on `emitter`. `recipe` is the manifest's scatter
    dict {kind: cfg}; unknown/`empty` kinds are skipped. Idempotent: reuses an existing layer of
    each kind so a re-run refreshes rather than doubling the instance count. convert weathers each
    layer's assets inline (off by default; Build Biome / apply_biome run the weather step with the
    artist's checkbox). curve_mode ("clear"/"keep") is passed to every layer so a biome scatter can
    be made path-aware; it reads the terrain curve mask, so build the typed curve first, then
    re-run with curve_mode to open the corridor. Returns the list of created layer object names."""
    if emitter is None:
        raise ValueError("no emitter to scatter on")
    if not recipe:
        raise ValueError("biome carries no scatter recipe")
    coll = _ensure_scatter_coll(emitter, scene)
    created = []
    for kind, cfg in recipe.items():
        if kind not in LAYER_TYPES or kind == "empty":
            continue
        knobs, align = _biome_layer_params(kind, cfg)
        obj, _assets = add_layer(emitter, kind, scene=scene, knobs=knobs, align=align,
                                 camera=camera, coll=coll, reuse=True, convert=convert,
                                 curve_mode=curve_mode,
                                 assets_exclude=cfg.get("exclude"))
        created.append(obj.name)
    return created


# -- The MCP op -------------------------------------------------------------------------------
def scatter_layer_op(op: dict) -> dict:
    """MCP op `scatter_layer`: build one scatter layer on a named emitter.

    The op that owns the `scatter` and `scatter_along` recipes (`geonodes.OWNED_RECIPES`), and the
    reason they refuse a raw `build_geonodes`. That raw call built the geometry and left the layer
    anonymous: no kind, no emitter recorded, no stamp -- so the Scatter panel listed nothing after an
    agent scattered, and the layer could not be rebuilt because the answer to "on what?" lived in
    whatever the panel happened to point at.

    op: {"emitter": <mesh>, "kind": trees|rocks|plants|grass|empty, "knobs"?: {...},
    "align"?: "up"|"normal", "camera"?: <camera>, "curve"?: <curve>,
    "curve_mode"?: clear|keep|verge|along, "curve_align"?: bool, "assets_exclude"?: [names],
    "name"?: <object>, "reuse"?: bool, "convert"?: bool}.

    `reuse` defaults to TRUE here and False in `add_layer`, and the difference is deliberate: an
    agent's op list gets replayed, and a replay that doubles the instance count every time is not
    idempotent. The panel's Add Layer button means "another one", so it keeps the other default.

    `knobs` are applied OVER the kind's preset rather than instead of it. Asking for `density` alone
    should mean "a rocks layer, denser", not "a rocks layer with the recipe's own spacing and slope
    limits" -- and the second reading is how an agent gets an empty layer from a valid-looking op,
    because a preset carries `distance_min` and `min_normal_z` and the recipe's defaults for those are
    not the kind's.
    """
    emitter = util.object_of(op.get("emitter"), "MESH", label="emitter")
    kind = op.get("kind", "trees")
    if kind not in LAYER_TYPES:
        raise ValueError(f"unknown scatter kind {kind!r} (have: {sorted(LAYER_TYPES)})")
    knobs = dict(LAYER_TYPES[kind]["knobs"], **(op.get("knobs") or {}))
    obj, assets = add_layer(
        emitter, kind, scene=bpy.context.scene, knobs=knobs, align=op.get("align"),
        camera=util.object_of(op.get("camera"), "CAMERA", required=False),
        curve=util.object_of(op.get("curve"), "CURVE", required=False),
        curve_mode=op.get("curve_mode"), curve_align=op.get("curve_align"),
        assets_exclude=op.get("assets_exclude"), name=op.get("name"),
        reuse=bool(op.get("reuse", True)), convert=bool(op.get("convert", True)))
    lay = obj.bbt_scatter_layer
    return {"op": "scatter_layer", "created": [obj.name],
            "info": f"{obj.name}: {kind} on {emitter.name}"
                    + (f", {lay.curve_mode} {lay.curve.name}" if lay.curve is not None else "")
                    + ("" if assets is not None else ", no asset collection (bring your own)"),
            "data": {"object": obj.name, "kind": kind, "emitter": emitter.name,
                     "recipe": layer_recipe(lay),
                     "assets": assets.name if assets is not None else "",
                     "curve_mode": lay.curve_mode,
                     "curve": lay.curve.name if lay.curve is not None else ""}}


# -- The layer's own structural config, and its registration ------------------------------------
def _mesh_poll(self, obj):
    return obj.type == "MESH"


def _curve_poll(self, obj):
    return obj.type == "CURVE"


def _camera_poll(self, obj):
    return obj.type == "CAMERA"


class BBT_ScatterLayer(bpy.types.PropertyGroup):
    """A layer's STRUCTURAL config, stored on the layer OBJECT. The peer of `BBT_FoliageTree`.

    Owned by core rather than by `ui/scatter` for the reason in `CONFIG_PROP` above: a layer is built
    on paths where the addon is not registered, and a group the panel owned made the typed op fail
    where the raw recipe it replaced had worked.

    The name is the object's; there is no name field.
    """

    kind: bpy.props.StringProperty(default="empty")
    assets: bpy.props.PointerProperty(
        name="Assets", type=bpy.types.Collection,
        description="Collection whose objects are instanced")
    assets_exclude: bpy.props.StringProperty(
        name="Skip", default="",
        description="Comma-separated asset names to leave out of THIS layer's pick, without "
                    "editing the shared collection (a generated trunk whose root flare does not "
                    "sit on slopes). Applied on Build")
    align: bpy.props.EnumProperty(
        name="Align",
        items=[("up", "Up", "Keep instances upright (trees)"),
               ("normal", "Normal", "Tilt instances to the surface (rocks, grass)")],
        default="up")
    # What this layer scatters ON, and which camera culls it. On the LAYER because a rebuild has to
    # bind to the mesh this layer is a layer of: while these were read from Scene.bbt_scatter, a
    # rebuild with a different emitter picked silently re-bound the scatter to that mesh, and a layer
    # nothing had picked an emitter for could not be rebuilt at all.
    emitter: bpy.props.PointerProperty(
        name="Emitter", type=bpy.types.Object, poll=_mesh_poll,
        description="The mesh this layer scatters on")
    camera: bpy.props.PointerProperty(
        name="Camera", type=bpy.types.Object, poll=_camera_poll,
        description="Optional camera; this layer culls scatter outside its view")
    vgroup: bpy.props.StringProperty(
        name="Mask Group",
        description="Emitter vertex group that paints where this layer scatters "
                    "(blank = off); applied on Build")
    # Curve binding (BobSplines, the scatter mask). clear/keep read the terrain's baked
    # bbt_curve_mask; along switches the layer to the scatter_along recipe (instances placed on the
    # curve itself).
    curve_mode: bpy.props.EnumProperty(
        name="Curve",
        items=[("none", "None", "Ignore curves"),
               ("clear", "Clear", "Clear this layer along paths (the whole path band)"),
               ("keep", "Keep only", "Scatter only along paths (the whole path band)"),
               ("verge", "Verge (path edge)", "Scatter only on the path shoulders / edge ring, "
                "not the driving surface (the curve overlay's bbt_curve_edge)"),
               ("along", "Along curve", "Place instances along a chosen curve (fence posts, cobbles)")],
        default="none")
    curve: bpy.props.PointerProperty(
        name="Curve", type=bpy.types.Object, poll=_curve_poll,
        description="The path this layer follows: instances along it (Along curve mode), or its "
                    "edge ring (Verge mode). Verge needs a curve; empty scatters nothing")
    curve_align: bpy.props.BoolProperty(
        name="Align to curve", default=ALONG_ALIGN_DEFAULT,
        description="Orient along-curve instances to follow the path (Along curve mode)")


def register():
    """Register the per-object layer config and the emitter's collection pointer.

    Idempotent and callable by a gate as well as by the addon, exactly like `foliage_build.register`
    and `core/env.py`: `add_layer` calls it, so "a layer always carries its config" holds by
    construction rather than by whoever remembered to register first.
    """
    if getattr(bpy.types.Object, CONFIG_PROP, None) is not None:
        return
    bpy.utils.register_class(BBT_ScatterLayer)
    setattr(bpy.types.Object, COLL_PROP, bpy.props.PointerProperty(type=bpy.types.Collection))
    setattr(bpy.types.Object, CONFIG_PROP, bpy.props.PointerProperty(type=BBT_ScatterLayer))


def unregister():
    if getattr(bpy.types.Object, CONFIG_PROP, None) is None:
        return
    delattr(bpy.types.Object, CONFIG_PROP)
    delattr(bpy.types.Object, COLL_PROP)
    bpy.utils.unregister_class(BBT_ScatterLayer)
