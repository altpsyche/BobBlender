"""Grid erosion ops: pipe-model hydraulic + stream-power incision + thermal.

All vectorised on the backend array module (`backend.xp` -> numpy or CuPy), so the SAME
code runs on CPU and GPU with no per-droplet Python loop. This replaces the old droplet
simulation, which was slow on CPU and could not carve canyons.

The pipe model (Mei et al. 2007, "Fast Hydraulic Erosion Simulation on GPU") is a shallow-
water sim: rain adds water; water flows between cells through virtual pipes driven by the
water-surface gradient; the moving water picks up sediment up to a capacity and drops it
where it slows. On top of that, a STREAM-POWER incision term erodes proportional to the
water flux through a cell times its slope (`k * flux^m * slope^n`) -- flux is the drainage
proxy, so channels that collect flow incise deeper: that is what forms canyons and dendritic
networks, which droplet erosion never did.

Deterministic (pure stencils, no random scatter) and expressed in domain-relative units, so
a seeded bake is reproducible and consistent across resolutions.
"""

import numpy as np

SQRT2 = 2.0 ** 0.5


def _ndimage(xp):
    if xp.__name__ == "cupy":
        import cupyx.scipy.ndimage as ndi
    else:
        import scipy.ndimage as ndi
    return ndi


def _sh(xp, a, dy, dx):
    """Neighbour value at (dy, dx) aligned to each cell, edge-replicated so a border cell
    sees itself off-grid (zero gradient there -> no flux/slump across the domain edge)."""
    p = xp.pad(a, ((1, 1), (1, 1)), mode="edge")
    H, W = a.shape
    return p[1 + dy:1 + dy + H, 1 + dx:1 + dx + W]


def thermal(h, xp, talus=0.006, factor=0.5, iterations=8, cell=1.0):
    """Slump material down 4-neighbour slopes steeper than the talus angle (keeps canyon
    walls at a believable repose angle, removes stair-stepping). Mass-conserving: outflow
    edge-clamps (nothing leaves off-grid), reinjection zero-pads (nothing arrives off-grid)."""
    for _ in range(int(iterations)):
        for dy, dx in ((-1, 0), (1, 0), (0, -1), (0, 1)):
            diff = h - _sh(xp, h, dy, dx)
            move = xp.clip((diff - talus * cell) * factor, 0.0, None)
            h = h - move
            # reinjection: material from the (-dy,-dx) source cell; zero-padded at the border
            p = xp.pad(move, ((1, 1), (1, 1)), mode="constant")
            H, W = h.shape
            h = h + p[1 - dy:1 - dy + H, 1 - dx:1 - dx + W]
    return h


_D8 = (((-1, 0), 1.0), ((1, 0), 1.0), ((0, -1), 1.0), ((0, 1), 1.0),
       ((-1, -1), SQRT2), ((-1, 1), SQRT2), ((1, -1), SQRT2), ((1, 1), SQRT2))


def _pd_fill(h, xp, iters, eps):
    """Planchon-Darboux depression fill (iterative, vectorised): the depressionless surface
    water drains over. Border cells stay at their DEM height (outlets); interior cells relax
    down to min(8-neighbour filled) + eps, never below the DEM. Enough iterations lets the fill
    propagate inward from the outlets so no interior pit traps flow."""
    inf = float(h.max()) + 1000.0
    filled = xp.full_like(h, inf)
    filled[0, :] = h[0, :]; filled[-1, :] = h[-1, :]
    filled[:, 0] = h[:, 0]; filled[:, -1] = h[:, -1]
    for _ in range(int(iters)):
        mn = filled
        for (dy, dx), _d in _D8:
            mn = xp.minimum(mn, _sh(xp, filled, dy, dx))
        filled = xp.minimum(filled, xp.maximum(h, mn + eps))
        filled[0, :] = h[0, :]; filled[-1, :] = h[-1, :]
        filled[:, 0] = h[:, 0]; filled[:, -1] = h[:, -1]
    return filled


