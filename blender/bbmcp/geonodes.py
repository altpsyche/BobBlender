"""Geometry-node recipes. Each recipe builds a reusable GeometryNodeTree.

A recipe is a named builder that constructs a whole node graph from a handful of
params. One function per recipe keeps this DRY, recipes can grow into Asset
Browser assets, and a name plus params is easy for an agent to call. This is
simpler than modelling Blender's entire node system in the contract.

To add a recipe, write build_<name>(ng, out, params) and register it in RECIPES.
"""

import bpy


def _new_geometry_group(name: str):
    """A fresh GeometryNodeTree with a Geometry output + Group Output node."""
    ng = bpy.data.node_groups.new(name, "GeometryNodeTree")
    ng.interface.new_socket("Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")
    out = ng.nodes.new("NodeGroupOutput")
    out.location = (600, 0)
    return ng, out


# Recipes
def build_wave_grid(ng, out, params: dict):
    """A grid whose Z ripples as sin(distance_from_center * frequency) * amplitude."""
    size = float(params.get("size", 10.0))
    resolution = int(params.get("resolution", 64))
    amplitude = float(params.get("amplitude", 1.0))
    frequency = float(params.get("frequency", 1.0))

    nodes, links = ng.nodes, ng.links

    grid = nodes.new("GeometryNodeMeshGrid")
    grid.location = (-600, 0)
    grid.inputs["Size X"].default_value = size
    grid.inputs["Size Y"].default_value = size
    grid.inputs["Vertices X"].default_value = resolution
    grid.inputs["Vertices Y"].default_value = resolution

    position = nodes.new("GeometryNodeInputPosition")
    position.location = (-600, -220)

    length = nodes.new("ShaderNodeVectorMath")
    length.operation = "LENGTH"
    length.location = (-400, -220)
    links.new(position.outputs["Position"], length.inputs[0])

    mul_freq = nodes.new("ShaderNodeMath")
    mul_freq.operation = "MULTIPLY"
    mul_freq.location = (-200, -220)
    mul_freq.inputs[1].default_value = frequency
    links.new(length.outputs["Value"], mul_freq.inputs[0])

    sine = nodes.new("ShaderNodeMath")
    sine.operation = "SINE"
    sine.location = (0, -220)
    links.new(mul_freq.outputs["Value"], sine.inputs[0])

    mul_amp = nodes.new("ShaderNodeMath")
    mul_amp.operation = "MULTIPLY"
    mul_amp.location = (200, -220)
    mul_amp.inputs[1].default_value = amplitude
    links.new(sine.outputs["Value"], mul_amp.inputs[0])

    combine = nodes.new("ShaderNodeCombineXYZ")
    combine.location = (400, -120)
    links.new(mul_amp.outputs["Value"], combine.inputs["Z"])

    set_position = nodes.new("GeometryNodeSetPosition")
    set_position.location = (200, 0)
    links.new(grid.outputs["Mesh"], set_position.inputs["Geometry"])
    links.new(combine.outputs["Vector"], set_position.inputs["Offset"])

    links.new(set_position.outputs["Geometry"], out.inputs["Geometry"])


RECIPES = {
    "wave_grid": build_wave_grid,
}


# Entry point (called by dispatch)
def build_geonodes(op: dict) -> dict:
    recipe = op.get("recipe", "wave_grid")
    builder = RECIPES.get(recipe)
    if builder is None:
        raise ValueError(
            f"unknown geonodes recipe: {recipe!r} (have: {sorted(RECIPES)})"
        )

    name = op.get("name") or recipe
    ng, out = _new_geometry_group(name)
    builder(ng, out, op.get("params", {}))

    created = [ng.name]

    if op.get("mark_asset"):
        ng.asset_mark()

    if op.get("target", "new_object") == "new_object":
        mesh = bpy.data.meshes.new(name)
        obj = bpy.data.objects.new(name, mesh)
        bpy.context.scene.collection.objects.link(obj)
        modifier = obj.modifiers.new(name="GeometryNodes", type="NODES")
        modifier.node_group = ng
        created.append(obj.name)

    return {"op": "build_geonodes", "created": created, "info": recipe}
