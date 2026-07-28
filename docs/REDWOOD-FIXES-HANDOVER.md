# Handover: finish the redwood-run fixes, then open the foliage-generator track

## Where this picks up

Repo `/home/siva/dev/BobBlender`, branch `main`. A previous session worked through the thirteen
defects the redwood-scene run exposed (docs/GENERATION.md, "What the redwood-scene run found
(2026-07-27)"). Ten are landed and verified; three generation-track items, all the documentation
moves, and the new foliage plan are not.

**Nothing is committed.** The working tree carries 21 modified files plus one new file
(`core/describe.py`), about 1,100 lines. Start by reading the diff — the intent of every change is
in the code comments, and this document does not repeat them.

Verified green at handover: 23/23 checks in the headless probe, 213/213 in `tools/tests`, and
`tools/scripts/check_selfimports.py` clean.

## Ground rules (unchanged from the original handover)

- A Blender 5.2 binary is in the CLI env at
  `~/.steam/steam/steamapps/common/Blender/blender`. Measure headlessly rather than reasoning about
  bpy. `uv run --project tools bob-mcp` is the agent server; the live bridge is :9876 when Blender
  is open.
- Every fix carries a check that would have failed before it. **This debt is currently outstanding
  and is the first task below.**
- ComfyUI is at `/home/siva/dev/ComfyUI` (`./venv/bin/python main.py`). Check
  `comfy_status().vram_free_mib` (needs ~5 GB) before any `comfy_mesh`. The preflight for this now
  exists (D15, landed), so a short card should now fail with a sentence rather than a CUDA
  traceback — worth confirming against a real server, since it has only been read, not run.

## What landed, and what the fix actually turned out to be

Read this section for the root causes, because in four cases the diagnosis in GENERATION.md named a
symptom whose cause was somewhere else.

1. **Generated texture sets unreachable (item 3 in the doc's numbering).** Two stacked bugs.
   `ApplyTextureSet` never declared `pack_dir`, so pydantic's `model_dump` silently dropped the
   field `comfy_texture_set` was already returning in its `apply_op` — the op could not see the pack
   no matter what the tool returned. Separately, `assets.texture_set_maps` derived file stems from
   the folder name, so the run's symlink workaround resolved zero maps and the layer rendered as a
   solid tint with success in every receipt. Fixed: `pack_dir` on the contract and honoured by the
   op via a new `assets.add_pack_root` (op roots rank above the addon preferences and below
   `$BOB_ASSET_PACKS`); role-suffix map resolution with the old stem-exact match tried first;
   `apply_texture_set` now refuses a set that resolves no base colour.
2. **A texture set never reaching a curve band (item 4).** Same root cause as above — the sets were
   symlinked, so no maps resolved. There was no second bug in BobSplines. What WAS missing is that
   `apply_curve_surface`'s chosen slot was never reported, so the run guessed the index; `curve_build`
   now returns it in `data.slot`.
3. **`set_env` reaching no material (item 5).** `shading.apply_world_feed` existed and was called by
   nobody. Added a world-hook registry to `core/env.py` (the acyclic root) that `ui/world.py`
   subscribes `apply_all` into; `set_env` re-applies consumers and names `season` as structural; new
   `apply_world` op; headless falls back to the core driver feed. The duplicate applier in
   `ui/shaders.py` is gone.
4. **`build_live` main-thread timeout (item 7).** Idempotency key per batch, per-op ack counting, a
   bounded batch registry that COLLECTS rather than re-runs, and long-polling on the client. A slow
   batch now blocks and returns the real result.
5. **No introspection (item 8).** New read-only `describe_scene` op (`core/describe.py`) and MCP
   tool. Reports the modifier stack in order, a terrain's build params, each material layer slot
   with its texture set and *which maps actually resolve on disk*, curve roles with their mask and
   edge attribute names, world state with the installed driver count, and the pack search path.
6. **Curve shape was role-only (item 9).** A typed `CurveShape` on `make_curve` / `curve_build`,
   applied after the role seed so the role (and therefore the mask channel) is preserved.
7. **Curve carving at its own Z (item 10).** The cause was not in BobSplines: `build_geonodes` never
   stamped `bbt_heightmap` / `bbt_terrain_*` on the object — only the Terrain panel's bake did — so
   `_has_bake` read False over MCP and there was nothing to drape onto. The op stamps them now;
   `drape_curve` takes `terrain` and reads the four numbers off it, warning on a disagreeing
   explicit value; carving at curve Z is a warning rather than a success mode.
8. **Unshaded after `reset: true` (item 6).** Confirmed the suspicion. The rebuild moves the fresh
   recipe modifier back to its old index, which on an already-shaded terrain sits BEHIND
   `BBT_Material`. `_keep_set_material_last` runs in both build paths.
9. **Scatter sink and per-asset filter (item 11).** `Z Offset` (global space, so a normal-aligned
   rock still sinks down) plus `assets_include` / `assets_exclude` by name, resolved to instance
   indices at build time and applied with a Delete Geometry on the instance domain so the random
   pick stays uniform over what is left. A per-layer `Skip` field on the panel keeps it across a
   structural rebuild.
10. **D15, ComfyUI VRAM.** All three sub-decisions answered: per-route floors in
    `comfy.VRAM_FLOOR_MIB` preflighted with one recovery attempt before refusing; `recover_vram`
    reports what `POST /free` actually gave back and names the restart when it is not enough;
    `PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True` on servers Bob launches; `render_scene`
    releases Blender's render buffers after the frame. New `comfy_free` MCP tool. The panel's Free
    VRAM button now reports the number instead of saying "Freed."

## Task 1: pay the verification debt (do this first)

Two throwaway probes were written and run, and both are currently parked in `_generated/` where
nothing will ever run them again:

- `_generated/probe_batch1.py` — 23 checks covering items 1 to 8 and 10 above.
- `_generated/probe_scatter.py` — the scatter Z-offset and include/exclude measurements.

Turn them into permanent checks:

- The pure-Python parts belong in `tools/tests`: `assets.texture_set_maps` stem tolerance,
  `assets.add_pack_root` ordering, `splines_build.set_shape`'s None-skipping, the new contract
  fields round-tripping through `Operation` (extend `_OP_SAMPLES` in `test_contracts.py` — the
  `pack_dir` bug was exactly a contract-field omission, so this is the test that would have caught
  it), and `comfy.VRAM_FLOOR_MIB` / `preflight_vram` against a stubbed status.
- The bpy parts belong in a committed headless gate, in the style of
  `tools/scripts/headless_texset.py`: exit 0 on pass, print PASS/FAIL per check. Fold both probes
  into one script and delete the `_generated/` copies.

Note when writing the headless gate: the curve ops need `bob_blender_tools.register()` first (see
the known gap below), which `headless_texset.py` does not do.

## Task 2: items 11 to 13, the generation track

These are the three that did not land. All three stay in docs/GENERATION.md.

- **D16, the foliage guardrail.** Three edits, all named in the doc's Foliage section: a one-line
  info row under the Scatter panel's `kind` selector (trees: "generates a trunk, not a crown"; the
  foliage kinds: "reads at 2 m or further"); a sentence in `comfy_mesh`'s docstring saying
  `kind="trees"` returns a single solid mesh with no leaf cards and no alpha; and an
  `import_generated` warning when a foliage-ish asset lands with `opacity.verdict != "cutout"`. The
  louder version — refusing `kind="trees"` without an explicit `trunk_only=True` — is a contract
  change and the doc's own reading is that it is too strong while the foliage generator does not
  exist. **Decide the volume and record the decision in D16.**
- **Prompt ergonomics.** `comfy_mesh` has no `negative` argument where `comfy_texture_set` does; add
  one and thread it to the subject stage (`comfy.subject_image` already takes `negative`). Put the
  subject-framing guidance in the tool description: SDXL ignores negations, so "no pot, no planter"
  returned a nursery pot twice while "bare-root ... on a white studio sweep" fixed it in one shot.
- **Two undocumented ceilings.** `control_bbox` is clamped to 3.0 per axis (find the clamp in
  `core/comfy.py` and document it in the `comfy_mesh` docstring — a 1:9 trunk is unrequestable and
  an agent currently discovers that by failing). And the mesh-control path fails with "Mesh file not
  found" when `$BOB_COMFY_DIR` is unset, which is the repo `.mcp.json`'s current state (its `env`
  block is empty). Either set the variable there or make the failure name it; naming it is the
  better fix, since a packaged install has no `.mcp.json` of ours.

