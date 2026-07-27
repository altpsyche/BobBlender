# Using BobBlenderTools

This is the front door. It tells you what the addon does, in the order you would actually do it,
and points at the reference doc for each stage. Everything else in `docs/` is a reference or a
design record; this is the only one written for someone who has just opened Blender.

BobBlenderTools builds procedural worlds inside Blender 5.2 LTS. You get a terrain, paths and
rivers carved into it, scattered props, layered materials that react to weather and season, and an
atmosphere that ties them together. All of it is geometry nodes and shader nodes you can open and
edit. Nothing is baked behind your back except the terrain heightfield, and that is a PNG on disk.

Everything lives in one N-panel tab in the 3D viewport: press `N`, pick **BobBlenderTools**.

---

## Five minutes to something on screen

You need the addon installed and enabled (see [README.md](../README.md) for install). You do not
need ComfyUI, a GPU, an asset pack, or a repo checkout.

Start from an empty scene. You do not need to add a mesh; the terrain build creates one.

1. Open the **Terrain** panel. If a red box says "Terrain compute not installed in Blender", click
   **Enable Compute**. It installs scipy (and CuPy if you have an NVIDIA card) into Blender's own
   Python, with your consent, and takes a minute or two.
2. Pick a **Preset**. `alpine` gives you mountains, `canyon` gives you a river-cut plateau.
3. Click **Bake + Build Terrain**. The panel bakes a heightfield and builds the displaced mesh,
   creating an object called `Terrain` (the **Object** field names it) on a 90 m tile by default. A
   grey landscape appears.
4. Open the **Biome** panel. Leave the biome on `blockout` and click **Build Biome**.

You should now see: a layered terrain material (soil, grass, rock, blended by slope and altitude),
four scatter layers of grey proxy props standing on it, a sky with the sun at the biome's time of
day, and the season and weather set. It is grey because `blockout` is a block-out biome: solid
tints and procedural proxies, no downloaded art. That is the point. It is a complete scene you can
light and frame immediately, and every piece of it is a knob you can turn.

You can also skip the terrain entirely. **Build Biome** shades and scatters whatever mesh is
active, so a plain `Add > Mesh > Grid` gets you a flat biome in one click. It just will not have
any landform under it.

---

## Three ways to drive it

The same builders sit behind all three. Pick by what you are doing, not by preference.

### The panels

The default. Direct manipulation, undo works, you see the result as you go. Use this for anything
where you are making a look rather than executing a plan. Everything below assumes the panels
unless it says otherwise.

### MCP, for driving Blender from an agent

The addon ships an MCP server inside itself, so an agent (Claude Code, or any MCP client) can
author into your live session or into a headless `.blend`. Use it when the work is describable
faster than it is clickable: "put a river down the north face, scatter rocks on the banks, set it
to a wet autumn evening, render it".

Start the bridge in the **Advanced** panel (**Start**), click **Copy MCP Config**, paste that into
your client, connect. Full setup in [MCP.md](MCP.md).

Fourteen tools:

| Tool | What it does |
|------|--------------|
| `build_live` | Apply a list of ops to the **open** Blender session. What you watch happen in the viewport. |
| `build` | Apply the same ops into a headless `.blend` file instead. No Blender window needed. |
| `render_scene` | Render the live session (or a saved `.blend`) to an image and return the path, so the agent can see its own result. |
| `bake_heightfield` | Bake and erode a terrain heightfield PNG. Runs in the MCP process on numpy, or CuPy if the machine has it. |
| `list_biomes` | Every biome on the search path and what each one builds: terrain, which scatter kinds, world. |
| `list_library_assets` | The asset packs and biomes found, including the bundled block-out pack. |
| `list_projects` | Project folders under the projects root. |
| `create_project` | Scaffold a new project folder. |
| `comfy_status` | Is ComfyUI reachable, on what device, free VRAM, queue depth, which workflows are installed. Never fails. |
| `comfy_texture_set` | Prompt to a seamless PBR texture set in the generated pack. Returns the `apply_texture_set` op ready to send. |
| `comfy_mesh` | Prompt to a staged scatter asset, geometry plus PBR. Returns the `import_generated` op. |
| `comfy_paint_mesh` | Texture a mesh you already have, in its own UVs. |
| `comfy_heightmap` | Prompt to a terrain macro mask. Returns the `bake_heightfield` `macro` fragment. |
| `comfy_stylize` | Restyle a rendered frame while holding its composition. A pitch frame, not geometry. |

