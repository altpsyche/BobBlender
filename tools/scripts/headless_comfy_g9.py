"""Headless measurement of the G9 gate (docs/COMFYUI.md): D12's remainder, Omni's voxel control.

G8 answered half of D12 and named the other half as the interesting one rather than the leftover.
Eight corners lost to 8,192 points because a box constrains EXTENT and says nothing about PLAN, and
a ground plan is what "drops into a layout" reduces to. `Hy3DOmniVoxelGenerate` is the only Omni
control mode left that carries one: it area-samples the same block-out mesh `mesh_geom_ctrl` takes and
`OmniEncoder.generate_voxel` quantises those samples onto a 16-cubed occupancy grid. So this gate is
the same comparison G8 ran, with a third real column and a different null.

  A. **The values, the frame and preflight.** Preflight over every shipped graph offline against the
     committed dump, the `api_node` assertion on `mesh_geom_voxel`, the third control mode as a value in one place
     (`CONTROL_MODES`, `CONTROL_WORKFLOWS`, `control_route` including its refusal of an unknown
     name), `stage_exports` on the mesh form, and the D14 tripwire. No server, always runs.
  B. **The input rotation, then the grid.** `Hy3DOmniVoxelGenerate` turns its control -90 degrees
     about X by default and `Hy3DOmniPointGenerate` does not, so the setting is measured on the
     asymmetric block-out before anything else runs and `comfy.VOXEL_INPUT_ROTATION` has to equal
     what won. Then the same three block-outs G4c and G8 used, the same conditioning image, the same
     scoring with NO rotation search: `mesh_geom_ctrl` point, `mesh_geom_voxel` voxel, `mesh_geom_voxel` with a SWAPPED control (the null),
     and `mesh_geom_bbox` bbox for the record.
  C. **The finished asset.** One block-out through `mesh_geom_voxel`, `mesh_simplify_uv`, `mesh_texture` and steps 6 to 8, against the G3
     asset checks it inherits, with the footprint measured again after the finish.
  D. **Transport.** `mesh_geom_voxel` uploads a mesh, so unlike `mesh_geom_bbox` it cannot be the fallback for a process with
     no ComfyUI folder. Measured rather than reasoned, with the weights held fixed.

    ~/.steam/steam/steamapps/common/Blender/blender --background --factory-startup \\
        --python tools/scripts/headless_comfy_g9.py -- [--part a,b,c,d] [--fresh] [--no-gen]
                                                       [--no-bbox]

Reachability-gated: with no server, or with the Omni pack or its weights absent, every generation
half prints SKIP and exits 0. Generated meshes cache WITH their timing and VRAM under
`_generated/comfy_g9_check/gen/`, so `--no-gen` re-scores in minutes and `--fresh` regenerates. The
shape maths, the block-outs, the VRAM sampler and the caching are imported from the G4c gate and the
normal-detail read from the G3 gate, so G4c's, G8's and G9's numbers are one measurement rather than
three implementations of it. Exit 0 = nothing failed.
"""

import argparse
import json
import os
import sys
import time

