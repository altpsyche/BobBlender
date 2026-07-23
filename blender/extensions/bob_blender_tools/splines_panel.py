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
- Water (C5): the IMPOSE family (river / stream). Unlike a path, a river runs monotonically DOWNHILL
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
side/tangent field for them arrive with their consumers; C2 roles differ by knob defaults and the
profile is symmetric.
"""

import os

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

from . import server, ui_helpers

# A small tuck (m) so the water edge sits just UNDER the bank lip rather than exactly on the
# waterline, avoiding a hairline gap where the ribbon meets the rising bank.
_WATER_TUCK = 0.15

# Curve overlay modifiers are named per curve so a terrain can carry several, and so removing a
# curve finds and drops exactly its modifier.
_OVERLAY_PREFIX = "BOB_Curve_"
# A river/stream's water-surface ribbon is its own object, one per curve (BobSplines C5.2).
_WATER_PREFIX = "BOB_Water_"

# Subdivisions per segment the curve evaluates to (Curve to Mesh honours resolution_u). High so the
# carved bench reads smooth: a coarse curve evaluates to a few straight segments whose junctions
# facet the bench into steps. Set on the datablock at Build.
_CURVE_RES = 128

# Roles: a typed curve preset. Each carries the SHAPE defaults (seeded onto bbt_curve at Add / role
# change, then scene-owned and live) plus STRUCTURAL keys that only apply on Build: family ("impose"
# = river/stream, carve DOWN to a descending water centreline; absent = follow-terrain), drape (the
# monotonic downhill solve for rivers), surface* (the C3 material band), wet* (the C5 damp bed).
#
# The shape defaults are in REAL units: `width` is the full channel width (1:1), `depth` the channel
# depth, `water_level` the fill fraction (0..1) of the channel. bbt_curve owns these live and the
# panel syncs them to BOTH the terrain-carve overlay and the water ribbon (see _sync_curve_params),
# so one set of params drives both and updating depth re-carves the terrain and moves the water.
# The water_level/flow/foam/bank_height keys matter only to the impose family (harmless on paths).
_SHAPE_KEYS = ("width", "depth", "falloff", "taper", "shoulder", "bank_slope", "bank_bias",
               "bank_height", "water_level", "flow", "foam_bank", "foam_rapids",
               "wave_amp", "wave_len", "wave_steep", "wave_speed", "wave_chop", "width_var")
ROLES = {
    "dirt_path": {
        "label": "Dirt Path", "icon": "IPO_EASE_IN_OUT",
        "desc": "A shallow worn trail draped onto the ground",
        "width": 4.8, "depth": 0.3, "falloff": 3.5, "taper": 2.0, "shoulder": 0.0,
        "bank_slope": 1.0, "bank_bias": 0.0, "bank_height": 0.4,
        "water_level": 0.6, "flow": 1.0, "foam_bank": 0.5, "foam_rapids": 1.0,
        "wave_amp": 0.0, "wave_len": 4.5, "wave_steep": 0.4, "wave_speed": 0.6, "wave_chop": 0.7,
        "width_var": 0.0,
        "surface": (0.13, 0.10, 0.07, 1.0), "surface_rough": 0.9, "surface_hard": 0.0,
        "surface_attr": "", "surface_channel": "a",
    },
    "trail": {
        "label": "Trail", "icon": "IPO_LINEAR",
        "desc": "A narrow footpath, barely recessed",
        "width": 2.4, "depth": 0.15, "falloff": 2.0, "taper": 1.0, "shoulder": 0.0,
        "bank_slope": 1.2, "bank_bias": 0.0, "bank_height": 0.4,
        "water_level": 0.6, "flow": 1.0, "foam_bank": 0.5, "foam_rapids": 1.0,
        "wave_amp": 0.0, "wave_len": 4.5, "wave_steep": 0.4, "wave_speed": 0.6, "wave_chop": 0.7,
        "width_var": 0.0,
        "surface": (0.17, 0.14, 0.10, 1.0), "surface_rough": 0.9, "surface_hard": 0.0,
        "surface_attr": "", "surface_channel": "a",
    },
    "road": {
        "label": "Road", "icon": "MOD_CURVE",
        "desc": "A wide graded track: a flat bench + shoulders, embanked on slopes",
        "width": 9.0, "depth": 0.4, "falloff": 4.0, "taper": 2.0, "shoulder": 1.5,
        "bank_slope": 0.7, "bank_bias": 0.0, "bank_height": 0.4,
        "water_level": 0.6, "flow": 1.0, "foam_bank": 0.5, "foam_rapids": 1.0,
        "wave_amp": 0.0, "wave_len": 4.5, "wave_steep": 0.4, "wave_speed": 0.6, "wave_chop": 0.7,
        "width_var": 0.0,
        "surface": (0.22, 0.21, 0.20, 1.0), "surface_rough": 0.8, "surface_hard": 1.0,
        "surface_attr": "bbt_curve_mask_b", "surface_channel": "b",
    },
    "river": {
        "label": "River", "icon": "MATFLUID",
        "desc": "A water channel that runs downhill; the terrain conforms down to a carved bed",
        "family": "impose",
        "width": 10.0, "depth": 1.2, "falloff": 4.5, "taper": 3.0, "shoulder": 0.5,
        "bank_slope": 0.9, "bank_bias": 0.0, "bank_height": 0.4,
        "water_level": 0.58, "flow": 1.0, "foam_bank": 0.5, "foam_rapids": 1.0,
        "wave_amp": 0.10, "wave_len": 4.5, "wave_steep": 0.4, "wave_speed": 0.6, "wave_chop": 0.7,
        "width_var": 0.35,
        "surface": (0.12, 0.11, 0.09, 1.0), "surface_rough": 0.85, "surface_hard": 0.0,
        "surface_attr": "", "surface_channel": "a",
        # densify: solve the descent from the terrain sampled DENSELY along the curve so the water
        # follows the valley instead of floating over dips. to_sea off (follow the valley floor).
        "drape": {"monotonic": True, "min_slope": 0.02, "to_sea": False, "densify": 48},
        "wet_attr": "bbt_curve_wet", "wet": 0.7,
    },
    "stream": {
        "label": "Stream", "icon": "MOD_WAVE",
        "desc": "A narrow brook: a shallow channel, quicker flow",
        "family": "impose",
        "width": 4.0, "depth": 0.5, "falloff": 2.5, "taper": 2.0, "shoulder": 0.2,
        "bank_slope": 1.1, "bank_bias": 0.0, "bank_height": 0.25,
        "water_level": 0.56, "flow": 1.4, "foam_bank": 0.4, "foam_rapids": 1.2,
        "wave_amp": 0.06, "wave_len": 2.5, "wave_steep": 0.45, "wave_speed": 0.6, "wave_chop": 0.8,
        "width_var": 0.30,
        "surface": (0.14, 0.12, 0.10, 1.0), "surface_rough": 0.85, "surface_hard": 0.0,
        "surface_attr": "", "surface_channel": "a",
        "drape": {"monotonic": True, "min_slope": 0.03, "to_sea": False, "densify": 48},
        "wet_attr": "bbt_curve_wet", "wet": 0.6,
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


def _apply_curve_transform(curve):
    """Bake the curve object's transform into its points and reset it to identity.

    The whole river pipeline (drape samples the heightmap at the point's own XY, the overlay/water
    read the curve via Object Info) assumes the curve sits at the ORIGIN. If the artist grab-moves
    the curve object, its Location offsets where the drape samples the terrain and shifts the whole
    river in Z. Baking the transform in (points move to world, object matrix -> identity) keeps the
    curve exactly where it looks while restoring the origin assumption, so a moved river still
    carves and floods against the terrain under it. Idempotent (a no-op when already at identity)."""
    import mathutils

    if curve is None or curve.type != "CURVE" or curve.matrix_world == mathutils.Matrix.Identity(4):
        return
    mw = curve.matrix_world.copy()
    for spline in curve.data.splines:
        if spline.type == "BEZIER":
            for p in spline.bezier_points:
                p.co = mw @ p.co
                p.handle_left = mw @ p.handle_left
                p.handle_right = mw @ p.handle_right
        else:  # NURBS / POLY: co is 4D (x, y, z, w)
            for p in spline.points:
                v = mw @ p.co.to_3d()
                p.co = (v.x, v.y, v.z, p.co[3])
    curve.matrix_world = mathutils.Matrix.Identity(4)
    curve.data.update_tag()


def _curve_off_terrain(curve, terrain):
    """True if any of the curve's control points sits outside the terrain footprint (|x|,|y| > size/2).

    A cheap panel-side check (control points only, no mesh eval) so the Active Path panel can warn that
    a dragged-off point will be clipped by the drape. Terrain is centred at the origin, size square."""
    if curve is None or terrain is None or curve.type != "CURVE":
        return False
    half = 0.5 * float(terrain.get("bbt_terrain_size", 90.0))
    for spline in curve.data.splines:
        pts = spline.bezier_points if spline.type == "BEZIER" else spline.points
        for p in pts:
            co = p.co
            if abs(co[0]) > half or abs(co[1]) > half:
                return True
    return False


def _build_curve_overlay(terrain, curve, carve=True):
    """Drape the curve onto the terrain (when it has a bake), then build/update the curve's overlay
    modifier. The overlay always writes the bbt_curve_mask attribute; carve=False makes it mask-only
    (no displacement), for a curve that drives material/scatter but not the terrain shape. Returns a
    short note on the drape source."""
    # Bake any grab-move into the points so the origin assumption holds (the drape samples the
    # heightmap at the point's own XY): a moved river must still sample the terrain beneath it.
    _apply_curve_transform(curve)
    # Densify the curve evaluation so the carved bench is smooth (see _CURVE_RES). On the datablock,
    # so scatter's proximity reads the same smooth centreline.
    if curve.type == "CURVE" and curve.data.resolution_u < _CURVE_RES:
        curve.data.resolution_u = _CURVE_RES
    role = ROLES.get(curve.bbt_curve.role, ROLES["dirt_path"])
    draped = False
    off_terrain = False
    if _has_bake(terrain):
        # role["drape"] adds the monotonic downhill solve for a river/stream (empty for a path).
        res = _apply([{"op": "drape_curve", "name": curve.name,
                       **_drape_params(terrain), **role.get("drape", {})}])
        # A drape that failed (missing heightmap, curve entirely off the terrain) returns an info-only
        # dict with no "created"; do not then claim the curve was draped.
        draped = bool(res and res[0].get("created"))
        # drape_curve clips points dragged off the terrain (else a river's monotonic solve carves a
        # runaway trench); flag it so Build/Bake & Erode can warn the artist to pull the curve back on.
        off_terrain = bool(res and res[0].get("dropped"))
    server._ensure_path()
    from bbmcp.geonodes import build_geonodes_on_object

    from . import scatter_panel

    # Only STRUCTURAL params here (they change the graph): the family branch, which attributes to
    # write, the edge ring name. The cross-section tunables are pushed live from bbt_curve by
    # _sync_curve_params after the build (reset=True drops the stale snapshot so the sync is the sole
    # authority; the recipe's own add_input defaults are just placeholders until the sync).
    params = {"curve": curve.name, "carve": carve,
              # impose (river/stream): carve the terrain DOWN to the draped monotonic centreline
              # instead of levelling to the live ground (docs/SPLINES.md 9 #1).
              "impose": role.get("family") == "impose",
              "surface_attr": role.get("surface_attr", ""),
              # a river writes the damp-bed mask the terrain material reads (apply_curve_wet).
              "wet_attr": role.get("wet_attr", ""),
              # this curve's own edge ring, so a Verge scatter layer can target just this path.
              "edge_attr": scatter_panel.edge_attr_name(curve)}
    build_geonodes_on_object(terrain, "curve_overlay", _overlay_name(curve), params, reset=True)
    _position_overlay(terrain, curve)
    # The overlay reads the curve via an Object Info node (a node-level reference, not a modifier
    # input), so after the overlay's modifier is rebuilt in place, or the curve is edited/moved/
    # re-draped, Blender does not always re-establish the terrain->curve dependency: the overlay
    # keeps evaluating a stale curve and Build alone will not fix it (only recreating the objects
    # did). Tag both so the graph relinks and the overlay re-evaluates the current curve.
    curve.update_tag()
    terrain.update_tag()
    note = "draped" if draped else "curve Z"
    if off_terrain:
        note += ", clipped off-terrain points"
    return note


def _apply_curve_material(terrain, role):
    """Configure the terrain material from the role's Material channel (BobSplines C3 / C5.4).

    Follow family (path/road): a curve-surface layer keyed to the curve mask (apply_curve_surface).
    Impose family (river/stream): the damp-bed wetness path (apply_curve_wet), so the carved bed and
    banks read wet and glossy under the transparent water. Returns the configured slot index (or
    True for the wet path), or None when the terrain has no Terrain BobShader to key."""
    if terrain is None:
        return None
    mat = terrain.active_material
    if mat is None:
        return None
    server._ensure_path()
    from bbmcp import materials

    if role.get("family") == "impose":
        return materials.apply_curve_wet(mat, role.get("wet", 0.6))
    return materials.apply_curve_surface(mat, role["surface"], role.get("surface_rough", 0.85),
                                         hard_edge=role.get("surface_hard", 0.0),
                                         channel=role.get("surface_channel", "a"))


def _water_name(curve):
    return _WATER_PREFIX + curve.name


def _water_obj(curve):
    """The curve's water-surface ribbon object, or None."""
    return bpy.data.objects.get(_water_name(curve)) if curve is not None else None


