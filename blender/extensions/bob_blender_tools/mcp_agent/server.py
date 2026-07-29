"""MCP server exposing repo-free build operations to agents.

Ships INSIDE the BobBlenderTools extension (mcp_agent/) and runs as a standalone stdio
process the agent client spawns — it does NOT import bpy and needs no repo checkout. It
drives a user's own folders (see paths.py env vars) and a running Blender via the live
socket bridge, or headlessly by spawning Blender with the extension's own runner.

Deps: mcp>=1.2 + pydantic>=2 (+ numpy>=1.26 for bake_heightfield). Launch via
mcp_agent/__main__.py; the Advanced panel's "Copy MCP Config" button prints the exact
.mcp.json snippet with this install's resolved path.

Tools cover reading asset packs, listing and creating projects, building geometry
(headless, or into the open Blender session), and the ComfyUI generation surface
(docs/GENERATION.md). Keep any destructive operation behind an explicit, obvious name.

The `comfy_*` tools run HERE rather than crossing the bridge, because generation needs no bpy: they
are HTTP against a local ComfyUI, and only the step that applies a result to a scene is an op. Every
one of them preflights the graph before queueing (so a missing model is a sentence, not an HTTP 400)
and returns `{"ok": false, "error": ...}` when the server is not reachable, because ComfyUI is never
required: without it every other tool here behaves exactly as before.
"""

import logging
import os
import re
import sys

from mcp.server.fastmcp import FastMCP

from . import bridge, executor, paths
from .contracts import BuildRequest

mcp = FastMCP("bobblendermcp")


def _slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.strip().lower()).strip("-")
    return slug


# -- The ComfyUI half (docs/GENERATION.md) ------------------------------------------------------
def _comfy():
    """The stdlib ComfyUI client from the extension's `core/`, imported lazily.

    Lazily because ComfyUI is optional: an agent that never generates anything must not pay an
    import for it, and a broken install of it must not stop `build` from working.
    """
    paths.add_core_to_path()
    import comfy  # noqa: E402  (resolved via <ext>/core on sys.path)

    return comfy


def _unreachable(comfy, detail: str) -> dict:
    """The one shape every comfy_* tool returns when there is no server to talk to."""
    return {"ok": False, "url": comfy.base_url(), "error":
            f"ComfyUI is not reachable at {comfy.base_url()}: {detail}. Start it (or set "
            "$BOB_COMFY_URL) and try again; nothing else in this toolset needs it."}


def _generation(fn, route=None):
    """Run one generation call, turning every failure into a sentence rather than a traceback.

    Four failures are worth telling apart and this is where they become one shape: no server at all,
    not enough free VRAM (the VRAM-handback rule: the card is the scarce resource the moment an
    agent generates and renders in one session), a graph that will not run (preflight: a missing
    model, a pack that is not installed, a cloud node) and anything else. The first three are the
    normal ones and all three are the artist's to fix.

    `route` names the VRAM floor to check (core/comfy.VRAM_FLOOR_MIB). The check tries one recovery
    before it refuses, so the common case -- a card a previous job left full -- costs a `POST /free`
    rather than a 90-second job that dies inside somebody else's worker process.
    """
    comfy = _comfy()
    ok, detail = comfy.reachable()
    if not ok:
        return _unreachable(comfy, detail)
    try:
        if route:
            comfy.preflight_vram(route)
        out = fn(comfy)
    except comfy.ComfyError as exc:
        return {"ok": False, "url": comfy.base_url(), "error": str(exc)}
    except (OSError, ValueError) as exc:
        return {"ok": False, "url": comfy.base_url(), "error": f"{type(exc).__name__}: {exc}"}
    out.setdefault("ok", True)
    return out


@mcp.tool()
def comfy_status() -> dict:
    """Is the local ComfyUI reachable, on what device, with how much free VRAM and how deep a queue.

    The check to make before any comfy_* call, and the one tool here that never fails: with no
    server it returns {"ok": false, ...} and a reason. Also lists the generation tools and the
    shipped workflows, so an agent can see what this install can actually run.

    Returns {ok, url, device, vram_free_mib, running, pending, detail, workflows}.
    """
    comfy = _comfy()
    status = comfy.service_status()
    try:
        status["workflows"] = sorted(
            os.path.splitext(f)[0] for f in os.listdir(comfy.WORKFLOW_DIR) if f.endswith(".json"))
    except OSError:
        status["workflows"] = []
    status["generated_pack"] = str(paths.generated_pack())
    return status


