"""Headless measurement: `heightmap_macro`, a prompted terrain macro mask (docs/GENERATION.md).

The question is not "does it generate an image that looks like terrain". It is whether an artist's
silhouette still OWNS the landform after Bob's erosion stack has run, and whether the result is a
landform at all rather than a blurred picture with slopes painted on it. So nothing here is scored
by eye:

  A. **The arithmetic, offline.** The derivation is one cutoff of the same luminance track A uses;
     the composition demotes the preset's own generator instead of being overwritten by it; the bake
     cache notices when a mask file changes under a name it has already seen; and the 8-bit budget is
     stated in levels and in metres before anything is generated. No server, always runs.
  B. **Three prompts, each baked three ways.** The mask alone (the blurred-image baseline), the mask
     plus the preset stack (the shipped path), and the preset stack with no mask (the null the
     correlation is read against). Scored by band-limited correlation at the mask's own cutoff, by
     the slope-area concavity index, and by the channelised fraction. Wall clock and per-process
     VRAM beside each.
  C. **The 8-bit question, end to end.** The same mask at 8 bits, at 16 bits and deliberately
     crushed to 5, through the same stack, differenced in METRES and then RENDERED and differenced
     in pixels against the renderer's own noise floor. The bit-depth worry is that 256 levels cannot
     carry a heightfield;
     this measures what 256 levels carry when the thing they carry is a mask.
  D. **Residency.** Whether this route can share a card with what is already resident, which on a
     16.3 GB card after a `mesh_geom_ctrl` job is a real question, and the block-out control gate is
     where that was first measured.
  E. **The panel path.** Generate Base and then Bake + Build through the real operators and the real
     job queue, with the main-thread tick measured, and the assertion that switching the mask off
     bakes the preset exactly as it always did.

    ~/.steam/steam/steamapps/common/Blender/blender --background --factory-startup \\
        --python tools/scripts/headless_gen_terrain_macro.py -- [--part a,b,c,d,e] [--fresh] [--preset alpine]

Reachability-gated: with no server every generation half prints SKIP and exits 0, which is itself
the check that ComfyUI is never required. Generated masks cache WITH their timing and VRAM under
`_generated/comfy_g5_check/gen/`, so a re-measured table is not a table of zeros. Exit 0 = nothing
failed.
"""

import argparse
import json
import os
import subprocess
import sys
import threading
import time

import bpy
import numpy as np

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(REPO, "blender", "extensions"))

from bob_blender_tools.core import comfy, comfy_maps  # noqa: E402
from bob_blender_tools.core import heightfields as hf  # noqa: E402
from bob_blender_tools.core.heightfields import ops_erode  # noqa: E402

FAILURES = []
OUT = os.path.join(REPO, "_generated", "comfy_g5_check")
GEN = os.path.join(OUT, "gen")

# One that erosion should FIGHT (an isolated steep massif has no drainage network to agree with),
# one it should AGREE with (a broad basin is where water was going anyway), and one directional
# feature that tests whether a silhouette survives as a line rather than as a blob.
PROMPTS = {
    "massif": "a single isolated steep massif rising abruptly from a flat plain",
    "basin": "a broad shallow basin ringed by low rolling hills",
    "ridge": "one long ridge running corner to corner with a valley on each side",
}
SEED = 5150
PRESET = "alpine"
BAKE_SIZE = 768

# The terrain the numbers are expressed on, so a difference can be quoted in metres rather than in
# normalised units nobody can judge. These are the panel's own defaults.
TILE_M = 180.0

# Render settings for part C. Small and cheap: the question is whether a quantisation step is
# VISIBLE, and a step that a 512-pixel frame cannot show is not one an artist will find.
RENDER_PX = 512
RENDER_SAMPLES = 32


def check(label, ok, detail=""):
    print(f"[{'PASS' if ok else 'FAIL'}] {label}" + (f" -- {detail}" if detail else ""))
    if not ok:
        FAILURES.append(label)
    return ok


def note(label, value):
    print(f"[----] {label} -- {value}")


def section(title):
    print()
    print(f"-- {title} " + "-" * max(0, 76 - len(title)))


def empty_scene():
    bpy.ops.wm.read_factory_settings(use_empty=True)


# -- VRAM, per process and summed over the ComfyUI family -----------------------------------------
_OURS = {os.getpid()}


def _gpu_sample():
    try:
        card = subprocess.run(["nvidia-smi", "--query-gpu=memory.used",
                               "--format=csv,noheader,nounits"],
                              capture_output=True, text=True, timeout=10).stdout.strip()
        apps = subprocess.run(["nvidia-smi", "--query-compute-apps=pid,used_memory",
                               "--format=csv,noheader,nounits"],
                              capture_output=True, text=True, timeout=10).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None, {}
    procs = {}
    for line in apps.splitlines():
        bits = [b.strip() for b in line.split(",")]
        if len(bits) == 2 and bits[0].isdigit() and bits[1].isdigit():
            procs[int(bits[0])] = int(bits[1])
    return (int(card.splitlines()[0]) if card else None), procs


