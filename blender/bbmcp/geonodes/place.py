"""Placement: put a finished node group into the scene or the library.

Kept separate from recipes so recipes only ever build node graphs and never
touch objects.
"""

import bpy


def place(ng, name, target="new_object", mark_asset=False):
    """Create the object and modifier. Return the list of created names."""
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

    return created