@mcp.tool()
def comfy_free() -> dict:
    """Ask ComfyUI to give the card back, and report honestly how much it actually gave.

    The tool to reach for between a generate and a Cycles render, and after a generate that failed
    on VRAM. It is not a fix for the VRAM-handback rule (docs/GENERATION.md) and does not pretend to
    be: `POST /free` only drops what ComfyUI's MAIN process will release, the generation workers are
    separate processes that cannot reuse that cache, and on the measured case it recovers about 100
    MiB of a 7.3 GB hold. When that is not enough this says so and names the thing that does work --
    restarting the server, which Bob will not do for a server it did not start.

    Returns {ok, before, after, recovered, advice} in MiB.
    """
    comfy = _comfy()
    ok, detail = comfy.reachable()
    if not ok:
        return _unreachable(comfy, detail)
    # Measured against the mesh floor, the route that actually runs out, so `enough` answers the
    # only question worth asking after a free: can I generate now.
    result = comfy.recover_vram(target_mib=comfy.VRAM_FLOOR_MIB["mesh"])
    result["ok"] = True
    result["url"] = comfy.base_url()
    result["floor_mib"] = comfy.VRAM_FLOOR_MIB
    return result


@mcp.tool()
def comfy_texture_set(
    prompt: str,
    seed: int = 0,
    size: int = 1024,
    reference: str | None = None,
    negative: str | None = None,
) -> dict:
    """Generate a seamless PBR texture set from a prompt, into the generated asset pack.

    Writes `<pack>/textures/<set>/` with basecolor, roughness, normal, height and AO plus a
    SOURCE.txt recording the model and its licence. Seamless by circular padding in the UNet AND the
    VAE, which is measured rather than claimed (seam ratio about 1.0, where 1.0 means the wrap is as
    continuous as any interior line).

    prompt: what the surface is ("wet mossy river stones"). The tileability and lighting clauses are
            added for you. reference: an optional local image the texture follows (switches graph).
    seed: reaches the sampler literally, INCLUDING the 0 default, so the same prompt and seed
            reproduce the same set. Repeating a call never overwrites (the second `mossy_stone`
            becomes `mossy_stone_02`), so a reroll is a DIFFERENT seed, not a second call.
    size: 1024 is SDXL's native size.

    Then ASSIGN it with the `apply_texture_set` op (via build_live or build), which needs Blender:
    {"op": "apply_texture_set", "object": "Terrain", "set": <the returned set>, "index": 1}.

    Returns {ok, set, dir, maps, seconds, apply_op, pack_dir} or {ok: false, error}.
    """
    def run(comfy):
        pack = str(paths.generated_pack())
        name, info = comfy.texture_set_from_prompt(
            prompt, pack, seed=int(seed), size=int(size), negative=negative,
            reference=reference,
            workflow="tex_tileable_ref" if reference else "tex_tileable")
        return {"set": name, "dir": info.get("dir"), "maps": sorted(info.get("maps") or {}),
                "seconds": info.get("seconds"), "seam": info.get("seam"), "pack_dir": pack,
                "apply_op": {"op": "apply_texture_set", "set": name, "object": "<your mesh>",
                             "index": 0, "pack_dir": pack}}

    return _generation(run, route="texture")


