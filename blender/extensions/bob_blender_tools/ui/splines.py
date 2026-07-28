"""Paths (BobSplines): typed curves that drive terrain and scatter, in-process over bbmcp.

The fourth authored subsystem (docs/SPLINES.md). A curve gets a ROLE and the role drives a
coordinated bundle of effects. the first pass shipped the plumbing and the follow-terrain family (dirt path,
trail, road); the terrain overlay gives terrain shape its own standalone GN overlay:

- Terrain shape (the terrain overlay): each curve carves a bench through the terrain via its OWN curve_overlay
 modifier stacked on the terrain object (docs/SPLINES.md 4.3). One modifier per curve, so a
 network of paths composes instead of the single inline path the first pass used. The overlay reads the
 incoming terrain geometry, so it works on any terrain mesh, and writes the curve mask
 attributes (bbt_curve_mask / bbt_curve_dist) that the shader and scatter READ, so a downstream
 effect never re-solves proximity or duplicates a knob (docs/SPLINES.md 9 #2 / #4).
- Terrain material (the material band): a surface band along the curve. Build configures a terrain-material
 layer keyed to the overlay's bbt_curve_mask (materials.apply_curve_surface, the same shape as
 the Flow mask keying a riverbed layer), so a road/dirt surface reads only along the path with
 no re-solved proximity. One shared curve-surface layer for now (all paths share it); distinct
 per-role surfaces need distinct mask attributes, a later step.
- Scatter (the scatter mask): a curve drives scatter through the baked bbt_curve_mask, not a scn.path proximity.
 The scatter recipe reads the mask per layer (clear a trail / keep-only along the band), so every
 curve with an overlay is respected at once (multi-curve). A layer can also switch to the
 scatter_along recipe to place instances ALONG a curve (fence posts), optionally aligned. The
 Paths Scatter channel flips unbound layers to clear and rebuilds; per-layer modes live in Scatter.
- Water (the water channel): the IMPOSE family (river / stream). Unlike a path, a river runs monotonically DOWNHILL
 and the terrain conforms DOWN to it: drape_curve solves a monotonic descending centreline and the
 overlay's impose mode carves the bed to it (docs/SPLINES.md 9 #1). A water-surface ribbon
 (curve_water, its own BOB_Water_<curve> object) sits in the channel, shaded by the water BobShader
 (materials.water_master: flowing, depth-tinted, foaming, transparent, freezes below 0 C). The bed
 reads damp via bbt_curve_wet routed into the terrain wetness path (materials.apply_curve_wet).

Ownership (docs/SPLINES.md section 9, confirmed): bbt_curve on the curve object holds ONLY
structural fields (role + which channels are on). The cross-section knobs (Path Width / Falloff /
Depth) live ONCE on the curve's overlay modifier and snapshot-restore across a rebuild like any
GN knob, so nothing here duplicates a width/depth knob. The list binds each row to its curve by
PointerProperty (section 9 #10), not by name, so it survives renames.

At Build the curve is draped onto the terrain (when the terrain carries a baked heightmap) so its
Z follows the ground; without a heightmap the curve's authored Z is used. Cross-section profiles
that make road/trail structurally distinct (a road bench + shoulders, embankments) and the
side/tangent field for them arrive with their consumers; the terrain overlay roles differ by knob defaults and the
profile is symmetric.

The role presets, the overlay/water/material builders, the live param sync and the MCP op handlers
all live in core/splines_build (shared with the dispatch handlers, subtract-duplication; docs/
UX-REDESIGN.md). This module keeps the panel: the curve list + terrain pick, the property groups,
the operators (which resolve context, run the injected scatter/host-bake callbacks the pure core
cannot, write the scene summary, self.report, and update the view layer), and the panel draw.
"""

import bpy
from bpy.props import (
    BoolProperty,
    CollectionProperty,
    EnumProperty,
    FloatProperty,
    IntProperty,
    PointerProperty,
    StringProperty,
)
from bpy.types import Operator, Panel, PropertyGroup, UIList

from ..bridge import server
from ..core import splines_build
from ..core.splines_build import (
    ROLES,
    build_curve,
    run_bake_erode,
    run_revert_erode,
    seed_role_params,
    sync_curve_params,
)
from . import helpers

