# BobShaders

The authored surface-material system. It gives every object a strong, art-directable
material and makes that material obey the shared world state (snow, wet, frost, season,
temperature). This doc describes what the code does today. The code is the source of truth:
`blender/bbmcp/materials.py` (the material engine, all node-group builders) and
`blender/extensions/bob_blender_tools/shaders_panel.py` (the Shaders N-panel).

Naming follows `CONVENTIONS.md`: shared shader node groups are `S_<Effect>`, wrapper
materials are `M_<Surface>`. The auto-cached Firmament volume/particulate materials keep the
generated `BOB_` namespace.

## What a BobShader is

A BobShader is a thin wrapper material whose node tree carries one group node literally named
`Master`, whose tree is one of the three shared master groups. `master_type(mat)` returns the
kind by reading that node:

- `S_SurfaceMaster` gives `"surface"`
- `S_TerrainMaster` gives `"terrain"`
- `S_WaterMaster` gives `"water"`
- anything else, or no `Master` node, gives `None`

That is the whole identity. There is no stored material name or target pointer. The panel
edits the material on the active object's active material slot; a slot's kind is detected from
the datablock. `is_bobshader(mat)` is `master_type(mat) is not None`.

A wrapper is one `Master` group node feeding one Principled BSDF feeding the Output
(`_build_wrapper`). The master's outputs Base Color / Roughness / Metallic always drive the
BSDF; a water master also outputs Transmission / IOR / Alpha / Normal, which `_build_wrapper`
wires when the master exposes them (`_WRAPPER_EXTRA_OUTPUTS`, no-op for surface/terrain). The
"instance parameters" are that node's input socket values, drawn and edited live in the panel
(shader group inputs re-evaluate on edit, so there is no rebuild to preserve them).

Wrappers are get-or-create and cached by name, so a re-New or re-Build never wipes tuned
inputs. New BobShaders auto-name `M_<object>`. A structural change (currently only the terrain
flow/wetness map wiring) rebuilds the wrapper and snapshots then restores the `Master` node's
tuned inputs by socket name (`_snapshot_group_inputs` / `_restore_group_inputs`), keyed off a
`bbt_sig` signature so an unchanged call is a no-op.

## Material engine architecture

The stack, top to bottom:

    M_<name>          wrapper: one Master group node into a Principled BSDF into Output
    S_*Master         S_SurfaceMaster / S_TerrainMaster / S_WaterMaster, each ends in S_Weather
    S_Weather         the shared weather layer (snow, wet, frost, dust, moss)
    S_EnvState        the world-to-shader bridge, driven once from scene.bbt_env

All shared groups are version-stamped and get-or-create (`_cached_group`): a group cached in an
older .blend with a stale `bbt_ver` is rebuilt in place so materials referencing it pick up the
new interface. The shared version is `S_GROUP_VER` (currently 3); `_GROUP_VER_OVERRIDE` versions
a single group so scoped changes rebuild only it (`S_WaterMaster` is at 6). Rebuilding a group
clears its interface and resets tuned inputs of every material instancing it, which is why
water-only changes use the per-group override.

`materials.py` also holds Firmament's volume and particulate materials in the `BOB_` namespace
(`BOB_CloudVolume`, `BOB_FogVolume`, `BOB_GroundFog_*`, `BOB_Rain`, `BOB_Mote`). Those shade one
effect each and are documented in `FIRMAMENT.md`; they are not BobShaders and are not edited from
the Shaders panel.

### S_EnvState, the world bridge

`env_state_group()` builds one shared group with no inputs and three outputs: Snow, Wetness,
Temperature. It holds four internal Value nodes (`env_snow`, `env_wetness`, `env_temperature`,
`env_weather`), one per `ENV_STATE_DRIVERS` entry. The panel installs one SINGLE_PROP driver per
node reading `scene.bbt_env.<field>` (`_install_env_drivers`). Because a node group is one
datablock shared by every material that instances it, driving it once feeds every surface.

The one convergence spot for `env.weather`: inside S_EnvState effective Wetness =
`max(env.wetness, weather_contribution)`, where the weather enum in {rain (index 3), storm
(index 4)} raises wetness (rain 0.6, storm 1.0) and clear/cloud/fog do not. Snow and Temperature
pass through raw.

