"""Headless measurement of the G2 gate (docs/COMFYUI.md).

Measures rather than asserts. Seven things, in the order the gate lists them:

  1. ten sets generated and accepted in one session, per-set wall clock and drift
  2. the longest MAIN-THREAD block during a background generation, against the blocking G1 path
  3. preflight over every shipped workflow, and over five deliberately broken graphs
  4. the 3x3 seam ratio before and after a W3 upres of the same tile
  5. roughness contrast, G1's global band against G2's local stretch, on the same image
  6. a job does not outlive a file load
  7. the Advanced-panel surface registers and draws without touching a socket

    ~/.steam/steam/steamapps/common/Blender/blender --background --factory-startup \
        --python tools/scripts/headless_comfy_g2.py [-- --sets 10]

The parts that need a server are gated on reachability and SKIP cleanly; the parts that do not
(preflight against the committed dump, the maps, the scheduler, the load_post reset) always run,
because "ComfyUI is never required" is itself one of the properties under test. Exit 0 = nothing
failed.
"""

import argparse
import json
import os
import shutil
import statistics
import sys
import threading
import time

import bpy
import numpy as np

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(REPO, "blender", "extensions"))

from bob_blender_tools.core import (  # noqa: E402
    assets, comfy, comfy_jobs, comfy_maps, materials, shading,
)

FAILURES = []
OUT = os.path.join(REPO, "_generated", "comfy_g2_check")
PACK = os.path.join(OUT, "pack")
DUMP = os.path.join(REPO, "tools", "tests", "data", "object_info_min.json")

PROMPTS = [
    "wet river pebbles with dark sand between them",
    "cracked dry desert soil with small pebbles",
    "mossy forest floor with damp twigs",
    "weathered granite with pale lichen",
    "coarse volcanic scree, angular black rock",
    "short dry meadow grass and thatch",
    "compacted dirt track with wheel ruts",
    "frost-shattered limestone slabs",
    "pine needle litter over dark humus",
    "fine wind-rippled dune sand",
]


def check(label, ok, detail=""):
    print(f"[{'PASS' if ok else 'FAIL'}] {label}" + (f" -- {detail}" if detail else ""))
    if not ok:
        FAILURES.append(label)
    return ok


def section(title):
    print()
    print(f"-- {title} " + "-" * max(0, 76 - len(title)))


# -- 3. preflight ------------------------------------------------------------------------------
def _node(cls, title, **inputs):
    return {"class_type": cls, "inputs": inputs, "_meta": {"title": title}}


def preflight_checks(info):
    section("preflight")
    for name in sorted(f for f in os.listdir(comfy.WORKFLOW_DIR) if f.endswith(".json")):
        prompt, prov = comfy.load_workflow(name)
        problems = comfy.preflight(prompt, info=info, required_titles=("BOB_OUT",),
                                   runtime_inputs=prov.get("runtime_inputs") or ())
        check(f"shipped graph preflights: {name}", not problems, "; ".join(problems))

    good = {"1": _node("CheckpointLoaderSimple", "BOB_CKPT",
                       ckpt_name=comfy.combo_options("CheckpointLoaderSimple", "ckpt_name",
                                                     info=info)[0]),
            "2": _node("SaveImage", "BOB_OUT", images=["1", 0], filename_prefix="bob/x")}
    broken = {
        "unknown class_type": (dict(good, x=_node("APackNobodyInstalled", "BOB_X")),
                               "unknown node"),
        "cloud api_node": (dict(good, x=_node(_an_api_node(info), "BOB_X")),
                           "cloud node rejected"),
        "missing model": ({**good, "1": _node("CheckpointLoaderSimple", "BOB_CKPT",
                                              ckpt_name="never_downloaded.safetensors")},
                          "missing model"),
        "duplicate BOB_* title": (dict(good, x=_node("SaveImage", "BOB_OUT", images=["1", 0],
                                                     filename_prefix="bob/y")),
                                  "duplicate title"),
        "UUID subgraph node": (dict(good, x=_node("6f1c4e2a-9b3d-4f7a-8c2e-1d5b7a9e3c04",
                                                  "BOB_X")),
                               "subgraph node rejected"),
    }
    for label, (graph, expected) in broken.items():
        problems = comfy.preflight(graph, info=info)
        hit = [p for p in problems if p.startswith(expected)]
        check(f"preflight catches {label}", bool(hit), hit[0] if hit else str(problems))


