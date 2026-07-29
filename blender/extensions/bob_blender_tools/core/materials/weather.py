"""S_EnvState + S_Weather: the world-to-shader bridge and the shared weather layer
(snow, wetness, frost, dust/moss aging) every BobShader master ends in. Driven from
`bbt_env` through S_EnvState's Value nodes."""

import os

import bpy

from .shared import _cached_group, _gin, _gout, _lerp, _mixcol, _mmath, _mplug, _mrange, _vscale




# BobShaders surface masters. Unlike everything above (Firmament's volume and
# particulate materials, which shade one effect) these are the artist-facing surface
# framework: shared shader NODE GROUPS (S_<Effect>) that a thin per-object wrapper
# material (M_<Surface>) instances, the Blender-native equivalent of an Unreal master
# material plus instances. One graph to maintain, many looks. Cached by name like the
# volume materials, and get-or-create so a re-Build never wipes a wrapper's tuned inputs.
#
# The stack, top to bottom:
#   M_<Surface>       one S_SurfaceMaster group node -> one Principled BSDF -> Output
#   S_SurfaceMaster   solid base colour + per-instance variation, ending in S_Weather
#   S_Weather         the shared weather layer (the snow term, plus the later dust/moss/frost terms)
#   S_EnvState        the world-to-shader bridge: internal Value nodes driven once from
#                     scene.bbt_env and shared by every material that instances the group
ENV_STATE = "S_EnvState"

WEATHER = "S_Weather"

# The leaf-card season layer (BobFoliage, docs/FOLIAGE.md). Its own group rather than a new
# output on S_EnvState, and that is a decision rather than a convenience: S_EnvState is EMBEDDED by
# S_Weather and by S_WaterMaster, a rebuild gives its sockets fresh identifiers, and every embedder
# left un-rebuilt keeps stale links to them -- which is why the item-3 and snow-line changes each
# cost a global S_GROUP_VER bump and a revert-to-default on every tuned terrain in the file. A
# season term that only leaves shade on the cards is not worth that, so it lives here with its own
# driven Value node and its own version, and the masters are untouched.
LEAF_SEASON = "S_LeafSeason"

# The Value node in S_LeafSeason the panel drives, in the shape ENV_STATE_DRIVERS uses. The field
# is an ENUM, and a driver reads an enum as its INDEX -- measured on 5.2: bbt_env.season = "autumn"
# drives 2.0, matching env.SEASONS order. The default is 1.0, summer, so a standalone .blend with
# no Firmament renders a summer leaf rather than a bare index of 0 (spring).
LEAF_SEASON_DRIVERS = (("env_season", "season", 1.0),)

# env.SEASONS indices the leaf layer keys off (must match env.py SEASONS order).
_SEASON_AUTUMN, _SEASON_WINTER = 2, 3

# The hue an autumn leaf turns to. Scaled by the leaf's own luminance rather than mixed flat over
# it (see `leaf_season_group`), so this is a full-brightness amber and the crown's light and shade
# come from the atlas.
_AUTUMN_COLOR = (0.85, 0.36, 0.07, 1.0)

# What a leaf still on the tree in winter looks like: the same turn, further along and drier. It is
# a FRACTION of the autumn turn rather than a colour of its own, because a leaf that is still there
# in winter is a dead autumn leaf. Bare branches are geometry and belong to the LOD/variant work.
_WINTER_TURN = 0.55


# The S_EnvState internal Value nodes the panel drives from bbt_env: (node name, field,
# default). One driver per node on the single shared group feeds every material (the
# Phase-0 finding). The panel installs and reinstalls these; per-material drivers on the
# same fields remain the known-good fallback if the shared-drive path ever regresses.
ENV_STATE_DRIVERS = (
    ("env_wetness", "wetness", 0.0),
    ("env_temperature", "temperature", 15.0),
    ("env_weather", "weather", 0.0),  # enum index; mapped to effective wetness in S_EnvState
    ("env_snow_line", "snow_line", 0.7),  # normalized 0..1; kept in sync with env.SNOW_LINE_DEFAULT
    ("env_snow_z_base", "snow_z_base", 0.0),  # terrain valley world-Z (stamped on build/season)
    ("env_snow_z_span", "snow_z_span", 20.0),  # terrain relief metres (peak - valley)
    ("env_cloud", "cloud_cover", 0.2),  # for frost: clear sky (low cloud) drives radiative cooling
    ("env_wind", "wind_strength", 1.0),  # for frost: hoar frost needs calm air (wind gives rime)
    ("env_frost", "frost", 0.6),  # artist dial: overall frost amount (physics still gates it)
)

