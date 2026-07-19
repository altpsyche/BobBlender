"""BobShaders: authored surface materials, the layer that ties the suite together.

The surface-materials counterpart to the Scatter and Firmament panels, and the top of
the dependency graph. Like Scatter it is panel-only and in-process (no venv, no new MCP
op): it drives the shared shader node groups in bbmcp/materials.py directly, so a code
change to it needs an addon re-enable, never an MCP reconnect.

S1 is the master framework plus the surface master:

- A per-object material is a thin wrapper (M_<name>): one S_SurfaceMaster group node
  feeding one Principled BSDF. The instance parameters are that group node's input values,
  drawn live in the panel and edited in place (shader group inputs re-evaluate on edit, so
  there is no rebuild to preserve, unlike the GN modifier surface Scatter draws).
- S_SurfaceMaster (solid base colour + per-instance variation) ends in S_Weather (the snow
  term in S1), which reads the world through S_EnvState. The world reaches the shaders via
  drivers on S_EnvState installed once from bbt_env (the Firmament mechanism, Phase-0
  confirmed shared-drive-once). BobShaders owns its own Live Environment toggle on
  Scene.bbt_shaders; it reads the world state, never Firmament's UI state.

Two homes, no drift: the shared world is bbt_env (owned by Firmament); BobShaders' own UI
state is bbt_shaders. Coverage has one authority: read the snow_cover attribute on the
terrain, compute the pinned fallback formula everywhere else (Use Attribute picks).
"""

import bpy
from bpy.props import (
    BoolProperty,
    EnumProperty,
    IntProperty,
    PointerProperty,
    StringProperty,
)
from bpy.types import Operator, Panel, PropertyGroup

from . import server

# The bbmcp modules, imported at register and held so unregister uses the same objects
# even after Reload Builders purges bbmcp. _env_owned records whether BobShaders had to
# register the shared world itself (standalone), so it only unregisters what it owns.
_env = None
_env_owned = False

# Live knobs drawn per sub-panel, by socket name on the wrapper's Master group node.
_SURFACE_KNOBS = ["Base Color", "Roughness", "Metallic", "Variation"]
_WEATHER_KNOBS = ["Snow Strength", "Use Attribute", "Slope Threshold", "Slope Falloff",
                  "Altitude", "Altitude Falloff"]

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


def _wrapper_name(raw):
    from bbmcp import materials

    return raw if raw.startswith(materials.SURFACE_WRAPPER_PREFIX) \
        else materials.SURFACE_WRAPPER_PREFIX + raw


def _current_material(scn):
    """The wrapper material the panel is editing (may not exist until Build)."""
    server._ensure_path()
    return bpy.data.materials.get(_wrapper_name(scn.material_name))


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
# feeding every material that instances it (Phase-0). Reinstalled on every Build (harmless
# if already present, and the only path that (re)creates the group). Removed when Live
# Environment is off or Firmament is absent, so no driver dangles.
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


def _on_live_env_change(self, context):
    """Install or remove the shared env drivers when the toggle flips. A driver edit on a
    shared datablock, safe from the rebuild re-entrancy the repo avoids for structural
    changes."""
    if self.live_env and _has_env(context.scene):
        _install_env_drivers(context.scene)
    else:
        _remove_env_drivers()


class BBT_ShadersProps(PropertyGroup):
    """BobShaders' own UI state (not the shared world, which is bbt_env)."""

    target: PointerProperty(
        name="Object", type=bpy.types.Object,
        poll=lambda self, obj: obj.type == "MESH",
        description="Mesh the material is assigned to (or Use Active)")
    material_name: StringProperty(
        name="Material", default="M_Surface",
        description="Wrapper material name (M_ is added if missing). One material can be "
                    "assigned to many objects, so props and scatter share a surface")
    master: EnumProperty(
        name="Master",
        items=[("surface", "Surface", "Single-surface master for props, rocks, vegetation"),
               ("terrain", "Terrain", "Multi-layer terrain master (blends layers by slope, "
                                      "altitude, noise, paint with a height-aware blend)")],
        default="surface")
    terrain_active: IntProperty(
        name="Active Layer", default=0, min=0,
        description="The terrain layer slot the sub-panel edits")
    live_env: BoolProperty(
        name="Live Environment", default=True, update=_on_live_env_change,
        description="Drive the weather layer live from the Environment state (bbt_env) "
                    "through the shared S_EnvState group, so raising the world snow whitens "
                    "every surface with no rebuild. BobShaders' own toggle; it reads the "
                    "world state, not Firmament's UI")


