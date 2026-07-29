"""Per-class generation PASS RATES: what fraction of assets in each class clear their own receipt.

Every other gate in this repo asks "does this route work". This asks the question a user actually has:
**if I generate a thing of this kind, what are the odds it comes back usable?** That is a rate over
samples, not a verdict on one asset, and nothing here measured it -- which is how three rounds of one
scene's asset list turned into three rounds of fixing one barn.

The definition of a pass is deliberately not mine: an asset passes when its own receipt comes back with
`warnings: []`. That makes the rate a measurement of the SHIPPED vocabulary rather than of taste, and it
means the rate moves when a real gate is added -- which is correct, because an asset that would have
shipped a defect silently was never a pass.

Classes, and each one is a claim the tools make:

  rocks       `comfy_mesh(kind="rocks")`. The class image-to-3D is genuinely good at.
  deadwood    `kind="trees"`, which returns one solid mesh: stumps, logs, snags, root balls.
  structures  buildings, through the block-out control route. The class three rounds failed at.
  bark        `comfy_bark_set`. Gated on grain direction and flatness.
  tileable    `comfy_texture_set`. Ground, path, rock surfaces.
  atlas       `comfy_leaf_atlas`. Gated on cell orientation, blank cells and the light ramp.

    ~/.steam/steam/steamapps/common/Blender/blender --background --factory-startup \\
        --python tools/scripts/headless_gen_class_rates.py -- \\
        [--classes bark,tileable] [--n 3] [--fresh]

Every sample is cached under `_generated/class_rates/`, keyed by class and index, so a re-run costs
seconds and a partial run can be extended. Prints one table per class plus a warning histogram, and
writes `rates.json`. Exit 0 always: a rate is a measurement, not a pass or a fail -- the thing that
fails is a rate that DROPS, and that needs a previous run to compare against, which `rates.json` is.
"""

import argparse
import collections
import json
import os
import sys
import time

import bpy

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(REPO, "blender", "extensions"))

from bob_blender_tools.core import (  # noqa: E402
    comfy,
    gen_assets,
    gen_receipt,
    proxies,
)

OUT = os.path.join(REPO, "_generated", "class_rates")
PACK = os.path.join(OUT, "pack")
FACES = 4000

# Fixed prompts per class. Fixed, because a rate is only comparable between runs if the sample is the
# same -- a "better" prompt list would raise every rate and measure nothing. These are ordinary
# requests, not curated wins: the point is what a user gets, so two of the rocks are deliberately the
# awkward kind (a flat slab, a pitted vesicular stone) that the asset gate already argued about.
PROMPTS = {
    "rocks": [
        "a weathered granite boulder, mossy and chipped",
        "a rounded river stone, smooth and grey",
        "a pitted vesicular volcanic rock",
        "a flat sandstone slab, layered edges",
        "a cluster of small broken shale pieces",
        "a lichen-covered limestone rock",
        "a dark basalt boulder with sharp fractures",
        "a smooth chalk nodule",
    ],
    "deadwood": [
        "a rotting tree stump with exposed roots",
        "a fallen birch log, bark peeling",
        "a weathered pine snag, broken at the top",
        "an upturned root ball, soil still clinging",
        "a mossy fallen branch, forked",
        "a split log, dry and grey",
        "a hollow rotten stump",
        "a driftwood trunk, sea-worn",
    ],
    "structures": [
        "a small weathered timber barn with a simple gable roof",
        "a stone cottage with a slate roof",
        "a corrugated lean-to shed",
        "a brick outbuilding with a flat roof",
        "a timber-framed hay barn",
        "a small chapel with a steep gable",
        "a concrete pillbox",
        "a wooden boathouse on piles",
    ],
    "bark": [
        "rough conifer bark",
        "grey beech bark, smooth with lenticels",
        "shaggy cedar bark, long fibrous strips",
        "deeply furrowed oak bark",
        "white birch bark with dark scars",
        "flaking plane tree bark",
        "cork oak bark, thick and corky",
        "pine bark, red-brown plates",
    ],
    "tileable": [
        "very dark wet forest floor, leaf litter and twigs, low key, unlit",
        "damp compacted dirt path, small stones",
        "dark green forest moss, uneven",
        "wet grey granite surface",
        "dry cracked mud",
        "weathered vertical timber boards, dark",
        "loose gravel, mixed grades",
        "short dark meadow grass from above",
    ],
    "atlas": [
        "a spruce needle spray on one short brown twig",
        "a spray of fresh green birch leaves along one short brown twig",
        "an oak leaf cluster on one short brown twig",
        "a fern frond on one short stem",
        "a willow shoot with narrow green leaves on one twig",
        "a maple leaf cluster on one short brown twig",
        "a holly sprig on one short brown twig",
        "an ash leaf spray on one short brown twig",
    ],
}