def _mfd_accum(filled, xp, iters, mfd_p, cell):
    """Multiple-flow-direction drainage-area accumulation on a depressionless surface. Returns
    RAW accumulation (each cell ~= number of upstream cells): hillslopes read ~1, channels read
    thousands. That large dynamic range is what makes stream-power incision carve deep channels
    and leave hillslopes alone -- normalising it to [0,1] would flatten the contrast and no
    canyons form."""
    weights, offs = [], []
    wsum = xp.zeros_like(filled)
    for (dy, dx), d in _D8:
        drop = xp.clip((filled - _sh(xp, filled, dy, dx)) / (d * cell), 0.0, None) ** mfd_p
        weights.append(drop); offs.append((dy, dx)); wsum = wsum + drop
    wsum = wsum + 1e-12
    weights = [w / wsum for w in weights]
    acc = xp.ones_like(filled)
    for _ in range(int(iters)):
        inflow = xp.zeros_like(filled)
        for (dy, dx), w in zip(offs, weights):
            inflow = inflow + _sh(xp, acc * w, -dy, -dx)  # from the (-d) neighbour toward here
        acc = 1.0 + inflow
    return acc


def _slope(h, xp, cell):
    gx = (_sh(xp, h, 0, 1) - _sh(xp, h, 0, -1)) / (2.0 * cell)
    gy = (_sh(xp, h, 1, 0) - _sh(xp, h, -1, 0)) / (2.0 * cell)
    return xp.sqrt(gx * gx + gy * gy)


def fluvial(h, xp, *, iterations=40, k=5e-4, sp_m=0.5, sp_n=1.0, diffusion=0.12,
            talus=0.006, thermal_factor=0.5, thermal_iters=1, cell=1.0, max_delta=0.03,
            recompute=40, fill_iters=1200, acc_iters=1200, fill_eps=1e-4, mfd_p=1.4):
    """Flow-accumulation stream-power fluvial erosion -- the canyon/dendritic-network carver.

    Each step incises `k * A^sp_m * slope^sp_n` (drainage area A from _mfd_accum on the
    depression-filled surface), then relaxes with hillslope diffusion + thermal so walls hold a
    repose angle. The drainage network is recomputed every `recompute` steps (it is stable, so
    this bounds cost). Fully vectorised on `xp` (GPU or CPU); deterministic."""
    h = xp.asarray(h, dtype=xp.float64)
    acc = None
    for it in range(int(iterations)):
        if it % int(recompute) == 0:
            filled = _pd_fill(h, xp, fill_iters, fill_eps)
            acc = _mfd_accum(filled, xp, acc_iters, mfd_p, cell)
        s = _slope(h, xp, cell)
        h = h - xp.minimum(k * (acc ** sp_m) * (s ** sp_n), max_delta)
        if diffusion > 0.0:
            lap = (_sh(xp, h, -1, 0) + _sh(xp, h, 1, 0)
                   + _sh(xp, h, 0, -1) + _sh(xp, h, 0, 1) - 4.0 * h)
            h = h + diffusion * lap
        if thermal_iters > 0:
            h = thermal(h, xp, talus, thermal_factor, thermal_iters, cell)
        h = xp.clip(h, 0.0, None)
    return h


