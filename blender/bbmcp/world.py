"""build_sky: the lighting foundation. A physical sky and a matched Sun light,
placed from Time of Day and geography.

The sun position comes from the solar model (or a manual override), and is written
to both the sky node and the Sun light so the sky and the shadows agree. bpy-only,
so it splits cleanly with BobFirmament.

Double-sun note: the sky disc and a Sun lamp would each light the scene and
double-count. Default is the lamp lights and the disc is suppressed
(sun_disc off), so shadow softness stays controllable via the lamp's angular size.
The disc's elevation and rotation still track the lamp, so turning it on later
places the visible sun correctly.

No world haze here (a Phase-0 finding, 2026-07-19): a constant-density world
Principled Volume has infinite optical depth, so it extinguishes the Sun lamp and
skylight (both originate at infinity) and blacks the frame at any density above
about 0.001. Aerial perspective for the sky dome comes from the physical sky model
itself (air_density, turbidity); scene aerial haze is a bounded fog domain, which
is S3's job, not an unbounded world volume.
"""

import math

import bpy

from . import solar

WORLD_NAME = "BOB_World"
SUN_NAME = "BOB_Sun"


def _get(params, key, default):
    v = params.get(key, default)
    return default if v is None else v


def _ensure_world():
    world = bpy.data.worlds.get(WORLD_NAME)
    if world is None:
        world = bpy.data.worlds.new(WORLD_NAME)
    world.use_nodes = True
    return world


def _activate_world(world):
    """Point the scene at the world, forcing the world-changed notifier every time.

    EEVEE Next recaptures the world light probe on that notifier, not on a plain
    ID tag. On a rebuild the scene.world pointer is unchanged (already BOB_World),
    so a straight assign is a no-op and the viewport keeps the stale, now-empty
    capture and renders black. Clearing to None first guarantees the change fires,
    so an in-place rebuild refreshes the viewport the same as the first build. F12
    renders were never affected; this is the live-viewport fix.
    """
    scene = bpy.context.scene
    if scene.world is world:
        scene.world = None
    scene.world = world


def _build_world_nodes(world, sky_kw, world_strength):
    """Physical sky to the world surface. No world volume: see the module note."""
    tree = world.node_tree
    tree.nodes.clear()

    output = tree.nodes.new("ShaderNodeOutputWorld")
    output.location = (600, 0)

    sky = tree.nodes.new("ShaderNodeTexSky")
    # Blender 5.2 replaced the old NISHITA sky with SINGLE/MULTIPLE_SCATTERING
    # physical models (probed empirically). MULTIPLE_SCATTERING is the higher
    # quality successor. Its knobs are air_density, ozone_density, turbidity, and
    # ground_albedo (the old dust_density is gone; turbidity carries haziness).
    sky.sky_type = "MULTIPLE_SCATTERING"
    sky.location = (0, 200)
    for attr, value in sky_kw.items():
        setattr(sky, attr, value)

    background = tree.nodes.new("ShaderNodeBackground")
    background.location = (300, 200)
    background.inputs["Strength"].default_value = world_strength
    tree.links.new(sky.outputs["Color"], background.inputs["Color"])
    tree.links.new(background.outputs["Background"], output.inputs["Surface"])


def _ensure_sun():
    obj = bpy.data.objects.get(SUN_NAME)
    if obj is not None and obj.type == "LIGHT":
        return obj
    light = bpy.data.lights.new(SUN_NAME, "SUN")
    obj = bpy.data.objects.new(SUN_NAME, light)
    bpy.context.scene.collection.objects.link(obj)
    return obj


def _place_sun(obj, elevation, azimuth, strength, angle_deg):
    """Aim the Sun light from an elevation and a north-clockwise azimuth.

    Derivation: a Sun lamp emits along local -Z, so its +Z must point at the sun.
    For sun direction d = (cos el sin az, cos el cos az, sin el) in Blender axes
    (+X east, +Y north, +Z up), the Euler (90-el, 0, 180-az) rotates +Z onto d.
    Below the horizon the lamp is switched off so night is dark and nothing lights
    the scene from underneath.
    """
    el = math.radians(elevation)
    az = math.radians(azimuth)
    obj.rotation_euler = (math.radians(90.0) - el, 0.0, math.radians(180.0) - az)
    light = obj.data
    light.angle = math.radians(angle_deg)
    light.energy = strength if elevation > 0.0 else 0.0


def build_sky(op: dict) -> dict:
    params = op.get("params", {})

    # Sun position: manual override, else the geographic solar model.
    if _get(params, "use_override", False):
        elevation = float(_get(params, "sun_elevation", 45.0))
        azimuth = float(_get(params, "sun_azimuth", 180.0))
        source = "override"
    else:
        pos = solar.sun_position(
            _get(params, "latitude", 45.0), _get(params, "longitude", 0.0),
            int(_get(params, "year", 2026)), int(_get(params, "month", 6)),
            int(_get(params, "day", 21)), float(_get(params, "time_of_day", 12.0)),
            utc_offset=float(_get(params, "utc_offset", 0.0)),
        )
        elevation, azimuth = pos["elevation"], pos["azimuth"]
        source = "solar"

    sky_kw = {
        "sun_disc": bool(_get(params, "sun_disc", False)),
        "sun_size": math.radians(_get(params, "sun_angle", 0.545)),
        "sun_intensity": _get(params, "sun_intensity", 1.0),
        "sun_elevation": math.radians(elevation),
        "sun_rotation": math.radians(azimuth),
        "altitude": _get(params, "altitude", 200.0),
        "air_density": _get(params, "air", 1.0),
        "ozone_density": _get(params, "ozone", 1.0),
        "turbidity": _get(params, "turbidity", 2.2),
        "ground_albedo": _get(params, "ground_albedo", 0.3),
    }

    world = _ensure_world()
    _build_world_nodes(
        world, sky_kw, world_strength=_get(params, "world_strength", 1.0))
    _activate_world(world)  # after the tree is built, so the refresh sees it

    sun = _ensure_sun()
    _place_sun(
        sun, elevation, azimuth,
        strength=_get(params, "sun_strength", 2.0),
        angle_deg=_get(params, "sun_angle", 0.545),
    )

    # A rebuild clears and refills the world node tree; tag the world and sun so an
    # open viewport re-evaluates the new shader instead of showing a stale one.
    world.update_tag()
    sun.update_tag()

    info = f"{source} el={elevation:.1f} az={azimuth:.1f}"
    return {"op": "build_sky", "created": [world.name, sun.name], "info": info}
