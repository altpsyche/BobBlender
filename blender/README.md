# blender/ — code that runs INSIDE Blender's Python

Everything here imports `bpy` and runs in Blender's bundled interpreter — never
in the `tools/.venv`. This is the other half of the two-worlds architecture
(see `docs/decisions/0003-*`).

| Path | What it is |
|------|-----------|
| `bbmcp/` | The DRY authoring library — builders that turn a validated op dict into meshes / geometry nodes / materials. Reused by the headless executor AND the live bridge. (Named `bbmcp`, not `bob_build`, to avoid sys.path clashes with other `bob_*` tools.) |
| `runners/` | Entry scripts launched by Blender (`blender --background --python …`). `headless_build.py` reads a request, applies ops via `bbmcp`, saves the .blend, writes a result JSON. |
| `extensions/bob_blender_mcp/` | The **live bridge** as a proper Blender extension (id `bob_blender_mcp`, "Bob Blender MCP"): a managed socket server (start/stop/status/autostart/reload) that applies ops to your *open* session. Dev-installed by `bob-setup`. |

## Two ways it's invoked

**Headless** (batch/reproducible) — the MCP `build` tool. `tools/bobtools/executor.py`
spawns a fresh Blender:
```
blender --background --factory-startup --python blender/runners/headless_build.py \
        -- <request.json> <result.json>
```

**Live** (into your open Blender) — the MCP `build_live` tool. `tools/bobtools/bridge.py`
connects to the `bob_blender_mcp` extension's socket; ops run on Blender's main thread.

`bbmcp` is put on `sys.path` by both the runner and the extension (no
install into Blender needed).

## Adding a builder

1. New function in `bbmcp/<area>.py` taking an op dict → result dict.
2. Register it in `bbmcp/dispatch.py`.
3. Add the matching op model in `tools/bobtools/contracts.py`.

The contract (external, validated) and the builder (Blender-side) are the only
two things that change when the vocabulary grows.
