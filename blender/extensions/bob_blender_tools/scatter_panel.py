"""Scatter: a GScatter-style multi-layer scatter panel, in-process over bbmcp.

The Scatter counterpart to the Heightfield Terrain panel. Unlike terrain, scatter
has no venv side: it is pure geometry nodes, so this drives the existing bbmcp
`scatter` recipe in-process through apply_op (no subprocess, no bake).

Data model is object-native, so each datum has one home and there is no
panel-vs-modifier drift:

- Each layer is one object in a per-emitter scatter collection. The emitter points
  at that collection via Object.bbt_scatter_coll. Structural config (kind, assets,
  align) lives on the layer object's Object.bbt_scatter_layer.
- The live knobs (Density, Seed, scale, slope, path clearing) live on the layer
  modifier's inputs (mod.properties.inputs.<id>.value in Blender 5.2, the surface
  the modifier actually evaluates), drawn directly in the panel. Editing one is
  live; no rebuild, no sync code.
- Scene.bbt_scatter holds only UI state (active emitter, path, active index).

Structural edits (assets/align/path presence) apply on an explicit Build press,
not from a property callback (rebuilding from an update callback risks
re-entrancy). build_geonodes is non-destructive and restores the live knobs by
socket name, so a structural rebuild preserves tuned values.
"""

import os
import random

import bpy
from bpy.props import BoolProperty, EnumProperty, IntProperty, PointerProperty, StringProperty
from bpy.types import Operator, Panel, PropertyGroup, UIList

from . import server, ui_helpers

# The live knobs drawn per layer, grouped by panel. A knob is only drawn when its
# socket exists (path/paint/camera sockets appear only when that feature is set).
_CORE_KNOBS = ["Density", "Distance Min", "Seed", "Min Scale", "Max Scale",
               "Min Normal Z", "Max Normal Z"]
# Live knobs for an along-curve layer (the scatter_along recipe), drawn instead of _CORE_KNOBS.
_ALONG_KNOBS = ["Spacing", "Offset", "Z Offset", "Yaw", "Jitter", "Seed", "Min Scale", "Max Scale"]
_HEIGHT_KNOBS = ["Height Strength", "Height Min", "Height Max", "Height Falloff"]
_NOISE_KNOBS = ["Noise Strength", "Noise Scale", "Noise Contrast", "Noise Seed"]
_CAMERA_KNOBS = ["Camera Distance", "Camera Cone", "Cull Falloff"]

# Layer type presets: a Blender-side dict (no codegen; no second interpreter reads
# it, unlike the heightfield presets). Each seeds the structural config and the
# initial live-knob values, and points assets at BOB_Assets_<Kind>. Icons are
# standard mesh-add icons, so they are always present.
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


# Helpers
def _apply(ops):
    """Run bbmcp ops in-process, the path the terrain panel's build step uses."""
    server._ensure_path()
    from bbmcp.dispatch import apply_op

    return [apply_op(op) for op in ops]


def _assets_name(kind):
    return f"BOB_Assets_{kind.capitalize()}"


# Biome enum: folders under library/models/<name>/ that carry a manifest.json (a real CC0 asset
# set). The item list is cached module-side so Blender does not GC the enum strings (the same
# pitfall the shader texture-set enum guards against), with a stable id per biome.
_BIOME_ITEMS = [("NONE", "None", "No biomes in library/models", "", 0)]
_BIOME_IDS = {"NONE": 0}


def _models_root():
    return os.path.join(os.path.dirname(server._repo_blender_dir()), "library", "models")


def _biomes():
    root = _models_root()
    if not os.path.isdir(root):
        return []
    return sorted(n for n in os.listdir(root)
                  if os.path.isfile(os.path.join(root, n, "manifest.json")))


def _biome_items(self, context):
    global _BIOME_ITEMS
    items = []
    for n in _biomes():
        if n not in _BIOME_IDS:
            _BIOME_IDS[n] = len(_BIOME_IDS)  # next unused id, fixed for this session
        items.append((n, n.replace("_", " ").title(), f"Import the {n} asset set", "", _BIOME_IDS[n]))
    _BIOME_ITEMS = items or [("NONE", "None", "No biomes in library/models", "", 0)]
    return _BIOME_ITEMS


