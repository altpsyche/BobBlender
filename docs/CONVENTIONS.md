# Conventions

Small rules that keep the repo navigable as it grows.

## Naming

- Projects: kebab-case, descriptive, no dates. Use `voronoi-cities`, not
  `project1`. Dates belong in render output, not the folder name.
- Reusable node groups: a descriptive name, categorised with Asset Browser
  catalogs (see `library/blender_assets.cats.txt`). If you want the category in
  the name, use a hyphen, for example `Scatter - Poisson`, `Math - Curl Noise`.
- Materials: `M_<Surface>`, for example `M_BrushedMetal`. Procedural shader node
  groups: `S_<Effect>`, for example `S_Triplanar`, `S_Fresnel`.
- Versions: `_v001`, `_v002`, zero-padded to three digits.

## Where files go

| Thing | Home |
|-------|------|
| Working `.blend` for a piece | `projects/<name>/src/` |
| A node group worth reusing | Mark as an asset and append into `library/` |
| Final images and video | `renders/<name>/<YYYYMMDD>/` (gitignored) |
| Deliverables | `projects/<name>/exports/` |
| Textures used by one project | `projects/<name>/textures/` |
| Textures used everywhere | `library/textures/` |

## Render output

Aim for `renders/<project>/<YYYYMMDD>/<project>_<scene>_v###` followed by the
frame and extension. Set `render.filepath` to match.

## Promoting to the library

When a geometry-node or material node group proves reusable:

1. Mark it as an asset in its `.blend` (right-click, Mark as Asset).
2. Append it into the matching library file (`library/geometry_nodes/...` or
   `library/materials/...`).
3. Assign an Asset Browser catalog (defined in
   `library/blender_assets.cats.txt`).
4. Give it a thumbnail and a one-line description.

## Git hygiene

- Never commit `renders/`; they are regenerable.
- `.blend` and texture files go through Git-LFS (see `.gitattributes`).
- When committing a library change, note what the node does, not just
  "updated library".
