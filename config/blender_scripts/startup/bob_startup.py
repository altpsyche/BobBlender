"""Optional studio startup script.

Add `config/blender_scripts` under Blender → Preferences → File Paths →
Scripts. Blender runs everything in the `startup/` subfolder on launch.

Keep this light — it's for studio-wide conveniences, not the `bob` extension
(which installs separately). Good candidates: default color-management, unit
setup, a console banner confirming the repo is wired up.
"""

import bpy
from bpy.app.handlers import persistent


@persistent
def _bob_new_file_defaults(_dummy):
    """Nudge sensible defaults onto every freshly-loaded file.

    Deliberately conservative — extend to taste.
    """
    scene = bpy.context.scene
    if scene is None:
        return
    # Example convention: Filmic/AgX view transform is usually what we want for
    # lookdev. Guard in case the name differs across builds.
    try:
        scene.view_settings.view_transform = "AgX"
    except (TypeError, AttributeError):
        pass


def register():
    if _bob_new_file_defaults not in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.append(_bob_new_file_defaults)
    print("[Bob] startup script loaded.")


def unregister():
    if _bob_new_file_defaults in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.remove(_bob_new_file_defaults)


# Blender calls register() for scripts in startup/ automatically.
register()
