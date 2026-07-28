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
from bpy.props import (BoolProperty, EnumProperty, FloatProperty, IntProperty, PointerProperty,
                       StringProperty)
from bpy.types import Operator, Panel, PropertyGroup, UIList

from ..bridge import server
from ..core import scatter_build
from . import helpers

# The live knobs drawn per layer, grouped by panel. A knob is only drawn when its
# socket exists (path/paint/camera sockets appear only when that feature is set).
_CORE_KNOBS = ["Density", "Distance Min", "Seed", "Min Scale", "Max Scale", "Z Offset",
               "Min Normal Z", "Max Normal Z"]
# Live knobs for an along-curve layer (the scatter_along recipe), drawn instead of _CORE_KNOBS.
_ALONG_KNOBS = ["Spacing", "Offset", "Z Offset", "Yaw", "Jitter", "Seed", "Min Scale", "Max Scale"]
_HEIGHT_KNOBS = ["Height Strength", "Height Min", "Height Max", "Height Falloff"]
_NOISE_KNOBS = ["Noise Strength", "Noise Scale", "Noise Contrast", "Noise Seed"]
_CAMERA_KNOBS = ["Camera Distance", "Camera Cone", "Cull Falloff"]

# The layer-type presets, the collection/naming helpers, the structural-params builder, the
# biome-params merge, and the two build functions live in core/scatter_build.py so the panel
# operators and the biome/MCP path share one copy (subtract-duplication; docs/CONVENTIONS.md).
# Bound here for the enum items, the UIList, and the operator bodies. edge_attr_name is re-exported
# for ui/splines.py, which reads it to bind a Verge layer to a curve's edge ring.
LAYER_TYPES = scatter_build.LAYER_TYPES
_unique_object_name = scatter_build._unique_object_name
_build_params = scatter_build._build_params
_count_instances = scatter_build._count_instances
_convert_layer_assets = scatter_build._convert_layer_assets
edge_attr_name = scatter_build.edge_attr_name


# Helpers
def _apply(ops):
    """Run bbmcp ops in-process, the path the terrain panel's build step uses."""
    from ..core.dispatch import apply_op

    return [apply_op(op) for op in ops]


# Biome-scatter enum: biomes whose manifest carries a scatter recipe, so a whole layer stack can
# be built from one pick (parallel to the Shaders Biome Terrain enum). Cached module-side with a
# stable id per biome (the same enum-GC / reindex guard the asset-set enum uses). Ids start at 0
# for the first real biome so a fresh enum resolves to a real recipe, not the NONE fallback.
_BIOME_SCATTER_ITEMS = [("NONE", "None", "No biome scatter recipe", "", 0)]
_BIOME_SCATTER_IDS = {}


def _biome_scatter_items(self, context):
    global _BIOME_SCATTER_ITEMS
    from ..core import assets

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
    from ..core import assets

    return any(assets.biome_scatter(n) for n in assets.list_biomes())


# Which species a scatter kind grows, cached: `draw` runs on every redraw and resolving this
# lists a directory and parses every preset in it. Cleared on register, so a fresh session (or a
# reload) picks up a pack that was added since.
_FOLIAGE_SPECIES_CACHE = {}


def _foliage_species_for(kind):
    from ..core import assets

    if kind not in _FOLIAGE_SPECIES_CACHE:
        _FOLIAGE_SPECIES_CACHE[kind] = assets.foliage_species_for_kind(kind)
    return _FOLIAGE_SPECIES_CACHE[kind]


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


