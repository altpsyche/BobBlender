"""Live bridge server with a managed lifecycle (start, stop, reload).

Runs the socket that lets MCP author into this Blender session. Ops execute on
the main thread via a timer, which is the only safe way to mutate bpy from a
socket, and only whitelisted core ops run (no arbitrary code).
"""

import collections
import json
import os
import queue
import socket
import sys
import threading

import bpy

_state = {"sock": None, "thread": None, "running": False, "port": None}
_jobs: "queue.Queue" = queue.Queue()

# Bound an untrusted local connection: cap the payload and time out a stalled read,
# so a client that never sends the newline terminator (or floods bytes) cannot wedge a
# handler thread forever or exhaust memory.
_MAX_PAYLOAD = 8 * 1024 * 1024
_READ_TIMEOUT = 30.0

# How long a handler waits for the main thread before it answers "still running". Ops execute on a
# timer, so a batch is only as fast as the slowest op in it, and the slow ones are real work: a
# 14,000-face import_generated ran past the old 60 s and the client was told "main-thread timeout"
# for a batch that then landed every asset in the scene. There is no shape of timeout that is
# distinguishable from a failure by itself, so the answer is not a longer number -- it is the batch
# registry below.
_JOB_WAIT = 120.0

# Batch registry: idempotency key -> {"state", "result", "ops", "done"}. A client sends a `batch` id
# with its ops; re-sending the SAME id never re-runs the work. It either collects the finished result
# or is told the batch is still running. This is what makes a retry safe, and without it the only
# safe response to a timeout was to do nothing (re-sending duplicated objects, since every op in the
# vocabulary that creates something is idempotent by NAME and import_generated is not).
_BATCH_LIMIT = 32
_batches: "collections.OrderedDict[str, dict]" = collections.OrderedDict()
_batch_lock = threading.Lock()


def _batch_get(key):
    with _batch_lock:
        entry = _batches.get(key)
        if entry is not None:
            _batches.move_to_end(key)
        return entry


def _batch_put(key, entry):
    with _batch_lock:
        _batches[key] = entry
        _batches.move_to_end(key)
        while len(_batches) > _BATCH_LIMIT:
            _batches.popitem(last=False)


def _batch_reply(key, entry):
    """The wire reply for a known batch: its result when finished, its progress when not."""
    if entry["state"] == "done":
        reply = dict(entry["result"])
        reply["batch"] = key
        reply["status"] = "done"
        reply["replayed"] = entry.get("collected", 0) > 0
        entry["collected"] = entry.get("collected", 0) + 1
        return reply
    return {"ok": False, "batch": key, "status": entry["state"], "results": [],
            "done_ops": entry.get("done_ops", 0), "total_ops": entry.get("total_ops", 0),
            "error": f"batch {key} is still running on the main thread "
                     f"({entry.get('done_ops', 0)}/{entry.get('total_ops', 0)} ops applied). "
                     f"Poll it with the same batch id; do NOT re-send the ops, they would run twice."}


def _validate_ops(ops):
    """Structural check at the socket trust boundary. Contract (Pydantic) validation runs
    in the agent-side MCP server (mcp_agent), but the socket is a second entry point: reject anything that is
    not a list of dicts each carrying a string "op" before it reaches a builder. apply_op
    still enforces the op allowlist."""
    if not isinstance(ops, list):
        raise ValueError("ops must be a list")
    for op in ops:
        if not isinstance(op, dict) or not isinstance(op.get("op"), str):
            raise ValueError("each op must be an object with a string 'op' field")
    return ops


# Path and config
def _repo_blender_dir() -> str:
    """Return <repo>/blender, resolving the symlink so dev-installs find the repo."""
    here = os.path.dirname(os.path.realpath(__file__))  # blender/extensions/bob_blender_tools/bridge
    return os.path.dirname(os.path.dirname(os.path.dirname(here)))  # blender


def _configured_port() -> int:
    env = os.environ.get("BOB_BRIDGE_PORT")
    if env:
        return int(env)
    try:
        import tomllib

        repo = os.path.dirname(_repo_blender_dir())
        with open(os.path.join(repo, "bob.toml"), "rb") as fh:
            return int(tomllib.load(fh).get("bridge", {}).get("port", 9876))
    except Exception:
        return 9876


# Main-thread executor
def _process_jobs():
    from ..core.dispatch import apply_op

    while not _jobs.empty():
        ops, entry, done = _jobs.get_nowait()
        results = []
        try:
            for op in ops:
                results.append(apply_op(op))
                # Per-op ack: count what has actually been applied, so a client that times out is
                # told how far the batch got rather than being left to guess.
                entry["done_ops"] = len(results)
            if bpy.context.view_layer:
                bpy.context.view_layer.update()
            entry["result"] = {"ok": True, "results": results, "error": None}
        except Exception as exc:
            # The results so far are part of the answer: a batch that failed on op 5 of 8 applied
            # four ops, and a caller retrying the whole batch needs to know that.
            entry["result"] = {
                "ok": False,
                "results": results,
                "error": f"{type(exc).__name__}: {exc} (failed on op "
                         f"{len(results) + 1}/{len(ops)}: {ops[len(results)].get('op')!r})",
            }
        finally:
            entry["state"] = "done"
            done.set()
    return 0.1  # reschedule while registered