@mcp.tool()
def comfy_bark_set(
    prompt: str = "rough conifer bark",
    name: str = "",
    seed: int = 0,
    size: int = 1024,
) -> dict:
    """Generate a BARK texture set, measured for grain direction as well as for tiling.

    `comfy_texture_set` with the one clause bark needs and the one measurement it needs. Bark grain
    runs along a trunk and a tileable SDXL pass has no reason to keep an axis: measured, "rough
    conifer bark" on its own came back as polygonal mud cracks 84 degrees off vertical, and the
    shipped clause holds it inside 18 degrees across species and seeds (docs/FOLIAGE.md 3).

    prompt: the species and its surface ("grey beech bark", "shaggy redwood bark"). The
    vertical-grain
            clause and the tiling and lighting clauses are added for you.
    name:   the set's folder name. Pass the name a species preset asks for -- the shipped `conifer`
            wants `bark_conifer` and `broadleaf` wants `bark_broadleaf` -- and every tree of that
            species picks it up with no assignment step. Blank names it after the prompt.

    The tree recipe reads it by NAME, so nothing needs applying afterwards: build or rebuild a
    foliage object and the bark is on it.

    Returns {ok, set, dir, maps, grain, seam, seconds, pack_dir} or {ok: false, error}.
    `grain.off_vertical_deg` is the verdict: under about 25 is usable, and it is gated by
    tools/scripts/headless_foliage.py rather than left to taste.
    """
    def run(comfy):
        pack = str(paths.generated_pack())
        set_name, info = comfy.bark_set(prompt, pack, name=(name or None), seed=int(seed),
                                        size=int(size))
        return {"set": set_name, "dir": info.get("dir"), "maps": sorted(info.get("maps") or {}),
                "grain": info.get("grain"), "seam": info.get("seam"),
                "seconds": info.get("seconds"), "pack_dir": pack}

    return _generation(run, route="texture")


@mcp.tool()
def comfy_leaf_atlas(
    prompt: str = "pine needle spray",
    cols: int = 2,
    rows: int = 2,
    name: str = "",
    seed: int = 0,
    size: int = 1024,
    route: str = "",
) -> dict:
    """Generate a LEAF ATLAS: a grid of foliage sprites on transparent, for BobFoliage's cards.

    A texture set with an `opacity` role, which is what a leaf card's cutout reads. Cell 0 is the
    bottom-left and the grid runs row-major upward, matching the recipe's cell-to-UV map.

    Bob generates one sprite per cell and composes the grid itself, because a diffusion model asked
    for a grid does not make one: measured, a "2 by 2 grid, one spray per quadrant" prompt returned
    five sprays in a ring straddling every boundary, none touching a cell's bottom edge, and
    per-cell coverage still passed. Composing instead means every cell is filled, the cells differ,
    and each sprite is oriented with its stem at the bottom -- where a card's v is 0, so a leaf
    hangs from its twig rather than by its tips.

    prompt: the foliage ("spruce needle spray", "birch leaf cluster", "fern frond"). The
    single-sprig
            and lighting clauses are added for you.
    cols/rows: the grid. 2x2 is four sprites; 4x4 is sixteen and about four times the wall clock.
    name:   the set's folder name; blank names it after the prompt. route:  "cells" (default,
    composed) or "grid" (one frame, the measured-insufficient way).

    The set RECORDS its own grid, so a species preset only has to name the set: the recipe reads the
    layout from it and its `Atlas Columns` / `Atlas Rows` knobs stay as an override. Point a tree at
    it with the `atlas` param on `build_geonodes`.

    Returns {ok, set, dir, maps, cols, rows, cells, cell_distinctness, seconds, pack_dir} or {ok:
    false, error}. `cells` is per cell: `opaque` 0 is a card that renders as nothing.
    """
    def run(comfy):
        pack = str(paths.generated_pack())
        set_name, info = comfy.leaf_atlas(prompt, pack, cols=int(cols), rows=int(rows),
                                          name=(name or None), seed=int(seed), size=int(size),
                                          route=(route or None))
        return {"set": set_name, "dir": info.get("dir"), "maps": sorted(info.get("maps") or {}),
                "cols": info.get("cols"), "rows": info.get("rows"), "cells": info.get("cells"),
                "cell_distinctness": info.get("cell_distinctness"),
                "clear_fraction": info.get("clear_fraction"),
                "seconds": info.get("seconds"), "pack_dir": pack}

    return _generation(run, route="texture")


