"""curve_water: a flat water-surface ribbon along a river curve, in harmony with the carved bed.

The water channel of BobSplines (docs/SPLINES.md 4.6, C5.2), following the spline-river model every
established tool uses (UE5 Water, Torque3D, EasyRoads, Waterways): the CURVE drives the water
surface, and the terrain is carved to it -- the water is NOT projected onto the terrain.

Vertical harmony is by construction. Both this ribbon and the terrain overlay derive their heights
from the SAME shared solve, curve_field's `path_z` (the draped, monotonically descending centreline):
- the overlay (impose mode) carves the bed to `path_z - Path Depth`;
- this ribbon sits the water surface at `path_z - Water Depth`, with Water Depth < Path Depth.
So the surface is always Water Depth below the channel rim and (Path Depth - Water Depth) above the
bed, with the banks standing above it -- it can never float or drift out of harmony with the bed,
and it needs no fragile read of the carved terrain geometry.

Shape: Curve to Mesh sweeps a flat line (the channel width) along the curve for the ribbon's XY
route (Z-up normal so it stays horizontal across the width); each vertex's Z is then set to
`path_z - Water Depth`. path_z is ~constant across the narrow width, so the surface is flat.

Live shading fields, all from curve_field so they cost nothing extra: bbt_shore (0 mid-channel, 1 at
the banks, from the distance to the centreline) drives the shallow->deep depth colour and the bank
foam; bbt_flow (the unit DOWNHILL tangent scaled by a speed that rises on rapids and falls toward the
banks) scrolls the water shader's ripple normal downstream with no bake; bbt_foam is high at the
banks and on steep (white-water) sections.

Params: curve (object name), width (channel width, seeded from the role). Live knobs: Width, Water
Depth, Flow Base, Foam Bank, Foam Rapids.
"""

import math

import bpy

from ..blocks import curve_field, math_node, object_geometry, position
from ..scaffold import add_input
from . import recipe

_PROFILE_COUNT = 17  # cross-width points; enough for a smooth shore->centre depth-colour gradient
#                      and for the angled Gerstner cross-waves to read (a coarse profile facets them)
_RAPIDS_SPEED = 2.5  # how much a steep (descending) section speeds the flow up
_SHORE_SLOW = 0.35   # relative flow speed at the banks (mid-channel is 1.0)

# Gerstner wave components: (angle offset from the downstream direction [rad], wavelength scale,
# amplitude scale). All run nearly downstream (small angles): a river's surface travels downstream,
# and -- crucially -- a wide cross-angle gives the wave a short ACROSS-width period that the ribbon's
# ~1 m width spacing cannot resolve, so it facets into mechanical chevrons. Small angles keep the
# across-width period large (smooth) while the slight spread + decaying length/height still break up
# the regular sine into natural chop. Fine cross-ripple detail comes from the shader normal instead.
_GERSTNER = (
    (0.0, 1.00, 1.00),
    (0.16, 0.64, 0.48),
    (-0.12, 0.40, 0.30),
)
_GRAVITY = 9.8  # deep-water dispersion omega = speed * sqrt(g*k): longer waves travel faster


def _store(ng, geo, name, value, data_type, location):
    """Store value into a named POINT attribute on geo. Return the new geometry socket."""
    store = ng.nodes.new("GeometryNodeStoreNamedAttribute")
    store.data_type = data_type
    store.domain = "POINT"
    store.location = location
    ng.links.new(geo, store.inputs["Geometry"])
    store.inputs["Name"].default_value = name
    ng.links.new(value, store.inputs["Value"])
    return store.outputs["Geometry"]


def _combine(ng, x, y, z, location):
    n = ng.nodes.new("ShaderNodeCombineXYZ")
    n.location = location
    for sock, val in (("X", x), ("Y", y), ("Z", z)):
        if isinstance(val, (int, float)):
            n.inputs[sock].default_value = val
        else:
            ng.links.new(val, n.inputs[sock])
    return n.outputs["Vector"]


def _separate(ng, vec, location):
    n = ng.nodes.new("ShaderNodeSeparateXYZ")
    n.location = location
    ng.links.new(vec, n.inputs[0])
    return n


