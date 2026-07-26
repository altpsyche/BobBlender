# Third-party models and node packs

Every model and every ComfyUI node pack the shipped workflows touch, its licence, and where the
obligation falls. Read this before downloading 20 GB of weights, and read it again before shipping
anything commercially: two of the entries below are not permissive, and one is explicitly
non-commercial.

**BobBlenderTools ships no weights and no node code.** The extension zip contains workflows
(`assets/workflows/*.json`) and Python; every model here is downloaded by the artist, into their own
ComfyUI install, from the source named below. That is what keeps every model licence out of Bob's
distribution question entirely (docs/COMFYUI.md, Licensing obligations, item 1).

**Output licensing follows the model that produced it.** A texture set's `SOURCE.txt` and a generated
mesh's sidecar JSON both record the model and its licence, so when a generated pack is shared the
terms travel with the asset (item 3).

This file was written by inspecting the install rather than the plan: node-pack licences are read
from the `LICENSE` file in each pinned submodule, and model files are the ones the 16 shipped graphs
actually name, checked against what is on disk. Verified 2026-07-26 against ComfyUI 0.28.0 at
`/home/siva/dev/ComfyUI`. Where a licence is not shipped inside the install, the column says so and
names the upstream source to confirm it against; those are marked **upstream**.

## Models

Sizes are apparent size on disk. "Route" names the workflows (docs/COMFYUI.md, the workflow
catalogue) that reference the model, so a licence term can be traced to the feature that carries it.

| Model | Source | Licence | Size | Route |
|---|---|---|---|---|
| **TRELLIS.2-4B** | `microsoft/TRELLIS.2-4B` (Hugging Face) | MIT (**upstream**) | 15 GB with its cache | W4 rembg, W5t, W9b, W9c, W9t, W6t: every geometry and mesh-texture route |
| **TRELLIS-image-large** (sparse-structure decoder only) | `microsoft/TRELLIS-image-large` | MIT (**upstream**) | inside the 15 GB above | pulled by `pipeline.json` as one component of the TRELLIS.2 pipeline |
| **BiRefNet** | `ZhengPeng7/BiRefNet` | MIT (**upstream**) | 424 MB | W4's background cutout (`Trellis2RemoveBackground`) |
| **Hunyuan3D-Omni** | `tencent/Hunyuan3D-Omni` | **Tencent Hunyuan 3D Omni Community License**, `models/hunyuan3d-omni/License.txt` in the install | 13 GB | W7, the block-out control route |
| **Hunyuan3D 2.1** | `hunyuan_3d_v2.1.safetensors` | **Tencent Hunyuan Community License** (**upstream**) | 6.9 GB | W5, the zero-install geometry smoke test |
| **Hunyuan3D-2mv** | `hunyuan3d-dit-v2-mv_fp16.safetensors` | **Tencent Hunyuan Community License** (**upstream**) | 4.6 GB | W6, multi-view geometry |
| **RealVisXL V5.0** (fp16) | `SG161222/RealVisXL_V5.0` | OpenRAIL++ (**upstream**) | 6.5 GB | every raster route: W1, W2, W3, W4's reference image, W12, W12e, W9, W13 |
| **CLIP-ViT-H-14-laion2B-s32B-b79K** | `laion/CLIP-ViT-H-14-laion2B-s32B-b79K` | MIT (**upstream**) | 2.4 GB | W2 and W9, as the IPAdapter vision encoder |
| **IP-Adapter SDXL (vit-h)** | `h94/IP-Adapter` | Apache-2.0 (**upstream**) | 667 MB | W2 (reference texture), W9 (palette lock across views) |
| **ControlNet Depth SDXL 1.0** | `diffusers/controlnet-depth-sdxl-1.0` | OpenRAIL++ (**upstream**) | 2.4 GB | W12, W12e, W9 |
| **ControlNet Union SDXL 1.0 promax** | `xinsir/controlnet-union-sdxl-1.0` | Apache-2.0 (**upstream**) | 2.4 GB | W12, W12e, W9, as the normal-map control |
| **Depth Anything V2 Large** | `depth-anything/Depth-Anything-V2-Large` | **CC-BY-NC-4.0** (**upstream**) | 1.3 GB | W12e only, the ESTIMATED stylise route |
| **NormalBAE** (`scannet.pt`) | `lllyasviel/Annotators` | MIT (**upstream**) | 278 MB | W12e only |
| **4x-UltraSharp** | Kim2091, `uwg/upscaler` / OpenModelDB | **CC-BY-NC-SA-4.0** (**upstream**) | 64 MB | W3 only, the texture-set 2x upres |

### The four that are not permissive, stated plainly

1. **Hunyuan (Omni, 2.1, 2mv): a territorial exclusion and an MAU threshold.** Verbatim from
   `models/hunyuan3d-omni/License.txt`, which is the copy in the install:

   > THIS LICENSE AGREEMENT DOES NOT APPLY IN THE EUROPEAN UNION, UNITED KINGDOM AND SOUTH KOREA AND
   > IS EXPRESSLY LIMITED TO THE TERRITORY, AS DEFINED BELOW.

   > "Territory" shall mean the worldwide territory, excluding the territory of the European Union,
   > United Kingdom and South Korea.

   > If, on the Tencent Hunyuan 3D Omni version release date, the monthly active users of all
   > products or services made available by or for Licensee is greater than 1 million monthly active
   > users in the preceding calendar month, You must request a license from Tencent, which Tencent
   > may grant to You in its sole discretion, and You are not authorized to exercise any of the
   > rights under this Agreement unless or until Tencent otherwise expressly grants You such rights.

   Where this lands: **W7 (block-out control), W5 (smoke test) and W6 (multi-view)**. Nothing else.
   TRELLIS.2 is MIT and is the primary model for exactly this reason, so an artist in the EU, the UK
   or South Korea can use every default route in this integration and simply not use those three.

