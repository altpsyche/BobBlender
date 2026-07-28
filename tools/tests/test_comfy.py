"""ComfyUI client and map-derivation tests (the first spike, docs/GENERATION.md).

Both modules are bpy-free, so they run in the venv directly, imported by path (inserting the core
dir) to avoid the extension package's bpy-importing __init__ -- the same shape test_assets.py and
test_heightfields.py already use.

No server is contacted. The client is exercised against a stdlib `http.server` fake, which is
what the plan's Testing section asks for and what keeps the queue / jobs-API / `/view` shapes
honest without a 7 GB checkpoint in CI.
"""

import importlib
import json
import os
import pathlib
import struct
import sys
import threading
import time
import zlib
from http.server import BaseHTTPRequestHandler, HTTPServer

import numpy as np
import pytest

CORE = (pathlib.Path(__file__).resolve().parents[2] / "blender" / "extensions"
        / "bob_blender_tools" / "core")
WORKFLOWS = CORE.parent / "assets" / "workflows"


@pytest.fixture(scope="module")
def mods():
    sys.path.insert(0, str(CORE))
    return importlib.import_module("comfy"), importlib.import_module("comfy_maps")


@pytest.fixture(scope="module")
def jobs(mods):
    return importlib.import_module("comfy_jobs")


# -- comfy_maps: the PNG codec and the derivations -------------------------------------------
def _paeth_png(rgb):
    """Encode with filter 4 on every row, which is what PIL (and therefore ComfyUI) emits. The
    round-trip through Bob's own writer only ever exercises filter 0, so the decoder's real
    input needs building explicitly."""
    height, width, channels = rgb.shape
    stride = width * channels
    raw = bytearray()
    prev = bytes(stride)
    for y in range(height):
        line = rgb[y].reshape(stride)
        row = bytearray(stride)
        for i in range(stride):
            # Plain ints throughout: under NEP 50 a python int minus a numpy uint8 stays uint8
            # and a negative difference raises OverflowError.
            left = int(line[i - channels]) if i >= channels else 0
            above = int(prev[i])
            upleft = int(prev[i - channels]) if i >= channels else 0
            p = left + above - upleft
            pa, pb, pc = abs(p - left), abs(p - above), abs(p - upleft)
            pred = left if (pa <= pb and pa <= pc) else (above if pb <= pc else upleft)
            row[i] = (int(line[i]) - pred) & 0xFF
        raw += b"\x04" + row
        prev = bytes(line)

    def chunk(kind, body):
        return (struct.pack(">I", len(body)) + kind + body
                + struct.pack(">I", zlib.crc32(kind + body) & 0xFFFFFFFF))

    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(bytes(raw)))
            + chunk(b"IEND", b""))


@pytest.fixture
def noise_rgb():
    rng = np.random.default_rng(7)
    return rng.integers(0, 256, (24, 32, 3), dtype=np.uint8)


def test_png_round_trip_rgb_and_grey(mods, tmp_path, noise_rgb):
    _, maps = mods
    maps.write_png(tmp_path / "rgb.png", noise_rgb)
    assert np.array_equal(maps.read_png((tmp_path / "rgb.png").read_bytes()), noise_rgb)
    grey = noise_rgb[:, :, 0]
    maps.write_png(tmp_path / "grey.png", grey)
    back = maps.read_png((tmp_path / "grey.png").read_bytes())
    assert back.shape == grey.shape and np.array_equal(back, grey)


def test_png_decodes_paeth_filtered_rows(mods, noise_rgb):
    """The filter ComfyUI actually writes, on random data so no filter can be lucky."""
    _, maps = mods
    assert np.array_equal(maps.read_png(_paeth_png(noise_rgb)), noise_rgb)


def test_png_rejects_16_bit_and_interlaced(mods):
    _, maps = mods

    def chunk(kind, body):
        return (struct.pack(">I", len(body)) + kind + body
                + struct.pack(">I", zlib.crc32(kind + body) & 0xFFFFFFFF))

    deep = (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 4, 4, 16, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(b"\x00" * 100)) + chunk(b"IEND", b""))
    with pytest.raises(ValueError, match="unsupported PNG"):
        maps.read_png(deep)


def test_derive_gives_every_role_the_set_contract_names(mods, noise_rgb):
    _, maps = mods
    out = maps.derive(noise_rgb)
    assert sorted(out) == ["ao", "basecolor", "height", "normal", "roughness"]
    assert out["basecolor"].shape == out["normal"].shape == noise_rgb.shape
    for role in ("roughness", "height", "ao"):
        assert out[role].shape == noise_rgb.shape[:2]
    assert all(v.dtype == np.uint8 for v in out.values())


def test_roughness_inverts_luminance_within_the_band(mods):
    _, maps = mods
    ramp = np.tile(np.linspace(0, 255, 64, dtype=np.uint8)[None, :, None], (8, 1, 3))
    rough = maps.roughness_from(ramp)
    assert rough[0, 0] > rough[0, -1], "a dark albedo must read rougher than a bright one"
    lo, hi = maps.ROUGHNESS_RANGE
    assert rough.min() >= int(lo * 255) - 1 and rough.max() <= int(hi * 255) + 1


def test_height_is_a_wrap_safe_high_pass(mods):
    """A constant image has no relief, and a gradient's low frequency must not survive: a height
    that tracks overall brightness tilts the whole ground."""
    _, maps = mods
    flat = np.full((64, 64, 3), 90, dtype=np.uint8)
    assert abs(int(maps.height_from(flat).mean()) - 128) <= 1
    assert np.ptp(maps.height_from(flat)) <= 1
    ramp = np.tile(np.linspace(40, 210, 64, dtype=np.uint8)[None, :, None], (64, 1, 3))
    h = maps.height_from(ramp)
    # A low-frequency ramp survives only at the wrap discontinuity, so the interior stays level.
    assert abs(int(h[:, 16:48].mean()) - 128) <= 6


def test_seam_report_reads_a_seam_and_clears_a_tileable_image(mods):
    _, maps = mods
    rng = np.random.default_rng(3)
    a = rng.integers(0, 256, (64, 64, 3), dtype=np.uint8)
    # Deliberately NOT tileable: shifting the right half hard makes the wrap a step edge.
    seamed = a.copy()
    seamed[:, 32:] = np.clip(seamed[:, 32:].astype(int) - 120, 0, 255).astype(np.uint8)
    assert maps.seam_report(seamed)["ratio"] > maps.seam_report(a)["ratio"]
    # White noise is statistically continuous across the wrap, so its ratio sits near 1.
    assert 0.8 < maps.seam_report(a)["ratio"] < 1.2


def test_tile3x3_shape(mods, noise_rgb):
    _, maps = mods
    assert maps.tile3x3(noise_rgb).shape == (72, 96, 3)
    assert maps.tile3x3(noise_rgb[:, :, 0]).shape == (72, 96)


# -- comfy_maps: grain direction (BobFoliage, bark) ---------------------------------------
def _grating(size=96, period=6.0, vertical=True):
    """A sine grating. Vertical STRIPES vary along x, so their gradient axis is horizontal."""
    ramp = (np.sin(np.arange(size, dtype=np.float32) / period) * 60 + 128).astype(np.uint8)
    tile = np.tile(ramp, (size, 1))
    return tile if vertical else tile.T.copy()


def test_grain_report_reads_the_axis_features_run_along(mods):
    _, maps = mods
    vertical = maps.grain_report(_grating(vertical=True))
    assert vertical["off_vertical_deg"] < 2.0
    assert vertical["coherence"] > 0.9
    horizontal = maps.grain_report(_grating(vertical=False))
    assert horizontal["off_vertical_deg"] > 88.0


def test_grain_report_separates_wrong_direction_from_no_direction(mods):
    """The two bark failures are different and one measure cannot catch both.

    Measured when the sets landed: a bark set came back 84 degrees off vertical with coherence 0.49 (polygonal mud
    cracks, plenty of coherent edges pointing the wrong way), and another came back 18 degrees off
    with coherence 0.018 (no grain at all, so the angle was luck). The angle catches the first and
    the coherence catches the second.
    """
    _, maps = mods
    wrong = maps.grain_report(_grating(vertical=False))
    assert wrong["coherence"] > 0.5 and wrong["off_vertical_deg"] > 45.0
    noise = maps.grain_report(np.random.default_rng(0).integers(
        0, 256, (96, 96), dtype=np.uint8))
    assert noise["coherence"] < 0.05
    assert noise["block_spread_deg"] > 20.0


def test_axis_spread_treats_angles_as_axes_not_directions(mods):
    _, maps = mods
    # 0 and 180 are the SAME axis, so their spread is zero rather than maximal.
    assert maps.axis_spread([0.0, 180.0]) < 1e-6
    assert maps.axis_spread([90.0, 90.0, 90.0]) < 1e-6
    assert maps.axis_spread([0.0, 45.0, 90.0, 135.0]) > 20.0


# -- comfy_maps: leaf atlases (BobFoliage) -------------------------------------------------
def _sprig(angle_deg, size=96, woody=False, flare=0.2):
    """A wedge: narrow at one end, wide at the other, lying at `angle_deg`.

    With `woody`, the narrow third is brown and the rest green, which is the cue a real sprig gives.
    `flare` changes the SHAPE, which is what makes two sprigs genuinely different -- a wedge rotated
    to the same upright pose from two different angles is the same sprite, and `cell_distinctness`
    correctly says so.
    """
    sprite = np.zeros((size, size, 4), np.uint8)
    rad = np.radians(angle_deg)
    ux, uy = np.cos(rad), np.sin(rad)
    reach = size // 3
    for s in range(-reach, reach):
        half = int(2 + (s + reach) * flare)
        cy, cx = size / 2 + uy * s, size / 2 + ux * s
        for j in range(-half, half + 1):
            yy, xx = int(cy - ux * j), int(cx + uy * j)
            if 0 <= yy < size and 0 <= xx < size:
                brown = woody and s < -reach // 3
                sprite[yy, xx, :3] = (110, 70, 40) if brown else (40, 120, 40)
                sprite[yy, xx, 3] = 255
    return sprite