# Role presets + the overlay / water / material builders and the terrain helpers live in
# core/splines_build (shared with the MCP ops). Bound here under their old names so the operators
# and the panel read unchanged.
_apply = splines_build._apply
_unique_object_name = splines_build._unique_object_name
_default_points = splines_build._default_points
_has_bake = splines_build._has_bake
_curve_off_terrain = splines_build._curve_off_terrain
_overlay_name = splines_build._overlay_name
_water_obj = splines_build._water_obj
_build_curve_overlay = splines_build._build_curve_overlay
_apply_curve_material = splines_build._apply_curve_material
_build_water = splines_build._build_water


# Helpers that stay in the ui layer: they read the panel's context/scene state, or run a sibling
# ui operator that pure core must not reach into.
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


def _clear_scatter(context):
    """Make the emitter's surface scatter avoid the paths: flip any layer still set to no curve
 binding to "clear" (so it reads the curve mask), leave explicit keep/along/clear layers alone,
 then rebuild the layers through the scatter panel's own build. Scatter reads the baked
 bbt_curve_mask (BobSplines, the scatter mask), so every curve with an overlay is cleared at once, no scn.path.
 Returns True when it ran. This runs a ui operator, so it stays in the ui layer and is passed
 into the core builders as the scatter_cb callback."""
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


# Live param sync callbacks: bbt_curve is the single owner of the shape params; editing one pushes
# it to BOTH the terrain-carve overlay and the water ribbon (core.sync_curve_params). The _syncing
# guard (owned by core) suppresses the per-prop sync while seed_role_params sets a whole role at once.
def _sync_cb(self, context):
    if not splines_build._syncing:
        sync_curve_params(_terrain(context), self.id_data)


def _seed_cb(self, context):
    seed_role_params(self.id_data, _terrain(context))


# Data model
def _curve_poll(self, obj):
    return obj.type == "CURVE"


def _terrain_poll(self, obj):
    return obj.type == "MESH"


