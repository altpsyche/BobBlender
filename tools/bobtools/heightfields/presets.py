"""Named terrain recipes: each preset is a filter STACK, not a flat knob set.

A preset is an ordered op list evaluated by engine.run_stack: the first op is a
generator (noise / dunes / voronoi) that establishes the base, and later ops erode
and shape it. Each op carries only its DISTINCTIVE parameters; the engine's op
defaults fill in the rest, so a stack reads as the handful of choices that give a
family its character.

These are the neutral, as-authored looks. The five curated global knobs
(Relief / Detail / Erosion / Warp / Seed) modulate a COPY of the active stack at
bake time -- see params.resolve_stack -- with every knob at 0.5 reproducing the
stack exactly as written here. Presets are grouped into four families:

  Mountains       alpine, glacial, foothills
  Canyons/mesas   canyon, mesa, badlands, plateau
  Hills/coastal   hills, plains, coastal, islands
  Dunes           dunes, sand_sea

The canyon stack is the flow-accumulation stream-power carver proven to produce
dendritic incised canyons (see heightfields/ops_erode.fluvial); the others are
tuned around the same engine. Keep these plain and few.
"""

# fluvial defaults shared by the incised families, so each stack states only what
# differs. fill_iters/acc_iters are drainage-propagation counts; 600-700 covers the
# longest flow paths at these sizes (the network is resolution-stable, verified by
# corr(256, 768->256)). sp_m=0.5, sp_n=1.0 is the resolution-invariant stream-power
# exponent pair, so the incision magnitude holds across bake resolutions.
_FLUVIAL = dict(sp_m=0.5, sp_n=1.0, recompute=20, fill_iters=700, acc_iters=700,
                thermal_iters=1, max_delta=0.03)


def _fluvial(**over):
    return {"kind": "fluvial", **_FLUVIAL, **over}


STACKS = {
    # --- Mountains ---
    "alpine": [
        {"kind": "noise", "ridged": 0.62, "detail_strength": 0.7, "octaves": 6, "warp": 70},
        _fluvial(iterations=90, k=0.018, sp_n=1.05, diffusion=0.045, talus=0.004),
        {"kind": "sharpen", "amount": 0.35, "radius": 1.5},
    ],
    "glacial": [
        {"kind": "noise", "ridged": 0.35, "detail_strength": 0.4, "octaves": 4, "warp": 80},
        _fluvial(iterations=55, k=0.011, diffusion=0.15, talus=0.008, max_delta=0.022,
                 recompute=25, fill_iters=600, acc_iters=600),
        {"kind": "smooth", "sigma": 2.0},
    ],
    "foothills": [
        {"kind": "noise", "ridged": 0.35, "detail_strength": 0.5, "octaves": 5, "warp": 85},
        _fluvial(iterations=55, k=0.013, diffusion=0.08, talus=0.005,
                 recompute=25, fill_iters=600, acc_iters=600),
        {"kind": "smooth", "sigma": 0.8},
    ],
    # --- Canyons / mesas / badlands / plateaus ---
    "canyon": [   # the proven dendritic-canyon hero
        {"kind": "noise", "ridged": 0.5, "detail_strength": 0.6, "octaves": 6, "warp": 60},
        _fluvial(iterations=100, k=0.02, diffusion=0.05, talus=0.004),
    ],
    "mesa": [
        {"kind": "noise", "ridged": 0.28, "detail_strength": 0.35, "octaves": 4, "warp": 50},
        {"kind": "voronoi", "cells": 5.0, "pattern": "mesa", "mix": "multiply", "amount": 0.85},
        {"kind": "terrace", "steps": 5, "sharpness": 0.9,
         "mask": {"kind": "height", "low": 0.42, "high": 1.0, "falloff": 0.1}},
        _fluvial(iterations=40, k=0.014, diffusion=0.05, recompute=25,
                 fill_iters=600, acc_iters=600),
        {"kind": "thermal", "talus": 0.012, "factor": 0.5, "iterations": 3},
    ],
    "badlands": [
        {"kind": "noise", "ridged": 0.42, "detail_strength": 0.65, "octaves": 7, "warp": 65},
        _fluvial(iterations=120, k=0.02, sp_n=1.1, diffusion=0.03, talus=0.003),
        {"kind": "thermal", "talus": 0.006, "factor": 0.4, "iterations": 2},
    ],
    "plateau": [
        {"kind": "noise", "ridged": 0.28, "detail_strength": 0.4, "octaves": 5, "warp": 50},
        {"kind": "curve", "gamma": 0.7, "contrast": 0.25},
        {"kind": "terrace", "steps": 4, "sharpness": 0.8,
         "mask": {"kind": "height", "low": 0.45, "high": 1.0, "falloff": 0.15}},
        _fluvial(iterations=50, k=0.016, diffusion=0.05, recompute=25,
                 fill_iters=600, acc_iters=600),
    ],
    # --- Hills / plains / coastal / islands ---
    "hills": [
        {"kind": "noise", "ridged": 0.15, "detail_strength": 0.4, "octaves": 5, "warp": 90},
        _fluvial(iterations=40, k=0.01, diffusion=0.12, max_delta=0.025,
                 recompute=40, fill_iters=500, acc_iters=500),
        {"kind": "smooth", "sigma": 1.0},
    ],
    "plains": [
        {"kind": "noise", "ridged": 0.1, "detail_strength": 0.28, "octaves": 4, "warp": 100},
        {"kind": "smooth", "sigma": 2.2},
        _fluvial(iterations=30, k=0.008, diffusion=0.16, max_delta=0.02,
                 recompute=30, fill_iters=400, acc_iters=400),
        {"kind": "smooth", "sigma": 1.2},
    ],
    "coastal": [
        {"kind": "noise", "ridged": 0.28, "detail_strength": 0.5, "octaves": 5, "warp": 85},
        {"kind": "falloff", "shape": "gradient", "angle": 90, "margin": 0.6, "power": 1.5},
        _fluvial(iterations=55, k=0.013, diffusion=0.08, recompute=25,
                 fill_iters=600, acc_iters=600),
        {"kind": "smooth", "sigma": 0.8},
    ],
    "islands": [
        {"kind": "noise", "ridged": 0.38, "detail_strength": 0.55, "octaves": 5, "warp": 90},
        {"kind": "falloff", "shape": "radial", "margin": 0.62, "power": 2.0},
        _fluvial(iterations=55, k=0.015, diffusion=0.07, recompute=25,
                 fill_iters=600, acc_iters=600),
        {"kind": "thermal", "talus": 0.005, "factor": 0.4, "iterations": 2},
    ],
    # --- Dunes ---
    "dunes": [
        {"kind": "dunes", "wind": 35, "frequency": 12, "sharpness": 2.2, "warp": 0.14,
         "variation": 0.5, "mix": "replace"},
        {"kind": "thermal", "talus": 0.02, "factor": 0.5, "iterations": 3},
    ],
    "sand_sea": [
        {"kind": "dunes", "wind": 22, "frequency": 7, "sharpness": 2.6, "warp": 0.2,
         "variation": 0.6, "mix": "replace"},
        {"kind": "noise", "ridged": 0.1, "detail_strength": 0.25, "octaves": 4, "warp": 100,
         "mix": "add", "amount": 0.12},
        {"kind": "thermal", "talus": 0.025, "factor": 0.5, "iterations": 2},
    ],
}

