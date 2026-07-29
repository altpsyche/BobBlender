"""S_TerrainMaster and the terrain wrapper: the multi-layer slope/altitude/noise/paint/
curvature blend, its baked-drainage wiring, and the curve-surface layer config, ending in
the shared weather layer."""

import os

import bpy

from .. import assets
from . import texset
from .shared import TERRAIN_MASTER, _build_wrapper, _cached_group, _gin, _gout, _lerp, _mixcol, _mmath, _mrange, _vmul, _wrapper_name, master_type
from .weather import _WEATHER_EXTRA, weather_group




def _terrain_maps(obj):
    """(flow_image, wetness_image, size) for a built terrain. Loads the baked drainage maps
    (siblings of the heightmap: <base>_flow.png / <base>_wetness.png) when their files exist.
    The heightmap path is the object's bbt_heightmap prop (set by the bake), else the image
    datablock on the terrain's GN modifier; size is the bbt_terrain_size prop (fallback 90)."""
    hm = obj.get("bbt_heightmap")
    if not hm:
        for mod in obj.modifiers:
            ng = getattr(mod, "node_group", None)
            if ng is None:
                continue
            for node in ng.nodes:
                if node.bl_idname == "GeometryNodeImageTexture":
                    img = node.inputs["Image"].default_value
                    if img is not None and img.filepath:
                        hm = img.filepath
                        break
            if hm:
                break
    size = float(obj.get("bbt_terrain_size", 90.0))
    flow = wet = None
    if hm:
        base, ext = os.path.splitext(bpy.path.abspath(hm))
        ext = ext or ".png"
        fp, wp = base + "_flow" + ext, base + "_wetness" + ext
        if os.path.exists(fp):
            flow = bpy.data.images.load(fp, check_existing=True)
        if os.path.exists(wp):
            wet = bpy.data.images.load(wp, check_existing=True)
    return flow, wet, size



def terrain_material_for(obj, mat_name=None, texsets=None, box=None):
    """terrain_material for a built terrain OBJECT: gathers its baked flow/wetness maps and size
    so a rebuild (adding a layer) keeps the drainage wiring instead of dropping it. mat_name
    preserves the existing material's identity when rebuilding in place; texsets and box pass
    through, and default to what the material already records."""
    flow, wet, size = _terrain_maps(obj)
    return terrain_material(mat_name or obj.name,
                            flow_image=flow, wetness_image=wet, terrain_size=size,
                            texsets=texsets, box=box)



def _autoconfig_riverbed(mat):
    """On a fresh terrain BobShader with drainage maps, wire a sensible riverbed look: enable a
    wet-gravel layer keyed to high flow, and a baseline terrain wetness in the channels. Editable
    and disablable afterward."""
    grp = mat.node_tree.nodes.get("Master")
    if grp is None:
        return
    def setv(name, val):
        sock = grp.inputs.get(name)
        if sock is not None:
            sock.default_value = val
    setv("L1 Enable", 1.0)
    setv("L1 Base Color", (0.20, 0.17, 0.14, 1.0))  # damp gravel / silt
    setv("L1 Roughness", 0.7)
    setv("L1 Flow Strength", 1.0)
    setv("L1 Flow Threshold", 0.55)
    setv("Terrain Wetness", 0.6)



# Masks other than the curve band, cleared on the curve-surface layer so it keys ONLY off the
# curve (a road/dirt surface follows the path, not a slope or altitude).
_CURVE_OTHER_MASKS = ("Slope Strength", "Height Strength", "Noise Strength",
                      "Paint Strength", "Curvature Strength", "Flow Strength")



