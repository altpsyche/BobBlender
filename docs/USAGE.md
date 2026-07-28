# Using BobBlenderTools

This is the front door. It tells you what the addon does, in the order you would actually do it,
and points at the reference doc for each stage. Everything else in `docs/` is a reference or a
design record; this is the only one written for someone who has just opened Blender.

BobBlenderTools builds procedural worlds inside Blender 5.2 LTS. You get a terrain, paths and
rivers carved into it, scattered props, layered materials that react to weather and season, and an
atmosphere that ties them together. All of it is geometry nodes and shader nodes you can open and
edit. Nothing is baked behind your back except the terrain heightfield, and that is a PNG on disk.

Everything lives in one N-panel tab in the 3D viewport: press `N`, pick **BobBlenderTools**.

Optionally, a local ComfyUI adds generation to four of the stages: prompted terrain layouts,
prompted scatter assets, prompted materials, and a stylised concept frame. **None of it is
required.** See [Generating content](#generating-content-with-comfyui).

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

## Set-up worth doing once

None of this is needed to start, but all of it saves friction later. It lives in
`Preferences > Add-ons > BobBlenderTools`.

- **Asset Pack Folders.** Point these at folders holding `models/<biome>/` and `textures/<set>/`
  and their biomes and texture sets join the pickers. `$BOB_ASSET_PACKS` does the same from the
  environment. The bundled block-out pack is always present as the floor of the search path, so
  the suite works with none set. After adding one, click **Rescan Asset Packs** in the Advanced
  panel.
- **Output Folder.** Where baked heightfields and generated data are written. Empty means beside
  the saved `.blend`, or a per-user cache while the file is unsaved. Set it if you want bakes in a
  known place.
- **Start bridge on launch.** Off by default. Turn it on only if you drive Blender from an agent
  every session.
- **ComfyUI URL / Folder / Reserve VRAM.** Optional. Empty URL means `http://127.0.0.1:8188`. The
  Folder is only needed for the Advanced panel's **Start Server** button; everything else works
  over HTTP against a server you started yourself. Reserve VRAM is passed to a Bob-started server
  so Blender keeps enough of the card to hold a viewport.

The preferences also carry a short licensing notice, because models are your download and output
licensing follows the model. Full table in [THIRD-PARTY-MODELS.md](THIRD-PARTY-MODELS.md).

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

Fourteen tools. Nine work with no ComfyUI at all:

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

Seven more need a local ComfyUI:

| Tool | What it does |
|------|--------------|
| `comfy_texture_set` | Prompt to a seamless PBR texture set in the generated pack. Returns the `apply_texture_set` op ready to send. |
| `comfy_bark_set` | A bark set for BobFoliage, measured for grain direction as well as tiling. Pass the name a species preset asks for (`bark_conifer`, `bark_broadleaf`) and the tree wears it with no apply step. |
| `comfy_leaf_atlas` | A grid of foliage sprites on transparent, for BobFoliage's leaf cards. The set records its own grid, so a tree only has to name it. |
| `comfy_mesh` | Prompt to a staged scatter asset, geometry plus PBR. Returns the `import_generated` op. |
| `comfy_paint_mesh` | Texture a mesh you already have, in its own UVs. **MCP only; there is no panel button for this.** |
| `comfy_heightmap` | Prompt to a terrain macro mask. Returns the `bake_heightfield` `macro` fragment. |
| `comfy_stylize` | Restyle a rendered frame while holding its composition. A pitch frame, not geometry. |

With no server the seven return `{"ok": false, "error": "...not reachable..."}` and the nine are
unaffected.

Each generation tool hands back the op that consumes its result, ready to send: `comfy_mesh`
returns an `import_generated` op, `comfy_texture_set` an `apply_texture_set` op, `comfy_heightmap`
the `macro` fragment for `bake_heightfield`. That split is deliberate: talking to ComfyUI needs no
Blender, so only the steps that need `bpy` are ops.

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

`knobs.json` is flat: `{"preset": "alpine", "size": 1024, "seed": 7, "relief": 0.6}`. Add
`--preview` for a fast 256 look, `--force` to ignore the cache, `--maps` to also emit the flow and
wetness sidecars. The result metadata prints as JSON on the last stdout line. Details in
[TERRAIN.md](TERRAIN.md).

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
  Snow Line decides how far down the mountain. Rain and storm wet the ground; whichever of that
  and the Wetness slider is higher wins.
- **Live Environment** is the master switch for whether materials and atmosphere follow this state
  at all. Off, they hold whatever they were built with.
- **Quality** (Preview / Final) trades volumetric and particle cost against fidelity across every
  atmosphere subsystem at once. Leave it on Preview while you work.
- **Time and place** is collapsed because you set it once: time of day, date, latitude, longitude,
  UTC offset. It drives real sun geometry, so an afternoon in Reykjavik and one in Nairobi do not
  look the same.
- **Sky Look** is a staged whole-atmosphere mood. It rebuilds the atmosphere subsystems and never
  touches the season.

**Next.** Biome, if you want a whole scene in one click. Terrain, if you want to shape the ground
first.

For the full field list and how each consumer reads it, see [FIRMAMENT.md](FIRMAMENT.md).

---

## 1. Biome

**What it is for.** One pick that stands up a coherent scene: terrain material, scatter layers, and
world mood, all from one recipe. It is the fastest way from nothing to something worth looking at,
and a good starting point to then edit by hand.

**Shortest path.** Select a terrain mesh (or set a Scatter emitter), pick a biome, click **Build
Biome**.

**The knobs that matter.**

- **Build Biome** applies every section the biome's manifest carries, in order: terrain material,
  scatter layers, world. If a step fails it stops and tells you which one, rather than reporting
  success over a half-built scene.
- **Weather assets** (on by default) converts the scattered props' materials to BobShaders so they
  react to rain and season like the ground does. Turn it off to keep plain materials.
- **Set Biome World** takes only the mood from a biome: season, weather, time, wind. No terrain, no
  scatter. Use it to match the light of one biome while keeping the ground you built. **Build Sky**
  beside it (on by default) rebuilds the sky so the sun moves to the biome's time.

The same recipe is also reachable one piece at a time: **Biome Terrain** in the Shaders panel
builds only the layer stack, and **Biome Scatter** in the Scatter panel builds only the layers.
Build Biome is those two plus the world, in order.

**What ships.** One biome, `blockout`. Procedural proxies and solid-tint terrain layers, no
external model files, no downloads. It is deliberately the canonical biome rather than a
placeholder: it works everywhere, it validates, and it is what the whole pipeline is tested
against. Add more by pointing the addon at an asset pack folder and clicking **Rescan Asset
Packs**.

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
  domain distorts), plus **Seed**. 0.5 on each means the preset as authored.
