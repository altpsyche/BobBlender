# Systems reference

Parameter and usage reference for the geometry-node recipes and the erosion
pipeline. For the design and layout, see `ARCHITECTURE.md`.

## How building works

An agent (or a script) sends ops. A geometry op looks like:

```json
{"op": "build_geonodes", "recipe": "terrain", "name": "Terrain",
 "params": {"height": 14, "seed": 3}, "target": "new_object"}
```

`build_geonodes` makes a node group, fills it from a recipe, and puts it on a new
object with a Geometry Nodes modifier. Two kinds of parameter:

- Live knobs: most numeric params become group inputs, editable on the modifier
  (3D View sidebar, or Modifier Properties) without rebuilding.
- Build-time params: things that change the graph or reference datablocks
  (emitter, assets, align, material, heightmap). Changing these means rebuilding.

Ops reach Blender two ways: `build` (headless, writes a .blend) and `build_live`
(into the open session, via the BobBlenderTools extension). After editing recipe
code, press Reload Builders in the MCP Bridge panel so the live bridge reloads it.

### Rebuilding in place

`build_geonodes` is non-destructive: if an object of that name already exists, it
refills that object's node group instead of respawning. The object, its
transform, selection, and your tuned modifier knobs all survive a graph rebuild,
so re-firing a build after a recipe edit updates the graph under your cursor. Pass
`"reset": true` to discard the tuned knobs and reapply the op's params fresh.

(Knob values in Blender 5.2 live on the node group interface socket defaults, not
on the modifier, since a Nodes modifier has no IDProperties. The rebuild snapshots
and restores them by socket name.)

### reload_image

`{"op": "reload_image", "path": "<abs path>"}` reloads image datablocks from disk
(all of them if `path` is omitted) and tags objects to re-evaluate. Use it after
`bake_heightfield` overwrites a heightmap PNG so the terrain updates without a
manual reload.

## terrain (recipe: `terrain`)

A fully procedural, live terrain. How it works: a grid, displaced in Z by a
height field. The field is a low-frequency Shape (the big landforms) plus a
domain-warped fractal Detail that is blended toward ridged and rides on the
Shape, so high ground is craggy and low ground is smoother. Sea Level sets where
the surface crosses z = 0; Height scales the relief.

| Param | Default | Live | What it does |
|-------|---------|------|--------------|
| `size` | 60 | yes | Grid width in metres. |
| `resolution` | 400 | yes | Grid subdivisions per side. Higher = more detail, heavier. |
| `height` | 14 | yes | Vertical scale of the relief. |
| `sea_level` | 0.35 | yes | Field value that maps to z = 0. Higher sinks more into valleys. |
| `shape_scale` | 0.025 | yes | Frequency of the big landforms. Lower = larger features. |
| `scale` | 0.11 | yes | Frequency of the detail noise. |
| `detail` | 9 | yes | Fractal octaves. More = finer structure. |
| `roughness` | 0.55 | yes | How much each finer octave contributes. |
| `warp` | 6 | yes | Domain-warp strength. Organic, flowing distortion. |
| `warp_scale` | 0.05 | yes | Frequency of the warp. |
| `ridged` | 0.6 | yes | 0 rolling hills, 1 sharp mountain ridges. |
| `detail_strength` | 0.7 | yes | How strongly detail rides on the shape height. |
| `seed` | 0 | yes | Changes the whole terrain. |

Tips: raise `ridged` for mountains, `warp` for organic shapes, lower `scale` and
`shape_scale` for bigger features, move `sea_level` for the waterline.

## Eroded terrain (`bake_heightfield` + recipe `heightmap_terrain`)

Higher quality than the live `terrain`, because erosion is simulated in the venv
(numpy on CPU, a CuPy CUDA kernel on GPU). Two parts:

1. The venv bakes an eroded heightmap PNG (`bake_heightfield`).
2. `heightmap_terrain` displaces a grid by that image in Blender.

### Baking the heightfield (tool: `bake_heightfield`)

`bobtools.heightfields`, a pure venv subpackage (graduated from the old
`erosion.py`, which stays as a compat shim). Base generation is seeded scipy
noise (domain warp, ridged blend, shape). Erosion is a list of passes:

- `hydraulic`: droplet erosion with sediment transport and deposition. The GPU
  track. Carves drainage rills. Erosion spreads over a `radius` brush so valleys
  stay smooth instead of spiky. Params: `droplets`, `max_steps`, `radius`,
  `inertia`, `capacity`, `deposition`, `erosion`, `evaporation`, `gravity`,
  `min_slope`.
- `thermal`: slumps slopes past a `talus` angle. Params: `talus`, `factor`,
  `iterations`.
- `smooth`: gaussian blur, `sigma`. Bracket the hydraulic pass with one (a coarse
  base pre-smooth and a light final smooth) to keep the result from going gritty.