The five `comfy_*` generation tools need a local ComfyUI. With none, they return
`{"ok": false, "error": "...not reachable..."}` and the other nine are unaffected.

The op vocabulary the two build tools take spans the whole suite and is documented field by field
in [API.md](API.md). Worked scenes, including a full one and a forest trail, are in
[MCP.md](MCP.md).

### Headless and CLI

For batches, regression runs, and anything that should not need a window.

```sh
uv run --project tools bob-setup                    # sync the venv, dev-install the extension
uv run --project tools bob-new-project <name>       # scaffold projects/<name>/
uv run --project tools bob-mcp                      # launch the MCP server by hand
```

Bake a terrain heightfield with no Blender at all:

```sh
uv run --project tools python -m bobtools.hf_cli --out /abs/height.png --knobs-file knobs.json
uv run --project tools python -m bobtools.hf_cli --backends   # what compute is available
```

`knobs.json` is flat: `{"preset": "alpine", "size": 1024, "seed": 7, "relief": 0.6}`. The result
metadata prints as JSON on the last stdout line. Details in [TERRAIN.md](TERRAIN.md).

`tools/scripts/` holds the headless runners: `build_extension.py` builds the distributable zip,
`headless_texset.py` exercises the texture-set sampler, and `headless_comfy_all.py` runs every
shipped generation gate as one command.

---

## 0. World

**What it is for.** The scene-wide state everything else reads: season, weather, temperature,
wetness, wind, time and place. Materials darken when it rains because they read this. Snow appears
on the terrain because this says it is below freezing. Set it first, because every later stage
takes its cue from it.

**Shortest path.** Pick a **Season** and click **Apply Season**. If no sky exists yet, a **Build
Sky** button appears at the top of this panel; click it. That is a lit scene with a coherent
season.

**The knobs that matter.**

- **Season** plus **Apply Season** is the one seasonal lever. It stamps snow, wetness and
  temperature, and winter builds falling snow and coverage for you.
- **Conditions (live)** sit on top of the season and update immediately: Weather, Temperature,
  Wetness, Snow Line, Cloud Cover, Wind Direction and Strength, Frost. Below 0 C it snows, and
  Snow Line decides how far down the mountain.
- **Live Environment** is the master switch for whether materials and atmosphere follow this state
  at all. Off, they hold whatever they were built with.
- **Quality** (Preview / Final) trades volumetric and particle cost against fidelity across every
  atmosphere subsystem at once.
- **Time and place** is collapsed because you set it once: time of day, date, latitude, longitude,
  UTC offset. It drives real sun geometry.
- **Sky Look** is a staged whole-atmosphere mood. It rebuilds the atmosphere subsystems and never
  touches the season.

**Next.** Biome, if you want a whole scene in one click. Terrain, if you want to shape the ground
first.

For the full field list and how each consumer reads it, see [FIRMAMENT.md](FIRMAMENT.md).

---

## 1. Biome

**What it is for.** One pick that stands up a coherent scene: terrain material, scatter layers, and
world mood, all from one recipe. It is the fastest way from nothing to something worth looking at,
and it is also a good starting point to then edit by hand.

**Shortest path.** Select a terrain mesh (or set a Scatter emitter), pick a biome, click **Build
Biome**.

**The knobs that matter.**

- **Build Biome** applies every section the biome's manifest carries, in order: terrain material,
  scatter layers, world. If a step fails it stops and tells you which one, rather than reporting
  success over a half-built scene.
- **Weather assets** (on by default) converts the scattered props' materials to BobShaders so they
  react to rain and season like the ground does. Turn it off to keep plain materials.
- **Set Biome World** takes only the mood from a biome: season, weather, time, wind. No terrain, no
  scatter. Use it to match the light of one biome while keeping the ground you built.

**What ships.** One biome, `blockout`. Procedural proxies and solid-tint terrain layers, no
external model files, no downloads. It is deliberately the canonical biome rather than a
placeholder: it works everywhere, it validates, and it is what the whole pipeline is tested
against. Add more by pointing the addon at an asset pack folder (add-on preferences, or
`$BOB_ASSET_PACKS`) and clicking **Rescan Asset Packs** in Advanced.

**Next.** Terrain, to shape the ground the biome is sitting on. Or Shaders, to open up what it
built.

