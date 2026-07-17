# Architecture

This repo is a central home for procedural Blender work, plus BobBlenderMCP, a
pipeline that lets an agent author Blender data over MCP. Target: Blender 5.2 LTS.

## Repo layout

- `projects/`: one folder per piece, started from `projects/_template/`.
- `library/`: reusable geometry-node and material node groups, registered as a
  Blender Asset Library. Agent output can land in `library/_generated/`
  (gitignored) and be appended into hand-edited files via the Asset Browser.
- `tools/`: a Python project (`bobtools`) that runs in its own venv, not inside
  Blender. Holds the MCP server, the executors, repo utilities, and the ComfyUI
  client.
- `blender/`: code that runs inside Blender's Python. `bbmcp/` is the authoring
  library, `runners/` are headless entry scripts, `extensions/bob_blender_mcp/`
  is the live bridge as an installable extension.
- `renders/`: outputs, gitignored.
- `config/`, `docs/`, `references/`.

Add-ons and extensions that are general tools (for example bob's-assembly) live
in their own repos and are dev-installed into Blender by symlink. They are not
vendored here.

## Two Python worlds

`bpy` only runs inside Blender's bundled interpreter (Python 3.13). The MCP
server and executors run in the `tools/.venv` (Python 3.14). They never import
each other; they communicate with JSON. Shared settings live in `bob.toml`,
which both interpreters read.

## BobBlenderMCP: one core, swappable executors

```
contracts (Pydantic ops, validated in the venv)
      |  JSON
      v
tools/bobtools (venv, no bpy)          blender/ (bpy, no mcp)
  contracts.py   op vocabulary           bbmcp/      builders
  executor.py    headless executor  -->  runners/    headless entry
  bridge.py      live executor           extensions/bob_blender_mcp  live bridge
  mcp_server.py  MCP tools
```

- The op vocabulary (`contracts.py`) is validated where agent input enters, so
  the Blender side trusts clean JSON and needs no extra deps.
- The builders (`blender/bbmcp/`) are the single place that knows how to build
  meshes, geometry nodes, and materials. Reused by both executors.
- The executor is swappable. `executor.py` spawns headless Blender for
  reproducible builds; `bridge.py` targets the open session for live work. Both
  present the same shape, so adding one did not change anything upstream.

To grow the vocabulary: add an op model in `contracts.py`, a builder in
`blender/bbmcp/`, and one line in `blender/bbmcp/dispatch.py`. Geometry-node
builders are recipes registered in `blender/bbmcp/geonodes.py`.

## The live bridge extension

`blender/extensions/bob_blender_mcp/` is a Blender extension that runs a local
socket server. It applies ops on Blender's main thread through a timer, which is
the only safe way to mutate `bpy` from a socket, and it runs only whitelisted
`bbmcp` ops. It has start/stop/status, autostart on launch, a Reload Builders
button (needed because Blender caches imports, so new builder code requires a
purge), and a clean stop so it can restart. Dev-installed by `bob-setup`.

## Naming

Everything is branded bob. To avoid Blender namespace collisions with other
bob tools, this pipeline uses unique names: extension id `bob_blender_mcp`,
operators `bob_blender_mcp.*`, classes `BBMCP_*`, N-panel tab `BobMCP`, the
sys.path module `bbmcp` (not `bob_build`), and the MCP server `bobblendermcp`.
Rule: anything that lands on Blender's global sys.path gets a collision-proof
name.

## Version control

Git plus Git-LFS. `.blend`, textures, and volumes go through LFS
(`.gitattributes`). Renders and `_generated/` are gitignored as regenerable.

## Repo boundary (kept extract-ready)

BobBlenderMCP stays in this monorepo for now. The framework (contracts,
executor, bridge, extension, dispatch) is kept separable from art-specific
builders so it can later be split into a standalone repo with `git subtree` or
published as a package. Extract when the op vocabulary stabilises, or when a
second project or a public release needs it.

## Daily use

1. Enable the Bob Blender MCP addon once, with autostart on.
2. Open a Claude Code session in this repo and approve the `bobblendermcp` MCP
   server (declared in `.mcp.json`).
3. Ask an agent to build. Results appear in the open viewport (`build_live`) or
   in a headless `.blend` (`build`).

Multiple agents each run their own MCP server process and connect to the one
bridge; the main-thread job queue serialises execution. If a shared broker is
ever needed, switch the MCP transport to HTTP.
