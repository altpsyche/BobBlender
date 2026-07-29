"""Headless measurement: Omni's bounding-box control mode, against the point cloud
(docs/GENERATION.md).

The control gate proved that a block-out proxy can own a generated asset's footprint, using 8,192
points sampled off the proxy's surface. The obvious follow-up sat open for a long time: **did that
need a point cloud, or just eight corners?** `Hy3DOmniBBoxGenerate` conditions on three numbers, the
encoder turns them into the eight corners of a box (`omni_encoder.bbox_to_corners`), and Bob knows
every proxy's bounding box for free. So this gate is one comparison with a null beside it.

  A. **The values, and the mapping that cannot be allowed to drift.** Preflight over every shipped
     graph offline against the committed dump, the `api_node` assertion on `mesh_geom_bbox`, the control mode as
     a value in one place (`control_route`, `CONTROL_WORKFLOWS`, `asset_chain` on either control
     form), and `gen_assets.control_bbox` checked against the control glb's OWN position extents,
     because a permuted bbox is still a valid bbox and conditions on a plausible wrong shape in
     silence. Plus the staged-copy tripwire: the installed `comfy-aimdo` version against the one the
     agent-surface gate measured the segfault on. No server, always runs, costs a second.
  B. **The grid.** The same three block-outs the control gate used, the same conditioning image, the same
     scoring with NO rotation search, each against its own self-agreement ceiling. Three control
     modes: `mesh_geom_ctrl` point (the number to beat), `mesh_geom_bbox` with Bob's proportions,
     and `mesh_geom_bbox` with `auto_bbox`, which estimates the proportions from the image and is
     the NULL that says whether Bob's numbers added anything at all.
  C. **The finished asset.** One block-out through `mesh_geom_bbox`, `mesh_simplify_uv`, `mesh_texture` and steps 6 to 8, against the asset gate
     asset checks it inherits, with the footprint measured again after simplify, bake, scale, LODs
     and BobShade.
  D. **The transport claim.** `mesh_geom_bbox` uploads nothing, so it should be the one Omni route that survives
     a process with no ComfyUI folder, which is what the geometry A/B found broken over MCP.
     Measured both ways with `comfy_dir()` forced to None, rather than asserted.

    ~/.steam/steam/steamapps/common/Blender/blender --background --factory-startup \\
        --python tools/scripts/headless_gen_bbox_control.py -- [--part a,b,c,d] [--fresh] [--no-gen]

Reachability-gated: with no server, or with the Omni pack or its weights absent, every generation
half prints SKIP and exits 0. Generated meshes cache WITH their timing and VRAM under
`_generated/bbox_control_check/gen/`, so `--no-gen` re-scores in minutes and `--fresh` regenerates. The
shape maths, the block-outs and the VRAM sampler are imported from the control gate rather than
copied, so both gates' numbers are the same measurement and not two implementations of it. Exit 0 =
nothing failed.
"""

import argparse
import json
import os
import struct
import sys
import time

import bpy
import numpy as np

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(REPO, "blender", "extensions"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import headless_gen_assets as assets_gate  # noqa: E402
import headless_gen_blockout_control as control_gate  # noqa: E402

from bob_blender_tools.core import (  # noqa: E402
    comfy,
    gen_assets,
    materials,
)

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))  # for `_gate`
from _gate import Gate  # noqa: E402

# The shared gate harness (`_gate.py`): one `check` / `note` / exit-code implementation for every
# gate, bound to module-level names so the call sites below read as plain assertions. `FAILURES` is
# the Gate's own list, not a copy, so anything already reading it keeps working.
GATE = Gate("bbox gate")
check, note, skip = GATE.check, GATE.note, GATE.skip
FAILURES = GATE.failures
OUT = os.path.join(REPO, "_generated", "bbox_control_check")
GEN = os.path.join(OUT, "gen")
DUMP = os.path.join(REPO, "tools", "tests", "data", "object_info_min.json")

SEED = control_gate.SEED
FACES = control_gate.FACES

# Every class `mesh_geom_bbox` needs beyond ComfyUI core and TRELLIS.2. Absent means SKIP, not FAIL.
OMNI_CLASSES = ("Hy3DOmniLoadPipeline", "Hy3DOmniPointGenerate", "Hy3DOmniBBoxGenerate")

