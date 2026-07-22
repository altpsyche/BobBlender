"""BobShaders: authored surface materials, the layer that ties the suite together.

The surface-materials counterpart to the Scatter and Firmament panels, and the top of
the dependency graph. Like Scatter it is panel-only and in-process (no venv, no new MCP
op): it drives the shared shader node groups in bbmcp/materials.py directly, so a code
change to it needs an addon re-enable, never an MCP reconnect.

Native identity (docs/UX-REDESIGN.md 5.4): the panel edits the material on the ACTIVE
object's active material slot, not a stored material_name + target pointer. Select a mesh
and its material slots are listed; pick a slot and the sub-panels edit that material. A
slot's kind is DETECTED from the datablock (materials.master_type): a surface BobShader, a
terrain BobShader, or a plain material offered a Convert. New BobShader auto-names M_<object>.

- A per-object material is a thin wrapper (M_<name>): one S_SurfaceMaster (or S_TerrainMaster)
  group node feeding one Principled BSDF. The instance parameters are that group node's input
  values, drawn live and edited in place (shader group inputs re-evaluate on edit, so there is
  no rebuild to preserve, unlike the GN modifier surface Scatter draws).
- S_SurfaceMaster ends in S_Weather, which reads the world through S_EnvState. The world reaches
  the shaders via drivers on S_EnvState installed once from bbt_env. The one master Live
  Environment toggle lives on the World panel (bbt_world); Shaders subscribes _apply_world to the
  world applier registry so raising the world snow whitens every surface with no rebuild.

Two homes, no drift: the shared world is bbt_env (World panel); BobShaders' own UI state is
bbt_shaders. Coverage has one authority: read the snow_cover attribute on the terrain, compute
the pinned fallback formula everywhere else (Use Attribute picks).
"""

import bpy
from bpy.props import (
    EnumProperty,
    IntProperty,
    PointerProperty,
    StringProperty,
)
from bpy.types import Operator, Panel, PropertyGroup

from . import server, ui_helpers, world_panel

# The bbmcp modules, imported at register and held so unregister uses the same objects
# even after Reload Builders purges bbmcp. _env_owned records whether BobShaders had to
# register the shared world itself (standalone), so it only unregisters what it owns.
_env = None
_env_owned = False

# Live knobs drawn per sub-panel, by socket name on the wrapper's Master group node.
_SURFACE_KNOBS = ["Base Color", "Roughness", "Metallic", "Variation"]
_MACRO_KNOBS = ["Macro Amount", "Macro Scale"]
_WEATHER_SNOW = ["Snow Strength", "Use Attribute", "Slope Threshold", "Slope Falloff",
                 "Altitude", "Altitude Falloff"]
_WEATHER_WET = ["Wetness Strength", "Wet Pooling"]
_WEATHER_FROST = ["Frost Strength"]
_WEATHER_SEASON = ["Dust Amount", "Moss Amount"]
_SHELL_KNOBS = ["Thickness", "Smooth"]
SNOW_SHELL_MOD = "BOB_SnowShell"

# Water master knobs (BobSplines C5.3, look pass W1-W6), split colour/optics from the flow/foam
# animation, with the freeze slider on its own so it reads as the deliberate action it is.
_WATER_LOOK = ["Shallow Color", "Deep Color", "Depth", "Depth Absorption", "Depth Opacity",
               "Shoreline Fade", "Water Roughness", "IOR", "Transmission", "Edge Fade"]
_WATER_FLOW = ["Flow Speed", "Ripple Strength", "Ripple Scale", "Wave Detail", "Surface Texture",
               "Foam Color", "Foam Amount", "Shore Foam", "Foam Crispness"]
_WATER_FREEZE = ["Frozen"]

# Surface presets: named parameter sets applied to the wrapper's Master inputs (like the
# scatter layer types and the cloud presets). A Blender-side dict; nothing else reads it.
# S1 sets the solid-colour look and per-instance variation; the weather knobs stay at
# their defaults (snow whitens by coverage when the world snow rises).
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


# Terrain live knobs, grouped for the active-layer sub-panels (per-slot, prefixed L{i}).
_TERRAIN_GLOBAL = ["Blend Softness", "Macro Amount", "Macro Scale"]
_LAYER_SURFACE = ["Base Color", "Roughness", "Metallic", "Height Bias"]
_LAYER_SLOPE = ["Min Normal Z", "Max Normal Z", "Slope Strength"]
_LAYER_ALT = ["Height Min", "Height Max", "Height Falloff", "Height Strength"]
_LAYER_NOISE = ["Noise Scale", "Noise Contrast", "Noise Seed", "Noise Strength"]
_LAYER_OTHER = ["Paint Strength", "Curvature Strength"]
# Flow band keys off the baked drainage-flow map (a riverbed/gravel layer); Curve band keys off
# the curve overlay's baked mask (a path/road layer), both channels plus their hard-edge toggle.
_LAYER_FLOW = ["Flow Strength", "Flow Threshold"]
_LAYER_CURVE = ["Curve Strength", "Curve Hard", "Curve B Strength", "Curve B Hard"]

# Terrain layer presets: a surface plus the masks that place it, applied to the active slot.
# The masks reuse the scatter vocabulary (slope band, altitude band, noise), so a rock layer
# and rock scatter land on the same slopes. A base fill (soil/sand) carries no mask.
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
    "alpine": {"label": "Alpine", "desc": "Rock, scree on ridges, cliff faces, high snow",
               "layers": [("rock", {}), ("scree", {}), ("cliff", {})],
               "weather": {"Snow Strength": 1.0, "Altitude": 2.0, "Altitude Falloff": 6.0}},
    "desert": {"label": "Desert", "desc": "Sand low, rock on slopes, scree ridges",
               "layers": [("sand", {"Height Strength": 0.0}), ("rock", {}), ("scree", {})],
               "weather": {"Snow Strength": 0.0}},
}


def _materials():
    """The bbmcp.materials module, path ensured (the in-process bbmcp import Scatter uses)."""
    server._ensure_path()
    from bbmcp import materials

    return materials


# Native identity: the panel acts on the active object's active material slot (no stored name).
def _active_object(context):
    obj = context.active_object
    return obj if obj is not None and obj.type == "MESH" else None


