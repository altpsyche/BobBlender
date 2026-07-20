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
    CollectionProperty,
    EnumProperty,
    FloatProperty,
    IntProperty,
    PointerProperty,
    StringProperty,
)
from bpy.types import AddonPreferences, Operator, Panel, PropertyGroup, UIList

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


# Filter-stack editor (P4). The venv terrain engine evaluates an ordered op stack
# (generators write a base, filters and erosion shape it, selectors mask where a
# filter acts). This exposes that stack in the panel: a preset is a starting point
# the artist loads and then edits op by op. Each op kind draws only its own params;
# a hidden `raw` string carries any params the panel does not surface (fill_iters,
# stream-power exponents, ...) so loading a preset and re-baking is faithful.
#
# Field names on BBT_TerrainOp match the engine's op-param keys, so building the
# stack dict is a direct read (see _op_to_dict). One field is shared across kinds
# when the meaning is compatible (iterations, amount, frequency, sharpness).
_OP_META = {
    "noise": ("Noise (base)", "RNDCURVE"),
    "dunes": ("Dunes", "FORCE_WIND"),
    "voronoi": ("Voronoi (cells)", "MESH_ICOSPHERE"),
    "fluvial": ("Fluvial erosion", "MOD_FLUIDSIM"),
    "pipe_hydraulic": ("Hydraulic erosion", "MOD_OCEAN"),
    "thermal": ("Thermal slump", "MOD_SMOOTH"),
    "terrace": ("Terrace", "ALIGN_FLUSH"),
    "warp": ("Domain warp", "MOD_WARP"),
    "curve": ("Curve remap", "FCURVE"),
    "sharpen": ("Sharpen", "SHARPCURVE"),
    "smooth": ("Smooth", "MOD_SMOOTH"),
    "falloff": ("Falloff (coast)", "MOD_EDGESPLIT"),
}

# Fields each kind exposes in the UI (and writes into the stack dict).
_OP_PARAMS = {
    "noise": ["ridged", "detail_strength", "octaves", "warp", "mix", "amount"],
    "dunes": ["wind", "frequency", "sharpness", "variation", "mix", "amount"],
    "voronoi": ["cells", "pattern", "mix", "amount"],
    "fluvial": ["iterations", "k", "diffusion"],
    "pipe_hydraulic": ["iterations", "rain", "incision"],
    "thermal": ["talus", "iterations"],
    "terrace": ["steps", "sharpness"],
    "warp": ["frequency", "amount"],
    "curve": ["gamma", "contrast"],
    "sharpen": ["amount", "radius"],
    "smooth": ["sigma"],
    "falloff": ["shape", "margin", "power"],
}

# Values a freshly ADDED op starts with (fields, plus "_raw" for the params the panel
# does not surface but the engine op needs to behave well).
_OP_ADD_DEFAULTS = {
    "noise": {"ridged": 0.4, "detail_strength": 0.5, "octaves": 5, "warp": 70.0,
              "mix": "replace", "amount": 1.0},
    "dunes": {"wind": 35.0, "frequency": 12.0, "sharpness": 2.2, "variation": 0.5,
              "mix": "replace", "amount": 0.5},
    "voronoi": {"cells": 6.0, "pattern": "mesa", "mix": "multiply", "amount": 0.8},
    "fluvial": {"iterations": 60, "k": 0.015, "diffusion": 0.08,
                "_raw": {"sp_m": 0.5, "sp_n": 1.0, "recompute": 20, "fill_iters": 700,
                         "acc_iters": 700, "thermal_iters": 1, "max_delta": 0.03,
                         "talus": 0.004}},
    "pipe_hydraulic": {"iterations": 120, "rain": 0.012, "incision": 0.4,
                       "_raw": {"dt": 0.1, "capacity": 0.3, "dissolve": 0.25,
                                "deposit": 0.3, "evaporate": 0.02, "min_slope": 0.03,
                                "sp_m": 1.0, "sp_n": 1.2}},
    "thermal": {"talus": 0.01, "iterations": 4, "_raw": {"factor": 0.5}},
    "terrace": {"steps": 6, "sharpness": 0.8},
    "warp": {"frequency": 3.0, "amount": 0.04},
    "curve": {"gamma": 1.0, "contrast": 0.0},
    "sharpen": {"amount": 0.4, "radius": 1.5},
    "smooth": {"sigma": 1.0},
    "falloff": {"shape": "edge", "margin": 0.2, "power": 2.0},
}

