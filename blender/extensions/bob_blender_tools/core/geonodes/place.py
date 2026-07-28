"""Placement: put a finished node group into the scene or the library.

Kept separate from recipes so recipes only ever build node graphs and never
touch objects.
"""

import bpy


def place(ng, name, target="new_object", mark_asset=False, location=None, collection=None):
    """Create the object and modifier. Return the list of created names.

    The node group and the object usually share a name (e.g. "Terrain"), so the
    returned list is de-duplicated in order: a caller reading `created` sees each
    real name once, not a "Terrain, Terrain" pair (object name + node group name).

    `location` puts the object somewhere other than the world origin. Every recipe
    builds around its object's origin, so without this the only place a build could
    land was (0, 0, 0) -- fine for a terrain, which is authored centred, and useless
    for anything you want to stand in a particular spot.

    `collection` names the collection the object joins instead of the scene's. A name
    that does not resolve is CREATED AND LEFT UNLINKED, which is what makes this the
    way to fill a scatter pool: `BOB_Assets_<Kind>` is an off-scene collection whose
    members are instanced rather than rendered directly (see `core.proxies`), so a
    tree built into it is a scatter source and not a tree standing on the origin. An
    existing collection is used as it is, linked or not, so this never changes whether
    a pool is in the scene.
    """
    created = [ng.name]

    if mark_asset:
        ng.asset_mark()

    if target == "new_object":
        mesh = bpy.data.meshes.new(name)
        obj = bpy.data.objects.new(name, mesh)
        target_coll = bpy.context.scene.collection
        if collection:
            target_coll = (bpy.data.collections.get(collection)
                           or bpy.data.collections.new(collection))
        target_coll.objects.link(obj)
        if location is not None:
            obj.location = tuple(location)
        modifier = obj.modifiers.new(name="GeometryNodes", type="NODES")
        modifier.node_group = ng
        created.append(obj.name)

    return list(dict.fromkeys(created))
