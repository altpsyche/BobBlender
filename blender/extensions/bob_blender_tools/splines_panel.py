"""Paths (BobSplines): typed curves that drive terrain and scatter, in-process over bbmcp.

The fourth authored subsystem (docs/SPLINES.md). A curve gets a ROLE and the role drives a
coordinated bundle of effects. C1 shipped the plumbing and the follow-terrain family (dirt path,
trail, road); C2 gives terrain shape its own standalone GN overlay:

- Terrain shape (C2): each curve carves a bench through the terrain via its OWN curve_overlay
  modifier stacked on the terrain object (docs/SPLINES.md 4.3). One modifier per curve, so a
  network of paths composes instead of the single inline path C1 used. The overlay reads the
  incoming terrain geometry, so it works on any terrain mesh, and writes the curve mask
  attributes (bbt_curve_mask / bbt_curve_dist) that the shader and scatter READ, so a downstream
  effect never re-solves proximity or duplicates a knob (docs/SPLINES.md 9 #2 / #4).
- Terrain material (C3): a surface band along the curve. Build configures a terrain-material
  layer keyed to the overlay's bbt_curve_mask (materials.apply_curve_surface, the same shape as
  the Flow mask keying a riverbed layer), so a road/dirt surface reads only along the path with
  no re-solved proximity. One shared curve-surface layer for now (all paths share it); distinct
  per-role surfaces need distinct mask attributes, a later step.
- Scatter (C4): a curve drives scatter through the baked bbt_curve_mask, not a scn.path proximity.
  The scatter recipe reads the mask per layer (clear a trail / keep-only along the band), so every
  curve with an overlay is respected at once (multi-curve). A layer can also switch to the
  scatter_along recipe to place instances ALONG a curve (fence posts), optionally aligned. The
  Paths Scatter channel flips unbound layers to clear and rebuilds; per-layer modes live in Scatter.

Ownership (docs/SPLINES.md section 9, confirmed): bbt_curve on the curve object holds ONLY
structural fields (role + which channels are on). The cross-section knobs (Path Width / Falloff /
Depth) live ONCE on the curve's overlay modifier and snapshot-restore across a rebuild like any
GN knob, so nothing here duplicates a width/depth knob. The list binds each row to its curve by
PointerProperty (section 9 #10), not by name, so it survives renames.

At Build the curve is draped onto the terrain (when the terrain carries a baked heightmap) so its
Z follows the ground; without a heightmap the curve's authored Z is used. Cross-section profiles
that make road/trail structurally distinct (a road bench + shoulders, embankments) and the
side/tangent field for them arrive with their consumers; C2 roles differ by knob defaults and the
profile is symmetric.
"""

import bpy
from bpy.props import (
    BoolProperty,
    CollectionProperty,
    EnumProperty,
    IntProperty,
    PointerProperty,
    StringProperty,
)
from bpy.types import Operator, Panel, PropertyGroup, UIList

from . import server, ui_helpers

# The cross-section knobs the panel draws live off the curve's overlay modifier (the single owner).
_PATH_KNOBS = ["Path Width", "Path Falloff", "Path Depth"]

# Curve overlay modifiers are named per curve so a terrain can carry several, and so removing a
# curve finds and drops exactly its modifier.
_OVERLAY_PREFIX = "BOB_Curve_"

# Subdivisions per segment the curve evaluates to (Curve to Mesh honours resolution_u). High so the
# carved bench reads smooth: a coarse curve evaluates to a few straight segments whose junctions
# facet the bench into steps. Set on the datablock at Build.
_CURVE_RES = 128

