# Architecture

This repo is a central home for procedural Blender work, plus BobBlenderMCP, a
pipeline that lets an agent author Blender data over MCP. Target: Blender 5.2 LTS.

## Repo layout

- `projects/`: one folder per piece, started from `projects/_template/`.
- `library/`: reusable geometry-node and material node groups, registered as a
  Blender Asset Library. Agent output can land in `library/_generated/`
  (gitignored) and be appended into hand-edited files via the Asset Browser.
- `tools/`: a Python project (`bobtools`) that runs in its own venv, not inside
  Blender. `bobtools/mcp/` is the framework and bus (contracts, executors, bridge,
  MCP server); `bobtools/heightfields/` is the pure terrain-compute capability;
  repo utilities and the ComfyUI client sit alongside.
- `blender/`: code that runs inside Blender's Python. `bbmcp/` is the authoring
  library, `runners/` are headless entry scripts, `extensions/bob_blender_tools/`
  is the BobBlenderTools addon (World, Terrain, Scatter, Shaders, Atmosphere, and a
  collapsed Advanced/Bridge panel; see the panel-UX section below).
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

## BobBlenderMCP: one core, swappable executors

```
contracts (Pydantic ops, validated in the venv)
      |  JSON
      v
tools/bobtools (venv, no bpy)             blender/ (bpy, no mcp)
  mcp/contracts.py    op vocabulary         bbmcp/      builders
  mcp/executor.py     headless executor -->  runners/    headless entry
  mcp/bridge.py       live executor          extensions/bob_blender_tools  the addon
  mcp/mcp_server.py   MCP tools
  heightfields/       terrain compute
```

- The op vocabulary (`contracts.py`) is validated where agent input enters, so
  the Blender side trusts clean JSON and needs no extra deps.
- The builders (`blender/bbmcp/`) are the single place that knows how to build
  meshes, geometry nodes, and materials. Reused by both executors.
- The executor is swappable. `executor.py` spawns headless Blender for
  reproducible builds; `bridge.py` targets the open session for live work. Both
  present the same shape, so adding one did not change anything upstream.

To grow the vocabulary: add an op model in `bobtools/mcp/contracts.py`, a builder
in `blender/bbmcp/`, and one line in `blender/bbmcp/dispatch.py`. Geometry-node
builders are recipes in `blender/bbmcp/geonodes/recipes/`.

## The BobBlenderTools extension

`blender/extensions/bob_blender_tools/` is the Bob suite's Blender-side host: one
addon, one `BobBlenderTools` N-panel tab, with the capabilities as sibling panels.
MCP is one capability here, not the roof.

### Panel UX (2026-07-20 redesign)

The tab is ordered along the pipeline so the N-panel teaches the workflow (full plan and
rationale in `UX-REDESIGN.md`): **World** (the shared `bbt_env`, promoted out of Firmament)
then **Terrain**, **Scatter**, **Shaders**, **Atmosphere** (Firmament's built sky/clouds/fog/
weather), and a collapsed **Advanced** panel (the MCP Bridge, demoted from the artist's entry
point). Order is set by `bl_order`, not registration; a one-line overview at the top of World
names the sequence.

Suite-wide principles, implemented once in `ui_helpers.py` (a context-header helper, a
structural-action marker with a shared icon + "rebuilds:" note, and a preset row):

- Native identity. Each panel reflects the active thing, not a panel-local pointer/name.
  Shaders edits the active object's active material slot (detected as a BobShader via
  `materials.master_type`); Scatter reflects the active emitter/layer; Terrain the target
  object. No `material_name`/`target`.
- One world, one place. The world master toggle (**Live Environment**) and the scene
  **Quality** level live on World (`bbt_world`). A SUBSCRIBER REGISTRY in `world_panel.py`
  lets each consumer register an applier `fn(scene)`; a world change re-applies all. World
  never imports its consumers, so `env.py` stays the acyclic root and a new world-driven
  subsystem is one `register_applier()` call. This folds the old per-panel `live_env` toggles
  (Shaders + Firmament) into one.
