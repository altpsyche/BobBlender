"""BobSplines orchestration, shared by the Paths panel and the MCP ops.

The typed-curve pipeline (docs/SPLINES.md): a curve gets a ROLE and the role drives a
coordinated bundle of effects (terrain carve, material band, water ribbon, scatter clear,
erosion). The geometry-node recipes themselves live in core/geonodes; this module holds the
layer above them: the role presets, the small bpy helpers that build/position/drive the
per-curve modifiers, the live param sync, and the builder functions each panel Operator and
each dispatch handler calls. One builder serves the button and the op.

bpy-only, and it never imports ui/: the panel operators import THIS module (presets + helpers
+ builders) and keep only their context resolution (active curve/terrain pick, the scene curve
list, UI-state writes, self.report, view_layer.update). The MCP handlers (make_curve /
curve_build / bake_erode / revert_erode) resolve a curve + terrain by name from the op and call
the same builders. Per-curve state is read straight off the datablock (curve.bbt_curve) rather
than by importing the ui property group.

Two dependencies cannot live in pure core, so the builders take them as OPTIONAL injected
callables (both default None, both skipped + noted when absent):
  - scatter_cb() -> bool: rebuild the scatter layers so they clear along the curve masks. The
    panel passes a wrapper around bpy.ops.bob_blender_tools.scatter_build_all() (a ui operator);
    the MCP handlers pass None.
  - host_bake(out_abs, params) -> (meta, error): run the host erosion bake. The panel passes a
    wrapper around the package-level _run_host_bake (which spawns the host process); the MCP
    handlers wire it lazily when reachable, else pass None so the erode is skipped with a note.
"""

import os

import bpy

# A small tuck (m) so the water edge sits just UNDER the bank lip rather than exactly on the
# waterline, avoiding a hairline gap where the ribbon meets the rising bank.
_WATER_TUCK = 0.15

# Curve overlay modifiers are named per curve so a terrain can carry several, and so removing a
# curve finds and drops exactly its modifier.
_OVERLAY_PREFIX = "BOB_Curve_"
# A river/stream's water-surface ribbon is its own object, one per curve (BobSplines, the water
# ribbon).
_WATER_PREFIX = "BOB_Water_"

# Subdivisions per segment the curve evaluates to (Curve to Mesh honours resolution_u). High so the
# carved bench reads smooth: a coarse curve evaluates to a few straight segments whose junctions
# facet the bench into steps. Set on the datablock at Build.
_CURVE_RES = 128

# Roles: a typed curve preset. Each carries the SHAPE defaults (seeded onto bbt_curve at Add / role
# change, then scene-owned and live) plus STRUCTURAL keys that only apply on Build: family ("impose"
# = river/stream, carve DOWN to a descending water centreline; absent = follow-terrain), drape (the
# monotonic downhill solve for rivers), surface* (the material band material band), wet* (the water
# channel damp bed).
#
# The shape defaults are in REAL units: `width` is the full channel width (1:1), `depth` the channel
# depth, `water_level` the fill fraction (0..1) of the channel. bbt_curve owns these live and the
# panel syncs them to BOTH the terrain-carve overlay and the water ribbon (see sync_curve_params),
# so one set of params drives both and updating depth re-carves the terrain and moves the water. The
# water_level/flow/foam/bank_height keys matter only to the impose family (harmless on paths).
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


def _role_of(curve):
    """The role dict for a curve, defaulting to dirt_path (reads curve.bbt_curve.role via bpy)."""
    cfg = getattr(curve, "bbt_curve", None)
    key = getattr(cfg, "role", "dirt_path") if cfg is not None else "dirt_path"
    return ROLES.get(key, ROLES["dirt_path"])


# -- Generic helpers -------------------------------------------------------------------------
def _apply(ops):
    """Run bbmcp ops in-process, the path the terrain and scatter panels build through. Lazy import
    so this module can be wired into dispatch without a circular import at load time."""
    from .dispatch import apply_op

    return [apply_op(op) for op in ops]


def _unique_object_name(base):
    name, i = base, 1
    while name in bpy.data.objects:
        i += 1
        name = f"{base}.{i:03d}"
    return name


