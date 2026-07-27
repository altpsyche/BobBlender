"""The op contract: the vocabulary shared across the agent/Blender boundary.

Agent input is validated here (the MCP server has Pydantic). The Blender side receives
valid JSON and executes it, so Blender's bundled Python needs no extra deps.
Grow the vocabulary by adding op models to Operation.
"""

from typing import Annotated, Literal, Union

from pydantic import BaseModel, Field

Vector3 = tuple[float, float, float]


# Operations
class AddMesh(BaseModel):
    op: Literal["add_mesh"] = "add_mesh"
    kind: Literal[
        "cube", "uv_sphere", "ico_sphere", "cylinder", "cone", "plane", "torus", "grid"
    ] = "cube"
    name: str | None = None
    location: Vector3 = (0.0, 0.0, 0.0)
    size: float = 2.0


class BuildGeoNodes(BaseModel):
    op: Literal["build_geonodes"] = "build_geonodes"
    recipe: str = "wave_grid"  # a named recipe in core/geonodes/recipes/
    name: str | None = None
    params: dict = Field(default_factory=dict)  # recipe-specific, checked by the builder
    target: Literal["new_object", "library"] = "new_object"
    mark_asset: bool = False  # mark the node group as an Asset Browser asset
    reset: bool = False  # rebuild in place but discard tuned knobs, reapply params


class MakeProxies(BaseModel):
    op: Literal["make_proxies"] = "make_proxies"
    kinds: list[str] = Field(default_factory=lambda: ["trees", "rocks", "plants"])


class MakePath(BaseModel):
    op: Literal["make_path"] = "make_path"
    name: str = "Path"
    points: list[Vector3] = Field(default_factory=list)  # NURBS control points
    resolution: int = 12  # curve subdivisions per segment
    # Optional drape: sample this heightmap at the control points and set their Z
    # to the terrain surface, so the path grades smoothly instead of copying the
    # terrain's fine relief. Must match the heightmap_terrain build values.
    heightmap: str | None = None
    size: float = 60.0
    height: float = 14.0
    sea_level: float = 0.3


class DrapeCurve(BaseModel):
    op: Literal["drape_curve"] = "drape_curve"
    name: str = "Path"  # an existing curve object
    # Re-sample the terrain's heightmap at each control point and set its Z to the surface, in place,
    # so a hand-drawn or moved curve follows the current ground (the counterpart to make_path's
    # drape). `terrain` is the way to call this: the object records the four values it was built with
    # and they are read off it, so there is nothing to restate and nothing to get wrong. The explicit
    # keys still win when given (for a heightfield no terrain has been built from yet) and a value
    # that disagrees with the terrain's own record comes back in the result's warnings.
    terrain: str | None = None
    heightmap: str | None = None
    size: float | None = None
    height: float | None = None
    sea_level: float | None = None
    # River drape (docs/SPLINES.md 9 #1, the IMPOSE family): clamp the sampled Z into a monotonic
    # downhill profile from source to mouth so the water never runs uphill. min_slope forces a
    # gentle continuous fall through flats; to_sea pulls the mouth to sea level (absolute Z 0).
    # densify (>= 2) resamples the curve to that many points before sampling+solving so path_z
    # tracks the real valley (a coarse curve floats the water over dips); rebuilds as one NURBS.
    monotonic: bool = False
    min_slope: float = 0.0
    to_sea: bool = False
    densify: int = 0


class ReloadImage(BaseModel):
    op: Literal["reload_image"] = "reload_image"
    path: str | None = None  # absolute image path, or None to reload all


