# Authoring-model review: config-vs-scene ownership across the suite

Status: review / analysis, 2026-07-21. Not a settled spec. Written to feed a planning pass
on unification and UX. It is self-contained: a new chat can act on it without prior context.
All claims are grounded in the code paths cited (file:line).

## How to use this

The suite feels torn between "describe a scene with config/manifests and auto-build it" and
"take control of the scene you already have." This document shows that the split is real,
mostly principled, and concentrated in a few concrete seams. Use it to decide, per subsystem,
whether to UNIFY behaviour or just DOCUMENT the rule, and to design UX that makes ownership
legible. Sections 5-7 are the actionable parts.

## 1. TL;DR

- The tension is not one axis but TWO orthogonal ones: WHERE work runs (headless/reproducible
  vs live session) and WHO owns the truth after an artist edits (the config/spec vs the scene
  object). Conflating them is what makes the system feel undecided.
- There IS a consistent, sound design spine, it is just never named: config is a seed;
  the scene object owns the truth after an edit; rebuilds snapshot-and-restore tuned values;
  explicit switches (`reset`, `live_env`) flip ownership back to the config.
- Roughly 80% of the confusion is that the spine is undocumented and its vocabulary
  ("structural" vs "live", who-wins-on-rebuild) is not uniform across panels. The other 20%
  is a small number of features that genuinely violate the spine (Section 6).

## 2. The two axes (a 2x2, not a spectrum)

- Axis A, execution: headless -> file (`tools/bobtools/mcp/executor.py:21`, reproducible) vs
  live -> open session (`tools/bobtools/mcp/bridge.py:15`). This is the documented "one core,
  swappable executors" (`docs/ARCHITECTURE.md:34-55`); both return the same `BuildResult`.
- Axis B, ownership after edit: the config/spec is truth, or the scene object is truth.

Placing each subsystem in the grid explains why no single answer fits:

|                          | Config-owned (spec is truth)                                              | Scene-owned (the edit is truth)                                                    |
|--------------------------|---------------------------------------------------------------------------|------------------------------------------------------------------------------------|
| Headless / reproducible  | venv terrain BAKE (params/stack -> PNG, hash-cache, sidecar, golden); `build` -> .blend; `create_project` | (none: headless builds are ops-owned by definition)                                |
| Live / in-session        | world/env with `live_env` ON (drivers project `bbt_env` every eval)       | Scatter, Shaders, terrain MESH, P4 stack, `build_live`, world/env with `live_env` OFF |

Most interactive features live bottom-right (scene-owned). The venv bake is the deliberate
top-left exception (agents, caching and tests need reproducibility). The environment straddles
the bottom row through a user toggle.

## 3. The implicit spine (the rule the system mostly follows)

> Config/spec (presets, manifests, params, recipes, ops) is a GENERATOR/SEED. Once content
> exists in the scene, the SCENE OBJECT is the home of truth. A rebuild snapshots the artist's
> tuned live values and restores them by name afterward; an explicit switch (`reset=True`,
> `live_env` off) reverts ownership to the config.

Mechanisms that implement it:

