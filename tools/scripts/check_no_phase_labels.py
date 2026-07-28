#!/usr/bin/env python3
"""Fail on development-phase labels in prose, comments, filenames and gate keys.

A phase label is a capital letter plus one or two digits, sometimes with a lowercase sub-letter:
one per subsystem, counting up as the work landed. The repo was indexed by them, and they had three
problems of which only the first is cosmetic. They were the *only* index into the repo's knowledge,
so a reader had to learn a private chronology before the software. Their shelf life expired the
moment the phase shipped: "this shipped broken" is history, while "a curve's radius must be handed
to Curve to Mesh explicitly" is still true. And two of the letters meant two different things each
-- one was both a splines polish pass and a generation review finding, another both a generation
route and a water-shader look pass -- so the index was ambiguous as well as private.

`docs/CONVENTIONS.md` states the replacement rule. This is its enforcement. The families it knows
about are named beside each alternative in the pattern below.

Usage:
  python tools/scripts/check_no_phase_labels.py [--list] [path ...]

Exit 0 = clean, 1 = labels found (printed as path:line: <source line>).

--list prints the per-area counts instead of failing, which is how the de-phasing work measured
its own progress: the count had to fall on every commit and was never allowed to rise.

The allowlist lives beside this script in `phase_label_allowlist.txt`, one substring per line with
a `#` comment giving the reason. It exists for genuine external names that happen to be
letter-number (`SDXL`, `TRELLIS.2`, `Hunyuan3D 2.1`, Blender versions) and for nothing else. A line
that suppresses a real phase label is a bug in the allowlist, not a fix for the hit.
"""

from __future__ import annotations

import argparse
import pathlib
import re
import sys

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
ALLOWLIST = pathlib.Path(__file__).resolve().parent / "phase_label_allowlist.txt"

# The areas that carry documentation and comments. Anything outside them (generated packs, LFS
# assets, third-party workflow JSON) is not this check's business.
DEFAULT_AREAS = ("docs", "blender/extensions", "tools/scripts", "tools/tests", ".github",
                 "README.md")

SUFFIXES = (".py", ".md", ".yml", ".yaml", ".json", ".toml")

SKIP_DIRS = {".git", "__pycache__", ".venv", "node_modules", "_generated", "dist"}

# One label family per alternative, bounded so `F7` (no such phase) and `G10` do not match and so
# a bare capital letter beside a number in ordinary prose does not either.
LABEL_RE = re.compile(
    r"\b("
    r"F[1-6]"            # BobFoliage
    r"|G[0-9][a-c]?"     # generation phases, with the b/c sub-phases
    r"|S[1-5]"           # BobFirmament
    r"|C[1-5]"           # BobSplines
    r"|P[0-7]"           # repo restructure
    r"|D(?:1[0-9]|[1-9])"          # generation decisions
    r"|R(?:1[0-9]|2[01]|[1-9])"    # generation review findings AND splines polish, the collision
    r"|W(?:1[0-4]|[1-9])"          # generation routes AND the water look pass, the other collision
    r"|A[1-9]|B[1-9]"    # UX audit findings, review bugs
    r"|M[12]"            # terrain engine rewrite
    r")\b"
)

# Filenames that were named after a phase or a phase artifact rather than a feature.
BAD_NAME_RE = re.compile(
    r"(_g[0-9][a-c]?\.py$|-HANDOVER\.md$|-AUDIT\.md$|-FINDINGS\.md$|-CRITIQUE\.md$"
    r"|-REDESIGN\.md$|^PRE-P[0-9]|^UX-ROUND[0-9])",
    re.IGNORECASE,
)


def allowed_spans(line: str, allowlist: list[str]) -> list[tuple[int, int]]:
    """Character spans of this line covered by an allowlist entry."""
    spans = []
    for phrase in allowlist:
        start = 0
        while (idx := line.find(phrase, start)) != -1:
            spans.append((idx, idx + len(phrase)))
            start = idx + 1
    return spans


def load_allowlist() -> list[str]:
    if not ALLOWLIST.is_file():
        return []
    out = []
    for raw in ALLOWLIST.read_text(encoding="utf-8").splitlines():
        text = raw.split("#", 1)[0].strip()
        if text:
            out.append(text)
    return out


def check_text(text: str, allowlist: list[str]) -> list[tuple[int, str]]:
    """[(lineno, source_line), ...] for lines carrying a label no allowlist entry covers."""
    hits = []
    for lineno, line in enumerate(text.splitlines(), 1):
        matches = list(LABEL_RE.finditer(line))
        if not matches:
            continue
        spans = allowed_spans(line, allowlist)
        if any(not any(s <= m.start() and m.end() <= e for s, e in spans) for m in matches):
            hits.append((lineno, line.strip()[:160]))
    return hits


def walk(paths: list[pathlib.Path]):
    for path in paths:
        if path.is_file():
            yield path
            continue
        for child in sorted(path.rglob("*")):
            if child.is_file() and child.suffix in SUFFIXES \
                    and not set(child.parts) & SKIP_DIRS:
                yield child


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("paths", nargs="*", help="files or directories, else every documented area")
    ap.add_argument("--list", action="store_true", help="print per-area counts instead of failing")
    args = ap.parse_args(argv)

    roots = [pathlib.Path(p) for p in args.paths] or \
        [REPO_ROOT / area for area in DEFAULT_AREAS]
    roots = [r for r in roots if r.exists()]
    allowlist = load_allowlist()

    total, bad_names, per_area = 0, [], {}
    for path in walk(roots):
        rel = path.relative_to(REPO_ROOT) if path.is_absolute() else path
        if BAD_NAME_RE.search(path.name):
            bad_names.append(str(rel))
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        hits = check_text(text, allowlist)
        if not hits:
            continue
        area = str(rel).split("/", 1)[0]
        per_area[area] = per_area.get(area, 0) + len(hits)
        total += len(hits)
        if not args.list:
            for lineno, line in hits:
                print(f"{rel}:{lineno}: {line}")

    if args.list:
        for area in sorted(per_area):
            print(f"  {area:22} {per_area[area]:5d}")
        print(f"  {'total':22} {total:5d} label line(s)")
        if bad_names:
            print(f"  {'phase-named files':22} {len(bad_names):5d}")
        return 0

    for name in bad_names:
        print(f"{name}: filename names a phase or a phase artifact, not a feature")
    if total or bad_names:
        print(f"\n{total} line(s) with a phase label, {len(bad_names)} phase-named file(s). "
              f"See docs/CONVENTIONS.md; allowlist genuine external names in "
              f"{ALLOWLIST.name}.", file=sys.stderr)
        return 1
    print("no phase labels")
    return 0


if __name__ == "__main__":
    sys.exit(main())
