# forest-barn: what was actually done, step by step

A record of how gate A was built, twice, so the sequence is reviewable without reading a
transcript. The scene is a dirt path through mixed woodland leading to a weathered timber barn at
dusk, built over MCP into a live Blender session, with every asset generated through ComfyUI and
every layout from the BobBlenderTools recipes. The contract is in `references/SCENE-BRIEF.md`:
three approval gates, stop at each one.

Gate A is assets only. Nothing is composed, nothing is lit for mood, and no terrain is built. The
whole point is that a rejected asset costs one call here and costs the entire scene at gate C.

---

## 1. Read the brief, then check the two environments

`references/SCENE-BRIEF.md` first, in full, including its trap list. The traps are not advice, they
are failures that each cost a session, and re-deriving them is the expensive way to learn them.

Then two checks that have both been skipped before at a cost:

- `comfy_status()`. Reports whether ComfyUI is reachable, on what device, and how much VRAM is
  free. It never fails; with no server it returns a reason.
- `describe_scene(include=["packs"])`. If it reports no live bridge, the Blender extension's socket
  server is not running and nothing can be authored.

The second check also reports the asset pack search path, which matters more than it looks. On the
first run of this scene the live session resolved its generated pack to a folder inside the ComfyUI
checkout while the MCP tools wrote to one inside the repo. Foliage resolves its bark and leaf
atlases BY NAME off that search path with no way to pass a directory, so the trees would have worn
whatever stale sets were in the wrong folder.

## 2. Work out what the tools actually take

The op vocabulary is `docs/API.md`; the recipes' real parameters are in the recipe modules
themselves, and the species presets are data files under
`blender/extensions/bob_blender_tools/assets/foliage/`. Reading those first is what makes the
difference between passing a parameter the recipe knows and passing one it silently drops.

Two things learned here that are not obvious from the docs:

- There is no MCP op for growing a species preset. Over MCP a tree is `build_geonodes` with the
  `foliage` recipe and the species' whole parameter dict passed by hand.
- `build_geonodes` reads `location` and `collection` out of `params` rather than from op fields, so
  `{"params": {"collection": "BOB_Assets_Trees"}}` is how a build lands in an off-scene scatter
  pool.

## 3. Screen every reference before paying for geometry

`comfy_mesh(subject_only=True)` stops after the reference image and hands back its path. This is
the single highest-leverage step in the whole gate.

Every image-to-3D stage conditions on the reference PICTURE and none of them reads the prompt text.
So the reference is the asset. A bad one is only visible as a bad mesh two to seven minutes later.

Measured on this scene: eleven references generated and looked at, about 75 seconds in total, of
which five were rejected on sight. Those five would have cost roughly 400 seconds of geometry to
discover. Rejected pictures included a barn photographed dead-on (no depth cue for image-to-3D), a
rock slab standing on little decorative feet, and a barn on a display plinth with a toy car beside
it.

Accept a reference by passing its path straight back as `subject=<path>`, which runs the geometry
against exactly the picture that was approved. Reject one by calling again with a different seed.

## 4. Generate in VRAM order, hero first

ComfyUI does not give the card back. Measured on this run: 12261 MiB free at the start, 5686 MiB
after the meshes and textures. The hero mesh route needs 7000 MiB free and the default mesh route
5000, so the order is forced:

1. The hero structure first, while the card is emptiest.
2. The other meshes.
3. Textures, bark and atlases last, which need only 3000.

The tools preflight against those floors, so an underfunded call is a sentence rather than an
out-of-memory error ninety seconds in. When the card does run out, only restarting ComfyUI recovers
it: `comfy_free` reports honestly that it recovered nothing.

## 5. Read the receipt on every asset

Each tool hands back numbers, and the gate is those numbers rather than an impression:

- `comfy_bark_set` returns `grain.off_vertical_deg`. Under about 25 means the grain runs along the
  trunk rather than across it.
- Every texture tool returns `flatness.low_freq_variation`, and over 0.075 means the albedo has
  lighting baked into it. `delight=True` divides it out.
- `comfy_leaf_atlas` returns a per-cell `opaque` (a cell at 0.0 is a card that renders as nothing),
  `cell_distinctness`, and `flatness.in_mask_stops`.
- `comfy_mesh` then `import_generated` returns `low_boundary_edges` (openness of the mesh that
  actually ships), `bake_fidelity.correlation` (the baked colour against the texture it came from),
  `uv_overlap`, `height_m` and `origin_above_base`.

On this run the receipts caught four things that would otherwise have reached a hero render: a lit
forest floor at 0.0989, a stump shipping open at 1.3%, a small rock at 1.6%, and a barn whose baked
colour did not match its source at 0.9108.

## 6. Import, and let the numbers pick the rerolls

`import_generated` is the Blender half: bake, scale to the real height, drop the origin to the
base, build an LOD chain, convert to a BobShader, write the pack and link into `BOB_Assets_<Kind>`.
Pass `cleanup: false` or the staging directory is consumed and the asset cannot be restaged.

Give each asset its own `kind` so it gets its own pool. Sharing a pool means a later
`scatter_along` picks between them at random, which is not what you want when placing one hero.

Rerolls this run, each driven by a number rather than a look:

- Stump at 1.3% open, regenerated at a raised face budget, came back at 0.58%.
- Barn at 0.9108 fidelity, regenerated from a different reference, came back at 0.9974.
- Forest floor at 0.0989 flatness, regenerated with `delight=True`, came back at 0.0662.

## 7. Lay the assets out and photograph them

Generated meshes live in unlinked collections, so they are not in the scene and cannot be
photographed directly. The route that places one instance:

1. A two-point `make_curve` at the spot.
2. `build_geonodes` with the `scatter_along` recipe, pointed at that curve, the floor plane as
   emitter and the asset's pool, with `spacing` longer than the curve so it places exactly one and
   `min_scale` and `max_scale` both at 1.0.

Those last two matter: the recipe ships with a 0.8 to 1.2 scale range, so on the defaults the one
asset whose height you measured and reported arrives somewhere between 0.8 and 1.2 of it.

Then a flat front light, a wide camera for the row, a close camera for the small assets, a
three-quarter camera on the hero, and a two-metre camera on a trunk to check the bark. Labels are
drawn onto the PNG afterwards because the op vocabulary has no text op.

## 8. Report measured numbers, stop, and wait

Show an image at every gate, state the numbers rather than impressions, and say plainly when a tool
cannot do something instead of quietly substituting. Then stop. Going backwards is cheap; going
backwards silently is not.

---

## The shape of the two runs

**First run.** Built the full asset set, photographed it, and reported. The artist rejected it on
four visual defects: melted barn windows, holes in the stump, bark wrapping wrong at the trunk
foot, and a chevron hash over the barn roof, plus a question about lighting baked into the
basecolors.

Those were traced to root causes in the pipeline rather than in the prompts, and handed to a second
agent to fix. Two of the five findings did not survive that agent's testing and were correctly
rejected: the suspected AO double-count was disproven by measurement, and the bark defect turned
out to be a different mechanism than the one proposed.

**Second run.** Regenerated everything against the fixed pipeline. The four reported defects are
fixed and measured as fixed. Two new problems appeared, which is what
[HANDOVER.md](HANDOVER.md) is for.
