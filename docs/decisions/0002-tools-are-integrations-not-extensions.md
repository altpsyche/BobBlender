# 0002 — `tools/` is an integrations hub, not an extension incubator

**Date:** 2026-07-17
**Status:** Accepted (supersedes the `tools/bob` part of 0001)

## Context

0001 scaffolded `tools/bob` as an in-repo Blender extension meant to "graduate"
to its own repo later. In practice that's redundant:

- Blender loads extensions from its own directory, so a copy in this repo is
  never actually *installed* — you dev-install by symlink regardless.
- Once you're symlinking, a tool can live in its own repo from day one.
- The real extension (**bob's-assembly**) already exists as its own repo.

Meanwhile there's a genuine, different need: external Python that *orchestrates*
— MCP servers, a ComfyUI bridge, batch/headless workflows. These want a normal
venv with pip dependencies (`mcp`, `httpx`, `websockets`) and must **not** run
inside Blender's bundled Python.

## Decisions

1. **Deleted `tools/bob`** (the in-repo extension skeleton). Interactive
   Blender-UI operators (promote-to-library, set-render-output) belong in the
   **bob's-assembly** extension repo.

2. **`tools/` is now an integrations & automation hub** — one installable
   Python project (`bobtools`) run from its own venv. Homes the MCP server,
   the ComfyUI client, and batch workflows.

3. **Extensions are dev-installed by symlink**, never vendored/submoduled here.
   (See the discussion that led here: a submodule only makes sense as a
   "clone-once bundle" + symlink bootstrap, which isn't worth the coupling.)

## Consequences

- Pure helpers from the deleted skeleton (naming, repo-root discovery) were
  preserved in `bobtools/naming.py` and `bobtools/config.py`.
- Two Python worlds now, on purpose: Blender's bundled interpreter (extensions,
  startup scripts) vs. the `tools/.venv` (integrations). Keep code in the right
  one.