def _layer_recipe(lay):
    """Which recipe a layer builds: along-curve placement vs the surface Poisson scatter."""
    return "scatter_along" if lay.curve_mode == "along" else "scatter"


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
    assets_exclude: StringProperty(
        name="Skip", default="",
        description="Comma-separated asset names to leave out of THIS layer's pick, without "
                    "editing the shared collection (a generated trunk whose root flare does not "
                    "sit on slopes). Applied on Build")
    align: EnumProperty(
        name="Align",
        items=[("up", "Up", "Keep instances upright (trees)"),
               ("normal", "Normal", "Tilt instances to the surface (rocks, grass)")],
        default="up")
    vgroup: StringProperty(
        name="Mask Group",
        description="Emitter vertex group that paints where this layer scatters "
                    "(blank = off); applied on Build")
    # Curve binding (BobSplines, the scatter mask). clear/keep read the terrain's baked bbt_curve_mask; along
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

    # Generate Asset (docs/GENERATION.md, mesh generation). Scatter-grade by default and the panel says so;
    # `gen_hero` swaps Decimate for Quadriflow and doubles the bake resolution.
    gen_prompt: StringProperty(
        name="Prompt", default="",
        description="What to generate, e.g. 'a mossy granite boulder'. The single-subject "
                    "framing clause is appended for you")
    gen_kind: EnumProperty(
        name="Kind", items=[(k, k.capitalize(), "") for k in ("trees", "rocks", "plants", "grass")],
        default="rocks",
        description="Which BOB_Assets_* collection the finished asset joins, so a scatter layer "
                    "of that kind instances it")
    gen_height: FloatProperty(
        name="Height (m)", default=1.5, min=0.01, max=100.0,
        description="Real-world height. Mandatory: every image-to-3D model emits a "
                    "unit-cube-normalised mesh, so without this the scatter looks like a toy set")
    gen_faces: IntProperty(
        name="Face Budget", default=4000, min=200, max=200000,
        description="Target triangle count for the scattered mesh. A generated mesh arrives at "
                    "roughly half a million")
    gen_seed: IntProperty(name="Seed", default=0, min=0)
    gen_hero: BoolProperty(
        name="Hero", default=False,
        description="Quadriflow instead of Decimate and a 2K bake. For an asset the camera gets "
                    "close to; scatter-grade is the default because a background prop instanced "
                    "four thousand times is never deformed")


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


def _generated_pack():
    """The generated asset pack root the addon registered, or None."""
    from ..core import assets

    return assets.generated_root()


def _comfy_reachable_cached():
    """The last known ComfyUI state, never a probe: a socket call from `draw` would freeze the
 UI for the timeout in exactly the case the row exists to report (found the first time a generation blocked the UI)."""
    from .shaders import _COMFY_STATE

    return _COMFY_STATE


