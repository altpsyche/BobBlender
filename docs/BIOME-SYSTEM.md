# Biome system

Canonical reference, written from the code. Source of truth:

- blender/bbmcp/assets.py (manifest reader, accessors, validator)
- blender/bbmcp/proxies.py (block-out proxy assets)
- blender/bbmcp/geonodes/recipes/scatter.py, scatter_along.py (the GN scatter recipes)
- blender/extensions/bob_blender_tools/scatter_panel.py (Biome Scatter, layers)
- blender/extensions/bob_blender_tools/shaders_panel.py (Biome Terrain, Convert)
- blender/extensions/bob_blender_tools/world_panel.py (Build Biome, Biome World, Biome panel)
- library/models/<biome>/manifest.json, library/textures/<set>/

## What a biome is

A biome is a folder under library/models/<name>/ carrying a manifest.json. The manifest
is a self-describing recipe: it names the terrain layer stack, the scatter placement per
kind, and the world mood (season, weather, time). One pick stands up a whole coherent
scene: terrain material, scattered layers, and world state.

The canonical biome is a block-out biome. Its props are procedural proxies
(bbmcp.proxies), its terrain is solid-tint layers, and it references no external model
files. The one shipped biome is library/models/blockout/ (meta.proxy = true).

There is no glTF import path. assets.py only reads and validates manifests; it does not
load model files. Proxy geometry comes from bbmcp.proxies; scatter layers and terrain
materials are built by the panels over existing bbmcp recipes and BobShaders masters.

## Manifest schema (v2)

biome_manifest() in assets.py normalizes any manifest to a v2 dict with five sections.
biome_meta / biome_terrain / biome_scatter / biome_world are thin accessors onto it.

    meta     name, description, climate, source, license, version, proxy
    models   {kind: [entry, ...]} for kinds trees/rocks/plants/grass (back-compat; empty
             for a proxy biome). An entry is a bare file string OR an object with a
             string "file". Never loaded by current code; kept for back-compat only.
    terrain  {"layers": [{"layer": "<key>", "texture"?: "<set>"}, ...]}. A layer with no
             "texture" renders as a solid tint (the block-out default).
    scatter  {kind: {density, scale, min_normal_z, max_normal_z, distance_min, align, ...}}
             the placement recipe. The panel fills any missing key from LAYER_TYPES.
    world    a subset of the Scene.bbt_env fields; the biome's world defaults.

Reserved top-level keys: meta, models, terrain, scatter, world. Any OTHER top-level list
key is treated as a legacy v1 model kind and folded under models. A v1 flat manifest
({kind: [files], "terrain": {...}}) still reads. meta.version defaults to 2 when any of
meta/models/scatter/world is present, else 1.

meta fields the code reads:
- name         display name (defaults to the folder name titled if absent)
- description  free text
- climate      free text
- source       attribution string
- license      license string
- version      1 or 2
- proxy        true marks a proxy biome; the validator then skips the model-file,
               scatter-needs-models, and (per-layer) checks that assume real assets

terrain layer keys (_TERRAIN_LAYER_KEYS, mirrors shaders_panel TERRAIN_LAYER_PRESETS):
soil, grass, rock, cliff, scree, sand.

scatter kinds (_SCATTER_KINDS, mirrors scatter_panel LAYER_TYPES minus "empty"):
trees, rocks, plants, grass.

scatter cfg keys read by the panel (_biome_layer_params in scatter_panel.py):
- density         placement density
- scale           [min, max], mapped to Min Scale / Max Scale
- min_normal_z, max_normal_z   slope band
- distance_min    Poisson minimum spacing
- align           "up" (upright, trees) or "normal" (tilted to surface)
Anything omitted falls back to the kind's LAYER_TYPES defaults.

world fields (_WORLD_FIELDS, the Scene.bbt_env property names): time_of_day, year, month,
day, utc_offset, latitude, longitude, season, weather, temperature, wetness, snow,
cloud_cover, wind_direction, wind_strength.

### The shipped blockout manifest

library/models/blockout/manifest.json:
- meta: name Blockout, climate temperate, source "procedural (bbmcp.proxies)",
  license none, version 2, proxy true.
- terrain: four solid-tint layers soil / grass / rock / cliff (no "texture" on any).
- scatter: trees, rocks, plants, grass, each with density, scale, min_normal_z, align.
- world: season summer, weather clear, time_of_day 10.0, cloud_cover 0.2, temperature
  22.0, wind_direction 90.0, wind_strength 1.5.
- No models block (proxy biome).

## Validation

validate_biome(biome) returns a list of human-readable warnings ([] = clean). It flags:
missing manifest; unknown model kind; missing model file; bad max_polys; malformed model
entries dropped by the normalizer; unknown scatter kind; a scatter kind with no models to
place (skipped for a proxy biome); a scatter cfg that is not an object; terrain layers not
a list; a terrain layer entry that is not an object; an unknown terrain layer key; a
missing terrain texture-set folder (only when a layer names one); an unknown world field.

A biome is treated as a proxy biome (checks that assume real model files are skipped) when
meta.proxy is true OR it has no models block. Warnings are printed to the console and
folded into the operator report; they do not stop a build.

## How a biome loads and gets applied

Enums are built by scanning list_biomes() (folders with a manifest.json) and filtering by
which section a biome carries. Each dynamic enum caches a stable per-session id per biome
(the enum-GC guard the other panels use).

- World panel, Biome panel (BBT_PT_biome): Build Biome and Biome World.
- Shaders panel: Biome Terrain (biomes whose manifest carries a terrain spec).
- Scatter panel: Biome Scatter (biomes whose manifest carries a scatter recipe).

### Build Biome (the orchestrator)

