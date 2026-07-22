# BobBlenderTools UX audit (2026-07-22)

Whole-suite UX audit across every N-panel and system, run against the ui_helpers idioms
(context_header P1/P7, structural_action P3, preset_row P4, seed_row) and the repo's
subtract-redundancy preference. Plain house style. Bucket A (functional bugs) is DONE and
headless-verified; B-H are open, scoped for a later pass.

Note on "moss": moss/dust/frost/wetness ARE exposed, in the Weather sub-panel, which is
DEFAULT_CLOSED (that is why it looked absent). Not a gap.

## A. Functional bugs -- DONE 2026-07-22 (headless-verified)
- Terrain stack mask kinds curvature/flow/noise TypeErrored on bake: `_op_to_dict`
  (__init__.py) always emitted `{low,high,falloff}`, but sel_curvature/sel_flow/sel_noise
  (ops_select.py) take `mode/strength` / `threshold` / `frequency/seed/contrast`. Fixed with a
  single `_MASK_PARAMS` table driving emit + load + draw per kind, so all three match each
  selector's real signature; the three kinds now also DRAW their params (were unconfigurable).
- The shipped `deposit` sediment op (engine._OPS) was missing from `_OP_META`/`_OP_PARAMS`/
  `_OP_ADD_DEFAULTS`, so it could never be added in the stack editor. Registered it (surfaced
  amount/iterations/flow_floor, rest in `_raw`); added the `flow_floor` field.
- Scatter "Noise Seed" could not be reshuffled (`scatter_random_seed` only touched "Seed").
  The operator now takes a `socket` name; both Seed and Noise Seed get a reshuffle button.
- Verified: venv engine runs all five mask kinds + a deposit op with the panel's emitted dicts,
  no TypeError (the old curvature emit still raises, proving the fix); Blender registers the new
  fields/enum and `_op_to_dict` emits engine-valid dicts; heightfield tests 43/43.

## B. Hidden features (exist in data/shader, no panel knob) -- PARTLY DONE 2026-07-22
- DONE: Terrain per-layer Flow band + Curve band placement masks now drawn in the Layer Masks
  sub-panel (_LAYER_FLOW / _LAYER_CURVE), so the "same masks as Scatter" label holds. [H]
- DONE: Terrain per-layer Detail Height drawn under the layer texture-set assign. [H]
- DONE: Surface Macro break-up (Amount/Scale) drawn unconditionally for wrapper surfaces, not
  only when a texture is assigned (it modulates base albedo either way). [M]
- WONTFIX (no utility): Curve surface colour/roughness/hard-edge is NOT a hidden socket.
  apply_curve_surface writes a real terrain layer slot, so those sockets are already live and
  now reachable via the extended terrain Layer Masks panel (Base Color/Roughness in _LAYER_SURFACE,
  Curve Hard in _LAYER_CURVE). A separate per-curve knob would duplicate that and mislead: all
  channel-"a" paths share ONE idempotently-reused slot, so a "this curve" knob would silently edit
  every path. apply_curve_wet is likewise a MAX-accumulated shared path. Left as-is; the real gap
  (discoverability that the curve band == a terrain layer) is a docs/flow note, not a knob. [M]
- DONE: Fog Detail added to firmament _FOG_SHAPE; the fog GN already exposed the socket. [M]
- OPEN: Minor unsurfaced engine knobs: build_sky sun_intensity, voronoi jitter, dunes warp. These
  are build-time params.get(), not live modifier sockets; surfacing needs scene props threaded
  into build params (bigger than socket-surfacing). Defer. [L]
- Verified headless: terrain + surface master groups expose every socket the new panel rows
  reference on all MAX_TERRAIN_LAYERS slots (verify_bucketB.py); fog GN exposes Fog Detail and
  all of _FOG_SHAPE resolves (verify_fog.py).

## C. Structural marking (P3) -- MOSTLY DONE 2026-07-22
- DONE: Atmosphere all six Build ops (Sky/Clouds/Fog/Rain/Motes/Snow) now route through
  ui_helpers.structural_action (shared STRUCTURAL_ICON + a "builds: ..." note). [H]
- DONE: Paths Bake & Erode + Revert to Clean marked structural (structural_action + Revert on
  STRUCTURAL_ICON), matching Build All in the same panel. [H]
- DONE: Shaders Biome Terrain WORLD -> STRUCTURAL_ICON (both draw sites). New/Convert/Convert-all/
  Add-Layer/Snow-Shell LEFT as ADD/NODE_MATERIAL/REMOVE on purpose: those are create / list-add /
  material-transform affordances with informative native icons, already separated from live knobs;
  forcing FILE_REFRESH onto them would replace clearer icons with a generic marker. [H/M -> decision]
