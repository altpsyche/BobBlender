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
from bpy.props import (
    BoolProperty,
    EnumProperty,
    FloatProperty,
    PointerProperty,
    StringProperty,
)
from bpy.types import Operator, Panel, PropertyGroup

from . import server

# The bbmcp.env module, imported and registered at addon register time and held so
# unregister uses the same object even after Reload Builders purges bbmcp.
_env = None

# Live cloud knobs, grouped for the panel (drawn from the modifier after a Build).
_CLOUD_SHAPE = ["Coverage", "Cloud Scale", "Warp"]
_CLOUD_SHAPE2 = ["Detail", "Softness", "Density"]
_CLOUD_LAYER = ["Layer Size", "Thickness", "Height"]
_CLOUD_WIND = ["Wind Direction", "Wind Speed"]

# Live fog knobs, grouped for the panel.
_FOG_SHAPE = ["Density", "Fog Noise", "Fog Scale", "Softness", "Warp"]
_FOG_LOOK = ["Fog Color", "Anisotropy"]
_FOG_LAYER = ["Layer Size", "Thickness", "Height", "Fog Top", "Falloff"]
_FOG_GROUND = ["Terrain Size", "Terrain Height", "Sea Level", "Ground Thickness"]
_FOG_WIND = ["Wind Direction", "Wind Speed"]

# Cloud-type presets: a Blender-side dict (no codegen; nothing else reads it). Each
# sets the live modifier knobs by socket name for a named sky look.
CLOUD_PRESETS = {
    "clear": {"label": "Clear", "desc": "A few thin wisps",
              "knobs": {"Coverage": 0.12, "Cloud Scale": 0.022, "Density": 7.0,
                        "Detail": 5.0, "Softness": 0.30, "Warp": 0.4, "Thickness": 35.0}},
    "scattered": {"label": "Scattered", "desc": "Fair-weather clouds with gaps",
                  "knobs": {"Coverage": 0.30, "Cloud Scale": 0.020, "Density": 9.0,
                            "Detail": 6.0, "Softness": 0.20, "Warp": 0.5, "Thickness": 42.0}},
    "cumulus": {"label": "Cumulus", "desc": "Big billowing clouds",
                "knobs": {"Coverage": 0.42, "Cloud Scale": 0.014, "Density": 11.0,
                          "Detail": 7.0, "Softness": 0.16, "Warp": 0.6, "Thickness": 60.0}},
    "overcast": {"label": "Overcast", "desc": "Full grey blanket",
                 "knobs": {"Coverage": 0.80, "Cloud Scale": 0.030, "Density": 8.0,
                           "Detail": 5.0, "Softness": 0.32, "Warp": 0.35, "Thickness": 45.0}},
    "storm": {"label": "Storm", "desc": "Dark, heavy, deep",
              "knobs": {"Coverage": 0.90, "Cloud Scale": 0.012, "Density": 15.0,
                        "Detail": 7.0, "Softness": 0.22, "Warp": 0.55, "Thickness": 90.0}},
}

# Fog-type presets (live modifier knobs, set within whichever fog mode was built).
FOG_PRESETS = {
    "ground_mist": {"label": "Ground Mist", "desc": "Thin, near-uniform low mist",
                    "knobs": {"Density": 1.0, "Fog Noise": 0.10, "Fog Scale": 0.04,
                              "Softness": 0.40, "Fog Top": 0.50, "Thickness": 25.0,
                              "Height": 12.0, "Warp": 0.30, "Falloff": 2.0,
                              "Anisotropy": 0.5}},
    "valley": {"label": "Valley Fog", "desc": "Denser fog that fills the low ground",
               "knobs": {"Density": 2.5, "Fog Noise": 0.20, "Fog Scale": 0.03,
                         "Softness": 0.40, "Fog Top": 0.70, "Thickness": 50.0,
                         "Height": 22.0, "Warp": 0.40, "Falloff": 1.6,
                         "Anisotropy": 0.5}},
    "banks": {"label": "Fog Banks", "desc": "Patchy drifting banks",
              "knobs": {"Density": 3.0, "Fog Noise": 0.85, "Fog Scale": 0.025,
                        "Softness": 0.30, "Fog Top": 0.60, "Thickness": 45.0,
                        "Height": 25.0, "Warp": 0.50, "Falloff": 1.2,
                        "Anisotropy": 0.4}},
    "thick": {"label": "Thick Fog", "desc": "Dense soup, low visibility",
              "knobs": {"Density": 6.0, "Fog Noise": 0.50, "Fog Scale": 0.02,
                        "Softness": 0.45, "Fog Top": 0.85, "Thickness": 70.0,
                        "Height": 30.0, "Warp": 0.35, "Falloff": 1.0,
                        "Anisotropy": 0.3}},
}