def _mod_ids(mod):
    return {it.name: it.identifier for it in mod.node_group.interface.items_tree
            if getattr(it, "item_type", None) == "SOCKET" and it.in_out == "INPUT"}


def _set_mod_input(mod, name, value):
    """Set a GN modifier's input value by socket name (skips a name the group does not expose)."""
    if mod is None or mod.node_group is None:
        return
    ident = _mod_ids(mod).get(name)
    inp = getattr(mod.properties.inputs, ident, None) if ident else None
    if inp is not None:
        try:
            inp.value = value
        except (AttributeError, TypeError, ValueError):
            pass


def _drive_water_freeze(water, wmod, scene):
    """Drive the water ribbon's Freeze input live from bbt_env.temperature, so a cold scene flattens
    the wave geometry (the shader freezes the look; this stops the mesh animating). Ramp matches the
    shader's env-cold path: 0 at/above freezing, 1 by -6 C. Reinstalled on each build because a reset
    rebuild regenerates the socket identifiers. No env, no driver (the default 0 leaves it liquid)."""
    if water is None or wmod is None or wmod.node_group is None:
        return
    if getattr(scene, "bbt_env", None) is None:
        return
    ident = _mod_ids(wmod).get("Freeze")
    inp = getattr(wmod.properties.inputs, ident, None) if ident else None
    if inp is None:
        return
    try:
        water.driver_remove(inp.path_from_id("value"), -1)
    except (TypeError, RuntimeError):
        pass
    fc = inp.driver_add("value")
    fc = fc[0] if isinstance(fc, list) else fc
    drv = fc.driver
    drv.type = "SCRIPTED"
    var = drv.variables.new()
    var.name = "v"
    var.type = "SINGLE_PROP"
    tgt = var.targets[0]
    tgt.id_type = "SCENE"
    tgt.id = scene
    tgt.data_path = "bbt_env.temperature"
    drv.expression = "max(0.0, min(1.0, -v / 6.0))"  # 0 above 0 C, 1 by -6 C (matches the shader)


