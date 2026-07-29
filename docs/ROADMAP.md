# Roadmap: what is not single-sourced yet, and why it matters

[ARCHITECTURE.md](ARCHITECTURE.md) describes what is built. This describes what is deliberately
**not** finished, in one place, so an open item cannot hide inside a doc that reads as complete.

Everything here is either a constraint that has to stay true for a future split to be mechanical, or a
named gap with the reason it has not been closed. Nothing here is a phase.

---

## The extraction constraints, and the one-way rule that keeps them cheap

Two subsystems are meant to become their own repos without a rewrite: the terrain heightfield engine,
and the Blender authoring side behind MCP. That stays cheap only while these hold, and each one is a
thing to check in review rather than an aspiration:

- **`core/heightfields/` imports numpy and scipy, plus optional CuPy. No `bpy`, no MCP, no config,
  and no PIL** — the 16-bit PNG codec is pure numpy plus `zlib` precisely because the compute runs
  inside Blender's bundled Python, which ships no PIL. Absolute paths in, files out. That is the
  whole reason it is extractable, and it is why the compute lives in the extension and is never
  copied into a venv: **one committed source**, shared by the in-Blender path and the venv golden
  tests, so there is no duplicate to drift.
- **`core/` imports only `bpy`.** No MCP, no venv code.
- **The framework (contracts, executors, bridge, MCP server) knows each subsystem only through op
  models and file artifacts**, never internals.
- **The seam is always the op contract plus files on disk.** JSON and files cross a process line;
  in-memory objects never do. A subsystem in another repo therefore behaves exactly like one in-tree.
- `check_selfimports.py` enforces the import half of this in CI: every intra-package import is
  relative, because the package loads under two fully-qualified names (`bl_ext.<pkgid>.…` live,
  `bob_blender_tools.…` headless) and an absolute self-import resolves in only one of them and dies
  silently in the other.

**The router that was cut, and why it stays cut.** An earlier design had a single `apply(ops)` that
split a mixed op list across the venv and Blender. It bought one thing — a mixed list in one call — at
the cost of undefined partial-failure state, a blocked MCP server during long bakes, and broken bridge
atomicity. `contracts.py` stays the one vocabulary; where an op runs is a property of the op, resolved
by the caller. A plugin registry earns its keep the day there is a real second consumer, and not
before.

## Determinism, and where it is deliberately given up

- **The CPU path is the bit-deterministic reference** and what the golden tests assert.
- **The GPU path is not bit-identical run to run**, because `atomicAdd` ordering is not deterministic.
  That is accepted, not a defect. **The cache key includes the backend** so the two can never alias.
- Generation stays on CPU: it is cheap, seeded and deterministic. The GPU win is the droplet kernel,
  not the grid passes.
- CPU stays selectable with CuPy present (`BOB_HF_BACKEND=cpu`, `select('cpu')`), so the deterministic
  reference still holds on a GPU box.
- 16-bit PNG plus a params sidecar means **any heightfield is reproducible from the recipe that made
  it**, which is the same provenance rule the generated assets follow.

## Open: an agent's work does not appear in the panels. THE highest-weight item

**Weight: highest. Written from the ARTIST's side, because the previous version of this section was not
and that is why it missed the actual complaint.** An earlier draft listed what the agent cannot do —
trigger a reload, place an instance, observe a change — because it was written from the agent's seat.
The artist's report is the opposite direction and it is the one that matters:

> "When MCP makes terrain and scatters I don't see anything in the scatter panel. No trees shown in the
> foliage panel."

### The symptoms, in the artist's words

- An agent scatters. **The scatter panel is empty.**
- An agent grows a tree. **The foliage panel shows no species.**
- Selecting anything the agent built shows the panel's own defaults, so **nudging one slider rebuilds
  from panel state and silently discards everything the agent set.**
- The same is true of the artist's OWN work from a previous session. This is not really about agents.

### The two mechanisms

Both are architectural, neither is drift, and neither was in this document before 2026-07-30:

- **The panels are build-only. They own state; they never read an object's.** The foliage panel drives
  `scene.levels`, `scene.profile_segments` and the rest, then builds from those. An agent-built tree
  carries its parameters on its geometry-nodes modifier. **Nothing reads modifier state back into a
  panel.** So a panel cannot show any existing object's build — the agent's, or its own from yesterday.
  It was never designed to read, only to write.
- **MCP cannot name a species, so an agent's tree has no species identity to show.** There is no op for
  "grow the conifer preset": over MCP a tree is `build_geonodes` with the `foliage` recipe and the
  species' whole parameter dict expanded by hand (`PROCESS.md` §2). The object therefore never records
  that it IS a conifer. The foliage panel displays nothing because the fact was never written down —
  this is a missing vocabulary entry, not a missing UI.

### What would fix it, in the order that changes how the tool feels

1. **Objects record their recipe and their params durably**, including a species NAME where one exists.
   Without this there is nothing for a panel to read.
2. **Panels adopt the selection.** Select a Bob-built object and the panel populates from what is on it.
   This one change makes an agent's work editable by hand, and last session's work editable today.
3. **MCP gets the vocabulary the panel already has** — grow a species by name, place one instance at a
   point — so an agent is not forced to hand-expand things the panel expresses directly.

### The agent's half of the same problem

Real, and secondary to the above. Kept because each cost time:

- **The agent had to avoid the artist's session to work safely, two rounds running.** A live session
  held an in-progress approval gate and predated the round's code changes, so every generation, finish,
  measurement and render was done headless on purpose. Two divergent states of one project, and no way
  to reconcile them except by the artist reopening things by hand.
- **The reloads are manual artist actions an agent cannot perform or even observe.** A changed builder
  body needs **Reload Builders**; a new op or new fields on one needs a client **reconnect**. So an
  agent that fixes a recipe cannot make the artist's open session see the fix, cannot tell whether it
  has, and has to end its turn with an instruction instead of a result. The reload matrix in
  [SCENE-BRIEF.md](../references/SCENE-BRIEF.md) §6 exists because picking the wrong one wastes a cycle
  — that matrix is a symptom.
- **There is no change feed in either direction.** `describe_scene` reports what IS, never what
  CHANGED, so an agent cannot ask "what has the artist done since I last looked" and cannot treat a
  hand edit as intent. And the artist cannot see the agent's op list as editable session history: the
  op list is the deliverable and replays into a session, but it is not what the session holds, so a
  hand tweak and a replay are two truths.
- **An agent-made asset does not land where an artist would put it.** `import_generated` always lands
  in an off-scene `BOB_Assets_<Kind>` pool and **there is no op that places one instance at a point**
  — the route is a two-point curve plus a `scatter_along` with the scale range pinned. An artist drags
  it into place; an agent builds a scatter to fake a drag. The same gap made this round's first render
  four frames of an empty white world, because the object was in a pool and nothing said so.
- **A whole texture route is reachable from NEITHER surface, and a docstring says otherwise.** The
  barn's texture comes back with door panels painted onto its roof because `mesh_texture` conditions on
  one image and invents what that image cannot see. The fix built for exactly that is the "stylised"
  texture route — `comfy.paint_views` plus `core/gen_paint`, which paints from turntable views so every
  surface is painted by a camera that can see it. `comfy.texture_chain()` selects it and
  **`texture_chain` has no caller anywhere outside its own definition**; `gen_paint` has exactly one,
  `tools/scripts/headless_gen_stylise_paint_multiview.py`. There is no panel operator and no MCP tool.
  `comfy_paint_mesh`'s own description asserts the stylised route "stays a panel action (Stylise)
  rather than an MCP tool" — the panel action was never built, and the only Stylise operator,
  `comfy_stylise` / "Stylise Last Render", restyles a 2D RENDER rather than painting a mesh. So the
  parity failure here is not agent-versus-artist: it is a capability that exists, is documented as
  belonging to one surface, and belongs to neither. A gate script is the only caller, which is why the
  gap survived — the route is exercised, so nothing reads as dead code.
- **One name already resolved to two answers.** A live session resolved its generated pack to a folder
  inside the ComfyUI checkout while the MCP tools wrote into the repo, so the trees would have worn
  whatever stale sets were in the wrong folder (`PROCESS.md` §1). Two consumers, one pack name,
  different roots — the parity failure in its cheapest form.

