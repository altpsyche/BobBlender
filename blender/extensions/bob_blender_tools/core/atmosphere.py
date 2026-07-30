"""BobFirmament orchestration, shared by the Atmosphere panel and the MCP ops.

The geometry-node recipes themselves (volumetrics, particulates, snow) are built in
core/geonodes/; this module holds the layer above them: the named presets, the small live
modifier-input helpers, the live-env driver feed (wind + snow), the snow-line math, and the
params->result builder functions each panel Operator and each dispatch handler calls. One
builder serves the button and the op, so the socket writes that make a cloud, fog, or weather
effect read right live in exactly one place.

bpy-only, and it never imports ui/: the panel operators import THIS module (presets + helpers
+ builders) and keep only their context resolution (active object, scene props, self.report,
puff/streak counts). The MCP handlers (build_clouds / build_fog / build_rain / build_motes /
build_snow_cover / apply_season / scene_preset) resolve objects by name from the op and call
the same builders. The world state is read through bpy attribute access (getattr(scene,
'bbt_firmament', ...) / core.env), never by importing the ui modules, so core stays the
acyclic root.

The two orchestrators (apply_season, scene_preset) call the core builders directly rather than
chaining bpy.ops, so they run headless over MCP with no operator context. scene_preset builds
the sky through core.world.build_sky (build_sky is not duplicated here); the panel operator
re-applies the world afterwards so the live sun drivers reinstall.
"""

import bpy

from . import env as _env, geonodes, util, world as _world

# Cloud-type presets: each sets the live modifier knobs by socket name for a named sky look.
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

# Mote presets (mote mode): dust, amber motes, and falling snow are the same mode, a preset
# picks the look. Falling snow is the plan's snow mote preset.
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

# Per-season application: the continuous env values a season implies (fed live to the readers)
# plus, for winter, the structural subsystems to build (falling snow + the snow-coverage pass).
# Applied by an explicit operator/op, never a property callback, so it does not hit the scatter
# re-entrancy. Season owns only the seasonal state and its own subsystems; it leaves time, place,
# and wind (the shot setup) alone. snow_line: where the snow line goes, NORMALIZED 0..1 over the
# terrain relief. Winter drops it to the valley (0 = whole map snows); the other seasons push it
# above the peaks (>1 = snow clears). Apply also stamps the terrain's Z bounds into the env.
SEASON_APPLY = {
    "spring": {"wetness": 0.20, "temperature": 12.0, "snow_line": 1.15},
    "summer": {"wetness": 0.0, "temperature": 24.0, "snow_line": 1.15},
    "autumn": {"wetness": 0.15, "temperature": 10.0, "snow_line": 1.15},
    "winter": {"wetness": 0.05, "temperature": -4.0,
               "snow_line": 0.0, "build_snow": True},
}

# Representative mid-season month per season, NORTHERN hemisphere. When "Season sets the date" is
# on, Apply Season writes env.month to this, and the live solar model does the physically-correct
# thing: a low winter sun with long shadows, a high summer sun. Sky mood (time of day, cloud,
# weather) stays Sky Look's job, so the two never fight.
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


# Sky Looks: one pick sets the SKY/atmosphere mood only (time of day, weather, cloud cover, wind)
# and seeds each named subsystem, building any that are missing (at the current quality, so a
# Preview pick stays cheap). A subsystem set to None is left alone, not deleted. `fog` is
# (mode, preset); the others are a preset key.
#
# A Sky Look deliberately does NOT touch season/snow/wetness/temperature: those are the seasonal
# state owned solely by Season + Apply Season (SEASON_APPLY above). Ground wetting for a
# rainy/stormy look comes from the `weather` enum, which drives every BobShader's wetness live, so
# a Look sets the mood and Season owns the ground.
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


