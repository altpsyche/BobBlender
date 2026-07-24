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

from . import compute  # noqa: F401
from .bridge import server  # noqa: F401
from .ui import (  # noqa: F401
    firmament,
    helpers,
    scatter,
    shaders,
    splines,
    world,
)

# A 2D top-down preview of the last baked heightfield, drawn in the panel. Loaded
# by the bake operator, created in register() and freed in unregister().
_preview_coll = None
_PREVIEW_KEY = "hf"


# Panel presets: picking a landscape family loads a good starting look -- the four
# global knobs reset to neutral (0.5, the preset as authored) and the Blender-side
# display knobs (height, sea level) that suit the family. The table is committed in
# presets.json, generated from the heightfield presets (core/heightfields/presets.py) by tools/scripts/gen_panel_presets.py
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


# Real-world scale (1 Blender unit = 1 m). A preset stores a relief RATIO (relief / tile width),
# not a fixed metre height, so the metre Height is derived from the artist's tile size and the
# landform stays physically proportioned at any size. Mirrors presets.height_for in
# core/heightfields (duplicated so the panel avoids importing the numpy compute); keep in sync.
_RELIEF_MIN_M = 0.5
_RELIEF_CEIL_FRAC = 0.6


def _height_from_ratio(ratio, size):
    """Derive metre Height from a relief ratio and tile size, clamped sane (see presets.height_for)."""
    h = float(ratio) * float(size)
    return max(_RELIEF_MIN_M, min(h, _RELIEF_CEIL_FRAC * float(size)))


def _on_terrain_size_update(self, context):
    """Resizing the tile rescales the derived Height so relief tracks size (real-world scale)."""
    self.height = _height_from_ratio(self.relief_ratio, self.terrain_size)


def _apply_hf_preset(hf):
    """Load the chosen preset's neutral slider values onto the heightfield props. Instant (A6):
    this only loads slider values (light, fully reversible, no rebuild until Bake + Build), so it
    uses the instant preset_row idiom like the other look presets, not the staged idiom reserved
    for the heavy rebuilds (Sky Look, Build Biome, Biome World).

    `relief_ratio` is stored, then Height is DERIVED from it and the current tile size (real-world
    scale); every other key maps straight onto its prop."""
    values = _HF_PRESETS.get(hf.preset)
    if not values:
        return
    for key, val in values.items():
        if key == "relief_ratio":
            hf.relief_ratio = val
            hf.height = _height_from_ratio(val, hf.terrain_size)
        else:
            setattr(hf, key, val)


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
class BBT_AssetPackItem(PropertyGroup):
    """One asset-pack folder in the preference list (a pack root: models/<biome>, textures/<set>)."""
    path: StringProperty(name="Folder", description="An asset-pack folder", subtype="DIR_PATH")


class BBT_UL_asset_packs(UIList):
    bl_idname = "BBT_UL_asset_packs"

    def draw_item(self, context, layout, data, item, icon, active_data, active_prop, index):
        layout.prop(item, "path", text="", emboss=False, icon="FILE_FOLDER")


class BBT_AddonPreferences(AddonPreferences):
    bl_idname = __package__

    autostart: BoolProperty(
        name="Start bridge on launch",
        description=("Automatically start the live MCP bridge when Blender opens. Off by default: "
                     "the bridge is an agent-authoring feature, not something an artist needs. "
                     "Start it on demand from the Advanced panel, or enable this for agent work"),
        default=False,
    )
    asset_packs: CollectionProperty(type=BBT_AssetPackItem)
    asset_packs_active: IntProperty(default=0)
    output_folder: StringProperty(
        name="Output Folder",
        description=("Where baked heightfields and generated data are written. Empty = beside the "
                     "saved .blend (or a per-user cache when unsaved)"),
        subtype="DIR_PATH",
        default="",
    )

    def draw(self, context):
        col = self.layout.column()
        col.prop(self, "autostart")
        col.label(text=f"Bridge: {server.status()}", icon="LINKED")

        col.separator()
        col.label(text="Asset Pack Folders (models/<biome>, textures/<set>)", icon="ASSET_MANAGER")
        row = col.row()
        row.template_list("BBT_UL_asset_packs", "", self, "asset_packs",
                          self, "asset_packs_active", rows=3)
        side = row.column(align=True)
        side.operator("bob_blender_tools.asset_pack_add", icon="ADD", text="")
        side.operator("bob_blender_tools.asset_pack_remove", icon="REMOVE", text="")
        col.operator("bob_blender_tools.rescan_packs", icon="FILE_REFRESH")

        col.separator()
        col.prop(self, "output_folder")


def _prefs():
    return bpy.context.preferences.addons[__package__].preferences


def _sync_pack_roots():
    """Push the preference pack folders into the bpy-free asset resolver. Called on register, on
    a pack-list edit, and by Rescan Asset Packs."""
    from .core import assets
    try:
        assets.set_pref_roots([i.path for i in _prefs().asset_packs if i.path])
    except Exception as exc:  # prefs not ready during early registration
        print(f"[bob_blender_tools] pack roots not synced: {exc}")


