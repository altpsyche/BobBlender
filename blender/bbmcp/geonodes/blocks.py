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


def _curve_meshes(ng, path_obj, location):
    """Turn a path curve into (draped_mesh, flat_mesh).

    draped_mesh keeps the curve's Z (the graded trail profile); flat_mesh is the
    same wire mesh flattened to z = 0 so horizontal distance and the nearest
    point measure in the XY plane. Both share topology, so an index from one
    reads the matching vertex on the other.
    """
    curve = object_geometry(ng, path_obj, location)
    to_mesh = ng.nodes.new("GeometryNodeCurveToMesh")
    to_mesh.location = (location[0] + 200, location[1])
    ng.links.new(curve, to_mesh.inputs["Curve"])

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


def curve_path_sample(ng, path_obj, location=(-900, -500)):
    """Horizontal distance to a draped path curve, plus its height there.

    Returns (distance, path_z): the XY distance to the nearest curve edge, and
    the curve's own Z at the nearest curve vertex. Because a draped curve carries
    a smooth grade in Z, path_z is a clean trail height, not the terrain's fine
    relief. Used by heightmap_terrain to level a bench along the trail.
    """
    draped, flat = _curve_meshes(ng, path_obj, location)
    sample = _sample_grid_flat(ng, (location[0] + 560, location[1] - 300))

    prox = ng.nodes.new("GeometryNodeProximity")
    prox.target_element = "EDGES"
    prox.location = (location[0] + 760, location[1])
    ng.links.new(flat, prox.inputs["Geometry"])
    ng.links.new(sample, prox.inputs["Sample Position"])

    nearest = ng.nodes.new("GeometryNodeSampleNearest")
    nearest.domain = "POINT"
    nearest.location = (location[0] + 760, location[1] - 200)
    ng.links.new(flat, nearest.inputs["Geometry"])
    ng.links.new(sample, nearest.inputs["Sample Position"])

    # Read the draped mesh's Z at the nearest vertex index.
    sep = ng.nodes.new("ShaderNodeSeparateXYZ")
    sep.location = (location[0] + 760, location[1] - 380)
    ng.links.new(position(ng, (location[0] + 580, location[1] - 380)), sep.inputs[0])

    idx = ng.nodes.new("GeometryNodeSampleIndex")
    idx.data_type = "FLOAT"
    idx.domain = "POINT"
    idx.location = (location[0] + 960, location[1] - 260)
    ng.links.new(draped, idx.inputs["Geometry"])
    ng.links.new(sep.outputs["Z"], idx.inputs["Value"])
    ng.links.new(nearest.outputs["Index"], idx.inputs["Index"])
    return prox.outputs["Distance"], idx.outputs["Value"]


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
