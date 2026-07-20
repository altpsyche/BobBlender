# Biome system: asset-quality fixes and a manifest v2 plan

Status: IMPLEMENTED (2026-07-20), all phases A-G done and verified headless. Scope: fix the
concrete quality issues in the verdant_trail biome, and evolve the biome manifest from "models +
terrain" into a self-describing biome that stands up a coherent scene (terrain + scatter + world)
from one file. Grew out of the BobBlenderTools UX redesign (docs/UX-REDESIGN.md), which added
Import Biome (Scatter) and Biome Terrain (Shaders).

The rendering pipeline was complete and correct (BobShaders/BobFirmament S1-S5). This track is
content (assets, manifest), data plumbing (readers, validation), and panel actions that compose
existing builders - plus the optional Phase F shading polish the user opted into (F3 model AO;
F1 terrain near/far anti-tiling), each verified to leave solid materials byte-identical.

## 0. Implementation outcome (2026-07-20)

- Phase A: assets.py biome_manifest() normalizing reader (v1 flat -> v2), accessors
  biome_meta/models/scatter/world + biome_terrain (unchanged), validate_biome(). Back-compat,
  no behaviour change.
- Phase B: meta block in the manifest + CREDITS.md + per-model SOURCE.txt (9 folders).
- Phase C: import_gltf(scale, rotation, max_polys); max_polys collapse-decimates via the
  evaluated depsgraph (headless-safe); assets without max_polys are never decimated.
- Phase D: Scatter panel "Biome Scatter" - one layer per scatter kind from scatter{}.
- Phase E: World panel "Biome World" (sets bbt_env + Build Sky) and "Apply Biome" (import ->
  terrain -> scatter -> world on the Scatter emitter / active mesh), in "Set up a look" (D5).
- Phase F: F3 AO Map socket on S_SurfaceMaster (bobshade folds the arm R channel); F1 near/far
  anti-tiling in texture_set_group (a detail-scale sample blend + a low-frequency macro
  brightness break-up), both exposed as live knobs.

Decisions, as resolved empirically (they override the D1/D2 guesses in section 2):
- D1 tree LOD: Poly Haven has NO mesh LOD for trees (1k/2k/4k/8k are texture resolutions) and no
  low-poly tree at all (all photogrammetry; lightest ~385k tris and coniferous; on-theme broadleaf
  all >=1.3M). tree_small_02 is 94% alpha leaf tris, so "decimate solid only" cannot reach the
  budget. Resolved (user): DECIMATE the current tree to 180k via the max_polys import path
  (relaxing the no-leaf-decimate rule for this asset only, opted in per-asset). Verified lush.
- D2 shrub_01: CORRECT as-is (not mis-oriented). It is a 2.6 m-wide shallow strip of small upright
  ground-cover plants; the thin (0.22 m) axis is strip depth, not a lay-flat. Kept, no rotation.
- D3 terrain 2k: deferred; instead F1 improves the triplanar near/far tiling (no re-download).
- D4 model AO: implemented (F3).
- D5 Apply Biome placement: World panel "Set up a look".

## 1. Review findings (empirical, 2026-07-20)

Measured by importing verdant_trail headless and rendering a full scene (terrain + scattered
converted assets). Baseline data to design against:

Assets (Poly Haven CC0 glTF, 1k textures, arm-packed AO/Rough/Metal, diff sRGB, nor_gl):
- Textures: all 1024x1024, correct colorspaces. Alpha handled (leaves/grass BLEND, shrubs HASHED,
  all alpha-linked). Normals present. Roughness/metallic split from arm, metallic 0. Real-world
  scale sane (tree 4.6 m, boulder 1.8 m, rocks 3-30 cm, grass 12-43 cm). BobShade convert
  preserves alpha/blend/normal and re-routes the diff/rough correctly. Verdict: good.
