# BobShaders plan

Forward-looking design for the surface-materials capability, not the current state.
`ARCHITECTURE.md` describes what is built today; this describes where authored surface
shading is going: strong, art-directable master materials, and the world-driven weather
layer that ties the whole suite together. Build against this; fold settled parts into
`ARCHITECTURE.md` and `SYSTEMS.md` as slices land.

## Two jobs

BobShaders does two things, and both matter:

1. Give the artist strong shaders to work with: a small set of parameterized MASTER
   materials (a terrain master that blends many surfaces, a surface master for props,
   rocks, and vegetation), each a solid base colour that optionally tints a texture set,
   with triplanar, anti-tiling, and per-instance variation. This is the substance:
   production-quality surfaces that also block out cleanly as flat colours.
2. Be the glue. Every master ends in one shared weather layer that reads the world state
   (`bbt_env`) and the `snow_cover` attribute Firmament writes, so every surface obeys the
   weather. Firmament authored the world and where snow sits, Terrain made the ground,
   Scatter placed the assets; BobShaders puts materials on all of it and makes them
   respond, so one control moves everything:

       set season = winter
         -> Firmament raises env.snow, builds falling snow, writes snow_cover
         -> BobShaders whitens surfaces by snow_cover, wets the melt line, frosts by cold
         -> Scatter swaps to winter assets on Apply

   The continuous look (white, wet, frosted) is BobShaders; the structural swap (bare
   trees) stays Scatter's Apply. Together the whole scene responds to one world state.

BobShaders is the top of the dependency graph: it reads `bbt_env` and `snow_cover`, is
applied to Terrain and Scatter output, and imports none of them. The seam is the op
contract plus `bbt_env` plus the `snow_cover` attribute contract, the same bus the rest of
the suite uses, so a `BobBlenderShaders` split stays mechanical.

## Decisions locked (2026-07-19)

- Master materials, not a bag of one-off materials. BobShaders authors a few parameterized
  master shader node groups; a per-object material is a thin wrapper (one master-group node
  plus one Principled BSDF) whose interface inputs are the "instance parameters." This is
  the Blender-native equivalent of an Unreal master material plus instances: one graph to
  maintain, many looks, cheap to vary. Named surfaces (Rock, Grass, Bark) become presets
  and per-instance parameter sets of a master, not separate graphs.
- The terrain master blends multiple surface layers across the ground by the SAME mask
  vocabulary Scatter uses (slope band, altitude band, noise, paint, and curvature), with a
  height-aware blend so layers interlock instead of cross-fading. Reusing the scatter masks
  is deliberate: rock scatter on scree slopes and rock texture on scree slopes then agree
  by construction. This is a core glue point, not a coincidence.
- Solid colour by default, texture optional, colour as tint. A surface's base is a flat
  base colour plus scalar roughness and metallic, so a block-out look is clean and
  predictable with no procedural noise. Assigning a texture set is optional; when a set is
  present the base colour MULTIPLIES (tints) the texture's albedo and the scalars modulate
  the maps, so the same colour and roughness parameters drive both the flat and the textured
  look and switching to textures never throws away the tuned colours. Texture sets are still
  first-class (strong shaders need real maps), the default and the no-map fallback is just a
  solid tint, not a procedural pattern.
- Anti-tiling is built in: triplanar projection (no UVs needed on terrain), macro variation
  (large-scale colour and roughness break-up), and distance-based detail (fine tiling up
  close, macro far), plus per-instance variation (Object Info Random or INSTANCER
  attributes) so scattered copies are not identical.
- One shared weather layer (`S_Weather`) ends every master, reading the world. Coverage has
  a single authority: BobShaders READS the `snow_cover` attribute where the Firmament pass
  ran (the terrain), and computes a shader-side fallback with the exact same formula (pinned
  in `SYSTEMS.md`) where it did not (scattered assets, plain meshes). Be honest that this
  means most surfaces use the fallback; the GN pass is the authority for the terrain and the
  documented formula is what the fallback must match.
- Cycles is the target and the verification gate. EEVEE keeps working where free; features
  that are Cycles-only (Pointiness-based curvature and cavity) are gated and have an
  EEVEE-safe alternative (a baked or painted mask), so EEVEE degrades rather than breaks.
