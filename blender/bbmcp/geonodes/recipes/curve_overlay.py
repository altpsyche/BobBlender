"""curve_overlay: carve a cross-section profile along a curve into the terrain (BobSplines C2).

The standalone terrain-shape overlay (docs/SPLINES.md 4.3), superseding the inline path grade
that used to live in heightmap_terrain. It runs as its OWN modifier ON the terrain object (after
the terrain modifier, so it sees the displaced surface), one modifier per curve, so a network of
paths stacks instead of being limited to a single inline path. It:

1. Levels a bench toward the curve's own draped Z (path_z - Path Depth) within Path Width, easing
   back to the untouched terrain over Path Falloff. This is the follow-terrain family (dirt path,
   trail, road); in C2 the roles differ only in these knob defaults, and the profile is symmetric.
2. Writes the curve mask attributes the shader and scatter READ instead of re-solving proximity
   (docs/SPLINES.md 9 #2 / #4): bbt_curve_mask (0..1, 1 on the band) and bbt_curve_dist (the XY
   distance to the centreline). bbt_curve_mask MAX-accumulates across curves, so overlapping paths
   add rather than overwrite (the value a prior overlay stored is read back and maxed).

Cross-section knobs live ONCE here, on the overlay modifier, snapshot-restored across a rebuild
like any GN knob (the single-owner decision): nothing downstream duplicates a width/depth knob.

This recipe adds a Geometry INPUT socket, so build_geonodes_on_object binds the terrain mesh in.
path_z reads the curve's current Z (the Paths panel drapes it onto the terrain at Build when the
terrain carries a heightmap, else the curve's authored Z is used), so it works on any terrain mesh.
"""

import bpy

from ..blocks import curve_field, displace_z, math_node, position, smooth_falloff
from ..scaffold import add_input
from . import recipe


@recipe("curve_overlay")
def build(ng, out, params: dict):
    nodes, links = ng.nodes, ng.links

    # Geometry passes through (carved); the input socket comes first so the modifier feeds the
    # terrain mesh into it (the same contract as the snow-coverage pass).
    ng.interface.new_socket("Geometry", in_out="INPUT", socket_type="NodeSocketGeometry")
    add_input(ng, "Path Width", "NodeSocketFloat", float(params.get("path_width", 2.4)), 0.0)
    add_input(ng, "Path Falloff", "NodeSocketFloat", float(params.get("path_falloff", 3.5)), 0.0)
    add_input(ng, "Path Depth", "NodeSocketFloat", float(params.get("path_depth", 0.3)))

    gi = nodes.new("NodeGroupInput")
    gi.location = (-1200, 0)
    geometry = gi.outputs["Geometry"]

    # curve_field solves proximity ONCE: distance to the centreline + the draped curve Z there.
    curve = bpy.data.objects.get(params.get("curve", ""))
    dist, _near, path_z = curve_field(ng, curve, (-1100, -560))

    # onpath: 1 inside the Path Width band, easing to 0 over Path Falloff. smooth_falloff is 0 on
    # the path and 1 off it, so onpath is its inverse.
    outer = math_node(ng, "ADD", gi.outputs["Path Width"], gi.outputs["Path Falloff"], (-600, 520))
    off_mask = smooth_falloff(ng, dist, gi.outputs["Path Width"], outer, (-420, 620))
    onpath = math_node(ng, "SUBTRACT", 1.0, off_mask, (-220, 620))

    # Bench: level toward path_z - Depth. offset = (target_z - terrain_z) * onpath, so the surface
    # meets the bench on the path and is untouched off it.
    psep = nodes.new("ShaderNodeSeparateXYZ")
    psep.location = (-600, -120)
    links.new(position(ng, (-780, -120)), psep.inputs[0])
    target_z = math_node(ng, "SUBTRACT", path_z, gi.outputs["Path Depth"], (-420, -120))
    diff = math_node(ng, "SUBTRACT", target_z, psep.outputs["Z"], (-240, -120))
    offset = math_node(ng, "MULTIPLY", diff, onpath, (-40, 20))
    carved = displace_z(ng, geometry, offset, (220, 0))

    # bbt_curve_mask, MAX-accumulated: read the value a prior curve's overlay stored (0 when the
    # attribute is absent), keep the stronger band, so overlapping paths add rather than overwrite.
    existing = nodes.new("GeometryNodeInputNamedAttribute")
    existing.data_type = "FLOAT"
    existing.location = (220, -320)
    existing.inputs["Name"].default_value = "bbt_curve_mask"
    mask = math_node(ng, "MAXIMUM", existing.outputs["Attribute"], onpath, (420, -320))

    store_mask = nodes.new("GeometryNodeStoreNamedAttribute")
    store_mask.data_type = "FLOAT"
    store_mask.domain = "POINT"
    store_mask.location = (620, 0)
    links.new(carved, store_mask.inputs["Geometry"])
    store_mask.inputs["Name"].default_value = "bbt_curve_mask"
    links.new(mask, store_mask.inputs["Value"])

    # bbt_curve_dist (raw, per curve): the XY distance to the centreline, for a consumer that wants
    # to threshold the band itself. Overlap is last-writer (an accepted v1 artifact, section 9 #9).
    store_dist = nodes.new("GeometryNodeStoreNamedAttribute")
    store_dist.data_type = "FLOAT"
    store_dist.domain = "POINT"
    store_dist.location = (820, 0)
    links.new(store_mask.outputs["Geometry"], store_dist.inputs["Geometry"])
    store_dist.inputs["Name"].default_value = "bbt_curve_dist"
    links.new(dist, store_dist.inputs["Value"])

    links.new(store_dist.outputs["Geometry"], out.inputs["Geometry"])
