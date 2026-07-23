"""snow: the GN-authored snow pass (S4).

Runs as a modifier ON the terrain object (after the terrain modifier, so it sees the
displaced surface), passes the geometry through unchanged, and writes two POINT float
attributes:
- `snow_cover` (0..1): the full coverage, read only by the accumulation shell (snow_shell),
  which displaces the surface by it for real thickness and drifts. The shell is geometry,
  so it needs a geometry value; this pass is its source.
- `snow_occlusion` (0..1): the raw shelter term, read by the surface material as an optional
  darkening. The material computes its OWN coverage (keyed off the env snow line) and does
  not read snow_cover, so terrain no longer depends on the pass to whiten. Absent, the
  material reads 0 (full snow), so a missing pass can never leave the terrain bare.

The pass is therefore optional detail (the shell + occlusion), not the coverage authority.
The panel seeds the pass's Snow (from the temperature) and Altitude (the world-Z snow line)
on build / Apply Season / Use Env Snow, so the shell's coverage tracks the same line the
material shades to.

    snow_cover = Snow * slope_mask(normal Z) * altitude_mask(local Z) * (1 - occlusion)
    snow_occlusion = is_hit * Occlusion

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

    # Two attributes, two consumers. snow_cover is the full coverage for the accumulation shell
    # (snow_shell displaces geometry by it, so the shell still needs the pass). snow_occlusion is
    # the raw shelter term (hit * Occlusion, 0..1) for the surface material, which computes its
    # own coverage and only reads this to darken sheltered spots; absent it reads 0 (full snow),
    # so the material never depends on the pass. Occlusion 0 leaves snow_occlusion 0 (no effect).
    store = nodes.new("GeometryNodeStoreNamedAttribute")
    store.data_type = "FLOAT"
    store.domain = "POINT"
    store.location = (400, 0)
    links.new(geometry, store.inputs["Geometry"])
    store.inputs["Name"].default_value = "snow_cover"
    links.new(cover, store.inputs["Value"])

    store_occ = nodes.new("GeometryNodeStoreNamedAttribute")
    store_occ.data_type = "FLOAT"
    store_occ.domain = "POINT"
    store_occ.location = (580, 0)
    links.new(store.outputs["Geometry"], store_occ.inputs["Geometry"])
    store_occ.inputs["Name"].default_value = "snow_occlusion"
    links.new(hit, store_occ.inputs["Value"])
    links.new(store_occ.outputs["Geometry"], out.inputs["Geometry"])
