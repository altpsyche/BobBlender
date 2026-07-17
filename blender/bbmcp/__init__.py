"""bbmcp — the bpy-side authoring library (runs inside Blender's Python).

The one place that knows HOW to build things (meshes, geometry nodes,
materials). Reused by the headless MCP executor and the Bob Blender MCP live
bridge extension. No MCP, no UI code here — just builders that take a validated
op dict and mutate the current Blender scene.

Named `bbmcp` (not `bob_build`) so it never collides on sys.path with other
`bob_*` tools like bob's-assembly.
"""
