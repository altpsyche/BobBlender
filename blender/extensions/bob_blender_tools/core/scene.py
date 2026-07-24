"""Scene control ops: delete, clear_scene, set_env.

Housekeeping an agent needs to drive a scene cleanly over MCP: remove a stray
object, reset to an empty scene before a fresh build, and write the shared world
state (Scene.bbt_env) that downstream consumers (sky look, snow line, wetness) read.
bpy-only.
"""

import bpy

from . import env as _env


_DATA_COLLECTIONS = {
    "MESH": "meshes", "CURVE": "curves", "CAMERA": "cameras",
    "LIGHT": "lights", "VOLUME": "volumes",
}


def _remove_object(obj):
    """Remove an object and its now-orphan data (mesh/curve/camera/light/volume)."""
    obj_type = obj.type
    data = obj.data
    bpy.data.objects.remove(obj, do_unlink=True)
    coll_name = _DATA_COLLECTIONS.get(obj_type)
    if data is not None and coll_name and getattr(data, "users", 1) == 0:
        coll = getattr(bpy.data, coll_name)
        try:
            coll.remove(data)
        except (RuntimeError, TypeError):
            pass


def delete(op: dict) -> dict:
    """Remove named objects. Missing names are reported, not an error."""
    names = op.get("names") or ([op["name"]] if op.get("name") else [])
    removed, missing = [], []
    for name in names:
        obj = bpy.data.objects.get(name)
        if obj is None:
            missing.append(name)
            continue
        _remove_object(obj)
        removed.append(name)
    info = f"removed {len(removed)}"
    if missing:
        info += f", missing: {', '.join(missing)}"
    return {"op": "delete", "created": [], "info": info}


def clear_scene(op: dict) -> dict:
    """Remove all objects except any named in `keep`, resetting to a near-empty scene.

    `keep` is a list of object names to preserve. `purge` (default True) also drops the
    orphan datablocks so a re-build starts clean instead of colliding with name.001s.
    """
    keep = set(op.get("keep") or [])
    removed = []
    for obj in list(bpy.data.objects):
        if obj.name in keep:
            continue
        removed.append(obj.name)
        _remove_object(obj)
    if op.get("purge", True):
        bpy.ops.outliner.orphans_purge(do_local_ids=True, do_linked_ids=True,
                                       do_recursive=True)
    return {"op": "clear_scene", "created": [], "info": f"removed {len(removed)} objects"}


def set_env(op: dict) -> dict:
    """Write fields onto Scene.bbt_env (the shared world state), the coherent way to set
    season/weather/time/wind/snow so downstream consumers update together.

    params is a dict of bbt_env field names (see core/env.py BBT_EnvProps). Unknown or
    unsettable fields are reported, not fatal. Returns the applied field list.
    """
    params = op.get("params", {})
    env = _env.get_env()
    if env is None:
        raise ValueError("set_env: Scene.bbt_env is absent (BobFirmament not registered)")
    applied, skipped = [], []
    for field, value in params.items():
        if not hasattr(env, field):
            skipped.append(field)
            continue
        try:
            setattr(env, field, value)
            applied.append(field)
        except (TypeError, ValueError):
            skipped.append(field)
    info = f"applied {', '.join(applied) or '(none)'}"
    if skipped:
        info += f"; skipped {', '.join(skipped)}"
    return {"op": "set_env", "created": [], "info": info}