For the manifest schema and how to author a biome, see [BIOME-SYSTEM.md](BIOME-SYSTEM.md).

---

## 2. Terrain

**What it is for.** The landform. A filter stack generates relief and then erodes it with a
stream-power fluvial model, so valleys have drainage, canyons have incision, and dunes have slip
faces at sand's real angle of repose. This is not eroded noise; the erosion is doing the work.

**Shortest path.** Pick a **Preset**, click **Bake + Build Terrain**.

**Object** is a name, not an object picker. It defaults to `Terrain`: the build creates that object
if it does not exist and rebuilds it in place if it does. Point two terrains at two different names
to keep both.

**The knobs that matter.**

- **Preset**: thirteen, in four families. Mountains (`alpine`, `glacial`, `foothills`), Lowlands
  (`hills`, `plains`, `coastal`, `islands`), Canyons (`mesa`, `canyon`, `badlands`, `plateau`),
  Dunes (`dunes`, `sand_sea`). Picking one loads slider values; nothing rebuilds until you bake.
- **Sculpt**: the four knobs that modulate the preset. **Relief** (how much vertical), **Detail**
  (how much high frequency), **Erosion** (how hard the fluvial pass runs), **Warp** (how much the
  domain distorts), plus **Seed**.
- **Backend**: `auto` uses the GPU when CuPy is installed and falls back to CPU. **Check Backends**
  tells you which you have.
- **Displace** (collapsed) is where real-world scale lives. **Tile size** in metres, **Height**,
  **Exaggeration**, **Sea Level**, **Mesh Density**. The panel prints the resulting peak height and
  metres-per-vertex, so you can tell whether a 1.8 m character will read on it. A preset holds a
  relief *ratio*, not a fixed height, so `alpine` stays proportioned whether the tile is 90 m or
  4 km.
- **Filter Stack (advanced)** turns off the four curated knobs and lets you edit the op list
  directly: add, remove, reorder, and set each op's parameters and mask. **Load Preset Stack**
  pulls the current preset's ops in as a starting point.

Mesh Density is deliberately separate from bake resolution. The heightmap keeps full detail for
shading; the mesh needs only enough vertices for the silhouette.

**Next.** Paths, to carve a river or trail into it. The terrain must be baked before paths can be
naturalised.

For the op library, the erosion model, amplification, and every preset's stack, see
[TERRAIN.md](TERRAIN.md).

---

## 3. Paths

**What it is for.** Typed curves that drive four channels at once: the terrain shape under them,
the material band on top, the scatter around them, and (for water roles) an actual water surface.
Draw a line, get a road that benches into the hillside with shoulders, a material that reads as
gravel, and trees that pull back from it.

**Shortest path.** Set **Terrain** to your baked terrain, click **Add Curve**, pick a role, edit
the curve in the viewport, click **Build This Curve**.

**The knobs that matter.**

- **Role** is the main decision, and re-picking it resets every shape parameter to that role's
  preset. Five roles in two families. Follow-terrain: **Dirt Path** (4.8 m wide, 0.3 m deep),
  **Trail** (2.4 m, barely recessed), **Road** (9.0 m, a flat bench plus 1.5 m shoulders, embanked
  on slopes, and its own material class so paved reads different from dirt). Impose: **River**
  (10 m, a monotonic descending channel the terrain conforms down to) and **Stream** (4 m,
  shallower and quicker).
- **Channels**: Terrain shape, Material band, Scatter, and for rivers and streams, Water. Each is a
  checkbox and each applies on Build, not live.
- **Build All** carves every terrain-channel curve at once.
- **Bake & Erode Curves** is the finishing move: it folds the carves into the baked heightfield and
  weathers them, so a road cut stops looking like it was stamped. It needs a baked terrain, because
  it works on the heightfield raster, not the live carve. **Revert to Clean** undoes it.

Paths are LIVE geometry nodes by default, so dragging a control point updates the carve. Bake and
erode when the layout is settled.

Reeds on a riverbank are a Scatter layer with Curve mode set to **Verge**, not a path setting. The
panel says so where you need to know it.

**Next.** Scatter, which can read the path mask you just made.

For the role catalog, the drape, and the LIVE vs BAKED split, see [SPLINES.md](SPLINES.md).

---

## 4. Scatter