def apply_curve_surface(mat, base_color, roughness=0.85, height_bias=0.3, hard_edge=0.0,
                        channel="a"):
    """Configure a terrain BobShader layer as a curve surface band (BobSplines, the material band/the
    per-role surfaces): a layer keyed to a curve overlay mask, so a road/dirt surface reads only
    along a path. Mirrors _autoconfig_riverbed but for the curve mask. Idempotent: reuses the slot
    already keyed to THIS channel on a re-apply, else the highest free (disabled) slot, else the top
    slot.

    channel selects which curve mask keys the layer (the per-role surfaces): "a" -> bbt_curve_mask
    (the shared band, dirt/trail), "b" -> bbt_curve_mask_b (a distinct class, e.g. a paved road).
    Two roles on different channels therefore key two DIFFERENT layers and read as different
    surfaces; the slot keys off exactly one channel (the other is cleared).

    height_bias is kept modest on purpose (docs/SPLINES.md 9 #7): with a SOFT edge the layer's
    height field is weight (= the curve mask here) + Height Bias + macro, so off the curve weight ->
    0 and H -> Height Bias; a small bias wins the height-lerp ON the curve (mask 1 -> H ~ 1 + bias)
    but loses to a full base layer (H ~ 1) OFF it, so the surface does not bleed past the path.

    hard_edge (0..1, the hard road edge) mixes in a crisp edge: at 1 the layer's H is gated straight
    off the curve mask (a step at the band boundary) so the surface edges sharply regardless of
    Blend Softness -- a road wants ~1, a worn dirt path wants 0 (the soft feathered edge above).

    Returns the configured slot index, or None when mat is not a terrain BobShader (or is an older
    master group without this curve channel -- rebuild the material to get it).
    """
    grp = mat.node_tree.nodes.get("Master") if mat and mat.use_nodes and mat.node_tree else None
    if grp is None or master_type(mat) != "terrain":
        return None
    strength_key = "Curve Strength" if channel == "a" else "Curve B Strength"
    hard_key = "Curve Hard" if channel == "a" else "Curve B Hard"
    other_strength = "Curve B Strength" if channel == "a" else "Curve Strength"
    if grp.inputs.get("L0 " + strength_key) is None:
        return None  # an older master group without this curve channel; rebuild the material

    slot, free = None, None
    for i in range(MAX_TERRAIN_LAYERS):
        cs = grp.inputs.get(f"L{i} {strength_key}")
        en = grp.inputs.get(f"L{i} Enable")
        if cs is not None and cs.default_value > 0.0:
            slot = i  # reuse the slot already keyed to this channel
            break
        if free is None and i > 0 and en is not None and en.default_value == 0.0:
            free = i
    if slot is None:
        slot = free if free is not None else MAX_TERRAIN_LAYERS - 1
    p = f"L{slot} "

    def setv(name, val):
        sock = grp.inputs.get(p + name)
        if sock is not None:
            sock.default_value = val

    setv("Enable", 1.0)
    setv(strength_key, 1.0)
    setv(hard_key, float(hard_edge))
    setv(other_strength, 0.0)  # this slot keys off exactly one curve channel
    setv("Base Color", base_color)
    setv("Roughness", roughness)
    setv("Height Bias", height_bias)
    for m in _CURVE_OTHER_MASKS:
        setv(m, 0.0)  # a reused slot might carry another mask; the curve band keys off the curve alone
    return slot



def apply_curve_wet(mat, wetness=0.6):
    """Make a terrain BobShader read damp along a river's channel (BobSplines, the damp bed). The curve
    overlay writes bbt_curve_wet into the terrain's Wetness Map (MAX-accumulated inside
    terrain_master_group); this raises Terrain Wetness -- the multiplier that wetness path is gated
    by -- so the bed and banks read wet and glossy, weather-amplified. Idempotent and non-lowering:
    it never drops an existing higher Terrain Wetness (a riverbed autoconfig or a wetter role wins),
    so re-Build is safe. Returns True when applied, or None when mat is not a terrain BobShader (or
    an older master group without the wet path -- rebuild the material)."""
    grp = mat.node_tree.nodes.get("Master") if mat and mat.use_nodes and mat.node_tree else None
    if grp is None or master_type(mat) != "terrain":
        return None
    sock = grp.inputs.get("Terrain Wetness")
    if sock is None:
        return None
    sock.default_value = max(sock.default_value, float(wetness))
    return True

MAX_TERRAIN_LAYERS = 6


# Default per-slot base colours, a sensible soil/grass/rock/cliff/scree/sand ramp so a fresh
# terrain reads as distinct layers before any preset. Presets and the panel override these.
_TERRAIN_LAYER_COLORS = (
    (0.16, 0.11, 0.07, 1.0),  # 0 soil
    (0.15, 0.26, 0.09, 1.0),  # 1 grass
    (0.34, 0.33, 0.31, 1.0),  # 2 rock
    (0.20, 0.19, 0.18, 1.0),  # 3 cliff
    (0.45, 0.40, 0.32, 1.0),  # 4 scree
    (0.68, 0.62, 0.48, 1.0),  # 5 sand
)

_SLOPE_SOFT = 0.05  # internal slope-band easing width (scatter's band is hard; a texture

#                     reads better with a soft edge, same thresholds)


