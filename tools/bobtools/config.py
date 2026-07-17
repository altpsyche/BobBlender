"""Locate the repo, Blender, and shared settings — from anywhere, on any OS.

Repo root resolution: $BOB_REPO → walk up from CWD for projects/+library/ →
install-relative fallback (tools/bobtools/config.py → parents[2]).

Shared config lives in <repo>/bob.toml so both the external venv and Blender's
bundled Python read the same host/port (a file is the DRY cross-interpreter
surface, same as the JSON build boundary).
"""

import glob
import os
import platform
import shutil
from pathlib import Path

try:
    import tomllib  # Python 3.11+ (and Blender 3.13's bundled Python)
except ModuleNotFoundError:  # pragma: no cover
    tomllib = None

DEFAULT_BRIDGE_HOST = "127.0.0.1"
DEFAULT_BRIDGE_PORT = 9876


def _has_markers(path: Path) -> bool:
    return (path / "projects").is_dir() and (path / "library").is_dir()


def repo_root() -> Path:
    env = os.environ.get("BOB_REPO")
    if env:
        return Path(env).expanduser().resolve()

    cwd = Path.cwd().resolve()
    for path in (cwd, *cwd.parents):
        if _has_markers(path):
            return path

    return Path(__file__).resolve().parents[2]


def projects_dir() -> Path:
    return repo_root() / "projects"


def library_dir() -> Path:
    return repo_root() / "library"


def renders_dir() -> Path:
    return repo_root() / "renders"


def template_dir() -> Path:
    return projects_dir() / "_template"


def blender_runner(name: str) -> Path:
    """Path to a bpy-side runner script under blender/runners/."""
    return repo_root() / "blender" / "runners" / name


# ── Shared settings (bob.toml) ────────────────────────────────────────────
def settings() -> dict:
    path = repo_root() / "bob.toml"
    if tomllib and path.is_file():
        with open(path, "rb") as fh:
            return tomllib.load(fh)
    return {}


def bridge_host() -> str:
    return (
        os.environ.get("BOB_BRIDGE_HOST")
        or settings().get("bridge", {}).get("host")
        or DEFAULT_BRIDGE_HOST
    )


def bridge_port() -> int:
    env = os.environ.get("BOB_BRIDGE_PORT")
    if env:
        return int(env)
    return int(settings().get("bridge", {}).get("port", DEFAULT_BRIDGE_PORT))


# ── Blender executable (cross-OS) ─────────────────────────────────────────
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
    """Resolve Blender: $BOB_BLENDER → known install locations → PATH."""
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