class BBT_Curve(PropertyGroup):
    """All per-curve config, on the curve object (docs/SPLINES.md 4.1). Two kinds:

 - STRUCTURAL (role + which channels are on): applied on Build, since they rebuild the modifiers.
 - SHAPE (width/depth/...): the single owner of the cross-section + water params. Each has an
 update callback that live-syncs it to BOTH the terrain-carve overlay and the water ribbon
 (_sync_cb -> core.sync_curve_params), so one set of numbers drives both and there is no
 panel-vs-modifier drift. width is the FULL channel width (1:1); depth the channel depth;
 water_level the fill."""

    role: EnumProperty(
        name="Role",
        items=[(k, v["label"], v["desc"], v["icon"], i) for i, (k, v) in enumerate(ROLES.items())],
        default="dirt_path", update=_seed_cb,
        description="What this curve is; picking a role re-seeds the shape defaults")
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
    do_water: BoolProperty(
        name="Water surface", default=True,
        description="Lay a water-surface ribbon in the carved channel (river / stream roles only), "
                    "shaded as a water BobShader; ignored by the follow-terrain roles")

    # Set by Bake & Erode, cleared by a fresh Build: when on, the channel banks are shaped by the
    # eroded heightfield (not the swept overlay embankment), so the overlay only carves a SHALLOW wet
    # bed to guarantee containment and the graded shoulder/bank is dropped. See core.sync_curve_params.
    banks_from_erosion: BoolProperty(default=False)

    # Shape params (live; synced to the overlay + water ribbon by _sync_cb).
    width: FloatProperty(
        name="Width", default=4.8, min=0.1, soft_max=30.0, update=_sync_cb,
        description="Full channel width in metres. Drives both the carved bed and the water ribbon")
    depth: FloatProperty(
        name="Depth", default=0.3, min=0.0, soft_max=8.0, update=_sync_cb,
        description="Channel depth: how far the bed is carved below the rim. Changing it re-carves "
                    "the terrain AND repositions the water")
    falloff: FloatProperty(
        name="Falloff", default=3.5, min=0.0, soft_max=20.0, update=_sync_cb,
        description="Width over which the bank blends back to the surrounding terrain")
    taper: FloatProperty(
        name="End Taper", default=2.0, min=0.0, soft_max=20.0, update=_sync_cb,
        description="Fade the channel and the water over the last N metres at each end")
    shoulder: FloatProperty(
        name="Shoulder", default=0.0, min=0.0, soft_max=10.0, update=_sync_cb,
        description="A flat shoulder that extends the bed beyond the channel width")
    # Verge band (item-8): the scatter edge ring a Verge layer reads. Not role-seeded (kept off
    # _SHAPE_KEYS), so a role change preserves the artist's verge tuning.
    verge_gap: FloatProperty(
        name="Verge Gap", default=0.0, min=0.0, soft_max=10.0, update=_sync_cb,
        description="Clear metres out from the path edge before the verge band starts (a hedgerow "
                    "set back from the road). A Verge scatter layer reads this band")
    verge_width: FloatProperty(
        name="Verge Width", default=1.5, min=0.0, soft_max=15.0, update=_sync_cb,
        description="Width of the verge band a Verge scatter layer scatters within")
    verge_side: FloatProperty(
        name="Verge Side", default=0.0, min=-1.0, max=1.0, update=_sync_cb,
        description="Which side of the path the verge is on: -1 left only, 0 both, +1 right only")
    bank_slope: FloatProperty(
        name="Bank Slope", default=1.0, min=0.05, soft_max=4.0, update=_sync_cb,
        description="Rise/run of the banks: lower is wider and gentler")
    bank_bias: FloatProperty(
        name="Bank Bias", default=0.0, min=-1.0, max=1.0, update=_sync_cb,
        description="Skew the embankment to one side of the curve")
    bank_height: FloatProperty(
        name="Bank Height", default=0.4, min=0.0, soft_max=4.0, update=_sync_cb,
        description="River/stream: how far the banks rise ABOVE the water, so it stays contained "
                    "even where the channel runs across a slope")
    water_level: FloatProperty(
        name="Water Level", default=0.6, min=0.0, max=1.0, update=_sync_cb,
        description="River/stream: how full the channel is (0 = empty bed, 1 = brim full)")
    flow: FloatProperty(
        name="Flow", default=1.0, min=0.0, soft_max=4.0, update=_sync_cb,
        description="River/stream: base flow speed that scrolls the water ripples downstream")
    foam_bank: FloatProperty(
        name="Foam (banks)", default=0.5, min=0.0, max=1.0, update=_sync_cb,
        description="River/stream: foam intensity near the banks")
    foam_rapids: FloatProperty(
        name="Foam (rapids)", default=1.0, min=0.0, max=1.0, update=_sync_cb,
        description="River/stream: foam on steep white-water sections")
    wave_amp: FloatProperty(
        name="Wave Height", default=0.10, min=0.0, soft_max=0.6, update=_sync_cb,
        description="River/stream: Gerstner wave height in metres (real animated crest/trough "
                    "displacement of the water surface). 0 = a flat sheet")
    wave_len: FloatProperty(
        name="Wave Length", default=4.5, min=0.2, soft_max=20.0, update=_sync_cb,
        description="River/stream: spacing between wave crests in metres")
    wave_steep: FloatProperty(
        name="Wave Steepness", default=0.4, min=0.0, max=1.0, update=_sync_cb,
        description="River/stream: 0 rounded swells, 1 peaked crests (the Gerstner trochoid)")
    wave_speed: FloatProperty(
        name="Wave Speed", default=0.6, min=0.0, soft_max=3.0, update=_sync_cb,
        description="River/stream: how fast the wave crests travel downstream (the Gerstner phase "
                    "speed); 0 = standing waves")
    wave_chop: FloatProperty(
        name="Wave Chop", default=0.7, min=0.0, max=1.0, update=_sync_cb,
        description="River/stream: how much the crests meander (breaks the regular wave lattice "
                    "into natural chop); 0 = clean parallel swells")
    width_var: FloatProperty(
        name="Width Variation", default=0.0, min=0.0, max=0.95, update=_sync_cb,
        description="How much the channel width wanders along the spline (0 = a dead-parallel strip). "
                    "A slow meander widens and narrows the bed AND the water ribbon together, so the "
                    "banks read as a natural river instead of a ruled band")


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
    # Bake & Erode (commit step): fold the curve carves into the terrain heightfield and weather them.
    erode_strength: FloatProperty(
        name="Erosion", default=0.5, min=0.0, max=1.0,
        description="How hard to weather the carved channels: slumps the hard banks to a natural "
                    "repose angle and cuts small tributaries. Higher = more erosion")
    erode_scope: EnumProperty(
        name="Scope", default="band",
        items=[("band", "Carved band", "Erode only the channels + banks, leaving the rest of the "
                "sculpted terrain untouched"),
               ("global", "Whole terrain", "Re-erode the entire terrain with the channels present, "
                "so rivers cut natural valleys and tributaries (dramatic; the base landform shifts)")],
        description="Erode only the channel band, or re-erode the whole terrain")
    erode_deposit: BoolProperty(
        name="Deposit bars", default=True,
        description="After incision, settle sediment where the flow slackens (high drainage, low "
                    "slope): alluviates the valley floor into a flatter floodplain and grows gentle "
                    "point bars on the inner bends, so the channel is not a bare cut V")
    erode_summary: StringProperty(default="")


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
        seed_role_params(obj, terrain)  # seed the shape defaults for this role (live from now on)
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
        name = curve.name if curve is not None else "(missing)"
        had_scatter = curve is not None and curve.bbt_curve.do_scatter
        # Drop the curve's overlay modifier first so its carve and mask contribution disappear. Search
        # ALL meshes for it, not just the current terrain pick: if the pick changed since Build, the
        # overlay lives on the OTHER terrain and would otherwise be orphaned there, carving forever.
        oname = _overlay_name(curve) if curve is not None else None
        if oname is not None:
            for obj in bpy.data.objects:
                m = next((md for md in obj.modifiers if md.type == "NODES" and md.name == oname), None)
                if m is not None:
                    obj.modifiers.remove(m)
        # Drop the river's water-surface ribbon object too, if it has one.
        water = _water_obj(curve)
        if water is not None:
            bpy.data.objects.remove(water, do_unlink=True)
        idx = scn.active
        if curve is not None:
            bpy.data.objects.remove(curve, do_unlink=True)
        scn.curves.remove(idx)
        scn.active = max(0, min(scn.active, len(scn.curves) - 1))
        # Rebuild scatter so a layer that was cleared along this curve's (now gone) mask fills back in;
        # without this the emitter keeps a bald strip where the deleted path used to be.
        rescattered = had_scatter and _clear_scatter(context)
        context.view_layer.update()
        self.report({"INFO"},
                    f"Removed {name}" + (", rebuilt scatter" if rescattered else ""))
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
        self.report({"INFO"}, f"Duplicated to {dup.name}; shape it and Build")
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
        # The pure carve/material/water/sync is in core.build_curve; the panel injects the scatter
        # clear (a ui operator core must not call) and keeps the summary / report / view-layer update.
        res = build_curve(curve, terrain, do_terrain=cfg.do_terrain, do_material=cfg.do_material,
                          do_water=cfg.do_water, do_scatter=cfg.do_scatter,
                          scatter_cb=lambda: _clear_scatter(context))
        if res["error"] is not None:
            # The only blocking error is a missing terrain; keep the original panel wording.
            self.report({"ERROR"}, "Pick a terrain mesh in the Paths panel first")
            return {"CANCELLED"}
        for warn in res["warnings"]:
            self.report({"WARNING"}, warn)
        # Rebuild the dependency graph so the overlay relinks to the (edited/re-draped) curve; a
        # stale link is why an edit or re-bake needed a delete + re-add before, not just a Build.
        context.view_layer.update()
        context.scene.bbt_curves.summary = \
            f"{role['label']}: {', '.join(res['did']) or 'nothing (check channels)'}"
        self.report({"INFO"}, f"Built {curve.name}: {', '.join(res['did']) or 'no channels applied'}")
        return {"FINISHED"}


