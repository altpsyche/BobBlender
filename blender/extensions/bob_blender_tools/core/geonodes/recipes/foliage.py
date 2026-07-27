"""foliage: a procedural tree or shrub grown from a skeleton, swept to mesh (BobFoliage F1).

docs/FOLIAGE.md. The geometry is entirely procedural and image-to-3D supplies none of it, for one
structural reason: branches attach to POINTS ON A CURVE with a tangent and a radius, and a generated
mesh has no skeleton to offer, so nothing can grow from one. Generation's job here is the two texture
sets (bark, and the leaf atlas F2 instances on the tips), never the shape.

How it grows. Level 0 is a trunk: a vertical line resampled to `Segments`, bent by a noise field
whose amplitude rises up the trunk (`Gnarl`) plus a steady `Lean`, with the radius tapering from
`Trunk Radius` toward the tip. Every level after that repeats one step:

    trim the parent to its upper `L<n> Start` fraction -> resample to `L<n> Branches` points
    -> instance a unit +Z curve on those points, rotated to leave the parent at `L<n> Angle`
    and spun around it by `L<n> Phyllotaxy` per index -> realize -> bend and taper it

so a level is data, not code, and `levels` is how many times the step is built. There is no recursion
primitive in Geometry Nodes; this is the fixed stack every GN tree generator uses instead.

Two invariants worth stating, because both fail silently when broken:

- **A branch base never moves.** The bend offset is weighted by the branch's own spline factor, which
  is 0 at its base, so the point that coincides with its parent stays coincident. Weight it any other
  way and the tree comes apart into floating sticks that still render.
- **A parent's length reaches its children.** Scale has to be a fraction of the PARENT's length or a
  level-3 twig is as long as the trunk. `SplineLength` is a curve-domain read that cannot survive the
  trim and resample, so the length is stored as the point attribute `bbt_fol_plen` first: constants
  interpolate to themselves, so it arrives intact at every attachment point.

Attributes written for later phases: `bbt_fol_plen` (the curve's own length, per point),
`bbt_fol_level` (per curve, 0 for the trunk), and `bbt_fol_tip` (per point, 1 at a branch tip and 0
elsewhere) -- which is what F2's leaf cards instance on.

Params: seed, levels (1-4), segments, profile_segments, plus the shape values below. Structural
(a rebuild): levels, profile_segments. Everything else is a live modifier knob, so tuning a tree is
a slider drag and only changing its depth costs a rebuild.
"""

import bpy

from ..blocks import math_node, noise_field, position
from ..scaffold import add_input
from . import recipe

MAX_LEVELS = 4          # four is a tree; the fifth level is twigs no card resolution can show
MIN_RADIUS = 0.004      # a curve radius of 0 collapses the sweep into a degenerate zero-area tube
_DEG = 0.017453292519943295

# Per-level defaults, index 0 = level 1 (the first branches off the trunk). Chosen to read as a
# conifer at `levels=3`, because that is the redwood the run that started this failed to make.
_LEVEL_DEFAULTS = (
    # branches, angle, length, radius, phyllotaxy, start
    (9, 62.0, 0.42, 0.34, 137.5, 0.28),
    (5, 55.0, 0.46, 0.42, 137.5, 0.22),
    (4, 48.0, 0.50, 0.50, 137.5, 0.18),
    (3, 42.0, 0.55, 0.55, 137.5, 0.15),
)

# 137.5 degrees is the golden angle, which is what real phyllotaxy converges on: successive children
# never line up, so a whorl does not read as a wheel of spokes. It is a default, not a constant --
# a whorled conifer wants 90 and an opposite-leaved species wants 180.


def _f(ng, int_socket, location=(0, 0)):
    """An INT socket as a float. Blender converts on link, but noise `W` and Math both want a real
    float socket, and going through one Math node makes that explicit rather than incidental."""
    return math_node(ng, "MULTIPLY", int_socket, 1.0, location)


