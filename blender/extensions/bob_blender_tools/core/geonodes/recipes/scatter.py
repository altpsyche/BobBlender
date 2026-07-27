"""scatter: distribute a collection of assets across an emitter surface.

A GScatter-style layer. Reads the Emitter object's geometry, distributes points
with Poisson disk sampling, filters by slope, masks the density, and instances a
random pick from the Assets collection with per-instance random scale and Z
rotation, aligned to the surface normal.

Modifier inputs (editable knobs):

- Core: Density, Distance Min, Seed, Min Scale, Max Scale.
- Slope: Min Normal Z / Max Normal Z (keep faces whose up-normal is in that band;
  1 = flat, 0 = vertical).
- Altitude mask: Height Min / Height Max / Height Falloff / Height Strength (a
  smooth height band on world Z; Strength 0 = off).
- Noise mask: Noise Scale / Noise Contrast / Noise Seed / Noise Strength (procedural
  patchy density for clumping; Strength 0 = off).
- Curve (BobSplines C4, curve_mode clear/keep): reads a terrain curve mask (written by the
  curve overlay), curve_attr selecting which one -- bbt_curve_mask (the whole band, default) or
  bbt_curve_edge (the shoulder/verge ring, R5, for the panel's Verge mode). clear clears the layer
  along it, keep scatters only within it. No scn.path proximity: the overlay solved it once, this
  just reads it, so many curves compose (the mask MAX-accumulates them) with no per-layer proximity.
- Paint (when a mask vertex group is set): Paint Strength.
- Camera cull (when a camera is set): Camera Distance / Camera Cone / Cull Falloff.
- Sink: Z Offset (metres, negative sinks). A generated trunk carries a wide root flare and an
  asset whose origin is at its base then floats its skirt over sloped ground; sinking the instance
  is the fix, and without this knob the only lever was camera placement.

`assets_exclude` / `assets_include` (lists of object names) drop assets from the pick without
touching the collection, so one bad member of a shared BOB_Assets_<Kind> pool can be left out of
ONE layer. Resolved at build time against the collection's own child order, which is the order
Collection Info's Separate Children emits, and reported as a warning when a name is not in the
collection (rather than silently scattering the full pool).

All masks multiply into the Poisson Density Factor (a 0..1 field), so they compose;
the slope band drives the Selection instead. The emitter, the asset collection, and
the optional camera are set on the nodes (Blender 5.x GN modifiers no longer store
object or collection inputs). Params: emitter and camera (object names), assets (a
collection name), curve_mode (none/clear/keep), vgroup (a vertex group name on the
emitter). Placing instances ALONG a curve is the separate scatter_along recipe.
"""

import bpy

from ..blocks import (
    math_node,
    mix_float,
    noise_field,
    object_geometry,
    position,
    random_value,
    smooth_falloff,
)
from ..scaffold import add_input
from . import recipe, resolve_named, warn

TAU = 6.283185307179586
RAD_TO_DEG = 57.29577951308232
CONE_SOFTNESS = 5.0  # degrees eased at the view-cone edge


def _slope_selection(ng, gi, normal_z, loc):
    """Keep faces whose up-normal Z is within [Min Normal Z, Max Normal Z].

    Max defaults to 1.0 and uses "not greater than" so perfectly flat faces
    (Z = 1) still pass at the default; lowering it excludes the flats.
    """
    min_ok = math_node(ng, "GREATER_THAN", normal_z, gi.outputs["Min Normal Z"], loc)
    over_max = math_node(ng, "GREATER_THAN", normal_z, gi.outputs["Max Normal Z"],
                         (loc[0], loc[1] - 160))
    max_ok = math_node(ng, "SUBTRACT", 1.0, over_max, (loc[0] + 180, loc[1] - 160))
    return math_node(ng, "MULTIPLY", min_ok, max_ok, (loc[0] + 360, loc[1] - 80))


