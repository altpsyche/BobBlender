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


def _fbm01(xp, size, freq, seed, octaves=3, persistence=0.8):
    """High-persistence multi-octave value noise in [0, 1] over the normalised grid. Persistence
    near 1 keeps the coarse octaves dominant so the field varies over large patches (what warps
    the talus angle into natural regions, not per-pixel hash) -- Cordonnier 2016's recipe."""
    from . import ops_generate
    u = (xp.arange(int(size), dtype=xp.float64) + 0.5) / int(size)
    x, y = xp.meshgrid(u, u)
    n = xp.zeros_like(x)
    amp, f, norm = 1.0, float(freq), 0.0
    for o in range(int(octaves)):
        n = n + amp * ops_generate._value_noise(xp, x, y, f, int(seed) + 7 * o)
        norm += amp
        amp *= float(persistence)
        f *= 2.0
    return n / max(norm, 1e-9)


def thermal(h, xp, talus=0.006, factor=0.5, iterations=8, cell=1.0,
            talus_warp=0.0, talus_freq=5.0, talus_seed=0):
    """Slump material down 4-neighbour slopes steeper than the talus angle (keeps canyon
    walls at a believable repose angle, removes stair-stepping). Mass-conserving: outflow
    edge-clamps (nothing leaves off-grid), reinjection zero-pads (nothing arrives off-grid).

    `talus_warp` > 0 makes the repose angle a per-cell field modulated by high-persistence
    noise (roughly talus*(1 +/- talus_warp)) instead of one constant, so banks and valley walls
    hold different angles in different places and stop reading as a single uniform ruled slope --
    the fix for artificially regular geometric valleys (Cordonnier 2016)."""
    t = talus
    if talus_warp > 0.0:
        n = _fbm01(xp, h.shape[0], talus_freq, talus_seed)
        t = talus * (1.0 + float(talus_warp) * (2.0 * n - 1.0))
        t = xp.clip(t, talus * 0.15, None)  # never let a cell's repose angle collapse to ~flat
    tc = t * cell
    for _ in range(int(iterations)):
        for dy, dx in ((-1, 0), (1, 0), (0, -1), (0, 1)):
            diff = h - _sh(xp, h, dy, dx)
            move = xp.clip((diff - tc) * factor, 0.0, None)
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


def _flow_prior_field(shape, xp, spec):
    """A drainage-area boost field from a river spline: `gain` along the polyline, easing to 0
    over its band. Added to the computed accumulation so stream-power incision cuts the valley
    where the river is authored -- the spline is a DRAINAGE PRIOR, not a carved cross-section.
    The valley and its banks then EMERGE from erosion (Cordonnier 2016), rather than being
    stamped on as a smooth swept profile."""
    from . import ops_carve
    curves = spec.get("curves") or ()
    if not curves:
        return None
    dist = ops_carve._distance_uv(shape, curves, xp, _ndimage(xp))
    prof = ops_carve._profile(dist, spec.get("width", 0.01), spec.get("falloff", 0.02), xp)
    return float(spec.get("gain", 3000.0)) * prof


def fluvial(h, xp, *, iterations=40, k=5e-4, sp_m=0.5, sp_n=1.0, diffusion=0.12,
            talus=0.006, thermal_factor=0.5, thermal_iters=1, cell=1.0, max_delta=0.03,
            recompute=40, fill_iters=1200, acc_iters=1200, fill_eps=1e-4, mfd_p=1.4,
            flow_prior=None, talus_warp=0.0, talus_freq=5.0, talus_seed=0):
    """Flow-accumulation stream-power fluvial erosion -- the canyon/dendritic-network carver.

    Each step incises `k * A^sp_m * slope^sp_n` (drainage area A from _mfd_accum on the
    depression-filled surface), then relaxes with hillslope diffusion + thermal so walls hold a
    repose angle. The drainage network is recomputed every `recompute` steps (it is stable, so
    this bounds cost). Fully vectorised on `xp` (GPU or CPU); deterministic.

    `flow_prior` ({curves, width, falloff, gain}) boosts the drainage area along a river spline so
    the solver incises the valley there and the banks emerge from erosion instead of a swept
    profile. `talus_warp` spatially varies the thermal repose angle (see thermal)."""
    h = xp.asarray(h, dtype=xp.float64)
    prior = _flow_prior_field(h.shape, xp, flow_prior) if flow_prior else None
    acc = None
    for it in range(int(iterations)):
        if it % int(recompute) == 0:
            filled = _pd_fill(h, xp, fill_iters, fill_eps)
            acc = _mfd_accum(filled, xp, acc_iters, mfd_p, cell)
            if prior is not None:
                acc = acc + prior
        s = _slope(h, xp, cell)
        h = h - xp.minimum(k * (acc ** sp_m) * (s ** sp_n), max_delta)
        if diffusion > 0.0:
            lap = (_sh(xp, h, -1, 0) + _sh(xp, h, 1, 0)
                   + _sh(xp, h, 0, -1) + _sh(xp, h, 0, 1) - 4.0 * h)
            h = h + diffusion * lap
        if thermal_iters > 0:
            h = thermal(h, xp, talus, thermal_factor, thermal_iters, cell,
                        talus_warp=talus_warp, talus_freq=talus_freq, talus_seed=talus_seed)
        h = xp.clip(h, 0.0, None)
    return h


