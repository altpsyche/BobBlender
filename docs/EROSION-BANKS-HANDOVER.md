# Handover: erosion + natural riverbanks (continue next session)

Status 2026-07-22, branch fix/audit-remediation. Plain house style (no em-dashes/emojis). Picks up
after the water-shader work and the "erosion after curves" (C6) feature. Read WATER-SHADER-HANDOVER.md
and SPLINES-HANDOVER.md (C6 section) first for the grounded state.

## DONE this session (bank-realism v3, UNCOMMITTED)
The verdict + plan below was delivered. Plan items 1, 2, 3, 5 are IMPLEMENTED and headless-verified;
item 4 is deferred (optional, tune against the look); item 6 stays deferred. What changed:
- Engine (venv). New `channel_seed` op (ops_carve.py): a shallow bed carve along the spline so the
  fluvial solver has a slope + depression to amplify. `fluvial` gained a `flow_prior`
  ({curves,width,falloff,gain}) that boosts drainage area A along the spline before stream-power
  incision, so the valley is cut WHERE THE RIVER IS (measured: incision concentrated 10-15x on-path;
  at the default strength the channel incises ~1.27m, matching the 1.2m authored river depth).
  `thermal` gained a per-cell noise-warped repose angle (`talus_warp`/`talus_freq`, high-persistence
  fbm `_fbm01`), so banks are not one uniform ruled slope. fluvial passes talus_warp through to its
  inner thermal. Registered `channel_seed` in engine._OPS. 3 new tests in tools/tests (41 pass).
- Addon (splines_panel.py). BBT_OT_curve_bake_erode builds the new stack: per-curve channel_seed +
  warped thermal + fluvial with the drainage prior (gain/warp scale with erode_strength). It NO
  LONGER re-imposes the graded swept embankment: a new per-curve flag `banks_from_erosion` (set by
  Erode, cleared by a fresh Build/Build All) makes the overlay carve only a SHALLOW wet bed
  (_guarantee_depth = clamp(depth*0.4, 0.2, 0.6), Shoulder/Bank Height 0) so the visible banks are
  the eroded terrain. Water (item 5): _derived_water branches on the flag; path_z now samples the
  eroded floor, water sits water_level into the shallow guarantee trough, ribbon reaches to the
  guarantee wall. _curve_band_spec now also returns the normalised seed `depth` (metres/height).
- Verified headless (Blender 5.2 binary, exact operator defaults, river role): water CONTAINED at 0%
  float by the 0.5m bar (maxgap 0.37m, mean gap -0.46m). Hillshade + removed-material map
  (library/_generated/banks_hillshade_cmp.png, gitignored) shows the S-channel carved along the
  spline with natural width variation. Scripts: scratchpad e_designB.py (containment), tune_channel.py
  (channel-depth sweep), hillshade_cmp.py, measure_banks2.py (prior concentration).

## DONE (item 4, deposition/point-bars, UNCOMMITTED on top of v3)
Erosion2-style sediment settling, so the incised channel is no longer a bare cut V.
- Engine (venv). New `deposit` op (ops_erode.py): raises the bed where flow slackens (drainage area
  high but slope low), gated by `flow_floor` to the wet channel, then slumps the fresh sediment to a
  gentle repose (inner thermal). Alluviates the valley floor into a flatter floodplain + grows gentle
  inner-bend bars; monotone add per step; deterministic, vectorised. Registered in engine._OPS. 2 new
  tests (adds material in-channel; deterministic) -> 43 pass.
- Addon (splines_panel.py). New scene toggle `erode_deposit` (default on) + a checkbox in the
  Naturalise box. BBT_OT_curve_bake_erode appends `deposit` after fluvial, ALWAYS masked to the
  corridor (bars belong in the band whatever the erode scope), amount/iterations scale with
  erode_strength. Safe by construction: the overlay re-carves the shallow wet-bed guarantee after the
  bake, so filling the floor cannot push the water out.
- Verified headless (venv, addon stack params at strength 0.5, scratchpad verify_deposit.py): +1.26
  mass added in-corridor, 0 leak far-field (mask correct), max floor fill 0.011 < the 0.03 seed depth
  (guarantee trough survives), channel STILL incised 0.17 below the banks.

