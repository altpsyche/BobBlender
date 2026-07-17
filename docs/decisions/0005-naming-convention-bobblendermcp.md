# 0005 — Naming convention: BobBlenderMCP (avoid `bob_*` collisions)

**Date:** 2026-07-18
**Status:** Accepted — implemented & verified. Renames names used in 0003/0004.

## Context

Every tool in this ecosystem is branded `bob…` (bob's-assembly, etc.). Two
`bob_*` Blender extensions collide in several namespaces — not just cosmetically:

- **Extension id** (`bob_bridge`) → both install to `bl_ext.user_default.bob_bridge`; one shadows the other.
- **Operator namespace** (`bob_bridge.*`) → global clash.
- **Top-level `sys.path` module** (`bob_build`) → Python loads whichever is first; the other silently gets the wrong module. (The nastiest one.)

## Decision

Brand this pipeline **Bob Blender MCP** and make every namespace unique:

| Thing | Was | Now |
|---|---|---|
| Extension id / folder | `bob_bridge` | `bob_blender_mcp` |
| Extension display name | "Bob Bridge" | "Bob Blender MCP" |
| Operator namespace | `bob_bridge.*` | `bob_blender_mcp.*` |
| Blender class prefix | `BOBBRIDGE_*` | `BBMCP_*` |
| N-panel tab (`bl_category`) | `Bob` | `BobMCP` |
| bpy authoring lib (sys.path module) | `bob_build` | `bbmcp` |
| MCP server name (`.mcp.json`) | `bob` | `bobblendermcp` |

CLI entry points (`bob-mcp`, `bob-setup`, `bob-new-project`) keep their names —
they live in the venv and don't collide with Blender-side `bob_*` tools.

## Rule going forward

- Blender ids are lowercase `snake_case` (`bob_blender_mcp`); brand display uses
  "Bob Blender MCP".
- Anything that lands on Blender's global `sys.path` gets a collision-proof name
  (`bbmcp`), never a generic `bob_*`.
- New Blender-facing names must be unique across all your `bob_*` tools.

## Repo location (also decided)

Stay in the BobBlender monorepo for now, **extract-ready**: keep the framework
(contract, executor, bridge, extension, dispatch) separable from art-specific
builders so a later `git subtree split` into a standalone `BobBlenderMCP` repo is
mechanical. Extract when the op vocabulary has stabilised or it's needed
elsewhere / published.

## Verified

Renamed extension validates + enables (`bl_ext.user_default.bob_blender_mcp`);
headless build works through `bbmcp`; bridge lifecycle unchanged.
