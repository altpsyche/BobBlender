"""Path, Blender-binary, and bridge resolution for the agent-side MCP server.

This is the standalone counterpart to the extension's Blender-side code: it runs in a
plain Python process (the MCP stdio server the agent client spawns), NOT inside Blender,
so it imports no bpy. It also assumes NO repo checkout — the server ships inside the
installed extension and drives a user's own folders, configured by environment:

    $BOB_WORKDIR      the working root outputs are sandboxed under (default: CWD)
    $BOB_PROJECTS     the projects root (default: <workdir>/projects)
    $BOB_RENDERS      the renders root (default: <workdir>/renders)
    $BOB_ASSET_PACKS  asset-pack search path (read directly by core/assets)
    $BOB_BLENDER      the Blender executable (else known installs, then PATH)
    $BOB_BRIDGE_HOST  / $BOB_BRIDGE_PORT   the live-bridge socket (default 127.0.0.1:9876)

The one committed compute copy (core/heightfields) and the pack resolver (core/assets)
live in the sibling `core/` dir; add_core_to_path() puts that dir on sys.path so they
import as top-level `heightfields` / `assets` without importing the bpy-bound addon
package. Same trick as the dev venv's bobtools._hfpath.
"""

import glob
import os
import platform
import shutil
import sys
from pathlib import Path

DEFAULT_BRIDGE_HOST = "127.0.0.1"
DEFAULT_BRIDGE_PORT = 9876


# -- Locations inside the installed extension ------------------------------------------------
def ext_dir() -> Path:
    """The extension root (the bob_blender_tools folder). mcp_agent/paths.py -> mcp_agent -> ext."""
    return Path(__file__).resolve().parents[1]


def core_dir() -> Path:
    return ext_dir() / "core"


def runner_path() -> Path:
    """The Blender-side headless runner shipped inside the extension."""
    return ext_dir() / "runners" / "headless_build.py"


def add_core_to_path() -> None:
    """Put <ext>/core on sys.path so `import heightfields` / `import assets` resolve to the one
    committed source, without importing the bpy-bound bob_blender_tools package."""
    core = str(core_dir())
    if core not in sys.path:
        sys.path.insert(0, core)


# -- User-configured working folders ---------------------------------------------------------
def workdir() -> Path:
    env = os.environ.get("BOB_WORKDIR")
    return Path(env).expanduser().resolve() if env else Path.cwd().resolve()


def projects_dir() -> Path:
    env = os.environ.get("BOB_PROJECTS")
    return Path(env).expanduser().resolve() if env else workdir() / "projects"


def renders_dir() -> Path:
    env = os.environ.get("BOB_RENDERS")
    return Path(env).expanduser().resolve() if env else workdir() / "renders"


def resolve_output(rel_or_abs: str) -> Path:
    """Resolve an agent-supplied output path against the working dir and refuse escapes.

    Wire-supplied paths reach the file writers here; without the containment check an absolute
    path or a `..` traversal could overwrite files outside the working tree (pathlib lets an
    absolute right-operand win over the root). Callers pass workdir-relative paths, or an absolute
    path that is itself under the working dir; anything escaping is rejected.
    """
    root = workdir()
    resolved = (root / rel_or_abs).resolve()
    if not resolved.is_relative_to(root):
        raise ValueError(
            f"path escapes the working dir ({root}): {rel_or_abs!r}. "
            "Set $BOB_WORKDIR to the folder you want to write under."
        )
    return resolved


# -- Blender executable, resolved per platform (repo-free copy of the dev resolver) ----------
def _blender_candidates() -> list[Path]:
    system = platform.system()
    if system == "Linux":
        patterns = [
            "~/.steam/steam/steamapps/common/Blender/blender",
            "~/.local/share/Steam/steamapps/common/Blender/blender",
            "~/.steam/root/steamapps/common/Blender/blender",
            "/usr/bin/blender",
            "/usr/local/bin/blender",
            "/opt/blender/blender",
            "/var/lib/flatpak/exports/bin/org.blender.Blender",
            "~/.local/share/flatpak/exports/bin/org.blender.Blender",
            "/snap/bin/blender",
        ]
    elif system == "Darwin":
        patterns = [
            "/Applications/Blender.app/Contents/MacOS/Blender",
            "~/Applications/Blender.app/Contents/MacOS/Blender",
            "~/Library/Application Support/Steam/steamapps/common/Blender/"
            "Blender.app/Contents/MacOS/Blender",
        ]
    elif system == "Windows":
        patterns = [
            "C:/Program Files/Steam/steamapps/common/Blender/blender.exe",
            "C:/Program Files (x86)/Steam/steamapps/common/Blender/blender.exe",
            "C:/Program Files/Blender Foundation/Blender */blender.exe",
        ]
    else:  # pragma: no cover
        patterns = []

    out: list[Path] = []
    for pat in patterns:
        expanded = str(Path(pat).expanduser())
        if any(ch in pat for ch in "*?["):
            out += [Path(g) for g in sorted(glob.glob(expanded), reverse=True)]
        else:
            out.append(Path(expanded))
    return out


def blender_binary() -> str:
    """Resolve Blender: $BOB_BLENDER, then known install locations, then PATH."""
    env = os.environ.get("BOB_BLENDER")
    if env:
        return str(Path(env).expanduser())

    for cand in _blender_candidates():
        if cand.is_file():
            return str(cand)

    for name in ("blender", "blender.exe"):
        found = shutil.which(name)
        if found:
            return found

    raise FileNotFoundError(
        "Blender not found. Set $BOB_BLENDER to the executable path, "
        "or add Blender to your PATH."
    )


# -- Live bridge socket ----------------------------------------------------------------------
def bridge_host() -> str:
    return os.environ.get("BOB_BRIDGE_HOST") or DEFAULT_BRIDGE_HOST


def bridge_port() -> int:
    env = os.environ.get("BOB_BRIDGE_PORT")
    if env:
        try:
            return int(env)
        except ValueError:
            pass  # non-numeric override: fall through to the default
    return DEFAULT_BRIDGE_PORT
