# Final UX review: BobBlenderTools N-panel

Ranked findings from a deep UX review of the whole BobBlenderTools suite (the "BobBlenderTools"
sidebar tab in the 3D view). Three lenses: panel readability, whole-tool flow, deep audit.

Method: verified headless, not eyeballed. Blender 5.2 registered the addon, then a recording
draw harness captured what every panel and sub-panel actually renders across four states (empty
scene, populated with a Terrain mesh + emitter, live_env off, firmament off), and operators were
driven to render the populated deep panels (a Surface / Terrain / Water BobShader, a scatter layer,
a dirt-path curve, a river curve). Findings tagged [measured] were seen in that rendered output.
Do not implement yet; this is the propose step.

## How to read this

- Each finding: severity, panel:location, the problem, a concrete fix.
- Findings are split into REMOVE / MERGE (subtraction, per Siva's guiding preference) and
  ADD / CLARIFY. A severity-ranked master list is first so you can pick top-down.
- Severity: HIGH (an artist gets stuck, misreads state, or the first screen is overwhelming),
  MEDIUM (friction or inconsistency a regular user hits), LOW (polish, cleanup, dead code).

## Ranked master list

1. HIGH  World panel is over-dense on first open (A1)
2. HIGH  Biome authoring has four entry points; "Biome Terrain" is drawn twice (S1, F1)
3. HIGH  Atmosphere opens to an empty header and Build Sky is buried under 10 sky knobs (A5, F2)
4. MED   Biome dropdowns default to empty, so the first Build Biome does nothing (S2)
5. MED   Terrain Layers always draws 6 slots and offers two ways to disable a layer (A3, S3-layers)
6. MED   The same terrain mesh is called three different things across panels (S4)
7. MED   Terrain shader sub-panel polls on a different material than its siblings (C1)
8. MED   Staged-vs-instant preset split is applied against its own stated rule (A6)
9. MED   Water shader is 23 flat knobs with no collapsible grouping (A2)
10. MED  "Make Proxies" duplicates work the layer/biome builders already do (S5)
11. MED  "New BobShader" silently acts as Convert; Convert appears in up to four spots (A7)
12. LOW  Hand-rolled row idioms bypass ui_helpers (S6)
13. LOW  Duplicate structural icon, duplicate captions, dead bl_label (S7, S8, S9)
14. LOW  Dead-end empty states with no next step (A4)
15. LOW  The firmament-off branch in World is unreachable in the shipped addon (A8)

---

## REMOVE / MERGE (subtraction)

### S1 HIGH  "Biome Terrain" is drawn in two places in the Shaders panel
Shaders panel: root `_draw_new_shader` row, and again inside the Terrain Layers sub-panel.
[measured] both render the identical `operator_menu_enum bob_blender_tools.shaders_biome_terrain`
with text "Biome Terrain" and the FILE_REFRESH icon.
Fix: keep it in one home only. Terrain Layers is the honest home (it authors a terrain layer
stack); remove the copy from the New-shader row. The New row should offer New only.

### F1 HIGH  Four entry points to biome authoring, relationship unclear
[measured] biome work is reachable from: Biome panel (Build Biome = terrain + scatter + world;
plus Biome World only), Scatter panel ("Biome Scatter"), and Shaders panel ("Biome Terrain", twice).
Build Biome already calls `scatter_biome_scatter` and `shaders_biome_terrain` internally, so the
per-panel buttons are its own sub-steps surfaced again beside it. Nothing tells the artist that
Biome = do-all and the others are the pieces.
Fix (merge + clarify, do not cut the pieces): make the Biome panel the one front door and label
the per-panel buttons as the partial path. Concretely: under Scatter's "Biome Scatter" and the
Shaders Terrain "Biome Terrain", add the muted caption idiom already used elsewhere, e.g.
"one biome layer; the Biome panel builds the whole scene". Remove the duplicate Biome Terrain
(S1). Consider renaming the per-panel ones to "Biome layers" / "Biome terrain only" so "Build
Biome" reads as the superset.

### S2 MEDIUM  Biome dropdowns default to empty; the first press is a no-op
[measured] `biome=''` and `biome_world=''` on first open (Blender logs "current value '0' matches
no enum"); the dropdowns render blank. Build Biome then reports "No biome to build" and Set Biome
World reports "No biome carries a world block". By contrast the Terrain preset defaults to the
first item ('alpine') [measured], so the staged idiom is applied two different ways.
Fix: default the `biome`, `biome_world`, and Scatter `biome` enums to their first real item, the
way the Terrain preset already does. The staged idiom still holds (you still press Apply), but the
control no longer looks broken and empty.

### S3-layers MEDIUM  Terrain Layers offers two ways to disable a layer
[measured] the Terrain Layers stack always draws six rows (Layer 0..5), each with a
CHECKBOX_HLT/DEHLT toggle operator, and below the list an Add Layer / Remove Layer pair.
`shaders_terrain_remove` disables the active slot; the per-row toggle disables any slot. Two
controls, one outcome, plus six always-present rows when typically one or two are used.
Fix: drop the Add/Remove pair and keep the per-row checkboxes as the single enable model (or the
reverse). If a fixed six-slot stack is intended, say so with a one-line caption; if not, draw only
the enabled slots plus one "add" affordance.

### S4 MEDIUM  The same mesh is named three ways across panels
[measured] context_header labels the active/emitter mesh as "Terrain object" (Biome, Terrain),
"Scatter" (Scatter), and "Active mesh" (Shaders) for what is literally the same object.
Fix: pick one noun for the thing you act on. "Active mesh" is the most honest suite-wide; use it
everywhere the header names a mesh. This is a vocabulary subtraction, not a feature change.

### S5 MEDIUM  "Make Proxies" duplicates the builders' own setup
[measured] the Scatter root shows a standalone "Make Proxies" button under an "Assets (shared,
for layers to use)" label. Per the scatter source, both `scatter_add` and `scatter_biome_scatter`
call make_proxies themselves. So the button only pre-creates what adding a layer would create
anyway.
Fix: remove the standalone button and its "Assets (shared...)" label from the root; let adding a
layer or a biome create the proxies. If a proxy-only path is still wanted for power users, move it
to the Advanced panel rather than the first thing on Scatter.

### S6 LOW  Hand-rolled row idioms bypass ui_helpers
Several near-identical rows are hand-rolled instead of routing through a shared helper, which is
exactly the redundancy the ui-subtraction preference targets:
- Radio-select rows repeated three times in Shaders (material-slot list, scatter-asset list,
  terrain-layer rows) and again in Scatter. No `select_row` helper exists; add one.
- `firmament_wind_from_env` inline in clouds, fog, rain, motes (four copies) and
  `firmament_snow_from_env` a fifth, each with the same `_env_owned_note`-or-button branch.
  Add one `wind_from_env_row` / `from_env_row` helper.
- `curve_revert_erode` (Paths) hand-rolls the STRUCTURAL_ICON on a bare operator while its sibling
  `curve_bake_erode` uses `structural_action`. Route revert through `structural_action(enabled=...)`
  so both structural buttons read the same.
Fix: add the two helpers to ui_helpers.py and route these call sites through them. No behaviour
change, less drift.

### S7 LOW  Duplicate structural icon in one box
[measured] in the Scatter layer box and the Paths active box, the caption
"Structural (Build to apply)" carries FILE_REFRESH and the structural_action button right below it
carries FILE_REFRESH too, so the same icon shows twice a few pixels apart.
Fix: drop the icon from the caption label; the button's marker is enough.

### S8 LOW  Dead bl_label on the Build Biome operator
world_panel.py sets `bl_label = "Apply Biome"` then immediately `bl_label = "Build Biome"` on
`BBT_OT_world_apply_biome`; the first line is dead. [measured] the button renders "Build Biome".
Fix: delete the dead first assignment.

### S9 LOW  "Firmament off" weather note renders twice
The `_env_note` "Firmament off: no live weather" line draws on the Shaders root panel and again in
the Weather sub-panel, so a user with both open sees it twice.
Fix: keep it on the Weather sub-panel only (where the weathering knobs live).

---

## ADD / CLARIFY

### A1 HIGH  World panel is over-dense on first open
[measured] the "World now" box alone draws time_of_day, a year/month/day row, utc_offset,
latitude, longitude, a caption, weather, a caption, temperature, wetness, snow, cloud_cover,
wind_direction, wind_strength: about thirteen controls and two captions, all visible at once.
Above it sit Quality and Live Environment; below it sit the Season box and the Sky Look box. The
top panel of the suite is a wall of sliders, and the live-vs-structural split (the boxes) is hard
to see under the volume.
Fix: separate "set once" geo inputs from "drive live" conditions. Move time_of_day + date +
utc_offset + latitude + longitude into a DEFAULT_CLOSED "Time and place" sub-panel (they are set
once when you place the scene). Leave the six live conditions (weather, temperature, wetness, snow,
cloud_cover, wind) in the open box as the day-to-day knobs. Season and Sky Look stay as the two
structural boxes. That turns the first screen into: masters, six live sliders, two Apply boxes.

### A5 HIGH  Atmosphere opens to nothing, and Build Sky is buried
[measured] the Atmosphere root panel (BBT_PT_firmament) draws literally nothing (its draw is a
pass). Opening it shows an empty header, then the Sky sub-panel (open by default) with a Manual Sun
toggle and ten sky knobs (sun_strength, sun_angle, sun_disc, world_strength, sky_altitude, air,
ozone, turbidity, ground_albedo), and only at the very bottom the Build Sky button. Clouds/Fog/
Weather sit below, collapsed. So the primary action of the whole panel, Build Sky, is the last
thing on the first sub-panel, under ten knobs that mean nothing until you have built once.
Note: the Clouds/Fog/Rain/Motes/Snow sub-panels do the honest thing (a Build box at the top, then
knobs gated behind "built"). Sky does not follow that pattern; it shows all ten knobs unconditionally.
Fix: give the Atmosphere root a one-line context header and the Build Sky button at the top (the
same structural_action the sub-panels use), then let Sky follow the sibling idiom: Build box first,
the ten knobs gated until a sky exists. That makes the first-build path obvious and matches the
rest of the panel.

### F2 MEDIUM  Sun authoring is split across World and Atmosphere with only a caption to bridge
[measured] the geographic sun inputs (time/date/place) live in World; the sun look inputs
(strength/angle/disc, plus Manual Sun override) live in Atmosphere > Sky; Build Sky lives in
Atmosphere. World carries the caption "time and place drive the sun live once a sky is built
(Atmosphere > Build Sky)". This is a real cross-panel dependency for the single most common first
task (get a sky).
Fix: acceptable to keep the split, but make the handoff two-way. Add a Build Sky affordance
reachable from World (for example, when no sky exists, show Build Sky in the World "World now" box
next to the time caption), so the artist can set time and build from one place on the first pass.
The deep sky look knobs stay in Atmosphere.

### A3 MEDIUM  Terrain Layers density (see also S3-layers)
Beyond the two-ways-to-disable issue, the six always-drawn slots plus the global knobs plus a
per-layer surface block plus a Layer Masks sub-panel with six mask sections is a lot of surface for
one sub-panel.
Fix: after applying S3-layers (draw only active slots), keep the per-layer surface and the masks
where they are. Consider a one-line "Layer N of M enabled" caption so the artist knows the stack
depth without counting rows.

### C1 MEDIUM  Terrain shader sub-panel polls on a different material than its siblings
BBT_PT_shaders_terrain.poll keys off `_active_material` while Surface, Water, and Weather poll off
`_editing_material`. On a scatter object (where the editing target is a selected asset material,
not the object's own active slot) the Terrain sub-panel can appear or hide based on the wrong
material. This is a state-legibility bug, not just style.
Fix: make the Terrain sub-panel poll on `_editing_material` like its siblings, unless there is a
deliberate reason for the divergence (if so, comment it).

### A6 MEDIUM  Staged-vs-instant preset split is applied against its own rule
The stated rule (ui_helpers docstrings) is: staged (pick then Apply) for a heavy preset that
rebuilds subsystems; instant (fires on pick) for a light look. In practice:
- Terrain preset is STAGED [measured, Apply Preset] yet it only loads slider values (light,
  fully reversible).
- Cloud/Fog/Rain/Mote presets are INSTANT [measured] yet each builds the object if it is missing
  (a heavy side effect on first pick).
So the heavy ones fire instantly and a light one is staged: the opposite of the rule.
Fix: pick the axis and apply it once. Cleanest: reserve staging for the genuinely destructive
rebuilds (Sky Look, Build Biome, Biome World) and make the Terrain preset instant like the other
look presets; and stop the instant subsystem presets from silently building (require Build first,
or mark that picking a preset will build). Whichever way, document the one rule in ui_helpers and
follow it everywhere.

### A2 MEDIUM  Water shader is a long flat list
[measured] the Water sub-panel draws about 23 knobs in three labeled groups (Depth colour + optics,
Flow + foam, Freeze) with no collapsible structure, so it is a long scroll.
Fix: split into DEFAULT_CLOSED sub-panels (Optics, Flow and foam, Freeze) the way Firmament splits
its subsystems, or at least collapse Flow+foam and Freeze by default so the depth/optics look is
the default view.

### A7 MEDIUM  "New BobShader" silently converts, and Convert is everywhere
Per the shaders source, `shaders_new` converts a plain active slot in place rather than erroring, so
"New" sometimes does the job of "Convert". Meanwhile Convert is reachable from up to four spots
(per-row Convert, Convert all, the scope-dropdown Convert, and scatter "Convert assets"). The
New/Convert/Select identity is otherwise good (Select is well consolidated into one operator).
Fix: keep the consolidation but make New and Convert distinct: New only creates on an empty or
new slot; if the slot holds a plain material, show Convert there instead of silently doing it under
the New label. Collapse the multiple Convert entry points to the per-row Convert plus the scope
dropdown; drop "Convert all" if the scope dropdown already has an all option.

### A4 LOW  Dead-end empty states with no next step
Mostly good, a few gaps [measured / from source]:
- Shaders sub-panels `return` silently when the master node is missing, rendering a blank panel.
- Scatter Masks and Camera Cull repeat "No active layer" and "Not used for an along-curve layer"
  with no alternative offered.
- The Shaders Snow Shell and Firmament Snow Coverage cross-panel dependency (needs a coverage pass)
  surfaces only as a post-click WARNING, not inline before pressing.
Fix: give each blank/dead-end a one-line next step, matching the good ones already in place
("Set a Camera on the Scatter panel", "Shade it in the Shaders panel").

### A8 LOW  The firmament-off branch in World is unreachable
[measured] the World panel greys Quality + Live Environment and shows "Firmament off: world present
but no atmosphere" when `_env.get_env(scene)` is None. But firmament_panel.register() always
registers bbt_env at addon load, so in the shipped single addon this branch never fires (I had to
monkeypatch get_env to None to render it).
Fix: if this is defensive for the planned polyrepo split, add a comment saying so. If not, remove
the dead branch and its greying so the panel is simpler.

---

## Answers to the brief's open questions

- Live-vs-staged split legible on World? Not at first open: the volume of sliders (A1) drowns the
  box structure. Splitting the geo inputs out fixes most of it.
- Two preset idioms confusing? The heavy-vs-light idea is fine; the problem is the code does not
  follow it (A6). Fix the mapping, keep both idioms.
- Biome / Scatter / Shaders overlap? Real and the headline flow issue (F1, S1). Merge the front
  door, label the pieces, remove the duplicate.
- Build Sky reachable from World? Recommend yes for the first build (F2).
- Empty states / gating? Mostly good; a few dead-ends need a next-step line (A4).
- Sub-panel depth? Water (A2) and Terrain Layers (A3) are the two heavy ones; both improve with
  grouping / drawing only what is in use.

## What was checked and found fine

- Firmament sub-panel order is Sky, Clouds, Fog, Weather (Sky first); good, Sky is the primary.
- The Select operator (shaders_select) is well consolidated across slot/asset/layer via one enum.
- structural_action and its FILE_REFRESH marker are used consistently for real builds; the dev
  Reload Builders correctly does not borrow the marker.
- seed_row is used consistently (Terrain, Scatter, Firmament) with the RNDCURVE marker.
- Live-environment gating (greying wind/coverage knobs when Live Env is on, with the "turn it off
  on World to edit" note) is consistent across all Firmament subsystems.
