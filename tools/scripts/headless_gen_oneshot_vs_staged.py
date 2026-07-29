"""Headless measurement: the `mesh_geom_texture` one-shot against the staged
`mesh_geom_trellis` + `mesh_simplify_uv` + `mesh_texture` chain (docs/GENERATION.md).

Two questions the asset gate left measurable and did not answer:

  1. Is the combined `geometry_texture` graph (`mesh_geom_texture`) worth having? Ten prompts, four of them
     foliage, through BOTH routes, with the SAME `mesh_subject` subject image so the reference
     framing is controlled for and the only variable is the graph. Per prompt and route: wall clock,
     peak VRAM (per process, at the queue moment and at the peak), face count, boundary edges after
     a weld, UV overlap, UV chart coverage, and a texture number that is not "looks fine" (in-chart
     albedo std plus a per-channel range).
  2. Does TRELLIS.2's opacity output reach a finished material? Measured on the GLB each route
     writes, and then through `gen_assets.finish_asset` on a subset.

    ~/.steam/steam/steamapps/common/Blender/blender --background --factory-startup \
        --python tools/scripts/headless_gen_oneshot_vs_staged.py [-- --prompts N --finish N --fresh]

Reachability-gated for the generation half, the same shape `headless_gen_assets.py` uses, and it
caches every generated mesh AND its timings and VRAM figures under
`_generated/comfy_g3b_check/gen/`, so re-running the measurement half costs seconds rather than
another half hour. Exit 0 = nothing failed.
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
import threading
import time

import bpy
import numpy as np

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(REPO, "blender", "extensions"))

from bob_blender_tools.core import assets, comfy, gen_assets  # noqa: E402

FAILURES = []
OUT = os.path.join(REPO, "_generated", "comfy_g3b_check")
GEN = os.path.join(OUT, "gen")
PACK = os.path.join(OUT, "pack")
DUMP = os.path.join(REPO, "tools", "tests", "data", "object_info_min.json")

FACES = gen_assets.DEFAULT_FACES
TEXTURE_SIZE = 1024

# Ten prompts, FOUR of them foliage, because the open-surface case is the reason TRELLIS.2 is
# primary and a benchmark run mostly on solids would answer the wrong question. The first five are
# the asset gate's, so those results are directly comparable with its table.
SUBJECTS = [
    {"key": "boulder", "kind": "rocks", "seed": 1234, "height_m": 1.8, "foliage": False,
     "prompt": "a mossy granite boulder"},
    {"key": "fern", "kind": "plants", "seed": 7, "height_m": 0.6, "foliage": True,
     "prompt": "a single fern frond, thin flat leaf blade"},
    {"key": "stump", "kind": "trees", "seed": 11, "height_m": 1.1, "foliage": False,
     "prompt": "a weathered tree stump with rough bark"},
    {"key": "leaf", "kind": "plants", "seed": 21, "height_m": 0.22, "foliage": True,
     "prompt": "a single flat green leaf, isolated on white, photographed face-on, flat blade, "
               "visible veins, no stem cluster"},
    {"key": "cone", "kind": "rocks", "seed": 17, "height_m": 0.12, "foliage": False,
     "prompt": "a single pine cone"},
    {"key": "grass", "kind": "grass", "seed": 33, "height_m": 0.35, "foliage": True,
     "prompt": "a single blade of grass, thin flat strap leaf, face-on, no clump"},
    {"key": "ivy", "kind": "plants", "seed": 41, "height_m": 0.3, "foliage": True,
     "prompt": "one ivy leaf, five lobes, flat blade, face-on"},
    {"key": "log", "kind": "trees", "seed": 55, "height_m": 0.9, "foliage": False,
     "prompt": "a short fallen birch log with peeling bark"},
    {"key": "mushroom", "kind": "plants", "seed": 63, "height_m": 0.14, "foliage": False,
     "prompt": "a single brown cap mushroom"},
    {"key": "flint", "kind": "rocks", "seed": 71, "height_m": 0.25, "foliage": False,
     "prompt": "an angular flint stone, sharp fractured faces"},
]

ROUTES = ("staged", "oneshot")


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


# -- VRAM ---------------------------------------------------------------------------------------
# "Does it fit 16 GB" is the question, and the pack-install figures cannot answer it: they were
# whole-card
# readings taken with a second ComfyUI resident on the same device. Two things are needed instead.
# The reading has to be PER PROCESS, and it has to be summed over the ComfyUI FAMILY, because
# comfy-env runs each isolated pack in its own process: the main server, the TRELLIS2 pixi worker
# and the GeometryPack pixi worker each hold their own allocation and only their sum is the answer.
_OURS = {os.getpid()}


def _gpu_sample():
    """(card MiB in use, {pid: MiB}) from nvidia-smi, or (None, {}) with no nvidia-smi."""
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
    """Peak VRAM across a job, sampled from a thread so the measurement does not serialise with it.

    `at_queue` is the baseline the moment the job is handed over and `peak` is the highest reading
    while it ran, both as (card, comfy family). The delta between them is what the graph cost; the
    absolute `peak` is what has to fit.
    """

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
                "comfy_start": self.comfy_start, "comfy_peak": self.comfy_peak}


def _merge_vram(parts):
    """The peak of several stages, and the lowest start among them."""
    parts = [p for p in parts if p]
    if not parts:
        return {}
    return {"card_start": min(p["card_start"] for p in parts),
            "card_peak": max(p["card_peak"] for p in parts),
            "comfy_start": min(p["comfy_start"] for p in parts),
            "comfy_peak": max(p["comfy_peak"] for p in parts)}


def _vram_added(parts):
    """The largest single-stage RISE over its own baseline, across stages.

    Reported beside the absolute peak because the two answer different questions and the absolute
    one is order-dependent: `mesh_subject` leaves SDXL resident, so whichever mesh graph runs first
    is measured on top of roughly 6.6 GB that has nothing to do with it. The rise is what the graph
    itself costs; the peak is what the machine actually had to hold.
    """
    parts = [p for p in parts if p]
    if not parts:
        return 0
    return max(p["comfy_peak"] - p["comfy_start"] for p in parts)


# -- generation ---------------------------------------------------------------------------------
def _cache(path):
    return os.path.isfile(path) and os.path.getsize(path) > 0


def _stamp_path(key):
    return os.path.join(GEN, key + "_g3b.json")


def _load_stamp(key):
    try:
        with open(_stamp_path(key)) as fh:
            return json.load(fh) or {}
    except (OSError, ValueError):
        return {}


def _save_stamp(key, data):
    with open(_stamp_path(key), "w") as fh:
        json.dump(data, fh, indent=2, sort_keys=True)


def generate_one(subject, fresh, reachable):
    """`mesh_subject` once, then BOTH routes off that one subject image. Returns the entry or None.

    The subject is shared on purpose: `mesh_subject`'s framing, not the geometry model, decides
    whether a foliage prompt comes back as a thin open blade or a bushy volume, so handing the two
    routes different reference images would have measured the reference, not the graph.
    """
    key = subject["key"]
    entry = dict(subject, seconds={}, vram={}, paths={})
    stamp = _load_stamp(key)
    png = os.path.join(GEN, key + "_subject.png")
    a_raw = os.path.join(GEN, key + "_a_raw.glb")
    a_simp = os.path.join(GEN, key + "_a_simp.glb")
    a_tex = os.path.join(GEN, key + "_a_tex.glb")
    b_tex = os.path.join(GEN, key + "_b_tex.glb")
    entry["paths"] = {"subject": png, "staged_raw": a_raw, "staged_simp": a_simp,
                      "staged": a_tex, "oneshot": b_tex}
    entry["seconds"] = dict(stamp.get("seconds") or {})
    entry["vram"] = dict(stamp.get("vram") or {})
    remesh = not subject["foliage"]

    want = {"subject": png, "staged_raw": a_raw, "staged_simp": a_simp, "staged": a_tex,
            "oneshot": b_tex}
    missing = [k for k, p in want.items() if fresh or not _cache(p)]
    if missing and not reachable:
        note(f"{key} SKIP", f"missing {', '.join(missing)} and no server")
        return entry if not missing else None

    if "subject" in missing:
        with Vram() as v:
            info = comfy.subject_image(subject["prompt"], png, seed=subject["seed"], size=1024)
        entry["seconds"]["subject"] = round(info["seconds"], 2)
        entry["vram"]["subject"] = v.report()

    if "staged_raw" in missing:
        with Vram() as v:
            info = comfy.mesh_geometry(png, a_raw, seed=subject["seed"], tier="default",
                                       remesh=remesh)
        entry["seconds"]["staged_geometry"] = round(info["seconds"], 2)
        entry["vram"]["staged_geometry"] = v.report()
    if "staged_simp" in missing:
        with Vram() as v:
            info = comfy.mesh_simplify_uv(a_raw, a_simp, faces=FACES)
        entry["seconds"]["staged_simplify"] = round(info["seconds"], 2)
        entry["vram"]["staged_simplify"] = v.report()
    if "staged" in missing:
        with Vram() as v:
            info = comfy.mesh_texture(a_simp, png, a_tex, seed=subject["seed"],
                                      texture_size=TEXTURE_SIZE)
        entry["seconds"]["staged_texture"] = round(info["seconds"], 2)
        entry["vram"]["staged_texture"] = v.report()

    if "oneshot" in missing:
        with Vram() as v:
            info = comfy.mesh_geom_texture(png, b_tex, seed=subject["seed"], tier="default",
                                           faces=FACES, texture_size=TEXTURE_SIZE, remesh=remesh)
        entry["seconds"]["oneshot"] = round(info["seconds"], 2)
        entry["vram"]["oneshot"] = v.report()

    secs = entry["seconds"]
    secs["staged_total"] = round(sum(secs.get(k, 0.0) for k in
                                     ("staged_geometry", "staged_simplify", "staged_texture")), 2)
    secs["oneshot_total"] = round(secs.get("oneshot", 0.0), 2)
    staged_parts = [entry["vram"].get(k) for k in ("staged_geometry", "staged_simplify",
                                                   "staged_texture")]
    entry["vram"]["staged_all"] = _merge_vram(staged_parts)
    entry["vram"]["oneshot_all"] = dict(entry["vram"].get("oneshot") or {})
    if entry["vram"]["staged_all"]:
        entry["vram"]["staged_all"]["comfy_added"] = _vram_added(staged_parts)
    if entry["vram"]["oneshot_all"]:
        entry["vram"]["oneshot_all"]["comfy_added"] = _vram_added([entry["vram"].get("oneshot")])
    entry["cached"] = not missing
    _save_stamp(key, {"seconds": secs, "vram": entry["vram"]})
    return entry


# -- measurement --------------------------------------------------------------------------------
def texture_report(obj):
    """Albedo and opacity statistics INSIDE the UV charts, plus the coverage that makes them honest.

    Whole-image statistics are meaningless on these textures: `Trellis2RasterizePBR` inpaints only
    one to three pixels past a chart edge, so on a layout using 13% of the sheet the other 87% is
    untouched black and an image mean reports the packing. Values are Blender's scene-linear floats,
    not sRGB bytes, so they are comparable between the two routes and not with an 8-bit histogram.
    """
    img = gen_assets.basecolor_image(obj)
    if img is None:
        return {"image": None}
    width, height = img.size
    px = np.empty(width * height * img.channels, dtype=np.float32)
    img.pixels.foreach_get(px)
    px = px.reshape(height, width, img.channels)
    mask = gen_assets.uv_coverage(obj, grid=min(height, width))
    if mask is not None and mask.shape != (height, width):
        mask = mask[np.ix_(np.arange(height) * mask.shape[0] // height,
                           np.arange(width) * mask.shape[1] // width)]
    sel = np.ones((height, width), dtype=bool) if mask is None else mask
    rgb = px[..., :3][sel]
    out = {"image": f"{width}x{height}", "coverage": round(float(sel.mean()), 4),
           "albedo_mean": round(float(rgb.mean()), 4), "albedo_std": round(float(rgb.std()), 4),
           "channel_range": [round(float(np.ptp(rgb[:, c])), 4) for c in range(3)]}
    if img.channels >= 4:
        alpha = px[..., 3][sel]
        out["alpha"] = {"min": round(float(alpha.min()), 4),
                        "mean": round(float(alpha.mean()), 4),
                        "max": round(float(alpha.max()), 4),
                        "below_floor": round(float((alpha < gen_assets.OPACITY_FLOOR).mean()), 4)}
    return out


def glb_materials(path):
    """The material dicts of a GLB, read from its JSON chunk with no importer involved.

    `alphaMode` is the whole question for the opacity channel: `Trellis2RasterizePBR` writes a real
    RGBA basecolor and then declares `"OPAQUE"`, which per the glTF spec tells every importer to
    ignore that alpha. So the flag has to be read, not the pixels.
    """
    import struct

    data = open(path, "rb").read()
    if data[:4] != b"glTF":
        return []
    off, chunks = 12, []
    while off < len(data):
        length, kind = struct.unpack_from("<I4s", data, off)
        chunks.append((kind, data[off + 8:off + 8 + length]))
        off += 8 + length
    return json.loads(chunks[0][1].decode("utf-8")).get("materials") or []


def alpha_survives_reimport(name, kind):
    """Import a finished asset the way a scatter layer does, and report what its alpha did.

    The one check that matters after the material is right: glTF carries opacity in
    `baseColorTexture`'s alpha and nowhere else, so an alpha map that does not survive the export
    and re-import is an alpha map only the authoring session ever sees.
    """
    empty_scene()
    obj = gen_assets.import_generated(name, kind=kind, pack_dir=PACK)
    mat = obj.data.materials[0] if obj.data.materials else None
    if mat is None or not mat.use_nodes:
        return {"linked": False, "reason": "no material"}
    bsdf = next((n for n in mat.node_tree.nodes
                 if n.bl_idname == "ShaderNodeBsdfPrincipled"), None)
    linked = bool(bsdf and bsdf.inputs["Alpha"].links)
    return {"linked": linked,
            "render_method": getattr(mat, "surface_render_method", None),
            "backface_culling": mat.use_backface_culling,
            "source": (bsdf.inputs["Alpha"].links[0].from_node.bl_idname if linked else None)}


def mesh_report(path):
    """Everything the gate asks of one route's returned GLB, measured in Blender."""
    empty_scene()
    obj = gen_assets.import_glb(path, name="cell")
    welded = gen_assets.weld(obj)
    dims = gen_assets.dimensions(obj)
    report = {"faces": gen_assets.face_count(obj), "welded_verts": welded,
              "boundary_edges": gen_assets.boundary_edges(obj),
              "thin_ratio": round(min(dims) / max(dims), 4) if max(dims) else None,
              "uv_overlap": gen_assets.uv_overlap(obj),
              "bytes": os.path.getsize(path)}
    report.update(texture=texture_report(obj))
    return report


