"""BobShaders orchestration, shared by the Shaders panel and the MCP ops.

The material node groups themselves are built in core/materials/; this module holds the
layer above them: the named presets, the small pure node helpers, the live-env driver
feed, and the params->result builder functions each panel Operator and each dispatch
handler calls. One builder serves the button and the op, so the socket writes that make a
terrain or a surface read right live in exactly one place.

bpy-only, and it never imports ui/: the panel operators import THIS module (presets +
helpers + builders) and keep only their context resolution (active object, editing
material, UI-state writes). The MCP handlers (shade_terrain / apply_shader / snow_shell)
resolve an object by name from the op and call the same builders.
"""

import bpy

from . import assets, env as _env, materials

SNOW_SHELL_MOD = "BOB_SnowShell"

# Surface presets: parameter sets applied to a surface wrapper's Master inputs. Solid-colour
# look + per-instance variation; the weather knobs stay at their defaults (snow whitens by
# coverage when the world snow rises).
SURFACE_PRESETS = {
    "rock": {"label": "Rock", "desc": "Mid-grey stone, rough, some variation",
             "knobs": {"Base Color": (0.34, 0.33, 0.31, 1.0), "Roughness": 0.85,
                       "Metallic": 0.0, "Variation": 0.15}},
    "cliff": {"label": "Cliff", "desc": "Dark grey-brown rock face",
              "knobs": {"Base Color": (0.22, 0.20, 0.18, 1.0), "Roughness": 0.90,
                        "Metallic": 0.0, "Variation": 0.20}},
    "bark": {"label": "Bark", "desc": "Warm brown tree bark",
             "knobs": {"Base Color": (0.20, 0.13, 0.08, 1.0), "Roughness": 0.80,
                       "Metallic": 0.0, "Variation": 0.25}},
    "soil": {"label": "Soil", "desc": "Dark earth, fully rough",
             "knobs": {"Base Color": (0.14, 0.10, 0.07, 1.0), "Roughness": 1.0,
                       "Metallic": 0.0, "Variation": 0.20}},
    "metal": {"label": "Metal", "desc": "Bare metal, low roughness",
              "knobs": {"Base Color": (0.56, 0.57, 0.58, 1.0), "Roughness": 0.30,
                        "Metallic": 1.0, "Variation": 0.05}},
    "painted": {"label": "Painted", "desc": "A flat painted surface",
                "knobs": {"Base Color": (0.30, 0.42, 0.55, 1.0), "Roughness": 0.50,
                          "Metallic": 0.0, "Variation": 0.0}},
    "grass_blade": {"label": "Grass Blade", "desc": "Green vegetation surface",
                    "knobs": {"Base Color": (0.16, 0.28, 0.09, 1.0), "Roughness": 0.60,
                              "Metallic": 0.0, "Variation": 0.30}},
}

# Terrain layer presets: a surface plus the masks that place it, applied to a slot. The masks
# reuse the scatter vocabulary (slope band, altitude band, noise), so a rock layer and rock
# scatter land on the same slopes. A base fill (soil/sand) carries no mask.
TERRAIN_LAYER_PRESETS = {
    "soil": {"label": "Soil", "desc": "Bare earth base fill (no mask)",
             "knobs": {"Base Color": (0.16, 0.11, 0.07, 1.0), "Roughness": 1.0,
                       "Slope Strength": 0.0, "Height Strength": 0.0, "Noise Strength": 0.0}},
    "grass": {"label": "Grass", "desc": "Green on flatter ground, clumped",
              "knobs": {"Base Color": (0.15, 0.26, 0.09, 1.0), "Roughness": 0.9,
                        "Min Normal Z": 0.6, "Slope Strength": 0.7,
                        "Noise Scale": 0.18, "Noise Contrast": 0.55, "Noise Strength": 0.5}},
    "rock": {"label": "Rock", "desc": "Grey stone on mid-to-steep slopes",
             "knobs": {"Base Color": (0.34, 0.33, 0.31, 1.0), "Roughness": 0.85,
                       "Min Normal Z": 0.0, "Max Normal Z": 0.6, "Slope Strength": 0.85}},
    "cliff": {"label": "Cliff", "desc": "Dark rock on the steepest faces",
              "knobs": {"Base Color": (0.20, 0.19, 0.18, 1.0), "Roughness": 0.9,
                        "Min Normal Z": 0.0, "Max Normal Z": 0.35, "Slope Strength": 1.0}},
    "scree": {"label": "Scree", "desc": "Loose tan debris on ridges / mid-slopes",
              "knobs": {"Base Color": (0.45, 0.40, 0.32, 1.0), "Roughness": 0.95,
                        "Min Normal Z": 0.3, "Max Normal Z": 0.75, "Slope Strength": 0.6,
                        "Curvature Strength": 0.3}},
    "sand": {"label": "Sand", "desc": "Pale sand in the low ground",
             "knobs": {"Base Color": (0.68, 0.62, 0.48, 1.0), "Roughness": 0.6,
                       "Height Max": 4.0, "Height Falloff": 3.0, "Height Strength": 0.8}},
}