def pipe_hydraulic(h, xp, *, iterations=120, rain=0.012, dt=0.12, gravity=9.81,
                   pipe_area=1.0, pipe_len=1.0, cell=1.0,
                   capacity=0.35, dissolve=0.35, deposit=0.35, evaporate=0.05,
                   min_slope=0.02, incision=0.0, sp_m=1.0, sp_n=1.2,
                   max_water=4.0, max_vel=4.0, max_delta=0.02):
    """Pipe-model hydraulic erosion with an optional stream-power incision term.

    `incision` > 0 adds `incision * flux^sp_m * slope^sp_n` channel cutting on top of the
    capacity-based transport, which is what deepens collecting channels into canyons.
    Returns the eroded height (float64). All fields are grid arrays; no Python per-cell loop.
    """
    h = xp.asarray(h, dtype=xp.float64)
    H, W = h.shape
    z = xp.zeros_like
    w = z(h)                     # water depth
    sed = z(h)                   # suspended sediment
    fL, fR, fT, fB = z(h), z(h), z(h), z(h)   # outflow flux per pipe
    ndi = _ndimage(xp)
    ga = gravity * pipe_area / pipe_len
    ys, xs = xp.meshgrid(xp.arange(H, dtype=xp.float64),
                         xp.arange(W, dtype=xp.float64), indexing="ij")

    for _ in range(int(iterations)):
        w = w + dt * rain
        surf = h + w
        # outflow flux, driven by the water-surface drop toward each neighbour
        fL = xp.clip(fL + dt * ga * (surf - _sh(xp, surf, 0, -1)), 0.0, None)
        fR = xp.clip(fR + dt * ga * (surf - _sh(xp, surf, 0, 1)), 0.0, None)
        fT = xp.clip(fT + dt * ga * (surf - _sh(xp, surf, -1, 0)), 0.0, None)
        fB = xp.clip(fB + dt * ga * (surf - _sh(xp, surf, 1, 0)), 0.0, None)
        # do not drain more water than the cell holds
        out = fL + fR + fT + fB
        avail = w * cell * cell
        scale = xp.minimum(1.0, avail / (out * dt + 1e-8))
        fL *= scale; fR *= scale; fT *= scale; fB *= scale
        # water update from net flux (neighbour inflow - own outflow)
        inflow = (_sh(xp, fR, 0, -1) + _sh(xp, fL, 0, 1)
                  + _sh(xp, fB, -1, 0) + _sh(xp, fT, 1, 0))
        out = fL + fR + fT + fB
        w_new = xp.clip(w + dt * (inflow - out) / (cell * cell), 0.0, max_water)
        # flow velocity from the net water passing through each axis
        w_avg = xp.maximum((w + w_new) * 0.5, 1e-4)
        dWx = 0.5 * (_sh(xp, fR, 0, -1) - fL + fR - _sh(xp, fL, 0, 1))
        dWy = 0.5 * (_sh(xp, fB, -1, 0) - fT + fB - _sh(xp, fT, 1, 0))
        vel = xp.clip(xp.sqrt(dWx * dWx + dWy * dWy) / (cell * w_avg), 0.0, max_vel)
        # Drainage proxy for stream power: the standing water column (w_avg) is high in channels
        # where flow collects, so incision keyed to it deepens collecting channels into canyons --
        # the erosion feedback (deeper channel -> collects more -> deeper) the instantaneous flux
        # alone was too weak to start.
        flux = w_avg
        # local slope
        gx = (_sh(xp, h, 0, 1) - _sh(xp, h, 0, -1)) / (2.0 * cell)
        gy = (_sh(xp, h, 1, 0) - _sh(xp, h, -1, 0)) / (2.0 * cell)
        slope = xp.sqrt(gx * gx + gy * gy)
        sin_slope = xp.maximum(slope / xp.sqrt(1.0 + slope * slope), min_slope)
        # sediment capacity of the moving water
        cap = capacity * sin_slope * vel
        # erode where under-capacity, deposit where over; clamp the per-step change so the
        # erosion feedback cannot run away to NaN
        under = cap > sed
        eroded = xp.where(under, xp.minimum(dissolve * (cap - sed), max_delta), 0.0)
        dropped = xp.where(under, 0.0, xp.minimum(deposit * (sed - cap), max_delta))
        h = h - eroded + dropped
        sed = sed + eroded - dropped
        # stream-power channel incision (canyon carving), keyed to the collected water column
        if incision > 0.0:
            inc = xp.minimum(incision * dt * (flux ** sp_m) * (slope ** sp_n), max_delta)
            h = h - inc
        # transport suspended sediment along the velocity (semi-Lagrangian back-trace)
        src_y = ys - (dWy / (cell * w_avg)) * dt
        src_x = xs - (dWx / (cell * w_avg)) * dt
        sed = ndi.map_coordinates(sed, xp.stack([src_y, src_x]), order=1, mode="nearest")
        # evaporate (per-step fraction, so water reaches a rain/evaporation steady state
        # instead of accumulating without bound and blowing up the incision)
        w = xp.clip(w_new * (1.0 - evaporate), 0.0, max_water)

    # settle any sediment still suspended, and clamp
    h = xp.clip(h + sed, 0.0, None)
    return h
