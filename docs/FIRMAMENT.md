# BobFirmament (Atmosphere)

Canonical reference for the atmosphere suite. Describes what the code does now, not
what is planned. Source of truth is the code; this doc follows it.

The suite authors the sky, the sun, volumetric clouds and fog, weather particles
(rain and motes), and snow, and it owns the shared world state every other
subsystem reads. It ships inside the `bob_blender_tools` extension plus supporting
`core` modules, all bpy-only, so a `BobBlenderFirmament` split stays mechanical.

## Two scene state blocks

- `Scene.bbt_env` (`core/env.py`, class `BBT_EnvProps`): the shared world state.
  Registered by `ui/firmament.register()` (which calls `env.register()`), so
  Firmament is the one registrar. Read by Terrain, Scatter, Shaders, and Firmament
  itself. Consumers guard for it being absent and fall back to their own defaults.
- `Scene.bbt_firmament` (`ui/firmament.py`, class `BBT_FirmamentProps`):
  Firmament's own UI and subsystem state (sun overrides, sky knobs, object names,
  weather camera, snow surface). Not read by other capabilities.
- `Scene.bbt_world` (`ui/world.py`, class `BBT_WorldProps`): the scene-wide
  masters that sit above the world data: `live_env`, `quality`, and the staged
  biome picks. This is not the world data; the world data is `bbt_env`.

## Panels

- `BBT_PT_world` (label "World", `bl_order` 0): the single driver for the world.
  Shows the pipeline overview, Quality (Preview/Final), the Live Environment
  master, the live Conditions (weather, temperature, wetness, snow, cloud cover,
  wind), Season + Apply Season, and the staged Sky Look. A first-build Build Sky
  affordance shows until a sky exists.
- `BBT_PT_world_time` (label "Time and place", child of World, closed by default):
  the set-once sun geographic inputs (time_of_day, year/month/day, utc_offset,
  latitude, longitude).
- `BBT_PT_biome` (label "Biome", `bl_order` 1): Build Biome and Biome World.
- `BBT_PT_firmament` (label "Atmosphere", `bl_order` 6): shows the sky state and
  carries the primary Build Sky / Rebuild Sky.
- `BBT_PT_firmament_sky` (label "Sky"): the inputs to Build Sky (sun override, sky
  knobs). Edit, then Rebuild Sky on the Atmosphere header.
- `BBT_PT_firmament_clouds` (label "Clouds"): Build Clouds plus live cloud knobs.
- `BBT_PT_firmament_fog` (label "Fog"): Build Fog plus live fog knobs.
- `BBT_PT_firmament_weather` (label "Weather"): Build Rain, Build Motes, Add Snow
  Coverage, plus their live knobs.

## Shared world state (bbt_env)

`BBT_EnvProps` fields, exactly as in `core/env.py`:

Time and place (carry `update=_on_geo_change`, so a consumer can re-place the sun
on edit):

- `time_of_day` float, 0..24, default 12.0 (local clock hours; 13.5 is 13:30)
- `year` int, 1..9999, default 2026
- `month` int, 1..12, default 6
- `day` int, 1..31, default 21
- `utc_offset` float, -14..14, default 0.0 (hours from UTC, east positive)
- `latitude` float, -90..90, default 45.0 (degrees north)
- `longitude` float, -180..180, default 0.0 (degrees east)

Season and weather (enums):

- `season` enum, default `summer`: `spring`, `summer`, `autumn`, `winter`
- `weather` enum, default `clear`: `clear`, `cloudy`, `overcast`, `rain`, `storm`,
  `snow`, `fog`

Continuous state (fed live into GN and material inputs by consumers):

- `temperature` float, -60..60, default 15.0 (degrees Celsius)
- `wetness` float, 0..1, default 0.0
- `snow` float, 0..1, default 0.0
- `cloud_cover` float, 0..1, default 0.2
- `wind_direction` float, 0..360, default 0.0 (degrees clockwise from north)
- `wind_strength` float, min 0, default 1.0

Accessors: `env.get_env(scene)` returns the state or None; `env.sun_params(env)`
pulls the geographic inputs (time_of_day, year, month, day, utc_offset, latitude,
longitude) as a plain dict for `build_sky`.

### Who writes bbt_env

