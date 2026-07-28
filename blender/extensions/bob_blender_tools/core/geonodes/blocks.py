"""Composable building blocks for geometry-node recipes.

Each block adds a small sub-graph and returns the socket a recipe wires onward.
This is the shared layer recipes compose, and the set that would become an op
vocabulary if we ever lift composition to the contract.
"""

import bpy

from .scaffold import add_input


def _plug(ng, socket, value):
    """Link value into socket if it is a socket, else set it as a default."""
    if isinstance(value, (int, float)):
        socket.default_value = value
    else:
        ng.links.new(value, socket)


def math_node(ng, op, a, b=None, location=(0, 0)):
    """A Math node. a and b are each a socket or a constant. Return the output."""
    node = ng.nodes.new("ShaderNodeMath")
    node.operation = op
    node.location = location
    _plug(ng, node.inputs[0], a)
    if b is not None:
        _plug(ng, node.inputs[1], b)
    return node.outputs["Value"]


def mix_float(ng, factor, a, b, location=(0, 0)):
    """Linear blend a*(1-factor) + b*factor."""
    inv = math_node(ng, "SUBTRACT", 1.0, factor, (location[0], location[1] + 160))
    am = math_node(ng, "MULTIPLY", a, inv, (location[0] + 180, location[1] + 160))
    bm = math_node(ng, "MULTIPLY", b, factor, (location[0] + 180, location[1] - 160))
    return math_node(ng, "ADD", am, bm, (location[0] + 360, location[1]))


def grid_source(ng, group_in, size, resolution):
    """Add Size and Resolution inputs and a Grid. Return the mesh socket."""
    add_input(ng, "Size", "NodeSocketFloat", float(size), 0.0)
    add_input(ng, "Resolution", "NodeSocketInt", int(resolution), 2)
    grid = ng.nodes.new("GeometryNodeMeshGrid")
    grid.location = (-1000, 200)
    ng.links.new(group_in.outputs["Size"], grid.inputs["Size X"])
    ng.links.new(group_in.outputs["Size"], grid.inputs["Size Y"])
    ng.links.new(group_in.outputs["Resolution"], grid.inputs["Vertices X"])
    ng.links.new(group_in.outputs["Resolution"], grid.inputs["Vertices Y"])
    return grid.outputs["Mesh"]


def position(ng, location=(-1200, -200)):
    """The per-point position vector."""
    node = ng.nodes.new("GeometryNodeInputPosition")
    node.location = location
    return node.outputs["Position"]


def radial_distance(ng, location=(-600, -200)):
    """Distance of each point from the origin in the XY plane. Return a scalar."""
    length = ng.nodes.new("ShaderNodeVectorMath")
    length.operation = "LENGTH"
    length.location = (location[0] + 200, location[1])
    ng.links.new(position(ng, location), length.inputs[0])
    return length.outputs["Value"]


def noise_field(ng, pos, scale, detail=2.0, roughness=0.5, seed=None, location=(-760, -200)):
    """A 4D fractal noise sampled at pos. Return the Fac scalar (roughly 0..1)."""
    node = ng.nodes.new("ShaderNodeTexNoise")
    node.noise_dimensions = "4D"
    node.location = location
    ng.links.new(pos, node.inputs["Vector"])
    _plug(ng, node.inputs["Scale"], scale)
    _plug(ng, node.inputs["Detail"], detail)
    _plug(ng, node.inputs["Roughness"], roughness)
    if seed is not None:
        ng.links.new(seed, node.inputs["W"])
    return node.outputs["Fac"]


def domain_warp(ng, pos, strength, scale, seed, location=(-1000, -200)):
    """Warp pos by a low-frequency noise. Return the warped position vector.

    This is what turns regular noise into organic, flowing terrain.
    """
    noise = ng.nodes.new("ShaderNodeTexNoise")
    noise.noise_dimensions = "4D"
    noise.location = location
    ng.links.new(pos, noise.inputs["Vector"])
    _plug(ng, noise.inputs["Scale"], scale)
    ng.links.new(math_node(ng, "ADD", seed, 19.0, (location[0], location[1] - 200)), noise.inputs["W"])

    centered = ng.nodes.new("ShaderNodeVectorMath")
    centered.operation = "SUBTRACT"
    centered.location = (location[0] + 200, location[1])
    ng.links.new(noise.outputs["Color"], centered.inputs[0])
    centered.inputs[1].default_value = (0.5, 0.5, 0.5)

    scaled = ng.nodes.new("ShaderNodeVectorMath")
    scaled.operation = "SCALE"
    scaled.location = (location[0] + 380, location[1])
    ng.links.new(centered.outputs["Vector"], scaled.inputs[0])
    _plug(ng, scaled.inputs["Scale"], strength)

    added = ng.nodes.new("ShaderNodeVectorMath")
    added.operation = "ADD"
    added.location = (location[0] + 560, location[1])
    ng.links.new(pos, added.inputs[0])
    ng.links.new(scaled.outputs["Vector"], added.inputs[1])
    return added.outputs["Vector"]


