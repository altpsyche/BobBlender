"""One worker thread, one timer tick, and a job registry that does not outlive a file load.

A texture set is tens of seconds and a mesh plus paint is minutes (docs/GENERATION.md, Bob-side
constraint 2), so the generation call cannot sit on the UI thread. The shape here is the one that
survives Blender's threading model rather than the one that looks concurrent:

    submit()   main thread, returns immediately with a Job worker     ONE background thread, drains
    a work queue, does the HTTP and the numpy, and
               NEVER touches bpy -- it only puts events on a result queue
    tick()     main thread, drains the result queue and runs the callbacks, which is the only
               place bpy is touched
    clear()    load_post: in-flight jobs are cancelled server-side and their callbacks dropped

One worker, not a pool, because 16 GB of VRAM makes the server sequential anyway (the VRAM-floor
rule): a second concurrent job would queue behind the first inside ComfyUI and buy nothing but two
ways to be half-finished when the file changes.

The module imports `bpy` lazily, so the scheduler itself is testable in the venv: a test calls
`tick()` by hand where Blender calls it from `bpy.app.timers`.
"""

import queue
import threading
import time
import traceback

try:
    from . import comfy
except ImportError:  # `core` itself on sys.path (the venv / headless route)
    import comfy

# How often the main thread drains results. 4 Hz is well under the 1 Hz job polling the worker
# does, so a finished job is visible within a frame or two, and the tick costs nothing when the
# queue is empty.
TICK_SECONDS = 0.25

_work = queue.Queue()
_results = queue.Queue()
_worker = None
_worker_lock = threading.Lock()

# job id -> Job, for the panel and for cancel. Only the main thread mutates this.
_registry = {}
_next_id = 0

# Longest single tick seen since the last reset, in seconds. The measurement behind "the UI stays
# responsive": the tick IS the main-thread work a job costs, so its worst case is the worst block.
_max_tick = 0.0


class Job:
    """One unit of generation. `fn` runs on the worker and must not touch bpy; `on_done` and
    `on_progress` run on the main thread from `tick()` and may."""

    __slots__ = ("id", "label", "fn", "on_done", "on_progress", "state", "progress", "result",
                 "error", "prompt_id", "queued_at", "started_at", "ended_at", "cancelled")

    def __init__(self, jid, label, fn, on_done=None, on_progress=None):
        self.id = jid
        self.label = label
        self.fn = fn
        self.on_done = on_done
        self.on_progress = on_progress
        self.state = "queued"
        self.progress = ""
        self.result = None
        self.error = None
        self.prompt_id = None
        self.queued_at = time.time()
        self.started_at = None
        self.ended_at = None
        self.cancelled = False

    @property
    def running(self):
        return self.state in ("queued", "running")

    @property
    def seconds(self):
        return (self.ended_at or time.time()) - self.queued_at

    def report(self, text):
        """Called from the worker to push a progress line. Never touches bpy; the string is
        handed to the main thread and shown from there."""
        _results.put((self.id, "progress", str(text)))

    def note_prompt_id(self, prompt_id):
        """Record the server-side id so cancel has something to cancel."""
        self.prompt_id = prompt_id


def _run_worker():
    while True:
        job = _work.get()
        if job is None:
            return
        if job.cancelled:
            _results.put((job.id, "cancelled", None))
            continue
        _results.put((job.id, "started", None))
        try:
            result = job.fn(job)
        except Exception as exc:  # any failure is a message for the panel, never a dead thread
            _results.put((job.id, "failed", exc))
            if not isinstance(exc, comfy.ComfyError):
                traceback.print_exc()
        else:
            _results.put((job.id, "done", result))


def _ensure_worker():
    global _worker
    with _worker_lock:
        if _worker is None or not _worker.is_alive():
            _worker = threading.Thread(target=_run_worker, name="bbt-comfy", daemon=True)
            _worker.start()
    return _worker