_MASK_ITEMS = [
    ("none", "No mask", "The op acts everywhere"),
    ("height", "Height band", "Only within a height band"),
    ("slope", "Slope band", "Only within a slope band"),
    ("curvature", "Curvature", "Ridges/rims vs valleys/hollows"),
    ("flow", "Flow", "Only in channels that collect drainage"),
    ("noise", "Noise", "A noise-broken region"),
]


class BBT_TerrainOp(PropertyGroup):
    kind: EnumProperty(
        name="Op", items=[(k, v[0], v[0], v[1], i) for i, (k, v) in enumerate(_OP_META.items())])
    enabled: BoolProperty(name="Enabled", default=True,
                          description="Uncheck to skip this op in the bake")
    raw: StringProperty(default="{}", options={"HIDDEN"})  # non-surfaced params, verbatim
    # shape / generators
    ridged: FloatProperty(name="Ridged", default=0.4, min=0.0, max=1.0)
    detail_strength: FloatProperty(name="Detail", default=0.5, min=0.0, max=2.0)
    octaves: IntProperty(name="Octaves", default=5, min=1, max=10)
    warp: FloatProperty(name="Warp", default=70.0, min=0.0, max=200.0)
    mix: EnumProperty(name="Mix", default="replace",
                      items=[("replace", "Replace", "Overwrite the field"),
                             ("add", "Add", "Add onto the field"),
                             ("multiply", "Multiply", "Modulate the field"),
                             ("max", "Max", "Keep the higher of the two")])
    amount: FloatProperty(name="Amount", default=0.5, min=0.0, max=2.0)
    wind: FloatProperty(name="Wind", default=35.0, min=0.0, max=180.0)
    frequency: FloatProperty(name="Frequency", default=12.0, min=0.5, max=40.0)
    sharpness: FloatProperty(name="Sharpness", default=2.0, min=0.0, max=6.0)
    variation: FloatProperty(name="Variation", default=0.5, min=0.0, max=1.0)
    cells: FloatProperty(name="Cells", default=6.0, min=2.0, max=24.0)
    pattern: EnumProperty(name="Pattern", default="mesa",
                          items=[("mesa", "Mesa", "Flat-topped cells"),
                                 ("crack", "Crack", "Ridged cell borders")])
    # erosion
    iterations: IntProperty(name="Iterations", default=60, min=0, max=400)
    k: FloatProperty(name="Strength (k)", default=0.015, min=0.0, max=0.06, precision=4)
    diffusion: FloatProperty(name="Diffusion", default=0.08, min=0.0, max=0.4, precision=3)
    rain: FloatProperty(name="Rain", default=0.012, min=0.0, max=0.2, precision=4)
    incision: FloatProperty(name="Incision", default=0.4, min=0.0, max=2.0)
    talus: FloatProperty(name="Talus", default=0.01, min=0.0, max=0.06, precision=4)
    # filters
    steps: IntProperty(name="Steps", default=6, min=1, max=24)
    gamma: FloatProperty(name="Gamma", default=1.0, min=0.2, max=3.0)
    contrast: FloatProperty(name="Contrast", default=0.0, min=-1.0, max=1.0)
    radius: FloatProperty(name="Radius", default=1.5, min=0.3, max=6.0)
    sigma: FloatProperty(name="Sigma", default=1.0, min=0.0, max=6.0)
    shape: EnumProperty(name="Shape", default="edge",
                        items=[("edge", "Edge", "Sink all four borders"),
                               ("radial", "Radial", "A round island"),
                               ("gradient", "Gradient", "A shoreline across the scene")])
    margin: FloatProperty(name="Margin", default=0.2, min=0.02, max=1.0)
    power: FloatProperty(name="Power", default=2.0, min=0.2, max=6.0)
    # per-op mask (a selector that gates where the op applies)
    mask_kind: EnumProperty(name="Mask", items=_MASK_ITEMS, default="none")
    mask_low: FloatProperty(name="Low", default=0.0, min=0.0, max=1.0)
    mask_high: FloatProperty(name="High", default=1.0, min=0.0, max=1.0)
    mask_falloff: FloatProperty(name="Falloff", default=0.1, min=0.0, max=1.0)


def _op_to_dict(op):
    """A BBT_TerrainOp -> the engine op dict, merging surfaced fields over `raw`."""
    try:
        d = json.loads(op.raw) if op.raw else {}
    except ValueError:
        d = {}
    d["kind"] = op.kind
    for key in _OP_PARAMS.get(op.kind, []):
        d[key] = getattr(op, key)
    if op.mask_kind and op.mask_kind != "none":
        d["mask"] = {"kind": op.mask_kind, "low": op.mask_low,
                     "high": op.mask_high, "falloff": op.mask_falloff}
    else:
        d.pop("mask", None)
    return d