# Below this temperature (C) snow is at full thickness; it ramps in from 0C. Snow has no amount
# slider -- temperature is the amount, snow_line is the extent.
SNOW_TEMP_FULL = -4.0


# Snow shading constants (the surface-snow look): a slightly cool near-white albedo and a
# soft, high roughness. Kept here so the shader and any future accumulation-shell tint agree.
_SNOW_ALBEDO = (0.90, 0.93, 0.97, 1.0)

_SNOW_ROUGHNESS = 0.6

# Weather-term tints: warm dust, dark moss, cool frost.
_DUST_COLOR = (0.55, 0.47, 0.33, 1.0)

_MOSS_COLOR = (0.12, 0.22, 0.06, 1.0)

# Hoar frost: a pale, faintly cool crystalline tint. Applied THIN (low opacity) with fine sparkle,
# so it reads as a delicate sheen on cold exposed rock, distinct from snow's thick opaque white.
_FROST_COLOR = (0.82, 0.87, 0.96, 1.0)

_FROST_MAX_OPACITY = 0.28   # the cool sheen never fully hides the rock (frost is thin, not a blanket)

_FROST_SPARKLE_SCALE = 18.0  # crystal glints (world-space noise frequency; fine but not aliasing)

_FROST_PATCH_SCALE = 0.25    # large-scale world-space noise that breaks frost into patches (low freq)

_FROST_PATCH_FLOOR = 0.15    # patch mask never fully zeroes, so a frosted region reads varied, not holed

# The extra S_Weather inputs the masters must expose and pass through (name, default).
_WEATHER_EXTRA = (
    ("Wetness Strength", 1.0), ("Wet Pooling", 0.0), ("Frost Strength", 1.0),
    ("Dust Amount", 0.0), ("Moss Amount", 0.0),
)

# env.weather enum indices (must match env.py WEATHER order): rain and storm wet the ground.
_WEATHER_RAIN, _WEATHER_STORM = 3, 4



