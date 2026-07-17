# library/

The reusable core. Register this folder as a Blender Asset Library under
Preferences > File Paths > Asset Libraries > Add.

Anything marked as an asset in the `.blend` files here appears in the Asset
Browser across every project, organised by the catalogs in
`blender_assets.cats.txt`.

## Sub-folders

| Folder | Contents |
|--------|----------|
| `geometry_nodes/` | `.blend` files of reusable geometry-node groups, marked as assets. |
| `materials/` | Reusable materials and shader node groups. |
| `hdri/` | Environment maps for lighting and lookdev. |
| `textures/` | Textures used across multiple projects. |
| `macros/` | Small utility node groups (bounding box, remap, and so on). |
| `_generated/` | Agent-built output (gitignored). Append what you want to keep into the folders above. |

## How to add an asset

1. Build or refine the node group in a project.
2. Mark it as an asset (right-click, Mark as Asset). The MCP `build_geonodes`
   op can also do this with `mark_asset: true`.
3. Append it into the matching `.blend` here, keeping related groups together
   (for example one `scatter.blend`, one `noise.blend`).
4. In the Asset Browser, assign a catalog and add a thumbnail and description.

## Suggested library .blend files

- `geometry_nodes/scatter.blend`: distribution and instancing systems
- `geometry_nodes/noise.blend`: curl noise, fbm, domain warping
- `geometry_nodes/curves.blend`: curve tools, growth, splines
- `materials/procedural.blend`: triplanar, fresnel, parallax, gradients
- `macros/util.blend`: bounding box, remap, vector math helpers

Create these as you go. There is no need to pre-make empty files.
