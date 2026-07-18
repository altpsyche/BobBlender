"""Turn a flat set of knobs into a full bake params dict.

One place builds the erosion recipe (base smooth, hydraulic, thermal, final
smooth, optional edge falloff), so presets, the Blender panel, and the CLI all
share it instead of each hand-writing a pass list. Knobs are the simple dial an
artist turns; build_params expands them into the pass structure the pipeline runs.

Droplet count is expressed as a density: the value is the count at REFERENCE_SIZE
and the pipeline scales it to the actual bake resolution (see pipeline._scale_passes),
so a preview and a full bake stay consistent instead of the low-res one over-eroding.
"""

REFERENCE_SIZE = 768  # droplet density is quoted at this resolution
PREVIEW_SIZE = 256    # bake(preview=True) resolution
MIN_DROPLETS = 20_000  # floor so a tiny preview still erodes

_DEFAULT_KNOBS = dict(
    size=REFERENCE_SIZE, seed=7, backend="auto",
    # shape
    octaves=5, roughness=0.5, ridged=0.4, detail_strength=0.45, warp=90,
    # hydraulic (droplets is a density at REFERENCE_SIZE)
    droplets=1_500_000, erosion=0.4, deposition=0.4, capacity=8.0,
    max_steps=96, radius=4, evaporation=0.02,
    # thermal
    thermal_iters=6, talus=0.005, thermal_factor=0.4,
    # smoothing that brackets the hydraulic pass
    base_smooth=1.5, final_smooth=0.8,
    # optional edge taper (0 = off); fraction of the shorter side eased in
    edge_falloff=0.0, falloff_power=2.0,
)


def default_knobs() -> dict:
    """A copy of the knob defaults, so callers can start from a known baseline."""
    return dict(_DEFAULT_KNOBS)


def build_params(knobs: dict | None = None) -> dict:
    """Expand flat knobs into a bake params dict (generate + ordered passes)."""
    k = {**_DEFAULT_KNOBS, **(knobs or {})}

    passes = []
    if k["base_smooth"] > 0:
        passes.append({"kind": "smooth", "sigma": k["base_smooth"]})
    if k["edge_falloff"] > 0:
        passes.append({"kind": "falloff", "margin": k["edge_falloff"],
                       "power": k["falloff_power"]})
    passes.append({
        "kind": "hydraulic", "density": k["droplets"], "erosion": k["erosion"],
        "deposition": k["deposition"], "capacity": k["capacity"],
        "max_steps": k["max_steps"], "radius": k["radius"],
        "evaporation": k["evaporation"],
    })
    if k["thermal_iters"] > 0:
        passes.append({"kind": "thermal", "talus": k["talus"],
                       "factor": k["thermal_factor"], "iterations": k["thermal_iters"]})
    if k["final_smooth"] > 0:
        passes.append({"kind": "smooth", "sigma": k["final_smooth"]})

    return {
        "size": k["size"], "seed": k["seed"], "backend": k["backend"],
        "generate": {"octaves": k["octaves"], "roughness": k["roughness"],
                     "ridged": k["ridged"], "detail_strength": k["detail_strength"],
                     "warp": k["warp"]},
        "passes": passes,
    }
