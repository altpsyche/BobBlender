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
is the fog volume's job, not an unbounded world volume.
"""

import math

import bpy

from . import env as _env, solar

WORLD_NAME = "BOB_World"
SUN_NAME = "BOB_Sun"
SKY_NODE = "BOB_Sky"  # the ShaderNodeTexSky; named so the live sun drivers can target it


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
    sky.name = sky.label = SKY_NODE
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


GEO_KEYS = ("time_of_day", "year", "month", "day", "utc_offset", "latitude", "longitude")


def _differs(passed, stored):
    """Whether a passed geographic value disagrees with the one on bbt_env. A value that will not
    compare as a number (which the solar model would reject later anyway) counts as disagreeing, so
    the check reports rather than raising out of a durability note."""
    try:
        return abs(float(passed) - float(stored)) > 1e-9
    except (TypeError, ValueError):
        return True


def _record_sun(scene, *, use_override, elevation, azimuth, strength, angle_deg):
    """Write the sun this build just decided onto Scene.bbt_firmament, and report whether it stuck.

    Why: Atmosphere's world applier re-places the sun from bbt_firmament plus bbt_env on every world
    re-apply (`apply_world`, `set_env`, `apply_biome`, `apply_season`, a World slider, and any write
    to a geographic field, each of which carries its own update callback). A sun override that lived
    only in this op's params was therefore lost at the next re-apply, and at a night `time_of_day`
    the recomputed sun is below the horizon, where the lamp energy is zeroed and the physical sky
    renders black. The frame comes back with nothing in it but the emissive geometry, which reads as
    a fog or exposure fault and is neither. The panel's props are the one place the applier looks,
    so this is where the override has to live.

    A build with NO override clears the flag for the same reason, in reverse: a stale one would
    outrank the solar model the caller just asked for.

    The angles go on before the flag, so the props' own live-update callbacks never see a True flag
    against last build's angles and flap the sun through a position nothing asked for. The caller
    places the sun after this returns, so the explicit placement is what lands either way.

    Returns True when the props exist to write (BobFirmament's UI registered), False headless.
    """
    fm = getattr(scene, "bbt_firmament", None) if scene is not None else None
    if fm is None:
        return False
    fm.override_elevation = elevation
    fm.override_azimuth = azimuth
    fm.sun_strength = strength
    fm.sun_angle = angle_deg
    fm.use_override = bool(use_override)  # last: see the flapping note above
    return True


def build_sky(op: dict) -> dict:
    params = op.get("params", {})

    # Seed the geographic-sun inputs (time/date/place) from the shared world state when the op
    # omits them, so a bare build_sky honours a prior set_env instead of falling back to the noon
    # solar defaults. Explicit op params still win (they overlay the env seed), and an override is
    # left alone (it supplies elevation/azimuth directly, no solar inputs needed).
    env = _env.get_env()
    use_override = bool(_get(params, "use_override", False))
    # A geographic key this op passes that disagrees with bbt_env is the one thing this op CANNOT
    # make durable: the sun is recomputed from bbt_env on the next re-apply, not from what was
    # passed here. Collected before the seed merge (which would hide it) and named in the result, so
    # a caller learns to send the clock through set_env rather than finding out at gate C.
    undurable = [] if (use_override or env is None) else [
        k for k in GEO_KEYS
        if k in params and params[k] is not None and _differs(params[k], getattr(env, k))
    ]
    if not use_override and env is not None:
        params = {**_env.sun_params(env), **params}

    # Sun position: manual override, else the geographic solar model.
    if use_override:
        # Clamped and wrapped to the panel props' own ranges, so the sky node, the lamp and the
        # recorded override are one number rather than three that disagree at the edges.
        elevation = max(-90.0, min(90.0, float(_get(params, "sun_elevation", 45.0))))
        azimuth = float(_get(params, "sun_azimuth", 180.0)) % 360.0
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

    # The lamp's two knobs, clamped to the panel props' ranges for the same reason the angles are:
    # `sun_strength` and `sun_angle` are ALSO read back off bbt_firmament by the world applier, so a
    # value this op could set and the props could not is a sun that changes on the next re-apply.
    strength = max(0.0, float(_get(params, "sun_strength", 2.0)))
    sun_angle = max(0.0, min(20.0, float(_get(params, "sun_angle", 0.545))))

    sky_kw = {
        "sun_disc": bool(_get(params, "sun_disc", False)),
        "sun_size": math.radians(sun_angle),
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
    # Record BEFORE placing, so this op's own placement is the last write and the frame matches the
    # number this returns (a recorded prop fires a live-update callback of its own).
    recorded = _record_sun(bpy.context.scene, use_override=use_override, elevation=elevation,
                           azimuth=azimuth, strength=strength, angle_deg=sun_angle)
    _place_sun(sun, elevation, azimuth, strength=strength, angle_deg=sun_angle)

    # A rebuild clears and refills the world node tree; tag the world and sun so an
    # open viewport re-evaluates the new shader instead of showing a stale one.
    world.update_tag()
    sun.update_tag()

    info = f"{source} el={elevation:.1f} az={azimuth:.1f}"
    if not recorded:
        info += "; not recorded on bbt_firmament (BobFirmament's UI is not registered)"
    if undurable:
        info += (f"; {', '.join(undurable)} passed here but not in bbt_env -- the next world "
                 f"re-apply recomputes the sun from bbt_env, so send it through set_env to keep it")
    return {"op": "build_sky", "created": [world.name, sun.name], "info": info,
            "data": {"source": source, "elevation": round(elevation, 4),
                     "azimuth": round(azimuth, 4), "sun_strength": strength,
                     "sun_angle": sun_angle, "recorded": recorded,
                     "durable": bool(recorded and not undurable), "undurable": undurable}}