def _output_dir():
    """The writable folder for generated data: the preference if set, else beside the saved
    .blend, else a per-user extension cache. Always exists on return."""
    prefs = _prefs()
    if prefs.output_folder:
        d = bpy.path.abspath(prefs.output_folder)
    elif bpy.data.filepath:
        d = os.path.join(os.path.dirname(bpy.data.filepath), "bob_generated")
    else:
        d = bpy.utils.extension_path_user(__package__, path="generated", create=True)
    os.makedirs(d, exist_ok=True)
    return d


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
    bl_description = "Refresh the core builders so new op code is picked up without restarting"

    def execute(self, context):
        self.report({"INFO"}, server.reload_builders())
        return {"FINISHED"}


class BBT_OT_copy_mcp_config(Operator):
    bl_idname = "bob_blender_tools.copy_mcp_config"
    bl_label = "Copy MCP Config"
    bl_description = ("Copy a .mcp.json snippet (with this install's resolved path) for an agent "
                     "client such as Claude Code. The agent-side MCP server ships inside this "
                     "extension; no repo checkout needed. See docs/MCP.md")

    def execute(self, context):
        # realpath so a dev symlink install resolves to the real tree, matching the runner bootstrap.
        ext = os.path.dirname(os.path.realpath(__file__))
        launcher = os.path.join(ext, "mcp_agent", "__main__.py")
        snippet = json.dumps(
            {
                "mcpServers": {
                    "bobblendermcp": {
                        "type": "stdio",
                        "command": "uv",
                        "args": [
                            "run",
                            "--with", "mcp>=1.2",
                            "--with", "pydantic>=2",
                            "--with", "numpy>=1.26",
                            "python", launcher,
                        ],
                        "env": {},
                    }
                }
            },
            indent=2,
        )
        context.window_manager.clipboard = snippet
        self.report({"INFO"}, "MCP config copied to clipboard (paste into .mcp.json)")
        return {"FINISHED"}


class BBT_OT_asset_pack_add(Operator):
    bl_idname = "bob_blender_tools.asset_pack_add"
    bl_label = "Add Asset Pack Folder"
    bl_description = "Add an asset-pack folder to the search path"

    def execute(self, context):
        prefs = _prefs()
        prefs.asset_packs.add()
        prefs.asset_packs_active = len(prefs.asset_packs) - 1
        _sync_pack_roots()
        return {"FINISHED"}


class BBT_OT_asset_pack_remove(Operator):
    bl_idname = "bob_blender_tools.asset_pack_remove"
    bl_label = "Remove Asset Pack Folder"
    bl_description = "Remove the selected asset-pack folder"

    def execute(self, context):
        prefs = _prefs()
        i = prefs.asset_packs_active
        if 0 <= i < len(prefs.asset_packs):
            prefs.asset_packs.remove(i)
            prefs.asset_packs_active = max(0, i - 1)
            _sync_pack_roots()
        return {"FINISHED"}


class BBT_OT_rescan_packs(Operator):
    bl_idname = "bob_blender_tools.rescan_packs"
    bl_label = "Rescan Asset Packs"
    bl_description = "Re-read the asset-pack folders and refresh the biome lists"

    def execute(self, context):
        from .core import assets
        _sync_pack_roots()
        packs = assets.list_packs()
        biomes = assets.list_biomes()
        # Force the biome enums (EnumProperty item callbacks read the filesystem) to redraw.
        for window in context.window_manager.windows:
            for area in window.screen.areas:
                area.tag_redraw()
        self.report({"INFO"}, f"{len(packs)} pack(s), {len(biomes)} biome(s)")
        return {"FINISHED"}


# Filter-stack editor (P4). The heightfield engine (core/heightfields) evaluates an ordered op stack
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
    "strata": ("Strata (plateau)", "SEQ_STRIP_META"),
    "fluvial": ("Fluvial erosion", "MOD_FLUIDSIM"),
    "pipe_hydraulic": ("Hydraulic erosion", "MOD_OCEAN"),
    "scarp": ("Scarp retreat", "MOD_BEVEL"),
    "rill": ("Rill (badlands)", "MOD_DISPLACE"),
    "glacial": ("Glacial (U-valleys)", "FREEZE"),
    "deposit": ("Deposit (sediment)", "MOD_PARTICLES"),
    "amplify": ("Amplify (detail)", "MOD_MULTIRES"),
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
    "strata": ["layers", "dissection", "base_freq", "sharpness"],
    "fluvial": ["iterations", "k", "diffusion"],
    "pipe_hydraulic": ["iterations", "rain", "incision"],
    "scarp": ["iterations", "cap_slope", "undercut", "talus", "open_size"],
    "rill": ["iterations", "talus"],
    "glacial": ["iterations"],
    "deposit": ["amount", "iterations", "flow_floor"],
    "amplify": ["iterations", "diffusion"],
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
    "dunes": {"wind": 35.0, "frequency": 3.0, "sharpness": 0.5, "variation": 0.5,
              "mix": "replace", "amount": 0.5},
    "voronoi": {"cells": 6.0, "pattern": "mesa", "mix": "multiply", "amount": 0.8},
    "strata": {"layers": 5, "dissection": 1.4, "base_freq": 3.0, "sharpness": 0.97},
    "scarp": {"iterations": 12, "cap_slope": 0.10, "undercut": 0.0015, "talus": 0.14,
              "open_size": 6, "_raw": {"talus_iters": 1}},
    "rill": {"iterations": 10, "talus": 0.05,
             "_raw": {"groove": 0.065, "spacing": 13.0, "smear": 8, "slope_gate": 0.25,
                      "aspect_sigma": 1.0, "sharpen": 0.25, "sharpen_sigma": 1.5,
                      "despike": 2, "thermal_iters": 1}},
    "glacial": {"iterations": 60,
                "_raw": {"ela": 0.5, "erode": 1.6, "widen": 0.9, "ice_width": 8.0,
                         "horn": 0.34, "arete_talus": 0.016}},
    "amplify": {"iterations": 20, "diffusion": 0.0,
                "_raw": {"mode": "fluvial", "strength": 0.025, "to": 768, "relief": 0.1}},
    "fluvial": {"iterations": 60, "k": 0.015, "diffusion": 0.08,
                "_raw": {"sp_m": 0.5, "sp_n": 1.0, "recompute": 20, "fill_iters": 700,
                         "acc_iters": 700, "thermal_iters": 1, "max_delta": 0.03,
                         "talus": 0.004}},
    "pipe_hydraulic": {"iterations": 120, "rain": 0.012, "incision": 0.4,
                       "_raw": {"dt": 0.1, "capacity": 0.3, "dissolve": 0.25,
                                "deposit": 0.3, "evaporate": 0.02, "min_slope": 0.03,
                                "sp_m": 1.0, "sp_n": 1.2}},
    "deposit": {"amount": 0.012, "iterations": 4, "flow_floor": 0.15,
                "_raw": {"recompute": 4, "fill_iters": 800, "acc_iters": 800, "mfd_p": 1.4,
                         "flow_m": 0.6, "slope_n": 1.5, "settle_talus": 0.004, "settle_iters": 2,
                         "settle_factor": 0.5}},
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

