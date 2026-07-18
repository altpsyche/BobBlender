# Unified system plan

Forward-looking design, not the current state. `ARCHITECTURE.md` describes what
is built today; this describes where the op system and terrain generation are
going and how they stay separable for a later polyrepo split. Build against this;
update it as phases land, and fold settled parts into `ARCHITECTURE.md`.

## Goal

One op vocabulary, executors chosen per op, with non-destructive iteration and
GPU-accelerated terrain. Two subsystems under it:

- BobBlenderMCP: authoring Blender data over MCP (contracts, executors, bridge,
  builders).
- BobBlenderHeightFields: terrain heightfield generation in the venv (numpy on
  CPU, CuPy/CUDA on GPU), independent of Blender.

Both stay extract-ready: clean boundaries, one-way dependencies, and the only
things crossing a process line are JSON and files. Either can become its own repo
later without a rewrite.

## Principles

- Single vocabulary, executors per op. An op is data; where it runs is a property
  of the op, resolved by the caller, not a new runtime layer.
- Files and JSON across boundaries, never in-memory objects. The venv writes a
  PNG; Blender reads it.
- Non-destructive by default. Re-running a build updates in place.
- Reproducible on CPU; fast on GPU. The CPU path is the deterministic reference,
  the GPU path is the accelerator.
- Extract-ready. No subsystem imports another's internals; the seam is the op
  contract plus files.

## Vocabulary and executors (no runtime router)

Earlier draft proposed a single `apply(ops)` router that split a mixed op list
across venv and Blender. Cut, for now. It bought one thing (a mixed list in one
call) at the cost of undefined partial-failure state, blocking the MCP server on
long bakes, and breaking bridge atomicity. Not worth it yet.

Instead:

- `contracts.py` stays the one vocabulary. Heightfield op models live in the
  heightfields package and are imported into the union, so extraction carries
  them. This is a plain import, not a plugin registry; the registry only earns
  its keep once a subsystem actually lives in another repo.
- Two executors, already the shape we have: Blender ops go to the bridge (live)
  or the headless executor; heightfield ops run in the venv. They stay discrete
  MCP tools (`build`, `build_live`, and a new `bake_heightfield`). The agent
  sequences them: bake, then build the terrain that reads the PNG.
- Revisit a router only when a second consumer or a real repo split needs it.

## BobBlenderHeightFields (venv subsystem)

Graduate `erosion.py` into `tools/bobtools/heightfields/`, a pure package: no
`bpy`, no MCP, and no `config.py`/`bob.toml`. It takes absolute paths and params
only; path resolution stays in the caller. This is what keeps it extractable.

- `generate.py`: base field (smooth noise, domain warp, ridged blend, shape).
- `erode.py`: erosion passes as an ordered, composable list. Thermal slumping and
  stream-power today; droplet-hydraulic with deposition next (own phase).
- `presets.py`: named parameter sets (`alpine`, `foothills`, `badlands`).
- `io.py`: 16-bit PNG plus a params sidecar (`<name>.json`) written beside it, the
  full recipe that produced the field, so any heightfield is reproducible.
- `backend.py`: selects the array module and kernels (CPU vs GPU, below).
- Cache: keyed by a hash of (params, seed, backend). A hit on the CPU backend
  returns the existing PNG; a `preview` flag bakes at low resolution for fast
  iteration, full resolution on commit.
- Tests: this is pure numpy and the riskiest code, so it carries unit tests
  (shape/range invariants, mass-conservation bounds on erosion, a fixed-seed
  golden image on the CPU backend).

### GPU acceleration (CUDA)

Detected hardware: NVIDIA RTX 5080 (Blackwell, sm_120), driver 610.43, CUDA
toolkit 13.3. So GPU is a real option, not hypothetical, and a 5080 makes
high-resolution erosion near-interactive rather than a bake-and-wait.

Design, backend-abstracted so it runs anywhere and accelerates when a GPU is
present:

- `backend.py` exposes an array module `xp`, CuPy when available and enabled,
  else numpy. Generation and the grid erosion passes (thermal, stream-power) are
  pure array math, so they run on either backend from the same code. This is
  "GPU even on numpy": same array code, CuPy backend.
- Droplet-hydraulic erosion needs atomic scatter (many droplets deposit into
  shared cells), which does not vectorise cleanly. GPU path: a CuPy `RawKernel`
  (CUDA C), one thread per droplet, `atomicAdd` into the height and sediment maps
  through a brush. CPU path: a seeded numpy reference for correctness and the
  golden test, with an optional numba `njit` speedup.
- Determinism: the CPU reference is bit-deterministic (seeded). GPU `atomicAdd`
  ordering is not, so GPU output is not bit-identical run to run. The CPU path is
  the reference and what golden tests assert; GPU is the fast path. The cache key
  includes the backend so the two never alias.
- Dependencies: a separate optional `gpu` extra (a CuPy build matching CUDA 13 /
  Blackwell). Imported lazily; absence or a config flag falls back to numpy. The
  base `terrain` extra stays CPU-only, so CPU installs and extraction stay clean.
- Memory/resolution: 16 GB allows 4k+ float maps, so quality can scale up on GPU
  well past the current 512.

