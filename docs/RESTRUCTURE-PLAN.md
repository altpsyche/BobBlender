# Restructure Plan: Packageable Extension, Clean Architecture, External Asset Packs

Status: proposal. Written 2026-07-24 against `fix/audit-remediation`.

## Why

The suite works but is only *dev-installable* (symlink). It cannot be zipped and
handed to a user, and the layout confuses newcomers. Three names for "our code"
(`bbmcp`, `bobtools`, `bob_blender_tools`) read as three scattered systems when
they are really three runtime layers. This plan makes the extension a
self-contained, shippable deliverable; renames the misnomer; splits the monster
files; and defines an external asset-pack format so bulky art never lives in this
repo.

Non-goals: no behaviour changes to builders. Mostly a structure + packaging +
docs pass. One deliberate new capability is in scope: **GPU-accelerated terrain
is a first-class, required feature of the shipped extension** (P5), not an
optional extra. The extension owns GPU delivery end to end; the artist never
hand-installs anything. CPU remains only as the automatic correctness fallback
for machines with no compatible GPU.

## What is actually wrong (root causes, not symptoms)

1. **Extension is not self-contained.** It imports `bbmcp` as a top-level module,
   but `bbmcp/` lives at `blender/bbmcp/`, outside the extension folder. It only
   works because `server.py` inserts `blender/` onto `sys.path` via the resolved
   dev symlink (`blender/extensions/bob_blender_tools/server.py:47`). Zip the
   folder alone and every `from bbmcp...` import dies.

2. **`bbmcp` is a confessed misnomer.** It builds the whole suite (terrain,
   scatter, world, paths, materials), not MCP. `ARCHITECTURE.md` already flags the
   rename as deferred. The name is why the layout looks scattered.

3. **Asset paths are hardcoded to the repo layout.** `bbmcp/assets.py` walks
   `__file__` up three levels to reach `library/models` and `library/textures`
   (`assets.py:55`, `:64`). A packaged extension has no repo above it, so biomes
   and texture sets resolve to nothing. There is no way to point at an external
   art pack.

4. **Venv-only features are wired into the extension.** The terrain erosion bake
   and ComfyUI paths shell out to `tools/.venv/bin/python` (`__init__.py:506`,
   `_host_argv`). An installed extension has no venv, so these are dead for an
   end user and must degrade gracefully instead of erroring.

5. **Files are too large to read.** `materials.py` is 2394 lines; four panels are
   1000-1400 lines each. Hard to navigate, hard to review.

6. **Two import idioms for the same code.** Headless runners and the live
   extension both reach `bbmcp` by ad-hoc `sys.path` inserts from different
   depths. One canonical import path is needed.

## Target architecture

The organising rule: **the extension folder is THE product.** Everything the
Blender side needs at runtime lives inside it. The venv side (`tools/`) is the
dev/pipeline toolchain and is never shipped to an artist.

### Two runtime worlds (unchanged, made explicit)

```
tools/  (venv, Python 3.14, NO bpy)          blender/  (Blender's Python 3.13, bpy)
  the dev/pipeline toolchain                   the shippable extension + headless entry
  never zipped for artists                     the zip an artist installs
        |                                             ^
        |  JSON ops over socket / spawned process     |
        +---------------------------------------------+
```

The JSON op boundary and the "one core, swappable executors" model from
`ARCHITECTURE.md` stay exactly as they are. This plan does not touch the wire.

### Extension internal layout (the target)