- Clear homes. Asset selection/import (Make Proxies, Import Biome) lives in Scatter;
  converting a material to a BobShader lives in Shaders (Convert, with an active/selected/
  collection scope for the unlinked scatter-asset collections).

Its MCP Bridge runs a local socket server, applying ops on Blender's main thread
through a timer (the only safe way to mutate `bpy` from a socket) and running only
whitelisted `bbmcp` ops. It has start/stop/status, autostart on launch, a Reload
Builders button (needed because Blender caches imports, so new builder code
requires a purge), and a clean stop so it can restart. Dev-installed by
`bob-setup`.

Two-sided reload for op changes. The builder code runs in Blender; the op
contract (`contracts.py`) runs in the long-running venv MCP server, which parses
it once at startup. So changing an existing recipe body needs only Reload
Builders, but adding or changing an op's contract also needs the MCP server
reconnected (`/mcp reconnect bobblendermcp`, or restart the CLI). Restarting
Blender alone does not reload the tools-side contract, `build_live` will reject
the new op tag until the server is reconnected.

## Firmament: the atmosphere capability

A sibling capability panel (labelled **Atmosphere** in the tab since the 2026-07-20 UX
redesign; the module stays `firmament_panel.py`) that authors sky, clouds, fog, weather
particulates, and snow coverage. It still owns and registers the shared world state, but the
world's UI (the Environment sliders) now lives in the World panel, and the scene Quality level
and the Live Environment master moved to `bbt_world` (see the panel-UX section).
Built in slices S1-S5 (see `FIRMAMENT.md` for the design and the slice records).
It is the base layer other capabilities read the world from, a one-way dependency, so
the graph stays acyclic and a `BobBlenderFirmament` split stays mechanical. All bpy-only.

- `bbmcp/env.py`: `Scene.bbt_env`, the canonical world state (time, place, season,
  weather, wind, snow, cloud_cover, temperature, wetness). BobFirmament registers it;
  every capability reads it through `get_env()` (None-guarded so a standalone caller
  falls back to its own defaults). `sun_params()` extracts the geographic-sun inputs.
- `bbmcp/solar.py`: pure-Python NOAA sun position (no bpy, unit-tested).
- `bbmcp/world.py`: `build_sky` op, a physical MULTIPLE_SCATTERING world shader plus a
  matched Sun light, positioned from the world state (no world haze). Authored on press,
  not a live-knob surface.
- `bbmcp/geonodes/recipes/volumetrics.py`: the `volumetrics` recipe (clouds, height_fog,
  noise_fog, ground_fog) built through `build_geonodes`; one bounded domain box per layer
  with a thin volume material carving the volume. `particulates.py`: rain streaks and
  dust/amber/snow motes in a camera-following domain. `snow.py`: the GN coverage pass that
  writes the `snow_cover` attribute (the single source of snow coverage; consumed later by
  BobShaders). `bbmcp/materials.py` holds the cached volume/particulate shaders.
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