def test_orient_sprite_stands_a_sprig_on_its_narrow_end_from_any_angle(mods):
    """The property a card depends on: v is 0 at the attachment, so the stem must be at the bottom.

    Checked at four angles because `mesh_subject` returns sprays lying every way -- measured, a "stem at the
    bottom of the frame" prompt produced diagonal sprays with the twig at the left.
    """
    _, maps = mods
    for angle in (0.0, 37.0, 90.0, 143.0, 250.0):
        out = maps.orient_sprite(_sprig(angle))
        solid = out[:, :, 3] > 127
        ys, xs = np.nonzero(solid)
        rows = solid[ys.min():ys.max() + 1]
        band = max(1, len(rows) // 6)
        assert rows[-band:].sum() < rows[:band].sum(), f"{angle} deg came out upside down"
        # ... and it is genuinely upright rather than merely narrow at the bottom.
        assert (ys.max() - ys.min()) > (xs.max() - xs.min())


def test_orient_sprite_prefers_the_woody_end_when_there_is_one(mods):
    """A sprig whose stem sticks out SIDEWAYS has its long axis along the fan, so no end of that
    axis is the stem and no rotation of it can be right. The woody/green split answers the question
    directly. Measured on a generated atlas: it disagreed with the geometric answer on exactly the
    two cells that came out attached by their needle tips."""
    _, maps = mods
    size = 96
    sprite = np.zeros((size, size, 4), np.uint8)
    sprite[30:70, 20:76, :3] = (40, 120, 40)      # a wide green fan
    sprite[30:70, 20:76, 3] = 255
    sprite[46:52, 76:92, :3] = (110, 70, 40)      # a brown stem out to the RIGHT
    sprite[46:52, 76:92, 3] = 255
    out = maps.orient_sprite(sprite)
    solid = out[:, :, 3] > 127
    ys, xs = np.nonzero(solid)
    excess = (out[:, :, 1].astype(np.float32)
              - (out[:, :, 0].astype(np.float32) + out[:, :, 2].astype(np.float32)) / 2.0)
    wy, _wx = np.nonzero(solid & (excess <= 2.0))
    gy, _gx = np.nonzero(solid & (excess > 2.0))
    assert wy.mean() > gy.mean(), "the woody mass should end up BELOW the green"
    assert ys.size and xs.size


def test_orient_sprite_ignores_the_colour_cue_on_a_uniform_sprite(mods):
    """The cue assumes a colour, so it has to be guarded: an all-green (or all-brown) sprite falls
    back to geometry, which is what keeps an autumn atlas from being oriented by hue."""
    _, maps = mods
    green = maps.orient_sprite(_sprig(40.0, woody=False))
    solid = green[:, :, 3] > 127
    ys, _xs = np.nonzero(solid)
    rows = solid[ys.min():ys.max() + 1]
    band = max(1, len(rows) // 6)
    assert rows[-band:].sum() < rows[:band].sum()


def test_atlas_compose_fills_every_cell_bottom_anchored(mods):
    _, maps = mods
    sprites = [_sprig(a, woody=True) for a in (0.0, 50.0, 140.0, 250.0)]
    base, opacity = maps.atlas_compose(sprites, 2, 2, 256)
    assert base.shape == (256, 256, 3) and opacity.shape == (256, 256)
    cells = maps.atlas_cells(opacity, 2, 2)
    assert [c["cell"] for c in cells] == [0, 1, 2, 3]
    assert all(c["opaque"] > 0.02 for c in cells)
    assert all(c["reaches_base"] for c in cells)
    assert all(c["base_taper"] < 0.6 for c in cells)


def test_atlas_cells_reports_an_empty_cell_rather_than_hiding_it(mods):
    """An empty cell is a card that renders as nothing, which is invisible in a crown."""
    _, maps = mods
    sprites = [_sprig(0.0, woody=True), None, _sprig(90.0, woody=True), None]
    _base, opacity = maps.atlas_compose(sprites, 2, 2, 256)
    cells = maps.atlas_cells(opacity, 2, 2)
    assert [c["cell"] for c in cells if c["opaque"] < 0.01] == [1, 3]
    assert not cells[1]["reaches_base"]


def test_atlas_cells_catches_a_sprite_standing_on_its_fan(mods):
    """`reaches_base` passes on an upside-down sprite; `base_taper` is what does not."""
    _, maps = mods
    upright = maps.place_sprite(_sprig(90.0, woody=True), 128)
    flipped = upright[::-1].copy()
    _b, up_op = maps.atlas_compose([upright], 1, 1, 128)
    assert maps.atlas_cells(up_op, 1, 1)[0]["base_taper"] < 0.6
    # Composing an already-placed sprite re-orients it, so measure the flip directly.
    cell = maps.atlas_cells(flipped[:, :, 3], 1, 1)[0]
    assert cell["reaches_base"] and cell["base_taper"] > 1.0


def test_cell_distinctness_catches_one_sprite_repeated(mods):
    """Four copies of one spray passes every per-cell check and still renders one leaf ten thousand
    times, which is the atlas analogue of the gate's "two seeds give different trees"."""
    _, maps = mods
    one = _sprig(30.0, woody=True)
    _b, same = maps.atlas_compose([one, one, one, one], 2, 2, 256)
    assert maps.cell_distinctness(same, 2, 2) < 1e-6
    # Different SHAPES, not merely different angles: orientation is the whole point of the composer,
    # so the same wedge lying at four angles is deliberately the same sprite once it is stood up.
    varied = [_sprig(30.0, woody=True, flare=f) for f in (0.12, 0.20, 0.32, 0.45)]
    _b2, spread = maps.atlas_compose(varied, 2, 2, 256)
    assert maps.cell_distinctness(spread, 2, 2) > 5.0


def test_alpha_bleed_floods_leaf_colour_and_not_the_background(mods):
    """The atlas basecolor is written as RGB, so a transparent texel's COLOUR is load-bearing:
    bilinear filtering blends it into every silhouette. Reading from the near-transparent fringe
    instead of from opaque texels is what put a hard white halo on every sprite -- measured."""
    _, maps = mods
    size = 64
    rgb = np.full((size, size, 3), 250, np.uint8)          # studio white background
    alpha = np.zeros((size, size), np.float32)
    rgb[24:40, 24:40] = (40, 120, 40)
    alpha[24:40, 24:40] = 1.0
    filled = maps.alpha_bleed(rgb, alpha)
    clear = filled[alpha < 0.02].astype(np.float32).mean(axis=0)
    assert abs(clear[0] - 40) < 6 and abs(clear[1] - 120) < 6 and abs(clear[2] - 40) < 6
    # The opaque region itself is untouched.
    assert (filled[alpha > 0.5] == rgb[alpha > 0.5]).all()


def test_bark_and_atlas_suffixes_are_the_measured_ones(mods):
    """The bark clause is a measurement, not a preference: the texture sets tried four wordings over two species
    and two seeds and only this one held the grain inside 18 degrees of vertical. Pinned so a
    well-meant rewrite is a test failure rather than a silently plastic trunk."""
    comfy, _ = mods
    assert comfy.BARK_SUFFIX == "vertical bark, deep furrows running top to bottom"
    assert comfy.DEFAULT_ATLAS_ROUTE == "cells"
    assert comfy.atlas_routes() == ("cells", "grid")
    # `mesh_subject`'s own negative forbids "multiple objects", which is right for one sprite and wrong for a
    # grid, so the atlas route carries its own.
    assert "bunch" in comfy.ATLAS_NEGATIVE and "grid" in comfy.ATLAS_NEGATIVE


def test_unknown_atlas_route_is_a_sentence_not_a_traceback(mods):
    comfy, _ = mods
    with pytest.raises(comfy.ComfyError) as exc:
        comfy._atlas_route("quadrants")
    assert "quadrants" in str(exc.value)


def test_update_meta_merges_rather_than_replacing(mods, tmp_path):
    comfy, _ = mods
    (tmp_path / "meta.json").write_text(json.dumps({"seed": 4, "seam": {"ratio": 1.0}}))
    merged = comfy.update_meta(str(tmp_path), grain={"off_vertical_deg": 3.0})
    assert merged["seed"] == 4 and merged["seam"]["ratio"] == 1.0
    assert merged["grain"]["off_vertical_deg"] == 3.0
    assert json.loads((tmp_path / "meta.json").read_text())["grain"]["off_vertical_deg"] == 3.0


def test_update_meta_writes_a_fresh_file_when_there_is_none(mods, tmp_path):
    comfy, _ = mods
    assert comfy.update_meta(str(tmp_path), atlas={"cols": 4, "rows": 4})["atlas"]["cols"] == 4


def test_roughness_uses_the_whole_band_on_a_bright_albedo(mods):
    """the first spike's defect, as a test. A bright albedo used to park every pixel at the top of the band
    (measured 117-242 of 255, mean 206) because the map was a global remap of luminance."""
    _, maps = mods
    rng = np.random.default_rng(11)
    bright = np.clip(rng.normal(205, 12, (128, 128, 3)), 0, 255).astype(np.uint8)
    rough = maps.roughness_from(bright)
    lo, hi = maps.ROUGHNESS_RANGE
    assert rough.mean() < 200, "the map must not sit against the top of the band"
    # The 2nd-to-98th stretch is by construction, so this asserts the stretch actually ran.
    assert np.ptp(rough) > 0.8 * (hi - lo) * 255
    assert rough.min() >= int(lo * 255) - 1 and rough.max() <= int(hi * 255) + 1


def test_roughness_is_mid_band_on_a_flat_albedo(mods):
    """The same lesson the height map learned: a constant image carries no information, so
    stretching its rounding noise across the band would be inventing detail."""
    _, maps = mods
    flat = np.full((64, 64, 3), 128, dtype=np.uint8)
    rough = maps.roughness_from(flat)
    lo, hi = maps.ROUGHNESS_RANGE
    assert np.ptp(rough) <= 1
    assert abs(int(rough.mean()) - int((lo + hi) / 2 * 255)) <= 1


def test_normal_map_is_unit_length_and_flat_where_the_relief_is(mods, noise_rgb):
    _, maps = mods
    normal = maps.normal_from(maps.relief(noise_rgb))
    vec = normal.astype(np.float32) / 255.0 * 2.0 - 1.0
    length = np.linalg.norm(vec, axis=2)
    assert abs(length.mean() - 1.0) < 0.02 and length.max() < 1.05
    assert (normal[:, :, 2] > 127).all(), "a tangent-space normal never points into the surface"
    flat = maps.normal_from(maps.relief(np.full((32, 32, 3), 90, dtype=np.uint8)))
    assert np.abs(flat.astype(int) - np.array([128, 128, 255])).max() <= 1, "flat means straight up"


def test_ao_darkens_a_pit_and_leaves_a_flat_surface_alone(mods):
    _, maps = mods
    height = np.zeros((96, 96), dtype=np.float32)
    height[40:56, 40:56] = -0.5  # a square pit in an otherwise level surface
    ao = maps.ao_from(height)
    assert ao[48, 48] < ao[4, 4], "the pit must be more occluded than the open surface"
    assert ao[4, 4] > 200, "an open surface is barely occluded"
    assert maps.ao_from(np.zeros((32, 32), dtype=np.float32)).min() == 255


def test_wrap_pad_and_blend_restore_tileability(mods):
    """What `tex_upres` needs: an operation that pads at the border makes the two copies of each edge band
    diverge, and cross-fading them puts the wrap back. Simulated with a brightness ramp applied to
    the padded image, which is exactly the kind of border artefact an upscaler leaves."""
    _, maps = mods
    rng = np.random.default_rng(5)
    tile = rng.integers(0, 256, (64, 64, 3), dtype=np.uint8)
    pad = 8
    padded = maps.wrap_pad(tile, pad).astype(np.float32)
    # A non-periodic disturbance: a brightness drift across the padded image, so the two copies of
    # each edge band come back at different levels, which is what independent tiles actually do.
    x = np.linspace(-1, 1, padded.shape[1], dtype=np.float32)[None, :, None]
    y = np.linspace(-1, 1, padded.shape[0], dtype=np.float32)[:, None, None]
    padded = np.clip(padded + 30.0 * (x + y), 0, 255).astype(np.uint8)
    plain = maps.seam_report(maps.crop_pad(padded, pad))["ratio"]
    blended = maps.seam_report(maps.crop_wrap_blend(padded, pad))["ratio"]
    assert blended < plain
    assert 0.7 < blended < 1.3, f"the blended crop should tile again, got {blended:.2f}"


# -- comfy: templating, slugs, and the client against a fake server --------------------------
def test_shipped_workflow_is_templatable_and_local_only(mods):
    """`tex_tileable`'s contract: it loads, every BOB_* title is unique, and it names no cloud node."""
    comfy, _ = mods
    prompt, prov = comfy.load_workflow(str(WORKFLOWS / "tex_tileable.json"))
    assert prov.get("derived_from"), "a shipped graph records the template it came from"
    names = [t for t in comfy.titles(prompt) if t and t.startswith("BOB_")]
    assert len(names) == len(set(names)), "BOB_* titles must be unique"
    for required in ("BOB_PROMPT", "BOB_SEED", "BOB_SIZE", "BOB_CKPT", "BOB_OUT"):
        assert required in names
    classes = {n["class_type"] for n in prompt.values()}
    assert not any(c.startswith("Tencent") or c.startswith("api_") for c in classes)
    bound = comfy.template(prompt, {"BOB_PROMPT": {"text": "x"}, "BOB_SEED": {"seed": 9}})
    by_title = comfy.titles(bound)
    assert bound[by_title["BOB_PROMPT"]]["inputs"]["text"] == "x"
    assert bound[by_title["BOB_SEED"]]["inputs"]["seed"] == 9
    # The original is untouched: templating returns a copy, so one graph serves many jobs.
    assert prompt[by_title["BOB_SEED"]]["inputs"]["seed"] == 0


def test_template_refuses_an_unknown_title(mods):
    comfy, _ = mods
    prompt, _ = comfy.load_workflow(str(WORKFLOWS / "tex_tileable.json"))
    with pytest.raises(comfy.ComfyError, match="no node titled BOB_NOPE"):
        comfy.template(prompt, {"BOB_NOPE": {"text": "x"}})


def test_slug_and_unique_set_name_never_overwrite(mods, tmp_path):
    comfy, _ = mods
    assert comfy.slugify("Mossy  Rock -- wet!") == "mossy_rock_wet"
    assert comfy.slugify("") == "texture"
    (tmp_path / "rock").mkdir()
    assert comfy.unique_set_name(str(tmp_path), "rock") == "rock_02"
    (tmp_path / "rock_02").mkdir()
    assert comfy.unique_set_name(str(tmp_path), "rock") == "rock_03"


class _Fake(BaseHTTPRequestHandler):
    """Enough ComfyUI to exercise the client: queue, the jobs API, cancel, and /view."""

    calls = []
    poll_state = ["pending", "in_progress", "completed"]
    last_upload = b""

    def log_message(self, *a):
        pass

    def _send(self, obj, code=200):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        _Fake.calls.append(self.path)
        if self.path.startswith("/system_stats"):
            return self._send({"devices": [{"name": "fake", "vram_free": 2 << 20}]})
        if self.path.startswith("/api/jobs?"):
            return self._send({"jobs": []})
        if self.path.startswith("/api/jobs/"):
            state = _Fake.poll_state.pop(0) if _Fake.poll_state else "completed"
            out = ({"13": {"images": [{"filename": "a.png", "subfolder": "bob",
                                      "type": "output"}]}} if state == "completed" else {})
            return self._send({"id": "p1", "status": state, "outputs": out})
        if self.path.startswith("/view"):
            self.send_response(200)
            self.send_header("Content-Length", "3")
            self.end_headers()
            return self.wfile.write(b"png")
        if self.path.startswith("/object_info/CheckpointLoaderSimple"):
            return self._send({"CheckpointLoaderSimple":
                               {"input": {"required": {"ckpt_name": [["a.safetensors"], {}]}}}})
        return self._send({"error": "not found"}, 404)

    def do_POST(self):
        _Fake.calls.append(self.path)
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length)
        if self.path == "/upload/image":
            _Fake.last_upload = body
            return self._send({"name": "ref.png", "subfolder": "bob", "type": "input"})
        if self.path == "/prompt":
            return self._send({"prompt_id": "p1"})
        if self.path.endswith("/cancel"):
            return self._send({"cancelled": True})
        if self.path == "/free":
            # 200 with a ZERO-BYTE body, which is what ComfyUI really answers here.
            self.send_response(200)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return None
        return self._send({"error": "not found"}, 404)


@pytest.fixture
def fake_server():
    _Fake.calls = []
    _Fake.poll_state = ["pending", "in_progress", "completed"]
    srv = HTTPServer(("127.0.0.1", 0), _Fake)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


def test_client_against_a_fake_server(mods, fake_server):
    comfy, _ = mods
    ok, detail = comfy.reachable(fake_server)
    assert ok and "fake" in detail
    assert comfy.has_jobs_api(fake_server)
    assert comfy.combo_options("CheckpointLoaderSimple", "ckpt_name",
                               url=fake_server) == ["a.safetensors"]
    pid = comfy.queue({"1": {"class_type": "X", "inputs": {}}}, url=fake_server)
    assert pid == "p1"
    outputs = comfy.wait(pid, url=fake_server, poll=0.0)
    assert comfy.images(outputs) == [{"filename": "a.png", "subfolder": "bob", "type": "output"}]
    assert comfy.view(comfy.images(outputs)[0], url=fake_server) == b"png"
    assert comfy.cancel(pid, url=fake_server) is True
    # The jobs API was polled, not /history: the per-job primitive is the point (the cancellation rule).
    assert any(c.startswith("/api/jobs/p1") for c in _Fake.calls)
    assert not any(c.startswith("/history") for c in _Fake.calls)


def test_unreachable_server_is_a_message_not_a_traceback(mods):
    comfy, _ = mods
    ok, detail = comfy.reachable("http://127.0.0.1:1", timeout=1)
    assert ok is False and "not reachable" in detail
    with pytest.raises(comfy.ComfyError, match="not reachable"):
        comfy.queue({}, url="http://127.0.0.1:1")


def test_upload_posts_multipart_and_returns_the_load_image_name(mods, fake_server, tmp_path):
    comfy, _ = mods
    src = tmp_path / "ref.png"
    src.write_bytes(b"\x89PNG\r\n\x1a\nnot-really")
    assert comfy.upload_image(str(src), url=fake_server, subfolder="bob") == "bob/ref.png"
    assert "/upload/image" in _Fake.calls
    body = _Fake.last_upload
    assert b'name="image"; filename="ref.png"' in body and b'name="subfolder"' in body


# -- Preflight: one test per class of failure ------------------------------------------------
# The canned /object_info is deliberately tiny. Preflight's contract is "every reason this graph
# would fail, as a sentence", and each of the five reasons has a graph built to trip exactly it.
OBJECT_INFO = {
    "CheckpointLoaderSimple": {
        "input": {"required": {"ckpt_name": [["good.safetensors", "other.safetensors"]]}}},
    "UpscaleModelLoader": {
        # The NEWER combo shape, options hidden in the options dict. the first spike's reader saw only the old
        # one and would have passed a missing upscale model straight through to an HTTP 400.
        "input": {"required": {"model_name": ["COMBO", {"options": ["4x-UltraSharp.pth"]}]}}},
    "KSampler": {"input": {"required": {"seed": ["INT", {}], "model": ["MODEL"]}}},
    "SaveImage": {"input": {"required": {"images": ["IMAGE"],
                                         "filename_prefix": ["STRING", {}]}}},
    "TencentImageToModelNode": {"api_node": True, "input": {"required": {}}},
}


def _node(cls, title, **inputs):
    return {"class_type": cls, "inputs": inputs, "_meta": {"title": title}}


def _good_graph():
    return {"1": _node("CheckpointLoaderSimple", "BOB_CKPT", ckpt_name="good.safetensors"),
            "2": _node("KSampler", "BOB_SEED", seed=0, model=["1", 0]),
            "3": _node("SaveImage", "BOB_OUT", images=["2", 0], filename_prefix="bob/x")}


def test_preflight_passes_a_good_graph(mods):
    comfy, _ = mods
    assert comfy.preflight(_good_graph(), info=OBJECT_INFO,
                           required_titles=("BOB_CKPT", "BOB_OUT")) == []


def test_preflight_catches_an_unknown_class_type(mods):
    comfy, _ = mods
    graph = _good_graph()
    graph["9"] = _node("SomePackNobodyInstalled", "BOB_EXTRA")
    problems = comfy.preflight(graph, info=OBJECT_INFO)
    assert len(problems) == 1 and problems[0].startswith("unknown node: SomePackNobodyInstalled")
    assert "not installed" in problems[0]


def test_preflight_rejects_a_cloud_api_node(mods):
    """The check that keeps local-only true over time rather than by intention (the local-only rule/the local-only decision)."""
    comfy, _ = mods
    graph = _good_graph()
    graph["9"] = _node("TencentImageToModelNode", "BOB_MESH")
    problems = comfy.preflight(graph, info=OBJECT_INFO)
    assert len(problems) == 1 and problems[0].startswith("cloud node rejected:")
    assert "local only" in problems[0]


def test_preflight_catches_a_missing_model_in_either_combo_shape(mods):
    comfy, _ = mods
    graph = _good_graph()
    graph["1"]["inputs"]["ckpt_name"] = "never_downloaded.safetensors"      # old combo shape
    graph["9"] = _node("UpscaleModelLoader", "BOB_UPSCALE_MODEL",
                       model_name="4x-NotHere.pth")                          # new combo shape
    problems = sorted(comfy.preflight(graph, info=OBJECT_INFO))
    assert len(problems) == 2
    assert all(p.startswith("missing model:") for p in problems)
    assert "never_downloaded.safetensors" in problems[1] and "good.safetensors" in problems[1]
    assert "4x-NotHere.pth" in problems[0] and "4x-UltraSharp.pth" in problems[0]


def test_preflight_catches_a_duplicate_and_a_missing_bob_title(mods):
    comfy, _ = mods
    graph = _good_graph()
    graph["9"] = _node("SaveImage", "BOB_OUT", images=["2", 0], filename_prefix="bob/y")
    problems = comfy.preflight(graph, info=OBJECT_INFO, required_titles=("BOB_IMAGE",))
    assert any(p.startswith("duplicate title: BOB_OUT") for p in problems)
    assert any(p.startswith("missing title: BOB_IMAGE") for p in problems)


def test_preflight_rejects_a_uuid_subgraph_node(mods):
    """The shipped default text-to-image template is subgraphed, so this is the common case."""
    comfy, _ = mods
    graph = _good_graph()
    graph["9"] = _node("6f1c4e2a-9b3d-4f7a-8c2e-1d5b7a9e3c04", "BOB_SUB")
    problems = comfy.preflight(graph, info=OBJECT_INFO)
    assert len(problems) == 1 and problems[0].startswith("subgraph node rejected:")
    assert "flatten it" in problems[0]


def test_preflight_skips_an_input_bound_at_runtime(mods):
    """An uploaded reference image is not in the LoadImage enum until the upload happens, so its
    placeholder must not be reported as a missing file."""
    comfy, _ = mods
    info = dict(OBJECT_INFO, LoadImage={"input": {"required": {"image": [["example.png"]]}}})
    graph = _good_graph()
    graph["9"] = _node("LoadImage", "BOB_IMAGE", image="uploaded-later.png")
    assert len(comfy.preflight(graph, info=info)) == 1
    assert comfy.preflight(graph, info=info, runtime_inputs=["BOB_IMAGE.image"]) == []


def test_check_raises_with_every_problem_at_once(mods):
    comfy, _ = mods
    graph = _good_graph()
    graph["1"]["inputs"]["ckpt_name"] = "nope.safetensors"
    graph["9"] = _node("NotInstalled", "BOB_X")
    with pytest.raises(comfy.ComfyError) as exc:
        comfy.check(graph, info=OBJECT_INFO)
    assert "missing model" in str(exc.value) and "unknown node" in str(exc.value)


def test_every_shipped_workflow_preflights_against_a_dump(mods):
    """The shipped-graph assertion the plan asks for (the title-template rule/the local-only rule), run offline against the committed
    /object_info dump so it needs no server."""
    comfy, _ = mods
    dump = pathlib.Path(__file__).with_name("data") / "object_info_min.json"
    info = json.loads(dump.read_text())
    files = sorted(p for p in WORKFLOWS.iterdir() if p.suffix == ".json")
    assert files, "no shipped workflows to check"
    for path in files:
        prompt, prov = comfy.load_workflow(str(path))
        problems = comfy.preflight(prompt, info=info, required_titles=("BOB_OUT",),
                                   runtime_inputs=prov.get("runtime_inputs") or ())
        assert problems == [], f"{path.name}: {problems}"
        assert prov.get("derived_from"), f"{path.name} records no upstream template"


# -- The scheduler ---------------------------------------------------------------------------
def _drain(jobs, timeout=5.0):
    """Tick until nothing is active, the way Blender's timer would."""
    deadline = time.time() + timeout
    while jobs.tick() and time.time() < deadline:
        time.sleep(0.005)
    jobs.tick()


def test_job_runs_off_the_main_thread_and_reports_back_on_it(jobs):
    jobs.clear()
    seen = {}

    def work(job):
        seen["worker_thread"] = threading.current_thread().name
        job.report("halfway")
        return 42

    def done(job):
        seen["result"] = job.result
        seen["done_thread"] = threading.current_thread().name

    job = jobs.submit("unit", work, on_done=done)
    assert job.state == "queued", "submit must return before the work does"
    _drain(jobs)
    assert seen["result"] == 42 and job.state == "done"
    assert seen["worker_thread"] != seen["done_thread"], "the callback belongs to the caller"
    assert seen["done_thread"] == threading.current_thread().name
    assert job.progress == "halfway"


def test_a_failing_job_is_an_error_on_the_job_not_a_dead_worker(jobs):
    jobs.clear()

    def boom(job):
        raise ValueError("no server")

    bad = jobs.submit("bad", boom)
    _drain(jobs)
    assert bad.state == "failed" and isinstance(bad.error, ValueError)
    # The same worker takes the next job: one raised exception must not end the thread.
    good = jobs.submit("good", lambda job: "ok")
    _drain(jobs)
    assert good.state == "done" and good.result == "ok"


def test_clear_drops_callbacks_so_a_job_cannot_outlive_a_file_load(jobs):
    """the threading rule: a result that lands after load_post must not run against the new file."""
    jobs.clear()
    gate = threading.Event()
    fired = []

    job = jobs.submit("slow", lambda j: gate.wait(2) or "late", on_done=lambda j: fired.append(j))
    jobs.clear()                 # the load_post handler, while the worker is still in gate.wait
    gate.set()
    time.sleep(0.1)
    jobs.tick()
    assert fired == [], "a callback from before the load must not run after it"
    assert jobs.jobs() == [] and job.id not in [j.id for j in jobs.jobs()]


def test_cancel_reaches_the_server_once_the_job_has_a_prompt_id(mods, jobs, fake_server,
                                                                monkeypatch):
    """A cancel that only clears the local registry leaves the job running and still eating VRAM,
    so the worker hands its prompt id back the moment the server accepts the graph."""
    comfy, _ = mods
    monkeypatch.setattr(comfy, "_PREF_URL", fake_server)
    jobs.clear()
    queued, gate = threading.Event(), threading.Event()

    def work(job):
        job.note_prompt_id(comfy.queue({"1": {"class_type": "X", "inputs": {}}}, url=fake_server))
        queued.set()
        gate.wait(2)

    job = jobs.submit("cancellable", work)
    assert queued.wait(2)
    assert jobs.cancel(job.id) is True
    gate.set()
    assert "/api/jobs/p1/cancel" in _Fake.calls
    _drain(jobs)


def test_cancel_marks_the_job_and_a_tick_is_cheap_when_idle(jobs):
    jobs.clear()
    gate = threading.Event()
    job = jobs.submit("slow", lambda j: gate.wait(2))
    assert jobs.cancel(job.id) is True
    assert job.state == "cancelled" and not jobs.active()
    gate.set()
    jobs.max_tick_seconds(reset=True)
    for _ in range(50):
        jobs.tick()
    assert jobs.max_tick_seconds() < 0.01, "an idle tick must not cost a frame"


# -- Mesh transport (the asset gate) ------------------------------------------------------------------------
# A mesh is reported differently from an image, and getting that wrong is silent: the job says
# "completed" and Bob finds nothing to fetch.
def test_meshes_reads_both_output_shapes_and_ignores_images(mods):
    comfy, _ = mods
    outputs = {
        # Preview3D, which is how a Trellis2ExportTrimesh path reaches the outputs dict at all.
        "5": {"result": [{"filename": "/srv/ComfyUI/output/bob_mesh_2026.glb", "type": "output",
                          "subfolder": "", "mediaType": "3d"}]},
        # SaveGLB, the core node the Hunyuan graph ends with.
        "10": {"3d": [{"filename": "bob_mesh_00001_.glb", "subfolder": "mesh", "type": "output"}]},
        # A SaveImage in the same graph must not be mistaken for a mesh.
        "13": {"images": [{"filename": "preview.png", "subfolder": "", "type": "output"}]},
    }
    found = comfy.meshes(outputs)
    assert [f["filename"] for f in found] == ["/srv/ComfyUI/output/bob_mesh_2026.glb",
                                              "bob_mesh_00001_.glb"]
    assert found[1]["subfolder"] == "mesh"
    assert comfy.images(outputs)[0]["filename"] == "preview.png"


def test_view_basenames_an_absolute_server_path(mods, monkeypatch, fake_server):
    """`Trellis2ExportTrimesh` reports an absolute path and `/view` rejects a leading slash with a
    400, so the basename has to happen client-side or every mesh fetch fails."""
    comfy, _ = mods
    seen = {}

    def fake_request(url, path, **kwargs):
        seen["path"] = path
        return b"GLB"

    monkeypatch.setattr(comfy, "_request", fake_request)
    comfy.view({"filename": "/srv/ComfyUI/output/bob_mesh.glb", "type": "output"})
    assert "filename=bob_mesh.glb" in seen["path"]
    assert "%2F" not in seen["path"] and "/srv" not in seen["path"]


def test_upload_mesh_prefers_a_local_copy_over_http(mods, monkeypatch, tmp_path):
    """With the ComfyUI folder preference set, the mesh is copied into `<comfy>/input/3d` and the
    absolute path is handed to Trellis2LoadMesh. Not GeomPackLoadMesh, whose COMBO is a cached
    directory listing that will not have seen the file (the pack install)."""
    comfy, _ = mods
    src = tmp_path / "proxy.glb"
    src.write_bytes(b"glTF-ish")
    comfy_root = tmp_path / "ComfyUI"
    comfy_root.mkdir()
    monkeypatch.setattr(comfy, "upload_image",
                        lambda *a, **k: pytest.fail("should not have used HTTP"))
    comfy.set_pref_comfy_dir(str(comfy_root))
    try:
        got = comfy.upload_mesh(str(src))
    finally:
        comfy.set_pref_comfy_dir(None)
    assert got == str(comfy_root / "input" / "3d" / "proxy.glb")
    assert (comfy_root / "input" / "3d" / "proxy.glb").read_bytes() == b"glTF-ish"


def test_upload_mesh_falls_back_to_http_with_no_local_checkout(mods, monkeypatch, tmp_path):
    comfy, _ = mods
    src = tmp_path / "proxy.glb"
    src.write_bytes(b"glTF-ish")
    comfy.set_pref_comfy_dir(None)
    monkeypatch.setattr(comfy, "upload_image", lambda path, **k: "3d/proxy.glb")
    # Relative to the server's working directory, which is the ComfyUI root; Trellis2LoadMesh
    # resolves it with a plain os.path.exists.
    assert comfy.upload_mesh(str(src)) == "input/3d/proxy.glb"


def test_mesh_tiers_are_resolutions_not_model_files(mods):
    """LoadTrellis2Models takes no checkpoint name, so the tier binds `resolution`. The hero tier
    is `1536_cascade`: a plain 1536 is not one of the four options the node offers."""
    comfy, _ = mods
    assert comfy.MESH_TIERS["preview"] == "512"
    assert comfy.MESH_TIERS["default"] == "1024"
    assert comfy.MESH_TIERS["hero"] == "1536_cascade"
    graph, _prov = comfy.load_workflow("mesh_geom_trellis")
    loader = graph[comfy.titles(graph)["BOB_MODEL"]]
    assert loader["class_type"] == "LoadTrellis2Models"
    assert "ckpt_name" not in loader["inputs"]


def test_mesh_graphs_end_in_a_retrievable_output(mods):
    """Every mesh graph must report its file, or the job completes and Bob has nothing to fetch:
    either a Preview3D fed by the exporter, or a core output node that reports itself."""
    comfy, _ = mods
    for name in ("mesh_geom_trellis", "mesh_texture", "mesh_simplify_uv", "mesh_geom_texture"):
        graph, _prov = comfy.load_workflow(name)
        classes = {n["class_type"] for n in graph.values()}
        assert "Trellis2ExportTrimesh" in classes, name
        assert "Preview3D" in classes, f"{name} would report no filename"
    graph, _prov = comfy.load_workflow("mesh_geom")
    assert "SaveGLB" in {n["class_type"] for n in graph.values()}


def test_w4_joins_the_cut_through_an_inverted_mask(mods):
    """The sign that decides whether `mesh_subject` saves the subject or the background.

    Trellis2RemoveBackground returns a FOREGROUND mask; JoinImageWithAlpha computes
    alpha = 1 - mask, following ComfyUI's LoadImage convention. Wiring them directly cuts the
    subject out and keeps the background, silently.
    """
    comfy, _ = mods
    graph, _prov = comfy.load_workflow("mesh_subject")
    by_title = comfy.titles(graph)
    join = graph[by_title["BOB_RGBA"]]
    invert = graph[by_title["BOB_ALPHA"]]
    assert join["class_type"] == "JoinImageWithAlpha"
    assert invert["class_type"] == "InvertMask"
    assert join["inputs"]["alpha"][0] == by_title["BOB_ALPHA"], "alpha must come via InvertMask"
    assert invert["inputs"]["mask"][0] == by_title["BOB_CUT"]
    assert join["inputs"]["image"][0] == by_title["BOB_CUT"]


def test_bind_process_selects_one_branch_and_can_carry_the_face_budget(mods):
    """The two things `bind_process` exists for, on the real graphs.

    A `COMFY_DYNAMICCOMBO_V3`'s sub-widgets belong to the SELECTED key, and `template()` only
    merges, so the other branch's `remesh.*` fields have to leave the graph rather than be
    overridden. And `target_face_count` is a binding point on `mesh_geom_texture` but not on `mesh_geom_trellis`: on `mesh_geom_texture` it IS the
    simplify budget, which is what makes the one-shot route one-shot.
    """
    comfy, _ = mods
    for name in ("mesh_geom_trellis", "mesh_geom_texture"):
        graph, _prov = comfy.load_workflow(name)
        values = {}
        bound = comfy.bind_process(graph, values, remesh=False, faces=4000)
        node = bound[comfy.titles(bound)["BOB_PROCESS"]]
        assert node["class_type"] == "Trellis2ProcessMesh", name
        assert not [k for k in node["inputs"] if k.startswith("remesh.")], name
        assert values["BOB_PROCESS"]["remesh"] == "off", name
        assert values["BOB_PROCESS"]["target_face_count"] == 4000, name
        # The shipped file is not mutated: a second call sees the source graph again.
        assert [k for k in graph[comfy.titles(graph)["BOB_PROCESS"]]["inputs"]
                if k.startswith("remesh.")], name
        on = comfy.bind_process(graph, {}, remesh=True)
        assert on is not graph


def test_w9b_is_a_one_shot_route_beside_the_staged_one(mods):
    """`mesh_geom_texture`'s shape, as the benchmark relies on it: one graph that conditions, generates a shape,
    simplifies and unwraps it, textures it, and exports THAT mesh -- with the PBR rasterised into
    the simplified mesh's own charts and projected through the pre-simplify shape."""
    comfy, _ = mods
    graph, prov = comfy.load_workflow("mesh_geom_texture")
    by_title = comfy.titles(graph)
    classes = {n["class_type"] for n in graph.values()}
    assert {"Trellis2ImageToShape", "Trellis2ShapeToTexturedMesh", "Trellis2ProcessMesh",
            "Trellis2RasterizePBR", "Trellis2ExportTrimesh"} <= classes
    assert "GeomPackSaveMesh" not in classes, "no axis conversion and no UV V-flip"
    raster = graph[by_title["BOB_TEXSIZE"]]
    assert raster["inputs"]["trimesh"][0] == by_title["BOB_PROCESS"], \
        "the PBR must land in the SIMPLIFIED mesh's UVs, not the dense one's"
    assert raster["inputs"]["original_mesh"][0] == by_title["BOB_SEED"], \
        "and project through the pre-simplify shape, or it describes the wrong surface"
    assert graph[by_title["BOB_OUT"]]["inputs"]["trimesh"][0] == by_title["BOB_TEXSIZE"]
    # Two samplers, so two titles: one title binds one node.
    assert graph[by_title["BOB_SEED"]]["class_type"] == "Trellis2ImageToShape"
    assert graph[by_title["BOB_TEXSEED"]]["class_type"] == "Trellis2ShapeToTexturedMesh"
    assert prov["runtime_inputs"] == ["BOB_IMAGE.image"]


def test_the_route_is_a_value_and_maps_onto_the_finish_passes(mods):
    """Every route reaches `finish_asset` through one mapping, which is what makes the route A/B and the geometry A/B
    verdicts config changes. The one-shot route's single file goes in as the SIMPLIFIED mesh with no
    texture pass: passing it as `texture_pass` instead would have Blender decimate and unwrap a mesh
    it is about to throw away."""
    comfy, _ = mods
    assert comfy.DEFAULT_ASSET_ROUTE == "oneshot", "the one-shot-against-staged verdict"
    assert set(comfy.ASSET_ROUTES) == {"oneshot", "staged", "alt"}
    assert comfy.asset_chain() is comfy.generate_asset_oneshot
    assert comfy.asset_chain("staged") is comfy.generate_asset_chain
    assert comfy.asset_chain("alt") is comfy.generate_asset_alt
    assert comfy.finish_passes({"raw_mesh": "r.glb", "textured_mesh": "t.glb"}) == ("t.glb", None)
    assert comfy.finish_passes({"raw_mesh": "r.glb", "simplified_mesh": "s.glb",
                                "textured_mesh": "t.glb"}) == ("s.glb", "t.glb")


def test_the_per_class_verdict_is_one_table_and_a_control_still_wins(mods, monkeypatch):
    """the geometry A/B's verdict is per asset class, so `asset_chain` takes the kind; `KIND_ROUTE` is the only
    place that mapping exists, and a control beats all of it because neither `mesh_geom_texture` nor `mesh_geom_alt` takes one."""
    comfy, _ = mods
    assert comfy.KIND_ROUTE == {} or set(comfy.KIND_ROUTE.values()) <= set(comfy.ASSET_ROUTES)
    monkeypatch.setattr(comfy, "KIND_ROUTE", {"rocks": "alt"})
    assert comfy.asset_chain(kind="rocks") is comfy.generate_asset_alt
    assert comfy.asset_chain(kind="plants") is comfy.generate_asset_oneshot, "not named, so default"
    assert comfy.asset_chain(route="oneshot", kind="rocks") is comfy.generate_asset_oneshot, \
        "an explicit route beats the table"
    assert comfy.asset_chain(kind="rocks", control="b.glb") is comfy.generate_asset_chain
    assert comfy.asset_chain(route="alt", control="b.glb") is comfy.generate_asset_chain
    assert comfy.asset_chain(kind="nonsense") is comfy.generate_asset_oneshot


def test_foliage_is_one_value_because_it_decides_two_stages(mods):
    """`is_foliage` was a `kind in ("plants", "grass")` literal in the panel, the MCP tool and every
    benchmark. It turns off the ComfyUI remesh AND Blender's pinhole fill, so a drift between two
    copies of it would half-close a leaf."""
    comfy, _ = mods
    assert comfy.is_foliage("plants") and comfy.is_foliage("grass")
    assert not comfy.is_foliage("rocks") and not comfy.is_foliage("trees")
    assert not comfy.is_foliage(None) and not comfy.is_foliage("")


def test_w8_composites_the_subject_onto_a_plate_before_the_vision_encoder(mods):
    """The one reason `mesh_geom_alt` exists beside `mesh_geom`. `mesh_subject` writes RGBA whose RGB is still the SDXL frame and
    `LoadImage` drops alpha rather than compositing it, so the challenger would be conditioned on a
    background TRELLIS.2 never sees, and the geometry A/B grid would have measured the background."""
    comfy, _ = mods
    graph, prov = comfy.load_workflow("mesh_geom_alt")
    by_title = comfy.titles(graph)
    plate, composite = by_title["BOB_PLATE"], by_title["BOB_SUBJECT"]
    assert graph[plate]["class_type"] == "EmptyImage"
    assert graph[plate]["inputs"]["color"] == 16777215, "white, as Hunyuan3D's own preprocessing"
    node = graph[composite]
    assert node["class_type"] == "ImageCompositeMasked"
    assert node["inputs"]["destination"] == [plate, 0] and node["inputs"]["source"][0] == \
        by_title["BOB_IMAGE"], "the subject goes ON the plate, not the other way round"
    # The mask has to be the ALPHA, so an InvertMask sits between: LoadImage returns 1 - alpha and
    # ImageCompositeMasked applies the source where the mask is 1. Wired direct, it pastes the
    # background over the subject.
    assert node["inputs"]["mask"] == [by_title["BOB_ALPHA"], 0]
    assert graph[by_title["BOB_ALPHA"]]["class_type"] == "InvertMask"
    assert graph[by_title["BOB_VISION"]]["inputs"]["image"] == [composite, 0]
    assert graph[by_title["BOB_OUT"]]["class_type"] == "SaveGLB", "no turn to undo, unlike Trellis"
    assert prov["runtime_inputs"] == ["BOB_IMAGE.image"]


def test_w8p_normalises_before_it_processes(mods):
    """Both halves of `mesh_process`, and both are load-bearing. Hunyuan returns [-1, 1] where TRELLIS.2
    returns [-0.5, 0.5], so without the normalise `mesh_texture` voxelises outside the unit cube and the albedo
    comes back BLACK (the geometry A/B: in-chart std 0.0064 against 0.1810). It has to run BEFORE the process node
    as well, or `remesh_band` and `floater_threshold` mean different sizes on the two models."""
    comfy, _ = mods
    graph, prov = comfy.load_workflow("mesh_process")
    by_title = comfy.titles(graph)
    norm = graph[by_title["BOB_NORM"]]
    assert norm["class_type"] == "GeomPackNormalizeMeshToBBox"
    assert norm["inputs"]["target_size"] == 1.0, "1.0 is a [-0.5, 0.5] box"
    assert norm["inputs"]["trimesh"] == [by_title["BOB_MESH"], 0]
    process = graph[by_title["BOB_PROCESS"]]
    assert process["class_type"] == "Trellis2ProcessMesh"
    assert process["inputs"]["trimesh"] == [by_title["BOB_NORM"], 0]
    assert graph[by_title["BOB_OUT"]]["inputs"]["trimesh"] == [by_title["BOB_PROCESS"], 0]
    assert prov["runtime_inputs"] == ["BOB_MESH.mesh_path"]
    # And it takes the same binding `mesh_geom_trellis` and `mesh_geom_texture` take, or the grid would not be controlled.
    values = {}
    bound = comfy.bind_process(graph, values, remesh=False, faces=4000)
    assert values["BOB_PROCESS"] == {"remesh": "off", "remesh.fill_holes": False,
                                     "remesh.fill_holes_perimeter": 0.03,
                                     "target_face_count": 4000}
    assert not [k for k in bound[by_title["BOB_PROCESS"]]["inputs"] if k.startswith("remesh.")]


def test_the_alt_chain_stages_the_same_keys_as_the_staged_one(mods, monkeypatch, tmp_path):
    """The challenger is a route and not a pipeline: same three staged files, so `finish_passes`,
    `stage_exports` and `finish_asset` need no case for it."""
    comfy, _ = mods
    calls = []

    def fake(name):
        def record(*args, **kwargs):
            calls.append(name)
            path = str(tmp_path / f"{name}.glb")
            open(path, "wb").close()
            return {"path": path, "seconds": 1.0}
        return record

    monkeypatch.setattr(comfy, "mesh_geom_alt", fake("w8"))
    monkeypatch.setattr(comfy, "mesh_process", fake("w8p"))
    monkeypatch.setattr(comfy, "mesh_texture", fake("w9t"))
    subject = tmp_path / "subject.png"
    subject.write_bytes(b"")

    staged = comfy.generate_asset_alt("a rock", str(tmp_path / "pack"), subject=str(subject))
    assert calls == ["w8", "w8p", "w9t"]
    assert set(staged) >= {"raw_mesh", "simplified_mesh", "textured_mesh", "subject", "meta"}
    assert staged["meta"]["workflows"] == ["mesh_subject", "mesh_geom_alt", "mesh_process",
                                           "mesh_texture"]
    assert staged["meta"]["model"] == "hunyuan3d-2.1"
    assert comfy.finish_passes(staged) == (staged["simplified_mesh"], staged["textured_mesh"])
    assert comfy.stage_exports(staged) == {"raw": 0, "simplified": 1, "textured": 2}


# -- The UI-to-API converter (the asset gate corrections) ----------------------------------------------------
@pytest.fixture(scope="module")
def converter():
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "scripts"))
    return importlib.import_module("comfy_ui_to_api")