def _an_api_node(info):
    for name in sorted(info):
        if info[name].get("api_node"):
            return name
    return "TencentImageToModelNode"


# -- 5. roughness contrast ---------------------------------------------------------------------
def g1_roughness(rgb, band=(0.45, 0.95)):
    """G1's roughness verbatim, so the comparison is against the real thing rather than a memory
    of it: desaturate, invert, remap into the band."""
    inv = 1.0 - comfy_maps.luminance(rgb)
    lo, hi = band
    return np.clip((lo + inv * (hi - lo)) * 255.0 + 0.5, 0, 255).astype(np.uint8)


def roughness_comparison(albedo):
    section("roughness contrast, same image")
    old, new = g1_roughness(albedo), comfy_maps.roughness_from(albedo)
    rows = []
    for label, m in (("G1 global band", old), ("G2 local stretch", new)):
        rows.append((label, int(m.min()), int(m.max()), float(m.mean()), float(m.std())))
        print(f"    {label:18s} range {m.min():3d}-{m.max():3d} of 255, "
              f"mean {m.mean():6.1f}, std {m.std():5.1f}")
    check("roughness contrast improved", rows[1][4] > rows[0][4] * 1.5,
          f"std {rows[0][4]:.1f} -> {rows[1][4]:.1f}")
    check("roughness is no longer bunched at the top of the band", rows[1][3] < rows[0][3],
          f"mean {rows[0][3]:.1f} -> {rows[1][3]:.1f}")
    return rows


# -- 2. main-thread responsiveness ---------------------------------------------------------------
def responsiveness(prompt):
    """The longest main-thread block during a background generation.

    Measured, not asserted: a loop stands in for Blender's event loop, calling `tick()` the way
    the timer does and recording the wall clock between consecutive iterations. Any main-thread
    work the job costs shows up as a long iteration, because there is nothing else in the loop.
    """
    section("main-thread block during a generation")
    comfy_jobs.clear()
    comfy_jobs.max_tick_seconds(reset=True)
    staging = comfy.staging_dir(PACK)
    job = comfy_jobs.submit("responsiveness", lambda j: comfy.texture_variants(
        prompt, PACK, count=1, seed=91000, on_variant=lambda i, n, info: j.report(f"{i}/{n}")))

    gaps, spin = [], 0.001
    last = time.perf_counter()
    while comfy_jobs.tick():
        time.sleep(spin)
        now = time.perf_counter()
        gaps.append(now - last)
        last = now
    comfy_jobs.tick()
    worst = max(gaps) - spin if gaps else 0.0
    print(f"    {len(gaps)} main-thread iterations while the job ran "
          f"({job.seconds:.1f} s of work)")
    print(f"    longest iteration           {max(gaps) * 1000:7.2f} ms "
          f"({worst * 1000:.2f} ms of it main-thread work)")
    print(f"    longest single tick()       {comfy_jobs.max_tick_seconds() * 1000:7.2f} ms")
    check("job completed off the main thread", job.state == "done", str(job.error or ""))
    check("no main-thread block over 50 ms (three frames at 60 Hz)", worst < 0.050,
          f"{worst * 1000:.2f} ms")

    # The same work on the main thread, which is what G1 shipped, for the comparison.
    t0 = time.perf_counter()
    comfy.texture_set_from_prompt(prompt, PACK, seed=91001)
    blocking = time.perf_counter() - t0
    print(f"    the G1 blocking path, same work: {blocking * 1000:8.1f} ms on the main thread")
    print(f"    improvement: {blocking / max(worst, 1e-6):.0f}x shorter worst-case block")
    shutil.rmtree(staging, ignore_errors=True)
    return {"worst_block_ms": worst * 1000, "max_tick_ms": comfy_jobs.max_tick_seconds() * 1000,
            "blocking_ms": blocking * 1000}


