# Critique: the terrain preset system

Written after a session trying to fix three reported terrain bugs (dunes spikes, canyon/mesa/badlands
looking identical, tree weathering). Plain house style. This is a candid assessment, not a defense of
the current code.

## Verdict

The terrain system offers thirteen named presets across four families (Mountains, Canyons, Lowlands,
Dunes). In practice most of them render as the same thing: generic eroded hills. The names promise
landforms the engine cannot actually produce. Canyon does not read as a canyon, Mesa does not read as
a mesa, Badlands and Plateau are near-indistinguishable from Hills. Only Dunes (after this session's
rewrite) and the Mountains family read as their label with any conviction, and even those are soft.

The deeper problem is not any single preset. It is that the presets are knob positions on one
generator, and that one generator only makes one kind of shape. You cannot knob your way from hills to
mesas. The system also measures the wrong thing, so it reports success while shipping look-alikes.

## What actually works

- Dunes and Sand Sea, after this session. The old profile was a symmetric triangle raised to a power,
  which made vertical fins, and the defaults crammed seven tall dunes into a 90 m tile. Rewritten to a
  real transverse-dune section (long windward, steep slip face, flat interdune) with sane proportions.
  Verified in a 3D render, not just a histogram. This one is genuinely fixed.
- Mountains (alpine, glacial, foothills). Ridged multifractal plus stream-power fluvial is a
  legitimate mountain recipe, and this is the case the engine was built for. Believable, if generic.
- Hills, plains, coastal, islands. These are supposed to be gentle, so "eroded noise hills" is an
  honest answer for them. The falloff shapes (radial, gradient) give coastal and islands a real
  identity.

Everything else is the problem.

## Core architectural flaws

### 1. One base shape, scaled

Every non-dune preset starts from the same generator: `generate.generate_base`, a ridged/warped
multifractal noise. The five global knobs (Relief, Detail, Erosion, Warp, Seed) and the per-op params
only scale magnitudes of that one field: detail strength, octave count, warp amplitude, erosion
iteration counts. None of them change the *kind* of shape. The reachable output is a single manifold:
"warped noise, optionally eroded." Hills, foothills, badlands, and plateau are all points inside that
one blob, so they look like siblings because they are.

A canyon is not loud noise. A mesa is not noise with a filter on top. They are different geological
processes and need different generators, not different sliders on the hill generator.

### 2. Fluvial erosion homogenizes toward one equilibrium

The stream-power fluvial pass (`ops_erode.fluvial`) is the strongest op in the stack and it drives any
input toward the same dendritic, smoothly-graded equilibrium. That is why the one preset built around
it, Canyon, is the most convincing of the four Canyons-family entries, and why the others, which want
to *not* look fluvial, get dragged back toward it. Turning erosion up makes everything look more alike,
not less. The engine has one strong attractor and most presets fall into it.

### 3. Structural ops are cosmetic bolt-ons, applied then destroyed

Terrace and Voronoi are the only ops that could impose non-fluvial structure, and the pipeline
undermines both:

- They run *before* fluvial and thermal, which then erode the structure away, and before the final
  min-max normalize, which rescales it. A terrace bench survives as a faint step, not a cliff.
- Voronoi's "mesa" pattern is a distance field (a dome per cell), not a flat-topped cell, so using it
  as a mesa base produced a regular grid of bumps that looked like an egg carton, not tablelands.
- Terrace, until this session, could not even make a flat tread. At maximum sharpness it produced a
  smoothstep ramp, so "terraced" terrain was just softly stepped hills. I fixed the op to make flat
  treads with sharp risers, which helped, but it is still a post-hoc filter, not real strata.

Real mesas come from flat-lying resistant cap-rock over soft rock, eroded by cliff retreat (scarp
erosion), not by dendritic river incision. The engine has no cap-rock concept and no scarp-retreat
erosion, so it cannot make a mesa honestly. It can only fake a stepped hill.

### 4. Proportion is divorced from the landform