# Biome-scatter enum: biomes whose manifest carries a scatter recipe, so a whole layer stack can
# be built from one pick (parallel to the Shaders Biome Terrain enum). Cached module-side with a
# stable id per biome (the same enum-GC / reindex guard the asset-set enum uses).
_BIOME_SCATTER_ITEMS = [("NONE", "None", "No biome scatter recipe", "", 0)]
_BIOME_SCATTER_IDS = {"NONE": 0}


def _biome_scatter_items(self, context):
    global _BIOME_SCATTER_ITEMS
    server._ensure_path()
    from bbmcp import assets

    items = []
    for n in assets.list_biomes():
        if not assets.biome_scatter(n):
            continue
        if n not in _BIOME_SCATTER_IDS:
            _BIOME_SCATTER_IDS[n] = len(_BIOME_SCATTER_IDS)  # next unused id, fixed for this session
        items.append((n, n.replace("_", " ").title(),
                      f"Scatter the {n} recipe (a layer per kind)", "", _BIOME_SCATTER_IDS[n]))
    _BIOME_SCATTER_ITEMS = items or [("NONE", "None", "No biome carries a scatter recipe", "", 0)]
    return _BIOME_SCATTER_ITEMS


def _has_biome_scatter():
    server._ensure_path()
    from bbmcp import assets

    return any(assets.biome_scatter(n) for n in assets.list_biomes())


def _nodes_mod(obj):
    if obj is None:
        return None
    return next((m for m in obj.modifiers if m.type == "NODES"), None)


def _socket_ids(ng):
    """Map input socket name -> identifier, for reaching the live modifier input."""
    return {it.name: it.identifier for it in ng.interface.items_tree
            if getattr(it, "item_type", None) == "SOCKET" and it.in_out == "INPUT"}


def _live_input(obj, socket_name):
    """The modifier input struct for a socket (has a live `.value`), or None."""
    mod = _nodes_mod(obj)
    if mod is None or mod.node_group is None:
        return None
    ident = _socket_ids(mod.node_group).get(socket_name)
    if ident is None:
        return None
    return getattr(mod.properties.inputs, ident, None)


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


def _active_coll(context):
    scn = context.scene.bbt_scatter
    return scn.emitter.bbt_scatter_coll if scn.emitter is not None else None


def _active_layer(context):
    coll = _active_coll(context)
    scn = context.scene.bbt_scatter
    if coll is None or not coll.objects:
        return None
    idx = max(0, min(scn.active, len(coll.objects) - 1))
    return coll.objects[idx]


def edge_attr_name(curve):
    """The per-curve edge-ring attribute a Verge layer reads for ONE path (BobSplines R5). The
    curve overlay writes the same name; both derive it from the curve's name (resolved at build,
    like the along-curve binding), so a rename is picked up on the next build. Verge needs a curve:
    an unbound layer reads a name nothing writes, so it scatters nothing."""
    return f"bbt_curve_edge_{curve.name}"


def _layer_recipe(lay):
    """Which recipe a layer builds: along-curve placement vs the surface Poisson scatter."""
    return "scatter_along" if lay.curve_mode == "along" else "scatter"


def _build_params(obj, scn):
    """Structural params for a layer rebuild, read from its object-native config.

    The live knobs are intentionally omitted: build_geonodes restores them from the
    old modifier by socket name, so a structural rebuild keeps the tuned values.
    """
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
    if lay.vgroup:
        params["vgroup"] = lay.vgroup
    # clear/keep/verge read a terrain curve mask the overlay baked (BobSplines C4/R5); no proximity.
    # verge keeps to ONE path's edge ring, so it needs a curve; with none bound it reads a name
    # nothing writes, so the layer scatters nothing (pick a path) rather than covering every verge.
    if lay.curve_mode in ("clear", "keep", "verge"):
        params["curve_mode"] = "clear" if lay.curve_mode == "clear" else "keep"
        if lay.curve_mode == "verge":
            params["curve_attr"] = edge_attr_name(lay.curve) if lay.curve is not None \
                else "bbt_curve_none"
    if scn.camera is not None:
        params["camera"] = scn.camera.name
    return params


