"""Firmament: the Atmosphere panel, peer to Terrain, Scatter, and Shaders.

Labelled "Atmosphere" in the tab since the 2026-07-20 UX redesign (docs/UX-REDESIGN.md). It
authors the built subsystems: Sky, Clouds, Fog, and Weather (rain/motes/snow coverage). The
shared world state (Scene.bbt_env) is still owned and registered here, but its UI (the
Environment sliders), the Preview/Final Quality level, the Live Environment master, and the
Scene Presets / Apply Season now live in the World panel (world_panel.py). This module keeps
the firmament_* operators; the World panel drives them.

The Live Environment master (bbt_world.live_env) and Quality reach this panel's subsystems
through the world applier registry: register() subscribes _apply_world, which (re)installs or
removes the wind/snow drivers and re-applies quality when a world control changes.

Two homes, no drift: the shared world is bbt_env (World panel); Firmament's own subsystem
state is bbt_firmament.
"""

import bpy
from bpy.props import (
    BoolProperty,
    EnumProperty,
    FloatProperty,
    PointerProperty,
    StringProperty,
)
from bpy.types import Operator, Panel, PropertyGroup

from . import server, ui_helpers, world_panel


def _live_env_on(scene):
    """The one master Live Environment toggle now lives on the World panel (bbt_world);
    default on when World is absent (standalone verify)."""
    return getattr(getattr(scene, "bbt_world", None), "live_env", True)

# The bbmcp.env module, imported and registered at addon register time and held so
# unregister uses the same object even after Reload Builders purges bbmcp.
_env = None

# Live cloud knobs, grouped for the panel (drawn from the modifier after a Build).
_CLOUD_SHAPE = ["Coverage", "Cloud Scale", "Warp"]
_CLOUD_SHAPE2 = ["Detail", "Softness", "Density"]
_CLOUD_LAYER = ["Layer Size", "Thickness", "Height"]
_CLOUD_WIND = ["Wind Direction", "Wind Speed"]

# Live fog knobs, grouped for the panel.
_FOG_SHAPE = ["Density", "Fog Noise", "Fog Scale", "Softness", "Warp"]
_FOG_LOOK = ["Fog Color", "Anisotropy"]
_FOG_LAYER = ["Layer Size", "Thickness", "Height", "Fog Top", "Falloff"]
_FOG_GROUND = ["Terrain Size", "Terrain Height", "Sea Level", "Ground Thickness"]
_FOG_WIND = ["Wind Direction", "Wind Speed"]

# Cloud-type presets: a Blender-side dict (no codegen; nothing else reads it). Each
# sets the live modifier knobs by socket name for a named sky look.
CLOUD_PRESETS = {
    "clear": {"label": "Clear", "desc": "A few thin wisps",
              "knobs": {"Coverage": 0.12, "Cloud Scale": 0.022, "Density": 7.0,
                        "Detail": 5.0, "Softness": 0.30, "Warp": 0.4, "Thickness": 35.0}},
    "scattered": {"label": "Scattered", "desc": "Fair-weather clouds with gaps",
                  "knobs": {"Coverage": 0.30, "Cloud Scale": 0.020, "Density": 9.0,
                            "Detail": 6.0, "Softness": 0.20, "Warp": 0.5, "Thickness": 42.0}},
    "cumulus": {"label": "Cumulus", "desc": "Big billowing clouds",
                "knobs": {"Coverage": 0.42, "Cloud Scale": 0.014, "Density": 11.0,
                          "Detail": 7.0, "Softness": 0.16, "Warp": 0.6, "Thickness": 60.0}},
    "overcast": {"label": "Overcast", "desc": "Full grey blanket",
                 "knobs": {"Coverage": 0.80, "Cloud Scale": 0.030, "Density": 8.0,
                           "Detail": 5.0, "Softness": 0.32, "Warp": 0.35, "Thickness": 45.0}},
    "storm": {"label": "Storm", "desc": "Dark, heavy, deep",
              "knobs": {"Coverage": 0.90, "Cloud Scale": 0.012, "Density": 15.0,
                        "Detail": 7.0, "Softness": 0.22, "Warp": 0.55, "Thickness": 90.0}},
}

# Fog-type presets (live modifier knobs, set within whichever fog mode was built).
FOG_PRESETS = {
    "ground_mist": {"label": "Ground Mist", "desc": "Thin, near-uniform low mist",
                    "knobs": {"Density": 1.0, "Fog Noise": 0.10, "Fog Scale": 0.04,
                              "Softness": 0.40, "Fog Top": 0.50, "Thickness": 25.0,
                              "Height": 12.0, "Warp": 0.30, "Falloff": 2.0,
                              "Anisotropy": 0.5}},
    "valley": {"label": "Valley Fog", "desc": "Denser fog that fills the low ground",
               "knobs": {"Density": 2.5, "Fog Noise": 0.20, "Fog Scale": 0.03,
                         "Softness": 0.40, "Fog Top": 0.70, "Thickness": 50.0,
                         "Height": 22.0, "Warp": 0.40, "Falloff": 1.6,
                         "Anisotropy": 0.5}},
    "banks": {"label": "Fog Banks", "desc": "Patchy drifting banks",
              "knobs": {"Density": 3.0, "Fog Noise": 0.85, "Fog Scale": 0.025,
                        "Softness": 0.30, "Fog Top": 0.60, "Thickness": 45.0,
                        "Height": 25.0, "Warp": 0.50, "Falloff": 1.2,
                        "Anisotropy": 0.4}},
    "thick": {"label": "Thick Fog", "desc": "Dense soup, low visibility",
              "knobs": {"Density": 6.0, "Fog Noise": 0.50, "Fog Scale": 0.02,
                        "Softness": 0.45, "Fog Top": 0.85, "Thickness": 70.0,
                        "Height": 30.0, "Warp": 0.35, "Falloff": 1.0,
                        "Anisotropy": 0.3}},
}

# Live weather knobs, grouped for the panel.
_RAIN_KNOBS = ["Count", "Fall Speed", "Streak Length", "Size", "Size Variation"]
_RAIN_WIND = ["Wind Direction", "Wind Speed", "Drift"]
_MOTE_KNOBS = ["Count", "Fall Speed", "Drift", "Turbulence", "Size", "Size Variation"]
_MOTE_LOOK = ["Color", "Emission"]
_MOTE_WIND = ["Wind Direction", "Wind Speed"]
_DOMAIN_KNOBS = ["Domain Size", "Domain Height"]
_SNOW_KNOBS = ["Snow", "Slope Threshold", "Slope Falloff", "Altitude",
               "Altitude Falloff", "Occlusion", "Occlusion Distance"]

# Rain presets (streak mode): live knobs set by socket name.
RAIN_PRESETS = {
    "drizzle": {"label": "Drizzle", "desc": "Light, fine rain",
                "knobs": {"Count": 1200, "Fall Speed": 6.0, "Streak Length": 0.14,
                          "Size": 0.006, "Size Variation": 0.4}},
    "rain": {"label": "Rain", "desc": "Steady rainfall",
             "knobs": {"Count": 3500, "Fall Speed": 9.0, "Streak Length": 0.22,
                       "Size": 0.010, "Size Variation": 0.4}},
    "downpour": {"label": "Downpour", "desc": "Heavy, fast, long streaks",
                 "knobs": {"Count": 6000, "Fall Speed": 13.0, "Streak Length": 0.30,
                           "Size": 0.014, "Size Variation": 0.5}},
}

