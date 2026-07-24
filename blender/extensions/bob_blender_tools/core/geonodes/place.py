"""Placement: put a finished node group into the scene or the library.

Kept separate from recipes so recipes only ever build node graphs and never
touch objects.
"""

import bpy


def place(ng, name, target="new_object", mark_asset=False):
    """Create the object and modifier. Return the list of created names.

    The node group and the object usually share a name (e.g. "Terrain"), so the
    returned list is de-duplicated in order: a caller reading `created` sees each
    real name once, not a "Terrain, Terrain" pair (object name + node group name).
    """
    created = [ng.name]

    if mark_asset:
        ng.asset_mark()

    if target == "new_object":
        mesh = bpy.data.meshes.new(name)
        obj = bpy.data.objects.new(name, mesh)
        bpy.context.scene.collection.objects.link(obj)
        modifier = obj.modifiers.new(name="GeometryNodes", type="NODES")
        modifier.node_group = ng
        created.append(obj.name)

    return list(dict.fromkeys(created))