def _gui_node(node_id, ntype, widgets):
    return {"id": node_id, "type": ntype, "widgets_values": widgets, "inputs": []}


def test_converter_skips_an_undeclared_control_after_generate(converter):
    """The frontend adds a control widget to any INT named `seed` whether or not the schema says
    so, and none of the TRELLIS.2 samplers declares it. Without the value-based detection every
    widget after the seed is off by one, and the graph queues with "randomize" where a float
    belongs."""
    schema = {"input": {"optional": {"seed": ["INT", {"default": 0}],
                                     "strength": ["FLOAT", {"default": 6.5}],
                                     "steps": ["INT", {"default": 12}]}},
              "input_order": {"optional": ["seed", "strength", "steps"]}}
    prompt = converter.convert(
        {"nodes": [_gui_node(1, "Sampler", [1406845044, "randomize", 6.5, 12])], "links": []},
        {"Sampler": schema})
    assert prompt["1"]["inputs"] == {"seed": 1406845044, "strength": 6.5, "steps": 12}


def test_converter_expands_a_dynamic_combo_into_its_selected_branch(converter):
    """A COMFY_DYNAMICCOMBO_V3 is a key plus the inputs of the branch that key selects, inline.
    The flat `<field>.<sub>` entries /object_info lists at the end of input_order are the UNION of
    every branch, so reading them in that order is wrong twice over."""
    schema = {"input": {"required": {
        "remesh": ["COMFY_DYNAMICCOMBO_V3", {"options": [
            {"key": "off", "inputs": {"required": {"fill_holes": ["BOOLEAN", {}],
                                                   "fill_holes_perimeter": ["FLOAT", {}]}}},
            {"key": "on", "inputs": {"required": {"remesh_band": ["FLOAT", {}],
                                                  "remove_inner_faces": ["BOOLEAN", {}]}}}]}],
        "target_face_count": ["INT", {"default": 500000}]},
        "optional": {"weld_digits": ["INT", {"default": 4}],
                     "remesh.fill_holes": ["BOOLEAN", {}],
                     "remesh.remesh_band": ["FLOAT", {}]}},
        "input_order": {"required": ["remesh", "target_face_count"],
                        "optional": ["weld_digits", "remesh.fill_holes", "remesh.remesh_band"]}}
    prompt = converter.convert(
        {"nodes": [_gui_node(1, "Process", ["on", 1.0, True, 500000, 4])], "links": []},
        {"Process": schema})
    assert prompt["1"]["inputs"] == {"remesh": "on", "remesh.remesh_band": 1.0,
                                     "remesh.remove_inner_faces": True,
                                     "target_face_count": 500000, "weld_digits": 4}


