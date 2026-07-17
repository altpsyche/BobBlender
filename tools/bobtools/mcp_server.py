"""MCP server exposing repo operations to agents/clients.

Run:  bob-mcp            (stdio transport)
Deps: pip install -e '.[mcp]'

Register with an MCP client (e.g. Claude Code):
    claude mcp add bob -- bob-mcp

Start small and safe: read the library, list/create projects. Add tools that
*drive* Blender or ComfyUI as those bridges mature — but keep destructive ops
behind explicit, obvious names.
"""

import logging
import sys

from mcp.server.fastmcp import FastMCP

from . import bridge, config, executor, scaffold
from .contracts import BuildRequest

mcp = FastMCP("bob")


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
    """Author ops into the CURRENTLY OPEN Blender session (live socket bridge).

    Same op vocabulary as `build`, but applied to the running Blender instead of
    a headless file — you see the result appear in your viewport. Requires
    blender/bridge/live_server.py to be running inside Blender.
    """
    request = BuildRequest(output_file="(live)", ops=ops)  # validate
    validated = [op.model_dump() for op in request.ops]
    return bridge.run_build_live(validated).model_dump()


def main() -> None:
    # stderr only — stdout is the MCP stdio protocol channel.
    logging.basicConfig(
        level=logging.INFO, stream=sys.stderr, format="[%(name)s] %(message)s"
    )
    mcp.run()


if __name__ == "__main__":
    main()
