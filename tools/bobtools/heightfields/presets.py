"""Named knob sets for a bake, so a good look is one name, not a dozen dials.

A preset is a flat knob dict; build_params expands it into the full pass recipe
(base smooth, hydraulic with a radius brush, thermal, final smooth), which is what
keeps the result from going spiky or gritty. Callers start from a preset's knobs
and override fields. Droplet counts are a density at params.REFERENCE_SIZE and
scale to the bake resolution. Keep these plain and few.
"""

from .params import build_params

PRESET_KNOBS = {
    "foothills": {
        "seed": 7, "octaves": 5, "ridged": 0.4, "detail_strength": 0.45, "warp": 90,
        "droplets": 1_500_000, "erosion": 0.4, "deposition": 0.4, "radius": 4,
        "max_steps": 96, "thermal_iters": 6,
    },
    "alpine": {
        "seed": 7, "octaves": 6, "ridged": 0.6, "detail_strength": 0.6, "warp": 80,
        "droplets": 2_000_000, "erosion": 0.5, "deposition": 0.3, "radius": 4,
        "max_steps": 110, "thermal_iters": 8, "base_smooth": 1.2,
    },
    "badlands": {
        "seed": 3, "octaves": 6, "ridged": 0.35, "detail_strength": 0.5, "warp": 70,
        "droplets": 2_500_000, "erosion": 0.7, "deposition": 0.2, "radius": 3,
        "max_steps": 120, "thermal_iters": 4, "evaporation": 0.015,
        "base_smooth": 1.0, "final_smooth": 0.6,
    },
    "rolling": {
        "seed": 11, "octaves": 5, "ridged": 0.15, "detail_strength": 0.35, "warp": 100,
        "droplets": 1_200_000, "erosion": 0.25, "deposition": 0.5, "radius": 5,
        "max_steps": 90, "thermal_iters": 10, "base_smooth": 2.0, "final_smooth": 1.0,
    },
    "canyon": {
        "seed": 5, "octaves": 6, "ridged": 0.5, "detail_strength": 0.55, "warp": 60,
        "droplets": 2_600_000, "erosion": 0.85, "deposition": 0.15, "radius": 2,
        "max_steps": 150, "thermal_iters": 3, "evaporation": 0.012, "final_smooth": 0.6,
    },
    "mesa": {
        "seed": 9, "octaves": 5, "ridged": 0.25, "detail_strength": 0.4, "warp": 70,
        "droplets": 1_800_000, "erosion": 0.35, "deposition": 0.45, "radius": 4,
        "max_steps": 100, "thermal_iters": 14, "talus": 0.01, "thermal_factor": 0.5,
    },
    "islands": {
        "seed": 2, "octaves": 5, "ridged": 0.4, "detail_strength": 0.5, "warp": 90,
        "droplets": 1_600_000, "erosion": 0.45, "deposition": 0.35, "radius": 4,
        "max_steps": 100, "thermal_iters": 6, "edge_falloff": 0.22, "falloff_power": 2.2,
    },
}

PRESETS = list(PRESET_KNOBS)


def knobs(name):
    """Return a copy of a preset's raw knob dict."""
    if name not in PRESET_KNOBS:
        raise ValueError(f"unknown preset: {name!r} (have: {sorted(PRESET_KNOBS)})")
    return dict(PRESET_KNOBS[name])


def get(name):
    """Return a full bake params dict for a preset (knobs expanded)."""
    return build_params(knobs(name))
