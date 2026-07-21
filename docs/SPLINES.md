# BobSplines: typed curves that drive terrain, material, scatter, and water

Status: spec / design, 2026-07-21, branch fix/audit-remediation. Written to be actionable
by a fresh chat: every claim about the current code is grounded at file:line, and the plan is
phased so each phase ships something usable. Plain house style (no em-dashes, per the repo
writing convention).

**C1 + C2 + C3 shipped (2026-07-21).** The two Tier-1 decisions in section 9 are confirmed (see
the note there). C1 (Paths panel + typed curve list wiring the existing grade/clear), C2 (the
standalone `curve_overlay` modifier: per-curve, multi-curve, writes the `bbt_curve_mask`/`_dist`
attributes consumers READ, retires the inline path grade), and C3 (a Curve mask channel on the
terrain material, keyed to `bbt_curve_mask`, plus a role surface band) are implemented.
Static-gated (py_compile + reference grep + a pydantic round-trip of the new op), NOT yet verified
in Blender. See section 7 C1/C2/C3 for the file maps. NOTE for existing scenes: the Curve layer
channel is a new socket on the cached S_TerrainMaster group, so it appears only on a freshly built
terrain material (delete the S_TerrainMaster node group or start fresh to regenerate it).

**Name.** The system is **BobSplines**, the fourth authored subsystem alongside BobShaders,
BobFirmament, and the scatter/terrain tools. Following the BobFirmament -> "Atmosphere" panel
precedent (evocative system name, plain panel label), its N-panel section is labelled **Paths**
(the artist's word for the feature set: dirt paths, roads, rivers). Its module home is
`blender/extensions/bob_blender_tools/splines_panel.py` plus the existing curve authoring in
`blender/bbmcp/path_curve.py` (which `make_path` already lives in).

## 1. TL;DR

Today the "path" is a single, untyped curve that is half-wired: scatter clears a trail along
it, terrain can grade a bench along it, but the Terrain panel never passes a curve, and the
curve authoring op is MCP-only with no UI. There are no path types and no other effects.

This spec turns that into **BobSplines**: a curve gets a **role** (dirt path, road, river,
stream, ridge/berm, retaining wall, fence line, ...), and the role drives a coordinated bundle
of effects across four channels from ONE curve:

1. **Terrain shape** (carve a channel, grade a bench, raise a berm) via a cross-section profile.
2. **Terrain material** (a surface band: asphalt/gravel road, worn dirt trail, wet silt riverbed).
3. **Scatter** (clear the trail, keep-only along a bank, scatter-along and align to the curve).
4. **Water and wetness** (a river's water surface + a damp riverbed; puddles on a wet dirt path).

Design spine (consistent with `docs/AUTHORING-MODEL-REVIEW.md`): the curve is a **scene-owned**
datablock the artist edits in the viewport; its **role + params** is config that SEEDS a bundle
of effects; the effects rebuild from the curve on an explicit Build; live knobs (width, depth,
falloff) are snapshot-preserved across rebuilds; a Reset reverts to the role preset.

Two execution modes, mapping onto the two axes in the authoring review:
- **LIVE** (default): a Geometry Nodes overlay on the terrain carves the profile and writes
  material-mask attributes; scatter reads the curve; a river builds a water mesh. Fully
  interactive, non-destructive, no re-bake. This is the authoring loop.
- **BAKED** (optional, rivers/canyons): the curve is rasterized into the venv heightfield bake
  so natural erosion and the flow/wetness maps respect the channel. Reproducible and cached,
  but re-bakes on edit. This is a "commit" step, not the live loop.

## 2. Current state (grounded)

- **Authoring**: `make_path` builds a NURBS curve, optionally draping its control points onto
  the heightmap so its Z is a smooth graded trail height (`blender/bbmcp/path_curve.py:33-69`,
  drape at `:18-30`). It is MCP-only: dispatched at `blender/bbmcp/dispatch.py:9`, contract
  `MakePath` at `tools/bobtools/mcp/contracts.py:41-53`. No panel exposes it.
- **Scatter effect** (clear a trail): `scatter` recipe takes a `path` param and multiplies
  density by a distance-band mask, easing to zero over Path Falloff
  (`blender/bbmcp/geonodes/recipes/scatter.py:212-217`; knobs Path Width/Falloff at `:178-180`).
  Wired through the panel via `scn.path` (`scatter_panel.py:294-296`, passed at `:241-242`).
- **Terrain effect** (grade a bench): `heightmap_terrain` takes a `path` param and levels the
  ground to the draped curve's own smooth Z, recessed by Path Depth, over Path Width/Falloff
  (`blender/bbmcp/geonodes/recipes/heightmap_terrain.py:40-44, 83-92`). BUT the bake operator
  never passes a path (`bob_blender_tools/__init__.py` bake_terrain has no path param), so this
  is reachable only through raw MCP ops today. This is the core "ignored" gap.
- **Reusable GN primitives** (`blender/bbmcp/geonodes/blocks.py`):
  - `curve_distance(ng, path_obj)` -> (distance, centreline_pos): XY distance to the curve plus
    the nearest point on the flattened curve (`:192-205`).
  - `curve_path_sample(ng, path_obj)` -> (distance, path_z): distance plus the draped curve's own
    Z at the nearest vertex, a clean trail height (`:208-243`).
  - `smooth_falloff(ng, value, inner, outer)`: smoothstep band mask, 0 on the trail, 1 off it
    (`:246-257`).
  - Curve-to-mesh helpers `_curve_meshes` (draped + flat) and `_sample_grid_flat` (`:164-189`).
- **Terrain material** (`blender/bbmcp/materials.py`): `terrain_master_group` blends up to 6
  layers (`MAX_TERRAIN_LAYERS`, `:1250`) by a height-lerp (higher composited height wins within
  `Blend Softness`, `:1449-1476`), not a linear crossfade. Each layer's weight is a product of
  gated masks: slope, altitude, noise, paint, curvature, and **flow** (`:1281-1350`). The **flow
  mask** keys a riverbed layer: a layer with Flow Strength on appears only where the baked Flow
  Map exceeds Flow Threshold (`:1337-1343`), and `_autoconfig_riverbed` turns L1 into damp gravel
  on a fresh terrain with drainage maps (`:1183-1199`). **Wetness** is a shading modifier, not a
  layer: `Wetness Map` * `Terrain Wetness` darkens albedo and drops roughness for a wet sheen
  (`:844-860`). The shader can read a GN-produced field two ways: a baked image sampled through
  the object-space UV `_map_uv` (like `Flow Map`, `:1382, 1517-1567`) or a mesh attribute read
  by `ShaderNodeAttribute` (like the paint layers `bbt_paint_L{i}`, `:1323-1329`, and
  `snow_cover`). World position and object UV are both available (`:1435-1444, 1517-1532`).
- **Venv bake** (`tools/bobtools/heightfields/`): an ordered scalar op-stack over a heightfield,
  pure numpy/CuPy, JSON params, hash-cached (`pipeline.py:49-110`, engine `engine.py:75-97`).
  Ops are `fn(h, xp, params, seed) -> h` in `_OPS` (`engine.py:45-64`); selectors are
  `fn(h, xp, **params) -> mask` in `SELECTORS` (`ops_select.py:62-68`); any op can carry a
  `"mask"` that alpha-blends its result (`engine.py:91-93`). Flow = `log1p(MFD accumulation over
  a Planchon-Darboux filled DEM)`, wetness = `0.6*flow + 0.4*low*(1-slope)`, both purely
  topographic and opt-in (`maps.py:24-42`). **Nothing in the venv knows about a curve.** The
  cache key hashes the fully resolved stack (`pipeline.py:70-71`), so curve data placed in a
  stack op auto-invalidates the cache; op-math files are fingerprinted in `_SOURCE_FILES`
  (`cache.py:19-35`).

## 3. Vocabulary

- **Curve**: a Blender curve object, the geometry, artist-editable in the viewport.
- **Role**: a typed behavior preset. Each role is a bundle of { cross-section profile, material
  band, scatter rule, water/wetness rule }. Roles are the extensible surface: dirt_path, road,
  river, stream, ridge/berm, terrace/retaining_wall, fence_line, and future additions.
- **Cross-section profile**: the signed Z offset stamped into the terrain as a function of
  distance-from-centreline and side. Parameters: width, depth or height, bank width, bank slope,
  falloff, and whether it levels to the curve's own height (a bench) or offsets from the terrain.
- **Curve field**: the shared per-point quantities every effect derives from, evaluated once:
  distance-to-centreline, centreline position, draped curve Z, tangent (flow direction), and
  signed side (left/right of the curve). See 4.2.
- **Effect channels**: Terrain shape, Terrain material band, Scatter, Water and wetness. A role
  turns on a subset; a curve can override per channel.
- **Mode**: LIVE (Geometry Nodes) or BAKED (venv carve). Default LIVE.

## 4. Architecture

### 4.1 Data model

- Role and params live on the curve OBJECT, in a `bbt_curve` PropertyGroup (mirror
  `BBT_ScatterLayer` on the layer object, `scatter_panel.py:258`). This keeps the truth on the
  scene datablock (the spine): the object owns its tuned values, they travel with it, and a
  rebuild snapshot-restores them like any GN knob.
- A scene-level list `bbt_curves` of curve entries (object pointer + cached UI state), edited by
  a UIList exactly like scatter layers (`scatter_panel.py:620-628, 675-684`). Add / remove /
  duplicate / select, add via a role preset picker.
- A **curve applier registry**, the same pattern as the world applier
  (`world_panel.register_applier`) and the biome orchestrator (`world_panel.py:193-275`), so
  "Build Curves" fans out only into the subsystems that are present (terrain, scatter, shaders).

### 4.2 The shared curve field (one evaluation, many consumers)

Add a `curve_field(ng, curve_obj)` block to `blocks.py` that returns, per sample point:
`(distance, centreline_pos, path_z, tangent, side)`. It generalizes `curve_distance` and
`curve_path_sample` (which already give distance, centreline, and draped Z) and adds two new
outputs the richer roles need:
- **tangent**: the curve direction at the nearest point (Sample Nearest -> Sample Index reading
  a stored tangent, or Curve Tangent via Sample Curve). Drives river flow direction and
  align-to-path scatter.
- **side**: sign of the cross product of tangent and the vector to the point, i.e. left/right of
  the curve. Drives asymmetric cut/fill embankments (cut the uphill side, fill the downhill).

**Re-drape live, do not trust the stored Z.** `make_path` bakes a draped Z into the curve's
control points against a specific heightmap (`path_curve.py:18-30`), so that Z goes stale the
moment the terrain is re-baked or re-sculpted. In LIVE mode the overlay runs on the terrain mesh
and must re-drape against the incoming terrain geometry (sample the terrain surface Z at each
control point's XY), so `path_z` always tracks the current ground. The stored draped Z stays only
as the fallback for headless/agent use where no live terrain is in scope. This is the difference
between `curve_path_sample` reading a baked mesh Z (today) and the field re-sampling the terrain
(BobSplines).

Every effect below consumes this one field, so a curve is evaluated once per grid, not once per
effect.

### 4.3 Terrain shape (LIVE, Geometry Nodes)

Generalize the `heightmap_terrain` inline path grading (`heightmap_terrain.py:83-92`) into a
standalone **curve overlay** GN modifier that stacks AFTER the terrain build and works on any
terrain mesh, not just `heightmap_terrain`. The overlay:
1. Re-displaces Z by a **profile** function `profile(distance, side, path_z, terrain_z)`,
   blended back to the untouched terrain over the falloff band (reuse `smooth_falloff`,
   `mix_float`, `displace_z`).
2. Stores the material-mask attributes (4.4) in the same pass.

Profiles per role:
- **dirt_path**: shallow recess toward the draped curve Z (the current behavior, `path_depth`).
- **road**: level a flat bench to the smooth draped grade across the full width, add shoulders,
  and (optional) an asymmetric cut/fill embankment using `side`.
- **river / stream**: a U or V channel below the terrain, bed at `path_z - depth`, with banks
  rising back over the bank width.
- **ridge / berm / dyke**: a raised profile above the terrain.
- **terrace / retaining_wall**: a step across the width.

Compositing: one overlay modifier per curve (recommended, for editability and snapshot/restore
parity with scatter layers), stacked in list order, or a single overlay iterating `bbt_curves`.
Trade-off is perf (N proximity solves) vs a single pass; see open questions.

The inline `path` in `heightmap_terrain` is superseded by the overlay and can be retired once
the overlay ships (keep it working through C1 for continuity).

### 4.4 Terrain material band (LIVE)

The overlay writes per-role 0..1 mask attributes onto the terrain mesh with Store Named
Attribute, e.g. `bbt_curve_surface` (road/trail/riverbed surface band) and `bbt_curve_wet`
(river wetness band). The terrain material reads them via `ShaderNodeAttribute`, exactly the
paint-layer channel it already uses (`materials.py:1323-1329`). Then:
- **surface band -> a dedicated layer**: add a **Curve mask** channel to `_terrain_layer`
  alongside slope/altitude/noise/paint/flow (`materials.py:1337-1350`), gated by a
  `Curve Strength` socket. A road-surface / dirt / riverbed-silt layer with Curve Strength on
  wins the height-lerp only along the curve. This reuses the existing layer machinery with no
  structural rework, exactly as the Flow mask already keys the riverbed layer. Auto-config a
  role's layer the way `_autoconfig_riverbed` does (`materials.py:1183-1199`): a road gets a
  gravel/asphalt layer keyed to `bbt_curve_surface`.
- **wet band -> the wetness path**: route `bbt_curve_wet` into `Wetness Map` / `Terrain Wetness`
  (`materials.py:844-860`) so a riverbed reads damp and glossy, and inherits weather (rain
  amplifies it via `env_state_group`, `materials.py:716-726`).

Because the mask is a mesh attribute, nothing re-bakes: editing the curve updates the overlay,
which updates the attribute, which updates the shader, live.

**Many curves, one attribute.** A road network or several rivers all write the same named mask
(`bbt_curve_surface`, `bbt_curve_wet`). Each per-curve overlay must accumulate into the shared
attribute by MAX (take the strongest band at each texel), reading the existing value and writing
`max(existing, this_band)`, so overlapping curves add rather than overwrite. Distinct roles that
must not blend (a gravel road vs a silt riverbed) need distinct attributes and distinct keyed
layers. Keep the attribute set small: one surface band and one wet band cover dirt/road/river;
add a third only if a role genuinely needs its own layer.

### 4.5 Scatter (LIVE)

Generalize the scatter path effect (`scatter.py:212-217`) from "clear only" to a **path mode**
selected per curve/role:
- **clear**: density to zero in the band (current behavior).
- **keep-only**: scatter only within the band (reeds along a riverbank, weeds along a trail edge).
- **scatter-along**: place assets along the centreline using Resample Curve / Curve to Points
  (fence posts, cobblestones, boulders lining a road). This is a distribution mode distinct from
  the surface Poisson scatter and needs a small new recipe branch or a `scatter_along` recipe.
- **align-to-path**: orient instances to the `tangent` (rows of posts, aligned debris).

A scatter layer gains an optional "follow curve" binding (which curve + which mode). The curve
applier can also auto-create bank/edge layers per role, the way biome scatter builds a layer
stack (`scatter_panel.py:366-437`). A river curve then clears scatter in the water and keeps
reeds in the wet band from the same curve.

### 4.6 Water and wetness (rivers and streams)

- **Water surface**: the river role optionally builds a water mesh, a GN ribbon via Curve to
  Mesh with a flat profile at the water level (`path_z` minus a small inset), carrying a water
  BobShader. This is a new recipe (e.g. `curve_water`). Flow direction for the water shader comes
  from the `tangent` output of the curve field.
- **Damp bed**: the `bbt_curve_wet` attribute drives the terrain wetness path (4.4), so the bed
  reads wet independent of the separate water mesh.
- **Weather**: a wet dirt path role can add puddle wetness in its ruts scaled by `env.wetness`
  (already the master wetness input, `materials.py:716-726`); snow settles on roads and beds
  through the existing `snow_cover` slope/altitude logic (`materials.py:800-826`).

### 4.7 BAKED mode (optional, venv, for erosion coherence)

For rivers/canyons where natural erosion and the flow/wetness maps must follow the channel,
resample the curve to points in terrain UV space `[0,1]` and pass them to the venv bake:
- a new `carve` op in `_OPS` (`engine.py`) that rasterizes the polyline to a distance field on
  `xp` and subtracts a trench from the heightfield, sitting in the stack like `terrace`/`fluvial`;
- optionally a `path` selector in `SELECTORS` (`ops_select.py`) so any filter can be masked to
  the band, and/or a curve-mask argument to `derive_maps` (`maps.py:24`) to bias flow/wetness
  down the channel.
Serialize as a UV-point list inside a stack op (resolution-independent and cache-correct, per
the engine review; a pre-rasterized PNG breaks both). This is the reproducible/config-owned
path and re-bakes on edit, so it is an explicit "Bake curve into terrain" commit, not the live
loop. Implement after the LIVE channels.

### 4.8 Authoring model and ownership

- Curve geometry: scene-owned, artist-edited.
- Role + params: seeded at create from the role preset, then scene-owned. Live knobs
  (width/depth/falloff/bank) snapshot-restored on rebuild like any GN knob; structural choices
  (role, which channels are on) apply on an explicit Build; a Reset reverts to the role preset.
- No new UI idioms: role presets use `ui_helpers.preset_row`, Build uses
  `ui_helpers.structural_action`, the header uses `context_header`, the list mirrors scatter.
  This inherits the consistency from the recent UI subtraction pass.

## 5. UX and panel

BobSplines gets a new top-level panel labelled **Paths** in the BobBlenderTools tab, registered
from `splines_panel.py` with its own applier (the shape of the other subsystem panels). Place it
right after Terrain in pipeline order (`bl_order` between Terrain=1 and Scatter=2), because
draping needs a terrain to sample and the downstream stages (Scatter=2, Shaders=3) consume the
curve.

- A UIList of curves (add via a role preset picker, remove, duplicate, select), the scatter-layer
  idiom. "Add Curve" creates an editable curve to hand-draw; `make_path` remains the
  headless/agent authoring path.
- Per active curve: role preset, cross-section knobs (width, depth/height, bank width/slope,
  falloff), per-channel toggles (Terrain shape / Material band / Scatter / Water), Build This
  Curve and Build All via `structural_action`.
- Sub-panels: Cross-section, Material band, Scatter binding, Water (river/stream only).

## 6. MCP and op vocabulary

- Extend the authoring op: add `role` and role params to `MakePath`, or add an `apply_curve`
  orchestrator op (`tools/bobtools/mcp/contracts.py:41`), so an agent can author a typed curve
  and have its effects applied in one call.
- Add a `build_curves` applier op that, given the curve list, rebuilds the terrain overlay,
  material masks, scatter bindings, and water. Mirrors the Apply Biome orchestrator.
- BAKED mode adds the venv `carve` op and its contract (4.7).

## 7. Phased plan (each phase ships something usable)

- **C1 (plumbing, fixes the neglect) -- DONE 2026-07-21**: `bbt_curve` PropertyGroup +
  `bbt_curves` list + Paths panel with add/remove/duplicate/role. Create via `make_path` (now
  also the panel's Add) or hand-draw. Wire the EXISTING grade (terrain) and clear (scatter)
  effects through the panel, closing the gap where the Terrain bake never passes a path. Ships
  the follow-terrain family (dirt_path, trail, road) end to end; roles differ only in default
  knob values in C1 (cross-section profiles are C2). Files:
  - `blender/extensions/bob_blender_tools/splines_panel.py` (new): `BBT_Curve` (structural-only:
    role + channel toggles), `BBT_CurveEntry` (pointer, not name), `BBT_CurvesProps`, the Paths
    panel (`bl_order` 2, after Terrain) + Active Path sub-panel, and add/remove/duplicate/build
    operators. Build re-drapes then rebuilds the terrain in place with the path, then rebinds and
    rebuilds scatter. Cross-section knobs are drawn live off the terrain modifier (single owner).
  - `blender/bbmcp/path_curve.py`: new `drape_curve` op (re-drape an existing curve onto a
    terrain heightmap in place), sharing `_surface_z` with `make_path`'s drape. Registered in
    `dispatch.py`; contract `DrapeCurve` in `tools/bobtools/mcp/contracts.py`.
  - `__init__.py` `bake_terrain`: stores `bbt_terrain_res/height/sea` on the terrain object (with
    the existing `bbt_heightmap`/`bbt_terrain_size`) so a downstream grade rebuild is reproducible
    without re-baking.
  - `bl_order` renumbered across the panels to slot Paths=2 (Scatter 3, Shaders 4, Atmosphere 5,
    Advanced 6).
  Known C1 limits (deferred, not bugs): grades with the ACTIVE curve only (heightmap_terrain's
  single inline path; multi-curve overlay is C2); drape reads control points in local space, so
  the curve object is assumed at the origin; scatter clear uses the single `scn.path` binding
  (per-layer curve binding is C4).
- **C2 (shape) -- DONE 2026-07-21**: the standalone `curve_overlay` GN modifier replaces the
  inline path grade. One modifier per curve on the terrain object (so a network of paths composes,
  removing C1's single-curve limit), reads the incoming terrain geometry (works on any terrain
  mesh), levels the follow-terrain bench (path_z - depth within Path Width, eased over Falloff),
  and writes `bbt_curve_mask` (0..1, MAX-accumulated across curves) + `bbt_curve_dist` for
  consumers to READ (section 9 #2/#4). Cross-section knobs live on this overlay modifier
  (snapshot-restored, single owner). Files:
  - `blender/bbmcp/geonodes/recipes/curve_overlay.py` (new), registered in `recipes/__init__.py`.
  - `blender/bbmcp/geonodes/blocks.py`: new `curve_field(ng, path_obj) -> (distance, near_pos,
    path_z)`, the shared single-eval field. Retired `curve_path_sample` (curve_field supersedes it;
    its only user was the inline grade).
  - `heightmap_terrain.py`: inline path grade removed (superseded by the overlay); imports trimmed.
  - `splines_panel.py`: Build/Build-All now drape then attach/update a per-curve `BOB_Curve_<name>`
    overlay (positioned after the base terrain modifier, before `BOB_Snow`); Remove drops the
    overlay modifier; the Active Path panel draws the overlay modifier's knobs.
  Scope trimmed vs the original C2 wording: river channel and berm are OUT (rivers deferred by
  decision #1; berm/ridge is not in the confirmed follow-terrain family). `tangent`/`side` are
  deferred to their consumers (asymmetric embankment + scatter-align in C4) rather than shipping
  untested unused field outputs; `curve_field` is documented to gain them then. C2 roles still
  differ only in knob defaults and the profile is symmetric.
  Known C2 limits (deferred, not bugs): drape reads control points in local space (curve object
  assumed at origin); path_z uses the draped/authored curve Z (a Build-time drape, not the live
  per-eval GN re-drape of 4.2); scatter clear still uses the single `scn.path` binding (per-layer
  binding + reading `bbt_curve_mask` is C4).
- **C3 (material) -- DONE 2026-07-21**: the curve-mask attribute keys a dedicated Curve layer
  channel on the terrain material, and a role surface band is auto-configured. Files:
  - `blender/bbmcp/materials.py`: `terrain_master_group` gains a per-layer `L{i} Curve Strength`
    socket; `_terrain_layer` reads `bbt_curve_mask` (a ShaderNodeAttribute, like the paint
    channel) and multiplies it into the layer weight, gated by Curve Strength (the same shape as
    the Flow mask keying a riverbed layer). New public `apply_curve_surface(mat, base_color,
    roughness, height_bias)` mirrors `_autoconfig_riverbed`: it enables a layer keyed to the curve
    mask, reusing the already-keyed slot on re-apply, else the highest free slot, else the top
    slot. Height Bias is kept modest so the surface wins ON the curve but does not bleed OFF it
    (section 9 #7).
  - `blender/extensions/bob_blender_tools/splines_panel.py`: a `do_material` channel on
    `bbt_curve`; role `surface`/`surface_rough` colours; Build (and Build All, from the active
    role) call `apply_curve_surface` on the terrain's material.
  Scope trims: no wetness path (that rides with the deferred water/river work); one shared
  curve-surface layer keyed to the single `bbt_curve_mask`, so all paths share the surface look
  (distinct per-role surfaces need distinct mask attributes, a later step). Existing-scene caveat:
  the Curve channel is a new socket on the cached S_TerrainMaster group, so it only appears on a
  freshly built terrain material (delete the S_TerrainMaster node group to regenerate it); an
  older material makes `apply_curve_surface` return None and the panel says so.
- **C4 (scatter)**: path modes (keep-only, scatter-along, align) and per-layer curve binding;
  auto bank/edge layers per role.
- **C5 (water)**: river water-surface recipe with flow direction; weather puddle integration.
- **C6 (optional, baked)**: venv `carve` op + flow/wetness bias + "Bake curve into terrain".

## 8. Open questions

- One overlay modifier per curve (editability, snapshot parity) vs a single overlay iterating the
  list (fewer proximity solves). Start per-curve; revisit if perf bites with many curves.
- Curve material mask: a dedicated Curve channel on each layer (clearer) vs reusing the existing
  paint attribute (no new socket). Leaning dedicated Curve channel.
- Roles data-driven (a `roles.json` beside `presets.json`, generated/validated like the terrain
  presets) so new road/river types are added without code, vs roles in code.
- Overlapping curves: a road crossing a river. Compositing order and a possible bridge/culvert
  role are out of scope for C1-C5; note as future work.
- Should scatter-along reuse the scatter layer system or be its own light recipe. Leaning a
  `scatter_along` recipe branch invoked by the curve applier.

## 9. Design risks and hard cases (from critical review, 2026-07-21)

Grounded in the current GN/material/scatter code. The Tier-1 items should be resolved before C2.

**Decisions confirmed (2026-07-21, with Siva).**
- #1 scope: C1-C3 target the FOLLOW-TERRAIN family only (dirt path, road, trail). Rivers/streams
  are deferred to a later phase, after the monotonic-descending-Z solve and the carve-vs-ribbon
  question (risk #3) are settled. C1 ships this family.
- #2 ownership: `bbt_curve` holds ONLY structural fields (role + which channels are on). The
  cross-section knobs live ONCE on the terrain modifier (scene-owned, snapshot-restored). Scatter
  and, later, material/water READ the overlay's baked mask attribute rather than duplicating a
  knob. C1 honours this (no width/depth on `bbt_curve`); the shared baked attribute + single
  proximity solve (risk #4) land with the C2 overlay.

Tier 1 (shapes the architecture):
1. **Rivers vs paths have opposite drape semantics.** Paths/roads FOLLOW terrain (drape then
   level); a river must run monotonically downhill and the terrain must be cut down to it.
   Draping a river onto arbitrary terrain and cutting a fixed depth gives uphill water and a flat
   water ribbon that pokes through the ground where the curve dips then rises. `curve_path_sample`
   returns whatever Z the terrain has, with no monotonicity. Split roles into "follow" and
   "impose" families: rivers/streams need a monotonic descending centreline (solve/clamp Z, or
   artist source/mouth heights interpolated), and the terrain conforms to the river.
2. **Knob ownership across multiple targets.** One curve drives a terrain overlay, maybe a scatter
   layer, and a water mesh, on three datablocks. Duplicated width/depth knobs drift (the
   config-vs-scene trap, `docs/AUTHORING-MODEL-REVIEW.md`). Resolve: `bbt_curve` holds only
   structural (role, channels); the cross-section knobs live once on the terrain overlay modifier
   (scene-owned, snapshot-restored like scatter); scatter/water/material read the overlay's baked
   mask attribute, never a duplicated knob.
3. **Crisp features vs terrain tessellation.** `displace_z` cannot add geometry, so a 2-3 m
   road/path on a coarse terrain grid carves blocky and the per-vertex mask edge is stair-stepped;
   the shader cannot compute curve distance itself. Either locally subdivide the terrain along
   curves, or build roads as their own Curve-to-Mesh ribbon with its own material and carve/mask
   the terrain only for paths and rivers.

Tier 2 (correctness / performance):
4. **Per-grid proximity per curve, live, is a perf cliff.** `curve_distance` solves a proximity
   over the whole terrain grid (`_sample_grid_flat` = grid position) on every curve edit, and
   again per scatter layer. Compute each curve's fields ONCE in the overlay, bake
   `bbt_curve_dist/mask/side` attributes, and have material and scatter read them. Makes the
   shared field (4.2) load-bearing, not optional.
5. **Live xor Baked for shape; riverbed material source differs.** A curve baked into the
   heightfield AND carried by the live overlay is carved twice. The existing riverbed layer keys
   off the BAKED flow map, which a live-carved river is not in, so a LIVE river's bed material and
   wetness come from the curve mask; the flow-map riverbed stays for natural drainage only.
6. **Scatter-on-carved-terrain depends on order.** Object Info reads evaluated geometry
   (`blocks.py:143`), so scatter sees the carved terrain only if the overlay is a terrain modifier
   and the applier builds terrain -> overlay -> scatter. Enforce order; watch depsgraph lag.
7. **Height-Bias vs mask in the layer blend.** `H = weight + Height Bias + macro`
   (`materials.py:1352-1363`); off-curve `weight->0` so `H->Height Bias + macro`. Raising a curve
   layer's Height Bias makes it bleed past the curve, and a mask-gated layer only edges as crisp
   as `Blend Softness`. A hard road edge likely needs H gated by the mask directly.

Tier 3 (name now, solve later):
8. **Endpoints bulge**: proximity goes radial past a curve end, fanning the effect into a
   semicircle. Taper width or cap near the ends.
9. **Junctions/crossings**: MAX-compositing merges masks but not conflicting bench Z where roads
   cross or a tributary meets a river. Needs a Z rule, or accept v1 artifacts.
10. **Wire by PointerProperty, not name** (today's `scn.path.name` will not survive a network's
    renames/reorders).
11. **Steep-terrain roads**: a flat bench of width W on slope s implies cut/fill ~ `W*tan(s)/2`,
    a cliff at the falloff unless the falloff scales with the cut depth or the road terraces.

## 10. Key files index

- Curve authoring: `blender/bbmcp/path_curve.py`; dispatch `blender/bbmcp/dispatch.py:9`;
  contract `tools/bobtools/mcp/contracts.py:41-53`.
- GN blocks (reused + extended): `blender/bbmcp/geonodes/blocks.py`
  (`curve_distance`, `curve_path_sample`, `smooth_falloff`, plus new `curve_field`).
- Terrain recipe (inline path today, overlay tomorrow):
  `blender/bbmcp/geonodes/recipes/heightmap_terrain.py`.
- Scatter recipe (path effect): `blender/bbmcp/geonodes/recipes/scatter.py:152-288`.
- Terrain material (layer blend, flow/wetness, attribute reads): `blender/bbmcp/materials.py`
  (`terrain_master_group`, `_terrain_layer`, `terrain_material`, `_autoconfig_riverbed`,
  `weather_group`, `env_state_group`).
- Venv bake (baked mode): `tools/bobtools/heightfields/{pipeline,engine,maps,ops_erode,
  ops_select,ops_filter,params,presets,cache}.py`.
- BobSplines home (new): `blender/extensions/bob_blender_tools/splines_panel.py` (the Paths panel
  + applier), building on `blender/bbmcp/path_curve.py`.
- Panels the Paths panel will mirror: `blender/extensions/bob_blender_tools/{scatter_panel,
  __init__ (Terrain), shaders_panel, world_panel, ui_helpers}.py`.
- Prior design docs: `docs/AUTHORING-MODEL-REVIEW.md` (the ownership spine), `docs/TERRAIN.md`,
  `docs/SHADERS.md`, `docs/SCATTER-SHADING-UX.md`, `docs/BIOME-SYSTEM.md`, `docs/ARCHITECTURE.md`.
