"""Bake orchestration: params in, a 16-bit PNG plus sidecar out.

bake() is the one entry the MCP tool and any script call. It resolves the
backend, generates the base field, runs the erosion passes, writes the PNG and
its reproducibility sidecar, and honors the params-hash cache. Absolute out_path
only; the caller resolves repo-relative paths.
"""

import time

import numpy as np

from . import backend as backend_mod
from . import cache, erode, generate, io
from .params import MIN_DROPLETS, PREVIEW_SIZE, REFERENCE_SIZE

DEFAULT_PASSES = [
    {"kind": "hydraulic"},
    {"kind": "thermal", "talus": 0.008, "factor": 0.3, "iterations": 2},
]


def _scale_passes(passes, size):
    """Resolve droplet density into an absolute count for this resolution.

    A hydraulic pass may quote droplets as a `density` (the count at
    REFERENCE_SIZE); scale it by the cell-count ratio so a low-res preview does
    not over-erode. A pass that gives an absolute `droplets` is left untouched.
    """
    scale = (size / REFERENCE_SIZE) ** 2
    out = []
    for spec in passes:
        spec = dict(spec)
        if spec.get("kind") == "hydraulic" and "density" in spec:
            density = spec.pop("density")
            spec["droplets"] = max(MIN_DROPLETS, int(density * scale))
        out.append(spec)
    return out


def bake(out_path: str, params: dict, force: bool = False, preview: bool = False) -> dict:
    """Generate and erode a heightfield to out_path. Returns stats + metadata.

    preview bakes at PREVIEW_SIZE for a fast look; droplet density scales with it,
    so agent and CLI runs are resolution-independent, not just the panel.
    """
    params = dict(params)
    if preview:
        params["size"] = PREVIEW_SIZE

    size = int(params.get("size", 512))
    seed = int(params.get("seed", 0))
    passes = _scale_passes(params.get("passes", DEFAULT_PASSES), size)
    backend = backend_mod.select(params.get("backend", "auto"))

    # Key the cache on the RESOLVED recipe: the scaled pass list and the backend
    # that actually runs (backend.name, not "auto"). This stops a GPU-baked sidecar
    # from being served to a CPU-only machine that would resolve "auto" differently,
    # and drops params keys that do not affect output from the key.
    resolved = {"size": size, "seed": seed, "backend": backend.name,
                "passes": passes, "generate": params.get("generate", {})}
    key = cache.params_hash(resolved)
    if not force:
        side = io.read_sidecar(out_path)
        if side is not None and side.get("hash") == key:
            return {**side, "cached": True}

    t0 = time.perf_counter()
    base = generate.generate_base(size, seed=seed, **params.get("generate", {}))
    # Erode with a reflected margin and crop it off, so the visible edges were
    # interior during erosion. Otherwise borders erode less (clipped brush, short
    # droplet paths) and stick up as a rim.
    margin = max(16, size // 12)
    padded = np.pad(base, margin, mode="reflect")
    eroded = erode.run_passes(padded, passes, backend, seed=seed)
    eroded = eroded[margin:-margin, margin:-margin]
    eroded -= eroded.min()
    eroded /= max(float(eroded.max()), 1e-9)
    elapsed = time.perf_counter() - t0

    io.to_png16(eroded, out_path)
    meta = {
        "hash": key,
        "params": params,
        "backend": backend.name,
        "platform": backend.platform,
        "deterministic": True,  # both CPU and (batched fixed-point) GPU are reproducible
        "size": size,
        "seconds": round(elapsed, 3),
        "stats": {
            "min": float(eroded.min()),
            "max": float(eroded.max()),
            "mean": float(eroded.mean()),
        },
        "path": str(out_path),
    }
    io.write_sidecar(out_path, meta)
    return {**meta, "cached": False}
