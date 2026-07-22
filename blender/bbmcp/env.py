"""The shared world state: Scene.bbt_env, owned and registered by BobFirmament.

One place holds the whole world so every capability agrees on it: time, date,
season, place, weather, and the continuous values (wetness, snow, cloud cover,
wind) that surfaces and scatters respond to. BobFirmament registers it and is the
environment authority; Terrain, Scatter, and BobShaders read it and fall back to
their own defaults when it is absent (Firmament disabled), so each still works
standalone. This is a one-way dependency rooted at Firmament, so the graph stays
acyclic and a polyrepo split stays mechanical.

bpy-only. Firmament's own UI and subsystem state lives separately in
Scene.bbt_firmament (in the extension); this module is only the shared world.
"""

import bpy
from bpy.props import EnumProperty, FloatProperty, IntProperty
from bpy.types import PropertyGroup

SEASONS = (
    ("spring", "Spring", "Spring"),
    ("summer", "Summer", "Summer"),
    ("autumn", "Autumn", "Autumn"),
    ("winter", "Winter", "Winter"),
)

WEATHER = (
    ("clear", "Clear", "Clear sky"),
    ("cloudy", "Cloudy", "Broken cloud"),
    ("overcast", "Overcast", "Full cloud cover"),
    ("rain", "Rain", "Rainfall, wet ground"),
    ("storm", "Storm", "Heavy rain and wind"),
    ("snow", "Snow", "Snowfall, snow cover"),
    ("fog", "Fog", "Low visibility, still air"),
)


# Geographic-change hook registry. A consumer (BobFirmament) subscribes fn(scene) to react when a
# time/place field changes, so the sun re-places live on the edit. This mirrors the world-applier
# registry pattern but lives here because it must sit on the bbt_env property update callbacks: the
# registry keeps env.py the acyclic root (it never imports the consumer; the consumer subscribes).
_geo_hooks = []


def register_geo_hook(fn):
    """Subscribe fn(scene) to run when a geographic field (time/date/place) changes. Idempotent."""
    if fn not in _geo_hooks:
        _geo_hooks.append(fn)


def unregister_geo_hook(fn):
    if fn in _geo_hooks:
        _geo_hooks.remove(fn)


def _on_geo_change(self, context):
    """Update callback on the geographic fields: run every subscribed hook. A hook that errors
    never blocks the others (a consumer's edit must not break a slider drag)."""
    scene = getattr(context, "scene", None) or getattr(bpy.context, "scene", None)
    for fn in list(_geo_hooks):
        try:
            fn(scene)
        except Exception as exc:
            print(f"[bbmcp.env] geo hook failed: {exc}")


class BBT_EnvProps(PropertyGroup):
    """The canonical world state.

    Continuous values (wind, snow, cloud_cover) feed live via drivers, so moving a
    slider moves the built effect with no rebuild. The geographic fields (time/date/place)
    place the sun; they carry an update callback (_on_geo_change) so a consumer can re-place
    the sun live, since the sun position is a nonlinear solar calc and cannot be a driver.
    A change of season drives structural swaps applied by an explicit operator (Apply Season),
    not a property callback, to avoid the re-entrancy the scatter rebuilds hit.

    Some fields are authored here but read by consumers not yet built: temperature is context
    BobShaders will read (frost, dust). weather and wetness ARE live: env.weather and env.wetness
    drive every BobShader's ground wetness through materials.env_state_group (rain/storm wet the
    ground). cloud_cover is live-driven onto the cloud layer's Coverage by BobFirmament itself.
    """

    # Time and place: what the geographic sun is computed from. update=_on_geo_change re-places
    # the sun live (see the registry above).
    time_of_day: FloatProperty(
        name="Time of Day", default=12.0, min=0.0, max=24.0,
        description="Local clock time in hours (13.5 is 13:30)", update=_on_geo_change)
    year: IntProperty(name="Year", default=2026, min=1, max=9999, update=_on_geo_change)
    month: IntProperty(name="Month", default=6, min=1, max=12, update=_on_geo_change)
    day: IntProperty(name="Day", default=21, min=1, max=31, update=_on_geo_change)
    utc_offset: FloatProperty(
        name="UTC Offset", default=0.0, min=-14.0, max=14.0,
        description="Hours from UTC, east positive", update=_on_geo_change)
    latitude: FloatProperty(
        name="Latitude", default=45.0, min=-90.0, max=90.0,
        description="Degrees north", update=_on_geo_change)
    longitude: FloatProperty(
        name="Longitude", default=0.0, min=-180.0, max=180.0,
        description="Degrees east", update=_on_geo_change)

    season: EnumProperty(name="Season", items=SEASONS, default="summer")
    weather: EnumProperty(name="Weather", items=WEATHER, default="clear")

    # Continuous state: fed live into geometry-node and material inputs by the
    # consumers, so a surface responds without a rebuild.
    temperature: FloatProperty(
        name="Temperature", default=15.0, min=-60.0, max=60.0,
        description="Degrees Celsius")
    wetness: FloatProperty(name="Wetness", default=0.0, min=0.0, max=1.0)
    snow: FloatProperty(name="Snow", default=0.0, min=0.0, max=1.0,
                        description="Snow level, drives coverage and whitening")
    cloud_cover: FloatProperty(name="Cloud Cover", default=0.2, min=0.0, max=1.0)
    wind_direction: FloatProperty(
        name="Wind Direction", default=0.0, min=0.0, max=360.0,
        description="Degrees clockwise from north the wind blows toward")
    wind_strength: FloatProperty(name="Wind Strength", default=1.0, min=0.0)


def get_env(scene=None):
    """The world state for a scene, or None when BobFirmament is not registered.

    Consumers call this and guard for None: read the shared world when it is
    there, fall back to their own defaults when it is not.
    """
    scene = scene or getattr(bpy.context, "scene", None)
    return getattr(scene, "bbt_env", None) if scene is not None else None


def sun_params(env):
    """Pull the geographic-sun inputs out of the world state as a plain dict,
    ready to hand to solar.sun_position or a build_sky op."""
    return {
        "time_of_day": env.time_of_day,
        "year": env.year, "month": env.month, "day": env.day,
        "utc_offset": env.utc_offset,
        "latitude": env.latitude, "longitude": env.longitude,
    }


def register():
    bpy.utils.register_class(BBT_EnvProps)
    bpy.types.Scene.bbt_env = bpy.props.PointerProperty(type=BBT_EnvProps)


def unregister():
    del bpy.types.Scene.bbt_env
    bpy.utils.unregister_class(BBT_EnvProps)
