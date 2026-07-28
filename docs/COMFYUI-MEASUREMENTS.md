# ComfyUI integration: the measurement record

The compressed log of the thirteen phases that built this track, the review findings that changed
the plan, and the decisions that are answered. [COMFYUI.md](COMFYUI.md) is the reference you read
while working; this file is the history you read when a number is questioned.

**This is a compression, not the original.** Each phase below was 150 to 330 lines of long-form
prose with its corrections spelled out; what is kept is the scope, the verdict, the decisive numbers
and the gate that proved them. The full text was last present in `docs/COMFYUI.md` at commit
`8e7c4a6` and is recoverable from git history:

```sh
git show 8e7c4a6:docs/COMFYUI.md
```

Every gate is a script under `tools/scripts/`, all of them reachability-gated (they skip cleanly
with no ComfyUI) and all cacheable, so any figure here can be re-measured. The scripts and how to
run them are in [Gates and how to re-run them](#gates-and-how-to-re-run-them).

---

## G0

**Texture-set sampler in Shaders (the BobShaders S3 work), plus the generated pack root.**

Passed. The `grass` set renders on a terrain layer in EEVEE (luminance range 0.6118) and Cycles
(0.6157). Node cost measured rather than assumed: 11 nodes and 4 image textures for one textured
layer, 17 and 8 for two. Proved by `headless_texset.py`, not by inspection.

Correction this phase forced: the master sockets did not carry what the plan assumed, which is why
the sampler shipped before any ComfyUI code existed.

## G0.5

**`ComfyUI-TRELLIS2` plus its required `ComfyUI-GeometryPack` installed and pinned as submodules;
`TRELLIS.2-4B` pulled (about 15 GB, bf16); the three bundled workflows run.**

Passed, with one real defect found. A textured GLB out of `geometry_texture` is 462,140 triangles
with two 2048² PBR maps in 72 s. A Bob block-out proxy textures through `standalone_texturing` in
10 s **but only after being unit-normalised**: at metre scale it returns a silently black albedo,
which is what makes normalise-then-rescale a mandatory `gen_assets` step rather than a nicety.
1024³ fits 16 GB one-shot with room (peak 5.7 GB of 16), so no cascade is needed. Foliage capability
confirmed on a leaf: **83,292 boundary edges**, thinnest/longest axis 0.059, non-watertight.

## G1

**The vertical spike: `tex_tileable`, the stdlib client, one blocking job, one written set, applied to one
terrain layer.**

Passed with room. **7.6 s** prompt to a rendered terrain layer against a 60 s gate (generate 5.0,
derive 0.67, write 0.11, apply 1.33, EEVEE 0.5); 7.3 s on a cold server. Seam ratio **0.83** against
3.86 untreated, i.e. the wrap is as continuous as an arbitrary interior line. EEVEE luminance range
0.6131. The `CircularVAEDecode` segfault found here shaped the graph: circular padding is applied to
the UNet and the VAE separately. Ten corrections to the plan came out of this phase. Gate:
`headless_comfy_texset.py`.

## G2

**Generalise: `comfy_jobs`, preflight, variants and Accept, `tex_tileable_ref` and `tex_upres`, real `comfy_maps`, the
Advanced panel.**

Passed. Ten sets generated and accepted in one session at a mean of **5.55 to 5.59 s** with drift
of -0.15 to -0.19 s, i.e. no leak. Longest main-thread block **16.5 ms** against the blocking path's
5,563 ms, measured with a stand-in event loop. Preflight catches all five failure classes, one test
each. Seam after a `tex_upres` upres 1.03 / 1.10 from 1.05 before. Roughness contrast **+55% std** (25.8 to
40.0) with the mean off the ceiling (187.8 to 156.8). 33 tests in `test_comfy.py`.

## G3

**Tracks C and B together: `mesh_subject`, `mesh_geom_trellis`, `mesh_texture`, `mesh_simplify_uv`, plus `mesh_geom` Hunyuan as the plumbing smoke test.
`core/gen_assets.py` steps 6 to 8, the generated manifest, and Scatter's Generate Asset.**

Passed, with two parts named as partial. Prompt to a scattered, correctly scaled, UV'd, PBR-textured,
BobShader-converted prop in **40 to 203 s** against a 300 s gate, on five assets. Faces 3,155 to
3,992 against a 4,000 budget, UV overlap at most 0.00002, baked normal std 0.24 to 0.28, bbox height
exact, origin **0.0 m** above the base, a three-step LOD chain, `master_type()` surface. The
black-albedo trap became a test: round-trip error 6.0e-08 m, texture std 0.39 against a 0.02 floor.

Steps 3 and 4 decided here and written into the pinned pipeline: TRELLIS.2 **5 of 5** inside budget
in 5.95 s, Blender Decimate **1 of 5** in 131.95 s, Quadriflow **refused every mesh**.

Partials, both carried to G3b: open surfaces needed `remesh: off` to appear at all, and the opacity
channel did not reach the finished material. Foliage measured three ways on three prompts, which is
the origin of the foliage rule in COMFYUI.md: a flat leaf gave 11,610 boundary edges at a 0.0422
thin ratio, a fern frond 51,842 edges as a bushy volume, and a broadleaf sprig **15 edges**, i.e. a
closed blob.

## G3b

**`mesh_geom_texture` one-shot `geometry_texture` against the `mesh_geom_trellis` plus `mesh_simplify_uv` plus `mesh_texture` staged chain, ten prompts, one
shared `mesh_subject` subject each. Plus the opacity channel, G3's named partial.**

Passed, and it changed the default to one-shot. `mesh_geom_texture` fits 16 GB with 61% of the card free (peak
**6,276 MiB** summed across the three ComfyUI processes) against the staged route's **8,586 MiB**.
Wall clock is a wash: 593.1 s against 584.1 s for all ten. Both routes 10/10 inside the 4,000 budget
with UV overlap at most 0.0001. `mesh_geom_texture` returns a far cleaner mesh (**10 to 662 boundary edges** against
1,467 to 3,050) while preserving foliage openness (4 of 4 open on both, thin ratios within 1.5%),
and it cannot hit the black-albedo trap that returned one fully black `mesh_texture` texture in ten. The dense
mesh it gives up bought **no measurable normal detail** at this budget.

Opacity: present in both routes and declared away as `alphaMode: OPAQUE`, so importers ignore it.
Now wired behind a plausibility rule, firing on a generated leaf at in-chart alpha mean **0.9806**
with 1.81% below the 0.98 floor, and proved through the glTF round trip. The rule matters: the
staged route's alpha runs 0.07 to 0.77 in-chart with 42% to 93% below the floor, which wiring would
have turned into a 60%-transparent stump.

## G4

**Tracks D then B-stylised, in that order because they share a graph: `stylize_render` and `stylize_render_est` stylise (render
plus true depth and normal export), then `mesh_paint_views` as the style-control paint route. Plus `mesh_geom_mv` and `mesh_geom_mv_trellis`
multi-view geometry.**

Passed, with one of its own claims disproved and named as such. A Bob render comes back stylised at
silhouette IoU **0.9980** (against 0.9967 estimated) in 7.7 s, peaking at 14,194 MiB of 16,303
summed over the ComfyUI family.

**The real-passes claim failed on quality.** The differences against Depth Anything V2 plus NormalBAE
are smaller than the estimator's own error on the source frame (r 0.7957, MAE 0.1224). The honest
case for keeping the export is 2.5 s per frame plus the fact that `mesh_paint_views` needs the same three files.

Paint route: **92.6%** of chart texels painted from 8 views, adjacent-view seam 22.3 to 26.5 of 255,
front-to-back drift 30.1. Multi-view beats single-view six-fold on the back-facing test: back-half
IoU 0.2637 (`mesh_geom_mv_trellis`) and 0.2140 (`mesh_geom_mv`) against 0.0439, against a 0.7110 self-agreement ceiling; `mesh_geom_mv` is 5x
faster at 24.4 s. The panel press costs 0.63 to 1.00 s of main thread (the render itself) and the
longest tick during the job is 0.14 ms.

## G4c

**Omni: model set 3, `mesh_geom_ctrl`, `export_control`, the Asset-from-Block-out entry, and the orientation
convention pinned per exporter.**

Passed, and it changed a decision the plan had already made. A block-out proxy conditions generation
and the result keeps its shape where it landed, scored with **no** rotation search: footprint IoU
0.8136 to 0.9787 against per-block-out ceilings of 0.8403 to 0.9920, proportions held to 2%, and
**0.8100 on the finished asset** after simplify, texture, bake, scale, LODs and BobShade.

`mesh_geom_ctrl` beat the `mesh_geom_mv_trellis` multi-view baseline **3 of 3 on every measure at once**: mean footprint IoU 0.9079
against 0.6748, 35.8 s against 164.9 s, 2 GB less VRAM. It still wins after `mesh_geom_mv_trellis` is allowed its best
axis map, so the plan's intended fallback was not the honest answer.

The wrapper is the least maintained dependency in the track and **shipped with the control signal
silently random**: a vendored `linear` to `liner` rename gave 0.010 voxel IoU before the fix and 0.53
after. `tools/scripts/comfy_omni_fix.py` is the fix. One bug found on another route: the exporter's
turns **accumulate**, so the staged chain's high-to-low bake had been reading across a 90 or 180
degree rotation since G3, which put a G3b conclusion back in question (re-answered at G7).

## G5

**Track E: `heightmap_macro`, `comfy_maps.macro_field`, the `macro` op and `params.with_macro`, and Generate Base
in the Terrain panel.**

Passed, and it answered R7 in the negative. A prompted silhouette survives an erosion pass at
band-limited correlation **0.906 to 0.923** against a no-mask null of 0.078 to 0.208, on three
prompts including the isolated massif erosion was expected to fight. The subordination is measured:
erosion supplies **2.89 to 3.04 m** of fine relief against a mask-only baseline's **0.28 to 0.31 m**,
median slope 42.1 to 42.8 degrees beside the no-mask bake's own 44.7, and the mask explains only 11
to 16% of the band above its own cutoff.

**R7's terracing does not occur at 8 bits, and does not occur at 5 either** (histogram concentration
1.86 and 1.91 against 16-bit's 1.93), so the 16-bit save node is deferred on evidence. What the
8-bit write costs is determinism, 0.80 m rms against a reseed's 9.28 m. About **12 s** prompt to a
built terrain, 0.3 ms of main thread, peak 7,444 to 9,844 MiB. It is the one route that shares a card
with Omni.

Found by accident here and still open: the terrain engine's slope-area gradient has the wrong sign
(D13 in COMFYUI.md).

## G6

**The six `comfy_*` MCP tools, one batched contract change (`apply_texture_set`, `import_generated`,
`export_control`, `OpResult.data`), the `macro` key on `bake_heightfield`, websocket progress, and
the whole shipped-gate suite as one command.**

Passed, and it found a crash that was not Bob's. An agent goes prompt to a scattered, scaled, UV'd,
PBR-textured, BobShaded asset in **102.5 s** with no GUI (generation 97.7 s, Blender 4.8 s) and
prompt to a rendered shaded terrain in **24.1 s** (set 6.0, mask 3.9, bake 7.5, build and render
6.7), both through the real MCP tools. Every asset property read out of the op's own result: faces
3,672 to 3,930 against 4,000, UV overlap at most 0.0000017, height 1.8 m exact, origin 0.0 m above
the base, `master_type` surface. Websocket progress: **28 per-node updates against 5 status strings**
on the same job, with termination still decided by the jobs API so a dead socket cannot cost a result.

**The finding:** this fork's `comfy-aimdo` dynamic-VRAM staging segfaults the server on the second
copied-model decode of a session, which is every tiling graph Bob ships. Five candidates measured;
the shipped fix keeps the staging feature by applying circular padding **in place** plus a lazy
`ensure_untiled`, giving ten texture sets in one session (seam 0.83 to 1.18, drift -0.01 s) with `mesh_subject`
at seam 8.466 and `heightmap_macro`'s open route at 10.086, i.e. verified untiled. Still open upstream as D14.

## G7

**The geometry A/B: `mesh_geom_alt` (Hunyuan 2.1 on a plate) and `mesh_process` (the shared processor), ten fixed prompts,
five of them foliage, one shared `mesh_subject` subject each, `remesh` controlled for on both sides.**

Passed, with a verdict per asset class rather than a winner.

- **Foliage: TRELLIS.2, structurally.** 2.1x faster (median 15.0 s against 31.3 s), half the VRAM
  (4,964 MiB against 9,620), 2.9x the boundary edges (median 984 against 344), and the challenger
  cannot return an open surface at all, so its holes are the simplifier's rather than the model's.
- **Solids: the challenger wins two columns and loses three.** Hunyuan is 2.1x faster (40.4 s
  against 86.1 s) and returns a closed shell (median 0 boundary edges against 116, unrepaired
  downstream on either route), against 3.7 GB more VRAM, a flatter albedo (0.1259 against 0.1555),
  one black texture in ten where `mesh_geom_texture` cannot hit the trap at all, and a licence with a territorial
  exclusion where TRELLIS.2 is MIT. Default holds; `route="alt"` is an explicit choice.
- **Block-out: Hunyuan through Omni and `mesh_geom_ctrl`**, decided at G4c. `mesh_geom_alt` takes no control, so the grid has
  no cell for it.

Both models 10/10 inside the 4,000 budget, worst UV overlap 0.00047, three of ten carried through
steps 6 to 8 on both models with every G3 asset check passing.

**Two silent defects found.** The challenger route returned a fully black albedo on **every** asset
until `mesh_process` gained a normalise (Hunyuan is [-1, 1] where TRELLIS.2 is [-0.5, 0.5]); the finished asset
then baked a **perfectly flat normal** because that normalise moved the low mesh and not the cage
(detail 0.00000 against 0.01750 fixed). That also proved this suite's "baked normal is non-flat"
check could not fail, since a flat normal reads std 0.2357 against a 0.01 threshold.

**D11 answered both ways.** The G4c alignment fix raises transferred detail on 4 of 4 assets (14x on
the fern), and the dense mesh still buys nothing at this budget (1 of 4 better than the control, 2 of
4 worse).

## G8

**D12's first half: `mesh_geom_bbox` (`mesh_geom_bbox`, Omni's bounding-box control), the control mode as a value
(`CONTROL_MODES`, `control_route`, `DEFAULT_CONTROL_MODE`), and the scope decision that declined `sky_equirect`,
`mesh_part` and batch generation.**

