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


def _blade(name, height, radius, color):
    """A thin tapered blade standing on the ground, for grass block-out."""
    mesh = bpy.data.meshes.new(name)
    bm = bmesh.new()
    bmesh.ops.create_cone(
        bm, cap_ends=True, segments=4, radius1=radius, radius2=0.0,
        depth=height, matrix=Matrix.Translation((0, 0, height / 2)),
    )
    bm.to_mesh(mesh)
    bm.free()
    return _new_object(name, mesh, [_material("BOB_Grass", color)])


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
    "grass": [
        ("Grass_A", lambda: _blade("Grass_A", 0.5, 0.04, (0.22, 0.36, 0.12))),
        ("Grass_B", lambda: _blade("Grass_B", 0.34, 0.035, (0.26, 0.4, 0.14))),
    ],
}


# -- Structure block-outs (the control-conditioned geometry route) ---------------------------------
# A different job from the scatter proxies above, and here for the same reason they are: a shape
# built out of primitives standing in for something better. The proxies stand in for an ASSET; a
# block-out stands in for a SILHOUETTE, and it is consumed by `gen_assets.export_control` and then
# by `comfy_mesh(control=...)` rather than by a scatter.
#
# It exists because of the second artist rejection, where a gabled timber structure came back
# bulging and edgeless for three generations across two runs. A building is close to the worst case
# for image-to-3D and the reasons are structural, not a bad roll: hard planar surfaces, right
# angles, repeated structure and thin proud detail, against geometry that comes back as a
# dual-contoured shell rounding every edge it touches. 8,617 triangles over an 8.7 m building is
# about 12 cm of surface per triangle, so a window frame standing 4 cm proud cannot survive the
# sampling whatever the model does.
#
# So the walls, the roof pitch and the footprint come from primitives, where a right angle is
# exactly a right angle, and the generator supplies only the surface. This stays inside the brief's
# division of labour -- the structure still comes from `comfy_mesh` -- which is why it is the route
# to try before the one that needs the brief amended. Measured, a point control holds a footprint
# IoU of 0.9106 against 0.5766 for the bbox control, so the silhouette it is given is the one it
# keeps.


def _shed(name, width=8.0, depth=7.0, wall_h=4.2, ridge_h=7.5, eave=0.35, door_w=2.6,
          door_h=3.0, jamb=0.12):
    """A gabled shed: a wall box, a real roof prism over it, and a doorway jamb standing proud.

    Not a finished building, and deliberately not: everything here is silhouette. The gable ENDS
    face -y and +y, so the ridge runs along y and the doorway sits in the -y gable, which is the
    elevation a three-quarter camera reads.

    `jamb` is the one piece of relief rather than silhouette, and it is here because the artist's own
    note on the failed generation was that the doorway "reads as a flat pale slab rather than a pair
    of doors". A doorway drawn only in the texture has nothing to catch a practical light; a jamb
    standing 12 cm proud is over one triangle's worth of surface at the budgets this route uses, so
    it is detail the control can actually carry.

    `width` and `depth` are the WALL box; the roof overhangs by `eave` on each of the four sides, so
    the overall footprint is width + 2*eave by depth + 2*eave, which is the figure the op reports back.
    `ridge_h` is measured from the ground, not from the wall top.

    Origin at the base and centred in plan, which is what `export_control` normalises from and what the
    manifest origin rule wants of anything that lands in a scene.
    """
    mesh = bpy.data.meshes.new(name)
    bm = bmesh.new()
    half_w, half_d = width / 2.0, depth / 2.0

    bmesh.ops.create_cube(bm, size=1.0, matrix=(Matrix.Translation((0, 0, wall_h / 2.0))
                                                @ Matrix.Diagonal((width, depth, wall_h, 1))))

    # The roof as its own solid: a prism with the ridge along y and an overhang on all four sides,
    # so the eave line is a real edge with a shadow under it rather than a painted stripe.
    roof_w, roof_d = half_w + eave, half_d + eave
    verts = [bm.verts.new(co) for co in (
        (-roof_w, -roof_d, wall_h), (roof_w, -roof_d, wall_h),
        (roof_w, roof_d, wall_h), (-roof_w, roof_d, wall_h),
        (0.0, -roof_d, ridge_h), (0.0, roof_d, ridge_h))]
    for face in ((0, 1, 4), (2, 3, 5), (0, 4, 5, 3), (1, 2, 5, 4), (0, 3, 2, 1)):
        bm.faces.new([verts[i] for i in face])

    # The doorway jamb, proud of the -y gable wall: a frame, not a slab, so the opening reads as an
    # opening. Three boxes rather than a boolean, because a block-out is a control signal and an
    # overlapping union samples exactly the same surface as a clean one.
    post = max(0.18, door_w * 0.12)
    for matrix in (
        Matrix.Translation((-(door_w / 2.0 + post / 2.0), -(half_d + jamb / 2.0), door_h / 2.0))
        @ Matrix.Diagonal((post, jamb, door_h, 1)),
        Matrix.Translation((door_w / 2.0 + post / 2.0, -(half_d + jamb / 2.0), door_h / 2.0))
        @ Matrix.Diagonal((post, jamb, door_h, 1)),
        Matrix.Translation((0.0, -(half_d + jamb / 2.0), door_h + post / 2.0))
        @ Matrix.Diagonal((door_w + 2.0 * post, jamb, post, 1)),
    ):
        bmesh.ops.create_cube(bm, size=1.0, matrix=matrix)

    bm.normal_update()
    bm.to_mesh(mesh)
    bm.free()
    return _new_object(name, mesh, [_material("BOB_Blockout", (0.42, 0.40, 0.38))])