```
blender/extensions/bob_blender_tools/     <- zip this; self-contained
  blender_manifest.toml
  __init__.py            <- thin: register/unregister, addon prefs, path setup
  core/                  <- was bbmcp: the bpy builder library (single source)
    __init__.py
    dispatch.py          <- op registry
    env.py  solar.py  world.py  mesh.py  images.py  proxies.py
    path_curve.py  util.py  assets.py
    materials/           <- split from the 2394-line materials.py
      __init__.py        <- re-exports the public surface (master_type, *_material, ...)
      env_state.py  weather.py  surface.py  terrain.py  water.py  shared.py
    geonodes/            <- recipes, unchanged
    heightfields/        <- vendored from tools/bobtools/heightfields (pure numpy;
                            runs on Blender's bundled numpy, no venv)
  ui/                    <- the panels (were *_panel.py at top level)
    __init__.py
    helpers.py           <- was ui_helpers.py
    world.py  firmament.py  scatter.py  shaders.py  splines.py
  bridge/
    server.py            <- the live socket server
  assets/                <- ONLY the tiny procedural defaults (blockout proxies)
    blockout/manifest.json
  presets.json
```

Why this shape:

- **`core/` replaces `bbmcp`.** Honest name, and as a namespaced subpackage
  (`bob_blender_tools.core`) it cannot collide on `sys.path`, so the "collision-
  proof name" rule is still satisfied without the MCP misnomer. Inside the
  extension every import is relative (`from ..core.dispatch import apply_op`), so
  it works under Blender's `bl_ext.*` namespace with no `sys.path` tricks.
- **`ui/`, `bridge/`, `core/` separate the three concerns** (draw, transport,
  build) that are currently flat. A reader sees the layers immediately.
- **`assets/` inside the extension holds only the procedural block-out** (no
  images, no `.blend`, nothing in LFS). Real art comes from external packs
  (below). The zip stays small.

### Headless side (venv-spawned reproducible builds)

`tools/bobtools/mcp/executor.py` spawns Blender with
`blender/runners/headless_build.py`. That runner is the ONE place that needs to
put the extension on the path:

```python
# blender/runners/headless_build.py
sys.path.insert(0, <path to blender/extensions>)     # resolved from config/env
from bob_blender_tools.core.dispatch import apply_op
```

So both executors reach the identical `core/` code: the live one by relative
import inside the loaded addon, the headless one by a single `sys.path` insert of
the extensions dir plus a plain package import. No more per-file depth-counting.

### The venv toolchain (`tools/`) — split by whether an artist needs it

`tools/` is the venv-side Python. Its parts do NOT share a fate: one is genuinely
dev-only, one must ship inside the extension. The dividing question is "does an
installed artist, with no repo and no venv, need this?"

- `tools/bobtools/mcp/` — the MCP framework and bus (contracts, executor, bridge
  client, server). **Dev-only, stays in the venv.** An artist installing the
  addon is not running an AI agent over a socket. Never zipped.
- `tools/bobtools/heightfields/` — the terrain compute capability. Numpy is the
  CPU backend and correctness reference; Blender's bundled Python already ships it
  (2.3.4 on 5.2). CuPy is the GPU backend. So this is **not** venv-only: it is
  in-process compute that runs inside Blender. **Its single source moves into the
  extension** at `core/heightfields/` (see "Single-source resolution" below — one
  committed copy, no duplicate, no vendor-on-build). A standalone install then
  generates and erodes terrain with no venv. The `auto` backend uses GPU (CuPy)
  whenever a device is present and CPU otherwise. GPU is a **required, first-class
  path** the extension installs for the user (P5); CPU is the automatic fallback
  for GPU-less machines only. This corrects an earlier draft that wrongly filed
  heightfields as venv-only.
- `tools/bobtools/comfyui.py` — needs an external ComfyUI server to be running, so
  it is a preference-gated extra either way; the code can be vendored but the
  feature is off unless a server URL is set.
- `tools/bobtools/{erosion,naming,scaffold,setup,config}.py` — repo/pipeline
  utilities, dev-only.

### Single-source resolution (heightfields compute)

Decided: **one committed copy, at `core/heightfields/`. No second copy, no
vendor-on-build, no checksum.** Vendor-on-build was rejected because live dev
builds load the extension straight from the repo — the copy must exist in git,
not appear at build time — and two committed copies only invite drift.

