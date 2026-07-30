"""Headless measurement: the geometry A/B, decided once (docs/GENERATION.md).

TRELLIS.2 (`mesh_geom_texture`, the shipped one-shot route) against Hunyuan3D 2.1 (`mesh_geom_alt`
then `mesh_process` then `mesh_texture`) on ten fixed prompts, three of them foliage, with ONE
shared `mesh_subject` subject image per prompt so the grid compares geometry models rather than
reference images. The deliverable is a verdict per asset class rather than a global winner, because
the two differ structurally and not by degree.

    ~/.steam/steam/steamapps/common/Blender/blender --background --factory-startup \
        --python tools/scripts/headless_gen_geometry_ab.py [-- --part a,b,c,d --prompts N --no-gen]

Four parts, because they cost very different amounts of GPU time:

  A  no server: preflight over every shipped graph offline, the api_node assertion, and the route
     decision (`comfy.asset_chain`, `KIND_ROUTE`, `stage_exports` on the alt chain) as a value in
     one place. Always runs.
  B  the grid: ten prompts through both models, model-major so each model loads once and its VRAM
     is attributable, with wall clock, per-process peak VRAM sampled from a thread, face count,
     boundary edges AFTER A WELD, thinnest/longest axis ratio, UV overlap, chart coverage and the
     in-chart albedo std that catches the black-albedo trap.
  C  three of the ten through steps 6 to 8 on BOTH models, against the asset checks.
  D  the dense-mesh question: does the dense mesh buy measurable normal detail now that the bake
     alignment is fixed?
     Re-measured on the same assets the route A/B used, from the route A/B's cache, so it costs no
     GPU at all.

Every generated mesh caches WITH its timing and its VRAM under `_generated/geometry_ab_check/gen/`, so
`--no-gen` re-scores in minutes and `--fresh` regenerates. Reachability-gated: with no server every
generation half prints SKIP and exits 0. Exit 0 = nothing failed.
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

from bob_blender_tools.core import assets, comfy, gen_assets, gen_bars  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))  # for `_gate`
from _gate import Gate, Vram, empty_scene, section, stamp  # noqa: E402
from _gate import gpu_sample  # noqa: E402

# The shared gate harness (`_gate.py`): one implementation of the verdict (`check` / `note` /
# `skip` / the exit code) AND of what every gate needs around it -- the section banner, the scene
# wipe, the VRAM sampler, the cached-artifact sidecar. Bound to module-level names so the call sites
# below read as plain assertions. `FAILURES` is the Gate's own list, not a copy.
GATE = Gate("geometry A/B")
check, note, skip = GATE.check, GATE.note, GATE.skip
FAILURES = GATE.failures
OUT = os.path.join(REPO, "_generated", "geometry_ab_check")
GEN = os.path.join(OUT, "gen")
PACK = os.path.join(OUT, "pack")
DUMP = os.path.join(REPO, "tools", "tests", "data", "object_info_min.json")
# The route A/B's cache, which is where the shared subject images and the dense-mesh question's four
# assets come from.
G3B = os.path.join(REPO, "_generated", "route_ab_check", "gen")

FACES = gen_assets.DEFAULT_FACES
TEXTURE_SIZE = 1024
CARD_MIB = 16303
# A texture whose in-chart standard deviation is below this is the black-albedo trap rather than a
# flat-looking asset. Off the registry, which is what MAKES it "the asset gate's floor" rather than a
# comment promising the two gates' numbers mean the same thing.
ALBEDO_FLOOR = gen_bars.value("albedo_floor")

# The same ten prompts, seeds and heights the route A/B used, so the TRELLIS.2 column here is
# comparable with that table and the shared subject images are already on disk. Three of the ten are
# foliage, which is where the two models differ structurally rather than by degree.
SUBJECTS = [
    {"key": "boulder", "kind": "rocks", "seed": 1234, "height_m": 1.8,
     "prompt": "a mossy granite boulder"},
    {"key": "fern", "kind": "plants", "seed": 7, "height_m": 0.6,
     "prompt": "a single fern frond, thin flat leaf blade"},
    {"key": "stump", "kind": "trees", "seed": 11, "height_m": 1.1,
     "prompt": "a weathered tree stump with rough bark"},
    {"key": "leaf", "kind": "plants", "seed": 21, "height_m": 0.22,
     "prompt": "a single flat green leaf, isolated on white, photographed face-on, flat blade, "
               "visible veins, no stem cluster"},
    {"key": "cone", "kind": "rocks", "seed": 17, "height_m": 0.12,
     "prompt": "a single pine cone"},
    {"key": "grass", "kind": "grass", "seed": 33, "height_m": 0.35,
     "prompt": "a single blade of grass, thin flat strap leaf, face-on, no clump"},
    {"key": "ivy", "kind": "plants", "seed": 41, "height_m": 0.3,
     "prompt": "one ivy leaf, five lobes, flat blade, face-on"},
    {"key": "log", "kind": "trees", "seed": 55, "height_m": 0.9,
     "prompt": "a short fallen birch log with peeling bark"},
    {"key": "mushroom", "kind": "plants", "seed": 63, "height_m": 0.14,
     "prompt": "a single brown cap mushroom"},
    {"key": "flint", "kind": "rocks", "seed": 71, "height_m": 0.25,
     "prompt": "an angular flint stone, sharp fractured faces"},
]

MODELS = ("trellis", "hunyuan")
# The dense-mesh question's four, which are exactly the four the route A/B took through steps 6 to
# 8.
D11_KEYS = ("boulder", "fern", "leaf", "stump")


def skip(label, value):
    print(f"[SKIP] {label} -- {value}")


def foliage(subject):
    """Whether this prompt is a foliage case, through the same value the operators read."""
    return comfy.is_foliage(subject["kind"])


# -- VRAM ---------------------------------------------------------------------------------------
# Per process and summed over the ComfyUI FAMILY, because comfy-env runs each isolated pack in its
# own process: the main server, the TRELLIS2 pixi worker and the GeometryPack pixi worker each hold
# their own allocation and only their sum answers "does it fit". Same sampler the route A/B and the
# stylise gate used.
def _merge_vram(parts):
    """The peak of several stages, and the lowest start among them."""
    parts = [p for p in parts if p]
    if not parts:
        return {}
    out = {"card_start": min(p["card_start"] for p in parts),
           "card_peak": max(p["card_peak"] for p in parts),
           "comfy_start": min(p["comfy_start"] for p in parts),
           "comfy_peak": max(p["comfy_peak"] for p in parts)}
    out["comfy_added"] = max(p["comfy_peak"] - p["comfy_start"] for p in parts)
    return out


# -- generation ---------------------------------------------------------------------------------
def _cache(path):
    return os.path.isfile(path) and os.path.getsize(path) > 0


def _stamp_target(key):
    """The artifact a keyed sidecar belongs to. `_gate.stamp` appends the `.json`, so the file a
    previous run wrote is the file this one reads -- no cache is invalidated by the fold."""
    return os.path.join(GEN, key + "_g7")


def _load_stamp(key):
    return stamp(_stamp_target(key))


def _save_stamp(key, entry):
    stamp(_stamp_target(key), {"seconds": entry["seconds"], "vram": entry["vram"]})


def paths_for(key):
    return {"subject": os.path.join(GEN, key + "_subject.png"),
            "trellis": os.path.join(GEN, key + "_t_tex.glb"),
            "hunyuan_raw": os.path.join(GEN, key + "_h_raw.glb"),
            "hunyuan_simp": os.path.join(GEN, key + "_h_simp.glb"),
            "hunyuan": os.path.join(GEN, key + "_h_tex.glb")}


def new_entry(subject):
    # `cached` and not `stamp`: `stamp` is the shared writer imported from `_gate`, and a local of that
    # name would shadow it for the rest of this scope -- harmless today, one line from not being.
    cached = _load_stamp(subject["key"])
    return dict(subject, paths=paths_for(subject["key"]),
                seconds=dict(cached.get("seconds") or {}), vram=dict(cached.get("vram") or {}),
                foliage=foliage(subject))


def stage_subject(entry, fresh, reachable):
    """The ONE reference image both models are conditioned on.

    Reused from the route A/B's cache when it is there, which is not a shortcut: it is the same
    prompt at the same seed, and it makes the TRELLIS.2 column here directly comparable with the
    route A/B's one-shot column rather than merely similar.
    """
    png = entry["paths"]["subject"]
    if _cache(png) and not fresh:
        return True
    old = os.path.join(G3B, entry["key"] + "_subject.png")
    if os.path.isfile(old) and not fresh:
        shutil.copyfile(old, png)
        entry["seconds"].setdefault("subject", 0.0)
        entry["subject_source"] = "g3b cache"
        return True
    if not reachable:
        return False
    with Vram() as v:
        info = comfy.subject_image(entry["prompt"], png, seed=entry["seed"], size=1024)
    entry["seconds"]["subject"] = round(info["seconds"], 2)
    entry["vram"]["subject"] = v.report()
    entry["subject_source"] = "mesh_subject"
    return True


def generate_model(entry, model, fresh, reachable):
    """One model's half of one cell. Returns True when every file it needs is on disk.

    Model-major rather than prompt-major on purpose: TRELLIS.2 is 15 GB with its cache and Hunyuan
    2.1 is 6.9 GB, so interleaving them would swap a model in and out ten times and every VRAM
    figure would be a statement about the swap rather than about the model.
    """
    paths = entry["paths"]
    remesh = not entry["foliage"]
    want = ["trellis"] if model == "trellis" else ["hunyuan_raw", "hunyuan_simp", "hunyuan"]
    missing = [k for k in want if fresh or not _cache(paths[k])]
    if not missing:
        return True
    if not reachable:
        return False

    if model == "trellis":
        with Vram() as v:
            info = comfy.mesh_geom_texture(paths["subject"], paths["trellis"], seed=entry["seed"],
                                           tier="default", faces=FACES,
                                           texture_size=TEXTURE_SIZE, remesh=remesh)
        entry["seconds"]["trellis_geometry_texture"] = round(info["seconds"], 2)
        entry["vram"]["trellis_geometry_texture"] = v.report()
    else:
        if "hunyuan_raw" in missing:
            with Vram() as v:
                info = comfy.mesh_geom_alt(paths["subject"], paths["hunyuan_raw"],
                                           seed=entry["seed"])
            entry["seconds"]["hunyuan_geometry"] = round(info["seconds"], 2)
            entry["vram"]["hunyuan_geometry"] = v.report()
        if "hunyuan_simp" in missing:
            with Vram() as v:
                info = comfy.mesh_process(paths["hunyuan_raw"], paths["hunyuan_simp"], faces=FACES,
                                          remesh=remesh)
            entry["seconds"]["hunyuan_process"] = round(info["seconds"], 2)
            entry["vram"]["hunyuan_process"] = v.report()
        if "hunyuan" in missing:
            with Vram() as v:
                info = comfy.mesh_texture(paths["hunyuan_simp"], paths["subject"],
                                          paths["hunyuan"], seed=entry["seed"],
                                          texture_size=TEXTURE_SIZE)
            entry["seconds"]["hunyuan_texture"] = round(info["seconds"], 2)
            entry["vram"]["hunyuan_texture"] = v.report()
    roll_up(entry)
    _save_stamp(entry["key"], entry)
    return True


def roll_up(entry):
    """Per-model totals from the per-stage timings and VRAM readings."""
    secs, vram = entry["seconds"], entry["vram"]
    stages = {"trellis": ["trellis_geometry_texture"],
              "hunyuan": ["hunyuan_geometry", "hunyuan_process", "hunyuan_texture"]}
    for model, keys in stages.items():
        secs[model + "_total"] = round(sum(secs.get(k, 0.0) for k in keys), 2)
        merged = _merge_vram([vram.get(k) for k in keys])
        if merged:
            vram[model + "_all"] = merged


# -- measurement ---------------------------------------------------------------------------------
def texture_report(obj):
    """Albedo statistics INSIDE the UV charts, plus the coverage that makes them honest.

    A whole-image statistic on these textures measures the UV packing: `Trellis2RasterizePBR`
    inpaints one to three pixels past a chart edge, so most of a sparse layout is untouched black.
    """
    img = gen_assets.basecolor_image(obj)
    if img is None or min(img.size) < 8:
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
    return {"image": f"{width}x{height}", "coverage": round(float(sel.mean()), 4),
            "albedo_mean": round(float(rgb.mean()), 4), "albedo_std": round(float(rgb.std()), 4),
            "channel_range": [round(float(np.ptp(rgb[:, c])), 4) for c in range(3)]}


def mesh_report(path):
    """Everything the grid asks of one cell's returned GLB, measured in Blender.

    Boundary edges are counted AFTER a weld, per the asset gate's correction: the glTF importer
    splits vertices per corner, so an unwelded count is a statement about the file format and not
    about the surface.
    """
    empty_scene()
    obj = gen_assets.import_glb(path, name="cell")
    welded = gen_assets.weld(obj)
    dims = gen_assets.dimensions(obj)
    lo, hi = gen_assets.bbox_world(obj)
    report = {"faces": gen_assets.face_count(obj), "welded_verts": welded,
              "boundary_edges": gen_assets.boundary_edges(obj),
              "thin_ratio": round(min(dims) / max(dims), 4) if max(dims) else None,
              "uv_overlap": gen_assets.uv_overlap(obj),
              "extent": round(float(max(hi[i] - lo[i] for i in range(3))), 4),
              "bytes": os.path.getsize(path)}
    report["texture"] = texture_report(obj)
    return report


def normal_stats(path):
    """(std, detail) of a baked tangent-space normal map.

    `std` cannot tell transferred detail from a shading difference, so `detail` is the mean absolute
    neighbour difference, which is high frequency by construction. This is the pair the dense-mesh
    question turns on.
    """
    img = bpy.data.images.load(path, check_existing=False)
    px = np.empty(len(img.pixels), dtype=np.float32)
    img.pixels.foreach_get(px)
    rgb = px.reshape(img.size[1], img.size[0], 4)[..., :3]
    bpy.data.images.remove(img)
    dx = np.abs(np.diff(rgb, axis=1)).mean()
    dy = np.abs(np.diff(rgb, axis=0)).mean()
    return float(rgb.std()), float((dx + dy) / 2.0)


def staged_for(entry, model):
    """The staging dict either model's chain would have produced, so the finish runs the shipped
    mapping (`comfy.finish_passes`, `comfy.stage_exports`) rather than a script-only arrangement."""
    paths = entry["paths"]
    if model == "trellis":
        return {"raw_mesh": paths["trellis"], "textured_mesh": paths["trellis"],
                "meta": {"route": "oneshot", "model": "TRELLIS.2-4B", "license": "MIT",
                         "workflows": ["mesh_subject", "mesh_geom_texture"]}}
    return {"raw_mesh": paths["hunyuan_raw"], "simplified_mesh": paths["hunyuan_simp"],
            "textured_mesh": paths["hunyuan"],
            "meta": {"route": "alt", "model": "hunyuan3d-2.1",
                     "license": "Tencent Hunyuan3D community",
                     "workflows": ["mesh_subject", "mesh_geom_alt", "mesh_process",
                                   "mesh_texture"]}}


def finish_cell(entry, model):
    """One cell through steps 6 to 8, with the control gate's export alignment applied."""
    empty_scene()
    staged = staged_for(entry, model)
    simplify_pass, texture_pass = comfy.finish_passes(staged)
    report = gen_assets.finish_asset(
        staged["raw_mesh"], PACK, kind=entry["kind"], name=f"{entry['key']}_{model}",
        height_m=entry["height_m"], fill_pinholes=not entry["foliage"],
        exports=comfy.stage_exports(staged),
        simplify_pass=simplify_pass, texture_pass=texture_pass,
        geometry_is_final=comfy.geometry_is_final(staged),
        provenance=dict(staged["meta"], prompt=entry["prompt"], seed=entry["seed"]))
    if "normal" in report["maps"]:
        report["normal_std"], report["normal_detail"] = normal_stats(report["maps"]["normal"])
    return report