def normal_stats(path):
    """(std, detail) of a baked tangent-space normal map.

    `std` alone cannot answer whether a bake transferred DETAIL, which is the whole question for the
    one-shot route: a same-topology bake still records the difference between the welded high mesh's
    smooth normals and the low mesh's, so it comes back non-flat while carrying no geometry the low
    mesh does not already have. `detail` is the mean absolute neighbour difference, which is high
    frequency by construction, so half a million faces baked down should read well above a bake of a
    mesh onto itself.

    That same-topology bake is no longer taken: `comfy.geometry_is_final` drops the role when the
    two meshes are one file, so this now only ever reads a genuine transfer. The number it used to
    produce was the defect it describes, and it shipped -- 52% of the barn's texels and 87% of the
    stump's deviated past one 8-bit step from a map whose only correct value was flat.
    """
    img = bpy.data.images.load(path, check_existing=False)
    px = np.empty(len(img.pixels), dtype=np.float32)
    img.pixels.foreach_get(px)
    size = img.size[1], img.size[0]
    rgb = px.reshape(size[0], size[1], 4)[..., :3]
    bpy.data.images.remove(img)
    dx = np.abs(np.diff(rgb, axis=1)).mean()
    dy = np.abs(np.diff(rgb, axis=0)).mean()
    return float(rgb.std()), float((dx + dy) / 2.0)


