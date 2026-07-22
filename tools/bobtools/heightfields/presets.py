"""Named terrain recipes: each preset is a filter STACK, not a flat knob set.

A preset is an ordered op list evaluated by engine.run_stack: the first op is a
generator (noise / dunes / voronoi) that establishes the base, and later ops erode
and shape it. Each op carries only its DISTINCTIVE parameters; the engine's op
defaults fill in the rest, so a stack reads as the handful of choices that give a
family its character.

These are the neutral, as-authored looks. The five curated global knobs
(Relief / Detail / Erosion / Warp / Seed) modulate a COPY of the active stack at
bake time -- see params.resolve_stack -- with every knob at 0.5 reproducing the
stack exactly as written here. Presets are grouped into families:

  Mountains       alpine, glacial, foothills
  Lowlands        hills, plains, coastal, islands
  Canyons         mesa, canyon
  Dunes           dunes, sand_sea

The mountain stacks pair ridged-multifractal noise with stream-power fluvial erosion
(see heightfields/ops_erode.fluvial); the lowlands use gentler versions of the same, with
falloff shaping coastal and islands. The Canyons family uses its OWN processes -- flat layered
strata (ops_generate.strata) dissected by cap-rock scarp retreat (ops_erode.scarp) for mesa, and
the stream-power hero incising a smoothed strata PLATEAU for canyon -- not eroded noise. Keep
these plain and few.

The old Canyons family (canyon, mesa, badlands, plateau) was removed in 2026-07 because the
single noise-plus-fluvial engine could only make them look like eroded hills. Mesa and canyon are
now back with real generators; badlands and plateau return as their own generators land.
See docs/TERRAIN-CRITIQUE.md for the full diagnosis.
"""

import math

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
    # --- Canyons & mesas (real strata + cap-rock scarp / plateau incision, not eroded noise) ---
    "mesa": [   # flat-topped tables and buttes: layered strata dissected by cliff retreat into
                # isolated caps with near-vertical sides and talus aprons. NOT dendritic fluvial.
        {"kind": "strata", "layers": 5, "dissection": 1.4, "base_freq": 3.0},
        {"kind": "scarp", "iterations": 12, "cap_slope": 0.10, "undercut": 0.0015,
         "talus": 0.14, "open_size": 6},
        {"kind": "thermal", "talus": 0.14, "factor": 0.5, "iterations": 1},
    ],
    "canyon": [   # dendritic canyons incised into a high layered PLATEAU: flat rims survive, the
                  # stream-power hero cuts confined steep-walled channels, strata show in the walls.
                  # A near-flat plateau base (smoothed strata) instead of the old ridged-noise hill.
        {"kind": "strata", "layers": 3, "dissection": 1.0, "base_freq": 1.7, "smooth": 5.0},
        {"kind": "scarp", "iterations": 6, "cap_slope": 0.12, "undercut": 0.003,
         "talus": 0.12, "open_size": 8},
        _fluvial(iterations=120, k=0.024, diffusion=0.03, talus=0.005),
        {"kind": "thermal", "talus": 0.02, "factor": 0.5, "iterations": 2},
    ],
    # --- Dunes ---
    "dunes": [   # a field of many crisp transverse dunes marching downwind. Frequency is high so
                 # the tile carries a dozen crests, not two soft mounds; the trailing thermal is a
                 # single high-talus clip that only knocks off single-pixel spikes and lets the slip
                 # face settle toward the repose angle -- it must NOT round the whole lee back to a
                 # blob (the old talus=0.02, 3 iters did exactly that).
        {"kind": "dunes", "wind": 35, "frequency": 8, "sharpness": 0.62, "warp": 0.14,
         "variation": 0.5, "mix": "replace"},
        # settle the lee to the real sand slip-face repose (~34 deg) at ANY bake resolution: talus is
        # derived from repose_deg, not a fixed value that would clip at a different angle per size.
        {"kind": "thermal", "repose_deg": 34, "factor": 0.5, "iterations": 1},
    ],
    "sand_sea": [   # broader, lower dunes over a large erg with faint underlying sand-sheet noise.
        {"kind": "dunes", "wind": 22, "frequency": 5, "sharpness": 0.66, "warp": 0.2,
         "variation": 0.6, "mix": "replace"},
        {"kind": "noise", "ridged": 0.1, "detail_strength": 0.25, "octaves": 4, "warp": 100,
         "mix": "add", "amount": 0.1},
        {"kind": "thermal", "repose_deg": 34, "factor": 0.5, "iterations": 1},
    ],
}