class BuildSky(BaseModel):
    op: Literal["build_sky"] = "build_sky"
    # Params are recipe-like and checked by the builder (core/world.py), the same
    # freeform pattern as build_geonodes. Keys: time_of_day, year, month, day,
    # utc_offset, latitude, longitude (geographic sun); use_override,
    # sun_elevation, sun_azimuth (manual sun in degrees); sun_strength, sun_angle,
    # sun_disc, sun_intensity; altitude, air, ozone, turbidity, ground_albedo
    # (the 5.2 MULTIPLE_SCATTERING sky); world_strength.
    # The geographic-sun inputs (time_of_day/date/place) DEFAULT from the shared world
    # state (Scene.bbt_env) when omitted, so a bare build_sky honours a prior set_env;
    # explicit keys here override, and use_override bypasses the solar model entirely.
    params: dict = Field(default_factory=dict)


class AddCamera(BaseModel):
    op: Literal["add_camera"] = "add_camera"
    name: str = "BOB_Camera"
    location: Vector3 = (12.0, -12.0, 8.0)
    # Aim at a point (camera looks down -Z, +Y up); overrides rotation when set.
    look_at: Vector3 | None = None
    rotation: Vector3 = (0.0, 0.0, 0.0)  # degrees, used when look_at is None
    lens: float = 50.0  # focal length in mm
    clip_end: float | None = None  # far clip; raise it for large terrains
    set_active: bool = True  # make this the scene camera


class Render(BaseModel):
    op: Literal["render"] = "render"
    output: str  # absolute image path the render writes (the tool resolves workdir-relative)
    engine: Literal["BLENDER_EEVEE", "CYCLES"] = "BLENDER_EEVEE"  # 5.2 enums
    samples: int = 64
    resolution: tuple[int, int] = (1920, 1080)
    resolution_percentage: int = 100
    camera: str | None = None  # camera object name, else the current scene camera
    device: Literal["GPU", "CPU"] = "GPU"  # Cycles only; EEVEE ignores it
    file_format: str = "PNG"
    # Drop Blender's render buffers and orphan datablocks after the frame is written (D15). On by
    # default because an agent that generates and renders in one session is the normal case and the
    # two halves fight over one card; False keeps them warm for a second render.
    release_gpu: bool = True


class Delete(BaseModel):
    op: Literal["delete"] = "delete"
    names: list[str] = Field(default_factory=list)  # object names to remove
    name: str | None = None  # convenience for a single object


class ClearScene(BaseModel):
    op: Literal["clear_scene"] = "clear_scene"
    keep: list[str] = Field(default_factory=list)  # object names to preserve
    purge: bool = True  # also drop orphan datablocks


class SetEnv(BaseModel):
    op: Literal["set_env"] = "set_env"
    # Fields to write onto Scene.bbt_env (season/weather/time_of_day/wind_*/snow_line/...);
    # see core/env.py BBT_EnvProps. Unknown fields are reported, not fatal.
    params: dict = Field(default_factory=dict)
    # Re-apply every world consumer after the write (drivers, sun, atmosphere wind, quality) -- the
    # same appliers a World-panel control runs. On by default because writing the fields alone
    # reaches no material until something else installs the drivers, which is what made the world
    # undialable over MCP. False writes the state and defers.
    apply: bool = True


class ApplyWorld(BaseModel):
    op: Literal["apply_world"] = "apply_world"
    # No arguments: re-apply every world consumer to the CURRENT bbt_env, changing no values. For the
    # case where the world is right and the scene is new (a material built after the last world
    # change carries no drivers until something re-installs them).


class DescribeScene(BaseModel):
    op: Literal["describe_scene"] = "describe_scene"
    # The one READ-ONLY op: report objects (transform, modifier stack in order, materials, the
    # heightmap/size/height/sea_level a terrain was built with), materials (master kind, terrain layer
    # slots with their texture sets, which maps actually resolve on disk, and the masks keying each
    # slot), curves (role, shape params, mask + edge attribute names), collections, the world state
    # (including whether the shared env drivers are installed) and the pack search path.
    objects: list[str] | None = None  # else every object in the scene
    include: list[str] | None = None  # objects/materials/collections/world/packs; else all


