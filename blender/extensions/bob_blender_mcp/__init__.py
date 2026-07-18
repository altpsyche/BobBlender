"""Bob Blender MCP: enable this addon to let MCP author into your live Blender.

Enable it once in Preferences > Add-ons. With autostart on, the socket bridge
comes up whenever Blender launches, with no scripts to paste. The N-panel
(View3D sidebar > BobMCP) has Start/Stop and a Reload Builders button.
"""

import json
import os
import subprocess
import tempfile
import time

import bpy
from bpy.props import (
    BoolProperty,
    EnumProperty,
    FloatProperty,
    IntProperty,
    PointerProperty,
    StringProperty,
)
from bpy.types import AddonPreferences, Operator, Panel, PropertyGroup

from . import server


# Preferences
class BBMCP_AddonPreferences(AddonPreferences):
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
class BBMCP_OT_start(Operator):
    bl_idname = "bob_blender_mcp.start"
    bl_label = "Start Bridge"
    bl_description = "Start the live MCP bridge socket"

    def execute(self, context):
        self.report({"INFO"}, server.start())
        return {"FINISHED"}


class BBMCP_OT_stop(Operator):
    bl_idname = "bob_blender_mcp.stop"
    bl_label = "Stop Bridge"
    bl_description = "Stop the live MCP bridge socket"

    def execute(self, context):
        self.report({"INFO"}, server.stop())
        return {"FINISHED"}


class BBMCP_OT_reload(Operator):
    bl_idname = "bob_blender_mcp.reload_builders"
    bl_label = "Reload Builders"
    bl_description = "Refresh bbmcp so new op code is picked up without restarting"

    def execute(self, context):
        self.report({"INFO"}, server.reload_builders())
        return {"FINISHED"}


# Heightfield terrain: bake in the venv, build in place here.
class BBMCP_HeightfieldProps(PropertyGroup):
    target: StringProperty(name="Object", default="Terrain")
    material: StringProperty(name="Material", default="")
    backend: EnumProperty(
        name="Backend",
        items=[("auto", "Auto", ""), ("gpu", "GPU", ""), ("cpu", "CPU", "")],
        default="auto",
    )
    preview: BoolProperty(name="Preview (256)", default=True,
                          description="Bake at 256 for a fast look; off for full resolution")
    resolution: IntProperty(name="Resolution", default=768, min=64, max=4096)
    seed: IntProperty(name="Seed", default=7)
    octaves: IntProperty(name="Octaves", default=5, min=1, max=10)
    ridged: FloatProperty(name="Ridged", default=0.4, min=0.0, max=1.0)
    detail_strength: FloatProperty(name="Detail", default=0.45, min=0.0, max=2.0)
    droplets: IntProperty(name="Droplets", default=1_500_000, min=1000, max=8_000_000)
    erosion: FloatProperty(name="Erosion", default=0.4, min=0.0, max=2.0)
    deposition: FloatProperty(name="Deposition", default=0.4, min=0.0, max=1.0)
    radius: IntProperty(name="Brush", default=4, min=1, max=8)
    max_steps: IntProperty(name="Steps", default=96, min=8, max=256)
    thermal_iters: IntProperty(name="Thermal", default=6, min=0, max=40)
    terrain_size: FloatProperty(name="Size m", default=90.0, min=1.0)
    height: FloatProperty(name="Height", default=17.0)
    sea_level: FloatProperty(name="Sea Level", default=0.28, min=0.0, max=1.0)


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


def _bake_argv(repo, out_abs, params_file):
    """The command that bakes the heightfield in the venv (numpy/CuPy) on the host.

    Direct venv python when Blender runs natively. Inside the Steam container the
    venv is unreachable, so hop to the host via the Steam runtime launcher (its
    --alongside-steam service), which forwards stdout and the exit code back.
    """
    host_py = os.path.join(repo, "tools", ".venv", "bin", "python")
    inner = [host_py, "-m", "bobtools.heightfields",
             "--out", out_abs, "--params-file", params_file, "--force"]
    if _in_steam_container():
        return ["steam-runtime-launch-client", "--alongside-steam", "--"] + inner
    return inner


