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


def _material(name, color, roughness=0.85):
    mat = bpy.data.materials.get(name)
    if mat is None:
        mat = bpy.data.materials.new(name)
        mat.use_nodes = True
        bsdf = mat.node_tree.nodes.get("Principled BSDF")
        if bsdf:
            bsdf.inputs["Base Color"].default_value = (*color, 1.0)
            bsdf.inputs["Roughness"].default_value = roughness
    mat.diffuse_color = (*color, 1.0)  # workbench / viewport fallback
    return mat


def _new_object(name, mesh, materials):
    for mat in materials:
        mesh.materials.append(mat)
    return bpy.data.objects.new(name, mesh)


def _tree(name, trunk_h, canopy_r, canopy_h):
    """Cone canopy on a short trunk, trunk and canopy on separate materials."""
    mesh = bpy.data.meshes.new(name)
    bm = bmesh.new()
    bmesh.ops.create_cone(
        bm, cap_ends=True, segments=6, radius1=0.11, radius2=0.09,
        depth=trunk_h, matrix=Matrix.Translation((0, 0, trunk_h / 2)),
    )
    bmesh.ops.create_cone(
        bm, cap_ends=True, segments=10, radius1=canopy_r, radius2=0.0,
        depth=canopy_h, matrix=Matrix.Translation((0, 0, trunk_h + canopy_h / 2)),
    )
    for face in bm.faces:
        face.material_index = 0 if face.calc_center_median().z < trunk_h else 1
    bm.to_mesh(mesh)
    bm.free()
    trunk = _material("BOB_Bark", (0.13, 0.09, 0.05))
    leaves = _material("BOB_Foliage", (0.11, 0.22, 0.09))
    return _new_object(name, mesh, [trunk, leaves])


def _blob(name, radius, squash, color, mat_name):
    mesh = bpy.data.meshes.new(name)
    bm = bmesh.new()
    bmesh.ops.create_icosphere(
        bm, subdivisions=1, radius=radius,
        matrix=Matrix.Diagonal((1, 1, squash, 1)) @ Matrix.Translation((0, 0, radius)),
    )
    bm.to_mesh(mesh)
    bm.free()
    return _new_object(name, mesh, [_material(mat_name, color)])


_KINDS = {
    "trees": [
        ("Tree_A", lambda: _tree("Tree_A", 0.8, 0.75, 2.6)),
        ("Tree_B", lambda: _tree("Tree_B", 1.1, 1.0, 3.6)),
    ],
    "rocks": [
        ("Rock_A", lambda: _blob("Rock_A", 0.4, 0.7, (0.28, 0.27, 0.25), "BOB_Rock")),
        ("Rock_B", lambda: _blob("Rock_B", 0.65, 0.6, (0.24, 0.23, 0.22), "BOB_Rock")),
    ],
    "plants": [
        ("Plant_A", lambda: _blob("Plant_A", 0.3, 0.5, (0.16, 0.3, 0.12), "BOB_Shrub")),
        ("Plant_B", lambda: _blob("Plant_B", 0.22, 0.55, (0.2, 0.34, 0.14), "BOB_Shrub")),
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
        for obj_name, make_obj in _KINDS.get(kind, []):
            collection.objects.link(make_obj())
    return collection


def make_proxies(op: dict) -> dict:
    kinds = op.get("kinds") or ["trees", "rocks", "plants"]
    created = [ensure_collection(kind).name for kind in kinds]
    return {"op": "make_proxies", "created": created, "info": ",".join(kinds)}
