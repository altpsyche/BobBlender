# Biome / asset / scatter rethink + World-look UX redesign

Status: DONE. Landed 2026-07-22 on branch fix/audit-remediation. Plain house style.

## What this track did

Three problems, all resolved:
1. The World "look" controls were confusing: Season + Apply Season overlapped an instant-firing
   Scene Preset (both wrote season/snow/wetness/temperature by divergent paths), plus Biome World
   and Apply Biome fired heavy chains on a menu pick.
2. The only on-disk biome (`verdant_trail`) used real glTF assets Siva disliked; the whole
   real-asset import path existed to serve it.
3. There was no block-out biome wiring the proxy props into the full terrain/scatter/world pipeline.

## Final design

### World look: three clearly separated layers (world_panel.py)
- World now (live conditions): time/place + weather/temperature/wetness/snow/cloud_cover/wind, all
  driver-fed and editable at any time. A caption notes weather rain/storm wets the ground and
  combines with Wetness by the higher of the two (env.weather drives every BobShader's wetness live
  via materials.env_state_group; it was never inert).
- Season: the ONE seasonal lever. Season dropdown + Apply Season writes snow/wetness/temperature and,
  for winter, builds the falling-snow motes + coverage pass. The live conditions stay editable on top
  of whatever a season stamps.
- Sky Look: a STAGED whole-atmosphere mood (time/weather/cloud/wind + cloud/fog/rain/mote subsystems).
  Staged = pick from the dropdown, then press Apply Sky Look; nothing fires on the pick. Sky Looks no
  longer touch season/snow/wetness/temperature (stripped from SCENE_PRESETS), so Season and Sky Look
  never fight. The pending pick lives on bbt_firmament.sky_look.

The stage-then-Apply idiom is `ui_helpers.staged_preset_row` (new). The heightfield landscape preset
(bbt_hf.preset) was converted from an instant update-callback to the same staged idiom (pick, then
Apply Preset), so both presets in the suite read identically.

### Biome panel (new top-level entry, bl_order 1, world_panel.py)
- Build a whole biome: staged pick (bbt_world.biome) + Build Biome button = the terrain -> scatter ->
  world chain on the Scatter emitter / active mesh, with a "Weather scattered assets" checkbox.
- Biome world only: staged pick (bbt_world.biome_world) + Set Biome World, sets the env mood from a
  biome's world block (optional Build Sky). Both moved out of the World panel.
- Pipeline order is now World=0, Biome=1, Terrain=2, Paths=3, Scatter=4, Shaders=5, Atmosphere=6,
  Advanced=7.

### Blockout biome (canonical), real-glTF path removed
- `library/models/verdant_trail/` deleted (git rm -r; LFS pointers dropped, blobs stay in history).
- `library/models/blockout/manifest.json` added: a proxy-only v2 biome. meta.proxy=true, no models
  block (proxies from bbmcp.proxies supply geometry), terrain layers with NO texture (solid tints),
  a scatter recipe for trees/rocks/plants/grass, a world block.
- `assets.validate_biome` is proxy-aware: a proxy biome (meta.proxy, or no models block) skips the
  model-file and scatter-needs-models checks. Texture-set checks only fire for a layer that names a
  texture.
- Removed from bbmcp/assets.py: import_gltf, populate_scatter_assets, biome_models, and the glTF-only
  helpers (_assets_collection, _clear_collection, _tri_count, _decimate_to, _override_matrix). The
  bpy/math/mathutils imports went with them.
- Removed the "Import Real" operator/button from the Scatter panel; Make Proxies + Biome Scatter is
  the one asset path.

### Terrain solid, image texture-set feature removed
- Decision 3 (Siva): remove the jpg texture sets, keep solids. library/textures never existed on
  disk, so the textured path was already dead.
- Removed the whole image texture-set subsystem: bbmcp/materials.py texture_set_group / texture_set_dir
  / _find_maps + the surface_material texture_set param and the terrain_material layer_sets param; the
  Shaders panel's texture pickers (surface_texture, layer_texture), their operators, _texture_sets /
  _set_items / _layer_sets helpers, and the _TEXSET knob UI.
- Kept ALL procedural/generated terrain code: erosion, heightfields, the baked flow/wetness drainage
  maps, snow coverage, macro break-up, the solid per-layer tint stack (S_TerrainMaster).

## Verification (all headless, Blender 5.2)
Register the addon, drive operators, MEASURE (node groups, modifiers, instance collections, env
fields). Confirmed each phase: blockout is the only biome and validates clean; Biome Scatter builds
four proxy GN layers; Biome Terrain builds S_TerrainMaster with zero TexSet groups and four solid
layers; Sky Look leaves all seasonal fields unchanged while Apply Season still owns them; Build Biome
runs the whole chain (solid terrain + four proxy layers converted to BobShaders + world env set). A
permissive panel-draw smoke draws all 19 panels with no error. The venv heightfield suite (43 tests)
still passes.

## Follow-up: live sun (2026-07-22)

Siva flagged that time_of_day / lat / long felt dead: they fed the sun only through a manual Build
Sky on the Atmosphere panel. Made them live. The sun position is a nonlinear solar calc, so it cannot
be a driver (a custom-function pydriver breaks on untrusted .blends), and msgbus does not fire on
scripted property sets. So: env.py now carries a geo-hook registry (register_geo_hook) and an
update callback on the geographic fields (time_of_day/year/month/day/utc_offset/latitude/longitude);
BobFirmament subscribes a hook that repositions the Sun lamp + energy + the sky node's sun angle
(no node rebuild) whenever a geographic field or a sun override changes, gated on Live Environment.
The sky node is named BOB_Sky (bbmcp/world.py) so the reposition can target it. Verified headless:
sky elevation tracks the solar model across the day, energy zeroes below the horizon, latitude and
manual override are live, Live-Environment-off freezes it.

## Notes
- verdant_trail blobs remain in git history (recoverable); only the working-tree pointers were removed.
- Operator idnames were kept stable (bob_blender_tools.world_apply_biome / world_biome_world), so
  keymaps and scripts referencing them still resolve; only their arguments moved to staged props.
