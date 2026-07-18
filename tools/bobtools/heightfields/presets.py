"""Named parameter sets for a bake, so a good look is one name, not twelve knobs.

A preset is a full bake params dict minus the output path. Callers start from a
preset and override fields. Each uses the droplet-hydraulic pass with a radius
erosion brush, bracketed by a light base smooth and a final smooth, which is what
keeps the result from going spiky or gritty. Keep these plain and few.
"""

FOOTHILLS = {
    "size": 768,
    "seed": 7,
    "generate": {"octaves": 5, "roughness": 0.45, "ridged": 0.4, "detail_strength": 0.45, "warp": 90},
    "passes": [
        {"kind": "smooth", "sigma": 1.5},
        {"kind": "hydraulic", "droplets": 1_500_000, "erosion": 0.4, "deposition": 0.4,
         "capacity": 8, "max_steps": 96, "radius": 4},
        {"kind": "thermal", "talus": 0.005, "factor": 0.4, "iterations": 6},
        {"kind": "smooth", "sigma": 0.8},
    ],
}

ALPINE = {
    "size": 768,
    "seed": 7,
    "generate": {"octaves": 6, "roughness": 0.5, "ridged": 0.6, "detail_strength": 0.6, "warp": 80},
    "passes": [
        {"kind": "smooth", "sigma": 1.2},
        {"kind": "hydraulic", "droplets": 2_000_000, "erosion": 0.5, "deposition": 0.3,
         "capacity": 9, "max_steps": 110, "radius": 4},
        {"kind": "thermal", "talus": 0.004, "factor": 0.4, "iterations": 8},
        {"kind": "smooth", "sigma": 0.8},
    ],
}

BADLANDS = {
    "size": 768,
    "seed": 3,
    "generate": {"octaves": 6, "roughness": 0.55, "ridged": 0.35, "detail_strength": 0.5, "warp": 70},
    "passes": [
        {"kind": "smooth", "sigma": 1.0},
        {"kind": "hydraulic", "droplets": 2_500_000, "erosion": 0.7, "deposition": 0.2,
         "capacity": 11, "max_steps": 120, "radius": 3, "evaporation": 0.015},
        {"kind": "thermal", "talus": 0.006, "factor": 0.35, "iterations": 4},
        {"kind": "smooth", "sigma": 0.6},
    ],
}

PRESETS = {"foothills": FOOTHILLS, "alpine": ALPINE, "badlands": BADLANDS}


def get(name):
    """Return a deep copy of a preset params dict."""
    import copy

    if name not in PRESETS:
        raise ValueError(f"unknown preset: {name!r} (have: {sorted(PRESETS)})")
    return copy.deepcopy(PRESETS[name])
