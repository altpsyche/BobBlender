"""Erosion passes on a 2D heightfield.

Three kinds, composed as an ordered list:

- thermal: slump material down slopes steeper than a talus angle. Cheap stencil.
- stream_power: drainage-area incision (carves valley networks). CPU only, the
  flow accumulation is an inherently sequential topological sum.
- hydraulic: droplet-based hydraulic erosion with sediment transport and
  deposition. This is the GPU track. Erosion is spread over a radius brush (not a
  single cell), which is what turns spiky pits into smooth valleys. A seeded CPU
  sequential reference (numpy) is the deterministic golden path; a CuPy RawKernel
  is the fast path. Both start from the same host-seeded droplet positions but are
  not bit-identical (GPU atomicAdd order), so the CPU path is the reference.

run_passes(h, passes, backend, seed) applies a list and returns a [0, 1] field.
"""

import logging
import os

import numpy as np

log = logging.getLogger("bob.heightfields")

# The CPU droplet loop is scalar Python (~2.4k droplets/s), so an uncapped preset
# density (1.5M+) is a multi-minute-to-multi-hour hang with no feedback. On CPU,
# clamp the droplet count to this many with a warning; override with the env var,
# or use a GPU backend for the full density. The GPU path is uncapped.
CPU_DROPLET_CAP = int(os.environ.get("BOB_HF_CPU_DROPLET_CAP", "200000"))

SQRT2 = 2.0 ** 0.5
_NEIGHBOURS = [
    (-1, 0, 1.0), (1, 0, 1.0), (0, -1, 1.0), (0, 1, 1.0),
    (-1, -1, SQRT2), (-1, 1, SQRT2), (1, -1, SQRT2), (1, 1, SQRT2),
]

_HYDRAULIC_DEFAULTS = dict(
    droplets=250_000, max_steps=64, inertia=0.05, capacity=8.0, deposition=0.3,
    erosion=0.3, evaporation=0.02, gravity=10.0, min_slope=0.01,
    start_speed=1.0, start_water=1.0, radius=3,
)


# Thermal and stream-power (CPU).

def _edge_shift(a, dy, dx):
    padded = np.pad(a, 1, mode="edge")
    return padded[1 + dy : 1 + dy + a.shape[0], 1 + dx : 1 + dx + a.shape[1]]


def thermal(h, talus=0.008, factor=0.35, iterations=1):
    """Slump material down 4-neighbour slopes steeper than talus. In place."""
    for _ in range(iterations):
        for dy, dx, _dist in _NEIGHBOURS[:4]:
            diff = h - _edge_shift(h, dy, dx)
            move = np.clip((diff - talus) * factor, 0.0, None)
            h -= move
            h += _edge_shift(move, -dy, -dx)
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
    edge (0 sinks edges to the field minimum). Run before hydraulic so drainage
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


# Droplet-hydraulic erosion.

def _erosion_brush(radius):
    """A radial falloff kernel: (dy, dx) offsets and normalised weights.

    Spreading erosion over this brush instead of a single cell is what prevents
    the spiky pits that a per-cell drop would leave.
    """
    radius = max(1, int(radius))
    dys, dxs, ws = [], [], []
    for dy in range(-radius, radius + 1):
        for dx in range(-radius, radius + 1):
            d = (dx * dx + dy * dy) ** 0.5
            if d <= radius:
                dys.append(dy); dxs.append(dx); ws.append(1.0 - d / (radius + 1.0))
    w = np.array(ws, dtype=np.float32)
    w /= w.sum()
    return np.array(dys, dtype=np.int32), np.array(dxs, dtype=np.int32), w


