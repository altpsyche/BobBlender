# Handover: de-phase the repo — organise everything by FEATURE, not by the phase that built it

**The ask, in one sentence.** Every subsystem in this repo is documented, commented, gated and
file-named after the *development phase* that produced it (`F1`–`F6`, `G0`–`G9`, `S1`–`S5`, `C1`–`C5`,
`P0`–`P7`, `D12`–`D16`, `R1`–`R11`, `W4`–`W13`, `A1`…, `B2`…), and a reader who was not present for
those phases cannot follow any of it. Convert the whole repo to describe **what things are and how
they behave**, not **when they were built** — without losing a single measured number or a single
recorded reason that is needed, its fine to trim or remove verbose data or info. I want it feature spoken, not phase spoken.

**Why this matters more than it looks.** The phase labels are not merely ugly. They are load-bearing
in the wrong way: they are the *only* index into the repo's knowledge. Want to know why bark UVs are
metres-based? That fact lives under "what F2 measured". Want to know which generation route ships?
That is "W9b". Want to run the gate for texture sets? That is `headless_comfy_g2.py`. So a new reader
(or a new agent session) has to learn a private chronology before it can learn the software, and
every phase label is a fact whose shelf life already expired — `F1 shipped this broken` is history,
while `a branch base must never move` is the thing that is still true.

**This document deletes itself.** It is the last phase-shaped artifact; removing it is the final step
of the task it describes.

---

## 1. The measured inventory

Re-run this survey before starting and again at the end — the second run is the acceptance test:

```bash
PAT='\b(F[1-6]|G[0-9][a-c]?|S[1-5]|C[1-5]|P[0-7]|D1[0-9]|R[1-9]|R1[01]|W[0-9]+[a-z]?|A[1-9]|B[1-9]|M[12])\b'
for area in docs blender/extensions tools/scripts tools/tests .github; do
  n=$(grep -rInoE "$PAT" --include="*.py" --include="*.md" --include="*.yml" --include="*.json" $area | wc -l)
  f=$(grep -rIlE "$PAT" --include="*.py" --include="*.md" --include="*.yml" --include="*.json" $area | wc -l)
  printf "%-20s %5d hits in %3d files\n" "$area" "$n" "$f"
done
```

As of 2026-07-28:

| Area | Hits | Files | The shape of it |
|---|---|---|---|
| `docs/` | 1,101 | 25 | section headings, "Landed at F4", `[F2, answered]` tags, phase tables |
| `blender/extensions/` | 766 | 69 | module and function docstrings, inline rationale comments |
| `tools/scripts/` | 473 | 23 | gate script *filenames*, gate registry keys, check labels |
| `tools/tests/` | 101 | 5 | test section banners, docstrings |
| `.github/` | 3 | 1 | `TODO(P0)`, a `PRE-P0-SCOPE.md` reference, an `if: false` job gated on "P0" |
| **total** | **~2,444** | **123** | |

Two structural facts on top of the counts:

- **24 of the 40 files in `docs/` are phase artifacts by name**, not feature documentation:
  `*-HANDOVER.md` (9), `*-REDESIGN.md` (2), `*-AUDIT.md` (2), `UX-ROUND2-*` (2), `*-FINDINGS.md`,
  `*-CRITIQUE.md`, `*-REVIEW.md`, `PRE-P0-SCOPE.md`, `RESTRUCTURE-PLAN.md`,
  `COMFYUI-MEASUREMENTS.md`. Several are superseded by the work they handed over and are now
  actively misleading.
- **10 gate scripts are named after phases**: `headless_comfy_g2.py`, `g3.py`, `g3b.py`, `g4.py`,
  `g4c.py`, `g5.py`, `g6.py`, `g7.py`, `g8.py`, `g9.py`. Their registry in
  `tools/scripts/headless_comfy_all.py` has a literal `"phase"` field per entry, and `--gate g4c`
  is the documented way to run one.

**One piece of good news, and it changes the risk profile.** Grep found **zero** phase codes in
user-visible runtime strings — no panel label, operator name, tooltip, report or MCP description
carries one:

```bash
grep -rnE '"[^"]*\b(F|G|S|C|P|D|R)[0-9]{1,2}[a-c]?\b[^"]*"' --include="*.py" \
  blender/extensions/bob_blender_tools/ui/ blender/extensions/bob_blender_tools/mcp_agent/
```

So this is a documentation, comment and filename job. **No shipped behaviour has to change**, and
that is the boundary of the task.

---

## 2. Taxonomy: what each family actually means

Do not touch a single label before reading this. The families are not interchangeable, one of them is
overloaded, and one of them is *not a phase at all*.

| Family | What it really is | Where it lives | Treatment |
|---|---|---|---|
| `F1`–`F6` | BobFoliage build phases | `docs/FOLIAGE.md`, `recipes/foliage.py`, `foliage_build.py`, `foliage_variants.py`, `ui/foliage.py`, `headless_foliage.py` | **convert** to feature/invariant prose |
| `G0`–`G9`, `G3b`, `G4c` | ComfyUI generation phases, each with its own gate script | `docs/COMFYUI.md`, `core/comfy*.py`, 10 gate scripts, the gate registry | **convert + rename files** |
| `S1`–`S5` | BobFirmament (atmosphere) phases | `docs/FIRMAMENT.md`, `core/materials/weather.py`, `ui/firmament.py` | **convert** |
| `C1`–`C5` | BobSplines phases | `docs/SPLINES.md`, `SPLINES-HANDOVER.md`, `core/curves*`, `ui/splines.py` | **convert** |
| `P0`–`P7` | Repo-restructure / polyrepo phases | `RESTRUCTURE-PLAN.md`, `PRE-P0-SCOPE.md`, `UNIFIED-SYSTEM.md`, `ci.yml`, heightfield modules | **convert**; `P4` is used as shorthand for a real *decision* ("compute lives in the extension, no venv copy") — state the decision instead |
| `D12`–`D16` | ComfyUI **decisions**, numbered | `docs/COMFYUI.md`, `ui/scatter.py`, `gen_assets.py` | **convert** — each is a rule; state the rule |
| `A1`, `A2`… | UX audit **findings**, severity-ranked | `UX-AUDIT.md`, `UX-FINAL-REVIEW-FINDINGS.md` | findings are resolved; **harvest the surviving rules, delete the rest** |
| `B1`, `B2`… | bug ids from a review round | `UX-*` docs | same as `A` |
| `M1`, `M2` | terrain-engine rewrite phases | terrain docs, memory | **convert** |
| `R1`–`R5` | BobSplines "polish pass" phases | `SPLINES*.md`, `core/curves*` | **convert** |
| `R7`, `R11` | **A DIFFERENT `R`** — numbered *measurements/rules* in the generation track (`R7` = "256 levels cannot carry a heightfield"; `R11` = the generated-model manifest rule) | `comfy*.py`, `gen_assets.py`, `docs/COMFYUI*.md` | **convert**, and note the collision in the commit body: `R` meaning two things in one repo is itself part of the report |
| `W1`–`W13` (+ `t`/`b`/`c`/`v` suffixes) | **NOT phases.** Aliases for ComfyUI *workflow routes*, every one of which already has a real name | `core/comfy.py` (~120 hits), `docs/COMFYUI*.md` (~400) | **rename to the real name** — see §4 |

`W` is the highest-value fix in the whole task and the least risky: `W4` *is* `mesh_subject`, `W9b`
*is* the shipped one-shot TRELLIS.2 route, `W7` *is* block-out-conditioned geometry. The JSON files in
`blender/extensions/bob_blender_tools/assets/workflows/` are already named descriptively. The codes are
pure redundancy sitting on top of good names.

Build the full `W` → name → workflow-JSON → python-function table first, from
`core/comfy.py` docstrings, and put it in the generation doc. Then the codes can go.

---

## 3. The conversion rules

The house style in this repo is dense, argued prose with measured numbers in it. **That style is the
asset — keep it.** What changes is the *anchor*: from "which phase did this" to "what is true".