class BBT_OT_scatter_generate_asset(Operator):
    bl_idname = "bob_blender_tools.scatter_generate_asset"
    bl_label = "Generate Asset"
    bl_description = ("Generate a scatter asset from the prompt with ComfyUI: reference image, "
                      "then geometry plus PBR texture in one pass, then Blender bakes, scales, "
                      "LODs and BobShades it into the generated pack. Runs in the background; "
                      "needs a local ComfyUI server, and without one nothing else changes")
    bl_options = {"REGISTER", "UNDO"}

    # One operator, two entries in the box, because the difference between them is one input. With
    # `from_control` the ACTIVE object's shape conditions the geometry (`mesh_geom_ctrl`) instead of the reference
    # image alone (`mesh_geom_texture`), and the block-out's own height replaces the Height field, since a proxy that
    # was placed in a layout already says how big the asset is.
    from_control: BoolProperty(
        name="From Block-out", default=False, options={"SKIP_SAVE"},
        description="Condition the geometry on the active object's shape, so the result keeps its "
                    "silhouette and footprint and drops into the layout it was blocked out in")

    def execute(self, context):
        from ..core import comfy, gen_assets
        from .shaders import _COMFY_STATE, _submit, _comfy_job_running

        scn = context.scene.bbt_scatter
        prompt = (scn.gen_prompt or "").strip()
        if not prompt:
            self.report({"ERROR"}, "Describe the asset first (the Prompt field)")
            return {"CANCELLED"}
        pack = _generated_pack()
        if not pack:
            self.report({"ERROR"}, "No generated pack folder (set an output folder in the "
                                   "add-on preferences)")
            return {"CANCELLED"}
        if _comfy_job_running():
            self.report({"WARNING"}, "A ComfyUI job is already running")
            return {"CANCELLED"}

        kind, seed = scn.gen_kind, int(scn.gen_seed)
        height, faces, hero = float(scn.gen_height), int(scn.gen_faces), bool(scn.gen_hero)

        # The block-out export is bpy, so it happens HERE, on the main thread, before the job is
        # submitted (the same rule as the stylise button's render). It is one mesh copy and one glTF
        # write, and it is the whole main-thread cost of the press.
        control, control_bbox = None, None
        if self.from_control:
            proxy = context.active_object
            if proxy is None or proxy.type != "MESH":
                self.report({"ERROR"}, "Make the block-out proxy the active object first")
                return {"CANCELLED"}
            staging = comfy.staging_dir(pack)
            os.makedirs(staging, exist_ok=True)
            signal = gen_assets.control_signal(
                proxy, comfy.unique_file_name(staging, f"{comfy.slugify(prompt)}_control", ".glb"),
                mode=comfy.DEFAULT_CONTROL_MODE)
            control, control_bbox = signal["path"], signal.get("bbox")
            height = signal["height_m"] or height
        # Foliage is open by construction, so its holes are the asset, and this decides TWO stages:
        # the ComfyUI remesh and Blender's pinhole fill. See comfy.FOLIAGE_KINDS.
        foliage = comfy.is_foliage(kind)

        # ONE worker job for the whole ComfyUI half (`mesh_subject` then `mesh_geom_texture` by default), then one main-thread
        # finish. They do not interleave, because whichever route runs hands over a mesh that is
        # already at its budget with UVs, so Blender has nothing to contribute in between. Nothing in
        # `generate` touches bpy; everything in `landed` does and nothing there touches the network.
        #
        # The route is a value, not a branch: `comfy.asset_chain` takes the kind and the control and
        # picks the staging function, and `comfy.finish_passes` maps whatever it staged onto the two
        # finish callbacks, so no route needs a second operator or a widget. The route A/B decided
        # the default and the geometry A/B decided which asset classes leave it.
        def generate(job):
            chain = comfy.asset_chain(kind=kind, control=control, control_bbox=control_bbox)
            return chain(prompt, pack, seed=seed, tier="default",
                         faces=faces, remesh=not foliage,
                         texture_size=2048 if hero else 1024,
                         **({"control": control, "control_bbox": control_bbox,
                             "control_mode": comfy.DEFAULT_CONTROL_MODE}
                            if self.from_control else {}),
                         on_queued=job.note_prompt_id,
                         on_progress=job.report)

        def landed(job):
            staged = job.result
            if not staged:
                return
            simplify_pass, texture_pass = comfy.finish_passes(staged)
            report = gen_assets.finish_asset(
                staged["raw_mesh"], pack, kind=kind, name=comfy.slugify(prompt),
                height_m=height, faces=faces, hero=hero,
                bake_size=2048 if hero else gen_assets.DEFAULT_BAKE_SIZE,
                fill_pinholes=not foliage, exports=comfy.stage_exports(staged),
                simplify_pass=simplify_pass, texture_pass=texture_pass,
                provenance=dict(staged["meta"], prompt=prompt, seed=seed))
            gen_assets.import_generated(report["name"], kind=kind, pack_dir=pack)
            comfy.reject_variant(staged["dir"])  # the staged intermediates are spent
            scn.summary = (f"{report['name']}: {report['lod_faces'][0]} faces, "
                           f"{report['height_m']} m, in BOB_Assets_{kind.capitalize()}")
            _COMFY_STATE.update(ok=True, detail=scn.summary)

        _submit(f"asset: {prompt[:32]}", generate, landed)
        self.report({"INFO"}, "Generating in the background; the viewport stays usable")
        return {"FINISHED"}


