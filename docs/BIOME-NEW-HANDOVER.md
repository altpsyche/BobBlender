# Handover: plan a new biome (a real, reference-grade forest)

Paste this into a fresh chat. It is a PLANNING task, not a build task: produce a plan and confirm the
decisions below with the user before authoring anything. Written 2026-07-25 against branch
`fix/audit-remediation`, Blender 5.2 LTS only. Caveman ultra for chat; code/commits/docs written
normally. The user commits — do NOT commit unless asked.

## Why this exists

The MCP live-scene work recreated a reference photo (a lush Appalachian summer-morning forest trail:
narrow winding mud path, mossy green understory, ferns, tall mixed trees, a backlit misty glow — see
`library/_generated/ref_scene.png` next to `ref_scene_ops.json`). It got the composition, the trail,
the light, and the mist right, but it hit a hard ceiling: **the only biome that ships is `blockout`**,
whose scatter is untextured proxy cones/icospheres. The render reads as a clean block-out, not a real
forest. Real bark, moss, ferns, leaf litter, and varied tree silhouettes are missing because there is
no real biome to apply.

This task plans the first **real** biome — the art + manifest that make `apply_biome` produce a
photo-plausible forest instead of a block-out.

## What already exists (read before planning)

- **Asset-pack spec** — `docs/API.md` §3. A pack is a folder: `pack.json` (manifest), `models/<biome>/
  manifest.json` (biome def), `textures/<set>/` (image sets). A pack ships biomes + texture sets; it
  does NOT ship skies/HDRIs (the sky is procedural Firmament). Manifest v2 = `{meta, models, terrain,
  scatter, world}`.
- **Resolver** — `core/assets.py` (bpy-free): `asset_roots()` search path is `$BOB_ASSET_PACKS`
  (os.pathsep list) → addon-pref folders → dev-repo `library/` → the bundled block-out pack (always the
  floor). `biome_dir` / `texture_set_dir` / `list_biomes` / `biome_manifest` (normalizes to v2) /
  `validate_biome` (human-readable warnings) / `read_pack`. First pack wins on a name collision.
- **Terrain shading** — `core/shading.py` + `docs/SHADERS.md`. Layer presets `soil/grass/rock/cliff/
  scree/sand` (`TERRAIN_LAYER_PRESETS`); stacks `temperate` (soil+grass+rock) / `alpine` / `desert`.
  A manifest terrain layer with a `texture` keys a real texture set into that layer; a layer with no
  texture is a solid tint. This is the leg that makes ground look real.
- **Scatter** — `core/scatter_build.py` + `docs/SCATTER-SHADING-UX.md`. Kinds `trees/rocks/plants/
  grass` with `density/scale/min_normal_z/align/...`; `biome_scatter` builds every layer from the
  manifest. **Geometry is block-out proxies** (`core.proxies`): there is NO model importer — the glTF
  path was removed (`core/assets.py` ~line 278). So today scatter shape cannot be real without
  re-adding an importer. This is the central decision below.
- **World** — a `bbt_env` preset in the manifest (season/weather/time_of_day/...), applied to the
  shared world state so sky/wetness/snow follow.
- **MCP surface** — `apply_biome` (shades + scatters + world in one op; now takes `world:false` to
  keep the current env and `curve_mode:"clear"/"keep"` for path-aware scatter), `world_biome`,
  `list_biomes` (tool), plus the thin-fog `build_fog preset/density` and env-seeded `build_sky` shipped
  with the reference test. Use these to APPLY and RENDER a candidate biome for review.
- **Prior biome history** — `docs/BIOME-SYSTEM.md` and `docs/BIOME-BLOCKOUT-REDESIGN.md`. The redesign
  killed the old `verdant_trail` asset, made block-out canonical, and moved real art to external packs
  with image texture sets. Read both so the plan does not re-introduce a pattern already rejected.

## The core question the plan must answer

**How real does the scatter GEOMETRY need to be, and how do we get it?** Three tiers — the plan should
recommend one (and a path to the next):

1. **Texture-only biome (proxies keep their shape).** Author real terrain texture sets (mossy soil,
   leaf litter, grass, mossy rock) + tuned scatter densities + a summer world. Ground and terrain read
   real; trees/rocks stay proxy silhouettes. Cheapest; ships against the existing pipeline with zero new
   code. Ceiling: the block-out cones remain.
