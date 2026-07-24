# BobBlenderTools suite UX redesign: problem statement and plan

Update (2026-07-22, docs/BIOME-BLOCKOUT-REDESIGN.md): the World "Set up a look" section below was
reworked. It is now three layers: World now (live conditions), Season (the one seasonal lever, Apply
Season), and Sky Look (a STAGED atmosphere preset that no longer touches season/snow/wetness/temp).
Biome World + Apply Biome moved out of the World panel into a new top-level Biome panel (Build Biome).
The "Import Real" asset control and its verdant_trail glTF set were removed (block-out proxies are the
one asset source). Both presets in the suite (Sky Look, heightfield landscape) now use the staged
`ui/helpers.staged_preset_row` idiom instead of instant apply.

Status: COMPLETE (design 2026-07-20; implemented 2026-07-20, phases 0-6). Scope: the driving
UX, flow, information architecture, and shared abstractions of the WHOLE BobBlenderTools N-panel
suite (MCP Bridge, Heightfield Terrain, Scatter, Firmament, Shaders). This is not a change to any
shading, geometry, solar, or erosion algorithm; the rendering is complete and correct. It is
about how the tools are driven. No shading regression: a fixed scene renders byte-identically to
the pre-redesign baseline (max/mean pixel delta 0.0).

Progress (see section 9 for the phase plan):
- Phase 0 DONE: shared UX helpers module (`ui/helpers.py`: context_header, structural_action,
  preset_row) and the panel reorder scaffold (bl_order sets the pipeline order; World=0 reserved).
  No behavioural change; verified headless (register + icon/idname audit).
- Phase 1 DONE: World panel (`ui/world.py`, bl_order 0) with World-now (live conditions) vs
  Set-up-a-look (Season/Apply Season/Scene Preset, structural), plus the Quality level and the
  ONE Live Environment master toggle (bbt_world). Firmament renamed to Atmosphere; its Environment
  sub-panel, Quality, live_env, and Scene Preset moved out. The two old per-panel live_env toggles
  (bbt_shaders.live_env, bbt_firmament.live_env) are folded into bbt_world.live_env via a SUBSCRIBER
  REGISTRY in ui/world: each consumer registers an applier fn(scene); a world change re-applies
  all. Adding a world-driven subsystem later is one register_applier() call; World never imports its
  consumers, so env.py stays the acyclic root. Verified: headless audit passes; a fixed scene driven
  through the new API matches the pre-redesign baseline exactly (state snapshot identical; render
  delta max 0.0 over 307200 channels). Baseline in library/_generated/ux_baseline/.
- Phases 2+3 DONE (Shaders identity pivot + contextual list, done together as they rework the same
  panel): materials.py gains master_type/is_bobshader (detect a BobShader by its Master group tree)
  and new_bobshader (auto-name M_<object> + assign). ui/shaders now edits the ACTIVE object's
  active material slot: the top panel lists every material slot of the active mesh (P1 header, P7
  empty state), each row select/status/adaptive New-or-Convert; a Batch-convert (scope active/
  selected/collection) keeps the one remaining collection picker for the unlinked scatter assets.
  Surface/Terrain/Weather sub-panels poll on master_type. Removed: material_name, target, master
  enum, asset_collection, Build&Assign, Assign-to-Active, Assign-to-Collection, Import Real Assets
  (-> Scatter in Phase 4), BobShade Assets (folded into Convert collection scope). Verified: audit
  passes; render still byte-identical to baseline; a functional test confirms detection, New,
  Convert (per-slot/all/scope), per-slot multi-material listing, select-slot, terrain ops, and the
  sub-panel polls all behave.
- Phase 4 DONE (Scatter asset home): context header (emitter / active layer, P1/P7); a dynamic
  asset-set enum + Import operator moved in from Shaders under a labelled "Assets (shared, for
  layers to use)" group next to Make Proxies, so the two read as siblings that fill the shared
  BOB_Assets_* collections; the explicit structural (assets/align/mask + Build, marked) vs
  live-knobs split in the Active Layer sub-panel. Verified: audit passes; a functional test
  imports the real verdant_trail set (trees:1, rocks:4, grass:5, plants:9) and Build All scatters
  213 instances.
- Phase 5 DONE (Terrain + Bridge polish): Heightfield panel renamed "Terrain" with a context
  header and a structurally-marked Bake+Build; its Material picker removed (decision D, shade via
  Shaders). MCP Bridge demoted to a collapsed "Advanced" panel (decision B). A one-line pipeline
  overview at the top of World (decision E). Verified: audit passes; cumulative render still
  byte-identical to baseline.