# The three control modes, in the order the tables read. "auto" is not a Bob mode: it is
# `mesh_geom_bbox` with `auto_bbox`, i.e. the node guessing the proportions off the conditioning
# image, and it is here as the null. Without it a bbox result that merely looks reasonable cannot be
# told apart from the model doing what it would have done from the picture alone.
COLUMNS = ("mesh_geom_ctrl point", "mesh_geom_bbox bbox", "mesh_geom_bbox auto")

# The decision rule, fixed BEFORE the run so the verdict cannot be chosen after seeing the table.
# `DEFAULT_CONTROL_MODE` moves to "bbox" only if the bbox column wins or draws on footprint IoU on
# at least two of the three block-outs AND is not slower; a draw is within 0.02 IoU, which is under
# the smallest gap between any two ceilings the control gate measured.
DRAW = 0.02
WIN_THRESHOLD = 2

# What the agent-surface gate measured the `comfy_aimdo` segfault on (docs/GENERATION.md, the
# staged-copy fault). Its re-test trigger is a
# fork update, so the version is the tripwire: same version, same install, nothing to re-test.
AIMDO_MEASURED = "0.4.10"


def section(title):
    print()
    print(f"-- {title} " + "-" * max(0, 76 - len(title)))


def glb_extents(path):
    """The POSITION accessor extents of a glb's first primitive, read out of its JSON chunk.

    Pure struct and json, because this has to work in Blender's interpreter with no mesh library,
    and because reading the file Omni will read is the only way to check the frame mapping against
    something other than the code that produced it.
    """
    with open(path, "rb") as fh:
        data = fh.read()
    _magic, _version, total = struct.unpack("<III", data[:12])
    offset, doc = 12, None
    while offset < total and doc is None:
        length, kind = struct.unpack("<II", data[offset:offset + 8])
        if kind == 0x4E4F534A:
            doc = json.loads(data[offset + 8:offset + 8 + length].decode("utf-8"))
        offset += 8 + length
    prim = doc["meshes"][0]["primitives"][0]
    acc = doc["accessors"][prim["attributes"]["POSITION"]]
    return [round(acc["max"][i] - acc["min"][i], 5) for i in range(3)]


def aimdo_version():
    """The installed `comfy-aimdo` version, or None. Read from the ComfyUI checkout's own venv."""
    base = comfy.comfy_dir() or os.path.expanduser("~/dev/ComfyUI")
    site = os.path.join(base, "venv", "lib")
    if not os.path.isdir(site):
        return None
    for root, dirs, _files in os.walk(site):
        for name in dirs:
            if name.startswith("comfy_aimdo-") and name.endswith(".dist-info"):
                return name[len("comfy_aimdo-"):-len(".dist-info")]
        if root.count(os.sep) - site.count(os.sep) > 2:
            dirs[:] = []
    return None


