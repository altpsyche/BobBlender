# Audit: weather and material interaction

Paste-ready brief for a fresh chat. Plain house style (no em-dashes, no emojis, no flowery phrasing).
Branch is `fix/audit-remediation`. Do not commit unless Siva asks.

Task, in order: REVIEW (map the system and reproduce behaviour headless, do not take this brief on
faith), then report findings ranked by severity, then propose fixes and let Siva confirm before any
large change. This system reaches every shaded surface, so a change is wide-blast.

## Scope

Audit how the shared weather layer interacts with every material, for correctness, cross-surface
consistency, physical plausibility, and interface integrity. The snow and frost model was just
reworked (see "Recent changes" below) and is UNCOMMITTED on this branch. Treat the current code as
the thing to audit, not the git history.

The weather terms to cover: snow, hoar frost, wetness (rain/storm/pooling/terrain-map), and the
season aging terms (dust, moss). The surfaces to cover: terrain (`S_TerrainMaster`), surfaces and
scattered assets (`S_SurfaceMaster`), and water (`S_WaterMaster`). All three embed the same
`S_Weather` group, so the audit is really "does the one weather layer behave correctly on all three,
under the full range of world states."

## Architecture (verify against the code, do not trust this summary)

BobBlenderTools is a Blender 5.2 extension (`blender/extensions/bob_blender_tools/`) with a
numpy/cupy compute side (`tools/bobtools/`) and an in-Blender geometry and material side
(`blender/bbmcp/`). The world state is `Scene.bbt_env` (`blender/bbmcp/env.py`, `BBT_EnvProps`),
owned by BobFirmament, read by everything, with a fallback to defaults when Firmament is absent.

Material side, all in `blender/bbmcp/materials.py`:
- `env_state_group` (`S_EnvState`): the world-to-shader bridge. Internal Value nodes are driven once
  from `bbt_env` by the panel (`ENV_STATE_DRIVERS` + `shaders_panel._install_env_drivers`). Outputs
  Snow, Wetness, Temperature, Snow Line, Snow Line Top, Cloud, Wind. It computes derived quantities:
  snow amount from temperature, the world-Z snow line from the normalized `snow_line` and the terrain
  Z bounds, and the effective wetness from the weather enum.
- `weather_group` (`S_Weather`): the shared weather layer, ending every master. Applies, in order,
  dust and moss, then wetness, then snow, then frost, onto (base colour, roughness, metallic).
- `surface_master_group`, `terrain_master_group`, `water_master_group`: each embeds `S_Weather` and
  passes the shared weather inputs through (`_WEATHER_EXTRA` plus the snow and slope inputs).
- `bobshade_material`, `surface_material`, `terrain_material`, `water_material`: the wrappers that
  instance a master and feed maps.
- Version stamping: `S_GROUP_VER` and `_GROUP_VER_OVERRIDE` gate in-place rebuilds when a shared
  group interface changes. Currently `S_GROUP_VER = 5`, `S_WaterMaster` override `8`. The weather
  interface rule: changing `S_Weather`'s interface means bumping the global version AND the water
  override together, or embedders keep stale links (verified before: terrain's weather links drop to
  0 when the bump is scoped wrong).

Geometry side:
- `blender/bbmcp/geonodes/recipes/snow.py`: a GN pass on the terrain that writes two POINT
  attributes. `snow_cover` (full coverage) feeds only the accumulation shell; `snow_occlusion` (the
  shelter term) is read by the material as an optional darkening. The material computes its own
  coverage and does not read `snow_cover`.
- `blender/bbmcp/geonodes/recipes/snow_shell.py`: displaces the surface by `snow_cover` for real
  snow thickness. It is the only reader of `snow_cover`.

Panels:
- `world_panel.py`: the Conditions box (live `bbt_env`: weather, temperature, wetness, snow_line,
  cloud, wind).
