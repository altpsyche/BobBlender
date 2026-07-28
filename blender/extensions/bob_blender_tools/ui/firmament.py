"""Firmament: the Atmosphere panel, peer to Terrain, Scatter, and Shaders.

Labelled "Atmosphere" in the tab since the 2026-07-20 UX redesign (docs/CONVENTIONS.md, panel UX
conventions). It authors the built subsystems: Sky, Clouds, Fog, and Weather (rain/motes/snow
coverage). The shared world state (Scene.bbt_env) is still owned and registered here, but its UI
(the Environment sliders), the Preview/Final Quality level, the Live Environment master, and the
Scene Presets / Apply Season now live in the World panel (world.py). This module keeps the
firmament_* operators; the World panel drives them.

The Live Environment master (bbt_world.live_env) and Quality reach this panel's subsystems through
the world applier registry: register subscribes _apply_world, which (re)installs or removes the
wind/snow drivers and re-applies quality when a world control changes.

Two homes, no drift: the shared world is bbt_env (World panel); Firmament's own subsystem state is
bbt_firmament.
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

from ..bridge import server
from ..core import atmosphere
from . import helpers, world

# Preset dicts, the live modifier-input helpers, the wind/snow drivers, the snow-line math, and
# the builder + orchestrator functions now live in core/atmosphere.py so the panel operators and
# the MCP ops share one copy (subtract-duplication). Bound here for the enum items, the panel draw
# code, and the operators that still resolve context (active object / scene props / report).
CLOUD_PRESETS = atmosphere.CLOUD_PRESETS
FOG_PRESETS = atmosphere.FOG_PRESETS
RAIN_PRESETS = atmosphere.RAIN_PRESETS
MOTE_PRESETS = atmosphere.MOTE_PRESETS
SEASON_APPLY = atmosphere.SEASON_APPLY
SCENE_PRESETS = atmosphere.SCENE_PRESETS

_nodes_mod = atmosphere._nodes_mod
_input = atmosphere._input
_named_mod = atmosphere._named_mod
_input_of = atmosphere._input_of
_apply_quality = atmosphere._apply_quality
_install_wind_drivers = atmosphere._install_wind_drivers
_remove_wind_drivers = atmosphere._remove_wind_drivers
_undrive_input = atmosphere._undrive_input
_sync_snow_pass = atmosphere._sync_snow_pass
_CLOUD_EXTRA = atmosphere._CLOUD_EXTRA


def _live_env_on(scene):
    """The one master Live Environment toggle now lives on the World panel (bbt_world);
 default on when World is absent (standalone verify)."""
    return getattr(getattr(scene, "bbt_world", None), "live_env", True)


def _env_owned_note(layout):
    """A muted one-liner under a greyed knob group: the Live Environment driver owns these,
 so an edit here would be overwritten. Shown only when Live Environment is on."""
    cap = layout.row()
    cap.enabled = False
    cap.label(text="Live Environment drives this; turn it off on World to edit")


def _from_env_row(layout, live, op_idname, object_name=None):
    """The copy-from-world branch shared by every subsystem's wind (and the snow) button (S6):
 when Live Environment is on a driver owns the input, so show the owned note and no button;
 else offer the one-shot copy-from-env button. Kept here (not helpers) because it is a
 Firmament-only idiom that reaches _env_owned_note."""
    if live:
        _env_owned_note(layout)
        return
    op = layout.operator(op_idname, icon="TRACKING_FORWARDS")
    if object_name is not None:
        op.object_name = object_name

# The bbmcp.env module, imported and registered at addon register time and held so
# unregister uses the same object even after Reload Builders purges bbmcp.
_env = None

# Live cloud knobs, grouped for the panel (drawn from the modifier after a Build).
_CLOUD_SHAPE = ["Coverage", "Cloud Scale", "Warp"]
_CLOUD_SHAPE2 = ["Detail", "Softness", "Density"]
_CLOUD_LAYER = ["Layer Size", "Thickness", "Height"]
_CLOUD_WIND = ["Wind Direction", "Wind Speed"]

# Live fog knobs, grouped for the panel.
_FOG_SHAPE = ["Density", "Fog Noise", "Fog Scale", "Fog Detail", "Softness", "Warp"]
_FOG_LOOK = ["Fog Color", "Anisotropy"]
_FOG_LAYER = ["Layer Size", "Thickness", "Height", "Fog Top", "Falloff"]
_FOG_GROUND = ["Terrain Size", "Terrain Height", "Sea Level", "Ground Thickness"]
_FOG_WIND = ["Wind Direction", "Wind Speed"]

# Live weather knobs, grouped for the panel.
_RAIN_KNOBS = ["Count", "Fall Speed", "Streak Length", "Size", "Size Variation"]
_RAIN_WIND = ["Wind Direction", "Wind Speed", "Drift"]
_MOTE_KNOBS = ["Count", "Fall Speed", "Drift", "Turbulence", "Size", "Size Variation"]
_MOTE_LOOK = ["Color", "Emission"]
_MOTE_WIND = ["Wind Direction", "Wind Speed"]
_DOMAIN_KNOBS = ["Domain Size", "Domain Height"]

def _draw_knobs(layout, obj, names, enabled=True):
    """Draw each present modifier input by socket name (live, no rebuild). enabled=False greys
 the row (a live driver owns the input, so an edit would be silently overwritten)."""
    mod = _nodes_mod(obj)
    if mod is None or mod.node_group is None:
        return
    ids = {it.name: it.identifier for it in mod.node_group.interface.items_tree
           if getattr(it, "item_type", None) == "SOCKET" and it.in_out == "INPUT"}
    col = layout.column(align=True)
    col.enabled = enabled
    for nm in names:
        ident = ids.get(nm)
        inp = getattr(mod.properties.inputs, ident, None) if ident else None
        if inp is not None:
            col.prop(inp, "value", text=nm)


def _draw_knobs_mod(layout, mod, names, enabled=True):
    """Draw each present input of a specific modifier by socket name (live). enabled=False greys
 the row (a live driver owns the input)."""
    if mod is None or mod.node_group is None:
        return
    ids = {it.name: it.identifier for it in mod.node_group.interface.items_tree
           if getattr(it, "item_type", None) == "SOCKET" and it.in_out == "INPUT"}
    col = layout.column(align=True)
    col.enabled = enabled
    for nm in names:
        ident = ids.get(nm)
        inp = getattr(mod.properties.inputs, ident, None) if ident else None
        if inp is not None:
            col.prop(inp, "value", text=nm)


# --- Live sun: reposition the Sun lamp + sky node from the world state whenever a geographic field
# (time/date/place, via env's geo-hook) or a sun override (this panel's own props) is edited, so the
# sun moves with no Build Sky press. The sun position is a nonlinear solar calc, so it cannot be a
# driver (and a custom-function pydriver breaks on untrusted files); a lightweight reposition runs
# on each edit instead. No node rebuild: it just sets the sun rotation/energy + the sky node sun
# angle, the same values a Build Sky would compute. ---
_solar = None


def _reposition_sun(scene):
    """Aim the existing Sun lamp + set the sky node's sun angle from the current world state
 (geographic solar model, or the manual override). A no-op when no sun has been built.
 Cheap: no node-tree rebuild, so it is safe to call on every geographic edit."""
    import math
    global _solar
    from ..core import world as W
    if _solar is None:
        from ..core import solar as _s
        _solar = _s
    sun = bpy.data.objects.get(W.SUN_NAME)
    env = getattr(scene, "bbt_env", None)
    if sun is None or env is None:
        return
    fm = getattr(scene, "bbt_firmament", None)
    if fm is not None and fm.use_override:
        el, az = fm.override_elevation, fm.override_azimuth
    else:
        pos = _solar.sun_position(env.latitude, env.longitude, int(env.year), int(env.month),
                                  int(env.day), env.time_of_day, utc_offset=env.utc_offset)
        el, az = pos["elevation"], pos["azimuth"]
    W._place_sun(sun, el, az,
                 strength=(fm.sun_strength if fm is not None else 2.0),
                 angle_deg=(fm.sun_angle if fm is not None else 0.545))
    world = scene.world
    tree = world.node_tree if world is not None and world.use_nodes else None
    sky = tree.nodes.get(W.SKY_NODE) if tree is not None else None
    if sky is not None:
        sky.sun_elevation = math.radians(el)
        sky.sun_rotation = math.radians(az)
        tree.update_tag()
    sun.update_tag()


def _sun_live_update(scene):
    """Reposition the sun on a geographic or override edit, when Live Environment is on. The
 env geo-hook (bbt_env fields) and the override-prop callbacks (this panel) both route here."""
    if scene is None or not _live_env_on(scene):
        return
    _reposition_sun(scene)


def _on_sun_override_change(self, context):
    """Update callback on this panel's sun-override props: re-place the sun live."""
    _sun_live_update(getattr(context, "scene", None) or getattr(bpy.context, "scene", None))