# -- Live modifier-input helpers (shared by panel + builders) --------------------------------
def _live_env_on(scene):
    """The one master Live Environment toggle lives on the World panel (bbt_world); default on
    when World is absent (standalone / headless build)."""
    return getattr(getattr(scene, "bbt_world", None), "live_env", True)


def _input(obj, socket_name):
    """The live modifier input struct for a socket name, or None."""
    mod = util.nodes_mod(obj)
    if mod is None or mod.node_group is None:
        return None
    ident = next((it.identifier for it in mod.node_group.interface.items_tree
                  if getattr(it, "item_type", None) == "SOCKET"
                  and it.in_out == "INPUT" and it.name == socket_name), None)
    return getattr(mod.properties.inputs, ident, None) if ident else None


def _input_of(mod, socket_name):
    if mod is None or mod.node_group is None:
        return None
    ident = next((it.identifier for it in mod.node_group.interface.items_tree
                  if getattr(it, "item_type", None) == "SOCKET"
                  and it.in_out == "INPUT" and it.name == socket_name), None)
    return getattr(mod.properties.inputs, ident, None) if ident else None


# -- Quality (Preview/Final) -----------------------------------------------------------------
# Quality levels: the Cycles volume settings (cost-spike defaults) plus the particulate count
# scale. Volume bounces (multiple scattering) are kept low: 0 for preview keeps self-shadowing
# single-scatter and cheap, a couple for final let light re-scatter so shadowed cloud reads bright
# instead of muddy. The particulate scale thins the field for the viewport and restores it for a
# final render.
_QUALITY = {
    "preview": {"step_rate": 2.0, "max_steps": 256, "bounces": 0, "particulate": 0.35},
    "final": {"step_rate": 1.0, "max_steps": 512, "bounces": 2, "particulate": 1.0},
}


def _apply_quality(scene):
    """Apply the Preview/Final level to every Firmament build: scale the particulate counts live
    (a Quality Scale modifier input, no rebuild) and set the Cycles volume step rate, max steps,
    and bounces. Called by every build and by the World quality control (via the world applier),
    so switching quality re-applies without a rebuild. The level lives on the World panel
    (bbt_world) since it is scene-wide; default Preview when World is absent (standalone)."""
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


# -- Live-env wind driver feed ---------------------------------------------------------------
def _drive_input(obj, inp, scene, env_path):
    """Install a driver on a modifier input's value that reads a Scene bbt_env field live, so
    moving the Environment slider moves the built effect with no rebuild and no per-object press.
    The input struct owns its animation through the object, so inp.driver_add('value') routes to
    obj.animation_data with the correct RNA path (verified in 5.2). Any prior driver on the same
    input is cleared first so a rebuild (which regenerates socket identifiers) never leaves a
    stale, dangling driver."""
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


# Per-object extra live-env inputs beyond wind: (socket name, bbt_env path). The cloud layer's
# Coverage is driven from env.cloud_cover so the Environment cloud-cover slider controls the
# clouds, the same live model as wind.
_CLOUD_EXTRA = (("Coverage", "bbt_env.cloud_cover"),)


def _install_wind_drivers(obj, scene, extra=()):
    """Feed Wind Direction / Speed (and any extra (socket, env_path) pairs) from bbt_env live, and
    enable the Wind toggle on a volume so its drift is active. Reinstalled by every build (socket
    identifiers are regenerated on the non-destructive rebuild, so a driver keyed by identifier
    must be re-added; the build ops are the only path that rebuilds these objects)."""
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


# -- Snow-line math (matches the shader) -----------------------------------------------------
def _snow_input(surface):
    """The Snow input struct of the BOB_Snow pass on a surface, or None."""
    return _input_of(util.nodes_mod(surface, "BOB_Snow"), "Snow")