def _count_instances(context, objs):
    """Total GN instances parented to any of objs, via the dependency graph."""
    names = {o.name for o in objs}
    dg = context.evaluated_depsgraph_get()
    return sum(1 for i in dg.object_instances
               if i.is_instance and i.parent is not None
               and i.parent.original.name in names)


# Data model
def _emitter_poll(self, obj):
    return obj.type == "MESH"


def _path_poll(self, obj):
    return obj.type == "CURVE"


def _camera_poll(self, obj):
    return obj.type == "CAMERA"


class BBT_ScatterLayer(PropertyGroup):
    """Structural config, stored on the layer object. The name is the object's."""

    kind: StringProperty(default="empty")
    assets: PointerProperty(
        name="Assets", type=bpy.types.Collection,
        description="Collection whose objects are instanced")
    align: EnumProperty(
        name="Align",
        items=[("up", "Up", "Keep instances upright (trees)"),
               ("normal", "Normal", "Tilt instances to the surface (rocks, grass)")],
        default="up")
    vgroup: StringProperty(
        name="Mask Group",
        description="Emitter vertex group that paints where this layer scatters "
                    "(blank = off); applied on Build")
    # Curve binding (BobSplines C4). clear/keep read the terrain's baked bbt_curve_mask; along
    # switches the layer to the scatter_along recipe (instances placed on the curve itself).
    curve_mode: EnumProperty(
        name="Curve",
        items=[("none", "None", "Ignore curves"),
               ("clear", "Clear", "Clear this layer along paths (the whole path band)"),
               ("keep", "Keep only", "Scatter only along paths (the whole path band)"),
               ("verge", "Verge (path edge)", "Scatter only on the path shoulders / edge ring, "
                "not the driving surface (the curve overlay's bbt_curve_edge)"),
               ("along", "Along curve", "Place instances along a chosen curve (fence posts, cobbles)")],
        default="none")
    curve: PointerProperty(
        name="Curve", type=bpy.types.Object, poll=_path_poll,
        description="The path this layer follows: instances along it (Along curve mode), or its "
                    "edge ring (Verge mode). Verge needs a curve; empty scatters nothing")
    curve_align: BoolProperty(
        name="Align to curve", default=True,
        description="Orient along-curve instances to follow the path (Along curve mode)")


class BBT_ScatterProps(PropertyGroup):
    """Scene-level UI state only, not layer data."""

    emitter: PointerProperty(
        name="Emitter", type=bpy.types.Object, poll=_emitter_poll,
        description="Object to scatter on (usually the terrain)")
    camera: PointerProperty(
        name="Camera", type=bpy.types.Object, poll=_camera_poll,
        description="Optional camera; every layer culls scatter outside its view")
    active: IntProperty(default=0)
    summary: StringProperty(default="")


# Operators
class BBT_OT_scatter_make_proxies(Operator):
    bl_idname = "bob_blender_tools.scatter_make_proxies"
    bl_label = "Make Proxies"
    bl_description = "Create block-out proxy assets (BOB_Assets_*) so a scatter works now"

    def execute(self, context):
        _apply([{"op": "make_proxies",
                 "kinds": ["trees", "rocks", "plants", "grass"]}])
        self.report({"INFO"}, "Proxy assets ready")
        return {"FINISHED"}