# -- part A: the value in one place ----------------------------------------------------------
def part_a():
    section("A. preflight over every shipped graph, offline against the committed dump")
    info = json.loads(open(DUMP).read())
    for name in sorted(f for f in os.listdir(comfy.WORKFLOW_DIR) if f.endswith(".json")):
        prompt, prov = comfy.load_workflow(name)
        problems = comfy.preflight(prompt, info=info, required_titles=("BOB_OUT",),
                                   runtime_inputs=prov.get("runtime_inputs") or ())
        check(f"preflight {name}", not problems, "; ".join(problems))
    for name in ("mesh_geom_alt", "mesh_process"):
        graph, prov = comfy.load_workflow(name)
        check(f"{name} names no cloud node",
              not any((info.get(n["class_type"]) or {}).get("api_node") for n in graph.values()),
              f"{len(graph)} nodes, derived from {(prov.get('derived_from') or '')[:48]}")

    section("A. the route is a value, in one place")
    check("asset_chain knows three chains and defaults to the route A/B's winner",
          comfy.DEFAULT_ASSET_ROUTE == "oneshot"
          and set(comfy.ASSET_ROUTES) == {"oneshot", "staged", "alt"}
          and comfy.asset_chain() is comfy.generate_asset_oneshot,
          f"{comfy.ASSET_ROUTES}, default {comfy.DEFAULT_ASSET_ROUTE}")
    check("an explicit route still wins",
          comfy.asset_chain("alt") is comfy.generate_asset_alt
          and comfy.asset_chain("staged") is comfy.generate_asset_chain)
    check("a control forces the staged chain, whatever the kind says",
          comfy.asset_chain(kind="rocks", control="/tmp/x.glb") is comfy.generate_asset_chain,
          "mesh_geom_texture and mesh_geom_alt both generate their own geometry and take no control mesh")
    expected = {k: comfy.KIND_ROUTE.get(k, comfy.DEFAULT_ASSET_ROUTE)
                for k in ("rocks", "trees", "plants", "grass")}
    check("the per-class verdict is read from KIND_ROUTE and nowhere else",
          all(comfy.asset_chain(kind=k) is comfy.asset_chain(route=r)
              for k, r in expected.items()),
          f"KIND_ROUTE={comfy.KIND_ROUTE or '{}'} gives {expected}")
    check("foliage is one value too", comfy.is_foliage("plants") and comfy.is_foliage("grass")
          and not comfy.is_foliage("rocks"), str(comfy.FOLIAGE_KINDS))

    alt = {"raw_mesh": "r.glb", "simplified_mesh": "s.glb", "textured_mesh": "t.glb"}
    check("stage_exports puts the alt chain's three files in one frame",
          comfy.stage_exports(alt) == {"raw": 0, "simplified": 1, "textured": 2},
          f"{comfy.stage_exports(alt)}; SaveGLB adds no turn and every later hop is a Trellis "
          "export, so the relative correction is the staged chain's")