- The World panel (`BBT_PT_world`, `BBT_PT_world_time`): direct user edits.
- Apply Season (`firmament_apply_season`): sets `snow`, `wetness`, `temperature`
  from `SEASON_APPLY[season]`.
- Apply Sky Look (`firmament_scene_preset`): sets `time_of_day`, `weather`,
  `cloud_cover`, `wind_direction`, `wind_strength` from `SCENE_PRESETS[sky_look]`.
- Biome World (`world_biome_world`) and Build Biome (`world_apply_biome`): set the
  bbt_env fields carried in a biome's world block.
- Cloud Preset (`firmament_cloud_preset`): keeps `cloud_cover` in step with the
  applied Coverage knob.

### Who reads bbt_env, and how

Two registries carry the reads so `env.py` never imports its consumers.

World applier registry (`ui/world.register_applier(fn)`; `apply_all(scene)`
runs every applier when a World control changes). Registered appliers:

- `ui/firmament._apply_world`: repositions the sun; installs or removes the wind
  drivers on clouds/fog/rain/motes (Wind Direction <- `bbt_env.wind_direction`,
  Wind Speed <- `bbt_env.wind_strength`; clouds also Coverage <-
  `bbt_env.cloud_cover`); installs or removes the snow-coverage driver (Snow <-
  `bbt_env.snow`); and re-applies Quality. Governed by `bbt_world.live_env`.
- `ui/shaders._apply_world`: installs or removes the shared `S_EnvState` drivers
  that feed every BobShader (`snow`, `wetness`, `temperature`, `weather`), so raising
  world snow whitens every surface with no rebuild. Governed by `bbt_world.live_env`.

Geographic-change hook registry (`env.register_geo_hook(fn)`, run by
`_on_geo_change` on the time/place field callbacks):

- `ui/firmament._sun_live_update`: repositions the sun when a geographic field
  changes and Live Environment is on. The sun is a nonlinear solar calc, so it
  cannot be a driver; a lightweight reposition runs on each edit instead.

BobShaders reads `snow`, `wetness`, `temperature`, and `weather` (enum index,
mapped to effective wetness; rain and storm wet the ground) through the shared
`S_EnvState` node group (`materials.ENV_STATE_DRIVERS`).

## The World panel: the single driver

`BBT_PT_world` is the one place to drive the world. Its masters:

- `live_env` (BoolProperty, default True): the one Live Environment master. On, it
  drives every consumer live via drivers (surface weather, atmosphere wind, cloud
  coverage, snow coverage). Off, each object is hand-tuned. Its `update` callback
  runs `apply_all`, so every applier reinstalls or removes its drivers.
- `quality` (EnumProperty, default `preview`): scene-wide Preview/Final. Its
  `update` callback runs `apply_all`, so `_apply_quality` re-applies without a
  rebuild.

Both were moved here from the old per-panel toggles; they are scene-wide, not
atmosphere-specific.

## Sky and sun

Op `build_sky` (`core/world.py`), driven by `firmament_build_sky` (Build Sky).
Creates or rebuilds:

- `BOB_World` world datablock with a physical sky node `BOB_Sky` (a
  `ShaderNodeTexSky` of type `MULTIPLE_SCATTERING`, the Blender 5.2 successor to
  Nishita) feeding a Background node into the World output. No world volume: an
  unbounded world Principled Volume has infinite optical depth and blacks the frame,
  so aerial perspective for the sky comes from the sky model and scene haze is a
  bounded fog domain.
- `BOB_Sun` SUN light, aimed from the solar elevation and azimuth. Below the
  horizon the lamp energy is set to 0.

Sun position comes from the geographic solar model, or a manual override when
`use_override` is set. The elevation and azimuth are written to both the sky node
(`sun_elevation`, `sun_rotation`) and the Sun light rotation so the disc and the
shadows agree. By default `sun_disc` is off (the lamp lights, not the disc) so the
sun is not counted twice; the disc still tracks the lamp when turned on.

`build_sky` params: `use_override`, `sun_elevation`, `sun_azimuth`, `sun_strength`,
`sun_angle`, `sun_disc`, `sun_intensity`, `world_strength`, `altitude`, `air`,
`ozone`, `turbidity`, `ground_albedo`, plus the geographic inputs from
`sun_params` (time_of_day, year, month, day, utc_offset, latitude, longitude).

