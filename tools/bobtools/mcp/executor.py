"""The headless executor: run a validated BuildRequest through Blender.

This is the swappable boundary. Today it spawns `blender --background` and runs
the bpy-side runner. A live-socket executor can later implement the same
`run_build(request) -> BuildResult` signature with zero changes upstream.
"""

import json
import logging
import subprocess
import tempfile
from pathlib import Path

from .. import config
from .contracts import BuildRequest, BuildResult

# Logs go to stderr. stdout is the MCP stdio protocol channel.
log = logging.getLogger("bob.executor")


def run_build(request: BuildRequest, *, timeout: float = 300.0) -> BuildResult:
    root = config.repo_root()
    blender = config.blender_binary()
    runner = config.blender_runner("headless_build.py")

    output_abs = (root / request.output_file).resolve()
    base_abs = str((root / request.base_file).resolve()) if request.base_file else ""

    # Payload the runner reads: contract fields + resolved absolute paths.
    payload = request.model_dump()
    payload["output_file_abs"] = str(output_abs)
    payload["base_file_abs"] = base_abs

    with tempfile.TemporaryDirectory() as tmp:
        ops_path = Path(tmp) / "request.json"
        result_path = Path(tmp) / "result.json"
        ops_path.write_text(json.dumps(payload))

        log.info("headless build: %s -> %s (%d ops)", blender, output_abs, len(request.ops))
        proc = subprocess.run(
            [
                blender,
                "--background",
                "--factory-startup",
                "--python-exit-code", "1",
                "--python", str(runner),
                "--", str(ops_path), str(result_path),
            ],
            capture_output=True,
            text=True,
            timeout=timeout,
        )

        if result_path.exists():
            return BuildResult.model_validate_json(result_path.read_text())

    # Runner never wrote a result, so surface Blender's stderr tail.
    tail = "\n".join((proc.stderr or proc.stdout or "").splitlines()[-15:])
    log.error("headless build produced no result (exit %s)", proc.returncode)
    return BuildResult(
        ok=False,
        output_file=request.output_file,
        error=f"runner produced no result (exit {proc.returncode}):\n{tail}",
    )