# -- The look-dev stylise family and B stylised, plus multi-view (the stylise gate) ------------------------------------------------
def test_drop_node_removes_the_lora_and_rewires_its_consumers(mods):
    """The reason a LoRA is a graph EDIT and not a zero strength: `LoraLoader` still has to name an
    installed file, and no shipped default can know what is installed on this machine (the portability rule)."""
    comfy, _ = mods
    graph, _prov = comfy.load_workflow("stylize_render")
    by_title = comfy.titles(graph)
    lora_id, ckpt_id = by_title["BOB_LORA"], by_title["BOB_CKPT"]
    assert graph[by_title["BOB_PROMPT"]]["inputs"]["clip"] == [lora_id, 1]

    stripped = comfy.drop_node(graph, "BOB_LORA", {0: "model", 1: "clip"})
    assert "BOB_LORA" not in comfy.titles(stripped)
    # The text encodes took the LoRA's CLIP; they now take the checkpoint's, and the sampler takes
    # the checkpoint's model. Nothing dangles.
    assert stripped[by_title["BOB_PROMPT"]]["inputs"]["clip"] == [ckpt_id, 1]
    assert stripped[by_title["BOB_NEG"]]["inputs"]["clip"] == [ckpt_id, 1]
    assert stripped[by_title["BOB_SEED"]]["inputs"]["model"] == [ckpt_id, 0]
    assert not any(isinstance(v, list) and v and v[0] == lora_id
                   for node in stripped.values() for v in node["inputs"].values())
    # And the shipped file is untouched, so a second call sees the LoRA again.
    assert "BOB_LORA" in comfy.titles(graph)
    assert comfy.drop_node(graph, "BOB_NOT_THERE", {0: "model"}) is graph