def _snow_amount(env):
    """The temperature-driven snow amount (0 above freezing, 1 by SNOW_TEMP_FULL), matching the
    shader. Snow has no amount slider -- temperature is the amount."""
    from .materials import SNOW_TEMP_FULL
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
    hub produces. Used by both the build and the live sync so they agree."""
    lo, band = _world_snow_line(env)
    tz = surface.matrix_world.translation.z
    sz = surface.matrix_world.to_scale().z or 1.0
    return (lo - tz) / sz, band / sz


def _sync_snow_pass(surface, env):
    """Set the GN pass (the shell) from the env: Snow amount from temperature, and the snow
    line/band in the surface's local frame (matching the shader's world-Z line). Snow is
    temperature-driven with no plain env field to drive live, so the shell is refreshed on build /
    Apply Season / Use Env Snow, not per-edit."""
    mod = util.nodes_mod(surface, "BOB_Snow")
    if mod is None or env is None:
        return
    lo, band = _local_snow_line(surface, env)
    for name, value in (("Snow", _snow_amount(env)), ("Altitude", lo), ("Altitude Falloff", band)):
        inp = _input_of(mod, name)
        if inp is not None:
            inp.value = value
    surface.update_tag()


def _show_domain_gizmo(obj):
    """Draw a volume domain as a wireframe box in the viewport, not a solid box.

    WIRE, not BOUNDS: the box is generated by the GN modifier, so the object's bounding box (what
    BOUNDS draws) comes from the empty base mesh and is not true to the volume size. WIRE draws the
    evaluated geometry, so the wireframe matches the actual domain. display_type is viewport-only,
    so the render is untouched; the material has no surface output, so the box was never visible in
    a render anyway."""
    if obj is not None:
        obj.display_type = "WIRE"


def _apply_knobs(obj, knobs):
    """Set a built object's live modifier inputs by socket name (no rebuild)."""
    for name, val in knobs.items():
        inp = _input(obj, name)
        if inp is not None:
            inp.value = val
    if obj is not None:
        obj.update_tag()


def _camera_name(scene, name=None):
    """A camera name to follow: the given name, else the scene camera, else None. Weather domains
    re-tile around it so preset/season-built weather is around the shot, not stuck at the origin."""
    if name:
        return name
    cam = getattr(scene, "camera", None)
    return cam.name if cam is not None else None


# -- Builder functions (params in, object/result out) ----------------------------------------
def build_clouds_object(scene, *, name="BOB_Clouds", cloud_shadows=True):
    """Build the procedural volumetric cloud layer: the volumetrics recipe in clouds mode, seeded
    from the world state, at the current quality, with the wind/coverage drivers and the shadow
    toggle. Returns the cloud object (or None)."""
    env = _env.get_env(scene)
    params = {"mode": "clouds"}
    if env is not None:  # seed the wind + coverage knobs from the shared world state
        params["wind_direction"] = env.wind_direction
        params["wind_speed"] = env.wind_strength
        params["coverage"] = env.cloud_cover
    geonodes.build_geonodes({"op": "build_geonodes", "recipe": "volumetrics",
                             "name": name, "params": params})
    _apply_quality(scene)
    obj = bpy.data.objects.get(name)
    _show_domain_gizmo(obj)
    if _live_env_on(scene):
        _install_wind_drivers(obj, scene, _CLOUD_EXTRA)
    if obj is not None:
        # Shadow fork (cost): a cloud volume that casts shadows makes every lit point march a
        # shadow ray through it, the expensive path. Default ON for dimensional form (cheap at a
        # normal sun height); turn off for the low-sun Final case where it gets expensive.
        obj.visible_shadow = cloud_shadows
    return obj


