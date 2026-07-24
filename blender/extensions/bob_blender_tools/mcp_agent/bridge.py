"""Live executor: apply ops to the open Blender session via the socket bridge.

Mirrors executor.run_build's shape but targets a running session instead of spawning
Blender. This is the swappable executor described in docs/ARCHITECTURE.md. Requires the
BobBlenderTools extension to be enabled and its MCP bridge running (Advanced -> Start).
"""

import json
import socket

from . import paths
from .contracts import BuildResult, OpResult


def run_build_live(
    ops: list[dict],
    *,
    host: str | None = None,
    port: int | None = None,
    timeout: float = 60.0,
) -> BuildResult:
    host = host or paths.bridge_host()
    port = port or paths.bridge_port()
    payload = (json.dumps({"ops": ops}) + "\n").encode()
    try:
        with socket.create_connection((host, port), timeout=timeout) as sock:
            sock.sendall(payload)
            buf = b""
            while not buf.endswith(b"\n"):
                chunk = sock.recv(65536)
                if not chunk:
                    break
                buf += chunk
    except (ConnectionRefusedError, OSError) as exc:
        return BuildResult(
            ok=False,
            output_file="(live)",
            error=f"no live bridge on {host}:{port} ({exc}). Enable the "
            "BobBlenderTools extension in Blender and start its MCP bridge.",
        )

    raw = json.loads(buf.decode() or "{}")
    return BuildResult(
        ok=raw.get("ok", False),
        output_file="(live)",
        results=[OpResult(**r) for r in raw.get("results", [])],
        error=raw.get("error"),
    )
