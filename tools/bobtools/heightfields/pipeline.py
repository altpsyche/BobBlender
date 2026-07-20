"""Bake orchestration: params in, a 16-bit PNG plus a reproducibility sidecar out.

bake() is the one entry the MCP tool, the CLI, and any script call. It resolves the
backend, evaluates the terrain op stack (generators write the base, filters and
erosion shape it -- see engine.run_stack), writes the PNG and its sidecar, and
honours the params-hash cache. Absolute out_path only; the caller resolves
repo-relative paths.

The whole recipe is a stack now (no separate generate-then-erode step, no droplet
density to scale per resolution): generation and erosion are resolution-independent,
so a preview at PREVIEW_SIZE and a full bake are the same landform at two sampling
densities.
"""

import time

import numpy as np

from . import backend as backend_mod
from . import cache, engine, io
from . import params as params_mod
from .params import PREVIEW_SIZE


def _stack_for(params: dict) -> list:
    """The op stack to run: an explicit `stack`, else resolve `preset` + global knobs."""
    if params.get("stack"):
        return params["stack"]
    return params_mod.build_params(params)["stack"]


def bake(out_path: str, params: dict, force: bool = False, preview: bool = False) -> dict:
    """Evaluate a terrain stack to out_path. Returns stats + metadata.

    preview bakes at PREVIEW_SIZE for a fast look; because the stack is
    resolution-independent, the preview and the full bake are the same landform.
    """
    params = dict(params)
    if preview:
        params["size"] = PREVIEW_SIZE

    size = int(params.get("size", 768))
    seed = int(params.get("seed", 0))
    stack = _stack_for(params)
    backend = backend_mod.select(params.get("backend", "auto"))

    # Key the cache on the RESOLVED recipe: the exact stack (with injected seeds and
    # knob modulations already applied) and the backend that actually runs
    # (backend.name, not "auto"), so a GPU-baked sidecar is not served to a CPU-only
    # machine that would resolve "auto" differently. The source fingerprint in
    # cache.py invalidates it when the op math changes.
    resolved = {"size": size, "seed": seed, "backend": backend.name, "stack": stack}
    key = cache.params_hash(resolved)
    if not force:
        side = io.read_sidecar(out_path)
        if side is not None and side.get("hash") == key:
            return {**side, "cached": True}

    t0 = time.perf_counter()
    # Generators establish the base into this zero field; the engine normalises the
    # result to [0, 1]. Erosion is edge-aware (borders are drainage outlets), so no
    # reflected margin is needed and no border rim forms.
    base = np.zeros((size, size), dtype=np.float64)
    eroded = engine.run_stack(base, stack, backend, seed=seed)
    elapsed = time.perf_counter() - t0

    io.to_png16(eroded, out_path)
    meta = {
        "hash": key,
        "params": {k: v for k, v in params.items() if k != "stack"},
        "stack": stack,
        "backend": backend.name,
        "platform": backend.platform,
        "deterministic": True,  # pure stencils on CPU and GPU are both reproducible
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