@mcp.tool()
def comfy_mesh(
    prompt: str,
    kind: str = "rocks",
    height_m: float = 2.0,
    faces: int = 4000,
    seed: int = 0,
    route: str | None = None,
    hero: bool = False,
    control: str | None = None,
    control_bbox: list[float] | None = None,
    control_mode: str | None = None,
    subject: str | None = None,
    negative: str | None = None,
) -> dict:
    """Generate a scatter asset from a prompt: reference image, then geometry plus PBR texture.

    This is the ComfyUI half only, and it is the slow one (40 to 200 s warm). It leaves a staged
    mesh on disk; the Blender half (bake, scale to height_m, origin at the base, LOD chain,
    BobShader, write the pack, import into BOB_Assets_<Kind>) is the `import_generated` op, which
    this returns ready to send. Generated meshes are scatter-grade by design: dense triangles, no
    edge flow, convincing at 3 m.

    WRITING THE PROMPT is the highest-leverage thing here, because every geometry stage conditions
    on the reference IMAGE and none of them reads your text. Describe the subject you want isolated
    on a plain background ("a bare-root douglas fir sapling, on a white studio sweep") rather than
    listing what you do not want: SDXL does not honour negations, and "no pot, no planter, no
    container" returned a nursery pot twice on the redwood run where the bare-root phrasing fixed it
    first try. `negative` is the place for exclusions -- it reaches the reference image's negative
    conditioning, which is the only stage a negation works at.

    kind: trees / rocks / plants / grass. plants and grass are treated as FOLIAGE, which keeps the
          open surfaces a leaf needs (no remesh, no pinhole fill). `kind="trees"` returns ONE SOLID
          MESH: no leaf cards, no alpha, no branch hierarchy, and no skeleton to grow one from. Use
          it for DEAD WOOD -- a stump, a fallen log, a snag, a root ball -- which is the class this
          route measures best on. A standing tree is not one of them: the crown comes back a faceted
          fan, and a generated trunk cannot carry branches because there is no curve to attach them
          to. Live trees come from the foliage generator (docs/FOLIAGE.md, the dead-wood routing
          rule).
    height_m: the real-world height. Mandatory in spirit: every image-to-3D model emits a
          unit-cube mesh, so without it the scatter looks like a toy set.
    faces: the face budget the simplify hits. hero: 2K bake and 2048 texture.
    seed: reaches the graph literally, INCLUDING the 0 default, so the same prompt and seed restage
          the same mesh -- which is the only way back after `import_generated` has consumed the
          staging (its `cleanup` defaults to true). The staged folder is named `<slug>_s<seed>`, so
          an existing one tells you the key it was made with. A reroll is a different seed.
    negative: what must NOT appear in the reference image (pot, hands, text, multiple objects). The
          framing clause every subject gets is appended for you; this is the artist's half.
    control: a control mesh from the `export_control` op, so the result keeps a block-out's
          silhouette and footprint (forces the staged chain, which is the only one taking a control).
          This route UPLOADS a mesh, and on this ComfyUI fork the loader runs in a worker whose
          working directory is not the server root, so it needs `$BOB_COMFY_DIR` (or the addon's
          ComfyUI-folder preference) pointing at the checkout. Without it the job fails inside the
          graph with "Mesh file not found"; `control_bbox` has no such dependency.
    control_bbox: the same op's `bbox` field instead, which conditions on the block-out's three
          proportions rather than on its surface. Cheaper and it uploads nothing; measured at the
          bbox gate, and which one is the default is `comfy.DEFAULT_CONTROL_MODE`. Pass one or the
          other. THE CEILING: the Omni node's widgets bound each of the three to [0.1, 3.0], so a
          raw ratio like [1, 9, 1] is rejected outright. `export_control` already divides by the
          longest axis, which is why its own output always fits; hand-written numbers must be
          normalised the same way, and that also caps the expressible slenderness at 1:10 per axis.
    control_mode: which Omni control the mesh in `control` becomes. "point" samples its surface and
          "voxel" quantises it to a 16-cubed occupancy grid; both read the same file, so the mesh
          alone cannot say which was meant and leaving this unset takes the measured default. Only
          "bbox" needs its own signal, which is `control_bbox`.
    subject: a local image with ALPHA to use instead of generating a reference. Skips the reference
          stage entirely, so `negative` does nothing alongside it.
    route: "oneshot" (default, `mesh_subject` then `mesh_geom_texture`), "staged" (`mesh_subject`, `mesh_geom_trellis`, `mesh_simplify_uv`, `mesh_texture`; the only route that
          leaves a dense mesh on disk) or "alt" (`mesh_subject`, `mesh_geom_alt`, `mesh_process`,
          `mesh_texture`; Hunyuan 2.1 geometry, which needs no custom node pack). Leave it unset and
          the kind decides, which is the geometry A/B verdict.

    Returns {ok, staged, import_op, seconds, pack_dir} or {ok: false, error}.
    """
    def run(comfy):
        pack = str(paths.generated_pack())
        chain = comfy.asset_chain(route=route, kind=kind, control=control,
                                  control_bbox=control_bbox)
        staged = chain(prompt, pack, seed=int(seed), tier="hero" if hero else "default",
                       faces=int(faces), remesh=not comfy.is_foliage(kind),
                       texture_size=2048 if hero else 1024, subject=subject, negative=negative,
                       **({"control": control, "control_bbox": control_bbox,
                           "control_mode": control_mode}
                          if (control or control_bbox) else {}))
        return {"staged": staged, "seconds": staged.get("seconds"), "pack_dir": pack,
                "import_op": {"op": "import_generated", "kind": kind, "staged": staged,
                              "height_m": float(height_m), "faces": int(faces),
                              "hero": bool(hero), "pack_dir": pack}}

    routes = _comfy().ASSET_ROUTES
    if route is not None and route not in routes:
        return {"ok": False, "error": f"unknown route {route!r} (have: {', '.join(routes)})"}
    if control_bbox is not None and (len(control_bbox) != 3
                                     or any(float(d) <= 0 for d in control_bbox)):
        return {"ok": False, "error": "control_bbox is three positive numbers "
                                      "[length, height, width], from export_control's bbox field"}
    # The Omni node's own widget bounds, checked here so the answer is a sentence rather than an
    # HTTP 400 from inside the graph 90 seconds later. Normalising is the fix and it is what
    # export_control already does, so the message says which number to divide by.
    lo, hi = _comfy().CONTROL_BBOX_RANGE
    if control_bbox is not None and any(not lo <= float(d) <= hi for d in control_bbox):
        return {"ok": False, "error": f"control_bbox is bounded to [{lo}, {hi}] per axis by the "
                                      f"Omni node, and {[float(d) for d in control_bbox]} leaves "
                                      f"it. Pass PROPORTIONS: divide all three by the largest, "
                                      f"which is what export_control's bbox field already is. "
                                      f"Normalised that way the low bound caps slenderness at "
                                      f"1:{int(1.0 / lo)} per axis."}
    modes = _comfy().CONTROL_MODES
    if control_mode is not None and control_mode not in modes:
        return {"ok": False, "error": f"unknown control_mode {control_mode!r} "
                                      f"(have: {', '.join(modes)})"}
    # The hero tier is 1536_cascade, which needs materially more of the card than the default 1024,
    # so it gets its own floor rather than sharing one that would let it through and then OOM.
    return _generation(run, route="mesh_hero" if hero else "mesh")