- `bbmcp/materials.py` grows the artist-facing surface framework alongside the cached
  Firmament volume/particulate shaders: shared shader NODE GROUPS (`S_<Effect>`) that a
  thin per-object wrapper material (`M_<Surface>`) instances, the Blender-native master +
  instances shape. As of S1: `S_EnvState` (the world-to-shader bridge, internal Value nodes
  driven once from `bbt_env`, one shared datablock feeding every material), `S_Weather`
  (the shared weather layer, snow term in S1), `S_SurfaceMaster` (solid base colour +
  per-instance variation, ending in `S_Weather`), and `surface_material()` (the `M_<name>`
  wrapper: one master group node -> one Principled BSDF). Cached and get-or-create, so a
  re-Build never wipes a wrapper's tuned inputs. As of S2, `S_TerrainMaster` (the multi-layer
  blend) and `terrain_material()` join them. As of S3, `texture_set_group()` builds a cached
  per-set group that triplanar-samples a `library/textures/<name>/` set (Blender's BOX
  projection, no UVs) and derives the normal from the height via a Bump node; the masters gained
  Albedo/Roughness/Metallic map inputs (identity defaults) so colour tints the map, and the
  wrapper rebuilds with an input snapshot when a set is assigned (keyed off a `bbt_sig`). The
  biome track (F1/F3, 2026-07-20) added: near/far anti-tiling in `texture_set_group` (a
  detail-scale sample blend + a low-frequency macro brightness break-up, live `Detail Blend` /
  `Macro Amount`, both `0` = the old single-scale look), and an `AO Map` input on `S_SurfaceMaster`
  (identity `1.0`) that Convert fills from the arm map's otherwise-unused AO (R) channel. Both are
  scoped so solid-colour materials render byte-identically (verified: 0.0 pixel delta).
- Coverage has one authority: `S_Weather` reads the `snow_cover` attribute where the
  Firmament GN pass ran (the terrain) and computes a shader-side fallback with the exact
  SYSTEMS.md formula everywhere else (scattered assets, plain meshes). A per-material Use
  Attribute input picks; default computed for the surface master, attribute for the terrain.
- As of S2, `S_TerrainMaster` blends an ordered stack of surface layers (one shared group,
  fixed 6 slots, the stack = the enabled ones) by the SAME masks Scatter uses (slope band,
  altitude band, world-space noise clumping, a per-layer paint attribute, and a Cycles
  Pointiness curvature term), with a HEIGHT-LERP blend so layers interlock instead of
  cross-fading, then hands the blended base to `S_Weather`. Reusing the scatter masks is the
  deliberate glue: rock texture and rock scatter agree on the same slopes.
- GN-generated meshes (terrain, heightmap_terrain) ignore the object's material slots, so
  `materials.assign_material` drives a small per-material Set-Material GN modifier
  (`BBT_Material`) at the end of the stack for any object carrying a Nodes modifier; a plain
  mesh just uses the object slot. This makes "assign a material to an object" work uniformly.
- As of S4 the weather layer carries the full term stack: snow, wetness (with the documented
  `env.weather`->wetness mapping: rain/storm wet the ground), frost (below freezing, up-facing),
  and dust/moss season aging. `S_EnvState` drives four fields now (snow, wetness, temperature,
  weather). Wet-cavity pooling uses Cycles Pointiness (confirmed working). The snow accumulation
  shell (`geonodes/recipes/snow_shell.py`, attached by the BobShaders panel) displaces the
  surface by the same `snow_cover` for real thickness.
- Live vs structural, per the repo policy: continuous world values (snow, wetness, temperature,
  weather) feed the weather layer live via drivers on the shared `S_EnvState` group, reinstalled
  on New/Convert and gated by the one World master Live Environment toggle (`bbt_world.live_env`),
  which drives Shaders through the world applier registry (`shaders_panel._apply_world`). A change
  of kind (texture set, layer stack, dust/moss amount, season swap) is an explicit New/Convert/Apply.
- Scatter output weathers by converting the scatter asset collection's materials to BobShaders
  (Shaders' Convert with the Collection scope, since `BOB_Assets_*` is unlinked and not
  viewport-selectable); the instances inherit the converted material and Object Info Random
  varies each per instance. `bbmcp/assets.py` replaces the block-out proxies with real CC0 glTF
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
  5080. BobShaders core is S1-S5 complete.
