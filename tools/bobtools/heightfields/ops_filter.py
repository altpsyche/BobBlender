"""Non-erosion filter ops: reshape existing height. Stack ops `(h, xp, params, seed) -> h`.

These are the shaping filters (as distinct from the physical erosion in ops_erode): terracing for
mesas/plateaus, domain warp for organic distortion, a remap curve for profile control, and an
unsharp sharpen. Vectorised on the backend array module.
"""

import math

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


def falloff(h, xp, margin=0.2, power=2.0, floor=0.0, shape="edge", angle=0.0):
    """Taper the field toward a boundary so land meets sea. Run before erosion so
    drainage flows out to the sunk rim. `power` shapes the ease; `floor` is the
    lowest multiplier at the very edge (0 sinks it to the field minimum). Three
    shapes:

    - "edge": sink all four borders (a bump framed by sea). `margin` is the border
      fraction the taper eases in over.
    - "radial": a round island -- full height inside a central disc, easing to sea
      near the rim over `margin`; corners are always sea. Noise/erosion break the
      circle into an organic coastline.
    - "gradient": a shoreline running across the scene -- sea on the `angle` side,
      land rising toward the opposite side over `margin`.

    Resolution-independent (distances are domain fractions), identical on CPU/GPU."""
    n0, n1 = h.shape
    yy = xp.linspace(0.0, 1.0, n0)[:, None]
    xx = xp.linspace(0.0, 1.0, n1)[None, :]
    m = max(float(margin), 1e-6)
    if shape == "radial":
        r = xp.sqrt((yy - 0.5) ** 2 + (xx - 0.5) ** 2) / 0.5   # 0 centre .. ~1.41 corners
        dist = (1.0 - r) / m                                   # land inside, sea past the rim
    elif shape == "gradient":
        a = math.radians(float(angle))
        proj = (xx - 0.5) * math.cos(a) + (yy - 0.5) * math.sin(a) + 0.5  # 0..1 across the scene
        dist = proj / m                                        # shore on the low side
    else:  # edge
        dy = xp.minimum(yy, 1.0 - yy)          # distance to nearest horizontal edge
        dx = xp.minimum(xx, 1.0 - xx)          # distance to nearest vertical edge
        dist = xp.minimum(dy, dx) / m
    mask = xp.clip(dist, 0.0, 1.0) ** max(float(power), 1e-6)
    mask = float(floor) + (1.0 - float(floor)) * mask
    return h * mask