def finish_route(entry, route, *, hero=False, force_opacity=False):
    """One route through steps 6 to 8, so the finished asset can be compared and not just the GLB.

    The two routes reach `finish_asset` differently, and the difference IS the trade:

    - staged: raw is the dense `mesh_geom_trellis` mesh, `simplify_pass` is the `mesh_simplify_uv` result and `texture_pass` the
      `mesh_texture` result, so Blender has a dense high mesh to bake a detail normal and AO from.
    - oneshot: `mesh_geom_texture` returned budget topology already textured, so the same file is BOTH the raw and
      the simplified mesh and there is no dense surface left to bake from. `comfy.geometry_is_final`
      is what says so, and it is what makes this route repair the mesh it ships (weld, pinhole fill)
      and skip a transfer that has nothing to transfer. Without it this benchmark measures a route
      that no longer exists: the forest-barn gate found the one-shot arm shipping the generator's
      holes and a cage projection of a mesh onto itself.
    """
    empty_scene()
    paths = entry["paths"]
    # Through `comfy.finish_passes`, the same mapping the panel uses, so the benchmark measures the
    # shipped code path rather than a script-only arrangement of it.
    if route == "staged":
        staged = {"raw_mesh": paths["staged_raw"], "simplified_mesh": paths["staged_simp"],
                  "textured_mesh": paths["staged"]}
        flows = ["mesh_subject", "mesh_geom_trellis", "mesh_simplify_uv", "mesh_texture"]
    else:
        staged = {"raw_mesh": paths["oneshot"], "textured_mesh": paths["oneshot"]}
        flows = ["mesh_subject", "mesh_geom_texture"]
    simplify_pass, texture_pass = comfy.finish_passes(staged)
    return gen_assets.finish_asset(
        staged["raw_mesh"], PACK, kind=entry["kind"],
        name=f"{entry['key']}_{route}" + ("_forced" if force_opacity else ""),
        height_m=entry["height_m"], hero=hero, fill_pinholes=not entry["foliage"],
        force_opacity=force_opacity,
        simplify_pass=simplify_pass, texture_pass=texture_pass,
        geometry_is_final=comfy.geometry_is_final(staged),
        provenance={"prompt": entry["prompt"], "seed": entry["seed"], "route": route,
                    "workflows": flows})