### Rule 1 — a measurement never loses its number, only its phase

```
before:  Measured before the fix: **7.7e-05 m** at `Wind` 6, eighty times F2's attachment residual
after:   Measured before the fix: **7.7e-05 m** at `Wind` 6, eighty times the card-attachment residual
```

```
before:  | Bark U per face, before the seam fix | **1,183 of 7,098** faces spanned 5/6 of the profile
after:   (unchanged — no phase label in it)
```

Any edit that drops a number, a threshold, a wall-clock or a ratio is a **failed** edit. These
numbers are the only reason the docs are trustworthy.

### Rule 2 — "phase X found/shipped/landed Y" becomes an invariant plus its check

The single most common pattern, and the most valuable to convert. The phase framing states the
*history*; the reader needs the *rule*.

```
before:  **Fixed at F2: the shipped defaults.** F1's `_LEVEL_DEFAULTS` grew a crown about 13 m wide
         on a 20 m trunk, a spreading broadleaf and not the narrow conifer this track was started by.
after:   **The shipped defaults grow a narrow conifer, and that is gated at width/height < 0.45.**
         The lever is `length` and the levels below it compounding: a level-1 branch is a fraction of
         the trunk's length, so 0.42 was an 8.4 m arm. Measured: 0.42 / 0.46 / 0.50 spans 10.9 m on a
         20 m trunk and 0.115 / 0.30 / 0.38 spans 6.6 m.
```

```
before:  # The radius never reached the mesh (F1 shipped this broken; F2 found it).
after:   # The radius must be handed to `Curve to Mesh`'s Scale input EXPLICITLY. Blender 4.0 stopped
         # applying a curve's radius attribute implicitly, so a graph that only calls Set Curve Radius
         # sweeps a uniform 1 m tube while `Trunk Radius`, `Taper` and every per-level ratio sit inert
         # — and it still looks like a tree. `bbt_fol_rad` feeds Scale; the gate measures the width.
```

### Rule 3 — "defects found, tallied by phase" becomes "failure modes, and the check that catches each"

`docs/FOLIAGE.md` has a running tally ("F2 found two of F1's, F3 found one of F2's…", eleven
defects). That list is genuinely valuable and is currently indexed by the worst possible key. Convert
each track's tally into a table:

| Failure mode | What it looks like | The check that catches it |
|---|---|---|
| the sweep ignores the curve radius | a tree of uniform sticks; every knob inert | `check_radius` — trunk width at `trunk_radius` 0.25 / 0.50 |
| gnarl as an amplitude in metres | plant-scale presets torn apart (a 0.4 m tuft returns 1.7 m) | `check_scale_invariance` |
| bark assigned with box projection | `Bark Scale` inert, grain follows world axes | `check_bark_uv` |
| … | … | … |

That table is the thing a maintainer actually needs, and it reads the same whether or not you know
what F3 was.

### Rule 4 — `[F2, answered]` / `[F5, answered]` / `[anytime]` / `[later]` tags

Open-questions sections are tagged with the phase that closed them. Replace with status only:
`**Answered.**` / `**Open.**` / `**Deferred, and why.**` An answered question keeps its answer inline
and loses the phase. A question no longer meaningful is deleted, not archived.

### Rule 5 — phase-numbered *decisions* (`D12`–`D16`, `P4`, `R7`, `R11`) become named rules

```
before:  The D16 guardrail, so nobody waits for this by generating trees.
after:   The generation-route guardrail: `comfy_mesh(kind="trees")` is for DEAD WOOD — stumps, logs,
         snags — and its note says so, because "generates a trunk, not a crown" read as an invitation.
```

Give each rule a short descriptive name where one is needed for cross-referencing (`the dead-wood
routing rule`, `the heightfield bit-depth floor`, `the generated-manifest origin rule`). Names
cross-reference as well as numbers do and carry their meaning with them.

### Rule 6 — track names stay; phase numbers go

