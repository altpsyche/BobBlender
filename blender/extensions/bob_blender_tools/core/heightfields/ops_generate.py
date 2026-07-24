"""Generator ops: create or add height. Stack ops `(h, xp, params, seed) -> h`.

Generators run on the backend array module so they compose with the GPU erosion in one stack.
They write into the incoming field via a `mix` mode (add / multiply / replace / max), so a
recipe can stack a ridged base, blend in dunes, and cut mesas from voronoi. The base ridged-
multifractal generation lives in generate.py (resolution-independent, world-sampled); these add
the family-specific structure (directional dunes, cellular mesas) that gives the four landscape
families their character.
"""

import numpy as np


def _coords(xp, size):
    """Normalised world grid over [0, 1) -- resolution-independent sample points, like generate.py."""
    u = (xp.arange(size, dtype=xp.float64) + 0.5) / size
    return xp.meshgrid(u, u)


def _mix(h, field, mode, amount):
    if mode == "replace":
        return field
    if mode == "multiply":
        return h * (1.0 - amount + amount * field)
    if mode == "max":
        m = h.__class__ if False else None  # placeholder; use xp via field
        return field  # handled by caller with xp.maximum
    return h + amount * field  # add (default)


def _value_noise(xp, x, y, freq, seed):
    """Cheap smooth value noise in [0,1] at world coords, for warping/variation (not the hero
    detail -- generate.py's gradient noise is that). Hash a jittered lattice, bilinear-blend."""
    xf = x * freq
    yf = y * freq
    x0 = xp.floor(xf).astype(xp.int64)
    y0 = xp.floor(yf).astype(xp.int64)
    tx = xf - x0
    ty = yf - y0
    def rand(ix, iy):
        n = (ix * 374761393 + iy * 668265263 + seed * 362437) & 0x7fffffff
        n = (n ^ (n >> 13)) * 1274126177 & 0x7fffffff
        return (n % 100000) / 100000.0
    v00 = rand(x0, y0); v10 = rand(x0 + 1, y0); v01 = rand(x0, y0 + 1); v11 = rand(x0 + 1, y0 + 1)
    sx = tx * tx * (3 - 2 * tx)
    sy = ty * ty * (3 - 2 * ty)
    return (v00 * (1 - sx) + v10 * sx) * (1 - sy) + (v01 * (1 - sx) + v11 * sx) * sy


def dunes(h, xp, seed=0, wind=35.0, frequency=7.0, sharpness=0.5, warp=0.12,
          variation=0.4, mix="add", amount=0.5):
    """Directional wind-formed dunes: parallel asymmetric ridges along the wind direction,
    warped and amplitude-modulated by low-frequency noise so they meander like a real sand sea.

    Profile is a real transverse-dune section: a long gentle windward (stoss) slope rising to a
    ROUNDED crest, then a short steep lee (slip face). `sharpness` in [0, 1] steepens the whole
    dune (shrinks the stoss fraction) without ever spiking the crest to a fin -- the crest is
    smoothstep-rounded, so higher sharpness reads as steeper, taller dunes, not knife-edges."""
    x, y = _coords(xp, h.shape[0])
    a = np.radians(wind)
    # warp the projection so dune crests meander
    wx = _value_noise(xp, x, y, 3.0, seed + 11) - 0.5
    wy = _value_noise(xp, x, y, 3.0, seed + 23) - 0.5
    proj = (x + warp * wx) * np.cos(a) + (y + warp * wy) * np.sin(a)
    phase = (proj * frequency) % 1.0
    # Real transverse-dune section across one wavelength, downwind:
    #   [0, stoss)          long gentle windward slope, smoothstep-rounded toe rising to the brink
    #   [stoss, lee_end)    short STEEP lee (slip face) dropping brink -> 0
    #   [lee_end, 1)        flat interdune corridor (bare sand between dunes)
    # `sharpness` in [0, 1] lengthens the windward run and shortens the lee, so a higher value
    # reads as a steeper, more sculpted dune -- never a symmetric sine wave, never a spike.
    s = min(1.0, max(0.0, float(sharpness)))
    stoss = 0.50 + 0.15 * s                     # windward fraction 0.50 .. 0.65
    lee_w = (1.0 - stoss) * (0.55 - 0.25 * s)   # lee width shrinks with s -> steeper slip face
    lee_end = stoss + lee_w
    w = xp.clip(phase / stoss, 0.0, 1.0)
    windward = w * w * (3.0 - 2.0 * w)          # gentle S-curve up to the brink
    ll = xp.clip((phase - stoss) / lee_w, 0.0, 1.0)
    lee = 1.0 - ll * ll * (3.0 - 2.0 * ll)      # steep drop from the brink to the interdune floor
    ridge = xp.where(phase < stoss, windward, xp.where(phase < lee_end, lee, 0.0))
    envelope = 1.0 - variation + variation * _value_noise(xp, x, y, 2.0, seed + 5)
    field = ridge * envelope
    if mix == "max":
        return xp.maximum(h, field)
    return _mix(h, field, mix, amount)