class BBT_OT_scatter_biome_scatter(Operator):
    bl_idname = "bob_blender_tools.scatter_biome_scatter"
    bl_label = "Biome Scatter"
    bl_description = ("Scatter a whole biome on the active emitter: build one layer per scatter "
                      "kind from the biome's recipe (density, scale, slope, align), point each at "
                      "its BOB_Assets_<kind> block-out proxies, and build them. Weather the "
                      "instances in Shaders (or use Build Biome, which converts them for you)")
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
        from ..core import assets

        recipe = assets.biome_scatter(self.biome)
        if not recipe:
            self.report({"ERROR"}, f"Biome '{self.biome}' has no scatter recipe")
            return {"CANCELLED"}
        warn = assets.validate_biome(self.biome)
        # The build loop (proxies + a reused layer per kind) lives in core/scatter_build; the panel
        # keeps the emitter resolution, the active-layer / summary UI writes, and the report. The
        # instances are weathered in Shaders (or by Build Biome), so build without converting here.
        created = scatter_build.biome_scatter(emitter, recipe, scene=context.scene,
                                              camera=scn.camera)
        built = [bpy.data.objects[n].bbt_scatter_layer.kind for n in created
                 if n in bpy.data.objects]
        coll = emitter.bbt_scatter_coll
        if created and coll is not None:
            scn.active = len(list(coll.objects)) - 1
        total = _count_instances(context, list(coll.objects)) if coll is not None else 0
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
        # The layer build (proxies + the scatter recipe + weathering the assets) lives in
        # core/scatter_build; the panel keeps the emitter resolution, the active-index write, and
        # the report. add_layer weathers the assets (convert=True) so a fresh layer reacts to the
        # world with no hunt for Shaders > Convert, the same first-class-shader path Build Biome takes.
        obj, _assets = scatter_build.add_layer(
            emitter, self.kind, scene=context.scene, camera=scn.camera)
        coll = emitter.bbt_scatter_coll
        if coll is not None:
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
        if obj is not None:
            _convert_layer_assets(obj.bbt_scatter_layer)  # weather its assets (custom or proxy)
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
            _convert_layer_assets(obj.bbt_scatter_layer)  # weather its assets (custom or proxy)
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
    # "generate" reshuffles the Generate Asset seed instead of a layer socket, so both seeds go
    # through the one reshuffle operator and the one `helpers.seed_row` idiom.
    target: StringProperty(default="layer", options={"HIDDEN"})

    def execute(self, context):
        if self.target == "generate":
            context.scene.bbt_scatter.gen_seed = random.randint(0, 99999)
            return {"FINISHED"}
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
            helpers.seed_row(col, inp, "value", "bob_blender_tools.scatter_random_seed",
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
    bl_order = 4  # pipeline stage: Scatter, after Paths (docs/CONVENTIONS.md, panel UX conventions)
    bl_options = {"DEFAULT_CLOSED"}

    def draw(self, context):
        scn = context.scene.bbt_scatter
        layout = self.layout
        emitter = scn.emitter

        # The context header, or the empty state: what we scatter on + which layer we edit, or the empty-state hint.
        layer = _active_layer(context)
        hdr = None
        if emitter is not None:
            hdr = f"{emitter.name} / {layer.name}" if layer is not None else emitter.name
        helpers.context_header(layout, "Active mesh", hdr, icon="OUTLINER_OB_MESH",
                                  empty="Pick an emitter to scatter on.")

        layout.prop(scn, "emitter")
        layout.prop(scn, "camera")

        # No standalone Make Proxies here. Both scatter_add and scatter_biome_scatter call
        # make_proxies themselves, so adding a layer or building a biome already creates the shared
        # BOB_Assets_* proxies. The proxy-only path stays reachable via the scatter_make_proxies
        # operator for power users; it is just not the first thing on the panel.
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
            cap = layout.row()
            cap.enabled = False
            cap.label(text="scatter layers only; the Biome panel builds the whole scene",
                      icon="INFO")

        helpers.structural_action(layout, "bob_blender_tools.scatter_build_all",
                                     note="rebuilds every layer of this emitter")
        if scn.summary:
            layout.label(text=scn.summary, icon="INFO")

        _draw_generate(layout, scn, active=context.active_object)


# The routing note under the Generate Asset kind selector (the dead-wood routing rule, then leaf cards,
# docs/FOLIAGE.md 4.5). Only the kinds image-to-3D is weak at carry one: rocks is what the route is
# for and says nothing, which keeps the row a warning rather than decoration. Trees is first because
# it is the one an artist reaches for and the one TRELLIS.2 cannot do -- it returns a single solid
# mesh, so a crown comes back a fan.
#
# Each note now DIRECTS rather than refuses. The first wording said "a trunk, not a crown", which
# invited exactly the use it meant to prevent; the second said what generation is for but left the
# artist at a dead end. Both were held at a refusal deliberately, because until leaf cards landed the leaf
# cards a panel that sent someone to BobFoliage for plants would have been recommending bare sticks.
# The plants and grass notes stop being about draw distance and start being about routing, while
# still allowing generated ground clumps as filler -- that row of the routing table is still a yes.
# Each note names the PANEL it sends the artist to, and that panel's header is "Foliage" -- so these
# say Foliage and not BobFoliage, which is the track's name and not a thing on screen. A pointer that
# names something the artist cannot find is the dead end this copy exists to remove.
_GEN_KIND_NOTE = {
    "trees": "stumps and logs only; grow standing trees in the Foliage panel",
    "plants": "ground clumps read at 2 m; grow real plants in the Foliage panel",
    "grass": "ground clumps read at 2 m; grow real tufts in the Foliage panel",
}


class BBT_OT_scatter_grow_foliage(Operator):
    bl_idname = "bob_blender_tools.scatter_grow_foliage"
    bl_label = "Grow in Foliage"
    bl_description = ("Grow this kind procedurally instead of generating it: builds a foliage "
                      "object at the 3D cursor from the kind's species preset. Needs no ComfyUI "
                      "server and takes no time, because the geometry is a recipe and only its "
                      "bark and leaf textures ever come from generation")
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        from ..core import assets, foliage_build

        kind = context.scene.bbt_scatter.gen_kind
        species = assets.foliage_species_for_kind(kind)
        if species is None:
            self.report({"ERROR"}, f"No foliage species ships for '{kind}'")
            return {"CANCELLED"}
        preset = assets.foliage_species(species)
        # The preset's params go straight to build_geonodes, with no panel state carried into the
        # recipe: that is what keeps foliage off the live-bridge-only list every curve op is on
        # (docs/MCP.md, known gap). Through `foliage_build.grow`, so a tree grown from HERE is the
        # same object the BobFoliage panel adds -- stamped, filed in BOB_Foliage, already listed
        # and already feeling the world's wind. The routing note sends the artist to that panel, so
        # the tree had better be waiting in it (docs/FOLIAGE.md 4.5).
        name = _unique_object_name(species.replace("_", " ").title())
        obj = foliage_build.grow(name, dict(preset["params"], seed=random.randint(0, 99999)),
                                 species=species, scene=context.scene,
                                 location=context.scene.cursor.location.copy())
        if obj is None:
            self.report({"ERROR"}, "Foliage build produced no object")
            return {"CANCELLED"}
        for other in context.selected_objects:
            other.select_set(False)
        obj.select_set(True)
        context.view_layer.objects.active = obj
        warn = assets.validate_foliage_species(species)
        if warn:
            print("[bob_blender_tools] foliage species warnings:", warn)
        # A texture set the species names but no pack ships is the ORDINARY pre-generation state, not
        # a mistake: no placeholder bark set ships on purpose (docs/FOLIAGE.md 4.4), so a fresh conifer
        # is a solid-tint trunk until someone generates its bark. Say which set is missing, because
        # otherwise the tree just looks flat and there is nothing on screen explaining why.
        missing = ", ".join(v for _k, _label, v in assets.foliage_missing_sets(species))
        note = f"; solid tint until {missing} is generated" if missing else ""
        # Now that the Foliage panel exists, the report names it. It used to point at the object's modifier
        # stack, which was honest while there was nowhere better and is a worse answer now: the
        # modifier stack is not an authoring surface, and the artist is one panel away from the
        # thirty knobs grouped and labelled.
        self.report({"INFO"}, f"Grew {name} ({preset['meta'].get('name', species)}); "
                              f"tune it in the Foliage panel{note}")
        return {"FINISHED"}


def _draw_generate(layout, scn, active=None):
    """Generate Asset, beside the proxy and biome routes rather than in a panel of its own: it is
 a third way to fill BOB_Assets_<Kind>, and the artist chooses between them in one place."""
    state = _comfy_reachable_cached()
    box = layout.box()
    row = box.row()
    row.label(text="Generate Asset", icon="SHADERFX")
    row.label(text="scatter-grade" if not scn.gen_hero else "hero")
    if not state.get("ok"):
        cap = box.row()
        cap.enabled = False
        cap.label(text=state.get("detail") or "ComfyUI not connected", icon="UNLINKED")
    box.prop(scn, "gen_prompt", text="")
    row = box.row(align=True)
    row.prop(scn, "gen_kind", text="")
    row.prop(scn, "gen_height")
    note = _GEN_KIND_NOTE.get(scn.gen_kind)
    if note:
        row = box.row()
        row.enabled = False
        row.label(text=note, icon="INFO")
    # The affordance the note points at. It sits in this box rather than in a panel of its own
    # because filling a kind stays ONE decision in ONE place (docs/FOLIAGE.md 4.2): proxies, biome,
    # generate and grow are four ways to fill BOB_Assets_<Kind> and the artist picks between them
    # here. A sentence telling someone to go elsewhere is read after they have already spent 90 s.
    # Unlike Generate it needs no server, so it is never greyed.
    if _foliage_species_for(scn.gen_kind):
        box.operator("bob_blender_tools.scatter_grow_foliage", icon="OUTLINER_OB_CURVES")
    row = box.row(align=True)
    row.prop(scn, "gen_faces")
    row.prop(scn, "gen_hero", toggle=True)
    helpers.seed_row(box, scn, "gen_seed", "bob_blender_tools.scatter_random_seed",
                     op_props={"target": "generate"})
    ready = bool(state.get("ok")) and bool((scn.gen_prompt or "").strip())
    run = box.row()
    run.enabled = ready
    run.operator("bob_blender_tools.scatter_generate_asset", icon="PLAY")
    # The block-out route (`mesh_geom_ctrl`): the same press with the active object's shape as a second input, so
    # it is a second button and not a second panel. Shown only when there is a mesh to condition on,
    # which is the adaptive rule the rest of the suite follows.
    blockout = active if (active is not None and active.type == "MESH") else None
    if blockout is not None:
        run = box.row()
        run.enabled = ready
        run.operator("bob_blender_tools.scatter_generate_asset", text="Asset from Block-out",
                     icon="MESH_CUBE").from_control = True
        note = box.row()
        note.enabled = False
        note.label(text=f"keeps {blockout.name}'s footprint, {blockout.dimensions.z:.2f} m tall")


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

        # Structural group : assets/align/mask/curve apply on a Build (a rebuild), not from a
        # callback. Marked so the split from the live knobs below is explicit.
        box = layout.box()
        # S7: no STRUCTURAL_ICON on the caption; the structural_action button in this box already
        # carries it, so it would show twice a few pixels apart.
        box.label(text="Structural (Build to apply)")
        # Curve binding (BobSplines: the scatter mask and the verge band): clear/keep read the terrain's baked curve mask (all
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
        if lay.assets is not None and not along:
            box.prop(lay, "assets_exclude")
        if not along:
            box.prop(lay, "align", expand=True)
            box.prop_search(lay, "vgroup", scn.emitter, "vertex_groups", text="Mask Group")
        helpers.structural_action(box, "bob_blender_tools.scatter_build_active",
                                     note="rebuilds this layer's graph (keeps tuned knobs)")

        # Live group : the modifier's own inputs, edited in place, no rebuild.
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
            layout.label(text="No active layer: add one on the Scatter panel", icon="INFO")
            return
        if obj.bbt_scatter_layer.curve_mode == "along":
            layout.label(text="Along-curve layer: placed by spacing, not masks; use an area "
                              "layer for masks", icon="INFO")
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
            layout.label(text="No active layer: add one on the Scatter panel", icon="INFO")
            return
        if obj.bbt_scatter_layer.curve_mode == "along":
            layout.label(text="Along-curve layer: no camera cull; use an area layer to cull",
                         icon="INFO")
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
    BBT_OT_scatter_generate_asset,
    BBT_OT_scatter_grow_foliage,
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
    _FOLIAGE_SPECIES_CACHE.clear()  # a reload may have added a pack
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
