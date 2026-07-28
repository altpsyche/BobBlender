# Architecture

This repo is a central home for procedural Blender work, plus BobBlenderMCP, a
pipeline that lets an agent author Blender data over MCP. Target: Blender 5.2 LTS.

## Repo layout

- `projects/`: one folder per piece, started from `projects/_template/`.
- `library/`: reusable geometry-node and material node groups, registered as a
  Blender Asset Library. Agent output can land in `library/_generated/`
  (gitignored) and be appended into hand-edited files via the Asset Browser.
- `tools/`: a Python project (`bobtools`) that runs in its own venv, not inside
  Blender: repo utilities, the ComfyUI client, and a thin `bob-mcp` launcher
  (`bobtools/mcp_launch.py`) for in-repo dev. The MCP server itself moved into the
  extension (`mcp_agent/`) so it ships with the product and runs standalone; the terrain
  compute likewise lives in the extension at `core/heightfields/` (the single-compute
  rule: one committed source, never a venv copy), which the venv reaches via `bobtools/_hfpath.py` (puts the core dir on sys.path).
- `blender/`: code that runs inside Blender's Python. `extensions/bob_blender_tools/`
  is the BobBlenderTools addon (World, Terrain, Scatter, Shaders, Atmosphere, and a
  collapsed Advanced/Bridge panel; see the panel-UX section below). It is the whole
  shippable product: `core/` (authoring library), `ui/`, `bridge/` (the live socket),
  `mcp_agent/` (the agent-side MCP server), and `runners/` (headless entry scripts).
  See [MCP.md](MCP.md) for the agent setup and tool reference.
- `renders/`: outputs, gitignored.
- `config/`, `docs/`, `references/`.