# -- Part A: the values, and the frame mapping ----------------------------------------------------
def part_a(args, reachable):
    section("A. The control mode as a value, the frame mapping, and preflight")

    with open(DUMP) as fh:
        info = json.load(fh)
    note("committed /object_info dump", f"{len(info)} classes, {os.path.relpath(DUMP, REPO)}")
    names = sorted(n[:-5] for n in os.listdir(comfy.WORKFLOW_DIR) if n.endswith(".json"))
    bad = []
    for name in names:
        graph, prov = comfy.load_workflow(name)
        problems = comfy.preflight(graph, info=info, required_titles=("BOB_OUT",),
                                   runtime_inputs=prov.get("runtime_inputs") or ())
        if problems:
            bad.append(f"{name}: {'; '.join(problems)[:120]}")
    check("every shipped graph preflights offline against the committed dump", not bad,
          f"{len(names)} graphs" + ("; " + "; ".join(bad) if bad else ""))

    graph, prov = comfy.load_workflow("mesh_geom_bbox")
    cloud = [n["class_type"] for n in graph.values()
             if info.get(n["class_type"], {}).get("api_node")]
    check("mesh_geom_bbox names no cloud node", not cloud, f"{len(graph)} nodes, api_node: {cloud or 'none'}")
    check("mesh_geom_bbox records the template it came from", bool(prov.get("derived_from")),
          str(prov.get("derived_from"))[:90])
    check("mesh_geom_bbox uploads no mesh, which is the point of it",
          not any(n["class_type"] == "Trellis2LoadMesh" for n in graph.values()),
          "classes: " + ", ".join(sorted({n["class_type"] for n in graph.values()})))

    # The mode is a value in ONE place, so the truth table is the test. A third mode reads the SAME
    # control file `mesh_geom_ctrl` does, so a mesh alone no longer names a mode and the default
    # breaks that tie too. Written against the constant rather than against "point", or this gate
    # fails the day the default moves for a reason the bbox gate did not measure.
    table = [({}, None, "no control at all"),
             ({"control": "/x.glb"}, comfy.DEFAULT_CONTROL_MODE, "a mesh and nothing else"),
             ({"control_bbox": [1, 1, 1]}, "bbox", "proportions and nothing else"),
             ({"control": "/x.glb", "control_bbox": [1, 1, 1]}, comfy.DEFAULT_CONTROL_MODE,
              "both, so the default decides"),
             ({"mode": "bbox", "control": "/x.glb"}, "bbox", "an explicit mode wins")]
    wrong = [f"{why}: {comfy.control_route(**kw)!r} not {want!r}"
             for kw, want, why in table if comfy.control_route(**kw) != want]
    check("control_route resolves every combination the callers can produce", not wrong,
          "; ".join(wrong) or f"5 of 5, default {comfy.DEFAULT_CONTROL_MODE!r}")
    check("every control mode names a shipped graph",
          all(comfy.CONTROL_WORKFLOWS.get(m) in names for m in comfy.CONTROL_MODES),
          ", ".join(f"{m} -> {comfy.CONTROL_WORKFLOWS.get(m)}" for m in comfy.CONTROL_MODES))
    check("either control form forces the staged chain",
          comfy.asset_chain(control="/x.glb") is comfy.generate_asset_chain
          and comfy.asset_chain(control_bbox=[1, 1, 1]) is comfy.generate_asset_chain
          and comfy.asset_chain(route="alt") is comfy.generate_asset_alt,
          "and an uncontrolled route still resolves normally")

    # The frame mapping, against the file Omni actually reads rather than against the code that
    # wrote it. A cube with three different extents, so a permutation cannot pass for the identity.
    control_gate.empty_scene()
    bpy.ops.mesh.primitive_cube_add(size=1.0)
    probe = bpy.context.active_object
    probe.scale = (0.3, 0.7, 1.0)
    bpy.ops.object.transform_apply(scale=True)
    os.makedirs(GEN, exist_ok=True)
    path = os.path.join(GEN, "frame_probe.glb")
    exported = gen_assets.export_control(probe, path)
    got, want = exported["bbox"], glb_extents(path)
    check("control_bbox matches the control glb's own extents, axis for axis",
          all(abs(a - b) < 1e-4 for a, b in zip(got, want)),
          f"bbox {got} against glb POSITION extents {want}, from Blender dims "
          f"{[round(float(d), 3) for d in probe.dimensions]}")
    check("export_control carries the bbox too, so one call serves both modes",
          "bbox" in exported and exported["path"],
          f"path plus bbox {exported['bbox']}")
    signal = gen_assets.control_signal(probe, path, mode="bbox")
    check("the bbox mode writes no mesh at all",
          signal["path"] is None and signal["bbox"] == got and signal["height_m"] > 0,
          f"height {signal['height_m']:.3f} m, bbox {signal['bbox']}")

    # `mesh_geom_bbox`'s own binding, both ways, without a server: `auto_bbox` is the difference
    # between Bob's numbers and the node's guess, and it is bound from one argument being None.
    for dims, auto in (([0.3, 1.0, 0.7], False), (None, True)):
        bound = comfy.template(comfy.load_workflow("mesh_geom_bbox")[0],
                               {"BOB_SEED": dict({"seed": SEED, "auto_bbox": dims is None},
                                                 **({} if dims is None else
                                                    {"bbox_length": dims[0],
                                                     "bbox_height": dims[1],
                                                     "bbox_depth": dims[2]}))})
        node = next(n for n in bound.values() if n["_meta"]["title"] == "BOB_SEED")
        check(f"`mesh_geom_bbox` binds auto_bbox={auto} for dims={dims}", node["inputs"]["auto_bbox"] is auto,
              f"length {node['inputs']['bbox_length']}, height {node['inputs']['bbox_height']}, "
              f"depth {node['inputs']['bbox_depth']}")

    # The staged-copy tripwire. The rule says re-test on a fork update; the version is what says
    # whether there was one, so a stale reminder becomes a check.
    version = aimdo_version()
    check("comfy-aimdo is the version the segfault was measured on, so the staged-copy check needs no re-run",
          version in (None, AIMDO_MEASURED),
          f"installed {version or 'absent'}, measured {AIMDO_MEASURED}"
          + ("" if version in (None, AIMDO_MEASURED) else "; re-run the tiling test and the staged-copy check"))
    note("TILING_COPY_MODE", f"{comfy.TILING_COPY_MODE!r}, the staged-copy workaround, still in force")
    note("ComfyUI folder", str(comfy.comfy_dir()))


