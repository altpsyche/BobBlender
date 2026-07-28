"""Headless measurement: Omni, `mesh_geom_ctrl` and `export_control` (docs/GENERATION.md).

The question the phase exists to answer is narrow and it is not "does it generate": it is **does a
block-out proxy still recognisably own the result**. So every figure here is scored against the
BLOCK-OUT, with no rotation search, because an asset that fits the layout only after being turned
does not fit the layout.

  A. **Which way does each exporter face?** A load-and-export round trip through a gate-only graph
     with no model in it, on an asymmetric block-out, over all 24 axis-aligned rotations. That pins
     `gen_assets.CONTROL_RETURN_TURN` by measurement rather than by reading the exporter's source,
     and it is cheap enough to run every time.
  B. **`mesh_geom_ctrl` against `mesh_geom_mv_trellis` on the same block-outs.** Three proxies, one of them asymmetric front-to-back,
     each conditioning Omni on its shape (`mesh_geom_ctrl`) and TRELLIS.2 on four Blender-rendered
     views of it (`mesh_geom_mv_trellis`, the multi-view baseline). Scored WITHOUT the rotation
     search: voxel IoU, Chamfer, the XY-projected footprint IoU that is what "drops into a layout"
     actually means, and the bbox aspect ratio against the proxy's. Wall clock and per-process VRAM
     beside each.
  C. **Does the finished asset still pass the checks it inherits from the asset gate?** One block-out through
     `mesh_geom_ctrl`, `mesh_simplify_uv` and `mesh_texture` and then all of steps 6 to 8: face
     budget, UV overlap, height, origin, LODs, BobShader.
  D. **Can the Omni wrapper share a session with SDXL?** The card is 16.3 GB and the stylise gate measured the
     stylise route peaking at 14.2 of it, so this is a residency question with a yes or no answer.

    ~/.steam/steam/steamapps/common/Blender/blender --background --factory-startup \\
        --python tools/scripts/headless_gen_blockout_control.py -- [--part a,b,c,d] [--fresh] [--no-baseline]

Reachability-gated: with no server, or with the Omni pack or its weights absent, every generation
half prints SKIP and exits 0, which is itself the check that no wrapper is ever required. Generated
meshes cache WITH their timing and VRAM under `_generated/comfy_g4c_check/gen/`, so a re-measured
table is not a table of zeros. Exit 0 = nothing failed.
"""

import argparse
import json
import math
import os
import subprocess
import sys
import threading
import time

import bpy
import numpy as np

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(REPO, "blender", "extensions"))

from bob_blender_tools.core import (  # noqa: E402
    comfy,
    gen_assets,
    gen_views,
    materials,
    proxies,
)

FAILURES = []
OUT = os.path.join(REPO, "_generated", "comfy_g4c_check")
GEN = os.path.join(OUT, "gen")
DUMP = os.path.join(REPO, "tools", "tests", "data", "object_info_min.json")

PROMPTS = {"rock": "a weathered granite boulder, mossy and chipped",
           "tree": "a windswept pine tree, dense needles, bare lower trunk",
           "notched": "a broken stone monument block, chipped edges, one deep alcove"}
SEED = 4242
RESOLUTION = 1024
FACES = 4000
GRID = 48
SAMPLES = 8000

# Every class `mesh_geom_ctrl` needs that is not in ComfyUI core or TRELLIS.2. Absent means SKIP,
# not FAIL.
OMNI_CLASSES = ("Hy3DOmniLoadPipeline", "Hy3DOmniPointGenerate")


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


def empty_scene():
    bpy.ops.wm.read_factory_settings(use_empty=True)


# -- VRAM, per process and summed over the ComfyUI family -----------------------------------------
_OURS = {os.getpid()}


def _gpu_sample():
    try:
        card = subprocess.run(["nvidia-smi", "--query-gpu=memory.used",
                               "--format=csv,noheader,nounits"],
                              capture_output=True, text=True, timeout=10).stdout.strip()
        apps = subprocess.run(["nvidia-smi", "--query-compute-apps=pid,used_memory",
                               "--format=csv,noheader,nounits"],
                              capture_output=True, text=True, timeout=10).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None, {}
    procs = {}
    for line in apps.splitlines():
        bits = [b.strip() for b in line.split(",")]
        if len(bits) == 2 and bits[0].isdigit() and bits[1].isdigit():
            procs[int(bits[0])] = int(bits[1])
    return (int(card.splitlines()[0]) if card else None), procs