Add-ons and extensions that are general tools (for example bob's-assembly) live
in their own repos and are dev-installed into Blender by symlink. They are not
vendored here.

## Two Python worlds

`bpy` only runs inside Blender's bundled interpreter (Python 3.13). The MCP
server and executors run in the `tools/.venv` (Python 3.14). They never import
each other; they communicate with JSON. Shared settings live in `bob.toml`,
which both interpreters read.

## BobBlenderTools: one core, swappable executors

Both halves ship inside `blender/extensions/bob_blender_tools/`. The split is not repo folders; it
is which interpreter a module imports under. The agent side runs in whatever Python the MCP client
spawns (Pydantic, no `bpy`); the Blender side runs in Blender's bundled Python (`bpy`, no MCP).

```
agent side (no bpy)                        Blender side (bpy, no mcp)
  mcp_agent/contracts.py   op vocabulary     core/                builders
  mcp_agent/server.py      MCP tools         core/dispatch.py     op -> builder
  mcp_agent/executor.py    headless exec -->  runners/             headless entry
  mcp_agent/bridge.py      live exec     -->  bridge/server.py     the live socket
                                             core/heightfields/   terrain compute (single source)
                                             core/comfy{,_jobs,_maps,_ws}.py  the ComfyUI client

tools/ (dev venv only, in-repo convenience)
  bobtools/mcp_launch.py   `bob-mcp` launcher for the in-repo server
  bobtools/hf_cli.py       venv CLI for a bake (puts the sole compute copy on sys.path)
  bobtools/comfyui.py      re-export of core/comfy.py, never a second copy
```

Contracts are validated on the agent side, where agent input enters, and JSON crosses to Blender.

Same single-source rule as the terrain compute, for the same reason: the ComfyUI client is
stdlib only and lives in the extension, because Blender's bundled Python has no `httpx`.
`bobtools/comfyui.py` re-exports it rather than reimplementing it. Split by what
they own: `core/comfy.py` is the HTTP client, the title templating, preflight, and the texture-set
recipe; `core/comfy_jobs.py` is the scheduler (one worker thread, a `bpy.app.timers` tick draining
a result queue, every `bpy` touch on the main thread, the registry cleared by a `@persistent`
`load_post` handler); `core/comfy_maps.py` derives the texture maps in numpy; `core/comfy_ws.py` is a
minimal stdlib websocket reader for ComfyUI's `/ws`, which supplies per-node progress and NOTHING else
— `comfy.wait()` still decides a job is finished from the jobs API, so a socket that never connects
costs a progress bar and cannot cost a result. Shipped ComfyUI
graphs live in `extensions/bob_blender_tools/assets/workflows/`, in API format, bound by node
title, and every one of them is preflighted before it is queued so an uninstalled pack or a
missing model is a sentence rather than an HTTP 400. Generated output lands in
`<output>/packs/generated/`, staged under a `_staging/` sibling of `textures/` until it is
accepted.

Three more modules need `bpy`, and they are the half of the integration that hands ComfyUI geometry
instead of pixels. `core/gen_views.py` renders a beauty frame plus TRUE depth and normal passes
through a view-layer material override (Blender 5.2's compositor cannot hand a pass back to Python),
and renders the isolated flat-lit turntable the paint route needs. `core/gen_paint.py` projects the
restyled views back into one UV texture: a world position and normal per texel from the same triangle
raster the coverage measurement uses, a texel-space z-buffer for visibility, a normal-weighted blend,
and the cross-view seam and drift figures that are the paint route's gate.

`core/gen_assets.py` is the one that finishes an asset: ComfyUI generates a
mesh, Blender bakes it, scales it to a real height, puts the origin on the ground, builds the LOD
chain, converts it to a BobShader, and writes it into the generated pack with a provenance
sidecar. Which ComfyUI graphs produce that mesh is a value rather than a branch
(`comfy.asset_chain()` picks the route, `comfy.finish_passes()` maps what it staged onto the two
finish callbacks), so swapping the four-graph chain for the one-shot one was a config change; the
same shape carries the texturing decision (`comfy.texture_chain()`: native PBR or the stylised paint
route). The split matters for threading as much as for tidiness: the whole ComfyUI chain runs on
the worker in one job, and the Blender half runs once in that job's main-thread callback, so a
five-minute generation never blocks a frame. Full plan and measurements: `docs/GENERATION.md`.

- The op vocabulary (`contracts.py`) is validated where agent input enters, so
  the Blender side trusts clean JSON and needs no extra deps.
- The builders (`blender/extensions/bob_blender_tools/core/`) are the single place that knows how to build
  meshes, geometry nodes, and materials. Reused by both executors.
- The executor is swappable. `executor.py` spawns headless Blender for
  reproducible builds; `bridge.py` targets the open session for live work. Both
  present the same shape, so adding one did not change anything upstream.

To grow the vocabulary: add an op model in the extension's `mcp_agent/contracts.py`, a builder
in `blender/extensions/bob_blender_tools/core/`, and one line in `blender/extensions/bob_blender_tools/core/dispatch.py`. Geometry-node
builders are recipes in `blender/extensions/bob_blender_tools/core/geonodes/recipes/`.

## The BobBlenderTools extension

`blender/extensions/bob_blender_tools/` is the Bob suite's Blender-side host: one
addon, one `BobBlenderTools` N-panel tab, with the capabilities as sibling panels.
MCP is one capability here, not the roof.

Internally the three concerns are separated: `ui/` holds the panels (`world`,
`firmament`, `foliage`, `scatter`, `shaders`, `splines`, and shared `helpers`), `bridge/` holds the
live socket `server`, and `core/` is the builder library. A thin `__init__.py` does only
register/unregister, addon prefs, and the terrain-bake operators. Every intra-package
import is relative, so the folder is self-contained and importable under both the live
`bl_ext.*` namespace and the headless `bob_blender_tools.*` name.

### Asset packs

Art lives OUTSIDE the repo, in asset packs. A pack is a plain folder with
`models/<biome>/manifest.json` biomes and `textures/<set>/` texture sets (plus an optional
`pack.json` root manifest: id/name/version/provides). `core/assets.py` resolves biomes and
texture sets over an ordered search path, `asset_roots()`, first hit wins: (1) the
`$BOB_ASSET_PACKS` env list, (2) the addon-preference **Asset Pack Folders**, (3) the dev repo
`library/` when running in-repo, (4) the block-out pack bundled inside the extension
(`assets/models/blockout/`) as the always-present floor. `assets.py` stays bpy-free (it is read
by the panels, never imports them); the preference folders are pushed in from the addon via
`set_pref_roots()`. **Rescan Asset Packs** (Advanced panel) re-reads the folders and refreshes
the biome enums. The extension zip ships only the block-out; real packs are separate downloads a
user points the preference at, so the repo and the zip stay lean and `library/` can later move to
its own repo without touching extension code.

Generated data (baked heightfields, drainage maps) writes to a resolved **Output Folder**
(`_output_dir()`): the addon-preference path if set, else beside the saved `.blend`, else a
per-user extension cache. The free-text bake target is `basename`-guarded so it cannot escape
that folder. This replaces the old hardcoded `library/_generated/`, which does not exist on an
installed machine.

### Compute delivery

The terrain bake runs the single-source compute (`core/heightfields`) IN-PROCESS. That compute
needs scipy (CPU) and CuPy (GPU), which Blender's bundled Python does not ship. `compute.py`
(bpy-free detection + a pip subprocess into Blender's own Python) plus the **Enable Compute**
operator install them on the user's request: it probes the GPU/CUDA line (nvidia-smi), installs
scipy and the matching `cupy-cudaXXx` wheel into Blender's Python, and verifies a real device
round-trip. `auto` then picks the GPU with no toggle; a startup probe steers the artist to Enable
Compute when a GPU is present and the deps are missing. If in-process compute fails for ANY reason
— deps absent, or CuPy imports but its CUDA/NVRTC libs are unreachable (a Steam pressure-vessel
sandbox hides system CUDA, and the CUDA-13 pip wheels are not yet published, so cupy-cuda13x cannot
JIT there) — the bake falls back to the dev venv by subprocess, which hops to the host via the
Steam launcher where CUDA works. `verify_gpu()` JIT-compiles a kernel (not just a reduction) so
Enable Compute reports the real in-Blender GPU state. AMD/ROCm is CPU-only for now. Standalone
artists on CUDA 13 + Steam without a venv get CPU until the cu13 CUDA wheels ship.

### Panel UX (2026-07-20 redesign)

The tab is ordered along the pipeline so the N-panel teaches the workflow (full plan and
rationale in `CONVENTIONS.md`, panel UX conventions): **World** (the shared `bbt_env`, promoted out of Firmament),
**Biome** (the one-action way to stand up a whole scene), **Terrain**, **Paths** (BobSplines
typed curves), **Scatter**, **Foliage** (BobFoliage, authoring right after the Scatter stage that
routes there), **Shaders**, **Atmosphere** (Firmament's built sky/clouds/fog/weather), and a
collapsed **Advanced** panel (the MCP Bridge, demoted from the artist's entry point). Order is set
by `bl_order`, not registration (World 0, Biome 1, Terrain 2, Paths 3, Scatter 4, Foliage 5,
Shaders 6, Atmosphere 7, Advanced 8), and every value is unique because a tie falls back to
registration order; a one-line overview at the top of World names the sequence.

Suite-wide principles, implemented once in `ui/helpers.py` (a context-header helper, a
structural-action marker with a shared icon + "rebuilds:" note, and a preset row):

- Native identity. Each panel reflects the active thing, not a panel-local pointer/name.
  Shaders edits the active object's active material slot (detected as a BobShader via
  `materials.master_type`); Scatter reflects the active emitter/layer; Terrain the target
  object. No `material_name`/`target`.
- One world, one place. The world master toggle (**Live Environment**) and the scene
  **Quality** level live on World (`bbt_world`). A SUBSCRIBER REGISTRY in `ui/world.py`
  lets each consumer register an applier `fn(scene)`; a world change re-applies all. World
  never imports its consumers, so `env.py` stays the acyclic root and a new world-driven
  subsystem is one `register_applier()` call. This folds the old per-panel `live_env` toggles
  (Shaders + Firmament) into one.
- Clear homes. Asset selection/import (Make Proxies, Import Biome) lives in Scatter;
  converting a material to a BobShader lives in Shaders (Convert, with an active/selected/
  collection scope for the unlinked scatter-asset collections).

Its MCP Bridge runs a local socket server, applying ops on Blender's main thread
through a timer (the only safe way to mutate `bpy` from a socket) and running only
whitelisted `core` ops. It has start/stop/status, autostart on launch, a Reload
Builders button (needed because Blender caches imports, so new builder code
requires a purge), and a clean stop so it can restart. Dev-installed by
`bob-setup`.

Two-sided reload for op changes. The builder code runs in Blender; the op
contract (`contracts.py`) runs in the long-running venv MCP server, which parses
it once at startup. So changing an existing recipe body needs only Reload
Builders, but adding or changing an op's contract also needs the MCP server
reconnected (`/mcp reconnect bobblendermcp`, or restart the CLI). Restarting
Blender alone does not reload the agent-side contract, `build_live` will reject
the new op tag until the server is reconnected.

## Firmament: the atmosphere capability

A sibling capability panel (labelled **Atmosphere** in the tab since the 2026-07-20 UX
redesign; the module is `ui/firmament.py`) that authors sky, clouds, fog, weather
particulates, and snow coverage. It still owns and registers the shared world state, but the
world's UI (the Environment sliders) now lives in the World panel, and the scene Quality level
and the Live Environment master moved to `bbt_world` (see the panel-UX section).
See `FIRMAMENT.md` for the design.
It is the base layer other capabilities read the world from, a one-way dependency, so
the graph stays acyclic and a `BobBlenderFirmament` split stays mechanical. All bpy-only.

- `core/env.py`: `Scene.bbt_env`, the canonical world state (time_of_day, year/month/day,
  utc_offset, latitude, longitude, season, weather, temperature, wetness, snow, cloud_cover,
  wind_direction, wind_strength). BobFirmament registers it;
  every capability reads it through `get_env()` (None-guarded so a standalone caller
  falls back to its own defaults). `sun_params()` extracts the geographic-sun inputs.
- `core/solar.py`: pure-Python NOAA sun position (no bpy, unit-tested).
- `core/world.py`: `build_sky` op, a physical MULTIPLE_SCATTERING world shader plus a
  matched Sun light, positioned from the world state (no world haze). Authored on press,
  not a live-knob surface.
- `core/geonodes/recipes/volumetrics.py`: the `volumetrics` recipe (clouds, height_fog,
  noise_fog, ground_fog) built through `build_geonodes`; one bounded domain box per layer
  with a thin volume material carving the volume. `particulates.py`: rain streaks and
  dust/amber/snow motes in a camera-following domain. `snow.py`: the GN coverage pass that
  writes the `snow_cover` attribute (the single source of snow coverage; consumed later by
  BobShaders). `core/materials/volumes.py` holds the cached volume/particulate shaders.
- Live-vs-structural, per the repo policy: continuous world values (wind, snow,
  cloud_cover) feed live via drivers on the GN modifier inputs under a Live Environment
  toggle, reinstalled on each build; a change of season is applied by an explicit Apply
  Season operator, never a property callback, to avoid the scatter rebuild re-entrancy.
- `Scene.bbt_firmament` holds Firmament's own subsystem state (sky/cloud/fog/weather object
  names and knobs). Since the redesign the Preview/Final quality level and the Live Environment
  master moved to `bbt_world`; whole-scene presets and Apply Season are drawn in the World panel
  (the operators stay `firmament_*`); the Environment sub-panel moved to World. The Atmosphere
  panel keeps the Sky / Clouds / Fog / Weather sub-panels. New ops (`build_sky`) meant one MCP
  reconnect; the recipes reuse `build_geonodes`, so recipe work is a Reload Builders, panel work
  an addon re-enable.

## BobShaders: the surface-materials capability

A sibling capability panel (peer to Firmament, Terrain, and Scatter) and the top of the
dependency graph: authored surface materials plus the shared, world-driven weather layer
that makes every surface obey `bbt_env`. It reads the world and the `snow_cover` attribute,
is applied to Terrain and Scatter output, and imports none of them. Panel-only and
in-process like Scatter (no MCP op, no reconnect). Built in slices; see `SHADERS.md` for
the design and slice records. All bpy-only, so a `BobBlenderShaders` split stays mechanical.

- `core/materials/` (a package split on coupling seams into `shared`, `volumes`, `weather`,
  `water`, `terrain`, `surface`, re-exported from its `__init__`) grows the artist-facing
  surface framework alongside the cached
  Firmament volume/particulate shaders: shared shader NODE GROUPS (`S_<Effect>`) that a
  thin per-object wrapper material (`M_<Surface>`) instances, the Blender-native master +
  instances shape. `S_EnvState` (the world-to-shader bridge, internal Value nodes
  driven once from `bbt_env`, one shared datablock feeding every material), `S_Weather`
  (the shared weather layer), `S_SurfaceMaster` (solid base colour +
  per-instance variation, ending in `S_Weather`), and `surface_material()` (the `M_<name>`
  wrapper: one master group node into one Principled BSDF). Cached and get-or-create, so a
  re-Build never wipes a wrapper's tuned inputs. `S_TerrainMaster` (the multi-layer
  blend) and `terrain_material()` join them. `S_WaterMaster` and `water_material()` are the third
  kind (see `WATER.md`): flowing, foaming, freezing water read from a curve ribbon's attributes.
  `master_type()` returns surface/terrain/water/None from the wrapper's Master group node, the
  native identity the panel keys off. The masters carry Albedo/Roughness/Metallic/AO Map input
  sockets (identity defaults, so a solid colour renders unchanged) that the Convert path routes a
  converted asset's own UV-mapped maps into; the AO socket is filled from the asset's arm map AO
  channel. Anti-tiling ships as `_macro_break` (a low-frequency world-noise macro brightness
  break-up, live `Macro Amount` / `Macro Scale`, `0` = off) plus per-instance Object Info Random.
  The texture-set sampler ships as `core/materials/texset.py`: one shared `S_TexSet` group,
  INSTANCED per layer, turns a `<pack>/textures/<set>/` set into the map values the masters already
  accept, so six textured layers cost the fold maths once rather than six times. The `Triplanar`
  toggle is Blender's own box projection on the image node, per material rather than per layer, and
  terrain projects from OBJECT coordinates because a GN grid has no UV layer. A layer with no set
  assigned is still a solid tint; a set whose maps do not resolve on disk is refused rather than
  reported as applied.
- Coverage has one authority: `S_Weather` reads the `snow_cover` attribute where the
  Firmament GN pass ran (the terrain) and computes a shader-side fallback with the exact
  SYSTEMS.md formula everywhere else (scattered assets, plain meshes). A per-material Use
  Attribute input picks; default computed for the surface master, attribute for the terrain.
- `S_TerrainMaster` blends an ordered stack of surface layers (one shared group,
  fixed 6 slots, the stack = the enabled ones) by the SAME masks Scatter uses (slope band,
  altitude band, world-space noise clumping, a per-layer paint attribute, and a Cycles
  Pointiness curvature term), with a HEIGHT-LERP blend so layers interlock instead of
  cross-fading, then hands the blended base to `S_Weather`. Reusing the scatter masks is the
  deliberate glue: rock texture and rock scatter agree on the same slopes.
- GN-generated meshes (terrain, heightmap_terrain) ignore the object's material slots, so
  `materials.assign_material` drives a small per-material Set-Material GN modifier
  (`BBT_Material`) at the end of the stack for any object carrying a Nodes modifier; a plain
  mesh just uses the object slot. This makes "assign a material to an object" work uniformly.
- The weather layer carries the full term stack: snow, wetness (with the documented
  `env.weather`->wetness mapping: rain/storm wet the ground), frost (below freezing, up-facing),
  and dust/moss season aging. `S_EnvState` drives four fields now (snow, wetness, temperature,
  weather). Wet-cavity pooling uses Cycles Pointiness (confirmed working). The snow accumulation
  shell (`geonodes/recipes/snow_shell.py`, attached by the BobShaders panel) displaces the
  surface by the same `snow_cover` for real thickness.
- Live vs structural, per the repo policy: continuous world values (snow, wetness, temperature,
  weather) feed the weather layer live via drivers on the shared `S_EnvState` group, reinstalled
  on New/Convert and gated by the one World master Live Environment toggle (`bbt_world.live_env`),
  which drives Shaders through the world applier registry (`ui/shaders._apply_world`). A change
  of kind (texture set, layer stack, dust/moss amount, season swap) is an explicit New/Convert/Apply.
- Scatter output weathers by converting the scatter asset collection's materials to BobShaders
  (Shaders' Convert with the Collection scope, since `BOB_Assets_*` is unlinked and not
  viewport-selectable); the instances inherit the converted material and Object Info Random
  varies each per instance. `core/assets.py` replaces the block-out proxies with real CC0 glTF
  models from one geographic scan location (`library/models/<biome>/manifest.json`) via
  **Scatter's Import Biome** (moved there from Shaders in the redesign), keeping the assets'
  native materials; GN instancing stores each mesh once so even a multi-million-poly scanned tree
  is memory-cheap to scatter. `materials.bobshade_material()` (Shaders' Convert) then converts
  each material in place - routing its Principled Base Color/Roughness/Metallic through
  `S_SurfaceMaster` (the asset's own maps feed the master's map inputs) so the assets gain
  per-instance variation and the full weather layer while their alpha and normals stay intact.
  One World control then weathers terrain and scattered assets alike; a Scene Preset (Winter)
  moves terrain, scatter, and props together with no rebuild. Measured budget: a 1080p Final
  full-stack frame (layered terrain + weathered scatter + clouds/fog/rain) is ~190s on the dev
  5080. The BobShaders core is complete.
- Biome system (docs/BIOMES.md; blockout rethink 2026-07-22): a biome folder
  (`library/models/<biome>/manifest.json`) is a self-describing scene, read through one normalizing
  reader `assets.biome_manifest()` that returns `{meta, models, terrain, scatter, world}` and maps a
  v1 flat manifest ({kind:[files]}) onto it (reserved keys meta/models/terrain/scatter/world; any
  other top-level list is a legacy model kind). `meta` carries attribution (`source`, `license`);
  `terrain` names each layer's preset plus an optional texture set, `scatter` is a per-kind recipe,
  `world` a `bbt_env` preset; `validate_biome()` flags the common authoring mistakes. The real-glTF
  import path (`import_gltf`, `populate_scatter_assets`, `biome_models`) and the `verdant_trail`
  biome were removed, so the `models` section is back-compat only and never loaded; a layer that
  names no texture set is a solid tint. The canonical biome is a procedural block-out (`library/models/blockout`,
  `meta.proxy=true`): proxy props from `core.proxies`, solid terrain, no external files. Whole-biome
  assembly is its own top-level **Biome** panel, driven by `world_apply_biome` (button "Build
  Biome"), which composes existing builders in order and stops on a cancelled step: terrain
  (**Biome Terrain**, Shaders), scatter (**Biome Scatter**, Scatter, a layer per kind from
  `scatter{}`), a weather-convert of the scattered assets (default on, each `BOB_Assets_<kind>` to
  BobShaders), and world (**Biome World**, sets `bbt_env` + Build Sky). Because those asset sources
  live in an unlinked, unselectable collection, their surface materials are edited through the
  selectable scatter LAYER object: selecting a layer lists its instanced assets' materials and the
  Surface / Weather sub-panels tune the chosen one, reaching every instance
  (docs/SCATTER.md). Names and conventions follow the UX redesign (ui/helpers, native
  context).
- `Scene.bbt_shaders` holds only BobShaders' own UI state (active terrain layer, the staged
  texture-set pick, the Convert scope + collection, and the scatter asset-material name). Identity
  is native: the material on the active object's active slot (no `material_name`/`target`/`master`
  enum), and a texture-set assignment is recorded on the MATERIAL (`bbt_texsets`,
  `bbt_texset_box`), not in this state, so a rebuild carries it forward. The panel
  (`ui/shaders.py`) lists every material slot of the active mesh with a per-row select and an
  adaptive New (empty) or Convert (plain), a Batch-convert (active/selected/collection) for the
  scatter assets, and the Surface / Terrain Layers (+ Layer Masks) / Weather sub-panels gated on
  `materials.master_type` of the active material. Registered after Firmament, which owns `bbt_env`; a `make_material` MCP
  op stays a commented stub in `dispatch.py`, added only if agent-over-MCP authoring is later wanted.

## Naming

The suite is branded BobBlenderTools; MCP, HeightFields, and Scatter are
capabilities under it, not the roof. Names that denote the umbrella use the Tools
brand; names that denote the MCP capability keep MCP, because they genuinely are
the MCP bus.

- Umbrella (Blender side): extension id `bob_blender_tools`, operators
  `bob_blender_tools.*`, classes `BBT_*`, N-panel tab `BobBlenderTools`, scene
  props `bbt_*`.
- MCP capability (kept MCP, correctly): the MCP server `bobblendermcp` (in
  `.mcp.json`), the agent-side package in the extension (`mcp_agent/`), and the live
  MCP Bridge panel.
- Builder library: `bob_blender_tools.core` (was `core`). It builds the whole
  suite (terrain, scatter, proxies, paths); the old MCP-flavoured name was a
  misnomer and is gone. As a namespaced subpackage of the extension it is
  collision-proof without needing a special top-level name, and every intra-package
  import is relative (`from .core... `), so no code lands on Blender's global
  sys.path.

Rule: anything that would land on Blender's global sys.path gets a collision-proof
name; the extension avoids the problem entirely by keeping its code inside its own
package and importing relatively.

## Version control

Git plus Git-LFS. `.blend`, textures, and volumes go through LFS
(`.gitattributes`). Renders and `_generated/` are gitignored as regenerable.

## Repo boundary (kept extract-ready)

BobBlenderMCP stays in this monorepo for now. The framework (the extension's
`mcp_agent/`: contracts, executor, bridge, server, plus the socket bridge and dispatch) is the bus;
compute capabilities (`core/heightfields/`, later a scatter package) ride over
it and stay separable. Everything is kept extract-ready so a piece can later split
into its own repo (BobBlenderMCP / BobBlenderHeightFields / BobBlenderScatter under
a BobBlenderTools umbrella) with `git subtree`. Extract when the op vocabulary
stabilises, or when a second project or a public release needs it.

The constraints that keep a split mechanical, and the gaps that are still open, are in
[ROADMAP.md](ROADMAP.md). That is the one place an unfinished thing is allowed to live, so no other
document has to hedge.

## Daily use

1. Enable the BobBlenderTools addon once, with autostart on.
2. Open a Claude Code session in this repo and approve the `bobblendermcp` MCP
   server (declared in `.mcp.json`).
3. Ask an agent to build. Results appear in the open viewport (`build_live`) or
   in a headless `.blend` (`build`).

Multiple agents each run their own MCP server process and connect to the one
bridge; the main-thread job queue serialises execution. If a shared broker is
ever needed, switch the MCP transport to HTTP.
