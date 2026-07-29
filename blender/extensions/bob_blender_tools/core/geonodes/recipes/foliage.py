"""foliage: a procedural tree or shrub grown from a skeleton, swept to mesh, with leaf cards on
its tips, moving in the world's wind (BobFoliage).

docs/FOLIAGE.md. The geometry is entirely procedural and image-to-3D supplies none of it, for one
structural reason: branches attach to POINTS ON A CURVE with a tangent and a radius, and a generated
mesh has no skeleton to offer, so nothing can grow from one. Generation's job here is the two
texture sets (bark, and the leaf atlas the cards read), never the shape.

How it grows. Level 0 is a trunk: a vertical line resampled to `Segments`, bent by a noise field
whose amplitude rises up the trunk (`Gnarl`) plus a steady `Lean`, with the radius tapering from
`Trunk Radius` toward the tip. Every level after that repeats one step:

    trim the parent to its upper `L<n> Start` fraction -> resample to `L<n> Branches` points ->
    instance a unit +Z curve on those points, rotated to leave the parent at `L<n> Angle` and spun
    around it by `L<n> Phyllotaxy` per index -> realize -> bend and taper it

so a level is data, not code, and `levels` is how many times the step is built. There is no
recursion primitive in Geometry Nodes; this is the fixed stack every GN tree generator uses instead.
Then the whole skeleton is swept once, and the cards are instanced on its tips.

Three invariants worth stating, because all three fail silently when broken:

- **A branch base never moves.** The bend offset is weighted by the branch's own spline factor, which
  is 0 at its base, so the point that coincides with its parent stays coincident. Weight it any
  other way and the tree comes apart into floating sticks that still render.
- **A parent's length reaches its children.** Scale has to be a fraction of the PARENT's length or a
  level-3 twig is as long as the trunk. `SplineLength` is a curve-domain read that cannot survive
  the trim and resample, so the length is stored as the point attribute `bbt_fol_plen` first:
  constants interpolate to themselves, so it arrives intact at every attachment point.
- **A branch is thinner than the parent it grows from, WHERE it grows from it.** An early version computed that as
  a running product of the per-level Radius ratios, which is exact only while the ratios are uniform
  and the parent does not taper -- and species presets are exactly what breaks both. The radius is
  now read off the parent at the attachment point (`bbt_fol_rad`, stored after the taper and carried
  by the same interpolation `bbt_fol_plen` uses), so a limb low on a thick trunk is thicker than one
  at the top and a preset can vary the ratios freely.

Attributes written, and who reads them: `bbt_fol_plen` (the curve's own length, per point) and
`bbt_fol_rad` (its radius after the taper) feed the next level; `bbt_fol_level` (per curve, 0 for
the trunk) and `bbt_fol_off` (how far the bend moved this point) are what the gate measures
structure with; `bbt_fol_t` (0 at the base, 1 at the tip) is the bark V coordinate and the wind
pass's wind falloff; `bbt_fol_tip` and `bbt_fol_tan` place and aim the cards; `bbt_fol_leaf` (per
face) is what tells the two Set Material nodes which faces are cards; `bbt_fol_cell` records which
atlas cell each card drew.

UVs and materials (docs/FOLIAGE.md 2.7). `Curve to Mesh` creates neither, so both are built here.
The swept mesh's V is `bbt_fol_t` scaled to METRES along the limb and its U is the profile circle's
own spline parameter scaled by the local circumference, so a twig and a trunk carry bark at the same
grain instead of the twig getting the whole texture. The cards' UV is their quad's own 0..1 UV
pushed into one cell of the atlas grid, whose size the atlas SET declares (`_atlas_grid`). Both
write `UVMap` on the corner domain, which is what makes them a real UV layer rather than a float2
nobody reads.

Two things about that were fixed which were invisible while nothing was textured. The cyclic
profile's U jumped from 1-1/n back to 0 on one quad of every ring, so a single column per limb
carried the whole texture reversed (`_unwrap_u`); and the bark material was assigned with BOX
projection, so the metres-based UV above reached nothing at all and the grain followed the world
axes instead of the limb (`_tree_materials`). Both rendered convincingly, which is the recurring
lesson of this track.

Wind (docs/FOLIAGE.md, wind and season). The whole tree leans and gusts downwind, weighted by the
square of each point's height, and the cards flutter on top of that about their own bases. Both are
driven by `Wind` / `Wind Direction`, which are ordinary live knobs that the world applier feeds from
`bbt_env`, so a tree responds to the shared weather with no per-tree animation and no keyframes;
Scene Time is the clock, so a render is deterministic. Three attributes make it work and each is
written where the information still exists: `bbt_fol_card` says which vertices are cards (the wood
has no such attribute, and Join Geometry zero-fills the side that lacks one -- measured);
`bbt_fol_anchor` is the TIP a card grew on, so a card rides its twig with exactly the twig's own
deflection and its base cannot part from it; and `bbt_fol_phase` is a per-card random that both the
flutter and the shader's autumn stagger read, so no two cards move or turn together.

Params: seed, levels (1-4), segments, profile_segments, the shape values below, and two texture-set
names (`bark_set`, `atlas`). Structural (a rebuild): levels, profile_segments, and the two set
names. Everything else is a live modifier knob, so tuning a tree is a slider drag and only changing
its depth or its textures costs a rebuild.
"""

import math

import bpy

from ... import assets
from ..blocks import math_node, noise_field, position
from ..scaffold import add_input
from . import recipe

MAX_LEVELS = 4          # four is a tree; the fifth level is twigs no card resolution can show
MIN_RADIUS = 0.004      # a curve radius of 0 collapses the sweep into a degenerate zero-area tube
_DEG = 0.017453292519943295

DEFAULT_ATLAS = "leaf_atlas_blockout"   # ships in the block-out pack, so a tree never waits on ComfyUI

# How far a limb wanders per unit of `Gnarl`, as a fraction of its OWN length. Chosen so the knob
# keeps the numbers the skeleton was first measured at (Gnarl 0.9 on a 20 m trunk is still +/- 0.45
# m) while the same value now means the same SHAPE at any scale. See `_bend`.
GNARL_SPAN = 0.05

# -- The wood shaping: the terms that stop a limb being a pipe
# --------------------------------------------------- Every one of these is INERT at its default,
# which is the point: every earlier measured a tree with none of them and those numbers still have
# to hold, so the shape they describe arrives through the species presets rather than through the
# recipe's floor. See docs/FOLIAGE.md 2.9.
#
# How far up a limb a base swell reaches, as a fraction of its length. Two constants because the two
# swells are different shapes: a root flare is the bottom twentieth of a 22 m bole, and a branch
# collar is a much larger fraction of a short branch. Squared inside `_taper`, so both are concave.
FLARE_SPAN = 0.05
COLLAR_SPAN = 0.18
# The wavelength of the radial lobing, as a multiple of the LOCAL radius (`_lobe`). Relative to the
# radius rather than to the world, or a trunk gets a few broad bulges and a twig gets none: the same
# reason `GNARL_SPAN` is a fraction of a length. Under 1 and the lobes are finer than a bark furrow
# and read as noise; over 2 and a trunk is an oval.
LOBE_WAVE = 1.3
# `Lobe` is meant to read as "the peak radius deviation, as a fraction of the local radius", and a
# raw fractal noise does not deliver that: its Fac clusters around 0.5 and never reaches either
# bound, so the nominal +/-1 amplitude arrived as +/-0.324 measured over a whole trunk. The gain is
# that number's reciprocal, so `Lobe` 0.2 really is a 20% peak swing and a preset value means the
# thing the knob's name says. Measured, not chosen -- see docs/FOLIAGE.md 2.9.
LOBE_GAIN = 3.09
# The bottom fraction of a limb over which the lobing fades in. It exists because of the FLARE: a
# flared base widens downward, so its first ring's normal tilts DOWNWARD, and a displacement along
# that normal pushes the lowest vertices under z=0. Measured on the shipped conifer -- the lowest
# vertex sat at -0.031 m, which is the pack writer's origin-at-the-base invariant broken and a
# scattered tree buried by three centimetres. Fading over the bottom 1.5% (0.33 m on a 22 m bole)
# costs nothing visible and puts the lowest vertex back at -0.0004 m.
LOBE_FOOT = 0.015