_KERNEL_SRC = r"""
#define HG(idx) fmaxf(h[(idx)], 0.f)

extern "C" __global__
void hydraulic(float* h, const int W, const int H,
               const float* sx, const float* sy, const int n,
               const int max_steps, const float inertia, const float capacity,
               const float deposition, const float erosion, const float evaporation,
               const float gravity, const float min_slope,
               const float start_speed, const float start_water,
               const int* bdy, const int* bdx, const float* bw, const int bn) {
    int i = blockDim.x * blockIdx.x + threadIdx.x;
    if (i >= n) return;
    float px = sx[i], py = sy[i];
    float dx = 0.f, dy = 0.f, speed = start_speed, water = start_water, sed = 0.f;
    for (int s = 0; s < max_steps; s++) {
        int x0 = (int)floorf(px), y0 = (int)floorf(py);
        if (x0 < 0 || x0 >= W - 1 || y0 < 0 || y0 >= H - 1) break;
        float fx = px - x0, fy = py - y0;
        int x1 = x0 + 1, y1 = y0 + 1;
        float h00 = HG(y0*W+x0), h10 = HG(y0*W+x1), h01 = HG(y1*W+x0), h11 = HG(y1*W+x1);
        float oldH = h00*(1-fx)*(1-fy) + h10*fx*(1-fy) + h01*(1-fx)*fy + h11*fx*fy;
        float gx = (h10-h00)*(1-fy) + (h11-h01)*fy;
        float gy = (h01-h00)*(1-fx) + (h11-h10)*fx;
        dx = dx*inertia - gx*(1.f-inertia);
        dy = dy*inertia - gy*(1.f-inertia);
        float len = sqrtf(dx*dx + dy*dy);
        if (len < 1e-6f) {  // stalled in an interior pit: drop the load here
            atomicAdd(&h[y0*W+x0], sed*(1-fx)*(1-fy));
            atomicAdd(&h[y0*W+x1], sed*fx*(1-fy));
            atomicAdd(&h[y1*W+x0], sed*(1-fx)*fy);
            atomicAdd(&h[y1*W+x1], sed*fx*fy);
            break;
        }
        dx /= len; dy /= len;
        float nx = px + dx, ny = py + dy;
        if (nx < 0 || nx >= W - 1 || ny < 0 || ny >= H - 1) {
            break;  // ran off the grid: discard the load so borders build no rim
        }
        int mx0 = (int)floorf(nx), my0 = (int)floorf(ny);
        float mfx = nx - mx0, mfy = ny - my0;
        float m00 = HG(my0*W+mx0), m10 = HG(my0*W+mx0+1), m01 = HG((my0+1)*W+mx0), m11 = HG((my0+1)*W+mx0+1);
        float newH = m00*(1-mfx)*(1-mfy) + m10*mfx*(1-mfy) + m01*(1-mfx)*mfy + m11*mfx*mfy;
        float delta = newH - oldH;
        float cap = fmaxf(-delta, min_slope) * speed * water * capacity;
        if (sed > cap || delta > 0.f) {  // deposit bilinearly at the current point
            float amt = (delta > 0.f) ? fminf(delta, sed) : (sed - cap) * deposition;
            atomicAdd(&h[y0*W+x0], amt*(1-fx)*(1-fy));
            atomicAdd(&h[y0*W+x1], amt*fx*(1-fy));
            atomicAdd(&h[y1*W+x0], amt*(1-fx)*fy);
            atomicAdd(&h[y1*W+x1], amt*fx*fy);
            sed -= amt;
        } else {  // erode, spread over the brush so valleys stay smooth
            float amt = fminf((cap - sed) * erosion, -delta);
            amt = fminf(amt, oldH);
            for (int b = 0; b < bn; b++) {
                int bx = x0 + bdx[b], by = y0 + bdy[b];
                if (bx < 0 || bx >= W || by < 0 || by >= H) continue;
                atomicAdd(&h[by*W+bx], -amt*bw[b]);
            }
            sed += amt;
        }
        px = nx; py = ny;
        speed = sqrtf(fmaxf(speed*speed + (-delta)*gravity, 0.f));
        water *= (1.f - evaporation);
        if (water < 1e-4f) break;
    }
}
"""


def _start_positions(shape, droplets, seed):
    """Host-seeded droplet start positions, shared by both backends."""
    H, W = shape
    rng = np.random.default_rng(seed)
    sx = rng.uniform(0.0, W - 1.001, size=droplets).astype(np.float32)
    sy = rng.uniform(0.0, H - 1.001, size=droplets).astype(np.float32)
    return sx, sy


def _hydraulic_gpu(h, sx, sy, p, backend):
    cp = backend.xp
    H, W = h.shape
    bdy, bdx, bw = _erosion_brush(p["radius"])
    hd = cp.asarray(h, dtype=cp.float32)
    kernel = cp.RawKernel(_KERNEL_SRC, "hydraulic")
    n = sx.size
    threads = 256
    blocks = (n + threads - 1) // threads
    kernel((blocks,), (threads,), (
        hd, np.int32(W), np.int32(H), cp.asarray(sx), cp.asarray(sy), np.int32(n),
        np.int32(p["max_steps"]), np.float32(p["inertia"]), np.float32(p["capacity"]),
        np.float32(p["deposition"]), np.float32(p["erosion"]), np.float32(p["evaporation"]),
        np.float32(p["gravity"]), np.float32(p["min_slope"]),
        np.float32(p["start_speed"]), np.float32(p["start_water"]),
        cp.asarray(bdy), cp.asarray(bdx), cp.asarray(bw), np.int32(bw.size),
    ))
    backend.synchronize()
    out = backend.asnumpy(hd).astype(np.float64)
    if not np.isfinite(out).all():
        raise FloatingPointError("GPU hydraulic erosion produced non-finite values")
    return out


