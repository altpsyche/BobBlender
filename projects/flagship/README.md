# flagship

The proving ground for BobBlenderMCP. This is where new pipeline capabilities
get built and verified before they become reusable library assets, so it leans
toward experiments rather than a single finished piece.

## Layout

| Folder | Contents |
|--------|----------|
| `src/` | Working `.blend` files. Main file: `flagship_v001.blend`. |
| `textures/` | Textures used only by this project. |
| `refs/` | References specific to this piece. |
| `exports/` | Deliverables (glTF, USD, stills). |
| `renders/` | Output frames and images. Gitignored. |

## Notes

- Idea: exercise the MCP-to-Blender pipeline end to end (headless and live).
- Techniques: geometry-node recipes from `blender/bbmcp/geonodes.py`. First one
  is `wave_grid`, a radial sine surface.
- Reusable bits to move into `library/`: any recipe that proves useful, marked
  as an asset.
- Render settings: to be decided per experiment.

## Checklist

- [ ] Main `.blend` saved in `src/`
- [ ] Render output follows `docs/CONVENTIONS.md`
- [ ] Reusable node groups marked as assets and appended to `library/`
- [ ] Final render in `renders/`, hero shot copied to `exports/`