# Wind. Amplitudes are FRACTIONS, never metres, for the reason `GNARL_SPAN` is: a species
# preset is a shape description, and one that only holds at 20 m is not a description. The trunk
# term scales with `Height` and the card term with `Card Size`, so a grass tuft in a gale bends by
# its own size and not by a tree's.
SWAY_SPAN = 0.02        # tip deflection per unit Wind, as a fraction of Height
SWAY_FREQ = 0.55        # gusts per second at Wind 1; the whole-tree rate, deliberately slow
FLUTTER_SPAN = 0.30     # card-tip wobble per unit Wind, as a fraction of Card Size
FLUTTER_FREQ = 1.9      # leaf rate at Wind 1, well above the trunk's so the two do not beat
# The steady part of the gust. Wind pushes a tree downwind and then breathes about that lean, so
# the oscillation rides on a bias rather than swinging symmetrically through the rest pose --
# a tree that passes through vertical every cycle reads as a metronome, not as weather.
SWAY_BIAS = 0.6
_TAU = 2.0 * math.pi

# Per-level defaults, index 0 = level 1 (the first branches off the trunk). A NARROW CONIFER: an
# early version's defaults grew a 13 m crown on a 20 m trunk, which is a spreading broadleaf and the
# opposite of the tall narrow conifer that started this track. The lever is `length` -- a level-1
# branch is a fraction of the trunk's length, so 0.42 was a 8.4 m arm. A conifer's lowest limbs are
# nearer a sixth of its height. These are the floor for a bare build; a real species comes from a
# preset (assets.foliage_species).
_LEVEL_DEFAULTS = (
    # branches, angle, length, radius, phyllotaxy, start, sag
    (16, 78.0, 0.115, 0.26, 137.5, 0.26, 0.0),
    (5, 54.0, 0.30, 0.38, 137.5, 0.20, 0.0),
    (4, 47.0, 0.38, 0.45, 137.5, 0.16, 0.0),
    (3, 42.0, 0.46, 0.50, 137.5, 0.15, 0.0),
)

# 137.5 degrees is the golden angle, which is what real phyllotaxy converges on: successive children
# never line up, so a whorl does not read as a wheel of spokes. It is a default, not a constant --
# a whorled conifer wants 90 and an opposite-leaved species wants 180.

# Which live socket each param key becomes (variants and LODs). The recipe owns both vocabularies --
# the params `build` reads and the sockets `add_input` creates -- so this is the one place they are
# tied together, and `core/foliage_variants.py` reads a TUNED tree's live knobs back into params
# through it. Without that a variant would be baked from the species preset alone and every slider
# the artist moved would be silently discarded, which is a defect that renders: eight variants of a
# tree nobody authored.
#
# The STRUCTURAL params (`levels`, `profile_segments`, `bark_set`, `atlas`, `skeleton`) are absent
# on purpose: they are Python arguments to this function and have no socket at all. `Wind` and `Wind
# Direction` are the mirror case -- sockets with no param key, because they belong to the world and
# the applier overwrites them (docs/FOLIAGE.md 4.6). The gate holds both halves.
PARAM_SOCKETS = {
    "seed": "Seed", "height": "Height", "segments": "Segments",
    "trunk_radius": "Trunk Radius", "taper": "Taper", "lean": "Lean", "gnarl": "Gnarl",
    "branch_segments": "Branch Segments", "shade_smooth": "Shade Smooth",
    "taper_curve": "Taper Curve", "flare": "Flare", "collar": "Collar", "lobe": "Lobe",
    "cards": "Cards", "card_size": "Card Size", "card_width": "Card Width",
    "droop": "Droop", "card_spread": "Spread",
    "leaf_level": "Leaf Level", "leaf_start": "Leaf Start",
    "atlas_cols": "Atlas Columns", "atlas_rows": "Atlas Rows", "bark_scale": "Bark Scale",
    "sway": "Sway", "leaf_flutter": "Leaf Flutter",
}
# `sag` is a LEVEL key and the card `droop` is a whole-tree one, which is why they are not the same
# word: both are gravity, but one bends wood and the other hangs a leaf spray, and a single `Droop`
# appearing once at the top and once per level would be two different knobs under one name.
_LEVEL_SOCKETS = {"branches": "Branches", "angle": "Angle", "length": "Length",
                  "radius": "Radius", "phyllotaxy": "Phyllotaxy", "start": "Start",
                  "sag": "Sag"}
# The two sockets the world owns, listed so a caller can say so rather than infer it from an
# absence.
WORLD_SOCKETS = ("Wind", "Wind Direction")


def param_socket(key):
    """The interface socket name a param key becomes, or None when it is structural.

    `l3_angle` -> `L3 Angle`, by the same arithmetic `build` uses to create it, so a level block
    added to `assets.FOLIAGE_PARAM_KEYS` needs no second table here.
    """
    if key in PARAM_SOCKETS:
        return PARAM_SOCKETS[key]
    if key.startswith("l") and "_" in key:
        head, _, tail = key.partition("_")
        if head[1:].isdigit() and tail in _LEVEL_SOCKETS:
            return f"L{int(head[1:])} {_LEVEL_SOCKETS[tail]}"
    return None


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


def _scaled(ng, vector, scalar, location=(0, 0)):
    """A vector times a scalar. `Scale` is a NAMED socket on Vector Math's SCALE operation and not
    the second vector input, so it cannot go through `_vec`."""
    node = ng.nodes.new("ShaderNodeVectorMath")
    node.operation = "SCALE"
    node.location = location
    ng.links.new(vector, node.inputs[0])
    ng.links.new(scalar, node.inputs["Scale"])
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


