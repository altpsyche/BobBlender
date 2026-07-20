"""The terrain op-stack evaluator.

A terrain recipe is an ordered list of ops (`{"kind": ..., **params}`) evaluated on a float
heightfield, generalising the old flat `passes` list into the World-Creator-style composable
stack: generators write height, filters transform it, and (later) selectors mask where a filter
applies. Every op runs on the backend array module (`backend.xp`), so the whole stack runs on
GPU (CuPy) or CPU (numpy) unchanged. Deterministic and resolution-consistent.

`run_stack(field, stack, backend, seed)` returns a normalised [0, 1] numpy heightfield.
"""

import numpy as np

from . import ops_erode


def _op_pipe_hydraulic(h, xp, p, seed):
    return ops_erode.pipe_hydraulic(h, xp, **p)


def _op_thermal(h, xp, p, seed):
    return ops_erode.thermal(h, xp, **p)


def _op_fluvial(h, xp, p, seed):
    return ops_erode.fluvial(h, xp, **p)


def _op_smooth(h, xp, p, seed):
    ndi = ops_erode._ndimage(xp)
    return ndi.gaussian_filter(h, max(float(p.get("sigma", 1.0)), 1e-3), mode="nearest")


# The op registry. Generators (noise, dunes, ...) and selectors join this in P2; the panel and
# presets only ever reference these kind strings, never the implementations.
_OPS = {
    "pipe_hydraulic": _op_pipe_hydraulic,
    "fluvial": _op_fluvial,
    "thermal": _op_thermal,
    "smooth": _op_smooth,
}


def register_op(kind, fn):
    """Register an op handler `fn(h, xp, params, seed) -> h`, so new generators/filters/selectors
    plug in without touching the evaluator."""
    _OPS[kind] = fn


def run_stack(field, stack, backend, seed=0):
    """Evaluate an ordered op stack on `field` and return a normalised [0, 1] numpy array.

    `field` is the starting heightfield (e.g. the generated base); ops transform it in order.
    Each op is `{"kind": <registered>, **params}`. Unknown kinds raise so a typo is loud."""
    xp = backend.xp
    h = xp.asarray(field, dtype=xp.float64)
    for i, op in enumerate(stack):
        spec = dict(op)
        kind = spec.pop("kind")
        handler = _OPS.get(kind)
        if handler is None:
            raise ValueError(f"unknown terrain op: {kind!r} (have: {sorted(_OPS)})")
        h = handler(h, xp, spec, seed + 101 * (i + 1))
        h = xp.clip(h, 0.0, None)
    h = h - h.min()
    h = h / xp.maximum(h.max(), 1e-9)
    return backend.asnumpy(h).astype(np.float64)