class Vram:
    """Peak VRAM across a job, sampled from a thread: per process, summed over the ComfyUI family,
    with the RISE over this stage's own baseline reported beside the absolute peak. Same class as
    the one-shot-against-staged and stylise gates, because the numbers have to be comparable with
    theirs."""

    def __init__(self, interval=0.5):
        self.interval = interval
        self.card_peak = self.comfy_peak = 0
        self.card_start = self.comfy_start = 0
        self._stop = threading.Event()
        self._thread = None

    def _family(self, procs):
        return sum(mib for pid, mib in procs.items() if pid not in _OURS)

    def _loop(self):
        while not self._stop.is_set():
            card, procs = _gpu_sample()
            if card is not None:
                self.card_peak = max(self.card_peak, card)
                self.comfy_peak = max(self.comfy_peak, self._family(procs))
            self._stop.wait(self.interval)

    def __enter__(self):
        card, procs = _gpu_sample()
        self.card_start = self.card_peak = card or 0
        self.comfy_start = self.comfy_peak = self._family(procs)
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *exc):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)
        return False

    def report(self):
        return {"card_start": self.card_start, "card_peak": self.card_peak,
                "comfy_start": self.comfy_start, "comfy_peak": self.comfy_peak,
                "rise": self.comfy_peak - self.comfy_start}


# -- Shape maths, scored where it lands -----------------------------------------------------------
def mesh_points(obj, count=SAMPLES, seed=0):
    """Area-weighted surface samples, centred and divided by ONE scale so the aspect ratio survives.

    The single scale matters here in a way it did not for the stylise gate: a footprint comparison
    is a comparison of proportions, so normalising per axis would erase exactly what is being
    measured.
    """
    mesh = obj.data
    mesh.calc_loop_triangles()
    verts = np.empty(len(mesh.vertices) * 3, dtype="float64")
    mesh.vertices.foreach_get("co", verts)
    verts = verts.reshape(-1, 3)
    world = np.array(obj.matrix_world, dtype="float64")
    verts = verts @ world[:3, :3].T + world[:3, 3]
    tris = np.array([list(t.vertices) for t in mesh.loop_triangles], dtype="int64")
    if not len(tris):
        return np.zeros((0, 3))
    a, b, c = verts[tris[:, 0]], verts[tris[:, 1]], verts[tris[:, 2]]
    areas = 0.5 * np.linalg.norm(np.cross(b - a, c - a), axis=1)
    total = areas.sum()
    if total <= 0:
        return np.zeros((0, 3))
    rng = np.random.default_rng(seed)
    pick = rng.choice(len(tris), size=count, p=areas / total)
    u, v = rng.random((count, 1)), rng.random((count, 1))
    over = (u + v) > 1
    u[over], v[over] = 1 - u[over], 1 - v[over]
    pts = a[pick] + u * (b[pick] - a[pick]) + v * (c[pick] - a[pick])
    centre = (pts.min(axis=0) + pts.max(axis=0)) * 0.5
    scale = max((pts.max(axis=0) - pts.min(axis=0)).max(), 1e-9)
    return (pts - centre) / scale


def voxelise(points, grid=GRID):
    """Surface-occupancy voxels. Surface, not solid: an open mesh has no inside."""
    idx = np.clip(((points + 0.5) * grid).astype("int32"), 0, grid - 1)
    vol = np.zeros((grid, grid, grid), dtype=bool)
    vol[idx[:, 0], idx[:, 1], idx[:, 2]] = True
    return vol


def footprint(points, grid=GRID):
    """XY occupancy: the ground plan a layout was composed around, with height thrown away."""
    idx = np.clip(((points[:, :2] + 0.5) * grid).astype("int32"), 0, grid - 1)
    plan = np.zeros((grid, grid), dtype=bool)
    plan[idx[:, 0], idx[:, 1]] = True
    return plan


def iou(a, b):
    union = np.logical_or(a, b).sum()
    return float(np.logical_and(a, b).sum() / union) if union else 0.0


def chamfer(a, b, chunk=512):
    """Symmetric mean nearest-neighbour distance between two point sets."""
    def one_way(src, dst):
        total = 0.0
        for start in range(0, len(src), chunk):
            block = src[start:start + chunk]
            d = np.linalg.norm(block[:, None, :] - dst[None, :, :], axis=2)
            total += d.min(axis=1).sum()
        return total / max(len(src), 1)
    return float(0.5 * (one_way(a, b) + one_way(b, a)))


def extents(points):
    return points.max(axis=0) - points.min(axis=0)


def fixed_agreement(proxy_points, candidate_points, grid=GRID):
    """Every figure for one candidate, scored where the candidate LANDED.

    No rotation search, and that is the whole methodological difference from `shape_agreement`.
    That was asking whether a model had understood a shape, so the exporter's frame was noise to be
    searched out. This is asking whether the result can be dropped into a composed layout, and a
    result that needs turning first cannot be, so the orientation is part of the answer.
    """
    proxy_extent = extents(proxy_points)
    cand_extent = extents(candidate_points)
    ratio = cand_extent / np.maximum(proxy_extent, 1e-9)
    return {"iou": iou(voxelise(proxy_points, grid), voxelise(candidate_points, grid)),
            "footprint_iou": iou(footprint(proxy_points, grid), footprint(candidate_points, grid)),
            "chamfer": chamfer(proxy_points[:2500], candidate_points[:2500]),
            "aspect": [round(float(r), 4) for r in ratio],
            "aspect_error": float(np.abs(ratio - 1.0).max())}


