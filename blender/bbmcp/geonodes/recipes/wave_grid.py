"""wave_grid: a grid whose Z ripples as sin(radial_distance * Frequency) * Amplitude."""

from ..blocks import displace_z, grid_source, math_node, radial_distance
from ..scaffold import add_input
from . import recipe


@recipe("wave_grid")
def build(ng, out, params: dict):
    group_in = ng.nodes.new("NodeGroupInput")
    group_in.location = (-900, 0)

    mesh = grid_source(ng, group_in, params.get("size", 10.0), params.get("resolution", 64))
    add_input(ng, "Amplitude", "NodeSocketFloat", float(params.get("amplitude", 1.0)))
    add_input(ng, "Frequency", "NodeSocketFloat", float(params.get("frequency", 1.0)), 0.0)

    dist = radial_distance(ng)
    ramped = math_node(ng, "MULTIPLY", dist, group_in.outputs["Frequency"], (-200, -200))
    waved = math_node(ng, "SINE", ramped, location=(0, -200))
    z = math_node(ng, "MULTIPLY", waved, group_in.outputs["Amplitude"], (200, -200))

    ng.links.new(displace_z(ng, mesh, z), out.inputs["Geometry"])