- The compute lives at `blender/extensions/bob_blender_tools/core/heightfields/`
  and is the sole authored source.
- The venv reaches the same code by putting the extensions dir on `sys.path` (the
  single insert the headless runner already needs, see below) and importing
  `bob_blender_tools.core.heightfields`. `tools/bobtools/heightfields/` is deleted.
- Golden tests move to import from `core.heightfields`; `tools/tests/test_heightfields.py`
  currently does `from bobtools import heightfields` — repoint it. CPU results stay
  the bit-reproducible reference and now test the exact shipped code, so no
  cross-copy checksum is needed.
- The build zips `core/heightfields/` as-is (it is already inside the extension) —
  nothing to vendor.

## Asset packs (external, never in this repo)

Today art lives in `library/` (30 MB of textures already, via LFS) and is found
by walking the repo tree. For a shippable product, art must be:

- **external** (a user brings their own or downloads a pack),
- **discoverable** (dropped in a folder or pointed at by a preference),
- **not in this git repo** (keeps the repo and the zip lean).

### Pack format

An asset pack is a plain folder (optionally distributed as a `.zip`):

```
forest-scandinavia/                <- one pack
  pack.json                        <- the manifest (see below)
  models/<biome>/manifest.json     <- biome definitions (existing biome_manifest shape)
  textures/<set>/                  <- grass_basecolor.jpg, grass_normal.png, ...
  hdri/<name>.exr
```

`pack.json`:

```json
{
  "schema": 1,
  "id": "forest-scandinavia",
  "name": "Scandinavian Forest",
  "version": "1.0.0",
  "author": "…",
  "license": "CC0",
  "provides": { "biomes": ["birch_glade"], "texture_sets": ["grass","rock","soil"], "hdri": ["overcast_4k"] }
}
```

This reuses the existing `biome_manifest()` / `validate_biome()` reader
(`assets.py`) unchanged — a biome folder inside a pack is exactly today's
`library/models/<biome>/`. Only the *root* changes from "the repo" to "a
registered pack".

### Resolution

Replace the `__file__`-walk in `assets.py` with a resolver over an ordered search
path:

```python
def asset_roots() -> list[Path]:
    # 1. every folder in $BOB_ASSET_PACKS (os.pathsep-separated)
    # 2. the addon preference "Asset Pack Folders" (a UIList of paths)
    # 3. the bundled defaults inside the extension (core-relative assets/)
```

`biome_dir(name)`, `_textures_root()`, etc. become searches across
`asset_roots()`, first hit wins, with the bundled block-out always present as the
floor. A new operator **Rescan Asset Packs** (in the Advanced panel) refreshes the
biome enum. In dev, `library/` is registered as one more pack root via
`$BOB_ASSET_PACKS=<repo>/library`, so the current workflow is unchanged for us.

### Distribution

- The extension zip ships with the block-out only.
- Real packs are published separately (their own repos / release downloads), each
  a zip a user extracts and points the preference at.
- `library/` in this repo becomes "the dev asset pack" and can later move to its
  own repo without touching extension code, because the extension only knows the
  search path, never `library/`.

## API documentation (after the structure fix)

Once `core/` is stable, produce `docs/API.md` (and keep it honest by generating
the reference parts from code):

1. **Op vocabulary** — the MCP-facing API. Generate a table from the Pydantic op
   models in `tools/bobtools/mcp/contracts.py` (name, fields, types, defaults) and
   cross-link each to its handler in `core/dispatch.py`. A ~50-line introspection
   script in `tools/scripts/gen_api_docs.py`, run in the venv, no new deps
   (Pydantic already there).
2. **Builder/core API** — the public surface of `core/` a recipe author calls
   (`dispatch.apply_op`, `materials` public functions, `env.get_env/sun_params`,
   the geonodes recipe contract). Authored, with the "to add an op" recipe from
   `ARCHITECTURE.md`.