## BUGFIX (off-terrain curve point flattened the terrain, UNCOMMITTED)
Report: dragging a curve control point OFF the terrain (or pulling one to a big height) flattened
the whole terrain to the ground on Build AND Bake & Erode. Root cause (confirmed headless): a river
is a monotonic downhill solve (_monotonic_descend), and its min-slope ceiling is
`running - min_slope * seg`. A point dragged off the terrain makes one segment's arc length huge
(out-and-back), so the bed ceiling drops metres-per-metre of stray distance and the whole downstream
channel runs away deep (measured: endpoint at x=3000 drove the bed to -58m); the impose overlay then
carves the terrain down to that runaway bed, reading as "everything flattened to the ground". The
erode path had the same poison via _curve_band_spec (it clamped off-terrain points to the [0,1] rim,
smearing the seed/drainage-prior along the edge).
Fix: clip the curve polyline to the terrain footprint (|x|,|y| <= size/2) BEFORE the solve.
- blender/bbmcp/path_curve.py: new `_clip_xy_to_terrain`; drape_curve's densify (river) branch clips
  before resample + monotonic descend, skips draping a curve that lies entirely off the terrain, and
  returns a `dropped` count.
- splines_panel.py: `_curve_band_spec` clips (not clamps) before the UV map; `_build_curve_overlay`
  surfaces the drop so Build reports "clipped off-terrain points".
Verified headless (repro2.py): endpoint at x=800/3000 -> bed stays -6.3m (was -14.7 / -58.5), terrain
std ~= the clean 4.44, relief intact; erode stays contained. Scripts: scratchpad repro.py, repro2.py.

## STILL TODO (next)
- Hand the LOOK to Siva in Blender (EEVEE + Cycles): run Bake & Erode on a river scene, judge the
  eroded banks + contained water + the new floodplain/bars. Tune erode_strength (deeper valleys),
  erode_deposit on/off, deposit amount / _guarantee_depth to taste.
- Water pass W5 (depth absorption + soft shoreline). Pairs with the eroded-floor water now in place.

## Where we are
- Water shader look pass: COMMITTED ("improved water", bc56537). Gerstner geometry waves, EEVEE
  refraction, foam, freeze. Water W5 (depth absorption / soft shoreline) is still QUEUED, not started.
- Erosion after curves (C6 v2): IMPLEMENTED + headless-verified + cleaned up, UNCOMMITTED. Erode the
  terrain (thermal + fluvial, band or global scope, strength slider) then RE-IMPOSE the spline channel
  on the eroded terrain so bed/banks/water re-derive together (0% water float verified). Files: venv
  tools/bobtools/heightfields/{ops_carve.py (distance-field + `path` selector helpers only), engine.py,
  ops_select.py (sel_path), pipeline.py (base_png + normalize=False), cache.py}; addon
  blender/extensions/bob_blender_tools/{splines_panel.py (BBT_OT_curve_bake_erode, _curve_band_spec,
  Naturalise panel box), __init__.py (_run_host_bake shared helper)}.
- Cleanup pass: DONE (removed the dead baked-`carve` op + impose/target machinery, extracted the shared
  bake-subprocess helper, dedup vs path_curve._ordered_polyline_xy, collapsed loops).

## The open problem (this is the task)
Siva: "the gap is fixed, but the banks still feel unnatural." Root cause: v2 RE-IMPOSES a smooth swept
cross-section (graded embankment) on the eroded terrain, so the channel is the one feature that never
got eroded, and even where erosion runs, the model barely touches flat/gentle banks. Smooth graded
banks are inherently artificial.

## Research verdict (deep-research ran; synthesis step died on the session limit, but the verified
claims are captured below and in /tmp/.../tasks/wkl2qikqc.output + the workflow journal.jsonl).
VERDICT: the heightfield op-stack architecture is SOUND, keep it. The problem is river integration, not
the pipeline. Do NOT rewrite the engine. Key findings (confirmed unless noted):
- Op-stack-over-a-grid is the industry standard: Houdini HeightField is a chained node stack; Mei 2007
  is heightfields-on-a-grid; Gaea Erosion2 is combined hydraulic+thermal. (World Creator uses layers
  not nodes; Cordonnier 2016 uses a Delaunay graph not a grid; both are minority counterpoints, not a
  reason to switch.)
