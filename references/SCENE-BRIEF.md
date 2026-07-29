# The brief for a fully generated scene

One scene, built over MCP in three approval gates: every asset generated with ComfyUI, every layout
and every piece of structured geometry from the BobBlenderTools recipes.

Copy the block in section 1 into a fresh session, fill the five bracketed fields, and send it. The
rest of this file is what that brief tells the agent to read: the gate contract, the asset manifest
it has to fill, and the list of traps that have already cost a session each. Every rule in section 5
is there because it produced a frame that rendered convincingly and was wrong.

---

## 1. The prompt

```text
Build a beautiful [SUBJECT] scene in my open Blender session, over MCP. Every ASSET is generated
with ComfyUI; every LAYOUT and every bit of geometry that has to be structured comes from the
BobBlenderTools recipes. Read references/SCENE-BRIEF.md first and follow its gate contract and
its trap list -- do not re-derive them.

Scene: [SUBJECT]
Mood and light: [MOOD, e.g. night, moonlit, mist in the hollow, warm practical light from inside]
Shot: [SHOT, e.g. a path leading away from camera to the building at the end of it]
Aspect and size: [16:9, 1920x1080]
Project name: [PROJECT-SLUG]

Division of labour, and it is not negotiable:
- Terrain    -- bake_heightfield for the field, then a build_geonodes heightmap_terrain build for
                the mesh. comfy_heightmap for the macro layout, fed in as bake_heightfield's
                `macro` key. comfy_texture_set for every layer's surface.
- Foliage    -- BobFoliage recipe for all live plant GEOMETRY. comfy_bark_set and comfy_leaf_atlas
                for its textures, generated under the exact names the species presets ask for.
                Image-to-3D cannot make a live tree; do not try.
- Structures -- comfy_mesh for the geometry, comfy_texture_set or the route's own bake for the
                textures. Placed with scatter_along (see the trap list, "placing a single
                generated mesh").
- Scatter    -- comfy_mesh for logs, stumps, boulders, debris. Dead wood and rock only.
- Path       -- BobSplines curve for the cut and the mask, comfy_texture_set for its band.
- Atmosphere -- our tools: build_sky, build_fog (the volumetrics recipe), build_motes and
                build_rain (the particulates recipe).

Work in three gates. STOP at each one and wait for my approval before starting the next. Do not
run ahead.

GATE A -- assets. Generate every asset. ALL ComfyUI work happens here and none of it happens
later: the card is not shared (see the trap list, "ComfyUI never gives the card back"). Lay them
all out in a row on a flat plane, evenly spaced, labelled, front-lit, and render one contact sheet
plus one three-quarter view of each hero asset. Report the measured numbers the tools hand back
(see section 4). I approve, reject or ask for a reroll per asset. Nothing else gets built until I
say the assets are good.

GATE B -- composition, as a BLOCKOUT. Real terrain, real path, real camera, real light direction.
Every asset stands in as a plain add_mesh cube at its true measured height. No generated art in
this gate, and no make_proxies. Render the frame. I approve the composition -- camera, lens, path
route, where things stand, what the light is doing -- before any asset goes in.

GATE C -- the scene. Stop the ComfyUI server first. Then swap the approved assets into the
approved blockout, dress it, light it, atmosphere, hero render at the size above.

Rules for how you report to me:
- Show me an image at every gate. Never describe a frame instead of rendering it.
- State the measured numbers, not your impression of them.
- If a tool cannot do something, say so plainly at the gate it comes up, with the workaround you
  intend, and let me choose. Do not quietly substitute something else.
- If you change a decision I approved, tell me you are changing it and why, before you do.
```

Five fields to fill: `SUBJECT`, `MOOD`, `SHOT`, aspect/size, `PROJECT-SLUG`.

---

## 2. The gate contract

A gate is not a status update. It is a stop.

| Gate | Deliverable | Approved means |
|------|-------------|----------------|
| A | Every asset generated, laid out, photographed, measured | Each asset is good enough to build a frame around, or named for a reroll |
| B | Blockout frame at final camera, final lens, final light direction | The composition is the shot. Camera, path route, placement and light direction are now fixed |
| C | Hero render | Done |

