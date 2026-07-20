"""Generate the extension's panel preset table from the venv PRESET_KNOBS.

The Heightfield panel is a Blender extension running in a different interpreter, so
it cannot import the venv. It reads a committed JSON of the generation knobs it
exposes; this script is the one place those values come from
(bobtools.heightfields.presets.PRESET_KNOBS). Rerun it when presets change and
commit the updated presets.json. A drift test (tools/tests) fails if the committed
file is stale.

Each preset is a filter stack in the venv; the panel does not need the stack, only
the slider values to load when a preset is picked: the four global knobs (reset to
their neutral 0.5, so the preset shows its authored look) plus the Blender-side
display knobs (height, sea level) that suit each family. seed is left to the user
(the randomize button), so presets do not pin it.

Run: uv run --extra terrain --project tools python tools/scripts/gen_panel_presets.py
"""

import json
import pathlib

from bobtools.heightfields import params, presets

# The global knobs the panel resets to neutral when a preset is chosen.
PANEL_KNOBS = ("relief", "detail", "erosion", "warp")

OUT = (pathlib.Path(__file__).resolve().parents[2]
       / "blender" / "extensions" / "bob_blender_tools" / "presets.json")


def build_panel_presets() -> dict:
    """The slider values the panel loads per preset: neutral global knobs + display."""
    defaults = params.default_knobs()
    out = {}
    for name in presets.PRESETS:
        row = {k: defaults[k] for k in PANEL_KNOBS}
        row.update(presets.display(name))  # height, sea_level
        out[name] = row
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
