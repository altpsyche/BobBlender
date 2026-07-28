#!/usr/bin/env python3
"""Run every shipped ComfyUI gate as ONE command, one summary line each (docs/GENERATION.md).

Every generation feature shipped its own headless gate, and once there were seven of them, checking
that none had regressed meant seven invocations with seven different flag sets, so in practice
nobody checked. This is that one command. It spawns Blender per gate, reads each gate's own verdict,
and prints a line carrying the exit status, the wall clock, the peak VRAM the whole card reached
while it ran, and how many checks the gate skipped for want of a server.

    uv run --project tools python tools/scripts/headless_comfy_all.py
        [--gate variants-maps,terrain-macro] [--fast] [--timeout 3600] [--json PATH]

Nothing here re-implements a check. Each gate keeps its own reachability gate and its own exit code
(0 = nothing failed, and a gate with no server prints SKIP and still exits 0), so this runner is a
scheduler and a table: a regression anywhere is one command rather than seven, and a machine with
no ComfyUI at all reports every generation gate as SKIP and exits 0, which is the same
"ComfyUI is never required" property each gate already carries individually.

Peak VRAM is the WHOLE CARD, sampled at 4 Hz from `nvidia-smi` while the child runs, not the
per-process figure the individual gates report. That is deliberate: this is the number that says
whether a gate can share the machine, and the per-process split is the individual gate's job.

--fast passes each gate the flags that make it cheap (fewer prompts, cached generations, no slow A/B
baseline). It measures less; what it still measures is whether every gate runs. The full run is
GPU-hours, so --fast is what a regression check should use and the full run is what a decision
needs.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SCRIPTS = REPO / "tools" / "scripts"

# Every shipped gate, cheapest-first within the pipeline order, with what it covers and the flags
# that make it cheap. `slow` is a rough full-run cost in minutes on the reference 5080, so a caller
# can see what a full sweep costs before starting one. Keys are features, not phases: `--gate
# bbox-control` says what will run.
GATES = [
    {"key": "texture-sets-sampler", "script": "headless_texset.py",
     "covers": "the shipped texture-set sampler on a terrain layer",
     "fast": [], "slow": 1, "server": False},
    # Cross-subsystem seams rather than one feature: the things a whole-scene run found between
    # terrain, water, scatter and shading. Listed here because this is the runner anyone actually
    # invokes, and a gate nobody runs guards nothing. Needs no server and no card, so it is in
    # every --fast sweep.
    {"key": "scene-seams", "script": "headless_redwood.py",
     "covers": "the seams a whole-scene run found between subsystems",
     "fast": [], "slow": 1, "server": False},
    # BobFoliage. Its geometry never needs a server -- that is the point of the subsystem -- and its
    # two TEXTURE sets do, so it is the one gate that is half and half: every structural check runs
    # regardless and `check_generation` alone prints SKIP without a server (docs/FOLIAGE.md).
    # `server: False` still, because the gate is not skipped as a whole for want of one.
    {"key": "foliage", "script": "headless_foliage.py",
     "covers": "tree, cards, textures, wind, variants, a stand",
     "fast": [], "slow": 4, "server": False},
    {"key": "texture-sets", "script": "headless_gen_texture_sets.py",
     "covers": "prompt to a shaded terrain layer",
     "fast": [], "slow": 1, "server": True},
    {"key": "variants-maps", "script": "headless_gen_variants_maps.py",
     "covers": "variants, preflight, derived maps",
     "fast": ["--sets", "2"], "slow": 3, "server": True},
    {"key": "assets", "script": "headless_gen_assets.py",
     "covers": "prompt to a scattered asset",
     "fast": ["--assets", "1", "--no-ab"], "slow": 15, "server": True},
    {"key": "oneshot-vs-staged", "script": "headless_gen_oneshot_vs_staged.py",
     "covers": "one-shot against the staged chain",
     "fast": ["--no-gen"], "slow": 20, "server": True},
    {"key": "stylise-paint-multiview", "script": "headless_gen_stylise_paint_multiview.py",
     "covers": "stylise, paint, multi-view conditioning",
     "fast": ["--part", "a"], "slow": 25, "server": True},
    {"key": "blockout-control", "script": "headless_gen_blockout_control.py",
     "covers": "Omni block-out control from a point cloud",
     "fast": ["--part", "a", "--no-baseline"], "slow": 15, "server": True},
    {"key": "terrain-macro", "script": "headless_gen_terrain_macro.py",
     "covers": "a prompted terrain macro mask through the op stack",
     "fast": ["--part", "a"], "slow": 10, "server": True},
    # The one gate that does NOT run inside Blender: it drives the MCP tools in this process and
    # reaches Blender only through the executor, which is the whole point of it.
    {"key": "agent-surface", "script": "headless_gen_agent_surface.py",
     "covers": "the agent-facing surface, driven as an agent drives it",
     "fast": ["--part", "a"], "slow": 8, "server": True, "runner": "python"},
    # --fast here is parts A and D: A costs a second and D re-scores the one-shot-against-staged
    # cache with no GPU at all, so the cheap run still measures the route decision and the
    # dense-mesh answer.
    {"key": "geometry-ab", "script": "headless_gen_geometry_ab.py",
     "covers": "the geometry A/B, TRELLIS.2 against Hunyuan3D 2.1",
     "fast": ["--part", "a,d"], "slow": 30, "server": True},
    # --fast is part A: the values, the frame mapping and preflight, all of which cost a second and
    # none of which needs the card.
    {"key": "bbox-control", "script": "headless_gen_bbox_control.py",
     "covers": "Omni control from three proportions, against the point cloud",
     "fast": ["--part", "a"], "slow": 12, "server": True},
    # --fast is part A, for the same reason: the values, the frame constant and preflight cost a
    # second between them and none of them needs the card.
    {"key": "voxel-control", "script": "headless_gen_voxel_control.py",
     "covers": "Omni control from an occupancy grid, and the control ordering",
     "fast": ["--part", "a"], "slow": 14, "server": True},
]

FAIL_RE = re.compile(r"(\d+)\s+(?:failure\(s\)|FAILED)")


def blender_binary() -> str:
    env = os.environ.get("BOB_BLENDER")
    if env:
        return str(Path(env).expanduser())
    for cand in ("~/.steam/steam/steamapps/common/Blender/blender",
                 "~/.local/share/Steam/steamapps/common/Blender/blender",
                 "/usr/bin/blender"):
        path = Path(cand).expanduser()
        if path.is_file():
            return str(path)
    found = shutil.which("blender")
    if not found:
        raise SystemExit("Blender not found. Set $BOB_BLENDER.")
    return found


class VramSampler(threading.Thread):
    """Whole-card used VRAM in MiB, peaked over the life of one child process."""

    def __init__(self, interval=0.25):
        super().__init__(daemon=True)
        self.interval, self.peak, self._stop = interval, None, threading.Event()

    def run(self):
        if not shutil.which("nvidia-smi"):
            return
        while not self._stop.is_set():
            try:
                out = subprocess.run(
                    ["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"],
                    capture_output=True, text=True, timeout=5).stdout.strip().splitlines()
                used = max(int(v.strip()) for v in out if v.strip())
            except (OSError, ValueError, subprocess.SubprocessError):
                used = None
            if used is not None:
                self.peak = used if self.peak is None else max(self.peak, used)
            self._stop.wait(self.interval)

    def stop(self):
        self._stop.set()
        self.join(timeout=2)
        return self.peak


def verdict(text: str, code: int) -> tuple[str, str]:
    """(status, detail) from a gate's own output plus its exit code.

    Each gate already prints a decisive line and returns 1 only when something failed, so this reads
    the gate rather than re-judging it. Three verdict wordings are live across the seven gates ("no
    failures", "all checks passed", and "N failure(s)" where N may be 0), and a gate that printed
    NONE of them did not finish -- which is the case worth reporting loudly, because Blender exits 0
    after a script traceback, so a crashed gate looks like a clean one to an exit code alone. That
    is not hypothetical: it is how the `variants-maps` gate was found to have been crashing in its
    own layout stub for three releases while reporting exit 0.

    A gate that ran no checks at all and only skipped is SKIP, not PASS: "passed with no server"
    would be a lie about what was measured.
    """
    skips = len(re.findall(r"\[SKIP\]", text))
    passes = len(re.findall(r"\[(?:PASS|ok)\]", text))
    match = FAIL_RE.search(text)
    failed = int(match.group(1)) if match else 0
    if code != 0 or failed:
        line = next((ln.strip() for ln in reversed(text.splitlines()) if FAIL_RE.search(ln)), "")
        return "FAIL", line[:120] or f"exit {code}"
    if not (match or "no failures" in text or "all checks passed" in text):
        return "FAIL", f"no verdict printed, so the gate did not finish (exit {code})"
    if skips and not passes:
        return "SKIP", f"{skips} skipped, nothing measured"
    note = f"{passes} checks" + (f", {skips} skipped" if skips else "")
    return ("SKIP" if skips and passes < skips else "PASS"), note


def run_gate(gate: dict, blender: str, extra: list[str], timeout: float) -> dict:
    script = SCRIPTS / gate["script"]
    if not script.is_file():
        return {"key": gate["key"], "status": "MISSING", "detail": f"no {gate['script']}",
                "seconds": 0.0, "peak_vram_mib": None}
    if gate.get("runner") == "python":
        argv = [sys.executable, str(script)]
    else:
        argv = [blender, "--background", "--factory-startup", "--python", str(script), "--"]
    argv += gate["fast"] if extra is None else list(extra)
    sampler = VramSampler()
    sampler.start()
    t0 = time.time()
    try:
        proc = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
        out, code = (proc.stdout or "") + (proc.stderr or ""), proc.returncode
    except subprocess.TimeoutExpired as exc:
        out, code = (exc.stdout or b"").decode("utf-8", "replace"), 124
    seconds = time.time() - t0
    peak = sampler.stop()
    status, detail = ("FAIL", f"timed out after {timeout:.0f}s") if code == 124 \
        else verdict(out, code)
    return {"key": gate["key"], "covers": gate["covers"], "status": status,
            "detail": detail,
            "seconds": round(seconds, 1), "peak_vram_mib": peak, "output": out}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--gate", default="", help="comma-separated keys, else every gate in order")
    ap.add_argument("--fast", action="store_true", help="each gate's own cheap flags")
    ap.add_argument("--timeout", type=float, default=3600.0, help="per gate, seconds")
    ap.add_argument("--json", default="", help="write the table here as JSON")
    ap.add_argument("--verbose", action="store_true", help="echo each gate's full output")
    ap.add_argument("--list", action="store_true", help="list the gates and their cost, then exit")
    args = ap.parse_args(argv)

    if args.list:
        for gate in GATES:
            print(f"  {gate['key']:24} {gate['covers']:56} ~{gate['slow']:>3} min full")
        return 0

    wanted = {k.strip() for k in args.gate.split(",") if k.strip()}
    gates = [g for g in GATES if not wanted or g["key"] in wanted]
    unknown = wanted - {g["key"] for g in GATES}
    if unknown:
        print(f"unknown gate(s): {', '.join(sorted(unknown))}", file=sys.stderr)
        return 2

    blender = blender_binary()
    print(f"Blender: {blender}")
    print(f"gates:   {', '.join(g['key'] for g in gates)}"
          f"{'  (--fast)' if args.fast else ''}")
    print()

    results, t0 = [], time.time()
    for gate in gates:
        print(f"[{gate['key']}] {gate['covers']} ...", flush=True)
        result = run_gate(gate, blender, gate["fast"] if args.fast else [], args.timeout)
        results.append(result)
        if args.verbose:
            print(result.pop("output", ""))
        else:
            result.pop("output", None)
        vram = f"{result['peak_vram_mib']} MiB" if result["peak_vram_mib"] else "n/a"
        print(f"    {result['status']:7} {result['seconds']:>7.1f} s  peak {vram:>10}"
              f"  {result['detail']}", flush=True)
    total = time.time() - t0

    print()
    print(f"{'gate':24} {'status':7} {'wall':>9} {'peak VRAM':>11}  note")
    for result in results:
        vram = f"{result['peak_vram_mib']} MiB" if result["peak_vram_mib"] else "n/a"
        print(f"{result['key']:24} {result['status']:7} {result['seconds']:>7.1f} s "
              f"{vram:>11}  {result['detail']}")
    failed = [r["key"] for r in results if r["status"] in ("FAIL", "MISSING")]
    peaks = [r["peak_vram_mib"] for r in results if r["peak_vram_mib"]]
    print(f"\ntotal {total:.1f} s over {len(results)} gate(s)"
          f"{f', peak {max(peaks)} MiB' if peaks else ''}")
    print(f"FAILED: {', '.join(failed)}" if failed else "every gate passed or skipped cleanly")

    if args.json:
        Path(args.json).write_text(json.dumps(
            {"total_seconds": round(total, 1), "gates": results}, indent=2))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
