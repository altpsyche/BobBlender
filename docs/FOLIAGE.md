# BobFoliage: trees from curves and cards, not from image-to-3D

**Complete and shipped**: the skeleton and its sweep, the leaf cards, the two texture sets, wind and
season, variants and the LOD ladder, and the wood shaping that stops a limb reading as a pipe
(`core/geonodes/recipes/foliage.py`, `core/foliage_build.py`, `core/foliage_variants.py`,
`ui/foliage.py` and `core/comfy.py` / `core/comfy_maps.py`, gated by
`tools/scripts/headless_foliage.py`, **262 checks**). The code is the source of truth throughout, the
way it is in [SPLINES.md](SPLINES.md) and [SYSTEMS.md](SYSTEMS.md); what remains as intent is
[Open questions](#open-questions), and none of it is on the critical path.

**One lesson is worth reading before the rest of the document.** A gate is a floor, not a ceiling.
The shape work exists because of one sentence said in front of a stand render — *the trees are very
pipe-y* — and none of the 240 checks in place at the time could have produced it, because every one of
them measured whether the recipe did what it meant to and what it meant to do was sweep a circle of
one radius along a straight line. [What stops a limb being a pipe](#what-stops-a-limb-being-a-pipe)
is the answer, and [`tools/scripts/render_foliage_gallery.py`](../tools/scripts/render_foliage_gallery.py)
is the thing that should have existed from the start: five framings of each shipped species, because a
stand shot hides a trunk behind four hundred others and every defect the shape work fixed lives
inside two metres of one tree.

**Origin.** Raised out of
[GENERATION.md's Foliage section](GENERATION.md#foliage-what-image-to-3d-is-for-and-what-it-is-not-for),
which was written after a whole-scene run of 2026-07-27 put the foliage limit in front of a
camera instead of in a gate table. That section is the evidence; this is the answer to it. The
dead-wood routing rule closed the near-term half -- say out loud that generation makes trunks, not
crowns -- and left the generator itself as a subsystem of its own, which is this.

The one-line thesis: **the geometry is entirely procedural, and generation supplies the surfaces.**
An MTree/SpeedTree-class generator in Geometry Nodes — trunk, recursive branches, leaf cards — fed
by two ComfyUI texture jobs (a bark set and a leaf atlas) and nothing else. A tree is a structure
plus a texture; Bob already has the structure vocabulary (GN recipes over curves) and the texture
vocabulary (BobShaders fed by generated sets), and what is missing is the recipe that puts them
together.

**Image-to-3D supplies no geometry to this track at all.** An earlier draft had the trunk coming
optionally from `comfy_mesh`; that was wrong, and [section 2.1](#trunk-and-main-limbs) is the
argument. Its remaining job is dead wood — stumps, logs, snags — which is a different asset class
that happens to share the `trees` kind. See [What image-to-3D keeps](#what-image-to-3d-keeps).

## Why a generator instead of better prompts

From the measurements, not from taste. TRELLIS.2 returns one mesh from one image; it has no notion
of a leaf card, an atlas, or a branch hierarchy, and the opacity channel it emits only becomes a
real cutout when the plausibility rule in `gen_assets.source_opacity` fires.

On that run it fired on nothing:

| Asset | Verdict | In-chart alpha |
|---|---|---|
| tree, sorrel, grass, log | `opaque` | mean 0.998, 0.00% below the floor |
| hemlock, fern | `implausible` | mean 0.816 / 0.795, 61.3% / 51.4% below the floor |

`implausible` is the guard refusing a channel that would have made those two 60% transparent, which
is correct and is also the end of the road: there is no alpha to use. So every leaf in that render
is opaque geometry, the baked normal carries no needle-scale detail (the dense mesh buys none at these
budgets, measured), and a 44 m tree scaled up from a unit-cube mesh reads as a faceted fan with a
flared root skirt.

Two further facts decide the shape of the fix, and both are about SURFACES:

- **The route is very good at surfaces.** `comfy_texture_set` is the strong half of the suite
  (measured seam ratio 1.02 to 1.11 over that run's sets). Bark and an atlas are both surfaces.
- **`mesh_subject` already emits genuine cutout alpha** — range 0.000 to 1.000, mean 0.175 measured —
  because a subject image is matted, not voxelised. The alpha Bob cannot get out of a mesh it can
  get out of a picture, which is the whole reason cards are affordable.

What is NOT on this list is any measured competence at trunk geometry. The solids result (closed shells, budget met,
albedo std 0.1555) is for rocks, boulders, logs, stumps and debris, and
extrapolating it to a standing tree does not survive [Trunk and main limbs](#trunk-and-main-limbs).

So the generator consumes ComfyUI **textures** and produces **all** of its own geometry. That
inverts the current dependency and is the point.

## Anatomy

Each part has a precedent in the suite it borrows from.

### Trunk and main limbs

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
  same budget is a different problem, and the one time it was tried — that whole-scene run — the
  result carried the flared root skirt that the scatter `Z Offset` knob now exists to hide. No measurement
  says a generated trunk reads at 2 m.
- **Bark relief at these budgets is a normal map either way.** The dense mesh was measured buying no
  needle-scale detail at scatter budgets; the same argument applies to bark grain. If the detail
  arrives as a texture regardless, spending 90 s and a skeleton-fitting problem on geometry for it
  buys nothing.

A swept trunk also gets three things free that a generated one cannot have: it follows the terrain
and a lean, it LODs by reducing the profile rather than by decimating, and its radius is known at
every point, which is what lets branches taper continuously into it.

Precedent: `curve_water`'s ribbon is already a swept profile along a curve with attributes written
per point, and `heightmap_terrain` already carries a texture set through a BobShader.

### Branch hierarchy

GN recursion over curves. Per level: length, angle from parent, taper, gnarl (a noise deflection),
phyllotaxy (the rotational offset between successive children), children per parent, and a downward
or upward bias. Three levels is a tree; one is a shrub.

Blender's Geometry Nodes has no recursion primitive, so "recursion" here means a fixed stack of N
level groups, each instancing the next on the previous level's points — the same trick every GN
tree generator uses. N is a build-time param, which makes it structural and therefore a Build, not
a slider.

Precedent: the recipe scaffold (`core/geonodes/scaffold.py`, `blocks.py`, the `recipe` registry) and
the BobSplines curve vocabulary. A branch resembles a typed curve with a role, and the answer is that it
must not literally be one: a 3-level tree is 235 curves, so scene-owned datablocks would bury the
Paths list under a single tree. The skeletons are curves built inside the graph.

### Foliage: alpha cards on the tips

**Shipped, and the placement rule was replaced by the wood shaping** — cards on TIPS is what made these trees read
as bare sticks with pom-poms, and [What stops a limb being a pipe](#what-stops-a-limb-being-a-pipe) has the two knobs that
replaced it. Everything else in this section is unchanged, including the count arithmetic below, which
is still what the recipe does at its defaults.

Cards instanced on branch tips, textured from a generated needle-spray or leaf
atlas, with the atlas cell chosen per card from a random index. This is where the alpha lives and it
is the only part with a ComfyUI dependency — and even that has a block-out fallback
([Textures: bring your own, or generate](#textures-bring-your-own-or-generate)), so the phase never waited on a server.

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
  the bend runs after the realize: every tip would get an identical spray down to the atlas cells;
- the cell is drawn on the duplicated cloud, where `Index` is unique per card, and stored as
  `bbt_fol_cell` so the UV stage can read it back off the realized mesh.

Two details that are not obvious and are both gated. **Droop is a direction blend, not a rotation**:
the card's axis is the tip's tangent lerped toward straight down, so at 1 every spray hangs
vertically whatever its branch was doing. An added rotation would droop an upward tip and *lift* a
downward one. And **the cards must not keep the tip's attributes**: every point attribute of a tip
rides through the duplicate and the instancing, so without clearing them every card vertex claims to
be a branch tip at the very end of a limb. `bbt_fol_tip` is zeroed and `bbt_fol_t` is rewritten to
the card's own 0-at-the-base, which is what the sway has to fall off over.

Precedent: `scatter`'s instancing and its Poisson/random pick, and `particulates`' camera-relative
card work.

### Wind and season, for free

**Shipped.** Wind and season both come from the [shared env](MCP.md#reading-the-scene-back)
rather than from per-tree animation, so a tree responds to the weather with no keyframes and no
per-tree press. What each of them turned out to be is not quite what this section predicted.

**Sway is geometry, not shading.** It is a vertex deflection in the recipe (`_sway`), one
`Set Position` on the joined mesh, and it has to be: EEVEE Next has no vertex displacement, so a
shader cannot move a leaf. The world reaches it through the two ordinary live knobs `Wind` and
`Wind Direction`, which the World applier writes onto every tree
([How the world reaches a tree](#how-the-world-reaches-a-tree)). The offset is

    downwind * Wind * Sway * 0.02 * Height * (anchor.z/Height)^2 * (0.6 + 0.4 * gust)
  + crosswind * Wind * Leaf Flutter * 0.30 * Card Size * card * t * wobble

and four details in it are load-bearing:

- **The height weight is read from `bbt_fol_anchor`, the SKELETON point a vertex belongs to**, not
  from the vertex itself. A swept ring's vertices sit at slightly different heights, so a squared
  falloff evaluated per vertex shears every cross-section — and drifts a tip ring away from the card
  standing on it. Measured before the fix: **7.7e-05 m** at `Wind` 6, eighty times the card-attachment
  residual, on a tree that rendered perfectly. With the anchor the residual in a gale is 9.67e-07 m,
  which is the card number unchanged. It is one of the eleven rows in
  [Failure modes](#failure-modes-and-the-check-that-catches-each), and it was found by writing the check.
- **The gust rides on a bias (0.6 + 0.4·sin)**, so wind pushes a tree over and then breathes about
  that lean. An unbiased sine swings it through vertical twice a cycle, which reads as a metronome.
- **The flutter is gated to the cards and weighted by `bbt_fol_t`.** That is exactly why the card work rewrote
  `bbt_fol_t` on the cards to their own 0-at-the-base: a card pivots at the twig it hangs from
  rather than through it. Measured at `Sway` 0: the wood moves **exactly 0.0** and the cards move.
- **Scene Time is the clock**, so an animation is deterministic and a still frame is still. `Wind`
  defaults to 0, which is what lets every earlier measurement stand unchanged.

`Sway` and `Leaf Flutter` are per-species — a spruce is the stiffest thing in the set at 0.45 / 0.5
and a grass tuft the loosest at 1.6 / 1.8 — so they are preset params. `Wind` and `Wind Direction`
are deliberately NOT: they belong to the world, the applier overwrites them on every change, and a
preset carrying one would be a tree bringing its own weather.

**Phase, and the claim this section had to correct.** A per-TREE phase comes from the object's own
world location (Self Object → Object Info → Location), so a stand placed by hand is out of step the
moment it is placed and a tree dragged across the scene re-phases as it goes. A per-INSTANCE phase
does not exist and cannot: an instanced object is evaluated once and the result copied. Measured —
two scatter instances of one tree differ by **9.54e-07 m**, i.e. not at all. That is the same
property [One tree in a panel, N variants in the world](#one-tree-in-a-panel-n-variants-in-the-world) already records for the seed, with
the same answer: variety across a stand comes from baking N variants, each of which carries its own
phase. The line in 2.5 promising "per-instance wind phase" was written before anyone tried it.

**Season is shading, and it did not touch a shared master.** A `S_LeafSeason` group sits between the
surface master's Base Color and the card's Principled, carrying its own driven `env_season` Value
node (a driver reads an enum as its index — measured, `autumn` drives 2.0). Autumn re-tints by
LUMINANCE rather than mixing toward a flat colour, so the atlas's light and shade survive and a
green leaf can brighten into amber instead of only darkening to brown; winter is the same turn
further along (`_WINTER_TURN` 0.55), because a leaf still on the tree in winter is a dead autumn
leaf and dropping it is geometry. The turn is staggered per card by `bbt_fol_phase` — the same
per-card random the flutter reads, so a leaf that moves as itself also turns as itself — because a
canopy that turns as one flat colour is the tell of a season swap.

Measured over three renders of one broadleaf, as mean red-minus-green across the frame: summer
**+0.0004**, winter **+0.0029**, autumn **+0.0049**; and with `Cards` 0 the bark reads +0.00125 in
both summer and autumn, so the season reaches the leaves and nothing else.

**Why S_LeafSeason is its own group and not a `Season` output on S_EnvState.** S_EnvState is
embedded by S_Weather and by S_WaterMaster; rebuilding it reassigns every socket identifier and
every embedder left un-rebuilt keeps stale links, which is why the item-3 and snow-line changes each
cost a global `S_GROUP_VER` bump and a revert-to-default on every tuned terrain in the file. A term
that reaches only leaves is not worth that. The gate holds the result as a number:
`S_GROUP_VER` is still **6** and S_SurfaceMaster's interface is untouched by this whole track.

Precedent: `S_Weather` and `S_EnvState` (`core/materials/weather.py`), and the wind inputs already
in `particulates` and `volumetrics`.

**This is the argument for building it inside Bob rather than importing a tree library.** A tree
that does not know the weather is a tree that has to be re-authored every time the scene's season
changes, and the suite's whole shape is that one env state drives everything.

### One tree in a panel, N variants in the world

**Shipped** (`core/foliage_variants.py`, and Make Variants on the panel). The authoring shape,
and the part that needed a decision rather than a design:

    N-panel: build ONE live tree, tune it with sliders
      -> Make Variants: bake N seeds into BOB_Assets_<Kind> as real objects
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

#### A variant is a LIVE GN object, and that was the hazard

"Bake" reads as "apply the modifier", and doing that would have silently deleted the whole of
[Wind and season, for free](#wind-and-season-for-free): the sway is a `Set Position` driven by Scene Time, so an
applied mesh is a tree stopped at whatever frame it was frozen on, and a still forest under a moving
sky reads as a render setting rather than as a missing feature. The measurement did not settle
this — it established that two instances AGREE at one frame (9.54e-07 m apart), which is a statement
about phase and says nothing about motion. Six numbers settled it:

| Measure | Value |
|---|---|
| An INSTANCED live-GN variant, frame 1 to 31 | **1.322786 m**, the same distance its source object moves |
| An APPLIED copy, over the same 30 frames | **exactly 0.0 m** |
| One live variant at 100 instances / at 400 | 5.87 / **5.79** ms/frame |
| Eight live variants at 100 / at 400 | 7.68 / **7.65** ms/frame |
| One live tree with no instancer at all | 5.67 ms/frame |
| Eight APPLIED meshes at 400 instances | 0.00 ms/frame |

So `Collection Info` → `Instance on Points` re-evaluates the source every frame and a baked stand
keeps its wind; and **the cost is per VARIANT and flat in instance count** — 400 instances cost what
100 do, and eight variants cost barely more than one, because Blender evaluates independent GN
objects in parallel. That is the shape that makes the choice easy: a forest is no dearer than a
copse, and the price of a stand that moves is a fixed handful of milliseconds that does not grow
with the forest. The applied stand's 0.00 ms is real rather than a measurement artifact — a mesh
with no time dependency is never re-evaluated at all — which is why the frozen route survives in
exactly one place, the pack writer ([How a variant is written into the pack](#how-a-variant-is-written-into-the-pack)).

**Variants are spread 40 m apart inside the pool, and that is not cosmetic.** A tree's phase is its
own world location, a pool is authored at the origin, and eight variants stacked at (0,0,0) share
one phase and pulse in unison — the failure that baking N variants exists to prevent, arriving by
the back door. Measured over a full gust cycle: stacked, two same-seed variants differ by **0.0 m**;
40 m apart, by **1.2227 m**. The spread costs nothing, because `Collection Info`'s Reset Children
means an instance still lands on its point (measured: a variant authored at x=40 and instanced on a
point at x=0 arrives centred on x=−0.29).

That last measurement needed care, and getting it wrong first is the useful part. Two sinusoids of
the same frequency and different phase are equal twice per cycle, so a single-frame reading is a
coin toss: at frame 1 the 40 m spread differs by 0.0087 m — the two phases happen to land on the
same point of the gust, 0.657 against 0.652 — and at frame 9 by 1.1618 m. The first version of the
check read frame 1 and failed on a recipe that was working perfectly, which is the bark-seam
mistake in the other direction. Out of step means "not always together", so the gate samples a
period.

Variety on top of N is continuous and free, from what scatter already does: random scale, random
yaw, the altitude and noise masks. **Wind phase is NOT on that list, and it was measured to find out why.** An
instanced tree is evaluated once, so every copy of one variant shares its phase (measured: two
instances differ by 9.54e-07 m). Phase varies per VARIANT, from each baked tree's own location, and
per CARD within a tree — which is where the shimmer comes from — but not per instance. Same property
as the seed, same answer: bake N. See [Wind and season, for free](#wind-and-season-for-free).

**Hero trees skip all of this.** A tree the camera gets close to stays a live GN object with its own
seed and its own sliders, placed by hand. Same recipe, no bake. That is the LIVE-versus-BAKED split
BobSplines already has, and it means the panel's output is useful before any variant exists.

### Measured: the skeleton and the cards

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

Also shipped, and not on the original sketch: **Skeleton Only**, which emits the curves and skips
the sweep. Tuning structure is much faster without paying for the tube mesh on every slider drag, and
a detached branch is obvious in that view and invisible in the swept one.

#### Two invariants that render perfectly when broken

Both rendered perfectly, and both were found by writing a check rather than by looking.

- **The radius never reached the mesh.** Blender 4.0 gave `Curve to Mesh` an explicit `Scale` input
  and stopped applying the curve's radius attribute implicitly. A graph that only calls `Set Curve
  Radius` sweeps a uniform **1 m-radius tube** — `Trunk Radius`, `Taper` and
  every per-level `Radius` ratio were inert on the geometry. It looked like a tree because a tree of
  uniform sticks at 20 m still reads as one. `bbt_fol_rad` now feeds `Scale` directly.
- **`Gnarl` was an amplitude in metres.** Invisible at 20 m and destructive at plant scale: the grass
  preset's 0.10 m stem was displaced 0.3 m, so the tuft came back **1.7 m tall and 2 m wide** — a
  plausible bush, and not a tuft. Both `Gnarl` and `Lean` are now a fraction of the limb's OWN
  length (`GNARL_SPAN`, tuned so the knob keeps the numbers the skeleton was first measured at).

Neither is a coincidence. They are the same failure this whole subsystem exists to name: *tree-shaped
is not the same as right*, and only a number tells them apart. There are nine more of the same shape,
tabulated in [Failure modes](#failure-modes-and-the-check-that-catches-each). Two of them are the
texture sets': bark shaded through a box projection, so no bark UV reached the shader at all, and an
alpha bleed that read from the silhouette's own background pixels, so it pushed white outwards and
made a *harder* halo than doing nothing. One is the wind falloff's: weighted per VERTEX it
sheared every swept ring and drifted a tip 7.7e-05 m away from the card standing on it, in a tree that
rendered perfectly — fixed by weighting from the skeleton point (`bbt_fol_anchor`) instead, at no
vertex cost.

**Two are in the variant pool, not in the recipe**, which is the one part of the subsystem that had
never been walked end to end:

- **A baked variant is not evaluated where it lives.** `BOB_Assets_<Kind>` is deliberately not
  linked to the scene — the pool shows up only as scattered instances — and an object outside the
  view layer is not evaluated at all, so `obj.evaluated_get(depsgraph).to_mesh()` on a pooled variant
  returns an EMPTY mesh rather than raising. The first end-to-end run reported four freshly baked
  variants at 0 verts each and exported four GLBs with no primitives, having said nothing. Nothing
  else caught it because INSTANCING is unaffected: `Collection Info` depends on the object, so the
  depsgraph pulls it in and the stand renders correctly at 25,284 verts. Only a direct read needs
  `foliage_variants.evaluable`, and every direct read now goes through it.
- **The world's wind reached the hero tree and not the stand.** `apply_wind` walked `scene.objects`,
  which by the same property walks straight past every pooled variant — so the tree in the viewport
  blew and the four hundred instances behind it stood still, in a scene where the wind visibly
  worked. `wind_targets` now walks the pools too.

There was a third in the family, caught in a check rather than in the code: the gate's phase check
read one frame, and two sinusoids of the same frequency and different phase are equal twice a cycle,
so it reported 0.0087 m and failed on a bake that was correct. See
[One tree in a panel, N variants in the world](#one-tree-in-a-panel-n-variants-in-the-world) — a check can be wrong in exactly the way the
code can.

**Three more belong to the wood shaping**, and all three are in
[What stops a limb being a pipe](#what-stops-a-limb-being-a-pipe): a card whose atlas does not resolve
renders as an opaque white rectangle, and the pack writer had the same bug one layer down; `Leaf Level`
unclamped makes a LOD rung bald; and a flared, lobed base sank 0.031 m below the ground plane.

**Eleven defects, and every one of them was found by writing a check rather than by looking.** That is
the claim the [failure-mode table](#failure-modes-and-the-check-that-catches-each) exists to keep
honest, and it has exactly one qualification.

**The pipe-y verdict is the qualification.** It was not a defect in any of the senses above — nothing
was broken, nothing was inert, every number was the number the recipe intended — and no check could
have produced it, because a check measures a recipe against its own intent and the intent was the
problem. It took a person looking at a render and saying so. The lesson is not that the checks were
wrong; it is that a gate is a floor and not a ceiling, and a subsystem whose deliverable is how
something LOOKS needs a gallery of it as well
([`render_foliage_gallery.py`](../tools/scripts/render_foliage_gallery.py)).

#### Measured: bark and the leaf atlas

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

### Plants and shrubs are the same recipe

`levels=1` gives a trunk plus one branch level, which is a shrub; a grass tuft is that with no woody
taper and cards straight off the base. Same recipe, different preset, and the finished asset lands in
`BOB_Assets_Plants` or `BOB_Assets_Grass` instead. A second recipe for plants would be two copies of
one branch solver, and they would drift.

Four presets ship in the block-out pack and all four are measured. **The wood shaping retuned every
one of them**, because those terms are inert in the recipe and a species preset is the only place they
mean anything ([What stops a limb being a pipe](#what-stops-a-limb-being-a-pipe)) — so these are the
shipped numbers and the earlier ones
column is kept beside them, since the difference is the phase:

| Species | Kind | Height | Width | w/h | Verts | Cards | as shipped |
|---|---|---|---|---|---|---|---|
| `conifer` | trees | 22.33 m | 9.33 m | 0.42 | 17,248 | 1,742 | 12,642 v / 1,228 c |
| `broadleaf` | trees | 12.34 m | 12.12 m | 0.98 | 23,712 | 3,303 | 7,916 v / 635 c |
| `shrub` | plants | 1.10 m | 1.20 m | 1.09 | 2,566 | 384 | 1,850 v / 205 c |
| `grass_tuft` | grass | 0.38 m | 0.52 m | 1.36 | 508 | 64 | 300 v / 30 c |

Two orders of magnitude of scale from one recipe, which is the claim this section makes, and is also
why the scale-invariance check exists: the tuft was the thing that found the metres bug.

The vertex budgets grew by 1.4× to 3× and all of it is cards, which is the trade the wood shaping makes knowingly: a
canopy is what a tree is at any distance past a few metres, and the tip-only rule was buying its low
budget by not having one. The ladder below absorbs it — a stand renders on LOD1 and LOD2 far more than
on LOD0, and both rungs came down in the same phase.

### LODs

A tree is the asset class where LODs matter most: it is the thing a scatter layer instances four
thousand times. One 3-level tree measures **8,508 verts / 7,098 faces** (profile 6) with no leaves;
the shipped conifer is **12,642 verts**, of which 4,912 are its 1,228 cards. So a 400-tree
stand at LOD0 alone is 5 M verts. The ladder is not optional.

**The foliage ladder is a REBUILD, not a decimate**, and that is the one thing to get right here.
`gen_assets.build_lods` and its `DEFAULT_LODS = (0.5, 0.15)` ratios do not apply: decimating a tree
collapses twigs into spikes and destroys the card quads, whereas rebuilding at a lower branch depth
with fewer, larger cards is both cheaper and better-looking. LOD0 is the full hierarchy; LOD1 drops
the last branch level and enlarges the cards to compensate; LOD2 is a handful of billboard cards.

Each LOD is therefore another build of the same recipe at different params, which is nearly free
because the recipe is procedural. It needs its own function, NOT `build_lods`.

#### The budgets, re-derived rather than defended

The pre-cards targets were LOD0 about 8 k verts, LOD1 about 2 k, LOD2 under 300, with a clause about
dropping the profile from 6 sides to 4. **The clause fired and the targets did not survive**, which
is what re-deriving means. The shipped conifer is 12,642 verts with its profile already at 5 and its
1,228 cards making up 39% of that, and dropping the profile is the weakest of the available levers:
it is exactly linear, so 5 sides to 4 is 1,546 verts of 12,642, where dropping a branch level is
9,322.

So the ladder drops levels, and the shipped rungs are:

    LOD0  as authored
    LOD1  levels − 1 (min 1), profile − 1 (min 3)
    LOD2  levels 1, profile 3, segments ≤ 8, branch segments ≤ 3

with `Cards` and `L1 Branches` left ALONE at LOD2, which was not the first rule tried — see below.
Measured on the four shipped species, at each rung, against LOD0:

| Species | LOD0 | LOD1 | LOD2 |
|---|---|---|---|
| `conifer` | 17,240 v | 4,768 v (27.7%) | 624 v (3.6%) |
| `broadleaf` | 23,760 v | 4,558 v (19.2%) | 288 v (1.2%) |
| `shrub` | 2,566 v | 568 v (22.1%) | 282 v (11.0%) |
| `grass_tuft` | 508 v | *(already at the floor)* | 284 v (55.9%) |

(the current numbers. The ladder ran 12,642 → 3,320 → 490 on the conifer before the presets carried a
canopy; the ratios barely moved, which is the point — the rung drops a LEVEL, and the cards go with the
level they grew on.)

**`Leaf Level` has to be clamped to the rung's depth, and this one nearly shipped broken.** A rung
rebuilds at `levels - 1`, so a species asking for leaves on level 3 asks LOD1 for a level that does not
exist; unclamped, the selection matches nothing and the rung is bare wood with its canopy silently gone.
Clamped, LOD1 of a 3-level species carries 632 cards on level 2. See
[What stops a limb being a pipe](#what-stops-a-limb-being-a-pipe).

A rung that comes back identical to LOD0 is dropped rather than shipped: `grass_tuft` is one level
on a 3-sided profile before anything is taken away, so it gets two rungs and not three, and a second
copy of LOD0 under a name promising it is cheaper never reaches the pool.

**The card enlargement is measured, not chosen, and it has two constraints.** Dropping a branch level
removes most of the tips, and a card grows on a tip, so a plain `levels-1` rebuild thins the canopy
to a fraction of its coverage — which at distance IS the tree. Total card area goes as the square of
`Card Size`, so scaling by `sqrt(area_LOD0 / area_rung)` restores it exactly, for any species, with
no constant to tune or to drift.

That rule alone is right at LOD1 and unusable at LOD2. Holding the conifer's 447 m² of canopy on 18
cards needs each card 7.4 m across, and the tree comes back **253% as wide** — a distant conifer
that is suddenly a sphere, which is a worse pop than the thinning. So the enlargement is also capped
where the crown reaches 1.15× LOD0's width, which needs no third build: crown width is very nearly
linear in card size, so the two builds already taken solve it. Every rung, every species, comes in
at or under **115%** of LOD0's width.

**What that costs at LOD2, stated rather than hidden.** The conifer holds **29.8%** of its canopy
area on 76 cards; broadleaf and shrub hold 100% and 91.5%. A narrow crown of 1,228 small cards has
no 76-card equivalent that is not a sphere, and with no impostor bake behind the rung the width has
to win. It is four times better than the rule this replaced — capping `Cards` at 2 and `L1 Branches`
at 8 gave a 168-vert conifer holding **7.5%**, a stick with leaves on it — for three times the
vertices, and it is also the simpler rule.

Frame cost per rung, on the 519-tree stand the gate builds, eight live variants each:

| Rung | Verts each | ms/frame |
|---|---|---|
| LOD0 | 12,642 | 14.43 |
| LOD1 | 3,320 | 10.05 |
| LOD2 | 490 | 7.90 |

which is the per-VARIANT cost of [One tree in a panel, N variants in the world](#one-tree-in-a-panel-n-variants-in-the-world) again: the
rung changes what a frame draws far more than what it evaluates, because the instance count is not
what the number is made of.

### Materials and UVs

The swept mesh comes back with **no UV map and no material** — `Curve to Mesh` creates neither — so
bark cannot be textured at all and neither can a card. The card work had to solve it before it could do
anything else. Solved as follows, and gated.

- **The trunk UV, in metres.** V (along the limb) is `bbt_fol_t` × `bbt_fol_plen`; U (around the
  profile) is the profile circle's own spline parameter, stored as `bbt_fol_u` on the circle BEFORE
  the sweep so `Curve to Mesh` carries it around every ring, × the local circumference
  (2π `bbt_fol_rad`). Both are then divided by `Bark Scale`, which is therefore **metres per texture
  tile**. Measured: on a 20 m trunk at 1 m per tile, V reaches 20.0, and halving the scale doubles
  it. Without the metres term a two-metre trunk and a twenty-centimetre twig would each get exactly
  one tile, so the twig's grain would be ten times too coarse — the single thing that makes a
  procedural trunk read as plastic.

  This deviates from the original brief, which asked for UVs "in 0..1". Cards are 0..1, because they index
  an atlas cell and must be; bark is deliberately not, because 0..1 and metres-based are mutually
  exclusive and this section had already chosen metres. The gate checks each on its own terms.

  Known seam, **fixed** — and it mattered more than "ordinary cylindrical-unwrap seam"
  suggested. The profile is cyclic, so its parameter runs 0 … 1−1/n and the last quad of each ring
  wraps from nearly 1 back to 0: one column per limb carrying the WHOLE texture, reversed and squeezed
  into a single quad. Measured on a 6-sided profile, **1,183 of 7,098 faces** spanned 5/6 of the
  profile where they were entitled to 1/6.

  The fix costs no vertices and no interface change (`_unwrap_u`). A UV lives on the CORNER domain and
  a corner can hold both 1 and 0 where a vertex cannot, so the wrap corners are found and pushed up by
  a whole turn: a corner is a wrap corner when its u sits far below its own FACE's mean u, which
  `Evaluate on Domain` at the face domain supplies (verified on 5.2 — a quad whose corners carried
  −1, 0, 0, −1 read back −0.5). The alternative, an open profile with n+1 points, would add a vertex
  ring per curve point and change every count already measured. After the fix all 7,098 faces span
  exactly 1/6.

  Measuring it needed care, and getting that wrong first is the useful part. The obvious check — each
  face's span in the written UV against its share of the local circumference — reported a 5× miss on a
  correctly fixed graph, because the U of a TAPERING limb genuinely spans a large range on any face
  near the wrap: the circumference it is scaled by differs between the quad's two rings. That is
  shear, it is inherent to a metres-based cylindrical UV, and it is not a seam. The gate divides the
  metres term back out and measures the RAW profile parameter, where every face is entitled to
  exactly 1/n.

- **The shear, measured and then fixed.** Acknowledging it as inherent was right about the mechanism
  and wrong about the size, and leaving it unmeasured is how it reached a hero render: the artist
  reported *bark wrapping wrong at the foot of the conifer trunk*.

  Measured per quad, in tiles of sideways slide, on the shipped conifer at the size that scene used
  — median **0.0000** and p95 **0.068** over 8,235 bark quads, and **1.713** in one band. All of it
  in the same place on both species: the bottom band of the trunk, where the ROOT FLARE takes the
  radius 0.798 → 0.416 in one step. `FLARE_SPAN` is 0.05 of the limb and a trunk resamples to 16
  points, so the whole 1.9× swell lands inside a single quad and the bark slides most of two tiles
  across it. Broadleaf measured **1.825**. Turning the flare off dropped the worst to 0.245, which
  identified the cause.

  So the flare **was** the cause, and the earlier reading that it could not be — because `_taper`
  sets the radius before `_tag` stores `bbt_fol_rad`, so the UV already accounts for it — had the
  mechanism exactly backwards. Accounting for it is what does the damage: keeping metres-per-tile
  constant around a circumference that changes that fast is only possible by sliding the texture
  sideways.

  The fix is one more attribute and no interface change. `_taper` now stores `bbt_fol_uvrad`, the
  taper radius **before** the swell, and `_sweep_uv` reads the circumference from that. Everything
  else — the sweep itself, `_lobe`, the next level's base radius — keeps the real `bbt_fol_rad`, so
  the flare stays in the silhouette and the collar still thickens where a limb meets its parent.
  After: conifer worst **0.184**, broadleaf **0.159**, and flare-on and flare-off now measure
  identically, which is the proof that the flare no longer touches the UV. The worst case moved off
  the trunk foot entirely and onto an ordinary twig taper at 15.3 m.

  The tradeoff, stated: bark over the flare is now stretched slightly rather than sheared, because
  fewer tiles wrap a wider trunk there. That is what bark over a swelling root does, and a stretch
  reads as a surface where a shear reads as a smear.

  `check_bark_shear` in `tools/scripts/headless_foliage.py` is the gate, at 0.5 of a tile, and it
  runs on the **shipped species presets** rather than on the gate's own params — the defect was a
  property of the conifer preset's flare against its segment count, and a check run on anything else
  would have missed it.

- **`profile_segments` is a hero-distance decision and it is still 5 on the conifer.** A 5-sided
  profile is 72° per facet, so a trunk is a visible pentagon at 2 m and reads as round at 40 m,
  which is what that preset was tuned for. Raising it to 12 also quarters the per-facet share of the
  residual shear (0.184 → 0.077), but it multiplies every vertex count in the recipe and the shear
  is no longer the reason to do it. If a tree is a hero at close range, raise `profile_segments` on
  that build; the shipped presets stay tuned for the scatter they were measured for.

#### The same family again: bark UVs that never reached the shader

**Bark was assigned with BOX projection, so none of the UVs above reached it.** The card work built the
metres-based bark UV in this section, measured it, and then created the bark material with
`surface_material(..., box=True)` — which samples by WORLD POSITION. So `Bark Scale` was inert on the
shader, the grain followed the world axes instead of the limb, and a leaning trunk was a slab of bark
projected through it. Box projection is the right default for `surface_material` in general (it exists
for un-UV'd props); a swept limb is precisely the case that carries real UVs and needs them.

Nobody could see it at the time because no bark set existed to put on a tree, which is the same shape as the
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

  The modifier-order rule that scene work landed ([SYSTEMS.md](SYSTEMS.md#rebuilding-in-place)) is
  sidestepped rather than obeyed: because both `Set Material` nodes are inside the recipe, this
  object needs no `BBT_Material` modifier and therefore has nothing that can end up in the wrong
  place. An `assign_material` call on a foliage object would flatten it back to one material.
- **A leaf card uses the `surface` master, with a cutout — not a fourth master.** [the cards answered.] What
  a card needs is alpha cutout, two-sided shading and some translucency. Two of the three turned out
  free: Blender shades both faces unless `use_backface_culling` is set, and EEVEE Next's `DITHERED`
  surface render method (already the default) is its cutout mode. Translucency is the only term a
  fourth master would have bought, it belongs with the season colour work rather than the geometry,
  and it is not worth a second full node group — so it waited.

  **It landed with the season layer, still with no fourth master and still outside S_SurfaceMaster** — and the argument
  turned out stronger than the one that deferred it. Three reasons, decisive last: the master is
  SHARED, so one socket on it costs a revert-to-default on every tuned terrain in the file; it is the
  matte argument above one step further, since translucency is about the leaf as a thin OBJECT and
  not as a surface, and a wet leaf must not turn transparent; and **the master's contract cannot
  express it at all** — it outputs three scalars into one Principled, and translucency is a second
  BSDF lobe. There is no socket shape on the master that carries one.

  So it lives in the card WRAPPER (`_wire_translucency`): a Translucent BSDF mixed into the surface
  at 0.25, its colour taken from the season-turned base so a backlit autumn tree glows amber. The
  part that had to be right is the gate. A plain mix of a Principled and a Translucent lights the
  whole quad, because the Principled's Alpha mattes only the Principled — every texel the atlas cut
  away comes back as glowing card and a spray renders as a bright rectangle, which reads as a
  lighting problem rather than a wiring one. The translucent branch is therefore matted against a
  Transparent BSDF by the SAME cutout socket first, so each branch is matted exactly once and the
  edges do not thin the way a doubled alpha would. The gate asserts both branches and that the two
  cutout sources are the same socket.

  The alpha comes from the set's `opacity` map if it ships one, else the basecolor image's own alpha
  channel, and goes **straight to the Principled, not through the master**. Alpha is a matte: it says
  which texels are leaf and which are the gap between leaves, whereas every term the master adds is
  about how a surface looks where it EXISTS. Routing a matte through them would let a wet leaf turn
  semi-transparent. Keeping it outside also means S_SurfaceMaster's interface is untouched, so no
  shared-group version bump and no tuned terrain in the file reset by this phase.

### What image-to-3D keeps

Dead wood. Stumps, logs, snags, root balls, broken tops: solids with no skeleton to grow from, no
branch hierarchy, no leaves, and exactly the class the geometry A/B measured as the route's best
case. They
share the `trees` scatter kind with generated trees, which is a naming accident and not a
contradiction — a fallen log and a growing pine are different assets that both land in
`BOB_Assets_Trees`.

So `comfy_mesh(kind="trees")` keeps working and keeps its dead-wood note; what changes is which sentence
the note carries. "Generates a trunk, not a crown" invited exactly the trunk-shaped use this section
rejects. It should say what it is for.

### What stops a limb being a pipe

**Shipped.** The phase that came from looking rather than from measuring, and the one place in
this track where that was the only thing that could have worked.

The verdict on the first stand was *"it's good, but the trees don't look natural — it's very pipe-y"*, and the review
that followed found the trees were pipes for reasons that were all decisions rather than bugs. Every
one of the 240 checks passed on them, and every one deserved to: the recipe swept **one circle of one
radius**, along a **straight line**, whose radius fell **linearly**, and hung all of a limb's leaves
in **one cluster at its far end**. A cylinder with a smooth radius function is a pipe no matter how
good the bark on it is, and no check that asks "did the sweep use the radius the taper set" can notice
that the radius function itself is the problem.

Six terms, in the order their payoff justifies. **All six are INERT at their defaults**, which is not
politeness — every earlier measurement was of a tree with none of them, those numbers are the contract, and the
gate's first wood-shaping check is that a default build is still **8,508 verts / 7,098 faces with a
cross-section circular to a ten-thousandth**. The shape arrives through the species presets, which is
where a shape description belongs ([Many trees, many species](#many-trees-many-species)).

| Term | What it is | Measured |
|---|---|---|
| `Leaf Level` / `Leaf Start` | which wood is leafy, and how far along it the cards start | tip-only at the defaults (940 cards on 235 tips, the cards' number); 2,628 at Leaf Start 0.4 |
| `Lobe` | radial displacement along the vertex normal, amplitude a fraction of the LOCAL radius | peak deviation **0.2004** at `Lobe` 0.2, **0.4002** at 0.4; **0 extra vertices** |
| `Flare` | a swell over the bottom `FLARE_SPAN` of the trunk: the root flare | base radius 0.4500 m → **0.8100 m** at `Flare` 0.8 (ratio 1.800); unchanged at 25% height |
| `Taper Curve` | the spline factor raised to a power before the taper is applied | mid-trunk radius 0.2440 m linear → **0.3391 m** at 2.0, tip radius unchanged |
| `L<n> Sag` | gravity per level: a downward pull weighted by the SQUARE of the spline factor | base offset still **exactly 0.0**; negative sags upward |
| `Collar` | the same swell as `Flare`, on a branch, over `COLLAR_SPAN`: the union | L1 base swells over 1.5×, and the widest collar is still thinner than its trunk |

Five of those need a sentence each about why they are the shape they are rather than the obvious one.

**The lobing goes along the vertex NORMAL, and that is what makes it one node instead of a subsystem.**
On a swept tube the normal IS the radial direction, so a single `Set Position` buys lobes around a limb
and bulges along it with none of the per-limb axis arithmetic a real radial displacement needs. It has
to sit after `_sweep_uv` and before the cards are joined, and both halves are gated: the UVs come back
**byte-identical** (a UV is on the corner domain and displacing a vertex cannot change which texel it
reads, only where that texel sits), and every card's base is still on its own anchor to **9.6e-07 m**
under a lobed sweep. The wavelength is scaled by the local radius, so a twig carries the same number of
lobes around it as a bole — the `GNARL_SPAN` argument again, and the reason the scale-invariance check
still passes.

`Lobe` is meant to read as "the peak deviation, as a fraction of the local radius", and a raw fractal
noise does not deliver that: its Fac clusters around 0.5 and reaches neither bound, so the nominal ±1
amplitude arrived as **±0.324** measured over a whole trunk. `LOBE_GAIN` is that number's reciprocal.
The knob now means what its name says, which is worth one constant.

**`Taper Curve` changes how the radius falls and never where it ends up.** At 1 the fall is linear, and
a straight line whose radius falls linearly is a cone — the thing the review was looking at. Above 1 it
falls slowly at first and steeply near the top, which is what a trunk does: near-cylindrical through
the bole, tapering out into the crown. The tip radius is `Taper`'s to say, so the gate asserts it does
not move (0.0675 m at both 1.0 and 2.0). A knob that changed both would be two knobs.

**`Flare` and `Collar` are one mechanism, and the argument for that is not economy.** Wood thickens
where it has to carry a moment; a root flare and a branch collar are the same statement at two scales,
and they differ only in how far up the limb the swell reaches (`FLARE_SPAN` 0.05 against `COLLAR_SPAN`
0.18 — a collar is a much larger fraction of a short branch than a flare is of a 22 m bole). A collar
cannot poke through its parent by construction, because a branch's base radius is already the parent's
own radius times a ratio well under 1, and the gate holds that as a number rather than as an argument.

**The sag is weighted by the SQUARE of the spline factor because a cantilever's deflection is.** A limb
therefore leaves its parent at the angle the rotation gave it and curves over further out, which is the
difference between a bough and a spoke; linear sag drops the whole limb by a constant slope and simply
re-reads as a smaller `Angle`. It is also the only wood-shaping term in Z, which is the one axis where the
attached-base invariant is at risk — so the gate checks that a sagging limb's base offset is still
exactly 0.0, on the same 1,410 base verts the skeleton check measures.

**Cards along the young wood is the biggest single win and the cheapest change in the phase.** The
tip-only rule put a 3 m bough's entire leaf allowance in one cluster at the far end, and the grass tuft
came back as fourteen woody dowels with a sprig glued to each — the clearest evidence in the whole
gallery, because a tuft has nowhere to hide. `Leaf Start` is the fraction along a leafy limb where the
cards begin and `Leaf Level` is the shallowest level that carries any, so a bole grows nothing and a
twig is covered. The epsilon in the comparison is load-bearing: `bbt_fol_t` is exactly 1.0 at a last
point, so a bare `t > Leaf Start` at the default of 1 selects nothing at all and the tree comes back
bald.

#### Three more in the same family

None of them is in the shape code, and each was found by a check rather than by looking — after the
shape work itself was found by looking, which is the phase's own joke.

- **A card whose atlas does not resolve is an opaque WHITE RECTANGLE.** The presets now name generated
  atlases, and the first end-to-end run rendered a full canopy of white quads — which reads exactly
  like the documented translucency failure and is nothing of the kind. The cause was the generated
  pack not being on the search path in a script that imports the addon without registering it; the
  defect it exposed is that the card has **no graceful floor** where bark has one. A missing bark set
  is a solid tint, which is the block-out convention ([Textures: bring your own, or generate](#textures-bring-your-own-or-generate));
  a missing atlas is no cutout and no albedo on a material whose tint is deliberately white. So
  `_atlas_set` falls back to the shipped block-out atlas and bark deliberately does not, and the
  asymmetry is the point: the two slots differ because their failures differ. The pack writer had the
  same bug one layer down — it read `params["atlas"]` raw, so a packed card exported opaque while the
  live tree beside it cut out correctly.
- **`Leaf Level` has to be clamped to the build's depth, or the LOD ladder goes bald.** A rung is a
  rebuild at `levels - 1` ([LODs](#lods)), so a species putting its leaves on level 3 asks LOD1 for a
  level that no longer exists, the selection matches nothing, and the rung is bare wood with its canopy
  silently gone — on the one asset nobody inspects closely. Clamped, LOD1 of a 3-level species carries
  **632 cards on level 2**.
- **A flared, lobed base sank below the ground plane.** A flare widens the trunk DOWNWARD, so its
  lowest ring's normal tilts down, and a displacement along that normal pushed the lowest vertex to
  **−0.031 m** — the pack writer's origin-at-the-base invariant broken, and every scattered instance
  buried by three centimetres. `LOBE_FOOT` fades the lobing in over the bottom 1.5% of a limb (0.33 m
  on a 22 m bole, invisible) and puts the lowest vertex back inside 0.005 m.

#### One check that had to be loosened, and why that is not a weakening

`check_variants` asserted every variant came back at **exactly** the same vertex budget. That was right
while a card could only grow on a tip, where the selection is an index and cannot drift. It is wrong
now: `bbt_fol_t` is `Spline Parameter`'s factor, which is an ARC-LENGTH fraction measured after the bend
and the sag have moved the points, so a different seed genuinely puts a handful of interior points on
the other side of `Leaf Start`. Measured over four seeds of the shipped conifer: **17,240 / 17,248 /
17,256** verts, a spread of 16 in 17,000. The check now allows a per-mille, because asserting exact
equality would be asserting that the seed does nothing.

#### What the two texture jobs produce

`comfy_bark_set` and `comfy_leaf_atlas` were built and measured before the wood shaping, which is the first thing that ran them
for the shipped species, which is what turns a solid-tint trunk into bark. Both took more than one
seed, and the measurements are the reason it was obvious which to keep:

| Set | Off vertical | Coherence | Block spread | Seam ratio |
|---|---|---|---|---|
| `bark_conifer`, seed 21 | 29.9° (**over the gate's 25**) | 0.524 | 1.1° | 0.995 |
| `bark_conifer`, seed 7 — shipped | **0.8°** | 0.604 | 0.3° | 1.021 |
| `bark_broadleaf`, seed 7 | 9.7° | 0.389 | 7.6° | **1.489** (a visible seam) |
| `bark_broadleaf`, seed 31 — shipped | 19.4° | 0.502 | 2.6° | **0.972** |

Two seeds, two different measures catching them: the first conifer set failed on grain direction and
the first broadleaf set on the wrap seam, and each passed everything the other failed. That is
[The grain measure, and why the seam ratio could not do it](#the-grain-measure-and-why-the-seam-ratio-could-not-do-it)'s argument arriving in practice —
neither measure alone is the check.

Three atlases as well, one per species family, each 2×2 with all four cells reaching their bottom edge:
`leaf_conifer` (cell distinctness 28.9, 74.2% clear), `leaf_broadleaf` (31.2, 76.4%) and `leaf_grass`
(26.8, 88.2%). A conifer wearing a broadleaf frond was not a geometry problem and it was the loudest
thing in the stand gallery.

**None of those five sets is checked in, and the presets name them anyway.** That is the arrangement
[Textures: bring your own, or generate](#textures-bring-your-own-or-generate) already chose for bark, extended to the atlas by the
fallback above: a generated set is regenerable in seconds, weighs several megabytes, and belongs to
whoever generated it, so what ships is the NAME. The seeds that measured well are recorded here
because they are the part that is not cheap to rediscover:

    comfy_bark_set(name="bark_conifer",    prompt="shaggy fibrous cedar bark, reddish brown",     seed=7)
    comfy_bark_set(name="bark_broadleaf",  prompt="grey brown oak bark, deep cracked ridges",     seed=31)
    comfy_leaf_atlas(name="leaf_conifer",  prompt="single spruce needle spray, dark green needles on one twig", seed=7)
    comfy_leaf_atlas(name="leaf_broadleaf",prompt="single oak leaf cluster on one short twig, green summer leaves", seed=7)
    comfy_leaf_atlas(name="leaf_grass",    prompt="single tuft of long thin green grass blades from one base", seed=7)

With none of them present a tree is the block-out state and nothing is broken: solid-tint bark, the
shipped block-out atlas on every card, and the whole shape intact — measured, the gate is
**236 of 236** in exactly that state, and the geometry numbers in this document are unaffected because
they are geometry.

## The two texture sets

**Shipped.** Two texture jobs and one warning. Neither job needed a new model or a new
workflow, which is what the section predicted; both needed something Bob-side that it did not.

1. **The leaf atlas.** `comfy_leaf_atlas`, `mesh_subject` with the alpha kept, as a set with an `opacity` role.
   The consuming side was already built and waiting: `opacity` is in `assets.TEXTURE_MAP_ROLES` and
   `materials.surface._wire_cutout` already prefers a dedicated opacity map over the basecolor's own
   alpha, so nothing downstream changed to make a generated atlas reach a card.

   **What was wrong in the sketch: "a grid layout prompt".** A diffusion model cannot be asked for a
   grid. Measured — `mesh_subject` with *"a 2 by 2 grid of four separate pine needle sprays, one spray per
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
   too, but for a Bob-side reason rather than a model one. See [Materials and UVs](#materials-and-uvs).
3. **The dead-wood routing guardrail**: `comfy_mesh(kind="trees")` is for stumps, logs and snags, so
   nobody waits for this by generating trees. Landed.

### The grain measure, and why the seam ratio could not do it

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

### Orienting a sprite, which the pixels asked for and the brief did not

A card's v is 0 at its attachment, so a sprite has to grow from the BOTTOM EDGE of its cell — the
property the placeholder atlas was hand-authored to have ([Textures: bring your own, or generate](#textures-bring-your-own-or-generate)).
`mesh_subject` does not give it. Asked for *"the cut end of the twig at the bottom of the frame, needles fanning
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

## Where it plugs in, and how it is driven

### Code surfaces

- **Recipe:** `foliage` under `core/geonodes/recipes/`, registered the ordinary way and
  built through `build_geonodes`, so it already works over MCP and in a headless `.blend`.
- **Ops:** the live knobs are modifier inputs and the structural ones are `build_geonodes` params,
  which is why there is no new op vocabulary and no MCP gap. **No op reads a PropertyGroup**: the
  panel's props feed params on press, exactly as the Scatter panel feeds the `scatter` recipe. That is
  deliberate — reading `ui/`-registered state from an op is what made every curve op live-bridge-only
  (the [known gap](MCP.md#known-gap-ops-that-need-the-addon)), and this track does not repeat it.
- **Assets:** a baked variant is a normal asset in the pack, so `import_generated`, the scatter layers
  and the biome manifests need no new vocabulary.

### A panel of its own, and why that is not panel sprawl

**Shipped** (`ui/foliage.py`, with the build layer in `core/foliage_build.py`). BobFoliage has
its own N-panel for AUTHORING, at panel order 5 — directly after the Scatter stage that routes to
it, so the artist sent here by Grow in Foliage finds it where they were pointed. **Its header is the
plain noun `Foliage`**, the way every other top-level panel in the category is (World, Biome, Paths,
Scatter, Shaders, Atmosphere): BobFoliage is this TRACK's name and the code and this document keep it,
but a header saying it was the only one of the seven that did. The routing copy below names the panel
by its header for the same reason, and the gate reads that header rather than a literal, so the two
cannot drift apart. The
count forces it: eight trunk knobs plus six per level is around thirty, and folding that into the
Scatter panel's Active Layer would bury scatter's own controls under a tree editor. Paths is the
precedent — BobSplines has its own panel and also feeds scatter — and this is the same shape.

What does NOT move is the decision. **Filling a kind stays one choice in one place**: Make Proxies,
Apply Biome, Generate Asset and Grow Foliage sit together in Scatter, because that is where an
artist already picks between them. So the suite gains one panel and zero duplicated decisions, which
is the subtraction rule honoured rather than broken.

What the panel holds, and what it deliberately does not:

| Lives on | What |
|---|---|
| The OBJECT | the tree itself, stamped `bbt_foliage`, filed in `BOB_Foliage`, with the species it was built from |
| The MODIFIER | every live knob — trunk, per level, cards, wind. Edited in place; no sync code, nothing to drift |
| `Scene.bbt_foliage` | UI state only: the active index, and the structural choices staged for the next Build |

**No panel state reaches a recipe except as a plain param.** Every operator resolves its context and
calls `core/foliage_build.py`, which imports no ui module and reads no PropertyGroup; the headless
gate builds trees through the same functions with the addon not registered at all. That is why
BobFoliage adds no MCP op and is not on the live-bridge-only list every curve op is on
([known gap](MCP.md#known-gap-ops-that-need-the-addon)) — and the gate checks it as a source fact
rather than as an intention.

### Many trees, many species

Each authored tree is an OBJECT carrying the foliage modifier. **The list is the `BOB_Foliage`
collection**, drawn with a `template_list` over its real objects with an active index in
`Scene.bbt_foliage` — the Scatter panel's model rather than Paths'. Both were available; what
decided it is that a `CollectionProperty` of Object pointers has to be kept in step with the scene,
and a collection cannot fall out of step with itself. A tree deleted in the outliner simply leaves
the list. `is_foliage` keys off the STAMP and not the collection, so a hero tree dragged into a
set-dress collection is still a tree to everything that looks for one.

A species preset is a set of params applied to the active tree, so "add a species" is "add a tree and
load a preset" — and loading one onto an existing tree keeps the object, its transform and its
identity, because `build_geonodes` rebuilds in place under the same name. Gated: same datablock,
same location, new shape.

**A species is DATA in a pack, not a dict in the recipe.** [the cards answered.] `<pack>/foliage/<name>.json`
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
`grass_tuft`, measured in [Plants and shrubs are the same recipe](#plants-and-shrubs-are-the-same-recipe).

`kind` is per-tree, so a pine bakes into `BOB_Assets_Trees` and a shrub authored beside it bakes into
`BOB_Assets_Plants`. Bake several species and a scatter layer's random pick spans all of their
Variants at once; the per-layer `assets_exclude` (the **Skip** field) drops any single variant that
reads badly on slopes without touching the others.

### Textures: bring your own, or generate

Two slots, a bark set and a leaf atlas, both held as ordinary texture-set NAMES and resolved through
the ordinary pack resolver (`assets.texture_set_maps`). So artist-authored content needs no feature
at all: drop a folder in a pack and it is in the picker. There is no import step and no special case
for hand-made against generated, which is the point.

Beside each picker is a Generate button, the same pairing the Generate Asset row already uses.
Generated sets land in the generated pack, and the `pack_dir` plumbing that scene work added makes
them resolvable from Blender the moment they are written. A placeholder atlas ships in the block-out
pack, so a tree is never blocked on a server.

**The placeholder atlas** is `textures/leaf_atlas_blockout/`, written by
`tools/scripts/make_leaf_atlas.py` and checked in — the pack has to work with no tools installed, and
the script is there so it is reproducible rather than mysterious. It is deliberately the smallest
thing that exercises every property the real atlas will have: a 2×2 grid, so a broken cell pick
cannot pass; straight alpha with a genuine cutout (**81.3% transparent**, measured, versus the
mean-0.998 opacity that made a whole-scene run's leaves solid); four visibly different sprays, so a
stuck index is visible rather than plausible; and each spray growing from the BOTTOM edge of its
cell, because a card's v is 0 at the attachment and a centred spray would float off its twig.

No placeholder BARK set ships. A bark-less tree is a solid-tint BobShader, which is the block-out
convention everywhere else in the suite, and bark is `comfy_bark_set`'s to generate — including the directionality
problem, which a hand-made placeholder would only hide. That decision held: the directionality problem
was real, and a placeholder would have hidden it ([The two texture sets](#the-two-texture-sets)).

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

### Routing: how an artist knows which tool makes what

**Shipped.** `_GEN_KIND_NOTE` (`ui/scatter.py`) used to WARN — a greyed line under the
Generate Asset kind selector saying trees are for stumps and logs — and then leave the artist at a
dead end with no pointer to the thing that does make trees.

The routing, stated once:

| Subject | Route |
|---|---|
| Standing trees, shrubs, grass tufts | **the Foliage panel** |
| Stumps, fallen logs, snags, root balls | `comfy_mesh(kind="trees")` |
| Rocks, boulders, debris | `comfy_mesh` — this is its home |
| Ground clumps read at 2 m or further | `comfy_mesh`, as scatter filler only |
| Bark, leaf atlas, duff, moss, needle litter | `comfy_texture_set` |

**Held until leaf cards existed, deliberately.** The tool grew bare branches without them, so a panel that
sent someone to the Foliage panel for plants would have been telling them something untrue. The three edits,
as shipped:

1. The trees note is a direction rather than a refusal: *"stumps and logs only; grow standing trees
   in the Foliage panel"*. It still names dead wood, which is the wording that stopped people reading
   "a trunk, not a crown" as an invitation.
2. The plants and grass notes are about routing rather than draw distance: *"ground clumps read at
   2 m; grow real plants in the Foliage panel"*. The clause keeps the filler row above a yes.
3. A **Grow in Foliage** button sits in the same box, drawn whenever the kind resolves a species,
   and builds a foliage object at the 3D cursor from that kind's preset. An affordance beats a
   sentence: the sentence is what an artist reads after they have already spent 90 s generating.
   Unlike Generate it is never greyed, because it needs no server.

The button is in the Generate Asset box rather than in a panel of its own because filling a kind
stays ONE decision in ONE place ([A panel of its own, and why that is not panel sprawl](#a-panel-of-its-own-and-why-that-is-not-panel-sprawl)):
proxies, biome, generate and grow are four ways to fill `BOB_Assets_<Kind>`, and the artist picks
between them where they already are.

Two invariants the gate holds, because this is copy and copy drifts: every kind whose note points at
the Foliage panel must actually resolve a species (a note pointing somewhere nothing grows is a worse dead
end than the refusal it replaced), and the routing half — every noted kind is a real kind, rocks carries
no note — stays owned by `headless_scene_seams.py`.

The loop closes in the other direction too: Make Variants reports which collection it
filled, so the artist ends up back at the Scatter panel holding the assets they just grew. It is
`BOB_Assets_<Kind>` for the SPECIES's kind, not for anything chosen at bake time, so a pine and a
shrub authored beside each other fill different pools with nothing to set.

**The loop is closed.** Grow in Foliage builds through `foliage_build.grow`, so the tree it
makes is the same object the Foliage panel adds — stamped, filed in `BOB_Foliage`, already listed
and already feeling the world's wind — and the operator's report says "tune it in the Foliage
panel" rather than pointing at the modifier stack. It pointed there while there was nowhere better;
a modifier stack is not an authoring surface, and it is a worse answer now that thirty knobs are
grouped and labelled one panel away.

### How the world reaches a tree

Wind arrives as VALUES written onto each tree's live knobs by the World applier
(`foliage_build.apply_world_wind`, subscribed at register), not as drivers. Firmament drives its own
wind knobs with real drivers, and that is right for a cloud layer: one object, one stable modifier.
A stand is N objects that each get a fresh modifier and fresh socket identifiers on every structural
rebuild, so N drivers would need reinstalling by every rebuild anyway. Values cost one pass over the
trees on a world change, need no cleanup when a tree is deleted, and are measurable headlessly —
a driver's value is only correct on the evaluated copy, which is exactly the kind of thing that
passes a check and renders wrong.

The build path seeds the same two knobs from `bbt_env` as well, so a tree grown over MCP into a scene
that is already blowing comes out blowing rather than waiting for the next slider drag. With no
Firmament there is no world to read, the knobs default to 0, and the tree is still — which is also
what keeps every earlier measurement valid.

Season goes the other way, through the shader: one driven Value node in a shared group, installed by
the same `install_env_drivers` that feeds S_EnvState. The two mechanisms are different because the
two effects are: one moves vertices and one changes a colour.

**"Every tree" had to be widened to mean the pools too.** The applier walked `scene.objects`, and a baked variant
lives in `BOB_Assets_<Kind>`, which is deliberately not linked to the scene — so the hero tree in
the viewport blew and the whole stand behind it stood still, in a scene where the wind demonstrably
worked. `foliage_build.wind_targets` walks the authored trees and the pools, and the gate checks the
pooled half specifically ([Measured: the skeleton and the cards](#measured-the-skeleton-and-the-cards)).

### How a variant is written into the pack

**Shipped** (`foliage_variants.write_variant_pack`), and it is the narrow writer the open
question predicted. `gen_assets.finish_asset` is the wrong tool and reaching for it would undo the
skeleton, the cards and the texture sets at once: it bakes dense-to-low (a procedural tree has no dense version), decimates (the
one thing [LODs](#lods) forbids — it spikes the twigs and destroys the card quads), Smart-UV-
projects (the bark UV is metres-based and measured and the card UV indexes an atlas cell) and
converts to a BobShader (the tree has two already). Confirmed before building rather than after, as
asked. What is reused is `origin_to_base`, `generated_dir` / `unique_asset_name`, and
`write_manifest_entry` / `write_sidecar`, and nothing else.

`origin_to_base` is worth a line of its own, because it is a no-op on the thing you would call it
on: it reads `obj.data.vertices`, and a live GN object's own mesh datablock is EMPTY, so it does
nothing at all and reports nothing. It means something only once the mesh is frozen, which is where
the writer calls it. The variants do not need it — the recipe grows a trunk from (0,0,0), and the
lowest vertex of a baked conifer sits at −0.00044 m — but that is a measurement in the gate and not
an assumption.

**A packed variant is FROZEN, and the entry says so.** glTF carries meshes and PBR materials; it
cannot carry a node group. So the export is the evaluated mesh at one frame, and the round trip
loses the two things the season layer added: the wind (an applied mesh moves 0.0 m over 30 frames) and the leaf
shader's season and translucency, which come back a plain Principled. It also splits vertices at the
UV seams — 12,642 out, 14,188 back — which is glTF's normal/UV split and not a change to the mesh.

So every entry carries a `foliage` block naming the species, the seed and the rung's params, which
makes the frozen mesh the FALLBACK rather than the record: a Bob file that can resolve the species
regrows the exact variant, alive, from two numbers. Gated, and it is an equality rather than a
resemblance — **0.00e+00 m over 12,642 verts**. That is strictly better than the GLB and costs one
dict.

One thing the writer had to do that is not about foliage at all. **The glTF exporter segfaults on
the card material.** Isolated: exporting a frozen tree with both BobShaders exits 139 at teardown,
with the bark material alone exits 0, with the card material alone exits 139, and stripping the
card's image nodes does not help — so it is the Mix Shader chain (`_wire_translucency` mattes a
Translucent against a Transparent and mixes that into the Principled) that the exporter's tree walk
cannot survive. The writer therefore swaps in plain Principleds built from the same texture sets
(`gen_assets.baked_material`, split out of `apply_baked_material` for this caller), which is both
the fix and the honest thing: it is what the export would have flattened to anyway, said out loud.
A gate that crashes AFTER printing its verdict reads as a clean run to an exit code, which is how
one generation gate hid a crash for three releases ([GENERATION.md](GENERATION.md)), so this is not
a workaround.

## Failure modes, and the check that catches each

Eleven defects, and the point of the table is that **all eleven rendered**. A tree of uniform sticks
still reads as a tree; a torn-apart grass tuft is a plausible bush; a card with no cutout is a leaf
until you look at its edge. Only a number tells them apart, which is why each row names the check.

| Failure mode | What it looks like | The check that catches it |
|---|---|---|
| the sweep ignores the curve radius | a tree of uniform 1 m sticks; `Trunk Radius`, `Taper` and every per-level ratio inert | `check_radius` -- trunk width at `trunk_radius` 0.25 / 0.50, expecting 0.250 m / 0.500 m |
| gnarl as an amplitude in metres | plant-scale presets torn apart: a 0.10 m grass stem displaced 0.3 m returns a 1.7 m by 2 m bush | `check_scale_invariance` -- the same tree at 20 m and 0.5 m, width/height 0.365 against 0.364 |
| bark assigned by box projection | `Bark Scale` inert and the grain following world axes, because no bark UV reaches the shader | `check_bark_uv` |
| the bark wrap seam | 1,183 of 7,098 faces spanning 5/6 of the profile instead of 1/6, so one column of faces carries five times its share of texture | `check_bark_uv`, per-face U span; after the fix, exactly 1/6 on all 7,098 |
| an alpha bleed read from the silhouette's own background | white pushed OUTWARD, so the halo is harder than doing nothing | the atlas cutout check, alpha range 0.000 to 1.000 |
| wind falloff weighted per vertex | every swept ring sheared, and a tip drifts 7.7e-05 m off the card standing on it, in a tree that renders perfectly | `check_wind`, tip-to-card gap; the fix weights from `bbt_fol_anchor`, at no vertex cost |
| a branch base that moves | a detached limb, which renders as a floating branch and is invisible in a crown | bend offset at branch bases must be **exactly 0.0** across 1,410 base verts |
| a card base off its tip | a spray floating away from the twig it grows on | worst card-base gap **9.6e-07 m** over 940 cards, with the base pair identified BY UV (see below) |
| a pooled variant read directly | four freshly baked variants at 0 verts each and four GLBs with no primitives, reported as success | `foliage_variants.evaluable`; instancing is unaffected, so only a direct read sees it |
| the world's wind reaching the hero tree only | the tree in the viewport blows and the four hundred instances behind it stand still | `wind_targets` walks the pools; the gate asserts pooled winds move |
| a card whose atlas does not resolve | an opaque WHITE rectangle, because the material's tint is deliberately white and the cutout comes from the set | `_atlas_set` falls back to the shipped block-out atlas; bark deliberately does not fall back |
| `Leaf Level` unclamped on a LOD rung | a bald rung: leaves requested at a depth the rung does not build | `Leaf Level` is clamped to the rung's own depth |
| a flared, lobed base below the ground plane | a tree sunk 0.031 m into the terrain | the base-offset check, after flare and lobing |
| the bark UV shearing across the flare | grain fanning out and smearing across the bottom metre of a trunk, at correct scale and correct direction everywhere else, so both existing bark measures pass | `check_bark_shear`, per-quad sideways slide: conifer 1.713 tiles before, 0.184 after, against a 0.5 bar, on the shipped presets |
| a sprite the composer could not orient | a leaf hanging by its tips, or a petiole pointing
sideways, in an atlas whose every per-cell figure passes -- the cell is full, the sprite reaches the
bottom edge, and `base_taper` reads narrow-at-the-base because a compact leaf IS narrow at whichever
edge it was set down on | `comfy_maps.sprite_orientation` and `gen_receipt.orientation_warning`: whether
the CUE could be read, not whether the result looks right. On the three atlases the second artist
rejection named it flags broadleaf 4 of 4, conifer 1 of 4 (the top-right cell, turned -171 degrees)
and grass 3 of 4 |
| a generated albedo with the light baked in | a surface that looks right in the reference and cannot be relit, plus a leaf card lit from the wrong side on whichever face is dark | `comfy_maps.flatness_report` (0.075 bar) and `mask_stops` (one stop inside the cutout); `gen_receipt.flatness_warning` carries the verdict |

Two of these are worth reading as method rather than as bugs.

**Measuring a card's attachment needs care, and the first two attempts were both wrong.** The quad is
`Card Size x Card Width` wide and stands on its base edge, so its two base CORNERS are half a width
either side of the tip and are supposed to be. The first check measured corners and reported a 0.15 m
gap on a perfectly attached card, 0.15 m being exactly half the card width. The second picked the base
pair by proximity to the nearest tip, which is wrong in a dense crown, where a card's free corner is
regularly nearer a NEIGHBOURING tip than its own base is to its own. The base pair is identified by UV,
because the card's own v runs 0 at the base.

**A check can be wrong in exactly the way the code can.** The wind-phase check read one frame, and two
sinusoids of the same frequency and different phase are equal twice a cycle, so it reported 0.0087 m and
failed a bake that was correct. See
[One tree in a panel, N variants in the world](#one-tree-in-a-panel-n-variants-in-the-world).

### What the gate covers, and what it costs

`tools/scripts/headless_foliage.py`, **262 checks**, of which the 25 in the generation half print SKIP
and exit 0 with no server -- the geometry never needs one, which is the whole point of the subsystem.

    blender --background --factory-startup --python tools/scripts/headless_foliage.py -- --no-gen

It covers the skeleton and its sweep (curve counts per level, mean length, vertex count against
profile segments, two seeds differing on all 8,508 verts), the cards (940 on 235 tips, 934 distinct
normals, all four atlas cells drawn at most-used/least-used 1.09), the materials and UVs, wind and
season, the variants and the LOD ladder, and the wood-shaping terms -- 22 checks asserting each is
inert at its default, which is what lets every earlier number stand unchanged.

A panel is as gateable as a recipe, and this one is gated: the build layer imports no `ui` and reads
no `PropertyGroup`, so the gate grows trees through the same functions the panel does (the
[known gap](MCP.md#known-gap-ops-that-need-the-addon) every curve op is on, and the one thing this
subsystem has stayed off by construction). Adding a tree leaves another tree's tuned knobs alone; a
structural rebuild keeps them; loading a species keeps the tree's transform AND its object identity;
and the World applier alone is enough to move every tree's wind, pools included.

Measured end to end on a warm 5080: a 2x2 atlas in 9.9 s, a bark set in 4.9 s, both resolving through
the ordinary pack resolver onto a preset-built conifer that renders at luminance range 0.48.

The stand: 8 variants at 17,256 verts, **945 trees over 260 m** drawing on all 8, written to
`_generated/foliage_stand.png` beside the whole-scene run's own eye-level frame, which is what
started this. Reproduce it with `tools/scripts/render_foliage_stand.py`, and shoot the per-species contact
sheets with `tools/scripts/render_foliage_gallery.py`.

## Open questions

Status only: **Answered**, **Open**, or **Deferred, and why**. The two still open are neither on the
critical path nor blocking anything.

- **Answered.** Is the 1.0 in-mask stops bar right for a needle spray, or should it be per-species?
  Neither: the bar was measuring two different things at once and there does not need to be a
  species table. A conifer sprig spans 1.143 stops inside its cutout, of which at most 0.48 is light
  — the rest is one needle shadowing the next, which is real geometry a flat card cannot carry and
  therefore belongs in the albedo, which is the artist's own reading of it.
  `comfy_maps.mask_light_split` fits a plane to log luminance over the masked texels and reports the
  plane's span (the light ramping across the sprite) apart from its residual (the sprite's own
  relief). Only the ramp is gated, at `gen_receipt.LEAF_RAMP_STOPS_MAX`. The proof that this is a split
  rather than a reweighting is that a synthetic key baked across the same sprites moves the ramp
  figure stop for stop and leaves the detail figure unchanged to two decimal places. Five atlases
  were generated chasing the old bar, and the prompt rewrite that finally moved it is what broke the
  sprite orientation — an unmeasured property regressing while a measured one improved, which is the
  failure class this whole section is about.

- **Answered.** What master does a leaf card use? The `surface` master with a cutout wired
  straight to the Principled, not a fourth master. See
  [Materials and UVs](#materials-and-uvs) for what that bought and what it deferred.
- **Answered.** Species presets: data or code? Data, in the pack. See
  [Many trees, many species](#many-trees-many-species).
- **Answered.** Where does the atlas's cell layout live? In the SET, as a `meta.json` sidecar
  (`atlas: {cols, rows}`), read by `assets.atlas_grid()` and used as the recipe's default. The
  `Atlas Columns` / `Atlas Rows` params stay as the override, per the brief: a hand-made atlas may
  ship no sidecar, and an artist may deliberately read a 4×4 as 2×2 to use only its bottom row. Every
  generated atlas records its own grid, so the ordinary case now needs no numbers at all. The failure
  this removes is silent by construction — a card reading 2×2 off a 4×4 atlas samples a quarter of the
  cell it wanted plus slices of three neighbours, which renders as foliage.
- **Answered.** Does the bark seam matter? Yes, and more than the phrase "ordinary
  cylindrical-unwrap seam" suggested: 1,183 of 7,098 faces carried five times their share of the
  texture. Fixed for no vertices and no interface change; the measurement, the fix, and the wrong
  first version of the check are in [Materials and UVs](#materials-and-uvs).
- **Answered.** Who builds the Foliage panel? It ships with the wind and season work, and it kept
  **Make Variants off it** at first rather than shipping it greyed: a button that does nothing teaches
  an artist to distrust every other button beside it, and the affordance is worth less than the trust. That held — the panel was
  complete without it, because a hero tree is a finished deliverable on its own
  ([One tree in a panel, N variants in the world](#one-tree-in-a-panel-n-variants-in-the-world)) — and
  the button arrived later **with the thing it does**, in a Variants sub-panel with the count, the LOD
  toggle and the pack write, reporting which `BOB_Assets_<Kind>` it filled so the artist ends up back at Scatter holding what they just grew.
- **Answered.** Does translucency belong on the surface master? No, and for a reason stronger
  than the version-bump cost: the master outputs three scalars into one Principled and translucency
  is a second BSDF lobe, so there is no socket on it that could carry one. It is wired in the card
  wrapper next to the alpha, matted by the same cutout. See
  [Materials and UVs](#materials-and-uvs).
- **Answered.** What writes a variant into the pack? A narrow writer reusing `origin_to_base`,
  the pack write and the manifest entry, and nothing else — confirmed before building rather than
  after. `finish_asset` would have undone the skeleton, the cards and the texture sets at once. The answer grew one part the
  question did not anticipate: because glTF carries no node group, the entry records the species and
  the seed as well as the mesh, so the frozen GLB is the fallback and two numbers are the record.
  See [How a variant is written into the pack](#how-a-variant-is-written-into-the-pack).
- **Answered.** Is a baked variant alive or applied? Alive. An instanced live-GN tree is still
  re-evaluated per frame (1.322786 m of sway between frame 1 and 31, against 0.0 m for an applied
  copy), and the cost of that is per VARIANT and flat in instance count. See
  [One tree in a panel, N variants in the world](#one-tree-in-a-panel-n-variants-in-the-world).
- **Answered.** Why did none of the 240 checks say the trees were pipes? Because a check measures
  a recipe against its own intent, and the intent was the defect: one circle of one radius, swept along
  a straight line, with a linear taper and all the leaves at the ends. The answer is not more checks of
  that kind — it is that a subsystem whose deliverable is an appearance needs a gallery as well as a
  gate, and it has one. See [What stops a limb being a pipe](#what-stops-a-limb-being-a-pipe).
- **Answered.** Should a preset be allowed to name a set no pack provides? For BARK yes, as
  [Textures: bring your own, or generate](#textures-bring-your-own-or-generate) already said; for an ATLAS no, and the difference is the failure rather than the principle. A
  bark-less trunk is a solid tint and a block-out convention; an atlas-less card is an opaque white
  rectangle, because its material's tint is deliberately white and its cutout comes from the set. So
  `_atlas_set` falls back to the shipped block-out atlas.
- **Open, and cheap whenever.** How much of MTree's parameter model to borrow? It and the other GN tree generators
  have converged on roughly the same knobs, and that convergence is worth treating as prior art
  rather than re-deriving. MTree is **GPL**, so this is a question about the parameter vocabulary and
  the UX, never about lifting code — the same line THIRD-PARTY-MODELS.md draws for models. A first
  vocabulary is already fixed; reading theirs is a revision, not a prerequisite.
- **Deferred, and why.** Should a hand-drawn curve be able to drive the trunk? The general question is
  answered by construction: skeletons are procedural curves built inside the graph, not scene datablocks, because
  a 3-level tree is 235 curves and a stand of them would bury the BobSplines curve list. What is
  still open is the narrow, useful case — a Curve OBJECT input socket so a hero tree's trunk can be
  drawn by hand and the branch solver runs on it unchanged. Cheap to add (the level stack already
  takes any curve as its level-0 parent) and worth doing only once someone wants it.

**Settled and not to be reopened without new evidence:** every wood-shaping term is inert at its
default and is turned on by a species preset, because the earlier measurements are the contract and a
shape description belongs to a species; the lobing is a normal displacement after the UVs and before the cards, and it
adds no vertices; a leaf atlas falls back and a bark set does not. And a variant is a live GN object and
not an applied mesh, and variants are spread out in the pool so each carries its own phase; the LOD ladder
is a rebuild and never a decimate, and its card enlargement is fitted to the canopy's area and
capped by the crown's width. The trunk is a sweep, always — there is no
generated-mesh trunk option, so a tree needs no ComfyUI server for its geometry, only for the two
texture sets, and both have a block-out fallback. Scatter picks among baked variants; it does not
re-seed per instance. Plants are the same recipe at a lower depth, not a second one.

**The shipped defaults grow a narrow conifer, and that is gated at width/height under 0.45.** An
earlier `_LEVEL_DEFAULTS` grew a crown about 13 m wide on a 20 m trunk, a spreading broadleaf and not
the narrow conifer this subsystem was started by. The lever is `length` and the levels below it
compounding: a level-1 branch is a fraction of the trunk's
length, so 0.42 was an 8.4 m arm. Measured: 0.42 / 0.46 / 0.50 spans 10.9 m on a 20 m trunk and
0.115 / 0.30 / 0.38 spans 6.6 m. `Lean` and `Gnarl` came down with them, because a wandering trunk
widens a crown as much as a long limb does and a conifer's bole is straight. Width/height is 0.33 and
the gate refuses anything over 0.45, so it cannot drift back silently. The defaults are still only the floor for
a bare build — a real tree comes from a species preset.