# Per-mask-kind (BBT_TerrainOp field, engine selector key). Drives emit (_op_to_dict), load
# (_load_op), and draw together, so all three match each selector's REAL signature (ops_select.py):
# height/slope take low/high/falloff, but curvature/flow/noise take their own params. Emitting
# low/high/falloff for those kinds TypeErrored the bake, and they drew no controls.
_MASK_PARAMS = {
    "height": [("mask_low", "low"), ("mask_high", "high"), ("mask_falloff", "falloff")],
    "slope": [("mask_low", "low"), ("mask_high", "high"), ("mask_falloff", "falloff")],
    "curvature": [("mask_mode", "mode"), ("mask_strength", "strength")],
    "flow": [("mask_threshold", "threshold")],
    "noise": [("mask_frequency", "frequency"), ("mask_seed", "seed"), ("mask_contrast", "contrast")],
}


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
    talus: FloatProperty(name="Talus", default=0.01, min=0.0, max=0.2, precision=4)
    # strata (plateau generator) + scarp (cap-rock cliff retreat)
    layers: IntProperty(name="Layers", default=5, min=1, max=24,
                        description="Strata: number of flat rock benches")
    dissection: FloatProperty(name="Dissection", default=1.4, min=0.5, max=4.0,
                              description="Strata: >1 isolates mesas/buttes; ~1 keeps a continuous plateau")
    base_freq: FloatProperty(name="Base Freq", default=3.0, min=0.5, max=12.0,
                             description="Strata: plateau feature frequency (higher = more, smaller mesas)")
    cap_slope: FloatProperty(name="Cap Slope", default=0.10, min=0.01, max=0.5, precision=3,
                             description="Scarp: slopes below this are resistant flat cap; steeper faces erode")
    undercut: FloatProperty(name="Undercut", default=0.0015, min=0.0, max=0.05, precision=4,
                            description="Scarp: per-iteration cliff-face retreat rate")
    open_size: IntProperty(name="Despire", default=6, min=0, max=16,
                           description="Scarp: shave spires/cones narrower than this while keeping flat caps")
    flow_floor: FloatProperty(
        name="Flow Floor", default=0.15, min=0.0, max=1.0,
        description="Deposit: only alluviate cells whose drainage is above this fraction of the max "
                    "(the wet channel), so uplands are left alone")
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
    mask_low: FloatProperty(name="Low", default=0.0, min=0.0, max=1.0)       # height / slope
    mask_high: FloatProperty(name="High", default=1.0, min=0.0, max=1.0)
    mask_falloff: FloatProperty(name="Falloff", default=0.1, min=0.0, max=1.0)
    mask_mode: EnumProperty(name="Curvature", default="convex",              # curvature
                            items=[("convex", "Convex", "Ridges and rims"),
                                   ("concave", "Concave", "Valleys and hollows")])
    mask_strength: FloatProperty(name="Strength", default=1.0, min=0.0, max=2.0)
    mask_threshold: FloatProperty(                                           # flow
        name="Threshold", default=0.02, min=0.0, max=1.0, precision=3,
        description="Drainage fraction of the max above which a cell counts as a channel")
    mask_frequency: FloatProperty(name="Frequency", default=6.0, min=0.5, max=40.0)  # noise
    mask_seed: IntProperty(name="Noise Seed", default=0, min=0)
    mask_contrast: FloatProperty(name="Contrast", default=0.5, min=0.0, max=1.0)


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
        m = {"kind": op.mask_kind}
        for field, key in _MASK_PARAMS.get(op.mask_kind, []):
            m[key] = getattr(op, field)
        d["mask"] = m
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
        for field, key in _MASK_PARAMS.get(op.mask_kind, []):
            if key in mask:
                setattr(op, field, mask[key])
    else:
        op.mask_kind = "none"
    op.raw = json.dumps({k: v for k, v in d.items()
                         if k not in exposed and k not in ("kind", "mask")})
    op.enabled = True


