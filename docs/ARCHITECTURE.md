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
  is the BobBlenderTools addon (MCP bridge, heightfield panel, next scatter).
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
addon, one `BobBlenderTools` N-panel tab, with the capabilities as sibling panels
(MCP Bridge, Heightfield Terrain, and next Scatter). MCP is one capability here,
not the roof.

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

A sibling capability panel (peer to Heightfield Terrain and Scatter) that authors
sky, clouds, fog, weather particulates, and snow coverage, and owns the shared world
state. Built in slices S1-S5 (see `FIRMAMENT.md` for the design and the slice records).
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
- `Scene.bbt_firmament` holds Firmament's own UI/subsystem state and the Preview/Final
  quality level. The panel (`firmament_panel.py`) carries whole-scene presets, Apply
  Season, the quality toggle, and the Environment / Sky / Clouds / Fog / Weather
  sub-panels. New ops (`build_sky`) meant one MCP reconnect; the recipes reuse
  `build_geonodes`, so recipe work is a Reload Builders, panel work an addon re-enable.

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
  blend) and `terrain_material()` join them; `S_TextureSet` and the triplanar/anti-tiling
  helpers arrive at S3.
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
- Live vs structural, per the repo policy: continuous world values (snow) feed the weather
  layer live via drivers on the shared `S_EnvState` group, reinstalled on Build and gated by
  BobShaders' own `bbt_shaders.live_env` toggle (it reads the world state, not Firmament's
  UI). A change of kind (texture set, layer stack, season swap) is an explicit Build/Apply.
- `Scene.bbt_shaders` holds BobShaders' own UI state (target, material name, master type,
  active terrain layer, Live Environment). The panel (`shaders_panel.py`) carries
  Build/Assign/Use-Active, presets, and the Surface / Terrain Layers (+ Layer Masks) /
  Weather sub-panels (gated by master type) drawing the wrapper's live knobs. Registered
  after Firmament, which owns `bbt_env`; a `make_material` MCP op stays a commented stub in
  `dispatch.py`, added only if agent-over-MCP authoring is later wanted.

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