def _active_material(context):
    """The material on the active object's active slot: the native identity the panel edits
    (docs/UX-REDESIGN.md 5.4). Selection follows the viewport and stays in sync with the
    Material Properties active slot."""
    obj = _active_object(context)
    return obj.active_material if obj is not None else None


def _is_scatter_object(obj):
    """A scatter layer object: its shaded look comes from an instanced asset collection, not its
    own material slots. New BobShader must not assign a solid material to it - assign_material
    would add a Set-Material modifier that overrides every instance's textures. Its assets are
    converted with Convert (Collection scope) instead."""
    lay = getattr(obj, "bbt_scatter_layer", None)
    return lay is not None and lay.assets is not None


def _asset_materials(obj):
    """Unique materials across a scatter layer's instanced assets collection, in a stable order.
    Empty when obj is not a scatter layer or its assets have no materials. Deduped by datablock:
    editing one material reaches every instance that uses it."""
    lay = getattr(obj, "bbt_scatter_layer", None)
    coll = lay.assets if lay is not None else None
    if coll is None:
        return []
    seen, out = set(), []
    for o in coll.all_objects:
        if o.type != "MESH":
            continue
        for slot in o.material_slots:
            m = slot.material
            if m is not None and m.name not in seen:
                seen.add(m.name)
                out.append(m)
    return out


def _editing_material(context):
    """The material the Surface / Weather sub-panels edit. For a normal mesh this is its active
    material (native identity). For a scatter layer object - whose asset sources live in an
    unlinked, unselectable collection - it is the chosen material from that asset pool
    (bbt_shaders.asset_material, defaulting to the first), so scattered assets are editable through
    the selectable layer without scene-linking their sources (docs/SCATTER-SHADING-UX.md)."""
    obj = _active_object(context)
    if obj is None:
        return None
    if _is_scatter_object(obj):
        mats = _asset_materials(obj)
        if not mats:
            return None
        want = context.scene.bbt_shaders.asset_material
        return next((m for m in mats if m.name == want), mats[0])
    return obj.active_material


def _master_node(mat):
    """The master group node inside a wrapper material (surface or terrain), or None."""
    if mat is None or not mat.use_nodes or mat.node_tree is None:
        return None
    node = mat.node_tree.nodes.get("Master")
    return node if node is not None and node.type == "GROUP" else None


def _terrain_node(mat):
    """The Master node when it is the terrain master (has the layer sockets), else None."""
    node = _master_node(mat)
    if node is None or node.node_tree is None:
        return None
    return node if node.node_tree is bpy.data.node_groups.get(_materials().TERRAIN_MASTER) else None


def _terrain_node_active(context):
    """The terrain-master node of the EDITING material (C1: keyed on _editing_material like the
    Surface / Water / Weather siblings, so on a scatter object the terrain sub-panel and its
    operators track the same material the panel edits, not the object's own active slot). The
    terrain sub-panel's poll guarantees that material is a terrain BobShader, so no fallback."""
    return _terrain_node(_editing_material(context))


def _named_mod(obj, name):
    """A NODES modifier by name (an object may carry the terrain, snow, shell passes)."""
    if obj is None:
        return None
    return next((m for m in obj.modifiers if m.type == "NODES" and m.name == name), None)


def _draw_mod_knobs(layout, mod, names):
    """Draw a GN modifier's live input values by socket name (mod.properties.inputs)."""
    if mod is None or mod.node_group is None:
        return
    ids = {it.name: it.identifier for it in mod.node_group.interface.items_tree
           if getattr(it, "item_type", None) == "SOCKET" and it.in_out == "INPUT"}
    col = layout.column(align=True)
    for nm in names:
        ident = ids.get(nm)
        inp = getattr(mod.properties.inputs, ident, None) if ident else None
        if inp is not None:
            col.prop(inp, "value", text=nm)


def _set_layer(node, i, knobs):
    """Set a terrain layer slot's inputs by knob name (the L{i} prefix added here)."""
    for k, v in knobs.items():
        sock = node.inputs.get(f"L{i} {k}")
        if sock is not None:
            sock.default_value = v


def _layer_enabled(node, i):
    sock = node.inputs.get(f"L{i} Enable")
    return sock is not None and sock.default_value > 0.5


def _assign(obj, mat):
    """Assign a material so the object shades, GN-generated meshes (terrain) included."""
    return _materials().assign_material(obj, mat)


# The live-env feed: drivers on the single shared S_EnvState group, installed once and
# feeding every material that instances it (Phase-0). Reinstalled on every New/Convert (harmless
# if already present, and the only path that (re)creates the group). Removed when the World Live
# Environment toggle is off or Firmament is absent, so no driver dangles.
def _install_env_drivers(scene):
    mats = _materials()
    g = mats.env_state_group()
    for node_name, field, _default in mats.ENV_STATE_DRIVERS:
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


def _remove_env_drivers():
    g = bpy.data.node_groups.get(_materials().ENV_STATE)
    if g is None:
        return
    for node in g.nodes:
        if node.type == "VALUE":
            try:
                node.outputs[0].driver_remove("default_value")
            except (TypeError, RuntimeError):
                pass


def _has_env(scene):
    return _env is not None and _env.get_env(scene) is not None


def _env_note(context, layout):
    """The "Firmament off: no live weather" hint. S9: drawn only on the Weather sub-panel, where
    the weather-driven knobs it warns about live. It used to also draw on the Shaders root, so a
    user with both open saw it twice; the root copy is gone."""
    if not _has_env(context.scene):
        layout.label(text="Firmament off: no live weather", icon="INFO")


def _live_env_on(scene):
    """The one master Live Environment toggle now lives on the World panel (bbt_world);
    default on when World is absent (standalone verify)."""
    return getattr(getattr(scene, "bbt_world", None), "live_env", True)


def _apply_world(scene):
    """Shaders' world applier (subscribed with world_panel): install or remove the shared
    S_EnvState drivers per the master Live Environment toggle, so raising the world snow
    whitens every surface with no rebuild. A driver edit on a shared datablock, safe from the
    rebuild re-entrancy the repo avoids for structural changes."""
    if _live_env_on(scene) and _has_env(scene):
        _install_env_drivers(scene)
    else:
        _remove_env_drivers()


