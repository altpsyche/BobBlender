# Pre-P0 scope: CI lint + single-source spike

Two de-risking items to land before P0 of `RESTRUCTURE-PLAN.md`. Both are small,
independent, and reversible. Written 2026-07-24 against `fix/audit-remediation`.

Target: **Blender 5.2 only** (bundled Python 3.13, numpy 2.3.4). No support for
older Blender/numpy — 5.2 output is the single reference everywhere.

Verified facts this scope rests on:
- No CI exists yet (`.github/workflows/` absent), no ruff, no pre-commit. This scope
  stands up **real CI** (GitHub Actions), not a stopgap. Tests are pytest
  `tools/tests/test_*.py`, run with
  `uv run --with pytest --extra all --extra gpu --project tools pytest tools/tests -q`.
- Intra-extension imports are ALREADY relative (`from . import server, ui_helpers,
  world_panel` in every panel). Only `bbmcp` is imported absolutely, and it is an
  external top-level package today. So the tree is already lint-clean.
- `heightfields/` imports numpy + lazy cupy only — no bpy, no scipy. Blender 5.2
  bundles numpy 2.3.4. venv is Python 3.14; Blender is Python 3.13.
- `test_heightfields.py` imports scipy (`from scipy.ndimage import zoom`) — venv-only,
  fine; the package under test does not. It asserts bit-reproducibility against
  `tools/tests/data/golden_hf.npy` (via `np.array_equal` and a run `hash`).
- `pyproject.toml` already declares `gpu = ["cupy-cuda13x>=14.1.1"]` (CUDA 13 line).

---

## Item A — Self-import lint

**Why.** After P1, `core/ui/bridge` load under two fully-qualified names:
`bl_ext.<pkgid>.bob_blender_tools.*` (live addon) and `bob_blender_tools.*`
(headless, single `sys.path` insert). Relative imports resolve in both; any
*absolute self-import* resolves in only one and dies silently in the other. Lock
the current clean state so a P1/P2 file move cannot regress it.

**Rule.** Inside `blender/extensions/bob_blender_tools/`, no module may import the
extension's own top package (or a dead alias) by absolute path. Enforced patterns
(per line, ignoring comments/strings-in-practice via simple prefix match):

```
^\s*import\s+(bob_blender_tools|bbmcp)(\.|\s|$)
^\s*from\s+(bob_blender_tools|bbmcp)(\.|\s)
```

Banned-prefix list is a constant in the script:
- **Now (pre-P1):** `["bob_blender_tools"]`. Green on the current tree (nothing
  self-imports absolutely). `bbmcp` stays ALLOWED — P0 keeps it importable via shim.
- **At end of P1:** add `"bbmcp"` to the list in the same commit that deletes the
  shim, so the dead name can never creep back.

Relative imports (`from . import`, `from ..core...`) are always allowed. Bare
sibling imports (`import ui_helpers`) are not currently present and would ALSO break
under `bl_ext.*`; treat them as a violation too (optional stretch — flag any bare
`import <name>` where `<name>` is a sibling module basename). Keep the v1 script to
the two absolute-prefix patterns; note the bare-sibling check as a follow-up.

**Deliverables.**
1. `tools/scripts/check_selfimports.py` — walks the extension dir, prints each
   offender as `path:line: <line text>`, exits 1 if any, 0 otherwise. AST-based
   (parses each module, inspects `Import`/`ImportFrom` nodes) so comments and
   strings never false-positive. Takes the package dir as argv[1], defaults to the
   extension. Reusable by CI and a future pre-commit hook.
2. `tools/tests/test_selfimports.py` — a pytest that calls the checker and asserts
   exit 0. Local-debuggable; also runs inside the suite.
3. **`.github/workflows/ci.yml` — the real CI, landed now** (not a follow-up). One
   workflow, jobs:
   - `lint-and-test`: checkout, `astral-sh/setup-uv`, run
     `python tools/scripts/check_selfimports.py`, then the pytest command above.
     Fast, plain runner, no Blender needed (heightfields unit tests use venv numpy).
   - `blender-headless` (added with the spike/P0 work): downloads the Blender 5.2
     binary and runs the in-Blender geometry tests. Kept a separate job so the fast
     lint/test gate is not blocked on the heavy Blender download.
   Triggers: push + pull_request. This is proper CI from day one; the pytest test
   is a convenience, not the enforcement mechanism.

**Acceptance.**
- `python tools/scripts/check_selfimports.py` exits 0 on the current tree.
- Manually adding `from bob_blender_tools.core import x` to any panel makes it exit 1
  with the right `path:line`. Revert.
- `pytest tools/tests/test_selfimports.py` passes.
- `ci.yml` runs green on a pushed branch (lint + full pytest suite).

**Effort:** ~1 hour. **Risk:** none (green-on-arrival guard).

---

## Item B — Single-source spike (heightfields runs in-Blender, matches golden)

**Why.** The plan's single-source resolution assumes `core/heightfields/` can be
the ONE committed copy, imported by both the venv (3.14) and Blender (3.13) and
still bit-reproducible against the golden. Two things are unproven and must be
proven before P4 commits to it:
1. `heightfields` imports and runs in-process under **Blender 5.2's** Python 3.13 +
   bundled numpy 2.3.4 (today the bake shells out to the venv; in-Blender is a new
   path).