class BBMCP_OT_bake_terrain(Operator):
    bl_idname = "bob_blender_mcp.bake_terrain"
    bl_label = "Bake + Build Terrain"
    bl_description = "Bake an eroded heightfield in the venv (GPU), then build the terrain in place"

    def execute(self, context):
        hf = context.scene.bbmcp_hf
        repo = os.path.dirname(server._repo_blender_dir())
        out_abs = os.path.join(repo, "library", "_generated", f"{hf.target}_hf.png")
        size = 256 if hf.preview else hf.resolution
        # Droplet count is a density: it must scale with cell count or a low-res
        # (preview) bake is massively over-eroded and comes out flat. The slider
        # is the count at 768; scale it to the actual bake size.
        droplets = max(20000, int(hf.droplets * (size / 768.0) ** 2))
        params = {
            "size": size, "seed": hf.seed, "backend": hf.backend,
            "generate": {"octaves": hf.octaves, "roughness": 0.5, "ridged": hf.ridged,
                         "detail_strength": hf.detail_strength, "warp": 90},
            "passes": [
                {"kind": "smooth", "sigma": 1.5},
                {"kind": "hydraulic", "droplets": droplets, "erosion": hf.erosion,
                 "deposition": hf.deposition, "capacity": 8, "max_steps": hf.max_steps,
                 "radius": hf.radius},
                {"kind": "thermal", "talus": 0.005, "factor": 0.4, "iterations": hf.thermal_iters},
                {"kind": "smooth", "sigma": 0.8},
            ],
        }

        tmp = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False)
        json.dump(params, tmp)
        tmp.close()
        t0 = time.perf_counter()
        argv = _bake_argv(repo, out_abs, tmp.name)
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

        apply_op({"op": "reload_image", "path": out_abs})
        tparams = {"heightmap": out_abs, "size": hf.terrain_size, "resolution": size,
                   "height": hf.height, "sea_level": hf.sea_level}
        if hf.material:
            tparams["material"] = hf.material
        apply_op({"op": "build_geonodes", "recipe": "heightmap_terrain",
                  "name": hf.target, "params": tparams})

        dt = time.perf_counter() - t0
        self.report({"INFO"},
                    f"Baked {meta.get('backend', '?')} {size}px in {dt:.1f}s -> {hf.target}")
        return {"FINISHED"}


# Panel
class BBMCP_PT_panel(Panel):
    bl_label = "Bob Blender MCP"
    bl_idname = "BBMCP_PT_panel"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "BobMCP"

    def draw(self, context):
        layout = self.layout
        running = server.is_running()
        layout.label(
            text=server.status(),
            icon="PROP_ON" if running else "PROP_OFF",
        )
        row = layout.row(align=True)
        row.operator("bob_blender_mcp.start", icon="PLAY", text="Start")
        row.operator("bob_blender_mcp.stop", icon="PAUSE", text="Stop")
        layout.operator("bob_blender_mcp.reload_builders", icon="FILE_REFRESH")


class BBMCP_PT_heightfield(Panel):
    bl_label = "Heightfield Terrain"
    bl_idname = "BBMCP_PT_heightfield"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "BobMCP"
    bl_options = {"DEFAULT_CLOSED"}

    def draw(self, context):
        hf = context.scene.bbmcp_hf
        layout = self.layout

        col = layout.column(align=True)
        col.prop(hf, "target")
        col.prop(hf, "material")
        layout.row(align=True).prop(hf, "backend", expand=True)
        row = layout.row(align=True)
        row.prop(hf, "preview")
        row.prop(hf, "resolution")

        box = layout.box()
        box.label(text="Shape", icon="RNDCURVE")
        box.prop(hf, "seed")
        box.prop(hf, "octaves")
        box.prop(hf, "ridged")
        box.prop(hf, "detail_strength")

        box = layout.box()
        box.label(text="Erosion", icon="MOD_FLUIDSIM")
        box.prop(hf, "droplets")
        box.prop(hf, "erosion")
        box.prop(hf, "deposition")
        box.prop(hf, "radius")
        box.prop(hf, "max_steps")
        box.prop(hf, "thermal_iters")

        box = layout.box()
        box.label(text="Displace", icon="MOD_DISPLACE")
        box.prop(hf, "terrain_size")
        box.prop(hf, "height")
        box.prop(hf, "sea_level")

        layout.operator("bob_blender_mcp.bake_terrain", icon="MOD_OCEAN", text="Bake + Build Terrain")


_CLASSES = (
    BBMCP_AddonPreferences,
    BBMCP_HeightfieldProps,
    BBMCP_OT_start,
    BBMCP_OT_stop,
    BBMCP_OT_reload,
    BBMCP_OT_bake_terrain,
    BBMCP_PT_panel,
    BBMCP_PT_heightfield,
)


def _autostart():
    try:
        if _prefs().autostart and not server.is_running():
            server.start()
    except Exception as exc:  # never let autostart break registration
        print(f"[bob_blender_mcp] autostart skipped: {exc}")
    return None  # one-shot timer


def register():
    for cls in _CLASSES:
        bpy.utils.register_class(cls)
    bpy.types.Scene.bbmcp_hf = PointerProperty(type=BBMCP_HeightfieldProps)
    # Defer autostart until prefs are available.
    bpy.app.timers.register(_autostart, first_interval=0.2)


def unregister():
    server.stop()
    del bpy.types.Scene.bbmcp_hf
    for cls in reversed(_CLASSES):
        bpy.utils.unregister_class(cls)
