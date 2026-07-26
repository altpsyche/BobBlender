"""ComfyUI client and map-derivation tests (G1, docs/COMFYUI.md).

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


def test_roughness_uses_the_whole_band_on_a_bright_albedo(mods):
    """G1's defect, as a test. A bright albedo used to park every pixel at the top of the band
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
    """What W3 needs: an operation that pads at the border makes the two copies of each edge band
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
    """W1's contract: it loads, every BOB_* title is unique, and it names no cloud node."""
    comfy, _ = mods
    prompt, prov = comfy.load_workflow(str(WORKFLOWS / "tex_tileable.json"))
    assert prov.get("derived_from"), "a shipped graph records the template it came from (R17)"
    names = [t for t in comfy.titles(prompt) if t and t.startswith("BOB_")]
    assert len(names) == len(set(names)), "BOB_* titles must be unique (R12)"
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
    # The jobs API was polled, not /history: the per-job primitive is the point (R5).
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
        # The NEWER combo shape, options hidden in the options dict. G1's reader saw only the old
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
    """The check that keeps local-only true over time rather than by intention (R18/D7)."""
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
    """The shipped-graph assertion the plan asks for (R12/R18), run offline against the committed
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
        assert prov.get("derived_from"), f"{path.name} records no upstream template (R17)"


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
    """R15: a result that lands after load_post must not run against the new file."""
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


# -- Mesh transport (G3) ------------------------------------------------------------------------
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
    directory listing that will not have seen the file (G0.5)."""
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
    """The sign that decides whether W4 saves the subject or the background.

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
    overridden. And `target_face_count` is a binding point on W9b but not on W5t: on W9b it IS the
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
    """W9b's shape, as the benchmark relies on it: one graph that conditions, generates a shape,
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
    """Both routes reach `finish_asset` through one mapping, which is what makes the G3b verdict a
    config change. The one-shot route's single file goes in as the SIMPLIFIED mesh with no texture
    pass: passing it as `texture_pass` instead would have Blender decimate and unwrap a mesh it is
    about to throw away."""
    comfy, _ = mods
    assert comfy.DEFAULT_ASSET_ROUTE == "oneshot", "G3b's verdict"
    assert set(comfy.ASSET_ROUTES) == {"oneshot", "staged"}
    assert comfy.asset_chain() is comfy.generate_asset_oneshot
    assert comfy.asset_chain("staged") is comfy.generate_asset_chain
    assert comfy.finish_passes({"raw_mesh": "r.glb", "textured_mesh": "t.glb"}) == ("t.glb", None)
    assert comfy.finish_passes({"raw_mesh": "r.glb", "simplified_mesh": "s.glb",
                                "textured_mesh": "t.glb"}) == ("s.glb", "t.glb")


# -- The UI-to-API converter (G3 corrections) ----------------------------------------------------
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


# -- Tracks D and B stylised, plus multi-view (G4) ------------------------------------------------
def test_drop_node_removes_the_lora_and_rewires_its_consumers(mods):
    """The reason a LoRA is a graph EDIT and not a zero strength: `LoraLoader` still has to name an
    installed file, and no shipped default can know what is installed on this machine (R6)."""
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
    """W12's shape, and the one thing about it that is not obvious: there is no standalone SDXL
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
    """W9 grows out of W12, which is why W12 is built first: the difference is the IPAdapter that
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
    W12 runs, omit them and W12e runs the estimators over the render itself."""
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
    """The cheap half of R20's consistency mitigation, and the ordering IS the mitigation: the front
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
    """W9-as-a-paint-route lands in the shape G3b gave the geometry decision: one place where the
    route becomes a decision, not a second operator."""
    comfy, _ = mods
    assert set(comfy.TEXTURE_ROUTES) == {"pbr", "stylised"}
    assert comfy.DEFAULT_TEXTURE_ROUTE == "pbr"
    assert comfy.texture_chain() is comfy.mesh_texture
    assert comfy.texture_chain("stylised") is comfy.paint_views
    # And the asset route is untouched by it.
    assert comfy.asset_chain() is comfy.generate_asset_oneshot


def test_the_two_multi_view_graphs_take_the_same_four_views(mods):
    """W6 and W6t exist to be compared, so they have to take one set of renders: same titles, same
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
    """W7's shape, and the three things about it that are easy to get wrong.

    The control is a MESH read by `Trellis2LoadMesh`, because the Omni pack ships no loader and its
    socket is TRELLIS.2's `TRIMESH` type; the point budget is named rather than left at the node's
    own default of 'use the raw vertices'; and the model path ships as a HuggingFace repo id so the
    graph is portable to a machine whose weights live somewhere else (R6)."""
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

    # Same retrievable tail as W5t: the export node reports a STRING, Preview3D makes it an output.
    assert graph[by_title["BOB_OUT"]]["class_type"] == "Trellis2ExportTrimesh"
    assert graph[by_title["BOB_OUT"]]["inputs"]["trimesh"] == [by_title["BOB_SEED"], 0]
    assert graph[by_title["BOB_VIEW"]]["inputs"]["model_file"] == [by_title["BOB_OUT"], 0]
    assert set(prov["runtime_inputs"]) == {"BOB_IMAGE.image", "BOB_CONTROL.mesh_path",
                                           "BOB_OMNI.repo_or_path"}


def test_w7_binds_the_local_weights_only_when_they_are_there(mods, monkeypatch, tmp_path):
    """`omni_model_dir` is the R6 rule in one function: a local absolute path when this machine has
    the weights, and the graph's own portable default when it does not."""
    comfy, _ = mods
    monkeypatch.setattr(comfy, "_PREF_COMFY_DIR", None)
    assert comfy.omni_model_dir() is None
    monkeypatch.setattr(comfy, "_PREF_COMFY_DIR", str(tmp_path))
    assert comfy.omni_model_dir() is None, "no weights directory means no binding"
    (tmp_path / "models" / "hunyuan3d-omni").mkdir(parents=True)
    assert comfy.omni_model_dir() == str(tmp_path / "models" / "hunyuan3d-omni")


def test_the_block_out_route_swaps_step_two_and_nothing_else(mods, monkeypatch, tmp_path):
    """`control` is a value on the staged chain, not a fourth route: W5t becomes W7 and every stage
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


def test_stage_exports_counts_every_trellis_write_in_the_chain(mods):
    """Each `Trellis2ExportTrimesh` glb write turns the subject -90 degrees about X and the turns
    ACCUMULATE, so the staged route hands over three files in three different frames (measured hop by
    hop at G4c). Two separate consequences ride on this mapping: the dense and the low mesh have to
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
