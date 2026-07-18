"""Image ops. Small, so a fresh bake shows without a manual reload.

reload_image reloads image datablocks from disk. After the venv re-bakes a
heightmap PNG, this refreshes the datablock the terrain samples, and tagging
objects makes the geometry-node modifiers re-evaluate.
"""

import os

import bpy


def reload_image(op: dict) -> dict:
    path = op.get("path")
    target = os.path.abspath(path) if path else None

    reloaded = []
    for img in bpy.data.images:
        if img.source != "FILE":
            continue
        if target is None or os.path.abspath(bpy.path.abspath(img.filepath)) == target:
            img.reload()
            reloaded.append(img.name)

    # Nudge dependent geometry so an Image Texture node re-samples the new pixels.
    for obj in bpy.data.objects:
        obj.update_tag()

    return {"op": "reload_image", "created": reloaded, "info": path or "all"}