**What it is for.** Instancing props over the terrain with Poisson distribution, slope and altitude
masks, vertex-group painting, path awareness, and camera culling. Geometry nodes throughout, so a
million instances cost one mesh.

**Shortest path.** Set **Emitter** to your terrain, click **Add Layer**, pick a type. The layer
builds immediately with proxy assets.

**The knobs that matter.**

- **Layer type** presets the whole layer. **Trees** (sparse, upright, pulls back from paths),
  **Rocks** (tilted to the surface, allows slopes), **Plants** (denser, lightly clumped), **Grass**
  (dense, small, clumped), **Empty** (recipe defaults, bring your own collection).
- **Emitter** is what you scatter on; **Camera** enables camera culling.
- **Masks** (sub-panel): slope range, altitude range, noise clumping, and a **Mask Group** vertex
  group you can paint by hand.
- **Curve** mode binds the layer to your paths: **Clear** pulls it off the path band, **Keep only**
  confines it to the band, **Verge** puts it on the shoulders only (this is the reeds case), and
  **Along curve** places instances along the curve itself for fence posts and cobbles.
- **Biome Scatter** builds a whole layer stack from a biome's recipe in one pick, without touching
  terrain or world.
- **Build All** rebuilds every layer of the emitter.

Each layer draws from a `BOB_Assets_<Kind>` collection. Adding a layer creates the shared proxies
if they do not exist, so you never start from nothing.

**Next.** Shaders, to make the props and the ground look like something.

---

## 5. Shaders

**What it is for.** BobShaders: three material masters that carry weather, season and wetness
response, triplanar projection, anti-tiling, and per-layer texture sockets. Any plain material can
be converted into one.

**Shortest path.** Select a mesh, click **New BobShader** and pick a master, or click **Convert**
next to an existing material.

**The knobs that matter.**

- **Three masters.** **Surface** for props, rocks and vegetation. **Terrain**, a multi-layer master
  that blends layers by slope, altitude, noise and paint with a height-aware blend. **Water** for
  river and stream ribbons: flowing, depth-tinted, foaming, and it freezes below 0 C.
- **Convert** appears per material slot, and the scope dropdown widens it to all slots, the
  selected meshes, or a whole collection.
- **Terrain Layers** (sub-panel) is where the terrain master is authored: **Add Layer**, per-layer
  and whole-stack presets, and **Layer Masks** to control what each layer sticks to. **Biome
  Terrain** builds the whole stack from a biome recipe.
- **Apply Texture Set** assigns a PBR set to a layer. **Triplanar** switches a layer to projected
  mapping so a cliff has no stretch.
- **Weather** (sub-panel) is the per-material response to the World state: how wet it gets, how
  much snow it takes, how much frost.
- **Add Snow Shell** adds accumulated snow as a real shell rather than a shader trick.

**Scattered assets are editable here too.** Select a scatter layer object and the panel lists the
materials of its instanced assets, with a **Convert assets to BobShader** button. The asset sources
are unlinked and not viewport-selectable, so this is the way in.

**Next.** Atmosphere, to light it.

For the shader catalog and the node group architecture, see [SHADERS.md](SHADERS.md).

---

## 6. Atmosphere

**What it is for.** Sky and sun, volumetric clouds and fog, rain, motes, and snow coverage. All of
it reads the World state, so it stays coherent with the season and weather you set at stage 0.

**Shortest path.** Click **Build Sky**. With Time and place already set, that is a physically
placed sun.

**The knobs that matter.**

- **Sky**: sun override and the sky knobs. Edit them, then **Rebuild Sky** on the Atmosphere
  header. Without an override, the sun position comes from the World panel's time, date and
  coordinates.
- **Clouds** and **Fog** each have a Build button plus live knobs and presets. Fog defaults dense,
  which is a real foggy-morning look but will wash a frame grey; the presets (`ground_mist`,
  `valley`, `banks`, `thick`) and a density override are how you get a thin, beam-friendly haze.
- **Weather**: **Build Rain**, **Build Motes** (dust and pollen; bind it to a camera), **Add Snow
  Coverage**. **Use Env Wind** and **Use Env Snow** pull these from the shared world state rather
  than setting them twice.
- **Apply Sky Look** on the World panel is the whole-atmosphere mood preset if you would rather not
  tune each subsystem.

**Next.** Frame a camera and render. Or go back to World and change the weather; everything you
built follows.

