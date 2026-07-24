"""Agent-side MCP server, shipped inside the BobBlenderTools extension.

Runs as a standalone stdio process (NOT inside Blender, no bpy): the agent client spawns it
to drive a running Blender over the live socket bridge, or headlessly by spawning Blender with
the extension's own runner. Launch via `mcp_agent/__main__.py` (see docs/MCP.md). Kept import-
light so importing the package never pulls in bpy or the addon's Blender-side code.
"""
