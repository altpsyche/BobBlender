"""heightmap_terrain: displace a grid by a heightmap image.

The heightmap comes from the venv erosion engine (bobtools.erosion). This is the
Blender side of the erosion pipeline: the heavy simulation happens in numpy, and
Blender just reads the result.

Params: heightmap (absolute image path), size, resolution, height, sea_level.

Path grading is no longer inline here. A curve now carves the terrain through the
standalone curve_overlay modifier (docs/SPLINES.md 4.3, BobSplines C2), stacked on
the terrain object, so a network of paths composes and downstream effects read the
overlay's baked mask attribute rather than this recipe re-solving proximity.
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

    image = None
    hm_path = params.get("heightmap")
    if hm_path:
        image = bpy.data.images.load(hm_path, check_existing=True)

    def sample(world_pos, y):
        """Sample the heightmap at a world XY: UV = pos.xy / Size + 0.5."""
        sep = nodes.new("ShaderNodeSeparateXYZ")
        sep.location = (-820, y)
        links.new(world_pos, sep.inputs[0])
        u = math_node(ng, "ADD", math_node(ng, "DIVIDE", sep.outputs["X"], gi.outputs["Size"], (-640, y + 40)), 0.5, (-460, y + 40))
        v = math_node(ng, "ADD", math_node(ng, "DIVIDE", sep.outputs["Y"], gi.outputs["Size"], (-640, y - 120)), 0.5, (-460, y - 120))
        uvw = nodes.new("ShaderNodeCombineXYZ")
        uvw.location = (-280, y)
        links.new(u, uvw.inputs["X"])
        links.new(v, uvw.inputs["Y"])
        tex = nodes.new("GeometryNodeImageTexture")
        tex.interpolation = "Linear"
        tex.extension = "EXTEND"
        tex.location = (-100, y)
        if image is not None:
            tex.inputs["Image"].default_value = image
        links.new(uvw.outputs["Vector"], tex.inputs["Vector"])
        return tex.outputs["Color"]

    # Natural terrain height at each grid point. Linking Color into a float Math
    # input converts the grayscale heightmap to a scalar.
    terrain_raw = sample(position(ng, (-1000, -160)), -160)
    terrain_z = math_node(
        ng, "MULTIPLY",
        math_node(ng, "SUBTRACT", terrain_raw, gi.outputs["Sea Level"], (320, -100)),
        gi.outputs["Height"], (500, -100),
    )
    z = terrain_z

    geometry = displace_z(ng, mesh, z, (1120, 0))

    # GN primitive output does not inherit the object's material slots reliably,
    # so assign the material by name here when given.
    mat = bpy.data.materials.get(params.get("material", ""))
    if mat is not None:
        set_mat = nodes.new("GeometryNodeSetMaterial")
        set_mat.location = (1320, 0)
        links.new(geometry, set_mat.inputs["Geometry"])
        set_mat.inputs["Material"].default_value = mat
        geometry = set_mat.outputs["Geometry"]

    links.new(geometry, out.inputs["Geometry"])