def _feed_env(scene):
    """Install the S_EnvState drivers after a New/Convert if the world feed is on."""
    if _live_env_on(scene) and _has_env(scene):
        _install_env_drivers(scene)


# Biome-terrain enum: biomes (library/models/<name>) whose manifest carries a terrain spec, so a
# terrain can be built with the biome's matching texture sets. Cached module-side (enum GC pitfall).
_BIOME_TERRAIN_ITEMS = [("NONE", "None", "No biome terrain", "", 0)]
_BIOME_TERRAIN_IDS = {"NONE": 0}


def _biome_terrain_items(self, context):
    global _BIOME_TERRAIN_ITEMS
    server._ensure_path()
    from bbmcp import assets

    items = []
    for n in assets.list_biomes():
        if assets.biome_terrain(n) is None:
            continue
        if n not in _BIOME_TERRAIN_IDS:
            _BIOME_TERRAIN_IDS[n] = len(_BIOME_TERRAIN_IDS)  # next unused id, fixed for this session
        items.append((n, n.replace("_", " ").title(), f"Build the {n} terrain (stack + textures)",
                      "", _BIOME_TERRAIN_IDS[n]))
    _BIOME_TERRAIN_ITEMS = items or [("NONE", "None", "No biome carries a terrain spec", "", 0)]
    return _BIOME_TERRAIN_ITEMS


def _has_biome_terrain():
    server._ensure_path()
    from bbmcp import assets

    return any(assets.biome_terrain(n) is not None for n in assets.list_biomes())


class BBT_ShadersProps(PropertyGroup):
    """BobShaders' own UI state (not the shared world, which is bbt_env; not the material
    identity, which is the active object's active slot)."""

    terrain_active: IntProperty(
        name="Active Layer", default=0, min=0,
        description="The terrain layer slot the sub-panel edits")
    convert_scope: EnumProperty(
        name="Scope",
        items=[("active", "Active material", "Convert the active object's active material"),
               ("all", "All slots", "Convert every material slot of the active object"),
               ("selected", "Selected objects", "Convert every material on the selected meshes"),
               ("collection", "Collection", "Convert every material in the chosen collection "
                                             "(for the unlinked scatter asset collections, which "
                                             "are not viewport-selectable)")],
        default="active")
    convert_collection: PointerProperty(
        name="Collection", type=bpy.types.Collection,
        description="Batch-convert every material in this collection to a BobShader, e.g. a "
                    "scatter's BOB_Assets_* so the scattered instances weather with the ground")
    asset_material: StringProperty(
        name="Asset Material",
        description="Which material of the active scatter layer's instanced assets the Surface / "
                    "Weather sub-panels edit (the assets' sources are not viewport-selectable)")
    # The Live Environment toggle folded into the one World-panel master (bbt_world.live_env);
    # Shaders subscribes _apply_world to drive its S_EnvState feed (docs/UX-REDESIGN.md 5.4/7).


# Identity operators: New (create a BobShader), Convert (plain -> BobShader), Select (slot).
class BBT_OT_shaders_new(Operator):
    bl_idname = "bob_blender_tools.shaders_new"
    bl_label = "New BobShader"
    bl_description = ("Create a BobShader material (auto-named M_<object>), wire the chosen "
                      "master, and assign it to the active mesh. Identity is the datablock on "
                      "the slot, not a stored name")
    bl_options = {"REGISTER", "UNDO"}

    master: EnumProperty(
        name="Master",
        items=[("surface", "Surface", "Single-surface master for props, rocks, vegetation"),
               ("terrain", "Terrain", "Multi-layer terrain master (blends layers by slope, "
                                      "altitude, noise, paint with a height-aware blend)"),
               ("water", "Water", "Water-surface master for river/stream ribbons: flowing, "
                                  "depth-tinted, foaming, transparent, freezes below 0 C")],
        default="surface")

    def execute(self, context):
        obj = _active_object(context)
        if obj is None:
            self.report({"ERROR"}, "Select a mesh first")
            return {"CANCELLED"}
        mats = _materials()
        # A7: New creates on an empty/new slot; it never silently converts. A slot that already
        # holds a plain material is turned into a BobShader with Convert (which keeps its textures
        # and is surfaced per-row in the panel), so New and Convert stay distinct actions.
        existing = obj.active_material
        if existing is not None:
            if mats.master_type(existing) is not None:
                self.report({"INFO"}, f"{existing.name} is already a BobShader")
                return {"CANCELLED"}
            self.report({"ERROR"}, f"{existing.name} is a plain material; use Convert to keep its "
                                   f"textures (New only fills an empty slot)")
            return {"CANCELLED"}
        # A scatter object shows its assets' materials through instances; a New solid material
        # would override them all (via the Set-Material modifier). Convert the assets instead.
        if _is_scatter_object(obj):
            self.report({"ERROR"}, "Scatter object: convert its assets with Convert (Collection), "
                                   "not New - a solid material would hide their textures")
            return {"CANCELLED"}
        mat = mats.new_bobshader(obj, self.master)
        if self.master == "water":
            mats.enable_eevee_refraction(context.scene)  # so EEVEE-Next refracts the Transmission
        _feed_env(context.scene)
        self.report({"INFO"}, f"New {self.master} BobShader {mat.name} on {obj.name}")
        return {"FINISHED"}


