"""Mesh builders. Each takes a validated op dict, returns a small result dict."""

import bpy

_PRIMITIVES = {
    "cube": bpy.ops.mesh.primitive_cube_add,
    "uv_sphere": bpy.ops.mesh.primitive_uv_sphere_add,
    "ico_sphere": bpy.ops.mesh.primitive_ico_sphere_add,
    "cylinder": bpy.ops.mesh.primitive_cylinder_add,
    "cone": bpy.ops.mesh.primitive_cone_add,
    "plane": bpy.ops.mesh.primitive_plane_add,
    "torus": bpy.ops.mesh.primitive_torus_add,
    "grid": bpy.ops.mesh.primitive_grid_add,
}


def add_mesh(op: dict) -> dict:
    kind = op.get("kind", "cube")
    add = _PRIMITIVES.get(kind)
    if add is None:
        raise ValueError(f"unknown mesh kind: {kind!r}")

    location = tuple(op.get("location", (0.0, 0.0, 0.0)))
    size = float(op.get("size", 2.0))

    # size/radius args differ per primitive.
    kwargs = {"location": location}
    if kind in ("cube", "plane", "grid"):
        kwargs["size"] = size
    elif kind in ("uv_sphere", "ico_sphere", "cylinder"):
        kwargs["radius"] = size / 2.0
    elif kind == "cone":
        kwargs["radius1"] = size / 2.0
    # torus uses its own major/minor radius defaults

    add(**kwargs)

    obj = bpy.context.active_object
    if op.get("name"):
        obj.name = op["name"]

    return {"op": "add_mesh", "created": [obj.name], "info": kind}