class BBT_OT_scatter_import_biome(Operator):
    bl_idname = "bob_blender_tools.scatter_import_biome"
    bl_label = "Import Real Assets"
    bl_description = ("Fill the shared BOB_Assets_* collections with real CC0 meshes from one "
                      "geographic scan set (library/models/<name>), replacing the block-out "
                      "proxies. Layers instance those collections, so any layer already set to "
                      "BOB_Assets_<kind> shows the real assets at once")
    bl_options = {"REGISTER", "UNDO"}

    biome: EnumProperty(name="Asset set", items=_biome_items)

    def execute(self, context):
        server._ensure_path()
        from bbmcp import assets

        if not self.biome or self.biome == "NONE":
            self.report({"ERROR"}, "No asset set found in library/models")
            return {"CANCELLED"}
        base = assets.biome_dir(self.biome)
        if not os.path.isfile(os.path.join(base, "manifest.json")):
            self.report({"ERROR"}, f"No assets at library/models/{self.biome} (manifest.json missing)")
            return {"CANCELLED"}
        # Populate the shared collections only (same scope as Make Proxies). populate reuses each
        # BOB_Assets_<kind> collection in place, so layers instancing it update live, no rebuild.
        counts = assets.populate_scatter_assets(self.biome)
        summary = ", ".join(f"{k}:{n}" for k, n in counts.items())
        self.report({"INFO"}, f"Imported {self.biome} -> {summary}")
        return {"FINISHED"}


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
    for k in ("min_normal_z", "max_normal_z", "distance_min"):
        if k in cfg:
            knobs[k] = cfg[k]
    return knobs, cfg.get("align", spec["align"])


class BBT_OT_scatter_biome_scatter(Operator):
    bl_idname = "bob_blender_tools.scatter_biome_scatter"
    bl_label = "Biome Scatter"
    bl_description = ("Scatter a whole biome on the active emitter: build one layer per scatter "
                      "kind from the biome's recipe (density, scale, slope, align), point each at "
                      "its BOB_Assets_<kind> collection, and build them. Import the biome's assets "
                      "first (or use Apply Biome) so the layers instance real meshes, not proxies")
    bl_options = {"REGISTER", "UNDO"}

    biome: EnumProperty(name="Biome", items=_biome_scatter_items)

    def execute(self, context):
        scn = context.scene.bbt_scatter
        emitter = scn.emitter
        if emitter is None:
            self.report({"ERROR"}, "Pick an emitter first")
            return {"CANCELLED"}
        if not self.biome or self.biome == "NONE":
            self.report({"ERROR"}, "No biome carries a scatter recipe")
            return {"CANCELLED"}
        server._ensure_path()
        from bbmcp import assets

        recipe = assets.biome_scatter(self.biome)
        if not recipe:
            self.report({"ERROR"}, f"Biome '{self.biome}' has no scatter recipe")
            return {"CANCELLED"}
        warn = assets.validate_biome(self.biome)
        coll = _ensure_scatter_coll(emitter, context.scene)
        built = []
        for kind, cfg in recipe.items():
            if kind not in LAYER_TYPES or kind == "empty":
                continue
            spec = LAYER_TYPES[kind]
            # Ensure the shared asset collection exists: make_proxies only fills an EMPTY
            # collection, so a prior Import Biome's real meshes are kept, not clobbered.
            _apply([{"op": "make_proxies", "kinds": [kind]}])
            asset_coll = bpy.data.collections.get(_assets_name(kind))
            knobs, align = _biome_layer_params(kind, cfg)
            # Idempotent: reuse an existing layer of this kind (build_geonodes rebuilds it in
            # place by name) instead of stacking a `.001` duplicate. So re-running Biome Scatter
            # or Apply Biome refreshes the layers rather than doubling the instance count.
            existing = next((o for o in coll.objects
                             if getattr(o.bbt_scatter_layer, "kind", "") == kind), None)
            name = existing.name if existing is not None \
                else _unique_object_name(f"{emitter.name} {spec['label']}")
            params = {"emitter": emitter.name, "align": align, **knobs}
            if asset_coll is not None:
                params["assets"] = asset_coll.name
            if scn.camera is not None:
                params["camera"] = scn.camera.name
            _apply([{"op": "build_geonodes", "recipe": "scatter", "name": name, "params": params}])
            obj = bpy.data.objects[name]
            _move_to_collection(obj, coll)
            lay = obj.bbt_scatter_layer
            lay.kind = kind
            lay.assets = asset_coll
            lay.align = align
            built.append(kind)
        if built:
            scn.active = len(list(coll.objects)) - 1
        total = _count_instances(context, list(coll.objects))
        scn.summary = f"{len(built)} biome layers, ~{total} instances"
        msg = f"Scattered {self.biome}: {', '.join(built) or '(no kinds)'}"
        if warn:
            msg += f" ({len(warn)} manifest warnings, see console)"
            print("[bob_blender_tools] biome warnings:", warn)
        self.report({"INFO"}, msg)
        return {"FINISHED"}