# Live weather knobs, grouped for the panel.
_RAIN_KNOBS = ["Count", "Fall Speed", "Streak Length", "Size", "Size Variation"]
_RAIN_WIND = ["Wind Direction", "Wind Speed", "Drift"]
_MOTE_KNOBS = ["Count", "Fall Speed", "Drift", "Turbulence", "Size", "Size Variation"]
_MOTE_LOOK = ["Color", "Emission"]
_MOTE_WIND = ["Wind Direction", "Wind Speed"]
_DOMAIN_KNOBS = ["Domain Size", "Domain Height"]
_SNOW_KNOBS = ["Snow", "Slope Threshold", "Slope Falloff", "Altitude",
               "Altitude Falloff", "Occlusion", "Occlusion Distance"]

# Rain presets (streak mode): live knobs set by socket name.
RAIN_PRESETS = {
    "drizzle": {"label": "Drizzle", "desc": "Light, fine rain",
                "knobs": {"Count": 800, "Fall Speed": 6.0, "Streak Length": 0.13,
                          "Size": 0.005, "Size Variation": 0.4}},
    "rain": {"label": "Rain", "desc": "Steady rainfall",
             "knobs": {"Count": 2500, "Fall Speed": 9.0, "Streak Length": 0.2,
                       "Size": 0.008, "Size Variation": 0.4}},
    "downpour": {"label": "Downpour", "desc": "Heavy, fast, long streaks",
                 "knobs": {"Count": 5000, "Fall Speed": 13.0, "Streak Length": 0.3,
                           "Size": 0.012, "Size Variation": 0.5}},
}

# Mote presets (mote mode): dust, amber motes, and falling snow are the same mode,
# a preset picks the look. Falling snow is the plan's snow mote preset.
MOTE_PRESETS = {
    "dust": {"label": "Dust", "desc": "Tan dust, denser and wind-driven",
             "knobs": {"Color": (0.60, 0.50, 0.35, 1.0), "Count": 1500,
                       "Fall Speed": 0.3, "Drift": 1.5, "Turbulence": 1.2,
                       "Size": 0.03, "Emission": 0.0}},
    "amber": {"label": "Amber Motes", "desc": "Fine sun-lit golden-hour specks",
              "knobs": {"Color": (0.90, 0.60, 0.25, 1.0), "Count": 700,
                        "Fall Speed": 0.15, "Drift": 0.6, "Turbulence": 0.8,
                        "Size": 0.02, "Emission": 0.0}},
    "snow": {"label": "Falling Snow", "desc": "White, slow, fluttering flakes",
             "knobs": {"Color": (1.0, 1.0, 1.0, 1.0), "Count": 2000,
                       "Fall Speed": 0.6, "Drift": 1.0, "Turbulence": 1.5,
                       "Size": 0.035, "Emission": 0.0}},
}


def _apply(ops):
    """Run bbmcp ops in-process, the path the Scatter and terrain panels use."""
    server._ensure_path()
    from bbmcp.dispatch import apply_op

    return [apply_op(op) for op in ops]


def _nodes_mod(obj):
    if obj is None:
        return None
    return next((m for m in obj.modifiers if m.type == "NODES"), None)


def _input(obj, socket_name):
    """The live modifier input struct for a socket name, or None."""
    mod = _nodes_mod(obj)
    if mod is None or mod.node_group is None:
        return None
    ident = next((it.identifier for it in mod.node_group.interface.items_tree
                  if getattr(it, "item_type", None) == "SOCKET"
                  and it.in_out == "INPUT" and it.name == socket_name), None)
    return getattr(mod.properties.inputs, ident, None) if ident else None


def _draw_knobs(layout, obj, names):
    """Draw each present modifier input by socket name (live, no rebuild)."""
    mod = _nodes_mod(obj)
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


def _named_mod(obj, mod_name):
    """A specific NODES modifier by name (an object may carry more than one, e.g. a
    terrain with both its terrain modifier and the snow-coverage pass)."""
    if obj is None:
        return None
    return next((m for m in obj.modifiers if m.type == "NODES" and m.name == mod_name), None)


def _input_of(mod, socket_name):
    if mod is None or mod.node_group is None:
        return None
    ident = next((it.identifier for it in mod.node_group.interface.items_tree
                  if getattr(it, "item_type", None) == "SOCKET"
                  and it.in_out == "INPUT" and it.name == socket_name), None)
    return getattr(mod.properties.inputs, ident, None) if ident else None


