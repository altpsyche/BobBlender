# Handover: full scene over MCP — expose the whole BobBlenderTools suite as ops

Paste this into a fresh chat. It is the task spec. Written 2026-07-25 against branch
`fix/audit-remediation`, Blender 5.2 LTS only. Caveman ultra mode (terse chat; code/commits/docs
written normally). User commits — do NOT commit unless asked.

## Where things stand (context)

The self-contained MCP work (P8) is DONE and uncommitted on this branch: the agent-side MCP server
ships inside the extension at `blender/extensions/bob_blender_tools/mcp_agent/` (server, contracts,
executor, bridge, paths), runs standalone with no repo, and the headless runner moved into the
extension at `.../runners/headless_build.py`. See `docs/MCP.md`. Lint 0, 71 pytest green.

A live scene test then drove the running Blender over MCP and PROVED the pipeline works end to end:
- `bake_heightfield` (alpine preset, GPU/cuda) wrote a 16-bit PNG.
- `build_live` authored into the running session: `build_sky`, `build_geonodes` (heightmap_terrain),
  `make_proxies`, three `scatter` layers (slope + height + noise masks), `make_path` (draped trail).
  Every op returned `ok`.
- A beauty shot rendered (`library/_generated/alpine_render.png`): eroded ridgelines, trees
  respecting a treeline, scree rocks, an evening sky.

**But the scene is a fraction of the suite, and it was ugly-white**, because the terrain had no
material and no biome. That is the gap this task closes.

## The core problem

The MCP op vocabulary is 8 low-level ops (`core/dispatch.py` `_HANDLERS`): `add_mesh`,
`build_geonodes`, `make_proxies`, `make_path`, `drape_curve`, `inspect_river`, `reload_image`,
`build_sky`. Everything that makes a scene *beautiful and complete* lives in ~40 panel **Operators**
that an agent cannot reach over the socket:

- **Shading / materials (BobShaders, `ui/shaders.py`, docs/SHADERS.md):** `shaders_new`,
  `shaders_convert`, `shaders_preset`, `shaders_terrain_add`, `shaders_terrain_layer_preset`,
  `shaders_terrain_stack_preset`, `shaders_biome_terrain`, `shaders_snow_shell_add`. **This is why
  the terrain rendered white — no material op exists.**
- **Biome (`ui/world.py`, docs/BIOME-SYSTEM.md / BIOME-BLOCKOUT-REDESIGN.md):** `world_apply_biome`,
  `world_biome_world`, `shaders_biome_terrain` — one call that shades terrain + sets scatter recipe +
  applies world/env for a named biome. Panel-only.
- **Typed paths / water (BobSplines, `ui/splines.py`, docs/SPLINES.md):** `curve_add` (typed:
  dirt path / road / river / stream), `curve_build`, `curve_build_all`, `curve_bake_erode`,
  `curve_revert_erode`. The current `make_path` op is only a raw NURBS + drape; it does NOT type a
  curve, carve the terrain (curve_overlay), build the water ribbon (curve_water), or erode.
- **Atmosphere (BobFirmament, `ui/firmament.py`, docs/FIRMAMENT.md):** `firmament_build_sky`,
  `firmament_build_clouds` / `firmament_cloud_preset`, `firmament_build_fog` / `firmament_fog_preset`,
  `firmament_build_rain`, `firmament_build_motes`, `firmament_build_snow_cover`,
  `firmament_apply_season`, `firmament_scene_preset`. Panel-only. (The MCP `build_sky` is the older
  `core/world.py` sun/sky, NOT the richer Firmament sky.)
- **World / shared env (`ui/world.py`, `core/env.py` `Scene.bbt_env`):** season / weather /
  time-of-day master state that every consumer reads. Panel-only.

An agent driving MCP today can make mountains + raw scatter + a plain sun. It cannot shade, apply a
biome, build a river, add weather/clouds/fog/snow, frame a camera, render, or delete anything.

## Issues I hit building the scene (fix these)

1. **No material/shading op** — terrain is untextured white. (Covered by Goal 2 shading ops.)
2. **No biome op** — cannot apply a biome look in one call.
3. **No camera op and no render op** — I had to hand-write a headless render script outside MCP.
   An agent cannot compose a shot or capture a result over MCP.
4. **No delete / clear op** — a stray `add_mesh` (I left a `__mcp_ping__` plane) cannot be removed;
   there is no way to reset the scene.
5. **Stale connected server** — the session's `bobblendermcp` was the pre-refactor build, so MCP
   `build` (headless) failed pointing at the old `blender/runners/headless_build.py` path. `bake` and
   `build_live` worked. **Rule:** after the P8 move, the MCP server must be reconnected
   (`/mcp reconnect bobblendermcp`) to load the new executor. Document + verify.
6. **Stale tool docstrings** — `mcp_agent/server.py` tool descriptions still say "repo-relative" and
   "Runs in the venv" in places surfaced to the client. Make them say workdir-relative / in-process
   and match the new behaviour, so an agent is not misled about paths.