# Blender-side displacement defaults per preset. Scale is REAL-WORLD: 1 Blender unit = 1 metre.
# The vertical relief is NOT a fixed metre value -- it is a RELIEF RATIO (typical relief / tile
# width) that is roughly scale-invariant for a landform, so the metre height is derived from the
# artist's tile size at build time (see height_for). This keeps a preset physically proportioned at
# ANY tile size: a 90 m patch of alpine gets ~27 m of relief, a 4 km alpine range gets ~1.2 km, and
# a 1.8 m character or a 6 m house dropped on either reads correctly. sea_level is the fraction of
# the normalised field taken as water/base. The field itself is always normalised [0, 1]; relief and
# sea_level live here so presets.py stays the single source of truth for the panel table
# (see gen_panel_presets.py).
#
# Ratios are physically grounded, not visually exaggerated: dunes are deliberately subtle on a small
# tile (a real 90 m dune field has ~1 m dunes) -- big dunes come from a big tile, not from inflating
# the height. Dune/sand_sea ratios are set so the transverse slip face lands near the ~34 deg angle
# of repose at the authored crest frequency.
DISPLAY = {
    "alpine":    {"relief": 0.30,  "sea_level": 0.22},
    "glacial":   {"relief": 0.22,  "sea_level": 0.24},
    "foothills": {"relief": 0.12,  "sea_level": 0.30},
    "hills":     {"relief": 0.05,  "sea_level": 0.30},
    "plains":    {"relief": 0.015, "sea_level": 0.32},
    "coastal":   {"relief": 0.05,  "sea_level": 0.34},
    "islands":   {"relief": 0.08,  "sea_level": 0.34},
    "mesa":      {"relief": 0.18,  "sea_level": 0.05},
    "canyon":    {"relief": 0.22,  "sea_level": 0.10},
    "dunes":     {"relief": 0.012, "sea_level": 0.0},
    "sand_sea":  {"relief": 0.019, "sea_level": 0.0},
}

# Absolute guardrails on derived relief (metres): never a degenerate flat sheet, never a vertical
# wall wider-than-tall nonsense. The ceiling (0.6 * size) caps overall grade near ~31 degrees so a
# stretched tile cannot become a cliff-cube.
_RELIEF_MIN_M = 0.5
_RELIEF_CEIL_FRAC = 0.6

# Real angles of repose, degrees. An op may carry `repose_deg` instead of a hand-picked `talus`;
# params.build_params converts it to the resolution-correct talus (see talus_for_angle) so the
# rendered slope holds the same PHYSICAL angle at any bake resolution and tile size. Dry sand and
# dune slip faces sit near 34 deg; loose rock talus/scree 30-37 deg. These are the physical targets a
# thermal/scarp/fluvial pass relaxes a slope down to.
REPOSE = {"sand": 34.0, "dune_slip": 34.0, "scree": 35.0, "talus_rock": 33.0}


def talus_for_angle(angle_deg, bake_res, relief_ratio):
    """The normalised per-cell `talus` threshold that renders as a real slope of `angle_deg`.

    The heightfield is normalised [0, 1] and displaced at build time by height = relief_ratio * tile
    over a tile of `bake_res` cells, so one cell of normalised rise `t` renders as a real slope
    tan(theta) = t * relief_ratio * bake_res. Inverting gives the talus that produces `angle_deg`:

        talus = tan(angle) / (relief_ratio * bake_res)

    Because relief is a scale-invariant ratio of tile width, this depends only on the bake
    resolution, not on the absolute tile size -- so a preset holds the same repose angle whether it
    is a 90 m patch or a 4 km range. It DOES scale with bake_res (a fixed talus would clip at a
    different real angle at preview vs full resolution), which is the resolution bug this fixes."""
    t = math.tan(math.radians(float(angle_deg)))
    return t / max(float(relief_ratio) * float(bake_res), 1e-9)

# Family grouping, for the panel dropdown ordering and docs.
FAMILIES = {
    "Mountains": ["alpine", "glacial", "foothills"],
    "Lowlands": ["hills", "plains", "coastal", "islands"],
    "Canyons": ["mesa", "canyon"],
    "Dunes": ["dunes", "sand_sea"],
}

PRESETS = list(STACKS)


def stack(name):
    """Return a deep-ish copy of a preset's op stack (op dicts copied one level)."""
    if name not in STACKS:
        raise ValueError(f"unknown preset: {name!r} (have: {sorted(STACKS)})")
    return [dict(op) for op in STACKS[name]]


_DEFAULT_DISPLAY = {"relief": 0.08, "sea_level": 0.30}


def display(name):
    """Return the Blender displacement defaults (relief ratio, sea_level) for a preset."""
    return dict(DISPLAY.get(name, _DEFAULT_DISPLAY))


def relief(name):
    """The relief ratio (typical relief / tile width) for a preset. Scale-invariant."""
    return float(DISPLAY.get(name, _DEFAULT_DISPLAY)["relief"])


def height_for(name, size):
    """Derive real-world vertical relief in METRES for a preset at a given tile size (metres).

    height = relief_ratio * size, clamped to [_RELIEF_MIN_M, _RELIEF_CEIL_FRAC * size] so a preset
    is neither a flat sheet nor a taller-than-wide wall. With 1 Blender unit = 1 m this is the
    Height fed to heightmap_terrain, so a preset stays physically proportioned at any tile size."""
    size = float(size)
    h = relief(name) * size
    return max(_RELIEF_MIN_M, min(h, _RELIEF_CEIL_FRAC * size))


def get(name):
    """Return a full bake params dict for a preset (stack resolved at neutral knobs)."""
    from . import params  # lazy: params imports presets, avoid a cycle at import time
    return params.build_params({"preset": name})
