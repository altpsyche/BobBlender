"""bbmcp: the bpy-side authoring library. Runs inside Blender's Python.

This is where the code that builds things lives (meshes, geometry nodes,
materials). Reused by the headless MCP executor and the BobBlenderTools live
bridge extension. No MCP or UI code here, only builders that take a validated
op dict and mutate the current Blender scene.

Named bbmcp (not bob_build) so it never collides on sys.path with other bob_*
tools like bob's-assembly.
"""
