"""Regenerate the golden heightfield the CPU pipeline must reproduce.

Run: uv run --extra terrain --project tools python tools/tests/data/make_golden.py
Only rerun when the generation or erosion algorithm intentionally changes; commit
the updated golden_hf.npy alongside the code change.
"""

import pathlib
import tempfile

import numpy as np

from bobtools import heightfields as hf
from bobtools.heightfields import io

GOLDEN_PARAMS = {
    "size": 64, "seed": 5, "backend": "cpu",
    "passes": [
        {"kind": "smooth", "sigma": 1.0},
        {"kind": "hydraulic", "droplets": 3000, "max_steps": 24, "radius": 2},
        {"kind": "thermal", "iterations": 2},
        {"kind": "smooth", "sigma": 0.8},
    ],
}


def main():
    out = pathlib.Path(__file__).parent / "golden_hf.npy"
    with tempfile.TemporaryDirectory() as d:
        png = str(pathlib.Path(d) / "g.png")
        hf.bake(png, GOLDEN_PARAMS, force=True)
        img = io.read_png16(png)
    np.save(out, img)
    print(f"wrote {out} shape={img.shape} dtype={img.dtype}")


if __name__ == "__main__":
    main()
