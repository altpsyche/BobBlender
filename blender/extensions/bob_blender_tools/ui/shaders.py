"""BobShaders: authored surface materials, the layer that ties the suite together.

The surface-materials counterpart to the Scatter and Firmament panels, and the top of the dependency
graph. Like Scatter it is panel-only and in-process (no venv, no new MCP op): it drives the shared
shader node groups in bbmcp/materials.py directly, so a code change to it needs an addon re-enable,
never an MCP reconnect.

Native identity (docs/CONVENTIONS.md, panel UX conventions): the panel edits the material on the
ACTIVE object's active material slot, not a stored material_name + target pointer. Select a mesh and
its material slots are listed; pick a slot and the sub-panels edit that material. A slot's kind is
DETECTED from the datablock (materials.master_type): a surface BobShader, a terrain BobShader, or a
plain material offered a Convert. New BobShader auto-names M_<object>.

- A per-object material is a thin wrapper (M_<name>): one S_SurfaceMaster (or S_TerrainMaster)
 group node feeding one Principled BSDF. The instance parameters are that group node's input values,
 drawn live and edited in place (shader group inputs re-evaluate on edit, so there is no rebuild to
 preserve, unlike the GN modifier surface Scatter draws).
- S_SurfaceMaster ends in S_Weather, which reads the world through S_EnvState. The world reaches
 the shaders via drivers on S_EnvState installed once from bbt_env. The one master Live Environment
 toggle lives on the World panel (bbt_world); Shaders subscribes _apply_world to the world applier
 registry so raising the world snow whitens every surface with no rebuild.

Two homes, no drift: the shared world is bbt_env (World panel); BobShaders' own UI state is
bbt_shaders. Coverage has one authority: the shader computes it the same way on every surface, keyed
off the env snow line, so terrain and assets obey the same line (no attribute switch).
"""

import os

import bpy
import bpy.utils.previews  # not implied by `import bpy`, and shaders.py can register standalone
from bpy.props import (
    EnumProperty,
    IntProperty,
    PointerProperty,
    StringProperty,
)
from bpy.types import Operator, Panel, PropertyGroup

from ..bridge import server
from ..core import shading, util
from . import helpers, world

# The bbmcp modules, imported at register and held so unregister uses the same objects
# even after Reload Builders purges bbmcp. _env_owned records whether BobShaders had to
# register the shared world itself (standalone), so it only unregisters what it owns.
_env = None
_env_owned = False

# Live knobs drawn per sub-panel, by socket name on the wrapper's Master group node.
_SURFACE_KNOBS = ["Base Color", "Roughness", "Metallic", "Variation"]
_MACRO_KNOBS = ["Macro Amount", "Macro Scale"]
_WEATHER_SNOW = ["Snow Strength", "Slope Threshold", "Slope Falloff"]
_WEATHER_WET = ["Wetness Strength", "Wet Pooling"]
_WEATHER_FROST = ["Frost Strength"]
_WEATHER_SEASON = ["Dust Amount", "Moss Amount"]
_SHELL_KNOBS = ["Thickness", "Smooth"]
SNOW_SHELL_MOD = "BOB_SnowShell"

# Water master knobs (BobSplines, the water look, the water look pass), split colour/optics from the
# flow/foam animation, with the freeze slider on its own so it reads as the deliberate action it is.
_WATER_LOOK = ["Shallow Color", "Deep Color", "Depth", "Depth Absorption", "Depth Opacity",
               "Shoreline Fade", "Water Roughness", "IOR", "Transmission", "Edge Fade"]
_WATER_FLOW = ["Flow Speed", "Ripple Strength", "Ripple Scale", "Wave Detail", "Surface Texture",
               "Foam Color", "Foam Amount", "Shore Foam", "Foam Crispness"]
_WATER_FREEZE = ["Frozen"]

# Surface / terrain / stack presets live in core/shading.py so the panel operators and the MCP
# ops share one copy (subtract-duplication; docs/CONVENTIONS.md). Bound here for the enum items
# and the operator bodies that read them.
SURFACE_PRESETS = shading.SURFACE_PRESETS


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

# Terrain layer / stack presets and the Add-Layer seed order: sourced from core/shading.py.
TERRAIN_LAYER_PRESETS = shading.TERRAIN_LAYER_PRESETS
_ADD_ORDER = shading._ADD_ORDER
TERRAIN_STACK_PRESETS = shading.TERRAIN_STACK_PRESETS


def _materials():
    """The bbmcp.materials module, path ensured (the in-process bbmcp import Scatter uses)."""
    from ..core import materials

    return materials


# Native identity: the panel acts on the active object's active material slot (no stored name).
def _active_object(context):
    obj = context.active_object
    return obj if obj is not None and obj.type == "MESH" else None


