"""Tests for the heightfields subpackage.

Run: uv run --with pytest --project tools pytest tools/tests -q

The CPU backend is the deterministic reference, so the golden check is
reproducibility (two CPU bakes are byte-identical) rather than a stored image.
GPU tests skip when no GPU is present.
"""

import numpy as np
import pytest

from bobtools import heightfields as hf
from bobtools.heightfields import backend, erode, generate, io


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


@pytest.mark.skipif(backend.select("auto").name != "gpu", reason="no GPU")
def test_gpu_bake_finite_and_similar(tmp_path):
    hf.bake(str(tmp_path / "cpu.png"), _small_params("cpu"), force=True)
    hf.bake(str(tmp_path / "gpu.png"), _small_params("gpu"), force=True)
    a = io.read_png16(str(tmp_path / "cpu.png"))
    b = io.read_png16(str(tmp_path / "gpu.png"))
    assert np.isfinite(b).all()
    corr = float(np.corrcoef(a.ravel(), b.ravel())[0, 1])
    assert corr > 0.6  # same structure, not bit-identical