Three properties the gates exist to protect:

- **Composition and lighting fail in ways that hide each other.** A dark frame cannot be read for
  whether the building is behind a crest. That is why gate B is lit plainly and carries no art, and
  why gate C is the only place the mood light appears. Build a `--daylight` check pass and keep it.
- **A rejected asset at gate C costs the whole scene.** At gate A it costs one call.
- **Generation and rendering cannot interleave**, because ComfyUI does not give the card back. The
  gates are also the VRAM schedule: every `comfy_*` call is inside gate A, and the server is stopped
  before the first Cycles frame. Trap 2 is the measurement.

Going backwards is allowed and cheap. Going backwards silently is not. Going backwards past gate A
after ComfyUI has been stopped costs a server restart, which is the one reason to be thorough at
gate A rather than fast.

---

## 3. The asset manifest

Fill this at gate A. One row per asset, and every row names the tool that made it, so nothing
arrives in the frame without a provenance. Record the exact prompt and the seed as well: they are
the whole key to an asset. A default `seed: 0` reaches the graph literally, so it reproduces, but a
rerolled asset has a seed of its own, and trap 15 (restaging a consumed mesh) needs both halves.

| # | Asset | Tool | Kind | Real size | Seed | Notes |
|---|-------|------|------|-----------|------|-------|
| 1 | terrain macro layout | `comfy_heightmap` | mask | tile metres | | prompt says where the landforms go, not what they look like |
| 2 | ground, base layer | `comfy_texture_set` | texture set | -- | | dark; see trap 9 |
| 3 | ground, second layer | `comfy_texture_set` | texture set | -- | | what the slope and noise masks reveal |
| 4 | path band | `comfy_texture_set` | texture set | -- | | |
| 5 | bark, per species | `comfy_bark_set` | texture set | -- | | name it `bark_conifer` / `bark_broadleaf`; trap 13 |
| 6 | leaf atlas, per species | `comfy_leaf_atlas` | texture set | -- | | name it `leaf_conifer` / `leaf_broadleaf` / `leaf_grass`; record cols/rows; trap 13 |
| 7 | structure | `comfy_mesh` | mesh | height m | | `hero: true`; traps 14 and 15 |
| 8 | structure surfaces | `comfy_texture_set` | texture set | -- | | one per material the structure carries |
| 9 | dead wood, several | `comfy_mesh` | mesh | height m | | logs, stumps, snags |
| 10 | boulders, several | `comfy_mesh` | mesh | height m | | |

Sizes are in metres and they are not optional. Every image-to-3D model emits a unit-cube mesh, so
an asset without a real height turns the scene into a toy set.

Four shipped species and three atlases cover them: `conifer` wears `bark_conifer` + `leaf_conifer`,
`broadleaf` and `shrub` BOTH wear `bark_broadleaf` + `leaf_broadleaf`, and `grass_tuft` wears
`leaf_grass` with no bark. So two bark sets and three atlases is the whole foliage texture bill.

---

## 4. What to measure, and report, at each gate

The tools hand back numbers. Report those, not adjectives.

**Gate A, per asset**

- `comfy_bark_set` -- `grain.off_vertical_deg`. Under about 25 is usable. Over that, the bark grain
  is running across the trunk instead of along it: reroll.
- every texture tool -- `flatness.low_freq_variation`. Under 0.075 is a flat albedo. Over it, the
  generation has light baked in: `delight=True` or reroll (trap 24).
- `comfy_leaf_atlas` -- the `cells` list. **A cell with `opaque: 0.0` is a card that renders as
  nothing.** Also `cell_distinctness` (higher is more varied), `clear_fraction`, and
  `flatness.in_mask_stops` (want under 1.0; a card is lit from both sides). Reroll or use a
  smaller grid rather than shipping dead cells.
- `comfy_texture_set` -- `seam.ratio`. Near 1.0 means the wrap is as continuous as any interior
  line.