def _stack_from_ops(hf):
    """The enabled ops as an engine stack list."""
    return [_op_to_dict(op) for op in hf.ops if op.enabled]


# Heightfield terrain: bake in-process (core/heightfields; dev-venv host-hop fallback), build in place here.
class BBT_HeightfieldProps(PropertyGroup):
    target: StringProperty(name="Object", default="Terrain")
    # No Material picker (docs/UX-REDESIGN.md decision D): a terrain gets its material by
    # selecting it in the Shaders panel (New BobShader -> Terrain), the one native path.
    preset: EnumProperty(name="Preset", items=_preset_items,
                         description="A landscape family to stage; press Apply Preset to load its "
                                     "starting slider values")
    backend: EnumProperty(
        name="Backend",
        items=[("auto", "Auto", "Use the GPU when present, else CPU"),
               ("gpu", "GPU", "Force the CuPy CUDA path"),
               ("cpu", "CPU", "Force the numpy reference path")],
        default="auto",
    )
    backend_hint: StringProperty(name="Backends", default="")
    emit_maps: BoolProperty(
        name="Flow + wetness maps", default=True,
        description="Also bake <name>_flow.png and <name>_wetness.png beside the height, "
                    "so a Terrain BobShader can read the terrain's own drainage (wet channels, "
                    "riverbed layers). Adds a short drainage solve to the bake")
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
    # authored"; the compute (core/heightfields params.resolve_stack) turns them into the op stack.
    relief: FloatProperty(name="Relief", default=0.5, min=0.0, max=1.0,
                          description="Ruggedness: higher is rockier, more dramatic ridgelines")
    detail: FloatProperty(name="Detail", default=0.5, min=0.0, max=1.0,
                          description="Feature size: higher adds finer octaves and crisper edges")
    erosion: FloatProperty(name="Erosion", default=0.5, min=0.0, max=1.0,
                           description="Incision: higher carves deeper valleys and channels")
    warp: FloatProperty(name="Warp", default=0.5, min=0.0, max=1.0,
                        description="Meander: higher distorts the domain for a more organic look")
    terrain_size: FloatProperty(name="Size m", default=90.0, min=1.0,
                                update=_on_terrain_size_update,
                                description="Tile width in metres (1 unit = 1 m). Height tracks it "
                                            "so the landform stays proportioned at any size")
    # Relief RATIO (relief / tile width) from the preset; drives the derived metre Height. Hidden:
    # the artist edits Size and (optionally) Height, not this.
    relief_ratio: FloatProperty(name="Relief ratio", default=0.08, min=0.0, max=1.0)
    height: FloatProperty(name="Height", default=22.0,
                          description="Vertical relief in metres. Derived from the preset's relief "
                                      "ratio x Size; override for a taller or flatter look")
    vert_exag: FloatProperty(
        name="Exaggeration", default=1.0, min=0.05, soft_max=8.0, max=50.0,
        description="Vertical-exaggeration multiplier on Height, like a GIS 2x/3x relief. Keeps the "
                    "real-world Height honest while punching up relief for a diorama or a small tile. "
                    "1.0 is true scale; the baked terrain uses Height x Exaggeration")
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


def _resolve_bake_params(hf_mod, knobs, params, maps):
    """Turn the panel's knobs/params into a full params dict the compute runs (mirrors the CLI's
    argument handling). Knobs with an explicit `stack` pass through; a bare knobs dict is expanded
    via build_params; a params dict resolves its `preset` over the base preset."""
    if knobs is not None:
        if "stack" in knobs:
            resolved = {k: knobs[k] for k in ("size", "seed", "backend", "stack") if k in knobs}
        else:
            resolved = hf_mod.build_params(knobs)
    else:
        resolved = dict(params)
        preset = resolved.pop("preset", None)
        if preset is not None:
            base = hf_mod.presets.get(preset)
            base.update(resolved)
            resolved = base
    if maps:
        resolved["maps"] = True
    return resolved


def _in_steam_container():
    """True when this Blender runs inside the Steam pressure-vessel container, where the host venv
    (its python and CUDA) is not reachable, so a fallback bake has to hop to the host."""
    return (
        os.environ.get("container") == "pressure-vessel"
        or os.path.isdir("/run/pressure-vessel")
        or os.path.isdir("/run/host")
    )