- Post-implementation feedback (2026-07-20): (a) stage numbers dropped from the panel labels
  (Terrain/Scatter/Shaders, not 1/2/3) - the World overview line carries the sequence, so
  decision E's discoverability is met without numbered labels; (b) the Scatter "Import Biome"
  control reframed to "Import Real" under an "Assets" group and made a pure populate of the shared
  BOB_Assets_* (no silent active-layer re-point/rebuild; layers already instancing a collection
  update live because populate reuses it), matching Make Proxies' scope.
- Bug fix (2026-07-20, "applying bob shaders removed the textures"): New BobShader used on a
  scatter layer object (no own material, textured via instances) ran assign_material, which adds a
  Set-Material modifier that OVERRODE every instance's textures with the fresh solid material.
  Fixed so New never destroys an existing look: if the active slot already holds a material it is
  Converted in place (textures kept); on a scatter object New refuses and points to Convert
  (Collection) - and the panel shows scatter objects a one-click "Convert <BOB_Assets_*>" button
  instead of New. Verified: New on a scatter object leaves the render unchanged (delta 0.0);
  Convert still preserves the asset textures.
- Feature (2026-07-20, "terrain should come with the right texture for the biome"): a biome
  manifest can now carry a `terrain` section (layer stack + a library texture set per layer). The
  Shaders "Biome Terrain" action (offered next to New for a fresh mesh, and by the Stack Preset in
  the Terrain sub-panel) builds a terrain material with that stack + textures in one pick.
  verdant_trail terrain = soil/grass/rock with the matching Poly Haven CC0 sets in
  library/textures/{soil,grass,rock}. Verified: builds 3 textured layers and renders textured;
  scatter Import still works (populate skips the non-list terrain section); render baseline
  unchanged.
- Phase 6 DONE (docs + verify): ARCHITECTURE.md (panel-UX section + reconciled statements),
  CONVENTIONS.md (Panel UX conventions), SYSTEMS.md (Terrain/quality/live_env references), and
  redesign pointers in SHADERS.md/FIRMAMENT.md. Full UX walkthrough (section 6) drives World ->
  Terrain -> Shaders (New terrain + alpine stack) -> Scatter (proxies + rocks, 981 instances) ->
  Shaders Convert (collection scope) -> Apply Season Winter (falling snow + coverage) -> Build
  Sky, all green, and renders a coherent snow scene (library/_generated/ux_phase6/).

## 1. Problem statement

The suite renders correctly but is confusing to operate, because it grew capability by
capability and each panel invented its own idioms. The confusion the user hit while working:

- You cannot tell what you are acting on. The Shaders panel has no showcase of the active
  material, no preview, and selecting a mesh changes nothing.
- Applying a material is unclear: several overlapping verbs and two competing ways to say
  which object.
- Apply Season is confusing: several overlapping ways to change the world, with the
  live-vs-structural distinction invisible.
- Asset import sits in the Shaders panel, where it does not belong.

These are symptoms of a suite-wide problem, not a Shaders-only one.

### Root causes (cross-cutting)

1. Parallel identity vs native selection (worst in Shaders). Shaders invented a
   `material_name` text field + `target` pointer that competes with Blender's active object ->
   active material slot. No panel consistently shows "what am I acting on."
2. No shared design language. "Build" means five different things (Build Terrain, Build This
   Layer, Build Sky/Clouds/Fog/Rain, Build & Assign). Presets, icons, section order, and
   empty-state hints are all ad hoc.
3. Live-vs-structural is invisible everywhere. Terrain bake, scatter assets/align, Firmament
   Apply Season, and Shader New/Convert are all STRUCTURAL (they build or rebuild a datablock);
   every panel also has LIVE knobs (instant). Nothing signals which is which. This is the
   single most recurring confusion.
4. Capability-boundary blur and misplaced concerns:
   - The world (`bbt_env`) is read by everything but buried in a Firmament sub-panel; Shaders
     carries its own `live_env` toggle and Scatter's Apply Season is really Firmament's.
   - Asset import lives in Shaders.
   - MCP Bridge (an agent/dev capability) sits in the artist N-panel.
   - Preview/Final Quality lives inside Firmament though it is scene-wide.
   - The Heightfield panel has its own Material picker, overlapping native Shaders assignment.
5. No information architecture. Panel order is registration order, not the pipeline; nothing
   surfaces the terrain -> scatter -> shade -> world sequence.

