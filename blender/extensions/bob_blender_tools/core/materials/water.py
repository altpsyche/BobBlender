"""S_WaterMaster and the water wrapper: flowing, foaming, freezing water read from a
curve ribbon's attributes (BobSplines C5), ending in the shared weather layer."""

import os

import bpy

from .shared import WATER_MASTER, _build_wrapper, _cached_group, _gin, _gout, _lerp, _mixcol, _mmath, _mplug, _mrange, _vscale
from .weather import _WEATHER_EXTRA, env_state_group, weather_group


_WATER_SHALLOW = (0.16, 0.34, 0.36, 1.0)  # near-shore tint

_WATER_DEEP = (0.02, 0.09, 0.13, 1.0)     # deep body

_WATER_FOAM = (0.92, 0.95, 0.97, 1.0)

_WATER_ICE = (0.66, 0.77, 0.83, 1.0)      # pale blue-white frozen tint

_ICE_ROUGHNESS = 0.32                     # frosted-glassy ice (vs the near-mirror liquid roughness)



def set_water_render_flags(mat):
    """Make a water material actually read as water in EEVEE-Next (W1): raytraced refraction so the
    0.92 Transmission bends what is behind the surface instead of reading flat grey. These are 5.2
    material datablock flags (probed live, not guessed): use_raytrace_refraction is the EEVEE-Next
    name (use_screen_refraction is the retained 4.x alias, set too for safety); show_transparent_back
    off drops the doubled back face; DITHERED keeps the surface refraction-capable (BLENDED alpha
    cannot refract). No-op / harmless in Cycles, which refracts from the Transmission weight alone.
    The scene's eevee.use_raytracing must also be on -- see enable_eevee_refraction, called from the
    addon build path (a scene setting, out of a material builder's remit)."""
    if mat is None:
        return
    for flag in ("use_raytrace_refraction", "use_screen_refraction"):
        if hasattr(mat, flag):
            setattr(mat, flag, True)
    if hasattr(mat, "show_transparent_back"):
        mat.show_transparent_back = False
    if hasattr(mat, "use_transparent_shadow"):
        mat.use_transparent_shadow = True
    if hasattr(mat, "surface_render_method"):
        mat.surface_render_method = "DITHERED"  # DITHERED refracts; BLENDED (alpha) does not



def enable_eevee_refraction(scene):
    """Turn on the scene-level EEVEE-Next ray tracing that raytraced refraction needs to show (W1).
    A no-op unless the active engine is EEVEE, and it only ever switches the toggle ON (never off, so
    it will not fight a user who wants it off). Cycles needs nothing here. Kept out of the material
    builder because it mutates the scene; the addon calls it when it builds/assigns water."""
    if scene is None:
        return
    eng = getattr(getattr(scene, "render", None), "engine", "")
    ee = getattr(scene, "eevee", None)
    if ee is not None and eng.startswith("BLENDER_EEVEE") and hasattr(ee, "use_raytracing"):
        ee.use_raytracing = True