class BBT_OT_scatter_add(Operator):
    bl_idname = "bob_blender_tools.scatter_add"
    bl_label = "Add Layer"
    bl_description = "Add a scatter layer of the chosen type"
    bl_options = {"REGISTER", "UNDO"}

    kind: EnumProperty(
        name="Type",
        items=[(k, v["label"], v["desc"], v["icon"], i)
               for i, (k, v) in enumerate(LAYER_TYPES.items())])

    def execute(self, context):
        scn = context.scene.bbt_scatter
        emitter = scn.emitter
        if emitter is None:
            self.report({"ERROR"}, "Pick an emitter first")
            return {"CANCELLED"}

        spec = LAYER_TYPES[self.kind]
        coll = _ensure_scatter_coll(emitter, context.scene)

        assets = None
        if self.kind != "empty":
            _apply([{"op": "make_proxies", "kinds": [self.kind]}])
            assets = bpy.data.collections.get(_assets_name(self.kind))

        name = _unique_object_name(f"{emitter.name} {spec['label']}")
        params = {"emitter": emitter.name, "align": spec["align"], **spec["knobs"]}
        if assets is not None:
            params["assets"] = assets.name
        if scn.camera is not None:
            params["camera"] = scn.camera.name
        _apply([{"op": "build_geonodes", "recipe": "scatter",
                 "name": name, "params": params}])

        obj = bpy.data.objects[name]
        _move_to_collection(obj, coll)
        lay = obj.bbt_scatter_layer
        lay.kind = self.kind
        lay.assets = assets
        lay.align = spec["align"]
        scn.active = list(coll.objects).index(obj)
        self.report({"INFO"}, f"Added {spec['label']} layer")
        return {"FINISHED"}


class BBT_OT_scatter_remove(Operator):
    bl_idname = "bob_blender_tools.scatter_remove"
    bl_label = "Remove Layer"
    bl_description = "Delete the active scatter layer"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        scn = context.scene.bbt_scatter
        obj = _active_layer(context)
        if obj is None:
            self.report({"WARNING"}, "No active layer to remove")
            return {"CANCELLED"}
        name = obj.name
        bpy.data.objects.remove(obj, do_unlink=True)
        coll = _active_coll(context)
        if coll is not None:
            scn.active = max(0, min(scn.active, len(coll.objects) - 1))
        self.report({"INFO"}, f"Removed {name}")
        return {"FINISHED"}


class BBT_OT_scatter_duplicate(Operator):
    bl_idname = "bob_blender_tools.scatter_duplicate"
    bl_label = "Duplicate Layer"
    bl_description = "Copy the active layer, with its own node group and config"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        scn = context.scene.bbt_scatter
        src = _active_layer(context)
        coll = _active_coll(context)
        if src is None or coll is None:
            self.report({"WARNING"}, "No active layer to duplicate")
            return {"CANCELLED"}
        dup = src.copy()
        dup.data = src.data.copy()
        mod = _nodes_mod(dup)
        if mod is not None and mod.node_group is not None:
            mod.node_group = mod.node_group.copy()  # own group, live knobs stay
        dup.name = _unique_object_name(src.name.rsplit(".", 1)[0])
        coll.objects.link(dup)
        scn.active = list(coll.objects).index(dup)
        self.report({"INFO"}, f"Duplicated to {dup.name}")
        return {"FINISHED"}


class BBT_OT_scatter_build_active(Operator):
    bl_idname = "bob_blender_tools.scatter_build_active"
    bl_label = "Build This Layer"
    bl_description = "Rebuild the active layer from its structural config (keeps tuned knobs)"

    def execute(self, context):
        scn = context.scene.bbt_scatter
        obj = _active_layer(context)
        if obj is None or scn.emitter is None:
            self.report({"ERROR"}, "No emitter or active layer")
            return {"CANCELLED"}
        _apply([{"op": "build_geonodes", "recipe": _layer_recipe(obj.bbt_scatter_layer),
                 "name": obj.name, "params": _build_params(obj, scn)}])
        obj = _active_layer(context)
        n = _count_instances(context, [obj]) if obj else 0
        self.report({"INFO"}, f"Built {obj.name}: {n} instances")
        return {"FINISHED"}