- DONE: World Biome World WORLD -> STRUCTURAL_ICON. [M]
- DONE: Terrain Reload Builders FILE_REFRESH -> CONSOLE (a dev reload, not a datablock rebuild;
  kept off the shared marker so STRUCTURAL_ICON stays meaningful). [L]

## D. Inert controls not greyed (live slider does nothing in current mode) -- MOSTLY DONE 2026-07-22
- DONE: Atmosphere greys the driver-owned knobs when Live Env is on -- cloud Coverage + wind, fog/
  rain/mote wind, and the snow amount (only the truly driven sockets; bands stay live). A muted
  _env_owned_note replaces the copy-from-env button. [H]
- DONE: Scatter altitude/noise band knobs greyed when their Strength is 0 (Strength stays live,
  the dependent knobs grey), the Paths idiom. [M]
- DONE: Terrain Sculpt relief/detail/erosion/warp greyed when a custom Filter Stack is on (seed
  stays live -- the stack bake still uses it), with a "bypassed" caption. [M]
- DONE: World Quality + Live Environment greyed when Firmament is off (no env state to drive). [M]
- OPEN: Minor -- motion blur, weather camera, snow shell take effect only on next build (not
  driver-owned, a different case from the above); left as-is. [L]
- Verified headless: addon registers; every touched panel draws with no error across all four
  live_env x custom_stack states (verify_cd.py).

## E. Hand-rolled idioms that should route through ui_helpers -- MOSTLY DONE 2026-07-22
- DONE: seed_row generalised from a hardcoded `.value` socket to (data, prop); routed the four
  firmament seeds, the Terrain Sculpt seed (hf.seed IntProperty), and both Scatter seeds
  (Seed + Noise Seed, socket + op socket= prop). All seed reshuffles now one idiom. [M]
- DONE: Scatter Remove/Duplicate and Shaders terrain-remove / snow-shell-remove now self.report
  on both the success and the empty-guard path, parity with the Paths siblings. [M]
- OPEN: shared caption()/empty-state helper across World / Active Path / Active Layer. [L]

## F. Redundancy (subtract) -- PARTLY DONE 2026-07-22
- DONE: Paths header trimmed to the name only (role was reprinted from the list-row icon and the
  Active Path sub-panel); now matches the Scatter header (name in header, kind in the sub-panel).
  Scatter was already correct (header names the layer, sub-panel shows the kind). [M]
- OPEN: Biome Terrain drawn in two places (shaders root + terrain panel). [L]
- OPEN: Recomputed _terrain(context) within one Paths draw; env-sync operators could fold. [L]

## G. Labels / icons / wording -- PARTLY DONE 2026-07-22
- DONE: Scatter import op bl_label "Import Assets" -> "Import Real Assets", sharing the button's
  "Import Real" stem (the button pairs with Make Proxies: real vs proxy). [M]
- DONE: the adjacent "Macro Amount" collision -- the TexSet one relabelled "Tile Macro Amount"
  (it breaks up texture tiling) vs the master's albedo Macro break-up, via a _draw_inputs labels
  override, in both surface and terrain. [M]
- OPEN: FREEZE icon on three sections; thermal/smooth share MOD_SMOOTH; Scene Preset overrides the
  PRESET icon; do_material "Damp bed" label vs a contradicting tooltip. [L]

## H. Discoverability / flow -- MOSTLY DONE 2026-07-22
- WONTFIX: Scatter "Build This Layer" (child) / "Build All" (parent) split mirrors Paths exactly
  (Build This Curve child / Build All parent), which the audit's own "already consistent" note
  endorses. Intentional parent=all / child=this; left as-is. [M]
- DONE: Terrain Sculpt sub-panel no longer DEFAULT_CLOSED (Terrain's primary tuning panel opens
  like each other system's; Displace + Filter Stack stay closed). [M]
- DONE: Paths Naturalise box shows a "Bake the terrain to naturalise" hint when the terrain is
  unbaked, rather than vanishing silently. [M]
- DONE: Shaders env-off warning (_env_note, promoted to a module function) now also draws in the
  Weather sub-panel where the inert weather knobs live. [M]
- OPEN: Load Preset Stack's source preset is chosen in a different (parent) panel, off-screen. [L]

Verified headless (E-H): every touched panel -- including Paths, the Scatter/Shaders sub-panels,
and the Weather sub-panel -- draws with no error across all live_env x custom_stack states, with
the generalised seed_row and the module-level _env_note (verify_cd.py).

## Already consistent (do not re-flag)
preset_row is used uniformly (cloud/fog/rain/mote/scene/surface/terrain-layer/stack presets);
bl_category and bl_order are consistent; Build-This/Build-All wording matches Paths<->Scatter;
top panels are all DEFAULT_CLOSED except World (the intentional anchor); the Advanced panel is
cleanly dev-only. Water master is fully exposed (all 31 sockets across Water + Weather panels).
