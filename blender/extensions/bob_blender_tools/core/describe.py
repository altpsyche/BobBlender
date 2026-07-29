"""describe_scene: read the scene back, so an agent can check instead of guess.

The one read-only op. Everything else in the vocabulary WRITES, and until this existed there was no
way to ask what the scene already held: which objects are in it, which layer slot a curve band took,
which texture set is on a terrain layer, what a terrain was built from, which curves carry which
role and mask attribute. A scene run therefore guessed slot indices and rendered probe
frames to read the scene back, which is slow, lossy, and wrong often enough to matter.

What it reports is chosen by the same rule the op results follow: the values a NEXT call needs as
arguments, and the values a check would otherwise have to be inferred from a render. So a material
reports its layer slots with their texture sets and which masks key them; a terrain reports the
heightmap, size, height and sea level it was built with (the four numbers a drape has to agree on);
a curve reports its role, shape params and the mask attribute a scatter layer must target.

Read-only, and deliberately so: it takes no name to act on and mutates nothing, so it is safe to
send at any point in a batch. bpy-only.
"""

import bpy

from . import assets, env as _env, materials

# The custom props a heightmap_terrain build stamps on its object (core/geonodes stamps them; the
# Terrain panel's bake stamps the same ones). Reported under `terrain` when any is present.
_TERRAIN_PROPS = (("heightmap", "bbt_heightmap"), ("size", "bbt_terrain_size"),
                  ("resolution", "bbt_terrain_res"), ("height", "bbt_terrain_height"),
                  ("sea_level", "bbt_terrain_sea"), ("heightmap_clean", "bbt_heightmap_clean"))

# Per-layer mask strengths worth reporting. A slot whose masks are all 0 and whose Enable is 1 is a
# plain base fill; a slot with Curve Strength 1 is a curve band. This is how "which slot did the
# band take" becomes readable without re-deriving apply_curve_surface's slot choice.
_LAYER_MASKS = ("Slope Strength", "Height Strength", "Noise Strength", "Paint Strength",
                "Curvature Strength", "Flow Strength", "Curve Strength", "Curve B Strength")


def _num(value):
    """A JSON-safe number/string for a custom prop or socket value."""
    if isinstance(value, (int, float, str, bool)):
        return round(value, 4) if isinstance(value, float) else value
    try:
        return [round(float(v), 4) for v in value]
    except TypeError:
        return str(value)


def _material_report(mat):
    """One material's BobShader shape: master kind, texture sets, and the layer slots of a terrain
    master with the masks that key each one."""
    kind = materials.master_type(mat)
    out = {"name": mat.name, "master": kind}
    if kind is None:
        return out
    node = mat.node_tree.nodes.get("Master")
    if kind == "terrain":
        sets, box = materials.stored_sets(mat, materials.MAX_TERRAIN_LAYERS)
        out["box_projection"] = box
        layers = []
        for i in range(materials.MAX_TERRAIN_LAYERS):
            en = node.inputs.get(f"L{i} Enable")
            if en is None:
                continue
            texset = sets[i] or None
            masks = {}
            for key in _LAYER_MASKS:
                sock = node.inputs.get(f"L{i} {key}")
                if sock is not None and sock.default_value > 0.0:
                    masks[key] = round(float(sock.default_value), 4)
            layers.append({
                "index": i,
                "enabled": en.default_value > 0.5,
                "texture_set": texset,
                # Reported even when the set is named, because a set that resolves no maps on disk
                # renders as a solid tint: the difference between "assigned" and "reaching the
                # frame" is exactly this list, and it is not visible in any receipt otherwise.
                "maps": sorted(assets.texture_set_maps(texset)) if texset else [],
                "base_color": _num(node.inputs[f"L{i} Base Color"].default_value)
                if node.inputs.get(f"L{i} Base Color") else None,
                "masks": masks,
            })
        out["layers"] = layers
    elif kind == "surface":
        sets, box = materials.stored_sets(mat, 1)
        out["texture_set"] = sets[0] or None
        out["maps"] = sorted(assets.texture_set_maps(sets[0])) if sets[0] else []
        out["box_projection"] = box
    return out


