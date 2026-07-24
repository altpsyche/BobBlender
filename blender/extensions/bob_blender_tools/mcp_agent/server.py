"""MCP server exposing repo-free build operations to agents.

Ships INSIDE the BobBlenderTools extension (mcp_agent/) and runs as a standalone stdio
process the agent client spawns — it does NOT import bpy and needs no repo checkout. It
drives a user's own folders (see paths.py env vars) and a running Blender via the live
socket bridge, or headlessly by spawning Blender with the extension's own runner.

Deps: mcp>=1.2 + pydantic>=2 (+ numpy>=1.26 for bake_heightfield). Launch via
mcp_agent/__main__.py; the Advanced panel's "Copy MCP Config" button prints the exact
.mcp.json snippet with this install's resolved path.

Tools cover reading asset packs, listing and creating projects, and building geometry
(headless, or into the open Blender session). Keep any destructive operation behind an
explicit, obvious name.
"""

import logging
import re
import sys

from mcp.server.fastmcp import FastMCP

from . import bridge, executor, paths
from .contracts import BuildRequest

mcp = FastMCP("bobblendermcp")


def _slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.strip().lower()).strip("-")
    return slug


@mcp.tool()
def list_projects() -> list[str]:
    """List project folders under the projects root ($BOB_PROJECTS or <workdir>/projects)."""
    projects = paths.projects_dir()
    if not projects.is_dir():
        return []
    return sorted(
        p.name for p in projects.iterdir() if p.is_dir() and not p.name.startswith("_")
    )


@mcp.tool()
def list_library_assets() -> dict:
    """List the asset packs and biomes on the search path ($BOB_ASSET_PACKS + bundled block-out).

    Returns {"packs": [{"root", "id", "name"}, ...], "biomes": [name, ...]}. The bundled block-out
    pack inside the extension is always present as the floor of the search path.
    """
    paths.add_core_to_path()
    import assets  # noqa: E402  (resolved via <ext>/core on sys.path)

    packs = [
        {"root": root, "id": man.get("id"), "name": man.get("name")}
        for root, man in assets.list_packs()
    ]
    return {"packs": packs, "biomes": assets.list_biomes()}


@mcp.tool()
def list_biomes() -> list[dict]:
    """List biomes on the search path with what apply_biome will build for each.

    Returns [{"name", "terrain": bool, "scatter": [kinds], "world": bool, "warnings": [..]}, ...]
    so an agent can pick a biome and know whether it shades terrain, scatters, and sets the world.
    """
    paths.add_core_to_path()
    import assets  # noqa: E402  (resolved via <ext>/core on sys.path)

    out = []
    for name in assets.list_biomes():
        man = assets.biome_manifest(name)
        out.append({
            "name": name,
            "terrain": bool(man.get("terrain")),
            "scatter": sorted((man.get("scatter") or {}).keys()),
            "world": bool(man.get("world")),
            "warnings": assets.validate_biome(name),
        })
    return out


@mcp.tool()
def create_project(name: str) -> str:
    """Scaffold a new project folder under the projects root. Returns the created path.

    Copies $BOB_TEMPLATE if set, else creates a bare folder with a README. Refuses to
    overwrite an existing project.
    """
    import os
    import shutil

    slug = _slugify(name)
    if not slug:
        raise ValueError(f"Could not derive a project name from {name!r}.")

    dest = paths.projects_dir() / slug
    if dest.exists():
        raise FileExistsError(f"Project already exists: {dest}")

    template = os.environ.get("BOB_TEMPLATE")
    if template:
        shutil.copytree(template, dest)
    else:
        dest.mkdir(parents=True)
        (dest / "README.md").write_text(f"# {name}\n\nBobBlender project.\n")

    readme = dest / "README.md"
    if readme.is_file():
        text = readme.read_text().replace("<project-name>", name).replace("<project>", slug)
        readme.write_text(text)

    return str(dest)


@mcp.tool()
def build(output_file: str, ops: list[dict], base_file: str | None = None) -> dict:
    """Build meshes/geometry into a .blend headlessly, via Blender.

    output_file: workdir-relative path to save (e.g. "_generated/x.blend").
    ops: list of operations, each a dict with an "op" field (see the op vocabulary in docs/API.md).
    base_file: optional workdir-relative .blend to open first (else empty scene).

    Returns a BuildResult dict: {ok, output_file, results:[{op,created,info}], error}.
    """
    request = BuildRequest(output_file=output_file, ops=ops, base_file=base_file)
    return executor.run_build(request).model_dump()