# When Add Layer fills a fresh slot, seed it with this preset by slot index.
_ADD_ORDER = ("soil", "grass", "rock", "cliff", "scree", "sand")

# Terrain-stack presets: enable a set of slots and place them. `layers` is an ordered list of
# (layer-preset-key, overrides). Slots beyond the list are disabled.
TERRAIN_STACK_PRESETS = {
    "temperate": {"label": "Temperate", "desc": "Soil, clumped grass, rock on slopes",
                  "layers": [("soil", {}), ("grass", {}), ("rock", {})]},
    "alpine": {"label": "Alpine", "desc": "Rock, scree on ridges, cliff faces, snowy",
               "layers": [("rock", {}), ("scree", {}), ("cliff", {})],
               "weather": {"Snow Strength": 1.0}},
    "desert": {"label": "Desert", "desc": "Sand low, rock on slopes, scree ridges",
               "layers": [("sand", {"Height Strength": 0.0}), ("rock", {}), ("scree", {})],
               "weather": {"Snow Strength": 0.0}},
}


# -- Pure node helpers (shared by panel + ops) -----------------------------------------------
def master_node(mat):
    """The master group node inside a wrapper material (surface or terrain), or None."""
    if mat is None or not mat.use_nodes or mat.node_tree is None:
        return None
    node = mat.node_tree.nodes.get("Master")
    return node if node is not None and node.type == "GROUP" else None


def terrain_node(mat):
    """The Master node when it is the terrain master (has the layer sockets), else None."""
    node = master_node(mat)
    if node is None or node.node_tree is None:
        return None
    return node if node.node_tree is bpy.data.node_groups.get(materials.TERRAIN_MASTER) else None


def set_layer(node, i, knobs):
    """Set a terrain layer slot's inputs by knob name (the L{i} prefix added here)."""
    for k, v in knobs.items():
        sock = node.inputs.get(f"L{i} {k}")
        if sock is not None:
            sock.default_value = v


def layer_enabled(node, i):
    sock = node.inputs.get(f"L{i} Enable")
    return sock is not None and sock.default_value > 0.5


# -- Live-env driver feed --------------------------------------------------------------------
def _has_env(scene):
    return _env.get_env(scene) is not None


def _live_env_on(scene):
    """The master Live Environment toggle lives on the World panel (bbt_world); default on when
    World is absent (standalone / headless build)."""
    return getattr(getattr(scene, "bbt_world", None), "live_env", True)


def install_env_drivers(scene):
    """Drivers on the single shared S_EnvState group so every material reads the live world.
    Reinstalled on every New/Convert (harmless if already present)."""
    g = materials.env_state_group()
    for node_name, field, _default in materials.ENV_STATE_DRIVERS:
        node = g.nodes.get(node_name)
        if node is None:
            continue
        sock = node.outputs[0]
        try:
            sock.driver_remove("default_value")
        except (TypeError, RuntimeError):
            pass
        fc = sock.driver_add("default_value")
        fc = fc[0] if isinstance(fc, list) else fc
        drv = fc.driver
        drv.type = "SCRIPTED"
        var = drv.variables.new()
        var.name = "v"
        var.type = "SINGLE_PROP"
        tgt = var.targets[0]
        tgt.id_type = "SCENE"
        tgt.id = scene
        tgt.data_path = "bbt_env." + field
        drv.expression = "v"


def remove_env_drivers():
    g = bpy.data.node_groups.get(materials.ENV_STATE)
    if g is None:
        return
    for node in g.nodes:
        if node.type == "VALUE":
            try:
                node.outputs[0].driver_remove("default_value")
            except (TypeError, RuntimeError):
                pass


def feed_env(scene):
    """Install the S_EnvState drivers after a New/Convert if the world feed is on."""
    if _live_env_on(scene) and _has_env(scene):
        install_env_drivers(scene)


