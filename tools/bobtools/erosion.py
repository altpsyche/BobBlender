"""Compat shim. Heightfield generation and erosion moved to the heightfields
subpackage (see docs/UNIFIED-SYSTEM.md). This keeps the old import surface working
for existing scripts. Prefer bobtools.heightfields going forward.
"""

from .heightfields.erode import run_passes, stream_power, thermal
from .heightfields.generate import generate_base
from .heightfields.io import to_png16


def erode(h, iterations=35, rain=1.0, erosion=0.6, m=0.9, n=1.1,
          talus=0.008, thermal_factor=0.35):
    """Stream-power + thermal erosion, the original numpy pipeline."""
    return stream_power(
        h.astype(float).copy(), iterations=iterations, rain=rain, erosion=erosion,
        m=m, n=n, talus=talus, thermal_factor=thermal_factor,
    )


def generate_and_erode(out_path, size=512, seed=0, iterations=35, **base_kwargs):
    base = generate_base(size, seed=seed, **base_kwargs)
    eroded = erode(base, iterations=iterations)
    eroded -= eroded.min()
    eroded /= max(float(eroded.max()), 1e-9)
    to_png16(eroded, out_path)
    return {
        "path": str(out_path), "size": size, "iterations": iterations,
        "min": float(eroded.min()), "max": float(eroded.max()),
    }


__all__ = [
    "generate_base", "erode", "stream_power", "thermal", "run_passes",
    "to_png16", "generate_and_erode",
]
