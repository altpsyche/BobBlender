"""forest-barn: build the barn's block-out and export the control mesh `comfy_mesh(control=...)` takes.

The Blender half of the barn, on its own, so the ComfyUI half can run from the agent surface and the
live session that holds gate A is never touched. Two steps and no generation:

  1. `proxies.make_blockout(shape="shed")` at the barn's real dimensions.
  2. `gen_assets.export_control` -- the unit-cube round trip Omni's encoder needs -- plus a clay
     three-quarter render of the block-out, which is the picture to compare the generation against.

It also prints the block-out's HIDDEN SURFACE, because that is the property that cost the last
generation its walls and the one a render cannot show: an area-weighted point sampler reads interior
faces as conditioning points. `blockout-control` part A is what gates it; this reports it beside the
control so the number travels with the file.

    ~/.steam/steam/steamapps/common/Blender/blender --background --factory-startup \\
        --python tools/scripts/forest_barn_blockout.py -- [--width 8.0] [--depth 7.0] ...

Writes `_generated/forest_barn/barn_control.glb` and `barn_blockout_clay.png`, and prints the
`comfy_mesh` arguments to send next.
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
    gen_views,
    proxies,
)

OUT = os.path.join(REPO, "_generated", "forest_barn")

# The barn as the manifest records it: 8.7 x 7.7 x 7.5 m overall, which is a 8.0 x 7.0 wall box under
# a 0.35 m eave. `ridge_h` is from the ground, so 7.5 is the height the asset is scaled to.
SHED = {"width": 8.0, "depth": 7.0, "wall_h": 4.2, "ridge_h": 7.5, "eave": 0.35,
        "door_w": 2.6, "door_h": 3.0, "jamb": 0.12}


def empty_scene():
    bpy.ops.wm.read_factory_settings(use_empty=True)


def main():
    parser = argparse.ArgumentParser()
    for key, value in SHED.items():
        parser.add_argument(f"--{key}", type=float, default=value)
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    args = parser.parse_args(argv)
    params = {key: getattr(args, key) for key in SHED}

    os.makedirs(OUT, exist_ok=True)
    empty_scene()
    made = proxies.make_blockout({"shape": "shed", "name": "BOB_Blockout_Barn", "replace": True,
                                  "params": params})
    obj = bpy.data.objects[made["data"]["object"]]
    print(f"[block-out] {made['info']}")

    control = os.path.join(OUT, "barn_control.glb")
    exported = gen_assets.export_control(obj, control)
    print(f"[control]   {control}")
    print(f"[control]   scale {exported['scale']:.4f} m, height {exported['height_m']:.4f} m, "
          f"{exported['points']} points, footprint {exported['footprint']}, "
          f"bbox for the voxel route {exported['bbox']}")

    views = gen_views.turntable_views(obj, os.path.join(OUT, "blockout_views"), count=4,
                                      elevation=18.0, extra_elevations=(), resolution=1024,
                                      samples=32, engine="BLENDER_EEVEE", stem="clay")
    print(f"[clay]      {len(views)} views, three-quarter at {views[0]['beauty']}")

    with open(os.path.join(OUT, "barn_control.json"), "w") as fh:
        json.dump({"params": params, "control": control, "exported": exported,
                   "dimensions": list(made["data"]["dimensions"]),
                   "faces": made["data"]["faces"],
                   "clay_views": [v["beauty"] for v in views]},
                  fh, indent=2, sort_keys=True, default=str)

    print()
    print("Next, from the agent surface (this is also the $BOB_COMFY_DIR check, because the control "
          "route is the one that uploads a mesh):")
    print(f'  comfy_mesh(prompt=..., control="{control}", control_mode="point", '
          f'height_m={params["ridge_h"]}, kind="rocks", hero=True, subject=<screened reference>)')


if __name__ == "__main__":
    main()
