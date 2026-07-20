"""Real CC0 model assets for the scatter (BobShaders whole-look).

Imports glTF models downloaded under `library/models/<biome>/` into the scatter asset
collections (`BOB_Assets_<Kind>`), replacing the block-out proxies so the scatter instances
real, geographically-coherent meshes (all from one Poly Haven scan location). bpy-only.

glTF, not .blend: it keeps the download light and self-describes its PBR textures. The assets
keep their NATIVE materials - trees and grass need their alpha-leaf materials, which the opaque
surface master cannot represent, so BobShaders textures the ground and the real assets bring
their own look. GN instancing references each mesh once, so even a 2M-poly scanned tree is
memory-cheap to scatter; keep density modest for render time.

A biome folder carries a `manifest.json`: {kind: ["<asset>/<file>.gltf", ...]}, kind being
trees / rocks / grass / plants (the scatter's asset kinds).
"""

import json
import os

import bpy
from mathutils import Matrix


def biome_dir(name):
    """`library/models/<name>`, resolved from this file's repo location."""
    repo = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    return os.path.join(repo, "library", "models", name)


def _assets_collection(kind):
    """The scatter asset collection for a kind, created if absent. Deliberately NOT linked to
    the scene (the scatter instances it; the source objects should not render directly)."""
    name = f"BOB_Assets_{kind.capitalize()}"
    coll = bpy.data.collections.get(name)
    if coll is None:
        coll = bpy.data.collections.new(name)
    return coll


def _clear_collection(coll):
    for o in list(coll.objects):
        coll.objects.unlink(o)
        if o.users == 0:
            bpy.data.objects.remove(o, do_unlink=True)


def import_gltf(path):
    """Import a glTF and return its mesh objects, unparented with their transform baked into the
    mesh (so a scattered instance starts clean) and the glTF's empties/armatures removed."""
    before = set(bpy.data.objects)
    bpy.ops.import_scene.gltf(filepath=path)
    new = [o for o in bpy.data.objects if o not in before]
    meshes = [o for o in new if o.type == "MESH"]
    for o in meshes:
        mw = o.matrix_world.copy()
        o.parent = None
        o.matrix_world = mw
        o.data.transform(o.matrix_world)  # bake the Y-up->Z-up + placement into the mesh
        o.matrix_world = Matrix()
    for o in new:
        if o.type != "MESH":
            bpy.data.objects.remove(o, do_unlink=True)
    return meshes


def populate_scatter_assets(biome):
    """Replace the block-out proxies in each BOB_Assets_<Kind> with the biome's real meshes.

    Reuses an existing asset collection (so a scatter's Collection Info keeps pointing at it and
    its instances update live), else creates it. Returns {kind: mesh count}."""
    base = biome_dir(biome) if not os.path.isabs(biome) else biome
    manifest = json.load(open(os.path.join(base, "manifest.json")))
    counts = {}
    for kind, files in manifest.items():
        coll = _assets_collection(kind)
        _clear_collection(coll)
        n = 0
        for rel in files:
            for o in import_gltf(os.path.join(base, rel)):
                for c in list(o.users_collection):
                    c.objects.unlink(o)
                coll.objects.link(o)
                n += 1
        counts[kind] = n
    return counts
