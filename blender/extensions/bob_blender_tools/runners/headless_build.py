"""Headless runner, executed inside Blender by the MCP executor.

    blender --background --factory-startup --python headless_build.py -- <req.json> <result.json>

Reads a BuildRequest payload, applies each op via the extension's core dispatch, saves the
.blend, and writes a BuildResult JSON. All Blender-side; no external deps.

This ships INSIDE the extension (bob_blender_tools/runners/) so a standalone install can build
headlessly with no repo checkout: the executor spawns Blender pointed at this file, and it
imports the installed extension's core.dispatch. It is an entry-point script, not intra-package
code, so its absolute `bob_blender_tools` bootstrap import is exempt from the self-import lint
(check_selfimports skips runners/).
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

    # The builder library is bob_blender_tools.core, the package this runner lives inside. Put the
    # dir that CONTAINS bob_blender_tools on sys.path, then a plain package import. runners/
    # -> bob_blender_tools -> <the containing dir>.
    containing = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    sys.path.insert(0, containing)
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