Passed, and D12's answer is no. Eight corners do not replace 8,192 points: footprint IoU **0.5766**
against the point route's **0.9200** on the same three block-outs, same image, same no-rotation-search
scoring, which is 50.1% to 70.8% of each block-out's own ceiling against the point route's 98.8% to
101.0%, for 7 seconds an asset.

**The control does reach the model and is simply not enough information**, which is what separates
this from G4c's silently-random wrapper: Bob's proportions beat the node's `auto_bbox` guess 3 of 3
on aspect error (0.205 / 0.051 / 0.015 against 0.504 / 1.352 / 0.183) and only 1 of 3 on ground plan.
The gain scales with how distinctive the box is: 3x over the null on a tall thin tree, a **loss** on
a near-cubic rock.

`mesh_geom_bbox` stays wired for a different reason than it was built for: it uploads nothing, so with
`comfy_dir()` forced away `mesh_geom_ctrl` fails in 4 ms with `Mesh file not found` where `mesh_geom_bbox` completes. One
finished asset through `mesh_geom_bbox`, `mesh_simplify_uv`, `mesh_texture` and steps 6 to 8 passes every G3 check (3,902 faces, UV overlap
0.0, LODs [3902, 1951, 585], `bake_rescale` 1.0, normal detail 0.00372).