# -- Part B: the grid ------------------------------------------------------------------------------
def routes_for(kind, image, dims):
    """The three columns for one block-out: which graph, which control, and where it caches."""
    control = os.path.join(GEN, f"{kind}_control.glb")
    return {
        "mesh_geom_ctrl point": (os.path.join(GEN, f"{kind}_w7.glb"),
                     lambda t: comfy.mesh_geom_ctrl(control, image, t, seed=SEED)),
        "mesh_geom_bbox bbox": (os.path.join(GEN, f"{kind}_w7b.glb"),
                     lambda t: comfy.mesh_geom_bbox(dims, image, t, seed=SEED)),
        "mesh_geom_bbox auto": (os.path.join(GEN, f"{kind}_w7b_auto.glb"),
                     lambda t: comfy.mesh_geom_bbox(None, image, t, seed=SEED)),
    }


def part_b(args, reachable, ready):
    section("B. Eight corners against 8,192 points, on the same three block-outs")
    if not (reachable and ready) and not args.no_gen:
        print("[SKIP] part B needs a server with the Omni pack and its weights")
        return
    note("decision rule, fixed before the run",
         f"bbox becomes the default only if it wins or draws (within {DRAW}) on footprint IoU on "
         f"at least {WIN_THRESHOLD} of 3 block-outs AND is not slower")

    scores, rows = {}, []
    for kind in control_gate.PROMPTS:
        obj = control_gate.blockout(kind)
        proxy_points = control_gate.mesh_points(obj, seed=1)
        ceiling = control_gate.fixed_agreement(proxy_points, control_gate.mesh_points(obj, seed=99))
        control = os.path.join(GEN, f"{kind}_control.glb")
        os.makedirs(GEN, exist_ok=True)
        exported = gen_assets.export_control(obj, control)
        dims = exported["bbox"]
        views = control_gate.views_of(obj, os.path.join(GEN, f"{kind}_views"))
        note(kind, f"{gen_assets.face_count(obj)} faces, {exported['height_m']:.3f} m tall, "
                   f"bbox {dims}, ceiling IoU {ceiling['iou']:.4f} / footprint "
                   f"{ceiling['footprint_iou']:.4f}")

        for label, (target, run) in routes_for(kind, views[0], dims).items():
            if args.no_gen and not os.path.isfile(target):
                print(f"[SKIP] {kind} {label}: --no-gen and nothing cached")
                continue
            stamp = control_gate.generate_cached(target, lambda t=target, r=run: r(t), args.fresh)
            if stamp.get("error") or not os.path.isfile(target):
                check(f"{kind} {label} generated a mesh", False, stamp.get("error", "no file"))
                continue
            control_gate.empty_scene()
            got = gen_assets.import_glb(target, name=f"{kind}_{label.replace(' ', '_')}",
                                        orient=gen_assets.CONTROL_RETURN_TURN)
            gen_assets.weld(got)
            agree = control_gate.fixed_agreement(proxy_points, control_gate.mesh_points(got, seed=2))
            best = control_gate.best_axis_map(proxy_points, control_gate.mesh_points(got, seed=2))
            agree["best_iou"] = best["iou"]
            agree["best_map"] = f"{best['perm']}{best['signs']}"
            scores[(kind, label)] = dict(agree, faces=gen_assets.face_count(got),
                                         seconds=stamp.get("seconds", 0.0),
                                         peak=stamp.get("vram", {}).get("comfy_peak", 0),
                                         rise=stamp.get("vram", {}).get("rise", 0),
                                         ceiling_iou=ceiling["iou"],
                                         ceiling_footprint=ceiling["footprint_iou"],
                                         bbox=dims)
            rows.append((kind, label, scores[(kind, label)]))

    if not rows:
        print("[SKIP] nothing generated and nothing cached")
        return

    print()
    print("| block-out | control | wall s | peak MiB | rise | faces | IoU | footprint IoU "
          "| ceiling footprint | Chamfer | aspect error |")
    print("|---|---|---|---|---|---|---|---|---|---|---|")
    for kind, label, s in rows:
        print(f"| {kind} | {label} | {s['seconds']:.1f} | {s['peak']} | {s['rise']} | "
              f"{s['faces']} | {s['iou']:.4f} | {s['footprint_iou']:.4f} | "
              f"{s['ceiling_footprint']:.4f} | {s['chamfer']:.4f} | {s['aspect_error']:.3f} |")
    print()
    print("| block-out | control | footprint IoU as a fraction of the ceiling | IoU if turned "
          "first | best map |")
    print("|---|---|---|---|---|")
    for kind, label, s in rows:
        print(f"| {kind} | {label} | {s['footprint_iou'] / max(s['ceiling_footprint'], 1e-9):.4f} "
              f"| {s['best_iou']:.4f} | {s['best_map']} |")

    def column(label):
        return [s for (_k, lab), s in scores.items() if lab == label]

    for label in COLUMNS:
        got = column(label)
        if got:
            # Mean AND fastest, because the first generation of a session carries the 13.5 GB Omni
            # load and a three-row mean cannot absorb that: the fastest row is the warm cost.
            note(f"{label}, over {len(got)} block-outs",
                 f"footprint IoU {np.mean([s['footprint_iou'] for s in got]):.4f}, IoU "
                 f"{np.mean([s['iou'] for s in got]):.4f}, "
                 f"{np.mean([s['seconds'] for s in got]):.1f} s mean and "
                 f"{min(s['seconds'] for s in got):.1f} s warm, "
                 f"peak {int(np.mean([s['peak'] for s in got]))} MiB, aspect error "
                 f"{np.mean([s['aspect_error'] for s in got]):.3f}")

    # First, whether the control signal reaches the model at ALL. This is the control gate's failure
    # mode -- a wrapper that ignores its control and says nothing -- and it has to be separated from
    # "the control is not enough", which is a finding rather than a defect. A box can only control
    # PROPORTIONS, so proportions are what it is asked to control, against the node's own guess.
    pairs = [(kind, scores.get((kind, "mesh_geom_bbox bbox")), scores.get((kind, "mesh_geom_bbox auto")))
             for kind in control_gate.PROMPTS]
    live = [(k, b, a) for k, b, a in pairs if b and a]
    if live:
        held = sum(1 for _k, b, a in live if b["aspect_error"] < a["aspect_error"])
        check("Bob's proportions reach the model, measured against the node's own guess",
              held == len(live),
              "; ".join(f"{k} aspect error {b['aspect_error']:.3f} against {a['aspect_error']:.3f}"
                        for k, b, a in live))
        beat_null = sum(1 for _k, b, a in live if b["footprint_iou"] > a["footprint_iou"] + DRAW)
        note("and on FOOTPRINT, which a box cannot describe, against the same null",
             f"Bob's bbox ahead on {beat_null} of {len(live)}; "
             + "; ".join(f"{k} {b['footprint_iou']:.4f} against {a['footprint_iou']:.4f}"
                         for k, b, a in live))

    pairs = [(kind, scores.get((kind, "mesh_geom_bbox bbox")), scores.get((kind, "mesh_geom_ctrl point")))
             for kind in control_gate.PROMPTS]
    live = [(k, b, p) for k, b, p in pairs if b and p]
    if live:
        wins = sum(1 for _k, b, p in live if b["footprint_iou"] >= p["footprint_iou"] - DRAW)
        faster = min(b["seconds"] for _k, b, _p in live) <= \
            min(p["seconds"] for _k, _b, p in live)
        verdict = "bbox" if (wins >= WIN_THRESHOLD and faster) else "point"
        note("VERDICT, footprint IoU, bbox against point",
             f"bbox wins or draws {wins} of {len(live)}; means "
             f"{np.mean([b['footprint_iou'] for _k, b, _p in live]):.4f} against "
             f"{np.mean([p['footprint_iou'] for _k, _b, p in live]):.4f}, warm wall clock "
             f"{min(b['seconds'] for _k, b, _p in live):.1f} s against "
             f"{min(p['seconds'] for _k, _b, p in live):.1f} s, so the rule says "
             f"DEFAULT_CONTROL_MODE = {verdict!r}")
        check("the shipped default is the one the rule chose",
              comfy.DEFAULT_CONTROL_MODE == verdict,
              f"shipped {comfy.DEFAULT_CONTROL_MODE!r}, rule {verdict!r}")
        # The adopted mode has to clear the control gate's own bar. The mode that lost does not, and
        # holding it to one would only encode a hope: what it has to do is be measured and be
        # documented.
        adopted = "mesh_geom_bbox bbox" if comfy.DEFAULT_CONTROL_MODE == "bbox" else "mesh_geom_ctrl point"
        got = column(adopted)
        check(f"the shipped control mode ({adopted}) clears the control gate's footprint bar on every block-out",
              all(s["footprint_iou"] > 0.5 for s in got),
              "; ".join(f"{k} {s['footprint_iou']:.4f}"
                        for (k, lab), s in scores.items() if lab == adopted))

    with open(os.path.join(OUT, "part_b.json"), "w") as fh:
        json.dump({f"{k}::{lab}": s for (k, lab), s in scores.items()}, fh, indent=2,
                  sort_keys=True, default=str)