# Mote presets (mote mode): dust, amber motes, and falling snow are the same mode,
# a preset picks the look. Falling snow is the plan's snow mote preset.
MOTE_PRESETS = {
    "dust": {"label": "Dust", "desc": "Tan dust, denser and wind-driven",
             "knobs": {"Color": (0.60, 0.50, 0.35, 1.0), "Count": 1500,
                       "Fall Speed": 0.3, "Drift": 1.5, "Turbulence": 1.2,
                       "Size": 0.03, "Emission": 0.0}},
    "amber": {"label": "Amber Motes", "desc": "Fine sun-lit golden-hour specks",
              "knobs": {"Color": (1.0, 0.65, 0.28, 1.0), "Count": 600,
                        "Fall Speed": 0.12, "Drift": 0.5, "Turbulence": 0.7,
                        "Size": 0.025, "Emission": 0.0}},
    "snow": {"label": "Falling Snow", "desc": "White, slow, fluttering flakes",
             "knobs": {"Color": (1.0, 1.0, 1.0, 1.0), "Count": 2000,
                       "Fall Speed": 0.6, "Drift": 1.0, "Turbulence": 1.5,
                       "Size": 0.035, "Emission": 0.0}},
}

# Per-season application: the continuous env values a season implies (fed live to the
# readers) plus, for winter, the structural subsystems to build (falling snow + the
# snow-coverage pass). Applied by an explicit operator, never a property callback, so
# it does not hit the scatter re-entrancy. Season deliberately owns only the seasonal
# state and its own subsystems; it leaves time, place, and wind (the shot setup) alone.
SEASON_APPLY = {
    "spring": {"snow": 0.0, "wetness": 0.20, "temperature": 12.0},
    "summer": {"snow": 0.0, "wetness": 0.0, "temperature": 24.0},
    "autumn": {"snow": 0.0, "wetness": 0.15, "temperature": 10.0},
    "winter": {"snow": 0.7, "wetness": 0.05, "temperature": -4.0,
               "build_snow": True},
}

# Whole-scene presets: one pick sets the shared world state (bbt_env) and seeds each
# named subsystem, building any that are missing (at the current quality, so a Preview
# pick stays cheap). A subsystem set to None is left alone, not deleted. `fog` is
# (mode, preset); the others are a preset key.
SCENE_PRESETS = {
    "clear_day": {
        "label": "Clear Day", "desc": "High midday sun, a few thin clouds",
        "env": {"time_of_day": 13.0, "weather": "clear", "season": "summer",
                "cloud_cover": 0.15, "snow": 0.0, "wetness": 0.0,
                "temperature": 24.0, "wind_direction": 90.0, "wind_strength": 1.5},
        "clouds": "clear", "fog": None, "rain": None, "motes": None},
    "golden_hour": {
        "label": "Golden Hour", "desc": "Low warm sun, scattered cloud, amber motes",
        "env": {"time_of_day": 18.5, "weather": "clear", "season": "summer",
                "cloud_cover": 0.30, "snow": 0.0, "wetness": 0.0,
                "temperature": 20.0, "wind_direction": 120.0, "wind_strength": 1.0},
        "clouds": "scattered", "fog": None, "rain": None, "motes": "amber"},
    "overcast": {
        "label": "Overcast", "desc": "Flat grey blanket, still air",
        "env": {"time_of_day": 12.0, "weather": "overcast", "season": "autumn",
                "cloud_cover": 0.85, "snow": 0.0, "wetness": 0.20,
                "temperature": 12.0, "wind_direction": 200.0, "wind_strength": 2.0},
        "clouds": "overcast", "fog": None, "rain": None, "motes": None},
    "storm": {
        "label": "Storm", "desc": "Dark deep cloud, heavy rain, strong wind",
        "env": {"time_of_day": 16.0, "weather": "storm", "season": "autumn",
                "cloud_cover": 0.95, "snow": 0.0, "wetness": 0.9,
                "temperature": 9.0, "wind_direction": 210.0, "wind_strength": 7.0},
        "clouds": "storm", "fog": ("height_fog", "valley"),
        "rain": "downpour", "motes": None},
    "foggy_dawn": {
        "label": "Foggy Dawn", "desc": "Low sun through a valley of fog",
        "env": {"time_of_day": 6.5, "weather": "fog", "season": "autumn",
                "cloud_cover": 0.40, "snow": 0.0, "wetness": 0.30,
                "temperature": 7.0, "wind_direction": 160.0, "wind_strength": 0.5},
        "clouds": "scattered", "fog": ("height_fog", "valley"),
        "rain": None, "motes": None},
    "dust_storm": {
        "label": "Dust Storm", "desc": "Hazy sky, dust driven on a hot wind",
        "env": {"time_of_day": 15.0, "weather": "cloudy", "season": "summer",
                "cloud_cover": 0.25, "snow": 0.0, "wetness": 0.0,
                "temperature": 34.0, "wind_direction": 250.0, "wind_strength": 6.0},
        "clouds": "scattered", "fog": ("noise_fog", "banks"),
        "rain": None, "motes": "dust"},
    "winter": {
        "label": "Winter", "desc": "Cold overcast, falling snow, white ground",
        "env": {"time_of_day": 11.0, "weather": "snow", "season": "winter",
                "cloud_cover": 0.80, "snow": 0.7, "wetness": 0.05,
                "temperature": -4.0, "wind_direction": 300.0, "wind_strength": 2.5},
        "clouds": "overcast", "fog": None, "rain": None, "motes": "snow"},
}


def _apply(ops):
    """Run bbmcp ops in-process, the path the Scatter and terrain panels use."""
    server._ensure_path()
    from bbmcp.dispatch import apply_op

    return [apply_op(op) for op in ops]


def _nodes_mod(obj):
    if obj is None:
        return None
    return next((m for m in obj.modifiers if m.type == "NODES"), None)


def _input(obj, socket_name):
    """The live modifier input struct for a socket name, or None."""
    mod = _nodes_mod(obj)
    if mod is None or mod.node_group is None:
        return None
    ident = next((it.identifier for it in mod.node_group.interface.items_tree
                  if getattr(it, "item_type", None) == "SOCKET"
                  and it.in_out == "INPUT" and it.name == socket_name), None)
    return getattr(mod.properties.inputs, ident, None) if ident else None


def _draw_knobs(layout, obj, names):
    """Draw each present modifier input by socket name (live, no rebuild)."""
    mod = _nodes_mod(obj)
    if mod is None or mod.node_group is None:
        return
    ids = {it.name: it.identifier for it in mod.node_group.interface.items_tree
           if getattr(it, "item_type", None) == "SOCKET" and it.in_out == "INPUT"}
    col = layout.column(align=True)
    for nm in names:
        ident = ids.get(nm)
        inp = getattr(mod.properties.inputs, ident, None) if ident else None
        if inp is not None:
            col.prop(inp, "value", text=nm)


