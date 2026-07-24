# Handover: build a beautiful scene from a reference image, live over MCP

Paste this into a fresh chat. Blender is already open with the BobBlenderTools extension enabled.
The user will share a reference image in that chat. Your job: recreate its mood as the best-looking
scene you can, driving the running Blender session entirely through the `bobblendermcp` MCP tools —
no panel clicks. Blender 5.2 LTS only. Do NOT commit anything unless asked.

## What just shipped (why this test exists)

The whole BobBlenderTools suite is now reachable over MCP: the op vocabulary went from 8 low-level
ops to 28, covering shading, biome, typed rivers/water, atmosphere, snow, camera, and render. A
headless proof already built a full scene (terrain + biome + river + sky + clouds + snow + camera)
and rendered it. This chat is the LIVE test of the same surface, driven from a reference image.
See `docs/MCP.md` and `docs/API.md` for the full op list and fields.

## First steps (do these before anything else)

1. **Reconnect the server** so the new op contract loads: `/mcp reconnect bobblendermcp`. Restarting
   Blender alone does NOT reload the agent-side contract; without a reconnect, `build_live` rejects
   the new op tags.
2. **In Blender**: Advanced panel → **Reload Builders** (picks up the new `core/` handlers live),
   and confirm the bridge reads `running on :9876` (Advanced → Start if not).
3. **Smoke-check the live link**: `list_biomes` (read-only), then a trivial `build_live` with a
   single `add_mesh` op and confirm it appears in the viewport. If `no live bridge on 127.0.0.1:9876`,
   the bridge is not started.

## The task

The user shares a reference image. Read it for: terrain character (alpine peaks, rolling hills,
canyon, desert dunes, forest valley), ground cover (snow line, treeline, grass/rock/sand), water
(river, lake, none), sky/time/weather (golden hour, overcast, storm, fog, clear midday), and camera
framing (wide vista, low hero angle). Then compose the scene to match, iterating on a render until it
reads like the reference.

Recommended workflow, all through MCP:

1. **Terrain heightfield** — `bake_heightfield` with a `preset` (alpine / canyon / etc.) and `size`
   (1024 for a real shot, 256 for a fast look), tuning `relief`/`detail`/`erosion`/`warp` to match the
   reference's landforms. It writes a PNG under the workdir.
2. **Terrain mesh** — `build_live` a `build_geonodes` op, recipe `heightmap_terrain`, pointing `params.heightmap`
   at that PNG. Set `size` (world metres), `resolution`, `height`.
3. **Look** — either `apply_biome` (shades terrain + scatters trees/rocks/plants/grass + sets world in
   one call; `list_biomes` shows what is available — only `blockout` ships bundled unless the user has
   `$BOB_ASSET_PACKS`), OR `shade_terrain` with a `stack` preset (`temperate`/`alpine`/`desert`) or
   explicit `layers`, plus `apply_shader` for individual props.
4. **Environment + light** — `set_env` (season, time_of_day, weather, snow_line, temperature), then
   `build_sky` (time_of_day, turbidity), or `scene_preset` for a one-call mood
   (clear_day/golden_hour/overcast/storm/foggy_dawn/dust_storm/winter).
5. **Atmosphere** — `build_clouds`, `build_fog` (height_fog/noise_fog/ground_fog), `build_rain`/`build_motes`
   (with a `preset`), `build_snow_cover` on the terrain where the reference has snow.
6. **Water / paths** — `make_curve` (role: river/stream/dirt_path/road/trail) then `curve_build`
   (pass `terrain` = the terrain object name; `do_terrain`/`do_water` true for a carved river with a
   surface). `bake_erode` deepens river beds (best-effort live).
7. **Camera + render** — `add_camera` (location + look_at + lens), then the `render_scene` tool
   (engine `BLENDER_EEVEE` or `CYCLES`, samples, resolution) which returns the PNG path. Open it,
   compare to the reference, and iterate: adjust env/time, snow line, biome, camera, re-render.

## Gotchas (learned building the headless proof)

- **Order matters for the look**: `apply_biome` writes the biome's own world block to `bbt_env`. If you
  want a specific season/time/snow line, call `set_env` AFTER `apply_biome`, or it gets overwritten.
- **`build_snow_cover` stamps the snow-line Z bounds** from the terrain, so the normalized `snow_line`
  (0..1, snow above the line) then maps correctly — call it after the terrain exists and after `set_env`.
- **`curve_build` needs `terrain`** set to the terrain object's name, or it errors (it has to carve/mask
  against a mesh).
- **`render` engine enum is `BLENDER_EEVEE`** in 5.2 (not `_NEXT`). Cycles GPU (cuda/optix) is available
  for a nicer shot via `device: "GPU"`.
- **Default `build_fog` height fog is dense** — good for a foggy reference, heavy otherwise. Clouds + sky
  alone read clean; add fog deliberately.
- **Bind objects by name**: ops resolve `object`/`terrain`/`curve`/`emitter` by name and raise a clear
  error if missing — check `created` names in each op result and reuse them.

## Constraints + proof

- Drive the RUNNING session with `build_live` (viewport updates as you go), and `render_scene` (no
  `base_file`) to render that live session. Reserve headless `build`/`render_scene base_file=` for a
  clean re-run.
- When the render matches the reference, save the final op list and the render under
  `library/_generated/` (e.g. `ref_scene_ops.json` + `ref_scene.png`) as the proof, alongside the
  reference image for comparison.
- Blender 5.2 only. Do not commit unless the user asks.
