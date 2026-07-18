"""Block-out proxy assets: simple stand-ins so a scatter works before you bring
your own assets.

Each kind lives in a collection named BOB_Assets_<Kind> that is not linked to the
scene, so the proxies show up only as scattered instances. Replace them by
editing that collection's contents or pointing a scatter's Assets input at your
own collection.
"""

import bmesh
import bpy
from mathutils import Matrix


def _cone(name, radius, depth, segments=8):
    mesh = bpy.data.meshes.new(name)
    bm = bmesh.new()
    bmesh.ops.create_cone(
        bm,
        cap_ends=True,
        segments=segments,
        radius1=radius,
        radius2=0.0,
        depth=depth,
        matrix=Matrix.Translation((0.0, 0.0, depth / 2.0)),
    )
    bm.to_mesh(mesh)
    bm.free()
    return mesh


def _ico(name, radius):
    mesh = bpy.data.meshes.new(name)
    bm = bmesh.new()
    bmesh.ops.create_icosphere(
        bm,
        subdivisions=1,
        radius=radius,
        matrix=Matrix.Translation((0.0, 0.0, radius)),
    )
    bm.to_mesh(mesh)
    bm.free()
    return mesh


_KINDS = {
    "trees": [
        ("Tree_A", lambda: _cone("Tree_A", 0.6, 2.6)),
        ("Tree_B", lambda: _cone("Tree_B", 0.85, 3.4)),
    ],
    "rocks": [
        ("Rock_A", lambda: _ico("Rock_A", 0.4)),
        ("Rock_B", lambda: _ico("Rock_B", 0.6)),
    ],
    "plants": [
        ("Plant_A", lambda: _cone("Plant_A", 0.25, 0.6)),
        ("Plant_B", lambda: _cone("Plant_B", 0.3, 0.5)),
    ],
}


def _collection_name(kind):
    return f"BOB_Assets_{kind.capitalize()}"


def ensure_collection(kind):
    """Return the BOB_Assets_<Kind> collection, creating proxies if it is empty."""
    name = _collection_name(kind)
    collection = bpy.data.collections.get(name)
    if collection is None:
        collection = bpy.data.collections.new(name)
    if not collection.objects:
        for obj_name, make_mesh in _KINDS.get(kind, []):
            obj = bpy.data.objects.new(obj_name, make_mesh())
            collection.objects.link(obj)
    return collection


def make_proxies(op: dict) -> dict:
    kinds = op.get("kinds") or ["trees", "rocks", "plants"]
    created = [ensure_collection(kind).name for kind in kinds]
    return {"op": "make_proxies", "created": created, "info": ",".join(kinds)}