def _named_mod(obj, mod_name):
    """A specific NODES modifier by name (an object may carry more than one, e.g. a
    terrain with both its terrain modifier and the snow-coverage pass)."""
    if obj is None:
        return None
    return next((m for m in obj.modifiers if m.type == "NODES" and m.name == mod_name), None)


def _input_of(mod, socket_name):
    if mod is None or mod.node_group is None:
        return None
    ident = next((it.identifier for it in mod.node_group.interface.items_tree
                  if getattr(it, "item_type", None) == "SOCKET"
                  and it.in_out == "INPUT" and it.name == socket_name), None)
    return getattr(mod.properties.inputs, ident, None) if ident else None


def _draw_knobs_mod(layout, mod, names):
    """Draw each present input of a specific modifier by socket name (live)."""
    if mod is None or mod.node_group is None:
        return
    ids = {it.name: it.identifier for it in mod.node_group.interface.items_tree
           if getattr(it, "item_type", None) == "SOCKET" and it.in_out == "INPUT"}
    col = layout.column(align=True)
    for nm in names:
        ident = ids.get(nm)
        inp = getattr(mod.properties.inputs, ident, None) if ident else None
        if inp is not None:
            col.prop(inp, "value", text=nm)


def _show_domain_gizmo(obj):
    """Draw the domain as a wireframe box in the viewport, not a solid box.

    WIRE, not BOUNDS: the box is generated by the GN modifier, so the object's
    bounding box (what BOUNDS draws) comes from the empty base mesh and is not true
    to the volume size. WIRE draws the evaluated geometry, so the wireframe matches
    the actual domain. display_type is viewport-only, so the render is untouched;
    the material has no surface output, so the box was never visible in a render
    anyway. Like an Unreal volume's bounds.
    """
    if obj is not None:
        obj.display_type = "WIRE"


# Quality levels: the Cycles volume settings (cost-spike defaults) plus the
# particulate count scale. Volume bounces (multiple scattering) are kept low: 0 for
# preview keeps self-shadowing single-scatter and cheap, a couple for final let light
# re-scatter so shadowed cloud reads bright instead of muddy. The particulate scale
# thins the field for the viewport and restores it for a final render.
_QUALITY = {
    "preview": {"step_rate": 2.0, "max_steps": 256, "bounces": 0, "particulate": 0.35},
    "final": {"step_rate": 1.0, "max_steps": 512, "bounces": 2, "particulate": 1.0},
}


def _apply_quality(scene):
    """Apply the Preview/Final level to every Firmament build: scale the particulate
    counts live (a Quality Scale modifier input, no rebuild) and set the Cycles volume
    step rate, max steps, and bounces. Called by every build op and by the World quality
    control (via the world applier), so switching quality re-applies without a rebuild.
    The level lives on the World panel (bbt_world) since it is scene-wide, not atmosphere-
    specific; default Preview when World is absent (standalone verify)."""
    fm = getattr(scene, "bbt_firmament", None)
    if fm is None:
        return
    level = getattr(getattr(scene, "bbt_world", None), "quality", "preview")
    q = _QUALITY.get(level, _QUALITY["preview"])
    for name in (fm.rain_object, fm.mote_object):
        inp = _input(bpy.data.objects.get(name), "Quality Scale")
        if inp is not None:
            inp.value = q["particulate"]
    cy = getattr(scene, "cycles", None)
    if cy is not None:
        cy.volume_step_rate = q["step_rate"]
        cy.volume_max_steps = q["max_steps"]
        cy.volume_bounces = q["bounces"]


def _drive_input(obj, inp, scene, env_path):
    """Install a driver on a modifier input's value that reads a Scene bbt_env field
    live, so moving the Environment slider moves the built effect with no rebuild and
    no per-object press. The input struct owns its animation through the object, so
    inp.driver_add('value') routes to obj.animation_data with the correct RNA path
    (verified in 5.2). Any prior driver on the same input is cleared first so a rebuild
    (which regenerates socket identifiers) never leaves a stale, dangling driver."""
    if inp is None:
        return
    try:
        obj.driver_remove(inp.path_from_id("value"), -1)
    except (TypeError, RuntimeError):
        pass
    fc = inp.driver_add("value")
    fc = fc[0] if isinstance(fc, list) else fc
    drv = fc.driver
    drv.type = "SCRIPTED"
    var = drv.variables.new()
    var.name = "v"
    var.type = "SINGLE_PROP"
    tgt = var.targets[0]
    tgt.id_type = "SCENE"
    tgt.id = scene
    tgt.data_path = env_path
    drv.expression = "v"


def _undrive_input(obj, inp):
    if obj is None or inp is None:
        return
    try:
        obj.driver_remove(inp.path_from_id("value"), -1)
    except (TypeError, RuntimeError):
        pass


# Per-object extra live-env inputs beyond wind: (socket name, bbt_env path). The cloud
# layer's Coverage is driven from env.cloud_cover so the Environment cloud-cover slider
# controls the clouds, the same live model as wind.
_CLOUD_EXTRA = (("Coverage", "bbt_env.cloud_cover"),)


def _install_wind_drivers(obj, scene, extra=()):
    """Feed Wind Direction / Speed (and any extra (socket, env_path) pairs) from bbt_env
    live, and enable the Wind toggle on a volume so its drift is active. Reinstalled by
    every build (socket identifiers are regenerated on the non-destructive rebuild, so a
    driver keyed by identifier must be re-added; our build ops are the only path that
    rebuilds these objects)."""
    if obj is None:
        return
    _drive_input(obj, _input(obj, "Wind Direction"), scene, "bbt_env.wind_direction")
    _drive_input(obj, _input(obj, "Wind Speed"), scene, "bbt_env.wind_strength")
    wind = _input(obj, "Wind")  # volumes carry a Wind toggle; particulates do not
    if wind is not None:
        wind.value = True
    for socket, path in extra:
        _drive_input(obj, _input(obj, socket), scene, path)
    obj.update_tag()


def _remove_wind_drivers(obj, extra=()):
    if obj is None:
        return
    _undrive_input(obj, _input(obj, "Wind Direction"))
    _undrive_input(obj, _input(obj, "Wind Speed"))
    for socket, _path in extra:
        _undrive_input(obj, _input(obj, socket))
    obj.update_tag()


def _snow_input(surface):
    """The Snow input struct of the BOB_Snow coverage pass on a surface, or None."""
    return _input_of(_named_mod(surface, "BOB_Snow"), "Snow")


def _install_snow_driver(surface, scene):
    """Feed the coverage pass's Snow amount from bbt_env.snow live, so raising the
    Environment snow level (or an Apply Season -> Winter) drives coverage with no
    rebuild. The terrain carries two Nodes modifiers, so this targets BOB_Snow by
    name, not the first modifier."""
    inp = _snow_input(surface)
    if inp is not None:
        _drive_input(surface, inp, scene, "bbt_env.snow")
        surface.update_tag()


def _firmament_wind_objects(fm):
    """The built clouds, fog, rain, and mote objects (those that exist)."""
    names = (fm.cloud_object, fm.fog_object, fm.rain_object, fm.mote_object)
    return [o for o in (bpy.data.objects.get(n) for n in names) if o is not None]


