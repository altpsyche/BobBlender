# Roadmap: what is not single-sourced yet, and why it matters

[ARCHITECTURE.md](ARCHITECTURE.md) describes what is built. This describes what is deliberately
**not** finished, in one place, so an open item cannot hide inside a doc that reads as complete.

Everything here is either a constraint that has to stay true for a future split to be mechanical, or a
named gap with the reason it has not been closed. Nothing here is a phase.

---

## The extraction constraints, and the one-way rule that keeps them cheap

Two subsystems are meant to become their own repos without a rewrite: the terrain heightfield engine,
and the Blender authoring side behind MCP. That stays cheap only while these hold, and each one is a
thing to check in review rather than an aspiration:

- **`core/heightfields/` imports numpy and scipy, plus optional CuPy. No `bpy`, no MCP, no config,
  and no PIL** — the 16-bit PNG codec is pure numpy plus `zlib` precisely because the compute runs
  inside Blender's bundled Python, which ships no PIL. Absolute paths in, files out. That is the
  whole reason it is extractable, and it is why the compute lives in the extension and is never
  copied into a venv: **one committed source**, shared by the in-Blender path and the venv golden
  tests, so there is no duplicate to drift.
- **`core/` imports only `bpy`.** No MCP, no venv code.
- **The framework (contracts, executors, bridge, MCP server) knows each subsystem only through op
  models and file artifacts**, never internals.
- **The seam is always the op contract plus files on disk.** JSON and files cross a process line;
  in-memory objects never do. A subsystem in another repo therefore behaves exactly like one in-tree.
- `check_selfimports.py` enforces the import half of this in CI: every intra-package import is
  relative, because the package loads under two fully-qualified names (`bl_ext.<pkgid>.…` live,
  `bob_blender_tools.…` headless) and an absolute self-import resolves in only one of them and dies
  silently in the other.

**The router that was cut, and why it stays cut.** An earlier design had a single `apply(ops)` that
split a mixed op list across the venv and Blender. It bought one thing — a mixed list in one call — at
the cost of undefined partial-failure state, a blocked MCP server during long bakes, and broken bridge
atomicity. `contracts.py` stays the one vocabulary; where an op runs is a property of the op, resolved
by the caller. A plugin registry earns its keep the day there is a real second consumer, and not
before.

## Determinism, and where it is deliberately given up

- **The CPU path is the bit-deterministic reference** and what the golden tests assert.
- **The GPU path is not bit-identical run to run**, because `atomicAdd` ordering is not deterministic.
  That is accepted, not a defect. **The cache key includes the backend** so the two can never alias.
- Generation stays on CPU: it is cheap, seeded and deterministic. The GPU win is the droplet kernel,
  not the grid passes.
- CPU stays selectable with CuPy present (`BOB_HF_BACKEND=cpu`, `select('cpu')`), so the deterministic
  reference still holds on a GPU box.
- 16-bit PNG plus a params sidecar means **any heightfield is reproducible from the recipe that made
  it**, which is the same provenance rule the generated assets follow.

## Open: the GPU install surface

The highest-risk surface in the whole extension, and the one most likely to be encountered by an
artist rather than by a developer. GPU acceleration is a first-class capability, not an optional
extra, so the one-click install has to handle the real matrix: NVIDIA CUDA 11.x against 12.x against
13.x, AMD ROCm, no driver at all, offline, and a Blender Python that blocks writes to its
`site-packages`.

- **Each failure must degrade to CPU with a specific message.** CPU fallback is the safety net for
  those failures and for GPU-less machines; it is NOT a reason to treat GPU as skippable.
- **The two unknowns to spike before touching this**: target CUDA-line detection, and
  pip-into-Blender-Python. The whole "GPU works out of the box" promise rests on them.
- **Verify a wheel install survives a Blender version update**, because the bundled Python can change.
- **Decide the AMD/ROCm support tier explicitly** — fully supported, or CPU-only for now — rather than
  leaving it implied.

Reference point that already works: `cupy-cuda13x` selects an RTX 5080 (sm_120, CUDA runtime 13.2),
array math matches numpy bit for bit, and an `atomicAdd` `RawKernel` compiles via NVRTC for sm_120 and
scatters correctly. 1.5 to 2.5 million droplets at 768 bake in about 2 s. That is one machine, not a
matrix.

