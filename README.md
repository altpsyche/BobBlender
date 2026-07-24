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
| `docs/` | Architecture, conventions, and per-system references. |

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
   This syncs the `tools/` venv, dev-installs the BobBlenderTools extension, and
   prints a checklist. Then:
   - In Blender, open Preferences > Add-ons and enable BobBlenderTools. The MCP
     bridge autostart is OFF by default (it is an agent-authoring feature); start it
     on demand from the Advanced panel, or turn on autostart in the add-on
     preferences for agent work.
   - Open a Claude Code session in this repo and approve the `bobblendermcp` MCP
     server (declared in `.mcp.json`).
   - Ask an agent to build. It lands in your live viewport (`build_live`) or a
     headless `.blend` (`build`). See `docs/MCP.md` and `tools/README.md`.

## Ship it (packaged install)

The extension folder is the whole product; build a distributable zip:

```sh
uv run --project tools python tools/scripts/build_extension.py            # -> dist/bob_blender_tools-<version>.zip
uv run --project tools python tools/scripts/build_extension.py --version 0.2.0   # stamp + build
```

The script validates the manifest (`blender --command extension validate`) then builds. The zip
is small (~260 KB): it ships the block-out pack only, no textures/`.blend`, and no scipy/CuPy.

Install (any user, no repo, no venv): Blender > Preferences > Get Extensions > Install from Disk,
pick the zip. Then:
- Add real art via Preferences > Add-ons > BobBlenderTools > Asset Pack Folders (or
  `$BOB_ASSET_PACKS`), and Rescan Asset Packs in the Advanced panel. The bundled block-out biome
  works with no packs.
- For terrain, click **Enable Compute** in the Terrain panel: it installs the compute (scipy, and
  the matching CuPy for an NVIDIA GPU) into Blender's own Python and verifies the GPU. `auto` then
  bakes on the GPU; machines with no GPU bake on CPU.
- To drive Blender from an agent (Claude Code or any MCP client) with no repo: Advanced panel →
  **Copy MCP Config**, paste into your client, connect. Full flow in `docs/MCP.md`.

Distribution channel: self-hosted zip for now (Install from Disk). Publishing to
extensions.blender.org is possible later (needs their review plus compatible tags/license).

## Starting a new project

```sh
cp -r projects/_template projects/<my-piece>
```

Then follow `projects/_template/README.md`. Naming, output paths, and where
files go are in `docs/CONVENTIONS.md`. The overall design is in
`docs/ARCHITECTURE.md`, and the recipe and parameter reference is in
`docs/SYSTEMS.md`. Each capability has its own doc: `docs/TERRAIN.md` (the
heightfield engine and presets), `docs/SHADERS.md` (BobShaders surfaces),
`docs/FIRMAMENT.md` (sky, clouds, fog, weather, snow), `docs/SPLINES.md` (typed
curves), and `docs/BIOME-SYSTEM.md` (whole-biome assembly).

## Target

Blender 5.2 LTS.