def _object_report(obj, materials_seen):
    """One object: type, transform, geometry size, modifier stack, materials, terrain build params."""
    rep = {
        "name": obj.name,
        "type": obj.type,
        "location": [round(c, 4) for c in obj.location],
        "dimensions": [round(c, 4) for c in obj.dimensions],
        "visible": not obj.hide_render,
        # The modifier stack IN ORDER, which is the thing that goes wrong invisibly: a GN-generated
        # mesh shades through the LAST modifier, so a Set-Material that is not last renders grey.
        "modifiers": [{"name": m.name, "type": m.type,
                       "node_group": getattr(getattr(m, "node_group", None), "name", None)}
                      for m in obj.modifiers],
    }
    if obj.type == "MESH":
        rep["faces"] = len(obj.data.polygons)
        mats = [s.material.name for s in obj.material_slots if s.material is not None]
        setmat = next((m for m in obj.modifiers if m.name == materials.SET_MATERIAL_MOD), None)
        if setmat is not None and getattr(setmat, "node_group", None) is not None:
            for node in setmat.node_group.nodes:
                if node.bl_idname == "GeometryNodeSetMaterial":
                    driven = node.inputs["Material"].default_value
                    if driven is not None and driven.name not in mats:
                        mats.append(driven.name)
        rep["materials"] = mats
        for name in mats:
            mat = bpy.data.materials.get(name)
            if mat is not None:
                materials_seen.setdefault(name, mat)
        terrain = {key: _num(obj[prop]) for key, prop in _TERRAIN_PROPS if prop in obj}
        if terrain:
            rep["terrain"] = terrain
    if obj.type == "CURVE":
        cfg = getattr(obj, "bbt_curve", None)
        if cfg is not None:
            from . import splines_build

            role_key = cfg.role
            role = splines_build.ROLES.get(role_key, {})
            rep["curve"] = {
                "role": role_key,
                "channels": {"terrain": cfg.do_terrain, "material": cfg.do_material,
                             "water": cfg.do_water, "scatter": cfg.do_scatter},
                "banks_from_erosion": cfg.banks_from_erosion,
                "shape": splines_build._shape_of(obj),
                # The two attribute names a scatter layer or a material layer has to target. They
                # move with the ROLE, which is why changing a role to change a width broke every
                # scatter layer's curve_attr.
                "mask_attr": role.get("surface_attr") or "bbt_curve_mask",
                "edge_attr": splines_build._edge_attr_name(obj),
                "points": sum(len(s.points) + len(s.bezier_points) for s in obj.data.splines),
            }
    return rep


def _world_report(scene):
    env = _env.get_env(scene)
    if env is None:
        return {"present": False}
    out = {"present": True}
    for field in ("time_of_day", "year", "month", "day", "season", "weather", "temperature",
                  "wetness", "snow_line", "snow_z_base", "snow_z_span", "frost", "cloud_cover",
                  "wind_direction", "wind_strength", "latitude", "longitude", "utc_offset"):
        if hasattr(env, field):
            out[field] = _num(getattr(env, field))
    world = getattr(scene, "bbt_world", None)
    if world is not None:
        out["live_env"] = bool(world.live_env)
        out["quality"] = world.quality
    # Whether the shared driver feed is actually INSTALLED, which is the difference between a world
    # value that reaches materials and one that just sits on the scene.
    group = bpy.data.node_groups.get(materials.ENV_STATE)
    driven = 0
    if group is not None and group.animation_data is not None:
        driven = len(group.animation_data.drivers)
    out["env_drivers"] = driven
    return out


def describe_scene(op: dict) -> dict:
    """Read-only op: report the scene's objects, materials, curves, collections and world state.

    params: objects (optional list of names, else every object), include (optional list of sections
    to return, from objects/materials/collections/world/packs; default all).

    Nothing is mutated and no name is required, so this is safe anywhere in a batch.
    """
    scene = bpy.context.scene
    wanted = op.get("include") or ["objects", "materials", "collections", "world", "packs"]
    names = op.get("objects")
    objs = ([o for o in (bpy.data.objects.get(n) for n in names) if o is not None]
            if names else list(scene.objects))

    seen_materials = {}
    data = {}
    if "objects" in wanted:
        data["objects"] = [_object_report(o, seen_materials) for o in objs]
    else:
        for o in objs:  # still gather the materials the requested sections need
            _object_report(o, seen_materials)
    if "materials" in wanted:
        data["materials"] = [_material_report(m) for m in
                             sorted(seen_materials.values(), key=lambda m: m.name)]
    if "collections" in wanted:
        data["collections"] = [{"name": c.name, "objects": sorted(o.name for o in c.objects)}
                               for c in sorted(bpy.data.collections, key=lambda c: c.name)]
    if "world" in wanted:
        data["world"] = _world_report(scene)
    if "packs" in wanted:
        data["packs"] = {"roots": assets.asset_roots(),
                         "op_roots": assets.op_roots(),
                         "generated": assets.generated_root(),
                         "texture_sets": assets.list_texture_sets(),
                         "biomes": assets.list_biomes()}
    counts = ", ".join(f"{len(v)} {k}" for k, v in data.items() if isinstance(v, list))
    return {"op": "describe_scene", "created": [], "data": data,
            "info": f"{scene.name}: {counts or 'world + packs only'}"}
