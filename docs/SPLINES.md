# BobSplines: typed curves that drive terrain, material, scatter, and water

Canonical reference, written from the code. The code is the single source of truth; this doc
describes what it does today.

BobSplines is the "Paths" panel: a curve gets a ROLE, and the role drives a coordinated bundle of
effects across four channels (terrain shape, terrain material, scatter, water) from one curve. The
curve is a scene-owned datablock the artist edits in the viewport; its role and shape params are
config that seed and drive the effects.

Homes:
- Panel: `blender/extensions/bob_blender_tools/ui/splines.py` (the Paths panel, the roles, the
  operators, and the per-curve `bbt_curve` data).
- Curve authoring + drape ops: `blender/extensions/bob_blender_tools/core/path_curve.py` (`make_path`, `drape_curve`,
  `inspect_river`).
- Recipes: `blender/extensions/bob_blender_tools/core/geonodes/recipes/curve_overlay.py` (terrain carve + masks),
  `curve_water.py` (water ribbon), `scatter_along.py` (instances along a curve).
- Material hooks: `blender/extensions/bob_blender_tools/core/materials.py` (`apply_curve_surface`, `apply_curve_wet`,
  `water_material` / `water_master_group`, `enable_eevee_refraction`).

## 1. What a typed curve is

A typed curve is an ordinary Blender curve object carrying a `bbt_curve` PropertyGroup
(`ui/splines.BBT_Curve`). The scene holds a list of them in `bbt_curves` (a `BBT_CurvesProps`
with a `curves` collection of `BBT_CurveEntry` rows, each a `PointerProperty` to the curve object,
so a row survives a rename). `bbt_curve` holds two kinds of config:

- STRUCTURAL: `role` plus the four channel toggles (`do_terrain`, `do_material`, `do_scatter`,
  `do_water`). These rebuild modifiers, so they apply only on a Build.
- SHAPE (live): `width`, `depth`, `falloff`, `taper`, `shoulder`, `bank_slope`, `bank_bias`,
  `bank_height`, `water_level`, `flow`, `foam_bank`, `foam_rapids`, `wave_amp`, `wave_len`,
  `wave_steep`, `wave_speed`, `wave_chop`, `width_var`. Each has an update callback
  (`_sync_curve_params`) that pushes the value to BOTH the terrain overlay modifier and the water
  ribbon in real time, so one set of numbers drives both with no re-Build. `width` is the FULL
  channel width in metres (the overlay takes `width * 0.5` as its Path Width radius).

There is one internal flag, `banks_from_erosion`, set by Bake & Erode and cleared by a fresh Build
(see section 6).

The curve is assumed to sit at the origin (its own control-point XY is used as the terrain sample
point). `_apply_curve_transform` bakes any grab-move of the curve object into its points and resets
the object matrix to identity before a build, so a moved curve still samples and carves correctly.

## 2. Role catalog

Roles live in the `ROLES` dict in `ui/splines.py`. There are five, in two families. The family
key `"impose"` marks river/stream; its absence marks the follow-terrain roles.

Follow-terrain family (the bench levels to the LIVE terrain height under the centreline, then
recesses by Depth):

- `dirt_path` ("Dirt Path"): a shallow worn trail draped onto the ground. width 4.8, depth 0.3.
- `trail` ("Trail"): a narrow footpath, barely recessed. width 2.4, depth 0.15.
- `road` ("Road"): a wide graded track, a flat bench plus shoulders, embanked on slopes. width 9.0,
  depth 0.4, shoulder 1.5. Keys its own material class (`surface_attr` = `bbt_curve_mask_b`, channel
  "b") and uses a hard surface edge, so a paved road reads distinct from dirt.

Impose family (the terrain conforms DOWN to a monotonic descending water centreline):

- `river` ("River"): a water channel that runs downhill. width 10.0, depth 1.2, water_level 0.58,
  wave_amp 0.10, width_var 0.35. Carries `drape` = {monotonic, min_slope 0.02, to_sea False,
  densify 48} and `wet` = 0.7 (writes `bbt_curve_wet`).
- `stream` ("Stream"): a narrow brook, shallow channel, quicker flow. width 4.0, depth 0.5, flow
  1.4, wave_len 2.5, wet 0.6. `drape` min_slope 0.03, densify 48.

Picking or changing a role re-seeds all shape params from the preset (`_seed_role_params`, the
`role` update callback). There is no separate Reset operator; re-picking the role is the reset.

## 3. How a curve drives each channel

Each channel is one of the four `do_*` toggles. A Build reads the toggles and applies only the
channels that are on.

### 3.1 Terrain shape (`do_terrain`)

Each curve gets its own Geometry Nodes overlay modifier on the terrain object, named
`BOB_Curve_<curvename>`, built from the `curve_overlay` recipe
(`blender/extensions/bob_blender_tools/core/geonodes/recipes/curve_overlay.py`). The overlay:

- Stacks after the base terrain modifier and before `BOB_Snow` (`_position_overlay`), so it carves
  the displaced surface and snow settles on the carved result. One modifier per curve, so a network
  of paths composes.
- FOLLOW roles: raycasts the incoming terrain straight down at the centreline to read the LIVE
  ground height (`_live_terrain_z`), levels a bench to that height minus Path Depth across Path
  Width plus a flat Shoulder Width, then grades back to the terrain over a slope-aware embankment
  (its width scales with the cut/fill depth to hold Bank Slope, capped at 3x Path Falloff; Bank Bias
  skews it to one side). Because the height is live, the bench tracks a terrain re-sculpt or a curve
  move with no re-Build.
- IMPOSE roles (`impose` param): carves a flat bed at the draped monotonic `path_z` minus Path
  Depth, and raises the banks to the higher of the natural terrain or a rim at `path_z` plus Bank
  Height, so level water stays contained even where the channel runs across a slope.
- Writes the mask attributes downstream effects READ (all MAX-accumulated across curves so
  overlapping paths add rather than overwrite): `bbt_curve_mask` (0..1, 1 on the band),
  `bbt_curve_carved` (coverage of curves that actually carve, for the junction rule),
  `bbt_curve_dist` (raw XY distance), the per-curve edge ring `bbt_curve_edge_<curvename>`
  (`edge_attr`), plus an optional per-role surface attribute (`surface_attr`) and wet attribute
  (`wet_attr`).
- Junction take-lower rule: where a prior curve already carved (read from `bbt_curve_carved`), this
  curve may only LOWER the surface, so a crossing settles to the lower bench order-independently.

Overlay inputs (synced live from `bbt_curve` by `_sync_curve_params`): Path Width (= width/2), Path
Depth, Path Falloff, End Taper, Width Variation, Shoulder Width, Bank Slope, Bank Bias, and Bank
Height (impose roles only).

A mask-only overlay is built with `carve=False` when a curve wants material/scatter/water but not
the terrain shape: it writes the masks but does not displace.

### 3.2 Terrain material (`do_material`)

`_apply_curve_material` configures the terrain's active material (which must be a Terrain BobShader):

- Follow roles: `materials.apply_curve_surface(mat, base_color, roughness, hard_edge, channel)` adds
  a terrain-material layer keyed to the curve mask, so the surface reads only along the path. Channel
  "a" keys off `bbt_curve_mask` (the shared band, dirt/trail); channel "b" keys off
  `bbt_curve_mask_b` (a distinct class, the road), so a paved road and a dirt trail read
  differently. `hard_edge` (road 1.0, dirt/trail 0.0) gates the edge crisp.
- Impose roles: `materials.apply_curve_wet(mat, wetness)` routes `bbt_curve_wet` into the terrain
  Wetness path and raises Terrain Wetness, so the carved bed and banks read damp and glossy under
  the transparent water. Non-lowering and idempotent.

If the terrain has no Terrain BobShader the material application returns None and Build reports a
warning. In Build All the follow family keys one layer per distinct channel (deduped), so a road and
a trail get separate layers.

### 3.3 Scatter (`do_scatter`)

Scatter reads the baked `bbt_curve_mask`, not a per-curve proximity solve, so every curve with an
overlay is respected at once. `_clear_scatter` flips any scatter layer still set to `curve_mode`
"none" over to "clear" and rebuilds the Scatter emitter's layers via
`bob_blender_tools.scatter_build_all`.

Per-layer curve binding lives in the Scatter panel (`ui/scatter.BBT_ScatterLayer.curve_mode`),
with these modes:

- `none`: ignore curves.
- `clear`: multiply density by (1 - mask), so the layer clears along the whole path band.
- `keep`: multiply by the mask, so the layer scatters only in the band.
- `verge`: scatter only on the path shoulder / edge ring, reading `bbt_curve_edge_<curvename>`
  (`ui/scatter.edge_attr_name`); needs a curve bound, else it reads a name nothing writes and
  scatters nothing.
- `along`: switch the layer to the `scatter_along` recipe, placing instances ALONG the bound curve.

The `scatter_along` recipe (`blender/extensions/bob_blender_tools/core/geonodes/recipes/scatter_along.py`) spaces instances
evenly by curve length / Spacing, offsets them sideways, projects them down onto the emitter so they
sit on the terrain, and keeps them upright (align yaws them to the path heading, else a random Z
spin). Its knobs: Spacing, Offset, Z Offset, Yaw, Jitter, Seed, Min Scale, Max Scale.

### 3.4 Water (`do_water`, impose roles only)