@mcp.tool()
def comfy_paint_mesh(
    mesh_file: str,
    prompt: str,
    seed: int = 0,
    texture_size: int = 1024,
    subject: str | None = None,
) -> dict:
    """Texture a mesh you already have, in its own UVs (the mesh-texturing family, the PBR route).

    Takes a local mesh file (GLB / OBJ / PLY / STL), uploads it, generates a reference image from
    the prompt unless you pass one, and returns a textured GLB with base colour, roughness, metallic
    and opacity in that mesh's UVs.

    The mesh has to be UNIT-CUBE normalised or the texture comes back silently BLACK: the encoder
    voxelises in unit-cube space and a metre-scale mesh lands outside the grid. The `export_control`
    op writes exactly that normalisation, so it is the way to get a Bob object into this tool.

    The other route, stylised painting with LoRA control, is not available here: it renders
    turntable views, which needs Blender, so it stays a panel action (Stylise) rather than an MCP
    tool.

    Returns {ok, path, seconds, subject} or {ok: false, error}.
    """
    def run(comfy):
        pack = str(paths.generated_pack())
        staging = comfy.staging_dir(pack)
        os.makedirs(staging, exist_ok=True)
        stem = comfy.slugify(prompt) or "painted"
        image = subject
        seconds = {}
        if not image:
            image = comfy.unique_file_name(staging, stem + "_subject", ".png")
            info = comfy.subject_image(prompt, image, seed=int(seed))
            seconds["subject"] = info["seconds"]
            image = info["path"]
        out = comfy.unique_file_name(staging, stem + "_painted", ".glb")
        info = comfy.mesh_texture(mesh_file, image, out, seed=int(seed),
                                  texture_size=int(texture_size))
        seconds["texture"] = info["seconds"]
        return {"path": info["path"], "subject": image, "seconds": seconds,
                "route": comfy.DEFAULT_TEXTURE_ROUTE, "pack_dir": pack}

    if not os.path.isfile(mesh_file):
        return {"ok": False, "error": f"no mesh file at {mesh_file!r}"}
    return _generation(run, route="paint")