def _apply_world(scene):
    """Atmosphere's world applier (subscribed with world_panel): re-apply the atmosphere
    subsystems to the current world state. Installs or removes the live wind/snow drivers per
    the master Live Environment toggle, and re-applies the Quality level. A non-structural
    driver edit, safe from the rebuild re-entrancy the repo avoids for structural changes.
    Adding a new atmosphere subsystem needs no new toggle wiring: it just gets driven here."""
    fm = getattr(scene, "bbt_firmament", None)
    if fm is None:
        return
    live = _live_env_on(scene)
    clouds = bpy.data.objects.get(fm.cloud_object)
    for obj in _firmament_wind_objects(fm):
        extra = _CLOUD_EXTRA if obj is clouds else ()
        if live:
            _install_wind_drivers(obj, scene, extra)
        else:
            _remove_wind_drivers(obj, extra)
    surface = fm.snow_surface or getattr(bpy.context, "active_object", None)
    if _named_mod(surface, "BOB_Snow") is not None:
        if live:
            _install_snow_driver(surface, scene)
        else:
            _undrive_input(surface, _snow_input(surface))
    _apply_quality(scene)


class BBT_FirmamentProps(PropertyGroup):
    """Firmament's own UI and subsystem state (not the shared world)."""

    # Sun override on top of the geographic position.
    use_override: BoolProperty(
        name="Manual Sun", default=False,
        description="Set the sun angle by hand instead of from time and place")
    override_elevation: FloatProperty(
        name="Elevation", default=45.0, min=-90.0, max=90.0,
        description="Degrees above the horizon")
    override_azimuth: FloatProperty(
        name="Azimuth", default=180.0, min=0.0, max=360.0,
        description="Degrees clockwise from north")

    # Sun light.
    sun_strength: FloatProperty(name="Sun Strength", default=2.0, min=0.0)
    sun_angle: FloatProperty(
        name="Sun Size", default=0.545, min=0.0, max=20.0,
        description="Angular diameter in degrees; larger softens shadows")
    sun_disc: BoolProperty(
        name="Show Sun Disc", default=False,
        description="Draw the sky's sun disc. Off by default so the lamp lights "
                    "and the sun is not counted twice")

    # Physical sky (5.2 MULTIPLE_SCATTERING; the Nishita successor).
    world_strength: FloatProperty(name="Sky Strength", default=1.0, min=0.0)
    sky_altitude: FloatProperty(
        name="Altitude", default=200.0, min=0.0, max=60000.0,
        description="Observer altitude in metres")
    air: FloatProperty(name="Air", default=1.0, min=0.0, max=10.0)
    ozone: FloatProperty(name="Ozone", default=1.0, min=0.0, max=10.0)
    turbidity: FloatProperty(
        name="Turbidity", default=2.2, min=1.0, max=10.0,
        description="Atmospheric haziness (the 5.2 sky's dust replacement)")
    ground_albedo: FloatProperty(name="Ground Albedo", default=0.3, min=0.0, max=1.0)

    # Quality (Preview/Final) and the Live Environment master toggle moved to the World panel
    # (bbt_world); they are scene-wide, not atmosphere-specific (docs/UX-REDESIGN.md 5.1, C).

    # Clouds: the cloud layer is one domain box; its knobs live on the modifier.
    cloud_object: StringProperty(name="Object", default="BOB_Clouds")
    cloud_shadows: BoolProperty(
        name="Cloud Shadows", default=True,
        description="Clouds self-shadow (dimensional form) and cast shadows on the "
                    "scene. On by default and cheap at normal sun angles, since a "
                    "high sun's shadow rays only cross the layer thickness. Turn off "
                    "for a flat, faster look when the sun is low and the frame is "
                    "Final quality, where near-horizontal shadow rays get expensive")

    # Fog: a bounded domain slab, same one-box pattern as clouds.
    fog_object: StringProperty(name="Object", default="BOB_Fog")
    fog_mode: EnumProperty(
        name="Fog Mode",
        items=[("height_fog", "Height Fog",
                "A near-uniform slab, densest low, thinning with height. Aerial "
                "perspective; valleys fill and hills poke out"),
               ("noise_fog", "Noise Fog",
                "The slab broken into soft, patchy, drifting banks"),
               ("ground_fog", "Ground Fog",
                "Mist that hugs the terrain surface, sampled from a heightmap, so it "
                "follows hills up and over instead of sitting at a fixed height")],
        default="height_fog")
    fog_heightmap: StringProperty(
        name="Heightmap", default="", subtype="FILE_PATH",
        description="Terrain heightmap the ground fog drapes over. Match Terrain "
                    "Size/Height/Sea Level to the heightmap_terrain build that used it")

    # Weather (S4): rain streaks and dust/amber/snow motes in a camera-following
    # domain, plus the snow-coverage pass that writes snow_cover on the terrain.
    weather_camera: PointerProperty(
        name="Camera", type=bpy.types.Object,
        poll=lambda self, obj: obj.type == "CAMERA",
        description="The particle domain re-tiles around this camera so the weather "
                    "is always around the shot. Leave empty to anchor it at the origin")
    use_motion_blur: BoolProperty(
        name="Motion Blur", default=True,
        description="Enable scene motion blur so fast particles read as streaks in "
                    "the render. Rain streaks are also real geometry, so they read "
                    "with or without it")
    rain_object: StringProperty(name="Object", default="BOB_Rain")
    mote_object: StringProperty(name="Object", default="BOB_Motes")
    snow_surface: PointerProperty(
        name="Surface", type=bpy.types.Object,
        poll=lambda self, obj: obj.type == "MESH",
        description="The terrain the snow-coverage pass writes snow_cover onto (BobShaders "
                    "reads that attribute). Defaults to the active mesh")


class BBT_OT_firmament_build_sky(Operator):
    bl_idname = "bob_blender_tools.firmament_build_sky"
    bl_label = "Build Sky"
    bl_description = "Author the physical sky and matched Sun light from the world state"

    def execute(self, context):
        env = _env.get_env(context.scene)
        fm = context.scene.bbt_firmament
        params = {
            # Geographic sun inputs come from the shared accessor (the same dict a
            # build_sky caller would assemble), so the documented sun_params API is
            # the one real path, not a hand-rebuilt duplicate.
            **_env.sun_params(env),
            "use_override": fm.use_override,
            "sun_elevation": fm.override_elevation,
            "sun_azimuth": fm.override_azimuth,
            "sun_strength": fm.sun_strength,
            "sun_angle": fm.sun_angle,
            "sun_disc": fm.sun_disc,
            "world_strength": fm.world_strength,
            "altitude": fm.sky_altitude,
            "air": fm.air, "ozone": fm.ozone,
            "turbidity": fm.turbidity, "ground_albedo": fm.ground_albedo,
        }
        res = _apply([{"op": "build_sky", "params": params}])
        self.report({"INFO"}, f"Sky: {res[0].get('info', '')}")
        return {"FINISHED"}


