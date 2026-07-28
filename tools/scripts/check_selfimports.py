#!/usr/bin/env python3
"""Fail on absolute self-imports inside the extension package.

The extension loads under two fully-qualified names: `bl_ext.<pkgid>.bob_blender_tools.*`
in a live Blender install, and `bob_blender_tools.*` in the headless runner (one
sys.path insert). Relative imports resolve in both worlds; an absolute import of the
package's own top name resolves in only one and dies silently in the other. This
check keeps every intra-package import relative.

Banned top-level names (absolute import of any of these from inside the package):
  - bob_blender_tools : the package importing itself by absolute path
  - bbmcp             : the dead pre-rename alias, gone from the tree and kept banned here

Usage:
  python tools/scripts/check_selfimports.py [package_dir]

Exit 0 = clean, 1 = violations found (printed as path:line: <source>).
"""

from __future__ import annotations

import ast
import pathlib
import sys

BANNED_TOP_LEVEL = ("bob_blender_tools", "bbmcp")

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
DEFAULT_PKG = REPO_ROOT / "blender" / "extensions" / "bob_blender_tools"


def _banned(name: str | None) -> bool:
    """True if a dotted module name's top segment is a banned self-name."""
    if not name:
        return False
    return name.split(".", 1)[0] in BANNED_TOP_LEVEL


def check_file(path: pathlib.Path) -> list[tuple[int, str]]:
    """Return [(lineno, source_line), ...] for absolute self-imports in one file."""
    src = path.read_text(encoding="utf-8")
    try:
        tree = ast.parse(src, filename=str(path))
    except SyntaxError as exc:  # a broken file is its own failure; surface it.
        return [(exc.lineno or 0, f"SyntaxError: {exc.msg}")]
    lines = src.splitlines()
    hits: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            if any(_banned(alias.name) for alias in node.names):
                hits.append((node.lineno, lines[node.lineno - 1].strip()))
        elif isinstance(node, ast.ImportFrom):
            # level > 0 is a relative import (from . / from ..) — always allowed.
            if node.level == 0 and _banned(node.module):
                hits.append((node.lineno, lines[node.lineno - 1].strip()))
    return hits


def check_package(pkg_dir: pathlib.Path) -> list[str]:
    """Return formatted 'path:line: source' violations across a package tree."""
    violations: list[str] = []
    for path in sorted(pkg_dir.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        # runners/ holds Blender-side entry scripts (blender --python), not intra-package code.
        # They legitimately bootstrap the addon by absolute name after a sys.path insert, so they
        # are exempt from the relative-import rule. mcp_agent/ is the standalone agent-side server;
        # it never imports the bpy-bound addon package, so it too is outside the two-worlds rule.
        if "runners" in path.parts or "mcp_agent" in path.parts:
            continue
        for lineno, source in check_file(path):
            rel = path.relative_to(REPO_ROOT)
            violations.append(f"{rel}:{lineno}: {source}")
    return violations


def main(argv: list[str]) -> int:
    pkg_dir = pathlib.Path(argv[1]).resolve() if len(argv) > 1 else DEFAULT_PKG
    if not pkg_dir.is_dir():
        print(f"error: not a directory: {pkg_dir}", file=sys.stderr)
        return 2
    violations = check_package(pkg_dir)
    if violations:
        banned = ", ".join(BANNED_TOP_LEVEL)
        print(f"Absolute self-imports found (use relative imports instead of: {banned}):")
        for v in violations:
            print(f"  {v}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
