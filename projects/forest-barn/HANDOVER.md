# forest-barn: failures, and the handover

Gate A has been rejected twice. The first rejection was four pipeline defects, fixed and verified.
The second was two: the leaf cards rotated wrong, and the barn. **The leaf one is closed at the root
and needs one reroll to confirm. The barn's blocker is fixed and measured, its walls now survive, and
what is left is its SURFACE — which is a decision rather than a defect.**

Read `PROCESS.md` for how the gate was built, `ASSETS.md` for every prompt, seed and measured number,
and `references/SCENE-BRIEF.md` for the gate contract and the trap list.

---

## Current state

- **Blender**: a live session holds the gate A layout, bridge on `127.0.0.1:9876`. **Still untouched**,
  deliberately, for the second round running: everything below was done headless. It needs **Reload
  Builders** to see the `_shed` rebuild (a builder body) and nothing else — no reconnect, no new ops.
- **ComfyUI**: on `127.0.0.1:8188`. **The card is wedged again** as of writing: the main process holds
  ~12.8 GB of 15.5 and the atlas route OOMs. It needs a restart before any further generation, and
  there is now a measured reason to change the ORDER when it comes back — see the ordering exception
  below.
- **`$BOB_COMFY_DIR` is verified from the agent surface.** This was the open question and it is
  answered by doing rather than by reading: `comfy_mesh(control=...)` ran the whole four-stage chain
  through the MCP tool with no "Mesh file not found", geometry 50.1 s, simplify 2.0 s, texture 16.0 s.
- **Generated pack**: one asset, `BarnShed`, staged at
  `_staging/a_small_weathered_dark_charcoal_timber_b_s57_02`. Everything else needs regenerating.
- **The gate A review file**: `_generated/forest_barn/gateA_barn.blend`, built by
  `tools/scripts/forest_barn_gate.py` from the pack rather than from a session, so it replays. Four
  objects 12 m apart at true metre scale — the block-out, the two `mesh_texture` variants and the
  projection-painted one — front-lit on a flat plane, with `Cam_Row` framed from the row's measured
  width and a `Cam_Hero_*` per object. **This is the artefact the previous two rounds did not produce**,
  and its absence was the parity gap in its most direct form: every figure was measured headless and
  there was nothing an artist could open.
- **Renders**: `renders/forest-barn/20260730/gateA4_barn_row.png` plus a hero per object. Older ones in
  `_generated/forest_barn/barn_views/` (turntables) and `blockout_views/` (the control).

## Closed: the barn's texture came back black

**Root-caused by measurement, fixed, and verified on the barn itself.**

`mesh_geom_ctrl` is Hunyuan3D-Omni and returns a mesh spanning **[-1, 1]** where TRELLIS.2 returns
[-0.5, 0.5]. `mesh_simplify_uv` rescales nothing and has no normalise node, and its only consumer is
`mesh_texture`, whose `Trellis2EncodeMesh` voxelises **in the unit cube**. So the encoder was handed a
mesh entirely outside its grid, saw nothing, and painted black — faithfully baked, with every other
figure in the receipt healthy. Measured hop by hop:

| file | longest extent, before | after |
|---|---|---|
| control (`export_control`) | 1.00000 | 1.00000 |
| raw (`mesh_geom_ctrl`) | 1.99361 | 1.99316 |
| **simp (`mesh_simplify_uv`)** | **1.99333** | **0.99998** |
| albedo spread / mean | **3.46 / 0.06** | **59.3 / 172.05** |

The fix is one node: `GeomPackNormalizeMeshToBBox(1.0)` in `mesh_simplify_uv.json`, which is the node
`mesh_process.json` already carried for exactly this reason. It is isotropic and centred, so the
proportions the block-out route exists to preserve are untouched (footprint IoU 0.8383 to 0.8270 on
the same cached mesh, aspect identical).

Worth knowing that this was the **third distinct cause** of the same black albedo, and that the
precondition was written down in both graphs' own notes while nothing enforced it. It is enforced now
over the two graphs that feed `mesh_texture` as a SET, so a third feed cannot be added without meeting
it.

## Closed: the barn's walls and roof did not survive

**The hypothesis in the last handover was right, and it is bigger than it looked.**