def strata(h, xp, seed=0, layers=5, dissection=1.4, base_freq=3.0, sharpness=0.97,
           smooth=2.0, mix="replace", amount=1.0):
    """Flat-lying layered rock strata: the mesa/plateau base (real strata, not a terrace filter over
    ridged noise). A broad, near-flat uplifted surface quantised into `layers` genuinely flat benches
    with near-vertical risers; the risers become cliffs once scarp (ops_erode.scarp) erodes the
    field. `dissection` > 1 lowers the midtones so the top strata survive only in patches -> isolated
    mesas/buttes; near 1 it keeps a continuous tableland (plateau). `sharpness` in [0, 1) sets how
    thin/steep the riser is (0.97 ~ near-vertical). `smooth` (gaussian sigma, cells) merges small high
    spots BEFORE quantising so they do not survive as isolated full-height spires/pyramids -- raise it
    for a cleaner plateau (canyon rims), lower it for more scattered buttes. Distinct from
    ops_filter.terrace, which shapes an EXISTING field; strata GENERATES the flat-layered plateau."""
    from . import ops_erode
    n = h.shape[0]
    u = (xp.arange(n, dtype=xp.float64) + 0.5) / n
    x, y = xp.meshgrid(u, u)
    surf = (0.6 * _value_noise(xp, x, y, float(base_freq), seed + 1)
            + 0.4 * _value_noise(xp, x, y, float(base_freq) * 2.1, seed + 7))
    # de-spike so no small high patch survives quantising into a stray spire/pyramid
    surf = ops_erode._ndimage(xp).gaussian_filter(surf, max(float(smooth), 1e-3), mode="nearest")
    surf = (surf - surf.min()) / (surf.max() - surf.min() + 1e-9)
    surf = surf ** float(dissection)
    L = max(float(layers), 1.0)
    scaled = surf * L
    base = xp.floor(scaled)
    frac = scaled - base
    k = min(max(float(sharpness), 0.0), 0.999)
    w = max(1.0 - k, 1e-3)
    riser = xp.clip((frac - (1.0 - w)) / w, 0.0, 1.0)   # LINEAR riser: a straight cliff face
    field = (base + riser) / L
    if mix == "add":
        return h + amount * field
    if mix == "max":
        return xp.maximum(h, field)
    return field


def voronoi(h, xp, seed=0, cells=8.0, pattern="mesa", jitter=0.85, mix="multiply", amount=0.7):
    """Jittered-grid Voronoi (Worley) cellular structure. pattern='mesa' gives flat-topped cells
    (plateaus/tablelands); pattern='crack' gives the ridged cell borders (cracked hardpan, joints)."""
    n = h.shape[0]
    x, y = _coords(xp, n)
    gx = x * cells
    gy = y * cells
    ix = xp.floor(gx)
    iy = xp.floor(gy)
    f1 = xp.full_like(x, 1e9)
    f2 = xp.full_like(x, 1e9)
    for oy in (-1, 0, 1):
        for ox in (-1, 0, 1):
            cxi = ix + ox
            cyi = iy + oy
            jx = _value_noise(xp, (cxi + 0.5) / cells, (cyi + 0.5) / cells, cells, seed + 1)
            jy = _value_noise(xp, (cxi + 0.5) / cells, (cyi + 0.5) / cells, cells, seed + 99)
            fx = cxi + 0.5 + jitter * (jx - 0.5)
            fy = cyi + 0.5 + jitter * (jy - 0.5)
            d = xp.sqrt((gx - fx) ** 2 + (gy - fy) ** 2)
            nf1 = xp.minimum(f1, d)
            f2 = xp.minimum(xp.maximum(f1, d), f2)
            f1 = nf1
    if pattern == "crack":
        field = xp.clip((f2 - f1) * cells * 0.5, 0.0, 1.0)   # thin borders bright
    else:  # mesa: flat cells, sharp rims (1 - normalized F1, plateaued)
        field = xp.clip(1.0 - f1, 0.0, 1.0) ** 0.5
    if mix == "max":
        return xp.maximum(h, field)
    return _mix(h, field, mix, amount)