# Which classes are meshes (they need the Blender finish before a receipt exists) and which are
# textures (their receipt comes straight off the generation).
MESH_CLASSES = {"rocks": "rocks", "deadwood": "trees", "structures": "rocks"}
TEXTURE_CLASSES = ("bark", "tileable", "atlas")

# The block-out every structure sample is conditioned on. Structures go through the control route
# because that is the shipped answer for the class -- generating a building from an image alone was
# measured as the worst case (core/proxies.py) and a rate for it would measure a route nobody should
# use.
STRUCTURE_HEIGHT = 7.5

SEED = 4242


def empty_scene():
    bpy.ops.wm.read_factory_settings(use_empty=True)


def sample_dir(cls, i):
    path = os.path.join(OUT, cls, f"{i:02d}")
    os.makedirs(path, exist_ok=True)
    return path


def cached(path):
    try:
        with open(path) as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def texture_sample(cls, i, prompt, fresh):
    """One texture-family sample, through the same warning functions the MCP tools attach."""
    target = os.path.join(sample_dir(cls, i), "receipt.json")
    if not fresh and (hit := cached(target)) is not None:
        return hit
    t0 = time.time()
    try:
        if cls == "bark":
            _name, info = comfy.bark_set(prompt, PACK, seed=SEED, size=1024)
            warnings = (gen_receipt.flatness_warning(info.get("flatness"))
                        + gen_receipt.grain_warning(info.get("grain")))
        elif cls == "atlas":
            _name, info = comfy.leaf_atlas(prompt, PACK, cols=2, rows=2, seed=SEED, size=1024,
                                           delight=True)
            warnings = (gen_receipt.flatness_warning(info.get("flatness"), leafy=True)
                        + gen_receipt.orientation_warning(info.get("cells"))
                        + gen_receipt.blank_cell_warning(info.get("cells")))
        else:
            _name, info = comfy.texture_set_from_prompt(prompt, PACK, seed=SEED, size=1024,
                                                        delight=True)
            warnings = gen_receipt.flatness_warning(info.get("flatness"))
    except comfy.ComfyError as exc:
        out = {"prompt": prompt, "error": str(exc)[:300], "warnings": ["GENERATION FAILED"],
               "seconds": round(time.time() - t0, 1)}
        with open(target, "w") as fh:
            json.dump(out, fh, indent=2, sort_keys=True, default=str)
        return out
    out = {"prompt": prompt, "warnings": warnings, "seconds": round(time.time() - t0, 1),
           "flatness": info.get("flatness"), "grain": info.get("grain"),
           "seam": info.get("seam")}
    with open(target, "w") as fh:
        json.dump(out, fh, indent=2, sort_keys=True, default=str)
    return out


def mesh_sample(cls, i, prompt, fresh):
    """One mesh sample, generated then FINISHED, because the receipt only exists after the finish."""
    target = os.path.join(sample_dir(cls, i), "receipt.json")
    if not fresh and (hit := cached(target)) is not None:
        return hit
    kind = MESH_CLASSES[cls]
    t0 = time.time()
    control = None
    if cls == "structures":
        empty_scene()
        made = proxies.make_blockout({"shape": "shed", "name": f"B_{i}", "replace": True})
        block = bpy.data.objects[made["data"]["object"]]
        control = os.path.join(sample_dir(cls, i), "control.glb")
        gen_assets.export_control(block, control)
    try:
        staged = comfy.asset_chain(kind=kind, control=control)(
            prompt, PACK, seed=SEED, faces=FACES, control=control,
            control_mode="point" if control else None)
        empty_scene()
        report = gen_assets.finish_asset(
            staged["raw_mesh"], PACK, kind=kind, name=f"{cls}_{i:02d}",
            height_m=STRUCTURE_HEIGHT if cls == "structures" else 1.5, faces=FACES,
            exports=comfy.stage_exports(staged),
            simplify_pass=staged.get("simplified_mesh"),
            texture_pass=staged.get("textured_mesh"))
    except comfy.ComfyError as exc:
        out = {"prompt": prompt, "error": str(exc)[:300], "warnings": ["GENERATION FAILED"],
               "seconds": round(time.time() - t0, 1)}
        with open(target, "w") as fh:
            json.dump(out, fh, indent=2, sort_keys=True, default=str)
        return out
    out = {"prompt": prompt, "warnings": report.get("warnings") or [],
           "seconds": round(time.time() - t0, 1), "faces": report.get("faces"),
           "low_openness": report.get("low_openness"),
           "bake_fidelity": report.get("bake_fidelity"),
           "map_stats": report.get("map_stats")}
    with open(target, "w") as fh:
        json.dump(out, fh, indent=2, sort_keys=True, default=str)
    return out


