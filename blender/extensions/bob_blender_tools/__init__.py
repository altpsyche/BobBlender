"""BobBlenderTools: the Bob procedural suite inside Blender.

Enable this addon once in Preferences > Add-ons. Everything lives in the
BobBlenderTools tab of the View3D N-panel: the MCP Bridge (start/stop the live
socket that lets agents author into this session, plus Reload Builders), the
Heightfield Terrain panel, and (next) Scatter. MCP is one capability here, not the
whole thing. With autostart on, the bridge comes up whenever Blender launches.
"""

import json
import os
import subprocess
import tempfile
import time

import bpy
import bpy.utils.previews
from bpy.props import (
    BoolProperty,
    EnumProperty,
    FloatProperty,
    IntProperty,
    PointerProperty,
    StringProperty,
)
from bpy.types import AddonPreferences, Operator, Panel, PropertyGroup

from . import firmament_panel, scatter_panel, server, shaders_panel

# A 2D top-down preview of the last baked heightfield, drawn in the panel. Loaded
# by the bake operator, created in register() and freed in unregister().
_preview_coll = None
_PREVIEW_KEY = "hf"


# Panel presets: the slider values the dropdown loads, so a good starting look is
# one pick instead of tuning every knob. The generation knobs come from
# presets.json, generated from the venv PRESET_KNOBS by
# tools/scripts/gen_panel_presets.py (the single source of truth; a drift test
# guards it). The display knobs below (height, sea level) are Blender-side
# displacement params, not heightfield-generation params, so they live here.
_HF_DISPLAY = {
    "foothills": {"height": 14.0, "sea_level": 0.30},
    "alpine": {"height": 20.0, "sea_level": 0.26},
    "badlands": {"height": 16.0, "sea_level": 0.30},
    "rolling": {"height": 9.0, "sea_level": 0.32},
    "canyon": {"height": 18.0, "sea_level": 0.28},
    "mesa": {"height": 14.0, "sea_level": 0.30},
    "islands": {"height": 16.0, "sea_level": 0.34},
}
_HF_DISPLAY_DEFAULT = {"height": 14.0, "sea_level": 0.30}


def _load_hf_presets():
    """Merge the generated generation knobs with the panel-side display knobs."""
    path = os.path.join(os.path.dirname(__file__), "presets.json")
    try:
        with open(path) as fh:
            gen = json.load(fh).get("presets", {})
    except (OSError, ValueError) as exc:
        print(f"[bob_blender_tools] presets.json not loaded: {exc}")
        return {}
    return {name: {**knobs, **_HF_DISPLAY.get(name, _HF_DISPLAY_DEFAULT)}
            for name, knobs in gen.items()}


_HF_PRESETS = _load_hf_presets()


def _preset_items(self, context):
    items = [("custom", "Custom", "Your own slider values")]
    items += [(k, k.title(), f"Load the {k} preset") for k in _HF_PRESETS]
    return items


def _apply_preset(self, context):
    """Populate the sliders from the chosen preset (update callback)."""
    values = _HF_PRESETS.get(self.preset)
    if not values:
        return  # "custom": leave the sliders alone
    for key, val in values.items():
        setattr(self, key, val)


def _load_preview(png_path):
    """Refresh the 2D preview from a freshly baked PNG. The file is overwritten
    each bake, so clear the cached thumbnail and reload it. Never fatal."""
    if _preview_coll is None:
        return
    try:
        _preview_coll.clear()
        _preview_coll.load(_PREVIEW_KEY, png_path, "IMAGE")
    except Exception as exc:
        print(f"[bob_blender_tools] preview load failed: {exc}")


# Preferences
class BBT_AddonPreferences(AddonPreferences):
    bl_idname = __package__

    autostart: BoolProperty(
        name="Start bridge on launch",
        description="Automatically start the live MCP bridge when Blender opens",
        default=True,
    )

    def draw(self, context):
        col = self.layout.column()
        col.prop(self, "autostart")
        col.label(text=f"Bridge: {server.status()}", icon="LINKED")


