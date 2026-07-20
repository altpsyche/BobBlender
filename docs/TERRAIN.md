# Terrain engine

The World-Creator-style terrain system: a composable, GPU filter stack that turns a
landscape choice and five knobs into an eroded heightfield. This document describes
the shipped system (P1-P5). `SYSTEMS.md` covers the Blender-side `heightmap_terrain`
build that displaces a grid by the baked PNG; this covers how the PNG is made.

The engine lives in the venv package `tools/bobtools/heightfields/` (numpy on CPU,
CuPy on GPU, no bpy), so it stays extractable as a standalone library. Blender drives
a bake by subprocess to the venv (`python -m bobtools.heightfields`); the panel and
the MCP `bake_heightfield` tool are the two front doors.

## The filter stack

A terrain recipe is an ordered list of ops evaluated on a float32 heightfield. Three
categories:

- Generators write or add height: `noise` (world-sampled ridged multifractal),
  `dunes` (directional wind ridges), `voronoi` (cellular mesas / cracks).
- Filters reshape height: `fluvial` and `pipe_hydraulic` (erosion), `thermal` (talus
  slump), `terrace`, `warp`, `curve`, `sharpen`, `smooth`, `falloff` (coast taper).
- Selectors produce a [0,1] mask that gates WHERE a filter applies: `height`, `slope`,
  `curvature`, `flow`, `noise`.

`engine.run_stack(field, stack, backend, seed)` evaluates the stack and returns a
normalised [0,1] field. Each op is `{"kind": <name>, ...params}` and may carry a
`"mask": {"kind": <selector>, ...}`; the filter is then blended by that mask,
`h = before*(1-mask) + after*mask`, so an op can act only on slopes, only on high flat
ground, only in channels, and so on. A stack starts from a zero field, so the first
op is a generator that establishes the base.

The whole stack runs on `backend.xp` (numpy or CuPy) unchanged, so CPU and GPU share
one code path. Everything is a vectorised stencil (no per-droplet loop), which is why
it is fast on the GPU and deterministic.

### Why fluvial carves canyons

Canyons come from FLOW-ACCUMULATION stream-power, not from droplet or pipe-model
erosion. `ops_erode.fluvial` does: Planchon-Darboux depression fill (the surface water
drains over) then multiple-flow-direction drainage-area accumulation (RAW, not
normalised, so hillslopes read ~1 and channels read thousands) then stream-power
incision `k * A^m * S^n` plus hillslope diffusion plus thermal. The large dynamic range
of the accumulation is what lets channels incise deep and leaves hillslopes alone;
normalising it flattens the contrast and no canyons form. `m=0.5, n=1.0` is the
resolution-invariant exponent pair, so the incision magnitude holds across bake
resolutions.

## Op library

| Op | Category | Key params |
|----|----------|------------|
| `noise` | generator | `ridged` 0..1, `detail_strength`, `octaves`, `warp`, `mix`, `amount` |
| `dunes` | generator | `wind` deg, `frequency`, `sharpness`, `variation`, `mix`, `amount` |
| `voronoi` | generator | `cells`, `pattern` mesa/crack, `mix`, `amount` |
| `fluvial` | erosion | `iterations`, `k`, `sp_m`, `sp_n`, `diffusion`, `fill_iters`, `acc_iters`, `recompute`, `max_delta` |
| `pipe_hydraulic` | erosion | `iterations`, `rain`, `incision`, `capacity`, `dissolve`, `deposit` |
| `thermal` | erosion | `talus`, `factor`, `iterations` |
| `terrace` | filter | `steps`, `sharpness`, `tilt` |
| `warp` | filter | `amount`, `frequency` |
| `curve` | filter | `gamma`, `contrast` |
| `sharpen` | filter | `amount`, `radius` |
| `smooth` | filter | `sigma` |
| `falloff` | filter | `shape` edge/radial/gradient, `margin`, `power`, `angle` |