def _active_material(context):
    """The material on the active object's active slot: the native identity the panel edits
    (docs/CONVENTIONS.md, panel UX conventions). Selection follows the viewport and stays in sync
    with the Material Properties active slot."""
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
    the selectable layer without scene-linking their sources (docs/SCATTER.md)."""
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


# The pure node helpers live in core/shading.py (shared with the MCP ops); bound here.
_master_node = shading.master_node
_terrain_node = shading.terrain_node


def _terrain_node_active(context):
    """The terrain-master node of the EDITING material (keyed on _editing_material like the
    Surface / Water / Weather siblings, so on a scatter object the terrain sub-panel and its
    operators track the same material the panel edits, not the object's own active slot). The
    terrain sub-panel's poll guarantees that material is a terrain BobShader, so no fallback."""
    return _terrain_node(_editing_material(context))


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


_set_layer = shading.set_layer
_layer_enabled = shading.layer_enabled


def _assign(obj, mat):
    """Assign a material so the object shades, GN-generated meshes (terrain) included."""
    return _materials().assign_material(obj, mat)


# The live-env feed (drivers on the shared S_EnvState group) lives in core/shading.py so the
# panel and the MCP shading ops install it the same way; bound here.
_install_env_drivers = shading.install_env_drivers
_remove_env_drivers = shading.remove_env_drivers


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


# Shaders' world applier (subscribed with world): install or remove the shared S_EnvState drivers
# per the master Live Environment toggle, so raising the world snow whitens every surface with no
# rebuild. A driver edit on a shared datablock, safe from the rebuild re-entrancy the repo avoids
# for structural changes. The three lines live in core/shading so the ops reach the same applier.
_apply_world = shading.apply_world_feed


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
    from ..core import assets

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
    from ..core import assets

    return any(assets.biome_terrain(n) is not None for n in assets.list_biomes())


# Texture-set enum: every `<pack>/textures/<set>/` on the search path, plus the explicit "none"
# that clears a slot back to a solid tint. Cached module-side for the same enum GC pitfall as the
# biome enum above (Blender does not keep a reference to the returned strings).
_TEXTURE_SET_ITEMS = [("NONE", "(none)", "Solid tint: no texture set on this layer", "", 0)]
_TEXTURE_SET_IDS = {"NONE": 0}


def _texture_set_items(self, context):
    global _TEXTURE_SET_ITEMS
    from ..core import assets

    items = [("NONE", "(none)", "Solid tint: no texture set on this layer", "", 0)]
    for n in assets.list_texture_sets():
        if n not in _TEXTURE_SET_IDS:
            _TEXTURE_SET_IDS[n] = len(_TEXTURE_SET_IDS)  # next unused id, fixed for this session
        items.append((n, n.replace("_", " ").title(), f"Sample the {n} texture set", "",
                      _TEXTURE_SET_IDS[n]))
    _TEXTURE_SET_ITEMS = items
    return _TEXTURE_SET_ITEMS


def _staged_variants():
    """Absolute paths of the variants waiting for a decision, oldest first. A listdir, so it is
    safe to call from draw; nothing here touches a socket."""
    from ..core import assets, comfy

    pack = assets.generated_root()
    return comfy.list_variants(pack) if pack else []


def _staged_variant_items(self, context):
    """Enum items over the staging folder, the variant path as the identifier.

    Cached on the module for the same reason the texture-set enum is: Blender does not keep a
    reference to the strings an items callback returns. The empty-staging sentinel is "NONE", not
    "", because Blender silently drops an empty identifier and then refuses to assign it.
    """
    global _STAGED_ITEMS
    items = []
    for i, path in enumerate(_staged_variants()):
        name = os.path.basename(path)
        items.append((path, name.replace("_", " "), f"Staged variant {name}", "", i + 1))
    _STAGED_ITEMS = items or [(STAGED_NONE, "(none staged)",
                               "Nothing is waiting for a decision", "", 0)]
    return _STAGED_ITEMS


STAGED_NONE = "NONE"
_STAGED_ITEMS = []


def _staged_pick(scn):
    """The selected staged variant's path, or "" when nothing is staged."""
    path = scn.gen_staged
    return path if path and path != STAGED_NONE else ""


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
    texture_set: EnumProperty(
        name="Texture Set", items=_texture_set_items,
        description="The texture set the Apply button samples into the active terrain layer (or "
                    "the surface material). Staged, not instant: assigning one rewires the "
                    "material's sampler nodes")
    gen_prompt: StringProperty(
        name="Prompt", default="cracked dry desert soil with small pebbles",
        description="What the ground is made of. ComfyUI generates a seamless albedo from this; "
                    "the top-view, flat-lighting part of the prompt is added for you")
    gen_seed: IntProperty(
        name="Seed", default=0, min=0,
        description="Same prompt and seed give the same texture, so a set can be reproduced")
    gen_variants: IntProperty(
        name="Variants", default=3, min=1, max=8,
        description="How many textures to generate, one seed apart, into the staging folder. "
                    "Texture generation is a pick-one-of-several loop, so Generate makes several "
                    "and nothing lands in the pack until you Accept one")
    gen_staged: EnumProperty(
        name="Variant", items=_staged_variant_items,
        description="The staged variant Accept writes into the generated pack. Reject deletes it")
    gen_reference: StringProperty(
        name="Reference", subtype="FILE_PATH", default="",
        description="Optional photo of the real surface. With one set, generation runs the "
                    "reference workflow instead: img2img from your photo with its palette locked, "
                    "so the result is that surface rather than the model's idea of the words")
    # The Live Environment toggle folded into the one World-panel master (bbt_world.live_env);
    # Shaders subscribes _apply_world to drive its S_EnvState feed (docs/CONVENTIONS.md, panel UX
    # conventions).


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
        # New never converts: New creates on an empty/new slot; it never silently converts. A slot
        # that already holds a plain material is turned into a BobShader with Convert (which keeps
        # its textures and is surfaced per-row in the panel), so New and Convert stay distinct
        # actions.
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
        shading.apply_surface_preset(node, self.preset)
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
        nxt = shading.terrain_add_layer(node)
        if nxt is None:
            self.report({"WARNING"}, f"All {mats.MAX_TERRAIN_LAYERS} layer slots are in use")
            return {"CANCELLED"}
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
        node = _terrain_node_active(context)
        if node is None:
            return {"CANCELLED"}
        shading.terrain_stack(node, self.preset)
        context.scene.bbt_shaders.terrain_active = 0
        self.report({"INFO"}, f"Applied {TERRAIN_STACK_PRESETS[self.preset]['label']} stack")
        return {"FINISHED"}


def _apply_texture_set(context, index, name):
    """Assign texture set `name` ("" clears it) to the editing material: a terrain layer slot
    (`index`) or a surface material (index < 0). Returns (report_label, error), so the Apply and
    the Generate operators share one assignment path instead of each carrying a copy.

    Context resolution only. The assignment itself is `shading.set_texture_set`, which the
    `apply_texture_set` op calls with an object and a material resolved by name instead."""
    mat = _editing_material(context)
    obj = _active_object(context)
    if obj is None and _materials().master_type(mat) == "terrain":
        return None, "No active mesh to read the terrain's drainage maps from"
    try:
        return shading.set_texture_set(obj, mat, index, name), None
    except ValueError as exc:
        return None, str(exc)


class BBT_OT_shaders_texture_set(Operator):
    bl_idname = "bob_blender_tools.shaders_texture_set"
    bl_label = "Apply Texture Set"
    bl_description = ("Sample the staged texture set into this layer's Albedo / Roughness / "
                      "Detail Height inputs, with its height driving a bump. Structural: it "
                      "rewires the material's sampler nodes")
    bl_options = {"REGISTER", "UNDO"}

    # The terrain layer slot, or -1 for a surface material (which has one set, not six).
    index: IntProperty(default=-1)

    def execute(self, context):
        name = context.scene.bbt_shaders.texture_set
        label, err = _apply_texture_set(context, self.index, "" if name == "NONE" else name)
        if err:
            self.report({"ERROR"}, err)
            return {"CANCELLED"}
        self.report({"INFO"}, label)
        return {"FINISHED"}


# Last known ComfyUI state, so the panel row can read "not connected" without an HTTP call in
# draw. Refreshed by Test Connection (the Advanced panel), by Generate, and by the job ticker;
# never in draw, because a socket call in a draw handler freezes the UI for the timeout in
# exactly the case the row exists to report.
_COMFY_STATE = {"ok": None, "detail": "not checked"}

# The staged-variant thumbnail. A preview collection rather than a bpy Image, so the panel can
# show the pick without adding a datablock the file then carries around.
_variant_previews = None
_variant_preview_key = None


def _variant_preview(path):
    """The icon id for a staged variant's basecolor, or 0 when there is nothing to show.

    0 is also what `--background` returns for a perfectly good preview (there is no icon manager
    without a UI), which is why the panel treats it as "draw no thumbnail" rather than an error.
    """
    global _variant_preview_key
    if _variant_previews is None or not path:
        return 0
    name = os.path.basename(path)
    png = os.path.join(path, f"{name}_basecolor.png")
    if not os.path.isfile(png):
        return 0
    if _variant_preview_key != png:
        _variant_previews.clear()
        try:
            _variant_previews.load(name, png, "IMAGE")
        except (KeyError, RuntimeError):
            return 0
        _variant_preview_key = png
    entry = _variant_previews.get(name)
    return entry.icon_id if entry else 0


def _redraw():
    """Tag every 3D-view side region, so a job finishing on the timer is visible without the
    artist having to move the mouse."""
    for window in bpy.context.window_manager.windows:
        for area in window.screen.areas:
            if area.type == "VIEW_3D":
                area.tag_redraw()


def _jobs():
    from ..core import comfy_jobs

    return comfy_jobs


def _comfy_job_running():
    return bool(_jobs().active())


def _job_row(layout):
    """Draw the running ComfyUI job's progress line and its Cancel, and say whether one is running.

    One implementation for both generation blocks in this panel: a caller draws its own button only
    when this returns False, so a second Generate cannot be pressed while the first is in flight.
    """
    running = _jobs().active()
    if not running:
        return False
    job = running[0]
    row = layout.row(align=True)
    row.label(text=f"{job.label} -- {job.progress or job.state} ({job.seconds:.0f}s)",
              icon="SORTTIME")
    row.operator("bob_blender_tools.comfy_cancel", text="", icon="X").job_id = job.id
    return True


def _submit(label, fn, on_done):
    """Queue a ComfyUI job on the shared worker and refresh the panel when it lands."""
    def done(job):
        if job.error is not None:
            _COMFY_STATE.update(ok=False, detail=str(job.error)[:90])
        else:
            on_done(job)
        _redraw()

    def progress(_job):
        _redraw()

    return _jobs().submit(label, fn, on_done=done, on_progress=progress)


class BBT_OT_shaders_generate_set(Operator):
    bl_idname = "bob_blender_tools.shaders_generate_set"
    bl_label = "Generate Variants"
    bl_description = ("Generate seamless texture variants from the prompt with ComfyUI into the "
                      "staging folder, then Accept the one you want. Runs in the background: the "
                      "viewport stays usable. Needs a local ComfyUI server; without one nothing "
                      "else changes")
    bl_options = {"REGISTER"}

    index: IntProperty(default=-1)

    def execute(self, context):
        from ..core import assets, comfy

        scn = context.scene.bbt_shaders
        prompt = (scn.gen_prompt or "").strip()
        if not prompt:
            self.report({"ERROR"}, "Describe the ground first (the Prompt field)")
            return {"CANCELLED"}
        pack = assets.generated_root()
        if not pack:
            self.report({"ERROR"}, "No generated pack folder (set an output folder in the "
                                   "add-on preferences)")
            return {"CANCELLED"}
        if _comfy_job_running():
            self.report({"WARNING"}, "A ComfyUI job is already running")
            return {"CANCELLED"}
        count = int(scn.gen_variants)
        seed = int(scn.gen_seed)
        reference = bpy.path.abspath(scn.gen_reference) if scn.gen_reference else None
        if reference and not os.path.isfile(reference):
            self.report({"ERROR"}, f"Reference image not found: {reference}")
            return {"CANCELLED"}

        # Everything below the closure runs on the worker thread: no bpy, no context, only the
        # values captured here (docs/GENERATION.md, Bob-side constraint 2).
        def work(job):
            def variant_done(i, total, info):
                job.report(f"variant {i}/{total}, seam {info['seam']['ratio']:.2f}")

            # note_prompt_id is what lets Cancel reach the server rather than only the registry.
            return comfy.texture_variants(prompt, pack, count=count, seed=seed,
                                          reference=reference, on_variant=variant_done,
                                          on_queued=job.note_prompt_id,
                                          on_progress=job.report)

        def landed(job):
            infos = job.result or []
            _COMFY_STATE.update(ok=True, detail=f"{len(infos)} variant(s) staged")
            items = _staged_variant_items(None, None)
            scn.gen_staged = infos[0]["dir"] if infos else items[0][0]

        _submit(f"texture x{count}: {prompt[:32]}", work, landed)
        self.report({"INFO"}, f"Generating {count} variant(s) in the background")
        return {"FINISHED"}


class BBT_OT_shaders_paint_stylised(Operator):
    bl_idname = "bob_blender_tools.shaders_paint_stylised"
    bl_label = "Paint Stylised"
    bl_description = ("Render a turntable of this object, restyle every view in ComfyUI under depth "
                      "and normal ControlNet, and project the result back into its own UVs. Every "
                      "surface is painted by a camera that could see it, which is what the "
                      "one-image texture route cannot do. Writes a colour map plus the derived set "
                      "and a Principled material; needs a local ComfyUI server")
    bl_options = {"REGISTER"}

    def execute(self, context):
        from ..core import assets, comfy, gen_paint, gen_views, render

        obj = context.active_object
        props = context.scene.bbt_stylise
        if obj is None or obj.type != "MESH":
            self.report({"ERROR"}, "Select a mesh object to paint")
            return {"CANCELLED"}
        if not obj.data.uv_layers:
            self.report({"ERROR"}, f"{obj.name} has no UV layer; this route paints into the charts "
                                   f"a mesh already has")
            return {"CANCELLED"}
        if _comfy_job_running():
            self.report({"WARNING"}, "A ComfyUI job is already running")
            return {"CANCELLED"}
        pack = assets.generated_root()
        if not pack:
            self.report({"ERROR"}, "No generated pack folder (set an output folder in the "
                                   "add-on preferences)")
            return {"CANCELLED"}

        prompt = (props.prompt or "").strip()
        stem = comfy.slugify(obj.name) or "painted"
        out_dir = gen_paint.paint_staging(pack, stem)
        size, seed = int(props.size), int(props.seed)
        lora = (props.lora or "").strip() or None
        denoise = float(props.denoise)

        # The route's three steps, split across the job boundary rather than run through
        # `gen_paint.paint_stylised`: the render and the projection touch bpy and must stay on the
        # main thread, and only the restyle in the middle may leave it (the threading rule).
        try:
            shot = gen_views.turntable_views(obj, os.path.join(out_dir, "views"),
                                             count=int(props.views), resolution=size,
                                             samples=int(props.samples), stem=stem)
        except Exception as exc:  # a render failure is a message, not a traceback in the console
            self.report({"ERROR"}, f"Turntable render failed: {exc}")
            return {"CANCELLED"}
        # Hand the card back before the restyle asks for it, for the reason measured in
        # `gen_paint.paint_stylised`: N EEVEE views then N SDXL jobs in one session is the
        # VRAM-handback rule's worst case, and the render's buffers are the half Bob controls.
        render.release_gpu()

        def work(job):
            return comfy.paint_views(shot, os.path.join(out_dir, "styled"), prompt, seed=seed,
                                     denoise=denoise, size=size, lora=lora,
                                     on_queued=job.note_prompt_id, on_progress=job.report)

        def landed(job):
            painted = job.result or {}
            out = gen_paint.paint_object(obj, shot, painted["images"], out_dir, stem, size=size)
            # `paint_receipt` stamps the object on its way out, so the panel below reads the same
            # figures whether this operator or the `paint_stylised` op produced them.
            receipt = gen_paint.paint_receipt(
                obj, out, prompt=prompt, seed=seed, lora=lora, pack_dir=pack,
                seconds={"restyle": painted.get("total_seconds", 0.0)})
            _COMFY_STATE.update(ok=True,
                                detail=f"painted {receipt['painted'] * 100:.0f}% of "
                                       f"{obj.name}'s charts from {receipt['views']} views")

        _submit(f"paint: {obj.name}", work, landed)
        self.report({"INFO"}, f"Rendered {len(shot)} views of {obj.name}; restyling in the "
                              f"background")
        return {"FINISHED"}


class BBT_OT_shaders_variant_accept(Operator):
    bl_idname = "bob_blender_tools.shaders_variant_accept"
    bl_label = "Accept"
    bl_description = ("Move the staged variant into the generated asset pack under a unique name "
                      "and sample it into this layer. Structural: it rewires the material's "
                      "sampler nodes")
    bl_options = {"REGISTER", "UNDO"}

    index: IntProperty(default=-1)

    def execute(self, context):
        from ..core import assets, comfy

        scn = context.scene.bbt_shaders
        pack = assets.generated_root()
        staged = _staged_pick(scn)
        if not pack or not staged:
            self.report({"ERROR"}, "Nothing staged to accept")
            return {"CANCELLED"}
        try:
            name = comfy.accept_variant(staged, pack)
        except (comfy.ComfyError, OSError) as exc:
            self.report({"ERROR"}, f"Accept failed: {exc}")
            return {"CANCELLED"}
        global _variant_preview_key
        _variant_preview_key = None
        # _texture_set_items rescans the packs on every call, so the accepted set is in the enum
        # by the time this assignment is validated.
        scn.texture_set = name
        label, err = _apply_texture_set(context, self.index, name)
        if err:
            self.report({"WARNING"}, f"Accepted {name} but could not apply it: {err}")
            return {"FINISHED"}
        self.report({"INFO"}, f"{label} (accepted as {name})")
        return {"FINISHED"}


class BBT_OT_shaders_variant_reject(Operator):
    bl_idname = "bob_blender_tools.shaders_variant_reject"
    bl_label = "Reject"
    bl_description = ("Delete the staged variant. Reject is a delete, so staging holds exactly "
                      "the results still waiting for a decision")
    bl_options = {"REGISTER"}

    all_of_them: bpy.props.BoolProperty(default=False)

    def execute(self, context):
        from ..core import comfy

        scn = context.scene.bbt_shaders
        targets = _staged_variants() if self.all_of_them else [_staged_pick(scn)]
        gone = sum(1 for t in targets if t and comfy.reject_variant(t))
        global _variant_preview_key
        _variant_preview_key = None
        scn.gen_staged = _staged_variant_items(None, None)[0][0]
        self.report({"INFO"}, f"Rejected {gone} variant(s)")
        return {"FINISHED"}


class BBT_OT_shaders_variant_upres(Operator):
    bl_idname = "bob_blender_tools.shaders_variant_upres"
    bl_label = "Upres 2x"
    bl_description = ("Upscale the staged variant to 2K through ComfyUI and re-derive its maps, "
                      "wrap-padded so the upscale does not put the seam back. Runs in the "
                      "background; the variant stays staged")
    bl_options = {"REGISTER"}

    def execute(self, context):
        from ..core import comfy

        staged = _staged_pick(context.scene.bbt_shaders)
        if not staged:
            self.report({"ERROR"}, "Nothing staged to upscale")
            return {"CANCELLED"}
        if _comfy_job_running():
            self.report({"WARNING"}, "A ComfyUI job is already running")
            return {"CANCELLED"}

        def work(job):
            job.report("upscaling")
            return comfy.upres_variant(staged, scale=2.0, on_queued=job.note_prompt_id,
                                       on_progress=job.report)

        def landed(job):
            global _variant_preview_key
            _variant_preview_key = None
            info = job.result or {}
            _COMFY_STATE.update(ok=True,
                                detail=f"upres to {info.get('size', '?')}, "
                                       f"seam {info.get('seam', {}).get('ratio', 0):.2f}")

        _submit(f"upres: {os.path.basename(staged)}", work, landed)
        self.report({"INFO"}, "Upscaling in the background")
        return {"FINISHED"}


class BBT_OT_shaders_texture_triplanar(Operator):
    bl_idname = "bob_blender_tools.shaders_texture_triplanar"
    bl_label = "Triplanar"
    bl_description = ("Toggle box (triplanar) projection for this material's texture sets. Off "
                      "is a top-down planar projection on terrain, and the mesh's own UVs on a "
                      "surface. Structural: projection is a property of each image node")
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        mats = _materials()
        mat = _editing_material(context)
        kind = mats.master_type(mat)
        count = mats.MAX_TERRAIN_LAYERS if kind == "terrain" else 1
        _sets, box = mats.stored_sets(mat, count)
        if kind == "terrain":
            obj = _active_object(context)
            if obj is None:
                self.report({"ERROR"}, "No active mesh")
                return {"CANCELLED"}
            shading.set_terrain_triplanar(obj, mat, not box)
        elif kind == "surface":
            shading.set_surface_triplanar(mat, not box)
        else:
            self.report({"ERROR"}, "Active material is not a surface or terrain BobShader")
            return {"CANCELLED"}
        self.report({"INFO"}, f"Triplanar {'off' if box else 'on'}")
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
        from ..core import assets

        spec = assets.biome_terrain(self.biome) if self.biome and self.biome != "NONE" else None
        if not spec:
            self.report({"ERROR"}, f"No terrain spec for biome '{self.biome}'")
            return {"CANCELLED"}
        mats = _materials()
        layers = spec["layers"]
        # Update the active terrain material in place if there is one, else a per-object M_<obj>.
        active = _active_material(context)
        name = active.name if mats.master_type(active) == "terrain" else obj.name
        try:
            mat, _node, _count = shading.build_terrain_material(
                obj, mat_name=name, layers=[L.get("layer") for L in layers])
        except ValueError as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
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
        had_coverage, _mod = shading.snow_shell_add(surface)
        if not had_coverage:
            self.report({"WARNING"}, "No snow_cover pass on this surface (add it in Atmosphere); "
                                     "the shell will read 0 until then")
        self.report({"INFO"}, f"Snow shell added on {surface.name}")
        return {"FINISHED"}


class BBT_OT_shaders_snow_shell_remove(Operator):
    bl_idname = "bob_blender_tools.shaders_snow_shell_remove"
    bl_label = "Remove Snow Shell"
    bl_description = "Remove the snow accumulation shell modifier"

    def execute(self, context):
        surface = _active_object(context)
        mod = util.nodes_mod(surface, SNOW_SHELL_MOD)
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


# The sampler's live knobs, on the S_TexSet instance for that slot. Tiling lives on the Mapping
# node and the bump strength on the shared Bump, so both are drawn separately below.
_TEXSET_KNOBS = ("AO Amount", "Roughness Amount", "Detail Height")


def _draw_texture_set(layout, context, mat, index=None):
    """The texture-set block, for a terrain layer slot (`index`) or a surface material
    (index None). A staged picker plus Apply, because assigning a set rewires the graph rather
    than setting a value (the staged_preset_row idiom), then the sampler's live knobs once
    a set is actually on. One implementation for both masters: they differ only in how many slots
    they have."""
    mats = _materials()
    scn = context.scene.bbt_shaders
    terrain = index is not None
    key = f"L{index}" if terrain else "S"
    sets, box = mats.stored_sets(mat, mats.MAX_TERRAIN_LAYERS if terrain else 1)
    current = sets[index if terrain else 0]

    layout.label(text=f"Texture Set: {current or '(none)'}", icon="TEXTURE")
    op = helpers.staged_preset_row(layout, scn, "texture_set",
                                   "bob_blender_tools.shaders_texture_set", text="Set",
                                   apply_text="Apply Texture Set",
                                   note="rebuilds: this material's sampler nodes")
    op.index = index if terrain else -1

    # Generate a set instead of picking one (docs/GENERATION.md, the texture family). Same slot,
    # same assignment path, so a generated set is a texture set like any other from here on.
    slot = index if terrain else -1
    gen = layout.column(align=True)
    gen.prop(scn, "gen_prompt", text="", icon="SHADERFX")
    gen.prop(scn, "gen_reference", text="", icon="IMAGE_REFERENCE")
    row = gen.row(align=True)
    row.prop(scn, "gen_seed", text="Seed")
    row.prop(scn, "gen_variants", text="x")
    if not _job_row(gen):
        gen.operator("bob_blender_tools.shaders_generate_set", text="Generate",
                     icon=helpers.STRUCTURAL_ICON).index = slot

    # Staged variants: pick one, look at it, Accept or Reject. Nothing reaches the pack (and so
    # nothing reaches the picker above) until Accept moves it there, which is the iteration rule.
    staged = _staged_variants()
    if staged:
        box = layout.box()
        box.label(text=f"Staged variants: {len(staged)}", icon="FILE_HIDDEN")
        icon_id = _variant_preview(_staged_pick(scn))
        if icon_id:
            box.template_icon(icon_value=icon_id, scale=6)
        box.prop(scn, "gen_staged", text="")
        row = box.row(align=True)
        row.operator("bob_blender_tools.shaders_variant_accept", text="Accept",
                     icon="CHECKMARK").index = slot
        row.operator("bob_blender_tools.shaders_variant_reject", text="Reject",
                     icon="TRASH").all_of_them = False
        row = box.row(align=True)
        row.operator("bob_blender_tools.shaders_variant_upres", icon="FULLSCREEN_ENTER")
        row.operator("bob_blender_tools.shaders_variant_reject", text="Reject All",
                     icon="TRASH").all_of_them = True

    cap = gen.row()
    cap.enabled = False
    cap.label(text=f"ComfyUI: {_COMFY_STATE['detail']}"
              if _COMFY_STATE["ok"] is not False else "ComfyUI: not connected",
              icon="INFO" if _COMFY_STATE["ok"] is not False else "ERROR")

    grp = mat.node_tree.nodes.get(mats.TEXSET_NODE_PREFIX + key)
    if grp is None:
        return
    col = layout.column(align=True)
    col.operator("bob_blender_tools.shaders_texture_triplanar", depress=box,
                 icon="MOD_UVPROJECT")
    mapping = mat.node_tree.nodes.get(mats.TEXSET_NODE_PREFIX + key + " Mapping")
    if mapping is not None:
        col.prop(mapping.inputs["Scale"], "default_value", text="Tiling")
    for nm in _TEXSET_KNOBS:
        sock = grp.inputs.get(nm)
        if sock is not None:
            col.prop(sock, "default_value", text=nm)
    # One bump per material (terrain blends every layer's detail height before it), so it is
    # drawn with the slot that is on screen rather than given a row of its own elsewhere.
    bump = mat.node_tree.nodes.get(mats.TEXSET_NODE_PREFIX + "Bump")
    if bump is not None:
        col.prop(bump.inputs["Strength"], "default_value", text="Bump Strength")


# Per-row slot status icons and labels by detected master type.
_MASTER_TAG = {"surface": ("MATERIAL", "Surface"), "terrain": ("MESH_GRID", "Terrain"),
               "water": ("MATFLUID", "Water")}


class BBT_PT_shaders(Panel):
    bl_label = "Shaders"
    bl_idname = "BBT_PT_shaders"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "BobBlenderTools"
    bl_order = 6  # pipeline stage: Shaders (docs/CONVENTIONS.md, panel UX conventions)
    bl_options = {"DEFAULT_CLOSED"}

    def draw(self, context):
        layout = self.layout
        mats = _materials()
        scn = context.scene.bbt_shaders
        obj = _active_object(context)

        # The context header, or the empty state: the context header, or the empty state that says
        # what to do next.
        if not helpers.context_header(layout, "Active mesh", obj.name if obj else None,
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
            helpers.select_row(row, "bob_blender_tools.shaders_select",
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
        # New never converts: no "Convert all" button here. Whole-object convert is now the "All
        # slots" option of the scope dropdown below, so Convert lives in exactly two places: per-row
        # (targeted) and the scope dropdown (all slots / selected / collection).

        # Adaptive action for the active slot: New when empty, else the editing header.
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
            helpers.select_row(row, "bob_blender_tools.shaders_select", m.name, m.name == sel,
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
        helpers.preset_row(layout, "bob_blender_tools.shaders_preset")
        _draw_inputs(layout, node, _SURFACE_KNOBS)
        # A scattered asset shades through its instanced collection, not its own slot, so the
        # tint/rough/variation above are all it exposes here.
        if _is_scatter_object(_active_object(context)):
            cap = layout.row()
            cap.enabled = False
            cap.label(text="scattered asset: above tints/modulates its look")
            return
        _draw_texture_set(layout, context, mat)
        # Macro break-up modulates the base albedo off a low-frequency world noise, so the solid
        # colour surface does not read as one flat sheet. Amount 0 = off.
        layout.label(text="Macro break-up", icon="MOD_NOISE")
        _draw_inputs(layout, node, _MACRO_KNOBS)


class BBT_PT_shaders_paint(Panel):
    bl_label = "Paint (stylised)"
    bl_idname = "BBT_PT_shaders_paint"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "BobBlenderTools"
    bl_parent_id = "BBT_PT_shaders"
    bl_options = {"DEFAULT_CLOSED"}

    @classmethod
    def poll(cls, context):
        obj = _active_object(context)
        return obj is not None and obj.type == "MESH" and bool(obj.data.uv_layers)

    def draw(self, context):
        layout = self.layout
        obj = _active_object(context)
        props = context.scene.bbt_stylise

        # What this object's paint WAS, read off the object rather than off a panel variable, so an
        # agent's paint shows here exactly as an artist's does (core/gen_paint.py, CONFIG_PROP).
        painted = getattr(obj, "bbt_paint", None)
        if painted is not None and painted.views:
            box = layout.box()
            box.label(text=f"Painted: {painted.painted * 100:.0f}% of charts from "
                           f"{painted.views} views", icon="BRUSH_DATA")
            row = box.row()
            row.enabled = False
            row.label(text=f"\"{painted.prompt}\" seed {painted.seed}"
                           + (f", LoRA {painted.lora}" if painted.lora else ""))
            for sentence in filter(None, painted.warnings.split("; ")):
                box.label(text=sentence, icon="ERROR")

        col = layout.column(align=True)
        col.prop(props, "prompt")
        row = col.row(align=True)
        row.prop(props, "denoise")
        row.prop(props, "views")
        row = col.row(align=True)
        row.prop(props, "size")
        row.prop(props, "seed")
        if not _job_row(col):
            col.operator("bob_blender_tools.shaders_paint_stylised",
                         icon=helpers.STRUCTURAL_ICON)
        cap = col.row()
        cap.enabled = False
        cap.label(text="renders a turntable, restyles every view, projects it back into these UVs")


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
        # Grouping: the root shows the depth/optics look (the default view). Flow+foam and Freeze
        # moved to DEFAULT_CLOSED child sub-panels below, the way Firmament splits its subsystems,
        # so the 23-knob wall is no longer one flat scroll.
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
        helpers.preset_row(row, "bob_blender_tools.shaders_terrain_stack_preset",
                              text="Stack Preset")
        if _has_biome_terrain():
            row.operator_menu_enum("bob_blender_tools.shaders_biome_terrain", "biome",
                                   text="Biome Terrain", icon=helpers.STRUCTURAL_ICON)
            cap = layout.row()
            cap.enabled = False
            cap.label(text="terrain layers only; the Biome panel builds the whole scene",
                      icon="INFO")
        _draw_inputs(layout, node, _TERRAIN_GLOBAL)

        # Layer slots (the adaptive-slots finding): draw only the ENABLED slots, not a fixed six-row
        # stack, so the box shows the depth actually in use. One disable model: the per-row checkbox
        # turns a layer off (it drops out of the list); Add Layer below is the sole add affordance.
        # The old Remove Layer button was a second way to do the checkbox's job, so it is gone.
        # Stacking is by Height Bias (not slot order), so no reorder is needed.
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
            helpers.select_row(row, "bob_blender_tools.shaders_select", f"Layer {i}",
                                  i == active, radio=False,
                                  op_props={"target": "layer", "index": i})
        if len(enabled) < maxn:
            box.operator("bob_blender_tools.shaders_terrain_add", icon="ADD")

        # Active layer: surface + a layer preset, then the placement masks.
        i = max(0, min(active, _materials().MAX_TERRAIN_LAYERS - 1))
        layout.label(text=f"Layer {i}", icon="NODE_TEXTURE")
        helpers.preset_row(layout, "bob_blender_tools.shaders_terrain_layer_preset",
                              text="Layer Preset")
        _draw_layer_inputs(layout, node, i, _LAYER_SURFACE)
        _draw_texture_set(layout, context, mat, index=i)


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
        layout.label(text="Altitude biases the env snow line; snow lies from there up")
        _draw_inputs(layout, node, _WEATHER_SNOW)
        layout.label(text="Wetness (rain/storm darken; env.wetness)", icon="MATFLUID")
        _draw_inputs(layout, node, _WEATHER_WET)
        layout.label(text="Frost (hoar: clear, calm, freezing; sparkly sheen on bare rock)", icon="FREEZE")
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
        shell = util.nodes_mod(surface, SNOW_SHELL_MOD)
        # Empty states: the shell reads the surface's snow_cover pass for its thickness. Say so
        # inline, before the Add press, rather than only in a post-click warning, so the dependency
        # is visible up front (the coverage pass is built in Atmosphere > Snow Coverage).
        if util.nodes_mod(surface, "BOB_Snow") is None:
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
    BBT_OT_shaders_texture_set,
    BBT_OT_shaders_generate_set,
    BBT_OT_shaders_paint_stylised,
    BBT_OT_shaders_variant_accept,
    BBT_OT_shaders_variant_reject,
    BBT_OT_shaders_variant_upres,
    BBT_OT_shaders_texture_triplanar,
    BBT_OT_shaders_biome_terrain,
    BBT_OT_shaders_snow_shell_add,
    BBT_OT_shaders_snow_shell_remove,
    BBT_PT_shaders,
    BBT_PT_shaders_surface,
    BBT_PT_shaders_paint,
    BBT_PT_shaders_water,
    BBT_PT_shaders_water_flow,
    BBT_PT_shaders_water_freeze,
    BBT_PT_shaders_terrain,
    BBT_PT_shaders_terrain_masks,
    BBT_PT_shaders_weather,
)


def register():
    global _env, _env_owned, _variant_previews
    from ..core import env, gen_paint
    _env = env
    _variant_previews = bpy.utils.previews.new()
    # Firmament owns the shared world; register it here only if running standalone (e.g. a
    # headless verify), and record ownership so unregister only removes what it created.
    if getattr(bpy.types.Scene, "bbt_env", None) is None:
        env.register()
        _env_owned = True
    for cls in CLASSES:
        bpy.utils.register_class(cls)
    bpy.types.Scene.bbt_shaders = bpy.props.PointerProperty(type=BBT_ShadersProps)
    gen_paint.register()  # the per-object paint record: core owns it, see its CONFIG_PROP
    # Subscribe the surface applier so the World master Live Environment toggle drives it.
    world.register_applier(_apply_world)


def unregister():
    global _env_owned, _variant_previews, _variant_preview_key
    from ..core import gen_paint

    if _variant_previews is not None:
        bpy.utils.previews.remove(_variant_previews)
        _variant_previews, _variant_preview_key = None, None
    world.unregister_applier(_apply_world)
    gen_paint.unregister()
    del bpy.types.Scene.bbt_shaders
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
    if _env_owned and _env is not None:
        _env.unregister()
        _env_owned = False
