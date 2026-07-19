"""volumetrics: procedural Cycles volumes. S2 is clouds; S3 adds fog modes.

Clouds mode builds ONE domain box for the whole cloud layer and lets the material
carve the clouds out of it with world-space noise. A single bounded domain (rather
than a field of instanced cubes) has no box seams between puffs and does not clip
the cloud at each cube face; the material fades the density to zero toward the box
faces so the layer never cuts off at the bound, and open sky shows wherever the
noise falls below the Coverage threshold. The box is instanced once so the live
knobs travel into the volume shader as INSTANCER attributes (the linchpin path).

Modifier inputs (all live): Layer Size, Thickness, Height, Coverage, Cloud Scale,
Cloud Seed, Density, Detail, Softness. Params: mode.
"""

import bpy

from ..blocks import math_node
from ..scaffold import add_input
from . import recipe

# bbmcp/materials.py: the cached volume material (a shader, not GN).
from ...materials import cloud_volume_material

# Attribute stored on the box <- the socket it reads from.
_ATTRS = [("cloud_density", "Density"),
          ("cloud_detail", "Detail"),
          ("cloud_softness", "Softness"),
          ("cloud_coverage", "Coverage"),
          ("cloud_scale", "Cloud Scale"),
          ("cloud_seed", "Cloud Seed")]


@recipe("volumetrics")
def build(ng, out, params: dict):
    params.get("mode", "clouds")  # S2 clouds only; S3 branches height_fog/noise_fog

    gi = ng.nodes.new("NodeGroupInput")
    gi.location = (-1000, 0)

    add_input(ng, "Layer Size", "NodeSocketFloat", float(params.get("size", 400.0)), 1.0)
    add_input(ng, "Thickness", "NodeSocketFloat", float(params.get("thickness", 40.0)), 0.0)
    add_input(ng, "Height", "NodeSocketFloat", float(params.get("height", 70.0)))
    add_input(ng, "Coverage", "NodeSocketFloat", float(params.get("coverage", 0.5)), 0.0, 1.0)
    add_input(ng, "Cloud Scale", "NodeSocketFloat", float(params.get("cloud_scale", 0.06)), 0.0)
    add_input(ng, "Cloud Seed", "NodeSocketInt", int(params.get("cloud_seed", 0)))
    add_input(ng, "Density", "NodeSocketFloat", float(params.get("density", 5.0)), 0.0)
    add_input(ng, "Detail", "NodeSocketFloat", float(params.get("detail", 5.0)), 0.0)
    add_input(ng, "Softness", "NodeSocketFloat", float(params.get("softness", 0.25)), 0.0, 1.0)
    add_input(ng, "Wind", "NodeSocketBool", bool(params.get("wind", False)))
    add_input(ng, "Wind Direction", "NodeSocketFloat", float(params.get("wind_direction", 0.0)), 0.0, 360.0)
    add_input(ng, "Wind Speed", "NodeSocketFloat", float(params.get("wind_speed", 2.0)), 0.0)

    nodes, links = ng.nodes, ng.links

    # A single point at the layer height, carrying the domain box as one instance.
    point = nodes.new("GeometryNodePoints")
    point.location = (-700, 200)
    point.inputs["Count"].default_value = 1
    height_vec = nodes.new("ShaderNodeCombineXYZ")
    height_vec.location = (-880, 60)
    links.new(gi.outputs["Height"], height_vec.inputs["Z"])
    links.new(height_vec.outputs["Vector"], point.inputs["Position"])

    # The domain box: Layer Size in XY, Thickness in Z.
    size_vec = nodes.new("ShaderNodeCombineXYZ")
    size_vec.location = (-700, -120)
    links.new(gi.outputs["Layer Size"], size_vec.inputs["X"])
    links.new(gi.outputs["Layer Size"], size_vec.inputs["Y"])
    links.new(gi.outputs["Thickness"], size_vec.inputs["Z"])
    cube = nodes.new("GeometryNodeMeshCube")
    cube.location = (-520, -120)
    links.new(size_vec.outputs["Vector"], cube.inputs["Size"])

    iop = nodes.new("GeometryNodeInstanceOnPoints")
    iop.location = (-300, 120)
    links.new(point.outputs["Geometry"], iop.inputs["Points"])
    links.new(cube.outputs["Mesh"], iop.inputs["Instance"])

    # Wind drift: advance an offset by Wind Speed * scene time along Wind Direction,
    # gated by the Wind toggle. Stored per instance so the material shifts its noise
    # sample and the clouds drift through the stationary box. Scene time (not wall
    # clock) drives it, so a Cycles animation renders the same every time.
    rad = math_node(ng, "MULTIPLY", gi.outputs["Wind Direction"], 0.0174532925, (-700, -320))
    dx = math_node(ng, "COSINE", rad, location=(-520, -280))
    dy = math_node(ng, "SINE", rad, location=(-520, -380))
    stime = nodes.new("GeometryNodeInputSceneTime")
    stime.location = (-700, -480)
    enable = math_node(ng, "MULTIPLY", gi.outputs["Wind"], gi.outputs["Wind Speed"], (-520, -520))
    mag = math_node(ng, "MULTIPLY", enable, stime.outputs["Seconds"], (-340, -480))
    drift = nodes.new("ShaderNodeCombineXYZ")
    drift.location = (-160, -420)
    links.new(math_node(ng, "MULTIPLY", dx, mag, (-340, -300)), drift.inputs["X"])
    links.new(math_node(ng, "MULTIPLY", dy, mag, (-340, -400)), drift.inputs["Y"])

    # Carry the live knobs onto the box as instance attributes the material reads.
    geo = iop.outputs["Instances"]
    for i, (attr, socket) in enumerate(_ATTRS):
        store = nodes.new("GeometryNodeStoreNamedAttribute")
        store.data_type = "FLOAT"
        store.domain = "INSTANCE"
        store.location = (-80 + i * 190, 120)
        links.new(geo, store.inputs["Geometry"])
        store.inputs["Name"].default_value = attr
        links.new(gi.outputs[socket], store.inputs["Value"])
        geo = store.outputs["Geometry"]

    wind_store = nodes.new("GeometryNodeStoreNamedAttribute")
    wind_store.data_type = "FLOAT_VECTOR"
    wind_store.domain = "INSTANCE"
    wind_store.location = (-80 + len(_ATTRS) * 190, 120)
    links.new(geo, wind_store.inputs["Geometry"])
    wind_store.inputs["Name"].default_value = "cloud_wind"
    links.new(drift.outputs["Vector"], wind_store.inputs["Value"])
    geo = wind_store.outputs["Geometry"]

    setmat = nodes.new("GeometryNodeSetMaterial")
    setmat.location = (-80 + (len(_ATTRS) + 1) * 190, 120)
    links.new(geo, setmat.inputs["Geometry"])
    setmat.inputs["Material"].default_value = cloud_volume_material()
    links.new(setmat.outputs["Geometry"], out.inputs["Geometry"])
