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