class BBT_OT_curve_build_all(Operator):
    bl_idname = "bob_blender_tools.curve_build_all"
    bl_label = "Build All"
    bl_description = ("Build every curve that has a channel on: overlays (carve/mask), surface band "
                      "or damp bed, water ribbons, scatter clear")

    def execute(self, context):
        scn = context.scene.bbt_curves
        terrain = _terrain(context)
        if terrain is None:
            self.report({"ERROR"}, "Pick a terrain mesh")
            return {"CANCELLED"}
        built, watered = 0, 0
        for entry in scn.curves:
            curve = entry.curve
            if curve is None:
                continue
            cfg = curve.bbt_curve
            cfg.banks_from_erosion = False  # a fresh build re-imposes the graded channel (until Erode)
            impose = ROLES.get(cfg.role, ROLES["dirt_path"]).get("family") == "impose"
            built_any = False
            if cfg.do_terrain or cfg.do_material or cfg.do_scatter or (cfg.do_water and impose):
                _build_curve_overlay(terrain, curve, carve=cfg.do_terrain)
                built += 1
                built_any = True
            # Water ribbon after the overlay drape, for the impose family only.
            if cfg.do_water and impose and _build_water(curve) is not None:
                watered += 1
                built_any = True
            if built_any:  # push the shape params onto the freshly built modifiers (live thereafter)
                sync_curve_params(terrain, curve)
        # Material: for the follow family, one surface layer per DISTINCT class among the do_material
        # curves (the per-role surfaces), deduped by channel, so a paved road and a dirt trail read differently. For the
        # impose family (river/stream) it is the damp bed (apply_curve_wet, idempotent/non-lowering),
        # so it just applies per curve. Scatter clear reads the accumulated mask, one rebuild covers all.
        extra = []
        seen_channels, surfaced, wetted = set(), 0, False
        for entry in scn.curves:
            c = entry.curve
            if c is None or not c.bbt_curve.do_material:
                continue
            role = ROLES.get(c.bbt_curve.role, ROLES["dirt_path"])
            if role.get("family") == "impose":
                if _apply_curve_material(terrain, role) is not None:
                    wetted = True
                continue
            ch = role.get("surface_channel", "a")
            if ch in seen_channels:
                continue
            if _apply_curve_material(terrain, role) is not None:
                seen_channels.add(ch)
                surfaced += 1
        if surfaced:
            extra.append(f"{surfaced} surface band(s)")
        if wetted:
            extra.append("damp bed")
        if watered:
            extra.append(f"{watered} water surface(s)")
        if any(e.curve is not None and e.curve.bbt_curve.do_scatter for e in scn.curves) \
                and _clear_scatter(context):
            extra.append("cleared scatter")
        # Relink the dependency graph so every overlay re-evaluates its current curve (see the note
        # in Build This Curve): a stale terrain->curve link was why a delete + re-add was needed.
        context.view_layer.update()
        note = (", " + ", ".join(extra)) if extra else ""
        scn.summary = f"built {built} curve(s){note}"
        self.report({"INFO"}, f"Built {built} curve(s) on {terrain.name}{note}")
        return {"FINISHED"}