def _terrain_drape(heightmap):
    """{terrain_size, terrain_height, sea_level} read off the terrain built from this heightmap.

    Ground fog samples the heightmap to decide where the ground is, so it needs the same four
    numbers the terrain was displaced with; its own defaults are 60 m / 14 m / 0.3, which describe a
    different terrain. Read off the object rather than passed in, exactly as `drape_curve` reads
    them (docs/MCP.md, "What a terrain was built from"): `build_geonodes` stamps `bbt_heightmap` and
    `bbt_terrain_size` / `_height` / `_sea` on every heightmap_terrain build, so the agreement is
    automatic and there is nothing for a caller to restate and get wrong.

    Measured, on a 90 m by 9 m terrain with the fog left on its own defaults: the mist's idea of the
    ground sat metres off the real surface, so the profile that should hug it filled the air instead
    and the lower half of the frame washed out solid. Density did not fix it -- 0.02 and 0.8 render
    identically once the domain is saturated, which is what made it look like a broken volume rather
    than a mismatched one.
    """
    for obj in bpy.data.objects:
        if obj.type != "MESH" or obj.get("bbt_heightmap") != heightmap:
            continue
        return {"terrain_size": float(obj.get("bbt_terrain_size", 90.0)),
                "terrain_height": float(obj.get("bbt_terrain_height", 22.0)),
                "sea_level": float(obj.get("bbt_terrain_sea", 0.22))}
    return {}


def build_fog_object(scene, *, name="BOB_Fog", mode="height_fog", heightmap=""):
    """Build a procedural volumetric fog domain in the given mode (height_fog / noise_fog /
    ground_fog), seeded from the world wind, at the current quality, with the wind drivers.
    A ground_fog heightmap (an absolute path) drapes the mist over the terrain, whose scale is
    read off the terrain object (see `_terrain_drape`). Returns the fog object (or None)."""
    env = _env.get_env(scene)
    params = {"mode": mode}
    if env is not None:  # seed the wind knobs from the shared world state
        params["wind_direction"] = env.wind_direction
        params["wind_speed"] = env.wind_strength
    if mode == "ground_fog" and heightmap:
        params["heightmap"] = heightmap
        params.update(_terrain_drape(heightmap))
    geonodes.build_geonodes({"op": "build_geonodes", "recipe": "volumetrics",
                             "name": name, "params": params})
    _apply_quality(scene)
    obj = bpy.data.objects.get(name)
    _show_domain_gizmo(obj)
    if _live_env_on(scene):
        _install_wind_drivers(obj, scene)
    return obj


def build_particulate(scene, obj_name, mode, *, camera_name=None, use_motion_blur=True,
                      extra=None):
    """Build a particulates object (streak or mote), seeding wind and the follow camera from the
    world state, at the current quality, and turning on motion blur. camera_name None falls back
    to the scene camera. Returns the built object (or None)."""
    env = _env.get_env(scene)
    params = {"mode": mode}
    camera_name = _camera_name(scene, camera_name)
    if camera_name:
        params["camera"] = camera_name
    if env is not None:  # seed the wind knobs from the shared world state
        params["wind_direction"] = env.wind_direction
        params["wind_speed"] = env.wind_strength
    if extra:
        params.update(extra)
    geonodes.build_geonodes({"op": "build_geonodes", "recipe": "particulates",
                             "name": obj_name, "params": params})
    obj = bpy.data.objects.get(obj_name)
    _apply_quality(scene)  # set Quality Scale from the level
    if _live_env_on(scene):
        _install_wind_drivers(obj, scene)
    if use_motion_blur:
        scene.render.use_motion_blur = True
        # Also enable it on the object so fast particles are guaranteed included, not just the
        # scene-level switch.
        if obj is not None and hasattr(obj, "cycles"):
            obj.cycles.use_motion_blur = True
    return obj


def build_snow_cover_on(scene, surface):
    """Write the snow_cover + snow_occlusion attributes onto a mesh surface via the snow recipe
    (the BOB_Snow modifier), seeded from the Environment snow level and snow line so the shell's
    coverage tracks what the material shades. Raises ValueError if surface is not a mesh."""
    if surface is None or getattr(surface, "type", None) != "MESH":
        raise ValueError("pick a mesh surface for the snow coverage")
    env = _env.get_env(scene)
    params = {}
    if env is not None:
        # Stamp the terrain's Z bounds so the normalized snow line maps to world Z, then seed the
        # pass: amount from temperature and the world-Z line/band matching the shader.
        _env.stamp_snow_bounds(scene, surface)
        lo, band = _local_snow_line(surface, env)  # local frame; the pass compares against local Z
        params["snow"] = _snow_amount(env)
        params["altitude"] = lo
        params["altitude_falloff"] = band
    geonodes.build_geonodes_on_object(surface, "snow", "BOB_Snow", params)
    return surface


