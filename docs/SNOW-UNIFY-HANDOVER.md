# Handover: unify the snow model behind a snow-line

Paste-ready brief for a fresh chat. Plain house style (no em-dashes, no emojis, no flowery phrasing).
Branch is `fix/audit-remediation`. Do not commit unless Siva asks.

Task, in order: REVIEW (reproduce the diagnosis headless, do not take it on faith), then PLAN (confirm or
adjust the design below), then IMPLEMENT. Propose before the heavy implement step; this touches the weather
model that reaches every surface, not just UI.

## Context

BobBlenderTools is a Blender 5.2 extension (`blender/extensions/bob_blender_tools/`) with a numpy/cupy
compute side (`tools/bobtools/`) and an in-Blender geometry/material side (`blender/bbmcp/`). Snow reaches a
surface two ways today and they can disagree. This track collapses them into one model and adds a snow-line
so the "peaks vs whole map" behaviour is intentional rather than an artifact.

Recent related work already on this branch (do not redo, do not collide):
- Item-3 weathering fix: scattered assets auto-convert to BobShaders on scatter build, and a Canopy Snow term
  lets snow/frost/dust hold on near-vertical assets (trees). Lives in `weather_group` (computed branch) and
  `bobshade_material`. See the weather-interface rule below.
- The World panel was reordered to Season, Conditions, Sky Look. Conditions holds the live `env.snow`.

## Root cause (verify before trusting)

Snow is implemented TWICE:
- Terrain: a GN pass, `blender/bbmcp/geonodes/recipes/snow.py`, writes a `snow_cover` float attribute
  (formula `Snow * slope_mask * altitude_mask * (1 - occlusion)`). The terrain material reads it because its
  weather layer is set to `Use Attribute = 1` (`materials.py`, `terrain_master_group`, the line commented
  "terrain carries the pass").
- Everything else (scattered assets, props, plain surfaces): the SAME formula recomputed in the shader,
  `materials.py` `weather_group`, the "computed" branch, driven live by `env.snow`.

The terrain path has NO fallback: `coverage = computed*(1 - Use Attribute) + snow_cover*Use Attribute`, and
terrain pins `Use Attribute = 1`. If the `snow_cover` attribute is absent, the shader Attribute node returns
0, so terrain reads zero snow while assets (computed, env-driven) still whiten. That is the reported edge
case: everything except the terrain covered in snow.

The pass's only unique power over the shader is occlusion (a raycast shelter term). Occlusion is an artist
knob (`_SNOW_KNOBS` in `firmament_panel.py`), defaults 0, and nothing sets it automatically. So in the common
case the pass computes exactly what the shader already computes: pure duplication that terrain nonetheless
hard-depends on.

Live feed: `env.snow` drives the terrain pass's Snow input through a driver, `firmament_panel.py`
`_install_snow_driver` / `_snow_input`, but only when the `BOB_Snow` modifier exists and Live Environment is
on. The pass is built by `firmament_build_snow_cover`, which Apply Season (winter) calls only if a snow
surface resolves (`fm.snow_surface` or the active object is a mesh). Apply Season with no valid surface sets
`env.snow` and builds falling snow but no coverage pass, which reproduces the edge case.

Reproduce headless (a Blender 5.2 binary is in the CLI env at
`~/.steam/steam/steamapps/common/Blender/blender`): register the addon, build a terrain material (Use
Attribute 1) with no `BOB_Snow` modifier, convert a scattered asset, set `env.snow = 1`, install the env
drivers, then measure. Terrain reads `snow_cover` = 0 (bare) while the asset's computed coverage is > 0.
Confirm the pass is redundant at occlusion 0 by comparing the pass output to the computed formula.

## Agreed design: one model, one snow-line

Delete the second implementation. One snow formula, in the shader, for every surface. The GN pass keeps only
occlusion and becomes optional detail, not a dependency.

User-facing behaviour to preserve (Siva likes the difference, wants it intentional):
- Conditions > Snow sets HOW HEAVY the snow is (intensity). It lies from the snow-line upward. A companion
  Snow Line control sits next to it; lower it and snow creeps down the slopes. Default snow-line is high, so
  Conditions alone frosts the peaks.
- Season > Apply (Winter) is the preset: turns Snow up AND drops the Snow Line to the valley floor (whole map)
  and starts falling snow. Spring/Summer/Autumn push the Snow Line above the peaks so snow clears.
