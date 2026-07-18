"""Geometry-node recipes and the build_geonodes entry point.

Layers: scaffold (group plumbing), blocks (composable sub-graphs), recipes
(named compositions), place (object or library placement). build_geonodes looks
up a recipe, builds the node group, and places it.
"""

from . import recipes
from .place import place
from .scaffold import new_group


def build_geonodes(op: dict) -> dict:
    recipe_name = op.get("recipe", "wave_grid")
    build = recipes.get(recipe_name)
    if build is None:
        raise ValueError(
            f"unknown geonodes recipe: {recipe_name!r} (have: {recipes.names()})"
        )

    params = op.get("params", {})
    name = op.get("name") or recipe_name
    ng, out = new_group(name)
    build(ng, out, params)
    created = place(
        ng, name, target=op.get("target", "new_object"), mark_asset=op.get("mark_asset", False)
    )
    return {"op": "build_geonodes", "created": created, "info": recipe_name}
