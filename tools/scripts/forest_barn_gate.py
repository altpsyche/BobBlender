"""forest-barn: build the gate A review .blend for the barn, and shoot its contact sheet.

The artefact the last two rounds did not produce. Every measurement was made headless, on purpose --
a live session held an approval gate and predated the code changes -- and the cost of that shows up
here: there was nothing an artist could OPEN to review. Packs and PNGs are not a scene.

So this writes one, from the pack rather than from a session, which means it replays: every asset comes
out of `packs/generated`, laid out in a row on a flat plane, evenly spaced, front-lit, at true metre
scale, with the BLOCK-OUT standing beside them as the reference the rejection is measured against.

Three barns, because the argument is a comparison and not a single frame:

  BarnShed         `mesh_texture` off the low three-quarter reference. Door panels on the roof.
  BarnShedAerial   `mesh_texture` off the aerial reference. Roof clean, gable end gone to stucco.
  BarnPainted      the projection route, `mesh_paint_views` plus `gen_paint`, painted from clay.

The painted one is rebuilt here rather than read from the pack, because `forest_barn_paint.py` applied
its material in a headless session that was never saved -- the same gap this script exists to close.
Its maps are on disk under `_generated/forest_barn/painted/`.

    ~/.steam/steam/steamapps/common/Blender/blender --background --factory-startup \\
        --python tools/scripts/forest_barn_gate.py --

Writes `_generated/forest_barn/gateA_barn.blend` and `renders/forest-barn/<date>/gateA4_*.png`.
Open the .blend and the cameras are already set: `Cam_Row` for the sheet, `Cam_Hero_*` per barn.
"""

import argparse
import json
import os
import sys

import bpy

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(REPO, "blender", "extensions"))

from bob_blender_tools.core import (  # noqa: E402
    gen_assets,
    materials,
    proxies,
)

OUT = os.path.join(REPO, "_generated", "forest_barn")
PACK = os.path.join(REPO, "packs", "generated")
PAINTED_MAPS = os.path.join(OUT, "painted")

SHED = {"width": 8.0, "depth": 7.0, "wall_h": 4.2, "ridge_h": 7.5, "eave": 0.35,
        "door_w": 2.6, "door_h": 3.0, "jamb": 0.12}

# Row order and spacing. 12 m apart on x: the barns are 8.8 m wide, so this leaves a clear gap and
# still fits one camera across the row.
SPACING = 12.0


def ground(size=80.0):
    bpy.ops.mesh.primitive_plane_add(size=size, location=(0, 0, 0))
    plane = bpy.context.active_object
    plane.name = "Ground"
    mat = bpy.data.materials.new("M_Ground")
    mat.use_nodes = True
    bsdf = mat.node_tree.nodes.get("Principled BSDF")
    if bsdf:
        bsdf.inputs["Base Color"].default_value = (0.22, 0.22, 0.22, 1.0)
        bsdf.inputs["Roughness"].default_value = 0.9
    plane.data.materials.append(mat)
    return plane


def front_light():
    """Flat front light plus a sky fill, which is the gate's lighting and not the scene's: a mood
    light hides exactly the defects a gate is for."""
    key = bpy.data.objects.new("Key", bpy.data.lights.new("Key", "SUN"))
    key.data.energy = 3.5
    key.rotation_euler = (1.05, 0.0, 0.35)
    bpy.context.scene.collection.objects.link(key)
    fill = bpy.data.objects.new("Fill", bpy.data.lights.new("Fill", "SUN"))
    fill.data.energy = 1.2
    fill.rotation_euler = (1.2, 0.0, -2.2)
    bpy.context.scene.collection.objects.link(fill)
    world = bpy.data.worlds.new("GateWorld")
    world.use_nodes = True
    world.node_tree.nodes["Background"].inputs[1].default_value = 0.45
    bpy.context.scene.world = world


def camera(name, location, target, lens=50.0):
    data = bpy.data.cameras.new(name)
    data.lens = lens
    cam = bpy.data.objects.new(name, data)
    cam.location = location
    bpy.context.scene.collection.objects.link(cam)
    track = cam.constraints.new("TRACK_TO")
    track.target = target
    track.track_axis = "TRACK_NEGATIVE_Z"
    track.up_axis = "UP_Y"
    return cam


def place(obj, x):
    obj.location = (x, 0.0, 0.0)
    return obj