BLOCKOUTS = {"shed": _shed}


def make_blockout(op: dict) -> dict:
    """MCP op: build a structure block-out in the scene, to be exported as a geometry control.

    Linked into the scene collection rather than a `BOB_Assets_*` pool, because a building is placed
    and not scattered, and because the next call is `export_control` on it by name.

    Any dimension the shape takes can be overridden through `params`, so a shed is a shed, a byre and
    a lean-to without a second recipe.
    """
    shape = str(op.get("shape") or "shed")
    make = BLOCKOUTS.get(shape)
    if make is None:
        raise ValueError(f"unknown block-out shape {shape!r}; have {sorted(BLOCKOUTS)}")
    name = op.get("name") or f"BOB_Blockout_{shape.capitalize()}"
    existing = bpy.data.objects.get(name)
    if existing is not None and not op.get("replace", False):
        raise ValueError(f"an object named {name!r} already exists; pass replace=true to rebuild it")
    if existing is not None:
        bpy.data.objects.remove(existing, do_unlink=True)
    params = {k: float(v) for k, v in (op.get("params") or {}).items()}
    obj = make(name, **params)
    bpy.context.scene.collection.objects.link(obj)
    if op.get("location"):
        obj.location = tuple(float(v) for v in op["location"])
    dims = tuple(round(d, 4) for d in obj.dimensions)
    return {"op": "make_blockout", "created": [obj.name],
            "data": {"object": obj.name, "shape": shape, "dimensions": list(dims),
                     "faces": len(obj.data.polygons)},
            "info": f"{obj.name}: {shape}, {dims[0]} x {dims[1]} x {dims[2]} m, "
                    f"{len(obj.data.polygons)} faces"}


def _collection_name(kind):
    return f"BOB_Assets_{kind.capitalize()}"


def collection(kind):
    """Return the BOB_Assets_<Kind> collection, creating it EMPTY if it does not exist.

    The get-or-create half of `ensure_collection`, without the proxy fabrication, for the caller
    that has an asset of its own to put in the pool. `gen_assets.import_generated` is that
    caller, and the split is a bug fix rather than a tidy-up: importing a generated boulder into
    an empty scene used to conjure three block-out proxies beside it, so a scatter layer pointed
    at the pool instanced the procedural blobs as well as the asset. Measured at the
    agent-surface gate, where the gate's render came back mostly proxies. Make Proxies is how an
    artist asks for proxies."""
    name = _collection_name(kind)
    found = bpy.data.collections.get(name)
    return found if found is not None else bpy.data.collections.new(name)


def ensure_collection(kind):
    """Return the BOB_Assets_<Kind> collection, creating proxies if it is empty."""
    coll = collection(kind)
    if not coll.objects:
        for obj_name, make_obj in _KINDS.get(kind, []):
            coll.objects.link(make_obj())
    return coll


def make_proxies(op: dict) -> dict:
    kinds = op.get("kinds") or ["trees", "rocks", "plants"]
    created = [ensure_collection(kind).name for kind in kinds]
    return {"op": "make_proxies", "created": created, "info": ",".join(kinds)}
