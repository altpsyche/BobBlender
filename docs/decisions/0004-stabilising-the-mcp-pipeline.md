# 0004 — Stabilising the MCP pipeline (portability, UX, lifecycle)

**Date:** 2026-07-18
**Status:** Accepted — implemented & verified

## Context

The MCP → Blender pipeline (0003) was proven but fragile: hardcoded paths,
Linux-only Blender detection, a loose `live_server.py` with lifecycle bugs
(couldn't restart, couldn't reload builders, no stop/status), and a daily UX
that meant pasting a script into Blender every launch. Goal: a fresh clone that
"just works" on any OS, with low-friction daily use.

## Decisions

1. **Shared config in `bob.toml`.** Bridge host/port live in one file both the
   venv and Blender's Python read (a file is the DRY cross-interpreter surface,
   like the JSON build boundary). Env vars override: `BOB_BRIDGE_HOST/PORT`,
   `BOB_BLENDER`, `BOB_REPO`.

2. **Cross-OS Blender detection.** `config.blender_binary()` searches
   `$BOB_BLENDER` → per-OS install locations (Linux Steam/system/flatpak/snap,
   macOS `.app` + Steam, Windows Program Files + Steam, glob for versioned
   dirs) → PATH. No hardcoded paths.

3. **The bridge is now a Blender extension** (`blender/extensions/bob_bridge/`),
   dev-installed by symlink (`bob-setup`). It owns a real lifecycle:
   Start/Stop/status + **autostart on launch** (no more pasting scripts), a
   **Reload Builders** button (purges `bob_build` from `sys.modules` so new op
   code loads live), and a clean stop (socket `settimeout`+close) that makes
   **restart** work. Replaces the deleted loose `live_server.py`. It's infra
   glue for THIS repo (imports `bob_build`), not a general publishable addon —
   consistent with 0002's dev-install-by-symlink stance.

4. **Portable `.mcp.json`.** Uses `uv run --project ${CLAUDE_PROJECT_DIR:-.}/tools
   --extra mcp bob-mcp` — cross-OS (uv resolves `bin/` vs `Scripts\`), and
   `uv run` auto-creates/syncs the venv on first launch. No absolute paths.

5. **`bob-setup` bootstrap.** Dev-installs the extension into the detected
   Blender profile and prints a readiness checklist. Fresh clone:
   `uv run --project tools bob-setup`.

6. **Context-guarded builders.** `dispatch.apply_op` forces OBJECT mode before
   running an op, so ops are robust to whatever mode the user is in.

7. **Logging to stderr only** (stdout is the MCP stdio channel).

## Multi-agent note

stdio transport = one `bob-mcp` process per Claude session. Multiple agents →
multiple processes → all connect to the one bridge; the main-thread job queue
serialises execution, so ops are safe (Blender auto-dedupes name clashes).
There is no transactional intent and headless builds to the same file can race
— advise per-agent output files. Upgrade path if a shared broker is ever
needed: switch MCP transport to HTTP/streamable-http (one server, many clients).

## Verified (headless)

- Cross-OS config resolves Blender + port; `bob.toml` read on both sides.
- Manifest validates; extension enables cleanly (register runs).
- Headless build regression passes (new config + context guard).
- Bridge **restart** works (start → stop → start), the old bind bug gone.

Interactive re-prove (enable addon → `prove_live.py`) is the user's step.