class BBT_OT_firmament_build_clouds(Operator):
    bl_idname = "bob_blender_tools.firmament_build_clouds"
    bl_label = "Build Clouds"
    bl_description = "Build the procedural volumetric cloud layer"

    def execute(self, context):
        fm = context.scene.bbt_firmament
        env = _env.get_env(context.scene)
        params = {"mode": "clouds"}
        if env is not None:  # seed the wind + coverage knobs from the shared world state
            params["wind_direction"] = env.wind_direction
            params["wind_speed"] = env.wind_strength
            params["coverage"] = env.cloud_cover
        _apply([{"op": "build_geonodes", "recipe": "volumetrics",
                 "name": fm.cloud_object, "params": params}])
        _apply_quality(context.scene)
        n = 0
        obj = bpy.data.objects.get(fm.cloud_object)
        _show_domain_gizmo(obj)
        if _live_env_on(context.scene):
            _install_wind_drivers(obj, context.scene, _CLOUD_EXTRA)
        if obj is not None:
            # Shadow fork (Phase-0 / S2 cost): a cloud volume that casts shadows
            # makes every lit point march a shadow ray through it, which is the
            # expensive path. Default ON for dimensional form (cheap at a normal sun
            # height); turn off for the low-sun Final case where it gets expensive.
            obj.visible_shadow = fm.cloud_shadows
            dg = context.evaluated_depsgraph_get()
            n = sum(1 for i in dg.object_instances if i.is_instance
                    and i.parent is not None and i.parent.original.name == obj.name)
        self.report({"INFO"}, f"Clouds: {n} puffs")
        return {"FINISHED"}


class BBT_OT_firmament_cloud_preset(Operator):
    bl_idname = "bob_blender_tools.firmament_cloud_preset"
    bl_label = "Cloud Preset"
    bl_description = "Set the cloud look from a named preset"
    bl_options = {"REGISTER", "UNDO"}

    preset: EnumProperty(
        name="Preset",
        items=[(k, v["label"], v["desc"]) for k, v in CLOUD_PRESETS.items()])

    def execute(self, context):
        fm = context.scene.bbt_firmament
        obj = bpy.data.objects.get(fm.cloud_object)
        if obj is None or _nodes_mod(obj) is None:
            bpy.ops.bob_blender_tools.firmament_build_clouds()
            obj = bpy.data.objects.get(fm.cloud_object)
        if obj is None:
            return {"CANCELLED"}
        knobs = CLOUD_PRESETS[self.preset]["knobs"]
        for name, val in knobs.items():
            inp = _input(obj, name)
            if inp is not None:
                inp.value = val
        # Keep env.cloud_cover in step with the preset's Coverage, so the preset reads
        # right whether Coverage is live-driven from the Environment or set directly.
        env = _env.get_env(context.scene)
        if env is not None and "Coverage" in knobs:
            env.cloud_cover = knobs["Coverage"]
        obj.update_tag()
        self.report({"INFO"}, f"Applied {CLOUD_PRESETS[self.preset]['label']} preset")
        return {"FINISHED"}


class BBT_OT_firmament_build_fog(Operator):
    bl_idname = "bob_blender_tools.firmament_build_fog"
    bl_label = "Build Fog"
    bl_description = "Build a procedural volumetric fog domain"

    def execute(self, context):
        fm = context.scene.bbt_firmament
        env = _env.get_env(context.scene)
        params = {"mode": fm.fog_mode}
        if env is not None:  # seed the wind knobs from the shared world state
            params["wind_direction"] = env.wind_direction
            params["wind_speed"] = env.wind_strength
        if fm.fog_mode == "ground_fog" and fm.fog_heightmap:
            params["heightmap"] = bpy.path.abspath(fm.fog_heightmap)
        _apply([{"op": "build_geonodes", "recipe": "volumetrics",
                 "name": fm.fog_object, "params": params}])
        _apply_quality(context.scene)
        fog_obj = bpy.data.objects.get(fm.fog_object)
        _show_domain_gizmo(fog_obj)
        if _live_env_on(context.scene):
            _install_wind_drivers(fog_obj, context.scene)
        label = {"height_fog": "Height Fog", "noise_fog": "Noise Fog",
                 "ground_fog": "Ground Fog"}[fm.fog_mode]
        self.report({"INFO"}, f"Fog: built {label}")
        return {"FINISHED"}


class BBT_OT_firmament_fog_preset(Operator):
    bl_idname = "bob_blender_tools.firmament_fog_preset"
    bl_label = "Fog Preset"
    bl_description = "Set the fog look from a named preset"
    bl_options = {"REGISTER", "UNDO"}

    preset: EnumProperty(
        name="Preset",
        items=[(k, v["label"], v["desc"]) for k, v in FOG_PRESETS.items()])

    def execute(self, context):
        fm = context.scene.bbt_firmament
        obj = bpy.data.objects.get(fm.fog_object)
        if obj is None or _nodes_mod(obj) is None:
            bpy.ops.bob_blender_tools.firmament_build_fog()
            obj = bpy.data.objects.get(fm.fog_object)
        if obj is None:
            return {"CANCELLED"}
        for name, val in FOG_PRESETS[self.preset]["knobs"].items():
            inp = _input(obj, name)
            if inp is not None:
                inp.value = val
        obj.update_tag()
        self.report({"INFO"}, f"Applied {FOG_PRESETS[self.preset]['label']} preset")
        return {"FINISHED"}


def _build_particulate(context, obj_name, mode, extra=None):
    """Build a particulates object (streak or mote), seeding wind and the follow
    camera from the panel and the shared world state, and turning on motion blur."""
    fm = context.scene.bbt_firmament
    env = _env.get_env(context.scene)
    params = {"mode": mode}
    # Follow the panel camera, else the scene camera, so preset/season-built weather is
    # around the shot rather than stuck at the world origin when none is picked.
    camera = fm.weather_camera or context.scene.camera
    if camera is not None:
        params["camera"] = camera.name
    if env is not None:  # seed the wind knobs from the shared world state
        params["wind_direction"] = env.wind_direction
        params["wind_speed"] = env.wind_strength
    if extra:
        params.update(extra)
    _apply([{"op": "build_geonodes", "recipe": "particulates",
             "name": obj_name, "params": params}])
    obj = bpy.data.objects.get(obj_name)
    _apply_quality(context.scene)  # set Quality Scale from the level
    if _live_env_on(context.scene):
        _install_wind_drivers(obj, context.scene)
    if fm.use_motion_blur:
        context.scene.render.use_motion_blur = True
        # Also enable it on the object so fast particles are guaranteed included, not
        # just the scene-level switch.
        if obj is not None and hasattr(obj, "cycles"):
            obj.cycles.use_motion_blur = True
    return obj


def _count_instances(context, obj):
    dg = context.evaluated_depsgraph_get()
    return sum(1 for i in dg.object_instances if i.is_instance
               and i.parent is not None and i.parent.original.name == obj.name)


class BBT_OT_firmament_build_rain(Operator):
    bl_idname = "bob_blender_tools.firmament_build_rain"
    bl_label = "Build Rain"
    bl_description = "Build the rain streak particle system in a camera-following domain"

    def execute(self, context):
        fm = context.scene.bbt_firmament
        obj = _build_particulate(context, fm.rain_object, "streak")
        n = _count_instances(context, obj) if obj is not None else 0
        self.report({"INFO"}, f"Rain: {n} streaks")
        return {"FINISHED"}


