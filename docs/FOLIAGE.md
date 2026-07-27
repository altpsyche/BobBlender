# BobFoliage: trees from curves and cards, not from image-to-3D

Plan document, part built. **F1 is landed** (`core/geonodes/recipes/foliage.py`, gated by
`tools/scripts/headless_foliage.py`); F2 to F5 are still plan. Where this describes F1 the code is
the source of truth, the way it is in [SPLINES.md](SPLINES.md) and [SYSTEMS.md](SYSTEMS.md);
everything else is intent.

**Origin.** Raised out of
[COMFYUI.md's Foliage section](COMFYUI.md#foliage-what-image-to-3d-is-for-and-what-it-is-not-for),
which was written after the redwood-scene run of 2026-07-27 put the foliage limit in front of a
camera instead of in a gate table. That section is the evidence; this is the answer to it. D16
closed the near-term half (say out loud that generation makes trunks, not crowns) and left the
generator itself as its own track, which is this.

The one-line thesis: **the geometry is entirely procedural, and generation supplies the surfaces.**
An MTree/SpeedTree-class generator in Geometry Nodes — trunk, recursive branches, leaf cards — fed
by two ComfyUI texture jobs (a bark set and a leaf atlas) and nothing else. A tree is a structure
plus a texture; Bob already has the structure vocabulary (GN recipes over curves) and the texture
vocabulary (BobShaders fed by generated sets), and what is missing is the recipe that puts them
together.

**Image-to-3D supplies no geometry to this track at all.** An earlier draft had the trunk coming
optionally from `comfy_mesh`; that was wrong, and [section 2.1](#21-trunk-and-main-limbs) is the
argument. Its remaining job is dead wood — stumps, logs, snags — which is a different asset class
that happens to share the `trees` kind. See [2.7](#27-what-image-to-3d-keeps).

## 1. Why a generator instead of better prompts

From the measurements, not from taste. TRELLIS.2 returns one mesh from one image; it has no notion
of a leaf card, an atlas, or a branch hierarchy, and the opacity channel it emits only becomes a
real cutout when the plausibility rule in `gen_assets.source_opacity` fires.

On the redwood run it fired on nothing:

| Asset | Verdict | In-chart alpha |
|---|---|---|
| tree, sorrel, grass, log | `opaque` | mean 0.998, 0.00% below the floor |
| hemlock, fern | `implausible` | mean 0.816 / 0.795, 61.3% / 51.4% below the floor |

`implausible` is the guard refusing a channel that would have made those two 60% transparent, which
is correct and is also the end of the road: there is no alpha to use. So every leaf in that render
is opaque geometry, the baked normal carries no needle-scale detail (G3b: the dense mesh buys none
at these budgets), and a 44 m tree scaled up from a unit-cube mesh reads as a faceted fan with a
flared root skirt.

Two further facts decide the shape of the fix, and both are about SURFACES:

- **The route is very good at surfaces.** `comfy_texture_set` is the strong half of the suite
  (measured seam ratio 1.02 to 1.11 on the redwood sets). Bark and an atlas are both surfaces.
- **W4 already emits genuine cutout alpha** — range 0.000 to 1.000, mean 0.175 measured at G3 —
  because a subject image is matted, not voxelised. The alpha Bob cannot get out of a mesh it can
  get out of a picture, which is the whole reason cards are affordable.

What is NOT on this list is any measured competence at trunk geometry. The solids result (G3/G7:
closed shells, budget met, albedo std 0.1555) is for rocks, boulders, logs, stumps and debris, and
extrapolating it to a standing tree does not survive [2.1](#21-trunk-and-main-limbs).

So the generator consumes ComfyUI **textures** and produces **all** of its own geometry. That
inverts the current dependency and is the point.

## 2. Anatomy

Five parts, and each one already has a precedent in the suite it borrows from.

### 2.1 Trunk and main limbs

A GN sweep along a procedurally grown skeleton curve, with a generated bark texture set on it. Not a
generated mesh, and this is the part of the original sketch that was wrong. Three reasons, strongest
first:

- **A generated mesh has no skeleton, and the branch hierarchy needs one.** Branches attach to
  points on a curve with a tangent and a radius, so they can be placed, oriented, tapered and
  recursed. A 200k-triangle blob has none of that: growing a single branch off it would mean fitting
  a skeleton to an arbitrary mesh first, which is a harder problem than generating the trunk. Every
  other part of this design (levels, phyllotaxy, tip placement, LOD by depth) is defined in terms of
  that skeleton. There is no version of the generator where the trunk is opaque geometry.
- **"Good at bark" was extrapolated, not measured.** The solids result covers rocks, boulders, logs,
  stumps and debris. A log is a short cylinder at a 4,000-face budget; a 44 m standing trunk at the
  same budget is a different problem, and the one time it was tried — the redwood run — the result
  carried the flared root skirt that the scatter `Z Offset` knob now exists to hide. No measurement
  says a generated trunk reads at 2 m.
- **Bark relief at these budgets is a normal map either way.** G3b measured the dense mesh buying no
  needle-scale detail at scatter budgets; the same argument applies to bark grain. If the detail
  arrives as a texture regardless, spending 90 s and a skeleton-fitting problem on geometry for it
  buys nothing.

A swept trunk also gets three things free that a generated one cannot have: it follows the terrain
and a lean, it LODs by reducing the profile rather than by decimating, and its radius is known at
every point, which is what lets branches taper continuously into it.

Precedent: `curve_water`'s ribbon is already a swept profile along a curve with attributes written
per point, and `heightmap_terrain` already carries a texture set through a BobShader.

### 2.2 Branch hierarchy

GN recursion over curves. Per level: length, angle from parent, taper, gnarl (a noise deflection),
phyllotaxy (the rotational offset between successive children), children per parent, and a downward
or upward bias. Three levels is a tree; one is a shrub.

Blender's Geometry Nodes has no recursion primitive, so "recursion" here means a fixed stack of N
level groups, each instancing the next on the previous level's points — the same trick every GN
tree generator uses. N is a build-time param, which makes it structural and therefore a Build, not
a slider.

Precedent: the recipe scaffold (`core/geonodes/scaffold.py`, `blocks.py`, the `recipe` registry) and
the BobSplines curve vocabulary. A branch IS a typed curve with a role, and it is worth asking
whether it should literally be one.

### 2.3 Foliage: alpha cards on the tips

Cards instanced on branch tips, textured from a generated needle-spray or leaf atlas, with the atlas
cell chosen per card from a random index. This is where the alpha lives and it is the only part with
a ComfyUI dependency.

Card count per tip, card size, droop, and the random spread are the live knobs. The card is two
triangles; a needle spray is one card, a broadleaf cluster is one card, and the density comes from
the number of tips, not from geometry per card.

Precedent: `scatter`'s instancing and its Poisson/random pick, and `particulates`' camera-relative
card work.

### 2.4 Wind and season, for free

The cards read `S_EnvState` like every other BobShader, so wind sway and autumn colour come from the
[shared env](MCP.md#reading-the-scene-back) rather than from per-tree animation. Sway is a
vertex-level deflection driven by `Wind` and `Wind Direction` with a per-instance phase offset so a
stand does not pulse in unison; autumn is the same season path the rest of the suite already reads.

Precedent: `S_Weather` and `S_EnvState` (`core/materials/weather.py`), and the wind inputs already
in `particulates` and `volumetrics`.

**This is the argument for building it inside Bob rather than importing a tree library.** A tree
that does not know the weather is a tree that has to be re-authored every time the scene's season
changes, and the suite's whole shape is that one env state drives everything.

### 2.5 One tree in a panel, N variants in the world

The authoring shape, and the part that needed a decision rather than a design:

    N-panel: build ONE live tree, tune it with sliders
      -> Make Variants: bake N seeds into BOB_Assets_Trees as real objects
      -> Scatter: a normal layer, picking randomly among those N

**Scatter cannot re-seed a tree per instance, and that is a property of Blender, not a shortcut.** An
instanced object is evaluated ONCE and `Instance on Points` copies that evaluated result, so 400
instances of one GN tree are 400 identical trees whatever the seed socket says. Generating trees
inside the scatter graph instead would work and would cost full tree geometry times instance count
with no instancing benefit at all — unusable at stand density, which is the only density that
matters here.

So variants are baked ahead. That is not a workaround; it is what SpeedTree and Quixel ship, and Bob
already has the whole machinery: `BOB_Assets_Trees`, the random pick over a collection, and the
per-layer `assets_exclude` filter for dropping a variant that reads badly on a slope. Eight variants
is the working default — enough that a repeat is not findable in a frame, few enough to bake in a
minute.

Variety on top of N is continuous and free, from what scatter already does: random scale, random
yaw, the altitude and noise masks. F4 adds per-instance wind phase and autumn timing, which is the
one that matters most — identical geometry moving out of phase reads as different trees.

**Hero trees skip all of this.** A tree the camera gets close to stays a live GN object with its own
seed and its own sliders, placed by hand. Same recipe, no bake. That is the LIVE-versus-BAKED split
BobSplines already has, and it means the panel's output is useful before any variant exists.

### 2.5.1 What F1 measured

Params: 3 levels, 20 m, 9 / 5 / 4 branches, 14 trunk segments, 6 branch segments, 6-sided profile.

| Measure | Value |
|---|---|
| Curves per level (trunk, L1, L2, L3) | 1 / 9 / 45 / 180 — the stack multiplies |
| Mean curve length per level | 20.01 / 8.81 / 4.07 / 2.07 m |
| Mesh | 8,508 verts, 7,098 faces |
| Bend offset at branch bases | **exactly 0.0** across 1,410 base verts |
| Bend offset near tips | 0.3985 m |
| Two seeds | 8,508 of 8,508 verts differ |
| Vertex count against profile segments | linear (3 → 4,254, 12 → 17,016) |

The base-offset pair is the one worth keeping: the bend is weighted by each branch's own spline
factor, which is 0 at its base, so the point instanced onto the parent cannot move. A detached tree
still renders perfectly, so "it looks right" is not evidence — the number is.

Also landed in F1 and not on the original sketch: **Skeleton Only**, which emits the curves and skips
the sweep. Tuning structure is much faster without paying for the tube mesh on every slider drag, and
a detached branch is obvious in that view and invisible in the swept one.

### 2.5.2 Plants and shrubs are the same recipe

`levels=1` gives a trunk plus one branch level, which is a shrub; a grass tuft is that with no woody
taper and cards straight off the base. Same recipe, different preset, and the finished asset lands in
`BOB_Assets_Plants` or `BOB_Assets_Grass` instead. A second recipe for plants would be two copies of
one branch solver, and they would drift.

### 2.6 LODs

Card count and branch depth per level, through the existing `gen_assets` LOD chain
(`DEFAULT_LODS = (0.5, 0.15)`). A tree is the asset class where LODs matter most: it is the thing a
scatter layer instances four thousand times.

The LOD ladder for foliage is not a decimate. LOD0 is the full hierarchy, LOD1 drops the last
branch level and enlarges the cards to compensate, LOD2 is a handful of billboard cards. That is a
different function from `build_lods` and probably wants its own.

### 2.7 What image-to-3D keeps

Dead wood. Stumps, logs, snags, root balls, broken tops: solids with no skeleton to grow from, no
branch hierarchy, no leaves, and exactly the class G3/G7 measured as the route's best case. They
share the `trees` scatter kind with generated trees, which is a naming accident and not a
contradiction — a fallen log and a growing pine are different assets that both land in
`BOB_Assets_Trees`.

So `comfy_mesh(kind="trees")` keeps working and keeps its D16 note; what changes is which sentence
the note carries. "Generates a trunk, not a crown" invited exactly the trunk-shaped use this section
rejects. It should say what it is for.

## 3. What the generation track owes it

Two texture jobs and one warning:

1. **The leaf atlas.** A tileable-adjacent job: W4 with a grid layout prompt and the alpha kept,
   emitting a 2x2 or 4x4 atlas of needle sprays or leaf clusters on transparent. Closer to
   `tex_tileable` than to anything in the geometry family, and it needs no new model — the G3
   measurement above says the alpha is already there.
2. **Bark sets.** `comfy_texture_set` already produces these and needs no new workflow, but bark is
   not a neutral test of it: bark grain is strongly DIRECTIONAL (vertical on most species, spiralled
   on a few), and a tileable SDXL pass has no reason to keep an axis consistent across the wrap. The
   existing seam ratio measures continuity, not direction, so F3 needs a check for it. If the
   directionality does not hold, the fallback is a prompt clause plus a fixed orientation on the
   sweep's UVs, not a new model.
3. **The D16 guardrail**, so nobody waits for this by generating trees. Landed.

## 4. Where it plugs in, and how it is driven

### 4.1 Code surfaces

- **Recipe (F1, landed):** `foliage` under `core/geonodes/recipes/`, registered the ordinary way and
  built through `build_geonodes`, so it already works over MCP and in a headless `.blend`.
- **Ops:** the live knobs are modifier inputs and the structural ones are `build_geonodes` params,
  which is why there is no new op vocabulary and no MCP gap. **No op reads a PropertyGroup**: the
  panel's props feed params on press, exactly as the Scatter panel feeds the `scatter` recipe. That is
  deliberate — reading `ui/`-registered state from an op is what made every curve op live-bridge-only
  (the [known gap](MCP.md#known-gap-ops-that-need-the-addon)), and this track does not repeat it.
- **Assets:** a baked variant is a normal asset in the pack, so `import_generated`, the scatter layers
  and the biome manifests need no new vocabulary.

### 4.2 A panel of its own, and why that is not panel sprawl

BobFoliage gets its own N-panel for AUTHORING. The count forces it: eight trunk knobs plus six per
level is around thirty, and folding that into the Scatter panel's Active Layer would bury scatter's
own controls under a tree editor. Paths is the precedent — BobSplines has its own panel and also
feeds scatter — and this is the same shape.

What does NOT move is the decision. **Filling a kind stays one choice in one place**: Make Proxies,
Apply Biome, Generate Asset and Grow Foliage sit together in Scatter, because that is where an
artist already picks between them. So the suite gains one panel and zero duplicated decisions, which
is the subtraction rule honoured rather than broken.

### 4.3 Many trees, many species

Each authored tree is an OBJECT carrying the foliage modifier, and the panel keeps a scene-level list
with an active index — the same model `bbt_curves` uses for Paths, not a new one. A species preset is
a set of params applied to the active tree, so "add a species" is "add a tree and load a preset".

`kind` is per-tree, so a pine bakes into `BOB_Assets_Trees` and a shrub authored beside it bakes into
`BOB_Assets_Plants`. Bake several species and a scatter layer's random pick spans all of their
variants at once; the per-layer `assets_exclude` (the **Skip** field) drops any single variant that
reads badly on slopes without touching the others.

### 4.4 Textures: bring your own, or generate

Two slots, a bark set and a leaf atlas, both held as ordinary texture-set NAMES and resolved through
the ordinary pack resolver (`assets.texture_set_maps`). So artist-authored content needs no feature
at all: drop a folder in a pack and it is in the picker. There is no import step and no special case
for hand-made against generated, which is the point.

Beside each picker is a Generate button, the same pairing the Generate Asset row already uses.
Generated sets land in the generated pack, and the `pack_dir` plumbing the redwood fixes added makes
them resolvable from Blender the moment they are written. A placeholder bark set and atlas ship in
the block-out pack, so a tree is never blocked on a server.

### 4.5 Routing: how an artist knows which tool makes what

This is the part the guardrail does not yet do. `_GEN_KIND_NOTE` (`ui/scatter.py`) currently WARNS —
a greyed line under the Generate Asset kind selector saying trees are for stumps and logs — and then
leaves the artist at a dead end with no pointer to the thing that does make trees.

The routing, stated once:

| Subject | Route |
|---|---|
| Standing trees, shrubs, grass tufts | **BobFoliage** |
| Stumps, fallen logs, snags, root balls | `comfy_mesh(kind="trees")` |
| Rocks, boulders, debris | `comfy_mesh` — this is its home |
| Ground clumps read at 2 m or further | `comfy_mesh`, as scatter filler only |
| Bark, leaf atlas, duff, moss, needle litter | `comfy_texture_set` |

**Land the routing with F2, not before.** The tool grows bare branches until leaf cards exist, so a
panel that sent someone to BobFoliage for plants today would be telling them something untrue. When
F2 lands, three edits:

1. The trees note becomes a direction rather than a refusal: "stumps and logs only — grow trees in
   BobFoliage".
2. The plants and grass notes stop being about draw distance and start being about routing, while
   still allowing generated ground clumps as filler (that row above stays a yes).
3. A **Grow in BobFoliage** button appears in the same box when the kind is trees, plants or grass,
   creating a foliage object with that kind's preset. An affordance beats a sentence: the sentence is
   what an artist reads after they have already spent 90 s generating.

The loop closes in the other direction too: Make Variants reports which collection it filled, so the
artist ends up back at the Scatter panel holding the assets they just grew.

## 5. Phases

Each phase ends with something visible in a viewport and a headless check, which is the discipline
the other tracks use.

- **F1 Trunk and branches, no leaves. DONE.** The skeleton, the sweep, the level stack, the shape
  params, Skeleton Only. Gated by `tools/scripts/headless_foliage.py`, 14 checks; the numbers are in
  [2.5.1](#251-what-f1-measured). Still owed to F1 before F2 leans on it: species presets, and a
  radius that reads the parent's actual radius at the attachment point rather than a per-level
  product (the product is exact while ratios are uniform, and stops being once presets vary them).
- **F2 Cards, and the routing that depends on them.** Instancing on `bbt_fol_tip`, atlas cell pick,
  a placeholder atlas checked into the block-out pack so the phase does not block on ComfyUI. Then
  the three [4.5](#45-routing-how-an-artist-knows-which-tool-makes-what) edits, which are held until
  now because a tool with no leaves cannot honestly be recommended for plants. Check: card count,
  alpha reaching the material, a render that is not flat (the shape `headless_texset.py` already
  uses), and the gate's existing assertion that every noted kind is a real kind.
- **F3 The two texture jobs.** The atlas (W4-with-alpha into a grid, into the generated pack as a
  set with an `opacity` role) and bark. Check: the existing seam and alpha measurements, plus
  per-cell coverage on the atlas, plus a directionality measure on bark — a dominant-gradient-angle
  histogram, since a bark set whose grain wanders is unusable on a swept trunk however well it
  tiles.
- **F4 Wind and season.** `S_EnvState` into the sway and the colour, per-instance phase. Check:
  vertex displacement responds to `set_env` wind, autumn colour responds to season, a stand does not
  move in unison.
- **F5 Variants, LODs and scatter.** Make Variants (N seeds into `BOB_Assets_<Kind>`), the foliage
  LOD ladder, then a real stand scattered on a terrain at a real density. Check: no two variants
  share a vertex set, instance counts and frame time per LOD, and a render beside the redwood
  reference that started this.

## 6. Open questions

These need answering before F1, not during it.

- **Should a hand-drawn curve be able to drive the trunk?** F1 answered the general question by
  construction: skeletons are procedural curves built inside the graph, not scene datablocks, because
  a 3-level tree is 235 curves and a stand of them would bury the BobSplines curve list. What is
  still open is the narrow, useful case — a Curve OBJECT input socket so a hero tree's trunk can be
  drawn by hand and the branch solver runs on it unchanged. Cheap to add (the level stack already
  takes any curve as its level-0 parent) and worth doing only once someone wants it.
- **Species presets or one parameter set?** Every GN tree generator ends up with presets. The
  question is whether they are data (a JSON table beside the biome manifests, which is where the
  rest of the suite puts this kind of thing) or code.
- **Where does the atlas's cell layout live?** The card recipe has to know the grid, so either the
  set carries it in a sidecar or the recipe takes it as a param. The pack spec already has a place
  for sidecar metadata.
- **How much of MTree's parameter model to borrow?** It and the other GN tree generators have
  converged on roughly the same knobs, and that convergence is worth treating as prior art rather
  than re-deriving. MTree is **GPL**, so this is a question about the parameter vocabulary and the
  UX, never about lifting code — the same line THIRD-PARTY-MODELS.md draws for models. Worth an
  afternoon reading its parameter list before F1 fixes ours.

**Settled by this revision:** the trunk is a sweep, always. There is no generated-mesh trunk option,
so a tree needs no ComfyUI server for its geometry — only for the two texture sets, and both have a
block-out fallback.
