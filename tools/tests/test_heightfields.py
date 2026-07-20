"""Tests for the heightfields subpackage.

Run: uv run --with pytest --extra all --extra gpu --project tools pytest tools/tests -q

The terrain recipe is a composable op stack (engine.run_stack): generators write a
base, filters and flow-accumulation erosion shape it, selectors mask where a filter
acts. The CPU backend is the deterministic reference (pure stencils, so a seeded run
is bit-reproducible and backs the golden); the GPU path runs the same ops and, being
stencils too, is deterministic run-to-run and byte-identical to CPU at these sizes.
GPU tests skip when no GPU is present.
"""

import importlib.util
import json
import pathlib

import numpy as np
import pytest
from scipy.ndimage import uniform_filter, zoom

from bobtools import heightfields as hf
from bobtools.heightfields import (
    backend, engine, erode, generate, io, params, pipeline, presets,
)

DATA = pathlib.Path(__file__).parent / "data"
REPO_ROOT = pathlib.Path(__file__).parents[2]


# Generation (unchanged: resolution-independent world-sampled noise).

def test_generate_deterministic():
    a = generate.generate_base(96, seed=5)
    b = generate.generate_base(96, seed=5)
    assert np.array_equal(a, b)
    assert a.shape == (96, 96)
    assert 0.0 <= a.min() and a.max() <= 1.0


def test_generate_seed_changes_field():
    a = generate.generate_base(96, seed=1)
    b = generate.generate_base(96, seed=2)
    assert not np.array_equal(a, b)


# A small stack params dict, used by the bake tests. Explicit stack so it does not
# drift when a preset is retuned; fast (small fill/acc counts at this size).
def _small_params(backend_name, size=96, seed=3):
    return {
        "size": size, "seed": seed, "backend": backend_name,
        "stack": [
            {"kind": "noise", "ridged": 0.5, "detail_strength": 0.6, "octaves": 4,
             "warp": 50, "seed": seed},
            {"kind": "fluvial", "iterations": 25, "k": 0.02, "diffusion": 0.05,
             "recompute": 25, "fill_iters": 200, "acc_iters": 200, "thermal_iters": 1,
             "talus": 0.004, "max_delta": 0.03},
            {"kind": "thermal", "talus": 0.01, "factor": 0.5, "iterations": 2},
        ],
    }


def test_bake_cpu_reproducible(tmp_path):
    p = _small_params("cpu")
    r1 = hf.bake(str(tmp_path / "a.png"), p, force=True)
    r2 = hf.bake(str(tmp_path / "b.png"), p, force=True)
    a = io.read_png16(str(tmp_path / "a.png"))
    b = io.read_png16(str(tmp_path / "b.png"))
    assert np.array_equal(a, b)  # deterministic
    assert r1["hash"] == r2["hash"]


def test_bake_range_and_finite(tmp_path):
    hf.bake(str(tmp_path / "h.png"), _small_params("cpu"), force=True)
    h = io.read_png16(str(tmp_path / "h.png"))
    assert np.isfinite(h).all()
    assert h.min() >= 0.0 and h.max() <= 1.0
    assert h.max() > 0.5  # normalised, so something reaches the top


def test_cache_hit(tmp_path):
    p = _small_params("cpu")
    path = str(tmp_path / "c.png")
    first = hf.bake(path, p)
    second = hf.bake(path, p)
    assert first["cached"] is False
    assert second["cached"] is True


def test_preview_arg_sets_resolution(tmp_path):
    p = _small_params("cpu", size=512)
    meta = hf.bake(str(tmp_path / "pv.png"), p, force=True, preview=True)
    assert meta["size"] == params.PREVIEW_SIZE
    img = io.read_png16(str(tmp_path / "pv.png"))
    assert img.shape == (params.PREVIEW_SIZE, params.PREVIEW_SIZE)


# Engine: per-op smoke, masking, determinism.

def test_engine_generators_and_ops_smoke():
    bk = backend.select("cpu")
    z = np.zeros((80, 80))
    # voronoi defaults to a multiply mix (it modulates an existing field, as in the
    # mesa stack); on a bare zero base test it in a writing mix so it produces output.
    for op in ({"kind": "noise"}, {"kind": "dunes"},
               {"kind": "voronoi", "pattern": "mesa", "mix": "max"},
               {"kind": "voronoi", "pattern": "crack", "mix": "max"}):
        out = engine.run_stack(z, [op], bk, seed=1)
        assert out.shape == (80, 80) and np.isfinite(out).all()
        assert out.max() > out.min()  # a generator wrote something