class BBT_OT_curve_bake_erode(Operator):
    bl_idname = "bob_blender_tools.curve_bake_erode"
    bl_label = "Bake & Erode Curves"
    bl_description = ("Commit: run the heightfield erosion on the terrain heightfield to weather the landscape "
                      "the curves shape, then RE-IMPOSE every curve on the eroded terrain (re-drape, "
                      "carve the channel + banks, rebuild the water) so bed, banks and water re-derive "
                      "together and the water stays contained -- no gap. Rerun after editing curves or "
                      "the terrain")
    bl_options = {"REGISTER"}

    def execute(self, context):
        scn = context.scene.bbt_curves
        terrain = _terrain(context)
        if terrain is None:
            self.report({"ERROR"}, "Pick a terrain mesh")
            return {"CANCELLED"}
        # _run_host_bake lives in the package __init__ (it spawns the host erosion process), so it
        # cannot move into pure core; inject it as the host_bake callback. Lazy import so a reload of
        # core alone does not need it re-imported.
        from .. import _run_host_bake

        def host_bake(out_abs, params):
            return _run_host_bake(context, out_abs, params=params, maps=True)

        curves = [e.curve for e in scn.curves if e.curve is not None]
        erode_params = {"strength": float(scn.erode_strength), "scope": scn.erode_scope,
                        "deposit": scn.erode_deposit, "seed": 0}
        res = run_bake_erode(terrain, curves, erode_params, host_bake=host_bake, scatter_cb=None)
        if res["error"] is not None:
            # "No curves ..." is an empty-selection warning, not a hard error, like before.
            level = "WARNING" if res["error"].startswith("No curves") else "ERROR"
            self.report({level}, res["error"])
            return {"CANCELLED"}
        context.view_layer.update()
        scn.erode_summary = res["note"]
        self.report({"INFO"},
                    f"Eroded {terrain.name} ({res['scope']}) + re-imposed {res['reimposed']} curve(s)")
        return {"FINISHED"}


