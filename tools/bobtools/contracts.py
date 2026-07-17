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
    recipe: str = "wave_grid"  # a named recipe in bbmcp/geonodes.py
    name: str | None = None
    params: dict = Field(default_factory=dict)  # recipe-specific, checked by the builder
    target: Literal["new_object", "library"] = "new_object"
    mark_asset: bool = False  # mark the node group as an Asset Browser asset


# Add MakeMaterial and other ops to this union as the library grows.
Operation = Annotated[Union[AddMesh, BuildGeoNodes], Field(discriminator="op")]


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