class Vram:
    """Peak VRAM across a job, sampled from a thread: per process, summed over the ComfyUI family,
    with the RISE over this stage's own baseline reported beside the absolute peak. Same class as the
    one-shot-against-staged, stylise and block-out-control gates, because the numbers have to be
    comparable with theirs."""

    def __init__(self, interval=0.5):
        self.interval = interval
        self.card_peak = self.comfy_peak = 0
        self.card_start = self.comfy_start = 0
        self._stop = threading.Event()
        self._thread = None

    def _family(self, procs):
        return sum(mib for pid, mib in procs.items() if pid not in _OURS)

    def _loop(self):
        while not self._stop.is_set():
            card, procs = _gpu_sample()
            if card is not None:
                self.card_peak = max(self.card_peak, card)
                self.comfy_peak = max(self.comfy_peak, self._family(procs))
            self._stop.wait(self.interval)

    def __enter__(self):
        card, procs = _gpu_sample()
        self.card_start = self.card_peak = card or 0
        self.comfy_start = self.comfy_peak = self._family(procs)
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *exc):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)
        return False

    def report(self):
        return {"card_start": self.card_start, "card_peak": self.card_peak,
                "comfy_start": self.comfy_start, "comfy_peak": self.comfy_peak,
                "rise": self.comfy_peak - self.comfy_start}


# -- The measurements ---------------------------------------------------------------------------
def resample(a, n):
    """Bilinear resample to n x n, the same way the macro op does it, so a comparison between a mask
    and a baked field is between two fields on one grid."""
    import scipy.ndimage as ndi

    if a.shape[0] == n and a.shape[1] == n:
        return np.asarray(a, dtype=np.float64)
    return ndi.zoom(np.asarray(a, dtype=np.float64), (n / a.shape[0], n / a.shape[1]),
                    order=1, mode="nearest")


def band_sigma(n, fraction=comfy_maps.MACRO_LOWPASS_FRACTION):
    """The gaussian sigma that splits a field where the MASK's own cutoff is.

    Not `fraction * n` itself: the mask is low-passed by a BOX blur of that radius, and a box blur of
    radius r has the second moment of a gaussian of r / sqrt(3). Splitting at the wider sigma would
    charge the mask for content it does not have and put nine percent of its own variance in the band
    that is supposed to be the erosion's, which is what the first run of this gate did.
    """
    return fraction * n / np.sqrt(3.0)


def bands(a, sigma):
    """Split a field at one cutoff: (low, high). The cutoff is the MASK's own cutoff, so the two
    bands are exactly "what the prompt could have said" and "what only erosion can say"."""
    import scipy.ndimage as ndi

    low = ndi.gaussian_filter(np.asarray(a, dtype=np.float64), sigma, mode="nearest")
    return low, a - low


def corr(a, b):
    """Pearson correlation over the whole field."""
    a = np.asarray(a, dtype=np.float64).ravel()
    b = np.asarray(b, dtype=np.float64).ravel()
    a = a - a.mean()
    b = b - b.mean()
    denom = np.sqrt(float((a * a).sum()) * float((b * b).sum()))
    return float((a * b).sum() / denom) if denom > 0 else 0.0


def band_report(mask, field, fraction=comfy_maps.MACRO_LOWPASS_FRACTION):
    """Correlation of a mask against a baked field, per band, plus where the field's variance is.

    `r_low` is the survival number: does the shape the artist asked for still exist. `r_high` should
    be near zero and it is not a formality -- if erosion's fine detail correlated with the mask's
    residual noise, the mask would be leaking quantisation steps into the band that is supposed to be
    the erosion's alone. `high_var` says how much of the finished landform the erosion actually built.
    """
    n = field.shape[0]
    sigma = band_sigma(n, fraction)
    m_low, m_high = bands(resample(mask, n), sigma)
    f_low, f_high = bands(field, sigma)
    total = float(field.var())
    return {"r_low": corr(m_low, f_low), "r_high": corr(m_high, f_high),
            "high_var": (float(f_high.var()) / total) if total > 0 else 0.0}


_D8 = ((-1, 0, 1.0), (1, 0, 1.0), (0, -1, 1.0), (0, 1, 1.0),
       (-1, -1, 2 ** 0.5), (-1, 1, 2 ** 0.5), (1, -1, 2 ** 0.5), (1, 1, 2 ** 0.5))

# Margin dropped from every drainage statistic. Erosion here is edge-aware and the borders ARE the
# outlets, so the outermost cells carry the whole tile's discharge over a short drop and would
# dominate any slope-area fit with an artefact of the boundary condition.
MARGIN_FRACTION = 1.0 / 32.0

# Where the fine band is split for the "how much relief did erosion actually add" number. A 64th of
# the width, i.e. finer than the mask's cutoff by a factor of five, so this is unambiguously the
# band no prompt could have specified.
FINE_FRACTION = 1.0 / 64.0


def d8_slope(h):
    """Steepest DOWNSLOPE gradient per cell, in normalised height per tile width.

    The downstream gradient, not the central-difference magnitude: in a one-cell-wide incision the
    central difference measures the gradient ACROSS the channel, which is the steepest thing in the
    frame and has nothing to do with the channel's own profile.
    """
    xp = np
    n = h.shape[0]
    best = xp.zeros_like(h)
    for dy, dx, dist in _D8:
        best = xp.maximum(best, (h - ops_erode._sh(xp, h, dy, dx)) / (dist / n))
    return best