- Live vs structural, the policy the scatter and Firmament work settled: continuous world
  values (snow, wetness, frost) feed the shaders live; a change of kind (base texture set,
  layer set, season asset swap) is an explicit Build / Apply, never a property callback.
- The world reaches the shaders through drivers (the Firmament mechanism). Baseline is
  per-material drivers installed and reinstalled centrally on build (known to work). An
  optimization, `S_EnvState` (below), collapses that to one driver set if a Phase-0 spike
  confirms it; the plan does not depend on the optimization.

## Goal

A material system, art-directable from a panel, that renders correctly in Cycles: a
terrain master that blends layered surfaces by slope, altitude, noise, paint, and
curvature with height-aware transitions; a surface master for props, rocks, and vegetation
with per-instance variation; both a solid base colour that optionally tints a texture set,
triplanar, and anti-tiling; both ending in a shared weather layer that reads `bbt_env` and
`snow_cover`; plus the snow accumulation shell (reads the same attribute). All Blender-side
shaders and a little geometry nodes, no venv compute. Extract-ready as `BobBlenderShaders`.

## Architecture and where it fits

A capability panel in BobBlenderTools, peer to Firmament, Terrain, and Scatter, and the
top of the dependency graph. Object-native like scatter: materials are real datablocks
assigned to scene objects.

Scene state, the same split the rest of the suite uses:

- `Scene.bbt_env` is the world state, owned by Firmament. BobShaders reads it through
  `env.get_env()` (None-guarded, so a material still works when Firmament is disabled, just
  without the shared weather) and never writes it.
- `Scene.bbt_shaders` is BobShaders' own UI and subsystem state: the materials, their
  master type, layer stacks, texture assignments, and weather-layer toggles.

The shading lives in materials and shared shader node groups (naming per `CONVENTIONS.md`:
`S_<Effect>` node groups, `M_<Surface>` wrapper materials):

- `S_TerrainMaster`: the multi-layer terrain blend.
- `S_SurfaceMaster`: the single-surface master for props, rocks, and vegetation.
- `S_Weather`: the weather layer, ending both masters.
- `S_EnvState`: the world-to-shader bridge (spike-gated optimization, below).
- `S_Triplanar`, `S_TextureSet`, `S_MacroDetail`: shared helpers for projection, map
  loading, and anti-tiling.
- `M_<Surface>`: thin wrapper materials (one master node + one BSDF), the artist-facing
  datablocks, cached by name like the existing volume/particulate materials.

## Master materials and instances

The pattern, since Blender has no native material instances: a master is a shader node
group with a rich exposed interface (the parameters). A per-object material is a thin
wrapper, one master-group node feeding one Principled BSDF and the output, and the
"instance parameters" are that node's interface input values. Presets are named parameter
sets applied to those inputs (like the scatter layer types and the cloud presets). Two
masters:

- `S_SurfaceMaster` (props, rocks, bark, single-surface): a solid base colour plus scalar
  roughness and metallic by default, optionally tinting a texture set (the colour multiplies
  the texture albedo), with triplanar (optional, for un-UV'd meshes), macro and detail
  variation, and per-instance variation via Object Info Random or INSTANCER attributes so
  scattered copies differ (colour, scale, rotation, wear). Presets: Rock, Cliff, Bark, Soil,
  Metal, Painted, Grass-blade.
- `S_TerrainMaster` (the multi-layer ground): below.

Both end in `S_Weather`, so world response is identical whatever the base. Editing the
master graph updates every wrapper; editing a wrapper's inputs changes only that material.
This is the "strong shader to work with, cheap to reuse" shape.

## The terrain master material

The substantive part. `S_TerrainMaster` blends an ordered stack of surface layers across
the ground and hands the result to `S_Weather`.

- Layers. Each layer is a surface (a solid base colour and scalar roughness by default,
  optionally a texture set the colour tints: albedo, roughness, normal, height) plus a
  placement weight. A sensible default stack: soil, grass, rock, cliff, scree, sand, each a
  flat colour until textured. The stack is data (`bbt_shaders`), so layers are added,
  removed, and reordered.
- Placement weight per layer, from the SAME masks Scatter uses, each gated by a strength so
  an unused mask is cheap: slope band (min/max normal Z), altitude band (world Z with
  falloff), noise/clumping (scale, contrast, seed), a paint mask (a vertex-colour or named
  attribute channel, or an RGBA splat image channel), and curvature (convex ridges vs
  concave valleys). Curvature uses Geometry Pointiness (Cycles-only), so it is gated with an
  EEVEE-safe fallback (a painted or baked curvature mask).
- Height-aware blend, not linear cross-fade. Layers composite by biasing each layer's own
  height map by its weight and picking the highest per texel (a height-lerp), so rock pokes
  through grass at a natural interlocking edge instead of a soft dissolve. This is the
  single feature that separates a strong terrain material from a weak one.
- Anti-tiling per layer: triplanar projection (terrain has no clean UVs), macro variation to
  break up repetition at distance, and distance-based detail so fine tiles read up close and
  macro reads far.
- Art direction: the paint mask lets the artist weight layers by painting a vertex colour or
  attribute, or by an RGBA splat image (the same PNG pipeline the heightmap uses). Opportunity
  glue: the venv erosion pipeline already computes flow accumulation and deposition; baking
  those to auxiliary masks alongside the height PNG would drive sediment into valleys and bare
  rock onto eroded ridges automatically. Noted as a strong follow-on, not required for v1.

The blended base then feeds `S_Weather` (snow on top of the layered ground, wetness pooling
in the low layers, and so on), so terrain gets both a real layered surface and the world
response.

## Colour, texture sets, and tinting

The default look is a solid base colour plus scalar roughness and metallic. This is the
clean block-out: no procedural noise, exactly the colour you set. A texture set is optional
and layers on top:

- A texture set is a folder under `library/textures/<name>/` with conventionally named maps
  (`*_basecolor`, `*_roughness`, `*_normal`, `*_height`, `*_ao`, `*_metallic`). A shared
  `S_TextureSet` helper loads the present maps and sets colour spaces correctly (Non-Color
  for data maps).
- Colour as tint. When a base-colour map is present, the final albedo is
  `map_albedo * base_colour`, so the base-colour parameter tints the texture (white = the
  texture unchanged) instead of being ignored. With no map, the albedo is just the base
  colour. So the same colour parameter is meaningful in both modes and switching a surface
  from solid to textured keeps its tuned colour.
- Scalars modulate maps. Roughness and metallic are used directly as the value when there is
  no map, and multiply the map when there is one, so a roughness slider always does
  something. Normal and height come from their maps when present, and are flat/off when not.
- So a surface is authored as a solid tint first and upgraded to real maps by pointing it at
  a set, with no graph change and no lost parameters. UDIM and per-project overrides
  (`projects/<name>/textures/`) are later; v1 is the shared library plus the solid tint.

## The shared weather layer

`S_Weather` takes the master's base albedo, roughness, normal, and optional height and
overlays the world's effect, each term gated by a strength so unused terms stay cheap
(honest caveat: shader gating reduces, it does not zero, cost; the budget slice measures a
full stack). Terms:

- Snow: mix a snow shading (white albedo, soft high roughness, faint sparkle, a little
  subsurface) by the coverage (below) times the snow amount. The surface-snow look, the
  first weather slice.
- Wetness: darken albedo, drop roughness, lift specular for a wet read, and optionally pool
  water in cavities and low ground. Pooling needs a cavity source; that is Pointiness
  (Cycles-only) or a painted/baked wetness mask (EEVEE-safe), so pooling is optional and its
  source is a choice, while the uniform wet darkening works everywhere.
- Frost: a cool crystalline micro-normal and blue-white sparkle on up-facing exposed faces,
  gated hard below a temperature threshold so it costs nothing above freezing.
- Dust and moss: warm dust on up-facing surfaces, moss on shaded faces. Because season is a
  kind (structural), this term is driven by a continuous dustiness/moss amount set on Apply
  Season, not by a live driver on the season enum. The lightest term; can start crude.

### Weather from the world state (mapping)

The layer does not read raw fields blindly; a small mapping turns the world state into two
effective drivers plus the discrete terms, so `env.weather` (the field the sweep deferred
here) actually does something:

- effective wetness = max(`env.wetness`, weather contribution), where `weather` in {rain,
  storm} raises it and {clear} does not; fog and cloud do not wet the ground.
- effective snow = combination of `env.snow` (the level) and coverage, with `weather` = snow
  reinforcing fresh accumulation.
- frost = f(`env.temperature`) below freezing.
- dust/moss = the Apply-set dustiness, biased by season.

This mapping is the one place `env.weather`, `wetness`, `snow`, `temperature`, and `season`
converge into shading, so it is documented in one spot and kept simple.

### The env bridge and how the world drives it

Baseline (known to work, the Firmament mechanism): each wrapper material carries drivers on
its `S_Weather` inputs reading `scene.bbt_env`, installed and reinstalled centrally by
BobShaders on build, and removed when Firmament is absent so no driver dangles. A BobShaders
Live Environment toggle gates this (its own toggle, not Firmament's `bbt_firmament.live_env`,
so BobShaders depends only on the world state, not another capability's UI).

Optimization to spike, not depend on: `S_EnvState`, one shared node group holding the live
env values in internal nodes driven once from `bbt_env`. Because a node group is a single
datablock shared by every instance, driving it once would feed every material, collapsing
the per-material driver set to one. Adopt it only if a Phase-0 spike confirms the internal
drivers install and evaluate through the depsgraph (the headless-eval landmine applies:
verify wiring deterministically, prove evaluation by render-delta). If it fails, the
per-material baseline stands.

### Coverage: the attribute and the one duplicated formula

`S_Weather` reads coverage from the `snow_cover` attribute (Geometry Attribute node, name
exactly `snow_cover`, FLOAT, POINT domain, 0..1) where the Firmament GN pass ran. That is
the terrain. Scattered assets and plain meshes carry no such attribute (they are separate
objects), so for them the layer computes a shader-side fallback from the shader Geometry
node (normal Z for slope, world Z for altitude) using the exact formula pinned in
`SYSTEMS.md` (the two masks ease in opposite directions). A per-material switch chooses
attribute (terrain) or computed (everything else); default computed, since most surfaces
have no pass. The GN pass stays the authority (it alone has occlusion and pairs with the
accumulation shell); the fallback must match its formula, the one place two implementations
exist.

## Snow accumulation shell

Deferred here from Firmament: a geometry-node pass (attached via the existing
`build_geonodes_on_object`, like the coverage pass) that displaces the surface along its
normal by `snow_cover`, then smooths and rounds it for real thickness, silhouette, and
drifts. It reads the same attribute the material reads, so shell thickness and material
whiteness line up. Added where thickness matters; the material-only look covers flat or
distant ground.

## The glue: how the capabilities converge

- Firmament is the source. BobShaders reads `bbt_env` (via the drivers) and `snow_cover`,
  never writes them, and its Live Environment feed extends Firmament's reach to every
  surface.
- Terrain is the first client. The terrain object already carries the `snow_cover` pass, so
  the terrain master reads it directly; the layer masks (slope/altitude/noise/paint/curvature)
  are the same vocabulary that placed the scatter, so texture and scatter agree; triplanar
  keeps projection clean on relief; and the erosion bake can later feed layer masks.
- Scatter is the second client. Scattered assets get the surface master (with per-instance
  variation) so props weather with the ground; season drives the continuous look, the
  structural asset swap stays Scatter's Apply.
- Firmament scene presets tie it together. They set `bbt_env`; because BobShaders reads
  `bbt_env`, picking Winter moves the surfaces too, with no extra step. Winter is the
  showcase: layered ground gone white, wet melt line, frosted rock, snow-laden branches.

## Live vs structural

- Live: continuous values (snow, wetness, frost) feed the weather layer via drivers, so
  surfaces respond to an Environment slider with no rebuild. BobShaders owns its Live
  Environment toggle.
- Structural: base texture set, the terrain layer stack, curvature/wetness mask source, and
  the accumulation shell are explicit Build / Apply. Season asset swaps belong to Scatter's
  Apply, not a material callback. A structural rebuild preserves tuned wrapper inputs where
  practical (the interface-value snapshot the GN rebuild uses is the model to follow).

## Ops and code layout (extract-ready)

- `bbmcp/materials.py` grows the master builders and shared node groups (`S_TerrainMaster`,
  `S_SurfaceMaster`, `S_Weather`, `S_EnvState`, `S_Triplanar`, `S_TextureSet`,
  `S_MacroDetail`) and the `M_<Surface>` wrapper builder, cached by name.
- `bbmcp/geonodes/recipes/snow_shell.py`: the accumulation-shell GN recipe, attached via
  `build_geonodes_on_object`.
- Op vs panel-only, a genuine fork: Scatter set the precedent of driving `build_geonodes`
  in-process from its panel with no new MCP op and no reconnect. BobShaders can do the same
  for interactive use (build materials in-process, panel-only, addon re-enable to iterate).
  A `build_material` op (the commented-out stub in `dispatch.py` / `contracts.py`) is worth
  adding only for agent-over-MCP authoring, and is the one reconnect if so; free-form
  `master: str` plus `params` keeps new surface types reconnect-free after that.
- Panel in the extension (`bob_blender_tools/shaders_panel.py`), scene state in
  `Scene.bbt_shaders`. Classes `BBT_*`, operators `bob_blender_tools.shaders_*`.

Naming: shared logic uses the `S_` convention (`S_TerrainMaster`); wrapper materials use
`M_<Surface>`. The generated `BOB_` namespace stays for the auto-cached volume/particulate
materials; BobShaders' artist-facing masters and wrappers follow `CONVENTIONS.md`.

## Panel and presets

`BBT_PT_shaders` in the BobBlenderTools tab:

- Object and material: pick the object (or Use Active), a master type (Terrain / Surface),
  Build / Assign, and the coverage-source switch (attribute vs computed).
- Terrain master sub-panel: the layer stack (a list like the scatter layers), add/remove/
  reorder, per-layer surface (solid colour or a tinted texture set) and the mask knobs (slope,
  altitude, noise, paint, curvature) drawn as live knobs, plus the height-blend and
  anti-tiling controls.
- Surface master sub-panel: the PBR inputs (base colour and scalars, optional tinted texture
  set), triplanar toggle,
  macro/detail, and per-instance variation.
- Weather layer sub-panel: per-term toggles and strengths (Snow, Wet, Frost, Dust), driven
  from `bbt_env` when Live Environment is on (sliders show as driven), manual with a Use Env
  button shown only when it is off.
- Snow sub-panel: accumulation-shell Add / Remove and thickness.
- Presets: surface presets (Rock, Cliff, Bark, ...) and terrain-stack presets (Alpine,
  Desert, Temperate); scene-wide look still comes from Firmament's scene presets moving
  `bbt_env`.

## Cycles-readiness and EEVEE

Cycles is the gate and where the layered masters, height-blend, and weather are tuned. EEVEE
works for the standard Principled path; the Cycles-only features (Pointiness for curvature
and cavity) are gated with an EEVEE-safe mask fallback, so EEVEE degrades (loses procedural
curvature, keeps painted/baked masks) rather than breaking. No material displacement (the
shell is real GN geometry, so it reads in both engines). Triplanar is three texture samples
per map, so the budget slice measures a full layered terrain plus weather, not a single
material.

## Verification (headless render, each slice)

Material quality is visual and subjective, so the render gate is a smoke test and
regression catch with a human eyeball checkpoint per slice, not a quality gate. The
automated gate:

- Build a master, assign to a test mesh (a grid, and a terrain carrying `snow_cover`),
  render a tiny Cycles frame, read pixels back.
- Terrain blend by render-delta: a layer's mask (raise the slope band) measurably changes
  the surface in its region; two layers produce two regions.
- Weather response by render-delta (the driver-readback landmine means prove it by render,
  not by reading the driven node value): env.snow 0 vs 1 whitens; env.wetness 0 vs 1
  darkens; env.temperature below freezing adds frost. Linear EXR where a bright surface
  clips.
- Coverage: the attribute path and the shader fallback agree on a mesh carrying the pass
  (proves the one-formula rule), checked by render-delta on matched frames.
- Per-instance variation: two instances of the surface master differ (Object Info Random).
- Each render completes in a few seconds so the gate stays runnable.

## Phase 0 spikes (de-risk before building)

- The height-aware layer blend: confirm a height-lerp of two texture-set layers interlocks
  cleanly in Cycles and reads better than a linear mix, at acceptable cost.
- Triplanar plus texture-set loading: `S_Triplanar` projecting a library texture set by
  world position renders without visible seams on steep ground.
- Reading `snow_cover`: an Attribute node (Geometry, `snow_cover`, POINT) renders coverage
  on a terrain that carries the pass and reads 0 (falls back) where it does not.
- Shader-side coverage matches the GN formula (the pinned endpoints), numerically close on a
  test mesh.
- The `S_EnvState` optimization: drive a shared node group's internal nodes from `bbt_env`
  once and confirm two materials read it live (render-delta) and that it evaluates through
  the depsgraph. If it fails, keep per-material drivers.
- Wetness cavity source: Pointiness pooling in Cycles versus a painted mask, and confirm the
  EEVEE fallback path.

If a spike is slow or wrong, the plan is adjusted then, on evidence.

## Phase 0 findings (2026-07-19, headless Cycles 5.2 on the dev 5080)

Run before any implementation. Evidence, not assertion.

- S_EnvState is VIABLE (the cheap-glue bet holds). `driver_add` works on a node-group-
  internal node's value, both on a Value node output and on a Math node input. One driver on
  a shared node group's internal value feeds every material that instances the group: two
  materials on two objects both rendered the one driven `bbt_env.wetness` value, near
  identical (0.184 / 0.196 for 0.8), so the group is shared and the driver reaches all
  instances in a render. The Firmament headless landmine carries over: a freshly added driver
  takes a few settle renders to wire in `--background` (the first render or two use the node
  default), but it evaluates in a real render and the interactive UI. So S_EnvState moves
  from spike-gated to adopted; per-material drivers remain a known-good fallback.
- The `snow_cover` contract holds. A shader Attribute node (Geometry, name `snow_cover`)
  reads the pass output on the terrain (mean emission 0.131) and reads ~0 on a plain grid
  with no pass (0.008), so the manual attribute-vs-computed switch is sound with a clean
  zero fallback. Attribute confirmed FLOAT on the POINT domain.
- The shader-side coverage formula reproduces the GN pass EXACTLY. Computing
  `Snow * smoothstep(normalZ, thr-fall, thr) * smoothstep(worldZ, alt, alt+fall)` per vertex
  matched the GN `snow_cover` attribute with max abs diff 0.0000 and correlation 1.0000. The
  pinned endpoints in `SYSTEMS.md` are correct, and a plain-mesh fallback can match the
  authority; keep the two identical.
- The height-aware layer blend works and reads far better than linear. A height-lerp of two
  noise-height layers rendered as crisp interlocking grass/rock patches (rock breaking
  through grass), where the linear mix of the same two layers was a flat muddy average. Cost
  is a few math nodes. This is the terrain-master headline and it is confirmed.
- Triplanar works. A checker projected by world position on three axes, blended by the
  normal, kept uniform cell size across a sphere with no polar stretch. The axis-transition
  zones are soft with a naive blend; sharpen with a blend-power exponent on the normal
  weights. Real texture-set loading was not spiked (no textures in `library/textures/` yet),
  but that is file I/O, not a shader risk.
- Pointiness (the optional wetness-cavity source) was NOT cleanly verified: the spike
  rendered zero/background in both engines (a setup issue, not a clear read). It is
  documented as Cycles-only and the plan already treats cavity pooling as optional with a
  painted/baked-mask fallback, so it is not load-bearing; verify at S4 with a proper setup
  (shade-smooth, a lit non-black world, a gain on the raw value) before relying on it.

Net: the load-bearing bets (S_EnvState, the coverage contract and formula, height-lerp,
triplanar) all hold. Nothing forces a plan change; S_EnvState is upgraded to adopted and
the wetness cavity stays behind its mask fallback until Pointiness is confirmed.

## Slices

Strong base shaders land first (the artist's ask), the weather depth after, each headless-
rendered and eyeballed like Firmament.

- S1 Master framework + surface master (done, 2026-07-19, headless Cycles/OptiX on the
  5080): the wrapper/master pattern, `S_SurfaceMaster` (solid base colour, per-instance
  variation), `S_Weather` with the snow term only, `S_EnvState` env drivers, build/assign,
  `Scene.bbt_shaders`, and the `Shaders` panel. Payoff: a strong single-surface material
  that whitens by coverage when the world snow rises, assignable to props and scatter with
  no rebuild. What shipped and the decisions taken:
  - Four cached shader node groups in `materials.py` (get-or-create, so a re-Build never
    wipes a wrapper's tuned inputs): `S_EnvState` (internal Value nodes driven once from
    `bbt_env`, one shared datablock feeding every material), `S_Weather` (snow term),
    `S_SurfaceMaster` (solid base + Object-Info-Random brightness variation, ending in
    `S_Weather`), and `surface_material()` building the `M_<name>` wrapper (one master group
    node -> one Principled BSDF -> Output). The instance parameters are the wrapper's group
    node input values, drawn live in the panel and edited in place (shader group inputs
    re-evaluate on edit, so there is no rebuild-and-restore surface, unlike GN modifiers).
  - Coverage is the pinned one-formula rule, exercised: `S_Weather` reads the `snow_cover`
    attribute (Geometry Attribute node, POINT) when Use Attribute = 1 (the terrain), and
    computes `Snow * slope_mask * altitude_mask` with the exact SYSTEMS.md smoothstep
    endpoints when 0 (the default; scattered assets and plain meshes carry no pass).
    Occlusion is omitted from the fallback (GN-only raycast, default 0), so the two paths
    are identical in the fallback's domain. Verified by render-delta: attribute and computed
    whiten a matched grid identically (diff 0.000).
  - The world bridge is `S_EnvState`, the adopted Phase-0 optimisation: one SINGLE_PROP
    driver per field (snow/wetness/temperature) on the single shared group, reading
    `scene.bbt_env`, reinstalled on every Build and removed when Live Environment is off or
    Firmament is absent. Per-material drivers remain the documented fallback in code.
    BobShaders owns its own `bbt_shaders.live_env` toggle; it reads the world state, never
    Firmament's `bbt_firmament.live_env`.
  - Panel-only, in-process like Scatter (the op-vs-panel-only fork, resolved to panel-only):
    no new MCP op, no reconnect; the `make_material` stub in `dispatch.py` stays commented.
  - Verified: 11/11 automated checks (icon/idname/prop audit; S_ groups built and wrapper
    wired; get-or-create idempotent; S_EnvState drivers valid and targeting the right
    `bbt_env` field; Live Environment toggle removes/reinstalls; snow render-delta whitens
    env.snow 0->1; attribute==computed coverage; two instances differ by Object Info Random)
    plus a 10/10 full-extension register smoke test (Firmament owns `bbt_env`, shaders does
    not double-register, clean re-register). Eyeball (AgX, 960p): a scatter of rocks over
    ground, snow accumulating on up-facing tops while steep/under faces keep the rock, the
    coverage line responding live to the Environment snow slider.
  - Deferred to later slices (stated honestly): texture sets, triplanar, and anti-tiling to
    S3 (see Open decisions); wetness/frost/dust and the accumulation shell to S4.
- S2 Terrain master (done, 2026-07-20, headless Cycles/OptiX on the 5080): `S_TerrainMaster`,
  the multi-layer height-aware blend by the scatter mask vocabulary, plus the layer-stack UI.
  Payoff: a real layered terrain that agrees with the scatter, ending in the same weather
  layer. The headline of "strong shaders to work with." What shipped and the decisions taken:
  - `S_TerrainMaster` is one shared group with a FIXED slot count (`MAX_TERRAIN_LAYERS = 6`),
    the master + instances model: the stack is the enabled slots, all knobs live on the
    wrapper node, so add/remove/tune never rebuild the graph (a disabled slot has Enable 0 and
    is never blended in). It ends in `S_Weather`, so terrain gets the identical world response
    as the surface master.
  - Per-layer placement weight = the product of the SAME masks Scatter uses, each gated by a
    strength (0 = off): slope band (Min/Max Normal Z), altitude band (reproducing scatter's
    `_height_mask` rising*falling), noise clumping (the IDENTICAL `ShaderNodeTexNoise` at world
    position the scatter recipe uses, so a layer and its matching scatter clump on the same
    ground by construction), a per-layer paint attribute (`bbt_paint_L{i}`), and a Cycles
    Pointiness curvature term (favours convex ridges, gated, default off; EEVEE reads flat, so
    it degrades not breaks, the baked-mask EEVEE path is S3+).
  - The blend is a HEIGHT-LERP, not a linear cross-fade (the Phase-0 headline): each layer
    builds a height field H = weight + Height Bias + macro noise, and layers composite by
    fac = enable * b2/(b1+b2) where b1/b2 are the heights above (max(H) - Blend Softness), so
    the higher-H layer wins per texel within a soft band and layers interlock. Stacking order
    is by Height Bias, not slot order (a nicer model than reorder), so there is no reorder op.
  - GN-object material assignment fixed (a real integration gap found at the eyeball): a
    GEOMETRY-NODES-generated mesh (the terrain, heightmap_terrain) ignores the object's
    material slots, so `obj.active_material` renders the default grey. `materials.assign_material`
    now also drives a small per-material Set-Material GN modifier (`BBT_Material`) at the end
    of the stack for any object carrying a Nodes modifier, so Assign shades GN terrain too,
    survives the terrain's non-destructive rebuild, and passes `snow_cover` through untouched.
  - Panel: a Terrain Layers sub-panel (shown when the master is Terrain) with the layer slots
    (enable toggle + colour swatch + select), Add/Remove, a Layer Preset menu (soil, grass,
    rock, cliff, scree, sand, each with its placement masks), a Layer Masks sub-panel (slope/
    altitude/noise/paint/curvature), the global blend knobs, and stack presets (Temperate,
    Alpine, Desert). The Surface and Terrain sub-panels are gated by the master type.
  - Verified: 13/13 automated (terrain icon/idname audit; group built, wrapper wired, ends in
    S_Weather -> S_EnvState, all slots exposed; slope-masked and altitude-banded layers place
    by render-delta on a sphere; height-lerp shows both layers as variance; still whitens with
    snow; stack preset enables the right slots; a GN terrain shades green-dominant via the
    Set-Material modifier) plus the 12/12 register smoke test. Eyeball (top-down layer map):
    grass clumps on flats by the noise mask, rock threads the steep drainage creases by the
    slope mask, soil base between, and snow whitens the up-facing high ground on top.
  - Deferred to S3 (stated honestly): per-layer triplanar, texture-set maps, and anti-tiling
    (macro/distance detail) - S2 is solid-colour layers, so triplanar has nothing to project
    yet. The paint mask is a named attribute in S2; the RGBA splat image and erosion-baked
    masks are later.
- S3 Texture, projection, anti-tiling: `S_TextureSet` from `library/textures/`, macro and
  distance detail, terrain-stack and surface presets. Payoff: production fidelity, no tiling.
- S4 Full weather + shell: wetness (with the cavity/mask source and the `env.weather`
  mapping), frost, dust/moss on Apply, and the `snow_shell` accumulation pass.
- S5 Whole-look + budget: assign to scattered assets so Scatter output weathers; confirm
  Firmament scene presets (Winter) move the surfaces with no extra step; the erosion-mask
  layer-driving glue if pursued; and a real 1080p budget frame with the full stack (layered
  terrain + weathered scatter + Firmament volumes) on the dev GPU.

## Open decisions

- Texture timing: RESOLVED (2026-07-19). Solid-colour base shipped in S1; texture-set
  support (`S_TextureSet`, triplanar, anti-tiling) lands at S3, the recommended path.
  `library/textures/` is empty, so there was nothing to project or test, and Base Color is
  built as the tint it will become (albedo today, Base Color * map at S3), so the interface
  is stable and switching solid<->textured later loses no tuned value.
- Env bridge: RESOLVED by the Phase-0 spike. Adopt `S_EnvState` (shared-drive-once,
  confirmed viable); per-material central drivers remain the known-good fallback.
- Terrain layer weighting source: PARTIALLY RESOLVED (S2). Procedural masks (slope, altitude,
  noise) and a per-layer named paint attribute (`bbt_paint_L{i}`) shipped in S2; the RGBA splat
  image and the erosion-baked masks are later slices.
- Terrain layer stacking order: RESOLVED (S2) to Height Bias, not slot reorder. Raising a
  layer's Height Bias brings it to the top of the height-lerp, so there is no reorder op.
- Op vs panel-only: RESOLVED (2026-07-19) to panel-only, in-process like Scatter (no
  reconnect); the `make_material` stub in `dispatch.py` stays commented. Add a
  `build_material` op only if agent-over-MCP material authoring is later wanted.
- Accumulation shell home: BobShaders (recommended, it reads `snow_cover` and pairs with the
  surface look) versus a Firmament/geonodes recipe.
- Curvature/cavity in EEVEE: accept degrade-to-mask, or bake a curvature map so EEVEE and
  Cycles match.

## Toward the next thing

With BobShaders in, the suite has the full loop: Terrain and Scatter build the scene,
Firmament sets the world, BobShaders makes every surface obey it with strong master
materials. The natural follow-on is a lighting/look-dev or shot-assembly capability that
consumes all four; noted only so the dependency direction stays clear: BobShaders is a
consumer of the world, not a new authority over it.