def _draw_knobs_mod(layout, mod, names):
    """Draw each present input of a specific modifier by socket name (live)."""
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


def _show_domain_gizmo(obj):
    """Draw the domain as a wireframe box in the viewport, not a solid box.

    WIRE, not BOUNDS: the box is generated by the GN modifier, so the object's
    bounding box (what BOUNDS draws) comes from the empty base mesh and is not true
    to the volume size. WIRE draws the evaluated geometry, so the wireframe matches
    the actual domain. display_type is viewport-only, so the render is untouched;
    the material has no surface output, so the box was never visible in a render
    anyway. Like an Unreal volume's bounds.
    """
    if obj is not None:
        obj.display_type = "WIRE"


def _set_volume_quality(scene, quality):
    """Cycles volume settings from the quality level (cost-spike defaults). Volume
    bounces (multiple scattering) are kept low: 0 for preview keeps self-shadowing
    single-scatter and cheap, a couple for final let light re-scatter so shadowed
    cloud reads bright instead of muddy."""
    cy = getattr(scene, "cycles", None)
    if cy is None:
        return
    if quality == "final":
        cy.volume_step_rate, cy.volume_max_steps, cy.volume_bounces = 1.0, 512, 2
    else:
        cy.volume_step_rate, cy.volume_max_steps, cy.volume_bounces = 2.0, 256, 0


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

    # Clouds: the cloud layer is one domain box; its knobs live on the modifier.
    cloud_object: StringProperty(name="Object", default="BOB_Clouds")
    cloud_shadows: BoolProperty(
        name="Cloud Shadows", default=True,
        description="Clouds self-shadow (dimensional form) and cast shadows on the "
                    "scene. On by default and cheap at normal sun angles, since a "
                    "high sun's shadow rays only cross the layer thickness. Turn off "
                    "for a flat, faster look when the sun is low and the frame is "
                    "Final quality, where near-horizontal shadow rays get expensive")

    # Fog: a bounded domain slab, same one-box pattern as clouds.
    fog_object: StringProperty(name="Object", default="BOB_Fog")
    fog_mode: EnumProperty(
        name="Fog Mode",
        items=[("height_fog", "Height Fog",
                "A near-uniform slab, densest low, thinning with height. Aerial "
                "perspective; valleys fill and hills poke out"),
               ("noise_fog", "Noise Fog",
                "The slab broken into soft, patchy, drifting banks"),
               ("ground_fog", "Ground Fog",
                "Mist that hugs the terrain surface, sampled from a heightmap, so it "
                "follows hills up and over instead of sitting at a fixed height")],
        default="height_fog")
    fog_heightmap: StringProperty(
        name="Heightmap", default="", subtype="FILE_PATH",
        description="Terrain heightmap the ground fog drapes over. Match Terrain "
                    "Size/Height/Sea Level to the heightmap_terrain build that used it")

    # Weather (S4): rain streaks and dust/amber/snow motes in a camera-following
    # domain, plus the snow-coverage pass that writes snow_cover on the terrain.
    weather_camera: PointerProperty(
        name="Camera", type=bpy.types.Object,
        poll=lambda self, obj: obj.type == "CAMERA",
        description="The particle domain re-tiles around this camera so the weather "
                    "is always around the shot. Leave empty to anchor it at the origin")
    use_motion_blur: BoolProperty(
        name="Motion Blur", default=True,
        description="Enable scene motion blur so fast particles read as streaks in "
                    "the render. Rain streaks are also real geometry, so they read "
                    "with or without it")
    rain_object: StringProperty(name="Object", default="BOB_Rain")
    mote_object: StringProperty(name="Object", default="BOB_Motes")
    snow_surface: PointerProperty(
        name="Surface", type=bpy.types.Object,
        poll=lambda self, obj: obj.type == "MESH",
        description="The terrain the snow-coverage pass writes snow_cover onto (BobShaders "
                    "reads that attribute). Defaults to the active mesh")


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


