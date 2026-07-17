# BobBlender

A centralised, growing home for procedural Blender work — geometry nodes,
material systems, small tools, and rendering. Focused on **beautiful worlds,
mathematical artwork, and procedural systems**.

> Add-ons / extensions live in their own repos. This repo holds *projects*,
> the reusable *library*, in-house *tools*, and studio *conventions*.

## Layout

| Path            | What lives here |
|-----------------|-----------------|
| `projects/`     | One self-contained folder per piece. Start from `projects/_template/`. |
| `library/`      | The reusable core: geometry-node & material node groups (marked as assets), HDRIs, textures. Registered as a **Blender Asset Library**. |
| `tools/`        | Integrations & automation hub (own venv): MCP server, ComfyUI bridge, batch workflows. *Not* Blender addons. |
| `renders/`      | Final outputs. Gitignored (regenerable). |
| `references/`   | Mood boards, math papers, shader refs. |
| `config/`       | Startup scripts, keymaps, studio conventions Blender should load. |
| `docs/`         | The growing playbook + architecture decisions. |

## First-time setup

1. **Git-LFS** (handles `.blend` and texture binaries):
   ```sh
   git lfs install
   ```
   Tracking rules are already in `.gitattributes`.

2. **Register the Asset Library** — Blender → *Preferences → File Paths →
   Asset Libraries → Add* → point it at the `library/` folder of this repo.
   Now every geometry-node / material node group marked as an asset shows up
   in the Asset Browser across all projects.

3. **Install extensions** (e.g. *bob's-assembly*) — these live in their own
   repos and install into Blender. For active development, dev-install by
   symlinking the checkout into Blender's extensions dir.

4. **Set up the tools + MCP pipeline** (one command, cross-OS):
   ```sh
   uv run --project tools bob-setup
   ```
   This syncs the `tools/` venv, dev-installs the **Bob Bridge** extension into
   Blender, and prints a checklist. Then:
   - Blender → *Preferences → Add-ons* → enable **Bob Blender MCP** (autostart on).
   - Open a Claude Code session in this repo → approve the **bobblendermcp** MCP
     server (declared in `.mcp.json`).
   - Ask an agent to build — it lands in your live viewport (`build_live`) or a
     headless `.blend` (`build`). See `tools/README.md`.

5. *(optional)* **Studio startup scripts** — Blender → *Preferences → File
   Paths → Scripts* → add `config/blender_scripts`. See `config/README.md`.

## Starting a new project

```sh
cp -r projects/_template projects/<my-piece>
```

Then follow `projects/_template/README.md`. Conventions (naming, output paths,
what goes where) live in `docs/CONVENTIONS.md`.

## Target

Blender **5.2 LTS**.
