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

The op vocabulary now spans the whole suite: geometry (`add_mesh`, `build_geonodes`,
`make_proxies`), shading (`shade_terrain`, `apply_shader`, `snow_shell`), biome
(`apply_biome`, `world_biome`), typed paths + water (`make_curve`, `curve_build`,
`bake_erode`, `revert_erode`), atmosphere (`build_sky`, `build_clouds`, `build_fog`,
`build_rain`, `build_motes`, `build_snow_cover`, `apply_season`, `scene_preset`), the
shared env (`set_env`), and scene control (`add_camera`, `render`, `delete`,
`clear_scene`). All are documented with their fields in [API.md](API.md).

## A full scene over MCP

An agent can build a complete, shaded scene with no panel clicks. Bake a heightfield, then
run one op list (`build_live` into the running session, or `build` headless). This is the
`library/_generated/full_scene_ops.json` proof, abbreviated:

```jsonc
// 1. bake_heightfield  out_file="_generated/forest_height.png"  params={preset:"alpine", size:1024}
// 2. build_live / build with:
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

## Reload rules (two-sided)

The builder code runs in Blender; the op contract runs in the agent-side server. They reload
independently:

- **Recipe / builder body change** — click **Reload Builders** (Advanced panel). No reconnect.
- **Op-contract change** (a new op, or new fields in `mcp_agent/contracts.py`) — reconnect the
  MCP server: `/mcp reconnect bobblendermcp`, or restart the client. Restarting Blender alone does
  not reload the contract; `build_live` will reject the new op tag until the server reconnects.

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