def ridged(ng, fac, location=(-560, -200)):
    """Fold a 0..1 field into sharp ridges: 1 - abs(2*fac - 1)."""
    doubled = math_node(ng, "MULTIPLY", fac, 2.0, location)
    shifted = math_node(ng, "SUBTRACT", doubled, 1.0, (location[0] + 180, location[1]))
    folded = math_node(ng, "ABSOLUTE", shifted, location=(location[0] + 360, location[1]))
    return math_node(ng, "SUBTRACT", 1.0, folded, (location[0] + 540, location[1]))


def displace_z(ng, geometry, z_scalar, location=(600, 0)):
    """Offset each point in Z by z_scalar. Return the geometry socket."""
    combine = ng.nodes.new("ShaderNodeCombineXYZ")
    combine.location = (location[0] - 200, location[1] - 150)
    ng.links.new(z_scalar, combine.inputs["Z"])
    set_position = ng.nodes.new("GeometryNodeSetPosition")
    set_position.location = location
    ng.links.new(geometry, set_position.inputs["Geometry"])
    ng.links.new(combine.outputs["Vector"], set_position.inputs["Offset"])
    return set_position.outputs["Geometry"]


def object_geometry(ng, emitter, location=(-900, 0)):
    """Read another object's geometry via Object Info. Return the geometry socket.

    emitter is either an Object datablock (set as the node default) or a socket
    to wire. Blender 5.x GN modifiers no longer store object inputs, so scatter
    sets the emitter on the node itself.
    """
    info = ng.nodes.new("GeometryNodeObjectInfo")
    info.transform_space = "RELATIVE"
    info.location = location
    if isinstance(emitter, bpy.types.Object):
        info.inputs["Object"].default_value = emitter
    elif emitter is not None:
        ng.links.new(emitter, info.inputs["Object"])
    return info.outputs["Geometry"]


def _flatten_xy(ng, vector, location=(0, 0)):
    """Drop the Z of a vector to 0, so distances measure in the XY plane."""
    sep = ng.nodes.new("ShaderNodeSeparateXYZ")
    sep.location = location
    ng.links.new(vector, sep.inputs[0])
    flat = ng.nodes.new("ShaderNodeCombineXYZ")
    flat.location = (location[0] + 180, location[1])
    ng.links.new(sep.outputs["X"], flat.inputs["X"])
    ng.links.new(sep.outputs["Y"], flat.inputs["Y"])
    return flat.outputs["Vector"]


def _curve_meshes(ng, path_obj, location, stores=()):
    """Turn a path curve into (draped_mesh, flat_mesh).

    draped_mesh keeps the curve's Z (the graded trail profile); flat_mesh is the
    same wire mesh flattened to z = 0 so horizontal distance and the nearest
    point measure in the XY plane. Both share topology, so an index from one
    reads the matching vertex on the other.

    stores: optional (attr_name, value_socket, data_type) tuples stored on the curve (POINT domain)
    BEFORE Curve to Mesh, so per-point curve fields (end distance for the taper, tangent for the
    embankment) ride onto both meshes and can be Sample-Nearest'd at the grid. Empty by default
    (curve_distance needs none).
    """
    geo = object_geometry(ng, path_obj, location)
    sy = location[1] + 240
    for name, value, dtype in stores:
        store = ng.nodes.new("GeometryNodeStoreNamedAttribute")
        store.data_type = dtype
        store.domain = "POINT"
        store.location = (location[0], sy)
        sy += 200
        ng.links.new(geo, store.inputs["Geometry"])
        store.inputs["Name"].default_value = name
        ng.links.new(value, store.inputs["Value"])
        geo = store.outputs["Geometry"]
    # Curve to Mesh evaluates the curve at its resolution_u (subdivisions per segment); the Paths
    # panel bumps that on the datablock (see splines_panel _build_curve_overlay) so the polyline is
    # dense and the carved bench reads smooth. A coarse curve evaluates to a few straight segments
    # whose slope kinks at each junction facet the bench into steps.
    to_mesh = ng.nodes.new("GeometryNodeCurveToMesh")
    to_mesh.location = (location[0] + 200, location[1])
    ng.links.new(geo, to_mesh.inputs["Curve"])

    flat_curve = _flatten_xy(ng, position(ng, (location[0] + 200, location[1] - 220)),
                             (location[0] + 380, location[1] - 220))
    set_pos = ng.nodes.new("GeometryNodeSetPosition")
    set_pos.location = (location[0] + 560, location[1])
    ng.links.new(to_mesh.outputs["Mesh"], set_pos.inputs["Geometry"])
    ng.links.new(flat_curve, set_pos.inputs["Position"])
    return to_mesh.outputs["Mesh"], set_pos.outputs["Geometry"]


