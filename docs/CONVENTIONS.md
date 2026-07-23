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
| A biome (manifest, optional models) | `library/models/<biome>/` (a `manifest.json`) |

## Attribution

Record provenance for downloaded assets even when the licence (e.g. CC0) does not require it.
A texture set carries a one-line `SOURCE.txt` (`<role>: Poly Haven '<slug>' (CC0, public domain).
<url>`). A biome records its attribution in the `meta` block of its `manifest.json` (`source`,
`license`); the shipped biome (`library/models/blockout`) is a procedural block-out with no
external files, so it needs none.

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

## Panel UX conventions

The BobBlenderTools N-panel suite follows one design language (full rationale in
`UX-REDESIGN.md`). When adding or editing a panel, keep to it:

- Order along the pipeline with `bl_order`, not registration: World 0, Biome 1, Terrain 2,
  Paths 3, Scatter 4, Shaders 5, Atmosphere 6, Advanced 7. A one-line overview at the top of
  World names the sequence (panel labels stay plain, no stage numbers).
- Native identity. Reflect the active thing (active object/material, active emitter/layer),
  never a panel-local name or duplicate target pointer.
- Use the shared helpers in `ui_helpers.py` so the language stays consistent: `context_header`
  (the "what am I acting on" line + empty-state hint), `structural_action` (a build/rebuild
  button with the shared `STRUCTURAL_ICON` and a short "rebuilds:/builds:" note), and
  `preset_row` (the one preset control, an `operator_menu_enum` with per-item label +
  description). Every preset in the suite uses `preset_row`.
- Make live-vs-structural visible: group instant live knobs apart from structural
  build/rebuild/apply actions, and mark the structural ones with `structural_action`.
- Show only what applies to the current state (adaptive/minimal): per-row New OR Convert, not
  both; hide sub-panels that do not apply (poll on the detected kind).
- The world is driven from one place (the World panel, `bbt_world`). A world-driven subsystem
  subscribes an applier via `world_panel.register_applier(fn)`; it does not add its own world
  toggle.

## Git hygiene

- Never commit `renders/`; they are regenerable.
- `.blend` and texture files go through Git-LFS (see `.gitattributes`).
- When committing a library change, note what the node does, not just
  "updated library".
