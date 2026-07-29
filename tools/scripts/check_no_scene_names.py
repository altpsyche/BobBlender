#!/usr/bin/env python3
"""Fail on a throwaway scene's name in shipped code, tests, scripts or docs.

The tool measures itself constantly, and every measurement was taken somewhere. Naming that
somewhere costs the reader twice. A scene nobody will ship is a private index, exactly like a phase
label: "<some scene>'s gate found it" tells a reader who was not there nothing, while "five
generated meshes shipped carrying 48 to 229 boundary edges" is still true and still checkable. And
a scene name dates the evidence to a project rather than attaching it to the asset class it was
actually measured on, so the next reader cannot tell whether it generalises.

The sibling rule is `tools/scripts/check_no_phase_labels.py`, and this is the same shape for the
same reason. `docs/CONVENTIONS.md` states both.

**The figures always stay.** Only their attribution changes: name the asset class and the sample
size, never the scene. A bar with no evidence behind it is the failure mode this repo avoids
everywhere, so an edit that drops a number to satisfy this guard has made the repo worse.

Usage:
  python tools/scripts/check_no_scene_names.py [--list] [path ...]

Exit 0 = clean, 1 = names found (printed as path:line: <source line>).

--list prints the per-area counts instead of failing, which is how this work measured its own
progress: the count had to fall on every commit and was never allowed to rise.

`projects/` is excluded on purpose. That is where scene work is allowed to live and where a scene
name is the correct name for a thing. Everything else is the shipped tool.

The deny-list is maintained by hand and grows the day someone names a third scene. Bare `barn` is
NOT on it: it is an ordinary English word and a legitimate block-out example ("a gabled timber
structure" is what the barn stood in for), so it is swept once by hand rather than banned forever.
"""

from __future__ import annotations

import argparse
import pathlib
import re
import sys

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]

# This file holds the deny-list, so it is the one place the names are allowed to appear.
SELF = pathlib.Path(__file__).resolve()

# The areas that carry documentation and comments, i.e. the shipped tool. `projects/` is absent
# deliberately; see the module docstring.
DEFAULT_AREAS = ("docs", "blender/extensions", "tools/scripts", "tools/tests", "references",
                 ".github", "README.md")

SUFFIXES = (".py", ".md", ".yml", ".yaml", ".json", ".toml")

SKIP_DIRS = {".git", "__pycache__", ".venv", "node_modules", "_generated", "dist", "projects",
             "packs"}

# Scene nouns, matched case-insensitively. One entry per way a scene has been named so far.
SCENE_RE = re.compile(
    r"("
    r"forest[-\s]barn"     # the scene this guard was written for
    r"|redwood"            # the earlier run, the second scene named
    r"|BarnCtrl"           # its control-conditioned asset, named after the barn
    r"|gate[A-Z]\d"        # its render names, e.g. gateA2_barn_threequarter.png
    r")",
    re.IGNORECASE,
)

# Filenames named after a scene rather than after what they check.
BAD_NAME_RE = re.compile(r"(redwood|forest[-_]barn)", re.IGNORECASE)


def check_text(text: str) -> list[tuple[int, str]]:
    """[(lineno, source_line), ...] for lines naming a scene."""
    return [(n, line.strip()[:160]) for n, line in enumerate(text.splitlines(), 1)
            if SCENE_RE.search(line)]


def walk(paths: list[pathlib.Path]):
    for path in paths:
        if path.is_file():
            if path.resolve() != SELF:
                yield path
            continue
        for child in sorted(path.rglob("*")):
            if child.is_file() and child.suffix in SUFFIXES \
                    and not set(child.parts) & SKIP_DIRS \
                    and child.resolve() != SELF:
                yield child


def relative(path: pathlib.Path) -> pathlib.Path:
    """`path` relative to the repo when it is inside it, else `path` unchanged.

    An explicit path argument is allowed to point anywhere -- that is how a pre-commit hook or a
    one-off check on a scratch file uses this -- so a path outside the repo has to print rather
    than raise.
    """
    try:
        return path.relative_to(REPO_ROOT)
    except ValueError:
        return path


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("paths", nargs="*", help="files or directories, else every documented area")
    ap.add_argument("--list", action="store_true", help="print per-area counts instead of failing")
    args = ap.parse_args(argv)

    roots = [pathlib.Path(p) for p in args.paths] or \
        [REPO_ROOT / area for area in DEFAULT_AREAS]
    roots = [r for r in roots if r.exists()]

    total, bad_names, per_area = 0, [], {}
    for path in walk(roots):
        rel = relative(path)
        if BAD_NAME_RE.search(path.name):
            bad_names.append(str(rel))
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        hits = check_text(text)
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
        print(f"  {'total':22} {total:5d} scene-name line(s)")
        if bad_names:
            print(f"  {'scene-named files':22} {len(bad_names):5d}")
        return 0

    for name in bad_names:
        print(f"{name}: filename names a scene, not what it checks")
    if total or bad_names:
        print(f"\n{total} line(s) naming a scene, {len(bad_names)} scene-named file(s). "
              f"See docs/CONVENTIONS.md: name the asset class and the sample size, and keep "
              f"every figure.", file=sys.stderr)
        return 1
    print("no scene names")
    return 0


if __name__ == "__main__":
    sys.exit(main())