class BBT_OT_firmament_build_clouds(Operator):
    bl_idname = "bob_blender_tools.firmament_build_clouds"
    bl_label = "Build Clouds"
    bl_description = "Build the procedural volumetric cloud layer"

    def execute(self, context):
        fm = context.scene.bbt_firmament
        env = getattr(context.scene, "bbt_env", None)
        params = {"mode": "clouds"}
        if env is not None:  # seed the wind knobs from the shared world state
            params["wind_direction"] = env.wind_direction
            params["wind_speed"] = env.wind_strength
        _apply([{"op": "build_geonodes", "recipe": "volumetrics",
                 "name": fm.cloud_object, "params": params}])
        _set_volume_quality(context.scene, fm.quality)
        n = 0
        obj = bpy.data.objects.get(fm.cloud_object)
        _show_domain_gizmo(obj)
        if obj is not None:
            # Shadow fork (Phase-0 / S2 cost): a cloud volume that casts shadows
            # makes every lit point march a shadow ray through it, which is the
            # expensive path. Default off (lit, no volumetric shadow); on for heroes.
            obj.visible_shadow = fm.cloud_shadows
            dg = context.evaluated_depsgraph_get()
            n = sum(1 for i in dg.object_instances if i.is_instance
                    and i.parent is not None and i.parent.original.name == obj.name)
        self.report({"INFO"}, f"Clouds: {n} puffs")
        return {"FINISHED"}


class BBT_OT_firmament_cloud_seed(Operator):
    bl_idname = "bob_blender_tools.firmament_cloud_seed"
    bl_label = "Randomize Cloud Seed"
    bl_description = "Reshuffle the cloud pattern with a new seed"

    def execute(self, context):
        import random

        obj = bpy.data.objects.get(context.scene.bbt_firmament.cloud_object)
        seed = _input(obj, "Cloud Seed")
        if seed is None:
            return {"CANCELLED"}
        seed.value = random.randint(0, 99999)
        obj.update_tag()
        return {"FINISHED"}


class BBT_OT_firmament_cloud_preset(Operator):
    bl_idname = "bob_blender_tools.firmament_cloud_preset"
    bl_label = "Cloud Preset"
    bl_description = "Set the cloud look from a named preset"
    bl_options = {"REGISTER", "UNDO"}

    preset: EnumProperty(
        name="Preset",
        items=[(k, v["label"], v["desc"]) for k, v in CLOUD_PRESETS.items()])

    def execute(self, context):
        fm = context.scene.bbt_firmament
        obj = bpy.data.objects.get(fm.cloud_object)
        if obj is None or _nodes_mod(obj) is None:
            bpy.ops.bob_blender_tools.firmament_build_clouds()
            obj = bpy.data.objects.get(fm.cloud_object)
        if obj is None:
            return {"CANCELLED"}
        for name, val in CLOUD_PRESETS[self.preset]["knobs"].items():
            inp = _input(obj, name)
            if inp is not None:
                inp.value = val
        obj.update_tag()
        self.report({"INFO"}, f"Applied {CLOUD_PRESETS[self.preset]['label']} preset")
        return {"FINISHED"}


class BBT_OT_firmament_cloud_wind_from_env(Operator):
    bl_idname = "bob_blender_tools.firmament_cloud_wind_from_env"
    bl_label = "Use Env Wind"
    bl_description = "Copy the Environment wind direction and strength onto the clouds"

    def execute(self, context):
        env = getattr(context.scene, "bbt_env", None)
        obj = bpy.data.objects.get(context.scene.bbt_firmament.cloud_object)
        wdir, wspd = _input(obj, "Wind Direction"), _input(obj, "Wind Speed")
        if env is None or wdir is None or wspd is None:
            return {"CANCELLED"}
        wdir.value = env.wind_direction
        wspd.value = env.wind_strength
        obj.update_tag()
        self.report({"INFO"}, "Cloud wind synced from Environment")
        return {"FINISHED"}


class BBT_OT_firmament_build_fog(Operator):
    bl_idname = "bob_blender_tools.firmament_build_fog"
    bl_label = "Build Fog"
    bl_description = "Build a procedural volumetric fog domain"

    def execute(self, context):
        fm = context.scene.bbt_firmament
        env = getattr(context.scene, "bbt_env", None)
        params = {"mode": fm.fog_mode}
        if env is not None:  # seed the wind knobs from the shared world state
            params["wind_direction"] = env.wind_direction
            params["wind_speed"] = env.wind_strength
        if fm.fog_mode == "ground_fog" and fm.fog_heightmap:
            params["heightmap"] = bpy.path.abspath(fm.fog_heightmap)
        _apply([{"op": "build_geonodes", "recipe": "volumetrics",
                 "name": fm.fog_object, "params": params}])
        _set_volume_quality(context.scene, fm.quality)
        _show_domain_gizmo(bpy.data.objects.get(fm.fog_object))
        label = {"height_fog": "Height Fog", "noise_fog": "Noise Fog",
                 "ground_fog": "Ground Fog"}[fm.fog_mode]
        self.report({"INFO"}, f"Fog: built {label}")
        return {"FINISHED"}


