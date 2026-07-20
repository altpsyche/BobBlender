"""World: the shared environment, promoted to its own top panel (docs/UX-REDESIGN.md 5.1).

The world (Scene.bbt_env) is read by Terrain, Scatter, Shaders, and Atmosphere, so it gets
the top slot in the pipeline instead of being buried in a Firmament sub-panel. This panel is
the single place to drive the world, which resolves the old Apply-Season confusion (several
overlapping ways to change the world across panels).

Two labelled groups make the live-vs-structural split visible (P3):
- World now: time/place and the live continuous conditions (weather, temperature, wetness,
  snow, cloud cover, wind). The conditions drive every consumer instantly via drivers.
- Set up a look: Season + Apply Season and Scene Presets, which are STRUCTURAL (they build or
  rebuild subsystems).

It also owns the scene-wide controls the plan moved here: Scene Quality (Preview/Final) and the
ONE Live Environment master toggle (folding the old per-panel Shaders and Firmament toggles).

Scaling model (the reason this is its own module, not a Firmament sub-panel): a subscriber
registry. Each consumer registers an applier fn(scene) that re-applies its response to the
current world state (drivers on/off, quality). A world control change calls every applier.
Adding a new world-driven subsystem later is one register_applier() call: World never imports
its consumers, so env.py stays the acyclic root and a polyrepo split stays mechanical. The
registry is addon-level (not bbmcp), so it survives a Reload Builders like the other UI state.
"""

import bpy
from bpy.props import BoolProperty, EnumProperty
from bpy.types import Operator, Panel, PropertyGroup

from . import server, ui_helpers

# The bbmcp.env module, held so the World panel can report when Firmament (the env owner) is off.
_env = None

# Subscriber registry: fn(scene) callables that re-apply a consumer's response to the current
# world state. Module-level so it survives Reload Builders (which only purges bbmcp).
_appliers = []


def register_applier(fn):
    """Subscribe a consumer's world applier fn(scene). Idempotent."""
    if fn not in _appliers:
        _appliers.append(fn)


def unregister_applier(fn):
    if fn in _appliers:
        _appliers.remove(fn)


def apply_all(scene):
    """Re-apply every subscribed consumer to the current world state. Called when a world
    control changes; a consumer that errors never blocks the others."""
    for fn in list(_appliers):
        try:
            fn(scene)
        except Exception as exc:  # a consumer's driver edit must not break the toggle
            print(f"[bob_blender_tools] world applier failed: {exc}")


def _on_world_change(self, context):
    apply_all(context.scene)


# Biome enums for the World panel: biomes carrying a world block (Biome World) and biomes with any
# applicable section (Apply Biome). Cached module-side with a stable id per biome (the enum-GC /
# reindex guard the other panels' dynamic enums use).
_BIOME_WORLD_ITEMS = [("NONE", "None", "No biome world", "", 0)]
_BIOME_WORLD_IDS = {"NONE": 0}
_BIOME_APPLY_ITEMS = [("NONE", "None", "No biome", "", 0)]
_BIOME_APPLY_IDS = {"NONE": 0}


def _assets():
    server._ensure_path()
    from bbmcp import assets

    return assets


def _biome_world_items(self, context):
    global _BIOME_WORLD_ITEMS
    a = _assets()
    items = []
    for n in a.list_biomes():
        if not a.biome_world(n):
            continue
        if n not in _BIOME_WORLD_IDS:
            _BIOME_WORLD_IDS[n] = len(_BIOME_WORLD_IDS)  # next unused id, fixed for this session
        items.append((n, n.replace("_", " ").title(), f"Set the world to the {n} biome", "",
                      _BIOME_WORLD_IDS[n]))
    _BIOME_WORLD_ITEMS = items or [("NONE", "None", "No biome carries a world block", "", 0)]
    return _BIOME_WORLD_ITEMS


def _biome_apply_items(self, context):
    global _BIOME_APPLY_ITEMS
    a = _assets()
    items = []
    for n in a.list_biomes():
        man = a.biome_manifest(n)
        if not (man["models"] or man["terrain"] or man["scatter"] or man["world"]):
            continue
        if n not in _BIOME_APPLY_IDS:
            _BIOME_APPLY_IDS[n] = len(_BIOME_APPLY_IDS)  # next unused id, fixed for this session
        items.append((n, n.replace("_", " ").title(), f"Stand up the {n} biome (all sections)", "",
                      _BIOME_APPLY_IDS[n]))
    _BIOME_APPLY_ITEMS = items or [("NONE", "None", "No biome in library/models", "", 0)]
    return _BIOME_APPLY_ITEMS


def _has_biome_world():
    a = _assets()
    return any(a.biome_world(n) for n in a.list_biomes())


def _has_any_biome():
    return bool(_assets().list_biomes())


