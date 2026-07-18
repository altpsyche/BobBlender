"""terrain: composed fractal terrain with large-scale shape, detail, and warp.

Two scales combine: a low-frequency Shape sets the big landforms, and a
domain-warped fractal Detail (blended toward ridged) rides on top, stronger on
high ground and quieter in valleys. Sea Level sets where the surface crosses
z = 0, and Height scales the relief.

Modifier inputs: Size, Resolution, Height, Sea Level, Shape Scale, Scale,
Detail, Roughness, Warp, Warp Scale, Ridged, Detail Strength, Seed.

Not yet erosion. That is a later track (a repeat-zone block, or a heightmap pass
in the tools venv). See docs/ARCHITECTURE.md.
"""

from ..blocks import (
    displace_z,
    domain_warp,
    grid_source,
    math_node,
    mix_float,
    noise_field,
    position,
    ridged,
)
from ..scaffold import add_input
from . import recipe


@recipe("terrain")
def build(ng, out, params: dict):
    gi = ng.nodes.new("NodeGroupInput")
    gi.location = (-1700, 0)

    mesh = grid_source(ng, gi, params.get("size", 60.0), params.get("resolution", 400))
    add_input(ng, "Height", "NodeSocketFloat", float(params.get("height", 14.0)))
    add_input(ng, "Sea Level", "NodeSocketFloat", float(params.get("sea_level", 0.35)), 0.0)
    add_input(ng, "Shape Scale", "NodeSocketFloat", float(params.get("shape_scale", 0.025)), 0.0)
    add_input(ng, "Scale", "NodeSocketFloat", float(params.get("scale", 0.11)), 0.0)
    add_input(ng, "Detail", "NodeSocketFloat", float(params.get("detail", 9.0)), 0.0)
    add_input(ng, "Roughness", "NodeSocketFloat", float(params.get("roughness", 0.55)), 0.0)
    add_input(ng, "Warp", "NodeSocketFloat", float(params.get("warp", 6.0)), 0.0)
    add_input(ng, "Warp Scale", "NodeSocketFloat", float(params.get("warp_scale", 0.05)), 0.0)
    add_input(ng, "Ridged", "NodeSocketFloat", float(params.get("ridged", 0.6)), 0.0)
    add_input(ng, "Detail Strength", "NodeSocketFloat", float(params.get("detail_strength", 0.7)), 0.0)
    add_input(ng, "Seed", "NodeSocketInt", int(params.get("seed", 0)))

    seed = gi.outputs["Seed"]
    pos = position(ng, (-1500, -360))

    # Large-scale landform shape (low frequency, smooth), on its own seed.
    shape_seed = math_node(ng, "ADD", seed, 53.0, (-1500, -560))
    shape = noise_field(ng, pos, gi.outputs["Shape Scale"], 3.0, 0.45, shape_seed, (-1300, -360))

    # High-frequency detail, domain warped, blended smooth toward ridged.
    warped = domain_warp(ng, pos, gi.outputs["Warp"], gi.outputs["Warp Scale"], seed, (-1300, 100))
    detail_raw = noise_field(
        ng, warped, gi.outputs["Scale"], gi.outputs["Detail"], gi.outputs["Roughness"], seed, (-700, 100)
    )
    detail = mix_float(ng, gi.outputs["Ridged"], detail_raw, ridged(ng, detail_raw, (-500, -120)), (100, 60))

    # combined = shape * (1 + (detail - 0.5) * Detail Strength): detail rides on
    # and is amplified by the landform height.
    signed = math_node(ng, "SUBTRACT", detail, 0.5, (480, 60))
    weighted = math_node(ng, "MULTIPLY", signed, gi.outputs["Detail Strength"], (660, 60))
    gain = math_node(ng, "ADD", weighted, 1.0, (840, 60))
    combined = math_node(ng, "MULTIPLY", shape, gain, (1020, -100))

    # z = (combined - Sea Level) * Height.
    above = math_node(ng, "SUBTRACT", combined, gi.outputs["Sea Level"], (1200, -100))
    z = math_node(ng, "MULTIPLY", above, gi.outputs["Height"], (1380, -100))
    ng.links.new(displace_z(ng, mesh, z, (1580, 0)), out.inputs["Geometry"])