AXIS_MAPS = [(perm, signs)
             for perm in ((0, 1, 2), (0, 2, 1), (1, 0, 2), (1, 2, 0), (2, 0, 1), (2, 1, 0))
             for signs in ((1, 1, 1), (1, 1, -1), (1, -1, 1), (1, -1, -1),
                           (-1, 1, 1), (-1, 1, -1), (-1, -1, 1), (-1, -1, -1))]


def best_axis_map(reference, candidate, grid=GRID):
    """The axis permutation and sign flips that align `candidate` with `reference`, and its IoU.

    Used to PIN a convention in part A, and in part B only as a diagnostic that separates "this
    result is turned" from "this result is a different shape". Never as a score: see
    `fixed_agreement` for why a footprint gate cannot search over rotations.
    """
    ref_vox = voxelise(reference, grid)
    best = None
    for perm, signs in AXIS_MAPS:
        moved = candidate[:, list(perm)] * np.array(signs)
        score = iou(ref_vox, voxelise(moved, grid))
        if best is None or score > best[0]:
            best = (score, perm, signs, moved)
    moved = best[3]
    return {"iou": best[0], "perm": best[1], "signs": best[2], "points": moved,
            "proper": int(np.linalg.det(np.eye(3)[list(best[1])] * np.array(best[2])[:, None])) == 1}


def _stamp(target, data=None):
    """Read or write the timing and VRAM beside a cached artifact, so a rerun reports what the
    generating run measured rather than what the cache cost."""
    path = target + ".json"
    if data is None:
        try:
            with open(path) as fh:
                return json.load(fh) or {}
        except (OSError, ValueError):
            return {}
    with open(path, "w") as fh:
        json.dump(data, fh, indent=2, sort_keys=True, default=str)
    return data


# -- The block-outs ------------------------------------------------------------------------------
def _sun_and_world(scene, strength=4.0):
    light = bpy.data.objects.new("Sun", bpy.data.lights.new("Sun", "SUN"))
    light.data.energy = strength
    light.rotation_euler = (math.radians(55), 0, math.radians(35))
    scene.collection.objects.link(light)
    world = bpy.data.worlds.new("World")
    world.use_nodes = True
    world.node_tree.nodes["Background"].inputs[1].default_value = 0.6
    scene.world = world


def notched_blockout():
    """A block-out whose BACK is not inferable from its front: a plain face forward, a deep alcove
    and an off-centre buttress behind. The asymmetric case the gate is required to include, and
    it is also the only one of the three that can distinguish a proper rotation from a mirror."""
    bpy.ops.mesh.primitive_cube_add(size=1.0, location=(0, 0, 0.55))
    block = bpy.context.active_object
    block.name = "Blockout_Notched"
    block.scale = (0.55, 0.42, 0.55)
    bpy.ops.object.transform_apply(scale=True)

    bpy.ops.mesh.primitive_cube_add(size=1.0, location=(0.1, 0.3, 0.55))
    alcove = bpy.context.active_object
    alcove.scale = (0.3, 0.28, 0.3)
    bpy.ops.object.transform_apply(scale=True)
    bpy.ops.mesh.primitive_cube_add(size=1.0, location=(-0.42, 0.34, 0.34))
    buttress = bpy.context.active_object
    buttress.scale = (0.16, 0.16, 0.34)
    bpy.ops.object.transform_apply(scale=True)

    cut = block.modifiers.new("Alcove", "BOOLEAN")
    cut.operation, cut.object = "DIFFERENCE", alcove
    add = block.modifiers.new("Buttress", "BOOLEAN")
    add.operation, add.object = "UNION", buttress
    bpy.context.view_layer.objects.active = block
    bpy.ops.object.modifier_apply(modifier="Alcove")
    bpy.ops.object.modifier_apply(modifier="Buttress")
    bpy.data.objects.remove(alcove, do_unlink=True)
    bpy.data.objects.remove(buttress, do_unlink=True)
    return block


def blockout(kind):
    """One block-out proxy in an empty scene, at a real metre scale, ready to export and to render.

    Two of the three are the SHIPPED proxies (`core.proxies`), not gate fixtures, because the whole
    claim is about the block-outs an artist already has in a layout.
    """
    empty_scene()
    scene = bpy.context.scene
    if kind == "notched":
        obj = notched_blockout()
    else:
        source = {"rock": ("rocks", "Rock_B"), "tree": ("trees", "Tree_A")}[kind]
        collection = proxies.ensure_collection(source[0])
        obj = collection.objects[source[1]].copy()
        obj.data = obj.data.copy()
        obj.name = f"Blockout_{kind.capitalize()}"
        scene.collection.objects.link(obj)
    if not obj.data.materials:
        mat = bpy.data.materials.new(f"M_Blockout_{kind}")
        mat.use_nodes = True
        mat.node_tree.nodes["Principled BSDF"].inputs["Base Color"].default_value = \
            (0.5, 0.48, 0.44, 1.0)
        obj.data.materials.append(mat)
    _sun_and_world(scene)
    bpy.context.view_layer.update()
    return obj


