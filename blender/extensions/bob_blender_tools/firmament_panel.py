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


def _env_owned_note(layout):
    """A muted one-liner under a greyed knob group: the Live Environment driver owns these,
    so an edit here would be overwritten. Shown only when Live Environment is on."""
    cap = layout.row()
    cap.enabled = False
    cap.label(text="Live Environment drives this; turn it off on World to edit")


def _from_env_row(layout, live, op_idname, object_name=None):
    """The copy-from-world branch shared by every subsystem's wind (and the snow) button (S6):
    when Live Environment is on a driver owns the input, so show the owned note and no button;
    else offer the one-shot copy-from-env button. Kept here (not ui_helpers) because it is a
    Firmament-only idiom that reaches _env_owned_note."""
    if live:
        _env_owned_note(layout)
        return
    op = layout.operator(op_idname, icon="TRACKING_FORWARDS")
    if object_name is not None:
        op.object_name = object_name

# The bbmcp.env module, imported and registered at addon register time and held so
# unregister uses the same object even after Reload Builders purges bbmcp.
_env = None

# Live cloud knobs, grouped for the panel (drawn from the modifier after a Build).
_CLOUD_SHAPE = ["Coverage", "Cloud Scale", "Warp"]
_CLOUD_SHAPE2 = ["Detail", "Softness", "Density"]
_CLOUD_LAYER = ["Layer Size", "Thickness", "Height"]
_CLOUD_WIND = ["Wind Direction", "Wind Speed"]

# Live fog knobs, grouped for the panel.
_FOG_SHAPE = ["Density", "Fog Noise", "Fog Scale", "Fog Detail", "Softness", "Warp"]
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
# snow_line: where Apply Season puts the snow line, NORMALIZED 0..1 over the terrain relief. Winter
# drops it to the valley (0 = whole map snows); the other seasons push it above the peaks (>1 =
# snow clears). Apply also stamps the terrain's Z bounds into the env so the value maps to world Z.
SEASON_APPLY = {
    "spring": {"wetness": 0.20, "temperature": 12.0, "snow_line": 1.15},
    "summer": {"wetness": 0.0, "temperature": 24.0, "snow_line": 1.15},
    "autumn": {"wetness": 0.15, "temperature": 10.0, "snow_line": 1.15},
    "winter": {"wetness": 0.05, "temperature": -4.0,
               "snow_line": 0.0, "build_snow": True},
}

# Representative mid-season month per season, NORTHERN hemisphere. When "Season sets the date"
# is on, Apply Season writes env.month to this, and the live solar model (env geo-hook ->
# _reposition_sun) does the physically-correct thing: a low winter sun with long shadows, a high
# summer sun. Sky mood (time of day, cloud, weather) stays Sky Look's job, so the two never fight.
_SEASON_MONTH_NORTH = {"spring": 4, "summer": 7, "autumn": 10, "winter": 1}


def _season_month(season, latitude):
    """The month a season implies, hemisphere-aware: the southern hemisphere is offset half a
    year, so its winter (June/July) sits opposite the northern winter (December/January)."""
    m = _SEASON_MONTH_NORTH.get(season)
    if m is None:
        return None
    if latitude < 0.0:
        m = (m + 5) % 12 + 1  # +6 months, wrapped into 1..12
    return m