def apply_world_feed(scene):
    """The World-applier hook: install or remove the shared drivers per the Live Environment
    toggle, so raising the world snow whitens every surface with no rebuild."""
    if _live_env_on(scene) and _has_env(scene):
        install_env_drivers(scene)
    else:
        remove_env_drivers()


# -- Builder functions (params in, mat/result out) -------------------------------------------
def apply_surface_preset(node, preset):
    """Set a surface Master node's inputs from a SURFACE_PRESETS entry."""
    for name, val in SURFACE_PRESETS[preset]["knobs"].items():
        sock = node.inputs.get(name)
        if sock is not None:
            sock.default_value = val


def terrain_stack(node, preset):
    """Fill a terrain Master node's slots from a TERRAIN_STACK_PRESETS entry. Returns the count
    of enabled layers."""
    spec = TERRAIN_STACK_PRESETS[preset]
    layers = spec["layers"]
    for i in range(materials.MAX_TERRAIN_LAYERS):
        en = node.inputs.get(f"L{i} Enable")
        if en is None:
            continue
        if i < len(layers):
            key, over = layers[i]
            en.default_value = 1.0
            set_layer(node, i, TERRAIN_LAYER_PRESETS[key]["knobs"])
            set_layer(node, i, over)
        else:
            en.default_value = 0.0
    for k, v in spec.get("weather", {}).items():
        sock = node.inputs.get(k)
        if sock is not None:
            sock.default_value = v
    return len(layers)


def terrain_layers(node, layer_keys):
    """Fill a terrain Master node's slots from an ordered list of layer-preset keys (each a key
    of TERRAIN_LAYER_PRESETS). Unknown keys still enable the slot at defaults. Returns the count."""
    for i in range(materials.MAX_TERRAIN_LAYERS):
        en = node.inputs.get(f"L{i} Enable")
        if en is None:
            continue
        if i < len(layer_keys):
            en.default_value = 1.0
            key = layer_keys[i]
            if key in TERRAIN_LAYER_PRESETS:
                set_layer(node, i, TERRAIN_LAYER_PRESETS[key]["knobs"])
        else:
            en.default_value = 0.0
    return len(layer_keys)


def terrain_add_layer(node):
    """Enable the next free slot and seed it by index. Returns the slot index, or None if full."""
    nxt = next((i for i in range(materials.MAX_TERRAIN_LAYERS)
                if not layer_enabled(node, i)), None)
    if nxt is None:
        return None
    node.inputs[f"L{nxt} Enable"].default_value = 1.0
    set_layer(node, nxt, TERRAIN_LAYER_PRESETS[_ADD_ORDER[nxt]]["knobs"])
    return nxt


def set_terrain_texture(obj, mat, index, set_name):
    """Assign texture set `set_name` ("" clears it) to terrain layer slot `index` and rebuild the
    wrapper. Structural rather than a live knob: the sampler is graph, not a socket value. The
    other slots' sets and the projection mode carry forward, and the rebuild restores every tuned
    input it had. Returns the material."""
    sets, box = materials.stored_sets(mat, materials.MAX_TERRAIN_LAYERS)
    sets[index] = set_name or ""
    return materials.terrain_material_for(obj, mat_name=mat.name, texsets=sets, box=box)


def set_terrain_triplanar(obj, mat, box):
    """Switch a terrain material between box (triplanar) and top-down planar projection. Also
    structural: it is a property on each image node, not a socket."""
    sets, _ = materials.stored_sets(mat, materials.MAX_TERRAIN_LAYERS)
    return materials.terrain_material_for(obj, mat_name=mat.name, texsets=sets, box=box)


def set_surface_texture(mat, set_name):
    """set_terrain_texture's single-slot counterpart for a surface BobShader."""
    return materials.surface_material(mat.name, texset_name=set_name or "")