def slope_area(h, acc, bins=14, lo=30.0, hi=3000.0):
    """The log-log slope-area gradient, binned. Returns (gradient, r).

    Medians per logarithmic bin rather than a fit through the raw cloud, because the cloud is wildly
    heteroscedastic and a least-squares line through it is dominated by hillslope cells. The range is
    the channelised one: 30 to 3000 upstream cells.

    Reported as the GRADIENT and read against the no-mask bake rather than against Flint's law. This
    engine's own gradient is positive (slope rises with drainage area), which is not what an
    equilibrium landscape does; see docs/GENERATION-BASELINES.md, because that is a finding about the terrain
    engine and not about the mask.
    """
    n = h.shape[0]
    m = int(n * MARGIN_FRACTION)
    inner = (slice(m, n - m), slice(m, n - m))
    a = acc[inner].ravel()
    s = d8_slope(h)[inner].ravel()
    keep = (a >= lo) & (a <= hi) & (s > 1e-9)
    if keep.sum() < 1000:
        return 0.0, 0.0
    a, s = a[keep], s[keep]
    edges = np.logspace(np.log10(lo), np.log10(hi), bins + 1)
    which = np.digitize(a, edges) - 1
    xs, ys = [], []
    for b in range(bins):
        cells = which == b
        if cells.sum() > 50:
            xs.append(np.log10(np.median(a[cells])))
            ys.append(np.log10(np.median(s[cells])))
    if len(xs) < 4:
        return 0.0, 0.0
    return float(np.polyfit(xs, ys, 1)[0]), float(np.corrcoef(xs, ys)[0, 1])


def landform_report(field, height_m):
    """Is this a landform the engine made, or a blurred picture: three numbers, all in real units.

    `fine_m` is the decisive one and it needs no theory: how many metres of relief live above a
    64th-of-the-width cutoff, which is the band a prompt cannot reach and only erosion can fill.
    `slope_med` is that in slope terms, in degrees, so it can be judged against a real hillside; the
    median rather than a high percentile because at this relief ratio the 95th percentile saturates
    near vertical on any eroded field and stops discriminating. `sa_gradient` is the drainage
    organisation, read against the no-mask bake.
    """
    xp = np
    h = np.asarray(field, dtype=np.float64)
    n = h.shape[0]
    m = int(n * MARGIN_FRACTION)
    inner = (slice(m, n - m), slice(m, n - m))
    filled = ops_erode._pd_fill(h, xp, 900, 1e-4)
    acc = ops_erode._mfd_accum(filled, xp, 900, 1.4, 1.0)
    gradient, fit_r = slope_area(h, acc)
    fine = bands(h, FINE_FRACTION * n)[1]
    # Real slope in degrees: the normalised gradient scaled by the tile's own aspect.
    degrees = np.degrees(np.arctan(d8_slope(h) * height_m / TILE_M))
    drained = acc / float(n * n)
    return {"fine_m": float(fine[inner].std() * height_m),
            "slope_med": float(np.median(degrees[inner])),
            "slope_p95": float(np.percentile(degrees[inner], 95)),
            "sa_gradient": gradient, "sa_r": fit_r,
            "channels": float((drained[inner] > 1e-3).mean())}


def bake(out_name, stack, size=BAKE_SIZE, seed=SEED, backend="auto"):
    """One bake through the shipped pipeline, returning (field, meta)."""
    path = os.path.join(OUT, out_name + ".png")
    resolved = hf.params.resolve_amplify_targets(stack, size, hf.presets.relief(PRESET))
    meta = hf.bake(path, {"size": size, "seed": seed, "backend": backend, "stack": resolved},
                   force=True)
    return hf.io.read_png16(path), meta


def stacks_for(mask_path, weight=hf.params.MACRO_WEIGHT):
    """The three stacks every prompt is measured through.

    `mask_only` is the honest baseline for "a diffusion heightmap is not terrain": the mask resampled
    and blurred, with no erosion at all. `full` is the shipped path. `null` is the same preset with
    no mask, which is what the correlation has to beat to mean anything.
    """
    preset = hf.presets.stack(PRESET)
    return {
        "mask_only": [{"kind": "macro", "path": mask_path, "mix": "replace", "amount": 1.0,
                       "smooth": hf.params.MACRO_SMOOTH}],
        "full": hf.params.with_macro(preset, mask_path, weight=weight),
        "null": hf.presets.stack(PRESET),
    }


# -- Part A: the arithmetic, offline -------------------------------------------------------------
def synthetic_prompt_image(n=1024, seed=3):
    """A stand-in for a generation: one bright massif, one dark basin, and enough fine noise that a
    derivation which kept the high frequencies would be obvious."""
    rng = np.random.default_rng(seed)
    y, x = np.mgrid[0:n, 0:n] / n
    field = (0.75 * np.exp(-(((x - 0.32) ** 2 + (y - 0.30) ** 2) / 0.02))
             + 0.35 * np.exp(-(((x - 0.75) ** 2 + (y - 0.72) ** 2) / 0.05)))
    field = np.clip(0.15 + field + 0.30 * rng.random((n, n)), 0.0, 1.0)
    return (np.repeat(field[:, :, None], 3, axis=2) * 255).astype(np.uint8)


