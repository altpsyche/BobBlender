"""Geometry-node recipes and the build_geonodes entry point.

Layers: scaffold (group plumbing), blocks (composable sub-graphs), recipes
(named compositions), place (object or library placement). build_geonodes looks
up a recipe, builds the node group, and places it.
"""

import bpy

from . import recipes
from .place import place
from .scaffold import new_group


def _clear_existing(name: str):
    """Drop a prior object and orphaned node group of this name.

    Makes a build idempotent: re-running the same named recipe replaces its own
    output instead of piling up name.001 duplicates, and keeps the clean name so
    references by name (a scatter's emitter) still resolve to the fresh build.
    Removing the object first frees the group, which is then removed only if no
    other object still uses it.
    """
    obj = bpy.data.objects.get(name)
    if obj is not None:
        bpy.data.objects.remove(obj, do_unlink=True)
    group = bpy.data.node_groups.get(name)
    if group is not None and group.users == 0:
        bpy.data.node_groups.remove(group)


def build_geonodes(op: dict) -> dict:
    recipe_name = op.get("recipe", "wave_grid")
    build = recipes.get(recipe_name)
    if build is None:
        raise ValueError(
            f"unknown geonodes recipe: {recipe_name!r} (have: {recipes.names()})"
        )

    params = op.get("params", {})
    name = op.get("name") or recipe_name
    if op.get("target", "new_object") == "new_object":
        _clear_existing(name)
    ng, out = new_group(name)
    build(ng, out, params)
    created = place(
        ng, name, target=op.get("target", "new_object"), mark_asset=op.get("mark_asset", False)
    )
    return {"op": "build_geonodes", "created": created, "info": recipe_name}