def _build_water(curve):
    """Build (or rebuild) the river/stream water-surface ribbon and shade it as a water BobShader.

    Must run AFTER the overlay Build has DRAPED the curve (the ribbon derives its Z from the draped
    curve via curve_field, the same reference the overlay carves the bed to, so surface and bed stay
    in harmony). Structural build only (reset=True); the Width / Water Depth / Flow / Foam tunables
    are pushed live from bbt_curve by _sync_curve_params, which fills the ribbon to the channel and
    meets the banks. Returns the object, or None on failure."""
    server._ensure_path()
    from bbmcp import materials

    name = _water_name(curve)
    _apply([{"op": "build_geonodes", "recipe": "curve_water", "name": name,
             "params": {"curve": curve.name}, "reset": True}])
    obj = bpy.data.objects.get(name)
    if obj is None:
        return None
    # A GN-generated mesh shades through a Set-Material modifier (like the terrain), not the slot.
    materials.assign_material(obj, materials.water_material(name))
    # EEVEE-Next only refracts the water's Transmission with scene ray tracing on (the material
    # flags are set in water_material); a no-op in Cycles / when already on.
    materials.enable_eevee_refraction(bpy.context.scene)
    # Drive Freeze from the environment temperature so a frozen river stops animating (the shader
    # freezes the look; this flattens the wave geometry in lockstep). Reinstalled every build.
    wmod = next((m for m in obj.modifiers if m.type == "NODES"), None)
    _drive_water_freeze(obj, wmod, bpy.context.scene)
    obj.update_tag()
    return obj


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


