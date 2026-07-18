"""BobBlenderMCP: the framework and bus of BobBlenderTools.

This subpackage is the seam agents author through. It holds the op vocabulary
(contracts), the executors that apply ops (executor for headless Blender, bridge
for the live session), and the MCP server that exposes them as tools. Compute
capabilities ride over this bus and stay extract-ready: HeightFields is a pure
venv subpackage (bobtools.heightfields), Scatter is Blender-side builders. The
only things crossing a process line are JSON and files.

Kept separable for a later polyrepo split into a standalone BobBlenderMCP repo.
"""
