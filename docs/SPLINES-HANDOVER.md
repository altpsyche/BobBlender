# Handover: BobSplines (curve system) — continue after C5

Status: 2026-07-21, branch fix/audit-remediation. For a fresh chat picking up BobSplines. The
durable spec is docs/SPLINES.md (phase plan, file maps at section 7, risk review at section 9);
this doc is the "where we are and what to do next" layer on top of it. Plain house style (no
em-dashes, no emojis), per the repo convention.

## Repo and environment
- /home/siva/dev/BobBlender — centralised Blender hub, Blender 5.2 LTS, Git + LFS.
- Siva is a technical artist: GN, materials, tools, rendering; not modeling.
- TESTING (updated 2026-07-21): there IS a Blender binary at
  `~/.steam/steam/steamapps/common/Blender/blender` (5.2), so bbmcp recipes/ops CAN be run and
  MEASURED headless -- `blender --background --factory-startup --python <script>.py`, importing
  bbmcp directly (no addon register needed; the recipes don't need it). This is how the C5 water
  geometry was verified (build terrain/curve/overlay/water, then evaluate meshes + ray_cast to
  measure Z relationships). A live MCP bridge into Siva's open session also exists (build_live over
  the socket) but only runs whitelisted bbmcp ops and needs a manual "Reload Builders" to pick up
  code changes. The static gate (py_compile + reference grep + pydantic round-trip) still runs
  first; the SHADER LOOK still needs Siva's eyes (headless can't judge the render).
- 5.2 API GOTCHA (bit us twice): some Geometry Nodes that had a `mode` enum PROPERTY now expose it
  as a menu SOCKET instead. `GeometryNodeResampleCurve.mode` and `GeometryNodeCurveToPoints.mode`
  both raise AttributeError. Set such modes defensively (try/except) and rely on the node default,
  or drive density another way (we used curve `resolution_u` for smoothing, and Curve to Points'
  default COUNT with a computed Count for spacing). If a fresh feature needs a specific node mode,
  have Siva run a 3-line probe in Blender's console to report the socket API rather than guessing.