A block-out is read as an **area-weighted surface sample** (`Hy3DOmniPointGenerate`), so a hidden
interior face is a conditioning point like any other. `_shed` was a wall cube with a roof prism sitting
on it, which put the wall box's top face (56.0 m²) and the roof prism's underside (67.0 m²) **inside**
the building — **125.94 m² of 425.98, 29.6% of every control point** describing a solid slab at wall
height that is not there. The code comment those faces were built under said an overlapping union
samples the same surface as a clean one, which is true for a render and exactly wrong for a point
cloud.

`_shed` is rebuilt as one closed shell — floor, four wall sides, a mitred soffit ring to the eave line,
two slopes, two gable triangles. **313.98 m² with 2.95 hidden, 0.9%**, all of it the doorway jamb boxes'
backs against the wall, at the same 8.7 × 7.7 × 7.5 m bbox and the same zero non-manifold edges. The
silhouette is identical; only the conditioning changed.

The result, generated against it: **profile max band deviation 0.0753** against a 0.10 bar, where a
synthetic A-frame at the same bbox scores 0.2551. Flat roof planes, a straight ridge, vertical planar
walls, a doorway with a jamb standing proud. Closed, too: `low_openness` reports **0 loops, 0
see-through edges, 0 open edges** welded.

**One receipt figure to not misread, because it cost this round twenty minutes.** `low_boundary_edges`
came back **1072** with `warnings` empty, which reads as a sieve shipping unnoticed. It is not: this
route hands over a mesh straight from `mesh_simplify_uv`, which neither welds nor repairs, and glTF
splits a vertex at every UV seam. `source_boundary_edges` is 0 and `low_openness` welds a copy of its
own and reports 0. The previous round measured the same thing as 1,187 unwelded against 24 welded.
**Read `low_openness`, not `low_boundary_edges`, on any Omni route.**

## Open, and it is a decision: the barn's SURFACE

The shape is right and the surface is not, and the mechanism is structural rather than a bad seed.
`mesh_texture` conditions on **one** reference image and invents every surface that image cannot see.
Look at `barn_views/barn_01_beauty.png`: the roof slope carries **door panels and X-bracing**, painted
onto it from the reference's gable elevation. In `barn_00_beauty.png` the gable siding reads as rough
stucco rather than the reference's vertical boards, and the doorway has drifted off-centre.

**Tested, because it was one 20 s call and it settles the mechanism.** `comfy_paint_mesh` re-runs
`mesh_texture` on the already-normalised simplified mesh, so a reference can be swapped without
regenerating anything. Screened a second reference from high up, showing the whole roof plane along with
the long side and the gable end (`_s57_03`), and re-textured at 2048:

| reference | roof slope | gable end |
|---|---|---|
| `s57`, low three-quarter | **door panels and X-bracing painted on it** | fair, boards visible but reads soft |
| `s57_03`, aerial | timber boards, the door panels **gone** — and a WINDOW painted on it instead | **worse**: rough stucco, no board rhythm |

Two references, two different faces right, and neither gets both. Albedo spread 29.72 → 38.47, mean
35.1 → 48.53, `bake_fidelity` 0.9990 → 0.9928 at 2.81 of 255 — both still inside their bars, so no
receipt figure distinguishes these two textures and only the render does. **That is the single-view
limitation demonstrated rather than argued, and it is not a prompt or a seed or a resolution problem:
one image cannot tell the painter which surface is a roof.** The aerial reference is the better of the
two and is what the current `BarnShedAerial` carries.

**Then the projection route was run, and it fixes the defect.** `tools/scripts/forest_barn_paint.py`:
strip the asset to flat grey, render a turntable, restyle every view through `mesh_paint_views`,
projection-bake. Painted from CLAY deliberately — `paint_views` restyles a RENDER, so painting the
finished asset would hand the model the defect it is meant to remove.

| | first run | with the underside view, denoise 0.70 |
|---|---|---|
| views | 7 | 8 |
| chart texels painted | 77.0% | **96.8%** |
| left to the hole fill | 149,710 | **0** |
| adjacent-view overlap MAD, of 255 | 18.7 to **75.4** | 8.8 to **29.1** |

**The roof carries planked roofing and the walls carry vertical boards.** No door panels on the roof,
no window on the roof. The basecolor atlas is clean chart by chart — boards, planks, a gable triangle,
a door panel — which is the thing neither single-view reference could do.

Two notes on the run, one of them my error. The first attempt passed `extra_elevations=(72.0,)` and
dropped the shipped `-55.0`, so nothing photographed the barn's floor face and 23% of the chart went to
the hole fill as black wedges; the default carries that view for exactly this reason and its own comment
says so. And `denoise` is the only lever on inter-view agreement, because `paint_views` owns its
IPAdapter reference internally (the front view seeds every later one) — there is no reference to pass.