class BBT_OT_shaders_use_active(Operator):
    bl_idname = "bob_blender_tools.shaders_use_active"
    bl_label = "Use Active"
    bl_description = "Set the target to the active object in the viewport"

    def execute(self, context):
        obj = context.active_object
        if obj is None or obj.type != "MESH":
            self.report({"ERROR"}, "Active object is not a mesh")
            return {"CANCELLED"}
        context.scene.bbt_shaders.target = obj
        return {"FINISHED"}


class BBT_OT_shaders_build(Operator):
    bl_idname = "bob_blender_tools.shaders_build"
    bl_label = "Build & Assign"
    bl_description = ("Build the surface master material (get-or-create, keeps tuned "
                      "inputs) and assign it to the target, then install the live env feed")

    def execute(self, context):
        scn = context.scene.bbt_shaders
        mats = _materials()
        mat = (mats.terrain_material(scn.material_name) if scn.master == "terrain"
               else mats.surface_material(scn.material_name))
        target = scn.target or context.active_object
        assigned = _assign(target, mat)
        if scn.live_env and _has_env(context.scene):
            _install_env_drivers(context.scene)
        where = f" on {target.name}" if assigned else " (no mesh target to assign)"
        self.report({"INFO"}, f"Built {mat.name} ({scn.master}){where}")
        return {"FINISHED"}


class BBT_OT_shaders_assign(Operator):
    bl_idname = "bob_blender_tools.shaders_assign"
    bl_label = "Assign to Active"
    bl_description = "Assign this material to the active object (share one surface across objects)"

    def execute(self, context):
        scn = context.scene.bbt_shaders
        mat = _current_material(scn)
        if mat is None:
            self.report({"ERROR"}, "Build the material first")
            return {"CANCELLED"}
        if not _assign(context.active_object, mat):
            self.report({"ERROR"}, "Active object is not a mesh")
            return {"CANCELLED"}
        self.report({"INFO"}, f"Assigned {mat.name} to {context.active_object.name}")
        return {"FINISHED"}


class BBT_OT_shaders_preset(Operator):
    bl_idname = "bob_blender_tools.shaders_preset"
    bl_label = "Surface Preset"
    bl_description = "Set the surface look from a named preset"
    bl_options = {"REGISTER", "UNDO"}

    preset: EnumProperty(
        name="Preset",
        items=[(k, v["label"], v["desc"]) for k, v in SURFACE_PRESETS.items()])

    def execute(self, context):
        scn = context.scene.bbt_shaders
        mat = _current_material(scn)
        node = _master_node(mat)
        if node is None:
            bpy.ops.bob_blender_tools.shaders_build()
            node = _master_node(_current_material(scn))
        if node is None:
            return {"CANCELLED"}
        for name, val in SURFACE_PRESETS[self.preset]["knobs"].items():
            sock = node.inputs.get(name)
            if sock is not None:
                sock.default_value = val
        self.report({"INFO"}, f"Applied {SURFACE_PRESETS[self.preset]['label']} preset")
        return {"FINISHED"}


def _ensure_terrain_node(context):
    """The active wrapper's terrain-master node, building the material if needed."""
    scn = context.scene.bbt_shaders
    node = _terrain_node(_current_material(scn))
    if node is None:
        _materials().terrain_material(scn.material_name)
        node = _terrain_node(_current_material(scn))
        if scn.live_env and _has_env(context.scene):
            _install_env_drivers(context.scene)
    return node


