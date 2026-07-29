# forest-barn: gate A asset manifest (rebuild)

**Almost none of these files exist.** The generated pack was wiped for a clean-slate rebuild, so most
of this is a record of prompts, seeds and measured numbers rather than an inventory. Every asset here
is reproducible: the tools take the same prompt and seed and return the same result. Read it for what
worked, what a given seed produced, and what the numbers were to beat.

The ONE asset that exists as of 2026-07-29 is the barn off the block-out route, the bold row below.
Everything else has to be regenerated, and the order matters now in a way it did not before: see the
gate-A ordering exception in [HANDOVER.md](HANDOVER.md), because Omni leaves the card unable to run
the atlas route in the same session.

Everything below was generated 2026-07-29 after gate A was rejected and the pipeline defects were
fixed. Prompts are the artist half; each tool appends its own framing, tiling or grain clause.

## Meshes

Every one screened as a reference first (`subject_only`, about 5 to 15 s) and only then paid for
in geometry. `bnd` is boundary edges on the mesh that SHIPPED, welded; the bar is 1% of faces.
`fidelity` is the baked basecolor against the texture it came from; the bar is 0.99.

| Asset | Seed | Faces | Measured size (m) | src bnd | closed | shipped bnd | % | fidelity | warns |
|-------|------|-------|-------------------|---------|--------|-------------|---|----------|-------|
| Barn (hero) | 5 | 8617 | 8.74 x 7.08 x 7.50 | 288 | 271 | 17 | 0.20% | 0.9974 | none |
| **Barn, block-out route** | **57** | **7924** | **8.78 x 7.83 x 7.50** | **0** | -- | 1072 unwelded, **0 welded** | **0%** | **0.9990** | **none** |

The block-out row is the one to read for the barn and it is the only asset in this file that exists
right now (`packs/generated/models`, `BarnShed`, staged at
`_staging/a_small_weathered_dark_charcoal_timber_b_s57_02`). Its prompt, which the mesh rows above
never recorded and should have:

> a small weathered dark charcoal timber barn with a simple gable roof and one straight ridge,
> standing alone, the whole building seen from one corner so that both its long side and its gable
> end are visible at once, flat even overcast light with no cast shadows, the entire structure well
> inside the frame

negative: `frontal, straight-on, head-on, elevation, flat facade, cropped, close-up, partial view,
gambrel, curved roof, two-pitch roof, dramatic sky, sunset, storm clouds, sun shadows, silvered,
galvanised, corrugated metal, shiny, display plinth, scale model, toy`

