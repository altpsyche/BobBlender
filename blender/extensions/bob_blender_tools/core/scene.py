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


# Fields that are STRUCTURAL: writing them changes nothing on its own, because what they mean is a
# rebuild rather than a driven value. Named here so the op result can SAY which op finishes the job,
# instead of reporting success on a change that produced no pixels (a scene run's finding).
_STRUCTURAL_ENV = {"season": "apply_season"}


def _apply_consumers():
    """Run the subscribed world appliers and return how many ran.

    With none subscribed (a headless build, or a session where the addon's UI is not registered) it
    falls back to the ONE applier that is pure core: the shared S_EnvState driver feed. That is the
    piece a material needs to read bbt_env at all, so a headless set_env still reaches the frame.
    """
    from . import shading

    n = _env.apply_world(bpy.context.scene)
    if n == 0:
        shading.apply_world_feed(bpy.context.scene)
    return n


def set_env(op: dict) -> dict:
    """Write fields onto Scene.bbt_env (the shared world state), then re-apply every consumer, which
    is the coherent way to set season/weather/time/wind/snow so downstream effects update together.

    params is a dict of bbt_env field names (see core/env.py BBT_EnvProps). Unknown or
    unsettable fields are reported, not fatal.

    `apply` (default True) runs the world appliers, the same subscribers a World-panel control runs:
    they install or remove the shared S_EnvState drivers, re-place the sun, re-drive the atmosphere
    wind and re-apply the quality level. Writing the fields alone reaches no material until something
    else happens to install the drivers, which made the world undialable over MCP. Pass apply=False
    to write the state and defer.

    Returns the applied field list, the fields that need a structural op to take effect, and how many
    appliers ran (0 means none are subscribed -- headless, or the addon's UI is not registered).
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
    hooks = _apply_consumers() if op.get("apply", True) else 0
    structural = {f: _STRUCTURAL_ENV[f] for f in applied if f in _STRUCTURAL_ENV}
    info = f"applied {', '.join(applied) or '(none)'}"
    if skipped:
        info += f"; skipped {', '.join(skipped)}"
    info += f"; re-applied {hooks} consumer(s)"
    for field, opname in sorted(structural.items()):
        info += f"; {field} is structural -- send the {opname} op for it to show"
    return {"op": "set_env", "created": [], "info": info,
            "data": {"applied": applied, "skipped": skipped, "appliers": hooks,
                     "structural": structural}}


def apply_world(op: dict) -> dict:
    """Re-apply every world consumer to the current Scene.bbt_env, changing no values.

    The op form of what the World panel does whenever one of its controls moves: install or remove
    the shared S_EnvState drivers per the Live Environment master, re-place the sun, re-drive the
    atmosphere wind, re-apply the quality level. Needed on its own (not just folded into `set_env`)
    for the case where the world is already right and the SCENE is new: a material built or converted
    after the last world change carries no drivers until something re-installs them.
    """
    hooks = _apply_consumers()
    if hooks == 0:
        return {"op": "apply_world", "created": [], "data": {"appliers": 0},
                "info": "no world appliers subscribed (headless, or the addon UI is not registered); "
                        "installed the shared S_EnvState driver feed so materials read bbt_env"}
    return {"op": "apply_world", "created": [], "data": {"appliers": hooks},
            "info": f"re-applied {hooks} world consumer(s)"}