def _bend(ng, curve, gnarl, lean, seed_f, scale, location, sag=None):
    """Offset every point by a noise field in XY, weighted by its own spline factor.

    The weight is why a branch stays attached: factor is 0 at the base, so the base point does not
    move and remains coincident with the parent point it was instanced on. `lean` adds a steady pull
    in +X on the same weighting, which is what makes a trunk lean rather than wander.

    `sag` (the wood shaping) is the third term and the only one in Z: a limb's own weight, as a
    downward pull that grows with the SQUARE of the spline factor. Squared rather than linear
    because a cantilever's deflection is, so a branch leaves its parent at the angle the rotation
    gave it and curves over further out -- which is the difference between a bough and a spoke.
    Linear sag instead drops the whole limb by a constant slope and just re-reads as a smaller
    `Angle`. Negative sags UPWARD, for a young conifer's whorls or a fastigiate poplar, and it is a
    fraction of the limb's own length so a species survives being applied at another size. At 0 the
    offset is identically zero, so every the earlier measurement of a straight limb still holds.

    Both amplitudes are a fraction of the CURVE'S OWN LENGTH, not metres. An early version had them
    in metres, which is invisible on a 20 m trunk and destroys anything small: measured, a 0.10 m
    grass stem at the default Gnarl of 0.6 was displaced 0.3 m, so the tuft came back 1.7 m tall and
    2 m wide. Species presets are the whole reason that matters -- a shape description has to
    survive being applied at a different size, or "the same shrub, waist high" is a different shrub.
    Reading `Spline Length` here works for both callers: the trunk is one spline, and the branches
    are bent after their realize, so each is its own spline with its own length.
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
    # GNARL_SPAN keeps the knob's numbers where they started: at Gnarl 0.9 on a 20 m trunk this is
    # the same +/- 0.45 m the original measurements were taken at, so a tuned tree does not change
    # shape.
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
    oz = 0.0
    if sag is not None:
        # Squared weighting of its own, not the noise's factor^1.5: this is a cantilever and that is
        # a wander. Both are 0 at the base, which is the invariant that keeps a branch in its
        # socket.
        drop = math_node(ng, "POWER", factor, 2.0, (lx + 180, ly + 640))
        oz = math_node(ng, "MULTIPLY",
                       math_node(ng, "MULTIPLY", sag, length.outputs["Length"],
                                 (lx + 360, ly + 640)),
                       math_node(ng, "MULTIPLY", drop, -1.0, (lx + 360, ly + 780)),
                       (lx + 720, ly + 640))
    offset = _combine(ng, ox, oy, oz, (lx + 1080, ly - 200))
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


def _taper(ng, curve, base_radius, taper, location, power=None, swell=None, span=FLARE_SPAN):
    """Radius falling from `base_radius` at the base to `base_radius * (1 - taper)` at the tip.

    Two wood-shaping terms on top of that, both inert at their defaults so the linear taper the
    earlier numbers were measured against is exactly what `power` 1 and `swell` 0 still produce.

    `power` raises the spline factor before the taper is applied, which is the difference between a
    cone and a bole. At 1 the radius falls linearly, and a straight line whose radius falls linearly
    IS a cone -- the shape the review called a pipe. Above 1 the fall is slow at first and steep
    near the top, which is what a trunk does: nearly cylindrical through the merchantable length,
    then tapering out into the crown. Below 1 gives the opposite, a limb that thins immediately,
    which is what a vine or a frond stem wants.

    `swell` multiplies the radius by up to (1 + swell) over the bottom `span` of the limb, squared
    so it is concave. It is one mechanism serving the two things the review asked for separately: on
    the trunk it is the ROOT FLARE, on a branch it is the COLLAR where the limb meets its parent.
    They are the same statement -- wood thickens where it has to carry a moment -- and differ only
    in how far up they reach, which is the `span` argument (FLARE_SPAN against COLLAR_SPAN). A
    collar cannot poke through its parent by construction: the base radius is already the parent's
    own radius times a per-level ratio well under 1, so a swell of 1 on a 0.46 ratio is still under
    the parent.

    It stores TWO radii, and the second one is why the bark stopped shearing at the trunk foot.
    `bbt_fol_uvrad` is the radius WITHOUT the swell; `_tag` then stores the real one as
    `bbt_fol_rad`. The sweep, the lobing and the next level's base radius all want the real radius,
    because that is the shape; only the bark UV wants the smooth one, because `_sweep_uv` builds U
    as `unwrap_u * 2*pi*rad / Bark Scale` and keeping metres-per-tile constant around a
    circumference that changes fast forces the texture to slide sideways instead.

    Measured, per quad, in tiles, on the shipped conifer at the gate's size: the
    median slide over 8,235 bark quads was 0.0000 and the p95 0.068, and the single bottom band was
    **1.713** -- the flare takes the radius 0.798 to 0.416 across one segment, because FLARE_SPAN is
    0.05 and `segments` is 16, so the whole flare fits inside one band. The broadleaf measured
    1.825. With the flare turned off entirely the worst dropped to 0.245, which is what identified
    the cause; reading the UV off the pre-swell radius gets the same result with the flare still in
    the silhouette.
    """
    lx, ly = location
    factor, _ = _spline_parameter(ng, (lx, ly + 200))
    curved = factor if power is None else math_node(ng, "POWER", factor, power, (lx + 180, ly + 380))
    shrink = math_node(ng, "SUBTRACT", 1.0,
                       math_node(ng, "MULTIPLY", taper, curved, (lx + 180, ly + 200)),
                       (lx + 360, ly + 200))
    radius = math_node(ng, "MULTIPLY", base_radius, shrink, (lx + 540, ly + 100))
    # The bark UV's radius, taken here because here is the last moment it exists: after the swell
    # there is no way to recover the smooth taper, and `_tag` reads the curve rather than a socket.
    # Floored the same way the real radius is, so a degenerate tip cannot divide the UV by zero.
    uv_radius = math_node(ng, "MAXIMUM", radius, MIN_RADIUS, (lx + 720, ly + 400))
    if swell is not None:
        ramp = math_node(ng, "POWER",
                         math_node(ng, "MAXIMUM",
                                   math_node(ng, "SUBTRACT", 1.0,
                                             math_node(ng, "DIVIDE", factor, span,
                                                       (lx + 180, ly - 240)),
                                             (lx + 360, ly - 240)),
                                   0.0, (lx + 540, ly - 240)),
                         2.0, (lx + 720, ly - 240))
        radius = math_node(ng, "MULTIPLY", radius,
                           math_node(ng, "ADD", 1.0,
                                     math_node(ng, "MULTIPLY", swell, ramp, (lx + 900, ly - 240)),
                                     (lx + 1080, ly - 240)),
                           (lx + 1260, ly + 100))
    radius = math_node(ng, "MAXIMUM", radius, MIN_RADIUS, (lx + 1440, ly + 100))
    # Stored on the POINT domain for `bbt_fol_rad`'s reason: it has to survive the trim, the
    # resample and the instancing that stand between here and `_sweep_uv`, and a constant per point
    # interpolates to itself.
    curve = _store(ng, curve, "bbt_fol_uvrad", uv_radius, "FLOAT", "POINT", (lx + 1440, ly - 480))
    node = ng.nodes.new("GeometryNodeSetCurveRadius")
    node.location = (lx + 1620, ly)
    ng.links.new(curve, node.inputs["Curve"])
    ng.links.new(radius, node.inputs["Radius"])
    return node.outputs["Curve"]


def _lobe(ng, mesh, gi, seed_f, location):
    """Push every wood vertex along its own normal, by a noise whose amplitude is the local radius.

    This is the answer to "the trunk is a pipe", and it is a pipe for a reason no knob could reach
    before: the sweep's profile is ONE circle of one radius, so every cross-section of every limb in
    every tree was a perfect circle, and the only thing that varied along a bole was how big that
    circle was. A cylinder with a smooth radius function is a pipe however good the bark on it is.

    Along the vertex NORMAL because on a swept tube the normal IS the radial direction, so one Set
    Position buys lobes around the limb and bulges along it without any of the per-limb axis
    arithmetic a proper radial displacement would need. Three properties make it safe:

    - the amplitude is a fraction of `bbt_fol_rad`, the tapered radius this ring actually has, so a
      trunk gets metres of relief and a twig gets millimetres and nothing is turned inside out;
    - the noise WAVELENGTH is also scaled by the radius (`LOBE_WAVE`), so a limb carries the same
      number of lobes around it whatever its size -- the `GNARL_SPAN` argument again;
    - it runs after `_sweep_uv` and before the cards are joined, so it moves no card and rewrites no
      UV: the bark UV was measured on the ring positions the sweep produced and displacing along the
      normal does not change which texel a corner reads, it changes where that texel sits in space.

    It adds no vertices, which is what keeps every the earlier count exact.
    """
    lx, ly = location
    rad = math_node(ng, "MAXIMUM", _named(ng, "bbt_fol_rad", "FLOAT", (lx, ly + 200)),
                    MIN_RADIUS, (lx + 180, ly + 200))
    field = noise_field(ng, position(ng, (lx, ly - 200)),
                        math_node(ng, "DIVIDE", LOBE_WAVE, rad, (lx + 360, ly + 200)),
                        detail=2.0, seed=seed_f, location=(lx + 540, ly - 200))
    # Faded in over the bottom `LOBE_FOOT` of the limb, so a flared base cannot be pushed under the
    # ground plane by its own downward-tilted normal. See LOBE_FOOT.
    foot = math_node(ng, "MINIMUM",
                     math_node(ng, "DIVIDE", _named(ng, "bbt_fol_t", "FLOAT", (lx, ly + 380)),
                               LOBE_FOOT, (lx + 180, ly + 380)),
                     1.0, (lx + 360, ly + 380))
    amount = math_node(ng, "MULTIPLY",
                       math_node(ng, "MULTIPLY",
                                 math_node(ng, "MULTIPLY", gi.outputs["Lobe"], rad,
                                           (lx + 540, ly + 200)),
                                 foot, (lx + 720, ly + 380)),
                       math_node(ng, "MULTIPLY",
                                 math_node(ng, "SUBTRACT", field, 0.5, (lx + 720, ly - 200)),
                                 2.0 * LOBE_GAIN, (lx + 900, ly - 200)),
                       (lx + 1080, ly))
    normal = ng.nodes.new("GeometryNodeInputNormal")
    normal.location = (lx + 1080, ly - 400)
    node = ng.nodes.new("GeometryNodeSetPosition")
    node.location = (lx + 1440, ly)
    ng.links.new(mesh, node.inputs["Geometry"])
    ng.links.new(_scaled(ng, normal.outputs["Normal"], amount, (lx + 1260, ly - 200)),
                 node.inputs["Offset"])
    return node.outputs["Geometry"]


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
    # it and cannot compute it: bark UVs run along the limb, the wind pass's wind sway has to fall
    # off to zero at the base or a branch pivots out of its socket, and the cards are placed by it.
    out = _store(ng, out, "bbt_fol_t", factor, "FLOAT", "POINT", (lx + 940, ly))
    # The tapered radius, per point. Read by the NEXT level for its own base radius, and by the bark
    # UV for the local circumference. A Set Curve Radius is not readable downstream of a realize, so
    # like the length it has to become a stored attribute to survive the trim/resample/instance
    # trip.
    radius = ng.nodes.new("GeometryNodeInputRadius")
    radius.location = (lx + 940, ly - 260)
    out = _store(ng, out, "bbt_fol_rad", radius.outputs["Radius"], "FLOAT", "POINT", (lx + 1120, ly))
    # The unit tangent, per point. At a tip it is the direction the branch was heading, which is the
    # axis a leaf spray fans around. A curve field, so it has to be stored before the sweep.
    tangent = ng.nodes.new("GeometryNodeInputTangent")
    tangent.location = (lx + 1120, ly - 260)
    out = _store(ng, out, "bbt_fol_tan", tangent.outputs["Tangent"], "FLOAT_VECTOR", "POINT",
                 (lx + 1300, ly))
    # Where this SKELETON point is, kept for the wind. Curve to Mesh gives every point a ring
    # of `profile` vertices at slightly different heights, and the cards grow on the same points, so
    # this one attribute is what lets both be deflected as rigid pieces of a limb rather than
    # vertex by vertex. Measured: with the ring weighted per vertex instead, the z^2 falloff shears
    # each cross-section and a tip ring's centroid drifts 7.7e-05 m from the card standing on it at
    # Wind 6 -- eighty times the card-attachment residual, from a tree that renders perfectly.
    return _store(ng, out, "bbt_fol_anchor", position(ng, (lx + 1300, ly - 260)),
                  "FLOAT_VECTOR", "POINT", (lx + 1480, ly))


def _branch_rotation(ng, angle_deg, phyllo_deg, seed_f, location):
    """The rotation that takes a unit +Z child curve and points it out of its parent.

    Three steps, and the ORDER is the whole thing. Align local Z to the parent's tangent; spin
    around that axis by the golden-angle multiple for this child's index; only then tilt away from
    it. Tilt before spin and every branch on a whorl leaves in the same direction, which reads as a
    flat fan -- which is what a generated crown already looks like and the reason this recipe
    exists.
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