def build_sky_from_env(scene):
    """Build the physical sky + matched Sun via core.world.build_sky, assembling the params from
    the shared world state and the firmament sun props (the same dict the Build Sky operator
    assembles). Returns the build_sky result dict. This does NOT reinstall the live sun drivers;
    the panel operator re-applies the world afterwards for that (the applier registry is a ui
    concern), and the MCP path gets a static-but-correct sun."""
    env = _env.get_env(scene)
    fm = getattr(scene, "bbt_firmament", None)
    params = dict(_env.sun_params(env)) if env is not None else {}
    if fm is not None:
        params.update({
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
        })
    return _world.build_sky({"op": "build_sky", "params": params})


# -- Preset application (shared by the operators and the orchestrators) -----------------------
def apply_cloud_preset(scene, preset, *, name=None, cloud_shadows=None):
    """Set the cloud look from a CLOUD_PRESETS entry, building the cloud layer first if it is
    missing. Keeps env.cloud_cover in step with the preset's Coverage so it reads right whether
    Coverage is live-driven or set directly. Returns the cloud object (or None)."""
    if preset not in CLOUD_PRESETS:
        raise ValueError(f"unknown cloud preset {preset!r} (have: {sorted(CLOUD_PRESETS)})")
    fm = getattr(scene, "bbt_firmament", None)
    name = name or getattr(fm, "cloud_object", "BOB_Clouds")
    if cloud_shadows is None:
        cloud_shadows = getattr(fm, "cloud_shadows", True)
    obj = bpy.data.objects.get(name)
    if obj is None or util.nodes_mod(obj) is None:
        obj = build_clouds_object(scene, name=name, cloud_shadows=cloud_shadows)
    if obj is None:
        return None
    knobs = CLOUD_PRESETS[preset]["knobs"]
    _apply_knobs(obj, knobs)
    env = _env.get_env(scene)
    if env is not None and "Coverage" in knobs:
        env.cloud_cover = knobs["Coverage"]
    return obj


def apply_fog_preset(scene, preset, *, name=None, mode=None, heightmap=None):
    """Set the fog look from a FOG_PRESETS entry, building the fog domain first if it is missing
    (in `mode`, defaulting to the panel's fog_mode). Returns the fog object (or None)."""
    if preset not in FOG_PRESETS:
        raise ValueError(f"unknown fog preset {preset!r} (have: {sorted(FOG_PRESETS)})")
    fm = getattr(scene, "bbt_firmament", None)
    name = name or getattr(fm, "fog_object", "BOB_Fog")
    if mode is None:
        mode = getattr(fm, "fog_mode", "height_fog")
    if heightmap is None:
        hm = getattr(fm, "fog_heightmap", "")
        heightmap = bpy.path.abspath(hm) if hm else ""
    obj = bpy.data.objects.get(name)
    if obj is None or util.nodes_mod(obj) is None:
        obj = build_fog_object(scene, name=name, mode=mode, heightmap=heightmap)
    if obj is None:
        return None
    _apply_knobs(obj, FOG_PRESETS[preset]["knobs"])
    return obj