def _firmament_wind_objects(fm):
    """The built clouds, fog, rain, and mote objects (those that exist)."""
    names = (fm.cloud_object, fm.fog_object, fm.rain_object, fm.mote_object)
    return [o for o in (bpy.data.objects.get(n) for n in names) if o is not None]


def _apply_world(scene):
    """Atmosphere's world applier (subscribed with world): re-apply the atmosphere
 subsystems to the current world state. Installs or removes the live wind/snow drivers per
 the master Live Environment toggle, and re-applies the Quality level. A non-structural
 driver edit, safe from the rebuild re-entrancy the repo avoids for structural changes.
 Adding a new atmosphere subsystem needs no new toggle wiring: it just gets driven here."""
    fm = getattr(scene, "bbt_firmament", None)
    if fm is None:
        return
    live = _live_env_on(scene)
    # Live sun: place it from the current world state now; msgbus keeps it live on later edits.
    if live:
        _reposition_sun(scene)
    clouds = bpy.data.objects.get(fm.cloud_object)
    for obj in _firmament_wind_objects(fm):
        extra = _CLOUD_EXTRA if obj is clouds else ()
        if live:
            _install_wind_drivers(obj, scene, extra)
        else:
            _remove_wind_drivers(obj, extra)
    surface = fm.snow_surface or getattr(bpy.context, "active_object", None)
    mod = _named_mod(surface, "BOB_Snow")
    if mod is not None:
        # Clear any legacy live driver (older builds drove the pass Snow from bbt_env.snow, now
        # removed) and refresh the shell from the env, since it is no longer driven live.
        _undrive_input(surface, _input_of(mod, "Snow"))
        _undrive_input(surface, _input_of(mod, "Altitude"))
        _sync_snow_pass(surface, _env.get_env(scene))
    _apply_quality(scene)


