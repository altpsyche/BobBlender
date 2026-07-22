# Handover: terrain after amplification, remaining generators

Paste-ready brief for a fresh chat. Plain house style (no em-dashes, no emojis, no flowery phrasing).
Continuation of the terrain redesign on branch `fix/audit-remediation`. Read docs/TERRAIN-CRITIQUE.md
(diagnosis), docs/TERRAIN-REDESIGN-HANDOVER.md and the terrain-engine-rewrite memory (prior work
orders), and docs/TERRAIN.md (living reference) first. This brief covers the amplification pass that
just landed and the generator work still open.

## Where things stand

The engine is `tools/bobtools/heightfields/` (pure numpy/cupy, headless, bakes 16-bit height PNGs that
Blender displaces via `blender/bbmcp/geonodes/recipes/heightmap_terrain.py`). 1 Blender unit = 1 metre.
CuPy works on the venv's CPython 3.14. All work below is in the working tree on `fix/audit-remediation`
and is NOT committed. 57 tests pass (`uv run --extra terrain --with pytest --project tools python -m
pytest tools/tests -q`).

Preset menu, 11 presets, all now carry a multi-scale amplification pass (see below): Mountains
(alpine, glacial, foothills), Lowlands (hills, plains, coastal, islands), Canyons (mesa, canyon), Dunes
(dunes, sand_sea).

## What just landed: Schott multi-scale amplification (Phase 1)

This was the "adopt next" item from the terrain-sota-research memory (Schott et al. SIGGRAPH 2024,
Terrain Amplification using Multi-scale Erosion). It is done and render-verified in 3D for all 11
presets, both process modes.

The idea: a preset's coarse macro shape runs cheap at a fixed base resolution, then an `amplify` op
grows it to the bake resolution in resolution-doubling levels, adding erosion-consistent fine detail at
each level. Because every level builds deterministically on the previous, a lower-resolution bake is a
faithful low-detail PREFIX of a higher one, so a preview and a full bake register (preview == final).
This also gives character-scale detail: a dropped 1.8 m human now reads against sub-metre rills.

Key pieces, so you can find them:

- `ops_erode.amplify(h, xp, *, mode, to, strength, iterations, seed, wind, repose, relief, cell,
  despike, diffusion)`: the op. Two modes.
  - `fluvial`: upsample (preserves the macro cliffs and ridges), seed an isotropic Perlin detail band
    on slopes, then pure stream-power incision. `diffusion` > 0 relaxes the incision into smooth swales
    (use it for soft lowland terrain that has no cliffs to keep crisp; keep it 0 for mesa/canyon so the
    cliff faces stay sharp). For mountains, canyons, mesas, hills.
  - `aeolian`: upsample, add windward transverse ripples and dunelets, settle to the sand angle of
    repose. No stream-power, because sand has no rivers and fluvial incision scars slip faces. For
    dunes and sand seas.
  Helpers alongside it: `_upsample`, `_iso_band` (isotropic Perlin so detail does not comb into grid
  stripes), `_ripple`, `_slope01`, `_talus_for_angle` (local mirror of presets.talus_for_angle).
- `engine.py`: `amplify` registered in `_OPS`.
- `params.py`: `AMPLIFY_BASE` (256, the macro resolution) and `AMPLIFY_PREVIEW` (512, one climb level,
  a real prefix of a full bake). `has_amplify(stack)` and `macro_size(stack, size)`. `resolve_stack`
  injects `to = size`, `relief`, and a seed onto the amplify op, scales its `strength` by the Detail
  knob, and resolves any macro `repose_deg` against the macro resolution (AMPLIFY_BASE when the stack
  amplifies, since that is where the macro passes actually run). `resolve_amplify_targets` sets a
  concrete `to` on the amplify op for the panel mirror.
- `pipeline.py`: `bake` generates the coarse macro at `macro_size` (AMPLIFY_BASE when amplifying) and
  the amplify op climbs to `size`; `_preview_size` returns AMPLIFY_PREVIEW for an amplify preset so a
  preview is a prefix of the full bake.
- `presets.py`: every stack ends in an amplify op. Per-family strength and diffusion: mesa/canyon use
  diffusion 0 (crisp cliffs), lowlands and foothills use a small diffusion so incision reads as
  drainage rather than sharp cracks, dunes/sand_sea use `mode: aeolian`.
- `tools/tests/test_heightfields.py`: four new amplify tests (preview-is-a-prefix, fluvial preserves
  flats and adds detail, aeolian does not channelise, wiring of macro and preview sizes). Two existing
  tests were pinned to a fixed `size` so the amplify cascade no-ops and they stay macro-only diagnostics.
- `blender/extensions/bob_blender_tools/presets.json`: the committed mirror, regenerated. Always
  regenerate after a presets.py change: `PYTHONPATH=tools python tools/scripts/gen_panel_presets.py`.

What amplification did NOT fix: it adds detail on top of the macro; it cannot invent a landform the
macro does not have. glacial still reads as rugged mountains, not glaciated valleys. That is generator
work, below.

## What is left