7. **`build_geonodes` result dupes** — `created` comes back as `["Terrain","Terrain"]` /
   `["ScatterTrees","ScatterTrees"]` (object name listed twice, likely object + node group). De-dup or
   label them (object vs node group) so results are readable.
8. **Scatter binding by bare name is fragile** — `emitter` / `assets` are passed as object /
   collection name strings; a typo silently scatters nothing. A biome-driven scatter op removes most
   of this hand-wiring; also consider returning a warning when a named emitter/collection is missing.

## Goal — a full scene, entirely over MCP

**Definition of done:** from an MCP client (no panel clicks, ideally from OUTSIDE the repo), an agent
can build a complete, beautiful scene using ONLY BobBlenderTools ops: eroded mountain terrain, a
**shaded** terrain material (or a full **biome** applied), multi-layer scatter, a **typed river**
that carves the terrain and has a water surface, a **path**, **atmosphere** (sky + clouds + fog or
weather), **snow** where appropriate, a **camera**, and a **render** to a file — then confirm it
looks good (rendered, not just asserted).

The work is mostly **exposing existing logic**, not writing new features. The features exist as panel
Operators; they must become MCP ops.

### The architecture principle (confirm, then apply consistently)

Each panel Operator's `execute()` currently holds the orchestration inline (bpy). Refactor the shared
logic OUT into a `core/` function that takes a plain params dict and returns a result dict — the same
shape a dispatch handler needs — then:
- the panel Operator calls that core function (thin `execute` = read props → call core → report), and
- a new `_HANDLERS` entry in `core/dispatch.py` calls the SAME core function.

One function serves both the button and the op. This matches the repo's "route through shared core,
subtract duplication" preference (see the UI-subtraction memory and docs/UX-REDESIGN.md). It also
keeps the socket safe: the bridge only runs whitelisted `_HANDLERS`, and each new op is validated by a
Pydantic model in `mcp_agent/contracts.py`.

Some `core/` logic already exists (core/world.py sky, core/path_curve.py, core/proxies.py); the new
work is core-ifying `ui/shaders.py`, `ui/world.py` (biome), `ui/splines.py`, `ui/firmament.py`, plus
small new `core/` modules for camera + render + scene ops.

### Ops to add (grouped; confirm scope/priority with the user first)

Recommend building in this order — each layer makes the scene visibly better:

1. **Shading (highest value — kills the white terrain):**
   - `shade_terrain` — build/assign a BobShaders terrain material on an object, from a layer/stack
     preset (soil/grass/rock/cliff/scree/sand). Wraps `ui/shaders.py` terrain builders.
   - `make_material` / `apply_shader` — a BobShader on an object from a preset. Wraps `shaders_new`
     / `shaders_preset` / `shaders_convert`.
   - `snow_shell` — the snow-line shell modifier. Wraps `shaders_snow_shell_add`.
2. **Biome (one call that shades + scatters + sets world):**
   - `apply_biome` — name a biome; build biome terrain material, biome scatter, and world/env. Wraps
     `world_apply_biome` + `shaders_biome_terrain` + `scatter_biome_scatter`. Uses the pack resolver
     (`core/assets.py`) so it works against `$BOB_ASSET_PACKS` and the bundled block-out biome.
   - `list_biomes` MCP tool (thin, read-only) so an agent can discover names (the standalone
     `list_library_assets` already returns biomes — confirm it is enough).
3. **Typed paths + water (BobSplines):**
   - `make_curve` (typed: dirt_path / road / river / stream / ridge) — replaces raw `make_path` for
     typed use; wraps `curve_add` + `curve_build`.
   - `carve_terrain` / `bake_erode` — carve the terrain to the curve network and optionally erode.
     Wraps `curve_bake_erode` / `curve_revert_erode`. A river then has a real bed + `curve_water`
     surface + water shader.
4. **Atmosphere (BobFirmament):**
   - `build_sky` (upgrade to the Firmament sky, or add `firmament_sky`), `build_clouds`,
     `build_fog`, `build_weather` (rain/motes), `snow_cover`, `apply_season`, `scene_preset`. Wrap the
     `firmament_*` operators. `scene_preset` alone can set a whole mood in one op.
   - Wire the shared env (`Scene.bbt_env`): an `set_env` op (season/weather/time/wind) so downstream
     consumers (sky look, snow line, wetness) update coherently.
5. **Camera + render + scene control:**
   - `add_camera` — create/position a camera (location + look-at target, lens), set as scene camera.
   - `render` — render the current scene (engine, resolution, samples, output path) to a file;
     return the path. This closes the loop so an agent can SEE its result over MCP.
   - `delete` / `clear_scene` — remove named objects or reset to empty. Fixes the stray-object issue.
   - Optional `frame_object` / `look_at` helper for framing.