def _prefs():
    return bpy.context.preferences.addons[__package__].preferences


# Operators
class BBT_OT_start(Operator):
    bl_idname = "bob_blender_tools.start"
    bl_label = "Start Bridge"
    bl_description = "Start the live MCP bridge socket"

    def execute(self, context):
        self.report({"INFO"}, server.start())
        return {"FINISHED"}


class BBT_OT_stop(Operator):
    bl_idname = "bob_blender_tools.stop"
    bl_label = "Stop Bridge"
    bl_description = "Stop the live MCP bridge socket"

    def execute(self, context):
        self.report({"INFO"}, server.stop())
        return {"FINISHED"}


class BBT_OT_reload(Operator):
    bl_idname = "bob_blender_tools.reload_builders"
    bl_label = "Reload Builders"
    bl_description = "Refresh bbmcp so new op code is picked up without restarting"

    def execute(self, context):
        self.report({"INFO"}, server.reload_builders())
        return {"FINISHED"}


# Heightfield terrain: bake in the venv, build in place here.
class BBT_HeightfieldProps(PropertyGroup):
    target: StringProperty(name="Object", default="Terrain")
    material: PointerProperty(name="Material", type=bpy.types.Material,
                              description="Material assigned to the terrain surface")
    preset: EnumProperty(name="Preset", items=_preset_items, update=_apply_preset,
                         description="Load a set of slider values")
    backend: EnumProperty(
        name="Backend",
        items=[("auto", "Auto", "Use the GPU when present, else CPU"),
               ("gpu", "GPU", "Force the CuPy CUDA path"),
               ("cpu", "CPU", "Force the numpy reference path")],
        default="auto",
    )
    backend_hint: StringProperty(name="Backends", default="")
    preview: BoolProperty(name="Preview (256)", default=True,
                          description="Bake at 256 for a fast look; off for full resolution")
    resolution: IntProperty(name="Resolution", default=768, min=64, max=4096)
    seed: IntProperty(name="Seed", default=7)
    octaves: IntProperty(name="Octaves", default=5, min=1, max=10)
    ridged: FloatProperty(name="Ridged", default=0.4, min=0.0, max=1.0)
    detail_strength: FloatProperty(name="Detail", default=0.45, min=0.0, max=2.0)
    droplets: IntProperty(name="Droplet Density", default=1_500_000, min=1000, max=8_000_000,
                          description="Droplet count at 768px; the pipeline scales it to the bake resolution")
    erosion: FloatProperty(name="Erosion", default=0.4, min=0.0, max=2.0)
    deposition: FloatProperty(name="Deposition", default=0.4, min=0.0, max=1.0)
    radius: IntProperty(name="Brush", default=4, min=1, max=8)
    max_steps: IntProperty(name="Steps", default=96, min=8, max=256)
    thermal_iters: IntProperty(name="Thermal", default=6, min=0, max=40)
    edge_falloff: FloatProperty(name="Edge Falloff", default=0.0, min=0.0, max=0.5,
                                description="Sink the borders toward sea before erosion; 0 = off (islands, plateaus)")
    terrain_size: FloatProperty(name="Size m", default=90.0, min=1.0)
    height: FloatProperty(name="Height", default=17.0)
    sea_level: FloatProperty(name="Sea Level", default=0.28, min=0.0, max=1.0)
    last_bake: StringProperty(name="Last bake", default="")


class BBT_OT_random_seed(Operator):
    bl_idname = "bob_blender_tools.random_seed"
    bl_label = "Randomize Seed"
    bl_description = "Pick a new random terrain seed"

    def execute(self, context):
        import random

        context.scene.bbt_hf.seed = random.randint(0, 99999)
        return {"FINISHED"}