## Task 3: move items 1 to 9 to their owners' docs

They are written up inside docs/GENERATION.md, which is the wrong home — they belong to MCP.md,
SPLINES.md and the scatter docs. Move each one as a short note on the fixed behaviour (not as a
defect report), and leave the generation-track ones (D15, D16, and items 12 and 13) where they are.
Specifically:

- MCP.md: `describe_scene`, the `build_live` batch/idempotency contract, `apply_world`, `pack_dir`
  on `apply_texture_set`, and the terrain-param stamp.
- SPLINES.md: the `shape` params, `curve_build`'s reported slot, `drape_curve`'s `terrain`
  argument, and the "carved at curve Z" warning.
- The scatter docs (SCATTER-SHADING-UX.md, or wherever the layer vocabulary lives — check): `Z
  Offset` and the include/exclude filter.
- docs/API.md is generated by `tools/scripts/gen_api_docs.py`; re-run it so the new ops and fields
  appear.

## Task 4: docs/FOLIAGE.md, the new track

Raised from docs/GENERATION.md's "Foliage: what image-to-3D is for, and what it is not for". Write it
as its own plan document with that section as its origin. The shape, from that section:

- **Trunk and main limbs**: a generated mesh (image-to-3D is good at bark) or a GN sweep along
  curves, with a generated bark texture set.