# Blender-side displacement defaults per preset: metres of vertical relief and the
# sea-level fraction that suit each family. These are NOT heightfield params (the
# field is always normalised [0,1]); they live here so presets.py stays the single
# source of truth for the panel's per-preset table (see gen_panel_presets.py).
DISPLAY = {
    "alpine":    {"height": 22.0, "sea_level": 0.22},
    "glacial":   {"height": 18.0, "sea_level": 0.24},
    "foothills": {"height": 13.0, "sea_level": 0.30},
    "canyon":    {"height": 20.0, "sea_level": 0.14},
    "mesa":      {"height": 15.0, "sea_level": 0.20},
    "badlands":  {"height": 15.0, "sea_level": 0.25},
    "plateau":   {"height": 16.0, "sea_level": 0.22},
    "hills":     {"height": 9.0,  "sea_level": 0.30},
    "plains":    {"height": 5.0,  "sea_level": 0.32},
    "coastal":   {"height": 12.0, "sea_level": 0.34},
    "islands":   {"height": 14.0, "sea_level": 0.34},
    "dunes":     {"height": 7.0,  "sea_level": 0.0},
    "sand_sea":  {"height": 9.0,  "sea_level": 0.0},
}

# Family grouping, for the panel dropdown ordering and docs.
FAMILIES = {
    "Mountains": ["alpine", "glacial", "foothills"],
    "Canyons": ["canyon", "mesa", "badlands", "plateau"],
    "Lowlands": ["hills", "plains", "coastal", "islands"],
    "Dunes": ["dunes", "sand_sea"],
}

PRESETS = list(STACKS)


def stack(name):
    """Return a deep-ish copy of a preset's op stack (op dicts copied one level)."""
    if name not in STACKS:
        raise ValueError(f"unknown preset: {name!r} (have: {sorted(STACKS)})")
    return [dict(op) for op in STACKS[name]]


def display(name):
    """Return the Blender displacement defaults (height, sea_level) for a preset."""
    return dict(DISPLAY.get(name, {"height": 14.0, "sea_level": 0.30}))


def get(name):
    """Return a full bake params dict for a preset (stack resolved at neutral knobs)."""
    from . import params  # lazy: params imports presets, avoid a cycle at import time
    return params.build_params({"preset": name})
