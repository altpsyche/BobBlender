# Handover: BobSplines (curve system) — continue after C4

Status: 2026-07-21, branch fix/audit-remediation. For a fresh chat picking up BobSplines. The
durable spec is docs/SPLINES.md (phase plan, file maps at section 7, risk review at section 9);
this doc is the "where we are and what to do next" layer on top of it. Plain house style (no
em-dashes, no emojis), per the repo convention.

## Repo and environment
- /home/siva/dev/BobBlender — centralised Blender hub, Blender 5.2 LTS, Git + LFS.
- Siva is a technical artist: GN, materials, tools, rendering; not modeling.
- CONSTRAINT: there is NO Blender binary or live MCP bridge in the CLI env, so the addon (imports
  bpy) cannot be registered or drawn headless. The static gate is `python -m py_compile` on every
  touched file, plus grepping that removed/renamed operator idnames and class names have no
  dangling refs, plus a pydantic round-trip for new MCP ops. Visual verification means Siva
  reloads the addon in Blender. Siva is at the keyboard in Blender and reports back.
- 5.2 API GOTCHA (bit us twice): some Geometry Nodes that had a `mode` enum PROPERTY now expose it
  as a menu SOCKET instead. `GeometryNodeResampleCurve.mode` and `GeometryNodeCurveToPoints.mode`
  both raise AttributeError. Set such modes defensively (try/except) and rely on the node default,
  or drive density another way (we used curve `resolution_u` for smoothing, and Curve to Points'
  default COUNT with a computed Count for spacing). If a fresh feature needs a specific node mode,
  have Siva run a 3-line probe in Blender's console to report the socket API rather than guessing.

## What BobSplines is
A curve gets a ROLE (dirt path, trail, road, ... rivers later) that drives four channels from ONE
curve: terrain shape (a cross-section carve), terrain material (a surface band), scatter (clear /
keep-only / along-curve), and water (rivers, deferred). LIVE Geometry Nodes by default; a BAKED
venv carve is a later optional commit step. Panel label "Paths", module splines_panel.py.

## Confirmed Tier-1 decisions (with Siva, do not relitigate)
1. Scope C1-C3 (and C4) to the FOLLOW-TERRAIN family only: dirt_path, trail, road. Rivers and
   streams are DEFERRED until the monotonic-descending-Z solve and the carve-vs-Curve-to-Mesh-ribbon
   question (docs/SPLINES.md section 9 #1 and #3) are settled. Berm/ridge is also out of the family.
2. Single-owner knobs: bbt_curve (on the curve object) holds ONLY structural fields (role + which
   channels are on). The cross-section knobs live ONCE on the curve's overlay modifier
   (snapshot-restored). Consumers (material, scatter) READ the overlay's baked bbt_curve_mask
   attribute rather than duplicating a knob or re-solving proximity.

## Status: C1-C4 DONE, committed, Blender-verified
Committed on fix/audit-remediation (commits "Spline System" 9e38911, "improved spline system"
74ff5ac). Working tree clean. Siva verified in Blender: the carved path is smooth, along-curve
instances sit on the terrain and stand upright. Detailed per-phase file maps are in docs/SPLINES.md
section 7; the short version:

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
  recipe gained `curve_attr`; Verge routes it via `scatter_panel.edge_attr_name(curve)`. Junction Z
  (risk #9) stays noted-as-future.

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

## What is next (pick with Siva)
Remaining phases are the deferred and optional ones:
- C5 water / rivers — DEFERRED by decision #1. Blocked on: a monotonic-descending centreline
  solve (source/mouth heights, clamp Z) so a river runs downhill and the terrain cuts DOWN to it
  (opposite drape from a path), and the "carve the terrain vs lay a Curve-to-Mesh water ribbon"
  choice (risk #3). This is the natural next DESIGN task; do the design before building.
- C6 baked venv carve (optional) — rasterize the curve to a distance field in the heightfield
  bake (a `carve` op in tools/bobtools/heightfields/engine.py `_OPS`) so natural erosion and the
  flow/wetness maps respect the channel. A "Bake curve into terrain" commit step, not the live loop.
- Junction/crossing Z rule (risk #9) — the masks MAX-composite but the bench Z does not, so where
  two curves cross the later overlay levels to its own centreline and clobbers the other. Needs a Z
  rule (take-lower) or a bridge/culvert role. Left as-is (Tier-3).

Note: the polish pass (R1-R5 above) landed the refinements the old handover listed here (live
re-drape, hard edge, endpoint taper, road shoulders/embankment, per-role surfaces, auto bank
scatter). `side` is now in curve_field (R4); `tangent` is computed there internally for `side` and
surfaced only when a scatter-align/other consumer needs it.

## How to re-test / continue in Blender
- After changing bbmcp GN or op code: Advanced panel > Reload Builders (refreshes bbmcp), then
  press the relevant Build (Paths: Build This Curve / Build All; Scatter: Build This Layer).
- The C3 Curve material channel is a new socket on the CACHED S_TerrainMaster node group, so it
  only appears on a freshly built terrain material. On an existing .blend, delete the
  S_TerrainMaster node group (or start fresh) to regenerate it; apply_curve_surface returns None on
  an old group and the panel says to shade it in Shaders.
- Static gate before handing to Siva: `python -m py_compile <files>`; grep that renamed/removed
  operator idnames and class names have no dangling refs.

## Key files
- Panel: blender/extensions/bob_blender_tools/splines_panel.py (Paths). Mirrors scatter_panel.py,
  world_panel.py (applier pattern), ui_helpers.py.
- Recipes: blender/bbmcp/geonodes/recipes/{curve_overlay,scatter,scatter_along,heightmap_terrain}.py.
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
