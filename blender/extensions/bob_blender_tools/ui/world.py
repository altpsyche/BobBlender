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

from ..bridge import server
from . import helpers

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
# reindex guard the other panels' dynamic enums use). Ids start at 0 for the first real biome so a
# fresh property resolves to a real item, not the NONE fallback (S2: no more blank first pick). The
# NONE placeholder only appears when there are no biomes at all, and then it is the sole item.
_BIOME_WORLD_ITEMS = [("NONE", "None", "No biome world", "", 0)]
_BIOME_WORLD_IDS = {}
_BIOME_APPLY_ITEMS = [("NONE", "None", "No biome", "", 0)]
_BIOME_APPLY_IDS = {}


def _assets():
    from ..core import assets

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


def _sky_built():
    """True when a sky+sun has been built. BobFirmament's build_sky creates the BOB_Sun
    object (bbmcp/world.py SUN_NAME), so its presence is the "a sky exists" marker the
    World first-build affordance and the Time-and-place caption key off."""
    return bpy.data.objects.get("BOB_Sun") is not None


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
    # Staged biome picks for the Biome panel (applied by their buttons, not on the pick).
    biome: EnumProperty(
        name="Biome", items=_biome_apply_items,
        description="The biome to stand up; press Build Biome to commit it")
    biome_world: EnumProperty(
        name="Biome World", items=_biome_world_items,
        description="A biome whose world mood to stage; press Set Biome World to apply it")
    biome_weather_assets: BoolProperty(
        name="Weather scattered assets", default=True,
        description="Convert the scattered proxies to BobShaders so they weather with the world "
                    "and their surface look is editable (select the scatter layer, then edit in "
                    "Shaders). Uncheck to keep the proxies' plain materials")
    biome_build_sky: BoolProperty(
        name="Build Sky", default=True,
        description="Rebuild the sky after setting the world, so the sun moves to the biome time")


class BBT_OT_world_biome_world(Operator):
    bl_idname = "bob_blender_tools.world_biome_world"
    bl_label = "Biome World"
    bl_description = ("Set the world to a biome's defaults: season, weather, time of day, cloud "
                      "cover, temperature, and wind from the biome's world block, then re-apply "
                      "every live consumer. Builds the sky so the sun moves to the set time")
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        world_state = context.scene.bbt_world
        biome = world_state.biome_world
        if not biome or biome == "NONE":
            self.report({"ERROR"}, "No biome carries a world block")
            return {"CANCELLED"}
        world = _assets().biome_world(biome)
        if not world:
            self.report({"ERROR"}, f"Biome '{biome}' has no world block")
            return {"CANCELLED"}
        # The bbt_env setattr loop lives in core/biome.apply_world (shared with the MCP world_biome
        # handler); the panel keeps the staged-pick resolution, the applier re-run, and the sky build.
        from ..core import biome
        res = biome.apply_world(context.scene.bbt_env, world)
        applied = res["applied"]
        apply_all(context.scene)  # re-apply drivers/quality to the new world state
        built = ""
        if world_state.biome_build_sky:
            try:
                bpy.ops.bob_blender_tools.firmament_build_sky()
                built = " + sky"
            except RuntimeError as exc:
                print(f"[bob_blender_tools] biome world: build sky skipped ({exc})")
        self.report({"INFO"}, f"Biome world {biome}: set {len(applied)} fields{built}")
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
    bl_label = "Build Biome"
    bl_description = ("Stand up a whole biome on one terrain mesh: build its terrain material, "
                      "scatter its proxy layers, and set the world - each section that the "
                      "manifest carries, in order. Uses the Scatter emitter as the terrain, or "
                      "the active mesh. One coherent scene from the staged pick")
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        world_state = context.scene.bbt_world
        biome = world_state.biome
        if not biome or biome == "NONE":
            self.report({"ERROR"}, "No biome to build")
            return {"CANCELLED"}
        target = _apply_target(context)
        if target is None:
            self.report({"ERROR"}, "Set a Scatter emitter or select a terrain mesh first")
            return {"CANCELLED"}
        a = _assets()
        man = a.biome_manifest(biome)
        warn = a.validate_biome(biome)
        # Scatter reads bbt_scatter.emitter; Biome Terrain shades the ACTIVE object.
        if getattr(context.scene, "bbt_scatter", None) is not None:
            context.scene.bbt_scatter.emitter = target

        def make_target_active():
            context.view_layer.objects.active = target
            try:
                target.select_set(True)
            except RuntimeError:
                pass

        def fail(step):
            # A nested operator signals failure with {'CANCELLED'} (it does not raise), so
            # ploughing on would build later steps against absent assets and report success
            # over a half-built scene. Stop the chain and say which step failed.
            self.report({"ERROR"}, f"Build {biome}: {step} failed; scene half-applied "
                                   f"(after: {', '.join(steps) or 'nothing'})")

        steps = []
        if man["terrain"]:
            make_target_active()
            if "CANCELLED" in bpy.ops.bob_blender_tools.shaders_biome_terrain(biome=biome):
                fail("terrain"); return {"CANCELLED"}
            steps.append("terrain")
        if man["scatter"]:
            if "CANCELLED" in bpy.ops.bob_blender_tools.scatter_biome_scatter(biome=biome):
                fail("scatter"); return {"CANCELLED"}
            steps.append("scatter")
            # Weather the scattered assets: convert each built layer's OWN assets collection to
            # BobShaders (Convert, Collection scope; idempotent, installs the env feed). Reading
            # the layers rather than the hardcoded BOB_Assets_<kind> names means a biome pointing
            # a layer at a custom collection is weathered too, not only the block-out proxies. Off
            # (the checkbox) keeps the plain materials.
            if world_state.biome_weather_assets:
                emitter = context.scene.bbt_scatter.emitter
                scoll = emitter.bbt_scatter_coll if emitter is not None else None
                seen = set()
                for lay_obj in (scoll.objects if scoll is not None else ()):
                    assets = getattr(lay_obj.bbt_scatter_layer, "assets", None)
                    if assets is None or assets.name in seen:
                        continue
                    seen.add(assets.name)
                    try:
                        bpy.ops.bob_blender_tools.shaders_convert(
                            scope="collection", coll_name=assets.name)
                    except RuntimeError as exc:
                        print(f"[bob_blender_tools] build biome: convert {assets.name} skipped ({exc})")
                if seen:
                    steps.append("weathered assets")
        if man["world"]:
            world_state.biome_world = biome  # world_biome_world reads the staged pick
            if "CANCELLED" in bpy.ops.bob_blender_tools.world_biome_world():
                fail("world"); return {"CANCELLED"}
            steps.append("world")
        msg = f"Built {biome} on {target.name}: {', '.join(steps) or '(nothing to apply)'}"
        if warn:
            msg += f" ({len(warn)} manifest warnings, see console)"
            print("[bob_blender_tools] biome warnings:", warn)
        self.report({"INFO"}, msg)
        return {"FINISHED"}