def _leafy(ng, gi, location):
    """The selection a card grows on: deep enough a limb, far enough along it (the wood shaping).

    every earlier put a card only on a TIP, on every level, and that is the single biggest reason
    the review read the trees as bare sticks with pom-poms on them: a 3 m bough carried its entire
    leaf allowance in one cluster at the far end, and the grass tuft came back as fourteen woody
    dowels with a sprig glued to each. Real foliage is distributed ALONG the youngest wood.

    Two knobs, and both are inert at their defaults so every the earlier card measurement still
    holds:

    - `Leaf Level` is the shallowest level that carries leaves. 0 (the default) is every level,
      including the trunk's own tip, which is exactly what the tip-only rule did. Set to `levels`
      and only the outermost twigs are leafy, which is what a tree does -- nothing grows out of a
      bole.
    - `Leaf Start` is how far along a leafy limb the cards begin, as a fraction. At 1 only the last
      point qualifies, which IS the tip, so the default reproduces the old selection exactly; at 0.4
      the outer 60% of every twig carries them.

    The epsilon is not decoration. `bbt_fol_t` is the spline factor, exactly 1.0 at the last point,
    so a bare `t > Leaf Start` at Leaf Start 1 selects nothing at all and the tree comes back bald.
    """
    lx, ly = location
    level = _f(ng, _named(ng, "bbt_fol_level", "INT", (lx, ly + 200)), (lx + 180, ly + 200))
    deep = math_node(ng, "GREATER_THAN", level,
                     math_node(ng, "SUBTRACT", _f(ng, gi.outputs["Leaf Level"], (lx, ly + 380)),
                               0.5, (lx + 180, ly + 380)),
                     (lx + 360, ly + 200))
    along = math_node(ng, "GREATER_THAN", _named(ng, "bbt_fol_t", "FLOAT", (lx, ly - 40)),
                      math_node(ng, "SUBTRACT", gi.outputs["Leaf Start"], 1e-4, (lx + 180, ly - 40)),
                      (lx + 360, ly - 40))
    return math_node(ng, "GREATER_THAN",
                     math_node(ng, "MULTIPLY", deep, along, (lx + 540, ly + 80)),
                     0.5, (lx + 720, ly + 80))