def _venv_bake(context, out_abs, *, knobs, params, preview, maps):
    """Fallback: run the bake in the dev repo's venv by subprocess, when Blender's own Python
    lacks the compute deps (scipy / CuPy) that P5's Enable Compute installs. The compute is the
    same single source (`core/heightfields`), reached by the venv via `-m heightfields` with the
    core dir on PYTHONPATH. Returns (meta, error). Unavailable on a packaged install (no venv);
    there the caller surfaces the Enable Compute path instead."""
    repo = os.path.dirname(server._repo_blender_dir())
    host_py = os.path.join(repo, "tools", ".venv", "bin", "python")
    # lexists, not exists: the venv's `python` is a symlink to the host's /usr/bin/pythonX. Inside a
    # Steam pressure-vessel sandbox /usr is the runtime's, so exists() (which follows the link) is
    # False even though the venv is really there — and the launcher below runs it on the HOST where
    # it resolves. Checking the link node itself avoids a false "no dev venv".
    if not os.path.lexists(host_py):
        return None, "no dev venv"
    payload = knobs if knobs is not None else params
    flag = "--knobs-file" if knobs is not None else "--params-file"
    tmp = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False)
    json.dump(payload, tmp)
    tmp.close()
    extra = ["--out", out_abs, flag, tmp.name, "--force"]
    if preview:
        extra.append("--preview")
    if maps:
        extra.append("--maps")
    # `-m bobtools.hf_cli`: bobtools is installed in the venv so it resolves with NO PYTHONPATH,
    # which is essential because PYTHONPATH is dropped across the Steam host-hop below. The shim
    # puts the single-source core/heightfields on sys.path itself (via bobtools._hfpath).
    argv = [host_py, "-m", "bobtools.hf_cli"] + extra
    if _in_steam_container():
        argv = ["steam-runtime-launch-client", "--alongside-steam", "--"] + argv
    try:
        proc = subprocess.run(argv, capture_output=True, text=True, timeout=600)
    except Exception as exc:
        return None, f"venv bake could not run: {exc}"
    finally:
        os.unlink(tmp.name)
    if proc.returncode != 0:
        return None, f"venv bake failed: {(proc.stderr or proc.stdout or '').strip()[-200:]}"
    lines = (proc.stdout or "").strip().splitlines()
    try:
        return (json.loads(lines[-1]) if lines else {}), None
    except ValueError:
        return {}, None


def _run_host_bake(context, out_abs, *, knobs=None, params=None, preview=False, maps=False):
    """Bake one heightfield, IN-PROCESS by default (P4), under a wait-cursor + progress guard.

    The compute is the single committed copy inside the extension (`core.heightfields`), run on
    Blender's bundled numpy. Some ops need scipy/CuPy, which Blender's Python does not ship until
    P5's Enable Compute installs them; while they are absent the bake falls back to the dev repo's
    venv (same source, by subprocess) so dev keeps its GPU bake. On a packaged install with neither
    the deps nor a venv, it returns a clear message. Returns (meta, None) or (None, error). The one
    owner of the bake contract, shared by the Terrain bake and the Paths Bake & Erode."""
    wm = context.window_manager
    window = context.window
    if window:
        window.cursor_set("WAIT")
    wm.progress_begin(0, 1)
    try:
        from .core import heightfields as hf_mod

        resolved = _resolve_bake_params(hf_mod, knobs, params, maps)
        try:
            return hf_mod.bake(out_abs, resolved, force=True, preview=preview), None
        except Exception as exc:
            # In-process compute failed. Causes: deps not installed (ModuleNotFoundError), or CuPy
            # imports but its CUDA/NVRTC libs are unreachable (e.g. a Steam pressure-vessel sandbox
            # hides system CUDA, and the CUDA-13 pip wheels are not yet published) -> a
            # CompileException/CUDA error, not an import error. Either way, fall back to the dev venv,
            # which hops to the host via the Steam launcher (see _venv_bake) where CUDA works.
            meta, verr = _venv_bake(context, out_abs, knobs=knobs, params=params,
                                    preview=preview, maps=maps)
            if meta is not None:
                return meta, None
            return None, (f"bake failed in-process ({type(exc).__name__}: {str(exc)[:120]}); "
                          f"venv fallback unavailable ({verr}). Use Enable Compute (Advanced) or "
                          "run from the repo venv.")
    finally:
        wm.progress_end()
        if window:
            window.cursor_set("DEFAULT")


class BBT_OT_hf_apply_preset(Operator):
    bl_idname = "bob_blender_tools.hf_apply_preset"
    bl_label = "Preset"
    bl_description = ("Load a landscape preset's starting slider values. Then sculpt with the "
                      "knobs and Bake + Build")
    bl_options = {"REGISTER", "UNDO"}

    preset: EnumProperty(name="Preset", items=_preset_items)

    def execute(self, context):
        hf = context.scene.bbt_hf
        hf.preset = self.preset  # the picked preset also drives the Bake, so keep it in sync
        _apply_hf_preset(hf)
        self.report({"INFO"}, f"Loaded {self.preset} preset")
        return {"FINISHED"}


def _venv_exists():
    return os.path.exists(os.path.join(os.path.dirname(server._repo_blender_dir()),
                                       "tools", ".venv", "bin", "python"))


class BBT_OT_detect_backends(Operator):
    bl_idname = "bob_blender_tools.detect_backends"
    bl_label = "Check Backends"
    bl_description = "Probe the compute backend the bake will use (GPU device round-trip / CPU)"

    def execute(self, context):
        hf = context.scene.bbt_hf
        hf.backend_hint = compute.status_line(compute.probe(refresh=True), _venv_exists())
        return {"FINISHED"}


