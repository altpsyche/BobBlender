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

## Closed: an agent's work now appears in the panels. Open: the agent's half of the same problem

**Written from the ARTIST's side, because the version of this section that was not is why it missed the
actual complaint for so long.** An earlier draft listed what the agent cannot do — trigger a reload,
place an instance, observe a change — because it was written from the agent's seat. The artist's report
was the opposite direction and it was the one that mattered:

> "When MCP makes terrain and scatters I don't see anything in the scatter panel. No trees shown in the
> foliage panel."

Three things caused that, all three are fixed, and the causes stay here because they are the kind of
architectural mistake that grows back. Every symptom below was real:

- An agent scattered. **The scatter panel was empty.**
- An agent grew a tree. **The foliage panel showed no species.**
- Selecting anything the agent built showed the panel's own defaults, so **nudging one slider rebuilt
  from panel state and silently discarded everything the agent set.**
- The same was true of the artist's OWN work from a previous session. It was never about agents.

### The three causes, and what each is now

**1. The panels were build-only: they owned state and never read an object's.** The foliage panel drove
`scene.levels`, `scene.profile_segments` and the rest and built from those, so a panel could not show
any existing object's build — an agent's, or its own from yesterday. **Now structural config lives on
the OBJECT**: `Object.bbt_foliage_tree` (declared AND registered by `core/foliage_build.py`, because a
headless gate builds trees with no addon) and `Object.bbt_scatter_layer`, which gained the layer's own
`emitter` and `camera`. Five Scene properties and a 45-key JSON blob of every build param were deleted;
what stays on the object is only the five params that are Python arguments to the recipe, because every
other param is a modifier socket and the modifier already restores it by socket name.

**2. Nothing read an object back into a panel.** The panels now draw straight off the object, so there
is no second copy to disagree and no sync code either. A layer rebuild reads the emitter the LAYER
records — previously a rebuild silently re-bound the scatter to whatever mesh the panel pointed at.

**3. MCP could not name a species, so an agent's tree had no identity to show.** A tree was
`build_geonodes` with the `foliage` recipe and a species' whole parameter dict expanded by hand, so the
object never recorded that it IS a conifer. **Now `grow_foliage` (species by NAME, in-place
re-speciesing, pool collection, explicit seed) and `scatter_layer` (kind + emitter + curve modes)**,
and the two recipes those own — `foliage`, `scatter`, `scatter_along` — **REFUSE a raw `build_geonodes`
and name the op that owns them** (`core/geonodes` `OWNED_RECIPES`, one table, message generated from
it). The refusal is what keeps this closed: the route that produced an anonymous object is gone rather
than merely deprecated. It lives in Blender and not in the contract, because the contract is validated
in a venv with no `bpy` and a copy of the table there would be a second table to drift.

### How it was verified, because "it appears in the panel now" is a claim about a live session

