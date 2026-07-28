# Conventions

Small rules that keep the repo navigable as it grows.

## Identifiers describe features, not the work that produced them

The rule, and it is enforced by `tools/scripts/check_no_phase_labels.py` in CI:

- **No letter-number labels** anywhere a reader will meet them: docs, headings, comments,
  docstrings, filenames, gate keys, check labels. A capital letter plus one or two digits, with or
  without a lowercase sub-letter, is a phase label whatever it names. They index the repo by a
  private chronology, and a reader who was not present cannot follow one.
- **A measurement keeps its number and loses its phase.** "eighty times the attachment residual the
  card work measured" becomes "eighty times the card-attachment residual". Dropping the figure is the
  one edit that is never allowed; the figures are why these docs are trustworthy.
- **"Phase X shipped/found Y" becomes the invariant plus its check.** The history is spent; the
  rule is still true. State the rule, then name the check that catches its violation.
- **A numbered decision becomes a named rule** — `the dead-wood routing rule`, `the heightfield
  bit-depth floor`, `the single-compute rule`. Names cross-reference as well as numbers do and
  carry their meaning with them.
- **Track names stay.** `BobFoliage`, `BobSplines`, `BobShaders`, `BobFirmament` are product
  names, not phases; a phase label appended to one is still a phase label and still goes. Panel
  labels stay plain nouns (`Foliage`, `Paths`, `Scatter`).
- **Generation routes are named by their workflow file**, which is already the code identity:
  `mesh_subject`, `mesh_geom_texture`, `heightmap_macro`. Never by a `W` number.
- What the guard's allowlist is *for*: genuine external names that happen to be letter-number
  (`SDXL`, `TRELLIS.2`, `Hunyuan3D 2.1`, `CLIP-ViT-H-14`, Blender versions, licence codes).
  Add to `tools/scripts/phase_label_allowlist.txt` with a reason, never to silence a real hit.

Behavioural identifiers are exempt because they are not prose: op names, MCP tool names, recipe
names, GN socket names, param keys, `bbt_*` attributes, collection names, manifest fields,
`S_GROUP_VER`. None of them carries a phase code today; keep it that way.

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

The BobBlenderTools N-panel suite follows one design language. When adding or editing a panel,
keep to it:

- Order along the pipeline with `bl_order`, not registration: World 0, Biome 1, Terrain 2,
  Paths 3, Scatter 4, Shaders 5, Atmosphere 6, Advanced 7. A one-line overview at the top of
  World names the sequence (panel labels stay plain, no stage numbers).
- Native identity. Reflect the active thing (active object/material, active emitter/layer),
  never a panel-local name or duplicate target pointer.
- Use the shared helpers in `ui/helpers.py` so the language stays consistent: `context_header`
  (the "what am I acting on" line + empty-state hint), `structural_action` (a build/rebuild
  button with the shared `STRUCTURAL_ICON` and a short "rebuilds:/builds:" note), and
  `preset_row` (the one preset control, an `operator_menu_enum` with per-item label +
  description). Every preset in the suite uses `preset_row`.
- Make live-vs-structural visible: group instant live knobs apart from structural
  build/rebuild/apply actions, and mark the structural ones with `structural_action`.
- Show only what applies to the current state (adaptive/minimal): per-row New OR Convert, not
  both; hide sub-panels that do not apply (poll on the detected kind).
- The world is driven from one place (the World panel, `bbt_world`). A world-driven subsystem
  subscribes an applier via `ui/world.register_applier(fn)`; it does not add its own world
  toggle.

## Git hygiene

- Never commit `renders/`; they are regenerable.
- `.blend` and texture files go through Git-LFS (see `.gitattributes`).
- When committing a library change, note what the node does, not just
  "updated library".

### Decisions inside those rules, with their reasons

These came out of a whole-suite panel review and are the parts most likely to be re-litigated:

- **`STRUCTURAL_ICON` only marks a datablock rebuild.** New / Convert / Add Layer / Snow Shell keep
  their native ADD / NODE_MATERIAL / REMOVE icons on purpose: they are create, list-add and
  material-transform affordances with clearer icons of their own, and they are already separated from
  the live knobs. Forcing the shared marker onto them would replace a specific icon with a generic
  one. Reload Builders is CONSOLE for the same reason -- it is a dev reload, not a rebuild, and
  keeping it off the marker is what keeps the marker meaningful.
- **No per-curve knob for a curve's surface look.** `apply_curve_surface` writes a real terrain layer
  slot, so those sockets are already live and reachable in the terrain Layer Masks panel. Every
  channel-"a" path shares ONE idempotently-reused slot, so a "this curve" knob would silently edit
  every path. `apply_curve_wet` is likewise a MAX-accumulated shared path. The real gap here is
  discoverability -- that a curve band IS a terrain layer -- which is a docs matter, not a knob.
- **A greyed button that does nothing is worse than an absent one.** A control that cannot act teaches
  an artist to distrust every control beside it, and the affordance is worth less than the trust. Ship
  the button with the thing it does, or not at all.
- **Open, deferred:** a few build-time engine params are not surfaced anywhere (`build_sky`
  `sun_intensity`, voronoi jitter, dunes warp). They are `params.get()` reads at build time rather
  than live modifier sockets, so surfacing them needs scene props threaded into the build params.
  Bigger than socket-surfacing, and nothing is blocked on them.

### How to review a panel change

Do not eyeball the draw code. Register the addon in Blender 5.2 headless and run a **recording draw
harness**: stub the layout API, capture what every panel and sub-panel actually renders, and do it
across the states that change the layout -- an empty scene, a populated one (a Terrain mesh plus an
emitter), live environment off, and Firmament off. Then drive the operators that populate the deep
panels (a Surface, Terrain and Water BobShader, a scatter layer, a dirt-path curve, a river curve) and
capture those too. Every finding worth acting on came out of that recording; none came out of reading
the draw functions.

The one trap: a stub layout has to implement every verb the panels call. A missing verb raises inside
the harness rather than in Blender, which reads as a panel bug. Listing the verbs explicitly beats a
`__getattr__` catch-all, which hides the next such drift.