- **Branch hierarchy**: GN recursion over curves with length, angle, taper, gnarl and phyllotaxy per
  level, reusing the recipe scaffold and the BobSplines curve vocabulary.
- **Foliage**: alpha CARDS instanced on branch tips, from a generated needle-spray or leaf atlas. W4
  already emits genuine cutout alpha (range 0.000 to 1.000, mean 0.175 measured at G3), so the atlas
  is a tileable-adjacent workflow rather than new model work.
- **Wind and season for free**: the cards read `S_EnvState` like every other BobShader.
- **LODs**: card count and branch depth per level, through the existing `gen_assets` LOD chain.

What the generation track owes it: the atlas workflow, and the D16 guardrail so nobody waits for the
generator by generating trees.

## Known gap, found while working, not in the original thirteen

**Curve ops cannot run through the headless `build` tool at all.** `obj.bbt_curve` is a
PropertyGroup registered by `ui/splines.py`, and `runners/headless_build.py` only imports
`core.dispatch` — so `make_curve` raises `AttributeError: 'Object' object has no attribute
'bbt_curve'`. Every curve op is live-bridge-only today. The same is true of anything else reading a
ui-registered PropertyGroup (`bbt_scatter_layer`, `bbt_world`). Worth deciding: either the headless
runner registers the addon, or the per-curve state moves out of `ui/` into `core/`. The second is
the architecturally honest answer and matches the "core is the acyclic root" rule the codebase
already follows, but it is a bigger change. Not blocking anything above.

## Commit guidance

Nothing is committed. The landed work is one coherent change set but touches five subsystems, so
split it by owner rather than committing 1,100 lines at once: assets/shading, splines/terrain,
world/env, bridge, scatter, generation. Branch first — `main` is the default branch and is one
commit ahead of origin already.
