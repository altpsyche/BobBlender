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

from . import (  # noqa: F401
    firmament_panel,
    scatter_panel,
    server,
    shaders_panel,
    ui_helpers,
    world_panel,
)

# A 2D top-down preview of the last baked heightfield, drawn in the panel. Loaded
# by the bake operator, created in register() and freed in unregister().
_preview_coll = None
_PREVIEW_KEY = "hf"


# Panel presets: picking a landscape family loads a good starting look -- the four
# global knobs reset to neutral (0.5, the preset as authored) and the Blender-side
# display knobs (height, sea level) that suit the family. The table is committed in
# presets.json, generated from the venv presets by tools/scripts/gen_panel_presets.py
# (the single source of truth; a drift test guards it). You then sculpt with the
# knobs; there is no separate "custom" entry -- your knob tweaks ARE the custom look.
def _load_hf_presets():
    """The per-preset slider table (global knobs + display) from the committed JSON."""
    path = os.path.join(os.path.dirname(__file__), "presets.json")
    try:
        with open(path) as fh:
            return json.load(fh).get("presets", {})
    except (OSError, ValueError) as exc:
        print(f"[bob_blender_tools] presets.json not loaded: {exc}")
        return {}


_HF_PRESETS = _load_hf_presets()


def _preset_items(self, context):
    return [(k, k.replace("_", " ").title(), f"Load the {k} landscape") for k in _HF_PRESETS]


def _apply_preset(self, context):
    """Reset the sliders to the chosen preset's neutral look (update callback)."""
    values = _HF_PRESETS.get(self.preset)
    if not values:
        return
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
    # No Material picker (docs/UX-REDESIGN.md decision D): a terrain gets its material by
    # selecting it in the Shaders panel (New BobShader -> Terrain), the one native path.
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
    mesh_res: IntProperty(
        name="Mesh Density", default=384, min=8, soft_max=1024, max=4096,
        description="Grid vertices per side for the displaced terrain mesh, INDEPENDENT of the "
                    "bake resolution. The heightmap keeps its full detail for shading; the mesh "
                    "needs only enough vertices for the silhouette. Matching it to a 2048 bake "
                    "would build ~4M vertices and stall the viewport")
    seed: IntProperty(name="Seed", default=7,
                      description="Random variation; the same seed always gives the same terrain")
    # The five curated global knobs. Each is 0..1 with 0.5 meaning "the preset as
    # authored"; the venv (params.resolve_stack) turns them into the op stack.
    relief: FloatProperty(name="Relief", default=0.5, min=0.0, max=1.0,
                          description="Ruggedness: higher is rockier, more dramatic ridgelines")
    detail: FloatProperty(name="Detail", default=0.5, min=0.0, max=1.0,
                          description="Feature size: higher adds finer octaves and crisper edges")
    erosion: FloatProperty(name="Erosion", default=0.5, min=0.0, max=1.0,
                           description="Incision: higher carves deeper valleys and channels")
    warp: FloatProperty(name="Warp", default=0.5, min=0.0, max=1.0,
                        description="Meander: higher distorts the domain for a more organic look")
    terrain_size: FloatProperty(name="Size m", default=90.0, min=1.0)
    height: FloatProperty(name="Height", default=22.0)
    sea_level: FloatProperty(name="Sea Level", default=0.22, min=0.0, max=1.0)
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
        # basename the free-text target so a value like "../../x" cannot escape _generated
        target = os.path.basename((hf.target or "terrain").strip()) or "terrain"
        out_abs = os.path.join(repo, "library", "_generated", f"{target}_hf.png")
        # Send the preset plus the five global knobs; the venv (build_params ->
        # resolve_stack) turns them into the op stack and applies the preview size,
        # so the panel does not duplicate that logic.
        knobs = {
            "size": hf.resolution, "seed": hf.seed, "backend": hf.backend,
            "preset": hf.preset, "relief": hf.relief, "detail": hf.detail,
            "erosion": hf.erosion, "warp": hf.warp,
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
        # Mesh grid density is DECOUPLED from the bake resolution: the heightmap keeps its full
        # detail (sampled for displacement and shading), but the mesh only needs enough vertices
        # for the silhouette. Matching verts to texels built 0.6M-4.2M verts and stalled the
        # viewport. Cap at the bake size so a low-res preview is not needlessly dense.
        grid_res = min(int(hf.mesh_res), bake_size)
        tparams = {"heightmap": out_abs, "size": hf.terrain_size, "resolution": grid_res,
                   "height": hf.height, "sea_level": hf.sea_level}
        # No material here (decision D): the terrain is shaded from the Shaders panel.
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
# Pipeline panel order (docs/UX-REDESIGN.md section 4): World=0, Terrain=1, Scatter=2,
# Shaders=3, Atmosphere=4, Advanced/Bridge=5. Set via bl_order so the N-panel teaches the
# terrain -> scatter -> shade -> world sequence regardless of registration order (P6). The
# dev/agent Bridge is demoted to a collapsed Advanced panel (decision B): it should not greet
# an artist first, but stays in the tab for when an agent needs the live socket.
class BBT_PT_panel(Panel):
    bl_label = "Advanced"
    bl_idname = "BBT_PT_panel"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "BobBlenderTools"
    bl_order = 5
    bl_options = {"DEFAULT_CLOSED"}

    def draw(self, context):
        layout = self.layout
        layout.label(text="MCP Bridge (agent/dev): live socket + builder reload", icon="CONSOLE")
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
    bl_label = "Terrain"
    bl_idname = "BBT_PT_heightfield"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "BobBlenderTools"
    bl_order = 1  # pipeline stage 1 (Terrain); see BBT_PT_panel comment
    bl_options = {"DEFAULT_CLOSED"}

    def draw(self, context):
        hf = context.scene.bbt_hf
        layout = self.layout

        if _preview_coll is not None and _PREVIEW_KEY in _preview_coll:
            layout.template_icon(icon_value=_preview_coll[_PREVIEW_KEY].icon_id, scale=8)

        # P1: the target terrain object the bake builds (or replaces).
        ui_helpers.context_header(layout, "Terrain object", hf.target, icon="OUTLINER_OB_MESH")
        col = layout.column(align=True)
        col.prop(hf, "target")
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

        # P3: Bake + Build is STRUCTURAL (bakes a heightfield, then builds the mesh); the
        # Shape/Erosion/Displace knobs below are its inputs. Shade the result in Shaders.
        ui_helpers.structural_action(layout, "bob_blender_tools.bake_terrain",
                                     text="Bake + Build Terrain",
                                     note="bakes a heightfield, then builds the terrain mesh")
        layout.label(text="Shade it in the Shaders panel", icon="INFO")
        if hf.last_bake:
            layout.label(text=f"Last: {hf.last_bake}", icon="INFO")


class BBT_PT_hf_shape(Panel):
    bl_label = "Sculpt"
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
        # The five curated global knobs modulate the chosen landscape preset.
        col = layout.column(align=True)
        col.prop(hf, "relief")
        col.prop(hf, "detail")
        col.prop(hf, "erosion")
        col.prop(hf, "warp")


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
        layout.prop(hf, "mesh_res")


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
    firmament_panel.register()  # owns and registers the shared world (bbt_env); subscribes its applier
    world_panel.register()      # World panel + bbt_world master toggles (drive every consumer)
    shaders_panel.register()    # reads bbt_env; subscribes its applier
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
    world_panel.unregister()
    firmament_panel.unregister()
    scatter_panel.unregister()
    del bpy.types.Scene.bbt_hf
    for cls in reversed(_CLASSES):
        bpy.utils.unregister_class(cls)