class BBT_OT_firmament_build_motes(Operator):
    bl_idname = "bob_blender_tools.firmament_build_motes"
    bl_label = "Build Motes"
    bl_description = "Build the dust / amber / snow mote particle system (pick a preset)"

    def execute(self, context):
        fm = context.scene.bbt_firmament
        obj = _build_particulate(context, fm.mote_object, "mote")
        n = _count_instances(context, obj) if obj is not None else 0
        self.report({"INFO"}, f"Motes: {n} motes")
        return {"FINISHED"}


class BBT_OT_firmament_randomize_seed(Operator):
    # One reshuffle-seed operator for every atmosphere object (clouds, fog, rain, motes):
    # they only differ by which object and which named Seed input, so both are properties
    # rather than three near-identical operator classes.
    bl_idname = "bob_blender_tools.firmament_randomize_seed"
    bl_label = "Randomize Seed"
    bl_description = "Reshuffle this atmosphere pattern with a new seed"

    object_name: StringProperty()
    seed_input: StringProperty(default="Seed")

    def execute(self, context):
        import random

        obj = bpy.data.objects.get(self.object_name)
        seed = _input(obj, self.seed_input)
        if seed is None:
            return {"CANCELLED"}
        seed.value = random.randint(0, 99999)
        obj.update_tag()
        return {"FINISHED"}


class BBT_OT_firmament_wind_from_env(Operator):
    # One "copy Environment wind onto this object" operator for every atmosphere object;
    # they were identical apart from how the object was resolved, so it takes object_name.
    bl_idname = "bob_blender_tools.firmament_wind_from_env"
    bl_label = "Use Env Wind"
    bl_description = "Copy the Environment wind direction and strength onto this object"

    object_name: StringProperty()

    def execute(self, context):
        env = _env.get_env(context.scene)
        obj = bpy.data.objects.get(self.object_name)
        wdir, wspd = _input(obj, "Wind Direction"), _input(obj, "Wind Speed")
        if env is None or wdir is None or wspd is None:
            return {"CANCELLED"}
        wdir.value = env.wind_direction
        wspd.value = env.wind_strength
        obj.update_tag()
        self.report({"INFO"}, "Wind synced from Environment")
        return {"FINISHED"}


class BBT_OT_firmament_rain_preset(Operator):
    bl_idname = "bob_blender_tools.firmament_rain_preset"
    bl_label = "Rain Preset"
    bl_description = "Set the rain look from a named preset"
    bl_options = {"REGISTER", "UNDO"}

    preset: EnumProperty(
        name="Preset",
        items=[(k, v["label"], v["desc"]) for k, v in RAIN_PRESETS.items()])

    def execute(self, context):
        fm = context.scene.bbt_firmament
        obj = bpy.data.objects.get(fm.rain_object)
        if obj is None or _nodes_mod(obj) is None:
            _build_particulate(context, fm.rain_object, "streak")
            obj = bpy.data.objects.get(fm.rain_object)
        if obj is None:
            return {"CANCELLED"}
        for name, val in RAIN_PRESETS[self.preset]["knobs"].items():
            inp = _input(obj, name)
            if inp is not None:
                inp.value = val
        obj.update_tag()
        self.report({"INFO"}, f"Applied {RAIN_PRESETS[self.preset]['label']} preset")
        return {"FINISHED"}


class BBT_OT_firmament_mote_preset(Operator):
    bl_idname = "bob_blender_tools.firmament_mote_preset"
    bl_label = "Mote Preset"
    bl_description = "Set the mote look (dust, amber motes, or falling snow)"
    bl_options = {"REGISTER", "UNDO"}

    preset: EnumProperty(
        name="Preset",
        items=[(k, v["label"], v["desc"]) for k, v in MOTE_PRESETS.items()])

    def execute(self, context):
        fm = context.scene.bbt_firmament
        obj = bpy.data.objects.get(fm.mote_object)
        if obj is None or _nodes_mod(obj) is None:
            _build_particulate(context, fm.mote_object, "mote")
            obj = bpy.data.objects.get(fm.mote_object)
        if obj is None:
            return {"CANCELLED"}
        for name, val in MOTE_PRESETS[self.preset]["knobs"].items():
            inp = _input(obj, name)
            if inp is not None:
                inp.value = val
        obj.update_tag()
        self.report({"INFO"}, f"Applied {MOTE_PRESETS[self.preset]['label']} preset")
        return {"FINISHED"}


class BBT_OT_firmament_build_snow_cover(Operator):
    bl_idname = "bob_blender_tools.firmament_build_snow_cover"
    bl_label = "Add Snow Coverage"
    bl_description = ("Write the snow_cover attribute onto the surface (slope + altitude, "
                      "seeded from the Environment snow level). BobShaders reads it")

    def execute(self, context):
        fm = context.scene.bbt_firmament
        surface = fm.snow_surface or context.active_object
        if surface is None or surface.type != "MESH":
            self.report({"WARNING"}, "Pick a mesh surface for the snow coverage")
            return {"CANCELLED"}
        env = _env.get_env(context.scene)
        params = {}
        if env is not None:  # seed the coverage amount from the shared world state
            params["snow"] = env.snow
        server._ensure_path()
        from bbmcp.geonodes import build_geonodes_on_object

        build_geonodes_on_object(surface, "snow", "BOB_Snow", params)
        if _live_env_on(context.scene):
            _install_snow_driver(surface, context.scene)
        self.report({"INFO"}, f"Snow coverage written on {surface.name}")
        return {"FINISHED"}


class BBT_OT_firmament_snow_from_env(Operator):
    bl_idname = "bob_blender_tools.firmament_snow_from_env"
    bl_label = "Use Env Snow"
    bl_description = "Copy the Environment snow level onto the coverage pass"

    def execute(self, context):
        fm = context.scene.bbt_firmament
        env = _env.get_env(context.scene)
        surface = fm.snow_surface or context.active_object
        mod = _named_mod(surface, "BOB_Snow")
        snow = _input_of(mod, "Snow")
        if env is None or snow is None:
            return {"CANCELLED"}
        snow.value = env.snow
        surface.update_tag()
        self.report({"INFO"}, "Snow coverage synced from Environment")
        return {"FINISHED"}


class BBT_OT_firmament_apply_season(Operator):
    bl_idname = "bob_blender_tools.firmament_apply_season"
    bl_label = "Apply Season"
    bl_description = ("Apply the current season: set its continuous state (snow, "
                     "wetness, temperature, fed live to the readers) and, for winter, "
                     "build the falling snow and snow-coverage pass. Explicit, not a "
                     "property callback, so it does not hit the scatter re-entrancy")
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        env = _env.get_env(context.scene)
        fm = context.scene.bbt_firmament
        spec = SEASON_APPLY.get(env.season)
        if spec is None:
            return {"CANCELLED"}
        for key in ("snow", "wetness", "temperature"):
            if key in spec:
                setattr(env, key, spec[key])
        built = []
        if spec.get("build_snow"):
            # Falling snow (the mote preset builds the object if missing) and the
            # coverage pass on the surface, if one is available. Coverage's Snow input
            # is driven from env.snow on build, so it tracks the level set above.
            bpy.ops.bob_blender_tools.firmament_mote_preset(preset="snow")
            built.append("falling snow")
            surface = fm.snow_surface or context.active_object
            if surface is not None and surface.type == "MESH":
                bpy.ops.bob_blender_tools.firmament_build_snow_cover()
                built.append("snow coverage")
        note = f"Season: {env.season}"
        if built:
            note += " (built " + ", ".join(built) + ")"
        self.report({"INFO"}, note)
        return {"FINISHED"}


