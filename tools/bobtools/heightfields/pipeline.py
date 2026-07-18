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

DEFAULT_PASSES = [
    {"kind": "hydraulic"},
    {"kind": "thermal", "talus": 0.008, "factor": 0.3, "iterations": 2},
]


def bake(out_path: str, params: dict, force: bool = False) -> dict:
    """Generate and erode a heightfield to out_path. Returns stats + metadata."""
    key = cache.params_hash(params)
    if not force:
        side = io.read_sidecar(out_path)
        if side is not None and side.get("hash") == key:
            return {**side, "cached": True}

    size = int(params.get("size", 512))
    seed = int(params.get("seed", 0))
    passes = params.get("passes", DEFAULT_PASSES)
    backend = backend_mod.select(params.get("backend", "auto"))

    t0 = time.perf_counter()
    base = generate.generate_base(size, seed=seed, **params.get("generate", {}))
    eroded = erode.run_passes(base, passes, backend, seed=seed)
    elapsed = time.perf_counter() - t0

    io.to_png16(eroded, out_path)
    meta = {
        "hash": key,
        "params": params,
        "backend": backend.name,
        "platform": backend.platform,
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