# -- Part C: the finished asset --------------------------------------------------------------------
def part_c(args, reachable, ready):
    section("C. The finished asset from three numbers, through the asset checks it inherits")
    if not (reachable and ready):
        print("[SKIP] part C needs a server with the Omni pack and its weights")
        return
    kind = "notched"
    obj = control_gate.blockout(kind)
    proxy_points = control_gate.mesh_points(obj, seed=1)
    height = float(obj.dimensions[2])
    dims = gen_assets.control_bbox(obj)
    pack = os.path.join(OUT, "pack")
    views = control_gate.views_of(obj, os.path.join(GEN, f"{kind}_views"))

    raw = os.path.join(GEN, f"{kind}_w7b.glb")
    stamp = control_gate.generate_cached(raw, lambda: comfy.mesh_geom_bbox(dims, views[0], raw, seed=SEED),
                                args.fresh)
    if stamp.get("error") or not os.path.isfile(raw):
        check("mesh_geom_bbox generated a mesh to finish", False, stamp.get("error", "no file"))
        return

    staged_dir = os.path.join(GEN, "finish")
    os.makedirs(staged_dir, exist_ok=True)
    simp = os.path.join(staged_dir, "simp.glb")
    tex = os.path.join(staged_dir, "tex.glb")
    t0 = time.time()
    try:
        if args.fresh or not os.path.isfile(simp):
            comfy.mesh_simplify_uv(raw, simp, faces=FACES)
        if args.fresh or not os.path.isfile(tex):
            comfy.mesh_texture(simp, views[0], tex, seed=SEED, texture_size=1024)
    except comfy.ComfyError as exc:
        check("mesh_simplify_uv and mesh_texture finished the block-out asset", False, str(exc)[:200])
        return
    note("mesh_simplify_uv plus mesh_texture", f"{time.time() - t0:.1f} s")

    # Through the SHIPPED function that decides the per-file turns. The bbox route uploads no
    # control, so this is also the check that `stage_exports` reads the chain and not the presence
    # of a control file.
    exports = comfy.stage_exports({"meta": {"control": None, "control_bbox": dims},
                                   "simplified_mesh": simp, "textured_mesh": tex})
    note("turns to undo per staged file", str(exports))
    control_gate.empty_scene()
    report = gen_assets.finish_asset(raw, pack, kind="rocks", name=f"bbox_{kind}",
                                     height_m=height, faces=FACES, exports=exports,
                                     simplify_pass=simp, texture_pass=tex)
    final = gen_assets.import_generated(report["name"], kind="rocks", pack_dir=pack)
    got = gen_assets.dimensions(final)
    low, _high = gen_assets.bbox_world(final)
    check("face count inside the budget", report["faces"] <= FACES, f"{report['faces']} of {FACES}")
    check("UVs exist and do not overlap", report["uv_overlap"] < 0.001,
          f"overlap {report['uv_overlap']}")
    check("bbox height equals the block-out's height", abs(got[2] - height) < 1e-3,
          f"{got[2]:.4f} m against {height:.4f} m")
    check("origin sits at the base", abs(low[2] - final.location[2]) < 1e-3,
          f"base {low[2]:.4f}, origin {final.location[2]:.4f}")
    check("the LOD chain exists", len(report["lod_faces"]) >= 3, str(report["lod_faces"]))
    check("the mesh_texture albedo reached the finished asset",
          "basecolor" in (report.get("maps") or {}),
          "maps " + ", ".join(sorted((report.get("maps") or {}))))
    check("the material is a BobShader",
          materials.master_type(final.active_material) is not None,
          str(materials.master_type(final.active_material)))
    # The bake frame, which the geometry A/B made a number rather than a hope: a route that rescales
    # on the server and does not move its dense mesh bakes a flat normal and nothing errors.
    note("bake_rescale", str(report.get("bake_rescale")))
    normal = (report.get("maps") or {}).get("normal")
    detail = assets_gate.neighbour_detail(normal) if normal else 0.0
    check("the baked normal carries detail", detail > 0.001,
          f"mean absolute neighbour difference {detail:.5f}"
          + ("" if normal else ", and no normal map was written"))
    agree = control_gate.fixed_agreement(proxy_points, control_gate.mesh_points(final, seed=2))
    note("the finished asset against the block-out",
         f"footprint IoU {agree['footprint_iou']:.4f}, IoU {agree['iou']:.4f}, "
         f"aspect {agree['aspect']}")

    # What steps 6 to 8 are on the hook for is PRESERVING whatever the control achieved, not
    # improving it, so the check is against this route's own raw mesh rather than against the
    # control gate's absolute bar. That bar belongs to the adopted mode and the control gate already
    # holds the point route to it; holding the mode that LOST to it would only record a
    # disappointment as a failure.
    control_gate.empty_scene()
    source = gen_assets.import_glb(raw, name="raw", orient=gen_assets.CONTROL_RETURN_TURN)
    gen_assets.weld(source)
    raw_agree = control_gate.fixed_agreement(proxy_points, control_gate.mesh_points(source, seed=2))
    check("simplify, bake, scale, LODs and BobShade keep the footprint the control achieved",
          agree["footprint_iou"] >= raw_agree["footprint_iou"] - 0.05,
          f"finished {agree['footprint_iou']:.4f} against the raw mesh's "
          f"{raw_agree['footprint_iou']:.4f}")
    with open(os.path.join(OUT, "part_c.json"), "w") as fh:
        json.dump({"report": report, "agreement": agree, "raw_agreement": raw_agree,
                   "bbox": dims}, fh, indent=2, sort_keys=True, default=str)