def apply_rain_preset(scene, preset, *, name=None, camera_name=None, use_motion_blur=None):
    """Set the rain look from a RAIN_PRESETS entry, building the rain streaks first if missing.
    Returns the rain object (or None)."""
    if preset not in RAIN_PRESETS:
        raise ValueError(f"unknown rain preset {preset!r} (have: {sorted(RAIN_PRESETS)})")
    fm = getattr(scene, "bbt_firmament", None)
    name = name or getattr(fm, "rain_object", "BOB_Rain")
    obj = bpy.data.objects.get(name)
    if obj is None or util.nodes_mod(obj) is None:
        obj = _particulate_from_panel(scene, name, "streak", fm,
                                      camera_name=camera_name, use_motion_blur=use_motion_blur)
    if obj is None:
        return None
    _apply_knobs(obj, RAIN_PRESETS[preset]["knobs"])
    return obj


def apply_mote_preset(scene, preset, *, name=None, camera_name=None, use_motion_blur=None):
    """Set the mote look (dust / amber / falling snow) from a MOTE_PRESETS entry, building the
    motes first if missing. Returns the mote object (or None)."""
    if preset not in MOTE_PRESETS:
        raise ValueError(f"unknown mote preset {preset!r} (have: {sorted(MOTE_PRESETS)})")
    fm = getattr(scene, "bbt_firmament", None)
    name = name or getattr(fm, "mote_object", "BOB_Motes")
    obj = bpy.data.objects.get(name)
    if obj is None or util.nodes_mod(obj) is None:
        obj = _particulate_from_panel(scene, name, "mote", fm,
                                      camera_name=camera_name, use_motion_blur=use_motion_blur)
    if obj is None:
        return None
    _apply_knobs(obj, MOTE_PRESETS[preset]["knobs"])
    return obj


def _particulate_from_panel(scene, name, mode, fm, *, camera_name=None, use_motion_blur=None):
    """Build a particulate, resolving the follow camera and motion-blur default from the firmament
    panel state when not given explicitly (so a preset that builds one matches the panel)."""
    if camera_name is None:
        cam = getattr(fm, "weather_camera", None) if fm is not None else None
        camera_name = cam.name if cam is not None else None
    if use_motion_blur is None:
        use_motion_blur = getattr(fm, "use_motion_blur", True)
    return build_particulate(scene, name, mode, camera_name=camera_name,
                             use_motion_blur=use_motion_blur)


def _snow_surface(scene):
    """The surface the season/snow builds write onto: the designated snow surface, else the active
    object. Read by duck-typing so core never imports the ui firmament state."""
    fm = getattr(scene, "bbt_firmament", None)
    surf = getattr(fm, "snow_surface", None) if fm is not None else None
    if surf is not None:
        return surf
    return getattr(bpy.context, "active_object", None)


# -- Orchestrators (pure core: no bpy.ops) ---------------------------------------------------
def apply_season_state(scene, *, season=None, build_snow=None, season_sets_date=None):
    """Apply a season: write its continuous env state (wetness, temperature, snow line) and, for
    winter (or build_snow=True), build the falling snow and the snow-coverage pass. season None
    uses the current env.season; season_sets_date None reads the panel's toggle. Returns
    (season, notes, created): notes are human labels for what was built, created are the object
    names. Raises ValueError on an unknown season or a missing environment."""
    env = _env.get_env(scene)
    if env is None:
        raise ValueError("no environment (bbt_env) in the scene")
    fm = getattr(scene, "bbt_firmament", None)
    season = season or env.season
    spec = SEASON_APPLY.get(season)
    if spec is None:
        raise ValueError(f"unknown season {season!r} (have: {sorted(SEASON_APPLY)})")
    env.season = season
    for key in ("wetness", "temperature"):
        if key in spec:
            setattr(env, key, spec[key])
    # Snow line: stamp the terrain's Z bounds so the normalized line maps to world Z, then set the
    # season's normalized value. Winter -> 0 (valley, whole map); others -> above the peaks.
    if "snow_line" in spec:
        surface = _snow_surface(scene)
        _env.stamp_snow_bounds(scene, surface)
        env.snow_line = spec["snow_line"]
    # Season -> date (gated): set the month so the live solar model lowers the winter sun. Writing
    # env.month fires env's geo-hook, which repositions the sun when Live Environment is on.
    sets_date = getattr(fm, "season_sets_date", True) if season_sets_date is None \
        else bool(season_sets_date)
    if sets_date:
        month = _season_month(season, env.latitude)
        if month is not None:
            env.month = month
    do_snow = spec.get("build_snow", False) if build_snow is None else bool(build_snow)
    notes, created = [], []
    if do_snow:
        # Falling snow (the mote preset builds the object if missing) and the coverage pass on the
        # surface, if one is available. The pass's Snow amount is seeded from the temperature on
        # build, so winter (-4C) fills it.
        motes = apply_mote_preset(scene, "snow")
        notes.append("falling snow")
        if motes is not None:
            created.append(motes.name)
        surface = _snow_surface(scene)
        if surface is not None and getattr(surface, "type", None) == "MESH":
            build_snow_cover_on(scene, surface)
            notes.append("snow coverage")
            created.append(f"{surface.name}:BOB_Snow")
    return season, notes, created


