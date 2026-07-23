"""curve_overlay: carve a cross-section profile along a curve into the terrain (BobSplines C2).

The standalone terrain-shape overlay (docs/SPLINES.md 4.3), superseding the inline path grade
that used to live in heightmap_terrain. It runs as its OWN modifier ON the terrain object (after
the terrain modifier, so it sees the displaced surface), one modifier per curve, so a network of
paths stacks instead of being limited to a single inline path. It:

1. Levels a bench toward (live terrain Z at the centreline) - Path Depth across Path Width + a flat
   Shoulder Width, then grades back to the terrain over an embankment. The embankment width is
   slope-aware (R4): it widens with the cut/fill depth so a bench on a slope ramps out at Bank Slope
   instead of cliffing (docs/SPLINES.md 9 #11), and Bank Bias skews it to one side of the curve.
2. Writes the curve mask attributes the shader and scatter READ instead of re-solving proximity
   (docs/SPLINES.md 9 #2 / #4): bbt_curve_mask (0..1, 1 on the band), this curve's own edge ring
   under edge_attr (the shoulder ring, for a Verge scatter layer, R5), an optional per-role surface
   class attribute (R5), bbt_curve_carved (coverage of carving curves, for the junction take-lower
   rule, R6), and bbt_curve_dist (the XY distance). The masks MAX-accumulate across curves, so
   overlapping paths add rather than overwrite (a prior overlay's value is read and maxed).
3. Resolves crossings by TAKE-LOWER (R6): where a prior curve carved, this curve may only lower the
   surface, so a junction settles to the lower bench rather than the last-built curve winning.

Cross-section knobs live ONCE here, on the overlay modifier, snapshot-restored across a rebuild
like any GN knob (the single-owner decision): nothing downstream duplicates a width/depth knob.

This recipe adds a Geometry INPUT socket, so build_geonodes_on_object binds the terrain mesh in.
The bench height is sampled LIVE (R1): the overlay raycasts the incoming terrain straight down at
the centreline every evaluation, so the bench tracks the ground as the terrain is re-sculpted or the
curve is moved, with no re-Build. curve_field's draped path_z stays only as the fallback where that
ray misses the mesh (see _live_terrain_z). The Build-time drape still runs so the curve WIRE sits on
the terrain for editing.
"""

import bpy

from ..blocks import (curve_field, displace_z, math_node, mix_float, position, smooth_falloff,
                       width_multiplier)
from ..scaffold import add_input
from . import recipe

_LIFT = 10000.0  # raycast the centreline probe from well above any terrain, straight down


def _live_terrain_z(ng, geometry, near, fallback_z, location):
    """The CURRENT terrain height under the curve centreline (BobSplines R1, live re-drape).

    curve_field's path_z reads the curve's Build-time draped Z, which goes stale the moment the
    terrain is re-sculpted or the curve is moved sideways. Instead, raycast the incoming terrain
    geometry straight down at the nearest centreline point (near) and read the hit Z, so the bench
    always levels to the ground as it is NOW (the same lift + downward raycast scatter_along uses to
    sit instances on the surface). The overlay runs after the base terrain modifier, so this samples
    the real displaced surface; a stacked curve overlay drapes onto the already-carved ground.

    Where the ray misses (the centreline runs off the mesh edge) it falls back to the draped
    fallback_z, so a curve that overhangs the terrain still grades sanely.
    """
    nsep = ng.nodes.new("ShaderNodeSeparateXYZ")
    nsep.location = location
    ng.links.new(near, nsep.inputs[0])
    src = ng.nodes.new("ShaderNodeCombineXYZ")
    src.location = (location[0] + 180, location[1])
    ng.links.new(nsep.outputs["X"], src.inputs["X"])
    ng.links.new(nsep.outputs["Y"], src.inputs["Y"])
    src.inputs["Z"].default_value = _LIFT
    ray = ng.nodes.new("GeometryNodeRaycast")
    ray.location = (location[0] + 360, location[1])
    ng.links.new(geometry, ray.inputs["Target Geometry"])
    ng.links.new(src.outputs["Vector"], ray.inputs["Source Position"])
    ray.inputs["Ray Direction"].default_value = (0.0, 0.0, -1.0)
    ray.inputs["Ray Length"].default_value = _LIFT * 2.0
    hsep = ng.nodes.new("ShaderNodeSeparateXYZ")
    hsep.location = (location[0] + 540, location[1])
    ng.links.new(ray.outputs["Hit Position"], hsep.inputs[0])
    # mix toward the hit Z when the ray hit (Is Hit -> 1), else keep the draped fallback.
    return mix_float(ng, ray.outputs["Is Hit"], fallback_z, hsep.outputs["Z"],
                     (location[0] + 720, location[1]))