# Live param sync: bbt_curve is the single owner of the shape params; editing one pushes it to BOTH
# the terrain-carve overlay and the water ribbon, so they never drift and update in real time.
_syncing = False  # suppress the per-prop sync while _seed_role_params sets a whole role at once


def _guarantee_depth(cfg):
    """Depth (m) of the SHALLOW wet bed the overlay carves in banks-from-erosion mode: a fraction of
    the authored depth, clamped, so it guarantees a containing trough at the eroded valley floor
    without re-imposing the full graded channel. Verified headless to hold water at 0% float."""
    return min(max(cfg.depth * 0.4, 0.2), 0.6)


def _derived_water(cfg):
    """(fill_width, water_depth) for the ribbon, from the shape params. water_depth is metres below
    path_z; the water fills to where the bank crosses the waterline (reach) plus a small tuck, so it
    spans the bed and meets the banks with no gap.

    In banks-from-erosion mode the drape samples the ERODED channel floor (path_z == floor, the
    overlay only carves the shallow guarantee bed), so the fill is keyed to the guarantee depth, not
    the authored depth, and the width is just the channel plus a tuck (the eroded banks, not a graded
    shoulder, hold the water; the shader shore fade hides the exact edge)."""
    if cfg.banks_from_erosion:
        g = _guarantee_depth(cfg)
        water_depth = g * (1.0 - cfg.water_level)
        # Reach the ribbon out to where the shallow guarantee bed's wall crosses the waterline (same
        # by-construction containment as the graded path, scaled to the guarantee depth), so the edge
        # sits at the waterline on the eroded bank, not low in the channel.
        reach = (g * cfg.water_level) / max(cfg.bank_slope, 0.05)
        fill = cfg.width + 2.0 * reach + 2.0 * _WATER_TUCK
        return fill, water_depth
    water_depth = cfg.depth * (1.0 - cfg.water_level)
    reach = (cfg.depth * cfg.water_level) / max(cfg.bank_slope, 0.05)
    fill = cfg.width + 2.0 * cfg.shoulder + 2.0 * reach + 2.0 * _WATER_TUCK
    return fill, water_depth


