"""foliage: a procedural tree or shrub grown from a skeleton, swept to mesh, with leaf cards on
its tips (BobFoliage F1 + F2).

docs/FOLIAGE.md. The geometry is entirely procedural and image-to-3D supplies none of it, for one
structural reason: branches attach to POINTS ON A CURVE with a tangent and a radius, and a generated
mesh has no skeleton to offer, so nothing can grow from one. Generation's job here is the two texture
sets (bark, and the leaf atlas the cards read), never the shape.

How it grows. Level 0 is a trunk: a vertical line resampled to `Segments`, bent by a noise field
whose amplitude rises up the trunk (`Gnarl`) plus a steady `Lean`, with the radius tapering from
`Trunk Radius` toward the tip. Every level after that repeats one step:

    trim the parent to its upper `L<n> Start` fraction -> resample to `L<n> Branches` points
    -> instance a unit +Z curve on those points, rotated to leave the parent at `L<n> Angle`
    and spun around it by `L<n> Phyllotaxy` per index -> realize -> bend and taper it

so a level is data, not code, and `levels` is how many times the step is built. There is no recursion
primitive in Geometry Nodes; this is the fixed stack every GN tree generator uses instead. Then the
whole skeleton is swept once, and the cards are instanced on its tips.

Three invariants worth stating, because all three fail silently when broken:

- **A branch base never moves.** The bend offset is weighted by the branch's own spline factor, which
  is 0 at its base, so the point that coincides with its parent stays coincident. Weight it any other
  way and the tree comes apart into floating sticks that still render.
- **A parent's length reaches its children.** Scale has to be a fraction of the PARENT's length or a
  level-3 twig is as long as the trunk. `SplineLength` is a curve-domain read that cannot survive the
  trim and resample, so the length is stored as the point attribute `bbt_fol_plen` first: constants
  interpolate to themselves, so it arrives intact at every attachment point.
- **A branch is thinner than the parent it grows from, WHERE it grows from it.** F1 computed that as
  a running product of the per-level Radius ratios, which is exact only while the ratios are uniform
  and the parent does not taper -- and species presets are exactly what breaks both. The radius is
  now read off the parent at the attachment point (`bbt_fol_rad`, stored after the taper and carried
  by the same interpolation `bbt_fol_plen` uses), so a limb low on a thick trunk is thicker than one
  at the top and a preset can vary the ratios freely.

Attributes written, and who reads them: `bbt_fol_plen` (the curve's own length, per point) and
`bbt_fol_rad` (its radius after the taper) feed the next level; `bbt_fol_level` (per curve, 0 for the
trunk) and `bbt_fol_off` (how far the bend moved this point) are what the gate measures structure
with; `bbt_fol_t` (0 at the base, 1 at the tip) is the bark V coordinate and F4's wind falloff;
`bbt_fol_tip` and `bbt_fol_tan` place and aim the cards; `bbt_fol_leaf` (per face) is what tells the
two Set Material nodes which faces are cards; `bbt_fol_cell` records which atlas cell each card drew.

UVs and materials (docs/FOLIAGE.md 2.7). `Curve to Mesh` creates neither, so both are built here.
The swept mesh's V is `bbt_fol_t` scaled to METRES along the limb and its U is the profile circle's
own spline parameter scaled by the local circumference, so a twig and a trunk carry bark at the same
grain instead of the twig getting the whole texture. The cards' UV is their quad's own 0..1 UV pushed
into one cell of the atlas grid. Both write `UVMap` on the corner domain, which is what makes them a
real UV layer rather than a float2 nobody reads.

Params: seed, levels (1-4), segments, profile_segments, the shape values below, and two texture-set
names (`bark_set`, `atlas`). Structural (a rebuild): levels, profile_segments, and the two set names.
Everything else is a live modifier knob, so tuning a tree is a slider drag and only changing its
depth or its textures costs a rebuild.
"""

import math

import bpy

from ..blocks import math_node, noise_field, position
from ..scaffold import add_input
from . import recipe

MAX_LEVELS = 4          # four is a tree; the fifth level is twigs no card resolution can show
MIN_RADIUS = 0.004      # a curve radius of 0 collapses the sweep into a degenerate zero-area tube
_DEG = 0.017453292519943295

DEFAULT_ATLAS = "leaf_atlas_blockout"   # ships in the block-out pack, so a tree never waits on ComfyUI