`BobFoliage`, `BobSplines`, `BobShaders`, `BobFirmament` are **product/subsystem names** and stay in
code comments, docstrings and doc titles. `BobFoliage F4` becomes `BobFoliage`. (Note the precedent
already set: the N-panel headers are plain nouns — `Foliage`, `Paths`, `Scatter` — while the track
keeps its Bob- name in the code. Same split.)

### Rule 7 — no blind `sed`

Every hit needs a human-or-model judgement about what the sentence is *for*. A regex replace will
produce sentences like "measured at  the fix held", destroy the numbers' context, and silently corrupt
the one thing worth keeping. Work file by file. If a sentence exists *only* to say which phase did
something, delete the sentence.

---

## 4. File and identifier renames

### 4.1 Gate scripts and the gate registry

The registry in `tools/scripts/headless_comfy_all.py` currently carries a `"phase"` field. Rename the
field to `"covers"` and re-key by feature. Proposed mapping — **confirm each against what the script
actually asserts before renaming it**, since a few gates cover more than their phase name suggests:

| now | proposed script name | proposed `--gate` key |
|---|---|---|
| `headless_texset.py` | *(already fine)* | `texture-sets-sampler` |
| `headless_comfy_texset.py` | `headless_gen_texture_sets.py` | `texture-sets` |
| `headless_comfy_g2.py` | `headless_gen_variants_maps.py` | `variants-maps` |
| `headless_comfy_g3.py` | `headless_gen_assets.py` | `assets` |
| `headless_comfy_g3b.py` | `headless_gen_oneshot_vs_staged.py` | `oneshot-vs-staged` |
| `headless_comfy_g4.py` | `headless_gen_stylise_paint_multiview.py` | `stylise-paint-multiview` |
| `headless_comfy_g4c.py` | `headless_gen_blockout_control.py` | `blockout-control` |
| `headless_comfy_g5.py` | `headless_gen_terrain_macro.py` | `terrain-macro` |
| `headless_comfy_g6.py` | `headless_gen_agent_surface.py` | `agent-surface` |
| `headless_comfy_g7.py` | `headless_gen_geometry_ab.py` | `geometry-ab` |
| `headless_comfy_g8.py` | `headless_gen_bbox_control.py` | `bbox-control` |
| `headless_comfy_g9.py` | `headless_gen_voxel_control.py` | `voxel-control` |

Renaming a gate is a **four-file** edit every time: the script, its entry in `GATES`, every doc that
names it, and any `--gate` example in a docstring or README. Use `git mv` so history follows. Old
`--gate` keys may be kept as hidden aliases for one release *only if* you also add a deprecation line;
otherwise drop them cleanly — this repo has one user.

### 4.2 Workflow codes

Replace `W<n>` with the route name everywhere. Where a doc genuinely needs to talk about several
routes in a comparison table, use the names; they are no longer than the codes once the reader does
not have to look them up. Keep one mapping table in the generation doc for anyone reading old commit
messages.

### 4.3 Docs to delete, after harvesting

Read each, move anything still true into the feature doc that owns it, then `git rm`:

`AUTHORING-MODEL-REVIEW.md`, `BIOME-NEW-HANDOVER.md`, `COMFYUI-MEASUREMENTS.md`,
`EROSION-BANKS-HANDOVER.md`, `MCP-FULLSCENE-HANDOVER.md`, `MCP-LIVE-TEST-HANDOVER.md`,
`MCP-SELFCONTAINED-HANDOVER.md`, `PRE-P0-SCOPE.md`, `REDWOOD-FIXES-HANDOVER.md`,
`SNOW-UNIFY-HANDOVER.md`, `SPLINES-HANDOVER.md`, `TERRAIN-AMPLIFY-HANDOVER.md`,
`TERRAIN-CRITIQUE.md`, `TERRAIN-REDESIGN-HANDOVER.md`, `UX-AUDIT.md`,
`UX-FINAL-REVIEW-FINDINGS.md`, `UX-FINAL-REVIEW-HANDOVER.md`, `UX-ROUND2-HANDOVER.md`,
`UX-ROUND2-REMAINING-HANDOVER.md`, `WATER-SHADER-HANDOVER.md`, `WEATHER-MATERIAL-AUDIT.md`,
`BIOME-BLOCKOUT-REDESIGN.md`, `UX-REDESIGN.md`, `RESTRUCTURE-PLAN.md`, and finally this file.