def part_a(args):
    section("A. the derivation, the composition, the cache, and the 8-bit budget (no server)")
    os.makedirs(OUT, exist_ok=True)
    rgb = synthetic_prompt_image()
    float_mask = comfy_maps.macro_field(rgb)
    mask8 = comfy_maps.macro_from(rgb)

    # 1. The derivation is relief()'s other half, not a second module.
    relief = comfy_maps.relief(rgb)
    r = corr(float_mask, relief)
    check("the macro mask is the low band, not the high one",
          abs(r) < 0.25 and float_mask.min() < 0.02 and float_mask.max() > 0.98,
          f"corr(macro, relief) {r:+.4f}, range {float_mask.min():.3f}..{float_mask.max():.3f}")
    # How much of the generation's detail the derivation throws away, as an attenuation rather than
    # as an absolute: the mask's own high-band energy against the source luminance's, at one cutoff.
    sigma = band_sigma(float_mask.shape[0])
    source = comfy_maps.luminance(rgb)
    src_high = bands(source, sigma)[1].var() / max(float(source.var()), 1e-12)
    mask_high = bands(float_mask, sigma)[1].var() / max(float(float_mask.var()), 1e-12)
    check("the derivation strips the band the erosion owns",
          mask_high < src_high / 10.0,
          f"{100 * mask_high:.2f}% of the mask's variance sits above the cutoff against "
          f"{100 * src_high:.2f}% of the source image's, a {src_high / max(mask_high, 1e-9):.0f}x "
          f"attenuation")

    # 2. The blur does not wrap, because a terrain tile is not a torus.
    seam_open = comfy_maps.seam_report(comfy_maps.macro_from(rgb, wrap=False))
    seam_tiled = comfy_maps.seam_report(comfy_maps.macro_from(rgb, wrap=True))
    note("seam ratio, open route", f"{seam_open['ratio']:.2f} (a border, as intended)")
    note("seam ratio, tiled route", f"{seam_tiled['ratio']:.2f} (continuous across the wrap)")
    check("the tiled route really is the tileable one",
          seam_tiled["ratio"] < seam_open["ratio"],
          f"{seam_tiled['ratio']:.2f} against {seam_open['ratio']:.2f}")

    # 3. The composition demotes the preset's own generator instead of being overwritten by it.
    path = os.path.join(OUT, "macro_synthetic.png")
    comfy_maps.write_png(path, mask8)
    composed = hf.params.with_macro(hf.presets.stack(PRESET), path, weight=0.6)
    first_gen = next(op for op in composed[1:] if op["kind"] in hf.params._GENERATORS)
    check("with_macro puts the mask first and demotes the generator to an add",
          composed[0]["kind"] == "macro" and first_gen["mix"] == "add"
          and abs(first_gen["amount"] - 0.4) < 1e-9,
          f"stack {[op['kind'] for op in composed]}, generator mix "
          f"{first_gen['mix']} at {first_gen['amount']:.2f}")
    check("the ops after the mask are the preset's, unchanged",
          [op["kind"] for op in composed[1:]] == [op["kind"] for op in hf.presets.stack(PRESET)],
          "erosion cannot tell a mask macro from a noise one")

    # 4. The cache keys on the mask's CONTENT, not just its path.
    tiny = [{"kind": "macro", "path": path, "mix": "replace", "amount": 1.0, "smooth": 0.02}]
    cached_path = os.path.join(OUT, "cache_probe.png")
    first = hf.bake(cached_path, {"size": 128, "seed": 1, "backend": "cpu", "stack": tiny},
                    force=True)
    again = hf.bake(cached_path, {"size": 128, "seed": 1, "backend": "cpu", "stack": tiny})
    comfy_maps.write_png(path, comfy_maps.macro_from(synthetic_prompt_image(seed=9)))
    after = hf.bake(cached_path, {"size": 128, "seed": 1, "backend": "cpu", "stack": tiny})
    check("a re-baked mask at the same path is not served from cache",
          again.get("cached") is True and after.get("cached") is False
          and first["hash"] != after["hash"],
          f"unchanged: cached={again.get('cached')}, edited: cached={after.get('cached')}")

    # 5. The 8-bit budget, stated before anything is generated.
    height_m = hf.presets.relief(PRESET) * TILE_M
    step_m = height_m / 255.0
    q_err = float(np.abs(float_mask - np.round(float_mask * 255) / 255).max()) * height_m
    note("terrain the numbers are on", f"{TILE_M:.0f} m tile, {height_m:.1f} m relief "
                                       f"(preset relief ratio {hf.presets.relief(PRESET):.3f})")
    note("one 8-bit level, in metres", f"{step_m:.3f} m")
    note("worst-case quantisation error of the mask", f"{q_err:.3f} m")
    # The op's blur is the reason 8 bits is a different claim for a mask than for a heightfield:
    # averaging over the blur's support recovers precision below one level.
    blurred = bands(resample(np.round(float_mask * 255) / 255, 256),
                    hf.params.MACRO_SMOOTH * 256)[0]
    levels_before = len(np.unique(np.round(float_mask * 255)))
    levels_after = len(np.unique(np.round(blurred * 65535)))
    check("the op's resample and blur put the mask back above 8-bit precision",
          levels_after > 4 * levels_before,
          f"{levels_before} distinct levels in the 8-bit file, {levels_after} after the op's "
          f"resample and {hf.params.MACRO_SMOOTH:.2f}-of-width blur")
    return {"height_m": height_m, "step_m": step_m}


# -- Part B: three prompts, each baked three ways ------------------------------------------------
def generate_cached(name, run, fresh):
    """A generated mask with its timing and VRAM cached beside it, so a re-measured table reports
    what the generating run measured rather than a row of zeros."""
    os.makedirs(GEN, exist_ok=True)
    png = os.path.join(GEN, name + ".png")
    side = os.path.join(GEN, name + ".stats.json")
    if not fresh and os.path.exists(png) and os.path.exists(side):
        with open(side) as fh:
            return png, json.load(fh), True
    stats = run(png)
    with open(side, "w") as fh:
        json.dump(stats, fh, indent=2, sort_keys=True)
    return png, stats, False