Measured, not reasoned. The venv suite is at **326 checks**, the three in-Blender CI gates pass
(**scene-seams at 45**, which now asserts the ownership table, the refusal of each owned recipe, and
that the owning builder's own route still works), and the foliage gate holds at **269 checks**. Two
throwaway probes, deliberately not committed as gates, drove the parts a gate cannot reach: a
**recording draw harness** over every registered panel (34 registered, 30 drew, 4 polled out on an empty scene, 0 failed — the harness stubs the
layout API, per [CONVENTIONS.md](CONVENTIONS.md)'s panel-review rule), and an **agent-route probe** that
builds through core with no panel state and then asserts the panels list it, the species survives, a
Build keeps the agent's structural params AND the artist's hand-tuned knob, and a layer rebuild binds
to its own emitter. A third drove the two ops through dispatch end to end: 33 checks, including that a
re-sent op reuses its layer instead of doubling the count, that an unknown species and a curve-shaped
emitter are refused by name and by type, and that `curve_mode="along"` really places instances on the
curve.

One defect the narrowing exposed, worth keeping because it was invisible while the blob existed:
`variant_params` did not copy `Wind` off the source tree, on the argument that a variant picks the live
world's wind up at build time. True only when there IS a world — with Firmament absent `apply_wind`
reaches nothing, and the variant built at the recipe's default. The blob had been carrying wind through
by accident, so the gate could not have caught it.

### What is still open here

- **Place one instance at a point.** `import_generated` lands an asset in an off-scene pool and there
  is no op that puts a single copy somewhere; the route is still a two-point curve plus a scatter with
  the scale range pinned. `grow_foliage` takes a `location`, so a TREE can be placed; a generated mesh
  cannot.
- **State parity in the other direction.** The panels read the object now, but nothing tells an agent
  what the artist changed — see the change-feed item below.

### The agent's half of the same problem, which is what is left

Real, secondary to the three causes above, and now the whole of what is open in this section. Kept
because each one cost time:

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
- **A whole texture route was reachable from NEITHER surface, and a docstring said otherwise. CLOSED.**
  The barn's texture comes back with door panels painted onto its roof because `mesh_texture` conditions
  on one image and invents what that image cannot see. The fix built for exactly that is the "stylised"
  texture route — `comfy.paint_views` plus `core/gen_paint`, which paints from turntable views so every
  surface is painted by a camera that can see it. It had no panel operator, no MCP surface, and one
  caller: `tools/scripts/headless_gen_stylise_paint_multiview.py`. A gate script being the only caller
  is why the gap survived — the route was exercised, so nothing read as dead code.

  Both surfaces now reach it, through one implementation. `core/gen_paint.paint_stylised` is the route
  (render the turntable, restyle every view, project it back), the **`paint_stylised` op** carries it to
  an agent, and **Shaders > Paint (stylised)** carries it to the panel. The op rather than a `comfy_*`
  tool because Blender is in the middle of the route, which is the same rule that makes
  `import_generated` an op. `comfy.texture_chain()` is deleted: it returned one of two functions with
  different signatures, so nothing could call what it returned without knowing which it had got, and in
  the event nothing called it at all. The route is still a NAME in one place (`comfy.TEXTURE_ROUTES`);
  what a name reaches is a whole entry point.

  Measured end to end through the op on 2026-07-30, eight views of a 1,280-face displaced ico-sphere at
  1024: **57.4 s** total (5.4 render, 47.4 restyle, 2.9 project), **99.9%** of chart texels painted
  directly with **0** left to the hole fill, adjacent-pair seam **1.7 to 2.8 of 255** over 83,699 to
  104,750 shared texels, front-against-180-degrees drift **3.1**, five maps written and a clean receipt.
  Two bars came out of it (`paint_coverage`, `view_overlap`), both provisional.

  **And what only LOOKING found.** The first three runs reported 99.6% to 100% of charts painted, a
  clean receipt, and seam figures an order of magnitude better than the boulder's — and the basecolor
  was one flat grey on a prompt asking for mossy green. At the shipped paint denoise the render
  dominates by design; on an untextured mesh under flat light there is nothing to keep, so the route
  returned the grey it was given. Every number in the receipt was healthy, which makes this the same
  defect as the black block-out albedo and the same fix: `gen_paint.chart_stats` feeds the EXISTING
  `map_spread` bar through `empty_map_warning`, measured in-chart because whole-file spread on that
  flat paint was 62.0 against a 6.0 bar and would have passed. Proved both ways on one mesh, seed and
  prompt: spread **1.31** at denoise 0.40 (warning fires) against **14.83** at 0.75 (clean, and the
  seams land in the boulder's 15.7 to 26.5 band). No new bar; the remedy clause is route-specific
  because a flat bake and a flat paint are fixed in different places. This is the render-a-gallery
  rule earning its keep: three green gates and a contact sheet would have caught it in one look.

  What the run found that no gate would have: the route renders N frames and then generates N times in
  ONE session, so it is the VRAM-handback rule's worst case. `core.render.release_gpu` is called between
  the two halves — and it recovers about 49 MiB, because Blender's turntable is not the expensive half.
  The refusal came from `ensure_untiled`: the tiling reset is itself an SDXL job, so by the time the
  chokepoint read the floor the shared checkpoint was resident and the card honestly read 3,599 MiB free
  against the 4,000 paint floor. The three routes that prime the model that way now read their floor
  BEFORE the reset, and `paint_views` reads it once for the batch rather than once per view.
- **One name already resolved to two answers.** A live session resolved its generated pack to a folder
  inside the ComfyUI checkout while the MCP tools wrote into the repo, so the trees would have worn
  whatever stale sets were in the wrong folder. What makes that silent rather than an error:
  **foliage resolves its bark set and leaf atlas BY NAME off the pack search path, and there is no
  way to pass a directory**, so a name that resolves in the wrong root is indistinguishable from one
  that resolves in the right one. Two consumers, one pack name, different roots — the parity failure
  in its cheapest form.

### What "1:1" would have to mean, beyond the three causes now closed

- **Every capability on one surface exists on the other**, checked mechanically rather than by
  intention — the contract and the panel drawn from one source, or a test that fails when either grows
  something the other lacks.
- **The agent can trigger the reloads it depends on**, so a code change it made becomes live without a
  human clicking, and can read back whether the live session is running the code it thinks it is.
- **A change feed, ops-shaped, both ways.** Artist edits become ops or at minimum a readable diff; the
  agent's ops become session history the artist can inspect and undo.
- **One resolution of every shared name** — pack roots first, since that one has already bitten.

The open design question is which way capability parity is enforced: generate the panel from the
contract (one source, larger change), or keep two surfaces and fail CI when they diverge (cheaper, and
it only catches capability parity). **State parity was the harder half and it is the half the artist was
complaining about** — read-back is state parity, and it is closed by structural config living on the
object, which neither of those two options would have touched.

Worth stating plainly, because it explains why this went unnoticed for so long: **every op in the
vocabulary writes, and only one reads.** `describe_scene` is that one and it reports to an agent, not to
a panel. A tool built entirely out of one-way operations feels divergent the first time two people use
it, and it will feel divergent to one person across two sessions. The ownership refusal is the shape of
the fix generalised: an op that can produce an object nothing can read afterwards should not exist.

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
at 1024; 2048 paints the same invention sharper). Two routes are untried ON THIS ASSET, and one of them
is now wired and measured on another: the `paint_stylised` op / `mesh_paint_views`, which paints from
Blender-rendered views so every surface is painted by a camera that can see it (99.9% of chart texels
painted directly on the shape it was proved on, and the barn is exactly the concave case that would
test it hardest); and `comfy_texture_set` per material,
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

## Open: the ordering exception the floors cannot catch (the one-surface half is CLOSED)

The leak itself is not news and is not re-derived here: [GENERATION.md](GENERATION.md)'s
VRAM-handback rule answered it on 2026-07-27 — `POST /free` drops only what the MAIN process's torch
allocator will release, the mesh nodes run in a SEPARATE pixi worker that cannot reuse that cache, and
only killing and relaunching `main.py` recovers the card. `comfy.recover_vram`,
`comfy.VRAM_FLOOR_MIB` and `comfy.preflight_vram` landed as the answer.

Two findings came out of re-running two gates on 2026-07-30. The first is closed; the second is why
this section is still here.

**1. `preflight_vram` had exactly one production caller. CLOSED.** It was `mcp_agent/server.py`, inside
the wrapper every `comfy_*` TOOL goes through, so an agent's generation was held to a floor and
nothing else was: the gate scripts call `core.comfy.mesh_texture` / `mesh_geom_ctrl` directly, and so do
the panel operators (`ui/foliage.py` calls `comfy.bark_set` and `comfy.leaf_atlas` straight). An artist
pressing Generate on a card another job had filled got the OOM traceback the floor exists to prevent.

The fix is the one this section already named: put it where the capability is. `comfy.generate_image`
and `comfy.generate_mesh` are the two functions every job in the module goes through, and both now
take a required `route` and call `preflight_vram` before they queue. Required, so a graph added later
cannot inherit the hole by omission — the same "state it or say `None` out loud" shape `gen_receipt`
uses for receipt keys. Two graphs say `None` with their reason at the call site (`mesh_simplify_uv`
and `mesh_process` load no model), plus the 64-px tiling reset, which must not be refused on the card
state it exists to unblock. The tier floor is derived from the resolution actually requested, so
`hero` / `1536_cascade` takes `mesh_hero` wherever it is spelled, and `mesh_paint_views` takes the
paint floor rather than the stylise one because it carries an IPAdapter and its vision encoder on top
of the two ControlNets. `server.py` keeps its own up-front check, now as an early-out rather than the
floor: it refuses a multi-stage chain before the first stage is paid for. Two tests hold it —
one drives a direct `core.comfy` call on a full card and fails if a job reaches the server, one scans
the source so a new call site with no route is a red test rather than a silent hole.

One thing finding 1 sharpened rather than fixed, measured while proving the paint route: a paint run
peaked at **14,136 MiB** of ComfyUI sampling on a card reading 13.2 GB free, against that route's 4,000
floor, and completed — ComfyUI offloads to fit. So on the main-process SDXL routes (texture, paint,
stylize, heightmap) the floor is a cheap early refusal and **not** a prediction of the peak. The floors
that are load-bearing are the mesh ones, where the TRELLIS.2 worker cannot share the main process's
cache and an over-commit is an OOM rather than a slowdown. Worth re-deriving the four SDXL numbers
against what they actually prevent; nothing is known to be wrong with them, but they are calibrated
against a failure mode that route does not have.

**2. A third instance of the ordering exception, on a third server.** The card reported **3,119 MiB
free**, over the 3000 texture floor, and the job died on an allocation of **82 MiB** ("total capacity of
15.48 GiB of which 100.19 MiB is free"). That is the pattern [GENERATION.md](GENERATION.md) already
records at 6530-reported-to-162-left and 5588-to-325: the floor is TRUE when read and the main process
expands after reading it. Raising the number is therefore not the fix, and the doc says why — there is
nothing readable at queue time that predicts it. The datum is here because a third independent
occurrence settles that it is the rule rather than a bad pair of runs.

The state that produced it, for whoever picks this up. After one block-out-conditioned
generation, of a 15.48 GiB card: ComfyUI main **7,482 MiB**, its TRELLIS.2 node subprocess
**3,038 MiB**, its geometry-pack node subprocess 260 MiB, **3,119 MiB free** — and `POST /free
{"unload_models": true, "free_memory": true}` returning HTTP 200 with byte-identical readings either
side of it.

**What it cost, and what it did not.** With the card in that state the block-out control gate's part C
reached its finish step and OOMed before the height-profile check ran, and the foliage gate's
generation section skipped — so two bars looked unverifiable. **On a freshly restarted server both
pass**: part C is 12 of 12 with `profile_deviation` measuring **0.0214 against its 0.1 bar** (0.0146
for a shape against itself, 0.2551 for an A-frame), and the finished asset holds footprint IoU 0.8289.
So nothing here is a defect in the code under test, and that is exactly what makes it expensive: the
gate reports on the ENVIRONMENT while looking like it reports on the code, and the only way to tell
the two apart today is to restart a server and run it again.

Two ways out left, cheapest first (the third, moving the preflight to the capability, is finding 1
above and is done — it does not help here, because this failure happens on a card that passes the
floor):

- **Make the ordering the suite's, not the operator's.** The floors cannot catch this and the docs
  say so; an ordering can. The gate suite has no declared order today, so whether a re-run works is a
  property of what somebody ran an hour ago.
- **A gate-side preflight that names the state instead of failing in it.** `Vram`/`gpu_sample` in
  `tools/scripts/_gate.py` already read per-process VRAM, so a gate can say "the card holds 10.5 GiB
  in ComfyUI node subprocesses; this part needs a restart" and SKIP — the rule the foliage gate's
  generation guard now follows.

Not attempted: raising the ceiling. 16 GB is the target card, and a route that only fits on a bigger
one is not shipped.

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

## Closed: the bar vocabulary is auditable now. Open: what the audit found

**The artist's report this section existed for:**

> "so much slop. magic numbers in core. I don't even understand what's happening."

That was a fair reading. Twenty-nine numbers decided whether a generated asset passed, spread across
`core/gen_receipt.py`, `core/comfy_maps.py` and five gate scripts, each justified in a prose comment
beside itself. To audit them you had to read all of it end to end, and there was no way to ask "which of
these rest on thin evidence?" The cost was measured rather than hypothetical: `leaf_ramp_stops` rested
on two samples, one synthetic, rejected **8 of 10** ordinary atlases, took the whole atlas class to a 0%
pass rate — and nothing surfaced that until somebody counted by hand. The information was in a comment
the whole time. **Comments do not fail.**

**`core/gen_bars.py` is the registry.** One entry per bar, carrying the value, the unit, what it
catches, how many REAL samples are behind it, how many synthetic, the date it was derived, who reads it,
and whether it is `provisional`. The derivation prose moved WITH each number, so there is one place to
read and one place to update; what stayed in `gen_receipt.py` is the `*_warning` functions, which are a
different concern — the sentences an artist reads. Every call site now reads the registry
(`OPEN_SURFACE_FRACTION = _bar("open_surface")`), so no bar has a second home, and
`test_no_literal_bar_survives_in_the_modules_that_moved` checks that on the source rather than on the
values.

**`tools/tests/test_gen_bars.py` is the teeth.** It does not forbid a thin bar — three of them catch
defects found in renders, and trading a working gate for a tidy table would be a bad exchange. It
forbids a thin bar that does not SAY it is thin, which is the state `leaf_ramp_stops` sat in for weeks.
14 checks, and the load-bearing one is `test_no_thin_bar_gates_a_shipped_asset_silently`.

**What counting them found, and none of it was known before the count:**

- **`control_hidden` was declared twice at 0.05**, in `gen_receipt.py` and in the block-out gate under
  a different name (`HIDDEN_AREA_MAX`). Two files, two names, one calibration, and nothing keeping them
  equal. One entry now, read by both.
- **`DRAW = 0.02` and `WIN_THRESHOLD = 2` were duplicated verbatim** between the bbox and voxel control
  gates, so the two A/B verdicts agreed by coincidence rather than by construction.
- **`footprint` has no recorded sample count at all.** Its comment says only which mode it belongs to.
  True, and not evidence for 0.5.
- **`open_boundary_edges` and `thin_axis_ratio` rest on ONE case each** — the leaf measurement. Their
  own comment said so; nothing counted it.
- **`grain_off_vertical`'s count is three prompt WORDINGS, not three sets.** The separation is enormous
  (83.8 with no clause, 17.6 with the shipped one), so the value is not in doubt; the sample count is.

**What the registry does not do yet:** pass rate per bar, from the class-rate harness. A bar nothing
fails is not protecting anything and a bar everything fails is a blocked route, and both are visible
only once the rate exists per bar rather than per class. That is the one item from this section's
original list still open, and it needs generation rather than code.

### The bars, generated from `core/gen_bars.py`

<!-- BEGIN GENERATED: bar-vocabulary (tools/scripts/gen_api_docs.py) -->

| Bar | Value | Unit | Evidence | Read by | Catches |
|---|---|---|---|---|---|
| `albedo_floor` | `0.02` | mean, 0..1 | 2 real -- thin | headless_gen_geometry_ab.py | a black albedo: the encoder handed a mesh outside its unit cube |
| `atlas_opaque` | `0.9` | alpha, 0..1 | 3 real -- **provisional** | comfy_maps.alpha_bleed | a white fringe around a card: colour bled from unpainted transparent texels |
| `axis_anisotropy` | `1.6` | ratio of covariance eigenvalues | 12 real | comfy_maps.orient_sprite | a sprite with no long axis, so its rotation is a guess |
| `axis_strand_contrast` | `2` | strands, wide end minus narrow | 12 real | comfy_maps.orient_sprite | a sprite oriented upside down: a needle tip mistaken for a cut stub |
| `axis_strong_taper` | `0.25` | narrow end RMS width / wide end | 1 real -- **provisional** | comfy_maps.orient_sprite | the escape hatch: taper so strong the stub end is unmistakable without a fan |
| `axis_taper` | `0.75` | narrow end RMS width / wide end | 12 real | comfy_maps.orient_sprite | a sprite with two alike ends, so which one attaches is a guess |
| `bake_diff` | `3` | mean absolute difference, 0..255 | 3 real -- **provisional** | gen_receipt.bake_fidelity_warning | the same misplaced bake, on the axis correlation is blind to |
| `bake_fidelity` | `0.99` | correlation, 0..1 | 3 real -- **provisional** | gen_receipt.bake_fidelity_warning | a bake that landed somewhere other than the surface it was baking |
| `bark_shear` | `0.5` | shear ratio | 2 real -- thin | headless_foliage.py, the bark section | the bark prompt clause regressing, read off the built trunk |
| `cell_opaque` | `0.02` | fraction of the cell that is opaque | 3 real -- **provisional** | gen_receipt.blank_cell_warning | an atlas cell with no sprite in it |
| `control_draw` | `0.02` | score difference | 3 real -- thin | headless_gen_bbox_control.py and headless_gen_voxel_control.py | an A/B whose two sides are too close to call, reported as a draw not a win |
| `control_hidden` | `0.05` | fraction of total surface area | 2 real -- **provisional** | gen_receipt.control_surface_warning + the block-out gate's hidden-surface check | a block-out conditioning generation on its own interior |
| `control_win` | `2` | wins out of the pairs measured | 3 real -- thin | headless_gen_bbox_control.py and headless_gen_voxel_control.py | a control mode declared better on one lucky pair |
| `control_wired` | `2` | wins out of the pairs measured | 3 real -- thin | headless_gen_voxel_control.py | the voxel route's own wiring, asserted separately from its score |
| `flatness` | `0.075` | fraction of a stop | 10 real | gen_receipt.flatness_warning | an albedo with baked lighting in it |
| `footprint` | `0.5` | fraction of the control's plan area | 0 real -- thin | headless_gen_voxel_control.py | a generation that kept the silhouette and lost the plan |
| `grain_off_vertical` | `25` | degrees off vertical | 3 real -- **provisional** | gen_receipt.grain_warning | bark whose grain wraps the trunk instead of running up it |
| `leaf_ramp_stops` | `0.55` | stops of ramp inside the opacity mask | 10 real, 1 synthetic -- **provisional** | gen_receipt.flatness_warning | a leaf sprite lit by a gradient, which a flat card cannot relight |
| `map_dark` | `4` | mean, 0..255 | 7 real | gen_receipt.empty_map_warning | which sentence an empty map gets: black, or uniform at some other value |
| `map_spread` | `6` | standard deviation, 0..255 | 7 real | gen_receipt.empty_map_warning | a map that shipped uniform -- black, white or flat grey |
| `metalness` | `0.1` | mean of the metalness map, 0..1 | 10 real | gen_receipt.metalness_warning | a generated material claiming to be metal |
| `open_boundary_edges` | `500` | boundary edges | 1 real -- thin | headless_gen_assets.py | the mesh repair not running at all on a route that needs it |
| `open_surface` | `0.01 (floor 12)` | fraction of face count (floor in edges) | 5 real | gen_receipt.open_surface_warning | a solid kind shipping with holes an artist can see through |
| `paint_coverage` | `0.9` | fraction of chart texels painted from a view | 2 real -- **provisional** | gen_receipt.paint_coverage_warning | a paint that left the charts to the hole fill: too few views, or views that could not see the surface |
| `profile_deviation` | `0.1` | max band deviation, fraction | 1 real, 1 synthetic -- thin | headless_gen_blockout_control.py part C | a generation that ignored the block-out's height profile |
| `seethrough` | `0.01` | fraction of face count | 5 real | gen_receipt.open_surface_warning | a sieve: many small see-through holes, no single big one |
| `seethrough_opening` | `0.1` | fraction of the longest dimension | 2 real -- **provisional** | gen_receipt.open_surface_warning | one big opening: a missing corner or panel, which no edge fraction reaches |
| `thin_axis_ratio` | `0.25` | shortest bbox axis / longest | 1 real -- thin | headless_gen_assets.py | a degenerate generated mesh: flat slab or spike |
| `view_overlap` | `200` | texels shared by an adjacent view pair | 2 real -- **provisional** | gen_receipt.view_overlap_warning | a turntable too sparse to measure its own seam, so the seam figure means nothing |
| `woody_excess` | `2` | green excess, 0..255 | 12 real | comfy_maps.orient_sprite | which texels are stem rather than leaf, so a stem can be found at all |
| `woody_separation` | `0.025` | fraction of the sprite's diagonal | 12 real | comfy_maps.orient_sprite | a woody patch too close to the leaf mass to be a stem |

_31 bars: 22 judged on the ASSET (a receipt sentence reads each), 9 on the CODE (a gate asserts each). 11 are declared **provisional** -- under 5 real samples, or measured wrong and kept because they still catch something. `tools/tests/test_gen_bars.py` fails when a thin bar judges a shipped asset without saying so._

<!-- END GENERATED -->

## Open: the provisional bars, and what each one needs

Nine of the twenty-nine are marked `provisional` in the table above, and each one's registry entry
carries what would settle it. They are kept rather than deleted because every one catches something real
— trading a working gate for a tidy table would be a bad exchange — and none of them is trusted. The
strong ones, for contrast: `metalness` from ten samples spanning 0.0002 to 0.83, `map_spread` from seven
maps spanning 0.00 to 57.51, `axis_anisotropy` and `axis_taper` from twelve cells with a clean
separating gap on both figures.

**Recalibrating needs generation, not code**, which is why this is one section rather than nine: a
second batch of leaf atlases settles four of them and a second batch of structures and rocks settles
three. One is different, and it is the worst of them:

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

Neither batch is blocked on anything but card time, and the registry is what makes the shopping list
readable: sort the generated table by its evidence column and the two batches to shoot are the answer.

**And one thing the count made visible that nobody had asked:** every one of the nine CODE bars is
thin by the same rule — `open_boundary_edges` and `thin_axis_ratio` rest on one case each,
`footprint` on none that was ever written down. They are not required to declare themselves and the
table marks them `thin` rather than `provisional`, because a gate bar asserts a property of the CODE
(a prompt clause still holds, a route is still wired) rather than generalising over the next thing a
generator returns — two species is enough to catch a regressed bark clause. Worth seeing; not worth
failing on.

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
