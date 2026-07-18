"""CLI for a bake, so Blender (a different interpreter) can drive one by
subprocess: `python -m bobtools.heightfields --out X.png --params-file p.json`.

Prints the result metadata as JSON on the last stdout line.
"""

import argparse
import json

from .pipeline import bake
from . import presets


def main():
    ap = argparse.ArgumentParser(prog="bobtools.heightfields")
    ap.add_argument("--out", required=True, help="absolute PNG path")
    ap.add_argument("--params-file", required=True, help="JSON params file")
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()

    with open(args.params_file) as fh:
        params = json.load(fh)
    preset = params.pop("preset", None)
    if preset is not None:
        base = presets.get(preset)
        base.update(params)
        params = base

    result = bake(args.out, params, force=args.force)
    print(json.dumps(result))


if __name__ == "__main__":
    main()