class BBT_PT_biome(Panel):
    bl_label = "Biome"
    bl_idname = "BBT_PT_biome"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "BobBlenderTools"
    bl_order = 1  # right after World: the one-action way to stand up a whole scene
    bl_options = {"DEFAULT_CLOSED"}

    def draw(self, context):
        layout = self.layout
        world = context.scene.bbt_world

        if not _has_any_biome():
            layout.label(text="No biomes in library/models", icon="INFO")
            return

        # What a biome is (item 4): one preset that touches terrain, scatter, season and weather
        # together. Build Biome stands up the whole scene; the per-panel Biome Terrain / Biome
        # Scatter / Biome World are the same recipe applied one piece at a time.
        layout.label(text="A biome presets terrain + scatter + world together", icon="INFO")

        # P1: the mesh Build Biome shades and scatters onto (Scatter emitter, or active mesh).
        target = _apply_target(context)
        helpers.context_header(
            layout, "Active mesh", target.name if target else None,
            icon="OUTLINER_OB_MESH",
            empty="Set a Scatter emitter or select a terrain mesh to build on")

        # Build the whole biome (terrain + scatter + world) from the staged pick.
        box = layout.box()
        box.label(text="Build a whole biome", icon=helpers.STRUCTURAL_ICON)
        box.prop(world, "biome_weather_assets")
        helpers.staged_preset_row(
            box, world, "biome", "bob_blender_tools.world_apply_biome", text="Biome",
            apply_text="Build Biome",
            note="builds terrain + scatter + world on the terrain object above")

        # Set only the world mood from a biome (no terrain/scatter), for a quick look match.
        if _has_biome_world():
            box = layout.box()
            box.label(text="Biome world only", icon="WORLD")
            box.prop(world, "biome_build_sky")
            helpers.staged_preset_row(
                box, world, "biome_world", "bob_blender_tools.world_biome_world",
                text="Biome World", apply_text="Set Biome World",
                note="sets season/weather/time/wind from the biome; no terrain or scatter")


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
        layout.label(text="World, Terrain, Paths, Scatter, Shaders, Atmosphere", icon="INFO")

        # Scene-wide masters. With Firmament off there is no env state, so nothing for Quality or
        # Live Environment to drive (no atmosphere subsystems, no shader env feed): grey them.
        # A8: in the shipped single addon this branch never fires (firmament.register always
        # registers bbt_env at load). It is kept deliberately for the planned polyrepo split, where
        # World can ship without Firmament and bbt_env is then genuinely absent.
        firmament_off = _env is None or _env.get_env(context.scene) is None
        row = layout.row(align=True)
        row.enabled = not firmament_off
        row.prop(world, "quality", expand=True)
        le = layout.row()
        le.enabled = not firmament_off
        le.prop(world, "live_env", icon="FORCE_WIND")
        if firmament_off:
            layout.label(text="Firmament off: world present but no atmosphere", icon="INFO")

        # First-build affordance (F2): before a sky exists, offer Build Sky here so the artist
        # can set the time/place (Time and place sub-panel) and build from the top panel on the
        # first pass, not hunt for it in Atmosphere. Self-limiting: it vanishes once a sky is built.
        if not firmament_off and not _sky_built():
            helpers.structural_action(
                layout, "bob_blender_tools.firmament_build_sky", text="Build Sky",
                note="no sky yet; builds the sky + sun from the time and place below")

        # -- Season: the one seasonal lever (snow/wetness/temperature + winter subsystems). Set
        # the season first, then tune the live Conditions below on top of whatever it stamps. --
        box = layout.box()
        box.label(text="Season", icon=helpers.STRUCTURAL_ICON)
        box.prop(env, "season")
        if not firmament_off:
            box.prop(context.scene.bbt_firmament, "season_sets_date")
        helpers.structural_action(
            box, "bob_blender_tools.firmament_apply_season", text="Apply Season",
            note="sets snow/wetness/temperature; winter builds falling snow + coverage")

        # -- World now: the live conditions, on top of the season. Time/place (the set-once sun geo
        # inputs) moved to the collapsed "Time and place" sub-panel below, so this stays day-to-day. --
        box = layout.box()
        col = box.column(align=True)
        col.label(text="Conditions (live)", icon="FORCE_WIND")
        col.prop(env, "weather")
        cap = box.row()
        cap.enabled = False
        cap.label(text="rain/storm wet the ground; combined with Wetness (whichever is higher)")
        col = box.column(align=True)
        col.prop(env, "temperature")
        col.prop(env, "wetness")
        col.prop(env, "snow_line")  # normalized 0..1; 0 = whole map, 1 = peaks clear
        cap = box.row()
        cap.enabled = False
        cap.label(text="below 0C it snows (colder = thicker); Snow Line sets how far down")
        col = box.column(align=True)
        col.prop(env, "cloud_cover")
        col.prop(env, "wind_direction")
        col.prop(env, "wind_strength")
        col.prop(env, "frost")  # overall hoar-frost amount; 0 = clean snow, no frost sheet

        # -- Sky Look: a staged whole-atmosphere mood (time/weather/cloud/wind + subsystems).
        # Sky only: it never touches the season. Needs Firmament (it rebuilds the atmosphere). --
        if not firmament_off:
            box = layout.box()
            box.label(text="Sky Look", icon="WORLD")
            helpers.staged_preset_row(
                box, context.scene.bbt_firmament, "sky_look",
                "bob_blender_tools.firmament_scene_preset", text="Sky Look",
                note="rebuilds the atmosphere subsystems; does not touch the season")


class BBT_PT_world_time(Panel):
    bl_label = "Time and place"
    bl_idname = "BBT_PT_world_time"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "BobBlenderTools"
    bl_parent_id = "BBT_PT_world"
    bl_options = {"DEFAULT_CLOSED"}  # set-once sun geo inputs, folded so World opens on the live knobs

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
        cap = layout.row()
        cap.enabled = False
        cap.label(text="drives the sun live once a sky is built (Atmosphere > Build Sky)")


CLASSES = (
    BBT_WorldProps,
    BBT_OT_world_biome_world,
    BBT_OT_world_apply_biome,
    BBT_PT_biome,
    BBT_PT_world,
    BBT_PT_world_time,  # child of BBT_PT_world; registered after its parent
)


def register():
    global _env
    from ..core import env
    _env = env
    for cls in CLASSES:
        bpy.utils.register_class(cls)
    bpy.types.Scene.bbt_world = bpy.props.PointerProperty(type=BBT_WorldProps)


def unregister():
    del bpy.types.Scene.bbt_world
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
