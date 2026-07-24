"""volumetrics: procedural Cycles volumes. Clouds (S2) and fog (S3).

Every mode builds ONE domain box for the whole layer and lets the material carve
the volume out of it. A single bounded domain (rather than a field of instanced
cubes) has no box seams between puffs and does not clip at each cube face; the
material fades density to zero toward the box bounds so the layer never cuts off,
and shows open sky (clouds) or clear air (fog) where the volume thins out. The box
is instanced once so the live knobs travel into the volume shader as INSTANCER
attributes (the linchpin path).

Modes:
- clouds: world-space fractal noise thresholded by Coverage, faded at every box
  face, self-shadowing for form. See materials.BOB_CloudVolume.
- height_fog: a bounded slab, densest at the bottom, fading to zero with height
  (aerial perspective / valley pooling). See materials.BOB_FogVolume.
- noise_fog: the same slab broken into soft patchy banks by noise.
- ground_fog: a terrain-draped mist that samples a heightmap so density hugs the
  ground surface (follows hills up and over), not a fixed-Z slab. Uses a per-image
  material (materials.ground_fog_volume_material) and adds the terrain-mapping knobs.

All numeric params are live modifier knobs. Build-time params are `mode` and, for
ground_fog, `heightmap` (the terrain image path).
"""

import bpy

from ..blocks import math_node
from ..scaffold import add_input
from . import recipe

# bbmcp/materials.py: the cached volume materials (shaders, not GN).
from ...materials import (
    cloud_volume_material,
    fog_volume_material,
    ground_fog_volume_material,
)

# Cloud instance attributes stored on the box <- the socket each reads from.
_CLOUD_ATTRS = [("cloud_density", "Density"),
                ("cloud_detail", "Detail"),
                ("cloud_softness", "Softness"),
                ("cloud_coverage", "Coverage"),
                ("cloud_scale", "Cloud Scale"),
                ("cloud_seed", "Cloud Seed"),
                ("cloud_warp", "Warp")]

# Fog instance attributes stored on the box <- the socket each reads from.
_FOG_ATTRS = [("fog_density", "Density"),
              ("fog_top", "Fog Top"),
              ("fog_falloff", "Falloff"),
              ("fog_noise", "Fog Noise"),
              ("fog_scale", "Fog Scale"),
              ("fog_detail", "Fog Detail"),
              ("fog_seed", "Fog Seed"),
              ("fog_softness", "Softness"),
              ("fog_warp", "Warp"),
              ("fog_aniso", "Anisotropy")]

# Extra attributes only ground_fog (terrain-draped) stores, for the terrain mapping.
_FOG_GROUND_ATTRS = [("fog_terrain_size", "Terrain Size"),
                     ("fog_terrain_height", "Terrain Height"),
                     ("fog_sea_level", "Sea Level"),
                     ("fog_ground_thickness", "Ground Thickness")]


def _layer_and_wind_inputs(ng, params, size, thickness, height):
    """The box and wind-drift inputs common to every volumetrics mode."""
    add_input(ng, "Layer Size", "NodeSocketFloat", float(params.get("size", size)), 1.0)
    add_input(ng, "Thickness", "NodeSocketFloat", float(params.get("thickness", thickness)), 0.0)
    add_input(ng, "Height", "NodeSocketFloat", float(params.get("height", height)))
    add_input(ng, "Wind", "NodeSocketBool", bool(params.get("wind", False)))
    add_input(ng, "Wind Direction", "NodeSocketFloat", float(params.get("wind_direction", 0.0)), 0.0, 360.0)
    add_input(ng, "Wind Speed", "NodeSocketFloat", float(params.get("wind_speed", 2.0)), 0.0)


