"""Bob Blender MCP — enable this addon to let MCP author into your live Blender.

One-time enable (Preferences → Add-ons). With autostart on, the socket bridge
comes up whenever Blender launches — no scripts to paste. The N-panel (View3D →
sidebar → BobMCP) has Start/Stop and a Reload-builders button.
"""

import bpy
from bpy.props import BoolProperty
from bpy.types import AddonPreferences, Operator, Panel

from . import server


# ── Preferences ───────────────────────────────────────────────────────────
class BBMCP_AddonPreferences(AddonPreferences):
    bl_idname = __package__

    autostart: BoolProperty(
        name="Start bridge on launch",
        description="Automatically start the live MCP bridge when Blender opens",
        default=True,
    )

    def draw(self, context):
        col = self.layout.column()
        col.prop(self, "autostart")
        col.label(text=f"Bridge: {server.status()}", icon="LINKED")


def _prefs():
    return bpy.context.preferences.addons[__package__].preferences


# ── Operators ─────────────────────────────────────────────────────────────
class BBMCP_OT_start(Operator):
    bl_idname = "bob_blender_mcp.start"
    bl_label = "Start Bridge"
    bl_description = "Start the live MCP bridge socket"

    def execute(self, context):
        self.report({"INFO"}, server.start())
        return {"FINISHED"}


class BBMCP_OT_stop(Operator):
    bl_idname = "bob_blender_mcp.stop"
    bl_label = "Stop Bridge"
    bl_description = "Stop the live MCP bridge socket"

    def execute(self, context):
        self.report({"INFO"}, server.stop())
        return {"FINISHED"}


class BBMCP_OT_reload(Operator):
    bl_idname = "bob_blender_mcp.reload_builders"
    bl_label = "Reload Builders"
    bl_description = "Refresh bbmcp so new op code is picked up without restarting"

    def execute(self, context):
        self.report({"INFO"}, server.reload_builders())
        return {"FINISHED"}


# ── Panel ─────────────────────────────────────────────────────────────────
class BBMCP_PT_panel(Panel):
    bl_label = "Bob Blender MCP"
    bl_idname = "BBMCP_PT_panel"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "BobMCP"

    def draw(self, context):
        layout = self.layout
        running = server.is_running()
        layout.label(
            text=server.status(),
            icon="PROP_ON" if running else "PROP_OFF",
        )
        row = layout.row(align=True)
        row.operator("bob_blender_mcp.start", icon="PLAY", text="Start")
        row.operator("bob_blender_mcp.stop", icon="PAUSE", text="Stop")
        layout.operator("bob_blender_mcp.reload_builders", icon="FILE_REFRESH")


_CLASSES = (
    BBMCP_AddonPreferences,
    BBMCP_OT_start,
    BBMCP_OT_stop,
    BBMCP_OT_reload,
    BBMCP_PT_panel,
)


def _autostart():
    try:
        if _prefs().autostart and not server.is_running():
            server.start()
    except Exception as exc:  # never let autostart break registration
        print(f"[bob_blender_mcp] autostart skipped: {exc}")
    return None  # one-shot timer


def register():
    for cls in _CLASSES:
        bpy.utils.register_class(cls)
    # Defer autostart until prefs are available.
    bpy.app.timers.register(_autostart, first_interval=0.2)


def unregister():
    server.stop()
    for cls in reversed(_CLASSES):
        bpy.utils.unregister_class(cls)