## 2. Decisions (locked with the user)

1. Selection model: NATIVE across the suite. Each panel reflects the active thing (active
   object/material for Shaders, active emitter/layer for Scatter, target object for Terrain,
   the scene for the world). A browser/library is a later, additive mode.
2. Contextual lists: selecting a mesh shows ALL of that mesh's materials in the Shaders panel
   and nothing else; editing follows the selected slot. The same "show only what is in scope"
   rule applies to the other panels.
3. Adaptive/minimal controls: show only what is needed for the current state (per-row New OR
   Convert, not both; hide sub-panels that do not apply).
4. Asset home: asset selection and import live in the SCATTER panel (a collection browser;
   Asset Browser aspirational). Converting a material to a BobShader stays in SHADERS.
5. Fold the Firmament world split (world now vs set up a look) into this redesign.

These decisions generalize into the suite-wide principles below.

## 3. Suite-wide design principles

Apply these consistently to every panel; they are the backbone of the redesign.

P1. Native context header. Every panel opens with a compact "what am I acting on" header
    derived from the active thing, not from panel-local state. No text-field identities, no
    duplicate target pointers.

P2. One verb vocabulary.
    - Build: create or rebuild a datablock from structural config (terrain, a scatter layer's
      graph, a sky, a cloud/fog/particulate object). Non-destructive; preserves tuned knobs.
    - Apply: change a KIND or do a one-shot structural action (Apply Season, apply a preset,
      Convert a material). 
    - Live knobs: instant, no press. Everything editable in place is a live knob.
    Rename operators/buttons to fit; do not use "Build" for a one-shot apply or vice versa.