`bbt_firmament` sky props (the Build Sky inputs, drawn on the Sky sub-panel):
`use_override`, `override_elevation`, `override_azimuth`, `sun_strength`,
`sun_angle` (angular diameter, degrees), `sun_disc`, `world_strength` (Sky
Strength), `sky_altitude` (mapped to the `altitude` param), `air`, `ozone`,
`turbidity` (default 2.2), `ground_albedo`. The sun override props carry an update
callback (`_on_sun_override_change`) that repositions the sun live.

**`build_sky` RECORDS the sun it decided on `bbt_firmament`, which is what makes it
survive a world re-apply.** `_record_sun` writes `use_override`, both override
angles, `sun_strength` and `sun_angle`, and a build with no override CLEARS the flag
so a stale one can never outrank the solar model a later caller asked for. The angles
go on before the flag (their own update callbacks would otherwise flap the sun
through a True flag against the previous build's angles), and the op places the sun
after recording, so the explicit placement is always the last write. Elevation is
clamped to +/-90, azimuth wrapped into [0, 360) and `sun_angle` clamped to 20, so the
sky node, the lamp and the recorded prop are one number rather than three that
disagree at the edges. `tools/scripts/headless_sun.py` measures the lamp across a
re-apply and is the guard.

Why it has to live there: every world re-apply re-places the sun from
`bbt_firmament` plus `bbt_env` through `_reposition_sun`. That is `apply_world`,
`set_env` with its default `apply: true`, `apply_biome` (default `world: true`),
`world_biome`, `apply_season` (it writes `month`), `scene_preset`, and an artist
moving a World slider. It is ALSO any `set_env` that writes a geographic field even
with `apply: false`, because `time_of_day`, `year`, `month`, `day`, `utc_offset`,
`latitude` and `longitude` each carry `_on_geo_change`, so the write itself fires the
reposition. `build_fog` is not one of them: it re-applies quality and the wind
drivers and leaves the sun alone. Before the recording, an override lived only in the
op's params, so at a night `time_of_day` the first re-apply recomputed a sun below
the horizon, `_place_sun` zeroed the lamp and the physical sky rendered black -- a
frame with nothing in it but the emissive geometry, which reads as a fog or exposure
fault and is neither.

**The one case that still cannot be made durable, and is reported instead of
hidden:** a geographic key passed to `build_sky` but never written to `bbt_env`. The
op computes the sun the caller asked for, but the next re-apply recomputes it from
`bbt_env`, which still holds the old clock. `data.durable` is then False and
`data.undurable` names the keys, and the fix is to send the clock through `set_env`.
The result's `data` also carries `source`, `elevation`, `azimuth`, `sun_strength`,
`sun_angle` and `recorded` (False headless, where the props are not registered).

Live sun: `_reposition_sun` aims the existing `BOB_Sun` and sets the `BOB_Sky` sun
angle from the current world state (or the override) with no node rebuild, so
editing time/place or a sun override moves the sun without a Build Sky press.
Build Sky itself re-runs `ui/world.apply_all` after building so the live drivers
are reinstalled.

### Solar model

`core/solar.py`, pure standard library, no bpy. `sun_position(latitude,
longitude, year, month, day, hour, utc_offset=0.0, refraction=True)` implements the
NOAA solar-position equations and returns a dict with `elevation`, `azimuth`,
`declination`, and `equation_of_time` (minutes). Conventions: latitude north
positive, longitude east positive, utc_offset east positive; azimuth clockwise
from true north (0 north, 90 east, 180 south, 270 west). Bennett's refraction
lifts the apparent sun near the horizon.

## Clouds and fog (volumetrics)

Recipe `volumetrics` (`core/geonodes/recipes/volumetrics.py`), reached through
`build_geonodes`. Every mode builds ONE bounded domain box for the whole layer and
lets a thin volume material carve the volume out of it (no seams, no per-cube
clipping, density faded to zero at the box faces). The box is instanced once so the
live knobs travel into the volume shader as INSTANCER attributes. Wind drift
advances a per-instance offset by Wind Speed * scene time along Wind Direction,
gated by the Wind toggle.

Modes (build-time param `mode`):

- `clouds`: world-space fractal noise thresholded by Coverage. Object `BOB_Clouds`.
  Built by `firmament_build_clouds` (Build Clouds).
- `height_fog`: a bounded slab, densest low, fading with height. Object `BOB_Fog`.
- `noise_fog`: the same slab broken into patchy drifting banks (higher default Fog
  Noise).
- `ground_fog`: a terrain-draped mist that samples a heightmap PNG so density hugs
  the ground; build-time param `heightmap` (the terrain image path).

Fog is built by `firmament_build_fog` (Build Fog); `fm.fog_mode` selects the mode
and `fm.fog_heightmap` supplies the ground_fog image.

**ground_fog reads the terrain's scale off the terrain.** The mode samples the
heightmap to decide where the ground is, so it needs the same size / height /
sea-level the terrain was displaced with; its own defaults describe a 60 m by 14 m
terrain. `atmosphere._terrain_drape` finds the object whose `bbt_heightmap` matches
and reads `bbt_terrain_size` / `_height` / `_sea` off it, the same way
`drape_curve` does, so there is nothing for a caller to restate. Measured before
that: on a 90 m by 9 m terrain the mist's idea of the ground sat metres off the
surface, the profile that should hug it filled the air, and the lower half of the
frame washed out solid at EVERY density from 0.004 to 1.0 — identical frames, which
is what says a domain is saturated rather than a knob broken.

**`build_fog` does not forward the domain.** It passes mode, heightmap and wind to
the recipe and nothing else, so `Layer Size` stays at its 400 m default and a camera
at ground level sits inside a quarter-kilometre of mist. For a mist that reads as
lying in a hollow, drive `volumetrics` directly (`build_geonodes`) and set the
domain: `size` about the tile width, `thickness` under 10 m, `height` just above the
valley floor.

Materials (`core/materials/`, shaders, cached by name): `cloud_volume_material`
(`BOB_CloudVolume`), `fog_volume_material` (`BOB_FogVolume`, shared by height_fog
and noise_fog), and `ground_fog_volume_material` (`BOB_GroundFog_<image>`, per
heightmap; falls back to the box fog material when no image is given). Cloud and fog
use separate materials: clouds carve with a Coverage threshold and a symmetric
all-face envelope; fog is a continuous medium with a vertical density gradient.

Live cloud knobs (socket names): `Coverage`, `Cloud Scale`, `Warp`, `Detail`,
`Softness`, `Density`, `Cloud Seed`, `Layer Size`, `Thickness`, `Height`, `Wind`,
`Wind Direction`, `Wind Speed`.

Live fog knobs: `Density`, `Fog Noise`, `Fog Scale`, `Fog Detail`, `Softness`,
`Warp`, `Fog Color`, `Anisotropy`, `Fog Seed`, `Layer Size`, `Thickness`,
`Height`, `Fog Top`, `Falloff`, `Wind`, `Wind Direction`, `Wind Speed`. Ground fog
adds `Terrain Size`, `Terrain Height`, `Sea Level`, `Ground Thickness`.

The cloud object's `visible_shadow` is set from `fm.cloud_shadows` (default on, for
dimensional form; off for the expensive low-sun Final case). The domain is drawn as
a wireframe (`display_type = "WIRE"`) so the gizmo matches the evaluated box.