badlands. Needs a NEW dedicated fine-rill op, then a preset. Confirmed in an earlier session that
stream-power fluvial cannot make dense fine rills; it organises flow into a few smooth graded valleys.
Badlands is dense, closely-spaced, sharp parallel gullies on steep soft slopes at low total relief.
Build an anisotropic downslope-incision op (carve many fine grooves following steepest descent) plus
sharpening for knife-edge divides. Note: amplify's fluvial mode is a general detail pass, not this; do
not try to make badlands out of amplify alone. Amplify could sit on top of a real badlands macro once
it exists.

glacial. Currently ships and, with amplify, reads as a detailed rugged mountain, but it is not
glacial. Needs a glacial-flow pass for U-shaped valleys, cirques, and aretes. Hardest one. Its own
literature dive (see the open-questions note in terrain-sota-research memory).

plateau. Likely cheap: strata with low dissection (continuous tableland) plus light scarp, no deep
fluvial, then the existing fluvial amplify on top. Reuse the mesa/canyon ops. Not started. Confirm the
name with Siva before adding it, since preset names are user-facing.

Schott Phase 2 (optional). The current amplify is the single-pass version of the idea. The paper's
fuller multi-scale coupling (running the erosion solve jointly across scales, not just level by level)
could be adopted later if the single pass proves limiting. Not needed now.

Polish (all minor, render-judge before touching):

- alpine at relief ratio 0.30 is a touch spiky; canyon central basin reads slightly round; a couple of
  mesa buttes read slightly conical. These predate amplification.
- aeolian ripples on dunes/sand_sea are slightly diagonal and there is faint grain on the lee faces.
  Dial `_ripple` direction and the windward mask if you want them cleaner.
- foothills and glacial still show a few sharper incision notches than the lowlands. Acceptable as
  rugged drainage; add a touch more `diffusion` to their amplify op if you want them softer.

## Render-in-the-loop workflow (mandatory, reuse it)

Never assert a landform or an angle from stats. Every claim needs a 3D render you look at plus a
diagnostic number. Do not judge terrain by scalar statistics alone; that failure is documented in
docs/TERRAIN-CRITIQUE.md.

Bake in the uv env (has numpy/scipy/pillow):

```
uv run --extra terrain --project tools python <script>
# from bobtools import heightfields as hf
# hf.bake(png, {"preset": "mesa", "size": 768}, force=True)     # full
# hf.bake(png, {"preset": "mesa"}, force=True, preview=True)    # preview == a prefix of the full bake
```

Render in Blender (bundled python has no PIL/scipy, only build and render there):

```
~/.steam/steam/steamapps/common/Blender/blender --background --factory-startup --python <script>
# put blender/ and blender/extensions/ on sys.path
# from bbmcp.dispatch import apply_op
# apply_op({"op":"reload_image","path":PNG})
# apply_op({"op":"build_geonodes","recipe":"heightmap_terrain","name":"Terrain",
#           "params":{"heightmap":PNG,"size":90.0,"resolution":512,"height":H,"sea_level":S},"reset":True})
# H = presets.height_for(name, 90.0); S = presets.display(name)["sea_level"]
# drop a 1.8 m cylinder (human) and a 6 m cube (house) for scale, camera + sun, EEVEE, render, LOOK.
```

Gotchas: the EEVEE engine enum in this 5.2 build is `BLENDER_EEVEE`, not `BLENDER_EEVEE_NEXT`. Pass
`reset=True` to build_geonodes so Height and Sea Level are panel-authoritative (see the
blender52-gn-modifier-inputs memory). dunes/sand_sea real relief is sub-metre, so exaggerate the height
only for inspection when you want to see the surface. Working prototype and render scripts from the
amplify session are in the session scratchpad if you want a starting template.

Tests: `uv run --extra terrain --with pytest --project tools python -m pytest tools/tests -q` (57
green). After any presets.py change, regenerate the mirror (a drift test guards it). Each new landform
gets a diagnostic test (see `test_mesa_reads_as_tableland`, `test_canyon_incises_a_plateau`, and the
amplify tests).

## Rules

- Verify with a render you look at, plus a landform diagnostic. Never assert a landform or angle from
  stats alone.
- Real-world scale: 1 unit = 1 m. Relief is a scale-invariant ratio of tile size (presets.DISPLAY),
  clamped sane by presets.height_for. A dropped 1.8 m character and 6 m house must read correctly.
- If two tuning attempts do not change the render, stop tuning and build the right op instead (badlands
  and the amplify smoothing both proved this).
- Plain house style in code and docs: no em-dashes, no emojis, no flowery phrasing.
- Propose heavy work and lead with a render before building. Confirm before removing or renaming
  user-facing preset names.
- Do not commit unless Siva asks.

## Doc and memory updates still to do

These were deferred from the amplify session; do them when the amplify work is accepted, or as part of
the next commit:

- docs/TERRAIN.md (living reference): add an amplification section describing the coarse-to-fine
  cascade, the two modes, the base/preview resolution scheme, and the per-preset strength/diffusion
  choices. It currently predates amplify.
- terrain-engine-rewrite memory: record that Schott Phase 1 (multi-scale amplification) landed for all
  11 presets, with the file map above, so the next session does not re-plan it.
- Commit: the amplify work is one coherent change (op, engine and params wiring, all 11 presets, mirror,
  tests). A plain-style commit message covering it is appropriate once Siva signs off.
