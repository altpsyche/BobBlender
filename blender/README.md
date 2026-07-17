# blender/

Code that runs inside Blender's Python. Everything here imports `bpy` and runs
in Blender's bundled interpreter, never in `tools/.venv`. See
`docs/ARCHITECTURE.md` for the two-worlds design.

| Path | What it is |
|------|-----------|
| `bbmcp/` | The authoring library. Builders turn a validated op dict into meshes, geometry nodes, or materials. Reused by the headless executor and the live bridge. Named `bbmcp`, not `bob_build`, to avoid sys.path clashes with other bob tools. |
| `runners/` | Entry scripts launched by Blender. `headless_build.py` reads a request, applies ops via `bbmcp`, saves the `.blend`, and writes a result JSON. |
| `extensions/bob_blender_mcp/` | The live bridge as a Blender extension (id `bob_blender_mcp`, "Bob Blender MCP"). A managed socket server with start/stop/status, autostart, and reload, that applies ops to the open session. Dev-installed by `bob-setup`. |

## Two ways it is invoked

Headless (batch, reproducible), via the MCP `build` tool. `executor.py` spawns a
fresh Blender:
```
blender --background --factory-startup --python blender/runners/headless_build.py \
        -- <request.json> <result.json>
```

Live (into the open Blender), via the MCP `build_live` tool. `bridge.py` connects
to the extension's socket and ops run on Blender's main thread.

Both the runner and the extension put `bbmcp` on `sys.path`, so nothing needs to
be installed into Blender.

## Adding a builder

1. Add a function in `bbmcp/<area>.py` that takes an op dict and returns a result
   dict.
2. Register it in `bbmcp/dispatch.py`.
3. Add the matching op model in `tools/bobtools/contracts.py`.

The contract (validated in the venv) and the builder (Blender-side) are the only
two things that change when the vocabulary grows.