# Socket plumbing
def _handle(conn: socket.socket):
    try:
        conn.settimeout(_READ_TIMEOUT)  # accept() does NOT inherit the listener timeout
        data = b""
        while not data.endswith(b"\n"):
            chunk = conn.recv(65536)
            if not chunk:
                break
            data += chunk
            if len(data) > _MAX_PAYLOAD:
                raise ValueError("payload exceeds size limit")
        req = json.loads(data.decode() or "{}")
        key = req.get("batch")
        if key is not None and not isinstance(key, str):
            raise ValueError("batch must be a string id")

        # A known batch id is a COLLECT, never a re-run. This is the whole idempotency contract: the
        # client may re-send after a timeout, and it gets the result or the progress, not a second
        # execution.
        known = _batch_get(key) if key else None
        if known is not None:
            if known["state"] != "done":
                known["done"].wait(timeout=_JOB_WAIT)
            reply = _batch_reply(key, known)
        elif req.get("poll"):
            reply = {"ok": False, "status": "unknown", "results": [], "batch": key,
                     "error": f"no batch {key!r} on this bridge (it may have been evicted after "
                              f"{_BATCH_LIMIT} newer batches, or the session restarted)"}
        else:
            ops = _validate_ops(req.get("ops", []))
            done = threading.Event()
            entry = {"state": "running", "result": None, "done": done,
                     "done_ops": 0, "total_ops": len(ops), "collected": 0}
            if key:
                _batch_put(key, entry)
            _jobs.put((ops, entry, done))
            done.wait(timeout=_JOB_WAIT)
            if entry["state"] == "done":
                reply = dict(entry["result"])
                reply["status"] = "done"
                if key:
                    reply["batch"] = key
                    entry["collected"] = 1
            elif key:
                reply = _batch_reply(key, entry)
            else:
                reply = {"ok": False, "status": "timeout", "results": [],
                         "done_ops": entry["done_ops"], "total_ops": entry["total_ops"],
                         "error": f"main-thread timeout after {_JOB_WAIT:.0f}s "
                                  f"({entry['done_ops']}/{entry['total_ops']} ops applied). The "
                                  "batch is STILL RUNNING; send a `batch` id with the request to be "
                                  "able to collect its result instead of re-running it."}
    except Exception as exc:
        reply = {"ok": False, "error": repr(exc)}
    try:
        conn.sendall((json.dumps(reply) + "\n").encode())
    finally:
        conn.close()


def _serve():
    sock = _state["sock"]
    while _state["running"]:
        try:
            conn, _ = sock.accept()
        except socket.timeout:
            continue
        except OSError:
            break  # socket closed by stop()
        threading.Thread(target=_handle, args=(conn,), daemon=True).start()


# Public lifecycle
def start(port: int | None = None) -> str:
    if _state["running"]:
        return f"already running on 127.0.0.1:{_state['port']}"

    port = port or _configured_port()

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.settimeout(1.0)  # so _serve can notice stop() promptly
    sock.bind(("127.0.0.1", port))
    sock.listen(5)

    _state.update(sock=sock, running=True, port=port)
    thread = threading.Thread(target=_serve, daemon=True)
    thread.start()
    _state["thread"] = thread

    if not bpy.app.timers.is_registered(_process_jobs):
        bpy.app.timers.register(_process_jobs, persistent=True)

    print(f"[bob_blender_tools] listening on 127.0.0.1:{port}")
    return f"listening on 127.0.0.1:{port}"


def stop() -> str:
    if not _state["running"]:
        return "not running"
    _state["running"] = False
    sock = _state["sock"]
    if sock is not None:
        try:
            sock.close()
        except OSError:
            pass
    _state["sock"] = None
    _state["port"] = None
    if bpy.app.timers.is_registered(_process_jobs):
        bpy.app.timers.unregister(_process_jobs)
    print("[bob_blender_tools] stopped")
    return "stopped"


def reload_builders() -> str:
    """Purge the core builder package from the import cache so new op code is picked up live."""
    # __package__ is now ...bob_blender_tools.bridge; the addon root is one segment up, so
    # strip the trailing ".bridge" to target ...bob_blender_tools.core (not bridge.core).
    addon_root = __package__.rsplit(".", 1)[0]
    core_root = addon_root + ".core"
    purged = [m for m in list(sys.modules) if m == core_root or m.startswith(core_root + ".")]
    for name in purged:
        del sys.modules[name]
    return f"reloaded builders ({len(purged)} modules refreshed)"


def is_running() -> bool:
    return bool(_state["running"])


def status() -> str:
    return f"running on :{_state['port']}" if _state["running"] else "stopped"