# Roles: the follow-terrain family. Each seeds the overlay grade knobs at first Build (SEED vs
# OWN: the scene owns them after, via snapshot-restore). Icons are standard IPO/curve icons, so
# they are always present (the reason scatter uses mesh-add icons). C2 roles differ only in these
# defaults; the cross-section profiles that make them structurally distinct (a road bench +
# shoulders, etc.) arrive with their consumers (docs/SPLINES.md 4.3). The scatter clear reuses the
# scatter layer's own Path Width (the existing effect), so no scatter width is seeded here.
# surface / surface_rough seed the curve-surface material layer (C3) when the Material channel is
# on: the band colour/roughness a path reads along its length.
ROLES = {
    "dirt_path": {
        "label": "Dirt Path", "icon": "IPO_EASE_IN_OUT",
        "desc": "A shallow worn trail draped onto the ground",
        "path_width": 2.4, "path_falloff": 3.5, "path_depth": 0.3,
        "surface": (0.13, 0.10, 0.07, 1.0), "surface_rough": 0.9,
    },
    "trail": {
        "label": "Trail", "icon": "IPO_LINEAR",
        "desc": "A narrow footpath, barely recessed",
        "path_width": 1.2, "path_falloff": 2.0, "path_depth": 0.15,
        "surface": (0.17, 0.14, 0.10, 1.0), "surface_rough": 0.9,
    },
    "road": {
        "label": "Road", "icon": "MOD_CURVE",
        "desc": "A wide graded track (a flat bench + shoulders arrive later)",
        "path_width": 4.5, "path_falloff": 4.0, "path_depth": 0.4,
        "surface": (0.22, 0.21, 0.20, 1.0), "surface_rough": 0.8,
    },
}


# Helpers
def _apply(ops):
    """Run bbmcp ops in-process, the path the terrain and scatter panels build through."""
    server._ensure_path()
    from bbmcp.dispatch import apply_op

    return [apply_op(op) for op in ops]


def _unique_object_name(base):
    name, i = base, 1
    while name in bpy.data.objects:
        i += 1
        name = f"{base}.{i:03d}"
    return name


def _active_entry(context):
    scn = context.scene.bbt_curves
    if not scn.curves:
        return None
    idx = max(0, min(scn.active, len(scn.curves) - 1))
    return scn.curves[idx]


def _active_curve(context):
    entry = _active_entry(context)
    return entry.curve if entry is not None and entry.curve is not None else None


def _terrain(context):
    """The terrain mesh the curves carve and scatter on: the panel's pick, else the Scatter
    emitter, else the active mesh (the same fall-through Apply Biome uses)."""
    scn = context.scene.bbt_curves
    if scn.terrain is not None and scn.terrain.type == "MESH":
        return scn.terrain
    scn_scatter = getattr(context.scene, "bbt_scatter", None)
    emitter = getattr(scn_scatter, "emitter", None) if scn_scatter is not None else None
    if emitter is not None and emitter.type == "MESH":
        return emitter
    obj = context.active_object
    return obj if obj is not None and obj.type == "MESH" else None


def _has_bake(terrain):
    """A terrain built by the bake operator carries its build params, so the curve can be draped
    onto it. Without one the overlay still carves, using the curve's authored Z."""
    return terrain is not None and terrain.get("bbt_heightmap")


def _drape_params(terrain):
    """The values drape_curve needs, matching heightmap_terrain's displace (path_curve._surface_z)."""
    return {"heightmap": terrain.get("bbt_heightmap", ""),
            "size": float(terrain.get("bbt_terrain_size", 90.0)),
            "height": float(terrain.get("bbt_terrain_height", 22.0)),
            "sea_level": float(terrain.get("bbt_terrain_sea", 0.22))}


def _default_points(terrain):
    """A short straight line of control points at the origin for a freshly added curve, sized to
    the terrain when one is picked so it is visible. The artist then shapes it in edit mode."""
    span = float(terrain.get("bbt_terrain_size", 20.0)) / 3.0 if terrain is not None else 8.0
    return [(-span, 0.0, 0.0), (-span / 3.0, 0.0, 0.0),
            (span / 3.0, 0.0, 0.0), (span, 0.0, 0.0)]


def _overlay_name(curve):
    return _OVERLAY_PREFIX + curve.name


def _overlay_mod(terrain, curve):
    """The curve's overlay modifier on the terrain, or None."""
    if terrain is None or curve is None:
        return None
    name = _overlay_name(curve)
    return next((m for m in terrain.modifiers if m.type == "NODES" and m.name == name), None)


