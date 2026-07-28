#!/usr/bin/env python3
"""Measure the whole generation surface, driven the way an agent drives it (docs/GENERATION.md).

This is the one gate that does NOT run inside Blender, and that is the point. Every earlier gate
imported `core` and called it; this one calls the real MCP tool functions in the MCP process and
reaches Blender only the way an agent can, through `executor.run_build`, which spawns Blender on the
op list. So what it measures is the agent-facing surface rather than the code behind it: if a tool's
signature, a contract model or a handler is wrong, this fails where a `core`-level test would pass.

    uv run --project tools --extra all python tools/scripts/headless_gen_agent_surface.py \
        [--part a,b,c,d] [--faces 4000] [--keep]

  A. **The contract, offline.** Every new op validated by its model, and every new op REJECTED with a
     readable sentence when given bad params (which is the half a gate usually skips). The comfy_*
     tools pointed at a dead port, so the "server not reachable" degradation is measured rather than
     asserted. The macro key composed onto a preset-shaped params dict, which is the hazard the
     corrected. No server, no GPU, always runs.
  B. **Prompt to a scattered asset, no GUI.** comfy_mesh over HTTP, then ONE build carrying
     import_generated, a scatter layer, a camera and a render. The asset is INSPECTED rather than
     trusted: face count against the budget, a UV layer with no overlap, height_m honoured on the
     built object, origin at the base, and a BobShader on it -- all read back out of the op's own
     result, because that is what an agent can see.
  C. **Prompt to a shaded terrain, no GUI.** comfy_texture_set and comfy_heightmap over HTTP, then
     bake_heightfield with the macro key, then one build carrying the terrain, shade_terrain,
     apply_texture_set, a camera and a render. Wall clock per stage.
  D. **Websocket progress.** The same generation with `/ws` on and off, counting the progress updates
     each route delivered and what they said, so "per-node progress" is a number rather than a claim.

Reachability-gated: with no ComfyUI, parts B, C and D print SKIP and the script still exits 0, which
is the "ComfyUI is never required" property. Exit 0 = nothing failed. The op lists and the renders are
written under `_generated/comfy_g6_check/` so the gate's claims can be audited against artifacts.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
EXT = REPO / "blender" / "extensions" / "bob_blender_tools"
sys.path.insert(0, str(EXT))

OUT = REPO / "_generated" / "comfy_g6_check"
PACK = OUT / "pack"

# The MCP server sandboxes every output path under $BOB_WORKDIR and resolves the generated pack from
# $BOB_GENERATED, so the gate sets both BEFORE importing the server: that is also the configuration an
# agent gets, and doing it here proves the two variables are enough to make the two worlds agree.
os.environ.setdefault("BOB_WORKDIR", str(REPO))
os.environ["BOB_GENERATED"] = str(PACK)
os.environ.setdefault("BOB_ASSET_PACKS", str(REPO / "library"))

from mcp_agent import contracts, executor, paths, server  # noqa: E402
from pydantic import ValidationError  # noqa: E402

FAILURES: list[str] = []
SEED = 4242
PROMPT_ASSET = "a weathered granite boulder covered in lichen"
PROMPT_TEX = "mossy forest floor with small stones and fallen needles"
PROMPT_MACRO = "one isolated steep massif in the north west, broad low valleys elsewhere"


def section(title: str) -> None:
    print(f"\n=== {title} " + "=" * max(0, 62 - len(title)))


def check(label: str, ok: bool, detail: str = "") -> bool:
    print(f"  [{'ok' if ok else 'FAIL'}] {label}" + (f": {detail}" if detail else ""))
    if not ok:
        FAILURES.append(f"{label} ({detail})" if detail else label)
    return ok


def rel(path) -> str:
    """A workdir-relative path, which is the only shape the MCP tools accept."""
    return str(Path(path).resolve().relative_to(Path(os.environ["BOB_WORKDIR"]).resolve()))


def run_build(ops: list[dict], name: str, base: str | None = None) -> dict:
    """One `build` through the real executor, with the op list saved beside the render.

    That sidecar is the provenance rule: every generated artefact carries the recipe that made it.
    """
    (OUT / f"{name}_ops.json").write_text(json.dumps(ops, indent=2, default=str))
    out_file = rel(OUT / f"{name}.blend")
    result = executor.run_build(contracts.BuildRequest(
        output_file=out_file, ops=ops, base_file=base), timeout=1800).model_dump()
    (OUT / f"{name}_result.json").write_text(json.dumps(result, indent=2, default=str))
    return result


def op_result(result: dict, op: str) -> dict:
    return next((r for r in result.get("results", []) if r.get("op") == op), {})


# -- A. The contract, offline -----------------------------------------------------------------
def part_a() -> None:
    section("A. the contract and the degradation, offline")

    # Accepted: one instance of each new op, through the real discriminated union.
    accepted = [
        {"op": "apply_texture_set", "object": "Terrain", "set": "grass", "index": 1},
        {"op": "import_generated", "kind": "rocks", "name": "boulder", "height_m": 1.8},
        {"op": "export_control", "object": "Proxy", "points": 4096},
    ]
    for op in accepted:
        try:
            model = contracts.BuildRequest(output_file="x.blend", ops=[op]).ops[0]
            check(f"{op['op']} validates", model.op == op["op"], type(model).__name__)
        except ValidationError as exc:
            check(f"{op['op']} validates", False, str(exc).splitlines()[0])

    # Rejected. Proving the rejection is the half a gate usually skips, so each of these is a
    # DIFFERENT failure mode: an unknown op, a missing required field, and a wrong type.
    rejections = [
        ({"op": "apply_texture_zet", "set": "grass"}, "unknown op"),
        ({"op": "export_control"}, "export_control with no object"),
        ({"op": "import_generated", "kind": "rocks", "faces": "lots"}, "faces as a string"),
        ({"op": "apply_texture_set", "set": "grass", "index": "second"}, "index as a string"),
    ]
    for op, label in rejections:
        try:
            contracts.BuildRequest(output_file="x.blend", ops=[op])
            check(f"rejects {label}", False, "accepted it")
        except ValidationError as exc:
            first = str(exc).splitlines()[1].strip() if len(str(exc).splitlines()) > 1 else ""
            check(f"rejects {label}", True, first[:70])

    # Every new op is reachable, i.e. in the dispatch registry as well as in the contract. Parsed
    # rather than imported, because importing dispatch needs bpy.
    import ast

    tree = ast.parse((EXT / "core" / "dispatch.py").read_text())
    handlers = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id == "_HANDLERS" for t in node.targets):
            handlers = {k.value for k in node.value.keys if isinstance(k, ast.Constant)}
    for op in ("apply_texture_set", "import_generated", "export_control"):
        check(f"{op} is whitelisted in dispatch", op in handlers)

    api = (REPO / "docs" / "API.md").read_text()
    for op in ("apply_texture_set", "import_generated", "export_control"):
        check(f"{op} is in API.md", f"`{op}`" in api)

    # The macro-mask composition hazard, at the level the MCP tool actually hits it: a preset's
    # params dict arrives with
    # its stack ALREADY resolved, so a macro has to compose onto that stack rather than be expanded
    # from flat knobs, and it must not apply twice.
    paths.add_core_to_path()
    from heightfields import presets  # noqa: E402
    from heightfields import pipeline  # noqa: E402

    mask = OUT / "fixture_mask.png"
    mask.parent.mkdir(parents=True, exist_ok=True)
    if not mask.is_file():
        import comfy_maps  # noqa: E402
        import numpy as np  # noqa: E402

        ramp = np.linspace(0, 255, 64, dtype=np.uint8)
        comfy_maps.write_png(str(mask), np.tile(ramp, (64, 1)))
    params = presets.get("alpine")
    params["macro"] = {"path": str(mask)}
    stack = pipeline._stack_for(params)
    check("macro composes onto a resolved preset stack", stack[0]["kind"] == "macro",
          f"op 0 is {stack[0]['kind']}")
    params["stack"] = stack
    check("composing twice is idempotent",
          sum(1 for op in pipeline._stack_for(params) if op["kind"] == "macro") == 1)

    # The degradation, measured: every comfy_* tool against a port with nothing on it.
    was = os.environ.get("BOB_COMFY_URL")
    os.environ["BOB_COMFY_URL"] = "http://127.0.0.1:1"
    try:
        import comfy  # noqa: E402

        comfy.set_pref_url(None)
        comfy.forget_object_info()
        probes = {
            "comfy_status": lambda: server.comfy_status(),
            "comfy_texture_set": lambda: server.comfy_texture_set("stone", seed=1),
            "comfy_mesh": lambda: server.comfy_mesh("stone", seed=1),
            "comfy_heightmap": lambda: server.comfy_heightmap("ridge", seed=1),
            "comfy_paint_mesh": lambda: server.comfy_paint_mesh(str(mask), "stone"),
            "comfy_stylize": lambda: server.comfy_stylize(str(mask), "painterly"),
        }
        for name, call in probes.items():
            out = call()
            readable = (out.get("ok") is False
                        and "not reachable" in (out.get("error") or out.get("detail") or "").lower())
            check(f"{name} degrades to a sentence", readable,
                  (out.get("error") or out.get("detail") or "")[:64])
    finally:
        if was is None:
            os.environ.pop("BOB_COMFY_URL", None)
        else:
            os.environ["BOB_COMFY_URL"] = was
        import comfy  # noqa: E402

        comfy.forget_object_info()

    # And the same three ops, rejected by the HANDLER rather than by the model: a set no pack
    # provides, and an import with neither a staged dict nor a name. Both go through a real Blender.
    result = run_build([{"op": "add_mesh", "kind": "grid", "name": "Ground"},
                        {"op": "shade_terrain", "object": "Ground", "stack": "temperate"},
                        {"op": "apply_texture_set", "object": "Ground",
                         "set": "no_such_set_anywhere"}], "a_reject_set")
    check("a texture set no pack provides is refused with a sentence",
          not result.get("ok") and "no texture set" in (result.get("error") or ""),
          (result.get("error") or "")[:90])
    result = run_build([{"op": "import_generated", "kind": "rocks"}], "a_reject_import")
    check("import_generated with neither staged nor name is refused",
          not result.get("ok") and "staged" in (result.get("error") or ""),
          (result.get("error") or "")[:90])


# -- B. Prompt to a scattered asset -----------------------------------------------------------
def part_b(args, reachable: bool) -> dict:
    section("B. prompt to a scattered asset, no GUI")
    if not reachable:
        print("  [SKIP] no ComfyUI server: the generation half cannot run")
        return {}

    t0 = time.time()
    gen = server.comfy_mesh(PROMPT_ASSET, kind="rocks", height_m=1.8, faces=args.faces,
                            seed=SEED)
    generate_s = time.time() - t0
    if not check("comfy_mesh returned a staged mesh", bool(gen.get("ok")),
                 (gen.get("error") or "")[:120]):
        return {}
    staged = gen["staged"]
    print(f"  generated in {generate_s:.1f} s: {os.path.basename(staged['raw_mesh'])}")

    # ONE build: the finish and import, a scatter layer that instances it, a camera, a render. The
    # import op is exactly what comfy_mesh handed back, which is the contract this gate exists to
    # check -- an agent should not have to assemble it.
    render_png = OUT / "b_scattered.png"
    ops = [
        {"op": "add_mesh", "kind": "grid", "name": "Ground", "size": 24.0},
        {"op": "shade_terrain", "object": "Ground", "stack": "temperate"},
        gen["import_op"],
        {"op": "build_geonodes", "recipe": "scatter", "name": "ScatterRocks",
         "params": {"emitter": "Ground", "assets": "BOB_Assets_Rocks", "density": 0.4,
                    "min_scale": 0.8, "max_scale": 1.3, "seed": SEED}},
        {"op": "add_camera", "name": "BOB_Camera", "location": [14.0, -14.0, 8.0],
         "look_at": [0.0, 0.0, 1.0], "lens": 45.0},
        {"op": "build_sky", "params": {"time_of_day": 15.0}},
        {"op": "render", "output": str(render_png), "engine": "BLENDER_EEVEE", "samples": 32,
         "resolution": [960, 540]},
    ]
    t1 = time.time()
    result = run_build(ops, "b_scattered")
    build_s = time.time() - t1
    if not check("the build succeeded", bool(result.get("ok")),
                 (result.get("error") or "")[:200]):
        return {}

    imported = op_result(result, "import_generated").get("data") or {}
    print(f"  finished and imported in {build_s:.1f} s: {imported.get('object')}")

    # The inspection, from what the op itself returned: this is what an agent can see, so it is what
    # the gate scores. Nothing here is read by opening the .blend by hand.
    faces = (imported.get("lod_faces") or [None])[0]
    check("face count inside the budget", isinstance(faces, int) and faces <= args.faces,
          f"{faces} against {args.faces}")
    overlap = imported.get("uv_overlap")
    check("a UV layer with no overlap", isinstance(overlap, (int, float)) and overlap <= 0.01,
          f"overlap {overlap}")
    height = imported.get("height_m")
    check("height_m honoured on the built object",
          isinstance(height, (int, float)) and abs(height - 1.8) <= 0.01, f"{height} m against 1.8")
    origin = imported.get("origin_above_base")
    check("origin at the base", isinstance(origin, (int, float)) and abs(origin) <= 1e-3,
          f"{origin} m above the lowest vertex")
    check("a BobShader on it", imported.get("master_type") in ("surface", "terrain"),
          str(imported.get("master_type")))
    scattered = op_result(result, "build_geonodes").get("created") or []
    check("a scatter layer instances it", bool(scattered), ", ".join(scattered))
    check("the render landed", render_png.is_file(),
          f"{render_png.stat().st_size // 1024} KiB" if render_png.is_file() else "missing")

    return {"generate_s": round(generate_s, 1), "build_s": round(build_s, 1),
            "asset": imported, "render": str(render_png),
            "stage_seconds": gen.get("seconds")}


# -- C. Prompt to a shaded terrain ------------------------------------------------------------
def part_c(args, reachable: bool) -> dict:
    section("C. prompt to a shaded terrain, no GUI")
    if not reachable:
        print("  [SKIP] no ComfyUI server: the generation half cannot run")
        return {}

    stages = {}
    t0 = time.time()
    tex = server.comfy_texture_set(PROMPT_TEX, seed=SEED)
    stages["texture_set"] = round(time.time() - t0, 2)
    if not check("comfy_texture_set wrote a set", bool(tex.get("ok")),
                 (tex.get("error") or "")[:120]):
        return {}
    print(f"  set {tex['set']} in {stages['texture_set']} s, maps {', '.join(tex['maps'])}")

    t0 = time.time()
    macro = server.comfy_heightmap(PROMPT_MACRO, out_file=rel(OUT / "c_macro.png"), seed=SEED)
    stages["macro_mask"] = round(time.time() - t0, 2)
    if not check("comfy_heightmap wrote a mask", bool(macro.get("ok")),
                 (macro.get("error") or "")[:120]):
        return {}

    # The bake takes the mask through the `macro` key the tool handed back, which is the whole reason
    # track E needed no new op.
    t0 = time.time()
    baked = server.bake_heightfield(rel(OUT / "c_terrain.png"), params={
        "preset": "alpine", "size": 768, "seed": SEED, **macro["bake_params"]}, force=True)
    stages["bake"] = round(time.time() - t0, 2)
    check("bake_heightfield took the macro key", "error" not in baked,
          baked.get("error", f"{baked.get('size')} px, {baked.get('seconds', 0):.1f} s"))
    if "error" in baked:
        return {}

    # And the same bake WITHOUT the mask, so "the mask changed the terrain" is a measurement rather
    # than a hope. Different hash means the mask reached the recipe; identical means it did not.
    plain = server.bake_heightfield(rel(OUT / "c_terrain_nomask.png"), params={
        "preset": "alpine", "size": 768, "seed": SEED}, force=True)
    check("the macro key changes the baked recipe", baked.get("hash") != plain.get("hash"),
          f"{str(baked.get('hash'))[:10]} against {str(plain.get('hash'))[:10]}")

    render_png = OUT / "c_shaded_terrain.png"
    ops = [
        {"op": "build_geonodes", "recipe": "heightmap_terrain", "name": "Terrain",
         "params": {"heightmap": str(OUT / "c_terrain.png"), "size": 180.0,
                    "resolution": 400, "height": 54.0}},
        {"op": "shade_terrain", "object": "Terrain", "layers": ["soil", "grass", "rock"]},
        {"op": "apply_texture_set", "object": "Terrain", "set": tex["set"], "index": 1,
         "pack_dir": tex["pack_dir"]},
        # No `set_env` here, and that is a finding rather than an omission: `Scene.bbt_env` is a
        # PropertyGroup the ADDON registers, and the headless runner imports `core` into a
        # --factory-startup Blender without enabling the addon, so every env-dependent op
        # (set_env, apply_season, scene_preset) raises there and works over `build_live`. The sky is
        # given its time explicitly for the same reason: with no params it reads the env it cannot see.
        {"op": "build_sky", "params": {"time_of_day": 15.0}},
        {"op": "add_camera", "name": "BOB_Camera", "location": [150.0, -150.0, 90.0],
         "look_at": [0.0, 0.0, 10.0], "lens": 42.0},
        {"op": "render", "output": str(render_png), "engine": "BLENDER_EEVEE", "samples": 48,
         "resolution": [960, 540]},
    ]
    t0 = time.time()
    result = run_build(ops, "c_terrain")
    stages["build_and_render"] = round(time.time() - t0, 2)
    if not check("the build succeeded", bool(result.get("ok")),
                 (result.get("error") or "")[:200]):
        return {}
    applied = op_result(result, "apply_texture_set")
    check("the generated set reached a terrain layer", bool(applied.get("info")),
          applied.get("info", ""))
    check("the render landed", render_png.is_file(),
          f"{render_png.stat().st_size // 1024} KiB" if render_png.is_file() else "missing")

    print("  wall clock per stage: " + ", ".join(f"{k} {v} s" for k, v in stages.items()))
    return {"stages": stages, "set": tex["set"], "render": str(render_png),
            "bake": {k: baked.get(k) for k in ("size", "seconds", "backend", "hash")}}


# -- D. Websocket progress --------------------------------------------------------------------
def part_d(reachable: bool) -> dict:
    section("D. websocket progress against status polling")
    if not reachable:
        print("  [SKIP] no ComfyUI server: there is no /ws to read")
        return {}

    paths.add_core_to_path()
    import comfy  # noqa: E402

    out = {}
    for label, use_ws in (("polling", False), ("websocket", True)):
        seen: list[str] = []
        graph, prov = comfy.load_workflow("tex_tileable")
        bound = comfy.template(graph, {
            "BOB_PROMPT": {"text": f"{PROMPT_TEX}, {comfy.PROMPT_SUFFIX}"},
            "BOB_SEED": {"seed": SEED + (1 if use_ws else 0)},
            "BOB_SIZE": {"width": 1024, "height": 1024}})
        comfy.check(bound, required_titles=("BOB_PROMPT", "BOB_SEED", "BOB_OUT"),
                    runtime_inputs=prov.get("runtime_inputs") or ())
        t0 = time.time()
        pid = comfy.queue(bound)
        comfy.wait(pid, timeout=600, poll=1.0, on_progress=seen.append, progress_ws=use_ws)
        seconds = time.time() - t0
        detailed = [s for s in seen if s.startswith(("step ", "node ")) or "cached" in s]
        out[label] = {"updates": len(seen), "detailed": len(detailed),
                      "seconds": round(seconds, 2), "sample": seen[:4]}
        print(f"  {label:10} {len(seen):>3} updates ({len(detailed)} per-node) over "
              f"{seconds:.1f} s: {seen[:3]}")

    check("the websocket route reports per-node progress",
          out["websocket"]["detailed"] > 0, f"{out['websocket']['detailed']} per-node updates")
    check("the polling route still reports something",
          out["polling"]["updates"] > 0, f"{out['polling']['updates']} status updates")
    check("the websocket route is strictly more informative",
          out["websocket"]["detailed"] > out["polling"]["detailed"],
          f"{out['websocket']['detailed']} against {out['polling']['detailed']}")
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--part", default="a,b,c,d")
    ap.add_argument("--faces", type=int, default=4000)
    ap.add_argument("--keep", action="store_true", help="keep the generated pack")
    args = ap.parse_args(argv)
    parts = {p.strip() for p in args.part.split(",") if p.strip()}

    OUT.mkdir(parents=True, exist_ok=True)
    pack = paths.generated_pack()
    paths.add_core_to_path()
    import comfy  # noqa: E402

    ok, detail = comfy.reachable()
    print(f"workdir:   {os.environ['BOB_WORKDIR']}")
    print(f"pack:      {pack}")
    print(f"ComfyUI:   {'reachable' if ok else 'not reachable'} ({detail})")
    print(f"Blender:   {paths.blender_binary()}")

    report = {}
    if "a" in parts:
        part_a()
    if "b" in parts:
        report["b"] = part_b(args, ok)
    if "c" in parts:
        report["c"] = part_c(args, ok)
    if "d" in parts:
        report["d"] = part_d(ok)

    (OUT / "g6_report.json").write_text(json.dumps(report, indent=2, default=str))
    section("result")
    if FAILURES:
        print(f"{len(FAILURES)} failure(s): " + "; ".join(FAILURES))
    else:
        print("no failures")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