class BBT_FirmamentProps(PropertyGroup):
    """Firmament's own UI and subsystem state (not the shared world)."""

    # Sky Look: the staged pick for the whole-atmosphere preset (applied by Apply Sky Look, not
    # on the pick). Owns only sky mood (time/weather/cloud/wind + subsystems), never the season.
    sky_look: EnumProperty(
        name="Sky Look",
        items=[(k, v["label"], v["desc"]) for k, v in SCENE_PRESETS.items()],
        description="A whole-sky mood to stage; press Apply Sky Look to commit it")

    # Season -> date coupling (item 2). When on, Apply Season sets env.month to the season's
    # representative month (hemisphere-aware) so the live sun drops in winter for free. Gated so
    # a pinned shot date is never silently clobbered.
    season_sets_date: BoolProperty(
        name="Season sets the date", default=True,
        description="Apply Season also sets the calendar month, so the sun sits low in winter "
                    "and high in summer. Turn off to keep the date you set")

    # Sun override on top of the geographic position. update=_on_sun_override_change re-places
    # the sun live, matching the geographic fields' live behaviour.
    use_override: BoolProperty(
        name="Manual Sun", default=False,
        description="Set the sun angle by hand instead of from time and place",
        update=_on_sun_override_change)
    override_elevation: FloatProperty(
        name="Elevation", default=45.0, min=-90.0, max=90.0,
        description="Degrees above the horizon", update=_on_sun_override_change)
    override_azimuth: FloatProperty(
        name="Azimuth", default=180.0, min=0.0, max=360.0,
        description="Degrees clockwise from north", update=_on_sun_override_change)

    # Sun light.
    sun_strength: FloatProperty(name="Sun Strength", default=2.0, min=0.0,
                                update=_on_sun_override_change)
    sun_angle: FloatProperty(
        name="Sun Size", default=0.545, min=0.0, max=20.0,
        description="Angular diameter in degrees; larger softens shadows",
        update=_on_sun_override_change)
    sun_disc: BoolProperty(
        name="Show Sun Disc", default=False,
        description="Draw the sky's sun disc. Off by default so the lamp lights "
                    "and the sun is not counted twice")

    # Physical sky (5.2 MULTIPLE_SCATTERING; the Nishita successor).
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

    # Quality (Preview/Final) and the Live Environment master toggle moved to the World panel
