# MCP: drive Blender from an agent

BobBlenderTools lets an agent (Claude Code, or any MCP client) author into Blender over MCP.
The agent-side MCP server ships **inside the extension**, so this works from a plain installed
zip with **no repo checkout**. Blender 5.2 LTS only.

There are two halves:

- **Blender side** — a localhost socket server inside the extension (`bridge/server.py`). It
  applies whitelisted `core` ops on Blender's main thread. Start it from the **Advanced** panel.
- **Agent side** — the stdio MCP server that ships in the extension at `mcp_agent/`. Your agent
  client spawns it; it validates ops (Pydantic), then either sends them to the running Blender
  over the socket (`build_live`) or spawns Blender headlessly (`build`).

## Quick start (standalone, no repo)

1. **Install the extension.** Blender → Preferences → Get Extensions → Install from Disk, pick
   `bob_blender_tools-<version>.zip`. Enable it in Preferences → Add-ons.

2. **Start the bridge.** In the 3D viewport, open the **BobBlenderTools** tab → **Advanced** →
   **Start**. Autostart is OFF by default (it is an agent feature, not something an artist needs);
   turn it on in the add-on preferences if you want the bridge up on every launch.

3. **Get the MCP config.** In the same Advanced panel, click **Copy MCP Config**. This copies a
   ready `.mcp.json` snippet with *this install's* resolved path already filled in, e.g.:

   ```json
   {
     "mcpServers": {
       "bobblendermcp": {
         "type": "stdio",
         "command": "uv",
         "args": [
           "run",
           "--with", "mcp>=1.2",
           "--with", "pydantic>=2",
           "--with", "numpy>=1.26",
           "python", "/home/you/.config/blender/5.2/extensions/user_default/bob_blender_tools/mcp_agent/__main__.py"
         ],
         "env": {}
       }
     }
   }
   ```

   The only dependencies are `mcp` + `pydantic` (+ `numpy` for `bake_heightfield`). `uv` fetches
   them into an ephemeral environment; nothing is installed globally. (No `uv`? Any Python 3.11+
   works — replace the `uv run --with ... python` prefix with your interpreter and
   `pip install "mcp>=1.2" "pydantic>=2" "numpy>=1.26"` into it.)

4. **Register it with your client.** Paste the snippet into your client's MCP config
   (`.mcp.json` for Claude Code — project-local or global), then approve the `bobblendermcp`
   server. In Claude Code: `/mcp reconnect bobblendermcp`.

5. **Point it at your folders** (optional — see below). By default it works against the current
   directory and the bundled block-out asset pack.

6. **Build.** Ask the agent to build. With the bridge running it lands in your live viewport
   (`build_live`); or it writes a headless `.blend` (`build`).

## Pointing at your own folders

The server is repo-free and reads its locations from the environment (set them in the `.mcp.json`
`env` block, or your shell). All are optional:

| Variable | What it controls | Default |
|----------|------------------|---------|
| `BOB_WORKDIR` | Root that all output paths are sandboxed under (a build cannot escape it). | current working directory |
| `BOB_PROJECTS` | Where `list_projects` / `create_project` look. | `<workdir>/projects` |
| `BOB_RENDERS` | Renders root. | `<workdir>/renders` |
| `BOB_TEMPLATE` | Folder `create_project` copies for a new project. | none (creates a bare folder + README) |
| `BOB_ASSET_PACKS` | `os.pathsep`-separated asset-pack folders (models/biomes + texture sets). | add-on prefs + bundled block-out |
| `BOB_GENERATED` | The generated pack the `comfy_*` tools write into and the Blender side reads back. Set it when generating, or the two halves can disagree about where an asset landed. | `<workdir>/packs/generated` |
| `BOB_COMFY_URL` | The local ComfyUI server the `comfy_*` tools talk to. | `http://127.0.0.1:8188` |
| `BOB_COMFY_DIR` | The ComfyUI checkout, so a mesh can be copied into `<comfy>/input/3d`. **Required by every tool that uploads a MESH** (`comfy_paint_mesh`, and `comfy_mesh` on the `staged` and `alt` routes): the HTTP fallback returns a relative path and the TRELLIS.2 nodes run in an isolated worker that cannot resolve one, so without it those calls fail with "Mesh file not found". Not needed by the default route, which uploads only an image, nor by `comfy_mesh(control_bbox=...)`, which uploads nothing. `control_mode="voxel"` uploads a mesh like `"point"` does and needs it too (measured by the voxel gate), so the bbox control is the ONLY block-out route that runs without it. **It also binds the local Omni weights**, so without it a block-out call keeps the graph's portable HuggingFace id and starts a 13.5 GB download instead of failing (measured by the bbox gate). | none |
| `BOB_BLENDER` | Blender executable for the headless `build`. | known install locations, then PATH |
| `BOB_BRIDGE_HOST` / `BOB_BRIDGE_PORT` | Live bridge socket. | `127.0.0.1` / `9876` |