- `falloff`: taper the field toward the borders so the edges sink (islands,
  plateaus). Params: `margin` (fraction of the shorter side eased in), `power`,
  `floor`. Run it before `hydraulic` so drainage flows out to the sunk rim.
- `stream_power`: drainage-area incision (CPU, the original pipeline). Params:
  `iterations`, `erosion`, `m`, `n`, `talus`, `thermal_factor`.

A good recipe: `smooth` (sigma ~1.5) -> `hydraulic` (droplets 1.5-2.5M, radius 4)
-> `thermal` (iterations ~6) -> `smooth` (sigma ~0.8). The presets are built this
way. `build_params(knobs)` (in `heightfields/params.py`) is the one place that
expands a flat knob set into this pass list, shared by the presets, the panel, and
the CLI.

Droplet count is a density: a hydraulic pass may give `density` (the count at
768px) instead of an absolute `droplets`, and the pipeline scales it to the bake
resolution, so a preview and a full bake stay consistent instead of the low-res one
over-eroding.

The MCP tool `bake_heightfield(out_file, params, preview, force)` writes a 16-bit
PNG plus a `<name>.json` sidecar (the full recipe, so the field is reproducible),
and a params-hash cache skips a re-bake when nothing changed. `preview=True` bakes
at 256 for a fast look (a real `preview` arg on `bake()`, so agent and CLI runs are
resolution-independent too, not just the panel); `backend` is `auto` (GPU when
present), `cpu`, or `gpu`. The CPU path is the deterministic reference; the GPU path
is fast but not bit-identical (atomicAdd order).

```json
{"op": "bake_heightfield", "out_file": "library/_generated/forest_height.png",
 "params": {"size": 768, "seed": 5, "backend": "gpu",
   "generate": {"ridged": 0.5, "detail_strength": 0.5, "octaves": 7},
   "passes": [{"kind": "hydraulic", "droplets": 1200000, "erosion": 0.3,
               "deposition": 0.4, "max_steps": 72},
              {"kind": "thermal", "talus": 0.005, "factor": 0.45, "iterations": 8}]}}
```

Presets (`foothills`, `alpine`, `badlands`, `rolling`, `canyon`, `mesa`,
`islands`) are starting points: pass `"preset": "alpine"` in params and override
fields. Each is a flat knob set (`presets.PRESET_KNOBS`) expanded through
`build_params`. From a script, `bobtools.heightfields.bake(abs_path, params,
preview=...)` is the same entry.

After a re-bake, send a `reload_image` op so the open session picks up the new
pixels (see below), then rebuild `heightmap_terrain`.

### From Blender: the Terrain panel

The BobBlenderTools sidebar (View3D > N > BobBlenderTools) has a "Terrain" panel (renamed from
"Heightfield Terrain" in the 2026-07-20 UX redesign) with a Bake + Build Terrain button. It bakes in the tools venv (so Blender's own Python
does not need numpy or CuPy), reloads the image, and builds the terrain object in
place. Preview bakes at 256 for a fast look; turn it off to commit at full
resolution. The panel is part of the extension, so picking up a code change to it
means re-enabling the addon or restarting Blender, not Reload Builders.

Panel features:

- 2D preview: the baked heightfield PNG is shown top-down above the button, so you
  read height and drainage without orbiting the viewport. It refreshes each bake.
- Preset dropdown: pick a preset (`foothills`, `alpine`, `badlands`, `rolling`,
  `canyon`, `mesa`, `islands`) to populate the sliders in one click; `custom`
  leaves your values alone. The generation knobs are generated from the venv
  presets into `presets.json` (see `tools/scripts/gen_panel_presets.py`), so the
  two stay in sync; the panel adds a display height and sea level per preset.
- Collapsible Shape / Erosion / Displace sub-panels group the knobs.
- Backend: `auto` (GPU when present, else CPU), `gpu`, or `cpu`. The question-mark
  button probes the venv and shows what is available; a bake that falls back to CPU
  reports a warning.
- Material: a real material picker; the chosen material is assigned to the surface.
- The bake shows a wait cursor and progress while it runs.

When Blender is launched through Steam it runs inside the Steam pressure-vessel
container, where the host venv and CUDA are not directly reachable. The operator
detects this and runs the bake on the host via `steam-runtime-launch-client
--alongside-steam`. Launching Blender directly (not via Steam) uses the venv
python straight. If the launcher is unavailable, the panel says so.

### Displacing it in Blender (recipe: `heightmap_terrain`)