The Water channel exists only for the river/stream family; the panel hides the toggle for follow
roles. `_build_water` builds a water-surface ribbon object named `BOB_Water_<curvename>` from the
`curve_water` recipe (`blender/extensions/bob_blender_tools/core/geonodes/recipes/curve_water.py`), then shades it with
`materials.water_material` (a water BobShader) via a Set-Material modifier and calls
`enable_eevee_refraction`.

The ribbon must be built AFTER the overlay drape: it derives its Z from the SAME draped descending
centreline (`curve_field`'s `path_z`) the overlay carves the bed to, so surface and bed stay in
harmony by construction (the surface sits Water Depth below the rim, always above the bed, never
floating). Curve to Mesh sweeps a flat horizontal line the channel wide along the curve; each vertex
Z is set to `path_z - Water Depth`.

The ribbon stores shading fields the water shader reads: `bbt_shore` (0 mid-channel, 1 at the
banks), `bbt_flow` (unit downhill tangent scaled by a speed that rises on rapids and slows at the
banks), `bbt_foam` (banks plus steep sections), `bbt_depth` (per-vertex water-column depth for
Beer-Lambert absorption), and `bbt_water_uv` (arc-length U, across-width V for a flow-aligned detail
normal). Real animated Gerstner waves displace the surface, driven by Scene Time so they move on
playback with no bake.

Ribbon inputs (synced from `bbt_curve`, derived to fill the channel and meet the banks): Width,
Water Depth, Bed Depth, Width Variation, End Taper, Flow Base, Foam Bank, Foam Rapids, Wave
Amplitude, Wave Length, Wave Steepness, Wave Speed, Wave Chop.

## 4. The drape and the shared field

At Build the curve is draped onto the terrain when the terrain carries a baked heightmap
(`terrain["bbt_heightmap"]`); without one the overlay carves using the curve's authored Z. Draping
runs through the `drape_curve` op (`path_curve.py`):

- FOLLOW roles: each control point's Z is set to the heightmap surface height at its XY (bilinear
  sample, matching `heightmap_terrain`'s displace), so the smooth curve follows the ground.
- IMPOSE roles: the role's `drape` dict adds `monotonic` (clamp the centreline into a downhill
  profile from source to mouth via `_monotonic_descend`, using a running min-slope ceiling), and
  `densify` 48 (resample the curve to 48 points along its evaluated shape BEFORE the solve, then
  rebuild it as one dense NURBS). Densify is load-bearing: sampling only the few control points lets
  the water float over dips (measured 17% floating with 4 points, 0% with 48). `to_sea` (off by
  default) would pull the mouth to sea level (absolute Z 0).

Points that fall off the terrain footprint are clipped before the solve (`_clip_xy_to_terrain`), so
an off-terrain excursion cannot drive a runaway trench; the Active Path panel warns when a curve
leaves the terrain.

`curve_overlay` and `curve_water` both read the curve through one shared `curve_field` block, so a
curve is solved once and the surface, bed, and masks all derive from the same solve.

## 5. LIVE vs BAKED

LIVE (the default authoring loop):
- The terrain overlay modifier, the water ribbon, the material mask layers, and scatter are all live
  Geometry Nodes / material state. Editing any shape param on `bbt_curve` syncs to both the overlay
  and the ribbon immediately (`_sync_curve_params`), so dragging Depth re-carves the terrain and
  repositions the water together, with no re-Build. Non-destructive: the source heightmap is
  untouched.

BAKED (optional commit, Bake & Erode):
- `bob_blender_tools.curve_bake_erode` folds the carves into the heightfield and weathers them. It
  needs a terrain with a baked heightfield (the erosion runs on the raster, not the live carve). It
  runs the venv erosion stack on the CLEAN source PNG: per curve a `channel_seed` cuts a shallow bed
  along the centreline, then `thermal` slumps banks at a noise-warped repose angle and `fluvial`
  incises with a drainage prior boosted along the spline, and (optional) a `deposit` pass settles
  point bars. It emits flow/wetness sibling maps beside the eroded PNG, swaps the terrain to the
  eroded PNG (recording the clean source as `bbt_heightmap_clean`), then re-imposes every curve on
  the eroded terrain.
- After an erode the channel LIVES IN the eroded heightfield, so each Terrain curve gets
  `banks_from_erosion = True`: the overlay then carves only a shallow guarantee bed to hold water,
  and the graded shoulder/bank is dropped so the eroded banks show. The panel greys the Depth and
  graded-bank knobs with a note in this state (they are forced; Build re-imposes them).
- Scope (`erode_scope`): "band" erodes only the channel corridor; "global" re-erodes the whole
  terrain with the channels present. `erode_strength` scales the weathering; `erode_deposit` toggles
  the point-bar deposition pass.
- `bob_blender_tools.curve_revert_erode` swaps the terrain back to the clean heightfield and
  re-imposes every curve with the full graded channel (clears `banks_from_erosion`).