class BBT_OT_shaders_terrain_add(Operator):
    bl_idname = "bob_blender_tools.shaders_terrain_add"
    bl_label = "Add Layer"
    bl_description = "Enable the next terrain layer slot and seed it with a default surface"

    def execute(self, context):
        mats = _materials()
        node = _ensure_terrain_node(context)
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


class BBT_OT_shaders_terrain_remove(Operator):
    bl_idname = "bob_blender_tools.shaders_terrain_remove"
    bl_label = "Remove Layer"
    bl_description = "Disable the active terrain layer slot"

    def execute(self, context):
        node = _terrain_node(_current_material(context.scene.bbt_shaders))
        i = context.scene.bbt_shaders.terrain_active
        sock = node.inputs.get(f"L{i} Enable") if node else None
        if sock is None:
            return {"CANCELLED"}
        sock.default_value = 0.0
        return {"FINISHED"}


class BBT_OT_shaders_terrain_select(Operator):
    bl_idname = "bob_blender_tools.shaders_terrain_select"
    bl_label = "Select Layer"
    bl_description = "Edit this terrain layer slot"

    index: IntProperty(default=0)

    def execute(self, context):
        context.scene.bbt_shaders.terrain_active = self.index
        return {"FINISHED"}


class BBT_OT_shaders_terrain_toggle(Operator):
    bl_idname = "bob_blender_tools.shaders_terrain_toggle"
    bl_label = "Toggle Layer"
    bl_description = "Enable or disable this terrain layer slot"

    index: IntProperty(default=0)

    def execute(self, context):
        node = _terrain_node(_current_material(context.scene.bbt_shaders))
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
        node = _ensure_terrain_node(context)
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
        node = _ensure_terrain_node(context)
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


def _draw_inputs(layout, node, names):
    """Draw the wrapper Master node's input sockets by name (live, no rebuild)."""
    col = layout.column(align=True)
    for nm in names:
        sock = node.inputs.get(nm)
        if sock is not None:
            col.prop(sock, "default_value", text=nm)


def _draw_layer_inputs(layout, node, i, names):
    """Draw a terrain layer slot's inputs (the L{i} prefix added), by their bare labels."""
    col = layout.column(align=True)
    for nm in names:
        sock = node.inputs.get(f"L{i} {nm}")
        if sock is not None:
            col.prop(sock, "default_value", text=nm)


class BBT_PT_shaders(Panel):
    bl_label = "Shaders"
    bl_idname = "BBT_PT_shaders"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "BobBlenderTools"
    bl_options = {"DEFAULT_CLOSED"}

    def draw(self, context):
        scn = context.scene.bbt_shaders
        layout = self.layout

        row = layout.row(align=True)
        row.prop(scn, "target")
        row.operator("bob_blender_tools.shaders_use_active", text="", icon="EYEDROPPER")
        layout.prop(scn, "material_name")
        layout.prop(scn, "master", expand=True)
        layout.prop(scn, "live_env", icon="FORCE_WIND")

        row = layout.row(align=True)
        row.operator("bob_blender_tools.shaders_build", icon="MATERIAL")
        if scn.master == "terrain":
            row.operator_menu_enum("bob_blender_tools.shaders_terrain_stack_preset", "preset",
                                   text="Stack", icon="PRESET")
        else:
            row.operator_menu_enum("bob_blender_tools.shaders_preset", "preset",
                                   text="Preset", icon="PRESET")
        layout.operator("bob_blender_tools.shaders_assign", icon="LINKED")

        if not _has_env(context.scene):
            layout.label(text="Firmament off: no live weather", icon="INFO")


class BBT_PT_shaders_surface(Panel):
    bl_label = "Surface"
    bl_idname = "BBT_PT_shaders_surface"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "BobBlenderTools"
    bl_parent_id = "BBT_PT_shaders"

    @classmethod
    def poll(cls, context):
        return context.scene.bbt_shaders.master == "surface"

    def draw(self, context):
        scn = context.scene.bbt_shaders
        layout = self.layout
        node = _master_node(_current_material(scn))
        if node is None:
            layout.label(text="Build to edit surface inputs", icon="INFO")
            return
        _draw_inputs(layout, node, _SURFACE_KNOBS)