# -- A gate-only round trip, with no model in it --------------------------------------------------
# Load a mesh, export it, and read it back. Nothing generates, so it costs a second and measures
# exactly one thing: what the EXPORTER does to a subject's orientation. It still goes through
# `comfy.check`, so it cannot smuggle in a cloud node any more than a shipped graph can.
ROUND_TRIP = {
    "1": {"class_type": "Trellis2LoadMesh", "inputs": {"mesh_path": ""},
          "_meta": {"title": "BOB_CONTROL"}},
    "2": {"class_type": "Trellis2ExportTrimesh",
          "inputs": {"trimesh": ["1", 0], "filename_prefix": "bob_g4c_round_trip",
                     "file_format": "glb"},
          "_meta": {"title": "BOB_OUT"}},
    "3": {"class_type": "Preview3D", "inputs": {"model_file": ["2", 0]},
          "_meta": {"title": "BOB_VIEW"}},
}


def omni_available(info):
    missing = [c for c in OMNI_CLASSES if c not in info]
    weights = comfy.omni_model_dir()
    return (not missing) and bool(weights), missing, weights


# -- Part A: the orientation convention, pinned ---------------------------------------------------
def part_a(args, reachable):
    section("A. Which way each exporter faces, measured on an asymmetric block-out")
    obj = blockout("notched")
    proxy_points = mesh_points(obj, seed=1)
    # The ceiling first, because without it every IoU below reads as a failure: a surface-voxel IoU
    # is strict, and two point samples of the SAME mesh do not score 1.0.
    ceiling = fixed_agreement(proxy_points, mesh_points(obj, seed=99))
    note("block-out", f"{gen_assets.face_count(obj)} faces, "
                      f"{tuple(round(d, 3) for d in obj.dimensions)} m, footprint ratio "
                      f"{tuple(round(r, 3) for r in gen_assets.footprint_ratio(obj))}")
    note("self-agreement ceiling", f"IoU {ceiling['iou']:.4f}, footprint IoU "
                                   f"{ceiling['footprint_iou']:.4f}")

    control = os.path.join(GEN, "orient_control.glb")
    exported = gen_assets.export_control(obj, control)
    check("export_control writes a unit-normalised control mesh",
          os.path.isfile(control) and abs(exported["scale"] - max(obj.dimensions)) < 1e-5,
          f"scale {exported['scale']:.4f}, height {exported['height_m']:.4f} m, "
          f"{exported['points']} points")

    # The round trip Bob's own export and import make on their own, with no server in it: this is
# the baseline the exporter's turn is measured against.
    empty_scene()
    local = gen_assets.import_glb(control, name="Local")
    local_map = best_axis_map(proxy_points, mesh_points(local, seed=2))
    check("Bob's own glTF export and import round trip is orientation-preserving",
          local_map["perm"] == (0, 1, 2) and tuple(local_map["signs"]) == (1, 1, 1),
          f"IoU {local_map['iou']:.4f} at identity against a {ceiling['iou']:.4f} ceiling, "
          f"best map {local_map['perm']} {local_map['signs']}")

    if not reachable:
        print("[SKIP] no ComfyUI server: the exporter half of part A needs one")
        return
    graph = json.loads(json.dumps(ROUND_TRIP))
    graph["1"]["inputs"]["mesh_path"] = comfy.upload_mesh(control)
    target = os.path.join(GEN, "orient_round_trip.glb")
    try:
        data, info = comfy.generate_mesh((graph, {}), {}, timeout=300, preflight_graph=False)
    except comfy.ComfyError as exc:
        check("Trellis2 round trip returned a mesh", False, str(exc)[:160])
        return
    with open(target, "wb") as fh:
        fh.write(data)
    note("round trip", f"{info['seconds']:.1f} s, {len(data)} bytes")

    empty_scene()
    tripped = gen_assets.import_glb(target, name="Tripped")
    tripped_points = mesh_points(tripped, seed=3)
    best = best_axis_map(proxy_points, tripped_points)
    before = fixed_agreement(proxy_points, tripped_points)
    note("Trellis2ExportTrimesh, best axis map against the block-out",
         f"IoU {best['iou']:.4f} at perm {best['perm']} signs {best['signs']}, "
         f"{'a proper rotation' if best['proper'] else 'a MIRROR'}")
    note("the same round trip left alone",
         f"IoU {before['iou']:.4f}, footprint {before['footprint_iou']:.4f}, "
         f"aspect {before['aspect']}")

    # And the claim that matters: the pinned constant undoes it, with nothing searched. Read against
    # the ceiling, not against 1.0.
    empty_scene()
    fixed = gen_assets.import_glb(target, name="Fixed", orient=gen_assets.CONTROL_RETURN_TURN)
    after = fixed_agreement(proxy_points, mesh_points(fixed, seed=3))
    check(f"CONTROL_RETURN_TURN {gen_assets.CONTROL_RETURN_TURN} is the exporter's turn undone",
          after["iou"] > 0.9 * ceiling["iou"]
          and after["footprint_iou"] > 0.9 * ceiling["footprint_iou"]
          and after["aspect_error"] < 0.02,
          f"IoU {after['iou']:.4f} of a {ceiling['iou']:.4f} ceiling, footprint "
          f"{after['footprint_iou']:.4f} of {ceiling['footprint_iou']:.4f}, aspect "
          f"{after['aspect']}, against {before['iou']:.4f} / {before['footprint_iou']:.4f} "
          f"untouched")
    _stamp(os.path.join(OUT, "part_a"), {"exporter_map": best, "before": before, "after": after,
                                         "local_map": local_map, "ceiling": ceiling})