def part_b(args, reachable):
    section("B. three prompts, each baked three ways")
    if not reachable:
        print("[SKIP] no ComfyUI server: no mask to bake from")
        return {}

    height_m = hf.presets.relief(PRESET) * TILE_M
    rows = []
    masks = {}
    for name, prompt in PROMPTS.items():
        def run(out_path, prompt=prompt, name=name):
            with Vram() as vram:
                info = comfy.heightmap_macro(prompt, out_path, seed=SEED, keep_source=True)
            return {"seconds": info["seconds"], "prompt": info["prompt"],
                    "tiled": info["tiled"], "vram": vram.report(), "source": info["source"]}

        png, stats, cached = generate_cached(name, run, args.fresh)
        masks[name] = png
        mask = hf.io.read_png(png)
        note(f"{name}: mask", f"{'cached' if cached else 'generated'} in "
                              f"{stats['seconds']:.1f}s, VRAM peak "
                              f"{stats['vram']['comfy_peak']} MiB "
                              f"(rise {stats['vram']['rise']}), tiled={stats['tiled']}")

        fields = {}
        timings = {}
        for label, stack in stacks_for(png).items():
            t0 = time.perf_counter()
            fields[label], meta = bake(f"{name}_{label}", stack)
            timings[label] = time.perf_counter() - t0
        row = {"name": name, "mask_s": stats["seconds"], "bake_s": timings["full"],
               "vram": stats["vram"]}
        for label in ("full", "null", "mask_only"):
            for key, value in band_report(mask, fields[label]).items():
                row[f"{label}_{key}"] = value
            for key, value in landform_report(fields[label], height_m).items():
                row[f"{label}_{key}"] = value
        rows.append(row)

    print()
    print("survival, at the mask's own cutoff (r_low) and above it (r_high):")
    print(f"{'prompt':8} {'mask s':>7} {'bake s':>7} {'r_low':>8} {'null':>8} {'r_high':>8} "
          f"{'null hi':>8} {'mask-lnk':>9} {'fine var':>9}")
    for r in rows:
        print(f"{r['name']:8} {r['mask_s']:7.1f} {r['bake_s']:7.1f} {r['full_r_low']:+8.4f} "
              f"{r['null_r_low']:+8.4f} {r['full_r_high']:+8.4f} {r['null_r_high']:+8.4f} "
              f"{r['full_r_high'] ** 2:9.3f} {r['full_high_var']:9.3f}")
    print()
    print("landform, in real units on a "
          f"{TILE_M:.0f} m tile at {height_m:.1f} m relief (mask-only in brackets):")
    print(f"{'prompt':8} {'fine m':>8} {'(mask)':>8} {'slp med':>8} {'(mask)':>8} {'(null)':>8} "
          f"{'sa grad':>8} {'(null)':>8} {'(mask)':>8} {'sa r':>6}")
    for r in rows:
        print(f"{r['name']:8} {r['full_fine_m']:8.3f} {r['mask_only_fine_m']:8.3f} "
              f"{r['full_slope_med']:8.2f} {r['mask_only_slope_med']:8.2f} "
              f"{r['null_slope_med']:8.2f} "
              f"{r['full_sa_gradient']:+8.3f} {r['null_sa_gradient']:+8.3f} "
              f"{r['mask_only_sa_gradient']:+8.3f} {r['full_sa_r']:+6.2f}")
    print()

    # The gate, as numbers rather than as an impression.
    for r in rows:
        check(f"{r['name']}: the silhouette survives the erosion pass",
              r["full_r_low"] > 0.7 and r["full_r_low"] > abs(r["null_r_low"]) + 0.5,
              f"r_low {r['full_r_low']:+.4f} against the no-mask null {r['null_r_low']:+.4f}")
    for r in rows:
        # Not "r_high is zero": r_high SQUARED is the share of the fine band the mask explains, and
        # the honest claim is that erosion owns most of it. The null's r_high beside it is what says
        # the residual coupling comes from the mask being in the stack rather than from luck.
        check(f"{r['name']}: erosion owns most of the band above the mask's cutoff",
              r["full_r_high"] ** 2 < 0.25,
              f"r_high {r['full_r_high']:+.4f}, so the mask explains "
              f"{100 * r['full_r_high'] ** 2:.0f}% of a fine band that is "
              f"{100 * r['full_high_var']:.0f}% of the field's variance "
              f"(no-mask null r_high {r['null_r_high']:+.4f})")
    for r in rows:
        check(f"{r['name']}: the result is a landform, not a blurred image",
              r["full_fine_m"] > 4 * r["mask_only_fine_m"]
              and r["full_slope_med"] > 1.3 * r["mask_only_slope_med"],
              f"{r['full_fine_m']:.2f} m of fine relief against the mask-only baseline's "
              f"{r['mask_only_fine_m']:.2f} m; median slope {r['full_slope_med']:.1f} deg against "
              f"{r['mask_only_slope_med']:.1f} deg, and the no-mask bake's own "
              f"{r['null_slope_med']:.1f} deg")
    for r in rows:
        check(f"{r['name']}: the drainage organisation is the engine's own",
              (r["full_sa_gradient"] > 0) == (r["null_sa_gradient"] > 0)
              and (r["full_sa_gradient"] > 0) != (r["mask_only_sa_gradient"] > 0),
              f"slope-area gradient {r['full_sa_gradient']:+.3f} beside the no-mask bake's "
              f"{r['null_sa_gradient']:+.3f}, and the mask alone has the opposite sign at "
              f"{r['mask_only_sa_gradient']:+.3f}")
    return {"rows": rows, "masks": masks}


# -- Part C: the 8-bit question, end to end -----------------------------------------------------
def terrain_from(path, name, height_m):
    """Build the shipped terrain from a heightmap PNG, exactly as the panel's Bake + Build does."""
    from bob_blender_tools.core.dispatch import apply_op

    apply_op({"op": "reload_image", "path": path})
    apply_op({"op": "build_geonodes", "recipe": "heightmap_terrain", "name": name,
              "params": {"heightmap": path, "size": TILE_M, "resolution": 384,
                         "height": height_m, "sea_level": 0.0},
              "reset": True})
    return bpy.data.objects.get(name)