**What is left is narrow and it is NOT the paint.** The render still shows large black regions while the
basecolor atlas has none and the bake reports 0 texels to the hole fill — receipt and render disagree,
which is this project's usual tell. The suspect is the material rather than the maps:
`gen_paint.paint_object` returned `M_barn_Painted` with `materials.master_type` **None**, where its own
docstring says it "reuses `gen_assets.apply_baked_material`, so a painted asset carries the same graph
shape a baked one does and stays a BobShader candidate rather than a special case". It is not one here.
Start there.

Three routes were on the table, and the brief allows the second:

1. **`mesh_paint_views`** — the projection route: paint from Blender-rendered views so every surface is
   painted by a camera that can see it. This is the route built for exactly this failure, and the
   experiment above is the argument for it. **It is not reachable from the agent surface**: it needs
   Blender to render the views, so it is a panel action (Stylise), and `comfy_paint_mesh` over MCP is
   the single-view graph again. An agent can diagnose this and cannot fix it — logged in
   [ROADMAP.md](../../docs/ROADMAP.md) as the sharpest case of the agent/artist parity gap.
2. **`comfy_texture_set` per material.** The brief's own manifest row 8 is "structure surfaces —
   `comfy_texture_set`, one per material the structure carries". A tileable barn-siding set and a roof
   set, applied with `apply_texture_set`, gives real board rhythm at real scale and cannot hallucinate
   a door onto a roof. `ASSETS.md` already records a "barn siding (spare)" set (seed 3, seam 0.794,
   flatness 0.0742) generated for this and never used. It needs the mesh to carry material slots the
   sets can key to, which the generated mesh currently does not.
3. **Keep `mesh_texture` and re-shoot at hero.** Cheapest. It fixes resolution (2048 against the 1024
   this ran at) and will not fix door panels on the roof, because that is not a resolution problem.

My read: 2 is what the brief asks for and 1 is what the pipeline built for it. 3 is worth doing anyway
as a control, since it is one call.

## The gate-A ordering exception, measured

**Once the Omni control route has run, the SDXL atlas route OOMs whatever the card reports free**, and
a preflight cannot save it. Measured on two fresh servers in a row: `comfy_status` reported 6530 MiB
free, over the 3000 texture floor, and the main process then expanded to 12.80 GiB of 15.48 as the job
started and BiRefNet died with 162 MiB left. Same shape on the previous server, 5588 reported against
325 at the point of failure, `comfy_free` recovering 32 MiB of a 12.4 GiB hold. There is no number
readable at queue time that predicts it.

The original gate got away with textures last because its meshes came from TRELLIS.2. Omni is heavier.
**So: generate the atlases and texture sets BEFORE the block-out-conditioned structure.** The brief's
"hero structure first, while the card is emptiest" is right about TRELLIS.2 and backwards about Omni.

## The leaf cards: orientation CLOSED and confirmed. Two new problems in its place

The diagnosis held. Restoring the twig restores both cues, and the receipt now says so per cell.
Six atlases generated at seed 29 (conifer) and 7 (broadleaf, grass), `delight=True`, 2×2, 1024:

| set | prompt added to the twig clause | resolved | worst ramp (bar 0.55) |
|---|---|---|---|
| `leaf_conifer_04` | — | **4/4 woody** | 1.317 |
| `leaf_conifer_05` | flatbed scan, flat even light | **4/4** | **0.627** |
| `leaf_broadleaf` | flatbed scan | 1/4 | 0.235 |
| `leaf_broadleaf_02` | + fresh GREEN leaves | **4/4 woody** | **0.711** |
| `leaf_grass` | flatbed scan | 1/4 | 1.153 |
| `leaf_grass_02` | + straw-brown cut base | **0/4** | 0.305 |

**Orientation is fixed.** Conifer went from woody fraction 0.000–0.002 and anisotropy 1.04–1.25 (every
cell a guess) to 0.108–0.254 and 2.03–4.52, every cell resolved on the woody cue, the two cues agreeing
within 0.6–16.3 degrees. No orientation warning on either conifer set.