def _gated(g, mask, strength, loc):
    """mix(1, mask, strength): strength 0 leaves the mask off (returns 1), 1 applies it
    fully. The exact gate scatter uses for its altitude/noise/paint masks."""
    d = _mmath(g, "SUBTRACT", mask, 1.0, loc)
    ds = _mmath(g, "MULTIPLY", d, strength, (loc[0], loc[1] - 150))
    return _mmath(g, "ADD", 1.0, ds, (loc[0] + 170, loc[1] - 70))



def _terrain_layer(g, I, i, pos, nz, wz, pointiness, x0):
    """One layer's (colour, roughness, metallic, height-field, enable). Weight is the product
    of the scatter masks, each gated by its strength (0 = off). The height field drives the
    height-lerp: where a layer's weight is high its H rises and it wins the per-texel pick."""
    p = f"L{i} "
    y = -i * 900

    # Slope band: soft version of scatter's [Min Normal Z, Max Normal Z] keep, same thresholds.
    s_lo = _mmath(g, "SUBTRACT", I[p + "Min Normal Z"], _SLOPE_SOFT, (x0, y))
    rising = _mrange(g, nz, s_lo, I[p + "Min Normal Z"], 0.0, 1.0, (x0 + 170, y))
    s_hi = _mmath(g, "ADD", I[p + "Max Normal Z"], _SLOPE_SOFT, (x0, y - 200))
    over = _mrange(g, nz, I[p + "Max Normal Z"], s_hi, 0.0, 1.0, (x0 + 170, y - 200))
    falling = _mmath(g, "SUBTRACT", 1.0, over, (x0 + 350, y - 200))
    slope_band = _mmath(g, "MULTIPLY", rising, falling, (x0 + 520, y - 100))
    slope = _gated(g, slope_band, I[p + "Slope Strength"], (x0 + 700, y))

    # Altitude band: reproduce scatter's _height_mask (rising * falling), gated by strength.
    a_lo = _mmath(g, "SUBTRACT", I[p + "Height Min"], I[p + "Height Falloff"], (x0, y - 400))
    a_rise = _mrange(g, wz, a_lo, I[p + "Height Min"], 0.0, 1.0, (x0 + 170, y - 400))
    a_hi = _mmath(g, "ADD", I[p + "Height Max"], I[p + "Height Falloff"], (x0, y - 560))
    a_over = _mrange(g, wz, I[p + "Height Max"], a_hi, 0.0, 1.0, (x0 + 170, y - 560))
    a_fall = _mmath(g, "SUBTRACT", 1.0, a_over, (x0 + 350, y - 560))
    alt_band = _mmath(g, "MULTIPLY", a_rise, a_fall, (x0 + 520, y - 480))
    alt = _gated(g, alt_band, I[p + "Height Strength"], (x0 + 700, y - 420))

    # Noise clumping: the identical ShaderNodeTexNoise the scatter recipe uses (4D, Detail
    # 2, Roughness 0.5, W = seed) at world position, so texture and scatter clump together.
    ntex = g.nodes.new("ShaderNodeTexNoise")
    ntex.noise_dimensions = "4D"
    ntex.location = (x0, y - 720)
    ntex.inputs["Detail"].default_value = 2.0
    ntex.inputs["Roughness"].default_value = 0.5
    g.links.new(pos, ntex.inputs["Vector"])
    g.links.new(I[p + "Noise Scale"], ntex.inputs["Scale"])
    g.links.new(I[p + "Noise Seed"], ntex.inputs["W"])
    inv_c = _mmath(g, "SUBTRACT", 1.0, I[p + "Noise Contrast"], (x0 + 170, y - 760))
    half = _mmath(g, "MULTIPLY", 0.5, inv_c, (x0 + 340, y - 760))
    n_lo = _mmath(g, "SUBTRACT", 0.5, half, (x0 + 510, y - 700))
    n_hi = _mmath(g, "ADD", 0.5, half, (x0 + 510, y - 820))
    patch = _mrange(g, ntex.outputs["Fac"], n_lo, n_hi, 0.0, 1.0, (x0 + 680, y - 720))
    noise = _gated(g, patch, I[p + "Noise Strength"], (x0 + 860, y - 700))

    # Paint: a per-layer named FLOAT attribute (bbt_paint_L{i}); paint that attribute on the
    # terrain to weight the layer. Absent attribute reads 0, and Paint Strength 0 = off.
    pa = g.nodes.new("ShaderNodeAttribute")
    pa.attribute_type = "GEOMETRY"
    pa.attribute_name = f"bbt_paint_L{i}"
    pa.location = (x0, y - 900)
    paint = _gated(g, pa.outputs["Fac"], I[p + "Paint Strength"], (x0 + 200, y - 900))

    # Curvature: Cycles Pointiness (0.5 flat, >0.5 convex ridges). Favours convex ground
    # (scree on ridges); gated by strength (0 = off). Cycles-only; EEVEE reads ~flat, so the
    # term degrades rather than breaks (a baked curvature mask is the EEVEE-safe path).
    curv_mask = _mrange(g, pointiness, 0.5, 0.75, 0.0, 1.0, (x0, y - 1060))
    curv = _gated(g, curv_mask, I[p + "Curvature Strength"], (x0 + 200, y - 1060))

    # Flow: rising smoothstep on the shared Flow Map around Flow Threshold (high flow -> 1, so
    # the layer keeps to channels/riverbeds), gated by strength. A fixed soft width keeps it to
    # two knobs. With no map wired (Flow Map = 0) a Flow-Strength layer correctly vanishes -- it
    # lives only in channels, which read as absent here.
    f_lo = _mmath(g, "SUBTRACT", I[p + "Flow Threshold"], _SLOPE_SOFT, (x0, y - 1200))
    flow_band = _mrange(g, I["Flow Map"], f_lo, I[p + "Flow Threshold"], 0.0, 1.0, (x0 + 200, y - 1200))
    flow = _gated(g, flow_band, I[p + "Flow Strength"], (x0 + 380, y - 1200))

    # Curve: the curve overlay's mask attribute (bbt_curve_mask, 1 on a path band; BobSplines, the
    # material band). Keeps the layer to a path/road, gated by strength. Absent attribute reads 0,
    # so a Curve-Strength layer correctly vanishes off every curve.
    ca = g.nodes.new("ShaderNodeAttribute")
    ca.attribute_type = "GEOMETRY"
    ca.attribute_name = "bbt_curve_mask"
    ca.location = (x0, y - 1340)
    curve = _gated(g, ca.outputs["Fac"], I[p + "Curve Strength"], (x0 + 200, y - 1340))

    # Curve B: a second curve channel off bbt_curve_mask_b, so a distinct role keys its own layer
    # (BobSplines, the verge band). Same shape as Curve; gated by Curve B Strength (0 = off, the
    # default).
    cb = g.nodes.new("ShaderNodeAttribute")
    cb.attribute_type = "GEOMETRY"
    cb.attribute_name = "bbt_curve_mask_b"
    cb.location = (x0, y - 1480)
    curve_b = _gated(g, cb.outputs["Fac"], I[p + "Curve B Strength"], (x0 + 200, y - 1480))

    # Weight = all masks, then Enable gates the layer out entirely.
    w = _mmath(g, "MULTIPLY", slope, alt, (x0 + 1040, y))
    w = _mmath(g, "MULTIPLY", w, noise, (x0 + 1210, y))
    w = _mmath(g, "MULTIPLY", w, paint, (x0 + 1380, y))
    w = _mmath(g, "MULTIPLY", w, curv, (x0 + 1550, y))
    w = _mmath(g, "MULTIPLY", w, flow, (x0 + 1720, y))
    w = _mmath(g, "MULTIPLY", w, curve, (x0 + 1890, y))
    w = _mmath(g, "MULTIPLY", w, curve_b, (x0 + 2060, y))

    # Height field for the height-lerp: presence (weight) + a per-layer macro noise + bias.
    mtex = g.nodes.new("ShaderNodeTexNoise")
    mtex.noise_dimensions = "4D"
    mtex.location = (x0 + 1040, y - 300)
    mtex.inputs["Detail"].default_value = 2.0
    mtex.inputs["W"].default_value = float(i) * 7.0
    g.links.new(pos, mtex.inputs["Vector"])
    g.links.new(I["Macro Scale"], mtex.inputs["Scale"])
    m_c = _mmath(g, "SUBTRACT", mtex.outputs["Fac"], 0.5, (x0 + 1220, y - 300))
    m_a = _mmath(g, "MULTIPLY", m_c, I["Macro Amount"], (x0 + 1390, y - 300))
    H = _mmath(g, "ADD", w, I[p + "Height Bias"], (x0 + 1560, y - 200))
    H = _mmath(g, "ADD", H, m_a, (x0 + 1730, y - 200))
    # Hard curve edge (the hard road edge): a steep remap of the raw curve mask swings H from a
    # floor off the band to well above every other layer on it, so the height-lerp pick flips within
    # the mask's own falloff (a crisp road edge) rather than over Blend Softness. Curve Hard 0 keeps
    # the soft H, so every layer that does not opt in (all of them by default) is byte-identical to
    # before.
    hard_H = _mrange(g, ca.outputs["Fac"], 0.45, 0.55, -1.0, 2.0, (x0 + 1560, y - 1620))
    H = _lerp(g, H, hard_H, I[p + "Curve Hard"], (x0 + 1900, y - 200))
    hard_H_b = _mrange(g, cb.outputs["Fac"], 0.45, 0.55, -1.0, 2.0, (x0 + 1560, y - 1760))
    H = _lerp(g, H, hard_H_b, I[p + "Curve B Hard"], (x0 + 2080, y - 200))
    # Colour as tint and scalar-multiply the maps (identity when no texture set: white / 1).
    col = _vmul(g, I[p + "Base Color"], I[p + "Albedo Map"], (x0 + 1560, y - 380))
    rough = _mmath(g, "MULTIPLY", I[p + "Roughness"], I[p + "Roughness Map"], (x0 + 1560, y - 500))
    return col, rough, I[p + "Metallic"], H, I[p + "Enable"], I[p + "Detail Height"]



