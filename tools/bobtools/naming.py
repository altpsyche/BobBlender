"""Pure naming helpers (no bpy, no I/O) — trivially testable.

Shared conventions live here so the CLI, MCP server, and any future workflow
all name things the same way. See docs/CONVENTIONS.md.
"""

import re


def slugify(name: str) -> str:
    """Lowercase kebab-case, safe for paths."""
    name = name.strip().lower()
    name = re.sub(r"[^\w\-]+", "-", name)
    name = re.sub(r"-{2,}", "-", name)
    return name.strip("-")


def render_subpath(project: str, scene: str, date_str: str, version: int = 1) -> str:
    """<project>/<date>/<project>_<scene>_v###  (extension added by the renderer)."""
    scene = slugify(scene) or "scene"
    return f"{project}/{date_str}/{project}_{scene}_v{version:03d}"
