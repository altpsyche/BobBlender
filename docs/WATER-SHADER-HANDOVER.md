# Handover: BobSplines water shader (S_WaterMaster) improvement

Status: 2026-07-22, branch fix/audit-remediation. For a fresh chat improving the river/stream water
BobShader. Plain house style (no em-dashes, no emojis). The durable spec for the whole curve system
is docs/SPLINES.md; the C5 water work and the UX unification are in docs/SPLINES-HANDOVER.md. This
doc is scoped to the WATER MATERIAL look.

## Goal
Siva's verdict on the current water: "very basic, no flow, no interaction, no waves, no foam, doesn't
freeze." Turn it into a convincing river/stream surface: readable flow + waves, foam that reads as
foam, transparency/refraction that works in EEVEE, and a real freeze-to-ice, without breaking the
live param model or the geometry (which are correct).

## UPDATE 2026-07-22 (round 2): flow rework + real Gerstner waves

Siva's round-1 verdict: "the flow pattern is fully unnatural [combing hair streaks]; no waves yet; we
need some gerstner waves." Fixed:
- The combing was the shader's single LOW-frequency flow-advected bump at high strength -- under a
  grazing view it smears into hair streaks. Replaced with only a SUBTLE high-frequency detail normal
  (two octaves, low default strength 0.10); the visible waves are now geometry.
- Real Gerstner waves added as animated VERTEX displacement in curve_water (not a normal): a sum of
  three trochoidal components travelling downstream (heading from bbt_flow), animated by a Scene Time
  node (no bake, moves on playback), amplitude flattened toward the banks, smooth-shaded. Exposed as
  Wave Height / Wave Length / Wave Steepness / Wave Chop on bbt_curve, synced to the ribbon and drawn
  in the Paths panel Water section (river/stream); roles seed them (river amp 0.10, stream 0.06).
- KEY GOTCHA that ate an investigation: a regular chevron/corduroy pattern in the renders was NOT the
  water -- it is the carved TERRAIN bed (the heightmap grid faceting) seen through the clear
  transmissive water, present even with the water surface flat/opaque. Verified by hiding the terrain:
  the water surface alone is smooth natural ripples. If Siva sees chevrons through the water it is a
  terrain-resolution matter, not the shader. Two sub-findings from that dig: pure sine Gerstner locks
  into a mechanical interference lattice -> the Wave Chop domain-warp (a large-scale noise bending the
  phase) breaks it into natural chop; and wide cross-wave angles facet on the ~1 m width spacing ->
  components are kept near-downstream (small angles) so the across-width period stays large.
- Versioning: a global S_GROUP_VER bump RESETS the tuned inputs of every cached S_ group (an interface
  rebuild gives sockets new identifiers -> node values drop to 0, verified), so it would wipe
  terrain/surface tuning too. Added a per-group override (_GROUP_VER_OVERRIDE) so the water group
  alone rebuilds (S_WaterMaster v4) and terrain/surface keep their tuned values. Bump the water entry,
  not S_GROUP_VER, for future water-only shader changes.
Verified headless: waves displace + animate (87% of verts move frame 1->40), amp 0 = perfectly flat,
attributes survive, material builds fresh at water ver 4 with the EEVEE flags. A terrain-hidden Cycles
render confirms the surface reads as natural flowing ripples reflecting the sky.

## STATUS 2026-07-22: W1-W4 + W6 landed (static-gated + headless-verified); look awaits Siva