def _position_overlay(terrain, curve):
    """Keep the curve overlay after the base terrain modifier and before BOB_Snow, so it carves
    the displaced surface and snow settles on the carved result (build_geonodes_on_object appends
    a fresh modifier to the end)."""
    mod = _overlay_mod(terrain, curve)
    snow = next((m for m in terrain.modifiers if m.name.startswith("BOB_Snow")), None)
    if mod is None or snow is None:
        return
    i = list(terrain.modifiers).index(mod)
    j = list(terrain.modifiers).index(snow)
    if i > j:
        terrain.modifiers.move(i, j)


def _build_curve_overlay(terrain, curve, carve=True):
    """Drape the curve onto the terrain (when it has a bake), then build/update the curve's overlay
    modifier. The overlay always writes the bbt_curve_mask attribute; carve=False makes it mask-only
    (no displacement), for a curve that drives material/scatter but not the terrain shape. Returns a
    short note on the drape source."""
    # Densify the curve evaluation so the carved bench is smooth (see _CURVE_RES). On the datablock,
    # so scatter's proximity reads the same smooth centreline.
    if curve.type == "CURVE" and curve.data.resolution_u < _CURVE_RES:
        curve.data.resolution_u = _CURVE_RES
    role = ROLES.get(curve.bbt_curve.role, ROLES["dirt_path"])
    draped = False
    if _has_bake(terrain):
        _apply([{"op": "drape_curve", "name": curve.name, **_drape_params(terrain)}])
        draped = True
    server._ensure_path()
    from bbmcp.geonodes import build_geonodes_on_object

    params = {"curve": curve.name, "carve": carve, "path_width": role["path_width"],
              "path_falloff": role["path_falloff"], "path_depth": role["path_depth"]}
    build_geonodes_on_object(terrain, "curve_overlay", _overlay_name(curve), params)
    _position_overlay(terrain, curve)
    return "draped" if draped else "curve Z"


def _apply_curve_material(terrain, role):
    """Configure the terrain material's curve-surface layer from the role (BobSplines C3). Returns
    the configured slot index, or None when the terrain has no Terrain BobShader to key."""
    if terrain is None:
        return None
    mat = terrain.active_material
    if mat is None:
        return None
    server._ensure_path()
    from bbmcp import materials

    return materials.apply_curve_surface(mat, role["surface"], role.get("surface_rough", 0.85))


def _clear_scatter(context):
    """Make the emitter's surface scatter avoid the paths: flip any layer still set to no curve
    binding to "clear" (so it reads the curve mask), leave explicit keep/along/clear layers alone,
    then rebuild the layers through the scatter panel's own build. Scatter reads the baked
    bbt_curve_mask (BobSplines C4), so every curve with an overlay is cleared at once, no scn.path.
    Returns True when it ran."""
    scn_scatter = getattr(context.scene, "bbt_scatter", None)
    emitter = getattr(scn_scatter, "emitter", None) if scn_scatter is not None else None
    coll = emitter.bbt_scatter_coll if emitter is not None else None
    if emitter is None or coll is None or not coll.objects:
        return False
    for obj in coll.objects:
        lay = obj.bbt_scatter_layer
        if lay.curve_mode == "none":
            lay.curve_mode = "clear"
    bpy.ops.bob_blender_tools.scatter_build_all()
    return True


# Data model
def _curve_poll(self, obj):
    return obj.type == "CURVE"


def _terrain_poll(self, obj):
    return obj.type == "MESH"


class BBT_Curve(PropertyGroup):
    """Structural config, stored on the curve object (docs/SPLINES.md 4.1 + section 9 #2): the
    role and which channels are on. NO cross-section knobs live here; those live on the curve's
    overlay modifier and snapshot-restore, so there is no panel-vs-modifier drift."""

    role: EnumProperty(
        name="Role",
        items=[(k, v["label"], v["desc"], v["icon"], i) for i, (k, v) in enumerate(ROLES.items())],
        default="dirt_path",
        description="What this curve is; seeds the carve + clear defaults at first Build")
    do_terrain: BoolProperty(
        name="Terrain shape", default=True,
        description="Carve a bench along the curve into the terrain (a per-curve overlay modifier)")
    do_material: BoolProperty(
        name="Material band", default=True,
        description="Add a surface band along the curve (a terrain-material layer keyed to the "
                    "curve mask); needs the terrain shaded as a Terrain BobShader")
    do_scatter: BoolProperty(
        name="Scatter", default=True,
        description="Clear scattered assets along the curve on the Scatter emitter")