### What "1:1" would have to mean, beyond the three above

- **Every capability on one surface exists on the other**, checked mechanically rather than by
  intention — the contract and the panel drawn from one source, or a test that fails when either grows
  something the other lacks.
- **The agent can trigger the reloads it depends on**, so a code change it made becomes live without a
  human clicking, and can read back whether the live session is running the code it thinks it is.
- **A change feed, ops-shaped, both ways.** Artist edits become ops or at minimum a readable diff; the
  agent's ops become session history the artist can inspect and undo.
- **One resolution of every shared name** — pack roots first, since that one has already bitten.

The open design question is which way parity is enforced: generate the panel from the contract (one
source, larger change), or keep two surfaces and fail CI when they diverge (cheaper, and it only
catches capability parity, not state parity). **State parity is the harder half and it is the half the
artist is complaining about** — read-back is state parity, not capability parity, so the cheap option
does not touch it.

Worth stating plainly, because it explains why this went unnoticed for so long: **every op in the
vocabulary writes, and nothing reads.** `describe_scene` is the one reader and it reports to an agent,
not to a panel. A tool built entirely out of one-way operations feels divergent the first time two
people use it, and it will feel divergent to one person across two sessions.

## Open: the GPU install surface

The highest-risk surface in the whole extension, and the one most likely to be encountered by an
artist rather than by a developer. GPU acceleration is a first-class capability, not an optional
extra, so the one-click install has to handle the real matrix: NVIDIA CUDA 11.x against 12.x against
13.x, AMD ROCm, no driver at all, offline, and a Blender Python that blocks writes to its
`site-packages`.

- **Each failure must degrade to CPU with a specific message.** CPU fallback is the safety net for
  those failures and for GPU-less machines; it is NOT a reason to treat GPU as skippable.
- **The two unknowns to spike before touching this**: target CUDA-line detection, and
  pip-into-Blender-Python. The whole "GPU works out of the box" promise rests on them.
- **Verify a wheel install survives a Blender version update**, because the bundled Python can change.
- **Decide the AMD/ROCm support tier explicitly** — fully supported, or CPU-only for now — rather than
  leaving it implied.

Reference point that already works: `cupy-cuda13x` selects an RTX 5080 (sm_120, CUDA runtime 13.2),
array math matches numpy bit for bit, and an `atomicAdd` `RawKernel` compiles via NVRTC for sm_120 and
scatters correctly. 1.5 to 2.5 million droplets at 768 bake in about 2 s. That is one machine, not a
matrix.

## Open: preference persistence across a packaged install

The asset-pack folder list and the Output Folder live in `AddonPreferences`. Confirm they survive the
packaged (non-symlink) install and a version update. Untested, and cheap to test.

## Open: old-scene migration

The rename from the pre-restructure module names was Python-module-level, and scene properties
(`bbt_*`), node groups and drivers key off bpy data names rather than Python paths, so pre-refactor
`.blend` files should keep working. **Should** is doing work in that sentence. The smoke test is: open
a scene saved before the refactor, re-run a build, diff the geometry.

## Closed: the in-Blender test job

`tools/tests/run_blender_tests.py` exists and `.github/workflows/ci.yml`'s `blender-headless` job is
on. It drives the gates that need `bpy` and nothing else — the sun gate, the texture-set check and
the scene-seams gate — for **85 checks in about 6 s of Blender**, one process per gate. Everything in
`tools/tests` still runs in the plain venv against the pure-python core, which is what keeps the fast
gate honest without Blender; this is the second job beside it rather than a replacement.

What is still not in CI, and the rule that decides it: a gate qualifies only when it needs `bpy` and
nothing else. `headless_foliage.py` is the one that hurts — it is the largest gate in the repo — but
it takes 150 s and reaches for ComfyUI when one is running, so on a CI box it would be slow AND
measuring something different from what it measures locally. The generation gates are out for the
same reason, one step further: they need a GPU and a ComfyUI. Those numbers still come from a local
run, and the way to bring one in is to make it need less, not to let CI skip it quietly.

