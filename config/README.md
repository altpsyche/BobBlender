# config/ — studio conventions Blender loads

Blender stores preferences in its own user config, not in a repo. This folder
holds the parts you *can* version and share.

## Startup scripts

`blender_scripts/startup/` — register the parent folder under
*Preferences → File Paths → Scripts*. Blender auto-runs every `.py` in the
`startup/` subfolder on launch. `bob_startup.py` sets a couple of file defaults
(edit to taste).

> This is separate from the `bob` extension in `tools/` — startup scripts are
> for lightweight, always-on studio defaults; the extension is for real tools.

## Good things to add here later

- `keymaps/` — exported keymap presets.
- A `themes/` preset.
- Default color management / OCIO config references.
