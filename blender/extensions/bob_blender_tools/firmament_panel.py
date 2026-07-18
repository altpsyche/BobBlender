"""Firmament: the atmosphere panel, peer to Heightfield Terrain and Scatter.

S1 is Context and Sky. Two sub-panels:

- Environment: the shared world state (Scene.bbt_env), owned and registered by
  bbmcp/env.py. This is the UI other capabilities read the world from, so it lives
  here but the data does not: the panel just draws context.scene.bbt_env.
- Sky: Firmament's own sky knobs (Scene.bbt_firmament) and a Build Sky button that
  authors a physical sky and a matched Sun light from the world state, over the
  build_sky op in-process (no venv side, like Scatter).

Two homes, no drift: the shared world is bbt_env; Firmament's own UI/subsystem
state is bbt_firmament. Clouds, fog, and weather sub-panels arrive with S2 to S5.
"""

import bpy
from bpy.props import BoolProperty, EnumProperty, FloatProperty
from bpy.types import Operator, Panel, PropertyGroup

from . import server

# The bbmcp.env module, imported and registered at addon register time and held so
# unregister uses the same object even after Reload Builders purges bbmcp.
_env = None


def _apply(ops):
    """Run bbmcp ops in-process, the path the Scatter and terrain panels use."""
    server._ensure_path()
    from bbmcp.dispatch import apply_op

    return [apply_op(op) for op in ops]


class BBT_FirmamentProps(PropertyGroup):
    """Firmament's own UI and subsystem state (not the shared world)."""

    # Sun override on top of the geographic position.
    use_override: BoolProperty(
        name="Manual Sun", default=False,
        description="Set the sun angle by hand instead of from time and place")
    override_elevation: FloatProperty(
        name="Elevation", default=45.0, min=-90.0, max=90.0,
        description="Degrees above the horizon")
    override_azimuth: FloatProperty(
        name="Azimuth", default=180.0, min=0.0, max=360.0,
        description="Degrees clockwise from north")

    # Sun light.
    sun_strength: FloatProperty(name="Sun Strength", default=2.0, min=0.0)
    sun_angle: FloatProperty(
        name="Sun Size", default=0.545, min=0.0, max=20.0,
        description="Angular diameter in degrees; larger softens shadows")
    sun_disc: BoolProperty(
        name="Show Sun Disc", default=False,
        description="Draw the sky's sun disc. Off by default so the lamp lights "
                    "and the sun is not counted twice")

    # Nishita sky.
    world_strength: FloatProperty(name="Sky Strength", default=1.0, min=0.0)
    sky_altitude: FloatProperty(
        name="Altitude", default=200.0, min=0.0, max=60000.0,
        description="Observer altitude in metres")
    air: FloatProperty(name="Air", default=1.0, min=0.0, max=10.0)
    ozone: FloatProperty(name="Ozone", default=1.0, min=0.0, max=10.0)
    turbidity: FloatProperty(
        name="Turbidity", default=2.2, min=1.0, max=10.0,
        description="Atmospheric haziness (the 5.2 sky's dust replacement)")
    ground_albedo: FloatProperty(name="Ground Albedo", default=0.3, min=0.0, max=1.0)

    quality: EnumProperty(
        name="Quality",
        items=[("preview", "Preview", "Coarse, fast; for the viewport and checks"),
               ("final", "Final", "Full quality for a render")],
        default="preview")