def apply_scene_preset(scene, look):
    """Apply a Sky Look (SCENE_PRESETS): write the sky mood env (time, weather, cloud cover, wind),
    build the sky, and seed each atmosphere subsystem (building any missing). Does not touch the
    season. Returns (preset, created): created is the object names built/seeded. Raises ValueError
    on an unknown look. The panel operator re-applies the world afterwards so the live sun drivers
    reinstall (a ui concern)."""
    p = SCENE_PRESETS.get(look)
    if p is None:
        raise ValueError(f"unknown sky look {look!r} (have: {sorted(SCENE_PRESETS)})")
    env = _env.get_env(scene)
    fm = getattr(scene, "bbt_firmament", None)
    if env is not None:
        for key, val in p["env"].items():
            setattr(env, key, val)
    created = []
    # Sky reads the world state we just set, so build it to move the sun.
    res = build_sky_from_env(scene)
    created.extend(res.get("created", []))
    if p.get("clouds"):
        obj = apply_cloud_preset(scene, p["clouds"])
        if obj is not None:
            created.append(obj.name)
    if p.get("fog"):
        mode, fkey = p["fog"]
        if fm is not None:
            fm.fog_mode = mode
        obj = apply_fog_preset(scene, fkey, mode=mode)
        if obj is not None:
            created.append(obj.name)
    if p.get("rain"):
        obj = apply_rain_preset(scene, p["rain"])
        if obj is not None:
            created.append(obj.name)
    if p.get("motes"):
        obj = apply_mote_preset(scene, p["motes"])
        if obj is not None:
            created.append(obj.name)
    return p, created


# -- Object resolution + MCP handlers --------------------------------------------------------
def build_clouds(op: dict) -> dict:
    """MCP op: build the volumetric cloud layer."""
    scene = bpy.context.scene
    name = op.get("object") or "BOB_Clouds"
    cloud_shadows = bool(op.get("cloud_shadows", True))
    obj = build_clouds_object(scene, name=name, cloud_shadows=cloud_shadows)
    created = [obj.name] if obj is not None else []
    info = f"clouds: {name}" + ("" if cloud_shadows else " (no shadows)")
    return {"op": "build_clouds", "created": created, "info": info}