class BBT_OT_scatter_build_all(Operator):
    bl_idname = "bob_blender_tools.scatter_build_all"
    bl_label = "Build All"
    bl_description = "Rebuild every layer of the active emitter"

    def execute(self, context):
        scn = context.scene.bbt_scatter
        coll = _active_coll(context)
        if coll is None or scn.emitter is None:
            self.report({"ERROR"}, "Pick an emitter first")
            return {"CANCELLED"}
        objs = list(coll.objects)
        for obj in objs:
            _apply([{"op": "build_geonodes", "recipe": _layer_recipe(obj.bbt_scatter_layer),
                     "name": obj.name, "params": _build_params(obj, scn)}])
        objs = list(coll.objects)
        total = _count_instances(context, objs)
        scn.summary = f"{len(objs)} layers, ~{total} instances"
        self.report({"INFO"}, f"Built {len(objs)} layers, {total} instances")
        return {"FINISHED"}


class BBT_OT_scatter_random_seed(Operator):
    bl_idname = "bob_blender_tools.scatter_random_seed"
    bl_label = "Randomize Seed"
    bl_description = "Reshuffle the active layer with a new seed"

    # Which seed socket to reshuffle. Defaults to the placement Seed; the noise clumping "Noise
    # Seed" passes its own name so it can be reshuffled too (was unreachable from the UI).
    socket: StringProperty(default="Seed", options={"HIDDEN"})

    def execute(self, context):
        obj = _active_layer(context)
        seed = _live_input(obj, self.socket) if obj else None
        if seed is None:
            return {"CANCELLED"}
        seed.value = random.randint(0, 99999)
        obj.update_tag()
        return {"FINISHED"}


# UI
_SEED_KNOBS = ("Seed", "Noise Seed")  # knobs that get a reshuffle button, each targeting its socket


def _draw_knobs(layout, obj, names, enabled=True):
    """Draw each present socket's live value, by name. Skips absent sockets. A seed knob (placement
    Seed or Noise Seed) gets a reshuffle button targeting ITS socket, so both are shufflable.
    enabled=False greys the rows (a band whose Strength is 0 does nothing)."""
    mod = _nodes_mod(obj)
    if mod is None or mod.node_group is None:
        return
    ids = _socket_ids(mod.node_group)
    col = layout.column(align=True)
    col.enabled = enabled
    for nm in names:
        ident = ids.get(nm)
        inp = getattr(mod.properties.inputs, ident, None) if ident else None
        if inp is None:
            continue
        if nm in _SEED_KNOBS:
            ui_helpers.seed_row(col, inp, "value", "bob_blender_tools.scatter_random_seed",
                                text=nm, op_props={"socket": nm})
        else:
            col.row(align=True).prop(inp, "value", text=nm)


class BBT_UL_scatter_layers(UIList):
    def draw_item(self, context, layout, data, item, icon, active_data,
                  active_prop, index):
        obj = item
        spec = LAYER_TYPES.get(obj.bbt_scatter_layer.kind, LAYER_TYPES["empty"])
        row = layout.row(align=True)
        row.label(text=obj.name, icon=spec["icon"])
        row.prop(obj, "hide_viewport", text="", emboss=False,
                 icon="HIDE_ON" if obj.hide_viewport else "HIDE_OFF")