# (bbt_world); they are scene-wide, not atmosphere-specific (docs/CONVENTIONS.md, panel UX
# conventions).

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

    # Weather: rain streaks and dust/amber/snow motes in a camera-following
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
        # Picking the terrain also fits the snow line's Z bounds to it, so the normalized line
        # reads right immediately (0 = its valley, 1 = its peaks).
        # Wrapped so the callback returns None: stamp_snow_bounds returns a bool, and a property
        # update callback that returns non-None raises "the return value must be None".
        update=lambda self, ctx: (_env.stamp_snow_bounds(ctx.scene, self.snow_surface), None)[1],
        description="The terrain the snow-coverage pass writes snow_cover onto (BobShaders "
                    "reads that attribute). Defaults to the active mesh")


class BBT_OT_firmament_build_sky(Operator):
    bl_idname = "bob_blender_tools.firmament_build_sky"
    bl_label = "Build Sky"
    bl_description = "Author the physical sky and matched Sun light from the world state"

    def execute(self, context):
        res = atmosphere.build_sky_from_env(context.scene)
        # A rebuild recreates the sky node (dropping its drivers) and resets the sun; re-apply the
        # world so the live sun drivers are reinstalled and time/place stay live from here.
        world.apply_all(context.scene)
        self.report({"INFO"}, f"Sky: {res.get('info', '')}")
        return {"FINISHED"}


class BBT_OT_firmament_build_clouds(Operator):
    bl_idname = "bob_blender_tools.firmament_build_clouds"
    bl_label = "Build Clouds"
    bl_description = "Build the procedural volumetric cloud layer"

    def execute(self, context):
        fm = context.scene.bbt_firmament
        obj = atmosphere.build_clouds_object(context.scene, name=fm.cloud_object,
                                             cloud_shadows=fm.cloud_shadows)
        n = _count_instances(context, obj) if obj is not None else 0
        self.report({"INFO"}, f"Clouds: {n} puffs")
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
        obj = atmosphere.apply_cloud_preset(context.scene, self.preset,
                                            name=fm.cloud_object, cloud_shadows=fm.cloud_shadows)
        if obj is None:
            return {"CANCELLED"}
        self.report({"INFO"}, f"Applied {CLOUD_PRESETS[self.preset]['label']} preset")
        return {"FINISHED"}


class BBT_OT_firmament_build_fog(Operator):
    bl_idname = "bob_blender_tools.firmament_build_fog"
    bl_label = "Build Fog"
    bl_description = "Build a procedural volumetric fog domain"

    def execute(self, context):
        fm = context.scene.bbt_firmament
        heightmap = (bpy.path.abspath(fm.fog_heightmap)
                     if fm.fog_mode == "ground_fog" and fm.fog_heightmap else "")
        atmosphere.build_fog_object(context.scene, name=fm.fog_object,
                                    mode=fm.fog_mode, heightmap=heightmap)
        label = {"height_fog": "Height Fog", "noise_fog": "Noise Fog",
                 "ground_fog": "Ground Fog"}[fm.fog_mode]
        self.report({"INFO"}, f"Fog: built {label}")
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
        obj = atmosphere.apply_fog_preset(context.scene, self.preset,
                                          name=fm.fog_object, mode=fm.fog_mode)
        if obj is None:
            return {"CANCELLED"}
        self.report({"INFO"}, f"Applied {FOG_PRESETS[self.preset]['label']} preset")
        return {"FINISHED"}


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
        cam = fm.weather_camera
        obj = atmosphere.build_particulate(
            context.scene, fm.rain_object, "streak",
            camera_name=cam.name if cam is not None else None,
            use_motion_blur=fm.use_motion_blur)
        n = _count_instances(context, obj) if obj is not None else 0
        self.report({"INFO"}, f"Rain: {n} streaks")
        return {"FINISHED"}


