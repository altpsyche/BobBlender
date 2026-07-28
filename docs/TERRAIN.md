# Terrain engine

The World-Creator-style terrain system: a composable, GPU filter stack that turns a
landscape choice and five knobs into an eroded heightfield. `SYSTEMS.md` covers the
Blender-side `heightmap_terrain` build that displaces a grid by the baked PNG; this
covers how the PNG is made.

The engine lives in the venv package `tools/core/heightfields/` (numpy on CPU,
CuPy on GPU, no bpy), so it stays extractable as a standalone library. Blender drives
a bake by subprocess to the venv (`python -m core.heightfields`); the panel and
the MCP `bake_heightfield` tool are the two front doors.

## The filter stack

A terrain recipe is an ordered list of ops evaluated on a float heightfield. Four
categories:

- Generators write or add height: `noise` (world-sampled ridged multifractal),
  `dunes` (directional wind ridges), `voronoi` (cellular mesas / cracks), `strata`
  (flat-lying layered rock benches), `macro` (an image as the macro base: a prompted
  mask from ComfyUI, or any hand-painted PNG).
- Erosion ops physically shape height: `fluvial` and `pipe_hydraulic` (water),
  `glacial` (ice), `scarp` (cap-rock cliff retreat), `rill` (downslope grooves),
  `thermal` (talus slump), `deposit` (sediment settling), `amplify` (multi-scale
  detail).
- Filters reshape height without a physical process: `terrace`, `warp`, `curve`,
  `sharpen`, `smooth`, `falloff` (coast taper). `channel_seed` carves a spline bed.
- Selectors produce a [0,1] mask that gates WHERE an op applies: `height`, `slope`,
  `curvature`, `flow`, `noise`, `path`.

`engine.run_stack(field, stack, backend, seed)` evaluates the stack and returns a
[0,1] numpy field. Each op is `{"kind": <name>, ...params}` and may carry a
`"mask": {"kind": <selector>, ...}`; the op is then blended by that mask,
`h = before*(1-mask) + after*mask`, so an op can act only on slopes, only on high flat
ground, only in a channel band, and so on. A stack starts from a zero field, so the
first op is a generator that establishes the base. An unknown op or selector kind
raises, so a typo is loud.

`run_stack` normalises the result to [0,1] when generating from a zero base
(`normalize=True`). When eroding an EXISTING baked field (the carve-then-erode
`base_png` path), it passes `normalize=False` and only clips, so the re-baked terrain
keeps the base's absolute height mapping and stays registered with anything placed
against the original.

The whole stack runs on `backend.xp` (numpy or CuPy) unchanged, so CPU and GPU share
one code path. Everything is a vectorised stencil (no per-droplet loop), which is why
it is fast on the GPU and deterministic.

## Op library