def submit(label, fn, on_done=None, on_progress=None):
    """Queue `fn(job)` on the worker thread and return the Job. Returns immediately."""
    global _next_id
    _next_id += 1
    job = Job(_next_id, label, fn, on_done=on_done, on_progress=on_progress)
    _registry[job.id] = job
    _ensure_worker()
    _work.put(job)
    _start_timer()
    return job


def jobs():
    """Every known job, newest last."""
    return [_registry[k] for k in sorted(_registry)]


def active():
    """Jobs still queued or running."""
    return [j for j in jobs() if j.running]


def cancel(job_id):
    """Cancel one job: server-side by prompt id when it has one, and locally either way, so a job
    that has not reached the worker yet never starts."""
    job = _registry.get(job_id)
    if job is None or not job.running:
        return False
    job.cancelled = True
    if job.prompt_id:
        try:
            comfy.cancel(job.prompt_id)
        except comfy.ComfyError:
            pass  # idempotent server-side, and a dead server is not a reason to keep the job
    job.state = "cancelled"
    job.ended_at = time.time()
    return True


def clear(cancel_running=True):
    """Drop the registry, cancelling anything in flight (the threading rule).

    Called from `load_post`: a job that finished against the previous file must not run its
    callback against the new one, so the callbacks go with the registry and any result that
    arrives afterwards is discarded by `tick()` as an unknown id.
    """
    for job in list(_registry.values()):
        if cancel_running and job.running:
            cancel(job.id)
    _registry.clear()
    while True:
        try:
            _results.get_nowait()
        except queue.Empty:
            break


def max_tick_seconds(reset=False):
    """The longest main-thread tick since the last reset. What "the UI stays responsive" is
    measured with, rather than asserted."""
    global _max_tick
    value = _max_tick
    if reset:
        _max_tick = 0.0
    return value


def tick():
    """Drain the result queue on the MAIN thread. Returns True while work remains, which is also
    what the Blender timer reads to decide whether to keep running."""
    global _max_tick
    started = time.time()
    while True:
        try:
            job_id, kind, payload = _results.get_nowait()
        except queue.Empty:
            break
        job = _registry.get(job_id)
        if job is None or job.cancelled:
            continue  # a result from before a file load, or from a cancelled job: drop it
        if kind == "progress":
            job.progress = payload
            if job.on_progress:
                _safe(job.on_progress, job)
        elif kind == "started":
            job.state = "running"
            job.started_at = time.time()
        elif kind in ("done", "failed", "cancelled"):
            job.state = {"done": "done", "failed": "failed"}.get(kind, "cancelled")
            job.ended_at = time.time()
            if kind == "done":
                job.result = payload
            elif kind == "failed":
                job.error = payload
                job.progress = str(payload)[:120]
            if job.on_done:
                _safe(job.on_done, job)
    _max_tick = max(_max_tick, time.time() - started)
    return bool(active())


def _safe(fn, job):
    """A callback that raises must not kill the timer, or every later job goes unreported."""
    try:
        fn(job)
    except Exception:
        traceback.print_exc()


# -- Blender wiring --------------------------------------------------------------------------
# Kept behind a lazy import so the scheduler runs (and is tested) outside Blender.
_timer_running = False


def _timer():
    global _timer_running
    if not tick():
        _timer_running = False
        return None  # unregister: no work left, so no idle polling
    return TICK_SECONDS


def _start_timer():
    global _timer_running
    if _timer_running:
        return
    try:
        import bpy
    except ImportError:
        return  # no Blender: the caller drives tick() itself
    _timer_running = True
    bpy.app.timers.register(_timer, first_interval=0.0)


_on_load = None


def register():
    """Install the load_post handler. Called by the addon's register().

    The handler has to be `@persistent`: a plain load_post handler is itself removed by the file
    load, so the one load it would survive to see is the only one it must not miss (the threading
    rule).
    """
    global _on_load
    import bpy
    from bpy.app.handlers import persistent

    if _on_load is None:
        @persistent
        def _handler(*_args):
            clear()

        _on_load = _handler
    if _on_load not in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.append(_on_load)


def unregister():
    import bpy

    if _on_load is not None and _on_load in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.remove(_on_load)
    clear()