def _in_steam_container():
    """True when this Blender runs inside the Steam pressure-vessel container.

    There the host venv (its python symlink and CUDA) is not reachable, so a bake
    has to run on the host instead.
    """
    return (
        os.environ.get("container") == "pressure-vessel"
        or os.path.isdir("/run/pressure-vessel")
        or os.path.isdir("/run/host")
    )


def _host_argv(repo, extra):
    """A `python -m bobtools.heightfields <extra>` command that runs on the host.

    Direct venv python when Blender runs natively. Inside the Steam container the
    venv is unreachable, so hop to the host via the Steam runtime launcher (its
    --alongside-steam service), which forwards stdout and the exit code back.
    """
    host_py = os.path.join(repo, "tools", ".venv", "bin", "python")
    inner = [host_py, "-m", "bobtools.heightfields"] + extra
    if _in_steam_container():
        return ["steam-runtime-launch-client", "--alongside-steam", "--"] + inner
    return inner


class BBT_OT_detect_backends(Operator):
    bl_idname = "bob_blender_tools.detect_backends"
    bl_label = "Check Backends"
    bl_description = "Ask the venv which compute backends are available (GPU/CPU)"

    def execute(self, context):
        hf = context.scene.bbt_hf
        repo = os.path.dirname(server._repo_blender_dir())
        argv = _host_argv(repo, ["--backends"])
        try:
            proc = subprocess.run(argv, capture_output=True, text=True, timeout=120)
        except FileNotFoundError:
            hf.backend_hint = "venv unreachable"
            self.report({"WARNING"}, "bake runner not found (see bake button tooltip)")
            return {"CANCELLED"}
        except Exception as exc:
            hf.backend_hint = "probe failed"
            self.report({"ERROR"}, f"probe failed: {exc}")
            return {"CANCELLED"}
        lines = (proc.stdout or "").strip().splitlines()
        try:
            data = json.loads(lines[-1]) if lines else {}
        except ValueError:
            data = {}
        names = data.get("available", [])
        if "gpu" in names:
            hf.backend_hint = f"GPU ready ({data.get('device', 'cuda')})"
        elif names:
            hf.backend_hint = "CPU only"
        else:
            hf.backend_hint = "none reported"
        return {"FINISHED"}


