"""Tests for the heightfields subpackage.

Run: uv run --with pytest --project tools pytest tools/tests -q

The CPU backend is the deterministic reference, so the golden check is
reproducibility (two CPU bakes are byte-identical) rather than a stored image.
GPU tests skip when no GPU is present.
"""

import importlib.util
import json
import pathlib

import numpy as np
import pytest

from bobtools import heightfields as hf
from bobtools.heightfields import backend, erode, generate, io, params, pipeline, presets

DATA = pathlib.Path(__file__).parent / "data"
REPO_ROOT = pathlib.Path(__file__).parents[2]


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


def _small_params(backend_name):
    return {
        "size": 96, "seed": 3, "backend": backend_name,
        "passes": [
            {"kind": "hydraulic", "droplets": 6000, "max_steps": 32},
            {"kind": "thermal", "iterations": 2},
        ],
    }


def test_bake_cpu_reproducible(tmp_path):
    p = _small_params("cpu")
    r1 = hf.bake(str(tmp_path / "a.png"), p, force=True)
    r2 = hf.bake(str(tmp_path / "b.png"), p, force=True)
    a = io.read_png16(str(tmp_path / "a.png"))
    b = io.read_png16(str(tmp_path / "b.png"))
    assert np.array_equal(a, b)  # deterministic golden
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


def test_erosion_changes_field(tmp_path):
    base = generate.generate_base(96, seed=3)
    eroded = erode.run_passes(
        base, [{"kind": "hydraulic", "droplets": 6000, "max_steps": 32}],
        backend.select("cpu"), seed=3,
    )
    assert not np.allclose(base, eroded)
    assert np.isfinite(eroded).all()


def test_backend_cpu_always_available():
    assert "cpu" in backend.available()
    assert backend.select("cpu").name == "cpu"


# Shared params builder (build_params) and presets.

def test_build_params_structure():
    p = params.build_params({"droplets": 500_000, "thermal_iters": 3})
    kinds = [s["kind"] for s in p["passes"]]
    assert kinds == ["smooth", "hydraulic", "thermal", "smooth"]
    hyd = next(s for s in p["passes"] if s["kind"] == "hydraulic")
    assert hyd["density"] == 500_000  # density, not an absolute count yet
    assert "octaves" in p["generate"]


def test_build_params_optional_passes():
    p = params.build_params({"thermal_iters": 0, "base_smooth": 0,
                             "final_smooth": 0, "edge_falloff": 0.2})
    kinds = [s["kind"] for s in p["passes"]]
    assert kinds == ["falloff", "hydraulic"]  # no smooths, no thermal, falloff on


@pytest.mark.parametrize("name", presets.PRESETS)
def test_presets_expand(name):
    p = presets.get(name)
    assert p["passes"] and any(s["kind"] == "hydraulic" for s in p["passes"])
    assert set(("octaves", "ridged", "detail_strength")) <= set(p["generate"])


