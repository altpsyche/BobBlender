"""scatter_along: instance assets ALONG a curve, sitting on the terrain (BobSplines, the scatter mask).

The distribution counterpart to the surface `scatter` recipe: instead of Poisson points across the
terrain, it places instances evenly along a curve centreline (fence posts, cobbles, boulders lining
a road). Points come from Curve to Points at a count derived from the curve length and the Spacing
knob, so the spacing stays even regardless of the curve's shape.

Placement, in order:
- Offset shifts points sideways off the centreline (a road edge), Jitter adds a random along/across
  wobble so a row does not read machine-regular.
- The points are PROJECTED down onto the emitter (a downward raycast) so instances sit on the
  terrain surface following the curve's plan-view route, then Z Offset raises or sinks them.
- Rotation keeps instances UPRIGHT: align yaws them about Z to follow the path heading (plus the
  Yaw knob), otherwise a random Z spin. It never tips them over (the reason not to use Curve to
  Points' own Rotation, which aligns the Z axis to the tangent and lays uprights flat).

Params: curve + emitter (object names), assets (collection name), align (build-time). Editable
knobs: Spacing, Offset, Z Offset, Yaw, Jitter, Seed, Min/Max Scale.
"""

import bpy

from ..blocks import math_node, object_geometry, position, random_value
from ..scaffold import add_input
from . import recipe

TAU = 6.283185307179586
DEG2RAD = 0.017453292519943295
_LIFT = 10000.0  # raycast the projection from well above any terrain, straight down
_MAX_ALONG = 10000  # hard cap on along-curve instance count (guards a tiny Spacing on a long curve)


def _vadd(ng, a, b, loc):
    n = ng.nodes.new("ShaderNodeVectorMath")
    n.operation = "ADD"
    n.location = loc
    ng.links.new(a, n.inputs[0])
    ng.links.new(b, n.inputs[1])
    return n.outputs["Vector"]


def _vscale(ng, vec, scalar, loc):
    n = ng.nodes.new("ShaderNodeVectorMath")
    n.operation = "SCALE"
    n.location = loc
    ng.links.new(vec, n.inputs[0])
    ng.links.new(scalar, n.inputs["Scale"])
    return n.outputs["Vector"]