The look pass shipped on branch fix/audit-remediation. `S_GROUP_VER` bumped 2 -> 3 (materials.py),
so a cached v2 water group rebuilds and exposes the new sockets. Verified headless with the Blender
5.2 binary (scripts in the session scratchpad): the full S_WaterMaster graph builds with no socket
errors, the ribbon attributes are still varied (bbt_flow 0.36..2.2, bbt_foam 0.02..0.5, bbt_shore
0..1), all seven Principled inputs are LINKED, the EEVEE flags are set, the water_time driver is
installed, and a Cycles CPU render confirms freeze changes the surface (6.9% of a ribbon-framed
shot, max px delta 0.36) and flow animates across frames (frames 1->48 move 3.7% of pixels). What
landed, by phase:
- W1 EEVEE refraction (materials.set_water_render_flags, called from water_material):
  use_raytrace_refraction + use_screen_refraction True, show_transparent_back False,
  surface_render_method DITHERED (BLENDED alpha cannot refract). materials.enable_eevee_refraction
  flips scene eevee.use_raytracing on (called from splines_panel._build_water and the Shaders New
  water path); no-op in Cycles. 5.2 flag names probed live, not guessed.
- W2 waves: three flow-advected noise octaves (slow swell / main ripple / fine chop), each sampled
  at two phases half a cycle apart and cross-faded by a triangle wave (the flow-map trick, so the
  advection never pops even where bbt_flow diverges), chained into one Bump normal; all fade out as
  it freezes. New "Wave Detail" knob scales the chop. Ripple Strength default 0.15 -> 0.30.
- W3 foam: max(bbt_foam*Foam Amount, shore-shallow foam from bbt_shore), broken up by a flow-scrolled
  noise, thresholded to crisp lines by "Foam Crispness"; foam also roughens. New "Shore Foam" +
  "Foam Crispness" knobs; Foam Amount default 1.0 -> 1.2.
- W4 freeze: new manual "Frozen" (0..1) input; frozen = max(Frozen, env-cold). Ice = flow normal
  collapses, cracked-ice Voronoi bump fades in, transmission -> opaque, alpha -> opaque, roughness ->
  frosted, and the manual path tints icy blue-white (env-cold path is tinted by the S_Weather frost
  term, so no double). Fixes the "doesn't freeze" complaint standalone (no Firmament needed).
- W6 defaults + panel: fresh-river defaults retuned as above; Shaders Water sub-panel gained Wave
  Detail / Shore Foam / Foam Crispness under Flow+foam and a Frozen slider under a new Freeze group.
  NOTE existing tuned materials keep their old stored values on the in-place group rebuild; only a
  freshly built water material picks up the new defaults (delete S_WaterMaster to force it).

REMAINING: W5 (true depth interaction / caustics) is still the lateral bbt_shore gradient only, not a
scene-depth read. The Flow/Foam knob-overlap consolidation (W6 second half) was left as-is. Both are
optional next steps; the four headline complaints (flow, waves, foam, freeze) + EEVEE refraction are
addressed. DEPLOY for Siva: bbmcp change -> Advanced > Reload Builders; panel change -> full addon
reload (restart / F3 Reload Scripts); then rebuild the river's water (or delete the S_WaterMaster
node group) so the v3 group + new defaults take. Judge the LOOK in EEVEE (refraction) AND Cycles.

## Current state (GROUNDED, measured headless 2026-07-22 -- read before changing anything)

The pipeline and wiring are CORRECT. This is under-built + a render-setting gap, not a broken data
path. Verified by building a river headless (the Blender binary at
`~/.steam/steam/steamapps/common/Blender/blender`, see the memory note blender-headless-testing) and
inspecting the ribbon + material:

- The ribbon carries the driving attributes, non-zero and varied: `bbt_flow` (a FLOAT_VECTOR, |v|
  0.36..2.2, the unit downhill tangent scaled by speed), `bbt_foam` (0.02..0.50), `bbt_shore` (0..1,
  0 mid-channel to 1 at the banks). Written by `blender/bbmcp/geonodes/recipes/curve_water.py`.
- The shader is fully wired: `materials.water_master_group` (blender/bbmcp/materials.py:1361) reads
  those three attributes, and the widened `_build_wrapper` (materials.py, `_WRAPPER_EXTRA_OUTPUTS`)
  drives Base Color / Roughness / Metallic / Normal / IOR / Alpha / Transmission Weight into the
  Principled BSDF -- all confirmed LINKED. The `water_time` Value node has a `frame` driver installed
  (ripples do animate on playback).

