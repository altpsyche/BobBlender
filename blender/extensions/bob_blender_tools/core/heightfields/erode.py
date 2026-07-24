"""Legacy CPU erosion helpers: thermal, stream-power, smoothing, edge falloff.

The terrain pipeline no longer runs these -- generation and erosion are now a GPU
op stack (see engine.run_stack, ops_erode). What remains here is the small set of
numpy helpers still used by the compat shim (bobtools.erosion) and by run_passes,
which composes an ordered list of these CPU passes:

- thermal: slump material down slopes steeper than a talus angle. Cheap stencil.
- stream_power: drainage-area incision (carves valley networks). CPU only; the flow
  accumulation is an inherently sequential topological sum.
- smooth / edge_falloff: gaussian blur and a border taper.

The droplet-hydraulic simulation (a CuPy RawKernel plus a scalar numpy reference)
was retired here: the vectorised pipe-model + flow-accumulation stream-power ops in
ops_erode supersede it, run on both CPU and GPU with no per-droplet loop, and carve
canyons the droplet model never could.

run_passes(h, passes, backend, seed) applies a list and returns a [0, 1] field.
"""

import numpy as np

SQRT2 = 2.0 ** 0.5
_NEIGHBOURS = [
    (-1, 0, 1.0), (1, 0, 1.0), (0, -1, 1.0), (0, 1, 1.0),
    (-1, -1, SQRT2), (-1, 1, SQRT2), (1, -1, SQRT2), (1, 1, SQRT2),
]


# Thermal and stream-power (CPU).

def _edge_shift(a, dy, dx, mode="edge"):
    padded = np.pad(a, 1, mode=mode)
    return padded[1 + dy : 1 + dy + a.shape[0], 1 + dx : 1 + dx + a.shape[1]]


def thermal(h, talus=0.008, factor=0.35, iterations=1):
    """Slump material down 4-neighbour slopes steeper than talus. In place.

    Mass-conserving at the border: the outflow term edge-pads (a boundary cell sees
    itself as its off-grid neighbour, so nothing flows off-grid), while the reinjection
    zero-pads (no material arrives from off-grid sources). Edge-padding the reinjection
    instead re-adds a border cell's own outflow, manufacturing mass along the rim.
    """
    for _ in range(iterations):
        for dy, dx, _dist in _NEIGHBOURS[:4]:
            diff = h - _edge_shift(h, dy, dx)
            move = np.clip((diff - talus) * factor, 0.0, None)
            h -= move
            h += _edge_shift(move, -dy, -dx, mode="constant")
    return h


def _receivers(h):
    rows, cols = h.shape
    best_drop = np.zeros_like(h)
    best_k = np.zeros(h.shape, dtype=np.int64)
    for k, (dy, dx, dist) in enumerate(_NEIGHBOURS):
        drop = (h - _edge_shift(h, dy, dx)) / dist
        mask = drop > best_drop
        best_drop = np.where(mask, drop, best_drop)
        best_k = np.where(mask, k, best_k)
    yy, xx = np.meshgrid(np.arange(rows), np.arange(cols), indexing="ij")
    dy = np.array([n[0] for n in _NEIGHBOURS])[best_k]
    dx = np.array([n[1] for n in _NEIGHBOURS])[best_k]
    ry = np.clip(yy + dy, 0, rows - 1)
    rx = np.clip(xx + dx, 0, cols - 1)
    recv = (ry * cols + rx).ravel()
    self_idx = (yy * cols + xx).ravel().astype(np.int64)
    flowing = best_drop.ravel() > 0
    return np.where(flowing, recv, self_idx), best_drop.ravel()


def _flow_accumulation(recv, surface):
    acc = np.ones(surface.size)
    order = np.argsort(-surface.ravel(), kind="stable")
    for i in order:
        r = recv[i]
        if r != i:
            acc[r] += acc[i]
    return acc


def smooth(h, sigma=1.0):
    """Gentle gaussian blur to knock back fine crinkle without losing valleys."""
    from scipy.ndimage import gaussian_filter

    return gaussian_filter(h, max(sigma, 1e-3), mode="nearest")


def edge_falloff(h, margin=0.15, power=2.0, floor=0.0):
    """Taper the field toward the borders so the edges sink (islands, plateaus).

    margin is the fraction of the shorter side over which the taper eases in from
    the edge; power shapes the ease; floor is the lowest multiplier at the very
    edge (0 sinks edges to the field minimum). Run before erosion so drainage
    flows out to the sunk rim.
    """
    rows, cols = h.shape
    yy = np.linspace(0.0, 1.0, rows)[:, None]
    xx = np.linspace(0.0, 1.0, cols)[None, :]
    dy = np.minimum(yy, 1.0 - yy)  # (rows, 1) distance to nearest horizontal edge
    dx = np.minimum(xx, 1.0 - xx)  # (1, cols) distance to nearest vertical edge
    dist = np.minimum(dy, dx) / max(margin, 1e-6)  # broadcasts to (rows, cols)
    mask = np.clip(dist, 0.0, 1.0) ** max(power, 1e-6)
    mask = floor + (1.0 - floor) * mask
    return h * mask


def stream_power(h, iterations=35, rain=1.0, erosion=0.6, m=0.9, n=1.1,
                 talus=0.008, thermal_factor=0.35):
    """Alternate stream-power incision with thermal slumping. CPU."""
    rows, cols = h.shape
    area = rows * cols
    for _ in range(iterations):
        thermal(h, talus, thermal_factor)
        recv, slope = _receivers(h)
        acc = _flow_accumulation(recv, h) / area
        incision = erosion * rain * (acc ** m) * (slope ** n)
        incision = np.minimum(incision, slope * 0.5)
        h = (h.ravel() - incision).reshape(rows, cols)
        h = np.clip(h, 0.0, None)
    return h


# Pass runner (CPU passes only; the GPU op stack lives in engine.run_stack).

def run_passes(h, passes, backend=None, seed=0):
    """Apply an ordered list of CPU erosion passes, return a normalised [0, 1] field."""
    h = h.astype(np.float64).copy()
    for spec in passes:
        spec = dict(spec)
        kind = spec.pop("kind")
        if kind == "thermal":
            thermal(h, **spec)
        elif kind == "smooth":
            h = smooth(h, **spec)
        elif kind == "falloff":
            h = edge_falloff(h, **spec)
        elif kind == "stream_power":
            h = stream_power(h, **spec)
        else:
            raise ValueError(f"unknown erosion pass: {kind!r}")
        h = np.clip(h, 0.0, None)
    h -= h.min()
    h /= max(float(h.max()), 1e-9)
    return h
