"""Small bpy-side helpers to keep builders robust to whatever state the user's
Blender is in when an op lands."""

import bpy


def ensure_object_mode() -> None:
    """Leave Edit/Sculpt/etc. mode so object-level ops behave predictably.

    A no-op headless (already OBJECT) and safe if there's no valid context.
    """
    if bpy.context.mode != "OBJECT":
        try:
            bpy.ops.object.mode_set(mode="OBJECT")
        except RuntimeError:
            pass
