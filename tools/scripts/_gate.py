"""The harness every headless gate script shares: a check, a note, and one exit code.

Eleven gate scripts carried a byte-identical `check()` and `note()` plus their own `FAILURES` list.
Identical is the problem rather than the duplication: the day one of them starts printing a
different verdict format, the reader has to notice, and nothing would fail if the count of failures
stopped reaching the exit code in exactly one file. One implementation makes "did this gate pass" a
property of the harness instead of eleven independent promises.

Imported by `sys.path` injection rather than as a package, because a gate runs inside Blender's
bundled interpreter (`blender --background --python tools/scripts/headless_x.py`) where the tools
venv is unreachable. Every gate already puts `tools/scripts` on `sys.path` to find the extension, so
this costs no new plumbing:

    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from _gate import Gate

    gate = Gate("asset gate")
    gate.check("the mesh ships closed", edges == 0, f"{edges} boundary edges")
    gate.note("faces", faces)
    sys.exit(gate.exit_code())

The leading underscore keeps it out of the gate namespace: `headless_*.py` are entry points and this
is not one, so a reader scanning the directory can tell at a glance which files can be run.
"""

from __future__ import annotations


class Gate:
    """One gate run: its checks, its notes and its verdict.

    An instance rather than module globals, so two gates driven from one process (which
    `headless_comfy_all.py` does) cannot pool their failures into each other's exit code.
    """

    def __init__(self, name=""):
        self.name = name
        self.failures = []
        self.passes = 0

    def check(self, label, ok, detail=""):
        """Record one assertion and print its verdict. Returns `ok`, so a caller can branch on it.

        Returning the value matters: a gate that cannot continue past a failed precondition reads as
        `if not gate.check(...): return`, which keeps the early exit and the assertion on one line
        instead of asking the reader to keep a flag in their head.
        """
        print(f"[{'PASS' if ok else 'FAIL'}] {label}" + (f" -- {detail}" if detail else ""))
        if ok:
            self.passes += 1
        else:
            self.failures.append(label)
        return ok

    def note(self, label, value):
        """Print a measurement that is NOT gated.

        Deliberately a different marker from a check, because the two answer different questions and
        a reader scanning a gate's output has to be able to tell "this was asserted" from "this was
        measured and reported". The same split `core.gen_receipt` draws between a gated key and an
        informational one.
        """
        print(f"[----] {label} -- {value}")

    def skip(self, label, why):
        """Print a check that was not run, and why. Never a pass: a gate that silently drops a case
        on a machine without ComfyUI reads as green when it is merely quiet."""
        print(f"[SKIP] {label} -- {why}")

    def summary(self):
        """The closing line: what passed, what failed, and which ones."""
        head = f"{self.name}: " if self.name else ""
        if not self.failures:
            print(f"{head}{self.passes} check(s) passed")
            return
        print(f"{head}{len(self.failures)} failure(s) of {self.passes + len(self.failures)}: "
              + ", ".join(self.failures))

    def exit_code(self):
        """0 when every check passed, 1 otherwise. Prints the summary first, so a caller that
        forgets to call `summary()` still gets one."""
        self.summary()
        return 1 if self.failures else 0


# -- The rest of what every gate needs, and why it is here rather than in ten files ---------------
#
# `check` / `note` / `skip` came here first because they decide a VERDICT, and one implementation
# makes "did this gate pass" a property of the harness. These four decide nothing, and they are here
# for a different reason: they were byte-identical copies. Nine copies of `section`, seven of
# `empty_scene`, five of `Vram` (with its two nvidia-smi helpers), four stamp readers. Identical is
# the problem rather than the duplication -- the day one copy drifts, nothing fails and no reader is
# told. That already happened: `Vram.report()` gained a `rise` key in ONE of its five copies, so two
# gates whose whole point is comparable VRAM figures were reporting different dicts.
#
# What is deliberately NOT folded, so the line is visible:
#
#   `headless_comfy_all.VramSampler`   a different measurement -- whole-card, no per-process family.
#                                      Sharing a name would imply the numbers are comparable.
#   `generate_cached`                  two functions with one name: one returns
#                                      (png, stats, cached), the other a stamp dict. Nothing to
#                                      share but the word.
#   `headless_foliage.wipe_scene`      removes objects and KEEPS names; `empty_scene` resets the
#                                      file. Folding them would silently change what a gate wipes.

import os
import subprocess
import threading