**Three silent traps found:** `auto_bbox` defaults TRUE so the obvious wiring never sees the
block-out; `stage_exports` asked "is there a control FILE" and would have laid every bbox asset on
its side; and `omni_model_dir` is derived from `comfy_dir`, so a process without the folder starts a
13.5 GB download instead of erroring.

## G9

**D12's remainder and its close: `mesh_geom_voxel` (`mesh_geom_voxel`, Omni's voxel control),
`comfy.VOXEL_INPUT_ROTATION` pinned by measurement, `control_route`'s refusal of an unknown mode,
`comfy_mesh(control_mode=...)`, and the swapped-control null.**

Passed, and it closed D12 without changing the default. An occupancy grid reaches footprint IoU
**0.8507** against the point route's **0.9106** on the same three block-outs, at 29.0 s warm against
36.0 s. The finding is the ordering: **point 0.9106, voxel 0.8507, bbox 0.5766**, so 4,096 cells
recover most of what 8,192 points buy and eight corners about half. Voxel **matches** point on a
compact block-out (0.9290 against 0.9287) and loses a thin one to its own cell size (0.8491 against
0.9759 on a 3.4 m tree whose trunk is narrower than the 21 cm cell a 16-cubed grid gives it).

The control reaches the model 3 of 3 against a swapped-control null that scores 0.2667: a run given
another block-out's control follows **that** one, 0.8960 against the block-out it was conditioned on
and 0.2168 against the one it was pictured as. The node's own `apply_input_rotation` default costs
43% of the ground plan and errors nowhere. Finished asset: 3,914 faces of 4,000, UV overlap 0.0,
LODs [3914, 1957, 587], normal detail 0.00398, footprint 0.7723 against the raw mesh's 0.7739. `mesh_geom_voxel`
uploads a mesh, so `mesh_geom_bbox` keeps the no-ComfyUI-folder fallback alone.

---

## Review findings that changed this plan