class ShadeTerrain(BaseModel):
    op: Literal["shade_terrain"] = "shade_terrain"
    object: str  # the terrain mesh to shade
    # A terrain-stack preset key (temperate/alpine/desert), OR an explicit ordered list of
    # layer-preset keys (soil/grass/rock/cliff/scree/sand). layers wins when both are set.
    stack: str | None = None
    layers: list[str] | None = None
    material: str | None = None  # material name, else M_<object>
    assign: bool = True


class ApplyShader(BaseModel):
    op: Literal["apply_shader"] = "apply_shader"
    object: str  # the mesh to shade
    master: Literal["surface", "terrain", "water"] = "surface"
    preset: str | None = None  # a SURFACE_PRESETS key (surface master only)


class SnowShell(BaseModel):
    op: Literal["snow_shell"] = "snow_shell"
    object: str  # the surface to shell (needs a snow_cover pass to read thickness)


# Generation, the Blender half (docs/COMFYUI.md, Ops and MCP). The ComfyUI half needs no bpy and is
# the comfy_* TOOLS in server.py, which talk HTTP directly; these three ops are the steps that do
# need Blender. The terrain macro mask deliberately has no op: it reaches a bake as the `macro` key
# of bake_heightfield's params, which every bake path already takes.
class ApplyTextureSet(BaseModel):
    op: Literal["apply_texture_set"] = "apply_texture_set"
    # A texture-set folder name on the asset-pack search path (comfy_texture_set returns one; ""
    # clears the slot back to a solid tint). Checked against the resolver, so a set that no pack
    # provides is an error rather than a material wired to nothing.
    set: str = ""
    object: str | None = None  # the mesh the material is on; required for a terrain master
    material: str | None = None  # a material by name, else the object's active material
    index: int = 0  # terrain layer slot; ignored by a surface master, which has one set
    # The pack the set came from, which is what comfy_texture_set returns in its `apply_op`. Declared
    # here because an undeclared field is DROPPED by model_dump: the tool returned pack_dir, the op
    # never saw it, and a freshly generated set was unreachable from Blender. The Blender side
    # registers it on the pack search path (core/assets.add_pack_root), so it also stays resolvable
    # across the material rebuilds a later Shaders edit triggers.
    pack_dir: str | None = None


class ImportGenerated(BaseModel):
    op: Literal["import_generated"] = "import_generated"
    kind: str = "rocks"  # trees / rocks / plants / grass: BOB_Assets_<Kind> is where it lands
    # Either `staged` (the dict comfy_mesh returned, which runs pipeline steps 6 to 8 first: bake,
    # scale to height_m, origin to base, weighted normals, LOD chain, BobShader, write the pack) or
    # `name` alone (import an asset the pack already holds). One op, because an agent has no main
    # thread to split the finish and the import across the way the panel does.
    staged: dict | None = None
    name: str | None = None
    height_m: float = 2.0  # the real-world height; mandatory in the manifest for a reason (R11)
    faces: int = 4000
    lods: list[float] | None = None  # LOD decimate ratios, else the shipped (0.5, 0.15)
    hero: bool = False  # 2K bake and 2048 texture rather than 1K/1024
    pack_dir: str | None = None  # else the registered or $BOB_GENERATED generated pack
    cleanup: bool = True  # delete the staged intermediates once the asset is written


class ExportControl(BaseModel):
    op: Literal["export_control"] = "export_control"
    object: str  # the block-out proxy whose shape should condition generation (W7)
    out_file: str | None = None  # else a unique name in the generated pack's _staging/
    points: int = 8192  # how densely Omni samples the control mesh
    pack_dir: str | None = None