Keep every op's params a Pydantic model in `mcp_agent/contracts.py`, add it to the `Operation` union,
add the `_HANDLERS` entry, and regenerate the API table (`gen_api_docs.py`). `build` and `build_live`
are generic over ops, so no new MCP *tools* are needed for these — they ride the existing op list.
(Add new MCP *tools* only for read-only discovery like `list_biomes`, or for `render` if it should be
a first-class tool rather than an op.)

## Decisions to confirm before building

- **Scope for v1.** All five layers, or shading + biome + camera/render first (the minimum for a
  beautiful full scene) and paths/atmosphere second? Recommend: layers 1, 2, 5 first (shaded biome
  terrain + scatter + camera + render = a complete beauty shot), then 3 and 4.
- **`build_sky` upgrade vs add.** Replace the old `core/world.py` sky with the Firmament sky (one
  sky), or keep both and add `firmament_sky`? Recommend upgrading `build_sky` to Firmament and keeping
  the old builder as the fallback it already is.
- **Render as op or tool.** `render` as an op in the list, or a standalone MCP tool `render_scene`?
  Recommend a tool (it returns a file path the agent reads, distinct from build).
- **How far to core-ify.** Full extraction of every operator, or wrap the operator by invoking
  `bpy.ops.bob_blender_tools.<x>()` from the handler? Recommend real extraction for the shading/biome/
  spline logic (testable, clean), but note `bpy.ops` invocation is an acceptable fast path for the
  operators whose logic is purely orchestration — confirm the tradeoff.

## Constraints + verification

- Blender 5.2 only. A 5.2 binary is at `~/.steam/steam/steamapps/common/Blender/blender` — the live
  bridge on `127.0.0.1:9876` is the running session. Drive it with `build_live` to test, and render
  headless to MEASURE the result (open the .blend, render a camera shot, view the PNG). EEVEE engine
  enum in 5.2 is `BLENDER_EEVEE` (not `_NEXT`); Cycles GPU is available (cuda) for a nicer shot.
- Reconnect the MCP server after any contract/handler change (`/mcp reconnect bobblendermcp`), and
  Reload Builders after a recipe/builder body change (docs/ARCHITECTURE.md two-sided reload).
- Lint 0: `python tools/scripts/check_selfimports.py`.
- Tests: `uv run --with pytest --extra all --extra gpu --project tools pytest tools/tests -q` (71
  baseline). Add tests for new core functions (they are bpy-side; test the pure params→plan pieces
  where possible, and add a headless smoke that builds + measures a scene).
- Regenerate the API table after touching contracts: `uv run --project tools python
  tools/scripts/gen_api_docs.py`.
- **Prove it:** one MCP session (or one `build`/`build_live` op list) that produces the full scene,
  then a render that looks good. Save the op list and the render under `library/_generated/`.
- Caveman ultra for chat; code/commits/docs normal. Do not commit unless asked.

## Key files

- Ops boundary: `blender/extensions/bob_blender_tools/core/dispatch.py` (`_HANDLERS`), contract
  `blender/extensions/bob_blender_tools/mcp_agent/contracts.py`, tools
  `blender/extensions/bob_blender_tools/mcp_agent/server.py`.
- Logic to core-ify: `ui/shaders.py` (shading), `ui/world.py` (biome + env), `ui/splines.py`
  (typed curves + carve/erode), `ui/firmament.py` (atmosphere), `ui/scatter.py` (biome scatter).
  Existing core: `core/world.py`, `core/path_curve.py`, `core/proxies.py`, `core/assets.py`,
  `core/env.py`, `core/geonodes/recipes/` (scatter, heightmap_terrain, curve_water, curve_overlay,
  snow, volumetrics).
- Docs: SHADERS.md, BIOME-SYSTEM.md, BIOME-BLOCKOUT-REDESIGN.md, SPLINES.md, FIRMAMENT.md,
  SCATTER-SHADING-UX.md, TERRAIN.md, ARCHITECTURE.md, MCP.md, API.md. Update MCP.md + API.md with the
  new ops; add a "full scene" worked example to MCP.md.
- New core modules to add (suggested): `core/shading.py`, `core/biome.py`, `core/splines_build.py`,
  `core/atmosphere.py`, `core/camera.py`, `core/render.py`.

## Done criteria

1. New ops exist and are whitelisted: shading, biome, typed path/river + carve, atmosphere, snow,
   camera, render, delete/clear — validated by contract models, documented in API.md + MCP.md.
2. Panel Operators and MCP ops share one `core/` function each (no duplicated orchestration).
3. Fixes landed: readable `build_geonodes` results, refreshed tool docstrings, delete/clear op, the
   reconnect rule documented, missing-emitter/collection warnings.
4. An agent builds a FULL scene over MCP alone (mountains + shaded biome terrain + scatter + typed
   river with water + path + atmosphere + snow + camera) and renders a good-looking result —
   demonstrated with the op list and the render committed to `library/_generated/`, not just asserted.
5. Lint 0, pytest green (plus new tests), API table current. Nothing committed unless the user asks.
