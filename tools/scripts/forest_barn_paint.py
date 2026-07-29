"""forest-barn: texture the barn through the PROJECTION route instead of the single-view one.

The experiment the single-view failure argues for. `mesh_texture` conditions on ONE reference image
and invents every surface that image cannot see, which is measured rather than supposed: with a low
three-quarter reference the barn's roof came back carrying door panels and X-bracing, and with an
aerial one the roof came back clean and the gable end lost its board rhythm. Two references, two
different faces right, neither both, and no receipt figure separating them.

So this runs the "stylised" texture route (`comfy.texture_chain("stylised")` -- `comfy.paint_views`
plus `core.gen_paint`), which restyles N turntable views and projection-bakes them, so every surface
is painted by a camera that can see it. Worth writing down that this route is reachable from NEITHER
the panel nor MCP -- `texture_chain` has no caller and `gen_paint` has one, a gate script -- which is
why this is a script (docs/ROADMAP.md, the agent-and-artist parity item).

**Painted from CLAY, and that is the whole reason it can work.** `paint_views` restyles a RENDER, so
painting the finished asset would hand the model the defect it is meant to remove and a paint-denoise
restyle would faithfully keep it. The object is stripped to flat grey first, so each view is invented
from the geometry and the prompt rather than edited from a wrong texture -- which also means the
denoise has to be well above `comfy.PAINT_DENOISE`, whose whole job is keeping the render dominant.

    ~/.steam/steam/steamapps/common/Blender/blender --background --factory-startup \\
        --python tools/scripts/forest_barn_paint.py -- [--name BarnShedAerial] [--views 6]
        [--denoise 0.85]
"""

import argparse
import json
import os
import sys

import bpy

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(REPO, "blender", "extensions"))

from bob_blender_tools.core import (  # noqa: E402
    comfy,
    gen_assets,
    gen_paint,
    gen_views,
    materials,
)

OUT = os.path.join(REPO, "_generated", "forest_barn")
PACK = os.path.join(REPO, "packs", "generated")

PROMPT = ("weathered dark charcoal timber barn, vertical board siding on the walls, plank and "
          "shingle roofing on the roof planes, flat even overcast light, no cast shadows")


