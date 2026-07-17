"""Naming helpers. No bpy, no I/O, so they are easy to test.

Shared so the CLI, MCP server, and future workflows name things the same way.
See docs/CONVENTIONS.md.
"""

import re


def slugify(name: str) -> str:
    """Lowercase kebab-case, safe for paths."""
    name = name.strip().lower()
    name = re.sub(r"[^\w\-]+", "-", name)
    name = re.sub(r"-{2,}", "-", name)
    return name.strip("-")