Revision 1 of the plan had real defects. Each is kept as finding, fix and where it landed, because
the fixes are most of what the track became.

| # | Finding | Fix, and what became of it |
|---|---|---|
| **R1** | Two phases of plumbing before anything visible; first artist-visible result in phase three. | G1 became a thin vertical spike, plumbing generalised afterwards against something that worked. |
| **R2** | Generated geometry has no UVs at all, and the whole material story assumed it did. | UV creation is a mandatory Blender-owned stage; the pipeline order is pinned. |
| **R3** | Retopology, UV and baking pointed at the wrong side of the fence. | Division of labour: **ComfyUI generates; Blender retopologises, unwraps, bakes, LODs, packs.** ML retopo is a nice-to-have, not a dependency. |
| **R4** | Paint-versus-retopo ordering undefined, and one order destroys the textures. | Paint the dense mesh, bake dense to low. **Superseded by R20**, then by R21 deleting the wrapper. |
| **R5** | `POST /interrupt` kills somebody else's job on a shared queue. | Use this fork's jobs API: poll `/api/jobs/{id}`, cancel `/api/jobs/{id}/cancel` (idempotent, atomic). `/history` plus `/interrupt` demoted to a fallback detected via `GET /features`. |
| **R6** | Workflows naming a checkpoint by filename are not portable. | Model nodes are templated (`BOB_CKPT`, `BOB_VAE`, `BOB_LORA`, `BOB_3D_MODEL`) and resolved from `/object_info` enums plus a preference. |
| **R7** | An 8-bit PNG cannot carry a heightfield; 256 steps is visible terracing. | Treat diffusion output strictly as a low-frequency macro mask. **Measured at G5: terracing never arrives** (concentration 1.86 at 8 bits, 1.91 at 5, 1.93 at 16); the 8-bit write costs determinism, not precision. 16-bit node deferred on evidence. |
| **R8** | `/free` alone does not fix 16 GB: the process and its allocator stay alive, and Blender holds VRAM too. | Three layers: `--reserve-vram` when Bob starts the server, single-model sequential workflows, a hard Stop Server. **Re-confirmed the hard way in 2026-07-27's redwood run**, which is D15. |
| **R9** | No iteration UX; one Generate button that overwrites is the wrong shape. | N variants into staging, show them, Accept one into the pack; Reject is a delete. |
| **R10** | Mesh provenance missing while texture provenance was specified. | A sidecar JSON beside every generated GLB: workflow, model, seed, prompt, licence. |
| **R11** | Manifest schema fork risk from a second reader. | Extend `_norm_entries()` with defaulted fields. One reader, still. |
| **R12** | Title-based templating unguarded; API export drops nodes feeding no output. | Preflight validates every `BOB_*` title is present and unique. |
| **R13** | No time budgets, so no way to tell a slow path from a broken one. | Every phase gate carries a measured wall-clock target. |
| **R14** | G0's sampler underestimated: six layers times five maps is thirty image nodes. | One shared sampler group per layer, nodes only for enabled layers, node count and EEVEE compile in the gate. |
| **R15** | Threading details missing; a job outliving a file load corrupts state. | Job registry clears on `load_post`; every `bpy` touch happens in the timer tick. |
| **R16** | No naming or dedup rule, so a second "rock" overwrites the first. | Slugged unique names, never an implicit overwrite. |
| **R17** | "Author the workflows from scratch" was wrong; ten official Hunyuan3D templates already ship. | Bob's graphs are **derived** from templates: convert to API format, retitle `BOB_*`, record upstream name and version. Upstream changes become a diff. |
| **R18** | The retopology question had an answer in the repo, and it is cloud-only (`TencentSmartTopologyNode` and four others, $0.20 to $1.00, comfy.org auth). | Evaluated and **rejected** on the local-only decision. Recorded so it is not rediscovered as new. |
| **R19** | Local-only makes Blender the only retopo path and the paint wrapper the only Hunyuan PBR route. | Retopology tiered on the observation that **quad topology barely matters for static scatter props**; Quadriflow reserved for hero. Paint priority inverted to `mesh_paint_views`. |
| **R20** | The paint wrapper is the same algorithm with a worse rasteriser; Blender already rasterises and projects in-process. | Blender-side route promoted. **Confirmed at G4** in 300 lines of numpy: 92.6% of chart texels from eight views, 29 texels left for hole fill. What it loses is cross-view consistency (22.3 to 26.5 of 255 adjacent, 30.1 front-to-back), which is what MV-Adapter would have fixed; see D10. |
| **R21** | TRELLIS.2 changes the geometry and paint decision, and one reason dominates: O-Voxel represents **open surfaces**, which Hunyuan's watertight SDF cannot. | TRELLIS.2 primary (MIT, native PBR); the compiled-rasteriser Hunyuan paint wrapper dropped; `mesh_paint_views` demoted to style-control alternative; `Trellis2Simplify` and `Trellis2UVUnwrap` became an A/B against Blender rather than an assumption. Hunyuan keeps multi-view conditioning, Omni control, and zero-install smoke testing. |

---

## Decisions answered

- **D1 Image model family. Answered at G1: SDXL, with `RealVisXL_V5.0_fp16`** (6.9 GB, OpenRAIL++).
  The decisive argument is structural: seamless tiling by circular padding works by switching every
  `Conv2d`'s padding mode, which only exists in a convolutional UNet. FLUX and Qwen-Image are DiTs,
  so the trick reaches only their VAE and the latent still carries a seam. Supporting: 25 steps at
  1024² in 5.0 s, the SDXL ControlNet set already on disk, the material and tileable LoRA ecosystem,
  and the smallest download of the three. The Qwen model on disk is Qwen-Image-**Edit**, so a
  text-to-image route would have meant another ~14 GB. Revisit only if prompt adherence becomes the
  binding constraint, and then as a first pass upscaled through SDXL rather than a swap.