| Param | Default | Live | What it does |
|-------|---------|------|--------------|
| `heightmap` | required | no | Absolute path to the heightmap image. |
| `size` | 60 | yes | Grid width in metres. |
| `resolution` | 512 | yes | Grid subdivisions. Match or exceed the heightmap for full detail. |
| `height` | 14 | yes | Vertical scale. |
| `sea_level` | 0.3 | yes | Height value mapped to z = 0. |
| `material` | none | no | Name of a material to assign to the surface. |
| `path` | none | no | Name of a curve object to grade a level trail along. |
| `path_width` | 2.4 | yes | Half-width in metres graded fully flat along the trail. |
| `path_falloff` | 3.5 | yes | Metres over which the grade eases back to natural terrain. |
| `path_depth` | 0.3 | yes | Metres to recess the trail below the sampled ground. |

### Path grading

With `path` set, the recipe levels a trail along the curve. For each grid point
it reads the draped curve's own height at the nearest curve vertex, then blends
the terrain toward that level within `path_width`, easing back over
`path_falloff`. The result is a graded bench that follows the ground up and down
but stays flat across its width, recessed by `path_depth`. The curve must be
draped (build it with `make_path` and a `heightmap`, see below) so its smooth Z
grades the trail; use a curve `resolution` of 64 or more to avoid terraced steps.
Pair it with a `path` on the scatter layers to clear vegetation off the trail.

## scatter (recipe: `scatter`)

A GScatter-style layer, one per asset type. How it works: reads the Emitter
object's surface, filters faces by slope, distributes points with Poisson
sampling, then instances a random pick from the Assets collection with random
scale and Z rotation. Trees can stand upright; rocks can tilt to the surface.

| Param | Default | Live | What it does |
|-------|---------|------|--------------|
| `emitter` | none | no | Name of the object to scatter on (usually the terrain). |
| `assets` | none | no | Name of the collection to instance. |
| `align` | `up` | no | `up` keeps instances upright (trees); `normal` tilts to the surface (rocks, grass). |
| `density` | 5.0 | yes | Points per square metre (before the distance limit). |
| `distance_min` | 0.3 | yes | Minimum spacing between instances. |
| `seed` | 0 | yes | Reshuffles positions, scale, rotation, and pick. |
| `min_scale` | 0.8 | yes | Smallest per-instance scale. |
| `max_scale` | 1.2 | yes | Largest per-instance scale. |
| `min_normal_z` | 0.5 | yes | Lower slope cutoff. 1 = flat only, 0 = any slope. |
| `max_normal_z` | 1.0 | yes | Upper slope cutoff. Below 1 excludes flats (scree on mid-slopes). |
| `height_min` / `height_max` | -1000 / 1000 | yes | Altitude band on world Z the layer scatters within. |
| `height_falloff` | 5.0 | yes | Metres the altitude band eases over at each edge. |
| `height_strength` | 0.0 | yes | Altitude mask mix. 0 = off, 1 = full. |
| `noise_scale` | 0.15 | yes | Frequency of the clumping noise. Lower = bigger patches. |
| `noise_contrast` | 0.5 | yes | Patch sharpness. 0 = smooth, 1 = hard-edged patches. |
| `noise_seed` | 0 | yes | Reshuffles the clumping pattern, independent of `seed`. |
| `noise_strength` | 0.0 | yes | Noise mask mix. 0 = off, 1 = full. |
| `vgroup` | none | no | Emitter vertex group whose weight paints where the layer scatters. |
| `paint_strength` | 1.0 | yes | Paint mask mix (only when `vgroup` is set). |
| `path` | none | no | Name of a curve object to clear the scatter along. |
| `path_width` | 3.0 | yes | Half-width in metres cleared fully (density 0) along the trail. |
| `path_falloff` | 3.0 | yes | Metres over which density eases back to full. |
| `camera` | none | no | Name of a camera; the layer culls scatter outside its view. |
| `camera_distance` | 80.0 | yes | Cull points beyond this distance from the camera. |
| `camera_cone` | 60.0 | yes | Half-angle (degrees) of the kept view cone; 180 = all around. |
| `cull_falloff` | 8.0 | yes | Metres the distance cull eases over. |

Masks: the slope band (`min_normal_z`/`max_normal_z`) drives the Poisson
Selection; every other mask multiplies into the Density Factor (a 0..1 field), so
they compose. Altitude and noise masks are always present and gated by their
`*_strength` (0 = no effect), so they cost nothing until used. The noise mask is a
cheap way to clump grass and plants into patches. The paint mask reads an emitter
vertex group through Object Info, so weight-painting the emitter authors exactly
where a layer grows; because it names a group it is a build-time param, so set the
group and press Build.

Path clearing: with `path` set, a distance-from-curve mask drives the Density
Factor, so density falls to zero within `path_width` of the curve and eases back
over `path_falloff`. Give each layer its own width so trees pull back further than
the rocks and plants that edge the trail. Use the same curve on the
`heightmap_terrain` `path` input to grade the ground flat under the clearing.