def _sync_curve_params(context, curve):
    """Push bbt_curve's shape params onto the curve's overlay (Path Width is a RADIUS, so width/2)
    and its water ribbon (Width/Water Depth derived to fill the channel and meet the banks). A no-op
    for inputs a modifier lacks (a follow overlay has no Bank Height; a path has no water ribbon), so
    it is safe for every role and whether or not the pieces are built yet."""
    if curve is None:
        return
    cfg = curve.bbt_curve
    terrain = _terrain(context)
    ov = _overlay_mod(terrain, curve)
    if ov is not None:
        if cfg.banks_from_erosion:
            # Banks come from the eroded heightfield: the overlay carves only a shallow wet bed
            # (guarantee depth) with the graded shoulder/bank dropped, so no smooth swept embankment
            # is re-imposed on top of the erosion.
            inputs = (("Path Width", cfg.width * 0.5), ("Path Depth", _guarantee_depth(cfg)),
                      ("Path Falloff", cfg.falloff), ("End Taper", cfg.taper),
                      ("Width Variation", cfg.width_var),
                      ("Shoulder Width", 0.0), ("Bank Slope", cfg.bank_slope),
                      ("Bank Bias", 0.0), ("Bank Height", 0.0))
        else:
            inputs = (("Path Width", cfg.width * 0.5), ("Path Depth", cfg.depth),
                      ("Path Falloff", cfg.falloff), ("End Taper", cfg.taper),
                      ("Width Variation", cfg.width_var),
                      ("Shoulder Width", cfg.shoulder), ("Bank Slope", cfg.bank_slope),
                      ("Bank Bias", cfg.bank_bias), ("Bank Height", cfg.bank_height))
        # Verge band (item-8): the scatter edge ring's own metres, pushed for every role (any curve
        # can carry a verge), not role-seeded, so a role change keeps the artist's verge tuning.
        inputs = inputs + (("Verge Gap", cfg.verge_gap), ("Verge Width", cfg.verge_width),
                           ("Verge Side", cfg.verge_side))
        for name, val in inputs:
            _set_mod_input(ov, name, val)
        terrain.update_tag()  # a modifier-input write does not always flush; tag so it re-carves live
    water = _water_obj(curve)
    wmod = next((m for m in water.modifiers if m.type == "NODES"), None) if water else None
    if wmod is not None:
        fill, water_depth = _derived_water(cfg)
        # Bed Depth = the full channel depth below the rim, so the ribbon can store the water-column
        # thickness (Bed Depth - Water Depth) per vertex for the shader's depth absorption. In
        # banks-from-erosion mode the containing trough is the shallow guarantee, so use that.
        bed_depth = _guarantee_depth(cfg) if cfg.banks_from_erosion else cfg.depth
        for name, val in (("Width", fill), ("Water Depth", water_depth), ("Bed Depth", bed_depth),
                          ("End Taper", cfg.taper), ("Width Variation", cfg.width_var),
                          ("Flow Base", cfg.flow), ("Foam Bank", cfg.foam_bank),
                          ("Foam Rapids", cfg.foam_rapids), ("Wave Amplitude", cfg.wave_amp),
                          ("Wave Length", cfg.wave_len), ("Wave Steepness", cfg.wave_steep),
                          ("Wave Speed", cfg.wave_speed), ("Wave Chop", cfg.wave_chop)):
            _set_mod_input(wmod, name, val)
        water.update_tag()


def _seed_role_params(curve, context):
    """Seed bbt_curve's shape params from the role preset (on Add / role change), then sync once."""
    global _syncing
    role = ROLES.get(curve.bbt_curve.role, ROLES["dirt_path"])
    _syncing = True
    try:
        for k in _SHAPE_KEYS:
            setattr(curve.bbt_curve, k, role[k])
    finally:
        _syncing = False
    _sync_curve_params(context, curve)


def _sync_cb(self, context):
    if not _syncing:
        _sync_curve_params(context, self.id_data)


