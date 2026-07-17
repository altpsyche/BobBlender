# library/ — the reusable core

This folder is registered as a **Blender Asset Library**
(*Preferences → File Paths → Asset Libraries → Add → this folder*).

Anything marked as an asset in the `.blend` files here appears in the Asset
Browser across every project, organised by the catalogs defined in
`blender_assets.cats.txt`.

## Sub-folders

| Folder            | Contents |
|-------------------|----------|
| `geometry_nodes/` | `.blend` files of reusable geometry-node groups, marked as assets. |
| `materials/`      | Reusable materials & shader node groups. |
| `hdri/`           | Environment maps for lighting/lookdev. |
| `textures/`       | Textures used across multiple projects. |
| `macros/`         | Small utility node groups (bounding box, remap, etc.). |

## How to add an asset

1. Build/refine the node group in a project.
2. **Bob → Promote to Library** (marks it as an asset), or right-click →
   *Mark as Asset*.
3. Append it into the matching `.blend` here (keep related groups grouped, e.g.
   one `scatter.blend`, one `noise.blend`).
4. In the Asset Browser, assign a **catalog** and add a thumbnail + description.

## Suggested library .blend files

- `geometry_nodes/scatter.blend` — distribution / instancing systems
- `geometry_nodes/noise.blend` — curl noise, fbm, domain warping
- `geometry_nodes/curves.blend` — curve tools, growth, splines
- `materials/procedural.blend` — triplanar, fresnel, parallax, gradients
- `macros/util.blend` — bounding box, remap, vector math helpers

Create these as you go — no need to pre-make empty files.