P3. Visible live-vs-structural. In every panel, group live knobs separately from structural
    actions, and mark structural actions consistently (a shared label/icon and a short "rebuilds:
    ..." note). The user should always know whether a control is instant or needs a build.

P4. One preset idiom. All presets use the same control (an operator_menu_enum with label +
    description) and the same wording, whether terrain, scatter layer type, cloud, fog, surface,
    stack, or scene.

P5. Adaptive/minimal. Show the one action that fits the current state; hide inapplicable
    sub-panels and knobs (sockets that do not exist, features not enabled).

P6. Pipeline information architecture. Order and group panels along the actual workflow so the
    N-panel itself teaches the sequence (see section 4).

P7. Consistent empty states. Every panel, when nothing is in scope, says what to do next in one
    line ("Select a mesh...", "Pick an emitter...", "Build a terrain first...").

## 4. Information architecture (panel order and grouping)

Reorder the BobBlenderTools tab to follow the pipeline, with the world as an anchor and the dev
capability demoted:

    World          (the shared environment; read by everything)
    1 Terrain      (Heightfield Terrain)
    2 Scatter
    3 Shaders
    Atmosphere     (Firmament sky/clouds/fog/weather - the built subsystems)
    Advanced / Bridge  (MCP Bridge + Reload Builders; collapsed by default)

Notes:
- "World" is `bbt_env` promoted out of Firmament into its own top panel, since Terrain, Scatter,
  Shaders, and Atmosphere all read it. Firmament keeps its subsystem builders (sky/clouds/fog/
  weather) under "Atmosphere". This directly resolves the Apply Season confusion: there is one
  place to drive the world.
- Scene Quality (Preview/Final) moves to World (or a small scene/render group), since it is
  scene-wide, not atmosphere-specific.
- MCP Bridge is an agent/dev capability; demote it to an "Advanced" panel collapsed by default
  (or the addon preferences). It should not greet an artist first.
- Numbered stage hints (1/2/3) on the pipeline panels, plus a one-line overview at the top of
  the tab (decision E). All of A-F below are locked; see section 11.

## 5. Per-panel target UX

### 5.1 World (promoted from Firmament Environment)

Two clearly labelled groups (P3):
- World now: time/date/place and the live continuous values (temperature, wetness, snow, cloud
  cover, wind). These drive everything instantly via drivers.
- Set up a look: Season + Apply Season, and Scene Presets, labelled STRUCTURAL ("builds falling
  snow and the coverage pass", "rebuilds subsystems").
- Scene Quality (Preview/Final) and the Live Environment master toggle live here too, since they
  are scene-wide. (Shaders' own live_env folds into this one world-level toggle; see 5.4.)

### 5.2 Terrain (Heightfield Terrain)

- Context header (P1): the target terrain object.
- Keep the bake pipeline but mark Bake + Build as STRUCTURAL (P3); the displace knobs stay live.
- One preset idiom (P4) for the terrain presets.
- Remove the panel's Material picker (decision D): shading is the Shaders panel's job now
  (native). A terrain gets its material by selecting it in Shaders.

### 5.3 Scatter

- Context header (P1): active emitter + active layer.
- Per layer, asset source becomes a collection browser (choose the collection to instance);
  keep Make Proxies; add Import Biome here (moved from Shaders) to create/populate a collection
  from `library/models/<biome>` and set it as the layer's assets.
- Group structural (assets, align, mask group, path/camera presence, Build) vs live knobs (P3),
  which the panel already half-does; make the split explicit.

### 5.4 Shaders (native, per-mesh material list)

No mesh selected:

    Shaders
      Select a mesh to shade its materials.

A plain mesh with no material:

    Shaders
      Active mesh: Rock_03
      Materials: (none)
      [ New BobShader v ]   (Surface / Terrain)

A multi-material mesh (the tree), the primary case:

    Shaders
      Active mesh: Tree_01
      Materials on this mesh:
        (*) leaves      BobShader: Surface
        ( ) branches    plain        [Convert]
        ( ) trunk       plain        [Convert]
        [ Convert all ]
      ----- editing: leaves (Surface) -----
      > Surface
      > Weather

Behaviour:
- List EVERY material slot of the active mesh and nothing else (decision 2). This is a
  BobShader-focused VIEW plus Convert; it does NOT reimplement Blender's slot add/remove (that
  stays in Material Properties). Selecting a row sets the native `active_material_index`, so it
  stays in sync with Properties.
- Per row state (P5): a BobShader shows its master type and is selectable; a plain material
  shows a per-row Convert; an empty slot / no material shows New BobShader.
- Below the list, the editing sub-panels show for the SELECTED material only, and only the ones
  relevant to its master type: Surface, OR Terrain Layers + Layer Masks; plus Weather. Note two
  levels of "active": the active material slot, and (for terrain) the active layer index.
- The world-driven weather is controlled from World (5.1); Shaders no longer carries a separate
  Live Environment toggle.

Removed from Shaders: `material_name`, `target`, Build & Assign, Assign to Active, Assign to
Collection, Import Real Assets, the standalone asset-collection picker. Convert keeps a scope
selector (Active material / Selected objects / Collection); the Collection scope is the only
place a collection picker remains in Shaders, needed to batch-convert the unlinked scatter asset
collections (they are not viewport-selectable). This is convert (a material op), distinct from
asset selection (Scatter's job) - the clean line between the two panels.

### 5.5 Atmosphere (Firmament sky/clouds/fog/weather)

- Keep the subsystem builders, but adopt the shared vocabulary (P2: these are Build actions),
  the shared preset idiom (P4), and the visible structural marking (P3).
- The Environment sub-panel moves out to World (5.1); Atmosphere keeps Sky/Clouds/Fog/Weather.

### 5.6 Advanced / Bridge

- MCP Bridge start/stop, Reload Builders, autostart. Collapsed by default; not the artist's
  entry point.

## 6. Cross-panel flow (the intended journey)

1. World: set time/season/weather (or a Scene Preset).
2. Terrain: build the ground.
3. Shaders: select the terrain -> New BobShader (Terrain) -> edit layers.
4. Scatter: pick emitter -> add layers -> per layer pick or Import the asset collection -> tune.
5. Shaders: select the asset objects (or Convert scope = Collection) -> Convert.
6. World / Atmosphere: Apply Season, build sky/clouds -> the whole scene weathers.

The panel order in section 4 mirrors this so the UI teaches the flow.

## 7. Abstraction and data-model changes

- Remove `Scene.bbt_shaders.material_name` and `.target`.
- Fold Shaders' `live_env` into a single world-level Live Environment toggle (with the driver
  install/remove it triggers).
- Keep on `Scene.bbt_shaders`: `terrain_active`, the authoring pickers `surface_texture` /
  `layer_texture` (applied to the active material). Add nothing that duplicates native selection.
- Detection helpers (materials.py): `is_bobshader(mat)` (node tree has a "Master" group whose
  tree is `S_SurfaceMaster`/`S_TerrainMaster`), `master_type(mat)` -> surface|terrain|None.
- New BobShader: create a material (auto-name `M_<object>`), assign via `assign_material`
  (GN-aware, existing), wire the chosen master. Identity is the datablock, not a stored name.
- Convert: `bobshade_material` (exists).
- Shared UX helpers (new, small): a context-header draw helper and a structural-action draw
  helper (label + icon + "rebuilds: ..." note), reused by all panels so P1/P3 are consistent.

## 8. Migration map (by file)

ui/shaders.py: remove material_name/target/_current_material-by-name, the Build/Assign*3
ops+buttons, Import Real Assets, the standalone asset_collection picker; add the active-mesh
material list, showcase header, per-row New/Convert, Convert-all; rework Surface/Terrain/Weather
sub-panels to key off active_object.active_material via master_type; drop the local live_env
(use World's).

ui/scatter.py: add per-layer asset collection browser and Import Biome (moved from Shaders,
calls core.assets.populate_scatter_assets); make the structural-vs-live split explicit; add the
context header.

materials.py: add is_bobshader/master_type and a New-material entry (auto-name + assign);
keep bobshade_material, assign_material, and all builders unchanged.

ui/firmament.py: move the Environment sub-panel into a new World panel (world now vs set up a
look); keep Sky/Clouds/Fog/Weather as Atmosphere; adopt shared verbs/presets/structural marking;
move Quality + a single Live Environment toggle to World.

extensions/bob_blender_tools/__init__.py: register panels in pipeline order (section 4); demote
MCP Bridge to an Advanced (collapsed) panel; add the shared UX helpers module.

heightfield panel (in __init__.py): add a context header; mark Bake+Build structural; remove or
demote the Material picker (OPEN DECISION D).

assets.py: unchanged; now called from Scatter.

New: a small shared UI helpers module (context header, structural-action row, preset row) so the
principles are implemented once.

## 9. Implementation phases (for the new chat)

Phase 0, shared UX infrastructure: the UI helpers module (context header, structural marker,
preset row), and the panel reorder scaffold. Nothing behavioural yet.

Phase 1, World panel: promote Environment out of Firmament; world-now vs set-up-a-look; fold in
Quality and one Live Environment toggle. Update Firmament to Atmosphere.

Phase 2, Shaders identity pivot: is_bobshader/master_type; replace material_name/target with
active-object resolution; sub-panels read the active material. No feature loss.

Phase 3, Shaders contextual list + adaptive actions: per-mesh material list, per-row New/Convert,
Convert-all; remove old assign verbs and Import from Shaders.

Phase 4, Scatter asset home: per-layer collection browser; move Import Biome in; explicit
structural/live split.

Phase 5, Terrain + Bridge polish: heightfield context header + structural marking + material
picker decision; demote MCP Bridge.

Phase 6, docs + verify: update ARCHITECTURE.md, SHADERS.md, FIRMAMENT.md, SYSTEMS.md, CONVENTIONS.md
(a short "panel UX conventions" section); run the audits and the UX walkthrough (section 10).

## 10. Verification

- Headless register + icon/idname/prop audit for every panel (draw() is not exercised by
  register(); validate every drawn icon against UILayout enum items, every operator idname
  against the registry, every bbt_* prop against its PropertyGroup).
- No shading/geometry regressions: the algorithms are untouched, so a render-delta on a fixed
  scene must match pre-redesign output.
- UX walkthrough (new): script or manually drive the full flow (section 6) and confirm each step
  has a clear context header, the right adaptive action, and correct structural vs live marking;
  confirm a multi-material mesh lists exactly its materials and a converted material becomes
  editable.

## 11. Resolved decisions (locked 2026-07-20)

All resolved to the recommended option:

A. World is promoted to its own top panel (bbt_env leaves Firmament).
B. MCP Bridge becomes a collapsed "Advanced" panel in the tab (not moved to addon prefs).
C. Quality (Preview/Final) moves to World/scene.
D. The Heightfield Material picker is removed; a terrain is shaded by selecting it in Shaders.
E. Discoverability is in: numbered stage hints (1/2/3) on the pipeline panels plus a one-line
   overview at the top of the tab.
F. Keep the "BobBlenderTools" tab brand; rename panels to match the IA (Firmament -> Atmosphere,
   Heightfield Terrain -> Terrain; World and Advanced are new). Operators/classes keep their
   `bob_blender_tools.*` / `BBT_*` names.

There are no remaining open UX decisions; the implementation chat can proceed from this doc.

## 12. Out of scope / follow-ons

- Material browser/library mode (additive, later).
- Blender Asset Browser integration for scatter assets.
- Pre-existing shading follow-ons unrelated to UX: frost micro-normal, distance detail,
  erosion-mask glue, world-space triplanar normal maps, EEVEE curvature mask.
