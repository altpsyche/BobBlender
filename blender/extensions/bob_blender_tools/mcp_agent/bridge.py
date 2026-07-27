"""Live executor: apply ops to the open Blender session via the socket bridge.

Mirrors executor.run_build's shape but targets a running session instead of spawning
Blender. This is the swappable executor described in docs/ARCHITECTURE.md. Requires the
BobBlenderTools extension to be enabled and its MCP bridge running (Advanced -> Start).

Every request carries an IDEMPOTENCY KEY (`batch`), because a slow batch used to come back as
`main-thread timeout` while its work completed: the assets were on disk and in the scene, the client
was told it had failed, and the safe-looking retry duplicated objects (`import_generated` creates a
new object each time; most other ops are idempotent by name). With a key, a timeout is not an answer
at all -- the client reconnects and COLLECTS the same batch until the bridge says it is done, and the
bridge never runs a key twice. So the only failures this can report are real ones.
"""

import json
import socket
import uuid

from . import paths
from .contracts import BuildResult, OpResult

# How long one socket exchange waits. The bridge's own main-thread wait is a little shorter, so a
# slow batch comes back as "still running" (a collectable answer) rather than as a dead socket.
_EXCHANGE_TIMEOUT = 150.0

# How long to keep collecting a batch that is still running, and how long to wait between attempts.
# 20 minutes is past anything the op vocabulary can take on one batch (the slowest measured step is a
# hero import_generated's bake); past it the batch id is reported so a caller can keep polling.
_COLLECT_DEADLINE = 1200.0
_COLLECT_INTERVAL = 2.0


def _exchange(payload, host, port, timeout):
    """One request/response over the bridge socket. Returns the decoded reply, or raises OSError."""
    with socket.create_connection((host, port), timeout=timeout) as sock:
        sock.sendall((json.dumps(payload) + "\n").encode())
        buf = b""
        while not buf.endswith(b"\n"):
            chunk = sock.recv(65536)
            if not chunk:
                break
            buf += chunk
    return json.loads(buf.decode() or "{}")


def _result(raw, batch):
    return BuildResult(
        ok=raw.get("ok", False),
        output_file="(live)",
        results=[OpResult(**r) for r in raw.get("results", [])],
        error=raw.get("error"),
        batch=batch,
        status=raw.get("status"),
    )


def run_build_live(
    ops: list[dict],
    *,
    host: str | None = None,
    port: int | None = None,
    timeout: float = _EXCHANGE_TIMEOUT,
    batch: str | None = None,
    deadline: float = _COLLECT_DEADLINE,
) -> BuildResult:
    """Apply ops to the running Blender session and return the BuildResult.

    `batch` is the idempotency key. Left unset a fresh one is generated, which is the normal case;
    pass a key returned by an earlier call to COLLECT that batch instead of sending new work (the
    bridge replies with its result if it has finished, or its progress if it has not). `deadline`
    bounds the total collect time before this gives up and hands the key back to the caller.
    """
    import time

    host = host or paths.bridge_host()
    port = port or paths.bridge_port()
    collecting = batch is not None
    key = batch or uuid.uuid4().hex
    payload = {"batch": key, "poll": True} if collecting else {"batch": key, "ops": ops}
    started = time.monotonic()

    while True:
        try:
            raw = _exchange(payload, host, port, timeout)
        except socket.timeout:
            # The socket gave up before the bridge answered. The batch is still running, so switch to
            # collecting rather than reporting a failure that is not one.
            raw = {"ok": False, "status": "running", "error": "socket timeout"}
        except (ConnectionRefusedError, OSError) as exc:
            return BuildResult(
                ok=False,
                output_file="(live)",
                error=f"no live bridge on {host}:{port} ({exc}). Enable the "
                "BobBlenderTools extension in Blender and start its MCP bridge.",
                batch=key if collecting else None,
            )
        if raw.get("status") not in ("running", "timeout"):
            return _result(raw, key)
        if time.monotonic() - started > deadline:
            return BuildResult(
                ok=False,
                output_file="(live)",
                status="running",
                batch=key,
                error=f"batch {key} is still running after {deadline:.0f}s "
                      f"({raw.get('done_ops', '?')}/{raw.get('total_ops', '?')} ops applied). It has "
                      f"NOT failed and the ops must not be re-sent; collect it with "
                      f"build_live(batch=\"{key}\").",
            )
        # From here on this is a collect, whatever it started as.
        payload = {"batch": key, "poll": True}
        time.sleep(_COLLECT_INTERVAL)