Example `env` block that writes into a chosen scratch folder and adds an art pack:

```json
"env": {
  "BOB_WORKDIR": "/home/you/bobwork",
  "BOB_ASSET_PACKS": "/home/you/art/verdant_pack"
}
```

## Tools

| Tool | What it does | Needs |
|------|--------------|-------|
| `list_projects` | Project folders under the projects root. | folder config |
| `list_library_assets` | Asset packs + biomes on the search path. | — (bundled pack always present) |
| `list_biomes` | Biomes + what each builds (terrain / scatter kinds / world). | — |
| `create_project` | Scaffold a new project folder. | write access to the projects root |
| `build_live` | Apply ops to the **open** Blender session (viewport). | the bridge running (step 2) |
| `build` | Build ops into a headless `.blend`. | a resolvable Blender binary |
| `bake_heightfield` | Generate + erode a terrain heightfield PNG. | `numpy` (CPU); CuPy on the machine for GPU |
| `render_scene` | Render the live session (or a headless `.blend`) to an image; returns the path. | the bridge, or a Blender binary for `base_file` |
| `comfy_status` | Is ComfyUI reachable, on what device, free VRAM, queue depth, shipped workflows. | — (reports "not reachable" rather than failing) |
| `comfy_texture_set` | Prompt to a seamless PBR texture set in the generated pack. | a local ComfyUI + an SDXL checkpoint |
| `comfy_bark_set` | A bark set, measured for grain DIRECTION as well as tiling. Name it what a species preset asks for and every tree of that species wears it. | a local ComfyUI + an SDXL checkpoint |
| `comfy_leaf_atlas` | A grid of foliage sprites on transparent, as a set with an `opacity` role, for BobFoliage's leaf cards. | a local ComfyUI + an SDXL checkpoint |
| `comfy_mesh` | Prompt to a staged scatter asset (geometry + PBR). Returns the `import_generated` op. | a local ComfyUI + TRELLIS.2 (or `route="alt"`, which needs no custom pack for the geometry) |
| `comfy_paint_mesh` | Texture a mesh you already have, in its own UVs. | a local ComfyUI + TRELLIS.2 |
| `comfy_heightmap` | Prompt to a terrain macro mask. Returns the `bake_heightfield` `macro` fragment. | a local ComfyUI + an SDXL checkpoint |
| `comfy_stylize` | Restyle a rendered frame while holding its composition. | a local ComfyUI + SDXL ControlNets |

The op vocabulary now spans the whole suite: geometry (`add_mesh`, `build_geonodes`,
`make_proxies`), shading (`shade_terrain`, `apply_shader`, `snow_shell`), biome
(`apply_biome`, `world_biome`), typed paths + water (`make_curve`, `curve_build`,
`bake_erode`, `revert_erode`), atmosphere (`build_sky`, `build_clouds`, `build_fog`,
`build_rain`, `build_motes`, `build_snow_cover`, `apply_season`, `scene_preset`), the
shared env (`set_env`, `apply_world`), introspection (`describe_scene`), scene control
(`add_camera`, `render`, `delete`, `clear_scene`), and generation's Blender half
(`apply_texture_set`, `import_generated`, `export_control`). All are documented with their
fields in [API.md](API.md).