2. **Re-add a model importer (glTF/glb) so scatter uses real meshes.** Restores the removed importer as
   a small `core/` module, has `biome_scatter` instance real models per kind (with the block-out proxy
   as the fallback when a model is absent). This is what makes a forest read as a forest. Larger scope:
   an importer, per-kind model resolution in the manifest, LOD/instancing cost, licensing.
3. **Both, phased** — ship tier 1 first (real ground now), then tier 2 (real trees/ferns/rocks) as a
   second track. Recommended default unless the user wants the real thing in one pass.

## Decisions to confirm with the user (before any authoring)

1. **Which biome first + its name.** Target the reference: a temperate/eastern-woodland summer forest
   (moss, ferns, mixed conifer + broadleaf, humid). Propose a concrete id (e.g. `eastern_woodland` or
   `temperate_forest`) and its climate/world.
2. **Geometry tier** (1 / 2 / 3 above). This sizes the whole task.
3. **Where the art lives + licensing.** A pack under `$BOB_ASSET_PACKS` (outside the repo, polyrepo-ready)
   vs the dev `library/`. Source of the assets (CC0 textures e.g. Poly Haven / ambientCG; CC0 models);
   attribution/license fields in `pack.json`. Do NOT commit large binaries into the repo without the
   user choosing LFS.
4. **Texture sets to author** and how they map to terrain layers: which of soil/grass/rock/cliff/scree/
   sand the forest uses (likely soil base + grass/moss + mossy rock), and whether a new layer preset
   (e.g. `moss`, `leaf_litter`) is warranted or the existing presets + textures suffice.
5. **Scatter recipe** — per-kind density/scale/min_normal_z/align for tall mixed trees, a dense fern/
   plant understory, mossy rocks, and grass, tuned so `curve_mode:"clear"` still opens a believable
   trail corridor.
6. **World preset** — summer, morning, humid, light ground mist (the reference mood), authored so
   `apply_biome` sets it but a later `set_env`/`world:false` can override for other times.
7. **Validation bar** — apply the candidate over MCP and render against `ref_scene.png`; define "good
   enough to ship" (side-by-side, not just `validate_biome` clean).

## Deliverable of THIS chat

A written plan (confirmed with the user) covering: the biome id + world, the geometry tier + any new
code it needs (importer module, manifest fields, scatter wiring), the exact pack folder layout, the
texture sets + their sources/licenses, the scatter recipe, the terrain layer stack, and a phase
breakdown with a render-vs-reference checkpoint. Do NOT author art or write the manifest yet — plan
first, get sign-off, then a follow-up chat builds it.

## Constraints + anchors

- Blender 5.2 LTS. A 5.2 binary is at `~/.steam/steam/steamapps/common/Blender/blender`; the live
  bridge on `127.0.0.1:9876` is the running session (drive with `build_live`, render with
  `render_scene`). EEVEE enum is `BLENDER_EEVEE`.
- Keep the bundled `blockout` biome as the search-path floor; a real biome is an ADDITIVE pack, never a
  replacement.
- Pack spec is `schema: 1`; `validate_biome(name)` must come back clean. Rescan Asset Packs (Advanced
  panel) refreshes the biome enums after editing pack folders.
- If tier 2/3: the importer is new `core/` code (bpy-side) with a contract + test; follow the repo's
  one-core / two-executor design (`docs/ARCHITECTURE.md`) and route the panel + MCP through one shared
  core function. Lint 0 (`tools/scripts/check_selfimports.py`), pytest green, regen `docs/API.md` if the
  contract changes.
- Key files: `core/assets.py` (resolver + manifest), `core/shading.py` (terrain layers/stacks),
  `core/scatter_build.py` (`biome_scatter`, `LAYER_TYPES`), `core/proxies.py` (the proxy geometry a real
  importer replaces/falls back to), `core/biome.py` (the `apply_biome` orchestrator). Docs: `API.md` §3,
  `SHADERS.md`, `BIOME-SYSTEM.md`, `BIOME-BLOCKOUT-REDESIGN.md`, `SCATTER-SHADING-UX.md`.
- Do not commit unless the user asks.