class BBT_OT_firmament_scene_preset(Operator):
    bl_idname = "bob_blender_tools.firmament_scene_preset"
    bl_label = "Scene Preset"
    bl_description = "Set the whole atmosphere in one pick (context plus each subsystem)"
    bl_options = {"REGISTER", "UNDO"}

    preset: EnumProperty(
        name="Preset",
        items=[(k, v["label"], v["desc"]) for k, v in SCENE_PRESETS.items()])

    def execute(self, context):
        p = SCENE_PRESETS[self.preset]
        env = _env.get_env(context.scene)
        fm = context.scene.bbt_firmament
        for key, val in p["env"].items():
            setattr(env, key, val)
        # Sky reads the world state we just set, so rebuild it to move the sun.
        bpy.ops.bob_blender_tools.firmament_build_sky()
        # Each subsystem preset op builds the object if missing, then seeds its knobs.
        if p.get("clouds"):
            bpy.ops.bob_blender_tools.firmament_cloud_preset(preset=p["clouds"])
        if p.get("fog"):
            mode, fkey = p["fog"]
            fm.fog_mode = mode
            bpy.ops.bob_blender_tools.firmament_fog_preset(preset=fkey)
        if p.get("rain"):
            bpy.ops.bob_blender_tools.firmament_rain_preset(preset=p["rain"])
        if p.get("motes"):
            bpy.ops.bob_blender_tools.firmament_mote_preset(preset=p["motes"])
        self.report({"INFO"}, f"Applied {p['label']} scene preset")
        return {"FINISHED"}


# Firmament is now "Atmosphere": the built sky/clouds/fog/weather subsystems. The world state
# (bbt_env), the Quality level, the Live Environment master, and Scene Presets moved to the
# World panel (docs/UX-REDESIGN.md 5.1/5.5). Class/operator names keep the firmament_* / BBT_*
# spelling per decision F.
class BBT_PT_firmament(Panel):
    bl_label = "Atmosphere"
    bl_idname = "BBT_PT_firmament"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "BobBlenderTools"
    bl_order = 4  # after the pipeline stages (docs/UX-REDESIGN.md section 4)
    bl_options = {"DEFAULT_CLOSED"}

    def draw(self, context):
        # Container only: Sky / Clouds / Fog / Weather are the sub-panels below. Build Sky
        # lives in the Sky sub-panel, so it is not repeated here.
        pass


class BBT_PT_firmament_sky(Panel):
    bl_label = "Sky"
    bl_idname = "BBT_PT_firmament_sky"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "BobBlenderTools"
    bl_parent_id = "BBT_PT_firmament"

    def draw(self, context):
        fm = context.scene.bbt_firmament
        layout = self.layout

        layout.prop(fm, "use_override")
        if fm.use_override:
            col = layout.column(align=True)
            col.prop(fm, "override_elevation")
            col.prop(fm, "override_azimuth")

        col = layout.column(align=True)
        col.prop(fm, "sun_strength")
        col.prop(fm, "sun_angle")
        col.prop(fm, "sun_disc")

        col = layout.column(align=True)
        col.prop(fm, "world_strength")
        col.prop(fm, "sky_altitude")
        col.prop(fm, "air")
        col.prop(fm, "ozone")
        col.prop(fm, "turbidity")
        col.prop(fm, "ground_albedo")

        layout.operator("bob_blender_tools.firmament_build_sky", icon="LIGHT_SUN")


class BBT_PT_firmament_clouds(Panel):
    bl_label = "Clouds"
    bl_idname = "BBT_PT_firmament_clouds"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "BobBlenderTools"
    bl_parent_id = "BBT_PT_firmament"
    bl_options = {"DEFAULT_CLOSED"}

    def draw(self, context):
        fm = context.scene.bbt_firmament
        layout = self.layout

        box = layout.box()
        box.prop(fm, "cloud_object")
        box.prop(fm, "cloud_shadows")
        row = box.row(align=True)
        row.operator("bob_blender_tools.firmament_build_clouds", icon="OUTLINER_OB_VOLUME")
        ui_helpers.preset_row(row, "bob_blender_tools.firmament_cloud_preset")

        # Live knobs from the modifier (present only after a Build), grouped.
        obj = bpy.data.objects.get(fm.cloud_object)
        if obj is None or _nodes_mod(obj) is None:
            layout.label(text="Build to edit cloud knobs", icon="INFO")
            return

        col = layout.column(align=True)
        col.label(text="Shape", icon="MOD_NOISE")
        _draw_knobs(col, obj, _CLOUD_SHAPE)
        seed = _input(obj, "Cloud Seed")
        if seed is not None:
            ui_helpers.seed_row(col, seed, "bob_blender_tools.firmament_randomize_seed",
                                text="Cloud Seed",
                                op_props={"object_name": fm.cloud_object,
                                          "seed_input": "Cloud Seed"})
        _draw_knobs(col, obj, _CLOUD_SHAPE2)

        col = layout.column(align=True)
        col.label(text="Layer", icon="MESH_GRID")
        _draw_knobs(col, obj, _CLOUD_LAYER)

        col = layout.column(align=True)
        col.label(text="Wind", icon="FORCE_WIND")
        wind = _input(obj, "Wind")
        if wind is not None:
            col.prop(wind, "value", text="Wind Drift")
            if wind.value:
                _draw_knobs(col, obj, _CLOUD_WIND)
                # Only meaningful when Live Environment is off; with it on a driver
                # owns these inputs and would immediately overwrite the copied value.
                if not _live_env_on(context.scene):
                    op = col.operator("bob_blender_tools.firmament_wind_from_env",
                                      icon="TRACKING_FORWARDS")
                    op.object_name = fm.cloud_object


