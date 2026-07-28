"""Preflight every shipped workflow against a ComfyUI server (docs/COMFYUI.md, Testing).

The check that keeps local-only true over time rather than by intention: a graph pasted in
from a community workflow can reach for a `comfy_api_nodes` class by accident, and the only place
that gets caught for free is here. It also catches the failure that actually happens on a fresh
machine, which is a model nobody downloaded.

    python3 tools/scripts/comfy_preflight.py [--url http://127.0.0.1:8188]
                                             [--object-info cached.json] [--dump-object-info out]

Exit code 0 = every shipped graph would queue. With no server and no cached `/object_info` it
prints SKIP and exits 0, because ComfyUI is never required.

`--object-info` runs the whole check offline against a dump, which is what makes it usable in a
test: `--dump-object-info` writes one from a live server.
"""

import argparse
import json
import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(REPO, "blender", "extensions", "bob_blender_tools", "core"))

import comfy  # noqa: E402

# Every shipped graph must be bindable on these, or Bob cannot drive it at all.
REQUIRED_TITLES = ("BOB_OUT",)


def workflows():
    return sorted(f for f in os.listdir(comfy.WORKFLOW_DIR) if f.endswith(".json"))


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--url", default=None)
    ap.add_argument("--object-info", help="a cached /object_info dump, instead of the server")
    ap.add_argument("--dump-object-info", help="write the server's /object_info here and exit")
    args = ap.parse_args()

    if args.dump_object_info:
        with open(args.dump_object_info, "w") as fh:
            json.dump(comfy.object_info(args.url), fh)
        print(f"wrote {args.dump_object_info}")
        return 0

    if args.object_info:
        with open(args.object_info) as fh:
            info = json.load(fh)
        source = args.object_info
    else:
        ok, detail = comfy.reachable(args.url)
        if not ok:
            print(f"[SKIP] no ComfyUI server ({detail})")
            print("    pass --object-info a dump to check the graphs offline")
            return 0
        info = comfy.object_info(args.url)
        source = comfy.base_url(args.url)

    print(f"{len(info)} node classes from {source}")
    failed = 0
    for name in workflows():
        try:
            prompt, prov = comfy.load_workflow(name)
        except comfy.ComfyError as exc:
            print(f"[FAIL] {name}: {exc}")
            failed += 1
            continue
        problems = comfy.preflight(prompt, info=info, required_titles=REQUIRED_TITLES,
                                   runtime_inputs=prov.get("runtime_inputs") or ())
        titles = sorted(t for t in comfy.titles(prompt) if t and t.startswith("BOB_"))
        if problems:
            failed += 1
            print(f"[FAIL] {name} ({len(prompt)} nodes)")
            for p in problems:
                print(f"         {p}")
        else:
            print(f"[PASS] {name} ({len(prompt)} nodes, {len(titles)} BOB_* titles)")

    print()
    print(f"{len(workflows()) - failed}/{len(workflows())} shipped workflows would queue")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
