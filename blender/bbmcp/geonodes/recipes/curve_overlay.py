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
   (docs/SPLINES.md 9 #2 / #4): bbt_curve_mask (0..1, 1 on the band), bbt_curve_edge (the shoulder/
   embankment ring only, for auto edge-scatter, R5), an optional per-role surface class attribute
   (R5), and bbt_curve_dist (the XY distance). The masks MAX-accumulate across curves, so
   overlapping paths add rather than overwrite (a prior overlay's value is read back and maxed).

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

from ..blocks import curve_field, displace_z, math_node, mix_float, position, smooth_falloff
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
    add_input(ng, "Path Depth", "NodeSocketFloat", float(params.get("path_depth", 0.3)))
    add_input(ng, "End Taper", "NodeSocketFloat", float(params.get("end_taper", 0.0)), 0.0)
    # Cross-section shape (R4): a flat shoulder extends the bench; the embankment beyond it is
    # slope-aware; Bank Bias skews it to one side of the curve.
    add_input(ng, "Shoulder Width", "NodeSocketFloat", float(params.get("shoulder_width", 0.0)), 0.0)
    add_input(ng, "Bank Slope", "NodeSocketFloat", float(params.get("bank_slope", 1.0)), 0.05)
    add_input(ng, "Bank Bias", "NodeSocketFloat", float(params.get("bank_bias", 0.0)), -1.0, 1.0)

    gi = nodes.new("NodeGroupInput")
    gi.location = (-1200, 0)
    geometry = gi.outputs["Geometry"]

    # curve_field solves proximity ONCE: distance to the centreline, the draped curve Z, the
    # arclength distance to the nearest spline end (taper), and the side of the curve (embankment).
    curve = bpy.data.objects.get(params.get("curve", ""))
    dist, near, path_z, end_dist, side = curve_field(ng, curve, (-1100, -560))

    # Bench target: level toward (live terrain Z at the centreline) - Depth, sampled LIVE off the
    # incoming terrain (R1), not the stale draped path_z. diff drives both the carve and, by its
    # magnitude, the slope-aware embankment width.
    live_z = _live_terrain_z(ng, geometry, near, path_z, (-1050, -960))
    psep = nodes.new("ShaderNodeSeparateXYZ")
    psep.location = (-780, -120)
    links.new(position(ng, (-960, -120)), psep.inputs[0])
    target_z = math_node(ng, "SUBTRACT", live_z, gi.outputs["Path Depth"], (-600, -120))
    diff = math_node(ng, "SUBTRACT", target_z, psep.outputs["Z"], (-420, -120))

    # Cross-section band (R4). The flat bench spans Path Width + Shoulder Width at bench level;
    # beyond it the embankment grades back to terrain. Its width is slope-aware (docs/SPLINES.md 9
    # #11): a deeper cut/fill (|diff|) needs a wider run to hold Bank Slope (rise/run), so a bench on
    # a slope ramps out instead of cliffing. Bank Bias skews the embankment to one side (side -1/+1).
    # The extra width is capped at 3x Path Falloff so the band stays bounded on far/steep ground.
    inner = math_node(ng, "ADD", gi.outputs["Path Width"], gi.outputs["Shoulder Width"], (-600, 320))
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

    # Bench carve: offset = diff * onpath, so the surface meets the bench on the path (onpath 1) and
    # is untouched off it (onpath 0). carve off = a mask-only overlay (do_terrain off): geometry
    # passes through, the masks below still write.
    offset = math_node(ng, "MULTIPLY", diff, onpath, (1200, 120))
    carved = displace_z(ng, geometry, offset, (1380, 0)) if params.get("carve", True) else geometry

    # bbt_curve_mask (the geometric band scatter and the shared surface layer read) MAX-accumulated.
    geo = _store_max(ng, carved, "bbt_curve_mask", onpath, (1560, 0))

    # bbt_curve_edge (R5): the shoulder/embankment ring only -- onpath weighted by a core-off mask
    # that is 0 on the driving surface (dist < Path Width) and 1 beyond the flat bench -- so an auto
    # edge-scatter layer keeps to the verge, not the road surface.
    core_outer = math_node(ng, "MAXIMUM", inner, math_node(ng, "ADD", gi.outputs["Path Width"], 0.1, (1560, 380)), (1740, 380))
    edge = math_node(ng, "MULTIPLY", onpath,
                     smooth_falloff(ng, dist, gi.outputs["Path Width"], core_outer, (1740, 460)), (1920, 440))
    geo = _store_max(ng, geo, "bbt_curve_edge", edge, (2100, 0))

    # bbt_curve_<class> (R5): the per-role surface band, so a distinct role (a paved road) keys its
    # own terrain-material layer instead of sharing one look with dirt paths. Written only when the
    # role asks for a non-shared class (else the shared bbt_curve_mask above is the surface too).
    surface_attr = params.get("surface_attr", "")
    if surface_attr and surface_attr != "bbt_curve_mask":
        geo = _store_max(ng, geo, surface_attr, onpath, (2280, 0))

    # bbt_curve_dist (raw, per curve): the XY distance to the centreline, for a consumer that wants
    # to threshold the band itself. Overlap is last-writer (an accepted v1 artifact, section 9 #9).
    store_dist = nodes.new("GeometryNodeStoreNamedAttribute")
    store_dist.data_type = "FLOAT"
    store_dist.domain = "POINT"
    store_dist.location = (2460, 0)
    links.new(geo, store_dist.inputs["Geometry"])
    store_dist.inputs["Name"].default_value = "bbt_curve_dist"
    links.new(dist, store_dist.inputs["Value"])

    links.new(store_dist.outputs["Geometry"], out.inputs["Geometry"])