## Weather particulates (rain and motes)

Recipe `particulates` (`core/geonodes/recipes/particulates.py`), reached through
`build_geonodes`. One recipe, two shape modes (build-time param `mode`):

- `streak` (rain): fast downward fall; a thin tapered cone stretched along its
  local Z and aligned to the velocity vector so wind leans the streak. Real
  geometry, so the look does not depend on motion blur. Object `BOB_Rain`, built by
  `firmament_build_rain` (Build Rain). Material `rain_material`.
- `mote` (dust, amber motes, falling snow): slow drift with turbulence; small ico
  spheres lit by the scene with an optional Emission. Object `BOB_Motes`, built by
  `firmament_build_motes` (Build Motes). Material `mote_material`.

Motion is deterministic and camera-following without a domain jump. Each particle
has a continuous world position `moved = base + velocity * scene_time` (plus
turbulence for motes), then is re-tiled to the copy nearest the camera:
`rep = moved - box*round((moved - cam)/box)`. Because `rep` is anchored to the
particle's own world position, motion blur reads the true world velocity, not the
camera's. The domain follows `fm.weather_camera` (else the scene camera, else the
origin). Build-time param `camera` is the object name.

Live common knobs: `Count`, `Domain Size`, `Domain Height`, `Fall Speed`, `Drift`,
`Size`, `Size Variation`, `Wind Direction`, `Wind Speed`, `Seed`, `Quality Scale`.
Motes add `Turbulence`. Streaks add `Streak Length` and `Color`. Motes add `Color`
and `Emission` (default 0).