class BBT_OT_shaders_convert(Operator):
    bl_idname = "bob_blender_tools.shaders_convert"
    bl_label = "Convert to BobShader"
    bl_description = ("Convert a plain material into a BobShader: route its own textures through "
                      "S_SurfaceMaster so it gains per-instance variation, macro break-up, and the "
                      "full weather layer (snow/wet/frost/dust/moss), keeping its alpha and "
                      "normals. Idempotent")
    bl_options = {"REGISTER", "UNDO"}

    index: IntProperty(default=-1)          # a specific slot of the active object (per-row)
    scope: EnumProperty(
        name="Scope",
        items=[("active", "Active material", ""), ("all", "All slots", ""),
               ("selected", "Selected objects", ""), ("collection", "Collection", "")],
        default="active")
    coll_name: StringProperty(default="")   # collection scope: this named collection, else the picker

    def _targets(self, context):
        obj = _active_object(context)
        seen, out = set(), []

        def add(m):
            if m is not None and m.name not in seen:
                seen.add(m.name)
                out.append(m)

        if self.index >= 0:
            if obj is not None and self.index < len(obj.material_slots):
                add(obj.material_slots[self.index].material)
        elif self.scope == "all":
            if obj is not None:
                for s in obj.material_slots:
                    add(s.material)
        elif self.scope == "active":
            add(_active_material(context))
        elif self.scope == "selected":
            for o in context.selected_objects:
                if o.type == "MESH":
                    for s in o.material_slots:
                        add(s.material)
        elif self.scope == "collection":
            coll = bpy.data.collections.get(self.coll_name) if self.coll_name \
                else context.scene.bbt_shaders.convert_collection
            if coll is not None:
                for o in coll.all_objects:
                    if o.type == "MESH":
                        for s in o.material_slots:
                            add(s.material)
        return out

    def execute(self, context):
        mats = _materials()
        targets = self._targets(context)
        if not targets:
            self.report({"WARNING"}, "No materials in scope to convert")
            return {"CANCELLED"}
        done = sum(1 for m in targets if mats.bobshade_material(m))
        _feed_env(context.scene)  # so the converted materials weather live from bbt_env
        self.report({"INFO"}, f"Converted {done} material(s) to BobShader")
        return {"FINISHED"}


class BBT_OT_shaders_select(Operator):
    # One "edit this item" selector for all three lists in Shaders: a material slot on the
    # active object, a material of the scattered assets, or a terrain layer slot. They only
    # differ in which active-state they set, so target picks which, instead of three
    # near-identical operator classes.
    bl_idname = "bob_blender_tools.shaders_select"
    bl_label = "Edit This"
    bl_description = "Edit this item in the Surface / Weather sub-panels"

    target: EnumProperty(
        items=[("slot", "Material slot", ""), ("asset", "Asset material", ""),
               ("layer", "Terrain layer", "")],
        default="slot")
    index: IntProperty(default=0)
    name: StringProperty(default="")

    def execute(self, context):
        scn = context.scene.bbt_shaders
        if self.target == "slot":
            obj = _active_object(context)
            if obj is None or self.index >= len(obj.material_slots):
                return {"CANCELLED"}
            obj.active_material_index = self.index
        elif self.target == "asset":
            scn.asset_material = self.name
        else:  # layer
            scn.terrain_active = self.index
        return {"FINISHED"}


class BBT_OT_shaders_preset(Operator):
    bl_idname = "bob_blender_tools.shaders_preset"
    bl_label = "Surface Preset"
    bl_description = "Set the active surface material's look from a named preset"
    bl_options = {"REGISTER", "UNDO"}

    preset: EnumProperty(
        name="Preset",
        items=[(k, v["label"], v["desc"]) for k, v in SURFACE_PRESETS.items()])

    def execute(self, context):
        node = _master_node(_editing_material(context))
        if node is None:
            self.report({"ERROR"}, "Active material is not a BobShader")
            return {"CANCELLED"}
        for name, val in SURFACE_PRESETS[self.preset]["knobs"].items():
            sock = node.inputs.get(name)
            if sock is not None:
                sock.default_value = val
        self.report({"INFO"}, f"Applied {SURFACE_PRESETS[self.preset]['label']} preset")
        return {"FINISHED"}


class BBT_OT_shaders_terrain_add(Operator):
    bl_idname = "bob_blender_tools.shaders_terrain_add"
    bl_label = "Add Layer"
    bl_description = "Enable the next terrain layer slot and seed it with a default surface"

    def execute(self, context):
        mats = _materials()
        node = _terrain_node_active(context)
        if node is None:
            return {"CANCELLED"}
        nxt = next((i for i in range(mats.MAX_TERRAIN_LAYERS) if not _layer_enabled(node, i)), None)
        if nxt is None:
            self.report({"WARNING"}, f"All {mats.MAX_TERRAIN_LAYERS} layer slots are in use")
            return {"CANCELLED"}
        node.inputs[f"L{nxt} Enable"].default_value = 1.0
        _set_layer(node, nxt, TERRAIN_LAYER_PRESETS[_ADD_ORDER[nxt]]["knobs"])
        context.scene.bbt_shaders.terrain_active = nxt
        self.report({"INFO"}, f"Added terrain layer {nxt}")
        return {"FINISHED"}


class BBT_OT_shaders_terrain_toggle(Operator):
    bl_idname = "bob_blender_tools.shaders_terrain_toggle"
    bl_label = "Toggle Layer"
    bl_description = "Enable or disable this terrain layer slot"

    index: IntProperty(default=0)

    def execute(self, context):
        node = _terrain_node_active(context)
        sock = node.inputs.get(f"L{self.index} Enable") if node else None
        if sock is None:
            return {"CANCELLED"}
        sock.default_value = 0.0 if sock.default_value > 0.5 else 1.0
        return {"FINISHED"}


class BBT_OT_shaders_terrain_layer_preset(Operator):
    bl_idname = "bob_blender_tools.shaders_terrain_layer_preset"
    bl_label = "Layer Preset"
    bl_description = "Set the active terrain layer's surface and placement masks"
    bl_options = {"REGISTER", "UNDO"}

    preset: EnumProperty(
        name="Preset",
        items=[(k, v["label"], v["desc"]) for k, v in TERRAIN_LAYER_PRESETS.items()])

    def execute(self, context):
        node = _terrain_node_active(context)
        i = context.scene.bbt_shaders.terrain_active
        if node is None:
            return {"CANCELLED"}
        _set_layer(node, i, TERRAIN_LAYER_PRESETS[self.preset]["knobs"])
        self.report({"INFO"}, f"Layer {i}: {TERRAIN_LAYER_PRESETS[self.preset]['label']}")
        return {"FINISHED"}


