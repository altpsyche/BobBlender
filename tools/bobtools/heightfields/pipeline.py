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

import hashlib
import os
import time

import numpy as np

from . import backend as backend_mod
from . import cache, engine, io, maps
from . import params as params_mod
from .params import PREVIEW_SIZE


def _stack_for(params: dict) -> list:
    """The op stack to run: an explicit `stack`, else resolve `preset` + global knobs."""
    if params.get("stack"):
        return params["stack"]
    return params_mod.build_params(params)["stack"]


def _map_path(out_path: str, kind: str) -> str:
    base, ext = os.path.splitext(out_path)
    return f"{base}_{kind}{ext or '.png'}"


def _emit_maps(out_path: str, h, backend) -> dict:
    """Write flow/wetness sidecar PNGs beside the height PNG; return {kind: path}."""
    derived = maps.derive_maps(h, backend)
    paths = {}
    for kind, arr in derived.items():
        p = _map_path(out_path, kind)
        io.to_png16(arr, p)
        paths[kind] = p
    return paths


def bake(out_path: str, params: dict, force: bool = False, preview: bool = False) -> dict:
    """Evaluate a terrain stack to out_path. Returns stats + metadata.

    preview bakes at PREVIEW_SIZE for a fast look; because the stack is
    resolution-independent, the preview and the full bake are the same landform.
    """
    params = dict(params)
    if preview:
        params["size"] = PREVIEW_SIZE

    # base_png: erode an EXISTING baked field in place of generating from zero (the carve-then-erode
    # "Bake & Erode Curves" path). The base sets the resolution, and the result is NOT re-normalised
    # (run_stack normalize=False) so it keeps the base's absolute height mapping.
    base_png = params.get("base_png")
    if base_png:
        base = io.read_png16(base_png)
        size = int(base.shape[0])
        params["size"] = size
        base_sig = hashlib.sha256(base.tobytes()).hexdigest()[:12]  # key on the loaded field itself
    else:
        size = int(params.get("size", 768))
        base = None
        base_sig = None
    seed = int(params.get("seed", 0))
    want_maps = bool(params.get("maps", False))
    stack = _stack_for(params)
    backend = backend_mod.select(params.get("backend", "auto"))

    # Key the cache on the RESOLVED recipe: the exact stack (with injected seeds and
    # knob modulations already applied) and the backend that actually runs
    # (backend.name, not "auto"), so a GPU-baked sidecar is not served to a CPU-only
    # machine that would resolve "auto" differently. The source fingerprint in
    # cache.py invalidates it when the op math changes. A base_png bake also keys on the
    # base's content so re-eroding an edited terrain re-runs.
    resolved = {"size": size, "seed": seed, "backend": backend.name, "stack": stack,
                "maps": want_maps, "base": base_sig}
    key = cache.params_hash(resolved)
    if not force:
        side = io.read_sidecar(out_path)
        if side is not None and side.get("hash") == key:
            # Re-emit maps if they were requested but a sidecar file went missing.
            if want_maps and not all(os.path.exists(p) for p in side.get("maps", {}).values()):
                _emit_maps(out_path, io.read_png16(out_path), backend)
            return {**side, "cached": True}

    t0 = time.perf_counter()
    # Generators establish the base into this zero field; the engine normalises the
    # result to [0, 1]. Erosion is edge-aware (borders are drainage outlets), so no
    # reflected margin is needed and no border rim forms. With a base_png the field is
    # the loaded terrain and the result keeps its absolute mapping (normalize=False).
    if base is None:
        base = np.zeros((size, size), dtype=np.float64)
    eroded = engine.run_stack(base, stack, backend, seed=seed, normalize=base_png is None)
    # Optional flow/wetness maps (a drainage solve on the final field) for shading and
    # scatter to key off the terrain's own hydrology; opt-in since they add cost.
    map_paths = _emit_maps(out_path, eroded, backend) if want_maps else {}
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
        "maps": map_paths,
        "stats": {
            "min": float(eroded.min()),
            "max": float(eroded.max()),
            "mean": float(eroded.mean()),
        },
        "path": str(out_path),
    }
    io.write_sidecar(out_path, meta)
    return {**meta, "cached": False}