## Open: the one thing the asset gate measured and did not fix

Everything else that gate found is closed: the mesh repair and the coincident bake in
[GENERATION.md](GENERATION.md) (the one-shot route), the lit albedo in its texture-set section, the
bark shear in [FOLIAGE.md](FOLIAGE.md), and `subject_only` on `comfy_mesh`. One is left, and it is
left for a reason rather than for time.

- **The bake cage is still a guess, on the one route that still uses it.** 2% of the longest
  dimension with rays reaching 8%, which is far too loose for two near-coincident surfaces. The
  coincident and colour-from-low paths now bake with no cage at all, so this is only read where
  there is a genuinely denser mesh to cross to — the staged and block-out routes. The honest version
  measures the actual separation between the pair and sizes the cage from it. Not done because
  **there is no staged asset on disk to measure a change against**, and changing a cage by reasoning
  alone is how the misaligned bake survived two gates. It needs one staged-route generation, which
  needs ComfyUI.

## Closed: the block-out control route painted black and lost the walls. Open: what it paints instead

Both halves of that rejection are root-caused, fixed and verified on the barn itself (2026-07-29).
Kept here because the CAUSES are worth not re-deriving and because what is left is a different
question from the one this section started as.

- **Black albedo: the mesh arrived in the wrong unit cube.** Omni returns [-1, 1] where TRELLIS.2
  returns [-0.5, 0.5]; `mesh_simplify_uv` rescaled nothing and had no normalise node; its only
  consumer is `mesh_texture`, whose encoder voxelises in the unit cube. So the encoder was handed a
  mesh entirely outside its grid. Measured: control 1.00000, raw 1.99361, **simplified 1.99333**, and
  an albedo at spread 3.46 / mean 0.06. With `GeomPackNormalizeMeshToBBox(1.0)` added to that graph —
  the node `mesh_process.json` already carried for this exact reason — the simplified mesh measures
  0.99998 and the albedo 59.3 / 172.05. Third distinct cause of the same black albedo, and the
  precondition was written in both graphs' notes while nothing enforced it; it is now asserted over
  the two graphs that feed `mesh_texture` as a set.
- **The walls: the block-out was conditioning on its own interior.** The point control is an
  AREA-WEIGHTED surface sample, so a hidden face is a conditioning point like any other. `_shed` was
  a wall cube with a roof prism on top, which put the wall box's top (56.0 m²) and the prism's
  underside (67.0 m²) inside the building — **125.94 m² of 425.98, 29.6% of every control point** on a
  slab at wall height that is not there. Rebuilt as one closed shell: 2.95 m² of 313.98, **0.9%**, same
  8.7 × 7.7 × 7.5 m bbox, same zero non-manifold edges. The generation against it holds a **profile
  max band deviation of 0.0753** against a 0.10 bar, where a synthetic A-frame at the same bbox scores
  0.2551. `control_mode="voxel"` and a higher guidance scale were not needed and stay untried.
- **Two gates came out of it, both calibrated, neither needing a server.**
  `blockout-control` part A gates hidden surface at 5%, and part C gates the height profile band by
  band. The intuitive form of that second one — mean plan area over the lower half — is reported and
  deliberately NOT gated: it reads 1.1127 on the A-frame, above 1.0, because a roof slope at knee
  height covers more plan than a thin wall ring does. It would have passed the exact shape it was
  written to catch.

**What is open is the SURFACE, and it is a different defect from the black one.** `mesh_texture`
conditions on one reference image and invents every surface that image cannot see: the barn's roof
slope came back carrying door panels and X-bracing painted from the reference's gable elevation, and
the gable siding reads as stucco rather than as vertical boards. Resolution is not the cause (this ran
at 1024; 2048 paints the same invention sharper). Two routes are untried and one of them is what the
brief already asks for: `comfy_paint_mesh` / `mesh_paint_views`, which paints from Blender-rendered
views so every surface is painted by a camera that can see it; and `comfy_texture_set` per material,
which is [SCENE-BRIEF.md](../references/SCENE-BRIEF.md)'s own manifest row 8 for structure surfaces and
which cannot hallucinate a door onto a roof, but which needs the generated mesh to carry material slots
the sets can key to.