def _combine(ng, x, y, z, location=(0, 0)):
    node = ng.nodes.new("ShaderNodeCombineXYZ")
    node.location = location
    for socket, value in zip(("X", "Y", "Z"), (x, y, z)):
        if isinstance(value, (int, float)):
            node.inputs[socket].default_value = float(value)
        else:
            ng.links.new(value, node.inputs[socket])
    return node.outputs["Vector"]


def _spline_parameter(ng, location=(0, 0)):
    node = ng.nodes.new("GeometryNodeSplineParameter")
    node.location = location
    return node.outputs["Factor"], node.outputs["Index"]


def _store(ng, geo, name, value, data_type, domain, location):
    node = ng.nodes.new("GeometryNodeStoreNamedAttribute")
    node.data_type = data_type
    node.domain = domain
    node.location = location
    ng.links.new(geo, node.inputs["Geometry"])
    node.inputs["Name"].default_value = name
    if isinstance(value, (int, float)):
        node.inputs["Value"].default_value = value
    else:
        ng.links.new(value, node.inputs["Value"])
    return node.outputs["Geometry"]


def _named(ng, name, data_type, location=(0, 0)):
    node = ng.nodes.new("GeometryNodeInputNamedAttribute")
    node.data_type = data_type
    node.location = location
    node.inputs["Name"].default_value = name
    return node.outputs["Attribute"]


def _resample(ng, curve, count, location=(0, 0)):
    node = ng.nodes.new("GeometryNodeResampleCurve")
    node.location = location
    node.inputs["Mode"].default_value = "Count"  # a menu SOCKET in 5.2, not an enum property
    ng.links.new(curve, node.inputs["Curve"])
    if isinstance(count, int):
        node.inputs["Count"].default_value = count
    else:
        ng.links.new(count, node.inputs["Count"])
    return node.outputs["Curve"]


def _unit_line(ng, segments, location=(0, 0)):
    """A straight unit curve from the origin to +Z, resampled. The instanced child template: it is
    scaled to length and rotated into place, so it has to be unit-length and +Z aligned -- that is
    the axis `Align Rotation to Vector` steers."""
    line = ng.nodes.new("GeometryNodeCurvePrimitiveLine")
    line.mode = "POINTS"
    line.location = location
    line.inputs["Start"].default_value = (0.0, 0.0, 0.0)
    line.inputs["End"].default_value = (0.0, 0.0, 1.0)
    return _resample(ng, line.outputs["Curve"], segments, (location[0] + 200, location[1]))


def _bend(ng, curve, gnarl, lean, seed_f, scale, location):
    """Offset every point by a noise field in XY, weighted by its own spline factor.

    The weight is why a branch stays attached: factor is 0 at the base, so the base point does not
    move and remains coincident with the parent point it was instanced on. `lean` adds a steady pull
    in +X on the same weighting, which is what makes a trunk lean rather than wander.
    """
    lx, ly = location
    factor, _ = _spline_parameter(ng, (lx, ly + 320))
    weight = math_node(ng, "POWER", factor, 1.5, (lx + 180, ly + 320))
    pos = position(ng, (lx, ly - 200))
    # Two decorrelated samples for X and Y. The offset vector is built from one noise per axis
    # rather than a vector noise so `Gnarl` scales an amplitude in metres.
    nx = noise_field(ng, pos, scale, seed=seed_f, location=(lx + 180, ly - 120))
    shifted = ng.nodes.new("ShaderNodeVectorMath")
    shifted.operation = "ADD"
    shifted.location = (lx + 180, ly - 360)
    ng.links.new(pos, shifted.inputs[0])
    shifted.inputs[1].default_value = (13.7, 4.2, 9.1)
    ny = noise_field(ng, shifted.outputs["Vector"], scale, seed=seed_f,
                     location=(lx + 360, ly - 360))
    amp = math_node(ng, "MULTIPLY", gnarl, weight, (lx + 540, ly + 200))
    ox = math_node(ng, "MULTIPLY", math_node(ng, "SUBTRACT", nx, 0.5, (lx + 540, ly - 120)),
                   amp, (lx + 720, ly - 120))
    oy = math_node(ng, "MULTIPLY", math_node(ng, "SUBTRACT", ny, 0.5, (lx + 540, ly - 360)),
                   amp, (lx + 720, ly - 360))
    ox = math_node(ng, "ADD", ox, math_node(ng, "MULTIPLY", lean, weight, (lx + 720, ly + 60)),
                   (lx + 900, ly - 120))
    offset = _combine(ng, ox, oy, 0.0, (lx + 1080, ly - 200))
    node = ng.nodes.new("GeometryNodeSetPosition")
    node.location = (lx + 1260, ly)
    ng.links.new(curve, node.inputs["Geometry"])
    ng.links.new(offset, node.inputs["Offset"])
    # How far this point was displaced, kept as `bbt_fol_off`. It is what makes the attached-base
    # invariant checkable rather than assumed: a gate reads the offset at the points whose spline
    # factor is 0 and it has to be exactly zero. A detached tree still renders, so "looks fine" is
    # not evidence, and this is the cheapest thing that is.
    magnitude = ng.nodes.new("ShaderNodeVectorMath")
    magnitude.operation = "LENGTH"
    magnitude.location = (lx + 1260, ly - 300)
    ng.links.new(offset, magnitude.inputs[0])
    return _store(ng, node.outputs["Geometry"], "bbt_fol_off", magnitude.outputs["Value"],
                  "FLOAT", "POINT", (lx + 1440, ly))