- `comfy_mesh` then `import_generated` -- `lod_faces` against the budget, `uv_overlap` (want about
  1e-5, not 1.6), `height_m`, `origin_above_base`, `master_type` (should read `surface`), and any
  `warnings`. Then the two that describe the SURFACE rather than the budget, because a receipt with
  neither of them is what let a sieve and a shredded texture through gate A once already:
  `low_boundary_edges` (near zero on a solid; see trap 21) and `bake_fidelity.correlation` (0.99 or
  better; see trap 22).
- `comfy_status` before and after the expensive routes -- free VRAM, against the floors in trap 2.

**Gate B**

- Ground height under every placed thing, ray cast against the **evaluated** terrain.
- Footprint relief for anything with a footprint: the spread between highest and lowest ground
  under it.
- Sightline from camera to the subject's base: does the ground rise above the line, and by how much.
- Instance count per scatter layer, off the depsgraph.

**Gate C**

- Engine, samples, resolution, and the render path.

---

## 5. The trap list

Each of these has already cost a session. They are ordered by how much time they cost.

### 1. Send the clock through `set_env`, and read `data.durable` on every `build_sky`

Anything that re-applies the world re-places the sun from `bbt_firmament` plus `bbt_env`. At a night
`time_of_day` the recomputed sun is below the horizon, where the lamp energy is zeroed and the
physical sky renders black. Symptom: the frame comes back with nothing in it but the emissive
geometry. Reads exactly like a fog or exposure fault and is neither.

What re-places the sun:

- `apply_world`, and `set_env` with its default `apply: true`. Both run the world appliers, and
  Atmosphere's applier repositions the sun.
- **A `set_env` that writes any geographic field, even with `apply: false`.** `time_of_day`, `year`,
  `month`, `day`, `utc_offset`, `latitude` and `longitude` each carry a property update callback, so
  the write itself fires the reposition. `apply: false` protects nothing.
- `apply_season` (it writes `month`), `scene_preset`, `apply_biome` with its default `world: true`,
  and `world_biome`.
- An artist nudging a World slider.

`build_fog` is NOT one of them: it re-applies quality and the wind drivers and leaves the sun alone.

A manual sun override is now safe: `build_sky` records `use_override` and its angles, plus
`sun_strength` and `sun_angle`, onto `bbt_firmament`, which is the one place the applier looks, so a
re-apply reproduces the sun instead of recomputing a different one. A build with no override clears
the flag, so the solar model still wins when that is what was asked for.

**What is still not durable, and the reason to read the result:** a geographic key passed to
`build_sky` but never written to `bbt_env`. The op builds the sun you asked for and the next
re-apply recomputes it from `bbt_env`, which still holds the old clock. `build_sky` says so:
`data.durable` is False and `data.undurable` names the keys. So set the clock with `set_env` and let
`build_sky` read it from there, and check `data.durable` on the reply rather than assuming it.

### 2. ComfyUI never gives the card back

Measured (docs/GENERATION.md, the VRAM-handback rule): after a run of `comfy_*` calls interleaved
with Cycles renders, every further `comfy_mesh` fails with `torch.OutOfMemoryError` raised inside
the TRELLIS2 worker while ComfyUI's main process holds **7.3 GB of a 15.5 GB card**. `comfy_free`
and a direct `POST /free` both return success and recover about **100 MiB**: the pages stay in the
main process's torch caching allocator, and the generation workers are separate processes that
cannot reuse that cache. Only killing and relaunching `main.py` recovered the card (0.5 GB free to
12.3 GB).

So the gate order is also the VRAM schedule, and two rules follow:

- **Every `comfy_*` call belongs to gate A.** Generate the `hero: true` structure FIRST, while the
  card is emptiest: the hero mesh route needs 7000 MiB free against 5000 for a default mesh (the
  per-route floors are `comfy.VRAM_FLOOR_MIB`, and the tools preflight against them, so an
  underfunded call is a sentence rather than an OOM 90 seconds in).
- **Stop the server before the gate C Cycles render**, not after the scene is done. Identify it by
  the listening socket (see section 6).

`comfy_free` is still worth calling between a generate and a render, and worth reading: it reports
`{before, after, recovered, advice}` in MiB rather than claiming success. Bob will not restart a
server it did not start.

### 3. A backlight alone is a black frame

