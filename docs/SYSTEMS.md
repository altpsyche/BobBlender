# Systems reference

Parameter and usage reference for the geometry-node recipes and the erosion
pipeline. For the design and layout, see `ARCHITECTURE.md`.

## How building works

An agent (or a script) sends ops. A geometry op looks like:

```json
{"op": "build_geonodes", "recipe": "terrain", "name": "Terrain",
 "params": {"height": 14, "seed": 3}, "target": "new_object"}
```

`build_geonodes` makes a node group, fills it from a recipe, and puts it on a new
object with a Geometry Nodes modifier. Two kinds of parameter:

- Live knobs: most numeric params become group inputs, editable on the modifier
  (3D View sidebar, or Modifier Properties) without rebuilding.
- Build-time params: things that change the graph or reference datablocks
  (emitter, assets, align, material, heightmap). Changing these means rebuilding.

Ops reach Blender two ways: `build` (headless, writes a .blend) and `build_live`
(into the open session, via the Bob Blender MCP extension). After editing recipe
code, press Reload Builders in the BobMCP panel so the live bridge reloads it.

## terrain (recipe: `terrain`)

A fully procedural, live terrain. How it works: a grid, displaced in Z by a
height field. The field is a low-frequency Shape (the big landforms) plus a
domain-warped fractal Detail that is blended toward ridged and rides on the
Shape, so high ground is craggy and low ground is smoother. Sea Level sets where
the surface crosses z = 0; Height scales the relief.

| Param | Default | Live | What it does |
|-------|---------|------|--------------|
| `size` | 60 | yes | Grid width in metres. |
| `resolution` | 400 | yes | Grid subdivisions per side. Higher = more detail, heavier. |
| `height` | 14 | yes | Vertical scale of the relief. |
| `sea_level` | 0.35 | yes | Field value that maps to z = 0. Higher sinks more into valleys. |
| `shape_scale` | 0.025 | yes | Frequency of the big landforms. Lower = larger features. |
| `scale` | 0.11 | yes | Frequency of the detail noise. |
| `detail` | 9 | yes | Fractal octaves. More = finer structure. |
| `roughness` | 0.55 | yes | How much each finer octave contributes. |
| `warp` | 6 | yes | Domain-warp strength. Organic, flowing distortion. |
| `warp_scale` | 0.05 | yes | Frequency of the warp. |
| `ridged` | 0.6 | yes | 0 rolling hills, 1 sharp mountain ridges. |
| `detail_strength` | 0.7 | yes | How strongly detail rides on the shape height. |
| `seed` | 0 | yes | Changes the whole terrain. |

Tips: raise `ridged` for mountains, `warp` for organic shapes, lower `scale` and
`shape_scale` for bigger features, move `sea_level` for the waterline.

## Eroded terrain (recipe: `heightmap_terrain` + `bobtools.erosion`)

Higher quality than the live `terrain`, because erosion is simulated in numpy.
Two parts:

1. The venv generates and erodes a heightmap PNG.
2. `heightmap_terrain` displaces a grid by that image in Blender.

### Generating and eroding the heightfield (venv)

`tools/bobtools/erosion.py`. Base generation is smooth scipy noise plus domain
warp, ridged blend, and shape composition. Erosion alternates stream-power
incision (carves valleys along drainage) with thermal slumping (talus slopes).

`generate_base(size, seed, octaves, roughness, ridged, warp, detail_strength)`:

| Param | Default | What it does |
|-------|---------|--------------|
| `size` | required | Heightmap resolution in pixels (square). |
| `seed` | 0 | Changes the terrain. |
| `octaves` | 8 | Fractal detail. |
| `roughness` | 0.55 | Finer-octave contribution. |
| `ridged` | 0.6 | 0 smooth, 1 ridges. |
| `warp` | size/22 | Domain-warp strength in pixels. |
| `detail_strength` | 0.7 | Detail riding on the shape. |

`erode(h, iterations, rain, erosion, m, n, talus, thermal_factor)`:

| Param | Default | What it does |
|-------|---------|--------------|
| `iterations` | 35 | More = deeper carving, slower. |
| `erosion` | 0.6 | Incision strength. Higher carves harder. |
| `m` | 0.9 | Drainage-area exponent (how much big rivers cut). |
| `n` | 1.1 | Slope exponent. |
| `talus` | 0.008 | Slope steeper than this slumps (thermal). |
| `thermal_factor` | 0.35 | Thermal strength. High values smooth away valleys. |

To change the heightfield, edit the params or seed and regenerate:

```python
from bobtools import erosion
base = erosion.generate_base(512, seed=7, ridged=0.5)
h = erosion.erode(base, iterations=70, erosion=1.6, thermal_factor=0.10)
erosion.to_png16(h, "library/_generated/forest_height.png")
```

Then rebuild `heightmap_terrain` pointing at that file.

### Displacing it in Blender (recipe: `heightmap_terrain`)

| Param | Default | Live | What it does |
|-------|---------|------|--------------|
| `heightmap` | required | no | Absolute path to the heightmap image. |
| `size` | 60 | yes | Grid width in metres. |
| `resolution` | 512 | yes | Grid subdivisions. Match or exceed the heightmap for full detail. |
| `height` | 14 | yes | Vertical scale. |
| `sea_level` | 0.3 | yes | Height value mapped to z = 0. |
| `material` | none | no | Name of a material to assign to the surface. |

## scatter (recipe: `scatter`)

A GScatter-style layer, one per asset type. How it works: reads the Emitter
object's surface, filters faces by slope, distributes points with Poisson
sampling, then instances a random pick from the Assets collection with random
scale and Z rotation. Trees can stand upright; rocks can tilt to the surface.

| Param | Default | Live | What it does |
|-------|---------|------|--------------|
| `emitter` | none | no | Name of the object to scatter on (usually the terrain). |
| `assets` | none | no | Name of the collection to instance. |
| `align` | `up` | no | `up` keeps instances upright (trees); `normal` tilts to the surface (rocks, grass). |
| `density` | 5.0 | yes | Points per square metre (before the distance limit). |
| `distance_min` | 0.3 | yes | Minimum spacing between instances. |
| `seed` | 0 | yes | Reshuffles positions, scale, rotation, and pick. |
| `min_scale` | 0.8 | yes | Smallest per-instance scale. |
| `max_scale` | 1.2 | yes | Largest per-instance scale. |
| `min_normal_z` | 0.5 | yes | Slope cutoff. 1 = flat only, 0 = any slope. |

Replacing assets: point `assets` at your own collection, or edit the contents of
the `BOB_Assets_<Kind>` collection the scatter already uses. Nothing in the graph
changes; the instances update.

## Proxies (op: `make_proxies`)

Block-out stand-ins so a scatter works before you bring real assets.

```json
{"op": "make_proxies", "kinds": ["trees", "rocks", "plants"]}
```

Creates collections `BOB_Assets_Trees`, `BOB_Assets_Rocks`, `BOB_Assets_Plants`
(a canopy-on-trunk tree, rocks, and shrubs, each with a simple material). The
collections are not linked to the scene, so the proxies appear only as scattered
instances. Replace them by editing a collection's contents.

## wave_grid (recipe: `wave_grid`)

A demo recipe: a grid whose Z ripples as sin(distance-from-centre * Frequency) *
Amplitude. Live params: Size, Resolution, Amplitude, Frequency.

## Adding or changing a recipe

Recipes live in `blender/bbmcp/geonodes/recipes/`, one file each, composed from
the shared blocks in `blocks.py`. See `blender/README.md` for the how-to.