def _vec(ng, op, a, b, location):
    n = ng.nodes.new("ShaderNodeVectorMath")
    n.operation = op
    n.location = location
    ng.links.new(a, n.inputs[0])
    if b is not None:
        ng.links.new(b, n.inputs[1])
    return n


@recipe("curve_water")
def build(ng, out, params: dict):
    nodes, links = ng.nodes, ng.links
    curve = bpy.data.objects.get(params.get("curve", ""))

    add_input(ng, "Width", "NodeSocketFloat", float(params.get("width", 3.0)), 0.01)
    # Water Depth: metres the surface sits BELOW the channel rim (the draped centreline path_z, the
    # same reference the overlay carves the bed from). Keep it below the overlay's Path Depth so the
    # surface stays above the bed; bigger = a deeper channel showing above the water.
    add_input(ng, "Water Depth", "NodeSocketFloat", float(params.get("water_depth", 0.4)), 0.0)
    add_input(ng, "Flow Base", "NodeSocketFloat", float(params.get("flow_base", 1.0)), 0.0)
    add_input(ng, "Foam Bank", "NodeSocketFloat", float(params.get("foam_bank", 0.5)), 0.0, 1.0)
    add_input(ng, "Foam Rapids", "NodeSocketFloat", float(params.get("foam_rapids", 1.0)), 0.0, 1.0)
    # End Taper: clip the ribbon over the last N metres at each end, matching the overlay's carve
    # taper, so the water does not jut out past where the channel is cut (which read as floating).
    add_input(ng, "End Taper", "NodeSocketFloat", float(params.get("end_taper", 0.0)), 0.0)
    # Gerstner waves: real crest/trough vertex displacement (not just a normal), animated by Scene
    # Time so it moves on playback with no bake. Amplitude flattens toward the banks; 0 = flat water.
    add_input(ng, "Wave Amplitude", "NodeSocketFloat", float(params.get("wave_amp", 0.08)), 0.0)
    add_input(ng, "Wave Length", "NodeSocketFloat", float(params.get("wave_len", 4.5)), 0.05)
    add_input(ng, "Wave Steepness", "NodeSocketFloat", float(params.get("wave_steep", 0.4)), 0.0, 1.0)
    add_input(ng, "Wave Speed", "NodeSocketFloat", float(params.get("wave_speed", 0.6)), 0.0)
    # Wave Chop domain-warps the wave phase by a large-scale noise so the crossing sine trains do not
    # lock into a rigid regular lattice (mechanical chevrons); the crests meander like real chop.
    add_input(ng, "Wave Chop", "NodeSocketFloat", float(params.get("wave_chop", 0.7)), 0.0, 1.0)

    gi = nodes.new("NodeGroupInput")
    gi.location = (-1500, 0)

    # Ribbon XY route: a flat line the channel wide, swept along the curve. Force the curve normal to
    # world-Z so the profile stays HORIZONTAL across the width (no roll to a near-vertical wall on a
    # descending curve). Guarded on both counts: a 5.2 mode-socket makes the property set a no-op,
    # and an unavailable node is skipped (the swept Z is overwritten below anyway).
    curve_geo = object_geometry(ng, curve, (-1300, 300))
    try:
        setnorm = nodes.new("GeometryNodeSetCurveNormal")
        setnorm.location = (-1120, 300)
        try:
            setnorm.mode = "Z_UP"
        except (AttributeError, TypeError):
            pass
        links.new(curve_geo, setnorm.inputs["Curve"])
        curve_geo = setnorm.outputs["Curve"]
    except RuntimeError:
        pass

    half = math_node(ng, "MAXIMUM", math_node(ng, "MULTIPLY", gi.outputs["Width"], 0.5, (-1300, -260)),
                     0.01, (-1120, -260))
    neg_half = math_node(ng, "MULTIPLY", half, -1.0, (-1120, -380))
    line = nodes.new("GeometryNodeCurvePrimitiveLine")
    line.location = (-940, -300)
    links.new(_combine(ng, neg_half, 0.0, 0.0, (-940, -440)), line.inputs["Start"])
    links.new(_combine(ng, half, 0.0, 0.0, (-940, -560)), line.inputs["End"])
    resample = nodes.new("GeometryNodeResampleCurve")
    resample.location = (-760, -300)
    try:  # 5.2 exposes the resample mode as a menu SOCKET, not a property; COUNT is the default
        resample.mode = "COUNT"
    except (AttributeError, TypeError):
        pass
    links.new(line.outputs["Curve"], resample.inputs["Curve"])
    resample.inputs["Count"].default_value = _PROFILE_COUNT
    c2m = nodes.new("GeometryNodeCurveToMesh")
    c2m.location = (-560, 40)
    links.new(curve_geo, c2m.inputs["Curve"])
    links.new(resample.outputs["Curve"], c2m.inputs["Profile Curve"])
    ribbon = c2m.outputs["Mesh"]

    # The shared curve solve, evaluated at each ribbon vertex. path_z is the reference the overlay
    # carves to, so setting the surface below it keeps water and bed locked together. dist gives the
    # shore gradient; tangent gives the flow direction and the descent (rapids).
    dist, _near, path_z, end_dist, _side, tangent = curve_field(ng, curve, (-1100, -1000))

    # Surface Z = path_z - Water Depth, keeping the swept XY. path_z is ~constant across the narrow
    # width (same nearest centreline), so the surface comes out flat across the ribbon.
    water_z = math_node(ng, "SUBTRACT", path_z, gi.outputs["Water Depth"], (-360, -160))
    rsep = _separate(ng, position(ng, (-360, -320)), (-180, -320))
    setpos = nodes.new("GeometryNodeSetPosition")
    setpos.location = (60, 40)
    links.new(ribbon, setpos.inputs["Geometry"])
    links.new(_combine(ng, rsep.outputs["X"], rsep.outputs["Y"], water_z, (-180, -120)),
              setpos.inputs["Position"])
    geo = setpos.outputs["Geometry"]

    # Shore: 0 at the centreline, 1 at the banks (distance / half-width, clamped).
    shore = math_node(ng, "MINIMUM", math_node(ng, "DIVIDE", dist, half, (240, -320)), 1.0,
                      (420, -320))

    # Flow: the unit tangent flattened to XY, flipped to point DOWNHILL (the spline may run either
    # way; tz < 0 already descends, so sign = -sign(tz)), scaled by a relative speed.
    tsep = _separate(ng, tangent, (240, -520))
    tan_xy = _combine(ng, tsep.outputs["X"], tsep.outputs["Y"], 0.0, (420, -520))
    tan_dir = _vec(ng, "NORMALIZE", tan_xy, None, (600, -520)).outputs["Vector"]
    uphill = math_node(ng, "GREATER_THAN", tsep.outputs["Z"], 0.0, (420, -680))
    sign = math_node(ng, "SUBTRACT", 1.0, math_node(ng, "MULTIPLY", uphill, 2.0, (600, -680)),
                     (780, -680))
    # Unit XY direction pointing DOWNSTREAM (tan_dir * sign); reused for the Gerstner wave heading.
    down_dir = _vec(ng, "SCALE", tan_dir, None, (960, -180))
    links.new(sign, down_dir.inputs["Scale"])
    down_dir = down_dir.outputs["Vector"]
    steep = math_node(ng, "ABSOLUTE", tsep.outputs["Z"], None, (420, -820))
    rapids = math_node(ng, "ADD", 1.0, math_node(ng, "MULTIPLY", steep, _RAPIDS_SPEED, (600, -820)),
                       (780, -820))
    midfast = math_node(ng, "SUBTRACT", 1.0, shore, (600, -320))          # 1 mid-channel, 0 at banks
    shorefac = math_node(ng, "ADD", _SHORE_SLOW,
                         math_node(ng, "MULTIPLY", midfast, 1.0 - _SHORE_SLOW, (780, -320)),
                         (960, -320))
    speed = math_node(ng, "MULTIPLY",
                      math_node(ng, "MULTIPLY", gi.outputs["Flow Base"], rapids, (960, -700)),
                      shorefac, (1140, -520))
    signed = math_node(ng, "MULTIPLY", speed, sign, (1320, -600))
    flow = _vec(ng, "SCALE", tan_dir, None, (1140, -180))
    links.new(signed, flow.inputs["Scale"])
    geo = _store(ng, geo, "bbt_flow", flow.outputs["Vector"], "FLOAT_VECTOR", (1340, 40))

    # Foam: white near the banks (shore high) and on steep white-water sections, clamped 0..1.
    bank_ramp = nodes.new("ShaderNodeMapRange")
    bank_ramp.interpolation_type = "SMOOTHSTEP"
    bank_ramp.location = (600, -960)
    links.new(shore, bank_ramp.inputs["Value"])
    bank_ramp.inputs["From Min"].default_value = 0.55
    bank_ramp.inputs["From Max"].default_value = 1.0
    bank_foam = math_node(ng, "MULTIPLY", bank_ramp.outputs["Result"], gi.outputs["Foam Bank"],
                          (820, -960))
    rapid_foam = math_node(ng, "MULTIPLY", steep, gi.outputs["Foam Rapids"], (820, -1080))
    foam = nodes.new("ShaderNodeMath")
    foam.operation = "MAXIMUM"
    foam.use_clamp = True
    foam.location = (1020, -1000)
    links.new(bank_foam, foam.inputs[0])
    links.new(rapid_foam, foam.inputs[1])
    geo = _store(ng, geo, "bbt_foam", foam.outputs["Value"], "FLOAT", (1520, 40))

    # Shore for the depth-colour gradient the water shader reads. Stored BEFORE the wave
    # displacement below, so the shading fields are captured on the flat surface (the displacement
    # only moves geometry; it must not perturb the stored flow/foam/shore).
    geo = _store(ng, geo, "bbt_shore", shore, "FLOAT", (1700, 40))

    # ---- Gerstner wave displacement (animated, no bake) ----
    # Sum of _GERSTNER trochoidal waves travelling downstream (heading = down_dir rotated per
    # component). Each: vertical z = A*sin(phase), horizontal = dir*(Q*A)*cos(phase) where the
    # phase = k*dot(dir, P.xy) - omega*t, k = 2pi/wavelength, omega = Wave Speed*sqrt(g*k), and
    # Q*A = Wave Steepness/(k*N) caps the horizontal so crests peak without looping. Applied as a
    # SetPosition Offset, scaled by an envelope that flattens the waves toward the banks.
    scene_t = nodes.new("GeometryNodeInputSceneTime")
    scene_t.location = (240, -1320)
    t = scene_t.outputs["Seconds"]
    Pg = position(ng, (240, -1220))
    ddir = _separate(ng, down_dir, (240, -1440))
    dx, dy = ddir.outputs["X"], ddir.outputs["Y"]
    env = math_node(ng, "MAXIMUM", midfast, 0.0, (240, -1560))  # 1 mid-channel, 0 at the banks
    n_waves = float(len(_GERSTNER))

    # Domain warp: offset the position that feeds every component's phase by a large-scale noise, so
    # the crest lines wander instead of forming a rigid periodic lattice. Warp magnitude scales with
    # wavelength (and Wave Chop) so it bends crests by a fraction of their spacing.
    wnoise = nodes.new("ShaderNodeTexNoise")
    wnoise.location = (240, -1720)
    links.new(Pg, wnoise.inputs["Vector"])
    wnoise.inputs["Scale"].default_value = 0.07  # large features (~14 m) -> slow meander
    wcenter = nodes.new("ShaderNodeVectorMath")
    wcenter.operation = "SUBTRACT"
    wcenter.location = (440, -1720)
    links.new(wnoise.outputs["Color"], wcenter.inputs[0])
    wcenter.inputs[1].default_value = (0.5, 0.5, 0.0)  # centre to [-0.5,0.5]; no Z warp
    warp_mag = math_node(ng, "MULTIPLY", gi.outputs["Wave Length"],
                         math_node(ng, "MULTIPLY", gi.outputs["Wave Chop"], 1.4, (440, -1860)),
                         (620, -1860))
    warp = nodes.new("ShaderNodeVectorMath")
    warp.operation = "SCALE"
    warp.location = (620, -1720)
    links.new(wcenter.outputs["Vector"], warp.inputs[0])
    links.new(warp_mag, warp.inputs["Scale"])
    Pw = _vec(ng, "ADD", Pg, warp.outputs["Vector"], (820, -1740)).outputs["Vector"]

    def _gerstner(angle, lscale, ascale, y):
        ca, sa = math.cos(angle), math.sin(angle)
        rdx = math_node(ng, "SUBTRACT", math_node(ng, "MULTIPLY", dx, ca, (500, y)),
                        math_node(ng, "MULTIPLY", dy, sa, (500, y - 120)), (680, y))
        rdy = math_node(ng, "ADD", math_node(ng, "MULTIPLY", dx, sa, (500, y - 240)),
                        math_node(ng, "MULTIPLY", dy, ca, (500, y - 360)), (680, y - 240))
        rdir = _combine(ng, rdx, rdy, 0.0, (860, y - 100))
        length = math_node(ng, "MULTIPLY", gi.outputs["Wave Length"], lscale, (500, y - 500))
        k = math_node(ng, "DIVIDE", 2.0 * math.pi, length, (680, y - 500))
        dotv = _vec(ng, "DOT_PRODUCT", rdir, Pw, (1040, y - 100)).outputs["Value"]
        kx = math_node(ng, "MULTIPLY", k, dotv, (1220, y - 100))
        omega = math_node(ng, "MULTIPLY", gi.outputs["Wave Speed"],
                          math_node(ng, "SQRT", math_node(ng, "MULTIPLY", _GRAVITY, k, (680, y - 620)),
                                    None, (860, y - 620)), (1040, y - 620))
        phase = math_node(ng, "SUBTRACT", kx, math_node(ng, "MULTIPLY", omega, t, (1220, y - 620)),
                          (1400, y - 300))
        amp = math_node(ng, "MULTIPLY", gi.outputs["Wave Amplitude"], ascale, (1400, y - 480))
        zoff = math_node(ng, "MULTIPLY", amp, math_node(ng, "SINE", phase, None, (1580, y - 180)),
                         (1760, y - 220))
        qa = math_node(ng, "DIVIDE", gi.outputs["Wave Steepness"],
                       math_node(ng, "MULTIPLY", k, n_waves, (1400, y - 640)), (1580, y - 640))
        hmag = math_node(ng, "MULTIPLY", qa, math_node(ng, "COSINE", phase, None, (1580, y - 360)),
                         (1760, y - 400))
        horiz = _vec(ng, "SCALE", rdir, None, (1940, y - 300))
        links.new(hmag, horiz.inputs["Scale"])
        hs = _separate(ng, horiz.outputs["Vector"], (2120, y - 300))
        return _combine(ng, hs.outputs["X"], hs.outputs["Y"], zoff, (2300, y - 200))

    offs = [_gerstner(a, ls, as_, -1300 - i * 900) for i, (a, ls, as_) in enumerate(_GERSTNER)]
    total = offs[0]
    for i, o in enumerate(offs[1:]):
        total = _vec(ng, "ADD", total, o, (2500, -1600 - i * 200)).outputs["Vector"]
    wave_off = _vec(ng, "SCALE", total, None, (2700, -1600))
    links.new(env, wave_off.inputs["Scale"])
    disp = nodes.new("GeometryNodeSetPosition")
    disp.location = (2900, 40)
    links.new(geo, disp.inputs["Geometry"])
    links.new(wave_off.outputs["Vector"], disp.inputs["Offset"])
    geo = disp.outputs["Geometry"]

    # Smooth shading so the wave slopes read as curved water, not facets.
    smooth = nodes.new("GeometryNodeSetShadeSmooth")
    smooth.domain = "FACE"
    smooth.location = (3080, 40)
    links.new(geo, smooth.inputs["Mesh"])
    smooth.inputs["Shade Smooth"].default_value = True
    geo = smooth.outputs["Mesh"]

    # Clip the ribbon ends: delete verts within End Taper of a spline end, so the water stops where
    # the carve tapers out instead of jutting past the channel (End Taper 0 deletes nothing).
    delete = nodes.new("GeometryNodeDeleteGeometry")
    delete.domain = "POINT"
    delete.location = (3260, 40)
    links.new(geo, delete.inputs["Geometry"])
    links.new(math_node(ng, "LESS_THAN", end_dist, gi.outputs["End Taper"], (3080, -160)),
              delete.inputs["Selection"])
    geo = delete.outputs["Geometry"]

    links.new(geo, out.inputs["Geometry"])