@mcp.tool()
def build_live(ops: list[dict]) -> dict:
    """Author ops into the open Blender session over the live socket bridge.

    Same op vocabulary as build, but applied to the running Blender instead of a headless
    file, so the result appears in the viewport. Requires the BobBlenderTools extension to
    be enabled with its bridge running (Advanced -> Start).
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
    """Generate and erode a terrain heightfield PNG (numpy on CPU, or CuPy on GPU when present).

    Runs in this MCP process, not Blender: the heavy erosion is numpy on CPU or a CuPy CUDA
    kernel on GPU, off the extension's one committed compute source (core/heightfields). Writes a
    16-bit PNG plus a params sidecar; a Blender heightmap_terrain build then displaces a grid by
    it. After re-baking, send a reload_image op so the open session picks up the new pixels.

    out_file: workdir-relative PNG path (e.g. "_generated/forest_height.png").
    params: {size, seed, backend: "auto"|"cpu"|"gpu", preset: name?, relief, detail, erosion,
             warp: 0..1 global knobs (0.5 = preset as authored)}. A preset expands to an op stack;
             pass an explicit "stack": [{"kind": ..., ...}] to run one directly (advanced).
    preview: bake at 256 for a fast look, commit full-res without it.
    force: ignore the params-hash cache and re-bake.

    Returns metadata: {path, out_file, backend, platform, size, seconds, stats, hash, cached}.
    """
    paths.add_core_to_path()
    from heightfields import bake, presets  # noqa: E402  (via <ext>/core on sys.path)

    p = dict(params or {})
    preset = p.pop("preset", None)
    if preset is not None:
        base = presets.get(preset)
        base.update(p)
        p = base

    try:
        out_abs = str(paths.resolve_output(out_file))
    except ValueError as exc:
        return {"error": str(exc), "out_file": out_file}
    result = bake(out_abs, p, force=force, preview=preview)
    result["out_file"] = out_file
    return result


@mcp.tool()
def render_scene(
    output_file: str,
    engine: str = "BLENDER_EEVEE",
    samples: int = 64,
    resolution: tuple[int, int] = (1920, 1080),
    camera: str | None = None,
    device: str = "GPU",
    base_file: str | None = None,
) -> dict:
    """Render a scene to an image file and return its path, so an agent can SEE its result.

    By default renders the OPEN Blender session over the live bridge (where build_live authored
    the scene). Pass base_file to instead open a saved .blend headlessly and render that.

    output_file: workdir-relative image path (e.g. "_generated/shot.png").
    engine: "BLENDER_EEVEE" (5.2 name, no _NEXT) or "CYCLES". samples: render samples.
    resolution: (x, y) pixels. camera: camera object name, else the scene camera.
    device: "GPU" or "CPU" (Cycles only; GPU is best-effort and falls back to CPU).
    base_file: optional workdir-relative .blend to render headlessly instead of the live session.

    Returns {ok, path, out_file, info, error}.
    """
    try:
        out_abs = str(paths.resolve_output(output_file))
    except ValueError as exc:
        return {"ok": False, "error": str(exc), "out_file": output_file}

    op = {
        "op": "render", "output": out_abs, "engine": engine, "samples": samples,
        "resolution": list(resolution), "camera": camera, "device": device,
    }

    if base_file is not None:
        # Headless: open the .blend, render, and re-save it next to the source (the PNG is
        # what the render op writes). A throwaway output_file keeps run_build happy.
        request = BuildRequest(output_file=base_file, ops=[op], base_file=base_file)
        result = executor.run_build(request).model_dump()
    else:
        request = BuildRequest(output_file="(live)", ops=[op])  # validate the op
        validated = [o.model_dump() for o in request.ops]
        result = bridge.run_build_live(validated).model_dump()

    info = ""
    for r in result.get("results", []):
        if r.get("op") == "render":
            info = r.get("info", "")
    return {
        "ok": result.get("ok", False),
        "path": out_abs if result.get("ok") else None,
        "out_file": output_file,
        "info": info,
        "error": result.get("error"),
    }


def main() -> None:
    # stderr only. stdout is the MCP stdio protocol channel.
    logging.basicConfig(
        level=logging.INFO, stream=sys.stderr, format="[%(name)s] %(message)s"
    )
    mcp.run()


if __name__ == "__main__":
    main()
