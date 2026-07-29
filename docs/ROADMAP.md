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

## Closed: the in-Blender test job

`tools/tests/run_blender_tests.py` exists and `.github/workflows/ci.yml`'s `blender-headless` job is
on. It drives the gates that need `bpy` and nothing else — the sun gate, the texture-set check and
the scene-seams gate — for **85 checks in about 6 s of Blender**, one process per gate. Everything in
`tools/tests` still runs in the plain venv against the pure-python core, which is what keeps the fast
gate honest without Blender; this is the second job beside it rather than a replacement.

What is still not in CI, and the rule that decides it: a gate qualifies only when it needs `bpy` and
nothing else. `headless_foliage.py` is the one that hurts — it is the largest gate in the repo — but
it takes 150 s and reaches for ComfyUI when one is running, so on a CI box it would be slow AND
measuring something different from what it measures locally. The generation gates are out for the
same reason, one step further: they need a GPU and a ComfyUI. Those numbers still come from a local
run, and the way to bring one in is to make it need less, not to let CI skip it quietly.

## Open: the one thing the asset gate measured and did not fix

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

## Open: the block-out control route paints black, and the walls do not survive it

A second artist rejection sent a gabled timber structure down the control-conditioned route
(`make_blockout` → `export_control` → `comfy_mesh(control=..., control_mode="point")`, chosen by the
artist over building the structure from recipes). Two thirds of it works and the remaining third
blocks the asset.
Measured on one generation, seed 71, against a screened reference:

- **The conditioning holds what it promised.** The finished mesh is 8.83 × 7.09 × 7.50 m against the
  block-out's 8.74 × 7.08 × 7.50, so the footprint and height are the layout's rather than the
  generator's. The clay renders are the argument worth keeping, and they are local rather than
  tracked (`renders/` holds only a `.gitkeep`), so the comparison is stated here rather than cited:
  the free generation is a rounded loaf with bulging walls, a lumpy ridge and no planar surface
  anywhere, which is exactly what the artist rejected; the conditioned one has flat roof planes, a
  straight ridge and a crisp eave edge. The defect the rejection named is a route problem and this
  route fixes it.
- **`mesh_texture` returns an all-black texture set on this route.** Measured on the staged GLB it
  writes: both images 2048 square, mean 0.00, standard deviation 0.00, against 56.50 and 80.89 for
  the same graph on the free route's own generation. The bake then carries the black through
  faithfully, which
  is why every other figure in the receipt is healthy. NOT diagnosed further: the next step is the
  graph itself, and the suspect is the frame the mesh arrives in, because this route is the one
  whose output is turned (`gen_assets.CONTROL_RETURN_TURN`) and whose black-albedo failure mode
  already has a known cause on the sibling routes — an input the encoder cannot see comes back as a
  plausible nothing rather than an error (`export_control`).
- **The wall and roof split does not survive.** The generation is an A-frame: the roof planes run to
  the ground and the block-out's walls, doorway and jamb are gone. So the point control carries
  extent and silhouette and does not carry interior structure. Worth trying before concluding: fewer
  overlapping solids in the block-out (the jamb boxes and the wall box's hidden top face are sampled
  area-weighted along with everything else), `control_mode="voxel"` which encodes a coarse ground
  plan rather than a surface, and a higher guidance scale.

If the texture pass cannot be made to paint this route, the artist's second option — building the
structure from recipes and generating only its textures — is the answer, and it needs the brief amended
because [SCENE-BRIEF.md](../references/SCENE-BRIEF.md) says structures come from `comfy_mesh`.

Two smaller calls were made deliberately and are recorded where they apply rather than here:
`profile_segments` stays 5 on the shipped conifer, because the shear that argued for raising it is
fixed and the remaining argument is faceting at hero distance (FOLIAGE.md); and `AO_STRENGTH` stays
at 0.6 with Albedo × AO unchanged, because the suspected double-count measured as absent — the AO's
source field is a high-pass at a thirty-second of the image and the delighting corrects at an
eighth, so the two cannot overlap (GENERATION.md, and the comment in `core/materials/texset.py`).

## Open: three generation bars rest on a single batch, and say so at the constant

Every other bar in the generation vocabulary is set from a spread wide enough to argue with —
`gen_receipt.METALNESS_MAX` from ten samples spanning 0.0002 to 0.83, `gen_receipt.MAP_SPREAD_MIN` from seven
maps spanning 0.00 to 57.51. Three are not, and each of them caught a real defect, so they stay as
gates. What they need is a second batch to be re-derived from, and until then the thinness of the
evidence is written beside the number rather than left for a reader to discover:

| Constant | Points behind it |
|---|---|
| `gen_receipt.LEAF_RAMP_STOPS_MAX = 0.55` | 2, and one is synthetic: 0.48 is the worst real cell over three atlases, 0.60 is that same cell with a half-stop key painted onto it |
| `gen_receipt.SEETHROUGH_OPENING_FRACTION = 0.10` | 2: a ground rock's 0.07 opening the artist accepted as vesicular stone, and a gabled structure's 0.18 the artist rejected in a render |
| `comfy_maps.AXIS_STRONG_TAPER_MAX = 0.25` | 1, and already marked provisional at the constant |

Recalibrating needs generation, not code: a second batch of atlases for the first, a second batch of
structures and rocks for the second. Both cost ComfyUI time and neither is blocked on anything else.

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
