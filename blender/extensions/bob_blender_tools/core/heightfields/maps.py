"""Auxiliary terrain maps derived from a baked heightfield: flow and wetness.

The height PNG says where the ground is; these say where the WATER is. They let
shading and scatter key off the terrain's own hydrology -- damp riverbeds, sediment
in channels, reeds along drainage -- instead of only weather-driven wetness. Both
are computed from the same flow-accumulation the erosion uses (ops_erode), so the
channels in the maps line up with the channels the erosion carved.

- flow: log-scaled drainage accumulation, [0, 1]. Bright where flow collects
  (channels, river mouths), dark on hillslopes. Log-scaled because raw accumulation
  spans hillslope ~1 to channel ~thousands.
- wetness: where the ground stays damp -- channels plus low, flat ground that holds
  water. A blend of flow and (low AND flat).

Both run on the backend array module (GPU or CPU) and are deterministic. They are an
opt-in bake output (they cost a drainage solve); the height bake never needs them.
"""

import numpy as np

from . import ops_erode


def derive_maps(h, backend, fill_iters=800, acc_iters=800, mfd_p=1.4):
    """Return {'flow', 'wetness'} as normalised [0, 1] numpy arrays for height `h`."""
    xp = backend.xp
    hh = xp.asarray(h, dtype=xp.float64)
    filled = ops_erode._pd_fill(hh, xp, fill_iters, 1e-4)
    acc = ops_erode._mfd_accum(filled, xp, acc_iters, mfd_p, 1.0)

    flow = xp.log1p(acc)
    flow = flow / xp.maximum(flow.max(), 1e-9)

    slope = ops_erode._slope(hh, xp, 1.0)
    slope = slope / xp.maximum(slope.max(), 1e-9)
    low = 1.0 - hh                                   # low ground pools water
    wetness = xp.clip(0.6 * flow + 0.4 * low * (1.0 - slope), 0.0, 1.0)

    return {
        "flow": backend.asnumpy(flow).astype(np.float64),
        "wetness": backend.asnumpy(wetness).astype(np.float64),
    }