def test_w12_chains_both_controlnets_with_the_union_normal_head(mods):
    """`stylize_render`'s shape, and the one thing about it that is not obvious: there is no standalone SDXL
    normal ControlNet on disk, so the normal hint goes through the UNION model plus
    SetUnionControlNetType, while depth uses the dedicated depth model."""
    comfy, _ = mods
    graph, prov = comfy.load_workflow("stylize_render")
    by_title = comfy.titles(graph)
    depth_apply = graph[by_title["BOB_DEPTH_APPLY"]]
    normal_apply = graph[by_title["BOB_NORMAL_APPLY"]]
    assert depth_apply["class_type"] == normal_apply["class_type"] == "ControlNetApplyAdvanced"
    # Chained, not parallel: ComfyUI has no multi-hint apply node.
    assert normal_apply["inputs"]["positive"] == [by_title["BOB_DEPTH_APPLY"], 0]
    assert normal_apply["inputs"]["negative"] == [by_title["BOB_DEPTH_APPLY"], 1]
    assert depth_apply["inputs"]["image"] == [by_title["BOB_DEPTH"], 0]
    assert normal_apply["inputs"]["image"] == [by_title["BOB_NORMAL"], 0]
    union = graph[by_title["BOB_NORMAL_TYPE"]]
    assert union["class_type"] == "SetUnionControlNetType" and union["inputs"]["type"] == "normal"
    assert normal_apply["inputs"]["control_net"] == [by_title["BOB_NORMAL_TYPE"], 0]
    assert graph[by_title["BOB_SEED"]]["inputs"]["positive"] == [by_title["BOB_NORMAL_APPLY"], 0]
    assert set(prov["runtime_inputs"]) >= {"BOB_IMAGE.image", "BOB_DEPTH.image",
                                           "BOB_NORMAL.image", "BOB_LORA.lora_name"}


def test_w9_is_w12_plus_a_reference(mods):
    """`mesh_paint_views` grows out of `stylize_render`, which is why `stylize_render` is built first: the difference is the IPAdapter that
    holds a palette across a turntable, and a lower denoise so the real render keeps dominating."""
    comfy, _ = mods
    paint, _ = comfy.load_workflow("mesh_paint_views")
    stylise, _ = comfy.load_workflow("stylize_render")
    shared = {"CheckpointLoaderSimple", "LoraLoader", "ControlNetApplyAdvanced",
              "SetUnionControlNetType", "VAEEncode", "KSampler"}
    assert shared <= {n["class_type"] for n in paint.values()}
    assert shared <= {n["class_type"] for n in stylise.values()}
    by_title = comfy.titles(paint)
    adapter = paint[by_title["BOB_IPADAPTER"]]
    assert adapter["class_type"] == "IPAdapterAdvanced"
    assert adapter["inputs"]["image"] == [by_title["BOB_REF"], 0]
    assert adapter["inputs"]["model"] == [by_title["BOB_LORA"], 0], "patched after the LoRA"
    assert paint[by_title["BOB_SEED"]]["inputs"]["model"] == [by_title["BOB_IPADAPTER"], 0]
    assert paint[by_title["BOB_SEED"]]["inputs"]["denoise"] < \
        stylise[comfy.titles(stylise)["BOB_SEED"]]["inputs"]["denoise"]
    assert "BOB_IPADAPTER" not in comfy.titles(stylise)


def test_the_stylise_route_picks_its_graph_from_the_hints_it_is_given(mods, monkeypatch, tmp_path):
    """Real passes or estimated ones is a route, not a second operator: pass Blender's two passes and
    `stylize_render` runs, omit them and `stylize_render_est` runs the estimators over the render itself."""
    comfy, _ = mods
    seen = {}

    def fake_generate(workflow, values, **kwargs):
        graph, _prov = workflow
        seen["graph"] = graph
        seen["values"] = values
        return b"\x89PNG", {"prompt_id": "p1", "seconds": 1.0}

    monkeypatch.setattr(comfy, "upload_image", lambda path, **kw: os.path.basename(str(path)))
    monkeypatch.setattr(comfy, "generate_image", fake_generate)
    source = tmp_path / "render.png"
    source.write_bytes(b"x")
    depth = tmp_path / "render_depth.png"
    depth.write_bytes(b"x")
    normal = tmp_path / "render_normal.png"
    normal.write_bytes(b"x")

    info = comfy.stylize_render(str(source), str(tmp_path / "out.png"), "painted concept art",
                               depth=str(depth), normal=str(normal), seed=3, size=1000)
    assert info["workflow"] == "stylize_render" and info["hints"] == "passes"
    assert seen["values"]["BOB_DEPTH"]["image"] == "render_depth.png"
    assert "BOB_LORA" not in comfy.titles(seen["graph"]), "no LoRA asked for, so no LoRA node"
    # 1000 is not a multiple of 8, and an SDXL latent is eighths of a pixel.
    assert seen["values"]["BOB_SIZE"] == {"width": 1000, "height": 1000}
    assert info["size"] == [1000, 1000]
    assert comfy.STYLISE_SUFFIX in info["prompt"]

    info = comfy.stylize_render(str(source), str(tmp_path / "out2.png"), "painted concept art",
                               lora="style.safetensors", lora_strength=0.6, size=1023)
    assert info["workflow"] == "stylize_render_est" and info["hints"] == "estimated"
    assert "BOB_DEPTH" not in seen["values"], "the estimated graph has no hint to bind"
    assert comfy.titles(seen["graph"])["BOB_LORA"]
    assert seen["values"]["BOB_LORA"] == {"lora_name": "style.safetensors",
                                         "strength_model": 0.6, "strength_clip": 0.6}
    assert seen["values"]["BOB_SIZE"] == {"width": 1024, "height": 1024}


def test_paint_views_reuses_the_stylised_front_as_the_reference(mods, monkeypatch, tmp_path):
    """The cheap half of the projection-route finding's consistency mitigation, and the ordering IS the mitigation: the front
    view is its own reference, and every later view takes the STYLISED front, so the palette is
    decided once instead of drifting per view."""
    comfy, _ = mods
    calls = []

    def fake_stylize(image_path, out_path, prompt_text, **kwargs):
        calls.append({"image": image_path, "reference": kwargs.get("reference"),
                      "preflight": kwargs.get("preflight_graph"), "out": out_path})
        pathlib.Path(out_path).write_bytes(b"png")
        return {"path": out_path, "seconds": 2.0}

    monkeypatch.setattr(comfy, "stylize_render", fake_stylize)
    views = []
    for i in range(3):
        beauty = tmp_path / f"view_{i:02d}_beauty.png"
        beauty.write_bytes(b"x")
        views.append({"beauty": str(beauty), "depth": str(beauty), "normal": str(beauty)})
    out = comfy.paint_views(views, str(tmp_path / "styled"), "mossy rock", seed=9)

    assert len(out["images"]) == 3 and out["views"] == 3
    assert calls[0]["reference"] == views[0]["beauty"], "the front is its own reference"
    assert calls[1]["reference"] == calls[0]["out"] == out["images"][0]
    assert calls[2]["reference"] == out["images"][0], "every later view, the same stylised front"
    assert [c["preflight"] for c in calls] == [True, False, False], "one preflight per batch"
    assert out["total_seconds"] == 6.0


def test_the_texture_route_is_a_value_beside_the_asset_route(mods):
    """`mesh_paint_views`-as-a-paint-route lands in the shape the route A/B gave the geometry decision: one place where the
    route becomes a decision, not a second operator."""
    comfy, _ = mods
    assert set(comfy.TEXTURE_ROUTES) == {"pbr", "stylised"}
    assert comfy.DEFAULT_TEXTURE_ROUTE == "pbr"
    assert comfy.texture_chain() is comfy.mesh_texture
    assert comfy.texture_chain("stylised") is comfy.paint_views
    # And the asset route is untouched by it.
    assert comfy.asset_chain() is comfy.generate_asset_oneshot


def test_the_two_multi_view_graphs_take_the_same_four_views(mods):
    """`mesh_geom_mv` and `mesh_geom_mv_trellis` exist to be compared, so they have to take one set of renders: same titles, same
    order, same alpha contract. The order is the order Hunyuan's own sockets name."""
    comfy, _ = mods
    titles = ("BOB_VIEW_FRONT", "BOB_VIEW_LEFT", "BOB_VIEW_BACK", "BOB_VIEW_RIGHT")
    for name in ("mesh_geom_mv", "mesh_geom_mv_trellis"):
        graph, prov = comfy.load_workflow(name)
        by_title = comfy.titles(graph)
        for title in titles:
            assert graph[by_title[title]]["class_type"] == "LoadImage", f"{name}.{title}"
        assert set(prov["runtime_inputs"]) == {f"{t}.image" for t in titles}, name

    hunyuan, _ = comfy.load_workflow("mesh_geom_mv")
    by_title = comfy.titles(hunyuan)
    cond = hunyuan[by_title["BOB_COND"]]
    assert cond["class_type"] == "Hunyuan3Dv2ConditioningMultiView"
    for socket, view in (("front", "FRONT"), ("left", "LEFT"), ("back", "BACK"),
                         ("right", "RIGHT")):
        encoder = cond["inputs"][socket]
        assert hunyuan[encoder[0]]["inputs"]["image"] == [by_title[f"BOB_VIEW_{view}"], 0]
        # crop 'none', not 'center': cropping four views independently centres each one
        # differently and the four stop describing one object.
        assert hunyuan[encoder[0]]["inputs"]["crop"] == "none"

    trellis, _ = comfy.load_workflow("mesh_geom_mv_trellis")
    by_title = comfy.titles(trellis)
    shape = trellis[by_title["BOB_SEED"]]
    assert shape["class_type"] == "Trellis2MultiViewImageToShape"
    for side, view in (("front", "FRONT"), ("left", "LEFT"), ("back", "BACK"), ("right", "RIGHT")):
        assert shape["inputs"][f"{side}_image"] == [by_title[f"BOB_VIEW_{view}"], 0]
        # The mask has to come back through an InvertMask: LoadImage returns 1 - alpha.
        invert = shape["inputs"][f"{side}_mask"]
        assert trellis[invert[0]]["class_type"] == "InvertMask"
        assert trellis[invert[0]]["inputs"]["mask"] == [by_title[f"BOB_VIEW_{view}"], 1]
    assert trellis[by_title["BOB_PROCESS"]]["inputs"]["trimesh"] == [by_title["BOB_SEED"], 0]


def test_w7_conditions_on_a_control_mesh_and_ends_in_a_retrievable_output(mods):
    """`mesh_geom_ctrl`'s shape, and the three things about it that are easy to get wrong.

    The control is a MESH read by `Trellis2LoadMesh`, because the Omni pack ships no loader and its
    socket is TRELLIS.2's `TRIMESH` type; the point budget is named rather than left at the node's
    own default of 'use the raw vertices'; and the model path ships as a HuggingFace repo id so the
    graph is portable to a machine whose weights live somewhere else (the portability rule)."""
    comfy, _ = mods
    graph, prov = comfy.load_workflow("mesh_geom_ctrl")
    by_title = comfy.titles(graph)

    gen = graph[by_title["BOB_SEED"]]
    assert gen["class_type"] == "Hy3DOmniPointGenerate"
    assert gen["inputs"]["control_mesh"] == [by_title["BOB_CONTROL"], 0]
    assert gen["inputs"]["image"] == [by_title["BOB_IMAGE"], 0]
    assert gen["inputs"]["pipeline"] == [by_title["BOB_OMNI"], 0]
    assert graph[by_title["BOB_CONTROL"]]["class_type"] == "Trellis2LoadMesh"
    assert gen["inputs"]["sample_point_count"] > 0, "0 means the proxy's raw vertices, a few dozen"

    omni = graph[by_title["BOB_OMNI"]]
    assert omni["inputs"]["repo_or_path"] == "tencent/Hunyuan3D-Omni"
    assert not os.path.isabs(omni["inputs"]["repo_or_path"])

    # Same retrievable tail as `mesh_geom_trellis`: the export node reports a STRING, Preview3D makes it an output.
    assert graph[by_title["BOB_OUT"]]["class_type"] == "Trellis2ExportTrimesh"
    assert graph[by_title["BOB_OUT"]]["inputs"]["trimesh"] == [by_title["BOB_SEED"], 0]
    assert graph[by_title["BOB_VIEW"]]["inputs"]["model_file"] == [by_title["BOB_OUT"], 0]
    assert set(prov["runtime_inputs"]) == {"BOB_IMAGE.image", "BOB_CONTROL.mesh_path",
                                           "BOB_OMNI.repo_or_path"}