Camera culling: with `camera` set, points beyond `camera_distance` or outside the
`camera_cone` forward cone drop out, cutting instance count for viewport and render
performance. It approximates the frustum (distance + cone, not exact FOV) and
updates live as the camera moves, since it reads the camera through Object Info.
The camera is scene-wide (one for all layers) but the distance and cone are
per-layer, so grass can cull closer than trees.

Replacing assets: point `assets` at your own collection, or edit the contents of
the `BOB_Assets_<Kind>` collection the scatter already uses. Nothing in the graph
changes; the instances update.

### From Blender: the Scatter panel

The BobBlenderTools sidebar (View3D > N > BobBlenderTools) has a "Scatter" panel, a
GScatter-style multi-layer scatter UI over the `scatter` recipe. Unlike the
Heightfield panel it has no venv side: it drives `build_geonodes recipe=scatter`
in-process (no subprocess, no bake), so a code change to it needs an addon
re-enable, never an MCP reconnect.

It is object-native: each layer is an object in a per-emitter scatter collection
(`Object.bbt_scatter_coll`), so the layers are the scene objects, not a parallel
list, and multi-emitter works by construction. Structural config (`kind`, `assets`,
`align`) lives on the layer object (`Object.bbt_scatter_layer`); the numeric knobs
live on the layer modifier's inputs. Two homes, no drift.

- Emitter + path: pick the emitter mesh (or "Use Active"), optionally a curve to
  clear a trail through every layer.
- Layers: a list of the emitter's layers with a kind icon and a hide toggle. Add is
  a dropdown of types (Trees, Rocks, Plants, Grass, Empty); each seeds the align and
  knobs and points `assets` at `BOB_Assets_<Kind>`, making the proxies if needed.
  Remove and Duplicate (a copy with its own node group) sit beside it.
- Live knobs: the Active Layer sub-panel draws Density, Distance Min, Seed, scale
  range, and the slope band straight from the modifier inputs, so editing one
  updates the scatter live with no rebuild. Path Width / Falloff appear when a path
  is set. Randomize Seed reshuffles the active layer.
- Masks sub-panel: Altitude (a world-Z band) and Noise (procedural clumping) masks,
  each gated by a strength slider (0 = off), plus Paint Strength when the layer has
  a mask vertex group set. All are live.
- Camera Cull sub-panel: with a camera picked on the Scatter panel, per-layer
  Camera Distance / Cone / Falloff cull scatter outside the view for performance.
- Structural edits (`assets`, `align`, mask group, path/camera presence) apply on
  Build This Layer / Build All, a non-destructive rebuild that preserves the tuned
  live knobs.
- Make Proxies creates the block-out `BOB_Assets_*` collections so a scatter works
  before real assets are in.

Live-knob mechanism (Blender 5.2): a Nodes modifier's live input value is
`mod.properties.inputs.<identifier>.value`, not the node-group interface
`default_value` (which only seeds a fresh bind and does not re-evaluate when
edited). The panel binds each knob to that input, and `build_geonodes` snapshots
and restores the same surface across a rebuild.

## volumetrics (recipe: `volumetrics`)