class BBT_OT_shaders_terrain_stack_preset(Operator):
    bl_idname = "bob_blender_tools.shaders_terrain_stack_preset"
    bl_label = "Stack Preset"
    bl_description = "Set the whole terrain layer stack from a named preset"
    bl_options = {"REGISTER", "UNDO"}

    preset: EnumProperty(
        name="Preset",
        items=[(k, v["label"], v["desc"]) for k, v in TERRAIN_STACK_PRESETS.items()])

    def execute(self, context):
        mats = _materials()
        node = _terrain_node_active(context)
        if node is None:
            return {"CANCELLED"}
        spec = TERRAIN_STACK_PRESETS[self.preset]
        for i in range(mats.MAX_TERRAIN_LAYERS):
            layers = spec["layers"]
            en = node.inputs.get(f"L{i} Enable")
            if i < len(layers):
                key, over = layers[i]
                en.default_value = 1.0
                _set_layer(node, i, TERRAIN_LAYER_PRESETS[key]["knobs"])
                _set_layer(node, i, over)
            else:
                en.default_value = 0.0
        for k, v in spec.get("weather", {}).items():
            sock = node.inputs.get(k)
            if sock is not None:
                sock.default_value = v
        context.scene.bbt_shaders.terrain_active = 0
        self.report({"INFO"}, f"Applied {spec['label']} stack")
        return {"FINISHED"}


class BBT_OT_shaders_biome_terrain(Operator):
    bl_idname = "bob_blender_tools.shaders_biome_terrain"
    bl_label = "Biome Terrain"
    bl_description = ("Build a terrain material for a biome on the active mesh: the biome's layer "
                      "stack (solid tints blended by placement masks), so the ground comes with "
                      "the right look. Get-or-create, keeps tuned inputs")
    bl_options = {"REGISTER", "UNDO"}

    biome: EnumProperty(name="Biome", items=_biome_terrain_items)

    def execute(self, context):
        obj = _active_object(context)
        if obj is None:
            self.report({"ERROR"}, "Select a mesh first")
            return {"CANCELLED"}
        server._ensure_path()
        from bbmcp import assets

        spec = assets.biome_terrain(self.biome) if self.biome and self.biome != "NONE" else None
        if not spec:
            self.report({"ERROR"}, f"No terrain spec for biome '{self.biome}'")
            return {"CANCELLED"}
        mats = _materials()
        layers = spec["layers"]
        # Update the active terrain material in place if there is one, else a per-object M_<obj>.
        active = _active_material(context)
        name = active.name if mats.master_type(active) == "terrain" else obj.name
        mat = mats.terrain_material_for(obj, mat_name=name)
        node = _terrain_node(mat)
        if node is None:
            self.report({"ERROR"}, "Could not build the terrain material")
            return {"CANCELLED"}
        for i in range(mats.MAX_TERRAIN_LAYERS):
            en = node.inputs.get(f"L{i} Enable")
            if en is None:
                continue
            if i < len(layers):
                en.default_value = 1.0
                key = layers[i].get("layer")
                if key in TERRAIN_LAYER_PRESETS:
                    _set_layer(node, i, TERRAIN_LAYER_PRESETS[key]["knobs"])
            else:
                en.default_value = 0.0
        _assign(obj, mat)
        _feed_env(context.scene)
        context.scene.bbt_shaders.terrain_active = 0
        textured = sum(1 for L in layers if L.get("texture"))
        self.report({"INFO"}, f"Built {self.biome} terrain on {obj.name} "
                              f"({len(layers)} layers, {textured} textured)")
        return {"FINISHED"}


class BBT_OT_shaders_snow_shell_add(Operator):
    bl_idname = "bob_blender_tools.shaders_snow_shell_add"
    bl_label = "Add Snow Shell"
    bl_description = ("Add the snow accumulation shell: a GN pass that displaces the surface "
                      "by snow_cover for real thickness and drifts. Needs the snow-coverage "
                      "pass first (it reads the same attribute)")

    def execute(self, context):
        surface = _active_object(context)
        if surface is None:
            self.report({"ERROR"}, "Select a mesh for the snow shell")
            return {"CANCELLED"}
        if _named_mod(surface, "BOB_Snow") is None:
            self.report({"WARNING"}, "No snow_cover pass on this surface (add it in Atmosphere); "
                                     "the shell will read 0 until then")
        server._ensure_path()
        from bbmcp.geonodes import build_geonodes_on_object

        build_geonodes_on_object(surface, "snow_shell", SNOW_SHELL_MOD, {})
        # Keep the Set-Material modifier last so the shell's geometry is still shaded.
        mats = _materials()
        setmat = _named_mod(surface, mats.SET_MATERIAL_MOD)
        if setmat is not None:
            surface.modifiers.move(list(surface.modifiers).index(setmat), len(surface.modifiers) - 1)
        self.report({"INFO"}, f"Snow shell added on {surface.name}")
        return {"FINISHED"}


class BBT_OT_shaders_snow_shell_remove(Operator):
    bl_idname = "bob_blender_tools.shaders_snow_shell_remove"
    bl_label = "Remove Snow Shell"
    bl_description = "Remove the snow accumulation shell modifier"

    def execute(self, context):
        surface = _active_object(context)
        mod = _named_mod(surface, SNOW_SHELL_MOD)
        if mod is None:
            self.report({"WARNING"}, "No snow shell to remove")
            return {"CANCELLED"}
        surface.modifiers.remove(mod)
        self.report({"INFO"}, "Removed snow shell")
        return {"FINISHED"}


def _draw_inputs(layout, node, names, labels=None):
    """Draw the wrapper Master node's input sockets by name (live, no rebuild). labels overrides
    the row text for specific sockets, so a socket whose name collides with another node's (e.g.
    the TexSet's Macro Amount next to the master's) can read distinctly."""
    labels = labels or {}
    col = layout.column(align=True)
    for nm in names:
        sock = node.inputs.get(nm)
        if sock is not None:
            col.prop(sock, "default_value", text=labels.get(nm, nm))


def _draw_layer_inputs(layout, node, i, names):
    """Draw a terrain layer slot's inputs (the L{i} prefix added), by their bare labels."""
    col = layout.column(align=True)
    for nm in names:
        sock = node.inputs.get(f"L{i} {nm}")
        if sock is not None:
            col.prop(sock, "default_value", text=nm)