# -- 6. a job does not outlive a file load -------------------------------------------------------
def load_post_reset():
    section("a job does not outlive a file load (R15)")
    comfy_jobs.register()
    comfy_jobs.clear()
    gate = threading.Event()
    fired = []
    comfy_jobs.submit("slow", lambda j: gate.wait(5) or "late", on_done=fired.append)
    check("job registered", len(comfy_jobs.active()) == 1)
    bpy.ops.wm.read_homefile(use_empty=True)   # fires load_post
    check("registry cleared by load_post", comfy_jobs.jobs() == [],
          f"{len(comfy_jobs.jobs())} left")
    gate.set()
    time.sleep(0.2)
    comfy_jobs.tick()
    check("the callback never ran against the new file", fired == [], str(fired))
    comfy_jobs.unregister()


# -- 1. ten sets, and 4. the seam through an upres -----------------------------------------------
def terrain_scene():
    bpy.ops.mesh.primitive_grid_add(x_subdivisions=32, y_subdivisions=32, size=20.0)
    obj = bpy.context.active_object
    obj.name = "Terrain"
    mat = materials.terrain_material("Terrain", terrain_size=20.0)
    materials.assign_material(obj, mat)
    return obj, mat


def ten_sets(count):
    section(f"{count} sets generated and accepted in one session")
    obj, mat = terrain_scene()
    times, names = [], []
    for i in range(count):
        prompt = PROMPTS[i % len(PROMPTS)]
        t0 = time.time()
        info = comfy.texture_variants(prompt, PACK, count=1, seed=1000 + i * 7)[0]
        name = comfy.accept_variant(info["dir"], PACK)
        shading.set_terrain_texture(obj, mat, i % materials.MAX_TERRAIN_LAYERS, name)
        times.append(time.time() - t0)
        names.append(name)
        print(f"    {i + 1:2d}. {times[-1]:5.2f} s  seam {info['seam']['ratio']:.2f}  {name}")
    half = max(1, len(times) // 2)
    first, second = statistics.mean(times[:half]), statistics.mean(times[half:])
    drift = second - first
    print(f"    mean {statistics.mean(times):.2f} s, first half {first:.2f} s, "
          f"second half {second:.2f} s, drift {drift:+.2f} s")
    check(f"{count} sets in one session with no restart", len(names) == count)
    check("every accepted set resolves through the picker",
          all(assets.texture_set_maps(n).get("basecolor") for n in names))
    check("nothing left in staging", comfy.list_variants(PACK) == [],
          f"{len(comfy.list_variants(PACK))} left")
    # A leak shows up as drift: 10% of the mean is well inside run-to-run variance on a shared GPU.
    check("no drift across the session", abs(drift) < 0.10 * statistics.mean(times),
          f"{drift:+.2f} s over {count} sets")
    return {"times": times, "mean": statistics.mean(times), "drift": drift, "names": names}


def upres_seam():
    section("seam through a W3 upres, same tile")
    info = comfy.texture_variants("weathered granite with pale lichen", PACK, count=1,
                                  seed=54321)[0]
    before = info["seam"]
    t0 = time.time()
    up = comfy.upres_variant(info["dir"], scale=2.0)
    secs = time.time() - t0
    after = up["seam"]
    print(f"    before  {info['size']}^2  seam {before['seam']:6.2f}  "
          f"interior {before['interior']:6.2f}  ratio {before['ratio']:.3f}")
    print(f"    after   {up['size']}^2  seam {after['seam']:6.2f}  "
          f"interior {after['interior']:6.2f}  ratio {after['ratio']:.3f}   ({secs:.1f} s)")
    albedo = comfy_maps.read_png(open(up["maps"]["basecolor"], "rb").read())
    comfy_maps.write_png(os.path.join(OUT, "upres_tile3x3.png"), comfy_maps.tile3x3(
        albedo[::2, ::2]))
    check("seam still holds after the upres (G1 band was 0.83 to 1.05)",
          0.80 <= after["ratio"] <= 1.25, f"ratio {after['ratio']:.3f}")
    comfy.reject_variant(info["dir"])
    return {"before": before, "after": after, "seconds": secs, "size": up["size"]}


def addon_surface():
    """The Advanced-panel service surface, proved to register and to draw without a socket call.

    Needs the extension installed (the dev symlink into Blender's user_default repo); SKIPs when
    it is not, because the rest of this script drives `core/` directly and does not need it.
    """
    section("Advanced panel ComfyUI surface")
    module = "bl_ext.user_default.bob_blender_tools"
    try:
        bpy.ops.preferences.addon_enable(module=module)
        addon = sys.modules[module]
    except (RuntimeError, KeyError) as exc:
        print(f"[SKIP] extension not installed for this Blender ({exc})")
        return
    prefs = bpy.context.preferences.addons[module].preferences
    check("ComfyUI preferences exist",
          all(hasattr(prefs, p) for p in ("comfy_url", "comfy_repo", "comfy_reserve_vram")))
    ops = ("comfy_test", "comfy_free", "comfy_start", "comfy_stop", "comfy_cancel",
           "shaders_generate_set", "shaders_variant_accept", "shaders_variant_reject",
           "shaders_variant_upres")
    missing = [o for o in ops if not hasattr(bpy.ops.bob_blender_tools, o)]
    check("every G2 operator registered", not missing, ", ".join(missing))
    check("the persistent load_post handler is installed",
          any(getattr(h, "_bpy_persistent", False) or h.__name__ == "_handler"
              for h in bpy.app.handlers.load_post))

    # The property that matters: the panel body reads cached state, so drawing it with no server
    # costs nothing. A socket call here would freeze the UI for the timeout.
    class _Stub:
        def label(self, **kw):
            pass

        def row(self, **kw):
            return self

        def operator(self, *a, **kw):
            return type("P", (), {"job_id": 0})()

    t0 = time.perf_counter()
    addon._draw_comfy_service(_Stub())
    drew = (time.perf_counter() - t0) * 1000
    check("the panel body draws without probing the server", drew < 5.0, f"{drew:.3f} ms")
    bpy.ops.preferences.addon_disable(module=module)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sets", type=int, default=10)
    args = ap.parse_args(sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else [])

    shutil.rmtree(PACK, ignore_errors=True)
    os.makedirs(os.path.join(PACK, "textures"), exist_ok=True)
    with open(os.path.join(PACK, "pack.json"), "w") as fh:
        json.dump({"schema": 1, "id": "generated", "name": "Generated"}, fh)
    assets.set_generated_root(PACK)

    # Always-run half: no server needed.
    with open(DUMP) as fh:
        preflight_checks(json.load(fh))
    addon_surface()
    load_post_reset()

    ok, detail = comfy.reachable()
    print()
    print(f"    ComfyUI: {detail}")
    report = {}
    if not ok:
        print("[SKIP] no ComfyUI server, so the generation half cannot run")
        print("    the suite is unaffected: this is the 'ComfyUI is never required' path")
    else:
        report["responsiveness"] = responsiveness(PROMPTS[0])
        report["sets"] = ten_sets(args.sets)
        report["upres"] = upres_seam()
        first = assets.texture_set_maps(report["sets"]["names"][0])["basecolor"]
        with open(first, "rb") as fh:
            report["roughness"] = roughness_comparison(comfy_maps.read_png(fh.read()))
        with open(os.path.join(OUT, "g2_report.json"), "w") as fh:
            json.dump(report, fh, indent=2, default=str)

    print()
    if FAILURES:
        print(f"{len(FAILURES)} FAILED: " + "; ".join(FAILURES))
    else:
        print("all checks passed")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
