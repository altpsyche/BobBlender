"""Generate the extension's panel preset table from the venv PRESET_KNOBS.

The Heightfield panel is a Blender extension running in a different interpreter, so
it cannot import the venv. It reads a committed JSON of the generation knobs it
exposes; this script is the one place those values come from
(heightfields.presets.PRESET_KNOBS, the single source in core/heightfields, reached
via bobtools._hfpath). Rerun it when presets change and
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

import bobtools._hfpath  # noqa: F401  (adds core/heightfields to sys.path)
from heightfields import params, presets

# The global knobs the panel resets to neutral when a preset is chosen.
PANEL_KNOBS = ("relief", "detail", "erosion", "warp")

OUT = (pathlib.Path(__file__).resolve().parents[2]
       / "blender" / "extensions" / "bob_blender_tools" / "presets.json")


def build_panel_presets() -> dict:
    """The slider values the panel loads per preset: neutral global knobs + display.

    Display carries `relief_ratio` (relief / tile width) and `sea_level`. The panel derives the
    metre Height from relief_ratio * terrain_size at apply/resize time (real-world scale, 1 unit =
    1 m). NOTE the ratio is emitted as `relief_ratio`, NOT `relief`, so it does not collide with the
    `relief` global knob the panel also loads."""
    defaults = params.default_knobs()
    out = {}
    for name in presets.PRESETS:
        row = {k: defaults[k] for k in PANEL_KNOBS}
        d = presets.display(name)
        row["relief_ratio"] = d["relief"]
        row["sea_level"] = d["sea_level"]
        out[name] = row
    return out


def build_panel_stacks() -> dict:
    """Each preset's raw op stack, so the stack editor can load one to edit
    without hopping to the venv. These are the neutral (as-authored) stacks; the
    global-knob modulation only applies to the preset+knobs bake path.

    Any op authored by a real repose ANGLE (`repose_deg`) is resolved here to a concrete talus at
    the full-bake reference resolution (params.DEFAULT_SIZE): the editor and its custom-stack bake
    take a plain talus, not an angle the engine op would reject, and it matches what a full bake
    produces. The preset+knobs path still resolves repose per bake resolution (params.build_params)."""
    out = {}
    for name in presets.PRESETS:
        stack = presets.stack(name)
        params._resolve_repose(stack, params.DEFAULT_SIZE, presets.relief(name))
        # An amplify op needs a concrete resolution target (not None) so the editor's custom-stack
        # bake climbs to the reference resolution instead of returning the coarse macro.
        params.resolve_amplify_targets(stack, params.DEFAULT_SIZE, presets.relief(name))
        out[name] = stack
    return out


def main():
    data = {
        "_note": "Generated from heightfields.presets (core/heightfields) by "
                 "tools/scripts/gen_panel_presets.py. Do not edit by hand.",
        "presets": build_panel_presets(),
        "stacks": build_panel_stacks(),
    }
    OUT.write_text(json.dumps(data, indent=2) + "\n")
    print(f"wrote {OUT} ({len(data['presets'])} presets, "
          f"{len(data['stacks'])} stacks)")


if __name__ == "__main__":
    main()
