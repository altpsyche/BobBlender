"""Regenerate the golden heightfield the CPU pipeline must reproduce byte-for-byte.

Run: uv run --extra terrain --project tools python tools/tests/data/make_golden.py
Only rerun when the generation/erosion op math intentionally changes (engine, ops_*,
generate). The golden runs an explicit op stack -- not a named preset -- so retuning
a preset does NOT require regenerating it. Commit golden_hf.npy with the code change.
"""

import pathlib
import tempfile

import numpy as np

from bobtools import heightfields as hf
from bobtools.heightfields import io

# A small explicit stack: a generator, flow-accumulation fluvial erosion, and a
# thermal pass -- enough to exercise the core op path deterministically at 64px.
GOLDEN_PARAMS = {
    "size": 64, "seed": 5, "backend": "cpu",
    "stack": [
        {"kind": "noise", "ridged": 0.5, "detail_strength": 0.6, "octaves": 4,
         "warp": 50, "seed": 5},
        {"kind": "fluvial", "iterations": 20, "k": 0.02, "sp_m": 0.5, "sp_n": 1.0,
         "diffusion": 0.05, "talus": 0.004, "thermal_iters": 1, "recompute": 20,
         "fill_iters": 120, "acc_iters": 120, "max_delta": 0.03},
        {"kind": "thermal", "talus": 0.01, "factor": 0.5, "iterations": 2},
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
