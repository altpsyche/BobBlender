"""forest-barn: finish the block-out-conditioned barn and measure everything about it.

The Blender half after `comfy_mesh(control=...)`: steps 6 to 8 (`gen_assets.finish_asset`) and then
every figure the two artist rejections asked for, against the BLOCK-OUT rather than against an
impression. Runs headless so the live session holding gate A is untouched.

What it reports, and why each one is here:

  - the POSITION extents of every staged file. `Trellis2EncodeMesh` voxelises in the unit cube, so a
    file whose longest extent is not about 1.0 is a file `mesh_texture` cannot see -- the cause of the
    all-black texture set the last round shipped.
  - the albedo's SPREAD, not its presence. A 2048 square of pure black passed a presence check with
    every other figure in the receipt healthy.
  - the height PROFILE against the block-out, band by band. The footprint IoU cannot tell a barn from
    an A-frame: measured, a synthetic A-frame at the same bbox scores 0.8376.
  - the receipt's own warnings, openness, metalness and bake fidelity.
  - clay and textured three-quarter renders, because no gate catches "looks wrong".

    ~/.steam/steam/steamapps/common/Blender/blender --background --factory-startup \\
        --python tools/scripts/forest_barn_finish.py -- --staged <dir>
"""

import argparse
import importlib.util
import json
import os
import sys

import bpy

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(REPO, "blender", "extensions"))

from bob_blender_tools.core import (  # noqa: E402
    comfy,
    gen_assets,
    gen_views,
    materials,
    proxies,
)

# The shape maths lives in the gate that calibrated it (`blockout-control`), so it is imported rather
# than copied: a second implementation of `plan_profile` would be a second set of bars to keep true.
_spec = importlib.util.spec_from_file_location(
    "_blockout_gate", os.path.join(REPO, "tools", "scripts", "headless_gen_blockout_control.py"))
gate = importlib.util.module_from_spec(_spec)
sys.modules["_blockout_gate"] = gate
_spec.loader.exec_module(gate)

OUT = os.path.join(REPO, "_generated", "forest_barn")
PACK = os.path.join(REPO, "packs", "generated")
NAME = "BarnShed"