| Op | Category | Key params |
|----|----------|------------|
| `noise` | generator | `ridged` 0..1, `detail_strength`, `octaves`, `roughness`, `warp`, `mix` replace/add/max, `amount` |
| `dunes` | generator | `wind` deg, `frequency`, `sharpness`, `warp`, `variation`, `mix`, `amount` |
| `voronoi` | generator | `cells`, `pattern` mesa/crack, `jitter`, `mix`, `amount` |
| `strata` | generator | `layers`, `dissection`, `base_freq`, `sharpness`, `smooth` |
| `macro` | generator | `path` to an 8 or 16-bit PNG, `smooth` (blur as a fraction of width), `invert`, `mix`, `amount` |
| `fluvial` | erosion | `iterations`, `k`, `sp_m`, `sp_n`, `diffusion`, `talus`, `thermal_iters`, `recompute`, `fill_iters`, `acc_iters`, `max_delta`, `flow_prior` |
| `pipe_hydraulic` | erosion | `iterations`, `rain`, `capacity`, `dissolve`, `deposit`, `evaporate`, `incision`, `sp_m`, `sp_n` |
| `glacial` | erosion | `ela`, `iterations`, `erode`, `kslope`, `ice_width`, `ice_gamma`, `widen`, `horn`, `arete_talus` |
| `scarp` | erosion | `iterations`, `cap_slope`, `undercut`, `talus`, `open_size` |
| `rill` | erosion | `iterations`, `groove`, `spacing`, `smear`, `slope_gate`, `aspect_sigma`, `sharpen`, `despike`, `talus` |
| `deposit` | erosion | `amount`, `iterations`, `flow_m`, `slope_n`, `flow_floor`, `settle_talus`, `settle_iters` |
| `thermal` | erosion | `talus`, `factor`, `iterations`, `talus_warp`, `talus_freq` (or `repose_deg`) |
| `amplify` | erosion | `mode` fluvial/aeolian, `to`, `strength`, `iterations`, `wind`, `repose`, `relief`, `diffusion`, `despike` |
| `terrace` | filter | `steps`, `sharpness`, `tilt` |
| `warp` | filter | `amount`, `frequency` |
| `curve` | filter | `gamma`, `contrast` |
| `sharpen` | filter | `amount`, `radius` |
| `smooth` | filter | `sigma` |
| `falloff` | filter | `shape` edge/radial/gradient, `margin`, `power`, `floor`, `angle` |
| `channel_seed` | carve | `curves`, `width`, `falloff`, `depth` |

Every generator takes a `mix` mode (replace / add / multiply / max) so a recipe can
layer a base and blend structure on top. `pipe_hydraulic`, `voronoi`, `terrace`,
`warp`, `curve`, `deposit`, and `channel_seed` are registered and available but not
used by any shipped preset today (they are building blocks for custom stacks and the
spline carve-and-erode path).