def _edge_attr_name(curve):
    """The per-curve edge-ring attribute a Verge scatter layer reads for ONE path (BobSplines, the
    verge band). The overlay writes the same name; both derive it from the curve name so a rename
    is picked up on the next build. Mirrors ui.scatter.edge_attr_name (kept in sync by the shared
    derivation; the scatter panel owns the reader side, so the one-line format is duplicated
    rather than cross-imported to keep core free of a ui import)."""
    return f"bbt_curve_edge_{curve.name}"


# -- Terrain / drape helpers -----------------------------------------------------------------
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


# -- Overlay helpers -------------------------------------------------------------------------
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

    The whole river pipeline (drape samples the heightmap at the point's own XY, the
    overlay/water read the curve via Object Info) assumes the curve sits at the ORIGIN. If the
    artist grab-moves the curve object, its Location offsets where the drape samples the terrain
    and shifts the whole river in Z. Baking the transform in (points move to world, object matrix
    -> identity) keeps the curve exactly where it looks while restoring the origin assumption, so
    a moved river still carves and floods against the terrain under it. Idempotent (a no-op when
    already at identity)."""
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

    A cheap panel-side check (control points only, no mesh eval) so the Active Path panel can
    warn that a dragged-off point will be clipped by the drape. Terrain is centred at the origin,
    size square."""
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
    role = _role_of(curve)
    draped = False
    off_terrain = False
    if _has_bake(terrain):
        # role["drape"] adds the monotonic downhill solve for a river/stream (empty for a path).
        # `terrain` rather than the four restated numbers: drape_curve reads them off the object, so
        # there is one authority for what the terrain was built from (docs/SPLINES.md).
        res = _apply([{"op": "drape_curve", "name": curve.name, "terrain": terrain.name,
                       **role.get("drape", {})}])
        # A drape that failed (missing heightmap, curve entirely off the terrain) returns an
# info-only dict with no "created"; do not then claim the curve was draped.
        draped = bool(res and res[0].get("created"))
        # drape_curve clips points dragged off the terrain (else a river's monotonic solve carves a
# runaway trench); flag it so Build/Bake & Erode can warn the artist to pull the curve back
# on.
        off_terrain = bool(res and res[0].get("dropped"))
    from .geonodes import build_geonodes_on_object

    # Only STRUCTURAL params here (they change the graph): the family branch, which attributes to
    # write, the edge ring name. The cross-section tunables are pushed live from bbt_curve by
    # sync_curve_params after the build (reset=True drops the stale snapshot so the sync is the sole
    # authority; the recipe's own add_input defaults are just placeholders until the sync).
    params = {"curve": curve.name, "carve": carve,
              # impose (river/stream): carve the terrain DOWN to the draped monotonic centreline
              # instead of levelling to the live ground (docs/SPLINES.md 9 #1).
              "impose": role.get("family") == "impose",
              "surface_attr": role.get("surface_attr", ""),
              # a river writes the damp-bed mask the terrain material reads (apply_curve_wet).
              "wet_attr": role.get("wet_attr", ""),
              # this curve's own edge ring, so a Verge scatter layer can target just this path.
              "edge_attr": _edge_attr_name(curve)}
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


# -- Material helpers ------------------------------------------------------------------------
def _apply_curve_material(terrain, role):
    """Configure the terrain material from the role's Material channel (BobSplines, the material band /
    the damp bed).

    Follow family (path/road): a curve-surface layer keyed to the curve mask
    (apply_curve_surface). Impose family (river/stream): the damp-bed wetness path
    (apply_curve_wet), so the carved bed and banks read wet and glossy under the transparent
    water. Returns the configured slot index (or True for the wet path), or None when the terrain
    has no Terrain BobShader to key."""
    if terrain is None:
        return None
    mat = terrain.active_material
    if mat is None:
        return None
    from . import materials

    if role.get("family") == "impose":
        return materials.apply_curve_wet(mat, role.get("wet", 0.6))
    return materials.apply_curve_surface(mat, role["surface"], role.get("surface_rough", 0.85),
                                         hard_edge=role.get("surface_hard", 0.0),
                                         channel=role.get("surface_channel", "a"))


# -- Water helpers ---------------------------------------------------------------------------
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
    the wave geometry (the shader freezes the look; this stops the mesh animating). Ramp matches
    the shader's env-cold path: 0 at/above freezing, 1 by -6 C. Reinstalled on each build because
    a reset rebuild regenerates the socket identifiers. No env, no driver (the default 0 leaves
    it liquid)."""
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
    are pushed live from bbt_curve by sync_curve_params, which fills the ribbon to the channel and
    meets the banks. Returns the object, or None on failure."""
    from . import materials

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


