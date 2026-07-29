# BobBlenderTools API

The interfaces you build against. Four surfaces:

1. **Op vocabulary** — the JSON ops an agent sends over MCP (generated from the contracts).
2. **Core builder API** — the `core/` functions a recipe or panel author calls.
3. **Asset-pack spec** — the folder format a pack author ships.
4. **Extension surface** — the operators, scene props, and preferences the addon registers.

The op table in section 1 is generated from code by `tools/scripts/gen_api_docs.py`; the rest is
authored. Regenerate after changing the contracts:

```sh
uv run --project tools python tools/scripts/gen_api_docs.py
```

---

## 1. Op vocabulary (MCP)

An op is a JSON object with a string `op` tag plus fields. The vocabulary is the Pydantic models in
`blender/extensions/bob_blender_tools/mcp_agent/contracts.py` (validated in the agent-side server,
where agent input enters); each op's builder is the handler in
`blender/extensions/bob_blender_tools/core/dispatch.py`. Blender receives already-valid JSON, so its
bundled Python needs no extra deps.

A build request is `{output_file, ops: [...], base_file?}`; the result is
`{ok, output_file, results: [{op, created, info, data}], error}`. Send ops through the `build`
(headless) or `build_live` (open session) MCP tools.

`info` is a sentence for a human; `data` is the machine-readable result, and only the ops whose output
the next call needs fill it in. `export_control` returns `{path, height_m, scale, footprint, ...}` —
the path is what `comfy_mesh(control=...)` takes. `import_generated` returns the finished asset's
`lod_faces`, `uv_overlap`, `height_m`, `origin_above_base`, `master_type`, `maps`, `file` and
`warnings`, which is how to CHECK a generated asset instead of trusting it. Everywhere else it is `{}`.

`data` is a free-form dict in the contract (`mcp_agent/contracts.py`, `OpResult.data`), so a handler
may add keys without a schema change and without an `/mcp reconnect`; only a Reload Builders is
needed. The forest-barn gate spent that freedom, because the receipt it got was true and useless:
`warnings: []` on five meshes with holes in them. `import_generated` now also returns the openness
and the provenance of the mesh that actually shipped —

| Key | What it answers |
| --- | --- |
| `source_faces`, `source_boundary_edges` | the generated mesh before anything Bob did, welded so glTF's per-seam vertex split is not counted as holes |
| `low_boundary_edges`, `pinholes_closed`, `low_welded_verts` | the openness of the mesh that SHIPPED, and what the repair fixed on it |
| `simplify_source`, `uv_source` | whose topology and whose charts these are: `trellis2` / `trellis2_uvunwrap` means the generator's, which is the default route working as designed |
| `welded_verts`, `textured_faces`, `bake_rescale` | the frame and scale the bake read across |
| `bake_fidelity` | `{correlation, mean_abs_diff, coverage}` of the baked basecolor against the texture it came from, in-chart |

Every one of those was computed and then dropped before the bridge. Two of them are now also
`warnings` rather than only figures: a solid kind shipping open, and a colour bake that returned
something other than its source.

<!-- BEGIN GENERATED: op-vocabulary (tools/scripts/gen_api_docs.py) -->