class BBT_CurveEntry(PropertyGroup):
    """One row in the scene curve list: a pointer to the curve object (docs/SPLINES.md section 9
    #10, bind by pointer not name so it survives renames)."""

    curve: PointerProperty(name="Curve", type=bpy.types.Object, poll=_curve_poll)


class BBT_CurvesProps(PropertyGroup):
    """Scene-level UI state plus the curve list. The per-curve truth lives on the curve object's
    bbt_curve; this is only the list, the active row, and the shared terrain pick."""

    curves: CollectionProperty(type=BBT_CurveEntry)
    active: IntProperty(default=0)
    terrain: PointerProperty(
        name="Terrain", type=bpy.types.Object, poll=_terrain_poll,
        description="Terrain mesh the curves carve and scatter on "
                    "(defaults to the Scatter emitter, else the active mesh)")
    summary: StringProperty(default="")


# Operators
class BBT_OT_curve_add(Operator):
    bl_idname = "bob_blender_tools.curve_add"
    bl_label = "Add Curve"
    bl_description = "Add a typed curve of the chosen role, ready to shape in edit mode"
    bl_options = {"REGISTER", "UNDO"}

    role: EnumProperty(
        name="Role",
        items=[(k, v["label"], v["desc"], v["icon"], i) for i, (k, v) in enumerate(ROLES.items())])

    def execute(self, context):
        scn = context.scene.bbt_curves
        terrain = _terrain(context)
        spec = ROLES[self.role]
        name = _unique_object_name(spec["label"])
        # make_path is the shared curve authoring op (headless + here), so the panel and an agent
        # create the same datablock. A flat starter line; Build re-drapes it onto the terrain.
        _apply([{"op": "make_path", "name": name, "resolution": 12,
                 "points": _default_points(terrain)}])
        obj = bpy.data.objects.get(name)
        if obj is None:
            self.report({"ERROR"}, "curve not created")
            return {"CANCELLED"}
        obj.bbt_curve.role = self.role
        entry = scn.curves.add()
        entry.curve = obj
        scn.active = len(scn.curves) - 1
        # Make it the active object so the artist can Tab straight into edit mode to shape it.
        context.view_layer.objects.active = obj
        try:
            obj.select_set(True)
        except RuntimeError:
            pass
        self.report({"INFO"}, f"Added {spec['label']}; shape it in edit mode, then Build")
        return {"FINISHED"}


class BBT_OT_curve_remove(Operator):
    bl_idname = "bob_blender_tools.curve_remove"
    bl_label = "Remove Curve"
    bl_description = "Delete the active curve and drop the bench its overlay carved"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        scn = context.scene.bbt_curves
        entry = _active_entry(context)
        if entry is None:
            return {"CANCELLED"}
        curve = entry.curve
        # Drop the curve's overlay modifier first so its carve and mask contribution disappear.
        terrain = _terrain(context)
        mod = _overlay_mod(terrain, curve)
        if mod is not None:
            terrain.modifiers.remove(mod)
        idx = scn.active
        if curve is not None:
            bpy.data.objects.remove(curve, do_unlink=True)
        scn.curves.remove(idx)
        scn.active = max(0, min(scn.active, len(scn.curves) - 1))
        return {"FINISHED"}


class BBT_OT_curve_duplicate(Operator):
    bl_idname = "bob_blender_tools.curve_duplicate"
    bl_label = "Duplicate Curve"
    bl_description = "Copy the active curve, with its own curve data and role config"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        scn = context.scene.bbt_curves
        src = _active_curve(context)
        if src is None:
            return {"CANCELLED"}
        dup = src.copy()
        dup.data = src.data.copy()
        dup.name = _unique_object_name(src.name.rsplit(".", 1)[0])
        for coll in src.users_collection:
            coll.objects.link(dup)
        entry = scn.curves.add()
        entry.curve = dup
        scn.active = len(scn.curves) - 1
        return {"FINISHED"}