def env_state_group():
    """The world-to-shader bridge: one shared group whose internal Value nodes hold the
    live env fields, driven once from scene.bbt_env by the panel. Because a node group is a single
    datablock shared by every material that instances it, driving it once feeds every surface
    (Phase-0). No inputs; outputs Snow, Wetness, Temperature, Snow Line, Snow Line Top, Cloud, Wind.
    When Firmament is absent no driver is installed and the Value defaults stand (no snow, a high
    snow line), so a material still renders standalone.

    Snow model (one authority, shared by terrain and assets) -- two controls, no amount slider:
    - Temperature is the amount: 0 above freezing, ramping to full thickness by SNOW_TEMP_FULL.
      So it snows when it is cold, colder = thicker, and nothing snows above freezing.
    - env.snow_line is the extent, normalized 0..1: 0 = the line at the valley floor (snow
      reaches the whole map), 1 = above the peaks (snow clears). Independent of temperature.
    - The normalized line becomes world Z here, base + snow_line * span, from the terrain's Z
      bounds (env.snow_z_base / snow_z_span, stamped on Apply Season / build; sane defaults for a
      standalone asset), so the same 0..1 reads right on a 90 m or a 1000 m terrain. The
      transition band is a fraction of span, so it scales too."""
    g, _fresh = _cached_group(ENV_STATE)
    if not _fresh:
        return g
    _gout(g, "Snow", "NodeSocketFloat")
    _gout(g, "Wetness", "NodeSocketFloat")
    _gout(g, "Temperature", "NodeSocketFloat")
    _gout(g, "Snow Line", "NodeSocketFloat")
    _gout(g, "Snow Line Top", "NodeSocketFloat")
    _gout(g, "Cloud", "NodeSocketFloat")  # frost: clear sky (low cloud) = radiative cooling
    _gout(g, "Wind", "NodeSocketFloat")    # frost: calm air = hoar frost (wind gives rime)
    _gout(g, "Frost", "NodeSocketFloat")   # artist dial: overall frost amount (0 = none)
    go = g.nodes.new("NodeGroupOutput")
    go.location = (600, 0)
    O = go.inputs

    # The driven raw fields (one driver each on these Value nodes, installed by the panel).
    val = {}
    for i, (node_name, _field, default) in enumerate(ENV_STATE_DRIVERS):
        v = g.nodes.new("ShaderNodeValue")
        v.name = v.label = node_name
        v.location = (-500, -i * 160)
        v.outputs[0].default_value = default
        val[node_name] = v.outputs[0]

    g.links.new(val["env_temperature"], O["Temperature"])
    g.links.new(val["env_cloud"], O["Cloud"])
    g.links.new(val["env_wind"], O["Wind"])
    g.links.new(val["env_frost"], O["Frost"])

    # Snow amount = temperature: 0 above freezing, easing to 1 (full) by SNOW_TEMP_FULL. This is
    # the only thing that turns snow on, so a cold night snows and a warm one never does.
    snow_amt = _mrange(g, val["env_temperature"], 0.0, SNOW_TEMP_FULL, 0.0, 1.0, (-90, 40))
    g.links.new(snow_amt, O["Snow"])
    # Snow line -> world Z (extent only, independent of temperature). Band a fraction of span, so
    # the softness scales with the terrain. Map so line 0 covers the WHOLE terrain (transition sits
    # below the valley) and line 1 clears above the peaks: hi goes base..base+span+band as line
    # goes 0..1, lo = hi - band. At line 0, hi = base, so the valley floor is fully snowed (not the
    # ~90% you get if the band starts at the valley).
    band = _mmath(g, "MULTIPLY", val["env_snow_z_span"], 0.12, (250, 40))
    span_band = _mmath(g, "ADD", val["env_snow_z_span"], band, (250, 120))
    hi = _mmath(g, "ADD", val["env_snow_z_base"],
                _mmath(g, "MULTIPLY", val["env_snow_line"], span_band, (420, 160)), (560, 140))
    lo = _mmath(g, "SUBTRACT", hi, band, (560, 60))
    g.links.new(lo, O["Snow Line"])
    g.links.new(hi, O["Snow Line Top"])

    # The weather -> wetness mapping (the one convergence spot, documented in SHADERS.md):
    # effective wetness = max(env.wetness, weather contribution), where weather in {rain,
    # storm} raises it (rain 0.6, storm 1.0) and clear/cloud/fog do not wet the ground.
    w = val["env_weather"]
    ge = _mmath(g, "GREATER_THAN", w, _WEATHER_RAIN - 0.5, (-260, -420))
    le = _mmath(g, "LESS_THAN", w, _WEATHER_STORM + 0.5, (-260, -560))
    band = _mmath(g, "MULTIPLY", ge, le, (-90, -480))       # 1 for weather in {rain, storm}
    lvl = _mmath(g, "MULTIPLY", _mmath(g, "SUBTRACT", w, float(_WEATHER_RAIN), (-90, -620)),
                 0.4, (80, -620))                            # rain 0.0, storm 0.4
    lvl = _mmath(g, "ADD", 0.6, lvl, (250, -620))            # rain 0.6, storm 1.0
    wwet = _mmath(g, "MULTIPLY", band, lvl, (250, -480))
    eff = _mmath(g, "MAXIMUM", val["env_wetness"], wwet, (420, -360))
    g.links.new(eff, O["Wetness"])
    return g



