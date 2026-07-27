"""A review gallery: every shipped foliage species, several shots each.

    ~/.steam/steam/steamapps/common/Blender/blender --background --factory-startup \
        --python tools/scripts/render_foliage_gallery.py -- [--samples 32] [--width 900]

Writes `_generated/foliage_gallery/<species>_<shot>.png`. `render_foliage_stand.py` answers "does a
stand read"; this answers "does one tree read", which is a different question and needs a different
camera: a stand shot hides a trunk behind four hundred others, and the things that make a single
tree look wrong (a cross-section, a taper, a branch collar) are all inside two metres of it.

Five shots per species, each chosen for one failure it is the only view of:

    full    the whole silhouette, level camera -- crown width against height
    low3q   eye level, three-quarter, looking up -- what a walking camera sees
    trunk   1/3 height, close -- the bole's cross-section, taper and bark
    crown   2/3 height, close -- card density, droop and the branch/card junction
    skel    Skeleton Only, whole tree -- the structure with no sweep hiding it

Every distance is a multiple of the species' own measured height, so the same five framings work on
a 22 m conifer and a 0.4 m grass tuft without a table of numbers per species.
"""

import argparse
import math
import os
import sys

import bpy
from mathutils import Vector

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(REPO, "blender", "extensions"))

from bob_blender_tools.core import assets, foliage_build      # noqa: E402
from bob_blender_tools.core import env as bbt_env             # noqa: E402

OUT_DIR = os.path.join(REPO, "_generated", "foliage_gallery")

SPECIES = ("conifer", "broadleaf", "shrub", "grass_tuft")

# (name, azimuth deg, target height as a fraction of the tree, distance in tree heights, lens mm)
SHOTS = (
    ("full",   -90.0, 0.50, 1.70, 50.0),
    ("low3q",  -40.0, 0.12, 0.80, 35.0),
    ("trunk",  -70.0, 0.30, 0.14, 50.0),
    ("crown",  -60.0, 0.68, 0.30, 50.0),
)


def measure(obj):
    """The evaluated bounding box, in world space: (min, max) as Vectors."""
    depsgraph = bpy.context.evaluated_depsgraph_get()
    ev = obj.evaluated_get(depsgraph)
    mesh = ev.to_mesh()
    if not mesh.vertices:
        ev.to_mesh_clear()
        raise SystemExit(f"{obj.name} evaluated to an empty mesh")
    lo = Vector((1e9, 1e9, 1e9))
    hi = Vector((-1e9, -1e9, -1e9))
    for v in mesh.vertices:
        co = obj.matrix_world @ v.co
        for i in range(3):
            lo[i] = min(lo[i], co[i])
            hi[i] = max(hi[i], co[i])
    ev.to_mesh_clear()
    return lo, hi


def light_the_set(scene):
    # --factory-startup opens the startup file, which has a 2 m Cube sitting exactly where the
    # trunk grows. It renders as a white plinth around the roots, which is not a foliage defect and
    # reads as one.
    for obj in list(bpy.data.objects):
        bpy.data.objects.remove(obj, do_unlink=True)
    sun = bpy.data.objects.new("Sun", bpy.data.lights.new("Sun", type="SUN"))
    scene.collection.objects.link(sun)
    sun.data.energy = 5.0
    sun.data.angle = 0.09
    sun.rotation_euler = (0.72, 0.0, 1.15)
    world = scene.world or bpy.data.worlds.new("World")
    scene.world = world
    world.use_nodes = True
    bg = world.node_tree.nodes.get("Background")
    if bg is not None:
        bg.inputs["Color"].default_value = (0.44, 0.55, 0.70, 1.0)
        bg.inputs["Strength"].default_value = 1.2
    # A ground plane, because a floating tree gives no sense of scale and no contact shadow -- and
    # the root/ground junction is one of the things a single-tree review is for.
    bpy.ops.mesh.primitive_plane_add(size=400.0, location=(0.0, 0.0, 0.0))
    ground = bpy.context.active_object
    ground.name = "Ground"
    mat = bpy.data.materials.new("GalleryGround")
    mat.use_nodes = True
    bsdf = mat.node_tree.nodes.get("Principled BSDF")
    if bsdf is not None:
        bsdf.inputs["Base Color"].default_value = (0.16, 0.15, 0.11, 1.0)
        bsdf.inputs["Roughness"].default_value = 0.9
    ground.data.materials.append(mat)


