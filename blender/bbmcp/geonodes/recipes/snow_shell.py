"""snow_shell: the snow accumulation shell (BobShaders S4).

Deferred here from Firmament: a geometry-node pass that gives snow real thickness and
silhouette, not just a white shading. It runs as a modifier ON the surface AFTER the
`snow` coverage pass (so `snow_cover` exists), reads that same attribute, and displaces the
surface along its normal by `snow_cover * Thickness`. The coverage is blurred first (Blur
Attribute) so the shell rounds off into soft drifts instead of a hard step, and so thin
coverage does not spike single vertices.

It reads the `snow_cover` attribute the `snow` pass wrote. That pass computes coverage against
the mesh's LOCAL Z, while the surface material computes its own coverage against WORLD Z, so the
shell thickness and the material whiteness align only when the pass is seeded in the surface's
local frame (the panel's `_sync_snow_pass` converts the world-Z snow line to local before feeding
Altitude). Attach it with `build_geonodes_on_object(obj, "snow_shell", "BOB_SnowShell", params)`,
after `BOB_Snow`.
"""

from ..blocks import math_node
from ..scaffold import add_input
from . import recipe


@recipe("snow_shell")
def build(ng, out, params: dict):
    nodes, links = ng.nodes, ng.links

    # Augments incoming geometry, so the Geometry INPUT socket comes first.
    ng.interface.new_socket("Geometry", in_out="INPUT", socket_type="NodeSocketGeometry")
    add_input(ng, "Thickness", "NodeSocketFloat", float(params.get("thickness", 0.3)), 0.0)
    add_input(ng, "Smooth", "NodeSocketInt", int(params.get("smooth", 3)), 0)

    gi = nodes.new("NodeGroupInput")
    gi.location = (-900, 0)
    geometry = gi.outputs["Geometry"]

    # Read the coverage the snow pass wrote, then blur it for rounded drifts.
    named = nodes.new("GeometryNodeInputNamedAttribute")
    named.data_type = "FLOAT"
    named.location = (-900, -220)
    named.inputs["Name"].default_value = "snow_cover"
    blur = nodes.new("GeometryNodeBlurAttribute")
    blur.data_type = "FLOAT"
    blur.location = (-700, -220)
    links.new(named.outputs["Attribute"], blur.inputs["Value"])
    links.new(gi.outputs["Smooth"], blur.inputs["Iterations"])
    cover = blur.outputs["Value"]

    # Offset = normal * (coverage * Thickness): lift the surface where snow sits.
    normal = nodes.new("GeometryNodeInputNormal")
    normal.location = (-700, 120)
    amount = math_node(ng, "MULTIPLY", cover, gi.outputs["Thickness"], (-500, -100))
    offset = nodes.new("ShaderNodeVectorMath")
    offset.operation = "SCALE"
    offset.location = (-300, 0)
    links.new(normal.outputs["Normal"], offset.inputs[0])
    links.new(amount, offset.inputs["Scale"])

    setpos = nodes.new("GeometryNodeSetPosition")
    setpos.location = (-80, 0)
    links.new(geometry, setpos.inputs["Geometry"])
    links.new(offset.outputs["Vector"], setpos.inputs["Offset"])
    links.new(setpos.outputs["Geometry"], out.inputs["Geometry"])
