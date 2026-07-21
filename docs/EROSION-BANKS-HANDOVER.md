# Handover: erosion + natural riverbanks (continue next session)

Status 2026-07-22, branch fix/audit-remediation. Plain house style (no em-dashes/emojis). Picks up
after the water-shader work and the "erosion after curves" (C6) feature. Read WATER-SHADER-HANDOVER.md
and SPLINES-HANDOVER.md (C6 section) first for the grounded state.

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
