"""MCP server exposing repo and build operations to agents.

Run: bob-mcp (stdio transport). Install with pip install -e '.[mcp]'.
Registered for Claude Code in the repo's .mcp.json.

Tools cover reading the library, listing and creating projects, and building
geometry (headless or into the open Blender session). Keep any destructive
operation behind an explicit, obvious name.
"""

import logging
import sys

from mcp.server.fastmcp import FastMCP

from .. import config, scaffold
from . import bridge, executor
from .contracts import BuildRequest

mcp = FastMCP("bobblendermcp")


@mcp.tool()
def list_projects() -> list[str]:
    """List project folders under projects/ (excludes the _template)."""
    projects = config.projects_dir()
    if not projects.is_dir():
        return []
    return sorted(
        p.name for p in projects.iterdir() if p.is_dir() and p.name != "_template"
    )


@mcp.tool()
def list_library_assets() -> list[str]:
    """List reusable .blend files in the asset library, repo-relative."""
    library = config.library_dir()
    if not library.is_dir():
        return []
    root = config.repo_root()
    return sorted(str(p.relative_to(root)) for p in library.rglob("*.blend"))


@mcp.tool()
def create_project(name: str) -> str:
    """Scaffold a new project from the template. Returns the created path."""
    return scaffold.create_project(name)


@mcp.tool()
def build(output_file: str, ops: list[dict], base_file: str | None = None) -> dict:
    """Build meshes/geometry into a .blend headlessly, via Blender.

    output_file: repo-relative path to save (e.g. "library/_generated/x.blend").
    ops: list of operations, each a dict with an "op" field. Supported today:
      {"op": "add_mesh", "kind": "cube|uv_sphere|ico_sphere|cylinder|cone|
        plane|torus|grid", "name": str?, "location": [x,y,z]?, "size": float?}
    base_file: optional repo-relative .blend to open first (else empty scene).

    Returns a BuildResult dict: {ok, output_file, results:[{op,created,info}], error}.
    """
    request = BuildRequest(output_file=output_file, ops=ops, base_file=base_file)
    return executor.run_build(request).model_dump()


@mcp.tool()
def build_live(ops: list[dict]) -> dict:
    """Author ops into the open Blender session over the live socket bridge.

    Same op vocabulary as build, but applied to the running Blender instead of a
    headless file, so the result appears in the viewport. Requires the Bob
    Blender MCP extension to be enabled with its bridge running.
    """
    request = BuildRequest(output_file="(live)", ops=ops)  # validate
    validated = [op.model_dump() for op in request.ops]
    return bridge.run_build_live(validated).model_dump()


@mcp.tool()
def bake_heightfield(
    out_file: str,
    params: dict | None = None,
    preview: bool = False,
    force: bool = False,
) -> dict:
    """Generate and erode a terrain heightfield PNG in the venv (numpy or CuPy).

    Runs in the venv, not Blender: the heavy erosion is numpy on CPU or a CuPy
    CUDA kernel on GPU. Writes a 16-bit PNG plus a params sidecar, and a Blender
    heightmap_terrain build then displaces a grid by it. After re-baking, send a
    reload_image op so the open session picks up the new pixels.

    out_file: repo-relative PNG path (e.g. "library/_generated/forest_height.png").
    params: {size, seed, backend: "auto"|"cpu"|"gpu", preset: name?,
             generate: {octaves, roughness, ridged, warp, detail_strength},
             passes: [{"kind": "hydraulic"|"thermal"|"stream_power", ...}]}.
    preview: bake at 256 for a fast look, commit full-res without it.
    force: ignore the params-hash cache and re-bake.

    Returns metadata: {path, out_file, backend, platform, size, seconds, stats,
    hash, cached}.
    """
    from ..heightfields import bake, presets

    p = dict(params or {})
    preset = p.pop("preset", None)
    if preset is not None:
        base = presets.get(preset)
        base.update(p)
        p = base

    try:
        out_abs = str(config.resolve_under_repo(out_file))
    except ValueError as exc:
        return {"error": str(exc), "out_file": out_file}
    result = bake(out_abs, p, force=force, preview=preview)
    result["out_file"] = out_file
    return result


def main() -> None:
    # stderr only. stdout is the MCP stdio protocol channel.
    logging.basicConfig(
        level=logging.INFO, stream=sys.stderr, format="[%(name)s] %(message)s"
    )
    mcp.run()


if __name__ == "__main__":
    main()
