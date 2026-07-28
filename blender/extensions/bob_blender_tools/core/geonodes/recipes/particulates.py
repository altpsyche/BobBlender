"""particulates: wind-driven weather particles in a camera-following domain.

One recipe, two shape modes selected by the `mode` param:

- streak (rain): fast downward fall, the instance geometry stretched and aligned to
  the velocity vector so wind leans the streaks. The streak is real geometry, so the
  look does not depend on motion blur.
- mote (dust, amber motes, falling snow): slow drift with turbulence, small round
  instances lit by the scene, with an optional Emission knob (default 0).

Deterministic, camera-following motion (the two Cycles landmines this recipe must
handle, from the plan):

- Scene Time drives all motion, so a Cycles animation renders the same every frame.
- The domain follows the camera WITHOUT a domain jump. Each particle has a continuous
  world position `moved = base + velocity * time` (+ turbulence for motes). It is then
  re-tiled to the copy nearest the camera: rep = moved - box*round((moved - cam)/box).
  Because rep is anchored to the particle's own world position, its motion-blur
  velocity is the true world velocity for every particle except the small fraction
  crossing a window edge within a shutter (~ speed*shutter/box); the camera's motion
  never enters the blur. This is the plan's "particle motion in world space, only the
  spawn region tracks the camera", and it is why snapping the follow-centre to the box
  lattice is NOT done (that would concentrate the jump into occasional all-particle
  streak frames instead of removing it).

Live knobs are modifier inputs. Build-time params are `mode` and `camera` (an object
name, set on the Object Info node, since 5.x GN modifiers no longer store object
inputs).
"""

import math

import bpy

from ..blocks import math_node, random_value
from ..scaffold import add_input
from . import recipe

from ...materials import mote_material, rain_material

DEG_TO_RAD = math.pi / 180.0


def _set(ng, socket, val):
    if isinstance(val, (tuple, list)):
        socket.default_value = val
    elif isinstance(val, (int, float)):
        socket.default_value = val
    else:
        ng.links.new(val, socket)


def _vm(ng, op, a, b=None, loc=(0, 0)):
    """A Vector Math node (a, b sockets or tuples). Returns the Vector output."""
    n = ng.nodes.new("ShaderNodeVectorMath")
    n.operation = op
    n.location = loc
    _set(ng, n.inputs[0], a)
    if b is not None:
        _set(ng, n.inputs[1], b)
    return n.outputs["Vector"]


def _vscale(ng, a, s, loc=(0, 0)):
    """Scale vector a by scalar s (socket or float). Returns the Vector output."""
    n = ng.nodes.new("ShaderNodeVectorMath")
    n.operation = "SCALE"
    n.location = loc
    _set(ng, n.inputs[0], a)
    _set(ng, n.inputs["Scale"], s)
    return n.outputs["Vector"]


def _combine(ng, x, y, z, loc=(0, 0)):
    n = ng.nodes.new("ShaderNodeCombineXYZ")
    n.location = loc
    _set(ng, n.inputs["X"], x)
    _set(ng, n.inputs["Y"], y)
    _set(ng, n.inputs["Z"], z)
    return n.outputs["Vector"]


def _camera_location(ng, camera, loc):
    """The camera's world location as a vector, or (0,0,0) when no camera is set.

    With no camera the domain sits at the world origin (a fixed shower), which is a
    reasonable fallback; with a camera the domain re-tiles around it (below)."""
    if camera is None:
        return _combine(ng, 0.0, 0.0, 0.0, loc)
    info = ng.nodes.new("GeometryNodeObjectInfo")
    info.transform_space = "ORIGINAL"  # world location
    info.location = loc
    info.inputs["Object"].default_value = camera
    return info.outputs["Location"]


def _common_inputs(ng, params, mode):
    """The knobs shared by both modes, with mode-appropriate defaults."""
    is_streak = mode == "streak"
    add_input(ng, "Count", "NodeSocketInt", int(params.get("count", 2000)), 0)
    add_input(ng, "Domain Size", "NodeSocketFloat", float(params.get("domain_size", 40.0)), 1.0)
    add_input(ng, "Domain Height", "NodeSocketFloat", float(params.get("domain_height", 40.0)), 1.0)
    fall = params.get("fall_speed", 9.0 if is_streak else 0.4)
    add_input(ng, "Fall Speed", "NodeSocketFloat", float(fall), 0.0)
    add_input(ng, "Drift", "NodeSocketFloat", float(params.get("drift", 1.0)), 0.0)
    size = params.get("size", 0.010 if is_streak else 0.03)
    add_input(ng, "Size", "NodeSocketFloat", float(size), 0.0)
    add_input(ng, "Size Variation", "NodeSocketFloat", float(params.get("size_variation", 0.4)), 0.0, 1.0)
    add_input(ng, "Wind Direction", "NodeSocketFloat", float(params.get("wind_direction", 0.0)), 0.0, 360.0)
    add_input(ng, "Wind Speed", "NodeSocketFloat", float(params.get("wind_speed", 2.0)), 0.0)
    add_input(ng, "Seed", "NodeSocketInt", int(params.get("seed", 0)))
    if mode == "mote":  # motes swirl; streaks fall straight
        add_input(ng, "Turbulence", "NodeSocketFloat", float(params.get("turbulence", 1.0)), 0.0)
    # Quality Scale multiplies the live Count so the Preview/Final level thins the
    # particle field for viewport speed without touching the authored Count (set by
    # the panel's quality toggle, not tuned by hand). 1.0 = full count.
    add_input(ng, "Quality Scale", "NodeSocketFloat", float(params.get("quality_scale", 1.0)), 0.0, 1.0)