Note: there is no standalone venv "carve" op; the baked path is the erosion pipeline above.

## 6. Operators

All in `ui/splines.py`:

- `bob_blender_tools.curve_add` ("Add Curve"): takes a `role`; creates a curve via `make_path` with a
  short straight starter line, seeds the role params, adds it to the list, and makes it active for
  edit-mode shaping. Drawn as an `operator_menu_enum` on the role so the artist picks a type.
- `bob_blender_tools.curve_remove` ("Remove Curve"): deletes the active curve, drops its overlay
  modifier (searched across ALL meshes, not just the current terrain pick, so a changed pick cannot
  orphan it) and its water ribbon, and rebuilds scatter.
- `bob_blender_tools.curve_duplicate` ("Duplicate Curve"): copies the curve with its own data and
  role config.
- `bob_blender_tools.curve_build` ("Build This Curve"): drapes and builds the active curve's on
  channels: the overlay (carve when `do_terrain`, else mask-only), the material band or damp bed,
  the water ribbon (impose), the scatter clear, then syncs the shape params. Errors if a channel
  needs a terrain and none is picked.
- `bob_blender_tools.curve_build_all` ("Build All"): builds every curve with a channel on; keys one
  material layer per distinct surface class; one scatter rebuild covers all.
- `bob_blender_tools.curve_bake_erode` ("Bake & Erode Curves"): the BAKED commit (section 5).
- `bob_blender_tools.curve_revert_erode` ("Revert to Clean"): undo the erode (section 5).

The panel (`BBT_PT_paths`, `bl_order` 3, after Terrain and before Scatter) shows the terrain pick,
the curve list with add/remove/duplicate, Build All, and a "Naturalise landscape (Bake & Erode)" box
when the terrain has a bake. The `BBT_PT_paths_active` sub-panel shows the active curve's structural
group (role + channel toggles + Build This Curve) and its live Shape / Banks / Water / Waves knobs.

The terrain the curves act on is `bbt_curves.terrain`, defaulting to the Scatter emitter, else the
active mesh (`_terrain`).

## 7. Driving from the panel

1. Pick or bake a terrain in the Terrain panel, and select it in the Paths panel (or let it default
   to the Scatter emitter / active mesh).
2. Add Curve, choose a role. Shape the curve in edit mode.
3. In Active Path, set the channels (Terrain shape / Material band or Damp bed / Scatter / Water),
   then Build This Curve (or Build All in the main panel).
4. Tune the live Shape / Banks / Water / Waves knobs; they update the carve and water in real time.
5. Optionally Bake & Erode to weather the landscape, or Revert to Clean.

Reeds on the banks: add a Scatter layer with Curve = Verge, bound to the path.

## 8. Driving from MCP

Curve authoring and drape ops (dispatched in `blender/extensions/bob_blender_tools/core/dispatch.py`, implemented in
`path_curve.py`):

- `make_path`: build a NURBS curve. Params: `name`, `points` (>= 2), `resolution`, and optionally
  `heightmap`/`size`/`height`/`sea_level` to drape control points at author time. Idempotent by
  name. This is the same op the panel's Add Curve uses.
- `drape_curve`: re-drape an existing curve onto a terrain heightmap in place. Params: `name`,
  `heightmap`, `size`, `height`, `sea_level`, plus the river options `monotonic`, `min_slope`,
  `to_sea`, `densify`. Returns `created` on success, `dropped` when off-terrain points were clipped,
  or an info-only dict when the curve lies entirely off the terrain.
- `inspect_river`: read-only diagnostic that measures a built water ribbon against the terrain and
  reports how many water verts float above the banks vs sit in the carved channel.

The GN recipes are built through `build_geonodes` / `build_geonodes_on_object` (the panel uses these
in-process): `curve_overlay` (on the terrain object), `curve_water` (its own object), and
`scatter_along` (on the scatter emitter). Recipe params are as documented in each recipe module.

## 9. Attributes and object naming (reference)

- Overlay modifier: `BOB_Curve_<curvename>` on the terrain object.
- Water object: `BOB_Water_<curvename>`.
- Mask attributes on the terrain (written by `curve_overlay`, MAX-accumulated): `bbt_curve_mask`,
  `bbt_curve_mask_b` (road surface class), `bbt_curve_carved`, `bbt_curve_dist`, `bbt_curve_wet`
  (impose), `bbt_curve_edge_<curvename>` (verge ring).
- Water ribbon attributes (written by `curve_water`): `bbt_shore`, `bbt_flow`, `bbt_foam`,
  `bbt_depth`, `bbt_water_uv`.
- Terrain object custom props read: `bbt_heightmap`, `bbt_heightmap_clean`, `bbt_terrain_size`,
  `bbt_terrain_height`, `bbt_terrain_sea`, `bbt_terrain_res`.