def render_terrain(obj, out_path):
    """One flat-lit frame of the terrain from a fixed camera, so two bakes can be differenced."""
    import mathutils

    scene = bpy.context.scene
    if scene.camera is None:
        cam_data = bpy.data.cameras.new("G5Cam")
        cam = bpy.data.objects.new("G5Cam", cam_data)
        scene.collection.objects.link(cam)
        scene.camera = cam
        cam.location = (TILE_M * 0.75, -TILE_M * 0.75, TILE_M * 0.55)
        direction = mathutils.Vector((0.0, 0.0, 0.0)) - cam.location
        cam.rotation_euler = direction.to_track_quat("-Z", "Y").to_euler()
        sun_data = bpy.data.lights.new("G5Sun", type="SUN")
        sun_data.energy = 3.0
        sun = bpy.data.objects.new("G5Sun", sun_data)
        scene.collection.objects.link(sun)
        sun.rotation_euler = (0.9, 0.2, 2.2)   # a low raking sun, which is what shows terracing
        scene.render.engine = "BLENDER_EEVEE"
        scene.eevee.taa_render_samples = RENDER_SAMPLES
        scene.render.resolution_x = scene.render.resolution_y = RENDER_PX
        scene.render.image_settings.file_format = "PNG"
        scene.render.film_transparent = False
    scene.render.filepath = out_path
    bpy.ops.render.render(write_still=True)
    image = bpy.data.images.load(out_path)
    pixels = np.array(image.pixels[:], dtype=np.float32).reshape(
        image.size[1], image.size[0], image.channels)
    bpy.data.images.remove(image)
    return pixels[:, :, :3].mean(axis=2) * 255.0


def terracing(field, bins=2048):
    """How much of the field's mass sits on discrete levels: the terracing an 8-bit write was expected
    to cause, as a number.

    A terraced field has a comb histogram, so the concentration of the fullest bins over the median
    bin is the measurement. `zero_step` is the same idea in the spatial domain: the fraction of
    adjacent cell pairs at exactly the same height, which is what a bench is.
    """
    counts, _ = np.histogram(field, bins=bins, range=(0.0, 1.0))
    counts = counts[counts > 0]
    steps = np.abs(np.diff(field, axis=1))
    return {"concentration": float(np.percentile(counts, 99.9) / np.median(counts)),
            "peak": float(counts.max() / np.median(counts)),
            "zero_step": float((steps < 1e-9).mean())}


