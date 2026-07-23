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

# Default snow line, NORMALIZED 0..1 (0 = valley floor / whole map snowed, 1 = above the peaks /
# snow clears). 0.7 reads as snow on the upper slopes under Conditions alone. The shader turns this
# into world Z using the terrain's Z bounds (snow_z_base/span), so the same value reads right at any
# terrain scale. Kept in sync with the matching driver default in materials.ENV_STATE_DRIVERS.
SNOW_LINE_DEFAULT = 0.7

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


def _snow_terrain(scene):
    """The terrain the snow line's Z bounds should map over. Firmament designates one snow surface
    per scene (fm.snow_surface); everything that stamps bounds (Apply Season, build snow, the picker)
    keys off it, so the drag callback must too or it fits to a different terrain and silently moves
    the line. Order: the designated snow surface, else the active object if it is a terrain, else the
    first bbt_terrain_height-stamped mesh (the single-terrain fallback). Read bbt_firmament by
    duck-typing so env.py stays the acyclic root and never imports the extension."""
    def _is_terrain(o):
        return o is not None and getattr(o, "type", None) == "MESH" and "bbt_terrain_height" in o
    fm = getattr(scene, "bbt_firmament", None)
    surf = getattr(fm, "snow_surface", None) if fm is not None else None
    if _is_terrain(surf):
        return surf
    active = getattr(getattr(bpy.context, "view_layer", None), "objects", None)
    active = getattr(active, "active", None)
    if _is_terrain(active):
        return active
    return next((o for o in scene.objects if _is_terrain(o)), None)


def _on_snow_line_change(self, context):
    """Update callback on snow_line: re-fit the terrain Z bounds from the scene's designated snow
    terrain, so the normalized line maps to the real terrain every time the artist drags it -- even
    on a terrain built before the bounds were first stamped. Keys off the same terrain the build and
    season paths do (_snow_terrain); no terrain, no change (defaults hold for a standalone asset)."""
    scene = getattr(context, "scene", None) or getattr(bpy.context, "scene", None)
    if scene is None:
        return
    terr = _snow_terrain(scene)
    if terr is not None:
        stamp_snow_bounds(scene, terr)


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
    # Snow has no amount slider: temperature drives whether it snows (below freezing = snow, colder
    # = thicker) and snow_line sets how far down it reaches. Two orthogonal controls, no third knob.
    # Snow line: normalized 0..1 over the terrain relief. Snow sits ABOVE the line, so 0 puts it at
    # the valley (whole map snowed) and 1 above the peaks (snow clears). Headroom past 0/1 lets the
    # artist force full or no cover. The shader scales this to world Z with snow_z_base/span.
    snow_line: FloatProperty(name="Snow Line", default=SNOW_LINE_DEFAULT, min=-0.25, max=1.25,
                             soft_min=0.0, soft_max=1.0, update=_on_snow_line_change,
                             description="How far down snow reaches, 0..1 (0 = whole map, 1 = "
                                         "peaks clear)")
    # Terrain Z bounds the snow line maps over, stamped on Apply Season / build snow (valley world-Z
    # and relief in metres). Defaults suit a mid-size terrain so a standalone asset still snows.
    # These are a single scene-global pair, stamped from the ONE designated snow terrain
    # (fm.snow_surface; see _snow_terrain). Every surface's material maps its own world-Z against this
    # one line, so a scene with two terrains at different Z ranges under one env shares a single snow
    # line and only the designated terrain reads correct. Per-terrain bounds would need the mapping
    # baked per material at apply time; the single-terrain model is canonical for now.
    snow_z_base: FloatProperty(name="Snow Z Base", default=0.0,
                               description="Valley world-Z the snow line maps from (set by the "
                                           "terrain)")
    snow_z_span: FloatProperty(name="Snow Z Span", default=20.0, min=0.001,
                               description="Terrain relief in metres the snow line maps over (set "
                                           "by the terrain)")
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


def stamp_snow_bounds(scene, obj):
    """Set the snow line's terrain Z bounds (snow_z_base/span) from obj's real world-Z extent, so
    the normalized snow line (0..1) maps to THIS terrain. Called whenever the terrain is known (on
    bake, on Apply Season, on build snow). Without it the line maps to defaults (0..20 m) and a
    terrain that dips below Z=0 or is a different height never fully covers at line 0. Uses the
    evaluated bounding box (post displacement). Returns True if it stamped."""
    from mathutils import Vector
    env = get_env(scene)
    if env is None or obj is None or getattr(obj, "type", None) != "MESH":
        return False
    try:
        dg = bpy.context.evaluated_depsgraph_get()
        ev = obj.evaluated_get(dg)
        mw = obj.matrix_world
        zs = [(mw @ Vector(c)).z for c in ev.bound_box]
    except (RuntimeError, AttributeError):
        return False
    base = min(zs)
    env.snow_z_base = base
    env.snow_z_span = max(max(zs) - base, 0.001)
    return True


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