2. **Depth Anything V2 Large is CC-BY-NC-4.0: non-commercial.** It is used by **W12e alone**, the
   estimated stylise route. G4 measured that the estimated route and the real-passes route are within
   the estimator's own error of each other, which cuts both ways: W12e is a genuine alternative, and
   it is also genuinely skippable. A commercial project should use **W12** (Bob's own depth and
   normal passes, which are Blender output and carry no model licence at all) and never load W12e.

3. **4x-UltraSharp is CC-BY-NC-SA-4.0: non-commercial, and share-alike.** It is used by **W3 alone**,
   the optional `Upres 2x` button on a staged texture variant. Nothing else in the integration
   depends on it, so a commercial project can leave the upres unused, or substitute a permissively
   licensed ESRGAN model of its own: `UpscaleModelLoader.model_name` is a plain enum over
   `models/upscale_models/`, and preflight will name the substitute if it is missing.

4. **RealVisXL V5.0 and ControlNet Depth SDXL are OpenRAIL++.** Permissive for commercial use, but
   they carry **use restrictions** (the RAIL behavioural clauses) that a plain MIT model does not.
   They apply to the person running the model, not to Bob.

## Node packs

None of this code is redistributed by BobBlenderTools: the packs are installed into the artist's own
ComfyUI. ComfyUI itself is GPL-3.0 and this extension is already `GPL-3.0-or-later`, so a GPL pack in
the artist's install raises no distribution question for Bob either. Licences below are read from the
`LICENSE` file in each pack, at the SHA pinned as a submodule in the reference fork.

| Pack | Pinned SHA | Licence | Needed by |
|---|---|---|---|
| `ComfyUI-TRELLIS2` | `9b87851` | MIT (`LICENSE`) | every geometry and mesh-texture route |
| `ComfyUI-GeometryPack` | `c67199d` | **GPL-3.0** (`LICENSE`) | required by TRELLIS2's own `node_reqs`; W9t names `GeomPackUVUnwrap` |
| `ComfyUI-Hy3D-Omni` | `e513cd0` | **NO LICENCE FILE AT ALL** | W7 only |
| `ComfyUI-seamless-tiling` | `9225ed5` | **GPL-3.0** (`LICENSE`) | W1, W2, W3, and W13's tiled route |
| `ComfyUI_IPAdapter_plus` | `a0f451a` | **GPL-3.0** (`LICENSE`) | W2, W9 |
| `ComfyUI_UltimateSDUpscale` | (not a submodule in this install) | **GPL-3.0** (`LICENSE`) | W3 only |
| `comfyui_controlnet_aux` | (not a submodule in this install) | Apache-2.0 (`LICENSE.txt`) | W12e only |

Three things about that table are worth stating rather than leaving in a cell.

**`ComfyUI-Hy3D-Omni` ships no licence at all.** No `LICENSE`, no `COPYING`, no licence field in a
`pyproject.toml`. So its terms are unstated, which under copyright means **no licence is granted**,
not that it is public domain. It is also the least maintained dependency in the integration and the
one that shipped with the control signal silently random (docs/COMFYUI.md, G4c). It is used by W7 and
by nothing else. Treat W7 as an in-house tool until upstream states a licence.

**`ComfyUI-GeometryPack` is GPL-3.0, and the plan said otherwise.** Revision 13's licensing section
reads "the primary model (TRELLIS.2, MIT) and the primary custom pack (`ComfyUI-TRELLIS2`, MIT) are
both permissive"; what it did not say is that TRELLIS2 auto-clones GeometryPack as a hard requirement
and GeometryPack is GPL-3.0. Consequence for Bob: none, for the reason above. Consequence for anyone
vendoring these packs into a closed product: real.

**`ComfyUI_UltimateSDUpscale` and `comfyui_controlnet_aux` are not pinned as submodules** in the
reference install, unlike the other five. They were already present. Each is used by exactly one
optional workflow (W3 and W12e), which is also the pair whose models are the two non-commercial ones,
so the whole non-commercial surface of this integration is those two routes and nothing else.

## What is NOT here, and why that matters

- **`briaai/RMBG-2.0`**, whose licence is non-commercial, is aliased away: the TRELLIS2 pack maps
  `"briaai/RMBG-2.0": "ZhengPeng7/BiRefNet"` and the weights on disk are BiRefNet's. So the plan's
  claim that dropping RMBG removes a non-commercial term is confirmed by the pack's own alias table
  rather than assumed.
- **No LoRA is shipped or required.** `bob_placeholder.safetensors` appears in W9, W12 and W12e as a
  binding point, and with no LoRA configured the `LoraLoader` node is REMOVED from the graph rather
  than run at strength 0, because a placeholder filename fails the validator on a machine with no
  LoRAs installed. A style LoRA an artist adds carries its own terms, which Bob cannot know.
- **No API or cloud model.** Every `comfy_api_nodes` class and all six `api_hunyuan3d_*` templates
  are out of scope, and this is enforced rather than intended: `/object_info` reports
  `api_node: true` per node, and preflight rejects any graph containing one. So no third-party
  terms-of-service reach this integration at all.