class BBT_PT_scatter(Panel):
    bl_label = "Scatter"
    bl_idname = "BBT_PT_scatter"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "BobBlenderTools"
    bl_order = 3  # pipeline stage 3, after Paths (docs/UX-REDESIGN.md section 4, docs/SPLINES.md 5)
    bl_options = {"DEFAULT_CLOSED"}

    def draw(self, context):
        scn = context.scene.bbt_scatter
        layout = self.layout
        emitter = scn.emitter

        # P1/P7: what we scatter on + which layer we edit, or the empty-state hint.
        layer = _active_layer(context)
        hdr = None
        if emitter is not None:
            hdr = f"{emitter.name} / {layer.name}" if layer is not None else emitter.name
        ui_helpers.context_header(layout, "Scatter", hdr, icon="OUTLINER_OB_MESH",
                                  empty="Pick an emitter to scatter on.")

        layout.prop(scn, "emitter")
        layout.prop(scn, "camera")

        # Asset source: the shared BOB_Assets_* collections that layers instance. Block-out
        # proxies to start, or import a real scan set to replace them (Scatter is the asset home).
        col = layout.column(align=True)
        col.label(text="Assets (shared, for layers to use)")
        row = col.row(align=True)
        row.operator("bob_blender_tools.scatter_make_proxies", text="Make Proxies",
                     icon="OUTLINER_OB_GROUP_INSTANCE")
        row.operator_menu_enum("bob_blender_tools.scatter_import_biome", "biome",
                               text="Import Real", icon="IMPORT")

        coll = _active_coll(context)
        if emitter is None:
            return  # the context header already showed the hint

        row = layout.row()
        if coll is not None:
            row.template_list("BBT_UL_scatter_layers", "", coll, "objects",
                              scn, "active", rows=3)
        else:
            row.label(text="No layers yet", icon="INFO")

        col = row.column(align=True)
        col.operator_menu_enum("bob_blender_tools.scatter_add", "kind",
                               text="", icon="ADD")
        col.operator("bob_blender_tools.scatter_remove", text="", icon="REMOVE")
        col.operator("bob_blender_tools.scatter_duplicate", text="", icon="DUPLICATE")

        # Biome Scatter: build the whole layer stack from a biome's recipe in one pick (parallel
        # to the Shaders Biome Terrain). Needs the emitter, so it sits here once one is set.
        if _has_biome_scatter():
            row = layout.row(align=True)
            row.operator_menu_enum("bob_blender_tools.scatter_biome_scatter", "biome",
                                   text="Biome Scatter", icon="WORLD")

        ui_helpers.structural_action(layout, "bob_blender_tools.scatter_build_all",
                                     note="rebuilds every layer of this emitter")
        if scn.summary:
            layout.label(text=scn.summary, icon="INFO")


class BBT_PT_scatter_layer(Panel):
    bl_label = "Active Layer"
    bl_idname = "BBT_PT_scatter_layer"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "BobBlenderTools"
    bl_parent_id = "BBT_PT_scatter"

    def draw(self, context):
        scn = context.scene.bbt_scatter
        layout = self.layout
        obj = _active_layer(context)
        if obj is None:
            layout.label(text="Pick or add a layer to edit it.", icon="INFO")
            return

        # The parent Scatter header already names the active layer; here show only its kind.
        lay = obj.bbt_scatter_layer
        along = lay.curve_mode == "along"
        spec = LAYER_TYPES.get(lay.kind, LAYER_TYPES["empty"])
        layout.label(text=spec["label"], icon=spec["icon"])

        # Structural group (P3): assets/align/mask/curve apply on a Build (a rebuild), not from a
        # callback. Marked so the split from the live knobs below is explicit.
        box = layout.box()
        box.label(text="Structural (Build to apply)", icon=ui_helpers.STRUCTURAL_ICON)
        # Curve binding (BobSplines C4/R5): clear/keep read the terrain's baked curve mask (all
        # paths at once); along places instances along the chosen curve; verge keeps to ONE path's
        # edge ring -- it needs a curve (empty scatters nothing).
        box.prop(lay, "curve_mode")
        if along:
            box.prop(lay, "curve")
            box.prop(lay, "curve_align")
        elif lay.curve_mode == "verge":
            box.prop(lay, "curve")
            if lay.curve is None:
                box.label(text="Pick a path for its verge", icon="ERROR")
        box.prop(lay, "assets")  # the per-layer collection browser
        if not along:
            box.prop(lay, "align", expand=True)
            box.prop_search(lay, "vgroup", scn.emitter, "vertex_groups", text="Mask Group")
        ui_helpers.structural_action(box, "bob_blender_tools.scatter_build_active",
                                     note="rebuilds this layer's graph (keeps tuned knobs)")

        # Live group (P3): the modifier's own inputs, edited in place, no rebuild.
        if _nodes_mod(obj) is None:
            layout.label(text="No scatter modifier", icon="ERROR")
            return
        layout.label(text="Live knobs (instant)")
        _draw_knobs(layout, obj, _ALONG_KNOBS if along else list(_CORE_KNOBS))