# -- Live param sync -------------------------------------------------------------------------
# bbt_curve is the single owner of the shape params; editing one pushes it to BOTH the terrain-carve
# overlay and the water ribbon, so they never drift and update in real time. The panel's per-prop
# update callback checks this flag so seed_role_params can set a whole role at once without a sync
# firing per property (the flag lives here so the ui callback and the core seeder share one
# authority).
_syncing = False


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
    overlay only carves the shallow guarantee bed), so the fill is keyed to the guarantee depth,
    not the authored depth, and the width is just the channel plus a tuck (the eroded banks, not
    a graded shoulder, hold the water; the shader shore fade hides the exact edge)."""
    if cfg.banks_from_erosion:
        g = _guarantee_depth(cfg)
        water_depth = g * (1.0 - cfg.water_level)
        # Reach the ribbon out to where the shallow guarantee bed's wall crosses the waterline (same
# by-construction containment as the graded path, scaled to the guarantee depth), so the
# edge sits at the waterline on the eroded bank, not low in the channel.
        reach = (g * cfg.water_level) / max(cfg.bank_slope, 0.05)
        fill = cfg.width + 2.0 * reach + 2.0 * _WATER_TUCK
        return fill, water_depth
    water_depth = cfg.depth * (1.0 - cfg.water_level)
    reach = (cfg.depth * cfg.water_level) / max(cfg.bank_slope, 0.05)
    fill = cfg.width + 2.0 * cfg.shoulder + 2.0 * reach + 2.0 * _WATER_TUCK
    return fill, water_depth


def sync_curve_params(terrain, curve):
    """Push bbt_curve's shape params onto the curve's overlay (Path Width is a RADIUS, so width/2)
    and its water ribbon (Width/Water Depth derived to fill the channel and meet the banks). A
    no-op for inputs a modifier lacks (a follow overlay has no Bank Height; a path has no water
    ribbon), so it is safe for every role and whether or not the pieces are built yet. `terrain`
    is the mesh the overlay lives on (the panel resolves it from context; None syncs only the
    water ribbon)."""
    if curve is None:
        return
    cfg = curve.bbt_curve
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
        if terrain is not None:
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


# The shape keys an op may set, plus the verge band (live, deliberately NOT role-seeded, so it is
# not in _SHAPE_KEYS but is still a legitimate thing to ask for).
SHAPE_PARAMS = _SHAPE_KEYS + ("verge_gap", "verge_width", "verge_side")


def set_shape(curve, terrain, shape):
    """Write explicit shape params onto bbt_curve and sync them to the overlay + water ribbon.

    The op-side counterpart to the panel's per-property sliders, and the reason it exists: without
    it the only way to change a curve's WIDTH over MCP was to change its ROLE, which also swaps the
    mask channel (bbt_curve_mask <-> bbt_curve_mask_b) and therefore silently invalidates every
    scatter layer's curve_attr. Shape and identity are separate things and this keeps them separate.

    Returns (applied, unknown): the names written, and the names that are not shape params. Values
    are clamped by the property definitions themselves, so an out-of-range number lands at the
    bound rather than raising. Sets the module _syncing flag so the panel's per-property callback
    does not fire one sync per key; one sync runs at the end.
    """
    global _syncing
    cfg = curve.bbt_curve
    applied, unknown = [], []
    _syncing = True
    try:
        for key, val in (shape or {}).items():
            if val is None:
                continue  # the contract model dumps every key; None means "not asked for"
            if key not in SHAPE_PARAMS:
                unknown.append(key)
                continue
            try:
                setattr(cfg, key, val)
            except (AttributeError, TypeError, ValueError):
                unknown.append(key)
                continue
            applied.append(key)
    finally:
        _syncing = False
    if applied:
        sync_curve_params(terrain, curve)
    return applied, unknown


def seed_role_params(curve, terrain):
    """Seed bbt_curve's shape params from the role preset (on Add / role change), then sync once.
    Sets the module _syncing flag around the writes so the panel's per-prop update callback does not
    fire a sync for each key (it runs one sync at the end instead)."""
    global _syncing
    role = _role_of(curve)
    _syncing = True
    try:
        for k in _SHAPE_KEYS:
            setattr(curve.bbt_curve, k, role[k])
    finally:
        _syncing = False
    sync_curve_params(terrain, curve)


# -- Erosion band spec -----------------------------------------------------------------------
def _curve_band_spec(curve, terrain, cap=300):
    """One curve's centreline as terrain-UV points [[u, v], ...] plus its channel width and a
    normalised seed depth, for the erosion band mask / drainage prior / channel seed. Reuses
    path_curve._ordered_polyline_xy (the same order-robust wire walk the drape uses) so the band
    tracks the channel exactly. u = x/size + 0.5; v = 0.5 - y/size (the PNG is top-row-first
    while Blender samples it V-up). `depth` is the authored channel depth mapped into the
    heightfield's normalised [0,1] range (metres / terrain height), so the seed carve matches the
    intended channel. Returns None for a degenerate curve."""
    _apply_curve_transform(curve)  # origin assumption: the curve XY IS the terrain sample point
    from . import path_curve
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


# -- Builder functions (params in, result out) -----------------------------------------------
def build_curve(curve, terrain, *, do_terrain, do_material, do_water,
                do_scatter=False, scatter_cb=None):
    """Apply one curve's channels (the middle of curve_build): drape + carve the overlay, add the
    surface band / damp bed, lay the water ribbon, sync the live params, and clear scatter along it.

    Reads the role and channel state off curve.bbt_curve. `terrain` is the mesh to carve/mask
    against (the caller resolves it). scatter_cb is an injected callable (the ui operator wraps
    the scatter build operator; MCP passes None, so the scatter step is skipped and noted).
    Returns {built, watered, surfaced, scattered, note, did, warnings, error}: error is a single
    blocking reason (no terrain) that makes the caller stop; warnings are non-fatal (missing
    BobShader, water build failed, no scatter emitter). A fresh build clears banks_from_erosion
    (the graded channel is re-imposed until an Erode sets it again)."""
    cfg = curve.bbt_curve
    role = _role_of(curve)
    impose = role.get("family") == "impose"
    result = {"built": False, "watered": False, "surfaced": False, "scattered": False,
              "slot": None, "note": "", "did": [], "warnings": [], "error": None}
    cfg.banks_from_erosion = False  # a fresh build re-imposes the graded channel (until Erode)

    # Any channel needs the overlay's masks; build it once (carve only when the Terrain channel
    # is on, else it is a mask-only overlay driving material/scatter/verge/water). A water-only
    # river still needs it, because that is where the monotonic downhill drape runs.
    needs_terrain = do_terrain or do_material or do_scatter or (do_water and impose)
    if needs_terrain and terrain is None:
        # Every requested channel needs a terrain to drape/carve/mask against; without one the
        # material band, water, and scatter would all fail with misleading downstream messages
        # (or build a water ribbon on an undraped curve). Stop here with one clear reason.
        result["error"] = "needs a terrain mesh to carve/mask against"
        return result
    if needs_terrain:
        note = _build_curve_overlay(terrain, curve, carve=do_terrain)
        result["built"] = True
        result["note"] = note
        result["did"].append(f"carved terrain ({note})" if do_terrain else "curve mask")
        if do_terrain and not note.startswith("draped"):
            # "curve Z" is not a neutral fallback: the overlay grades the bench to whatever Z the
# control points happen to hold, so on rising ground it cuts a trench and on falling
# ground it leaves the path in the air. Say so instead of reporting it as a success
# mode.
            result["warnings"].append(
                "carved at the curve's own Z, not the terrain surface: the terrain carries no "
                "bbt_heightmap, so there is nothing to drape onto. Build it from a bake "
                "(bake_heightfield then a heightmap_terrain build_geonodes) and re-build the curve, "
                "or the bench will cut through rising ground")

    if do_material:
        slot = _apply_curve_material(terrain, role)
        if slot is not None:
            result["surfaced"] = True
            # The SLOT is part of the result, not an internal detail: it is the `index` an
# apply_texture_set has to name to put a real surface on the band, and there is no other
# way to read it back (the redwood run guessed it and rendered probe frames). None for
# the impose family, whose damp bed is a whole-material knob rather than a layer.
            result["slot"] = None if impose else slot
            result["did"].append("damp bed" if impose
                                 else f"surface band (layer {slot}, channel "
                                      f"{role.get('surface_channel', 'a')})")
        else:
            result["warnings"].append("Material band needs a Terrain BobShader (shade it in Shaders)")

    # Water surface: build the ribbon AFTER the overlay drape so it sits on the descending
    # centreline. Only the impose family (river/stream) carries a water channel.
    if do_water and impose:
        if _build_water(curve) is not None:
            result["watered"] = True
            result["did"].append("water surface")
        else:
            result["warnings"].append("Could not build the water ribbon")

    # Push the shape params onto the freshly built overlay + water (they built with placeholder
    # defaults under reset=True); from here every param edit stays live via the update callback.
    sync_curve_params(terrain, curve)

    if do_scatter:
        if scatter_cb is not None and scatter_cb():
            result["scattered"] = True
            result["did"].append("cleared scatter")
        elif scatter_cb is None:
            result["warnings"].append("scatter clear skipped (no scatter callback in this context)")
        else:
            result["warnings"].append("Scatter clear needs a Scatter emitter with layers")

    return result


def run_bake_erode(terrain, curves, erode_params, *, host_bake=None, scatter_cb=None):
    """Bake & Erode (the commit step): fold the curve carves into the terrain heightfield, weather
    them with the erosion stack, then RE-IMPOSE every curve on the eroded terrain so bed, banks and
    water re-derive together and the water stays contained.

    `curves` is the list of curve objects to re-impose (each must carry bbt_curve). erode_params:
    {"strength": 0..1, "scope": "band"|"global", "deposit": bool, "seed": int}. host_bake is the
    injected bake callable (out_abs, params) -> (meta, error); when None the erode is SKIPPED and
    the result carries a note (best-effort, so an MCP caller without a reachable host still gets
    a clear answer instead of an exception). Returns {eroded, reimposed, scope, note, error}."""
    result = {"eroded": False, "reimposed": 0, "scope": "", "note": "", "error": None}
    hm = terrain.get("bbt_heightmap")
    if not hm or not os.path.exists(hm):
        result["error"] = "Terrain has no baked heightfield -- bake it in the Terrain panel first"
        return result

    # Always erode the CLEAN source, never a previous eroded output (else re-runs stack up). If the
    # terrain is currently showing an eroded PNG, fall back to the stored clean source.
    clean_src = terrain.get("bbt_heightmap_clean") if hm.endswith("_eroded.png") else hm
    if not clean_src or not os.path.exists(clean_src):
        clean_src = hm
    size = float(terrain.get("bbt_terrain_size", 90.0))
    height = float(terrain.get("bbt_terrain_height", 22.0))
    sea = float(terrain.get("bbt_terrain_sea", 0.22))
    grid_res = int(terrain.get("bbt_terrain_res", 256))

    scope = erode_params.get("scope", "band")
    deposit_on = bool(erode_params.get("deposit", True))
    seed = int(erode_params.get("seed", 0))

    # Curves to re-impose after the erode, plus their UV polylines for the band mask.
    specs, reimpose = [], []
    for curve in curves:
        if curve is None:
            continue
        cfg = curve.bbt_curve
        impose = _role_of(curve).get("family") == "impose"
        if cfg.do_terrain or cfg.do_material or cfg.do_scatter or (cfg.do_water and impose):
            reimpose.append(curve)
        if cfg.do_terrain:
            spec = _curve_band_spec(curve, terrain)
            if spec is not None:
                specs.append(spec)
    if not reimpose:
        result["error"] = "No curves with a channel to erode + re-impose"
        return result

    if host_bake is None:
        # Best-effort: without a host bake we cannot rewrite the heightfield, so leave the terrain
# as it is and report the curves that WOULD be re-imposed. The MCP handler lands here when
# the host process is not reachable from this (e.g. headless) session.
        result["note"] = ("erosion skipped (no host bake available); "
                           f"{len(reimpose)} curve(s) ready to re-impose")
        return result

    # Erosion stack: the SPLINE SEEDS the channel, then erosion SHAPES the banks (it no longer
    # re-imposes a smooth swept embankment). Per curve, channel_seed cuts a shallow bed along the
    # centreline so the fluvial solver has a slope + depression to amplify; fluvial then incises
    # with a DRAINAGE PRIOR (flow_prior boosts the drainage area on the spline, so the solver cuts
    # the valley where the river is) and thermal slumps the banks at a NOISE-WARPED repose angle
    # (talus_warp), so they read as natural weathered slopes, not one uniform ruled bank. The
    # channel is a shallow bed guarantee afterwards; the visible banks are this eroded terrain.
    s = float(erode_params.get("strength", 0.5))
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
    if deposit_on and curve_specs:
        deposit = {"kind": "deposit", "amount": 0.006 + 0.012 * s, "iterations": int(3 + 3 * s),
                   "talus_warp": 0.4 + 0.3 * s, "talus_freq": 5.0}
    if scope == "band" and specs:
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
    params = {"base_png": clean_src, "stack": stack, "backend": "auto", "seed": seed}

    # host_bake emits flow/wetness sidecar maps beside the eroded PNG (<stem>_eroded_flow.png /
    # _wetness.png). The terrain material discovers them by the sibling convention
    # (materials._terrain_maps), so after a material rebuild the riverbed layer keys off the ERODED
    # drainage, not the clean base.
    _meta, err = host_bake(out_abs, params)
    if err is not None:
        result["error"] = err
        return result

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
    for curve in reimpose:
        cfg = curve.bbt_curve
        impose = _role_of(curve).get("family") == "impose"
        cfg.banks_from_erosion = cfg.do_terrain  # eroded-channel curves keep only a shallow bed
        _build_curve_overlay(terrain, curve, carve=cfg.do_terrain)
        if cfg.do_water and impose:
            _build_water(curve)
        sync_curve_params(terrain, curve)

    if scatter_cb is not None:
        scatter_cb()

    result["eroded"] = True
    result["reimposed"] = len(reimpose)
    result["scope"] = "corridor band" if scope == "band" else "whole terrain"
    result["note"] = f"eroded ({result['scope']}), re-imposed {len(reimpose)} curve(s)"
    return result


def run_revert_erode(terrain, curves, *, scatter_cb=None):
    """Revert Bake & Erode: swap the terrain back to the clean (pre-erosion) heightfield and
    re-impose every curve on it, so the full graded channel returns. `curves` is the list of curve
    objects. Returns {reverted, reimposed, error}."""
    result = {"reverted": False, "reimposed": 0, "error": None}
    clean = terrain.get("bbt_heightmap_clean")
    if not clean or not os.path.exists(clean):
        result["error"] = "No clean heightfield recorded (nothing eroded to revert)"
        return result
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
    for curve in curves:
        if curve is None:
            continue
        cfg = curve.bbt_curve
        impose = _role_of(curve).get("family") == "impose"
        if not (cfg.do_terrain or cfg.do_material or cfg.do_scatter or (cfg.do_water and impose)):
            continue
        cfg.banks_from_erosion = False
        _build_curve_overlay(terrain, curve, carve=cfg.do_terrain)
        if cfg.do_water and impose:
            _build_water(curve)
        sync_curve_params(terrain, curve)
        n += 1
    if scatter_cb is not None:
        scatter_cb()
    result["reverted"] = True
    result["reimposed"] = n
    return result


# -- Object resolution + MCP handlers --------------------------------------------------------
def _curve_object(name):
    """Resolve a curve object by name for an op, with a clear error when it is missing or wrong type."""
    if not name:
        raise ValueError("no curve name given")
    obj = bpy.data.objects.get(name)
    if obj is None:
        raise ValueError(f"no object named {name!r} in the scene")
    if obj.type != "CURVE":
        raise ValueError(f"object {name!r} is a {obj.type}, not a CURVE")
    return obj


def _terrain_object(name, *, required=False):
    """Resolve a terrain mesh by name (optional). Raises when a named object is missing or not a
    mesh; returns None when no name is given (unless required)."""
    if not name:
        if required:
            raise ValueError("no terrain name given")
        return None
    obj = bpy.data.objects.get(name)
    if obj is None:
        raise ValueError(f"no object named {name!r} in the scene")
    if obj.type != "MESH":
        raise ValueError(f"terrain {name!r} is a {obj.type}, not a MESH")
    return obj


def _list_scene_curves(scene, names=None):
    """The curve objects to operate on for a scene-wide op: an explicit list of names when given,
    else every entry in the scene curve list (Scene.bbt_curves)."""
    if names:
        return [_curve_object(n) for n in names]
    scn = getattr(scene, "bbt_curves", None)
    if scn is None:
        return []
    return [e.curve for e in scn.curves if e.curve is not None]


def _register_curve_entry(scene, curve):
    """Add the curve to the scene curve list so an MCP-made curve shows up in the Paths panel. A
    no-op when the list is not registered (a standalone core session) or the curve is already
    listed."""
    scn = getattr(scene, "bbt_curves", None)
    if scn is None:
        return
    if any(e.curve is curve for e in scn.curves):
        return
    entry = scn.curves.add()
    entry.curve = curve
    scn.active = len(scn.curves) - 1


def make_curve(op: dict) -> dict:
    """MCP op: add a typed curve of a role. Creates a NURBS through the shared make_path op (so the
    agent and the panel author the same datablock), sets its role, and seeds the role's shape
    defaults.

    params: name (str), role (dirt_path/road/river/stream/trail/... a ROLES key), points
    (optional list of xyz control points, else a starter line sized to the terrain), terrain
    (optional mesh name, used to size the starter line and sync the seeded params), shape
    (optional dict of SHAPE_PARAMS overriding the role's defaults:
    width/depth/falloff/taper/shoulder/bank_*/water_*/ wave_*/flow/foam_*/width_var/verge_*)."""
    role = op.get("role", "dirt_path")
    if role not in ROLES:
        raise ValueError(f"unknown role {role!r} (have: {sorted(ROLES)})")
    terrain = _terrain_object(op.get("terrain"))
    base = op.get("name") or ROLES[role]["label"]
    name = _unique_object_name(base)
    points = op.get("points") or _default_points(terrain)
    if len(points) < 2:
        raise ValueError(f"need at least 2 points for a curve, got {len(points)}")
    _apply([{"op": "make_path", "name": name, "resolution": 12, "points": points}])
    obj = bpy.data.objects.get(name)
    if obj is None:
        raise ValueError("curve not created")
    obj.bbt_curve.role = role
    seed_role_params(obj, terrain)  # seed the shape defaults for this role (live from now on)
    # Shape overrides AFTER the role seed, so a caller can take a road's channel and narrow it
    # without changing the role (which would move it to the other mask channel).
    applied, unknown = set_shape(obj, terrain, op.get("shape"))
    _register_curve_entry(bpy.context.scene, obj)
    cfg = obj.bbt_curve
    info = (f"{obj.name}: {ROLES[role]['label']} ({len(points)} points, "
            f"{cfg.width:.2f} m wide, {cfg.depth:.2f} m deep)")
    if applied:
        info += f" | shape: {', '.join(sorted(applied))}"
    if unknown:
        info += f" | not shape params: {', '.join(sorted(unknown))}"
    return {"op": "make_curve", "created": [obj.name], "info": info,
            "data": {"role": role, "shape": _shape_of(obj),
                     "mask_attr": ROLES[role].get("surface_attr") or "bbt_curve_mask",
                     "edge_attr": _edge_attr_name(obj)}}


def _shape_of(curve):
    """The curve's live shape params as a plain dict, for an op result. What an agent reads back
    instead of inferring the numbers from the role table."""
    cfg = curve.bbt_curve
    return {k: round(float(getattr(cfg, k)), 4) for k in SHAPE_PARAMS
            if isinstance(getattr(cfg, k, None), float)}


def curve_build(op: dict) -> dict:
    """MCP op: build one curve's channels (carve, material band, water). Resolves the curve + terrain
    by name and calls build_curve. The channel bools default to the curve's own bbt_curve settings
    so an agent can just name the curve; pass do_terrain/do_material/do_water/do_scatter to
    override.

    `shape` (a dict of SHAPE_PARAMS) is applied BEFORE the build, so the channel is carved at the
    width and depth asked for rather than at the role's defaults.

    Scatter clear is skipped over MCP (its rebuild is a ui operator; scatter_cb is None)."""
    curve = _curve_object(op.get("curve"))
    terrain = _terrain_object(op.get("terrain"))
    cfg = curve.bbt_curve
    shaped, unknown = set_shape(curve, terrain, op.get("shape"))
    do_terrain = bool(op.get("do_terrain", cfg.do_terrain))
    do_material = bool(op.get("do_material", cfg.do_material))
    do_water = bool(op.get("do_water", cfg.do_water))
    do_scatter = bool(op.get("do_scatter", False))  # off by default over MCP (no scatter callback)
    res = build_curve(curve, terrain, do_terrain=do_terrain, do_material=do_material,
                      do_water=do_water, do_scatter=do_scatter, scatter_cb=None)
    if res["error"] is not None:
        raise ValueError(res["error"])
    bpy.context.view_layer.update()
    did = ", ".join(res["did"]) or "no channels applied"
    info = f"{curve.name}: {did}"
    if shaped:
        info += f" | shape: {', '.join(sorted(shaped))}"
    if unknown:
        info += f" | not shape params: {', '.join(sorted(unknown))}"
    if res["warnings"]:
        info += " | " + "; ".join(res["warnings"])
    created = []
    if res["built"]:
        created.append(f"{terrain.name}:{_overlay_name(curve)}" if terrain is not None else curve.name)
    if res["watered"]:
        created.append(_water_name(curve))
    role = _role_of(curve)
    # `slot` and `draped` are the two things an agent cannot read back any other way: the layer
# index an apply_texture_set must name to surface the band, and whether the carve used the
# draped Z or the curve's own (which is the difference between a graded bench and a trench
# through a hill).
    return {"op": "curve_build", "created": created, "info": info,
            "data": {"slot": res["slot"], "note": res["note"],
                     "draped": res["note"].startswith("draped"),
                     "shape": _shape_of(curve),
                     "mask_attr": role.get("surface_attr") or "bbt_curve_mask",
                     "edge_attr": _edge_attr_name(curve),
                     "warnings": res["warnings"]}}


def _host_bake_cb():
    """Wire the package-level host bake (_run_host_bake) for an MCP erode when it is reachable. Lazy
    import (it lives in the addon __init__, not core, so it survives the bridge's core-only
    reload) and wrapped with bpy.context. Returns a (out_abs, params) -> (meta, error) callable,
    or None when the package entry is not importable (a bare-core session with no addon
    __init__)."""
    try:
        from .. import _run_host_bake
    except (ImportError, ValueError):
        return None

    def _cb(out_abs, params):
        return _run_host_bake(bpy.context, out_abs, params=params, maps=True)

    return _cb


def bake_erode(op: dict) -> dict:
    """MCP op: Bake & Erode a terrain and re-impose its curves. Best-effort -- wires the host bake
    when reachable, else returns a note that the erosion was skipped (still reporting the curves
    that would be re-imposed) instead of failing.

    params: terrain (mesh name, required), curves (optional list of curve names, else every scene
    curve), strength (0..1), scope (band/global), deposit (bool), seed (int)."""
    terrain = _terrain_object(op.get("terrain"), required=True)
    curves = _list_scene_curves(bpy.context.scene, op.get("curves"))
    erode_params = {"strength": float(op.get("strength", 0.5)),
                    "scope": op.get("scope", "band"),
                    "deposit": bool(op.get("deposit", True)),
                    "seed": int(op.get("seed", 0))}
    host_bake = _host_bake_cb()
    res = run_bake_erode(terrain, curves, erode_params, host_bake=host_bake, scatter_cb=None)
    if res["error"] is not None:
        raise ValueError(res["error"])
    bpy.context.view_layer.update()
    if res["eroded"]:
        return {"op": "bake_erode", "created": [terrain.name], "info": res["note"]}
    # host bake unavailable: nothing rewritten, so report the skip note (not an error).
    return {"op": "bake_erode", "created": [], "info": res["note"]}


def revert_erode(op: dict) -> dict:
    """MCP op: revert a terrain to its clean heightfield and re-impose the curves' graded channels.

    params: terrain (mesh name, required), curves (optional list of curve names, else every scene
    curve)."""
    terrain = _terrain_object(op.get("terrain"), required=True)
    curves = _list_scene_curves(bpy.context.scene, op.get("curves"))
    res = run_revert_erode(terrain, curves, scatter_cb=None)
    if res["error"] is not None:
        raise ValueError(res["error"])
    bpy.context.view_layer.update()
    return {"op": "revert_erode", "created": [terrain.name],
            "info": f"reverted {terrain.name} to clean + re-imposed {res['reimposed']} curve(s)"}