`fm.use_motion_blur` (default on) sets `scene.render.use_motion_blur` and the
object's `cycles.use_motion_blur` on build.

## Snow

Two GN passes on the terrain surface, both reading and writing one attribute so the
shell thickness and the material whiteness never disagree.

- Recipe `snow` (`core/geonodes/recipes/snow.py`), modifier `BOB_Snow`. Runs after
  the terrain modifier (so it sees the displaced surface), passes geometry through,
  and writes a 0..1 `snow_cover` float attribute on the points:
  `snow_cover = Snow * slope_mask(normal Z) * altitude_mask(world Z) * (1 - occlusion)`.
  Slope and altitude are smoothsteps; occlusion is a crude short upward Raycast
  against the same mesh, gated by the Occlusion knob. Live knobs: `Snow`,
  `Slope Threshold`, `Slope Falloff`, `Altitude`, `Altitude Falloff`, `Occlusion`,
  `Occlusion Distance`. Built by `firmament_build_snow_cover` (Add Snow Coverage) on
  `fm.snow_surface` (else the active mesh). BobShaders reads `snow_cover`.
- Recipe `snow_shell` (`core/geonodes/recipes/snow_shell.py`), modifier
  `BOB_SnowShell`. Attaches after `BOB_Snow`, reads the same `snow_cover` (blurred
  by `Smooth`), and displaces the surface along its normal by
  `snow_cover * Thickness` for real thickness. Live knobs: `Thickness`, `Smooth`.
  Attached via `build_geonodes_on_object`; there is no dedicated Firmament operator
  for it (it lands with the BobShaders surface snow work).

Falling snow is the `mote` preset `snow`, not a separate object.

The snow passes attach via `build_geonodes_on_object(obj, recipe, mod_name,
params)`, a non-destructive path that records the old modifier index and moves the
rebuilt modifier back, so a later pass keeps running after the rebuilt one.

## Quality (Preview/Final)

`bbt_world.quality`. `_apply_quality(scene)` applies the level to every Firmament
build with no rebuild, called by every build op and by the World quality control
(via the world applier). The `_QUALITY` table:

- `preview`: `volume_step_rate` 2.0, `volume_max_steps` 256, `volume_bounces` 0,
  particulate Quality Scale 0.35.
- `final`: `volume_step_rate` 1.0, `volume_max_steps` 512, `volume_bounces` 2,
  particulate Quality Scale 1.0.

It sets the Cycles volume settings on `scene.cycles` and the `Quality Scale` input
on the rain and mote objects (which multiplies the live Count).

## Presets and looks

Blender-side dicts in `ui/firmament.py`; nothing else reads them.

- `SCENE_PRESETS` (Sky Look), staged on `bbt_firmament.sky_look`, applied by
  `firmament_scene_preset` (Apply Sky Look): `clear_day`, `golden_hour`, `overcast`,
  `storm`, `foggy_dawn`, `dust_storm`, `winter`. Each sets `bbt_env` time/weather/
  cloud_cover/wind, rebuilds the sky (so the sun moves), and seeds each named
  subsystem (`clouds`, `fog` as (mode, preset), `rain`, `motes`), building any that
  is missing. A subsystem set to None is left alone. A Sky Look never touches
  season/snow/wetness/temperature.
- `SEASON_APPLY` (Apply Season), applied by `firmament_apply_season`: `spring`,
  `summer`, `autumn`, `winter`. Sets `snow`, `wetness`, `temperature`; winter also
  builds the falling-snow mote preset and the snow-coverage pass. Season owns only
  the seasonal state and its own subsystems; it leaves time, place, and wind alone.
- `CLOUD_PRESETS` (`firmament_cloud_preset`): `clear`, `scattered`, `cumulus`,
  `overcast`, `storm`.