class BBT_PT_scatter_masks(Panel):
    bl_label = "Masks"
    bl_idname = "BBT_PT_scatter_masks"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "BobBlenderTools"
    bl_parent_id = "BBT_PT_scatter_layer"
    bl_options = {"DEFAULT_CLOSED"}

    def draw(self, context):
        layout = self.layout
        obj = _active_layer(context)
        if obj is None or _nodes_mod(obj) is None:
            layout.label(text="No active layer", icon="INFO")
            return
        if obj.bbt_scatter_layer.curve_mode == "along":
            layout.label(text="Not used for an along-curve layer", icon="INFO")
            return
        # A band does nothing until its Strength is above 0. Keep the Strength knob live and grey
        # the dependent knobs when it is 0 (the same "grey the inert knob" idiom Paths uses).
        def _on(socket):
            inp = _live_input(obj, socket)
            return inp is not None and inp.value > 0.0
        layout.label(text="Altitude")
        _draw_knobs(layout, obj, _HEIGHT_KNOBS[:1])
        _draw_knobs(layout, obj, _HEIGHT_KNOBS[1:], enabled=_on("Height Strength"))
        layout.label(text="Noise / clumping")
        _draw_knobs(layout, obj, _NOISE_KNOBS[:1])
        _draw_knobs(layout, obj, _NOISE_KNOBS[1:], enabled=_on("Noise Strength"))
        # Paint Strength exists only when the layer has a mask group set + Built.
        if _live_input(obj, "Paint Strength") is not None:
            layout.label(text="Paint")
            _draw_knobs(layout, obj, ["Paint Strength"])
        elif obj.bbt_scatter_layer.vgroup:
            layout.label(text="Mask group set, press Build to apply", icon="INFO")


class BBT_PT_scatter_camera(Panel):
    bl_label = "Camera Cull"
    bl_idname = "BBT_PT_scatter_camera"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "BobBlenderTools"
    bl_parent_id = "BBT_PT_scatter_layer"
    bl_options = {"DEFAULT_CLOSED"}

    def draw(self, context):
        scn = context.scene.bbt_scatter
        layout = self.layout
        obj = _active_layer(context)
        if obj is None or _nodes_mod(obj) is None:
            layout.label(text="No active layer", icon="INFO")
            return
        if obj.bbt_scatter_layer.curve_mode == "along":
            layout.label(text="Not used for an along-curve layer", icon="INFO")
            return
        if scn.camera is None:
            layout.label(text="Set a Camera on the Scatter panel", icon="INFO")
            return
        if _live_input(obj, "Camera Distance") is None:
            layout.label(text="Build this layer to cull", icon="INFO")
            return
        _draw_knobs(layout, obj, _CAMERA_KNOBS)


CLASSES = (
    BBT_ScatterLayer,
    BBT_ScatterProps,
    BBT_OT_scatter_make_proxies,
    BBT_OT_scatter_import_biome,
    BBT_OT_scatter_biome_scatter,
    BBT_OT_scatter_add,
    BBT_OT_scatter_remove,
    BBT_OT_scatter_duplicate,
    BBT_OT_scatter_build_active,
    BBT_OT_scatter_build_all,
    BBT_OT_scatter_random_seed,
    BBT_UL_scatter_layers,
    BBT_PT_scatter,
    BBT_PT_scatter_layer,
    BBT_PT_scatter_masks,
    BBT_PT_scatter_camera,
)


def register():
    for cls in CLASSES:
        bpy.utils.register_class(cls)
    bpy.types.Object.bbt_scatter_coll = PointerProperty(type=bpy.types.Collection)
    bpy.types.Object.bbt_scatter_layer = PointerProperty(type=BBT_ScatterLayer)
    bpy.types.Scene.bbt_scatter = PointerProperty(type=BBT_ScatterProps)


def unregister():
    del bpy.types.Scene.bbt_scatter
    del bpy.types.Object.bbt_scatter_layer
    del bpy.types.Object.bbt_scatter_coll
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