Five references were screened for it and three were rejected on sight, all three for the SAME reason
and all three under the earlier wording ("seen from a three-quarter angle"): SDXL's barn prior is
head-on. s5 and s23 came back dead-on with the building clipped at the frame edge, s11 came back a
cropped close-up of a wall -- which is the picture the last round wasted a whole generation on.
Naming the angle as a property of the SUBJECT ("seen from one corner so that both its long side and
its gable end are visible at once") plus `frontal, straight-on, head-on, elevation` in the NEGATIVE is
what fixed it: s41 and s57 both came back true three-quarter views. s41 was passed over for a gambrel
roof against a gable block-out; s57 is a simple gable with one straight ridge and a doorway in the
gable end, which is the block-out's own shape.

Control: `_generated/forest_barn/barn_control.glb`, from `make_blockout(shape="shed")` at
8.7 x 7.7 x 7.5 m, 31 faces, 0.9% hidden surface. `control_mode="point"`, 8192 points.
Geometry 50.1 s, simplify 2.0 s, texture 16.0 s. **Not hero**: the card had 7,246 MiB against the
7,000 floor and had already OOMed twice that session, so this is a 1024 texture where the rejected
barn was 2048. A hero re-shoot is a cheap follow-up on a fresh server.

Measured against the block-out: footprint IoU **0.7807**, aspect [1.0, 1.0077, 0.9913], profile max
band deviation **0.0753** against a 0.10 bar where a synthetic A-frame scores 0.2551. Albedo spread
**29.72**, mean 35.1, against 0.00 / 0.00 for the same route before the normalise fix.
`bake_fidelity` 0.9990 at 1.0 of 255. `metalness` effective 0.0053. Origin at the base, `uv_overlap`
0.0, LODs [7924, 3961, 1187], `warnings` empty and this time that is true rather than uninformative.

**What is still wrong with it, and it is the surface rather than the shape.** See
[HANDOVER.md](HANDOVER.md): `mesh_texture` paints from one reference view and invents every surface
that view cannot see, so the roof slope carries door panels and X-bracing, and the gable siding reads
as stucco rather than as vertical boards.
| Stump | 7 | 6364 | 1.67 x 1.68 x 1.10 | 293 | 256 | 37 | 0.58% | 0.9995 | none |
| Fallen log | 7 | 3962 | 0.79 x 2.15 x 0.75 | 48 | 44 | 4 | 0.10% | 0.9993 | none |
| Boulder | 7 | 3880 | 1.46 x 1.11 x 1.30 | 40 | 40 | 0 | 0.00% | 0.9995 | none |
| Small rock | 31 | 3337 | 0.60 x 0.53 x 0.42 | 297 | 243 | 54 | 1.6% | 0.9978 | **open** |

## Textures

| Set | Seed | Delit | Seam | Flatness | Other | Warns |
|-----|------|-------|------|----------|-------|-------|
| forest floor | 3 | yes | 0.989 | 0.0662 (0.0989 as generated) | -- | none |
| forest moss | 3 | no | 0.947 | 0.0355 | -- | none |
| path band | 3 | no | 0.939 | 0.0492 | -- | none |
| wet granite | 3 | no | 0.891 | 0.0740 | -- | none |
| barn siding (spare) | 3 | no | 0.794 | 0.0742 | -- | none |
| `bark_conifer` | 13 | no | 1.046 | 0.0393 | grain 14.43 deg | none |
| `bark_broadleaf` | 31 | no | 0.932 | 0.0667 | grain 1.31 deg | none |
| `leaf_conifer` | 29 | yes | -- | 1.143 in-mask stops | distinctness 24.2 | **lit** |
| `leaf_broadleaf` | 7 | yes | -- | 0.657 in-mask stops | distinctness 58.4 | none |
| `leaf_grass` | 7 | yes | -- | 0.810 in-mask stops | distinctness 21.5 | none |

Terrain macro: `comfy_heightmap` seed 5, `invert: true` (white came back as the low ground again).

## Live geometry

| Species | Measured size (m) | Cards | Card size | Bark scale | Profile segments |
|---------|-------------------|-------|-----------|------------|------------------|
| Conifer | 7.74 x 8.22 x 16.36 | 8 | 0.45 | 0.28 | 8 |
| Broadleaf | 14.06 x 15.33 x 14.74 | 8 | 0.42 | 0.28 | 8 |
| Shrub | 1.17 x 1.23 x 1.07 | 5 | 0.16 | 0.12 | 5 |
| Grass tuft | 0.68 x 0.51 x 0.39 | 1 | 0.20 | 0.05 | 3 |

Profile segments raised from the shipped 5 to 8 on both trees: 5 makes a pentagonal trunk, which
was visible at the 2 m camera the bark check uses.

## What the reference screening cost and saved

Eleven references were generated and looked at; five were rejected on sight. Total reference cost
about 75 s. The five rejected pictures would have cost roughly 400 s of geometry to discover.

Rejected: a barn shot dead-on (no depth cue for image-to-3D), a rock slab standing on little feet,
a slab shot flat-on that became a 6 cm wafer, a moss mound that could not be closed at any budget,
and a barn whose silvered siding read as metal.

## The metallic trap, and it is new

The first barn (s41 reference, silvered grey siding) came back with glTF `metallicFactor` 1.0 and a
metallic map averaging **0.83**. A DIFFUSE bake of a metal surface is black, so the albedo baked at
a mean of 14.9 against the source's 47.3, and `bake_fidelity` caught it at 0.9108 against the 0.99
bar. The other four meshes that day measured metallic 0.00.

The fix was the reference, not the code: the s5 barn is dark charcoal rather than silver and came
back at metallic **0.019**, fidelity 0.9974. Worth knowing that the `bake_fidelity` warning names
resampling as the cause, which is the right check but the wrong diagnosis for this failure.

## Still over bar, declared rather than hidden

- **`leaf_conifer` at 1.143 in-mask stops.** Five atlases were generated chasing the 1.0 bar
  (1.735, 2.545, 2.115, 1.372, 1.143). Asking for flat LIGHTING made it worse; describing a flatter
  SUBJECT ("pressed flat like a herbarium specimen, all needles in a single plane") is what moved
  it. The flattest candidate, 1.21, had a cell at 3% opacity and distinctness of 19.3, so it was
  passed over for this one at 24.2. A needle spray may not reach 1.0: the variation is
  self-shadowing between needles, which is real geometry a card cannot carry.
- **Small rock at 1.6% boundary edges.** Its reference is a pitted, vesicular rock, so the openness
  is surface, not pinholes. Raising the budget does not help this class: the moss mound went from
  4.3% at 3500 faces to 3.8% at 9000. Raising the budget DOES help a solid with pinholes, which is
  what took the stump from 1.3% to 0.58%.

## VRAM

12261 free at the start, 5686 after the meshes and textures, which is under the 7000 hero floor.
The preflight refused the second hero barn by sentence rather than by OOM. A restart recovered
13088.