def _taper(ng, curve, base_radius, taper, location):
    """Radius falling from `base_radius` at the base to `base_radius * (1 - taper)` at the tip."""
    lx, ly = location
    factor, _ = _spline_parameter(ng, (lx, ly + 200))
    shrink = math_node(ng, "SUBTRACT", 1.0,
                       math_node(ng, "MULTIPLY", taper, factor, (lx + 180, ly + 200)),
                       (lx + 360, ly + 200))
    radius = math_node(ng, "MAXIMUM",
                       math_node(ng, "MULTIPLY", base_radius, shrink, (lx + 540, ly + 100)),
                       MIN_RADIUS, (lx + 720, ly + 100))
    node = ng.nodes.new("GeometryNodeSetCurveRadius")
    node.location = (lx + 900, ly)
    ng.links.new(curve, node.inputs["Curve"])
    ng.links.new(radius, node.inputs["Radius"])
    return node.outputs["Curve"]


def _tag(ng, curve, level, location):
    """Write the per-curve level, the curve's own length per point, and the tip flag.

    `bbt_fol_plen` is stored on the POINT domain deliberately. The next level reads it after a trim
    and a resample, and a curve-domain read (`Spline Length`) does not survive either -- the trimmed
    copy's length is the trimmed length, not the parent's, which would shorten every branch by the
    Start fraction. A constant stored per point interpolates to itself, so it arrives intact.
    """
    lx, ly = location
    length = ng.nodes.new("GeometryNodeSplineLength")
    length.location = (lx, ly + 200)
    out = _store(ng, curve, "bbt_fol_plen", length.outputs["Length"], "FLOAT", "POINT", (lx + 200, ly))
    out = _store(ng, out, "bbt_fol_level", int(level), "INT", "CURVE", (lx + 400, ly))
    # The tip flag: 1 at the last point of each spline, 0 elsewhere. F2 instances leaf cards on it,
    # and it is written per level so a shrub with two levels still has tips.
    factor, _ = _spline_parameter(ng, (lx + 400, ly - 260))
    tip = math_node(ng, "GREATER_THAN", factor, 0.999, (lx + 580, ly - 260))
    out = _store(ng, out, "bbt_fol_tip", tip, "FLOAT", "POINT", (lx + 760, ly))
    # The along-branch coordinate, 0 at the base and 1 at the tip. Written because the SHADER needs
    # it and cannot compute it: bark UVs run along the limb, F4's wind sway has to fall off to zero
    # at the base or a branch pivots out of its socket, and F2 places cards by it.
    return _store(ng, out, "bbt_fol_t", factor, "FLOAT", "POINT", (lx + 940, ly))