Procedural Cycles volumes, with three modes selected by the `mode` param: `clouds`,
`height_fog`, and `noise_fog`. Every mode builds ONE bounded domain box for the
whole layer and lets a thin volume material carve the volume out of it (no field of
instanced cubes, which showed box seams). The Build ops set the domain object's
viewport `display_type` to `WIRE`, so it draws as a wireframe box (like an Unreal
volume's bounds) rather than a solid box while authoring. WIRE, not BOUNDS: the box
is generated by the GN modifier, so the object's bounding box (what BOUNDS draws) is
the empty base mesh and not true to the volume size, whereas WIRE draws the
evaluated geometry and matches the real domain. It is viewport-only and does not
affect the render, and the volume material has no surface output so the box never
renders as a surface anyway. The mesh itself must stay real: Cycles renders a volume
only inside a closed mesh. How clouds work: ONE domain box spans the
whole layer (built as a
single instanced cube at `height`), and the material carves the clouds out of it.
A cached thin Principled Volume (`materials.py`, `BOB_CloudVolume`) samples 3D
fractal noise in world space and thresholds it by Coverage, so there is cloud where
the noise clears the threshold and open sky elsewhere, with no box seams. The density
fades to zero toward every face of the box (a soft envelope on the box's Generated
coordinates), so the cloud never cuts off at the bound. The box is instanced once so
the live knobs reach the volume shader as INSTANCER attributes. Driven from the
Firmament panel's Clouds sub-panel.

| Param | Default | Live | What it does |
|-------|---------|------|--------------|
| `size` | 400 | yes | Layer footprint in metres (the box XY). |
| `thickness` | 40 | yes | Layer depth in metres (the box Z). |
| `height` | 70 | yes | World Z of the layer centre. |
| `coverage` | 0.5 | yes | How much of the sky fills with cloud (noise threshold). |
| `cloud_scale` | 0.06 | yes | Noise frequency: lower = bigger cloud masses. |
| `cloud_seed` | 0 | yes | Reshuffles the cloud pattern. |
| `density` | 5.0 | yes | Volume density (opacity/brightness of the cloud). |
| `detail` | 5.0 | yes | Noise octaves for the cloud interior. |
| `softness` | 0.25 | yes | Widens the density threshold; softer, wispier edges. |
| `warp` | 0.4 | yes | Domain warp: billowy organic shapes instead of round blobs. |
| `wind` | off | yes | Toggle: drift the clouds through the box by scene time. |
| `wind_direction` | 0 | yes | Compass direction (degrees) the clouds drift toward. |
| `wind_speed` | 2.0 | yes | Drift rate in metres per second (seeded from env wind). |

Self-shadowing gives the clouds their dimensional form (bright tops, dark
undersides) and is ON by default: the Build Clouds op leaves the cloud object's
`visible_shadow` on. It is cheap at a normal sun height (shadow rays only cross the
layer thickness), so it costs little; turn the Cloud Shadows toggle off for a flat,
faster look when the sun is low and the frame is Final quality, where near-horizontal
shadow rays cross the whole layer. Build Clouds also sets the Cycles Volume Step
Rate, Max Steps, and Volume Bounces from a Preview/Final quality level (bounces 0/2,
so shadowed cloud reads bright on finals). `coverage` runs from sparse wisps near 0
to a full overcast near 0.85. `warp` domain-warps the noise so the clouds billow
organically instead of reading as round blobs. The panel has a Preset menu (Clear,
Scattered, Cumulus, Overcast, Storm) that sets the look knobs in one pick.

Wind: with the Wind toggle on, the recipe advances an offset by `wind_speed` * scene
time along `wind_direction` and stores it per instance; the material shifts its noise
sample by that offset, so the cloud pattern drifts through the stationary box (clouds
cross the sky, the box and its face envelope stay put). Scene time, not wall clock,
drives it, so a Cycles animation renders the same every time. The Firmament Clouds
panel has a Wind Drift toggle plus a Use Env Wind button that copies the Environment
(`bbt_env`) wind onto the clouds. The panel also groups the knobs (Shape, Layer, Wind)
and has a Randomize Cloud Seed button.

Live Environment (S5; toggle moved to the World panel in the 2026-07-20 redesign): the one
Live Environment master (default on, on `bbt_world`) drives Wind Direction / Wind Speed of the
clouds, fog, and particulates, the
cloud layer's Coverage (from `env.cloud_cover`), and the snow-coverage Snow input (from
`env.snow`) live from `bbt_env` with drivers, so moving an Environment slider moves every
effect with no rebuild. The per-object Use Env Wind / Use Env Snow buttons are shown only
when the toggle is OFF (when on, the driver owns the input and a one-time copy would be
overwritten). The drivers are reinstalled on each build, since the non-destructive rebuild
regenerates the node-group socket identifiers.

### Fog (`volumetrics` modes `height_fog`, `noise_fog`, `ground_fog`)

Bounded domain fog, the aerial-perspective path the S1 uniform world haze could not
give (a world volume has infinite optical depth and blacks the frame; a bounded box
does not). The same one-box pattern as clouds, but with its own material (a Principled
Volume). Density is a height profile (raised to a Falloff power) times a noise
modulation times an XY-wall envelope, all scaled by Density, with Fog Color tinting
the scattering albedo and Anisotropy (forward scattering) driving the sun-side glow
and light shafts. The noise gets a domain warp (Warp knob) so banks billow
organically. The XY-wall envelope fades only the four side walls; the height profile
handles the top fade.

Three modes:

- `height_fog` (material `BOB_FogVolume`): the height profile is box-relative, read
  from the box's own Generated Z, densest at the box bottom and fading to zero at Fog
  Top (a fraction of the box height). Density falls with world height (aerial
  perspective), and because the profile is anchored to the box at a fixed world Z,
  valleys below the fog top fill and hills poke out: crude terrain-aware pooling with
  no terrain sampling. Fog Noise defaults low (a near-uniform slab).
- `noise_fog` (same `BOB_FogVolume`): identical graph, Fog Noise defaults high so the
  slab breaks into soft patchy banks, with a lower, thicker default box.
- `ground_fog` (per-image material `BOB_GroundFog_<image>`): the height profile is
  terrain-relative. The material samples the heightmap by world XY (UV = xy / Terrain
  Size + 0.5), reconstructs the terrain height `(sample - Sea Level) * Terrain Height`
  the same way `heightmap_terrain` does, and fades density with height above that
  ground over Ground Thickness metres. So the mist hugs the surface and follows hills
  up and over, instead of sitting at a fixed Z. The material is cached per heightmap
  image (an image is a node property, not a socket), so two fogs over the same terrain
  share it while the terrain mapping stays live through knobs. Without a heightmap it
  falls back to `BOB_FogVolume`.

