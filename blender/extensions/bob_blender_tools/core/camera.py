"""add_camera: create and aim a camera, and set it as the scene camera.

A shot needs a camera an agent can place from outside Blender. This creates (or
reuses by name) a camera object, points it at a look-at target, sets its lens, and
makes it the active scene camera so a render frames it. bpy-only.

Aim: a camera looks down its local -Z with +Y up. Given a location and a look-at
point, the direction (target - location) is turned into a rotation with Blender's
to_track_quat('-Z', 'Y'), the same math the "Track To" constraint uses, so the
result matches what an artist gets by eye. When no target is given the explicit
rotation_euler is used instead (degrees), so a caller can hand-aim.
"""

import math

import bpy
from mathutils import Vector

CAMERA_NAME = "BOB_Camera"


def _get(params, key, default):
    v = params.get(key, default)
    return default if v is None else v


def _ensure_camera(name):
    obj = bpy.data.objects.get(name)
    if obj is not None and obj.type == "CAMERA":
        return obj
    cam = bpy.data.cameras.new(name)
    obj = bpy.data.objects.new(name, cam)
    bpy.context.scene.collection.objects.link(obj)
    return obj


def add_camera(op: dict) -> dict:
    params = op.get("params", op)  # accept flat op or {"params": {...}}
    name = _get(params, "name", CAMERA_NAME)

    obj = _ensure_camera(name)
    location = tuple(_get(params, "location", (12.0, -12.0, 8.0)))
    obj.location = location

    target = params.get("look_at")
    if target is not None:
        direction = Vector(tuple(target)) - Vector(location)
        if direction.length > 1e-6:
            obj.rotation_euler = direction.to_track_quat("-Z", "Y").to_euler()
    else:
        rot = _get(params, "rotation", (0.0, 0.0, 0.0))
        obj.rotation_euler = tuple(math.radians(a) for a in rot)

    cam = obj.data
    cam.lens = float(_get(params, "lens", 50.0))
    clip_end = params.get("clip_end")
    if clip_end is not None:
        cam.clip_end = float(clip_end)

    if _get(params, "set_active", True):
        bpy.context.scene.camera = obj

    obj.update_tag()
    info = f"lens={cam.lens:.0f}mm loc={location}"
    if target is not None:
        info += f" look_at={tuple(target)}"
    return {"op": "add_camera", "created": [obj.name], "info": info}