**Why generation is tools and not ops.** Talking to ComfyUI needs no Blender, so the `comfy_*` tools
run in the MCP process and block there deliberately; only the steps that need `bpy` are ops. That
split is also why every generation tool hands back the op that consumes its result, ready to send:
`comfy_mesh` returns an `import_op`, `comfy_texture_set` returns an `apply_op`, and `comfy_heightmap`
returns the `bake_params` fragment. ComfyUI is optional throughout — with no server every `comfy_*`
tool returns `{"ok": false, "error": "...not reachable..."}` and nothing else changes.

## Reading the scene back

`describe_scene` is the one op that mutates nothing, and it exists because an agent with no
introspection has to guess and then render probe frames to check the guess. It reports, per object,
the transform, the **modifier stack in order**, the materials, and — for a terrain — the heightmap,
size, height and sea level it was built with. Per material it reports the master kind, each terrain
layer slot with its texture set, **which of that set's maps actually resolve on disk**, and the masks
keying the slot. Per curve it reports the role, the shape params, and the mask and edge attribute
names. It also reports collections, the world state including whether the shared env drivers are
installed, and the pack search path. `objects` narrows it to named objects and `include` narrows it
to any of `objects` / `materials` / `collections` / `world` / `packs`.

Three things it is the answer to specifically:

- **Which layer slot a curve band took.** `curve_build` now also returns it directly in
  `data.slot`, which is the index an `apply_texture_set` must name to put a real surface on a road
  or a river bank. Before either, the only way to find out was to try an index and look.
- **Whether a texture set is actually feeding a layer.** A set whose maps do not resolve renders as
  a solid tint and used to report success, so `maps` per layer is the check. `apply_texture_set`
  now also refuses outright a set that resolves no base colour, rather than reporting success.
- **What a terrain was built from.** `build_geonodes` stamps `bbt_heightmap` and
  `bbt_terrain_size` / `_height` / `_sea` on the object. Everything downstream reads them off the
  object instead of being told: `drape_curve` takes `terrain` and needs no restated numbers (an
  explicit value that disagrees with the stamp still wins, and comes back in `data.warnings`), and
  a curve carved without a drape now returns a warning rather than reporting "carved terrain" as a
  success mode. See [SPLINES.md](SPLINES.md).

**The world needs applying, not just writing.** `set_env` writes `Scene.bbt_env` AND re-applies
every world consumer (drivers, sun, atmosphere wind, quality), which is what makes a season or
wetness change reach a material at all; pass `apply: false` to write and defer. `apply_world` is the
same re-apply with no value change, for the case where the world is right and the scene is new — a
material built after the last world change carries no drivers until something re-installs them.
`set_env`'s result names any field that is **structural** rather than driven (`season` is: send
`apply_season` for it to show).

## Retries: batches and slow ops

`build_live` requests carry an idempotency key. A slow batch used to come back as `main-thread
timeout` while its work completed — the objects were in the scene and the client was told it had
failed, so the safe-looking retry created them twice.

- Every request generates a `batch` id unless you pass one. **Re-sending a known id COLLECTS that
  batch; it never re-runs it.** The bridge keeps the last 32.
- A reply carries `status`: `done` (and `ok` says whether it succeeded) or `running`. **`running` is
  not a failure and the ops must not be re-sent** — poll with the same id.
- A failure names how far it got: `failed on op 5/8: 'import_generated'`, with the results of the
  first four in `results`. A batch is not a transaction; what ran, ran.

## Known gap: ops that need the addon

**One limit of headless `build`.** It imports the extension's `core` into a `--factory-startup`
Blender without enabling the addon, so any op that reads a PropertyGroup the addon registers raises
there. That is the shared env (`set_env`, `apply_season`, `scene_preset`), and **also `apply_biome`**,
whose scatter half reads `Object.bbt_scatter_coll` (registered in `ui/scatter.py`) and fails with
`AttributeError: 'Object' object has no attribute 'bbt_scatter_coll'` even with `world: false`.

