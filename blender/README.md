# blender/

Code that runs inside Blender's Python. Everything here imports `bpy` and runs
in Blender's bundled interpreter, never in `tools/.venv`. See
`docs/ARCHITECTURE.md` for the two-worlds design, and `docs/SYSTEMS.md` for the
recipes and every tunable parameter.

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

## Adding a top-level op

1. Add a function in `bbmcp/<area>.py` that takes an op dict and returns a result
   dict.
2. Register it in `bbmcp/dispatch.py`.
3. Add the matching op model in `tools/bobtools/contracts.py`.

## Adding a geometry-node recipe

Recipes live under `bbmcp/geonodes/`, split into layers:

- `scaffold.py`: group plumbing (`new_group`, `add_input`).
- `blocks.py`: composable sub-graphs (`grid_source`, `noise_field`, `displace_z`, ...).
- `recipes/`: one file per recipe, each a `build(ng, out, params)` decorated with
  `@recipe("name")`.
- `place.py`: object or library placement, so recipes never touch objects.

To add one: drop a file in `recipes/`, compose it from blocks, decorate it, and
add it to the import line in `recipes/__init__.py`. No other file changes, since
`build`/`build_live` are generic over the `recipe` param.