class BBT_OT_firmament_build_sky(Operator):
    bl_idname = "bob_blender_tools.firmament_build_sky"
    bl_label = "Build Sky"
    bl_description = "Author the Nishita sky, Sun light, and world haze from the world state"

    def execute(self, context):
        env = context.scene.bbt_env
        fm = context.scene.bbt_firmament
        params = {
            "time_of_day": env.time_of_day,
            "year": env.year, "month": env.month, "day": env.day,
            "utc_offset": env.utc_offset,
            "latitude": env.latitude, "longitude": env.longitude,
            "use_override": fm.use_override,
            "sun_elevation": fm.override_elevation,
            "sun_azimuth": fm.override_azimuth,
            "sun_strength": fm.sun_strength,
            "sun_angle": fm.sun_angle,
            "sun_disc": fm.sun_disc,
            "world_strength": fm.world_strength,
            "altitude": fm.sky_altitude,
            "air": fm.air, "ozone": fm.ozone,
            "turbidity": fm.turbidity, "ground_albedo": fm.ground_albedo,
        }
        res = _apply([{"op": "build_sky", "params": params}])
        self.report({"INFO"}, f"Sky: {res[0].get('info', '')}")
        return {"FINISHED"}


class BBT_PT_firmament(Panel):
    bl_label = "Firmament"
    bl_idname = "BBT_PT_firmament"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "BobBlenderTools"
    bl_options = {"DEFAULT_CLOSED"}

    def draw(self, context):
        layout = self.layout
        layout.prop(context.scene.bbt_firmament, "quality", expand=True)
        layout.operator("bob_blender_tools.firmament_build_sky", icon="LIGHT_SUN")


class BBT_PT_firmament_env(Panel):
    bl_label = "Environment"
    bl_idname = "BBT_PT_firmament_env"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "BobBlenderTools"
    bl_parent_id = "BBT_PT_firmament"

    def draw(self, context):
        env = context.scene.bbt_env
        layout = self.layout

        col = layout.column(align=True)
        col.prop(env, "time_of_day")
        row = col.row(align=True)
        row.prop(env, "year")
        row.prop(env, "month")
        row.prop(env, "day")
        col.prop(env, "utc_offset")

        col = layout.column(align=True)
        col.prop(env, "latitude")
        col.prop(env, "longitude")

        col = layout.column(align=True)
        col.prop(env, "season")
        col.prop(env, "weather")

        col = layout.column(align=True)
        col.prop(env, "temperature")
        col.prop(env, "wetness")
        col.prop(env, "snow")
        col.prop(env, "cloud_cover")

        col = layout.column(align=True)
        col.prop(env, "wind_direction")
        col.prop(env, "wind_strength")


class BBT_PT_firmament_sky(Panel):
    bl_label = "Sky"
    bl_idname = "BBT_PT_firmament_sky"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "BobBlenderTools"
    bl_parent_id = "BBT_PT_firmament"

    def draw(self, context):
        fm = context.scene.bbt_firmament
        layout = self.layout

        layout.prop(fm, "use_override")
        if fm.use_override:
            col = layout.column(align=True)
            col.prop(fm, "override_elevation")
            col.prop(fm, "override_azimuth")

        col = layout.column(align=True)
        col.prop(fm, "sun_strength")
        col.prop(fm, "sun_angle")
        col.prop(fm, "sun_disc")

        col = layout.column(align=True)
        col.prop(fm, "world_strength")
        col.prop(fm, "sky_altitude")
        col.prop(fm, "air")
        col.prop(fm, "ozone")
        col.prop(fm, "turbidity")
        col.prop(fm, "ground_albedo")

        layout.operator("bob_blender_tools.firmament_build_sky", icon="LIGHT_SUN")


CLASSES = (
    BBT_FirmamentProps,
    BBT_OT_firmament_build_sky,
    BBT_PT_firmament,
    BBT_PT_firmament_env,
    BBT_PT_firmament_sky,
)


def register():
    global _env
    server._ensure_path()
    from bbmcp import env
    _env = env
    env.register()  # BobFirmament owns and registers the shared world state
    for cls in CLASSES:
        bpy.utils.register_class(cls)
    bpy.types.Scene.bbt_firmament = bpy.props.PointerProperty(type=BBT_FirmamentProps)


def unregister():
    del bpy.types.Scene.bbt_firmament
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
    if _env is not None:
        _env.unregister()