class BBT_OT_firmament_build_motes(Operator):
    bl_idname = "bob_blender_tools.firmament_build_motes"
    bl_label = "Build Motes"
    bl_description = "Build the dust / amber / snow mote particle system (pick a preset)"

    def execute(self, context):
        fm = context.scene.bbt_firmament
        cam = fm.weather_camera
        obj = atmosphere.build_particulate(
            context.scene, fm.mote_object, "mote",
            camera_name=cam.name if cam is not None else None,
            use_motion_blur=fm.use_motion_blur)
        n = _count_instances(context, obj) if obj is not None else 0
        self.report({"INFO"}, f"Motes: {n} motes")
        return {"FINISHED"}


class BBT_OT_firmament_randomize_seed(Operator):
    # One reshuffle-seed operator for every atmosphere object (clouds, fog, rain, motes):
    # they only differ by which object and which named Seed input, so both are properties
    # rather than three near-identical operator classes.
    bl_idname = "bob_blender_tools.firmament_randomize_seed"
    bl_label = "Randomize Seed"
    bl_description = "Reshuffle this atmosphere pattern with a new seed"

    object_name: StringProperty()
    seed_input: StringProperty(default="Seed")

    def execute(self, context):
        import random

        obj = bpy.data.objects.get(self.object_name)
        seed = _input(obj, self.seed_input)
        if seed is None:
            return {"CANCELLED"}
        seed.value = random.randint(0, 99999)
        obj.update_tag()
        return {"FINISHED"}


class BBT_OT_firmament_wind_from_env(Operator):
    # One "copy Environment wind onto this object" operator for every atmosphere object;
    # they were identical apart from how the object was resolved, so it takes object_name.
    bl_idname = "bob_blender_tools.firmament_wind_from_env"
    bl_label = "Use Env Wind"
    bl_description = "Copy the Environment wind direction and strength onto this object"

    object_name: StringProperty()

    def execute(self, context):
        env = _env.get_env(context.scene)
        obj = bpy.data.objects.get(self.object_name)
        wdir, wspd = _input(obj, "Wind Direction"), _input(obj, "Wind Speed")
        if env is None or wdir is None or wspd is None:
            return {"CANCELLED"}
        wdir.value = env.wind_direction
        wspd.value = env.wind_strength
        obj.update_tag()
        self.report({"INFO"}, "Wind synced from Environment")
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
        obj = atmosphere.apply_rain_preset(context.scene, self.preset)
        if obj is None:
            return {"CANCELLED"}
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
        obj = atmosphere.apply_mote_preset(context.scene, self.preset)
        if obj is None:
            return {"CANCELLED"}
        self.report({"INFO"}, f"Applied {MOTE_PRESETS[self.preset]['label']} preset")
        return {"FINISHED"}


class BBT_OT_firmament_build_snow_cover(Operator):
    bl_idname = "bob_blender_tools.firmament_build_snow_cover"
    bl_label = "Add Snow Coverage"
    bl_description = ("Write the snow_cover + snow_occlusion attributes onto the surface, "
                      "seeded from the Environment snow level and snow line. The accumulation "
                      "shell reads snow_cover for thickness; the material reads snow_occlusion")

    def execute(self, context):
        fm = context.scene.bbt_firmament
        surface = fm.snow_surface or context.active_object
        if surface is None or surface.type != "MESH":
            self.report({"WARNING"}, "Pick a mesh surface for the snow coverage")
            return {"CANCELLED"}
        atmosphere.build_snow_cover_on(context.scene, surface)
        self.report({"INFO"}, f"Snow coverage written on {surface.name}")
        return {"FINISHED"}


class BBT_OT_firmament_snow_from_env(Operator):
    bl_idname = "bob_blender_tools.firmament_snow_from_env"
    bl_label = "Use Env Snow"
    bl_description = "Refresh the snow pass (the shell) from the Environment temperature and snow line"

    def execute(self, context):
        fm = context.scene.bbt_firmament
        env = _env.get_env(context.scene)
        surface = fm.snow_surface or context.active_object
        if env is None or _named_mod(surface, "BOB_Snow") is None:
            return {"CANCELLED"}
        _sync_snow_pass(surface, env)
        self.report({"INFO"}, "Snow pass synced from Environment")
        return {"FINISHED"}