**Every curve op is in the same position, and that is the wider version of the gap.** `make_curve`,
`curve_build` and the rest read `Object.bbt_curve`, a PropertyGroup registered by `ui/splines.py`,
so headless `build` raises `AttributeError: 'Object' object has no attribute 'bbt_curve'` and
BobSplines is live-bridge-only today. The same is true of anything reading `bbt_scatter_layer` or
`bbt_world`. Two ways out, neither taken yet: the headless runner registers the addon, or the
per-curve and per-layer state moves out of `ui/` into `core/`. The second matches the "core is the
acyclic root" rule the codebase already follows and is the honest answer; it is also the bigger
change. Note that `tools/scripts/headless_redwood.py` calls `bob_blender_tools.register()` for
exactly this reason, which is what a gate covering curve ops has to do until the gap closes.

Use `build_live` for those, or pass explicit params (`build_sky` with a `time_of_day` works
headlessly; a bare `build_sky` reads the env it cannot see). For a headless `.blend` the pieces work
one at a time: `shade_terrain` shades and a `build_geonodes` `scatter` recipe scatters, which is
between them what `apply_biome` composes.

The example below is therefore **`build_live` only** where it uses `apply_biome`.

## A full scene over MCP

An agent can build a complete, shaded scene with no panel clicks. Bake a heightfield, then
run one op list. This one uses `apply_biome` and `set_env`, so it is **`build_live`**, into the
running session; see the headless limit above. This is the
`library/_generated/full_scene_ops.json` proof, abbreviated:

```jsonc
// 1. bake_heightfield  out_file="_generated/forest_height.png"  params={preset:"alpine", size:1024}
// 2. build_live with:
[
  {"op": "build_geonodes", "recipe": "heightmap_terrain", "name": "Terrain",
   "params": {"heightmap": "_generated/forest_height.png", "size": 200, "resolution": 400, "height": 70}},
  {"op": "apply_biome", "object": "Terrain", "biome": "blockout"},   // shade + scatter + world in one
  {"op": "set_env", "params": {"season": "autumn", "time_of_day": 16, "snow_line": 0.92}},
  {"op": "build_sky", "params": {"time_of_day": 16, "turbidity": 3.2}},
  {"op": "build_clouds"},
  {"op": "make_curve", "name": "River", "role": "river", "terrain": "Terrain"},
  {"op": "curve_build", "curve": "River", "terrain": "Terrain", "do_terrain": true, "do_water": true},
  {"op": "build_snow_cover", "object": "Terrain"},
  {"op": "add_camera", "name": "BOB_Camera", "location": [175, -175, 100], "look_at": [0, 0, 10], "lens": 42}
]
// 3. render_scene  output_file="_generated/full_scene.png"  engine="BLENDER_EEVEE" samples=48
```

`list_biomes` tells the agent which biomes are available and whether each shades terrain,
scatters, and sets the world, so `apply_biome` is a single informed call.

### A forest trail (path corridor + backlit mood)

Two things make a path shot read: the trail's trees must pull back, and the light must come from
the far end. Both are single flags:

```jsonc
[
  {"op": "build_geonodes", "recipe": "heightmap_terrain", "name": "Terrain",
   "params": {"heightmap": "<abs>/_generated/forest_height.png", "size": 60, "resolution": 512, "height": 7}},
  {"op": "apply_biome", "object": "Terrain", "biome": "blockout"},        // shade + scatter + world
  {"op": "set_env", "params": {"season": "summer", "time_of_day": 7.5, "weather": "clear"}},
  {"op": "make_curve", "name": "Trail", "role": "dirt_path", "terrain": "Terrain", "points": [...]},
  {"op": "curve_build", "curve": "Trail", "terrain": "Terrain", "do_terrain": true, "do_material": true},
  // Re-apply the biome scatter path-aware AFTER the curve exists; world:false keeps the env above.
  {"op": "apply_biome", "object": "Terrain", "biome": "blockout", "world": false, "curve_mode": "clear"},
  {"op": "build_sky", "params": {}},   // bare sky now honours set_env's 07:30 -> low morning sun
  {"op": "build_fog", "mode": "ground_fog", "preset": "ground_mist", "density": 0.15},  // thin haze, beams read
  {"op": "build_motes", "preset": "amber", "camera": "BOB_Camera"},
  {"op": "add_camera", "name": "BOB_Camera", "location": [0, -30, 4.5], "look_at": [0, 8, 0.6], "lens": 30}
]
```