height_fog and noise_fog share `BOB_FogVolume` and differ only in default Fog Noise;
`mode` picks fresh-build defaults. Because the rebuild is non-destructive, switching
mode on an existing fog object keeps the tuned knobs; use the Fog Preset menu (Ground
Mist, Valley Fog, Fog Banks, Thick Fog) to change the live look, as with clouds.

| Param | Default | Live | What it does |
|-------|---------|------|--------------|
| `size` | 400 | yes | Layer footprint in metres (the box XY). |
| `thickness` | 40 / 60 / 60 | yes | Box depth in metres (height / noise / ground defaults). |
| `height` | 20 / 30 / 15 | yes | World Z of the box centre (height / noise / ground defaults). |
| `density` | 2.0 | yes | How thick the fog reads. An artist scale, not raw extinction: ~1 light, ~3 moderate, ~6 thick. |
| `fog_top` | 0.6 | yes | Fraction of the box height the fog rises to (height/noise modes). |
| `falloff` | 1.5 | yes | Power on the height curve: >1 hugs the ground tighter, <1 fills evenly. |
| `fog_noise` | 0.15 / 0.85 / 0.25 | yes | 0 = smooth, 1 = patchy banks. Low height, high noise, low-mid ground. |
| `fog_scale` | 0.03 | yes | Noise frequency: lower = bigger banks. |
| `fog_detail` | 4.0 | yes | Noise octaves for the fog. |
| `fog_seed` | 0 | yes | Reshuffles the fog pattern. |
| `softness` | 0.3 | yes | Widens the bank edges (noise threshold width). |
| `warp` | 0.3 | yes | Domain warp: banks billow organically instead of round patches. |
| `color` | white | yes | Fog Color, the scattering tint (cool shadow / warm dawn). |
| `anisotropy` | 0.4 | yes | Forward scattering (-0.9..0.9): the sun-side glow and light shafts. |
| `wind` / `wind_direction` / `wind_speed` | off / 0 / 2 | yes | Drift the banks by scene time, seeded from env wind. |
| `heightmap` | none | no | ground_fog only: terrain image path the mist drapes over (build-time). |
| `terrain_size` / `terrain_height` / `sea_level` | 60 / 14 / 0.3 | yes | ground_fog only: match the `heightmap_terrain` build that used the image. |
| `ground_thickness` | 8.0 | yes | ground_fog only: how high (metres) the mist clings above the terrain. |

Build Fog sets the Cycles Volume Step Rate, Max Steps, and Volume Bounces from the
Preview/Final quality level, the same as Build Clouds. The Firmament Fog sub-panel
picks the mode (and a heightmap for ground fog), builds, applies a preset, and draws
the live knobs grouped (Shape, Look, Layer, Terrain, Wind) with a Randomize Fog Seed
button and a Use Env Wind button. Fog is a viewed-from-outside effect: a camera
immersed deep in a dense slab sees only extinction (near-black), and a very dense
slab whites out over a long path.

Density note: fog fills the whole box (unlike clouds, which the Coverage threshold
mostly empties), so a large box builds high optical depth from a small extinction:
raw density around 0.05 already reads as a thick sea of fog. The Density knob is
therefore an artist-friendly scale (internally multiplied by 0.02 to reach the raw
extinction), so the usable range sits in friendly single digits: about 0.1 a faint
haze, 1 light, 3 moderate, 6 a thick soup. It is still exponential and
view-dependent (a grazing horizon path fogs out sooner than looking down), so tune
against the actual shot.

## particulates (recipe: `particulates`)

Wind-driven weather particles in a camera-following domain, with two shape modes
selected by the `mode` param: `streak` (rain) and `mote` (dust, amber motes, falling
snow). Real instanced geometry, so the depsgraph instance count is exact (unlike the
volumes). Driven from the Firmament panel's Weather sub-panel.

How the motion works. Each particle has a stable random base position in a box, a
continuous world position `moved = base + velocity * scene time` (motes add a smooth
turbulence offset), and is then re-tiled to the copy nearest the camera:
`rep = moved - box * round((moved - camera) / box)`. Scene Time drives it, so a Cycles
animation renders the same every frame. Because `rep` is anchored to the particle's own
world position, its motion-blur velocity is the true particle velocity, not the camera's:
the domain follows the camera with no domain-jump streak (do NOT snap the follow-centre to
the box lattice; that concentrates the jump into occasional all-particle streak frames
instead of removing it). Streaks are real geometry: a thin tapered cone (needle) aligned
to the velocity vector, so wind leans the streak, with the rain material holding the streak
core opaque and fading only the tips (a soft needle, not a hard-capped tube); motes are
small ico spheres, scene-lit with a Translucent term mixed in so dust and amber motes glow
when backlit by a low sun (the golden-hour catch). The camera is a build-time param (set on
the Object Info node); with no camera the domain sits at the origin.