Operator bob_blender_tools.world_apply_biome, panel label "Build Biome"
(BBT_PT_biome, bl_order 1, right after World). It stands up a whole biome on one terrain
mesh from the staged pick (world.biome). Target is the Scatter emitter if set, else the
active mesh. It runs each section the manifest carries, in order, and stops the chain if a
nested operator returns CANCELLED (it reports which step failed rather than reporting
success over a half-built scene):

1. terrain (if manifest.terrain): make the target active, call shaders_biome_terrain.
2. scatter (if manifest.scatter): call scatter_biome_scatter.
3. weathered assets (if world.biome_weather_assets, on by default): for each scatter kind,
   convert BOB_Assets_<Kind> to BobShaders (shaders_convert, scope collection).
4. world (if manifest.world): stage world.biome_world = biome, call world_biome_world.

The Biome panel also exposes the biome_weather_assets toggle and a separate "Biome World
only" section (world mood, no terrain or scatter) with a biome_build_sky toggle.

### Biome Terrain

Operator bob_blender_tools.shaders_biome_terrain, label "Biome Terrain". Builds a terrain
BobShader on the active mesh (get-or-create S_TerrainMaster wrapper, keeps tuned inputs).
For each manifest terrain layer it enables the L{i} slot and applies that layer key's
TERRAIN_LAYER_PRESETS knobs. Slots beyond the layer count are disabled.

The layers are solid tints blended by placement masks (slope/height). The current build
path does NOT wire per-layer image texture sets: it reports the count of layers whose
manifest names a "texture", but only solid tints are applied. The manifest "texture"
field is validated (the folder must exist under library/textures) and the S_TerrainMaster
material carries texture-set sockets and anti-tiling machinery, but the biome-terrain
build does not populate them. The blockout biome names no textures, so this is not visible
there.

### Biome Scatter

Operator bob_blender_tools.scatter_biome_scatter, label "Biome Scatter". For the active
Scatter emitter it builds one layer per scatter kind from the biome's recipe:

- For each kind in manifest.scatter that is a known LAYER_TYPES kind (not "empty"):
  ensure BOB_Assets_<Kind> proxies exist (make_proxies; only fills an empty collection,
  so it is idempotent), merge the biome cfg over the kind's LAYER_TYPES defaults
  (_biome_layer_params), then build the scatter recipe.
- Idempotent: an existing layer of the same kind is rebuilt in place by name (via
  build_geonodes), so re-running refreshes layers instead of stacking duplicates.

## Scatter and shading relationship

Scatter and shading are separate object-native subsystems that the biome composes.

Scatter: each layer is one object in a per-emitter scatter collection
(Object.bbt_scatter_coll). Structural config (kind, assets, align, curve binding, mask
group) lives on Object.bbt_scatter_layer; live knobs live on the layer modifier's inputs.
Two GN recipes back a layer:
- scatter: surface Poisson distribution, slope-filtered, with altitude / noise / paint /
  curve-band / camera-cull density masks that all multiply together.
- scatter_along: instances placed evenly ALONG a curve, projected down onto the emitter
  (used by the "Along curve" curve_mode).

Shading: scattered proxies instance real meshes with their own materials. Build Biome
turns those into first-class editable BobShaders so they weather with the world:

- Auto: with biome_weather_assets on, Build Biome calls shaders_convert (scope
  "collection") on each BOB_Assets_<Kind>. Convert routes each asset's own textures
  through S_SurfaceMaster and installs the bbt_env feed, so the scattered assets weather
  live. It is idempotent.
- Manual: the same shaders_convert operator (label "Convert to BobShader") on the Shaders
  panel, with scope active / all / selected / collection.

Do not use New BobShader on a scatter object: assigning a solid material via the
Set-Material modifier would override every instance's textures. Convert (Collection) is
the supported path, and the New operator refuses a scatter object.

AO and anti-tiling live in the shader masters (materials.py), not the biome data:
- AO Map socket on S_SurfaceMaster: Convert folds the glTF arm map's AO (R) channel into
  it (glTF drops occlusionTexture, so the packed AO would otherwise be unused). Identity
  1.0 = off.
- Macro break-up (anti-tiling): a low-frequency world noise modulating albedo brightness
  so a repeating texture stops reading as a tile at distance. Amount 0 = off.

## Where biome files live, and attribution

- library/models/<biome>/manifest.json     the biome (only blockout ships today).
- library/textures/<set>/                   shared terrain texture sets, referenced by a
                                            terrain layer's "texture" by folder name.

Shipped texture sets: grass, rock, soil. Each has basecolor / roughness / normal / height
/ ao maps and a SOURCE.txt naming the origin (Poly Haven CC0). Attribution for a biome
itself lives in the manifest meta block (source, license). There is no per-biome
CREDITS.md and no per-model attribution file, because the proxy biome ships no models.

## How to add a biome

1. Create library/models/<name>/ with a manifest.json.
2. Fill meta (at least name; add source/license). For a proxy biome set proxy: true and
   omit models.
3. Add a terrain block: layers using the keys soil/grass/rock/cliff/scree/sand. Omit
   "texture" for solid tints (the block-out look).
4. Add a scatter block: one entry per kind (trees/rocks/plants/grass) with any of density,
   scale, min_normal_z, max_normal_z, distance_min, align. Anything omitted falls back to
   LAYER_TYPES.
5. Add a world block: any of the bbt_env fields for the biome's mood.
6. The folder is picked up automatically (list_biomes scans for manifest.json). Run Build
   Biome; check the console for validate_biome warnings.

For a biome that ships texture sets, add a library/textures/<set>/ folder (with a
SOURCE.txt) and name it in a terrain layer's "texture". The validator checks the folder
exists. Note the current Biome Terrain build applies solid tints only and does not yet
wire the texture set.