# Biome: one call that shades terrain + scatters + sets the world for a named biome.
class ApplyBiome(BaseModel):
    op: Literal["apply_biome"] = "apply_biome"
    object: str  # target mesh to shade + scatter onto
    biome: str  # biome folder name (see the list_biomes tool)
    assign: bool = True  # assign the built terrain material
    weather_assets: bool = True  # convert scattered assets to BobShaders so they weather
    world: bool = True  # write the biome's world block to bbt_env; False keeps the current
    # env, so a set_env after (or a re-apply) is not clobbered by the biome's own world block.
    # Path-aware scatter: "clear" pulls instances off carved paths, "keep" confines them to the
    # path band, None (default) scatters everywhere. Reads the terrain curve mask, so build the
    # typed curve first, then re-apply the biome (with world=False) to open the trail corridor.
    curve_mode: Literal["scatter", "clear", "keep"] | None = None


class WorldBiome(BaseModel):
    op: Literal["world_biome"] = "world_biome"
    biome: str  # apply just the biome's world/env block to Scene.bbt_env


# Atmosphere (BobFirmament).
class BuildClouds(BaseModel):
    op: Literal["build_clouds"] = "build_clouds"
    object: str = "BOB_Clouds"
    cloud_shadows: bool = True


class BuildFog(BaseModel):
    op: Literal["build_fog"] = "build_fog"
    object: str = "BOB_Fog"
    mode: Literal["height_fog", "noise_fog", "ground_fog"] = "height_fog"
    heightmap: str = ""  # absolute path, used only for ground_fog
    # The default fog is dense (thick foggy-morning look) and washes the frame grey. For a thin,
    # beam-friendly haze pass a preset (ground_mist/valley/banks/thick) and/or a density override.
    preset: str | None = None
    density: float | None = None  # explicit Density knob; overlays the preset/recipe default


class BuildRain(BaseModel):
    op: Literal["build_rain"] = "build_rain"
    object: str = "BOB_Rain"
    camera: str | None = None  # frames the particle volume; else the scene camera
    motion_blur: bool = True
    preset: str | None = None  # drizzle / rain / downpour


class BuildMotes(BaseModel):
    op: Literal["build_motes"] = "build_motes"
    object: str = "BOB_Motes"
    camera: str | None = None
    motion_blur: bool = True
    preset: str | None = None  # dust / amber / snow


class BuildSnowCover(BaseModel):
    op: Literal["build_snow_cover"] = "build_snow_cover"
    object: str  # the mesh surface to accumulate snow on


class ApplySeason(BaseModel):
    op: Literal["apply_season"] = "apply_season"
    season: str | None = None  # spring/summer/autumn/winter; None = current env.season
    build_snow: bool | None = None  # None = the season's default
    season_sets_date: bool | None = None  # None = default (set the month from the season)


class ScenePreset(BaseModel):
    op: Literal["scene_preset"] = "scene_preset"
    # A whole-mood preset: clear_day/golden_hour/overcast/storm/foggy_dawn/dust_storm/winter.
    look: str


# Typed paths + water + erosion (BobSplines).
# The shape params a curve op may set (core/splines_build.SHAPE_PARAMS). Exposed because the role's
# defaults used to be the ONLY way to shape a curve: narrowing a 9 m road meant switching to
# dirt_path, which also swaps the material mask channel (bbt_curve_mask_b -> bbt_curve_mask) and
# therefore silently invalidates every scatter layer's curve_attr. Shape and identity are separate.
class CurveShape(BaseModel):
    width: float | None = None  # FULL channel width in metres (1:1, not a radius)
    depth: float | None = None  # channel depth below the rim
    falloff: float | None = None  # width the bank blends back to the surrounding terrain over
    taper: float | None = None  # metres the channel and water fade over at each end
    shoulder: float | None = None  # flat shoulder extending the bed beyond the width
    bank_slope: float | None = None  # rise/run of the banks; lower is wider and gentler
    bank_bias: float | None = None  # -1..1, skew the embankment to one side
    bank_height: float | None = None
    width_var: float | None = None  # 0..1 meander in the channel width
    water_level: float | None = None  # 0..1 fill fraction of the channel (river/stream)
    flow: float | None = None
    foam_bank: float | None = None
    foam_rapids: float | None = None
    wave_amp: float | None = None
    wave_len: float | None = None
    wave_steep: float | None = None
    wave_speed: float | None = None
    wave_chop: float | None = None
    verge_gap: float | None = None  # clear metres out from the edge before the verge band
    verge_width: float | None = None  # width of the band a Verge scatter layer scatters in
    verge_side: float | None = None  # -1 left only, 0 both, +1 right only