def test_engine_per_op_mask_limits_change():
    # A height-masked filter must change ONLY where the selector is non-zero.
    bk = backend.select("cpu")
    base = [{"kind": "noise", "ridged": 0.4, "seed": 2}]
    field = engine.run_stack(np.zeros((96, 96)), base, bk, seed=2)
    masked = engine.run_stack(
        np.zeros((96, 96)),
        base + [{"kind": "terrace", "steps": 6, "sharpness": 0.9,
                 "mask": {"kind": "height", "low": 0.6, "high": 1.0, "falloff": 0.05}}],
        bk, seed=2,
    )
    changed = np.abs(masked - field) > 1e-4
    # low ground (field < ~0.5) must be essentially untouched by the high-band mask
    assert changed[field < 0.4].mean() < 0.02
    assert changed[field > 0.7].mean() > 0.1  # high ground was terraced


def test_engine_unknown_op_raises():
    with pytest.raises(ValueError):
        engine.run_stack(np.zeros((16, 16)), [{"kind": "nope"}], backend.select("cpu"))


def test_erosion_changes_field():
    bk = backend.select("cpu")
    base = generate.generate_base(96, seed=3)
    eroded = engine.run_stack(
        base, [{"kind": "fluvial", "iterations": 20, "recompute": 20,
                "fill_iters": 200, "acc_iters": 200}], bk, seed=3)
    assert not np.allclose(base, eroded)
    assert np.isfinite(eroded).all()


def test_backend_cpu_always_available():
    assert "cpu" in backend.available()
    assert backend.select("cpu").name == "cpu"


# Params builder (build_params) and presets, now stack-based.

def test_build_params_structure():
    p = params.build_params({"preset": "canyon", "seed": 5})
    assert set(("size", "seed", "backend", "preset", "stack", "globals")) <= set(p)
    kinds = [op["kind"] for op in p["stack"]]
    assert kinds[0] in ("noise", "dunes", "voronoi")     # a generator first
    assert any(k in ("fluvial", "pipe_hydraulic", "thermal") for k in kinds)


def test_neutral_knobs_reproduce_preset():
    # All knobs at 0.5 must equal the authored stack (only the seed is injected).
    authored = presets.stack("alpine")
    neutral = params.resolve_stack("alpine", seed=7)
    for a, n in zip(authored, neutral):
        assert a["kind"] == n["kind"]
        for key, val in a.items():
            if key in ("seed", "mask"):
                continue
            assert n[key] == pytest.approx(val), (a["kind"], key)


def test_erosion_knob_scales_incision():
    lo = params.resolve_stack("canyon", erosion=0.0, seed=5)
    hi = params.resolve_stack("canyon", erosion=1.0, seed=5)
    fi = lambda s: next(o for o in s if o["kind"] == "fluvial")["iterations"]
    assert fi(hi) > fi(lo)


def test_seed_knob_varies_generators():
    a = params.resolve_stack("canyon", seed=1)[0]["seed"]
    b = params.resolve_stack("canyon", seed=2)[0]["seed"]
    assert a != b


@pytest.mark.parametrize("name", presets.PRESETS)
def test_presets_expand(name):
    p = presets.get(name)
    kinds = [op["kind"] for op in p["stack"]]
    assert kinds[0] in ("noise", "dunes", "voronoi")   # a generator establishes the base
    assert len(kinds) >= 2                              # plus at least one shaping op
    assert presets.display(name).keys() >= {"height", "sea_level"}