**`COMFYUI-MEASUREMENTS.md` needs care**: it is a measurement archive, and measurements are exactly
what must not be lost. Fold it into the generation doc as a "measured baselines" appendix, or keep it
under a non-phase name (`docs/GENERATION-BASELINES.md`) with the phase columns rewritten as *what was
measured* and *when*.

`RESTRUCTURE-PLAN.md` and `UNIFIED-SYSTEM.md` describe work that may still be pending. If so, they
stay — restated as a feature roadmap ("what is not yet single-sourced, and why it matters") with no
`P<n>` numbering.

---

## 5. Target documentation shape

One doc per subsystem, each with the same sections. This is what makes the result navigable without a
chronology:

```
docs/
  ARCHITECTURE.md      how the pieces fit; the only doc that surveys everything
  CONVENTIONS.md       code, naming, comment and gate conventions
  USAGE.md             an artist's path through the suite
  API.md               the op vocabulary
  MCP.md               the agent-facing surface
  TERRAIN.md  WATER.md  SHADERS.md  SPLINES.md  FOLIAGE.md
  FIRMAMENT.md  BIOMES.md  SCATTER.md  GENERATION.md   (was COMFYUI.md)
  THIRD-PARTY-MODELS.md  licence and provenance rules
  GENERATION-BASELINES.md  measured numbers, if not folded into GENERATION.md
```

Per-subsystem template:

1. **What it is** — one paragraph, and the one-line thesis if it has one.
2. **How it works** — the pipeline, in the order data flows.
3. **Knobs and params** — what is live, what is structural, and why each is on that side.
4. **Invariants** — the things that must stay true, each with its measured number. *This is where the
   old "what phase N measured" tables go.*
5. **Failure modes** — the Rule 3 table: what breaks, what it looks like, which check catches it.
6. **Open questions** — status-tagged, never phase-tagged.

Keep the argued, "here is why the obvious approach is wrong" voice throughout. That is the repo's
actual documentation style and it survives de-phasing untouched.

---

## 6. Do not break

- **Public/behavioural identifiers.** Op names, MCP tool names, recipe names, GN socket names, param
  keys (`assets.FOLIAGE_PARAM_KEYS` and friends), attribute names (`bbt_fol_*`), collection names
  (`BOB_Assets_<Kind>`, `BOB_Foliage`), manifest fields (including `"generator": "BobFoliage"`),
  `S_GROUP_VER` and every `_GROUP_VER_OVERRIDE`. None of these carries a phase code; leave them alone.
- **Check labels are behaviour-adjacent.** `headless_comfy_all.py` parses each gate's verdict line by
  regex (`FAIL_RE`, and three accepted wordings). Do not reword a verdict line without updating the
  runner. Individual `check("...")` labels are free to rewrite.
- **CI.** `.github/workflows/ci.yml` references `PRE-P0-SCOPE.md` and has `TODO(P0)` on a disabled
  `blender-headless` job. Rewrite the comment to say what the job needs (`an in-Blender test entry
  point at tools/tests/run_blender_tests.py`) rather than which phase would add it. Decide with the
  owner whether that job should be enabled as part of this work — it is currently `if: false`.
- **Test names.** Renaming a test function is fine; deleting an assertion is not.
- **LFS.** Some docs reference generated assets under LFS. Renaming docs is safe; do not move binary
  assets as part of this task.

---

## 7. Work order

Do it subsystem by subsystem, and land each one as its own commit with the full gate suite green. Do
**not** open with a repo-wide search-and-replace.

1. **Build the maps first, commit nothing.** The `W` → route table, the `D`/`R`/`P` → rule table, and
   the gate script → what-it-covers table. Put them in a scratch file. Everything downstream depends
   on their accuracy, and this is the step where a wrong guess is cheap.