| Op | Handler | Fields (type, default) |
| --- | --- | --- |
| `add_mesh` | `mesh.add_mesh` | `kind`: 'cube' \| 'uv_sphere' \| 'ico_sphere' \| 'cylinder' \| 'cone' \| 'plane' \| 'torus' \| 'grid' = `'cube'`<br>`name`: str \| None = `None`<br>`location`: tuple = `(0.0, 0.0, 0.0)`<br>`size`: float = `2.0` |
| `build_geonodes` | `geonodes.build_geonodes` | `recipe`: str = `'wave_grid'`<br>`name`: str \| None = `None`<br>`params`: dict = `{}`<br>`target`: 'new_object' \| 'library' = `'new_object'`<br>`mark_asset`: bool = `False`<br>`reset`: bool = `False` |
| `make_proxies` | `proxies.make_proxies` | `kinds`: list = `['trees', 'rocks', 'plants']` |
| `make_path` | `path_curve.make_path` | `name`: str = `'Path'`<br>`points`: list = `[]`<br>`resolution`: int = `12`<br>`heightmap`: str \| None = `None`<br>`size`: float = `60.0`<br>`height`: float = `14.0`<br>`sea_level`: float = `0.3` |
| `drape_curve` | `path_curve.drape_curve` | `name`: str = `'Path'`<br>`terrain`: str \| None = `None`<br>`heightmap`: str \| None = `None`<br>`size`: float \| None = `None`<br>`height`: float \| None = `None`<br>`sea_level`: float \| None = `None`<br>`monotonic`: bool = `False`<br>`min_slope`: float = `0.0`<br>`to_sea`: bool = `False`<br>`densify`: int = `0` |
| `reload_image` | `images.reload_image` | `path`: str \| None = `None` |
| `build_sky` | `world.build_sky` | `params`: dict = `{}` |
| `add_camera` | `camera.add_camera` | `name`: str = `'BOB_Camera'`<br>`location`: tuple = `(12.0, -12.0, 8.0)`<br>`look_at`: tuple \| None = `None`<br>`rotation`: tuple = `(0.0, 0.0, 0.0)`<br>`lens`: float = `50.0`<br>`clip_end`: float \| None = `None`<br>`set_active`: bool = `True` |
| `render` | `render.render` | `output`: str = **required**<br>`engine`: 'BLENDER_EEVEE' \| 'CYCLES' = `'BLENDER_EEVEE'`<br>`samples`: int = `64`<br>`resolution`: tuple = `(1920, 1080)`<br>`resolution_percentage`: int = `100`<br>`camera`: str \| None = `None`<br>`device`: 'GPU' \| 'CPU' = `'GPU'`<br>`file_format`: str = `'PNG'`<br>`release_gpu`: bool = `True` |
| `delete` | `scene.delete` | `names`: list = `[]`<br>`name`: str \| None = `None` |
| `clear_scene` | `scene.clear_scene` | `keep`: list = `[]`<br>`purge`: bool = `True` |
| `set_env` | `scene.set_env` | `params`: dict = `{}`<br>`apply`: bool = `True` |
| `apply_world` | `scene.apply_world` | (none) |
| `describe_scene` | `describe.describe_scene` | `objects`: list \| None = `None`<br>`include`: list \| None = `None` |
| `shade_terrain` | `shading.shade_terrain` | `object`: str = **required**<br>`stack`: str \| None = `None`<br>`layers`: list \| None = `None`<br>`material`: str \| None = `None`<br>`assign`: bool = `True` |
| `apply_shader` | `shading.apply_shader` | `object`: str = **required**<br>`master`: 'surface' \| 'terrain' \| 'water' = `'surface'`<br>`preset`: str \| None = `None` |
| `snow_shell` | `shading.snow_shell` | `object`: str = **required** |
| `apply_texture_set` | `shading.apply_texture_set` | `set`: str = `''`<br>`object`: str \| None = `None`<br>`material`: str \| None = `None`<br>`index`: int = `0`<br>`pack_dir`: str \| None = `None` |
| `import_generated` | `gen_assets.import_generated_op` | `kind`: str = `'rocks'`<br>`staged`: dict \| None = `None`<br>`name`: str \| None = `None`<br>`height_m`: float = `2.0`<br>`faces`: int = `4000`<br>`lods`: list \| None = `None`<br>`hero`: bool = `False`<br>`pack_dir`: str \| None = `None`<br>`cleanup`: bool = `True` |
| `export_control` | `gen_assets.export_control_op` | `object`: str = **required**<br>`out_file`: str \| None = `None`<br>`points`: int = `8192`<br>`pack_dir`: str \| None = `None` |
| `apply_biome` | `biome.apply_biome` | `object`: str = **required**<br>`biome`: str = **required**<br>`assign`: bool = `True`<br>`weather_assets`: bool = `True`<br>`world`: bool = `True`<br>`curve_mode`: 'scatter' \| 'clear' \| 'keep' \| None = `None` |
| `world_biome` | `biome.world_biome` | `biome`: str = **required** |
| `build_clouds` | `atmosphere.build_clouds` | `object`: str = `'BOB_Clouds'`<br>`cloud_shadows`: bool = `True` |
| `build_fog` | `atmosphere.build_fog` | `object`: str = `'BOB_Fog'`<br>`mode`: 'height_fog' \| 'noise_fog' \| 'ground_fog' = `'height_fog'`<br>`heightmap`: str = `''`<br>`preset`: str \| None = `None`<br>`density`: float \| None = `None` |
| `build_rain` | `atmosphere.build_rain` | `object`: str = `'BOB_Rain'`<br>`camera`: str \| None = `None`<br>`motion_blur`: bool = `True`<br>`preset`: str \| None = `None` |
| `build_motes` | `atmosphere.build_motes` | `object`: str = `'BOB_Motes'`<br>`camera`: str \| None = `None`<br>`motion_blur`: bool = `True`<br>`preset`: str \| None = `None` |
| `build_snow_cover` | `atmosphere.build_snow_cover` | `object`: str = **required** |
| `apply_season` | `atmosphere.apply_season` | `season`: str \| None = `None`<br>`build_snow`: bool \| None = `None`<br>`season_sets_date`: bool \| None = `None` |
| `scene_preset` | `atmosphere.scene_preset` | `look`: str = **required** |
| `make_curve` | `splines_build.make_curve` | `name`: str = `'Path'`<br>`role`: 'dirt_path' \| 'trail' \| 'road' \| 'river' \| 'stream' = `'dirt_path'`<br>`points`: list = `[]`<br>`terrain`: str \| None = `None`<br>`shape`: CurveShape \| None = `None` |
| `curve_build` | `splines_build.curve_build` | `curve`: str = **required**<br>`terrain`: str \| None = `None`<br>`do_terrain`: bool \| None = `None`<br>`do_material`: bool \| None = `None`<br>`do_water`: bool \| None = `None`<br>`do_scatter`: bool = `False`<br>`shape`: CurveShape \| None = `None` |
| `bake_erode` | `splines_build.bake_erode` | `terrain`: str = **required**<br>`curves`: list \| None = `None`<br>`strength`: float = `0.5`<br>`scope`: 'band' \| 'global' = `'band'`<br>`deposit`: bool = `True`<br>`seed`: int = `0` |
| `revert_erode` | `splines_build.revert_erode` | `terrain`: str = **required**<br>`curves`: list \| None = `None` |

