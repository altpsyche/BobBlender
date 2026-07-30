"""Stdlib-only ComfyUI client, preflight, and the texture-set recipe that uses them.

Two Python worlds share this file (docs/GENERATION.md, Bob-side constraint 1): `bpy` runs on
Blender's bundled interpreter, which has no `httpx`, and the tools venv has its own. So the client
is `urllib.request` and `json` only and lives HERE, inside the extension, as the single source --
the same shape `core/heightfields` already uses. Nothing in this module imports `bpy`, so the
headless scripts and the venv can drive it directly.

ComfyUI is never required. Every entry point either returns a value or raises `ComfyError` with a
sentence a panel can print; `reachable()` is the cheap check a UI row uses to read "not connected"
and change nothing else.

Job status comes from this fork's jobs API (`GET /api/jobs/{id}`, `POST /api/jobs/{id}/cancel`),
which is a proper per-job primitive rather than `/interrupt`'s "kill whatever is running" (the
cancellation rule). `/history/{id}` is the fallback for a vanilla upstream server that lacks it.

This module is the CLIENT plus the texture-set recipe. Job orchestration (the worker thread, the
timer tick, the registry that clears on a file load) is `core/comfy_jobs.py`; it calls in here and
nothing here calls back out, so the client stays drivable from a script with no scheduler. Per-node
progress comes from `core/comfy_ws.py`, which is advisory: `wait()` still decides a job is finished
from the jobs API, so a websocket that never connects costs granularity and nothing else.

`preflight()` is the highest-value function in the file. Every realistic failure -- a pack that is
not installed, a model that was never downloaded, a graph pasted in from a community workflow that
reaches for a cloud node, a subgraph, a title typo -- becomes a sentence before anything is queued,
instead of an HTTP 400 with a validator dump in it.
"""

import json
import mimetypes
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid as _uuid

try:
    from . import comfy_maps, comfy_ws
except ImportError:  # `core` itself on sys.path (the venv / headless route, core.heightfields' own
    import comfy_maps  # pattern via tools/bobtools/_hfpath.py), where there is no parent package
    import comfy_ws

# The server Bob talks to, most specific first: an explicit argument, then the addon preference
# (pushed in by the addon, which owns bpy, the same bpy-free hand-off `assets.set_pref_roots`
# uses), then the env var, then the default.
DEFAULT_URL = "http://127.0.0.1:8188"
_PREF_URL = None

# ComfyUI routes a job's progress events to the socket whose `clientId` matches the `client_id` the
# prompt was queued with, and it keys those sockets BY that id, so a second connection using the
# same id replaces the first. The pid makes the id per-process, which is the collision that actually
# happens here: the MCP server and a running Blender both drive the same ComfyUI. Within one process
# the integration runs one job at a time (16 GB, the VRAM-floor rule), so one socket per process is
# enough.
CLIENT_ID = f"bob_blender_tools-{os.getpid()}"


def set_pref_url(url):
    """Register the addon-preference ComfyUI URL. None or "" unregisters it."""
    global _PREF_URL
    _PREF_URL = (str(url).strip().rstrip("/") or None) if url else None


# The ComfyUI checkout, when the artist has pointed the preference at one and it is local. Only
# needed for Start Server; the mesh transport uses it too, because writing straight into
# `<comfy>/input/3d/` is both faster than a multipart POST and one less failure mode.
_PREF_COMFY_DIR = None


def set_pref_comfy_dir(path):
    """Register the addon-preference ComfyUI folder. None or "" unregisters it."""
    global _PREF_COMFY_DIR
    _PREF_COMFY_DIR = (str(path).strip() or None) if path else None


def comfy_dir():
    """The registered ComfyUI folder if it exists on this machine, else `$BOB_COMFY_DIR`, else None.

    The env fallback is the same shape and the same reason as `assets.generated_root`'s
    `$BOB_GENERATED` (the agent-surface gate): only the ADDON can register a preference, and two of
    the three processes this code runs in are not the addon. Without it the MCP server cannot
    transport a mesh at all, which the geometry A/B found by driving the `alt` route through the
    real tool: see `upload_mesh`.
    """
    for path in (_PREF_COMFY_DIR, os.environ.get("BOB_COMFY_DIR")):
        if path and os.path.isdir(str(path)):
            return str(path)
    return None


# Where the shipped graphs live. Derived from templates, API format, bound by node title.
WORKFLOW_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                            "assets", "workflows")

# `tex_tileable`'s prompt suffix. A generated albedo with baked lighting is unusable and no amount
# of Bob-side maths removes it, so the flat-lighting clause is not the artist's job to remember.
PROMPT_SUFFIX = ("seamless tileable texture, orthographic top view, flat even lighting, "
                 "no shadows, no vignette, no highlights")

# `mesh_subject`'s prompt suffix, and the same argument as PROMPT_SUFFIX: the geometry model's
# failure mode on a cropped, grouped or busy reference is silent and costs a whole 87 s generation,
# so the clause that prevents it is not the artist's job to remember.
SUBJECT_SUFFIX = ("single object, centred in frame, full view, not cropped, plain background, "
                  "even diffuse studio lighting, sharp focus")

# -- Circular padding: how the tiling nodes are bound, in one place --------------------------------
# `SeamlessTile` and `MakeCircularVAE` each offer "Make a copy" or "Modify in place", and which one
# Bob asks for is a VALUE here rather than a widget in four graphs, for the same reason
# `asset_chain()` and `macro_tiling()` are.
#
# **In place, and it is a crash fix rather than a preference.** "Make a copy" is the semantically
# clean choice and it is what the first spike shipped, but on a ComfyUI with dynamic VRAM staging
# enabled (`comfy-aimdo`, this fork's default) the deepcopy owns a staged host buffer whose
# destructor is unsafe: `comfy_aimdo/host_buffer.py.__del__` faults when the copy's buffers are
# released, and it is reached from inside `model_patcher.partially_load`, so the SECOND decode of a
# session takes the whole server down. Measured by the agent-surface gate, four ways:
#
#   copy + dynamic VRAM                        dead on the second decode, every time
#   copy + `POST /free` between jobs           dead on the second decode (the copy is still garbage)
#   copy + a strong reference held on it       dead on the fifth job (the fault is in the STAGING
#                                              path, not in garbage collection, so pinning the
#                                              object cannot keep its internal buffers alive)
#   copy + `--disable-dynamic-vram`            no crash, but the whole install loses staging
#   **in place**                               no crash, and staging stays on for every other route
#
# The control that decides it: with no copy anywhere, SDXL then a 15 GB TRELLIS.2 job then SDXL
# again runs clean under dynamic VRAM. So staging is not broken, the deepcopy is, and only these
# four graphs make one.
#
# What in place costs, and it is real: it mutates the SESSION's shared model, so the next graph on
# the same checkpoint inherits circular padding unless it is undone. The first spike named that
# hazard and the agent-surface gate measured it -- a `mesh_subject` subject image came back at seam
# ratio 1.059, i.e. wrapped, where an untiled frame is 3.9 to 8.5. `ensure_untiled()` is the other
# half of this decision and every non-tiling SDXL entry point calls it. Revisit on a fork update: if
# `host_buffer.__del__` is fixed upstream, this becomes "Make a copy" again, `ensure_untiled`
# becomes a no-op, and both halves can go.
TILING_COPY_MODE = "Modify in place"

# Titles of the two padding nodes, so the binding and the reset name them once.
TILE_TITLE, TILE_VAE_TITLE = "BOB_TILE", "BOB_TILE_VAE"


def tiling_values(enable=True):
    """The `BOB_TILE` / `BOB_TILE_VAE` binding: circular padding on or off, applied in place."""
    mode = "enable" if enable else "disable"
    return {TILE_TITLE: {"tiling": mode, "copy_model": TILING_COPY_MODE},
            TILE_VAE_TITLE: {"tiling": mode, "copy_vae": TILING_COPY_MODE}}


# Whether a graph in THIS process has left the server's shared SDXL model circularly padded.
# Per-URL, because one Bob can drive more than one server, and pessimistic on the first call: a
# fresh process does not know what a previous one left behind, so the first non-tiling graph resets
# regardless.
_TILING_DIRTY = {}


def mark_tiling_applied(url=None, dirty=True):
    _TILING_DIRTY[base_url(url)] = bool(dirty)


def reset_tiling(url=None, timeout=120):
    """Put the server's shared model and VAE back to ordinary padding. Returns the seconds it took.

    Reuses `tex_tileable` itself at 64 px and one step rather than shipping a reset graph, so there
    is no second copy of the tiling wiring to drift out of sync with the real one. The sample is
    throwaway; what matters is that both padding nodes execute, which they only do if an output
    depends on them.
    """
    graph, prov = load_workflow("tex_tileable")
    values = {"BOB_PROMPT": {"text": "tiling reset"},
              "BOB_SEED": {"seed": 0, "steps": 1},
              "BOB_SIZE": {"width": 64, "height": 64}}
    values.update(tiling_values(enable=False))
    ckpt = prov.get("default_checkpoint")
    if ckpt:
        values["BOB_CKPT"] = {"ckpt_name": ckpt}
    t0 = time.time()
    # No floor: this is 64 px and one step on a model the server has already loaded, and it is the
    # thing that has to run BEFORE the real job. Holding a reset to the floor of the route it
    # unblocks would refuse the fix on exactly the card that needs it.
    generate_image((graph, prov), values, route=None, url=url, timeout=timeout,
                   required_titles=("BOB_PROMPT", "BOB_SEED", "BOB_OUT"))
    mark_tiling_applied(url, False)
    return time.time() - t0


def ensure_untiled(url=None, on_progress=None):
    """Reset the shared model if a tiling graph has run, before a graph that must NOT tile.

    Called by every non-tiling SDXL entry point: `mesh_subject`'s subject image, the stylise and
    paint routes, and `heightmap_macro`'s OPEN route, which drops the padding nodes and would
    otherwise inherit whatever the last texture set left on the model -- and a tiling macro mask is
    measurably the wrong thing (the macro-mask gate: seam ratio 0.80 tiled against 86.18 open, i.e.
    the tiled one really does repeat the landform).

    Lazy on purpose: ten texture sets in a row pay nothing, and the cost lands once in front of the
    next subject image, which is itself followed by a 90 s geometry job. Never raises -- a failed
    reset must not stop the generation the caller actually asked for; it is logged as progress and
    the dirty flag stays set so the next call tries again.
    """
    if not _TILING_DIRTY.get(base_url(url), True):
        return 0.0
    if on_progress:
        on_progress("resetting tiling")
    try:
        return reset_tiling(url=url)
    except ComfyError:
        return 0.0


# File extensions the mesh transport recognises, which is the set Load3D accepts plus the formats
# Trellis2ExportTrimesh can write.
MESH_EXTS = (".glb", ".gltf", ".obj", ".ply", ".stl", ".fbx", ".off", ".3mf", ".dae")

# Statuses the jobs API reports. Anything not terminal means keep polling.
_DONE = ("completed",)
_FAILED = ("failed", "cancelled")

# Widget fields whose value names a file on disk. A value missing from one of these enums is the
# normal failure ("you never downloaded that checkpoint"), so it gets the "missing model" wording;
# anything else missing from an enum is a graph bug and gets "invalid option".
_MODEL_FIELDS = ("ckpt_name", "vae_name", "lora_name", "model_name", "unet_name", "clip_name",
                 "control_net_name", "style_model_name", "clip_vision_name", "ipadapter_file",
                 "gligen_name", "upscale_model", "config_name")


class ComfyError(RuntimeError):
    """A ComfyUI call failed in a way worth showing an artist verbatim."""


def base_url(url=None):
    return (url or _PREF_URL or os.environ.get("BOB_COMFY_URL") or DEFAULT_URL).rstrip("/")


# -- HTTP ------------------------------------------------------------------------------------
def _request(url, path, *, data=None, timeout=30, raw=False):
    full = base_url(url) + path
    body = json.dumps(data).encode() if data is not None else None
    req = urllib.request.Request(full, data=body, method="POST" if body else "GET",
                                headers={"Content-Type": "application/json"} if body else {})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            payload = resp.read()
    except urllib.error.HTTPError as exc:
        detail = (exc.read() or b"").decode("utf-8", "replace")[:400]
        raise ComfyError(f"ComfyUI {exc.code} on {path}: {detail or exc.reason}") from exc
    except (urllib.error.URLError, OSError) as exc:
        raise ComfyError(f"ComfyUI not reachable at {base_url(url)} ({exc})") from exc
    if raw:
        return payload
    if not payload.strip():
        # An empty 200 is a legitimate answer, not a broken one: `POST /free` returns exactly that
        # (measured: status 200, zero bytes, no content type). Treating it as non-JSON is what made
        # the Advanced panel's Free VRAM button report an error on every successful press.
        return {}
    try:
        return json.loads(payload)
    except ValueError as exc:
        raise ComfyError(f"ComfyUI returned non-JSON on {path}") from exc


def reachable(url=None, timeout=3):
    """(True, "<device>") when the server answers, (False, reason) when it does not. The check a
    UI row makes before offering Generate; never raises."""
    try:
        stats = _request(url, "/system_stats", timeout=timeout)
    except ComfyError as exc:
        return False, str(exc)
    devices = stats.get("devices") or [{}]
    name = devices[0].get("name") or "unknown device"
    free = devices[0].get("vram_free")
    return True, f"{name}" + (f", {free // (1 << 20)} MiB free" if free else "")