2. **`CONVENTIONS.md`: write the rule down.** Add a short section: *identifiers describe features, not
   phases; no letter-number labels in docs, comments, filenames or check labels; a measurement keeps
   its number and loses its phase.* Everything after this is enforcing a documented rule rather than a
   preference.
3. **The CI guard, before the cleanup.** Add `tools/scripts/check_no_phase_labels.py` — the survey
   regex from §1, with a small allowlist file for genuine matches (model names like `SDXL`,
   `TRELLIS.2`, `Hunyuan3D 2.1`, Blender versions, `G0` if it ever means something real). Wire it into
   `ci.yml` beside `check_selfimports.py`. It will fail loudly at first; that is the point — it is the
   task's own progress bar, and it is what stops the rot returning.
4. **Generation (`COMFYUI.md` → `GENERATION.md`) — biggest and hardest, ~800 hits.** The `W` renames,
   the `G` phases, the `D` decisions, the 10 gate script renames, the registry, the measurement
   archive. Split into several commits: workflow renames, then gate renames, then the doc rewrite.
5. **Foliage** (~300 hits) — good template for the others, and the failure-mode table is already
   nearly written in prose.
6. **Splines** (`C`, `R1`–`R5`), **Firmament** (`S`), **Terrain** (`M`, `R7`), **Biomes**, **UX/panels**
   (`A`, `B`).
7. **Restructure/polyrepo (`P`)** — decide with the owner what is still pending, then either restate as
   a roadmap or delete.
8. **The doc deletions** from §4.3, each with its harvest already landed in a feature doc.
9. **`README.md` and `docs/ARCHITECTURE.md` last** — they are the index, and they should be rewritten
   once the things they point at have stopped moving.
10. **Agent memory.** `~/.claude/projects/-home-siva-dev-BobBlender/memory/` is indexed the same way
    (`F1-F6 DONE`, `S1-S5 DONE`, `G0-G5 DONE`, `M1/M2/P1`). Rewrite those entries feature-wise too, or
    a fresh session will reintroduce the vocabulary on day one. Cheap, and it is the difference between
    a fix and a habit.
11. **Delete this file.**

Verification after **every** commit:

```bash
tools/.venv/bin/python -m pytest tools/tests -q                 # expect 271 passed
python tools/scripts/check_selfimports.py
python tools/scripts/check_no_phase_labels.py                   # count must fall, never rise
uv run --project tools python tools/scripts/headless_comfy_all.py --fast   # every gate PASS or SKIP
```

Plus the subsystem's own gate at full strength for whichever subsystem the commit touched, e.g.
`blender --background --factory-startup --python tools/scripts/headless_foliage.py -- --no-gen`
(expect `all checks passed`, 237 checks as of 2026-07-28).

---

## 8. Definition of done

- `check_no_phase_labels.py` passes with an allowlist containing only genuine external names.
- No file in the repo is named after a phase.
- `docs/` contains one document per subsystem plus the cross-cutting five, and no `*-HANDOVER.md`.
- Every measured number that existed before still exists, findable by feature. Spot-check a dozen from
  the old docs by grepping the value, not the phase.
- Every gate and every unit test passes, with the same check counts as before the work started
  (labels may change; **counts may not fall**).
- `CONVENTIONS.md` states the rule, so the next contributor does not have to infer it.
- A reader who has never seen this repo can answer "why are bark UVs in metres?" and "which
  generation route ships?" without learning a chronology.

## 9. One judgement call to make explicitly

The phase labels encode *real history*, and some of it is genuinely useful: "this rendered perfectly
and was still wrong, and here is the check that found it" is the most valuable pattern in these docs.
**Keep the lesson, drop the timeline.** Where a phase story is the clearest way to explain why a rule
exists, tell it without the label — "an earlier version of this weighted the falloff per vertex, which
sheared every swept ring" says everything `F4 found that F4 shipped` says, and says it to someone who
was not there.

If you find yourself unable to convert a passage without losing meaning, that passage is describing a
**decision with no home**. Give it one: a named rule in the subsystem's Invariants section. That is the
whole task in one sentence.