A key aimed up the shot rims everything and lights nothing facing the camera. The sky carries the
fill. Measure it rather than guessing: render an 80x45 frame of sky and ground and read the mean
pixel value per parameter set. A deep blue sky at a low sun height is what makes a fill read as
night rather than as grey.

### 4. Frost is on a dial, snow is on the thermometer, and `snow` is not a field

`bbt_env` ships `frost: 0.6` and BobShaders reads it, so the frost dial is up by default. What is
NOT up by default is the weather that lets it show: `temperature` defaults to 15 C, and both frost
and snow are gated to freezing. A summer night that comes back with frost up every trunk and caps on
the boulders was made cold by something else -- a biome's world block, `apply_season` with winter, a
winter `scene_preset` -- and the frost is the symptom, not the cause. Find the write.

**`bbt_env` has no `snow` field.** `set_env {"snow": 0.0}` is reported in the result's `skipped`
list and changes nothing. Snow has two controls and neither is an amount: `temperature` decides
whether it snows at all (below freezing, colder is thicker) and `snow_line` decides how far down it
reaches (0 snows the whole map, 1 clears everything but the peaks, and it takes 1.25).

So: pass `frost: 0.0` to kill the sheet, check `temperature` and `snow_line` for the cause, and read
the `skipped` list on every `set_env` reply, because a misspelled field is not an error.

### 5. `apply_world` last, or the world never reaches the materials

`set_env` installs the shared env drivers over the materials that exist when it runs. Everything in
the frame is built after it. Send `apply_world` at the end. `describe_scene` reports the state as
`env_drivers: 0`, which is the only way to see it from outside.

### 6. `apply_biome` rewrites the world unless told not to

`apply_biome` defaults to `world: true`, which writes the biome's whole world block (season,
weather, time_of_day and whatever else it carries) onto `bbt_env`. Reach for it to dress a forest
floor at gate C and it takes the approved mood with it, and it repositions the sun on the way out
(trap 1). Pass `world: false` whenever the world is already the one you want. `curve_mode: "clear"`
on the same op is how its scatter respects the path corridor.

### 7. Ground fog: the terrain, and the domain

The `_terrain_drape` fix means `ground_fog` now reads the terrain's size, height and sea level off
the terrain object, so those agree automatically. `build_fog` also takes a `preset`
(`ground_mist` / `valley` / `banks` / `thick`) and an explicit `density`, and the presets set
`Thickness`, `Height`, `Fog Top`, `Softness` and the noise knobs, so most of a mist is reachable
from the op.

What is still not reachable: **`Layer Size`, which is 400 m by default** and which nothing but the
recipe sets. A camera at ground level therefore sits inside a quarter-kilometre of mist, and every
density from 0.004 to 1.0 renders the same solid wash, which is what says a domain is saturated
rather than a knob broken.

So: reach for `build_fog` with `preset: "ground_mist"` first. For a mist that has to lie in a
hollow, drive `volumetrics` directly through `build_geonodes` and set the domain: `mode:
height_fog`, `size` about the tile width, `thickness` under 10 m, `height` just above the valley
floor so the camera looks over most of it.

### 8. EEVEE will not light from emissive geometry; Cycles will

Any practical light that is emissive geometry needs Cycles. Use EEVEE for composition iteration and
Cycles for anything where the light matters.

### 9. A generated texture set is about three stops brighter than the block-out tint it replaces

A set is a tint multiplied by an albedo map. Swapping a 0.16-albedo block-out tint for a mid-grey
photographic map raises the surface hard: the first forest floor and footpath read as pale concrete
under a moon. **The fix is the prompt and its negative**, not a knob. Ask for "very dark", "wet",
"low key", "unlit", and put `bright, sunlit, pale, washed out, grey concrete` in the negative.

### 10. Leaf cards: many small, not few big

A card is two triangles. The shipped species presets card a tree with 2 or 3 cards per tip at 0.55
to 1.0 m each, which is a good block-out and reads as **cut paper** in a frame. Override to roughly
6 or 7 cards per tip at 0.17 to 0.24 m. Same silhouette area, an order of magnitude more edges.

Bark scale comes down for the same reason: 0.7 m per repeat on a trunk 40 m away is one furrow per
twenty pixels, so no bark reads at all. Try 0.26 to 0.3.

