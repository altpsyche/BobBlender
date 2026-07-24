"""Base heightfield generation: resolution-independent coherent-noise terrain in [0, 1].

Sampled at NORMALISED world coordinates, so the same seed gives the SAME landform at any
resolution -- a 256 preview and a 1024 final are two sampling densities of one continuous
field, not two unrelated random grids. (The old gaussian-blur-of-white-noise fBm was
resolution-DEPENDENT: preview and final correlated negatively.) Gradient (Perlin) noise with a
ridged-multifractal transform and a domain warp gives crisp ridgelines instead of rounded blobs.

Pure numpy, deterministic, no third-party dependency (the venv is Python 3.14, where the
compiled noise libraries have no wheels yet). Generation is cheap (~100 ms at 1024, a handful of
array ops per octave); the GPU-worthy work is erosion, not this.
"""

import numpy as np

# 8 gradient directions (axis + diagonal), the classic Perlin gradient set for 2D.
_GRAD2 = np.array(
    [[1, 1], [-1, 1], [1, -1], [-1, -1], [1, 0], [-1, 0], [0, 1], [0, -1]], dtype=np.float64
)


def _perm(seed):
    """A 512-long permutation table (256 tiled twice, so perm[a + b] never overflows)."""
    p = np.random.default_rng(seed).permutation(256).astype(np.int64)
    return np.concatenate([p, p])


def _fade(t):
    return t * t * t * (t * (t * 6 - 15) + 10)


def _lerp(a, b, t):
    return a + t * (b - a)


def _perlin(x, y, perm):
    """Vectorised 2D Perlin gradient noise in ~[-1, 1] at continuous float coords."""
    x0 = np.floor(x).astype(np.int64)
    y0 = np.floor(y).astype(np.int64)
    xf = x - x0
    yf = y - y0
    xi = x0 & 255
    yi = y0 & 255
    u = _fade(xf)
    v = _fade(yf)

    def grad(h, dx, dy):
        g = _GRAD2[h & 7]
        return g[..., 0] * dx + g[..., 1] * dy

    aa = perm[perm[xi] + yi]
    ab = perm[perm[xi] + yi + 1]
    ba = perm[perm[xi + 1] + yi]
    bb = perm[perm[xi + 1] + yi + 1]
    x1 = _lerp(grad(aa, xf, yf), grad(ba, xf - 1, yf), u)
    x2 = _lerp(grad(ab, xf, yf - 1), grad(bb, xf - 1, yf - 1), u)
    return _lerp(x1, x2, v)


def _fbm(x, y, perm, octaves, gain, freq0, ridged):
    """Fractal noise summed over octaves at coords scaled by freq0 (cycles per unit domain).
    `ridged` in [0, 1] blends each octave toward a ridged-multifractal transform (sharp crests)."""
    total = np.zeros_like(x)
    amp, norm, freq = 1.0, 0.0, freq0
    for _ in range(int(octaves)):
        n = _perlin(x * freq, y * freq, perm)         # ~[-1, 1]
        smooth = 0.5 * (n + 1.0)                       # [0, 1] fBm octave
        ridge = (1.0 - np.abs(n)) ** 2                 # [0, 1], sharp ridgeline at n == 0
        total += amp * ((1.0 - ridged) * smooth + ridged * ridge)
        norm += amp
        amp *= gain
        freq *= 2.0
    return total / max(norm, 1e-9)


def generate_base(size, seed=0, octaves=6, roughness=0.5, ridged=0.5,
                  warp=None, detail_strength=0.6):
    """A composed base heightfield in [0, 1]: a low-frequency landform shape carrying warped,
    ridged high-frequency detail. Resolution-independent (world-sampled)."""
    perm = _perm(seed)
    # Normalised world grid over [0, 1); the SAMPLE POINTS densify with size but address the
    # same continuous field, which is what makes preview and final the same landform.
    u = (np.arange(size) + 0.5) / size
    x, y = np.meshgrid(u, u)

    # Domain warp: offset the detail coordinates by a low-frequency noise field (world units).
    # warp is a small domain-fraction amplitude; a legacy pixel-scale value (~60-100) is mapped
    # down so old presets stay sane until they are retuned.
    warp_amp = 0.15 if warp is None else float(warp) * 0.003
    pw = _perm(seed + 777)
    xw = x + warp_amp * _perlin(x * 2.0, y * 2.0, pw)
    yw = y + warp_amp * _perlin(x * 2.0 + 5.2, y * 2.0 + 1.7, pw)

    shape = _fbm(x, y, _perm(seed + 53), octaves=3, gain=0.5, freq0=2.0, ridged=0.0)
    detail = _fbm(xw, yw, perm, octaves=octaves, gain=roughness, freq0=5.0, ridged=ridged)

    combined = shape * (1.0 + (detail - 0.5) * detail_strength)
    combined -= combined.min()
    combined /= max(combined.max(), 1e-9)
    return combined