Selectors (used as an op's `mask`): `height`, `slope`, `curvature`, `flow`, `noise`,
`path`. These mirror the mask vocabulary the scatter recipe and the BobShaders terrain
material use, so terrain SHAPE, SCATTER, and TEXTURING key off the same masks and stay
coherent. `path` masks to a band around a BobSplines curve polyline.

## Erosion model

The old droplet-hydraulic simulation was retired; the vectorised ops below run on both
CPU and GPU with no per-droplet loop.

### Why fluvial carves canyons

Canyons come from FLOW-ACCUMULATION stream-power. `ops_erode.fluvial` does:
Planchon-Darboux depression fill (the surface water drains over), then
multiple-flow-direction drainage-area accumulation (RAW, not normalised, so hillslopes
read ~1 and channels read thousands), then stream-power incision `k * A^sp_m * S^sp_n`
plus hillslope diffusion plus thermal. The large dynamic range of the accumulation is
what lets channels incise deep and leaves hillslopes alone; normalising it flattens the
contrast and no canyons form. `sp_m=0.5, sp_n=1.0` is the resolution-invariant exponent
pair, so the incision magnitude holds across bake resolutions. The drainage network is
recomputed every `recompute` steps (it is stable, so this bounds cost). A `flow_prior`
({curves, width, falloff, gain}) boosts the drainage area along a river spline so the
solver incises the valley there and the banks EMERGE from erosion rather than being
stamped on as a swept cross-section.

### Landform-specific processes

Stream-power fluvial grades everything toward a dendritic V-valley equilibrium, so it
cannot make several real landforms. Each of these uses its OWN process:

- `glacial`: ice, not water. Ice is sourced above an `ela` snowline and routed
  downslope, broadened into a valley-width tongue (`ice_width`), and abrades the bed
  to cut broad flat-floored U-troughs, overdeepened cirques, and knife-edge aretes and
  horns (`horn`, `arete_talus`) above the snowline. Powers the `glacial` preset.
- `scarp`: cap-rock scarp retreat. Flat caps (slope < `cap_slope`) resist; steep faces
  are undercut and recede, with a steep talus apron. Dissects a layered `strata`
  plateau into flat-topped mesas with near-vertical sides. Powers `mesa`, `canyon`,
  `plateau`.
- `rill`: anisotropic downslope-groove incision. Does NOT use flow accumulation;
  instead it smears a fine noise band downhill along steepest descent to carve dense,
  closely-spaced, near-parallel gullies with knife-edge divides on steep soft slopes.
  Powers the `badlands` preset.
- `deposit`: fluvial deposition. Raises the bed where flowing water loses capacity
  (high drainage area, low slope), alluviating valley floors and growing point bars,
  gated to wet channels by `flow_floor`. Not in a shipped preset.
- `thermal`: 4-neighbour talus slump. Used everywhere as a relaxation pass and inside
  the other ops to hold walls at a repose angle. `talus_warp` > 0 makes the repose
  angle a per-cell noise field so valley walls stop reading as one uniform ruled slope.
- `pipe_hydraulic`: Mei et al. 2007 shallow-water pipe model with an optional
  stream-power incision term. Available for custom stacks; not in a shipped preset.

## Amplification

`amplify` (`ops_erode.amplify`) is multi-scale terrain amplification (Schott et al.
2024). Every shipped preset ends in an amplify op. It grows a coarse macro field up to
the bake resolution `to` in resolution-doubling levels, adding erosion-consistent fine
detail at each level. Because every level builds deterministically on the previous, a
bake to a lower `to` is a faithful low-detail PREFIX of a bake to a higher `to` -- a
preview and a full bake register (preview == final).

Two modes so the added detail matches how the landform erodes:

- `fluvial`: upsample, seed an isotropic detail band on slopes, then PURE stream-power
  incision (no thermal) so rills and gullies emerge while macro cliffs and ridges stay
  crisp. For mountains, canyons, mesas, hills. `diffusion` > 0 relaxes the added
  channels into smooth swales for soft lowlands; `diffusion` 0 keeps cliff faces crisp.
- `aeolian`: upsample, add windward transverse ripples and dunelets, then settle to the
  sand angle of repose. No stream-power (sand has no rivers). For dunes and sand seas.

Because amplify runs at the bake resolution, the coarse macro runs cheap at a fixed
`AMPLIFY_BASE` (256) resolution and amplify climbs from there. `params.macro_size` and
`pipeline` handle this: an amplify stack generates its macro at `AMPLIFY_BASE`, then
amplify climbs to `size`. A preview of an amplify stack bakes at `AMPLIFY_PREVIEW`
(512), one climb level above the base, so the preview is a real prefix of the cascade.

The `mesa` and `plateau` amplify ops carry `diffusion: 0.06`: a clean planar scarp wall
otherwise combs the stream-power rills into an evenly-spaced vertical picket-fence, and
the small diffusion breaks that into varied buttresses and furrows. `canyon` needs no
diffusion because its fluvial-carved walls are already varied.

## Presets

Thirteen presets, each a tuned stack, across four families (`presets.STACKS`,
`presets.FAMILIES`):

- Mountains: `alpine` (ridged noise + fluvial + fluvial amplify), `glacial` (ridged
  noise sculpted by the `glacial` ice op), `foothills` (gentler fluvial + smooth).
- Lowlands: `hills`, `plains` (both soft noise + gentle fluvial + diffused amplify),
  `coastal` (a `falloff` gradient shoreline), `islands` (a radial `falloff` island).
- Canyons: `mesa` (`strata` + cap-rock `scarp`), `canyon` (smoothed `strata` plateau
  incised by the stream-power hero fluvial), `badlands` (soft macro dissected by the
  `rill` groove op), `plateau` (`strata` + `curve` + light `scarp` for a continuous
  cliff-edged tableland). Real generators, not eroded noise.
- Dunes: `dunes` (crisp transverse `dunes` + aeolian amplify), `sand_sea` (broader
  lower dunes over a faint sand-sheet noise).

`presets.STACKS` holds the op stacks; `presets.DISPLAY` holds per-preset Blender
displacement defaults (a relief RATIO and a sea-level fraction, see below). `presets.py`
is the single source of truth; `tools/scripts/gen_panel_presets.py` regenerates the
committed `blender/extensions/bob_blender_tools/presets.json` (knob table + raw stacks)
that the panel reads, and a drift test fails if it goes stale.

## Real-world scale and repose

1 Blender unit = 1 metre. A preset does not store a fixed metre height; it stores a
scale-invariant relief RATIO (typical relief / tile width) in `presets.DISPLAY`, and
`presets.height_for(name, size)` derives the metre height from the artist's tile size
(clamped to [0.5 m, 0.6 * size] so a preset can be neither a flat sheet nor a
taller-than-wide wall). So the same preset stays proportioned at any size: a 90 m patch
of alpine gets ~27 m of relief, a 4 km alpine range gets ~1.2 km, and a 1.8 m character
or 6 m house dropped on either reads correctly.

Slope-relaxation passes (`thermal`, and the talus inside `scarp` / `fluvial` /
`deposit`) can be authored by a real angle of repose instead of a hand-picked talus: an
op carries `repose_deg` and `params.resolve_stack` converts it to the concrete talus for
the resolution the pass runs at, via `presets.talus_for_angle`
(`talus = tan(angle) / (relief_ratio * bake_res)`). Because relief is a ratio, the
rendered angle is independent of tile size, and because the talus tracks the bake
resolution, a preview and a full bake hold the SAME physical angle. Dune slip faces use
`repose_deg` 34 (dry sand's angle of repose); `presets.REPOSE` lists the physical
targets. Structural near-vertical faces (cap-rock cliffs in `scarp`) still take a raw
talus on purpose. Amplify resolves its own per-level repose internally.

## Global knobs

Five curated knobs modulate a copy of the active preset stack (`params.resolve_stack`).
Each is 0..1 with 0.5 meaning "the preset exactly as authored", and maps to ONE clear
lever per op kind so the response is predictable on any preset:

| Knob | What it does | Levers |
|------|--------------|--------|
| Relief | ruggedness | noise `detail_strength`, dune `sharpness` |
| Detail | feature size | noise `octaves` (+/-2), dune `frequency`, `sharpen` amount, amplify `strength` |
| Erosion | incision | `fluvial` / `rill` / `glacial` / `thermal` iteration counts |
| Warp | meander | noise / dune domain-warp amplitude, `warp` op amount |
| Seed | variation | a decorrelated seed into every generator (`_SEED_OPS`: noise, dunes, voronoi, strata, warp, amplify, rill) |

`resolve_stack` also sets each amplify op's `to` (the bake resolution) and its `relief`
ratio, and resolves any `repose_deg` pass to a concrete talus. `voronoi`, `terrace`,
`curve`, `smooth`, `falloff` keep their preset values; their character is structural,
not a global-knob axis.

## The macro mask: an art-directed silhouette on top of a preset

A bake also takes a sixth, optional input: `macro`, a dict of `{path, weight, smooth,
invert}` that puts an IMAGE at the head of the stack. `params.with_macro` prepends a
`macro` op and demotes the preset's own generator to an `add` of the remaining relief, so
the mask and the family are a weighted sum rather than one replacing the other, and every
erosion op after it behaves exactly as it does on a noise base. `pipeline._stack_for`
applies it to a preset stack and to an explicit one alike, so the panel, the CLI and MCP
all reach it through the same key.

It is a MASK, not a heightfield: the op blurs it at a fiftieth of the field width, so
nothing finer than a massif survives to compete with the erosion. Measured on three
prompts (docs/GENERATION-BASELINES.md, G5): the mask's shape survives an erosion pass
at band-limited correlation 0.906 to 0.923 while supplying 0.28 to 0.31 m of a tile's
relief against the erosion's 2.89 to 3.04 m. The Terrain panel's `Generate Base` writes
one from a prompt through ComfyUI; any 8 or 16-bit PNG works, including a hand-painted
one.

## How to run

### CLI

```
python -m core.heightfields --out /abs/height.png --knobs-file knobs.json
python -m core.heightfields --out /abs/height.png --params-file params.json
python -m core.heightfields --backends        # print available backends as JSON
```

- `--knobs-file` is flat knobs (`preset` + `relief`/`detail`/`erosion`/`warp`/`seed`/
  `size`), expanded via `build_params`; a knobs file may also carry an explicit `stack`,
  passed through as-is (the panel custom-stack mode).
- `--params-file` is a full params dict; a `preset` key expands to a stack, then the
  rest of the file overrides.
- `--preview` bakes at preview resolution (`PREVIEW_SIZE` 256, or `AMPLIFY_PREVIEW` 512
  for an amplify stack). `--force` ignores the cache. `--maps` also emits flow/wetness
  PNGs. The result metadata prints as JSON on the last stdout line.

### Panel

`View3D > N > BobBlenderTools > Terrain`. Two ways to author:

1. Preset + knobs (default): pick a Landscape preset, then turn Relief / Detail /
   Erosion / Warp / Seed under Sculpt. `Bake + Build Terrain` bakes the heightfield in
   the venv (GPU) and builds the displaced mesh in place. Mesh Density is decoupled from
   the bake resolution (the heightmap keeps full detail for shading; the mesh needs only
   enough vertices for the silhouette).
2. Filter Stack (advanced): toggle "Use custom stack", "Load <preset>" to pull the
   preset's ops in to edit, then add / remove / reorder ops and set each op's params and
   mask. When custom mode is on the bake runs the edited stack verbatim.

The panel always bakes full resolution. There is no "Preview (256)" checkbox: every
shipped preset amplifies, so a preview would only be a lower-detail prefix of the same
landform, not a faster path to the final. The `--preview` CLI flag and
`pipeline.bake(preview=True)` are kept for CPU/scripted use.

### MCP

The `bake_heightfield` tool (`tools/bobtools/mcp/mcp_server.py`) bakes in the venv:

```
bake_heightfield(out_file, params={size, seed, backend, preset?, relief, detail,
                 erosion, warp}, preview=False, force=False)
```

`out_file` is a repo-relative PNG path. A `preset` in params expands to a stack; pass an
explicit `"stack": [...]` to run one directly. Returns `{path, out_file, backend,
platform, size, seconds, stats, hash, cached}`. After re-baking into an open session,
send a `reload_image` op so it picks up the new pixels.

## Flow and wetness maps

Beside `<name>.png` a bake can also write `<name>_flow.png` and `<name>_wetness.png`
(`maps.derive_maps`). The panel toggle "Flow + wetness maps" defaults ON; the engine
and CLI default OFF (opt in with `--maps`, or `maps: True` in params). Flow is
log-scaled drainage accumulation (bright in channels); wetness blends flow with low,
flat ground. They are computed from the same flow-accumulation the erosion uses, so
their channels line up with the carved canyons. The map paths are recorded in the bake
sidecar under `"maps"`.

These let shading key off the terrain's OWN hydrology rather than only weather-driven
wetness. The terrain BobShader samples them: the bake stores the heightmap path + size
on the object, `new_bobshader(obj, "terrain")` loads the sibling maps, and the terrain
material samples them by object-space XY into a `Flow Map` (drives a per-layer flow mask)
and a `Wetness Map` (folds into the weather wet factor, so channels read damp even in
EEVEE).

## The Blender build

`heightmap_terrain` (`blender/extensions/bob_blender_tools/core/geonodes/recipes/heightmap_terrain.py`) displaces a
grid by the baked PNG. Params: `heightmap` (absolute path), `size`, `resolution`,
`height`, `sea_level`, `material`. It enforces metric scene units (1 unit = 1 m) as the
single choke point, samples the heightmap at each grid point's world XY
(`UV = pos.xy / Size + 0.5`), computes `z = (sample - Sea Level) * Height`, and
displaces.

The recipe sets `GeometryNodeSetShadeSmooth` on the output: a GN grid is flat-shaded by
default, so a displaced terrain would read as a field of per-quad facets (an orange-peel
stipple on every slope). The heightfield is continuous, so the terrain ships
smooth-shaded from this single build choke point.

(`recipes/terrain.py` is a separate, non-eroded procedural GN terrain that composes
noise directly in nodes; it is not part of the heightfield erosion pipeline.)

## Guarantees

- Deterministic: pure stencils, no random scatter or float atomics, so a seeded bake is
  bit-reproducible run to run on both CPU and GPU. This backs the params-hash cache and
  the committed golden.
- Resolution-independent: generation is world-sampled and erosion uses physical
  stream-power exponents, so a preview and a full bake are the same landform. Author at
  low res, commit at high.
- GPU or CPU: CuPy CUDA when present (and ROCm with a cupy-rocm build), else numpy. The
  venv is Python 3.14, where compiled noise libraries have no wheels, so the noise is
  hand-rolled numpy. `backend.select` honours `BOB_HF_BACKEND`, then the caller's
  preference, then GPU-first auto order.

## Cache

`pipeline.bake` keys a params-hash cache (`cache.params_hash`) on the RESOLVED recipe:
size, seed, the resolved backend name (not "auto"), the exact stack with injected seeds
and knob modulations, the maps flag, and (for a `base_png` re-erode) the base's content
hash. The hash also folds in a fingerprint of the engine source files, so editing the op
math invalidates old sidecars automatically with no manual version bump. A bake whose
hash matches the existing sidecar is a no-op that returns the cached stats.

## Files

| File | Role |
|------|------|
| `heightfields/engine.py` | the op-stack evaluator + op registry + masking |
| `heightfields/generate.py` | world-sampled ridged-multifractal base noise |
| `heightfields/ops_generate.py` | dunes, voronoi, strata, macro generators |
| `heightfields/ops_erode.py` | fluvial, pipe_hydraulic, glacial, scarp, rill, deposit, thermal, amplify + drainage helpers |
| `heightfields/ops_filter.py` | terrace, warp, curve, sharpen, falloff |
| `heightfields/ops_carve.py` | channel_seed + curve distance-field helpers |
| `heightfields/ops_select.py` | height/slope/curvature/flow/noise/path selectors |
| `heightfields/maps.py` | flow / wetness derived maps |
| `heightfields/presets.py` | the 13 preset stacks + display defaults + repose |
| `heightfields/params.py` | global-knob modulation, build_params, amplify/repose resolution |
| `heightfields/pipeline.py` | bake orchestration, cache, sidecar, preview sizing |
| `heightfields/backend.py` | CPU/GPU backend selection |
| `heightfields/cache.py` | resolved-recipe params hash + source fingerprint |
| `heightfields/io.py` | 16-bit PNG read/write + sidecar |
| `heightfields/__main__.py` | CLI entry (subprocess bake from Blender) |
| `heightfields/erode.py` | legacy CPU thermal/stream-power helpers (compat shim only) |

## Extending

- Add an op: write `fn(h, xp, **params) -> h` in the right `ops_*.py`, register it in
  `engine._OPS`, and (if it takes a seed) add its kind to `params._SEED_OPS`. To expose
  it in the panel stack editor, add it to the op metadata in the extension `__init__.py`.
- Add a selector: write `fn(h, xp, **params) -> mask` in `ops_select.py` and register it
  in `SELECTORS`.
- Add a preset: add a stack to `presets.STACKS`, a row to `presets.DISPLAY`, and its name
  to `presets.FAMILIES`, then rerun `gen_panel_presets.py`.
</content>
</invoke>