def _sample_grid_flat(ng, location):
    """This point's position flattened to z = 0, for XY-plane proximity."""
    return _flatten_xy(ng, position(ng, (location[0], location[1] - 40)),
                       (location[0] + 180, location[1] - 40))


def curve_distance(ng, path_obj, location=(-900, -500)):
    """Horizontal distance from each point to a path curve.

    Returns (distance, near_pos): the XY distance to the nearest curve edge and
    the nearest point on the flattened curve, whose XY marks the trail centreline.
    """
    _, flat = _curve_meshes(ng, path_obj, location)
    prox = ng.nodes.new("GeometryNodeProximity")
    prox.target_element = "EDGES"
    prox.location = (location[0] + 760, location[1])
    ng.links.new(flat, prox.inputs["Geometry"])
    ng.links.new(_sample_grid_flat(ng, (location[0] + 560, location[1] - 300)),
                 prox.inputs["Sample Position"])
    return prox.outputs["Distance"], prox.outputs["Position"]


def _end_dist_field(ng, location):
    """A curve field: per-point arclength distance to the NEAREST spline end (0 at both ends,
    rising toward the middle). Evaluated when stored on the curve. Drives the endpoint taper
    so the band fades in over the last stretch instead of fanning into a radial cap past the tip."""
    sp = ng.nodes.new("GeometryNodeSplineParameter")
    sp.location = location
    sl = ng.nodes.new("GeometryNodeSplineLength")
    sl.location = (location[0], location[1] - 180)
    from_end = math_node(ng, "SUBTRACT", sl.outputs["Length"], sp.outputs["Length"],
                         (location[0] + 200, location[1] - 90))
    return math_node(ng, "MINIMUM", sp.outputs["Length"], from_end, (location[0] + 380, location[1]))


def _tangent_field(ng, location):
    """The curve's unit tangent at each point (a curve field), stored on the curve for the side
    computation. Kept internal: no consumer needs the raw tangent yet, only its sign about the
    centreline (side), so it is not surfaced in curve_field's tuple (docs/SPLINES.md note on adding
    field outputs only with their consumer)."""
    t = ng.nodes.new("GeometryNodeInputTangent")
    t.location = location
    return t.outputs["Tangent"]


def _sample_curve_attr(ng, flat_mesh, grid_flat, name, data_type, location):
    """Read a per-point attribute stored on the flat curve mesh at the vertex NEAREST each grid
    point (Sample Nearest -> Sample Index), bringing a curve field (end distance, tangent) onto the
    terrain grid. proximity gives the distance/centreline but no index, so this is a second nearest
    solve on the same flat mesh."""
    near = ng.nodes.new("GeometryNodeSampleNearest")
    near.domain = "POINT"
    near.location = location
    ng.links.new(flat_mesh, near.inputs["Geometry"])
    ng.links.new(grid_flat, near.inputs["Sample Position"])
    attr = ng.nodes.new("GeometryNodeInputNamedAttribute")
    attr.data_type = data_type
    attr.location = (location[0], location[1] - 180)
    attr.inputs["Name"].default_value = name
    samp = ng.nodes.new("GeometryNodeSampleIndex")
    samp.data_type = data_type
    samp.domain = "POINT"
    samp.location = (location[0] + 200, location[1] - 60)
    ng.links.new(flat_mesh, samp.inputs["Geometry"])
    ng.links.new(attr.outputs["Attribute"], samp.inputs["Value"])
    ng.links.new(near.outputs["Index"], samp.inputs["Index"])
    return samp.outputs["Value"]