# -- Part B: `mesh_geom_ctrl` against the `mesh_geom_mv_trellis` baseline
# ----------------------------------------------------------
def views_of(obj, out_dir):
    """Four cardinal views at a low elevation, in the order both multi-view graphs' sockets name."""
    views = gen_views.turntable_views(obj, out_dir, count=4, elevation=10.0, extra_elevations=(),
                                      resolution=RESOLUTION, samples=32, engine="BLENDER_EEVEE",
                                      stem="view")
    return [v["beauty"] for v in views[:4]]


def generate_cached(target, run, fresh):
    """Run `run()` unless the mesh is cached, and keep its timing and VRAM beside it either way."""
    if fresh and os.path.isfile(target):
        os.remove(target)
    if os.path.isfile(target):
        stamp = _stamp(target)
        if stamp:
            return stamp
    try:
        with Vram() as sampler:
            info = run()
    except comfy.ComfyError as exc:
        return {"error": str(exc)[:200]}
    return _stamp(target, {"seconds": info["seconds"], "vram": sampler.report()})


def part_b(args, reachable, ready):
    section("B. mesh_geom_ctrl against the mesh_geom_mv_trellis baseline, on three block-outs, scored where it landed")
    if not reachable or not ready:
        print("[SKIP] part B needs a server with the Omni pack and its weights")
        return
    rows, scores = [], {}
    for kind, prompt in PROMPTS.items():
        obj = blockout(kind)
        proxy_points = mesh_points(obj, seed=1)
        # The ceiling, so the IoU column can be read: a surface-voxel IoU is strict and two samples
        # of the SAME mesh do not score 1.0.
        ceiling = fixed_agreement(proxy_points, mesh_points(obj, seed=99))
        control = os.path.join(GEN, f"{kind}_control.glb")
        exported = gen_assets.export_control(obj, control)
        views = views_of(obj, os.path.join(GEN, f"{kind}_views"))
        note(f"{kind}", f"{gen_assets.face_count(obj)} faces, {exported['height_m']:.3f} m tall, "
                        f"ceiling IoU {ceiling['iou']:.4f} / footprint "
                        f"{ceiling['footprint_iou']:.4f}, four views rendered")

        runs = {"mesh_geom_ctrl": (os.path.join(GEN, f"{kind}_w7.glb"),
                       lambda c=control, v=views, k=kind: comfy.mesh_geom_ctrl(
                           c, v[0], os.path.join(GEN, f"{k}_w7.glb"), seed=SEED),
                       gen_assets.CONTROL_RETURN_TURN)}
        if not args.no_baseline:
            runs["mesh_geom_mv_trellis"] = (os.path.join(GEN, f"{kind}_w6t.glb"),
                           lambda v=views, k=kind: comfy.mesh_geom_mv_trellis(
                               v, os.path.join(GEN, f"{k}_w6t.glb"), seed=SEED, remesh=True),
                           None)
        for label, (target, run, orient) in runs.items():
            stamp = generate_cached(target, run, args.fresh)
            if stamp.get("error") or not os.path.isfile(target):
                check(f"{kind} {label} generated a mesh", False, stamp.get("error", "no file"))
                continue
            empty_scene()
            got = gen_assets.import_glb(target, name=f"{kind}_{label}", orient=orient)
            gen_assets.weld(got)
            points = mesh_points(got, seed=2)
            agree = fixed_agreement(proxy_points, points)
            # The diagnostic that keeps the comparison fair: how the same result would score if it
