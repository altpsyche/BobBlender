"""heightmap_terrain: displace a grid by a heightmap image.

The heightmap comes from the in-process heightfield compute (core/heightfields). This is
the Blender side of the erosion pipeline: the heavy simulation happens in numpy (or CuPy on
GPU) inside Blender's own Python, and this recipe just reads the result.

Params: heightmap (absolute image path), size, resolution, height, sea_level.

Path grading is no longer inline here. A curve now carves the terrain through the
standalone curve_overlay modifier (docs/SPLINES.md 4.3, BobSplines, the terrain overlay), stacked on
the terrain object, so a network of paths composes and downstream effects read the
overlay's baked mask attribute rather than this recipe re-solving proximity.
"""

import bpy

from ..blocks import displace_z, grid_source, math_node, position
from ..scaffold import add_input
from . import recipe


@recipe("heightmap_terrain")
def build(ng, out, params: dict):
    # Real-world scale: 1 Blender unit == 1 metre. Size and Height are metres, so the terrain, a 1.8
# m character and a 6 m house all share one honest scale. Enforce metric here (the single choke
# point for this recipe) so a stray scene-unit scale can never silently squash proportions.
    scene = bpy.context.scene
    if scene is not None:
        scene.unit_settings.system = "METRIC"
        scene.unit_settings.scale_length = 1.0

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

    # Shade smooth. A GN grid is flat-shaded by default, so a displaced terrain reads as a field of
    # per-quad facets (an "orange-peel" stipple on every slope) at any real bake resolution. The
    # heightfield is continuous, so the terrain should carry continuous normals: set the face shade
    # smooth here, the single build choke point, so every preset ships smooth without the caller
    # touching mesh data.
    smooth = nodes.new("GeometryNodeSetShadeSmooth")
    smooth.location = (1520, 0)
    links.new(geometry, smooth.inputs["Geometry"])
    geometry = smooth.outputs["Geometry"]

    links.new(geometry, out.inputs["Geometry"])
