# ComfyUI integration plan

Status: revision 14. **G0, G0.5, G1, G2, G3, G3b, G4, G4c, G5 and G6 are done**; see
[What G0 shipped](#what-g0-shipped), [What G0.5 measured](#what-g05-measured),
[What G1 shipped](#what-g1-shipped), [What G2 shipped](#what-g2-shipped),
[What G3 shipped](#what-g3-shipped), [What G3b measured](#what-g3b-measured),
[What G4 measured](#what-g4-measured), [What G4c measured](#what-g4c-measured),
[What G5 measured](#what-g5-measured), and [What G6 measured](#what-g6-measured). Everything from G7
on is still plan. **The G1 go/no-go passed with a lot of room: 7.6 s prompt to rendered terrain
layer against a 60 s gate**; G2 generalised that path without losing it (ten sets at a mean of
5.55 s, worst-case main-thread block 16.5 ms against the blocking path's 5563 ms); G3 carried
the same shape into geometry, at 40 to 203 s prompt-to-scattered-prop against a 300 s gate; G3b
replaced the four-graph geometry chain with the one-shot **W9b** after measuring both on ten
prompts; G4 added the two routes that need Blender to hand ComfyUI geometry rather than pixels
(**W12** stylise and **W9** paint), plus the multi-view geometry comparison, and found that one of
those two claims does not survive measurement; G4c shipped **W7**, which makes a Blender
block-out proxy decide an asset's silhouette, and beat the multi-view route it was meant to fall back
on; and G5 shipped **W13**, a prompted macro mask into the terrain op stack, and measured that a
silhouette survives an erosion pass (band-limited correlation 0.906 to 0.923 against a no-mask null
of 0.078 to 0.208) while erosion still builds the landform; and G6 put the whole surface behind MCP,
where an agent goes prompt to scattered asset in **102 s** and prompt to rendered shaded terrain in
**24 s** with no GUI at all, and where a crash that had nothing to do with Bob turned out to be
killing the server. See the verdicts below.

Steps 3 and 4 of the pinned pipeline are **decided**: `Trellis2Simplify` plus `Trellis2UVUnwrap`,
by a wide margin over Blender Decimate and Quadriflow. The measurements and the three findings
behind that are in [The pinned pipeline](#the-pinned-pipeline).

`tools/bobtools/comfyui.py` is no longer a dormant 68-line `httpx` client; it is a re-export of
the one stdlib client at `core/comfy.py`, and the `[comfyui]` extra is gone with it.

Reference install: `/home/siva/dev/ComfyUI`, a fork of ComfyUI 0.28.0 set up engine-only (custom
nodes pinned as submodules, `./dev setup`, `./dev run`), whose README states workflows and models
live outside it. So **the workflows belong to BobBlender**.

Settled: models get downloaded fresh (the installed anime and character set is irrelevant), 3D
generation is in scope, and submodules may be added. Workflows are **derived from shipped
templates**, not authored from scratch (R17): the four local official Hunyuan3D templates bundled in
this install, and the nine workflows bundled with `ComfyUI-TRELLIS2`.

**Two 3D models, each for what it is uniquely good at** (R21): **TRELLIS.2** is primary because it
does open surfaces and native PBR, and **Hunyuan3D** is retained for multi-view conditioning, Omni
control from a block-out, and as the zero-install smoke test. Not a hedge; they do different things.

**Fully local. No API calls.** The six official `api_hunyuan3d_*` templates and every
`comfy_api_nodes` class are out of scope, and this is enforced rather than merely intended:
`/object_info` reports `api_node: true` per node (`server.py:781`), so preflight **rejects** any
graph containing one. What that costs and how the plan absorbs it is R18 to R20 below.

Licensing is not a blocker: nothing commercial yet, and the Hunyuan Community License is
permissive below 1M MAU, which a Blender tool that ships no weights will not approach. What that
does still require is spelled out in [Licensing obligations](#licensing-obligations).

Binding environment numbers: **16 GB VRAM (RTX 5080)**, 583 GB free disk. Disk is a non-issue.
VRAM decides the shape of every workflow below.

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
  this worry did not survive: see [What G5 measured](#what-g5-measured).
- **VRAM contention is real and 16 GB is tight.** See R8.
- **ComfyUI is never required.** No server means the Generate rows read "not connected" and every
  existing feature behaves exactly as today. The extension zip ships no models, no ComfyUI, and no
  new hard dependency.

---

## What G0 shipped

The texture-set sampler, panel-only and in-process, plus the generated pack root. No ComfyUI is
involved and nothing here needs an MCP reconnect. Files: `core/materials/texset.py` (new),
`core/materials/{shared,terrain,surface}.py`, `core/assets.py`, `core/shading.py`,
`ui/shaders.py`, `__init__.py`, `tools/scripts/headless_texset.py` (new).

**Shape.** One shared `S_TexSet` group holds the fold maths and is instanced per textured layer;
image texture nodes are created only for layers that actually carry a set. One `ShaderNodeMapping`
per set carries the tiling and feeds that set's image nodes; one `ShaderNodeTexCoord` serves the
whole material. Triplanar is Blender's own box projection on the image node
(`projection = 'BOX'`, `projection_blend`), which is one property rather than a hand-rolled
three-sample graph and behaves identically in EEVEE and Cycles.

**Measured (headless, Blender 5.2, `tools/scripts/headless_texset.py`, all checks pass):**

| Metric | Value |
|---|---|
| Nodes in `M_Terrain`, one textured layer | **11** (4 image textures) |
| Nodes in `M_Terrain`, two textured layers | **17** (8 image textures) |
| Nodes in an untextured terrain wrapper | 3, byte-identical to before G0 |
| Worst case, six textured layers | 4 images/layer x 6 = 24 images, 39 nodes |
| EEVEE render of the `grass` set on a terrain layer | compiles and renders, luminance range **0.6118** (not flat) |
| Cycles render, same scene | renders, luminance range **0.6157** |

So R14's thirty-image worry lands at twenty-four in the worst case and four in the realistic one.

**Five things revision 5 had wrong, corrected here:**

1. **`S_TerrainMaster` does not carry Metallic Map or AO Map sockets.** Per layer it carries
   `Albedo Map` (colour), `Roughness Map` (float) and `Detail Height` (float). Only
   `S_SurfaceMaster` has the four. So the sampler **folds AO into the albedo**, which is the
   convention `surface.py`'s own AO Map comment already documented, and leaves Metallic alone
   (no shipped set has a metallic map, and both masters already carry a Metallic scalar).
2. **The masters have no normal socket, so the normal map on disk is unused.** Instead the
   height map drives a `ShaderNodeBump` into the wrapper's Principled Normal: on terrain from the
   master's blended `Height` output, which was previously built and left dangling, so the relief
   follows whichever layer wins the height-lerp per texel. Adding a real normal path would mean a
   per-layer vector socket, hence an `S_GROUP_VER` bump, which resets every tuned terrain in the
   file. Not worth it for G0, and box projection has no reliable tangent space anyway.
3. **Terrain projects from OBJECT coordinates in both modes.** A GN-generated grid carries no UV
   layer, so "triplanar off" cannot mean UV there; it means a top-down planar projection. Surface
   props do use their UVs when box projection is off.
4. **Triplanar is per material, not per layer.** Six independent projection toggles on one ground
   material is knob sprawl with no case behind it.
5. **`ARCHITECTURE.md` claimed `bbt_shaders` held the texture-set pickers.** It did not. It now
   holds one staged pick; the assignment itself lives on the MATERIAL (`bbt_texsets`,
   `bbt_texset_box`), so a rebuild for any other reason carries it forward instead of silently
   dropping back to tints. That line is corrected.

**Durability.** Assigning a set is structural (it rewires the graph), so it goes through
`_build_wrapper`'s signature path and uses the staged-pick-then-Apply panel idiom rather than an
instant preset. `_build_wrapper` gained a second snapshot covering every `TexSet *`-named node, so
assigning a set to a second layer no longer resets the first layer's tiling, AO amount, or bump
strength. `S_TexSet` carries its own version in `_GROUP_VER_OVERRIDE` and folds it into the
wrapper signature, so changing the sampler later rebuilds the wrappers that instance it without
costing every terrain in the file a revert-to-default.

**Generated pack root.** `<output>/packs/generated` is created with its `pack.json` on register
and pushed into `assets.asset_roots()` via `set_generated_root()`, the same bpy-free hand-off
`set_pref_roots()` already used. It sits after the preference folders and before the dev
`library/`, so generated output is discoverable with no configuration but a curated pack of the
same name still wins.

## What G0.5 measured

**Verdict: the pack works, TRELLIS.2 stays primary, and the open-surface claim is confirmed by
measurement rather than by the model card.** But it did not install cleanly, and the reason is
worth reading before anyone repeats it.

### The install, and the one thing that breaks it

The README's "manual (most reliable)" route is **not** a way to avoid `comfy-env`:
`install.py` is literally `from comfy_env import install; install()`, and the pack's `__init__.py`
registers its nodes through `comfy_env.register_nodes()`. There is no non-comfy-env path. Revision
5 read the README's warning as "prefer the manual route"; the honest reading is "comfy-env is
mandatory, the manual route only changes who invokes it".

What comfy-env does is better than feared: it builds an isolated pixi env at `~/.ce` and installs
**prebuilt** CUDA wheels (`flash-attn`, `sageattention`, `cumesh-vb`, `drtk`, `flex-gemm-ap`,
`o-voxel-vb-ap`, all `cu128torch2.8`). Nothing compiles from source. `pip install -r
requirements.txt --upgrade` touched no torch in the main venv.

**The failure.** On first start the pack registered **0 nodes**, silently, with the reason only in
the log:

```
[comfy-env] Metadata scan failed for nodes (exit 1, 5.9s):
  ModuleNotFoundError: No module named 'comfy_kitchen'
```

The isolated env imports **this fork's** `comfy/` source, and `comfy/model_base.py` imports
`comfy.ldm.joyimage.model`, which needs `comfy_kitchen`. That package is in the fork's own venv and
not in the pixi env. Fix:
`~/.ce/.pixi/envs/trellis2-nodes/bin/python -m pip install comfy-kitchen==0.2.20`. After that,
**24 TRELLIS2 nodes** plus **125 GeometryPack nodes** register. Recorded in the fork's
`FORK_README.md` so it is not rediscovered.

This is a **fork-specific** interaction, not a general pack bug, and it is exactly the class of
problem G0.5 existed to find. Any pack that comfy-env isolates will hit it on this fork.

### Measurements, RTX 5080 16 GB

Driven through the HTTP API rather than the GUI (this ran headless), with a UI-to-API converter
that maps each node's declared widget inputs onto `widgets_values` in order. Peak VRAM is the
whole-card figure from `nvidia-smi`; delta is above the baseline at queue time, which drifted
because a second ComfyUI was resident on the same card.

| Run | Wall clock | Peak VRAM | Result |
|---|---|---|---|
| `geometry_only_1024`, **cold** (includes the ~15 GB model pull) | **680 s** | 5681 MiB | 498,682-face mesh, UV unwrapped |
| `geometry_only_1024`, **warm** | **87 s** | 5667 MiB | same |
| `geometry_texture` (bundled cannon, 512 shape + PBR) | **72 s** | 4680 MiB | 32 MB GLB, 462,140 tris, two 2048² maps |
| `geometry_texture` (leaf, alpha-masked) | **150 s** | 4215 MiB | 16 MB GLB, 195,342 tris, thin open surface |
| `standalone_texturing` (bundled pineapple STL) | **104 s** | 5123 MiB | 14 MB GLB |
| `standalone_texturing` (Bob block-out proxy, **metre scale**) | 172 s | 4375 MiB | **black texture, see below** |
| `standalone_texturing` (Bob block-out proxy, **unit-normalised**) | **10 s** | 4384 MiB | 1.6 MB GLB, correctly textured, 76 tris unchanged |

**1024³ fits 16 GB one-shot with room to spare.** Peak never exceeded **5.7 GB** of 16 GB across
every run, so `geometry_only_1024_cascade` is not needed on this card, and 1536³ is worth trying.
Microsoft's H100 reference (10 s shape + 7 s material at 1024³) is roughly 5x optimistic for a
5080, which is a reasonable scaling.

`Trellis2ProcessMesh` inside `geometry_only_1024` goes 9.2M verts / 20.7M faces raw, to a quad DC
remesh, to floater removal, to a 500k-face simplify, to weld, to UV unwrap, landing at 498,682
faces with UVs. So the pack already does pipeline steps 3 and 4 to a usable standard, and its
`GeomPackUV_*` family additionally exposes xatlas, LSCM, ARAP, harmonic, and Blender's own smart /
cube / cylinder / sphere unwrappers. The G3 A/B against Blender is now a comparison between two
real implementations rather than a hope.

### The finding that matters most for track B

Texturing the Bob block-out proxy **at its real 4.7 m height returned a fully black albedo**.
Re-exporting the identical proxy normalised to the unit cube returned a correct texture in 10 s.
`Trellis2EncodeMesh` voxelises in unit-cube space, so a metre-scale mesh lands outside the grid and
the encoder sees nothing. It does not error; it returns black.

So **normalise on the way out and rescale on the way back** is a mandatory `gen_assets` step, not a
nicety, and it pairs exactly with the mandatory `height_m` in the generated manifest. Worth an
assertion in the G3 headless test: a black or near-constant albedo means the mesh was out of range.

### Open surfaces: confirmed, with numbers

The whole reason TRELLIS.2 is primary. Measured on the leaf GLB in Blender:

| Metric | Value |
|---|---|
| Boundary edges (exactly one adjacent face) | **83,292** of 334,659 |
| Non-manifold edges (more than two faces) | 0 |
| Watertight | **No** |
| Vertex bbox extents | 0.3837 x **0.0596** x 1.0045 |
| Thinnest / longest axis | **0.059** |

A genuinely thin, open, single-sided leaf: a line edge-on and a veined blade face-on. Hunyuan's
watertight SDF-voxel output cannot represent this at all, so R21's decisive argument holds up.

> **Corrected at G3, and the thinness survives while the openness does not.** The boundary-edge
> figure above was measured on an unwelded glTF import. glTF stores UVs and normals per vertex, so
> the importer splits a vertex at every UV seam, and a fully CLOSED mesh then reports tens of
> thousands of "boundary" edges. Measured on a G3 boulder: 233,812 boundary edges of 489,781 faces
> on import, 19,623 after a merge-by-distance; on a G3 leaf, 0 after welding. So **83,292 is an
> upper bound, not a measurement**, and "Watertight: No" followed from the same artefact.
>
> The thinness figure is real and reproduced: a G3 leaf came back at a **0.046** thinnest/longest
> axis ratio against this run's 0.059.
>
> The open-surface capability is real too, and G3 found where it went: **`Trellis2ProcessMesh`
> with `remesh: on`, which is what every bundled `geometry_only_*` graph ships, runs a
> dual-contouring remesh that returns a watertight shell.** The same leaf measures 0 boundary
> edges with it on and **11,620** with it off. So the capability R21 rests on is present in the
> model and switched OFF by the shipped graph's default, and W5t binds `remesh` as a value:
> foliage generates with it off, solids with it on.

One caveat found the hard way: **the subject image needs a real alpha cutout.** The first attempt
used an opaque PNG with a white background, and since the workflow feeds `LoadImage`'s mask
(inverted) into `Trellis2GetConditioning`, the whole square frame became the subject and the result
was a leaf card sealed inside a transparent unit-cube shell. With alpha, the same seed and prompt
give the clean leaf above. `Trellis2RemoveBackground` exists in the node set for exactly this, and
W4's contract must guarantee an alpha channel, not merely a white background.

### Corrections to revision 5's TRELLIS.2 section

1. **There is no fp8 or GGUF build to choose.** `pipeline.json` names `*_fp16` and `*_bf16`
   safetensors; quantisation is a runtime `precision` combo on `LoadTrellis2Models`
   (`auto`/`bf16`/`fp16`/`fp32`), not a separate download. The "4 to 9 GB depending on quant"
   estimate is wrong: the pull is **~15 GB** and it is automatic on first run, not a manual
   download.
2. **`LoadTrellis2Models` takes no model filename.** Its widgets are `resolution`
   (`512`/`1024`/`1024_cascade`/`1536_cascade`), `precision`, and `attn_backend`. So `BOB_3D_MODEL`
   templating does not apply to it, and R6's model-enum resolution is a Hunyuan-only concern.
3. **`Trellis2MultiViewImageToShape` exists.** TRELLIS.2 is not single-image-only. That weakens,
   though it does not delete, the case for keeping Hunyuan: Omni block-out control still has no
   TRELLIS equivalent, and Hunyuan remains the zero-install smoke test. **The multi-view
   justification is now itself a G7 A/B, not an assumption.**
4. **The node set is larger and differently named than the table said.** Also present:
   `Trellis2ProcessMesh` (the remesh/simplify/weld/unwrap pipeline the bundled graphs actually
   use), `Trellis2RasterizePBR`, `Trellis2ExportTrimesh`, `Trellis2ExportGLB`,
   `Trellis2DecodeSSLatent`, `Trellis2Empty3DLatent`, `Trellis2LoadSSFlowModel`,
   `Trellis2SSConditioning`, `Trellis2ApplyGuidanceInterval`, `Trellis2ShapeToMesh`.
   `Trellis2Simplify` and `Trellis2UVUnwrap` do exist but the shipped graphs prefer
   `Trellis2ProcessMesh` and `GeomPackUVUnwrap`.
5. **`ComfyUI-GeometryPack` is a second required pack**, declared in TRELLIS2's
   `comfy-env-root.toml` `node_reqs` and auto-cloned. Revision 5's submodule table did not mention
   it. Both are now pinned submodules in the fork.
6. **The bundled graphs do not use `Trellis2RemoveBackground`.** They take `LoadImage` plus
   `InvertMask` and feed the mask to `Trellis2GetConditioning(background_color="black")`. The
   rembg node is available but not on the shipped path.
7. **Generated meshes really are unit-normalised**, as the honest-limits section warned: the
   cannon came back 0.674 x 1.005 x 0.599. `height_m` stays mandatory.

### One thing to know before automating this

`comfy-env` **caches each node's scanned schema**, so a COMBO whose options are a directory listing
(`GeomPackLoadMesh.file_path`) does not pick up a newly written file even across a server restart.
Bob's mesh-upload path must therefore use `GeomPackLoadMeshPath`, which takes a free-form path, not
the combo node. This is a real constraint on W9t and the whole track-B upload story, and it belongs
in preflight.

## What G1 shipped

**Verdict: go.** Prompt to a rendered terrain layer in **7.6 s** against a 60 s gate, seamless by
measurement rather than by eye, and the whole path is four files. Nothing about the plan's shape
needs to change; several of its details were wrong and are corrected below.

Files: `assets/workflows/tex_tileable.json` (W1, new), `core/comfy.py` (new),
`core/comfy_maps.py` (new), `ui/shaders.py` (one Generate row plus the operator), `__init__.py`
(one restricted-context fix), `tools/scripts/comfy_ui_to_api.py` (new),
`tools/scripts/headless_comfy_texset.py` (new), `tools/tests/test_comfy.py` (new),
`tools/bobtools/comfyui.py` (now a re-export), `tools/pyproject.toml`.

### Measured, RTX 5080 16 GB, 1024 square, 25 steps

Wall clock through the real Generate button in the panel, headless Blender 5.2,
`tools/scripts/headless_comfy_texset.py`:

| Stage | Warm server | Note |
|---|---|---|
| generate (queue, sample, fetch over `/view`) | **5.0 s** | 25 steps, dpmpp_2m + karras |
| derive maps in numpy | **0.67 s** | 0.62 s of it is PNG defiltering, see below |
| write the set (3 PNGs + `SOURCE.txt`) | 0.11 s | |
| apply to the terrain layer (structural rebuild) | 1.33 s | `shading.set_terrain_texture` |
| EEVEE render, 128 square | 0.5 s | luminance range 0.6131, not flat |
| **TOTAL** | **7.6 s** | gate was 60 s |

Cold server (fresh process, no checkpoint loaded, but the 6.9 GB file in the OS page cache):
**7.3 s total**, generation 6.5 s. A genuinely cold first-ever read adds the disk time for 6.9 GB
and is not measured here; say 10 to 15 s rather than pretending it is free. Peak VRAM for the
whole SDXL job sits under 9 GB of 16, so Blender keeping a viewport is not a problem.

**Where the time is not.** Generation dominates at 66%, and the only stage anyone might have
guessed wrong about, the numpy derivation, is 9%. So G2's map work has real headroom: a proper
AO, normal and cavity pass can cost ten times what the crude one does and the gate still holds.

### Seam, measured five ways on one seed

`comfy_maps.seam_report()` gives mean absolute difference across the wrap versus across the
interior, in 0-255. **A tileable image has ratio near 1.0**, because the wrap is then just another
pair of adjacent columns. Same prompt, same seed 1234, one figure per route:

| Route | seam | interior | ratio | Verdict |
|---|---|---|---|---|
| Nothing (control) | 57.96 | 15.01 | **3.86** | The seam is a visible step edge. |
| `SeamlessTile` on the UNet only, stock VAE decode | 30.39 | 11.27 | **2.70** | **The VAE puts the seam back.** Half the treatment is not most of the benefit. |
| WAS `Image Seamless Texture` offset blend alone | 14.52 | 12.45 | 1.17 | Numerically decent, but see the ghosting below. |
| Circular UNet **plus** WAS offset blend | 9.31 | 9.60 | 0.97 | Redundant; the blend still costs detail. |
| **Circular UNet plus circular VAE (shipped)** | **9.38** | **11.27** | **0.83** | Continuous. |

Across six later generations with different prompts and seeds the shipped route measured 0.88,
0.93, 0.96, 1.01, 1.04, 1.05. So it lands on 1.0 with ordinary variance and no direction.

**The WAS blend's cost is measurable, not just aesthetic.** Its interior figure drops from 11.27
to 9.60, a 15% loss of local contrast, because the blend feathers a wide band rather than a line.
That is the ghosting the plan suspected, quantified: it does not just soften the seam, it softens
a strip of the texture. It is also strictly unnecessary here.

### The defect that changed W1

`CircularVAEDecode`, which is the node the source graph used and the obvious choice, **segfaults
the server**. It `copy.deepcopy`s the live VAE per execution and discards the copy; the next
decode of the session dies inside `comfy/model_management.py load_models_gpu`. Reproduced twice,
each time on the second decode after a restart:

```
Fatal Python error: Segmentation fault
  File "comfy/model_management.py", line 743 in model_load
  File "custom_nodes/ComfyUI-seamless-tiling/SeamlessTile.py", line 106 in decode
```

`MakeCircularVAE` with `copy_vae = "Make a copy"` feeding the stock `VAEDecode` gives a
**byte-identical** result (9.384 / 11.270 / 0.833) and survived three consecutive generations, so
that is what W1 ships. The difference is that its copy is retained in the execution cache instead
of being freed while model management still tracks it. `"Modify in place"` also works but mutates
the session's shared VAE, so a later non-tiled graph would silently inherit circular padding.

This is the kind of thing only a spike finds, and it is why G1 existed.

### Ten things this plan had wrong, corrected here

1. **The recorded UI-to-API conversion rule does not work.** The rule handed to G1 was "each
   node's `inputs` list declares widget inputs with a `widget` key, zip those names against
   `widgets_values`". In a frontend 0.4 GUI export, `inputs` lists only LINK sockets; a plain
   widget appears nowhere in the node. Widget names have to come from `/object_info`, in
   `input_order`, filtered to the widget types, still skipping the extra `control_after_generate`
   entry after a seed. `tools/scripts/comfy_ui_to_api.py` implements the corrected rule and is now
   the reusable step for every later derived graph.
2. **The official text-to-image template is not usable as a derivation source.**
   `01_get_started_text_to_image.json` is now Z-Image-turbo and **subgraphed**: three nodes, one of
   them a UUID type. So the subgraph caveat this document already records is not a corner case, it
   is the shipped default. W1 derives from `ComfyUI-seamless-tiling/tiled_workflow.png` instead,
   which is a real reference graph carrying the exact node set the seamless route needs.
3. **A Sobel-ish height is the wrong primitive.** Gradient magnitude is an edge map, so it raises
   a ridge on BOTH sides of every crevice and the bump reads as embossed rather than carved.
   `height_from()` subtracts a wrap-around low-pass instead, which keeps the sign (dark sinks,
   bright rises) and drops the low frequency so overall albedo brightness cannot tilt the ground.
   Crude still, but crude in a usable direction.
4. **`/features` does not advertise the jobs API.** On this fork it returns
   `supports_preview_metadata`, `supports_model_type_tags`, `max_upload_size`, `node_replacements`,
   `assets`, and nothing about jobs. The jobs API is there and works; it has to be probed with
   `GET /api/jobs?limit=1`, which is what `has_jobs_api()` does.
5. **G0 left the generated pack root unregistered in the normal case.** `_sync_pack_roots()` runs
   at addon registration, which at Blender startup happens against a restricted `_RestrictData`
   that has no `filepath`, so `_output_dir()` raised and the whole sync was skipped: the generated
   pack was invisible until someone pressed Rescan Asset Packs. One `getattr` fixes it. G0 claimed
   this worked "on register"; it worked only when the addon was enabled by hand mid-session.
6. **The Qwen stack on disk is not a text-to-image route.** The plan counted
   `qwen_image_vae` + `qwen_2.5_vl_7b_fp8_scaled` + `qwen-image-edit-2511-Q5_K_M.gguf` as "the
   expensive half already downloaded". The GGUF is Qwen-Image-**Edit**, which needs a reference
   image; Qwen-Image itself is another ~14 GB. See D1.
7. **The seamless-tiling pack is GPL-3.0, not MIT.** Irrelevant to distribution (ComfyUI is
   GPL-3.0 and this extension is already `GPL-3.0-or-later`, and Bob ships no node code), but the
   submodule table said "Low" risk without saying which licence.
8. **PNG decoding is not free, and it is the one place a pure-Python codec bites.** ComfyUI saves
   through PIL, which picks the **Paeth** filter for essentially every row, and Paeth needs the
   reconstructed left neighbour so no numpy shape vectorises it. A numpy-indexed inner loop cost
   1.59 s per 1024 square image; the same loop over `bytes` and a `bytearray` costs 0.62 s. Worth
   knowing before someone assumes numpy is automatically the fast option.
9. **A crude roughness is low contrast, and that is the honest limitation to carry into G2.**
   Desaturate-and-invert into a 0.45-0.95 band gives a real but compressed variation (measured
   117-242 out of 255 on a moss set, mean 206), because a bright albedo maps to the top of the
   band. It reads as "rough everywhere, slightly less so on stone tops". A local-contrast
   normalisation rather than a global band is G2's fix.
10. **Normalising a high-pass by its own percentile amplifies float noise on a flat albedo.**
    Found by the unit test, not by looking: a constant image has zero real detail, so the 99th
    percentile of `|detail|` is rounding noise and dividing by it produced full-range garbage
    instead of mid-grey. The scale is floored at one 8-bit step.

### What G1 deliberately did not build

Named so G2 does not have to rediscover the boundary. No worker thread (the call blocks under the
same wait cursor the heightfield bake uses, at a measured 7.6 s). No preflight. No variants and no
`_staging`. No `contracts.py` change, no MCP op, no reconnect. No AO, normal or metallic map: the
terrain master folds AO into the albedo and carries no normal socket (see
[What G0 shipped](#what-g0-shipped)), so producing them would be work nothing reads, and a wrong
AO is worse than no AO. No ComfyUI URL preference; `BOB_COMFY_URL` covers the non-default case
until G2 builds the service surface.

The panel row shows the last known ComfyUI state rather than probing in `draw()`, on purpose: a
socket call in a draw handler would freeze the UI for the timeout in exactly the case the row
exists to report. Generate updates that state, and with no server it reads "not connected" and
nothing else in the suite behaves differently, which is checked headless.

## What G2 shipped

**Verdict: the vertical slice became a system and got faster doing it.** The blocking call is a
worker thread, the HTTP 400 is a sentence, one Generate is N variants you pick between, and the
crude two derived maps are four real ones. Three graphs ship instead of one.

Files: `core/comfy_jobs.py` (new), `core/comfy.py` (preflight, upload, service status, variants,
Accept, upres), `core/comfy_maps.py` (AO, normal, cavity, local-contrast roughness, wrap pad and
blend), `assets/workflows/tex_tileable_ref.json` (W2, new),
`assets/workflows/tex_upres.json` (W3, new), `ui/shaders.py` (async Generate, staged variants,
Accept / Reject / Upres, thumbnail), `__init__.py` (three preferences, the Advanced-panel ComfyUI
block, five service operators, the `load_post` hook),
`tools/scripts/comfy_preflight.py` (new), `tools/scripts/headless_comfy_g2.py` (new),
`tools/tests/test_comfy.py` (33 tests, up from 13), `tools/tests/data/object_info_min.json` (new).

### Measured, RTX 5080 16 GB, 1024 square, 25 steps

Everything below is `tools/scripts/headless_comfy_g2.py` on Blender 5.2 against a warm server,
over three consecutive full runs; ranges are the spread across those runs, not error bars.

**Ten sets, generated and accepted in one session, no restart:**

| | |
|---|---|
| Per-set wall clock (generate, derive five maps, write, Accept, assign to a terrain layer) | 5.03 to 5.77 s, **mean 5.55 to 5.59 s** |
| First five against last five | 5.62 s against 5.47 s, **drift -0.15 to -0.19 s** |
| Seam ratio across the ten | 0.90 to 1.14, nine of ten inside 0.90 to 1.02 |
| Left in staging afterwards | 0 |

The drift is negative, so there is no leak to find: ten accepted sets cost the same as the first
one. Note this is *faster* than G1's 7.6 s for the same work, because G1's figure included a
1.33 s structural material rebuild from cold and an EEVEE render that this loop does once.

**The UI stays responsive, measured rather than asserted.** A main-thread loop stands in for
Blender's event loop, calling `tick()` the way the timer does and recording the wall clock between
consecutive iterations; any main-thread work the job costs shows up as a long iteration because
there is nothing else in the loop.

| | |
|---|---|
| Main-thread iterations while one generation ran | 5100 to 5600, over 5.9 to 6.4 s of work |
| **Longest main-thread block** | **15.4 to 16.5 ms** (one frame at 60 Hz) |
| Longest single `tick()` | 0.02 to 0.04 ms |
| The same work on the main thread, which is what G1 shipped | **5538 to 5563 ms** |
| Improvement | **338x to 361x shorter worst case** |

The 16 ms is not the tick; it is Python's GIL being held by the worker during a numpy or zlib
stretch, which is exactly the interference a worker thread can still cause and the honest number
to quote. The tick itself is two orders of magnitude below it.

**Preflight catches all five classes**, one test each in `test_comfy.py` and again in the headless
script against the committed `/object_info` dump. The messages, verbatim:

```
unknown node: APackNobodyInstalled at node x (BOB_X) (the pack that provides it is not installed)
cloud node rejected: Tencent3DPartNode at node x (BOB_X) (this integration is local only)
missing model: never_downloaded.safetensors (CheckpointLoaderSimple.ckpt_name); installed: ...
duplicate title: BOB_OUT on nodes 2, x (templating binds by title, so one would be unreachable)
subgraph node rejected: node x (BOB_X) has a UUID type (...); flatten it, title templating
  cannot see inside a subgraph
```

**Roughness, on the same generated image, G1's code against G2's:**

| | Range of 255 | Mean | **Std** |
|---|---|---|---|
| G1, global band | 116 to 242 | 187.8 | 25.8 |
| **G2, local contrast plus percentile stretch** | 89 to 242 | **156.8** | **40.0** |

Standard deviation is the honest measure, not range: G1's range was already wide, but the
distribution was piled against the top of the band. **Contrast is up 55%** and the mean has come
off the ceiling. The band itself widened from 0.45-0.95 to 0.35-0.95, which accounts for the lower
floor; the 55% is the part the new derivation earned.

**Seam through a W3 upres of the same tile**, two independent runs:

| Run | Before (1024²) | After (2048²) |
|---|---|---|
| Isolated | seam 10.68, interior 10.20, **ratio 1.047** | seam 5.52, interior 5.35, **ratio 1.033** |
| In the headless gate | seam 11.46, interior 10.93, **ratio 1.049** | seam 5.93, interior 5.37, **ratio 1.104** |

So the upres is seam-neutral to within the ordinary variance G1 measured (0.83 to 1.05 across six
generations). Both runs took roughly 49 s for a 1024 to 2048 pass. Getting there took two
corrections, below, and the second one is the interesting part.

### Six things this plan had wrong, corrected here

1. **`/object_info` has TWO combo shapes and G1 read only one.** The old shape puts the options
   first (`[[...], {opts}]`, e.g. `LoadImage.image`); the newer one declares the type as the
   literal string `"COMBO"` and hides the options in the options dict
   (`["COMBO", {"options": [...]}]`, e.g. `UpscaleModelLoader.model_name`). G1's `combo_options()`
   returned `[]` for the second, which means the model check that exists to catch a missing
   download would have silently passed a missing upscale model. Found while writing W3, which is
   the first graph that names a non-checkpoint model.
2. **`BOB_DENOISE` is not a node, and neither is `BOB_SIZE` in an img2img graph.** The templated
   title list conflated node names with field names. Templating binds a node by its single
   `_meta.title`, and seed and denoise are two widgets on ONE `KSampler`, so Bob binds
   `{"BOB_SEED": {"seed": s, "denoise": d}}`. Likewise W2 has no `EmptyLatentImage`, so `BOB_SIZE`
   titles the `ImageScale` that resizes the reference. The list is a set of BINDING POINTS, not a
   set of nodes.
3. **Circular padding does not survive `UltimateSDUpscale`, and the fix is Bob-side.** W3 with the
   circular UNet and the circular VAE still came back at **ratio 3.43** from an input measuring
   0.94. Two reasons, both structural: the ESRGAN pass pads at the image border, and the diffusion
   pass runs per tile, so a circular-padded UNet only ever sees a crop. Wrap-padding the tile by
   128 px before sending it and cropping after got it to **2.08** and no further, because a
   non-periodic image has no periodic window: the crop's two edges are two independently denoised
   renders of the same content and they have drifted apart. **Cross-fading those two copies in the
   pad band** finishes the job: **1.03**. This is the offset blend G1 measured and rejected, but
   between two renders of the SAME content rather than between unrelated pixels, so the cost is
   2.2% of interior contrast (5.47 to 5.35) instead of the 15% the WAS blend charged.
4. **`_staging` is not a texture set and must not live under `textures/`.** Confirmed by reading
   `assets.list_texture_sets()`, which unions every `textures/` directory it finds under a pack
   root: a variant staged in there would appear in the picker before anyone accepted it. It is
   `<pack>/_staging/`, a sibling. Accept is `os.rename` plus a per-file stem rename, because
   `texture_set_maps()` resolves `<set>/<set>_<role>.<ext>` and a moved folder whose files kept the
   staging stem would resolve to no maps at all.
5. **A `load_post` handler has to be `@persistent`.** A plain one is itself removed by the file
   load, so the single load it would survive to see is the only one it must not miss. R15 said
   "the job registry clears on `load_post`" without saying this, which is the difference between
   the rule working and looking like it works.
6. **AO is read; the normal map still is not.** G1 skipped both on the grounds that nothing
   consumes them. Half of that was wrong: `core/materials/texset.py`'s role list carries `ao` and
   folds it into the albedo, so a real AO map changes the look immediately. The normal map genuinely
   is unread (neither master carries a normal socket; relief comes from a bump on the height), and
   G2 writes it anyway because it is part of the texture-set contract and track B needs it. So a
   generated set now instances **four** image nodes per layer, not three.

### What G2 deliberately did not build

No websocket progress: 1 Hz job polling carries a progress string fine and G7 owns the upgrade.
(It landed at **G6** instead, and the deferral's own reasoning is what expired: the string polling
carries is the JOB's status, so a 200 s mesh job reported `in_progress` two hundred times.)
No `contracts.py` change, no MCP op, no reconnect; G6 batches all of that. No metallic map (no
shipped set has one and nature surfaces are dielectric). No cavity FILE: the roughness consumes a
cavity signal in memory, and no master reads a cavity map, so writing one would be work nothing
loads. No batch or queue UI beyond one job at a time, because 16 GB makes the server sequential
anyway and a second concurrent job would queue inside ComfyUI and buy only two ways to be
half-finished when the file changes.

**Stop Server only stops a server Bob started.** ComfyUI exposes no shutdown route (`/free`
unloads models but leaves the process and its CUDA context up), so the only honest implementation
is to own the subprocess. A server the artist launched by hand is left alone and the panel says so;
Free VRAM is the always-available path and is what R8's middle layer actually asks for.

### One finding on threading, since G3 asked for it

**The worker thread does not fight Blender's timer model.** The pattern holds exactly as R15
specified: worker does HTTP and numpy, `bpy.app.timers` tick drains a result queue at 4 Hz, every
`bpy` touch happens in the tick, and the `@persistent` `load_post` handler drops the registry so a
result arriving after a file load is discarded as an unknown id. The tick costs 0.04 ms. The one
real interaction is the GIL: the worker's numpy and zlib stretches hold it, which is where the
16 ms worst case comes from, and that is a ceiling that does not grow with job length.

So the recommendation for G3 is **threading, not the blocking call**, and more firmly than for
texture sets. A texture set was 5.6 s, which the blocking path survived under a wait cursor; a
mesh plus paint is 87 to 680 s (G0.5), where a blocked UI is not a wait, it is a hang. Nothing in
the G2 measurements argues for keeping the blocking path anywhere.

## What G3 shipped

**Verdict: the pipeline works end to end, geometry is not the hard part, and the two defaults it
inherited were both wrong in the same direction.** Prompt to a correctly scaled, UV'd,
PBR-textured, BobShader-converted, scatter-instanced prop in 29 to 187 s against a 300 s gate, on
five assets, 0 failures. The steps 3 and 4 A/B came back one-sided and the pinned pipeline above
now records the verdict.

The two inherited defaults are worth stating up front, because between them they would have made
G3 look like it worked while quietly not doing the thing TRELLIS.2 was chosen for. The bundled
geometry graph remeshes to a watertight shell, so every "open surface" asset came back closed
until `remesh` became a value; and a BobShader does not survive a glTF round trip, so an asset
converted before export arrived back as a plain Principled. Neither shows up in a visual check.

Files: `core/gen_assets.py` (new), `core/comfy.py` (mesh transport, the four mesh recipes, the
whole-chain helper), `core/assets.py` (`_norm_entries` defaults, generated-manifest validation),
`ui/scatter.py` (Generate Asset), `__init__.py` (the ComfyUI folder now reaches the client),
five new graphs in `assets/workflows/`, `tools/scripts/comfy_ui_to_api.py` (two conversion rules),
`tools/scripts/headless_comfy_g3.py` (new), `tools/tests/test_comfy.py` (42 tests, up from 33),
`tools/tests/test_assets.py` (14, up from 10), `tools/tests/data/object_info_min.json`
(41 classes, up from 17), and `docs/ARCHITECTURE.md`. Suite **152**, up from 139.

### Measured, RTX 5080 16 GB, `tools/scripts/headless_comfy_g3.py`

**Per asset, prompt to a scattered BobShader.** Generation is W4 plus W5t at the 1024 tier; the
rest is one uninterrupted run of W9c, W9t and the Blender half. All five inside the 300 s gate.

| Asset | W4 | W5t | W9c | W9t | Blender | **Total** | Faces |
|---|---|---|---|---|---|---|---|
| boulder | 112.5 s | 60.6 s | 1.0 s | 9.1 s | 3.8 s | **187.0 s** | 3,433 |
| fern | 6.0 s | 14.1 s | 1.0 s | 41.2 s | 3.2 s | **65.6 s** | 3,382 |
| stump | 5.8 s | 46.7 s | 1.0 s | 9.1 s | 3.9 s | **66.5 s** | 3,885 |
| leaf | 5.5 s | 12.0 s | 1.0 s | 7.0 s | 3.6 s | **29.2 s** | 3,873 |
| cone | 5.5 s | 116.8 s | 1.0 s | 10.2 s | 4.4 s | **137.9 s** | 3,195 |

Boulder's 112 s W4 is the first BiRefNet load of the session, not a per-image cost; every later
subject image took 5.5 to 6 s. W5t at the same 1024 tier ranges 12 to 117 s, an order of magnitude
of spread driven by subject complexity, and it is the single least predictable number here.

**Which stage dominates: generation, harder than G2 measured for rasters.** W4 plus W5t is 31% to
93% of the wall clock (median 79%) and the whole Blender half is **2% to 12%** (median 5.4%), of
which the Cycles bake is 0.8 to 1.7 s. G2's raster split was 66% generation; geometry is the same
shape and more extreme. The practical reading: the Blender pipeline could get five times slower
without an artist noticing, and the only lever worth pulling on speed is the geometry tier. If a
future asset does exceed the budget, the fix is the **512 preview tier**, not a different graph.

**The steps 3 and 4 A/B** is in [The pinned pipeline](#the-pinned-pipeline), with the three
findings behind it. Short version: `Trellis2Simplify` plus `Trellis2UVUnwrap` hit the budget 5 of
5 in 5.95 s for all five meshes; Blender Decimate hit it 1 of 5 in 131.95 s; Quadriflow refused
every mesh outright.

**The black-albedo trap, as a test rather than a note.** Both halves pass:

| | |
|---|---|
| Exported proxy fits the unit cube | longest axis **1.000000** |
| Normalise out, rescale back to `height_m` | 1.800000 m out, 1.800000 m back, error **6.0e-08 m** |
| Generated texture is not near-constant | best image mean 0.194, **std 0.391** against a 0.02 floor |

### The gate, honestly

**Passed, 0 failures on the final run**, and two of the requirements only got there after a
correction that is worth reading rather than skipping.

| Gate item | Result |
|---|---|
| Prompt to a scattered, scaled, UV'd, PBR-textured, BobShaded prop, under 5 min | **29 to 187 s**, five of five |
| Face count within budget | **3,195 to 3,885** against 4,000 (`Trellis2Simplify` is stochastic, so it varies a little per run) |
| A UV layer with no overlap | **0.000000 to 0.000038** across runs, against a 0.01 threshold |
| A non-flat baked normal | std **0.237 to 0.264** |
| bbox height matches `height_m` | exact, five of five |
| Origin at the base | **0.0 m** above the lowest vertex, five of five |
| An LOD chain | three steps, e.g. 3,433 / 1,716 / 514 |
| `materials.master_type()` reports a BobShader | `surface`, five of five, and after instancing |
| At least one foliage asset with genuine open surfaces | **leaf: 11,610 boundary edges, thinnest/longest axis 0.0422** |
| Backface culling | off on every generated material |
| The W4 alpha channel | real cutout: alpha range 0.000 to 1.000, mean 0.175 |
| The black-albedo trap | round trip error **6.0e-08 m**; texture std **0.444** against a 0.02 floor |
| Scatter instances it | **317 instances** on a 20 m grid, still a BobShader |
| The steps 3 and 4 A/B, with a verdict | in [The pinned pipeline](#the-pinned-pipeline) |

**Foliage, in full, because the headline number hides a real limitation.** Three foliage prompts
were tried and they came back as three different things:

| Prompt | Boundary edges (welded) | Thinnest/longest | What it is |
|---|---|---|---|
| "a single flat green leaf ... face-on, flat blade" | **11,610** | **0.0422** | a genuinely thin open blade |
| "a single fern frond, thin flat leaf blade" | 51,842 | 0.8125 | open, but a bushy volume rather than a blade |
| "a broadleaf plant sprig with several thin flat leaves" | 15 | 0.82 | a closed blob |

Whether a prompt returns a thin open surface is the model's decision and W4's framing, not the
pipeline's, so the gate asserts it across the SET and reports each asset. What the pipeline itself
must guarantee is asserted per asset and holds on all three: the importer, the weld, the simplify,
the bake and the BobShader convert all survive non-manifold single-sided input, and whatever
openness a mesh arrived with survives to the finished asset.

**The one thing still missing: the opacity channel does not reach the finished material.**
`Trellis2TextureMesh` emits opacity, but the basecolor that arrives in the GLB is opaque, so
nothing is wired into the Principled Alpha and the check reports "no". For a single-sided leaf that
is cosmetically survivable, because backface culling is off and it renders from both sides, and
materially wrong for anything that needs a real cutout. Carried into G3b, which is where the
one-shot `geometry_texture` graph exercises the opacity output properly.

> **Answered at G3b, and half of this paragraph was wrong.** The basecolor arriving in the GLB is
> NOT opaque: it is an RGBA PNG carrying TRELLIS.2's opacity in its alpha, on 20 of 20 GLBs
> measured. What made it look absent is a declaration and a bake, not missing data. The pack sets
> `alphaMode: "OPAQUE"`, which per the glTF spec tells the importer to ignore that alpha, and
> `bake_high_to_low` then wrote an alpha-less basecolor over it. The channel is now wired behind a
> plausibility rule, and on this same leaf the G3 gate reports it wired. See
> [What G3b measured](#what-g3b-measured).

### Thirteen things this plan had wrong, corrected here

1. **The open-surface capability is switched off by the graph the plan told G3 to derive from.**
   `Trellis2ProcessMesh` with `remesh: on`, which is what every bundled `geometry_only_*` graph
   ships, runs a dual-contouring remesh and returns a WATERTIGHT shell. The same leaf measures 0
   boundary edges with it on and **11,620** with it off, at an identical 0.04 thinnest/longest axis
   ratio. So R21's decisive argument for TRELLIS.2 is sound about the model and was being defeated
   by a default. `remesh` is now a binding point on W5t and Generate Asset picks it from the
   scatter kind: `plants` and `grass` get it off, everything else gets it on.
2. **The BobShader does not survive the glTF round trip, so converting before export is not
   enough.** glTF carries PBR and not Blender node groups, so a finished asset re-imported by
   `import_generated` came back as a plain Principled and `master_type()` returned None even
   though the export had been converted. Caught by the scatter check, not by inspection. The
   convert belongs on import anyway, since `bobshade_material` routes an asset's OWN maps through
   `S_SurfaceMaster` and those maps only exist once the bake has run.
3. **The UI-to-API conversion rule was still incomplete after G1 fixed it once**, and the TRELLIS.2
   nodes break both remaining assumptions. First, `control_after_generate` is frequently NOT
   declared in the schema: the frontend adds the control widget to any INT named `seed`, and none
   of the TRELLIS.2 samplers declares it, so every widget after a seed shifted by one and
   `Trellis2ImageToShape` converted with `ss_guidance_strength: "randomize"`. Second,
   `COMFY_DYNAMICCOMBO_V3` (`Trellis2ProcessMesh.remesh`, `GeomPackUVUnwrap.backend`) is a key
   widget followed inline by the inputs of the branch that key selects, named `<field>.<sub>`; the
   flat dotted entries `/object_info` lists at the end of `input_order` are the UNION of every
   branch, so reading them in that order is wrong twice over. Both rules are in
   `comfy_ui_to_api.py` with a test each.
4. **A mesh graph reports nothing.** `Trellis2ExportTrimesh` is the only exporter that converts the
   pack's internal Z-up to glTF's Y-up and flips the UV V, so it is the one Bob's graphs must end
   with, but it is a V3 node returning a plain STRING and ComfyUI records a node's `ui` dict as its
   outputs. Measured: the job completes with `outputs: {}` and `outputs_count: 0`. **`Preview3D`
   is the fix and it is plumbing, not a viewer**: it takes that path string and emits a real
   `{filename, type, subfolder}` entry. `/view` then serves the GLB happily, once the client
   basenames the filename, because the route rejects a leading slash outright.
5. **`GeomPackSaveMesh` is the wrong exporter even though the bundled graph uses it.** It writes
   through trimesh with no axis conversion and no UV V-flip, so its GLB arrives in Blender rotated
   and with the texture upside down. It is also the reason to prefer `Trellis2LoadMesh` over
   `GeomPackLoadMeshPath` on the way in: one free-form STRING input, no second pack in the path.
6. **`geometry_only_1536` is `1536_cascade`.** `LoadTrellis2Models.resolution` offers exactly
   `512`, `1024`, `1024_cascade`, `1536_cascade`; there is no plain 1536. G0.5's "no cascade is
   needed on this card" is right about 1024 and cannot be true about the hero tier, because at
   1536 the cascade IS the tier.
7. **`mesh_audit.json` is not a usable derivation source.** It is built on `PulseMeshAudit`, from a
   pack that is not installed and is not in `ComfyUI-TRELLIS2`'s declared `node_reqs`. W9c derives
   from `refinement` alone.
8. **W4's alpha has a sign trap, and it is the same class of bug as the black albedo.**
   `Trellis2RemoveBackground` returns a FOREGROUND mask (white = object) and `JoinImageWithAlpha`
   computes `alpha = 1 - mask`, following ComfyUI's `LoadImage` convention that a mask is the
   inverse of alpha. Wiring the two together directly saves the background and cuts the subject
   out, silently. An `InvertMask` between them is load-bearing, and there is a test asserting the
   wiring rather than the output.
9. **Boundary-edge counts on an imported glTF are meaningless before welding.** glTF stores UVs and
   normals per vertex, so the importer splits a vertex at every UV seam and every sharp edge:
   the boulder measured 233,812 boundary edges of 489,781 faces on import and 19,623 after a
   merge-by-distance. **G0.5's leaf figure of 83,292 of 334,659 was very likely inflated the same
   way** and should be treated as an upper bound, not a measurement.
10. **A `Trellis2ProcessMesh` result is full of pinholes**, and they are not an aesthetic detail:
   19,623 boundary edges on a boulder that should be closed, and every boundary loop is a
   constraint Decimate will not collapse across. Filling only SMALL loops
   (`bpy.ops.mesh.fill_holes(sides=256)`) closes them while leaving a leaf's silhouette, which is
   one loop of tens of thousands of edges, untouched.
11. **A bake with no source textures writes a black basecolor**, which is worse than no map at all,
   because the material then reads a real texture that says the object is black. A geometry-only
   generation must skip the colour transfer and bake only normal and AO.
12. **`obj.bound_box` is cached until a depsgraph evaluation.** Measured right after
    `mesh.transform()` it still describes the old mesh, which made a working origin-to-base look
    broken. Every measurement here reads the vertices instead.
13. **The obvious cheap UV-overlap measure measures adjacency.** Collecting each face's corner
    cells and counting cells more than one face touches reports 0.26 on a layout Smart UV Project
    has just made clean, because two neighbouring faces share their corners by definition. The
    honest version rasterises the triangles and compares summed coverage with the union.

### What G3 deliberately did not build

No `contracts.py` change, no MCP op, no reconnect; G6 still batches all of that. No variant picker
for meshes: a texture set is 5 s and worth generating four of, a mesh is 60 to 190 s and the
staging folder holds one result that Generate Asset consumes immediately. No hero retopology tier,
because finding 2 above means there is not one locally; `hero=True` raises the bake to 2K and the
texture to 2048 and says in its report that it decimated. No thumbnail or turntable preview, and
no `mesh_geom_texture` (W9b) comparison, which is G3b.

## What G3b measured

**Verdict: the one-shot graph wins and is the new shipped default, and the reason it wins is not
the one the plan expected.** W9b was supposed to be a VRAM-versus-convenience trade that gave up an
intermediate simplify pass. It gives up no simplify pass at all, it fits 16 GB with 61% of the card
free, and it beats the staged route on the two things that actually decide an asset: the mesh comes
back five to a hundred times cleaner, and it cannot hit the black-albedo trap, which the staged
route hit on 1 of 10 prompts.

Files: `assets/workflows/mesh_geom_texture.json` (W9b, new), `core/comfy.py` (`bind_process`,
`mesh_geom_texture`, `generate_asset_oneshot`, `asset_chain`, `finish_passes`, the route default),
`core/gen_assets.py` (`uv_counts` / `uv_coverage`, `basecolor_image`, `source_opacity`,
`cutout_render_method`, the alpha in `bake_high_to_low`, the opacity in `finish_asset` and
`import_generated`), `ui/scatter.py` (the route is a value), `tools/tests/test_comfy.py` (45 tests,
up from 42), `tools/tests/data/object_info_min.json` (42 classes, up from 41),
`tools/scripts/headless_comfy_g3b.py` (new). Suite **155**, up from 152.

### The benchmark, RTX 5080 16 GB, `tools/scripts/headless_comfy_g3b.py`

Ten prompts, four of them foliage, each generated ONCE through W4 and then handed to both routes,
so the reference framing is controlled for and the only variable is the graph. `remesh` is off for
foliage and on for the rest **on both routes**, so the open-surface column is not confounded with
it. Albedo and opacity figures are Blender's scene-linear floats measured INSIDE the UV charts
(see correction 4).

| Prompt | Route | Wall s | Peak MiB | Rise MiB | Faces | Boundary | UV overlap | Chart cover | Albedo std | Alpha mean |
|---|---|---|---|---|---|---|---|---|---|---|
| boulder | staged | 46.7 | 8100 | 2650 | 3,964 | 2,356 | 0.00000 | 0.716 | 0.2269 | 0.576 |
| boulder | **one-shot** | 63.5 | **5080** | 3542 | 3,910 | **114** | 0.00004 | 0.574 | 0.1559 | **0.998** |
| fern | staged | 23.1 | 8160 | 2504 | 3,439 | 3,034 | 0.00002 | 0.559 | 0.2227 | 0.311 |
| fern | **one-shot** | 22.1 | **4396** | 2806 | 3,750 | **1,033** | 0.00001 | 0.563 | 0.1917 | **0.998** |
| stump | staged | 57.7 | 8200 | 2650 | 3,389 | 2,563 | 0.00008 | 0.633 | 0.1641 | 0.395 |
| stump | **one-shot** | 83.5 | **5240** | 3594 | 3,897 | **146** | 0.00010 | 0.632 | 0.1322 | **0.998** |
| leaf | staged | 20.0 | 8272 | 2504 | 3,849 | 1,249 | 0.00000 | 0.726 | 0.1051 | 0.068 |
| leaf | **one-shot** | 15.1 | **4362** | 2666 | 3,939 | **965** | 0.00000 | 0.744 | 0.1859 | **0.999** |
| cone | staged | 116.2 | 8304 | 2824 | 3,966 | 3,050 | 0.00000 | 0.642 | 0.1574 | 0.334 |
| cone | **one-shot** | 185.8 | **6276** | 4508 | 3,869 | **662** | 0.00000 | 0.634 | 0.1701 | **0.998** |
| grass | staged | 49.4 | 8416 | 2504 | 3,723 | 1,515 | 0.00000 | 0.646 | 0.2382 | 0.505 |
| grass | **one-shot** | 14.0 | **4512** | 2666 | 3,849 | **710** | 0.00000 | 0.661 | 0.1779 | **0.998** |
| ivy | staged | 96.1 | 8456 | 2508 | 3,983 | 1,359 | 0.00000 | 0.748 | **0.0000** | 0.000 |
| ivy | **one-shot** | 13.0 | **4544** | 2666 | 3,874 | **511** | 0.00001 | 0.702 | 0.2234 | **0.999** |
| log | staged | 39.1 | 8484 | 2546 | 3,779 | 2,253 | 0.00000 | 0.649 | 0.2946 | 0.526 |
| log | **one-shot** | 29.1 | **4752** | 2840 | 3,934 | **45** | 0.00000 | 0.594 | 0.2074 | **0.998** |
| mushroom | staged | 45.5 | 8524 | 2568 | 3,810 | 1,802 | 0.00000 | 0.656 | 0.2523 | 0.488 |
| mushroom | **one-shot** | 55.6 | **5508** | 3542 | 3,716 | **45** | 0.00000 | 0.592 | 0.2181 | **0.998** |
| flint | staged | 90.3 | 8586 | 2714 | 3,957 | 1,467 | 0.00000 | 0.621 | 0.2278 | 0.770 |
| flint | **one-shot** | 111.3 | **5756** | 3714 | 3,818 | **10** | 0.00000 | 0.579 | 0.0780 | **0.998** |

Summed: **staged 584.1 s, one-shot 593.1 s** for all ten, medians 49.4 s and 55.6 s. Per prompt the
spread swamps the difference and runs both ways (ivy 96.1 s against 13.0 s, cone 116.2 s against
185.8 s), so **wall clock does not choose between them** and neither is the reason to prefer either.

**Does W9b fit 16 GB: yes, easily.** Peak **6,276 MiB** summed across the ComfyUI processes
(7,726 MiB whole card) against 16,303 MiB, so the worst of ten prompts left 61% of the card free.
Two things about that number:

- **VRAM has to be read per process and summed over the ComfyUI FAMILY.** comfy-env runs each
  isolated pack in its own process, so the main server, the TRELLIS2 pixi worker and the
  GeometryPack pixi worker each hold their own allocation. G0.5's whole-card figures were
  doubly misleading: they included another resident ComfyUI, and they could not attribute anything.
- **The absolute peak is order-dependent and the rise is the honest per-graph figure.** W4 leaves
  SDXL resident, so whichever mesh graph runs straight after it is measured on top of roughly 6.6 GB
  that is not its own. That is why the staged route reaches a HIGHER absolute peak (8,586 MiB) while
  showing a SMALLER rise: its rise is a lower bound, because W5t never had to allocate above the
  residue it started on top of. The one-shot graph is the biggest single allocation of either route
  (up to **4,508 MiB** above its own baseline) and still the lower absolute peak.

### Open surfaces, with `remesh` controlled for

Both routes got the identical `remesh` value, so this is a comparison of graphs and not of settings.
Boundary edges are counted after a `weld()`, per G3's correction 9.

| Prompt | remesh | Staged boundary | One-shot boundary | Staged thin | One-shot thin |
|---|---|---|---|---|---|
| boulder | on | 2,356 | 114 | 0.6403 | 0.6393 |
| fern | off | 3,034 | 1,033 | 0.8071 | 0.8001 |
| stump | on | 2,563 | 146 | 0.8022 | 0.7903 |
| leaf | off | 1,249 | 965 | 0.0388 | 0.0414 |
| cone | on | 3,050 | 662 | 0.7979 | 0.7991 |
| grass | off | 1,515 | 710 | 0.1665 | 0.1648 |
| ivy | off | 1,359 | 511 | 0.1249 | 0.1315 |
| log | on | 2,253 | 45 | 0.3117 | 0.3139 |
| mushroom | on | 1,802 | 45 | 0.6283 | 0.6282 |
| flint | on | 1,467 | 10 | 0.9598 | 0.9577 |

**Openness is a property of `remesh`, and pinholes are a property of the route.** Every foliage
prompt came back open on both routes (4 of 4 each, at 500+ boundary edges), and the thinnest/longest
axis ratios agree to within 1.5% on all ten, which is what makes the comparison fair. What differs
is the count on the solids that are SUPPOSED to be closed: the staged route leaves 1,467 to 3,050
boundary edges on a rock or a log, and the one-shot route leaves 10 to 146. The staged figure is
the sieve G3's correction 10 described, and it comes from `Trellis2Simplify` in W9c; W9b's
`Trellis2ProcessMesh` simplifies, welds and unwraps in one node and does not shred the surface
doing it. Same statement for foliage: the one-shot route keeps the silhouette loop and drops the
pinholes, which is exactly what `close_pinholes(sides=256)` exists to approximate Bob-side.

### The finished asset, and the dense mesh that turned out not to matter

Four prompts through steps 6 to 8 on both routes. `bake source` is the face count of the high mesh
the bake read; `detail` is the mean absolute neighbour difference in the baked normal map, which is
high frequency by construction, because std alone cannot tell transferred detail from a shading
difference.

| Prompt | Route | Faces | Bake source | Normal std | Normal detail | master_type | Blender s |
|---|---|---|---|---|---|---|---|
| boulder | one-shot | 3,922 | 3,910 | 0.2494 | 0.00954 | surface | 2.1 |
| boulder | staged | 3,972 | **489,570** | 0.2543 | 0.00788 | surface | 4.0 |
| fern | one-shot | 3,768 | 3,750 | 0.2656 | 0.02076 | surface | 2.0 |
| fern | staged | 3,449 | **442,619** | 0.2390 | 0.00153 | surface | 3.4 |
| leaf | one-shot | 3,939 | 3,939 | 0.2405 | 0.00321 | surface | 2.1 |
| leaf | staged | 3,849 | **487,854** | 0.2681 | 0.00261 | surface | 3.6 |
| stump | one-shot | 3,974 | 3,897 | 0.2622 | 0.02178 | surface | 2.3 |
| stump | staged | 3,399 | **470,679** | 0.2635 | 0.01386 | surface | 4.0 |

**The dense mesh bought nothing measurable.** Baking a 490,000-face high onto the low produced a
normal map indistinguishable in std (0.2390 to 0.2681 against the one-shot route's 0.2405 to 0.2656)
and LOWER in high-frequency content on 4 of 4. The reason is not that the bake failed: it is that
`Trellis2Simplify` at a 4,000-face budget already tracks the 490,000-face surface closely enough
that the residual is nearly nothing to encode. The one-shot route's slightly higher detail figure is
its own weld boundary, not transferred geometry, so the honest reading is "no difference either way"
rather than "the one-shot route wins here too". Either way, **the plan's assumption that the
intermediate dense mesh is what pays for the detail normal is not supported at this budget.** It
would be worth re-testing if the face budget ever drops to a few hundred.

**Blender's own simplify is still not an option**, re-measured on these meshes rather than carried
over from G3's five: 493,779 faces in, **6,757** out on the boulder; 442,619 in, **24,327** out on
the fern; 471,678 in, **12,119** out on the stump; 487,854 in, 3,998 out on the leaf. One of four
inside a 4,000 budget, which matches G3's one of five. So the third route (keep the dense mesh and
let Blender do steps 3 and 4) stays dead.

### The opacity channel, answered

**It is present in both routes and it was never absent. What G3 saw was a flag, not the data.**
`Trellis2RasterizePBR` writes TRELLIS.2's opacity output (voxel attribute channel 5) into the ALPHA
of the base-colour texture it bakes, so every one of the twenty GLBs measured here carries an RGBA
basecolor. It then declares `alphaMode: "OPAQUE"`, which per the glTF spec instructs every importer
to ignore that alpha, and Bob's own bake wrote an alpha-less basecolor over the top. Two independent
reasons for the same symptom.

What the channel actually says depends on the route, and only one answer is usable:

| Route | In-chart alpha mean | Below 0.98 | Verdict |
|---|---|---|---|
| **one-shot (W9b)** | 0.9976 to 0.9990, all ten | **0.00%** | A real signal that says the surface is solid. |
| staged (W9t) | 0.068 to 0.770, nine of ten | 42% to 93% | Not a cutout. Wiring it makes a tree stump 60% transparent and a leaf 93% transparent. |
| staged (W9t), the G3 leaf | **0.9806** | **1.81%** | A genuine soft-edged cutout, and it wires. |

So the wiring shipped with a plausibility rule that the measurement forced rather than the obvious
non-constant test: `gen_assets.source_opacity` wires the channel only when at least 0.5% of the
in-chart texels are below 0.98 AND the in-chart mean is still 0.9 or above, i.e. the surface is
mostly there with a minority genuinely cut away. It reports `cutout`, `opaque` or `implausible`
either way, so a report can say which rather than only whether.

**Wired, and here is the alpha range on a generated leaf.** The G3 cached leaf reads in-chart
min 0.000, **mean 0.9806**, max 1.000, with **1.81%** of the blade below 0.98 at 0.744 chart
coverage: a soft one-to-two-texel silhouette band, which is what a leaf's edge should be. It reads
`cutout`, it wires, and `headless_comfy_g3.py` now reports "alpha channel wired into the Principled
-- yes" on it with 0 failures, which is the G3 partial closed on a real asset.

Nothing in the G3b ten wired, and that is the correct outcome for those twenty textures rather than
a gap: the one-shot channel says `opaque` and the staged channel says `implausible`. So the path was
also **forced once on the generated fern** to prove it end to end rather than by assertion: the
alpha
lands in the baked basecolor's fourth channel, the exported GLB stops declaring `OPAQUE` and
declares
`BLEND`, and the re-import a scatter layer makes comes back with the Alpha socket linked to an image
node and the render method `DITHERED`.

The remaining honest limitation: **the staged route's opacity channel is unreliable per generation,
not uniformly broken.** Four of the five G3 assets and nine of the ten G3b assets read `implausible`
(the G3 stump reads mean 0.1335 with 87% below the floor); one leaf reads as a clean cutout. The
one-shot route is consistent across all ten, and consistently says the same thing: for a
TRELLIS.2 asset the cutout is the GEOMETRY, not the texture, which is the whole point of open
surfaces, so an opaque channel on a leaf is the right answer rather than a missing feature.

### Five things this plan had wrong, corrected here

1. **`geometry_texture` does not end with `GeomPackSaveMesh`.** The handover into G3b said it did,
   and the exporter swap that fact implies is not needed: the bundled graph already ends with
   `Trellis2ExportTrimesh` feeding a `Preview3D`, and `GeomPackSaveMesh` does not appear in it at
   all. Read across the pack's nine bundled graphs: `standalone_texturing` (W9t's source) carries
   BOTH exporters, which is where the swap was really needed; every `geometry_only_*` carries
   NEITHER and only a VTK viewer, which is why W5t had to add the pair; and `geometry_texture` is
   the one graph that already had it right.
2. **The one-shot route loses no simplify pass, so the question the phase was set to answer was
   the wrong question.** `Trellis2ProcessMesh` is simplify plus floater removal plus weld plus UV
   unwrap in a single node: its widgets are `target_face_count`, `floater_threshold`,
   `weld_vertices`, `weld_digits` and the four `chart_*` parameters, which is exactly what W9c does
   with `Trellis2Simplify` plus `Trellis2UVUnwrap`. Binding `BOB_PROCESS.target_face_count` to the
   face budget makes W9b return finished topology, already unwrapped, with the PBR rasterised into
   those charts and projected through the pre-simplify shape via `original_mesh`. What W9b gives up
   is not a simplify pass, it is the DENSE mesh, and correction 3 is what that turned out to cost.
3. **The staged route can return a fully black texture, and it did.** The ivy prompt came back with
   an albedo of std **0.0000** and every channel range 0.00 out of W9t, on a mesh W5t had generated
   correctly and W9c had simplified correctly. This is the black-albedo trap from G0.5 arriving in
   production, and the mesh was already unit-normalised, so the G3 assertion that catches it would
   have fired on a finished asset rather than prevented it. The one-shot route cannot hit it
   structurally: it never re-encodes a mesh through `Trellis2EncodeMesh`, it reuses the shape latent
   the generation already produced. One total failure in ten on the primary route is the single
   strongest argument in this phase, and it is an argument about structure rather than about a
   sample of ten.
4. **A whole-image texture statistic measures the UV packing, not the surface.**
   `Trellis2RasterizePBR` inpaints its PBR channels only one to three pixels past a chart edge, and
   chart coverage across these assets ran **0.130 to 0.856**, so on the worst layout 87% of every
   map
   is untouched black. G3's texture-std figures are whole-image and therefore mostly a statement
   about how well the charts packed. `gen_assets.uv_coverage()` rasterises the UV triangles into a
   mask so every figure above is in-chart; the same mask is what makes an opacity channel safe to
   read, and forcing the off-chart texels opaque is what stops a wired alpha ringing every island
   with a transparent rim.
5. **`Trellis2ShapeToTexturedMesh` was missing from the plan's node table**, and it is the node the
   one-shot route is built on: it takes the shape latent and the subs from
   `Trellis2ImageToShape` plus the same conditioning and returns the PBR voxel grid, which is what
   lets W9b texture without an encode step. The table listed `Trellis2ShapeToTexturedMesh` as
   "geometry plus PBR in one" without saying that it is a texture stage taking a shape latent, not a
   combined generator.

### What G3b deliberately did not build

No route picker in the panel. The route is a value in one place (`comfy.DEFAULT_ASSET_ROUTE`, read
through `comfy.asset_chain()`), and a second radio button on the Generate Asset box would be knob
sprawl for a decision that has a measured answer. No `hero` measurement on the one-shot route: it
raises the tier to `1536_cascade` and the texture to 2048, and neither was measured here, so it
keeps whatever the staged route's Hero already did and is honestly untested at that tier. No
`contracts.py` change, no MCP op, no reconnect; G6 still batches all of that.

## What G4 measured

**Verdict, in one sentence each.** The stylise route works and the silhouette holds (IoU 0.998).
**Blender's real depth and normal passes buy nothing measurable over Depth Anything V2 plus
NormalBAE on the same frame, and their honest justification is 2.5 s per frame, not quality.** The
paint route works, and its cross-view drift is now a number rather than an impression: adjacent views
disagree by 22 to 27 of 255 over their shared texels and the front disagrees with the back by 30.
Multi-view geometry is not a G7 curiosity, it is the fix for the thing G3 called out: a single view
scores **0.044** back-half IoU on an object whose back it cannot see, and four views score **0.264**
(TRELLIS.2) or **0.214** (Hunyuan).

Files: `assets/workflows/stylize_render.json` (W12, new), `stylize_render_est.json` (W12e, new),
`mesh_paint_views.json` (W9, new), `mesh_geom_mv.json` (W6, new), `mesh_geom_mv_trellis.json`
(W6t, new), `core/gen_views.py` (new), `core/gen_paint.py` (new), `core/comfy.py` (`drop_node`,
`stylize_render`, `paint_views`, `mesh_geom_mv`, `mesh_geom_mv_trellis`, the texture route, and the
empty-`/free` fix), `__init__.py` (`BBT_StyliseProps`, `Stylise Last Render` in the Advanced panel),
`tools/tests/test_comfy.py` (53 tests, up from 45), `tools/tests/data/object_info_min.json`
(50 classes, up from 42), `tools/scripts/headless_comfy_g4.py` (new). Suite **162**, up from 155.

### How the passes come out of Blender, which was the part with no shortcut

Not the compositor. Blender 5.2 replaced `Scene.node_tree` with a compositing NODE GROUP, and its
`CompositorNodeOutputFile` writes a single multilayer EXR whatever the per-item format says, which
`bpy.data.images.load` then refuses to open (`size (0, 0)`, `file_format TARGA`). Three separate
File Output nodes did not help; nor did a per-item format override.

What works is a **view-layer material override**: one emission material per pass, rendered as the
beauty image through the ordinary render path. `ShaderNodeCameraData`'s View Z Depth into a
`ShaderNodeMapRange` is the depth; `ShaderNodeVectorTransform` (WORLD to CAMERA) into an encode is
the normal. It is engine-native, exact, works on an arbitrary scene, costs ONE sample because the
result is constant per pixel, and writes an ordinary PNG. EEVEE and Cycles agree to the byte
(depth 0.6455 either way on the probe scene).

Measured on the shipped path: **0.9 to 1.3 s for a beauty frame plus both passes at 1024 square**
(EEVEE, 48 samples), which is why the operator re-renders rather than trying to reuse a Render
Result it cannot read the passes out of.

### Track D: real passes against estimated ones, one table, one verdict

Same scene, same seed, same prompt, same ControlNet strengths; the only variable is where the depth
and normal hints come from. `changed` is the mean absolute difference from the source render INSIDE
the geometry (a frame with a sky in it is half untouched background, and a whole-image mean would
report the sky's stability as the restyle's timidity). `depth r` and `depth MAE` are Depth Anything
V2's reading of the STYLISED frame against Blender's true depth, after the affine alignment a
scale-free estimator requires.

| Route | Denoise | Wall s | Peak MiB | Silhouette IoU | Edge IoU | Depth r | Depth MAE | Changed |
|---|---|---|---|---|---|---|---|---|
| **W12, real passes** | 0.55 | **7.7** | 14,066 | **0.9980** | 0.1083 | 0.7869 | 0.1219 | 11.5 |
| W12e, estimated | 0.55 | 10.3 | 14,066 | 0.9967 | **0.1254** | **0.7881** | **0.1224** | 10.6 |
| **W12, real passes** | 0.75 | **8.4** | 14,162 | **0.9981** | **0.1150** | 0.7489 | 0.1307 | 47.5 |
| W12e, estimated | 0.75 | 10.8 | 14,098 | 0.9957 | 0.1055 | **0.7664** | **0.1278** | 43.6 |

Reference points the table needs to be readable. Depth Anything V2 against Blender's true depth on
the **source** frame measures r **0.7957** and MAE **0.1224**, so the estimator's own error is
already the size of every difference in the table. The edge IoU of that same source pair is
**0.1577**, which is the ceiling for the edge column, so 0.105 to 0.125 is 67% to 79% of what is
achievable rather than a failure.

**The verdict, stated plainly because the phase asked for it: the real passes are NOT worth keeping
for quality.** Silhouette IoU improves by 0.001 to 0.002, which is noise at this scale; the depth
correlation is a hair WORSE with the real passes at both denoise levels; the edge IoU goes one way at
0.55 and the other at 0.75. On this scene, at these settings, an artist could not tell the two apart.

**They are worth keeping for two other reasons, and the export code is cheap.** W12 is consistently
**2.5 s faster per frame** (7.7 to 8.4 s against 10.3 to 10.8 s), because it does not run two
estimator networks per generation, and that difference grows with resolution rather than shrinking.
And `core/gen_views.py` is not maintained for track D's sake anyway: W9's per-view restyle needs the
same three files, and the paint route's projection needs the camera metadata that comes with them.
If W9 were deleted tomorrow, deleting the real-pass route with it would cost 2.5 s a frame and
nothing else.

**VRAM is the honest surprise here.** Measured cleanly (a `/free` and six seconds before each run,
per-process and summed over the ComfyUI family): the stylise route peaks at **14,194 MiB** with the
real passes and **14,226 MiB** with the estimators, on a card that has 16,303. SDXL plus TWO
ControlNets at 1024 square is the heaviest raster job in this integration by a wide margin -- G1's
texture set peaked under 9 GB -- and it is the first route where reserving VRAM for Blender actually
matters. The estimator weights are not what costs it: both routes land within 32 MiB of each other.

### Track B stylised: the turntable, and the numbers the gate asked for

Eight views of a G3b-generated boulder (3,910 faces, 0.574 chart coverage): six in a ring at 20
degrees plus a top and an underside view. Each goes through W9 at denoise 0.40 under both
ControlNets, with the front view's stylised result as the IPAdapter reference for every later view.
**8 views in 50.9 to 52.9 s** including the ControlNet loads, plus **6.4 s** for all eight renders
with their depth and normal passes.

| Measurement | Value |
|---|---|
| Chart texels painted directly by a view | **92.6%** (29 texels left for the hole fill) |
| Adjacent-view overlap, shared texels | 52,180 to 79,682 per pair |
| **Adjacent-view seam, mean absolute difference** | **22.3 to 26.5 of 255, mean 24.1** |
| **Front against 180 degrees (drift)** | **30.1 of 255 over 43,587 shared texels** |
| LoRA effect at paint settings (denoise 0.40) | 1.7 of 255 |
| LoRA effect at denoise 0.75, strength 1.0 | 7.1 of 255 |

**Read the drift figure against the seam figure, not against zero.** Two neighbouring views that
have seen the same texels from 30 degrees apart disagree by 24 of 255; the front and the back
disagree by 30. So per-view SDXL img2img plus an IPAdapter reference does hold a palette (the drift
is 1.25x the adjacent-view figure, not 5x), and it does not hold a texture: 24 of 255 across an
overlap is a soft banding that the normal-weighted blend averages rather than removes. That is R20's
consistency loss, quantified, and it is the honest limit of this route without a multi-view-consistent
adapter.

**Two things about the turntable turned out to be load-bearing rather than cosmetic**, and both are
in `gen_views.turntable_views` as defaults:

- **The ring alone is not enough coverage.** Six views at one elevation painted 71.5% of the chart
  texels and left 48,396 to the hole fill. Adding one top and one underside view took it to
  **92.6%** and 29.
- **The views have to be isolated and flat-lit.** A paint view is of the MESH, not of the scene it
  sits in, so every other object is hidden for the render; and the pixels become an albedo map, so
  the world is swapped for a uniform white environment. This is W1's flat-lighting argument (family
  1) arriving in track B: a colour map with a sun's terminator baked into it is lit twice.

**LoRA style control is real and weak with what is on disk.** At paint settings a LoRA moves the
front view by 1.7 of 255, and at denoise 0.75 with strength 1.0 by 7.1: the mechanism is wired (there
is a test for the graph edit, and a gate check for the effect), but every LoRA in
`models/loras/` is an anime or Illustrious finetune sitting on top of a photoreal RealVisXL base, and
the paint route deliberately keeps denoise low so the real render dominates. So the claim "this is
the only route with real style control" (R19/R20) survives as an architectural statement and not yet
as a demonstrated look. A material or painterly SDXL LoRA is a download away, and the route takes it
as a value.

### Multi-view geometry: the back-facing test, with the error quantified

A ground-truth object built for the purpose: a plain block from the front, with a deep hemispherical
cavity and a tall fin on the BACK. Four cardinal views at 10 degrees elevation drive W6t and W6;
W5t gets the front view only. Every candidate is unit-normalised, point-sampled and scored against
the ground truth by surface-voxel IoU and Chamfer distance, best over the four quarter turns about
the up axis, whole and on the back half alone.

| Route | Wall s | Peak MiB | Faces | Turns | IoU whole | IoU back | Chamfer | Chamfer back |
|---|---|---|---|---|---|---|---|---|
| W5t, single view | 47.7 | 8,086 | 464,601 | 1 | 0.0611 | **0.0439** | 0.0623 | 0.0755 |
| **W6t, `Trellis2MultiViewImageToShape`** | 120.4 | 7,286 | 486,874 | 2 | **0.2489** | **0.2637** | **0.0322** | **0.0283** |
| W6, Hunyuan multi-view | **24.4** | 9,618 | 288,016 | 0 | 0.1549 | 0.2140 | 0.0368 | 0.0344 |

The ceiling, so those numbers can be read: the ground truth against a second point sample of ITSELF
scores IoU **0.6979** whole and **0.7110** back, at Chamfer 0.0138. A surface-voxel IoU is strict, so
0.264 is 37% of achievable and 0.044 is 6%.

**Three findings, in the order they change the plan.**

1. **Multi-view is the fix for the thing G3 found, and it belongs in track C rather than in the G7
   A/B slot.** G3 reported that W4's framing, not the geometry model, decides whether a foliage
   prompt returns a thin blade or a bushy volume, and this is the same effect from the other side: a
   single view is not weak evidence about the back, it is NO evidence, and the model fills it in with
   a plausible mirror of the front (back-half IoU 0.044, worse than its own whole-mesh 0.061). Four
   views raise the back half **six-fold** and halve the Chamfer error. The plan's multi-view entry
   is moved accordingly: see the pinned pipeline's step 1.
2. **`Trellis2MultiViewImageToShape` wins on accuracy and Hunyuan wins on speed, decisively both
   ways.** TRELLIS.2 is 23% better on back-half IoU and 18% better on back-half Chamfer; Hunyuan is
   **5x faster** (24.4 s against 120.4 s) and returns 41% fewer faces. That is a real choice rather
   than a winner: TRELLIS.2 stays primary, and Hunyuan multi-view earns a place as the preview tier
   for the block-out control path G4c is about, where a 24 s answer that keeps the footprint is worth
   more than a 120 s answer that keeps the detail.
3. **The two exporters do not agree on which way the subject faces.** The alignment search chose 1
   or 2 quarter turns about the up axis for the TRELLIS.2 meshes and **0** for Hunyuan's `SaveGLB`.
   G4c has to know that: an Omni-conditioned asset is only "dropped into a layout already composed"
   if it comes back facing the way the block-out did, so the block-out path needs an orientation
   convention pinned by measurement rather than assumed from the exporter.

### The panel path, measured through the real operator

`Stylise Last Render` in the collapsed Advanced panel, driven headless through the operator and the
real job queue:

| | |
|---|---|
| Main-thread cost of the press | **0.63 to 1.00 s**, which is the RENDER and nothing else |
| The stylise itself | 4.1 to 10.0 s on the worker, over 409 to 990 main-thread ticks |
| **Longest main-thread tick while it ran** | **0.05 to 0.14 ms** |
| Scene state afterwards | view transform, material override and pass materials all restored |

The render is on the main thread because it has to be: `bpy.ops.render.render` is bpy, and R15's rule
is that every bpy touch happens there. So the honest description of the button is "one second of
render, then the stylise runs in the background", which is what its caption says.

### Nine things this plan had wrong, corrected here

1. **The compositor is not the way to get a pass out of Blender 5.2.** `Scene.node_tree` is gone
   (compositing is a node group on `Scene.compositing_node_group`), and `CompositorNodeOutputFile`
   writes one multilayer EXR that `bpy.data.images.load` will not open, regardless of per-item format
   overrides. The material-override route above is what ships. Anyone reading an older Blender
   tutorial will reach for the compositor first; this is the note that saves the hour.
2. **`image_settings.linear_colorspace_settings` does not control the output transfer, and without
   the right override every pass is gamma encoded.** Measured on a sphere: a linear 0.5 landed on
   byte **187**, which is sRGB(0.5), so a front-facing normal encoded as (187, 187, 255) instead of
   (128, 128, 255) and every depth ramp was a curve of itself. The fix is
   `color_management = 'OVERRIDE'` plus a **Raw** view transform on the image settings, with a
   numeric sRGB-to-linear undo kept as the fallback for a build that lacks Raw.
3. **Blender's camera-space normal has Z reversed relative to the normal-map convention.**
   `ShaderNodeVectorTransform` WORLD to CAMERA gives a surface facing the camera a Z of about -1, so
   an unflipped encode paints a front-facing surface BLACK in blue. X and Y are already right, so
   `NORMAL_FLIP` is `(1, 1, -1)`.
4. **Do not pick that convention by correlating against NormalBAE.** The first attempt did exactly
   that and the argmin chose to flip Z, i.e. the one channel that is provably right, because the test
   scene was mostly flat ground and the channel barely varied. A sphere is the correct test: red has
   to rise left to right, green bottom to top, and blue to saturate in the middle. NormalBAE's own
   agreement with the true pass is 52.1 of 255 on that scene with per-channel correlations of -0.48,
   0.00 and -0.15, which is a statement about the estimator rather than about the pass.
5. **The depth range has to come from geometry IN FRONT of the camera.** A ground plane large enough
   to reach behind the camera put `near` at 0.0001 m and spent most of the 256 available values on
   empty space (measured: an 8.88 to 24.93 m range became 0.00 to 25.70). The corners behind the
   camera are dropped.
6. **`POST /free` returns 200 with a ZERO-BYTE body, so `comfy.free()` has raised since G2 and the
   Advanced panel's Free VRAM button never worked.** It reported "ComfyUI returned non-JSON on
   /free" on every successful press. An empty 200 is now a value (`{}`) rather than a failure, with a
   test that asserts it against a fake that answers the same way.
7. **There is no standalone SDXL normal ControlNet on this disk.** The plan said "the SDXL ControlNet
   set is already downloaded" as if depth and normal were two models; what is there is
   `controlnet-depth-sdxl-1.0` plus the **union promax** model, so the normal hint goes through
   `SetUnionControlNetType(normal)`. Both W12 and W9 carry that node and there is a test for it.
8. **A LoRA cannot ship at strength 0.** A `LoraLoader` still has to NAME an installed file, and no
   shipped default can know what is on this machine (R6), so a placeholder fails the server's
   validator even though preflight skips it as a runtime input. `comfy.drop_node` removes the node
   and rewires its consumers instead, which is a graph edit rather than a strength.
9. **Two headless-gate traps worth writing down.** `bpy.ops.wm.read_factory_settings` re-reads
   preferences from factory defaults, which DISABLES an addon enabled at runtime and takes its Scene
   properties with it, so the scene has to be built before the addon is enabled. And a gate that
   imports `bob_blender_tools.core.comfy_jobs` by path while the addon is
   `bl_ext.user_default.bob_blender_tools` gets TWO module objects with two registries, so the
   operator's job is invisible: the gate imports the ADDON's module.

### What G4 deliberately did not build

No MV-Adapter. It is the fix for the drift measured above, and it is a download plus a wrapper; the
number it would have to beat is now on file (24.1 of 255 adjacent, 30.1 front-to-back), which is
exactly what makes it worth doing later rather than now. No paint-route button: the route is a value
(`comfy.TEXTURE_ROUTES`, `comfy.texture_chain()`) and `Generate Asset` still runs the PBR route,
because a plausible material is what a scatter prop needs and the stylised route needs an artist who
wants a stylised look. No `contracts.py` change, no MCP op, no reconnect; G6 still batches all of
that. No metallic or roughness from the paint route beyond the shared numpy derivation, which is
honest for nature assets. No Hunyuan turbo variant (it needs a `FluxGuidance` node and cfg 4, and the
non-turbo checkpoint already answers in 24 s).

---

## What G4c measured

**Verdict, in one sentence each.** A block-out proxy does condition generation and the result does
keep the block-out's shape: **footprint IoU 0.814 to 0.979 against ceilings of 0.840 to 0.992**, with
the proportions held to within 2%, scored where the mesh landed and with no rotation search.
**W7 beats the W6t multi-view baseline on all three block-outs and on every measure at once**, which
was not the expected outcome: 0.908 mean footprint IoU against 0.675, 4.6x faster (35.8 s against
164.9 s), and 2 GB lower peak VRAM. **The Omni wrapper is unmaintained and shipped broken**, in a way
that produces plausible output rather than an error, and finding that was most of the phase.

Files: `assets/workflows/mesh_geom_ctrl.json` (W7, new), `core/comfy.py`
(`mesh_geom_ctrl`, `omni_model_dir`, `unique_file_name`, and `control` as a value on the staged
chain), `core/gen_assets.py` (`export_control`, `footprint_ratio`, `turn`, `CONTROL_RETURN_TURN`,
`CONTROL_POINTS`, and `orient` threaded through `import_glb`, `prepare_low` and `finish_asset`),
`ui/scatter.py` (`Asset from Block-out` in the existing Generate Asset box, one operator with a
`from_control` property rather than a second one), `tools/scripts/comfy_omni_fix.py` (new),
`tools/scripts/headless_comfy_g4c.py` (new), `tools/tests/test_comfy.py` (57 tests, up from 53),
`tools/tests/data/object_info_min.json` (53 classes, up from 50). Suite **167**, up from 163. Two
bug fixes landed in `gen_assets` on the way, both on the STAGED route and neither about Omni:
`bake_high_to_low` had been reading across a rotation, and it had been dropping the generated albedo
whenever the geometry graph handed over an untextured mesh.

### The wrapper, evaluated the way G0.5 evaluated TRELLIS.2

**There is no maintained ComfyUI wrapper for Hunyuan3D-Omni. There is exactly one wrapper at all.**
The evidence, since the plan asked for it rather than for an impression:

- `Rizzlord/ComfyUI-Hy3D-Omni`: **3 stars, 0 forks, no `LICENSE` file, last push 2025-10-03**, which
  is nine and a half months before this phase. It vendors the whole of Tencent's `Hunyuan3D-Omni`
  (33 MB) inside itself and exposes five nodes: a pipeline loader plus Point, Voxel, BBox and Pose
  generators.
- `PozzettiAndrea/ComfyUI-HunyuanX`, which a search still describes as "ComfyUI wrapper node for
  Hunyuan3D-Omni", **is a 404** and is not among that author's twenty-plus current repos. The Awesome
  ComfyUI 3D index entry pointing at it is stale.
- `kijai/ComfyUI-Hunyuan3DWrapper` (1,025 stars) has no Omni support: no `omni` anywhere in its nodes.
- A GitHub code search for `Hunyuan3DOmni` finds RunPod images, research forks and notebooks, and one
  ComfyUI pack: the one above.

So the honest answer to the phase's own question is "no maintained wrapper exists", and the fallback
it named was W6t on block-out views. **It was installed and measured anyway, and it works**, which is
why W7 ships rather than the fallback. What it took:

1. **The dependency list is a trap, and almost none of it is real.** Upstream's `requirements.txt`
   pins `numpy==1.24.4`, `torchaudio==2.5.1+cu124`, `deepspeed`, `open3d`, `realesrgan` and
   `pytorch-lightning==1.9.5`; installing it into a torch 2.13.0+cu130 venv would destroy the venv.
   What the shape-only pipeline actually imports is five additive packages:
   `diffusers peft pytorch-lightning torchdiffeq pymeshlab`, and a `pip --dry-run` confirms they
   touch neither torch nor numpy. **`torch_cluster` and `diso` are not needed at all**, which is the
   part worth knowing: `torch_cluster.fps` is a lazy import inside one function of the shape VAE's
   surface encoder, which the point, voxel and bbox paths never enter, and `diso` is only reached by
   `mc_mode='dmc'`. `torch_cluster` has no wheel for this torch (`data.pyg.org` carries only
   `pyg_lib` for `torch-2.13.0+cu130`) but 1.6.3 does build from source against it with nvcc 13.3, so
   it is available if a later route wants it. **This is the compile-from-source risk R21 deleted
   coming back and turning out not to bite.**
2. **The weights are 13.5 GB and the auto-download goes to the wrong place.**
   `Hy3DOmniLoadPipeline`'s default `repo_or_path` is the HuggingFace repo id, which lands in
   `~/.cache/hy3dgen`. Skipping `*_ema.bin` halves 25.7 GB to 13.5. Three `.bin` pickles rather than
   safetensors, and `torch.load` reads them under torch 2.13's `weights_only=True` default without
   complaint.
3. **And then it generates a mesh that ignores the control, silently.** Covered below, because it is
   the finding rather than an install note.

### The defect that made the whole phase worth doing

The pack's vendored copy of `hy3dshape` differs from upstream in exactly one functional way: upstream's
`OmniEncoder.linear` is spelled **`self.liner`**. That attribute is the MLP projecting the
Fourier-embedded control signal into the DiT's token stream, the released checkpoint stores it as
`linear.*`, and `from_pretrained` loads with `strict=False`. So three tensors go missing, the
projection keeps its random initialisation, and **generation runs to completion with the control
reduced to noise**. Nothing raises. The only evidence is one line of server log:

```
Loaded .../cond_encoder/pytorch_model.bin with 442 missing and 3 unexpected keys
Missing Keys: Counter({'image_encoder': 439, 'liner': 3})
Unexpected Keys: Counter({'linear': 3})
```

Measured on a control shape nothing in the reference image would suggest, a 0.6 x 0.15 x 1.0 L:

| | Output extents, normalised | voxel IoU against the control |
|---|---|---|
| Control | 0.600, 0.150, 1.000 | -- |
| **Before the fix** | 1.000, 1.000, 0.958 | **0.010** |
| **After the fix** | 0.597, 1.000, 0.153 | **0.526** |

The proportions come back exact, with Y and Z swapped, which is the orientation finding below
arriving as a side effect. `tools/scripts/comfy_omni_fix.py` is the fix: it renames the three keys in
the direction the **installed** wrapper needs, detected from its source rather than hard-coded, so it
stays correct if the pack is ever updated to upstream's spelling. It keeps the original beside it and
`--check` reports without writing. The 439 missing `image_encoder` keys are benign: DINOv2 comes from
the transformers hub.

**Why the fix is on the weights and not on the code.** The wrapper is a pinned submodule of a fork
whose `./dev verify` asserts `git submodule status` is clean, and there is no upstream to send the
patch to. A three-key rename of a locally downloaded weight file leaves every git tree clean, is
reversible, and is checked on every gate run. That is the trade, recorded so nobody re-derives it.

### The orientation convention, pinned per exporter by measurement

A footprint gate cannot score best-over-rotation, because an asset that fits the layout only after
being turned does not fit the layout. So the convention had to be pinned first. Part A of the gate
loads and exports one asymmetric block-out through a graph with **no model in it** and searches all
24 axis-aligned rotations once, deliberately, to find the exporter's turn:

| | Result |
|---|---|
| Bob's own glTF export then import, no server | orientation-preserving, **IoU 0.5261 at identity** against a 0.5170 ceiling |
| **`Trellis2ExportTrimesh` round trip, left alone** | **IoU 0.0603**, footprint 0.3638 |
| Its best axis map | perm (0, 2, 1) signs (1, -1, 1), IoU 0.5164, and **a proper rotation, not a mirror** |
| **The same result with `CONTROL_RETURN_TURN` applied** | **IoU 0.5164 of a 0.5170 ceiling**, footprint 0.8532 of 0.8403, aspect ratio exactly 1.000 |

So the convention is one **-90 degree turn about X**, and its cause is in the exporter's source rather
than in the model: `Trellis2ExportTrimesh` converts internal Z-up to Y-up when it writes glb or gltf
(`verts[1], verts[2] = verts[2], -verts[1]`) and `Trellis2LoadMesh` converts nothing on the way in, so
a graph that loads a mesh and exports one is not symmetric. Blender's own glTF import turn does not
cancel it, because Bob's export applied the same turn going out. **Hunyuan's `SaveGLB` (W5, W6) needs
no turn**, which is the other half of G4's finding that the two exporters disagree: G4's alignment
search chose 0 quarter turns for it and 1 to 2 for the TRELLIS.2 meshes.

The asymmetric block-out is load-bearing for this measurement and not decoration. On a Y-symmetric
control, a mirror and a rotation score identically, so the sign is unresolvable: the first attempt at
this used a symmetric L and could not tell `(1, 1, 1)` from `(1, -1, 1)`.

### The turns ACCUMULATE, and that was a bug on a route this phase did not own

One turn is the right answer for W7's own output and the wrong answer for the chain. Measured hop by
hop on the same block-out, each file scored against it over all 24 rotations:

| file | turns to undo | best map |
|---|---|---|
| the control Bob sent | **0** | identity, IoU 0.5261 |
| W7 output | **1** | (0, 2, 1) (1, -1, 1) |
| W9c output | **2** | (0, 1, 2) (1, -1, -1) |
| W9t output | **3** | (0, 2, 1) (1, 1, -1) |

Every `Trellis2ExportTrimesh` glb write adds one turn, because W9c and W9t each LOAD their input (no
conversion) and EXPORT their output (one conversion). So the staged route hands `finish_asset` three
files in three different frames, and that has a consequence beyond the block-out route:

**`prepare_low` imports the dense mesh from the raw file and the low mesh from the simplified or the
textured one, so `bake_high_to_low` has been transferring normals and AO from a cage rotated 90 or 180
degrees away from its target.** Nothing errors. A misaligned bake still writes a non-flat normal map,
which is exactly why the G3 asset checks passed over it: the check was "baked normal std is not zero",
and it is not zero.

`gen_assets.undo_exports(count)` is the maths, `comfy.stage_exports(staged)` decides the count per
file, and `finish_asset(exports=...)` applies it. It is relative on the image routes (leave the raw
mesh's frame alone, bring the others into line, so no existing asset's orientation changes) and
absolute on the block-out route (undo the raw mesh's turn too, so the asset faces the way the
block-out did). `CONTROL_RETURN_TURN` survives as the name for `undo_exports(1)`.

The measured effect on the FINISHED asset, on the notched block-out:

| | footprint IoU | voxel IoU | aspect against the proxy |
|---|---|---|---|
| **With one turn undone everywhere (wrong)** | 0.6253 | 0.0768 | 1.000, 1.019, 0.950 |
| **With the turns counted per file** | **0.8100** | **0.3099** | **1.000, 0.987, 0.980** |
| the raw W7 mesh, for reference | 0.8136 | 0.3061 | 1.000, 0.987, 0.980 |

So the whole of steps 3 to 8 -- simplify, texture, bake, scale, origin, LODs, BobShade, glTF round
trip -- now costs the footprint **0.004**, which is the honest form of "it survives finishing".

**What this does to a G3b conclusion, stated as an open question rather than a claim.** G3b decided
the one-shot route's default partly on "the dense mesh the staged route keeps bought NO measurable
normal detail at a 4,000-face budget". That measurement was taken through a misaligned bake. It may
still be true -- 4,000 faces is a low budget and G3b's reasoning about it is sound -- but it is no
longer measured, and it should be re-run before it is trusted. The default is not at risk either way:
W9b won on VRAM, on boundary edges and on the black-albedo trap, and none of those involve the bake.

### W7 against the W6t baseline, scored where it landed

Three block-outs, two of them the **shipped** `core.proxies` rather than gate fixtures, and one
purpose-built with an alcove and an off-centre buttress on the back. Each conditions Omni on its shape
(W7, 8192 sampled points, 50 steps) and TRELLIS.2 on four Blender-rendered cardinal views of it (W6t,
the G4 accuracy winner). No rotation search anywhere in these numbers.

| block-out | route | wall s | peak MiB | rise | faces | IoU | **footprint IoU** | Chamfer | aspect error |
|---|---|---|---|---|---|---|---|---|---|
| rock | **W7** | **36.0** | **8,952** | **1,184** | 406,596 | **0.4875** | **0.9315** | **0.0157** | **0.012** |
| rock | W6t | 92.3 | 11,012 | 3,244 | 473,085 | 0.0749 | 0.9288 | 0.0415 | 0.212 |
| tree | **W7** | **35.3** | **9,342** | **1,184** | 125,538 | **0.8428** | **0.9787** | **0.0091** | **0.013** |
| tree | W6t | 159.2 | 10,930 | 2,772 | 418,266 | 0.4054 | 0.8874 | 0.0141 | 0.056 |
| notched | **W7** | **36.0** | **9,468** | **1,184** | 384,380 | **0.3061** | **0.8136** | **0.0183** | **0.020** |
| notched | W6t | 243.2 | 12,964 | 4,680 | 472,118 | 0.0198 | 0.2081 | 0.1052 | 0.230 |

The ceilings those are read against, each block-out against a second point sample of itself:

| block-out | ceiling IoU | ceiling footprint IoU |
|---|---|---|
| rock | 0.5177 | 0.9413 |
| tree | 0.8379 | 0.9920 |
| notched | 0.5170 | 0.8403 |

So W7 reaches **94%, 101% and 59%** of the achievable voxel IoU and **99%, 99% and 97%** of the
achievable footprint IoU. W6t reaches 14%, 48% and 4% of the voxel ceiling.

**The column that keeps the comparison honest.** W6t was never asked to preserve an orientation, so
scoring it without a rotation search charges it for something it does not claim. Here is the same data
with each result allowed its best axis map first:

| block-out | route | IoU as landed | IoU if turned first | footprint as landed | footprint if turned | best map |
|---|---|---|---|---|---|---|
| rock | W7 | 0.4875 | 0.4964 | 0.9315 | 0.9315 | (0,1,2)(-1,1,-1) |
| rock | W6t | 0.0749 | 0.0817 | 0.9288 | 0.9353 | (0,1,2)(-1,-1,-1) |
| tree | W7 | 0.8428 | 0.8542 | 0.9787 | 0.9787 | (0,1,2)(1,1,1) |
| tree | W6t | 0.4054 | 0.4054 | 0.8874 | 0.8874 | (0,1,2)(1,1,1) |
| notched | W7 | 0.3061 | 0.3061 | 0.8136 | 0.8136 | (0,1,2)(1,1,1) |
| notched | W6t | 0.0198 | 0.0434 | 0.2081 | **0.4069** | (1,0,2)(-1,1,1) |

**Read that table and the verdict does not change.** Turning W6t's results freely recovers very little:
its voxel IoU goes to 0.082, 0.405 and 0.043, still a third to a fifth of W7's. Only the notched
footprint moves materially, from 0.208 to 0.407, so half of that particular failure is orientation and
the other half is a different shape; W7 scores 0.814 on the same block-out without being turned at all.
W7's own best map is identity on two of three and a sign flip worth 0.009 on the symmetric blob, which
is the pinned convention working rather than luck.

**VERDICT: W7 beats four rendered views, and by enough that the cheaper route is not the honest
answer here.** W7 wins footprint IoU 3 of 3, means 0.9079 against 0.6748; wins wall clock 4.6x
(35.8 s mean against 164.9 s); wins peak VRAM by about 2 GB and the RISE over its own baseline by
2.4x (1,184 MiB against 2,772 to 4,680); and holds the proportions to within 2% where W6t drifts up to
23%. The one thing W6t keeps is texture-grade density on the tree, where W7 returned 125,538 faces
against 418,266, and at a 4,000-face budget that buys nothing (G3b measured exactly this and found no
detectable normal detail).

**Two honest qualifications.** First, the rock is a case where both routes succeed at the footprint
(0.9315 against 0.9288): a squashed blob's ground plan is easy from four views too, and the gap opens
on the tree and the notched block, where the silhouette carries information. Second, W7's absolute
voxel IoU on the notched block is 0.306 of a 0.517 ceiling, so a deep alcove is still only partly
respected; the footprint and the proportions survive, the fine concavity does not. The claim this
phase can defend is "the layout still works", not "the block-out is reproduced".

### The finished asset, and the residency question answered both ways

One block-out all the way through W7, W9c, W9t and steps 6 to 8, against the checks it inherits from
G3: **3,862 faces of a 4,000 budget, UV overlap 0.0, bbox height 0.6550 m against the block-out's
0.6550 m, origin 0.0 m above the base, LOD chain [3,862, 1,930, 578], `master_type()` = surface**, all
four maps (`ao, basecolor, normal, roughness`), and the footprint measured again on the finished asset
at **0.8100** against the raw mesh's 0.8136. W9c plus W9t cost **18.0 s** on top of W7's 36. The
basecolor took a fix to get there, which is correction 11 below.

**Residency, and it is a yes with an asterisk rather than the no the handover expected.** Measured on
the 16,303 MiB card:

| | Result |
|---|---|
| Omni's resident footprint once loaded | **~7.8 to 9.6 GB**, loaded at fp16 from a 12.2 GB fp32 checkpoint |
| W7's own per-run rise over that | **1,184 MiB**, so a second W7 job is nearly free |
| W7 with SDXL still resident from W4 | **runs, peak 10,816 MiB**, no manual `/free` needed |
| **`POST /free` with Omni resident** | **9,632 MiB still held.** ComfyUI's model management cannot evict it: the wrapper caches its pipeline in a module-level dict, outside everything ComfyUI knows about. Only a server restart reclaims it |
| The stylise route (G4's 14,194 MiB peak) with Omni resident | **survives, peak 13,504 MiB**, fitting SDXL plus both ControlNets into the 6,671 MiB Omni left free -- **at 13.6 s against 10.3 to 10.8 s alone** |

So the two heaviest jobs in this integration do share a card, and the cost is throughput rather than
failure: ComfyUI offloads instead of refusing and the stylise route runs about 30% slower. The
asymmetry is what matters for anyone planning a session: **SDXL can be evicted and Omni cannot**, so
the order to avoid is "W7 first, then a long stylise run". The handover's guess that an 8 GB wrapper
could not share a session with the 14.2 GB stylise route had the arithmetic right and the outcome
wrong, because it assumed neither side would offload.

### Eleven things this plan had wrong, corrected here

1. **`BOB_POINTS` does not exist, and neither does a point-cloud file format.** The wrapper's control
   socket is TRELLIS.2's `TRIMESH` type, so the control is a MESH the node samples itself and
   `Trellis2LoadMesh` is the loader. Which means the second half of that deliverable dissolved:
   `export_control` needed **no new exporter**, because Omni normalises into the unit cube exactly as
   `Trellis2EncodeMesh` does and the fix is the same `unit_normalise_export` round trip track B
   already owns. The plan expected a new format and a new exporter; what it got was a nine-line
   function that names a point budget and records the return turn.
2. **The control's default density is unusable and silent about it.**
   `Hy3DOmniPointGenerate`'s `sample_point_count` defaults to 0, which means "use the control mesh's
   raw VERTICES". That is right for the scanned point clouds upstream conditions on and useless for a
   Bob block-out: `Rock_B` has 42 vertices and `Tree_A` has 19 faces. Bob binds 8192.
3. **`BOB_PROMPT` has no home in W7.** Omni takes no text conditioning at all: `OmniEncoder.forward`
   takes image, surface, pose, bbox, point and voxel, and the pipeline's `prompt` argument is dead. So
   "plus `BOB_PROMPT` for detail" was wrong, and the artist's words reach W7 only through the W4
   reference image, which is also why the block-out route still generates one.
4. **A `strict=False` load is a silent-failure surface, and this plan had no check for it.** Preflight
   catches five failure classes and none of them can see a checkpoint whose keys did not match the
   code. `comfy_omni_fix.py --check` is the sixth check, and it is a separate script rather than a
   preflight rule because it needs torch, which Blender does not have.
5. **"Needs Omni weights and a wrapper" understated the wrapper by a lot.** It is the least maintained
   dependency in this integration by an order of magnitude and the only one with no license file. That
   is now written into the submodule table rather than implied.
6. **The block-out route cannot use the one-shot chain, so G3b's default does not apply to it.** W9b
   generates its own geometry from the image and takes no control mesh, so `control` forces the staged
   W4 -> W7 -> W9c -> W9t chain. That is not a regression: the staged route was kept wired at G3b for
   exactly this kind of reason.
7. **`force_reload` on the pipeline loader OOMs a 16 GB card.** The wrapper caches pipelines in a
   module-level dict and builds the new one before dropping the old, i.e. two 12 GB checkpoints at
   once. Restart the server instead. The same dict is why `POST /free` cannot reclaim Omni's VRAM,
   which is what part D of the gate is about.
8. **A ceiling has to be measured per block-out, not once.** The self-agreement ceiling ran from 0.517
   on the rock to 0.838 on the tree at the same grid and sample count, because a thin conical proxy
   fills fewer surface voxels than a squashed sphere. A single global ceiling would have made the tree
   look worse than the rock when it is the best result of the three.
9. **`octree_resolution` 512 is waste in this pipeline.** It returns about 700,000 faces against
   200,000 at 256, and Bob's next step is a 4,000-face budget either way. W7 ships at 256, which is a
   deviation from upstream's own default and the reason W7 is faster than its 50-step count suggests.
10. **One orientation constant was not enough, and the reason was a bug on another route.** The turns
    accumulate per `Trellis2ExportTrimesh` write, so the staged chain's three files are in three
    frames and `bake_high_to_low` had been reading across a 90 or 180 degree rotation on every staged
    asset since G3. Written up above; `comfy.stage_exports` is the fix and it is one concept where the
    first attempt had two.
11. **The bake reads colour from the DENSE mesh, and Omni hands over no material at all**, so the
    block-out route's first finished asset came out with `ao` and `normal` and no albedo: W9t's texture
    was on the low mesh and `bake_high_to_low` never looked there. TRELLIS.2's geometry output happens
    to carry a material, which is why the same code path has always worked on the W5t staged route and
    why this only surfaced here. The roles now choose their own source: normal and AO are a transfer
    and still need the dense mesh, colour and roughness are a SELF bake with no cage when only the low
    mesh is textured, because the generated PBR is already in the low mesh's own UVs. `maps` on the
    block-out asset is now `ao, basecolor, normal, roughness`, and the gate asserts the basecolor is
    there.

### What G4c deliberately did not build

No voxel route. `Hy3DOmniVoxelGenerate` is installed and its class is in the committed
`object_info` dump, but W7 ships the point node: upstream's own voxel path applies a hard-coded -90
degree turn about X to its input (`apply_input_rotation`, on by default) because its demo voxel assets
are Z-up while its point assets are Y-up, and adding a second orientation convention to measure was
not worth it when the point route already clears the gate. No bounding-box or skeleton route for the
same reason, though the bbox one is interesting for a later phase: it is the cheapest possible control
and Bob knows every proxy's bbox for free. No `contracts.py` change and no MCP op; G6 still batches
all of that, and `export_control` is on its list. No second panel: `Asset from Block-out` is the same
operator with a `from_control` property, shown only when the active object is a mesh.

---

## What G5 measured

**Verdict, in one sentence each.** A prompted silhouette does survive an erosion pass and it is not
close: **band-limited correlation 0.906 to 0.923 at the mask's own cutoff, against a no-mask null of
0.078 to 0.208** on the same preset and seed. Erosion still builds the landform rather than
decorating a blurred picture: the mask explains **11 to 16%** of the band above its own cutoff, the
finished field carries **2.89 to 3.04 m** of fine relief against a mask-only baseline's **0.28 to
0.31 m**, and its median slope is **42.1 to 42.8 degrees** against the mask's 24.6 to 27.3 and the
no-mask bake's own 44.7. **R7's terracing does not happen, and it does not happen at 5 bits either**,
which makes the 8-bit question a different question than the plan assumed. And **W13 is W1's topology
with different values**, which is written into its provenance rather than dressed up.

> **Two corrections from G6.** This fork's dynamic-VRAM staging segfaults the server on the second
> copied-model decode of a session, which affected `Generate Base` on its tiled route and every texture
> set; the padding is now applied IN PLACE and undone by `ensure_untiled`, and the G5 numbers below
> reproduce under it. That reset is also **required** on this route rather than incidental: the OPEN
> route drops the padding nodes, so without it a texture set earlier in the session would make the mask
> tile, and G5's own measurement is that a tiling macro mask repeats the landform (seam 0.80 against
> 86.18). Verified after the change: the open route's raw generation reads **10.086** and the tiled
> route's **0.964**. See [What G6 measured](#what-g6-measured) and D14.

Files: `assets/workflows/heightmap_macro.json` (W13, new), `core/comfy.py` (`heightmap_macro`,
`macro_prompt`, `MACRO_SUFFIX`, and `macro_tiling` as the route value), `core/comfy_maps.py`
(`macro_field`, `macro_from`, and a `wrap` flag on the shared box blur),
`core/heightfields/ops_generate.py` (the `macro` op), `core/heightfields/engine.py` (its
registration), `core/heightfields/params.py` (`with_macro`, and `macro` as a bake knob),
`core/heightfields/pipeline.py` (`_stack_file_sig`), `core/heightfields/io.py` (`read_png` beside a
still-strict `read_png16`), `__init__.py` (`BBT_OT_terrain_generate_base`, five properties on
`BBT_HeightfieldProps`, `_macro_knob`, `_draw_generate_base`, and a `target` on the existing seed
reshuffle), `tools/scripts/headless_comfy_g5.py` (new), `tools/tests/test_comfy.py` (62 tests, up
from 57), `tools/tests/test_heightfields.py` (60, up from 53). Suite **179**, up from 167. No new
node classes, so `object_info_min.json` is unchanged at 53.

### The survival measurement, and the null it is read against

Three prompts, chosen so one is a shape erosion should FIGHT and one is a shape it should AGREE with,
each baked three ways on the same preset (`alpine`) and seed: the mask alone with no erosion at all
(the blurred-image baseline), the mask plus the preset stack (the shipped path), and the preset stack
with no mask (the null). Bands are split at the mask's own cutoff, which is a gaussian of
`MACRO_LOWPASS_FRACTION * n / sqrt(3)` rather than `* n` -- a box blur of radius r has the second
moment of a gaussian of r/sqrt(3), and splitting at the wider sigma charges the mask for content it
does not have.

| prompt | mask s | bake s | r_low | no-mask null | r_high | null r_high | mask-linked share of the fine band | fine band as a share of the field |
|---|---|---|---|---|---|---|---|---|
| isolated steep massif | 4.5 | 7.5 | **+0.9057** | +0.2079 | +0.3476 | +0.0120 | 12% | 12% |
| broad shallow basin | 4.6 | 7.5 | **+0.9136** | +0.0782 | +0.3288 | +0.0016 | 11% | 11% |
| long corner-to-corner ridge | 4.5 | 7.5 | **+0.9229** | +0.1442 | +0.3972 | -0.0225 | 16% | 11% |

`r_high` came back at 0.33 to 0.40 where the handover expected near zero, and that is not a leak: the
no-mask null's `r_high` is +0.012 to -0.023, so the coupling exists only when the mask is in the
stack, and its cause is that `amplify` seeds its detail band **on slopes**
(`amp * band * _slope01(h)`). The macro decides where the slopes are, so it decides where the fine
detail lands. The honest form of the number is `r_high` squared -- the mask explains 11 to 16% of a
band that is itself 11 to 12% of the field's variance, so about 1.5% of the finished landform is
mask-linked fine detail and the rest of that band is the erosion's.

And in real units on a 180 m tile at 54.0 m relief, which is what says this is a landform rather than
a blurred image:

| prompt | fine relief, full | fine relief, mask only | median slope, full | mask only | no-mask bake | slope-area gradient, full | no-mask | mask only |
|---|---|---|---|---|---|---|---|---|
| massif | **2.974 m** | 0.299 m | **42.76 deg** | 24.56 | 44.70 | +0.432 | +0.322 | -0.207 |
| basin | **3.043 m** | 0.275 m | **42.14 deg** | 25.39 | 44.70 | +0.419 | +0.322 | -0.206 |
| ridge | **2.886 m** | 0.305 m | **42.69 deg** | 27.34 | 44.70 | +0.414 | +0.322 | -0.143 |

Fine relief is measured above a 64th-of-the-width cutoff, five times finer than anything the mask can
carry, so the ten-fold gap is the erosion's contribution stated in metres. The slope-area gradient is
read against the no-mask bake and not against Flint's law, for a reason that is a finding in its own
right and is correction 10 below.

**VERDICT: the mask decides the layout and the stack decides the terrain, which is what track E was
for.** The two shapes the phase was built around behaved the same way, so the prediction that erosion
would fight an isolated massif and agree with a broad basin did not show up as a difference in
survival: `r_low` is 0.906 for the massif and 0.914 for the basin, and the massif's fine relief is
2.974 m against the basin's 3.043 m. If there is a case where a silhouette does not survive, these
three did not find it.

### The 8-bit question, and R7 turned out to be asking about the wrong failure

R7 predicted visible terracing from 256 levels. Measured on the basin mask, quantised to 16, 8 and a
deliberately-crushed 5 bits and run through the identical stack:

| | levels in the file | worst-cell mask error | rms after the stack | worst cell after | r against 16-bit | fine relief | median slope | histogram concentration | flat-pair fraction |
|---|---|---|---|---|---|---|---|---|---|
| 16-bit | 65,315 | 0.0004 m | -- | -- | 1.00000 | 3.038 m | 42.10 | 1.929 | 0.02447 |
| **8-bit, as shipped** | 256 | **0.1059 m** (0.196% of 54 m) | **0.8006 m** | 32.751 m | **0.99804** | 3.043 m | 42.14 | **1.864** | 0.02427 |
| 5-bit | 32 | 0.8710 m (1.613%) | 1.7227 m | 38.891 m | 0.99090 | 3.051 m | 42.06 | 1.912 | 0.02297 |
| a different SEED, for scale | -- | -- | **9.2792 m** | 44.928 m | 0.73320 | 3.214 m | 40.04 | 3.262 | 0.02348 |

**There is no terracing at any bit depth tested**, and the reason is the op's own blur rather than the
bit depth: the mask is resampled to the macro level and blurred at a fiftieth of the field width, and
that averaging puts a 256-level file back above 8-bit precision -- 256 distinct levels in the file
become **27,936** after the resample and blur. The float derivation the file is written from carries
588,851 distinct levels, because the derivation averages three 8-bit channels over a box of radius
width/12, so the 8-bit write is the ONLY 8-bit step on the route and it costs 0.196% of the relief.

What 8 bits does cost is not precision, it is **determinism**: 0.106 m in the mask becomes 0.80 m rms
in the finished terrain, an eight-fold amplification, because a stream-power stack is chaotic in its
initial condition and a slightly different mask puts a channel in a slightly different place. That is
real and it is small on the scale that matters: a reseed moves the same terrain 9.28 m rms at r 0.733,
eleven times further. In the render, with EEVEE deterministic to a 0.0000 noise floor:

| render against the 16-bit path | mean, of 255 | max |
|---|---|---|
| 8-bit | **0.5526** | 182 |
| 5-bit | 2.6361 | 186 |
| a different seed | 35.2617 | 189 |

So the answer to "how much of the mask's usable information survives 256 levels, and is it visible" is
**effectively all of it, and no**: the landform statistics are identical to three significant figures
(fine relief 3.043 against 3.038 m, median slope 42.14 against 42.10, the mask's own survival r_low
+0.9136 against +0.9136), and the frame moves by half a grey level. **The 16-bit save node stays
deferred, and now the deferral has a measurement behind it instead of an assumption.**

### Wall clock, VRAM, and the session question

| | Result |
|---|---|
| Mask | **4.3 to 6.5 s** at 1024 square, 20 steps |
| Peak VRAM | **7,444 to 9,844 MiB** of 16,303 summed over the ComfyUI family |
| Rise over the stage's own baseline | **352 MiB** with SDXL already resident, 2,752 to 7,152 MiB on a run that has to load it |
| Bake, 768 px, GPU | **7.4 to 7.5 s**, unchanged from a no-mask bake |
| **Prompt to a built terrain, through the panel** | **about 12 s** (4.6 s mask, 7.4 s bake and build) |
| Main-thread cost of the Generate Base press | **0.3 ms** |
| Longest main-thread tick while the job ran | **0.08 ms** over 497 ticks |
| `POST /free` after a W13 run | 468 MiB still held, i.e. SDXL evicts cleanly |
| **W13 with Omni resident** (11,266 MiB held by the family) | **runs, 5.2 s, peak 13,858 MiB**, rise 2,592 |
| `POST /free` in that state | 9,634 MiB still held, which is G4c's Omni figure again |

So this is the one route in the integration that shares a card with anything: it is SDXL at 1024 with
no ControlNet, it is the lightest generation Bob does, and it ran unchanged with the heaviest thing in
the integration already resident. The G4c session-planning warning does not apply to it.

### Twelve things this plan had wrong, corrected here

1. **The op stack has no "first input" to feed, and a mask prepended to a preset does nothing.**
   `engine.run_stack` starts every stack from a ZERO field, so "the stack's first input" is really
   "be op 0" -- fine -- but every shipped preset opens with a generator whose `mix` is `replace`, so a
   macro op placed in front of one is overwritten on the very next op and **nothing raises**. That is
   the whole reason `params.with_macro` exists: it prepends the mask AND demotes the stack's own
   generator to an `add` of the remaining relief, so the artist's silhouette and the family's
   character are one weighted sum. This was the single most likely way for the phase to ship a
   feature that silently did nothing, and the plan's phrasing pointed straight at it.
2. **The mask does not run at the bake resolution.** Every shipped preset ends in `amplify`, whose
   coarse macro runs at `params.AMPLIFY_BASE` (256) so that a preview is a faithful prefix of a full
   bake, so a 1024-square mask is resampled to 256 and the cascade re-creates the resolution
   afterwards. Which means mask resolution is not a quality axis on this route at all, and the panel
   ships no size widget: 1024 is SDXL's native size and a second resolution knob would only be a way
   to generate a worse composition.
3. **Track E needed no derivation module, and the honest answer was five lines.** `macro_field` is
   `relief()`'s complement: same `luminance`, same box blur, the other side of one cutoff, with a
   percentile stretch instead of a recentre because the stack reads it as an elevation ordering
   rather than a signed displacement. What it DID need was one thing the plan had no reason to
   predict: a `wrap` flag on the shared box blur, because every track A map is a tile and a terrain
   mask is not, and a wrapping blur bleeds the far side of the landform into this one.
4. **Whether a macro mask wants to tile is answered, and the answer is no.** Measured: seam ratio
   **0.80** with circular padding against **86.18** without, i.e. the tiling route genuinely does put
   the same elevation on both borders, which is exactly the repeat that makes a single terrain tile
   read as a wallpaper. So `open` is the default, `tiled` survives as a value for the endless-sheet
   case, and `comfy.macro_tiling()` is where that route is decided. The open route **drops**
   `SeamlessTile` and `MakeCircularVAE` rather than switching them to "disable", which is the
   `BOB_LORA` argument from G4 (R6): a disabled node still has to name an installed pack.
5. **R7 predicted the wrong artefact.** Terracing does not appear at 8 bits and does not appear at 5,
   because the mask is blurred before erosion ever sees it. The measurement is above; the practical
   consequence is that bit depth is not the binding constraint on this route and the 16-bit save node
   is deferred on evidence.
6. **What the 8-bit write does cost is determinism, not precision, and the plan had no concept for
   it.** The stack amplifies 0.106 m of mask error into 0.80 m rms of terrain because stream-power
   incision is chaotic in its initial condition. Nothing is wrong with that -- a reseed moves it
   eleven times further -- but it means "the same prompt and seed" is only reproducible while the
   mask FILE is byte-identical, which is why correction 7 matters.
7. **The bake cache had a hole that only a file-reading op could open.** `pipeline.bake` keys on the
   resolved recipe, and a recipe naming a path is only as identified as that file's contents, so a
   regenerated mask written to the same name was served the PREVIOUS terrain from cache.
   `_stack_file_sig` digests every file any op reads, which is generic rather than macro-specific so
   the next such op needs no change. R16's unique naming makes this rare in the panel and it is not
   rare at all from a script.
8. **`io.read_png16` could not read the file this route writes.** It is deliberately strict and it
   stays strict -- an 8-bit file accepted as a terrain BASE would terrace it into 256 benches and
   nothing downstream would say a word -- so the fix was to factor the decoder into `read_png`
   (8 or 16 bit, grey or RGB(A), alpha dropped) and keep `read_png16` as the validating entry the
   bake path uses.
9. **W13 is W1's topology with different values, and that is the honest description.** Nine nodes,
   the same nine classes, the same wiring. What differs: the prompt brief and negative (a top-down
   elevation map, not a material), 20 steps at cfg 4.0 against 25 at 5.0, and `euler` with the normal
   scheduler against `dpmpp_2m` + `karras`, because karras front-loads the fine-detail steps and this
   route discards exactly that half of the schedule. It earns a file for the brief, the tuning, the
   provenance and the droppable tiling pair, not for wiring, and `_bob.notes` says so.
10. **The terrain engine's own slope-area gradient is POSITIVE, which is the opposite of Flint's
    law.** Found while building the gate, not looked for. A no-mask `alpine` bake gives **+0.322**
    (slope RISES with drainage area) with a strong fit (binned medians, r 0.86 to 0.89); masked bakes
    give +0.414 to +0.432; the mask alone gives -0.143 to -0.207. So the statistic discriminates
    cleanly and the gate scores against the engine's own output rather than against theory, which is
    the right call for a gate about a mask. But an equilibrium fluvial landscape has a NEGATIVE
    gradient of roughly 0.3 to 0.6, and a finite-iteration stream-power stack with no uplift is not
    an equilibrium landscape, so this may be entirely expected. It is not G5's to resolve and it is
    now **D13**.
11. **A second seed on a panel needs a reshuffle, and that is not a second operator.**
    `BBT_OT_random_seed` grew a `target` property (`terrain` or `macro`), the way the Scatter panel's
    reshuffle already serves both a layer socket and the Generate Asset seed.
12. **A params dict carrying BOTH a resolved stack and a mask ignored the mask, and the MCP route is
    the one that hits it.** `pipeline._stack_for` returned an explicit `stack` verbatim, and
    `presets.get()` returns a params dict with the stack already resolved in it, which is exactly what
    `mcp_agent/server.py:bake_heightfield` builds from a `preset` argument. So the agent-facing route
    would have accepted `macro` and baked the unmasked terrain, silently -- the same class of failure
    as correction 1 and found the same way, by writing down what the caller actually passes.
    `_stack_for` now composes a macro onto either shape and is idempotent, so `build_params` having
    already applied it is not a second application, and the panel stopped doing its own composition:
    one owner, not two. This is also why `comfy_heightmap()` needs no new op from G6.

### What G5 deliberately did not build

No 16-bit or EXR save node: measured unnecessary, correction 5. No mask staging or Accept flow -- R9
is about variants awaiting a decision, and a macro mask is an INPUT to the next bake rather than an
artifact, so it lands in `<output>/macro/` beside the terrain it feeds and never in the generated
pack's `_staging/`, where the texture-variant picker would list it. No tiled-terrain UI: the route is
a value with a measured default and no shipped feature stitches tiles yet, so a radio button would be
knob sprawl on a decision nothing can act on. No invert auto-detection -- which way a model paints an
elevation map is genuinely a coin flip per prompt, and a plausibility rule for it would need a
measurement this phase does not have, so it is one toggle that says what it does. No `macro` entry in
the advanced Filter Stack editor: the op is composed at bake time from the panel's own state, and the
editor's kind list is for ops an artist hand-authors. No `contracts.py` change and no MCP op; G6
still batches those, and the terrain mask write is on its list, which is why the Blender half is a
`core` function (`heightfields.params.with_macro`, reached through the `macro` bake knob) that G6 can
wrap without touching the panel.

## What G6 measured

**Verdict: the generation surface is agent-drivable, and the phase's most valuable finding is not
about Bob.** An agent goes prompt to a scattered, correctly scaled, UV'd, PBR-textured, BobShaded
asset in **102.5 s** with no GUI, and prompt to a rendered shaded terrain in **24.1 s**, both from
MCP tool calls and one op list. The contract change was paid once, as planned. Websocket progress
shipped rather than deferring again, and it is **28 per-node updates against 5 status strings** on the
same five-second job. `THIRD-PARTY-MODELS.md` exists and found two non-commercial models and a node
pack with no licence at all. And the whole thing is one command:
`tools/scripts/headless_comfy_all.py`.

The finding that matters most: **this ComfyUI fork's dynamic-VRAM staging segfaults the server on the
second copied-VAE decode of a session**, which is every texture-set and macro-mask graph Bob ships.
It is not Bob's code. Five candidates were measured; the shipped fix asks for circular padding IN
PLACE and undoes it before anything that must not wrap, so dynamic VRAM stays on. Details below.

Files: `core/comfy_ws.py` (new), `core/comfy.py` (`CLIENT_ID`, `wait(progress_ws=...)`),
`core/shading.py` (`set_texture_set`, `apply_texture_set`), `core/gen_assets.py`
(`import_generated_op`, `export_control_op`, `_resolve_pack`), `core/proxies.py` (`collection`),
`core/assets.py` (`generated_root`'s env fallback, `ensure_generated_pack`, the `asset_roots` fix),
`core/dispatch.py` (three handlers), `mcp_agent/contracts.py` (three models plus `OpResult.data`, one
edit), `mcp_agent/server.py` (six `comfy_*` tools, the `macro` key on `bake_heightfield`),
`mcp_agent/paths.py` (`generated_pack`), `ui/shaders.py` (`_apply_texture_set` routed through core),
`__init__.py` (the licence notice, `_generated_pack_dir` subtracted),
`docs/THIRD-PARTY-MODELS.md` (new), `tools/scripts/headless_comfy_g6.py` (new),
`tools/scripts/headless_comfy_all.py` (new), `tools/tests/test_comfy.py` (73 tests, up from 62),
`tools/tests/test_contracts.py` (38, up from 24). Suite **196**, up from 179.

### The two gates, measured through the real agent path

Both numbers are from `tools/scripts/headless_comfy_g6.py`, which is the one gate in this plan that
does **not** run inside Blender: it calls the MCP tool functions in the MCP process and reaches
Blender only through `executor.run_build`, the way an agent has to. So a wrong tool signature, a wrong
contract model or a missing handler fails it where a `core`-level test would pass.

**Prompt to a scattered asset, no GUI.** One `comfy_mesh` call, then one build carrying
`import_generated`, a scatter layer, a camera and a render:

| Stage | Wall clock |
|---|---|
| `comfy_mesh` (W4 then W9b over HTTP) | **97.7 s** warm, 162.6 s on the run that loads TRELLIS.2 |
| The build: finish, import, scatter, sky, EEVEE render | **4.8 s** |
| **Total, prompt to a rendered scatter** | **102.5 s** |

And the asset inspected rather than trusted, every figure read out of the op's own `data` because
that is what an agent can see:

| Gate item | Result |
|---|---|
| Face count inside the budget | **3,672 to 3,930** against 4,000 |
| A UV layer with no overlap | **0.000000 to 0.0000017** against a 0.01 threshold |
| `height_m` honoured on the built object | **1.8 m** exact |
| Origin at the base | **0.0 m** above the lowest vertex |
| A BobShader on it | `surface` |
| A scatter layer instances it | `ScatterRocks` |

**Prompt to a shaded terrain, no GUI.** `comfy_texture_set`, then `comfy_heightmap`, then
`bake_heightfield` with the `macro` key, then one build:

| Stage | Wall clock |
|---|---|
| `comfy_texture_set` (five maps written and resolved) | **5.7 to 7.0 s** |
| `comfy_heightmap` (W13, the macro mask) | **3.9 s** |
| `bake_heightfield` with the macro key, 768 px, GPU | **7.5 s** |
| Build the terrain, shade it, apply the set, sky, EEVEE render | **3.8 to 6.7 s** |
| **Total, prompt to a rendered shaded terrain** | **24.1 s** |

The mask reaching the bake is measured rather than assumed: the masked and unmasked bakes of the same
preset and seed resolve to **different recipe hashes** (`0d58a18ef7` against `9fa752252d`), which is
the check that would have caught G5's correction 12 had it existed then.

### Every shipped gate, one command

`uv run --project tools --extra all python tools/scripts/headless_comfy_all.py --fast`, on the
reference 5080. Peak VRAM is the WHOLE CARD sampled at 4 Hz while each gate ran, not the per-process
figure the individual gates report, because this is the number that says whether a gate can share the
machine:

| gate | phase | status | wall | peak VRAM | checks |
|---|---|---|---|---|---|
| `g0` | G0 texture-set sampler | PASS | 2.8 s | 13,789 MiB | 32 |
| `texset` | G1 prompt to a shaded layer | PASS | 11.3 s | 15,067 MiB | 13 |
| `g2` | G2 variants, preflight, maps | PASS | 115.0 s | **15,846 MiB** | 37 |
| `g3` | G3 prompt to a scattered asset | PASS | 29.1 s | 12,003 MiB | 38 |
| `g3b` | G3b one-shot against staged | PASS | 68.0 s | 9,385 MiB | 42 |
| `g4` | G4 stylise, paint, multi-view | PASS | 9.3 s | 9,714 MiB | 18 |
| `g4c` | G4c Omni block-out control | PASS | 4.4 s | 8,724 MiB | 4 |
| `g5` | G5 terrain macro mask | PASS | 1.5 s | 8,668 MiB | 7 |
| `g6` | G6 the agent-facing surface | PASS | 2.6 s | 8,720 MiB | 23 |
| | **total** | **9 of 9** | **243.9 s** | **15,846 MiB** | **214** |

Those are with **dynamic VRAM on**, which is the shipped configuration. The same suite with
`--disable-dynamic-vram` totalled **185.3 s at a 13,808 MiB peak**, and the difference is worth stating
plainly rather than hiding, because it cuts against the shipped choice: **for the routes Bob ships
today, staging is slower and uses more of the card, and buys nothing measurable.** Nothing in the
integration currently exceeds 16 GB, so the memory it stages is memory it did not need to. What it buys
is the option -- the 1536 hero tier, and any future model that does not fit -- and losing that option
permanently to work around four graphs is the trade this phase declined to make. Both configurations
pass every gate; if wall clock ever matters more than headroom, the flag is one line.

`--fast` is what these numbers are: fewer prompts, cached generations, no slow A/B baseline. It
measures less than a full run and it still runs every gate, which is the property that matters for a
regression check. A full sweep is roughly 100 minutes of GPU time (`--list` prints the per-gate
estimate).

**Three gates needed fixing to report at all, and all three were harness bugs rather than product
regressions.** G2 crashed in its own `_Stub` layout (no `column`, which G4's stylise block calls) and
had been doing so silently since G4; `--sets 1` divided the drift computation by an empty half; and the
runner's first verdict reader did not know that G3 and G3b say "0 failure(s)" where the others say "no
failures". Nothing in the product had regressed, which is the honest result, but nobody could have
known that before this command existed.

### The crash that was not Bob's, and the fix that keeps the feature

Part C failed on its first run with `ComfyUI not reachable`, and the server was in fact dead. The
crash log has no Python traceback because it is a segfault:

```
Fatal Python error: Segmentation fault
  File "comfy_aimdo/host_buffer.py", line 129 in __del__
  File "comfy/model_patcher.py", line 1803 in load
  File "comfy/model_patcher.py", line 2024 in partially_load
  File "comfy/model_management.py", line 771 in model_use_more_vram
  File "comfy/model_management.py", line 743 in model_load
  File "comfy/sd.py", line 1105 in decode
  File "nodes.py", line 335 in decode          <- the STOCK VAEDecode
```

Reproduced with the websocket disabled and from a two-call script, so it is not G6's code: **the
second copied-VAE decode of a session kills the process.** `MakeCircularVAE(copy_vae="Make a copy")`
plus `SeamlessTile(copy_model="Make a copy")` are what make a texture tile (D4), so this is every W1,
W2, W3 and W13-tiled run. It is the same crash SITE as G1's `CircularVAEDecode` segfault
(`model_management.py` `model_load`), which means **G1's fix has stopped working** on this fork: what
is new is `comfy-aimdo` 0.4.10's dynamic VRAM staging. G2 measured ten sets in one session with no
crash, so this is an environment regression since G2, not a latent bug in the graph.

**Five candidates, measured, because the first fix that works is not always the right one.** Losing
dynamic VRAM means losing the staging that lets a 16 GB card hold a model bigger than its free VRAM,
which is too much to pay for four graphs if anything cheaper works:

| Candidate | Crash | Tiling quality | Verdict |
|---|---|---|---|
| `POST /free` between jobs | **dead on job 2** | -- | The copy is still garbage; freeing models does not help. |
| Patch the pack to pin the copy | **dead on job 5** | seam 0.91 to 1.24 | Delays it only, and it needs a fork patch. Reverted. |
| `--disable-dynamic-vram` | none | seam 0.98 to 1.01 | Works, and costs the whole install its staging. |
| In place, no reset | none | seam 0.90 to 1.14 | Works, and **wraps every later frame**: W4 came back at seam 1.059. |
| **In place, plus a lazy reset** | **none** | **seam 0.83 to 1.18** | **Shipped.** |

Two of those measurements decided it. The pinned-copy attempt failing on job 5 relocated the fault:
`__del__` is reached from **inside** `model_patcher.partially_load`, so the staging path releases the
buffer during a partial load and a strong reference to the VAE object cannot keep its internal buffers
alive. And the control run says staging itself is fine -- **with no copy anywhere, SDXL then a 15 GB
TRELLIS.2 job then SDXL again ran clean**, five jobs, forcing real eviction and re-staging. So the
deepcopy is the ingredient, only four graphs make one, and the answer is to stop making it.

`comfy.TILING_COPY_MODE` is therefore `"Modify in place"`, bound through `tiling_values()` so the
decision is a value in one place beside `asset_chain()` and `macro_tiling()`. What in place costs is
real and G1 named it: it mutates the SESSION's shared model, so the next graph on the same checkpoint
inherits circular padding. `comfy.ensure_untiled()` is the other half, and it is **lazy** -- it resets
only when a tiling graph has actually run, and only in front of a graph that must not wrap, so ten
texture sets in a row pay nothing and the cost lands once ahead of the next subject image. It reuses
W1 itself at 64 px and one step rather than shipping a reset graph, so there is no second copy of the
tiling wiring to drift.

Measured end to end, dynamic VRAM on, through Bob's own code:

| | Result |
|---|---|
| **Ten texture sets in one session** | **10 of 10**, mean **5.93 s**, drift **-0.01 s**, seam **0.83 to 1.18** |
| W4 subject image after those ten | seam **8.466**, i.e. untiled (1.059 without the reset) |
| W13 open route, on the raw generation | seam **10.086**, untiled |
| W13 tiled route, on the raw generation | seam **0.964**, wraps, which is what that route is for |
| Cost of the reset | about 1 s, once per switch from tiling to non-tiling |

Four call sites carry it: `_texture_values` (W1, W2, W3) and the W13 tiled route mark the model
padded; W4's `subject_image`, `stylize_render` (W12, W12e, W9) and **W13's OPEN route** reset first.
That last one is a correctness bug this fix would have introduced if it had been missed: the open
route DROPS the padding nodes, so it runs on the shared model, and a mask that tiles puts the same
elevation on both borders, which is the wallpaper repeat G5 measured and rejected.

**`--disable-dynamic-vram` stays documented as the fallback**, for the one case Bob cannot cover: in
place mutates process-global state on the server, so another client generating concurrently could see
a padded model inside that window. Bob runs one job at a time and the reset is idempotent, so this is
narrow, but it is real, and it is why the flag is written down rather than forgotten. The flag state is
**not visible over HTTP** (`/system_stats` lists `comfy-aimdo` as installed but says nothing about
whether staging is on), so Bob cannot detect either configuration and does not try.

### Websocket progress: shipped, not deferred again

The plan deferred this to G7 twice. It shipped here because the deferral's reason had expired: the
argument was that "1 Hz job polling carries a progress string fine", and it does, but the string is
the job's own status, so a 200 s mesh job reported `in_progress` two hundred times. Measured on the
same five-second job:

| Route | Updates | Of which per-node | What the first three said |
|---|---|---|---|
| Status polling | 5 | **0** | `in_progress`, `in_progress`, `in_progress` |
| **Websocket** | **28** | **28** | `node 6`, `step 1/25`, `step 2/25` |

`core/comfy_ws.py` is a hand-rolled RFC 6455 reader, because the client is stdlib-only (Bob-side
constraint 1) and the choice was that or no per-node progress. It is small because it only does the
client half of one direction. **The design decision that makes it safe to ship is that progress is
advisory: `wait()` still decides a job is finished from the jobs API**, so a socket that never
connects, drops, or is stolen by another process costs a progress bar and cannot cost a result. Four
tests cover the framing (fragmented messages, a masked pong reply, binary preview frames dropped, a
server that will not upgrade returning `None` rather than raising) and one covers the fallback.

One thing this forced: **`client_id` had to become per-process.** ComfyUI routes a job's events to the
socket whose `clientId` matches the prompt's `client_id`, and it keys those sockets by that id, so two
processes using `bob_blender_tools` would steal each other's progress. `CLIENT_ID` now carries the pid,
which is the collision that actually happens here (the MCP server and a running Blender driving one
ComfyUI).

### Eight things this plan had wrong, corrected here

1. **`asset_roots()` did not read `generated_root()`, it read the private global, and that broke the
   agent-facing route the moment the env fallback existed.** The generated pack has to be reachable
   from a process the addon never registered in -- which is now two of the three ways this code runs,
   the MCP server (no bpy) and the Blender the executor spawns (`--factory-startup`, extension imported
   but not enabled) -- so `generated_root()` gained a `$BOB_GENERATED` fallback. The resolver kept
   reading the global, so `comfy_texture_set` wrote a set into a pack `texture_set_dir` could not see
   and the apply step failed on a set that existed on disk. Found by the gate, one line.
2. **`import_generated` fabricated block-out proxies as a side effect, and it always had.** It called
   `proxies.ensure_collection`, which populates an empty `BOB_Assets_<Kind>` with procedural blobs, so
   importing one generated boulder into an empty scene put three proxies beside it and a scatter layer
   pointed at the pool instanced all four. The G6 render came back mostly proxies, which is how it was
   found; `proxies.collection` is the get-or-create half without the fabrication. This affected the
   PANEL too, since G3, and nobody noticed because an artist usually has proxies already.
3. **The macro mask needed no op, and the plan was right about that, but the tool needed one guard the
   plan did not name.** `comfy_heightmap` returns the `bake_params` fragment ready to paste, because an
   agent that has to know the key is `{"macro": {"path": ...}}` will get it wrong once and then stop
   using it. The gate scores the recipe hash rather than the tool's return value.
4. **Two ops needed a machine-readable result and `OpResult` had nowhere to put one.** `export_control`
   produces a path the NEXT call consumes, and `import_generated` produces the face count, UV overlap,
   height and origin the gate has to score. Both were about to be parsed out of an English `info`
   string. `OpResult.data` is the fix, and it is the one addition to the contract beyond the three ops.
5. **The `_apply_texture_set` the plan named is a UI function, not a core one.** It lives in
   `ui/shaders.py` and resolves context, so the op could not "go through the same path" without one
   extraction: `shading.set_texture_set` now holds the master-type dispatch and the panel keeps only
   its context resolution. Same shape as every other op in this suite, and it made the panel helper
   six lines shorter.
6. **Headless `build` cannot run any env-dependent op, and this was never written down.**
   `Scene.bbt_env` is a PropertyGroup the ADDON registers, and the headless runner imports `core` into
   a `--factory-startup` Blender without enabling the addon, so `set_env`, `apply_season` and
   `scene_preset` raise there while working perfectly over `build_live`. Not new to G6 and not caused
   by it; the G6 gate drops `set_env` and passes the sky its time explicitly, and the limitation is now
   in `docs/MCP.md` where an agent will meet it.
7. **The last open item on the full-scene handover's list was real, and it was one line of silence.**
   A scatter layer binds its emitter and its asset collection BY NAME, and `bpy.data.objects.get()` on
   a typo returns None, so the layer built, reported success and scattered nothing -- the worst shape a
   failure can take over MCP, where nobody is watching a viewport. `recipes.resolve_named` warns per
   unresolved name and `build_geonodes` surfaces it in `info` and in `data.warnings`. A warning rather
   than a raise, because an unset emitter is legitimate in some panel flows. The other seven items on
   that list were already closed, three of them without anyone recording it; the verification is now a
   table at the top of `docs/MCP-FULLSCENE-HANDOVER.md`.
8. **`ComfyUI-GeometryPack` is GPL-3.0 and `ComfyUI-Hy3D-Omni` has no licence file at all.** The
   licensing section claimed the primary model and the primary pack were both permissive, which is true
   and beside the point: TRELLIS2 auto-clones GeometryPack as a hard requirement, and that is GPL-3.0.
   Worse, the Omni pack ships no `LICENSE`, no `COPYING` and no licence field, so its terms are
   unstated, which means no licence is granted rather than that it is free. Two models are also
   non-commercial and the plan had not noticed either: **Depth Anything V2 Large (CC-BY-NC-4.0)**, used
   by W12e alone, and **4x-UltraSharp (CC-BY-NC-SA-4.0)**, used by W3 alone. So the entire
   non-commercial surface of this integration is two optional routes, which is worth knowing precisely
   rather than vaguely. All of it is in `docs/THIRD-PARTY-MODELS.md`, checked against the submodule SHAs
   and the models directory rather than against this document.

### What G6 deliberately did not build

No `comfy_paint_mesh` stylised route: it renders turntable views, which needs Blender, so the MCP tool
serves the PBR route (W9t) and the stylised one stays a panel action. That is the same split
`texture_chain()` already documents, and hiding it behind one tool would hide the fact that one route
needs Blender in the middle. No MCP tool for the block-out control chain beyond `export_control` plus
`comfy_mesh(control=...)`, because that IS the chain. No detection of `--disable-dynamic-vram`: it is
not visible over HTTP, and a warning that cannot tell a fixed server from a broken one is noise on
every start. No addon registration in the headless runner to make `set_env` work there: it would change
what `build` is, and `build_live` already does it. No progress UI change in the panel: the operators
already pass `on_progress` and now get better strings through it for free, which is the whole point of
having put the route behind one function.

---

## Review findings that changed this plan

Revision 1 had real defects. Each is listed with the fix, because the fixes are most of what is
new here.

**R1. Two phases of plumbing before anything visible.** Six tracks, nine workflows, nine phases,
and the first artist-visible result landed in phase three. That is how a plan dies.
*Fix:* G1 becomes a thin vertical spike (one prompt, one tileable image, one terrain layer,
hardcoded everything) and the plumbing gets generalised afterwards, against something that works.

**R2. Generated geometry has no UVs at all, and the whole material story assumed it did.** The
Hunyuan geometry stage outputs a raw watertight mesh. `materials.bobshade_material()` works by
routing an asset's *existing* maps into `S_SurfaceMaster`, so on a generated mesh it has nothing
to route, and track A's texture sets have no UV space to land in. Revision 1 hand-waved this.
*Fix:* UV creation is a mandatory Blender-owned stage, not an optional polish step, and the
pipeline order below is pinned rather than implied.

**R3. Retopology, UV, and baking were pointed at the wrong side of the fence.** Revision 1 had
`core/gen_assets.py` doing a Decimate pass, which on a triangle soup just yields a worse triangle
soup with no UVs and nothing bakeable. Meanwhile Blender 5.2 already has Quadriflow remesh,
Smart UV Project, and Cycles high-to-low baking, all deterministic, all license-free, all
in-process.
*Fix:* explicit division of labour. **ComfyUI generates. Blender retopologises, unwraps, bakes,
LODs, and packs.** This is also the answer to the retopo question: the ML retopo models are a
nice-to-have, not a dependency.

**R4. Paint-versus-retopo ordering was undefined, and one order destroys the textures.** If Bob
retopologises after painting, the painted UVs are gone. If Bob retopologises first and hands its
own UVs to the paint model, it is fighting a model that wants to run xatlas itself.
*Fix:* **paint the dense mesh and let the paint model own its UVs, then bake dense to low in
Blender.** A bake is a transfer, so the source layout is irrelevant.
**Superseded by R20.** Once `mesh_paint_views` became the primary route, Blender owns the
rasterising and projecting, so painting the *retopologised* mesh is correct and this fix applies
only to the Hunyuan paint wrapper, which R21 then deleted outright. Kept for the reasoning trail.

**R5. `POST /interrupt` was the wrong cancel primitive.** It kills whatever is currently running,
which on a shared or multi-agent queue is somebody else's job. This fork exposes a proper jobs
API that revision 1 missed: `GET /api/jobs` (filter by pending / in_progress / completed /
failed, sort, paginate), `GET /api/jobs/{job_id}`, and `POST /api/jobs/{job_id}/cancel`, which is
idempotent and uses an atomic `interrupt_if_running` so a cancel cannot land on a prompt that
started in the gap since the snapshot (`server.py:811-979`).
*Fix:* poll `/api/jobs/{id}`, cancel with `/api/jobs/{id}/cancel`. `/history` polling and
`/interrupt` are demoted to a fallback for a vanilla upstream server that lacks the jobs API,
detected once via `GET /features`.

**R6. Workflows that name a checkpoint by filename are not portable.** A shipped
`texture_set.json` hardcoding `some_model_v3.safetensors` fails on any machine that named the
file differently. Revision 1's templating list (prompt, seed, size) missed the one field that
actually breaks.
*Fix:* model nodes are templated too (`BOB_CKPT`, `BOB_VAE`, `BOB_LORA`, `BOB_3D_MODEL`), and Bob
resolves them from the `/object_info` combo enums plus a preference, so a workflow binds to
whatever the machine actually has.

**R7. An 8-bit PNG cannot carry a heightfield.** 256 height steps is visible terracing.
Revision 1 said "16-bit-ish", which is not a plan.
*Fix:* the diffusion output is treated strictly as a **low-frequency macro mask**, normalised and
smoothed, feeding the op stack's first input; the erosion stack generates all real detail. A
16-bit or EXR save node gets added only if a genuine height ever needs to survive the round trip.
**Measured at G5, and the fix was right for a reason R7 did not name.** The mask is a mask, so the
terracing never arrives: 256 levels, blurred at a fiftieth of the field width before erosion sees
them, leave the finished terrain with the same histogram concentration as a 16-bit path
(1.864 against 1.929) and the same landform statistics to three figures. Crushing the mask to 32
levels does not produce terracing either. What the 8-bit write does cost is determinism, not
precision: see [What G5 measured](#what-g5-measured). The save node stays deferred on evidence.

**R8. `/free` alone does not fix 16 GB.** ComfyUI's `/free` unloads its own models but leaves the
process and its allocator alive, and Blender holds VRAM too.
*Fix:* three layers. Launch the server with `--reserve-vram` when Bob starts it, keep every
workflow single-model and sequential, and offer a hard Stop Server for the case where a full
Cycles frame needs the whole card.

**R9. No iteration UX.** Texture and mesh generation is a twenty-attempt loop. A single Generate
button that overwrites the previous result is the wrong shape.
*Fix:* generate N variants into a staging folder, show them, Accept one into the pack. Reject is
a delete.

**R10. Mesh provenance was missing** while texture provenance was specified.
*Fix:* a sidecar JSON beside every generated GLB with workflow, model, seed, prompt, and the
license of the model that made it.

**R11. Manifest schema fork risk.** `biome_manifest()` is a normalising reader that already
handles v1 and v2. Revision 1 said "reinstates a loader", which invites a second reader that
drifts.
*Fix:* extend `_norm_entries()` with defaulted fields. One reader, still.

**R12. Title-based templating needs guarding.** Titles are not uniqueness-enforced, and
ComfyUI's API-format export drops nodes that do not feed an output, so a templated node wired to
nothing vanishes silently.
*Fix:* the preflight script validates that every `BOB_*` title is present and unique.

**R13. No time budgets, so no way to tell a slow path from a broken one.**
*Fix:* each phase gate carries a measured wall-clock target.

**R14. G0's sampler was underestimated.** Six terrain layers times five maps is up to thirty
image nodes in one graph, against EEVEE's sampler limits.
*Fix:* one shared sampler node group instantiated per layer, image nodes created only for enabled
layers, and a measured node count and EEVEE-compile check in the gate.

**R15. Threading details missing.** A job outliving a file load, or a glTF import off the main
thread, corrupts state.
*Fix:* the job registry clears on `load_post`; every `bpy` touch happens in the timer tick.

**R16. No naming or dedup rule**, so the second generated "rock" silently overwrites the first.
*Fix:* slugged unique names, never an implicit overwrite.

**R17. "Author the workflows from scratch" was wrong.** Ten official Hunyuan3D templates already
ship with this install, at
`venv/lib/python3.12/site-packages/comfyui_workflow_templates_json/templates/`, and six are
already copied to `user/default/workflows/3D/`. Four are local, six are cloud API. Authoring from
zero would mean reinventing recipes that Comfy-Org maintains and updates.
*Fix:* Bob's shipped graphs are **derived** from the official templates: convert to API format,
retitle the `BOB_*` nodes, record the upstream template name and version in the file. Upstream
changes then become a diff, not an archaeology exercise.

**R18. The retopology question had an answer in the repo, and it is not usable here.**
`TencentSmartTopologyNode` (quad or triangle retopology, $1.00), `TencentModelTo3DUVNode` (UV
unwrap, $0.20, under 30k faces), `Tencent3DTextureEditNode` (repaint an existing mesh, $0.60),
`Tencent3DPartNode` ($0.60), `TencentImageToModelNode` (image to PBR mesh, $0.20, quad option) are
all **cloud API nodes** (`comfy_api_nodes/nodes_hunyuan3d.py`, `is_api_node=True`, comfy.org auth),
not open weights.
*Outcome:* evaluated and **rejected** on the local-only decision. Recorded here so the option is
not rediscovered as new, and so the prices are on file if the constraint ever changes. Six of the
ten official templates are therefore irrelevant to this plan.

**R19. Local-only makes two stages load-bearing with no escape hatch.** With the cloud tier gone,
Blender is the *only* retopology and UV path, and the paint wrapper is the *only* Hunyuan route to
PBR texture. Revision 2 leaned on "the cloud can cover it" in both places.
*Fix, retopology:* explicitly tiered, and the tiering rests on an observation that removes most of
the pain. **Quad topology barely matters for static scatter props.** Quads matter for deformation,
subdivision, and hand editing. A background rock instanced 4,000 times is never deformed, so
Decimate-collapse plus Smart UV Project is genuinely adequate, faster, and more reliable than
Quadriflow. Quadriflow is reserved for promote-to-hero. So the free local tiers replace the paid
cloud tiers one-for-one.
*Fix, paint:* invert the priority. `mesh_paint_views` (Blender renders the views, ControlNet
restyles them, Blender projection-bakes them) becomes the **primary** paint route and the wrapper
becomes the optional upgrade, for the reason in R20.

**R20. The paint wrapper is the same algorithm with a worse rasteriser.** Hunyuan3D 2.1 paint is
multi-view diffusion plus projection, and its heavy dependency is a compiled CUDA rasteriser for
the render-and-project half. **Blender already rasterises and projects, better, in-process.** So
the Blender-side route is not a poor substitute for the wrapper; it is the same idea with the risky
dependency removed.
*Confirmed at G4, and the rasterising half cost 300 lines of numpy rather than a dependency.*
`core/gen_paint.py` interpolates a world position and normal per texel from the same triangle raster
`gen_assets.uv_counts` already used, then projects, weights by how face-on each view was, and rejects
what a texel-space z-buffer says was hidden. No ray casts (a million `scene.ray_cast` calls is not a
plan) and no compiled anything: **92.6% of a boulder's chart texels painted directly from eight
views**, 29 texels left for the hole fill.
*What it genuinely loses:* multi-view consistency. Hunyuan's paint model is trained to emit
consistent views; plain per-view SDXL img2img drifts and seams. Mitigations, in order: low denoise
(0.3 to 0.45) so the real render dominates, depth **and** normal ControlNet from Blender's true
passes locking geometry, one canonical front pass reused as an IPAdapter reference for the others,
normal-weighted projection blending in Blender, and, best, a multi-view-consistent adapter such as
MV-Adapter, which is SDXL-based and therefore keeps LoRA style control while adding the
consistency.
**Measured at G4, with the first four mitigations in and MV-Adapter not**: adjacent views disagree by
**22.3 to 26.5 of 255** over their shared texels and the front disagrees with its 180 degree opposite
by **30.1**. So the mitigations hold a palette (the drift is 1.25x the neighbour figure rather than
5x) and do not hold a texture, which is what the fifth mitigation is for. The number to beat is on
file. Metallic and roughness come from the same Bob-side numpy derivation track A uses,
which is honest for nature assets where metallic is zero anyway.
**Largely superseded by R21.** `Trellis2TextureMesh` does this locally with native PBR, so
`mesh_paint_views` demotes from primary route to style-control alternative, and the Hunyuan paint
wrapper is dropped entirely.

**R21. TRELLIS.2 changes the geometry and paint decision, and one reason dominates.** Microsoft
TRELLIS.2 (December 2025, **MIT**, 4B params) does image-to-3D with native PBR (base color,
roughness, metallic, opacity) straight to GLB, and `ComfyUI-TRELLIS2` wraps it with fp8 support
(added 2026-02-26) plus a `low_vram` toggle, so 16 GB is the recommended tier for 1024³ rather than
a blocker (the stock repo's 24 GB figure is the unoptimised path; GGUF Q4 and Q8 run the full
pipeline in roughly 6 to 9 GB).

The decisive reason is not PBR and not the license. It is that TRELLIS.2's **O-Voxel representation
handles open surfaces and non-manifold geometry, explicitly "clothing, leaves"**. Hunyuan's
watertight SDF-voxel output cannot represent a leaf at all. For a suite whose whole subject is
biomes and vegetation, that is the difference between the tool working and not working, and it
would have surfaced at G3 as an unexplained wall.

*What it collapses:* PBR is native, so the compiled-rasteriser Hunyuan paint wrapper (the plan's one
serious install risk) is dropped. `Trellis2TextureMesh` textures an **existing** mesh, which is
track B on the grey block-out proxies, locally and MIT, so the hand-rolled `mesh_paint_views` route
demotes to a style-control alternative and MV-Adapter stops being necessary. `Trellis2Simplify` and
`Trellis2UVUnwrap` overlap pipeline steps 3 and 4, so those become an A/B against Blender rather
than an assumption.

*What Hunyuan keeps, and it is not nothing:* zero install cost (native in ComfyUI core, no
submodule, no compiled dependency), **multi-view conditioning** via
`Hunyuan3Dv2ConditioningMultiView`, which is how Blender-rendered
block-out views become consistent by construction, and **Omni** point-cloud / voxel / bounding-box
control, which has no TRELLIS equivalent. Benchmarks favouring Hunyuan on structured CAD geometry
(IoU 0.692 versus 0.202 on ABC Easy) are TRELLIS **1**, on hard-surface CAD, so they are weak
evidence for nature assets.

*The one real unknown:* `ComfyUI-TRELLIS2` (541 stars, MIT, last push 2026-06-07, 26 open issues)
opens its own README with a warning that its `comfy-env` plus pixi one-click install is
experimental. Deps are `comfy-env`, `comfy-3d-viewers`, `comfy-sparse-attn`, `trimesh[easy]`, all
pinned pip packages, so probably prebuilt wheels rather than compile-from-source, but **unverified
against this machine's Blackwell cu130 venv**. `visualbruno/ComfyUI-Trellis2` is the fallback pack.
*Fix:* a G0.5 spike that installs it and generates one textured mesh, before any Bob code depends
on it.

---

## Model choice: TRELLIS.2 primary, Hunyuan3D for what only it does

Both, deliberately, split by capability rather than hedged. The division:

| Job | Model | Why |
|---|---|---|
| Geometry with **open surfaces** (foliage, leaves, cloth, thin shells) | **TRELLIS.2** | O-Voxel represents non-watertight geometry. Hunyuan structurally cannot. Decisive for a vegetation suite. |
| **PBR texture** on generated or existing meshes | **TRELLIS.2** | Native base color, roughness, metallic, opacity. `Trellis2TextureMesh` also textures a mesh you already have, which is track B on block-out proxies. |
| Simplify and UV unwrap | **TRELLIS.2 or Blender**, A/B at G3 | `Trellis2Simplify` and `Trellis2UVUnwrap` exist; so do Decimate, Quadriflow, Smart UV Project. Measure, do not assume. |
| **Multi-view conditioning** | **ANSWERED at G4: both, split by cost** | `Trellis2MultiViewImageToShape` (W6t) is the accuracy tier at back-half IoU **0.2637** in 120.4 s; `Hunyuan3Dv2ConditioningMultiView` (W6) is the preview tier at **0.2140** in **24.4 s**, 5x faster with 41% fewer faces. Either beats a single view six-fold on the half it cannot see (0.0439). Revision 5's claim that only Hunyuan could do it was wrong, and the comparison moving to G7 was too cautious: this is a track C route now. |
| **Control from a block-out** (point cloud, voxels, bbox) | **ANSWERED at G4c: Hunyuan3D Omni, and it beat the alternative** | No TRELLIS equivalent, and W7 is now measured against the route that came closest: footprint IoU **0.908** mean against W6t's 0.675 on the same three block-outs, 4.6x faster, 2 GB less VRAM, proportions held to 2% against 23%. The one idea in this plan that turns an existing suite strength into a generation input, and the only route whose OUTPUT ORIENTATION is part of the answer. |
| Zero-install smoke test | **Hunyuan3D native** | Already in ComfyUI core. Proves the Bob-side plumbing before any submodule exists. |
| Watertight hard-surface props | **Hunyuan3D**, weakly | The CAD benchmarks favouring it are TRELLIS 1, not 2. Treat as unproven, revisit at G7. |

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

### Challengers, for the G7 A/B slot

W8 exists so swapping the geometry model is a config change, not a rewrite. The slot's first
occupant is now **Hunyuan 2.1 itself**, benchmarked against TRELLIS.2 on ten fixed prompts that
include at least three foliage cases, because that is where the two differ structurally rather
than by degree. Further candidates if the verdict is close:

- **Direct3D-S2** for sharper geometry (high-resolution SDF with spatial sparse attention).
- **Hi3DGen** for normal-bridged detail, notably crisp on rock and bark.
- **TripoSG**, **PartCrafter**, **PartPacker** for part-level and alternative flow models.
- **TEXGen** as a texture-stage alternative.

Plan position: **TRELLIS.2 primary, Hunyuan for multi-view and Omni control, one grid at G7,
measured, decided once.**

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
(`comfy_maps.wrap_pad` / `crop_wrap_blend`). Numbers in [What G2 shipped](#what-g2-shipped).

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

**W8 `mesh_geom_alt.json`** the A/B slot: same inputs, same output contract, challenger model
inside, so the G8 benchmark is a config change rather than a rewrite.

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
won; numbers and the verdict in [What G3b measured](#what-g3b-measured).

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
on speed**: see [What G4 measured](#what-g4-measured).

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
twelve corrections are in [What G5 measured](#what-g5-measured).

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
| **`ComfyUI-TRELLIS2`** (PozzettiAndrea) | **The one required pack**: W5t, W9t, W9b, W9c. MIT, pinned at `9b878516`. | **Retired by G0.5.** Installed and running; 24 nodes. `comfy-env` is mandatory, not optional, but it installs prebuilt CUDA wheels rather than compiling. The one gotcha, `comfy-kitchen` missing inside the isolated pixi env, is written up in [What G0.5 measured](#what-g05-measured) and in the fork's `FORK_README.md`. `visualbruno/ComfyUI-Trellis2` was never needed. |
| **`ComfyUI-GeometryPack`** (PozzettiAndrea) | **Also required**, declared in TRELLIS2's `node_reqs`. 125 mesh nodes: load/save, decimate, remesh, the `GeomPackUV_*` unwrapper family, preview. MIT, pinned at `c67199d`. | Low. Auto-cloned by comfy-env; pinned explicitly so a fresh clone is reproducible. Its `GeomPackLoadMesh` COMBO caches its directory listing, so Bob must use `GeomPackLoadMeshPath`. |
| **`ComfyUI-seamless-tiling`** (spinagon) | **Required for W1 and W3.** `SeamlessTile` (UNet) and `MakeCircularVAE` (VAE decoder). **GPL-3.0**, pinned `9225ed5`. | **Retired by G1.** Installed and working; pure Python, no dependencies, nothing to compile. Two things to know: **do not use its `CircularVAEDecode`**, which segfaults the server on the second decode of a session, and GPL-3.0 rather than MIT (harmless, ComfyUI is GPL and this extension is `GPL-3.0-or-later`, and Bob ships no node code). The WAS `Image Seamless Texture` alternative was measured and rejected: D4. |
| **`ComfyUI-Hy3D-Omni`** (Rizzlord, **no license file**) | **W7**, the block-out control route. Five nodes: `Hy3DOmniLoadPipeline` plus a Point / Voxel / BBox / Pose generator. Pinned at `e513cd08`. | **Retired by G4c, and it was the real risk in this plan.** It is the ONLY ComfyUI wrapper for Omni that exists (3 stars, 0 forks, no license, last push 2025-10-03, and the better-advertised `PozzettiAndrea/ComfyUI-HunyuanX` is a 404), and it ships with the control signal broken: a vendored rename of `OmniEncoder.linear` to `self.liner` makes the checkpoint's three control-projection tensors load as MISSING under `strict=False`, so generation ignores the control and says nothing. Measured 0.010 voxel IoU before the fix and 0.53 after. `tools/scripts/comfy_omni_fix.py` is the fix; the whole write-up is in [What G4c measured](#what-g4c-measured) and in the fork's `FORK_README.md`. |
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
   in [What G2 shipped](#what-g2-shipped): the resolver unions every `textures/` directory under a
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
                      mesh_paint_views, mesh_geom_mv, mesh_geom_mv_trellis and heightmap_macro
                      shipped
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

Since G3b it also owns the asset ROUTE, in one place: `asset_chain(route)` returns
`generate_asset_oneshot` (W4 then W9b, the default) or `generate_asset_chain` (W4, W5t, W9c, W9t),
and `finish_passes(staged)` maps whatever either staged onto `finish_asset`'s `simplify_pass` and
`texture_pass`. `bind_process()` is the shared `Trellis2ProcessMesh` binding, which cannot go
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
  server Bob started; see [What G2 shipped](#what-g2-shipped).
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
[What G6 measured](#what-g6-measured) and D14.

---

## Phases

| Phase | Content | Gate, with a time target |
|---|---|---|
| **G0** | **DONE.** Texture-set sampler in Shaders (the BobShaders S3 work) plus the generated pack root. What actually shipped, and the five things this plan had wrong about the master sockets, are in [What G0 shipped](#what-g0-shipped). | **Passed.** The `grass` set renders on a terrain layer in EEVEE (luminance range 0.6118) and Cycles (0.6157); 11 nodes / 4 image textures for one textured layer, 17 / 8 for two. Proved by `tools/scripts/headless_texset.py`, not by inspection. |
| **G0.5** | **DONE.** `ComfyUI-TRELLIS2` and its required `ComfyUI-GeometryPack` installed and pinned as submodules; `TRELLIS.2-4B` pulled (~15 GB, bf16, auto); the three bundled workflows run. Full numbers, the install failure and its fix, and seven corrections to this plan's TRELLIS.2 section are in [What G0.5 measured](#what-g05-measured). | **Passed, with one real defect found.** Textured GLB out of `geometry_texture` (462,140 tris, two 2048² PBR maps, 72 s). Bob block-out proxy textured by `standalone_texturing` (10 s) **but only after being unit-normalised**: at metre scale it returns a silently black albedo, which makes normalise-then-rescale a mandatory `gen_assets` step. 1024³ fits 16 GB one-shot with room to spare (peak 5.7 GB of 16), so no cascade. Foliage confirmed: 83,292 boundary edges, thinnest/longest axis 0.059, non-watertight. |
| **G1** | **DONE.** Vertical spike: W1, the stdlib client, one blocking job, one written set, applied to one terrain layer. What shipped, the five-way seam comparison, the `CircularVAEDecode` segfault, and ten corrections to this plan are in [What G1 shipped](#what-g1-shipped). | **Passed, with room.** **7.6 s** prompt to a rendered terrain layer against a 60 s gate (generate 5.0, derive 0.67, write 0.11, apply 1.33, EEVEE 0.5), cold server 7.3 s. Set carries basecolor, roughness and height through `assets.texture_set_maps()`; EEVEE luminance range 0.6131. Seam ratio **0.83** against 3.86 untreated, i.e. the wrap is as continuous as an arbitrary interior line. Proved by `tools/scripts/headless_comfy_texset.py`, which skips cleanly with no server. **Go.** |
| **G2** | **DONE.** Generalise: `comfy_jobs`, preflight, variants and Accept, W2 and W3, real `comfy_maps`, Advanced panel. What shipped, the measurements, and six corrections to this plan are in [What G2 shipped](#what-g2-shipped). | **Passed.** Ten sets generated and accepted in one session, mean **5.55 to 5.59 s**, drift **-0.15 to -0.19 s** (no leak). Longest main-thread block **16.5 ms** against the blocking path's 5563 ms, measured with a stand-in event loop. Preflight catches all five failure classes, one test each. Seam after a W3 upres **1.03 / 1.10** from 1.05 before. Roughness contrast **+55% std** (25.8 to 40.0) with the mean off the ceiling (187.8 to 156.8). 33 tests in `test_comfy.py`, suite 139. |
| **G3** | **DONE.** Tracks C and B together: W4, **W5t**, **W9t**, **W9c**, plus W5 Hunyuan as the plumbing smoke test. `core/gen_assets.py` (steps 6 to 8), the generated manifest through `_norm_entries`, and the Scatter `Generate Asset`. What shipped, the measurements, and eleven corrections are in [What G3 shipped](#what-g3-shipped). | **Passed, with two parts named as partial.** Prompt to a scattered, correctly scaled, UV'd, PBR-textured, BobShader-converted prop in **40 to 203 s** against a 300 s gate, on five assets. Face count 3,155 to 3,992 against a 4,000 budget, UV overlap at most 0.00002, baked normal std 0.24 to 0.28, bbox height exact, origin **0.0 m** above the base, a three-step LOD chain, `master_type()` = surface. The black-albedo trap is a test: round trip error **6.0e-08 m**, texture std 0.39 against a 0.02 floor. The steps 3 and 4 A/B is decided and written into the pinned pipeline: TRELLIS.2 **5 of 5** inside budget in 5.95 s, Blender Decimate **1 of 5** in 131.95 s, Quadriflow refused every mesh. **Partial:** open surfaces needed a graph change to appear at all (`remesh: off`), and the opacity channel does not reach the finished material. |
| **G3b** | **DONE.** W9b one-shot `geometry_texture` against W5t plus W9c plus W9t staged, ten prompts, one shared W4 subject each. Plus the opacity channel, which was G3's named partial. What shipped, the measurements, and five corrections are in [What G3b measured](#what-g3b-measured). | **Passed, and it changed the default.** W9b **fits 16 GB with 61% of the card free** (peak 6,276 MiB summed across the three ComfyUI processes, 4,508 MiB of it the graph's own rise) against the staged route's 8,586 MiB peak. Wall clock is a wash (**593.1 s against 584.1 s** for all ten). Both routes 10/10 inside the 4,000 budget with UV overlap at most 0.0001. W9b returns a far cleaner mesh (**10 to 662 boundary edges against 1,467 to 3,050**) while preserving foliage openness (4 of 4 open on both, thin ratios within 1.5%), and it **cannot hit the black-albedo trap** that returned one fully black W9t texture in ten. The dense mesh it gives up bought **no measurable normal detail** at this budget. Opacity: present in both routes and declared away as `alphaMode: OPAQUE`; wired behind a plausibility rule, firing on a generated leaf at in-chart alpha mean **0.9806**, 1.81% below 0.98, and proved through the glTF round trip. |
| **G4** | **DONE.** Tracks D then B-stylised, in that order because they share a graph: **W12** and **W12e** stylise (render plus true depth and normal export via a material override, two-stage ControlNet img2img, the Advanced-panel button), then **W9** grown out of it as the style-control paint route with `core/gen_views.py` and `core/gen_paint.py` behind it. Plus **W6** and **W6t** multi-view geometry. What shipped, the measurements, and nine corrections are in [What G4 measured](#what-g4-measured). | **Passed, with one claim disproved and named as such.** A Bob render comes back stylised at **silhouette IoU 0.9980** (against 0.9967 estimated) in **7.7 s**, peaking at **14,194 MiB** of 16,303 summed over the ComfyUI family. **The real-passes claim failed on quality:** the differences against Depth Anything V2 plus NormalBAE are smaller than the estimator's own error on the source frame (r 0.7957, MAE 0.1224), and the honest case for keeping the export is **2.5 s per frame** plus the fact that W9 needs the same three files anyway. A mesh comes back stylised with LoRA control wired (7.1 of 255 at denoise 0.75, 1.7 at paint settings): **92.6%** of chart texels painted from 8 views, adjacent-view seam **22.3 to 26.5 of 255**, front-to-back drift **30.1**. Multi-view beats single-view on the back-facing test by six-fold: back-half IoU **0.2637** (W6t) and **0.2140** (W6) against **0.0439**, against a 0.7110 self-agreement ceiling; W6 is 5x faster at 24.4 s. The panel press costs **0.63 to 1.00 s** of main thread (the render) and the longest tick during the job is **0.14 ms**. |
| **G4c** | **DONE.** Omni: model set 3, **W7**, `export_control`, the `Asset from Block-out` entry, and the orientation convention pinned per exporter. What shipped, the measurements, and ten corrections are in [What G4c measured](#what-g4c-measured). | **Passed, and it changed a decision the plan had already made.** A block-out proxy conditions generation and the result keeps its shape where it landed, scored with NO rotation search: **footprint IoU 0.8136 to 0.9787** against per-block-out ceilings of 0.8403 to 0.9920, proportions held to **2%**, and **0.8100 on the FINISHED asset** after simplify, texture, bake, scale, LODs and BobShade. **W7 beat the W6t multi-view baseline 3 of 3 on every measure at once** (mean footprint IoU **0.9079 against 0.6748**, **35.8 s against 164.9 s**, 2 GB less VRAM), and it still wins after W6t is allowed its best axis map, so the plan's fallback is not the honest answer. The wrapper is the least maintained dependency here and **shipped with the control signal silently random** (a vendored `linear` to `liner` rename, 0.010 voxel IoU before the fix and 0.53 after); `tools/scripts/comfy_omni_fix.py` is the fix. **One bug found on another route:** the exporter's turns ACCUMULATE, so the staged chain's high-to-low bake has been reading across a 90 or 180 degree rotation since G3, which puts a G3b conclusion back in question. Proved by `tools/scripts/headless_comfy_g4c.py`, four parts, no failures. |
| **G5** | **DONE.** Track E: **W13**, `comfy_maps.macro_field`, the `macro` op and `params.with_macro`, and `Generate Base` in the Terrain panel. What shipped, the measurements, and twelve corrections are in [What G5 measured](#what-g5-measured). | **Passed, and it answered R7 in the negative.** A prompted silhouette survives an erosion pass at **band-limited correlation 0.906 to 0.923** against a no-mask null of 0.078 to 0.208, on three prompts including the isolated massif erosion was expected to fight. It still looks intentional rather than shaped noise, as numbers: the erosion supplies **2.89 to 3.04 m** of fine relief against a mask-only baseline's **0.28 to 0.31 m**, the median slope is **42.1 to 42.8 deg** beside the no-mask bake's own 44.7, and the mask explains only 11 to 16% of the band above its own cutoff. **R7's terracing does not occur at 8 bits, and does not occur at 5 either** (histogram concentration 1.86 and 1.91 against 16-bit's 1.93), so the 16-bit save node is deferred on evidence; what the 8-bit write costs is determinism, 0.80 m rms against a reseed's 9.28 m. **About 12 s** prompt to a built terrain, **0.3 ms** of main thread, peak **7,444 to 9,844 MiB**, and it is the one route here that shares a card with Omni. Proved by `tools/scripts/headless_comfy_g5.py`, five parts, no failures. |
| **G6** | **DONE.** The six `comfy_*` MCP tools, the single batched contract change (`apply_texture_set`, `import_generated`, `export_control`, plus `OpResult.data`), the `macro` key on `bake_heightfield`, websocket progress, `THIRD-PARTY-MODELS.md`, and the whole shipped-gate suite as one command. What shipped, the measurements, and eight corrections are in [What G6 measured](#what-g6-measured). | **Passed, and it found a crash that was not Bob's.** An agent goes prompt to a scattered, scaled, UV'd, PBR-textured, BobShaded asset in **102.5 s** with no GUI (generation 97.7 s, Blender 4.8 s) and prompt to a rendered shaded terrain in **24.1 s** (set 6.0, mask 3.9, bake 7.5, build and render 6.7), both through the real MCP tools and one op list, with every asset property read out of the op's own result: faces **3,672 to 3,930** against 4,000, UV overlap at most **0.0000017**, height **1.8 m** exact, origin **0.0 m** above the base, `master_type` surface. Websocket progress ships: **28 per-node updates against 5 status strings** on the same job, with termination still decided by the jobs API so a dead socket cannot cost a result. Every new op is rejected with a readable sentence when given bad params, proved rather than asserted (four contract rejections and two handler rejections). **The finding: this fork's `comfy-aimdo` dynamic-VRAM staging segfaults the server on the second copied-model decode of a session**, which is every tiling graph Bob ships. Five candidates were measured and the shipped fix keeps the staging feature: circular padding applied IN PLACE plus a lazy `ensure_untiled`, giving **ten texture sets in one session** (seam 0.83 to 1.18, drift -0.01 s) with W4 at seam 8.466 and W13's open route at 10.086, i.e. verified untiled. Proved by `tools/scripts/headless_comfy_g6.py`, four parts, 41 checks, no failures. |
| **G7** | The geometry A/B: W8, ten fixed prompts including **at least three foliage cases**, TRELLIS.2 versus Hunyuan 2.1, one grid, decided once. Further challengers (Direct3D-S2, Hi3DGen) only if the verdict is close. | A written verdict on which model is primary per asset class, not one global winner. |
| **G8** | Optional: track F sky dome (W14), part-level variation via W11, batch generation, ML retopo swap-in if open weights land. | |

## Testing

A Blender 5.2 binary is available in the CLI environment (`blender-headless-testing`), so these
are measurable rather than `py_compile` theatre.

**Start here: `tools/scripts/headless_comfy_all.py` runs every shipped gate as ONE command**, one
summary line each with the wall clock, the whole-card peak VRAM and the number of checks, and it exits
non-zero if any gate failed. `--fast` passes each gate its own cheap flags (fewer prompts, cached
generations, no slow A/B baseline), which is what a regression check should use; the full run is
GPU-hours and is what a phase verdict needs. `--list` prints the gates and their rough cost, `--gate`
selects a subset, `--verbose` echoes a gate's own output. It re-implements no check: each gate keeps
its own reachability gate and its own exit code, so this is a scheduler and a table.

It earned its keep on the first run. **The G2 gate had been crashing since G4** — its stand-in
`UILayout` had no `column`, which the stylise block G4 added to the panel body calls — and it looked
clean the whole time because Blender exits 0 after a script traceback. So the runner reads each gate's
VERDICT LINE and reports "no verdict printed, so the gate did not finish" when there is none, rather
than trusting an exit code. Nobody had re-run G2 in two phases, which is exactly the failure mode the
one-command suite exists to prevent.

- `core/comfy.py` against a stdlib `http.server` fake: queue, jobs-API status shapes, cancel
  idempotency, `/view` bytes, multipart upload, preflight against a canned `/object_info`.
  **Shipped at G1** as `tools/tests/test_comfy.py` (13 tests, no server contacted): the fake covers
  `/system_stats`, `/prompt`, the pending -> in_progress -> completed poll, cancel, `/view` and a
  canned `/object_info` combo, and asserts the client polls the jobs API rather than `/history`.
  It also asserts W1 itself loads, has unique `BOB_*` titles, names no cloud node, records its
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
  and after a W3 upres, roughness contrast G1 against G2 on the same image, and the `load_post`
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
  linearity against the analytic answer, then W12 against W12e at two denoise levels with silhouette
  IoU, edge IoU, and Depth Anything V2's reading of each output against Blender's true depth after an
  affine alignment. **B**: an eight-view turntable, W9 with and without a LoRA, the projection bake,
  and the per-pair overlap MAD plus the front-to-back drift. **C**: a purpose-built ground truth
  whose back cannot be inferred from its front, through W5t, W6t and W6, scored by surface-voxel IoU
  and Chamfer best-over-rotation, whole and back-half, against a self-agreement ceiling. **D**: the
  Advanced-panel operator through the real job queue, with the main-thread tick measured. Every
  generated file caches WITH its timing and VRAM beside it, so a rerun reports what the generating run
  measured rather than a table of zeros. Reachability-gated: with no server it prints SKIP for every
  generation half and exits 0.
- `tools/scripts/headless_comfy_g4c.py`, the G4c gate, in four parts (`--part a,b,c,d`).
  **A**: `export_control`'s round trip, then the ORIENTATION convention, measured over all 24
  axis-aligned rotations on an asymmetric block-out so a mirror cannot pass for a rotation, and the
  assertion that `gen_assets.CONTROL_RETURN_TURN` undoes the exporter's turn. It needs a server but no
  model, so it costs a second. **B**: W7 against the W6t baseline on three block-outs, two of them the
  shipped `core.proxies`, scored WITHOUT a rotation search: voxel IoU, Chamfer, the XY-projected
  footprint IoU, and the bbox aspect ratio, each against that block-out's own self-agreement ceiling.
  **C**: one block-out all the way through W7, W9c, W9t and steps 6 to 8, against the G3 asset checks
  it inherits, plus the footprint measured again on the FINISHED asset. **D**: whether Omni can be
  resident alongside SDXL, which on a 16.3 GB card is a real question rather than a formality.
  `--no-baseline` drops W6t, the slow half. Reachability-gated twice over: no server, or no Omni pack
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
- `tools/scripts/headless_comfy_g3b.py`, the G3b gate: ten prompts through both routes off ONE
  shared W4 subject each, with wall clock, per-process VRAM sampled from a thread at the queue
  moment and at the peak, face count, boundary edges after a weld, UV overlap, chart coverage and
  in-chart albedo and alpha statistics; then four of them through steps 6 to 8 on both routes with
  the baked normal's std AND its high-frequency content; then Blender's Decimate floor on the dense
  meshes; then the opacity channel, including one forced wiring that is followed through the glTF
  export and the re-import a scatter layer makes. It caches generated meshes, timings and VRAM under
  `_generated/comfy_g3b_check/gen/`, so `--no-gen` re-measures the whole thing in about four minutes
  and `--fresh` regenerates. Reachability-gated for the generation half.

## Decisions remaining

- **D6 Geometry ambition. Answered by measurement at G3, in the direction the plan did not
  expect: scatter-grade only, because there is no local hero tier to have.** Quadriflow, the whole
  basis of R19's hero tier, refuses every generated mesh (non-manifold, inconsistent normals) on
  all five test assets, and Blender Decimate cannot reach a 4,000-face budget on them either. What
  actually delivers the budget is `Trellis2Simplify`, which is triangles. So the remaining question
  is not "scatter-grade or hero" but "is a hero path worth a MANUAL retopo step", and that is a
  workflow question for after G7, not a tiering decision that needed answering before G3 hardened.
  `hero=True` survives as a bake-resolution and texture-resolution switch, honestly labelled.
- **D2 Further challengers for G7.** Direct3D-S2 for sharpness or Hi3DGen for normal-bridged
  detail? Only if TRELLIS.2 versus Hunyuan lands close. **G4 measured the multi-view half of that
  comparison and it did not land close in either direction**: TRELLIS.2 is 23% better on back-half
  IoU and Hunyuan is 5x faster, which is a split by capability rather than a tie. So D2 stays open for
  the single-view geometry grid G7 owns, and the case for a third model is weaker than it was.
- **D10 MV-Adapter, now that there is a number to beat.** The paint route's cross-view drift is
  measured (adjacent-view seam 24.1 of 255, front-to-back 30.1), which is what makes MV-Adapter worth
  a phase: it is SDXL-based, so it keeps the LoRA style control, and its whole claim is the
  consistency those two figures quantify. Not needed by G4c or G5. Open.
- **D11 Re-measure the dense mesh, because G3b measured it through a misaligned bake.** G4c found that
  every `Trellis2ExportTrimesh` write turns the subject and the turns accumulate, so the staged
  route's `bake_high_to_low` was transferring from a cage rotated 90 or 180 degrees away from its
  target on every asset since G3. `comfy.stage_exports` fixes it. What that puts in question is one
  sentence of G3b's verdict, "the dense mesh bought no measurable normal detail at a 4,000-face
  budget", and re-running that comparison is cheap now that the fix is in. It does not threaten the
  one-shot default, which won on VRAM, boundary edges and the black-albedo trap. Open, and it belongs
  wherever a higher-budget or hero path is next considered rather than in a phase of its own.
- **D13 The terrain engine's slope-area gradient has the wrong sign, and G5 found it by accident.**
  Building the G5 landform statistic turned up something that is not about the mask at all: a no-mask
  `alpine` bake's log-log slope-area gradient is **+0.322**, i.e. slope RISES with upstream drainage
  area, with a strong fit (binned medians, r 0.86 to 0.89 over 30 to 3,000 upstream cells, D8
  downslope gradient, a 32nd-of-width border margin dropped so the outlet boundary cannot dominate).
  An equilibrium fluvial landscape has a NEGATIVE gradient of roughly 0.3 to 0.6 (Flint's law). A
  finite-iteration stream-power stack with no uplift term is not an equilibrium landscape, so this may
  be exactly what the model should do and the answer may be "nothing to fix, write it down". But it is
  worth an afternoon, because if it is not expected then every mountain preset's valley profile is
  wrong in a way no visual check has caught, and the fix would be an uplift term rather than a knob.
  Not G5's to resolve: the gate scores a mask against the engine's own output, and the masked bakes
  land at +0.414 to +0.432 beside the null's +0.322 while a mask with no erosion gives -0.143 to
  -0.207, so the statistic discriminates cleanly whatever its sign means. Open, cheap, and it belongs
  with the terrain engine rather than with this integration.
- **D14 This fork's dynamic-VRAM staging segfaults on a copied model, and Bob works around it by not
  copying one.** Found at G6 and not caused by Bob: `comfy_aimdo`'s host buffer is released inside
  `model_patcher.partially_load` and its destructor faults, so the SECOND copied-VAE decode of a
  session kills the whole server. Bob's four tiling graphs were the only thing making a copy, so they
  now ask for circular padding IN PLACE (`comfy.TILING_COPY_MODE`) and undo it before anything that
  must not wrap (`comfy.ensure_untiled`). Measured: ten sets in one session, no crash, and the routes
  that must not wrap verified untiled. Dynamic VRAM stays ON, which matters because it is what lets a
  16 GB card hold a model larger than its free VRAM. **What is still open:** the mutation is
  process-global on the server, so a second client generating concurrently could see a padded model
  inside that window -- `--disable-dynamic-vram` is the documented fallback for anyone who hits it. Fix
  belongs upstream in `comfy_aimdo`. Re-test on each fork update: if the destructor is fixed, revert
  `TILING_COPY_MODE` to `"Make a copy"`, and `ensure_untiled` and the whole concern can go.
- **D12 The other three Omni control modes.** `Hy3DOmniVoxelGenerate`, `…BBoxGenerate` and
  `…PoseGenerate` are installed and unmeasured. The bounding-box one is the interesting one for a
  suite like this: Bob knows every proxy's bbox for free, it is the cheapest control there is, and it
  would answer whether the footprint result at G4c needed 8,192 sampled points or just eight corners.
  Open, cheap, and a good half-day.

### Answered

- **D1 Image model family. Answered at G1: SDXL, with `RealVisXL_V5.0_fp16` as the finetune**
  (6.9 GB, OpenRAIL++, `SG161222/RealVisXL_V5.0`). SDXL base itself was not downloaded; a photoreal
  finetune is what track A wants and it carries its own VAE.

  The decisive argument is **structural, not taste**: seamless tiling by circular padding works by
  switching every `Conv2d`'s padding mode, and that only exists in a **convolutional UNet**. FLUX
  and Qwen-Image are DiTs, whose bodies are patch embedding plus attention, so the trick reaches
  only their VAE and the latent content still carries a seam. Since track A's whole product is a
  tileable texture and D4 landed on circular padding, a DiT would mean giving up the one technique
  that measurably works (ratio 0.83 against the WAS blend's 1.17, with the blend costing 15% of
  interior contrast). Supporting reasons, in order: 25 steps at 1024 square in 5.0 s is fast enough
  to iterate on; the SDXL ControlNet set on disk is already what W12 and the W9 style route need;
  the material and tileable LoRA ecosystem is SDXL's; and it was the smallest download of the three
  by a wide margin.

  Qwen was the tempting option because its encoder and VAE are already local, and that reasoning
  was wrong for a second reason as well: the Qwen model on disk is Qwen-Image-**Edit**, which needs
  a reference image, so a text-to-image route would still mean another ~14 GB. FLUX.1-schnell would
  have meant ~17 GB (unet fp8 plus `t5xxl` plus `clip_l`, neither text encoder present).

  Revisit only if prompt adherence turns out to be the binding constraint on texture quality, in
  which case the honest move is a Qwen or FLUX **first pass** upscaled and re-tiled through SDXL,
  not a swap.
- **D4 Seamless tiling. Answered at G1: circular padding, and both halves of it.** The
  `ComfyUI-seamless-tiling` submodule (spinagon, **GPL-3.0**, pinned `9225ed5`, pure Python, no
  dependencies) supplies `SeamlessTile` for the UNet and `MakeCircularVAE` for the VAE decoder. The
  WAS offset blend was measured and **rejected**: it is a wide feathered band, not a line, so it
  costs 15% of interior local contrast, and it is not needed once padding is circular. Numbers and
  the `CircularVAEDecode` segfault that shaped the graph are in
  [What G1 shipped](#what-g1-shipped).
- **D5 G0 scope. Answered: landed in this track.** The sampler shipped as G0 before any ComfyUI
  code, which is what let G1 be a thin spike instead of a spike plus a sampler.
- **D9 Two models or one. Answered: both, split by capability**, though G0.5 weakened one leg of
  it: `Trellis2MultiViewImageToShape` exists, so multi-view is no longer a Hunyuan-only capability
  and that comparison moves to the G7 A/B. Omni block-out control and zero-install smoke testing
  are what Hunyuan still uniquely provides. TRELLIS.2 primary for open surfaces
  and PBR; Hunyuan for multi-view conditioning, Omni block-out control, and the zero-install smoke
  test. See the division table under [Model choice](#model-choice-trellis2-primary-hunyuan3d-for-what-only-it-does).
- **D8 Paint route priority. Answered by R21.** `Trellis2TextureMesh` is the route.
  `mesh_paint_views` stays as the style-control alternative, and the Hunyuan paint wrapper, which was
  the plan's only compile-from-source risk, is deleted.

- **D3 Retopology route. Answered.** Official retopology is cloud-only, so Blender does it:
  Decimate-collapse for scatter-grade, Quadriflow for hero, tiered by intent (R19). An open ML
  retopologiser (MeshAnything V2, BPT, DeepMesh, EdgeRunner class) stays a G8 slot, labelled
  speculative: those models cap out around a few thousand faces and none has a maintained ComfyUI
  pack.
- **D7 Cloud tier. Answered: no.** Fully local, no API calls, enforced by the `api_node` preflight
  check rather than by intent. The trade is recorded in the swap table above so it is a known cost,
  not a surprise.