@mcp.tool()
def comfy_heightmap(
    prompt: str,
    out_file: str = "_generated/macro.png",
    seed: int = 0,
    route: str | None = None,
    invert: bool = False,
) -> dict:
    """Generate a terrain MACRO MASK from a prompt: where the massif, the basin, the ridge go.

    Not a heightfield, and the distinction is the whole design. The mask supplies the low-frequency
    LAYOUT (about 0.3 m of relief on a 54 m tile) and the erosion stack supplies the landform (about
    3 m of fine relief and every slope), measured. Diffusion has no drainage logic; the op stack
    has.

    Feed it to `bake_heightfield`'s `macro` key, which this returns ready to use:
    bake_heightfield(out_file="_generated/t.png", params={"preset": "alpine",
                     "macro": {"path": <the returned path>, "weight": 0.6}})
    then build the terrain with a heightmap_terrain `build_geonodes` op.

    invert: which way a model paints an elevation map is a coin flip per prompt, so this is the
            toggle for when white came back as the low ground.
    route: "open" (default) or "tiled". Open is measured-correct for a single terrain: a tiling mask
            puts the same elevation on both borders, so the massif repeats at the edge.

    Returns {ok, path, out_file, seconds, bake_params, meta} or {ok: false, error}.
    """
    def run(comfy):
        info = comfy.heightmap_macro(prompt, str(out_abs), seed=int(seed), route=route,
                                     invert=bool(invert))
        return {"path": info["path"], "out_file": out_file,
                "seconds": round(info["seconds"], 2), "meta": info["meta"],
                "bake_params": {"macro": {"path": info["path"]}}}

    try:
        out_abs = paths.resolve_output(out_file)
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}
    if route is not None and route not in ("open", "tiled"):
        return {"ok": False, "error": f"unknown route {route!r} (have: open, tiled)"}
    return _generation(run, route="heightmap")


@mcp.tool()
def comfy_stylize(
    image_file: str,
    prompt: str,
    out_file: str | None = None,
    seed: int = 0,
    strength: float = 0.55,
    depth: str | None = None,
    normal: str | None = None,
) -> dict:
    """Restyle a rendered frame while holding its composition, via depth and normal ControlNet.

    A pitch frame, not geometry: nothing about the scene changes. Pass `depth` and `normal` (Bob's
    true passes, which the panel's Stylise button exports) to use the real geometry, or leave them
    out and the graph estimates both from the image itself. Measured: the estimated route is not
    meaningfully worse, so leaving them out is a real option rather than a fallback.

    strength: the denoise, and the one knob that trades style against silhouette. 0.55 keeps the
              composition (silhouette IoU about 0.998); higher styles harder and drifts.

    Returns {ok, path, seconds, route} or {ok: false, error}.
    """
    def run(comfy):
        target = out_file
        if target:
            out_abs = str(paths.resolve_output(target))
        else:
            stem = os.path.splitext(os.path.basename(image_file))[0]
            out_abs = comfy.unique_file_name(os.path.dirname(os.path.abspath(image_file)),
                                             stem + "_styled", ".png")
        # The route is a value in `core/comfy.py`: passing both passes selects `stylize_render` and
