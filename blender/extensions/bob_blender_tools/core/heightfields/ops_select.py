"""Selector ops: produce a [0,1] mask that gates where a filter applies.

Selectors are the World-Creator "control" layer: `terrace only high flat ground`, `erode only
slopes`, `deposit only in channels`. They deliberately mirror the mask vocabulary the scatter
recipe and the BobShaders terrain material already use (slope band, altitude band, curvature,
flow, noise), so terrain SHAPE, SCATTER, and TEXTURING key off the same selectors and agree.

Each selector is `(h, xp, params) -> mask` in [0,1]. Combine them with mask_mode when a filter
carries one. Height is treated as the normalised current field.
"""

from . import ops_erode


def _smoothband(v, xp, low, high, falloff):
    """1 inside [low, high], easing to 0 over `falloff` on each side."""
    f = max(float(falloff), 1e-6)
    lo = xp.clip((v - (low - f)) / f, 0.0, 1.0)
    hi = xp.clip(((high + f) - v) / f, 0.0, 1.0)
    return lo * hi


def sel_height(h, xp, low=0.0, high=1.0, falloff=0.1):
    return _smoothband(h, xp, low, high, falloff)


def sel_slope(h, xp, low=0.0, high=1.0, falloff=0.1, cell=1.0):
    s = ops_erode._slope(h, xp, cell)
    s = s / xp.maximum(s.max(), 1e-9)
    return _smoothband(s, xp, low, high, falloff)


def sel_curvature(h, xp, mode="convex", strength=1.0, cell=1.0):
    """Convex (ridges/rims) vs concave (valleys/hollows) from the Laplacian."""
    lap = (ops_erode._sh(xp, h, -1, 0) + ops_erode._sh(xp, h, 1, 0)
           + ops_erode._sh(xp, h, 0, -1) + ops_erode._sh(xp, h, 0, 1) - 4.0 * h) / (cell * cell)
    lap = lap / xp.maximum(xp.abs(lap).max(), 1e-9)
    c = -lap if mode == "convex" else lap
    return xp.clip(0.5 + strength * c, 0.0, 1.0)


def sel_flow(h, xp, threshold=0.02, fill_iters=700, acc_iters=700, mfd_p=1.4, cell=1.0):
    """Drainage/flow mask: 1 in channels that collect flow (accumulation above `threshold` of
    the max), 0 on hillslopes. The wet-channel selector for depositing sediment or texturing riverbeds."""
    filled = ops_erode._pd_fill(h, xp, fill_iters, 1e-4)
    acc = ops_erode._mfd_accum(filled, xp, acc_iters, mfd_p, cell)
    acc = acc / xp.maximum(acc.max(), 1e-9)
    f = max(float(threshold), 1e-6)
    return xp.clip(acc / f, 0.0, 1.0)


def sel_noise(h, xp, frequency=6.0, seed=0, contrast=0.5):
    from . import ops_generate
    import numpy as np
    u = (xp.arange(h.shape[0], dtype=xp.float64) + 0.5) / h.shape[0]
    x, y = xp.meshgrid(u, u)
    n = ops_generate._value_noise(xp, x, y, frequency, seed)
    inv = 1.0 - float(contrast)
    return xp.clip((n - 0.5) / max(inv, 1e-3) + 0.5, 0.0, 1.0)


def sel_path(h, xp, curves=(), width=0.02, falloff=0.04):
    """Channel-band mask: 1 within `width` of any curve polyline, easing to 0 over `falloff`. Lets an
    erosion op be masked to the carved band (naturalise only the channel, leave the sculpt alone)."""
    from . import ops_carve
    if not curves:
        return xp.zeros_like(h)
    dist = ops_carve._distance_uv(h.shape, curves, xp, ops_erode._ndimage(xp))
    return ops_carve._profile(dist, width, falloff, xp)


SELECTORS = {
    "height": sel_height,
    "slope": sel_slope,
    "curvature": sel_curvature,
    "flow": sel_flow,
    "noise": sel_noise,
    "path": sel_path,
}