def _seed_cb(self, context):
    _seed_role_params(self.id_data, context)


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
      (_sync_curve_params), so one set of numbers drives both and there is no panel-vs-modifier
      drift. width is the FULL channel width (1:1); depth the channel depth; water_level the fill."""

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
    # bed to guarantee containment and the graded shoulder/bank is dropped. See _sync_curve_params.
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
        _seed_role_params(obj, context)  # seed the shape defaults for this role (live from now on)
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
        impose = role.get("family") == "impose"
        terrain = _terrain(context)
        did = []
        cfg.banks_from_erosion = False  # a fresh build re-imposes the graded channel (until Erode)

        # Any channel needs the overlay's masks; build it once (carve only when the Terrain channel
        # is on, else it is a mask-only overlay driving material/scatter/verge/water). A water-only
        # river still needs it, because that is where the monotonic downhill drape runs.
        needs_terrain = cfg.do_terrain or cfg.do_material or cfg.do_scatter or (cfg.do_water and impose)
        if needs_terrain and terrain is None:
            # Every requested channel needs a terrain to drape/carve/mask against; without one the
            # material band, water, and scatter would all fail with misleading downstream messages
            # (or build a water ribbon on an undraped curve). Stop here with one clear reason.
            self.report({"ERROR"}, "Pick a terrain mesh in the Paths panel first")
            return {"CANCELLED"}
        if needs_terrain:
            note = _build_curve_overlay(terrain, curve, carve=cfg.do_terrain)
            did.append(f"carved terrain ({note})" if cfg.do_terrain else "curve mask")

        if cfg.do_material:
            if _apply_curve_material(terrain, role) is not None:
                did.append("damp bed" if impose else "surface band")
            else:
                self.report({"WARNING"}, "Material band needs a Terrain BobShader (shade it in Shaders)")

        # Water surface: build the ribbon AFTER the overlay drape so it sits on the descending
        # centreline. Only the impose family (river/stream) carries a water channel.
        if cfg.do_water and impose:
            if _build_water(curve) is not None:
                did.append("water surface")
            else:
                self.report({"WARNING"}, "Could not build the water ribbon")

        # Push the shape params onto the freshly built overlay + water (they built with placeholder
        # defaults under reset=True); from here every param edit stays live via the update callback.
        _sync_curve_params(context, curve)

        if cfg.do_scatter:
            if _clear_scatter(context):
                did.append("cleared scatter")
            else:
                self.report({"WARNING"}, "Scatter clear needs a Scatter emitter with layers")

        # Rebuild the dependency graph so the overlay relinks to the (edited/re-draped) curve; a
        # stale link is why an edit or re-bake needed a delete + re-add before, not just a Build.
        context.view_layer.update()
        context.scene.bbt_curves.summary = \
            f"{role['label']}: {', '.join(did) or 'nothing (check channels)'}"
        self.report({"INFO"}, f"Built {curve.name}: {', '.join(did) or 'no channels applied'}")
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
                _sync_curve_params(context, curve)
        # Material: for the follow family, one surface layer per DISTINCT class among the do_material
        # curves (R5), deduped by channel, so a paved road and a dirt trail read differently. For the
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


def _curve_band_spec(curve, terrain, cap=300):
    """One curve's centreline as terrain-UV points [[u, v], ...] plus its channel width and a
    normalised seed depth, for the erosion band mask / drainage prior / channel seed. Reuses
    path_curve._ordered_polyline_xy (the same order-robust wire walk the drape uses) so the band
    tracks the channel exactly. u = x/size + 0.5; v = 0.5 - y/size (the PNG is top-row-first while
    Blender samples it V-up). `depth` is the authored channel depth mapped into the heightfield's
    normalised [0,1] range (metres / terrain height), so the seed carve matches the intended channel.
    Returns None for a degenerate curve."""
    _apply_curve_transform(curve)  # origin assumption: the curve XY IS the terrain sample point
    server._ensure_path()
    from bbmcp import path_curve
    size = float(terrain.get("bbt_terrain_size", 90.0)) or 1.0
    height = float(terrain.get("bbt_terrain_height", 22.0)) or 1.0
    xy = path_curve._ordered_polyline_xy(curve)
    # Clip off-terrain points (a point dragged off the terrain) BEFORE mapping to UV. Clamping them
    # to the [0,1] edge instead would smear the seed/drainage-prior/band along the terrain rim; the
    # channel_seed + fluvial prior must only track the on-terrain path (mirrors the drape clip).
    xy = path_curve._clip_xy_to_terrain(xy, size)
    if len(xy) > cap:
        xy = path_curve._resample_xy(xy, cap)
    if len(xy) < 2:
        return None
    cfg = curve.bbt_curve
    return {"points": [[x / size + 0.5, 0.5 - y / size] for x, y in xy],
            "width": max(cfg.width * 0.5, 0.01) / size,
            "falloff": max(cfg.falloff, 0.1) / size,
            "depth": min(max(cfg.depth / height, 0.005), 0.2)}


class BBT_OT_curve_bake_erode(Operator):
    bl_idname = "bob_blender_tools.curve_bake_erode"
    bl_label = "Bake & Erode Curves"
    bl_description = ("Commit: run the venv erosion on the terrain heightfield to weather the landscape "
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
        hm = terrain.get("bbt_heightmap")
        if not hm or not os.path.exists(hm):
            self.report({"ERROR"}, "Terrain has no baked heightfield -- bake it in the Terrain panel first")
            return {"CANCELLED"}
        # Always erode the CLEAN source, never a previous eroded output (else re-runs stack up). If the
        # terrain is currently showing an eroded PNG, fall back to the stored clean source.
        clean_src = terrain.get("bbt_heightmap_clean") if hm.endswith("_eroded.png") else hm
        if not clean_src or not os.path.exists(clean_src):
            clean_src = hm
        size = float(terrain.get("bbt_terrain_size", 90.0))
        height = float(terrain.get("bbt_terrain_height", 22.0))
        sea = float(terrain.get("bbt_terrain_sea", 0.22))
        grid_res = int(terrain.get("bbt_terrain_res", 256))

        # Curves to re-impose after the erode, plus their UV polylines for the band mask.
        specs, curves = [], []
        for entry in scn.curves:
            curve = entry.curve
            if curve is None:
                continue
            cfg = curve.bbt_curve
            impose = ROLES.get(cfg.role, ROLES["dirt_path"]).get("family") == "impose"
            if cfg.do_terrain or cfg.do_material or cfg.do_scatter or (cfg.do_water and impose):
                curves.append(curve)
            if cfg.do_terrain:
                spec = _curve_band_spec(curve, terrain)
                if spec is not None:
                    specs.append(spec)
        if not curves:
            self.report({"WARNING"}, "No curves with a channel to erode + re-impose")
            return {"CANCELLED"}

        # Erosion stack: the SPLINE SEEDS the channel, then erosion SHAPES the banks (it no longer
        # re-imposes a smooth swept embankment). Per curve, channel_seed cuts a shallow bed along the
        # centreline so the fluvial solver has a slope + depression to amplify; fluvial then incises
        # with a DRAINAGE PRIOR (flow_prior boosts the drainage area on the spline, so the solver cuts
        # the valley where the river is) and thermal slumps the banks at a NOISE-WARPED repose angle
        # (talus_warp), so they read as natural weathered slopes, not one uniform ruled bank. The
        # channel is a shallow bed guarantee afterwards; the visible banks are this eroded terrain.
        s = float(scn.erode_strength)
        curve_specs = [{"points": c["points"]} for c in specs]
        seed_ops = [{"kind": "channel_seed", "curves": [{"points": c["points"]}],
                     "width": c["width"], "falloff": c["falloff"], "depth": c["depth"]}
                    for c in specs]
        thermal = {"kind": "thermal", "talus": 0.010 - 0.006 * s, "iterations": int(6 + 20 * s),
                   "talus_warp": 0.4 + 0.3 * s, "talus_freq": 5.0}
        fluvial = {"kind": "fluvial", "iterations": int(12 + 45 * s), "k": 2e-4 + 5e-4 * s,
                   "diffusion": 0.1, "talus_warp": 0.4 + 0.3 * s, "talus_freq": 5.0}
        if curve_specs:
            band_w = max(c["width"] for c in specs)
            band_f = max(c["falloff"] for c in specs)
            fluvial["flow_prior"] = {"curves": curve_specs, "width": band_w,
                                     "falloff": band_f + 0.01, "gain": 3000.0 + 5000.0 * s}
        # Deposition pass (Erosion2-style): settle sediment where the flow slackens so the incised V
        # alluviates into a flatter floodplain with gentle inner-bend bars, not a bare cut. Always
        # masked to the corridor (bars belong in the river band, whatever the erode scope), and the
        # overlay re-carves the shallow wet-bed guarantee afterwards, so filling the floor here cannot
        # push the water out. Amount scales with erode_strength.
        deposit = None
        if scn.erode_deposit and curve_specs:
            deposit = {"kind": "deposit", "amount": 0.006 + 0.012 * s, "iterations": int(3 + 3 * s),
                       "talus_warp": 0.4 + 0.3 * s, "talus_freq": 5.0}
        if scn.erode_scope == "band" and specs:
            band = max(c["width"] + c["falloff"] for c in specs) * 2.0
            mask = {"kind": "path", "curves": curve_specs, "width": band, "falloff": max(band, 0.05)}
            thermal["mask"] = mask
            fluvial["mask"] = mask  # same spec; the mask is read-only, so one dict serves both ops
        if deposit is not None:
            dband = max(c["width"] + c["falloff"] for c in specs) * 1.5
            deposit["mask"] = {"kind": "path", "curves": curve_specs,
                               "width": dband, "falloff": max(dband, 0.04)}
        stack = [*seed_ops, thermal, fluvial]
        if deposit is not None:
            stack.append(deposit)

        stem = os.path.splitext(os.path.basename(clean_src))[0]
        out_abs = os.path.join(os.path.dirname(clean_src), f"{stem}_eroded.png")
        params = {"base_png": clean_src, "stack": stack, "backend": "auto", "seed": 0}

        from . import _run_host_bake
        # Emit flow/wetness sidecar maps beside the eroded PNG (<stem>_eroded_flow.png / _wetness.png).
        # The terrain material discovers them by the sibling convention (materials._terrain_maps), so
        # after a material rebuild the riverbed layer keys off the ERODED drainage, not the clean base.
        _meta, err = _run_host_bake(context, out_abs, params=params, maps=True)
        if err is not None:
            self.report({"ERROR"}, err)
            return {"CANCELLED"}

        # Swap the terrain to the eroded PNG and record the clean source for the next re-run. The drape
        # now samples the eroded terrain (bbt_heightmap = eroded), so the re-imposed channels + water
        # follow the eroded ground.
        _apply([{"op": "reload_image", "path": out_abs},
                {"op": "build_geonodes", "recipe": "heightmap_terrain", "name": terrain.name,
                 "params": {"heightmap": out_abs, "size": size, "resolution": grid_res,
                            "height": height, "sea_level": sea}}])
        terrain["bbt_heightmap"] = out_abs
        terrain["bbt_heightmap_clean"] = clean_src

        # Re-impose every curve on the eroded terrain: re-drape, rebuild the water ribbon, push the
        # live params. For a Terrain curve the channel now LIVES IN the eroded heightfield (seed +
        # drainage prior carved it), so banks_from_erosion makes the overlay carve only a shallow wet
        # bed to guarantee containment -- the graded swept embankment is NOT re-imposed, so the eroded
        # banks show. Bed, banks and water re-derive from the eroded path_z (the eroded floor), so they
        # stay in harmony and the water is contained (0% float, verified headless).
        for curve in curves:
            cfg = curve.bbt_curve
            impose = ROLES.get(cfg.role, ROLES["dirt_path"]).get("family") == "impose"
            cfg.banks_from_erosion = cfg.do_terrain  # eroded-channel curves keep only a shallow bed
            _build_curve_overlay(terrain, curve, carve=cfg.do_terrain)
            if cfg.do_water and impose:
                _build_water(curve)
            _sync_curve_params(context, curve)
        context.view_layer.update()

        scope = "corridor band" if scn.erode_scope == "band" else "whole terrain"
        scn.erode_summary = f"eroded ({scope}), re-imposed {len(curves)} curve(s)"
        self.report({"INFO"},
                    f"Eroded {terrain.name} ({scope}) + re-imposed {len(curves)} curve(s)")
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
        clean = terrain.get("bbt_heightmap_clean")
        if not clean or not os.path.exists(clean):
            self.report({"ERROR"}, "No clean heightfield recorded (nothing eroded to revert)")
            return {"CANCELLED"}
        size = float(terrain.get("bbt_terrain_size", 90.0))
        height = float(terrain.get("bbt_terrain_height", 22.0))
        sea = float(terrain.get("bbt_terrain_sea", 0.22))
        grid_res = int(terrain.get("bbt_terrain_res", 256))
        _apply([{"op": "reload_image", "path": clean},
                {"op": "build_geonodes", "recipe": "heightmap_terrain", "name": terrain.name,
                 "params": {"heightmap": clean, "size": size, "resolution": grid_res,
                            "height": height, "sea_level": sea}}])
        terrain["bbt_heightmap"] = clean
        del terrain["bbt_heightmap_clean"]
        # Re-impose every curve on the clean terrain with the full graded channel back (clear the
        # banks-from-erosion flag so the overlay stamps the shoulder/banks again, not the shallow bed).
        n = 0
        for entry in scn.curves:
            curve = entry.curve
            if curve is None:
                continue
            cfg = curve.bbt_curve
            impose = ROLES.get(cfg.role, ROLES["dirt_path"]).get("family") == "impose"
            if not (cfg.do_terrain or cfg.do_material or cfg.do_scatter or (cfg.do_water and impose)):
                continue
            cfg.banks_from_erosion = False
            _build_curve_overlay(terrain, curve, carve=cfg.do_terrain)
            if cfg.do_water and impose:
                _build_water(curve)
            _sync_curve_params(context, curve)
            n += 1
        context.view_layer.update()
        scn.erode_summary = "reverted to clean terrain"
        self.report({"INFO"}, f"Reverted {terrain.name} to clean + re-imposed {n} curve(s)")
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
            # All (P3) rather than as raw buttons.
            ui_helpers.structural_action(
                box, "bob_blender_tools.curve_bake_erode",
                note=scn.erode_summary or "erodes the landscape, re-imposes the channels (water stays put)")
            # Revert is structural too (it rewrites the baked heightfield), so route it through
            # structural_action like its Bake & Erode sibling (S6) rather than a hand-rolled row
            # with the marker icon. Enabled only once an erode has recorded a clean source.
            ui_helpers.structural_action(
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

        # Structural group (P3): role + channels apply on a Build, not from a callback.
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
        ui_helpers.structural_action(box, "bob_blender_tools.curve_build",
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
