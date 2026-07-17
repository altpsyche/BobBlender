# Conventions

Small, boring rules that keep the repo navigable as it grows.

## Naming

- **Projects**: `kebab-case`, descriptive, no dates — `voronoi-cities`, not
  `project1`. Dates belong in render output, not the folder name.
- **Node groups** (the reusable ones): `Category — Name`, e.g.
  `Scatter — Poisson`, `Math — Curl Noise`, `Util — Bounding Box`. The em-dash
  reads well in the Asset Browser and sorts by category.
- **Materials**: `M_<Surface>` (`M_BrushedMetal`), procedural shader node
  groups: `S_<Effect>` (`S_Triplanar`, `S_Fresnel`).
- **Versions**: `_v001`, `_v002` … zero-padded to 3.

## Where files go

| Thing | Home |
|-------|------|
| Working .blend for a piece | `projects/<name>/src/` |
| A node group worth reusing | **Promote** it into `library/` (see below) |
| Final images / video | `renders/<name>/<YYYYMMDD>/` (gitignored) |
| Client/export deliverables | `projects/<name>/exports/` |
| Textures used by one project | `projects/<name>/textures/` |
| Textures used everywhere | `library/textures/` |

## Render output convention

`renders/<project>/<YYYYMMDD>/<project>_<scene>_v###`
followed by frame/extension. Set `render.filepath` to this by hand, or let the
**bob's-assembly** extension automate it from the current .blend. The slug/path
logic is mirrored in `tools/bobtools/naming.py` for headless/batch use.

## Promoting to the library

When a geometry-node or material node group proves reusable:

1. In its .blend, mark it as an asset: right-click → *Mark as Asset* (the
   **bob's-assembly** extension can automate this).
2. Save/append it into the matching `library/` .blend
   (`library/geometry_nodes/…` or `library/materials/…`).
3. Assign an Asset Browser **catalog** (categories are defined in
   `library/blender_assets.cats.txt`).
4. Give it a thumbnail and a one-line description — future-you will thank you.

## Git hygiene

- Never commit `renders/` — they're regenerable.
- `.blend` and textures go through **Git-LFS** (see `.gitattributes`).
- Commit node-group library changes with a note on *what the node does*, not
  just "updated library".
