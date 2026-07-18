# BobBlender

A central, growing home for procedural Blender work: geometry nodes, material
systems, small tools, and rendering. The focus is beautiful worlds, mathematical
artwork, and procedural systems.

Add-ons and extensions that are general tools live in their own repos. This repo
holds projects, the reusable library, in-house tools, and conventions.

## Layout

| Path | What lives here |
|------|-----------------|
| `projects/` | One self-contained folder per piece. Start from `projects/_template/`. |
| `library/` | Reusable geometry-node and material node groups (marked as assets), HDRIs, textures. Registered as a Blender Asset Library. |
| `tools/` | Python project in its own venv: MCP server, ComfyUI client, batch workflows. Not Blender addons. |
| `blender/` | Code that runs inside Blender's Python: the authoring library, headless runners, and the live bridge extension. |
| `renders/` | Final outputs. Gitignored. |
| `references/` | Mood boards, math papers, shader references. |
| `config/` | Studio config Blender can load. |
| `docs/` | Architecture and conventions. |

## First-time setup

1. Git-LFS (tracks `.blend` and texture binaries per `.gitattributes`):
   ```sh
   git lfs install
   ```

2. Register the Asset Library. In Blender, open Preferences > File Paths >
   Asset Libraries > Add, and point it at this repo's `library/` folder. Every
   node group marked as an asset then shows up in the Asset Browser across all
   projects.

3. Install extensions. General tools like bob's-assembly live in their own repos
   and install into Blender. For active development, dev-install by symlinking
   the checkout into Blender's extensions dir.

4. Set up the tools and MCP pipeline (one command, cross-OS):
   ```sh
   uv run --project tools bob-setup
   ```
   This syncs the `tools/` venv, dev-installs the Bob Blender MCP extension, and
   prints a checklist. Then:
   - In Blender, open Preferences > Add-ons and enable Bob Blender MCP (autostart
     on).
   - Open a Claude Code session in this repo and approve the `bobblendermcp` MCP
     server (declared in `.mcp.json`).
   - Ask an agent to build. It lands in your live viewport (`build_live`) or a
     headless `.blend` (`build`). See `tools/README.md`.

## Starting a new project

```sh
cp -r projects/_template projects/<my-piece>
```

Then follow `projects/_template/README.md`. Naming, output paths, and where
files go are in `docs/CONVENTIONS.md`. The overall design is in
`docs/ARCHITECTURE.md`, and the terrain, erosion, and scatter systems with all
their parameters are in `docs/SYSTEMS.md`.

## Target

Blender 5.2 LTS.