def _cards(ng, skeleton, gi, seed_f, location):
    """Instance leaf cards along the leafy wood of the skeleton. Returns a mesh socket.

    The route is tips -> a point cloud -> N duplicates of each point -> a card on each, and each hop
    exists for a reason:

    - the tips become a POINT CLOUD (instance a single point on the selection, then realize) because
      the tips are the last points of splines, and neither `Duplicate Elements` nor a per-tip index
      means anything useful on a curve's point domain,
    - `Duplicate Elements` gives `Cards` copies of each tip point, so a card is one instance on one
      point and every card can be aimed and textured on its own. Building an N-card cluster ONCE and
      instancing that would be cheaper and wrong for the same reason an early version's bend runs
      after the realize: every tip would get the identical spray, down to the atlas cells,
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
    ng.links.new(_leafy(ng, gi, (lx - 800, ly + 400)), seed_tips.inputs["Selection"])
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
    # One more per-card value, stored HERE for the same reason the cell is: this is the last stage
    # at which a card is one point, so `Index` is unique per card. After the instancing it is four
    # vertices with no way back. (`bbt_fol_anchor`, the tip a card rides, needs no store of its own
    # -- it was written on the skeleton point in `_tag` and rides here with every other tip
    # attribute, which is the same free ride `bbt_fol_tan` takes to aim the spray.)
    #
    # `bbt_fol_phase` is this card's own 0..1 random, read by the flutter here and by the leaf
    # shader's autumn stagger. One attribute for both, because they are the same statement -- this
    # leaf is not that leaf -- and two independent randoms would let a card flutter as one leaf and
    # turn colour as another.
    phase = ng.nodes.new("FunctionNodeRandomValue")
    phase.data_type = "FLOAT"
    phase.location = (lx + 1160, ly - 400)
    phase.inputs["Min"].default_value = 0.0
    phase.inputs["Max"].default_value = 1.0
    ng.links.new(index.outputs["Index"], phase.inputs["ID"])
    ng.links.new(math_node(ng, "ADD", seed_f, 811.0, (lx + 960, ly - 480)), phase.inputs["Seed"])
    placed = _store(ng, placed, "bbt_fol_phase", phase.outputs["Value"], "FLOAT", "POINT",
                    (lx + 1520, ly))

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
    # the wind pass's wind falloff -- so leaving them is not cosmetic. `bbt_fol_t` is rewritten to
    # the card's OWN 0 at the base and 1 at the free end, which is what a card's sway has to fall
    # off over or it pivots out of the twig it hangs from.
    local = ng.nodes.new("ShaderNodeSeparateXYZ")
    local.location = (lx + 3700, ly - 300)
    ng.links.new(_named(ng, "bbt_fol_cuv", "FLOAT_VECTOR", (lx + 3520, ly - 300)), local.inputs[0])
    cards = _store(ng, cards, "bbt_fol_t", local.outputs["Y"], "FLOAT", "POINT", (lx + 3880, ly))
    cards = _store(ng, cards, "bbt_fol_tip", 0.0, "FLOAT", "POINT", (lx + 4060, ly))
    # Which vertices are cards, on the POINT domain, for the wind pass. Written only on the cards
    # and never on the wood: Join Geometry zero-fills the side that lacks an attribute (measured on
    # 5.2 -- a join of a flagged grid and an unflagged one reads back four 1s and four 0s), so the
    # swept mesh answers 0 without the recipe having to walk it. `bbt_fol_leaf` cannot serve here
    # because it is a FACE flag, and a vertex offset is a POINT question.
    cards = _store(ng, cards, "bbt_fol_card", 1.0, "FLOAT", "POINT", (lx + 4240, ly))
    # The face flag the two Set Material nodes key off. On the FACE domain because that is the
    # domain a material selection is evaluated on; a point flag would have to interpolate, and a
    # face straddling the join between a card and a twig would then be neither.
    return _store(ng, cards, "bbt_fol_leaf", True, "BOOLEAN", "FACE", (lx + 4240, ly))


def _wind_axes(ng, gi, location):
    """(downwind, crosswind) unit vectors in XY from `Wind Direction`, in degrees.

    The same convention the rest of the suite uses (`particulates`, `volumetrics`, and the env
    field they all read): degrees clockwise from north, naming the direction the wind blows TOWARD.
    Crosswind is that turned a quarter turn, which is the axis a leaf flutters across -- a leaf
    that only moves along the wind reads as a flag rather than as foliage.
    """
    lx, ly = location
    rad = math_node(ng, "MULTIPLY", gi.outputs["Wind Direction"], _DEG, (lx, ly))
    cos = math_node(ng, "COSINE", rad, None, (lx + 180, ly + 80))
    sin = math_node(ng, "SINE", rad, None, (lx + 180, ly - 80))
    downwind = _combine(ng, sin, cos, 0.0, (lx + 360, ly + 80))
    crosswind = _combine(ng, cos, math_node(ng, "MULTIPLY", sin, -1.0, (lx + 360, ly - 200)), 0.0,
                         (lx + 540, ly - 120))
    return downwind, crosswind


def _tree_phase(ng, location):
    """A per-TREE phase offset, from the object's own world location.

    Two trees standing side by side must not pulse in unison, and the cheapest thing that is true
    of two trees and false of one is where they are. Read through Self Object -> Object Info, whose
    Location is the object's world position (measured: moving the object to x=7 moves the value to
    7 with no rebuild), so a stand placed by hand is out of phase the moment it is placed and a
    tree dragged across the scene re-phases as it goes.

    **What this does NOT buy, measured rather than assumed.** An object INSTANCED by scatter is
    evaluated once and its result copied, so every instance of one baked variant reads the source
    object's location and therefore shares its phase -- exactly the property docs/FOLIAGE.md 2.5
    already records for the seed, and with the same answer: variety across a stand comes from
    baking N variants, each of which then carries its own phase.
    """
    lx, ly = location
    self_obj = ng.nodes.new("GeometryNodeSelfObject")
    self_obj.location = (lx, ly)
    info = ng.nodes.new("GeometryNodeObjectInfo")
    info.location = (lx + 180, ly)
    ng.links.new(self_obj.outputs["Self Object"], info.inputs["Object"])
    sep = ng.nodes.new("ShaderNodeSeparateXYZ")
    sep.location = (lx + 380, ly)
    ng.links.new(info.outputs["Location"], sep.inputs[0])
    # Two incommensurate multipliers, so a stand on a grid does not land back in phase every row.
    return math_node(ng, "ADD",
                     math_node(ng, "MULTIPLY", sep.outputs["X"], 0.7, (lx + 560, ly + 80)),
                     math_node(ng, "MULTIPLY", sep.outputs["Y"], 1.31, (lx + 560, ly - 80)),
                     (lx + 740, ly))


def _sway(ng, geo, gi, location):
    """Deflect the finished tree by the wind: a whole-tree lean and gust, plus a per-card flutter.

    One `Set Position` on the joined mesh, because the two terms share a falloff argument and
    because a card has to inherit its twig's motion before it adds its own. The offset is:

        downwind * Wind * Sway * SWAY_SPAN * Height * (z/Height)^2 * (bias + gust)
      + crosswind * Wind * Leaf Flutter * FLUTTER_SPAN * Card Size * card * t * wobble

    and every factor of it earns its place:

    - **The height weight is squared, and it is read from `bbt_fol_anchor`.** Squared because a
      trunk is stiff at the butt and whippy at the top, and linear reads as a hinge at the ground.
      Read from the anchor -- the SKELETON point a vertex belongs to -- rather than from the vertex
      itself, so a tube's cross-section and the cards standing on it are all displaced by the one
      amount and a limb translates instead of shearing. Per-vertex weighting instead is what the
      first version did, and it drifted a tip ring 7.7e-05 m from its own card at Wind 6: eighty
      times the card-attachment residual, invisible, and the same failure family as the rest of this
      track. With the anchor the residual is unchanged from a still tree.
    - **The gust rides on a bias, not on zero.** Wind blows a tree over and then breathes; an
      unbiased sine swings it through vertical twice a cycle, which reads as a metronome.
    - **The flutter is gated by `bbt_fol_card` and weighted by `bbt_fol_t`.** That is the whole
      reason the card work rewrites `bbt_fol_t` on the cards to their OWN 0-at-the-base: a card
      pivots at the twig it hangs from rather than swinging through it.
    - **Scene Time is the clock.** No keyframes and no drivers on the geometry, so an animation
      renders the same every time and a still frame is a still frame.

    At `Wind` 0 every term is zero, so a tree with no weather is byte-identical to a tree with no
    weather -- which is what lets every every earlier measurement in the gate stand unchanged.
    """
    lx, ly = location
    downwind, crosswind = _wind_axes(ng, gi, (lx, ly + 600))
    seconds = ng.nodes.new("GeometryNodeInputSceneTime")
    seconds.location = (lx, ly + 300)
    time = seconds.outputs["Seconds"]
    wind = math_node(ng, "MAXIMUM", gi.outputs["Wind"], 0.0, (lx, ly + 180))
    height = math_node(ng, "MAXIMUM", gi.outputs["Height"], 0.01, (lx, ly + 100))
    card = _named(ng, "bbt_fol_card", "FLOAT", (lx, ly - 100))
    anchor = ng.nodes.new("ShaderNodeSeparateXYZ")
    anchor.location = (lx + 180, ly - 200)
    ng.links.new(_named(ng, "bbt_fol_anchor", "FLOAT_VECTOR", (lx, ly - 200)), anchor.inputs[0])
    frac = math_node(ng, "MINIMUM",
                     math_node(ng, "DIVIDE", anchor.outputs["Z"], height, (lx + 920, ly - 200)),
                     1.0, (lx + 1100, ly - 200))
    weight = math_node(ng, "MULTIPLY", frac, frac, (lx + 1280, ly - 200))

    gust = math_node(ng, "ADD", SWAY_BIAS,
                     math_node(ng, "MULTIPLY", 1.0 - SWAY_BIAS,
                               math_node(ng, "SINE",
                                         math_node(ng, "ADD",
                                                   math_node(ng, "MULTIPLY", time,
                                                             _TAU * SWAY_FREQ, (lx + 180, ly + 300)),
                                                   _tree_phase(ng, (lx - 900, ly + 420)),
                                                   (lx + 360, ly + 300)),
                                         None, (lx + 540, ly + 300)),
                               (lx + 720, ly + 300)),
                     (lx + 900, ly + 300))
    bend = math_node(ng, "MULTIPLY",
                     math_node(ng, "MULTIPLY",
                               math_node(ng, "MULTIPLY", wind, gi.outputs["Sway"],
                                         (lx + 1100, ly + 180)),
                               math_node(ng, "MULTIPLY", height, SWAY_SPAN, (lx + 1100, ly + 100)),
                               (lx + 1280, ly + 140)),
                     math_node(ng, "MULTIPLY", weight, gust, (lx + 1460, ly + 40)),
                     (lx + 1640, ly + 100))

    # The flutter: fast, small, per card, and across the wind. Its own phase comes from the card's
    # `bbt_fol_phase`, so a spray shimmers rather than beating as one sheet.
    phase = math_node(ng, "MULTIPLY", _named(ng, "bbt_fol_phase", "FLOAT", (lx, ly - 600)),
                      _TAU, (lx + 180, ly - 600))
    wobble = math_node(ng, "SINE",
                       math_node(ng, "ADD",
                                 math_node(ng, "MULTIPLY", time,
                                           _TAU * FLUTTER_FREQ, (lx + 180, ly - 480)),
                                 phase, (lx + 380, ly - 520)),
                       None, (lx + 560, ly - 520))
    # Weighted by the card's own base-to-tip factor, so the attachment corner does not move at all.
    span = math_node(ng, "MULTIPLY",
                     math_node(ng, "MULTIPLY", wind, gi.outputs["Leaf Flutter"],
                               (lx + 740, ly - 640)),
                     math_node(ng, "MULTIPLY", gi.outputs["Card Size"], FLUTTER_SPAN,
                               (lx + 740, ly - 720)),
                     (lx + 920, ly - 680))
    flutter = math_node(ng, "MULTIPLY",
                        math_node(ng, "MULTIPLY", span, wobble, (lx + 1100, ly - 600)),
                        math_node(ng, "MULTIPLY", card,
                                  _named(ng, "bbt_fol_t", "FLOAT", (lx + 920, ly - 860)),
                                  (lx + 1100, ly - 800)),
                        (lx + 1280, ly - 700))

    offset = _vec(ng, "ADD", _scaled(ng, downwind, bend, (lx + 1820, ly + 100)),
                  _scaled(ng, crosswind, flutter, (lx + 1820, ly - 700)), (lx + 2000, ly))

    node = ng.nodes.new("GeometryNodeSetPosition")
    node.location = (lx + 2200, ly)
    ng.links.new(geo, node.inputs["Geometry"])
    ng.links.new(offset, node.inputs["Offset"])
    return node.outputs["Geometry"]


# How far below its own face's mean a corner's U has to sit before it is read as the wrap corner.
# The arithmetic, for an n-sided profile: the wrap face's corners are {(n-1)/n, 0}, so its mean is
# (n-1)/2n and its low corners sit -(n-1)/2n below it, which tends to -0.5. Every ORDINARY face's
# corners sit within 1/2n of their own mean, so at worst -1/6 for the coarsest profile the recipe
# allows (n=3). -0.3 separates the two for every n from 3 to 24.
_SEAM_CUT = -0.3


def _unwrap_u(ng, location):
    """The profile's U with its cyclic wrap undone, on the CORNER domain.

    `bbt_fol_u` is the profile circle's spline parameter, so it runs 0 .. 1-1/n around the tube and
    then jumps back to 0. One vertex cannot hold both 1 and 0, which is what makes a cylindrical
    unwrap need a seam -- but a UV lives on the CORNER domain, and a corner can. So the wrap corners
    are found and pushed up by a whole turn, and U becomes monotonic 0 .. 1 with no duplicated
    geometry: the alternative fix is an open profile with n+1 points, which would add a vertex ring
    per curve point and change every vertex count the gate measures.

    A corner is a wrap corner when its U sits far below its own FACE's mean U. `Evaluate on Domain`
    at the FACE domain averages a point attribute over the face's corners -- verified on Blender 5.2
    (a quad whose corners carried -1, 0, 0, -1 read back -0.5), which is exactly the mean this
    needs.
    """
    lx, ly = location
    u = _named(ng, "bbt_fol_u", "FLOAT", (lx, ly))
    face_mean = ng.nodes.new("GeometryNodeFieldOnDomain")
    face_mean.data_type = "FLOAT"
    face_mean.domain = "FACE"
    face_mean.location = (lx + 180, ly - 160)
    ng.links.new(u, face_mean.inputs["Value"])
    below = math_node(ng, "LESS_THAN",
                      math_node(ng, "SUBTRACT", u, face_mean.outputs["Value"], (lx + 360, ly - 80)),
                      _SEAM_CUT, (lx + 540, ly - 80))
    return math_node(ng, "ADD", u, below, (lx + 720, ly))


def _sweep_uv(ng, mesh, gi, location):
    """Write the swept mesh's `UVMap` in METRES, so bark reads at one grain over the whole tree.

    U runs around the tube: the profile circle's own spline parameter (stored as `bbt_fol_u` before
    the sweep, since `Curve to Mesh` carries a profile attribute around every ring) scaled by the
    local circumference. V runs along the limb: `bbt_fol_t` scaled by the limb's length. Both are
    then divided by `Bark Scale`, which is therefore metres per texture tile.

    Without the metres scaling a two-metre trunk and a twenty-centimetre twig each get exactly one
    tile of bark, so the twig's grain is ten times too coarse -- the single thing that makes a
    procedural trunk read as plastic.

    The wrap seam, which was measured and fixed. The profile is cyclic, so its spline parameter runs
    0 .. 1-1/n and the last quad of every ring runs from nearly 1 back to 0 -- one column per limb
    carrying the WHOLE texture, reversed and squeezed into a single quad. Measured on a 6-sided
    profile: the median face spanned 0.035 of a tile in U and the worst spanned 3.927, a factor of
    112, i.e. a smeared mirrored band down every trunk. Nobody could see it at the time because
    nothing was textured, which is why it was raised as an open question rather than found later.

    The fix is `_unwrap_u`, and it costs no vertices and no interface change.

    The SHEAR, which is a different failure at the same seam and was found by the bark-UV gate
    rather than here. U is proportional to azimuth times circumference, so a band whose two rings
    have different radii maps the same azimuth to different U, and the texture slides sideways
    across it. That is inherent to a metres-based cylindrical UV and it is small everywhere the
    radius changes slowly -- median 0.0000 of a tile and p95 0.068 over the shipped conifer's 8,235
    bark quads. It is not small at the ROOT FLARE: `FLARE_SPAN` is 0.05 of the limb and a trunk is
    resampled to 16 points, so the whole 1.9x swell lands on ONE band and slides the bark **1.713
    tiles** sideways across it. The broadleaf measured 1.825. That is the reported defect, "bark
    wrapping wrong at the foot of the conifer trunk".

    So the circumference is read from `bbt_fol_uvrad`, the taper radius BEFORE the swell (`_taper`),
    while everything else -- the sweep, the lobing, the next level's base radius -- keeps the real
    one. The flare stays in the silhouette and the grain stays one grain over the whole tree; only
    the bark's metres-per-tile stops chasing a radius that changes faster than a band can carry.
    Measured after: worst slide 0.068 tiles, which is the p95 of the rest of the tree, i.e. the
    flare is no longer special.

    No fallback, and that is safe rather than optimistic: `_taper` stores `bbt_fol_uvrad`
    unconditionally and every limb in the tree comes through `_taper`, so the one graph that reads
    this attribute is the one graph that wrote it. A missing one would mean the recipe never built.
    """
    lx, ly = location
    circumference = math_node(ng, "MULTIPLY",
                              _named(ng, "bbt_fol_uvrad", "FLOAT", (lx, ly + 200)),
                              2.0 * math.pi, (lx + 200, ly + 200))
    scale = math_node(ng, "MAXIMUM", gi.outputs["Bark Scale"], 0.01, (lx, ly - 400))
    u = math_node(ng, "DIVIDE",
                  math_node(ng, "MULTIPLY", _unwrap_u(ng, (lx, ly + 60)),
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

    **Bark is UV-projected, not box-projected, and an early version had it the other way round.**
    The card work built the metres-based bark UV of docs/FOLIAGE.md 2.7 and then assigned the bark
    set with `box=True`, which samples by WORLD POSITION -- so the UVs it had just measured reached
    nothing, the grain followed the world axes instead of the limb, and a leaning trunk was a slab
    of bark projected through it. The third defect in the family this subsystem shipped three of: it
    renders, and only a number tells. Box projection is the right default for `surface_material` in
    general (it exists for un-UV'd props); a swept limb is precisely the case that carries real UVs
    and needs them.
    """
    from ...materials import _wrapper_name, foliage_card_material, surface_material

    base = ng.name.split(".")[0]
    bark_name = f"{base} Bark"
    fresh = _wrapper_name(bark_name) not in bpy.data.materials
    bark = surface_material(bark_name, texset_name=str(params.get("bark_set", "")), box=False)
    if fresh:
        node = bark.node_tree.nodes.get("Master")
        if node is not None:
            node.inputs["Base Color"].default_value = (0.19, 0.13, 0.09, 1.0)
            node.inputs["Roughness"].default_value = 0.85
            node.inputs["Canopy Snow"].default_value = 1.0  # a trunk is vertical; snow needs the hint
    card = foliage_card_material(f"{base} Leaf", atlas=_atlas_set(params))
    return bark, card