### 11. A path reads as the absence of undergrowth, not as a colour

A `dirt_path` band is 0.13 albedo and will never catch a moon. What makes a path is the cleared
band, and it has to be path-width: the scatter clears against the curve mask, so a wide `falloff`
strips a 13 m swathe and the frame comes back as bare ground with no path in it. Roughly 2.8 m of
tread plus a 1.6 m falloff gives a 6 m corridor -- walkable, visible along, and the canopy still
closes over it. **Grass has to clear against it too**, or there is undergrowth everywhere and still
no path.

Tune the numbers through `make_curve`'s `shape`, and **never by switching `role`**. A role carries
its mask channel with it (`dirt_path` uses `bbt_curve_mask`, `road` uses `bbt_curve_mask_b`), so
narrowing a road by making it a path silently invalidates every scatter layer's `curve_attr` and the
clearing stops happening. Shape and identity are separate for exactly this reason.

### 12. Stay inside the cleared corridor with the camera

A camera 2.5 m outside it lets the scatter stand a trunk in the lens, and a third of the frame goes
black behind a tree the camera cannot see past. A metre off the centreline keeps the path as a
leading line and lets the corridor's own clearance act as a lens hood.

### 13. Generate bark and atlases under the names the species presets already ask for

`conifer` asks for `bark_conifer` and `leaf_conifer`; `broadleaf` AND `shrub` both ask for
`bark_broadleaf` and `leaf_broadleaf`; `grass_tuft` asks for `leaf_grass`. Generate under those
exact names and every tree of that species wears them on its next build. **There is no assignment
step to forget.**

### 14. `comfy_mesh` with `kind: "trees"` is for dead wood

It returns one solid mesh: no leaf cards, no alpha, no branch hierarchy, no skeleton to grow one
from. A standing tree comes back as a faceted opaque fan. Use it for stumps, fallen logs, snags and
root balls. Live trees come from BobFoliage, always.

### 15. Placing a single generated mesh

`import_generated` has no `location` and no `collection`: it always lands the asset in
`BOB_Assets_<Kind>`, which is an off-scene pool. **There is no op that places one instance of it at
a point.** The route that works:

1. `import_generated` with its own `kind`, e.g. `kind: "structure"`, so it gets a pool of its own
   (`BOB_Assets_Structure`) instead of sharing one with the rocks.
2. `make_curve` a two-point curve at the site, on the terrain.
3. `build_geonodes` recipe `scatter_along`, `curve` that curve, `emitter` the terrain, `assets` that
   pool, `spacing` longer than the curve so it places one, `yaw` to face it, and
   **`min_scale: 1.0` with `max_scale: 1.0`**.

Those last two are not optional and they are the trap inside the trap: `scatter_along` ships Min
Scale 0.8 and Max Scale 1.2, so the default randomises the one asset whose height you measured,
scaled and reported. A hero structure placed on the defaults is somewhere between 0.8 and 1.2 of the
size the manifest says it is, and nothing in the frame says so.

`yaw` is an addition to the heading, not an absolute: with `align` on (the default) instances yaw to
the curve's own direction first, so the two-point curve's bearing is half the rotation and `yaw` is
the rest.

`scatter_along` projects onto the terrain and keeps instances upright, so this also solves the
absence of a rotation param. **Verify the instance count is 1 off the depsgraph** rather than
assuming it.

### 16. `import_generated` consumes its staging directory

`cleanup` defaults to true and deletes it on success, so the second run of the same op list fails
with `staged mesh missing`. Pass `cleanup: false`.

And the pack copy is **not** a substitute for the staging: the packed GLB carries the whole LOD
chain, so re-importing it came back at 8079 faces against 4898 with a UV overlap of 1.63 against
0.000011. To restage, re-run `comfy_mesh` with the same prompt and seed, which is why the manifest
records both. The staging folder is named `<slug>_s<seed>`, so an existing one tells you the key it
was made with.

### 17. Blockout proxies are `add_mesh` cubes, and they cannot be the right shape

Two halves to this.

Do **not** call `make_proxies` for a pool you are filling with generated assets. It fills the same
`BOB_Assets_<Kind>` collection with grey block-out blobs and the scatter instances both.