def _load_op(op, d):
    """Populate a BBT_TerrainOp from an engine op dict (from a preset stack)."""
    op.kind = d.get("kind", "noise")
    exposed = set(_OP_PARAMS.get(op.kind, []))
    for key in exposed:
        if key in d:
            setattr(op, key, d[key])
    mask = d.get("mask")
    if mask:
        op.mask_kind = mask.get("kind", "none")
        op.mask_low = mask.get("low", 0.0)
        op.mask_high = mask.get("high", 1.0)
        op.mask_falloff = mask.get("falloff", 0.1)
    else:
        op.mask_kind = "none"
    op.raw = json.dumps({k: v for k, v in d.items()
                         if k not in exposed and k not in ("kind", "mask")})
    op.enabled = True


def _stack_from_ops(hf):
    """The enabled ops as an engine stack list."""
    return [_op_to_dict(op) for op in hf.ops if op.enabled]


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
    emit_maps: BoolProperty(
        name="Flow + wetness maps", default=False,
        description="Also bake <name>_flow.png and <name>_wetness.png beside the height, "
                    "for shading and scatter to key off the terrain's own drainage")
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
    # P4 filter-stack editor: an editable op stack. When use_custom_stack is on and
    # the stack is non-empty, the bake runs it instead of the preset + global knobs.
    use_custom_stack: BoolProperty(
        name="Use custom stack", default=False,
        description="Bake the editable op stack below instead of the preset + knobs")
    ops: CollectionProperty(type=BBT_TerrainOp)
    active_op: IntProperty(name="Active op", default=0)


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
        # Either send the edited op stack verbatim (P4 custom mode), or the preset
        # plus the five global knobs; the venv turns knobs into a stack and applies
        # the preview size, so the panel does not duplicate that logic.
        if hf.use_custom_stack and len(hf.ops):
            knobs = {"size": hf.resolution, "seed": hf.seed, "backend": hf.backend,
                     "stack": _stack_from_ops(hf)}
        else:
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
        if hf.emit_maps:
            extra.append("--maps")
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


# Filter-stack editor operators (P4): add / remove / reorder ops, and load a
# preset's stack in to edit. The stack lives on bbt_hf.ops (a CollectionProperty).
class BBT_OT_terrain_op_add(Operator):
    bl_idname = "bob_blender_tools.terrain_op_add"
    bl_label = "Add Op"
    bl_description = "Add a terrain op to the stack"
    bl_options = {"REGISTER", "UNDO"}

    kind: EnumProperty(name="Op",
                       items=[(k, v[0], v[0], v[1], i) for i, (k, v) in enumerate(_OP_META.items())])

    def execute(self, context):
        hf = context.scene.bbt_hf
        op = hf.ops.add()
        op.kind = self.kind
        defaults = _OP_ADD_DEFAULTS.get(self.kind, {})
        for key, val in defaults.items():
            if key == "_raw":
                op.raw = json.dumps(val)
            else:
                setattr(op, key, val)
        if "_raw" not in defaults:
            op.raw = "{}"
        hf.active_op = len(hf.ops) - 1
        hf.use_custom_stack = True  # adding an op means you want to bake the stack
        return {"FINISHED"}


class BBT_OT_terrain_op_remove(Operator):
    bl_idname = "bob_blender_tools.terrain_op_remove"
    bl_label = "Remove Op"
    bl_description = "Remove the active op from the stack"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        hf = context.scene.bbt_hf
        if not hf.ops:
            return {"CANCELLED"}
        hf.ops.remove(hf.active_op)
        hf.active_op = max(0, min(hf.active_op, len(hf.ops) - 1))
        return {"FINISHED"}


class BBT_OT_terrain_op_move(Operator):
    bl_idname = "bob_blender_tools.terrain_op_move"
    bl_label = "Move Op"
    bl_description = "Reorder the active op (order matters: the stack runs top to bottom)"
    bl_options = {"REGISTER", "UNDO"}

    direction: EnumProperty(items=[("UP", "Up", ""), ("DOWN", "Down", "")])

    def execute(self, context):
        hf = context.scene.bbt_hf
        i = hf.active_op
        j = i - 1 if self.direction == "UP" else i + 1
        if 0 <= j < len(hf.ops):
            hf.ops.move(i, j)
            hf.active_op = j
        return {"FINISHED"}


