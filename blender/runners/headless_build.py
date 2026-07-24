"""Headless runner, executed inside Blender by the executor.

    blender --background --factory-startup --python headless_build.py -- <req.json> <result.json>

Reads a BuildRequest payload, applies each op via bbmcp, saves the .blend,
and writes a BuildResult JSON. All Blender-side; no external deps.
"""

import json
import os
import sys

import bpy


def _argv_after_dashes():
    idx = sys.argv.index("--")
    return sys.argv[idx + 1 :]


def main():
    req_path, result_path = _argv_after_dashes()

    # The builder library is bob_blender_tools.core, inside the extension. One
    # sys.path insert of the extensions dir, then a plain package import.
    blender_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # blender/
    sys.path.insert(0, os.path.join(blender_dir, "extensions"))
    from bob_blender_tools.core.dispatch import apply_op

    payload = json.loads(open(req_path).read())
    result = {
        "ok": True,
        "output_file": payload.get("output_file"),
        "results": [],
        "error": None,
    }

    try:
        base = payload.get("base_file_abs")
        if base:
            bpy.ops.wm.open_mainfile(filepath=base)
        else:
            bpy.ops.wm.read_homefile(use_empty=True)

        for op in payload.get("ops", []):
            result["results"].append(apply_op(op))

        out = payload["output_file_abs"]
        os.makedirs(os.path.dirname(out), exist_ok=True)
        bpy.ops.wm.save_as_mainfile(filepath=out)
    except Exception as exc:  # report cleanly to the executor
        result["ok"] = False
        result["error"] = f"{type(exc).__name__}: {exc}"

    with open(result_path, "w") as fh:
        json.dump(result, fh)


if __name__ == "__main__":
    main()
