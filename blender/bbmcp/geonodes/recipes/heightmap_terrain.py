"""heightmap_terrain: displace a grid by a heightmap image.

The heightmap comes from the venv erosion engine (bobtools.erosion). This is the
Blender side of the erosion pipeline: the heavy simulation happens in numpy, and
Blender just reads the result.

Params: heightmap (absolute image path), size, resolution, height, sea_level.
"""

import bpy

from ..blocks import displace_z, grid_source, math_node, position
from ..scaffold import add_input
from . import recipe


@recipe("heightmap_terrain")
def build(ng, out, params: dict):
    gi = ng.nodes.new("NodeGroupInput")
    gi.location = (-1200, 0)

    mesh = grid_source(ng, gi, params.get("size", 60.0), params.get("resolution", 512))
    add_input(ng, "Height", "NodeSocketFloat", float(params.get("height", 14.0)))
    add_input(ng, "Sea Level", "NodeSocketFloat", float(params.get("sea_level", 0.3)), 0.0)

    nodes, links = ng.nodes, ng.links

    # UV in 0..1 from position: pos.xy / Size + 0.5.
    pos = position(ng, (-1000, -260))
    sep = nodes.new("ShaderNodeSeparateXYZ")
    sep.location = (-820, -260)
    links.new(pos, sep.inputs[0])
    u = math_node(ng, "ADD", math_node(ng, "DIVIDE", sep.outputs["X"], gi.outputs["Size"], (-640, -180)), 0.5, (-460, -180))
    v = math_node(ng, "ADD", math_node(ng, "DIVIDE", sep.outputs["Y"], gi.outputs["Size"], (-640, -340)), 0.5, (-460, -340))
    uvw = nodes.new("ShaderNodeCombineXYZ")
    uvw.location = (-280, -260)
    links.new(u, uvw.inputs["X"])
    links.new(v, uvw.inputs["Y"])

    tex = nodes.new("GeometryNodeImageTexture")
    tex.interpolation = "Linear"
    tex.extension = "EXTEND"
    tex.location = (-100, -200)
    path = params.get("heightmap")
    if path:
        tex.inputs["Image"].default_value = bpy.data.images.load(path, check_existing=True)
    links.new(uvw.outputs["Vector"], tex.inputs["Vector"])

    # Grayscale heightmap: linking the Color output to a Math (float) input
    # converts it to a scalar.
    above = math_node(ng, "SUBTRACT", tex.outputs["Color"], gi.outputs["Sea Level"], (320, -100))
    z = math_node(ng, "MULTIPLY", above, gi.outputs["Height"], (500, -100))
    geometry = displace_z(ng, mesh, z, (720, 0))

    # GN primitive output does not inherit the object's material slots reliably,
    # so assign the material by name here when given.
    mat = bpy.data.materials.get(params.get("material", ""))
    if mat is not None:
        set_mat = nodes.new("GeometryNodeSetMaterial")
        set_mat.location = (920, 0)
        links.new(geometry, set_mat.inputs["Geometry"])
        set_mat.inputs["Material"].default_value = mat
        geometry = set_mat.outputs["Geometry"]

    links.new(geometry, out.inputs["Geometry"])