class BBT_OT_firmament_fog_seed(Operator):
    bl_idname = "bob_blender_tools.firmament_fog_seed"
    bl_label = "Randomize Fog Seed"
    bl_description = "Reshuffle the fog pattern with a new seed"

    def execute(self, context):
        import random

        obj = bpy.data.objects.get(context.scene.bbt_firmament.fog_object)
        seed = _input(obj, "Fog Seed")
        if seed is None:
            return {"CANCELLED"}
        seed.value = random.randint(0, 99999)
        obj.update_tag()
        return {"FINISHED"}


class BBT_OT_firmament_fog_preset(Operator):
    bl_idname = "bob_blender_tools.firmament_fog_preset"
    bl_label = "Fog Preset"
    bl_description = "Set the fog look from a named preset"
    bl_options = {"REGISTER", "UNDO"}

    preset: EnumProperty(
        name="Preset",
        items=[(k, v["label"], v["desc"]) for k, v in FOG_PRESETS.items()])

    def execute(self, context):
        fm = context.scene.bbt_firmament
        obj = bpy.data.objects.get(fm.fog_object)
        if obj is None or _nodes_mod(obj) is None:
            bpy.ops.bob_blender_tools.firmament_build_fog()
            obj = bpy.data.objects.get(fm.fog_object)
        if obj is None:
            return {"CANCELLED"}
        for name, val in FOG_PRESETS[self.preset]["knobs"].items():
            inp = _input(obj, name)
            if inp is not None:
                inp.value = val
        obj.update_tag()
        self.report({"INFO"}, f"Applied {FOG_PRESETS[self.preset]['label']} preset")
        return {"FINISHED"}


class BBT_OT_firmament_fog_wind_from_env(Operator):
    bl_idname = "bob_blender_tools.firmament_fog_wind_from_env"
    bl_label = "Use Env Wind"
    bl_description = "Copy the Environment wind direction and strength onto the fog"

    def execute(self, context):
        env = getattr(context.scene, "bbt_env", None)
        obj = bpy.data.objects.get(context.scene.bbt_firmament.fog_object)
        wdir, wspd = _input(obj, "Wind Direction"), _input(obj, "Wind Speed")
        if env is None or wdir is None or wspd is None:
            return {"CANCELLED"}
        wdir.value = env.wind_direction
        wspd.value = env.wind_strength
        obj.update_tag()
        self.report({"INFO"}, "Fog wind synced from Environment")
        return {"FINISHED"}


def _build_particulate(context, obj_name, mode, extra=None):
    """Build a particulates object (streak or mote), seeding wind and the follow
    camera from the panel and the shared world state, and turning on motion blur."""
    fm = context.scene.bbt_firmament
    env = getattr(context.scene, "bbt_env", None)
    params = {"mode": mode}
    if fm.weather_camera is not None:
        params["camera"] = fm.weather_camera.name
    if env is not None:  # seed the wind knobs from the shared world state
        params["wind_direction"] = env.wind_direction
        params["wind_speed"] = env.wind_strength
    if extra:
        params.update(extra)
    _apply([{"op": "build_geonodes", "recipe": "particulates",
             "name": obj_name, "params": params}])
    if fm.use_motion_blur:
        context.scene.render.use_motion_blur = True
    return bpy.data.objects.get(obj_name)


def _count_instances(context, obj):
    dg = context.evaluated_depsgraph_get()
    return sum(1 for i in dg.object_instances if i.is_instance
               and i.parent is not None and i.parent.original.name == obj.name)


class BBT_OT_firmament_build_rain(Operator):
    bl_idname = "bob_blender_tools.firmament_build_rain"
    bl_label = "Build Rain"
    bl_description = "Build the rain streak particle system in a camera-following domain"

    def execute(self, context):
        fm = context.scene.bbt_firmament
        obj = _build_particulate(context, fm.rain_object, "streak")
        n = _count_instances(context, obj) if obj is not None else 0
        self.report({"INFO"}, f"Rain: {n} streaks")
        return {"FINISHED"}


class BBT_OT_firmament_build_motes(Operator):
    bl_idname = "bob_blender_tools.firmament_build_motes"
    bl_label = "Build Motes"
    bl_description = "Build the dust / amber / snow mote particle system (pick a preset)"

    def execute(self, context):
        fm = context.scene.bbt_firmament
        obj = _build_particulate(context, fm.mote_object, "mote")
        n = _count_instances(context, obj) if obj is not None else 0
        self.report({"INFO"}, f"Motes: {n} motes")
        return {"FINISHED"}