- `firmament_panel.py`: `SEASON_APPLY` (Apply Season sets temperature, wetness, snow_line, builds
  falling snow and the coverage pass in winter), the Sky Look presets (`SCENE_PRESETS`, set weather,
  cloud, wind), and the snow pass build/sync helpers (`_snow_amount`, `_world_snow_line`,
  `_sync_snow_pass`, `stamp` via `env.stamp_snow_bounds`).
- `shaders_panel.py`: the per-material weather knob lists (`_WEATHER_SNOW`, `_WEATHER_WET`,
  `_WEATHER_FROST`, `_WEATHER_SEASON`) and `_install_env_drivers`.

## Recent changes (uncommitted on this branch, this is the current model)

The snow and frost model was reworked over several passes. The new model:
- Snow has NO amount slider. `env.snow` was removed. Snow amount is temperature: 0 above freezing,
  ramping to full by `SNOW_TEMP_FULL` (-4 C), computed in `env_state_group` as the Snow output.
- The snow line is `env.snow_line`, NORMALIZED 0..1 (0 = whole terrain snowed, 1 = above the peaks,
  cleared). `env_state_group` maps it to world-Z lo/hi (Snow Line, Snow Line Top) using the terrain
  Z bounds `env.snow_z_base` / `env.snow_z_span`, stamped from the terrain's evaluated bounding box
  by `env.stamp_snow_bounds` (called on bake terrain, on Apply Season, on build snow, on picking the
  Snow Surface, and on every snow_line drag via `_on_snow_line_change`). Line 0 is mapped so the
  whole terrain, valley floor included, is covered.
- Coverage in `weather_group`: `Snow * max(slope_mask, canopy) * altitude_mask(lo, hi) *
  (1 - snow_occlusion)`, then times Snow Strength. The old Use Attribute switch is gone; the material
  no longer reads `snow_cover`.
- Frost is physically modelled hoar frost: gated by frost point (temperature just below 0), clear sky
  (1 - Cloud), calm air (1 - Wind), sky exposure (`upface_eff`, which is `max(upface, canopy)` so it
  reaches scattered-asset canopies), and bare (1 - snow coverage, so it never doubles on snow). The
  look is a thin low-opacity cool sheen plus a fine crystalline sparkle (a noise texture), not an
  opaque white. `env_state_group` gained Cloud and Wind outputs to drive the clear and calm gates.
- Per-material Altitude / Altitude Falloff inputs were removed from `S_Weather` and the masters (the
  line is env-owned now).

Files touched (all uncommitted): `blender/bbmcp/env.py`, `blender/bbmcp/materials.py`,
`blender/bbmcp/assets.py`, `blender/bbmcp/geonodes/recipes/snow.py`,
`blender/extensions/bob_blender_tools/__init__.py`, `firmament_panel.py`, `world_panel.py`,
`shaders_panel.py`.

## What to audit (interaction focus, ranked prompts)

1. Cross-surface consistency. The same `S_Weather` runs on terrain, surface/asset, and water. Confirm
   each term reads correctly on each. In particular water: Snow Strength defaults 0 for water, but
   frost is still passed through. Water has its own freeze path (`_WATER_ICE`, Frozen, env-cold). Does
   the weather frost term now double with the water freeze look, or fight it, below 0 C? The old code
   comments claim the env-cold water sheen comes from the weather frost term. Verify that still holds
   and does not double-tint.

2. Term compositing and order. `weather_group` layers dust, moss, wetness, snow, frost. Check the
   combinations: wet plus snow, dust plus snow, moss on undersides plus frost, wetness plus frost
   (wet then freeze). Are the results physically sane, or does a later term wrongly override an
   earlier one? Snow drops metallic and lifts roughness; wetness darkens and lowers roughness; do
   they compose sensibly when both fire?

3. Frost gating end to end. Frost depends on Cloud and Wind, which the Sky Look presets drive. A
   stormy or overcast Look raises cloud and wind, which now suppresses frost even when it is freezing.
   Confirm that is intended and that Season (cold) and Sky Look (cloud/wind) compose without a
   surprising cancellation. Check the frost point band, the sparkle scale at different terrain scales
   (aliasing), and that frost is off above freezing, off under snow, off overcast, off windy.

