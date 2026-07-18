# Architecture

This repo is a central home for procedural Blender work, plus BobBlenderMCP, a
pipeline that lets an agent author Blender data over MCP. Target: Blender 5.2 LTS.

## Repo layout

- `projects/`: one folder per piece, started from `projects/_template/`.
- `library/`: reusable geometry-node and material node groups, registered as a
  Blender Asset Library. Agent output can land in `library/_generated/`
  (gitignored) and be appended into hand-edited files via the Asset Browser.
- `tools/`: a Python project (`bobtools`) that runs in its own venv, not inside
  Blender. `bobtools/mcp/` is the framework and bus (contracts, executors, bridge,
  MCP server); `bobtools/heightfields/` is the pure terrain-compute capability;
  repo utilities and the ComfyUI client sit alongside.
- `blender/`: code that runs inside Blender's Python. `bbmcp/` is the authoring
  library, `runners/` are headless entry scripts, `extensions/bob_blender_tools/`
  is the BobBlenderTools addon (MCP bridge, heightfield panel, next scatter).
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
tools/bobtools (venv, no bpy)             blender/ (bpy, no mcp)
  mcp/contracts.py    op vocabulary         bbmcp/      builders
  mcp/executor.py     headless executor -->  runners/    headless entry
  mcp/bridge.py       live executor          extensions/bob_blender_tools  the addon
  mcp/mcp_server.py   MCP tools
  heightfields/       terrain compute
```

- The op vocabulary (`contracts.py`) is validated where agent input enters, so
  the Blender side trusts clean JSON and needs no extra deps.
- The builders (`blender/bbmcp/`) are the single place that knows how to build
  meshes, geometry nodes, and materials. Reused by both executors.
- The executor is swappable. `executor.py` spawns headless Blender for
  reproducible builds; `bridge.py` targets the open session for live work. Both
  present the same shape, so adding one did not change anything upstream.

To grow the vocabulary: add an op model in `bobtools/mcp/contracts.py`, a builder
in `blender/bbmcp/`, and one line in `blender/bbmcp/dispatch.py`. Geometry-node
builders are recipes in `blender/bbmcp/geonodes/recipes/`.

## The BobBlenderTools extension

`blender/extensions/bob_blender_tools/` is the Bob suite's Blender-side host: one
addon, one `BobBlenderTools` N-panel tab, with the capabilities as sibling panels
(MCP Bridge, Heightfield Terrain, and next Scatter). MCP is one capability here,
not the roof.

Its MCP Bridge runs a local socket server, applying ops on Blender's main thread
through a timer (the only safe way to mutate `bpy` from a socket) and running only
whitelisted `bbmcp` ops. It has start/stop/status, autostart on launch, a Reload
Builders button (needed because Blender caches imports, so new builder code
requires a purge), and a clean stop so it can restart. Dev-installed by
`bob-setup`.

Two-sided reload for op changes. The builder code runs in Blender; the op
contract (`contracts.py`) runs in the long-running venv MCP server, which parses
it once at startup. So changing an existing recipe body needs only Reload
Builders, but adding or changing an op's contract also needs the MCP server
reconnected (`/mcp reconnect bobblendermcp`, or restart the CLI). Restarting
Blender alone does not reload the tools-side contract, `build_live` will reject
the new op tag until the server is reconnected.

## Naming

The suite is branded BobBlenderTools; MCP, HeightFields, and Scatter are
capabilities under it, not the roof. Names that denote the umbrella use the Tools
brand; names that denote the MCP capability keep MCP, because they genuinely are
the MCP bus.

- Umbrella (Blender side): extension id `bob_blender_tools`, operators
  `bob_blender_tools.*`, classes `BBT_*`, N-panel tab `BobBlenderTools`, scene
  props `bbt_*`.
- MCP capability (kept MCP, correctly): the MCP server `bobblendermcp` (in
  `.mcp.json`), the venv package `bobtools/mcp/`, and the live MCP Bridge panel.
- Builder library: the sys.path module `bbmcp` (not `bob_build`). It builds the
  whole suite (terrain, scatter, proxies, paths), so the MCP-flavoured name is a
  legacy misnomer kept for now; rename deferred to the polyrepo extraction.

Rule: anything that lands on Blender's global sys.path gets a collision-proof
name.

## Version control

Git plus Git-LFS. `.blend`, textures, and volumes go through LFS
(`.gitattributes`). Renders and `_generated/` are gitignored as regenerable.

## Repo boundary (kept extract-ready)

BobBlenderMCP stays in this monorepo for now. The framework (`bobtools/mcp/`:
contracts, executor, bridge, server, plus the extension and dispatch) is the bus;
compute capabilities (`bobtools/heightfields/`, later a scatter package) ride over
it and stay separable. Everything is kept extract-ready so a piece can later split
into its own repo (BobBlenderMCP / BobBlenderHeightFields / BobBlenderScatter under
a BobBlenderTools umbrella) with `git subtree`. Extract when the op vocabulary
stabilises, or when a second project or a public release needs it.

## Daily use

1. Enable the BobBlenderTools addon once, with autostart on.
2. Open a Claude Code session in this repo and approve the `bobblendermcp` MCP
   server (declared in `.mcp.json`).
3. Ask an agent to build. Results appear in the open viewport (`build_live`) or
   in a headless `.blend` (`build`).

Multiple agents each run their own MCP server process and connect to the one
bridge; the main-thread job queue serialises execution. If a shared broker is
ever needed, switch the MCP transport to HTTP.