def empty_scene():
    bpy.ops.wm.read_factory_settings(use_empty=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--staged", required=True, help="the _staging/<variant> directory")
    parser.add_argument("--name", default=NAME)
    parser.add_argument("--faces", type=int, default=8000)
    # A re-textured GLB in place of the chain's own. `comfy_paint_mesh` re-runs `mesh_texture` on the
    # already-normalised simplified mesh, which is one 20 s job against a whole regeneration, so a
    # reference can be swapped and judged without touching the geometry. The export count is unchanged:
    # simplified + one texture pass is three turns either way.
    parser.add_argument("--texture", default=None,
                        help="override meta.json's textured_mesh with this GLB")
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    args = parser.parse_args(argv)

    with open(os.path.join(args.staged, "meta.json")) as fh:
        meta = json.load(fh)
    with open(os.path.join(OUT, "barn_control.json")) as fh:
        blockout = json.load(fh)

    staged = {"meta": meta, "raw_mesh": meta["raw_mesh"],
              "simplified_mesh": meta["simplified_mesh"],
              "textured_mesh": args.texture or meta["textured_mesh"]}
    if args.texture:
        print(f"[texture] overridden with {args.texture}")
    exports = comfy.stage_exports(staged)

    print("-- the space every staged file arrives in")
    for label, path in (("control", blockout["control"]), ("raw", staged["raw_mesh"]),
                        ("simp", staged["simplified_mesh"]), ("tex", staged["textured_mesh"])):
        extents = gate.glb_extents(path)
        print(f"   {label:8s} {extents}  longest {max(extents):.5f}")
    print(f"   turns to undo per file: {exports}")

    # The block-out, rebuilt and sampled, so the finished asset is scored against the shape the layout
    # was composed around rather than against its own bounding box.
    empty_scene()
    made = proxies.make_blockout({"shape": "shed", "name": "BOB_Blockout_Barn", "replace": True,
                                  "params": blockout["params"]})
    proxy = bpy.data.objects[made["data"]["object"]]
    proxy_points = gate.mesh_points(proxy, seed=1)
    ceiling = gate.profile_agreement(proxy_points, gate.mesh_points(proxy, seed=99))
    total, inside = gate.hidden_surface(proxy)
    print(f"\n-- the block-out: {made['info']}")
    print(f"   hidden surface {inside:.2f} m2 of {total:.2f} ({100.0 * inside / total:.1f}% of every "
          f"control point)")
    print(f"   profile self-agreement: max band deviation {ceiling['max_deviation']:.4f}")

    empty_scene()
    report = gen_assets.finish_asset(staged["raw_mesh"], PACK, kind="structure", name=args.name,
                                     height_m=float(blockout["params"]["ridge_h"]),
                                     faces=args.faces, exports=exports,
                                     simplify_pass=staged["simplified_mesh"],
                                     texture_pass=staged["textured_mesh"])
    obj = gen_assets.import_generated(report["name"], kind="structure", pack_dir=PACK)
    dims = gen_assets.dimensions(obj)
    low, _high = gen_assets.bbox_world(obj)
    points = gate.mesh_points(obj, seed=2)
    agree = gate.fixed_agreement(proxy_points, points)
    profile = gate.profile_agreement(proxy_points, points)
    stats = (report.get("map_stats") or {}).get("basecolor") or {}

    print(f"\n-- the finished asset")
    print(f"   faces {report['faces']} of {args.faces}, LODs {report['lod_faces']}")
    print(f"   size {tuple(round(d, 4) for d in dims)} m against the block-out's "
          f"{blockout['dimensions']}")
    print(f"   origin above base {abs(low[2] - obj.location[2]):.5f} m, "
          f"uv_overlap {report['uv_overlap']}, master {materials.master_type(obj.active_material)}")
    print(f"   maps {', '.join(sorted(report.get('maps') or {}))}")
    print(f"   albedo spread {stats.get('std')} mean {stats.get('mean')} "
          f"(bar {gate.gen_receipt.MAP_SPREAD_MIN}, honest maps measure 33 to 58)")
    print(f"   bake_fidelity {report.get('bake_fidelity')}")
    print(f"   metalness {report.get('metalness')}")
    # The count and the classification, in that order and never one without the other. This route
    # hands over a mesh straight from `mesh_simplify_uv`, which neither welds nor repairs, so the
    # count is inflated by every UV seam glTF split a vertex at -- measured 1,187 unwelded against 24
    # welded on the previous control-conditioned structure. `low_openness` welds a copy of its own and
    # is the figure to read.
    print(f"   low_boundary_edges {report.get('low_boundary_edges')} (UNWELDED on this route, see "
          f"low_openness for the real figure)")
    print(f"   source_boundary_edges {report.get('source_boundary_edges')}")
    print(f"   low_openness {report.get('low_openness')}")
    print(f"   footprint IoU {agree['footprint_iou']:.4f}, IoU {agree['iou']:.4f}, "
          f"aspect {agree['aspect']}")
    print(f"   profile max band deviation {profile['max_deviation']:.4f} "
          f"(bar {gate.PROFILE_DEVIATION_MAX}, 0.2551 for an A-frame), lower-half ratio "
          f"{profile['wall_band_ratio']:.4f}")
    print(f"   proxy  {profile['proxy_profile']}")
    print(f"   asset  {profile['candidate_profile']}")
    warnings = report.get("warnings") or []
    print(f"   warnings ({len(warnings)}):")
    for line in warnings:
        print(f"     - {line}")

    # `import_generated` lands the asset in an off-scene pool (BOB_Assets_<Kind>), so it has to be
    # linked into the scene before anything can photograph it -- the first run of this script rendered
    # four frames of an empty white world and they looked like a clean pass.
    if obj.name not in bpy.context.scene.collection.objects:
        bpy.context.scene.collection.objects.link(obj)
    light = bpy.data.objects.new("Sun", bpy.data.lights.new("Sun", "SUN"))
    light.data.energy = 3.0
    light.rotation_euler = (0.95, 0.0, 0.6)
    bpy.context.scene.collection.objects.link(light)
    world = bpy.data.worlds.new("BarnWorld")
    world.use_nodes = True
    world.node_tree.nodes["Background"].inputs[1].default_value = 0.35
    bpy.context.scene.world = world
    bpy.context.view_layer.update()

    # `flat_light=False`, because these frames are for LOOKING at rather than for becoming an albedo:
    # a uniform white world is what the paint route wants and it flattens the form out of a shape.
    views = gen_views.turntable_views(obj, os.path.join(OUT, "barn_views"), count=4,
                                      elevation=18.0, extra_elevations=(), resolution=1024,
                                      samples=48, engine="BLENDER_EEVEE", stem="barn",
                                      isolate=False, flat_light=False)
    print(f"\n-- renders: {[v['beauty'] for v in views]}")

    with open(os.path.join(OUT, "barn_finish.json"), "w") as fh:
        json.dump({"report": report, "agreement": agree, "profile": profile,
                   "profile_ceiling": ceiling, "exports": exports, "meta": meta,
                   "views": [v["beauty"] for v in views]},
                  fh, indent=2, sort_keys=True, default=str)


if __name__ == "__main__":
    main()