- **D2 Further challengers (Direct3D-S2, Hi3DGen). Answered at G7: no, and the condition is what
  answers it.** D2 was conditional on TRELLIS.2 versus Hunyuan landing close. It did not, in either
  direction: G4 split by capability, G7 split the same way but wider. What stays is the **slot** (`mesh_geom_alt`
  plus `mesh_process`), so a future challenger costs one graph and no rewrite. Reopen with a specific gap, not
  for coverage.
- **D3 Retopology route. Answered.** Official retopology is cloud-only, so Blender does it:
  Decimate-collapse for scatter-grade, Quadriflow for hero, tiered by intent (R19). Open ML
  retopologisers (MeshAnything V2, BPT, DeepMesh, EdgeRunner class) cap out around a few thousand
  faces with no maintained ComfyUI pack, so they stay speculative.
- **D4 Seamless tiling. Answered at G1: circular padding, both halves.** `ComfyUI-seamless-tiling`
  (spinagon, GPL-3.0, pinned `9225ed5`, pure Python) supplies `SeamlessTile` for the UNet and
  `MakeCircularVAE` for the decoder. The WAS offset blend was measured and **rejected**: a wide
  feathered band rather than a line, costing 15% of interior local contrast, and unnecessary once
  padding is circular (ratio 0.83 against the blend's 1.17).
- **D5 G0 scope. Answered: landed in this track.** The sampler shipped as G0 before any ComfyUI code,
  which is what let G1 be a spike rather than a spike plus a sampler.
- **D6 Geometry ambition. Answered at G3, in the direction the plan did not expect: scatter-grade
  only, because there is no local hero tier to have.** Quadriflow refuses every generated mesh
  (non-manifold, inconsistent normals) on all five test assets, and Blender Decimate cannot reach a
  4,000-face budget either. `Trellis2Simplify` delivers the budget, and it is triangles. So the
  remaining question is whether a hero path is worth a **manual** retopo step, which is a workflow
  question, not a tiering decision. `hero=True` survives as a bake-resolution and texture-resolution
  switch, honestly labelled.
- **D7 Cloud tier. Answered: no.** Fully local, enforced by the `api_node` preflight check rather
  than by intent.
- **D8 Paint route priority. Answered by R21.** `Trellis2TextureMesh` is the route;
  `mesh_paint_views` stays as the style-control alternative; the Hunyuan paint wrapper, the plan's
  only compile-from-source risk, is deleted.
- **D9 Two models or one. Answered: both, split by capability.** G0.5 weakened one leg
  (`Trellis2MultiViewImageToShape` exists, so multi-view is not Hunyuan-only and that comparison
  moved to G7). Omni block-out control and zero-install smoke testing are what Hunyuan still
  uniquely provides.
- **D10 MV-Adapter. Answered: no, and this is the decision that closed the track.** The case was
  real: measured cross-view drift (adjacent seam 24.1 of 255, front-to-back 30.1), SDXL-based so LoRA
  style control survives, consistency is exactly its claim. Four things decide against it. It
  **unblocks nothing** and no phase since has needed it. The defect it fixes is **below the tolerance
  of the asset class**: 24.1 of 255 is about 9% on props declared convincing at 3 m. A fourth pack is
  a fourth liability on top of `ComfyUI-Hy3D-Omni`, which ships **no licence file at all**, is
  unchanged at `e513cd0` and unpushed since 2025-10-03. And the phase would cost double an
  integration, because these packs share one defect shape (**a control that does not reach the model
  never errors**: G0.5 black albedo, G4c random projection, G8 `auto_bbox`, G9
  `apply_input_rotation`, four for four), so it would have to budget for a swapped-reference null of
  its own. Reopen on a trigger: either a hero tier exists and meshes are viewed at 30 cm, or a route
  needs cross-view consistency to function rather than to improve.
- **D11 The dense mesh, re-measured through a fixed bake. Answered at G7: still nothing, and G3b's
  measurement really was wrong.** Applying `comfy.stage_exports` raises transferred high-frequency
  content on 4 of 4 of G3b's own assets (14x on the fern, 0.02143 against 0.00153), so G3b's staged
  column was reading a rotated cage. The conclusion survives the fix: against the one-shot control
  the aligned dense-mesh bake wins by more than 10% on 1 of 4, draws on one, loses on two. Reopen at
  a few-hundred-face budget or a hero tier; the G7 gate's part D re-scores it from cache with no GPU.
- **D12 Which Omni control a block-out should use. Answered over two phases and closed at G9: the
  point cloud, with the whole ordering measured rather than the winner alone.** Footprint IoU
  **point 0.9106, voxel 0.8507, bbox 0.5766** against a swapped-control null at 0.2667. All three
  modes ship for three different reasons: point is the default because it wins the ground plan;
  voxel is 19% faster and matches point on a compact block-out while losing a thin one to its 16-cubed
  cell size; bbox is the only mode that uploads nothing, so it is the fallback wherever mesh transport
  is unavailable. Two sub-questions closed with it: `Hy3DOmniPoseGenerate` stays dropped (it
  conditions on a skeleton; this suite scatters rocks, trees, plants and grass), and there is no
  sweep to run on the voxel mode because the grid is fixed at 16 cubed inside `OmniEncoder`.

Still open, and therefore kept in [COMFYUI.md](COMFYUI.md#decisions-remaining): **D13** (the terrain
engine's slope-area gradient sign, which belongs to the terrain engine), **D14** (the upstream
`comfy_aimdo` segfault, with its version tripwire in two gates), **D15** (ComfyUI never returning its
VRAM) and **D16** (the foliage guardrail and the foliage generator).

---

## Gates and how to re-run them

A Blender 5.2 binary is available in the CLI environment (`blender-headless-testing`), so these
are measurable rather than `py_compile` theatre.

**Start here: `tools/scripts/headless_comfy_all.py` runs every shipped gate as ONE command**, one
summary line each with the wall clock, the whole-card peak VRAM and the number of checks, and it exits
non-zero if any gate failed. `--fast` passes each gate its own cheap flags (fewer prompts, cached
generations, no slow A/B baseline), which is what a regression check should use; the full run is
GPU-hours and is what a phase verdict needs. `--list` prints the gates and their rough cost, `--gate`
selects a subset, `--verbose` echoes a gate's own output. It re-implements no check: each gate keeps
its own reachability gate and its own exit code, so this is a scheduler and a table.

It earned its keep on the first run. **The G2 gate had been crashing since G4** -- its stand-in
`UILayout` had no `column`, which the stylise block G4 added to the panel body calls -- and it looked
clean the whole time because Blender exits 0 after a script traceback. So the runner reads each gate's
VERDICT LINE and reports "no verdict printed, so the gate did not finish" when there is none, rather
than trusting an exit code. Nobody had re-run G2 in two phases, which is exactly the failure mode the
one-command suite exists to prevent.

- `core/comfy.py` against a stdlib `http.server` fake: queue, jobs-API status shapes, cancel
  idempotency, `/view` bytes, multipart upload, preflight against a canned `/object_info`.
  **Shipped at G1** as `tools/tests/test_comfy.py` (13 tests, no server contacted): the fake covers
  `/system_stats`, `/prompt`, the pending -> in_progress -> completed poll, cancel, `/view` and a
  canned `/object_info` combo, and asserts the client polls the jobs API rather than `/history`.
  It also asserts `tex_tileable` itself loads, has unique `BOB_*` titles, names no cloud node, records its
  upstream template, and is not mutated by templating. **G2 took it to 33 tests**: a multipart
  upload against the fake, one test per preflight failure class (unknown class, cloud node,
  missing model in BOTH combo shapes, duplicate and missing `BOB_*` title, UUID subgraph, plus a
  runtime-bound input that must be skipped), every shipped graph preflighted offline against a
  committed `/object_info` dump, and four scheduler tests (the callback lands on the caller's
  thread, a raising job does not kill the worker, `clear()` drops callbacks so nothing outlives a
  file load, an idle tick costs under 10 ms per fifty).
- `tools/scripts/comfy_preflight.py` over every shipped workflow against the real server,
  including the `BOB_*` title presence and uniqueness check (R12) and the **no-`api_node`
  assertion**, which is the test that keeps local-only true over time. **Shipped at G2**; it also
  runs fully offline against `--object-info` a dump, which is how the same check reaches CI, and
  it prints SKIP and exits 0 with neither.
- `core/comfy_maps.py` on synthetic albedo, and `core/heightfields` on a synthetic mask. **The macro
  half shipped at G5**: five tests in `test_comfy.py` (the mask is the low band and not the high one
  on one image, a flat generation gives 0.5 rather than amplified rounding noise, the blur wraps only
  on the tiled route, the route is a value and the open one drops both tiling nodes with the chain
  rewired, and `heightmap_macro` writes an 8-bit single-channel mask plus a sidecar carrying the
  cutoff) and six in `test_heightfields.py` (`read_png` takes 8-bit while `read_png16` still refuses
  it, the op resamples and blurs and restretches, an empty path is a no-op rather than a raise,
  `with_macro` demotes the generator without mutating the preset, a mask actually pulls a bake toward
  its shape, and the cache re-bakes an edited mask at the same path).
- `core/comfy_maps.py` on synthetic albedo. **Shipped at G1 and extended at G2**: PNG round trip
  for RGB and grey, a decode of **Paeth-filtered** rows built explicitly (Bob's own writer only
  emits filter 0, so the decoder's real input needs constructing), a 16-bit rejection, height
  staying flat on a flat albedo and dropping a low-frequency ramp, `seam_report` separating a step
  edge from continuous noise, and at G2: roughness using the whole band on a bright albedo and
  sitting mid-band on a flat one, unit-length normals that are `(128, 128, 255)` where the relief
  is flat, AO darkening a pit and leaving open surface alone, and `wrap_pad` plus
  `crop_wrap_blend` restoring tileability after a non-periodic disturbance.
- `tools/scripts/headless_comfy_g2.py`, the G2 gate: ten generate-and-accept cycles with the drift
  reported, the longest main-thread block during a background job measured against the blocking
  path, preflight over the shipped graphs and over five deliberately broken ones, the seam before
  and after a `tex_upres` upres, roughness contrast G1 against G2 on the same image, and the `load_post`
  reset. Reachability-gated for the half that needs a server; the preflight, maps and scheduler
  half always runs.
- Headless track A: apply a fixture set to a terrain layer, assert image nodes are wired into the
  master's map sockets and the render is not flat. **G1 added the generated-set counterpart**,
  `tools/scripts/headless_comfy_texset.py`: generate, resolve through `assets.texture_set_maps()`,
  assign, render, and report the stage split. Gated on reachability, so with no server it prints
  SKIP and exits 0, which is itself the check that ComfyUI is never required.
- `tools/scripts/headless_comfy_g3.py`, the G3 gate. **Shipped at G3**, and it caches its
  generated source meshes (with their timings) under `_generated/comfy_g3_check/gen/`, so re-running
  the Blender half costs seconds instead of another 90 s per asset; `--fresh` overrides that and
  `--no-ab` / `--ab-only` split the slow half off. Reachability-gated for the server half.
- Headless track C: import a real generated GLB, run simplify through bake, then assert face count
  is within the budget, a UV layer exists with no overlap, the baked normal map is non-flat,
  bounding-box height matches `height_m`, the origin is at the base, the LOD chain exists, and
  `materials.master_type()` reports a BobShader. **Shipped at G3** over five assets, and it caught
  the one bug that mattered: the BobShader does NOT survive the glTF round trip, because glTF
  carries PBR and not Blender node groups, so `import_generated` has to re-apply
  `bobshade_material` rather than trusting the convert done before export.
- Headless track C, open surfaces: the same over **non-manifold, single-sided geometry**. **Shipped
  at G3**, and it is where the plan's open-surface assumption was corrected twice: the boundary-edge
  count has to be taken after a merge-by-distance or the glTF importer's per-vertex split inflates
  it wildly, and `Trellis2ProcessMesh(remesh=on)` closes the surface outright. Backface culling is
  asserted (off, so a blade renders from behind); the opacity channel is measured and, since G3b,
  reports **wired** on the leaf.
- `tools/scripts/headless_comfy_g4.py`, the G4 gate, in four parts (`--part a,b,c,d`) because they
  cost very different amounts of GPU time. **A**: the normal convention on a sphere and the depth
  linearity against the analytic answer, then `stylize_render` against `stylize_render_est` at two denoise levels with silhouette
  IoU, edge IoU, and Depth Anything V2's reading of each output against Blender's true depth after an
  affine alignment. **B**: an eight-view turntable, `mesh_paint_views` with and without a LoRA, the projection bake,
  and the per-pair overlap MAD plus the front-to-back drift. **C**: a purpose-built ground truth
  whose back cannot be inferred from its front, through `mesh_geom_trellis`, `mesh_geom_mv_trellis` and `mesh_geom_mv`, scored by surface-voxel IoU
  and Chamfer best-over-rotation, whole and back-half, against a self-agreement ceiling. **D**: the
  Advanced-panel operator through the real job queue, with the main-thread tick measured. Every
  generated file caches WITH its timing and VRAM beside it, so a rerun reports what the generating run
  measured rather than a table of zeros. Reachability-gated: with no server it prints SKIP for every
  generation half and exits 0.
- `tools/scripts/headless_comfy_g4c.py`, the G4c gate, in four parts (`--part a,b,c,d`).
  **A**: `export_control`'s round trip, then the ORIENTATION convention, measured over all 24
  axis-aligned rotations on an asymmetric block-out so a mirror cannot pass for a rotation, and the
  assertion that `gen_assets.CONTROL_RETURN_TURN` undoes the exporter's turn. It needs a server but no
  model, so it costs a second. **B**: `mesh_geom_ctrl` against the `mesh_geom_mv_trellis` baseline on three block-outs, two of them the
  shipped `core.proxies`, scored WITHOUT a rotation search: voxel IoU, Chamfer, the XY-projected
  footprint IoU, and the bbox aspect ratio, each against that block-out's own self-agreement ceiling.
  **C**: one block-out all the way through `mesh_geom_ctrl`, `mesh_simplify_uv`, `mesh_texture` and steps 6 to 8, against the G3 asset checks
  it inherits, plus the footprint measured again on the FINISHED asset. **D**: whether Omni can be
  resident alongside SDXL, which on a 16.3 GB card is a real question rather than a formality.
  `--no-baseline` drops `mesh_geom_mv_trellis`, the slow half. Reachability-gated twice over: no server, or no Omni pack
  or weights, prints SKIP and exits 0.
- `tools/scripts/headless_comfy_g5.py`, the G5 gate, in five parts (`--part a,b,c,d,e`).
  **A**: the derivation against `relief()` on one image, the tiled and open seam ratios, the
  composition (`with_macro` demoting the preset's generator), the cache noticing an edited mask at a
  name it has already baked, and the 8-bit budget in levels and in metres. No server, always runs.
  **B**: three prompts, each baked three ways -- mask alone, mask plus preset, preset with no mask --
  scored by band-limited correlation at the mask's own cutoff, by fine relief and median slope in
  metres and degrees, and by a binned slope-area gradient read against the no-mask bake. **C**: the
  same mask at 16, 8 and 5 bits through the identical stack, differenced in metres, checked for
  terracing as histogram concentration and flat-pair fraction, then RENDERED and differenced in
  pixels against both the renderer's own noise floor and a reseeded bake, so a pixel difference has a
  scale. **D**: residency, with the per-process VRAM rule and what `POST /free` reclaims.
  **E**: `Generate Base` and then `Bake + Build` through the real operators and the real job queue,
  with the main-thread tick measured and the assertion that switching the mask off bakes the preset
  exactly as before. Masks cache WITH their timing and VRAM under `_generated/comfy_g5_check/gen/`,
  and part B keeps the raw generation beside each (`heightmap_macro(keep_source=True)`) because the
  8-bit claim can only be audited against the image the mask was derived from. Reachability-gated:
  with no server every generation half prints SKIP and exits 0.
- `tools/scripts/headless_comfy_g6.py`, the G6 gate, in four parts (`--part a,b,c,d`), and the one
  gate here that does **not** run inside Blender. It calls the real MCP tool functions in the MCP
  process and reaches Blender only through `executor.run_build`, the way an agent has to, so a wrong
  tool signature, a wrong contract model or a missing handler fails it where a `core`-level test
  passes. **A**: every new op validated by its model and REJECTED with a readable sentence when given
  bad params (four contract rejections, each a different failure mode, plus two handler rejections
  through a real Blender), every op present in both the dispatch registry and API.md, the macro key
  composed onto a preset-shaped params dict and proved idempotent, and all six `comfy_*` tools against
  a dead port. No server, always runs. **B**: prompt to a scattered asset, with the asset scored from
  the op's own `data` (faces, UV overlap, height, origin, master type) because that is what an agent
  can see. **C**: prompt to a shaded terrain, wall clock per stage, and the masked bake's recipe hash
  differenced against the unmasked one so "the mask reached the bake" is a measurement. **D**:
  websocket progress against status polling, counting updates and how many were per-node. The op lists
  and renders land in `_generated/comfy_g6_check/` so the claims can be audited against artifacts.
- `tools/scripts/comfy_omni_fix.py`, which is a test as much as a fix: `--check` reports whether the
  Omni control projection actually loaded and exits 1 when it did not. The G4c gate runs it, because
  it is the one failure in this integration that no graph-level check can see.
- `tools/scripts/headless_comfy_g7.py`, the G7 gate, in four parts (`--part a,b,c,d`), inside
  Blender. **A**: preflight over every shipped graph offline against the committed dump, the
  `api_node` assertion on the two new graphs, and the route decision as a value in one place
  (`asset_chain` over route, kind and control; `KIND_ROUTE`; `is_foliage`; `stage_exports` on the alt
  chain). No server, always runs, costs a second. **B**: the grid, ten prompts through both models,
  MODEL-MAJOR so each model loads once and its VRAM is attributable rather than a statement about
  swapping, with wall clock, per-process peak VRAM sampled from a thread, faces, boundary edges after
  a weld, thin ratio, UV overlap, chart coverage and in-chart albedo std against the 0.02 black-albedo
  floor; plus the plate control, which is the same subject through `mesh_geom` (no composite) so the Hunyuan
  column cannot be dismissed as a measurement of the background. **C**: three of the ten through
  steps 6 to 8 on BOTH models against the G3 asset checks. **D**: D11, the dense mesh re-measured with
  the bake alignment fixed, on the same four assets G3b used, read straight from the G3b cache so it
  costs no GPU at all. Generated meshes cache WITH their timings and VRAM under
  `_generated/comfy_g7_check/gen/`, subject images are reused from the G3b cache so the TRELLIS.2
  column is directly comparable with that table, `--no-gen` re-scores and `--fresh` regenerates.
  Reachability-gated for the generation half. `--fast` is `--part a,d`, which is the whole
  no-GPU half of the gate.
- `tools/scripts/headless_comfy_g8.py`, the G8 gate, in four parts (`--part a,b,c,d`), inside
  Blender. **A**: preflight over every shipped graph offline against the committed dump, the
  `api_node` assertion on `mesh_geom_bbox`, the control mode as a value in one place (`control_route`'s whole
  truth table, `CONTROL_WORKFLOWS`, `asset_chain` on either control form), the frame mapping checked
  against a control glb's own POSITION accessor extents rather than against the code that wrote
  them, `mesh_geom_bbox`'s `auto_bbox` binding both ways, and D14's tripwire (the installed `comfy-aimdo` version
  against the one G6 measured). No server, always runs, costs a second. **B**: the grid, the same
  three block-outs and the same conditioning image G4c used, three control columns (`mesh_geom_ctrl` point, `mesh_geom_bbox`
  from Bob's proportions, `mesh_geom_bbox` `auto_bbox` as the null), scored with NO rotation search against each
  block-out's own self-agreement ceiling, with the decision rule PRINTED before the table so the
  verdict cannot be chosen after seeing it. **C**: one block-out through `mesh_geom_bbox`, `mesh_simplify_uv`, `mesh_texture` and steps 6 to
  8 against the G3 asset checks, with the footprint checked against this route's own raw mesh rather
  than against the adopted mode's absolute bar. **D**: the transport claim, with `comfy_dir()` forced
  to None and the weights held fixed, so `mesh_geom_ctrl` failing and `mesh_geom_bbox` completing is one variable and not two.
  Meshes cache WITH their timing and VRAM under `_generated/comfy_g8_check/gen/`, so `--no-gen`
  re-scores in about a minute and `--fresh` regenerates. It imports the G4c gate's shape maths,
  block-outs and VRAM sampler rather than copying them, so the two phases' figures are the same
  measurement. Reachability-gated twice over: no server, or no Omni pack or weights, prints SKIP and
  exits 0. `--fast` is `--part a`.
- `tools/scripts/headless_comfy_g9.py`, the G9 gate, in four parts (`--part a,b,c,d`), inside
  Blender. **A**: preflight over every shipped graph offline against the committed dump, the
  `api_node` assertion on `mesh_geom_voxel`, the third mode as a value (`control_route`'s extended truth table
  including its REFUSAL of an unknown name, `MESH_CONTROL_MODES`, `CONTROL_WORKFLOWS`,
  `stage_exports` on the mesh form), the shipped graph's `apply_input_rotation` against
  `comfy.VOXEL_INPUT_ROTATION`, one exporter serving two modes, and D14's tripwire again. No server,
  always runs. **B**: the input-rotation probe FIRST, both settings on the asymmetric block-out, then
  the grid, four columns in ONE session (`mesh_geom_ctrl` point, `mesh_geom_voxel` voxel, `mesh_geom_voxel` with a swapped control, `mesh_geom_bbox` bbox),
  scored with no rotation search against each block-out's own ceiling, with both decision rules
  printed before the table. The swapped-control null is the part worth reusing: each run sees its own
  block-out's image and a different one's control, and it is scored against BOTH, so "the control
  reached the model" is a measurement rather than an inference from a good score. **C**: one
  block-out through `mesh_geom_voxel`, `mesh_simplify_uv`, `mesh_texture` and steps 6 to 8 against the G3 asset checks, footprint checked
  against this route's own raw mesh. **D**: transport, `comfy_dir()` forced to None with the weights
  held fixed, which is where `mesh_geom_voxel`'s one claimed advantage over `mesh_geom_ctrl` turned out not to exist. Meshes
  cache with their timing and VRAM under `_generated/comfy_g9_check/gen/`; `--no-gen` re-scores,
  `--fresh` regenerates, `--no-bbox` drops the column G8 already measured. Imports G4c's shape maths,
  block-outs, VRAM sampler and caching and G3's normal-detail read rather than copying any of them.
  Reachability-gated twice over. `--fast` is `--part a`.
- `tools/scripts/headless_comfy_g3b.py`, the G3b gate: ten prompts through both routes off ONE
  shared `mesh_subject` subject each, with wall clock, per-process VRAM sampled from a thread at the queue
  moment and at the peak, face count, boundary edges after a weld, UV overlap, chart coverage and
  in-chart albedo and alpha statistics; then four of them through steps 6 to 8 on both routes with
  the baked normal's std AND its high-frequency content; then Blender's Decimate floor on the dense
  meshes; then the opacity channel, including one forced wiring that is followed through the glTF
  export and the re-import a scatter layer makes. It caches generated meshes, timings and VRAM under
  `_generated/comfy_g3b_check/gen/`, so `--no-gen` re-measures the whole thing in about four minutes
  and `--fresh` regenerates. Reachability-gated for the generation half.