class BBT_OT_firmament_particulate_seed(Operator):
    bl_idname = "bob_blender_tools.firmament_particulate_seed"
    bl_label = "Randomize Seed"
    bl_description = "Reshuffle the particle pattern with a new seed"

    object_name: StringProperty()

    def execute(self, context):
        import random

        obj = bpy.data.objects.get(self.object_name)
        seed = _input(obj, "Seed")
        if seed is None:
            return {"CANCELLED"}
        seed.value = random.randint(0, 99999)
        obj.update_tag()
        return {"FINISHED"}


class BBT_OT_firmament_rain_preset(Operator):
    bl_idname = "bob_blender_tools.firmament_rain_preset"
    bl_label = "Rain Preset"
    bl_description = "Set the rain look from a named preset"
    bl_options = {"REGISTER", "UNDO"}

    preset: EnumProperty(
        name="Preset",
        items=[(k, v["label"], v["desc"]) for k, v in RAIN_PRESETS.items()])

    def execute(self, context):
        fm = context.scene.bbt_firmament
        obj = bpy.data.objects.get(fm.rain_object)
        if obj is None or _nodes_mod(obj) is None:
            _build_particulate(context, fm.rain_object, "streak")
            obj = bpy.data.objects.get(fm.rain_object)
        if obj is None:
            return {"CANCELLED"}
        for name, val in RAIN_PRESETS[self.preset]["knobs"].items():
            inp = _input(obj, name)
            if inp is not None:
                inp.value = val
        obj.update_tag()
        self.report({"INFO"}, f"Applied {RAIN_PRESETS[self.preset]['label']} preset")
        return {"FINISHED"}


class BBT_OT_firmament_mote_preset(Operator):
    bl_idname = "bob_blender_tools.firmament_mote_preset"
    bl_label = "Mote Preset"
    bl_description = "Set the mote look (dust, amber motes, or falling snow)"
    bl_options = {"REGISTER", "UNDO"}

    preset: EnumProperty(
        name="Preset",
        items=[(k, v["label"], v["desc"]) for k, v in MOTE_PRESETS.items()])

    def execute(self, context):
        fm = context.scene.bbt_firmament
        obj = bpy.data.objects.get(fm.mote_object)
        if obj is None or _nodes_mod(obj) is None:
            _build_particulate(context, fm.mote_object, "mote")
            obj = bpy.data.objects.get(fm.mote_object)
        if obj is None:
            return {"CANCELLED"}
        for name, val in MOTE_PRESETS[self.preset]["knobs"].items():
            inp = _input(obj, name)
            if inp is not None:
                inp.value = val
        obj.update_tag()
        self.report({"INFO"}, f"Applied {MOTE_PRESETS[self.preset]['label']} preset")
        return {"FINISHED"}


class BBT_OT_firmament_build_snow_cover(Operator):
    bl_idname = "bob_blender_tools.firmament_build_snow_cover"
    bl_label = "Add Snow Coverage"
    bl_description = ("Write the snow_cover attribute onto the surface (slope + altitude, "
                      "seeded from the Environment snow level). BobShaders reads it")

    def execute(self, context):
        fm = context.scene.bbt_firmament
        surface = fm.snow_surface or context.active_object
        if surface is None or surface.type != "MESH":
            self.report({"WARNING"}, "Pick a mesh surface for the snow coverage")
            return {"CANCELLED"}
        env = getattr(context.scene, "bbt_env", None)
        params = {}
        if env is not None:  # seed the coverage amount from the shared world state
            params["snow"] = env.snow
        server._ensure_path()
        from bbmcp.geonodes import build_geonodes_on_object

        build_geonodes_on_object(surface, "snow", "BOB_Snow", params)
        self.report({"INFO"}, f"Snow coverage written on {surface.name}")
        return {"FINISHED"}


class BBT_OT_firmament_snow_from_env(Operator):
    bl_idname = "bob_blender_tools.firmament_snow_from_env"
    bl_label = "Use Env Snow"
    bl_description = "Copy the Environment snow level onto the coverage pass"

    def execute(self, context):
        fm = context.scene.bbt_firmament
        env = getattr(context.scene, "bbt_env", None)
        surface = fm.snow_surface or context.active_object
        mod = _named_mod(surface, "BOB_Snow")
        snow = _input_of(mod, "Snow")
        if env is None or snow is None:
            return {"CANCELLED"}
        snow.value = env.snow
        surface.update_tag()
        self.report({"INFO"}, "Snow coverage synced from Environment")
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