def curve_field(ng, path_obj, location=(-900, -500)):
    """The shared per-point curve field (docs/SPLINES.md 4.2): (distance, near_pos, path_z,
    end_dist, side, tangent).

    - distance: XY distance from each point to the curve.
    - near_pos: the nearest point on the flattened (z = 0) curve, whose XY is the centreline.
    - path_z:   the draped curve's height at the nearest point, INTERPOLATED along the curve so it
      grades smoothly (see drape_curve for how the curve gets its draped Z). The follow-family
      overlay prefers a live terrain raycast (the live re-drape) and keeps this only as the off-mesh fallback; the
      IMPOSE family (rivers) uses path_z directly as the water/bed reference, and the water ribbon
      (curve_water) sits a fixed depth below the SAME path_z, so bed and surface stay in harmony.
    - end_dist: arclength distance to the nearest spline end at the nearest curve vertex, for the
      endpoint taper. Stored on the curve, carried through Curve to Mesh, sampled at the grid.
    - side:     sign of the 2D cross product tangent x (grid - centreline), i.e. -1 / 0 / +1 for the
      left / on / right of the curve, for the asymmetric embankment (the road cross-section).
    - tangent:  the curve's 3D unit tangent at the nearest vertex (Sample Nearest -> Sample Index).
      Its XY gives the downstream flow direction and its Z the local descent (rapids); consumed by
      the river water ribbon (the water channel). Sampled by the same reliable index read that drives `side`.

    Generalises curve_distance (distance + near_pos), so a consumer that needs several of these (the
    curve overlay) solves proximity ONCE rather than per effect (docs/SPLINES.md 9 #4).
    """
    end_dist_curve = _end_dist_field(ng, (location[0] - 320, location[1] + 320))
    tangent_curve = _tangent_field(ng, (location[0] - 320, location[1] + 120))
    draped, flat = _curve_meshes(ng, path_obj, location,
                                 stores=(("bbt_end_dist", end_dist_curve, "FLOAT"),
                                         ("bbt_tangent", tangent_curve, "FLOAT_VECTOR")))
    grid_flat = _sample_grid_flat(ng, (location[0] + 560, location[1] - 300))

    # Distance + centreline in the XY plane (flat curve). EDGES gives the nearest point ON the
    # edge (interpolated), so the band width is horizontal and continuous, not snapped to a vertex.
    prox = ng.nodes.new("GeometryNodeProximity")
    prox.target_element = "EDGES"
    prox.location = (location[0] + 760, location[1])
    ng.links.new(flat, prox.inputs["Geometry"])
    ng.links.new(grid_flat, prox.inputs["Sample Position"])

    # path_z: the nearest point on the DRAPED curve's edges, sampled at the grid point's real
    # position (near the path terrain_z ~= curve_z). Its Z is INTERPOLATED along the edge, so the
    # bench grades smoothly. Reading a vertex Z with Sample Nearest + Sample Index terraced the
    # bench -- one flat plateau per curve vertex, a step at each boundary; the edge Position on the
    # 3D curve has no such steps.
    proxz = ng.nodes.new("GeometryNodeProximity")
    proxz.target_element = "EDGES"
    proxz.location = (location[0] + 760, location[1] - 260)
    ng.links.new(draped, proxz.inputs["Geometry"])
    ng.links.new(position(ng, (location[0] + 580, location[1] - 260)), proxz.inputs["Sample Position"])
    zsep = ng.nodes.new("ShaderNodeSeparateXYZ")
    zsep.location = (location[0] + 960, location[1] - 260)
    ng.links.new(proxz.outputs["Position"], zsep.inputs[0])

    end_dist = _sample_curve_attr(ng, flat, grid_flat, "bbt_end_dist", "FLOAT",
                                  (location[0] + 760, location[1] - 560))

    # side: sign of the 2D cross tangent x (grid - centreline). tangent sampled at the nearest curve
    # vertex; v = grid - centreline, both flattened to z = 0. cross_z = tx*vy - ty*vx.
    tangent = _sample_curve_attr(ng, flat, grid_flat, "bbt_tangent", "FLOAT_VECTOR",
                                 (location[0] + 760, location[1] - 780))
    tsep = ng.nodes.new("ShaderNodeSeparateXYZ")
    tsep.location = (location[0] + 1160, location[1] - 780)
    ng.links.new(tangent, tsep.inputs[0])
    v = ng.nodes.new("ShaderNodeVectorMath")
    v.operation = "SUBTRACT"
    v.location = (location[0] + 1160, location[1] - 980)
    ng.links.new(grid_flat, v.inputs[0])
    ng.links.new(prox.outputs["Position"], v.inputs[1])
    vsep = ng.nodes.new("ShaderNodeSeparateXYZ")
    vsep.location = (location[0] + 1340, location[1] - 980)
    ng.links.new(v.outputs["Vector"], vsep.inputs[0])
    txvy = math_node(ng, "MULTIPLY", tsep.outputs["X"], vsep.outputs["Y"], (location[0] + 1520, location[1] - 820))
    tyvx = math_node(ng, "MULTIPLY", tsep.outputs["Y"], vsep.outputs["X"], (location[0] + 1520, location[1] - 980))
    cross = math_node(ng, "SUBTRACT", txvy, tyvx, (location[0] + 1700, location[1] - 900))
    side = math_node(ng, "SIGN", cross, None, (location[0] + 1880, location[1] - 900))

    return prox.outputs["Distance"], prox.outputs["Position"], zsep.outputs["Z"], end_dist, side, tangent