- **Backend**: `auto` uses the GPU when CuPy is installed and falls back to CPU. **Check Backends**
  (the `?` beside the row) tells you which you have.
- **Resolution** is the bake resolution. **Flow + wetness maps**, on by default, also writes
  `<name>_flow.png` and `<name>_wetness.png` beside the heightmap. Those are what let a terrain
  material put moss in the drainage channels, so leave it on unless you are iterating fast.
- **Displace** (collapsed) is where real-world scale lives. **Size m** (tile width), **Height**,
  **Exaggeration**, **Sea Level**, **Mesh Density**. The panel prints the resulting peak height and
  metres-per-vertex, so you can tell whether a 1.8 m character will read on it. A preset holds a
  relief *ratio*, not a fixed height, so `alpine` stays proportioned whether the tile is 90 m or
  4 km. Exaggeration is an honest separate multiplier: the panel shows the true-scale peak beside
  the exaggerated one when they differ.
- **Filter Stack (advanced)** turns off the four curated knobs and lets you edit the op list
  directly: add, remove, reorder, and set each op's parameters and mask. **Load Preset Stack**
  pulls the current preset's ops in as a starting point. In custom mode the bake runs your stack
  verbatim and the Sculpt knobs are greyed, because they would be lying.

Mesh Density is deliberately separate from bake resolution. The heightmap keeps full detail for
shading; the mesh needs only enough vertices for the silhouette.