class BBT_OT_enable_compute(Operator):
    bl_idname = "bob_blender_tools.enable_compute"
    bl_label = "Enable Compute"
    bl_description = ("Install the terrain compute (scipy, and CuPy for GPU) into Blender's own "
                      "Python so terrain bakes in-process with no venv. Downloads wheels and writes "
                      "to Blender's Python; needs network and disk")

    def invoke(self, context, event):
        # Consent: this downloads wheels and writes into Blender's Python. Confirm before doing it.
        return context.window_manager.invoke_confirm(self, event)

    def execute(self, context):
        hf = context.scene.bbt_hf
        pr = compute.probe(refresh=True)
        packages = compute.needed_packages(pr)
        if not packages:
            hf.backend_hint = compute.status_line(pr, _venv_exists())
            self.report({"INFO"}, "compute already installed")
            return {"FINISHED"}

        window = context.window
        if window:
            window.cursor_set("WAIT")
        context.window_manager.progress_begin(0, 1)
        try:
            ok, msg = compute.install(packages)
        finally:
            context.window_manager.progress_end()
            if window:
                window.cursor_set("DEFAULT")
        if not ok:
            hf.backend_hint = "install failed (CPU fallback)"
            self.report({"ERROR"}, f"Enable Compute failed: {msg}. Terrain still bakes on CPU/venv.")
            return {"CANCELLED"}

        pr = compute.probe(refresh=True)
        # If a GPU wheel went in, verify a real device round-trip (the P5 acceptance check). A wheel
        # that imports but cannot reach the driver degrades to CPU with a clear message, not a crash.
        if pr["cupy_ok"]:
            gok, ginfo = compute.verify_gpu()
            hf.backend_hint = compute.status_line(pr, _venv_exists())
            if gok:
                self.report({"INFO"}, f"Compute enabled. GPU verified: {ginfo}")
            else:
                self.report({"WARNING"}, f"Installed, but GPU device test failed ({ginfo}); using CPU.")
        else:
            hf.backend_hint = compute.status_line(pr, _venv_exists())
            self.report({"INFO"}, "CPU compute enabled (scipy installed); terrain bakes in-process.")
        return {"FINISHED"}


class BBT_OT_bake_terrain(Operator):
    bl_idname = "bob_blender_tools.bake_terrain"
    bl_label = "Bake + Build Terrain"
    bl_description = "Bake an eroded heightfield in-process, then build the terrain in place"

    def execute(self, context):
        hf = context.scene.bbt_hf
        # basename the free-text target so a value like "../../x" cannot escape the output folder
        target = os.path.basename((hf.target or "terrain").strip()) or "terrain"
        out_abs = os.path.join(_output_dir(), f"{target}_hf.png")
        # Either send the edited op stack verbatim (P4 custom mode), or the preset
        # plus the five global knobs; the compute turns knobs into a stack, so the panel
        # does not duplicate that logic.
        if hf.use_custom_stack and len(hf.ops):
            knobs = {"size": hf.resolution, "seed": hf.seed, "backend": hf.backend,
                     "stack": _stack_from_ops(hf)}
        else:
            knobs = {
                "size": hf.resolution, "seed": hf.seed, "backend": hf.backend,
                "preset": hf.preset, "relief": hf.relief, "detail": hf.detail,
                "erosion": hf.erosion, "warp": hf.warp,
            }

        # Blocking bake with feedback (wait cursor + progress spinner) via the shared host-bake runner.
        t0 = time.perf_counter()
        # Panel always bakes full resolution: every shipped preset amplifies, so a "preview" only ever
        # dropped to AMPLIFY_PREVIEW (512 vs 768) for a marginal GPU speedup. The fast-look path lives on
        # in the CLI (--preview) and pipeline.bake(preview=True) for CPU/scripted use.
        meta, err = _run_host_bake(context, out_abs, knobs=knobs, preview=False, maps=hf.emit_maps)
        if err is not None:
            self.report({"ERROR"}, err)
            return {"CANCELLED"}

        # Build the terrain in place from the fresh PNG.
        from .core.dispatch import apply_op

        # Take the actual bake size from the returned metadata (the pipeline owns it), not the
        # requested resolution, for the terrain grid resolution.
        bake_size = int(meta.get("size", hf.resolution))
        apply_op({"op": "reload_image", "path": out_abs})
        # Mesh grid density is DECOUPLED from the bake resolution: the heightmap keeps its full
        # detail (sampled for displacement and shading), but the mesh only needs enough vertices
        # for the silhouette. Matching verts to texels built 0.6M-4.2M verts and stalled the
        # viewport. Cap at the bake size so a low-res preview is not needlessly dense.
        grid_res = min(int(hf.mesh_res), bake_size)
        # Height is the honest real-world relief; Exaggeration is a separate GIS-style multiplier so
        # a diorama or a small tile can punch up relief without lying about the base Height (exag 1.0
        # is true scale). The recipe only sees the product.
        eff_height = hf.height * hf.vert_exag
        tparams = {"heightmap": out_abs, "size": hf.terrain_size, "resolution": grid_res,
                   "height": eff_height, "sea_level": hf.sea_level}
        # No material here (decision D): the terrain is shaded from the Shaders panel.
        # reset=True: Height and Sea Level are panel-authoritative (derived from relief ratio x Size),
        # so a re-bake must take them from params. Without it, build_geonodes preserves the old live
        # modifier values by socket name (the knob-restore meant for hand-tuned subsystems) and the
        # panel's Height silently does nothing -- a stale tall value then squashes/inflates the rebuild.
        apply_op({"op": "build_geonodes", "recipe": "heightmap_terrain",
                  "name": hf.target, "params": tparams, "reset": True})

        # Record the heightmap + size on the object so a Terrain BobShader can locate the sibling
        # flow/wetness maps and sample them at the right scale. Reload the maps too (a re-bake
        # overwrites them in place) so an existing terrain material's samples refresh.
        obj = bpy.data.objects.get(hf.target)
        if obj is not None:
            obj["bbt_heightmap"] = out_abs
            obj["bbt_terrain_size"] = float(hf.terrain_size)
            # The rest of the build params, so a downstream grade (the Paths panel) can
            # rebuild heightmap_terrain in place with a path without re-baking. resolution is
            # the grid density actually built (structural), so it must be exact; height and
            # sea level snapshot-restore on rebuild but are stored as the seed fallback.
            obj["bbt_terrain_res"] = int(grid_res)
            obj["bbt_terrain_height"] = float(eff_height)   # exaggerated relief, so a path rebuild matches
            obj["bbt_terrain_sea"] = float(hf.sea_level)
            # Stamp the snow line's Z bounds from the freshly built terrain, so the normalized
            # snow line maps to THIS terrain's real height (0 = valley, 1 = peaks) the moment the
            # artist drags it -- not to stale 0..20 m defaults that leave a low valley bare.
            from .core import env as _env
            context.view_layer.update()
            _env.stamp_snow_bounds(context.scene, obj)
        if hf.emit_maps:
            base, ext = os.path.splitext(out_abs)
            for kind in ("flow", "wetness"):
                mp = f"{base}_{kind}{ext}"
                if os.path.exists(mp):
                    apply_op({"op": "reload_image", "path": mp})

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
# Pipeline panel order (docs/UX-REDESIGN.md section 4, + Paths per docs/SPLINES.md 5): World=0,
# Terrain=1, Paths=2, Scatter=3, Shaders=4, Atmosphere=5, Advanced/Bridge=6. Set via bl_order so
# the N-panel teaches the terrain -> paths -> scatter -> shade sequence regardless of registration
# order (P6). The
# dev/agent Bridge is demoted to a collapsed Advanced panel (decision B): it should not greet
# an artist first, but stays in the tab for when an agent needs the live socket.
class BBT_PT_panel(Panel):
    bl_label = "Advanced"
    bl_idname = "BBT_PT_panel"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "BobBlenderTools"
    bl_order = 7
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
        # A dev reload, not a datablock rebuild: keep it off the shared STRUCTURAL_ICON
        # (FILE_REFRESH) so that marker stays meaningful for real builds.
        layout.operator("bob_blender_tools.reload_builders", icon="CONSOLE")

        layout.separator()
        layout.label(text="Agent authoring (MCP): drive this session from an agent client", icon="URL")
        layout.operator("bob_blender_tools.copy_mcp_config", icon="COPYDOWN")

        layout.separator()
        layout.label(text="Asset packs (folders set in add-on preferences)", icon="ASSET_MANAGER")
        layout.operator("bob_blender_tools.rescan_packs", icon="FILE_REFRESH")