The heightfield is always normalized to [0, 1], and the physical relief (Height in metres) and Sea
Level are applied later, at mesh-build time, in `heightmap_terrain`. So the same field can be built at
7 m (dunes) or flattened to nothing. During this session a Dunes terrain showed as rounded blobs
purely because Height had drifted to -0.02. Proportion is not part of a landform's identity in this
system, but proportion *is* half of what makes a dune look like a dune or a mesa look like a mesa. A
preset that does not own its vertical scale cannot guarantee its own look.

### 5. The measurement trap (this is the trust break)

The system, and I this session, leaned on scalar statistics to judge terrain: height histograms,
gradient percentiles, laplacian, pairwise correlation between presets. I drove the correlation between
canyon/mesa/badlands/plateau from 0.93-0.98 down to about zero and reported that as a fix. The renders
were still generic hills. A number went green while the output stayed wrong.

Correlation measures whether two fields are *different from each other*. It says nothing about whether
either one *is a canyon*. Decorrelating four hills gives you four different hills, not a canyon, a
mesa, and a badland. Optimizing the proxy instead of the goal is precisely how the tool ends up
"making the user a fool": it can pass its own checks and still lie in its labels.

There is no diagnostic that asks the real question per family, for example:

- Mesa: what fraction of the area is near-flat cap sitting above near-vertical cliffs?
- Canyon: are there confined, steep-walled channels incised below flat surrounding rims?
- Badlands: is the drainage density high with low overall relief?
- Dunes: are the slip faces asymmetric and near the angle of repose?

Without those, regressions ship and nobody notices until an artist looks at the viewport.

### 6. A preset is a slider position, not a promise

`presets.json` stores each preset as neutral knob values plus an op stack. It is a starting point for
tuning, not a guaranteed landform. Nothing enforces that "Canyon" produces a canyon after the artist
touches Relief or Erosion, or even at defaults. The label is decoration.

## Recommendations

Short term, be honest with the menu:

- Do not offer landform names the engine cannot deliver. If the engine only does mountains, hills,
  coastal/islands, and dunes well, ship those and drop or clearly demote canyon/mesa/badlands/plateau
  until they are real. A smaller honest menu rebuilds trust faster than a big menu of look-alikes.

Medium term, fix the generators, not the sliders:

- Give each landform its own generative process, not a filter on shared noise.
  - Mesa/plateau: start from flat-lying layered strata (a quantized base that is genuinely flat), add
    a resistant cap layer, and erode by cliff retreat so you get flat tops with vertical scarps and
    talus aprons. Do not run dendritic fluvial over it.
  - Canyon: start from a high, near-flat uplifted surface and carve a confined incising channel
    network into it, keeping the rims flat. The current fluvial hero is close, but it should incise a
    plateau, not erode a hill.
  - Badlands: fine, dense rill erosion on soft sediment at low relief. High drainage density is the
    signature, not depth.
  - Dunes: aeolian profile (done this session).
  - Mountains: ridged plus fluvial (works, keep).

- Make proportion part of the landform. Couple a sensible height range into each generator, or clamp
  so a preset cannot be flattened or stretched into nonsense.

- Build landform-diagnostic acceptance tests, one measurable feature set per family (the questions in
  section 5), and render-in-the-loop as a required check. A preset named Canyon must pass the canyon
  diagnostic and be eyeballed in a render before it can ship. Stop trusting histograms and
  correlation as evidence of a look.

Long term:

- Consider whether a filter-stack over shared noise is the right architecture at all for categorical
  landform variety, or whether the system should carry a small set of purpose-built process models
  (fluvial, aeolian, scarp/strata, glacial) that an artist composes, with noise as texture rather than
  as the skeleton.

## Honest note on this session

Dunes is genuinely fixed and verified in 3D. The canyon-family work reduced correlation and, with the
terrace-op fix, made mesa and plateau flat-topped in my test renders, but it did not make them read as
real canyons, mesas, or badlands, because the underlying engine cannot yet produce those. That work
treated a symptom (four presets too similar) and not the disease (one generator cannot make four
landforms). The disease is what the redesign needs to address.