class BBT_OT_curve_build(Operator):
    bl_idname = "bob_blender_tools.curve_build"
    bl_label = "Build This Curve"
    bl_description = ("Apply the active curve's channels: carve a bench into the terrain (its own "
                      "overlay modifier), add the surface band, and clear scatter along it. Drapes "
                      "the curve onto the terrain first so it follows the ground")

    def execute(self, context):
        curve = _active_curve(context)
        if curve is None:
            self.report({"ERROR"}, "No active curve")
            return {"CANCELLED"}
        cfg = curve.bbt_curve
        role = ROLES.get(cfg.role, ROLES["dirt_path"])
        terrain = _terrain(context)
        did = []

        # Any channel needs the overlay's bbt_curve_mask; build it once (carve only when the Terrain
        # channel is on, else it is a mask-only overlay driving material/scatter).
        if cfg.do_terrain or cfg.do_material or cfg.do_scatter:
            if terrain is None:
                self.report({"WARNING"}, "Pick a terrain mesh")
            else:
                note = _build_curve_overlay(terrain, curve, carve=cfg.do_terrain)
                did.append(f"carved terrain ({note})" if cfg.do_terrain else "curve mask")

        if cfg.do_material:
            if _apply_curve_material(terrain, role) is not None:
                did.append("surface band")
            else:
                self.report({"WARNING"}, "Material band needs a Terrain BobShader (shade it in Shaders)")

        if cfg.do_scatter:
            if _clear_scatter(context):
                did.append("cleared scatter")
            else:
                self.report({"WARNING"}, "Scatter clear needs a Scatter emitter with layers")

        context.scene.bbt_curves.summary = \
            f"{role['label']}: {', '.join(did) or 'nothing (check channels)'}"
        self.report({"INFO"}, f"Built {curve.name}: {', '.join(did) or 'no channels applied'}")
        return {"FINISHED"}


class BBT_OT_curve_build_all(Operator):
    bl_idname = "bob_blender_tools.curve_build_all"
    bl_label = "Build All"
    bl_description = "Build every curve that has a channel on: overlays (carve/mask), surface band, scatter clear"

    def execute(self, context):
        scn = context.scene.bbt_curves
        terrain = _terrain(context)
        if terrain is None:
            self.report({"ERROR"}, "Pick a terrain mesh")
            return {"CANCELLED"}
        built = 0
        for entry in scn.curves:
            curve = entry.curve
            if curve is None:
                continue
            cfg = curve.bbt_curve
            if cfg.do_terrain or cfg.do_material or cfg.do_scatter:
                _build_curve_overlay(terrain, curve, carve=cfg.do_terrain)
                built += 1
        # Material band from the ACTIVE curve's role (one shared surface layer for now). Scatter
        # clear reads the accumulated bbt_curve_mask, so a single rebuild covers every curve.
        active = _active_curve(context)
        extra = []
        if active is not None and active.bbt_curve.do_material and \
                _apply_curve_material(terrain, ROLES.get(active.bbt_curve.role, ROLES["dirt_path"])) is not None:
            extra.append("surface band")
        if any(e.curve is not None and e.curve.bbt_curve.do_scatter for e in scn.curves) \
                and _clear_scatter(context):
            extra.append("cleared scatter")
        note = (", " + ", ".join(extra)) if extra else ""
        scn.summary = f"built {built} curve(s){note}"
        self.report({"INFO"}, f"Built {built} curve(s) on {terrain.name}{note}")
        return {"FINISHED"}


def _draw_mod_knobs(layout, mod, names):
    """Draw a specific modifier's live input values by socket name, skipping absent sockets."""
    if mod is None or mod.node_group is None:
        return
    ids = {it.name: it.identifier for it in mod.node_group.interface.items_tree
           if getattr(it, "item_type", None) == "SOCKET" and it.in_out == "INPUT"}
    col = layout.column(align=True)
    for nm in names:
        ident = ids.get(nm)
        inp = getattr(mod.properties.inputs, ident, None) if ident else None
        if inp is not None:
            col.prop(inp, "value", text=nm)


class BBT_UL_curves(UIList):
    def draw_item(self, context, layout, data, item, icon, active_data, active_prop, index):
        curve = item.curve
        row = layout.row(align=True)
        if curve is None:
            row.label(text="(missing curve)", icon="ERROR")
            return
        spec = ROLES.get(curve.bbt_curve.role, ROLES["dirt_path"])
        row.label(text=curve.name, icon=spec["icon"])
        row.prop(curve, "hide_viewport", text="", emboss=False,
                 icon="HIDE_ON" if curve.hide_viewport else "HIDE_OFF")