## What BobSplines is
A curve gets a ROLE (dirt path, trail, road, river, stream) that drives four channels from ONE
curve: terrain shape (a cross-section carve), terrain material (a surface band or damp bed), scatter
(clear / keep-only / along-curve), and water (a river's flowing surface). LIVE Geometry Nodes by
default; a BAKED venv carve is a later optional commit step. Panel label "Paths", module
splines_panel.py.

## Confirmed Tier-1 decisions (with Siva, do not relitigate)
1. C1-C4 scoped to the FOLLOW-TERRAIN family (dirt_path, trail, road); rivers/streams were DEFERRED
   until the monotonic-descending-Z solve and the carve-vs-Curve-to-Mesh-ribbon question
   (docs/SPLINES.md section 9 #1 and #3) were settled. Both are now settled and the IMPOSE family
   (river/stream) shipped in C5 (see the C5 section below). Berm/ridge is still out of the family.
2. Single-owner knobs: the cross-section params have ONE owner. (Originally the overlay modifier,
   snapshot-restored; SUPERSEDED 2026-07-22 -- the owner is now bbt_curve on the curve object, see
   the UX unification below. Same principle, cleaner: no snapshot dance, one source, live-synced to
   both the carve and the water.) Consumers (material, scatter) still READ the overlay's baked
   bbt_curve_mask attribute rather than duplicating a knob or re-solving proximity.

## UX unification + live params (2026-07-22, geometry verified headless)
The river carve params (on the terrain overlay) and the water params (on the ribbon object) used to
be two disjoint live-knob sections drawn off the two modifiers; changing water depth did not touch
the terrain, Path Width was a radius (never 1:1 with the water), and the rebuild snapshot kept
clobbering the derived water width. Fixed by making bbt_curve the SINGLE owner of the shape params:
- bbt_curve gains FloatProperties (width, depth, falloff, taper, shoulder, bank_slope, bank_bias,
  bank_height, water_level, flow, foam_bank, foam_rapids), each with an update callback that pushes
  the value -- translated/derived -- onto BOTH the overlay modifier and the water ribbon
  (splines_panel._sync_curve_params), live, no Build. So one set of numbers drives both and stays in
  sync; changing Depth re-carves the terrain AND repositions the water (verified: depth 1.2->2.5 moved
  the bed -1.29 and the water -0.55).
- Width is now the FULL channel width (1:1): the overlay Path Width (a radius) gets width/2; the water
  ribbon Width is derived to fill the bed and meet the banks at the waterline. Role defaults re-scaled
  to full width (river 10, road 9, dirt 4.8, stream 4, trail 2.4).
- water_level (0..1 fill) replaces the old Water Depth knob: Water Depth = depth*(1-water_level), so
  the water sits inside the channel and the fill is intuitive.
- Build now (re)builds the overlay/water with reset=True (structural-only params) then calls
  _sync_curve_params; role change / Add re-seeds bbt_curve from the role (_seed_role_params). The
  panel draws ONE live "Shape" section (Cross-section / Banks / Water) off bbt_curve; the old
  _PATH_KNOBS/_WATER_KNOBS/_draw_mod_knobs/_mod_input plumbing is gone.
DEPLOY: this touches splines_panel.py (the ADDON), so a full addon reload (restart Blender, or F3
Reload Scripts) is needed, not just Reload Builders.

## Status: C1-C4 DONE + Blender-verified; C5 DONE, static-gated, awaiting Blender verify
C1-C4 committed on fix/audit-remediation (commits "Spline System" 9e38911, "improved spline system"
74ff5ac). Siva verified C1-C4 in Blender: the carved path is smooth, along-curve instances sit on
the terrain and stand upright. C5 (rivers) is implemented and static-gated (py_compile + reference
grep + a pydantic round-trip on the extended DrapeCurve contract), NOT yet Blender-verified -- see
the C5 section below for what to check. Detailed per-phase file maps are in docs/SPLINES.md section
7; the short version:

- C1 plumbing: Paths panel (splines_panel.py, bl_order 2 after Terrain) with a typed curve list
  bound by PointerProperty (not name); add/remove/duplicate/build. New `drape_curve` bbmcp op
  (path_curve.py, dispatch.py, DrapeCurve contract). bake_terrain stores bbt_terrain_res/height/sea
  on the terrain object. bl_order renumbered (Scatter 3, Shaders 4, Atmosphere 5, Advanced 6).
- C2 shape: standalone `curve_overlay` GN recipe, one modifier per curve on the terrain object
  (multi-curve), reads the incoming terrain geometry, carves the bench, and writes bbt_curve_mask
  (MAX-accumulated) + bbt_curve_dist. New `curve_field` block in blocks.py (removed the superseded
  `curve_path_sample`). Inline path grade retired from heightmap_terrain. A `carve` param makes a
  mask-only overlay (mask without displacement) when Terrain shape is off.
- C3 material: per-layer `L{i} Curve Strength` socket on terrain_master_group; `_terrain_layer`
  reads bbt_curve_mask into the layer weight; public `materials.apply_curve_surface(mat, color,
  rough, bias)` mirrors _autoconfig_riverbed (reuses the keyed slot, modest Height Bias so it wins
  on the curve but does not bleed off it, risk #7). Panel do_material channel + role surface colours.
- C4 scatter: the scatter recipe reads bbt_curve_mask as a density factor (curve_mode none/clear/
  keep); the old scn.path proximity is GONE. New `scatter_along` recipe places instances along a
  curve (Curve to Points, count = length / Spacing), projects them down onto the emitter so they
  sit on the ground, keeps them UPRIGHT and yaws them to the path when aligned, with knobs Offset
  (lateral), Z Offset, Yaw, Jitter, Spacing, Seed, Min/Max Scale. scatter_panel: BBT_ScatterLayer
  gains curve_mode/curve/curve_align; the layer routes to `scatter` or `scatter_along`.

### Bugs fixed this session (Blender feedback), in case they regress
- Carved path had steps. Three causes, all fixed: path_z read a curve VERTEX Z (Sample Nearest)
  and terraced -> now interpolated edge proximity Position on the draped curve; the curve evaluated
  too coarse -> resolution_u bumped to 128 at Build; the drape sampled the heightmap nearest-pixel
  -> now bilinear (matches the recipe's Linear sample).
- Along-curve spawned on the spline, floating. Fixed: scatter_along raycasts each point down onto
  the emitter and snaps it to the surface.
- Along-curve instances lay flat ("trees sleeping"). Fixed: was using Curve to Points' Rotation
  (aligns Z to the tangent); now upright + a Z-yaw to the heading only.

## Polish pass R1-R5 (DONE, static-gated, awaiting Blender verify)
Broad-sweep refinements to the shipped follow-terrain family, no new channel (docs/SPLINES.md
section 7 "Polish pass"). Static-gated (py_compile + reference grep); NOT yet Blender-verified.
- R1 live re-drape: the bench raycasts the terrain Z at the centreline LIVE each eval
  (curve_overlay `_live_terrain_z`), so it tracks a re-sculpt or a curve edit with no re-Build. The
  draped path_z is now only the off-mesh fallback; the Build-time drape still sits the curve wire.
- R2 hard road edge (risk #7): a per-layer `Curve Hard` socket mixes H toward a steep mask remap,
  so a road edges crisply regardless of Blend Softness (materials `apply_curve_surface(hard_edge=)`;
  road 1.0, dirt/trail 0.0). New socket on the cached S_TerrainMaster group.
- R3 endpoint taper (risk #8): `End Taper` fades the band over the last N metres of the curve and
  kills the radial semicircle past a spline end (blocks `_end_dist_field`, Spline Parameter/Length).
- R4 road shape (risk #11): `curve_field` gains `side`; the overlay adds `Shoulder Width`, a
  slope-aware embankment (`Bank Slope`, capped at 3x falloff), and `Bank Bias` (skew by side). Roles
  seed them. `tangent` is computed internally for `side`, not surfaced (no other consumer yet).
- R5 per-role surfaces + verge scatter: a SECOND material curve channel (Curve B off
  `bbt_curve_mask_b`) lets a road key its own layer vs dirt (`apply_curve_surface(channel=)`). The
  each curve's overlay writes its own `bbt_curve_edge_<curve>` (shoulder ring); scatter gets a
  **Verge (path edge)** curve_mode that keeps a layer to that ring, controlled in the Scatter panel
  like any layer. Verge takes a Curve (like Along) and follows THAT path's ring; with none bound it
  reads a name nothing writes, so it scatters nothing (empty = off, not "every path" -- Siva's call,
  so the pick is explicit). The earlier auto-`do_bank` toggle (a hidden auto-created layer) was CUT
  for readability, per Siva: verge is now just a scatter mode, no cross-panel magic. `scatter`
  recipe gained `curve_attr`; Verge routes it via `scatter_panel.edge_attr_name(curve)`.
- R6 junction Z (take-lower, risk #9): the overlay writes bbt_curve_carved (MAX coverage of carving
  curves) and clamps its carve so that where a prior curve carved it may only LOWER the surface,
  never raise it -> a crossing settles to the lower bench, order-independently, no last-writer
  clobber. Reads bbt_curve_carved (not bbt_curve_mask) so a mask-only path does not suppress a
  crossing road's fill. Shared-height junction / bridge-culvert still future.

VERIFY watch: the five new curve GN nodes (Spline Parameter / Spline Length / Curve Tangent / Sample
Nearest / Sample Index) are standard but NEW to this repo, so confirm they build on first Reload
Builders. R2 and R5-surfaces need a freshly built terrain material (delete S_TerrainMaster).

DEP-STALENESS FIX (2026-07-21, needs Blender verify): Siva hit "curves stop carving the terrain after
a re-bake or a curve edit/move; only delete + re-add fixes it, Build alone does not." Root cause: the
overlay reads the curve via an Object Info node (a node-level reference, not a modifier input), so
after the overlay modifier is rebuilt in place or the curve mutates, Blender does not always relink
the terrain->curve dependency and the overlay evaluates a stale curve. Fix: drape_curve now
obj.data.update_tag()s the curve after mutating points; _build_curve_overlay update_tag()s curve +
terrain; and both Build operators call context.view_layer.update() to rebuild the depsgraph relations.
If this proves insufficient, the deeper fix is to expose the curve as an explicit Object INPUT socket
on the overlay group (set on the modifier, so the dependency is declared) instead of on the node.

## C5 water / rivers — DONE 2026-07-21; geometry verified headless, shader look awaits Siva
The IMPOSE family (river/stream) shipped, resolving decision #1 and risks #1/#3 (see docs/SPLINES.md
section 7 C5 for the full file map). GEOMETRY VERIFIED HEADLESS: ran the full terrain -> monotonic
drape -> impose overlay -> water build through the Blender binary (5.2) against the real
library/_generated/Terrain_hf.png and measured water-vertex Z vs the ground and the carved bed --
after the densify fix, 0% of the water floats above the ground and it sits ~0.6 above the carved bed
along the whole run. The SHADER look (depth colour, scrolling flow, foam, freeze) still needs Siva's
eyes. What landed:
- C5.1 downhill drape + impose carve: `path_curve.drape_curve` gained a `monotonic` mode
  (`_monotonic_descend`): sample the terrain along the curve, then clamp the centreline into a
  downhill profile from the higher (source) end to the lower (mouth) end -- a running min-slope
  ceiling forces a continuous fall, and `to_sea` adds a linear source->sea ceiling so the mouth
  reaches sea level (absolute Z 0). It also gained `densify`: resample the curve to N points along
  its shape and rebuild it as one dense NURBS BEFORE sampling+solving. densify is load-bearing --
  measured headless, the raw 4 control points make `path_z` a smooth line that ignores the terrain
  (17% of the water floated above the ground); resampling to 48 points tracks the valley and drops
  it to 0%. `DrapeCurve` contract gained monotonic/min_slope/to_sea/densify. `curve_overlay` gained
  `impose`: when set, the bench target is the DRAPED monotonic `path_z` (not the R1 live raycast),
  so the terrain conforms DOWN to the water centreline. Path Depth is the bed below the water
  surface; the R4 embankment grades the banks back up. Roles seed densify 48, to_sea off.
- C5.2 water ribbon: new `curve_water` recipe. Curve to Mesh sweeps a flat line (channel width)
  along the curve for the XY route (Z-up normal, horizontal across width), then sets each vertex Z
  to `path_z - Water Depth`, where `path_z` is `curve_field`'s draped descending centreline -- the
  SAME solve the overlay carves the bed to (`path_z - Path Depth`). So the surface is Water Depth
  below the rim and stays in harmony with the bed BY CONSTRUCTION, with no read of the carved terrain
  (the spline-river model UE5/Torque3D/Waterways use: the curve drives the water, the terrain is
  carved to it). `curve_field` now returns a 6-tuple (its `tangent` was surfaced for the flow
  direction). Stores `bbt_flow` (unit downhill tangent * speed: faster on rapids, slower to the
  banks), `bbt_foam` (banks + steep), and `bbt_shore` (0 mid, 1 banks, from the centreline distance).
  Its own object `BOB_Water_<curve>`, built by the Paths panel AFTER the overlay drapes the curve.
- C5.3 water BobShader: `materials.S_WaterMaster` (`water_master_group` / `water_material`), the
  third master kind. Depth-colour gradient, foam, a frame-driven scrolling ripple normal advected
  along `bbt_flow` (no bake), transparency (Transmission + IOR + a bank Alpha fade). `_build_wrapper`
  widened to drive Transmission/IOR/Alpha/Normal into the Principled (no-op for surface/terrain).
  Ends in S_Weather so the below-freezing frost term freezes it to ice (Transmission + ripples
  collapse cold). `S_GROUP_VER` bumped 1 -> 2. Shaders panel: a `water` New option, a Water
  sub-panel, `_MASTER_TAG` entry; Weather sub-panel already applies (kind-agnostic poll).
- C5.4 damp bed: `curve_overlay` writes `bbt_curve_wet`; `terrain_master_group` MAXes it into the
  Wetness Map and `materials.apply_curve_wet` raises Terrain Wetness so the bed reads damp,
  weather-amplified. Scatter clears in the water band (the shipped `bbt_curve_mask` clear); reeds on
  the banks are a Verge scatter layer (shipped mode, no new code). Sea mouth = the `to_sea` drape.

VERIFY watch (C5): `GeometryNodeCurveLine` / `GeometryNodeResampleCurve` (count mode set defensively,
5.2 socket-vs-property) / `GeometryNodeCurveToMesh` WITH a profile are new to this repo; confirm the
ribbon builds and that the profile's `bbt_shore` attribute propagates onto the swept mesh (the one
part existing code does not already exercise -- curve_field uses Curve to Mesh with no profile). The
water master needs a freshly built material (S_GROUP_VER bump forces it; delete S_WaterMaster if in
doubt). Ripple animation needs playback / a frame change to move. Build order: Build drapes the curve
(monotonic) via the overlay, THEN lays the ribbon, so the ribbon sits on the descending centreline.

## C6 erosion after curves — DONE 2026-07-22, headless-verified (design v2, gap-fixed)
Siva: "the landscape becomes unnatural with all the curve modifications" -> run erosion after the
curves. Shipped as a "Bake & Erode Curves" commit (strength slider + whole-terrain/band scope toggle).

IMPORTANT design note (v1 -> v2, do not regress): v1 baked the carve INTO the heightfield and set the
live overlays to mask-only. Siva hit "erosion pushes the terrain down, the water and terrain have a
huge gap" -- because mask-only dropped the overlay's CONTAINMENT BANKS, so erosion lowered the banks
with nothing re-imposing them and the fixed-level water floated (the old C5 float bug, reintroduced).
v2 (current) does NOT bake the carve and does NOT go mask-only. Instead:
  erode the terrain -> swap the terrain to the eroded PNG -> RE-IMPOSE every curve on the eroded
  terrain (re-drape + overlay carve=True + rebuild the water).
So bed, banks and water all re-derive from the eroded path_z together (the C5 by-construction
harmony, re-established against the eroded ground). Verified headless: even with GLOBAL erosion
(terrain pushed down), 0% of the water's shore verts float -- banks sit above water exactly as the
clean build (mean terrain-above-water -3.5 m, 0 floating of 2208 shore verts).

What landed (after a cleanup pass):
- venv building blocks: `path` selector (ops_select -> ops_carve._distance_uv, the channel-band mask),
  pipeline `base_png` input + run_stack `normalize=False` (erode an existing PNG, keep its absolute
  height mapping), cache keys on the loaded base's content hash. ops_carve.py now holds ONLY the
  distance-field/profile helpers the selector needs. (A baked `carve` op with trench/impose modes was
  built + verified during development, then REMOVED in the cleanup as dead: option B never bakes a
  carve. Re-add it when the eroded-channel mode below is designed -- the impose take-lower + UV
  alignment math is recorded in git history / this doc.)
- Addon operator BBT_OT_curve_bake_erode (splines_panel.py): erosion-only stack (thermal + fluvial,
  scaled by scn.erode_strength; band scope masks to the curve corridor via `path`, global erodes
  all), bakes the CLEAN source (bbt_heightmap, or bbt_heightmap_clean if currently showing an eroded
  PNG) -> <stem>_hf_eroded.png via the shared `_run_host_bake` helper (__init__.py, also used by the
  Terrain bake), swaps the terrain onto it (bbt_heightmap = eroded, bbt_heightmap_clean = source),
  then re-imposes every channel curve (_build_curve_overlay carve=True re-drapes on the eroded
  terrain) and rebuilds the water -- one loop, mirroring Build All. Re-runs start from the clean
  source (idempotent).
- _curve_band_spec (splines_panel): the curve's UV polyline + channel width for the band mask, via
  path_curve._ordered_polyline_xy (shared with the drape). UV mapping verified: u = x/size + 0.5,
  v = 0.5 - y/size (PNG top-row-first, Blender samples V-up), lands on the curve, not mirrored.

TRADEOFF (v2): the channel itself is re-imposed (overlay embankment), NOT erosion-weathered; the
LANDSCAPE around/along it is what erodes. That fixes the gap and naturalises the terrain, which is
the complaint -- BUT Siva then noted "the banks still feel unnatural", which is this tradeoff biting:
the re-imposed embankment is a smooth procedural slope. A true "eroded channel + water re-fit to it"
is the open enhancement -- it needs the water to re-derive its fill level from the eroded channel
floor (a new water-fill model), not the current fixed path_z - WaterDepth. See the heightfield/erosion
research (in progress) before committing to it.
- NOT yet done (optional next): the eroded-channel mode above; emit flow/wetness maps on the eroded
  PNG for the riverbed material; a "revert to clean" button; global-scope strength tuning.
Remaining older item:
- Future (named, not scoped): a separate sea/ocean/lake surface; bridges/culverts where a road
  crosses a river (R6 take-lower currently sinks the road into the water); tributary networks and
  width-from-flow-accumulation beyond the simple downstream ramp.
- (Junction/crossing Z rule, risk #9 — DONE R6, take-lower: see the polish list above. A true
  shared-height junction or a bridge/culvert role is still future work.)

Note: the polish pass (R1-R5 above) landed the refinements the old handover listed here (live
re-drape, hard edge, endpoint taper, road shoulders/embankment, per-role surfaces, auto bank
scatter). `side` is now in curve_field (R4); `tangent` is computed there internally for `side` and
surfaced only when a scatter-align/other consumer needs it.

## How to re-test / continue in Blender
- After changing bbmcp GN or op code: Advanced panel > Reload Builders (refreshes bbmcp), then
  press the relevant Build (Paths: Build This Curve / Build All; Scatter: Build This Layer).
- The C3 Curve material channel and the C5 damp-bed read are new sockets/graph on the CACHED
  S_TerrainMaster group, and S_WaterMaster is a new group; the C5 `S_GROUP_VER` bump (1 -> 2)
  rebuilds all cached S_* groups in place on first access, so a freshly built material picks them up.
  On an existing .blend that still shows the old behaviour, delete the S_TerrainMaster / S_WaterMaster
  node group to force a rebuild.
- Static gate before handing to Siva: `python -m py_compile <files>`; grep that renamed/removed
  operator idnames and class names have no dangling refs.

## Key files
- Panel: blender/extensions/bob_blender_tools/splines_panel.py (Paths; river/stream roles, do_water,
  _build_water). Mirrors scatter_panel.py, world_panel.py (applier pattern), ui_helpers.py.
- Shaders panel: blender/extensions/bob_blender_tools/shaders_panel.py (water New option +
  BBT_PT_shaders_water sub-panel).
- Recipes: blender/bbmcp/geonodes/recipes/{curve_overlay,curve_water,scatter,scatter_along,heightmap_terrain}.py.
  curve_overlay gained impose + bbt_curve_wet; curve_water (NEW) is the water ribbon.
- GN blocks: blender/bbmcp/geonodes/blocks.py (curve_field, curve_distance, smooth_falloff,
  _curve_meshes; displace_z, object_geometry, math_node, random_value, position).
- Curve authoring + drape: blender/bbmcp/path_curve.py (make_path, drape_curve, _surface_z);
  dispatch blender/bbmcp/dispatch.py; contract tools/bobtools/mcp/contracts.py.
- Terrain material: blender/bbmcp/materials.py (terrain_master_group, _terrain_layer,
  apply_curve_surface, _autoconfig_riverbed, MAX_TERRAIN_LAYERS, master_type).
- Scatter panel + recipe: blender/extensions/bob_blender_tools/scatter_panel.py; scatter recipe
  reads bbt_curve_mask; scatter_along places along the curve.
- Venv bake (for C6): tools/bobtools/heightfields/{pipeline,engine,maps,ops_*,cache}.py.
- Spec + review: docs/SPLINES.md (section 7 phase file maps, section 9 risks). Ownership spine:
  docs/AUTHORING-MODEL-REVIEW.md.

Project memory (auto-loaded) indexes this at curve-system-plan.md (the phase status and the fixes).