def decimate_floor(entry):
    """What Blender's Decimate reaches on this mesh, i.e. what the one-shot route would cost if it
    kept the dense mesh and let Blender do steps 3 and 4 instead of Trellis2ProcessMesh.

    Measured rather than assumed from the asset gate's five: this is the third route the verdict has
    to rule out, and it is free to measure because the staged route generated the dense mesh anyway.
    """
    empty_scene()
    t0 = time.time()
    obj = gen_assets.import_glb(entry["paths"]["staged_raw"], name="floor")
    gen_assets.weld(obj)
    if not entry["foliage"]:
        gen_assets.close_pinholes(obj)
    source = gen_assets.face_count(obj)
    gen_assets.decimate_to(obj, FACES)
    return {"source_faces": source, "faces": gen_assets.face_count(obj),
            "seconds": round(time.time() - t0, 2)}


# -- reporting ----------------------------------------------------------------------------------
def print_table(entries, cells):
    section("mesh_geom_texture one-shot against mesh_geom_trellis + mesh_simplify_uv + mesh_texture staged, ten prompts")
    head = (f"{'prompt':<9} {'route':<8} {'wall s':>7} {'card MiB':>9} {'comfy MiB':>10} "
            f"{'added':>7} {'faces':>6} {'bound':>6} {'overlap':>8} {'cover':>6} {'alb std':>8} "
            f"{'ranges R/G/B':>18} {'alpha mean':>10}")
    print(head)
    for entry in entries:
        for route in ROUTES:
            cell = cells.get((entry["key"], route))
            if cell is None:
                print(f"{entry['key']:<9} {route:<8} {'missing':>7}")
                continue
            v = entry["vram"].get(route + "_all") or {}
            tex = cell.get("texture") or {}
            alpha = (tex.get("alpha") or {}).get("mean")
            over = "-" if cell["uv_overlap"] is None else f"{cell['uv_overlap']:.5f}"
            ranges = "/".join(f"{r:.2f}" for r in tex.get("channel_range", []))
            print(f"{entry['key']:<9} {route:<8} "
                  f"{entry['seconds'].get(route + '_total', 0.0):>7.1f} "
                  f"{v.get('card_peak', 0):>9} {v.get('comfy_peak', 0):>10} "
                  f"{v.get('comfy_added', 0):>7} "
                  f"{cell['faces']:>6} {cell['boundary_edges']:>6} {over:>8} "
                  f"{tex.get('coverage', 0):>6.3f} {tex.get('albedo_std', 0):>8.4f} "
                  f"{ranges:>18} "
                  f"{('-' if alpha is None else f'{alpha:.3f}'):>10}")