# were allowed to be turned first. `mesh_geom_mv_trellis` was never asked to preserve an
# orientation, so without this column its score reads as a geometry failure when part of
# it is a frame.
            best = best_axis_map(proxy_points, points)
            at_best = fixed_agreement(proxy_points, best["points"])
            agree["best_iou"] = best["iou"]
            agree["best_footprint_iou"] = at_best["footprint_iou"]
            agree["best_map"] = f"{best['perm']}{best['signs']}"
            scores[(kind, label)] = dict(agree, faces=gen_assets.face_count(got),
                                         seconds=stamp.get("seconds", 0.0),
                                         peak=stamp.get("vram", {}).get("comfy_peak", 0),
                                         rise=stamp.get("vram", {}).get("rise", 0),
                                         ceiling_iou=ceiling["iou"],
                                         ceiling_footprint=ceiling["footprint_iou"])
            rows.append((kind, label, scores[(kind, label)]))

    print()
    print("| block-out | route | wall s | peak MiB | rise | faces | IoU | footprint IoU "
          "| Chamfer | aspect error |")
    print("|---|---|---|---|---|---|---|---|---|---|")
    for kind, label, s in rows:
        print(f"| {kind} | {label} | {s['seconds']:.1f} | {s['peak']} | {s['rise']} | "
              f"{s['faces']} | {s['iou']:.4f} | {s['footprint_iou']:.4f} | {s['chamfer']:.4f} | "
              f"{s['aspect_error']:.3f} |")
    print()
    print("| block-out | route | IoU as landed | IoU if turned first | footprint as landed "
          "| footprint if turned | best map |")
    print("|---|---|---|---|---|---|---|")
    for kind, label, s in rows:
        print(f"| {kind} | {label} | {s['iou']:.4f} | {s['best_iou']:.4f} | "
              f"{s['footprint_iou']:.4f} | {s['best_footprint_iou']:.4f} | {s['best_map']} |")
    print()
    print("| block-out | ceiling IoU | ceiling footprint IoU |")
    print("|---|---|---|")
    for kind in PROMPTS:
        got = next((s for (k, _l), s in scores.items() if k == kind), None)
        if got:
            print(f"| {kind} | {got['ceiling_iou']:.4f} | {got['ceiling_footprint']:.4f} |")

    for kind in PROMPTS:
        w7, w6t = scores.get((kind, "mesh_geom_ctrl")), scores.get((kind, "mesh_geom_mv_trellis"))
        if w7 and w6t:
            note(f"{kind}, `mesh_geom_ctrl` against `mesh_geom_mv_trellis`",
                 f"IoU {w7['iou']:.4f} against {w6t['iou']:.4f}, footprint "
                 f"{w7['footprint_iou']:.4f} against {w6t['footprint_iou']:.4f}, Chamfer "
                 f"{w7['chamfer']:.4f} against {w6t['chamfer']:.4f}, "
                 f"{w7['seconds']:.1f} s against {w6t['seconds']:.1f} s")
    w7s = [s for (_k, label), s in scores.items() if label == "mesh_geom_ctrl"]
    w6ts = [s for (_k, label), s in scores.items() if label == "mesh_geom_mv_trellis"]
    if w7s:
        check("every mesh_geom_ctrl result keeps most of the block-out's footprint where it landed",
              all(s["footprint_iou"] > 0.5 for s in w7s),
              "; ".join(f"{k} {s['footprint_iou']:.4f}"
                        for (k, label), s in scores.items() if label == "mesh_geom_ctrl"))
        check("every mesh_geom_ctrl result keeps the block-out's proportions to within a third",
              all(s["aspect_error"] < 0.34 for s in w7s),
              "; ".join(f"{k} {s['aspect']}" for (k, label), s in scores.items() if label == "mesh_geom_ctrl"))
    if w7s and w6ts:
        won = sum(1 for kind in PROMPTS
                  if scores.get((kind, "mesh_geom_ctrl")) and scores.get((kind, "mesh_geom_mv_trellis"))
                  and scores[(kind, "mesh_geom_ctrl")]["footprint_iou"]
                  > scores[(kind, "mesh_geom_mv_trellis")]["footprint_iou"])
        note("VERDICT, footprint IoU, mesh_geom_ctrl against mesh_geom_mv_trellis",
             f"`mesh_geom_ctrl` wins {won} of {len(w6ts)}; means "
             f"`mesh_geom_ctrl` {np.mean([s['footprint_iou'] for s in w7s]):.4f} against "
             f"`mesh_geom_mv_trellis` {np.mean([s['footprint_iou'] for s in w6ts]):.4f}, wall clock "
             f"{np.mean([s['seconds'] for s in w7s]):.1f} s against "
             f"{np.mean([s['seconds'] for s in w6ts]):.1f} s")
    with open(os.path.join(OUT, "part_b.json"), "w") as fh:
        json.dump({f"{k}_{lab}": s for (k, lab), s in scores.items()}, fh, indent=2,
                  sort_keys=True, default=str)