- **`build_sky` reads `bbt_env`.** With no params it takes time/date/place from `set_env`, so a
  bare `build_sky` after a `set_env` gives the right sun. For a hero backlight aim it manually with
  `use_override` + a low `sun_elevation` at the `sun_azimuth` the camera faces.
- **`build_fog` defaults dense** (a thick foggy-morning look) and will wash the frame grey. For a
  thin, beam-friendly haze pass a `preset` (`ground_mist`/`valley`/`banks`/`thick`) and/or a
  `density` override.
- **Path-aware biome scatter.** `apply_biome` takes `curve_mode` (`clear` pulls scatter off carved
  paths, `keep` confines it to them) and `world` (set `false` to leave `bbt_env` untouched). Build
  the typed curve first, then re-apply with `world:false, curve_mode:"clear"` to open the corridor
  without re-writing the env you just set.

## A prompted scene over MCP (generation)

Needs a local ComfyUI (see [GENERATION.md](GENERATION.md)); with none of it, every other example above
still works. Check `comfy_status()` first. Two flows, and both are measured end to end in
`tools/scripts/headless_gen_agent_surface.py`.

**Prompt to a shaded terrain, about 24 s.** The mask decides where the massif goes; the erosion stack
builds every slope.

```jsonc
// 1. comfy_texture_set  prompt="mossy forest floor with small stones"
//    -> {"set": "mossy_forest_floor_with_small_stones", "apply_op": {...}, "pack_dir": "..."}
// 2. comfy_heightmap    prompt="one isolated steep massif in the north west, broad low valleys"
//    -> {"path": ".../macro.png", "bake_params": {"macro": {"path": ".../macro.png"}}}
// 3. bake_heightfield   out_file="_generated/terrain.png"
//    params={"preset": "alpine", "size": 1024, "macro": {"path": ".../macro.png"}}
// 4. build_live / build with:
[
  {"op": "build_geonodes", "recipe": "heightmap_terrain", "name": "Terrain",
   "params": {"heightmap": "<abs>/_generated/terrain.png", "size": 180, "resolution": 400,
              "height": 54}},
  {"op": "shade_terrain", "object": "Terrain", "layers": ["soil", "grass", "rock"]},
  // The generated set onto a terrain LAYER. Structural: it rewires the material's sampler nodes.
  {"op": "apply_texture_set", "object": "Terrain", "set": "mossy_forest_floor_with_small_stones",
   "index": 1},
  {"op": "add_camera", "name": "BOB_Camera", "location": [150, -150, 90], "look_at": [0, 0, 10]}
]
// 5. render_scene  output_file="_generated/shot.png"
```

**Prompt to a scattered asset, about 100 s.** `comfy_mesh` is the slow call and does the ComfyUI half
only; `import_generated` is the Blender half (bake, scale to `height_m`, origin to base, LOD chain,
BobShader, write the pack, link into `BOB_Assets_<Kind>`), and `comfy_mesh` hands it back ready.

```jsonc
// 1. comfy_mesh  prompt="a weathered granite boulder covered in lichen"  kind="rocks"
//                height_m=1.8  faces=4000
//    -> {"staged": {...}, "import_op": {...}, "pack_dir": "..."}
// 2. build_live / build with:
[
  <the import_op from step 1, verbatim>,
  {"op": "build_geonodes", "recipe": "scatter", "name": "ScatterRocks",
   "params": {"emitter": "Terrain", "assets": "BOB_Assets_Rocks", "density": 0.4}}
]
```

The `import_generated` result carries a `data` dict, which is how to CHECK the asset rather than
trust it: `lod_faces` against the budget, `uv_overlap`, `height_m`, `origin_above_base`, `master_type`
(should read `surface`), plus any `warnings`. Read it before rendering.

Two more notes:

- **Pass the `pack_dir` each tool returns, and set `BOB_GENERATED`.** Generation writes into the
  generated pack; the Blender side has to resolve the same folder, and the two disagree whenever
  generation and Blender are different processes (the tool uses `$BOB_GENERATED` or
  `<workdir>/packs/generated`, a live addon uses its own output-folder preference). `apply_texture_set`
  takes `pack_dir` for this and registers it on the pack search path for the rest of the session, so
  the set stays resolvable across the material rebuilds a later Shaders edit triggers. Op roots rank
  above the addon preferences and below `$BOB_ASSET_PACKS`. Without it a freshly generated set is
  invisible and the op fails with "no texture set" on a folder that exists.
- **A block-out can drive the shape.** `export_control` writes an existing proxy out as a control mesh
  and returns its path and height in `data`; pass that path to `comfy_mesh(control=...)` and the
  generated asset keeps the silhouette and footprint of the object you placed.
- **The same op also returns `bbox`, and that is the weaker control.** `comfy_mesh(control_bbox=...)`
  conditions on the proxy's three proportions instead of its surface, uploads nothing and saves about
  7 s. Measured against the mesh control on three block-outs: footprint IoU **0.5766** against
  **0.9200**, so use the path unless you cannot. Where you cannot is the case worth knowing: with no
  `BOB_COMFY_DIR` set, the mesh control fails at the node and the bbox control still works.
- **There are three control modes and the mesh path serves two of them.** `control_mode` picks which:
  `"point"` samples the proxy's surface (the default, and the best ground plan) and `"voxel"`
  quantises it to a 16-cubed occupancy grid. Measured on the same three block-outs,
  footprint IoU is **point 0.9106, voxel 0.8507, bbox 0.5766**. Voxel is worth naming when the call
  is time-bound and the proxy is COMPACT: it runs 19% faster (29.0 s warm against 36.0 s) and matches
  the point route on a boulder-shaped block-out, while losing ground on a thin one, because a cell is
  a sixteenth of the longest axis and anything narrower than that is not in the control. Leave it
  unset and you get the measured default. `"voxel"` uploads the same mesh `"point"` does, so it needs
  `BOB_COMFY_DIR` just as much.

## Reload rules (two-sided)

The builder code runs in Blender; the op contract runs in the agent-side server. They reload
independently:

- **Recipe / builder body change** — click **Reload Builders** (Advanced panel). No reconnect.
- **Op-contract change** (a new op, or new fields in `mcp_agent/contracts.py`) — reconnect the
  MCP server: `/mcp reconnect bobblendermcp`, or restart the client. Restarting Blender alone does
  not reload the contract; `build_live` will reject the new op tag until the server reconnects.
- **Tool-signature change** (a new argument on a `comfy_*` tool, such as `control_mode` when the voxel mode arrived) --
  reconnect for the same reason: the client caches the tool schema it was given, so a new argument
  is invisible until it asks again. No Blender-side reload, because nothing in the extension moved.

## Troubleshooting

- **`no live bridge on 127.0.0.1:9876`** — the bridge is not running. Advanced → Start. Confirm
  the status line reads `running on :9876`.
- **Port already in use** — set `BOB_BRIDGE_PORT` (in the `.mcp.json` `env`) and
  `$BOB_BRIDGE_PORT` for the Blender side to match, or free the port.
- **Server will not connect / tool list empty** — check the client approved `bobblendermcp` and
  the path in the snippet still points at the installed extension (re-run **Copy MCP Config** if
  you reinstalled or upgraded Blender — the versioned path changes). Confirm `uv` is on PATH.
- **`Blender not found`** (headless `build`) — set `BOB_BLENDER` to the executable path, or add
  Blender to PATH.
- **`path escapes the working dir`** — an output path resolved outside `BOB_WORKDIR`. Set
  `BOB_WORKDIR` to the folder you want to write under, or pass a path inside it.
- **`bake_heightfield` fails on import** — numpy is missing from the launch env. Keep the
  `--with numpy>=1.26` in the snippet. GPU (CuPy) is used only when present on the machine; CPU is
  the always-available fallback.

## In-repo dev

For work inside the checkout, `uv run --project tools bob-mcp` still runs the same server (a thin
launcher, `tools/bobtools/mcp_launch.py`, points at the extension's `mcp_agent`). The repo
`.mcp.json` uses it. See [ARCHITECTURE.md](ARCHITECTURE.md) for the one-core / two-executor design.
