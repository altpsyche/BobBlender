# 0003 — Agent-driven Blender authoring: one core, swappable executors

**Date:** 2026-07-17
**Status:** Accepted — vertical slice proven

## Context

Goal: let an agent (via MCP) *author* Blender data — meshes, geometry nodes,
materials — correctly, DRY, and futureproof so it grows with the repo. Hard
constraint: `bpy` only runs inside Blender's bundled Python (3.13), while the
MCP server runs in the external `tools/.venv` (3.14). The boundary between them
IS the architecture.

## Decision

Make the boundary a **data contract** and keep exactly one copy of each concern:

```
contracts (Pydantic ops)              ← ONE vocabulary, validated at the boundary
      │ JSON
      ▼
tools/bobtools (venv, no bpy)         blender/ (bpy, no mcp)
  contracts.py   op models              bob_build/   builders  ← ONE place that
  executor.py    spawn Blender  ──────▶ runners/     entry        knows HOW
  mcp_server.py  build() tool           headless_build.py
```

1. **`blender/bob_build/`** — the DRY authoring core. Builders take a validated
   op dict → mutate the scene → return a result dict. No MCP, no UI. Reused by
   the headless executor, the future live bridge, and bob's-assembly operators.
2. **`tools/bobtools/contracts.py`** — the op vocabulary as Pydantic models
   (`AddMesh`, then `BuildGeoNodes`, `MakeMaterial`, …). Validation happens here,
   where agent input enters; Blender-side trusts clean JSON (needs no deps).
3. **`tools/bobtools/executor.py`** — `run_build(request) -> BuildResult`.
   Today spawns `blender --background --python runners/headless_build.py`. A
   live-socket executor can later implement the same signature with zero changes
   upstream. **The executor is the swappable part.**
4. **Non-clobber principle** — agent output defaults to `library/_generated/`
   (gitignored, regenerable); the artist appends assets into their hand-edited
   `.blend` via the Asset Browser. Human and agent output never fight. Targeting
   a scene directly stays available via `base_file`/`output_file`.
5. **Blender resolution** — `config.blender_binary()`: `$BOB_BLENDER` → Steam
   install → PATH. No hardcoded paths.

## Why it's futureproof / DRY

- Growing the vocabulary = add one op model + one builder. Plumbing untouched.
- `bob_build` is written once, reused by every executor and by the extension.
- Two Python worlds stay cleanly separated; JSON is the only shared surface.

## Proven

Vertical slice `AddMesh` ran MCP-contract → executor → headless Blender 5.2 →
`bob_build` → saved `.blend`, verified by reopening the file (3 named meshes,
empty-scene start). See `tools/bobtools/`, `blender/`.

## Next

`BuildGeoNodes` (the real target) behind the same contract; then a live-socket
executor in bob's-assembly for interactive control of an open session.