# -- Part C: the finished asset, through the checks it inherits from the asset gate ----------------
def part_c(args, reachable, ready):
    section("C. The finished asset from a block-out, through the asset checks it inherits")
    if not reachable or not ready:
        print("[SKIP] part C needs a server with the Omni pack and its weights")
        return
    kind = "notched"
    obj = blockout(kind)
    proxy_points = mesh_points(obj, seed=1)
    height = float(obj.dimensions[2])
    pack = os.path.join(OUT, "pack")
    control = os.path.join(GEN, f"{kind}_control.glb")
    if not os.path.isfile(control):
        gen_assets.export_control(obj, control)
    views = views_of(obj, os.path.join(GEN, f"{kind}_views"))

    staged_dir = os.path.join(GEN, "finish")
    os.makedirs(staged_dir, exist_ok=True)
    raw = os.path.join(GEN, f"{kind}_w7.glb")
    if not os.path.isfile(raw):
        stamp = generate_cached(raw, lambda: comfy.mesh_geom_ctrl(control, views[0], raw, seed=SEED),
                                args.fresh)
        if stamp.get("error"):
            check("mesh_geom_ctrl generated a mesh to finish", False, stamp["error"])
            return
    simp = os.path.join(staged_dir, "simp.glb")
    tex = os.path.join(staged_dir, "tex.glb")
    t0 = time.time()
    try:
        if not os.path.isfile(simp):
            comfy.mesh_simplify_uv(raw, simp, faces=FACES)
        if not os.path.isfile(tex):
            comfy.mesh_texture(simp, views[0], tex, seed=SEED, texture_size=1024)
    except comfy.ComfyError as exc:
        check("mesh_simplify_uv and mesh_texture finished the block-out asset", False, str(exc)[:200])
        return
    note("mesh_simplify_uv plus mesh_texture", f"{time.time() - t0:.1f} s")

    # Through the SHIPPED function that decides the per-file turns, not a hand-written mapping, so
    # the gate fails if `stage_exports` and the chain ever disagree.
    exports = comfy.stage_exports({"meta": {"control": control}, "simplified_mesh": simp,
                                   "textured_mesh": tex})
    note("turns to undo per staged file", str(exports))
    empty_scene()
    report = gen_assets.finish_asset(raw, pack, kind="rocks", name=f"blockout_{kind}",
                                    height_m=height, faces=FACES, exports=exports,
                                    simplify_pass=simp, texture_pass=tex)
    obj_final = gen_assets.import_generated(report["name"], kind="rocks", pack_dir=pack)
    dims = gen_assets.dimensions(obj_final)
    low, high = gen_assets.bbox_world(obj_final)
    check("face count inside the budget", report["faces"] <= FACES,
          f"{report['faces']} of {FACES}")
    check("UVs exist and do not overlap", report["uv_overlap"] < 0.001,
          f"overlap {report['uv_overlap']}")
    check("bbox height equals the block-out's height", abs(dims[2] - height) < 1e-3,
          f"{dims[2]:.4f} m against {height:.4f} m")
    check("origin sits at the base", abs(low[2] - obj_final.location[2]) < 1e-3,
          f"base {low[2]:.4f}, origin {obj_final.location[2]:.4f}")
    check("the LOD chain exists", len(report["lod_faces"]) >= 3, str(report["lod_faces"]))
    # Omni returns geometry with NO material, so the colour roles have no dense mesh to transfer
# from and have to come from the low mesh's own `mesh_texture` texture instead. Without that
# they were silently absent and the asset shipped grey.
    check("the mesh_texture albedo reached the finished asset",
          "basecolor" in (report.get("maps") or {}),
          "maps " + ", ".join(sorted((report.get("maps") or {}))))
    check("the material is a BobShader",
          materials.master_type(obj_final.active_material) is not None,
          str(materials.master_type(obj_final.active_material)))
    # And the point of the whole phase: the FINISHED asset, not just the raw mesh, still owns the
    # block-out's ground plan.
    agree = fixed_agreement(proxy_points, mesh_points(obj_final, seed=2))
    check("the finished asset still keeps the block-out's footprint",
          agree["footprint_iou"] > 0.5,
          f"footprint IoU {agree['footprint_iou']:.4f}, IoU {agree['iou']:.4f}, "
          f"aspect {agree['aspect']}")
    with open(os.path.join(OUT, "part_c.json"), "w") as fh:
        json.dump({"report": report, "agreement": agree}, fh, indent=2, sort_keys=True, default=str)