Selectors (used as an op's `mask`): `height`, `slope`, `curvature`, `flow`, `noise`.
These mirror the mask vocabulary the scatter recipe and the BobShaders terrain material
use, so terrain SHAPE, SCATTER, and TEXTURING key off the same masks and stay coherent.

## Preset families

Thirteen presets, each a tuned stack, across four families:

- Mountains: `alpine`, `glacial`, `foothills`
- Canyons: `canyon` (dendritic stream-power), `mesa`, `badlands`, `plateau`
- Lowlands: `hills`, `plains`, `coastal`, `islands`
- Dunes: `dunes`, `sand_sea`

Presets live in `presets.STACKS` (the op stacks) and `presets.DISPLAY` (per-family
Blender displacement defaults: metres of height, sea-level fraction). `presets.py` is
the single source of truth; `tools/scripts/gen_panel_presets.py` regenerates the
committed `blender/extensions/bob_blender_tools/presets.json` (knob table + raw
stacks) that the panel reads, and a drift test fails if it goes stale.

## Global knobs

Five curated knobs modulate a copy of the active preset stack (`params.resolve_stack`).
Each is 0..1 with 0.5 meaning "the preset exactly as authored", and maps to ONE clear
lever per op kind so the response is predictable on any preset:

| Knob | What it does | Levers |
|------|--------------|--------|
| Relief | ruggedness | noise `detail_strength`, dune `sharpness` |
| Detail | feature size | noise `octaves` (+/-2), dune `frequency`, `sharpen` amount |
| Erosion | incision | `fluvial` / `thermal` iteration counts |
| Warp | meander | noise / dune domain-warp amplitude |
| Seed | variation | a decorrelated seed into every generator |

## The panel

`View3D > N > BobBlenderTools > Terrain`. Two ways to author:

1. Preset + knobs (default): pick a Landscape preset, then turn Relief / Detail /
   Erosion / Warp / Seed under Sculpt. Bake + Build bakes the heightfield in the venv
   and builds the displaced mesh in place. Mesh Density is decoupled from the bake
   resolution (the heightmap keeps full detail for shading; the mesh needs only enough
   vertices for the silhouette).
2. Filter Stack (advanced): toggle "Use custom stack", "Load <preset>" to pull the
   preset's ops in to edit, then add / remove / reorder ops and set each op's params
   and mask. When custom mode is on the bake runs the edited stack verbatim. Each op
   surfaces its common params; a hidden field carries the rest so a loaded preset
   re-bakes faithfully.

## Flow and wetness maps

On by default for a terrain bake ("Flow + wetness maps", or `--maps` on the CLI):
beside `<name>_hf.png` it writes `<name>_hf_flow.png` and `<name>_hf_wetness.png`
(`maps.derive_maps`). Flow is log-scaled drainage accumulation (bright in channels);
wetness blends flow with low, flat ground. They are computed from the same
flow-accumulation the erosion uses, so their channels line up with the carved canyons.
The map path is recorded in the bake sidecar under `"maps"`.

These let shading key off the terrain's OWN hydrology rather than only weather-driven
wetness. The terrain BobShader samples them (in `bbmcp/materials.py`): the bake stores
the heightmap path + size on the object (`bbt_heightmap`, `bbt_terrain_size`),
`new_bobshader(obj, "terrain")` loads the sibling maps, and `terrain_material` samples
them by object-space XY (`UV = Position.xy / size + 0.5`, image Non-Color / Linear /
EXTEND) into two master inputs:

- `Flow Map` drives a per-layer Flow mask (`L{i} Flow Strength` / `Flow Threshold`),
  alongside slope/altitude/noise/paint/curvature, so a sediment/gravel layer can be kept
  to the channels.
- `Wetness Map` + `Terrain Wetness` fold into the weather wet factor
  (`wf = MAX(weather wet, cavity pool, terrain wet)`), so channels read damp independent
  of the weather -- and in EEVEE too, since the map is baked (the Cycles Pointiness cavity
  term is not).

Creating a Terrain BobShader on a terrain with maps auto-wires a riverbed layer keyed to
flow plus a baseline `Terrain Wetness`, for a one-pick channel look (editable afterward).
Scatter does not yet sample the maps (a riparian flow mask is future work).

## Guarantees

- Deterministic: pure stencils, no random scatter or float atomics, so a seeded bake
  is bit-reproducible run to run on both CPU and GPU, and the two agree byte-for-byte
  at small sizes. This backs the params-hash cache and the committed golden.
- Resolution-independent: generation is world-sampled and erosion uses physical
  stream-power exponents, so a 256 preview and a 1024 bake are the same landform
  (corr(256, 768->256): hills 0.96, canyon 0.93). Author at low res, commit at high.
- GPU or CPU: CuPy CUDA when present (and ROCm with a cupy-rocm build), else numpy.
  The venv is Python 3.14, where compiled noise libraries have no wheels, so the noise
  is hand-rolled numpy.

## Extending

- Add an op: write `fn(h, xp, **params) -> h` in the right `ops_*.py`, register it in
  `engine._OPS`, and (if it takes a seed) add its kind to `params._SEED_OPS`. To expose
  it in the panel stack editor, add it to `_OP_META` / `_OP_PARAMS` / `_OP_ADD_DEFAULTS`
  in the extension `__init__.py`.
- Add a selector: write `fn(h, xp, **params) -> mask` in `ops_select.py` and register it
  in `SELECTORS`.
- Add a preset: add a stack to `presets.STACKS` and a row to `presets.DISPLAY`, then
  rerun `gen_panel_presets.py` and the golden is unaffected (it uses an explicit stack).

## Files

| File | Role |
|------|------|
| `heightfields/engine.py` | the op-stack evaluator + op registry + masking |
| `heightfields/generate.py` | world-sampled ridged-multifractal base noise |
| `heightfields/ops_generate.py` | dunes, voronoi generators |
| `heightfields/ops_erode.py` | fluvial, pipe_hydraulic, thermal + drainage helpers |
| `heightfields/ops_filter.py` | terrace, warp, curve, sharpen, smooth, falloff |
| `heightfields/ops_select.py` | height/slope/curvature/flow/noise selectors |
| `heightfields/maps.py` | flow / wetness derived maps |
| `heightfields/presets.py` | the 13 preset stacks + display defaults |
| `heightfields/params.py` | global-knob modulation, build_params |
| `heightfields/pipeline.py` | bake orchestration, cache, sidecar |
| `heightfields/erode.py` | legacy CPU thermal/stream-power helpers (compat shim) |
