"""Scatter orchestration, shared by the Scatter panel and the biome/MCP path.

The GN `scatter` / `scatter_along` recipes themselves live in core/geonodes/; this module holds
the layer above them: the Blender-side layer-type presets, the small collection/naming helpers,
the structural-params builder, and the two build functions each panel Operator and the biome
handler call. One builder serves the Add-Layer button and the biome scatter, so the make_proxies
+ build_geonodes recipe writes that stand up a layer live in exactly one place.

bpy-only, and it never imports ui/: the panel operators import THIS module (presets + helpers +
builders) and keep only their context resolution (active emitter, UI-state writes, self.report).
The build path runs the make_proxies + build_geonodes ops through the in-process dispatch
(`apply_op`), the same path the panel used, imported lazily so dispatch can register the biome
handlers that import this module without an import cycle.

Scene state is read by plain attribute access (`getattr(obj, 'bbt_scatter_layer', None)`), never
through the ui: core stays the acyclic root of the dependency graph.
"""

import bpy

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
def _build_params(obj, scn):
    """Structural params for a layer rebuild, read from its object-native config.

    The live knobs are intentionally omitted: build_geonodes restores them from the old modifier
    by socket name, so a structural rebuild keeps the tuned values. `scn` is the Scene.bbt_scatter
    UI state (emitter + camera), read by plain attribute access."""
    lay = obj.bbt_scatter_layer
    if lay.curve_mode == "along":
        # scatter_along places instances along the curve, then projects them DOWN onto the emitter
        # so they sit on the terrain following the curve's route (needs both the curve and emitter).
        params = {"align": lay.curve_align, "emitter": scn.emitter.name if scn.emitter else ""}
        if lay.assets is not None:
            params["assets"] = lay.assets.name
        if lay.curve is not None:
            params["curve"] = lay.curve.name
        return params
    params = {"emitter": scn.emitter.name if scn.emitter else "", "align": lay.align}
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
    if scn.camera is not None:
        params["camera"] = scn.camera.name
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
              assets_exclude=None):
    """Build one scatter layer object on `emitter` and file it in the emitter's scatter collection.

    Ensures the kind's block-out proxy assets (make_proxies is idempotent: it only fills an empty
    collection), builds the `scatter` recipe through dispatch, moves the result into the emitter's
    scatter collection, and stamps its object-native structural config. Shared by the panel's Add
    Layer and by biome_scatter.

    knobs/align default to the kind's LAYER_TYPES preset; pass merged biome values to override.
    reuse rebuilds an existing layer of this kind in place (build_geonodes is non-destructive) so
    a re-run refreshes rather than stacking a `.001` duplicate. convert weathers the layer's assets
    (Add Layer wants this; biome scatter defers it to its own weather step). curve_mode
    ("clear"/"keep") makes the layer read the terrain curve mask so it pulls off / confines to the
    carved paths; it is also stamped on the layer so a later structural rebuild keeps it. Returns
    (layer_object, assets_collection | None)."""
    if emitter is None:
        raise ValueError("no emitter to scatter on")
    if kind not in LAYER_TYPES:
        raise ValueError(f"unknown scatter kind {kind!r} (have: {sorted(LAYER_TYPES)})")
    spec = LAYER_TYPES[kind]
    if knobs is None:
        knobs = dict(spec["knobs"])
    if align is None:
        align = spec["align"]
    if coll is None:
        coll = _ensure_scatter_coll(emitter, scene)

    assets = None
    if kind != "empty":
        _apply_op({"op": "make_proxies", "kinds": [kind]})
        assets = bpy.data.collections.get(_assets_name(kind))

    if name is None:
        existing = next((o for o in coll.objects
                         if getattr(o.bbt_scatter_layer, "kind", "") == kind), None) \
            if reuse else None
        name = existing.name if existing is not None \
            else _unique_object_name(f"{emitter.name} {spec['label']}")

    params = {"emitter": emitter.name, "align": align, **knobs}
    if assets is not None:
        params["assets"] = assets.name
    if assets_exclude:
        params["assets_exclude"] = list(assets_exclude)
    if camera is not None:
        params["camera"] = camera.name
    if curve_mode in ("clear", "keep"):
        params["curve_mode"] = curve_mode
    _apply_op({"op": "build_geonodes", "recipe": "scatter", "name": name, "params": params})

    obj = bpy.data.objects.get(name)
    if obj is None:
        raise ValueError(f"scatter build did not create the layer object {name!r}")
    _move_to_collection(obj, coll)
    lay = obj.bbt_scatter_layer
    lay.kind = kind
    lay.assets = assets
    lay.align = align
    if assets_exclude is not None and hasattr(lay, "assets_exclude"):
        lay.assets_exclude = ", ".join(assets_exclude)  # so a later structural rebuild keeps it
    if curve_mode is not None and hasattr(lay, "curve_mode"):
        try:
            lay.curve_mode = curve_mode
        except (TypeError, ValueError):
            pass
    if convert:
        _convert_layer_assets(lay, scene)
    return obj, assets


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