_Registry-only (dispatch handlers with no contract model, not exposed to the MCP op union): `inspect_river`._

<!-- END GENERATED -->

**`CurveShape`** (the `shape` field on `make_curve` and `curve_build`) is a flat dict of optional
floats: `width`, `depth`, `falloff`, `taper`, `shoulder`, `bank_slope`, `bank_bias`, `bank_height`,
`width_var`, `water_level`, `flow`, `foam_bank`, `foam_rapids`, `wave_amp`, `wave_len`, `wave_steep`,
`wave_speed`, `wave_chop`, `verge_gap`, `verge_width`, `verge_side`. Every key is dumped whatever the
caller set, and **`None` means "not asked for"** — the Blender side skips it rather than writing a
zero. Values out of range are clamped by the property definitions. See
[SPLINES.md](SPLINES.md#8-driving-from-mcp) for what each one shapes.

**`location` and `collection` are `params`, not op fields.** Every recipe accepts them and
`build_geonodes` acts on them itself, on a rebuild as well as a first build:
`{"params": {"location": [-5, 29, -4.1]}}` stands the build somewhere, and
`{"params": {"collection": "BOB_Assets_Trees"}}` puts it in a scatter pool instead of the scene.
They live in `params` rather than in the contract so a recipe that needs neither is unaffected, and
so an agent gains both without a reconnect. See
[SYSTEMS.md](SYSTEMS.md#where-a-build-lands-location-and-collection).

**The one read-only op is `describe_scene`.** Everything else mutates. Its result is entirely in
`data`, and it is the way to read a scene back rather than guessing and rendering a probe frame:
see [MCP.md](MCP.md#reading-the-scene-back).

### Adding an op

1. Add a Pydantic model to `contracts.py` and include it in the `Operation` union.
2. Add a builder in `core/` that takes the op dict and mutates the scene, returning
   `{"op", "created": [...], "info"}`.
3. Register it: one line in `core/dispatch.py` `_HANDLERS`.
4. Reconnect the MCP server (the venv parses contracts once at startup): `/mcp reconnect
   bobblendermcp` or restart the CLI. A body-only change to an existing recipe needs just **Reload
   Builders** (Advanced panel); a new/changed op contract needs the reconnect too.
5. `uv run --project tools python tools/scripts/gen_api_docs.py` to refresh this table.

---

## 2. Core builder API

`bob_blender_tools.core` is the single bpy-side builder library, reached one way: relative imports
inside the extension (`from ..core.dispatch import apply_op`), and from the headless runner by one
`sys.path` insert of the extensions dir plus `from bob_blender_tools.core... import`. Every module
is bpy-side; `assets` and `heightfields` are the bpy-free exceptions (pure I/O / numpy).

### Dispatch

- `dispatch.apply_op(op: dict) -> dict` — the one entry both executors call. Looks up the op tag in
  `_HANDLERS`, forces object mode, runs the builder, returns its result dict. Raises `ValueError`
  on an unknown op.

### Materials (`core.materials`, a package re-exporting its submodules)

The BobShader system: shared `S_<Effect>` node groups instanced by thin `M_<name>` wrappers.
Split on coupling seams into `shared` / `volumes` / `weather` / `water` / `terrain` / `surface`,
re-exported from `__init__` so callers keep `from ..core.materials import X`.

- `surface_material(name)`, `terrain_material(name, ...)`, `water_material(name)` — get-or-create a
  wrapper material of each master kind (re-Build keeps tuned inputs).
- `new_bobshader(obj, master="surface"|"terrain"|"water")` — create + assign a wrapper to an
  object (GN-aware). The factory that dispatches the three kinds.
- `bobshade_material(mat, variation=0.15)` — convert an existing material in place, routing its
  Principled maps through `S_SurfaceMaster` (weather + per-instance variation, alpha/normals kept).
- `master_type(mat) -> 'surface'|'terrain'|'water'|None`, `is_bobshader(mat)` — native identity,
  read off the wrapper's `Master` group node.
- `assign_material(obj, mat)` — assign GN-aware (drives a `BBT_Material` Set-Material modifier for
  GN meshes; the object slot otherwise).
- `apply_curve_surface(mat, base_color, ...)`, `apply_curve_wet(mat, wetness)` — configure a
  terrain BobShader layer as a curve-driven band (BobSplines).
- Group builders (cached, versioned): `env_state_group()`, `weather_group()`,
  `surface_master_group()`, `terrain_master_group()`, `water_master_group()`. Volume/particulate
  shaders: `cloud_volume_material()`, `fog_volume_material()`, `rain_material()`, `mote_material()`.
- Constants: `SNOW_TEMP_FULL`, `MAX_TERRAIN_LAYERS`, `SURFACE_MASTER` / `TERRAIN_MASTER` /
  `WATER_MASTER`.

### World state (`core.env`)

- `get_env(scene=None) -> bbt_env | None` — the canonical world state (time/season/weather/wind/
  snow/...); None-guarded so a standalone caller falls back to its own defaults.
- `sun_params(env) -> dict` — the geographic-sun inputs extracted from the world state.
- `stamp_snow_bounds(scene, obj)` — record an object's Z range for the snow-line mapping.
- `register_geo_hook(fn)` / `unregister_geo_hook(fn)` — subscribe to world changes.

### Geometry-node recipes (`core.geonodes`)

- `build_geonodes(op) -> dict` — build a named recipe into a node group and place it
  (op: `recipe`, `name?`, `params`, `target`, `mark_asset`, `reset`).
- `build_geonodes_on_object(obj, recipe, params)` — attach a recipe modifier to an existing object.
- Recipe contract: a `build(ng, out, params)` function registered with `@recipe("name")`, imported
  in `recipes/__init__.py` so its decorator runs. `recipes.names()` lists them, `recipes.get(name)`
  fetches one. Recipes: `wave_grid`, `heightmap_terrain`, `terrain`, `scatter`, `scatter_along`,
  `curve_overlay`, `curve_water`, `volumetrics`, `particulates`, `snow`, `snow_shell`, `foliage`.
- `place(ng, name, target, mark_asset, location, collection)` — the object half. `location` puts the
  build somewhere other than the origin; `collection` names the collection it joins, CREATING IT
  UNLINKED if it does not exist, which is what fills an off-scene scatter pool. Both are read by
  `build_geonodes` out of `params` (see below).

### Terrain compute (`core.heightfields`, bpy-free)

The single committed copy of the terrain compute (numpy CPU / CuPy GPU, needs scipy).

- `bake(out_path, params, force=False, preview=False) -> dict` — evaluate an op stack to a 16-bit
  PNG + reproducibility sidecar; returns stats/metadata. `build_params(knobs)` expands the panel
  knobs into a full stack. `available()` lists backends; `select(name)` picks one.
- `io.to_png16(field, path)` / `io.read_png16(path)` — the pure-numpy(+zlib) 16-bit grayscale PNG
  codec (no PIL, no bpy).

### Assets (`core.assets`, bpy-free)

The asset-pack resolver — see section 3.

---

## 3. Asset-pack spec

Art lives outside the repo, in packs. A pack is a plain folder (optionally a `.zip`):

```
forest-scandinavia/
  pack.json                      <- the root manifest (optional)
  models/<biome>/manifest.json   <- biome definitions
  textures/<set>/                <- grass_basecolor.jpg, grass_normal.png, ...
```

A pack ships biomes and texture sets. It does NOT ship skies/HDRIs: the world is procedural
(Firmament's `build_sky` builds a physical MULTIPLE_SCATTERING sky + sun from `bbt_env`), so there
is no environment-texture to bring.

`pack.json`:

```json
{
  "schema": 1,
  "id": "forest-scandinavia",
  "name": "Scandinavian Forest",
  "version": "1.0.0",
  "author": "…",
  "license": "CC0",
  "provides": { "biomes": ["birch_glade"], "texture_sets": ["grass","rock"] }
}
```

### Resolution (`core.assets`)

- `asset_roots()` — the ordered, existing, de-duplicated search path: (1) `$BOB_ASSET_PACKS`
  (os.pathsep-separated), (2) the addon-preference **Asset Pack Folders**, (3) the dev repo
  `library/` when in-repo, (4) the bundled block-out pack inside the extension (always the floor).
- `biome_dir(name)`, `texture_set_dir(name)`, `list_biomes()` — resolve over the search path,
  **first pack wins** on a name collision.
- `read_pack(root)` / `list_packs()` — read `pack.json` (synthesizes a minimal one when absent).
- `set_pref_roots(paths)` — the addon pushes the preference folders in (assets stays bpy-free).

### Biome manifest

`models/<biome>/manifest.json`, read through `biome_manifest(name)` which normalizes to v2:
`{meta, models, terrain, scatter, world}`.

- `meta` — `name`, `description`, `climate`, `source`, `license`, `version`, optional `proxy: true`
  (a block-out biome: geometry from `core.proxies`, no external files).
- `terrain` — `{"layers": [{"layer": "<preset>", "texture"?: "<set>"}]}`. A layer with no `texture`
  is a solid tint.
- `scatter` — `{kind: {density, scale, min_normal_z, align, ...}}` (kinds: trees/rocks/plants/grass).
- `world` — a `bbt_env` preset (season/weather/time_of_day/...).
- `models` — legacy/back-compat only (no importer; scatter uses block-out proxies).

`validate_biome(name)` returns human-readable warnings (unknown layer key, missing texture set,
unknown world field, malformed scatter). **Rescan Asset Packs** (Advanced panel) refreshes the
biome enums after editing pack folders.

---

## 4. Extension surface

One addon, one `BobBlenderTools` N-panel tab. Names follow the umbrella brand: operators
`bob_blender_tools.*`, classes `BBT_*`, scene props `bbt_*`.

### Panels (pipeline order via `bl_order`)

World (0), Biome (1), Terrain (2), Paths (3), Scatter (4), Foliage (5), Shaders (6), Atmosphere (7),
Advanced (8, collapsed: the MCP bridge, Reload Builders, Rescan Asset Packs). Internally: `ui/`
holds the panels (`world`, `firmament`, `foliage`, `scatter`, `shaders`, `splines`, shared
`helpers`), `bridge/` the socket `server`, `core/` the builders; `__init__.py` is thin (register,
prefs, terrain-bake ops).

### Scene state (`bbt_*` PointerProperties)

`bbt_world` (World master: Live Environment, Quality), `bbt_env` (the shared world state, owned by
Firmament), `bbt_hf` (terrain/heightfield), `bbt_curves` (Paths), `bbt_scatter`, `bbt_shaders`,
`bbt_firmament`. Identity is native (the active object / material / layer), not a stored name.

### Key operators (`bob_blender_tools.*`)

- Terrain: `bake_terrain` (bake + build in-process), `detect_backends`, `enable_compute`
  (install scipy/CuPy into Blender's Python), `hf_apply_preset`, `terrain_op_add/remove/move`.
- World/Biome: `world_apply_biome` (Build Biome), `world_biome_world`, `firmament_scene_preset`,
  `firmament_apply_season`.
- Paths: `curve_add/remove/duplicate`, `curve_build`, `curve_build_all`, `curve_bake_erode`.
- Scatter: `scatter_add`, `scatter_build_active/all`, `scatter_biome_scatter`,
  `scatter_make_proxies`.
- Shaders: `shaders_new`, `shaders_convert`, `shaders_terrain_add`, `shaders_biome_terrain`,
  `shaders_snow_shell_add/remove`.
- Atmosphere: `firmament_build_sky/clouds/fog/rain/motes/snow_cover` and their `*_preset` loaders.
- Advanced/assets: `start`, `stop`, `reload_builders`, `rescan_packs`, `asset_pack_add/remove`.

### Add-on preferences

`autostart` (MCP bridge on launch, default OFF), `asset_packs` (the Asset Pack Folders list) +
`asset_packs_active`, `output_folder` (where bakes are written; empty = beside the `.blend`, else a
per-user cache).