**With ComfyUI: Generate Base.** A prompt field above Bake + Build produces a *macro mask*, not a
terrain. It decides where the massif, the basin and the ridge go; the erosion stack still builds
every slope. Once a mask exists you get a toggle, a **Mask Weight** and an **Invert** (which way a
model paints elevation is a coin flip per prompt, so Invert is the fix when white came back as low
ground). Turn the toggle off and the preset bakes exactly as it always did. It is labelled a mask
rather than a generator on purpose, because it is an input to the press below it.

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
  preset. There is no separate reset; re-picking the role is the reset. Five roles in two families.
  Follow-terrain, which levels to the live terrain height under the centreline and then recesses:
  **Dirt Path** (4.8 m wide, 0.3 m deep), **Trail** (2.4 m, barely recessed), **Road** (9.0 m, a
  flat bench plus 1.5 m shoulders, embanked on slopes, and its own material class so paved reads
  different from dirt). Impose, where the terrain conforms down to a monotonic descending water
  centreline: **River** (10 m, 1.2 m deep) and **Stream** (4 m, shallower and quicker).
- **Channels** are checkboxes and each applies on Build, not live: Terrain shape, Material band,
  Scatter, and for rivers and streams, Water.
- **Build All** carves every terrain-channel curve at once.
- **Bake & Erode Curves** is the finishing move: it folds the carves into the baked heightfield and
  weathers them, so a road cut stops looking like it was stamped. It needs a baked terrain, because
  it works on the heightfield raster, not the live carve. **Erosion**, **Scope** and **Deposit
  bars** tune it, and **Revert to Clean** undoes it.

Paths are LIVE geometry nodes by default, so dragging a control point updates the carve. Bake and
erode when the layout is settled.

Two things the panel warns you about rather than failing silently: a curve dragged off the terrain
carves only its on-terrain part, and a terrain with no bake gets carved using the curve's own Z.

Reeds on a riverbank are a Scatter layer with Curve mode set to **Verge**, not a path setting.

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

- **Layer type** presets the whole layer. **Trees** (sparse, upright, pulls back further from a
  path), **Rocks** (tilted to the surface, allows slopes), **Plants** (denser, tilted, lightly
  clumped), **Grass** (dense, small, clumped), **Empty** (recipe defaults, bring your own
  collection).
- **Emitter** is what you scatter on; **Camera** enables the Camera Cull sub-panel, which stops
  paying for instances outside the frame.
- **Active Layer** carries density, spacing, scale range, and **Align** (Up for trees, Normal for
  anything that should tilt to the ground).
- **Masks** (sub-panel): an **Altitude** band, **Noise / clumping**, and **Paint** via a **Mask
  Group** vertex group you set on the layer and then Build. Each band does nothing until its
  Strength is above 0, and the dependent knobs grey out until then.
- **Curve** mode binds the layer to your paths: **Clear** pulls it off the path band, **Keep only**
  confines it to the band, **Verge** puts it on the shoulders only (this is the reeds case), and
  **Along curve** places instances along the curve itself for fence posts and cobbles. An
  along-curve layer is placed by spacing rather than masks, and the Masks sub-panel says so.
- **Biome Scatter** builds a whole layer stack from a biome's recipe in one pick, without touching
  terrain or world.
- **Build This Layer** / **Build All** rebuild one or every layer of the emitter.

Each layer draws from a `BOB_Assets_<Kind>` collection. Adding a layer creates the shared proxies
if they do not exist, so you never start from nothing.

**With ComfyUI: Generate Asset.** A prompt, a **Kind** (trees / rocks / plants / grass), a
real-world **Height (m)**, a **Face Budget**, a **Seed** and a **Hero** toggle. It generates a
reference image, then geometry plus PBR texture, and Blender then bakes it, scales it to the
height, drops the origin to the base, builds an LOD chain, converts it to a BobShader and writes it
into the pack and the matching `BOB_Assets_<Kind>` collection, ready to scatter. It runs in the
background; the viewport stays usable and a progress row with a cancel appears in Advanced.

Kind is load-bearing beyond where the asset lands: `plants` and `grass` are treated as foliage,
which keeps the open surfaces a leaf needs by turning off both the remesh and the pinhole fill.
Hero raises the bake and texture resolution; it does not buy you clean topology.