def section(title):
    """A gate's section banner. One format, so two gates' output can be read side by side."""
    print()
    print(f"-- {title} " + "-" * max(0, 76 - len(title)))


def empty_scene():
    """A fresh, empty .blend, for a gate that measures one scene after another.

    `bpy` is imported inside the function on purpose: this module is also imported by gates that run
    in the tools venv (the agent-surface gate is one), and a module-level `import bpy` would make it
    unimportable there -- which would put `check` and `note` out of reach for exactly the reason a
    scene helper is not needed.
    """
    import bpy

    bpy.ops.wm.read_factory_settings(use_empty=True)


def stamp(target, data=None):
    """Read or write the timing and VRAM sidecar beside a cached artifact.

    Called with `data` it writes and returns it; without, it reads and returns `{}` when there is
    nothing readable. The point is that a RERUN reports what the generating run measured rather than
    what the cache cost -- a table of zeros beside a cached mesh is worse than no table, because it
    reads as a fast route.
    """
    import json

    path = target + ".json"
    if data is None:
        try:
            with open(path) as fh:
                return json.load(fh) or {}
        except (OSError, ValueError):
            return {}
    with open(path, "w") as fh:
        json.dump(data, fh, indent=2, sort_keys=True, default=str)
    return data


# The PIDs that are not ComfyUI's: this process. A gate runs inside Blender or the venv and holds
# GPU memory of its own (a render, a bake), and counting it as the server's would attribute
# Blender's frame buffer to the graph under test.
_OURS = {os.getpid()}


def gpu_sample():
    """(card MiB in use, {pid: MiB}) from nvidia-smi, or (None, {}) where there is no nvidia-smi.

    Absence is not an error: a gate on a machine with no NVIDIA card still measures everything else,
    and a VRAM column of Nones says so honestly.
    """
    try:
        card = subprocess.run(["nvidia-smi", "--query-gpu=memory.used",
                               "--format=csv,noheader,nounits"],
                              capture_output=True, text=True, timeout=10).stdout.strip()
        apps = subprocess.run(["nvidia-smi", "--query-compute-apps=pid,used_memory",
                               "--format=csv,noheader,nounits"],
                              capture_output=True, text=True, timeout=10).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None, {}
    procs = {}
    for line in apps.splitlines():
        bits = [b.strip() for b in line.split(",")]
        if len(bits) == 2 and bits[0].isdigit() and bits[1].isdigit():
            procs[int(bits[0])] = int(bits[1])
    return (int(card.splitlines()[0]) if card else None), procs


def comfy_family(procs):
    """Summed VRAM over the ComfyUI process family: everything on the card that is not this process.

    A gate holds GPU memory of its own -- a render, a bake -- so counting it as the server's would
    attribute Blender's frame buffer to the graph under test. `Vram` reads it per sample and the
    handback checks read it once either side of a free, which is why it is a function rather than a
    method: three gates had the same sum written inline beside a `_OURS` they imported privately.
    """
    return sum(mib for pid, mib in procs.items() if pid not in _OURS)


class Vram:
    """Peak VRAM across a job, sampled from a thread so the measurement does not serialise with it.

    Per process, summed over the ComfyUI family, with the RISE over this stage's own baseline
    reported beside the absolute peak: the rise is what the graph cost, the peak has to fit. Both
    are needed because the ordering rule the generation routes live under is about the peak -- once
    Omni has run, the SDXL atlas route OOMs whatever the card reports free.

    One class for every gate that reports VRAM, because those numbers are only worth anything if
    they are comparable, and five copies were already not: `report()` carried `rise` in one.
    """

    def __init__(self, interval=0.5):
        self.interval = interval
        self.card_peak = self.comfy_peak = 0
        self.card_start = self.comfy_start = 0
        self._stop = threading.Event()
        self._thread = None

    def _loop(self):
        while not self._stop.is_set():
            card, procs = gpu_sample()
            if card is not None:
                self.card_peak = max(self.card_peak, card)
                self.comfy_peak = max(self.comfy_peak, comfy_family(procs))
            self._stop.wait(self.interval)

    def __enter__(self):
        card, procs = gpu_sample()
        self.card_start = self.card_peak = card or 0
        self.comfy_start = self.comfy_peak = comfy_family(procs)
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *exc):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)
        return False

    def report(self):
        return {"card_start": self.card_start, "card_peak": self.card_peak,
                "comfy_start": self.comfy_start, "comfy_peak": self.comfy_peak,
                "rise": self.comfy_peak - self.comfy_start}