And `add_mesh`, which is the op left, takes a `location` and one uniform `size` -- no scale, no
rotation, no per-axis dimensions. **A 9 by 13 by 6 m structure cannot be proxied at its true
proportions over MCP.** Make the cube at the measured HEIGHT, say plainly at gate B that its plan is
a square and the real footprint is not, and lean on the measured numbers instead: footprint relief
and the camera-to-base sightline (section 4) are what the siting actually turns on, and both are
raycasts against the terrain rather than reads of the proxy.

### 18. Measure the ground, do not read the heightmap

Ray cast straight down onto the **evaluated** terrain object. The mesh itself is a flat grid until
its geometry-node modifier displaces it, so an un-evaluated read is wrong by the whole relief, and
reading the heightmap's pixels means guessing its row order too.

### 19. Site a footprint by search, not by hand

A 9 by 13 m footprint on rolling ground has no height that works for every corner, and BobSplines
cannot flatten one for it: the follow-terrain roles level TO the live terrain under their centreline
rather than imposing a height on it, so a yard track cannot grade a pad. Only the water roles
impose, and they bring a river.

So scan candidate spots and score each on two measured numbers: footprint relief, and whether the
camera can see the foot of the subject. The first siting by hand put a building in a hollow whose
crest wanted a camera 17 metres in the air to clear it. A prompted macro mask with a flat knoll in
it took the same footprint from 2.21 m of relief to 0.20 m -- which is the terrain generator doing
the siting for you, and the best available answer.

### 20. A lit plane coplanar with a wall clips at any strength

An emissive doorway flush with the siding has no edge to shade, so it goes flat white wherever it is
set, and dimming it until it stops clipping leaves nothing to light the scene with. Give the opening
a thickness -- a jamb standing proud, and a lit area a fraction of the opening rather than all of
it. A slot a tenth the area can be ten times brighter and still hold its colour.

### 21. A generated mesh's receipt now says whether it shipped closed. Read it.

`import_generated` used to return a face count, a height and `warnings: []`, all true, and none of
it described the surface. An asset gate shipped five meshes carrying 48 to 229 boundary edges
on that receipt, and the stump's holes were found in a hero render.

The route is fixed -- the weld and the pinhole fill now run on the mesh that ships -- but the
residue is real and it is the number to read: **`low_boundary_edges` on a solid kind should be near
zero, and you get a warning above 1% of the face count**. After the repair the gate assets sit at 9,
11, 17, 72 and 73. The 72 and the 73 warn, and they warn because a rock slab and a tree stump at
2.5% and 1.9% are still see-through at a close camera. That is a REGENERATE signal, not a rounding
error.

Two figures beside it, for reading the same receipt:

- `source_boundary_edges` is the generated mesh before any repair, and it is the honest openness of
  what the model returned. Compare with `pinholes_closed` to see how much Bob could fix.
- **Never quote a boundary-edge count off an unwelded mesh.** glTF splits a vertex at every UV seam,
  so an unwelded import reads several times its real openness: the stump measures 3,646 unwelded
  against a real 229. Both the pipeline figures are welded; a count you take yourself may not be.

### 22. A colour bake can succeed and return something else. `bake_fidelity` is the check.

Nothing else in the pipeline catches this, because a misaligned or resampled colour transfer still
writes a plausible texture: the map has the right average, the right histogram and no error
anywhere. A generated gabled structure shipped with its shingle courses shredded into a chevron hash, at
correlation **0.817** to the texture it came from, and every other check passed.

`import_generated` now returns `bake_fidelity: {correlation, mean_abs_diff, coverage}`, measured
in-chart against the source, and warns below **0.99** correlation or above **3.0** of 255 mean
absolute difference. A clean self-bake scores 0.998 to 0.9995. If it warns, the texture is not the
one the generator made and no amount of prompt work will fix it.

### 23. Look at the reference before paying for the geometry

`comfy_mesh(subject_only=True)` stops after the reference image and hands back its path. Every
geometry graph conditions on that picture and none of them reads your prompt, so the reference IS
the asset, and a bad one is only visible as a bad mesh two hundred seconds later.