- Houdini's Erode exposes a "Bank Angle" control: banks are a TUNABLE EROSION OUTPUT, not a fixed
  graded cross-section. Every Houdini erosion param accepts a MASK (localized erosion is standard).
- Gaea Rivers is a downhill-flowing NETWORK from headwater points + tributaries with river-aware flow
  correction built in, not a single hand-carved spline. It reshapes the terrain to guarantee a
  pathway. ("Rivers early in the graph so erosion takes the pathway into account" appeared but the
  strict claim was voted refuted 1-2; treat carve-early as plausible-but-contested.)
- WHY smooth banks look wrong (sourced to Mei 2007 + Cordonnier 2016, votes errored on the limit so
  re-verify): Mei sediment capacity C = Kc*sin(alpha)*|v| collapses toward 0 on flat/gentle slopes, so
  beds + gentle banks barely erode (under-detailed, smooth). A UNIFORM talus/repose angle yields
  artificial regular geometric valleys; the fix is to spatially modulate the max slope with
  high-persistence 3D Perlin noise (they used 6-54 degrees) for natural variation.
- Natural banks come from UNDERCUTTING + gravitational bank COLLAPSE at the repose angle (St'ava 2008),
  not from a swept profile. Erosion2 adds 3 sediment classes + DEPOSITION (scree, alluvial fans, point
  bars) that pure stream-power incision lacks.
- Stream-power can be SEEDED by a river path/strokes and then the valley + banks EMERGE from erosion
  (Cordonnier 2016). Genevaux 2013 is hydrology-first (river network drives terrain) with typed Rosgen
  cross-sections layered bedrock/gravel/sand.

## Recommended plan (do the ponder writeup first, then build; prioritized)
1. Stop re-imposing a smooth bank. Make the bank an erosion OUTPUT: after the spline seeds the channel,
   let thermal + fluvial shape the banks; DO NOT stamp the graded embankment back on top. Keep only a
   shallow bed guarantee.
2. Seed the fluvial solver with the spline as a DRAINAGE PRIOR: boost flow-accumulation along the curve
   path (add A along the polyline before the stream-power incision) so the solver cuts the valley where
   the river is. This reshapes the removed `carve` op into a shallow seed + a drainage boost (re-add it
   from git history / SPLINES-HANDOVER notes; the UV mapping u=x/size+0.5, v=0.5-y/size and impose
   take-lower math are recorded there).
3. Noise-warp the talus/repose angle spatially (Cordonnier): make thermal `talus` a per-cell value
   modulated by high-persistence Perlin (roughly 6-54 deg equivalent) instead of a single constant, so
   banks stop reading as uniform slopes. Cheap: a per-cell talus field in ops_erode.thermal.
4. Add a deposition/point-bar term (Erosion2-style) or lean on the existing Mei pipe_hydraulic deposit
   path in the band so inner-bend bars + valley-floor fill appear.
5. Derive the WATER surface from the ERODED channel floor + a fill depth, not the fixed path_z -
   WaterDepth. This is the water-fill model the SPLINES-HANDOVER C6 tradeoff note flags as required for
   a true eroded channel. Re-fit the ribbon width to the eroded cross-section (shader shore fade hides
   edges).
6. Optional bigger bet (defer, discuss with Siva): flow-accumulation-derived tributary NETWORK feeding
   the authored main spline (Gaea-style), rather than one lone curve.

## Verify (same as before)
Blender 5.2 binary ~/.steam/steam/steamapps/common/Blender/blender --background --factory-startup
--python <script>. venv fast-tests: PYTHONPATH=tools tools/.venv/bin/python (numpy+cupy+scipy present).
Prior test scripts in the session scratchpad: test_clean.py (band erode), e2b_gap.py (0% float check),
hillshade2.py (visual). Measure geometry headless; the LOOK is Siva's call in Blender (EEVEE + Cycles).

## Then: water pass W5
Depth absorption (Beer-Lambert) + depth-tinted opacity + soft shoreline so deep water hides the bed and
shallow edges stay clear. Needs a true per-vertex water depth on the ribbon. Materials: S_WaterMaster
(water_master_group), curve_water recipe. This pairs naturally with plan item 5 (eroded-floor water).