def frame(cam, target, azimuth_deg, distance):
    a = math.radians(azimuth_deg)
    cam.location = (target.x + distance * math.cos(a),
                    target.y + distance * math.sin(a),
                    target.z)
    direction = target - Vector(cam.location)
    cam.rotation_euler = direction.to_track_quat("-Z", "Y").to_euler()


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--samples", type=int, default=32)
    ap.add_argument("--width", type=int, default=900)
    ap.add_argument("--species", default="")
    ap.add_argument("--engine", default="")
    args = ap.parse_args(argv)

    os.makedirs(OUT_DIR, exist_ok=True)
    # The repo's generated pack, put on the search path explicitly. Without this a species that names
    # a GENERATED set gets an empty map dict and renders as its solid block-out tint -- and a leaf
    # card's tint is white, so the whole canopy comes back as opaque white quads that read as a
    # translucency bug rather than as a missing pack root. This addon is imported here, never
    # registered, so nothing else puts the root there.
    pack = os.path.join(REPO, "packs", "generated")
    if os.path.isdir(pack):
        assets.add_pack_root(pack)

    scene = bpy.context.scene
    if getattr(scene, "bbt_env", None) is None:
        bbt_env.register()
        scene = bpy.context.scene
    scene.bbt_env.season = "summer"
    scene.bbt_env.wind_strength = 0.0     # a still tree: this is a shape review, not a motion one
    scene.bbt_env.temperature = 17.0
    scene.bbt_env.snow_line = 1.2

    light_the_set(scene)

    cam_data = bpy.data.cameras.new("Cam")
    cam = bpy.data.objects.new("Cam", cam_data)
    scene.collection.objects.link(cam)
    scene.camera = cam

    engines = [e.identifier for e in
               type(scene.render).bl_rna.properties["engine"].enum_items]
    engine = args.engine or next(e for e in ("BLENDER_EEVEE_NEXT", "BLENDER_EEVEE", "CYCLES")
                                 if e in engines)
    scene.render.engine = engine
    scene.render.resolution_x = args.width
    scene.render.resolution_y = int(args.width * 1.2)
    scene.render.resolution_percentage = 100
    scene.render.image_settings.file_format = "PNG"
    if hasattr(scene, "eevee"):
        scene.eevee.taa_render_samples = args.samples

    wanted = [s for s in (args.species.split(",") if args.species else SPECIES) if s]
    for name in wanted:
        preset = assets.foliage_species(name)
        if preset is None:
            print(f"    no species {name}")
            continue
        params = dict(preset["params"], seed=7)
        tree = foliage_build.grow(f"Gallery_{name}", params, species=name, scene=scene)
        bpy.context.view_layer.update()
        lo, hi = measure(tree)
        height = hi.z - lo.z
        width = max(hi.x - lo.x, hi.y - lo.y)
        print(f"  {name}: {height:.2f} m tall, {width:.2f} m wide, w/h {width / height:.2f}")

        for shot, azimuth, at, dist, lens in SHOTS:
            cam_data.lens = lens
            target = Vector((0.0, 0.0, lo.z + at * height))
            frame(cam, target, azimuth, max(dist * height, 0.05))
            scene.render.filepath = os.path.join(OUT_DIR, f"{name}_{shot}")
            bpy.ops.render.render(write_still=True)
            print(f"    {name}_{shot}.png")

        # The wood alone, at `Cards` 0. NOT Skeleton Only, which was the obvious choice and renders a
        # blank frame: it emits curves, a curve has no bevel, so nothing rasterizes at all. Skeleton
        # Only is a viewport view. `Cards` 0 is the swept wood, which is what a structure shot wants
        # anyway -- the taper, the flare, the collars and the sag are all in the sweep.
        # Written as a LIVE KNOB, not as a rebuild override: a rebuild restores every live value by
        # socket name (that is what keeps a tuned tree tuned across one), so a `cards` param would be
        # put straight back by the restore and the shot would come back identical.
        foliage_build.live_input(tree, "Cards").value = 0
        bpy.context.view_layer.update()
        cam_data.lens = 50.0
        frame(cam, Vector((0.0, 0.0, lo.z + 0.5 * height)), -90.0, 1.7 * height)
        scene.render.filepath = os.path.join(OUT_DIR, f"{name}_wood")
        bpy.ops.render.render(write_still=True)
        print(f"    {name}_wood.png")

        bpy.data.objects.remove(tree, do_unlink=True)

    print(f"    wrote {OUT_DIR}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []))
