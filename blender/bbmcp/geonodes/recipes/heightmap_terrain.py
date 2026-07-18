"""heightmap_terrain: displace a grid by a heightmap image.

The heightmap comes from the venv erosion engine (bobtools.erosion). This is the
Blender side of the erosion pipeline: the heavy simulation happens in numpy, and
Blender just reads the result.

Params: heightmap (absolute image path), size, resolution, height, sea_level.

Optional path param (a curve object name) grades a trail: within Path Width of
the curve the ground is levelled to the curve's own height, easing back to
natural terrain over Path Falloff, recessed by Path Depth. The curve should be
draped onto the surface (make_path does this when given the heightmap), so its
smooth Z profile grades the trail without copying the terrain's fine relief.
"""

import bpy

from ..blocks import (
    curve_path_sample,
    displace_z,
    grid_source,
    math_node,
    mix_float,
    position,
    smooth_falloff,
)
from ..scaffold import add_input
from . import recipe


@recipe("heightmap_terrain")
def build(ng, out, params: dict):
    gi = ng.nodes.new("NodeGroupInput")
    gi.location = (-1200, 0)

    mesh = grid_source(ng, gi, params.get("size", 60.0), params.get("resolution", 512))
    add_input(ng, "Height", "NodeSocketFloat", float(params.get("height", 14.0)))
    add_input(ng, "Sea Level", "NodeSocketFloat", float(params.get("sea_level", 0.3)), 0.0)

    path = bpy.data.objects.get(params.get("path", ""))
    if path is not None:
        add_input(ng, "Path Width", "NodeSocketFloat", float(params.get("path_width", 2.4)), 0.0)
        add_input(ng, "Path Falloff", "NodeSocketFloat", float(params.get("path_falloff", 3.5)), 0.0)
        add_input(ng, "Path Depth", "NodeSocketFloat", float(params.get("path_depth", 0.3)))

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

    if path is not None:
        # Level the trail to the draped curve's own smooth height, so it grades
        # gently instead of copying the terrain's fine relief. The curve is draped
        # onto the surface by make_path, so its Z is already a world height.
        dist, path_flat = curve_path_sample(ng, path, (-1100, -560))
        path_z = math_node(ng, "SUBTRACT", path_flat, gi.outputs["Path Depth"], (680, -760))
        p_outer = math_node(ng, "ADD", gi.outputs["Path Width"], gi.outputs["Path Falloff"], (500, -560))
        mask = smooth_falloff(ng, dist, gi.outputs["Path Width"], p_outer, (680, -560))
        # mask 0 on the path -> path_z, 1 off it -> terrain_z.
        z = mix_float(ng, mask, path_z, terrain_z, (880, -300))

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