So the features EXIST but under-deliver:
1. **EEVEE refraction is OFF.** `mat.use_raytrace_refraction` is False on the built material, so the
   0.92 Transmission never refracts in EEVEE Next -> the water reads flat/opaque/grey. THE biggest
   "looks basic" cause. (Cycles refracts fine; the artist is almost certainly in EEVEE.)
2. **Waves too subtle.** The normal is a SINGLE low-frequency 4D-noise Bump (Ripple Strength 0.15,
   Ripple Scale 1.8, materials.py:1446-1460). One weak octave reads as a faint shimmer, not waves.
3. **Foam too faint.** `bbt_foam` maxes at ~0.5 and is mixed as a soft white wash (materials.py:
   1473-1480). No contrast/threshold, no animation, no crisp foam lines, no shore-intersection foam.
4. **Freeze needs a temperature.** The freeze rides on `env_state_group` Temperature < 0 via
   `S_Weather`'s frost term + a `cold` factor that kills transmission/ripples (materials.py:1403-1409,
   1498). With no Firmament / env driver the temp defaults to 15 C, so it never freezes; and even
   below 0 the ice look is only a frost whiten, no ice normal/cracks/snow.
5. **No depth interaction.** Colour/opacity vary only by `bbt_shore` (lateral position), not by real
   water depth or the scene behind; no caustics, no depth-tinted refraction.

### Where the knobs live (important -- do not duplicate)
Two layers, do not confuse them:
- GEOMETRY attributes come from `curve_water` inputs (Width, Water Depth, Flow Base, Foam Bank, Foam
  Rapids, End Taper), which the Paths panel drives LIVE from `bbt_curve` via
  `splines_panel._sync_curve_params`. These set the MAGNITUDE of bbt_flow/foam/shore.
- SHADER knobs live on the water material's Master node (Shallow/Deep Color, Depth, Water Roughness,
  IOR, Transmission, Flow Speed, Ripple Strength/Scale, Foam Color/Amount, Edge Fade + the weather
  passthrough), edited in the Shaders panel Water sub-panel (`BBT_PT_shaders_water`, shaders_panel.py).
There is some overlap (`Flow`/`Flow Speed`, `Foam Bank`/`Foam Amount`). Part of this work is deciding
the clean split and not shipping two knobs that fight.

## Improvement plan (phased; each phase is a visible win)

- **W1 EEVEE transparency + refraction (the biggest look win).** In `water_material` /
  `_build_wrapper` set the material for real refraction: `mat.use_raytrace_refraction = True`, sort
  out alpha (Principled Alpha + the 5.2 EEVEE-Next transparency path), `use_transparent_shadow`, and
  `show_transparent_back = False`. NOTE 5.2 EEVEE-Next renamed several material flags -- run a 3-line
  probe in Blender's console to confirm the exact attribute names before coding (do not guess), and
  note that the scene's EEVEE Raytracing must be enabled for refraction to show. Verify in BOTH
  EEVEE and Cycles.
- **W2 Flow + multi-scale waves.** Replace the single bump with a proper flow-normal: TWO
  phase-blended samples advected along `bbt_flow` (blend by a triangle wave of frac(time) so there is
  no scroll-reset -- the Waterways technique), plus a second finer + faster octave and a large slow
  swell, combined into one normal. Raise the default Ripple Strength. Optional stretch: real mesh
  displacement (a Gerstner/​noise wave) in `curve_water` behind a "Waves" toggle for hero shots.
- **W3 Foam that reads as foam.** Contrast/threshold `bbt_foam` into crisp lines, break it up with a
  noise scrolled along `bbt_flow`, and add SHORE foam where the water is shallow (use `bbt_shore`
  near 1 as the shallow proxy, boosted). Raise defaults. Foam should also lift roughness (it already
  does) and read white in shadow.