# How far a limb wanders per unit of `Gnarl`, as a fraction of its OWN length. Chosen so the knob
# keeps the numbers F1 was measured at (Gnarl 0.9 on a 20 m trunk is still +/- 0.45 m) while the
# same value now means the same SHAPE at any scale. See `_bend`.
GNARL_SPAN = 0.05

# Per-level defaults, index 0 = level 1 (the first branches off the trunk). A NARROW CONIFER: F1's
# defaults grew a 13 m crown on a 20 m trunk, which is a spreading broadleaf and the opposite of the
# redwood that started this track. The lever is `length` -- a level-1 branch is a fraction of the
# trunk's length, so 0.42 was a 8.4 m arm. A conifer's lowest limbs are nearer a sixth of its height.
# These are the floor for a bare build; a real species comes from a preset (assets.foliage_species).
_LEVEL_DEFAULTS = (
    # branches, angle, length, radius, phyllotaxy, start
    (16, 78.0, 0.115, 0.26, 137.5, 0.26),
    (5, 54.0, 0.30, 0.38, 137.5, 0.20),
    (4, 47.0, 0.38, 0.45, 137.5, 0.16),
    (3, 42.0, 0.46, 0.50, 137.5, 0.15),
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


def _vec(ng, op, a, b=None, location=(0, 0)):
    """A Vector Math node. `a`/`b` are each a socket or a 3-tuple. Returns the Vector output."""
    node = ng.nodes.new("ShaderNodeVectorMath")
    node.operation = op
    node.location = location
    for i, value in enumerate((a, b)):
        if value is None:
            continue
        if isinstance(value, (tuple, list)):
            node.inputs[i].default_value = value
        else:
            ng.links.new(value, node.inputs[i])
    return node.outputs["Vector"]


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

    Both amplitudes are a fraction of the CURVE'S OWN LENGTH, not metres. F1 had them in metres,
    which is invisible on a 20 m trunk and destroys anything small: measured, a 0.10 m grass stem at
    the default Gnarl of 0.6 was displaced 0.3 m, so the tuft came back 1.7 m tall and 2 m wide.
    Species presets are the whole reason that matters -- a shape description has to survive being
    applied at a different size, or "the same shrub, waist high" is a different shrub. Reading
    `Spline Length` here works for both callers: the trunk is one spline, and the branches are
    bent after their realize, so each is its own spline with its own length.
    """
    lx, ly = location
    factor, _ = _spline_parameter(ng, (lx, ly + 320))
    weight = math_node(ng, "POWER", factor, 1.5, (lx + 180, ly + 320))
    length = ng.nodes.new("GeometryNodeSplineLength")
    length.location = (lx, ly + 480)
    pos = position(ng, (lx, ly - 200))
    # Two decorrelated samples for X and Y. The offset vector is built from one noise per axis
    # rather than a vector noise so `Gnarl` scales one amplitude rather than a direction.
    nx = noise_field(ng, pos, scale, seed=seed_f, location=(lx + 180, ly - 120))
    shifted = ng.nodes.new("ShaderNodeVectorMath")
    shifted.operation = "ADD"
    shifted.location = (lx + 180, ly - 360)
    ng.links.new(pos, shifted.inputs[0])
    shifted.inputs[1].default_value = (13.7, 4.2, 9.1)
    ny = noise_field(ng, shifted.outputs["Vector"], scale, seed=seed_f,
                     location=(lx + 360, ly - 360))
    # GNARL_SPAN keeps the knob's numbers where F1 left them: at Gnarl 0.9 on a 20 m trunk this is
    # the same +/- 0.45 m the F1 measurements were taken at, so a tuned tree does not change shape.
    span = math_node(ng, "MULTIPLY", length.outputs["Length"], GNARL_SPAN, (lx + 180, ly + 480))
    amp = math_node(ng, "MULTIPLY", math_node(ng, "MULTIPLY", gnarl, span, (lx + 360, ly + 320)),
                    weight, (lx + 540, ly + 200))
    ox = math_node(ng, "MULTIPLY", math_node(ng, "SUBTRACT", nx, 0.5, (lx + 540, ly - 120)),
                   amp, (lx + 720, ly - 120))
    oy = math_node(ng, "MULTIPLY", math_node(ng, "SUBTRACT", ny, 0.5, (lx + 540, ly - 360)),
                   amp, (lx + 720, ly - 360))
    lean_amp = math_node(ng, "MULTIPLY", math_node(ng, "MULTIPLY", lean, span, (lx + 360, ly + 60)),
                         weight, (lx + 540, ly + 60))
    ox = math_node(ng, "ADD", ox, lean_amp, (lx + 900, ly - 120))
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
    """Write everything a later stage reads off this level: length, radius, level, tip, t, tangent.

    Called AFTER `_taper`, which is load-bearing for `bbt_fol_rad`: the point of that attribute is
    the radius the parent ACTUALLY has where a child attaches, so it has to be read from the curve
    once the taper has set it, not from the level's base value.

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
    # The tip flag: 1 at the last point of each spline, 0 elsewhere. The leaf cards instance on it,
    # and it is written per level so a shrub with two levels still has tips.
    factor, _ = _spline_parameter(ng, (lx + 400, ly - 260))
    tip = math_node(ng, "GREATER_THAN", factor, 0.999, (lx + 580, ly - 260))
    out = _store(ng, out, "bbt_fol_tip", tip, "FLOAT", "POINT", (lx + 760, ly))
    # The along-branch coordinate, 0 at the base and 1 at the tip. Written because the SHADER needs
    # it and cannot compute it: bark UVs run along the limb, F4's wind sway has to fall off to zero
    # at the base or a branch pivots out of its socket, and the cards are placed by it.
    out = _store(ng, out, "bbt_fol_t", factor, "FLOAT", "POINT", (lx + 940, ly))
    # The tapered radius, per point. Read by the NEXT level for its own base radius, and by the bark
    # UV for the local circumference. A Set Curve Radius is not readable downstream of a realize, so
    # like the length it has to become a stored attribute to survive the trim/resample/instance trip.
    radius = ng.nodes.new("GeometryNodeInputRadius")
    radius.location = (lx + 940, ly - 260)
    out = _store(ng, out, "bbt_fol_rad", radius.outputs["Radius"], "FLOAT", "POINT", (lx + 1120, ly))
    # The unit tangent, per point. At a tip it is the direction the branch was heading, which is the
    # axis a leaf spray fans around. A curve field, so it has to be stored before the sweep.
    tangent = ng.nodes.new("GeometryNodeInputTangent")
    tangent.location = (lx + 1120, ly - 260)
    return _store(ng, out, "bbt_fol_tan", tangent.outputs["Tangent"], "FLOAT_VECTOR", "POINT",
                  (lx + 1300, ly))


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


def _card_quad(ng, gi, location):
    """One leaf card: a quad standing on the origin and growing along +Z, carrying its own 0..1 UV.

    Two triangles, per docs/FOLIAGE.md 2.3 -- the density of a canopy comes from the number of tips,
    not from geometry per card. It grows along +Z and its base sits ON the origin (the Grid is
    centred, so it is lifted by half its length) because the point it will be instanced on is the
    branch TIP: a centred card would bury half of itself in the twig it hangs from.

    The Grid's own `UV Map` output is stored as `bbt_fol_cuv` rather than written straight to a UV
    layer, because the atlas cell is not known yet -- it is picked per card, after instancing. This
    is the card's LOCAL 0..1 coordinate, which `_card_uv` later pushes into one cell of the grid.
    """
    lx, ly = location
    grid = ng.nodes.new("GeometryNodeMeshGrid")
    grid.location = (lx, ly)
    grid.inputs["Vertices X"].default_value = 2
    grid.inputs["Vertices Y"].default_value = 2
    ng.links.new(math_node(ng, "MULTIPLY", gi.outputs["Card Size"], gi.outputs["Card Width"],
                           (lx - 200, ly + 200)), grid.inputs["Size X"])
    ng.links.new(gi.outputs["Card Size"], grid.inputs["Size Y"])
    uv = _store(ng, grid.outputs["Mesh"], "bbt_fol_cuv", grid.outputs["UV Map"],
                "FLOAT_VECTOR", "POINT", (lx + 200, ly))
    # Swing the quad out of XY into XZ and stand it on the origin, so its long axis is +Z -- the
    # same axis `Align Rotation to Vector` steers for the branches, which is what lets the card
    # rotation below reuse the branch rotation's proven align / spin / tilt order.
    lift = ng.nodes.new("GeometryNodeTransform")
    lift.location = (lx + 400, ly)
    ng.links.new(uv, lift.inputs["Geometry"])
    lift.inputs["Rotation"].default_value = (math.pi / 2.0, 0.0, 0.0)
    ng.links.new(_combine(ng, 0.0, 0.0,
                          math_node(ng, "MULTIPLY", gi.outputs["Card Size"], 0.5, (lx + 200, ly - 260)),
                          (lx + 380, ly - 260)),
                 lift.inputs["Translation"])
    return lift.outputs["Geometry"]


def _card_axis(ng, droop, location):
    """The axis a spray fans around: the tip's own tangent, pulled toward straight down by `Droop`.

    Droop as a direction BLEND rather than an extra rotation is what makes it read as weight: at 0
    the spray continues the branch, at 1 it hangs vertically whatever the branch was doing, and in
    between every tip droops by the same amount regardless of which way it was pointing. An added
    rotation would instead droop an upward tip and lift a downward one.
    """
    lx, ly = location
    tangent = _named(ng, "bbt_fol_tan", "FLOAT_VECTOR", (lx, ly))
    pulled = ng.nodes.new("ShaderNodeVectorMath")
    pulled.operation = "SCALE"
    pulled.location = (lx + 200, ly)
    ng.links.new(tangent, pulled.inputs[0])
    ng.links.new(math_node(ng, "SUBTRACT", 1.0, droop, (lx, ly - 200)), pulled.inputs["Scale"])
    down = _combine(ng, 0.0, 0.0, math_node(ng, "MULTIPLY", droop, -1.0, (lx + 200, ly - 260)),
                    (lx + 380, ly - 260))
    return _vec(ng, "NORMALIZE",
                _vec(ng, "ADD", pulled.outputs["Vector"], down, (lx + 560, ly)), None, (lx + 740, ly))


def _card_rotation(ng, gi, seed_f, index, location):
    """The rotation that aims one card out of a tip: align, spin, tilt -- the branch order exactly.

    Align the card's +Z to the drooped tip axis; spin it around that axis by its share of a full
    turn so N cards make a spray rather than a stack; only then tilt it away by `Spread`. Tilting
    before spinning fans every card in the same direction, which is a flat fan, which is the shape
    a generated crown already comes back as and the whole reason this recipe exists.

    The spin is deterministic (index / count of a full turn) and the JITTER is what `Spread` adds on
    top of it, so the knob reads as one thing -- how wide and how loose the spray is -- and a spray
    at Spread 0 is a tidy radial whorl rather than a random one.
    """
    lx, ly = location
    align = ng.nodes.new("FunctionNodeAlignRotationToVector")
    align.axis = "Z"
    align.location = (lx + 200, ly)
    align.inputs["Factor"].default_value = 1.0
    ng.links.new(_card_axis(ng, gi.outputs["Droop"], (lx - 800, ly)), align.inputs["Vector"])

    turn = math_node(ng, "DIVIDE", 2.0 * math.pi,
                     math_node(ng, "MAXIMUM", _f(ng, gi.outputs["Cards"], (lx - 400, ly - 200)),
                               1.0, (lx - 200, ly - 200)),
                     (lx, ly - 200))
    spin_by = math_node(ng, "MULTIPLY", index, turn, (lx + 200, ly - 200))
    spin = ng.nodes.new("FunctionNodeRotateRotation")
    spin.rotation_space = "LOCAL"
    spin.location = (lx + 560, ly)
    ng.links.new(align.outputs["Rotation"], spin.inputs["Rotation"])
    ng.links.new(_combine(ng, 0.0, 0.0, spin_by, (lx + 380, ly - 200)), spin.inputs["Rotate By"])

    # Tilt away from the axis by Spread, plus up to that much again at random per card.
    jitter = ng.nodes.new("FunctionNodeRandomValue")
    jitter.data_type = "FLOAT"
    jitter.location = (lx + 200, ly - 480)
    jitter.inputs["Min"].default_value = -1.0
    jitter.inputs["Max"].default_value = 1.0
    ng.links.new(seed_f, jitter.inputs["Seed"])
    ng.links.new(index, jitter.inputs["ID"])
    spread = gi.outputs["Spread"]
    tilt_deg = math_node(ng, "ADD", spread,
                         math_node(ng, "MULTIPLY", spread, jitter.outputs["Value"],
                                   (lx + 380, ly - 480)),
                         (lx + 560, ly - 480))
    tilt = ng.nodes.new("FunctionNodeRotateRotation")
    tilt.rotation_space = "LOCAL"
    tilt.location = (lx + 920, ly)
    ng.links.new(spin.outputs["Rotation"], tilt.inputs["Rotation"])
    ng.links.new(_combine(ng, math_node(ng, "MULTIPLY", tilt_deg, _DEG, (lx + 740, ly - 480)),
                          0.0, 0.0, (lx + 740, ly - 300)),
                 tilt.inputs["Rotate By"])
    return tilt.outputs["Rotation"]


def _card_uv(ng, cards, gi, location):
    """Write the cards' `UVMap`: their own 0..1 quad UV pushed into one cell of the atlas grid.

    The cell index was drawn per card and stored as `bbt_fol_cell`, so it survived the instancing
    and is a plain attribute read here. Row-major over the grid, and v is measured from the BOTTOM
    of the image the way Blender's UV space is, so cell 0 is the bottom-left cell and the atlas is
    authored to match (tools/scripts/make_leaf_atlas.py).

    Stored on the CORNER domain, which is the whole difference between a UV layer and a float2 that
    nothing reads: a POINT-domain float2 named UVMap does not appear in `mesh.uv_layers` at all.
    """
    lx, ly = location
    cols = _f(ng, gi.outputs["Atlas Columns"], (lx, ly + 300))
    rows = _f(ng, gi.outputs["Atlas Rows"], (lx, ly + 200))
    cell = _f(ng, _named(ng, "bbt_fol_cell", "INT", (lx, ly - 100)), (lx + 200, ly - 100))
    col = math_node(ng, "MODULO", cell, cols, (lx + 400, ly - 100))
    row = math_node(ng, "FLOOR", math_node(ng, "DIVIDE", cell, cols, (lx + 400, ly - 300)),
                    None, (lx + 600, ly - 300))
    local = ng.nodes.new("ShaderNodeSeparateXYZ")
    local.location = (lx + 200, ly + 60)
    ng.links.new(_named(ng, "bbt_fol_cuv", "FLOAT_VECTOR", (lx, ly + 60)), local.inputs[0])
    u = math_node(ng, "DIVIDE", math_node(ng, "ADD", local.outputs["X"], col, (lx + 800, ly + 60)),
                  cols, (lx + 980, ly + 60))
    v = math_node(ng, "DIVIDE", math_node(ng, "ADD", local.outputs["Y"], row, (lx + 800, ly - 140)),
                  rows, (lx + 980, ly - 140))
    return _store(ng, cards, "UVMap", _combine(ng, u, v, 0.0, (lx + 1160, ly)),
                  "FLOAT2", "CORNER", (lx + 1340, ly))


def _cards(ng, skeleton, gi, seed_f, location):
    """Instance leaf cards on every branch tip of the skeleton. Returns a mesh socket.

    The route is tips -> a point cloud -> N duplicates of each point -> a card on each, and each hop
    exists for a reason:

    - the tips become a POINT CLOUD (instance a single point on the selection, then realize) because
      the tips are the last points of splines, and neither `Duplicate Elements` nor a per-tip index
      means anything useful on a curve's point domain,
    - `Duplicate Elements` gives `Cards` copies of each tip point, so a card is one instance on one
      point and every card can be aimed and textured on its own. Building an N-card cluster ONCE and
      instancing that would be cheaper and wrong for the same reason F1's bend runs after the
      realize: every tip would get the identical spray, down to the atlas cells,
    - the atlas cell is drawn HERE, on the duplicated cloud, where `Index` is unique per card, and
      stored so the UV stage downstream can read it off the realized mesh.

    `Cards` 0 empties the whole branch, which is the off switch: `Duplicate Elements` with an amount
    of 0 produces nothing and everything after it is a no-op on empty geometry.
    """
    lx, ly = location
    one = ng.nodes.new("GeometryNodePoints")
    one.location = (lx, ly - 300)
    one.inputs["Count"].default_value = 1

    seed_tips = ng.nodes.new("GeometryNodeInstanceOnPoints")
    seed_tips.location = (lx + 200, ly)
    ng.links.new(skeleton, seed_tips.inputs["Points"])
    ng.links.new(one.outputs["Points"], seed_tips.inputs["Instance"])
    ng.links.new(math_node(ng, "GREATER_THAN", _named(ng, "bbt_fol_tip", "FLOAT", (lx, ly + 200)),
                           0.5, (lx + 200, ly + 200)),
                 seed_tips.inputs["Selection"])
    tips = ng.nodes.new("GeometryNodeRealizeInstances")
    tips.location = (lx + 400, ly)
    ng.links.new(seed_tips.outputs["Instances"], tips.inputs["Geometry"])

    dup = ng.nodes.new("GeometryNodeDuplicateElements")
    dup.domain = "POINT"
    dup.location = (lx + 600, ly)
    ng.links.new(tips.outputs["Geometry"], dup.inputs["Geometry"])
    ng.links.new(gi.outputs["Cards"], dup.inputs["Amount"])

    index = ng.nodes.new("GeometryNodeInputIndex")
    index.location = (lx + 600, ly - 300)
    # The atlas cell, drawn per card. Max is INCLUSIVE on an integer Random Value, so the last cell
    # is cols*rows - 1; asking for cols*rows would sample a cell past the right edge of the atlas.
    cells = math_node(ng, "MULTIPLY", _f(ng, gi.outputs["Atlas Columns"], (lx + 600, ly - 480)),
                      _f(ng, gi.outputs["Atlas Rows"], (lx + 600, ly - 560)), (lx + 780, ly - 520))
    pick = ng.nodes.new("FunctionNodeRandomValue")
    pick.data_type = "INT"
    pick.location = (lx + 960, ly - 400)
    pick.inputs["Min"].default_value = 0
    ng.links.new(math_node(ng, "SUBTRACT", cells, 1.0, (lx + 780, ly - 640)), pick.inputs["Max"])
    ng.links.new(index.outputs["Index"], pick.inputs["ID"])
    ng.links.new(math_node(ng, "ADD", seed_f, 5417.0, (lx + 780, ly - 720)), pick.inputs["Seed"])
    placed = _store(ng, dup.outputs["Geometry"], "bbt_fol_cell", pick.outputs["Value"],
                    "INT", "POINT", (lx + 1160, ly))

    instance = ng.nodes.new("GeometryNodeInstanceOnPoints")
    instance.location = (lx + 1900, ly)
    ng.links.new(placed, instance.inputs["Points"])
    ng.links.new(_card_quad(ng, gi, (lx + 1000, ly + 500)), instance.inputs["Instance"])
    ng.links.new(_card_rotation(ng, gi, seed_f, index.outputs["Index"], (lx + 1360, ly - 900)),
                 instance.inputs["Rotation"])
    realize = ng.nodes.new("GeometryNodeRealizeInstances")
    realize.location = (lx + 2100, ly)
    ng.links.new(instance.outputs["Instances"], realize.inputs["Geometry"])

    cards = _card_uv(ng, realize.outputs["Geometry"], gi, (lx + 2300, ly))
    # Two attributes the cards INHERITED from the tips they grew on and must not keep. Every point
    # attribute on a tip rides through the duplicate and the instancing, so without this every card
    # vertex claims to be a branch tip (`bbt_fol_tip` 1) at the very end of a limb (`bbt_fol_t` 1).
    # Both are read downstream -- the tip flag is how anything finds the tips, and `bbt_fol_t` is
    # F4's wind falloff -- so leaving them is not cosmetic. `bbt_fol_t` is rewritten to the card's
    # OWN 0 at the base and 1 at the free end, which is what a card's sway has to fall off over or
    # it pivots out of the twig it hangs from.
    local = ng.nodes.new("ShaderNodeSeparateXYZ")
    local.location = (lx + 3700, ly - 300)
    ng.links.new(_named(ng, "bbt_fol_cuv", "FLOAT_VECTOR", (lx + 3520, ly - 300)), local.inputs[0])
    cards = _store(ng, cards, "bbt_fol_t", local.outputs["Y"], "FLOAT", "POINT", (lx + 3880, ly))
    cards = _store(ng, cards, "bbt_fol_tip", 0.0, "FLOAT", "POINT", (lx + 4060, ly))
    # The face flag the two Set Material nodes key off. On the FACE domain because that is the
    # domain a material selection is evaluated on; a point flag would have to interpolate, and a
    # face straddling the join between a card and a twig would then be neither.
    return _store(ng, cards, "bbt_fol_leaf", True, "BOOLEAN", "FACE", (lx + 4240, ly))


def _sweep_uv(ng, mesh, gi, location):
    """Write the swept mesh's `UVMap` in METRES, so bark reads at one grain over the whole tree.

    U runs around the tube: the profile circle's own spline parameter (stored as `bbt_fol_u` before
    the sweep, since `Curve to Mesh` carries a profile attribute around every ring) scaled by the
    local circumference. V runs along the limb: `bbt_fol_t` scaled by the limb's length. Both are
    then divided by `Bark Scale`, which is therefore metres per texture tile.

    Without the metres scaling a two-metre trunk and a twenty-centimetre twig each get exactly one
    tile of bark, so the twig's grain is ten times too coarse -- the single thing that makes a
    procedural trunk read as plastic.

    Known seam: the profile is cyclic, so its parameter runs 0 .. 1-1/n and the last quad of each
    ring wraps from nearly 1 back to 0, giving one column of reversed UV per limb. That is the
    ordinary cylindrical-unwrap seam; F3 owns bark and can decide whether to hide it.
    """
    lx, ly = location
    circumference = math_node(ng, "MULTIPLY", _named(ng, "bbt_fol_rad", "FLOAT", (lx, ly + 200)),
                              2.0 * math.pi, (lx + 200, ly + 200))
    scale = math_node(ng, "MAXIMUM", gi.outputs["Bark Scale"], 0.01, (lx, ly - 400))
    u = math_node(ng, "DIVIDE",
                  math_node(ng, "MULTIPLY", _named(ng, "bbt_fol_u", "FLOAT", (lx, ly + 60)),
                            circumference, (lx + 400, ly + 60)),
                  scale, (lx + 600, ly + 60))
    v = math_node(ng, "DIVIDE",
                  math_node(ng, "MULTIPLY", _named(ng, "bbt_fol_t", "FLOAT", (lx, ly - 140)),
                            _named(ng, "bbt_fol_plen", "FLOAT", (lx, ly - 220)), (lx + 400, ly - 140)),
                  scale, (lx + 600, ly - 140))
    return _store(ng, mesh, "UVMap", _combine(ng, u, v, 0.0, (lx + 800, ly)),
                  "FLOAT2", "CORNER", (lx + 980, ly))


def _materials(ng, mesh, bark, card, location):
    """Shade the tree: bark everywhere, then the card material over the faces flagged `bbt_fol_leaf`.

    Two Set Material nodes in SERIES on the joined mesh, not one per branch before the join. Both
    work, but this one is deterministic: Join Geometry prepends, so the per-branch version's slot
    order depends on link order, whereas here bark is always the first material added and the cards
    the second. Measured on Blender 5.2: the evaluated mesh comes back with slot 0 empty (the base
    mesh's own implicit no-material slot, which Set Material appends after and which no face
    references), bark at 1 and the cards at 2. An object's own material SLOTS are ignored by a
    GN-generated mesh entirely -- measured, and the reason a Set Material Index alone renders grey.

    A material of None is skipped rather than assigned, so a build whose texture set did not resolve
    still produces geometry instead of failing.
    """
    lx, ly = location
    out = mesh
    for i, (mat, selection) in enumerate(((bark, None), (card, "bbt_fol_leaf"))):
        if mat is None:
            continue
        node = ng.nodes.new("GeometryNodeSetMaterial")
        node.location = (lx + i * 220, ly)
        ng.links.new(out, node.inputs["Geometry"])
        node.inputs["Material"].default_value = mat
        if selection is not None:
            ng.links.new(_named(ng, selection, "BOOLEAN", (lx + i * 220, ly - 240)),
                         node.inputs["Selection"])
        out = node.outputs["Geometry"]
    return out


def _tree_materials(ng, params):
    """Get-or-create the tree's two BobShaders and return (bark, card).

    Built here rather than by a caller for the reason `particulates` builds its own: a recipe that
    needs a specific material is the thing that knows which one, and requiring a separate op first
    would mean a bare `build_geonodes(recipe="foliage")` -- over MCP, or in a headless .blend --
    came back grey. They are ordinary per-object BobShaders, so Shaders can retune them afterwards.

    The bark tint is set only on a genuinely fresh material: re-pressing Build must not revert a
    colour someone tuned, which is the same rule `_build_wrapper` follows for its own inputs.
    """
    from ...materials import _wrapper_name, foliage_card_material, surface_material

    base = ng.name.split(".")[0]
    bark_name = f"{base} Bark"
    fresh = _wrapper_name(bark_name) not in bpy.data.materials
    bark = surface_material(bark_name, texset_name=str(params.get("bark_set", "")), box=True)
    if fresh:
        node = bark.node_tree.nodes.get("Master")
        if node is not None:
            node.inputs["Base Color"].default_value = (0.19, 0.13, 0.09, 1.0)
            node.inputs["Roughness"].default_value = 0.85
            node.inputs["Canopy Snow"].default_value = 1.0  # a trunk is vertical; snow needs the hint
    card = foliage_card_material(f"{base} Leaf", atlas=str(params.get("atlas", DEFAULT_ATLAS)))
    return bark, card


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
    add_input(ng, "Lean", "NodeSocketFloat", float(params.get("lean", 0.15)))
    add_input(ng, "Gnarl", "NodeSocketFloat", float(params.get("gnarl", 0.5)), 0.0)
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
    # Leaf cards (F2). Live, because a canopy is tuned by eye and none of these changes the topology
    # of anything but the cards themselves. `Cards` 0 is the off switch and leaves a bare skeleton.
    add_input(ng, "Cards", "NodeSocketInt", int(params.get("cards", 5)), 0, 64)
    add_input(ng, "Card Size", "NodeSocketFloat", float(params.get("card_size", 0.70)), 0.0)
    add_input(ng, "Card Width", "NodeSocketFloat", float(params.get("card_width", 0.60)), 0.01, 4.0)
    add_input(ng, "Droop", "NodeSocketFloat", float(params.get("droop", 0.35)), 0.0, 1.0)
    add_input(ng, "Spread", "NodeSocketFloat", float(params.get("card_spread", 34.0)), 0.0, 90.0)
    add_input(ng, "Atlas Columns", "NodeSocketInt", int(params.get("atlas_cols", 2)), 1, 16)
    add_input(ng, "Atlas Rows", "NodeSocketInt", int(params.get("atlas_rows", 2)), 1, 16)
    add_input(ng, "Bark Scale", "NodeSocketFloat", float(params.get("bark_scale", 0.6)), 0.01)
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
        # The radius this branch starts at: the parent's ACTUAL radius where it attached, times this
        # level's ratio. `bbt_fol_rad` was stored after the parent's taper and rode here on the same
        # interpolation that carries `bbt_fol_plen`, so it is the parent's local thickness, not its
        # base thickness. F1 used a running product of the ratios instead, which agrees with this
        # only while every ratio is uniform AND the parent does not taper -- and species presets vary
        # the ratios per level, which is exactly what makes the product wrong.
        base_radius = math_node(ng, "MULTIPLY",
                                _named(ng, "bbt_fol_rad", "FLOAT", (1120, row + 400)),
                                gi.outputs[prefix + "Radius"], (1300, row + 400))
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
    # The profile's own spline parameter, stored BEFORE the sweep. Curve to Mesh carries a profile
    # attribute around every ring of the tube, which is the only way to get a coordinate that runs
    # AROUND the trunk; nothing downstream of the sweep can recover it from the mesh.
    profile_curve = _store(ng, circle.outputs["Curve"], "bbt_fol_u",
                           _spline_parameter(ng, (3400, -700))[0], "FLOAT", "POINT", (3600, -400))

    to_mesh = ng.nodes.new("GeometryNodeCurveToMesh")
    to_mesh.location = (3800, 0)
    ng.links.new(join.outputs["Geometry"], to_mesh.inputs["Curve"])
    ng.links.new(profile_curve, to_mesh.inputs["Profile Curve"])
    # The radius has to be handed to `Scale` EXPLICITLY. Blender 4.0 gave Curve to Mesh a Scale
    # input and stopped applying the curve's radius attribute implicitly, so F1's sweep -- which
    # only set the radius and never wired it -- came back as a uniform 1 m tube whatever Trunk
    # Radius, Taper and the per-level ratios said. Measured: every F1 tree's trunk and every twig
    # were the same 2 m across. `bbt_fol_rad` is the tapered radius this level actually has, which
    # is the same value the next level's base radius and the bark U circumference read.
    ng.links.new(_named(ng, "bbt_fol_rad", "FLOAT", (3600, -180)), to_mesh.inputs["Scale"])
    to_mesh.inputs["Fill Caps"].default_value = False

    smooth = ng.nodes.new("GeometryNodeSetShadeSmooth")
    smooth.location = (4000, 0)
    ng.links.new(to_mesh.outputs["Mesh"], smooth.inputs["Mesh"])
    ng.links.new(gi.outputs["Shade Smooth"], smooth.inputs["Shade Smooth"])
    swept = _sweep_uv(ng, smooth.outputs["Mesh"], gi, (4200, 0))

    # -- Leaf cards on the tips, then one material each ------------------------------------------
    # The cards read the SKELETON, not the swept mesh: a tip on the mesh is a ring of `profile`
    # vertices, so instancing there would put `profile` sprays on every twig. On the curve it is one
    # point carrying the tangent the spray fans around.
    cards = _cards(ng, join.outputs["Geometry"], gi, seed_f, (3400, -1600))
    # Cards are left FLAT-shaded: a two-triangle quad given a smoothed normal shades as a bent sheet
    # and catches light that a leaf silhouette should not.
    both = ng.nodes.new("GeometryNodeJoinGeometry")
    both.location = (7600, 0)
    ng.links.new(cards, both.inputs["Geometry"])
    ng.links.new(swept, both.inputs["Geometry"])

    bark_mat, card_mat = _tree_materials(ng, params)
    ng.links.new(_materials(ng, both.outputs["Geometry"], bark_mat, card_mat, (7800, 0)),
                 out.inputs["Geometry"])