class BBT_OT_bake_terrain(Operator):
    bl_idname = "bob_blender_tools.bake_terrain"
    bl_label = "Bake + Build Terrain"
    bl_description = "Bake an eroded heightfield in the venv (GPU), then build the terrain in place"

    def execute(self, context):
        hf = context.scene.bbt_hf
        repo = os.path.dirname(server._repo_blender_dir())
        out_abs = os.path.join(repo, "library", "_generated", f"{hf.target}_hf.png")
        # Send flat knobs; the pipeline (build_params + preview) expands the pass
        # list and scales droplet density to the bake resolution, so the panel does
        # not duplicate that logic. Droplets is a density at 768px.
        knobs = {
            "size": hf.resolution, "seed": hf.seed, "backend": hf.backend,
            "octaves": hf.octaves, "roughness": 0.5, "ridged": hf.ridged,
            "detail_strength": hf.detail_strength, "warp": 90,
            "droplets": hf.droplets, "erosion": hf.erosion, "deposition": hf.deposition,
            "radius": hf.radius, "max_steps": hf.max_steps, "thermal_iters": hf.thermal_iters,
            "edge_falloff": hf.edge_falloff,
        }

        tmp = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False)
        json.dump(knobs, tmp)
        tmp.close()
        t0 = time.perf_counter()
        extra = ["--out", out_abs, "--knobs-file", tmp.name, "--force"]
        if hf.preview:
            extra.append("--preview")
        argv = _host_argv(repo, extra)
        # Blocking bake with feedback: a wait cursor and the progress spinner, so
        # the UI shows work instead of looking hung. (A window only exists when
        # Blender runs with a UI, not headless.)
        wm = context.window_manager
        window = context.window
        if window:
            window.cursor_set("WAIT")
        wm.progress_begin(0, 1)
        try:
            proc = subprocess.run(argv, capture_output=True, text=True, timeout=600)
        except FileNotFoundError:
            self.report(
                {"ERROR"},
                "bake runner not found. Inside Steam, the host bake needs "
                "steam-runtime-launch-client; or launch Blender directly (not via "
                "Steam) so the tools venv is reachable.",
            )
            return {"CANCELLED"}
        except Exception as exc:  # subprocess never launched
            self.report({"ERROR"}, f"bake could not run: {exc}")
            return {"CANCELLED"}
        finally:
            os.unlink(tmp.name)
            wm.progress_end()
            if window:
                window.cursor_set("DEFAULT")

        if proc.returncode != 0:
            self.report({"ERROR"}, f"bake failed: {(proc.stderr or proc.stdout or '').strip()[-200:]}")
            return {"CANCELLED"}
        lines = (proc.stdout or "").strip().splitlines()
        try:
            meta = json.loads(lines[-1]) if lines else {}
        except ValueError:
            meta = {}

        # Build the terrain in place from the fresh PNG.
        server._ensure_path()
        from bbmcp.dispatch import apply_op

        # The pipeline decides the actual bake size (preview overrides it), so
        # take it from the returned metadata for the terrain grid resolution.
        bake_size = int(meta.get("size", hf.resolution))
        apply_op({"op": "reload_image", "path": out_abs})
        tparams = {"heightmap": out_abs, "size": hf.terrain_size, "resolution": bake_size,
                   "height": hf.height, "sea_level": hf.sea_level}
        if hf.material:
            tparams["material"] = hf.material.name
        apply_op({"op": "build_geonodes", "recipe": "heightmap_terrain",
                  "name": hf.target, "params": tparams})

        _load_preview(out_abs)

        actual = meta.get("backend", "?")
        dt = time.perf_counter() - t0
        hf.last_bake = f"{actual} {meta.get('platform', '')} {bake_size}px  {dt:.1f}s"
        if hf.backend in ("auto", "gpu") and actual == "cpu":
            self.report({"WARNING"},
                        "GPU not used, baked on CPU (slower). Press Check Backends.")
        else:
            self.report({"INFO"},
                        f"Baked {actual} {bake_size}px in {dt:.1f}s -> {hf.target}")
        return {"FINISHED"}


# Panel
class BBT_PT_panel(Panel):
    bl_label = "MCP Bridge"
    bl_idname = "BBT_PT_panel"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "BobBlenderTools"

    def draw(self, context):
        layout = self.layout
        running = server.is_running()
        layout.label(
            text=server.status(),
            icon="PROP_ON" if running else "PROP_OFF",
        )
        row = layout.row(align=True)
        row.operator("bob_blender_tools.start", icon="PLAY", text="Start")
        row.operator("bob_blender_tools.stop", icon="PAUSE", text="Stop")
        layout.operator("bob_blender_tools.reload_builders", icon="FILE_REFRESH")


class BBT_PT_heightfield(Panel):
    bl_label = "Heightfield Terrain"
    bl_idname = "BBT_PT_heightfield"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "BobBlenderTools"
    bl_options = {"DEFAULT_CLOSED"}

    def draw(self, context):
        hf = context.scene.bbt_hf
        layout = self.layout

        if _preview_coll is not None and _PREVIEW_KEY in _preview_coll:
            layout.template_icon(icon_value=_preview_coll[_PREVIEW_KEY].icon_id, scale=8)

        col = layout.column(align=True)
        col.prop(hf, "target")
        col.prop(hf, "material")
        layout.prop(hf, "preset")

        row = layout.row(align=True)
        row.prop(hf, "backend", expand=True)
        row.operator("bob_blender_tools.detect_backends", text="", icon="QUESTION")
        if hf.backend_hint:
            icon = "ERROR" if hf.backend_hint.startswith(("CPU", "none", "venv", "probe")) else "INFO"
            layout.label(text=hf.backend_hint, icon=icon)

        row = layout.row(align=True)
        row.prop(hf, "preview")
        row.prop(hf, "resolution")

        layout.operator("bob_blender_tools.bake_terrain", icon="MOD_OCEAN", text="Bake + Build Terrain")
        if hf.last_bake:
            layout.label(text=f"Last: {hf.last_bake}", icon="INFO")


