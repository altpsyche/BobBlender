"""The terrain op-stack evaluator.

A terrain recipe is an ordered list of ops (`{"kind": ..., **params}`) evaluated on a float
heightfield, generalising the old flat `passes` list into the World-Creator-style composable
stack: generators write height, filters transform it, and selectors mask WHERE a filter applies.
Every op runs on the backend array module (`backend.xp`), so the whole stack runs on GPU (CuPy)
or CPU (numpy) unchanged. Deterministic and resolution-consistent.

Per-op masking: an op may carry `"mask": {"kind": <selector>, **params}`; the filter is then
blended by that selector's [0,1] mask, `h = h_before*(1-mask) + h_after*mask`, so a filter only
acts where the selector says (erode slopes, terrace high ground, deposit in channels).

`run_stack(field, stack, backend, seed)` returns a normalised [0, 1] numpy heightfield.
"""

import numpy as np

from . import ops_erode, ops_filter, ops_generate, ops_select


def _wrap(fn):
    return lambda h, xp, p, seed: fn(h, xp, **p)


def _op_generate_noise(h, xp, p, seed):
    # base noise via the resolution-independent generator, mixed into the field
    from . import generate
    field = xp.asarray(generate.generate_base(h.shape[0], seed=int(p.get("seed", seed)),
                                               octaves=int(p.get("octaves", 6)),
                                               roughness=float(p.get("roughness", 0.5)),
                                               ridged=float(p.get("ridged", 0.5)),
                                               warp=p.get("warp"),
                                               detail_strength=float(p.get("detail_strength", 0.6))),
                       dtype=xp.float64)
    mode = p.get("mix", "replace")
    amount = float(p.get("amount", 1.0))
    if mode == "add":
        return h + amount * field
    if mode == "max":
        return xp.maximum(h, field)
    return field


# op registry: generators, filters, erosion. Selectors live in ops_select and are applied as masks.
_OPS = {
    "noise": _op_generate_noise,
    "dunes": _wrap(ops_generate.dunes),
    "voronoi": _wrap(ops_generate.voronoi),
    "fluvial": _wrap(ops_erode.fluvial),
    "pipe_hydraulic": _wrap(ops_erode.pipe_hydraulic),
    "thermal": _wrap(ops_erode.thermal),
    "terrace": _wrap(ops_filter.terrace),
    "warp": _wrap(ops_filter.warp),
    "curve": _wrap(ops_filter.curve),
    "sharpen": _wrap(ops_filter.sharpen),
    "smooth": lambda h, xp, p, seed: ops_erode._ndimage(xp).gaussian_filter(
        h, max(float(p.get("sigma", 1.0)), 1e-3), mode="nearest"),
}


def register_op(kind, fn):
    """Register an op handler `fn(h, xp, params, seed) -> h`."""
    _OPS[kind] = fn


def _mask_for(h, xp, spec):
    kind = spec.get("kind")
    sel = ops_select.SELECTORS.get(kind)
    if sel is None:
        raise ValueError(f"unknown selector: {kind!r} (have: {sorted(ops_select.SELECTORS)})")
    return sel(h, xp, **{k: v for k, v in spec.items() if k != "kind"})


def run_stack(field, stack, backend, seed=0):
    """Evaluate an ordered op stack on `field`; return a normalised [0, 1] numpy array.

    Each op is `{"kind": <registered>, **params}` and may carry `"mask": {selector spec}`.
    Unknown op or selector kinds raise so a typo is loud."""
    xp = backend.xp
    h = xp.asarray(field, dtype=xp.float64)
    for i, op in enumerate(stack):
        spec = dict(op)
        kind = spec.pop("kind")
        mask_spec = spec.pop("mask", None)
        handler = _OPS.get(kind)
        if handler is None:
            raise ValueError(f"unknown terrain op: {kind!r} (have: {sorted(_OPS)})")
        before = h
        after = handler(h, xp, spec, seed + 101 * (i + 1))
        if mask_spec is not None:
            m = xp.clip(_mask_for(before, xp, mask_spec), 0.0, 1.0)
            after = before * (1.0 - m) + after * m
        h = xp.clip(after, 0.0, None)
    h = h - h.min()
    h = h / xp.maximum(h.max(), 1e-9)
    return backend.asnumpy(h).astype(np.float64)