def summarise(entries, cells):
    """One summary row per route, over whatever cells exist."""
    section("per-route summary")
    out = {}
    for route in ROUTES:
        have = [(e, cells[(e["key"], route)]) for e in entries
                if cells.get((e["key"], route))]
        if not have:
            out[route] = None
            continue
        secs = [e["seconds"].get(route + "_total", 0.0) for e, _ in have]
        peaks = [e["vram"].get(route + "_all", {}).get("comfy_peak", 0) for e, _ in have]
        cards = [e["vram"].get(route + "_all", {}).get("card_peak", 0) for e, _ in have]
        added = [e["vram"].get(route + "_all", {}).get("comfy_added", 0) for e, _ in have]
        faces = sorted(c["faces"] for _, c in have)
        out[route] = {
            "n": len(have),
            "median_seconds": round(sorted(secs)[len(secs) // 2], 1),
            "total_seconds": round(sum(secs), 1),
            "worst_seconds": round(max(secs), 1),
            "median_faces": faces[len(faces) // 2],
            "in_budget": sum(1 for f in faces if f <= FACES * 1.1),
            "peak_comfy_mib": max(peaks),
            "peak_card_mib": max(cards),
            "peak_added_mib": max(added),
            "worst_overlap": max((c["uv_overlap"] or 0.0) for _, c in have),
            "median_albedo_std": sorted((c["texture"].get("albedo_std") or 0.0)
                                        for _, c in have)[len(have) // 2],
            "median_coverage": sorted((c["texture"].get("coverage") or 0.0)
                                      for _, c in have)[len(have) // 2],
            "open_meshes": sum(1 for _, c in have if c["boundary_edges"] > 0),
        }
        s = out[route]
        note(route, f"{s['n']} prompts, median {s['median_seconds']} s (worst "
                    f"{s['worst_seconds']} s, {s['total_seconds']} s for all), peak "
                    f"{s['peak_comfy_mib']} MiB in the ComfyUI processes / {s['peak_card_mib']} MiB "
                    f"whole card / {s['peak_added_mib']} MiB added by the graph itself, "
                    f"{s['in_budget']}/{s['n']} inside the {FACES} budget (median "
                    f"{s['median_faces']}), worst UV overlap {s['worst_overlap']:.5f}, median "
                    f"albedo std {s['median_albedo_std']:.4f} at {s['median_coverage']:.3f} chart "
                    f"coverage, {s['open_meshes']}/{s['n']} open")
    return out


def open_surface_report(entries, cells):
    """The open-surface question per ROUTE, with remesh controlled for rather than conflated.

    Both routes get the identical `remesh` value, off for foliage and on for everything else, so a
    difference in boundary edges is a difference between the graphs and not between two settings.
    """
    section("open surfaces, remesh controlled for")
    print(f"{'prompt':<9} {'remesh':<7} {'staged bound':>13} {'oneshot bound':>14} "
          f"{'staged thin':>12} {'oneshot thin':>13}")
    for entry in entries:
        a = cells.get((entry["key"], "staged"))
        b = cells.get((entry["key"], "oneshot"))
        if not a or not b:
            continue
        print(f"{entry['key']:<9} {('off' if entry['foliage'] else 'on'):<7} "
              f"{a['boundary_edges']:>13} {b['boundary_edges']:>14} "
              f"{(a['thin_ratio'] or 0):>12.4f} {(b['thin_ratio'] or 0):>13.4f}")
    for route in ROUTES:
        foliage = [cells[(e["key"], route)] for e in entries
                   if e["foliage"] and cells.get((e["key"], route))]
        if not foliage:
            continue
        opened = [c for c in foliage if c["boundary_edges"] >= 500]
        check(f"{route}: a foliage prompt with remesh off comes back OPEN",
              bool(opened),
              f"{len(opened)} of {len(foliage)} foliage meshes carry 500+ boundary edges "
              f"(counts: {[c['boundary_edges'] for c in foliage]})")


def opacity_report(entries, cells, finished):
    """The partial the asset gate left open: is TRELLIS.2's opacity output in the GLB, and does it
    reach the finished material?"""
    section("the opacity channel")
    for entry in entries[:1]:
        for name in ("staged", "oneshot"):
            path = entry["paths"][name]
            for mat in glb_materials(path):
                note(f"{entry['key']}/{name}: what the pack declares",
                     f"alphaMode={mat.get('alphaMode')}, doubleSided={mat.get('doubleSided')}")
    for route in ROUTES:
        have = [(e, cells[(e["key"], route)]) for e in entries if cells.get((e["key"], route))]
        with_alpha = [(e, c) for e, c in have if (c["texture"].get("alpha") or {}).get("max")
                      is not None]
        check(f"{route}: the returned GLB carries an opacity channel",
              len(with_alpha) == len(have) and bool(have),
              f"{len(with_alpha)} of {len(have)} basecolor textures are RGBA")
        cut = [(e, c) for e, c in with_alpha if c["texture"]["alpha"]["min"]
               < gen_assets.OPACITY_FLOOR]
        note(f"{route}: meshes whose in-chart opacity is a real cutout",
             f"{len(cut)} of {len(with_alpha)}; "
             + ", ".join(f"{e['key']} {c['texture']['alpha']['mean']:.3f}"
                         f"({c['texture']['alpha']['below_floor']:.3f} below "
                         f"{gen_assets.OPACITY_FLOOR})" for e, c in cut[:6]))
    by_key = {e["key"]: e for e in entries}
    for (key, route), report in sorted(finished.items()):
        op = report.get("opacity") or {}
        note(f"finished {key}/{route}: opacity",
             f"wired={op.get('wired')} in_chart={op.get('in_chart')}")
        for mat in glb_materials(report["file"]):
            note(f"finished {key}/{route}: exported alphaMode", mat.get("alphaMode"))
        trip = alpha_survives_reimport(report["name"], by_key[key]["kind"])
        report["reimport"] = trip
        if op.get("wired"):
            check(f"{key}/{route}: the alpha survives the glTF round trip a scatter layer makes",
                  trip["linked"], json.dumps(trip))
        else:
            note(f"{key}/{route}: re-import", json.dumps(trip))
    wired = [k for k, r in finished.items() if (r.get("opacity") or {}).get("wired")]
    note("assets whose opacity was wired by the plausibility rule",
         f"{sorted(wired)}" if wired else "none: see the verdicts above")


def finish_report(entries, cells, finished, floors):
    """What the finished asset costs on each route. The normal map is the number that decides it."""
    section("finished assets, both routes")
    print(f"{'prompt':<9} {'route':<8} {'faces':>6} {'bake source':>12} {'normal std':>10} "
          f"{'normal detail':>13} {'master':<8} {'blender s':>9} {'maps'}")
    for (key, route), report in sorted(finished.items()):
        std, detail = report.get("normal_std"), report.get("normal_detail")
        print(f"{key:<9} {route:<8} {report['lod_faces'][0]:>6} "
              f"{report.get('source_faces', 0):>12} "
              f"{('-' if std is None else f'{std:.4f}'):>10} "
              f"{('-' if detail is None else f'{detail:.5f}'):>13} "
              f"{str(report['master_type']):<8} "
              f"{report['seconds'].get('total', 0):>9.1f} {sorted(report['maps'])}")
    for key, floor in sorted(floors.items()):
        note(f"{key}: Blender Decimate on the dense mesh",
             f"{floor['source_faces']} faces in, {floor['faces']} out against a {FACES} budget, "
             f"{floor['seconds']} s")


def verdict(entries, cells, summary, finished, floors):
    """A verdict sentence, not a table with no conclusion."""
    section("verdict: one-shot against staged")
    a, b = summary.get("staged"), summary.get("oneshot")
    if not a or not b:
        note("verdict", "not enough measured cells for a verdict")
        return
    fits = b["peak_comfy_mib"] < 16303
    faster = b["median_seconds"] < a["median_seconds"]
    note("does mesh_geom_texture fit 16 GB",
         f"{'YES' if fits else 'NO'}: peak {b['peak_comfy_mib']} MiB across the ComfyUI processes "
         f"({b['peak_card_mib']} MiB whole card, {b['peak_added_mib']} MiB of it added by the "
         f"graph) of 16303 MiB, against the staged route's {a['peak_comfy_mib']} MiB peak and "
         f"{a['peak_added_mib']} MiB rise")
    note("wall clock", f"one-shot median {b['median_seconds']} s against staged "
                       f"{a['median_seconds']} s ({'faster' if faster else 'slower'})")
    note("face budget", f"one-shot {b['in_budget']}/{b['n']} inside {FACES} (median "
                        f"{b['median_faces']}), staged {a['in_budget']}/{a['n']} (median "
                        f"{a['median_faces']})")
    note("texture", f"one-shot median albedo std {b['median_albedo_std']:.4f} at "
                    f"{b['median_coverage']:.3f} coverage, staged "
                    f"{a['median_albedo_std']:.4f} at {a['median_coverage']:.3f}")
    normals = {route: [r.get("normal_std") for (k, rt), r in finished.items()
                       if rt == route and r.get("normal_std") is not None]
               for route in ROUTES}
    for route, vals in normals.items():
        if vals:
            note(f"{route}: baked normal std", f"{[round(v, 4) for v in vals]}")
        else:
            # An absence with a reason, or a reader takes it for a measurement that failed. The
            # one-shot route's two meshes are one file, so there is no detail to transfer and
            # `comfy.geometry_is_final` drops the role: the number this column used to carry was the
            # weld boundary between a welded high and an unwelded low, not geometry.
            note(f"{route}: baked normal std",
                 "no normal map written, which is the route being honest about having no dense "
                 "mesh")
    if floors:
        note("the third route", "keeping mesh_geom_texture's dense mesh and letting Blender simplify lands at "
             + ", ".join(f"{k} {v['faces']}" for k, v in sorted(floors.items()))
             + f" faces against {FACES}")


# -- main ---------------------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser()
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    ap.add_argument("--prompts", type=int, default=len(SUBJECTS))
    ap.add_argument("--finish", type=int, default=3,
                    help="how many prompts also go through steps 6 to 8 on BOTH routes")
    ap.add_argument("--fresh", action="store_true", help="regenerate even when a GLB is cached")
    ap.add_argument("--no-gen", action="store_true", help="measure the cache only")
    ap.add_argument("--keep", action="store_true", help="keep the output pack")
    args = ap.parse_args(argv)

    os.makedirs(GEN, exist_ok=True)
    if os.path.isdir(PACK):
        shutil.rmtree(PACK)
    os.makedirs(PACK, exist_ok=True)
    assets.set_generated_root(PACK)

    section("environment")
    ok, detail = comfy.reachable()
    note("ComfyUI", f"{'up' if ok else 'not connected'} -- {detail}")
    if args.no_gen:
        ok = False
    repo = os.environ.get("BOB_COMFY_DIR", os.path.expanduser("~/dev/ComfyUI"))
    if os.path.isdir(repo):
        comfy.set_pref_comfy_dir(repo)
        note("mesh transport", f"local copy into {repo}/input/3d")
    card, procs = _gpu_sample()
    note("GPU at start", f"{card} MiB in use on the card, compute apps {procs}")

    section("preflight over every shipped graph, offline")
    info = json.loads(open(DUMP).read())
    for name in sorted(f for f in os.listdir(comfy.WORKFLOW_DIR) if f.endswith(".json")):
        prompt, prov = comfy.load_workflow(name)
        problems = comfy.preflight(prompt, info=info, required_titles=("BOB_OUT",),
                                   runtime_inputs=prov.get("runtime_inputs") or ())
        check(f"preflight {name}", not problems, "; ".join(problems))
    w9b, prov = comfy.load_workflow("mesh_geom_texture")
    check("mesh_geom_texture names no cloud node",
          not any((info.get(n["class_type"]) or {}).get("api_node") for n in w9b.values()),
          f"{len(w9b)} nodes, derived from {prov.get('derived_from', '')[:52]}")

    section("generation, both routes off one shared subject image")
    entries = []
    for subject in SUBJECTS[:args.prompts]:
        entry = generate_one(subject, args.fresh, ok)
        if entry is None:
            continue
        if all(_cache(p) for k, p in entry["paths"].items()):
            entries.append(entry)
            note(entry["key"], f"{'cached' if entry.get('cached') else 'fresh'}, "
                               f"staged {entry['seconds'].get('staged_total')} s, "
                               f"one-shot {entry['seconds'].get('oneshot_total')} s")
    if not entries:
        note("SKIP", "no generated meshes and no server; nothing measurable")
        return 0

    cells = {}
    for entry in entries:
        for route in ROUTES:
            try:
                cells[(entry["key"], route)] = mesh_report(entry["paths"][route])
            except Exception as exc:  # a cell that cannot be measured is a result, not a crash
                note(f"{entry['key']}/{route} unmeasurable", str(exc)[:160])
    print_table(entries, cells)
    summary = summarise(entries, cells)
    open_surface_report(entries, cells)

    finished, floors = {}, {}
    for entry in entries[:max(0, args.finish)]:
        for route in ROUTES:
            try:
                report = finish_route(entry, route)
            except Exception as exc:
                note(f"finish {entry['key']}/{route} failed", str(exc)[:200])
                continue
            if "normal" in report["maps"]:
                report["normal_std"], report["normal_detail"] = \
                    normal_stats(report["maps"]["normal"])
            finished[(entry["key"], route)] = report
        try:
            floors[entry["key"]] = decimate_floor(entry)
        except Exception as exc:
            note(f"decimate floor {entry['key']} failed", str(exc)[:160])
    finish_report(entries, cells, finished, floors)
    opacity_report(entries, cells, finished)

    # The plausibility rule refuses every channel these ten prompts produced (one route says
    # "opaque", the other says something unusable), so asserting that the wiring works would be
    # asserting a path nothing took. Force it once instead and measure the result end to end.
    section("the opacity wiring, forced on one foliage asset")
    foliage = next((e for e in entries if e["foliage"]), None)
    if foliage is None:
        note("SKIP", "no foliage prompt in this run")
    else:
        forced = finish_route(foliage, "oneshot", force_opacity=True)
        op = forced.get("opacity") or {}
        note(f"{foliage['key']}: forced opacity", json.dumps(op))
        check(f"{foliage['key']}: a forced opacity map lands in the basecolor's alpha",
              "basecolor" in forced["maps"], sorted(forced["maps"]))
        modes = [m.get("alphaMode") for m in glb_materials(forced["file"])]
        check(f"{foliage['key']}: the exported GLB stops declaring OPAQUE",
              modes and all(m != "OPAQUE" for m in modes), f"alphaMode {modes}")
        trip = alpha_survives_reimport(forced["name"], foliage["kind"])
        check(f"{foliage['key']}: the alpha survives the round trip a scatter layer makes",
              trip["linked"] and trip.get("render_method") == "DITHERED", json.dumps(trip))

    for (key, route), report in sorted(finished.items()):
        check(f"{key}/{route}: face count within budget",
              report["lod_faces"][0] <= FACES * 1.1,
              f"{report['lod_faces'][0]} against {FACES} ({report['simplify_source']})")
        check(f"{key}/{route}: a UV layer with no overlap",
              report["uv_overlap"] is not None and report["uv_overlap"] < 0.01,
              f"{report['uv_overlap']:.6f} ({report['uv_source']})")
        check(f"{key}/{route}: materials.master_type() reports a BobShader",
              report["master_type"] == "surface", str(report["master_type"]))

    verdict(entries, cells, summary, finished, floors)

    section("summary")
    with open(os.path.join(OUT, "g3b_results.json"), "w") as fh:
        json.dump({"summary": summary,
                   "cells": {f"{k}/{r}": v for (k, r), v in cells.items()},
                   "seconds": {e["key"]: e["seconds"] for e in entries},
                   "vram": {e["key"]: e["vram"] for e in entries},
                   "finished": {f"{k}/{r}": {kk: vv for kk, vv in v.items() if kk != "maps"}
                                for (k, r), v in finished.items()},
                   "decimate_floors": floors}, fh, indent=2, sort_keys=True, default=str)
    note("results", os.path.join(OUT, "g3b_results.json"))
    if not args.keep and os.path.isdir(PACK):
        shutil.rmtree(PACK)
    print(f"{len(FAILURES)} failure(s)" + (": " + ", ".join(FAILURES) if FAILURES else ""))
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
