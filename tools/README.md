# tools/ — integrations & automation hub

External Python that **orchestrates** — it runs in its own venv (not inside
Blender) and talks to Blender, ComfyUI, and MCP clients.

> Interactive Blender UI tools do **not** live here. This
> folder is for things that need real pip dependencies and a normal Python
> environment.

## Setup

One command (cross-OS, creates the venv, dev-installs the Bob Bridge extension):
```sh
uv run --project tools bob-setup
```
Manual, if you prefer:
```sh
cd tools && uv venv .venv && uv pip install --python .venv -e '.[all]'
```
Shared config (bridge host/port) lives in `../bob.toml`; env vars override:
`BOB_BLENDER`, `BOB_REPO`, `BOB_BRIDGE_HOST`, `BOB_BRIDGE_PORT`.

## What's here

| Module | What it does | Deps |
|--------|--------------|------|
| `bobtools/config.py` | Repo root + cross-OS Blender detection + shared settings. | — |
| `bobtools/naming.py` | Shared naming conventions (slugs, render paths). | — |
| `bobtools/scaffold.py` | `bob-new-project <name>` — new project from the template. | — |
| `bobtools/setup.py` | `bob-setup` — dev-install the bridge extension + checklist. | — |
| `bobtools/contracts.py` | Pydantic op vocabulary, validated at the boundary. | `pydantic` |
| `bobtools/executor.py` | Headless executor — spawns Blender to build a `.blend`. | — |
| `bobtools/bridge.py` | Live executor — sends ops to the open Blender via socket. | — |
| `bobtools/mcp_server.py` | `bob-mcp` — MCP server exposing repo + build tools. | `mcp` |
| `bobtools/comfyui.py` | Thin ComfyUI API client (queue workflow → fetch outputs). | `httpx`, `websockets` |

## MCP server

Registered in `../.mcp.json` (portable `uv run` invocation). Tools:
`list_projects`, `list_library_assets`, `create_project`, `build` (headless),
`build_live` (into your open Blender via the Bob Blender MCP extension).

Adding a build op is DRY: op model in `contracts.py` + builder in
`../blender/bbmcp/` + one line in its `dispatch.py`. No new MCP tool needed
— `build`/`build_live` are generic over ops.

## ComfyUI

Export a graph from ComfyUI via **Save (API Format)**, then:
```python
from bobtools.comfyui import ComfyUIClient
with ComfyUIClient("http://127.0.0.1:8188") as c:
    wf = c.load_workflow("workflows/texture_gen.json")
    result = c.run(wf)          # queue → wait
```

## Adding a workflow

New module under `bobtools/`, reuse `config`/`naming`, add an entry point in
`pyproject.toml` if it's a CLI. If one grows big enough to stand alone, it can
graduate to its own repo — and this time that's honest, because it's not tied
to Blender's install model.