- `FOG_PRESETS` (`firmament_fog_preset`): `ground_mist`, `valley`, `banks`, `thick`.
- `RAIN_PRESETS` (`firmament_rain_preset`): `drizzle`, `rain`, `downpour`.
- `MOTE_PRESETS` (`firmament_mote_preset`): `dust`, `amber`, `snow`.

Each subsystem preset op builds its object if it is missing, then sets the live
knobs by socket name.

## Operators

Firmament (`ui/firmament.py`, all `bl_idname` prefixed `bob_blender_tools.`):

- `firmament_build_sky` (Build Sky)
- `firmament_build_clouds` (Build Clouds)
- `firmament_cloud_preset` (Cloud Preset)
- `firmament_build_fog` (Build Fog)
- `firmament_fog_preset` (Fog Preset)
- `firmament_build_rain` (Build Rain)
- `firmament_build_motes` (Build Motes)
- `firmament_randomize_seed` (Randomize Seed; props `object_name`, `seed_input`)
- `firmament_wind_from_env` (Use Env Wind; prop `object_name`)
- `firmament_rain_preset` (Rain Preset)
- `firmament_mote_preset` (Mote Preset)
- `firmament_build_snow_cover` (Add Snow Coverage)
- `firmament_snow_from_env` (Use Env Snow)
- `firmament_apply_season` (Apply Season)
- `firmament_scene_preset` (Apply Sky Look)

World (`ui/world.py`):

- `world_biome_world` (Biome World): sets bbt_env from a biome world block, then
  `apply_all`, and optionally rebuilds the sky.
- `world_apply_biome` (Build Biome): stands up terrain + scatter + world for a biome.

## Driving it

From the panel: the World panel edits `bbt_env` and the masters; the Atmosphere
panel and its sub-panels build and tune the subsystems. With Live Environment on,
moving a Conditions slider moves every built effect through drivers; the geographic
fields move the sun through the geo hook. Turn Live Environment off to hand-tune,
then use the Use Env Wind / Use Env Snow buttons for a one-shot copy.

The panel runs core ops in-process (`ui/firmament._apply` calls
`core.dispatch.apply_op`), the same path Scatter and Terrain use.

From MCP: send op dicts to the core dispatch. The registered handlers are
`build_sky` and `build_geonodes` (`core/dispatch.py`). Sky:

    {"op": "build_sky", "params": {"latitude": 45.0, "time_of_day": 13.0, ...}}

Clouds and fog:

    {"op": "build_geonodes", "recipe": "volumetrics", "name": "BOB_Clouds",
     "params": {"mode": "clouds", "coverage": 0.3, "wind_direction": 90.0}}

Rain and motes:

    {"op": "build_geonodes", "recipe": "particulates", "name": "BOB_Rain",
     "params": {"mode": "streak", "camera": "Camera", "wind_speed": 3.0}}

The snow coverage pass is attached with `build_geonodes_on_object` (a function, not
a dispatch op), targeting the terrain object with recipe `snow` and modifier name
`BOB_Snow`.

## Code layout

- `core/world.py`: `build_sky` (physical MULTIPLE_SCATTERING world + Sun light +
  solar placement; no world haze). Object names `BOB_World`, `BOB_Sun`, `BOB_Sky`.
- `core/env.py`: the shared world state `Scene.bbt_env`, the geo-hook registry,
  `get_env`, and `sun_params`. bpy-only.
- `core/solar.py`: the pure-Python NOAA solar-position math. No bpy.
- `core/geonodes/recipes/volumetrics.py`: the clouds/fog GN recipe.
- `core/geonodes/recipes/particulates.py`: the rain/mote GN recipe.
- `core/geonodes/recipes/snow.py`: the `snow_cover` coverage pass.
- `core/geonodes/recipes/snow_shell.py`: the accumulation shell.
- `core/materials/`: the volume and particulate materials (shaders), and the
  shared `S_EnvState` weather group BobShaders drives from `bbt_env`.
- `extensions/bob_blender_tools/ui/firmament.py`: the Atmosphere panel,
  operators, presets, live-driver install, and `bbt_firmament` state. Owns and
  registers `bbt_env`; subscribes `_apply_world` and `_sun_live_update`.
- `extensions/bob_blender_tools/ui/world.py`: the World and Biome panels, the
  world-applier registry, and `bbt_world`.