When Firmament is absent no driver is installed and the Value defaults stand (snow 0, wetness 0,
temperature 15), so a material still renders standalone and water never freezes on its own. The
master Live Environment toggle lives on the World panel (`bbt_world.live_env`); Shaders subscribes
`_apply_world` to the world applier registry, so toggling it installs or removes the shared drivers
with no rebuild (`_apply_world` / `_remove_env_drivers`).

### S_Weather, the shared weather layer

`weather_group()` ends every master. It takes Base Color / Roughness / Metallic in and out, plus
the weather knobs, and applies terms in order on (albedo, roughness, metallic):

1. Dust (up-facing) and moss (down-facing), continuous amounts set by season on apply.
2. Wetness: darken albedo and drop roughness. The wet factor is the MAX of three sources -
   uniform env wetness (`Wetness Strength`), Cycles cavity pooling (`Wet Pooling`, off a
   Pointiness cavity term), and the baked terrain map (`Wetness Map` * `Terrain Wetness`).
3. Snow: whiten albedo, soften roughness, drop metallic by the coverage factor.
4. Frost: below freezing, on up-facing faces, a cool blue-white sheen.

Snow coverage has one authority. Where the Firmament GN pass ran (the terrain) S_Weather reads
the `snow_cover` attribute (Geometry Attribute node, POINT domain). Everywhere else (scattered
assets, plain meshes) it computes a shader-side fallback with the same formula pinned in
`SYSTEMS.md`:

    slope_mask    = smoothstep(normalZ, Slope Threshold - Slope Falloff, Slope Threshold)
    altitude_mask = smoothstep(worldZ,  Altitude,        Altitude + Altitude Falloff)
    coverage      = Snow * slope_mask * altitude_mask

`Use Attribute` picks between them (0 computed, the default; 1 the terrain's `snow_cover`). The GN
occlusion term is omitted from the fallback (default 0), so the two paths are identical in the
fallback's domain.

## Shader catalog (the S_ / M_ groups in code)

Node groups (all in `materials.py`):

| Group            | Constant         | Builder                  | Role |
|------------------|------------------|--------------------------|------|
| `S_EnvState`     | `ENV_STATE`      | `env_state_group()`      | world-to-shader bridge |
| `S_Weather`      | `WEATHER`        | `weather_group()`        | shared weather layer |
| `S_SurfaceMaster`| `SURFACE_MASTER` | `surface_master_group()` | single-surface master |
| `S_TerrainMaster`| `TERRAIN_MASTER` | `terrain_master_group()` | multi-layer terrain master |
| `S_WaterMaster`  | `WATER_MASTER`   | `water_master_group()`   | water-surface master |

Wrapper materials are `M_<Surface>` (prefix `SURFACE_WRAPPER_PREFIX = "M_"`), built by
`surface_material(name)`, `terrain_material(name, ...)`, `water_material(name)`.

There is no `S_Triplanar`, `S_TextureSet`, or `S_MacroDetail` group in the code, and no
texture-set loader. The masters carry identity-default map input sockets (Albedo Map, Roughness
Map, Metallic Map, AO Map) but nothing wires an image-texture set into them except the Convert
path (below), which feeds a converted asset's own UV-mapped maps.

### S_SurfaceMaster

Single-surface master for props, rocks, vegetation. A solid Base Color plus scalar Roughness and
Metallic, with per-instance Variation (Object Info Random jitters brightness through an HSV Value,
so scattered copies differ). Base Color is a tint: albedo = varied base color * Albedo Map, then
macro break-up, then folded by AO Map. Ends in S_Weather.

Inputs: Base Color, Roughness, Metallic, Variation; the identity map sockets Albedo Map, Roughness
Map, Metallic Map, AO Map; Macro Amount, Macro Scale (anti-tiling); and the weather passthrough
(Snow Strength, Use Attribute, Slope Threshold, Slope Falloff, Altitude, Altitude Falloff, plus
`_WEATHER_EXTRA`: Wetness Strength, Wet Pooling, Frost Strength, Dust Amount, Moss Amount). Outputs
Base Color, Roughness, Metallic.

### S_TerrainMaster

Multi-layer terrain blend. `MAX_TERRAIN_LAYERS = 6` fixed slots; the stack is the enabled slots
(a disabled slot has `L{i} Enable` 0 and is never blended in), so add/remove/tune never rebuild
the graph.

Each layer's placement weight is the product of the SAME masks Scatter uses, each gated by a
strength (0 = off): slope band (Min/Max Normal Z, Slope Strength), altitude band (Height Min/Max,
Height Falloff, Height Strength), noise clumping (Noise Scale/Contrast/Seed/Strength, the identical
`ShaderNodeTexNoise` at world position the scatter recipe uses), a paint attribute
(`bbt_paint_L{i}`, Paint Strength), a Cycles Pointiness curvature term (Curvature Strength), a flow
band off the baked Flow Map (Flow Strength, Flow Threshold), and two curve bands off the curve
overlay masks (`bbt_curve_mask` / `bbt_curve_mask_b`: Curve Strength, Curve Hard, Curve B Strength,
Curve B Hard).

The blend is a HEIGHT-LERP, not a linear cross-fade: each layer builds a height field
H = weight + Height Bias + per-layer macro noise, and layers composite by picking the higher H per
texel within a soft `Blend Softness` band, so rock breaks through grass at a natural edge. Stacking
order is by Height Bias, not slot order (no reorder op). Curve Hard swings a layer's H off the raw
curve mask for a crisp road edge instead of the soft band.

A river/stream overlay writes `bbt_curve_wet` along its channel, MAX-accumulated into the Wetness
Map inside the group; `apply_curve_wet(mat)` raises Terrain Wetness (the multiplier that path is
gated by) so the bed and banks read damp. Global inputs: Blend Softness, Macro Amount, Macro Scale,
Flow Map. Outputs Base Color, Roughness, Metallic, and a Height (currently unconsumed by the
wrapper). Ends in S_Weather.

The terrain material samples baked drainage maps per-terrain when present:
`terrain_material(name, flow_image, wetness_image, terrain_size)` wires the `<base>_flow.png` /
`<base>_wetness.png` siblings of the heightmap (by object-space XY, UV = pos.xy/size + 0.5) into the
Flow Map / Wetness Map inputs. `terrain_material_for(obj)` and `_terrain_maps(obj)` gather those
from a built terrain. On a fresh terrain with drainage maps `_autoconfig_riverbed` enables a
wet-gravel layer keyed to high flow.

### S_WaterMaster

Water-surface master for the river/stream ribbons BobSplines lays (`docs/SPLINES.md`,
`docs/WATER-SHADER-HANDOVER.md`). It reads the ribbon's baked geometry attributes (`bbt_flow`,
`bbt_foam`, `bbt_shore`, `bbt_depth`, `bbt_water_uv`) and produces a flowing, depth-tinted,
foaming, transparent surface that freezes to ice below 0 C. It animates live off a frame-driven
Value node (no bake). Beyond Base Color / Roughness / Metallic it outputs Transmission, IOR, Alpha,
Normal. Like the others it ends in S_Weather; the freeze reuses the shared below-freezing frost
path plus a manual `Frozen` input.