def label(warning):
    """A warning collapsed to a short tag, so the histogram groups the same defect across samples."""
    text = warning.lower()
    for needle, tag in (("carries no picture", "empty-map"),
                        ("carries baked lighting", "baked-light"),
                        ("degrees off vertical", "grain-sideways"),
                        ("carry no sprite", "blank-cell"),
                        ("cue that could not be read", "orientation-guess"),
                        ("see through", "see-through"),
                        ("boundary edges", "open-surface"),
                        ("claims to be metal", "metal-claim"),
                        ("resampled the surface", "bake-resample"),
                        ("no cutout", "no-cutout"),
                        ("inside it", "control-interior"),
                        ("generation failed", "generation-failed")):
        if needle in text:
            return tag
    return "other"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--classes", default="bark,tileable")
    parser.add_argument("--n", type=int, default=3)
    parser.add_argument("--fresh", action="store_true")
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    args = parser.parse_args(argv)

    os.makedirs(OUT, exist_ok=True)
    gen_assets.ensure_generated_pack(PACK) if hasattr(gen_assets, "ensure_generated_pack") else None
    ok, detail = comfy.reachable()
    print(f"ComfyUI: {detail if ok else 'NOT REACHABLE'}")
    if not ok:
        print("[SKIP] every class needs a server; nothing measured")
        sys.exit(0)

    wanted = [c.strip() for c in args.classes.split(",") if c.strip()]
    rates = {}
    for cls in wanted:
        if cls not in PROMPTS:
            print(f"[SKIP] unknown class {cls!r}; have {sorted(PROMPTS)}")
            continue
        prompts = PROMPTS[cls][:max(1, args.n)]
        print(f"\n-- {cls}: {len(prompts)} samples "
              + "-" * max(0, 60 - len(cls)))
        results, histogram = [], collections.Counter()
        for i, prompt in enumerate(prompts):
            sample = (mesh_sample if cls in MESH_CLASSES else texture_sample)(
                cls, i, prompt, args.fresh)
            warnings = sample.get("warnings") or []
            results.append(sample)
            for w in warnings:
                histogram[label(w)] += 1
            verdict = "PASS" if not warnings else "fail"
            tags = ", ".join(sorted({label(w) for w in warnings})) or "-"
            print(f"   [{verdict}] {prompt[:52]:54s} {sample.get('seconds', 0):6.1f} s  {tags}")
        passed = sum(1 for r in results if not (r.get("warnings") or []))
        rate = passed / len(results)
        rates[cls] = {"n": len(results), "passed": passed, "rate": round(rate, 3),
                      "histogram": dict(histogram), "samples": results}
        print(f"   RATE {passed}/{len(results)} = {rate * 100:.0f}%"
              + (f"   most common: {histogram.most_common(1)[0][0]}" if histogram else ""))

    print("\n-- Pass rates " + "-" * 63)
    print("| class | n | pass | rate | most common failure |")
    print("|---|---|---|---|---|")
    for cls, row in rates.items():
        common = (max(row["histogram"], key=row["histogram"].get)
                  if row["histogram"] else "-")
        print(f"| {cls} | {row['n']} | {row['passed']} | {row['rate'] * 100:.0f}% | {common} |")

    with open(os.path.join(OUT, "rates.json"), "w") as fh:
        json.dump(rates, fh, indent=2, sort_keys=True, default=str)
    print(f"\nwritten to {os.path.join(OUT, 'rates.json')}")


if __name__ == "__main__":
    main()