**Without ComfyUI, for anything that grows: Grow in BobFoliage.** Pick trees, plants or grass and a
**Grow in BobFoliage** button appears in the same box. It builds a real tree, shrub or grass tuft at
the 3D cursor from that kind's species preset — a trunk, branch levels and alpha leaf cards, all
procedural. It needs no server and is instant, and every knob is live on the object's modifier, so
you can drag its height, branch angles, card size and droop and watch it change.

Use it for anything standing and alive. Generation is for **dead wood** — stumps, fallen logs, snags,
root balls — which is what it is genuinely good at, and for ground clumps that read at 2 m or further
as scatter filler. It cannot make a crown: an image-to-3D model returns one solid mesh with no leaf
cards and no alpha, so a generated tree comes back as an opaque fan. The greyed note under the kind
selector says which is which, and the button beside it is the other road.

**Asset from Block-out** is the same button with one extra input. Make a block-out proxy the active
object and the generated asset keeps its silhouette and footprint, so it drops into the layout you
blocked out. The proxy's own height replaces the Height field, because a proxy you already placed
has already said how big the asset is. The button only appears when there is a mesh to condition
on, and names it.

**Next.** Foliage, if what you want to fill the kind with is growing. Otherwise Shaders, to make
the props and the ground look like something.

---

## 5. Foliage

**What it is for.** BobFoliage: procedural trees, shrubs and grass tufts, grown from a skeleton and
dressed with alpha leaf cards. Every part of the geometry is a recipe, so it needs no ComfyUI server
at all; generation's only job here is the two texture sets a tree wears, and both have a block-out
fallback. It is the answer to the thing image-to-3D cannot do: a generated tree comes back as one
opaque fan with no branch hierarchy and no cutout.

**Shortest path.** Open BobFoliage, press **+** and pick a species. You have a tree. Everything
below is tuning it and then turning it into a forest.

**The list is the trees.** `BOB_Foliage` holds them and the panel draws that collection directly, so
a tree deleted in the outliner simply leaves the list. **+** grows one from a species preset at the
3D cursor, the copy button grows another of the same species at a fresh seed, and **Load Species**
replaces the active tree's shape while keeping the object, its place and anything pointing at it.

**Shape.** Two kinds of control and the panel keeps them apart. **Structural** — Levels, Profile,
Skeleton Only, and the two texture sets — changes what is built and applies on a **Build** press;
tuned knobs survive it. Everything else is a live modifier input: drag it and the tree changes.
There are about thirty, grouped as the trunk (height, radius, taper, lean, gnarl, segments) and one
box per branch level (count, angle, length, radius, phyllotaxy, where on the parent it starts).

*Skeleton Only* is worth knowing about: it emits the curves and skips the sweep, so tuning structure
is much faster, and a detached branch is obvious in that view and invisible in the swept one.

**Leaves.** Cards per tip, card size and width, droop and spread. Cards 0 leaves a bare skeleton.
Droop is a direction blend rather than a rotation, so at 1 every spray hangs vertically whatever its
branch was doing.

**Wind.** Sway and Leaf Flutter belong to the species — a spruce is stiff, a birch is all motion —
and Wind and Wind Direction do not: they are the world's, written onto every tree by the World
applier, so they are greyed here whenever there is a world. Raise Wind Strength in World and the
whole scene moves, with no keyframes and no per-tree press. The autumn turn follows the world's
season the same way, through the shader.

**Variants.** This is how a tree becomes a forest. **Make Variants** bakes N seeds of the ACTIVE
tree — its tuned knobs, not its species preset — into `BOB_Assets_<Kind>`, which is exactly the pool
a scatter layer reads. Eight is the default: enough that a repeat is not findable in a frame. Then
add a Trees layer in Scatter and it picks among them.

Two things worth knowing about what a variant is. It stays **live**, so a scattered stand still
feels the world's wind — a frozen mesh would be a forest stopped on one frame — and the cost of that
is per variant rather than per instance, so four hundred trees cost what four hundred instances of
one tree do. And the **LOD ladder** is on by default: each variant is also built at two cheaper
rungs into `BOB_Foliage_LODs`, which is out of the scatter pool (or a layer would instance all three)
and out of the scene until you point a layer at it. The rungs are rebuilds of the recipe at a lower
branch depth, never a decimate, because decimating a tree spikes the twigs and destroys the card
quads.