class BBT_OT_firmament_apply_season(Operator):
    bl_idname = "bob_blender_tools.firmament_apply_season"
    bl_label = "Apply Season"
    bl_description = ("Apply the current season: set its continuous state (snow, "
                     "wetness, temperature, fed live to the readers) and, for winter, "
                     "build the falling snow and snow-coverage pass. Explicit, not a "
                     "property callback, so it does not hit the scatter re-entrancy")
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        try:
            season, built, _created = atmosphere.apply_season_state(context.scene)
        except ValueError:
            return {"CANCELLED"}
        note = f"Season: {season}"
        if built:
            note += " (built " + ", ".join(built) + ")"
        self.report({"INFO"}, note)
        return {"FINISHED"}


class BBT_OT_firmament_scene_preset(Operator):
    bl_idname = "bob_blender_tools.firmament_scene_preset"
    bl_label = "Apply Sky Look"
    bl_description = ("Apply the staged Sky Look: set the sky mood (time, weather, cloud cover, "
                      "wind) and seed each atmosphere subsystem. Does not touch the season")
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        p, _created = atmosphere.apply_scene_preset(
            context.scene, context.scene.bbt_firmament.sky_look)
        # apply_scene_preset builds the sky via core.world.build_sky (dropping the sky node's
        # drivers and resetting the sun); re-apply the world so the live sun drivers reinstall and
        # the wind drivers land on the freshly built subsystems.
        world.apply_all(context.scene)
        self.report({"INFO"}, f"Applied {p['label']} scene preset")
        return {"FINISHED"}


# Firmament is now "Atmosphere": the built sky/clouds/fog/weather subsystems. The world state
# (bbt_env), the Quality level, the Live Environment master, and Scene Presets moved to the World
# panel (docs/CONVENTIONS.md, panel UX conventions). Class/operator names keep the firmament_* /
# BBT_* spelling per decision F.
class BBT_PT_firmament(Panel):
    bl_label = "Atmosphere"
    bl_idname = "BBT_PT_firmament"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "BobBlenderTools"
    bl_order = 7  # after the pipeline stages (docs/CONVENTIONS.md, panel UX conventions)
    bl_options = {"DEFAULT_CLOSED"}

    def draw(self, context):
        # Primary action first: the root is no longer empty. It shows the sky state and carries the
# primary Build Sky, so the panel's main action is the first thing you see instead of buried
# at the bottom of the Sky sub-panel under its ten inputs. Sky / Clouds / Fog / Weather tune
# below.
        layout = self.layout
        built = bpy.data.objects.get("BOB_Sun") is not None
        layout.label(text="Sky built" if built else "No sky yet",
                     icon="LIGHT_SUN" if built else "INFO")
        helpers.structural_action(
            layout, "bob_blender_tools.firmament_build_sky",
            text="Rebuild Sky" if built else "Build Sky",
            note="builds the sky + sun (time/place: World; sky inputs: Sky sub-panel)")


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

        # These are the INPUTS to Build Sky (not live post-build knobs), so they show always. Build
# Sky itself lives on the Atmosphere header above so the primary action comes first, not
# repeated here; edit an input, then press Rebuild Sky up there.
        col = layout.column(align=True)
        col.label(text="Sun", icon="LIGHT_SUN")
        col.prop(fm, "use_override")
        if fm.use_override:
            col.prop(fm, "override_elevation")
            col.prop(fm, "override_azimuth")
        col.prop(fm, "sun_strength")
        col.prop(fm, "sun_angle")
        col.prop(fm, "sun_disc")

        col = layout.column(align=True)
        col.label(text="Sky", icon="WORLD")
        col.prop(fm, "world_strength")
        col.prop(fm, "sky_altitude")
        col.prop(fm, "air")
        col.prop(fm, "ozone")
        col.prop(fm, "turbidity")
        col.prop(fm, "ground_albedo")

        cap = layout.row()
        cap.enabled = False
        cap.label(text="edit, then Rebuild Sky on the Atmosphere header above", icon="INFO")


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
        helpers.structural_action(box, "bob_blender_tools.firmament_build_clouds",
                                     note="builds: the cloud volume object")

        # Live knobs from the modifier (present only after a Build), grouped.
        obj = bpy.data.objects.get(fm.cloud_object)
        if obj is None or _nodes_mod(obj) is None:
            layout.label(text="Build to edit cloud knobs", icon="INFO")
            return

        # Instant preset: the look preset is instant (light: sets knobs), so it is gated behind
