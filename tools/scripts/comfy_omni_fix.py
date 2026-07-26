#!/usr/bin/env python3
"""Make the downloaded Hunyuan3D-Omni control encoder match the installed wrapper's attribute name.

The defect, measured at G4c and silent by construction. `ComfyUI-Hy3D-Omni` vendors a copy of
Tencent's `hy3dshape` in which one attribute has been renamed: upstream's `OmniEncoder.linear`, the
MLP that projects the Fourier-embedded control signal (point cloud, voxel, bounding box or skeleton)
into the DiT's token stream, is spelled `self.liner` in the vendored copy. The released checkpoint
stores it as `linear.*`, and the pipeline loads with `strict=False`, so the three tensors go
MISSING, the projection keeps its random initialisation, and generation runs to completion with the
control signal reduced to noise. The server log says so and nothing else does:

    Loaded .../cond_encoder/pytorch_model.bin with 442 missing and 3 unexpected keys
    Missing Keys: Counter({'image_encoder': 439, 'liner': 3})
    Unexpected Keys: Counter({'linear': 3})

Measured effect on a block-out whose shape nothing in the image suggests (a 0.6 x 0.15 x 1.0 L):
voxel IoU against the control **0.010** before this fix. The 439 `image_encoder` keys are benign --
DINOv2 comes from the transformers hub, not from this file.

So the fix is a three-key rename, and this script does it in the direction the INSTALLED wrapper
needs rather than in a fixed direction, which is what keeps it correct if the wrapper is ever
updated to upstream's spelling. Every git tree stays clean: the wrapper submodule is untouched, and
the only edited file is a local copy of a downloaded weight file, with the original kept beside it.

    python tools/scripts/comfy_omni_fix.py                     # detect and fix
    python tools/scripts/comfy_omni_fix.py --check             # report only, exit 1 if wrong
    python tools/scripts/comfy_omni_fix.py --comfy <path>      # explicit ComfyUI checkout

Exits 0 with a SKIP when the wrapper or the weights are absent, because neither is required.
"""
import argparse
import os
import re
import shutil
import sys

COMFY_DEFAULT = os.path.expanduser("~/dev/ComfyUI")
WRAPPER = os.path.join("custom_nodes", "ComfyUI-Hy3D-Omni")
ENCODER = os.path.join(WRAPPER, "Hunyuan3D-Omni", "hy3dshape", "models", "conditioners",
                       "omni_encoder.py")
WEIGHTS = os.path.join("models", "hunyuan3d-omni", "cond_encoder", "pytorch_model.bin")
NAMES = ("linear", "liner")


def wanted_name(source_path):
    """Which prefix the installed wrapper's `OmniEncoder` expects, read from its source."""
    with open(source_path) as fh:
        source = fh.read()
    found = [name for name in NAMES if re.search(rf"self\.{name}\s*=\s*nn\.Sequential", source)]
    if len(found) != 1:
        raise SystemExit(f"cannot tell which name {source_path} uses (found {found or 'none'})")
    return found[0]


def state_dict_name(state):
    """Which prefix the checkpoint stores, or None when it carries neither."""
    for name in NAMES:
        if any(key.startswith(name + ".") for key in state):
            return name
    return None


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--comfy", default=os.environ.get("BOB_COMFY_DIR", COMFY_DEFAULT))
    ap.add_argument("--check", action="store_true", help="report only; exit 1 when a fix is needed")
    args = ap.parse_args()

    encoder = os.path.join(args.comfy, ENCODER)
    weights = os.path.join(args.comfy, WEIGHTS)
    for path, what in ((encoder, "ComfyUI-Hy3D-Omni"), (weights, "the Omni cond_encoder weights")):
        if not os.path.exists(path):
            print(f"SKIP: {what} is not installed ({path})")
            return 0

    import torch

    want = wanted_name(encoder)
    state = torch.load(weights, map_location="cpu", weights_only=True)
    have = state_dict_name(state)
    print(f"wrapper expects '{want}.*', checkpoint carries '{have}.*'")
    if have is None:
        print("FAIL: the checkpoint carries neither name; is this the Omni cond_encoder?")
        return 1
    if have == want:
        print("OK: the control projection loads. Nothing to do.")
        return 0
    if args.check:
        print(f"FIX NEEDED: rename {have}.* to {want}.* (the control signal is currently random)")
        return 1

    backup = weights + ".orig"
    if not os.path.exists(backup):
        shutil.copyfile(weights, backup)
        print(f"original kept at {backup}")
    renamed = {(want + key[len(have):] if key.startswith(have + ".") else key): value
               for key, value in state.items()}
    torch.save(renamed, weights)
    moved = sorted(k for k in renamed if k.startswith(want + "."))
    print(f"renamed {len(moved)} keys: {', '.join(moved)}")
    print("Reload the pipeline (Hy3DOmniLoadPipeline force_reload, or restart ComfyUI).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