3. **Asset pack spec** — the `pack.json` schema, the biome manifest shape (from
   `validate_biome`), and the search-path rules. This is the doc a pack author
   reads.
4. **Extension surface** — panels, operators (`bob_blender_tools.*`), scene props
   (`bbt_*`), and addon preferences. A short authored map.

Docstrings become the source of truth: each op model and each public `core`
function carries a one-line docstring the generator lifts.

## Phased execution

Each phase is independently landable and testable headless (a Blender 5.2 binary
is in the CLI env; measure geometry, do not just `py_compile`).

**P0 — Prove the import move (no rename yet).**
Move `blender/bbmcp/` to `blender/extensions/bob_blender_tools/core/` in place but
keep the module importable as `bbmcp` via a one-line shim, so nothing breaks.
Fix the two `sys.path` sites (`server.py`, `headless_build.py`) to the new single
path. Run the headless build + a live build. Green = the code is co-located.

**P1 — Relative imports + rename to `core`.**
Rewrite all `import bbmcp` / `from bbmcp...` to relative `..core...` inside the
extension; update the headless runner to `from bob_blender_tools.core...`. Delete
the shim. Update `contracts`/`dispatch` docstrings and `ARCHITECTURE.md` naming
section. **Also update the Reload-Builders purge in `bridge/server.py` (currently
matches the literal string `"bbmcp"`, `server.py:174`) to the new package name, or
the live reload silently stops purging.** This is the "kill the misnomer" phase.
**Add a CI lint guarding the dual name.** `core/` is loaded under two
fully-qualified names — `bl_ext.<repo>.bob_blender_tools.core` live, and
`bob_blender_tools.core` headless — so relative imports work in both worlds but any
*absolute self-import* (`import bob_blender_tools...` or `from bob_blender_tools...`
from inside `core/ui/bridge`) breaks one of them silently. Add a grep-based CI
check that fails on absolute self-imports inside the extension package; internal
code must stay relative. The current `bbmcp` code is already clean (verified: only
relative `from . import ...`), so this locks in the good state before P2 moves
files around.

**P2 — Internal layout: `ui/`, `bridge/`, split `materials/`.**
Move the panels into `ui/`, `server.py` into `bridge/`, and split `materials.py`
into the `materials/` subpackage with a re-export `__init__.py` so callers keep
`from ..core.materials import surface_material`. Pure mechanical moves guarded by
the headless material-build test. **Gate: before splitting, map `materials.py`'s
real internal coupling** (2394 lines; the proposed env_state/weather/surface/
terrain/water/shared seams are an unverified first cut). Draw the actual call/
symbol graph and cut on the true low-coupling seams — do not force the guessed
boundaries. If a clean cut isn't obvious, land fewer, larger submodules rather
than a tangle of cross-imports.