class BBT_WorldProps(PropertyGroup):
    """World-level UI state: the scene-wide controls that drive every consumer. The world
    DATA is Scene.bbt_env (bbmcp/env.py); this is only the master toggles that sit above it."""

    live_env: BoolProperty(
        name="Live Environment", default=True, update=_on_world_change,
        description="Drive every consumer live from the world state: surface weather "
                    "(snow/wet/frost), atmosphere wind (clouds/fog/particles), and snow "
                    "coverage all follow the sliders below with no rebuild. The one master "
                    "toggle for the suite. Turn off to hand-tune each object")
    quality: EnumProperty(
        name="Quality", update=_on_world_change,
        items=[("preview", "Preview", "Coarse, fast; for the viewport and checks"),
               ("final", "Final", "Full quality for a render")],
        default="preview",
        description="Scene-wide render quality. Preview thins the particulates and lowers the "
                    "volume step/bounce counts for a fast viewport; Final restores them. "
                    "Applied live to every built subsystem, no rebuild")


class BBT_OT_world_biome_world(Operator):
    bl_idname = "bob_blender_tools.world_biome_world"
    bl_label = "Biome World"
    bl_description = ("Set the world to a biome's defaults: season, weather, time of day, cloud "
                      "cover, temperature, and wind from the biome's world block, then re-apply "
                      "every live consumer. Builds the sky so the sun moves to the set time")
    bl_options = {"REGISTER", "UNDO"}

    biome: EnumProperty(name="Biome", items=_biome_world_items)
    build_sky: BoolProperty(
        name="Build Sky", default=True,
        description="Rebuild the sky after setting the world, so the sun moves to the biome time")

    def execute(self, context):
        if not self.biome or self.biome == "NONE":
            self.report({"ERROR"}, "No biome carries a world block")
            return {"CANCELLED"}
        world = _assets().biome_world(self.biome)
        if not world:
            self.report({"ERROR"}, f"Biome '{self.biome}' has no world block")
            return {"CANCELLED"}
        env = context.scene.bbt_env
        applied = []
        for field, val in world.items():
            if not hasattr(env, field):
                continue
            try:
                setattr(env, field, val)
                applied.append(field)
            except (TypeError, ValueError):
                print(f"[bob_blender_tools] biome world: bad value for {field!r}: {val!r}")
        apply_all(context.scene)  # re-apply drivers/quality to the new world state
        built = ""
        if self.build_sky:
            try:
                bpy.ops.bob_blender_tools.firmament_build_sky()
                built = " + sky"
            except RuntimeError as exc:
                print(f"[bob_blender_tools] biome world: build sky skipped ({exc})")
        self.report({"INFO"}, f"Biome world {self.biome}: set {len(applied)} fields{built}")
        return {"FINISHED"}


def _apply_target(context):
    """The mesh a biome is applied to: the Scatter emitter if set, else the active mesh."""
    scn_scatter = getattr(context.scene, "bbt_scatter", None)
    emitter = getattr(scn_scatter, "emitter", None) if scn_scatter is not None else None
    if emitter is not None and emitter.type == "MESH":
        return emitter
    obj = context.active_object
    return obj if obj is not None and obj.type == "MESH" else None


class BBT_OT_world_apply_biome(Operator):
    bl_idname = "bob_blender_tools.world_apply_biome"
    bl_label = "Apply Biome"
    bl_description = ("Stand up a whole biome on one terrain mesh: import its assets, build its "
                      "terrain material, scatter its layers, and set the world - each section that "
                      "the manifest carries, in order. Uses the Scatter emitter as the terrain, or "
                      "the active mesh. One coherent scene from one pick")
    bl_options = {"REGISTER", "UNDO"}

    biome: EnumProperty(name="Biome", items=_biome_apply_items)
    weather_assets: BoolProperty(
        name="Weather scattered assets", default=True,
        description="Convert the scattered assets to BobShaders so they weather with the world and "
                    "their surface look is editable (select the scatter layer, then edit in "
                    "Shaders). Uncheck to keep the assets' native materials")

    def execute(self, context):
        if not self.biome or self.biome == "NONE":
            self.report({"ERROR"}, "No biome to apply")
            return {"CANCELLED"}
        target = _apply_target(context)
        if target is None:
            self.report({"ERROR"}, "Set a Scatter emitter or select a terrain mesh first")
            return {"CANCELLED"}
        a = _assets()
        man = a.biome_manifest(self.biome)
        warn = a.validate_biome(self.biome)
        # Scatter reads bbt_scatter.emitter; Biome Terrain shades the ACTIVE object.
        if getattr(context.scene, "bbt_scatter", None) is not None:
            context.scene.bbt_scatter.emitter = target

        def make_target_active():
            context.view_layer.objects.active = target
            try:
                target.select_set(True)
            except RuntimeError:
                pass

        steps = []
        if man["models"]:
            # Import first so the scatter instances real meshes. glTF import re-points the active
            # object, so re-assert the target as active before the terrain step below.
            bpy.ops.bob_blender_tools.scatter_import_biome(biome=self.biome)
            steps.append("assets")
        if man["terrain"]:
            make_target_active()
            bpy.ops.bob_blender_tools.shaders_biome_terrain(biome=self.biome)
            steps.append("terrain")
        if man["scatter"]:
            bpy.ops.bob_blender_tools.scatter_biome_scatter(biome=self.biome)
            steps.append("scatter")
            # Weather the scattered assets: convert each kind's BOB_Assets_<kind> to BobShaders
            # (shaders_convert Collection scope; idempotent, installs the env feed). Off skips it.
            if self.weather_assets:
                for kind in man["scatter"]:
                    coll = f"BOB_Assets_{kind.capitalize()}"
                    if bpy.data.collections.get(coll) is None:
                        continue
                    try:
                        bpy.ops.bob_blender_tools.shaders_convert(scope="collection", coll_name=coll)
                    except RuntimeError as exc:
                        print(f"[bob_blender_tools] apply biome: convert {coll} skipped ({exc})")
                steps.append("weathered assets")
        if man["world"]:
            bpy.ops.bob_blender_tools.world_biome_world(biome=self.biome)
            steps.append("world")
        msg = f"Applied {self.biome} on {target.name}: {', '.join(steps) or '(nothing to apply)'}"
        if warn:
            msg += f" ({len(warn)} manifest warnings, see console)"
            print("[bob_blender_tools] biome warnings:", warn)
        self.report({"INFO"}, msg)
        return {"FINISHED"}