# Build like the other knobs. It no longer sits above the gate where picking it would
# silently build.
        helpers.preset_row(layout, "bob_blender_tools.firmament_cloud_preset")

        live = _live_env_on(context.scene)
        col = layout.column(align=True)
        col.label(text="Shape", icon="MOD_NOISE")
        # Coverage is driven from bbt_env.cloud_cover when Live Environment is on; grey it so
        # the edit does not read as live (Cloud Scale / Warp are always author-owned).
        _draw_knobs(col, obj, ["Coverage"], enabled=not live)
        _draw_knobs(col, obj, _CLOUD_SHAPE[1:])
        seed = _input(obj, "Cloud Seed")
        if seed is not None:
            helpers.seed_row(col, seed, "value", "bob_blender_tools.firmament_randomize_seed",
                                text="Cloud Seed",
                                op_props={"object_name": fm.cloud_object,
                                          "seed_input": "Cloud Seed"})
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
                _draw_knobs(col, obj, _CLOUD_WIND, enabled=not live)
                # A driver owns wind when Live Environment is on: grey the knobs and drop the
                # copy-from-env button (it would be overwritten), else offer the one-shot copy.
                _from_env_row(col, live, "bob_blender_tools.firmament_wind_from_env",
                              object_name=fm.cloud_object)


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
        helpers.structural_action(box, "bob_blender_tools.firmament_build_fog",
                                     note="builds: the fog volume object")

        # Live knobs from the modifier (present only after a Build), grouped.
        obj = bpy.data.objects.get(fm.fog_object)
        if obj is None or _nodes_mod(obj) is None:
            layout.label(text="Build to edit fog knobs", icon="INFO")
            return

        # Instant preset: instant look preset, gated behind Build so picking it never silently
# builds.
        helpers.preset_row(layout, "bob_blender_tools.firmament_fog_preset")

        col = layout.column(align=True)
        col.label(text="Shape", icon="MOD_NOISE")
        _draw_knobs(col, obj, _FOG_SHAPE)
        seed = _input(obj, "Fog Seed")
        if seed is not None:
            helpers.seed_row(col, seed, "value", "bob_blender_tools.firmament_randomize_seed",
                                text="Fog Seed",
                                op_props={"object_name": fm.fog_object,
                                          "seed_input": "Fog Seed"})

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
                live = _live_env_on(context.scene)
                _draw_knobs(col, obj, _FOG_WIND, enabled=not live)
                _from_env_row(col, live, "bob_blender_tools.firmament_wind_from_env",
                              object_name=fm.fog_object)


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
        helpers.structural_action(box, "bob_blender_tools.firmament_build_rain",
                                     note="builds: falling rain streaks")
        rain = bpy.data.objects.get(fm.rain_object)
        if rain is not None and _nodes_mod(rain) is not None:
            # Instant preset: instant look preset, gated behind Build so it never silently builds.
            helpers.preset_row(box, "bob_blender_tools.firmament_rain_preset")
            box.prop(rain, "hide_viewport", text="Hide", invert_checkbox=True, icon="HIDE_OFF")
            _draw_knobs(box, rain, _RAIN_KNOBS)
            seed = _input(rain, "Seed")
            if seed is not None:
                helpers.seed_row(box, seed, "value", "bob_blender_tools.firmament_randomize_seed",
                                    op_props={"object_name": fm.rain_object})
            live = _live_env_on(context.scene)
            _draw_knobs(box, rain, _RAIN_WIND, enabled=not live)
            _from_env_row(box, live, "bob_blender_tools.firmament_wind_from_env",
                          object_name=fm.rain_object)
            _draw_knobs(box, rain, ["Color"])
            _draw_knobs(box, rain, _DOMAIN_KNOBS)

        # Motes (dust / amber / falling snow, all mote mode).
        box = layout.box()
        box.label(text="Motes (Dust / Amber / Snow)", icon="PARTICLES")
        helpers.structural_action(box, "bob_blender_tools.firmament_build_motes",
                                     note="builds: floating motes (dust / amber / snow)")
        motes = bpy.data.objects.get(fm.mote_object)
        if motes is not None and _nodes_mod(motes) is not None:
            # Instant preset: instant look preset, gated behind Build so it never silently builds.
            helpers.preset_row(box, "bob_blender_tools.firmament_mote_preset")
            box.prop(motes, "hide_viewport", text="Hide", invert_checkbox=True, icon="HIDE_OFF")
            _draw_knobs(box, motes, _MOTE_KNOBS)
            seed = _input(motes, "Seed")
            if seed is not None:
                helpers.seed_row(box, seed, "value", "bob_blender_tools.firmament_randomize_seed",
                                    op_props={"object_name": fm.mote_object})
            _draw_knobs(box, motes, _MOTE_LOOK)
            live = _live_env_on(context.scene)
            _draw_knobs(box, motes, _MOTE_WIND, enabled=not live)
            _from_env_row(box, live, "bob_blender_tools.firmament_wind_from_env",
                          object_name=fm.mote_object)
            _draw_knobs(box, motes, _DOMAIN_KNOBS)

        # Snow pass (GN, on the terrain): feeds the accumulation shell (snow_cover) and the
        # material's optional shelter term (snow_occlusion). The material whitens on its own.
        box = layout.box()
        box.label(text="Snow Coverage", icon="OUTLINER_DATA_SURFACE")
        box.prop(fm, "snow_surface")
        helpers.structural_action(box, "bob_blender_tools.firmament_build_snow_cover",
                                     note="builds: the snow pass (shell coverage + occlusion)")
        surface = fm.snow_surface or context.active_object
        snow_mod = _named_mod(surface, "BOB_Snow")
        if snow_mod is not None:
            live = _live_env_on(context.scene)
            # Snow (amount) is driven from bbt_env when Live Environment is on, so grey it. The
