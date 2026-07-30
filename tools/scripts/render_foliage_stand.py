"""The stand shot: BobFoliage conifers scattered on a terrain, framed like a whole-scene run.

    ~/.steam/steam/steamapps/common/Blender/blender --background --factory-startup \
        --python tools/scripts/render_foliage_stand.py -- [--samples 32] [--width 1280]

Writes `_generated/foliage_stand.png`. The point of the framing is the comparison: a whole-scene
run put a camera at eye level among GENERATED trunks and got the faceted fan with the flared root
skirt that started this whole track, so this puts a camera in the same place among procedural ones.
Same scatter, same terrain vocabulary, same eye height; the trees are the only thing that changed.

The stand is eight LIVE variants (docs/FOLIAGE.md 2.5), so it is also blowing while it is being
photographed -- which a still frame cannot show, and which is the whole reason a variant is not an
applied mesh.
"""

import argparse
import math
import os
import sys

import bpy

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(REPO, "blender", "extensions"))

from bob_blender_tools.core import assets, foliage_build, foliage_variants  # noqa: E402
from bob_blender_tools.core import scatter_build                            # noqa: E402
from bob_blender_tools.core import env as bbt_env                            # noqa: E402
from bob_blender_tools.core.dispatch import apply_op                         # noqa: E402

HEIGHTMAP = os.path.join(REPO, "library", "textures", "grass", "grass_height.png")
OUT = os.path.join(REPO, "_generated", "foliage_stand")


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--samples", type=int, default=32)
    ap.add_argument("--width", type=int, default=1280)
    # EEVEE Next is "BLENDER_EEVEE_NEXT" on some builds and plain "BLENDER_EEVEE" on others, so the
    # default is resolved against the enum this Blender actually has rather than named outright.
    ap.add_argument("--engine", default="")
    ap.add_argument("--variants", type=int, default=foliage_variants.DEFAULT_VARIANTS)
    args = ap.parse_args(argv)

    # The repo's generated pack, on the search path. The shipped species name a generated bark set
    # and a generated leaf atlas, and this addon is imported rather than registered, so without this
    # the stand is grown with the block-out fallbacks and the shot is about the wrong thing.
    pack = os.path.join(REPO, "packs", "generated")
    if os.path.isdir(pack):
        assets.add_pack_root(pack)

    scene = bpy.context.scene
    if getattr(scene, "bbt_env", None) is None:
        bbt_env.register()
        scene = bpy.context.scene
    scene.bbt_env.season = "summer"
    scene.bbt_env.wind_strength = 2.5
    scene.bbt_env.wind_direction = 55.0
    # Above freezing and the snow line above the peaks, or the shared env's defaults put a winter
    # coat on the terrain and the shot is about the snow instead of about the trees.
    scene.bbt_env.temperature = 17.0
    scene.bbt_env.snow_line = 1.2

    hero = foliage_build.grow("StandConifer",
                              dict(assets.foliage_species("conifer")["params"], seed=11),
                              species="conifer", scene=scene)
    report = foliage_variants.make_variants(hero, count=args.variants, levels=(0,), scene=scene)
    bpy.data.objects.remove(hero, do_unlink=True)
    print(f"    {report['count']} variants in {report['collection']}, "
          f"{report['lod_verts'][0]:,} verts each")

    apply_op({"op": "build_geonodes", "recipe": "heightmap_terrain", "name": "Ground",
              "params": {"heightmap": HEIGHTMAP, "size": 260.0, "resolution": 192,
                         "height": 20.0, "sea_level": 0.18}, "reset": True})
    # A shaded terrain, or the forest floor is the untextured white that the MCP work went and
    # killed and the shot would be about that instead.
    apply_op({"op": "shade_terrain", "object": "Ground", "stack": "temperate"})
    apply_op({"op": "apply_texture_set", "object": "Ground", "set": "grass", "layer": "grass"})
    scatter_build.build_recipe(
        "scatter", "Stand",
        {"emitter": "Ground", "assets": report["collection"], "align": "up", "density": 0.03,
         "distance_min": 5.5, "min_normal_z": 0.7, "min_scale": 0.8, "max_scale": 1.35,
         "z_offset": -0.3}, reset=True)
    bpy.context.view_layer.update()
    count, sources = foliage_variants.stand_report(bpy.data.objects["Stand"])
    print(f"    {count} trees over 260 m, drawing on {len(sources)} of {args.variants} variants")

    sun = bpy.data.objects.new("Sun", bpy.data.lights.new("Sun", type="SUN"))
    scene.collection.objects.link(sun)
    sun.data.energy = 6.0
    sun.data.angle = 0.09
    sun.rotation_euler = (0.62, 0.0, 0.7)
    sun.location = (0.0, 0.0, 90.0)
    # A sky rather than a void: under a single sun everything facing away from it is black, and a
    # canopy is mostly facing away from everything.
    world = scene.world or bpy.data.worlds.new("World")
    scene.world = world
    world.use_nodes = True
    bg = world.node_tree.nodes.get("Background")
    if bg is not None:
        bg.inputs["Color"].default_value = (0.42, 0.53, 0.68, 1.0)
        bg.inputs["Strength"].default_value = 1.1

    # Eye level among the trunks, which is that run's framing and the only one that shows
    # whether a crown reads: an overhead shot of any canopy is a green rug. The height is measured
    # off the terrain rather than guessed, or the camera ends up inside a ridge -- which is what
    # a fixed Z did on the first pass, and it renders as a very dark forest.
    ground = bpy.data.objects["Ground"].evaluated_get(bpy.context.evaluated_depsgraph_get())
    gmesh = ground.to_mesh()
    cam_xy = (0.0, -52.0)
    near = min(gmesh.vertices,
               key=lambda v: (v.co.x - cam_xy[0]) ** 2 + (v.co.y - cam_xy[1]) ** 2)
    eye = near.co.z + 2.6
    ground.to_mesh_clear()

    cam_data = bpy.data.cameras.new("Cam")
    cam_data.lens = 30.0
    cam = bpy.data.objects.new("Cam", cam_data)
    scene.collection.objects.link(cam)
    cam.location = (cam_xy[0], cam_xy[1], eye)
    cam.rotation_euler = (math.radians(83.0), 0.0, 0.0)
    scene.camera = cam
    print(f"    camera at eye level {eye:.2f} m ({near.co.z:.2f} m of ground under it)")

    engines = [e.identifier for e in
               type(scene.render).bl_rna.properties["engine"].enum_items]
    engine = args.engine or next(e for e in ("BLENDER_EEVEE_NEXT", "BLENDER_EEVEE", "CYCLES")
                                 if e in engines)
    scene.render.engine = engine
    scene.render.resolution_x = args.width
    scene.render.resolution_y = int(args.width * 0.625)
    scene.render.resolution_percentage = 100
    scene.render.image_settings.file_format = "PNG"
    scene.render.filepath = OUT
    if hasattr(scene, "eevee"):
        scene.eevee.taa_render_samples = args.samples
    bpy.ops.render.render(write_still=True)
    print(f"    wrote {OUT}.png")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []))
