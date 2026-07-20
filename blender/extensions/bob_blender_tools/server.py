"""Live bridge server with a managed lifecycle (start, stop, reload).

Runs the socket that lets MCP author into this Blender session. Ops execute on
the main thread via a timer, which is the only safe way to mutate bpy from a
socket, and only whitelisted bbmcp ops run (no arbitrary code).
"""

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


def _validate_ops(ops):
    """Structural check at the socket trust boundary. Contract (Pydantic) validation runs
    in the venv MCP server, but the socket is a second entry point: reject anything that is
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
    """Return <repo>/blender, resolving the symlink so dev-installs find bbmcp."""
    here = os.path.dirname(os.path.realpath(__file__))  # blender/extensions/bob_blender_tools
    return os.path.dirname(os.path.dirname(here))       # blender


def _ensure_path() -> None:
    blender_dir = _repo_blender_dir()
    if blender_dir not in sys.path:
        sys.path.insert(0, blender_dir)


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
    from bbmcp.dispatch import apply_op

    while not _jobs.empty():
        ops, holder, done = _jobs.get_nowait()
        try:
            results = [apply_op(op) for op in ops]
            if bpy.context.view_layer:
                bpy.context.view_layer.update()
            holder["result"] = {"ok": True, "results": results, "error": None}
        except Exception as exc:
            holder["result"] = {
                "ok": False,
                "results": [],
                "error": f"{type(exc).__name__}: {exc}",
            }
        finally:
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
        ops = _validate_ops(req.get("ops", []))
        holder, done = {}, threading.Event()
        _jobs.put((ops, holder, done))
        done.wait(timeout=60)
        reply = holder.get("result", {"ok": False, "error": "main-thread timeout"})
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

    _ensure_path()
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
    """Purge bbmcp from the import cache so new op code is picked up live."""
    _ensure_path()
    purged = [m for m in list(sys.modules) if m == "bbmcp" or m.startswith("bbmcp.")]
    for name in purged:
        del sys.modules[name]
    return f"reloaded builders ({len(purged)} modules refreshed)"


def is_running() -> bool:
    return bool(_state["running"])


def status() -> str:
    return f"running on :{_state['port']}" if _state["running"] else "stopped"