def scarp(h, xp, *, iterations=12, cap_slope=0.10, undercut=0.0015, talus=0.14,
          talus_iters=1, cell=1.0, open_size=3):
    """Cap-rock scarp-retreat erosion -- the mesa/plateau carver, paired with ops_generate.strata.

    Flat caps and benches (slope < `cap_slope`) are RESISTANT and barely erode; steep faces are
    undercut and recede laterally, with a talus apron settling to a steep repose (`talus`) below
    them. Over `iterations` this dissects a layered plateau into flat-topped mesas with near-vertical
    sides -- the cliff-retreat process that dendritic fluvial (which rounds everything toward a graded
    profile) cannot produce. `talus` is high on purpose: cap rock holds a near-vertical face, so the
    thermal pass must NOT relax cliffs down to a hillslope. `open_size` > 0 applies a grey-opening at
    the end to shave narrow spires/cones (a butte eroded to a CG point) while preserving wide flat
    caps. Vectorised on `xp`, deterministic."""
    h = xp.asarray(h, dtype=xp.float64)
    for _ in range(int(iterations)):
        s = _slope(h, xp, cell)
        # cap-rock protection: erosion weight ~0 on flat caps, rising on steep faces
        w = xp.clip((s - cap_slope) / max(float(cap_slope), 1e-6), 0.0, 1.0)
        h = h - float(undercut) * w                 # undercut the face -> cliff retreats
        if talus_iters > 0:                          # talus apron settles to a steep repose
            h = thermal(h, xp, talus, 0.5, int(talus_iters), cell)
        h = xp.clip(h, 0.0, None)
    if int(open_size) > 0:
        h = _ndimage(xp).grey_opening(h, size=int(open_size), mode="nearest")
    return h


def deposit(h, xp, *, amount=0.012, iterations=4, cell=1.0,
            fill_iters=800, acc_iters=800, mfd_p=1.4, recompute=4,
            flow_m=0.6, slope_n=1.5, flow_floor=0.15,
            settle_talus=0.004, settle_iters=2, settle_factor=0.5,
            talus_warp=0.0, talus_freq=5.0, talus_seed=0):
    """Fluvial deposition pass (Erosion2-style sediment settling): raise the bed where flowing water
    loses the capacity to carry sediment -- high drainage area but low slope. That alluviates the
    incised valley floor into a flatter floodplain and grows gentle point bars on the slack inner-bend
    margins -- the deposition that pure stream-power incision (fluvial) lacks, which is why an eroded
    channel otherwise reads as a bare cut V. Monotone per step (only adds material), then slumps the
    fresh sediment to a gentle repose so bars read rounded, not blocky.

    `flow_floor` gates deposition to cells whose drainage (normalised to the field max) is above the
    floor, so only the wet channels alluviate and low-slope UPLANDS are left alone. Mask it to the
    channel band (path selector) to restrict alluviation to the river corridor. Deterministic,
    vectorised on `xp`."""
    h = xp.asarray(h, dtype=xp.float64)
    acc = None
    for it in range(int(iterations)):
        if it % int(recompute) == 0:
            filled = _pd_fill(h, xp, fill_iters, 1e-4)
            acc = _mfd_accum(filled, xp, acc_iters, mfd_p, cell)
            acc = acc / xp.maximum(acc.max(), 1e-9)
        s = _slope(h, xp, cell)
        s = s / xp.maximum(s.max(), 1e-9)
        wet = xp.clip((acc - flow_floor) / max(1.0 - float(flow_floor), 1e-6), 0.0, 1.0)
        dep = amount * (wet ** flow_m) * (xp.clip(1.0 - s, 0.0, 1.0) ** slope_n)
        h = h + dep
        if settle_iters > 0:
            h = thermal(h, xp, settle_talus, settle_factor, settle_iters, cell,
                        talus_warp=talus_warp, talus_freq=talus_freq, talus_seed=talus_seed)
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