**Write to Pack** additionally exports the bake as ordinary generated assets. Those are frozen —
glTF carries no node group, so the wind and the leaf shader stay behind — but each entry records the
species and the seed, so a Bob file that has the preset regrows the exact variant alive.

**Textures.** Two slots, a bark set and a leaf atlas, both ordinary texture-set names resolved
through the pack search path, so artist-made content needs no import step. Beside each is a
**Generate** button (ComfyUI, in the background). A species preset already names the bark it wants,
so generating it under that name is enough — no assignment step. Until then the trunk is a solid
tint, which is the intended block-out state; a placeholder leaf atlas ships, so cards always work.

**What to watch for.** A tree with no bark set is a flat tint, not a bug. If a stand looks like one
tree repeated, you scattered before you baked variants, or the layer is pointed at the proxy pool.
If the trees are half-buried, that is the layer's Z Offset rather than the tree — a baked variant's
origin sits at its base.

**Next.** Scatter, to lay the variants over a terrain. Or Shaders, to make the ground look like
something.

---

## 6. Shaders

**What it is for.** BobShaders: three material masters that carry weather, season and wetness
response, triplanar projection, anti-tiling, and per-layer texture sockets. Any plain material can
be converted into one.

**Shortest path.** Select a mesh, click **New BobShader** and pick a master, or click **Convert**
next to an existing material.

**The knobs that matter.**

- **Three masters.** **Surface** for props, rocks and vegetation. **Terrain**, a multi-layer master
  that blends layers by slope, altitude, noise and paint with a height-aware blend. **Water** for
  river and stream ribbons.
- The panel lists **every material slot on this mesh** and nothing else. Each row either reports
  which master it is or offers **Convert**. The scope dropdown beside it widens Convert to all
  slots, the selected meshes, or a whole collection.
- **Surface Preset** stamps a whole look on a surface material: `rock`, `cliff`, `bark`, `soil`,
  `metal`, `painted`, `grass_blade`.

**Terrain Layers** (sub-panel) is where the terrain master is authored.

- Up to **six layers**, and the panel draws only the enabled ones so the box shows the depth
  actually in use. **Add Layer** adds; the per-row checkbox removes. Stacking is by Height Bias,
  not slot order, so there is nothing to reorder.
- **Stack Preset** (`temperate`, `alpine`, `desert`) sets the whole stack; **Layer Preset**
  (`soil`, `grass`, `rock`, `cliff`, `scree`, `sand`) sets the active one. **Biome Terrain** builds
  the stack from a biome recipe.
- **Layer Masks** (sub-panel) is what makes a layer land somewhere specific, and it is the same
  mask vocabulary Scatter uses: a **Slope band**, an **Altitude band**, **Noise / clumping**,
  **Paint / curvature**, a **Flow band** that puts a layer in the drainage channels, and a **Curve
  band** that puts one along a path or road. Flow needs the terrain's flow maps baked and Curve
  needs Paths' Bake & Erode; both default to Strength 0, so an unbaked scene is unchanged.

**Water** (sub-panel) opens on the depth colour and optics. Two collapsed children hold the rest:
**Flow and foam** (animated, so it needs playback to read) and **Freeze**, which turns the surface
to ice and also fires on its own below 0 C.

**Textures.**

- **Apply Texture Set** samples a set into the active terrain layer (or the surface material):
  albedo, roughness and a detail height that drives a bump. It is staged rather than instant,
  because assigning a set rewires the graph.
- **Triplanar** switches a layer to projected mapping so a cliff has no UV stretch. Anti-tiling is
  built into the masters rather than being a knob you have to find.

**Weather** (sub-panel) is the per-material response to the World state: how wet it gets, how much
snow it takes, how much frost. **Add Snow Shell** adds accumulated snow as real geometry rather
than a shader trick, and **Remove Snow Shell** takes it off.

**Scattered assets are editable here too.** Select a scatter layer object and the panel lists the
materials of its instanced assets, with a **Convert assets to BobShader** button. The asset sources
are unlinked and not viewport-selectable, so this is the way in.