def test_the_comfy_folder_falls_back_to_the_environment(mods, monkeypatch, tmp_path):
    """Only the ADDON can register a preference, and the MCP server is not the addon. Without this
    fallback `upload_mesh` cannot copy into `<comfy>/input/3d`, and the geometry A/B measured that the HTTP route
    it falls back to does not work on this fork: the pack's loader runs in a pixi worker whose
    working directory is not the ComfyUI root, so a relative path fails inside the graph."""
    comfy, _ = mods
    monkeypatch.setattr(comfy, "_PREF_COMFY_DIR", None)
    monkeypatch.delenv("BOB_COMFY_DIR", raising=False)
    assert comfy.comfy_dir() is None and comfy.input_3d_dir() is None
    monkeypatch.setenv("BOB_COMFY_DIR", str(tmp_path / "nowhere"))
    assert comfy.comfy_dir() is None, "a path that does not exist is not a ComfyUI folder"
    monkeypatch.setenv("BOB_COMFY_DIR", str(tmp_path))
    assert comfy.comfy_dir() == str(tmp_path)
    # And the preference still wins over the environment.
    other = tmp_path / "pref"
    other.mkdir()
    monkeypatch.setattr(comfy, "_PREF_COMFY_DIR", str(other))
    assert comfy.comfy_dir() == str(other)


def test_w7_binds_the_local_weights_only_when_they_are_there(mods, monkeypatch, tmp_path):
    """`omni_model_dir` is the portability rule rule in one function: a local absolute path when this machine has
    the weights, and the graph's own portable default when it does not."""
    comfy, _ = mods
    monkeypatch.setattr(comfy, "_PREF_COMFY_DIR", None)
    assert comfy.omni_model_dir() is None
    monkeypatch.setattr(comfy, "_PREF_COMFY_DIR", str(tmp_path))
    assert comfy.omni_model_dir() is None, "no weights directory means no binding"
    (tmp_path / "models" / "hunyuan3d-omni").mkdir(parents=True)
    assert comfy.omni_model_dir() == str(tmp_path / "models" / "hunyuan3d-omni")


def test_the_block_out_route_swaps_step_two_and_nothing_else(mods, monkeypatch, tmp_path):
    """`control` is a value on the staged chain, not a fourth route: `mesh_geom_trellis` becomes `mesh_geom_ctrl` and every stage
    after it is the same call with the same arguments."""
    comfy, _ = mods
    calls = []

    def fake(name):
        def record(*args, **kwargs):
            calls.append(name)
            path = str(tmp_path / f"{name}.glb")
            open(path, "wb").close()
            return {"path": path, "seconds": 1.0}
        return record

    monkeypatch.setattr(comfy, "mesh_geometry", fake("w5t"))
    monkeypatch.setattr(comfy, "mesh_geom_ctrl", fake("w7"))
    monkeypatch.setattr(comfy, "mesh_simplify_uv", fake("w9c"))
    monkeypatch.setattr(comfy, "mesh_texture", fake("w9t"))
    subject = tmp_path / "subject.png"
    subject.write_bytes(b"")

    staged = comfy.generate_asset_chain("a rock", str(tmp_path / "pack"), subject=str(subject))
    assert calls == ["w5t", "w9c", "w9t"]
    assert staged["meta"]["workflows"][1] == "mesh_geom_trellis"

    calls.clear()
    control = tmp_path / "blockout.glb"
    control.write_bytes(b"")
    staged = comfy.generate_asset_chain("a rock", str(tmp_path / "pack"), subject=str(subject),
                                        control=str(control))
    assert calls == ["w7", "w9c", "w9t"]
    assert staged["meta"]["workflows"] == ["mesh_subject", "mesh_geom_ctrl", "mesh_simplify_uv",
                                           "mesh_texture"]
    assert staged["meta"]["control"] == str(control)
    assert staged["meta"]["model"] == "Hunyuan3D-Omni"


def test_w7b_conditions_on_three_numbers_and_uploads_nothing(mods):
    """`mesh_geom_bbox`'s shape. The control is not a socket at all: `Hy3DOmniBBoxGenerate` has no `control_mesh`
    input, so the graph has no mesh loader, which is what makes it the one Omni route that needs no
    ComfyUI folder to know (the geometry A/B's mesh-transport failure). `auto_bbox` ships FALSE because the whole
    point is that Bob knows the proportions the node would otherwise estimate off the image."""
    comfy, _ = mods
    graph, prov = comfy.load_workflow("mesh_geom_bbox")
    by_title = comfy.titles(graph)

    gen = graph[by_title["BOB_SEED"]]
    assert gen["class_type"] == "Hy3DOmniBBoxGenerate"
    assert gen["inputs"]["image"] == [by_title["BOB_IMAGE"], 0]
    assert gen["inputs"]["pipeline"] == [by_title["BOB_OMNI"], 0]
    assert gen["inputs"]["auto_bbox"] is False
    assert "control_mesh" not in gen["inputs"]
    assert not any(n["class_type"] == "Trellis2LoadMesh" for n in graph.values())

    omni = graph[by_title["BOB_OMNI"]]
    assert omni["inputs"]["repo_or_path"] == "tencent/Hunyuan3D-Omni"
    assert not os.path.isabs(omni["inputs"]["repo_or_path"])

    # `mesh_geom_ctrl`'s tail unchanged, so the same one export turn comes back and the same undo applies.
    assert graph[by_title["BOB_OUT"]]["class_type"] == "Trellis2ExportTrimesh"
    assert graph[by_title["BOB_VIEW"]]["inputs"]["model_file"] == [by_title["BOB_OUT"], 0]
    assert set(prov["runtime_inputs"]) == {"BOB_IMAGE.image", "BOB_OMNI.repo_or_path"}


def test_w7v_reads_the_same_control_and_does_not_turn_it(mods):
    """`mesh_geom_voxel`'s shape. It is `mesh_geom_ctrl` with one node swapped, so the loader, the exporter and the return turn
    are all shared and the difference is what the generator does with the same file.

    `apply_input_rotation` is the setting the whole graph turns on. The node defaults it to TRUE
    because upstream's `infer_voxel` turns its control and `infer_point` does not, an asymmetry that
    belongs to two demo datasets rather than to the model. Bob's control is the file `mesh_geom_ctrl` already
    conditions on correctly, so the extra turn is a turn away from the frame that works, and it
    would have shown up as "the voxel mode is bad" rather than as an error (the voxel gate)."""
    comfy, _ = mods
    graph, prov = comfy.load_workflow("mesh_geom_voxel")
    by_title = comfy.titles(graph)

    gen = graph[by_title["BOB_SEED"]]
    assert gen["class_type"] == "Hy3DOmniVoxelGenerate"
    assert gen["inputs"]["image"] == [by_title["BOB_IMAGE"], 0]
    assert gen["inputs"]["pipeline"] == [by_title["BOB_OMNI"], 0]
    assert gen["inputs"]["control_mesh"] == [by_title["BOB_CONTROL"], 0]
    assert gen["inputs"]["apply_input_rotation"] is comfy.VOXEL_INPUT_ROTATION
    assert comfy.VOXEL_INPUT_ROTATION is False
    assert graph[by_title["BOB_CONTROL"]]["class_type"] == "Trellis2LoadMesh"

    omni = graph[by_title["BOB_OMNI"]]
    assert omni["inputs"]["repo_or_path"] == "tencent/Hunyuan3D-Omni"
    assert not os.path.isabs(omni["inputs"]["repo_or_path"])

    # `mesh_geom_ctrl`'s tail unchanged, so the same one export turn comes back and the same undo applies.
    assert graph[by_title["BOB_OUT"]]["class_type"] == "Trellis2ExportTrimesh"
    assert graph[by_title["BOB_VIEW"]]["inputs"]["model_file"] == [by_title["BOB_OUT"], 0]
    assert set(prov["runtime_inputs"]) == {"BOB_IMAGE.image", "BOB_CONTROL.mesh_path",
                                           "BOB_OMNI.repo_or_path"}


def test_the_control_mode_is_one_decision_in_one_place(mods):
    """`control_route` is where "which Omni mode" lives, and every caller passes what it holds
    rather than deciding. The two-signal case matters: the panel exports both because the bbox costs
    nothing once the object is in hand, so the default has to break that tie.

    Since the voxel gate two modes share the MESH form, so a control file no longer names a mode on its own and
    the tie it breaks is the one between point and voxel."""
    comfy, _ = mods
    assert comfy.control_route() is None
    assert comfy.control_route(control="/x.glb") == comfy.DEFAULT_CONTROL_MODE
    assert comfy.control_route(control_bbox=[1, 1, 0.5]) == "bbox"
    assert comfy.control_route(control="/x.glb",
                               control_bbox=[1, 1, 0.5]) == comfy.DEFAULT_CONTROL_MODE
    assert comfy.control_route("bbox", control="/x.glb") == "bbox"
    assert comfy.control_route("voxel", control="/x.glb") == "voxel"
    assert set(comfy.CONTROL_MODES) == set(comfy.CONTROL_WORKFLOWS)
    assert comfy.DEFAULT_CONTROL_MODE in comfy.CONTROL_MODES
    assert set(comfy.MESH_CONTROL_MODES) <= set(comfy.CONTROL_MODES)
    assert "bbox" not in comfy.MESH_CONTROL_MODES
    # Either form forces the staged chain, because no one-shot route takes a control of any kind.
    assert comfy.asset_chain(control="/x.glb") is comfy.generate_asset_chain
    assert comfy.asset_chain(control_bbox=[1, 1, 0.5]) is comfy.generate_asset_chain
    assert comfy.asset_chain() is comfy.generate_asset_oneshot


def test_an_unknown_control_mode_raises_rather_than_generating_uncontrolled(mods):
    """The dispatch in `generate_asset_source` falls through to the UNCONTROLLED route, so a mode
    name that reaches it unrecognised produces a plausible mesh that no block-out shaped. That is
    the failure this integration has now found four times over (the pack install's black albedo, the control gate's random
    projection, the bbox gate's `auto_bbox`, the voxel gate's input rotation), and it is the one class of bug worth a raise:
    every instance of it ran to completion and reported success."""
    comfy, _ = mods
    with pytest.raises(comfy.ComfyError):
        comfy.control_route(mode="voxels", control="/x.glb")
    with pytest.raises(comfy.ComfyError):
        comfy.control_route(mode="pose")


def test_the_bbox_route_swaps_step_two_and_carries_its_own_provenance(mods, monkeypatch, tmp_path):
    """The bbox control is the same value swap the point control is, one graph further along."""
    comfy, _ = mods
    calls = []

    def fake(name):
        def record(*args, **kwargs):
            calls.append(name)
            path = str(tmp_path / f"{name}.glb")
            open(path, "wb").close()
            return {"path": path, "seconds": 1.0}
        return record

    monkeypatch.setattr(comfy, "mesh_geometry", fake("w5t"))
    monkeypatch.setattr(comfy, "mesh_geom_ctrl", fake("w7"))
    monkeypatch.setattr(comfy, "mesh_geom_bbox", fake("w7b"))
    monkeypatch.setattr(comfy, "mesh_simplify_uv", fake("w9c"))
    monkeypatch.setattr(comfy, "mesh_texture", fake("w9t"))
    subject = tmp_path / "subject.png"
    subject.write_bytes(b"")

    staged = comfy.generate_asset_chain("a rock", str(tmp_path / "pack"), subject=str(subject),
                                        control_bbox=[0.4, 1.0, 0.6])
    assert calls == ["w7b", "w9c", "w9t"]
    assert staged["meta"]["workflows"] == ["mesh_subject", "mesh_geom_bbox", "mesh_simplify_uv",
                                           "mesh_texture"]
    assert staged["meta"]["control_mode"] == "bbox"
    assert staged["meta"]["control"] is None
    assert staged["meta"]["control_bbox"] == [0.4, 1.0, 0.6]
    assert staged["meta"]["model"] == "Hunyuan3D-Omni"


def test_the_voxel_route_swaps_step_two_on_the_same_control_file(mods, monkeypatch, tmp_path):
    """The third mode costs one table entry, one graph and no exporter: the mesh `mesh_geom_ctrl` uploads is the
    mesh `mesh_geom_voxel` uploads, so the only thing that decides between them is the named mode."""
    comfy, _ = mods
    calls = []

    def fake(name):
        def record(*args, **kwargs):
            calls.append(name)
            path = str(tmp_path / f"{name}.glb")
            open(path, "wb").close()
            return {"path": path, "seconds": 1.0}
        return record

    monkeypatch.setattr(comfy, "mesh_geom_ctrl", fake("w7"))
    monkeypatch.setattr(comfy, "mesh_geom_voxel", fake("w7v"))
    monkeypatch.setattr(comfy, "mesh_simplify_uv", fake("w9c"))
    monkeypatch.setattr(comfy, "mesh_texture", fake("w9t"))
    subject = tmp_path / "subject.png"
    subject.write_bytes(b"")
    control = tmp_path / "blockout.glb"
    control.write_bytes(b"")

    staged = comfy.generate_asset_chain("a rock", str(tmp_path / "pack"), subject=str(subject),
                                        control=str(control), control_mode="voxel")
    assert calls == ["w7v", "w9c", "w9t"]
    assert staged["meta"]["workflows"] == ["mesh_subject", "mesh_geom_voxel", "mesh_simplify_uv",
                                           "mesh_texture"]
    assert staged["meta"]["control_mode"] == "voxel"
    assert staged["meta"]["control"] == str(control)
    assert staged["meta"]["model"] == "Hunyuan3D-Omni"