2. **Blender 5.2 CPU output is the single golden reference.** Since 5.2 is the only
   supported target, the golden must be the bytes 5.2 produces — not whatever the
   venv's numpy happens to emit. If the current `golden_hf.npy` (made under venv
   numpy) does not match Blender-5.2 CPU byte-for-byte, **regenerate the golden
   under Blender 5.2** and make that canonical. No tolerance compares, no
   supporting divergent numpy versions.

This is a throwaway proof, NOT the P4 move. No files are renamed; nothing in the
repo changes except an added spike script under scratchpad.

**Method.**
1. **Stage** the future layout in scratchpad: copy `tools/bobtools/heightfields/`
   to `<scratch>/bob_blender_tools/core/heightfields/` and add empty `__init__.py`
   at `bob_blender_tools/` and `bob_blender_tools/core/` so
   `from bob_blender_tools.core import heightfields` resolves off one `sys.path`
   insert of `<scratch>`.
2. **venv run (control):** `tools/.venv/bin/python` inserts `<scratch>`, imports via
   the new path, runs a small deterministic stack (`size=96, seed=3`, backend
   forced `cpu`), writes `venv_cpu.npy`. Confirms the path works and matches the
   existing `bobtools.heightfields` result.
3. **Blender run (the actual risk):** drive the in-env Blender 5.2 binary headless
   (`blender --background --python spike.py`), insert `<scratch>`, import via the
   new path, run the identical stack CPU, write `blender_cpu.npy`. Confirms
   in-process numpy compute works with no venv.
4. **Compare, Blender-5.2 as reference:** `np.array_equal` of `blender_cpu.npy` vs
   `tools/tests/data/golden_hf.npy`. If equal, golden stands. If not, the Blender
   5.2 output is authoritative — regenerate `golden_hf.npy` from `blender_cpu.npy`
   (update `make_golden.py` to run under Blender 5.2) and record the numpy delta as
   the reason. `venv_cpu.npy` is only a sanity control, not a reference; if the venv
   diverges from Blender 5.2 that is acceptable and expected — Blender wins.
5. **Auto backend in Blender:** with no cupy in Blender's Python, confirm the `auto`
   backend selects CPU without raising (the fallback P4/P5 depend on).

**Deliverables.**
- `<scratch>/spike_singlesource.py` (Blender side) + a short venv driver, throwaway.
- Findings appended here as a "Spike results" section: pass/fail per step, and the
  golden decision if bytes differ.

**Acceptance (all must hold to unblock P4's single-source decision):**
- Import via `bob_blender_tools.core.heightfields` succeeds under Blender 5.2 (and,
  as a control, under the venv).
- Blender-5.2 CPU run completes and is finite/normalised.
- Golden matches Blender-5.2 CPU byte-for-byte — either the existing golden already
  does, or it is regenerated under Blender 5.2 so it does. Golden is defined as
  "what Blender 5.2 produces", full stop.
- `auto` backend picks CPU cleanly in Blender (no cupy) with no error.

**Effort:** ~half a day incl. regenerating the golden if needed. **Risk:** low-medium
— the only open question is whether the golden needs a one-time regen under Blender
5.2; single-source itself is sound either way.

---

## Spike results (2026-07-24) — PASS

Ran the staged future layout (`bob_blender_tools/core/heightfields/`) in both
interpreters off one `sys.path` insert. Environment observed:
- venv: Python 3.14.6, numpy **2.5.1**, `auto` backend → **gpu** (cupy + CUDA present).
- Blender 5.2.0: Python 3.13.13, numpy **2.3.4**, `auto` backend → **cpu** (no cupy).

Byte-for-byte comparison of the quantized 64px golden field (`np.array_equal`):
- `blender_cpu` vs `golden_hf.npy`: **equal**, max_abs_diff 0.0, 0/4096 differ.
- `venv_cpu` vs `golden_hf.npy`: **equal**.
- `venv_cpu` vs `blender_cpu`: **equal**.

Conclusions:
- Single-source is sound. The compute imports and runs in-process under Blender 5.2
  and is bit-reproducible against the committed golden. **No golden regen needed** —
  the numpy 2.3.4 vs 2.5.1 gap does not perturb output (pure stencils + seeded PRNG).
- `auto` fallback works: Blender (no cupy) selects CPU with no error; venv selects GPU.
  Confirms the P4/P5 CPU-fallback assumption.

**New required P4 sub-task surfaced (blocker):** `heightfields/io.py` does
`from PIL import Image` at module top, and **Blender's Python has no PIL**, so the
package fails to import in-Blender as-is. The spike worked around it by making the
PIL import lazy AND bypassing the PNG file layer. But `pipeline.bake()` genuinely
writes/reads a 16-bit PNG through `io.to_png16`/`read_png16`, so P4 must replace the
PIL dependency with a **pure-numpy(+zlib) 16-bit-grayscale PNG codec** — no PIL, and
no bpy (the package must stay bpy-free). This is the one real code change P4 needs
beyond moving files; add it to P4 scope. Everything else in P4 is a move + repoint.

Spike artifacts are throwaway under scratchpad (`spike.py`, staged tree, `*_cpu.npy`).

**A first, then B.** A stands up the real CI (`ci.yml`) plus the self-import guard —
foundational, so B's eventual Blender-headless tests plug into a CI that already
exists. A is a fast, low-risk commit that locks the clean import state. B is the
spike that gates P4's single-source commitment and produces (if needed) the
Blender-5.2 golden. Neither depends on P0; land A, run B and record results, then
start P0.