def set_texture_set(obj, mat, index, set_name):
    """Assign texture set `set_name` ("" clears it) to whichever master `mat` carries, and return
    a one-line label. Raises ValueError with a sentence when it cannot.

    The dispatch on master type is here rather than in the panel because the Shaders picker, the
    Generate-and-Accept path and the `apply_texture_set` op all need the same three lines: a terrain
    master takes a layer slot, a surface master takes its single set, and anything else is a
    material this cannot be asked of. `index` is clamped rather than rejected, because a terrain
    master's slot count is a build-time constant an agent has no way to read.
    """
    if mat is None:
        raise ValueError("no material to assign a texture set to")
    kind = materials.master_type(mat)
    if kind == "terrain":
        if obj is None:
            raise ValueError("a terrain texture set needs the object the material is on")
        i = max(0, min(int(index), materials.MAX_TERRAIN_LAYERS - 1))
        set_terrain_texture(obj, mat, i, set_name)
        return f"Layer {i}: {set_name or 'solid tint'}"
    if kind == "surface":
        set_surface_texture(mat, set_name)
        return f"Surface: {set_name or 'solid tint'}"
    raise ValueError(f"material {mat.name!r} is not a surface or terrain BobShader")


def set_surface_triplanar(mat, box):
    """set_terrain_triplanar's counterpart for a surface BobShader (box = un-UV'd projection,
    flat = the prop's own UVs)."""
    sets, _ = materials.stored_sets(mat, 1)
    return materials.surface_material(mat.name, texset_name=sets[0], box=box)


def snow_shell_add(surface):
    """Add the snow-accumulation shell modifier and keep the Set-Material modifier last. Returns
    (had_coverage, mod_name): had_coverage is False when no BOB_Snow pass feeds the shell yet."""
    from .geonodes import build_geonodes_on_object

    had_coverage = any(m.type == "NODES" and m.name == "BOB_Snow" for m in surface.modifiers)
    build_geonodes_on_object(surface, "snow_shell", SNOW_SHELL_MOD, {})
    setmat = next((m for m in surface.modifiers
                   if m.type == "NODES" and m.name == materials.SET_MATERIAL_MOD), None)
    if setmat is not None:
        surface.modifiers.move(list(surface.modifiers).index(setmat), len(surface.modifiers) - 1)
    return had_coverage, SNOW_SHELL_MOD


def build_terrain_material(obj, *, mat_name=None, stack=None, layers=None):
    """Get-or-create a terrain BobShader on obj and fill its layer stack, from a stack preset key
    (`stack`) or an explicit list of layer-preset keys (`layers`). Returns (material, node,
    layer_count). Does NOT assign; the caller decides (the panel keeps the active material, the
    op assigns)."""
    mat = materials.terrain_material_for(obj, mat_name=mat_name or obj.name)
    node = terrain_node(mat)
    if node is None:
        raise ValueError("could not build the terrain material (no terrain master node)")
    if layers is not None:
        count = terrain_layers(node, layers)
    elif stack is not None:
        if stack not in TERRAIN_STACK_PRESETS:
            raise ValueError(f"unknown terrain stack preset {stack!r} "
                             f"(have: {sorted(TERRAIN_STACK_PRESETS)})")
        count = terrain_stack(node, stack)
    else:
        count = terrain_stack(node, "temperate")
    return mat, node, count


# -- Object resolution + MCP handlers --------------------------------------------------------
def _mesh_object(name):
    """Resolve a mesh object by name for an op, with a clear error when it is missing or wrong
    type (the fragile bare-name binding the handover flags: fail loudly, do not no-op)."""
    if not name:
        raise ValueError("no object name given")
    obj = bpy.data.objects.get(name)
    if obj is None:
        raise ValueError(f"no object named {name!r} in the scene")
    if obj.type != "MESH":
        raise ValueError(f"object {name!r} is a {obj.type}, not a MESH")
    return obj


def shade_terrain(op: dict) -> dict:
    """MCP op: build and assign a terrain BobShader on an object from a stack preset or explicit
    layers. This is what kills the untextured-white terrain over MCP."""
    obj = _mesh_object(op.get("object"))
    stack = op.get("stack")
    layers = op.get("layers")
    mat_name = op.get("material") or obj.name
    mat, _node, count = build_terrain_material(
        obj, mat_name=mat_name, stack=stack, layers=layers)
    if op.get("assign", True):
        materials.assign_material(obj, mat)
    feed_env(bpy.context.scene)
    how = f"layers={layers}" if layers is not None else f"stack={stack or 'temperate'}"
    return {"op": "shade_terrain", "created": [mat.name],
            "info": f"{obj.name}: {count} layers ({how})"}