# -- part B: the grid ------------------------------------------------------------------------
def print_grid(entries, cells):
    section("B. the grid: TRELLIS.2 (mesh_geom_texture) against Hunyuan 2.1 (mesh_geom_alt, mesh_process, mesh_texture), ten prompts")
    print(f"{'prompt':<9} {'foliage':<7} {'model':<8} {'wall s':>7} {'comfy MiB':>10} "
          f"{'added':>7} {'faces':>6} {'bound':>6} {'thin':>7} {'overlap':>8} {'cover':>6} "
          f"{'alb std':>8} {'alb mean':>9}")
    for entry in entries:
        for model in MODELS:
            cell = cells.get((entry["key"], model))
            if cell is None:
                print(f"{entry['key']:<9} {'yes' if entry['foliage'] else 'no':<7} "
                      f"{model:<8} {'missing':>7}")
                continue
            v = entry["vram"].get(model + "_all") or {}
            tex = cell.get("texture") or {}
            over = "-" if cell["uv_overlap"] is None else f"{cell['uv_overlap']:.5f}"
            print(f"{entry['key']:<9} {'yes' if entry['foliage'] else 'no':<7} {model:<8} "
                  f"{entry['seconds'].get(model + '_total', 0.0):>7.1f} "
                  f"{v.get('comfy_peak', 0):>10} {v.get('comfy_added', 0):>7} "
                  f"{cell['faces']:>6} {cell['boundary_edges']:>6} "
                  f"{(cell['thin_ratio'] or 0):>7.4f} {over:>8} "
                  f"{tex.get('coverage', 0):>6.3f} {tex.get('albedo_std', 0):>8.4f} "
                  f"{tex.get('albedo_mean', 0):>9.4f}")