def water_master_group():
    """The water-surface master (see the section comment). The surface animates live off a
    frame-driven Value node, no bake:
      - Waves (W2): three flow-advected noise octaves (a slow swell, the main ripple, a fine chop)
        chained into one normal. Each octave is sampled at TWO phases half a cycle apart and blended
        by a triangle wave, the flow-map trick, so the advection resets without a visible pop even
        where bbt_flow diverges (banks vs mid-channel, rapids). All three fade out as it freezes.
      - Foam (W3): bbt_foam (banks + rapids) is lifted by a shore term (shallow water near the banks,
        from bbt_shore), broken up by a flow-scrolled noise, and thresholded to crisp lines whose
        sharpness is Foam Crispness -- not a flat white wash. Foam also roughens the surface.
      - Freeze (W4): a manual Frozen input OR the env below-0 term freezes the water: the flow normal
        collapses to glassy-flat, cracked-ice detail fades in (a Voronoi distance-to-edge bump),
        transmission drops to opaque, and (manual path) the albedo tints icy blue-white. The shared
        S_Weather frost term adds the winter sheen on the env-cold path.
    Beyond Base Color / Roughness / Metallic it outputs Transmission / IOR / Alpha / Normal, which
    the widened _build_wrapper drives into the Principled BSDF."""
    g, _fresh = _cached_group(WATER_MASTER)
    if not _fresh:
        return g
    _gin(g, "Shallow Color", "NodeSocketColor", _WATER_SHALLOW)
    _gin(g, "Deep Color", "NodeSocketColor", _WATER_DEEP)
    _gin(g, "Depth", "NodeSocketFloat", 1.5, 0.0)
    # W5 depth interaction, keyed to the ribbon's per-vertex bbt_depth (metres of water column):
    # Absorption is the Beer-Lambert extinction per metre (deep water reads darker/deeper-coloured),
    # Depth Opacity fades the transmission out with depth (deep = more opaque, so the bed hides),
    # Shoreline Fade is the metres of water column over which the edge dissolves to transparent (a
    # soft waterline instead of a hard cut). All degrade gracefully to the old shore look at depth 0.
    _gin(g, "Depth Absorption", "NodeSocketFloat", 0.5, 0.0)
    _gin(g, "Depth Opacity", "NodeSocketFloat", 0.5, 0.0, 1.0)
    # Shoreline Fade: fraction of the half-width (from the bank inward) over which the surface fades to
    # transparent, so the waterline dissolves into the bank. Keyed to bbt_shore (always present, so a
    # pre-W5 ribbon is safe), not bbt_depth (0 there would make a mis-paired old ribbon vanish).
    _gin(g, "Shoreline Fade", "NodeSocketFloat", 0.15, 0.0, 1.0)
    _gin(g, "Water Roughness", "NodeSocketFloat", 0.04, 0.0, 1.0)
    _gin(g, "IOR", "NodeSocketFloat", 1.33, 1.0, 2.0)
    _gin(g, "Transmission", "NodeSocketFloat", 0.92, 0.0, 1.0)
    _gin(g, "Flow Speed", "NodeSocketFloat", 0.08, 0.0)
    # Ripple Strength is the SHADER micro-detail normal only (fine surface texture); the visible
    # waves are geometry Gerstner in curve_water. Kept low so it never combs into streaks.
    _gin(g, "Ripple Strength", "NodeSocketFloat", 0.10, 0.0, 2.0)
    _gin(g, "Ripple Scale", "NodeSocketFloat", 1.8, 0.0)
    _gin(g, "Wave Detail", "NodeSocketFloat", 0.5, 0.0, 1.0)
    # Surface Texture (issue 3): strength of a tiling multi-scale normal sampled in the ribbon's
    # flow-space UV (bbt_water_uv), scrolled downstream. Gives the surface real detail texture instead
    # of reading flat; 0 = the old plain procedural look.
    _gin(g, "Surface Texture", "NodeSocketFloat", 0.6, 0.0, 2.0)
    _gin(g, "Foam Color", "NodeSocketColor", _WATER_FOAM)
    _gin(g, "Foam Amount", "NodeSocketFloat", 1.2, 0.0, 2.0)
    _gin(g, "Shore Foam", "NodeSocketFloat", 0.6, 0.0, 1.0)
    _gin(g, "Foam Crispness", "NodeSocketFloat", 0.5, 0.0, 1.0)
    _gin(g, "Edge Fade", "NodeSocketFloat", 0.0, 0.0, 1.0)
    _gin(g, "Frozen", "NodeSocketFloat", 0.0, 0.0, 1.0)
    # Weather passthrough (as surface_master), so the shared S_Weather layer works. Snow defaults
    # OFF for water: flowing water sheds snow, and the winter look comes from the frost/freeze path.
    _gin(g, "Snow Strength", "NodeSocketFloat", 0.0, 0.0, 1.0)
    _gin(g, "Slope Threshold", "NodeSocketFloat", 0.5, 0.0, 1.0)
    _gin(g, "Slope Falloff", "NodeSocketFloat", 0.2, 0.0, 1.0)
    for _wn, _wd in _WEATHER_EXTRA:
        _gin(g, _wn, "NodeSocketFloat", _wd, 0.0, 1.0)
    _gout(g, "Base Color", "NodeSocketColor")
    _gout(g, "Roughness", "NodeSocketFloat")
    _gout(g, "Metallic", "NodeSocketFloat")
    _gout(g, "Transmission", "NodeSocketFloat")
    _gout(g, "IOR", "NodeSocketFloat")
    _gout(g, "Alpha", "NodeSocketFloat")
    _gout(g, "Normal", "NodeSocketVector")

    gi = g.nodes.new("NodeGroupInput")
    gi.location = (-1900, 0)
    go = g.nodes.new("NodeGroupOutput")
    go.location = (1500, 0)
    I, O = gi.outputs, go.inputs

    # Live env: Temperature drives the freeze term (shared group, driven from bbt_env by the panel;
    # 15 C default when Firmament is absent, so env cold -> 0 and the water never freezes standalone
    # -- which is why the manual Frozen input exists). frozen = max(manual, env cold); liquid = 1 - it.
    env = g.nodes.new("ShaderNodeGroup")
    env.node_tree = env_state_group()
    env.location = (-1900, -900)
    cold = _mrange(g, env.outputs["Temperature"], 0.0, -6.0, 0.0, 1.0, (-1700, -900))  # 0 warm, 1 icy
    frozen = _mmath(g, "MAXIMUM", cold, I["Frozen"], (-1520, -900))
    liquid = _mmath(g, "SUBTRACT", 1.0, frozen, (-1340, -900))

    # Ribbon attributes (curve_water stores these on the water mesh).
    def _geo_attr(name, y):
        n = g.nodes.new("ShaderNodeAttribute")
        n.attribute_type = "GEOMETRY"
        n.attribute_name = name
        n.location = (-1900, y)
        return n
    flow = _geo_attr("bbt_flow", 380)
    foam_a = _geo_attr("bbt_foam", 240)
    shore_a = _geo_attr("bbt_shore", 100)
    depth_a = _geo_attr("bbt_depth", -40)  # metres of water column (W5); 0 on pre-W5 ribbons

    # Beer-Lambert depth extinction: depth_fac 0 at the shoreline (bbt_depth 0) rising toward 1 in
    # deep water, = 1 - exp(-Absorption * depth). Drives the depth colour and the depth opacity below.
    depth_fac = _mmath(g, "SUBTRACT", 1.0,
                       _mmath(g, "EXPONENT",
                              _mmath(g, "MULTIPLY", -1.0,
                                     _mmath(g, "MULTIPLY", I["Depth Absorption"],
                                            depth_a.outputs["Fac"], (-1720, -40)), (-1560, -40)),
                              None, (-1400, -40)), (-1240, -40))

    # Frame-driven time (mirrors _install_env_drivers, but `frame` is a built-in driver variable so
    # no scene target is needed): waves scroll without a bake. Installed once on the fresh group.
    tval = g.nodes.new("ShaderNodeValue")
    tval.name = tval.label = "water_time"
    tval.location = (-1900, -260)
    try:
        fc = tval.outputs[0].driver_add("default_value")
        fc = fc[0] if isinstance(fc, list) else fc
        fc.driver.type = "SCRIPTED"
        fc.driver.expression = "frame"
    except (RuntimeError, TypeError):
        pass  # no anim context (headless build): waves hold at frame 0, the surface still renders
    time = _mmath(g, "MULTIPLY", tval.outputs[0], I["Flow Speed"], (-1700, -260))
    geo = g.nodes.new("ShaderNodeNewGeometry")
    geo.location = (-1900, 560)
    pos = geo.outputs["Position"]

    def _vsub(a, b, loc):
        n = g.nodes.new("ShaderNodeVectorMath")
        n.operation = "SUBTRACT"
        n.location = loc
        g.links.new(a, n.inputs[0])
        g.links.new(b, n.inputs[1])
        return n.outputs["Vector"]

    def _noise(vec, scale, w, detail, loc):
        n = g.nodes.new("ShaderNodeTexNoise")
        n.noise_dimensions = "4D"
        n.location = loc
        n.inputs["Detail"].default_value = detail
        n.inputs["Roughness"].default_value = 0.5
        g.links.new(vec, n.inputs["Vector"])
        _mplug(g, n.inputs["Scale"], scale)
        _mplug(g, n.inputs["W"], w)
        return n.outputs["Fac"]

    def _flow_octave(scale, scroll, wspeed, detail, y):
        """One flow-advected noise height, sampled at two phases 0.5 apart and cross-faded by a
        triangle wave (weight 1 at a reset seam, 0 mid-cycle) so the scroll never pops. `scroll`
        is the advection distance in metres per cycle, `scale` the noise frequency."""
        p0 = _mmath(g, "FRACT", time, None, (-1500, y))
        p1 = _mmath(g, "FRACT", _mmath(g, "ADD", time, 0.5, (-1500, y - 130)), None, (-1330, y - 130))
        d0 = _mmath(g, "MULTIPLY", p0, scroll, (-1330, y))
        d1 = _mmath(g, "MULTIPLY", p1, scroll, (-1330, y - 260))
        s0 = _noise(_vsub(pos, _vscale(g, flow.outputs["Vector"], d0, (-1150, y)), (-980, y)),
                    scale, _mmath(g, "MULTIPLY", time, wspeed, (-1150, y - 400)), detail, (-800, y))
        s1 = _noise(_vsub(pos, _vscale(g, flow.outputs["Vector"], d1, (-1150, y - 260)),
                          (-980, y - 260)),
                    scale, _mmath(g, "MULTIPLY", _mmath(g, "ADD", time, 0.5, (-1330, y - 520)),
                                  wspeed, (-1150, y - 520)), detail, (-800, y - 260))
        w = _mmath(g, "ABSOLUTE",
                   _mmath(g, "SUBTRACT", _mmath(g, "MULTIPLY", p0, 2.0, (-800, y - 400)), 1.0,
                          (-620, y - 400)), None, (-440, y - 400))
        return _lerp(g, s0, s1, w, (-440, y - 160))

    def _wave_bump(height, strength, normal_in, loc):
        b = g.nodes.new("ShaderNodeBump")
        b.location = loc
        _mplug(g, b.inputs["Strength"], strength)
        g.links.new(height, b.inputs["Height"])
        if normal_in is not None:
            g.links.new(normal_in, b.inputs["Normal"])
        return b.outputs["Normal"]

    # Fine surface detail only. The VISIBLE waves are geometry Gerstner (curve_water); a
    # low-frequency bump here combed into hair-like streaks under a grazing view, so this is now two
    # HIGH-frequency, LOW-strength flow-scrolled octaves that read as micro-texture riding on the
    # geometry waves. Chained through the Bump Normal input; both fade out as it freezes.
    rs = _mmath(g, "MULTIPLY", I["Ripple Strength"], liquid, (-260, 620))
    fine_str = _mmath(g, "MULTIPLY", _mmath(g, "MULTIPLY", rs, 0.6, (-260, 140)),
                      I["Wave Detail"], (-80, 140))
    mid_h = _flow_octave(_mmath(g, "MULTIPLY", I["Ripple Scale"], 2.0, (-1700, 620)),
                         0.5, 0.35, 3.0, 620)
    fine_h = _flow_octave(_mmath(g, "MULTIPLY", I["Ripple Scale"], 5.0, (-1700, 140)),
                          0.3, 0.60, 4.0, 140)
    n_mid = _wave_bump(mid_h, rs, None, (140, 560))
    n_fine = _wave_bump(fine_h, fine_str, n_mid, (400, 320))

    # UV-space detail normal (issue 3): sample a tiling multi-scale noise in the ribbon's flow-space
    # UV (bbt_water_uv: U = arc length downstream in metres, V = across-width 0..1), scrolled along U
    # by the frame time so the detail travels DOWNSTREAM in the surface's own frame. Flow-aligned by
    # construction, not a world-space advection, so it does NOT comb into hair streaks like the earlier
    # advected bump. V is stretched (x6) so the noise varies across the width too instead of streaking
    # purely along flow. A pre-batch-1 ribbon reads the attribute as 0 -> flat, a safe no-op. Strength
    # = Surface Texture, faded out as it freezes.
    uv_a = _geo_attr("bbt_water_uv", -160)
    uvsep = g.nodes.new("ShaderNodeSeparateXYZ")
    uvsep.location = (-1300, -160)
    g.links.new(uv_a.outputs["Vector"], uvsep.inputs[0])
    uvcoord = g.nodes.new("ShaderNodeCombineXYZ")
    uvcoord.location = (-940, -160)
    g.links.new(_mmath(g, "ADD", uvsep.outputs["X"],
                       _mmath(g, "MULTIPLY", time, 0.5, (-1120, -100)), (-940, -80)),
                uvcoord.inputs["X"])
    g.links.new(_mmath(g, "MULTIPLY", uvsep.outputs["Y"], 6.0, (-1120, -240)), uvcoord.inputs["Y"])

    def _uvnoise(scale, loc):
        n = g.nodes.new("ShaderNodeTexNoise")
        n.noise_dimensions = "2D"
        n.location = loc
        g.links.new(uvcoord.outputs["Vector"], n.inputs["Vector"])
        n.inputs["Scale"].default_value = scale
        n.inputs["Detail"].default_value = 2.0
        return n.outputs["Fac"]

    uv_h = _mmath(g, "ADD", _mmath(g, "MULTIPLY", _uvnoise(0.5, (-760, -100)), 0.6, (-580, -100)),
                  _mmath(g, "MULTIPLY", _uvnoise(1.5, (-760, -280)), 0.4, (-580, -280)), (-400, -160))
    uv_str = _mmath(g, "MULTIPLY", I["Surface Texture"], liquid, (-220, -160))
    n_fine = _wave_bump(uv_h, uv_str, n_fine, (560, 240))

    # Cracked-ice normal: a Voronoi distance-to-edge bump that fades in with `frozen` and chains on
    # top of the (now flat) wave normal. Strength 0 when liquid, so it is a pass-through.
    voro = g.nodes.new("ShaderNodeTexVoronoi")
    voro.voronoi_dimensions = "3D"
    voro.feature = "DISTANCE_TO_EDGE"
    voro.location = (320, 40)
    voro.inputs["Scale"].default_value = 0.6  # world-scale cells, ~1.5 m ice plates
    g.links.new(pos, voro.inputs["Vector"])
    ice_str = _mmath(g, "MULTIPLY", frozen, 0.35, (500, 40))
    normal = _wave_bump(voro.outputs["Distance"], ice_str, n_fine, (680, 200))

    # Depth colour: shallow near the shoreline, deep in the body. On a W5 ribbon the driver is the
    # real Beer-Lambert depth_fac (metres of column); the old shore proxy (1-shore)*Depth is kept as a
    # floor so a pre-W5 ribbon (bbt_depth 0 everywhere) still reads with the shore gradient it had.
    shore_deep = g.nodes.new("ShaderNodeMath")
    shore_deep.operation = "MULTIPLY"
    shore_deep.use_clamp = True
    shore_deep.location = (-1000, -260)
    g.links.new(_mmath(g, "SUBTRACT", 1.0, shore_a.outputs["Fac"], (-1180, -260)), shore_deep.inputs[0])
    g.links.new(I["Depth"], shore_deep.inputs[1])
    deepness = _mmath(g, "MAXIMUM", depth_fac, shore_deep.outputs["Value"], (-820, -260))
    col = _mixcol(g, deepness, I["Shallow Color"], I["Deep Color"], (-620, -300))

    # Foam (W3). Base = max(bbt_foam*Foam Amount, shallow-shore foam). A flow-scrolled noise breaks
    # it up, then a Map Range thresholds it to crisp lines whose width narrows with Foam Crispness.
    foam_wash = _mmath(g, "MULTIPLY", foam_a.outputs["Fac"], I["Foam Amount"], (-1000, -480))
    shore_shallow = _mrange(g, shore_a.outputs["Fac"], 0.6, 1.0, 0.0, 1.0, (-1000, -640))
    shore_foam = _mmath(g, "MULTIPLY", shore_shallow, I["Shore Foam"], (-820, -640))
    foam_base = _mmath(g, "MAXIMUM", foam_wash, shore_foam, (-640, -540))
    foam_scroll = _vscale(g, flow.outputs["Vector"], _mmath(g, "MULTIPLY", time, 0.6, (-1000, -800)),
                          (-820, -800))
    foam_n = _noise(_vsub(pos, foam_scroll, (-640, -800)),
                    _mmath(g, "MULTIPLY", I["Ripple Scale"], 1.5, (-820, -940)),
                    _mmath(g, "MULTIPLY", time, 0.4, (-820, -1060)), 2.0, (-460, -800))
    # foam_base * (0.5 + noise): streaks where the noise is high, gaps where low.
    foam_mod = _mmath(g, "MULTIPLY", foam_base,
                      _mmath(g, "ADD", 0.5, foam_n, (-280, -800)), (-100, -560))
    half_w = _mmath(g, "MULTIPLY", _mmath(g, "SUBTRACT", 1.0, I["Foam Crispness"], (-280, -940)),
                    0.5, (-100, -940))
    foam_lo = _mmath(g, "SUBTRACT", 0.5, half_w, (80, -900))
    foam_hi = _mmath(g, "ADD", 0.55, half_w, (80, -1020))
    foam = _mmath(g, "MULTIPLY",
                  _mrange(g, foam_mod, foam_lo, foam_hi, 0.0, 1.0, (260, -740)), liquid, (440, -740))
    col = _mixcol(g, foam, col, I["Foam Color"], (620, -560))
    # Base roughness: near-mirror water, rougher where foam churns, frosted where frozen.
    rough = _lerp(g, I["Water Roughness"], 0.6, foam, (620, -820))
    rough = _lerp(g, rough, _ICE_ROUGHNESS, frozen, (620, -980))
    # Ice tint: icy blue-white as the water freezes, driven by the FULL frozen term (env cold OR
    # manual Frozen), not the manual input alone. The old code tinted only I["Frozen"] and delegated
    # the env-cold tint to the S_Weather frost term -- but frost is gated by clear sky and calm air,
    # so an overcast or windy freeze dropped the tint entirely and the river rendered glassy, opaque,
    # and dark (physically wrong for thick ice). To avoid doubling with the frost tint where frost
    # DOES fire, the ice tint is complemented by the same frost gate (frost point * clear * calm):
    # where frost is active (cold + clear + calm) it backs off and the frost term tints; where frost
    # is suppressed (overcast / windy) the ice tint carries the full look. So exactly one icy tint
    # applies on any surface, never zero and never both. Gate math mirrors weather_group's frost gate.
    frost_pt = _mrange(g, env.outputs["Temperature"], 1.0, -5.0, 0.0, 1.0, (440, -300))
    clear = _mmath(g, "SUBTRACT", 1.0, env.outputs["Cloud"], (440, -420))
    calm = _mrange(g, env.outputs["Wind"], 4.0, 0.5, 0.0, 1.0, (440, -540))
    # Match the weather frost cond exactly (frost_pt * clear * calm * Frost Strength * env.frost) so
    # the complement is exact: else zeroing Frost Strength OR the env frost dial would suppress the
    # frost tint AND back off the ice tint, leaving a cold river untinted.
    fs_frost = _mmath(g, "MULTIPLY", I["Frost Strength"], env.outputs["Frost"], (620, -520))
    frost_active = _mmath(g, "MULTIPLY",
                          _mmath(g, "MULTIPLY", frost_pt, clear, (620, -360)),
                          _mmath(g, "MULTIPLY", calm, fs_frost, (620, -480)), (780, -400))
    ice_tint = _mmath(g, "MULTIPLY", _mmath(g, "MULTIPLY", frozen,
                      _mmath(g, "SUBTRACT", 1.0, frost_active, (620, -600)), (800, -540)),
                      0.8, (800, -660))
    col = _mixcol(g, ice_tint, col, _WATER_ICE, (960, -420))

    # Weather layer: inherit wetness/frost/snow. Its below-freezing frost term whitens + roughens
    # the albedo on the env-cold path; metallic stays 0.
    weather = g.nodes.new("ShaderNodeGroup")
    weather.node_tree = weather_group()
    weather.location = (980, -560)
    g.links.new(col, weather.inputs["Base Color"])
    g.links.new(rough, weather.inputs["Roughness"])
    weather.inputs["Metallic"].default_value = 0.0
    for name in ("Snow Strength", "Slope Threshold", "Slope Falloff",
                 *[n for n, _ in _WEATHER_EXTRA]):
        g.links.new(I[name], weather.inputs[name])

    # Transmission collapses to opaque as it freezes; IOR passes through. Depth Opacity fades the
    # transmission out with depth (deep water -> less see-through, so the bed hides under a river);
    # frozen ice is fully opaque regardless.
    depth_op = _mmath(g, "SUBTRACT", 1.0,
                      _mmath(g, "MULTIPLY", I["Depth Opacity"], depth_fac, (800, -1120)), (980, -1120))
    trans = _mmath(g, "MULTIPLY", _mmath(g, "MULTIPLY", I["Transmission"], depth_op, (1160, -1120)),
                   liquid, (1340, -1120))
    # Alpha: a soft shoreline (W5) fades the surface to transparent over the outer Shoreline Fade
    # fraction of the half-width (shore 1 = bank), so the waterline dissolves into the bank instead of
    # cutting a hard line. Keyed to bbt_shore so a pre-W5 ribbon stays visible. The old lateral Edge
    # Fade still composes. Frozen ice is fully opaque.
    shore_fade = _mrange(g, shore_a.outputs["Fac"],
                         _mmath(g, "SUBTRACT", 1.0, I["Shoreline Fade"], (800, -1300)), 1.0,
                         1.0, 0.0, (980, -1300))
    edge = _mmath(g, "SUBTRACT", 1.0,
                  _mmath(g, "MULTIPLY", shore_a.outputs["Fac"], I["Edge Fade"], (980, -1440)),
                  (1160, -1440))
    alpha = _mmath(g, "MULTIPLY", edge, shore_fade, (1340, -1360))
    alpha = _lerp(g, alpha, 1.0, frozen, (1520, -1300))

    g.links.new(weather.outputs["Base Color"], O["Base Color"])
    g.links.new(weather.outputs["Roughness"], O["Roughness"])
    g.links.new(weather.outputs["Metallic"], O["Metallic"])
    g.links.new(trans, O["Transmission"])
    g.links.new(I["IOR"], O["IOR"])
    g.links.new(alpha, O["Alpha"])
    g.links.new(normal, O["Normal"])
    return g



def water_material(mat_name):
    """A water-surface wrapper (S_WaterMaster) for a river/stream ribbon (BobSplines C5.3). The
    widened _build_wrapper drives the master's Transmission / IOR / Alpha / Normal into the
    Principled BSDF on top of the usual Base Color / Roughness / Metallic. get-or-create, so a
    re-Build keeps tuned inputs. The material's EEVEE refraction/transparency flags are set here so
    the Transmission actually refracts (W1); the scene-level ray tracing toggle is the addon's job."""
    mat = _build_wrapper(mat_name, water_master_group(), "water", None)
    set_water_render_flags(mat)
    return mat