def _atlas_set(params):
    """The atlas set this build will actually read: the one asked for, else the shipped block-out one.

    The fallback is not politeness, and the ASYMMETRY WITH BARK is the whole point (the wood
    shaping). A species preset naming a set that no pack provides is a documented STATE rather than
    a mistake (docs/FOLIAGE.md 4.4) -- `conifer` asks for `bark_conifer`, and before anyone
    generates it the trunk is a solid tint, which is the block-out convention everywhere in the
    suite.

    A CARD has no such graceful floor. Its material is the surface master with a white tint so the
    atlas reads at face value, and its cutout comes from the set; with no set there is no cutout and
    no albedo, so every leaf renders as an opaque WHITE RECTANGLE. Measured on the first
    wood-shaping run with a generated atlas the resolver could not see: a whole canopy of white
    quads, which reads exactly like the documented translucency bug and is nothing of the kind.

    So the two slots differ because their failures differ. A missing bark set costs a texture; a
    missing atlas costs the silhouette. `_atlas_grid` resolves through here too, or a build would
    read the absent set's (absent) sidecar and the fallback atlas with the wrong grid.
    """
    name = str(params.get("atlas", DEFAULT_ATLAS) or DEFAULT_ATLAS)
    return name if assets.texture_set_maps(name) else DEFAULT_ATLAS


