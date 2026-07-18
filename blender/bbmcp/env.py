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


class BBT_EnvProps(PropertyGroup):
    """The canonical world state. Continuous values are live; kind changes
    (season, weather) drive structural swaps applied by an explicit operator, not
    a property callback, to avoid the re-entrancy the scatter rebuilds hit."""

    # Time and place: what the geographic sun is computed from.
    time_of_day: FloatProperty(
        name="Time of Day", default=12.0, min=0.0, max=24.0,
        description="Local clock time in hours (13.5 is 13:30)")
    year: IntProperty(name="Year", default=2026, min=1, max=9999)
    month: IntProperty(name="Month", default=6, min=1, max=12)
    day: IntProperty(name="Day", default=21, min=1, max=31)
    utc_offset: FloatProperty(
        name="UTC Offset", default=0.0, min=-14.0, max=14.0,
        description="Hours from UTC, east positive")
    latitude: FloatProperty(
        name="Latitude", default=45.0, min=-90.0, max=90.0,
        description="Degrees north")
    longitude: FloatProperty(
        name="Longitude", default=0.0, min=-180.0, max=180.0,
        description="Degrees east")

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
