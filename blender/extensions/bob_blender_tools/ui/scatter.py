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

import random

import bpy
from bpy.props import BoolProperty, EnumProperty, IntProperty, PointerProperty, StringProperty
from bpy.types import Operator, Panel, PropertyGroup, UIList

from ..bridge import server
from ..core import scatter_build
from . import helpers

# The live knobs drawn per layer, grouped by panel. A knob is only drawn when its
# socket exists (path/paint/camera sockets appear only when that feature is set).
_CORE_KNOBS = ["Density", "Distance Min", "Seed", "Min Scale", "Max Scale",
               "Min Normal Z", "Max Normal Z"]
# Live knobs for an along-curve layer (the scatter_along recipe), drawn instead of _CORE_KNOBS.
_ALONG_KNOBS = ["Spacing", "Offset", "Z Offset", "Yaw", "Jitter", "Seed", "Min Scale", "Max Scale"]
_HEIGHT_KNOBS = ["Height Strength", "Height Min", "Height Max", "Height Falloff"]
_NOISE_KNOBS = ["Noise Strength", "Noise Scale", "Noise Contrast", "Noise Seed"]
_CAMERA_KNOBS = ["Camera Distance", "Camera Cone", "Cull Falloff"]

# The layer-type presets, the collection/naming helpers, the structural-params builder, the
# biome-params merge, and the two build functions live in core/scatter_build.py so the panel
# operators and the biome/MCP path share one copy (subtract-duplication; docs/UX-REDESIGN.md).
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
# for the first real biome so a fresh enum resolves to a real recipe, not the NONE fallback (S2).
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
    bl_order = 4  # pipeline stage: Scatter, after Paths (docs/UX-REDESIGN.md section 4)
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
        helpers.context_header(layout, "Active mesh", hdr, icon="OUTLINER_OB_MESH",
                                  empty="Pick an emitter to scatter on.")

        layout.prop(scn, "emitter")
        layout.prop(scn, "camera")

        # S5: no standalone Make Proxies here. Both scatter_add and scatter_biome_scatter call
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
        # S7: no STRUCTURAL_ICON on the caption; the structural_action button in this box already
        # carries it, so it would show twice a few pixels apart.
        box.label(text="Structural (Build to apply)")
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
        helpers.structural_action(box, "bob_blender_tools.scatter_build_active",
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