- Polycounts: tree_small_02 is the LOD0 scan at 2,062,487 polys; shrub_01 is 156,012. The rest
  are fine (grass 714-2489, rocks 12k-66k, other shrubs 2k-9k).
- shrub_01_a is 2.59 x 0.22 x 0.40 m: a 2.6 m-wide, 22 cm-thin, flat object unlike the upright
  shrubs. Likely imported laid flat (orientation/scale quirk), and it is also the heavy shrub.
- Model AO is dropped: the glTF references metallicRoughnessTexture but no occlusionTexture, so the
  arm map's AO (R) channel is unused. Terrain sets DO use AO (folded into albedo), so it is
  inconsistent. Minor.
- No model attribution: library/textures/*/SOURCE.txt exist, but the model folders have none.

Terrain sets (library/textures/{soil,grass,rock}): all 1k with basecolor/rough/normal/height/ao;
triplanar via S_TerrainMaster; AO folded into albedo, height -> bump. Fine mid-ground; may tile up
close on a hero foreground.

Manifest today: {trees:[...], rocks:[...], grass:[...], plants:[...], terrain:{layers}}. Read by
assets.populate_scatter_assets (list values only), assets.biome_terrain (the terrain dict),
assets.list_biomes (folders with manifest.json). Strengths: data-driven, self-describing,
drop-a-folder extensibility, reuses shared texture sets by name, skips non-list sections. Gaps:
no metadata/attribution; flat namespace (model kinds sit next to the reserved "terrain" key); no
scatter recipe (the biome knows its models but not how to place them); no world defaults; no LOD
or per-asset controls; no schema/validation.

## 2. Decisions

Recommended (proceed unless the implementation chat objects):
- Manifest v2 is BACK-COMPATIBLE: today's flat {kind:[strings]} keeps working; the new sections
  (meta/models/scatter/world) are additive; a normalizing reader maps old -> new.
- A biome becomes a coherent scene: Import Biome (assets) + Biome Terrain + Biome Scatter + Biome
  World, with an optional Apply Biome that runs all on the active terrain/emitter.
- Operators/classes keep the bob_blender_tools.* / BBT_* names; new panels/actions follow the UX
  redesign conventions (ui_helpers, native context, structural marking).
- Attribution is recorded (meta block + a per-biome CREDITS.md), even though CC0 does not require
  it, to match the repo's SOURCE.txt convention and keep provenance.

Open (decide in the implementation chat; this doc plans both branches):
- D1 Tree LOD: re-fetch a lighter Poly Haven LOD (needs network) vs an offline Blender decimate
  preprocess. Recommended: re-fetch LOD1/LOD2 if network is available; else decimate the solid
  meshes only (never the alpha leaf cards). Target a scatter budget of ~100-200k polys.
- D2 shrub_01: diagnose first (render it alone). If laid flat, add a rotation override to stand it
  up; if a genuine ground-spreader, keep it but tag its scatter align/scale; if broken, drop it.
- D3 Terrain 2k: upgrade the soil/grass/rock sets to 2k for a hero foreground (needs re-download),
  or defer. Recommended: defer; instead tune the triplanar Scale default so 1k tiles less.
- D4 Model AO (F3): implement (adds an AO Map socket to S_SurfaceMaster) vs skip. Recommended:
  low priority; implement only if the crevice flatness reads on rocks in a hero shot.
- D5 Where Apply Biome lives: World panel "Set up a look" vs a dedicated Biome mini-section.
  Recommended: World, next to Scene Preset, since a biome is world-level context.

## 3. Manifest v2 schema (target, back-compatible)

    {
      "meta": {
        "name": "Verdant Trail",
        "description": "Lush temperate trail: grassy soil, rock on slopes, small trees and shrubs",
        "climate": "temperate",
        "source": "Poly Haven (CC0, public domain)",
        "license": "CC0-1.0",
        "version": 2
      },
      "models": {
        "trees":  [ { "file": "tree_small_02/tree_small_02_lod2.gltf", "scale": 1.0 } ],
        "rocks":  [ "boulder_01/boulder_01_1k.gltf", ... ],
        "grass":  [ "grass_medium_02/grass_medium_02_1k.gltf" ],
        "plants": [ { "file": "shrub_01/shrub_01_1k.gltf", "rotation": [90, 0, 0] }, ... ]
      },
      "terrain": { "layers": [ {"layer":"soil","texture":"soil"},
                               {"layer":"grass","texture":"grass"},
                               {"layer":"rock","texture":"rock"} ] },
      "scatter": {
        "trees":  { "density": 0.4, "scale": [0.8, 1.3], "min_normal_z": 0.6, "align": "up" },
        "rocks":  { "density": 1.5, "scale": [0.4, 1.2], "min_normal_z": 0.25, "align": "normal" },
        "plants": { "density": 1.0, "scale": [0.7, 1.2], "min_normal_z": 0.4, "align": "normal" },
        "grass":  { "density": 22.0, "scale": [0.6, 1.1], "min_normal_z": 0.35, "align": "normal" }
      },
      "world": { "season": "summer", "weather": "clear", "time_of_day": 10.0,
                 "cloud_cover": 0.2, "temperature": 22.0, "wind_direction": 90.0,
                 "wind_strength": 1.5 }
    }

Rules:
- A model entry is a string (bare file) OR an object {file, scale?, rotation?, weight?, max_polys?}.
  rotation is XYZ degrees applied on import (fixes D2); scale multiplies; weight biases random pick;
  max_polys triggers a decimate on import if exceeded.
- Reserved top-level keys: meta, models, terrain, scatter, world. Any OTHER top-level list key is a
  legacy model kind (back-compat), read as if under models.
- scatter keys are the scatter kinds (trees/rocks/plants/grass); values seed the layer's structural
  config + live knobs, mirroring scatter_panel.LAYER_TYPES. Missing keys fall back to LAYER_TYPES.
- terrain unchanged from today.

## 4. Fixes and system work (detail)

Quality:
- Q1 Tree poly budget (D1). Replace tree_small_02 LOD0 (2M) with a scatter-weight LOD. Update the
  models entry to the lighter file. Add per-kind/asset max_polys + a validator warning. Files:
  library/models/verdant_trail/ (asset + manifest), assets.py (import honors max_polys via a
  Decimate modifier applied before baking the transform; foliage meshes with alpha are left alone).
- Q2 shrub_01 orientation (D2). Diagnose, then a per-asset rotation/scale override in models,
  applied in assets.import_gltf before the transform bake. Or drop it from the manifest.
- Q3 Model attribution. Add a meta block (source/license) and a per-biome CREDITS.md listing each
  asset's Poly Haven slug + URL. Optionally a SOURCE.txt per model folder to match textures.
- Q4 (optional) Terrain resolution / triplanar (D3). Either re-fetch 2k soil/grass/rock, or raise
  the S_TerrainMaster triplanar Scale default so 1k reads finer. materials.texture_set_group /
  terrain defaults.

System (manifest v2):
- M1 Schema + normalizing reader in assets.py: biome_manifest(biome) -> normalized v2 dict
  (v1 flat kinds -> models; defaults filled). Accessors biome_meta/biome_models/biome_terrain
  (exists)/biome_scatter/biome_world. Keep list_biomes.
- M2 populate_scatter_assets honors models{} (object entries, overrides) and stays back-compat with
  flat kinds; keeps skipping non-list/non-model sections.
- M3 import_gltf honors per-asset scale/rotation/max_polys (Q1/Q2 mechanism).
- M4 validate_biome(biome) -> list of warnings: missing texture set for a terrain layer, bad layer
  key, missing model file, model over max_polys, scatter kind with no models, unknown world field.
  Surfaced in the Import/Apply operator reports and printed at import.

New panel actions (compose existing builders; follow UX-redesign conventions):
- A1 Biome Scatter (Scatter panel): reads scatter{}, and for the active emitter creates a layer per
  kind (structural config from the biome + LAYER_TYPES fallback), points assets at BOB_Assets_<kind>,
  sets the live knobs (density/scale/slope), and Build All. Parallel to Biome Terrain. Reuses
  scatter_panel.LAYER_TYPES + the scatter recipe.
- A2 Biome World (World panel): reads world{}, sets bbt_env fields, optionally Build Sky. Marked
  structural.
- A3 Apply Biome (D5): Import assets + Biome Terrain (on the terrain object) + Biome Scatter (on the
  emitter) + Biome World, in one action. Needs a terrain/emitter target (use the active object, or
  the Scatter emitter + a chosen terrain). Composes A1/A2 + the existing Biome Terrain + Import.

## 5. Phased implementation plan

Phase A - manifest v2 foundation: schema readers + normalization + validator in assets.py. No
  behaviour change to existing actions (Import Biome, Biome Terrain still work via the normalized
  reader). Verify: existing verify_phase4 + verify_biome_terrain still pass; validator flags a
  deliberately broken test manifest.
Phase B - attribution (Q3): meta block + CREDITS.md for verdant_trail. Docs/provenance only.
Phase C - asset quality (Q1 tree LOD, Q2 shrub_01): per-asset overrides + max_polys in import;
  update the manifest to the lighter tree and the shrub_01 fix. Verify: polycounts under budget;
  a scene render is materially lighter and still reads lush; shrub_01 stands correctly.
Phase D - Biome Scatter (A1): the scatter section + the action + panel UI. Verify: one action
  scatters all kinds at biome densities; instance counts sane; render coherent.
Phase E - Biome World (A2) + Apply Biome (A3): world section + actions. Verify: Apply Biome stands
  a full coherent verdant_trail scene (terrain + scatter + world) in one action; render.
Phase F - optional polish: F1 terrain 2k / triplanar (D3), F2 nothing, F3 model AO (D4, the one
  shader edit: add an AO Map socket to S_SurfaceMaster, default 1.0, and have bobshade_material
  fold the arm R channel into it). Each optional and independently verified for no regression.
Phase G - docs + verify: fold settled parts into ARCHITECTURE.md/SHADERS.md/CONVENTIONS.md and
  assets.py docstrings; run the full audit + a biome-scene render; update the memory note.

## 6. Verification (same discipline as the UX track)

Blender 5.2 headless, fish shell: env BOB_REPO=/home/siva/dev/BobBlender <blender> --background
--factory-startup --python <abs script> > out.log 2>&1, then read the log (do not pipe with $?;
use $status). Scratchpad is wiped between sessions; recreate scripts from here.
- Headless register + icon/idname/prop audit for every new operator/panel (draw() is not exercised
  by register(); validate drawn icons against the UILayout enum, idnames against the registry).
- Manifest readers/validator: unit-style checks (normalize v1 and v2; validator catches each error
  class).
- No shading regression on UNCHANGED paths: the fixed-scene render-delta (library/_generated/
  ux_baseline) must stay byte-identical for Phases A/B/D/E (they add data/actions, not shading).
  Phase C changes the tree asset, so its scene render CHANGES by design; assert it is lighter and
  still renders, do not diff against the 2M baseline. Phase F3 changes the surface master; re-run
  the surface render-delta and accept only the intended AO darkening.
- Biome-scene render: after D/E, drive Apply Biome and render; confirm a coherent lush scene and
  that instance counts / polycounts are within budget.

## 7. Out of scope / follow-ons

- Additional biomes (alpine, desert) authored to v2 - the point of the system, but content, later.
- A biome browser/thumbnail UI.
- Automatic LOD switching by camera distance (GN LOD) for the scatter.
- Blender Asset Browser catalog integration for biomes.
- Downloading assets from within the tool (kept a manual/offline step).
