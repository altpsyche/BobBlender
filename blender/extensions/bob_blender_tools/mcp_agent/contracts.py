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
    # Re-sample this heightmap at each control point and set its Z to the terrain
    # surface, in place, so a hand-drawn or moved curve follows the current ground
    # (the counterpart to make_path's drape). Must match the heightmap_terrain build.
    heightmap: str | None = None
    size: float = 60.0
    height: float = 14.0
    sea_level: float = 0.3
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
class MakeCurve(BaseModel):
    op: Literal["make_curve"] = "make_curve"
    name: str = "Path"
    role: Literal["dirt_path", "trail", "road", "river", "stream"] = "dirt_path"
    points: list[Vector3] = Field(default_factory=list)  # else a starter line sized to terrain
    terrain: str | None = None


class CurveBuild(BaseModel):
    op: Literal["curve_build"] = "curve_build"
    curve: str  # an existing typed curve object
    terrain: str | None = None
    # None => use the curve's own bbt_curve setting for each.
    do_terrain: bool | None = None
    do_material: bool | None = None
    do_water: bool | None = None
    do_scatter: bool = False  # no scatter callback over MCP


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
        ShadeTerrain,
        ApplyShader,
        SnowShell,
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


class BuildResult(BaseModel):
    ok: bool
    output_file: str
    results: list[OpResult] = Field(default_factory=list)
    error: str | None = None