def leaf_season_group():
    """The leaf-card season layer: a base colour in, the same colour turned for the season out.

    Sits between the surface master and the Principled on a leaf card only
    (`foliage_card_material`), which is the whole reason it is a group of its own -- see
    `LEAF_SEASON` for why a Season output on S_EnvState would have cost every tuned terrain in the
    file a revert-to-default.

    Two things it does that a straight colour mix would not:

    - **The turn is staggered per card.** `bbt_fol_phase` is the recipe's per-card random, and the
      turn is scaled by `1 - Stagger * phase`, so at Stagger 0 every leaf turns together and at 0.5
      they turn between half and fully. A canopy that turns as one flat colour is the tell of a
      season swap; a real one is mottled. The attribute is absent on anything that is not a
      BobFoliage card, where the node returns 0 and the leaf simply turns fully.
    - **Winter is autumn further along, not a colour of its own.** A leaf still on the tree in
      winter is a dead autumn leaf, so it reads as a fraction of the same turn (`_WINTER_TURN`).
      Dropping it entirely is geometry, and belongs with the variants.

    The season itself arrives on a driven Value node exactly as the rest of the env does, so Apply
    Season and the `set_env` op move a canopy with no rebuild and no per-material press.
    """
    g, _fresh = _cached_group(LEAF_SEASON)
    if not _fresh:
        return g
    _gin(g, "Base Color", "NodeSocketColor", (1.0, 1.0, 1.0, 1.0))
    _gin(g, "Autumn Tint", "NodeSocketColor", _AUTUMN_COLOR)
    _gin(g, "Autumn Amount", "NodeSocketFloat", 1.0, 0.0, 1.0)
    _gin(g, "Stagger", "NodeSocketFloat", 0.5, 0.0, 1.0)
    _gout(g, "Base Color", "NodeSocketColor")
    _gout(g, "Turn", "NodeSocketFloat")  # how far this leaf has turned, for anything downstream
    gi = g.nodes.new("NodeGroupInput")
    gi.location = (-800, 0)
    go = g.nodes.new("NodeGroupOutput")
    go.location = (700, 0)
    I, O = gi.outputs, go.inputs

    season = g.nodes.new("ShaderNodeValue")
    season.name = season.label = LEAF_SEASON_DRIVERS[0][0]
    season.location = (-800, -300)
    season.outputs[0].default_value = LEAF_SEASON_DRIVERS[0][2]
    s = season.outputs[0]

    # A band per season rather than an equality: a driven float is exact here, but a band is what
    # the weather-to-wetness mapping in S_EnvState already uses and it costs one node.
    autumn = _mmath(g, "MULTIPLY",
                    _mmath(g, "GREATER_THAN", s, _SEASON_AUTUMN - 0.5, (-600, -220)),
                    _mmath(g, "LESS_THAN", s, _SEASON_AUTUMN + 0.5, (-600, -360)), (-420, -280))
    winter = _mmath(g, "MULTIPLY", _mmath(g, "GREATER_THAN", s, _SEASON_WINTER - 0.5, (-600, -500)),
                    _WINTER_TURN, (-420, -500))
    turned = _mmath(g, "MAXIMUM", autumn, winter, (-240, -380))

    # The per-card stagger. Absent (anything that is not a foliage card) the node returns 0, so the
    # scale is 1 and the leaf turns fully -- the safe direction: a season that reaches nothing is
    # visible, a season that half-reaches everything is not.
    phase = g.nodes.new("ShaderNodeAttribute")
    phase.attribute_type = "GEOMETRY"
    phase.attribute_name = "bbt_fol_phase"
    phase.location = (-600, -700)
    keep = _mmath(g, "SUBTRACT", 1.0,
                  _mmath(g, "MULTIPLY", I["Stagger"], phase.outputs["Fac"], (-420, -700)),
                  (-240, -700))
    turn = g.nodes.new("ShaderNodeMath")
    turn.operation = "MULTIPLY"
    turn.use_clamp = True
    turn.location = (60, -500)
    g.links.new(_mmath(g, "MULTIPLY", turned, keep, (-60, -500)), turn.inputs[0])
    g.links.new(I["Autumn Amount"], turn.inputs[1])

    # Re-tint by LUMINANCE rather than mixing toward a flat colour. A straight mix to amber erases
    # the atlas: at full turn every needle in the crown is the one value, which is the "season swap"
    # look. Scaling the tint by the leaf's own brightness keeps the atlas's light and shade and
    # replaces only its hue, and it can BRIGHTEN, which a multiply filter cannot -- a green leaf has
    # little red in it, so multiplying a green atlas by an amber can only ever darken it to brown.
    lum = g.nodes.new("ShaderNodeVectorMath")
    lum.operation = "DOT_PRODUCT"
    lum.location = (-60, 260)
    g.links.new(I["Base Color"], lum.inputs[0])
    lum.inputs[1].default_value = (0.2126, 0.7152, 0.0722)  # Rec. 709 luma
    tinted = _vscale(g, I["Autumn Tint"],
                     _mmath(g, "MULTIPLY", lum.outputs["Value"], 2.0, (120, 260)), (300, 260))
    g.links.new(_mixcol(g, turn.outputs["Value"], I["Base Color"], tinted, (480, 100)),
                O["Base Color"])
    g.links.new(turn.outputs["Value"], O["Turn"])
    return g