# passing neither selects `stylize_render_est`, so there is no workflow argument to get
# wrong here.
        info = comfy.stylize_render(image_file, out_abs, prompt, depth=depth, normal=normal,
                                    seed=int(seed), denoise=float(strength))
        return {"path": info["path"], "seconds": round(info["seconds"], 2),
                "route": "passes" if (depth and normal) else "estimated"}

    if not os.path.isfile(image_file):
        return {"ok": False, "error": f"no image at {image_file!r}"}
    return _generation(run, route="stylize")


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
def build_live(ops: list[dict] | None = None, batch: str | None = None) -> dict:
    """Author ops into the open Blender session over the live socket bridge.

    Same op vocabulary as build, but applied to the running Blender instead of a headless file, so
    the result appears in the viewport. Requires the BobBlenderTools extension to be enabled with
    its bridge running (Advanced -> Start).

    Every call runs under an IDEMPOTENCY KEY, returned as `batch`. A slow batch (a 14,000-face
    import_generated is the measured case) no longer comes back as a timeout that cannot be told
    from a failure: this waits, collecting the same key, and only returns when the bridge says the
    batch is done. If it does give up it returns status "running" with the key, which means the work
    is STILL IN PROGRESS and the ops must NOT be re-sent -- call build_live(batch="<the key>") to
    collect it. Passing `batch` alone collects; passing `ops` alone starts new work.

    Returns {ok, results:[{op, created, info, data}], error, batch, status}.
    """
    if batch is not None and not ops:
        return bridge.run_build_live([], batch=batch).model_dump()
    request = BuildRequest(output_file="(live)", ops=ops or [])  # validate
    validated = [op.model_dump() for op in request.ops]
    return bridge.run_build_live(validated, batch=batch).model_dump()


@mcp.tool()
def describe_scene(objects: list[str] | None = None,
                   include: list[str] | None = None) -> dict:
    """Read the open Blender session back: objects, materials, curves, collections, world, packs.

    The read-only half of the vocabulary, and the answer to every "which index / which name / what
    was this built from" question that otherwise costs a probe render. Nothing is mutated.

    What it reports, chosen as the values a NEXT call needs as arguments:
    - objects: type, location, dimensions, face count, the MODIFIER STACK IN ORDER (a GN-generated
      mesh shades through the last modifier, so a Set-Material that is not last renders grey), the
      materials, and for a terrain the heightmap / size / resolution / height / sea_level it was
      built with -- the four numbers a drape has to agree with.
    - materials: master kind, and for a terrain master every layer slot with whether it is enabled,
      its texture set, WHICH MAPS ACTUALLY RESOLVE ON DISK (a set that resolves none renders as a
      solid tint), its tint, and the masks keying it. A layer with "Curve Strength" is a curve band,
      which is how you find the `index` an apply_texture_set must name to surface a road.
    - curves: role, which channels are on, the live shape params, and the mask + edge attribute
      names a scatter layer has to target (they move with the ROLE).
    - world: the bbt_env fields, the Live Environment master, and how many env drivers are actually
      installed (the difference between a world value that reaches materials and one that does not).
    - packs: the asset-pack search path in order, the generated pack, texture sets and biomes.

    objects: names to report, else every object. include: sections from
    objects/materials/collections/world/packs, else all.
    """
    result = bridge.run_build_live([{"op": "describe_scene", "objects": objects,
                                     "include": include}])
    data = result.results[0].data if result.results else {}
    return {"ok": result.ok, "error": result.error, **data}


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

             "macro": {"path": <png>, "weight": 0.6, "smooth": 0.02, "invert": false} art-directs
             the LAYOUT from an image: the mask becomes the stack's base and the preset's own
             generator is demoted to adding the rest of the relief, so a prompted silhouette decides
             where the massif goes and the erosion still builds every slope. `comfy_heightmap`
             generates that mask from a prompt and returns this exact fragment.
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
    # A preset's params dict arrives with its stack ALREADY resolved, so `macro` has to be composed
# onto that stack rather than expanded from flat knobs. `pipeline._stack_for` does exactly that
# and is idempotent, which is why this tool passes the key straight through instead of calling
# `params.with_macro` here and risking a second application (docs/GENERATION.md, the macro-mask
# gate correction 12).

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