class MakeCurve(BaseModel):
    op: Literal["make_curve"] = "make_curve"
    name: str = "Path"
    role: Literal["dirt_path", "trail", "road", "river", "stream"] = "dirt_path"
    points: list[Vector3] = Field(default_factory=list)  # else a starter line sized to terrain
    terrain: str | None = None
    # Applied AFTER the role seeds its defaults, so a caller keeps the role (and therefore its mask
    # channel and its whole effect bundle) while changing the numbers.
    shape: CurveShape | None = None


class CurveBuild(BaseModel):
    op: Literal["curve_build"] = "curve_build"
    curve: str  # an existing typed curve object
    terrain: str | None = None
    # None => use the curve's own bbt_curve setting for each.
    do_terrain: bool | None = None
    do_material: bool | None = None
    do_water: bool | None = None
    do_scatter: bool = False  # no scatter callback over MCP
    shape: CurveShape | None = None  # applied before the build, so the channel is carved to it


class BakeErode(BaseModel):
    op: Literal["bake_erode"] = "bake_erode"
    terrain: str
    curves: list[str] | None = None  # None => every scene curve
    strength: float = 0.5
    scope: Literal["band", "global"] = "band"
    deposit: bool = True
    seed: int = 0


class RevertErode(BaseModel):
    op: Literal["revert_erode"] = "revert_erode"
    terrain: str
    curves: list[str] | None = None


# Add new ops to this union as the library grows.
Operation = Annotated[
    Union[
        AddMesh,
        BuildGeoNodes,
        MakeProxies,
        MakePath,
        DrapeCurve,
        ReloadImage,
        BuildSky,
        AddCamera,
        Render,
        Delete,
        ClearScene,
        SetEnv,
        ApplyWorld,
        DescribeScene,
        ShadeTerrain,
        ApplyShader,
        SnowShell,
        ApplyTextureSet,
        ImportGenerated,
        ExportControl,
        ApplyBiome,
        WorldBiome,
        BuildClouds,
        BuildFog,
        BuildRain,
        BuildMotes,
        BuildSnowCover,
        ApplySeason,
        ScenePreset,
        MakeCurve,
        CurveBuild,
        BakeErode,
        RevertErode,
    ],
    Field(discriminator="op"),
]


# Request and result envelope
class BuildRequest(BaseModel):
    """What to build and where to save it. Paths are workdir-relative (see paths.resolve_output)."""

    output_file: str  # e.g. "_generated/proof.blend"
    ops: list[Operation] = Field(default_factory=list)
    base_file: str | None = None  # open this .blend first, else an empty scene


class OpResult(BaseModel):
    op: str
    created: list[str] = Field(default_factory=list)
    info: str = ""
    # Machine-readable result, for the ops whose output the NEXT call needs: export_control's control
    # path and height, import_generated's face count, UV overlap, height and warnings. `info` is the
    # sentence a human reads; this is what an agent checks instead of trusting it. Empty for the ops
    # whose whole result is the objects they created.
    data: dict = Field(default_factory=dict)


class BuildResult(BaseModel):
    ok: bool
    output_file: str
    results: list[OpResult] = Field(default_factory=list)
    error: str | None = None
    # Live-bridge only. `batch` is the idempotency key the ops ran under: re-sending it COLLECTS that
    # batch rather than re-running it, which is what makes a retry safe after a slow batch. `status`
    # is "done" for a finished batch (ok says whether it succeeded) or "running" when the batch is
    # still on Blender's main thread -- which is NOT a failure, and the ops must not be re-sent.
    batch: str | None = None
    status: str | None = None
