# Handover: final UX review of the BobBlenderTools N-panel

Paste-ready brief for a fresh chat. Plain house style (no em-dashes, no emojis).

## Your task

Do a final, deep UX review of the whole BobBlenderTools suite (the N-panel in Blender's 3D view
sidebar, category "BobBlenderTools"). Three lenses, in order:

1. Panel + tool UX (readability). Is each panel readable at a glance? Does it convey the important
   information (what am I acting on, what is live vs structural, what will a press do)? Are there
   extra buttons, redundant controls, or duplicate labels that can be removed WITHOUT losing a
   feature? This is a subtraction pass, not a feature pass.
2. The flow of the whole tool. Does the panel order and cross-panel handoff teach the intended
   sequence (World mood -> Biome quick-build OR the manual Terrain -> Paths -> Scatter -> Shaders
   pipeline -> Atmosphere)? Where does an artist get stuck, backtrack, or not know the next step?
3. Deep UX audit. Everything else: naming, grouping, empty states, captions, icon consistency,
   preset idioms, staged-vs-instant behaviour, mode toggles, defaults, discoverability. Rank
   findings by severity and give a concrete fix for each.

Deliver: a ranked findings list (severity, panel:location, the problem, the concrete fix). Separate
"remove/merge" (subtraction) from "add/clarify". Do NOT implement yet; propose, then let Siva pick.

## Rules (hard)

- Do not eyeball code to judge behaviour. VERIFY headless. Blender 5.2 is at
  `~/.steam/steam/steamapps/common/Blender/blender --background --factory-startup --python <script>`.
  Put `blender/` and `blender/extensions/` on sys.path, `import bob_blender_tools; register()`, then
  drive operators and MEASURE (socket names, modifier presence, env field values, node groups). A
  permissive panel-draw smoke that calls each Panel.draw with a stub self/layout across toggle states
  (Firmament on/off, live_env on/off, empty vs populated scene) catches draw-time breakage and shows
  what each panel actually renders in each state. Build one; there is a working pattern in prior
  session scratchpads (draw_smoke.py).
- Plain house style in any doc/code you write: no em-dashes, no emojis, no flowery phrasing.
- Branch is `fix/audit-remediation`. The blockout-biome + World-look redesign just landed there and
  is UNCOMMITTED (working tree dirty). Do not commit unless Siva asks. Read the current tree, not
  git history, for current state.

## Current state (what you are reviewing)

Panels and pipeline order (bl_order), all in the "BobBlenderTools" sidebar tab:
- 0 World (world_panel.py): masters (Quality expand, Live Environment toggle); "World now" box
  (time/place + live conditions weather/temperature/wetness/snow/cloud_cover/wind); Season (dropdown
  + Apply Season, the sole seasonal lever); Sky Look (a STAGED atmosphere preset).
- 1 Biome (world_panel.py, BBT_PT_biome, DEFAULT_CLOSED): staged Build Biome (bbt_world.biome +
  Build Biome button + Weather-scattered-assets checkbox) and Biome-world-only (staged).
- 2 Terrain (__init__.py, BBT_PT_heightfield, DEFAULT_CLOSED): target, staged landscape Preset,
  backend, Bake + Build.
- 3 Paths (splines_panel.py).
- 4 Scatter (scatter_panel.py): emitter/camera, Make Proxies, layer list, Biome Scatter, Build All,
  plus Active Layer / Masks / Camera Cull sub-panels.
- 5 Shaders (shaders_panel.py): New/Convert/Select identity, Surface / Terrain / Water / Weather /
  Snow Shell sub-panels.
- 6 Atmosphere (firmament_panel.py): Sky / Clouds / Fog / Weather sub-panels + Build Sky.
- 7 Advanced (__init__.py, BBT_PT_panel, DEFAULT_CLOSED): MCP bridge + Reload Builders.

Shared UX idioms (ui_helpers.py), check they are used consistently and not bypassed:
- context_header(layout, label, value, icon, empty): the "what am I acting on" line + empty-state hint.
- structural_action(layout, op, text, note): a build/rebuild button with the FILE_REFRESH marker and
  a muted "rebuilds: ..." caption. The marker means "this is structural, not a live knob".
- preset_row(layout, op, ...): the instant preset idiom (operator_menu_enum, fires on pick).
- staged_preset_row(layout, data, prop, op, text, note, apply_text): NEW stage-then-Apply idiom
  (dropdown stages the choice, a separate Apply button commits). Used by Sky Look, the Terrain
  landscape preset, Build Biome, Biome World.
- seed_row(...): value + reshuffle (RNDCURVE icon).

Live vs structural model (P3): live conditions are driven (weather/wetness/snow/cloud/wind, and the
sun from time/place via an env geo-hook); structural actions rebuild datablocks and carry the marker
icon + note. env.weather is LIVE (drives every BobShader's ground wetness), not inert.

What just changed (read docs/BIOME-BLOCKOUT-REDESIGN.md for the full account, it is DONE):
- verdant_trail biome and the whole real-glTF import path removed; the canonical biome is the
  procedural block-out (proxies + solid terrain, no external files).
- The jpg image texture-set feature was removed (terrain + surface are solid tint; procedural code
  like erosion/flow/wetness/snow/macro-breakup kept).
- World "look" split into three layers (World now / Season / Sky Look); Sky Look no longer touches
  season/snow/wetness/temperature. Build-a-biome moved to the new Biome panel.
- time_of_day / latitude / longitude are now LIVE (reposition the sun on edit, once a sky is built).

## Watch for (likely findings, verify before asserting)

- The World panel is dense: masters + a big "World now" box (7 conditions + time/place) + Season +
  Sky Look. Is the live-vs-staged split legible? Too many sliders visible at once?
- Two preset idioms now coexist: instant preset_row (cloud/fog/surface/layer/stack presets) and
  staged staged_preset_row (Sky Look, Terrain, Biome). Is that inconsistency confusing, or is the
  heavy-vs-light distinction clear enough? Should more presets be staged, or fewer?
- The Biome panel and the Scatter panel's "Biome Scatter" + "Make Proxies", and the Shaders panel's
  "Biome Terrain", overlap conceptually (biome vs manual). Is the relationship clear? Redundant entry
  points?
- Build Sky lives in Atmosphere but the sun inputs live in World; the caption points across panels.
  Acceptable, or should Build Sky be reachable from World?
- Empty states and gating: many panels early-return or grey out (no emitter, Firmament off, no
  biome). Are the hints good? Any dead-end with no next-step hint?
- Sub-panel depth in Scatter and Shaders: how many clicks to a common task? Any button that only
  duplicates a menu already present?

## Guiding preference (from Siva)

Simplify by SUBTRACTING redundancy (repeated buttons, duplicate labels, hand-rolled idioms that
should route through ui_helpers), never by cutting features. Merge duplicate operators. See the
[[ui-subtraction-preference]] and [[bobtools-ux-redesign]] memories.

## Context to read first

- docs/BIOME-BLOCKOUT-REDESIGN.md (just-landed redesign, DONE).
- docs/UX-REDESIGN.md (the prior whole-suite redesign; the P1-P7 principles and the panel IA).
- docs/UX-AUDIT.md (the earlier N-panel audit, buckets A-H, all landed).
- docs/SPLINES.md section 5 (Paths placement), docs/SHADERS.md, docs/BIOME-SYSTEM.md (superseded
  header explains what is gone).
- Memory index: [[bobtools-ux-redesign]], [[ui-subtraction-preference]], [[biome-blockout-redesign]],
  [[blender-headless-testing]].
