"""Named terrain recipes: each preset is a filter STACK, not a flat knob set.

A preset is an ordered op list evaluated by engine.run_stack: the first op is a
generator (noise / dunes / voronoi) that establishes the base, and later ops erode
and shape it. Each op carries only its DISTINCTIVE parameters; the engine's op
defaults fill in the rest, so a stack reads as the handful of choices that give a
family its character.

These are the neutral, as-authored looks. The five curated global knobs
(Relief / Detail / Erosion / Warp / Seed) modulate a COPY of the active stack at
bake time -- see params.resolve_stack -- with every knob at 0.5 reproducing the
stack exactly as written here. Presets are grouped into three families:

  Mountains       alpine, glacial, foothills
  Lowlands        hills, plains, coastal, islands
  Dunes           dunes, sand_sea

The mountain stacks pair ridged-multifractal noise with stream-power fluvial erosion
(see heightfields/ops_erode.fluvial); the lowlands use gentler versions of the same, with
falloff shaping coastal and islands. Keep these plain and few.

The Canyons family (canyon, mesa, badlands, plateau) was removed in 2026-07: the single
noise-plus-fluvial engine could not make them read as their landforms, only as look-alike
eroded hills. They come back when they have real generators (strata, cap-rock, scarp
retreat). See docs/TERRAIN-CRITIQUE.md for the full diagnosis.
"""

# fluvial defaults shared by the mountain and lowland stacks, so each states only what
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
    "dunes": [   # a field of many crisp transverse dunes marching downwind. Frequency is high so
                 # the tile carries a dozen crests, not two soft mounds; the trailing thermal is a
                 # single high-talus clip that only knocks off single-pixel spikes and lets the slip
                 # face settle toward the repose angle -- it must NOT round the whole lee back to a
                 # blob (the old talus=0.02, 3 iters did exactly that).
        {"kind": "dunes", "wind": 35, "frequency": 8, "sharpness": 0.62, "warp": 0.14,
         "variation": 0.5, "mix": "replace"},
        {"kind": "thermal", "talus": 0.06, "factor": 0.5, "iterations": 1},
    ],
    "sand_sea": [   # broader, lower dunes over a large erg with faint underlying sand-sheet noise.
        {"kind": "dunes", "wind": 22, "frequency": 5, "sharpness": 0.66, "warp": 0.2,
         "variation": 0.6, "mix": "replace"},
        {"kind": "noise", "ridged": 0.1, "detail_strength": 0.25, "octaves": 4, "warp": 100,
         "mix": "add", "amount": 0.1},
        {"kind": "thermal", "talus": 0.06, "factor": 0.5, "iterations": 1},
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
    "hills":     {"height": 9.0,  "sea_level": 0.30},
    "plains":    {"height": 5.0,  "sea_level": 0.32},
    "coastal":   {"height": 12.0, "sea_level": 0.34},
    "islands":   {"height": 14.0, "sea_level": 0.34},
    "dunes":     {"height": 8.0,  "sea_level": 0.0},
    "sand_sea":  {"height": 9.0,  "sea_level": 0.0},
}

# Family grouping, for the panel dropdown ordering and docs.
FAMILIES = {
    "Mountains": ["alpine", "glacial", "foothills"],
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
