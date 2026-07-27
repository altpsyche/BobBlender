# ComfyUI integration

How BobBlenderTools uses a local ComfyUI: what it generates, which workflows do it, what the Blender
side owns, and where the limits are. [USAGE.md](USAGE.md) is what an artist reads instead of this;
this file is for whoever is changing the integration.

**The track is CLOSED.** Thirteen phases (G0 to G9) built it and the last phase question closed at
G9. The compressed record of every phase, the review findings that shaped the plan, and the twelve
answered decisions live in [COMFYUI-MEASUREMENTS.md](COMFYUI-MEASUREMENTS.md). What is still open is
four decisions at the end of this file, and only two of them belong to this track. Reopen on a named
trigger, not for coverage.

Binding facts, because they decide the shape of everything below:

- **Fully local. No API calls.** The six `api_hunyuan3d_*` templates and every `comfy_api_nodes`
  class are out of scope, and it is enforced rather than intended: `/object_info` reports
  `api_node: true` per node, so preflight **rejects** any graph containing one.
- **16 GB VRAM (RTX 5080)**, 583 GB free disk. Disk is a non-issue; VRAM decides every workflow.
- **Two 3D models, each for what only it does.** TRELLIS.2 is primary because it does open surfaces
  and native PBR; Hunyuan3D is retained for multi-view conditioning, Omni control from a block-out,
  and as the zero-install smoke test. See [Model choice](#model-choice-trellis2-primary-hunyuan3d-for-what-only-it-does).
- **Workflows are derived from shipped templates**, not authored from scratch: the four local
  official Hunyuan3D templates and the nine bundled with `ComfyUI-TRELLIS2`. Upstream changes are
  then a diff rather than archaeology.
- **Reference install:** `/home/siva/dev/ComfyUI`, a fork of ComfyUI 0.28.0 set up engine-only
  (custom nodes pinned as submodules). Its own README says workflows and models live outside it, so
  **the workflows belong to BobBlender**.
- **ComfyUI is never required.** With no server the Generate rows read "not connected" and every
  other feature behaves exactly as it does today. The extension zip ships no models and no new hard
  dependency.
- Licensing is not a blocker but it does carry obligations: see
  [Licensing obligations](#licensing-obligations).

---

## What this is for

BobBlenderTools authors procedural worlds (World, Biome, Terrain, Paths, Scatter, Shaders,
Atmosphere). ComfyUI generates raster and mesh data. Tracks are ranked by the gap they close in the
suite, not by novelty. These letters are used throughout the document.

| Track | What it makes | Why it earns its place | Priority |
|---|---|---|---|
| **A. Texture sets** | Seamless PBR sets in `textures/<set>/` | Biggest suite gap. The masters carry per-layer map sockets and, since G0, a sampler that fills them, but only three Poly Haven sets exist on disk. Generation is what makes the sampler worth having. | 1 |
| **B. Mesh texturing** | PBR maps in an existing mesh's UVs | Highest leverage. The block-out proxies already exist and are grey. Painting them is what turns a block-out biome into a look, and it is the half a technical artist does not do by hand. | 2 |
| **C. Mesh generation** | glTF assets from an image or a prompt | Fills the hole the block-out redesign left on purpose: `verdant_trail` and the real-glTF import path were removed, so Scatter has nothing but procedural proxies. | 3 |
| **D. Look-dev stylise** | A styled concept frame from a Bob render | Blender hands ComfyUI a **real** depth and normal pass rather than an estimated one. Output is a pitch frame, not geometry. Shares almost all of its graph with track B. | 4 |
| **E. Macro heightmap** | A low-frequency base for the terrain op stack | Art-directable silhouette as the stack's first input. Strictly subordinate, see the limits. **Shipped at G5**, and the subordination is measured rather than intended. | 5 |
| **F. Sky dome** | Equirectangular HDRI | `library/hdri/` is empty so the intent exists, but Firmament's procedural sky is coupled to `bbt_env` and better. An alternative look, not a replacement. | 6 |

## Honest limits, stated up front

- **Generated meshes are dense triangle soup.** Roughly 50k to 500k triangles, watertight,
  unit-normalised, no quads, no edge flow, no UV seam control, no rigging, convincing at 3 m and
  mush at 30 cm. Fine for scattered background props, because GN instancing stores the mesh once
  and a million-poly scan is already known-cheap in this suite. Not fine for a hero asset without
  retopology. Track C is a scatter-asset factory and the UI says so.
- **Generated meshes have no real-world scale.** Every image-to-3D model outputs a
  unit-cube-normalised mesh. Without a per-asset real height the scatter looks like a toy set,
  which is why `height_m` is mandatory in the manifest below. Most commonly skipped detail in
  AI-to-Blender pipelines.
- **Diffusion heightmaps are not terrain.** Low-frequency, banded, no drainage logic.
  `terrain-sota-research` already landed on keeping the op-stack plus erosion and deferring ML, so
  track E feeds the first op as a macro mask and the fluvial stream-power stack does the real work.
  If E starts competing with the erosion stack it is being misused. **Measured at G5 and the split
  holds**: the mask supplies 0.28 to 0.31 m of relief and the erosion 2.89 to 3.04 m of it on the
  same tile, so the prompt places the massif and the stack builds every slope. The banding half of
  this worry did not survive: see [What G5 measured](COMFYUI-MEASUREMENTS.md#g5).
- **VRAM contention is real and 16 GB is tight.** `/free` alone does not fix it, which is
  [R8](COMFYUI-MEASUREMENTS.md#review-findings-that-changed-this-plan) and, thirteen phases later,
  D15 below.
- **Foliage crowns are the one subject image-to-3D cannot do**, and the rule is in
  [Foliage](#foliage-what-image-to-3d-is-for-and-what-it-is-not-for).

`R<n>` throughout this document refers to a numbered review finding; all of them are in
[Review findings that changed this plan](COMFYUI-MEASUREMENTS.md#review-findings-that-changed-this-plan).
`G<n>` refers to a phase, in [Phase verdicts](#phase-verdicts) and the same file.

---

## Phase verdicts

One row per phase, headline number only. The scope, the full numbers and the corrections each phase
forced are in [COMFYUI-MEASUREMENTS.md](COMFYUI-MEASUREMENTS.md).

| Phase | What it settled | Headline |
|---|---|---|
| [G0](COMFYUI-MEASUREMENTS.md#g0) | Texture-set sampler in Shaders, generated pack root | Passed. A set renders on a terrain layer in EEVEE and Cycles; 11 nodes for one textured layer |
| [G0.5](COMFYUI-MEASUREMENTS.md#g05) | TRELLIS.2 installed and pinned; the bundled graphs run | Passed. 462,140-tri textured GLB in 72 s, 1024³ in 5.7 GB of 16. **Metre-scale input returns a black albedo**, so normalise-then-rescale is mandatory |
| [G1](COMFYUI-MEASUREMENTS.md#g1) | The vertical spike: prompt to a rendered terrain layer | Passed with room. **7.6 s** against a 60 s gate; seam ratio **0.83** against 3.86 untreated |
| [G2](COMFYUI-MEASUREMENTS.md#g2) | Async jobs, preflight, variants, Accept, upres | Passed. Ten sets at a 5.55 s mean, longest main-thread block **16.5 ms** against the blocking path's 5,563 ms |
| [G3](COMFYUI-MEASUREMENTS.md#g3) | Tracks C and B: prompt to a scattered, shaded prop | Passed with two partials. **40 to 203 s** against a 300 s gate; `Trellis2Simplify` 5 of 5 inside budget where Decimate managed 1 of 5 and Quadriflow refused every mesh |
| [G3b](COMFYUI-MEASUREMENTS.md#g3b) | One-shot W9b against the four-graph staged chain | Passed, and it changed the default. **6,276 MiB against 8,586**, a wash on wall clock, 10 to 662 boundary edges against 1,467 to 3,050, and the dense mesh buys no normal detail |
| [G4](COMFYUI-MEASUREMENTS.md#g4) | Stylise (W12) and the style-control paint route (W9) | Passed, with its own real-passes claim **disproved**: silhouette IoU 0.9980 against the estimator's 0.9967, and the true-pass difference is smaller than the estimator's own error |
| [G4c](COMFYUI-MEASUREMENTS.md#g4c) | Omni block-out control (W7) | Passed, and it beat the intended fallback 3 of 3: footprint IoU **0.9079 against 0.6748**, 35.8 s against 164.9 s. Found the wrapper's control signal **silently random** |
| [G5](COMFYUI-MEASUREMENTS.md#g5) | Track E: a prompted macro mask into the op stack | Passed, and it answered R7 in the negative. Silhouette survives erosion at correlation **0.906 to 0.923** against a 0.078 null; erosion still supplies 2.89 m of relief against the mask's 0.28 m |
| [G6](COMFYUI-MEASUREMENTS.md#g6) | The whole surface behind MCP | Passed. Agent goes prompt to scattered asset in **102.5 s** and prompt to rendered terrain in **24.1 s**, no GUI. Found a fork segfault that was not Bob's (D14) |
| [G7](COMFYUI-MEASUREMENTS.md#g7) | Geometry A/B against Hunyuan 2.1, ten prompts | Passed, verdict per class. **Foliage: TRELLIS.2 structurally** (15.0 s against 31.3, half the VRAM, and the challenger cannot leave a surface open). Solids: default holds on licence and VRAM |
| [G8](COMFYUI-MEASUREMENTS.md#g8) | D12 first half: Omni's bounding-box control | Passed, answer no. Eight corners give footprint IoU **0.5766** against 8,192 points' **0.9200**. W7b stays as the no-mesh-transport fallback |
| [G9](COMFYUI-MEASUREMENTS.md#g9) | D12's remainder: Omni's voxel control, and the close | Passed, default unchanged. Ordering measured: **point 0.9106, voxel 0.8507, bbox 0.5766**, against a swapped-control null of 0.2667 |

## Model choice: TRELLIS.2 primary, Hunyuan3D for what only it does

Both, deliberately, split by capability rather than hedged. The division:

| Job | Model | Why |
|---|---|---|
| Geometry with **open surfaces** (foliage, leaves, cloth, thin shells) | **TRELLIS.2** | O-Voxel represents non-watertight geometry. Hunyuan structurally cannot. Decisive for a vegetation suite. |
| **PBR texture** on generated or existing meshes | **TRELLIS.2** | Native base color, roughness, metallic, opacity. `Trellis2TextureMesh` also textures a mesh you already have, which is track B on block-out proxies. |
| Simplify and UV unwrap | **TRELLIS.2 or Blender**, A/B at G3 | `Trellis2Simplify` and `Trellis2UVUnwrap` exist; so do Decimate, Quadriflow, Smart UV Project. Measure, do not assume. |
| **Multi-view conditioning** | **ANSWERED at G4: both, split by cost** | `Trellis2MultiViewImageToShape` (W6t) is the accuracy tier at back-half IoU **0.2637** in 120.4 s; `Hunyuan3Dv2ConditioningMultiView` (W6) is the preview tier at **0.2140** in **24.4 s**, 5x faster with 41% fewer faces. Either beats a single view six-fold on the half it cannot see (0.0439). Revision 5's claim that only Hunyuan could do it was wrong, and the comparison moving to G7 was too cautious: this is a track C route now. |
| **Control from a block-out** (point cloud, voxels, bbox) | **ANSWERED at G4c: Hunyuan3D Omni, and it beat the alternative** | No TRELLIS equivalent, and W7 is now measured against the route that came closest: footprint IoU **0.908** mean against W6t's 0.675 on the same three block-outs, 4.6x faster, 2 GB less VRAM, proportions held to 2% against 23%. The one idea in this plan that turns an existing suite strength into a generation input, and the only route whose OUTPUT ORIENTATION is part of the answer. |
| Zero-install smoke test | **Hunyuan3D native** | Already in ComfyUI core. Proves the Bob-side plumbing before any submodule exists, and since G7 it is also the only route that generates an asset at all with `ComfyUI-TRELLIS2` missing (W8 then W8p then W9t needs TRELLIS2 for the processing and texture halves, so "no custom pack" holds for the geometry only). |
| Watertight hard-surface props | **ANSWERED at G7: Hunyuan is better at exactly this and it still does not become the default** | The claim was right and the measurement is now on file: median **0** boundary edges against TRELLIS.2's 116 on five solids, and **2.1x faster** (40.4 s against 86.1 s). It loses on VRAM (9,688 MiB against 5,958), albedo (0.1259 against 0.1555), one black texture in ten, and a licence that excludes the EU, the UK and South Korea. So `route="alt"` is documented with its numbers rather than promoted, and `comfy.KIND_ROUTE` is empty on purpose. |
| Foliage and any open surface | **ANSWERED at G7: TRELLIS.2, structurally** | 2.9x the boundary edges (median 984 against 344) at 2.1x the speed and half the VRAM, and the challenger's holes are its simplifier's rather than its own: `VoxelToMesh` extracts an isosurface, so a leaf is a leaf-shaped bag whatever the caller asks for. |

### TRELLIS.2: one pack, verified node set

`ComfyUI-TRELLIS2` (PozzettiAndrea, MIT, 541 stars, last push 2026-06-07). Node classes read from
the repo source, with the weights in `ComfyUI/models/trellis2/` via a registered
`folder_paths` entry:

| Node | Stage |
|---|---|
| `LoadTrellis2Models` | loader, registers `models/trellis2` |
| `Trellis2RemoveBackground` | subject cut, `low_vram` toggle, so no separate rembg pack needed |
| `Trellis2GetConditioning` | conditioning |
| `Trellis2ImageToShape` | geometry |
| `Trellis2ShapeToTexturedMesh` | PBR voxel grid from a shape LATENT plus the subs, so no encode step: this is what makes W9b one-shot |
| `Trellis2LoadMesh`, `Trellis2EncodeMesh`, `Trellis2TextureMesh` | **texture an existing mesh** = track B |
| `Trellis2RefineMesh` | detail refinement pass |
| `Trellis2Simplify` | fill holes, DC remesh, unify, simplify |
| `Trellis2UVUnwrap` | UV |
| `Trellis2RenderPreview`, `Trellis2RenderVideo` | preview and turntable |

Bundled workflows to derive from: `geometry_only_512/1024/1536`, `geometry_only_1024_cascade`,
`geometry_texture`, **`standalone_texturing`**, `refinement`, `mesh_audit`, `remove_background`.

Speed reference from Microsoft, on an H100, so treat as a ceiling not a forecast: 512³ about 3 s,
1024³ about 10 s shape plus 7 s material, 1536³ about 60 s.

### Hunyuan3D local geometry: four official templates, zero submodules

Verified read from `comfyui_workflow_templates_json/templates/`. They share one skeleton:

```
ImageOnlyCheckpointLoader -> CLIPVisionEncode(center) -> Hunyuan3Dv2Conditioning[MultiView]
  -> ModelSamplingAuraFlow(1.0) -> KSampler -> VAEDecodeHunyuan3D(8000, 256)
  -> VoxelToMesh(surface net, 0.6) -> SaveGLB
```

| Template | Checkpoint | Latent | Steps | CFG |
|---|---|---|---|---|
| `3d_hunyuan3d_image_to_model` | `hunyuan3d-dit-v2_fp16.safetensors` | 3072 | 20 | 8 |
| `3d_hunyuan3d_multiview_to_model` | `hunyuan3d-dit-v2-mv_fp16.safetensors` | 3072 | 20 | 7.5 |
| `3d_hunyuan3d_multiview_to_model_turbo` | `hunyuan3d-dit-v2-mv-turbo_fp16.safetensors` | 3072 | 20 | 4 (plus `FluxGuidance`) |
| `3d_hunyuan3d-v2.1` | `hunyuan_3d_v2.1.safetensors` (`Comfy-Org/hunyuan3D_2.1_repackaged`) | 4096 | 30 | 5 |

Two conclusions. **Hunyuan geometry needs no wrapper submodule**, which is why it stays as the
zero-install smoke test and the multi-view path. And since only the checkpoint and four numbers
differ, Bob ships **two** Hunyuan graphs (single-view and multi-view) with those values templated,
not four.

None of the four does texture. Under revision 4 that gap was the whole problem; under R21 TRELLIS.2
fills it, and the multi-view template is what Hunyuan is kept for.

### What local-only gives up, and what replaces it

Recorded so the trade is explicit rather than forgotten.

Revised under R21: TRELLIS.2 closes most of what the cloud tier was wanted for.

| Cloud capability rejected | Local replacement | Cost of the swap |
|---|---|---|
| `TencentSmartTopology`, quad retopology, $1.00 | `Trellis2Simplify`, or Blender Decimate for scatter-grade and Quadriflow for hero. A/B at G3. | Small. Quads barely matter on a static instanced prop (R19), and there are now two free candidates instead of one. |
| `TencentModelTo3DUV`, $0.20 | `Trellis2UVUnwrap` or Smart UV Project | Negligible now. |
| `Tencent3DTextureEdit`, $0.60 | **`Trellis2TextureMesh`** via `standalone_texturing` | Effectively closed. Same capability, local, MIT. This was the largest gap in revision 4. |
| `TencentImageToModel` v3.0/3.1 with PBR, $0.20 | **TRELLIS.2** `Trellis2ShapeToTexturedMesh` | Small. Both are recent PBR-capable tiers; TRELLIS.2 additionally does open surfaces, which the Tencent path does not advertise. |
| `Tencent3DPart`, $0.60 | Hunyuan3D-Part / P3-SAM open weights, wrapper needed | Deferred to G8 either way. |

### Open-weight members to add

| Member | Value here |
|---|---|
| **TRELLIS.2-4B** | Primary geometry and PBR. MIT. Open surfaces. Required. |
| **Hunyuan 2.1** | Zero-install smoke test, native. Kept. |
| **Hunyuan 2.0-mv / -mv-turbo** | Multi-view conditioning, as the **challenger** to `Trellis2MultiViewImageToShape` at G7. Demoted from "the reason Hunyuan stays" by G0.5; Omni control is now that reason. |
| **Hunyuan Omni** | **DONE at G4c, 13.5 GB.** Controllable generation from point cloud, voxels, bounding box or skeleton, and it delivered what the plan hoped: condition on a block-out proxy and the asset keeps the silhouette the layout was composed around (footprint IoU 0.814 to 0.979). Two corrections: the control is a MESH the node samples, not a point-cloud file, and the only ComfyUI wrapper for it is unmaintained and ships with the control signal broken. No TRELLIS equivalent. |
| **Hunyuan 2.1 paint** | **Dropped.** Superseded by `Trellis2TextureMesh`, and it carried the plan's only compile-from-source risk. |
| **MV-Adapter** | **Demoted to optional.** It existed to fix `mesh_paint_views`' consistency problem; that route is no longer primary. Keep only if the stylised look in track B needs SDXL LoRA control. |
| **Hunyuan Part / P3-SAM** | Part-swap variation. G8. |

### Enforcing local-only

Not a policy note, a check. `/object_info` sets `api_node: true` for every `is_api_node` class
(`server.py:781`), and those classes also sit under `category = "partner/..."`. Preflight refuses
to queue a graph containing one, and the shipped-workflow test asserts the same over every file in
`assets/workflows/`. So a cloud node cannot arrive by copy-paste from a community graph, which is
the realistic way it would happen.

### Challengers: the slot, and why it is closed

W8 exists so swapping the geometry model is a config change, not a rewrite, and **G7 ran the grid**:
Hunyuan 2.1 against TRELLIS.2 on ten fixed prompts, five of them foliage, one shared subject each.
The verdict is per class and it is in [What G7 measured](COMFYUI-MEASUREMENTS.md#g7).

**The further candidates are dropped rather than deferred**, because the condition on them was
"only if the verdict is close" and it was not close in either direction: 2x on wall clock, 3.7 GB on
VRAM, and a capability difference on foliage that no amount of tuning closes. **Direct3D-S2**
(high-resolution SDF, sharper edges), **Hi3DGen** (normal-bridged detail on rock and bark),
**TripoSG**, **PartCrafter**, **PartPacker** and **TEXGen** are all still real models; adding one now
would answer a question nobody has. The slot is where they go if a question appears, and W8p means
half the work is already done: a new model needs its own generation graph and nothing else.

Plan position: **TRELLIS.2 primary, decided by measurement per asset class at G7. Hunyuan for
multi-view, Omni block-out control, and as the explicit `alt` route when speed or a closed shell
matters more than an MIT licence.**

---

## Licensing obligations

Not a blocker, but three concrete things have to be right.

Simpler under revision 5: the primary model (**TRELLIS.2, MIT**) and the primary custom pack
(`ComfyUI-TRELLIS2`, MIT) are both permissive, and dropping the RMBG dependency removes a
non-commercial term. Hunyuan's Community License now applies only to the retained secondary paths
(smoke test, multi-view, Omni).

1. **Never redistribute weights.** The extension zip ships workflows and code, never models. The
   user downloads their own. This is what keeps the Hunyuan license and every other model's terms
   out of the distribution question entirely.
2. **A `THIRD-PARTY-MODELS.md`** listing every model the shipped workflows reference: name,
   source URL, license, and any territorial or MAU restriction, plus a plain statement that
   output licensing follows the model that produced it. Referenced from the addon preferences
   next to the download links, so an artist sees it before pulling 20 GB. **Shipped at G6** as
   `docs/THIRD-PARTY-MODELS.md`, written by inspecting the install rather than this document, and it
   found four things this section had wrong: `ComfyUI-GeometryPack` is **GPL-3.0** and is a hard
   requirement of the MIT pack that auto-clones it; `ComfyUI-Hy3D-Omni` ships **no licence file at
   all**, so its terms are unstated rather than permissive; and two models are **non-commercial** --
   Depth Anything V2 Large (CC-BY-NC-4.0, W12e only) and 4x-UltraSharp (CC-BY-NC-SA-4.0, W3 only).
   The whole non-commercial surface of the integration is those two optional routes. The addon
   preferences carry a four-line notice naming the territorial exclusion and the two NC routes, which
   is the point an artist decides to download 20 GB.
3. **Provenance in the artifact.** `SOURCE.txt` for texture sets and the sidecar JSON for meshes
   both record the model and its license. When a pack gets shared, the terms travel with the
   asset rather than living only in someone's memory.

The Hunyuan territorial restriction (EU, UK, KR) and the MAU threshold get stated in that file
verbatim rather than paraphrased.

---

## The pinned pipeline

One route, all local. Pinned rather than implied, which is the correction from R3, R4, and R19.

```
1  ComfyUI   reference        prompt -> clean single-subject image with ALPHA     (W4)
              Trellis2RemoveBackground does the cut; no separate rembg pack
            or Blender        mesh or block-out -> 4 cardinal views -> geometry   (W6t / W6)
              MEASURED AT G4 and not a curiosity: one view scores 0.044 back-half
              IoU on an object whose back it cannot see, four views score 0.264
              (W6t, TRELLIS.2) or 0.214 (W6, Hunyuan, 5x faster). Use it whenever
              the back matters and a mesh or block-out exists to render.
            or Blender        block-out proxy -> control MESH -> geometry         (W7, Omni)
              SHIPPED AT G4c. The one route whose input is a shape rather than a
              picture of one, and the only one whose output ORIENTATION is part of
              the answer. `gen_assets.export_control` writes the control and
              `CONTROL_RETURN_TURN` undoes the exporter's turn. Forces the staged
              chain below, because W9b generates its own geometry.
            or Blender        block-out proxy -> three PROPORTIONS -> geometry    (W7b, Omni)
              SHIPPED AT G8. The same route with the control reduced to eight
              corners. `comfy.DEFAULT_CONTROL_MODE` picks between the modes and
              `gen_assets.control_bbox` produces the numbers; nothing is uploaded,
              so this is the one Omni route that needs no `$BOB_COMFY_DIR`.
            or Blender        block-out proxy -> 16-cubed OCCUPANCY -> geometry   (W7v, Omni)
              SHIPPED AT G9, and it closes D12. W7's own control file read as a
              coarse grid instead of a surface: 0.8507 footprint IoU against
              point's 0.9106 and bbox's 0.5766, 19% faster than point, and it
              MATCHES point on a compact proxy while losing a thin one to its own
              21 cm cell. Uploads a mesh, so it needs `$BOB_COMFY_DIR` too.
2-5 ComfyUI  geometry, simplify, UV and PBR in ONE job.  DEFAULT since G3b.       (W9b)
              Trellis2ImageToShape -> Trellis2ProcessMesh(target_face_count) ->
              Trellis2RasterizePBR. Steps 2 to 5 below are the staged alternative,
              still wired: it is the only route that leaves a DENSE mesh on disk,
              and W9t alone is track B on a mesh Bob already has.
2  ComfyUI   geometry         Trellis2ImageToShape -> mesh, open surfaces OK      (W5t)
3  ComfyUI   simplify         Trellis2Simplify.  DECIDED AT G3, see below.        (W9c)
4  ComfyUI   UV               Trellis2UVUnwrap.  DECIDED AT G3, see below.        (W9c)
5  ComfyUI   PBR texture      Trellis2TextureMesh -> base color, roughness,
                               metallic, opacity in the mesh's UVs               (W9t)
6  Blender   bake + derive    bake dense -> low for normal and AO; accept the
                               PBR maps as authored; numpy only fills gaps
7  Blender   finish           scale to height_m, origin to base, weighted normals,
                               LOD chain, BobShader convert, write generated pack
8  Blender   consume          Scatter instances it; the existing weather layer and
                               per-instance variation come along for free
```

Steps 6 to 8 are Blender's and non-negotiable: they are what make a generated asset a first-class
BobShader in a UV layout and a world scale Bob controls, rather than a foreign object (R2).

**Steps 3 and 4: TRELLIS.2, decided at G3, and it was not close.** Measured on the same five
generated meshes at the same 4,000-face budget:

| Route | Inside budget | Median faces | Worst UV overlap | Median UV islands | Wall clock, all five |
|---|---|---|---|---|---|
| **`Trellis2Simplify` + `Trellis2UVUnwrap`** | **5 of 5** | **3,955** | **0.00002** | **419** | **5.95 s** |
| Blender Decimate + Smart UV | 1 of 5 | 12,124 | 0.00378 | 6,035 | 131.95 s |
| Quadriflow + Smart UV | 1 of 5 | 12,124 | 0.00378 | 6,035 | 127.99 s |

Three findings behind those numbers, in descending order of how much they change the plan:

1. **Blender's Decimate cannot reach the budget on generated geometry.** Not "overshoots": it hits
   a hard floor and stops. On the boulder, ratio 0.00817 (a 4,000 target) returns 12,259 faces, and
   every further pass returns the same count; asking for ratio 0.1 on the result changes nothing.
   The same Decimate hits exactly 4,000 on a subdivided icosphere, so this is about the topology,
   not the modifier. Closing the remesh pinholes first (19,623 boundary edges on the boulder,
   1,081 after) lowers the floor to 6,760 and no further. Across the five meshes the floor ran from
   3,998 to 30,921.
2. **Quadriflow refuses every generated mesh.** "The mesh needs to be manifold and have face
   normals that point in a consistent direction", on all five, and it CANCELS rather than raising,
   so a `try`/`except` around it silently leaves half a million faces. R19's "Quadriflow only for
   hero" is therefore dead as written: there is no hero retopology tier locally, and `hero=True`
   falls back to Decimate and says so in the report.
3. **The seam story goes the same way.** UV islands are the honest stand-in for a visual seam
   count, since every island boundary is seam length to hide: `Trellis2UVUnwrap` produced 419
   islands at the median against Smart UV Project's 6,035, on meshes of comparable size. Both
   layouts are effectively overlap-free, so the difference is entirely in fragmentation.

So `gen_assets` shrinks to import, bake and finish, as the plan anticipated for this outcome, and
Blender's Decimate stays only as the route that works with no TRELLIS2 pack installed. The
consequence for the code is better than expected: with steps 3 and 4 on the server, the ENTIRE
ComfyUI half (W4 then W9b, or W4, W5t, W9c and W9t) is one uninterrupted worker-thread job with no
Blender work in the middle, and the whole Blender half runs once in that job's main-thread callback.

**G3b re-measured Blender's floor on four more meshes and it holds**: 493,779 faces in and 6,757
out on the boulder, 442,619 in and 24,327 out on the fern, 471,678 in and 12,119 out on the stump,
487,854 in and 3,998 out on the leaf. One of four inside a 4,000 budget, against G3's one of five.

Two fallback paths stay wired, both cheap to keep:

- **Hunyuan geometry** (W5) instead of step 2, for watertight hard-surface props and as the
  zero-install smoke test that proves the Bob-side plumbing before `ComfyUI-TRELLIS2` exists.
- **`mesh_paint_views`** (W9) instead of step 5, when the look has to be stylised rather than
  plausible: Blender renders the views, SDXL plus LoRA restyles them under depth and normal
  ControlNet, Blender projection-bakes. Demoted from primary by R21 but not deleted, because it is
  the only route with real style control. One session-planning note from G4c: once W7 has run, Omni
  holds 9.6 GB that `POST /free` cannot reclaim, and this route then runs about 30% slower on a
  16.3 GB card because ComfyUI offloads to fit. **Shipped and measured at G4**, and the route is a value
  beside the asset route: `comfy.TEXTURE_ROUTES` is `("pbr", "stylised")` and `comfy.texture_chain()`
  returns `mesh_texture` or `paint_views`. The two have different signatures on purpose, because the
  stylised route needs Blender in the middle (render the views, restyle them, project them back) and
  hiding that would hide the reason it is not one job.

---

## ComfyUI side: the workflow catalogue

Small single-purpose graphs, not one mega graph. Two reasons: 16 GB forces sequential model
loads, and Bob already owns a job queue, so chaining belongs there. Every graph is API format,
lives in `blender/extensions/bob_blender_tools/assets/workflows/`, and binds by **node title**
(`_meta.title` survives a GUI re-export; node ids do not).

Templated titles, standardised across all graphs: `BOB_PROMPT`, `BOB_NEG`, `BOB_SEED`,
`BOB_TEXSEED`, `BOB_SIZE`, `BOB_IMAGE`, `BOB_MESH`, `BOB_POINTS`, `BOB_DEPTH`, `BOB_NORMAL`,
`BOB_CKPT`, `BOB_VAE`, `BOB_LORA`, `BOB_3D_MODEL`, `BOB_UPSCALE_MODEL`, `BOB_TILE`,
`BOB_TILE_VAE`, `BOB_OUT`.

`BOB_TEXSEED` arrived at G3b for the one graph that samples twice: W9b's shape sampler and its
texture sampler are two nodes, and one title binds one node.

These are **binding points, not nodes** (corrected at G2). A node has one `_meta.title`, so two
values that live on the same node bind through one title: denoise is a field on the `KSampler`
titled `BOB_SEED`, not a `BOB_DENOISE` node, and in an img2img graph with no `EmptyLatentImage`
`BOB_SIZE` titles the `ImageScale` that resizes the reference. A graph declares any input Bob
binds at queue time (an uploaded image, say) in `_bob.runtime_inputs` as `TITLE.field`, so
preflight does not test a placeholder against an enum it is legitimately absent from.

A shipped file is `{"_bob": {provenance}, "prompt": {API graph}}`, not a bare API graph: the
`/prompt` validator walks every top-level key as a node, so provenance cannot live beside them.
`_bob` records the upstream template and, importantly, **every deviation from it**, so an upstream
change stays a diff (R17). `core.comfy.load_workflow()` returns the pair.

### Family 1: materials (track A)

**W1 `tex_tileable.json`, SHIPPED at G1.** Prompt to seamless albedo. As built:
`CheckpointLoaderSimple(BOB_CKPT)` -> `SeamlessTile(BOB_TILE, circular)` -> `KSampler(BOB_SEED,
25 steps, cfg 5.0, dpmpp_2m + karras)`, with two `CLIPTextEncode(BOB_PROMPT / BOB_NEG)` and
`EmptyLatentImage(BOB_SIZE)` feeding it, then `MakeCircularVAE(BOB_TILE_VAE)` ->
`VAEDecode(BOB_DECODE)` -> `SaveImage(BOB_OUT)`. Eight nodes.
`BOB_TILE` and `BOB_TILE_VAE` are added to the templated-title list so the tiling mode is a value,
not a graph edit. **Both** circular nodes are load-bearing: UNet-only measures a seam 2.7x the
interior detail, because the VAE decoder's zero padding reintroduces it.
Shot top-down, flat, evenly lit; `comfy.PROMPT_SUFFIX` appends "seamless tileable texture,
orthographic top view, flat even lighting, no shadows, no vignette, no highlights" to whatever the
artist typed, because a generated albedo with baked lighting is unusable and no amount of Bob-side
maths removes it. The negative prompt names shadows, vignette, gradients, highlights, perspective
and horizon for the same reason.

**W2 `tex_tileable_ref.json`, SHIPPED at G2.** Reference photo to seamless set. W1 with the empty
latent replaced by `LoadImage(BOB_IMAGE)` -> `ImageScale(BOB_SIZE)` -> `VAEEncode`, sampled at
`BOB_SEED.denoise` (0.65 by default), with `IPAdapterModelLoader` + `CLIPVisionLoader` +
`IPAdapterAdvanced` locking the palette. Fourteen nodes. The reference is encoded through the
**circular** VAE, not the stock one: a zero-padded encode writes the photo's own edge
discontinuity into the starting latent, which is the seam W1 exists to remove. Measured 8.2 s and
seam ratio 1.03 on a first run.

**W3 `tex_upres.json`, SHIPPED at G2.** Tile to 2K or 4K. `UltimateSDUpscale` at denoise 0.2 with
`4x-UltraSharp` and both circular halves; `seam_fix_mode` is `None` and `force_uniform_tiles` is
on. Nine nodes, roughly 49 s for 1024 to 2048.

The seamless part of W3 is **not** in the graph, and that is the G2 correction worth carrying
forward: the upscaler pads at the image border and denoises per tile, so circular padding cannot
reach the wrap. `core.comfy.upres_variant()` wrap-pads the tile by 128 px on the way out and
cross-fades the duplicated bands on the way back
(`comfy_maps.wrap_pad` / `crop_wrap_blend`). Numbers in [What G2 shipped](COMFYUI-MEASUREMENTS.md#g2).

Map derivation (height, normal, roughness, AO) is **not** in these graphs. It happens Bob-side in
numpy: deterministic, tunable, no submodule. **Shipped at G2**, all five roles from one shared
relief field so the maps describe one surface rather than four similar ones. Cavity is a signal
rather than a sixth file: the roughness consumes it and no master reads a cavity map.

### Family 2: geometry (track C)

Derived from shipped templates per R17, with the upstream template name recorded in each file.

**W4 `mesh_subject.json`, SHIPPED at G3.** Prompt to a clean single-subject reference image with
a REAL ALPHA cutout. W1's SDXL half without the circular padding (a subject is not a tile), then
`Trellis2RemoveBackground` -> `InvertMask` -> `JoinImageWithAlpha` -> `SaveImage`. Ten nodes. No
pad node: `BOB_SIZE` generates square, so the subject is already centred in a square frame.
`comfy.SUBJECT_SUFFIX` appends "single object, centred in frame, full view, not cropped, plain
background", because the geometry model's failure mode on a cropped or busy image is silent and
costs a whole generation.

The `InvertMask` is load-bearing and inverted from the obvious wiring:
`Trellis2RemoveBackground` returns a FOREGROUND mask and `JoinImageWithAlpha` computes
`alpha = 1 - mask`, following ComfyUI's `LoadImage` convention. Wired directly, W4 saves the
background and cuts the subject out.

**W5t `mesh_geom_trellis.json`, SHIPPED at G3, the primary geometry graph.** From
`geometry_only_1024`. `LoadImage(BOB_IMAGE)` -> `InvertMask` -> `Trellis2GetConditioning`, with
`LoadTrellis2Models(BOB_MODEL)` -> `Trellis2ImageToShape(BOB_SEED)` ->
`Trellis2ProcessMesh(BOB_PROCESS)` -> `Trellis2ExportTrimesh(BOB_OUT)` -> `Preview3D`. Eight nodes.

`BOB_MODEL` binds the resolution TIER, not a model file: 512 preview, 1024 default,
**`1536_cascade`** hero. There is no plain 1536 option, so `geometry_only_1536` is the cascade one
and G0.5's "no cascade needed" holds for 1024 only.

`BOB_PROCESS.remesh` is the knob that decides whether the graph can do open surfaces at all. On
(the bundled default) it dual-contours to a watertight shell; off, the same leaf keeps 11,620
boundary edges. `core.comfy.mesh_geometry(remesh=False)` is the foliage route and Generate Asset
picks it from the scatter kind.

**W5 `mesh_geom.json`, SHIPPED at G3.** The Hunyuan fallback and smoke test, from
`3d_hunyuan3d-v2.1`, single view. Zero custom packs, so it is what proves the Bob-side plumbing
without `ComfyUI-TRELLIS2`. `SaveGLB` is a core output node and reports its own filename, so this
is the one mesh graph that needs no `Preview3D`.
`LoadImage(BOB_IMAGE)` -> `ImageOnlyCheckpointLoader(BOB_3D_MODEL)` -> `CLIPVisionEncode(center)`
-> `Hunyuan3Dv2Conditioning` -> `ModelSamplingAuraFlow(1.0)` -> `EmptyLatentHunyuan3Dv2(BOB_LATENT)`
-> `KSampler(BOB_SEED, BOB_STEPS, BOB_CFG, euler, normal)` -> `VAEDecodeHunyuan3D(8000, 256)` ->
`VoxelToMesh(surface net, 0.6)` -> `SaveGLB(BOB_OUT)`.
Defaults 2.1: latent 4096, 30 steps, cfg 5. Swap `BOB_3D_MODEL` to `hunyuan3d-dit-v2_fp16` with
latent 3072, 20 steps, cfg 8 for the faster 2.0 variant. Zero submodules.

**W6 `mesh_geom_mv.json`, SHIPPED at G4.** From `3d_hunyuan3d_multiview_to_model`, using
`Hunyuan3Dv2ConditioningMultiView`, for when a single view guesses the back wrong. Sixteen nodes:
four `LoadImage` (`BOB_VIEW_FRONT` / `LEFT` / `BACK` / `RIGHT`) into four `CLIPVisionEncode` into
`Hunyuan3Dv2ConditioningMultiView`, then the same AuraFlow-shift, `EmptyLatentHunyuan3Dv2(3072)`,
`KSampler(20 steps, cfg 7.5)`, `VAEDecodeHunyuan3D`, `VoxelToMesh`, `SaveGLB` skeleton W5 uses. Views
come either from W4 with a turntable prompt or, better, **from Blender when a block-out exists**,
because then they are consistent by construction rather than by luck; `core.gen_views.turntable_views`
is what renders them. Turbo is this graph with the `-mv-turbo` checkpoint, cfg 4, and a
`FluxGuidance` node, and is not shipped.

`CLIPVisionEncode`'s crop is **`none`** here, not the `center` W5 uses, and it is the template's own
choice: cropping four views independently centres each one differently and the four stop describing
one object. Measured at G4 against the single-view route and against W6t: back-half IoU **0.2140**
from four views against **0.0439** from one, in **24.4 s**, which is 5x faster than W6t for 19% less
back-half accuracy.

**W6t `mesh_geom_mv_trellis.json`, SHIPPED at G4.** W5t with `Trellis2ImageToShape` and its
`Trellis2GetConditioning` replaced by `Trellis2MultiViewImageToShape`, which takes the four image and
mask pairs directly. Thirteen nodes, same four view titles as W6 so one set of Blender renders drives
both, and the same `Trellis2ProcessMesh` / `Trellis2ExportTrimesh` / `Preview3D` tail so the outputs
are comparable file for file. The four `InvertMask` nodes are load-bearing for W5t's reason:
`LoadImage` returns `mask = 1 - alpha`. **The accuracy winner at G4** (back-half IoU 0.2637,
Chamfer 0.0283) and the slower of the two at 120.4 s.

**W7 `mesh_geom_ctrl.json`, SHIPPED at G4c, the block-out control route.** Six nodes:
`Trellis2LoadMesh(BOB_CONTROL)` and `LoadImage(BOB_IMAGE)` into
`Hy3DOmniPointGenerate(BOB_SEED)` fed by `Hy3DOmniLoadPipeline(BOB_OMNI)`, then the same
`Trellis2ExportTrimesh(BOB_OUT)` -> `Preview3D` tail W5t and W6t use, so the output is comparable
file for file with theirs. `core.comfy.mesh_geom_ctrl()` drives it and
`core.gen_assets.export_control()` writes the control.

Four things the plan had wrong about this graph, corrected here:

- **`BOB_POINTS` does not exist and there is no point-cloud file.** The wrapper's control socket is
  TRELLIS.2's `TRIMESH` type, so the control is a MESH that the node samples itself, and
  `Trellis2LoadMesh` is what reads it. That is why W7 needs no exporter of its own: it reuses the
  `unit_normalise_export` round trip track B already owns.
- **`sample_point_count` must be set.** The node's own default of 0 means "use the control mesh's raw
  VERTICES", which is right for the scanned point clouds upstream conditions on and useless for a Bob
  block-out: `Rock_B` has 42. Bob binds 8192 and lets the node area-sample.
- **`BOB_PROMPT` has no home here.** Omni takes no text conditioning at all -- `OmniEncoder.forward`
  takes image, surface, pose, bbox, point and voxel, and nothing else -- so the artist's words reach
  W7 only through the W4 reference image.
- **The result comes back turned.** `Trellis2ExportTrimesh` converts internal Z-up to Y-up on glb
  output and `Trellis2LoadMesh` converts nothing on the way in, so the chain is asymmetric by exactly
  one -90 degree turn about X. `gen_assets.CONTROL_RETURN_TURN` undoes it and part A of the G4c gate
  measures that it does, on an asymmetric block-out so a mirror cannot pass for a rotation.

Needs `ComfyUI-Hy3D-Omni` and 13.5 GB of Omni weights, and needs
`tools/scripts/comfy_omni_fix.py` run once against them. Absent any of that, preflight fails the
graph by class name and every other route is unaffected.

**W7b `mesh_geom_bbox.json`, SHIPPED at G8, the second control mode.** W7 with the control reduced
from a surface to three numbers. Five nodes, and it is W7's file minus one: `LoadImage(BOB_IMAGE)`
plus `Hy3DOmniLoadPipeline(BOB_OMNI)` into `Hy3DOmniBBoxGenerate(BOB_SEED)`, then W7's
`Trellis2ExportTrimesh(BOB_OUT)` -> `Preview3D` tail. There is no `Trellis2LoadMesh`, because the
node has no `control_mesh` socket at all: it takes `bbox_length`, `bbox_height` and `bbox_depth`, and
`OmniEncoder.bbox_to_corners` turns those into the eight corners of a box spanning `[-d/2, d/2]` per
axis. `core.comfy.mesh_geom_bbox()` drives it and `core.gen_assets.control_bbox()` produces the three
numbers from any object.

Three things about it that are not obvious:

- **`auto_bbox` ships FALSE, and true is a null rather than a mode.** True makes the node estimate
  the proportions from the conditioning image's silhouette (Otsu, or alpha when there is one), which
  is a guess at something Bob knows exactly. It is still reachable, as `mesh_geom_bbox(None, ...)`,
  and G8 scores it as the control that says whether Bob's numbers added anything.
- **The three numbers are in the CONTROL GLB's frame, not Blender's.** Blender is Z-up and the frame
  every other Omni route's control arrives in is glTF Y-up, so a Blender `(x, y, z)` extent goes in as
  `(x, z, y)`. `gen_assets.CONTROL_BBOX_AXES` is the one place that lives, and part A of the G8 gate
  checks it against the control glb's own POSITION accessor extents rather than against the code that
  wrote it, because a permuted bbox is still a valid bbox: the model would condition on a plausible
  wrong shape and say nothing.
- **It uploads nothing.** That makes it the one Omni route that does not need `$BOB_COMFY_DIR`, which
  is the failure G7 found over MCP. G9 confirmed that this is W7b's alone: the voxel mode's
  `control_mesh` input is required, so it fails the same way W7 does. It is also why the graph needs
  no unit-normalise round trip: a proportion has no scale to be outside the unit cube with.

**W7v `mesh_geom_voxel.json`, SHIPPED at G9, the third control mode, and the one that closes D12.**
W7's file with `Hy3DOmniPointGenerate` swapped for `Hy3DOmniVoxelGenerate` and nothing else moved:
same `Trellis2LoadMesh(BOB_CONTROL)`, same `LoadImage(BOB_IMAGE)`, same `Hy3DOmniLoadPipeline`, same
`Trellis2ExportTrimesh(BOB_OUT)` -> `Preview3D` tail, same `CONTROL_RETURN_TURN` on the way back.
`core.comfy.mesh_geom_voxel()` drives it and `core.gen_assets.control_signal(mode="voxel")` produces
the control, which is the byte-identical file the point mode gets.

Three things about it that are not obvious:

- **"Voxel" is a branch in the encoder, not a file.** The node area-samples the control mesh and
  `OmniEncoder.generate_voxel` quantises those samples onto a grid whose resolution is a constructor
  argument fixed at **16 cubed**, keeping each occupied cell's centre once. So at most 4,096 points
  reach the DiT however many are sampled, `sample_point_count` is a filling budget rather than a
  detail budget, and the mode's ceiling is set by the proxy's proportions: a cell is a sixteenth of
  the longest axis, and anything thinner is not in the control.
- **`apply_input_rotation` ships FALSE against the node's own default of true.** The node turns its
  control -90 degrees about X before sampling and the point node does not, faithfully reproducing
  upstream's `infer_voxel` and `infer_point`. On Bob's control, which W7 already reads correctly,
  that turn costs 43% of the ground plan and errors nowhere. `comfy.VOXEL_INPUT_ROTATION` is the one
  place it lives and part B of the G9 gate measures both settings before it measures anything else.
- **It is the only mode reachable by name alone.** Point and voxel take the same file, so
  `control_route` cannot infer which was meant from a control path; the default breaks the tie and
  `control_mode="voxel"` is how a caller overrides it, over MCP as well as in-process.

Same pack and same weights as W7, so the same reachability gate and the same
`tools/scripts/comfy_omni_fix.py` requirement apply.

**W8 `mesh_geom_alt.json`, SHIPPED at G7, the A/B slot.** Same inputs and the same output contract
as W5t, challenger model inside, so swapping the geometry model is a config change rather than a
rewrite. The occupant is **Hunyuan3D 2.1**: W5's skeleton (`ImageOnlyCheckpointLoader` through
`VoxelToMesh` to `SaveGLB`, latent 4096, 30 steps, cfg 5) with three nodes added at the front, and
those three are the only reason this file exists beside W5. `EmptyImage(BOB_PLATE)` is a WHITE plate,
`InvertMask(BOB_ALPHA)` turns `LoadImage`'s mask back into the alpha, and
`ImageCompositeMasked(BOB_SUBJECT)` puts the subject on the plate before `CLIPVisionEncode` sees it.
Without that, the challenger is conditioned on the SDXL background: W4's RGB is still the generated
frame behind the cutout and ComfyUI's `LoadImage` drops alpha rather than compositing it, where
TRELLIS.2 gets a real cutout through its mask socket. Thirteen nodes, no custom pack at all.

**W8p `mesh_process.json`, SHIPPED at G7, the shared processor.** `Trellis2LoadMesh` ->
`GeomPackNormalizeMeshToBBox(1.0)` -> `Trellis2ProcessMesh(BOB_PROCESS)` ->
`Trellis2ExportTrimesh(BOB_OUT)` -> `Preview3D`. Five nodes, no model load, no VRAM of its own.
It exists because the A/B has to be controlled: W9b processes its own output with
`Trellis2ProcessMesh`, so the challenger's output has to go through the SAME node at the same face
budget and the same `remesh` branch. Sending it through W9c instead would have scored W9c's sieve as
the challenger's openness (G3b: 1,467 to 3,050 boundary edges against 10 to 146).

The normalise is load-bearing twice, and it is the defect G7 found rather than a tidy-up. Hunyuan
returns a mesh spanning [-1, 1] where TRELLIS.2 returns [-0.5, 0.5], and `Trellis2ProcessMesh` does
not rescale: measured, the processed challenger mesh lands at [-0.55, 0.55], which is outside the
unit cube `Trellis2EncodeMesh` voxelises in, so W9t returns a fully BLACK albedo on **every** asset
(in-chart std 0.0064, mean 0.0001, against 0.1810 and 0.3745 with the normalise in). That is the
G0.5 black-albedo trap for a third time, and the third distinct cause. Running before the process
node rather than after it is the other half: `remesh_band`, `floater_threshold` and `weld_digits` are
lengths, so at two different scales the same numbers are two different settings.

### Family 3: mesh painting and finishing (track B)

**W9t `mesh_texture.json`, SHIPPED at G3, the primary route.** From `standalone_texturing`.
`Trellis2LoadMesh(BOB_MESH)` -> `Trellis2EncodeMesh` -> `Trellis2TextureMesh(BOB_SEED)`, with
`GeomPackUVUnwrap` and `Trellis2RasterizePBR(BOB_TEXSIZE)` -> `Trellis2ExportTrimesh(BOB_OUT)` ->
`Preview3D`. Eleven nodes. Point it at a grey block-out proxy and it comes back textured. Local,
MIT, no compiled rasteriser. This is what revision 4 was trying to build by hand (R21).

Two node swaps against the source, both necessary. `Trellis2LoadMesh` replaces `GeomPackLoadMesh`,
whose COMBO is a cached directory listing (G0.5). `Trellis2ExportTrimesh` replaces
`GeomPackSaveMesh`, which writes through trimesh with no Z-up to Y-up conversion and no UV V-flip,
so its GLB arrives in Blender rotated with the texture upside down. Measured 7 to 46 s per mesh.

**W9b `mesh_geom_texture.json`, SHIPPED at G3b, and the DEFAULT route.** From `geometry_texture`:
geometry plus PBR from one subject image in one job. Ten nodes.
`LoadImage(BOB_IMAGE)` -> `InvertMask(BOB_ALPHA)` -> `Trellis2GetConditioning(BOB_COND)`, with
`LoadTrellis2Models(BOB_MODEL)` -> `Trellis2ImageToShape(BOB_SEED)` feeding both
`Trellis2ShapeToTexturedMesh(BOB_TEXSEED)` (the shape latent and the subs, giving the PBR voxel
grid) and `Trellis2ProcessMesh(BOB_PROCESS)`, then
`Trellis2RasterizePBR(BOB_TEXSIZE)` -> `Trellis2ExportTrimesh(BOB_OUT)` -> `Preview3D`.

**It is one-shot because `Trellis2ProcessMesh` is steps 3 and 4.** That node simplifies, removes
floaters, welds and UV unwraps, so binding `BOB_PROCESS.target_face_count` to the face budget
returns finished topology with charts, and `Trellis2RasterizePBR` bakes the PBR into THOSE charts
while projecting through `original_mesh`, the pre-simplify shape, so the texture still describes the
dense surface. No intermediate simplify pass is skipped, because there is none to skip.
`BOB_PROCESS.remesh` is the same open-surface knob as W5t's and is bound the same way.

Two seeds, so two titles: `BOB_SEED` is the shape sampler and `BOB_TEXSEED` the texture sampler.
Unlike W9t it cannot texture a mesh Bob already has, only geometry it generated itself, which is why
W9t stays the track-B route. Benchmarked against W5t plus W9c plus W9t on ten prompts at G3b and it
won; numbers and the verdict in [What G3b measured](COMFYUI-MEASUREMENTS.md#g3b).

**W9c `mesh_simplify_uv.json`, SHIPPED at G3.** `Trellis2Simplify` then `Trellis2UVUnwrap` as
their own graph. Built to be A/B'd against Blender; it WON that A/B and is now pipeline steps 3
and 4 outright. Five nodes and no model load at all, so it runs in about 1 s per mesh and is also
the cheapest way to smoke-test the mesh transport.

Derived from `refinement` alone. `mesh_audit`, which this plan also named as a source, is built on
`PulseMeshAudit` from a pack that is not installed and is not in `ComfyUI-TRELLIS2`'s declared
`node_reqs`. One numeric limit worth knowing: `Trellis2Simplify.target_face_count` has a minimum of
1000, and preflight does not check numeric ranges, so a smaller budget fails at the validator with
a legible message rather than at preflight.

**W9 `mesh_paint_views.json`, SHIPPED at G4, the style-control alternative.** Twenty-one nodes, and
it is W12 plus an IPAdapter: `core.gen_views.turntable_views` renders N views of the unwrapped mesh
with **true** depth and normal passes, each view goes through img2img at denoise **0.40** (the middle
of R20's 0.3 to 0.45 band) under both ControlNets, `BOB_REF` supplies the IPAdapter reference, and
`core.gen_paint` projection-bakes the results with normal-weighted blending. Demoted from primary by
R21, kept because it is the **only** route with real LoRA style control.

Two details that are decisions rather than settings. The FRONT view is stylised first with the front
RENDER as its own reference, and every later view takes the **stylised front** as the reference, so
the palette is decided once instead of drifting per view; `comfy.paint_views` owns that ordering and
there is a test for it. And the normal ControlNet runs at 0.60 here against W12's 0.45: on a single
frame the normal pass mostly adds surface plausibility, and across a turntable it is what stops the
same texel being lit differently per view.

Measured at G4, eight views of a 3,910-face boulder in 50.9 s: **92.6%** of chart texels painted
directly, adjacent-view seam **22.3 to 26.5 of 255**, front-against-180-degrees drift **30.1**. The
route works; the drift is the honest limit, and MV-Adapter is the known fix with a number to beat.

**W11 `mesh_part.json`** Hunyuan3D-Part / P3-SAM part segmentation for part-swap variation. G8.

`W10` (the Hunyuan 2.1 paint wrapper) is **deleted**, superseded by W9t.

### Family 4: rendering and terrain (tracks D, E, F)

**W12 `stylize_render.json`, SHIPPED at G4.** Bob render plus Bob depth plus Bob normal into a
two-stage SDXL ControlNet img2img. Seventeen nodes:
`CheckpointLoaderSimple(BOB_CKPT)` -> `LoraLoader(BOB_LORA)` -> two `CLIPTextEncode`, with
`LoadImage(BOB_IMAGE)` -> `ImageScale(BOB_SIZE)` -> `VAEEncode`, and the conditioning passing through
`ControlNetApplyAdvanced(BOB_DEPTH_APPLY, 0.85)` then
`ControlNetApplyAdvanced(BOB_NORMAL_APPLY, 0.45)` into `KSampler(BOB_SEED, denoise 0.55)` ->
`VAEDecode` -> `SaveImage`. Chained rather than merged because ComfyUI has no multi-hint apply node,
which is also what makes the two strengths independent values.

Two things about it are not obvious. The normal hint goes through the **union promax** ControlNet plus
`SetUnionControlNetType(normal)`, because there is no standalone SDXL normal ControlNet on disk; depth
uses the dedicated depth model. And `BOB_LORA` is REMOVED from the graph when no style LoRA is asked
for (`comfy.drop_node`), because a `LoraLoader` at strength 0 still has to name an installed file.

**W12e `stylize_render_est.json`, SHIPPED at G4.** W12 with the two hint loads replaced by
`DepthAnythingV2Preprocessor` and `BAE-NormalMapPreprocessor` reading the scaled render itself. It is
the control the real-passes claim was measured against, and the only stylise route available for an
image Bob did not render. **The measurement came back against the real passes on quality and for them
on speed**: see [What G4 measured](COMFYUI-MEASUREMENTS.md#g4).

W12 shares almost all of its graph with W9, which is why it was built first and W9 grown out of it.
No upscale stage: W3 already exists and an upres of a pitch frame is a second press, not a second
graph.

**W13 `heightmap_macro.json`, SHIPPED at G5.** Prompt to a low-frequency macro mask. Deliberately
soft and deliberately 8-bit-tolerant per R7, because it is a mask, not a heightfield.

**It is W1's nine nodes, and the file exists for its values rather than its wiring**, which is
recorded in its own `_bob.notes` so nobody looks for a difference that is not there. What differs:
the prompt brief and negative are a top-down elevation map instead of a material; 20 steps at cfg 4.0
against 25 at 5.0; `euler` with the normal scheduler against `dpmpp_2m` plus `karras`, because karras
front-loads the fine-detail steps and this route discards exactly that half of the schedule; and the
two circular-padding nodes are **droppable** rather than fixed. `comfy.macro_tiling()` decides that
per press, and the default drops them: measured seam ratio 0.80 with padding against 86.18 without,
so the tiled route really does put the same elevation on both borders, which is the repeat a single
terrain tile must not have. `tiled` stays as a value for an endless-sheet case. Bob derives the mask
in numpy (`comfy_maps.macro_field`, one cutoff of the same luminance track A's height channel uses)
and the terrain engine reads it as the `macro` op, which is op 0 of the stack. Verdicts and the
twelve corrections are in [What G5 measured](COMFYUI-MEASUREMENTS.md#g5).

**W14 `sky_equirect.json`** panorama for track F. Last, and only if the procedural sky is ever
insufficient.

### Deriving from the shipped templates

Source of truth for the TRELLIS.2 graphs is the pack's own `workflows/` directory:
`geometry_only_512/1024/1536`, `geometry_only_1024_cascade`, `geometry_texture`,
`standalone_texturing`, `refinement`, `mesh_audit`, `remove_background`. Read them at the pinned
submodule SHA and record which one each Bob graph came from, so a pack update is a diff.

Source of truth for the Hunyuan graphs is
`venv/lib/python3.12/site-packages/comfyui_workflow_templates_json/templates/`, specifically the
**four local** `3d_hunyuan3d*` files. The six `api_hunyuan3d_*` files are out of scope. The copies
already in `user/default/workflows/3D/` include four cloud ones, which can be deleted or kept as
reference; either way preflight will refuse to queue them. Bob's copies record the upstream
template name so an upstream change is a diff.

One caveat: `04_hunyuan_3d_2.1_subgraphed.json` wraps the eight native nodes in a ComfyUI
**subgraph**, so its node `type` is a UUID and the real graph lives under
`definitions.subgraphs`. Bob ships flattened graphs only, and preflight rejects any graph
containing a UUID-typed node, because title-based templating cannot see inside a subgraph.

### Mesh transport

**Shipped at G3, and the way OUT was the part the plan had not thought about.**

Inbound is as the plan expected. `POST /upload/image` writes raw bytes to an arbitrary `subfolder`
with a `commonpath` traversal guard and no image-specific handling (`server.py:396-460`), so a GLB
uploads through it; when the ComfyUI folder preference points at a local checkout Bob copies
straight into `<comfy>/input/3d/` instead, which is faster and one less failure mode. The node on
the far end is **`Trellis2LoadMesh`**, whose `mesh_path` is a free-form STRING, not
`GeomPackLoadMesh`, whose COMBO is a cached directory listing (G0.5).

Outbound needed a discovery. `Trellis2ExportTrimesh` is the only exporter that converts the pack's
internal Z-up to glTF's Y-up and flips the UV V, so Bob's graphs have to end with it, but it is a
V3 node returning a plain STRING and ComfyUI records a node's `ui` dict as its outputs. Measured:
the job completes with `outputs: {}` and `outputs_count: 0`, and there is nothing to fetch.
**`Preview3D` is the fix**, and it is plumbing rather than a viewer: it takes the exporter's path
string and emits a real `{filename, type, subfolder}` entry, which `/view` then serves as bytes,
once the client basenames the filename (the route rejects a leading slash outright,
`server.py:539`). `SaveGLB`, the core node the Hunyuan graph ends with, reports itself and needs
none of this.

### Submodules to add to the fork

Pinned per the fork's convention, each with a `FORK_README.md` row.

| Pack | For | Risk |
|---|---|---|
| **`ComfyUI-TRELLIS2`** (PozzettiAndrea) | **The one required pack**: W5t, W9t, W9b, W9c. MIT, pinned at `9b878516`. | **Retired by G0.5.** Installed and running; 24 nodes. `comfy-env` is mandatory, not optional, but it installs prebuilt CUDA wheels rather than compiling. The one gotcha, `comfy-kitchen` missing inside the isolated pixi env, is written up in [What G0.5 measured](COMFYUI-MEASUREMENTS.md#g05) and in the fork's `FORK_README.md`. `visualbruno/ComfyUI-Trellis2` was never needed. |
| **`ComfyUI-GeometryPack`** (PozzettiAndrea) | **Also required**, declared in TRELLIS2's `node_reqs`. 125 mesh nodes: load/save, decimate, remesh, the `GeomPackUV_*` unwrapper family, preview. MIT, pinned at `c67199d`. | Low. Auto-cloned by comfy-env; pinned explicitly so a fresh clone is reproducible. Its `GeomPackLoadMesh` COMBO caches its directory listing, so Bob must use `GeomPackLoadMeshPath`. |
| **`ComfyUI-seamless-tiling`** (spinagon) | **Required for W1 and W3.** `SeamlessTile` (UNet) and `MakeCircularVAE` (VAE decoder). **GPL-3.0**, pinned `9225ed5`. | **Retired by G1.** Installed and working; pure Python, no dependencies, nothing to compile. Two things to know: **do not use its `CircularVAEDecode`**, which segfaults the server on the second decode of a session, and GPL-3.0 rather than MIT (harmless, ComfyUI is GPL and this extension is `GPL-3.0-or-later`, and Bob ships no node code). The WAS `Image Seamless Texture` alternative was measured and rejected: D4. |
| **`ComfyUI-Hy3D-Omni`** (Rizzlord, **no license file**) | **W7**, the block-out control route. Five nodes: `Hy3DOmniLoadPipeline` plus a Point / Voxel / BBox / Pose generator. Pinned at `e513cd08`. | **Retired by G4c, and it was the real risk in this plan.** It is the ONLY ComfyUI wrapper for Omni that exists (3 stars, 0 forks, no license, last push 2025-10-03, and the better-advertised `PozzettiAndrea/ComfyUI-HunyuanX` is a 404), and it ships with the control signal broken: a vendored rename of `OmniEncoder.linear` to `self.liner` makes the checkpoint's three control-projection tensors load as MISSING under `strict=False`, so generation ignores the control and says nothing. Measured 0.010 voxel IoU before the fix and 0.53 after. `tools/scripts/comfy_omni_fix.py` is the fix; the whole write-up is in [What G4c measured](COMFYUI-MEASUREMENTS.md#g4c) and in the fork's `FORK_README.md`. |
| KJNodes and an image-filter pack | image utility across all graphs | Low. |
| MV-Adapter | W9 style-control route only | Low, but **now optional** rather than required (R21). |
| 16-bit / EXR save | only if R7 is ever revisited | **Measured unnecessary at G5**: 8 bits leaves no terracing, and neither does 5. Deferred on evidence. |
| Hunyuan3D paint wrapper (kijai) | **dropped** | Superseded by `Trellis2TextureMesh`. It was the compile-from-source risk; deleting it is the single biggest risk reduction in revision 5. |
| Background removal (RMBG / BiRefNet) | **not needed** | `Trellis2RemoveBackground` ships in the TRELLIS2 pack, which also sidesteps RMBG-2.0's non-commercial terms. |
| 3D-Pack (MrForExample) | not needed | High. Large compiled dependency tree, and its main draw was TRELLIS support that now has a dedicated pack. |

### Models to download

Staged so each phase starts without waiting on the whole set. Confirm repo, quant, and license at
download time.

| Stage | Model | Lands in | Approx |
|---|---|---|---|
| **Set 0, DONE at G0.5** | `microsoft/TRELLIS.2-4B`. **No quant choice to make**: `pipeline.json` names `*_fp16`/`*_bf16` safetensors and the pack pulls them itself on the first run of `LoadTrellis2Models`. Precision is a runtime combo (`auto`/`bf16`/`fp16`/`fp32`), not a build. | **`models/trellis2/`** (registered via `folder_paths`; also pulls a DINOv3 encoder into `models/dinov3/`) | **~15 GB measured**, not the 4 to 9 GB estimated |
| **Set 1, images, DONE at G1** | **`RealVisXL_V5.0_fp16.safetensors`** (`SG161222/RealVisXL_V5.0`, OpenRAIL++). D1 answered: SDXL, because circular-padding tiling needs a conv UNet. SDXL base itself is not needed; tileable LoRAs are optional and none was required to hit ratio 0.83. | `checkpoints/` | **6.9 GB measured** |
| Set 1, **DONE at G4** | Depth Anything V2 and NormalBAE, auto-pulled by `comfyui_controlnet_aux` on first run. Needed by **W12e** (the estimated-hints control) rather than by W12, which is the point of track D. Also on disk and load-bearing for both stylise graphs: `controlnet-depth-sdxl-1.0` and the **union promax** SDXL ControlNet, which is where the normal head comes from, plus `ip-adapter_sdxl_vit-h` and `CLIP-ViT-H-14` for W9's reference | its own cache, `controlnet/SDXL/` | about 1.5 GB plus the 4.7 GB ControlNet set already there |
| Set 2, Hunyuan, **DONE at G4** | `hunyuan_3d_v2.1.safetensors` from `Comfy-Org/hunyuan3D_2.1_repackaged` for the smoke test (G3), plus **`hunyuan3d-dit-v2-mv_fp16.safetensors`** from `Comfy-Org/hunyuan3D_2.0_repackaged` `split_files/` for W6 (G4). Ungated, no token needed. The multi-view A/B is a comparison rather than a gap, and G4 measured it | **`checkpoints/`** (loaded by `ImageOnlyCheckpointLoader`) | **4.93 GB measured** for the mv checkpoint |
| Set 3, control, **DONE at G4c** | `tencent/Hunyuan3D-Omni`, for **W7**. Skip `*_ema.bin` (the wrapper's `variant: default` never reads it) and the repo assets, which halves the download. Not safetensors: three `.bin` pickles, and `torch.load` reads them with `weights_only=True` on torch 2.13 without complaint. Needs `tools/scripts/comfy_omni_fix.py` run once, or the control does nothing | **`models/hunyuan3d-omni/`**, bound at runtime by `comfy.omni_model_dir()` | **13.5 GB measured** (12.2 model + 1.3 VAE), against the 5 to 10 GB estimated; 25.7 GB if the EMA copy is not skipped |
| Set 4, optional | MV-Adapter, only for the W9 style-control route | per pack | 2 to 4 GB |
| Set 5, A/B | a further challenger (Direct3D-S2, Hi3DGen) only if G7 is close | per pack | 5 to 8 GB |

Background-removal weights are no longer a separate download: `Trellis2RemoveBackground` ships with
the pack.

Keep from the existing install: the **Qwen stack** (`qwen_image_vae`,
`qwen_2.5_vl_7b_fp8_scaled`, `qwen-image-edit-2511-Q5_K_M.gguf`), Apache-2.0 and already the
expensive half of a Qwen route; the **SDXL ControlNet set** (4.7 GB) if SDXL wins D1; the **4x
upscalers**. Everything else (Illustrious checkpoints, IPAdapter face work, ReActor, SAM2,
GroundingDINO, openpose) is out of scope and can be ignored or pruned.

---

## Bob side

### Constraints

1. **Two Python worlds.** `bpy` runs on Blender's bundled 3.13, which has no `httpx`; the tools
   venv is 3.14 and does. So the client is **stdlib only** (`urllib.request`, `json`,
   `mimetypes`), lives in the extension as the single source (the `core/heightfields` pattern),
   and the venv reaches it through a shim shaped like `bobtools/_hfpath.py`. **Done at G1**, and
   `_hfpath` turned out to be the shim: it already puts the extension's `core/` on `sys.path`, so
   `bobtools/comfyui.py` is now a re-export and the `[comfyui]` extra (`httpx`, `websockets`) is
   gone from `tools/pyproject.toml`. One consequence worth noting: `core/comfy.py` imports
   `comfy_maps` through a `try: from . import ... except ImportError: import ...` pair, because it
   is a package member inside Blender and a flat module in the venv.
2. **Never block the UI thread.** A texture set is tens of seconds, a mesh plus paint is minutes.
   One worker thread, a `bpy.app.timers` tick draining a result queue, every `bpy` touch on the
   main thread, registry cleared on `load_post` (R15). **Done at G2** in `core/comfy_jobs.py`,
   measured at a 16.5 ms worst-case main-thread block, and the `load_post` handler must be
   `@persistent` or it is removed by the very load it exists for.
3. **One contract change, one reconnect.** All new ops land in a single `contracts.py` edit, paid
   once, late.
4. **Generated data owns its own pack.** `<output>/packs/generated/` with `textures/<set>/`,
   `models/<kind>/`, its own `pack.json`, and a `_staging/` for unaccepted variants (R9).
   `core.assets.asset_roots()` gains it as a root so accepted output appears in the pickers with
   no configuration. `_staging/` is a SIBLING of `textures/`, not a set inside it, for the reason
   in [What G2 shipped](COMFYUI-MEASUREMENTS.md#g2): the resolver unions every `textures/` directory under a
   root, so a variant staged in there would show up in the picker unaccepted.
5. **UI subtraction.** No new top-level panel. Service surface in the collapsed **Advanced**
   panel beside the MCP Bridge; every action in the panel that owns its artifact.

### New modules

```
core/comfy.py         SHIPPED G1, extended G2. stdlib client (jobs API, /view, upload,
                      service status), title templating, PREFLIGHT, and the texture-set
                      recipe: variants into _staging, Accept, Reject, upres
core/comfy.py         EXTENDED G6. `tiling_values` / `TILING_COPY_MODE` (circular padding applied
                      IN PLACE, which is a crash fix for this fork's staging, not a preference) and
                      `ensure_untiled`, the lazy reset that undoes it before any graph on the same
                      checkpoint that must not wrap. Plus `CLIENT_ID` per process, because ComfyUI
                      keys progress sockets by client id
core/comfy.py         EXTENDED G7. `mesh_geom_alt` (W8) and `mesh_process` (W8p), the
                      `generate_asset_alt` chain built out of them, and the per-asset-class
                      verdict as a value: `KIND_ROUTE` beside `DEFAULT_ASSET_ROUTE`, read by
                      `asset_chain(route, kind, control)`, which also absorbed the "a control
                      forces the staged chain" rule that used to be duplicated at both call
                      sites. Plus `FOLIAGE_KINDS` / `is_foliage`, which was a literal in three
                      places that had already drifted between the product and a benchmark
core/comfy_jobs.py    SHIPPED G2. one worker thread, a bpy.app.timers tick draining a result
                      queue, every bpy touch on the main thread, @persistent load_post reset
core/comfy_maps.py    SHIPPED G1, real at G2. PNG codec, one relief field to height / normal /
                      AO / cavity, local-contrast roughness, wrap pad and blend
core/comfy_ws.py      SHIPPED G6. a minimal stdlib RFC 6455 reader for ComfyUI's /ws, so progress
                      is per-node (`step 7/20`) instead of the job's own status string. Advisory by
                      design: `comfy.wait()` still reads terminal state from the jobs API, so a
                      socket that never connects costs a progress bar and never a result
core/comfy_maps.py    EXTENDED G5. `macro_field` / `macro_from`, the terrain macro mask, which is
                      `relief()` read from the other side of the same cutoff, plus a `wrap` flag on
                      the shared box blur because a terrain tile is not a torus
core/heightfields/    EXTENDED G5. the `macro` generator op (an image as the stack's base:
                      resample, blur, restretch, mix), `params.with_macro` (which prepends it AND
                      demotes the preset's own generator, or the mask would be overwritten by the
                      next op), `macro` as a bake knob so panel / CLI / MCP share one line,
                      `pipeline._stack_file_sig` so the cache notices an edited mask at a name it
                      has seen, and `io.read_png` beside a still-strict `io.read_png16`
core/textures.py      NOT NEEDED. G0 shipped this as core/materials/texset.py plus the
                      existing core/assets.py resolver; there is no second IO layer to write
core/gen_assets.py    SHIPPED G3. glTF import, weld, pinhole fill, Decimate (Quadriflow
                      refuses generated meshes), Smart UV, Cycles bake dense to low, scale to
                      height_m, origin to base, weighted normals, LOD chain, BobShader convert,
                      generated-pack write with a provenance sidecar. bpy-side, and the reason
                      the ComfyUI half is one worker job
core/gen_assets.py    EXTENDED G4c. `export_control` (a block-out proxy out as the control W7
                      conditions on, which turned out to need NO new exporter: it is the same
                      unit-cube round trip track B already owned), `footprint_ratio`, and
                      `undo_exports` plus `turn`, which put every file a chain hands over into
                      one frame. See `EXPORT_TURN` for why that is two bug fixes and not one
core/gen_assets.py    EXTENDED G7. `match_frame` and the `bake_rescale` report: a bake reads
                      across SCALE as well as rotation, and a chain that normalises its low mesh
                      on the server (W8p) leaves the dense mesh at twice the size, which makes
                      every cage ray miss and writes a perfectly flat normal map. G4c put the two
                      meshes in one rotation; this puts them in one frame
core/gen_views.py     SHIPPED G4. bpy-side. A beauty frame plus TRUE depth and normal passes
                      through a view-layer MATERIAL OVERRIDE (not the compositor, which in
                      Blender 5.2 cannot hand a pass back), the geometry-derived depth range,
                      the camera metadata the projection needs, and the isolated flat-lit
                      turntable W9 renders from
core/gen_paint.py     SHIPPED G4. bpy-side. The UV g-buffer (world position and normal per
                      texel, from the same triangle raster gen_assets.uv_counts uses), the
                      per-view projection with a texel-space z-buffer for visibility, the
                      normal-weighted blend, the hole fill, and the cross-view seam and drift
                      report that is the gate for the paint route
assets/workflows/*.json   tex_tileable, tex_tileable_ref, tex_upres, mesh_subject,
                      mesh_geom_trellis, mesh_texture, mesh_simplify_uv, mesh_geom,
                      mesh_geom_texture, stylize_render, stylize_render_est,
                      mesh_paint_views, mesh_geom_mv, mesh_geom_mv_trellis, heightmap_macro
                      and, at G7, mesh_geom_alt and mesh_process shipped: 18 graphs
```

Job orchestration lives in `core/comfy_jobs.py` and the client in `core/comfy.py`;
`comfy_jobs` calls in and nothing in `comfy` calls back out, so the client stays drivable from a
script with no scheduler. Both are bpy-free enough to test in the venv: `comfy_jobs` imports `bpy`
lazily and a test drives `tick()` where Blender drives it from a timer.

`core/comfy.py` surface, as built: `features()` and `has_jobs_api()` to pick the jobs API over
`/history`; `service_status()` from `/system_stats` plus `/queue`; `object_info()` cached per URL,
driving **`preflight()`** (every `class_type` exists, **no node carries `api_node: true`**, every
COMBO value is offered, every `BOB_*` title present and unique, no UUID-typed subgraph node) so a
failure reads "missing model: X" or "cloud node rejected: X" instead of HTTP 400, with `check()`
raising on the lot at once; `queue()`; `job(id)`; `cancel(id)`; `free()`; `view()`;
`upload_image(path, subfolder)`; `template(workflow, values)` binding by title. Preflight is the
highest-value function in the module, because a missing model is the normal failure.

Since G3b it also owns the asset ROUTE, in one place, and G7 widened that one place rather than
adding a second: `asset_chain(route, kind, control)` returns `generate_asset_oneshot` (W4 then W9b,
the default), `generate_asset_chain` (W4, W5t, W9c, W9t, and the only chain that takes a control) or
`generate_asset_alt` (W4, W8, W8p, W9t, the challenger), deciding from three inputs in priority
order: a control forces the staged chain, an explicit route wins next, and `KIND_ROUTE` carries the
per-asset-class verdict. `finish_passes(staged)` maps whatever any of them staged onto
`finish_asset`'s `simplify_pass` and `texture_pass`. `bind_process()` is the shared `Trellis2ProcessMesh` binding, which cannot go
through `template()` because a dynamic combo's sub-widgets belong to the selected key and templating
only merges.

G5 added a third member on the same terms: `macro_tiling(route)` over `MACRO_ROUTES`, which is where
"does a terrain macro mask want to tile" becomes one decision instead of a flag threaded through a
graph. It returns a bool rather than a function because the two routes are one graph with two nodes
dropped, not two chains.

G4c added two members to that same family rather than a third route. `control=` is a value on the
staged chain that swaps step 2 from W5t to W7 and changes nothing else, so the block-out path is one
keyword and not a parallel pipeline; and `stage_exports(staged)` sits beside `finish_passes(staged)`,
reading the same staged dict and returning how many `Trellis2ExportTrimesh` turns `finish_asset` has
to undo on each file it is handed.

**Websocket progress shipped at G6**, not G7. `core/comfy_ws.py` is a stdlib RFC 6455 reader and
`wait()` prefers it, and the reason it was safe to ship is that it is advisory: the jobs API still
decides a job is terminal, so a socket that never connects, drops, or is stolen by another process
costs granularity and cannot cost a result. Measured at 28 per-node updates against 5 status strings
on the same job. It also forced `CLIENT_ID` to carry the pid, because ComfyUI keys progress sockets by
`clientId` and the MCP server and a running Blender both drive the same server.

### Contracts

**Texture set**, matching what `assets.texture_set_dir()` already resolves and the Poly Haven sets
already use:

```
<pack>/textures/<set>/
  <set>_basecolor.png  <set>_roughness.jpg  <set>_normal.png
  <set>_height.png     <set>_ao.jpg         SOURCE.txt
```

**Generated model**, through the existing normalising reader with defaulted new fields (R11):

```json
{"meta": {"generated": true, "model": "hunyuan3d-2.1", "license": "Hunyuan Community"},
 "models": {"tree": [
   {"file": "tree_oak_01.glb", "height_m": 12.0, "lod": [0.5, 0.15],
    "origin": "base", "faces": 4000, "prompt": "...", "seed": 1234}]}}
```

`height_m` is mandatory for generated entries, because every image-to-3D model emits a
unit-cube-normalised mesh and without a real height the scatter looks like a toy set. This is the
most commonly skipped detail in AI-to-Blender pipelines. `origin: base` puts the origin at the
mesh bottom centre so scatter sits on the ground instead of half-buried. A sidecar
`tree_oak_01.json` carries full provenance (R10).

### Ops and MCP

**Shipped at G6, in one contract edit and one reconnect, as planned.** Generation needs no `bpy`, so
the MCP tools live venv-side in `mcp_agent/server.py` and talk HTTP directly; only the steps that
need Blender are ops.

Tools: `comfy_status()`, `comfy_texture_set()`, `comfy_mesh()`, `comfy_paint_mesh()`,
`comfy_heightmap()`, `comfy_stylize()`, every one preflighting before it queues and degrading to a
clear "not reachable" sentence rather than a stack trace (measured: all six, against a dead port).
Each also returns the OP that consumes its result, ready to send: `comfy_mesh` an `import_op`,
`comfy_texture_set` an `apply_op`, `comfy_heightmap` the `bake_params` fragment. An agent that has to
assemble those itself will get one wrong and stop using the feature.

`comfy_paint_mesh` serves the PBR route only (W9t). The stylised route renders turntable views, which
needs Blender, so it stays a panel action; `texture_chain()` already documents that asymmetry and
hiding it behind one tool would hide the fact that one route needs Blender in the middle.

Three ops, one batched contract change: `apply_texture_set` (a set name plus a terrain layer index, or
a material by name), `import_generated` (either `staged` from `comfy_mesh`, which runs pipeline steps 6
to 8 and then imports, or `name` alone to import what the pack already holds), `export_control` (a
block-out proxy out as the control MESH W7 conditions on). Plus one addition the plan had not foreseen:
**`OpResult.data`**, because `export_control` produces a path the next call needs and
`import_generated` produces the face count, UV overlap, height and origin a caller has to check, and
both were otherwise going to be parsed out of an English sentence.

`comfy_heightmap()` needs **no new op at all**, which G5 arranged deliberately: the mask reaches a
bake as the `macro` key of the params dict every existing bake path already takes, so an agent
generates a mask over HTTP with no `bpy` and then bakes it through the terrain op that exists. G6
exposed that key on `bake_heightfield`'s schema and proved an agent can use it, by measuring that the
masked and unmasked bakes of the same preset and seed resolve to different recipe hashes.

The generated pack is the one thing this surface needs configuring, and `$BOB_GENERATED` is it:
`assets.generated_root()` falls back to that variable, so the MCP process (no bpy) and the Blender the
executor spawns (`--factory-startup`, extension imported but not enabled) agree on where an asset
landed. A live session's own output-folder preference still wins, which is why every tool returns the
`pack_dir` it used and every op takes an explicit one.

### UI placement

- **Advanced**, a `ComfyUI` sub-header beside MCP Bridge: **shipped at G2** as a cached status
  line (URL, device, free VRAM, queue depth), Test Connection, Free VRAM, Start Server, Stop
  Server, and a row per running job with a cancel. The state
  is a cache refreshed by a button or a finishing job, never by `draw()`. Stop Server stops only a
  server Bob started; see [What G2 shipped](COMFYUI-MEASUREMENTS.md#g2).
  **`Stylise Last Render` joined it at G4**, with three widgets and not ten: a style prompt, a
  Strength (the denoise, which is the one knob that trades style against silhouette) and the render
  Samples. The ControlNet strengths, the sampler and the negative prompt are values in `core/comfy.py`
  because they have measured defaults, and the optional style LoRA is a preference-shaped string that
  removes the LoRA node from the graph entirely when it is empty. The press renders the camera with
  both passes on the MAIN thread (0.63 to 1.00 s: `bpy.ops.render.render` is bpy) and stylises on the
  worker, which is what the caption says.
- **Shaders**, in the texture-set block: **shipped at G1, finished at G2**. A prompt field, an
  optional reference photo (which switches generation to W2), a seed, a variant count, and one
  `Generate` that runs in the background with a live progress row and a cancel. Staged variants
  appear as a thumbnail plus a picker with `Accept`, `Reject`, `Upres 2x` and `Reject All`; Accept
  moves the variant into the pack and assigns it through `_apply_texture_set`, the same path the
  picker's Apply uses, so a generated set is a texture set like any other from there on. It serves
  the surface master as well as a terrain layer for free, because that block already draws for
  both.
- **Scatter**, beside Make Proxies and Biome Scatter: **shipped at G3** as one `Generate Asset`
  box carrying prompt, kind, real-world height, face budget, seed and a `Hero` toggle, plus the
  cached ComfyUI state so the row reads "not connected" with no server and probes nothing from
  `draw()`. No new top-level panel, and the seed reshuffle routes through the existing
  `scatter_random_seed` operator and `helpers.seed_row` rather than a second copy of the idiom.
  The kind is load-bearing beyond where the asset lands: `plants` and `grass` turn OFF both the
  ComfyUI remesh and the Blender pinhole fill, which is what keeps a leaf a leaf.
  `Hero` raises the bake to 2K and the texture to 2048 and reports that it decimated, because
  G3 measured that Quadriflow refuses generated meshes outright.
  **`Asset from Block-out` shipped at G4c and added no panel and no operator.** It is the SAME
  operator with a `from_control` property, drawn as a second button in the same box and only when the
  active object is a mesh, with a disabled line naming that object and its height. The block-out's
  own Z extent replaces the Height field, because a proxy already placed in a layout has already said
  how big the asset is. The export is bpy, so it happens on the main thread before the job is
  submitted (the G4 pattern), and it is one mesh copy plus one glTF write.
  **G3b changed what the button runs and added no widget to it.** The route is a value
  (`comfy.DEFAULT_ASSET_ROUTE`, read through `comfy.asset_chain()`, with `comfy.finish_passes()`
  mapping whatever it staged onto the two `finish_asset` callbacks), so the box still carries six
  fields and now runs two jobs instead of four. A route radio button would be knob sprawl on a
  decision that has a measured answer.
- **Terrain**: `Generate Base`, labelled a macro mask so nobody reads it as a terrain generator.
  **Shipped at G5, in the existing Terrain panel and above Bake + Build**, because it is an INPUT to
  that press rather than a sibling of it. Four widgets: a prompt, a seed with the shared reshuffle,
  and -- once a mask exists -- a toggle, a Mask Weight and an Invert on one row, with the file name
  underneath. No size widget (the mask is resampled to the stack's macro level, so 1024 is the only
  sensible size and a second resolution knob would just be a way to compose worse), no route radio
  (the tiling answer is measured and no shipped feature stitches tiles), and no staging or Accept
  flow: R9 is about variants awaiting a decision, and this is an input, so it lands in
  `<output>/macro/` beside the terrain rather than in the generated pack's `_staging/` where the
  texture-variant picker would list it. The row reads "not connected" from the same cached state the
  Scatter and Shaders boxes use and probes nothing from `draw()`. Measured through the real
  operators: **0.3 ms of main thread for the press, a 0.08 ms longest tick over 497 ticks**, and
  turning the toggle off bakes the preset stack byte-for-byte as it always did.

Preferences: **shipped at G2**, ComfyUI URL (pushed into the bpy-free client by `set_pref_url`,
the same hand-off `assets.set_pref_roots` uses), ComfyUI folder (only needed for Start Server), and
Reserve VRAM in GB. A workflow folder override and a default face budget arrive with the phases
that need them. No staging retention preference: Reject is a delete and Accept is a move, so
staging holds exactly the results still awaiting a decision and there is nothing to sweep.
**G6 added no preference and one four-line disabled notice** under that block: models are the artist's
download, output licensing follows the model, Hunyuan excludes the EU, UK and South Korea, and two
routes use non-commercial models. That is the licensing obligation delivered where the decision is
made, and it is text rather than a widget because there is nothing to configure.

**G6 left the Start Server command line alone, and that was a decision rather than an omission.** The
copied-VAE segfault this fork has could be fixed there with `--disable-dynamic-vram`, and briefly was;
the shipped fix is Bob-side instead (circular padding in place, `ensure_untiled` to undo it), because
the flag would cost the whole install its weight staging to work around four graphs. The flag remains
the documented fallback for the concurrent-client window. See
[What G6 measured](COMFYUI-MEASUREMENTS.md#g6) and D14.

---

## Gates

Every claim in this document is proved by a script rather than by inspection, and
`tools/scripts/headless_comfy_all.py` runs all of them as one command (`--fast` for a regression
check, `--list` for the gates and their cost, `--gate` for a subset). Each gate is
reachability-gated, so with no ComfyUI it skips cleanly instead of failing. The gates, what each one
measures and how to re-run one on cached generations are in
[COMFYUI-MEASUREMENTS.md](COMFYUI-MEASUREMENTS.md#gates-and-how-to-re-run-them).

## What the redwood-scene run found (2026-07-27)

One session, one reference photograph (a foggy redwood road), one instruction: build it over MCP from
generated assets only. It produced a scene and thirteen findings, and it is the first time the suite
was driven end to end by an agent with no panel clicks. Kept here because a run like this finds what
gates do not: gates assert per feature, a scene asserts the seams between them. Ownership noted per
item; the generation-track ones are D15 and D16 in [Decisions remaining](#decisions-remaining).

1. **ComfyUI never returns its VRAM, and generation plus rendering in one session deadlocks.** The
   full measurement is D15. Cost this run: two dead generate attempts and a manual process kill.
2. **Foliage meshes are the wrong tool for crowns.** The section below, and D16.
3. **A generated texture set is invisible to Blender unless the generated pack is on the ASSET
   search path.** `comfy_texture_set` writes into `packs/generated/textures/<set>/` and returns a
   `pack_dir`, and `apply_texture_set` takes no `pack_dir` at the Blender side, so it resolved
   against the addon's own search path and failed with "no texture set ... (have: grass, rock,
   soil)". Workaround used: symlink each set folder into the in-repo `library/textures/`. Second
   trap inside the first: `assets.texture_set_maps` derives file stems from the FOLDER name, so a
   symlink renamed to something friendlier resolves to zero maps and the set silently reads as a
   solid tint. Owner: MCP/assets. Fix candidates: honour `pack_dir` on the op, or have the addon
   register `$BOB_GENERATED` as a pack automatically.
4. **A texture set assigned to the layer slot a curve band occupies does not reach the band.**
   `apply_curve_surface` takes the first free slot (3 here) and sets its Base Color, Roughness and
   hard edge; assigning a set to that same index reports success and changes nothing on screen,
   tested with three different sets (matte asphalt, pale asphalt, needle duff). So a BobSplines road
   is base-colour-only over MCP. Owner: BobSplines/shading.
5. **`set_env` does not reach materials on its own, and nothing in the op vocabulary re-feeds the
   drivers.** Season, temperature, wetness and snow-line changes produced no pixel change; only
   `shade_terrain` (which calls `feed_env`) or a stack preset carrying a `weather` dict moved the
   material. An agent therefore cannot dial the world state alone. Owner: MCP/world. Fix candidate:
   an `apply_world` op, or make `set_env` call the same applier the World panel does.
6. **A terrain rebuilt with `reset: true` came back unshaded.** After `build_geonodes(recipe=
   "heightmap_terrain", reset=True)` on an existing object, the terrain rendered as default grey
   until it was deleted and rebuilt from scratch, then re-shaded. Suspected cause: the Set-Material
   modifier's position relative to the recipe modifier after a reset. Owner: geonodes/shading.
7. **`build_live` reports `main-thread timeout` on a batch that then completes.** A batch with a
   14,000-face `import_generated` timed out at the client; the assets were on disk and in the scene
   afterwards. There is no way to tell a timeout from a failure, so the safe retry (re-sending)
   risks duplicate objects. Owner: bridge. Fix candidate: a per-op ack, or an idempotency key.
8. **There is no introspection op.** No way to list objects, read a material's layer slots, or ask
   which slot a curve band took, so the run guessed slot indices and rendered probe frames to read
   the scene back. Owner: MCP. Fix candidate: a read-only `describe_scene` op.
9. **Curve shape is role defaults only.** No op sets `bbt_curve.width`/`depth`, so the road's 9 m
   bench had to be swapped for `dirt_path`'s 4.8 m by changing ROLE, which also changes the mask
   channel (`bbt_curve_mask_b` to `bbt_curve_mask`) and therefore every scatter layer's
   `curve_attr`. Owner: BobSplines/MCP.
10. **A curve carves at its own Z until it is draped, and draping needs the terrain's numbers
    restated.** `curve_build` reported "carved terrain (curve Z)" and cut a trench through rising
    ground; `drape_curve` fixed it only when passed the same heightmap, `size`, `height` and
    `sea_level` the terrain was built with. Nothing checks that they match. Owner: BobSplines.
11. **Scatter cannot sink an instance or exclude one asset from a collection.** Generated trees
    carry a wide root flare; with no Z offset and no per-asset filter, the flares float over sloped
    ground and fill the frame, and the only lever is camera placement. Owner: scatter.
12. **SDXL ignores negations, and the subject image decides the asset.** "no pot, no planter, no
    container" returned a nursery pot twice; "bare-root ... on a white studio sweep" removed it in
    one shot. `comfy_mesh` also has no `negative` argument where `comfy_texture_set` does. Owner:
    generation track. Fix candidate: a `negative` argument, and prompt guidance in the tool
    description.
13. **The bbox control cannot express a column, and the mesh control needs `$BOB_COMFY_DIR`.**
    `control_bbox` is clamped to 3.0 per axis, so a 1:9 trunk is unrequestable; asking for
    `[0.35, 0.35, 3]` then failed anyway with "Mesh file not found" because the staged chain uploads
    a mesh and the variable was unset. Owner: generation track (documented behaviour, undocumented
    ceiling).

## Foliage: what image-to-3D is for, and what it is not for

Written after the redwood-scene run of 2026-07-27, which built a full scene over MCP from generated
assets only and put the foliage limit in front of a camera instead of in a gate table. The
measurements were already here (G3, G3b, G7); what was missing was a sentence an artist reads BEFORE
spending 90 s on a tree, and a plan for the thing that actually makes foliage.

**The limit, restated as a rule.** TRELLIS.2 returns one mesh from one image. It has no notion of a
leaf card, an atlas, or a branch hierarchy, and the opacity channel it does emit only becomes a real
cutout when the plausibility rule in `gen_assets.source_opacity` fires. On the redwood run it fired
on nothing: the tree, the sorrel, the grass and the log all came back `opaque` (in-chart alpha mean
0.998, 0.00% below the floor) and the hemlock and the fern came back `implausible` (mean 0.816 and
0.795 with 61.3% and 51.4% of the surface below the floor), which is the guard refusing a channel
that would have made them 60% transparent. So every leaf in that render is opaque geometry, the
baked normal carries no needle-scale detail (G3b: the dense mesh buys none at these budgets), and a
44 m tree scaled from a unit-cube mesh reads as a faceted fan with a flared root skirt.

Where the line falls, from the numbers rather than from taste:

| Subject | Generate a mesh for it? | Evidence |
|---|---|---|
| Rocks, boulders, logs, stumps, debris | **Yes.** This is what the route is good at. | G3/G7 solids: closed shells, budget met, albedo std 0.1555 |
| Ground clumps read at 2 to 4 m (fern, sorrel, grass tuft) | **Yes, as scatter filler.** Do not put the camera on one. | G3 fern frond: open, 51,842 boundary edges, but a bushy volume rather than blades |
| A single leaf or blade as a hero asset | **Only if the gate says `cutout`.** | G3 leaf: 11,610 boundary edges, thin ratio 0.0422, alpha wired at mean 0.9806 |
| A whole tree, or any crown of foliage | **No.** Generate the TRUNK and build the crown. | G3 broadleaf sprig: 15 boundary edges, i.e. a closed blob; redwood run: crowns are fans |
| Bark, duff, moss, needle litter as SURFACES | **Yes, and prefer this.** `comfy_texture_set` is the strong half of the suite. | measured seam ratio 1.02 to 1.11 on the redwood sets |

**The UX guardrail (near-term, in this track).** Three places have to say the same thing, because
the artist and the agent arrive from different doors:

1. **Scatter / Generate Asset panel.** The `kind` selector currently offers trees / rocks / plants /
   grass with no statement of what generation is good at. Trees needs a note reading "generates a
   trunk, not a crown -- crowns come from the foliage generator" and the foliage kinds need "reads
   at 2 m or further". A one-line info row under the kind selector, not a popup.
2. **`comfy_mesh`'s tool description.** The MCP docstring already says "scatter-grade by design:
   dense triangles, no edge flow, convincing at 3 m". It must also say that `kind="trees"` returns a
   single solid mesh with no leaf cards and no alpha, so an agent stops asking for trees.
3. **`import_generated`'s receipt.** It already reports `opacity.verdict` and `warnings`. Add a
   warning when a foliage-ish asset lands with `verdict != "cutout"`: "no usable opacity channel;
   this reads as solid geometry". That is the sentence that would have saved the redwood run its
   three tree attempts.

**The SpeedTree-style track (new feature, raised from here).** The right long-term answer is a
Geometry Nodes foliage generator that consumes ComfyUI textures rather than ComfyUI geometry:

- **Trunk and main limbs**: either a generated mesh (image-to-3D is good at bark) or a GN sweep
  along generated curves, with the generated bark texture set on it.
- **Branch hierarchy**: GN recursion over curves -- length, angle, taper, gnarl, phyllotaxy per
  level, which is exactly what the existing recipe scaffold and the BobSplines curve vocabulary
  already do for paths.
- **Foliage**: alpha CARDS instanced on the branch tips, sampled from a generated needle-spray or
  leaf atlas. W4 already emits a genuine cutout alpha (range 0.000 to 1.000, mean 0.175 measured at
  G3), so the atlas is a tileable-adjacent workflow rather than new model work.
- **Wind and season**: the cards read `S_EnvState` like every other BobShader, so wind and autumn
  colour come for free from the shared env.
- **LODs**: card count and branch depth per LOD, with the existing LOD chain in `gen_assets`.

That is a Bob-side feature with a small ComfyUI dependency (one atlas workflow), so it belongs in
its own plan document (docs/FOLIAGE.md) with this section as its origin, not in the generation
track. What this track owes it: the atlas workflow, and the guardrail above so nobody waits for it
by generating trees.

## Decisions remaining

Four, and only the first two belong to this track. The twelve answered ones, with what answered them,
are in [COMFYUI-MEASUREMENTS.md](COMFYUI-MEASUREMENTS.md#decisions-answered).

- **D15 ComfyUI never gives its VRAM back, and a mixed generate-and-render session deadlocks on it.
  OPEN, found 2026-07-27 while building the redwood scene over MCP.** Symptom: after a run of
  `comfy_*` calls interleaved with `render_scene` on Cycles/OPTIX, every further `comfy_mesh` fails
  with `torch.OutOfMemoryError` raised INSIDE the TRELLIS2 worker (first in
  `_sample_shape_slat_cascade`, then in BiRefNet matting) while `nvidia-smi` shows ComfyUI's main
  process (`./venv/bin/python main.py`) holding 7.3 GB of a 15.5 GB card. The panel's Free VRAM and a
  direct `POST /free {"unload_models":true,"free_memory":true}` both return success and free about
  100 MiB: the pages stay in the main process's torch caching allocator. The TRELLIS2 nodes run in a
  SEPARATE pixi worker process, so they cannot reuse that cache, which is what makes the leak fatal
  rather than merely untidy. Only killing and relaunching `main.py` recovered the card (0.5 GB free to
  12.3 GB). Contributors measured in the same session: the worker parks 3.2 GB of resident weights,
  Blender keeps about 1.1 GB after its own Free VRAM, and each MCP server process that ran
  `bake_heightfield` holds roughly 0.47 GB of CuPy context. This is R8 arriving in practice. Three
  things to settle: whether Free VRAM should escalate (`empty_cache`, then
  `PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True` at launch, then a supervised restart of a server
  Bob did not start); whether the generation tools should preflight `comfy_status().vram_free_mib`
  against a per-route floor and fail with that sentence instead of a CUDA traceback; and whether
  `render_scene` should release Blender's GPU memory afterwards, since an agent that generates and
  renders in one session is now the normal case.
- **D16 The foliage guardrail is copy in three places, and the foliage generator is a new track.
  OPEN, raised 2026-07-27 by the redwood-scene run.** The measurements need no repeating (G3, G3b,
  G7); what is undecided is how loudly Bob says "do not generate a crown". Cheapest version is the
  three edits in [Foliage](#foliage-what-image-to-3d-is-for-and-what-it-is-not-for): a panel info
  row, a sentence in `comfy_mesh`'s description, and an `import_generated` warning when a foliage
  asset has no `cutout` verdict. The louder version refuses `kind="trees"` without an explicit
  `trunk_only=True`, which is a contract change and probably too strong while the foliage generator
  does not exist. Decide the volume, then write docs/FOLIAGE.md for the GN generator itself (trunk
  plus recursive branches plus alpha cards off a generated atlas plus env-driven wind).
- **D13 The terrain engine's slope-area gradient has the wrong sign, and G5 found it by accident.**
  A no-mask `alpine` bake's log-log slope-area gradient is **+0.322**, i.e. slope RISES with upstream
  drainage area, with a strong fit (binned medians, r 0.86 to 0.89 over 30 to 3,000 upstream cells,
  D8 downslope gradient, a 32nd-of-width border margin dropped so the outlet cannot dominate). An
  equilibrium fluvial landscape has a NEGATIVE gradient of roughly 0.3 to 0.6 (Flint's law). A
  finite-iteration stream-power stack with no uplift term is not an equilibrium landscape, so the
  answer may be "nothing to fix, write it down". Worth an afternoon: if it is not expected then every
  mountain preset's valley profile is wrong in a way no visual check has caught, and the fix is an
  uplift term rather than a knob. Not this track's to resolve, and the G5 statistic discriminates
  cleanly whatever the sign means (masked bakes +0.414 to +0.432 beside the null's +0.322, a mask with
  no erosion -0.143 to -0.207). **Belongs with the terrain engine.**
- **D14 This fork's dynamic-VRAM staging segfaults on a copied model, and Bob works around it by not
  copying one.** Found at G6 and not caused by Bob: `comfy_aimdo`'s host buffer is released inside
  `model_patcher.partially_load` and its destructor faults, so the SECOND copied-VAE decode of a
  session kills the whole server. Bob's four tiling graphs were the only thing making a copy, so they
  ask for circular padding IN PLACE (`comfy.TILING_COPY_MODE`) and undo it before anything that must
  not wrap (`comfy.ensure_untiled`). Measured: ten sets in one session, no crash, and the routes that
  must not wrap verified untiled. Dynamic VRAM stays ON, because it is what lets a 16 GB card hold a
  model larger than its free VRAM. **Still open:** the mutation is process-global on the server, so a
  second client generating concurrently could see a padded model inside that window;
  `--disable-dynamic-vram` is the documented fallback. The fix belongs upstream. The re-test condition
  is a fork update, so the version is the tripwire: the installed `comfy-aimdo` is **0.4.10** and
  `host_buffer.py` has not been touched since 2026-07-17. Part A of the G8 gate asserts that and fails
  with "re-run the G6 tiling test and D14" when the version moves, and G9's part A carries the same
  check, so the reminder survives either gate being dropped.