# Per-row slot status icons and labels by detected master type.
_MASTER_TAG = {"surface": ("MATERIAL", "Surface"), "terrain": ("MESH_GRID", "Terrain"),
               "water": ("MATFLUID", "Water")}


class BBT_PT_shaders(Panel):
    bl_label = "Shaders"
    bl_idname = "BBT_PT_shaders"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "BobBlenderTools"
    bl_order = 5  # pipeline stage: Shaders (docs/UX-REDESIGN.md section 4)
    bl_options = {"DEFAULT_CLOSED"}

    def draw(self, context):
        layout = self.layout
        mats = _materials()
        scn = context.scene.bbt_shaders
        obj = _active_object(context)

        # P1/P7: the context header, or the empty state that says what to do next.
        if not ui_helpers.context_header(layout, "Active mesh", obj.name if obj else None,
                                         icon="OUTLINER_OB_MESH",
                                         empty="Select a mesh to shade its materials."):
            return

        # Scatter layer object: its look is the instanced assets, whose sources are unlinked and
        # not viewport-selectable. Edit those materials here, through the (selectable) layer.
        if _is_scatter_object(obj):
            self._draw_scatter_assets(context, layout, obj)
            return

        slots = obj.material_slots
        if len(slots) == 0:
            layout.label(text="Materials: (none)")
            self._draw_new_shader(layout)
            self._draw_more_convert(context, layout)
            return

        # Contextual list: EVERY material slot of this mesh and nothing else (decision 2).
        active_idx = obj.active_material_index
        box = layout.box()
        box.label(text="Materials on this mesh:")
        for i, slot in enumerate(slots):
            m = slot.material
            mt = mats.master_type(m) if m is not None else None
            row = box.row(align=True)
            ui_helpers.select_row(row, "bob_blender_tools.shaders_select",
                                  m.name if m is not None else "(empty)", i == active_idx,
                                  op_props={"target": "slot", "index": i})
            if m is None:
                row.label(text="empty")
            elif mt in _MASTER_TAG:
                ic, lbl = _MASTER_TAG[mt]
                row.label(text=f"BobShader: {lbl}", icon=ic)
            else:
                op = row.operator("bob_blender_tools.shaders_convert", text="Convert",
                                  icon="NODE_MATERIAL")
                op.index = i
        # A7: no "Convert all" button here. Whole-object convert is now the "All slots" option of
        # the scope dropdown below, so Convert lives in exactly two places: per-row (targeted) and
        # the scope dropdown (all slots / selected / collection).

        # Adaptive action for the active slot (P5): New when empty, else the editing header.
        active_mat = slots[active_idx].material if active_idx < len(slots) else None
        active_type = mats.master_type(active_mat)
        if active_mat is None:
            self._draw_new_shader(layout)
        elif active_type is not None:
            layout.label(text=f"editing: {active_mat.name} ({active_type})", icon="GREASEPENCIL")
        else:
            layout.label(text=f"{active_mat.name}: plain, Convert above", icon="INFO")

        self._draw_more_convert(context, layout)

    @staticmethod
    def _draw_new_shader(layout):
        # The one "create a shader here" affordance: New BobShader. Shared by the no-materials and
        # empty-active-slot cases so it is authored once rather than as two identical blocks.
        # Biome Terrain lives only in the Terrain Layers sub-panel (its real home, where the layer
        # stack is authored); it was removed from here to kill the duplicate entry point.
        row = layout.row(align=True)
        row.operator_menu_enum("bob_blender_tools.shaders_new", "master",
                               text="New BobShader", icon="ADD")

    @staticmethod
    def _draw_more_convert(context, layout):
        # Convert materials beyond the active object -- every material on the selected meshes,
        # or a whole collection (e.g. an unlinked scatter asset collection). The per-row
        # Convert / Convert all above already handle the active object, so this is just the
        # wider scopes, kept as one compact row rather than a boxed section.
        scn = context.scene.bbt_shaders
        row = layout.row(align=True)
        row.prop(scn, "convert_scope", text="")
        op = row.operator("bob_blender_tools.shaders_convert", text="Convert", icon="NODE_MATERIAL")
        op.scope = scn.convert_scope
        if scn.convert_scope == "collection":
            layout.prop(scn, "convert_collection", text="")

    @staticmethod
    def _draw_scatter_assets(context, layout, obj):
        # A scatter layer's look is its instanced assets. List their (deduped) materials with a
        # select and a status; a single Convert turns the whole asset collection into BobShaders.
        # The selected material drives the Surface / Weather sub-panels (via _editing_material).
        mats = _materials()
        scn = context.scene.bbt_shaders
        coll = obj.bbt_scatter_layer.assets
        asset_mats = _asset_materials(obj)
        box = layout.box()
        box.label(text=f"Scattered assets: {coll.name if coll else '(none)'}",
                  icon="OUTLINER_OB_GROUP_INSTANCE")
        if not asset_mats:
            box.label(text="No materials to edit in the asset collection", icon="INFO")
            return
        names = {m.name for m in asset_mats}
        sel = scn.asset_material if scn.asset_material in names else asset_mats[0].name
        any_plain = False
        for m in asset_mats:
            mt = mats.master_type(m)
            row = box.row(align=True)
            ui_helpers.select_row(row, "bob_blender_tools.shaders_select", m.name, m.name == sel,
                                  op_props={"target": "asset", "name": m.name})
            if mt in _MASTER_TAG:
                ic, lbl = _MASTER_TAG[mt]
                row.label(text=lbl, icon=ic)
            else:
                any_plain = True
                row.label(text="plain", icon="DOT")
        if any_plain and coll is not None:
            op = box.operator("bob_blender_tools.shaders_convert",
                              text="Convert assets to BobShader", icon="NODE_MATERIAL")
            op.scope = "collection"
            op.coll_name = coll.name
        cap = box.row()
        cap.enabled = False
        cap.label(text="pick a material, then tune it in Surface / Weather below")