- Biome system (manifest v2, 2026-07-20): a biome folder (`library/models/<biome>/manifest.json`)
  is a self-describing scene, read through one normalizing reader `assets.biome_manifest()` that
  returns `{meta, models, terrain, scatter, world}` and maps a v1 flat manifest ({kind:[files]})
  onto it (reserved keys meta/models/terrain/scatter/world; any other top-level list is a legacy
  model kind). `meta` carries attribution (also a per-biome `CREDITS.md` + per-model `SOURCE.txt`);
  `models` entries are a file string or `{file, scale?, rotation?, max_polys?}` honoured by
  `import_gltf` (rotation/scale bake after the glTF transform; `max_polys` collapse-decimates via
  the evaluated depsgraph, e.g. verdant_trail's tree 2.06M -> 180k; assets without `max_polys` are
  never decimated); `scatter` is a per-kind recipe; `world` a `bbt_env` preset. `validate_biome()`
  flags the common authoring mistakes (missing file, bad layer key, missing texture set, scatter
  kind with no models, unknown world field). Four panel actions compose existing builders: Import
  Biome + **Biome Terrain** (Shaders), **Biome Scatter** (Scatter, a layer per kind from
  `scatter{}`), **Biome World** (World, sets `bbt_env` + Build Sky), and **Apply Biome** (World's
  "Set up a look": import -> terrain -> scatter -> world on the Scatter emitter / active mesh, one
  coherent scene). Apply Biome also weathers the scattered assets by default (a `weather_assets`
  toggle), converting each `BOB_Assets_<kind>` to BobShaders. Because those asset sources live in an
  unlinked, unselectable collection, their surface materials are edited through the selectable
  scatter LAYER object: selecting a layer lists its instanced assets' materials and the Surface /
  Weather sub-panels tune the chosen one, reaching every instance (docs/SCATTER-SHADING-UX.md).
  Names and conventions follow the UX redesign (ui_helpers, native context).
- `Scene.bbt_shaders` holds only BobShaders' own UI state (active terrain layer, the texture-set
  pickers, and the Convert scope + collection). Identity is native: the material on the active
  object's active slot (no `material_name`/`target`/`master` enum). The panel (`shaders_panel.py`)
  lists every material slot of the active mesh with a per-row select and an adaptive New (empty)
  or Convert (plain), a Batch-convert (active/selected/collection) for the scatter assets, and the
  Surface / Terrain Layers (+ Layer Masks) / Weather sub-panels gated on `materials.master_type`
  of the active material. Registered after Firmament, which owns `bbt_env`; a `make_material` MCP
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
  `.mcp.json`), the venv package `bobtools/mcp/`, and the live MCP Bridge panel.
- Builder library: the sys.path module `bbmcp` (not `bob_build`). It builds the
  whole suite (terrain, scatter, proxies, paths), so the MCP-flavoured name is a
  legacy misnomer kept for now; rename deferred to the polyrepo extraction.

Rule: anything that lands on Blender's global sys.path gets a collision-proof
name.

## Version control

Git plus Git-LFS. `.blend`, textures, and volumes go through LFS
(`.gitattributes`). Renders and `_generated/` are gitignored as regenerable.

## Repo boundary (kept extract-ready)

BobBlenderMCP stays in this monorepo for now. The framework (`bobtools/mcp/`:
contracts, executor, bridge, server, plus the extension and dispatch) is the bus;
compute capabilities (`bobtools/heightfields/`, later a scatter package) ride over
it and stay separable. Everything is kept extract-ready so a piece can later split
into its own repo (BobBlenderMCP / BobBlenderHeightFields / BobBlenderScatter under
a BobBlenderTools umbrella) with `git subtree`. Extract when the op vocabulary
stabilises, or when a second project or a public release needs it.

## Daily use

1. Enable the BobBlenderTools addon once, with autostart on.
2. Open a Claude Code session in this repo and approve the `bobblendermcp` MCP
   server (declared in `.mcp.json`).
3. Ask an agent to build. Results appear in the open viewport (`build_live`) or
   in a headless `.blend` (`build`).

Multiple agents each run their own MCP server process and connect to the one
bridge; the main-thread job queue serialises execution. If a shared broker is
ever needed, switch the MCP transport to HTTP.