**P3 — Asset resolver + pack format + writable output dir.**
Add `asset_roots()` and the resolver, the addon-preference path list, the
`pack.json` reader, and **Rescan Asset Packs**. Register `library/` as a dev pack
via env. Ship `assets/blockout/` inside the extension. Verify biome build resolves
from a pack root outside the repo.
Also add a **writable output location** for generated data: the heightfield bake
currently writes to `<repo>/library/_generated/` (`__init__.py:620`), which does
not exist on an installed machine. Add an addon-preference "Output Folder"
(default: a per-OS user cache dir, or the current .blend's folder if saved), route
the bake write and the subsequent `reload_image` op through it, and keep the
`basename`-only guard so a free-text target cannot escape it.

**P4 — Move heightfields in-process (single source); gate only the true extras.**
Move `bobtools/heightfields/` to `core/heightfields/` as the sole committed copy
(per "Single-source resolution" above — no duplicate, no vendor-on-build), run it
on Blender's bundled numpy, so the terrain generate/erode bake works with no venv.
Make the bake path call the in-process compute instead of shelling to
`tools/.venv` (replaces `_host_argv` / `_run_host_bake`, `__init__.py:500`+). The
backend stays `auto`: it uses the GPU (CuPy) whenever a device is present and falls
back to CPU otherwise. CPU is the correctness reference and the automatic fallback
for GPU-less machines, never a mode the artist picks. GPU delivery is P5 and is
required, not optional.
Repoint the venv and golden tests at the moved source: delete
`tools/bobtools/heightfields/`, add the extensions dir to the venv's `sys.path`,
and change `tools/tests/test_heightfields.py` from `from bobtools import
heightfields` to `bob_blender_tools.core.heightfields`. One source now tests
exactly the shipped code; CPU results stay bit-reproducible with no cross-copy
checksum.
The ONLY features gated off in a standalone install: **ComfyUI** (needs `httpx`,
not bundled in Blender's Python, plus an external ComfyUI server — keep it
venv/dev-only unless we choose to ship an `httpx` wheel) and the **MCP bridge
autostart** (an agent-authoring feature; default OFF for artists, on-demand in the
Advanced panel). Disable those buttons with a clear note rather than erroring.
Note GPU is explicitly NOT in this gated list — it is a required capability
(P5).
**Required P4 code change (from the pre-P0 spike): PIL-free PNG I/O.**
`heightfields/io.py` imports `from PIL import Image` at module top, and Blender's
bundled Python has no PIL, so the package cannot import in-Blender as-is. Replace
the PIL PNG read/write with a **pure-numpy(+zlib) 16-bit-grayscale PNG codec** — no
PIL, no bpy (the compute must stay bpy-free). The spike confirmed everything else
already works: the compute imports and runs under Blender 5.2 and is byte-for-byte
identical to the venv golden across numpy 2.3.4 vs 2.5.1, and `auto` correctly
falls back to CPU in Blender. So beyond this codec, P4 is a move + repoint. See
`docs/PRE-P0-SCOPE.md` "Spike results".

**P5 — GPU acceleration as a REQUIRED, first-class capability. (Not optional; ships in v1.)**
GPU is a shipped feature of the extension, not an add-on the user finds later.
Because CuPy is CUDA/ROCm-version-specific and too large to bundle in the zip
sanely, "required" means **the extension owns GPU delivery end to end** via a
guided install — never a manual expert step. Any user with capable hardware gets
GPU-accelerated terrain from inside the addon, out of the box. This phase is a
release gate for v1, not a follow-up.

- **Detect on load, and prompt**: probe for an NVIDIA GPU + CUDA runtime (and AMD +
  ROCm), determine the CUDA major line (11.x vs 12.x), and whether `cupy` already
  imports in Blender's Python. If capable hardware is present and GPU is not yet
  enabled, surface the enable action prominently (not buried) — the default path
  actively steers a GPU user to turn it on, rather than leaving it dormant.
- **Enable GPU Acceleration** action in the Terrain panel: with the user's consent
  (required — writing into Blender's Python and downloading a wheel need it),
  `pip install` the matching `cupy-cudaXXx` (or ROCm) wheel into Blender's bundled
  Python, then verify a real device round-trip. One click, no shell knowledge,
  clear progress and error text.
- **Auto-use after install**: the `auto` backend picks the GPU on the next bake; no
  toggle to flip. Status line shows the live device ("GPU: RTX 4090" / "CPU").
- **CPU fallback is automatic and silent** ONLY when no compatible GPU or driver is
  present — the artist is never blocked and never has to choose a "mode". CPU is a
  fallback for GPU-less machines, not the intended path for a GPU machine.
- The installer is resilient: network failure, wrong CUDA line, or a missing
  driver degrades to CPU with a specific message, never a crash.
- **Acceptance for the phase**: on a CUDA machine the guided install completes and
  a bake runs on the GPU with a device round-trip verified; on a GPU-less machine
  the same build bakes on CPU with no error. Both are required to close P5.

**P6 — Manifest permissions + build + release.**
Declare `[permissions]` in `blender_manifest.toml` before building: `network`
(the bridge socket AND the P5 GPU-wheel download) and `files` (asset reads, bake
writes, installing the wheel into Blender's Python) — undeclared, Blender 5.2 flags
or restricts them. Then
`blender --command extension build --source-dir blender/extensions/bob_blender_tools`
produces `bob_blender_tools-<version>.zip`. Add a `tools/scripts/build_extension.py`
wrapper that stamps the version and runs `blender --command extension validate`.
No vendoring or checksum step — `core/heightfields/` is already the single
committed source inside the extension (P4), so the zip includes it as-is.
**Release gate: P5 GPU delivery must be functional before a v1 tag** — GPU is a
required capability, so a build that cannot install/verify GPU on capable hardware
is not v1-shippable.
Document install: Preferences → Get Extensions → Install from Disk. Decide the
channel: self-hosted zip (now) vs the extensions.blender.org platform later (needs
their review + compatible tags/license).

**P7 — API docs.**
Write `tools/scripts/gen_api_docs.py` and author the four sections above into
`docs/API.md`. Wire into the release script so a build refreshes the op table.

## Risks / decisions to confirm

- **Fold vs keep-separate `bbmcp`.** This plan folds it into the extension as
  `core/`. Polyrepo extraction later becomes "move the extension folder", which is
  cleaner than today's split. If we instead want `bbmcp` to stay a standalone
  library for a separate `BobBlenderMCP` product, we keep it separate and add a
  vendor-on-build step (copy + import-rewrite into a staging dir). Recommendation:
  fold — one source of truth, no drift, trivial zip.
- **Materials split boundaries.** The proposed `materials/` submodules
  (env_state/weather/surface/terrain/water/shared) are a first cut; confirm the
  seams against the real coupling before P2.
- **Preference persistence.** Asset-pack folder list and Output Folder live in
  `AddonPreferences`; confirm they survive the packaged (non-symlink) install and a
  version update.
- **Old-scene migration.** The rename is Python-module-level; scene props (`bbt_*`),
  node groups, and drivers key off bpy data names, not Python paths, so pre-refactor
  `.blend` files should keep working. Confirm with a smoke test: open a scene saved
  before the refactor, re-run a build, diff the geometry.
- **ComfyUI packaging.** Recommendation: keep venv/dev-only (it needs an external
  ComfyUI server regardless). Ship an `httpx` wheel via manifest `[build] wheels`
  only if standalone ComfyUI is actually wanted.
- **GPU installer robustness (P5) — highest-risk item, and now a release gate.**
  GPU is required, so this surface must be solid, not best-effort. The one-click
  CuPy install must handle the matrix: NVIDIA CUDA 11.x vs 12.x, AMD ROCm,
  no-driver, offline, and a Blender Python that blocks writes to its site-packages.
  Each failure degrades to CPU with a specific message (CPU fallback is the safety
  net for these failures and for GPU-less machines — it is NOT a reason to treat
  GPU as skippable). **De-risk first:** before committing to the phase, spike the
  two unknowns — target CUDA-line detection, and pip-into-Blender-Python — on a
  real CUDA box, because the whole "required GPU" promise rests on them. Verify the
  wheel install survives a Blender version update (bundled Python may change).
  Decide the ROCm/AMD support tier explicitly (fully supported vs CPU-only for now)
  rather than leaving it implied.

## Outcome

- One deliverable: a self-contained `bob_blender_tools` zip, block-out included,
  installable by any user.
- One honest name for the bpy library (`core`), reached one way.
- Files small enough to read; concerns separated into `core/ui/bridge`.
- Art lives outside the repo in versioned packs, brought in by the user.
- Terrain compute is one committed source (`core/heightfields/`), shared by the
  in-Blender path and the venv golden tests — no duplicate, no drift.
- GPU-accelerated terrain out of the box: required first-class capability, guided
  install owns the whole experience, automatic CPU fallback only where no GPU.
- Reference docs generated from code so they stay true.