def test_panel_presets_json_in_sync():
    # The panel (a different interpreter) reads a committed presets.json generated
    # from PRESET_KNOBS. This fails if presets changed without regenerating it, so
    # the venv stays the single source of truth. Regenerate with
    # tools/scripts/gen_panel_presets.py.
    gen_path = REPO_ROOT / "tools" / "scripts" / "gen_panel_presets.py"
    spec = importlib.util.spec_from_file_location("gen_panel_presets", gen_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    committed = json.loads(mod.OUT.read_text())["presets"]
    assert committed == mod.build_panel_presets()


# Density scaling now lives in the pipeline, not the panel.

def test_density_scales_with_resolution():
    passes = [{"kind": "hydraulic", "density": 4_000_000}]
    at_ref = pipeline._scale_passes(passes, params.REFERENCE_SIZE)[0]["droplets"]
    at_half = pipeline._scale_passes(passes, params.REFERENCE_SIZE // 2)[0]["droplets"]
    assert at_ref == 4_000_000
    assert abs(at_half - 1_000_000) <= 1  # quarter the cells, quarter the droplets


def test_density_floor_and_absolute_untouched():
    scaled = pipeline._scale_passes([{"kind": "hydraulic", "density": 1000}], 32)
    assert scaled[0]["droplets"] == params.MIN_DROPLETS  # floored for tiny previews
    absolute = pipeline._scale_passes([{"kind": "hydraulic", "droplets": 7777}], 64)
    assert absolute[0]["droplets"] == 7777  # an explicit count is left alone


def test_preview_arg_sets_resolution(tmp_path):
    # Absolute droplets keep this fast; preview must override the size to 256.
    p = {"size": 512, "seed": 3,
         "passes": [{"kind": "hydraulic", "droplets": 3000, "max_steps": 20}]}
    meta = hf.bake(str(tmp_path / "pv.png"), p, force=True, preview=True)
    assert meta["size"] == params.PREVIEW_SIZE
    img = io.read_png16(str(tmp_path / "pv.png"))
    assert img.shape == (params.PREVIEW_SIZE, params.PREVIEW_SIZE)


# Edge behaviour.

def test_edge_falloff_sinks_borders():
    field = np.ones((80, 80))
    out = erode.edge_falloff(field, margin=0.2, power=2.0)
    assert out[0, :].max() < 0.05  # top row sunk
    assert out[:, 0].max() < 0.05  # left column sunk
    assert out[40, 40] == pytest.approx(1.0)  # centre untouched


def test_no_border_spike(tmp_path):
    # B1 regression: the reflected-margin crop must keep the rim from spiking.
    # Without the fix, droplets running off-grid dumped their load on the border,
    # so the edge stole the global max.
    p = {"size": 128, "seed": 4,
         "passes": [{"kind": "smooth", "sigma": 1.2},
                    {"kind": "hydraulic", "droplets": 60_000, "max_steps": 48, "radius": 3}]}
    hf.bake(str(tmp_path / "b.png"), p, force=True)
    h = io.read_png16(str(tmp_path / "b.png")).astype(np.float64)
    ring = np.concatenate([h[:3].ravel(), h[-3:].ravel(), h[:, :3].ravel(), h[:, -3:].ravel()])
    interior = h[16:-16, 16:-16]
    assert np.percentile(ring, 99) <= np.percentile(interior, 99)


def test_golden_small(tmp_path):
    # A committed golden guards the CPU pipeline against silent changes. Versions
    # are pinned by uv.lock, so the CPU bake is bit-reproducible. Regenerate with
    # tools/tests/data/make_golden.py if the algorithm intentionally changes.
    golden = np.load(DATA / "golden_hf.npy")
    hf.bake(str(tmp_path / "g.png"), _GOLDEN_PARAMS, force=True)
    got = io.read_png16(str(tmp_path / "g.png"))
    assert np.array_equal(got, golden)


_GOLDEN_PARAMS = {
    "size": 64, "seed": 5, "backend": "cpu",
    "passes": [
        {"kind": "smooth", "sigma": 1.0},
        {"kind": "hydraulic", "droplets": 3000, "max_steps": 24, "radius": 2},
        {"kind": "thermal", "iterations": 2},
        {"kind": "smooth", "sigma": 0.8},
    ],
}


@pytest.mark.skipif(backend.select("auto").name != "gpu", reason="no GPU")
def test_gpu_bake_finite_and_similar(tmp_path):
    hf.bake(str(tmp_path / "cpu.png"), _small_params("cpu"), force=True)
    hf.bake(str(tmp_path / "gpu.png"), _small_params("gpu"), force=True)
    a = io.read_png16(str(tmp_path / "cpu.png"))
    b = io.read_png16(str(tmp_path / "gpu.png"))
    assert np.isfinite(b).all()
    corr = float(np.corrcoef(a.ravel(), b.ravel())[0, 1])
    assert corr > 0.9  # batched fixed-point GPU tracks the sequential CPU reference closely


@pytest.mark.skipif(backend.select("auto").name != "gpu", reason="no GPU")
def test_gpu_bake_deterministic(tmp_path):
    # A2 regression: the fixed-point batched kernel makes a seeded GPU bake
    # bit-reproducible run-to-run (float atomicAdd on the surface was not).
    hf.bake(str(tmp_path / "g1.png"), _small_params("gpu"), force=True)
    hf.bake(str(tmp_path / "g2.png"), _small_params("gpu"), force=True)
    a = io.read_png16(str(tmp_path / "g1.png"))
    b = io.read_png16(str(tmp_path / "g2.png"))
    assert np.array_equal(a, b)