def _domain_geo(ng, gi):
    """One point at Height carrying the domain box as a single instance, plus the
    wind-drift vector. Returns (instances_geometry_socket, drift_vector_socket).

    The box is instanced once so the live knobs reach the volume shader as
    INSTANCER attributes. Wind drift advances an offset by Wind Speed * scene time
    along Wind Direction, gated by the Wind toggle; the material shifts its noise
    sample by it so the pattern drifts through the stationary box. Scene time (not
    wall clock) drives it, so a Cycles animation renders the same every time.
    """
    nodes, links = ng.nodes, ng.links

    point = nodes.new("GeometryNodePoints")
    point.location = (-700, 200)
    point.inputs["Count"].default_value = 1
    height_vec = nodes.new("ShaderNodeCombineXYZ")
    height_vec.location = (-880, 60)
    links.new(gi.outputs["Height"], height_vec.inputs["Z"])
    links.new(height_vec.outputs["Vector"], point.inputs["Position"])

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

    return iop.outputs["Instances"], drift.outputs["Vector"]


def _finish(ng, out, gi, geo, drift, attrs, wind_attr, material, extra_stores=()):
    """Store the live knobs and the wind offset as INSTANCE attributes, assign the
    volume material, and output. Shared by every mode.

    attrs is a list of (attr_name, socket_name) stored as FLOAT. extra_stores is a
    list of (attr_name, socket_name, data_type) for non-float knobs (e.g. the fog
    colour as FLOAT_COLOR). The wind offset is stored last as FLOAT_VECTOR.
    """
    nodes, links = ng.nodes, ng.links

    stores = [(a, s, "FLOAT") for (a, s) in attrs] + list(extra_stores)
    for i, (attr, socket, dtype) in enumerate(stores):
        store = nodes.new("GeometryNodeStoreNamedAttribute")
        store.data_type = dtype
        store.domain = "INSTANCE"
        store.location = (-80 + i * 190, 120)
        links.new(geo, store.inputs["Geometry"])
        store.inputs["Name"].default_value = attr
        links.new(gi.outputs[socket], store.inputs["Value"])
        geo = store.outputs["Geometry"]

    wind_store = nodes.new("GeometryNodeStoreNamedAttribute")
    wind_store.data_type = "FLOAT_VECTOR"
    wind_store.domain = "INSTANCE"
    wind_store.location = (-80 + len(stores) * 190, 120)
    links.new(geo, wind_store.inputs["Geometry"])
    wind_store.inputs["Name"].default_value = wind_attr
    links.new(drift, wind_store.inputs["Value"])
    geo = wind_store.outputs["Geometry"]

    setmat = nodes.new("GeometryNodeSetMaterial")
    setmat.location = (-80 + (len(stores) + 1) * 190, 120)
    links.new(geo, setmat.inputs["Geometry"])
    setmat.inputs["Material"].default_value = material
    links.new(setmat.outputs["Geometry"], out.inputs["Geometry"])


def _build_clouds(ng, out, gi, params):
    _layer_and_wind_inputs(ng, params, 400.0, 40.0, 70.0)
    add_input(ng, "Coverage", "NodeSocketFloat", float(params.get("coverage", 0.5)), 0.0, 1.0)
    add_input(ng, "Cloud Scale", "NodeSocketFloat", float(params.get("cloud_scale", 0.06)), 0.0)
    add_input(ng, "Cloud Seed", "NodeSocketInt", int(params.get("cloud_seed", 0)))
    add_input(ng, "Density", "NodeSocketFloat", float(params.get("density", 5.0)), 0.0)
    add_input(ng, "Detail", "NodeSocketFloat", float(params.get("detail", 5.0)), 0.0)
    add_input(ng, "Softness", "NodeSocketFloat", float(params.get("softness", 0.25)), 0.0, 1.0)
    add_input(ng, "Warp", "NodeSocketFloat", float(params.get("warp", 0.4)), 0.0, 1.0)

    geo, drift = _domain_geo(ng, gi)
    _finish(ng, out, gi, geo, drift, _CLOUD_ATTRS, "cloud_wind", cloud_volume_material())