class BBT_PT_firmament_clouds(Panel):
    bl_label = "Clouds"
    bl_idname = "BBT_PT_firmament_clouds"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "BobBlenderTools"
    bl_parent_id = "BBT_PT_firmament"
    bl_options = {"DEFAULT_CLOSED"}

    def draw(self, context):
        fm = context.scene.bbt_firmament
        layout = self.layout

        box = layout.box()
        box.prop(fm, "cloud_object")
        box.prop(fm, "cloud_shadows")
        row = box.row(align=True)
        row.operator("bob_blender_tools.firmament_build_clouds", icon="OUTLINER_OB_VOLUME")
        row.operator_menu_enum("bob_blender_tools.firmament_cloud_preset", "preset",
                               text="Preset", icon="PRESET")

        # Live knobs from the modifier (present only after a Build), grouped.
        obj = bpy.data.objects.get(fm.cloud_object)
        if obj is None or _nodes_mod(obj) is None:
            layout.label(text="Build to edit cloud knobs", icon="INFO")
            return

        col = layout.column(align=True)
        col.label(text="Shape", icon="MOD_NOISE")
        _draw_knobs(col, obj, _CLOUD_SHAPE)
        seed = _input(obj, "Cloud Seed")
        if seed is not None:
            row = col.row(align=True)
            row.prop(seed, "value", text="Cloud Seed")
            row.operator("bob_blender_tools.firmament_cloud_seed", text="", icon="FILE_REFRESH")
        _draw_knobs(col, obj, _CLOUD_SHAPE2)

        col = layout.column(align=True)
        col.label(text="Layer", icon="MESH_GRID")
        _draw_knobs(col, obj, _CLOUD_LAYER)

        col = layout.column(align=True)
        col.label(text="Wind", icon="FORCE_WIND")
        wind = _input(obj, "Wind")
        if wind is not None:
            col.prop(wind, "value", text="Wind Drift")
            if wind.value:
                _draw_knobs(col, obj, _CLOUD_WIND)
                col.operator("bob_blender_tools.firmament_cloud_wind_from_env",
                             icon="TRACKING_FORWARDS")


class BBT_PT_firmament_fog(Panel):
    bl_label = "Fog"
    bl_idname = "BBT_PT_firmament_fog"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "BobBlenderTools"
    bl_parent_id = "BBT_PT_firmament"
    bl_options = {"DEFAULT_CLOSED"}

    def draw(self, context):
        fm = context.scene.bbt_firmament
        layout = self.layout

        box = layout.box()
        box.prop(fm, "fog_object")
        box.prop(fm, "fog_mode")
        if fm.fog_mode == "ground_fog":
            box.prop(fm, "fog_heightmap")
        row = box.row(align=True)
        row.operator("bob_blender_tools.firmament_build_fog", icon="OUTLINER_OB_VOLUME")
        row.operator_menu_enum("bob_blender_tools.firmament_fog_preset", "preset",
                               text="Preset", icon="PRESET")

        # Live knobs from the modifier (present only after a Build), grouped.
        obj = bpy.data.objects.get(fm.fog_object)
        if obj is None or _nodes_mod(obj) is None:
            layout.label(text="Build to edit fog knobs", icon="INFO")
            return

        col = layout.column(align=True)
        col.label(text="Shape", icon="MOD_NOISE")
        _draw_knobs(col, obj, _FOG_SHAPE)
        seed = _input(obj, "Fog Seed")
        if seed is not None:
            row = col.row(align=True)
            row.prop(seed, "value", text="Fog Seed")
            row.operator("bob_blender_tools.firmament_fog_seed", text="", icon="FILE_REFRESH")

        col = layout.column(align=True)
        col.label(text="Look", icon="COLOR")
        _draw_knobs(col, obj, _FOG_LOOK)

        col = layout.column(align=True)
        col.label(text="Layer", icon="MESH_GRID")
        _draw_knobs(col, obj, _FOG_LAYER)

        # Terrain mapping, only for ground fog (the sockets exist only then).
        if _input(obj, "Terrain Size") is not None:
            col = layout.column(align=True)
            col.label(text="Terrain", icon="RNDCURVE")
            _draw_knobs(col, obj, _FOG_GROUND)

        col = layout.column(align=True)
        col.label(text="Wind", icon="FORCE_WIND")
        wind = _input(obj, "Wind")
        if wind is not None:
            col.prop(wind, "value", text="Wind Drift")
            if wind.value:
                _draw_knobs(col, obj, _FOG_WIND)
                col.operator("bob_blender_tools.firmament_fog_wind_from_env",
                             icon="TRACKING_FORWARDS")