def weather_group():
    """The shared weather layer, ending every master.

    Coverage has ONE authority now: the shader computes it, identically on every surface
    (terrain, scattered assets, plain meshes). There is no attribute switch and no
    dependence on the GN pass, so a missing pass can never leave a surface bare while its
    neighbours whiten (the old Use-Attribute zero-trap):
      slope_mask    = smoothstep(normalZ, from Slope Threshold - Slope Falloff to Slope
                      Threshold)   -- eases on the LOW side, snow holds on up-facing ground
      altitude_mask = smoothstep(worldZ, from Snow Line to Snow Line Top)   -- snow lies ABOVE
                      the line; both bounds are world-Z, computed by the env bridge from the
                      normalized env.snow_line and the terrain's Z bounds, so Season and
                      Conditions move it live and it reads right at any terrain scale.
      coverage      = Snow * max(slope_mask, canopy) * altitude_mask * (1 - occlusion)
    Snow (from the env bridge) is the temperature-driven amount, so a cold night snows here as
    snow and a warm one clears. Occlusion is an optional shelter term read from the snow_occlusion
    attribute (the GN pass writes it; absent returns 0, so no pass means full snow, never
    zero). The GN pass still writes snow_cover too, but only the accumulation shell (geometry)
    reads that; the shader no longer does. Frost is gated to bare (non-snowed) faces, so it
    never doubles the tint on snow.
    """
    g, _fresh = _cached_group(WEATHER)
    if not _fresh:
        return g
    _gin(g, "Base Color", "NodeSocketColor", (0.5, 0.5, 0.5, 1.0))
    _gin(g, "Roughness", "NodeSocketFloat", 0.5, 0.0, 1.0)
    _gin(g, "Metallic", "NodeSocketFloat", 0.0, 0.0, 1.0)
    _gin(g, "Snow Strength", "NodeSocketFloat", 1.0, 0.0, 1.0)
    _gin(g, "Slope Threshold", "NodeSocketFloat", 0.5, 0.0, 1.0)
    _gin(g, "Slope Falloff", "NodeSocketFloat", 0.2, 0.0, 1.0)
    # The snow line (world-Z lo/hi) comes entirely from the env bridge now, scaled to the terrain,
    # so there is no per-material altitude knob to drift out of sync between surfaces.
    # Canopy accumulation (item-3 fix): 0 leaves the pure slope/up-facing model (terrain, solid
    # surfaces); the asset-convert path raises it so snow/frost/dust also hold on the upper
    # bounding box of a near-vertical instance (a tree trunk + cone canopy caught almost none).
    _gin(g, "Canopy Snow", "NodeSocketFloat", 0.0, 0.0, 1.0)
    # Weather terms, each gated by a strength/amount (0 = off).
    _gin(g, "Wetness Strength", "NodeSocketFloat", 1.0, 0.0, 1.0)
    _gin(g, "Wet Pooling", "NodeSocketFloat", 0.0, 0.0, 1.0)
    # Terrain-driven wetness (a baked flow/wetness map, fed only by the terrain master; 0 for
    # surfaces and legacy terrains). Lets channels and low ground read damp independent of the
    # weather, in EEVEE too (the map is baked, unlike the Cycles-only Wet Pooling cavity term).
    _gin(g, "Wetness Map", "NodeSocketFloat", 0.0, 0.0, 1.0)
    _gin(g, "Terrain Wetness", "NodeSocketFloat", 0.0, 0.0, 1.0)
    _gin(g, "Frost Strength", "NodeSocketFloat", 1.0, 0.0, 1.0)
    _gin(g, "Dust Amount", "NodeSocketFloat", 0.0, 0.0, 1.0)
    _gin(g, "Moss Amount", "NodeSocketFloat", 0.0, 0.0, 1.0)
    _gout(g, "Base Color", "NodeSocketColor")
    _gout(g, "Roughness", "NodeSocketFloat")
    _gout(g, "Metallic", "NodeSocketFloat")

    gi = g.nodes.new("NodeGroupInput")
    gi.location = (-1200, 200)
    go = g.nodes.new("NodeGroupOutput")
    go.location = (2100, 0)
    I, O = gi.outputs, go.inputs

    env = g.nodes.new("ShaderNodeGroup")
    env.node_tree = env_state_group()
    env.location = (-1200, -520)
    snow_amount = env.outputs["Snow"]
    env_wet = env.outputs["Wetness"]
    env_temp = env.outputs["Temperature"]
    line_lo = env.outputs["Snow Line"]      # world-Z bottom of the snow line
    line_hi = env.outputs["Snow Line Top"]  # world-Z top of the transition band
    env_cloud = env.outputs["Cloud"]
    env_wind = env.outputs["Wind"]
    env_frost = env.outputs["Frost"]  # artist dial (0 = no frost)

    # Shader Geometry: world-space normal Z (slope) and position Z (altitude), the same
    # quantities the GN pass reads, so the fallback reproduces it.
    geo = g.nodes.new("ShaderNodeNewGeometry")
    geo.location = (-1200, -120)
    nsep = g.nodes.new("ShaderNodeSeparateXYZ")
    nsep.location = (-1000, -80)
    g.links.new(geo.outputs["Normal"], nsep.inputs[0])
    psep = g.nodes.new("ShaderNodeSeparateXYZ")
    psep.location = (-1000, -280)
    g.links.new(geo.outputs["Position"], psep.inputs[0])

    slope_lo = _mmath(g, "SUBTRACT", I["Slope Threshold"], I["Slope Falloff"], (-800, -60))
    slope_mask = _mrange(g, nsep.outputs["Z"], slope_lo, I["Slope Threshold"], 0.0, 1.0, (-620, -20))
    # Altitude mask: snow lies above the env snow line (world-Z lo..hi from the bridge), so it
    # tracks env.snow_line and the below-freezing drop with no per-material knob to drift.
    alt_mask = _mrange(g, psep.outputs["Z"], line_lo, line_hi, 0.0, 1.0, (-620, -280))

    # Canopy term: the Generated coordinate is the instance's own bounding box (0 at the base,
    # 1 at the top), per-instance and scale-free, so its upper band marks the canopy of any
    # scattered asset regardless of face orientation. Canopy Snow gates it (0 for terrain/solid
    # surfaces), so raising it lets the top of a tree accumulate where the slope mask would not.
    tc = g.nodes.new("ShaderNodeTexCoord")
    tc.location = (-1000, -480)
    gsep = g.nodes.new("ShaderNodeSeparateXYZ")
    gsep.location = (-800, -480)
    g.links.new(tc.outputs["Generated"], gsep.inputs[0])
    canopy_top = _mrange(g, gsep.outputs["Z"], 0.4, 0.9, 0.0, 1.0, (-620, -480))
    canopy = _mmath(g, "MULTIPLY", I["Canopy Snow"], canopy_top, (-440, -480))

    # Snow exposure holds where the surface faces up OR on the canopy; times the snow amount.
    snow_exp = _mmath(g, "MAXIMUM", slope_mask, canopy, (-420, -60))
    computed = _mmath(g, "MULTIPLY", snow_amount, snow_exp, (-420, -120))
    computed = _mmath(g, "MULTIPLY", computed, alt_mask, (-240, -120))

    # Optional shelter: the GN pass writes a snow_occlusion attribute (0..1). Absent, the
    # Attribute node returns 0, so a surface with no pass simply gets no shelter reduction
    # (full snow) -- never the old zero-coverage trap. coverage = computed * (1 - occlusion).
    occ = g.nodes.new("ShaderNodeAttribute")
    occ.attribute_type = "GEOMETRY"
    occ.attribute_name = "snow_occlusion"
    occ.location = (-420, -400)
    keep = _mmath(g, "SUBTRACT", 1.0, occ.outputs["Fac"], (-240, -400))
    coverage = _mmath(g, "MULTIPLY", computed, keep, (120, -300))

    snow_factor = g.nodes.new("ShaderNodeMath")
    snow_factor.operation = "MULTIPLY"
    snow_factor.use_clamp = True  # coverage * strength, clamped to 0..1
    snow_factor.location = (300, -220)
    g.links.new(coverage, snow_factor.inputs[0])
    g.links.new(I["Snow Strength"], snow_factor.inputs[1])
    sf = snow_factor.outputs["Value"]

    # Up/down-facing and cavity terms, from the shader Geometry: dust/frost hold on up-facing
    # faces, moss on shaded down-facing ones, and wetness pools in concave cavities.
    upface = _mrange(g, nsep.outputs["Z"], 0.2, 0.8, 0.0, 1.0, (300, 260))
    downface = _mmath(g, "SUBTRACT", 1.0, upface, (480, 300))
    cavity = _mrange(g, geo.outputs["Pointiness"], 0.5, 0.35, 0.0, 1.0, (300, 420))
    # Dust and frost accumulate up-facing OR on the canopy, so they read on a tree the same way
    # snow does; moss stays keyed to the true (down-facing) undersides.
    upface_eff = _mmath(g, "MAXIMUM", upface, canopy, (480, 240))

    # The weather stack, applied in order on (albedo, roughness, metallic):
    #   dust/moss (aging) -> wetness (darken + gloss) -> snow (whiten) -> frost (cool sheen).
    col, rough, metal = I["Base Color"], I["Roughness"], I["Metallic"]

    # Dust on up-facing, moss on down-facing (continuous amounts, set by season on Apply).
    dust = _mmath(g, "MULTIPLY", I["Dust Amount"], upface_eff, (660, 320))
    col = _mixcol(g, dust, col, _DUST_COLOR, (840, 360))
    moss = _mmath(g, "MULTIPLY", I["Moss Amount"], downface, (660, 200))
    col = _mixcol(g, moss, col, _MOSS_COLOR, (1000, 300))

    # Wetness: darken albedo and drop roughness for a wet sheen. The wet factor is the MAX of
    # three sources: uniform weather wetness, Cycles cavity pooling (Wet Pooling), and the baked
    # terrain map (Wetness Map * Terrain Wetness) so drainage channels read damp on their own.
    wet = _mmath(g, "MULTIPLY", env_wet, I["Wetness Strength"], (660, 60))
    pool = _mmath(g, "MULTIPLY", cavity, I["Wet Pooling"], (660, -60))
    terr = _mmath(g, "MULTIPLY", I["Wetness Map"], I["Terrain Wetness"], (660, -160))
    wp = _mmath(g, "MAXIMUM", wet, pool, (840, 0))
    wetf = g.nodes.new("ShaderNodeMath")
    wetf.operation = "MAXIMUM"
    wetf.use_clamp = True
    wetf.location = (1010, 0)
    g.links.new(wp, wetf.inputs[0])
    g.links.new(terr, wetf.inputs[1])
    wf = wetf.outputs["Value"]
    darkf = _mmath(g, "SUBTRACT", 1.0, _mmath(g, "MULTIPLY", wf, 0.45, (1000, -80)), (1180, -40))
    col = _vscale(g, col, darkf, (1180, 220))
    rough = _lerp(g, rough, 0.08, wf, (1180, -220))

    # Snow on top: whiten albedo, soften roughness, drop metallic by the coverage factor.
    col = _mixcol(g, sf, col, _SNOW_ALBEDO, (1360, 240))
    rough = _lerp(g, rough, _SNOW_ROUGHNESS, sf, (1360, -220))
    metal = _mmath(g, "MULTIPLY", metal, _mmath(g, "SUBTRACT", 1.0, sf, (1360, -360)), (1540, -320))

    # Hoar frost (physically modelled, distinct from snow): vapour deposits directly to ice on
    # clear, calm nights when a sky-facing surface radiates heat away and cools below the frost
    # point. So it is gated by real conditions, not just temperature:
    #   frost point : ramps in just below freezing (surfaces cool below air temp)
    #   clear sky   : (1 - Cloud) -- cloud traps outgoing radiation, so overcast = little frost
    #   calm air    : (1 - wind) -- wind brings granular rime instead of feathery hoar
    #   sky-exposed : up-facing OR the instance canopy (upface_eff) radiate to the open sky, so
    #                 frost forms on cold ground AND on the branches/canopy of a scattered tree,
    #                 not just flat up-facing faces; undersides barely frost
    #   bare        : (1 - snow) -- a snowed face shows snow, so frost never doubles the tint
    # The LOOK is a thin sparkly sheen (low-opacity cool tint + fine crystalline glints), never
    # snow's opaque blanket, so cold bare rock reads as frosted, not snow-covered.
    frost_pt = _mrange(g, env_temp, 1.0, -5.0, 0.0, 1.0, (1360, 520))   # just below freezing on
    clear = _mmath(g, "SUBTRACT", 1.0, env_cloud, (1360, 620))
    calm = _mrange(g, env_wind, 4.0, 0.5, 0.0, 1.0, (1360, 700))         # calm (low wind) -> 1
    bare = _mmath(g, "SUBTRACT", 1.0, sf, (1360, 400))
    cond = _mmath(g, "MULTIPLY", _mmath(g, "MULTIPLY", frost_pt, clear, (1540, 560)),
                  _mmath(g, "MULTIPLY", calm, I["Frost Strength"], (1540, 660)), (1720, 560))
    # Artist dial: env.frost scales the whole frost look so a cold, clear, calm scene can still be
    # snow WITHOUT a frost sheet (0 = none). The physics gate above is unchanged; this is amount.
    cond = _mmath(g, "MULTIPLY", cond, env_frost, (1720, 640))
    # Patchy break-up: a low-frequency world-space noise so frost forms in patches instead of a
    # uniform blanket (real hoar frost varies with surface moisture, sky-view, thermal mass).
    # Floored so a frosted region reads as varied density, not holes.
    ppatch = g.nodes.new("ShaderNodeTexNoise")
    ppatch.location = (1360, 300)
    ppatch.inputs["Detail"].default_value = 2.0
    _mplug(g, ppatch.inputs["Scale"], _FROST_PATCH_SCALE)
    g.links.new(geo.outputs["Position"], ppatch.inputs["Vector"])
    patch = _mrange(g, ppatch.outputs["Fac"], 0.35, 0.6, _FROST_PATCH_FLOOR, 1.0, (1540, 300))
    frost = _mmath(g, "MULTIPLY", cond, _mmath(g, "MULTIPLY", upface_eff, bare, (1540, 400)), (1720, 460))
    frost = _mmath(g, "MULTIPLY", frost, patch, (1900, 440))
    # Fine crystal sparkle: sparse world-space glints, so frost glitters rather than flatly whitens.
    fnoise = g.nodes.new("ShaderNodeTexNoise")
    fnoise.location = (1540, 800)
    fnoise.inputs["Detail"].default_value = 2.0
    _mplug(g, fnoise.inputs["Scale"], _FROST_SPARKLE_SCALE)
    g.links.new(geo.outputs["Position"], fnoise.inputs["Vector"])
    sparkle = _mmath(g, "MULTIPLY", _mrange(g, fnoise.outputs["Fac"], 0.62, 0.8, 0.0, 1.0, (1720, 800)),
                     frost, (1900, 760))
    # Thin cool sheen, then bright glints; the sheen is capped well below opaque (frost is not
    # snow).
    col = _mixcol(g, _mmath(g, "MULTIPLY", frost, _FROST_MAX_OPACITY, (1900, 640)), col, _FROST_COLOR,
                  (2080, 560))
    col = _mixcol(g, _mmath(g, "MULTIPLY", sparkle, 0.7, (2080, 760)), col, (1.0, 1.0, 1.0, 1.0),
                  (2260, 660))
    rough = _lerp(g, rough, 0.12, sparkle, (2260, -160))  # glints are shiny ice facets

    g.links.new(col, O["Base Color"])
    g.links.new(rough, O["Roughness"])
    g.links.new(metal, O["Metallic"])
    return g
