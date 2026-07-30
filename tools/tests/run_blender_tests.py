"""The in-Blender entry point CI's `blender-headless` job invokes.

    blender --background --factory-startup --python tools/tests/run_blender_tests.py

Everything in `tools/tests` runs in the plain venv against the pure-python core, which is what keeps
the fast gate honest without Blender -- and it means no GEOMETRY assertion ran in CI at all. Every
geometry number in the docs came from someone's local Blender. This closes that: the gates that need
only `bpy` already exist and already assert real geometry, so the missing piece was a runner, not a
test suite.

**What runs here, and the one rule for adding to it.** A gate qualifies when it needs `bpy` and
NOTHING else -- no ComfyUI, no GPU, no network, no LFS asset. That is why `headless_foliage.py` is
absent despite being the largest gate in the repo: it takes 150 s and reaches for ComfyUI when one
happens to be running, so on a CI box it would be both slow and measuring something different from
what it measures locally. A gate that is sometimes-skipped is worse than one that is honestly out of
scope, because a green tick that skipped its own subject teaches nobody anything.

**One Blender per gate, and that is a correctness requirement rather than tidiness.** These gates
call `bob_blender_tools.register()` at import, which registers addon classes and preferences; a
second registration in the same process collides, and `read_factory_settings` between them drops the
preferences entry the first one made, so a gate run second fails for reasons that have nothing to do
with what it measures. A fresh process is also exactly how each gate is run and validated by hand,
so CI and a developer see the same thing. It costs about a second per gate.

Runs either inside Blender (CI's invocation, via `bpy.app.binary_path`) or as plain Python with
`$BLENDER` or a `blender` on PATH. Exit 0 = every gate passed.
"""

from __future__ import annotations

import os
import pathlib
import shutil
import subprocess
import sys

REPO = pathlib.Path(__file__).resolve().parents[2]
SCRIPTS = REPO / "tools" / "scripts"

# bpy-only gates, in ascending cost. Add one here the day it stops needing anything but Blender.
GATES = (
    ("sun", "headless_sun.py"),
    ("texture sets", "headless_texset.py"),
    ("scene seams", "headless_scene_seams.py"),
)


def blender_binary() -> str:
    """The Blender to spawn the gates with, most specific first.

    `bpy.app.binary_path` when this is itself running inside Blender, which is CI's invocation and
    guarantees the gates run under the SAME build rather than whatever else is on PATH.
    """
    try:
        import bpy

        if bpy.app.binary_path:
            return bpy.app.binary_path
    except ImportError:
        pass
    found = os.environ.get("BLENDER") or shutil.which("blender")
    if not found:
        raise SystemExit("no Blender found: run this under `blender --background --python`, "
                         "set $BLENDER, or put `blender` on PATH")
    return found


# What proves a gate RAN rather than merely started: the closing line `_gate.Gate.summary` prints,
# in either of its two forms.
#
# Exit code alone does not prove it, and that is a Blender fact rather than a gate one: **an uncaught
# exception in a `--python` script exits 0.** Measured -- a script whose only statement is `raise
# RuntimeError` exits 0, while `sys.exit(3)` exits 3. So a gate that died at import, before reaching
# its own `sys.exit(main())`, reported PASS: all three gates once came back green here while every one
# of them had failed on a NameError, and the tracebacks scrolling past were the only sign.
#
# `--python-exit-code 1` looks like the fix and is the wrong one: it also fires on an exception in a
# post-run TIMER, so a gate that passed every check failed the run because the addon's bridge autostart
# could not bind a port another Blender already held. That makes a gate's verdict depend on what else
# is running on the machine. Asserting the gate reached its own summary asserts exactly the thing that
# was missing, and nothing else.
_SUMMARY_MARKERS = ("check(s) passed", "failure(s) of")


def _reached_summary(output: str) -> bool:
    return any(m in output for m in _SUMMARY_MARKERS)


def main() -> int:
    binary = blender_binary()
    os.makedirs(REPO / "_generated", exist_ok=True)

    failed = []
    for label, filename in GATES:
        print(f"\n{'=' * 78}\n== {label} ({filename})\n{'=' * 78}", flush=True)
        proc = subprocess.run([binary, "--background", "--factory-startup",
                               "--python", str(SCRIPTS / filename)],
                              cwd=str(REPO), capture_output=True, text=True)
        out = (proc.stdout or "") + (proc.stderr or "")
        print(out, end="", flush=True)
        if proc.returncode:
            failed.append(f"{label} (exit {proc.returncode})")
        elif not _reached_summary(out):
            failed.append(f"{label} (died before its summary)")

    print(f"\n{'=' * 78}")
    if failed:
        print(f"FAILED: {', '.join(failed)} -- {len(failed)} of {len(GATES)} gates")
        return 1
    print(f"all {len(GATES)} in-Blender gates passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
