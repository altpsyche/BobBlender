# Scatter: scattered assets as first-class, editable BobShaders

**Shipped and verified headless.** A scattered asset is not a black box: select its layer and the
Shaders panel edits its surface look (base colour, roughness, variation, weather, AO, macro) like any
other BobShader. This is panel and pipeline behaviour plus one auto-convert step; it changes **no
shader graph**, so the solid and terrain render baselines stay byte-identical — confirmed at **0.0
pixel delta**.

Biomes are [BIOMES.md](BIOMES.md); the master contract is [SHADERS.md](SHADERS.md).

Where it lives: `ui/shaders._editing_material` and `_asset_materials`, the top-panel scatter material
list, `BBT_OT_shaders_select(target="asset")` and `bbt_shaders.asset_material`; plus `ui/world`'s
Apply Biome `weather_assets` toggle (default on), which converts each scattered kind's
`BOB_Assets_<kind>` after the scatter step.

## 1. The problem, untangled

Selecting the terrain shows the Terrain sub-panel; nothing shows the Surface params of the trees,
rocks, plants, or grass. Two separate things cause this, and only one is a real constraint:

- Native materials vs BobShaders (a soft default). Assets import with their own glTF materials.
  They CAN be BobShaders: `materials.bobshade_material` reroutes only Base Color / Roughness /
  Metallic through `S_SurfaceMaster` and leaves Alpha, Normal, and Emission untouched, so leaf and
  grass alpha survive. They are left native only because import should not silently rewrite every
  material, and because of a stale note in `assets.py` ("the opaque surface master cannot represent
  alpha") that stopped being true once Convert learned to preserve alpha. This default is flippable.
- Unlinked collections (the real constraint). The asset sources live in `BOB_Assets_<kind>`
  collections that are unlinked from the scene on purpose: the scatter geometry-nodes graph
  instances them, so scene-linking the sources too would render a duplicate at the origin and
  clutter the viewport. Off-scene means not viewport-selectable, so the panel's "edit the active
  object's material" model has nothing to click, whether the material is a BobShader or not.

There is no free lunch on the second point: a selectable source must be viewport-present (clutter);
a clean scene keeps it unlinked (unselectable). The current design chose clean. The fix restores
editability without giving up the clean scene.

## 2. Design goals

- Keep `BOB_Assets_*` unlinked (the instancing pattern is correct; do not scene-link the sources).
- Extend native identity rather than reintroduce a stored-pointer picker (the UX redesign removed
  those). The scatter layer object is the natural, already-selectable handle to its asset pool.
- No shader-graph change, so solid and terrain baselines render byte-identically.
- Scattered assets should be first-class: BobShaders by default, editable in place, updating every
  instance live (the `S_SurfaceMaster` inputs are a shared datablock, so one edit reaches all
  instances with no rebuild).

## 3. The fix

### 3.1 The scatter layer is the editable shading unit (Option A)

Conceptual model: a scatter layer's look IS the materials of its asset collection, so a selected
scatter layer becomes the panel's proxy for editing those materials. All in `ui/shaders.py`:

- State: `bbt_shaders.asset_material` (StringProperty), the chosen material within the active
  layer's assets collection.
- `_editing_material(context)`: a normal mesh returns its `active_material` (today's behaviour); a
  scatter layer object (`_is_scatter_object`) returns the chosen material from
  `bbt_scatter_layer.assets`, clamped to the collection's material set, defaulting to the first.
- Repoint the Surface and Weather sub-panel polls and draws, and their operators (surface preset,
  surface texture assign), at `_editing_material`. Terrain stays on `active_material` (an asset is
  never a terrain shader).
- Top panel, scatter branch: replace today's single "Convert &lt;collection&gt;" note with a list of
  the assets collection's materials (deduped by datablock, e.g. `tree_small_02_leaves / branches /
  trunk`), each row a select plus a status tag (surface BobShader, or plain with a per-material
  Convert), plus "Convert all". Selecting sets `asset_material`.
- Asset-material selection via the shared `BBT_OT_shaders_select` (target="asset"), the one
  selector operator for material slots, asset materials and terrain layers.

Result: select a tree layer in the viewport, its materials list in the Shaders panel, pick one, and
the Surface and Weather sub-panels edit it, updating every scattered instance with no rebuild.

### 3.2 Convert both ways: auto on Apply Biome + manual

- Auto (default on): Apply Biome (`ui/world.BBT_OT_world_apply_biome`) gains a step, after the
  scatter is built, that converts each `BOB_Assets_<kind>` the biome uses to BobShaders (iterate the
  biome's scatter kinds, convert each collection's materials via `bobshade_material`). A
  `weather_assets: BoolProperty(default=True)` toggle skips it when unwanted. So a freshly applied
  biome has editable, weather-driven assets out of the box.
- Manual (always available): the per-material and "Convert all" rows in the new scatter-layer
  material list (3.1), and the existing Batch-convert (Collection scope) for assets imported without
  Apply Biome.

### 3.3 Correct the stale note

Update the `assets.py` module docstring: assets import with their native materials and are converted
to BobShaders (automatically by Apply Biome, or manually), Convert preserving their alpha and
normals, so trees and grass weather with the ground. Drop the "opaque surface master cannot
represent alpha" line, which Convert made obsolete.

## 4. The three moving parts

- **`_editing_material`, and the Surface and Weather sub-panels pointed at it.** This is the smallest
  change that makes the Surface panel appear at all for an active scatter layer; it defaults to the
  first asset material.
- **The asset-material list plus its select operator**, in the top panel, so a layer with four asset
  materials is four rows and not a guess.
- **Auto-convert on Apply Biome** (`weather_assets`, default on) plus a manual "Convert assets"
  affordance in the scatter-layer view, so weathering reaches scattered assets without a per-asset
  step.

## 5. Verification (headless, same discipline as prior tracks)

- Register plus icon / idname / prop audit (the new operator and the `asset_material` prop).
- Functional: build a scatter, select a layer object, confirm `_editing_material` returns an asset
  material; after Convert the Surface poll is true; setting a Surface input propagates to the
  material datablock (shared, so all instances change).
- Apply Biome with `weather_assets` on: the asset collections come back as surface BobShaders, and
  selecting a layer then shows and edits them.
- No regression: solid and terrain baselines byte-identical (panel plus convert only, no shader-graph
  change); re-run the fixed-scene solid render-delta and expect 0.0. Converting scatter assets
  changes the SCATTER render by design, not the solid or terrain baselines.
- Render: tune a leaf base-colour tint and a rock roughness on converted assets, confirm the
  scattered instances change.

## 6. Decisions (settled 2026-07-20)

- Edit scatter-asset materials through the active scatter layer object (Option A), not by linking
  the sources or a raw material picker. Keeps the scene clean and the native-identity model.
- Convert both ways: auto on Apply Biome (default on, one toggle to skip) and manual (per-material
  and Convert-all in the layer view, plus the existing Batch-convert).
- Keep `BOB_Assets_*` unlinked (the instancing pattern). Fix the stale `assets.py` alpha note.

## 7. Out of scope / follow-ons

- Per-layer material overrides that differ from the shared asset material (a layer wanting a
  distinct tint would need its own material copy; today all layers instancing a collection share it).
- A thumbnail or swatch browser for asset materials.