def painted_variant(name="BarnPainted"):
    """`BarnShedAerial`'s geometry wearing the projection route's maps.

    Rebuilt rather than read from the pack because the paint run applied its material in a headless
    session and nothing wrote a .blend -- which is the gap this whole script is about. Goes through
    `gen_assets.apply_baked_material`, the same function `gen_paint.paint_object` calls, so what shows
    up here is what that route actually produces, including the fact that it does NOT come back a
    BobShader (`master_type` None). That is a defect to look at, not something to paper over here.
    """
    obj = gen_assets.import_generated("BarnShedAerial", kind="structure", pack_dir=PACK)
    obj.name = name
    obj.data = obj.data.copy()
    maps = {}
    for role in ("basecolor", "roughness", "normal", "ao", "height"):
        path = os.path.join(PAINTED_MAPS, f"barn_{role}.png")
        if os.path.isfile(path):
            maps[role] = path
    if not maps:
        return None, {}
    mat = gen_assets.apply_baked_material(obj, maps, "M_BarnPainted")
    return obj, {"maps": sorted(maps), "material": mat.name,
                 "master": materials.master_type(mat)}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", default="20260730")
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    args = parser.parse_args(argv)

    renders = os.path.join(REPO, "renders", "forest-barn", args.date)
    os.makedirs(renders, exist_ok=True)
    os.makedirs(OUT, exist_ok=True)

    bpy.ops.wm.read_factory_settings(use_empty=True)
    ground()
    front_light()

    report = {}
    row = []

    # The block-out first, because it is what every figure is scored against.
    made = proxies.make_blockout({"shape": "shed", "name": "BOB_Blockout_Barn", "replace": True,
                                  "params": SHED})
    block = bpy.data.objects[made["data"]["object"]]
    row.append(("Block-out (the control)", place(block, -SPACING * 1.5)))

    for label, asset in (("mesh_texture, low 3/4 ref", "BarnShed"),
                         ("mesh_texture, aerial ref", "BarnShedAerial")):
        obj = gen_assets.import_generated(asset, kind="structure", pack_dir=PACK)
        if obj.name not in bpy.context.scene.collection.objects:
            bpy.context.scene.collection.objects.link(obj)
        row.append((label, obj))

    painted, painted_report = painted_variant()
    report["painted"] = painted_report
    if painted is not None:
        if painted.name not in bpy.context.scene.collection.objects:
            bpy.context.scene.collection.objects.link(painted)
        row.append(("mesh_paint_views, from clay", painted))

    # Lay the row out centred on the origin, and record the metre facts a reviewer needs.
    start = -SPACING * (len(row) - 1) / 2.0
    report["row"] = []
    for i, (label, obj) in enumerate(row):
        place(obj, start + i * SPACING)
        dims = gen_assets.dimensions(obj)
        report["row"].append({
            "label": label, "object": obj.name, "x": round(float(obj.location[0]), 3),
            "size_m": [round(float(d), 4) for d in dims],
            "faces": gen_assets.face_count(obj),
            "material": obj.active_material.name if obj.active_material else None,
            "master": materials.master_type(obj.active_material) if obj.active_material else None,
        })
        print(f"[row] {label:32s} {obj.name:24s} x={obj.location[0]:+7.2f}  "
              f"{tuple(round(float(d), 2) for d in dims)} m  "
              f"master={report['row'][-1]['master']}")

    bpy.context.view_layer.update()
    focus = bpy.data.objects.new("RowFocus", None)
    focus.location = (0.0, 0.0, 3.5)
    bpy.context.scene.collection.objects.link(focus)
    scene = bpy.context.scene
    # Framed from the row's actual WIDTH rather than by eye: the first attempt put a 50 mm lens at
    # 0.95 of the span and cut the two outer barns off at the frame edge. The row is (n-1) * SPACING
    # of centres plus one barn either side, and a 35 mm lens on a 36 mm sensor sees 2*atan(18/35), so
    # the distance follows from the half-width rather than from a guess.
    import math

    width = SPACING * (len(row) - 1) + 12.0
    lens = 35.0
    distance = (width / 2.0) / math.tan(math.atan(18.0 / lens))
    cam_row = camera("Cam_Row", (0.0, -distance, 10.0), focus, lens=lens)
    print(f"[cam]  row {width:.1f} m wide, {lens:.0f} mm at {distance:.1f} m")
    for _label, obj in row:
        camera(f"Cam_Hero_{obj.name}", (obj.location[0] + 11.0, -11.0, 7.5), obj, lens=55.0)

    scene.render.engine = "BLENDER_EEVEE"
    scene.render.resolution_x, scene.render.resolution_y = 1920, 720
    scene.render.film_transparent = False
    scene.camera = cam_row
    sheet = os.path.join(renders, "gateA4_barn_row.png")
    scene.render.filepath = sheet
    bpy.ops.render.render(write_still=True)
    print(f"[render] {sheet}")

    scene.render.resolution_x, scene.render.resolution_y = 1080, 1080
    for _label, obj in row:
        scene.camera = bpy.data.objects[f"Cam_Hero_{obj.name}"]
        path = os.path.join(renders, f"gateA4_hero_{obj.name}.png")
        scene.render.filepath = path
        bpy.ops.render.render(write_still=True)
        print(f"[render] {path}")

    scene.camera = cam_row
    scene.render.filepath = ""
    blend = os.path.join(OUT, "gateA_barn.blend")
    bpy.ops.wm.save_as_mainfile(filepath=blend)
    print(f"[blend]  {blend}")

    report["blend"] = blend
    report["sheet"] = sheet
    with open(os.path.join(OUT, "gateA_barn.json"), "w") as fh:
        json.dump(report, fh, indent=2, sort_keys=True, default=str)


if __name__ == "__main__":
    main()
