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
               "bank_height", "water_level", "flow", "foam_bank", "foam_rapids")
ROLES = {
    "dirt_path": {
        "label": "Dirt Path", "icon": "IPO_EASE_IN_OUT",
        "desc": "A shallow worn trail draped onto the ground",
        "width": 4.8, "depth": 0.3, "falloff": 3.5, "taper": 2.0, "shoulder": 0.0,
        "bank_slope": 1.0, "bank_bias": 0.0, "bank_height": 0.4,
        "water_level": 0.6, "flow": 1.0, "foam_bank": 0.5, "foam_rapids": 1.0,
        "surface": (0.13, 0.10, 0.07, 1.0), "surface_rough": 0.9, "surface_hard": 0.0,
        "surface_attr": "", "surface_channel": "a",
    },
    "trail": {
        "label": "Trail", "icon": "IPO_LINEAR",
        "desc": "A narrow footpath, barely recessed",
        "width": 2.4, "depth": 0.15, "falloff": 2.0, "taper": 1.0, "shoulder": 0.0,
        "bank_slope": 1.2, "bank_bias": 0.0, "bank_height": 0.4,
        "water_level": 0.6, "flow": 1.0, "foam_bank": 0.5, "foam_rapids": 1.0,
        "surface": (0.17, 0.14, 0.10, 1.0), "surface_rough": 0.9, "surface_hard": 0.0,
        "surface_attr": "", "surface_channel": "a",
    },
    "road": {
        "label": "Road", "icon": "MOD_CURVE",
        "desc": "A wide graded track: a flat bench + shoulders, embanked on slopes",
        "width": 9.0, "depth": 0.4, "falloff": 4.0, "taper": 2.0, "shoulder": 1.5,
        "bank_slope": 0.7, "bank_bias": 0.0, "bank_height": 0.4,
        "water_level": 0.6, "flow": 1.0, "foam_bank": 0.5, "foam_rapids": 1.0,
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
    if _has_bake(terrain):
        # role["drape"] adds the monotonic downhill solve for a river/stream (empty for a path).
        _apply([{"op": "drape_curve", "name": curve.name,
                 **_drape_params(terrain), **role.get("drape", {})}])
        draped = True
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
    return "draped" if draped else "curve Z"


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


def _derived_water(cfg):
    """(fill_width, water_depth) for the ribbon, from the shape params. water_depth is metres below
    the rim (path_z); the water fills to where the bank crosses the waterline (reach) plus a small
    tuck, so it spans the bed and meets the banks with no gap."""
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
        for name, val in (("Path Width", cfg.width * 0.5), ("Path Depth", cfg.depth),
                          ("Path Falloff", cfg.falloff), ("End Taper", cfg.taper),
                          ("Shoulder Width", cfg.shoulder), ("Bank Slope", cfg.bank_slope),
                          ("Bank Bias", cfg.bank_bias), ("Bank Height", cfg.bank_height)):
            _set_mod_input(ov, name, val)
        terrain.update_tag()  # a modifier-input write does not always flush; tag so it re-carves live
    water = _water_obj(curve)
    wmod = next((m for m in water.modifiers if m.type == "NODES"), None) if water else None
    if wmod is not None:
        fill, water_depth = _derived_water(cfg)
        for name, val in (("Width", fill), ("Water Depth", water_depth), ("End Taper", cfg.taper),
                          ("Flow Base", cfg.flow), ("Foam Bank", cfg.foam_bank),
                          ("Foam Rapids", cfg.foam_rapids)):
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
        # Drop the curve's overlay modifier first so its carve and mask contribution disappear.
        terrain = _terrain(context)
        mod = _overlay_mod(terrain, curve)
        if mod is not None:
            terrain.modifiers.remove(mod)
        # Drop the river's water-surface ribbon object too, if it has one.
        water = _water_obj(curve)
        if water is not None:
            bpy.data.objects.remove(water, do_unlink=True)
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
        impose = role.get("family") == "impose"
        terrain = _terrain(context)
        did = []

        # Any channel needs the overlay's masks; build it once (carve only when the Terrain channel
        # is on, else it is a mask-only overlay driving material/scatter/verge/water). A water-only
        # river still needs it, because that is where the monotonic downhill drape runs.
        if cfg.do_terrain or cfg.do_material or cfg.do_scatter or (cfg.do_water and impose):
            if terrain is None:
                self.report({"WARNING"}, "Pick a terrain mesh")
            else:
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
        impose = spec.get("family") == "impose"
        layout.label(text=spec["label"], icon=spec["icon"])

        # Structural group (P3): role + channels apply on a Build, not from a callback.
        box = layout.box()
        box.label(text="Structural (Build to apply)", icon=ui_helpers.STRUCTURAL_ICON)
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

        # Shape (live): one owner on bbt_curve, synced to BOTH the carve overlay and the water ribbon
        # as you drag. Editing Depth re-carves the terrain and repositions the water together.
        layout.separator()
        layout.label(text="Shape (live)", icon="MODIFIER")
        col = layout.column(align=True)
        col.prop(cfg, "width")
        col.prop(cfg, "depth")
        col.prop(cfg, "falloff")
        col.prop(cfg, "taper")

        layout.label(text="Banks")
        col = layout.column(align=True)
        col.prop(cfg, "shoulder")
        col.prop(cfg, "bank_slope")
        col.prop(cfg, "bank_bias")
        if impose:
            col.prop(cfg, "bank_height")

        if impose:
            layout.label(text="Water", icon="MATFLUID")
            col = layout.column(align=True)
            col.prop(cfg, "water_level")
            col.prop(cfg, "flow")
            col.prop(cfg, "foam_bank")
            col.prop(cfg, "foam_rapids")


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