def _store_max(ng, geometry, name, value, location):
    """MAX-accumulate value into a named FLOAT POINT attribute (read the prior value, keep the
    stronger), so overlapping curves add rather than overwrite (docs/SPLINES.md 4.4). Returns the
    new geometry socket so the stores chain."""
    existing = ng.nodes.new("GeometryNodeInputNamedAttribute")
    existing.data_type = "FLOAT"
    existing.location = (location[0] - 200, location[1] - 320)
    existing.inputs["Name"].default_value = name
    m = math_node(ng, "MAXIMUM", existing.outputs["Attribute"], value, (location[0], location[1] - 320))
    store = ng.nodes.new("GeometryNodeStoreNamedAttribute")
    store.data_type = "FLOAT"
    store.domain = "POINT"
    store.location = location
    ng.links.new(geometry, store.inputs["Geometry"])
    store.inputs["Name"].default_value = name
    ng.links.new(m, store.inputs["Value"])
    return store.outputs["Geometry"]


@recipe("curve_overlay")
def build(ng, out, params: dict):
    nodes, links = ng.nodes, ng.links

    # Geometry passes through (carved); the input socket comes first so the modifier feeds the
    # terrain mesh into it (the same contract as the snow-coverage pass).
    ng.interface.new_socket("Geometry", in_out="INPUT", socket_type="NodeSocketGeometry")
    add_input(ng, "Path Width", "NodeSocketFloat", float(params.get("path_width", 2.4)), 0.0)
    add_input(ng, "Path Falloff", "NodeSocketFloat", float(params.get("path_falloff", 3.5)), 0.0)
    add_input(ng, "Path Depth", "NodeSocketFloat", float(params.get("path_depth", 0.3)), 0.0)
    add_input(ng, "End Taper", "NodeSocketFloat", float(params.get("end_taper", 0.0)), 0.0)
    # Width Variation: fraction the bench half-width wanders along the spline (0 = constant width, the
    # old behaviour). A low-frequency noise sampled at the centreline scales the inner bench width, so
    # the carved channel meanders in lockstep with the water ribbon (curve_water uses the SAME
    # construction and WIDTH_NOISE_SCALE). Seeded > 0 only for the river/stream roles; 0 elsewhere.
    add_input(ng, "Width Variation", "NodeSocketFloat",
              float(params.get("width_var", 0.0)), 0.0, 0.95)
    # Cross-section shape (R4): a flat shoulder extends the bench; the embankment beyond it is
    # slope-aware; Bank Bias skews it to one side of the curve.
    add_input(ng, "Shoulder Width", "NodeSocketFloat", float(params.get("shoulder_width", 0.0)), 0.0)
    add_input(ng, "Bank Slope", "NodeSocketFloat", float(params.get("bank_slope", 1.0)), 0.05)
    add_input(ng, "Bank Bias", "NodeSocketFloat", float(params.get("bank_bias", 0.0)), -1.0, 1.0)
    # Verge band (R5, item-8): the shoulder ring a Verge scatter layer reads (edge_attr), controlled
    # independently of the carve. Verge Gap is the clear metres OUT from the path edge before the band
    # starts (a hedgerow set back from the road); Verge Width is the band's own width; Verge Side is
    # -1 (left only) / 0 (both) / +1 (right only). Distance-based, so a mask-only path (no carve) still
    # has a verge.
    add_input(ng, "Verge Gap", "NodeSocketFloat", float(params.get("verge_gap", 0.0)), 0.0)
    add_input(ng, "Verge Width", "NodeSocketFloat", float(params.get("verge_width", 1.5)), 0.0)
    add_input(ng, "Verge Side", "NodeSocketFloat", float(params.get("verge_side", 0.0)), -1.0, 1.0)
    # Bank Height (impose/river only): the channel banks rise to at least path_z + Bank Height, so
    # the water (which sits below path_z) is contained even where the river runs across a slope and
    # the downhill ground falls away below it. In a valley the natural banks are already higher, so
    # this builds nothing there (MAX with the terrain); on a sidehill it raises a downhill levee.
    if params.get("impose", False):
        add_input(ng, "Bank Height", "NodeSocketFloat", float(params.get("bank_height", 0.4)), 0.0)

    gi = nodes.new("NodeGroupInput")
    gi.location = (-1200, 0)
    geometry = gi.outputs["Geometry"]

    # curve_field solves proximity ONCE: distance to the centreline, the draped curve Z, the
    # arclength distance to the nearest spline end (taper), and the side of the curve (embankment).
    curve = bpy.data.objects.get(params.get("curve", ""))
    # tangent is unused here (the embankment reads only `side`); the river water ribbon consumes it.
    dist, near, path_z, end_dist, side, _tangent = curve_field(ng, curve, (-1100, -560))

    # Bench target Z, in one of two families (docs/SPLINES.md 9 #1):
    # - FOLLOW (dirt path / trail / road): sample the LIVE terrain Z under the centreline (R1) so
    #   the bench tracks a re-sculpt or a curve move, then recess it by Depth.
    # - IMPOSE (river / stream): use the DRAPED monotonic path_z instead, so the terrain conforms
    #   DOWN to the descending water centreline rather than the channel following the ground. The
    #   drape (drape_curve monotonic) guarantees path_z never rises source->mouth, so the carved bed
    #   runs downhill; Depth sinks the bed below the water surface the ribbon later sits on.
    # diff drives both the carve and, by its magnitude, the slope-aware embankment width.
    if params.get("impose", False):
        bench_z = path_z
    else:
        bench_z = _live_terrain_z(ng, geometry, near, path_z, (-1050, -960))
    psep = nodes.new("ShaderNodeSeparateXYZ")
    psep.location = (-780, -120)
    links.new(position(ng, (-960, -120)), psep.inputs[0])
    target_z = math_node(ng, "SUBTRACT", bench_z, gi.outputs["Path Depth"], (-600, -120))
    diff = math_node(ng, "SUBTRACT", target_z, psep.outputs["Z"], (-420, -120))

    # Cross-section band (R4). The flat bench spans Path Width + Shoulder Width at bench level;
    # beyond it the embankment grades back to terrain. Its width is slope-aware (docs/SPLINES.md 9
    # #11): a deeper cut/fill (|diff|) needs a wider run to hold Bank Slope (rise/run), so a bench on
    # a slope ramps out instead of cliffing. Bank Bias skews the embankment to one side (side -1/+1).
    # The extra width is capped at 3x Path Falloff so the band stays bounded on far/steep ground.
    # Inner bench half-width, scaled by the shared width-variation noise so the carved channel wanders
    # in lockstep with the water ribbon (curve_water widens its swept profile by the same wmul). Width
    # Variation 0 -> wmul 1 -> the exact old constant-width bench, so non-river roles are unaffected.
    inner_base = math_node(ng, "ADD", gi.outputs["Path Width"], gi.outputs["Shoulder Width"], (-780, 320))
    wmul = width_multiplier(ng, near, gi.outputs["Width Variation"], (-2400, 520))
    inner = math_node(ng, "MULTIPLY", inner_base, wmul, (-420, 380))
    cut_depth = math_node(ng, "ABSOLUTE", diff, None, (-600, 200))
    emb_raw = math_node(ng, "DIVIDE", cut_depth, gi.outputs["Bank Slope"], (-420, 200))
    cap = math_node(ng, "MULTIPLY", gi.outputs["Path Falloff"], 3.0, (-420, 100))
    emb_extra = math_node(ng, "MINIMUM", emb_raw, cap, (-240, 200))
    emb = math_node(ng, "ADD", gi.outputs["Path Falloff"], emb_extra, (-60, 200))
    bias = math_node(ng, "MULTIPLY", gi.outputs["Bank Bias"], side, (-240, 360))  # -1..1
    bias1 = math_node(ng, "ADD", 1.0, bias, (-60, 360))                           # 0..2
    emb_side = math_node(ng, "MAXIMUM",
                         math_node(ng, "MULTIPLY", emb, bias1, (120, 280)), 0.05, (300, 280))
    outer = math_node(ng, "ADD", inner, emb_side, (480, 320))

    # onpath: 1 on the flat bench, easing to 0 across the embankment. smooth_falloff is its inverse.
    onpath = math_node(ng, "SUBTRACT", 1.0, smooth_falloff(ng, dist, inner, outer, (660, 440)),
                       (840, 440))
    # Endpoint taper (R3, docs/SPLINES.md 9 #8): fade the band over the last End Taper metres so it
    # stops at the tip instead of fanning into a radial semicircle past it (the tip vertex has
    # end_dist 0, so its fan tapers too). End Taper 0 leaves it off (MAXIMUM guards a 0-width range).
    taper_outer = math_node(ng, "MAXIMUM", gi.outputs["End Taper"], 1e-6, (660, 600))
    taper = smooth_falloff(ng, end_dist, 0.0, taper_outer, (840, 620))
    onpath = math_node(ng, "MULTIPLY", onpath, taper, (1020, 500))

    if params.get("impose", False):
        # IMPOSE trough (river/stream): carve a flat bed at path_z - Depth across the bench, then
        # let the banks rise to the HIGHER of the natural terrain or a rim at path_z + Bank Height.
        # In a valley the natural walls are already above the rim, so nothing extra is built; on a
        # cross-slope the fallen-away downhill side is raised to the rim (a levee), so the level
        # water is contained instead of perching/floating off the open downhill edge. wall_t is 0
        # inside the bench (so target = bed there) and ramps to 1 by `outer`; beyond `outer` the rim
        # slopes back to the terrain over a further Path Falloff so the levee is not a cliff. End
        # Taper still fades the ends.
        rim = math_node(ng, "ADD", path_z, gi.outputs["Bank Height"], (660, 40))
        wall_top = math_node(ng, "MAXIMUM", psep.outputs["Z"], rim, (840, 40))
        # The bank rises from the bed to the rim over a SHORT run held at Bank Slope (rise/run), so
        # it reaches above the water right at the bench edge and contains it -- a gradual rise over
        # the whole embankment left the water edge overhanging the still-low near bank. rise =
        # rim - bed = Path Depth + Bank Height. Beyond the rim the levee slopes back to terrain.
        rise = math_node(ng, "ADD", gi.outputs["Path Depth"], gi.outputs["Bank Height"], (660, -60))
        wall_run = math_node(ng, "MAXIMUM",
                             math_node(ng, "DIVIDE", rise, gi.outputs["Bank Slope"], (840, -60)),
                             0.3, (1020, -60))
        wall_top_d = math_node(ng, "ADD", inner, wall_run, (1200, -60))
        wall_t = smooth_falloff(ng, dist, inner, wall_top_d, (660, -140))
        target = mix_float(ng, wall_t, target_z, wall_top, (1040, 40))  # bed in bench -> wall top
        levee = math_node(ng, "ADD", wall_top_d, gi.outputs["Path Falloff"], (660, -280))
        band = math_node(ng, "SUBTRACT", 1.0, smooth_falloff(ng, dist, wall_top_d, levee, (840, -280)),
                         (1020, -280))
        band = math_node(ng, "MULTIPLY", band, taper, (1200, -280))
        offset_raw = math_node(ng, "MULTIPLY",
                               math_node(ng, "SUBTRACT", target, psep.outputs["Z"], (1400, 40)),
                               band, (1740, 120))
    else:
        # FOLLOW bench carve: offset = diff * onpath, so the surface meets the bench on the path
        # (onpath 1) and is untouched off it (onpath 0).
        offset_raw = math_node(ng, "MULTIPLY", diff, onpath, (1120, 120))
    # Junction Z rule (R6, docs/SPLINES.md 9 #9, take-lower): where a PRIOR curve already carved
    # (its bbt_curve_carved coverage rode in on this overlay's input geometry), only let this curve
    # LOWER the surface, never raise it, so a crossing settles to the lower bench instead of the
    # last-built curve clobbering the other. Order-independent for the crossing height; the mix by
    # prior coverage eases a partial overlap rather than stepping it. It reads bbt_curve_carved (not
    # bbt_curve_mask) so a mask-only path -- material/scatter but no carve -- does not suppress a
    # crossing road's fill. A lone curve has prior 0 everywhere, so it is byte-identical to before.
    prior = nodes.new("GeometryNodeInputNamedAttribute")
    prior.data_type = "FLOAT"
    prior.location = (1120, -100)
    prior.inputs["Name"].default_value = "bbt_curve_carved"
    down_only = math_node(ng, "MINIMUM", offset_raw, 0.0, (1300, -40))
    offset = mix_float(ng, prior.outputs["Attribute"], offset_raw, down_only, (1480, 80))
    # carve off = a mask-only overlay (do_terrain off): geometry passes through, the masks below
    # still write.
    carved = displace_z(ng, geometry, offset, (1660, 0)) if params.get("carve", True) else geometry

    # bbt_curve_mask (the geometric band scatter and the shared surface layer read) MAX-accumulated.
    geo = _store_max(ng, carved, "bbt_curve_mask", onpath, (1840, 0))

    # bbt_curve_carved: coverage of curves that actually CARVE (do_terrain on), MAX-accumulated, for
    # the junction take-lower gate above. Written only when this overlay carves, so a mask-only
    # overlay leaves it untouched.
    if params.get("carve", True):
        geo = _store_max(ng, geo, "bbt_curve_carved", onpath, (2020, 0))

    # Curve edge ring (R5, item-8): a verge band set by its OWN metres, not tied to the bench. It
    # starts Verge Gap out from the path edge (Path Width, a radius) and spans Verge Width, with soft
    # edges, faded at the ends by the same End Taper as the band. Stored under THIS curve's own
    # attribute (scatter_panel.edge_attr_name), so a Verge scatter layer targets one path's verge;
    # only this curve's overlay writes it, so it holds this ring alone. A Verge layer with no curve
    # bound reads a name nothing writes, so it scatters nothing (it needs a path).
    v_inner = math_node(ng, "ADD", gi.outputs["Path Width"], gi.outputs["Verge Gap"], (1560, 500))
    v_outer = math_node(ng, "ADD", v_inner, gi.outputs["Verge Width"], (1560, 460))
    # Soft edge, capped at 40% of the band width so a narrow verge stays a distinct band.
    v_soft = math_node(ng, "MINIMUM", 0.4, math_node(ng, "MULTIPLY", gi.outputs["Verge Width"], 0.4, (1560, 420)), (1740, 420))
    v_rise = smooth_falloff(ng, dist, v_inner, math_node(ng, "ADD", v_inner, v_soft, (1740, 520)), (1920, 520))
    v_fall = math_node(ng, "SUBTRACT", 1.0,
                       smooth_falloff(ng, dist, math_node(ng, "SUBTRACT", v_outer, v_soft, (1740, 380)), v_outer, (1920, 380)),
                       (2060, 400))
    v_band = math_node(ng, "MULTIPLY", v_rise, v_fall, (2200, 460))
    v_band = math_node(ng, "MULTIPLY", v_band, taper, (2200, 420))  # fade at the curve ends
    # One-sided select: both sides when Verge Side is ~0, else keep the half whose `side` sign matches.
    v_both = math_node(ng, "SUBTRACT", 1.0, math_node(ng, "ABSOLUTE", gi.outputs["Verge Side"], location=(1740, 300)), (1920, 300))
    v_match = math_node(ng, "GREATER_THAN", math_node(ng, "MULTIPLY", side, gi.outputs["Verge Side"], (1740, 260)), 0.0, (1920, 260))
    v_sidesel = math_node(ng, "MAXIMUM", v_both, v_match, (2060, 280))
    edge = math_node(ng, "MULTIPLY", v_band, v_sidesel, (2360, 440))
    edge_attr = params.get("edge_attr", "")
    if edge_attr:
        geo = _store_max(ng, geo, edge_attr, edge, (2440, 0))

    # bbt_curve_<class> (R5): the per-role surface band, so a distinct role (a paved road) keys its
    # own terrain-material layer instead of sharing one look with dirt paths. Written only when the
    # role asks for a non-shared class (else the shared bbt_curve_mask above is the surface too).
    surface_attr = params.get("surface_attr", "")
    if surface_attr and surface_attr != "bbt_curve_mask":
        geo = _store_max(ng, geo, surface_attr, onpath, (2460, 0))

    # bbt_curve_wet (BobSplines C5.4, the damp bed): the river/stream role writes its band into a
    # wetness mask the terrain material reads (materials.apply_curve_wet) so the bed and banks read
    # damp and glossy, weather-amplified. Same band as onpath (1 in the channel, easing up the
    # banks), MAX-accumulated. Written only when the role asks (wet_attr set), so a dry path leaves
    # it untouched and the attribute reads 0 everywhere else.
    wet_attr = params.get("wet_attr", "")
    if wet_attr:
        geo = _store_max(ng, geo, wet_attr, onpath, (2500, -300))

    # bbt_curve_dist (raw, per curve): the XY distance to the centreline, for a consumer that wants
    # to threshold the band itself. Overlap is last-writer (an accepted v1 artifact, section 9 #9).
    store_dist = nodes.new("GeometryNodeStoreNamedAttribute")
    store_dist.data_type = "FLOAT"
    store_dist.domain = "POINT"
    store_dist.location = (2640, 0)
    links.new(geo, store_dist.inputs["Geometry"])
    store_dist.inputs["Name"].default_value = "bbt_curve_dist"
    links.new(dist, store_dist.inputs["Value"])

    links.new(store_dist.outputs["Geometry"], out.inputs["Geometry"])