**One receipt figure misleads on every Omni route and is not yet fixed.** `low_boundary_edges` reads
the mesh AS IT SHIPS, and this route ships a mesh straight from `mesh_simplify_uv`, which neither welds
nor repairs — so every UV seam glTF split a vertex at reads as openness. The barn reported **1072 with
an empty warning list** while `low_openness`, which welds a copy of its own, reported 0 loops and 0
see-through edges, and `source_boundary_edges` reported 0. Previous round: 1,187 unwelded against 24
welded. The number that misleads is the one with the prominent name.

**And an ordering exception that no preflight can catch** (full measurement in
[GENERATION.md](GENERATION.md), the VRAM-handback rule): once Omni has run, the SDXL atlas route OOMs
whatever the card reports free — 6530 MiB reported, main process expanding to 12.80 GiB, dead at 162
MiB, on two fresh servers in a row. There is no number readable at queue time that predicts it, only an
ordering: atlases and texture sets BEFORE the block-out-conditioned structure. The brief's "hero
structure first, while the card is emptiest" is right about TRELLIS.2 and backwards about Omni.

Two smaller calls were made deliberately and are recorded where they apply rather than here:
`profile_segments` stays 5 on the shipped conifer, because the shear that argued for raising it is
fixed and the remaining argument is faceting at hero distance (FOLIAGE.md); and `AO_STRENGTH` stays
at 0.6 with Albedo × AO unchanged, because the suspected double-count measured as absent — the AO's
source field is a high-pass at a thirty-second of the image and the delighting corrects at an
eighth, so the two cannot overlap (GENERATION.md, and the comment in `core/materials/texset.py`).

## Open: reliability is a per-class PASS RATE, and it was never measured

Every other gate here asks "does this route work". None asked the question a user has: **if I generate
a thing of this kind, what are the odds it comes back usable?** That is a rate over samples, and its
absence is why three rounds of one scene's asset list became three rounds of fixing one barn — every
finding arrived attached to a specific asset and got fixed at that asset's scope.

`tools/scripts/headless_gen_class_rates.py` measures it. Six classes, eight fixed ordinary prompts
each, and a pass is defined as the asset's own receipt returning `warnings: []` — deliberately not a
judgement of mine, which also means the rate DROPS when a real gate is added, and that is correct: an
asset that would have shipped a defect silently was never a pass.

First run, and it discriminates, which is the only property that makes it worth anything:

| class | n | pass | rate | most common failure |
|---|---|---|---|---|
| bark | 3 | 3 | **100%** | — |
| tileable | 3 | 3 | **100%** | — |
| atlas | 4 | 0 | **0%** | orientation-guess |

The atlas row is the finding, and it corrects a claim made from a hand-tuned sample: with the plain
prompts a user would actually type, the class is 0/4. The wording that fixes it — "flatbed scan on a
plain white background, flat even light, no shadows", plus asking for FRESH GREEN leaves so the
green/brown split the woody cue reads actually exists — was discovered by hand and lives nowhere. That
is precisely what `ATLAS_SPRITE_SUFFIX` exists for: a clause the artist should not have to remember.
Folding it in and re-measuring the rate is the first class-level fix this harness has earned, and it is
the shape every fix should have from here — move a rate, not rescue an asset.

Not yet measured: `rocks`, `deadwood` and `structures`. Those are mesh classes at 60 to 200 s a sample,
so a full eight-sample run of all three is roughly an hour of card time and has to be scheduled rather
than slipped into a session. The expectation on file is that rocks and deadwood are high and structures
is low, and the honest product answer if structures measures low is that the tool routes structures to
recipes and generates only their surfaces.

## Closed: two measurements that only a gate script could read

Both found by auditing for the mistake described above, and both pre-existing rather than mine:

- **Bark grain direction.** `grain.off_vertical_deg` was gated at 25 degrees in
  `headless_foliage.py` and nowhere else — `TEXTURE_INFORMATIONAL` said so in as many words, and
  `comfy_bark_set`'s own description told the artist the bar lived in a gate script. So a set could
  ship with its grain running across the trunk and a clean receipt. Now `gen_receipt.grain_warning`,
  and `grain` moved from informational to gated. The gate script keeps its own version, and the split
  is the rule worth stating: **a gate script should assert properties of the CODE — that a prompt
  clause still works, that a route is wired — and a receipt should judge the ASSET.** The same figure
  belongs in both for different reasons.
- **Blank atlas cells.** `headless_foliage.py` gated `all(c["opaque"] > 0.02)`; the receipt had no
  sentence, so an atlas could ship a cell with no sprite and the card built on it renders as nothing.
  Now `gen_receipt.blank_cell_warning`, declared beside `orientation_warning` on the same key, because
  a cell can be blank and correctly oriented or full and upside down and one sentence would name the
  wrong remedy half the time.

And one that was mine, moved the same way: the block-out hidden-surface check now lives in
`gen_assets.export_control`, which takes ANY object, rather than in a gate script that only ever saw
the shipped shapes. Verified on an artist-style block-out built as a wall cube with a roof prism
dropped on top: 29.4% of its surface interior, warned; the shipped shell 0.9%, silent.

## Open: the bar vocabulary is not auditable, and an artist cannot tell which numbers to trust

**Weight: high, and it is the reason the item below went unnoticed for weeks.** The artist's report:

> "so much slop. magic numbers in core. I don't even understand what's happening."

That is a fair reading of the current state. There are roughly twenty bars deciding whether a generated
asset passes — spread across `core/gen_receipt.py`, `core/comfy_maps.py` and the gate scripts under
`tools/scripts/` — and every one is justified in a prose comment beside it. To audit them you have to
read all of it end to end, and there is no way to ask "which of these rest on thin evidence?"

The cost is measured, not hypothetical. `LEAF_RAMP_STOPS_MAX` rested on **two** samples, one of them
synthetic, and rejected **8 of 10** ordinary atlases — an entire asset class reading as 0% on the
pass-rate harness — and nothing surfaced that until somebody counted by hand. The information was in a
comment the whole time. **Comments do not fail.** Five more bars were added on 2026-07-30
(`HIDDEN_AREA_MAX`, `PROFILE_DEVIATION_MAX`, `CELL_OPAQUE_MIN`, `GRAIN_OFF_VERTICAL_MAX`,
`CONTROL_HIDDEN_MAX`) in exactly the same style, which made the problem worse while fixing others.

What it wants, and none of it is decided:

- **One registry, structured rather than prose.** Per bar: value, unit, what it catches, how many REAL
  samples are behind it, how many are synthetic, when it was last re-derived, and which receipt sentence
  reads it. The prose can stay in docs; the machine-readable facts have to be machine-readable.
- **A test that fails on thin evidence** — a bar gating shipped assets on fewer than about five real
  samples is a guess wearing a threshold's clothes, and it should have to say so out loud or be
  downgraded to informational.
- **The bar table generated from the registry**, not written by hand, so it cannot drift from the code.
- **Pass rate per bar, from the harness.** A bar that nothing fails is not protecting anything; a bar
  that everything fails is a blocked route. Both are visible only once the rate exists per bar rather
  than per class.

This is also the mechanism that would keep `MESH_GATED` / `TEXTURE_GATED` honest. That registry already
proves the pattern works — it fails CI when a receipt key has no reader, and it caught a mistake on
2026-07-30 within minutes. The bars need the same treatment: the question there was "does this number
have a reader", and the question here is "does this number have evidence".

## Open: three generation bars rest on a single batch, and say so at the constant

Every other bar in the generation vocabulary is set from a spread wide enough to argue with —
`gen_receipt.METALNESS_MAX` from ten samples spanning 0.0002 to 0.83, `gen_receipt.MAP_SPREAD_MIN` from seven
maps spanning 0.00 to 57.51. Three are not, and each of them caught a real defect, so they stay as
gates. What they need is a second batch to be re-derived from, and until then the thinness of the
evidence is written beside the number rather than left for a reader to discover:

**`LEAF_RAMP_STOPS_MAX` now has its second batch, and the batch says the bar is wrong.** Ten real
atlas sheets measured at `in_mask_ramp_stops`: 0.235, 0.305, 0.627, 0.692, 0.711, 0.978, 1.075, 1.153,
1.317, 1.402. **Two of ten pass 0.55.** A bar that rejects 80% of ordinary requests is not a gate, it is
a blocked route, and the pass-rate harness reports the class at 0% because of it.

The derivation is the problem rather than the number. 0.55 came from the worst per-cell figure over the
three atlases the asset gate shipped — and those three were generated with the "pressed flat like a
herbarium specimen" wording, which suppressed the ramp precisely BECAUSE it flattened the subject, and
which is the wording that broke both orientation cues and cost an approval gate. So the bar was
calibrated on samples that were defective in the other dimension, and it has been rejecting every
atlas made correctly since.

Two ways out, and the second is a fix rather than a moved goalpost:

- re-derive the bar from the ten above, with the synthetic key runs (a 1.0-stop key took the gate's
  conifer from 0.48 to 0.72) as the thing it has to stay able to catch. Cheap, and it leaves a
  measurement nobody can act on.
- **divide out the ramp that is actually measured.** `comfy_maps.mask_light_split` fits a least-squares
  plane to log2 luminance inside the mask and reports its p5..p95 span — so the correction is exactly
  that plane, subtracted. `delight` runs today and does not clear it (measured: ran on every one of the
  ten, none reached the bar), because it removes a broad low-frequency field rather than the specific
  plane the gate reads. Fitting and removing the same plane the bar is written on makes the two agree
  by construction and takes the class rate off the floor for a real reason.

| Constant | Points behind it |
|---|---|
| `gen_receipt.LEAF_RAMP_STOPS_MAX = 0.55` | **10 real sheets now, and 8 of them fail it — see above. The original 2, one synthetic, came from atlases made with wording that was broken in the orientation dimension** |
| `gen_receipt.SEETHROUGH_OPENING_FRACTION = 0.10` | 2: a ground rock's 0.07 opening the artist accepted as vesicular stone, and a gabled structure's 0.18 the artist rejected in a render |
| `comfy_maps.AXIS_STRONG_TAPER_MAX = 0.25` | 1, and already marked provisional at the constant |

Recalibrating needs generation, not code: a second batch of atlases for the first, a second batch of
structures and rocks for the second. Both cost ComfyUI time and neither is blocked on anything else.

## Open: scatter items that were scoped and not built

Named so they are not rediscovered as new: a per-layer emitter override, an in-panel Make Proxies
affordance, and masking beyond slope (`Min Normal Z`) plus path clearing.

## Deferred, with the reason

- **Auto-reloading builders on file mtime.** Purging modules on a timer trades one button press for a
  class of stale-import heisenbugs. Reload Builders stays explicit.
- **A standalone ComfyUI packaging path.** It needs an external ComfyUI server regardless, so it stays
  dev-only. Ship an `httpx` wheel through the manifest only if standalone is actually wanted.
- **A plugin registry for op models.** Heightfield op models are imported into the contract union by a
  plain import, so extraction carries them. A registry earns its keep on the second consumer.

## The knob policy, because it is the thing most likely to be re-derived

On a non-destructive rebuild, **restore only the knobs the op did not explicitly set.** An op that
re-sends `density` wins, because the op is the intent; a knob the op leaves at its default keeps the
artist's sidebar tweak. Without that split, re-sent params would silently no-op.

The Blender 5.2 fact underneath it: **a Nodes modifier has no IDProperties at all** (`mod.keys()`
and `mod["Socket_1"]` both raise), so a knob's live value lives on the modifier's own input
interface, `mod.properties.inputs.<identifier>.value` — per object, since each build owns its group.
Snapshot and restore operate **there**, never on `mod[id]` and never on the node group interface
socket's `default_value`: that default only seeds a fresh modifier bind, and editing it after the
build does not re-evaluate. Restoring to the wrong one of the two silently drops every knob the
artist tuned.

One thing this does not remove: a **contract change still needs an MCP server reconnect**, because the
server parses `contracts.py` at startup. That is the tools-side half of a two-sided reload.
