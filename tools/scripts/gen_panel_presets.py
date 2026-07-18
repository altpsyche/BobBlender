"""Generate the extension's panel preset table from the venv PRESET_KNOBS.

The Heightfield panel is a Blender extension running in a different interpreter, so
it cannot import the venv. It reads a committed JSON of the generation knobs it
exposes; this script is the one place those values come from
(bobtools.heightfields.presets.PRESET_KNOBS). Rerun it when presets change and
commit the updated presets.json. A drift test (tools/tests) fails if the committed
file is stale.

The panel adds its own display knobs (height, sea level) on top; those are
Blender-side displacement params, not heightfield-generation params, so they live
in the panel, not here.

Run: uv run --extra terrain --project tools python tools/scripts/gen_panel_presets.py
"""

import json
import pathlib

from bobtools.heightfields import params, presets

# The generation knobs the panel exposes as sliders. seed is left to the user (the
# randomize button), so presets do not pin it.
PANEL_KNOBS = ("octaves", "ridged", "detail_strength", "droplets", "erosion",
               "deposition", "radius", "max_steps", "thermal_iters", "edge_falloff")

OUT = (pathlib.Path(__file__).resolve().parents[2]
       / "blender" / "extensions" / "bob_blender_tools" / "presets.json")


def build_panel_presets() -> dict:
    """The panel-exposed generation knobs for each preset, defaults filled in."""
    defaults = params.default_knobs()
    out = {}
    for name in presets.PRESETS:
        resolved = {**defaults, **presets.knobs(name)}
        out[name] = {k: resolved[k] for k in PANEL_KNOBS}
    return out


def main():
    data = {
        "_note": "Generated from bobtools.heightfields.presets by "
                 "tools/scripts/gen_panel_presets.py. Do not edit by hand.",
        "presets": build_panel_presets(),
    }
    OUT.write_text(json.dumps(data, indent=2) + "\n")
    print(f"wrote {OUT} ({len(data['presets'])} presets)")


if __name__ == "__main__":
    main()