import bpy
import numpy as np

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(REPO, "blender", "extensions"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import headless_comfy_g3 as g3  # noqa: E402
import headless_comfy_g4c as g4c  # noqa: E402

from bob_blender_tools.core import (  # noqa: E402
    comfy,
    gen_assets,
    materials,
)

FAILURES = []
OUT = os.path.join(REPO, "_generated", "comfy_g9_check")
GEN = os.path.join(OUT, "gen")
DUMP = os.path.join(REPO, "tools", "tests", "data", "object_info_min.json")

SEED = g4c.SEED
FACES = g4c.FACES

# Every class `mesh_geom_voxel` needs beyond ComfyUI core and TRELLIS.2. Absent means SKIP, not FAIL.
OMNI_CLASSES = ("Hy3DOmniLoadPipeline", "Hy3DOmniPointGenerate", "Hy3DOmniVoxelGenerate",
                "Hy3DOmniBBoxGenerate")

# Which block-out's control each swap run gets, against its own image. The null: if the control
# reaches the model the result follows the CONTROL and not the picture, so it should score badly
# against the block-out whose image it saw and well against the block-out whose control it got.
# A cycle rather than a pairing, so no two runs share a control.
SWAP = {"rock": "tree", "tree": "notched", "notched": "rock"}

# The columns, in the order the tables read.
COLUMNS = ("mesh_geom_ctrl point", "mesh_geom_voxel voxel", "mesh_geom_voxel swap", "mesh_geom_bbox bbox")

# The decision rules, all three fixed BEFORE the run so no verdict can be chosen after the table.
#
#   DRAW           two footprint IoUs within this are a draw. G8's value, kept so the two phases'
#                  verdicts are decided on the same scale.
#   WIN_THRESHOLD  how many of the three block-outs a challenger has to win or draw on.
#   WIRED_THRESHOLD how many of the three the properly controlled run has to beat its own swapped
#                  null on. Below this the control is not reaching the model and there is no verdict
#                  to read, only a defect: three phases running, an Omni control that misses NEVER
#                  errors (G0.5's black albedo, G4c's random projection, G8's auto_bbox).
DRAW = 0.02
WIN_THRESHOLD = 2
WIRED_THRESHOLD = 2

# G4c's footprint bar. It belongs to the ADOPTED mode and to no other: holding a challenger to the
# winner's bar records a negative result as a broken suite (G8's sixth correction).
FOOTPRINT_BAR = 0.5

# What G6 measured the `comfy_aimdo` segfault on (docs/COMFYUI.md, D14), the same tripwire G8 set.
AIMDO_MEASURED = "0.4.10"


def check(label, ok, detail=""):
    print(f"[{'PASS' if ok else 'FAIL'}] {label}" + (f" -- {detail}" if detail else ""))
    if not ok:
        FAILURES.append(label)
    return ok


def note(label, value):
    print(f"[----] {label} -- {value}")


def section(title):
    print()
    print(f"-- {title} " + "-" * max(0, 76 - len(title)))


# -- Part A: the values, the frame and preflight ---------------------------------------------------
def part_a(args, reachable):
    section("A. The third control mode as a value, and preflight")

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

    graph, prov = comfy.load_workflow("mesh_geom_voxel")
    cloud = [n["class_type"] for n in graph.values()
             if info.get(n["class_type"], {}).get("api_node")]
    check("mesh_geom_voxel names no cloud node", not cloud, f"{len(graph)} nodes, api_node: {cloud or 'none'}")
    check("mesh_geom_voxel records the template it came from", bool(prov.get("derived_from")),
          str(prov.get("derived_from"))[:90])
    check("mesh_geom_voxel reads the same control file mesh_geom_ctrl does, through the same loader",
          any(n["class_type"] == "Trellis2LoadMesh" for n in graph.values())
          and any(n["class_type"] == "Hy3DOmniVoxelGenerate" for n in graph.values()),
          "classes: " + ", ".join(sorted({n["class_type"] for n in graph.values()})))

    # The shipped graph has to agree with the constant, or the constant is decoration.
    node = next(n for n in graph.values() if n["_meta"]["title"] == "BOB_SEED")
    check("the shipped graph's apply_input_rotation equals VOXEL_INPUT_ROTATION",
          node["inputs"]["apply_input_rotation"] is comfy.VOXEL_INPUT_ROTATION,
          f"graph {node['inputs']['apply_input_rotation']}, constant "
          f"{comfy.VOXEL_INPUT_ROTATION}; node default is True")
    note("mesh_geom_voxel control density", f"{node['inputs']['sample_point_count']} samples onto a 16-cubed "
                                f"grid, so at most 4096 cells reach the encoder")

    # The mode is a value in ONE place, so the truth table is the test. Two modes now share the mesh
    # form, which is the new row: a mesh alone cannot say which was meant.
    table = [({}, None, "no control at all"),
             ({"control": "/x.glb"}, comfy.DEFAULT_CONTROL_MODE,
              "a mesh and nothing else, so the default decides between the two mesh modes"),
             ({"control_bbox": [1, 1, 1]}, "bbox", "proportions and nothing else"),
             ({"control": "/x.glb", "control_bbox": [1, 1, 1]}, comfy.DEFAULT_CONTROL_MODE,
              "both, so the default decides"),
             ({"mode": "voxel", "control": "/x.glb"}, "voxel", "an explicit mode wins")]
    wrong = [f"{why}: {comfy.control_route(**kw)!r} not {want!r}"
             for kw, want, why in table if comfy.control_route(**kw) != want]
    check("control_route resolves every combination the callers can produce", not wrong,
          "; ".join(wrong) or f"5 of 5, default {comfy.DEFAULT_CONTROL_MODE!r}")

    refused = None
    try:
        comfy.control_route(mode="voxels", control="/x.glb")
    except comfy.ComfyError as exc:
        refused = str(exc)
    check("an unknown control mode raises instead of silently generating uncontrolled",
          refused is not None, (refused or "it returned a mode")[:110])

    check("every control mode names a shipped graph",
          all(comfy.CONTROL_WORKFLOWS.get(m) in names for m in comfy.CONTROL_MODES),
          ", ".join(f"{m} -> {comfy.CONTROL_WORKFLOWS.get(m)}" for m in comfy.CONTROL_MODES))
    check("the mesh modes are the ones that take a control file",
          all(m in comfy.CONTROL_MODES for m in comfy.MESH_CONTROL_MODES)
          and "bbox" not in comfy.MESH_CONTROL_MODES,
          f"mesh modes {comfy.MESH_CONTROL_MODES}, all modes {comfy.CONTROL_MODES}")
    check("a voxel control still forces the staged chain",
          comfy.asset_chain(control="/x.glb") is comfy.generate_asset_chain,
          "same as every other control form")

    # `stage_exports` reads "is there a control", and the voxel route has one in the mesh form, so
    # its raw mesh gets the absolute turn. Wrong here means every voxel asset lies on its side.
    staged = {"meta": {"control": "/x.glb", "control_bbox": None, "control_mode": "voxel"},
              "simplified_mesh": "/s.glb", "textured_mesh": "/t.glb"}
    check("stage_exports gives the voxel route the block-out route's absolute turn",
          comfy.stage_exports(staged) == {"raw": 1, "simplified": 2, "textured": 3},
          str(comfy.stage_exports(staged)))

    # One exporter, one producer: G9 added a control mode and no new export.
    g4c.empty_scene()
    bpy.ops.mesh.primitive_cube_add(size=1.0)
    probe = bpy.context.active_object
    probe.scale = (0.3, 0.7, 1.0)
    bpy.ops.object.transform_apply(scale=True)
    os.makedirs(GEN, exist_ok=True)
    path = os.path.join(GEN, "frame_probe.glb")
    point_signal = gen_assets.control_signal(probe, path, mode="point")
    voxel_signal = gen_assets.control_signal(probe, path, mode="voxel")
    check("the voxel mode produces the same control file the point mode does",
          voxel_signal["path"] == point_signal["path"]
          and voxel_signal["bbox"] == point_signal["bbox"]
          and voxel_signal["mode"] == "voxel",
          f"one exporter, mode label {voxel_signal['mode']!r}, bbox {voxel_signal['bbox']}")

    version = _aimdo_version()
    check("comfy-aimdo is the version G6 measured the segfault on, so D14 needs no re-test",
          version in (None, AIMDO_MEASURED),
          f"installed {version or 'absent'}, measured {AIMDO_MEASURED}"
          + ("" if version in (None, AIMDO_MEASURED) else "; re-run the G6 tiling test and D14"))
    note("ComfyUI folder", str(comfy.comfy_dir()))


def _aimdo_version():
    """The installed `comfy-aimdo` version, or None. Same read as the G8 gate's."""
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


# -- Part B: the input rotation, then the grid ------------------------------------------------------
def _prepare(kind):
    """One block-out's proxy samples, control file, conditioning view and bbox, in one place."""
    obj = g4c.blockout(kind)
    os.makedirs(GEN, exist_ok=True)
    control = os.path.join(GEN, f"{kind}_control.glb")
    exported = gen_assets.export_control(obj, control)
    return {"kind": kind, "points": g4c.mesh_points(obj, seed=1),
            "ceiling": g4c.fixed_agreement(g4c.mesh_points(obj, seed=1),
                                           g4c.mesh_points(obj, seed=99)),
            "control": control, "bbox": exported["bbox"], "height_m": exported["height_m"],
            "faces": gen_assets.face_count(obj),
            "view": g4c.views_of(obj, os.path.join(GEN, f"{kind}_views"))[0]}


def _score(target, proxy_points, name):
    """Import a generated glb the way the shipped path does and score it where it landed."""
    g4c.empty_scene()
    got = gen_assets.import_glb(target, name=name, orient=gen_assets.CONTROL_RETURN_TURN)
    gen_assets.weld(got)
    points = g4c.mesh_points(got, seed=2)
    agree = g4c.fixed_agreement(proxy_points, points)
    agree["faces"] = gen_assets.face_count(got)
    agree["points"] = points
    return agree


def _run_cached(target, run, args, label):
    """Generate unless cached, and return (stamp, ok). `--no-gen` scores the cache only."""
    if args.no_gen and not os.path.isfile(target):
        print(f"[SKIP] {label}: --no-gen and nothing cached")
        return {}, False
    stamp = g4c.generate_cached(target, run, args.fresh)
    if stamp.get("error") or not os.path.isfile(target):
        check(f"{label} generated a mesh", False, stamp.get("error", "no file"))
        return stamp, False
    return stamp, True


def frame_probe(args, blocks):
    """Which way `apply_input_rotation` goes, measured on the asymmetric block-out.

    The node's default is True and the point node has no such flag, so this is the one setting on
    `mesh_geom_voxel` that a reasonable person would leave alone and that would silently cost the whole phase.
    """
    kind = "notched"
    block = blocks[kind]
    scored = {}
    for rotate in (False, True):
        target = os.path.join(GEN, f"{kind}_w7v_rot{int(rotate)}.glb")
        stamp, ok = _run_cached(
            target,
            lambda t=target, r=rotate: comfy.mesh_geom_voxel(block["control"], block["view"], t,
                                                             seed=SEED, rotate_input=r),
            args, f"{kind} `mesh_geom_voxel` apply_input_rotation={rotate}")
        if not ok:
            continue
        agree = _score(target, block["points"], f"probe_rot{int(rotate)}")
        scored[rotate] = dict(agree, seconds=stamp.get("seconds", 0.0))
        note(f"apply_input_rotation={rotate}",
             f"footprint IoU {agree['footprint_iou']:.4f}, IoU {agree['iou']:.4f}, "
             f"aspect {agree['aspect']}, {stamp.get('seconds', 0.0):.1f} s")
    if len(scored) != 2:
        print("[SKIP] the input-rotation probe needs both settings")
        return None
    won = max(scored, key=lambda r: scored[r]["footprint_iou"])
    check("the shipped VOXEL_INPUT_ROTATION is the setting that scored higher",
          comfy.VOXEL_INPUT_ROTATION is won,
          f"measured {won} at footprint IoU {scored[won]['footprint_iou']:.4f} against "
          f"{scored[not won]['footprint_iou']:.4f}; shipped {comfy.VOXEL_INPUT_ROTATION}, "
          f"node default True")
    return {str(k): {m: v for m, v in s.items() if m != "points"} for k, s in scored.items()}


def part_b(args, reachable, ready):
    section("B. The input rotation, then eight corners and 4,096 cells against 8,192 points")
    if not (reachable and ready) and not args.no_gen:
        print("[SKIP] part B needs a server with the Omni pack and its weights")
        return
    note("wiring rule, fixed before the run",
         f"the voxel run must beat its own swapped-control null on footprint IoU on at least "
         f"{WIRED_THRESHOLD} of 3, else there is a defect rather than a verdict")
    note("decision rule, fixed before the run",
         f"voxel becomes the default only if it wins or draws (within {DRAW}) on footprint IoU on "
         f"at least {WIN_THRESHOLD} of 3 block-outs AND is not slower warm")

    blocks = {kind: _prepare(kind) for kind in g4c.PROMPTS}
    for kind, block in blocks.items():
        note(kind, f"{block['faces']} faces, {block['height_m']:.3f} m tall, bbox {block['bbox']}, "
                   f"ceiling IoU {block['ceiling']['iou']:.4f} / footprint "
                   f"{block['ceiling']['footprint_iou']:.4f}")

    probe = frame_probe(args, blocks)

    scores, rows, swap_cross = {}, [], {}
    for kind, block in blocks.items():
        runs = {
            "mesh_geom_ctrl point": (os.path.join(GEN, f"{kind}_w7.glb"),
                         lambda t, b=block: comfy.mesh_geom_ctrl(b["control"], b["view"], t,
                                                                 seed=SEED)),
            "mesh_geom_voxel voxel": (os.path.join(GEN, f"{kind}_w7v.glb"),
                          lambda t, b=block: comfy.mesh_geom_voxel(b["control"], b["view"], t,
                                                                   seed=SEED)),
            "mesh_geom_voxel swap": (os.path.join(GEN, f"{kind}_w7v_swap.glb"),
                         lambda t, b=block, o=blocks[SWAP[kind]]:
                         comfy.mesh_geom_voxel(o["control"], b["view"], t, seed=SEED)),
        }
        if not args.no_bbox:
            runs["mesh_geom_bbox bbox"] = (os.path.join(GEN, f"{kind}_w7b.glb"),
                                lambda t, b=block: comfy.mesh_geom_bbox(b["bbox"], b["view"], t,
                                                                        seed=SEED))
        for label, (target, run) in runs.items():
            stamp, ok = _run_cached(target, lambda t=target, r=run: r(t), args, f"{kind} {label}")
            if not ok:
                continue
            agree = _score(target, block["points"], f"{kind}_{label.replace(' ', '_')}")
            if label == "mesh_geom_voxel swap":
                # The null's whole point: score it against the block-out whose CONTROL it got as
                # well as against the one whose image it saw.
                other = blocks[SWAP[kind]]
                swap_cross[kind] = {
                    "control_from": SWAP[kind],
                    "against_control": g4c.fixed_agreement(other["points"],
                                                           agree["points"])["footprint_iou"],
                    "against_image": agree["footprint_iou"]}
            scores[(kind, label)] = {k: v for k, v in agree.items() if k != "points"}
            scores[(kind, label)].update(
                seconds=stamp.get("seconds", 0.0),
                peak=stamp.get("vram", {}).get("comfy_peak", 0),
                rise=stamp.get("vram", {}).get("rise", 0),
                ceiling_iou=block["ceiling"]["iou"],
                ceiling_footprint=block["ceiling"]["footprint_iou"])
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
    print("| block-out | control | footprint IoU as a fraction of the ceiling |")
    print("|---|---|---|")
    for kind, label, s in rows:
        print(f"| {kind} | {label} | "
              f"{s['footprint_iou'] / max(s['ceiling_footprint'], 1e-9):.4f} |")

    def column(label):
        return [s for (_k, lab), s in scores.items() if lab == label]

    for label in COLUMNS:
        got = column(label)
        if got:
            # Mean AND fastest, because the first generation of a session carries the 13.5 GB Omni
            # load and a three-row mean cannot absorb it: the fastest row is the warm cost.
            note(f"{label}, over {len(got)} block-outs",
                 f"footprint IoU {np.mean([s['footprint_iou'] for s in got]):.4f}, IoU "
                 f"{np.mean([s['iou'] for s in got]):.4f}, "
                 f"{np.mean([s['seconds'] for s in got]):.1f} s mean and "
                 f"{min(s['seconds'] for s in got):.1f} s warm, "
                 f"peak {int(np.mean([s['peak'] for s in got]))} MiB, aspect error "
                 f"{np.mean([s['aspect_error'] for s in got]):.3f}")

    # WIRING first. A swapped control is a stronger null than an absent one: both runs load the same
    # model with the same image and the same steps, so the only difference is which block-out the
    # control came from. If the result does not move with it, the control is not reaching the model.
    wired = [(k, scores.get((k, "mesh_geom_voxel voxel")), scores.get((k, "mesh_geom_voxel swap"))) for k in g4c.PROMPTS]
    live = [(k, v, s) for k, v, s in wired if v and s]
    if live:
        held = sum(1 for _k, v, s in live if v["footprint_iou"] > s["footprint_iou"])
        check("the voxel control reaches the model, measured against a swapped control",
              held >= WIRED_THRESHOLD,
              f"{held} of {len(live)}; "
              + "; ".join(f"{k} own {v['footprint_iou']:.4f} against swapped "
                          f"{s['footprint_iou']:.4f}" for k, v, s in live))
        if swap_cross:
            note("and the swapped run follows the CONTROL rather than the image",
                 "; ".join(f"{k} scores {c['against_control']:.4f} against {c['control_from']} "
                           f"(its control) and {c['against_image']:.4f} against {k} (its image)"
                           for k, c in sorted(swap_cross.items())))

    # Then the verdict, on the same rule shape G8 used, so the two are directly comparable.
    pairs = [(k, scores.get((k, "mesh_geom_voxel voxel")), scores.get((k, "mesh_geom_ctrl point"))) for k in g4c.PROMPTS]
    live = [(k, v, p) for k, v, p in pairs if v and p]
    if live:
        wins = sum(1 for _k, v, p in live if v["footprint_iou"] >= p["footprint_iou"] - DRAW)
        faster = min(v["seconds"] for _k, v, _p in live) <= min(p["seconds"]
                                                                for _k, _b, p in live)
        verdict = "voxel" if (wins >= WIN_THRESHOLD and faster) else "point"
        note("VERDICT, footprint IoU, voxel against point",
             f"voxel wins or draws {wins} of {len(live)}; means "
             f"{np.mean([v['footprint_iou'] for _k, v, _p in live]):.4f} against "
             f"{np.mean([p['footprint_iou'] for _k, _b, p in live]):.4f}, warm wall clock "
             f"{min(v['seconds'] for _k, v, _p in live):.1f} s against "
             f"{min(p['seconds'] for _k, _b, p in live):.1f} s, so the rule says "
             f"DEFAULT_CONTROL_MODE = {verdict!r}")
        check("the shipped default is the one the rule chose",
              comfy.DEFAULT_CONTROL_MODE == verdict,
              f"shipped {comfy.DEFAULT_CONTROL_MODE!r}, rule {verdict!r}")
        adopted = {"point": "mesh_geom_ctrl point", "voxel": "mesh_geom_voxel voxel",
                   "bbox": "mesh_geom_bbox bbox"}[comfy.DEFAULT_CONTROL_MODE]
        got = column(adopted)
        if got:
            check(f"the shipped control mode ({adopted}) clears G4c's footprint bar everywhere",
                  all(s["footprint_iou"] > FOOTPRINT_BAR for s in got),
                  "; ".join(f"{k} {s['footprint_iou']:.4f}"
                            for (k, lab), s in scores.items() if lab == adopted))

    with open(os.path.join(OUT, "part_b.json"), "w") as fh:
        json.dump({"grid": {f"{k}::{lab}": s for (k, lab), s in scores.items()},
                   "swap": swap_cross, "input_rotation": probe},
                  fh, indent=2, sort_keys=True, default=str)


# -- Part C: the finished asset ---------------------------------------------------------------------
def part_c(args, reachable, ready):
    section("C. The finished asset from an occupancy grid, through the G3 checks it inherits")
    if not (reachable and ready):
        print("[SKIP] part C needs a server with the Omni pack and its weights")
        return
    kind = "notched"
    block = _prepare(kind)
    height = block["height_m"]
    pack = os.path.join(OUT, "pack")

    raw = os.path.join(GEN, f"{kind}_w7v.glb")
    stamp, ok = _run_cached(
        raw, lambda: comfy.mesh_geom_voxel(block["control"], block["view"], raw, seed=SEED),
        args, "mesh_geom_voxel for the finished asset")
    if not ok:
        return
    note("mesh_geom_voxel", f"{stamp.get('seconds', 0.0):.1f} s")

    staged_dir = os.path.join(GEN, "finish")
    os.makedirs(staged_dir, exist_ok=True)
    simp = os.path.join(staged_dir, "simp.glb")
    tex = os.path.join(staged_dir, "tex.glb")
    t0 = time.time()
    try:
        if args.fresh or not os.path.isfile(simp):
            comfy.mesh_simplify_uv(raw, simp, faces=FACES)
        if args.fresh or not os.path.isfile(tex):
            comfy.mesh_texture(simp, block["view"], tex, seed=SEED, texture_size=1024)
    except comfy.ComfyError as exc:
        check("mesh_simplify_uv and mesh_texture finished the block-out asset", False, str(exc)[:200])
        return
    note("mesh_simplify_uv plus mesh_texture", f"{time.time() - t0:.1f} s")

    exports = comfy.stage_exports({"meta": {"control": block["control"], "control_bbox": None,
                                            "control_mode": "voxel"},
                                   "simplified_mesh": simp, "textured_mesh": tex})
    note("turns to undo per staged file", str(exports))
    g4c.empty_scene()
    report = gen_assets.finish_asset(raw, pack, kind="rocks", name=f"voxel_{kind}",
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
    note("bake_rescale", str(report.get("bake_rescale")))
    normal = (report.get("maps") or {}).get("normal")
    detail = g3.neighbour_detail(normal) if normal else 0.0
    check("the baked normal carries detail", detail > 0.001,
          f"mean absolute neighbour difference {detail:.5f}"
          + ("" if normal else ", and no normal map was written"))
    agree = _score_finished(final, block["points"])
    note("the finished asset against the block-out",
         f"footprint IoU {agree['footprint_iou']:.4f}, IoU {agree['iou']:.4f}, "
         f"aspect {agree['aspect']}")

    raw_agree = _score(raw, block["points"], "raw")
    check("simplify, bake, scale, LODs and BobShade keep the footprint the control achieved",
          agree["footprint_iou"] >= raw_agree["footprint_iou"] - 0.05,
          f"finished {agree['footprint_iou']:.4f} against the raw mesh's "
          f"{raw_agree['footprint_iou']:.4f}")
    with open(os.path.join(OUT, "part_c.json"), "w") as fh:
        json.dump({"report": report, "agreement": agree,
                   "raw_agreement": {k: v for k, v in raw_agree.items() if k != "points"}},
                  fh, indent=2, sort_keys=True, default=str)


def _score_finished(obj, proxy_points):
    return g4c.fixed_agreement(proxy_points, g4c.mesh_points(obj, seed=2))


# -- Part D: transport -------------------------------------------------------------------------------
def part_d(args, reachable, ready):
    section("D. mesh_geom_voxel uploads a mesh, so mesh_geom_bbox keeps the no-ComfyUI-folder fallback to itself")
    if not (reachable and ready):
        print("[SKIP] part D needs a server with the Omni pack and its weights")
        return
    kind = "rock"
    block = _prepare(kind)

    # ONE variable, the G8 pattern: `omni_model_dir` is derived from `comfy_dir` too, so forcing the
    # folder away without holding the weights unbinds them as well and the wrapper starts a 13.5 GB
    # download inside a loader that `/interrupt` does not reach.
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
        error = None
        try:
            comfy.mesh_geom_voxel(block["control"], block["view"],
                                  os.path.join(GEN, "transport_voxel.glb"), seed=SEED, steps=1)
        except comfy.ComfyError as exc:
            error = str(exc)
        check("the voxel route fails without a ComfyUI folder, exactly as the point route does",
              error is not None, (error or "it succeeded").splitlines()[0][:160])
        note("so the transport fallback is still mesh_geom_bbox alone",
             "one Omni mode of three uploads nothing, and it is the one that lost on footprint")
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
    parser.add_argument("--no-bbox", action="store_true",
                        help="drop the mesh_geom_bbox column, which G8 already measured")
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    args = parser.parse_args(argv)
    os.makedirs(GEN, exist_ok=True)

    section("G9: Omni's voxel control mode, and the close of D12")
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
    if FAILURES:
        print(f"{len(FAILURES)} failure(s): " + "; ".join(FAILURES))
    else:
        print("no failures")
    sys.exit(1 if FAILURES else 0)


if __name__ == "__main__":
    main()