For every subsystem's parameters, see [FIRMAMENT.md](FIRMAMENT.md).

---

## 7. Advanced

**What it is for.** Things an artist does not need every day: the agent bridge, the ComfyUI
service, and asset pack management. Collapsed by default on purpose.

**What is in it.**

- **MCP Bridge.** **Start** / **Stop** the live socket, and a status line that should read
  `running on :9876`. **Copy MCP Config** puts a ready snippet on your clipboard with this
  install's resolved path already filled in. **Reload Builders** is a dev reload for when you have
  edited a recipe body.
- **ComfyUI (generation): optional, never required.** A status line, **Test Connection**, **Free
  VRAM**, **Start Server** and **Stop Server**. Below that, **Stylise Last Render**: it renders the
  camera plus true depth and normal passes and restyles the frame with a prompt. That makes a pitch
  frame, not scene data, which is why it lives here rather than in a pipeline stage.
- **Rescan Asset Packs**, after you point the add-on preferences at a new pack folder.
- Any running generation job shows here with its elapsed time and a cancel button.

---

## What ComfyUI adds, and what happens without it

**Nothing in this addon requires ComfyUI.** With no server running, the Generate rows read "not
connected" and are greyed out, every `comfy_*` MCP tool returns `{"ok": false}` with a reason, and
every other feature behaves exactly as it does with a server. The extension zip ships no models, no
ComfyUI, and no new hard dependency; it is about 260 KB.

That is a tested property, not a stated intention. On a machine with no server,
`tools/scripts/headless_comfy_all.py --fast` runs all twelve gates and reports "every gate passed
or skipped cleanly", exiting 0: the checks behind the server skip, and everything that does not
need one still runs and still passes.

Install it if you want generated content. Skip it and you lose nothing you have read about above.

What it adds, and where each one appears:

| Where | What you get |
|-------|--------------|
| Terrain panel, **Generate Base** | A prompted macro mask that decides where the massif and the basins go. The erosion stack still builds every slope; the mask only sets the layout. |
| Scatter panel, **Generate Asset** | A prompt to a finished scatter asset: reference image, geometry, PBR texture, then Blender bakes it, scales it to a real height, builds LODs and BobShades it into the pack. |
| Scatter panel, **Asset from Block-out** | The same, but the active mesh's shape conditions the geometry, so the result keeps the silhouette and footprint of the proxy you placed in your layout. |
| Shaders panel, **Generate Variants** | Seamless PBR texture sets from a prompt, with accept / reject / upres. |
| Advanced panel, **Stylise Last Render** | A styled concept frame from your render, holding its composition via true depth and normal passes. |

Three things worth knowing before you start.

- **Generated meshes are scatter-grade by design.** Dense triangles, no edge flow, no UV seam
  control. Convincing at 3 m, mush at 30 cm. Fine for a scattered background prop, not for a hero
  asset without retopology. The UI says so.
- **Give every generated asset a real height.** Every image-to-3D model emits a unit-cube mesh.
  Without a height in metres the scatter looks like a toy set. It is the most commonly skipped
  detail in this kind of pipeline.
- **Block-out control has three modes and the default is right.** `point` samples your proxy's
  surface and gives the best ground plan. `voxel` quantises it to a 16-cubed grid: 19% faster, and
  it matches `point` on a compact shape like a boulder, but it loses a thin one, because anything
  narrower than a sixteenth of the longest axis is not in the control. `bbox` sends three numbers
  and uploads nothing, which makes it the fallback when mesh transport is unavailable, at about
  half the footprint accuracy. Leave it unset unless you have a reason.

Setup, model downloads, licensing and the full measurement record are in [COMFYUI.md](COMFYUI.md)
and [THIRD-PARTY-MODELS.md](THIRD-PARTY-MODELS.md).

---

## Troubleshooting

**"Terrain compute not installed in Blender"** in the Terrain panel. The bake needs scipy on CPU
and CuPy on GPU, inside Blender's own Python. Click **Enable Compute** in that same box; it
downloads the right wheels and verifies the GPU. **Check Backends** (the `?` beside the backend
row) reports what you actually have. Machines with no GPU bake on CPU and that is a supported path,
not a degraded one.

**Generation says "ComfyUI not connected".** Expected if you have not installed it. If you have:
**Advanced > Test Connection**, and **Start Server** if it is not up. Default address is
`127.0.0.1:8188`.