class BBT_PT_hf_shape(Panel):
    bl_label = "Shape"
    bl_idname = "BBT_PT_hf_shape"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "BobBlenderTools"
    bl_parent_id = "BBT_PT_heightfield"
    bl_options = {"DEFAULT_CLOSED"}

    def draw(self, context):
        hf = context.scene.bbt_hf
        layout = self.layout
        row = layout.row(align=True)
        row.prop(hf, "seed")
        row.operator("bob_blender_tools.random_seed", text="", icon="FILE_REFRESH")
        layout.prop(hf, "octaves")
        layout.prop(hf, "ridged")
        layout.prop(hf, "detail_strength")


class BBT_PT_hf_erosion(Panel):
    bl_label = "Erosion"
    bl_idname = "BBT_PT_hf_erosion"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "BobBlenderTools"
    bl_parent_id = "BBT_PT_heightfield"
    bl_options = {"DEFAULT_CLOSED"}

    def draw(self, context):
        hf = context.scene.bbt_hf
        layout = self.layout
        layout.prop(hf, "droplets")
        layout.prop(hf, "erosion")
        layout.prop(hf, "deposition")
        layout.prop(hf, "radius")
        layout.prop(hf, "max_steps")
        layout.prop(hf, "thermal_iters")
        layout.prop(hf, "edge_falloff")


class BBT_PT_hf_displace(Panel):
    bl_label = "Displace"
    bl_idname = "BBT_PT_hf_displace"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "BobBlenderTools"
    bl_parent_id = "BBT_PT_heightfield"
    bl_options = {"DEFAULT_CLOSED"}

    def draw(self, context):
        hf = context.scene.bbt_hf
        layout = self.layout
        layout.prop(hf, "terrain_size")
        layout.prop(hf, "height")
        layout.prop(hf, "sea_level")


_CLASSES = (
    BBT_AddonPreferences,
    BBT_HeightfieldProps,
    BBT_OT_start,
    BBT_OT_stop,
    BBT_OT_reload,
    BBT_OT_random_seed,
    BBT_OT_detect_backends,
    BBT_OT_bake_terrain,
    BBT_PT_panel,
    BBT_PT_heightfield,
    BBT_PT_hf_shape,
    BBT_PT_hf_erosion,
    BBT_PT_hf_displace,
)


def _autostart():
    try:
        if _prefs().autostart and not server.is_running():
            server.start()
    except Exception as exc:  # never let autostart break registration
        print(f"[bob_blender_tools] autostart skipped: {exc}")
    return None  # one-shot timer


def register():
    global _preview_coll
    for cls in _CLASSES:
        bpy.utils.register_class(cls)
    bpy.types.Scene.bbt_hf = PointerProperty(type=BBT_HeightfieldProps)
    scatter_panel.register()
    firmament_panel.register()  # owns and registers the shared world (bbt_env)
    shaders_panel.register()    # reads bbt_env; registers after Firmament owns it
    _preview_coll = bpy.utils.previews.new()
    # Defer autostart until prefs are available.
    bpy.app.timers.register(_autostart, first_interval=0.2)


def unregister():
    global _preview_coll
    server.stop()
    if _preview_coll is not None:
        bpy.utils.previews.remove(_preview_coll)
        _preview_coll = None
    shaders_panel.unregister()
    firmament_panel.unregister()
    scatter_panel.unregister()
    del bpy.types.Scene.bbt_hf
    for cls in reversed(_CLASSES):
        bpy.utils.unregister_class(cls)