def _hydraulic_cpu(h, sx, sy, p):
    """Sequential droplet erosion: each droplet updates the terrain before the
    next, self-limiting and deterministic. The golden reference; keep counts modest.
    """
    H, W = h.shape
    inertia, capacity, deposition = p["inertia"], p["capacity"], p["deposition"]
    erosion, evaporation, gravity = p["erosion"], p["evaporation"], p["gravity"]
    min_slope, start_speed, start_water = p["min_slope"], p["start_speed"], p["start_water"]
    max_steps = int(p["max_steps"])
    bdy, bdx, bw = _erosion_brush(p["radius"])
    brush = list(zip(bdy.tolist(), bdx.tolist(), bw.tolist()))

    def hg(y, x):
        return max(h[y, x], 0.0)

    def deposit(y0, x0, fx, fy, amt):
        h[y0, x0] += amt * (1 - fx) * (1 - fy)
        h[y0, x0 + 1] += amt * fx * (1 - fy)
        h[y0 + 1, x0] += amt * (1 - fx) * fy
        h[y0 + 1, x0 + 1] += amt * fx * fy

    def erode_brush(y0, x0, amt):
        for dyy, dxx, w in brush:
            by, bx = y0 + dyy, x0 + dxx
            if 0 <= by < H and 0 <= bx < W:
                h[by, bx] -= amt * w

    for i in range(sx.size):
        px = float(sx[i]); py = float(sy[i])
        dx = dy = 0.0
        speed, water, sed = start_speed, start_water, 0.0
        for _ in range(max_steps):
            x0 = int(px); y0 = int(py)
            if x0 < 0 or x0 >= W - 1 or y0 < 0 or y0 >= H - 1:
                break
            fx = px - x0; fy = py - y0
            h00 = hg(y0, x0); h10 = hg(y0, x0 + 1); h01 = hg(y0 + 1, x0); h11 = hg(y0 + 1, x0 + 1)
            oldH = h00*(1-fx)*(1-fy) + h10*fx*(1-fy) + h01*(1-fx)*fy + h11*fx*fy
            gx = (h10 - h00) * (1 - fy) + (h11 - h01) * fy
            gy = (h01 - h00) * (1 - fx) + (h11 - h10) * fx
            dx = dx * inertia - gx * (1 - inertia)
            dy = dy * inertia - gy * (1 - inertia)
            ln = (dx * dx + dy * dy) ** 0.5
            if ln < 1e-6:
                deposit(y0, x0, fx, fy, sed)
                break
            dx /= ln; dy /= ln
            nx = px + dx; ny = py + dy
            if nx < 0 or nx >= W - 1 or ny < 0 or ny >= H - 1:
                break  # ran off the grid: discard the load so borders build no rim
            mx0 = int(nx); my0 = int(ny)
            mfx = nx - mx0; mfy = ny - my0
            m00 = hg(my0, mx0); m10 = hg(my0, mx0 + 1); m01 = hg(my0 + 1, mx0); m11 = hg(my0 + 1, mx0 + 1)
            newH = m00*(1-mfx)*(1-mfy) + m10*mfx*(1-mfy) + m01*(1-mfx)*mfy + m11*mfx*mfy
            delta = newH - oldH
            cap = max(-delta, min_slope) * speed * water * capacity
            if sed > cap or delta > 0:
                amt = min(delta, sed) if delta > 0 else (sed - cap) * deposition
                deposit(y0, x0, fx, fy, amt)
                sed -= amt
            else:
                amt = min((cap - sed) * erosion, -delta)
                amt = min(amt, oldH)
                erode_brush(y0, x0, amt)
                sed += amt
            px = nx; py = ny
            speed = max(speed * speed + (-delta) * gravity, 0.0) ** 0.5
            water *= (1 - evaporation)
            if water < 1e-4:
                break
    return h


def hydraulic(h, backend, seed=0, **params):
    """Droplet-hydraulic erosion. GPU when the backend is a GPU, else CPU."""
    p = {**_HYDRAULIC_DEFAULTS, **params}
    is_gpu = backend is not None and backend.is_gpu
    n = int(p["droplets"])
    if not is_gpu and n > CPU_DROPLET_CAP:
        log.warning(
            "CPU erosion: capping droplets %d -> %d to avoid a multi-minute hang "
            "(use a GPU backend or raise BOB_HF_CPU_DROPLET_CAP for the full density)",
            n, CPU_DROPLET_CAP,
        )
        n = CPU_DROPLET_CAP
    sx, sy = _start_positions(h.shape, n, seed)
    if is_gpu:
        return _hydraulic_gpu(h, sx, sy, p, backend)
    return _hydraulic_cpu(h, sx, sy, p)


# Pass runner.

def run_passes(h, passes, backend, seed=0):
    """Apply an ordered list of erosion passes, return a normalised [0, 1] field."""
    h = h.astype(np.float64).copy()
    for i, spec in enumerate(passes):
        spec = dict(spec)
        kind = spec.pop("kind")
        if kind == "hydraulic":
            h = hydraulic(h, backend, seed=seed + 101 * (i + 1), **spec)
        elif kind == "thermal":
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
