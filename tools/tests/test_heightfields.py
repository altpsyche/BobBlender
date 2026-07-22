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
from scipy.ndimage import zoom

from bobtools import heightfields as hf
from bobtools.heightfields import (
    backend, engine, erode, generate, io, maps, ops_erode, params, pipeline, presets,
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


def _diag_river_curve(n=128):
    """A simple diagonal river polyline in terrain UV, plus a base with relief to erode."""
    u = np.linspace(0, 1, n)
    x, y = np.meshgrid(u, u)
    base = np.clip(0.3 + 0.4 * x + 0.06 * np.sin(y * 8.0), 0.0, 1.0)
    vs = np.linspace(0.05, 0.95, 40)
    us = 0.5 + 0.12 * np.sin(vs * 6.283)
    curves = [{"points": [[float(a), float(b)] for a, b in zip(us, vs)]}]
    return base, curves


def test_channel_seed_lowers_only_the_band():
    # The seed carve must lower height along the spline and leave the rest of the field alone.
    bk = backend.select("cpu")
    base, curves = _diag_river_curve()
    seeded = engine.run_stack(
        base, [{"kind": "channel_seed", "curves": curves, "width": 0.006,
                "falloff": 0.02, "depth": 0.04}], bk, normalize=False)
    drop = base - seeded
    assert drop.max() > 0.02          # the band was lowered
    assert (drop > 1e-4).mean() < 0.2  # but only a thin corridor, not the whole map
    assert np.isfinite(seeded).all()


def test_flow_prior_concentrates_incision_on_the_path():
    # The drainage prior must make fluvial incise MORE along the authored spline than a run
    # with no prior -- the spline is a drainage prior, not a cosmetic carve.
    bk = backend.select("cpu")
    base, curves = _diag_river_curve()
    from bobtools.heightfields import ops_carve, ops_erode
    xp = bk.xp
    dist = ops_carve._distance_uv(base.shape, curves, xp, ops_erode._ndimage(xp))
    onpath = bk.asnumpy(ops_carve._profile(dist, 0.008, 0.02, xp)) > 0.5
    seed = {"kind": "channel_seed", "curves": curves, "width": 0.006, "falloff": 0.02, "depth": 0.02}
    common = dict(iterations=40, k=6e-4, diffusion=0.08, recompute=20,
                  fill_iters=200, acc_iters=200)
    no_prior = engine.run_stack(base, [seed, {"kind": "fluvial", **common}], bk, normalize=False)
    band = {"curves": curves, "width": 0.008, "falloff": 0.03, "gain": 8000.0}
    with_prior = engine.run_stack(
        base, [seed, {"kind": "fluvial", "flow_prior": band, **common}], bk, normalize=False)
    incised_no = (base - no_prior)[onpath].mean()
    incised_yes = (base - with_prior)[onpath].mean()
    assert incised_yes > incised_no * 1.3   # the prior deepens the authored channel
    assert np.isfinite(with_prior).all()


def test_talus_warp_makes_bank_angle_nonuniform():
    # A warped repose angle must produce a spatially varying result vs the constant-talus run,
    # so valley walls stop reading as one uniform ruled slope.
    bk = backend.select("cpu")
    base = generate.generate_base(128, seed=4)
    plain = engine.run_stack(
        base, [{"kind": "thermal", "talus": 0.01, "iterations": 8}], bk, normalize=False)
    warped = engine.run_stack(
        base, [{"kind": "thermal", "talus": 0.01, "iterations": 8,
                "talus_warp": 0.6, "talus_freq": 5.0}], bk, normalize=False)
    assert not np.allclose(plain, warped)   # the warp actually changed the slump
    assert np.isfinite(warped).all()


def test_deposit_adds_material_in_channels():
    # Deposition must RAISE the bed (add sediment), unlike incision, and concentrate in the wet
    # channel: a run with a seeded groove must gain more mass on-path than off-path.
    bk = backend.select("cpu")
    base, curves = _diag_river_curve()
    from bobtools.heightfields import ops_carve, ops_erode
    xp = bk.xp
    dist = ops_carve._distance_uv(base.shape, curves, xp, ops_erode._ndimage(xp))
    onpath = bk.asnumpy(ops_carve._profile(dist, 0.01, 0.03, xp)) > 0.5
    seed = {"kind": "channel_seed", "curves": curves, "width": 0.006, "falloff": 0.02, "depth": 0.03}
    seeded = engine.run_stack(base, [seed], bk, normalize=False)
    filled = engine.run_stack(
        base, [seed, {"kind": "deposit", "amount": 0.02, "iterations": 4,
                      "fill_iters": 200, "acc_iters": 200}], bk, normalize=False)
    gain = filled - seeded
    assert gain.sum() > 0.0                       # net material was added
    assert gain[onpath].mean() > gain[~onpath].mean()  # alluviation favours the wet channel
    assert np.isfinite(filled).all()


def test_deposit_deterministic():
    # Same input + params -> byte-identical output (pure stencils, backs a reproducible bake).
    bk = backend.select("cpu")
    base = generate.generate_base(96, seed=7)
    op = [{"kind": "deposit", "amount": 0.015, "iterations": 3, "fill_iters": 150, "acc_iters": 150}]
    a = engine.run_stack(base, op, bk, normalize=False)
    b = engine.run_stack(base, op, bk, normalize=False)
    assert np.array_equal(a, b)


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
    p = params.build_params({"preset": "alpine", "seed": 5})
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
    lo = params.resolve_stack("alpine", erosion=0.0, seed=5)
    hi = params.resolve_stack("alpine", erosion=1.0, seed=5)
    fi = lambda s: next(o for o in s if o["kind"] == "fluvial")["iterations"]
    assert fi(hi) > fi(lo)


def test_seed_knob_varies_generators():
    a = params.resolve_stack("alpine", seed=1)[0]["seed"]
    b = params.resolve_stack("alpine", seed=2)[0]["seed"]
    assert a != b


def test_mesa_reads_as_tableland():
    # Mesa's landform signature: a large near-flat CAP fraction sitting above steep CLIFFS -- distinct
    # from mountains (steep but not flat-capped) and hills (gentle, no cliffs). This gates the mesa
    # generator (strata + scarp); it is a diagnostic, backed by a 3D render in review, never a stat
    # asserted on its own.
    bk = backend.select("auto")
    def flat_cliff(name):
        # size=256 keeps this a MACRO diagnostic (amplify.to == run resolution, so the cascade
        # no-ops): the mesa signature is a property of the strata+scarp generator, not the detail pass.
        h = engine.run_stack(np.zeros((256, 256)), params.resolve_stack(name, seed=5, size=256), bk, seed=5)
        gy, gx = np.gradient(h)
        s = np.hypot(gx, gy)
        return (s < 0.0015).mean(), (s > 0.02).mean()
    m_flat, m_cliff = flat_cliff("mesa")
    a_flat, a_cliff = flat_cliff("alpine")
    assert m_flat > 0.5, m_flat            # broad flat caps/benches dominate
    assert m_flat > 5.0 * a_flat           # far flatter-capped than graded mountains
    assert m_cliff > a_cliff               # yet has steeper cliff faces than the mountains


def test_canyon_incises_a_plateau():
    # Canyon's landform signature: flat plateau RIMS (unlike mountains/hills) that are DEEPLY INCISED
    # below the rim by confined channels (unlike a solid mesa cap). Gates the canyon generator
    # (strata plateau + fluvial hero); a diagnostic, render-verified, not a stat on its own.
    bk = backend.select("auto")
    def flat_deep(name):
        # size=256 keeps this a MACRO diagnostic (the amplify cascade no-ops at the run resolution).
        h = engine.run_stack(np.zeros((256, 256)), params.resolve_stack(name, seed=5, size=256), bk, seed=5)
        gy, gx = np.gradient(h)
        flat = (np.hypot(gx, gy) < 0.0015).mean()
        deep = (h < np.percentile(h, 85) - 0.25).mean()   # floor well below the rim level
        return flat, deep
    c_flat, c_deep = flat_deep("canyon")
    m_flat, m_deep = flat_deep("mesa")
    a_flat, _ = flat_deep("alpine")
    assert c_flat > 0.2, c_flat            # real flat rims (mountains/hills sit near 0.02)
    assert c_flat > 5.0 * a_flat           # far more rim than graded mountains
    assert c_deep > 0.6, c_deep            # deeply incised below the rim
    assert c_deep > m_deep                 # cuts deeper below its rim than a mesa cap


# --- Amplification (Schott et al. 2024 multi-scale erosion): the amplify op and its two modes. ---

def _amp_macro(n=96, seed=0):
    """A synthetic coarse macro: a broad drainable slope with roughness plus a genuinely flat cap, so
    the amplify tests can check flats survive, detail is added, and only the fluvial mode channelises."""
    rng = np.random.default_rng(seed)
    _, xx = np.mgrid[0:n, 0:n]
    h = 0.7 - 0.5 * (xx / n) + 0.02 * rng.standard_normal((n, n))
    h[:, : n // 3] = 0.7                       # a flat cap, zero slope
    return np.clip(h, 0.0, None)


def _flat_frac(a, thr=0.002):
    gy, gx = np.gradient(a)
    return float((np.hypot(gx, gy) < thr).mean())


def _detail_std(a):
    lap = a[2:, 1:-1] + a[:-2, 1:-1] + a[1:-1, 2:] + a[1:-1, :-2] - 4 * a[1:-1, 1:-1]
    return float(lap.std())


def _nrm(a):
    a = a - a.min()
    return a / max(a.max(), 1e-9)


def test_amplify_preview_is_prefix_of_final():
    # The point of the cascade: a lower-resolution bake is a faithful low-detail PREFIX of a higher
    # one (preview == final), because each doubling level builds on the previous. Render-verified.
    bk = backend.select("auto"); xp = bk.xp
    h = xp.asarray(_amp_macro(64, seed=2))
    kw = dict(mode="fluvial", strength=0.03, iterations=12, seed=1, relief=0.2)
    prev = _nrm(bk.asnumpy(ops_erode.amplify(h, xp, to=128, **kw)))
    fin = _nrm(bk.asnumpy(ops_erode.amplify(h, xp, to=256, **kw)))
    down = _nrm(zoom(fin, 128 / 256, order=1, mode="nearest"))
    assert float(np.sqrt(np.mean((down - prev) ** 2))) < 0.05


def test_amplify_fluvial_preserves_flats_and_adds_detail():
    # Fluvial amplify ADDS fine drainage detail on slopes while leaving flat caps flat: a preserve-
    # and-incise pass, not a smoother or a regenerator. Render-verified on mesa and canyon.
    bk = backend.select("auto"); xp = bk.xp
    macro = _amp_macro(96, seed=3)
    up = zoom(macro, 2, order=1, mode="nearest")
    out = bk.asnumpy(ops_erode.amplify(xp.asarray(macro), xp, mode="fluvial", to=192,
                                       strength=0.03, iterations=16, seed=1, relief=0.2))
    assert _flat_frac(out) > 0.9 * _flat_frac(up)          # the flat cap survives
    assert _detail_std(out) > 2.0 * _detail_std(up)        # fine detail is added


def test_amplify_aeolian_does_not_channelise():
    # Aeolian amplify (dunes) must NOT carve drainage channels the way fluvial does -- sand has no
    # rivers. On the same drainable slope, fluvial incises far more below the macro surface than
    # aeolian. Render-verified: fluvial scarred the dune slip faces, aeolian did not.
    bk = backend.select("auto"); xp = bk.xp
    macro = _amp_macro(96, seed=4)
    up = _nrm(zoom(macro, 2, order=1, mode="nearest"))
    def scar(mode, it):
        o = _nrm(bk.asnumpy(ops_erode.amplify(xp.asarray(macro), xp, mode=mode, to=192, strength=0.02,
                                              iterations=it, seed=1, relief=0.2, wind=30)))
        return float((o < up - 0.05).mean())
    assert scar("fluvial", 30) > 2.0 * scar("aeolian", 2)


def test_amplify_wiring_macro_and_preview_sizes():
    # A preset that amplifies runs its macro at AMPLIFY_BASE and previews at AMPLIFY_PREVIEW (a real
    # climb level, so the preview is a prefix of the full bake); a preset that does not keeps its
    # whole stack at the bake size and previews at PREVIEW_SIZE.
    amp_stack = params.build_params({"preset": "alpine", "size": 768})["stack"]
    assert params.has_amplify(amp_stack)
    assert params.macro_size(amp_stack, 768) == params.AMPLIFY_BASE
    assert pipeline._preview_size({"preset": "alpine"}) == params.AMPLIFY_PREVIEW
    # a stack without amplify runs whole at the bake size and previews at PREVIEW_SIZE (every preset
    # amplifies now, so the negative case is an explicit non-amplify stack, e.g. a carve-then-erode).
    plain = [{"kind": "noise"}, {"kind": "fluvial", "iterations": 10}]
    assert not params.has_amplify(plain)
    assert params.macro_size(plain, 768) == 768
    assert pipeline._preview_size({"stack": plain}) == params.PREVIEW_SIZE


def test_talus_for_angle_inverts_slope():
    # talus_for_angle must be the inverse of the render slope model tan(theta)=talus*ratio*res, so a
    # requested repose angle round-trips to the talus that produces it.
    for ang in (30.0, 34.0, 45.0):
        for res, ratio in ((256, 0.012), (768, 0.019)):
            t = presets.talus_for_angle(ang, res, ratio)
            assert np.degrees(np.arctan(t * ratio * res)) == pytest.approx(ang, abs=1e-6)


def _face_angle_topdec(name, res):
    # Real slope in degrees of the steep faces (top decile) of a baked preset, via the same render
    # model: tan(theta) = per-cell normalised rise * relief_ratio * bake_res.
    bk = backend.select("auto")
    p = params.build_params({"preset": name, "size": res, "seed": 7})
    h = engine.run_stack(np.zeros((res, res)), p["stack"], bk, seed=p["seed"])
    gy, gx = np.gradient(h)
    ang = np.degrees(np.arctan(np.hypot(gx, gy) * presets.relief(name) * res))
    return ang[ang > np.percentile(ang, 90)].mean()


def test_repose_clamp_is_resolution_consistent():
    # The repose clamp's whole point: a dune slip face must render at the SAME physical angle at
    # preview and full bake resolutions. A fixed talus (the old behaviour) clipped at a different
    # angle per resolution; repose_deg -> talus_for_angle removes that. Diagnostic, render-verified.
    for name in ("dunes", "sand_sea"):
        lo = _face_angle_topdec(name, 256)
        hi = _face_angle_topdec(name, 768)
        assert abs(lo - hi) < 2.0, (name, lo, hi)   # was ~5 deg apart with a fixed talus


@pytest.mark.parametrize("name", presets.PRESETS)
def test_presets_expand(name):
    p = presets.get(name)
    kinds = [op["kind"] for op in p["stack"]]
    assert kinds[0] in ("noise", "dunes", "voronoi", "strata")   # a generator establishes the base
    assert len(kinds) >= 2                              # plus at least one shaping op
    assert presets.display(name).keys() >= {"relief", "sea_level"}
    assert presets.height_for(name, 512.0) > 0.0   # real-world metre relief derives cleanly


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


# Resolution independence and channel structure (the two headline behaviours).

def test_resolution_independence_through_erosion():
    # A preview and a full bake must be the SAME landform (the old failure was
    # corr -0.56). Uses a light preset so it runs quickly on CPU.
    bk = backend.select("auto")
    def bake(N):
        # size=N pins amplify.to to the run resolution so the cascade no-ops: this asserts the MACRO
        # is resolution-independent (the property the amplify cascade relies on to register a preview
        # against a full bake). preview==final of the cascade itself is test_amplify_preview_*.
        return engine.run_stack(np.zeros((N, N)), params.resolve_stack("hills", seed=5, size=N),
                                bk, seed=5)
    lo, hi = bake(96), bake(288)
    hi_ds = zoom(hi, 96 / 288, order=1)[:96, :96]
    corr = float(np.corrcoef(lo.ravel(), hi_ds.ravel())[0, 1])
    assert corr >= 0.9, corr


# (Fluvial channel formation is asserted by test_flow_map_concentrates below, which
# measures drainage concentration directly. The old below-local-mean channel-fraction
# test was tuned to the deep-incising canyon preset; that preset was removed with the
# Canyons family in 2026-07, and the gentler mountain presets do not separate cleanly on
# that proxy. See docs/TERRAIN-CRITIQUE.md on why depth proxies mislead.)


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


# Auxiliary flow/wetness maps (P5).

def test_flow_map_concentrates():
    # Flow accumulation must concentrate into channels: the top percentile far above
    # the median, and the map bounded to [0, 1] and finite.
    bk = backend.select("auto")
    h = engine.run_stack(np.zeros((160, 160)), params.resolve_stack("alpine", seed=5),
                         bk, seed=5)
    m = maps.derive_maps(h, bk)
    flow, wet = m["flow"], m["wetness"]
    for a in (flow, wet):
        assert np.isfinite(a).all() and a.min() >= 0.0 and a.max() <= 1.0
    assert np.percentile(flow, 99) > np.percentile(flow, 50) + 0.2  # channels stand out


def test_bake_emits_maps(tmp_path):
    p = _small_params("cpu")
    p["maps"] = True
    out = str(tmp_path / "m.png")
    meta = hf.bake(out, p, force=True)
    assert set(meta["maps"]) == {"flow", "wetness"}
    for path in meta["maps"].values():
        assert pathlib.Path(path).exists()
        assert io.read_png16(path).shape == io.read_png16(out).shape
    # default bake writes no maps
    meta2 = hf.bake(str(tmp_path / "n.png"), _small_params("cpu"), force=True)
    assert meta2["maps"] == {}


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