def smooth_falloff(ng, value, inner, outer, location=(0, 0)):
    """Smoothstep 0..1: 0 where value <= inner, 1 where value >= outer.

    As a path mask over curve_distance: 0 on the trail, 1 on untouched ground.
    """
    node = ng.nodes.new("ShaderNodeMapRange")
    node.interpolation_type = "SMOOTHSTEP"
    node.location = location
    _plug(ng, node.inputs["From Min"], inner)
    _plug(ng, node.inputs["From Max"], outer)
    ng.links.new(value, node.inputs["Value"])
    return node.outputs["Result"]


WIDTH_NOISE_SCALE = 0.05  # base meander frequency (cycles/m) for the shared river width variation


def width_multiplier(ng, near, width_var, location=(0, 0)):
    """The shared width-variation multiplier for a river channel (docs/SPLINES.md 7, issue 1):
    1 +/- `width_var`, from a two-octave low-frequency noise sampled at the centreline `near`
    (whose Z is 0, so the noise is planar). Used by BOTH curve_water (to widen the swept water
    ribbon) and curve_overlay (to widen the carved bench) so the bed and the surface meander in
    LOCKSTEP -- the width model is shared, not cosmetic on the ribbon. `width_var` 0 -> a constant
    multiplier of 1 (the old dead-parallel strip), so non-river roles are unaffected.

    A plain Perlin Fac hugs 0.5, so the raw swing is weak; the two octaves are centred to [-1, 1],
    blended 0.7 / 0.3 (a slow meander plus a finer width wobble that breaks the ruled bank), then a
    contrast gain pushes the signal toward the extremes and clamps, so `width_var` reads as a true
    fraction. Floored at 0.15 so the channel never collapses or inverts."""
    lx, ly = location

    def _octave(scale, y):
        n = ng.nodes.new("ShaderNodeTexNoise")
        n.location = (lx, y)
        ng.links.new(near, n.inputs["Vector"])
        n.inputs["Scale"].default_value = scale
        c = math_node(ng, "SUBTRACT", math_node(ng, "MULTIPLY", n.outputs["Fac"], 2.0, (lx + 180, y)),
                      1.0, (lx + 360, y))  # centre Fac 0..1 to [-1, 1]
        return c

    o1 = _octave(WIDTH_NOISE_SCALE, ly)
    o2 = _octave(WIDTH_NOISE_SCALE * 3.3, ly - 220)
    mixed = math_node(ng, "ADD", math_node(ng, "MULTIPLY", o1, 0.7, (lx + 540, ly)),
                      math_node(ng, "MULTIPLY", o2, 0.3, (lx + 540, ly - 220)), (lx + 720, ly - 110))
    clamp = ng.nodes.new("ShaderNodeClamp")
    clamp.location = (lx + 1080, ly - 110)
    ng.links.new(math_node(ng, "MULTIPLY", mixed, 2.2, (lx + 900, ly - 110)), clamp.inputs["Value"])
    clamp.inputs["Min"].default_value = -1.0
    clamp.inputs["Max"].default_value = 1.0
    scaled = math_node(ng, "MULTIPLY", width_var, clamp.outputs["Result"], (lx + 1260, ly - 40))
    return math_node(ng, "MAXIMUM", math_node(ng, "ADD", 1.0, scaled, (lx + 1440, ly)), 0.15,
                     (lx + 1620, ly))


def random_value(ng, data_type, min_value, max_value, seed=None, location=(0, 0)):
    """A Random Value node. Setting data_type reduces the sockets to that type,
    so Min, Max, Value, and Seed are unambiguous by name."""
    node = ng.nodes.new("FunctionNodeRandomValue")
    node.data_type = data_type
    node.location = location
    _plug(ng, node.inputs["Min"], min_value)
    _plug(ng, node.inputs["Max"], max_value)
    if seed is not None:
        ng.links.new(seed, node.inputs["Seed"])
    return node.outputs["Value"]