# Sky Looks: one pick sets the SKY/atmosphere mood only (time of day, weather, cloud cover,
# wind) and seeds each named subsystem, building any that are missing (at the current quality,
# so a Preview pick stays cheap). A subsystem set to None is left alone, not deleted. `fog` is
# (mode, preset); the others are a preset key.
#
# A Sky Look deliberately does NOT touch season/snow/wetness/temperature: those are the
# seasonal state owned solely by Season + Apply Season (SEASON_APPLY above). Ground wetting for
# a rainy/stormy look comes from the `weather` enum, which drives every BobShader's wetness
# live (materials.env_state_group), so a Look sets the mood and Season owns the ground.
SCENE_PRESETS = {
    "clear_day": {
        "label": "Clear Day", "desc": "High midday sun, a few thin clouds",
        "env": {"time_of_day": 13.0, "weather": "clear",
                "cloud_cover": 0.15, "wind_direction": 90.0, "wind_strength": 1.5},
        "clouds": "clear", "fog": None, "rain": None, "motes": None},
    "golden_hour": {
        "label": "Golden Hour", "desc": "Low warm sun, scattered cloud, amber motes",
        "env": {"time_of_day": 18.5, "weather": "clear",
                "cloud_cover": 0.30, "wind_direction": 120.0, "wind_strength": 1.0},
        "clouds": "scattered", "fog": None, "rain": None, "motes": "amber"},
    "overcast": {
        "label": "Overcast", "desc": "Flat grey blanket, still air",
        "env": {"time_of_day": 12.0, "weather": "overcast",
                "cloud_cover": 0.80, "wind_direction": 200.0, "wind_strength": 2.0},
        "clouds": "overcast", "fog": None, "rain": None, "motes": None},
    "storm": {
        "label": "Storm", "desc": "Dark deep cloud, heavy rain, strong wind",
        "env": {"time_of_day": 16.0, "weather": "storm",
                "cloud_cover": 0.90, "wind_direction": 210.0, "wind_strength": 7.0},
        "clouds": "storm", "fog": ("height_fog", "valley"),
        "rain": "downpour", "motes": None},
    "foggy_dawn": {
        "label": "Foggy Dawn", "desc": "Low sun through a valley of fog",
        "env": {"time_of_day": 6.5, "weather": "fog",
                "cloud_cover": 0.40, "wind_direction": 160.0, "wind_strength": 0.5},
        "clouds": "scattered", "fog": ("height_fog", "valley"),
        "rain": None, "motes": None},
    "dust_storm": {
        "label": "Dust Storm", "desc": "Hazy sky, dust driven on a hot wind",
        "env": {"time_of_day": 15.0, "weather": "cloudy",
                "cloud_cover": 0.25, "wind_direction": 250.0, "wind_strength": 6.0},
        "clouds": "scattered", "fog": ("noise_fog", "banks"),
        "rain": None, "motes": "dust"},
    "winter": {
        "label": "Winter", "desc": "Cold overcast sky, falling snow motes",
        "env": {"time_of_day": 11.0, "weather": "snow",
                "cloud_cover": 0.80, "wind_direction": 300.0, "wind_strength": 2.5},
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


def _draw_knobs(layout, obj, names, enabled=True):
    """Draw each present modifier input by socket name (live, no rebuild). enabled=False greys
    the row (a live driver owns the input, so an edit would be silently overwritten)."""
    mod = _nodes_mod(obj)
    if mod is None or mod.node_group is None:
        return
    ids = {it.name: it.identifier for it in mod.node_group.interface.items_tree
           if getattr(it, "item_type", None) == "SOCKET" and it.in_out == "INPUT"}
    col = layout.column(align=True)
    col.enabled = enabled
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


def _draw_knobs_mod(layout, mod, names, enabled=True):
    """Draw each present input of a specific modifier by socket name (live). enabled=False greys
    the row (a live driver owns the input)."""
    if mod is None or mod.node_group is None:
        return
    ids = {it.name: it.identifier for it in mod.node_group.interface.items_tree
           if getattr(it, "item_type", None) == "SOCKET" and it.in_out == "INPUT"}
    col = layout.column(align=True)
    col.enabled = enabled
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
    """The Snow input struct of the BOB_Snow pass on a surface, or None."""
    return _input_of(_named_mod(surface, "BOB_Snow"), "Snow")


def _snow_amount(env):
    """The temperature-driven snow amount (0 above freezing, 1 by SNOW_TEMP_FULL), matching the
    shader. Snow has no amount slider -- temperature is the amount."""
    from bbmcp.materials import SNOW_TEMP_FULL
    t = max(0.0, min(1.0, env.temperature / SNOW_TEMP_FULL))
    return t * t * (3.0 - 2.0 * t)  # smoothstep, matches the shader MapRange


def _world_snow_line(env):
    """The world-Z snow line (lo) and transition band, mirroring the shader: line 0 covers the
    whole terrain (transition below the valley), line 1 clears above the peaks. Used to seed the
    GN pass (the shell) so its coverage tracks the same line the material shades to."""
    band = 0.12 * env.snow_z_span
    hi = env.snow_z_base + env.snow_line * (env.snow_z_span + band)
    return hi - band, band


def _local_snow_line(surface, env):
    """The snow line lo/band in the SURFACE's local frame. The line is a world-Z line (the material
    shades to it via world position), but the GN pass compares it against the mesh's LOCAL position
    (a GN modifier's Position is object-local), so it must be converted or a Z-translated/scaled
    terrain piles shell snow where the material renders bare rock. Handles the realistic terrain
    transform (Z translation and uniform/Z scale); a rotated terrain is not a case the heightfield
    hub produces. Used by both the build operator and the live sync so they agree."""
    lo, band = _world_snow_line(env)
    tz = surface.matrix_world.translation.z
    sz = surface.matrix_world.to_scale().z or 1.0
    return (lo - tz) / sz, band / sz


def _sync_snow_pass(surface, env):
    """Set the GN pass (the shell) from the env: Snow amount from temperature, and the snow line/band
    in the surface's local frame (matching the shader's world-Z line). Snow is temperature-driven with
    no plain env field to drive live, so the shell is refreshed on build / Apply Season / Use Env Snow,
    not per-edit."""
    mod = _named_mod(surface, "BOB_Snow")
    if mod is None or env is None:
        return
    lo, band = _local_snow_line(surface, env)
    for name, value in (("Snow", _snow_amount(env)), ("Altitude", lo), ("Altitude Falloff", band)):
        inp = _input_of(mod, name)
        if inp is not None:
            inp.value = value
    surface.update_tag()


# --- Live sun: reposition the Sun lamp + sky node from the world state whenever a geographic field
# (time/date/place, via env's geo-hook) or a sun override (this panel's own props) is edited, so the
# sun moves with no Build Sky press. The sun position is a nonlinear solar calc, so it cannot be a
# driver (and a custom-function pydriver breaks on untrusted files); a lightweight reposition runs on
# each edit instead. No node rebuild: it just sets the sun rotation/energy + the sky node sun angle,
# the same values a Build Sky would compute. ---
_solar = None


def _reposition_sun(scene):
    """Aim the existing Sun lamp + set the sky node's sun angle from the current world state
    (geographic solar model, or the manual override). A no-op when no sun has been built.
    Cheap: no node-tree rebuild, so it is safe to call on every geographic edit."""
    import math
    global _solar
    server._ensure_path()
    from bbmcp import world as W
    if _solar is None:
        from bbmcp import solar as _s
        _solar = _s
    sun = bpy.data.objects.get(W.SUN_NAME)
    env = getattr(scene, "bbt_env", None)
    if sun is None or env is None:
        return
    fm = getattr(scene, "bbt_firmament", None)
    if fm is not None and fm.use_override:
        el, az = fm.override_elevation, fm.override_azimuth
    else:
        pos = _solar.sun_position(env.latitude, env.longitude, int(env.year), int(env.month),
                                  int(env.day), env.time_of_day, utc_offset=env.utc_offset)
        el, az = pos["elevation"], pos["azimuth"]
    W._place_sun(sun, el, az,
                 strength=(fm.sun_strength if fm is not None else 2.0),
                 angle_deg=(fm.sun_angle if fm is not None else 0.545))
    world = scene.world
    tree = world.node_tree if world is not None and world.use_nodes else None
    sky = tree.nodes.get(W.SKY_NODE) if tree is not None else None
    if sky is not None:
        sky.sun_elevation = math.radians(el)
        sky.sun_rotation = math.radians(az)
        tree.update_tag()
    sun.update_tag()


def _sun_live_update(scene):
    """Reposition the sun on a geographic or override edit, when Live Environment is on. The
    env geo-hook (bbt_env fields) and the override-prop callbacks (this panel) both route here."""
    if scene is None or not _live_env_on(scene):
        return
    _reposition_sun(scene)


def _on_sun_override_change(self, context):
    """Update callback on this panel's sun-override props: re-place the sun live."""
    _sun_live_update(getattr(context, "scene", None) or getattr(bpy.context, "scene", None))


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
    # Live sun: place it from the current world state now; msgbus keeps it live on later edits.
    if live:
        _reposition_sun(scene)
    clouds = bpy.data.objects.get(fm.cloud_object)
    for obj in _firmament_wind_objects(fm):
        extra = _CLOUD_EXTRA if obj is clouds else ()
        if live:
            _install_wind_drivers(obj, scene, extra)
        else:
            _remove_wind_drivers(obj, extra)
    surface = fm.snow_surface or getattr(bpy.context, "active_object", None)
    mod = _named_mod(surface, "BOB_Snow")
    if mod is not None:
        # Clear any legacy live driver (older builds drove the pass Snow from bbt_env.snow, now
        # removed) and refresh the shell from the env, since it is no longer driven live.
        _undrive_input(surface, _input_of(mod, "Snow"))
        _undrive_input(surface, _input_of(mod, "Altitude"))
        _sync_snow_pass(surface, _env.get_env(scene))
    _apply_quality(scene)


class BBT_FirmamentProps(PropertyGroup):
    """Firmament's own UI and subsystem state (not the shared world)."""

    # Sky Look: the staged pick for the whole-atmosphere preset (applied by Apply Sky Look, not
    # on the pick). Owns only sky mood (time/weather/cloud/wind + subsystems), never the season.
    sky_look: EnumProperty(
        name="Sky Look",
        items=[(k, v["label"], v["desc"]) for k, v in SCENE_PRESETS.items()],
        description="A whole-sky mood to stage; press Apply Sky Look to commit it")

    # Season -> date coupling (item 2). When on, Apply Season sets env.month to the season's
    # representative month (hemisphere-aware) so the live sun drops in winter for free. Gated so
    # a pinned shot date is never silently clobbered.
    season_sets_date: BoolProperty(
        name="Season sets the date", default=True,
        description="Apply Season also sets the calendar month, so the sun sits low in winter "
                    "and high in summer. Turn off to keep the date you set")

    # Sun override on top of the geographic position. update=_on_sun_override_change re-places
    # the sun live, matching the geographic fields' live behaviour.
    use_override: BoolProperty(
        name="Manual Sun", default=False,
        description="Set the sun angle by hand instead of from time and place",
        update=_on_sun_override_change)
    override_elevation: FloatProperty(
        name="Elevation", default=45.0, min=-90.0, max=90.0,
        description="Degrees above the horizon", update=_on_sun_override_change)
    override_azimuth: FloatProperty(
        name="Azimuth", default=180.0, min=0.0, max=360.0,
        description="Degrees clockwise from north", update=_on_sun_override_change)

    # Sun light.
    sun_strength: FloatProperty(name="Sun Strength", default=2.0, min=0.0,
                                update=_on_sun_override_change)
    sun_angle: FloatProperty(
        name="Sun Size", default=0.545, min=0.0, max=20.0,
        description="Angular diameter in degrees; larger softens shadows",
        update=_on_sun_override_change)
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
        # Picking the terrain also fits the snow line's Z bounds to it, so the normalized line
        # reads right immediately (0 = its valley, 1 = its peaks).
        # Wrapped so the callback returns None: stamp_snow_bounds returns a bool, and a property
        # update callback that returns non-None raises "the return value must be None".
        update=lambda self, ctx: (_env.stamp_snow_bounds(ctx.scene, self.snow_surface), None)[1],
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
        # A rebuild recreates the sky node (dropping its drivers) and resets the sun; re-apply the
        # world so the live sun drivers are reinstalled and time/place stay live from here.
        world_panel.apply_all(context.scene)
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
    bl_description = ("Write the snow_cover + snow_occlusion attributes onto the surface, "
                      "seeded from the Environment snow level and snow line. The accumulation "
                      "shell reads snow_cover for thickness; the material reads snow_occlusion")

    def execute(self, context):
        fm = context.scene.bbt_firmament
        surface = fm.snow_surface or context.active_object
        if surface is None or surface.type != "MESH":
            self.report({"WARNING"}, "Pick a mesh surface for the snow coverage")
            return {"CANCELLED"}
        env = _env.get_env(context.scene)
        params = {}
        if env is not None:
            # Stamp the terrain's Z bounds so the normalized snow line maps to world Z, then seed
            # the pass (the shell): amount from temperature and the world-Z line/band matching the
            # shader, so the shell's coverage tracks what the material shades.
            _env.stamp_snow_bounds(context.scene, surface)
            lo, band = _local_snow_line(surface, env)  # local frame; the pass compares against local Z
            params["snow"] = _snow_amount(env)
            params["altitude"] = lo
            params["altitude_falloff"] = band
        server._ensure_path()
        from bbmcp.geonodes import build_geonodes_on_object

        build_geonodes_on_object(surface, "snow", "BOB_Snow", params)
        self.report({"INFO"}, f"Snow coverage written on {surface.name}")
        return {"FINISHED"}


class BBT_OT_firmament_snow_from_env(Operator):
    bl_idname = "bob_blender_tools.firmament_snow_from_env"
    bl_label = "Use Env Snow"
    bl_description = "Refresh the snow pass (the shell) from the Environment temperature and snow line"

    def execute(self, context):
        fm = context.scene.bbt_firmament
        env = _env.get_env(context.scene)
        surface = fm.snow_surface or context.active_object
        if env is None or _named_mod(surface, "BOB_Snow") is None:
            return {"CANCELLED"}
        _sync_snow_pass(surface, env)
        self.report({"INFO"}, "Snow pass synced from Environment")
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
        for key in ("wetness", "temperature"):
            if key in spec:
                setattr(env, key, spec[key])
        # Snow line: stamp the terrain's Z bounds so the normalized line maps to world Z, then set
        # the season's normalized value. Winter -> 0 (valley, whole map); others -> above the peaks
        # (clears). Terrain and assets both read the same env line.
        if "snow_line" in spec:
            surface = fm.snow_surface or context.active_object
            _env.stamp_snow_bounds(context.scene, surface)
            env.snow_line = spec["snow_line"]
        # Season -> date (item 2, gated): set the month so the live solar model lowers the winter
        # sun. Writing env.month fires env's geo-hook, which repositions the sun when Live
        # Environment is on. Sky mood (time/cloud/weather) is left to Sky Look.
        if fm.season_sets_date:
            month = _season_month(env.season, env.latitude)
            if month is not None:
                env.month = month
        built = []
        if spec.get("build_snow"):
            # Falling snow (the mote preset builds the object if missing) and the
            # coverage pass on the surface, if one is available. The pass's Snow amount is
            # seeded from the temperature on build, so winter (-4C) fills it.
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
    bl_label = "Apply Sky Look"
    bl_description = ("Apply the staged Sky Look: set the sky mood (time, weather, cloud cover, "
                      "wind) and seed each atmosphere subsystem. Does not touch the season")
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        p = SCENE_PRESETS[context.scene.bbt_firmament.sky_look]
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
    bl_order = 6  # after the pipeline stages (docs/UX-REDESIGN.md section 4)
    bl_options = {"DEFAULT_CLOSED"}

    def draw(self, context):
        # A5: the root is no longer empty. It shows the sky state and carries the primary Build
        # Sky, so the panel's main action is the first thing you see instead of buried at the
        # bottom of the Sky sub-panel under its ten inputs. Sky / Clouds / Fog / Weather tune below.
        layout = self.layout
        built = bpy.data.objects.get("BOB_Sun") is not None
        layout.label(text="Sky built" if built else "No sky yet",
                     icon="LIGHT_SUN" if built else "INFO")
        ui_helpers.structural_action(
            layout, "bob_blender_tools.firmament_build_sky",
            text="Rebuild Sky" if built else "Build Sky",
            note="builds the sky + sun (time/place: World; sky inputs: Sky sub-panel)")


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

        # These are the INPUTS to Build Sky (not live post-build knobs), so they show always.
        # Build Sky itself lives on the Atmosphere header above (A5), not repeated here; edit an
        # input, then press Rebuild Sky up there.
        col = layout.column(align=True)
        col.label(text="Sun", icon="LIGHT_SUN")
        col.prop(fm, "use_override")
        if fm.use_override:
            col.prop(fm, "override_elevation")
            col.prop(fm, "override_azimuth")
        col.prop(fm, "sun_strength")
        col.prop(fm, "sun_angle")
        col.prop(fm, "sun_disc")

        col = layout.column(align=True)
        col.label(text="Sky", icon="WORLD")
        col.prop(fm, "world_strength")
        col.prop(fm, "sky_altitude")
        col.prop(fm, "air")
        col.prop(fm, "ozone")
        col.prop(fm, "turbidity")
        col.prop(fm, "ground_albedo")

        cap = layout.row()
        cap.enabled = False
        cap.label(text="edit, then Rebuild Sky on the Atmosphere header above", icon="INFO")


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
        ui_helpers.structural_action(box, "bob_blender_tools.firmament_build_clouds",
                                     note="builds: the cloud volume object")

        # Live knobs from the modifier (present only after a Build), grouped.
        obj = bpy.data.objects.get(fm.cloud_object)
        if obj is None or _nodes_mod(obj) is None:
            layout.label(text="Build to edit cloud knobs", icon="INFO")
            return

        # A6: the look preset is instant (light: sets knobs), so it is gated behind Build like the
        # other knobs. It no longer sits above the gate where picking it would silently build.
        ui_helpers.preset_row(layout, "bob_blender_tools.firmament_cloud_preset")

        live = _live_env_on(context.scene)
        col = layout.column(align=True)
        col.label(text="Shape", icon="MOD_NOISE")
        # Coverage is driven from bbt_env.cloud_cover when Live Environment is on; grey it so
        # the edit does not read as live (Cloud Scale / Warp are always author-owned).
        _draw_knobs(col, obj, ["Coverage"], enabled=not live)
        _draw_knobs(col, obj, _CLOUD_SHAPE[1:])
        seed = _input(obj, "Cloud Seed")
        if seed is not None:
            ui_helpers.seed_row(col, seed, "value", "bob_blender_tools.firmament_randomize_seed",
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
                _draw_knobs(col, obj, _CLOUD_WIND, enabled=not live)
                # A driver owns wind when Live Environment is on: grey the knobs and drop the
                # copy-from-env button (it would be overwritten), else offer the one-shot copy.
                _from_env_row(col, live, "bob_blender_tools.firmament_wind_from_env",
                              object_name=fm.cloud_object)


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
        ui_helpers.structural_action(box, "bob_blender_tools.firmament_build_fog",
                                     note="builds: the fog volume object")

        # Live knobs from the modifier (present only after a Build), grouped.
        obj = bpy.data.objects.get(fm.fog_object)
        if obj is None or _nodes_mod(obj) is None:
            layout.label(text="Build to edit fog knobs", icon="INFO")
            return

        # A6: instant look preset, gated behind Build so picking it never silently builds.
        ui_helpers.preset_row(layout, "bob_blender_tools.firmament_fog_preset")

        col = layout.column(align=True)
        col.label(text="Shape", icon="MOD_NOISE")
        _draw_knobs(col, obj, _FOG_SHAPE)
        seed = _input(obj, "Fog Seed")
        if seed is not None:
            ui_helpers.seed_row(col, seed, "value", "bob_blender_tools.firmament_randomize_seed",
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
                live = _live_env_on(context.scene)
                _draw_knobs(col, obj, _FOG_WIND, enabled=not live)
                _from_env_row(col, live, "bob_blender_tools.firmament_wind_from_env",
                              object_name=fm.fog_object)


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
        ui_helpers.structural_action(box, "bob_blender_tools.firmament_build_rain",
                                     note="builds: falling rain streaks")
        rain = bpy.data.objects.get(fm.rain_object)
        if rain is not None and _nodes_mod(rain) is not None:
            # A6: instant look preset, gated behind Build so it never silently builds.
            ui_helpers.preset_row(box, "bob_blender_tools.firmament_rain_preset")
            box.prop(rain, "hide_viewport", text="Hide", invert_checkbox=True, icon="HIDE_OFF")
            _draw_knobs(box, rain, _RAIN_KNOBS)
            seed = _input(rain, "Seed")
            if seed is not None:
                ui_helpers.seed_row(box, seed, "value", "bob_blender_tools.firmament_randomize_seed",
                                    op_props={"object_name": fm.rain_object})
            live = _live_env_on(context.scene)
            _draw_knobs(box, rain, _RAIN_WIND, enabled=not live)
            _from_env_row(box, live, "bob_blender_tools.firmament_wind_from_env",
                          object_name=fm.rain_object)
            _draw_knobs(box, rain, ["Color"])
            _draw_knobs(box, rain, _DOMAIN_KNOBS)

        # Motes (dust / amber / falling snow, all mote mode).
        box = layout.box()
        box.label(text="Motes (Dust / Amber / Snow)", icon="PARTICLES")
        ui_helpers.structural_action(box, "bob_blender_tools.firmament_build_motes",
                                     note="builds: floating motes (dust / amber / snow)")
        motes = bpy.data.objects.get(fm.mote_object)
        if motes is not None and _nodes_mod(motes) is not None:
            # A6: instant look preset, gated behind Build so it never silently builds.
            ui_helpers.preset_row(box, "bob_blender_tools.firmament_mote_preset")
            box.prop(motes, "hide_viewport", text="Hide", invert_checkbox=True, icon="HIDE_OFF")
            _draw_knobs(box, motes, _MOTE_KNOBS)
            seed = _input(motes, "Seed")
            if seed is not None:
                ui_helpers.seed_row(box, seed, "value", "bob_blender_tools.firmament_randomize_seed",
                                    op_props={"object_name": fm.mote_object})
            _draw_knobs(box, motes, _MOTE_LOOK)
            live = _live_env_on(context.scene)
            _draw_knobs(box, motes, _MOTE_WIND, enabled=not live)
            _from_env_row(box, live, "bob_blender_tools.firmament_wind_from_env",
                          object_name=fm.mote_object)
            _draw_knobs(box, motes, _DOMAIN_KNOBS)

        # Snow pass (GN, on the terrain): feeds the accumulation shell (snow_cover) and the
        # material's optional shelter term (snow_occlusion). The material whitens on its own.
        box = layout.box()
        box.label(text="Snow Coverage", icon="OUTLINER_DATA_SURFACE")
        box.prop(fm, "snow_surface")
        ui_helpers.structural_action(box, "bob_blender_tools.firmament_build_snow_cover",
                                     note="builds: the snow pass (shell coverage + occlusion)")
        surface = fm.snow_surface or context.active_object
        snow_mod = _named_mod(surface, "BOB_Snow")
        if snow_mod is not None:
            live = _live_env_on(context.scene)
            # Snow (amount) is driven from bbt_env when Live Environment is on, so grey it. The
            # world-Z Altitude/Falloff are the snow line, set from the env on build/sync (Use Env
            # Snow), so they show as author-owned; the slope band and occlusion are author-owned too.
            _draw_knobs_mod(box, snow_mod, ["Snow"], enabled=not live)
            _draw_knobs_mod(box, snow_mod, ["Slope Threshold", "Slope Falloff", "Altitude",
                                            "Altitude Falloff", "Occlusion", "Occlusion Distance"])
            _from_env_row(box, live, "bob_blender_tools.firmament_snow_from_env")
        else:
            box.label(text="Feeds the snow shell + occlusion; the material snows on its own",
                      icon="INFO")


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
    global _env, _solar
    server._ensure_path()
    from bbmcp import env
    _env = env
    _solar = None  # rebound lazily by the driver function (survives a Reload Builders)
    env.register()  # BobFirmament owns and registers the shared world state
    for cls in CLASSES:
        bpy.utils.register_class(cls)
    bpy.types.Scene.bbt_firmament = bpy.props.PointerProperty(type=BBT_FirmamentProps)
    # Live sun: subscribe the reposition to the shared env's geographic-change hook, so editing
    # time/date/place re-places the sun (the override props carry their own update callback).
    _env.register_geo_hook(_sun_live_update)
    # Subscribe the atmosphere applier so the World master toggle / quality drive it (P6 scaling).
    world_panel.register_applier(_apply_world)


def unregister():
    world_panel.unregister_applier(_apply_world)
    if _env is not None:
        _env.unregister_geo_hook(_sun_live_update)
    del bpy.types.Scene.bbt_firmament
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
    if _env is not None:
        _env.unregister()