- **W4 Freeze / ice, real + controllable.** Add a manual `Frozen` (0..1) input to the water master so
  the artist can freeze without setting up Firmament; drive it as `max(Frozen, cold_from_env)`. Ice
  look: keep the whiten/roughen + transmission/ripple kill, add an ice normal (cracked/frosted noise)
  and optional snow-cover on top, blended by the frozen factor.
- **W5 Depth interaction.** Depth-tinted colour + opacity (deeper = darker + more opaque) and a
  depth-fade at the shoreline. Cheapest real depth: store a per-vertex depth on the ribbon in
  `curve_water` (Path Depth minus the local fill) or read a shallow proxy from `bbt_shore`; a true
  scene-depth read is EEVEE-Next specific -- probe before committing. Optional: fake caustics.
- **W6 Defaults + knob consolidation.** Tune the out-of-box Ripple/Foam/Flow/colour so a fresh river
  reads as flowing water, and resolve the Flow/Flow Speed and Foam Bank/Foam Amount overlap (one
  clear owner each; the geometry attr sets presence, the shader knob sets look).

Bump `S_GROUP_VER` (materials.py:664) whenever the S_WaterMaster interface changes so existing cached
groups rebuild.

## Key files
- `blender/bbmcp/materials.py`: `water_master_group` (:1361, the shader), `water_material` (:1513),
  `_build_wrapper` + `_WRAPPER_EXTRA_OUTPUTS` (the Principled wiring), `weather_group`/`env_state_group`
  (freeze source), `S_GROUP_VER` (:664), `WATER_MASTER`/`_WATER_*` colour constants (:1355).
- `blender/bbmcp/geonodes/recipes/curve_water.py`: the ribbon + `bbt_flow`/`bbt_foam`/`bbt_shore`
  computation (where W3 shore-foam / W5 depth attribute would be added).
- `blender/extensions/bob_blender_tools/shaders_panel.py`: `BBT_PT_shaders_water` sub-panel +
  `_WATER_LOOK`/`_WATER_FLOW` knob lists + `_feed_env` (env drivers). New shader knobs get drawn here.
- `blender/extensions/bob_blender_tools/splines_panel.py`: `_sync_curve_params` / `_derived_water`
  (the geometry-side flow/foam magnitudes from `bbt_curve`), the Paths Water panel section.

## Verification
- Static: `python -m py_compile` on touched files; grep new socket/attr names for dangling refs;
  bump + confirm `S_GROUP_VER` so the cached group rebuilds.
- Headless (measure, do not eyeball code): the Blender binary above, `--background --factory-startup
  --python <script>.py`, importing bbmcp directly (no addon register). Build terrain + river + water
  ribbon, shade with `materials.water_material`, then assert the ribbon attributes are non-zero, the
  Principled inputs are LINKED, the frozen/refraction flags are set, and (for freeze) drive the env
  Temperature below 0 and confirm the outputs change. A ready pattern is in the session scratchpad
  (`diag_water_mat.py`).
- Blender (Siva): the shader LOOK can only be judged rendered. Test in EEVEE (realtime, refraction on)
  AND Cycles; on flat and sloped rivers; with weather set to winter/temp < 0 for the freeze.
- DEPLOY GOTCHA: material/recipe code is bbmcp -> Advanced > Reload Builders; but the Shaders/Paths
  PANEL code is the ADDON -> needs a full addon reload (restart Blender, or F3 Reload Scripts). A
  water look change also needs a freshly built water material (the S_GROUP_VER bump forces it; else
  delete the S_WaterMaster node group).

## Constraints (from the session)
- Blender 5.2 EEVEE-Next: several material/render flags were renamed vs 4.x; PROBE the exact names in
  the console rather than guessing (this bit the curve nodes twice: GeometryNodeCurveLine ->
  GeometryNodeCurvePrimitiveLine, ResampleCurve/CurveToPoints `.mode` became menu sockets).
- Keep the live param model intact: the water shape/flow/foam magnitudes are synced from `bbt_curve`;
  do not move them back onto the modifier. Shader-look knobs stay on the material's Master node.