Look inputs (`_WATER_LOOK`): Shallow Color, Deep Color, Depth, Depth Absorption, Depth Opacity,
Shoreline Fade, Water Roughness, IOR, Transmission, Edge Fade. Flow/foam inputs (`_WATER_FLOW`):
Flow Speed, Ripple Strength, Ripple Scale, Wave Detail, Surface Texture, Foam Color, Foam Amount,
Shore Foam, Foam Crispness. Freeze input (`_WATER_FREEZE`): Frozen.

Depth uses Beer-Lambert extinction off `bbt_depth` (deep water darker and more opaque). The visible
waves are geometry Gerstner in the curve recipe; the shader carries only high-frequency micro-detail
normals plus a flow-space UV detail normal, so it does not comb into hair streaks. `Depth Absorption`,
`Depth Opacity`, `Shoreline Fade` degrade gracefully to the old shore look on a pre-depth ribbon.

Water needs render flags to refract: `set_water_render_flags(mat)` (called inside `water_material`)
sets the material's EEVEE-Next raytraced refraction / transparency flags, and
`enable_eevee_refraction(scene)` turns on the scene-level ray tracing (called from the build path).
Both are no-ops in Cycles.

## Triplanar and anti-tiling

Triplanar projection and a texture-set loader are NOT in the code (they were in the plan and are
not implemented). What ships:

- Macro break-up (`_macro_break`): a low-frequency world noise modulating albedo brightness so a
  surface does not read as one flat sheet. On S_SurfaceMaster it is the Macro Amount / Macro Scale
  inputs; on S_TerrainMaster each layer's height field gets a per-layer macro noise (Macro Amount /
  Macro Scale global). Amount 0 = off.
- Per-instance variation: Object Info Random jitters brightness by +/- Variation on the surface
  master, so scattered copies differ.