def service_status(url=None, timeout=3):
    """Everything the Advanced panel's ComfyUI block shows, in one call: reachability, device,
    free VRAM in MiB, and the queue depth. Never raises; a dead server is `ok: False` plus the
    reason. Called from a button or the job ticker, NEVER from `draw()` -- a socket call in a draw
    handler freezes the UI for the timeout in exactly the case the row exists to report."""
    out = {"ok": False, "url": base_url(url), "device": "", "vram_free_mib": None,
           "running": None, "pending": None, "detail": ""}
    try:
        stats = _request(url, "/system_stats", timeout=timeout)
    except ComfyError as exc:
        out["detail"] = str(exc)
        return out
    device = (stats.get("devices") or [{}])[0]
    free = device.get("vram_free")
    out.update(ok=True, device=device.get("name") or "unknown device",
               vram_free_mib=(free // (1 << 20)) if free else None)
    try:
        out["running"], out["pending"] = queue_depth(url)
    except ComfyError:
        pass  # reachable but the queue endpoint failed: still report the device
    bits = [out["device"]]
    if out["vram_free_mib"] is not None:
        bits.append(f"{out['vram_free_mib']} MiB free")
    if out["running"] is not None:
        bits.append(f"queue {out['running']}+{out['pending']}")
    out["detail"] = ", ".join(bits)
    return out


def features(url=None):
    """The `/features` dict. `has_jobs_api()` is what actually decides the polling route, because
    this fork does not advertise the jobs API here."""
    try:
        return _request(url, "/features", timeout=5)
    except ComfyError:
        return {}


def has_jobs_api(url=None):
    """True when `GET /api/jobs` exists, so per-job status and cancel are available (the cancellation
    rule). Probed once rather than read from /features, which does not list it on this fork."""
    try:
        _request(url, "/api/jobs?limit=1", timeout=5)
        return True
    except ComfyError:
        return False


def _entry_options(entry):
    """The option list of one `/object_info` input entry, or None when it is not a COMBO.

    Two shapes are live on this fork and BOTH have to be handled. The old one puts the options first
    (`[[...], {opts}]`, e.g. `LoadImage.image`); the newer one declares the type as the literal
    string `"COMBO"` and hides the options in the options dict (`["COMBO", {"options": [...]}]`,
    e.g. `UpscaleModelLoader.model_name`). The first spike read only the old shape, so a missing
    upscale model would have sailed through the check that exists to catch it.
    """
    if not isinstance(entry, (list, tuple)) or not entry:
        return None
    typ = entry[0]
    if isinstance(typ, list):
        return list(typ)
    opts = entry[1] if len(entry) > 1 else None
    if typ == "COMBO" and isinstance(opts, dict) and isinstance(opts.get("options"), list):
        return list(opts["options"])
    return None


def _field_entry(schema, field):
    """One class's input spec for `field`, from either section, or None."""
    spec = (schema or {}).get("input", {})
    for section in ("required", "optional"):
        entry = (spec.get(section) or {}).get(field)
        if entry is not None:
            return entry
    return None


def combo_options(class_type, field, url=None, info=None):
    """The option list of one node's COMBO widget, e.g. the installed checkpoints. What the
    model-enum resolution (the portability rule) is built on, so a graph fails with "missing
    model: X" instead of an HTTP 400. `info` reuses a cached `/object_info` instead of fetching
    one class."""
    if info is None:
        info = _request(url, "/object_info/" + urllib.parse.quote(class_type), timeout=30)
    return _entry_options(_field_entry(info.get(class_type), field)) or []


# `/object_info` is ~1778 classes and several MB on a loaded install, and preflight reads it once
# per graph, so it is cached per base URL. Refreshed by hand (the Test Connection button) rather
# than on a clock: node classes do not appear without a server restart.
_OBJECT_INFO = {}


def object_info(url=None, refresh=False):
    """The full `/object_info` dict, cached per server URL."""
    key = base_url(url)
    if refresh or key not in _OBJECT_INFO:
        _OBJECT_INFO[key] = _request(url, "/object_info", timeout=120)
    return _OBJECT_INFO[key]


def forget_object_info(url=None):
    """Drop the cache, so the next preflight sees a newly installed pack or model."""
    _OBJECT_INFO.pop(base_url(url), None) if url is not None else _OBJECT_INFO.clear()


def queue_depth(url=None):
    """(running, pending) from `/queue`. The Advanced panel's queue-depth row, and the thing that
    explains a job sitting at "pending" for a minute because something else owns the card."""
    data = _request(url, "/queue", timeout=10)
    return len(data.get("queue_running") or []), len(data.get("queue_pending") or [])


def free(url=None, unload_models=True, free_memory=True):
    """Ask ComfyUI to unload its models and release its allocator (the VRAM-floor rule, layer two of
    three).

    Not a Stop Server: the process and its CUDA context stay up. That is the honest limit of what
    the HTTP API can do, and it is why the panel offers both.
    """
    _request(url, "/free", data={"unload_models": bool(unload_models),
                                 "free_memory": bool(free_memory)}, timeout=60)
    return True


# -- VRAM floors and recovery (the VRAM-handback rule)
# ----------------------------------------------------------- Free VRAM (MiB) a route needs before
# it is worth queueing. Measured on a 15.5 GB card: TRELLIS2 runs in a SEPARATE pixi
# worker process, so it cannot reuse ComfyUI main's torch cache and OOMs inside
# `_sample_shape_slat_cascade` (and then inside BiRefNet matting) while main still holds 7.3 GB. The
# point of a floor is that the failure becomes a sentence about VRAM before 90 seconds are spent,
# instead of a CUDA traceback from inside somebody else's worker.
#
# The numbers are the worker's resident weights (3.2 GB measured) plus its cascade working set, and
# the hero tier's 1536_cascade needs materially more than the default 1024.
#
# Every job in this module is held to one of these, because `generate_image` and `generate_mesh`
# check before they queue and take `route` with no default. A graph that loads no model says
# `route=None` at the call site with its reason beside it -- that is the whole list of exceptions,
# and it is visible rather than implied.
VRAM_FLOOR_MIB = {
    "mesh": 5000,        # any image-to-3D route at the default tier
    "mesh_hero": 7000,   # 1536_cascade
    "texture": 3000,     # SDXL at 1024 with circular padding
    "paint": 4000,
    "heightmap": 3000,
    "stylize": 3500,
}


def vram_free_mib(url=None, timeout=3):
    """Free VRAM on the server's first device in MiB, or None when it cannot be read."""
    return service_status(url, timeout=timeout).get("vram_free_mib")


def recover_vram(url=None, target_mib=None, timeout=3):
    """Escalate through what the HTTP API can actually do, and report what it recovered.

    Layer one is `POST /free {"unload_models": true, "free_memory": true}`, which is all this can
    reach: the pages stay in the main process's torch caching allocator, so on the measured case it
    returns success and about 100 MiB. Saying that out loud is the point -- the panel's Free VRAM
    button looked like it worked, and the next generate still OOMed.

    Returns {before, after, recovered, enough, advice}. `advice` names the one thing that DOES
    recover the card when the free was not enough, which is a restart of a server Bob did not start
    (measured: 0.5 GB free to 12.3 GB), plus the launch flag that stops the fragmentation building
    up in the first place.
    """
    before = vram_free_mib(url, timeout=timeout)
    try:
        free(url)
    except ComfyError as exc:
        return {"before": before, "after": before, "recovered": 0, "enough": False,
                "advice": f"could not reach the free endpoint: {exc}"}
    after = vram_free_mib(url, timeout=timeout)
    recovered = (after - before) if (after is not None and before is not None) else None
    enough = target_mib is None or (after is not None and after >= target_mib)
    advice = ""
    if not enough:
        advice = (
            "`POST /free` only drops what the main process will give back; the generation workers "
            "run in separate processes and cannot reuse that cache, so the card stays full. "
            "Restart ComfyUI to recover it (measured: 0.5 GB free to 12.3 GB), and launch it with "
            "PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True so the fragmentation does not build "
            "up again. Bob will not restart a server it did not start.")
    return {"before": before, "after": after, "recovered": recovered, "enough": enough,
            "advice": advice}


def preflight_vram(route="mesh", url=None, free_first=True):
    """Raise ComfyError with a VRAM sentence when the card cannot hold this route's working set.

    Called from `generate_image` and `generate_mesh`, which every job in this module goes through,
    so the floor belongs to the CAPABILITY rather than to one caller: a panel operator, a gate
    script and an MCP tool all inherit it, and a route added later cannot forget it because `route`
    has no default. `mcp_agent.server._generation` also calls it up front, which is not a duplicate
    of this so much as an early-out: it refuses a multi-stage chain before the first stage is paid
    for rather than after. `free_first` tries the recovery once,
    because the common case is a card that a previous job left full and that one `POST /free` fixes;
    only when that is not enough does this refuse. A server that cannot report its VRAM at all is
    allowed through -- an unknown is not a reason to block work.
    """
    need = VRAM_FLOOR_MIB.get(route)
    if need is None:
        return None
    have = vram_free_mib(url)
    if have is None or have >= need:
        return have
    result = recover_vram(url, target_mib=need) if free_first else {
        "after": have, "enough": False, "advice": ""}
    if result["enough"]:
        return result["after"]
    raise ComfyError(
        f"not enough free VRAM for the {route} route: {result['after']} MiB free, {need} MiB "
        f"needed. This is the VRAM-handback rule (docs/GENERATION.md): generation and rendering in one session deadlock "
        f"on a card neither gives back. {result['advice']}".strip())


def upload_image(path, url=None, subfolder="", overwrite=True, timeout=120):
    """Upload a file to the server's input folder via `POST /upload/image`, returning the name
    `LoadImage` will accept (`<subfolder>/<name>` when a subfolder is used).

    Multipart by hand, because the client is stdlib only. The endpoint writes raw bytes with a
    `commonpath` traversal guard and no image-specific handling, which is what makes it the mesh
    transport in the asset gate as well as the reference-photo transport for `tex_tileable_ref`.
    """
    name = os.path.basename(path)
    with open(path, "rb") as fh:
        payload = fh.read()
    boundary = "----bob" + _uuid.uuid4().hex
    ctype = mimetypes.guess_type(name)[0] or "application/octet-stream"
    parts = []
    for field, value in (("subfolder", subfolder), ("overwrite", "true" if overwrite else "false"),
                         ("type", "input")):
        parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{field}"\r\n\r\n'
                     f"{value}\r\n".encode())
    parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="image"; '
                 f'filename="{name}"\r\nContent-Type: {ctype}\r\n\r\n'.encode())
    body = b"".join(parts) + payload + f"\r\n--{boundary}--\r\n".encode()
    req = urllib.request.Request(base_url(url) + "/upload/image", data=body, method="POST",
                                 headers={"Content-Type":
                                          f"multipart/form-data; boundary={boundary}"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            out = json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        detail = (exc.read() or b"").decode("utf-8", "replace")[:300]
        raise ComfyError(f"ComfyUI rejected the upload of {name}: {detail or exc.reason}") from exc
    except (urllib.error.URLError, OSError, ValueError) as exc:
        raise ComfyError(f"ComfyUI upload of {name} failed ({exc})") from exc
    got_sub = out.get("subfolder") or ""
    got_name = out.get("name") or name
    return f"{got_sub}/{got_name}" if got_sub else got_name


def queue(prompt, url=None, client_id=None):
    """POST a prompt graph, returning its prompt_id.

    `client_id` decides which websocket the server publishes this job's progress to, so it defaults
    to the same `CLIENT_ID` `wait()` connects with. Pass one only to take that routing over.
    """
    out = _request(url, "/prompt", data={"prompt": prompt, "client_id": client_id or CLIENT_ID},
                   timeout=60)
    pid = out.get("prompt_id")
    if not pid:
        raise ComfyError(f"ComfyUI accepted no prompt id: {str(out)[:200]}")
    return pid


def job(prompt_id, url=None, jobs_api=True):
    """One job's status dict, normalised to {"status", "outputs"}. Uses the jobs API when the
    server has it and `/history/{id}` otherwise."""
    if jobs_api:
        try:
            data = _request(url, f"/api/jobs/{urllib.parse.quote(prompt_id)}", timeout=15)
            return {"status": data.get("status") or "pending",
                    "outputs": data.get("outputs") or {},
                    "error": data.get("execution_error")}
        except ComfyError as exc:
            if "404" not in str(exc):
                raise
            return {"status": "pending", "outputs": {}, "error": None}
    hist = _request(url, f"/history/{urllib.parse.quote(prompt_id)}", timeout=15)
    entry = hist.get(prompt_id)
    if not entry:
        return {"status": "pending", "outputs": {}, "error": None}
    status_str = (entry.get("status") or {}).get("status_str")
    return {"status": "completed" if status_str == "success" else "failed",
            "outputs": entry.get("outputs") or {}, "error": None}


def _mesh_transport_hint(detail):
    """The sentence to append when a job died because the mesh never arrived, else "".

    "Mesh file not found: input/3d/x.glb" is what every mesh-uploading graph (`mesh_texture`,
    `mesh_simplify_uv`, `mesh_geom_ctrl`, `mesh_process`) says when `$BOB_COMFY_DIR` is unset, and
    on its own it names neither the variable nor the reason. A generated-foliage run hit it on the
    block-out route and read it as a bad control mesh. Naming it here rather than setting the
    variable in the repo's own `.mcp.json`, because a packaged install has no `.mcp.json` of ours and would inherit
    the silence.
    """
    if "mesh file not found" not in str(detail).lower() or comfy_dir() is not None:
        return ""
    return (". The mesh was uploaded over HTTP because no local ComfyUI folder is configured, and "
            "on this fork the loader runs in a worker whose working directory is not the server "
            "root, so a relative path never resolves. Set $BOB_COMFY_DIR (or the addon's ComfyUI "
            "Folder preference) to the checkout and Bob copies the mesh into <comfy>/input/3d "
            "instead. `control_bbox` uploads nothing and needs none of this.")


def cancel(prompt_id, url=None):
    """Cancel one job by id. Idempotent server-side, so a finished id is a no-op, not an error."""
    return bool(_request(url, f"/api/jobs/{urllib.parse.quote(prompt_id)}/cancel",
                         data={}, timeout=15).get("cancelled"))


def wait(prompt_id, url=None, timeout=600, poll=0.5, on_progress=None, progress_ws=True):
    """Block until a job is terminal, returning its outputs dict. Raises on failure or timeout.

    Blocking, and it stays that way: `core.comfy_jobs` runs this loop on its worker thread, so
    the client keeps working from a plain script with no scheduler in sight. `on_progress` is how
    a caller reports without owning the loop.

    Progress is per-node when `/ws` is available and the job's status string when it is not, and the
    split is deliberate: the websocket supplies the DETAIL (`step 7/20`, `node 12`) while the jobs
    API still decides the job is finished. So a socket that never connects, drops, or is stolen by
    another process using the same client id costs a progress bar and cannot cost a result. The
    socket also serves as this loop's sleep, so an event is reported when it arrives rather than at
    the next poll tick.
    """
    jobs_api = has_jobs_api(url)
    deadline = time.time() + timeout
    ws = comfy_ws.connect(base_url(url), CLIENT_ID) if (progress_ws and on_progress) else None
    last = None

    def relay(event):
        nonlocal last
        text = comfy_ws.progress_text(event, prompt_id)
        if text and text != last:
            last = text
            on_progress(text)

    try:
        while True:
            state = job(prompt_id, url=url, jobs_api=jobs_api)
            if state["status"] in _DONE:
                return state["outputs"]
            if state["status"] in _FAILED:
                err = state.get("error") or {}
                detail = (err.get("exception_message") or err.get("exception_type")
                          or state["status"])
                raise ComfyError(f"ComfyUI job {state['status']}: {detail}"
                                 + _mesh_transport_hint(detail))
            if time.time() > deadline:
                cancel(prompt_id, url=url)
                raise ComfyError(f"ComfyUI job timed out after {timeout:.0f}s (cancelled)")
            if ws is not None and not ws.closed:
                ws.pump(poll, relay)  # the pump IS the sleep
                continue
            if on_progress:
                on_progress(state["status"])
            time.sleep(poll)
    finally:
        if ws is not None:
            ws.close()


def images(outputs):
    """[{"filename", "subfolder", "type"}, ...] over every image in a job's outputs, in node
    order. The jobs API and `/history` nest these differently, so both shapes are walked."""
    found = []
    for node_out in (outputs or {}).values():
        items = node_out.get("images") if isinstance(node_out, dict) else node_out
        if isinstance(node_out, dict) and items is None:
            items = [v for v in node_out.values() if isinstance(v, list)]
            items = items[0] if items else None
        for item in items or []:
            if not isinstance(item, dict) or not item.get("filename"):
                continue
            # Not a whitelist of image extensions, an exclusion of mesh ones: the fallback branch
            # above takes any list of dicts, so in a graph that saves both a preview PNG and a GLB
            # it would otherwise hand the caller a GLB to run through the PNG decoder.
            if item["filename"].lower().endswith(MESH_EXTS):
                continue
            found.append({"filename": item["filename"],
                          "subfolder": item.get("subfolder", ""),
                          "type": item.get("type", "output")})
    return found


def view(image, url=None, timeout=120):
    """The bytes of one output artifact, via `/view`. Serves meshes as well as images: the route
    only special-cases a `preview` query, and otherwise returns the file.

    `filename` is basenamed because a mesh node reports an ABSOLUTE server-side path and `/view`
    rejects a leading slash outright (`server.py:539`).
    """
    query = urllib.parse.urlencode({"filename": os.path.basename(image["filename"]),
                                    "subfolder": image.get("subfolder", ""),
                                    "type": image.get("type", "output")})
    return _request(url, "/view?" + query, timeout=timeout, raw=True)


# -- Meshes ----------------------------------------------------------------------------------
# A mesh is not an image, and the difference is entirely in how the job REPORTS it.
#
# `Trellis2ExportTrimesh` is the only exporter in the pack that converts the pack's internal Z-up
# to glTF's Y-up and flips the UV V, so it is the one Bob's graphs end with. But it is a V3 node
# returning a plain STRING, and ComfyUI records a node's `ui` dict as its outputs, not its return
# value, so the job comes back with `outputs: {}` and `outputs_count: 0`. Measured, not assumed.
#
# `Preview3D` takes that string and emits a real `{filename, type, subfolder, mediaType}` entry.
# So every mesh graph is export-then-preview, and the preview node is load-bearing plumbing rather
# than a viewer. `SaveGLB` (the Hunyuan route) is a core output node and reports itself, which is
# why `mesh_geom` needs no Preview3D.
def meshes(outputs):
    """[{"filename", "subfolder", "type"}, ...] over every mesh file in a job's outputs.

    Walks the same two nestings `images()` does, and selects on the file EXTENSION rather than on
    a key name, because `Preview3D` files its entry under `result` and `SaveGLB` under `3d`.
    """
    found = []
    for node_out in (outputs or {}).values():
        if not isinstance(node_out, dict):
            continue
        for value in node_out.values():
            for item in (value if isinstance(value, list) else []):
                name = item.get("filename") if isinstance(item, dict) else None
                if name and name.lower().endswith(MESH_EXTS):
                    found.append({"filename": name, "subfolder": item.get("subfolder", ""),
                                  "type": item.get("type", "output")})
    return found


def input_3d_dir():
    """`<comfy>/input/3d`, created, when the ComfyUI folder preference points at a local checkout;
    None otherwise, which is the signal to upload over HTTP instead."""
    base = comfy_dir()
    if base is None:
        return None
    path = os.path.join(base, "input", "3d")
    try:
        os.makedirs(path, exist_ok=True)
    except OSError:
        return None
    return path


def upload_mesh(path, url=None, subfolder="3d"):
    """Put a mesh where the server can load it, and return the path string `Trellis2LoadMesh`
    takes. Two routes, both verified:

    1. The ComfyUI folder preference points at a local checkout: copy into `<comfy>/input/3d/` and
       return the absolute path. No HTTP, no size limit, no multipart.
    2. Otherwise `POST /upload/image`, which writes raw bytes to an arbitrary subfolder with a
       `commonpath` guard and no image-specific handling, and return `input/<sub>/<name>`.

    Route 2 is the fallback for a server that is not on this machine, and the geometry A/B measured
    that on THIS fork it does not actually work: the upload lands correctly, but `Trellis2LoadMesh`
    runs inside a comfy-env pixi worker whose working directory is not the ComfyUI root, so a
    relative path fails with "Mesh file not found: input/3d/...". Route 1 is therefore effectively
    required for every mesh-uploading graph (`mesh_texture`, `mesh_simplify_uv`, `mesh_geom_ctrl`,
    `mesh_process`), which is why `comfy_dir` takes `$BOB_COMFY_DIR` as well as the addon
    preference: the MCP server is not the addon and cannot read a preference.

    NOT `GeomPackLoadMesh`: its `file_path` is a COMBO whose options are a directory listing, and
    comfy-env caches each node's scanned schema, so a file written a second ago is absent from the
    enum even across a server restart (the pack install). `Trellis2LoadMesh` takes a free-form
    string.
    """
    import shutil

    name = os.path.basename(path)
    local = input_3d_dir()
    if local is not None:
        dest = os.path.join(local, name)
        if os.path.abspath(dest) != os.path.abspath(path):
            shutil.copyfile(path, dest)
        return dest
    return "input/" + upload_image(path, url=url, subfolder=subfolder)


def generate_mesh(workflow, values, *, route, url=None, timeout=1800, on_progress=None,
                  on_queued=None, required_titles=(), preflight_graph=True):
    """Run one graph and return (mesh bytes, info). `generate_image`'s twin, with a longer default
    timeout because a geometry job is 87 s warm and 680 s on the run that pulls 15 GB of weights.

    `route` names the VRAM floor, and it has no default for the reason `gen_receipt` gives a receipt
    key no default reader: a new graph then has to state which floor it runs under or say `None` out
    loud, and the omission that left every caller but one unguarded cannot happen silently again.
    """
    graph, prov = load_workflow(workflow) if isinstance(workflow, str) else workflow
    bound = template(graph, values)
    if preflight_graph:
        check(bound, url=url, required_titles=required_titles,
              runtime_inputs=prov.get("runtime_inputs") or ())
    t0 = time.time()
    if route:
        preflight_vram(route, url=url)
    pid = queue(bound, url=url)
    if on_queued:
        on_queued(pid)
    outputs = wait(pid, url=url, timeout=timeout, poll=1.0, on_progress=on_progress)
    found = meshes(outputs)
    if not found:
        raise ComfyError("ComfyUI job produced no mesh (is BOB_OUT followed by a Preview3D?)")
    data = view(found[-1], url=url, timeout=600)
    return data, {"prompt_id": pid, "seconds": time.time() - t0, "provenance": prov,
                  "server_file": found[-1]["filename"]}


# -- Workflows -------------------------------------------------------------------------------
def load_workflow(name):
    """A shipped graph as (prompt, provenance). The files wrap the API prompt as
    `{"_bob": {...}, "prompt": {...}}` so provenance travels with the graph without the `/prompt`
    validator seeing a key that is not a node."""
    path = name if os.path.isabs(name) else os.path.join(WORKFLOW_DIR, name)
    if not path.endswith(".json"):
        path += ".json"
    try:
        with open(path) as fh:
            data = json.load(fh)
    except (OSError, ValueError) as exc:
        raise ComfyError(f"workflow {os.path.basename(path)} unreadable: {exc}") from exc
    prompt = data.get("prompt") if isinstance(data, dict) else None
    if not isinstance(prompt, dict):
        raise ComfyError(f"workflow {os.path.basename(path)} has no 'prompt' graph")
    return prompt, (data.get("_bob") or {})


def titles(prompt):
    """{title: node_id} over a graph. Duplicate titles collide, which is why `preflight()`
    asserts BOB_* titles are unique (the title-template rule); the lookup itself takes the last
    one and says nothing."""
    return {(node.get("_meta") or {}).get("title"): nid for nid, node in prompt.items()}


def drop_node(prompt, title, passthrough):
    """A copy of `prompt` with the node titled `title` REMOVED and its consumers rewired.

    `passthrough` maps the dropped node's output index onto one of its own input keys, so a
    pass-through node can leave the graph without breaking the chain it sat in: `{0: "model", 1:
    "clip"}` for a `LoraLoader`.

    Why a graph edit rather than a zero strength: a `LoraLoader` at strength 0 still has to NAME an
    installed file, and the shipped default cannot know what is installed on this machine (the
    portability rule). A graph with no LoRA in it is the honest default, and the node comes back the
    moment a style is asked for. Returns the graph unchanged when the title is absent.
    """
    by_title = titles(prompt)
    nid = by_title.get(title)
    if nid is None:
        return prompt
    node = prompt[nid]
    sources = {}
    for out_index, field in passthrough.items():
        link = (node.get("inputs") or {}).get(field)
        if not isinstance(link, (list, tuple)):
            raise ComfyError(f"cannot drop {title}: its {field} input is a value, not a link")
        sources[int(out_index)] = list(link)
    out = {}
    for other_id, other in prompt.items():
        if other_id == nid:
            continue
        inputs = dict(other.get("inputs") or {})
        for field, value in inputs.items():
            if isinstance(value, (list, tuple)) and len(value) == 2 and value[0] == nid:
                replacement = sources.get(int(value[1]))
                if replacement is None:
                    raise ComfyError(f"cannot drop {title}: output {value[1]} has no passthrough")
                inputs[field] = replacement
        out[other_id] = {**other, "inputs": inputs}
    return out


def template(prompt, values):
    """A copy of `prompt` with inputs overridden by node TITLE, not node id: `_meta.title`
    survives a GUI re-export and node ids do not: the portability and title-template rules together.

    values: {"BOB_SEED": {"seed": 12}, ...}. A title absent from the graph raises, because a
    silently-unapplied prompt or seed is the failure mode that wastes a generation.
    """
    by_title = titles(prompt)
    out = {nid: {**node, "inputs": dict(node.get("inputs") or {})}
           for nid, node in prompt.items()}
    for title, fields in values.items():
        nid = by_title.get(title)
        if nid is None:
            raise ComfyError(f"workflow has no node titled {title} "
                             f"(has: {sorted(t for t in by_title if t)})")
        out[nid]["inputs"].update(fields)
    return out


# -- Preflight -------------------------------------------------------------------------------
# A ComfyUI subgraph node's `type` is the subgraph's UUID and its real nodes live under
# `definitions.subgraphs`, which title-based templating cannot see into. The shipped default
# text-to-image template is subgraphed, so this is the common case, not a corner one.
_UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I)


def _node_label(nid, node):
    title = (node.get("_meta") or {}).get("title")
    return f"node {nid}" + (f" ({title})" if title and title != node.get("class_type") else "")


def preflight(prompt, url=None, info=None, required_titles=(), runtime_inputs=()):
    """Every reason this graph would fail, as a list of sentences. Empty means queue it.

    Five classes of failure, which is every one seen so far and the whole reason the function exists
    -- the portability, title-template and local-only rules between them:

    1. a `class_type` the server does not have, i.e. a pack that is not installed;
    2. a node carrying `api_node: true`, i.e. a cloud node -- the check that keeps local-only
       true over time rather than by intention;
    3. a COMBO value the server does not offer, i.e. a model that was never downloaded;
    4. a `BOB_*` title that is missing or duplicated, so templating cannot bind (the title-template rule);
    5. a UUID-typed subgraph node.

    `runtime_inputs` names `TITLE.field` or `ClassType.field` pairs that Bob binds just before
    queueing (an uploaded reference image, say), so their placeholder value is not checked against
    an enum it is legitimately absent from. Shipped graphs declare theirs in `_bob`.
    """
    if info is None:
        info = object_info(url)
    skip = {str(s) for s in runtime_inputs}
    problems = []

    by_title = {}
    for nid, node in sorted(prompt.items()):
        title = (node.get("_meta") or {}).get("title")
        if title:
            by_title.setdefault(title, []).append(nid)
        cls = node.get("class_type") or ""
        if _UUID_RE.match(cls):
            problems.append(f"subgraph node rejected: {_node_label(nid, node)} has a UUID type "
                            f"({cls}); flatten it, title templating cannot see inside a subgraph")
            continue
        schema = info.get(cls)
        if schema is None:
            problems.append(f"unknown node: {cls} at {_node_label(nid, node)} "
                            f"(the pack that provides it is not installed)")
            continue
        if schema.get("api_node"):
            problems.append(f"cloud node rejected: {cls} at {_node_label(nid, node)} "
                            f"(this integration is local only)")
            continue
        for field, value in (node.get("inputs") or {}).items():
            if isinstance(value, (list, tuple)) or not isinstance(value, str):
                continue  # a link, or a number/bool: nothing to resolve
            if f"{title}.{field}" in skip or f"{cls}.{field}" in skip:
                continue
            options = _entry_options(_field_entry(schema, field))
            if options is None or value in options:
                continue
            shown = ", ".join(options[:6]) + (", ..." if len(options) > 6 else "")
            if field in _MODEL_FIELDS:
                problems.append(f"missing model: {value} ({cls}.{field}); "
                                f"installed: {shown or 'nothing'}")
            else:
                problems.append(f"invalid option: {value} for {cls}.{field}; "
                                f"allowed: {shown or 'nothing'}")

    for title, ids in sorted(by_title.items()):
        if title.startswith("BOB_") and len(ids) > 1:
            problems.append(f"duplicate title: {title} on nodes {', '.join(ids)} "
                            f"(templating binds by title, so one of them would be unreachable)")
    for title in required_titles:
        if title not in by_title:
            problems.append(f"missing title: {title} (the graph has no node Bob can bind it to)")
    return problems


def check(prompt, url=None, **kwargs):
    """Preflight or raise. One `ComfyError` listing every problem, not just the first: a machine
    missing two models should say so once."""
    problems = preflight(prompt, url=url, **kwargs)
    if problems:
        raise ComfyError("; ".join(problems))
    return True


# -- Texture sets ----------------------------------------------------------------------------
def slugify(text, limit=40):
    """A prompt as a filesystem-safe set name stem."""
    slug = re.sub(r"[^a-z0-9]+", "_", (text or "").lower()).strip("_")
    return (slug[:limit].rstrip("_") or "texture")


def unique_set_name(textures_dir, stem):
    """`stem`, or `stem_02`, `stem_03`, ... -- the first name no set already occupies. Never an
    implicit overwrite (the naming rule): a second "mossy rock" is a new set, not a replaced one."""
    if not os.path.isdir(os.path.join(textures_dir, stem)):
        return stem
    for n in range(2, 1000):
        cand = f"{stem}_{n:02d}"
        if not os.path.isdir(os.path.join(textures_dir, cand)):
            return cand
    raise ComfyError(f"too many sets named {stem}")


def unique_file_name(directory, stem, ext):
    """`unique_set_name`'s twin for a single FILE, and for the same reason (the naming rule): a second
    block-out control export is a new file, not a replaced one."""
    for n in range(1, 1000):
        cand = os.path.join(directory, stem + ("" if n == 1 else f"_{n:02d}") + ext)
        if not os.path.exists(cand):
            return cand
    raise ComfyError(f"too many files named {stem}{ext}")


def generate_image(workflow, values, *, route, url=None, timeout=600, on_progress=None,
                   on_queued=None, required_titles=(), preflight_graph=True):
    """Run one graph and return (png bytes, info). The single path every raster job takes.

    Preflight first, so a missing model or an uninstalled pack is a sentence rather than an HTTP
    400 from the validator. `preflight_graph=False` skips the `/object_info` fetch for a caller
    that has already checked the same graph this session.

    `route` names the VRAM floor and is required; see `generate_mesh` for why it has no default.
    The graph check comes first because a graph that cannot run is a defect and a full card is a
    condition, and reporting the condition would hide the defect.

    `on_queued(prompt_id)` fires the moment the server accepts the graph. That is what makes a
    cancel reach the server rather than only the local registry, so a cancelled job stops costing
    VRAM instead of running to completion unwatched.
    """
    graph, prov = load_workflow(workflow) if isinstance(workflow, str) else workflow
    bound = template(graph, values)
    if preflight_graph:
        check(bound, url=url, required_titles=required_titles,
              runtime_inputs=prov.get("runtime_inputs") or ())
    t0 = time.time()
    if route:
        preflight_vram(route, url=url)
    pid = queue(bound, url=url)
    if on_queued:
        on_queued(pid)
    outputs = wait(pid, url=url, timeout=timeout, on_progress=on_progress)
    found = images(outputs)
    if not found:
        raise ComfyError("ComfyUI job produced no image (is BOB_OUT a SaveImage node?)")
    png = view(found[0], url=url)
    return png, {"prompt_id": pid, "seconds": time.time() - t0, "provenance": prov}


def _texture_values(prompt_text, *, seed, size, negative, checkpoint, prov):
    """The BOB_* bindings `tex_tileable`, `tex_tileable_ref` and `tex_upres` all share."""
    full = ", ".join(p for p in ((prompt_text or "").strip(), PROMPT_SUFFIX) if p)
    values = {"BOB_PROMPT": {"text": full},
              "BOB_SEED": {"seed": int(seed)},
              "BOB_SIZE": {"width": int(size), "height": int(size)}}
    values.update(tiling_values(enable=True))  # circular padding, in place; see TILING_COPY_MODE
    if negative:
        values["BOB_NEG"] = {"text": negative}
    ckpt = checkpoint or prov.get("default_checkpoint")
    if ckpt:
        values["BOB_CKPT"] = {"ckpt_name": ckpt}
    return values, full, ckpt


def write_texture_set(out_dir, name, maps, source_text, meta=None):
    """Write `<out_dir>/<name>_<role>.png` for each map, plus `SOURCE.txt` and, when given, a
    `meta.json` (the provenance rule: provenance travels with the artifact). Returns {role:
    path}."""
    os.makedirs(out_dir, exist_ok=True)
    written = {}
    for role, array in maps.items():
        path = os.path.join(out_dir, f"{name}_{role}.png")
        comfy_maps.write_png(path, array)
        written[role] = path
    with open(os.path.join(out_dir, "SOURCE.txt"), "w") as fh:
        fh.write(source_text)
    if meta is not None:
        with open(os.path.join(out_dir, "meta.json"), "w") as fh:
            json.dump(meta, fh, indent=2, sort_keys=True)
    return written


def _source_text(workflow, prov, ckpt, seed, size, full_prompt, reference=None):
    return (f"basecolor: generated by ComfyUI, workflow {workflow} "
            f"({prov.get('derived_from', 'derived')}), checkpoint {ckpt or 'graph default'}, "
            f"seed {int(seed)}, {size}x{size}.\n"
            f"prompt: {full_prompt}\n"
            + (f"reference image: {reference}\n" if reference else "")
            + "roughness, height, normal, ao: derived from the albedo by BobBlenderTools "
              "(core/comfy_maps.py).\n"
              "Output licensing follows the model that produced it.\n")


def texture_variant(prompt_text, out_dir, *, seed=0, size=1024, negative=None, checkpoint=None,
                    url=None, workflow="tex_tileable", reference=None, denoise=0.65,
                    timeout=600, on_progress=None, on_queued=None, preflight_graph=True,
                    delight=False):
    """Generate ONE seamless texture set into `out_dir` (which is created), returning info.

    `reference` is a local image path: passing one switches the default workflow to
    `tex_tileable_ref`, uploads the file, and runs img2img at `denoise` with the reference's palette
    locked by IPAdapter.

    `delight` divides the low-frequency lighting out of the albedo before any map is derived
    (`comfy_maps.delight`), because a basecolor is meant to be reflectance and a diffusion model
    returns a photograph. Off by default: it is a real change to what lands on disk, and
    `flatness` in the returned info -- reported either way -- is what says whether a given set
    wants it. Measured over ten generated texture sets, `low_freq_variation` runs 0.025 on a flat
    one to 0.099 on a lit one, and delighting takes the lit ones to 0.036 to 0.066 with the mean
    luminance and the total variation intact.
    """
    if reference and workflow == "tex_tileable":
        workflow = "tex_tileable_ref"
    graph, prov = load_workflow(workflow)
    values, full_prompt, ckpt = _texture_values(prompt_text, seed=seed, size=size,
                                                negative=negative, checkpoint=checkpoint,
                                                prov=prov)
    if reference:
        values["BOB_IMAGE"] = {"image": upload_image(reference, url=url, subfolder="bob")}
        values["BOB_SEED"]["denoise"] = float(denoise)

    mark_tiling_applied(url)  # before the queue: a crash mid-job still leaves the model padded
    png, gen = generate_image((graph, prov), values, route="texture", url=url, timeout=timeout,
                              on_progress=on_progress, on_queued=on_queued,
                              preflight_graph=preflight_graph,
                              required_titles=("BOB_PROMPT", "BOB_SEED", "BOB_OUT"))

    t1 = time.time()
    # Decoded once here rather than inside `derive`, so the AS-GENERATED flatness can be measured
    # before any correction touches it without paying for a second Paeth decode (0.64 s at 1024).
    raw = comfy_maps.read_png(png)
    generated_flatness = comfy_maps.flatness_report(raw)
    strength = comfy_maps.DELIGHT_STRENGTH if delight else 0.0
    maps = comfy_maps.derive(raw, delight_strength=strength)
    seam = comfy_maps.seam_report(maps["basecolor"])
    # Measured on what SHIPS, and reported whether or not delighting ran. Both readings are useful:
    # with it off this is the verdict on the generation, and with it on it is the verdict on the
    # correction. The lit-albedo case is silent otherwise -- the prompts already ask for flat light
    # and a gabled timber structure's reference came back an overcast outdoor photograph anyway.
    flatness = comfy_maps.flatness_report(maps["basecolor"])
    flatness["delighted"] = bool(delight)
    flatness["as_generated"] = generated_flatness["low_freq_variation"]
    t_derive = time.time() - t1

    t2 = time.time()
    name = os.path.basename(out_dir.rstrip("/\\"))
    meta = {"prompt": full_prompt, "artist_prompt": (prompt_text or "").strip(),
            "workflow": workflow, "derived_from": prov.get("derived_from"),
            "checkpoint": ckpt, "seed": int(seed), "size": int(size),
            "reference": reference, "seam": seam, "flatness": flatness,
            "prompt_id": gen["prompt_id"]}
    written = write_texture_set(out_dir, name, maps,
                                _source_text(workflow, prov, ckpt, seed, size, full_prompt,
                                             reference),
                                meta=meta)
    t_write = time.time() - t2

    info = dict(meta)
    info.update(dir=out_dir, name=name, maps=written,
                seconds={"generate": gen["seconds"], "derive": t_derive, "write": t_write,
                         "total": gen["seconds"] + t_derive + t_write})
    return info


def staging_dir(pack_dir):
    """`<pack>/_staging`, where unaccepted variants live (the iteration rule).

    Deliberately a SIBLING of `textures/`, not a set inside it: `assets.list_texture_sets()`
    unions every `textures/` directory it finds under a pack root, so a variant staged in there
    would appear in the picker before anyone accepted it.
    """
    return os.path.join(pack_dir, "_staging")


def list_variants(pack_dir):
    """Staged variant directories, oldest first, as absolute paths."""
    base = staging_dir(pack_dir)
    if not os.path.isdir(base):
        return []
    dirs = [os.path.join(base, n) for n in sorted(os.listdir(base))]
    return [d for d in dirs if os.path.isdir(d)]


def texture_variants(prompt_text, pack_dir, *, count=1, seed=0, on_variant=None, **kwargs):
    """Generate `count` variants into `<pack>/_staging/`, one seed apart, and return their infos.

    Nothing is written into the pack proper: the artist picks one with `accept_variant()` and the
    rest are deleted (the iteration rule). `on_variant(index, count, info_or_None)` is called after
    each, so a caller can report progress without waiting for the whole batch. It runs on whatever
    thread this does, so it must not touch `bpy`.
    """
    base = staging_dir(pack_dir)
    os.makedirs(base, exist_ok=True)
    stem = slugify(prompt_text)
    out = []
    for i in range(max(1, int(count))):
        this_seed = int(seed) + i
        name = unique_set_name(base, f"{stem}_s{this_seed}")
        info = texture_variant(prompt_text, os.path.join(base, name), seed=this_seed,
                               # /object_info is fetched once for the batch, not once per variant
                               preflight_graph=(i == 0), **kwargs)
        out.append(info)
        if on_variant:
            on_variant(i + 1, count, info)
    return out


def accept_variant(variant_dir, pack_dir, name=None):
    """Move one staged variant into `<pack>/textures/` under a unique name, and return that name.

    A MOVE, not a copy: an accepted variant leaves staging, so the staging list is exactly the set
    of undecided results and nothing needs a retention sweep. The per-file stems are renamed with
    the folder because `assets.texture_set_maps()` resolves `<set>/<set>_<role>.<ext>`, so a
    folder whose files still carry the staging stem would resolve to no maps at all.
    """
    variant_dir = os.path.abspath(variant_dir)
    if not os.path.isdir(variant_dir):
        raise ComfyError(f"no staged variant at {variant_dir}")
    old = os.path.basename(variant_dir)
    textures = os.path.join(pack_dir, "textures")
    os.makedirs(textures, exist_ok=True)
    # Default to the prompt slug rather than the staging name, which carries a seed suffix nobody
    # wants to read in a picker.
    stem = name or _meta_stem(variant_dir) or re.sub(r"_s\d+(_\d+)?$", "", old) or old
    final = unique_set_name(textures, slugify(stem))
    dest = os.path.join(textures, final)
    os.rename(variant_dir, dest)
    for entry in sorted(os.listdir(dest)):
        if entry.startswith(old + "_"):
            os.rename(os.path.join(dest, entry),
                      os.path.join(dest, final + entry[len(old):]))
    return final


def _meta_stem(variant_dir):
    try:
        with open(os.path.join(variant_dir, "meta.json")) as fh:
            return (json.load(fh) or {}).get("artist_prompt") or ""
    except (OSError, ValueError):
        return ""


def reject_variant(variant_dir):
    """Delete one staged variant. Reject is a delete (the iteration rule), so nothing accumulates
    unasked."""
    import shutil

    variant_dir = os.path.abspath(variant_dir)
    if os.path.isdir(variant_dir):
        shutil.rmtree(variant_dir)
        return True
    return False


# How much of the opposite edge is wrapped around a tile before it is sent to `tex_upres`, in pixels
# at input resolution. Measured, not guessed: without it the upscale comes back at seam ratio 3.43
# from an input measuring 0.94, because UltimateSDUpscale's ESRGAN pass and its per-tile crops both
# pad at the image border and a circular-padded UNet never sees the wrap. 128 is one
# UltimateSDUpscale tile_padding plus a mask_blur band, with room to spare.
UPRES_WRAP_PAD = 128


def upres_variant(variant_dir, *, scale=2.0, url=None, workflow="tex_upres", denoise=0.2,
                  checkpoint=None, timeout=1200, on_progress=None, on_queued=None,
                  wrap_pad=UPRES_WRAP_PAD):
    """Upscale a staged variant's basecolor through `tex_upres` and re-derive its maps in place.

    Runs on the STAGED copy on purpose: an upres is another iteration, so it belongs in front of
    Accept rather than behind it.

    The tile is wrap-padded on the way out and cropped on the way back, because the upscaler
    cannot be told the image is a torus; see `UPRES_WRAP_PAD`.
    """
    name = os.path.basename(variant_dir.rstrip("/\\"))
    src = os.path.join(variant_dir, f"{name}_basecolor.png")
    if not os.path.isfile(src):
        raise ComfyError(f"staged variant {name} has no basecolor to upscale")
    meta = {}
    try:
        with open(os.path.join(variant_dir, "meta.json")) as fh:
            meta = json.load(fh) or {}
    except (OSError, ValueError):
        pass

    with open(src, "rb") as fh:
        tile = comfy_maps.read_png(fh.read())
    send = src
    if wrap_pad:
        send = os.path.join(variant_dir, f"{name}_upres_input.png")
        comfy_maps.write_png(send, comfy_maps.wrap_pad(tile, wrap_pad))

    graph, prov = load_workflow(workflow)
    values, full_prompt, ckpt = _texture_values(meta.get("artist_prompt", ""),
                                                seed=meta.get("seed", 0), size=1024,
                                                negative=None, checkpoint=checkpoint, prov=prov)
    values.pop("BOB_SIZE", None)  # `tex_upres`'s size comes from the image, not an empty latent
    values["BOB_IMAGE"] = {"image": upload_image(send, url=url, subfolder="bob")}
    values["BOB_SEED"].update(denoise=float(denoise), upscale_by=float(scale))

    mark_tiling_applied(url)
    try:
        png, gen = generate_image((graph, prov), values, route="texture", url=url, timeout=timeout,
                                  on_progress=on_progress, on_queued=on_queued,
                                  required_titles=("BOB_IMAGE", "BOB_SEED", "BOB_OUT"))
    finally:
        if send != src and os.path.isfile(send):
            os.remove(send)
    upscaled = comfy_maps.read_png(png)
    if wrap_pad:
        # The pad scaled with the image, so the crop does too. Trust the measured factor rather
        # than the requested one: UltimateSDUpscale rounds to whole tiles.
        upscaled = comfy_maps.crop_wrap_blend(
            upscaled, int(round(wrap_pad * upscaled.shape[0] / (tile.shape[0] + 2 * wrap_pad))))
    maps = comfy_maps.derive(upscaled)
    seam = comfy_maps.seam_report(maps["basecolor"])
    size = maps["basecolor"].shape[0]
    meta.update(upres={"scale": scale, "denoise": denoise, "workflow": workflow,
                       "seam_before": meta.get("seam"), "prompt_id": gen["prompt_id"]},
                seam=seam, size=size)
    written = write_texture_set(variant_dir, name, maps,
                                _source_text(workflow, prov, ckpt, meta.get("seed", 0), size,
                                             full_prompt),
                                meta=meta)
    return {"dir": variant_dir, "name": name, "maps": written, "seam": seam, "size": size,
            "seconds": {"generate": gen["seconds"]}}


def texture_set_from_prompt(prompt_text, pack_dir, *, seed=0, size=1024, negative=None,
                            checkpoint=None, url=None, workflow="tex_tileable",
                            timeout=600, on_progress=None, reference=None, delight=False):
    """Generate one seamless texture set straight into `<pack>/textures/`, skipping staging.

    The one-shot path: what the first spike measured and what a headless script or an MCP tool wants
    when there is nobody there to pick between variants. Returns (set_name, info).

    Blocking. The caller owns the wait cursor, or runs it through `core.comfy_jobs`.
    """
    textures = os.path.join(pack_dir, "textures")
    os.makedirs(textures, exist_ok=True)
    name = unique_set_name(textures, slugify(prompt_text))
    info = texture_variant(prompt_text, os.path.join(textures, name), seed=seed, size=size,
                           negative=negative, checkpoint=checkpoint, url=url, workflow=workflow,
                           reference=reference, timeout=timeout, on_progress=on_progress,
                           delight=delight)
    return name, info


# -- BobFoliage's two texture jobs (docs/FOLIAGE.md, the texture sets) -----------------------------
# Neither needs a new workflow or a new model. The leaf atlas is `mesh_subject` with its alpha kept
# and the grid composed Bob-side; bark is `tex_tileable` through the ordinary `texture_variant`,
# with one prompt clause and one new measurement. What each needs instead is a clause the artist
# should not have to remember, for the same reason `PROMPT_SUFFIX` and `SUBJECT_SUFFIX` exist.

# Bark's clause, and it is MEASURED rather than chosen. Bark grain is directional and a tileable
# SDXL pass has no reason to keep an axis, so four wordings were tried across two species and two
# seeds and only one held (worst case, degrees off vertical):
#
#   "vertical bark, deep furrows running top to bottom"            17.6   <- this one
#   "vertical grain running straight up and down"                  71.3
#   "deep vertical furrows and ridges, grain parallel to the
#    trunk axis, top to bottom"                                    84.8
#   no clause at all                                               83.8
#
# The failures are not subtle: "rough conifer bark" on its own came back as polygonal mud cracks
# with no axis whatsoever. Naming the FEATURE (furrows) and its direction is what works; naming the
# direction alone is not enough, and adding more words made it worse. `comfy_maps.grain_report` is
# the measurement, and `headless_foliage.py` holds the threshold, so a checkpoint change that breaks
# this is a gate failure rather than a silently plastic trunk.
BARK_SUFFIX = "vertical bark, deep furrows running top to bottom"

# The leaf-atlas sprite clause. Appended in front of `SUBJECT_SUFFIX`, which already asks for one
# centred uncropped subject on a plain background -- exactly what one atlas cell is.
#
# Every clause here is MEASURED, and the measurement is a class pass rate rather than one atlas:
# `headless_gen_class_rates.py --classes atlas` runs eight ordinary requests and counts how many come
# back with an empty receipt. On the previous wording that rate was **0 of 4**, and the two failures it
# split into are what the three additions answer.
#
#   the WOODY TWIG. `orient_sprite` has two cues and both read the twig: the green/brown split gives a
#     direct axis, and a fan of needles against a solid stub gives the fallback. The previous wording
#     asked for "the cut end of its stem", which a diffusion model honours as a crop rather than as a
#     twig -- measured, woody fraction 0.000 to 0.002 against a 0.01 floor across four cells, so
#     `_woody_axis` returned None on every one and each sprite was turned by the principal axis of a
#     round shape: +91, +71, +103 and -11 degrees, arbitrary, with nothing in the receipt saying so.
#     With a twig actually asked for, the same seed measured 0.108 to 0.254 and 4 of 4 resolved.
#   FRESH GREEN foliage against the brown twig, which is the clause that looks redundant and is not.
#     The woody cue is a green/brown SPLIT, so it needs green to split from: a birch atlas came back
#     autumn-coloured, one cell measured woody fraction 0.554 -- over half the sprite reading as wood --
#     and the split became meaningless. 1 of 4 cells resolved. Asking for green took the same seed to
#     4 of 4.
#   the FLATBED SCAN. `SUBJECT_SUFFIX` already asks for even diffuse studio lighting and it is not
#     enough: measured 1.317 stops of light ramping across a sprite against a 0.55 bar, delighting ran
#     and did not clear it. Naming the imaging method rather than the lighting took the same seed and
#     prompt to 0.627. A card is lit from both sides, so a key baked into a sprite has no camera angle
#     that hides it.
#
# "flat lay" is gone, and deliberately: it is one step from "pressed flat like a herbarium specimen",
# which is the wording that removed the stem AND the elongation and cost an approval gate. The scan
# clause carries the perpendicular camera without asking for a flattened subject.
# MEASURED AND REVERTED, kept here because the null is the useful part. The three clauses above were
# tried as a suffix and re-measured on the same four prompts: the rate stayed 0 of 4 and the light got
# WORSE, `baked-light` firing on 4 samples against 2 before, with `orientation-guess` still on 3. The
# hand-tuned wins those clauses came from were real -- they are quoted above with their figures -- and
# they were wins on ONE prompt at ONE seed, which is not the same claim as a suffix that helps every
# request. Shipping it on the strength of the hand runs would have made the shared clause worse for
# everyone and looked like progress.
#
# What the null points at instead: if four ordinary requests out of four fail the same bar, the bar is
# the more likely defect. `gen_receipt.LEAF_RAMP_STOPS_MAX = 0.55` rests on two points, one of them
# synthetic, and docs/ROADMAP.md already lists it as needing a second batch -- this is that batch, and
# it says the bar rejects the entire class. A gate no ordinary sample can pass is a blocked route
# wearing a gate's clothes.
ATLAS_SPRITE_SUFFIX = ("a single sprig, the cut end of its stem at the bottom of the frame, "
                       "foliage fanning upward, flat lay")

# `mesh_subject`'s own negative is written for a solid: it forbids "multiple objects, group,
# collection", which is right for a boulder and right for one sprite, and it also forbids a ground
# plane and a cast shadow, which a matte-and-composite route particularly wants gone.
ATLAS_NEGATIVE = ("multiple sprigs, bunch, bouquet, arrangement, grid, collage, whole tree, trunk, "
                  "branch, ground, soil, pot, vase, hand, scene, depth of field, blur, "
                  "cast shadow, reflection, text, watermark, signature, border, frame")

# How the cells of an atlas are produced. "cells" is the default and the route the gate measures.
#
# **A diffusion model cannot be asked for a grid.** Measured: `mesh_subject` with "a 2 by 2 grid of
# four separate pine needle sprays, one spray per quadrant, each growing upward from the bottom of
# its quadrant" returned FIVE sprays arranged in a ring, straddling every cell boundary, each
# pointing a different way, none touching a cell's bottom edge. Per-cell coverage passed anyway (8
# to 11% opaque in all four quadrants), which is why coverage alone is not the check and why this is
# a route rather than an argument: the atlas was tree-shaped and unusable.
#
# So "cells" generates ONE sprite per cell and composes the grid in numpy
# (`comfy_maps.atlas_compose`). That makes the layout a guarantee instead of a hope, makes the cells
# differ by construction (a seed apart), lets Bob orient and bottom-anchor each sprite, and costs
# LESS: four sprites at 512 measured 9.4 s against 7.3 s for one 1024 frame that could not be used.
# "grid" is kept because it is one call and an artist may want to hand-author the layout prompt, and
# because a future model that CAN lay out a grid should not need new code to be tried.
ATLAS_ROUTES = ("cells", "grid")
DEFAULT_ATLAS_ROUTE = "cells"

# A grid-route prompt says the layout out loud; it is the wording measured as insufficient, kept
# so the route is honest about what it asks for.
ATLAS_GRID_SUFFIX = ("evenly spaced in a regular grid, one sprig per cell, each growing upward from "
                     "the bottom of its cell, flat lay, plain background")


def atlas_routes():
    return ATLAS_ROUTES


def _atlas_route(route=None):
    route = (route or DEFAULT_ATLAS_ROUTE).strip().lower()
    if route not in ATLAS_ROUTES:
        raise ComfyError(f"unknown atlas route {route!r}, expected one of {list(ATLAS_ROUTES)}")
    return route


def update_meta(set_dir, **fields):
    """Merge `fields` into a set's `meta.json` and return the whole dict.

    A second writer rather than a parameter on `texture_variant`, so the bark measurements travel
    with the artifact (the provenance rule) without changing what every other caller of the
    texture-set recipe writes."""
    path = os.path.join(set_dir, "meta.json")
    meta = {}
    try:
        with open(path) as fh:
            meta = json.load(fh) or {}
    except (OSError, ValueError):
        pass
    meta.update(fields)
    with open(path, "w") as fh:
        json.dump(meta, fh, indent=2, sort_keys=True)
    return meta


def bark_set(prompt_text, pack_dir, *, name=None, seed=0, size=1024, url=None, negative=None,
             checkpoint=None, timeout=600, on_progress=None, reference=None, delight=False):
    """Generate a bark texture set into `<pack>/textures/`, measured for grain DIRECTION.

    `texture_variant` does the work: bark needs no new workflow, and the seam ratio it already
    reports is the tileability half. What this adds is the half the seam ratio cannot see -- bark
    grain runs along the trunk, and a set whose grain wanders is unusable on a swept limb however
    well it tiles. `BARK_SUFFIX` is the clause that makes it hold and `grain` in the returned info
    (and in the set's `meta.json`) is the number that says whether it did.

    `name` fixes the set's folder name, which is what lets a species preset NAME the bark it wants
    (`bark_conifer`) and pick it up the moment it is generated. Still never an overwrite: a second
    `bark_conifer` becomes `bark_conifer_02` (the naming rule), and the preset keeps the one it
    already resolves.

    Returns (set_name, info). `info["grain"]["off_vertical_deg"]` is the verdict.
    """
    textures = os.path.join(pack_dir, "textures")
    os.makedirs(textures, exist_ok=True)
    set_name = unique_set_name(textures, slugify(name or prompt_text))
    out_dir = os.path.join(textures, set_name)
    full = ", ".join(p for p in ((prompt_text or "").strip(), BARK_SUFFIX) if p)
    info = texture_variant(full, out_dir, seed=seed, size=size, negative=negative,
                           checkpoint=checkpoint, url=url, reference=reference, timeout=timeout,
                           on_progress=on_progress, delight=delight)
    with open(info["maps"]["basecolor"], "rb") as fh:
        albedo = comfy_maps.read_png(fh.read())
    grain = comfy_maps.grain_report(albedo)
    info["grain"] = grain
    info["name"] = set_name
    update_meta(out_dir, grain=grain, bark_clause=BARK_SUFFIX, artist_prompt=(prompt_text or "").strip())
    return set_name, info


def atlas_sprite(prompt_text, out_path, *, seed=0, size=512, negative=None, url=None,
                 timeout=600, on_progress=None, preflight_graph=True):
    """One atlas cell's sprite: `mesh_subject` with the sprig clause and a real cutout. Returns info."""
    full = ", ".join(p for p in ((prompt_text or "").strip(), ATLAS_SPRITE_SUFFIX) if p)
    return subject_image(full, out_path, seed=seed, size=size,
                         negative=negative or ATLAS_NEGATIVE, url=url, timeout=timeout,
                         on_progress=on_progress, preflight_graph=preflight_graph)


def leaf_atlas(prompt_text, pack_dir, *, cols=2, rows=2, seed=0, size=1024, route=None, name=None,
               negative=None, url=None, timeout=600, on_progress=None, on_cell=None,
               delight=False):
    """Generate a leaf/needle ATLAS into `<pack>/textures/` as a set with an `opacity` role.

    The consuming side was built with the leaf cards and is waiting: `opacity` is in
    `assets.TEXTURE_MAP_ROLES` and `materials.surface._wire_cutout` already prefers a dedicated
    opacity map over the basecolor's own alpha, so nothing downstream needs a change for this to
    reach a card's Principled Alpha.

    Written as an RGB basecolor plus a separate grey `opacity`, not as one RGBA file: `write_png` is
    an 8-bit grey/RGB codec and the role split is what the resolver and the cutout already speak.
    That makes the transparent region's COLOUR load-bearing, because bilinear filtering blends it
    into every silhouette -- so it is flooded with leaf colour (`comfy_maps.alpha_bleed`) rather
    than left as the studio background, which is a white rim on every needle.

    The grid is recorded in the set's `meta.json` as `atlas: {cols, rows}`, which answers the open
    question in docs/FOLIAGE.md 6: the set CARRIES its layout, `assets.atlas_grid()` reads it, and
    the recipe's `Atlas Columns` / `Atlas Rows` params stay as the override.

    Returns (set_name, info). `info["cells"]` is the per-cell report -- an empty cell is a card that
    renders as nothing, so it is measured here and gated in `headless_foliage.py`.
    """
    route = _atlas_route(route)
    cols, rows, size = max(1, int(cols)), max(1, int(rows)), int(size)
    textures = os.path.join(pack_dir, "textures")
    os.makedirs(textures, exist_ok=True)
    set_name = unique_set_name(textures, slugify(name or f"{prompt_text} atlas"))
    out_dir = os.path.join(textures, set_name)
    cells_dir = os.path.join(out_dir, "cells")
    os.makedirs(cells_dir, exist_ok=True)
    # A failed generation must not keep the name it asked for. The directory has to exist before the
    # first sprite is written, and `unique_set_name` hands out the next free suffix, so without this a
    # call that dies mid-generation leaves an EMPTY set holding the name and sends the retry to `_02`.
    # Measured the hard way: three OOMed atlas calls left `leaf_conifer`, `leaf_conifer_02` and
    # `leaf_conifer_03` as empty shells, and `leaf_conifer` is the name the conifer species preset
    # resolves BY NAME with no assignment step (the atlas naming rule). A set that resolves zero maps
    # renders as a solid tint with every receipt still reporting success, so the next foliage build
    # would have worn a flat green card and said it was fine.
    import shutil

    try:
        return _leaf_atlas(prompt_text, out_dir, set_name, cells_dir, route=route, cols=cols,
                           rows=rows, size=size, seed=seed, negative=negative, url=url,
                           timeout=timeout, on_progress=on_progress, on_cell=on_cell,
                           delight=delight)
    except BaseException:
        shutil.rmtree(out_dir, ignore_errors=True)
        raise


def _leaf_atlas(prompt_text, out_dir, set_name, cells_dir, *, route, cols, rows, size, seed,
                negative, url, timeout, on_progress, on_cell, delight):
    """`leaf_atlas`'s body, split out so the caller can free the set name if this raises. See there."""
    t0 = time.time()
    sources = []
    orientations = []
    if route == "cells":
        cell = max(64, size // max(cols, rows))
        sprites = []
        for i in range(cols * rows):
            path = os.path.join(cells_dir, f"cell_{i:02d}.png")
            # A seed a STRIDE apart, not one apart: adjacent SDXL seeds are not adjacent images, but
            # a stride makes "cell 3 of this atlas" reproducible from (seed, index) alone.
            info = atlas_sprite(prompt_text, path, seed=int(seed) + i * 17, size=cell,
                                negative=negative, url=url, timeout=timeout,
                                on_progress=on_progress, preflight_graph=(i == 0))
            with open(path, "rb") as fh:
                sprites.append(comfy_maps.read_png(fh.read()))
            sources.append(info)
            if on_cell:
                on_cell(i + 1, cols * rows, info)
        basecolor, opacity = comfy_maps.atlas_compose(sprites, cols, rows, size,
                                                      orientations=orientations)
    else:
        full = ", ".join(p for p in ((prompt_text or "").strip(),
                                     f"a {cols} by {rows} grid of sprigs", ATLAS_GRID_SUFFIX) if p)
        path = os.path.join(cells_dir, "grid.png")
        info = subject_image(full, path, seed=int(seed), size=size,
                             negative=negative or ATLAS_NEGATIVE, url=url, timeout=timeout,
                             on_progress=on_progress)
        sources.append(info)
        with open(path, "rb") as fh:
            frame = comfy_maps.read_png(fh.read())
        if frame.ndim != 3 or frame.shape[2] != 4:
            raise ComfyError("the atlas frame came back with no alpha channel (is BOB_RGBA wired?)")
        opacity = frame[:, :, 3].copy()
        basecolor = comfy_maps.alpha_bleed(frame[:, :, :3], opacity.astype("float32") / 255.0)
    generate_s = time.time() - t0

    t1 = time.time()
    # The atlas is the case delighting matters most for, because a card is lit from BOTH sides: the
    # renderer lights the back of a leaf with the sun behind it, and a key baked into the sprite
    # fights it with no camera angle that hides the argument. Measured inside the opacity mask of
    # the three generated atlases, albedo luminance spanned 1.21 stops
    # (broadleaf), 1.82 (conifer) and 1.84 (grass), which is several times what a real leaf varies.
    #
    # Delighted BEFORE the derivations for `derive`'s reason, and measured INSIDE the mask because
    # that is the only part a card shows: the cleared background is whatever the bleed left there.
    generated_flatness = comfy_maps.flatness_report(basecolor)
    if delight:
        basecolor = comfy_maps.delight(basecolor)
    maps = comfy_maps.derive(basecolor)
    maps["opacity"] = opacity
    flatness = comfy_maps.flatness_report(basecolor)
    flatness["delighted"] = bool(delight)
    flatness["as_generated"] = generated_flatness["low_freq_variation"]
    flatness["in_mask_stops"] = comfy_maps.mask_stops(basecolor, opacity)
    # Split per cell into the light ON the sprite and the sprite's own relief, and gate the first
    # only (`LEAF_RAMP_STOPS_MAX`). The worst cell rather than a mean: one card in four hanging a
    # lit sprite in front of the camera is still a lit sprite in front of the camera.
    light = comfy_maps.cell_light_split(basecolor, opacity, cols, rows)
    ramps = [c["ramp_stops"] for c in light if c["ramp_stops"] is not None]
    details = [c["detail_stops"] for c in light if c["detail_stops"] is not None]
    flatness["in_mask_ramp_stops"] = max(ramps) if ramps else None
    flatness["in_mask_detail_stops"] = max(details) if details else None
    cells = comfy_maps.atlas_cells(opacity, cols, rows)
    # The orientation goes INTO the cell rows rather than beside them, because it answers the same
    # question the rest of the row does -- is this cell a usable card -- and a figure in a second
    # table is a figure nobody reads. Absent on the grid route, which composes nothing and rotates
    # nothing.
    by_cell = {int(o.get("cell", -1)): o for o in orientations}
    light_by_cell = {int(o["cell"]): o for o in light}
    for row in cells:
        found = by_cell.get(int(row["cell"]))
        if found:
            row["orient"] = {k: v for k, v in found.items() if k != "cell"}
        split = light_by_cell.get(int(row["cell"]))
        if split:
            row["ramp_stops"] = split["ramp_stops"]
            row["detail_stops"] = split["detail_stops"]
    distinct = comfy_maps.cell_distinctness(opacity, cols, rows)
    derive_s = time.time() - t1

    t2 = time.time()
    alpha = opacity.astype("float32") / 255.0
    meta = {"artist_prompt": (prompt_text or "").strip(), "atlas": {"cols": cols, "rows": rows},
            "route": route, "seed": int(seed), "size": int(size), "cells": cells,
            "cell_distinctness": distinct, "workflow": "mesh_subject",
            "clear_fraction": float((alpha < 0.05).mean()),
            "opaque_fraction": float((alpha > 0.95).mean()),
            "flatness": flatness,
            "prompt_ids": [s.get("prompt_id") for s in sources]}
    source_text = (
        f"basecolor + opacity: generated by ComfyUI, workflow mesh_subject, "
        f"{len(sources)} frame(s), seed {int(seed)}, composed by BobBlenderTools into a "
        f"{cols}x{rows} atlas ({route} route).\n"
        f"prompt: {prompt_text}\n"
        f"sprite clause: {ATLAS_SPRITE_SUFFIX}\n"
        "roughness, height, normal, ao: derived from the composed albedo by BobBlenderTools "
        "(core/comfy_maps.py). opacity is the generation's own matte.\n"
        "Output licensing follows the model that produced it.\n")
    written = write_texture_set(out_dir, set_name, maps, source_text, meta=meta)
    write_s = time.time() - t2

    info = dict(meta)
    info.update(dir=out_dir, name=set_name, maps=written, cols=cols, rows=rows,
                seconds={"generate": generate_s, "derive": derive_s, "write": write_s,
                         "total": generate_s + derive_s + write_s})
    return set_name, info


# -- Generated meshes (the mesh-generation family, ComfyUI half)
# -------------------------------------------------- Everything below runs on the worker thread and
# touches no bpy. It gets a raw generated GLB into `<pack>/_staging/<variant>/`; turning that into a
# scattered BobShader asset is `core.gen_assets`, which needs Blender and owns pipeline steps 6 to
# 8.
#
# The tiers are the resolution combo on `LoadTrellis2Models`, NOT a model file. `geometry_only_1536`
# turns out to be `1536_cascade`: a plain "1536" is not one of the four options the node offers.
MESH_TIERS = {"preview": "512", "default": "1024", "hero": "1536_cascade"}


def _tier_route(resolution):
    """The VRAM floor a geometry job runs under, from the resolution it actually asked the node for.

    Off the RESOLUTION rather than the tier name so `tier="1536_cascade"` passed raw is read the
    same as `tier="hero"`; the hero cascade needs materially more of the card than the default 1024
    and sharing one floor would let it through and then OOM inside somebody else's worker.
    """
    return "mesh_hero" if resolution == MESH_TIERS["hero"] else "mesh"


def subject_prompt(prompt_text):
    """The artist's prompt with `mesh_subject`'s single-subject clause appended."""
    return ", ".join(p for p in ((prompt_text or "").strip(), SUBJECT_SUFFIX) if p)


def subject_image(prompt_text, out_path, *, seed=0, size=1024, negative=None, checkpoint=None,
                  url=None, workflow="mesh_subject", timeout=600, on_progress=None,
                  on_queued=None, preflight_graph=True):
    """`mesh_subject`: prompt to one single-subject RGBA reference PNG at `out_path`. Returns info.

    The alpha is the contract, not the white background. The geometry graphs feed `LoadImage`'s
    (inverted) mask into `Trellis2GetConditioning`, so an opaque PNG makes the whole square frame
    the subject and the result is the object sealed inside a transparent shell (the pack install).
    """
    graph, prov = load_workflow(workflow)
    full = subject_prompt(prompt_text)
    values = {"BOB_PROMPT": {"text": full},
              "BOB_SEED": {"seed": int(seed)},
              "BOB_SIZE": {"width": int(size), "height": int(size)}}
    if negative:
        values["BOB_NEG"] = {"text": negative}
    ckpt = checkpoint or prov.get("default_checkpoint")
    if ckpt:
        values["BOB_CKPT"] = {"ckpt_name": ckpt}

    # A subject image must NOT wrap: it is a single centred object, and a circular UNet would carry
    # its edge round the frame. So undo any padding a texture set left on the shared model.
    #
    # The floor is read BEFORE that reset, for `stylize_render`'s measured reason: the reset is an
    # SDXL job, so afterwards the card reads as full of the very model this needs, and the chokepoint
    # would refuse a job that was about to run. Hence route=None below.
    preflight_vram("texture", url=url)
    ensure_untiled(url, on_progress=on_progress)
    png, gen = generate_image((graph, prov), values, route=None, url=url, timeout=timeout,
                              on_progress=on_progress, on_queued=on_queued,
                              preflight_graph=preflight_graph,
                              required_titles=("BOB_PROMPT", "BOB_SEED", "BOB_OUT"))
    os.makedirs(os.path.dirname(os.path.abspath(out_path)) or ".", exist_ok=True)
    with open(out_path, "wb") as fh:
        fh.write(png)
    return {"path": out_path, "prompt": full, "artist_prompt": (prompt_text or "").strip(),
            "checkpoint": ckpt, "seed": int(seed), "size": int(size),
            "workflow": workflow, "prompt_id": gen["prompt_id"], "seconds": gen["seconds"]}


def process_mesh_values(remesh=True):
    """The `Trellis2ProcessMesh` binding, and the one knob on it that changes what TRELLIS.2 IS.

    `remesh` runs a dual-contouring remesh that returns a WATERTIGHT shell. On the bundled
    `geometry_only_*` graphs it is on, and the asset gate measured what that costs: the same leaf
    comes back with 0 boundary edges with it on and 11,620 with it off, at the same 0.04
    thinnest/longest axis ratio. So the open-surface capability that makes TRELLIS.2 primary (the
    open-surface rule) is present in the model and switched OFF by the shipped graph's default.
    Foliage wants `remesh=False`; a rock wants it on, because a closed shell is what a rock should
    be and the remesh cleans the isosurface up.

    The sub-widgets are branch-specific, so the two cases name different fields. Sending the other
    branch's fields is what a dynamic combo cannot take.
    """
    if remesh:
        return {"remesh": "on", "remesh.remesh_band": 1.0, "remesh.remove_inner_faces": True}
    return {"remesh": "off", "remesh.fill_holes": False, "remesh.fill_holes_perimeter": 0.03}


def bind_process(graph, values, *, remesh=True, faces=None):
    """Bind `BOB_PROCESS` (a `Trellis2ProcessMesh`) and return the graph the binding needs.

    Separate from `template()` because a dynamic combo cannot simply be merged into: the selected
    key owns its sub-widgets, so the OTHER branch's `remesh.*` fields have to be dropped from the
    graph rather than overridden. Shared by `mesh_geom_trellis` and `mesh_geom_texture`, which both
    carry the node.

    `faces` binds `target_face_count`. On `mesh_geom_trellis` that stays at the graph's 500000,
    because Bob's simplify budget is applied later; on `mesh_geom_texture` it IS the budget, because
    `Trellis2ProcessMesh` is simplify plus weld plus unwrap in one node and that is what makes the
    one-shot route one-shot.
    """
    nid = titles(graph).get("BOB_PROCESS")
    if nid is None:
        return graph
    bound = process_mesh_values(remesh)
    if faces is not None:
        bound["target_face_count"] = int(faces)
    values["BOB_PROCESS"] = bound
    return {k: ({**n, "inputs": {f: v for f, v in n["inputs"].items()
                                 if not f.startswith("remesh.")}} if k == nid else n)
            for k, n in graph.items()}


def mesh_geometry(image_path, out_path, *, seed=0, tier="default", url=None, remesh=True,
                  workflow="mesh_geom_trellis", timeout=1800, on_progress=None, on_queued=None,
                  preflight_graph=True):
    """`mesh_geom_trellis`: a subject PNG with alpha to a dense UV-unwrapped GLB at `out_path`. Returns
    info.

    `remesh=False` keeps open surfaces; see `process_mesh_values`. It is the difference between a
    leaf and a leaf-shaped bag.
    """
    graph, prov = load_workflow(workflow)
    resolution = MESH_TIERS.get(tier, tier)
    values = {"BOB_IMAGE": {"image": upload_image(image_path, url=url, subfolder="bob")},
              "BOB_SEED": {"seed": int(seed)}}
    if "BOB_MODEL" in titles(graph):
        values["BOB_MODEL"] = {"resolution": resolution}
    graph = bind_process(graph, values, remesh=remesh)
    data, gen = generate_mesh((graph, prov), values, route=_tier_route(resolution),
                              url=url, timeout=timeout, on_progress=on_progress,
                              on_queued=on_queued,
                              preflight_graph=preflight_graph,
                              required_titles=("BOB_IMAGE", "BOB_SEED", "BOB_OUT"))
    return _write_mesh(out_path, data, gen, workflow=workflow, prov=prov,
                       extra={"seed": int(seed), "tier": tier, "resolution": resolution,
                              "remesh": bool(remesh), "subject": image_path})


def mesh_geom_alt(image_path, out_path, *, seed=0, url=None, workflow="mesh_geom_alt",
                  checkpoint=None, size=1024, timeout=1800, on_progress=None, on_queued=None,
                  preflight_graph=True):
    """`mesh_geom_alt`: the same subject PNG to a mesh through the CHALLENGER model, Hunyuan3D 2.1.

    Same inputs and the same output contract as `mesh_geometry`, which is what makes the geometry
    A/B a config change rather than a rewrite. Two differences are structural rather than settings,
    and both are the geometry A/B verdict rather than defects:

    - the output is watertight whatever the caller wants, because `VoxelToMesh` extracts an
      isosurface, so there is no `remesh` argument here to turn open surfaces on with;
    - it carries no texture and no UVs at all, so the challenger route runs `mesh_process` (`mesh_process`)
      and then `mesh_texture` (`mesh_texture`) behind it. `generate_asset_alt` is that chain.

    `size` is the plain plate the subject is composited onto through its own alpha. `mesh_subject`'s
    RGB is still the SDXL frame behind the cutout and `LoadImage` drops alpha rather than
    compositing it, so without the plate this model would be conditioned on a background TRELLIS.2
    never sees.
    """
    graph, prov = load_workflow(workflow)
    values = {"BOB_IMAGE": {"image": upload_image(image_path, url=url, subfolder="bob")},
              "BOB_SEED": {"seed": int(seed)},
              "BOB_PLATE": {"width": int(size), "height": int(size)}}
    ckpt = checkpoint or prov.get("default_checkpoint")
    if ckpt and "BOB_3D_MODEL" in titles(graph):
        values["BOB_3D_MODEL"] = {"ckpt_name": ckpt}
    data, gen = generate_mesh((graph, prov), values, route="mesh", url=url, timeout=timeout,
                              on_progress=on_progress, on_queued=on_queued,
                              preflight_graph=preflight_graph,
                              required_titles=("BOB_IMAGE", "BOB_SEED", "BOB_OUT"))
    return _write_mesh(out_path, data, gen, workflow=workflow, prov=prov,
                       extra={"seed": int(seed), "subject": image_path, "plate": int(size),
                              "model": "hunyuan3d-2.1"})


def mesh_texture(mesh_path, image_path, out_path, *, seed=0, texture_size=1024, url=None,
                 workflow="mesh_texture", timeout=1800, on_progress=None, on_queued=None,
                 preflight_graph=True):
    """`mesh_texture`: PBR-texture an existing mesh in its own UVs, writing the textured GLB to
    `out_path`.

    `mesh_path` MUST already be unit-normalised. `Trellis2EncodeMesh` voxelises in unit-cube space,
    so a metre-scale mesh lands outside the grid, the encoder sees nothing, and the albedo comes
    back silently black. `core.gen_assets` does the normalise-then-rescale round trip; this function
    does not, because it has no bpy and no idea what the mesh means.
    """
    graph, prov = load_workflow(workflow)
    values = {"BOB_MESH": {"mesh_path": upload_mesh(mesh_path, url=url)},
              "BOB_IMAGE": {"image": upload_image(image_path, url=url, subfolder="bob")},
              "BOB_SEED": {"seed": int(seed)},
              "BOB_TEXSIZE": {"texture_size": int(texture_size)}}
    data, gen = generate_mesh((graph, prov), values, route="paint", url=url, timeout=timeout,
                              on_progress=on_progress, on_queued=on_queued,
                              preflight_graph=preflight_graph,
                              required_titles=("BOB_MESH", "BOB_IMAGE", "BOB_SEED", "BOB_OUT"))
    return _write_mesh(out_path, data, gen, workflow=workflow, prov=prov,
                       extra={"seed": int(seed), "texture_size": int(texture_size),
                              "source_mesh": mesh_path, "subject": image_path})


def mesh_geom_texture(image_path, out_path, *, seed=0, tier="default", faces=4000,
                      texture_size=1024, remesh=True, url=None, workflow="mesh_geom_texture",
                      timeout=1800, on_progress=None, on_queued=None, preflight_graph=True):
    """`mesh_geom_texture`: a subject PNG with alpha to a simplified, unwrapped, PBR-TEXTURED GLB in
    one job.

    The one-shot alternative to `mesh_geom_trellis` plus `mesh_simplify_uv` plus `mesh_texture`, and
    it is one graph rather than three because `Trellis2ProcessMesh` already simplifies, welds and
    unwraps: bind `faces` and `Trellis2RasterizePBR` bakes the PBR into the budget mesh's own
    charts.

    What it does NOT produce is a dense mesh, so there is no high-poly surface left to bake a detail
    normal or AO from. That is the whole trade the route A/B measured; the numbers are in
    docs/GENERATION.md.
    """
    graph, prov = load_workflow(workflow)
    resolution = MESH_TIERS.get(tier, tier)
    values = {"BOB_IMAGE": {"image": upload_image(image_path, url=url, subfolder="bob")},
              "BOB_SEED": {"seed": int(seed)},
              "BOB_TEXSEED": {"seed": int(seed)},
              "BOB_TEXSIZE": {"texture_size": int(texture_size)}}
    if "BOB_MODEL" in titles(graph):
        values["BOB_MODEL"] = {"resolution": resolution}
    graph = bind_process(graph, values, remesh=remesh, faces=faces)
    data, gen = generate_mesh((graph, prov), values, route=_tier_route(resolution),
                              url=url, timeout=timeout, on_progress=on_progress,
                              on_queued=on_queued,
                              preflight_graph=preflight_graph,
                              required_titles=("BOB_IMAGE", "BOB_SEED", "BOB_OUT"))
    return _write_mesh(out_path, data, gen, workflow=workflow, prov=prov,
                       extra={"seed": int(seed), "tier": tier, "resolution": resolution,
                              "remesh": bool(remesh), "faces_requested": int(faces),
                              "texture_size": int(texture_size), "subject": image_path})


def mesh_geom_mv(view_paths, out_path, *, seed=0, url=None, workflow="mesh_geom_mv",
                 timeout=1800, on_progress=None, on_queued=None, preflight_graph=True):
    """`mesh_geom_mv`: four cardinal views to a watertight GLB through Hunyuan3D multi-view
    conditioning.

    `view_paths` is (front, left, back, right) in that order, which is the order
    `Hunyuan3Dv2ConditioningMultiView`'s own sockets name. Views come from Blender when a block-out
    exists, which is what makes them consistent by construction rather than by luck.
    """
    graph, prov = load_workflow(workflow)
    values = {}
    for title, path in zip(("BOB_VIEW_FRONT", "BOB_VIEW_LEFT", "BOB_VIEW_BACK", "BOB_VIEW_RIGHT"),
                           view_paths):
        values[title] = {"image": upload_image(path, url=url, subfolder="bob")}
    values["BOB_SEED"] = {"seed": int(seed)}
    data, gen = generate_mesh((graph, prov), values, route="mesh", url=url, timeout=timeout,
                              on_progress=on_progress, on_queued=on_queued,
                              preflight_graph=preflight_graph,
                              required_titles=("BOB_VIEW_FRONT", "BOB_SEED", "BOB_OUT"))
    return _write_mesh(out_path, data, gen, workflow=workflow, prov=prov,
                       extra={"seed": int(seed), "views": list(view_paths),
                              "model": "hunyuan3d-dit-v2-mv"})


def mesh_geom_mv_trellis(view_paths, out_path, *, seed=0, tier="default", remesh=True, faces=None,
                         url=None, workflow="mesh_geom_mv_trellis", timeout=1800, on_progress=None,
                         on_queued=None, preflight_graph=True):
    """`mesh_geom_mv_trellis`: the same four views through `Trellis2MultiViewImageToShape`, the
    TRELLIS.2 challenger.

    Same inputs and the same output contract as `mesh_geom_mv`, so the multi-view A/B is a config
    change rather than two pipelines: the open-surface rule's A/B slot, brought forward because
    `mesh_subject`'s framing turned out to be what decides an asset's shape.
    """
    graph, prov = load_workflow(workflow)
    resolution = MESH_TIERS.get(tier, tier)
    values = {}
    for title, path in zip(("BOB_VIEW_FRONT", "BOB_VIEW_LEFT", "BOB_VIEW_BACK", "BOB_VIEW_RIGHT"),
                           view_paths):
        values[title] = {"image": upload_image(path, url=url, subfolder="bob")}
    values["BOB_SEED"] = {"seed": int(seed)}
    if "BOB_MODEL" in titles(graph):
        values["BOB_MODEL"] = {"resolution": resolution}
    graph = bind_process(graph, values, remesh=remesh, faces=faces)
    data, gen = generate_mesh((graph, prov), values, route=_tier_route(resolution),
                              url=url, timeout=timeout, on_progress=on_progress,
                              on_queued=on_queued,
                              preflight_graph=preflight_graph,
                              required_titles=("BOB_VIEW_FRONT", "BOB_SEED", "BOB_OUT"))
    return _write_mesh(out_path, data, gen, workflow=workflow, prov=prov,
                       extra={"seed": int(seed), "tier": tier, "resolution": resolution,
                              "remesh": bool(remesh), "views": list(view_paths),
                              "model": "TRELLIS.2-4B"})


def omni_model_dir():
    """`<comfy>/models/hunyuan3d-omni` when the ComfyUI folder preference points at a checkout that
    has it, else None, which leaves `mesh_geom_ctrl`'s shipped HuggingFace repo id in place (the
    portability rule)."""
    base = comfy_dir()
    if base is None:
        return None
    path = os.path.join(base, "models", "hunyuan3d-omni")
    return path if os.path.isdir(path) else None


def mesh_geom_ctrl(control_path, image_path, out_path, *, seed=0, points=8192, steps=50,
                   guidance=4.5, octree=256, url=None, workflow="mesh_geom_ctrl", timeout=1800,
                   on_progress=None, on_queued=None, preflight_graph=True):
    """`mesh_geom_ctrl`: a block-out proxy plus a reference image to a mesh that keeps the block-out's
    shape.

    The one route in the mesh-generation family whose input is a mesh Bob already has rather than a
    picture of one, and the only one whose OUTPUT ORIENTATION means anything, so two Bob-side rules
    travel with it:

    - `control_path` MUST be unit-normalised. `core.gen_assets.export_control` is that round trip.
      Omni normalises into the unit cube and a metre-scale control lands outside it, which returns a
      plausible unconditioned generation rather than an error (the pack install trap, a second
      time).
    - the result comes back needing `gen_assets.CONTROL_RETURN_TURN`. Measured, per exporter, by the control gate; see that constant for why the chain is asymmetric.

    `points` is the control density. The node's own default is the control mesh's raw vertices,
    which for a block-out proxy is a few dozen of them.
    """
    graph, prov = load_workflow(workflow)
    values = {"BOB_CONTROL": {"mesh_path": upload_mesh(control_path, url=url)},
              "BOB_IMAGE": {"image": upload_image(image_path, url=url, subfolder="bob")},
              "BOB_SEED": {"seed": int(seed), "sample_point_count": int(points),
                           "num_inference_steps": int(steps), "guidance_scale": float(guidance),
                           "octree_resolution": int(octree)}}
    local = omni_model_dir()
    if local:
        values["BOB_OMNI"] = {"repo_or_path": local}
    data, gen = generate_mesh((graph, prov), values, route="mesh", url=url, timeout=timeout,
                              on_progress=on_progress, on_queued=on_queued,
                              preflight_graph=preflight_graph,
                              required_titles=("BOB_CONTROL", "BOB_IMAGE", "BOB_SEED", "BOB_OUT"))
    return _write_mesh(out_path, data, gen, workflow=workflow, prov=prov,
                       extra={"seed": int(seed), "control": control_path, "subject": image_path,
                              "points": int(points), "steps": int(steps),
                              "octree_resolution": int(octree), "model": "Hunyuan3D-Omni"})


# What `Hy3DOmniVoxelGenerate` will accept per bbox axis, from its own widget declaration
# (`bbox_length` / `bbox_height` / `bbox_depth`, min 0.1 max 3.0). ComfyUI validates widget bounds
# server-side, so a value outside this is an HTTP 400 rather than a clamp -- the undocumented
# ceiling a block-out run found. Here because it is a fact about the node, and callers
# that want to refuse early (the MCP tool does) need one place to read it from.
CONTROL_BBOX_RANGE = (0.1, 3.0)


def mesh_geom_bbox(dims, image_path, out_path, *, seed=0, steps=50, guidance=4.5, octree=256,
                   url=None, workflow="mesh_geom_bbox", timeout=1800, on_progress=None,
                   on_queued=None, preflight_graph=True):
    """`mesh_geom_bbox`: the same block-out conditioning as `mesh_geom_ctrl` with the control reduced
    to three numbers.

    `dims` is `[length, height, width]` in the control glb's frame, which is NOT Blender's;
    `core.gen_assets.control_bbox` is the one place that mapping lives. Pass None for the node's own
    estimate from the image, which is the null the bbox gate scores Bob's numbers against rather
    than a mode anyone should choose: it reads a silhouette Bob already knows the answer to.

    Two things `mesh_geom_ctrl` has to worry about and this does not. Nothing is uploaded, so the
    mesh-transport failure that made every other Omni route depend on `$BOB_COMFY_DIR` cannot occur
    here. And there is no unit-normalise round trip to forget, because a proportion has no scale.
    What does carry over unchanged is the return turn: same exporter, one glb write, so
    `gen_assets.CONTROL_RETURN_TURN` still applies.
    """
    graph, prov = load_workflow(workflow)
    control = {"seed": int(seed), "num_inference_steps": int(steps),
               "guidance_scale": float(guidance), "octree_resolution": int(octree),
               "auto_bbox": dims is None}
    if dims is not None:
        length, height, width = (float(d) for d in dims)
        control.update(bbox_length=length, bbox_height=height, bbox_depth=width)
    values = {"BOB_IMAGE": {"image": upload_image(image_path, url=url, subfolder="bob")},
              "BOB_SEED": control}
    local = omni_model_dir()
    if local:
        values["BOB_OMNI"] = {"repo_or_path": local}
    data, gen = generate_mesh((graph, prov), values, route="mesh", url=url, timeout=timeout,
                              on_progress=on_progress, on_queued=on_queued,
                              preflight_graph=preflight_graph,
                              required_titles=("BOB_IMAGE", "BOB_SEED", "BOB_OUT"))
    return _write_mesh(out_path, data, gen, workflow=workflow, prov=prov,
                       extra={"seed": int(seed), "control_bbox": None if dims is None
                              else [round(float(d), 5) for d in dims],
                              "subject": image_path, "steps": int(steps),
                              "octree_resolution": int(octree), "model": "Hunyuan3D-Omni"})


# Whether Bob's control mesh gets the extra -90 degree turn about X that `Hy3DOmniVoxelGenerate`
# applies by default, and the one place that answer lives. It does not, and the reason is worth
# keeping because the node's own default is the other way.
#
# Upstream's `inference.py` turns its control before sampling in `infer_voxel` and does not in
# `infer_point`, and the wrapper reproduces both faithfully. That asymmetry belongs to the two demo
# datasets' frames rather than to the model: `OmniEncoder.forward` reads point and voxel through the
# same Fourier embedder in the same [-1, 1] cube, and the only difference between the branches is
# the quantiser and the conditioning token. Bob's control glb is the file `mesh_geom_ctrl` already
# conditions on correctly, measured over all 24 axis-aligned rotations by the control gate, so the
# extra turn is a turn away from the frame that works. Measured by the voxel gate on the asymmetric
# block-out, both ways.
VOXEL_INPUT_ROTATION = False


def mesh_geom_voxel(control_path, image_path, out_path, *, seed=0, samples=81920, steps=50,
                    guidance=4.5, octree=256, rotate_input=None, url=None,
                    workflow="mesh_geom_voxel", timeout=1800, on_progress=None, on_queued=None,
                    preflight_graph=True):
    """`mesh_geom_voxel`: `mesh_geom_ctrl`'s block-out control read as a coarse occupancy grid rather
    than as a point cloud.

    Same control file, same loader, same `core.gen_assets.export_control` round trip and the same
    `gen_assets.CONTROL_RETURN_TURN` on the way back, so everything Bob-side is shared with
    `mesh_geom_ctrl` and the difference is one node. What that node does with the control is not
    shared: it area-samples `samples` points and `OmniEncoder.generate_voxel` quantises them onto a
    16-cubed grid, keeping each occupied cell's centre once. So `samples` is a FILLING budget rather
    than a detail budget -- at most 4,096 cells survive it, and the control that reaches the model
    is a ground plan at cell resolution.

    `rotate_input` overrides `VOXEL_INPUT_ROTATION`, which exists for the gate that measured it.
    """
    graph, prov = load_workflow(workflow)
    rotate = VOXEL_INPUT_ROTATION if rotate_input is None else bool(rotate_input)
    values = {"BOB_CONTROL": {"mesh_path": upload_mesh(control_path, url=url)},
              "BOB_IMAGE": {"image": upload_image(image_path, url=url, subfolder="bob")},
              "BOB_SEED": {"seed": int(seed), "sample_point_count": int(samples),
                           "apply_input_rotation": rotate,
                           "num_inference_steps": int(steps), "guidance_scale": float(guidance),
                           "octree_resolution": int(octree)}}
    local = omni_model_dir()
    if local:
        values["BOB_OMNI"] = {"repo_or_path": local}
    data, gen = generate_mesh((graph, prov), values, route="mesh", url=url, timeout=timeout,
                              on_progress=on_progress, on_queued=on_queued,
                              preflight_graph=preflight_graph,
                              required_titles=("BOB_CONTROL", "BOB_IMAGE", "BOB_SEED", "BOB_OUT"))
    return _write_mesh(out_path, data, gen, workflow=workflow, prov=prov,
                       extra={"seed": int(seed), "control": control_path, "subject": image_path,
                              "samples": int(samples), "apply_input_rotation": rotate,
                              "steps": int(steps), "octree_resolution": int(octree),
                              "model": "Hunyuan3D-Omni"})


# Which Omni control mode a block-out uses, and the one place that decision lives. Every mode ends
# at the same exporter and the same `gen_assets.CONTROL_RETURN_TURN`, so nothing downstream differs.
#
#   "point" `mesh_geom_ctrl`,  an area-sampled point cloud from the proxy's surface (8,192 points by default).
#   "bbox"  `mesh_geom_bbox`, the proxy's three proportions, which the encoder turns into eight corners.
#   "voxel" `mesh_geom_voxel`, the proxy's surface quantised to a 16-cubed occupancy grid.
#
CONTROL_MODES = ("point", "bbox", "voxel")
CONTROL_WORKFLOWS = {"point": "mesh_geom_ctrl", "bbox": "mesh_geom_bbox",
                     "voxel": "mesh_geom_voxel"}

# The modes whose control signal is a MESH on disk. Both read the same file, which is why a caller
# holding one cannot be inferred to have meant either in particular.
MESH_CONTROL_MODES = ("point", "voxel")

# The default is measured rather than assumed, and the control-ordering question's answer is no:
# eight corners do not replace 8,192 points. The bbox gate ran both on the same three block-outs the
# control gate used, off the same conditioning image, scored with no rotation search against each
# block-out's own self-agreement ceiling. Full numbers in docs/GENERATION.md; the short version:
#
#   footprint IoU, which is what "drops into a layout" reduces to: point 0.9200 against bbox 0.5766,
#     i.e. 98.8% to 101.0% of each ceiling against 50.1% to 70.8%. The bbox route saves 7 s an asset.
#   the control DOES reach the model, which matters because the last Omni control that scored badly
#     was being ignored outright (the control gate). Bob's proportions beat the node's own `auto_bbox` guess 3 of 3
#     on aspect error and 1 of 3 on ground plan, which is the whole finding: a box constrains extent
#     and says nothing about plan.
#   the gain tracks how distinctive the box is. 3x over the null on a tall thin tree ([0.42, 1.0,
#     0.44]) and a LOSS on a near-cubic rock ([1.0, 0.67, 0.95]), whose three numbers say "about this
#     big", which the image already said.
#
# "bbox" stays wired for a reason the bbox gate was not looking for. It uploads nothing, so it is
# the only Omni route that runs in a process with no ComfyUI folder: measured with `comfy_dir()`
# forced away, `mesh_geom_ctrl` fails at the node with "Mesh file not found" and `mesh_geom_bbox`
# completes. That makes it the block-out route's fallback wherever mesh transport is unavailable,
# which is worth more than the seconds.
DEFAULT_CONTROL_MODE = "point"


def control_route(mode=None, control=None, control_bbox=None):
    """Which control mode one generation runs, or None when it has no control at all.

    An explicit `mode` wins, then whichever signal the caller actually holds, then
    `DEFAULT_CONTROL_MODE` when it holds both. Callers pass what they have rather than deciding, the
    same rule `asset_chain` follows.

    Two modes share the mesh form. `mesh_geom_ctrl` and `mesh_geom_voxel` take the SAME control
    file, so a mesh alone cannot say which of them was meant and the inferred answer is
    `DEFAULT_CONTROL_MODE`; "voxel" is reachable by naming it, which is what a challenger mode
    should cost. An unknown name raises rather than falling through to an unconditioned generation,
    because on this route a control that does not reach the model never errors on its own (the pack
    install, the control gate, the bbox gate, and the voxel gate's own input rotation).
    """
    if mode:
        mode = str(mode)
        if mode not in CONTROL_MODES:
            raise ComfyError(f"unknown control mode {mode!r}; "
                             f"expected one of {', '.join(CONTROL_MODES)}")
        return mode
    if control and control_bbox:
        return DEFAULT_CONTROL_MODE
    if control_bbox:
        return "bbox"
    if not control:
        return None
    return DEFAULT_CONTROL_MODE if DEFAULT_CONTROL_MODE in MESH_CONTROL_MODES else "point"


def mesh_simplify_uv(mesh_path, out_path, *, faces=4000, url=None, workflow="mesh_simplify_uv",
                     timeout=900, on_progress=None, on_queued=None, preflight_graph=True):
    """`mesh_simplify_uv`: normalise into the unit cube, then `Trellis2Simplify` and
    `Trellis2UVUnwrap`, the ComfyUI side of the steps 3 and 4 A/B. No model is loaded, so the wall
    clock here is the algorithm and not a checkpoint read.

    The normalise is `mesh_process`'s, for `mesh_process`'s reason, and it was missing here until the
    block-out route needed it. `Trellis2Simplify` rescales nothing, so this graph's output arrives in
    whatever space its input did, and its only consumer is `mesh_texture`, whose encoder voxelises in
    the unit cube. That was a latent precondition while the one caller was `mesh_geom_trellis` at
    [-0.5, 0.5] and a defect the moment `mesh_geom_ctrl` became one: Omni returns [-1, 1], measured
    1.99361 out of the geometry stage and 1.99333 out of this one, and the finished barn shipped a
    2048-square basecolor at spread 3.46 and mean 0.06 -- black, faithfully baked, with 3,886 faces,
    0.0 UV overlap and a 0.8383 footprint IoU beside it.
    """
    graph, prov = load_workflow(workflow)
    values = {"BOB_MESH": {"mesh_path": upload_mesh(mesh_path, url=url)},
              "BOB_SIMPLIFY": {"target_face_count": int(faces)}}
    # No floor: this graph loads no model, so refusing it on free VRAM would block the cheap half of
    # a chain on the card state its expensive half created.
    data, gen = generate_mesh((graph, prov), values, route=None, url=url, timeout=timeout,
                              on_progress=on_progress, on_queued=on_queued,
                              preflight_graph=preflight_graph,
                              required_titles=("BOB_MESH", "BOB_OUT"))
    return _write_mesh(out_path, data, gen, workflow=workflow, prov=prov,
                       extra={"faces_requested": int(faces), "source_mesh": mesh_path})


def mesh_process(mesh_path, out_path, *, faces=4000, remesh=True, url=None,
                 workflow="mesh_process", timeout=900, on_progress=None, on_queued=None,
                 preflight_graph=True):
    """`mesh_process`: normalise into the unit cube, then `Trellis2ProcessMesh`, i.e. steps 3 and 4 in
    ONE node.

    `mesh_simplify_uv`'s replacement on any route that did not generate its own topology. It exists
    because a challenger model has to be scored through the same processing the shipped route
    applies to its own output: same face budget, same weld, same chart parameters, same `remesh`
    branch. The route A/B measured what using `mesh_simplify_uv` instead would have cost, and it is
    not small (1,467 to 3,050 boundary edges against 10 to 146 on the same prompts), so processing
    the two halves of a grid through different nodes would score the node rather than the model.

    The normalise is not tidiness. Hunyuan returns [-1, 1] where TRELLIS.2 returns [-0.5, 0.5] and
    `Trellis2ProcessMesh` rescales neither, so without it every `mesh_texture` texture on this route
    comes back BLACK (the geometry A/B: in-chart albedo std 0.0064 against 0.1810), and the two
    models' meshes would meet the same `remesh_band` at different sizes.

    No model is loaded, so this costs wall clock and no VRAM of its own.
    """
    graph, prov = load_workflow(workflow)
    values = {"BOB_MESH": {"mesh_path": upload_mesh(mesh_path, url=url)}}
    graph = bind_process(graph, values, remesh=remesh, faces=faces)
    # No floor, for `mesh_simplify_uv`'s reason: no model is loaded here.
    data, gen = generate_mesh((graph, prov), values, route=None, url=url, timeout=timeout,
                              on_progress=on_progress, on_queued=on_queued,
                              preflight_graph=preflight_graph,
                              required_titles=("BOB_MESH", "BOB_OUT"))
    return _write_mesh(out_path, data, gen, workflow=workflow, prov=prov,
                       extra={"faces_requested": int(faces), "remesh": bool(remesh),
                              "source_mesh": mesh_path})


def _write_mesh(out_path, data, gen, *, workflow, prov, extra):
    os.makedirs(os.path.dirname(os.path.abspath(out_path)) or ".", exist_ok=True)
    with open(out_path, "wb") as fh:
        fh.write(data)
    info = {"path": out_path, "bytes": len(data), "workflow": workflow,
            "derived_from": prov.get("derived_from"), "prompt_id": gen["prompt_id"],
            "server_file": gen.get("server_file"), "seconds": gen["seconds"]}
    info.update(extra)
    return info


def mesh_staging_dir(pack_dir):
    """`<pack>/_staging`, shared with the texture-set variants. One undecided-results folder, and
    it is a SIBLING of both `textures/` and `models/` for the same reason: the resolvers union
    every `textures/` and every `models/<name>/manifest.json` they find under a pack root, so a
    variant staged inside either would show up in a picker before anyone accepted it."""
    return staging_dir(pack_dir)


def list_mesh_variants(pack_dir):
    """Staged variant directories that hold a generated mesh, oldest first."""
    return [d for d in list_variants(pack_dir)
            if any(n.lower().endswith(MESH_EXTS) for n in sorted(os.listdir(d)))]


def _stage_dir(prompt_text, pack_dir, seed):
    """A fresh `<pack>/_staging/<slug>_s<seed>/` for one generation, and its name."""
    base = staging_dir(pack_dir)
    os.makedirs(base, exist_ok=True)
    name = unique_set_name(base, f"{slugify(prompt_text)}_s{int(seed)}")
    out_dir = os.path.join(base, name)
    os.makedirs(out_dir, exist_ok=True)
    return out_dir, name


def _stage_subject(prompt_text, out_dir, *, seed=0, size=1024, checkpoint=None, url=None,
                   subject=None, negative=None, on_progress=None, on_queued=None):
    """Pipeline step 1 for whichever chain is running: `mesh_subject`, or the image the artist already
    has.

    `subject` skips `mesh_subject` entirely and is reported as costing nothing, which is true and is
    what makes a benchmark able to hand every route the SAME reference image.

    `negative` reaches `mesh_subject`'s BOB_NEG and nothing else, because the subject image is the
    ONLY stage a text prompt touches: every geometry graph downstream conditions on the picture, so
    anything not said here cannot be said later. It is threaded through every chain rather than
    being `mesh_subject`'s private argument for that reason.
    """
    if subject:
        return {"path": subject, "artist_prompt": (prompt_text or "").strip(),
                "prompt": subject, "seed": int(seed), "seconds": 0.0}
    if on_progress:
        on_progress("reference image")
    return subject_image(prompt_text, os.path.join(out_dir, "subject.png"), seed=seed, size=size,
                         checkpoint=checkpoint, url=url, timeout=600, negative=negative,
                         on_progress=on_progress, on_queued=on_queued)


def stage_subject_only(prompt_text, pack_dir, *, seed=0, size=1024, checkpoint=None, url=None,
                       negative=None, on_progress=None, on_queued=None):
    """Pipeline step 1 ALONE: stage the reference image and stop, so it can be looked at first.

    Every geometry graph conditions on the picture and none of them reads the text, so the reference
    is where an asset is won or lost -- and until this existed there was no way to see one without
    paying for the geometry behind it. A gabled timber structure took three seeds: 11 came back a
    cropped close-up of a wall, 23 the whole structure standing on a display plinth with a toy car
    beside it, and 41 was the one. The subject stage cost about 8 s each; the geometry stage cost
    81, 435 and 113 s. Two of those three geometry jobs were spent on references an artist would
    have rejected on sight.

    Returns the same shape the chains' first step does plus `dir` and `name`, so the accepted image
    goes straight back in as `comfy_mesh(subject=...)` and the geometry runs against exactly the
    picture that was approved. Nothing is wasted either way: the staging directory it writes is the
    one a later `subject=` call will not need, and `reject_variant` clears it.
    """
    out_dir, name = _stage_dir(prompt_text, pack_dir, seed)
    info = _stage_subject(prompt_text, out_dir, seed=seed, size=size, checkpoint=checkpoint,
                          url=url, negative=negative, on_progress=on_progress,
                          on_queued=on_queued)
    meta = {"artist_prompt": (prompt_text or "").strip(), "prompt": info.get("prompt"),
            "seed": int(seed), "size": int(size), "route": "subject_only",
            "workflows": ["mesh_subject"], "subject": info["path"],
            "seconds": {"subject": info["seconds"]}}
    with open(os.path.join(out_dir, "meta.json"), "w") as fh:
        json.dump(meta, fh, indent=2, sort_keys=True)
    return {"dir": out_dir, "name": name, "meta": meta, "subject": info["path"],
            "prompt": info.get("prompt"), "seconds": info["seconds"]}


def generate_asset_source(prompt_text, pack_dir, *, seed=0, tier="default", size=1024,
                          checkpoint=None, url=None, timeout=1800, on_progress=None,
                          on_queued=None, subject=None, negative=None, remesh=True, control=None,
                          points=8192, control_bbox=None, control_mode=None):
    """`mesh_subject` then `mesh_geom_trellis` into a fresh `<pack>/_staging/<variant>/`, returning
    that variant's info.

    The ComfyUI half of Generate Asset, whole. `subject` is a local image path that SKIPS
    `mesh_subject`, for the artist who already has the reference they want; it still has to carry
    alpha.

    A control swaps step 2 from `mesh_geom_trellis` to the Omni route: same reference image, same
    output contract, geometry conditioned on a shape the layout was composed around. It is a value
    here rather than a separate staging function because everything downstream (`mesh_simplify_uv`,
    `mesh_texture`, and all of `finish_asset`) is identical -- which is also why the block-out route
    runs the STAGED chain and not the one-shot one: `mesh_geom_texture` generates its own geometry
    and takes no control.

    Two forms of it, and `control_route` decides which: `control` is a unit-normalised block-out
    proxy for `mesh_geom_ctrl`, `control_bbox` is that proxy's three proportions for
    `mesh_geom_bbox`. `core.gen_assets` produces either from the same object.
    """
    out_dir, name = _stage_dir(prompt_text, pack_dir, seed)
    steps = {}
    subject_info = _stage_subject(prompt_text, out_dir, seed=seed, size=size, checkpoint=checkpoint,
                                  url=url, subject=subject, negative=negative,
                                  on_progress=on_progress, on_queued=on_queued)
    steps["subject"] = subject_info["seconds"]

    mode = control_route(control_mode, control, control_bbox)
    if on_progress:
        on_progress("geometry from block-out" if mode else "geometry")
    raw = os.path.join(out_dir, name + "_raw.glb")
    common = dict(seed=seed, url=url, timeout=timeout, on_progress=on_progress,
                  on_queued=on_queued)
    # One dispatch per control mode and nothing else, so a mode that is a value everywhere else does
    # not become a branch here. The `else` is the uncontrolled route and only an absent mode reaches
    # it: `control_route` refuses an unknown name rather than letting it arrive as a silently
    # unconditioned generation, which is the failure this integration keeps finding.
    if mode == "bbox":
        mesh_info = mesh_geom_bbox(control_bbox, subject_info["path"], raw, **common)
    elif mode == "voxel":
        mesh_info = mesh_geom_voxel(control, subject_info["path"], raw, **common)
    elif mode == "point":
        mesh_info = mesh_geom_ctrl(control, subject_info["path"], raw, points=points, **common)
    else:
        mesh_info = mesh_geometry(subject_info["path"], raw, tier=tier, remesh=remesh, **common)
    steps["geometry"] = mesh_info["seconds"]

    meta = {"artist_prompt": (prompt_text or "").strip(), "prompt": subject_info.get("prompt"),
            "seed": int(seed), "tier": tier, "remesh": bool(remesh),
            "subject": subject_info["path"], "control": control,
            "control_bbox": control_bbox, "control_mode": mode,
            "raw_mesh": mesh_info["path"],
            "workflows": ["mesh_subject",
                          CONTROL_WORKFLOWS.get(mode, "mesh_geom_trellis")],
            "model": "Hunyuan3D-Omni" if mode else "TRELLIS.2-4B",
            "license": "Tencent Hunyuan3D community" if mode else "MIT", "seconds": steps}
    with open(os.path.join(out_dir, "meta.json"), "w") as fh:
        json.dump(meta, fh, indent=2, sort_keys=True)
    return {"dir": out_dir, "name": name, "meta": meta, "raw_mesh": mesh_info["path"],
            "subject": subject_info["path"], "seconds": steps}


def generate_asset_chain(prompt_text, pack_dir, *, seed=0, tier="default", faces=4000,
                         texture_size=1024, checkpoint=None, url=None, timeout=1800,
                         on_progress=None, on_queued=None, subject=None, negative=None,
                         remesh=True, control=None, points=8192, control_bbox=None,
                         control_mode=None):
    """Every ComfyUI stage of one asset, in order, on ONE thread: `mesh_subject`, `mesh_geom_trellis`,
    `mesh_simplify_uv`, `mesh_texture`.

    This is the shape the panel uses, and the ordering is why it works. Steps 3 and 4 are done by
    `mesh_simplify_uv` on the server, so its output IS the low mesh `mesh_texture` textures, and
    Blender has nothing to do between them. That makes the whole ComfyUI half a single worker job
    with no main-thread work in the middle, which is the only arrangement that keeps a five-minute
    run off the UI thread.

    Returns the staged paths. `core.gen_assets.finish_asset` takes them and does steps 6 to 8.
    """
    staged = generate_asset_source(prompt_text, pack_dir, seed=seed, tier=tier, remesh=remesh,
                                   checkpoint=checkpoint, url=url, timeout=timeout,
                                   on_progress=on_progress, on_queued=on_queued, subject=subject,
                                   negative=negative, control=control, points=points,
                                   control_bbox=control_bbox, control_mode=control_mode)
    out_dir, name = staged["dir"], staged["name"]

    if on_progress:
        on_progress("simplify and unwrap")
    simp = mesh_simplify_uv(staged["raw_mesh"], os.path.join(out_dir, name + "_simp.glb"),
                            faces=faces, url=url, on_progress=on_progress, on_queued=on_queued)
    staged["simplified_mesh"] = simp["path"]
    staged["seconds"]["simplify"] = simp["seconds"]

    if on_progress:
        on_progress("PBR texture")
    tex = mesh_texture(simp["path"], staged["subject"],
                       os.path.join(out_dir, name + "_tex.glb"), seed=seed,
                       texture_size=texture_size, url=url, timeout=timeout,
                       on_progress=on_progress, on_queued=on_queued)
    staged["textured_mesh"] = tex["path"]
    staged["seconds"]["texture"] = tex["seconds"]

    meta = staged["meta"]
    meta["workflows"] = [meta["workflows"][0], meta["workflows"][1],
                         "mesh_simplify_uv", "mesh_texture"]
    meta.update(simplified_mesh=simp["path"], textured_mesh=tex["path"],
                seconds=staged["seconds"])
    with open(os.path.join(out_dir, "meta.json"), "w") as fh:
        json.dump(meta, fh, indent=2, sort_keys=True)
    return staged


def generate_asset_alt(prompt_text, pack_dir, *, seed=0, tier="default", faces=4000,
                       texture_size=1024, checkpoint=None, url=None, timeout=1800,
                       on_progress=None, on_queued=None, subject=None, negative=None, remesh=True,
                       size=1024):
    """Every ComfyUI stage of one asset through the CHALLENGER geometry model: `mesh_subject`,
    `mesh_geom_alt`, `mesh_process`, `mesh_texture`.

    `generate_asset_chain`'s shape with Hunyuan 2.1 in place of `mesh_geom_trellis` and the shared
    `Trellis2ProcessMesh` (`mesh_process`) in place of `mesh_simplify_uv`, so it stages the same
    three files and every consumer stays route-agnostic. `tier` is accepted and unused: Hunyuan has
    no resolution tier, which is one of the things the caller does not have to know.

    `remesh=False` reaches `mesh_process` and nothing else. The geometry is watertight before it
    gets there, because `VoxelToMesh` extracts an isosurface, so this route cannot make an open
    surface at all and asking it to is not an error. That is the structural half of the geometry A/B
    verdict.
    """
    out_dir, name = _stage_dir(prompt_text, pack_dir, seed)
    steps = {}
    subject_info = _stage_subject(prompt_text, out_dir, seed=seed, size=size, checkpoint=checkpoint,
                                  url=url, subject=subject, negative=negative,
                                  on_progress=on_progress, on_queued=on_queued)
    steps["subject"] = subject_info["seconds"]

    if on_progress:
        on_progress("geometry")
    raw = mesh_geom_alt(subject_info["path"], os.path.join(out_dir, name + "_raw.glb"), seed=seed,
                        size=size, url=url, timeout=timeout, on_progress=on_progress,
                        on_queued=on_queued)
    steps["geometry"] = raw["seconds"]

    if on_progress:
        on_progress("simplify and unwrap")
    simp = mesh_process(raw["path"], os.path.join(out_dir, name + "_simp.glb"), faces=faces,
                        remesh=remesh, url=url, on_progress=on_progress, on_queued=on_queued)
    steps["simplify"] = simp["seconds"]

    if on_progress:
        on_progress("PBR texture")
    tex = mesh_texture(simp["path"], subject_info["path"],
                       os.path.join(out_dir, name + "_tex.glb"), seed=seed,
                       texture_size=texture_size, url=url, timeout=timeout,
                       on_progress=on_progress, on_queued=on_queued)
    steps["texture"] = tex["seconds"]

    meta = {"artist_prompt": (prompt_text or "").strip(), "prompt": subject_info.get("prompt"),
            "seed": int(seed), "tier": tier, "remesh": bool(remesh), "route": "alt",
            "subject": subject_info["path"], "raw_mesh": raw["path"],
            "simplified_mesh": simp["path"], "textured_mesh": tex["path"],
            "faces_requested": int(faces),
            "workflows": ["mesh_subject", "mesh_geom_alt", "mesh_process", "mesh_texture"],
            "model": "hunyuan3d-2.1", "license": "Tencent Hunyuan3D community", "seconds": steps}
    with open(os.path.join(out_dir, "meta.json"), "w") as fh:
        json.dump(meta, fh, indent=2, sort_keys=True)
    return {"dir": out_dir, "name": name, "meta": meta, "raw_mesh": raw["path"],
            "simplified_mesh": simp["path"], "textured_mesh": tex["path"],
            "subject": subject_info["path"], "seconds": steps}


# Which ComfyUI chain Generate Asset runs. A route, not a rewrite: every function below stages the
# same paths and `core.gen_assets.finish_asset` consumes any of them.
#
#   "oneshot" `mesh_subject` -> `mesh_geom_texture`. Two jobs, one model load, no mesh round trip, and no dense mesh.
#   "staged"  `mesh_subject` -> `mesh_geom_trellis` -> `mesh_simplify_uv` -> `mesh_texture`. Four jobs, and the one that keeps a DENSE mesh on disk.
#   "alt"     `mesh_subject` -> `mesh_geom_alt` -> `mesh_process` -> `mesh_texture`. The same shape with Hunyuan 2.1 as the geometry model.
#
# The route A/B measured both on ten prompts and the one-shot route won, which is why it is the
# default. Short version, full numbers in docs/GENERATION.md: a wash on wall clock (593 s against
# 584 s for all ten), both 10/10 inside the face budget with the same UV quality, a lower VRAM peak,
# and two things that decided it. It returns a far cleaner mesh (10 to 662 boundary edges against
# 1,467 to 3,050 on the same prompts, with foliage openness and thinness preserved to within 1%),
# and it cannot hit the black-albedo trap, because it never re-encodes a mesh: the staged route's
# `mesh_texture` returned one fully black texture in ten. The dense mesh the one-shot route gives up
# bought no measurable detail in the baked normal at a 4,000-face budget, which is what the plan
# assumed it was for.
#
# "staged" stays wired and is not dead code: it is the only route that leaves a dense mesh on disk
# for a future higher-budget or hero path, and `mesh_texture` on its own is still the mesh-texturing
# family, texturing a mesh Bob already has. `mesh_geom_texture` can only texture geometry it
# generated itself.
#
# "alt" is the geometry A/B grid's challenger and it stays wired for the same kind of reason: it is
# the only route whose geometry model needs no custom pack at all, so it is what still generates an
# asset on an install with ComfyUI-TRELLIS2 missing or broken. The geometry A/B measured it against
# the default on ten prompts and the verdict is per asset class, not global; `KIND_ROUTE` below is
# where that lands.
ASSET_ROUTES = ("oneshot", "staged", "alt")
DEFAULT_ASSET_ROUTE = "oneshot"

# The geometry A/B verdict, and the ONE place a per-asset-class geometry decision lives. A kind not
# named here takes `DEFAULT_ASSET_ROUTE`; naming one routes that class to a different chain with no
# branch anywhere else and no widget.
#
# It is EMPTY, and that is a decision with numbers behind it rather than an unfinished table. The
# geometry A/B ran ten prompts through both models off one shared subject each, with
# `Trellis2ProcessMesh` and its `remesh` setting identical on both sides. Full numbers in
# docs/GENERATION.md; the short version:
#
#   foliage (5 prompts, remesh off): TRELLIS.2, decisively. Median 15.0 s against 31.3 s, peak
#     4,964 MiB against 9,620, and 2.9x the boundary edges (median 984 against 344) because the
#     challenger CANNOT return an open surface -- `VoxelToMesh` extracts an isosurface, so the holes
#     it does have come from the simplifier rather than from the model. One of its five textures came
#     back black.
#   solids (5 prompts, remesh on): the challenger wins two columns and loses three. It is 2.1x
#     faster (median 40.4 s against 86.1 s) and returns properly closed shells (median 0 boundary
#     edges against 116, and nothing downstream closes those on either route). It costs 3.7 GB more
#     VRAM (9,688 MiB against 5,958), returns a flatter albedo (median std 0.1259 against 0.1555),
#     hit the black-albedo trap once in ten where `mesh_geom_texture` cannot hit it at all, and carries a
#     non-permissive licence with a territorial exclusion where TRELLIS.2 is MIT. Two wins on speed
#     and cleanliness do not buy a DEFAULT that an artist in the EU, the UK or South Korea may not
#     use, so solids stay on the default route and "alt" stays an explicit, documented choice.
#   block-out: Hunyuan, but through Omni and `mesh_geom_ctrl` rather than through this route, decided by the control gate on
#     footprint IoU (0.9079 mean against the multi-view baseline's 0.6748). `asset_chain` routes a
#     control to the staged chain, which is where `mesh_geom_ctrl` lives.
KIND_ROUTE = {}

# Which scatter kinds are FOLIAGE, which decides two stages at once and was a literal in three
# places before the geometry A/B. On the ComfyUI side it turns off `Trellis2ProcessMesh`'s
# dual-contouring remesh, which otherwise returns a watertight shell and makes a leaf a leaf-shaped
# bag; on the Blender side it leaves the pinhole fill alone, which would weld the blade shut for the
# same reason.
FOLIAGE_KINDS = ("plants", "grass")


def is_foliage(kind):
    """Whether a scatter kind wants open surfaces kept. See `FOLIAGE_KINDS`."""
    return str(kind or "") in FOLIAGE_KINDS



def asset_chain(route=None, kind=None, control=None, control_bbox=None):
    """The staging function for one asset. The one place the route becomes a decision.

    Three inputs, in priority order, and every caller passes what it knows rather than deciding:

    - a control of EITHER form forces the staged chain, because `mesh_geom_texture` generates its own geometry from
      the image and takes no control, and the challenger's Hunyuan graph takes none either. There is
      no one-shot version of the block-out route to choose. Which Omni mode runs is a separate
      decision and it lives in `control_route`, not here.
    - `route` is an explicit override, from the MCP tool or a benchmark.
    - `kind` picks up the geometry A/B per-class verdict in `KIND_ROUTE`.
    """
    if control or control_bbox:
        return generate_asset_chain
    name = route or KIND_ROUTE.get(str(kind or "")) or DEFAULT_ASSET_ROUTE
    return {"oneshot": generate_asset_oneshot, "staged": generate_asset_chain,
            "alt": generate_asset_alt}.get(name, generate_asset_oneshot)


def finish_passes(staged):
    """(simplify_pass, texture_pass) for `core.gen_assets.finish_asset`, from either route's staging.

    The routes differ by exactly this, and putting it here keeps every caller route-agnostic. The
    staged route hands over three files, so steps 3 and 4 come from `mesh_simplify_uv` and step 5
    from `mesh_texture`. The one-shot route hands over ONE file that is already simplified,
    unwrapped and textured, so it goes in as the simplified mesh with no texture pass at all:
    passing it as `texture_pass` instead would make Blender decimate and unwrap a mesh it is about
    to throw away.
    """
    if staged.get("simplified_mesh"):
        return staged["simplified_mesh"], staged.get("textured_mesh")
    return staged.get("textured_mesh"), None


def geometry_is_final(staged):
    """Whether the file that becomes the LOW mesh is the same file the DENSE mesh came from.

    True on the one-shot route and false on every other, and it is the one thing `finish_passes`
    cannot say: both routes hand `finish_asset` a simplified mesh, so a caller reading only that
    tuple cannot tell "the server retopologised a mesh Bob sent" from "the server generated the only
    mesh there is". Two decisions downstream need to know, and both went wrong on the first batch of
    generated assets for want of being told:

    - the REPAIR. `prepare_low` skips its weld, pinhole fill, decimate and unwrap whenever the
      server simplified, which is right when the server also cleaned up and wrong here: measured on
      five generated solids, the shipped meshes carried 48 to 229 boundary edges, and the stump's
      holes were visible in the render. Nothing was discarded, as the first reading of this had it
      -- `close_pinholes` never ran at all, on either production route.
    - the BAKE. High and low being one file makes `bake_high_to_low`'s selected-to-active transfer a
      cage projection of a mesh onto a copy of itself, which resamples a clean texture into a
      jittered one: measured on the structure's roof, correlation 0.817 and a mean absolute
      difference of 10.4 of 255, and the artist reported it as a chevron hash over the shingles.
      There is nothing to transfer, because the colour is already in the low mesh's own UVs.

    Asked as a fact about the staging rather than inferred later from `has_textures`, which is the
    inference that let the second one happen. Paths are compared normalised, so a chain that hands
    over the same file by two spellings still reads as one file.
    """
    if staged.get("simplified_mesh"):
        return False
    raw, textured = staged.get("raw_mesh"), staged.get("textured_mesh")
    if not raw or not textured:
        return False
    return os.path.normpath(str(raw)) == os.path.normpath(str(textured))


def stage_exports(staged):
    """How many `Trellis2ExportTrimesh` glb writes `finish_asset` has to undo on each staged file.

    Every one of those writes turns the subject -90 degrees about X and the turns accumulate along a
    chain, so the staged route hands over three files in three different frames. Measured hop by hop
    by the control gate; the maths and the two bugs it fixes are on `gen_assets.undo_exports`.

    Relative on the image routes and absolute on the block-out route, which is the only asymmetry
    here: with no control the incoming orientation means nothing, so the raw mesh's frame is left
    alone and the later files are merely brought into line with it. With a control it means
    everything, so the raw mesh's own turn is undone as well and the finished asset lands facing the
    way the block-out did.

    EITHER control form, and that is not a formality. `mesh_geom_bbox` uploads no mesh, so a rule
    written as "is there a control file" reads the bbox route as uncontrolled and leaves its asset
    lying on its side: the turn comes from the exporter, which both Omni routes end at, not from the
    control.

    The "alt" chain needs no case of its own, and that is arithmetic rather than luck: Hunyuan's
    `SaveGLB` adds no turn where `mesh_geom_trellis`'s `Trellis2ExportTrimesh` adds one, and every
    later hop on both chains is a Trellis export, so the two differ by a constant that a relative
    correction cancels.
    """
    meta = staged.get("meta") or {}
    base = 1 if (meta.get("control") or meta.get("control_bbox")) else 0
    if not staged.get("simplified_mesh"):
        # The one-shot route: `mesh_geom_texture` returns ONE file that is both the raw and the low
        # mesh, and it takes no control, so there is nothing to bring into line and nothing to face.
        return {"raw": base, "simplified": base}
    return {"raw": base, "simplified": base + 1, "textured": base + 2}


# -- Stylised renders and painted meshes (the look-dev stylise family, and the mesh-texturing family
# stylised) ------------------------- One graph shape serves both, which is why `stylize_render` is
# built first and `mesh_paint_views` grows out of it: a per-view restyle IS a stylised render that
# happens to be one of six. The difference is the IPAdapter reference `mesh_paint_views` carries so
# the views agree on a palette, and the lower denoise that keeps the real render dominant (the
# projection-route finding).
#
# Two hint routes, and the whole point of the look-dev stylise family is that the first one exists:
#   "passes"    `stylize_render`, Blender's TRUE depth and normal passes, exported by core.gen_views
#   "estimated" `stylize_render_est`, Depth Anything V2 plus NormalBAE reading the render itself
# The estimated route is not a fallback nobody wants: it is the control the real-passes claim is
# measured against, and the only route available for an image Bob did not render.
STYLISE_WORKFLOWS = {"passes": "stylize_render", "estimated": "stylize_render_est"}
DEFAULT_STYLISE_ROUTE = "passes"

# Shipped sampler defaults. Denoise is the knob that trades style for silhouette, so it is the one
# the panel exposes; the ControlNet strengths are values here rather than widgets.
STYLISE_DENOISE = 0.55
PAINT_DENOISE = 0.40

# A style prompt has the same problem `tex_tileable`'s did: the artist types the look and forgets
# the framing clause that keeps the result usable as a restyle rather than a new picture.
STYLISE_SUFFIX = "same composition, same camera, same layout, coherent lighting"


def _paint_or_stylise_route(workflow):
    """Which VRAM floor one `stylize_render` call runs under: `mesh_paint_views` carries an
    IPAdapter and its vision encoder on top of the two ControlNets, so it is the paint floor and not
    the stylise one. One graph shape serving two families is exactly why this is read off the
    workflow rather than passed in by a caller that would sometimes forget."""
    return "paint" if workflow == "mesh_paint_views" else "stylize"


def _round8(value):
    """SDXL latents are eighths of a pixel, so a render's odd resolution has to land on a multiple
    of 8 before it becomes a latent."""
    return max(8, int(round(float(value) / 8.0)) * 8)


def _stylise_size(size):
    width, height = (size, size) if isinstance(size, (int, float)) else size[:2]
    return _round8(width), _round8(height)


def stylise_prompt(prompt_text):
    """The artist's style prompt with the composition-preserving clause appended."""
    return ", ".join(p for p in ((prompt_text or "").strip(), STYLISE_SUFFIX) if p)


def stylize_render(image_path, out_path, prompt_text, *, depth=None, normal=None, seed=0,
                   denoise=STYLISE_DENOISE, size=1024, negative=None, checkpoint=None,
                   lora=None, lora_strength=0.8, depth_strength=None, normal_strength=None,
                   reference=None, url=None, workflow=None, timeout=900, on_progress=None,
                   on_queued=None, preflight_graph=True, check_vram=True):
    """`stylize_render` (or `mesh_paint_views`, or `stylize_render_est`): one image restyled under
    depth and normal ControlNet, written to `out_path`. Returns info.

    Pass `depth` and `normal` (Bob's real passes) for the `stylize_render` route; omit both and the
    estimated route runs instead, which is the same graph with two preprocessors in place of the two
    loads. `reference` selects `mesh_paint_views`, whose IPAdapter locks the palette across a
    turntable.

    `lora` is a filename from the server's own LoRA enum. None DROPS the LoraLoader from the graph
    rather than running it at strength 0, because a placeholder filename fails the validator on a
    machine with no LoRAs installed (`drop_node`).

    `check_vram` is where this route's floor is read, and it is HERE rather than in
    `generate_image` because of the `ensure_untiled` call below: the reset is itself an SDXL job, so
    by the time the chokepoint looked, the shared checkpoint was resident and the card legitimately
    read 3,599 MiB free against a 4,000 floor -- a refusal caused by the priming step rather than by
    a full card. Measured on this route. `paint_views` turns it off for every view after the first,
    the same batching argument `preflight_graph` makes one line down.
    """
    if workflow is None:
        workflow = ("mesh_paint_views" if reference
                    else STYLISE_WORKFLOWS["passes" if (depth and normal) else "estimated"])
    graph, prov = load_workflow(workflow)
    by_title = titles(graph)
    if "BOB_DEPTH" in by_title and not (depth and normal):
        raise ComfyError(f"{workflow} needs Blender's depth and normal passes "
                         f"(core.gen_views.render_passes writes both); pass none of them to run "
                         f"the estimated route instead")
    width, height = _stylise_size(size)
    full = stylise_prompt(prompt_text)
    values = {"BOB_PROMPT": {"text": full},
              "BOB_SIZE": {"width": width, "height": height},
              "BOB_SEED": {"seed": int(seed), "denoise": float(denoise)},
              "BOB_IMAGE": {"image": upload_image(image_path, url=url, subfolder="bob")}}
    if negative:
        values["BOB_NEG"] = {"text": negative}
    ckpt = checkpoint or prov.get("default_checkpoint")
    if ckpt:
        values["BOB_CKPT"] = {"ckpt_name": ckpt}
    if depth and "BOB_DEPTH" in by_title:
        values["BOB_DEPTH"] = {"image": upload_image(depth, url=url, subfolder="bob")}
    if normal and "BOB_NORMAL" in by_title:
        values["BOB_NORMAL"] = {"image": upload_image(normal, url=url, subfolder="bob")}
    if reference and "BOB_REF" in by_title:
        values["BOB_REF"] = {"image": upload_image(reference, url=url, subfolder="bob")}
    if depth_strength is not None:
        values["BOB_DEPTH_APPLY"] = {"strength": float(depth_strength)}
    if normal_strength is not None:
        values["BOB_NORMAL_APPLY"] = {"strength": float(normal_strength)}
    if lora:
        values["BOB_LORA"] = {"lora_name": lora, "strength_model": float(lora_strength),
                              "strength_clip": float(lora_strength)}
    else:
        graph = drop_node(graph, "BOB_LORA", {0: "model", 1: "clip"})

    # A stylised frame holds a real composition, so wrapping it round the border is nonsense. Same
    # shared model as `tex_tileable`, so the same reset. Cheap here: it is one 64 px sample in front
    # of a multi-second restyle, and the paint route only pays it on its first view.
    if check_vram:
        preflight_vram(_paint_or_stylise_route(workflow), url=url)
    ensure_untiled(url, on_progress=on_progress)
    # route=None here: the floor was read above, before the reset loaded the checkpoint. Reading it
    # again now would measure the card WITH this route's own model on it.
    png, gen = generate_image((graph, prov), values, route=None, url=url,
                              timeout=timeout, on_progress=on_progress,
                              on_queued=on_queued,
                              preflight_graph=preflight_graph,
                              required_titles=("BOB_PROMPT", "BOB_SEED", "BOB_OUT"))
    os.makedirs(os.path.dirname(os.path.abspath(out_path)) or ".", exist_ok=True)
    with open(out_path, "wb") as fh:
        fh.write(png)
    return {"path": out_path, "workflow": workflow, "prompt": full,
            "artist_prompt": (prompt_text or "").strip(), "seed": int(seed),
            "denoise": float(denoise), "size": [width, height], "checkpoint": ckpt,
            "lora": lora, "lora_strength": float(lora_strength) if lora else 0.0,
            "hints": "passes" if (depth and normal) else "estimated",
            "reference": reference, "prompt_id": gen["prompt_id"], "seconds": gen["seconds"],
            "derived_from": prov.get("derived_from")}


def paint_views(views, out_dir, prompt_text, *, seed=0, denoise=PAINT_DENOISE, size=1024,
                negative=None, checkpoint=None, lora=None, lora_strength=0.8, url=None,
                workflow="mesh_paint_views", timeout=900, on_progress=None, on_queued=None):
    """`mesh_paint_views`'s ComfyUI half: restyle every turntable view in ONE worker job. Returns info.

    `views` is what `core.gen_views.turntable_views` produced, so each entry already carries its
    beauty, depth and normal paths. The FRONT view goes first and is its own reference; every later
    view takes the stylised front as the IPAdapter reference, so the palette is decided once instead
    of drifting per view. That ordering is the cheap half of the projection-route finding's
    consistency mitigation, and `core.gen_paint` measures what it bought.

    The stylised images land beside the renders as `<stem>_styled.png`, in view order, which is the
    order `gen_paint.paint_maps` expects.
    """
    os.makedirs(out_dir, exist_ok=True)
    out, reference, seconds = [], None, {}
    for i, view in enumerate(views):
        if on_progress:
            on_progress(f"stylise view {i + 1}/{len(views)}")
        stem = os.path.splitext(os.path.basename(view["beauty"]))[0]
        target = os.path.join(out_dir, stem + "_styled.png")
        info = stylize_render(view["beauty"], target, prompt_text,
                             depth=view.get("depth"), normal=view.get("normal"),
                             reference=reference or view["beauty"], seed=int(seed), size=size,
                             denoise=denoise, negative=negative, checkpoint=checkpoint,
                             lora=lora, lora_strength=lora_strength, url=url, workflow=workflow,
                             timeout=timeout, on_progress=on_progress, on_queued=on_queued,
                             preflight_graph=(i == 0), check_vram=(i == 0))
        if reference is None:
            reference = target  # the stylised FRONT, from here on
        seconds[f"view_{i:02d}"] = info["seconds"]
        out.append(info)
    return {"images": [info["path"] for info in out], "views": len(out), "reference": reference,
            "infos": out, "seconds": seconds,
            "total_seconds": float(sum(seconds.values()))}


# Which route paints a mesh Bob already has. Both texture a mesh in ITS OWN UVs, so the choice is a
# NAME in one place; what the name reaches is a whole entry point rather than a function this module
# can hand back, and that asymmetry is the honest shape of the two routes:
#
#   "pbr"      `mesh_texture`, Trellis2TextureMesh: plausible native PBR, no style control, one job.
#              One mesh in, one textured mesh out, so `comfy_paint_mesh` is the whole route.
#   "stylised" `mesh_paint_views`: `paint_views` here, with Blender on both sides of it. Bob renders
#              the turntable and projects the restyle back, so the route is `core.gen_paint`'s
#              `paint_stylised` and reaches an agent as the `paint_stylised` OP. LoRA and prompt
#              style control, N jobs, and a colour map with the rest of the set derived.
#
# There was a `texture_chain()` here that returned one of the two functions. It went because the two
# have different signatures -- one mesh against N views -- so nothing could call what it returned
# without knowing which it had got, and in the event nothing called it at all.
#
# "pbr" stays the default because a plausible material is what a scatter prop needs; "stylised" is
# for the case the retopology tier rule named, where the target look is stylised rather than
# photographic, and it is the only route that has real style control at all.
TEXTURE_ROUTES = ("pbr", "stylised")
DEFAULT_TEXTURE_ROUTE = "pbr"


# -- Terrain macro mask (the macro-heightmap family)
# ------------------------------------------------------------- `heightmap_macro`'s prompt suffix,
# and the same argument as PROMPT_SUFFIX and SUBJECT_SUFFIX: the failure mode is silent and costs a
# whole generation, so the clause that prevents it is not the artist's job to remember. Here the
# failure is a picture OF a mountain instead of a plan view of one.
MACRO_SUFFIX = ("top-down orthographic aerial elevation map, greyscale, white is the highest ground "
                "and black is the lowest, one large-scale landform, smooth broad gradients, "
                "no texture detail, no contour lines")

# Whether `heightmap_macro`'s circular padding stays in the graph. A route is a value in one place,
# beside `asset_chain()` and `texture_chain()`.
#
# "open" is the default and it is a measured decision rather than an inherited one. Circular padding
# is what makes the texture family tile, and it is exactly wrong here: a tiling macro mask has to
# put the same elevation on both borders, so the massif it invents repeats and the basin drains off
# one edge into a copy of itself. A terrain tile is one place, not a torus. "tiled" is kept for the
# case that genuinely wants it, an endless-terrain sheet where neighbouring tiles must join.
MACRO_ROUTES = ("open", "tiled")
DEFAULT_MACRO_ROUTE = "open"


def macro_tiling(route=None):
    """True when `heightmap_macro` keeps its two circular-padding nodes. The one place THAT route is
    decided."""
    route = route or DEFAULT_MACRO_ROUTE
    if route not in MACRO_ROUTES:
        raise ComfyError(f"unknown macro route {route!r} (have: {', '.join(MACRO_ROUTES)})")
    return route == "tiled"


def macro_prompt(prompt_text):
    return ", ".join(p for p in ((prompt_text or "").strip(), MACRO_SUFFIX) if p)


def heightmap_macro(prompt_text, out_path, *, seed=0, size=1024, route=None, negative=None,
                    checkpoint=None, url=None, workflow="heightmap_macro", timeout=600,
                    on_progress=None, on_queued=None, invert=False, keep_source=False):
    """Generate one terrain macro mask and write it to `out_path`. Returns info.

    The whole Bob half of the macro-heightmap family is these twenty lines plus one op, because the
    mask is derived by the SAME normalise-then-write path the texture family's height channel takes
    (`comfy_maps`): a generated image, one cutoff of its luminance, one 8-bit PNG. What differs is
    which side of the cutoff is kept, and that is one constant
    (`comfy_maps.MACRO_LOWPASS_FRACTION`).

    8-bit on purpose (the bit-depth floor). The claim is not that 8 bits carries a heightfield, it
    is that 8 bits carries a MASK that is about to be resampled, blurred and then eroded, and the
    macro-mask gate measures that rather than asserting it. A sidecar records the prompt, the route
    and the cutoff so a terrain baked from a mask can say where its silhouette came from (the
    provenance rule).

    `keep_source` also writes the raw generation beside the mask. Off by default because an artist
    has no use for it; the macro-mask gate turns it on, because the 8-bit claim can only be AUDITED
    against the image the mask was derived from. The derivation averages thousands of pixels across
    three channels, so the float mask carries far more precision than any single 8-bit sample does,
    and that is the whole reason the quantisation question has a measurable answer.
    """
    graph, prov = load_workflow(workflow)
    full = macro_prompt(prompt_text)
    values = {"BOB_PROMPT": {"text": full},
              "BOB_SEED": {"seed": int(seed)},
              "BOB_SIZE": {"width": int(size), "height": int(size)}}
    if negative:
        values["BOB_NEG"] = {"text": negative}
    ckpt = checkpoint or prov.get("default_checkpoint")
    if ckpt:
        values["BOB_CKPT"] = {"ckpt_name": ckpt}
    # Before the `ensure_untiled` in the open branch below, for `stylize_render`'s measured reason:
    # the reset is an SDXL job, and after it the card reads as full of this route's own model.
    preflight_vram("heightmap", url=url)
    tiled = macro_tiling(route)
    if tiled:
        values.update(tiling_values(enable=True))
        mark_tiling_applied(url)
    else:
        # The same argument as dropping BOB_LORA (see `drop_node`): a tiling node switched to
        # "disable" would still be a node whose pack has to be installed, and the honest default is
        # a graph that does not contain it.
        graph = drop_node(graph, "BOB_TILE", {0: "model"})
        graph = drop_node(graph, "BOB_TILE_VAE", {0: "vae"})
        # Dropping the nodes means this route runs on the SHARED model, so a texture set earlier in
        # the session would otherwise make the mask tile -- and a tiling macro mask puts the same
        # elevation on both borders, which is the wallpaper repeat the macro-mask gate measured and
        # rejected.
        ensure_untiled(url, on_progress=on_progress)

    png, gen = generate_image((graph, prov), values, route=None, url=url, timeout=timeout,
                              on_progress=on_progress, on_queued=on_queued,
                              required_titles=("BOB_PROMPT", "BOB_SEED", "BOB_OUT"))

    t1 = time.time()
    rgb = comfy_maps.read_png(png)
    mask = comfy_maps.macro_from(rgb, wrap=tiled)
    if invert:
        mask = 255 - mask
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    comfy_maps.write_png(out_path, mask)
    source_path = None
    if keep_source:
        source_path = os.path.splitext(out_path)[0] + "_source.png"
        with open(source_path, "wb") as fh:
            fh.write(png)
    meta = {"artist_prompt": (prompt_text or "").strip(), "prompt": full, "seed": int(seed),
            "size": int(size), "route": route or DEFAULT_MACRO_ROUTE, "tiled": tiled,
            "invert": bool(invert), "workflow": workflow,
            "derived_from": prov.get("derived_from"), "checkpoint": ckpt,
            "lowpass_fraction": comfy_maps.MACRO_LOWPASS_FRACTION,
            "note": "a low-frequency macro MASK for the terrain op stack, not a heightfield (the bit-depth floor)",
            "source": source_path,
            "seconds": {"generate": gen["seconds"], "derive": time.time() - t1}}
    with open(os.path.splitext(out_path)[0] + ".json", "w") as fh:
        json.dump(meta, fh, indent=2, sort_keys=True)
    return {"path": out_path, "meta": meta, "prompt": full, "tiled": tiled,
            "source": source_path,
            "seconds": gen["seconds"] + (time.time() - t1), "prompt_id": gen["prompt_id"]}


def generate_asset_oneshot(prompt_text, pack_dir, *, seed=0, tier="default", faces=4000,
                           texture_size=1024, checkpoint=None, url=None, timeout=1800,
                           on_progress=None, on_queued=None, subject=None, negative=None,
                           remesh=True, size=1024):
    """Every ComfyUI stage of one asset through the ONE-SHOT route: `mesh_subject` then
    `mesh_geom_texture`.

    `generate_asset_chain`'s twin, staging the same keys so `core.gen_assets.finish_asset` takes
    either. The one difference a caller has to know about is that `raw_mesh` and `textured_mesh` are
    the SAME file: `mesh_geom_texture` returns budget topology already textured, so there is no
    dense mesh and no intermediate simplify. Pass it as `simplify_pass` with no `texture_pass` and
    Blender skips both its own decimate and its own unwrap, which is the point.
    """
    out_dir, name = _stage_dir(prompt_text, pack_dir, seed)
    steps = {}
    subject_info = _stage_subject(prompt_text, out_dir, seed=seed, size=size, checkpoint=checkpoint,
                                  url=url, subject=subject, negative=negative,
                                  on_progress=on_progress, on_queued=on_queued)
    steps["subject"] = subject_info["seconds"]

    if on_progress:
        on_progress("geometry and PBR")
    mesh_info = mesh_geom_texture(subject_info["path"], os.path.join(out_dir, name + "_tex.glb"),
                                  seed=seed, tier=tier, faces=faces, texture_size=texture_size,
                                  remesh=remesh, url=url, timeout=timeout,
                                  on_progress=on_progress, on_queued=on_queued)
    steps["geometry_texture"] = mesh_info["seconds"]

    meta = {"artist_prompt": (prompt_text or "").strip(), "prompt": subject_info.get("prompt"),
            "seed": int(seed), "tier": tier, "remesh": bool(remesh), "route": "oneshot",
            "subject": subject_info["path"], "raw_mesh": mesh_info["path"],
            "textured_mesh": mesh_info["path"], "faces_requested": int(faces),
            "workflows": ["mesh_subject", "mesh_geom_texture"],
            "model": "TRELLIS.2-4B", "license": "MIT", "seconds": steps}
    with open(os.path.join(out_dir, "meta.json"), "w") as fh:
        json.dump(meta, fh, indent=2, sort_keys=True)
    return {"dir": out_dir, "name": name, "meta": meta, "raw_mesh": mesh_info["path"],
            "textured_mesh": mesh_info["path"], "subject": subject_info["path"],
            "seconds": steps}