class BBT_PT_shaders_surface(Panel):
    bl_label = "Surface"
    bl_idname = "BBT_PT_shaders_surface"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "BobBlenderTools"
    bl_parent_id = "BBT_PT_shaders"

    @classmethod
    def poll(cls, context):
        return _materials().master_type(_editing_material(context)) == "surface"

    def draw(self, context):
        scn = context.scene.bbt_shaders
        layout = self.layout
        mat = _editing_material(context)
        node = _master_node(mat)
        if node is None:
            return
        ui_helpers.preset_row(layout, "bob_blender_tools.shaders_preset")
        _draw_inputs(layout, node, _SURFACE_KNOBS)
        # A scattered asset shades through its instanced collection, not its own slot, so the
        # tint/rough/variation above are all it exposes here.
        if _is_scatter_object(_active_object(context)):
            cap = layout.row()
            cap.enabled = False
            cap.label(text="scattered asset: above tints/modulates its look")
            return
        # Macro break-up modulates the base albedo off a low-frequency world noise, so the solid
        # colour surface does not read as one flat sheet. Amount 0 = off.
        layout.label(text="Macro break-up", icon="MOD_NOISE")
        _draw_inputs(layout, node, _MACRO_KNOBS)


class BBT_PT_shaders_water(Panel):
    bl_label = "Water"
    bl_idname = "BBT_PT_shaders_water"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "BobBlenderTools"
    bl_parent_id = "BBT_PT_shaders"

    @classmethod
    def poll(cls, context):
        return _materials().master_type(_editing_material(context)) == "water"

    def draw(self, context):
        # A2: the root shows the depth/optics look (the default view). Flow+foam and Freeze moved
        # to DEFAULT_CLOSED child sub-panels below, the way Firmament splits its subsystems, so the
        # 23-knob wall is no longer one flat scroll.
        layout = self.layout
        node = _master_node(_editing_material(context))
        if node is None:
            return
        layout.label(text="Depth colour + optics", icon="MATFLUID")
        _draw_inputs(layout, node, _WATER_LOOK)


class BBT_PT_shaders_water_flow(Panel):
    bl_label = "Flow and foam"
    bl_idname = "BBT_PT_shaders_water_flow"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "BobBlenderTools"
    bl_parent_id = "BBT_PT_shaders_water"
    bl_options = {"DEFAULT_CLOSED"}  # animated, needs playback: not the default view

    def draw(self, context):
        layout = self.layout
        node = _master_node(_editing_material(context))
        if node is None:
            return
        cap = layout.row()
        cap.enabled = False
        cap.label(text="animated, needs playback", icon="FORCE_FORCE")
        _draw_inputs(layout, node, _WATER_FLOW)


class BBT_PT_shaders_water_freeze(Panel):
    bl_label = "Freeze"
    bl_idname = "BBT_PT_shaders_water_freeze"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "BobBlenderTools"
    bl_parent_id = "BBT_PT_shaders_water"
    bl_options = {"DEFAULT_CLOSED"}  # deliberate action, folded so it reads as one

    def draw(self, context):
        layout = self.layout
        node = _master_node(_editing_material(context))
        if node is None:
            return
        _draw_inputs(layout, node, _WATER_FREEZE)
        cap = layout.row()
        cap.enabled = False
        cap.label(text="Frozen 1 = ice; also freezes below 0 C (Weather sub-panel)")


class BBT_PT_shaders_terrain(Panel):
    bl_label = "Terrain Layers"
    bl_idname = "BBT_PT_shaders_terrain"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "BobBlenderTools"
    bl_parent_id = "BBT_PT_shaders"

    @classmethod
    def poll(cls, context):
        return _materials().master_type(_editing_material(context)) == "terrain"

    def draw(self, context):
        scn = context.scene.bbt_shaders
        layout = self.layout
        mat = _editing_material(context)
        node = _terrain_node(mat)
        if node is None:
            return

        row = layout.row(align=True)
        ui_helpers.preset_row(row, "bob_blender_tools.shaders_terrain_stack_preset",
                              text="Stack Preset")
        if _has_biome_terrain():
            row.operator_menu_enum("bob_blender_tools.shaders_biome_terrain", "biome",
                                   text="Biome Terrain", icon=ui_helpers.STRUCTURAL_ICON)
            cap = layout.row()
            cap.enabled = False
            cap.label(text="terrain layers only; the Biome panel builds the whole scene",
                      icon="INFO")
        _draw_inputs(layout, node, _TERRAIN_GLOBAL)

        # Layer slots (S3-layers/A3): draw only the ENABLED slots, not a fixed six-row stack, so
        # the box shows the depth actually in use. One disable model: the per-row checkbox turns a
        # layer off (it drops out of the list); Add Layer below is the sole add affordance. The old
        # Remove Layer button was a second way to do the checkbox's job, so it is gone. Stacking is
        # by Height Bias (not slot order), so no reorder is needed.
        maxn = _materials().MAX_TERRAIN_LAYERS
        active = scn.terrain_active
        enabled = [i for i in range(maxn)
                   if (en := node.inputs.get(f"L{i} Enable")) is not None and en.default_value > 0.5]
        box = layout.box()
        cap = box.row()
        cap.enabled = False
        cap.label(text=f"{len(enabled)} of {maxn} layers", icon="RENDERLAYERS")
        for i in enabled:
            row = box.row(align=True)
            op = row.operator("bob_blender_tools.shaders_terrain_toggle", text="",
                              icon="CHECKBOX_HLT")
            op.index = i
            col = node.inputs.get(f"L{i} Base Color")
            if col is not None:
                row.prop(col, "default_value", text="")
            ui_helpers.select_row(row, "bob_blender_tools.shaders_select", f"Layer {i}",
                                  i == active, radio=False,
                                  op_props={"target": "layer", "index": i})
        if len(enabled) < maxn:
            box.operator("bob_blender_tools.shaders_terrain_add", icon="ADD")

        # Active layer: surface + a layer preset, then the placement masks.
        i = max(0, min(active, _materials().MAX_TERRAIN_LAYERS - 1))
        layout.label(text=f"Layer {i}", icon="NODE_TEXTURE")
        ui_helpers.preset_row(layout, "bob_blender_tools.shaders_terrain_layer_preset",
                              text="Layer Preset")
        _draw_layer_inputs(layout, node, i, _LAYER_SURFACE)