- GN rebuild snapshot/restore by socket NAME, skipping structural inputs: `_snapshot_knobs` /
  `_restore_knobs` in `blender/bbmcp/geonodes/__init__.py:52-96`; `_STRUCTURAL = {Size,
  Resolution}` at `:43` (topology always takes the recipe's value).
- The ownership switch is in the op vocabulary itself: `BuildGeoNodes.reset` "rebuild in place
  but discard tuned knobs, reapply params" (`tools/bobtools/mcp/contracts.py:33`).
- Shader analogue: `_build_wrapper` snapshots and restores the Master group inputs across a
  structural rebuild (`blender/bbmcp/materials.py:966-1004`).
- Environment makes Axis B a visible toggle: `bbt_world.live_env`
  (`blender/extensions/bob_blender_tools/world_panel.py:124`).

Where the spine holds cleanly: Scatter (purest), Shaders, terrain mesh, world/env, and the MCP
`reset` flag.

## 4. Per-subsystem map

Hand-off = the point where a declarative input becomes a scene mutation. Truth-after-edit =
who wins when the artist has tuned something and the thing is rebuilt/re-applied.

| Subsystem | Declarative / config side | Hand-off point | Truth after edit | Quadrant / notes |
|-----------|---------------------------|----------------|------------------|------------------|
| MCP headless `build` | `ops` list + optional `base_file` | `executor.run_build` spawns headless Blender -> writes .blend (`executor.py:21-66`) | The ops (re-run reproduces the file) | Headless / config-owned |
| MCP `build_live` | same `ops` vocabulary | `bridge.run_build_live` sends ops to the session (`bridge.py:15-48`) | The scene (ops mutate it, no record kept) | Live / scene-owned |
| Projects | `projects/_template` skeleton | `scaffold.create_project` copytree | The project folder / .blend after | File scaffold only, no scene semantics |
| Terrain bake (venv) | preset stack + Relief/Detail/Erosion/Warp/Seed -> resolved stack; `params`-hash cache + sidecar + golden | `pipeline.bake` -> PNG (+ `_flow`/`_wetness`) | The config (PNG is a derived, reproducible artifact) | Headless / config-owned. Pure function, no bpy |
| Terrain mesh + shade | the PNG; `heightmap_terrain` recipe; global-knob preset table | bake operator builds `heightmap_terrain`, stores `bbt_heightmap`/`bbt_terrain_size` on the object; shade via Shaders | The scene object (GN knobs snapshot-preserved; `_STRUCTURAL` size/res from config) | Live / scene-owned. This feature SPANS both quadrants: authority flips at the PNG |
| Terrain global knobs (panel) | `presets.json` "presets" block | `_apply_preset` setattr onto the property group (`__init__.py:68-74`) | Config wins on re-pick: one-way push, no back-sync, "no custom entry" (`__init__.py:44-49`) | Caveat: enum `update` fires only on CHANGE, so re-picking the same preset does not overwrite |
| Terrain P4 stack editor | preset raw stack (`presets.json` "stacks") loaded to edit | `use_custom_stack` + `ops` collection; bake sends the edited stack | The scene (the edited op collection); a hidden `raw` field preserves non-surfaced params | Separate axis from the global knobs; preserved across preset picks |
| Shaders (BobShaders) | master group inputs; texture-set + biome-terrain specs | `new_bobshader` on the ACTIVE slot (`materials.py:1116`); auto-config only on FRESH create | The material node state (inputs snapshot-restored on rebuild) | Identity is native/active-slot (UX redesign, phases 2-3) |
| Scatter | `BBT_ScatterLayer` structural fields (kind/assets/align/vgroup) on the layer OBJECT (`scatter_panel.py:258`) | Build press -> `build_geonodes` with structural-only `_build_params` (`:229-245`) | The scene object: live knobs live only on the modifier and are snapshot-restored; recipe only SEEDS at first Add | Purest expression of the spine; no parallel config record ("no panel-vs-modifier drift") |
| World / Env | `Scene.bbt_env` (owned by Firmament, `env.py:37`); subscriber registry `apply_all` (`world_panel.py:36-57`) | `live_env` ON: drivers project `bbt_env` into modifier inputs and the shared `S_EnvState` node group (`materials.py:585-590,685-727`; `shaders_panel.py:339-362`) | Conditional: ON -> `bbt_env` is truth (drivers clobber manual edits every eval); OFF -> drivers removed, values freeze, hand-tuning wins | The only subsystem that makes Axis B a user-visible switch. Presets write the env FIELD not the socket so they read right in both modes |
| Biome / manifest | `library/models/<name>/manifest.json` v2 (meta/models/terrain/scatter/world), `assets.biome_manifest:114-150`, `validate_biome:183-250` | Apply Biome orchestrator chains import -> terrain -> scatter -> world (`world_panel.py:193-275`) | MIXED per section, see Section 6 | A one-shot generator, not a persistent source of truth |
| Presets (generation) | venv `presets.STACKS`/`DISPLAY` -> `gen_panel_presets.py` -> committed `presets.json` (presets + stacks) | drift test `test_panel_presets_json_in_sync` guards it | The venv is the single source of truth; the JSON is a generated mirror across the interpreter boundary | Not an ownership issue; a duplication/drift seam |

## 5. Naming the split for the UX (glossary to standardize)

The suite already uses two words inconsistently. Pin them and use them everywhere:

- STRUCTURAL: changes the graph/topology; applied only on an explicit Build (never a property
  callback, to avoid rebuild re-entrancy). Examples: Scatter assets/align/mask, terrain
  Size/Resolution.
- LIVE: edited directly on the modifier/material input; instant, no rebuild.
- SEED vs OWN: a config value that only SEEDS at creation (then the scene owns it) vs a config
  value the scene keeps deferring to (config owns). Today this distinction is implicit and the
  main source of surprise.

## 6. The seams (where the spine breaks) - prioritized

1. Biome-apply is internally contradictory (HIGH). Under one "Apply Biome":
   - Biome Scatter PRESERVES tuned layer knobs (snapshot/restore; reuses layers by kind, so
     idempotent) - scene wins (`scatter_panel.py:366-437`, esp. reuse at `:405-411`).
   - Biome Terrain CLOBBERS per-layer knobs back to the layer preset: the operator
     unconditionally re-pushes `_set_layer` for every layer (`shaders_panel.py:828-840`).
     Material identity and the drainage/flow-map wiring survive (get-or-create), but tuned
     layer knobs do not.
   - Biome World is a straight `setattr` overwrite (`world_panel.py:163-168`).
   Same umbrella, three different ownership rules. This is the clearest genuine inconsistency.

2. Terrain spans two quadrants in one workflow (MEDIUM). Config-owned bake (reproducible PNG)
   hands off to a scene-owned mesh you shade live; authority flips at the PNG. Within the panel,
   the global-knob preset pick is config-wins with no "customized" lock (`__init__.py:68-74`),
   while the resulting mesh/shader inputs are scene-wins. Two rules, one panel.

3. Preset re-pick silently overwrites tweaks (MEDIUM, UX). `_apply_preset` is a one-way push;
   there is deliberately no "custom" entry, so a re-pick discards hand-tuning with no warning
   and no visible "you have unsaved tweaks" state.

4. `build` vs `build_live` share a vocabulary but not a persistence model (LOW, by design).
   Headless bakes a reproducible file from the ops; live mutates the session and keeps no record
   of the ops. Worth documenting so users know live work is not reproducible from a spec.

5. `presets.json` duplicates venv config across the interpreter boundary (LOW). Guarded by a
   drift test, but still a maintenance seam and a place where "which is truth" can confuse.

## 7. Verdict and candidate work

Split roughly 80% documentation, 20% unification.

A. Document (dissolves most of the felt confusion):
   - Write the spine (Section 3) and the glossary (Section 5) into a short `AUTHORING-MODEL.md`,
     and add a one-line "who owns this after you edit it" note to each panel's docs.
   - State per subsystem: what is STRUCTURAL vs LIVE, and whether config SEEDS or OWNS.

B. Unify (genuine defects):
   - Make Biome Terrain preserve tuned layer knobs the way Biome Scatter does (snapshot/restore,
     or only push knobs to layers the artist has not touched). This is the top fix; it removes the
     worst inconsistency (`shaders_panel.py:828-840`).
   - Give preset re-pick a "customized" signal: detect divergence from the preset's neutral
     values and either lock (require an explicit Reset) or warn before overwriting
     (`__init__.py:68-74`). Mirror the explicitness of `live_env` / `use_custom_stack`.

C. UX to make ownership legible (design work for the planning chat):
   - A consistent visual language for STRUCTURAL (Build to apply) vs LIVE (instant) already
     exists via `ui_helpers.structural_action`; apply it uniformly, including a visible Reset
     ("revert to config", i.e. the `reset=True` path) affordance wherever the scene owns truth.
   - Surface a per-feature "source of truth" hint (config vs your edits), especially on Terrain
     (bake config vs live mesh) and Biome (which sections re-apply vs preserve).
   - Consider one shared mental model in the UI: "pick a starting point (preset/biome), then
     take control; a Reset returns to the starting point." That is already the de-facto spine;
     the UX just needs to say so.

## 8. Open questions for the planning chat

- Should Biome Terrain match Biome Scatter (preserve tuned knobs), or is a biome re-apply
  intended to be a hard reset for terrain layers? (Pick one and make it consistent.)
- Should the terrain global-knob preset support an explicit "custom/customized" state, or is
  silent overwrite acceptable if a Reset button exists?
- Should `build_live` optionally record its ops (so live work can be replayed/reproduced), or
  stay intentionally ephemeral?
- Is the venv bake's config-owned/reproducible model worth extending to more of the pipeline
  (e.g. a scene-level manifest that can rebuild a whole look), or should manifests stay one-shot
  generators that seed a then-scene-owned result?
- Does the environment's explicit `live_env` toggle model generalize? Should Terrain/Shaders/
  Scatter each expose an equivalent "live vs frozen/owned" switch, or is per-subsystem behaviour
  fine as long as it is documented?

## 9. Key files index

- MCP core: `tools/bobtools/mcp/{mcp_server,executor,bridge,contracts}.py`; `tools/bobtools/scaffold.py`.
- Terrain engine (venv): `tools/bobtools/heightfields/{pipeline,params,presets,engine,maps,cache}.py`.
- GN build + snapshot: `blender/bbmcp/geonodes/__init__.py` (`_snapshot_knobs`/`_restore_knobs`, `_STRUCTURAL`).
- Recipes: `blender/bbmcp/geonodes/recipes/{heightmap_terrain,scatter}.py`.
- Materials/shaders: `blender/bbmcp/materials.py` (`terrain_master_group`, `_build_wrapper`,
  `new_bobshader`, `terrain_material`, `weather_group`, `env_state_group`, `texture_set_group`).
- Environment: `blender/bbmcp/env.py`; `blender/extensions/bob_blender_tools/world_panel.py`.
- Panels: `blender/extensions/bob_blender_tools/{__init__.py (Terrain), shaders_panel.py,
  scatter_panel.py, firmament_panel.py, world_panel.py, ui_helpers.py}`.
- Biome/manifest: `blender/bbmcp/assets.py`; manifests under `library/models/<name>/manifest.json`.
- Presets bridge: `tools/scripts/gen_panel_presets.py` -> `blender/extensions/bob_blender_tools/presets.json`;
  drift test in `tools/tests/test_heightfields.py`.
- Prior design docs: `docs/ARCHITECTURE.md`, `docs/SYSTEMS.md`, `docs/UX-REDESIGN.md`,
  `docs/UNIFIED-SYSTEM.md`, `docs/SHADERS.md`, `docs/TERRAIN.md`, `docs/BIOME-SYSTEM.md`,
  `docs/SCATTER-SHADING-UX.md`.
</content>
