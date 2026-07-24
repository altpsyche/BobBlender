# tools/

Python that orchestrates. It runs in its own venv, not inside Blender, and talks
to Blender, ComfyUI, and MCP clients. Interactive Blender UI code does not live
here; this folder is for code that needs pip dependencies and a normal Python
environment.

## Setup

One command (cross-OS: creates the venv, dev-installs the extension):
```sh
uv run --project tools bob-setup
```
Manual, if you prefer:
```sh
cd tools && uv venv .venv && uv pip install --python .venv -e '.[all]'
```
Shared config (bridge host and port) lives in `../bob.toml`. Env vars override
it: `BOB_BLENDER`, `BOB_REPO`, `BOB_BRIDGE_HOST`, `BOB_BRIDGE_PORT`.

## What's here

| Module | What it does | Deps |
|--------|--------------|------|
| `bobtools/config.py` | Repo root, cross-OS Blender detection, shared settings. | none |
| `bobtools/naming.py` | Naming helpers (slugs). | none |
| `bobtools/scaffold.py` | `bob-new-project <name>`, a new project from the template. | none |
| `bobtools/setup.py` | `bob-setup`, dev-installs the extension and prints a checklist. | none |
| `bobtools/mcp_launch.py` | `bob-mcp` dev launcher: runs the extension's `mcp_agent` server in-repo. | `mcp`, `pydantic` |
| `bobtools/heightfields/` | Terrain heightfield generation and erosion (CPU/GPU). Writes a heightmap PNG that `heightmap_terrain` displaces. See `../docs/SYSTEMS.md`. | `numpy`, `pillow`, `scipy`, optional `cupy` |
| `bobtools/comfyui.py` | ComfyUI API client (queue a workflow, fetch outputs). | `httpx`, `websockets` |

## MCP server

The server now ships INSIDE the extension (`../blender/extensions/bob_blender_tools/mcp_agent/`)
so it runs standalone with no repo — see [`../docs/MCP.md`](../docs/MCP.md) for the full
install/connect/use flow and the **Copy MCP Config** button. In-repo, `bob-mcp` (this package's
`mcp_launch.py`, registered in `../.mcp.json`) runs that same server. Tools: `list_projects`,
`list_library_assets`, `create_project`, `build` (headless), `build_live` (into the open Blender),
`bake_heightfield`.

Adding a build op stays small: an op model in the extension's `mcp_agent/contracts.py`, a builder
in `../blender/extensions/bob_blender_tools/core/`, and one line in its `dispatch.py`. No new MCP
tool is needed, since `build` and `build_live` are generic over ops.

## ComfyUI

Export a graph from ComfyUI with Save (API Format), then:
```python
from bobtools.comfyui import ComfyUIClient
with ComfyUIClient("http://127.0.0.1:8188") as c:
    wf = c.load_workflow("workflows/texture_gen.json")
    result = c.run(wf)  # queue, then wait
```

## Adding a workflow

Add a module under `bobtools/`, reuse `config` and `naming`, and add an entry
point in `pyproject.toml` if it is a CLI.
