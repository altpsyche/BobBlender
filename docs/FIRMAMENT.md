# BobFirmament plan

Forward-looking design for the atmosphere capability, not the current state.
`ARCHITECTURE.md` describes what is built today; this describes where sky, cloud,
fog, and weather generation are going and how they stay Cycles-correct and
separable for a later polyrepo split. Build against this; fold settled parts into
`ARCHITECTURE.md` and `SYSTEMS.md` as slices land.

The PANEL UX was redesigned 2026-07-20 (`UX-REDESIGN.md`): this panel is now labelled
**Atmosphere** and keeps the Sky/Clouds/Fog/Weather builders. The shared world (the Environment
sliders), the Preview/Final Quality level, the Live Environment master, and the Scene Presets /
Apply Season moved to the new **World** panel (`bbt_world` + `bbt_env`); Firmament still owns and
registers `bbt_env`. The atmosphere algorithms below are unchanged; only how they are driven moved.

## Decisions locked (2026-07-19)

- Clouds are procedural volumes (a noise-driven volume box), no VDB assets in v1.
- "Ambers" means amber dust motes: fine, sun-lit, non-emissive specks drifting on
  the wind, a golden-hour look. Not glowing embers. Emission stays an optional
  particulate parameter (default off) so fireflies or embers are a later preset,
  not a core feature.
- Cycles is the target and the verification gate. EEVEE keeps working where that is
  free, but parity is not gated.
- Time of Day drives the sun geographically (latitude, longitude, date, time via a
  pure-Python solar model) with a manual elevation/azimuth override on top.
- BobFirmament owns the world state and is the environment authority. Time, season,
  weather, wind, snow, and wetness live in `Scene.bbt_env`, registered by
  BobFirmament, and every other system (Terrain, Scatter, BobShaders) reads it to
  respond: rain outside means droplets and wet ground, winter means white surfaces
  and bare trees. This makes BobFirmament the base layer other capabilities depend on,
  a deliberate one-way dependency, not a neutral standalone. Accepted so the world has
  one source of truth.
- Geometry nodes where they improve scalability, quality, or expandability;
  shaders and Python where GN cannot help or would hurt (revision, 2026-07-19).
  Clouds and domain fog move to a shared GN `volumetrics` recipe for placement,
  instancing, camera-cull, LOD, and drift, paired with a thin volume material for
  fine density detail and light scattering. GN owns structure, the material owns
  detail. Sky, sun, and world haze stay shaders and Python because GN cannot author
  a world background, a light's contribution, or a uniform world volume. Pure-GN
  volume grids are avoided for cloud interiors because they voxelize; analytic
  shader noise keeps fine wisps sharp and lighter to render.
- Snow coverage is GN-authored. A geometry-node pass computes where snow sits and
  stores it as a mesh attribute; the accumulation shell and the surface material both
  read that one attribute, so there is a single source, no shader-versus-GN drift.
- Research before fallback. Where the plan makes a technical bet (GN-instanced
  volumes, per-instance volume noise), the Phase-0 spike researches it fully and the
  result picks the path. No retreat is pre-committed in the plan.

## Goal

One atmosphere system, art-directable from a panel, that renders correctly in
Cycles: a physical sky with a real sun, procedural volumetric clouds, layered fog,
and wind-driven weather particulates (rain, dust, amber motes). Everything is
Blender-side (shaders, geometry nodes, lights); there is no venv compute. It stays
extract-ready as `BobBlenderFirmament`, one-way dependencies, the seam being the op
contract plus scene state.

## Architecture and where it fits

A capability panel in BobBlenderTools, peer to Terrain and Scatter, and the base
layer they read the world from. Like scatter it is object-native and scene-driven,
not a parallel data model. Two scene state blocks, with a clear split so there is
no drift:

- `Scene.bbt_env` is the world state (the shared environment context below): time,
  date, season, latitude and longitude, weather, temperature, wetness, snow,
  cloud_cover, wind. BobFirmament registers and authors it; every capability reads it.
- `Scene.bbt_firmament` is BobFirmament's own UI and subsystem state: which clouds,
  fog, and particulate systems exist, their panel toggles, and the render quality
  level. Firmament-internal, not read by other capabilities.
- The sky lives on the World and a Sun light object. Volumes live on geometry-node
  objects (the `volumetrics` recipe) carrying a thin volume material. Particulates
  live on geometry-node objects in a camera-following domain. Each is a normal
  datablock, so a rebuild is non-destructive the same way scatter's is.

The seam to the rest of the system is the op contract plus `bbt_env`. One new op
authors the sky and world (`build_sky`); clouds, fog, and particulates reuse
`build_geonodes` with the new `volumetrics` and `particulates` recipes, backed by a
small volume-material helper. New ops mean an MCP server reconnect once; panel-only
changes need just an addon re-enable.

## Shared environment context (the world state)

One place holds the whole world state so every system agrees on it: time, date,
season, latitude and longitude, weather, temperature, wetness, snow level, cloud
cover, and wind. BobFirmament owns it. Atmosphere is time, season, and weather, so
the atmosphere system is the natural authority for the world, and it defines what the
other systems see: if it is raining, Scatter and Shaders read that and respond with
droplets and wet ground. This is the intended design, not a compromise.

The one cost, stated plainly: because BobFirmament owns the world state, the other
capabilities depend on it. That is a deliberate one-way dependency, BobFirmament as
the base of the graph. It does not import them, so the graph stays acyclic and the
split stays mechanical, it just roots at BobFirmament instead of a neutral package.

- The world state is `Scene.bbt_env`, a PropertyGroup defined and registered by
  BobFirmament in `bbmcp/env.py`: time_of_day, date, season, latitude, longitude,
  weather, temperature, wetness, snow, cloud_cover, and wind direction and strength.
  BobFirmament registering it resolves the ownership question, there is one registrar.
- BobFirmament's panel is the author and UI for it, and its own subsystems (sky,
  clouds, fog, precipitation) are the first consumers.
- Every other system reads it: Scatter thins or swaps layers by season, Terrain and
  BobShaders whiten and wet surfaces by weather and temperature. The read path is the
  `env.py` accessor plus a small helper that feeds an env value into a geometry-node
  input or a driver, so a value can be read live. Readers guard for `bbt_env` being
  absent (BobFirmament disabled) and fall back to their own defaults, so a capability
  still functions standalone, just without the shared weather.
- Post-split, `bbt_env` travels with BobFirmament (or a thin BobEnv core carved from
  it) and stays the dependency other repos build on, the lowest layer under the op
  contract. It is a vocabulary, like the ops, not a cross-import.

Propagation follows the live-vs-structural rule the scatter work settled:

- Live: continuous values (snow level, wetness, wind, cloud cover) feed geometry-node
  and material inputs or drivers, so a surface whitens as snow rises with no rebuild.
- Structural: a change of kind (winter swaps summer trees for bare ones) is applied by
  an explicit Apply operator, never a property callback, to avoid the re-entrancy the
  scatter rebuilds hit. Moving the season slider updates the live look immediately; an
  Apply Season press does the asset swaps.