def _height_mask(ng, gi, pos_z, loc):
    """A smooth altitude band on world Z, mixed by Height Strength (0 = off)."""
    lo = math_node(ng, "SUBTRACT", gi.outputs["Height Min"], gi.outputs["Height Falloff"], loc)
    rising = smooth_falloff(ng, pos_z, lo, gi.outputs["Height Min"], (loc[0] + 180, loc[1]))
    hi = math_node(ng, "ADD", gi.outputs["Height Max"], gi.outputs["Height Falloff"],
                   (loc[0], loc[1] - 200))
    over = smooth_falloff(ng, pos_z, gi.outputs["Height Max"], hi, (loc[0] + 180, loc[1] - 200))
    falling = math_node(ng, "SUBTRACT", 1.0, over, (loc[0] + 360, loc[1] - 200))
    band = math_node(ng, "MULTIPLY", rising, falling, (loc[0] + 540, loc[1] - 100))
    return mix_float(ng, gi.outputs["Height Strength"], 1.0, band, (loc[0] + 720, loc[1]))


def _noise_mask(ng, gi, pos, loc):
    """Procedural patchy density for clumping, mixed by Noise Strength (0 = off).

    Noise Contrast narrows the transition around 0.5: 1 = hard patches, 0 = smooth.
    """
    fac = noise_field(ng, pos, gi.outputs["Noise Scale"], seed=gi.outputs["Noise Seed"],
                      location=loc)
    inv = math_node(ng, "SUBTRACT", 1.0, gi.outputs["Noise Contrast"], (loc[0], loc[1] - 220))
    half = math_node(ng, "MULTIPLY", 0.5, inv, (loc[0] + 180, loc[1] - 220))
    low = math_node(ng, "SUBTRACT", 0.5, half, (loc[0] + 360, loc[1] - 160))
    high = math_node(ng, "ADD", 0.5, half, (loc[0] + 360, loc[1] - 280))
    patch = smooth_falloff(ng, fac, low, high, (loc[0] + 540, loc[1] - 120))
    return mix_float(ng, gi.outputs["Noise Strength"], 1.0, patch, (loc[0] + 720, loc[1]))


def _child_order(collection):
    """The direct children Collection Info's Separate Children emits, in its order: the child
    collections first, then the objects, which is the order Blender builds that instance list in.
    Returns a list of names; the index of a name here is its Instance Index in the pick."""
    return [c.name for c in collection.children] + [o.name for o in collection.objects]


def _allowed_indices(collection, include, exclude):
    """(indices, warnings) for the assets a layer may pick from.

    An INCLUDE list is the whole allowance; an EXCLUDE list removes from the full pool. Both are
    resolved to Instance Index values here, at build time, because that is where the collection is in
    hand. A name that is not a direct child of the collection is a warning, not a silent no-op: the
    reason to reach for this is one specific bad asset, so failing to match it must not read as
    success. Returns (None, warnings) when every child is allowed (the graph then adds no filter and
    is byte-identical to before).
    """
    order = _child_order(collection)
    warnings = []
    for name in list(include or []) + list(exclude or []):
        if name not in order:
            warnings.append(f"asset {name!r} is not a direct child of collection "
                            f"{collection.name!r} (has: {', '.join(order) or 'nothing'})")
    if include:
        keep = [i for i, n in enumerate(order) if n in set(include)]
    else:
        keep = list(range(len(order)))
    if exclude:
        drop = set(exclude)
        keep = [i for i in keep if order[i] not in drop]
    if not keep:
        warnings.append(f"the asset filter leaves collection {collection.name!r} with nothing to "
                        f"scatter; ignored so the layer still builds")
        return None, warnings
    if len(keep) == len(order):
        return None, warnings
    return keep, warnings