One gabled structure took three seeds: one came back a cropped close-up of a wall, one the whole
structure standing on a display plinth with a toy car beside it, one was right. The subject stage cost about 8 s each and
the geometry stage cost 81, 435 and 113 s. Two of those three geometry jobs were spent on pictures
that would have been rejected on sight.

Accept one by passing its path back as `subject=<path>`, which runs the geometry against exactly the
picture that was approved. Reject one by calling again with a different seed. It takes the TEXTURE
VRAM floor rather than the mesh floor, so it still works on a card too low to generate geometry on
-- which is exactly the state a gate ends in (trap 2).

Use it for anything hero, and for any prompt where framing matters more than surface.

### 24. A generated albedo is a photograph, and a photograph has light in it

The prompts already ask for flat even lighting and the model does not always comply. Nothing said so
until it reached a hero render: a structure's reference came back an overcast outdoor photograph with
a sky gradient and an eave shadow baked into the siding.

Every texture tool now reports `flatness.low_freq_variation` and warns over **0.075**. Across ten
sets the flat ones measured 0.025 to 0.074 and the two visibly lit ones 0.0965 and 0.0989. Pass
`delight=True` to divide the lighting out; it preserves the mean, so trap 9's brightness advice
still applies unchanged.

**Leaf atlases are the case that matters most**, because a card is lit from both sides and there is
no camera angle that hides a key baked into a sprite. Read `flatness.in_mask_stops` -- how many
stops the albedo spans inside the cutout, where a real leaf varies by a fraction of one. The three
atlases that gate shipped measured 1.21, 1.82 and 1.84. Delighting brought the broadleaf to 0.92 and
barely moved the other two, which is the case where the answer is **reroll**: their variation is
per-needle shading and four different sprites, not a low-frequency ramp.

---

## 6. Before you start: the two environment checks

Both were skipped once and cost real time.

**ComfyUI.** `comfy_status()` first. If it reports `not reachable`, it is not running -- start it
rather than working around it:

```sh
cd /home/siva/dev/ComfyUI && ./venv/bin/python main.py --listen 127.0.0.1 --port 8188
```

Use `venv`, not `.venv`: the second one is missing `sqlalchemy` and dies on import. Wait for the
port with an `until curl` loop, then confirm with `comfy_status()` again -- it reports the device
and free VRAM. Identify the process by the **listening socket** (`ss -ltnp | grep 8188`), not by
`pkill -f`, which will match its own shell wrapper and report success while the server keeps
running.

Stop it **before the gate C Cycles render**, not when the scene is done: it holds about 7.3 GB it
will not hand back, and only a restart recovers the card (trap 2). Reserve the last generation for
before the stop, because starting it again is a minute and a model load.

**The live bridge.** `describe_scene` with `include: ["packs"]`. If it reports
`no live bridge on 127.0.0.1:9876`, the bridge is off: Advanced panel, Start.

And know which reload you need, because picking the wrong one wastes a cycle:

| Changed | Needs |
|---------|-------|
| A recipe or builder body | **Reload Builders** (Advanced panel) |
| A new op, or new fields on one (`mcp_agent/contracts.py`) | Client **reconnect**, `/mcp reconnect bobblendermcp` |
| A new `params` key on an existing op | Reload Builders only. `params` is a free-form dict, so it needs no contract change |

Recipe names are a plain `str` in the contract too, so a **new recipe needs Reload Builders and no
reconnect**.

---

## 7. Output conventions

- Renders: `renders/<project>/<YYYYMMDD>/`.
- Bakes and probes: `_generated/`.
- Generated assets: the generated pack, `packs/generated/`. Pass the `pack_dir` every generation
  tool returns into `apply_texture_set` and `import_generated`, or a freshly generated set is
  invisible and the op fails on a folder that exists.
- Gate A's contact sheet: `tools/scripts/render_foliage_gallery.py` and `render_foliage_stand.py`
  already shoot a lit, labelled row of assets. Read them before writing a layout pass by hand.
- Write the whole scene as one op list and save it as JSON. It is the deliverable: it replays into a
  live session, it diffs, and it is the only artifact that says what the scene actually is.