**New problem 1: the two bars are in tension, and one of them was calibrated on defective sets.** The
wording that makes a sprite orientable — a real twig, photographed — is the wording that brings baked
light back. `leaf_conifer_05` misses the 0.55 ramp bar by 0.08 stops and `leaf_broadleaf_02` by 0.16.
This matters more than a reroll: `gen_receipt.LEAF_RAMP_STOPS_MAX = 0.55` is one of the three constants
[ROADMAP.md](../../docs/ROADMAP.md) already flags as resting on two points, one of them synthetic — and
those two points came from the atlases generated with the HERBARIUM wording, i.e. from sets that were
broken in the orientation dimension. This round is the second batch that constant was waiting for: 24
real cells at the correct wording, spanning 0.049 to 1.317, with the two good sets at 0.099–0.711.
**Whether 0.55 survives that is an artist call, not a code call.** Do not quietly widen it.

**New problem 2: grass cannot be oriented by either cue, at any seed or prompt.** It has no woody stem
by nature (`woody_fraction` 0.000 on five of eight cells measured) so the woody cue is structurally
unavailable, and the axis cue needs a fanned wide end against a stub — grass measures `strands` 1 to 1
and 1 to 2, contrast 0 or 1, against a 2 bar. Asking for a straw-brown base to give the woody cue
something to read made it WORSE, 1/4 to 0/4: a pale straw base is still bright and yellow-green, so it
never crosses `comfy_maps.WOODY_EXCESS`. The remedy is a third cue rather than a prompt, and the obvious
candidate is measurable: **blades CONVERGE at the base and diverge at the tips**, which is a real
signature of a tuft that neither existing cue looks for. Not built.

**Hazard to clear before any foliage build.** Trap 13 resolves atlases BY NAME, and right now the plain
names `leaf_broadleaf` and `leaf_grass` are held by the REJECTED first attempts, while the good ones
carry `_02` suffixes. A tree built today would wear the bad sets. The winners have not been renamed
because renaming picks a winner, which is the artist's call — but nothing should be built until it is
done.

## Your two answers, recorded

- **Revert both unapproved changes: agreed and actioned.** Neither was a code change, which is worth
  knowing — the shipped species presets still carry `profile_segments` 5 (conifer), 6 (broadleaf), 5
  (shrub), 3 (grass_tuft), and the 8 was a per-build override in the rejected run. So "revert" means
  the next foliage build passes the preset values and nothing else, and the rock slab goes back in the
  manifest in place of the small rock. **The two problems come back with them** and are now expected
  rather than defects: 5 segments gives a pentagonal trunk at the 2 m bark camera, and a slab
  photographed flat-on generates as a 6 cm wafer, so the slab needs a reference screened for a
  three-quarter view (which the reference-screening step now reliably gets — see the barn's five).

## Also unresolved, smaller

- **`map_fidelity` still returns None on flat input**, and a None fidelity warns about nothing.
  `empty_map_warning` covers it from the other side, so nothing ships black unnoticed any more, but
  "the measurement declined to answer" and "the bake is fine" still reach the receipt as the same
  empty list. Not touched this round.
- **The barn is not hero.** 1024 texture, 7924 faces, because the card had 7,246 MiB against the 7,000
  floor and had already OOMed twice. One call on a fresh server fixes it — and per the ordering
  exception above, do it after the atlases.
- **The doorway drifted off-centre** in the generation, where the block-out centres it in the -y gable.
  Not chased. It may be the texture rather than the geometry; the profile and footprint figures cannot
  see a feature move sideways.

## What NOT to redo

The four original defects are fixed and measured. The black albedo is fixed at the node and pinned by
tests over both feeding graphs. The hidden-surface defect is fixed in `_shed` and gated in
`blockout-control` part A, which needs no server and runs every time.

**The reference screening step is not optional and it is now also a prompt lesson.** Three of five
barn references were rejected on sight and all three failed the same way — SDXL's barn prior is
head-on, and asking for "a three-quarter angle" in the positive prompt does not move it. Name the angle
as a property of the subject and put `frontal, straight-on, head-on, elevation` in the NEGATIVE.

Two gates were added and both are calibrated, so do not re-derive their bars:
`HIDDEN_AREA_MAX` 5% (shed 29.6% built as solids, 0.9% as a shell) and `PROFILE_DEVIATION_MAX` 0.10
(0.0146 a shape against itself, 0.2551 an A-frame, 0.0753 the barn that shipped). The intuitive
"lower-half plan area" version of that second one is reported and deliberately NOT gated: it reads
1.1127 on the A-frame, above 1.0, because a roof slope at knee height covers more plan than a wall ring
does. It would have passed the exact shape it was written to catch.

291 tests pass.