def _build_fog(ng, out, gi, params, mode):
    """height_fog, noise_fog, and ground_fog. All share the box, the wind drift, and
    the polish knobs (Falloff, Warp, Fog Color, Anisotropy). height_fog and noise_fog
    share the box-relative material and differ only in default Fog Noise; ground_fog
    uses a terrain-draped material that samples a heightmap so the mist hugs the
    ground, and adds the terrain-mapping knobs."""
    is_noise = mode == "noise_fog"
    is_ground = mode == "ground_fog"
    # Box defaults per mode: height_fog thin low slab, noise_fog lower/thicker banks,
    # ground_fog a taller box that encloses the terrain plus the mist above it.
    thickness = {"noise_fog": 60.0, "ground_fog": 60.0}.get(mode, 40.0)
    height = {"noise_fog": 30.0, "ground_fog": 15.0}.get(mode, 20.0)
    _layer_and_wind_inputs(ng, params, 400.0, thickness=thickness, height=height)

    add_input(ng, "Density", "NodeSocketFloat", float(params.get("density", 2.0)), 0.0)
    add_input(ng, "Fog Top", "NodeSocketFloat", float(params.get("fog_top", 0.6)), 0.0, 1.0)
    add_input(ng, "Falloff", "NodeSocketFloat", float(params.get("falloff", 1.5)), 0.1, 8.0)
    default_noise = 0.85 if is_noise else (0.25 if is_ground else 0.15)
    add_input(ng, "Fog Noise", "NodeSocketFloat", float(params.get("fog_noise", default_noise)), 0.0, 1.0)
    add_input(ng, "Fog Scale", "NodeSocketFloat", float(params.get("fog_scale", 0.03)), 0.0)
    add_input(ng, "Fog Detail", "NodeSocketFloat", float(params.get("fog_detail", 4.0)), 0.0)
    add_input(ng, "Fog Seed", "NodeSocketInt", int(params.get("fog_seed", 0)))
    add_input(ng, "Softness", "NodeSocketFloat", float(params.get("softness", 0.3)), 0.0, 1.0)
    add_input(ng, "Warp", "NodeSocketFloat", float(params.get("warp", 0.3)), 0.0, 1.0)
    add_input(ng, "Fog Color", "NodeSocketColor", tuple(params.get("color", (1.0, 1.0, 1.0, 1.0))))
    add_input(ng, "Anisotropy", "NodeSocketFloat", float(params.get("anisotropy", 0.4)), -0.9, 0.9)

    attrs = list(_FOG_ATTRS)
    material = fog_volume_material()
    if is_ground:
        add_input(ng, "Terrain Size", "NodeSocketFloat", float(params.get("terrain_size", 60.0)), 1.0)
        add_input(ng, "Terrain Height", "NodeSocketFloat", float(params.get("terrain_height", 14.0)))
        add_input(ng, "Sea Level", "NodeSocketFloat", float(params.get("sea_level", 0.3)), 0.0)
        add_input(ng, "Ground Thickness", "NodeSocketFloat", float(params.get("ground_thickness", 8.0)), 0.0)
        attrs = attrs + _FOG_GROUND_ATTRS
        image = None
        hm_path = params.get("heightmap")
        if hm_path:
            image = bpy.data.images.load(hm_path, check_existing=True)
        material = ground_fog_volume_material(image)  # falls back to box fog if None

    geo, drift = _domain_geo(ng, gi)
    _finish(ng, out, gi, geo, drift, attrs, "fog_wind", material,
            extra_stores=[("fog_color", "Fog Color", "FLOAT_COLOR")])


@recipe("volumetrics")
def build(ng, out, params: dict):
    mode = params.get("mode", "clouds")

    gi = ng.nodes.new("NodeGroupInput")
    gi.location = (-1000, 0)

    if mode in ("height_fog", "noise_fog", "ground_fog"):
        _build_fog(ng, out, gi, params, mode)
    else:
        _build_clouds(ng, out, gi, params)