# -- Part D: the transport claim
# --------------------------------------------------------------------
def part_d(args, reachable, ready):
    section("D. mesh_geom_bbox uploads nothing, so it is the one Omni route with no ComfyUI folder to know")
    if not (reachable and ready):
        print("[SKIP] part D needs a server with the Omni pack and its weights")
        return
    kind = "rock"
    obj = control_gate.blockout(kind)
    dims = gen_assets.control_bbox(obj)
    control = os.path.join(GEN, f"{kind}_control.glb")
    if not os.path.isfile(control):
        gen_assets.export_control(obj, control)
    views = control_gate.views_of(obj, os.path.join(GEN, f"{kind}_views"))

    # ONE variable, which needs saying because the obvious way to run this measures two.
    # `omni_model_dir` is derived from `comfy_dir` as well, so simply forcing the folder away also
    # unbinds the LOCAL WEIGHTS and both routes fall back to the graph's portable repo id -- at
    # which point the wrapper starts a 13.5 GB HuggingFace download and neither route is being
    # measured. The weights are held at what this machine has; the transport is the variable.
    weights = comfy.omni_model_dir()
    saved_pref = comfy.comfy_dir()
    saved_env = os.environ.pop("BOB_COMFY_DIR", None)
    comfy.set_pref_comfy_dir(None)
    saved_lookup = comfy.omni_model_dir
    comfy.omni_model_dir = lambda: weights
    try:
        note("ComfyUI folder, forced away",
             f"comfy_dir {comfy.comfy_dir()}, input_3d_dir {comfy.input_3d_dir()}, "
             f"weights held at {weights}")
        point_error = None
        try:
            comfy.mesh_geom_ctrl(control, views[0], os.path.join(GEN, "transport_point.glb"),
                                 seed=SEED, steps=1)
        except comfy.ComfyError as exc:
            point_error = str(exc)
        check("the point route fails without a ComfyUI folder, which is the geometry A/B's finding restated",
              point_error is not None, (point_error or "it succeeded").splitlines()[0][:160])

        target = os.path.join(GEN, "transport_bbox.glb")
        if args.fresh and os.path.isfile(target):
            os.remove(target)
        t0 = time.time()
        try:
            info = comfy.mesh_geom_bbox(dims, views[0], target, seed=SEED)
        except comfy.ComfyError as exc:
            check("the bbox route runs with no ComfyUI folder at all", False, str(exc)[:200])
            return
        check("the bbox route runs with no ComfyUI folder at all", os.path.isfile(target),
              f"{info['bytes']} bytes in {time.time() - t0:.1f} s, control {info['control_bbox']}")
    finally:
        comfy.omni_model_dir = saved_lookup
        comfy.set_pref_comfy_dir(saved_pref)
        if saved_env is not None:
            os.environ["BOB_COMFY_DIR"] = saved_env


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--part", default="a,b,c,d")
    parser.add_argument("--fresh", action="store_true", help="regenerate cached meshes")
    parser.add_argument("--no-gen", action="store_true",
                        help="score the cache only, generate nothing")
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    args = parser.parse_args(argv)
    os.makedirs(GEN, exist_ok=True)

    section("verdict: Omni's bounding-box control mode")
    ok, detail = comfy.reachable()
    note("ComfyUI", detail if ok else f"not reachable ({detail[:70]})")
    checkout = os.environ.get("BOB_COMFY_DIR", os.path.expanduser("~/dev/ComfyUI"))
    if os.path.isdir(checkout):
        comfy.set_pref_comfy_dir(checkout)
        note("mesh transport", f"local copy into {checkout}/input/3d")
    ready, missing, weights = False, list(OMNI_CLASSES), None
    if ok:
        info = comfy.object_info()
        missing = [c for c in OMNI_CLASSES if c not in info]
        weights = comfy.omni_model_dir()
        ready = (not missing) and bool(weights)
        note("Omni", f"pack {'present' if not missing else 'MISSING ' + ','.join(missing)}, "
                     f"weights {weights or 'MISSING'}")
        if not ready:
            print("[SKIP] the Omni pack or its weights are absent, which is a supported state: "
                  "every other route is unaffected")

    parts = {p.strip() for p in args.part.split(",") if p.strip()}
    if "a" in parts:
        part_a(args, ok)
    if "b" in parts:
        part_b(args, ok, ready)
    if "c" in parts:
        part_c(args, ok, ready)
    if "d" in parts:
        part_d(args, ok, ready)

    section("Summary")
    sys.exit(GATE.exit_code())


if __name__ == "__main__":
    main()