def part_c(args, reachable, masks):
    section("C. the 8-bit question: what 256 levels cost, in metres and then in pixels")
    # Fall back to whatever part B cached on an earlier run, so --part c is usable on its own.
    masks = masks or {name: os.path.join(GEN, name + ".png") for name in PROMPTS
                      if os.path.exists(os.path.join(GEN, name + ".png"))}
    if not masks:
        print("[SKIP] no cached mask to re-quantise; run --part b first")
        return

    name = "basin" if "basin" in masks else sorted(masks)[0]
    height_m = hf.presets.relief(PRESET) * TILE_M
    source = os.path.splitext(masks[name])[0] + "_source.png"
    if not os.path.exists(source):
        print(f"[SKIP] no cached generation beside {os.path.basename(masks[name])}; "
              "re-run part b with --fresh")
        return
    # The reference is the FLOAT derivation, not the file Bob ships. The generation is 8-bit per
    # channel, but the derivation averages three channels over a box of radius width/12, so the float
    # mask carries far more precision than any sample in the source did. That is what makes the
    # terracing
    # question answerable: the question is not what the diffusion model could express, it is what the
    # last 8-bit step between the derivation and the op stack costs.
    float_mask = comfy_maps.macro_field(comfy_maps.read_png(open(source, "rb").read()))
    note("distinct levels in the float derivation",
         f"{len(np.unique(np.round(float_mask, 6)))} against 256 in the shipped file")

    variants = {}
    mask_error = {}
    for label, levels in (("q16", 65535), ("q8", 255), ("q5", 31)):
        quantised = np.round(float_mask * levels) / levels
        path = os.path.join(OUT, f"{name}_{label}.png")
        if levels == 65535:
            hf.io.to_png16(quantised, path)
        else:
            comfy_maps.write_png(path, np.round(quantised * 255).astype(np.uint8))
        variants[label] = path
        mask_error[label] = float(np.abs(quantised - float_mask).max()) * height_m
        note(f"{label}: {len(np.unique(quantised))} levels in the file",
             f"worst-cell error against the float derivation {mask_error[label]:.4f} m "
             f"({100 * mask_error[label] / height_m:.3f}% of the relief)")

    fields = {}
    for label, path in variants.items():
        fields[label], _ = bake(f"{name}_bits_{label}",
                                hf.params.with_macro(hf.presets.stack(PRESET), path))
    # One bake at a different SEED, as the scale every difference below is read against: if a bit
    # depth moved the terrain as much as a reseed did, the mask would not be deciding the landform.
    fields["reseed"], _ = bake(f"{name}_bits_reseed",
                               hf.params.with_macro(hf.presets.stack(PRESET), variants["q16"]),
                               seed=SEED + 1)

    print()
    print("against the 16-bit path, through the identical stack:")
    print(f"{'':8} {'rms m':>8} {'max m':>8} {'r':>9} {'fine m':>8} {'slope':>7} {'comb':>7} "
          f"{'flat':>8}")
    rows = {}
    for label in ("q16", "q8", "q5", "reseed"):
        delta = (fields[label] - fields["q16"]) * height_m
        land = landform_report(fields[label], height_m)
        comb = terracing(fields[label])
        rows[label] = {"rms": float(np.sqrt((delta ** 2).mean())),
                       "max": float(np.abs(delta).max()),
                       "r": corr(fields[label], fields["q16"]),
                       "r_low": band_report(float_mask, fields[label])["r_low"], **land, **comb}
        print(f"{label:8} {rows[label]['rms']:8.4f} {rows[label]['max']:8.4f} "
              f"{rows[label]['r']:9.5f} {land['fine_m']:8.3f} {land['slope_med']:7.2f} "
              f"{comb['concentration']:7.3f} {comb['zero_step']:8.5f}")
    print()

    check("one 8-bit level costs a fraction of a percent of the relief",
          mask_error["q8"] < 0.005 * height_m,
          f"worst cell {mask_error['q8']:.4f} m of {height_m:.1f} m, and one level is "
          f"{height_m / 255.0:.4f} m")
    check("the op stack leaves NO terracing at any bit depth tested",
          all(abs(rows[label]["concentration"] - rows["q16"]["concentration"])
              < 0.1 * rows["q16"]["concentration"] for label in ("q8", "q5")),
          f"histogram concentration {rows['q16']['concentration']:.3f} at 16 bits, "
          f"{rows['q8']['concentration']:.3f} at 8, {rows['q5']['concentration']:.3f} at 5; "
          f"flat-pair fraction {rows['q16']['zero_step']:.5f} / {rows['q8']['zero_step']:.5f} / "
          f"{rows['q5']['zero_step']:.5f}")
    check("the landform is statistically the same at 8 bits",
          abs(rows["q8"]["fine_m"] - rows["q16"]["fine_m"]) < 0.02 * rows["q16"]["fine_m"]
          and abs(rows["q8"]["slope_med"] - rows["q16"]["slope_med"]) < 0.5
          and abs(rows["q8"]["r_low"] - rows["q16"]["r_low"]) < 0.01,
          f"fine relief {rows['q8']['fine_m']:.3f} m against {rows['q16']['fine_m']:.3f}, median "
          f"slope {rows['q8']['slope_med']:.2f} against {rows['q16']['slope_med']:.2f}, and the "
          f"mask's own survival r_low {rows['q8']['r_low']:+.4f} against "
          f"{rows['q16']['r_low']:+.4f}")
    check("a reseed moves the terrain far more than the bit depth does",
          rows["reseed"]["rms"] > 3 * rows["q8"]["rms"],
          f"reseed {rows['reseed']['rms']:.3f} m rms against the 8-bit path's "
          f"{rows['q8']['rms']:.3f} m")

    # Then the same question in pixels, against the renderer's own noise floor.
    empty_scene()
    renders = {}
    for label in ("q16", "q8", "q5", "reseed"):
        path = os.path.join(OUT, f"{name}_bits_{label}.png")
        obj = terrain_from(path, "G5Terrain", height_m)
        if obj is None:
            check("the terrain built for the render", False, "build_geonodes returned no object")
            return
        renders[label] = render_terrain(obj, os.path.join(OUT, f"render_{label}.png"))
    floor = np.abs(render_terrain(bpy.data.objects.get("G5Terrain"),
                                 os.path.join(OUT, "render_floor.png")) - renders["reseed"])
    noise = float(floor.mean())

    print()
    note("renderer noise floor (the same terrain rendered twice)", f"{noise:.4f} of 255")
    print(f"{'render':10} {'mean 255':>9} {'max 255':>9}")
    pixels = {}
    for label in ("q8", "q5", "reseed"):
        diff = np.abs(renders[label] - renders["q16"])
        pixels[label] = {"mean": float(diff.mean()), "max": float(diff.max())}
        print(f"{label:10} {pixels[label]['mean']:9.4f} {pixels[label]['max']:9.4f}")
    print()
    check("in the render, the bit depth is a fraction of what a reseed is",
          pixels["q8"]["mean"] < 0.4 * pixels["reseed"]["mean"],
          f"8-bit {pixels['q8']['mean']:.3f} of 255, 5-bit {pixels['q5']['mean']:.3f}, "
          f"a reseed {pixels['reseed']['mean']:.3f}, noise floor {noise:.4f}")


# -- Part D: residency --------------------------------------------------------------------------
def part_d(args, reachable):
    section("D. residency: can this route share the card with what is already on it")
    if not reachable:
        print("[SKIP] no ComfyUI server")
        return
    card, procs = _gpu_sample()
    family = sum(mib for pid, mib in procs.items() if pid not in _OURS)
    note("card before", f"{card} MiB used, {family} MiB of it the ComfyUI family "
                        f"({len(procs)} processes)")
    with Vram() as vram:
        t0 = time.perf_counter()
        info = comfy.heightmap_macro(PROMPTS["basin"], os.path.join(OUT, "residency_macro.png"),
                                     seed=SEED + 1)
        elapsed = time.perf_counter() - t0
    report = vram.report()
    note("heightmap_macro with that already resident", f"{elapsed:.1f}s, peak {report['comfy_peak']} MiB, "
                                           f"rise {report['rise']} MiB")
    try:
        comfy.free()
        time.sleep(2.0)
    except comfy.ComfyError as exc:
        note("POST /free", f"failed: {exc}")
    card_after, procs_after = _gpu_sample()
    held = sum(mib for pid, mib in procs_after.items() if pid not in _OURS)
    note("after POST /free", f"{card_after} MiB on the card, {held} MiB still held by the family")
    check("heightmap_macro ran without needing the card to itself", info["path"] and elapsed < 120,
          f"{elapsed:.1f}s at a {report['comfy_start']} MiB starting occupancy")