| Param | Default | Live | What it does |
|-------|---------|------|--------------|
| `mode` | `streak` | no | `streak` (rain) or `mote` (dust/amber/snow). Build-time. |
| `camera` | none | no | Object name the domain re-tiles around. Build-time. |
| `count` | 2000 | yes | Number of particles. |
| `domain_size` | 40 | yes | Domain footprint in metres (XY). |
| `domain_height` | 40 | yes | Domain depth in metres (Z). |
| `fall_speed` | 9 / 0.4 | yes | Downward speed (m/s); streak / mote defaults. |
| `drift` | 1.0 | yes | Horizontal wind multiplier. |
| `size` | 0.010 / 0.03 | yes | Instance radius (streak cone / mote). |
| `size_variation` | 0.4 | yes | Per-particle random scale spread. |
| `streak_length` | 0.2 | yes | streak only: length = Fall Speed * this. |
| `turbulence` | 1.0 | yes | mote only: swirl/flutter strength. |
| `emission` | 0.0 | yes | mote only: emission strength (0 = scene-lit; raise for fireflies/embers). |
| `color` | blue-grey / white | yes | Streak or mote colour (INSTANCER knob). |
| `wind_direction` | 0 | yes | Compass direction (degrees) the wind blows toward. |
| `wind_speed` | 2.0 | yes | Wind speed (seeded from env wind, or driven live). |
| `quality_scale` | 1.0 | yes | Multiplies Count from the Preview/Final level (preview 0.35, final 1.0); set by the panel quality toggle, not by hand. |
| `seed` | 0 | yes | Reshuffles the particle pattern. |

Motion blur: the Build ops set `scene.render.use_motion_blur` from a panel toggle and
also the object's `cycles.use_motion_blur`, so fast particles read as streaks in the
render; rain streaks are also real geometry, so they read with or without it. The Weather
panel groups the knobs, has Rain presets (Drizzle, Rain, Downpour) and Mote presets
(Dust, Amber Motes, Falling Snow), a Randomize Seed button, a Use Env Wind button
(live-syncs Wind Direction/Speed from the Environment), and a Camera picker for the
follow domain. The Preview/Final quality toggle scales the particle Count through a
Quality Scale input (preview 0.35, final 1.0) at every build and when the toggle flips,
so the viewport stays light and a final render restores the full count.

Main-panel controls (S5; relocated to the World panel in the 2026-07-20 redesign): the World
panel carries the Preview/Final quality level, the one Live Environment master, and the Scene
Preset menu (Clear Day, Golden Hour, Overcast, Storm, Foggy Dawn, Dust Storm, Winter) that sets
the `bbt_env` context and seeds each subsystem in one pick, building any that are missing. The
Environment
sub-panel has an Apply Season button that applies the current season's continuous state
(snow, wetness, temperature) and, for Winter, builds the falling snow and the
snow-coverage pass.

## snow (recipe: `snow`)

The GN-authored snow-coverage pass, the single source of snow coverage. It runs as a
modifier ON the terrain (after the terrain modifier, so it sees the displaced surface),
passes the geometry through unchanged, and writes a 0..1 `snow_cover` float attribute on
the points. Later the BobShaders surface snow material and the accumulation shell both
read that one attribute, so they never disagree. Attach it with
`build_geonodes_on_object(obj, "snow", "BOB_Snow", params)` (the Weather panel's Add Snow
Coverage button), which is non-destructive like `build_geonodes` and takes a `reset` flag.

    snow_cover = Snow * slope_mask(normal Z) * altitude_mask(world Z) * (1 - occlusion)

### The coverage formula (the one documented spot)

This is the single authoritative definition of the slope/altitude coverage. The GN pass
(`snow.py`) is the authority; a later BobShaders shader-side fallback (for plain
materials that carry no snow modifier) MUST match it exactly. Both masks are a MapRange
in SMOOTHSTEP interpolation (`blocks.py` `smooth_falloff`), and the two ease in opposite
directions, which is the easy thing to get wrong:

- `slope_mask` = smoothstep of `normal.Z` from `From Min = Slope Threshold - Slope Falloff`
  to `From Max = Slope Threshold`. So it is 0 below the low edge and reaches 1 AT the
  threshold (eases on the LOW side): snow holds on up-facing ground.
- `altitude_mask` = smoothstep of `world Z` from `From Min = Altitude` to
  `From Max = Altitude + Altitude Falloff`. So it starts at the altitude and reaches 1
  above it (eases on the HIGH side): snow holds on high ground.
- `occlusion` = `Occlusion * (upward Raycast hit within Occlusion Distance ? 1 : 0)`;
  `snow_cover` is multiplied by `(1 - occlusion)`. Every factor is clamped to 0..1, so
  `snow_cover in [0, 1]`.