class BBT_PT_paths(Panel):
    bl_label = "Paths"
    bl_idname = "BBT_PT_paths"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "BobBlenderTools"
    bl_order = 2  # pipeline stage 2: after Terrain, before Scatter (docs/SPLINES.md 5)
    bl_options = {"DEFAULT_CLOSED"}

    def draw(self, context):
        scn = context.scene.bbt_curves
        layout = self.layout

        curve = _active_curve(context)
        hdr = None
        if curve is not None:
            spec = ROLES.get(curve.bbt_curve.role, ROLES["dirt_path"])
            hdr = f"{curve.name} ({spec['label']})"
        ui_helpers.context_header(layout, "Path", hdr, icon="CURVE_DATA",
                                  empty="Add a curve to shape a path.")

        layout.prop(scn, "terrain")
        terrain = _terrain(context)
        if terrain is not None and not _has_bake(terrain):
            layout.label(text=f"{terrain.name} has no bake; carving uses the curve's own Z",
                         icon="INFO")

        row = layout.row()
        row.template_list("BBT_UL_curves", "", scn, "curves", scn, "active", rows=3)
        col = row.column(align=True)
        col.operator_menu_enum("bob_blender_tools.curve_add", "role", text="", icon="ADD")
        col.operator("bob_blender_tools.curve_remove", text="", icon="REMOVE")
        col.operator("bob_blender_tools.curve_duplicate", text="", icon="DUPLICATE")

        if scn.curves:
            ui_helpers.structural_action(layout, "bob_blender_tools.curve_build_all",
                                         note="carves every terrain-channel curve")
        if scn.summary:
            layout.label(text=scn.summary, icon="INFO")


class BBT_PT_paths_active(Panel):
    bl_label = "Active Path"
    bl_idname = "BBT_PT_paths_active"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "BobBlenderTools"
    bl_parent_id = "BBT_PT_paths"

    def draw(self, context):
        layout = self.layout
        curve = _active_curve(context)
        if curve is None:
            layout.label(text="Add or pick a curve to edit it.", icon="INFO")
            return
        cfg = curve.bbt_curve
        spec = ROLES.get(cfg.role, ROLES["dirt_path"])
        layout.label(text=spec["label"], icon=spec["icon"])

        # Structural group (P3): role + channels apply on a Build, not from a callback.
        box = layout.box()
        box.label(text="Structural (Build to apply)", icon=ui_helpers.STRUCTURAL_ICON)
        box.prop(cfg, "role")
        box.prop(cfg, "do_terrain")
        box.prop(cfg, "do_material")
        box.prop(cfg, "do_scatter")
        ui_helpers.structural_action(box, "bob_blender_tools.curve_build",
                                     note="drapes + carves, adds the surface band, clears scatter")

        # Live group (P3): the cross-section knobs live on the curve's overlay modifier (the single
        # owner), edited in place. They appear once this curve has carved the terrain.
        terrain = _terrain(context)
        overlay = _overlay_mod(terrain, curve)
        layout.label(text="Cross-section (lives on the overlay, instant)")
        if overlay is None or overlay.node_group is None:
            layout.label(text="Build this curve's terrain carve to tune it", icon="INFO")
            return
        _draw_mod_knobs(layout, overlay, _PATH_KNOBS)


CLASSES = (
    BBT_Curve,
    BBT_CurveEntry,
    BBT_CurvesProps,
    BBT_OT_curve_add,
    BBT_OT_curve_remove,
    BBT_OT_curve_duplicate,
    BBT_OT_curve_build,
    BBT_OT_curve_build_all,
    BBT_UL_curves,
    BBT_PT_paths,
    BBT_PT_paths_active,
)


def register():
    for cls in CLASSES:
        bpy.utils.register_class(cls)
    bpy.types.Object.bbt_curve = PointerProperty(type=BBT_Curve)
    bpy.types.Scene.bbt_curves = PointerProperty(type=BBT_CurvesProps)


def unregister():
    del bpy.types.Scene.bbt_curves
    del bpy.types.Object.bbt_curve
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
