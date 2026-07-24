"""Biome orchestration, shared by the World / Biome panel and the MCP ops.

A biome is one preset that touches terrain, scatter, and the world together (docs/BIOME-SYSTEM.md).
The manifest is read and validated in core/assets.py; this module is the layer that stands the
biome up on a real object: it drives the shading terrain leg (core/shading), the scatter leg
(core/scatter_build), and the world leg (the bbt_env setattr loop) through direct core calls.

bpy-only, and it never imports ui/: the World panel operators import THIS module for the world
setattr loop, and the MCP handlers (apply_biome / world_biome) resolve the target object by name
and call the same core builders. The panel's Build Biome operator keeps chaining its sibling
operators (it needs the CANCELLED accounting, active-object management, and the Build-Sky step
that live on the ui side); the core handler is the bpy.ops-free equivalent for the agent path.

Scene state is read by plain attribute access (`getattr(scene, 'bbt_env', None)`), never through
the ui, so core stays the acyclic root of the dependency graph.
"""

import bpy

from . import assets, env as _env, materials, scatter_build, shading


# -- World leg (the bbt_env setattr loop; was world_biome_world's body) ----------------------
def apply_world(env, world):
    """Write a biome's world block onto the shared world state (Scene.bbt_env). `world` is a subset
    of the bbt_env fields (season, weather, time_of_day, ...). A field bbt_env does not carry, or a
    value it rejects, is skipped rather than raising, so a partial/loose manifest still applies what
    it can. Returns {"applied": [...], "skipped": [...]}.

    The live consumers (surface weather, atmosphere wind, snow coverage) read bbt_env through
    drivers, so setting the fields propagates with no rebuild; the ui applier registry re-affirms
    driver on/off + quality, which the panel operator calls after this."""
    if env is None:
        raise ValueError("no world state (Scene.bbt_env); BobFirmament is not registered")
    applied, skipped = [], []
    for field, val in (world or {}).items():
        if not hasattr(env, field):
            skipped.append(field)
            continue
        try:
            setattr(env, field, val)
            applied.append(field)
        except (TypeError, ValueError):
            print(f"[bob_blender_tools] biome world: bad value for {field!r}: {val!r}")
            skipped.append(field)
    return {"applied": applied, "skipped": skipped}


# -- MCP handlers (op dict in, result dict out; resolve the target by name) ------------------
def _weather_assets(layer_names, scene):
    """Convert the built scatter layers' assets to BobShaders (idempotent), deduped across layers
    by asset collection. Returns the count of collections weathered."""
    seen = 0
    done_colls = set()
    for n in layer_names:
        obj = bpy.data.objects.get(n)
        lay = getattr(obj, "bbt_scatter_layer", None) if obj is not None else None
        coll = getattr(lay, "assets", None) if lay is not None else None
        if coll is None or coll.name in done_colls:
            continue
        done_colls.add(coll.name)
        scatter_build._convert_layer_assets(lay, scene)
        seen += 1
    return seen


def apply_biome(op: dict) -> dict:
    """MCP op: stand up a whole biome on one target mesh -- its terrain material, its proxy scatter
    layers, and its world block, each section the manifest carries, in order. This is the agent-side
    equivalent of the panel's Build Biome, with no bpy.ops chaining.

    op: {"object": <mesh name>, "biome": <name>, "assign"?: bool, "weather_assets"?: bool,
    "world"?: bool, "curve_mode"?: "scatter"|"clear"|"keep"}. world=False skips the env-overwrite
    leg (call set_env after apply_biome without it being clobbered, or re-apply the biome scatter
    after building curves without touching the world). curve_mode makes the biome scatter
    path-aware -- "clear" pulls instances off carved paths, "keep" confines them to the path band;
    it reads the terrain curve mask, so build the typed curve first, then re-apply the biome (with
    world=False) to open the corridor. Raises ValueError on a missing object or biome (loud
    failure, per the handover)."""
    obj = shading._mesh_object(op.get("object"))
    biome = op.get("biome")
    if not biome:
        raise ValueError("no biome name given")
    if biome not in assets.list_biomes():
        raise ValueError(f"no biome named {biome!r} (have: {assets.list_biomes()})")
    man = assets.biome_manifest(biome)
    warn = assets.validate_biome(biome)
    scene = bpy.context.scene
    created, steps = [], []

    if man["terrain"]:
        layers = man["terrain"]["layers"]
        mat, _node, count = shading.build_terrain_material(
            obj, mat_name=obj.name, layers=[L.get("layer") for L in layers])
        if op.get("assign", True):
            materials.assign_material(obj, mat)
        shading.feed_env(scene)
        created.append(mat.name)
        textured = sum(1 for L in layers if L.get("texture"))
        steps.append(f"terrain ({count} layers, {textured} textured)")

    if man["scatter"]:
        names = scatter_build.biome_scatter(obj, man["scatter"], scene=scene,
                                            curve_mode=op.get("curve_mode"))
        created += names
        cm = op.get("curve_mode")
        steps.append(f"scatter ({len(names)} layers{f', {cm}' if cm else ''})")
        if op.get("weather_assets", True):
            n = _weather_assets(names, scene)
            if n:
                steps.append(f"weathered {n} asset set(s)")

    if man["world"] and op.get("world", True):
        res = apply_world(_env.get_env(scene), man["world"])
        shading.feed_env(scene)  # re-affirm the surface env feed against the new world
        steps.append(f"world ({len(res['applied'])} fields)")

    info = f"{obj.name}: {biome}: " + (", ".join(steps) or "nothing to apply")
    if warn:
        info += f" ({len(warn)} manifest warnings)"
        print("[bob_blender_tools] biome warnings:", warn)
    return {"op": "apply_biome", "created": created, "info": info}


def world_biome(op: dict) -> dict:
    """MCP op: set only the shared world (Scene.bbt_env) from a biome's world block -- no terrain,
    no scatter. The quick look-match leg of apply_biome.

    op: {"biome": <name>}. Raises ValueError on a missing biome or an absent world block."""
    biome = op.get("biome")
    if not biome:
        raise ValueError("no biome name given")
    world = assets.biome_world(biome)
    if not world:
        raise ValueError(f"biome {biome!r} carries no world block")
    scene = bpy.context.scene
    res = apply_world(_env.get_env(scene), world)
    shading.feed_env(scene)
    return {"op": "world_biome", "created": [],
            "info": f"{biome}: set {len(res['applied'])} world fields"
                    + (f", skipped {len(res['skipped'])}" if res["skipped"] else "")}