class BBT_PT_shaders_terrain(Panel):
    bl_label = "Terrain Layers"
    bl_idname = "BBT_PT_shaders_terrain"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "BobBlenderTools"
    bl_parent_id = "BBT_PT_shaders"

    @classmethod
    def poll(cls, context):
        return context.scene.bbt_shaders.master == "terrain"

    def draw(self, context):
        scn = context.scene.bbt_shaders
        layout = self.layout
        node = _terrain_node(_current_material(scn))
        if node is None:
            layout.label(text="Build (Terrain) to edit the layer stack", icon="INFO")
            return

        _draw_inputs(layout, node, _TERRAIN_GLOBAL)

        # Layer slots: one row each, an enable toggle plus a select button showing the base
        # colour. The stacking order is by Height Bias (not slot order), so no reorder needed.
        box = layout.box()
        active = scn.terrain_active
        for i in range(_materials().MAX_TERRAIN_LAYERS):
            en = node.inputs.get(f"L{i} Enable")
            if en is None:
                continue
            row = box.row(align=True)
            on = en.default_value > 0.5
            op = row.operator("bob_blender_tools.shaders_terrain_toggle", text="",
                              icon="CHECKBOX_HLT" if on else "CHECKBOX_DEHLT")
            op.index = i
            col = node.inputs.get(f"L{i} Base Color")
            if col is not None:
                row.prop(col, "default_value", text="")
            sel = row.operator("bob_blender_tools.shaders_terrain_select",
                               text=f"Layer {i}", depress=(i == active))
            sel.index = i
        row = box.row(align=True)
        row.operator("bob_blender_tools.shaders_terrain_add", icon="ADD")
        row.operator("bob_blender_tools.shaders_terrain_remove", icon="REMOVE")

        # Active layer: surface + a layer preset, then the placement masks.
        i = max(0, min(active, _materials().MAX_TERRAIN_LAYERS - 1))
        layout.label(text=f"Layer {i}", icon="NODE_TEXTURE")
        layout.operator_menu_enum("bob_blender_tools.shaders_terrain_layer_preset", "preset",
                                  text="Layer Preset", icon="PRESET")
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
        node = _terrain_node(_current_material(scn))
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


class BBT_PT_shaders_weather(Panel):
    bl_label = "Weather"
    bl_idname = "BBT_PT_shaders_weather"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "BobBlenderTools"
    bl_parent_id = "BBT_PT_shaders"
    bl_options = {"DEFAULT_CLOSED"}

    def draw(self, context):
        scn = context.scene.bbt_shaders
        layout = self.layout
        node = _master_node(_current_material(scn))
        if node is None:
            layout.label(text="Build to edit the weather layer", icon="INFO")
            return
        layout.label(text="Snow (whitens by coverage)", icon="FREEZE")
        layout.label(text="Use Attribute: 0 computed, 1 terrain snow_cover")
        _draw_inputs(layout, node, _WEATHER_KNOBS)


CLASSES = (
    BBT_ShadersProps,
    BBT_OT_shaders_use_active,
    BBT_OT_shaders_build,
    BBT_OT_shaders_assign,
    BBT_OT_shaders_preset,
    BBT_OT_shaders_terrain_add,
    BBT_OT_shaders_terrain_remove,
    BBT_OT_shaders_terrain_select,
    BBT_OT_shaders_terrain_toggle,
    BBT_OT_shaders_terrain_layer_preset,
    BBT_OT_shaders_terrain_stack_preset,
    BBT_PT_shaders,
    BBT_PT_shaders_surface,
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


def unregister():
    global _env_owned
    del bpy.types.Scene.bbt_shaders
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
    if _env_owned and _env is not None:
        _env.unregister()
        _env_owned = False