# world-Z Altitude/Falloff are the snow line, set from the env on build/sync (Use Env
# Snow), so they show as author-owned; the slope band and occlusion are author-owned
# too.
            _draw_knobs_mod(box, snow_mod, ["Snow"], enabled=not live)
            _draw_knobs_mod(box, snow_mod, ["Slope Threshold", "Slope Falloff", "Altitude",
                                            "Altitude Falloff", "Occlusion", "Occlusion Distance"])
            _from_env_row(box, live, "bob_blender_tools.firmament_snow_from_env")
        else:
            box.label(text="Feeds the snow shell + occlusion; the material snows on its own",
                      icon="INFO")


CLASSES = (
    BBT_FirmamentProps,
    BBT_OT_firmament_build_sky,
    BBT_OT_firmament_build_clouds,
    BBT_OT_firmament_cloud_preset,
    BBT_OT_firmament_build_fog,
    BBT_OT_firmament_fog_preset,
    BBT_OT_firmament_build_rain,
    BBT_OT_firmament_build_motes,
    BBT_OT_firmament_randomize_seed,
    BBT_OT_firmament_wind_from_env,
    BBT_OT_firmament_rain_preset,
    BBT_OT_firmament_mote_preset,
    BBT_OT_firmament_build_snow_cover,
    BBT_OT_firmament_snow_from_env,
    BBT_OT_firmament_apply_season,
    BBT_OT_firmament_scene_preset,
    BBT_PT_firmament,
    BBT_PT_firmament_sky,
    BBT_PT_firmament_clouds,
    BBT_PT_firmament_fog,
    BBT_PT_firmament_weather,
)


def register():
    global _env, _solar
    from ..core import env
    _env = env
    _solar = None  # rebound lazily by the driver function (survives a Reload Builders)
    env.register()  # BobFirmament owns and registers the shared world state
    for cls in CLASSES:
        bpy.utils.register_class(cls)
    bpy.types.Scene.bbt_firmament = bpy.props.PointerProperty(type=BBT_FirmamentProps)
    # Live sun: subscribe the reposition to the shared env's geographic-change hook, so editing
    # time/date/place re-places the sun (the override props carry their own update callback).
    _env.register_geo_hook(_sun_live_update)
    # Subscribe the atmosphere applier so the World master toggle / quality drive it (quality
# scaling).
    world.register_applier(_apply_world)


def unregister():
    world.unregister_applier(_apply_world)
    if _env is not None:
        _env.unregister_geo_hook(_sun_live_update)
    del bpy.types.Scene.bbt_firmament
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
    if _env is not None:
        _env.unregister()