def _base_and_move(ng, gi, camera, mode):
    """Build the deterministic camera-following particle positions.

    Returns (points_geometry, velocity_vector). velocity is returned so a streak can
    align its geometry to it.
    """
    nodes, links = ng.nodes, ng.links
    is_mote = mode == "mote"

    box = _combine(ng, gi.outputs["Domain Size"], gi.outputs["Domain Size"],
                   gi.outputs["Domain Height"], (-1200, -420))

    # Per-particle base position, uniform in the box centred on the origin. Random
    # Value varies per point on the points domain (implicit element id), like scatter.
    rv = nodes.new("FunctionNodeRandomValue")
    rv.data_type = "FLOAT_VECTOR"
    rv.location = (-1200, 200)
    rv.inputs["Min"].default_value = (-0.5, -0.5, -0.5)
    rv.inputs["Max"].default_value = (0.5, 0.5, 0.5)
    links.new(gi.outputs["Seed"], rv.inputs["Seed"])
    base = _vm(ng, "MULTIPLY", rv.outputs["Value"], box, (-1000, 200))

    # Velocity = horizontal wind drift + downward fall.
    rad = math_node(ng, "MULTIPLY", gi.outputs["Wind Direction"], DEG_TO_RAD, (-1200, -60))
    dx = math_node(ng, "COSINE", rad, location=(-1040, -20))
    dy = math_node(ng, "SINE", rad, location=(-1040, -120))
    wind_mag = math_node(ng, "MULTIPLY", gi.outputs["Wind Speed"], gi.outputs["Drift"], (-1040, -220))
    horiz = _vscale(ng, _combine(ng, dx, dy, 0.0, (-860, -60)), wind_mag, (-680, -60))
    fall = _combine(ng, 0.0, 0.0, math_node(ng, "MULTIPLY", gi.outputs["Fall Speed"], -1.0, (-860, -260)),
                    (-680, -240))
    vel = _vm(ng, "ADD", horiz, fall, (-500, -140))

    # moved = base + velocity * scene time (Seconds), so motion is deterministic.
    stime = nodes.new("GeometryNodeInputSceneTime")
    stime.location = (-680, 360)
    moved = _vm(ng, "ADD", base, _vscale(ng, vel, stime.outputs["Seconds"], (-320, 40)), (-140, 200))

    # Motes get a smooth, evolving turbulence offset so they flutter and swirl (the
    # Turbulence input is added in _common_inputs, which has params in scope).
    if is_mote:
        turb = nodes.new("ShaderNodeTexNoise")
        turb.noise_dimensions = "4D"
        turb.location = (-500, 460)
        turb.inputs["Scale"].default_value = 0.06
        turb.inputs["Detail"].default_value = 2.0
        links.new(base, turb.inputs["Vector"])
        links.new(math_node(ng, "MULTIPLY", stime.outputs["Seconds"], 0.3, (-680, 520)), turb.inputs["W"])
        offset = _vscale(ng, _vm(ng, "SUBTRACT", turb.outputs["Color"], (0.5, 0.5, 0.5), (-320, 460)),
                         gi.outputs["Turbulence"], (-140, 460))
        moved = _vm(ng, "ADD", moved, offset, (40, 300))

    # Re-tile to the copy nearest the camera: rep = moved - box*round((moved-cam)/box).
    cam = _camera_location(ng, camera, (-1200, -640))
    diff = _vm(ng, "SUBTRACT", moved, cam, (220, 60))
    q = _vm(ng, "DIVIDE", diff, box, (400, 60))
    r = _vm(ng, "ROUND", q, loc=(580, 60))
    rep = _vm(ng, "SUBTRACT", moved, _vm(ng, "MULTIPLY", r, box, (580, -120)), (760, 60))

    points = nodes.new("GeometryNodePoints")
    points.location = (760, 300)
    count = math_node(ng, "MULTIPLY", gi.outputs["Count"], gi.outputs["Quality Scale"], (580, 300))
    links.new(count, points.inputs["Count"])
    setpos = nodes.new("GeometryNodeSetPosition")
    setpos.location = (940, 300)
    links.new(points.outputs["Points"], setpos.inputs["Geometry"])
    links.new(rep, setpos.inputs["Position"])
    return setpos.outputs["Geometry"], vel


