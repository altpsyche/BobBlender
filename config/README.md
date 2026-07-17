# config/

Blender stores its preferences in its own user config, not in a repo. This
folder holds the parts you can version and share.

Nothing is required here yet. Good things to add when you want them:

- Startup scripts. Create `blender_scripts/startup/` and register the parent
  folder under Preferences > File Paths > Scripts. Blender runs every `.py` in
  the `startup/` subfolder on launch. Use it for lightweight, always-on studio
  defaults, not real tools (those belong in an extension).
- Exported keymap presets under `keymaps/`.
- A theme preset.
- Color management or OCIO config references.
