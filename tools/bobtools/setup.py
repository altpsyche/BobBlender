"""`bob-setup` — dev-install the bob_blender_mcp extension into Blender, cross-OS.

Symlinks (or copies, where symlinks aren't available) the repo's
blender/extensions/bob_blender_mcp into Blender's user extensions dir, so
enabling it in Preferences picks up live repo edits. Prints a readiness list.

Run from a fresh clone:  uv run --project tools bob-setup
"""

import argparse
import os
import platform
import re
import shutil
import sys
from pathlib import Path

from . import config

_VERSION_RE = re.compile(r"^\d+\.\d+$")


def _blender_config_root() -> Path | None:
    system = platform.system()
    if system == "Linux":
        return Path.home() / ".config" / "blender"
    if system == "Darwin":
        return Path.home() / "Library" / "Application Support" / "Blender"
    if system == "Windows":
        appdata = os.environ.get("APPDATA")
        if appdata:
            return Path(appdata) / "Blender Foundation" / "Blender"
    return None


def _latest_extensions_dir() -> Path | None:
    root = _blender_config_root()
    if not root or not root.is_dir():
        return None
    # Highest version profile (extensions/ is created lazily by Blender, so we
    # don't require it to pre-exist — just a real version dir).
    versions = sorted(
        (
            p
            for p in root.iterdir()
            if p.is_dir() and _VERSION_RE.match(p.name)
        ),
        key=lambda p: [int(x) for x in p.name.split(".")],
        reverse=True,
    )
    if not versions:
        return None
    return versions[0] / "extensions" / "user_default"


def dev_install_extension() -> str:
    src = config.repo_root() / "blender" / "extensions" / "bob_blender_mcp"
    if not src.is_dir():
        raise FileNotFoundError(f"extension source not found: {src}")

    target_root = _latest_extensions_dir()
    if target_root is None:
        raise FileNotFoundError(
            "Could not find Blender's extensions dir. Launch Blender once, then "
            "re-run, or symlink manually."
        )
    target_root.mkdir(parents=True, exist_ok=True)
    dest = target_root / "bob_blender_mcp"

    if dest.is_symlink() or dest.exists():
        if dest.is_symlink() and os.path.realpath(dest) == str(src.resolve()):
            return f"already linked: {dest}"
        if dest.is_symlink() or dest.is_file():
            dest.unlink()
        else:
            shutil.rmtree(dest)

    try:
        dest.symlink_to(src, target_is_directory=True)
        return f"symlinked {dest} -> {src}"
    except (OSError, NotImplementedError):
        shutil.copytree(src, dest)
        return f"copied (no symlink) {src} -> {dest}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Set up the Bob MCP pipeline.")
    parser.add_argument(
        "--skip-extension", action="store_true", help="Don't dev-install the addon."
    )
    args = parser.parse_args(argv)

    print(f"repo:    {config.repo_root()}")
    try:
        print(f"blender: {config.blender_binary()}")
    except FileNotFoundError as exc:
        print(f"blender: NOT FOUND — {exc}", file=sys.stderr)
    print(f"bridge:  {config.bridge_host()}:{config.bridge_port()}")

    if not args.skip_extension:
        try:
            print(f"addon:   {dev_install_extension()}")
        except (FileNotFoundError, OSError) as exc:
            print(f"addon:   FAILED — {exc}", file=sys.stderr)
            return 1

    print(
        "\nNext:\n"
        "  1. Blender → Preferences → Add-ons → enable 'Bob Blender MCP' (autostart on).\n"
        "  2. Open a Claude Code session in this repo; approve the 'bob' MCP server.\n"
        "  3. Ask the agent to build — it lands in your live viewport."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
