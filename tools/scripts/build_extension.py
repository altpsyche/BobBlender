#!/usr/bin/env python3
"""Build the BobBlenderTools extension zip.

Stamps the manifest version (optional), validates the manifest, then runs Blender's extension
builder to produce `bob_blender_tools-<version>.zip`. The extension folder is already the whole
product, and the terrain compute is committed inside it rather than copied from a venv, so there is
nothing to
vendor -- this just validates and zips.

Usage:
  uv run --project tools python tools/scripts/build_extension.py [--version X.Y.Z] [--output-dir DIR]

Blender is resolved via bobtools.config.blender_binary() ($BOB_BLENDER, known installs, PATH).
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

from bobtools.config import blender_binary

REPO = Path(__file__).resolve().parents[2]
EXT = REPO / "blender" / "extensions" / "bob_blender_tools"
MANIFEST = EXT / "blender_manifest.toml"


def _current_version() -> str:
    text = MANIFEST.read_text(encoding="utf-8")
    m = re.search(r'^version\s*=\s*"([^"]+)"', text, re.M)
    if not m:
        raise SystemExit("version not found in blender_manifest.toml")
    return m.group(1)


def _stamp_version(version: str) -> None:
    if not re.fullmatch(r"\d+\.\d+\.\d+", version):
        raise SystemExit(f"--version must be X.Y.Z, got {version!r}")
    text = MANIFEST.read_text(encoding="utf-8")
    new = re.sub(r'^version\s*=\s*"[^"]+"', f'version = "{version}"', text, count=1, flags=re.M)
    MANIFEST.write_text(new, encoding="utf-8")
    print(f"stamped version = {version}")


def _run(blender: str, *args: str) -> int:
    cmd = [blender, "--command", "extension", *args]
    print("+ " + " ".join(cmd))
    return subprocess.run(cmd).returncode


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="Build the BobBlenderTools extension zip")
    ap.add_argument("--version", help="stamp this X.Y.Z into the manifest before building")
    ap.add_argument("--output-dir", default=str(REPO / "dist"), help="where to write the zip (default: dist/)")
    args = ap.parse_args(argv)

    if args.version:
        _stamp_version(args.version)
    version = _current_version()

    # Refresh the generated op-vocabulary table in docs/API.md from the contracts (best-effort:
    # a docs hiccup must not block a build).
    gen = Path(__file__).with_name("gen_api_docs.py")
    rc = subprocess.run([sys.executable, str(gen)]).returncode
    if rc != 0:
        print("warning: API docs refresh failed; continuing", file=sys.stderr)

    try:
        blender = blender_binary()
    except FileNotFoundError as exc:
        print(exc, file=sys.stderr)
        return 2

    if _run(blender, "validate", str(EXT)) != 0:
        print("validation failed", file=sys.stderr)
        return 1

    out = Path(args.output_dir)
    out.mkdir(parents=True, exist_ok=True)
    if _run(blender, "build", "--source-dir", str(EXT), "--output-dir", str(out)) != 0:
        print("build failed", file=sys.stderr)
        return 1

    zips = sorted(out.glob("bob_blender_tools-*.zip"))
    print(f"built version {version}: " + (str(zips[-1]) if zips else f"(zip in {out})"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