def apply_shader(op: dict) -> dict:
    """MCP op: create a BobShader (surface/terrain/water) on an object, optionally from a surface
    preset. Wraps New + Surface Preset."""
    obj = _mesh_object(op.get("object"))
    master = op.get("master", "surface")
    if master not in ("surface", "terrain", "water"):
        raise ValueError(f"master must be surface/terrain/water, got {master!r}")
    mat = materials.new_bobshader(obj, master)
    preset = op.get("preset")
    if master == "surface" and preset:
        if preset not in SURFACE_PRESETS:
            raise ValueError(f"unknown surface preset {preset!r} (have: {sorted(SURFACE_PRESETS)})")
        node = master_node(mat)
        if node is not None:
            apply_surface_preset(node, preset)
    if master == "water":
        materials.enable_eevee_refraction(bpy.context.scene)
    feed_env(bpy.context.scene)
    info = f"{obj.name}: {master} BobShader" + (f" ({preset})" if preset else "")
    return {"op": "apply_shader", "created": [mat.name], "info": info}


def apply_texture_set(op: dict) -> dict:
    """MCP op: assign a texture set to a terrain layer slot or to a surface BobShader.

    The generation half of this is `comfy_texture_set()`, which runs in the MCP process over HTTP
    with no bpy; this is the step that has to cross the bridge, because assigning a set rewires a
    material. Same `set_texture_set` the Shaders picker uses, so there is one assignment path.

    `set` is checked against the resolver rather than trusted: a name that no pack provides would
    otherwise wire image nodes with nothing behind them and render as flat grey, which looks like a
    shading bug rather than a missing folder.

    `pack_dir` is the pack the set came from, which is what every generation tool returns. Honoured
    rather than ignored: the MCP process writes into `$BOB_GENERATED` (or `<workdir>/packs/generated`)
    while a live addon registers its own output-folder preference, so without it a set that was just
    generated resolves in neither and this op fails on a folder that exists. Registering the root
    also keeps the set resolvable for the material rebuilds a later Shaders edit triggers.

    Both checks below are made because the folder existing is not the same as the maps resolving. A
    set folder with no readable map wires no sampler at all and the layer reads as a solid tint on
    screen -- success in the receipt, nothing in the frame, which is the failure the redwood run
    spent three texture sets on.
    """
    name = (op.get("set") or "").strip()
    assets.add_pack_root(op.get("pack_dir"))
    if name and assets.texture_set_dir(name) is None:
        have = assets.list_texture_sets()
        raise ValueError(f"no texture set {name!r} on the asset-pack search path "
                         f"(have: {', '.join(have) if have else 'none'}). A set generated by "
                         "comfy_texture_set lives in the generated pack, so pass the tool's "
                         "`pack_dir` on this op, or set $BOB_GENERATED (or the addon's output "
                         "folder) to the pack that holds it.")
    maps = assets.texture_set_maps(name) if name else {}
    if name and not maps.get("basecolor"):
        raise ValueError(
            f"texture set {name!r} at {assets.texture_set_dir(name)} carries no readable base-colour "
            f"map (found: {', '.join(sorted(maps)) or 'nothing'}). A set names its files "
            f"<role>.<ext> or <anything>_<role>.<ext>, roles "
            f"{', '.join(assets.TEXTURE_MAP_ROLES)}. Assigning it would report success and render as "
            "a solid tint.")

    mat_name = op.get("material")
    obj = _mesh_object(op["object"]) if op.get("object") else None
    if mat_name:
        mat = bpy.data.materials.get(mat_name)
        if mat is None:
            raise ValueError(f"no material named {mat_name!r}")
    else:
        if obj is None:
            raise ValueError("give either an object or a material to assign the texture set to")
        mat = obj.active_material
        if mat is None:
            raise ValueError(f"object {obj.name!r} has no material to assign a texture set to "
                             "(shade_terrain or apply_shader first)")
    label = set_texture_set(obj, mat, op.get("index", 0), name)
    feed_env(bpy.context.scene)
    return {"op": "apply_texture_set", "created": [mat.name],
            "data": {"maps": sorted(maps), "index": op.get("index", 0),
                     "dir": assets.texture_set_dir(name) if name else None},
            "info": f"{mat.name}: {label}" + (f" ({', '.join(sorted(maps))})" if maps else "")}


def snow_shell(op: dict) -> dict:
    """MCP op: add the snow-accumulation shell to an object."""
    obj = _mesh_object(op.get("object"))
    had_coverage, mod_name = snow_shell_add(obj)
    info = f"{obj.name}: snow shell added"
    if not had_coverage:
        info += " (no coverage pass yet; reads 0 until Atmosphere snow_cover)"
    return {"op": "snow_shell", "created": [f"{obj.name}:{mod_name}"], "info": info}