# -- Part E: the panel path ---------------------------------------------------------------------
ADDON = "bl_ext.user_default.bob_blender_tools"


def part_e(args, reachable):
    section("E. the panel path, through the real operator and the real job queue")
    empty_scene()
    try:
        bpy.ops.preferences.addon_enable(module=ADDON)
    except (RuntimeError, KeyError) as exc:
        print(f"[SKIP] extension not installed for this Blender ({exc})")
        return
    if not reachable:
        print("[SKIP] no ComfyUI server: the row would read 'not connected' and change nothing")
        return
    # The ADDON's comfy_jobs, not this script's: the extension is imported under its own module
    # name, so importing it by path would give a SECOND registry the operator's job is invisible in
    # (found by the stylise gate, and exactly the kind of thing a gate exists to catch).
    import importlib

    comfy_jobs = importlib.import_module(ADDON + ".core.comfy_jobs")
    scene = bpy.context.scene
    props = scene.bbt_hf
    props.macro_prompt = PROMPTS["basin"]
    props.macro_seed = SEED
    props.preset = PRESET
    props.target = "G5Panel"
    props.resolution = BAKE_SIZE
    props.terrain_size = TILE_M
    props.emit_maps = False

    comfy_jobs.max_tick_seconds(reset=True)
    t0 = time.perf_counter()
    result = bpy.ops.bob_blender_tools.terrain_generate_base()
    press = time.perf_counter() - t0
    check("Generate Base queued a job and returned at once", result == {"FINISHED"} and press < 0.5,
          f"{result}, {press * 1000:.1f} ms on the main thread")
    check("exactly one job is in flight", len(comfy_jobs.active()) == 1,
          f"{len(comfy_jobs.active())} active")

    ticks, deadline = 0, time.time() + 300
    while comfy_jobs.active() and time.time() < deadline:
        comfy_jobs.tick()
        ticks += 1
        time.sleep(0.01)
    comfy_jobs.tick()
    jobs = comfy_jobs.jobs()
    job = jobs[-1] if jobs else None
    check("the job finished without an error", job is not None and job.state == "done",
          f"state {getattr(job, 'state', 'none')}, {getattr(job, 'error', None)}")
    note("longest main-thread tick while it ran",
         f"{comfy_jobs.max_tick_seconds() * 1000:.2f} ms over {ticks} ticks")
    check("the tick stays under one frame at 60 Hz", comfy_jobs.max_tick_seconds() < 0.016,
          f"{comfy_jobs.max_tick_seconds() * 1000:.2f} ms")
    check("the press left a mask on the panel and switched it on",
          bool(props.macro_path) and os.path.exists(props.macro_path) and props.use_macro,
          f"{os.path.basename(props.macro_path or '')}, use_macro={props.use_macro}")
    if not props.macro_path:
        return

    t0 = time.perf_counter()
    result = bpy.ops.bob_blender_tools.bake_terrain()
    baked = time.perf_counter() - t0
    check("Bake + Build built a terrain from it", result == {"FINISHED"}
          and bpy.data.objects.get("G5Panel") is not None, f"{result} in {baked:.1f}s")
    side = os.path.splitext(props.macro_path)[0]
    heightmap = bpy.data.objects["G5Panel"]["bbt_heightmap"] if "G5Panel" in bpy.data.objects else ""
    recipe = hf.io.read_sidecar(heightmap) if heightmap else None
    check("the mask is the first op of the stack the bake actually ran",
          bool(recipe) and recipe["stack"][0]["kind"] == "macro"
          and recipe["stack"][0]["path"] == props.macro_path,
          f"stack {[op['kind'] for op in (recipe or {}).get('stack', [])]}")
    note("panel bake", f"{baked:.1f}s at {props.resolution}px, sidecar beside "
                       f"{os.path.basename(side)}.json")
    # And switching it off has to bake the preset exactly as before, or the feature is not optional.
    props.use_macro = False
    bpy.ops.bob_blender_tools.bake_terrain()
    recipe = hf.io.read_sidecar(bpy.data.objects["G5Panel"]["bbt_heightmap"])
    check("switching the mask off bakes the preset as it always did",
          recipe["stack"][0]["kind"] == "noise",
          f"stack {[op['kind'] for op in recipe['stack']]}")


def main():
    global PRESET
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--part", default="a,b,c,d,e")
    ap.add_argument("--fresh", action="store_true", help="regenerate cached masks")
    ap.add_argument("--preset", default=PRESET)
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    args = ap.parse_args(argv)
    parts = {p.strip() for p in args.part.split(",") if p.strip()}
    PRESET = args.preset
    os.makedirs(OUT, exist_ok=True)
    ok, detail = comfy.reachable()
    print(f"ComfyUI: {'reachable' if ok else 'not reachable'} ({detail})")
    print(f"preset {PRESET}, bake {BAKE_SIZE}px, seed {SEED}, tile {TILE_M:.0f} m")

    if "a" in parts:
        part_a(args)
    staged = part_b(args, ok) if "b" in parts else {}
    if "c" in parts:
        part_c(args, ok, staged.get("masks", {}))
    if "d" in parts:
        part_d(args, ok)
    if "e" in parts:
        part_e(args, ok)

    section("result")
    if FAILURES:
        print(f"{len(FAILURES)} failure(s): " + "; ".join(FAILURES))
    else:
        print("no failures")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