def _pick_from(ng, instances, indices, loc):
    """Restrict a Separate-Children instance set to `indices` and return its geometry.

    Deletes the other instances on the INSTANCE domain rather than remapping the random pick, so
    Domain Size downstream reports the FILTERED count and the random index needs no arithmetic: the
    pick stays uniform over what is left, whatever is left.
    """
    nodes, links = ng.nodes, ng.links
    index = nodes.new("GeometryNodeInputIndex")
    index.location = (loc[0], loc[1] - 180)
    keep = None
    for n, i in enumerate(indices):
        # COMPARE is |a - b| < Epsilon. Set the epsilon rather than trusting its default: these are
        # integer indices, so half a step separates every pair exactly.
        eq = nodes.new("ShaderNodeMath")
        eq.operation = "COMPARE"
        eq.location = (loc[0] + 180, loc[1] - 180 - n * 120)
        links.new(index.outputs["Index"], eq.inputs[0])
        eq.inputs[1].default_value = float(i)
        eq.inputs[2].default_value = 0.5
        keep = eq.outputs["Value"] if keep is None else math_node(
            ng, "MAXIMUM", keep, eq.outputs["Value"], (loc[0] + 360, loc[1] - 180 - n * 120))
    drop = math_node(ng, "SUBTRACT", 1.0, keep, (loc[0] + 540, loc[1] - 180))
    delete = nodes.new("GeometryNodeDeleteGeometry")
    delete.domain = "INSTANCE"
    delete.mode = "ALL"
    delete.location = (loc[0] + 720, loc[1])
    links.new(instances, delete.inputs["Geometry"])
    links.new(drop, delete.inputs["Selection"])
    return delete.outputs["Geometry"]


def _camera_cull(ng, camera, gi, pos, loc):
    """Keep points near the camera and inside its forward cone; 0..1 field.

    Distance cull fades over Cull Falloff past Camera Distance; the cone fades over
    a small fixed softness past Camera Cone (a half-angle in degrees). Approximates
    the view frustum without coupling to the camera's exact FOV. Updates as the
    camera moves (Object Info re-evaluates).
    """
    info = ng.nodes.new("GeometryNodeObjectInfo")
    info.transform_space = "ORIGINAL"  # world location/rotation
    info.location = loc
    info.inputs["Object"].default_value = camera

    # Camera forward: rotate local -Z by the camera's world rotation.
    fwd = ng.nodes.new("ShaderNodeVectorRotate")
    fwd.rotation_type = "EULER_XYZ"
    fwd.location = (loc[0] + 200, loc[1] - 200)
    fwd.inputs["Vector"].default_value = (0.0, 0.0, -1.0)
    ng.links.new(info.outputs["Rotation"], fwd.inputs["Rotation"])

    # Vector camera -> point, its length, and its direction.
    vec = ng.nodes.new("ShaderNodeVectorMath")
    vec.operation = "SUBTRACT"
    vec.location = (loc[0] + 200, loc[1])
    ng.links.new(pos, vec.inputs[0])
    ng.links.new(info.outputs["Location"], vec.inputs[1])

    length = ng.nodes.new("ShaderNodeVectorMath")
    length.operation = "LENGTH"
    length.location = (loc[0] + 380, loc[1] + 120)
    ng.links.new(vec.outputs["Vector"], length.inputs[0])

    norm = ng.nodes.new("ShaderNodeVectorMath")
    norm.operation = "NORMALIZE"
    norm.location = (loc[0] + 380, loc[1] - 60)
    ng.links.new(vec.outputs["Vector"], norm.inputs[0])

    dot = ng.nodes.new("ShaderNodeVectorMath")
    dot.operation = "DOT_PRODUCT"
    dot.location = (loc[0] + 560, loc[1] - 120)
    ng.links.new(norm.outputs["Vector"], dot.inputs[0])
    ng.links.new(fwd.outputs["Vector"], dot.inputs[1])

    # Clamp the dot into [-1, 1] before arccos, then to degrees.
    cos_c = math_node(ng, "MINIMUM", dot.outputs["Value"], 1.0, (loc[0] + 740, loc[1] - 120))
    cos_c = math_node(ng, "MAXIMUM", cos_c, -1.0, (loc[0] + 900, loc[1] - 120))
    angle = math_node(ng, "ARCCOSINE", cos_c, location=(loc[0] + 1060, loc[1] - 120))
    angle_deg = math_node(ng, "MULTIPLY", angle, RAD_TO_DEG, (loc[0] + 1220, loc[1] - 120))

    far = math_node(ng, "ADD", gi.outputs["Camera Distance"], gi.outputs["Cull Falloff"],
                    (loc[0] + 560, loc[1] + 200))
    dist_out = smooth_falloff(ng, length.outputs["Value"], gi.outputs["Camera Distance"], far,
                              (loc[0] + 740, loc[1] + 160))
    dist_mask = math_node(ng, "SUBTRACT", 1.0, dist_out, (loc[0] + 920, loc[1] + 160))

    cone_hi = math_node(ng, "ADD", gi.outputs["Camera Cone"], CONE_SOFTNESS,
                        (loc[0] + 1220, loc[1] + 40))
    cone_out = smooth_falloff(ng, angle_deg, gi.outputs["Camera Cone"], cone_hi,
                              (loc[0] + 1400, loc[1]))
    cone_mask = math_node(ng, "SUBTRACT", 1.0, cone_out, (loc[0] + 1580, loc[1]))
    return math_node(ng, "MULTIPLY", dist_mask, cone_mask, (loc[0] + 1760, loc[1] + 80))