def test_stage_exports_counts_every_trellis_write_in_the_chain(mods):
    """Each `Trellis2ExportTrimesh` glb write turns the subject -90 degrees about X and the turns
    ACCUMULATE, so the staged route hands over three files in three different frames (measured hop by
    hop at the control gate). Two separate consequences ride on this mapping: the dense and the low mesh have to
    land in one frame or the bake reads across a rotated cage, and a block-out asset has to land in
    the block-out's own frame or it does not drop into the layout."""
    comfy, _ = mods
    staged = {"meta": {}, "simplified_mesh": "s.glb", "textured_mesh": "t.glb"}
    # No control: relative only, so the raw mesh's own frame is left exactly as it is today and the
    # later files are merely brought into line with it.
    assert comfy.stage_exports(staged) == {"raw": 0, "simplified": 1, "textured": 2}
    # With one: absolute as well, because now the incoming orientation is the whole point.
    staged["meta"]["control"] = "blockout.glb"
    assert comfy.stage_exports(staged) == {"raw": 1, "simplified": 2, "textured": 3}
    # And the bbox control counts the same, though it uploads no file: the turn comes from the
    # exporter both Omni routes end at, so reading "is there a control file" lays the asset on its
    # side (the bbox gate).
    staged["meta"] = {"control": None, "control_bbox": [0.4, 1.0, 0.6]}
    assert comfy.stage_exports(staged) == {"raw": 1, "simplified": 2, "textured": 3}
    # And so does the voxel mode, which is back in the mesh form: same rule, same exporter.
    staged["meta"] = {"control": "blockout.glb", "control_bbox": None, "control_mode": "voxel"}
    assert comfy.stage_exports(staged) == {"raw": 1, "simplified": 2, "textured": 3}
    # The one-shot route returns ONE file that is both meshes, and takes no control.
    assert comfy.stage_exports({"meta": {}, "textured_mesh": "one.glb"}) == {"raw": 0,
                                                                            "simplified": 0}


def test_free_vram_survives_an_empty_two_hundred(mods, fake_server):
    """`POST /free` answers 200 with no body at all (measured: zero bytes, no content type). Reading
    that as non-JSON is what made the Advanced panel's Free VRAM button report an error on every
    successful press, so an empty 200 is a value now, not a failure."""
    comfy, _ = mods
    assert comfy.free(url=fake_server) is True
    assert "/free" in _Fake.calls


# -- The macro-heightmap family: the terrain macro mask (`heightmap_macro`) ---------------------------------------------------
def test_the_macro_mask_is_the_low_band_of_the_same_cutoff(mods):
    """The macro-heightmap family needed no derivation module, and this is the reason: it is `relief()` read from the
    other side. Same luminance, same box blur, the opposite half of one split."""
    _comfy, maps = mods
    n = 192
    y, x = np.mgrid[0:n, 0:n] / n
    rng = np.random.default_rng(5)
    field = 0.8 * np.exp(-(((x - 0.3) ** 2 + (y - 0.3) ** 2) / 0.02))
    noisy = np.clip(0.1 + field + 0.3 * rng.random((n, n)), 0.0, 1.0)
    rgb = (np.repeat(noisy[:, :, None], 3, axis=2) * 255).astype(np.uint8)

    macro = maps.macro_field(rgb)
    relief = maps.relief(rgb)
    assert macro.shape == (n, n) and macro.dtype == np.float32
    # It spans the whole range, because the stack reads it as an elevation ordering.
    assert macro.min() < 0.02 and macro.max() > 0.98
    # And it is not the high band: the two are all but uncorrelated on the same image.
    both = np.corrcoef(macro.ravel(), relief.ravel())[0, 1]
    assert abs(both) < 0.3, f"macro and relief correlate at {both:+.3f}"
    # The mask is smooth where the source was not: adjacent-cell steps collapse.
    assert np.abs(np.diff(macro, axis=1)).max() < 0.15 * np.abs(np.diff(noisy, axis=1)).max()
    assert maps.macro_from(rgb).dtype == np.uint8


def test_a_flat_generation_gives_half_not_amplified_noise(mods):
    """The same floor `_normalise` has, for the same reason: percentile-stretching a flat field would
    amplify float rounding noise into a full-range landform the prompt never asked for."""
    _comfy, maps = mods
    flat = np.full((64, 64, 3), 130, dtype=np.uint8)
    assert np.allclose(maps.macro_field(flat), 0.5)


def test_the_macro_blur_wraps_only_when_the_route_asks(mods):
    """A terrain tile is not a torus, so the mask's blur replicates its edge by default. The tiled
    route is the exception and it has to actually be tileable when chosen."""
    _comfy, maps = mods
    rng = np.random.default_rng(11)
    n = 128
    ramp = np.linspace(0.0, 1.0, n, dtype=np.float32)[None, :].repeat(n, axis=0)
    rgb = (np.repeat(np.clip(ramp + 0.1 * rng.random((n, n)), 0, 1)[:, :, None], 3, axis=2)
           * 255).astype(np.uint8)
    open_seam = maps.seam_report(maps.macro_from(rgb, wrap=False))["ratio"]
    tiled_seam = maps.seam_report(maps.macro_from(rgb, wrap=True))["ratio"]
    assert tiled_seam < open_seam, f"tiled {tiled_seam:.2f} against open {open_seam:.2f}"


def test_the_macro_route_is_a_value_and_the_open_one_drops_the_tiling(mods):
    """`heightmap_macro`'s tiling pair ships IN the graph so preflight covers those classes, and the route decides
    per press. Dropping the nodes rather than switching them to "disable" is the `BOB_LORA` argument
    (the portability rule): a disabled node still has to name an installed pack."""
    comfy, _ = mods
    assert set(comfy.MACRO_ROUTES) == {"open", "tiled"}
    assert comfy.DEFAULT_MACRO_ROUTE == "open"
    assert comfy.macro_tiling() is False and comfy.macro_tiling("tiled") is True
    with pytest.raises(comfy.ComfyError, match="unknown macro route"):
        comfy.macro_tiling("wrap")

    prompt, prov = comfy.load_workflow(str(WORKFLOWS / "heightmap_macro.json"))
    classes = {node["class_type"] for node in prompt.values()}
    assert {"SeamlessTile", "MakeCircularVAE"} <= classes
    open_graph = comfy.drop_node(comfy.drop_node(prompt, "BOB_TILE", {0: "model"}),
                                "BOB_TILE_VAE", {0: "vae"})
    assert not ({"SeamlessTile", "MakeCircularVAE"}
                & {node["class_type"] for node in open_graph.values()})
    # The chain is rewired, not broken: the sampler reads the checkpoint's model directly now.
    titles = comfy.titles(open_graph)
    assert open_graph[titles["BOB_SEED"]]["inputs"]["model"] == [titles["BOB_CKPT"], 0]
    assert open_graph[titles["BOB_DECODE"]]["inputs"]["vae"] == [titles["BOB_CKPT"], 2]
    assert prov["default_checkpoint"].endswith(".safetensors")


def test_heightmap_macro_writes_an_eight_bit_mask_and_its_provenance(mods, monkeypatch, tmp_path):
    """The whole Bob half of the macro-heightmap family: one graph, one cutoff, one 8-bit PNG, one sidecar. 8-bit is the
    decision the bit-depth floor asked for and the macro-mask gate measured, so the sidecar records the cutoff that makes it a mask."""
    comfy, maps = mods
    seen = {}
    n = 256
    y, x = np.mgrid[0:n, 0:n] / n
    field = np.exp(-(((x - 0.4) ** 2 + (y - 0.4) ** 2) / 0.05))
    rgb = (np.repeat(field[:, :, None], 3, axis=2) * 255).astype(np.uint8)
    png = _paeth_png(rgb)   # what PIL, and so ComfyUI, actually emits

    def fake_generate(workflow, values, **kwargs):
        graph, _prov = workflow
        seen["classes"] = {node["class_type"] for node in graph.values()}
        seen["values"] = values
        return png, {"prompt_id": "p9", "seconds": 4.5}

    monkeypatch.setattr(comfy, "generate_image", fake_generate)
    out = tmp_path / "basin_macro.png"
    info = comfy.heightmap_macro("a broad basin", str(out), seed=17)

    assert out.exists() and info["tiled"] is False
    assert "SeamlessTile" not in seen["classes"], "the open route drops the tiling"
    assert comfy.MACRO_SUFFIX in seen["values"]["BOB_PROMPT"]["text"]
    assert seen["values"]["BOB_SEED"] == {"seed": 17}
    written = maps.read_png(out.read_bytes())
    assert written.ndim == 2 and written.dtype == np.uint8, "one channel, 8 bits"
    assert written.min() < 5 and written.max() > 250
    side = json.loads((tmp_path / "basin_macro.json").read_text())
    assert side["route"] == "open" and side["seed"] == 17
    assert side["lowpass_fraction"] == maps.MACRO_LOWPASS_FRACTION
    assert "MASK" in side["note"] and info["source"] is None

    # keep_source is what makes the 8-bit claim auditable rather than asserted; off by default.
    info = comfy.heightmap_macro("a broad basin", str(tmp_path / "again.png"), keep_source=True)
    assert info["source"] and os.path.exists(info["source"])


# -- Websocket progress (the agent-surface gate, core/comfy_ws.py) ------------------------------------------------
# The reader is hand-rolled because the client is stdlib-only (Bob-side constraint 1), so the frame
# framing is Bob's code and has to be tested as such. What is NOT tested here is termination, on
# purpose: `wait` decides a job is finished from the jobs API, and the last test below is the one that
# proves a missing websocket costs progress detail and nothing else.
def _ws_frame(payload, opcode=0x1, fin=True):
    """A server-to-client frame: unmasked, which is what the reader has to accept."""
    head = bytes([(0x80 if fin else 0) | opcode])
    if len(payload) < 126:
        head += bytes([len(payload)])
    else:
        head += bytes([126]) + struct.pack(">H", len(payload))
    return head + payload


@pytest.fixture
def ws_server():
    """A socket that completes the websocket handshake and then sends whatever it is told to.

    `frames` is set by each test before connecting; `pongs` records what the client sent back, which
    is how the ping reply is measured rather than assumed.
    """
    import socket

    state = {"frames": [], "pongs": [], "upgrade": True}
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)

    def serve():
        try:
            conn, _ = listener.accept()
        except OSError:
            return
        head = b""
        while b"\r\n\r\n" not in head:
            chunk = conn.recv(4096)
            if not chunk:
                return
            head += chunk
        state["request"] = head.decode("latin-1")
        if not state["upgrade"]:
            conn.sendall(b"HTTP/1.1 404 Not Found\r\nContent-Length: 0\r\n\r\n")
            conn.close()
            return
        conn.sendall(b"HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\n"
                     b"Connection: Upgrade\r\n\r\n")
        for frame in state["frames"]:
            conn.sendall(frame)
        try:
            conn.settimeout(1.0)
            state["pongs"].append(conn.recv(4096))
        except OSError:
            pass

    thread = threading.Thread(target=serve, daemon=True)
    state["start"] = thread.start
    state["url"] = f"http://127.0.0.1:{listener.getsockname()[1]}"
    yield state
    try:
        listener.close()
    except OSError:
        pass


@pytest.fixture(scope="module")
def ws(mods):
    sys.path.insert(0, str(CORE))
    return importlib.import_module("comfy_ws")


def test_ws_reads_text_frames_and_reassembles_a_fragmented_one(ws, ws_server):
    ws_server["frames"] = [
        _ws_frame(b'{"type": "progress", "data": {"value": 3, "max": 20}}'),
        _ws_frame(b'{"type": "exec', opcode=0x1, fin=False),   # fragment 1
        _ws_frame(b'uting", "data": {"node": "12"}}', opcode=0x0),  # continuation, fin
        _ws_frame(b"\x89PNG-preview-bytes", opcode=0x2),  # a live preview; must be dropped
    ]
    ws_server["start"]()
    sock = ws.connect(ws_server["url"], "bob-test")
    assert sock is not None, "the handshake should complete"
    assert "clientId=bob-test" in ws_server["request"]
    seen = []
    sock.pump(1.0, seen.append)
    sock.close()
    assert [e["type"] for e in seen] == ["progress", "executing"], "binary frames are dropped"
    assert seen[1]["data"]["node"] == "12", "a fragmented message reassembles"


def test_ws_answers_a_ping_with_a_masked_pong(ws, ws_server):
    ws_server["frames"] = [_ws_frame(b"hb", opcode=0x9),
                           _ws_frame(b'{"type": "status", "data": {}}')]
    ws_server["start"]()
    sock = ws.connect(ws_server["url"], "bob-test")
    seen = []
    sock.pump(1.0, seen.append)
    sock.close()
    time.sleep(0.2)
    assert seen and seen[0]["type"] == "status"
    reply = b"".join(ws_server["pongs"])
    assert reply[:1] == b"\x8a", "opcode 10, pong"
    assert reply[1] & 0x80, "a client frame MUST be masked (RFC 6455 5.3)"


def test_ws_connect_returns_none_when_the_server_will_not_upgrade(ws, ws_server):
    ws_server["upgrade"] = False
    ws_server["start"]()
    assert ws.connect(ws_server["url"], "bob-test") is None, "None, not an exception"


def test_progress_text_reads_the_event_vocabulary_and_ignores_the_rest(ws):
    assert ws.progress_text({"type": "progress", "data": {"value": 7, "max": 20}}) == "step 7/20"
    assert ws.progress_text({"type": "executing", "data": {"node": "5"}}) == "node 5"
    assert ws.progress_text({"type": "execution_cached", "data": {"nodes": ["1", "2"]}}) \
        == "2 cached"
    assert ws.progress_text({"type": "status", "data": {
        "status": {"exec_info": {"queue_remaining": 3}}}}) == "queued, 3 ahead"
    # A queue of one is this job, so it is not news; and an unknown type is not guessed at.
    assert ws.progress_text({"type": "status", "data": {
        "status": {"exec_info": {"queue_remaining": 1}}}}) is None
    assert ws.progress_text({"type": "progress_state", "data": {"nodes": {}}}) is None
    # Another client's job on the same server is filtered out by prompt id.
    event = {"type": "progress", "data": {"value": 1, "max": 4, "prompt_id": "other"}}
    assert ws.progress_text(event, prompt_id="mine") is None
    assert ws.progress_text(event, prompt_id="other") == "step 1/4"


def test_wait_still_finishes_when_there_is_no_websocket(mods, fake_server):
    """The safety property the design rests on: the fake serves no /ws, so `connect` returns None and
    `wait` falls back to the status string. A dropped or absent socket must cost granularity and never
    a result."""
    comfy, _ = mods
    seen = []
    outputs = comfy.wait("p1", url=fake_server, timeout=10, poll=0.01, on_progress=seen.append)
    assert comfy.images(outputs)[0]["filename"] == "a.png"
    assert seen and set(seen) <= {"pending", "in_progress"}, "status strings, not per-node detail"