**With ComfyUI: Generate Variants.** Inside the texture-set block, so a generated set and a
downloaded one are the same kind of thing from the moment it lands. A prompt describing the
surface, an optional **Reference** photo (which switches to a reference workflow that locks your
photo's palette), a **Seed** and a variant **count**. Texture generation is a pick-one-of-several
loop, so it makes several at once and stages them; a thumbnail plus a picker then offers
**Accept**, **Reject**, **Upres 2x** and **Reject All**. Accept moves the variant into the pack and
assigns it through the same path the ordinary picker uses. Reject deletes it, so staging only ever
holds what is still awaiting a decision.

The sets are seamless by circular padding in both the UNet and the VAE, which is measured rather
than claimed. The same block serves a surface material and a terrain layer, because a surface has
one set where a terrain has six.

**Next.** Atmosphere, to light it.

For the shader catalog and the node group architecture, see [SHADERS.md](SHADERS.md).

---

## 7. Atmosphere

**What it is for.** Sky and sun, volumetric clouds and fog, rain, motes, and snow coverage. All of
it reads the World state, so it stays coherent with the season and weather you set at stage 0.

**Shortest path.** Click **Build Sky**. With Time and place already set, that is a physically
placed sun.

**The knobs that matter.**

- **Sky**: sun override and the sky knobs. Edit them, then **Rebuild Sky** on the Atmosphere
  header (the same button changes label once a sky exists). Without an override, the sun position
  comes from the World panel's time, date and coordinates.
- **Clouds** and **Fog** each have a Build button plus live knobs and a preset menu. Fog defaults
  dense, which is a real foggy-morning look but will wash a frame grey; the presets
  (`ground_mist`, `valley`, `banks`, `thick`) and a density override are how you get a thin,
  beam-friendly haze that lets light shafts read.
- **Weather**: **Build Rain**, **Build Motes** (dust and pollen, worth binding to a camera),
  **Add Snow Coverage**, each with its own preset menu. **Use Env Wind** and **Use Env Snow** pull
  from the shared world state rather than making you set it twice.
- **Randomize Seed** reshuffles the particulate layout without touching anything else.
- **Apply Sky Look** on the World panel is the whole-atmosphere mood preset if you would rather not
  tune each subsystem.

Remember **Quality** on the World panel. Preview keeps volumetrics cheap while you work; switch to
Final before you render.

**Next.** Frame a camera and render. Or go back to World and change the weather; everything you
built follows.

For every subsystem's parameters, see [FIRMAMENT.md](FIRMAMENT.md).

---

## 8. Advanced

**What it is for.** Things an artist does not need every day: the agent bridge, the ComfyUI
service, and asset pack management. Collapsed by default on purpose.

**What is in it.**

- **MCP Bridge.** **Start** / **Stop** the live socket, and a status line that should read
  `running on :9876`. **Copy MCP Config** puts a ready snippet on your clipboard with this
  install's resolved path already filled in. **Reload Builders** is a dev reload for when you have
  edited a recipe body.
- **ComfyUI (generation): optional, never required.** A cached status line showing URL, device,
  free VRAM and queue depth, plus **Test Connection**, **Free VRAM**, **Start Server** and **Stop
  Server**. Stop Server only stops a server Bob started. The status is a cache refreshed by a
  button or a finishing job, never by drawing the panel, so a dead server cannot freeze the UI.
- **Stylise Last Render**, with three widgets: a style prompt, a **Strength** (the denoise, the one
  knob that trades style against silhouette) and render **Samples**. It renders the camera plus
  true depth and normal passes and restyles the frame. That makes a pitch frame, not scene data,
  which is why it lives here rather than in a pipeline stage.
- **Rescan Asset Packs**, after you point the add-on preferences at a new pack folder.
- Any running generation job shows here with its elapsed time and a cancel button.

---

## Generating content with ComfyUI

**Nothing in this addon requires ComfyUI.** With no server running, the Generate rows read "not
connected" and are greyed out, every `comfy_*` MCP tool returns `{"ok": false}` with a reason, and
every other feature behaves exactly as it does with a server. The extension zip ships no models, no
ComfyUI, and no new hard dependency; it is about 260 KB.

That is a tested property, not a stated intention. On a machine with no server,
`tools/scripts/headless_comfy_all.py --fast` runs all twelve gates and reports "every gate passed
or skipped cleanly", exiting 0: the checks behind the server skip, and everything that does not
need one still runs and still passes.

Install it if you want generated content. Skip it and you lose nothing described above.

### What it adds, and where

| Stage | Control | What it makes |
|-------|---------|---------------|
| Terrain | **Generate Base** | A macro mask: where the massif, basin and ridge go. The erosion stack still builds every slope. |
| Scatter | **Generate Asset** | A finished scatter asset from a prompt: geometry, PBR texture, real-world scale, LODs, BobShader, in the pack. |
| Scatter | **Asset from Block-out** | The same, conditioned on a proxy's shape, so it keeps that silhouette and footprint. |
| Shaders | **Generate Variants** | Seamless PBR texture sets from a prompt or a reference photo, with accept / reject / upres. |
| Advanced | **Stylise Last Render** | A styled concept frame from your render, composition held by true depth and normal passes. |
| MCP only | `comfy_paint_mesh` | Textures a mesh you already have, in its own UVs. No panel button exists for this. |

Two models do the 3D work, each for what only it does, and the panel never asks you to choose
between them.

- **TRELLIS.2 is the default for everything**, because it is the only one of the two that can
  return an open surface at all (so a leaf stays a leaf rather than a leaf-shaped bag), it uses
  less VRAM on both foliage and solids, and it is MIT.
- **Hunyuan3D runs when you condition on a block-out.** Passing a control forces the route that
  carries it, which is where the Omni block-out path lives. That is the one case where it is
  automatic.
- **Hunyuan3D is otherwise an explicit choice**, over MCP only, with `comfy_mesh(route="alt")`. It
  is roughly twice as fast on solid shapes and returns properly closed shells, but it costs more
  VRAM and its licence excludes the EU, UK and South Korea, which is why it is not a default
  anybody can inherit by accident. It is also the route that still works if the TRELLIS.2 node pack
  is missing or broken, since its geometry model needs no custom pack.

The **Kind** field does not pick the model. It decides foliage handling: `plants` and `grass` turn
off both the remesh and the pinhole fill, and it decides which `BOB_Assets_<Kind>` collection the
asset joins.

### What to expect, honestly

- **Generated meshes are scatter-grade by design.** Roughly 50k to 500k triangles before
  simplification, no quads, no edge flow, no UV seam control. Convincing at 3 m and mush at 30 cm.
  Fine for a scattered background prop, not for a hero asset without retopology. There is no local
  hero tier to have: the face budget is delivered by a ComfyUI simplify node, and Blender's own
  Decimate cannot reach it on these meshes at all. The **Hero** toggle raises bake and texture
  resolution, and says so rather than implying clean topology.
- **Always give a generated asset a real height.** Every image-to-3D model emits a unit-cube mesh,
  so without a height in metres the scatter looks like a toy set. It is the most commonly skipped
  detail in this kind of pipeline, which is why the field is not optional in spirit.
- **Diffusion heightmaps are not terrain.** They are low frequency and have no drainage logic,
  which is exactly why Generate Base feeds the op stack as a mask instead of replacing it. If the
  mask starts competing with the erosion stack it is being misused.
- **Block-out control has three modes and the default is right.** `point` samples your proxy's
  surface and gives the best ground plan. `voxel` quantises it to a 16-cubed grid: 19% faster, and
  it matches `point` on a compact shape like a boulder, but it loses a thin one, because anything
  narrower than a sixteenth of the longest axis is not in the control. `bbox` sends three numbers
  and uploads nothing, which makes it the fallback when mesh transport is unavailable, at about
  half the footprint accuracy. Leave it unset unless you have a reason.
- **16 GB of VRAM is the reference and it is tight.** Set Reserve VRAM in preferences so Blender
  keeps enough of the card for a viewport, and use **Free VRAM** in Advanced when a job has
  finished and you want the card back.
- **Generation never blocks the viewport.** Jobs run on a worker with a progress row and a cancel.
  The only main-thread cost is the bpy part of a press: one glTF write for a block-out, one render
  for Stylise.

Setup, model downloads and licensing are in [GENERATION.md](GENERATION.md), the phase-by-phase measurement
record is in [GENERATION-BASELINES.md](GENERATION-BASELINES.md), and the per-model terms are in
[THIRD-PARTY-MODELS.md](THIRD-PARTY-MODELS.md). Licensing is worth reading before you download
20 GB of weights: models are your download, output licensing follows the model, and two of the
routes use models that are not permissive.

---

## Rendering

There is no render panel, on purpose: Blender's own is fine and the suite does not want to own it.
Two things are worth knowing.

- **Switch Quality to Final** on the World panel first. Preview deliberately undersamples the
  volumetrics, and a fog or cloud pass rendered at Preview will not match what you approved.
- **Over MCP, `render_scene` renders the live session by default** and returns the path, which is
  how an agent sees its own result. Pass `base_file` to render a saved `.blend` headlessly instead.
  The EEVEE engine id in Blender 5.2 is `BLENDER_EEVEE`, with no `_NEXT` suffix.

Output paths follow [CONVENTIONS.md](CONVENTIONS.md): `renders/<project>/<YYYYMMDD>/`.

---

## Troubleshooting

**"Terrain compute not installed in Blender"** in the Terrain panel. The bake needs scipy on CPU
and CuPy on GPU, inside Blender's own Python. Click **Enable Compute** in that same box; it
downloads the right wheels and verifies the GPU. **Check Backends** reports what you actually have.
Machines with no GPU bake on CPU and that is a supported path, not a degraded one.

**Generation says "ComfyUI not connected".** Expected if you have not installed it. If you have:
**Advanced > Test Connection**, and **Start Server** if it is not up. Default address is
`127.0.0.1:8188`, overridable in preferences or `$BOB_COMFY_URL`.

**A mesh-uploading generation route fails at the node, or starts a 13.5 GB download.** Every route
that hands ComfyUI a mesh needs `BOB_COMFY_DIR` pointing at your ComfyUI checkout. Without it the
mesh control fails at the node, and one route responds by trying to fetch its weights fresh. Set
the variable before starting Blender or the MCP server. The `bbox` control mode is the one
mesh-shaped control that works without it, because it uploads nothing.

**Generated files land somewhere you did not expect.** Generation writes into the generated pack,
and the Blender side has to resolve the same folder. Set `BOB_GENERATED` so both halves agree. In a
live session the addon's own Output Folder wins instead, so pass back the `pack_dir` each tool
returns.

**A biome or asset pack does not show up.** Point the add-on preferences (or `$BOB_ASSET_PACKS`) at
the pack folder, then click **Rescan Asset Packs** in Advanced. The bundled block-out pack is
always present as the floor of the search path, so an empty biome list means the panel is not
seeing your folder rather than that none exist.

**A texture set or layer preset is there but nothing changes.** Texture sets and stack presets are
*staged*, not instant: picking one arms it and the Apply button commits it. That is deliberate,
because assigning a set rewires the material graph rather than setting a value.

**A mask band does nothing.** Every band in Scatter Masks and Layer Masks is inert until its
Strength is above 0, and the dependent knobs stay greyed until then. Flow and Curve bands
additionally need their source baked: terrain flow maps for Flow, Paths' Bake & Erode for Curve.

**`no live bridge on 127.0.0.1:9876`.** The bridge is not running. **Advanced > Start**, and check
the status line reads `running on :9876`. Autostart is off by default because it is an agent
feature, not something an artist needs.

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
| Generation: setup, workflows, limits | [GENERATION.md](GENERATION.md) |
| Generation: the measurement record | [GENERATION-BASELINES.md](GENERATION-BASELINES.md) |
| Why generated trees are trunks, and the plan for crowns | [FOLIAGE.md](FOLIAGE.md) |
| Model and node pack licensing | [THIRD-PARTY-MODELS.md](THIRD-PARTY-MODELS.md) |
| How the code is organised | [ARCHITECTURE.md](ARCHITECTURE.md) |
| Naming, file locations, panel conventions | [CONVENTIONS.md](CONVENTIONS.md) |
