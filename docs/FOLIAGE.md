# BobFoliage: trees from curves and cards, not from image-to-3D

Plan document, part built. **F1, F2 and F3 are landed** (`core/geonodes/recipes/foliage.py` and
`core/comfy.py` / `core/comfy_maps.py`, gated by `tools/scripts/headless_foliage.py`, 126 checks);
F4 and F5 are still plan. Where this describes F1 to F3 the code is the source of truth, the way it is
in [SPLINES.md](SPLINES.md) and [SYSTEMS.md](SYSTEMS.md); everything else is intent.

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
that happens to share the `trees` kind. See [2.8](#28-what-image-to-3d-keeps).

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

Each part has a precedent in the suite it borrows from.

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
the BobSplines curve vocabulary. A branch resembles a typed curve with a role, and F1 settled that it
must not literally be one: a 3-level tree is 235 curves, so scene-owned datablocks would bury the
Paths list under a single tree. The skeletons are curves built inside the graph.

### 2.3 Foliage: alpha cards on the tips

**Landed at F2.** Cards instanced on branch tips, textured from a generated needle-spray or leaf
atlas, with the atlas cell chosen per card from a random index. This is where the alpha lives and it
is the only part with a ComfyUI dependency — and even that has a block-out fallback
([4.4](#44-textures-bring-your-own-or-generate)), so the phase never waited on a server.

Card count per tip, card size, droop, and the spread are the live knobs. The card is two triangles;
a needle spray is one card, a broadleaf cluster is one card, and the density comes from the number
of tips, not from geometry per card.

The route is tips → a point cloud → `Cards` duplicates of each point → one quad on each, and each
hop earns itself:

- the tips become a POINT CLOUD (a single point instanced on the `bbt_fol_tip` selection, then
  realized) because they are the last points of splines, and neither `Duplicate Elements` nor a
  per-tip index means anything useful on a curve's point domain;
- `Duplicate Elements` gives one point per card, so every card is aimed and textured on its own.
  Building an N-card cluster once and instancing THAT would be cheaper and wrong for the same reason
  F1's bend runs after the realize: every tip would get an identical spray down to the atlas cells;
- the cell is drawn on the duplicated cloud, where `Index` is unique per card, and stored as
  `bbt_fol_cell` so the UV stage can read it back off the realized mesh.

Two details that are not obvious and are both gated. **Droop is a direction blend, not a rotation**:
the card's axis is the tip's tangent lerped toward straight down, so at 1 every spray hangs
vertically whatever its branch was doing. An added rotation would droop an upward tip and *lift* a
downward one. And **the cards must not keep the tip's attributes**: every point attribute of a tip
rides through the duplicate and the instancing, so without clearing them every card vertex claims to
be a branch tip at the very end of a limb. `bbt_fol_tip` is zeroed and `bbt_fol_t` is rewritten to
the card's own 0-at-the-base, which is what F4's sway has to fall off over.

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

### 2.5.1 What F1 and F2 measured

Params: 3 levels, 20 m, 9 / 5 / 4 branches, 14 trunk segments, 6 branch segments, 6-sided profile.

| Measure | Value |
|---|---|
| Curves per level (trunk, L1, L2, L3) | 1 / 9 / 45 / 180 — the stack multiplies |
| Mean curve length per level | 20.00 / 2.40 / 0.73 / 0.28 m |
| Mesh, cards off | 8,508 verts, 7,098 faces |
| Bend offset at branch bases | **exactly 0.0** across 1,410 base verts |
| Bend offset near tips | 0.1597 m |
| Two seeds | 8,508 of 8,508 verts differ |
| Vertex count against profile segments | linear (3 → 4,254, 12 → 17,016) |
| Cards at 4 per tip | 940 on 235 tips, one quad each |
| Card base against its tip | worst gap **9.6e-07 m** over 940 cards |
| Card orientations | 934 distinct normals over 940 cards |
| Atlas cells drawn (2×2) | all four, most-used / least-used **1.09** |
| Trunk width at `trunk_radius` 0.25 / 0.50 | 0.250 m / 0.500 m |
| Same tree at 20 m and at 0.5 m | width/height 0.365 against 0.364 |

Three of those are invariants rather than facts, and each one fails silently:

- **A branch base never moves.** The bend is weighted by each branch's own spline factor, which is 0
  at its base, so the point instanced onto the parent cannot move. A detached tree still renders
  perfectly, so "it looks right" is not evidence — the number is.
- **A card's base sits on its tip.** The same invariant one level down. Measuring it needs care: the
  quad is `Card Size × Card Width` wide and stands on its base edge, so its two base CORNERS are
  half a width either side of the tip and are supposed to be — the midpoint between them is the
  attachment. The first version of the check measured corners and reported a 0.15 m gap on a
  perfectly attached card, 0.15 m being exactly half the card width. The second picked the base pair
  by proximity to the nearest tip, which is wrong in a dense crown, where a card's free corner is
  regularly nearer a NEIGHBOURING tip than its own base is to its own. The base pair is identified by
  UV, because the card's own v runs 0 at the base.
- **The same params at another size give the same tree.** Which is what a species preset means.

Also landed in F1 and not on the original sketch: **Skeleton Only**, which emits the curves and skips
the sweep. Tuning structure is much faster without paying for the tube mesh on every slider drag, and
a detached branch is obvious in that view and invisible in the swept one.

#### Two things F2 found that F1 shipped broken

Both rendered perfectly, and both were found by writing a check rather than by looking.

- **The radius never reached the mesh.** Blender 4.0 gave `Curve to Mesh` an explicit `Scale` input
  and stopped applying the curve's radius attribute implicitly. F1 only ever called `Set Curve
  Radius`, so every F1 tree was swept as a uniform **1 m-radius tube** — `Trunk Radius`, `Taper` and
  every per-level `Radius` ratio were inert on the geometry. It looked like a tree because a tree of
  uniform sticks at 20 m still reads as one. `bbt_fol_rad` now feeds `Scale` directly.
- **`Gnarl` was an amplitude in metres.** Invisible at 20 m and destructive at plant scale: the grass
  preset's 0.10 m stem was displaced 0.3 m, so the tuft came back **1.7 m tall and 2 m wide** — a
  plausible bush, and not a tuft. Both `Gnarl` and `Lean` are now a fraction of the limb's OWN
  length (`GNARL_SPAN`, tuned so the knob keeps the numbers F1 was measured at).

Neither is a coincidence. They are the same failure the whole track exists to name: *tree-shaped is
not the same as right*, and only a number tells them apart. F3 found a third one of the same family
(bark shaded through a box projection, so no bark UV reached the shader at all) and one of its own
making (an alpha bleed that read from the silhouette's own background pixels, so it pushed white
outwards and made a *harder* halo than doing nothing). Tally, by the phase that FOUND them rather than
the one that shipped them: F2 found two of F1's, F3 found one of F2's and one of its own.

#### What F3 measured

| Measure | Value |
|---|---|
| Bark U per face, before the seam fix | **1,183 of 7,098** faces spanned 5/6 of the profile, not 1/6 |
| Bark U per face, after | exactly 1/6 on all 7,098; 0 faces over 1.6× their share |
| Bark grain, shipped clause | **5.7°** off vertical, coherence 0.490, block spread 1.2° over 16 blocks |
| Bark grain, no clause | 83.8° off vertical (mud cracks) / 18.3° at coherence **0.018** (no grain) |
| Bark seam ratio | 0.987 |
| Generated 2×2 leaf atlas | 82.7% clear, 8.4% opaque; per-cell coverage 10.3 to 14.8% |
| Cells reaching their bottom edge | 4 of 4; base/middle width 0.03 to 0.14 |
| Most-similar pair of cells | 23.5 / 255 mean alpha apart |
| The grid prompt, one 1024 frame | **5** sprays in a ring; 0 of 4 cells bottom-anchored |
| Wall clock, warm 5080 | 2×2 atlas 9.9 s, bark set 4.9 s, compose + derive 0.18 s |

### 2.5.2 Plants and shrubs are the same recipe

`levels=1` gives a trunk plus one branch level, which is a shrub; a grass tuft is that with no woody
taper and cards straight off the base. Same recipe, different preset, and the finished asset lands in
`BOB_Assets_Plants` or `BOB_Assets_Grass` instead. A second recipe for plants would be two copies of
one branch solver, and they would drift.

F2 shipped this as four presets in the block-out pack and measured all four:

| Species | Kind | Height | Width | w/h | Verts | Cards |
|---|---|---|---|---|---|---|
| `conifer` | trees | 23.3 m | 7.4 m | 0.32 | 12,642 | 1,228 |
| `broadleaf` | trees | 13.6 m | 10.9 m | 0.80 | 7,916 | 635 |
| `shrub` | plants | 1.27 m | 1.18 m | 0.93 | 1,850 | 205 |
| `grass_tuft` | grass | 0.40 m | 0.45 m | 1.15 | 300 | 30 |

Two orders of magnitude of scale from one recipe, which is the claim this section makes, and is also
why the scale-invariance check exists: the tuft was the thing that found the metres bug.

### 2.6 LODs

A tree is the asset class where LODs matter most: it is the thing a scatter layer instances four
thousand times. F1 measured one 3-level tree at **8,508 verts / 7,098 faces** (profile 6) with no
leaves; F2's shipped conifer is **12,642 verts**, of which 4,912 are its 1,228 cards. So a 400-tree
stand at LOD0 alone is 5 M verts. The ladder is not optional.

**The foliage ladder is a REBUILD, not a decimate**, and that is the one thing to get right here.
`gen_assets.build_lods` and its `DEFAULT_LODS = (0.5, 0.15)` ratios do not apply: decimating a tree
collapses twigs into spikes and destroys the card quads, whereas rebuilding at a lower branch depth
with fewer, larger cards is both cheaper and better-looking. LOD0 is the full hierarchy; LOD1 drops
the last branch level and enlarges the cards to compensate; LOD2 is a handful of billboard cards.

Each LOD is therefore another build of the same recipe at different params, which is nearly free
because the recipe is procedural. It needs its own function, NOT `build_lods`.

Working budget targets, to be confirmed at F5 rather than assumed: LOD0 about 8 k verts, LOD1 about
2 k, LOD2 under 300. If LOD0 cannot be held near 8 k with cards included, the profile drops from 6
sides to 4 before anything else gives.

**F2's numbers say that clause will fire.** The shipped conifer is at 12.6 k with the profile already
down to 5, and the cards are 39% of it — so the honest reading is that the 8 k target was set before
cards existed and F5 should re-derive it rather than defend it. The cheapest levers, measured: the
profile is exactly linear in vertex count (3 → 4,254, 12 → 17,016), and cards are 4 verts each times
tips times `Cards`, so halving either halves that share outright.

### 2.7 Materials and UVs, which F2 added

F1's swept mesh came back with **no UV map and no material** — `Curve to Mesh` creates neither — so
bark could not be textured at all and neither could a card. F2 had to solve it before it could do
anything else. Solved as follows, and gated.

- **The trunk UV, in metres.** V (along the limb) is `bbt_fol_t` × `bbt_fol_plen`; U (around the
  profile) is the profile circle's own spline parameter, stored as `bbt_fol_u` on the circle BEFORE
  the sweep so `Curve to Mesh` carries it around every ring, × the local circumference
  (2π `bbt_fol_rad`). Both are then divided by `Bark Scale`, which is therefore **metres per texture
  tile**. Measured: on a 20 m trunk at 1 m per tile, V reaches 20.0, and halving the scale doubles
  it. Without the metres term a two-metre trunk and a twenty-centimetre twig would each get exactly
  one tile, so the twig's grain would be ten times too coarse — the single thing that makes a
  procedural trunk read as plastic.

  This deviates from the F2 brief, which asked for UVs "in 0..1". Cards are 0..1, because they index
  an atlas cell and must be; bark is deliberately not, because 0..1 and metres-based are mutually
  exclusive and this section had already chosen metres. The gate checks each on its own terms.

  Known seam, **fixed at F3** — and it mattered more than "ordinary cylindrical-unwrap seam"
  suggested. The profile is cyclic, so its parameter runs 0 … 1−1/n and the last quad of each ring
  wraps from nearly 1 back to 0: one column per limb carrying the WHOLE texture, reversed and squeezed
  into a single quad. Measured on a 6-sided profile, **1,183 of 7,098 faces** spanned 5/6 of the
  profile where they were entitled to 1/6.

  The fix costs no vertices and no interface change (`_unwrap_u`). A UV lives on the CORNER domain and
  a corner can hold both 1 and 0 where a vertex cannot, so the wrap corners are found and pushed up by
  a whole turn: a corner is a wrap corner when its u sits far below its own FACE's mean u, which
  `Evaluate on Domain` at the face domain supplies (verified on 5.2 — a quad whose corners carried
  −1, 0, 0, −1 read back −0.5). The alternative, an open profile with n+1 points, would add a vertex
  ring per curve point and change every count F1 and F2 measured. After the fix all 7,098 faces span
  exactly 1/6.

  Measuring it needed care, and getting that wrong first is the useful part. The obvious check — each
  face's span in the written UV against its share of the local circumference — reported a 5× miss on a
  correctly fixed graph, because the U of a TAPERING limb genuinely spans a large range on any face
  near the wrap: the circumference it is scaled by differs between the quad's two rings. That is shear,
  it is inherent to a metres-based cylindrical UV, and it is not a seam. The gate divides the metres
  term back out and measures the RAW profile parameter, where every face is entitled to exactly 1/n.

#### The third defect the F1/F2 family shipped

**Bark was assigned with BOX projection, so none of the UVs above reached it.** F2 built the
metres-based bark UV in this section, measured it, and then created the bark material with
`surface_material(..., box=True)` — which samples by WORLD POSITION. So `Bark Scale` was inert on the
shader, the grain followed the world axes instead of the limb, and a leaning trunk was a slab of bark
projected through it. Box projection is the right default for `surface_material` in general (it exists
for un-UV'd props); a swept limb is precisely the case that carries real UVs and needs them.

Nobody could see it at F2 because no bark set existed to put on a tree, which is the same shape as the
inert radius and the metres-based gnarl: it renders, and only a number tells. That makes three defects
in this family across two phases, all found by writing a check rather than by looking.
- **The card UV** is the quad's own 0..1 (the `Mesh Grid` node's `UV Map` output, stashed as
  `bbt_fol_cuv` so it survives instancing) pushed into one cell of the atlas grid: `(cuv + (col,
  row)) / (cols, rows)`, row-major from the bottom-left, which is how the placeholder atlas is
  authored. Both UVs are stored on the **corner domain**, which is the whole difference between a UV
  layer and a float2 nobody reads: a point-domain float2 named `UVMap` does not appear in
  `mesh.uv_layers` at all.
- **Two materials on one object,** via two `Set Material` nodes in SERIES on the joined mesh — bark
  over everything, then the card material over the faces flagged `bbt_fol_leaf`. Not one per branch
  before the join: both work, but Join Geometry prepends, so the per-branch version's slot order
  depends on link order. Measured on Blender 5.2, and the plan's "0 for the swept skeleton, 1 for the
  cards" is not quite what happens: the evaluated mesh comes back with **slot 0 empty** — the base
  mesh's own implicit no-material slot, which `Set Material` appends after and which no face
  references — then bark at 1 and cards at 2. Also measured, and the reason it has to be done inside
  the graph at all: a GN-generated mesh **ignores the object's material slots entirely**, so
  `Set Material Index` alone renders grey.

  The modifier-order rule the redwood work landed ([SYSTEMS.md](SYSTEMS.md#rebuilding-in-place)) is
  sidestepped rather than obeyed: because both `Set Material` nodes are inside the recipe, this
  object needs no `BBT_Material` modifier and therefore has nothing that can end up in the wrong
  place. An `assign_material` call on a foliage object would flatten it back to one material.
- **A leaf card uses the `surface` master, with a cutout — not a fourth master.** [F2 answered.] What
  a card needs is alpha cutout, two-sided shading and some translucency. Two of the three turned out
  free: Blender shades both faces unless `use_backface_culling` is set, and EEVEE Next's `DITHERED`
  surface render method (already the default) is its cutout mode. Translucency is the only term a
  fourth master would have bought, it belongs with the season colour work rather than the geometry,
  and it is not worth a second full node group — so it waits for F4.

  The alpha comes from the set's `opacity` map if it ships one, else the basecolor image's own alpha
  channel, and goes **straight to the Principled, not through the master**. Alpha is a matte: it says
  which texels are leaf and which are the gap between leaves, whereas every term the master adds is
  about how a surface looks where it EXISTS. Routing a matte through them would let a wet leaf turn
  semi-transparent. Keeping it outside also means S_SurfaceMaster's interface is untouched, so no
  shared-group version bump and no tuned terrain in the file reset by this phase.

### 2.8 What image-to-3D keeps

Dead wood. Stumps, logs, snags, root balls, broken tops: solids with no skeleton to grow from, no
branch hierarchy, no leaves, and exactly the class G3/G7 measured as the route's best case. They
share the `trees` scatter kind with generated trees, which is a naming accident and not a
contradiction — a fallen log and a growing pine are different assets that both land in
`BOB_Assets_Trees`.

So `comfy_mesh(kind="trees")` keeps working and keeps its D16 note; what changes is which sentence
the note carries. "Generates a trunk, not a crown" invited exactly the trunk-shaped use this section
rejects. It should say what it is for.

## 3. What the generation track owes it

**Delivered at F3.** Two texture jobs and one warning. Neither job needed a new model or a new
workflow, which is what the section predicted; both needed something Bob-side that it did not.

1. **The leaf atlas.** `comfy_leaf_atlas`, W4 with the alpha kept, as a set with an `opacity` role.
   The consuming side was already built and waiting: `opacity` is in `assets.TEXTURE_MAP_ROLES` and
   `materials.surface._wire_cutout` already prefers a dedicated opacity map over the basecolor's own
   alpha, so nothing downstream changed to make a generated atlas reach a card.

   **What was wrong in the sketch: "a grid layout prompt".** A diffusion model cannot be asked for a
   grid. Measured — W4 with *"a 2 by 2 grid of four separate pine needle sprays, one spray per
   quadrant, each growing upward from the bottom of its quadrant"* returned **five** sprays arranged
   in a ring, straddling every cell boundary, each pointing a different way, none touching a cell's
   bottom edge. Per-cell coverage passed anyway (8 to 11% opaque in all four quadrants), which is
   exactly why coverage alone is not the check. So Bob generates ONE sprite per cell and composes the
   grid in numpy (`comfy_maps.atlas_compose`). That makes the layout a guarantee instead of a hope,
   makes the cells differ by construction, and costs less: four sprites at 512 measured 9.4 s against
   7.3 s for one 1024 frame that could not be used.
2. **Bark sets.** `comfy_bark_set` — `texture_variant` unchanged, plus one prompt clause and one new
   measurement. The section's prediction held exactly: **directionality does not survive on its own,
   and the fallback it named is the fix.** The clause is measured rather than chosen, over two species
   and two seeds (worst case, degrees off vertical):

   | Clause | Worst | Mean |
   |---|---|---|
   | `vertical bark, deep furrows running top to bottom` | **17.6** | 9.7 |
   | `vertical grain running straight up and down` | 71.3 | 30.1 |
   | `deep vertical furrows and ridges, grain parallel to the trunk axis, top to bottom` | 84.8 | 31.1 |
   | no clause at all | 83.8 | 33.4 |

   Naming the FEATURE and its direction is what works; naming the direction alone is not enough, and
   adding more words made it worse. `comfy.BARK_SUFFIX` carries the winner and a unit test pins it,
   because a well-meant rewrite of that string is a silently plastic trunk.

   The other half of the section — "a fixed orientation on the sweep's UVs" — turned out to be needed
   too, but for a Bob-side reason rather than a model one. See [2.7](#27-materials-and-uvs-which-f2-added).
3. **The D16 guardrail**, so nobody waits for this by generating trees. Landed.

### 3.1 The grain measure, and why the seam ratio could not do it

The existing `seam_report` measures CONTINUITY across the wrap. `comfy_maps.grain_report` measures
DIRECTION: the dominant gradient axis by doubled-angle (structure-tensor) averaging, turned 90 degrees
into the axis the features themselves run along, plus a coherence and a per-block spread.

Doubled-angle because grain is an axis and not a direction — a furrow edge points left on one side and
right on the other, so averaging raw angles cancels them to nothing. Validated on images whose answer
is known: a vertical sine grating reads 0.0 degrees off vertical at coherence 1.000, a horizontal one
reads 90.0, white noise reads coherence 0.003.

**Two failures, and neither measure catches both.** This is the finding that shaped the check:

| Bark | Off vertical | Coherence | What it actually was |
|---|---|---|---|
| `rough conifer bark`, no clause | 83.8 deg | 0.487 | polygonal mud cracks — strongly coherent, wrong axis |
| `grey beech bark`, no clause | 18.3 deg | **0.018** | no grain at all; the angle was luck |
| with the shipped clause | 1.6 to 17.6 deg | 0.41 to 0.48 | bark |

So a coherence threshold alone passes the mud cracks and an angle threshold alone passes the
isotropic tile. The gate holds three numbers: angle under 25 degrees, coherence over 0.15, per-block
spread under 20 degrees. The shipped clause measured 5.7 / 0.490 / 1.2 on the gate's own run.

### 3.2 Orienting a sprite, which the brief did not ask for and the pixels did

A card's v is 0 at its attachment, so a sprite has to grow from the BOTTOM EDGE of its cell — the
property the placeholder atlas was hand-authored to have ([4.4](#44-textures-bring-your-own-or-generate)).
W4 does not give it. Asked for *"the cut end of the twig at the bottom of the frame, needles fanning
upward"*, it returned sprays lying diagonally with the twig at the LEFT, on which a card attaches by
its side and reads as a leaf growing sideways out of a branch.

Bob rotates each sprite instead (`comfy_maps.orient_sprite`), and the useful part is how the
attaching end is identified, because two heuristics were tried and the first was not good enough:

- **The principal axis's narrow end.** A spray is a bare twig at one end and a fan at the other, so
  the narrow end attaches. Measured over a whole half of the axis the needles dilute it to a coin
  toss (a symmetric spray separated 46.5 against 53.7); measured over the outer fifth the same spray
  separates 4.3 against 32.4, so `AXIS_END_BAND` is a fifth. This is species-neutral and it is the
  fallback.
- **The woody/green split.** The direction from the green centroid to the woody centroid points at
  the end that attaches, which is the question rather than a proxy for it. On the four sprites of one
  generated atlas it disagreed with the geometric answer on exactly the two cells that came out
  attached by their needle tips, and agreed on the two that came out right — so it is primary. It is
  strictly better on a spray whose twig sticks out SIDEWAYS from its fan, because that sprite's long
  axis is the fan and no end of it is the stem, so no rotation of the geometric axis can be correct.

The colour cue assumes a colour, so it is guarded (a woody fraction between 1% and 40%, centroids at
least 2.5% of the diagonal apart) and falls back to geometry outside those — which is what keeps an
autumn or dead-foliage atlas from being oriented by hue. `base_taper` in `atlas_cells` measures the
result either way, so a sprite this gets backwards is reported rather than hidden.

One more thing the pixels forced. The atlas basecolor is written as RGB with the matte as a separate
`opacity` map, so a fully transparent texel's COLOUR is load-bearing: bilinear filtering blends it
into every silhouette, and a generation's transparent region is the studio background. Unflooded, every
needle came back with a white rim. `alpha_bleed` floods leaf colour outward, and its own bug is worth
recording because it was visible before it was measured: reading from any texel above alpha 0.02
includes the anti-aliased silhouette's own near-transparent BACKGROUND pixels, so the flood pushed
white outwards and produced a *harder* halo than doing nothing. The floor is 0.9 (`ATLAS_OPAQUE`).

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

**Owned by [F4](#5-phases); not built yet.** BobFoliage gets its own N-panel for AUTHORING. The
count forces it: eight trunk knobs plus six per level is around thirty, and folding that into the
Scatter panel's Active Layer would bury scatter's own controls under a tree editor. Paths is the
precedent — BobSplines has its own panel and also feeds scatter — and this is the same shape.

What does NOT move is the decision. **Filling a kind stays one choice in one place**: Make Proxies,
Apply Biome, Generate Asset and Grow Foliage sit together in Scatter, because that is where an
artist already picks between them. So the suite gains one panel and zero duplicated decisions, which
is the subtraction rule honoured rather than broken.

### 4.3 Many trees, many species

Each authored tree is an OBJECT carrying the foliage modifier, and the panel keeps a scene-level list
with an active index — the same model `bbt_curves` uses for Paths, not a new one. A species preset is
a set of params applied to the active tree, so "add a species" is "add a tree and load a preset".

**A species is DATA in a pack, not a dict in the recipe.** [F2 answered.] `<pack>/foliage/<name>.json`
with a `meta` block (name, description, kind) and a `params` block, resolved over the same search
path as biomes and texture sets, read by `assets.foliage_species()`. Both answers were defensible and
the suite has precedent for each: scatter's `LAYER_TYPES` and the terrain layer presets are Python
dicts. What decided it is that those describe the TOOL and a species describes CONTENT — a spruce is
the same kind of thing as a texture set or a biome, it is what a pack would want to ship, and it is
what an artist would want to hand someone. A dict in the recipe can be shipped by exactly one party.

The reader filters `params` to the recipe's known keys, which makes a typo droppable rather than
silently ignored downstream, and `assets.validate_foliage_species()` reports the drop along with the
other quiet failures: an inert `l3_*` block on a two-level species, an unknown `meta.kind`, a leaf
atlas or bark set that no pack provides. The block-out pack ships `conifer`, `broadleaf`, `shrub` and
`grass_tuft`, measured in [2.5.2](#252-plants-and-shrubs-are-the-same-recipe).

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
them resolvable from Blender the moment they are written. A placeholder atlas ships in the block-out
pack, so a tree is never blocked on a server.

**The placeholder atlas** is `textures/leaf_atlas_blockout/`, written by
`tools/scripts/make_leaf_atlas.py` and checked in — the pack has to work with no tools installed, and
the script is there so it is reproducible rather than mysterious. It is deliberately the smallest
thing that exercises every property the real atlas will have: a 2×2 grid, so a broken cell pick
cannot pass; straight alpha with a genuine cutout (**81.3% transparent**, measured, versus the
mean-0.998 opacity that made the redwood run's leaves solid); four visibly different sprays, so a
stuck index is visible rather than plausible; and each spray growing from the BOTTOM edge of its
cell, because a card's v is 0 at the attachment and a centred spray would float off its twig.

No placeholder BARK set ships. A bark-less tree is a solid-tint BobShader, which is the block-out
convention everywhere else in the suite, and bark is F3's to generate — including the directionality
problem, which a hand-made placeholder would only hide. That decision held: the directionality problem
was real, and a placeholder would have hidden it ([3](#3-what-the-generation-track-owes-it)).

**So the tree presets NAME the bark they want and resolve it when it exists.** `conifer` asks for
`bark_conifer`, `broadleaf` for `bark_broadleaf`, and `comfy_bark_set(name=...)` writes under exactly
that name into the generated pack — after which every tree of that species is wearing it with no
assignment step anywhere, because the recipe resolves a set by name through the ordinary pack search
path. Before it is generated the tree is a solid tint, which is the intended block-out state.

That makes "the named set is absent" a STATE rather than an authoring mistake, and the validator has to
say so differently: `assets.foliage_missing_sets()` reports those separately, and
`validate_foliage_species()` includes them with wording that names the tool that makes them. A caller
asking "is this preset well authored" filters them out; a caller asking "what does this tree still
need" reads exactly them.

### 4.5 Routing: how an artist knows which tool makes what

**Landed with F2.** `_GEN_KIND_NOTE` (`ui/scatter.py`) used to WARN — a greyed line under the
Generate Asset kind selector saying trees are for stumps and logs — and then leave the artist at a
dead end with no pointer to the thing that does make trees.

The routing, stated once:

| Subject | Route |
|---|---|
| Standing trees, shrubs, grass tufts | **BobFoliage** |
| Stumps, fallen logs, snags, root balls | `comfy_mesh(kind="trees")` |
| Rocks, boulders, debris | `comfy_mesh` — this is its home |
| Ground clumps read at 2 m or further | `comfy_mesh`, as scatter filler only |
| Bark, leaf atlas, duff, moss, needle litter | `comfy_texture_set` |

**Landed with F2, not before.** The tool grew bare branches until leaf cards existed, so a panel that
sent someone to BobFoliage for plants would have been telling them something untrue. The three edits,
as shipped:

1. The trees note is a direction rather than a refusal: *"stumps and logs only; grow standing trees
   in BobFoliage"*. It still names dead wood, which is the D16 wording that stopped people reading
   "a trunk, not a crown" as an invitation.
2. The plants and grass notes are about routing rather than draw distance: *"ground clumps read at
   2 m; grow real plants in BobFoliage"*. The clause keeps the filler row above a yes.
3. A **Grow in BobFoliage** button sits in the same box, drawn whenever the kind resolves a species,
   and builds a foliage object at the 3D cursor from that kind's preset. An affordance beats a
   sentence: the sentence is what an artist reads after they have already spent 90 s generating.
   Unlike Generate it is never greyed, because it needs no server.

The button is in the Generate Asset box rather than in a panel of its own because filling a kind
stays ONE decision in ONE place ([4.2](#42-a-panel-of-its-own-and-why-that-is-not-panel-sprawl)):
proxies, biome, generate and grow are four ways to fill `BOB_Assets_<Kind>`, and the artist picks
between them where they already are.

Two invariants the gate holds, because this is copy and copy drifts: every kind whose note points at
BobFoliage must actually resolve a species (a note pointing somewhere nothing grows is a worse dead
end than the refusal it replaced), and the D16 half — every noted kind is a real kind, rocks carries
no note — stays owned by `headless_redwood.py`.

The loop closes in the other direction too: Make Variants reports which collection it filled, so the
artist ends up back at the Scatter panel holding the assets they just grew. That half is F5's.

**Not yet built: the BobFoliage panel itself** ([4.2](#42-a-panel-of-its-own-and-why-that-is-not-panel-sprawl)),
which **F4 owns**. Grow in BobFoliage creates the object and selects it, and its thirty-odd knobs are
live on the object's Geometry Nodes modifier, which is where a Blender user can already reach them —
so the button is honest and the tree is tunable, but the modifier stack is not an authoring surface
and the operator's own report says where the knobs are rather than pretending otherwise.

## 5. Phases

Each phase ends with something visible in a viewport and a headless check, which is the discipline
the other tracks use.

- **F1 Trunk and branches, no leaves. DONE.** The skeleton, the sweep, the level stack, the shape
  params, Skeleton Only. Gated by `tools/scripts/headless_foliage.py`; the numbers are in
  [2.5.1](#251-what-f1-and-f2-measured). Its three debts were paid at F2: species presets, a radius
  that reads the parent's actual radius at the attachment point rather than a per-level product, and
  a shipped `_LEVEL_DEFAULTS` that is a narrow conifer instead of a spreading broadleaf (crown
  width/height **0.65 → 0.33** on a 20 m trunk). Writing those checks also turned up the two defects
  F1 had shipped invisibly — the inert radius and the metres-based gnarl.
- **F2 Cards, and the routing that depends on them. DONE.** Instancing on `bbt_fol_tip`, the atlas
  cell pick, the UVs and two materials [2.7](#27-materials-and-uvs-which-f2-added) that everything
  else needed first, a placeholder atlas in the block-out pack so the phase did not block on
  ComfyUI, and the three [4.5](#45-routing-how-an-artist-knows-which-tool-makes-what) edits, held
  until now because a tool with no leaves cannot honestly be recommended for plants. The gate is 79
  checks, and no new op, MCP tool or panel state: the cards are modifier knobs and the presets are
  `build_geonodes` params.
- **F3 The two texture jobs. DONE.** `comfy_leaf_atlas` (W4 per cell, composed Bob-side, into the
  generated pack as a set with an `opacity` role) and `comfy_bark_set` (W1 plus a measured grain
  clause), both in [3](#3-what-the-generation-track-owes-it). Neither needed a new model or a new
  workflow; both needed something Bob-side that the plan had not predicted — a grid cannot be
  prompted for, and a generated sprite has to be oriented ([3.2](#32-orienting-a-sprite-which-the-brief-did-not-ask-for-and-the-pixels-did)).

  It also paid for two things F2 had left: the bark UV wrap seam, and the box projection that meant
  no bark UV reached the shader at all ([2.7](#27-materials-and-uvs-which-f2-added)). And it answered
  both of its open questions — the atlas grid lives in the set's sidecar, and the seam mattered.

  The gate is `tools/scripts/headless_foliage.py` at **126 checks**, of which the 25 in the generation half
  prints SKIP and exits 0 with no server. Measured end to end on a warm 5080: a 2×2 atlas in 9.9 s,
  a bark set in 4.9 s, both resolving through the ordinary pack resolver onto a preset-built conifer
  that renders at luminance range 0.48.
- **F4 Wind, season, and the panel.** `S_EnvState` into the sway and the colour, per-instance phase,
  plus card translucency (deferred here from F2, where it was the one term a fourth master would
  have bought). Check: vertex displacement responds to `set_env` wind, autumn colour responds to
  season, a stand does not move in unison.

  **F4 also owns the BobFoliage panel** ([4.2](#42-a-panel-of-its-own-and-why-that-is-not-panel-sprawl),
  [4.3](#43-many-trees-many-species)) — the scene-level tree list with an active index, the species
  picker, the structural-versus-live split, and the two texture-set pickers with their Generate
  buttons. It sits here rather than at F5 for three reasons. F4 is the smaller phase and can carry
  it. F5's Make Variants needs an ACTIVE TREE to bake, which is the list this panel owns, so putting
  the panel later means F5 builds both. And the order is the artist's order: you author a tree, then
  you bake variants of it — building the authoring surface after the bake is backwards.

  Its own checks, since a panel is as gateable as a recipe: the props feed `build_geonodes` params on
  press and **no operator reads a PropertyGroup** (the [known gap](MCP.md#known-gap-ops-that-need-the-addon)
  every curve op is on, and the one thing this track has stayed off by construction); adding a tree
  and switching the active index does not disturb another tree's tuned knobs; and loading a species
  onto an existing tree keeps its transform and its object identity, because a preset is params
  applied to a tree, not a new tree.
- **F5 Variants, LODs and scatter.** Make Variants (N seeds into `BOB_Assets_<Kind>`), the foliage
  LOD ladder, then a real stand scattered on a terrain at a real density. Check: no two variants
  share a vertex set, the per-LOD budgets in [2.6](#26-lods), instance counts and frame time per LOD,
  origin at the base on every variant (`gen_assets.origin_to_base`, or a scattered tree sits buried),
  and a render beside the redwood reference that started this.

  **Open before F5 starts:** what writes a variant into the pack. `gen_assets.finish_asset` is built
  for a GENERATED mesh — it bakes dense-to-low, decimates, unwraps and converts to a BobShader, and a
  procedural tree needs none of that: its UVs are known, its LODs are rebuilds, and it already has its
  materials. Calling it would undo F1's work. Expect a narrow writer that reuses only the pack write,
  the manifest entry and `origin_to_base`, and confirm that before building rather than after.

## 6. Open questions

Each is tagged with the phase that forces it. None blocks starting F3.

- **[F2, answered] What master does a leaf card use?** The `surface` master with a cutout wired
  straight to the Principled, not a fourth master. See
  [2.7](#27-materials-and-uvs-which-f2-added) for what that bought and what it deferred.
- **[F2, answered] Species presets: data or code?** Data, in the pack. See
  [4.3](#43-many-trees-many-species).
- **[F3, answered] Where does the atlas's cell layout live?** In the SET, as a `meta.json` sidecar
  (`atlas: {cols, rows}`), read by `assets.atlas_grid()` and used as the recipe's default. The
  `Atlas Columns` / `Atlas Rows` params stay as the override, per the brief: a hand-made atlas may
  ship no sidecar, and an artist may deliberately read a 4×4 as 2×2 to use only its bottom row. Every
  generated atlas records its own grid, so the ordinary case now needs no numbers at all. The failure
  this removes is silent by construction — a card reading 2×2 off a 4×4 atlas samples a quarter of the
  cell it wanted plus slices of three neighbours, which renders as foliage.
- **[F3, answered] Does the bark seam matter?** Yes, and more than the phrase "ordinary
  cylindrical-unwrap seam" suggested: 1,183 of 7,098 faces carried five times their share of the
  texture. Fixed for no vertices and no interface change; the measurement, the fix, and the wrong
  first version of the check are in [2.7](#27-materials-and-uvs-which-f2-added).
- **[F4, answered] Who builds the BobFoliage panel?** F4, which now says so in its phase entry. It
  was briefly unowned between F2 and this line, which is the state in which a described feature
  quietly never gets built. What is still genuinely open is one detail of it: whether **Make
  Variants** appears on the panel at F4, greyed with a "F5" note, or only arrives with F5. Prefer the
  second — a button that does nothing teaches an artist to distrust the panel.
- **[F5] What writes a variant into the pack?** See the F5 phase note; `finish_asset` is the wrong
  tool and reaching for it would undo F1 and F2 both — the tree's UVs, LODs and materials are all
  already correct, and `finish_asset` bakes, decimates and unwraps.
- **[anytime] How much of MTree's parameter model to borrow?** It and the other GN tree generators
  have converged on roughly the same knobs, and that convergence is worth treating as prior art
  rather than re-deriving. MTree is **GPL**, so this is a question about the parameter vocabulary and
  the UX, never about lifting code — the same line THIRD-PARTY-MODELS.md draws for models. F1 fixed a
  first vocabulary already; reading theirs is a revision, not a prerequisite.
- **[later] Should a hand-drawn curve be able to drive the trunk?** F1 answered the general question by
  construction: skeletons are procedural curves built inside the graph, not scene datablocks, because
  a 3-level tree is 235 curves and a stand of them would bury the BobSplines curve list. What is
  still open is the narrow, useful case — a Curve OBJECT input socket so a hero tree's trunk can be
  drawn by hand and the branch solver runs on it unchanged. Cheap to add (the level stack already
  takes any curve as its level-0 parent) and worth doing only once someone wants it.

**Settled and not to be reopened without new evidence:** the trunk is a sweep, always — there is no
generated-mesh trunk option, so a tree needs no ComfyUI server for its geometry, only for the two
texture sets, and both have a block-out fallback. Scatter picks among baked variants; it does not
re-seed per instance. Plants are the same recipe at a lower depth, not a second one.

**Fixed at F2: the shipped defaults.** F1's `_LEVEL_DEFAULTS` grew a crown about 13 m wide on a 20 m
trunk, a spreading broadleaf and not the narrow conifer this track was started by. The lever turned
out to be `length` and the levels below it compounding: a level-1 branch is a fraction of the trunk's
length, so 0.42 was an 8.4 m arm. Measured, 0.42 / 0.46 / 0.50 spans 10.9 m on a 20 m trunk and
0.115 / 0.30 / 0.38 spans 6.6 m; `Lean` and `Gnarl` came down with them, because a wandering trunk
widens a crown as much as a long limb does and a conifer's bole is straight. Width/height is now 0.33
and gated at under 0.45, so it cannot drift back silently. The defaults are still only the floor for
a bare build — a real tree comes from a species preset.