- Mental model: Conditions sets how much, the Snow Line sets how far down, Season is the one-click winter that
  moves both. Terrain, rocks and trees all obey the same line, so "trees white, ground bare" cannot happen.

## Plan (phases)

1. Snow-line into the env bridge.
   - Add `snow_line` (world-Z metres) to `bbt_env` in `blender/bbmcp/env.py` (`BBT_EnvProps`, next to `snow`
     at line ~109). Default high, above a typical terrain, so bare default plus Conditions reads as peaks.
   - Add it to `ENV_STATE_DRIVERS` and add a `Snow Line` output on `env_state_group` in `materials.py`
     (~line 585 and ~705). `_install_env_drivers` in `shaders_panel.py` already loops `ENV_STATE_DRIVERS`, so
     the live driver comes for free.

2. One snow formula in the shader.
   - `weather_group` (`materials.py`, ~line 750): the altitude mask reads the env `Snow Line` (keep the
     per-material `Altitude` input as an optional bias, default 0, added to the line). Coverage becomes
     `env.snow * slope/canopy * altitude(SnowLine) * (1 - occlusion) * Snow Strength`, identical on terrain
     and assets. Keep the item-3 canopy term intact.
   - Drop the `Use Attribute` hard switch. Read occlusion from a `snow_occlusion` attribute; absent returns 0,
     so a missing pass means full snow, never the zero-coverage trap.

3. Pass demoted to occlusion only.
   - `snow.py`: write a `snow_occlusion` attribute (the shelter term, 0..1) instead of full `snow_cover`. At
     occlusion 0 it writes 0, so the pass is now purely additive and optional.
   - Terrain material (`terrain_master_group`, the `Use Attribute = 1` line ~1975) stops forcing the pass.

4. Season drives the line.
   - `SEASON_APPLY` in `firmament_panel.py` (~line 163): winter sets `snow=0.7` and `snow_line=<valley>`; other
     seasons set `snow_line=<above peaks>`. Falling snow stays. `build_snow_cover` stays available as optional
     occlusion detail, not required for terrain to snow.

5. Conditions UI.
   - Add a `Snow Line` prop under Conditions in `world_panel.py` next to `env.snow` (the Conditions box,
     "Conditions (live)" label ~line 401, `env.snow` at ~line 409), so the live control is where the artist
     expects it.

Version bump (mandatory): steps 2 and 3 change the `S_Weather` interface. Per the weather-interface rule,
bump the global `S_GROUP_VER` (currently 4) AND the `S_WaterMaster` entry in `_GROUP_VER_OVERRIDE` (currently
7) together, or the terrain and water master groups keep stale links to the rebuilt weather node (verified:
scoping the bump dropped terrain's 16 weather links to 0). Cost: tuned terrain and surface material inputs
reset on upgrade, which is expected for a weather-interface change.

## Verification (headless, measured, not eyeballed)

1. env.snow high, no pass, terrain material: terrain coverage > 0. The edge case is gone.
2. Sweep `snow_line` down: measure the world-Z where coverage crosses 0.5 and confirm it tracks the line, on
   BOTH a terrain mesh and a scattered asset (consistency between the two).
3. Winter Apply: snow-line at the valley, the whole grid covers. Summer: snow-line above the peaks, zero
   coverage.
4. Occlusion pass built with occlusion > 0: sheltered faces measurably lower than exposed ones.
5. Reload test: build terrain, water and surface masters, stamp every S_ group stale, rebuild, confirm the
   weather-node links survive (terrain 16, water 13) so the version bump is safe.
6. A/B render: peaks-only (high line) versus whole-map (low line), look at the render.

## Rules (hard)

- Verify headless, do not eyeball code to judge behaviour. Panels and materials: run Blender headless
  (`--background --factory-startup --python <script>`), put `blender/` and `blender/extensions/` on sys.path,
  `import bob_blender_tools; register()`, drive the operators, and inspect the datablocks (node trees,
  attributes, drivers, modifier inputs). Terrain and snow looks: build and RENDER, look at the image.
- Plain house style in any doc or code you write.
- Branch is `fix/audit-remediation`. Do not commit unless Siva asks.
- Propose the design (phase 2/3 especially) and let Siva confirm before implementing the heavy parts.

## Open questions for Siva

- The default snow-line is a world-Z value in metres, which is scale-dependent across terrains. Start with a
  high metre default and let Season and the knob set it. If the metre value feels unintuitive, consider a
  normalized-to-terrain-height option later (needs the terrain bounds in the shader, which is not currently
  available generically).