@recipe("scatter_along")
def build(ng, out, params: dict):
    nodes, links = ng.nodes, ng.links
    curve = bpy.data.objects.get(params.get("curve", ""))
    emitter = bpy.data.objects.get(params.get("emitter", ""))
    assets = bpy.data.collections.get(params.get("assets", ""))
    align = bool(params.get("align", True))

    add_input(ng, "Spacing", "NodeSocketFloat", float(params.get("spacing", 2.0)), 0.01)
    add_input(ng, "Offset", "NodeSocketFloat", float(params.get("offset", 0.0)))
    add_input(ng, "Z Offset", "NodeSocketFloat", float(params.get("z_offset", 0.0)))
    add_input(ng, "Yaw", "NodeSocketFloat", float(params.get("yaw", 0.0)), -180.0, 180.0)
    add_input(ng, "Jitter", "NodeSocketFloat", float(params.get("jitter", 0.0)), 0.0)
    add_input(ng, "Seed", "NodeSocketInt", int(params.get("seed", 0)))
    add_input(ng, "Min Scale", "NodeSocketFloat", float(params.get("min_scale", 0.8)), 0.0)
    add_input(ng, "Max Scale", "NodeSocketFloat", float(params.get("max_scale", 1.2)), 0.0)

    gi = nodes.new("NodeGroupInput")
    gi.location = (-1200, 0)
    seed = gi.outputs["Seed"]

    curve_geo = object_geometry(ng, curve, (-1000, 240))

    # Even spacing: count = curve length / Spacing (at least one), along the spline.
    clen = nodes.new("GeometryNodeCurveLength")
    clen.location = (-820, 420)
    links.new(curve_geo, clen.inputs["Curve"])
    count = math_node(ng, "DIVIDE", clen.outputs["Length"], gi.outputs["Spacing"], (-640, 420))
    count = math_node(ng, "FLOOR", count, None, (-480, 420))
    count = math_node(ng, "MAXIMUM", count, 1.0, (-400, 420))
    # Cap the instance count so a tiny Spacing on a long curve (Spacing min is 0.01) cannot request
    # ~100x the curve length in points and hang the eval. _MAX_ALONG is far above any real fence/row.
    count = math_node(ng, "MINIMUM", count, float(_MAX_ALONG), (-320, 420))

    c2p = nodes.new("GeometryNodeCurveToPoints")
    try:
        c2p.mode = "COUNT"  # older Blender: a node property; 5.2: a menu socket, COUNT is the default
    except (AttributeError, TypeError):
        pass
    c2p.location = (-140, 220)
    links.new(curve_geo, c2p.inputs["Curve"])
    links.new(count, c2p.inputs["Count"])  # Int socket, implicitly converts the float count

    # Tangent (unit, from Curve to Points) and its XY perpendicular, for Offset/Jitter and yaw.
    tsep = nodes.new("ShaderNodeSeparateXYZ")
    tsep.location = (60, 560)
    links.new(c2p.outputs["Tangent"], tsep.inputs[0])
    tan_flat = nodes.new("ShaderNodeCombineXYZ")
    tan_flat.location = (240, 620)
    links.new(tsep.outputs["X"], tan_flat.inputs["X"])
    links.new(tsep.outputs["Y"], tan_flat.inputs["Y"])
    perp = nodes.new("ShaderNodeCombineXYZ")  # (-ty, tx, 0): sideways in the XY plane
    perp.location = (240, 460)
    links.new(math_node(ng, "MULTIPLY", tsep.outputs["Y"], -1.0, (60, 420)), perp.inputs["X"])
    links.new(tsep.outputs["X"], perp.inputs["Y"])

    # Offset + jitter. neg_j..Jitter random streams (decorrelated from scale/index by seed shift).
    neg_j = math_node(ng, "MULTIPLY", gi.outputs["Jitter"], -1.0, (60, 300))
    jl = random_value(ng, "FLOAT", neg_j, gi.outputs["Jitter"],
                      math_node(ng, "ADD", seed, 11, (60, 200)), (240, 260))
    ja = random_value(ng, "FLOAT", neg_j, gi.outputs["Jitter"],
                      math_node(ng, "ADD", seed, 23, (60, 100)), (240, 100))
    lateral = math_node(ng, "ADD", gi.outputs["Offset"], jl, (440, 260))
    disp = _vadd(ng, position(ng, (240, -40)), _vscale(ng, perp.outputs["Vector"], lateral, (440, 460)),
                 (620, 40))
    disp = _vadd(ng, disp, _vscale(ng, tan_flat.outputs["Vector"], ja, (440, 620)), (800, 40))

    # Project down onto the emitter: lift the displaced XY high, raycast straight down.
    emitter_geo = object_geometry(ng, emitter, (620, -260))
    dsep = nodes.new("ShaderNodeSeparateXYZ")
    dsep.location = (980, 120)
    links.new(disp, dsep.inputs[0])
    src = nodes.new("ShaderNodeCombineXYZ")
    src.location = (1160, 120)
    links.new(dsep.outputs["X"], src.inputs["X"])
    links.new(dsep.outputs["Y"], src.inputs["Y"])
    links.new(math_node(ng, "ADD", dsep.outputs["Z"], _LIFT, (980, 40)), src.inputs["Z"])
    ray = nodes.new("GeometryNodeRaycast")
    ray.location = (1160, -80)
    links.new(emitter_geo, ray.inputs["Target Geometry"])
    links.new(src.outputs["Vector"], ray.inputs["Source Position"])
    ray.inputs["Ray Direction"].default_value = (0.0, 0.0, -1.0)
    ray.inputs["Ray Length"].default_value = _LIFT * 2.0
    # Final position = hit point raised by Z Offset (leave a miss, off the terrain edge, alone).
    landed = _vadd(ng, ray.outputs["Hit Position"],
                   _zvec(ng, gi.outputs["Z Offset"], (1160, -300)), (1340, 60))
    grounded = nodes.new("GeometryNodeSetPosition")
    grounded.location = (1520, 200)
    links.new(c2p.outputs["Points"], grounded.inputs["Geometry"])
    links.new(ray.outputs["Is Hit"], grounded.inputs["Selection"])
    links.new(landed, grounded.inputs["Position"])

    # Assets: one separate child per instance, a random pick.
    coll = nodes.new("GeometryNodeCollectionInfo")
    coll.location = (1520, -260)
    if assets is not None:
        coll.inputs["Collection"].default_value = assets
    coll.inputs["Separate Children"].default_value = True
    coll.inputs["Reset Children"].default_value = True
    domain = nodes.new("GeometryNodeAttributeDomainSize")
    domain.component = "INSTANCES"
    domain.location = (1700, -420)
    links.new(coll.outputs["Instances"], domain.inputs["Geometry"])
    max_index = math_node(ng, "SUBTRACT", domain.outputs["Instance Count"], 1, (1880, -420))
    # Decorrelated seed streams: asset pick, scale, and random spin each shift the seed by a distinct
    # offset (jitter already uses +11/+23), so the biggest rock does not always take the same scale or
    # facing. Same base Seed -> reproducible; different streams -> independent.
    index = random_value(ng, "INT", 0, max_index,
                         math_node(ng, "ADD", seed, 31, (1880, -520)), (2060, -420))

    scale = random_value(ng, "FLOAT", gi.outputs["Min Scale"], gi.outputs["Max Scale"],
                         math_node(ng, "ADD", seed, 43, (1700, 420)), (1880, 420))

    # Rotation: UPRIGHT always. align -> yaw about Z to the path heading + the Yaw knob; else a
    # random Z spin. Never Curve to Points' Rotation (it tips uprights onto the path).
    if align:
        heading = math_node(ng, "ARCTAN2", tsep.outputs["Y"], tsep.outputs["X"], (1880, 640))
        yaw_rad = math_node(ng, "MULTIPLY", gi.outputs["Yaw"], DEG2RAD, (1880, 520))
        angle = math_node(ng, "ADD", heading, yaw_rad, (2060, 600))
    else:
        angle = random_value(ng, "FLOAT", 0.0, TAU,
                             math_node(ng, "ADD", seed, 53, (1880, 600)), (2060, 600))
    rot = _zvec(ng, angle, (2240, 560))

    inst = nodes.new("GeometryNodeInstanceOnPoints")
    inst.location = (2260, 0)
    links.new(grounded.outputs["Geometry"], inst.inputs["Points"])
    links.new(coll.outputs["Instances"], inst.inputs["Instance"])
    inst.inputs["Pick Instance"].default_value = True
    links.new(index, inst.inputs["Instance Index"])
    links.new(scale, inst.inputs["Scale"])
    links.new(rot, inst.inputs["Rotation"])

    links.new(inst.outputs["Instances"], out.inputs["Geometry"])


def _zvec(ng, z_scalar, loc):
    """A (0, 0, z) vector from a scalar."""
    n = ng.nodes.new("ShaderNodeCombineXYZ")
    n.location = loc
    ng.links.new(z_scalar, n.inputs["Z"])
    return n.outputs["Vector"]