class BBT_PT_world(Panel):
    bl_label = "World"
    bl_idname = "BBT_PT_world"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "BobBlenderTools"
    bl_order = 0  # top of the pipeline (docs/UX-REDESIGN.md section 4)

    def draw(self, context):
        layout = self.layout
        world = context.scene.bbt_world
        env = context.scene.bbt_env

        # One-line pipeline overview (decision E): the N-panel teaches the sequence.
        layout.label(text="World, then Terrain, Scatter, Shaders, Atmosphere", icon="INFO")

        # Scene-wide masters.
        row = layout.row(align=True)
        row.prop(world, "quality", expand=True)
        layout.prop(world, "live_env", icon="FORCE_WIND")
        if _env is None or _env.get_env(context.scene) is None:
            layout.label(text="Firmament off: world present but no atmosphere", icon="INFO")

        # -- World now: time/place (feeds the sun) + the live conditions --
        box = layout.box()
        box.label(text="World now", icon="WORLD")
        col = box.column(align=True)
        col.prop(env, "time_of_day")
        row = col.row(align=True)
        row.prop(env, "year")
        row.prop(env, "month")
        row.prop(env, "day")
        col.prop(env, "utc_offset")
        col = box.column(align=True)
        col.prop(env, "latitude")
        col.prop(env, "longitude")
        cap = box.row()
        cap.enabled = False
        cap.label(text="time and place feed the sun; Build Sky in Atmosphere to apply")

        col = box.column(align=True)
        col.label(text="Conditions (live)", icon="FORCE_WIND")
        col.prop(env, "weather")
        col.prop(env, "temperature")
        col.prop(env, "wetness")
        col.prop(env, "snow")
        col.prop(env, "cloud_cover")
        col.prop(env, "wind_direction")
        col.prop(env, "wind_strength")

        # -- Set up a look: structural season + presets --
        box = layout.box()
        box.label(text="Set up a look", icon=ui_helpers.STRUCTURAL_ICON)
        box.prop(env, "season")
        ui_helpers.structural_action(
            box, "bob_blender_tools.firmament_apply_season", text="Apply Season",
            note="sets snow/wetness/temperature; winter builds falling snow + coverage")
        ui_helpers.preset_row(box, "bob_blender_tools.firmament_scene_preset",
                              text="Scene Preset", icon="WORLD")
        cap = box.row()
        cap.enabled = False
        cap.label(text="a Scene Preset rebuilds the atmosphere subsystems")

        # Biome: set the world to a biome's defaults, or stand up the whole biome (terrain +
        # scatter + world) on the Scatter emitter / active mesh in one action (docs D5).
        if _has_any_biome():
            row = box.row(align=True)
            if _has_biome_world():
                row.operator_menu_enum("bob_blender_tools.world_biome_world", "biome",
                                       text="Biome World", icon="WORLD")
            row.operator_menu_enum("bob_blender_tools.world_apply_biome", "biome",
                                   text="Apply Biome", icon=ui_helpers.STRUCTURAL_ICON)
            cap = box.row()
            cap.enabled = False
            cap.label(text="Apply Biome imports assets, builds terrain + scatter, sets the world")


CLASSES = (
    BBT_WorldProps,
    BBT_OT_world_biome_world,
    BBT_OT_world_apply_biome,
    BBT_PT_world,
)


def register():
    global _env
    server._ensure_path()
    from bbmcp import env
    _env = env
    for cls in CLASSES:
        bpy.utils.register_class(cls)
    bpy.types.Scene.bbt_world = bpy.props.PointerProperty(type=BBT_WorldProps)


def unregister():
    del bpy.types.Scene.bbt_world
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