def clay(obj, value=0.55):
    """Strip the object to one flat grey material, so the paint route invents a surface rather than
    editing the one that is already wrong."""
    obj.data.materials.clear()
    mat = bpy.data.materials.new("M_BarnClay")
    mat.use_nodes = True
    bsdf = mat.node_tree.nodes.get("Principled BSDF")
    if bsdf:
        bsdf.inputs["Base Color"].default_value = (value, value, value, 1.0)
        bsdf.inputs["Roughness"].default_value = 0.85
    obj.data.materials.append(mat)
    return mat


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--name", default="BarnShedAerial")
    parser.add_argument("--views", type=int, default=6)
    parser.add_argument("--denoise", type=float, default=0.85)
    parser.add_argument("--size", type=int, default=1024)
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    args = parser.parse_args(argv)

    ok, detail = comfy.reachable()
    print(f"[comfy]  {detail if ok else 'NOT REACHABLE: ' + detail}")
    if not ok:
        sys.exit(1)

    bpy.ops.wm.read_factory_settings(use_empty=True)
    obj = gen_assets.import_generated(args.name, kind="structure", pack_dir=PACK)
    if obj.name not in bpy.context.scene.collection.objects:
        bpy.context.scene.collection.objects.link(obj)
    print(f"[asset]  {obj.name}, {gen_assets.face_count(obj)} faces, "
          f"{tuple(round(d, 3) for d in obj.dimensions)} m")
    clay(obj)
    bpy.context.view_layer.update()

    views_dir = os.path.join(OUT, "paint_views")
    # `extra_elevations` keeps its shipped default and that is load-bearing. The first run of this
    # script passed (72.0,) alone, dropping the -55 underside view, and 23% of the chart went to the
    # hole fill and rendered as black wedges -- the barn has a floor face, and no camera above the
    # horizon sees it. The default's own comment says this: a ring at 20 degrees left 28% of a closed
    # boulder's charts unpainted.
    views = gen_views.turntable_views(obj, views_dir, count=args.views, elevation=18.0,
                                      extra_elevations=(72.0, -55.0), resolution=args.size,
                                      samples=32, engine="BLENDER_EEVEE", stem="clay")
    print(f"[views]  {len(views)} clay views, including one from above and one from below (the roof "
          f"and the floor each need a camera that can see them)")

    gbuf = gen_paint.uv_gbuffer(obj, size=args.size)
    print(f"[uv]     chart coverage {gbuf['mask'].mean():.3f}" if gbuf else "[uv] NO UV LAYER")

    # `paint_views` owns the IPAdapter reference itself -- the front view is its own reference and every
    # later view takes the stylised front -- so the palette is already decided once rather than per
    # view, and there is no reference to pass. Which means DENOISE is the only lever on inter-view
    # agreement here: measured at 0.85, adjacent-view overlap MAD ran 18.7 to 75.4 of 255, and 75 is a
    # seam you can see. Lower keeps the clay grey; higher invents more and agrees less.
    painted = comfy.paint_views(views, os.path.join(OUT, "paint_styled"), PROMPT, seed=57,
                               denoise=args.denoise, size=args.size)
    print(f"[paint]  {len(painted['images'])} views restyled in "
          f"{painted.get('total_seconds', 0):.1f} s at denoise {args.denoise}")

    out = gen_paint.paint_object(obj, views, painted["images"], os.path.join(OUT, "painted"),
                                "barn", size=args.size)
    report = out["report"]
    print(f"[bake]   coverage {report['coverage']:.3f}, painted "
          f"{report['painted'] * 100:.1f}% of chart texels directly from {report['views']} views "
          f"({report['ring']} in the ring), {report['unpainted']} texels to the hole fill")
    print(f"[bake]   maps {sorted(out['maps'])}, material {out['material']} "
          f"(master {materials.master_type(obj.active_material)})")
    for pair in report["pairs"]:
        mad = "n/a" if pair["mad"] is None else f"{pair['mad']:.1f}"
        print(f"[seam]   views {pair['views'][0]} to {pair['views'][1]}: "
              f"{pair['texels']} shared texels, overlap MAD {mad} of 255")
    drift = report["drift"]
    print(f"[drift]  front to {drift['views'][1]} (180 deg): {drift['texels']} texels, MAD "
          f"{'n/a' if drift['mad'] is None else format(drift['mad'], '.1f')} of 255")

    light = bpy.data.objects.new("Sun", bpy.data.lights.new("Sun", "SUN"))
    light.data.energy = 3.0
    light.rotation_euler = (0.95, 0.0, 0.6)
    bpy.context.scene.collection.objects.link(light)
    world = bpy.data.worlds.new("BarnWorld")
    world.use_nodes = True
    world.node_tree.nodes["Background"].inputs[1].default_value = 0.35
    bpy.context.scene.world = world
    bpy.context.view_layer.update()
    shots = gen_views.turntable_views(obj, os.path.join(OUT, "barn_painted_views"), count=4,
                                      elevation=18.0, extra_elevations=(), resolution=1024,
                                      samples=48, engine="BLENDER_EEVEE", stem="painted",
                                      isolate=False, flat_light=False)
    print(f"[render] {[s['beauty'] for s in shots]}")

    with open(os.path.join(OUT, "barn_paint.json"), "w") as fh:
        json.dump({"report": report, "prompt": PROMPT, "denoise": args.denoise,
                   "views": [v["beauty"] for v in views],
                   "styled": painted["images"], "maps": out["maps"],
                   "renders": [s["beauty"] for s in shots]},
                  fh, indent=2, sort_keys=True, default=str)


if __name__ == "__main__":
    main()