def _instance_and_finish(ng, out, gi, points, vel, mode, params):
    """Instance the streak or mote geometry, store the material knobs, set the
    material, and output."""
    nodes, links = ng.nodes, ng.links
    is_streak = mode == "streak"

    # Per-particle uniform scale from Size Variation, on its own seed stream so it is
    # not correlated with the base position.
    scale_seed = math_node(ng, "ADD", gi.outputs["Seed"], 5501, (1000, -260))
    lo = math_node(ng, "SUBTRACT", 1.0, gi.outputs["Size Variation"], (1000, -360))
    hi = math_node(ng, "ADD", 1.0, gi.outputs["Size Variation"], (1000, -460))
    pscale = random_value(ng, "FLOAT", lo, hi, scale_seed, (1180, -360))

    if is_streak:
        # A thin tapered cone (needle) stretched along its local Z, then aligned to
        # velocity so the streak points the way the drop falls and leans with the wind.
        add_input(ng, "Streak Length", "NodeSocketFloat", float(params.get("streak_length", 0.2)), 0.0)
        add_input(ng, "Color", "NodeSocketColor",
                  tuple(params.get("color", (0.7, 0.8, 0.9, 1.0))))
        depth = math_node(ng, "MULTIPLY", gi.outputs["Fall Speed"], gi.outputs["Streak Length"], (1000, 40))
        # A tapered cone (needle), not a cylinder: the streak reads as a thin raindrop
        # instead of a hard rod. The apex (Radius Top 0) is at +Z, which Align maps to
        # the velocity direction, so the point leads the fall. The material tapers the
        # opacity at both ends so neither the base cap nor the apex reads as an edge.
        cone = nodes.new("GeometryNodeMeshCone")
        cone.location = (1180, 100)
        cone.inputs["Vertices"].default_value = 6
        cone.inputs["Radius Top"].default_value = 0.0
        links.new(gi.outputs["Size"], cone.inputs["Radius Bottom"])
        links.new(depth, cone.inputs["Depth"])
        mesh = cone.outputs["Mesh"]
        align = nodes.new("FunctionNodeAlignRotationToVector")
        align.axis = "Z"
        align.location = (1180, 300)
        align.inputs["Factor"].default_value = 1.0
        links.new(vel, align.inputs["Vector"])
        rotation = align.outputs["Rotation"]
        material = rain_material()
        stores = [("rain_color", "Color", "FLOAT_COLOR")]
    else:
        add_input(ng, "Color", "NodeSocketColor",
                  tuple(params.get("color", (1.0, 1.0, 1.0, 1.0))))
        add_input(ng, "Emission", "NodeSocketFloat", float(params.get("emission", 0.0)), 0.0)
        ico = nodes.new("GeometryNodeMeshIcoSphere")
        ico.location = (1180, 100)
        ico.inputs["Subdivisions"].default_value = 1
        links.new(gi.outputs["Size"], ico.inputs["Radius"])
        mesh = ico.outputs["Mesh"]
        rotation = None
        material = mote_material()
        stores = [("mote_color", "Color", "FLOAT_COLOR"),
                  ("mote_emission", "Emission", "FLOAT")]

    iop = nodes.new("GeometryNodeInstanceOnPoints")
    iop.location = (1400, 200)
    links.new(points, iop.inputs["Points"])
    links.new(mesh, iop.inputs["Instance"])
    links.new(pscale, iop.inputs["Scale"])  # scalar broadcasts to all three axes
    if rotation is not None:
        links.new(rotation, iop.inputs["Rotation"])
    geo = iop.outputs["Instances"]

    # Store the material knobs on the instance domain, read back as INSTANCER
    # attributes in the material (the same per-instance path clouds and fog use).
    for i, (attr, socket, dtype) in enumerate(stores):
        store = nodes.new("GeometryNodeStoreNamedAttribute")
        store.data_type = dtype
        store.domain = "INSTANCE"
        store.location = (1600 + i * 200, 200)
        links.new(geo, store.inputs["Geometry"])
        store.inputs["Name"].default_value = attr
        links.new(gi.outputs[socket], store.inputs["Value"])
        geo = store.outputs["Geometry"]

    setmat = nodes.new("GeometryNodeSetMaterial")
    setmat.location = (1600 + len(stores) * 200, 200)
    links.new(geo, setmat.inputs["Geometry"])
    setmat.inputs["Material"].default_value = material
    links.new(setmat.outputs["Geometry"], out.inputs["Geometry"])


@recipe("particulates")
def build(ng, out, params: dict):
    mode = params.get("mode", "streak")
    if mode not in ("streak", "mote"):
        mode = "streak"
    camera = bpy.data.objects.get(params.get("camera", ""))

    gi = ng.nodes.new("NodeGroupInput")
    gi.location = (-1500, 0)

    _common_inputs(ng, params, mode)
    points, vel = _base_and_move(ng, gi, camera, mode)
    _instance_and_finish(ng, out, gi, points, vel, mode, params)