4. Snow line bounds correctness. The normalized line maps through `snow_z_base` / `snow_z_span`,
   which are GLOBAL env values stamped from one terrain. Audit: a scene with more than one terrain
   (last stamp wins), a scattered asset that sits on a terrain (does it read the terrain's bounds
   correctly, and what about an asset far from the terrain), and the `_on_snow_line_change` callback
   scanning for the first `bbt_terrain_height` mesh (is that the right terrain, and is a per-drag
   evaluated bounding box acceptable cost).

5. Live driver propagation. Every consumed env field must be in `ENV_STATE_DRIVERS` and reach the
   shader through `_install_env_drivers`. Confirm Snow Line, Cloud, Wind, Temperature, Wetness, and
   the weather enum all drive live, and that the standalone case (no Firmament, no drivers) still
   renders sane defaults on every master.

6. GN pass versus shader. The shell reads `snow_cover` (pass, altitude in object-LOCAL Z); the
   material reads its own coverage (world Z) plus `snow_occlusion`. Audit the alignment between shell
   thickness and material whiteness when the terrain is translated in Z, and confirm `snow_occlusion`
   semantics (absent returns 0, so no pass means full snow, never a bare terrain).

7. Version and interface integrity. The weather and env-state interfaces changed. Confirm every
   embedder rebuilds and re-links (terrain, surface, water) after a stale-stamp rebuild, and that
   `_GROUP_VER_OVERRIDE` for water is consistent. Check that tuned inputs resetting on upgrade is the
   only expected cost.

8. Season and Sky Look interaction. `SEASON_APPLY` sets temperature, wetness, snow_line; Sky Look
   sets weather, cloud, wind. Confirm they own disjoint state and do not fight, now that frost reads
   cloud and wind. Confirm Apply Season winter still builds a coherent result (snow plus the pass
   plus falling snow) with the new temperature-driven amount.

9. Physical plausibility pass. Step back and sanity-check the looks: wetness darkening and gloss,
   snow whitening and roughness, frost sheen and sparkle, dust and moss aging. Flag anything that
   reads wrong for its stated cause.

## Verification (headless, measured, not eyeballed)

A Blender 5.2 binary is in the CLI env at `~/.steam/steam/steamapps/common/Blender/blender`. Run it
`--background --factory-startup --python <script>`, put `blender/` and `blender/extensions/` on
sys.path, `import bob_blender_tools; bob_blender_tools.register()`, then drive the operators and
inspect the datablocks (node trees, attributes, drivers, modifier inputs).

To MEASURE a weather term exactly rather than eyeball it, use the emission-probe technique already
proven on this branch: build a material that instances `weather_group` directly, set its Base Color
input to black, wire its Base Color output into an Emission then Material Output, render to OpenEXR
with `view_transform = Raw` and `film_transparent`, and read the pixels. With a black base and one
term active, the output channel is that term's contribution times its tint, so the coverage or frost
amount is recoverable (for snow, output R divided by `_SNOW_ALBEDO[0]`). Drive the state either by
setting the `S_EnvState` Value nodes directly, or by setting `bbt_env` and calling
`shaders_panel._install_env_drivers(scene)` so the real driver chain runs. Isolate a term by zeroing
the others (for example set Frost Strength to 0 to read snow alone). Sample interior pixels, not the
whole frame, so edge antialiasing does not pull the mean below the true value.

For geometry (the pass and the shell), evaluate the depsgraph and read the `snow_cover` and
`snow_occlusion` POINT attributes directly.

## Rules (hard)

- Verify headless and measured. Do not judge behaviour by reading the node graph alone.
- Plain house style in any doc or code you write.
- Branch is `fix/audit-remediation`. Do not commit unless Siva asks.
- Propose before any large or interface-changing fix, and remember the weather interface version rule
  (bump `S_GROUP_VER` and the `S_WaterMaster` override together).
