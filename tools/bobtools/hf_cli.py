"""Venv CLI entry for the single-source heightfields bake.

Invoked as `python -m bobtools.hf_cli ...`. `bobtools` is installed in the venv, so this resolves
with NO PYTHONPATH set — which matters because PYTHONPATH is dropped across the Steam host-hop
(`steam-runtime-launch-client`) used when a sandboxed Blender falls back to the host venv for GPU.
Importing `_hfpath` puts the sole compute copy (core/heightfields) on sys.path; then the real CLI
in `heightfields.__main__` runs.
"""

import bobtools._hfpath  # noqa: F401  (side effect: adds core/heightfields to sys.path)
from heightfields.__main__ import main

if __name__ == "__main__":
    main()
