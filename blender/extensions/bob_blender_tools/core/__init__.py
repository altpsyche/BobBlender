"""core: the bpy-side authoring library. Runs inside Blender's Python.

This is where the code that builds things lives (meshes, geometry nodes,
materials). Reused by the headless MCP executor and the BobBlenderTools live
bridge extension. No MCP or UI code here, only builders that take a validated
op dict and mutate the current Blender scene.

A namespaced subpackage of the extension (bob_blender_tools.core), so it never
collides on sys.path with other bob_* tools; every intra-package import is
relative, so it loads identically under the live bl_ext.* namespace and the
headless bob_blender_tools.* name.
"""