def _branch_rotation(ng, angle_deg, phyllo_deg, seed_f, location):
    """The rotation that takes a unit +Z child curve and points it out of its parent.

    Three steps, and the ORDER is the whole thing. Align local Z to the parent's tangent; spin around
    that axis by the golden-angle multiple for this child's index; only then tilt away from it. Tilt
    before spin and every branch on a whorl leaves in the same direction, which reads as a flat fan
    -- which is what a generated crown already looks like and the reason this recipe exists.
    """
    lx, ly = location
    tangent = ng.nodes.new("GeometryNodeInputTangent")
    tangent.location = (lx, ly)
    align = ng.nodes.new("FunctionNodeAlignRotationToVector")
    align.axis = "Z"
    align.location = (lx + 200, ly)
    align.inputs["Factor"].default_value = 1.0
    ng.links.new(tangent.outputs["Tangent"], align.inputs["Vector"])

    _, index = _spline_parameter(ng, (lx, ly - 220))
    phyllo = math_node(ng, "MULTIPLY", math_node(ng, "MULTIPLY", index, phyllo_deg,
                                                 (lx + 200, ly - 220)),
                       _DEG, (lx + 380, ly - 220))
    spin = ng.nodes.new("FunctionNodeRotateRotation")
    spin.rotation_space = "LOCAL"
    spin.location = (lx + 560, ly)
    ng.links.new(align.outputs["Rotation"], spin.inputs["Rotation"])
    ng.links.new(_combine(ng, 0.0, 0.0, phyllo, (lx + 380, ly - 400)), spin.inputs["Rotate By"])

    # A per-branch jitter on the angle, keyed by the point index so it is stable across a rebuild.
    jitter = ng.nodes.new("FunctionNodeRandomValue")
    jitter.data_type = "FLOAT"
    jitter.location = (lx + 380, ly - 620)
    jitter.inputs["Min"].default_value = -9.0
    jitter.inputs["Max"].default_value = 9.0
    ng.links.new(seed_f, jitter.inputs["Seed"])
    idx = ng.nodes.new("GeometryNodeInputIndex")
    idx.location = (lx + 200, ly - 620)
    ng.links.new(idx.outputs["Index"], jitter.inputs["ID"])
    angle = math_node(ng, "MULTIPLY",
                      math_node(ng, "ADD", angle_deg, jitter.outputs["Value"],
                                (lx + 560, ly - 620)),
                      _DEG, (lx + 740, ly - 620))
    tilt = ng.nodes.new("FunctionNodeRotateRotation")
    tilt.rotation_space = "LOCAL"
    tilt.location = (lx + 920, ly)
    ng.links.new(spin.outputs["Rotation"], tilt.inputs["Rotation"])
    ng.links.new(_combine(ng, angle, 0.0, 0.0, (lx + 740, ly - 400)), tilt.inputs["Rotate By"])
    return tilt.outputs["Rotation"], idx.outputs["Index"]


def _trim_upper(ng, curve, start, location):
    """The parent from `start` (a factor) to its tip. Branches grow off the upper stretch: a conifer
    with limbs down to the ground reads as a bush, and the bare stretch is the species tell."""
    node = ng.nodes.new("GeometryNodeTrimCurve")
    node.mode = "FACTOR"
    node.location = location
    ng.links.new(curve, node.inputs["Curve"])
    # Positional: FACTOR and LENGTH mode each contribute a socket pair, both named Start/End, so
    # by-name access would silently reach the LENGTH pair.
    ng.links.new(start, node.inputs[2])
    node.inputs[3].default_value = 1.0
    return node.outputs["Curve"]