## Open: preference persistence across a packaged install

The asset-pack folder list and the Output Folder live in `AddonPreferences`. Confirm they survive the
packaged (non-symlink) install and a version update. Untested, and cheap to test.

## Open: old-scene migration

The rename from the pre-restructure module names was Python-module-level, and scene properties
(`bbt_*`), node groups and drivers key off bpy data names rather than Python paths, so pre-refactor
`.blend` files should keep working. **Should** is doing work in that sentence. The smoke test is: open
a scene saved before the refactor, re-run a build, diff the geometry.

## Open: the in-Blender test job

`.github/workflows/ci.yml` carries a disabled `blender-headless` job. It needs an in-Blender entry
point at `tools/tests/run_blender_tests.py` that imports the extension under its `bl_ext` name and
drives the geometry tests inside `bpy`. Everything in `tools/tests` today runs in the plain venv
against the pure-python core, which is why the fast gate stays honest without Blender — but it also
means no geometry assertion runs in CI, and every geometry number in these docs comes from a local
Blender run.

## Open: the one thing the forest-barn gate measured and did not fix

Everything else that gate found is closed: the mesh repair and the coincident bake in
[GENERATION.md](GENERATION.md) (the one-shot route), the lit albedo in its texture-set section, the
bark shear in [FOLIAGE.md](FOLIAGE.md), and `subject_only` on `comfy_mesh`. One is left, and it is
left for a reason rather than for time.

- **The bake cage is still a guess, on the one route that still uses it.** 2% of the longest
  dimension with rays reaching 8%, which is far too loose for two near-coincident surfaces. The
  coincident and colour-from-low paths now bake with no cage at all, so this is only read where
  there is a genuinely denser mesh to cross to — the staged and block-out routes. The honest version
  measures the actual separation between the pair and sizes the cage from it. Not done because
  **there is no staged asset on disk to measure a change against**, and changing a cage by reasoning
  alone is how the misaligned bake survived two gates. It needs one staged-route generation, which
  needs ComfyUI.

Two smaller calls were made deliberately and are recorded where they apply rather than here:
`profile_segments` stays 5 on the shipped conifer, because the shear that argued for raising it is
fixed and the remaining argument is faceting at hero distance (FOLIAGE.md); and `AO_STRENGTH` stays
at 0.6 with Albedo × AO unchanged, because the suspected double-count measured as absent — the AO's
source field is a high-pass at a thirty-second of the image and the delighting corrects at an
eighth, so the two cannot overlap (GENERATION.md, and the comment in `core/materials/texset.py`).

## Open: scatter items that were scoped and not built

Named so they are not rediscovered as new: a per-layer emitter override, an in-panel Make Proxies
affordance, and masking beyond slope (`Min Normal Z`) plus path clearing.

## Deferred, with the reason

- **Auto-reloading builders on file mtime.** Purging modules on a timer trades one button press for a
  class of stale-import heisenbugs. Reload Builders stays explicit.
- **A standalone ComfyUI packaging path.** It needs an external ComfyUI server regardless, so it stays
  dev-only. Ship an `httpx` wheel through the manifest only if standalone is actually wanted.
- **A plugin registry for op models.** Heightfield op models are imported into the contract union by a
  plain import, so extraction carries them. A registry earns its keep on the second consumer.

## The knob policy, because it is the thing most likely to be re-derived

On a non-destructive rebuild, **restore only the knobs the op did not explicitly set.** An op that
re-sends `density` wins, because the op is the intent; a knob the op leaves at its default keeps the
artist's sidebar tweak. Without that split, re-sent params would silently no-op.

The Blender 5.2 fact underneath it: **a Nodes modifier has no IDProperties at all** (`mod.keys()`
and `mod["Socket_1"]` both raise), so a knob's live value lives on the modifier's own input
interface, `mod.properties.inputs.<identifier>.value` — per object, since each build owns its group.
Snapshot and restore operate **there**, never on `mod[id]` and never on the node group interface
socket's `default_value`: that default only seeds a fresh modifier bind, and editing it after the
build does not re-evaluate. Restoring to the wrong one of the two silently drops every knob the
artist tuned.

One thing this does not remove: a **contract change still needs an MCP server reconnect**, because the
server parses `contracts.py` at startup. That is the tools-side half of a two-sided reload.
