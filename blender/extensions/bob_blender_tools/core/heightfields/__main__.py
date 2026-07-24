"""CLI for a bake, so another interpreter can drive one by subprocess. Run it as
`python -m heightfields --out X.png --params-file p.json` (with this core dir on sys.path),
or from the dev venv as `python -m bobtools.hf_cli ...` (which puts core on the path first).

Prints the result metadata as JSON on the last stdout line. `--backends` prints
the available compute backends (and the GPU device name if present) instead of
baking, so the panel can show a backend hint without importing numpy or CuPy.
"""

import argparse
import json

from . import presets
from .params import build_params
from .pipeline import bake


def _print_backends():
    from . import backend as backend_mod

    names = backend_mod.available()
    info = {"available": names}
    if "gpu" in names:
        try:
            info["device"] = backend_mod.select("gpu").info().get("device")
        except Exception:
            pass
    print(json.dumps(info))


def main():
    ap = argparse.ArgumentParser(prog="heightfields")
    ap.add_argument("--out", help="absolute PNG path")
    ap.add_argument("--params-file", help="JSON full-params file")
    ap.add_argument("--knobs-file", help="JSON flat-knobs file (expanded via build_params)")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--preview", action="store_true", help="bake at preview resolution")
    ap.add_argument("--maps", action="store_true",
                    help="also emit flow/wetness sidecar PNGs beside the height PNG")
    ap.add_argument("--backends", action="store_true",
                    help="print available backends as JSON and exit")
    args = ap.parse_args()

    if args.backends:
        _print_backends()
        return

    if not args.out or not (args.params_file or args.knobs_file):
        ap.error("need --out and one of --params-file/--knobs-file, unless --backends")

    if args.knobs_file:
        with open(args.knobs_file) as fh:
            knobs = json.load(fh)
        # A knobs file may carry an explicit op stack (the panel's custom-stack mode);
        # pass it through as-is. Otherwise expand the preset + global knobs.
        if "stack" in knobs:
            params = {k: knobs[k] for k in ("size", "seed", "backend", "stack") if k in knobs}
        else:
            params = build_params(knobs)
    else:
        with open(args.params_file) as fh:
            params = json.load(fh)
        preset = params.pop("preset", None)
        if preset is not None:
            base = presets.get(preset)
            base.update(params)
            params = base

    if args.maps:
        params["maps"] = True
    result = bake(args.out, params, force=args.force, preview=args.preview)
    print(json.dumps(result))


if __name__ == "__main__":
    main()