Terrain and surface layers are solid-tint. The map input sockets exist so the Convert path can route
a converted asset's own textures through the master; nothing else feeds them.

## How shaders relate to biomes and scatter

Scatter. A scatter layer object's look comes from its instanced asset collection, not its own
slots, so New must not assign a solid material to it (that would add a Set-Material modifier
overriding every instance). Instead the Shaders panel edits the assets' materials through the
selectable layer (`_editing_material` / `_asset_materials`), and Convert (Collection scope) turns
the whole asset collection into BobShaders. `bobshade_material(mat)` converts an imported material
IN PLACE: it captures the Principled Base Color / Roughness / Metallic (a linked map or a value) and
routes them through S_SurfaceMaster as its map inputs (tint white, scalars 1, so the asset's own UV
maps read at face value), so the asset gains per-instance variation, macro break-up, and the full
weather layer while its Alpha, Normal, Emission stay untouched. AO from a packed ORM/arm map's R
channel is routed into AO Map (a deliberate heuristic, documented in `bobshade_material`). Convert is
idempotent (skips a material already carrying a `Master` node). Coverage on assets is the computed
path (they carry no `snow_cover` pass).

Biomes. `BBT_OT_shaders_biome_terrain` builds a terrain material for a biome whose manifest carries
a terrain spec (`assets.biome_terrain`): it enables and seeds the layer stack from the spec's layers
(mapped through `TERRAIN_LAYER_PRESETS`) and assigns it. The Biome panel builds the whole scene; this
operator does the terrain layers only.

Splines. The BobSplines panel drives the water and terrain masters through the material API:
`water_material(name)` for river/stream ribbons, `apply_curve_surface(mat, ...)` for a path/road
surface layer keyed to `bbt_curve_mask` / `bbt_curve_mask_b`, and `apply_curve_wet(mat)` for the
damp riverbed. These are the same masters and attributes described above.

GN-mesh assignment. A geometry-nodes-generated mesh (the terrain) ignores the object's material
slots, so `assign_material(obj, mat)` also drives a per-material Set-Material GN modifier
(`SET_MATERIAL_MOD = "BBT_Material"`, group from `_set_material_group`) kept last in the stack, so
Assign shades GN terrain and passes `snow_cover` through untouched.

## The panel and its operators

`BBT_PT_shaders` (BobBlenderTools tab, order 5) opens with the active-mesh context header, then
lists the mesh's material slots with adaptive New / Convert. Sub-panels are gated by the editing
material's master type.

Operators (idname prefix `bob_blender_tools.`):

| Operator | Class | Does |
|----------|-------|------|
| `shaders_new` | `BBT_OT_shaders_new` | create a BobShader (master: surface / terrain / water), auto-named `M_<object>`, on an empty slot |
| `shaders_convert` | `BBT_OT_shaders_convert` | convert plain material(s) to BobShader; scope active / all slots / selected / collection, or a per-row index |
| `shaders_select` | `BBT_OT_shaders_select` | pick the item the sub-panels edit; target slot / asset / layer |
| `shaders_preset` | `BBT_OT_shaders_preset` | set the surface look from `SURFACE_PRESETS` |
| `shaders_terrain_add` | `BBT_OT_shaders_terrain_add` | enable the next terrain layer slot, seed it |
| `shaders_terrain_toggle` | `BBT_OT_shaders_terrain_toggle` | enable/disable a terrain layer slot |
| `shaders_terrain_layer_preset` | `BBT_OT_shaders_terrain_layer_preset` | set the active layer from `TERRAIN_LAYER_PRESETS` |
| `shaders_terrain_stack_preset` | `BBT_OT_shaders_terrain_stack_preset` | set the whole stack from `TERRAIN_STACK_PRESETS` |
| `shaders_biome_terrain` | `BBT_OT_shaders_biome_terrain` | build a biome's terrain layer stack |
| `shaders_snow_shell_add` | `BBT_OT_shaders_snow_shell_add` | add the snow accumulation shell GN pass |
| `shaders_snow_shell_remove` | `BBT_OT_shaders_snow_shell_remove` | remove it |

New never silently converts: a slot holding a plain material must be turned into a BobShader with
Convert (which keeps its textures). Convert is the only place whole-object / selected / collection
scope lives (the scope dropdown), plus a per-row targeted Convert.

Sub-panels:

- Surface (`BBT_PT_shaders_surface`, poll surface): the preset row, then `_SURFACE_KNOBS`
  (Base Color, Roughness, Metallic, Variation) and Macro break-up (`_MACRO_KNOBS`: Macro Amount,
  Macro Scale). A scattered asset shows only the tint/rough/variation.
- Water (`BBT_PT_shaders_water`, poll water): depth colour + optics (`_WATER_LOOK`), with child
  panels Flow and foam (`_WATER_FLOW`) and Freeze (`_WATER_FREEZE`), both DEFAULT_CLOSED.
- Terrain Layers (`BBT_PT_shaders_terrain`, poll terrain): Stack Preset, Biome Terrain (when a
  biome carries a terrain spec), the global knobs (`_TERRAIN_GLOBAL`), the enabled layer slots
  (toggle + colour swatch + select), Add Layer, and the active layer's surface (`_LAYER_SURFACE`)
  with a Layer Preset. Child Layer Masks (`BBT_PT_shaders_terrain_masks`, DEFAULT_CLOSED): slope
  (`_LAYER_SLOPE`), altitude (`_LAYER_ALT`), noise (`_LAYER_NOISE`), paint/curvature
  (`_LAYER_OTHER`), flow band (`_LAYER_FLOW`), curve band (`_LAYER_CURVE`).
- Weather (`BBT_PT_shaders_weather`, poll any BobShader, DEFAULT_CLOSED): a "Firmament off"
  hint, then Snow (`_WEATHER_SNOW`: Snow Strength, Use Attribute, Slope Threshold, Slope Falloff,
  Altitude, Altitude Falloff), Wetness (`_WEATHER_WET`: Wetness Strength, Wet Pooling), Frost
  (`_WEATHER_FROST`: Frost Strength), Season aging (`_WEATHER_SEASON`: Dust Amount, Moss Amount),
  and the Snow Accumulation Shell box (Add / Remove + `_SHELL_KNOBS`: Thickness, Smooth).

Panel UI state is `Scene.bbt_shaders` (`BBT_ShadersProps`): terrain_active, convert_scope,
convert_collection, asset_material. The shared world state is `Scene.bbt_env` (owned by Firmament,
read-only here). The Live Environment master toggle is `bbt_world.live_env`.

### Presets (all Blender-side dicts in `shaders_panel.py`)

- `SURFACE_PRESETS`: rock, cliff, bark, soil, metal, painted, grass_blade. Each sets Base Color,
  Roughness, Metallic, Variation.
- `TERRAIN_LAYER_PRESETS`: soil, grass, rock, cliff, scree, sand. A surface plus the placement
  masks that put it there.
- `TERRAIN_STACK_PRESETS`: temperate, alpine, desert. An ordered set of layer presets plus a
  weather block (e.g. alpine raises Snow Altitude).

## Snow accumulation shell

A GN pass, not a material term. `shaders_snow_shell_add` builds the `snow_shell` geonodes recipe as
the `BOB_SnowShell` modifier (via `build_geonodes_on_object`), which displaces the surface by
`snow_cover` for real thickness and drifts, and moves the Set-Material modifier last so the shell
still shades. It reads the same `snow_cover` attribute the material reads, so shell thickness and
material whiteness line up; it needs the `BOB_Snow` coverage pass first (built in Atmosphere >
Snow Coverage) or it reads 0. Knobs Thickness, Smooth.

## Driving it

From the panel: select a mesh, New a surface / terrain / water BobShader (or Convert a plain one),
pick a slot, and tune the Master node's inputs live in the sub-panels. Presets set input groups in
one press. There is no rebuild step for tuning; only assigning terrain drainage maps is structural
(and it snapshots/restores tuned inputs).

Programmatically (the path the panel, splines, and headless verifies use): call the `bbmcp.materials`
functions in-process on a running Blender - `surface_material` / `terrain_material` / `water_material`
to build wrappers, `new_bobshader(obj, master)` to create-and-assign, `bobshade_material(mat)` to
convert, `master_type` / `is_bobshader` to detect, `assign_material` to shade GN meshes,
`env_state_group` plus panel driver install to feed the world.

There is no dedicated MCP op for BobShaders. It is panel-only and in-process, like Scatter; the
`make_material` entry in `dispatch.py` stays commented out. Over MCP you build the terrain (bake +
build) and the curves; the BobShaders materials are applied by the in-process material API above,
so a code change to the shader system needs an addon re-enable, never an MCP reconnect.