Attribute contract for readers: a Store Named Attribute, name exactly `snow_cover`,
FLOAT, on the POINT (vertex) domain. Read it with an Attribute node (Geometry type).

| Param | Default | Live | What it does |
|-------|---------|------|--------------|
| `snow` | 0.5 | yes | Overall coverage amount (seeded from `bbt_env.snow`). |
| `slope_threshold` | 0.5 | yes | Normal Z above which snow sticks (1 = flat only). |
| `slope_falloff` | 0.2 | yes | Normal-Z range the slope mask eases over. |
| `altitude` | 0.0 | yes | World Z above which snow starts. |
| `altitude_falloff` | 5.0 | yes | Metres the altitude mask eases over. |
| `occlusion` | 0.0 | yes | Shelter term strength (crude upward raycast; 0 = off). |
| `occlusion_distance` | 2.0 | yes | Metres the upward occlusion ray travels. |

Slope and altitude are solid smoothsteps (snow holds on up-facing, high ground);
occlusion is a crude-but-real short upward Raycast against the same mesh (a hit means
something is directly above, so less snow), gated by the Occlusion knob and meant to
improve later. A heightfield has no overhangs, so occlusion has no effect there. The
falling-snow look is the `particulates` Falling Snow mote preset.

## snow_shell (recipe: `snow_shell`)

The snow accumulation shell (BobShaders S4): gives snow real thickness and silhouette, not
just a white shading. It runs as a modifier ON the surface AFTER the `snow` coverage pass
(so `snow_cover` exists), reads that same attribute, blurs it (Blur Attribute) for rounded
drifts, and displaces the surface along its normal by `snow_cover * Thickness`. Because it
reads the SAME attribute the surface material reads, the shell thickness and the material
whiteness line up by construction (the single-source rule). Attach it with
`build_geonodes_on_object(obj, "snow_shell", "BOB_SnowShell", params)` (the BobShaders
Weather panel's Add Snow Shell button), non-destructive like the snow pass. It adds a
Geometry INPUT socket, so it augments the object's own mesh.

| Param | Default | Live | What it does |
|-------|---------|------|--------------|
| `thickness` | 0.3 | yes | Metres the surface lifts along its normal at full coverage. |
| `smooth` | 3 | yes | Blur iterations on `snow_cover` before displacing (rounded drifts). |

## Path (op: `make_path`)

Authors a NURBS curve object for the scatter and terrain `path` inputs, so a
trail can be built through the pipeline rather than only by hand.

```json
{"op": "make_path", "name": "Forest_Path", "resolution": 96,
 "points": [[16,-30,0],[9,-18,0],[2,-8,0],[-3,2,0],[-6,12,0],[-14,22,0],[-22,30,0]],
 "heightmap": "/abs/forest_height.png", "size": 70, "height": 16, "sea_level": 0.30}
```

| Param | Default | What it does |
|-------|---------|--------------|
| `name` | `Path` | Curve object name. Re-running replaces a same-name curve. |
| `points` | [] | NURBS control points [x, y, z]. Z is ignored when draping. |
| `resolution` | 12 | Curve subdivisions. For grading use 64 or more, see below. |
| `heightmap` | none | Drape the control points onto this heightmap (see below). |
| `size` / `height` / `sea_level` | 60 / 14 / 0.3 | Must match the `heightmap_terrain` build so the drape sits on the surface. |

Draping: with `heightmap` given, each control point's Z is set to the terrain
surface height there, `(sample - sea_level) * height`. Because a NURBS curve has
few control points, the draped profile is smooth, so `heightmap_terrain` grades a
gently rising trail instead of copying the terrain's fine relief.

Resolution and terracing: the terrain grade reads the curve's height at the
nearest curve vertex, so a coarse curve leaves visible steps (the slope material
paints the risers as rock). Use `resolution` 64 to 96 for grading; the steps then
fall below the material's slope threshold. It does not matter for scatter-only
paths, which use horizontal distance, not the curve's Z.

## Proxies (op: `make_proxies`)

Block-out stand-ins so a scatter works before you bring real assets.

```json
{"op": "make_proxies", "kinds": ["trees", "rocks", "plants"]}
```

Creates collections `BOB_Assets_Trees`, `BOB_Assets_Rocks`, `BOB_Assets_Plants`
(a canopy-on-trunk tree, rocks, and shrubs, each with a simple material). The
collections are not linked to the scene, so the proxies appear only as scattered
instances. Replace them by editing a collection's contents.

## wave_grid (recipe: `wave_grid`)

A demo recipe: a grid whose Z ripples as sin(distance-from-centre * Frequency) *
Amplitude. Live params: Size, Resolution, Amplitude, Frequency.

## Adding or changing a recipe

Recipes live in `blender/bbmcp/geonodes/recipes/`, one file each, composed from
the shared blocks in `blocks.py`. See `blender/README.md` for the how-to.