def test_panel_presets_json_in_sync():
    # The panel (a different interpreter) reads a committed presets.json generated
    # from the venv presets. This fails if presets changed without regenerating it.
    # Regenerate with tools/scripts/gen_panel_presets.py.
    gen_path = REPO_ROOT / "tools" / "scripts" / "gen_panel_presets.py"
    spec = importlib.util.spec_from_file_location("gen_panel_presets", gen_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    committed = json.loads(mod.OUT.read_text())
    assert committed["presets"] == mod.build_panel_presets()
    assert committed["stacks"] == mod.build_panel_stacks()  # P4 stack editor source


# Resolution independence and canyon structure (the two headline behaviours).

def test_resolution_independence_through_erosion():
    # A preview and a full bake must be the SAME landform (the old failure was
    # corr -0.56). Uses a light preset so it runs quickly on CPU.
    bk = backend.select("auto")
    def bake(N):
        return engine.run_stack(np.zeros((N, N)), params.resolve_stack("hills", seed=5),
                                bk, seed=5)
    lo, hi = bake(96), bake(288)
    hi_ds = zoom(hi, 96 / 288, order=1)[:96, :96]
    corr = float(np.corrcoef(lo.ravel(), hi_ds.ravel())[0, 1])
    assert corr >= 0.9, corr


def test_canyon_has_incised_channels():
    # The canyon preset must carve a dendritic channel network; a flat preset must
    # not. Channel fraction = cells markedly below their local mean.
    bk = backend.select("auto")
    def channel_pct(name):
        h = engine.run_stack(np.zeros((192, 192)), params.resolve_stack(name, seed=5),
                             bk, seed=5)
        return (h < uniform_filter(h, 21) - 0.05).mean() * 100
    canyon, plains = channel_pct("canyon"), channel_pct("plains")
    assert canyon > 3.0, canyon          # a real network forms
    assert canyon > plains + 2.0         # far more incised than flat ground


# Edge behaviour.

def test_edge_falloff_sinks_borders():
    field = np.ones((80, 80))
    out = erode.edge_falloff(field, margin=0.2, power=2.0)
    assert out[0, :].max() < 0.05  # top row sunk
    assert out[:, 0].max() < 0.05  # left column sunk
    assert out[40, 40] == pytest.approx(1.0)  # centre untouched


def test_no_border_rim(tmp_path):
    # The edge-aware stencil erosion drains borders out (they are outlets), so the
    # rim must not stick up above the interior. No reflected margin is used now.
    p = _small_params("cpu", size=160)
    hf.bake(str(tmp_path / "b.png"), p, force=True)
    h = io.read_png16(str(tmp_path / "b.png")).astype(np.float64)
    ring = np.concatenate([h[:3].ravel(), h[-3:].ravel(), h[:, :3].ravel(), h[:, -3:].ravel()])
    interior = h[16:-16, 16:-16]
    assert np.percentile(ring, 99) <= np.percentile(interior, 99) + 1e-6


def test_golden_small(tmp_path):
    # A committed golden guards the CPU op stack against silent math changes.
    # Regenerate with tools/tests/data/make_golden.py if the algorithm changes.
    golden = np.load(DATA / "golden_hf.npy")
    hf.bake(str(tmp_path / "g.png"), _GOLDEN_PARAMS, force=True)
    got = io.read_png16(str(tmp_path / "g.png"))
    assert np.array_equal(got, golden)


_GOLDEN_PARAMS = {
    "size": 64, "seed": 5, "backend": "cpu",
    "stack": [
        {"kind": "noise", "ridged": 0.5, "detail_strength": 0.6, "octaves": 4,
         "warp": 50, "seed": 5},
        {"kind": "fluvial", "iterations": 20, "k": 0.02, "sp_m": 0.5, "sp_n": 1.0,
         "diffusion": 0.05, "talus": 0.004, "thermal_iters": 1, "recompute": 20,
         "fill_iters": 120, "acc_iters": 120, "max_delta": 0.03},
        {"kind": "thermal", "talus": 0.01, "factor": 0.5, "iterations": 2},
    ],
}


@pytest.mark.skipif(backend.select("auto").name != "gpu", reason="no GPU")
def test_gpu_matches_cpu(tmp_path):
    # The stack is pure stencils, so the CuPy float64 path matches the numpy
    # reference very closely (byte-identical at small sizes).
    hf.bake(str(tmp_path / "cpu.png"), _small_params("cpu"), force=True)
    hf.bake(str(tmp_path / "gpu.png"), _small_params("gpu"), force=True)
    a = io.read_png16(str(tmp_path / "cpu.png"))
    b = io.read_png16(str(tmp_path / "gpu.png"))
    assert np.isfinite(b).all()
    corr = float(np.corrcoef(a.ravel(), b.ravel())[0, 1])
    assert corr > 0.99


@pytest.mark.skipif(backend.select("auto").name != "gpu", reason="no GPU")
def test_gpu_bake_deterministic(tmp_path):
    # Pure stencils (no atomics, no random scatter) make a seeded GPU bake
    # bit-reproducible run-to-run, so the sidecar and cache are safe.
    hf.bake(str(tmp_path / "g1.png"), _small_params("gpu"), force=True)
    hf.bake(str(tmp_path / "g2.png"), _small_params("gpu"), force=True)
    a = io.read_png16(str(tmp_path / "g1.png"))
    b = io.read_png16(str(tmp_path / "g2.png"))
    assert np.array_equal(a, b)