class BBT_PT_heightfield(Panel):
    bl_label = "Terrain"
    bl_idname = "BBT_PT_heightfield"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "BobBlenderTools"
    bl_order = 2  # pipeline stage: Terrain (Biome panel is 1); see BBT_PT_panel comment
    bl_options = {"DEFAULT_CLOSED"}

    def draw(self, context):
        hf = context.scene.bbt_hf
        layout = self.layout

        if _preview_coll is not None and _PREVIEW_KEY in _preview_coll:
            layout.template_icon(icon_value=_preview_coll[_PREVIEW_KEY].icon_id, scale=8)

        # P1: the target mesh the bake builds (or replaces). "Active mesh" is the one suite-wide
        # noun for the thing a panel acts on (S4), matching World/Biome/Scatter/Shaders.
        helpers.context_header(layout, "Active mesh", hf.target, icon="OUTLINER_OB_MESH")
        col = layout.column(align=True)
        col.prop(hf, "target")
        # A6: instant preset (light: loads slider values, no rebuild until Bake + Build), so it
        # uses the same instant idiom as the other look presets. The current pick rides in the
        # dropdown label (operator_menu_enum won't show it on its own), so no separate caption.
        helpers.preset_row(layout, "bob_blender_tools.hf_apply_preset", text="Preset",
                              current=hf.preset)

        row = layout.row(align=True)
        row.prop(hf, "backend", expand=True)
        row.operator("bob_blender_tools.detect_backends", text="", icon="QUESTION")
        if hf.backend_hint:
            icon = "ERROR" if hf.backend_hint.startswith(("CPU", "none", "venv", "probe", "Enable", "install", "GPU wheel")) else "INFO"
            layout.label(text=hf.backend_hint, icon=icon)

        # P5: guided compute delivery. The bake needs scipy (CPU) and CuPy (GPU) inside Blender's
        # Python; when they are missing, steer the user to install them (prominently when a GPU is
        # present). One click; downloads wheels and writes to Blender's Python, with consent.
        pr = compute.probe()
        pkgs = compute.needed_packages(pr)
        if pkgs:
            box = layout.box()
            if pr["gpu"] and not pr["cupy_ok"]:
                box.label(text=f"GPU available: {pr['gpu_name']}", icon="RESTRICT_RENDER_OFF")
            box.label(text="Terrain compute not installed in Blender", icon="ERROR")
            box.operator("bob_blender_tools.enable_compute",
                         text=f"Enable Compute ({', '.join(pkgs)})", icon="IMPORT")

        row = layout.row(align=True)
        row.prop(hf, "resolution")
        layout.prop(hf, "emit_maps")

        # P3: Bake + Build is STRUCTURAL (bakes a heightfield, then builds the mesh); the
        # Shape/Erosion/Displace knobs below are its inputs. Shade the result in Shaders.
        helpers.structural_action(layout, "bob_blender_tools.bake_terrain",
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
    # Sculpt is Terrain's primary tuning panel, so it opens by default like each other system's
    # primary sub-panel; Displace and the advanced Filter Stack stay DEFAULT_CLOSED.

    def draw(self, context):
        hf = context.scene.bbt_hf
        layout = self.layout
        helpers.seed_row(layout, hf, "seed", "bob_blender_tools.random_seed")
        # The four curated global knobs modulate the chosen landscape preset. A custom Filter
        # Stack bakes its own ops instead (seed still applies), so grey them when it is on.
        col = layout.column(align=True)
        col.enabled = not hf.use_custom_stack
        col.prop(hf, "relief")
        col.prop(hf, "detail")
        col.prop(hf, "erosion")
        col.prop(hf, "warp")
        if hf.use_custom_stack:
            cap = layout.row()
            cap.enabled = False
            cap.label(text="Custom stack on: these preset knobs are bypassed")


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
        layout.prop(hf, "vert_exag")
        layout.prop(hf, "sea_level")
        layout.prop(hf, "mesh_res")
        # Real-world scale readout (1 unit = 1 m): peak-above-sea relief and the ground texel size,
        # so the artist knows a dropped 1.8 m character or 6 m house will read correctly. Peak folds
        # in Exaggeration (what the terrain actually builds at); the true-scale peak is shown when it
        # differs so the honest relief stays visible behind the diorama multiplier.
        peak = hf.height * hf.vert_exag * (1.0 - hf.sea_level)
        texel = hf.terrain_size / max(int(hf.mesh_res), 1)
        box = layout.box()
        box.scale_y = 0.7
        box.label(text=f"Scale: {hf.terrain_size:.0f} m tile, {peak:.1f} m peak", icon="EMPTY_ARROWS")
        if abs(hf.vert_exag - 1.0) > 1e-3:
            true_peak = hf.height * (1.0 - hf.sea_level)
            box.label(text=f"Exaggeration {hf.vert_exag:.1f}x (true {true_peak:.1f} m)", icon="FULLSCREEN_ENTER")
        box.label(text=f"Mesh texel: {texel:.2f} m/vert", icon="MESH_GRID")


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
            mps = _MASK_PARAMS.get(op.mask_kind, [])
            if mps:
                sub = box.row(align=True) if op.mask_kind in ("height", "slope") \
                    else box.column(align=True)
                for field, _key in mps:
                    sub.prop(op, field)


_CLASSES = (
    BBT_TerrainOp,
    BBT_AssetPackItem,       # before AddonPreferences: its CollectionProperty references this type
    BBT_UL_asset_packs,
    BBT_AddonPreferences,
    BBT_HeightfieldProps,
    BBT_OT_start,
    BBT_OT_stop,
    BBT_OT_reload,
    BBT_OT_copy_mcp_config,
    BBT_OT_asset_pack_add,
    BBT_OT_asset_pack_remove,
    BBT_OT_rescan_packs,
    BBT_OT_random_seed,
    BBT_OT_hf_apply_preset,
    BBT_OT_detect_backends,
    BBT_OT_enable_compute,
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


def _warm_probe():
    """Warm the compute probe cache once at startup (nvidia-smi is a subprocess; keep it out of the
    panel draw). The Terrain panel then reads the cached result to steer Enable Compute."""
    try:
        compute.probe(refresh=True)
    except Exception as exc:
        print(f"[bob_blender_tools] compute probe skipped: {exc}")
    return None  # one-shot timer


def register():
    global _preview_coll
    for cls in _CLASSES:
        bpy.utils.register_class(cls)
    bpy.types.Scene.bbt_hf = PointerProperty(type=BBT_HeightfieldProps)
    _sync_pack_roots()  # feed the resolver the saved preference pack folders
    scatter.register()
    splines.register()    # Paths: typed curves; drives terrain (grade) + scatter (clear)
    firmament.register()  # owns and registers the shared world (bbt_env); subscribes its applier
    world.register()      # World panel + bbt_world master toggles (drive every consumer)
    shaders.register()    # reads bbt_env; subscribes its applier
    _preview_coll = bpy.utils.previews.new()
    # Defer autostart until prefs are available.
    bpy.app.timers.register(_autostart, first_interval=0.2)
    bpy.app.timers.register(_warm_probe, first_interval=0.1)


def unregister():
    global _preview_coll
    server.stop()
    if _preview_coll is not None:
        bpy.utils.previews.remove(_preview_coll)
        _preview_coll = None
    shaders.unregister()
    world.unregister()
    firmament.unregister()
    splines.unregister()
    scatter.unregister()
    del bpy.types.Scene.bbt_hf
    for cls in reversed(_CLASSES):
        bpy.utils.unregister_class(cls)
