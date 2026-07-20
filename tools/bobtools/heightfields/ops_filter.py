"""Non-erosion filter ops: reshape existing height. Stack ops `(h, xp, params, seed) -> h`.

These are the shaping filters (as distinct from the physical erosion in ops_erode): terracing for
mesas/plateaus, domain warp for organic distortion, a remap curve for profile control, and an
unsharp sharpen. Vectorised on the backend array module.
"""

from . import ops_erode


def terrace(h, xp, steps=6.0, sharpness=0.7, tilt=0.0):
    """Quantise height into `steps` benches with smooth risers -- mesas, plateaus, sedimentary
    strata. sharpness in [0,1): 0 keeps the original ramp, ->1 flattens each tread hard. `tilt`
    adds a fraction of the original height back so terraces follow the underlying slope."""
    s = max(float(steps), 1.0)
    scaled = h * s
    base = xp.floor(scaled)
    frac = scaled - base
    # smoothstep the riser; sharpness pushes the tread flat
    k = min(max(float(sharpness), 0.0), 0.999)
    eased = frac * frac * (3 - 2 * frac)
    shaped = base + (1.0 - k) * frac + k * eased
    terr = shaped / s
    return (1.0 - tilt) * terr + tilt * h


def warp(h, xp, amount=0.04, frequency=3.0, seed=0):
    """Domain-warp the height field by low-frequency noise -- breaks up regularity, adds organic
    meander. amount is a domain fraction; frequency sets the warp scale."""
    from . import ops_generate
    ndi = ops_erode._ndimage(xp)
    n = h.shape[0]
    u = (xp.arange(n, dtype=xp.float64) + 0.5) / n
    x, y = xp.meshgrid(u, u)
    dx = (ops_generate._value_noise(xp, x, y, frequency, seed + 3) - 0.5) * amount * n
    dy = (ops_generate._value_noise(xp, x, y, frequency, seed + 7) - 0.5) * amount * n
    ys, xs = xp.meshgrid(xp.arange(n, dtype=xp.float64), xp.arange(n, dtype=xp.float64), indexing="ij")
    return ndi.map_coordinates(h, xp.stack([ys + dy, xs + dx]), order=1, mode="reflect")


def curve(h, xp, gamma=1.0, contrast=0.0):
    """Remap the height profile: gamma bends the midtones (gamma<1 lifts valleys, >1 sharpens
    peaks); contrast is an s-curve around 0.5."""
    v = xp.clip(h, 0.0, 1.0) ** max(float(gamma), 1e-3)
    if contrast:
        c = float(contrast)
        v = xp.clip(0.5 + (v - 0.5) * (1.0 + c) - c * (v - 0.5) ** 3 * 4.0, 0.0, 1.0)
    return v


def sharpen(h, xp, amount=0.5, radius=1.5):
    """Unsharp mask: add back a fraction of (h - blur(h)) to crisp ridgelines."""
    ndi = ops_erode._ndimage(xp)
    blur = ndi.gaussian_filter(h, max(float(radius), 1e-3), mode="nearest")
    return xp.clip(h + amount * (h - blur), 0.0, None)