**A mesh-uploading generation route fails at the node, or starts a 13.5 GB download.** Every route
that hands ComfyUI a mesh needs `BOB_COMFY_DIR` pointing at your ComfyUI checkout. Without it the
mesh control fails at the node, and one route responds by trying to fetch its weights fresh. Set
the variable before starting Blender or the MCP server. The `bbox` control mode is the one
mesh-shaped control that works without it, because it uploads nothing.

**Generated files land somewhere you did not expect.** Generation writes into the generated pack,
and the Blender side has to resolve the same folder. Set `BOB_GENERATED` so both halves agree. In a
live session the addon's own output folder wins instead, so pass back the `pack_dir` each tool
returns.

**A biome or asset pack does not show up.** Point the add-on preferences (or `$BOB_ASSET_PACKS`) at
the pack folder, then click **Rescan Asset Packs** in Advanced. The bundled block-out pack is
always present as the floor of the search path, so an empty biome list means the panel is not
seeing your folder rather than that none exist.

**`no live bridge on 127.0.0.1:9876`.** The bridge is not running. **Advanced > Start**, and check
the status line reads `running on :9876`. Autostart is off by default because it is an agent
feature, not something an artist needs; turn it on in the add-on preferences if you want it up
every launch.

**Port already in use.** Set `BOB_BRIDGE_PORT` in the MCP config's `env` and `$BOB_BRIDGE_PORT` on
the Blender side to match, or free the port.

**Your agent cannot see the tools, or sees a stale signature.** Two different reloads, and picking
the wrong one wastes time. A change to the MCP tool list or the op contract needs a client
**reconnect** (`/mcp reconnect bobblendermcp` in Claude Code). A change to a recipe body needs
**Reload Builders** in the Advanced panel. If you reinstalled or upgraded Blender, the versioned
path in your MCP config moved: re-run **Copy MCP Config**.

**`Blender not found`** from the headless `build` tool. Set `BOB_BLENDER` to the executable path,
or put Blender on `PATH`.

**`path escapes the working dir`.** An output path resolved outside `BOB_WORKDIR`. Set
`BOB_WORKDIR` to the folder you want to write under, or pass a path inside it.

**`bake_heightfield` fails on import.** numpy is missing from the MCP launch environment. Keep the
`--with numpy>=1.26` in the config snippet.

**Headless `build` raises on ops that read addon state.** Headless `build` runs against a
`--factory-startup` Blender with the addon's `core` imported but the addon itself not enabled, so
any op that reads a PropertyGroup the addon registers will raise there. That is `set_env`,
`apply_season` and `scene_preset` (they read the shared env), and also `apply_biome`, whose scatter
half reads `Object.bbt_scatter_coll` and fails with
`AttributeError: 'Object' object has no attribute 'bbt_scatter_coll'`.

Use `build_live` for any of them. If you need a headless `.blend`, the pieces work individually:
`shade_terrain` shades, `build_geonodes` with the `scatter` recipe scatters, and `build_sky` works
headlessly as long as you pass an explicit `time_of_day` (a bare `build_sky` reads the env it
cannot see).

More MCP-specific cases are in [MCP.md](MCP.md).

---

## Where to go next

| You want | Read |
|----------|------|
| Install, repo layout, building the zip | [../README.md](../README.md) |
| Every recipe and parameter in one place | [SYSTEMS.md](SYSTEMS.md) |
| Terrain ops, presets, erosion, scale | [TERRAIN.md](TERRAIN.md) |
| Typed curves, roles, water | [SPLINES.md](SPLINES.md) |
| BobShaders masters and node groups | [SHADERS.md](SHADERS.md) |
| Sky, clouds, fog, weather, snow | [FIRMAMENT.md](FIRMAMENT.md) |
| Biome manifests and authoring | [BIOME-SYSTEM.md](BIOME-SYSTEM.md) |
| Driving Blender from an agent | [MCP.md](MCP.md) |
| The op vocabulary, field by field | [API.md](API.md) |
| Generation: setup, models, measurements | [COMFYUI.md](COMFYUI.md) |
| Model and node pack licensing | [THIRD-PARTY-MODELS.md](THIRD-PARTY-MODELS.md) |
| How the code is organised | [ARCHITECTURE.md](ARCHITECTURE.md) |
| Naming, file locations, panel conventions | [CONVENTIONS.md](CONVENTIONS.md) |
