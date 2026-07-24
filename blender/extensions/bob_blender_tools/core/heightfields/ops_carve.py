"""Curve distance field + profile helpers for the erosion band mask.

Rasterises BobSplines curve polylines (terrain UV space: u -> column, v -> row, both in [0, 1]) into a
distance field so the `path` selector (ops_select) can mask an erosion op to the channel corridor --
weathering the terrain along the curves while leaving the rest of the sculpt alone. The curve arrives
as a resolution-independent UV-point list ({"points": [[u, v], ...]}), NOT a pre-rasterised image, so
a bake at any size lands the band in the same place and the params-hash cache stays correct.
"""

import numpy as np

from .ops_erode import _ndimage  # noqa: F401  (re-exported: ops_select builds the EDT via this)


def _rasterize(shape, curves):
    """A numpy grid, 0.0 on the (densely sampled) polylines and 1.0 elsewhere -- the EDT seed.
    Built on numpy (a one-time setup), then handed to the backend for the distance transform."""
    n = int(shape[0])
    line = np.ones(shape, dtype=np.float64)
    rows, cols = [], []
    for cv in curves:
        pts = cv.get("points") or []
        for i in range(len(pts)):
            u0, v0 = pts[i]
            u1, v1 = pts[i + 1] if i + 1 < len(pts) else (u0, v0)
            r0, c0 = v0 * (n - 1), u0 * (n - 1)
            r1, c1 = v1 * (n - 1), u1 * (n - 1)
            steps = int(max(abs(r1 - r0), abs(c1 - c0))) * 2 + 2
            a = np.linspace(0.0, 1.0, steps)
            rows.append(np.rint(r0 + (r1 - r0) * a))
            cols.append(np.rint(c0 + (c1 - c0) * a))
    if rows:
        r = np.concatenate(rows).astype(np.int64)
        c = np.concatenate(cols).astype(np.int64)
        ok = (r >= 0) & (r < n) & (c >= 0) & (c < n)
        line[r[ok], c[ok]] = 0.0
    return line


def _distance_uv(shape, curves, xp, ndi):
    """Min UV distance (a [0, 1] fraction of the map width) from every cell to any polyline."""
    line = _rasterize(shape, curves)
    dist_px = ndi.distance_transform_edt(xp.asarray(line))
    return xp.asarray(dist_px, dtype=xp.float64) / float(shape[0])


def _smoothstep(x, xp):
    x = xp.clip(x, 0.0, 1.0)
    return x * x * (3.0 - 2.0 * x)


def _profile(dist, width, falloff, xp):
    """1.0 within `width` of the polyline, easing to 0 over `falloff` beyond it."""
    f = max(float(falloff), 1e-6)
    return _smoothstep((float(width) + f - dist) / f, xp)


def channel_seed(h, xp, curves=(), width=0.006, falloff=0.02, depth=0.03):
    """Shallow bed seed along the spline: lower h by `depth * profile` so the fluvial solver has an
    initial channel (a slope + a depression) to amplify via its drainage prior. This is a SEED, not
    the final channel -- erosion deepens it and shapes the banks from here (the graded cross-section
    is deliberately NOT stamped back on). Runs before fluvial in the stack."""
    if not curves:
        return h
    dist = _distance_uv(h.shape, curves, xp, _ndimage(xp))
    return h - float(depth) * _profile(dist, width, falloff, xp)
