"""The op contract: the vocabulary shared across the venv/Blender boundary.

Agent input is validated here (the venv has Pydantic). The Blender side receives
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
    recipe: str = "wave_grid"  # a named recipe in bbmcp/geonodes/recipes/
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
    # Params are recipe-like and checked by the builder (bbmcp/world.py), the same
    # freeform pattern as build_geonodes. Keys: time_of_day, year, month, day,
    # utc_offset, latitude, longitude (geographic sun); use_override,
    # sun_elevation, sun_azimuth (manual sun in degrees); sun_strength, sun_angle,
    # sun_disc, sun_intensity; altitude, air, ozone, turbidity, ground_albedo
    # (the 5.2 MULTIPLE_SCATTERING sky); world_strength.
    params: dict = Field(default_factory=dict)


# Add MakeMaterial and other ops to this union as the library grows.
Operation = Annotated[
    Union[AddMesh, BuildGeoNodes, MakeProxies, MakePath, DrapeCurve, ReloadImage, BuildSky],
    Field(discriminator="op"),
]


# Request and result envelope
class BuildRequest(BaseModel):
    """What to build and where to save it. Paths are repo-relative."""

    output_file: str  # e.g. "library/_generated/proof.blend"
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