@recipe("foliage")
def build(ng, out, params: dict):
    levels = max(1, min(MAX_LEVELS, int(params.get("levels", 3))))
    profile = max(3, min(24, int(params.get("profile_segments", 6))))

    gi = ng.nodes.new("NodeGroupInput")
    gi.location = (-2200, 0)

    add_input(ng, "Seed", "NodeSocketInt", int(params.get("seed", 0)), 0)
    add_input(ng, "Height", "NodeSocketFloat", float(params.get("height", 18.0)), 0.1)
    add_input(ng, "Segments", "NodeSocketInt", int(params.get("segments", 14)), 2, 128)
    add_input(ng, "Trunk Radius", "NodeSocketFloat", float(params.get("trunk_radius", 0.45)), 0.005)
    add_input(ng, "Taper", "NodeSocketFloat", float(params.get("taper", 0.85)), 0.0, 0.99)
    add_input(ng, "Lean", "NodeSocketFloat", float(params.get("lean", 0.4)))
    add_input(ng, "Gnarl", "NodeSocketFloat", float(params.get("gnarl", 0.9)), 0.0)
    add_input(ng, "Branch Segments", "NodeSocketInt", int(params.get("branch_segments", 6)), 2, 64)
    for level in range(1, levels + 1):
        count, angle, length, radius, phyllo, start = _LEVEL_DEFAULTS[level - 1]
        prefix = f"L{level} "
        add_input(ng, prefix + "Branches", "NodeSocketInt",
                  int(params.get(f"l{level}_branches", count)), 1, 64)
        add_input(ng, prefix + "Angle", "NodeSocketFloat",
                  float(params.get(f"l{level}_angle", angle)), 0.0, 170.0)
        add_input(ng, prefix + "Length", "NodeSocketFloat",
                  float(params.get(f"l{level}_length", length)), 0.01, 2.0)
        add_input(ng, prefix + "Radius", "NodeSocketFloat",
                  float(params.get(f"l{level}_radius", radius)), 0.01, 1.0)
        add_input(ng, prefix + "Phyllotaxy", "NodeSocketFloat",
                  float(params.get(f"l{level}_phyllotaxy", phyllo)), 0.0, 360.0)
        add_input(ng, prefix + "Start", "NodeSocketFloat",
                  float(params.get(f"l{level}_start", start)), 0.0, 0.95)
    add_input(ng, "Shade Smooth", "NodeSocketBool", bool(params.get("shade_smooth", True)))

    seed_f = _f(ng, gi.outputs["Seed"], (-2000, -700))

    # -- Level 0: the trunk ----------------------------------------------------------------------
    line = ng.nodes.new("GeometryNodeCurvePrimitiveLine")
    line.mode = "POINTS"
    line.location = (-2000, 300)
    line.inputs["Start"].default_value = (0.0, 0.0, 0.0)
    ng.links.new(_combine(ng, 0.0, 0.0, gi.outputs["Height"], (-2200, 460)), line.inputs["End"])
    trunk = _resample(ng, line.outputs["Curve"], gi.outputs["Segments"], (-1800, 300))
    trunk = _bend(ng, trunk, gi.outputs["Gnarl"], gi.outputs["Lean"], seed_f, 0.35, (-1600, 300))
    trunk = _taper(ng, trunk, gi.outputs["Trunk Radius"], gi.outputs["Taper"], (-200, 300))
    trunk = _tag(ng, trunk, 0, (800, 300))

    parts = [trunk]
    parent = trunk
    # The radius a level's branches start at, as a running product of the per-level ratios. Computed
    # here rather than read off the parent geometry: a Set Curve Radius is not readable downstream of
    # a realize without another stored attribute, and the ratios are uniform per level anyway.
    base_radius = gi.outputs["Trunk Radius"]

    for level in range(1, levels + 1):
        row = -900 * level
        prefix = f"L{level} "
        upper = _trim_upper(ng, parent, gi.outputs[prefix + "Start"], (-2000, row))
        points = _resample(ng, upper, gi.outputs[prefix + "Branches"], (-1800, row))
        template = _unit_line(ng, gi.outputs["Branch Segments"], (-1800, row - 400))

        level_seed = math_node(ng, "ADD", seed_f, float(level * 977), (-1600, row - 700))
        rotation, index = _branch_rotation(ng, gi.outputs[prefix + "Angle"],
                                           gi.outputs[prefix + "Phyllotaxy"], level_seed,
                                           (-1400, row - 200))

        # Length as a fraction of the PARENT's length, read back off the stored attribute, with a
        # per-branch random spread so a whorl is not a set of identical spokes.
        plen = _named(ng, "bbt_fol_plen", "FLOAT", (-1400, row + 260))
        spread = ng.nodes.new("FunctionNodeRandomValue")
        spread.data_type = "FLOAT"
        spread.location = (-1400, row + 420)
        spread.inputs["Min"].default_value = 0.78
        spread.inputs["Max"].default_value = 1.22
        ng.links.new(level_seed, spread.inputs["Seed"])
        ng.links.new(index, spread.inputs["ID"])
        scale = math_node(ng, "MULTIPLY",
                          math_node(ng, "MULTIPLY", plen, gi.outputs[prefix + "Length"],
                                    (-1200, row + 260)),
                          spread.outputs["Value"], (-1020, row + 260))

        instance = ng.nodes.new("GeometryNodeInstanceOnPoints")
        instance.location = (-500, row)
        ng.links.new(points, instance.inputs["Points"])
        ng.links.new(template, instance.inputs["Instance"])
        ng.links.new(rotation, instance.inputs["Rotation"])
        ng.links.new(_combine(ng, scale, scale, scale, (-800, row + 200)),
                     instance.inputs["Scale"])
        realize = ng.nodes.new("GeometryNodeRealizeInstances")
        realize.location = (-300, row)
        ng.links.new(instance.outputs["Instances"], realize.inputs["Geometry"])

        # Bend AFTER the realize, not on the template: one gnarled template instanced N times gives
        # N identical branches, and the point of a stand of trees is that no two limbs match. The
        # noise reads realized world position, so every branch samples a different part of the field.
        kids = _bend(ng, realize.outputs["Geometry"], gi.outputs["Gnarl"], 0.0, level_seed, 0.6,
                     (-100, row))
        base_radius = math_node(ng, "MULTIPLY", base_radius, gi.outputs[prefix + "Radius"],
                                (1300, row + 400))
        kids = _taper(ng, kids, base_radius, gi.outputs["Taper"], (1500, row))
        kids = _tag(ng, kids, level, (2500, row))
        parts.append(kids)
        parent = kids

    # -- Sweep the whole skeleton in one pass -----------------------------------------------------
    join = ng.nodes.new("GeometryNodeJoinGeometry")
    join.location = (3400, 0)
    # Reversed so the trunk is the LAST link and therefore the first spline: a stable spline order
    # is what lets a gate assert per-level counts, and Join Geometry prepends.
    for part in reversed(parts):
        ng.links.new(part, join.inputs["Geometry"])

    # Skeleton Only: emit the curves and skip the sweep. Structural, because it changes what kind of
    # geometry comes out. Worth having beyond testing -- tuning branch structure is a much faster loop
    # without paying for the tube mesh on every slider drag, and it is the only view in which a
    # detached branch is obvious rather than hidden inside a trunk.
    if bool(params.get("skeleton", False)):
        ng.links.new(join.outputs["Geometry"], out.inputs["Geometry"])
        return

    circle = ng.nodes.new("GeometryNodeCurvePrimitiveCircle")
    circle.mode = "RADIUS"
    circle.location = (3400, -400)
    circle.inputs["Resolution"].default_value = profile
    circle.inputs["Radius"].default_value = 1.0

    to_mesh = ng.nodes.new("GeometryNodeCurveToMesh")
    to_mesh.location = (3600, 0)
    ng.links.new(join.outputs["Geometry"], to_mesh.inputs["Curve"])
    ng.links.new(circle.outputs["Curve"], to_mesh.inputs["Profile Curve"])
    to_mesh.inputs["Fill Caps"].default_value = False

    smooth = ng.nodes.new("GeometryNodeSetShadeSmooth")
    smooth.location = (3800, 0)
    ng.links.new(to_mesh.outputs["Mesh"], smooth.inputs["Mesh"])
    ng.links.new(gi.outputs["Shade Smooth"], smooth.inputs["Shade Smooth"])
    ng.links.new(smooth.outputs["Mesh"], out.inputs["Geometry"])