class BBT_PT_shaders_terrain_masks(Panel):
    bl_label = "Layer Masks"
    bl_idname = "BBT_PT_shaders_terrain_masks"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "BobBlenderTools"
    bl_parent_id = "BBT_PT_shaders_terrain"
    bl_options = {"DEFAULT_CLOSED"}

    def draw(self, context):
        scn = context.scene.bbt_shaders
        layout = self.layout
        node = _terrain_node(_editing_material(context))
        if node is None:
            return
        i = max(0, min(scn.terrain_active, _materials().MAX_TERRAIN_LAYERS - 1))
        layout.label(text=f"Layer {i} placement (same masks as Scatter)")
        layout.label(text="Slope band", icon="NORMALS_FACE")
        _draw_layer_inputs(layout, node, i, _LAYER_SLOPE)
        layout.label(text="Altitude band", icon="SORT_ASC")
        _draw_layer_inputs(layout, node, i, _LAYER_ALT)
        layout.label(text="Noise / clumping", icon="MOD_NOISE")
        _draw_layer_inputs(layout, node, i, _LAYER_NOISE)
        layout.label(text="Paint / curvature", icon="BRUSH_DATA")
        _draw_layer_inputs(layout, node, i, _LAYER_OTHER)
        # Flow / curve bands: place a layer in the drainage channels or along a path/road. Both
        # need their source baked (Paths Bake & Erode for curve, terrain flow for flow); default
        # Strength 0 leaves the layer untouched, so an unbaked scene is unchanged.
        layout.label(text="Flow band (drainage channels)", icon="MATFLUID")
        _draw_layer_inputs(layout, node, i, _LAYER_FLOW)
        layout.label(text="Curve band (path / road)", icon="CURVE_DATA")
        _draw_layer_inputs(layout, node, i, _LAYER_CURVE)


class BBT_PT_shaders_weather(Panel):
    bl_label = "Weather"
    bl_idname = "BBT_PT_shaders_weather"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "BobBlenderTools"
    bl_parent_id = "BBT_PT_shaders"
    bl_options = {"DEFAULT_CLOSED"}

    @classmethod
    def poll(cls, context):
        return _materials().master_type(_editing_material(context)) is not None

    def draw(self, context):
        layout = self.layout
        mat = _editing_material(context)
        node = _master_node(mat)
        if node is None:
            return
        # The weather knobs below are the ones the live world drives; repeat the env-off warning
        # here (it also shows on the root) so it sits with the inert knobs, not a panel away.
        _env_note(context, layout)
        layout.label(text="Snow (whitens by coverage)", icon="FREEZE")
        layout.label(text="Use Attribute: 0 computed, 1 terrain snow_cover")
        _draw_inputs(layout, node, _WEATHER_SNOW)
        layout.label(text="Wetness (rain/storm darken; env.wetness)", icon="MATFLUID")
        _draw_inputs(layout, node, _WEATHER_WET)
        layout.label(text="Frost (below freezing, up-facing)", icon="FREEZE")
        _draw_inputs(layout, node, _WEATHER_FROST)
        layout.label(text="Season aging (dust up / moss down)", icon="OUTLINER_DATA_SURFACE")
        _draw_inputs(layout, node, _WEATHER_SEASON)

        # Snow accumulation shell: a GN modifier on the active surface object. It shells one mesh,
        # so it is not offered when editing a scatter layer's shared asset material (the shell
        # would attach to the instancer, not the assets).
        if _is_scatter_object(_active_object(context)):
            return
        box = layout.box()
        box.label(text="Snow Accumulation Shell", icon="MOD_SMOOTH")
        surface = _active_object(context)
        shell = _named_mod(surface, SNOW_SHELL_MOD)
        # A4: the shell reads the surface's snow_cover pass for its thickness. Say so inline, before
        # the Add press, rather than only in a post-click warning, so the dependency is visible up
        # front (the coverage pass is built in Atmosphere > Snow Coverage).
        if _named_mod(surface, "BOB_Snow") is None:
            cap = box.row()
            cap.enabled = False
            cap.label(text="needs a coverage pass: Atmosphere > Snow Coverage (reads 0 until then)",
                      icon="INFO")
        row = box.row(align=True)
        if shell is None:
            row.operator("bob_blender_tools.shaders_snow_shell_add", icon="ADD")
        else:
            row.operator("bob_blender_tools.shaders_snow_shell_remove", icon="REMOVE")
            _draw_mod_knobs(box, shell, _SHELL_KNOBS)


CLASSES = (
    BBT_ShadersProps,
    BBT_OT_shaders_new,
    BBT_OT_shaders_convert,
    BBT_OT_shaders_select,
    BBT_OT_shaders_preset,
    BBT_OT_shaders_terrain_add,
    BBT_OT_shaders_terrain_toggle,
    BBT_OT_shaders_terrain_layer_preset,
    BBT_OT_shaders_terrain_stack_preset,
    BBT_OT_shaders_biome_terrain,
    BBT_OT_shaders_snow_shell_add,
    BBT_OT_shaders_snow_shell_remove,
    BBT_PT_shaders,
    BBT_PT_shaders_surface,
    BBT_PT_shaders_water,
    BBT_PT_shaders_water_flow,
    BBT_PT_shaders_water_freeze,
    BBT_PT_shaders_terrain,
    BBT_PT_shaders_terrain_masks,
    BBT_PT_shaders_weather,
)


def register():
    global _env, _env_owned
    server._ensure_path()
    from bbmcp import env
    _env = env
    # Firmament owns the shared world; register it here only if running standalone (e.g. a
    # headless verify), and record ownership so unregister only removes what it created.
    if getattr(bpy.types.Scene, "bbt_env", None) is None:
        env.register()
        _env_owned = True
    for cls in CLASSES:
        bpy.utils.register_class(cls)
    bpy.types.Scene.bbt_shaders = bpy.props.PointerProperty(type=BBT_ShadersProps)
    # Subscribe the surface applier so the World master Live Environment toggle drives it.
    world_panel.register_applier(_apply_world)


def unregister():
    global _env_owned
    world_panel.unregister_applier(_apply_world)
    del bpy.types.Scene.bbt_shaders
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
    if _env_owned and _env is not None:
        _env.unregister()
        _env_owned = False