The payoff is the target vision: BobFirmament sets the season to winter, snow level
rises, and every system that reads the context responds. The map turns white, ground
wets at the melt line, scatter drops to winter assets on Apply.

## Subsystems

### 1. Sky and Sun (the lighting foundation)

A physical sky in the World shader (the MULTIPLE_SCATTERING model, the 5.2
successor to Nishita) plus a matched Sun light. The sun position is computed from
Time of Day, latitude, longitude, and date by a pure-Python solar model (the
standard NOAA equations, no dependencies, the same math Blender's Sun Position
add-on uses), then written to both the sky node (`sun_elevation`, `sun_rotation`)
and the Sun light's rotation, so the disc in the sky and the shadows in the scene
agree. A manual override lets an artist nudge elevation and azimuth off the
physical value. This is a World shader, so it renders the same in Cycles and EEVEE.

Op: `build_sky` (kind of world setup). Params: time_of_day, latitude, longitude,
date, sun_elevation/azimuth override, plus sky knobs (altitude, air, ozone,
turbidity, ground_albedo, the 5.2 sky's controls) and sun strength and angular
size. No world haze (see the Phase-0 finding): aerial perspective for the sky comes
from the sky model, scene fog is S3. The sky, sun angle, strength, and geographic
position are all (re)authored on a Build Sky press (the world/sun is a shader + light
rebuild, not a live-knob surface like the GN volumes), so changing time of day or a sky
knob takes effect on the next Build Sky.

### 2. Volumes: clouds and fog (GN structure + thin material)

Real Cycles volumes, procedural, no assets. Clouds and domain fog share one GN
`volumetrics` recipe (like `particulates` unifies rain, dust, and motes), paired
with a thin volume material. GN owns structure so it scales and expands; the
material owns fine detail and light scattering so quality holds.

Revised at S2 (see the S2 slice record): a cloud layer ships as ONE bounded domain
box, not a field of instanced cubes, because the instanced field showed box seams
and clipped the cloud at each cube face. The material carves the clouds out of the
single box with world-space noise and a Coverage threshold, and fades density to
zero at the box faces so nothing cuts off. The instancing-a-field description below
is the earlier forward-looking design; a bounded single domain is what a continuous
layer wants, and it also avoids the overlap-depth cost. Fog (S3) is likewise a
bounded domain. A field of many small instanced puffs remains an option for a later
scattered-cumulus mode, but is not how the S2 layer is built.

- The GN side places volume domains and shapes where they are: a coverage field
  decides which cells of a cloud layer are filled, instances the filled domains,
  culls them to the camera and fades by distance (LOD, reusing the scatter
  camera-cull), and drifts the whole field by Wind * time. For terrain-aware ground
  fog it samples the emitter surface so mist pools in low ground. The mode param
  selects a cloud layer, a height-fog slab, or patchy noise-fog banks from the one
  recipe.
- The material side is a thin Principled Volume that adds analytic noise density on
  top of the GN base and does the actual scattering and absorption. Analytic noise
  is sampled per render step, so cloud interiors stay sharp and light instead of
  voxelizing the way a pure-GN volume grid (Volume Cube, Mesh to Volume) would. Per
  instance texture coordinates vary each puff so an instanced field does not repeat.
- Cloud and fog use SEPARATE materials, not one shared material (decided at S3, see
  the S3 record). Their core primitives are opposites: the cloud material carves
  shapes out of the box with a Coverage threshold and a symmetric all-face envelope;
  the fog material is a continuous medium with a vertical density gradient (dense
  low, thin high) that doubles as the top-face fade. Forcing fog through the cloud
  material would fight that difference (its envelope fades the bottom face, exactly
  wrong for ground fog). `materials.BOB_CloudVolume` and `materials.BOB_FogVolume`;
  height_fog and noise_fog share BOB_FogVolume and differ only by knob defaults, while
  ground_fog uses a per-heightmap material (BOB_GroundFog_<image>) whose height profile
  is terrain-relative (samples the heightmap so the mist hugs the ground). Fog carries
  Anisotropy, Fog Color, Warp, and a Falloff curve on top of the height profile.
- Aerial perspective is not a world volume. A constant-density world Principled
  Volume was the plan, but Phase-0/S1 disproved it: an unbounded world volume has
  infinite optical depth and extinguishes the Sun lamp and skylight, blacking the
  frame at any usable density. So the sky dome's aerial perspective comes from the
  physical sky model (air, turbidity), and scene aerial haze is a bounded fog domain
  in the `volumetrics` recipe (height_fog / noise_fog), not a world volume.

Ops: `build_geonodes` recipe `volumetrics` (mode: clouds, height_fog, noise_fog)
builds the GN structure and assigns the volume material; a small volume-material
helper in `bbmcp/materials.py` builds and caches that material (a shader, not GN).
Live knobs: Coverage, Height, Thickness, Density, Detail, Softness, and the fog
variants of those. Mode, domain bounds, and the emitter (for terrain-aware fog) are
structural.

Cycles volume settings (step rate, max steps, volume bounces) are set on build from
a quality level, so the volumes actually resolve instead of rendering thin or noisy.

### 3. Particulates: rain, dust, amber motes

One parameterized geometry-node recipe in a camera-following domain, driven by Wind
and Scene Time, with two shape modes:

- Streaks (rain): fast downward fall, geometry stretched and aligned to velocity,
  wind leans the streaks. Points recycle within the domain height by a time modulo so
  a small point count covers an endless shower.
- Motes (dust, amber motes): slow drift with turbulence. Dust is denser and
  wind-driven near the ground; amber motes are fine, sparse, and lit by the low sun
  for the golden-hour look. Both are lit by the scene (non-emissive); an optional
  Emission knob (default off) leaves room for fireflies or embers later.

The domain follows the camera (a driver copies the camera location, or the recipe
reads the camera through Object Info) so the effect is always around the shot. Scene
Time drives motion, so Cycles animation renders are deterministic frame to frame.
Instance motion blur is enabled for rain and motes so fast particles read as streaks
in the final render.

Op: `build_geonodes` recipe `particulates`. Params: mode (streak/mote), count,
fall_speed, drift, turbulence, size, size_variation, camera, wind (from the scene),
emission. Presets seed rain, dust, and amber-mote looks.

### 4. Wind and Time (from the world state)

Wind and Time of Day live in `bbt_env`, the world state BobFirmament owns. Wind (direction, strength) is consumed by the particulate
velocity, the streak lean, and the cloud and noise-fog drift. As of S5 this is a live
feed: the Live Environment toggle drives each subsystem's Wind Direction / Wind Speed
inputs (and the snow-coverage Snow input) from `bbt_env` with drivers, so moving the
Environment wind slider moves everything with no rebuild and no per-object press; the
drivers are reinstalled on every build (socket identifiers regenerate on a rebuild). Scene
frame drives all animation through a Scene Time node, so nothing depends on wall-clock and
every frame of a Cycles render is reproducible. Time of Day sets the sun for the shot (Scene Time
is separate) and can be keyed for a day-night sweep.

### 5. Snow (GN-authored coverage, one source)

Snow is not a single technique, but its coverage has a single owner. A geometry-node
pass computes where snow sits and writes it as a mesh attribute; everything else reads
that one attribute, so the shell and the material never disagree. This is the fix for
the shader-versus-GN drift risk: GN authors, others read.

- Coverage attribute (GN): where snow sticks is up-facing, high enough, and sheltered.
  A `snow` GN pass computes `env.snow` gated by surface slope (normal Z) and altitude
  (world Z), with an occlusion term where a surface is enclosed, and stores the result
  with Store Named Attribute as a 0..1 `snow_cover` attribute on the mesh. Slope and
  altitude are trivial in GN; occlusion is the harder term and can start crude and
  improve. This pass is the single source of coverage.
- Surface snow look (material reads the attribute): a BobShaders weather layer mixes a
  snow shader (white albedo, soft roughness, sparkle, subsurface) by the `snow_cover`
  attribute read through an Attribute node. It is shading, not geometry, so it lives in
  the material, but it does not recompute coverage, it consumes the GN attribute. This
  is what covers the map. GN doing the coverage and the material reading it is the
  requested "GN drives the material" shape.
- Accumulation shell (GN): the same pass, or one chained to it, displaces the surface
  along its normal by `snow_cover`, then smooths and rounds it for real thickness,
  silhouette, and drifts. It reads the same attribute it wrote, so shell thickness and
  material whiteness line up exactly. Added where thickness matters.
- Falling snow (GN): the `particulates` recipe in mote mode, a white, slow,
  fluttering, wind-driven preset. In BobFirmament, done when particulates land.

One nuance to keep honest: a surface with no `snow` GN modifier has no `snow_cover`
attribute, so its material reads the default (no snow). To whiten plain materials that
carry no modifier, the BobShaders layer can compute a basic slope-and-altitude coverage
from the shader Geometry node using the same formula. That is the one place two
implementations of the formula exist; keep the formula documented in one spot and
identical. The GN pass stays the authority and the only path with occlusion and
accumulation.

For BobFirmament now, snow is the falling-snow preset plus the `env.snow` state and the
`snow` coverage pass. The surface material and the shell can land with BobShaders,
reading the attribute the pass writes.

## Cycles-readiness

This is the heaviest system in the repo and the reason the plan leads with rendering.

- Real volumes, not fakes. Every atmospheric effect is an actual Cycles volume (world
  or domain), never a compositor mist-pass trick. Materials use only Cycles-supported
  nodes (Principled Volume, Volume Scatter and Absorption, noise, gradients).
- Settings on setup. The build ops set Volume Step Rate, Max Steps, and Max Volume
  Bounces from a quality level (preview vs final), so volumes resolve rather than
  render thin or grainy. Preview lowers them for viewport and headless checks.
- Emission and motion blur. Optional particulate emission lights the scene in Cycles;
  rain and motes get instance motion blur. Rain uses translucent or lightly emissive
  streaks, never glass, to avoid Cycles refraction cost.
- The verification gate is a real Cycles render (see Verification), the same
  empirical, headless bar the rest of the repo holds.

Known Cycles landmines this system must handle, called out so a slice does not walk
into them:

- Double sun. The Nishita sky has a sun disc that lights the scene. With a separate
  Sun light on top, sunlight is counted twice (over-bright, double speculars). Fix:
  the disc lights or the lamp lights, not both. Default is to suppress the disc's
  contribution and let the Sun lamp light, so shadow softness stays controllable; the
  disc angle still tracks the lamp so the visible sun sits right.
- World volume cost and overlap. A world volume is unbounded, so its optical depth is
  infinite: it extinguishes distant light (the Sun lamp and skylight originate at
  infinity) and blacks the frame. S1 confirmed this and dropped world haze entirely
  (see Phase 0 findings). Domain fog is bounded, so it does not have this failure;
  aerial perspective for the sky is the sky model's job. Do not put a Principled
  Volume on the World volume output.
- Camera-following domain and motion blur (RESOLVED at S4, 2026-07-19). If the
  particulate domain teleports to follow the camera each frame, instances inherit a
  spurious motion-blur streak from the jump, and world-anchored parallax is lost. The
  fix that shipped: particle motion is computed in world space (`moved = base +
  velocity * scene_time`) and each particle is re-tiled to the copy nearest the camera
  (`rep = moved - box*round((moved - cam)/box)`), so `rep` is anchored to the true world
  position and motion blur reads the real particle velocity, not the camera's. The
  earlier "domain follows in whole steps" phrasing (snap the follow-centre to the box
  lattice) is the WRONG reading: it concentrates the jump into occasional all-particle
  streak frames rather than removing it. Continuous follow with round-based re-tiling is
  what works, measured (0 domain jump on a whole-box camera move; blur = world velocity
  with the camera moving 6 m between subframes). See the S4 slice record.
- Volumetric shadows and light shafts. Clouds self-shadowing and shadowing the ground
  are a large part of the look. The plan expected this to be an expensive hero-only
  fork; S2 measured it and found the cost tracks sun elevation, not shadows as such
  (high sun, shadow rays cross only the layer thickness, cheap; low sun, they cross the
  whole layer, expensive). So v1 ships self-shadow ON by default for real cloud form,
  with a per-object Cloud Shadows toggle to switch it off for the one costly case, a
  low sun at Final quality. See the S2 findings.

Performance budget. This is the one system where a frame can cost minutes, so a target
is set rather than discovered. The Final quality level aims for a 1080p frame in a
low number of minutes on the dev GPU with denoising on, clouds plus one fog plus one
particulate system active. Levers when over budget: the Preview level (coarser volume
steps, fewer particulates, cheaper shadows), render-layer and holdout separation of the
heavy volumes, and adaptive sampling with the OptiX or OpenImageDenoise denoiser. The
budget is checked at S5 on a real frame, not just the tiny verification renders. Measured
at S5: a 1080p Final frame with clouds plus one fog plus rain, 96 spp adaptive, OptiX with
denoising, landed at ~175 s (about 2.9 minutes) on the 5080, within the low-minutes budget.

## Ops and code layout (extract-ready)

All bpy-only, one-way dependencies, so a `BobBlenderFirmament` split is mechanical:

- `bbmcp/world.py`: `build_sky` (physical MULTIPLE_SCATTERING world + Sun light +
  apply solar result; no world haze, see the S1 finding).
- `bbmcp/env.py`: the world state (`Scene.bbt_env` schema, registered by BobFirmament,
  plus accessors and a small geometry-node/driver feed helper). Imports only bpy. The
  base layer other capabilities read; BobFirmament owns registration, so there is one
  registrar and no ambiguity about who creates it.
- `bbmcp/solar.py`: the pure-Python solar position math (no bpy), plus a thin driver
  namespace registration if drivers are wanted. Pure and unit-testable, like the
  heightfields math.
- `bbmcp/geonodes/recipes/volumetrics.py`: the GN recipe for clouds and domain fog
  (structure, instancing, cull, drift), composed from the shared blocks.
- `bbmcp/geonodes/recipes/particulates.py`: the GN recipe for rain, dust, and motes,
  composed from the shared blocks like the other recipes.
- `bbmcp/materials.py`: the thin volume material builder (a shader) the volumetrics
  recipe assigns, cached by name. The only non-GN, non-world shading code.
- Panel in the extension (`bob_blender_tools`), scene state in `Scene.bbt_firmament`.

The GN split mirrors the rest of the repo: `volumetrics` and `particulates` join
`terrain`, `heightmap_terrain`, `scatter`, and `wave_grid` as recipes, reached
through `build_geonodes`. Only sky, haze, and the volume material are shaders.

## Panel and presets

`BBT_PT_firmament` in the BobBlenderTools tab, with collapsible sub-panels:
Environment (the shared context: time, date, season, latitude/longitude, weather,
temperature, wetness, snow, wind, plus an Apply Season operator for the structural
swaps), Sky (sun override and sky knobs), Clouds, Fog (the three flavors), and Weather
(rain, snow, dust, amber-mote toggles and knobs). The Environment sub-panel is the UI
for `bbt_env`, which other capabilities read. A Blender-side preset dict, like the
scatter layer types: Clear Day, Golden Hour, Overcast, Storm, Foggy Dawn, Dust Storm,
Winter. Presets set the context and seed each subsystem. A quality toggle (Preview /
Final) scales the Cycles volume settings and particulate counts.

## Verification (headless Cycles render, each slice)

Honest limit first: unlike scatter, whose instance counts are unambiguous, most of
this system's quality is visual and subjective. A headless luminance check confirms
something rendered, not that it looks like a cloud rather than a grey slab. So the
render gate here is a smoke test that catches breakage and regressions, not a quality
gate. Each slice also has an eyeball checkpoint: a reference frame rendered and looked
at by a human before the slice is called done. The plan does not pretend the automated
render proves correctness.

With that stated, the automated gate: the subsystems produce light and volume, not
countable instances, so verification is a real render, not a depsgraph count:

- Render a tiny frame (about 64 px, 8 to 16 samples, coarse volume step) with the
  Cycles engine on CPU, to a temporary image, and read the pixels back.
- Sky: assert the frame is lit and non-black, and that moving Time of Day from noon to
  night lowers mean luminance.
- Volumes: assert a fog or cloud volume measurably changes luminance in its screen
  region versus the same frame without it.
- Particulates: assert instances appear in the domain (depsgraph count for the build
  side) and that a rendered frame gains variance where they are.
- Assert each render completes in a few seconds on CPU, so the gate stays runnable.

Keep the test scenes tiny; volume renders scale hard with resolution and samples.

## Phase 0 spikes (de-risk before building)

Confirm the risky rendering assumptions headless first, the way the GPU work opened
with a spike:

- Nishita sky lights a headless Cycles frame, and the solar model puts the sun where
  it should be for a known lat/long/date/time.
- A GN-built volume domain, shaded by the thin Principled Volume material, renders as
  a volume in a few seconds on CPU at 64 px and reads as cloud, not a grey slab.
- The central bet, researched fully before S2: GN instances several volume domains and
  Cycles renders them with per-instance variation. The specific unknown is whether the
  volume material can read a per-instance value (an Object Info Random, or an instance
  attribute captured in GN) so each puff's noise differs and the field does not tile.
  This is the linchpin of GN clouds, so the spike researches it thoroughly: instance
  attributes into volume shaders, per-instance texture coordinates, and the render cost
  of overlapping instanced volumes. The research picks the approach. No fallback is
  written into the plan ahead of that result.
- Camera-following particulates animate and motion-blur in Cycles without a spurious
  streak from the domain jump, with identical output for the same frame number.

If a spike is slow or wrong, the research says so and the plan is adjusted then, on
evidence, rather than on a guess made now.

### Phase 0 findings

- Per-instance volume noise (the linchpin), resolved 2026-07-19, confirmed headless in
  Cycles 5.2. GN-instanced volume domains render as volumes, and a per-instance value
  reaches the volume material through two mechanisms, both verified by driving
  per-instance emission and measuring per-cube luminance (control CoV 0.00):
  - Object Info -> Random in the shader gives a per-GN-instance random with no GN
    plumbing (CoV 0.24, four distinct puffs).
  - GN Store Named Attribute on the INSTANCE domain, read by a material Attribute node
    of type Instancer, carries any GN-computed per-instance value. A deterministic
    index/3 spread rendered as a clean 0, .33, .67, 1 luminance ladder (CoV 0.61).
  So the tiling worry is disproven and GN clouds are viable. Primary mechanism is the
  instance attribute (B), because GN can drive the per-instance offset with a computed,
  spatially coherent value (cell id, world position) rather than only random; Object
  Info Random (A) stays the free shortcut for pure variation.
- Cost at scale, RESOLVED (2026-07-19), timed headless on the dev RTX 5080 (OptiX,
  denoised), a GN field of overlapping instanced volume-domain cubes each carrying the
  thin per-instance-noise volume material. Headline: this is affordable, with the cost
  driven by factors other than instance count. Findings:
  - Instance count is cheap. A 540p Preview field went 16 puffs 18.6 s, 64 puffs 9.1 s,
    144 puffs 9.4 s, 256 puffs 13.1 s. More puffs did not cost more; 64 to 256 barely
    moved. So S2 can afford 100 to 250 puffs per layer.
  - Domain SIZE dominates, not count. In that same sweep the 16-puff field was the
    slowest because its puffs were the largest (150 m cubes); the 64-to-144 fields with
    40 to 64 m puffs were fastest. A worst-case v1 run of 16 huge 208 m overlapping
    domains at 128 spp took 174 s. Keep puffs small (about 30 to 70 m); avoid few large
    domains.
  - Cost is ray-marching the domain bounds (step count times overlapping domains a ray
    crosses), largely independent of visible density and even of whether a domain is
    full. An empty sky rendered 5.4 s at 1080p; the same view through a deep, dense
    144-puff field rendered 39 s, and a near-empty thin field still rendered 14 s. So
    the coverage field must NOT instance domains where the layer has no cloud (an empty
    domain still costs), cap volume max steps and step rate, and minimise overlap depth.
  - Budget headroom: a clouds-only 1080p Final frame lands in roughly 14 to 40 s on the
    5080 depending on density and layer depth, well inside the low-minutes budget, so
    there is room for one fog plus one particulate system. This does NOT include
    volumetric self-shadows or god-rays (the explicit shadow fork), which stay untested
    and expensive, deferred per the plan.
  - Shadow cost is driven by sun elevation, not by shadows as such (found in S2, then
    corrected). A first read of "cloud shadows on = over 15 minutes" came from a
    pathological 720p frame: low sun (22 degrees), Final steps, grazing camera, dense
    cloud, big ground. At a high sun (45 degrees), Preview steps, 540p, self-shadow ON
    is 25.5 s versus 29.9 s OFF, i.e. cheaper. Reason: shadow rays travel toward the
    sun, so a high sun crosses only the layer thickness (cheap) while a low sun crosses
    the whole wide layer near-horizontally (expensive). So S2 defaults self-shadow ON
    for real cloud form, with a toggle off for the low-sun Final case; volume_bounces is
    kept low (0 preview / 2 final).
  - S2 defaults set by this: Preview 32 spp, volume step rate 2.0, max steps 256; Final
    96 spp, step rate 1.0, max steps 512; puffs 30 to 70 m; 100 to 250 per layer;
    coverage culls empty cells; reuse the scatter camera-cull and add distance LOD.
  Decision confirmed: this spike ran as the immediate precursor to S2, not before S1.

Findings from S1 (2026-07-19, headless Cycles 5.2):

- The Blender 5.2 Sky Texture node dropped the NISHITA sky type. The physical models
  are now SINGLE_SCATTERING and MULTIPLE_SCATTERING (the Nishita successor;
  MULTIPLE_SCATTERING is the higher quality and what build_sky uses). Its knobs are
  air_density, ozone_density, turbidity, and ground_albedo; the old dust_density is
  gone, turbidity carries haziness. Probed empirically, treat as ground truth over
  older docs.
- World haze as a constant-density world Principled Volume does not work and is cut
  from build_sky. A world volume is unbounded, so its optical depth is infinite, and
  it extinguishes the Sun lamp and skylight (both originate at infinity): the frame
  goes black at any density above about 0.001 (measured: haze 0 renders mean 0.89,
  haze 0.001 renders 0.0003). This is the world-volume landmine in its strongest form.
  Consequence: aerial perspective for the sky dome comes from the physical sky model
  itself (air_density, turbidity, both verified to change the sky), and scene aerial
  haze is a bounded fog domain, which is S3's job, not a world volume. The plan's
  "world haze volume" is dropped; build_sky authors sky plus sun only.

## Slices

- S1 Context and Sky (done, 2026-07-19): `bbmcp/env.py` (`bbt_env`) and the
  Environment sub-panel, `build_sky`, `solar.py`, the Sky sub-panel, geographic sun
  with manual override driven from the context. World haze was cut (the finding
  above). Verified: `bbt_env` round-trips; the pure solar model matches the geometry
  (equator-equinox overhead, lat-45 solstice noon 68.4 deg, due south at noon, below
  the horizon at night, 9 unit tests); a lit non-black Cycles frame at noon; noon
  brighter than night; the Sun light placed and lit above the horizon and switched
  off below it; no duplicate world or sun on rebuild; the sky responds to air density;
  the manual override drives the sun. Eyeball: a summer-solstice sun sweep (dawn to
  dusk) casts shadows east to west as the sun crosses. `solar.py` is bpy-free and
  unit-tested; the sun-lamp aim is derived (Euler (90-el, 0, 180-az) rotates +Z onto
  the sun), not guessed.
- S2 Clouds (done, 2026-07-19): the `volumetrics` recipe (clouds mode) in
  `geonodes/recipes/volumetrics.py`, the cloud volume material in `materials.py`, the
  Clouds sub-panel and Build Clouds op. Design revised during the eyeball checkpoint,
  on the user's call: the first cut instanced a field of small volume-domain cubes,
  which showed their box seams and clipped the cloud at every cube face. It was
  replaced by ONE domain box for the whole layer, with the clouds carved out of it by
  the material: world-space fractal noise thresholded by Coverage (open sky where it
  clears the threshold, no seams), and the density faded to zero toward every box face
  (a Chebyshev envelope on the box's Generated coordinates) so the layer never cuts
  off at the bound. The box is instanced once so the live knobs (Density, Detail,
  Softness, Coverage, Cloud Scale, Cloud Seed) travel into the volume shader as
  INSTANCER attributes. This also sidesteps the instanced field's overlap-depth cost
  (a single crossing per ray, not many). Build Clouds sets the Cycles volume steps
  from the quality level and applies the shadow fork. Verified headless in Cycles on
  the 5080: one domain box; a Principled Volume driven by world position through
  INSTANCER attributes; renders as a volume (empty 0.50 to clouds 0.88) with real
  spatial variance (std 0.21); Coverage responds live (0.45 to 0.15 drops it back
  toward empty); Density responds live. Full-extension register test green (panel, op,
  shadow-fork default, non-destructive rebuild, clean re-register). The eyeball frame
  reads as a continuous cloud layer, no seams, no cutoff, ~4 to 25 s at 540p depending
  on how much cloud is in view.
  - Self-shadow is ON by default (revised, on measurement). The plan assumed cloud
    shadows were an expensive hero-only fork, from a 15-minute 720p frame. That frame
    was a pathological combination (low sun at 22 degrees, Final steps, a grazing
    camera, dense cloud). Measured properly, shadow cost is driven by sun elevation,
    not by shadows as such: a high sun's shadow rays only cross the layer thickness, so
    self-shadowing is cheap. At a 45-degree sun, 540p, Preview steps, shadow ON was
    25.5 s versus 29.9 s OFF, actually faster, and it gives the clouds real form
    (bright tops, dark undersides). So Build Clouds leaves the cloud object's
    visible_shadow ON by default and gives real dimensional cloud; the Cloud Shadows
    toggle turns it off for the one expensive case, a low sun with Final steps, where
    near-horizontal shadow rays cross the whole layer. volume_bounces is set from the
    quality level (0 preview, 2 final) so shadowed cloud reads bright, not muddy.
  - Coverage was linearised: the raw noise clusters near 0.5, so a (1 - Coverage)
    threshold crowded the useful range into a narrow band. The material now maps
    Coverage across the band the noise occupies (threshold 0.66 down to 0.34), so the
    knob reads roughly across 0..1 (measured 0.2 to 0.8 rising smoothly, saturating at
    the top). A 960p Final frame with self-shadow reads as dimensional cumulus in ~52 s.
  - Wind drift (added in S2): a Wind toggle advances a per-instance offset by Wind
    Speed * scene time along Wind Direction; the material shifts its noise sample by it
    so the cloud pattern drifts through the stationary box (verified: Wind on moves the
    pattern frame to frame, off is bit-identical). Scene time drives it, so a Cycles
    animation is reproducible. The Clouds panel groups the knobs (Shape, Layer, Wind),
    has a Randomize Cloud Seed button, a Wind Drift toggle, and a Use Env Wind button
    that copies the `bbt_env` wind onto the clouds. Full wiring of season and a
    scene-wide wind live-feed is still S5.
  - Polish pass (done): a domain warp in the material (Warp knob) pushes the noise
    sample around by a lower-frequency noise, so the clouds billow organically instead
    of reading as round blobs; verified as a clear quality lift on a cumulus frame.
    Coverage was retuned (threshold 0.74 to 0.38) so it runs from sparse wisps near 0
    to full overcast near 0.85, checked by a render sweep (0.2 sparse, 0.8 overcast).
    A Cloud Preset menu (Clear, Scattered, Cumulus, Overcast, Storm) sets the look
    knobs in one pick. Gate re-verified (10 checks) and register (18 checks) green.
  - Known nonblocking polish: the box top-face fade can show as a faint seam when the
    camera looks steeply up at the layer. No world haze (deferred per plan).
- S3 Fog (done, 2026-07-19): the `volumetrics` recipe in height_fog and noise_fog
  modes, a new `BOB_FogVolume` material, the Fog sub-panel and Build Fog op, four fog
  presets (Ground Mist, Valley Fog, Fog Banks, Thick Fog). Both fog modes are the
  SAME node graph and material; the `mode` param only selects fresh-build knob
  defaults (height_fog low Fog Noise for a near-uniform slab, noise_fog high Fog
  Noise for patchy banks), and the non-destructive rebuild preserves tuned knobs, so
  switching mode on an existing object keeps the knobs (the preset menu is the live
  look control, as with clouds). Key design decisions:
  - Fog gets its own material (see the Volumes subsystem note). `BOB_FogVolume` is a
    Principled Volume whose density = fog_density * a vertical height profile * a
    noise modulation * an XY-wall envelope. The height profile is built from the
    box's own Generated Z (0 at the box bottom, 1 at the top; the box is not rotated
    so this tracks world Z): dense at the bottom, fading to zero at Fog Top (a
    fraction of the box height). That gradient IS the height-fog / aerial-perspective
    look and doubles as the top-face fade, so no separate top envelope is needed; the
    envelope only fades the four XY side walls. The noise modulation is
    mix(1, banked_noise, Fog Noise), so Fog Noise 0 is a uniform slab and 1 is fully
    patchy banks; Softness sets the bank edge width. The noise sample is drifted by a
    per-instance fog_wind vector (wind * scene time), reusing the cloud wind path.
  - Terrain-aware pooling is done CRUDE and needs no emitter sampling: because the
    height gradient is anchored to the box at a fixed world Z, valleys below the fog
    top fill and hills poke out (the sea-of-fog look). This is the aerial-perspective
    path the S1 uniform world haze could not give, because it has position. An
    emitter-draped ground-hugging mist (fog that follows the terrain over hills too)
    is a possible richer follow-up, not committed.
  - The recipe was refactored to share scaffolding: `_domain_geo` (one point ->
    single instanced cube + the wind-drift vector) and `_finish` (store the knobs as
    INSTANCE attributes, store the wind vector, set the material, output) are now used
    by both `_build_clouds` and `_build_fog`. The clouds path is byte-for-byte
    equivalent (regression-checked).
  - Verified headless on the 5080 (OptiX). Register/build: clouds still build
    (regression), the fog object is one instanced domain box with all fog sockets and
    the BOB_FogVolume material reading its seven fog INSTANCER attributes plus
    fog_wind, presets apply live, seed randomizes. Render smoke test (linear EXR
    metrics, since the Standard transform clips bright volumes to white): clouds
    change luminance vs empty (+0.22) and add variance; height fog is stronger low
    than high (low 1.16 vs high ~0, i.e. varies with Z); fog pools low (a hill base in
    the slab is more affected than its peak above the slab); noise fog is ~8x patchier
    across X than height fog. Eyeball: valley and banks vistas read as a sea of fog
    with conical hills poking out, bases dissolving into the fog. Nonblocking: an
    immersed camera deep inside a dense slab goes near-black (optical depth), so the
    fog is a viewed-from-outside effect; a very dense slab whites out (keep density
    modest, ~2-4, for wide vistas).
  - S3 polish + ground fog (done, 2026-07-19, on the user's "do everything not scoped
    for later"). The fog material was refactored into one builder shared by a box
    variant and a terrain variant, and gained four look knobs plus a third mode:
    - Anisotropy (forward scattering, default 0.4) drives the sun-side glow and makes
      light shafts read; Fog Color tints the scattering albedo (cool shadow / warm
      dawn); Warp domain-warps the noise so banks billow organically (lifted from the
      cloud material); Falloff is a power on the height curve (>1 hugs the ground
      tighter). All are live INSTANCER knobs; Fog Color is stored as a FLOAT_COLOR
      instance attribute (the recipe's _finish grew an extra_stores hook for non-float
      knobs).
    - ground_fog mode: a terrain-draped mist. Because a volume shader cannot sample an
      arbitrary mesh per march-point, the material samples the terrain HEIGHTMAP PNG
      (the same ones heightmap_terrain bakes) by world XY, reconstructs terrain Z the
      same way (`(sample - Sea Level) * Terrain Height`), and fades density with height
      above that ground over Ground Thickness. So the mist follows hills up and over,
      not a fixed-Z slab. This completes the plan's terrain-aware goal (the emitter
      sampling flagged at S3) via the heightmap rather than emitter geometry. The
      material carries the image (a node property, not a socket), so it is cached per
      heightmap image (BOB_GroundFog_<image>), unlike the shared box BOB_FogVolume;
      the terrain mapping (size/height/sea level/thickness) stays live. No heightmap
      falls back to the box material.
    - Panel: the Fog Mode enum gains Ground Fog with a heightmap picker; new knob
      groups Look (Fog Color, Anisotropy) and Terrain (shown only for ground fog); the
      presets set Warp/Falloff/Anisotropy too.
    - Verified headless on the 5080: build/attr (26 checks: polish sockets, material
      reads fog_warp/fog_aniso/fog_color, ground per-image material with the heightmap
      image texture as Non-Color, terrain sockets, heightmap-absent fallback). Render:
      a blue Fog Color tints the fog blue (dBlue 0.99 vs dRed -0.18); anisotropy
      changes the look; ground fog changes the frame and responds to Ground Thickness
      (more thickness = more mist, the draping mechanism). Eyeball: ground fog clings
      to the terrain and follows its contour, the polished valley vista is unchanged.
    - Still scoped for later (S5): camera-following aerial fog for whole-landscape
      depth without the immersion whiteout, auto-driving fog from bbt_env weather/time,
      multi-layer fog, vertical turbulence. Emitter-mesh-draped fog (vs heightmap) also
      remains a possible alternative if a fog is wanted over non-heightmap terrain.
- S4 Particulates and falling snow (done, 2026-07-19): the `particulates` recipe
  (`geonodes/recipes/particulates.py`, streak and mote modes), two cheap particulate
  materials (`materials.rain_material` / `mote_material`), the `snow` coverage recipe
  (`geonodes/recipes/snow.py`), a `build_geonodes_on_object` helper, and the Weather
  sub-panel with Build Rain / Build Motes / Add Snow Coverage, rain and mote presets,
  Randomize Seed, and Use Env Snow. Reuses `build_geonodes` (no new op, no MCP
  reconnect; the snow pass attaches via the new helper, panel-side). Key decisions:
  - Camera-follow WITHOUT a domain jump (the flagged motion-blur landmine, resolved on
    measurement). Each particle has a continuous world position `moved = base +
    velocity * scene_time` (+ turbulence for motes), then is re-tiled to the copy
    nearest the camera: `rep = moved - box*round((moved - cam)/box)`. Because `rep` is
    anchored to the particle's own world position, its motion-blur velocity is the true
    world velocity for every particle except the small fraction crossing a window edge
    within a shutter (~ speed*shutter/box). The camera's motion never enters the blur.
    IMPORTANT: snapping the follow-centre to the box lattice ("whole steps", the naive
    reading) is the WRONG move; it concentrates the jump into occasional all-particle
    streak frames instead of removing it. Continuous follow + round-based re-tiling is
    the fix. Verified: a whole-box camera move gives a bit-identical field relative to
    the camera (0 domain jump); with the camera moving 6 m between two shutter samples,
    non-wrapping particles still displace by exactly world-velocity*dt (camera ignored);
    a stationary-camera time-wrap fraction of 0.53% matched the predicted 0.47%.
  - Streaks are real geometry, not a blur artefact. A thin tapered cone (needle) is
    stretched along its local Z (Depth = Fall Speed * Streak Length) and aligned to the
    velocity vector with Align Rotation to Vector, so wind leans the streak (verified:
    instance long axis dot velocity = 1.000). The geometric streak is why the residual
    per-particle wrap is invisible under motion blur. Rain uses a cheap Transparent-mixed
    Principled (no glass/transmission, per the plan) whose opacity holds solid across the
    streak core and eases to zero at the tips (a smoothstep plateau on the cone's
    Generated Z: full out to 60% of the half-length, fading by the end), so a streak
    reads as a legible soft needle rather than a hard-capped tube; the geometry is thin
    and short by default (Size 0.010, Streak Length 0.2, up-scaled by motion blur). (The
    first cut was a solid cylinder that read as thick rods, then a triangular taper that
    read too faint; the cone + plateau is the polished result.) Motes are ico spheres
    with live Color and optional Emission (default 0) as INSTANCER knobs; a Translucent
    BSDF is mixed into the diffuse base so motes forward-scatter and glow when backlit by
    a low sun (the plan's sun-lit golden-hour look, verified: backlit amber motes render
    as bright warm specks where a pure-diffuse mote would show its dark side). Dust,
    amber motes, and falling snow are the same mote mode; a preset picks the look.
  - Particle count is a live knob and the depsgraph instance count is exact here
    (unlike volumes): Build Rain / Build Motes report the count. Motion blur is a panel
    toggle (default on) that sets `scene.render.use_motion_blur`.
  - Snow coverage is one GN pass on the terrain (the single coverage source): it runs
    as a modifier AFTER the terrain modifier (so it sees the displaced surface), passes
    geometry through, and writes `snow_cover = Snow * slope_mask(normal Z) *
    altitude_mask(world Z) * (1 - occlusion)` on the points. Slope and altitude are
    solid smoothsteps; occlusion is a crude-but-real short upward Raycast against the
    same mesh (a heightfield has no overhangs to trigger it, so it is a structural path
    for now, gated by the Occlusion knob, the term meant to improve later). The pass
    attaches with `build_geonodes_on_object` (non-destructive, mirrors `build_geonodes`
    with a `reset` flag), so re-pressing Add Snow keeps tuned knobs. Verified: snow_cover
    in [0,1], correlates with up-facing slope (0.85) and altitude (0.87) each isolated,
    scales with the Snow knob; seeded from `bbt_env.snow`. The surface snow material and
    accumulation shell that read the attribute land later with BobShaders.
  - Verified headless on the 5080 (OptiX): 15 recipe checks (count, determinism, re-tile
    no-jump, subframe blur velocity, time-wrap fraction, streak alignment, mote knobs,
    snow_cover sanity) and 16 register/panel-op checks (build ops, presets set knobs
    live, Randomize Seed, snow pass stacks after terrain and seeds from env,
    non-destructive re-press, clean re-register) all green. Eyeball: rain reads as
    wind-leaning streaks filling the camera domain; falling snow reads as white motes.
    Nonblocking: rain looks best against a darker/overcast sky (bright Nishita sky is low
    contrast for pale streaks); the terrain in the test frames carried no material.
  - S4 polish pass (done, 2026-07-19): rain readability (cone needle + opacity plateau +
    retuned presets, above), sun-lit motes (Translucent BSDF mixed into the mote material,
    above), and two consistency additions matching the Clouds/Fog panels: a "Use Env Wind"
    button for rain and motes (`firmament_particulate_wind_from_env`, live-syncs Wind
    Direction/Speed from `bbt_env`), and object-level `cycles.use_motion_blur` set on build
    (not just the scene switch), so fast particles are guaranteed included. Re-verified
    headless (14 polish checks green: cone geometry, alignment preserved, determinism,
    opacity plateau, translucent mote, particulate Use-Env-Wind, object motion blur, icons,
    full operator coverage, clean re-register) plus eyeball frames (rain reads as legible
    needles not tubes; backlit amber motes glow warm). Snow-coverage preview and a better
    occlusion term were considered and deferred (occlusion stays crude per the plan).
- S5 Wind, season, presets, and budget (done, 2026-07-19): the live env feed (wind and
  snow driven from `bbt_env`), the Apply Season operator, the whole-scene preset dict
  (including Winter), the Preview/Final quality toggle extended to scale particulate
  counts, and the performance budget checked on a real 1080p frame. All headless on the
  5080 (OptiX). Key decisions:
  - Live env feed is DRIVERS, not a callback or handler (the plan's "continuous values
    feed via drivers"). A `frame_change` handler would not fire on a slider drag, and a
    `depsgraph_update_post` handler that writes sockets risks the re-entrancy the repo
    avoids; a driver reading `scene.bbt_env.wind_*` re-evaluates the instant the slider
    moves. A scene-level Live Environment toggle (`bbt_firmament.live_env`, default on)
    installs drivers on Wind Direction / Wind Speed of clouds, fog, rain, and motes (and
    sets the volumes' Wind toggle on), plus the snow-coverage pass's Snow input from
    `bbt_env.snow`; off removes them so the knobs are manual (the per-object Use Env
    buttons then do a one-time copy). Verified: the drivers install, are valid, and their
    variable reads the right `bbt_env` field; a render-delta test proved changing only
    `env.wind_strength` (0 to 40) changes the rendered cloud (mean|delta| 0.039).
  - Driver mechanism and the RNA path (verified 5.2): drive the GN modifier input via
    the input struct itself, `mod.properties.inputs.<ident>.driver_add("value")`, which
    routes to the object's animation data at
    `modifiers["GeometryNodes"].properties.inputs.<ident>.value` (dot notation on the
    input, NOT bracket `["Socket_N"]`, and NOT an IDProperty path, both of which fail).
    The socket identifiers regenerate on the non-destructive rebuild, so a driver keyed
    by identifier would dangle; every build op REINSTALLS the drivers (our build ops are
    the only path that rebuilds these objects), clearing any prior driver on the input
    first.
  - Headless caveat (a real S5 landmine): a driver ADDED to the persistent background
    depsgraph is not evaluated by a bare `view_layer.update()` or `frame_set` on that
    same graph; the driven value trails or stays at the default. It IS evaluated after a
    full depsgraph rebuild, which a real render and the interactive UI both do (Blender
    guarantees driver eval in renders). So the live feed works in every real path;
    only immediate headless value-readback is unreliable. Verify the WIRING
    deterministically (driver valid + variable target) and prove EVALUATION with a
    render-delta, not by reading the modifier input back in-session.
  - Apply Season is an explicit operator (not a property callback, to dodge the scatter
    re-entrancy). It reads `env.season` and sets the season's continuous state (snow,
    wetness, temperature, fed live to the readers); for Winter it also builds the falling
    snow (the snow mote preset) and the snow-coverage pass on the surface, whose Snow
    input is driven from `env.snow` so it tracks the level. Season owns only the seasonal
    state and its own subsystems; it leaves time, place, and wind (the shot setup) alone.
  - Whole-scene presets (Clear Day, Golden Hour, Overcast, Storm, Foggy Dawn, Dust Storm,
    Winter) set `bbt_env` in one pick and seed each named subsystem, building any that is
    missing (at the current quality) then applying its per-subsystem preset. A subsystem
    the preset names None is left alone, not deleted. `SCENE_PRESETS` on the main panel;
    the sky is rebuilt so the sun moves to the preset's time.
  - Quality scales counts: the particulates recipe gained a Quality Scale input
    (Points Count = Count * Quality Scale). `_apply_quality` sets the Cycles volume step
    rate / max steps / bounces AND the Quality Scale on every particulate object from the
    level (preview 0.35, final 1.0), called by every build and by the quality toggle's
    update callback so switching quality re-applies live without a rebuild and without
    fighting the authored Count.
  - Performance budget (the one real-frame check, measured on the 5080, OptiX, denoise
    on): a 1080p Final frame with clouds (cumulus) + one height fog + rain, 96 spp
    adaptive, over an untextured terrain, rendered in ~175 s (about 2.9 min), within the
    plan's low-minutes budget. Levers if over: the Preview level (coarser volume steps,
    0.35 particulate count), thinner/lower fog (a dense or camera-immersed slab whites
    out, the S3 note, and dominated a first framing), and cloud shadow off for a low sun.
  - Verified headless: 32 logic checks (icon/idname/prop audit; wind + snow drivers
    install, are valid, and target the right `bbt_env` field; reinstall across a rebuild;
    Live Environment off removes them, on reinstalls; quality sets Quality Scale and
    thins the count; Apply Season Winter raises snow, drops temperature, builds falling
    snow and the coverage pass; scene presets set env and build/seed subsystems; every
    firmament operator executes), plus the wind render-delta and the budget frame. Eyeball:
    the budget frame reads as dark cumulus over a hazy fogged terrain with rain streaks.

- S5 pre-BobShaders hardening (done, 2026-07-19): a three-axis sweep (docs-vs-code,
  dead/delinked code, integration/BobShaders-readiness) before building BobShaders on top.
  Fixes:
  - Modifier-reorder bug (silent-wrong): `build_geonodes`' in-place rebuild appended the
    fresh modifier at the end of the stack, so re-baking a terrain that carried the
    BOB_Snow pass flipped the order to [BOB_Snow, terrain] and computed `snow_cover` on
    the undisplaced mesh. Now it records the old modifier index and moves the fresh one
    back, so a later pass keeps running after the rebuilt one. Verified with a
    terrain+snow rebuild test.
  - `env.cloud_cover` was delinked; now driven live onto the cloud layer's Coverage under
    Live Environment (the cloud preset keeps `env.cloud_cover` in step so presets read
    right either way).
  - Use Env Wind / Use Env Snow buttons were no-ops under the default (a driver owns the
    input); they are now shown only when Live Environment is off.
  - Scene presets / Apply Season built rain and motes with no follow camera (spawned at
    the world origin); `_build_particulate` now falls back to `scene.camera`.
  - `env.get_env()` / `sun_params()` (the documented shared-world API) had no callers;
    Build Sky now assembles its geographic inputs via `sun_params`, and the Firmament
    consumers read through `get_env`, so the pattern BobShaders is told to copy is
    exercised in-tree.
  - `weather` / `temperature` / `wetness` are authored but read by no one yet (BobShaders
    will consume them); the `env.py` docstring was corrected so it no longer claims
    `weather` drives structural swaps (only `season` does, via Apply Season).
  - Docs/comment drift fixed: the Build Sky tooltip no longer says "Nishita"/"world haze",
    the stale "shadow default off" comment, the SYSTEMS `streak_length` default (0.2),
    stale "cylinder" comments (geometry is a cone), and the ignored `turbulence` build
    param. The snow coverage formula's exact smoothstep endpoints are now pinned in one
    SYSTEMS.md block (the dual-formula spot BobShaders must match), and the settled S1-S5
    state was folded into ARCHITECTURE.md.
  - Dead code removed: three unused attribute-name tuples in materials.py.

Out of BobFirmament's core slices, landing with BobShaders: the surface snow material
and the optional accumulation shell. Both read the `snow_cover` attribute the S4 pass
writes, so they line up with the falling snow with one coverage source.

## Toward BobShaders

BobShaders is the planned capability after BobFirmament: authored surface materials.
The world state makes it a first-class consumer. Its central idea is a shared weather
layer every BobShaders material includes, driven by `bbt_env`: snow by the `snow`
coverage the GN pass writes, wetness and darkening by `env.wetness`, frost by
temperature, moss and dust by season. The surface snow look above is the first slice of
that layer, and it reads the `snow_cover` attribute rather than recomputing coverage.
BobShaders depends on BobFirmament for `bbt_env`, the base-layer dependency described
above, and guards for it being absent so a material still works standalone. Full plan
when BobFirmament lands; noted here because the world state is what ties the two
together.

- `bbmcp/solar.py` imports nothing but the standard library. Pure math in, sun angles
  out. Unit-testable and trivially extractable.
- `bbmcp/world.py`, `bbmcp/env.py`, `bbmcp/materials.py`, and the `volumetrics` and
  `particulates` recipes import only bpy. No MCP, no venv.
- BobFirmament is the base of the dependency graph, not a neutral standalone. It owns
  `bbt_env`, and Terrain, Scatter, and BobShaders depend on it to read the world. The
  dependency is one-way (BobFirmament imports none of them), so the graph is acyclic and
  a split is still mechanical; it simply roots at BobFirmament. Post-split, `bbt_env`
  travels with BobFirmament or a thin BobEnv core carved from it.
- The seam is the op contract (`build_sky`, `build_geonodes` for the `volumetrics` and
  `particulates` recipes) plus the `bbt_env` world state, so a subsystem in another repo
  behaves the same as one in-tree, consistent with the bus model in `UNIFIED-SYSTEM.md`.