DEFAULT_ATLAS_GRID = (2, 2)   # the placeholder atlas's own layout, and the floor when nothing says


def _atlas_grid(params):
    """(cols, rows) for the atlas this build reads: the explicit params, else the SET's own sidecar.

    The open question in docs/FOLIAGE.md 6, answered here and in `assets.atlas_grid`. the card
    work's interim answer was these two params alone, which works and does not scale: an artist
    assigning a generated 4x4 atlas has to know to change two numbers, and a card reading 2x2 off a
    4x4 atlas samples a quarter of the cell it wanted plus slices of three neighbours -- which
    renders as foliage, so nothing catches it. A generated set records its grid, so the ordinary
    case needs no numbers at all.

    The params stay as the OVERRIDE, per the brief: a hand-made atlas may ship no sidecar, and an
    artist may deliberately read a 4x4 as 2x2 to use only its bottom row.
    """
    cols, rows = DEFAULT_ATLAS_GRID
    declared = assets.atlas_grid(_atlas_set(params))
    if declared is not None:
        cols, rows = declared
    return (max(1, min(16, int(params.get("atlas_cols", cols)))),
            max(1, min(16, int(params.get("atlas_rows", rows)))))


def _env_wind(params):
    """(Wind, Wind Direction) for this build: the explicit params, else the shared world, else 0.

    Reading `bbt_env` at build time is what makes "wind for free" true of every caller and not only
    of the panel -- a tree grown over MCP into a scene that is already blowing comes out blowing.
    It is a SEED and not the live feed: the live half is `foliage_build.apply_wind`, which the World
    applier runs on every change. Absent Firmament (a standalone .blend, the headless gate) there is
    no world to read and the default is a still tree, which is also what keeps every the earlier
    measurement valid at its defaults.
    """
    from ... import env as bbt_env

    world = bbt_env.get_env()
    wind = params.get("wind", getattr(world, "wind_strength", 0.0) if world else 0.0)
    direction = params.get("wind_direction",
                           getattr(world, "wind_direction", 0.0) if world else 0.0)
    return max(0.0, float(wind)), float(direction) % 360.0


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
    # The four wood-shaping terms, all defaulting to INERT: `Taper Curve` 1 is the linear taper the
    # skeleton measured, and 0 flare, collar and lobe are the perfect cylinder it measured it on. A
    # species preset turns them on, which is where a shape description belongs (docs/FOLIAGE.md
    # 2.9).
    add_input(ng, "Taper Curve", "NodeSocketFloat", float(params.get("taper_curve", 1.0)), 0.2, 5.0)
    add_input(ng, "Flare", "NodeSocketFloat", float(params.get("flare", 0.0)), 0.0, 4.0)
    add_input(ng, "Collar", "NodeSocketFloat", float(params.get("collar", 0.0)), 0.0, 3.0)
    add_input(ng, "Lobe", "NodeSocketFloat", float(params.get("lobe", 0.0)), 0.0, 0.6)
    for level in range(1, levels + 1):
        count, angle, length, radius, phyllo, start, sag = _LEVEL_DEFAULTS[level - 1]
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
        # Gravity, per level, because it is per level in a tree: a bough sags and the twig on its
        # end sags much less in absolute terms and about as much relative to itself. Negative sweeps
        # up.
        add_input(ng, prefix + "Sag", "NodeSocketFloat",
                  float(params.get(f"l{level}_sag", sag)), -1.0, 1.0)
    # Leaf cards (the cards). Live, because a canopy is tuned by eye and none of these changes the
    # topology of anything but the cards themselves. `Cards` 0 is the off switch and leaves a bare
    # skeleton.
    add_input(ng, "Cards", "NodeSocketInt", int(params.get("cards", 5)), 0, 64)
    add_input(ng, "Card Size", "NodeSocketFloat", float(params.get("card_size", 0.70)), 0.0)
    add_input(ng, "Card Width", "NodeSocketFloat", float(params.get("card_width", 0.60)), 0.01, 4.0)
    add_input(ng, "Droop", "NodeSocketFloat", float(params.get("droop", 0.35)), 0.0, 1.0)
    add_input(ng, "Spread", "NodeSocketFloat", float(params.get("card_spread", 34.0)), 0.0, 90.0)
    # Where the leaves are (the wood shaping, `_leafy`). Both defaults reproduce the tip-only rule
    # exactly: level 0 is every level, and Leaf Start 1 is the last point of a limb, which is its
    # tip. CLAMPED to this build's depth, and that is not defensive tidying -- it is what keeps the
    # LOD ladder from producing a bald tree. A rung is a rebuild at `levels - 1` (docs/FOLIAGE.md
    # 2.6), so a species that puts its leaves on level 3 asks LOD1 for a level that no longer
    # exists, the selection matches nothing, and the rung comes back as bare wood with its canopy
    # silently gone.
    add_input(ng, "Leaf Level", "NodeSocketInt",
              min(int(params.get("leaf_level", 0)), levels), 0, MAX_LEVELS)
    add_input(ng, "Leaf Start", "NodeSocketFloat", float(params.get("leaf_start", 1.0)), 0.0, 1.0)
    # The atlas grid. The SET carries its own layout in a sidecar and that is the default; an
    # explicit param still wins. See `_atlas_grid` -- this is the open question answered.
    grid_cols, grid_rows = _atlas_grid(params)
    add_input(ng, "Atlas Columns", "NodeSocketInt", grid_cols, 1, 16)
    add_input(ng, "Atlas Rows", "NodeSocketInt", grid_rows, 1, 16)
    add_input(ng, "Bark Scale", "NodeSocketFloat", float(params.get("bark_scale", 0.6)), 0.01)
    # Wind. Live, and seeded from the shared world so a tree built into a windy scene is
    # already moving; the world applier keeps both fed after that (core.foliage_build). `Wind` 0
    # is a still tree and every the earlier measurement still holds at it.
    wind, wind_dir = _env_wind(params)
    add_input(ng, "Wind", "NodeSocketFloat", wind, 0.0)
    add_input(ng, "Wind Direction", "NodeSocketFloat", wind_dir, 0.0, 360.0)
    add_input(ng, "Sway", "NodeSocketFloat", float(params.get("sway", 1.0)), 0.0, 4.0)
    add_input(ng, "Leaf Flutter", "NodeSocketFloat", float(params.get("leaf_flutter", 1.0)),
              0.0, 4.0)
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
    # No sag on the trunk: a bole carries its load in compression, and a drooping one is a different
    # species of plant. The swell here is the ROOT FLARE -- the same `_taper` term the branches use
    # as a collar, over a much shorter span.
    trunk = _taper(ng, trunk, gi.outputs["Trunk Radius"], gi.outputs["Taper"], (-200, 300),
                   power=gi.outputs["Taper Curve"], swell=gi.outputs["Flare"], span=FLARE_SPAN)
    trunk = _tag(ng, trunk, 0, (2200, 300))

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
        # noise reads realized world position, so every branch samples a different part of the
        # field.
        kids = _bend(ng, realize.outputs["Geometry"], gi.outputs["Gnarl"], 0.0, level_seed, 0.6,
                     (-100, row), sag=gi.outputs[prefix + "Sag"])
        # The radius this branch starts at: the parent's ACTUAL radius where it attached, times this
        # level's ratio. `bbt_fol_rad` was stored after the parent's taper and rode here on the same
        # interpolation that carries `bbt_fol_plen`, so it is the parent's local thickness, not its
        # base thickness. An early version used a running product of the ratios instead, which
        # agrees with this only while every ratio is uniform AND the parent does not taper -- and
        # species presets vary the ratios per level, which is exactly what makes the product wrong.
        base_radius = math_node(ng, "MULTIPLY",
                                _named(ng, "bbt_fol_rad", "FLOAT", (1120, row + 400)),
                                gi.outputs[prefix + "Radius"], (1300, row + 400))
        kids = _taper(ng, kids, base_radius, gi.outputs["Taper"], (1500, row),
                      power=gi.outputs["Taper Curve"], swell=gi.outputs["Collar"],
                      span=COLLAR_SPAN)
        kids = _tag(ng, kids, level, (3400, row))
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
    # geometry comes out. Worth having beyond testing -- tuning branch structure is a much faster
    # loop without paying for the tube mesh on every slider drag, and it is the only view in which a
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
    # input and stopped applying the curve's radius attribute implicitly, so an early version's
    # sweep -- which only set the radius and never wired it -- came back as a uniform 1 m tube
    # whatever Trunk Radius, Taper and the per-level ratios said. Measured: every tree's trunk and
    # every twig were the same 2 m across. `bbt_fol_rad` is the tapered radius this level actually
    # has, which is the same value the next level's base radius and the bark U circumference read.
    ng.links.new(_named(ng, "bbt_fol_rad", "FLOAT", (3600, -180)), to_mesh.inputs["Scale"])
    to_mesh.inputs["Fill Caps"].default_value = False

    smooth = ng.nodes.new("GeometryNodeSetShadeSmooth")
    smooth.location = (4000, 0)
    ng.links.new(to_mesh.outputs["Mesh"], smooth.inputs["Mesh"])
    ng.links.new(gi.outputs["Shade Smooth"], smooth.inputs["Shade Smooth"])
    swept = _sweep_uv(ng, smooth.outputs["Mesh"], gi, (4200, 0))
    # The lobing goes HERE and nowhere else: after the UVs, which it must not disturb, and before
    # the cards are joined, which it must not move. See `_lobe`.
    swept = _lobe(ng, swept, gi, seed_f, (5600, 400))

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

    # Wind last, on the joined mesh: the cards have to be in place before anything moves them, and
    # the trunk and its leaves must be deflected by ONE pass or they part company (`_sway`).
    swaying = _sway(ng, both.outputs["Geometry"], gi, (7800, -2400))
    bark_mat, card_mat = _tree_materials(ng, params)
    ng.links.new(_materials(ng, swaying, bark_mat, card_mat, (10200, 0)),
                 out.inputs["Geometry"])