class BBT_PT_firmament_fog(Panel):
    bl_label = "Fog"
    bl_idname = "BBT_PT_firmament_fog"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "BobBlenderTools"
    bl_parent_id = "BBT_PT_firmament"
    bl_options = {"DEFAULT_CLOSED"}

    def draw(self, context):
        fm = context.scene.bbt_firmament
        layout = self.layout

        box = layout.box()
        box.prop(fm, "fog_object")
        box.prop(fm, "fog_mode")
        if fm.fog_mode == "ground_fog":
            box.prop(fm, "fog_heightmap")
        row = box.row(align=True)
        row.operator("bob_blender_tools.firmament_build_fog", icon="OUTLINER_OB_VOLUME")
        ui_helpers.preset_row(row, "bob_blender_tools.firmament_fog_preset")

        # Live knobs from the modifier (present only after a Build), grouped.
        obj = bpy.data.objects.get(fm.fog_object)
        if obj is None or _nodes_mod(obj) is None:
            layout.label(text="Build to edit fog knobs", icon="INFO")
            return

        col = layout.column(align=True)
        col.label(text="Shape", icon="MOD_NOISE")
        _draw_knobs(col, obj, _FOG_SHAPE)
        seed = _input(obj, "Fog Seed")
        if seed is not None:
            ui_helpers.seed_row(col, seed, "bob_blender_tools.firmament_randomize_seed",
                                text="Fog Seed",
                                op_props={"object_name": fm.fog_object,
                                          "seed_input": "Fog Seed"})

        col = layout.column(align=True)
        col.label(text="Look", icon="COLOR")
        _draw_knobs(col, obj, _FOG_LOOK)

        col = layout.column(align=True)
        col.label(text="Layer", icon="MESH_GRID")
        _draw_knobs(col, obj, _FOG_LAYER)

        # Terrain mapping, only for ground fog (the sockets exist only then).
        if _input(obj, "Terrain Size") is not None:
            col = layout.column(align=True)
            col.label(text="Terrain", icon="RNDCURVE")
            _draw_knobs(col, obj, _FOG_GROUND)

        col = layout.column(align=True)
        col.label(text="Wind", icon="FORCE_WIND")
        wind = _input(obj, "Wind")
        if wind is not None:
            col.prop(wind, "value", text="Wind Drift")
            if wind.value:
                _draw_knobs(col, obj, _FOG_WIND)
                if not _live_env_on(context.scene):  # a live driver owns these inputs when on
                    op = col.operator("bob_blender_tools.firmament_wind_from_env",
                                      icon="TRACKING_FORWARDS")
                    op.object_name = fm.fog_object


class BBT_PT_firmament_weather(Panel):
    bl_label = "Weather"
    bl_idname = "BBT_PT_firmament_weather"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "BobBlenderTools"
    bl_parent_id = "BBT_PT_firmament"
    bl_options = {"DEFAULT_CLOSED"}

    def draw(self, context):
        fm = context.scene.bbt_firmament
        layout = self.layout

        col = layout.column(align=True)
        col.prop(fm, "weather_camera")
        col.prop(fm, "use_motion_blur")

        # Rain (streak mode).
        box = layout.box()
        box.label(text="Rain", icon="OUTLINER_OB_FORCE_FIELD")
        row = box.row(align=True)
        row.operator("bob_blender_tools.firmament_build_rain", icon="MOD_FLUIDSIM")
        ui_helpers.preset_row(row, "bob_blender_tools.firmament_rain_preset")
        rain = bpy.data.objects.get(fm.rain_object)
        if rain is not None and _nodes_mod(rain) is not None:
            box.prop(rain, "hide_viewport", text="Hide", invert_checkbox=True, icon="HIDE_OFF")
            _draw_knobs(box, rain, _RAIN_KNOBS)
            seed = _input(rain, "Seed")
            if seed is not None:
                ui_helpers.seed_row(box, seed, "bob_blender_tools.firmament_randomize_seed",
                                    op_props={"object_name": fm.rain_object})
            _draw_knobs(box, rain, _RAIN_WIND)
            if not _live_env_on(context.scene):  # a live driver owns Wind when on
                op = box.operator("bob_blender_tools.firmament_wind_from_env",
                                  icon="TRACKING_FORWARDS")
                op.object_name = fm.rain_object
            _draw_knobs(box, rain, ["Color"])
            _draw_knobs(box, rain, _DOMAIN_KNOBS)

        # Motes (dust / amber / falling snow, all mote mode).
        box = layout.box()
        box.label(text="Motes (Dust / Amber / Snow)", icon="PARTICLES")
        row = box.row(align=True)
        row.operator("bob_blender_tools.firmament_build_motes", icon="OUTLINER_OB_POINTCLOUD")
        ui_helpers.preset_row(row, "bob_blender_tools.firmament_mote_preset")
        motes = bpy.data.objects.get(fm.mote_object)
        if motes is not None and _nodes_mod(motes) is not None:
            box.prop(motes, "hide_viewport", text="Hide", invert_checkbox=True, icon="HIDE_OFF")
            _draw_knobs(box, motes, _MOTE_KNOBS)
            seed = _input(motes, "Seed")
            if seed is not None:
                ui_helpers.seed_row(box, seed, "bob_blender_tools.firmament_randomize_seed",
                                    op_props={"object_name": fm.mote_object})
            _draw_knobs(box, motes, _MOTE_LOOK)
            _draw_knobs(box, motes, _MOTE_WIND)
            if not _live_env_on(context.scene):  # a live driver owns Wind when on
                op = box.operator("bob_blender_tools.firmament_wind_from_env",
                                  icon="TRACKING_FORWARDS")
                op.object_name = fm.mote_object
            _draw_knobs(box, motes, _DOMAIN_KNOBS)

        # Snow coverage (the GN pass on the terrain surface, the single coverage source).
        box = layout.box()
        box.label(text="Snow Coverage", icon="OUTLINER_DATA_SURFACE")
        box.prop(fm, "snow_surface")
        box.operator("bob_blender_tools.firmament_build_snow_cover", icon="FREEZE")
        surface = fm.snow_surface or context.active_object
        snow_mod = _named_mod(surface, "BOB_Snow")
        if snow_mod is not None:
            _draw_knobs_mod(box, snow_mod, _SNOW_KNOBS)
            if not _live_env_on(context.scene):  # a live driver owns Snow when on
                box.operator("bob_blender_tools.firmament_snow_from_env", icon="TRACKING_FORWARDS")
        else:
            box.label(text="Writes snow_cover for BobShaders to read", icon="INFO")


CLASSES = (
    BBT_FirmamentProps,
    BBT_OT_firmament_build_sky,
    BBT_OT_firmament_build_clouds,
    BBT_OT_firmament_cloud_preset,
    BBT_OT_firmament_build_fog,
    BBT_OT_firmament_fog_preset,
    BBT_OT_firmament_build_rain,
    BBT_OT_firmament_build_motes,
    BBT_OT_firmament_randomize_seed,
    BBT_OT_firmament_wind_from_env,
    BBT_OT_firmament_rain_preset,
    BBT_OT_firmament_mote_preset,
    BBT_OT_firmament_build_snow_cover,
    BBT_OT_firmament_snow_from_env,
    BBT_OT_firmament_apply_season,
    BBT_OT_firmament_scene_preset,
    BBT_PT_firmament,
    BBT_PT_firmament_sky,
    BBT_PT_firmament_clouds,
    BBT_PT_firmament_fog,
    BBT_PT_firmament_weather,
)


def register():
    global _env
    server._ensure_path()
    from bbmcp import env
    _env = env
    env.register()  # BobFirmament owns and registers the shared world state
    for cls in CLASSES:
        bpy.utils.register_class(cls)
    bpy.types.Scene.bbt_firmament = bpy.props.PointerProperty(type=BBT_FirmamentProps)
    # Subscribe the atmosphere applier so the World master toggle / quality drive it (P6 scaling).
    world_panel.register_applier(_apply_world)


def unregister():
    world_panel.unregister_applier(_apply_world)
    del bpy.types.Scene.bbt_firmament
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
    if _env is not None:
        _env.unregister()
