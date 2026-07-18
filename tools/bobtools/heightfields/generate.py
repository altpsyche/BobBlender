"""Base heightfield generation: composed fractal terrain in [0, 1].

CPU and deterministic on purpose. Generation is cheap (a few blurs), and keeping
the random source on the host with a seeded numpy Generator makes the base field
bit-reproducible regardless of which backend erodes it afterward. The expensive,
GPU-worthy work is erosion, not this.
"""

import numpy as np


def _smooth_noise(size, sigma, rng):
    """Isotropic smooth noise in [0, 1] by blurring white noise. No axis artifacts."""
    from scipy.ndimage import gaussian_filter

    n = gaussian_filter(rng.standard_normal((size, size)), max(sigma, 0.8), mode="wrap")
    n -= n.min()
    return n / max(n.max(), 1e-9)


def _fractal(size, octaves, roughness, base_sigma, rng):
    height = np.zeros((size, size))
    amp, total, sigma = 1.0, 0.0, base_sigma
    for _ in range(octaves):
        height += amp * _smooth_noise(size, sigma, rng)
        total += amp
        amp *= roughness
        sigma /= 2.0
    return height / total


def _domain_warp(field, strength, rng):
    """Warp a field by low-frequency noise for organic, flowing shapes."""
    from scipy.ndimage import map_coordinates

    size = field.shape[0]
    wy = _smooth_noise(size, size / 6.0, rng) * 2.0 - 1.0
    wx = _smooth_noise(size, size / 6.0, rng) * 2.0 - 1.0
    yy, xx = np.mgrid[0:size, 0:size]
    coords = [yy + wy * strength, xx + wx * strength]
    return map_coordinates(field, coords, order=1, mode="reflect")


def generate_base(size, seed=0, octaves=8, roughness=0.55, ridged=0.6,
                  warp=None, detail_strength=0.7):
    """A composed base heightfield in [0, 1]: large shape plus warped ridged detail."""
    rng = np.random.default_rng(seed)
    shape = _smooth_noise(size, size / 5.0, rng)
    detail = _fractal(size, octaves, roughness, size / 16.0, rng)
    if warp is None:
        warp = size / 22.0
    if warp > 0:
        detail = _domain_warp(detail, warp, rng)
    ridges = 1.0 - np.abs(2.0 * detail - 1.0)
    detail = (1.0 - ridged) * detail + ridged * ridges
    combined = shape * (1.0 + (detail - 0.5) * detail_strength)
    combined -= combined.min()
    combined /= max(combined.max(), 1e-9)
    return combined