@recipe("scatter")
def build(ng, out, params: dict):
    # Resolved through `resolve_named` rather than `bpy.data.*.get`, so a typo'd name is a warning on
    # the op result instead of a layer that builds, reports success and scatters nothing.
    emitter = resolve_named("objects", params.get("emitter", ""), what="emitter object")
    assets = resolve_named("collections", params.get("assets", ""), what="asset collection")
    camera = resolve_named("objects", params.get("camera", ""), what="camera object")
    vgroup = params.get("vgroup", "")
    curve_mode = params.get("curve_mode", "none")  # none / clear / keep, off a curve mask band
    curve_attr = params.get("curve_attr", "bbt_curve_mask")  # which mask: the band, or bbt_curve_edge

    gi = ng.nodes.new("NodeGroupInput")
    gi.location = (-1400, 0)

    add_input(ng, "Density", "NodeSocketFloat", float(params.get("density", 5.0)), 0.0)
    add_input(ng, "Distance Min", "NodeSocketFloat", float(params.get("distance_min", 0.3)), 0.0)
    add_input(ng, "Seed", "NodeSocketInt", int(params.get("seed", 0)))
    add_input(ng, "Min Scale", "NodeSocketFloat", float(params.get("min_scale", 0.8)), 0.0)
    add_input(ng, "Max Scale", "NodeSocketFloat", float(params.get("max_scale", 1.2)), 0.0)
    add_input(ng, "Min Normal Z", "NodeSocketFloat", float(params.get("min_normal_z", 0.5)))
    add_input(ng, "Max Normal Z", "NodeSocketFloat", float(params.get("max_normal_z", 1.0)))
    add_input(ng, "Height Min", "NodeSocketFloat", float(params.get("height_min", -1000.0)))
    add_input(ng, "Height Max", "NodeSocketFloat", float(params.get("height_max", 1000.0)))
    add_input(ng, "Height Falloff", "NodeSocketFloat", float(params.get("height_falloff", 5.0)), 0.0)
    add_input(ng, "Height Strength", "NodeSocketFloat", float(params.get("height_strength", 0.0)), 0.0, 1.0)
    add_input(ng, "Noise Scale", "NodeSocketFloat", float(params.get("noise_scale", 0.15)), 0.0)
    add_input(ng, "Noise Contrast", "NodeSocketFloat", float(params.get("noise_contrast", 0.5)), 0.0, 1.0)
    add_input(ng, "Noise Seed", "NodeSocketInt", int(params.get("noise_seed", 0)))
    add_input(ng, "Noise Strength", "NodeSocketFloat", float(params.get("noise_strength", 0.0)), 0.0, 1.0)
    # Sink: metres along world Z, applied to the instances after placement. Negative buries the
    # base, which is what a generated trunk's wide root flare needs on sloped ground.
    add_input(ng, "Z Offset", "NodeSocketFloat", float(params.get("z_offset", 0.0)))
    if vgroup:
        add_input(ng, "Paint Strength", "NodeSocketFloat", float(params.get("paint_strength", 1.0)), 0.0, 1.0)
    if camera is not None:
        add_input(ng, "Camera Distance", "NodeSocketFloat", float(params.get("camera_distance", 80.0)), 0.0)
        add_input(ng, "Camera Cone", "NodeSocketFloat", float(params.get("camera_cone", 60.0)), 0.0, 180.0)
        add_input(ng, "Cull Falloff", "NodeSocketFloat", float(params.get("cull_falloff", 8.0)), 0.0)

    nodes, links = ng.nodes, ng.links
    seed = gi.outputs["Seed"]

    geometry = object_geometry(ng, emitter, (-1200, 300))

    # Slope band drives the distribution Selection.
    normal = nodes.new("GeometryNodeInputNormal")
    normal.location = (-1200, -200)
    nsep = nodes.new("ShaderNodeSeparateXYZ")
    nsep.location = (-1020, -200)
    links.new(normal.outputs["Normal"], nsep.inputs[0])
    selection = _slope_selection(ng, gi, nsep.outputs["Z"], (-840, -160))

    # Density-factor masks (each 0..1), multiplied together.
    pos = position(ng, (-1200, -520))
    psep = nodes.new("ShaderNodeSeparateXYZ")
    psep.location = (-1020, -520)
    links.new(pos, psep.inputs[0])

    factor = _height_mask(ng, gi, psep.outputs["Z"], (-840, -520))
    factor = math_node(ng, "MULTIPLY", factor,
                       _noise_mask(ng, gi, pos, (-840, -820)), (400, -600))

    # Curve band (BobSplines C4): read the terrain's baked bbt_curve_mask (0..1, 1 on a path).
    # clear -> multiply by (1 - mask) so density drops to zero along the trail; keep -> multiply by
    # the mask so scatter stays only in the band (reeds along a bank). Absent attribute reads 0:
    # clear then leaves density untouched, keep correctly yields nothing (no curve, no band).
    if curve_mode in ("clear", "keep"):
        cmask = nodes.new("GeometryNodeInputNamedAttribute")
        cmask.data_type = "FLOAT"
        cmask.location = (-840, 560)
        cmask.inputs["Name"].default_value = curve_attr
        band = cmask.outputs["Attribute"]
        if curve_mode == "clear":
            band = math_node(ng, "SUBTRACT", 1.0, band, (-640, 560))
        factor = math_node(ng, "MULTIPLY", factor, band, (580, -400))

    # Vertex-group paint mask: the emitter's group weight (via Object Info) scales
    # density where painted. Mixed by Paint Strength.
    if vgroup:
        named = nodes.new("GeometryNodeInputNamedAttribute")
        named.data_type = "FLOAT"
        named.location = (-840, -1120)
        named.inputs["Name"].default_value = vgroup
        paint = mix_float(ng, gi.outputs["Paint Strength"], 1.0, named.outputs["Attribute"],
                          (-620, -1120))
        factor = math_node(ng, "MULTIPLY", factor, paint, (760, -400))

    # Camera culling: drop density outside the camera's distance and view cone.
    if camera is not None:
        cull = _camera_cull(ng, camera, gi, pos, (-840, -1500))
        factor = math_node(ng, "MULTIPLY", factor, cull, (940, -400))

    # Poisson distribution.
    dist = nodes.new("GeometryNodeDistributePointsOnFaces")
    dist.distribute_method = "POISSON"
    dist.location = (1140, 100)
    links.new(geometry, dist.inputs["Mesh"])
    links.new(selection, dist.inputs["Selection"])
    links.new(gi.outputs["Distance Min"], dist.inputs["Distance Min"])
    links.new(gi.outputs["Density"], dist.inputs["Density Max"])
    links.new(factor, dist.inputs["Density Factor"])
    links.new(seed, dist.inputs["Seed"])

    # Asset instances from the collection, one separate child per instance.
    coll = nodes.new("GeometryNodeCollectionInfo")
    coll.location = (1140, -200)
    if assets is not None:
        coll.inputs["Collection"].default_value = assets
    coll.inputs["Separate Children"].default_value = True
    coll.inputs["Reset Children"].default_value = True

    # Per-asset filter: drop the excluded children before the pick, so one bad member of a shared
    # BOB_Assets_<Kind> pool can be left out of THIS layer without editing the collection.
    pool = coll.outputs["Instances"]
    if assets is not None:
        keep, filter_warnings = _allowed_indices(assets, params.get("assets_include"),
                                                 params.get("assets_exclude"))
        for message in filter_warnings:
            warn(message)
        if keep is not None:
            pool = _pick_from(ng, pool, keep, (1140, -560))

    # Random pick index in [0, instance_count - 1], counted over the FILTERED pool.
    domain = nodes.new("GeometryNodeAttributeDomainSize")
    domain.component = "INSTANCES"
    domain.location = (1320, -360)
    links.new(pool, domain.inputs["Geometry"])
    max_index = math_node(ng, "SUBTRACT", domain.outputs["Instance Count"], 1, (1500, -360))
    index = random_value(ng, "INT", 0, max_index, seed, (1680, -360))

    scale = random_value(ng, "FLOAT", gi.outputs["Min Scale"], gi.outputs["Max Scale"], seed, (1460, 300))

    instance = nodes.new("GeometryNodeInstanceOnPoints")
    instance.location = (1580, 100)
    links.new(dist.outputs["Points"], instance.inputs["Points"])
    links.new(pool, instance.inputs["Instance"])
    instance.inputs["Pick Instance"].default_value = True
    links.new(index, instance.inputs["Instance Index"])
    # align "normal" tilts instances to the surface (rocks, grass); "up" leaves
    # them standing (trees). Random Z spin is added below either way.
    if params.get("align", "up") == "normal":
        links.new(dist.outputs["Rotation"], instance.inputs["Rotation"])
    links.new(scale, instance.inputs["Scale"])

    # Random spin about Z, in the instance's local space.
    spin = random_value(ng, "FLOAT", 0.0, TAU, seed, (1580, 380))
    spin_vec = nodes.new("ShaderNodeCombineXYZ")
    spin_vec.location = (1760, 380)
    links.new(spin, spin_vec.inputs["Z"])
    rotate = nodes.new("GeometryNodeRotateInstances")
    rotate.location = (1940, 100)
    links.new(instance.outputs["Instances"], rotate.inputs["Instances"])
    links.new(spin_vec.outputs["Vector"], rotate.inputs["Rotation"])
    rotate.inputs["Local Space"].default_value = True

    # Sink along world Z. Global space (Local Space off), so the offset is metres of elevation
    # regardless of how the instance was tilted to the surface normal: a rock aligned to a 40-degree
    # slope should still sink DOWN, not into its own tilted -Z.
    sink_vec = nodes.new("ShaderNodeCombineXYZ")
    sink_vec.location = (2120, 380)
    links.new(gi.outputs["Z Offset"], sink_vec.inputs["Z"])
    translate = nodes.new("GeometryNodeTranslateInstances")
    translate.location = (2120, 100)
    links.new(rotate.outputs["Instances"], translate.inputs["Instances"])
    links.new(sink_vec.outputs["Vector"], translate.inputs["Translation"])
    translate.inputs["Local Space"].default_value = False

    links.new(translate.outputs["Instances"], out.inputs["Geometry"])