def terrain_master_group():
    """The multi-layer terrain blend, ending in S_Weather. One shared group, MAX_TERRAIN_LAYERS
    fixed slots; the stack is the enabled ones. See the module comment for the height-lerp."""
    g, _fresh = _cached_group(TERRAIN_MASTER)
    if not _fresh:
        return g

    _gin(g, "Blend Softness", "NodeSocketFloat", 0.15, 0.001, 1.0)
    _gin(g, "Macro Amount", "NodeSocketFloat", 0.15, 0.0, 1.0)
    _gin(g, "Macro Scale", "NodeSocketFloat", 0.3, 0.0)
    # The baked drainage-flow map, sampled per-terrain in the wrapper and fed here (0 = none).
    # A layer's Flow mask keys off this, so a sediment/gravel layer can live in the channels.
    _gin(g, "Flow Map", "NodeSocketFloat", 0.0, 0.0, 1.0)
    for i in range(MAX_TERRAIN_LAYERS):
        p = f"L{i} "
        _gin(g, p + "Enable", "NodeSocketFloat", 1.0 if i == 0 else 0.0, 0.0, 1.0)
        _gin(g, p + "Base Color", "NodeSocketColor", _TERRAIN_LAYER_COLORS[i])
        _gin(g, p + "Roughness", "NodeSocketFloat", 0.85, 0.0, 1.0)
        _gin(g, p + "Metallic", "NodeSocketFloat", 0.0, 0.0, 1.0)
        _gin(g, p + "Height Bias", "NodeSocketFloat", float(i) * 0.05)
        _gin(g, p + "Min Normal Z", "NodeSocketFloat", 0.0, 0.0, 1.0)
        _gin(g, p + "Max Normal Z", "NodeSocketFloat", 1.0, 0.0, 1.0)
        _gin(g, p + "Slope Strength", "NodeSocketFloat", 0.0, 0.0, 1.0)
        _gin(g, p + "Height Min", "NodeSocketFloat", -1000.0)
        _gin(g, p + "Height Max", "NodeSocketFloat", 1000.0)
        _gin(g, p + "Height Falloff", "NodeSocketFloat", 5.0, 0.0)
        _gin(g, p + "Height Strength", "NodeSocketFloat", 0.0, 0.0, 1.0)
        _gin(g, p + "Noise Scale", "NodeSocketFloat", 0.15, 0.0)
        _gin(g, p + "Noise Contrast", "NodeSocketFloat", 0.5, 0.0, 1.0)
        _gin(g, p + "Noise Seed", "NodeSocketFloat", float(i))
        _gin(g, p + "Noise Strength", "NodeSocketFloat", 0.0, 0.0, 1.0)
        _gin(g, p + "Paint Strength", "NodeSocketFloat", 0.0, 0.0, 1.0)
        _gin(g, p + "Curvature Strength", "NodeSocketFloat", 0.0, 0.0, 1.0)
        # Flow band: keep this layer where the drainage-flow map is above Flow Threshold
        # (channels/riverbeds). Gated by Flow Strength (0 = off, the default).
        _gin(g, p + "Flow Strength", "NodeSocketFloat", 0.0, 0.0, 1.0)
        _gin(g, p + "Flow Threshold", "NodeSocketFloat", 0.6, 0.0, 1.0)
        # Curve band: keep this layer to a path/road, keyed off the curve overlay's baked
        # bbt_curve_mask attribute (BobSplines, the material band, docs/SPLINES.md 4.4). Gated by
        # Curve Strength (0 = off, the default), the same shape as the Flow mask keying a riverbed
        # layer.
        _gin(g, p + "Curve Strength", "NodeSocketFloat", 0.0, 0.0, 1.0)
        # Curve Hard (BobSplines the hard road edge, docs/SPLINES.md 9 #7): 0 = the soft height-lerp
        # edge (default, unchanged), 1 = a crisp edge gated straight off the curve mask, so a road
        # surface stops sharply at the band regardless of Blend Softness rather than feathering over
        # it.
        _gin(g, p + "Curve Hard", "NodeSocketFloat", 0.0, 0.0, 1.0)
        # Curve B (BobSplines, the verge band): a SECOND curve channel keyed off bbt_curve_mask_b,
        # so a distinct role (a paved road) keys its own surface layer without sharing the dirt-path
        # look. Same shape as Curve; both default off, so a layer opts into at most one.
        _gin(g, p + "Curve B Strength", "NodeSocketFloat", 0.0, 0.0, 1.0)
        _gin(g, p + "Curve B Hard", "NodeSocketFloat", 0.0, 0.0, 1.0)
        # Texture-set maps, identity defaults so an untextured layer is unchanged.
        _gin(g, p + "Albedo Map", "NodeSocketColor", (1.0, 1.0, 1.0, 1.0))
        _gin(g, p + "Roughness Map", "NodeSocketFloat", 1.0, 0.0, 1.0)
        _gin(g, p + "Detail Height", "NodeSocketFloat", 0.0)
    # Weather passthrough (identical to S_Weather's inputs).
    _gin(g, "Snow Strength", "NodeSocketFloat", 1.0, 0.0, 1.0)
    _gin(g, "Slope Threshold", "NodeSocketFloat", 0.5, 0.0, 1.0)
    _gin(g, "Slope Falloff", "NodeSocketFloat", 0.2, 0.0, 1.0)
    for _wn, _wd in _WEATHER_EXTRA:
        _gin(g, _wn, "NodeSocketFloat", _wd, 0.0, 1.0)
    # Terrain-only wetness passthrough (fed from the wrapper's wetness-map sample). Declared
    # here rather than in _WEATHER_EXTRA so surface materials do not carry these unused inputs.
    _gin(g, "Wetness Map", "NodeSocketFloat", 0.0, 0.0, 1.0)
    _gin(g, "Terrain Wetness", "NodeSocketFloat", 0.0, 0.0, 1.0)
    _gout(g, "Base Color", "NodeSocketColor")
    _gout(g, "Roughness", "NodeSocketFloat")
    _gout(g, "Metallic", "NodeSocketFloat")
    _gout(g, "Height", "NodeSocketFloat")  # blended detail height, the wrapper bumps it

    gi = g.nodes.new("NodeGroupInput")
    gi.location = (-3000, 0)
    go = g.nodes.new("NodeGroupOutput")
    go.location = (2200, 0)
    I = gi.outputs

    geo = g.nodes.new("ShaderNodeNewGeometry")
    geo.location = (-3000, -1600)
    nsep = g.nodes.new("ShaderNodeSeparateXYZ")
    nsep.location = (-2820, -1560)
    g.links.new(geo.outputs["Normal"], nsep.inputs[0])
    psep = g.nodes.new("ShaderNodeSeparateXYZ")
    psep.location = (-2820, -1720)
    g.links.new(geo.outputs["Position"], psep.inputs[0])
    pos, nz, wz = geo.outputs["Position"], nsep.outputs["Z"], psep.outputs["Z"]
    pointiness = geo.outputs["Pointiness"]

    layers = [_terrain_layer(g, I, i, pos, nz, wz, pointiness, -2600)
              for i in range(MAX_TERRAIN_LAYERS)]

    # Height-lerp fold: acc starts at layer 0 (the base fill); each further layer blends in
    # by fac = enable * b2/(b1+b2), where b1/b2 are the heights above (max(H) - Blend Softness),
    # so the higher-H layer wins per texel within the soft band. Enable gates a layer out.
    soft = I["Blend Softness"]
    acc_col, acc_rough, acc_metal, acc_H, _e0, acc_dh = layers[0]
    fx = 700
    for i in range(1, MAX_TERRAIN_LAYERS):
        col, rough, metal, H, enable, dh = layers[i]
        fy = -i * 300
        # Gate the height by Enable before it enters the fold. Enable only zeroing the colour blend
        # factor (below) let a DISABLED layer still push its height into acc_H, raising the bar for
        # later layers and suppressing them (a disabled slot's masks default to strength 0, so its
        # height is high). Fold H toward a floor when disabled so it never wins and never pollutes
        # acc_H; an enabled layer (Enable 1) is unchanged, so enabled terrains are identical.
        H = _lerp(g, -1000.0, H, enable, (fx - 200, fy))
        hmax = _mmath(g, "MAXIMUM", acc_H, H, (fx, fy))
        ma = _mmath(g, "SUBTRACT", hmax, soft, (fx + 170, fy))
        b1 = _mmath(g, "MAXIMUM", _mmath(g, "SUBTRACT", acc_H, ma, (fx + 340, fy + 80)), 0.0, (fx + 510, fy + 80))
        b2 = _mmath(g, "MAXIMUM", _mmath(g, "SUBTRACT", H, ma, (fx + 340, fy - 80)), 0.0, (fx + 510, fy - 80))
        bsum = _mmath(g, "ADD", _mmath(g, "ADD", b1, b2, (fx + 680, fy)), 1e-6, (fx + 850, fy))
        fac = _mmath(g, "DIVIDE", b2, bsum, (fx + 1020, fy))
        fac = _mmath(g, "MULTIPLY", fac, enable, (fx + 1190, fy))  # disabled layer never wins
        acc_col = _mixcol(g, fac, acc_col, col, (fx + 1360, fy + 200))
        acc_rough = _lerp(g, acc_rough, rough, fac, (fx + 1360, fy))
        acc_metal = _lerp(g, acc_metal, metal, fac, (fx + 1360, fy - 220))
        acc_dh = _lerp(g, acc_dh, dh, fac, (fx + 1360, fy - 560))
        acc_H = _mmath(g, "MAXIMUM", acc_H, H, (fx + 1360, fy - 400))
        fx += 1600

    # End in S_Weather: the blended base + the snow passthrough.
    weather = g.nodes.new("ShaderNodeGroup")
    weather.node_tree = weather_group()
    weather.location = (fx + 200, 0)
    g.links.new(acc_col, weather.inputs["Base Color"])
    g.links.new(acc_rough, weather.inputs["Roughness"])
    g.links.new(acc_metal, weather.inputs["Metallic"])
    # Damp bed (BobSplines, the damp bed): a river/stream overlay writes bbt_curve_wet along its
    # channel; MAX it into the Wetness Map so the bed and banks read damp (materials.apply_curve_wet
    # raises Terrain Wetness, the multiplier that path is gated by, so it shows). An absent
    # attribute reads 0, so a terrain with no river is byte-identical; weather still amplifies it
    # (rain raises env wetness in S_Weather, and wetf takes the MAX of the terrain map and the
    # weather wetness).
    cwet = g.nodes.new("ShaderNodeAttribute")
    cwet.attribute_type = "GEOMETRY"
    cwet.attribute_name = "bbt_curve_wet"
    cwet.location = (fx + 40, -900)
    wetmap = g.nodes.new("ShaderNodeMath")
    wetmap.operation = "MAXIMUM"
    wetmap.use_clamp = True
    wetmap.location = (fx + 220, -900)
    g.links.new(I["Wetness Map"], wetmap.inputs[0])
    g.links.new(cwet.outputs["Fac"], wetmap.inputs[1])
    g.links.new(wetmap.outputs["Value"], weather.inputs["Wetness Map"])
    for name in ("Snow Strength", "Slope Threshold", "Slope Falloff",
                 "Terrain Wetness",
                 *[n for n, _ in _WEATHER_EXTRA]):
        g.links.new(I[name], weather.inputs[name])
    for name in ("Base Color", "Roughness", "Metallic"):
        g.links.new(weather.outputs[name], go.inputs[name])
    g.links.new(acc_dh, go.inputs["Height"])
    return g