# -- Part D: can Omni share the card with SDXL ----------------------------------------------------
def part_d(args, reachable, ready):
    section("D. Residency: can the Omni wrapper share a session with SDXL")
    if not reachable or not ready:
        print("[SKIP] part D needs a server with the Omni pack and its weights")
        return
    card, _procs = _gpu_sample()
    note("card", f"{card} MiB in use right now")
    comfy.free()
    time.sleep(6.0)

    subject = os.path.join(GEN, "residency_subject.png")
    control = os.path.join(GEN, "notched_control.glb")
    if not os.path.isfile(control):
        gen_assets.export_control(blockout("notched"), control)
    with Vram() as sampler:
        try:
            comfy.subject_image(PROMPTS["notched"], subject, seed=SEED, size=1024)
        except comfy.ComfyError as exc:
            check("mesh_subject ran first, leaving SDXL resident", False, str(exc)[:160])
            return
    sdxl = sampler.report()
    note("after mesh_subject (SDXL resident)", f"peak {sdxl['comfy_peak']} MiB, rise {sdxl['rise']}")

    target = os.path.join(GEN, "residency_w7.glb")
    if os.path.isfile(target):
        os.remove(target)
    with Vram() as sampler:
        try:
            info = comfy.mesh_geom_ctrl(control, subject, target, seed=SEED)
            failure = None
        except comfy.ComfyError as exc:
            info, failure = None, str(exc)[:300]
    shared = sampler.report()
    if failure:
        note("mesh_geom_ctrl with SDXL still resident", f"FAILED: {failure}")
        comfy.free()
        time.sleep(6.0)
        with Vram() as sampler:
            info = comfy.mesh_geom_ctrl(control, subject, target, seed=SEED)
        alone = sampler.report()
        check("mesh_geom_ctrl needs a /free between stages, and one is enough", os.path.isfile(target),
              f"shared peak {shared['comfy_peak']} MiB then failed; alone "
              f"{alone['comfy_peak']} MiB in {info['seconds']:.1f} s")
        _stamp(os.path.join(OUT, "part_d"), {"sdxl": sdxl, "shared": shared, "alone": alone,
                                             "resident": False})
        return
    check("mesh_geom_ctrl is resident-safe: it runs with SDXL still loaded", os.path.isfile(target),
          f"peak {shared['comfy_peak']} MiB of a 16,303 MiB card, rise {shared['rise']}, "
          f"{info['seconds']:.1f} s")

    # The question the other way round, and it is the one that bites: the stylise gate measured that
# route peaking at 14,194 MiB, and Omni's ~7 to 8 GB cannot be evicted by ComfyUI's model
# management because the wrapper caches its pipeline in a module-level dict. `POST /free` cannot
# reach it.
    comfy.free()
    time.sleep(6.0)
    card_after_free, procs = _gpu_sample()
    held = sum(mib for pid, mib in procs.items() if pid not in _OURS)
    note("after a /free with Omni resident", f"{held} MiB still held by the ComfyUI family "
                                             f"(the card reads {card_after_free})")
    styled = os.path.join(GEN, "residency_styled.png")
    with Vram() as sampler:
        try:
            styled_info = comfy.stylize_render(subject, styled,
                                               "painted concept art, warm evening light",
                                               seed=SEED, size=1024)
            stylise_error = None
        except comfy.ComfyError as exc:
            styled_info, stylise_error = {}, str(exc)[:300]
    stylise = dict(sampler.report(), seconds=styled_info.get("seconds", 0.0))
    if stylise_error:
        note("VERDICT on sharing a session",
             f"the stylise route FAILED with Omni resident: {stylise_error}")
        check("the stylise route needs a server restart, not a /free, once Omni has run",
              True, f"held {held} MiB after /free, stylise peaked {stylise['comfy_peak']}")
    else:
        note("VERDICT on sharing a session",
             f"the stylise route survives with Omni resident: peak {stylise['comfy_peak']} MiB of "
             f"16,303, rise {stylise['rise']} into the {16303 - held} MiB Omni left free, "
             f"{stylise['seconds']:.1f} s against 10.3 to 10.8 s and 14,194 MiB measured alone")
    _stamp(os.path.join(OUT, "part_d"), {"sdxl": sdxl, "shared": shared, "resident": True,
                                         "held_after_free": held, "stylise": stylise,
                                         "stylise_error": stylise_error})


# -- main ----------------------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--part", default="a,b,c,d")
    parser.add_argument("--fresh", action="store_true")
    parser.add_argument("--no-baseline", action="store_true",
                        help="skip mesh_geom_mv_trellis, which is the slow half of part B")
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    args = parser.parse_args(argv)

    os.makedirs(GEN, exist_ok=True)
    ok, detail = comfy.reachable()
    note("ComfyUI", detail if ok else f"not reachable ({detail[:70]})")
    checkout = os.environ.get("BOB_COMFY_DIR", os.path.expanduser("~/dev/ComfyUI"))
    if os.path.isdir(checkout):
        comfy.set_pref_comfy_dir(checkout)
        note("mesh transport", f"local copy into {checkout}/input/3d")
    ready = False
    if ok:
        ready, missing, weights = omni_available(comfy.object_info())
        note("Omni", f"pack {'present' if not missing else 'MISSING ' + ','.join(missing)}, "
                     f"weights {weights or 'MISSING'}")
        if not ready:
            print("[SKIP] the Omni pack or its weights are absent, which is a supported state: "
                  "every other route is unaffected")
        else:
            # The one check that cannot be inferred from the graph: whether the control projection
            # actually loaded. It needs torch, which Blender does not have, so it runs in ComfyUI's
            # own venv.
            venv = os.path.join(checkout, "venv", "bin", "python")
            if os.path.isfile(venv):
                fix = subprocess.run(
                    [venv, os.path.join(REPO, "tools", "scripts", "comfy_omni_fix.py"),
                     "--check", "--comfy", checkout], capture_output=True, text=True)
                lines = (fix.stdout or fix.stderr).strip().splitlines()
                check("the Omni control projection loads (comfy_omni_fix --check)",
                      fix.returncode == 0, lines[-1] if lines else "no output")

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