def summarise(entries, cells):
    """One summary row per model, plus the same split by asset class, which is the verdict's unit."""
    section("B. per-model summary, and the same split by asset class")
    out = {}
    groups = {"all": lambda e: True, "solids": lambda e: not e["foliage"],
              "foliage": lambda e: e["foliage"]}
    for model in MODELS:
        out[model] = {}
        for group, keep in groups.items():
            have = [(e, cells[(e["key"], model)]) for e in entries
                    if keep(e) and cells.get((e["key"], model))]
            if not have:
                continue
            secs = sorted(e["seconds"].get(model + "_total", 0.0) for e, _ in have)
            faces = sorted(c["faces"] for _, c in have)
            stds = sorted((c["texture"].get("albedo_std") or 0.0) for _, c in have)
            row = {
                "n": len(have),
                "median_seconds": round(secs[len(secs) // 2], 1),
                "total_seconds": round(sum(secs), 1),
                "median_faces": faces[len(faces) // 2],
                "in_budget": sum(1 for f in faces if f <= FACES * 1.1),
                "peak_comfy_mib": max(e["vram"].get(model + "_all", {}).get("comfy_peak", 0)
                                      for e, _ in have),
                "peak_added_mib": max(e["vram"].get(model + "_all", {}).get("comfy_added", 0)
                                      for e, _ in have),
                "worst_overlap": max((c["uv_overlap"] or 0.0) for _, c in have),
                "median_albedo_std": stds[len(stds) // 2],
                "black_textures": sum(1 for s in stds if s < ALBEDO_FLOOR),
                "median_boundary": sorted(c["boundary_edges"] for _, c in have)[len(have) // 2],
                "open_meshes": sum(1 for _, c in have if c["boundary_edges"] >= 500),
                "median_thin": round(sorted((c["thin_ratio"] or 0.0)
                                            for _, c in have)[len(have) // 2], 4),
            }
            out[model][group] = row
            note(f"{model}/{group}",
                 f"{row['n']} prompts, median {row['median_seconds']} s ({row['total_seconds']} s "
                 f"for all), peak {row['peak_comfy_mib']} MiB / {row['peak_added_mib']} MiB added, "
                 f"{row['in_budget']}/{row['n']} inside {FACES} (median {row['median_faces']}), "
                 f"median boundary {row['median_boundary']}, {row['open_meshes']}/{row['n']} open, "
                 f"median thin {row['median_thin']}, median albedo std "
                 f"{row['median_albedo_std']:.4f}, {row['black_textures']} black")
    return out


def open_surface_report(entries, cells):
    """The open-surface question, with `remesh` controlled for: both models' meshes go through the
    SAME `Trellis2ProcessMesh` at the same setting, off for foliage and on for the rest."""
    section("B. open surfaces, remesh controlled for on both models")
    print(f"{'prompt':<9} {'remesh':<7} {'trellis bound':>14} {'hunyuan bound':>14} "
          f"{'trellis thin':>13} {'hunyuan thin':>13}")
    for entry in entries:
        a, b = cells.get((entry["key"], "trellis")), cells.get((entry["key"], "hunyuan"))
        if not a or not b:
            continue
        print(f"{entry['key']:<9} {('off' if entry['foliage'] else 'on'):<7} "
              f"{a['boundary_edges']:>14} {b['boundary_edges']:>14} "
              f"{(a['thin_ratio'] or 0):>13.4f} {(b['thin_ratio'] or 0):>13.4f}")
    for model in MODELS:
        cells_f = [cells[(e["key"], model)] for e in entries
                   if e["foliage"] and cells.get((e["key"], model))]
        if not cells_f:
            continue
        opened = [c for c in cells_f if c["boundary_edges"] >= 500]
        note(f"{model}: foliage prompts that came back OPEN with remesh off",
             f"{len(opened)} of {len(cells_f)} carry 500+ boundary edges "
             f"(counts {[c['boundary_edges'] for c in cells_f]})")
    tr = [cells[(e["key"], "trellis")] for e in entries
          if e["foliage"] and cells.get((e["key"], "trellis"))]
    if tr:
        check("TRELLIS.2 keeps an open surface on foliage where the challenger structurally cannot",
              sum(1 for c in tr if c["boundary_edges"] >= 500) >= 1,
              f"trellis boundary {[c['boundary_edges'] for c in tr]}")


def black_albedo_report(entries, cells):
    """The trap, per model. It is a CHECK on the shipped route and a measurement on the challenger.

    The asymmetry is deliberate rather than convenient. "The default route never returns a black
    texture" is an invariant `mesh_geom_texture` holds structurally, because it never re-encodes a
    mesh, so a failure there is a regression. The challenger's rate is a property of
    `Trellis2EncodeMesh` on a mesh it did not generate, which the route A/B already measured at 1 in
    10 on the staged route; asserting it away would turn a verdict input into a red suite, and
    asserting it holds would be asserting something the measurement says is false.
    """
    section("B. the black-albedo trap")
    for model in MODELS:
        have = [(e, cells[(e["key"], model)]) for e in entries if cells.get((e["key"], model))]
        if not have:
            continue
        black = [e["key"] for e, c in have
                 if (c["texture"].get("albedo_std") or 0.0) < ALBEDO_FLOOR]
        detail = (f"{len(have) - len(black)} of {len(have)} above a {ALBEDO_FLOOR} in-chart std "
                  f"floor" + (f"; black: {black}" if black else ""))
        if model == "trellis":
            check("the shipped route never returns the black-albedo trap", not black, detail)
        else:
            note(f"{model}: textures that came back black", detail)


def plate_control(entries, reachable, fresh):
    """Does the plain plate in `mesh_geom_alt` matter, or is it ceremony?

    `mesh_subject` writes RGBA whose RGB is still the SDXL frame, and ComfyUI's LoadImage drops
    alpha rather than compositing it, so `mesh_geom` hands Hunyuan a background TRELLIS.2 never
    sees. This measures the same subject through `mesh_geom` (no plate) and reads the difference off
    the geometry, on one solid and one foliage prompt. It is two generations, and without it the
    grid's Hunyuan column would be open to the charge that it measured the background.
    """
    section("B. the plate control: the same subject with and without the composite")
    if not entries:
        return {}
    pairs = [next((e for e in entries if not e["foliage"]), None),
             next((e for e in entries if e["foliage"]), None)]
    out = {}
    for entry in [p for p in pairs if p]:
        path = os.path.join(GEN, entry["key"] + "_h_noplate.glb")
        if (fresh or not _cache(path)):
            if not reachable:
                skip("plate control", "no server")
                return out
            comfy.mesh_geometry(entry["paths"]["subject"], path, seed=entry["seed"],
                                workflow="mesh_geom")
        plate = mesh_report(entry["paths"]["hunyuan_raw"])
        raw = mesh_report(path)
        out[entry["key"]] = {"plate": plate, "no_plate": raw}
        note(f"{entry['key']}: with the plate",
             f"{plate['faces']} faces, thin {plate['thin_ratio']}, extent {plate['extent']}")
        note(f"{entry['key']}: without it (`mesh_geom`, the smoke test)",
             f"{raw['faces']} faces, thin {raw['thin_ratio']}, extent {raw['extent']}")
    return out


# -- part D: the dense mesh, re-measured through a fixed bake ------------------------------------
def part_d(limit=len(D11_KEYS)):
    """The dense-mesh question: does the dense mesh buy measurable normal detail now that the bake is
    aligned?

    The route A/B concluded it bought none, but every `Trellis2ExportTrimesh` write turns the
    subject and the turns accumulate, so that bake read from a cage rotated 90 or 180 degrees from
    its target. The fix is `comfy.stage_exports`. Same four assets, same files, from the route A/B's
    cache, so the only variable is the alignment. Three finishes per asset:

      staged aligned    the dense `mesh_geom_trellis` mesh baked onto the `mesh_simplify_uv` low
      mesh, in one frame staged as the route A/B ran it   the same with no `exports`, i.e. the
      misaligned bake one-shot          `mesh_geom_texture`'s own mesh baked onto itself, which is
      the no-dense-mesh control
    """
    section("D. the dense mesh, re-measured with the bake alignment fixed")
    by_key = {s["key"]: s for s in SUBJECTS}
    out = {}
    for key in D11_KEYS[:limit]:
        subject = by_key[key]
        files = {"raw": os.path.join(G3B, key + "_a_raw.glb"),
                 "simp": os.path.join(G3B, key + "_a_simp.glb"),
                 "tex": os.path.join(G3B, key + "_a_tex.glb"),
                 "one": os.path.join(G3B, key + "_b_tex.glb")}
        if not all(_cache(p) for p in files.values()):
            skip(f"dense mesh, {key}", "the route A/B's cache does not hold this asset")
            continue
        staged = {"raw_mesh": files["raw"], "simplified_mesh": files["simp"],
                  "textured_mesh": files["tex"]}
        one = {"raw_mesh": files["one"], "textured_mesh": files["one"]}
        runs = {"staged_aligned": (staged, comfy.stage_exports(staged)),
                "staged_route": (staged, None),
                "oneshot": (one, comfy.stage_exports(one))}
        row = {}
        for label, (stage, exports) in runs.items():
            empty_scene()
            simplify_pass, texture_pass = comfy.finish_passes(stage)
            report = gen_assets.finish_asset(
                stage["raw_mesh"], PACK, kind=subject["kind"], name=f"{key}_{label}",
                height_m=subject["height_m"], fill_pinholes=not foliage(subject),
                exports=exports, simplify_pass=simplify_pass, texture_pass=texture_pass,
                geometry_is_final=comfy.geometry_is_final(stage),
                provenance={"prompt": subject["prompt"], "seed": subject["seed"], "d11": label})
            if "normal" in report["maps"]:
                std, detail = normal_stats(report["maps"]["normal"])
            else:
                std = detail = None
            row[label] = {"faces": report["lod_faces"][0],
                          "source_faces": report.get("source_faces"),
                          "bake_rescale": report.get("bake_rescale"),
                          "normal_std": std, "normal_detail": detail}
        out[key] = row

    if not out:
        return out
    def fmt(value, places):
        return "-" if value is None else f"{value:.{places}f}"

    print(f"{'prompt':<9} {'run':<15} {'faces':>6} {'bake source':>12} {'normal std':>11} "
          f"{'normal detail':>14}")
    for key, row in out.items():
        for label, r in row.items():
            print(f"{key:<9} {label:<15} {r['faces']:>6} {r['source_faces']:>12} "
                  f"{fmt(r['normal_std'], 4):>11} {fmt(r['normal_detail'], 5):>14}")

    # The one-shot arm ships NO baked normal, and that is the correction the asset gate forced
    # rather than a gap in this table. Its two meshes are one file, so a transfer has nothing to
    # say; the figure this A/B used to score against was the weld boundary between a welded high and
    # an unwelded low, which the comment below conceded and used as a baseline anyway. Measured on
    # the shipped assets: 52% of a structure's texels and 87% of the stump's deviated past one 8-bit
    # step from a map whose only correct value was flat. So the control is now zero by construction,
    # and the question "does the dense mesh buy detail" is answered against its own misaligned bake.
    scored = [(k, r) for k, r in out.items()
              if r.get("staged_aligned", {}).get("normal_detail") is not None]
    for key, r in scored:
        note(f"dense mesh, {key}",
             f"aligned detail {r['staged_aligned']['normal_detail']:.5f} against the route A/B's misaligned "
             f"{fmt(r.get('staged_route', {}).get('normal_detail'), 5)}; the one-shot arm ships no "
             f"normal map (one file for both meshes, nothing to transfer); std "
             f"{fmt(r['staged_aligned']['normal_std'], 4)} / "
             f"{fmt(r['staged_route']['normal_std'], 4)}")
    if scored:
        # "Buys detail" has to mean a margin rather than a difference. Against the one-shot route it
        # is now the whole of the staged route's detail, since that route writes no normal at all.
        fixed = [k for k, r in scored
                 if r.get("staged_route", {}).get("normal_detail") is not None
                 and r["staged_aligned"]["normal_detail"] > r["staged_route"]["normal_detail"] * 1.1]
        absent = [k for k, r in out.items() if r.get("oneshot", {}).get("normal_detail") is None]
        note("dense mesh, the answer",
             f"the aligned dense-mesh bake beats its own earlier misaligned bake on "
             f"{len(fixed)} of {len(scored)} assets ({', '.join(fixed) or 'none'}); the one-shot "
             f"arm wrote no normal on {len(absent)} of {len(out)} "
             f"({', '.join(absent) or 'none'}), which is the route "
             f"being honest about having no dense mesh rather than a missing measurement")
    return out


# -- the verdict ---------------------------------------------------------------------------------
def verdict(entries, cells, summary, finished, plate):
    """A verdict per asset class, with the losing case stated as plainly as the winning one."""
    section("verdict, per asset class")
    tr, hu = summary.get("trellis", {}), summary.get("hunyuan", {})
    if not tr or not hu:
        note("verdict", "not enough measured cells for a verdict")
        return {}

    lines = {}
    for group in ("solids", "foliage"):
        a, b = tr.get(group), hu.get(group)
        if not a or not b:
            continue
        note(f"{group}: TRELLIS.2",
             f"median {a['median_seconds']} s, peak {a['peak_comfy_mib']} MiB, median boundary "
             f"{a['median_boundary']}, {a['open_meshes']}/{a['n']} open, median albedo std "
             f"{a['median_albedo_std']:.4f}, {a['in_budget']}/{a['n']} in budget")
        note(f"{group}: Hunyuan 2.1",
             f"median {b['median_seconds']} s, peak {b['peak_comfy_mib']} MiB, median boundary "
             f"{b['median_boundary']}, {b['open_meshes']}/{b['n']} open, median albedo std "
             f"{b['median_albedo_std']:.4f}, {b['in_budget']}/{b['n']} in budget")
        lines[group] = {"trellis": a, "hunyuan": b,
                        "faster": "hunyuan" if b["median_seconds"] < a["median_seconds"]
                                  else "trellis",
                        "leaner": "hunyuan" if b["peak_comfy_mib"] < a["peak_comfy_mib"]
                                  else "trellis"}
    note("block-out", "no cell here and none possible: the challenger's Hunyuan graph takes no "
                      "control mesh, so the block-out class was decided by the control gate in favour of mesh_geom_ctrl (Omni), "
                      "footprint IoU 0.9079 mean against mesh_geom_mv_trellis's 0.6748")
    return lines


# -- main -----------------------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser()
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    ap.add_argument("--part", default="a,b,c,d", help="which parts to run")
    ap.add_argument("--prompts", type=int, default=len(SUBJECTS))
    ap.add_argument("--finish", type=int, default=3,
                    help="how many prompts also go through steps 6 to 8 on BOTH models")
    ap.add_argument("--fresh", action="store_true", help="regenerate even when a GLB is cached")
    ap.add_argument("--no-gen", action="store_true", help="measure the cache only")
    ap.add_argument("--no-plate", action="store_true", help="skip the plate control's 2 jobs")
    ap.add_argument("--keep", action="store_true", help="keep the output pack")
    args = ap.parse_args(argv)
    parts = {p.strip() for p in args.part.split(",") if p.strip()}

    os.makedirs(GEN, exist_ok=True)
    if os.path.isdir(PACK):
        shutil.rmtree(PACK)
    os.makedirs(PACK, exist_ok=True)
    assets.set_generated_root(PACK)

    section("environment")
    reachable, detail = comfy.reachable()
    note("ComfyUI", f"{'up' if reachable else 'not connected'} -- {detail}")
    if args.no_gen:
        reachable = False
    repo = os.environ.get("BOB_COMFY_DIR", os.path.expanduser("~/dev/ComfyUI"))
    if os.path.isdir(repo):
        comfy.set_pref_comfy_dir(repo)
        note("mesh transport", f"local copy into {repo}/input/3d")
    card, procs = gpu_sample()
    note("GPU at start", f"{card} MiB in use on the card, compute apps {procs}")

    if "a" in parts:
        part_a()

    results = {"summary": None, "cells": {}, "finished": {}, "d11": {}, "plate": {}}
    entries, cells = [], {}
    if "b" in parts or "c" in parts:
        section("B. generation, model-major, both models off ONE shared subject per prompt")
        staged_entries = []
        for subject in SUBJECTS[:args.prompts]:
            entry = new_entry(subject)
            if stage_subject(entry, args.fresh, reachable):
                staged_entries.append(entry)
            else:
                skip(entry["key"], "no subject image and no server")
        for model in MODELS:
            for entry in staged_entries:
                if generate_model(entry, model, args.fresh, reachable):
                    continue
                skip(f"{entry['key']}/{model}", "not cached and no server")
        for entry in staged_entries:
            roll_up(entry)
            have = {m: all(_cache(entry["paths"][k]) for k in
                           (["trellis"] if m == "trellis" else
                            ["hunyuan_raw", "hunyuan_simp", "hunyuan"]))
                    for m in MODELS}
            if not any(have.values()):
                continue
            entries.append(entry)
            note(entry["key"], f"subject from {entry.get('subject_source', 'cache')}, trellis "
                               f"{entry['seconds'].get('trellis_total')} s, hunyuan "
                               f"{entry['seconds'].get('hunyuan_total')} s")
        if not entries:
            skip("B", "no generated meshes and no server; nothing measurable")

    if entries and "b" in parts:
        for entry in entries:
            for model in MODELS:
                path = entry["paths"][model]
                if not _cache(path):
                    continue
                try:
                    cells[(entry["key"], model)] = mesh_report(path)
                except Exception as exc:  # an unmeasurable cell is a result, not a crash
                    note(f"{entry['key']}/{model} unmeasurable", str(exc)[:160])
        print_grid(entries, cells)
        results["summary"] = summarise(entries, cells)
        open_surface_report(entries, cells)
        black_albedo_report(entries, cells)
        if not args.no_plate:
            results["plate"] = plate_control(entries, reachable, args.fresh)

    finished = {}
    if entries and "c" in parts:
        section("C. the finished asset, both models, steps 6 to 8")
        for entry in entries[:max(0, args.finish)]:
            for model in MODELS:
                if not cells.get((entry["key"], model)):
                    continue
                try:
                    finished[(entry["key"], model)] = finish_cell(entry, model)
                except Exception as exc:
                    note(f"finish {entry['key']}/{model} failed", str(exc)[:200])
        print(f"{'prompt':<9} {'model':<8} {'faces':>6} {'bake source':>12} {'overlap':>9} "
              f"{'rescale':>8} {'nrm std':>8} {'nrm detail':>11} {'height m':>9} {'origin':>7} "
              f"{'master':<8} {'blender s':>9} {'maps'}")
        for (key, model), r in sorted(finished.items()):
            print(f"{key:<9} {model:<8} {r['lod_faces'][0]:>6} {r.get('source_faces', 0):>12} "
                  f"{r['uv_overlap']:>9.6f} {(r.get('bake_rescale') or 1.0):>8.4f} "
                  f"{(r.get('normal_std') or 0):>8.4f} {(r.get('normal_detail') or 0):>11.5f} "
                  f"{r['height_m']:>9.3f} {r['origin_above_base']:>7.3f} "
                  f"{str(r['master_type']):<8} {r['seconds'].get('total', 0):>9.1f} "
                  f"{sorted(r['maps'])}")
        by_key = {e["key"]: e for e in entries}
        for (key, model), r in sorted(finished.items()):
            check(f"{key}/{model}: face count within budget",
                  r["lod_faces"][0] <= FACES * 1.1,
                  f"{r['lod_faces'][0]} against {FACES} ({r['simplify_source']})")
            check(f"{key}/{model}: a UV layer with no overlap",
                  r["uv_overlap"] is not None and r["uv_overlap"] < 0.01,
                  f"{r['uv_overlap']:.6f} ({r['uv_source']})")
            # NOT the std: a perfectly flat tangent-space normal is (0.5, 0.5, 1.0), whose channel
            # spread reads std 0.2357, so every "std > 0.01" flatness check in this suite could not
            # fail. The neighbour difference is 0.0 on a constant image by construction.
            check(f"{key}/{model}: the baked normal is not flat",
                  (r.get("normal_detail") or 0) > 1e-4,
                  f"neighbour detail {r.get('normal_detail')}, std {r.get('normal_std')}")
            check(f"{key}/{model}: the dense and the low mesh baked in ONE frame",
                  abs((r.get("bake_rescale") or 1.0) - 1.0) < 1e-6
                  or r.get("normal_detail", 0) > 1e-4,
                  f"rescale {r.get('bake_rescale')}, detail {r.get('normal_detail')}")
            check(f"{key}/{model}: bbox height is the requested height_m",
                  abs(r["height_m"] - by_key[key]["height_m"]) < 0.01,
                  f"{r['height_m']} against {by_key[key]['height_m']}")
            check(f"{key}/{model}: origin at the base",
                  abs(r["origin_above_base"]) < 1e-3, f"{r['origin_above_base']} m above it")
            check(f"{key}/{model}: a three-step LOD chain", len(r["lod_faces"]) == 3,
                  str(r["lod_faces"]))
            check(f"{key}/{model}: materials.master_type() reports a BobShader",
                  r["master_type"] == "surface", str(r["master_type"]))

    if "d" in parts:
        results["d11"] = part_d()

    if entries and "b" in parts:
        results["verdict"] = verdict(entries, cells, results["summary"], finished,
                                     results.get("plate"))

    section("summary")
    results["cells"] = {f"{k}/{m}": v for (k, m), v in cells.items()}
    results["finished"] = {f"{k}/{m}": {kk: vv for kk, vv in v.items() if kk != "maps"}
                           for (k, m), v in finished.items()}
    results["seconds"] = {e["key"]: e["seconds"] for e in entries}
    results["vram"] = {e["key"]: e["vram"] for e in entries}
    with open(os.path.join(OUT, "geometry_ab_results.json"), "w") as fh:
        json.dump(results, fh, indent=2, sort_keys=True, default=str)
    note("results", os.path.join(OUT, "geometry_ab_results.json"))
    if not args.keep and os.path.isdir(PACK):
        shutil.rmtree(PACK)
    return GATE.exit_code()


if __name__ == "__main__":
    sys.exit(main())