def terrain_material(mat_name, flow_image=None, wetness_image=None,
                     terrain_size=None, texsets=None, box=None):
    """A multi-layer terrain wrapper (S_TerrainMaster): per-layer tints, the baked flow/wetness
    maps, and optionally a texture set sampled into each layer's map inputs.

    flow_image / wetness_image are the baked drainage maps (siblings of the heightmap). When
    given, they are sampled per-terrain by object-space XY (UV = pos.xy/size + 0.5, matching the
    heightmap_terrain displacement) and fed into the master's Flow Map / Wetness Map inputs, so a
    layer's Flow mask and the terrain wetness key off the terrain's own drainage.

    texsets is one texture-set name per layer slot ("" = solid tint, the default), and box picks
    triplanar over top-down planar projection. Both default to whatever the material already
    records, so a rebuild for any other reason (adding a layer, re-baking drainage) carries the
    textures forward instead of silently dropping back to tints."""
    master = terrain_master_group()
    size = float(terrain_size or 90.0)
    prev = bpy.data.materials.get(_wrapper_name(mat_name))
    sets, prev_box = texset.stored_sets(prev, MAX_TERRAIN_LAYERS)
    if texsets is not None:
        sets = ([str(s or "") for s in texsets] + [""] * MAX_TERRAIN_LAYERS)[:MAX_TERRAIN_LAYERS]
    box = prev_box if box is None else bool(box)
    # Resolve the set files up front, so a set that no longer exists on disk degrades to a solid
    # tint rather than raising inside the node build.
    layer_maps = {i: m for i, m in ((i, assets.texture_set_maps(s)) for i, s in enumerate(sets) if s) if m}
    sig = ("terrain|"
           + ("|flow" if flow_image is not None else "")
           + ("|wet" if wetness_image is not None else "")
           + texset.sig_part(sets, box))

    def _map_uv(nt):
        """Object-space XY -> heightmap UV, shared by the flow/wetness samples. Object coords
        match the grid's local space (where the heightmap was sampled), so the maps line up even
        if the terrain object is moved."""
        tc = nt.nodes.new("ShaderNodeTexCoord")
        tc.location = (-1000, -520)
        sep = nt.nodes.new("ShaderNodeSeparateXYZ")
        sep.location = (-820, -520)
        nt.links.new(tc.outputs["Object"], sep.inputs[0])
        u = _mmath(nt, "ADD", _mmath(nt, "DIVIDE", sep.outputs["X"], size, (-640, -480)), 0.5, (-460, -480))
        v = _mmath(nt, "ADD", _mmath(nt, "DIVIDE", sep.outputs["Y"], size, (-640, -620)), 0.5, (-460, -620))
        uvw = nt.nodes.new("ShaderNodeCombineXYZ")
        uvw.location = (-300, -540)
        nt.links.new(u, uvw.inputs["X"])
        nt.links.new(v, uvw.inputs["Y"])
        return uvw.outputs["Vector"]

    def wire(nt, grp, bsdf, old_sig):
        # Baked drainage maps: sample by the shared object-space UV and feed the master.
        if flow_image is not None or wetness_image is not None:
            uv = _map_uv(nt)
            for img, socket, yy in ((flow_image, "Flow Map", -520),
                                    (wetness_image, "Wetness Map", -760)):
                if img is None:
                    continue
                img.colorspace_settings.name = "Non-Color"
                tex = nt.nodes.new("ShaderNodeTexImage")
                tex.image = img
                tex.interpolation = "Linear"
                tex.extension = "EXTEND"
                tex.location = (-120, yy)
                nt.links.new(uv, tex.inputs["Vector"])
                nt.links.new(tex.outputs["Color"], grp.inputs[socket])

        # Texture sets. Object coordinates in BOTH projection modes: the terrain is a
        # GN-generated grid with no UV layer, so flat here means a top-down planar projection
        # and box adds the cliff faces. One coordinate node feeds every layer's Mapping.
        if not layer_maps:
            return
        coord = nt.nodes.new("ShaderNodeTexCoord")
        coord.name = texset.TEXSET_NODE_PREFIX + "Coord"
        coord.location = (-2500, 400)
        sampled = 0
        for i, maps in sorted(layer_maps.items()):
            node = texset.texset_sample(nt, f"L{i}", maps, coord.outputs["Object"], box=box,
                                        loc=(-2200, 600 - i * 800))
            if node is None:
                continue
            sampled += 1
            nt.links.new(node.outputs["Albedo Map"], grp.inputs[f"L{i} Albedo Map"])
            nt.links.new(node.outputs["Roughness Map"], grp.inputs[f"L{i} Roughness Map"])
            nt.links.new(node.outputs["Detail Height"], grp.inputs[f"L{i} Detail Height"])
        # The master's Height output is the height-lerp's blended detail height, so one Bump
        # gives every textured layer its relief, following whichever layer won per texel.
        if sampled:
            texset.texset_bump(nt, grp.outputs["Height"], bsdf, loc=(100, -340))

    mat = _build_wrapper(mat_name, master, sig, wire)
    texset.store_sets(mat, sets, box)
    return mat