class BBT_PT_firmament_weather(Panel):
    bl_label = "Weather"
    bl_idname = "BBT_PT_firmament_weather"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "BobBlenderTools"
    bl_parent_id = "BBT_PT_firmament"
    bl_options = {"DEFAULT_CLOSED"}

    def draw(self, context):
        fm = context.scene.bbt_firmament
        layout = self.layout

        col = layout.column(align=True)
        col.prop(fm, "weather_camera")
        col.prop(fm, "use_motion_blur")

        # Rain (streak mode).
        box = layout.box()
        box.label(text="Rain", icon="OUTLINER_OB_FORCE_FIELD")
        row = box.row(align=True)
        row.operator("bob_blender_tools.firmament_build_rain", icon="MOD_FLUIDSIM")
        row.operator_menu_enum("bob_blender_tools.firmament_rain_preset", "preset",
                               text="Preset", icon="PRESET")
        rain = bpy.data.objects.get(fm.rain_object)
        if rain is not None and _nodes_mod(rain) is not None:
            box.prop(rain, "hide_viewport", text="Hide", invert_checkbox=True, icon="HIDE_OFF")
            _draw_knobs(box, rain, _RAIN_KNOBS)
            seed = _input(rain, "Seed")
            if seed is not None:
                row = box.row(align=True)
                row.prop(seed, "value", text="Seed")
                op = row.operator("bob_blender_tools.firmament_particulate_seed",
                                  text="", icon="FILE_REFRESH")
                op.object_name = fm.rain_object
            _draw_knobs(box, rain, _RAIN_WIND)
            _draw_knobs(box, rain, ["Color"])
            _draw_knobs(box, rain, _DOMAIN_KNOBS)

        # Motes (dust / amber / falling snow, all mote mode).
        box = layout.box()
        box.label(text="Motes (Dust / Amber / Snow)", icon="PARTICLES")
        row = box.row(align=True)
        row.operator("bob_blender_tools.firmament_build_motes", icon="OUTLINER_OB_POINTCLOUD")
        row.operator_menu_enum("bob_blender_tools.firmament_mote_preset", "preset",
                               text="Preset", icon="PRESET")
        motes = bpy.data.objects.get(fm.mote_object)
        if motes is not None and _nodes_mod(motes) is not None:
            box.prop(motes, "hide_viewport", text="Hide", invert_checkbox=True, icon="HIDE_OFF")
            _draw_knobs(box, motes, _MOTE_KNOBS)
            seed = _input(motes, "Seed")
            if seed is not None:
                row = box.row(align=True)
                row.prop(seed, "value", text="Seed")
                op = row.operator("bob_blender_tools.firmament_particulate_seed",
                                  text="", icon="FILE_REFRESH")
                op.object_name = fm.mote_object
            _draw_knobs(box, motes, _MOTE_LOOK)
            _draw_knobs(box, motes, _MOTE_WIND)
            _draw_knobs(box, motes, _DOMAIN_KNOBS)

        # Snow coverage (the GN pass on the terrain surface, the single coverage source).
        box = layout.box()
        box.label(text="Snow Coverage", icon="OUTLINER_DATA_SURFACE")
        box.prop(fm, "snow_surface")
        box.operator("bob_blender_tools.firmament_build_snow_cover", icon="FREEZE")
        surface = fm.snow_surface or context.active_object
        snow_mod = _named_mod(surface, "BOB_Snow")
        if snow_mod is not None:
            _draw_knobs_mod(box, snow_mod, _SNOW_KNOBS)
            box.operator("bob_blender_tools.firmament_snow_from_env", icon="TRACKING_FORWARDS")
        else:
            box.label(text="Writes snow_cover for BobShaders to read", icon="INFO")


CLASSES = (
    BBT_FirmamentProps,
    BBT_OT_firmament_build_sky,
    BBT_OT_firmament_build_clouds,
    BBT_OT_firmament_cloud_seed,
    BBT_OT_firmament_cloud_preset,
    BBT_OT_firmament_cloud_wind_from_env,
    BBT_OT_firmament_build_fog,
    BBT_OT_firmament_fog_seed,
    BBT_OT_firmament_fog_preset,
    BBT_OT_firmament_fog_wind_from_env,
    BBT_OT_firmament_build_rain,
    BBT_OT_firmament_build_motes,
    BBT_OT_firmament_particulate_seed,
    BBT_OT_firmament_rain_preset,
    BBT_OT_firmament_mote_preset,
    BBT_OT_firmament_build_snow_cover,
    BBT_OT_firmament_snow_from_env,
    BBT_PT_firmament,
    BBT_PT_firmament_env,
    BBT_PT_firmament_sky,
    BBT_PT_firmament_clouds,
    BBT_PT_firmament_fog,
    BBT_PT_firmament_weather,
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
