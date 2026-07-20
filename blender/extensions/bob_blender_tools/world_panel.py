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
from bpy.types import Panel, PropertyGroup

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


CLASSES = (
    BBT_WorldProps,
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