class BBT_OT_curve_revert_erode(Operator):
    bl_idname = "bob_blender_tools.curve_revert_erode"
    bl_label = "Revert to Clean"
    bl_description = ("Undo Bake & Erode: swap the terrain back to the clean (pre-erosion) heightfield "
                      "and re-impose every curve on it, so the full graded channel returns. Use this to "
                      "get back to the un-eroded sculpt after trying an erode")
    bl_options = {"REGISTER"}

    def execute(self, context):
        scn = context.scene.bbt_curves
        terrain = _terrain(context)
        if terrain is None:
            self.report({"ERROR"}, "Pick a terrain mesh in the Paths panel first")
            return {"CANCELLED"}
        curves = [e.curve for e in scn.curves if e.curve is not None]
        res = run_revert_erode(terrain, curves, scatter_cb=None)
        if res["error"] is not None:
            self.report({"ERROR"}, res["error"])
            return {"CANCELLED"}
        context.view_layer.update()
        scn.erode_summary = "reverted to clean terrain"
        self.report({"INFO"}, f"Reverted {terrain.name} to clean + re-imposed {res['reimposed']} curve(s)")
        return {"FINISHED"}


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
    bl_order = 3  # pipeline stage: Paths, after Terrain, before Scatter (docs/SPLINES.md 5)
    bl_options = {"DEFAULT_CLOSED"}

    def draw(self, context):
        scn = context.scene.bbt_curves
        layout = self.layout

        # Name only in the header: the role is shown by the list-row icon and again (with its
        # label) in the Active Path sub-panel, so repeating it here just duplicates. Matches the
        # Scatter header, which names the layer and leaves the kind to the Active Layer sub-panel.
        curve = _active_curve(context)
        hdr = curve.name if curve is not None else None
        helpers.context_header(layout, "Path", hdr, icon="CURVE_DATA",
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
            helpers.structural_action(layout, "bob_blender_tools.curve_build_all",
                                         note="carves every terrain-channel curve")
        if scn.summary:
            layout.label(text=scn.summary, icon="INFO")

        # Naturalise: fold the carves into the heightfield and weather them. Needs a baked terrain
        # (the erosion works on the heightfield raster, not the live GN carve).
        terrain = _terrain(context)
        if scn.curves and _has_bake(terrain):
            box = layout.box()
            box.label(text="Naturalise landscape (Bake & Erode)", icon="MODIFIER")
            box.prop(scn, "erode_strength", slider=True)
            box.prop(scn, "erode_scope")
            box.prop(scn, "erode_deposit")
            # Both are structural (they rewrite the baked heightfield), so mark them like Build
            # All rather than as raw buttons.
            helpers.structural_action(
                box, "bob_blender_tools.curve_bake_erode",
                note=scn.erode_summary or "erodes the landscape, re-imposes the channels (water stays put)")
            # Revert is structural too (it rewrites the baked heightfield), so route it through
            # structural_action like its Bake & Erode sibling (S6) rather than a hand-rolled row
            # with the marker icon. Enabled only once an erode has recorded a clean source.
            helpers.structural_action(
                box, "bob_blender_tools.curve_revert_erode",
                enabled=bool(terrain.get("bbt_heightmap_clean")))
        elif scn.curves and terrain is not None:
            # The box needs a baked heightfield; say so rather than vanishing without a hint.
            layout.label(text="Bake the terrain (Terrain panel) to naturalise the carves",
                         icon="INFO")


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
        impose = spec.get("family") == "impose"
        layout.label(text=spec["label"], icon=spec["icon"])

        # Structural group : role + channels apply on a Build, not from a callback.
        box = layout.box()
        # S7: no STRUCTURAL_ICON on the caption; the structural_action button in this box carries
        # it, so it would show twice.
        box.label(text="Structural (Build to apply)")
        box.prop(cfg, "role")
        box.prop(cfg, "do_terrain", text="Carve channel" if impose else "Terrain shape")
        box.prop(cfg, "do_material", text="Damp bed" if impose else "Material band")
        box.prop(cfg, "do_scatter")
        if impose:  # the Water channel exists only for the river/stream (impose) family
            box.prop(cfg, "do_water")
        helpers.structural_action(box, "bob_blender_tools.curve_build",
                                     note=("drapes downhill + carves the bed, lays the water surface"
                                           if impose else
                                           "drapes + carves, adds the surface band, clears scatter"))
        tip = box.row()
        tip.enabled = False
        tip.label(text="Reeds on the banks: a Scatter layer, Curve = Verge (path edge)", icon="INFO")

        # Warn if the curve was dragged off the terrain: the drape clips off-terrain points, so a
        # partly-off curve carves only its on-terrain part and a fully-off one does nothing.
        if _curve_off_terrain(curve, _terrain(context)):
            warn = layout.row()
            warn.alert = True
            warn.label(text="Curve leaves the terrain; off-terrain points are ignored", icon="ERROR")

        # Shape (live): one owner on bbt_curve, synced to BOTH the carve overlay and the water ribbon
        # as you drag. Editing Depth re-carves the terrain and repositions the water together.
        layout.separator()
        layout.label(text="Shape (live)", icon="MODIFIER")
        col = layout.column(align=True)
        col.prop(cfg, "width")
        # After Bake & Erode the banks come from the eroded terrain: the overlay carves only a shallow
        # guarantee bed, so Depth and every Banks knob are forced and dragging them does nothing.
        # Show them disabled with a note instead of as live-but-inert sliders (Build re-imposes them).
        eroded_banks = cfg.banks_from_erosion
        drow = col.row()
        drow.enabled = not eroded_banks
        drow.prop(cfg, "depth")
        col.prop(cfg, "falloff")
        col.prop(cfg, "taper")
        col.prop(cfg, "width_var", text="Width Variation", slider=True)

        layout.label(text="Banks")
        if eroded_banks:
            note = layout.row()
            note.enabled = False
            note.label(text="Shaped banks come from erosion; Build to re-impose the graded channel",
                       icon="INFO")
        col = layout.column(align=True)
        # bank_slope still sets the water reach in eroded mode, so it stays live; the graded-embankment
        # knobs (shoulder/bias/height) are forced to 0 there, so grey just those.
        col.prop(cfg, "bank_slope")
        graded = col.column(align=True)
        graded.enabled = not eroded_banks
        graded.prop(cfg, "shoulder")
        graded.prop(cfg, "bank_bias")
        if impose:
            graded.prop(cfg, "bank_height")

        # Verge band (item-8): the ring a Verge scatter layer reads. Only relevant when this curve
        # drives scatter, so it rides with do_scatter.
        if cfg.do_scatter:
            layout.label(text="Verge (scatter edge band)", icon="OUTLINER_OB_FORCE_FIELD")
            col = layout.column(align=True)
            col.prop(cfg, "verge_gap")
            col.prop(cfg, "verge_width")
            col.prop(cfg, "verge_side", slider=True)

        if impose:
            layout.label(text="Water", icon="MATFLUID")
            col = layout.column(align=True)
            col.prop(cfg, "water_level")
            col.prop(cfg, "flow")
            col.prop(cfg, "foam_bank")
            col.prop(cfg, "foam_rapids")

            layout.label(text="Waves (animated)", icon="MOD_WAVE")
            col = layout.column(align=True)
            col.prop(cfg, "wave_amp")
            col.prop(cfg, "wave_len")
            col.prop(cfg, "wave_steep")
            col.prop(cfg, "wave_speed")
            col.prop(cfg, "wave_chop")


CLASSES = (
    BBT_Curve,
    BBT_CurveEntry,
    BBT_CurvesProps,
    BBT_OT_curve_add,
    BBT_OT_curve_remove,
    BBT_OT_curve_duplicate,
    BBT_OT_curve_build,
    BBT_OT_curve_build_all,
    BBT_OT_curve_bake_erode,
    BBT_OT_curve_revert_erode,
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