def build_fog(op: dict) -> dict:
    """MCP op: build a volumetric fog domain (height_fog / noise_fog / ground_fog).

    The default fog is deliberately dense (a thick foggy-morning look). For a thin, beam-friendly
    haze pass a `preset` (ground_mist / valley / banks / thick) and/or an explicit `density`
    override -- without one, a full-height domain washes the whole frame grey and no light shafts
    read. density overlays the preset (or the recipe default) so `preset` + `density` together set
    a look and then dial its thickness."""
    scene = bpy.context.scene
    name = op.get("object") or "BOB_Fog"
    mode = op.get("mode", "height_fog")
    if mode not in ("height_fog", "noise_fog", "ground_fog"):
        raise ValueError(f"fog mode must be height_fog/noise_fog/ground_fog, got {mode!r}")
    heightmap = op.get("heightmap") or ""
    obj = build_fog_object(scene, name=name, mode=mode, heightmap=heightmap)
    preset = op.get("preset")
    if preset:
        if preset not in FOG_PRESETS:
            raise ValueError(f"unknown fog preset {preset!r} (have: {sorted(FOG_PRESETS)})")
        _apply_knobs(obj, FOG_PRESETS[preset]["knobs"])
    density = op.get("density")
    if density is not None and obj is not None:
        _apply_knobs(obj, {"Density": float(density)})
    created = [obj.name] if obj is not None else []
    info = f"fog: {mode} ({name})"
    if preset:
        info += f" [{preset}]"
    if density is not None:
        info += f" density={density}"
    return {"op": "build_fog", "created": created, "info": info}


def build_rain(op: dict) -> dict:
    """MCP op: build the rain streak particle system, optionally seeded from a rain preset."""
    scene = bpy.context.scene
    name = op.get("object") or "BOB_Rain"
    obj = build_particulate(scene, name, "streak", camera_name=op.get("camera"),
                            use_motion_blur=bool(op.get("motion_blur", True)))
    preset = op.get("preset")
    if preset:
        if preset not in RAIN_PRESETS:
            raise ValueError(f"unknown rain preset {preset!r} (have: {sorted(RAIN_PRESETS)})")
        _apply_knobs(obj, RAIN_PRESETS[preset]["knobs"])
    created = [obj.name] if obj is not None else []
    info = f"rain: {name}" + (f" ({preset})" if preset else "")
    return {"op": "build_rain", "created": created, "info": info}


def build_motes(op: dict) -> dict:
    """MCP op: build the dust / amber / snow mote particle system, optionally seeded from a mote
    preset."""
    scene = bpy.context.scene
    name = op.get("object") or "BOB_Motes"
    obj = build_particulate(scene, name, "mote", camera_name=op.get("camera"),
                            use_motion_blur=bool(op.get("motion_blur", True)))
    preset = op.get("preset")
    if preset:
        if preset not in MOTE_PRESETS:
            raise ValueError(f"unknown mote preset {preset!r} (have: {sorted(MOTE_PRESETS)})")
        _apply_knobs(obj, MOTE_PRESETS[preset]["knobs"])
    created = [obj.name] if obj is not None else []
    info = f"motes: {name}" + (f" ({preset})" if preset else "")
    return {"op": "build_motes", "created": created, "info": info}


def build_snow_cover(op: dict) -> dict:
    """MCP op: write the snow-coverage pass (snow_cover + snow_occlusion) onto a terrain surface."""
    scene = bpy.context.scene
    surface = util.object_of(op.get("object"), "MESH", label="object")
    build_snow_cover_on(scene, surface)
    return {"op": "build_snow_cover", "created": [f"{surface.name}:BOB_Snow"],
            "info": f"snow coverage written on {surface.name}"}


def apply_season(op: dict) -> dict:
    """MCP op: apply a season's env state and (for winter / build_snow) its snow subsystems."""
    scene = bpy.context.scene
    season, notes, created = apply_season_state(
        scene, season=op.get("season"), build_snow=op.get("build_snow"),
        season_sets_date=op.get("season_sets_date"))
    info = f"season: {season}"
    if notes:
        info += " (built " + ", ".join(notes) + ")"
    return {"op": "apply_season", "created": created, "info": info}


def scene_preset(op: dict) -> dict:
    """MCP op: apply a Sky Look (env mood + sky + seeded subsystems)."""
    scene = bpy.context.scene
    p, created = apply_scene_preset(scene, op.get("look"))
    return {"op": "scene_preset", "created": created, "info": f"{p['label']} sky look"}