Spike first: confirm a CuPy build installs and runs a trivial `RawKernel` on
sm_120 under CUDA 13, since Blackwell plus CUDA 13 is new enough that wheel and
PTX support must be verified before anything is built on it. Do not add the
dependency without sign-off.

## Non-destructive Blender builds

- Rebuild in place. `build_geonodes` reuses the named object, its Nodes modifier,
  and its node group; clears and refills the graph; and restores knob values by
  socket name. Selection, transform, and tuned sliders survive a graph rebuild.
  Object/collection/material inputs are set on nodes by the recipe, so they come
  back for free.
- Knob policy, stated to avoid ambiguity: on rebuild, restore only the knobs the
  op did not explicitly set. An op that re-sends `density` wins (the op is
  intent); a knob the op leaves default keeps your sidebar tweak. Otherwise
  re-sent params would silently no-op.
- Blender 5.2 caveat: the repo already found 5.2 GN modifiers dropped
  IDProperties for datablock inputs. Numeric-knob snapshot/restore across an
  interface rebuild must be verified against the 5.2 API before this is promised;
  it is a short spike inside Phase A.
- `reload_image`: a small Blender op (or the terrain recipe calling
  `image.reload()`) so a fresh bake updates displacement without a manual reload.
- Auto-reload builders on file mtime: optional, deferred. Purging modules on a
  timer trades one button click for a class of stale-import heisenbugs. Not part
  of Phase A.

Contract changes still need an MCP server reconnect (the venv parses
`contracts.py` at startup), the tools-side half of the two-sided reload. Nothing
here removes that.

## The loop this enables

Bake a heightfield on the GPU (preview res) -> `heightmap_terrain` rebuilds in
place from it -> scatter with a path. Nudge an erosion param, re-bake, the terrain
updates in place without respawning. Tune `Path Width` in the sidebar with no
rebuild at all. On a 5080 the bake is fast enough that this feels iterative.

## Phasing

Resequenced so the artist-facing win lands first and the risky algorithm is
isolated behind the fast loop it needs.

- Phase 0 (done, 2026-07-18): GPU spike. `cupy-cuda13x==14.1.1` (a `gpu` extra,
  isolated from the default install) selects the RTX 5080 (sm_120, CUDA runtime
  13.2), array math matches numpy bit-for-bit, and an `atomicAdd` `RawKernel`
  compiles via NVRTC for sm_120 and scatters correctly. CPU stays selectable with
  CuPy present (`BOB_HF_BACKEND=cpu` and `select('cpu')`), so the deterministic
  reference holds on a GPU box. `backend.py` picks CPU or GPU with a clean slot
  for a future AMD/ROCm or portable backend.
- Phase 1 (done): `bake_heightfield` MCP tool + `reload_image` Blender op (+ its
  contract). The tool resolves a repo-relative PNG path, bakes in the venv, and
  writes a sidecar; `reload_image` refreshes the datablock so a re-bake shows.
- Phase 2 (done): `bobtools/heightfields/` graduated (generate, erode, io,
  presets, cache, backend), `erosion.py` kept as a compat shim, unit tests green
  (CPU determinism golden, GPU parity). Generation stays CPU (cheap, seeded,
  deterministic); the GPU win is the droplet kernel, not the grid passes.
- Phase 3 (done): in-place rebuild + `reset` flag. Verified in 5.2: rebuild keeps
  the object, transform, and tuned knobs; `reset` reapplies params; no duplicate.
  Key 5.2 finding: a Nodes modifier has no IDProperties at all, so knob values
  live on the node group interface socket `default_value` (per-object, since each
  build owns its group). Snapshot/restore operate there, not on `mod[id]`. Auto-
  reload skipped as planned.
- Phase 4 (done): droplet-hydraulic erosion, sequential numpy CPU reference plus a
  CuPy `RawKernel` GPU path, seeded from shared host positions. 1.5-2.5M droplets
  at 768 bake in ~2 s on the 5080. Quality: erosion spreads over a radius brush
  (the fix for spiky pits), and a `smooth` pass brackets the hydraulic pass (a
  coarse base pre-smooth, a light final smooth). A render shows a real eroded
  mountain, rocky crest, drainage rills, smooth flanks. Presets updated to this
  recipe.
- Phase D (done): the BobHeightField N-panel. `blender/extensions/bob_blender_mcp`
  gained a "Heightfield Terrain" panel (shape / erosion / displace knobs) and a
  Bake + Build operator that subprocesses `<repo>/tools/.venv/bin/python -m
  bobtools.heightfields` (so Blender's interpreter drives the venv bake), then
  reloads the image and builds the terrain in place. Verified headless end to end.

Router and any plugin/registry machinery: deferred until a real second consumer
or repo split forces them.

## Extraction readiness (polyrepo)

One-way dependencies, so a split is mechanical:

- `bobtools/heightfields/` imports only numpy/scipy/pillow (+ optional CuPy). No
  `bpy`, no MCP, no `config.py`. Absolute paths in, files out. Extractable as
  BobBlenderHeightFields with its op models, sidecar format, and gpu extra.
- `bbmcp/` imports only `bpy`. No MCP, no venv code.
- The framework (contracts, executors, bridge, mcp_server) knows each subsystem
  only through op models and file artifacts, not internals.
- The seam is always the op contract plus files on disk, so a subsystem in another
  repo behaves the same as one in-tree.
