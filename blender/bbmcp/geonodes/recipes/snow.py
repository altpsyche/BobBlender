"""snow: the GN-authored snow-coverage pass (S4).

The single source of snow coverage. This runs as a modifier ON the terrain object
(after the terrain modifier, so it sees the displaced surface), passes the geometry
through unchanged, and writes a 0..1 `snow_cover` float attribute on the points
(vertices). Later the BobShaders surface snow material and the accumulation shell both
read that one attribute, so the shell thickness and the material whiteness line up
exactly, with no shader-versus-GN drift.

    snow_cover = Snow * slope_mask(normal Z) * altitude_mask(world Z) * (1 - occlusion)

- slope: snow sticks to up-facing ground. slope_mask rises from 0 (steep) to 1 as the
  surface normal Z passes Slope Threshold, eased over Slope Falloff.
- altitude: snow sticks higher up. altitude_mask rises from 0 to 1 as world Z passes
  Altitude, eased over Altitude Falloff.
- occlusion (crude to start, per the plan): a sheltered surface holds less snow. A
  short upward Raycast against the same mesh marks a point as occluded when something
  is directly above it (an overhang, a crevice). Gated by Occlusion strength (0 = off);
  the term is genuinely crude and is the part meant to improve later.

This recipe adds a Geometry INPUT socket (it augments incoming geometry, it does not
generate its own), so build_geonodes_on_object binds the object's own mesh into it.
"""

from ..blocks import math_node, position, smooth_falloff
from ..scaffold import add_input
from . import recipe


@recipe("snow")
def build(ng, out, params: dict):
    nodes, links = ng.nodes, ng.links

    # Geometry passes through; this pass only adds an attribute. The input socket must
    # come first so the modifier feeds the object's mesh into it.
    ng.interface.new_socket("Geometry", in_out="INPUT", socket_type="NodeSocketGeometry")
    add_input(ng, "Snow", "NodeSocketFloat", float(params.get("snow", 0.5)), 0.0, 1.0)
    add_input(ng, "Slope Threshold", "NodeSocketFloat", float(params.get("slope_threshold", 0.5)), 0.0, 1.0)
    add_input(ng, "Slope Falloff", "NodeSocketFloat", float(params.get("slope_falloff", 0.2)), 0.0, 1.0)
    add_input(ng, "Altitude", "NodeSocketFloat", float(params.get("altitude", 0.0)))
    add_input(ng, "Altitude Falloff", "NodeSocketFloat", float(params.get("altitude_falloff", 5.0)), 0.0)
    add_input(ng, "Occlusion", "NodeSocketFloat", float(params.get("occlusion", 0.0)), 0.0, 1.0)
    add_input(ng, "Occlusion Distance", "NodeSocketFloat", float(params.get("occlusion_distance", 2.0)), 0.0)

    gi = nodes.new("NodeGroupInput")
    gi.location = (-1000, 0)
    geometry = gi.outputs["Geometry"]

    # Slope: keep up-facing ground. slope_mask 0 on steep faces, 1 on flat/gentle.
    normal = nodes.new("GeometryNodeInputNormal")
    normal.location = (-1000, -200)
    nsep = nodes.new("ShaderNodeSeparateXYZ")
    nsep.location = (-820, -200)
    links.new(normal.outputs["Normal"], nsep.inputs[0])
    slope_lo = math_node(ng, "SUBTRACT", gi.outputs["Slope Threshold"], gi.outputs["Slope Falloff"], (-820, -360))
    slope_mask = smooth_falloff(ng, nsep.outputs["Z"], slope_lo, gi.outputs["Slope Threshold"], (-640, -260))

    # Altitude: snow above a world-Z line, eased over Altitude Falloff.
    pos = position(ng, (-1000, -520))
    psep = nodes.new("ShaderNodeSeparateXYZ")
    psep.location = (-820, -520)
    links.new(pos, psep.inputs[0])
    alt_hi = math_node(ng, "ADD", gi.outputs["Altitude"], gi.outputs["Altitude Falloff"], (-820, -680))
    alt_mask = smooth_falloff(ng, psep.outputs["Z"], gi.outputs["Altitude"], alt_hi, (-640, -560))

    # Occlusion (crude): a short upward ray against the same mesh. A hit means a
    # surface is directly above (sheltered), so less snow. Gated by Occlusion strength.
    lift = nodes.new("ShaderNodeCombineXYZ")
    lift.location = (-640, -820)
    lift.inputs["Z"].default_value = 0.01  # start just above the surface
    src = nodes.new("ShaderNodeVectorMath")
    src.operation = "ADD"
    src.location = (-460, -820)
    links.new(pos, src.inputs[0])
    links.new(lift.outputs["Vector"], src.inputs[1])
    up = nodes.new("ShaderNodeCombineXYZ")
    up.location = (-460, -980)
    up.inputs["Z"].default_value = 1.0
    ray = nodes.new("GeometryNodeRaycast")
    ray.location = (-280, -820)
    links.new(geometry, ray.inputs["Target Geometry"])
    links.new(src.outputs["Vector"], ray.inputs["Source Position"])
    links.new(up.outputs["Vector"], ray.inputs["Ray Direction"])
    links.new(gi.outputs["Occlusion Distance"], ray.inputs["Ray Length"])
    hit = math_node(ng, "MULTIPLY", ray.outputs["Is Hit"], gi.outputs["Occlusion"], (-100, -820))
    keep = math_node(ng, "SUBTRACT", 1.0, hit, (80, -820))

    # coverage = Snow * slope * altitude * (1 - occlusion), all factors in 0..1.
    cover = math_node(ng, "MULTIPLY", gi.outputs["Snow"], slope_mask, (-200, -300))
    cover = math_node(ng, "MULTIPLY", cover, alt_mask, (-20, -300))
    cover = math_node(ng, "MULTIPLY", cover, keep, (160, -300))

    store = nodes.new("GeometryNodeStoreNamedAttribute")
    store.data_type = "FLOAT"
    store.domain = "POINT"
    store.location = (400, 0)
    links.new(geometry, store.inputs["Geometry"])
    store.inputs["Name"].default_value = "snow_cover"
    links.new(cover, store.inputs["Value"])
    links.new(store.outputs["Geometry"], out.inputs["Geometry"])