# -- Circular padding applied in place, and undone (the agent-surface gate, the copied-VAE segfault) --------------
# The crash this exists for is in ComfyUI, not in Bob: a deepcopied VAE owns a staged host buffer
# whose destructor faults, and it kills the server on the second decode of a session. Bob's half of
# the fix is to stop asking for the copy, which mutates the SESSION's shared model instead -- so the
# tests that matter are the ones proving the mutation is undone before anything that must not wrap.
def test_the_tiling_binding_is_a_value_in_one_place(mods):
    comfy, _ = mods
    on, off = comfy.tiling_values(True), comfy.tiling_values(False)
    assert on[comfy.TILE_TITLE]["tiling"] == "enable"
    assert off[comfy.TILE_VAE_TITLE]["tiling"] == "disable"
    # BOTH nodes in place, and both of them matter: `tex_tileable` copies the UNet as well as the VAE, so a fix
    # that only spared the VAE would still deepcopy 5 GB of SDXL and still crash.
    assert on[comfy.TILE_TITLE]["copy_model"] == comfy.TILING_COPY_MODE
    assert on[comfy.TILE_VAE_TITLE]["copy_vae"] == comfy.TILING_COPY_MODE
    assert comfy.TILING_COPY_MODE == "Modify in place"


def test_a_texture_set_binds_tiling_on_and_marks_the_model_dirty(mods, monkeypatch, tmp_path):
    comfy, _ = mods
    seen = {}

    def fake_generate(workflow, values, **kwargs):
        graph, _prov = workflow
        seen.setdefault("calls", []).append(values)
        n = 8
        rgb = np.zeros((n, n, 3), dtype=np.uint8)
        return _paeth_png(rgb), {"prompt_id": "p1", "seconds": 1.0}

    monkeypatch.setattr(comfy, "generate_image", fake_generate)
    comfy.mark_tiling_applied(None, False)
    comfy.texture_variant("stone", str(tmp_path / "set"), seed=1)
    values = seen["calls"][-1]
    assert values[comfy.TILE_TITLE] == {"tiling": "enable", "copy_model": "Modify in place"}
    assert comfy._TILING_DIRTY[comfy.base_url(None)] is True, "the shared model is now padded"


def test_a_subject_image_resets_the_padding_first(mods, monkeypatch, tmp_path):
    """`mesh_subject` must not wrap: it is one centred object, and a circular UNet carries its edge round the
    frame. Measured at the agent-surface gate without this: seam ratio 1.059, i.e. tiled, where untiled reads 3.9 to 8.5."""
    comfy, _ = mods
    order = []

    def fake_generate(workflow, values, **kwargs):
        order.append("reset" if values.get(comfy.TILE_TITLE, {}).get("tiling") == "disable"
                     else "subject")
        return _paeth_png(np.zeros((8, 8, 3), dtype=np.uint8)), {"prompt_id": "p", "seconds": 1.0}

    monkeypatch.setattr(comfy, "generate_image", fake_generate)
    comfy.mark_tiling_applied(None, True)          # a texture set ran earlier in the session
    comfy.subject_image("a boulder", str(tmp_path / "s.png"), seed=1)
    assert order == ["reset", "subject"], "the reset runs BEFORE the subject, not after"
    assert comfy._TILING_DIRTY[comfy.base_url(None)] is False

    # Lazy: a second subject in the same session pays nothing, which is what makes this cheap.
    order.clear()
    comfy.subject_image("a log", str(tmp_path / "s2.png"), seed=2)
    assert order == ["subject"]


def test_the_open_macro_route_resets_too(mods, monkeypatch, tmp_path):
    """The open route DROPS the padding nodes, so it runs on the shared model. Without the reset a
    texture set earlier in the session would make the mask tile, and the macro-mask gate measured that a tiling macro
    mask repeats the landform across the border (seam 0.80 tiled against 86.18 open)."""
    comfy, maps = mods
    order = []

    def fake_generate(workflow, values, **kwargs):
        graph, _prov = workflow
        classes = {n["class_type"] for n in graph.values()}
        order.append("reset" if "SeamlessTile" in classes else "macro")
        n = 64
        y, x = np.mgrid[0:n, 0:n] / n
        rgb = (np.repeat((x * 0.8 + 0.1)[:, :, None], 3, axis=2) * 255).astype(np.uint8)
        return _paeth_png(rgb), {"prompt_id": "p", "seconds": 1.0}

    monkeypatch.setattr(comfy, "generate_image", fake_generate)
    comfy.mark_tiling_applied(None, True)
    comfy.heightmap_macro("a massif", str(tmp_path / "m.png"), seed=1)  # open is the default
    assert order == ["reset", "macro"]


def test_a_failed_reset_does_not_stop_the_generation(mods, monkeypatch, tmp_path):
    """The reset is a convenience, not a precondition: if it cannot run, the caller still gets the
    image it asked for, and the dirty flag stays set so the next call tries again."""
    comfy, _ = mods
    calls = []

    def fake_generate(workflow, values, **kwargs):
        if values.get(comfy.TILE_TITLE, {}).get("tiling") == "disable":
            raise comfy.ComfyError("reset could not run")
        calls.append("subject")
        return _paeth_png(np.zeros((8, 8, 3), dtype=np.uint8)), {"prompt_id": "p", "seconds": 1.0}

    monkeypatch.setattr(comfy, "generate_image", fake_generate)
    comfy.mark_tiling_applied(None, True)
    comfy.subject_image("a boulder", str(tmp_path / "s.png"), seed=1)
    assert calls == ["subject"]
    assert comfy._TILING_DIRTY[comfy.base_url(None)] is True, "still dirty, so it retries"


# -- the VRAM-handback rule: the VRAM floors and the recovery report ------------------------------------------------
# The redwood run's first finding, as tests. The old behaviour was a CUDA traceback from inside
# somebody else's worker process 90 seconds into a job; the contract now is a sentence before the
# job is queued, and a Free VRAM that reports the number it recovered instead of the word "Freed".
@pytest.fixture
def card(mods, monkeypatch):
    """A stubbed server whose free VRAM the test drives, counting the /free calls."""
    comfy, _ = mods
    state = {"free_mib": 12000, "frees": 0, "gives_back": 0}

    def status(url=None, timeout=3):
        return {"ok": True, "url": "stub", "device": "stub", "vram_free_mib": state["free_mib"],
                "running": 0, "pending": 0, "detail": ""}

    def free(url=None, unload_models=True, free_memory=True):
        state["frees"] += 1
        state["free_mib"] += state["gives_back"]
        return True

    monkeypatch.setattr(comfy, "service_status", status)
    monkeypatch.setattr(comfy, "free", free)
    return state


def test_every_generation_route_has_a_floor_and_hero_is_higher(mods):
    comfy, _ = mods
    assert set(comfy.VRAM_FLOOR_MIB) == {"mesh", "mesh_hero", "texture", "paint", "heightmap",
                                         "stylize"}
    # The hero tier is 1536_cascade and needs materially more than the default 1024, so sharing the
    # mesh floor would let it through and then OOM, which is the failure the floors exist to stop.
    assert comfy.VRAM_FLOOR_MIB["mesh_hero"] > comfy.VRAM_FLOOR_MIB["mesh"]


def test_preflight_passes_a_card_with_room_and_never_frees_it(mods, card):
    comfy, _ = mods
    assert comfy.preflight_vram("mesh") == 12000
    assert card["frees"] == 0, "a card with room must not be disturbed"


def test_preflight_tries_one_recovery_before_it_refuses(mods, card):
    """The common case is a card a previous job left full that one POST /free fixes, so refusing
    without trying would block work that would have run."""
    comfy, _ = mods
    card["free_mib"], card["gives_back"] = 1200, 6000
    assert comfy.preflight_vram("mesh") == 7200
    assert card["frees"] == 1


def test_preflight_refuses_with_a_vram_sentence_when_the_free_does_not_help(mods, card):
    """The measured case: /free returns success and about 100 MiB, because the pages stay in the
    main process's allocator and the generation workers are separate processes."""
    comfy, _ = mods
    card["free_mib"], card["gives_back"] = 900, 100
    with pytest.raises(comfy.ComfyError) as exc:
        comfy.preflight_vram("mesh")
    msg = str(exc.value)
    assert "not enough free VRAM for the mesh route" in msg
    assert "1000 MiB free, 5000 MiB needed" in msg
    assert "Restart ComfyUI" in msg and "expandable_segments:True" in msg
    assert card["frees"] == 1


def test_a_card_that_cannot_report_its_vram_is_let_through(mods, monkeypatch):
    """An unknown is not a reason to block work: a fork or a CPU-only server that reports no device
    memory would otherwise be unable to generate at all."""
    comfy, _ = mods
    monkeypatch.setattr(comfy, "service_status",
                        lambda url=None, timeout=3: {"ok": True, "vram_free_mib": None})
    assert comfy.preflight_vram("mesh") is None
    assert comfy.preflight_vram("no_such_route") is None, "an unknown route has no floor"


def test_recover_vram_reports_what_it_actually_got_back(mods, card):
    comfy, _ = mods
    card["free_mib"], card["gives_back"] = 500, 100
    got = comfy.recover_vram(target_mib=5000)
    assert (got["before"], got["after"], got["recovered"]) == (500, 600, 100)
    assert got["enough"] is False and "Restart ComfyUI" in got["advice"]
    # Enough, so no advice: the button says the number and stops talking.
    card["free_mib"], card["gives_back"] = 4000, 3000
    got = comfy.recover_vram(target_mib=5000)
    assert got["enough"] is True and got["advice"] == ""


def test_recover_vram_survives_a_server_that_will_not_free(mods, card, monkeypatch):
    """A dead endpoint is a report, not a traceback: this runs inside preflight, and a card that
    cannot be freed must still produce the sentence that names the restart."""
    comfy, _ = mods

    def boom(url=None, unload_models=True, free_memory=True):
        raise comfy.ComfyError("not reachable")

    monkeypatch.setattr(comfy, "free", boom)
    card["free_mib"] = 400
    got = comfy.recover_vram(target_mib=5000)
    assert got["enough"] is False and "could not reach the free endpoint" in got["advice"]
    assert got["before"] == got["after"] == 400


# -- the dead-wood routing rule: the leaf-opacity receipt warning -------------------------------------------------------
def test_leaf_opacity_warning_fires_on_the_kinds_whose_look_is_leaves(mods):
    """LEAFY_KINDS deliberately differs from FOLIAGE_KINDS: that one is about keeping holes open
    through remesh and pinhole fill (plants, grass); this one is about the finished LOOK, and a tree
    is in it because the crown is the reason it was generated."""
    comfy, _ = mods
    assert set(comfy.LEAFY_KINDS) == {"trees", "plants", "grass"}
    assert set(comfy.FOLIAGE_KINDS) < set(comfy.LEAFY_KINDS)
    # The measured redwood verdicts, both of them.
    for verdict in ("opaque", "implausible", None):
        warns = comfy.leaf_opacity_warning("trees", {"verdict": verdict})
        assert len(warns) == 1 and "reads as solid geometry" in warns[0]
        assert str(verdict or "none") in warns[0], "the receipt names WHICH case this was"
    assert comfy.leaf_opacity_warning("trees", {"verdict": "cutout"}) == []
    # A rock has no crown to be wrong about, whatever its alpha says.
    assert comfy.leaf_opacity_warning("rocks", {"verdict": "opaque"}) == []
    assert comfy.leaf_opacity_warning("grass", {}) and comfy.leaf_opacity_warning("plants", None)


# -- The two undocumented ceilings (the redwood run, item 13) ------------------------------------
def test_control_bbox_range_is_the_nodes_own_widget_bound(mods):
    """Not a Bob policy: `Hy3DOmniVoxelGenerate` declares min 0.1 max 3.0 on each of the three, and
    ComfyUI validates widget bounds server-side, so an out-of-range value is an HTTP 400 rather than
    a clamp. `gen_assets.control_bbox` divides by the longest axis, which is why its own output
    always fits and a hand-written [1, 9, 1] does not."""
    comfy, _ = mods
    lo, hi = comfy.CONTROL_BBOX_RANGE
    assert (lo, hi) == (0.1, 3.0)
    assert all(lo <= d <= hi for d in (0.35, 1.0, 0.35))
    assert not all(lo <= d <= hi for d in (1.0, 9.0, 1.0))


def test_a_mesh_not_found_failure_names_the_variable_that_causes_it(mods, monkeypatch):
    """"Mesh file not found: input/3d/x.glb" names neither $BOB_COMFY_DIR nor the reason, and the
    redwood run read it as a bad control mesh. The hint is attached only when the variable really is
    unset, so a genuinely missing file on a configured machine is not misdiagnosed."""
    comfy, _ = mods
    monkeypatch.setattr(comfy, "comfy_dir", lambda: None)
    hint = comfy._mesh_transport_hint("Mesh file not found: input/3d/proxy.glb")
    assert "$BOB_COMFY_DIR" in hint and "input/3d" in hint and "control_bbox" in hint
    assert comfy._mesh_transport_hint("CUDA out of memory") == ""
    monkeypatch.setattr(comfy, "comfy_dir", lambda: "/srv/ComfyUI")
    assert comfy._mesh_transport_hint("Mesh file not found: input/3d/proxy.glb") == ""


# -- Prompt ergonomics: the negative reaches the one stage a negation works at -------------------
def test_the_negative_reaches_w4_and_only_w4(mods, monkeypatch, tmp_path):
    """SDXL does not honour negations in the positive prompt ("no pot, no planter" returned a
    nursery pot twice), and the subject image is the only stage any text touches: every geometry
    graph downstream conditions on the picture. So the argument has to arrive at `mesh_subject` or it does
    nothing at all."""
    comfy, _ = mods
    seen = {}

    def fake_subject(prompt_text, out_path, **kw):
        seen.update(kw, prompt=prompt_text)
        return {"path": out_path, "seconds": 1.0, "prompt": prompt_text}

    monkeypatch.setattr(comfy, "subject_image", fake_subject)
    comfy._stage_subject("a fir sapling", str(tmp_path), negative="pot, planter, hands")
    assert seen["negative"] == "pot, planter, hands"

    # And every chain accepts it, so no route silently drops the argument.
    import inspect
    for fn in (comfy.generate_asset_oneshot, comfy.generate_asset_chain, comfy.generate_asset_alt,
               comfy.generate_asset_source):
        assert "negative" in inspect.signature(fn).parameters, fn.__name__


def test_a_supplied_subject_skips_w4_so_the_negative_is_moot(mods, tmp_path):
    comfy, _ = mods
    got = comfy._stage_subject("a fir", str(tmp_path), subject="/tmp/mine.png", negative="pot")
    assert got["path"] == "/tmp/mine.png" and got["seconds"] == 0.0