class BBT_OT_terrain_load_preset_stack(Operator):
    bl_idname = "bob_blender_tools.terrain_load_preset_stack"
    bl_label = "Load Preset Stack"
    bl_description = "Replace the stack with the current landscape preset's ops, to edit"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        hf = context.scene.bbt_hf
        path = os.path.join(os.path.dirname(__file__), "presets.json")
        try:
            with open(path) as fh:
                stacks = json.load(fh).get("stacks", {})
        except (OSError, ValueError) as exc:
            self.report({"ERROR"}, f"presets.json not readable: {exc}")
            return {"CANCELLED"}
        ops = stacks.get(hf.preset)
        if not ops:
            self.report({"ERROR"}, f"no stack for preset {hf.preset!r}")
            return {"CANCELLED"}
        hf.ops.clear()
        for d in ops:
            _load_op(hf.ops.add(), d)
        hf.active_op = 0
        hf.use_custom_stack = True
        self.report({"INFO"}, f"Loaded {hf.preset} stack ({len(ops)} ops) to edit")
        return {"FINISHED"}


class BBT_UL_terrain_ops(UIList):
    def draw_item(self, context, layout, data, item, icon, active_data, active_prop, index):
        label, ic = _OP_META.get(item.kind, (item.kind, "DOT"))
        row = layout.row(align=True)
        row.label(text=label, icon=ic)
        mask = item.mask_kind if item.mask_kind != "none" else ""
        if mask:
            row.label(text="", icon="MOD_MASK")
        row.prop(item, "enabled", text="", emboss=False,
                 icon="CHECKBOX_HLT" if item.enabled else "CHECKBOX_DEHLT")


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
        layout.prop(hf, "emit_maps")

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


class BBT_PT_hf_stack(Panel):
    bl_label = "Filter Stack (advanced)"
    bl_idname = "BBT_PT_hf_stack"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "BobBlenderTools"
    bl_parent_id = "BBT_PT_heightfield"
    bl_options = {"DEFAULT_CLOSED"}

    def draw(self, context):
        hf = context.scene.bbt_hf
        layout = self.layout

        # Custom mode toggle + load the current preset's ops to edit.
        row = layout.row(align=True)
        row.prop(hf, "use_custom_stack")
        row.operator("bob_blender_tools.terrain_load_preset_stack",
                     text=f"Load {hf.preset.replace('_', ' ').title()}", icon="IMPORT")
        if not hf.use_custom_stack:
            layout.label(text="Off: the bake uses the preset + Sculpt knobs", icon="INFO")

        # The op list plus add/remove/reorder controls. Order matters: top to bottom.
        row = layout.row()
        row.template_list("BBT_UL_terrain_ops", "", hf, "ops", hf, "active_op", rows=4)
        col = row.column(align=True)
        col.operator_menu_enum("bob_blender_tools.terrain_op_add", "kind", text="", icon="ADD")
        col.operator("bob_blender_tools.terrain_op_remove", text="", icon="REMOVE")
        col.separator()
        col.operator("bob_blender_tools.terrain_op_move", text="", icon="TRIA_UP").direction = "UP"
        col.operator("bob_blender_tools.terrain_op_move", text="", icon="TRIA_DOWN").direction = "DOWN"

        # Params for the active op, then its optional mask.
        if 0 <= hf.active_op < len(hf.ops):
            op = hf.ops[hf.active_op]
            box = layout.box()
            label = _OP_META.get(op.kind, (op.kind,))[0]
            box.label(text=label, icon=_OP_META.get(op.kind, ("", "DOT"))[1])
            for key in _OP_PARAMS.get(op.kind, []):
                box.prop(op, key)
            box.prop(op, "mask_kind")
            if op.mask_kind in ("height", "slope"):
                sub = box.row(align=True)
                sub.prop(op, "mask_low"); sub.prop(op, "mask_high"); sub.prop(op, "mask_falloff")


_CLASSES = (
    BBT_TerrainOp,
    BBT_AddonPreferences,
    BBT_HeightfieldProps,
    BBT_OT_start,
    BBT_OT_stop,
    BBT_OT_reload,
    BBT_OT_random_seed,
    BBT_OT_detect_backends,
    BBT_OT_bake_terrain,
    BBT_OT_terrain_op_add,
    BBT_OT_terrain_op_remove,
    BBT_OT_terrain_op_move,
    BBT_OT_terrain_load_preset_stack,
    BBT_UL_terrain_ops,
    BBT_PT_panel,
    BBT_PT_heightfield,
    BBT_PT_hf_shape,
    BBT_PT_hf_displace,
    BBT_PT_hf_stack,
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
