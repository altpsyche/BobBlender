"""Every bar a generated asset is judged against, and the evidence behind each one.

The artist's report this exists for:

    "so much slop. magic numbers in core. I don't even understand what's happening."

That was a fair reading. Around thirty numbers decided whether a generated asset passed, spread over
`gen_receipt.py`, `comfy_maps.py` and five gate scripts, and every one was justified in a prose
comment beside itself. To audit them you had to read all of it end to end, and there was no way to ask
"which of these rest on thin evidence?" **Comments do not fail.** The cost is measured rather than
hypothetical: `leaf_ramp_stops` rested on two samples, one of them synthetic, and rejects eight of ten
ordinary leaf atlases -- an entire asset class reading 0% on the pass-rate harness -- and nothing
surfaced that until somebody counted by hand. The information was in a comment the whole time.

So the numbers live here, ONE of each, with the facts about them machine-readable: how many REAL
samples are behind each, how many synthetic, when it was last derived, which sentence or check reads
it, and whether it is a threshold anyone should trust yet. `tools/tests/test_gen_bars.py` is what makes
that load-bearing -- a bar gating shipped assets on thin evidence has to SAY SO, in the field, or the
test fails. The bar table in docs/ROADMAP.md is generated from here, so it cannot drift from the code.

**The prose moved with the numbers, deliberately.** A derivation belongs beside the value it derives,
and splitting the two would leave two places to update. What stayed behind in `gen_receipt.py` is the
`*_warning` functions -- the SENTENCES an artist reads. Two concerns, two modules: this one answers
"what is the number and why", that one answers "what do we tell somebody".

**What is NOT here, and the line is worth stating.** Output-SHAPING constants are not bars:
`AO_STRENGTH`, `NORMAL_STRENGTH`, the `DELIGHT_*` family, `ROUGHNESS_*`, `MACRO_*`, `CAVITY_*` and the
AO fractions decide what a map LOOKS like, and no asset passes or fails on them. Neither are
measurement PARAMETERS that feed a bar without being one (`GRAIN_BINS`, `AXIS_END_BAND`,
`STRAND_GAP_*`, the atlas margin and bleed passes) or run parameters (seeds, face budgets,
resolutions). A bar is a number an asset is JUDGED against. Mixing the other two kinds in would make
"how many bars are there" unanswerable again, which is the whole complaint.

bpy-free and dependency-free -- stdlib only -- because `comfy_maps` runs in the MCP venv with no
Blender, the gate scripts run inside Blender, and the venv tests import it as a bare module. It
imports nothing from this package either, so it is the leaf of the dependency graph and cannot cycle.
"""

from dataclasses import dataclass

# How many REAL samples a bar needs behind it before it can gate a shipped asset without declaring
# itself provisional. Five, because that is where the measured spreads start separating cases rather
# than describing them: `metalness` at ten samples spans 0.0002 to 0.83 and the bar sits in open
# space, while `seethrough_opening` at two points sits between its only two observations and cannot
# be said to generalise. Not a law of nature -- a line that has to be somewhere, chosen where the
# evidence in this repo actually changes character.
MIN_REAL_SAMPLES = 5


@dataclass(frozen=True)
class Bar:
    """One threshold, its evidence, and who reads it.

    Frozen because a bar is a fact about a calibration, not a runtime setting: anything that wanted to
    vary one per call would be asking for a different bar, and a mutable registry would let a caller
    change what every later asset is judged against.
    """

    value: float
    unit: str            # what the number is IN, so a reader can tell 25 degrees from 0.25 of a span
    catches: str         # the defect it fires on, in one line
    reader: str          # the receipt sentence or gate check that reads it: where its verdict lands
    judges: str = "asset"     # "asset" (a receipt sentence) or "code" (a gate assertion)
    real: int = 0             # generated samples measured, the number that decides `thin`
    synthetic: int = 0        # hand-made or perturbed samples: evidence, but not of what ships
    derived: str = ""         # ISO date this value was last read off a measurement
    floor: float | None = None  # the absolute companion of a fractional bar (see `open_surface`)
    provisional: bool = False   # declared thin: kept because it catches something real, not trusted
    note: str = ""              # why it is provisional, or what would settle it

    @property
    def thin(self) -> bool:
        """Fewer real samples than `MIN_REAL_SAMPLES`. Synthetic samples deliberately do not count:
        a bar's job is to judge what a generator returns, and a shape somebody built to fail it is
        evidence about the measurement rather than about the population."""
        return self.real < MIN_REAL_SAMPLES

    @property
    def undeclared(self) -> bool:
        """Thin, gating a shipped ASSET, and not saying so. What the test fails on.

        Asset-only, and the asymmetry is deliberate rather than an omission. An asset bar generalises
        over a POPULATION -- the next thing a generator returns -- so its sample count is a claim about
        how well it generalises, and five is where the measured spreads in this repo start separating
        cases rather than describing them. A code bar asserts a property of the CODE: that a prompt
        clause still holds, that a route is still wired. Two species are enough to catch a regressed
        bark clause, and demanding five would be asking a different question than the gate asks.

        Every code bar here is nonetheless thin by the asset rule, and `table_rows` marks them so --
        that is worth SEEING even where it is not worth failing on.
        """
        return self.thin and self.judges == "asset" and not self.provisional


# ---------------------------------------------------------------------------------------------
# The asset bars: a receipt sentence reads each one, so an artist meets it.
# ---------------------------------------------------------------------------------------------
_ASSET = {
    # -- Solid geometry ----------------------------------------------------------------------
    # A SOLID kind's residual openness, as a fraction of its face count, plus a floor so a small mesh
    # is not warned for two edges. Both numbers come off five generated solids measured after the
    # weld and the pinhole fill: stump 73 of 3,840 faces (1.9%) and rock slab 72 of 2,900 (2.5%) are
    # the two the artist could see through, against fallen log 17 of 3,908 (0.4%), a gabled structure
    # 11 of 7,502 (0.15%) and boulder 9 of 3,807 (0.24%) which read as closed. A fraction rather than
    # a count because a 500-face rock and a 40,000-face building are not the same claim.
    "open_surface": Bar(
        value=0.01, floor=12, unit="fraction of face count (floor in edges)",
        catches="a solid kind shipping with holes an artist can see through",
        reader="gen_receipt.open_surface_warning", real=5, derived="2026-07-29"),

    # The same bar asked of the SEE-THROUGH edges alone, once `gen_assets.openness_report` can tell a
    # hole from a pit. Needed alongside the opening bar below because they catch opposite shapes and
    # each is blind to the other:
    #
    #   a sieve    hundreds of tiny holes, so a large share of the faces and no single big opening.
    #              The generated boulder arrived like this (19,623 boundary edges before the pinhole
    #              fill) and the fraction is what catches it.
    #   one wedge  a gabled structure's missing corner under the left eave: ONE loop, 5 edges of
    #              8,839 faces, 1.59 m across. 0.06% of the faces, so no edge fraction will ever fire
    #              on it, and the artist saw it immediately.
    "seethrough": Bar(
        value=0.01, unit="fraction of face count",
        catches="a sieve: many small see-through holes, no single big one",
        reader="gen_receipt.open_surface_warning", real=5, derived="2026-07-29"),

    # The opening's own size, over the object's longest dimension. It sits between the only two
    # measured cases: a ground rock's biggest see-through opening is 0.07 of the rock, which the
    # artist accepts as vesicular stone, and a gabled structure's is 0.18, which the artist rejected
    # in a render.
    "seethrough_opening": Bar(
        value=0.10, unit="fraction of the longest dimension",
        catches="one big opening: a missing corner or panel, which no edge fraction reaches",
        reader="gen_receipt.open_surface_warning", real=2, derived="2026-07-29",
        provisional=True,
        note="TWO points, one batch, and the bar sits between them -- it is the midpoint of its own "
             "only two observations rather than a separation read off a spread. Settled by a second "
             "batch of generated structures and rocks (docs/ROADMAP.md)."),

    # -- The bake, and what the material claims ----------------------------------------------
    # Whether a bake landed on the surface it was baking. Three assets re-finished through the same
    # code twice, `gen_assets.map_fidelity` in-chart over the whole atlas:
    #
    #   asset       cage projection onto itself   self-bake
    #   structure   0.9015 / 6.48                 0.9980 / 2.28
    #   slab        0.9524 / 5.96                 0.9985 / 2.57
    #   stump       0.9793 / 3.21                 0.9995 / 1.12
    #
    # 0.99 and 3.0 separate every pair with room on both sides: 0.008 of margin under the worst good
    # bake and 0.011 over the best bad one. A self-bake is still a resample through Cycles and the
    # bake margin, so the bar is not 1.0, and it is nowhere near what a shredded map scores. On the
    # structure's roof charts alone, where the artist saw it, the same two runs measured 0.817
    # against 0.991.
    "bake_fidelity": Bar(
        value=0.99, unit="correlation, 0..1",
        catches="a bake that landed somewhere other than the surface it was baking",
        reader="gen_receipt.bake_fidelity_warning", real=3, derived="2026-07-28",
        provisional=True,
        note="Three assets, but each measured as a PAIR (good bake against bad) so the separation is "
             "read off six figures with margin on both sides. Thin by sample count, sound by "
             "construction; a second batch would still be worth having."),
    "bake_diff": Bar(
        value=3.0, unit="mean absolute difference, 0..255",
        catches="the same misplaced bake, on the axis correlation is blind to",
        reader="gen_receipt.bake_fidelity_warning", real=3, derived="2026-07-28",
        provisional=True, note="The companion of `bake_fidelity`; same three pairs."),

    # What an image-to-3D model decided about metalness. Nothing else in the pipeline looks at it and
    # one generated asset came back fully metal. Ten staged GLBs, every one declaring
    # `metallicFactor` 1.0 with a metallicRoughness texture wired, so the map's mean IS the claim:
    # one at 0.83 (the failure the artist saw) and the other nine from 0.0027 down to 0.0002.
    #
    # Nothing a Bob scene ships is a metal -- rock, bark, timber, litter, thatch and leaves are all
    # dielectric -- so the bar is not a judgement about how much metal is too much. It sits over the
    # noisiest honest answer by a factor of five and under the one real failure by a factor of eight.
    "metalness": Bar(
        value=0.1, unit="mean of the metalness map, 0..1",
        catches="a generated material claiming to be metal",
        reader="gen_receipt.metalness_warning", real=10, derived="2026-07-28"),

    # -- What a shipped map contains ---------------------------------------------------------
    # A uniform map, gating what SHIPPED rather than a comparison between two things that can both be
    # empty. `bake_fidelity` cannot catch this and the reason is worth keeping: `map_fidelity` returns
    # None when either side has no variation, and `bake_fidelity_warning(None)` returns nothing, so
    # "the measurement declined to answer" and "the bake is fine" reach the receipt as the same empty
    # list -- a block-out structure shipped a 2048 square of pure black with `warnings: []`. Seven
    # maps spanning 0.00 to 57.51 of spread. A uniform map is a failure whatever it is uniform AT, so
    # the bar is on the spread; the darkness bar only picks which sentence to write.
    "map_spread": Bar(
        value=6.0, unit="standard deviation, 0..255",
        catches="a map that shipped uniform -- black, white or flat grey",
        reader="gen_receipt.empty_map_warning", real=7, derived="2026-07-29"),
    "map_dark": Bar(
        value=4.0, unit="mean, 0..255",
        catches="which sentence an empty map gets: black, or uniform at some other value",
        reader="gen_receipt.empty_map_warning", real=7, derived="2026-07-29"),

    # -- Baked lighting -----------------------------------------------------------------------
    # An albedo carrying its own key and shadow, which cannot be relit. Measured over TEN generated
    # texture sets, spanning 0.0247 (flat conifer bark) to 0.0989 (a damp forest floor with a raking
    # light across the litter) -- the upper end of that run: 0.0492 wet bare earth, 0.0509 leaf grass,
    # 0.0667 broadleaf bark, 0.0740 wet granite, 0.0742 silvered siding, then the two the artist could
    # SEE were lit, 0.0965 broadleaf leaf (the sprite's own key and shadow) and 0.0989 the floor.
    # 0.075 sits above everything that reads flat and below both lit ones, and delighting takes both
    # under it (0.0355 and 0.0662). A threshold rather than a hard failure because a lit albedo is
    # usable -- it just cannot be relit, and on a hero surface that shows.
    #
    # The count was wrong here first time round: the constant listed only the seven figures at the top
    # of the range, and docs/GENERATION.md's texture-set section records the full ten. Cross-checking
    # the two is what caught it, which is the argument for one table over prose in two places.
    "flatness": Bar(
        value=0.075, unit="fraction of a stop",
        catches="an albedo with baked lighting in it",
        reader="gen_receipt.flatness_warning", real=10, derived="2026-07-29"),

    # The same question asked the way a leaf CARD asks it, and THE BAR THIS REGISTRY EXISTS TO EXPOSE.
    #
    # Its first version was one stop of TOTAL variation inside the opacity mask, which was wrong in a
    # way that cost a gate: five atlases were generated chasing it, the thing that finally moved the
    # number was describing a flatter SUBJECT, and that rewrite is what broke sprite orientation --
    # a pressed specimen has no twig for `orient_sprite` to read. An unmeasured property regressed
    # while a measured one improved. `comfy_maps.mask_light_split` separates the two: a needle spray's
    # variation is overwhelmingly one needle shadowing the next, which is real geometry a flat card
    # cannot carry and so belongs in the albedo, and only the RAMP across a sprite is light that
    # cannot be relit.
    #
    # The derivation is the problem rather than the number. 0.55 came from the worst per-cell figure
    # over the three atlases the asset gate shipped, and those three were generated with the "pressed
    # flat like a herbarium specimen" wording -- which suppressed the ramp precisely BECAUSE it
    # flattened the subject, and which is the wording that broke both orientation cues. So the bar was
    # calibrated on samples that were defective in the other dimension, and has been rejecting every
    # atlas made correctly since.
    #
    # It now HAS its second batch and the batch says the bar is wrong. Ten real atlas sheets at
    # `in_mask_ramp_stops`: 0.235, 0.305, 0.627, 0.692, 0.711, 0.978, 1.075, 1.153, 1.317, 1.402.
    # **Two of ten pass 0.55.** A bar that rejects 80% of ordinary requests is not a gate, it is a
    # blocked route, and the pass-rate harness reports the atlas class at 0% because of it.
    "leaf_ramp_stops": Bar(
        value=0.55, unit="stops of ramp inside the opacity mask",
        catches="a leaf sprite lit by a gradient, which a flat card cannot relight",
        reader="gen_receipt.flatness_warning", real=10, synthetic=1, derived="2026-07-30",
        provisional=True,
        note="MEASURED WRONG, not merely thin: 8 of its 10 real samples FAIL it, so it blocks the "
             "atlas class rather than gating it. Its 0.55 came from three atlases made with wording "
             "that was itself defective in the orientation dimension. Two ways out and the second is "
             "a fix rather than a moved goalpost: re-derive from the ten, or subtract the plane "
             "`comfy_maps.mask_light_split` already fits, which makes the bar and the measurement "
             "agree by construction (docs/ROADMAP.md)."),

    # -- Leaf atlases -------------------------------------------------------------------------
    # A cell with no sprite in it. The bar existed in `headless_foliage.py` as
    # `all(c["opaque"] > 0.02)`, which was the problem: an atlas shipping a blank cell had a clean
    # RECEIPT, and the card built on that cell renders as nothing -- not as a smaller leaf. Same bar,
    # moved to where it reaches somebody. Its evidence is the mechanism rather than a spread: a cell
    # at 0.0 opaque carries no sprite at any threshold, and 0.02 is the noise floor of the alpha
    # channel after the bleed passes.
    "cell_opaque": Bar(
        value=0.02, unit="fraction of the cell that is opaque",
        catches="an atlas cell with no sprite in it",
        reader="gen_receipt.blank_cell_warning", real=3, derived="2026-07-30",
        provisional=True,
        note="The three shipped atlases; the count is inferred from the batch, not recorded at the "
             "constant. A noise floor rather than a separation -- the failure it catches measures "
             "0.0 opaque and a pass measures tenths -- so the count matters less here than the "
             "mechanism does."),

    # A sprite whose alpha fringe is soft enough to render a white halo. The bleed pushes leaf colour
    # into the transparent region and this is the coverage at which a texel counts as leaf rather
    # than as fringe.
    "atlas_opaque": Bar(
        value=0.9, unit="alpha, 0..1",
        catches="a white fringe around a card: colour bled from unpainted transparent texels",
        reader="comfy_maps.alpha_bleed", judges="asset", real=3, derived="2026-07-29",
        provisional=True,
        note="The three shipped atlases, inferred from the batch rather than recorded. A mechanism "
             "bar rather than a measured separation: below this coverage a texel IS fringe."),

    # -- Sprite orientation ------------------------------------------------------------------
    # Whether a sprite's rotation can be READ at all, so a card is not hung by its tips. Twelve cells
    # across three generated atlases, split by whether the artist could see which end attached:
    #
    #   anisotropy  how much longer than wide the mask is
    #     readable:    2.20, 2.56, 2.83, 3.10, 4.42, 6.90
    #     arbitrary:   1.02, 1.09, 1.21, 1.33, 1.51
    #   end_ratio   the narrow end's RMS width over the wide end's
    #     readable:    0.17, 0.38, 0.44, 0.46, 0.53, 0.66
    #     arbitrary:   0.89, 0.90, 0.92, 0.94, 0.99
    #
    # 1.6 and 0.75 sit in the gap on both figures (1.51 against 2.20, 0.66 against 0.89). Wanted
    # TOGETHER, because either alone passes a shape with no attaching end: a symmetric ellipse is
    # anisotropic with equal ends, and a lopsided blob tapers along an axis that is noise.
    "axis_anisotropy": Bar(
        value=1.6, unit="ratio of covariance eigenvalues",
        catches="a sprite with no long axis, so its rotation is a guess",
        reader="comfy_maps.orient_sprite", real=12, derived="2026-07-29"),
    "axis_taper": Bar(
        value=0.75, unit="narrow end RMS width / wide end",
        catches="a sprite with two alike ends, so which one attaches is a guess",
        reader="comfy_maps.orient_sprite", real=12, derived="2026-07-29"),

    # The third condition, about the axis cue being WRONG rather than unreadable. The assumption is
    # "the narrow end is the cut stub", and a needle spray breaks it: a fir sprig tapers at BOTH ends,
    # so the tip can be narrower than the stub and the sprite comes back exactly upside down with
    # healthy taper figures -- the gate's `leaf_conifer` top-right cell, turned -171 degrees at
    # anisotropy 2.56 and end ratio 0.44, both well inside the bars above.
    #
    # What separates the ends is not width, it is whether one is a FAN: a cut stub is one solid strand
    # and a needle tip is several. Median gaps-apart runs a slice across the band cuts, same twelve
    # cells:
    #
    #   stub / fan, cue is right   1/4, 1/3, 1/8
    #   no fan either end, no cue  1/1, 1/1     (the cell that shipped upside down, and one other)
    #   fanned at BOTH ends        5/4, 2/2
    #
    # So the wide end has to be at least two strands wider than the narrow one, which every right
    # answer clears and no wrong one does.
    "axis_strand_contrast": Bar(
        value=2, unit="strands, wide end minus narrow",
        catches="a sprite oriented upside down: a needle tip mistaken for a cut stub",
        reader="comfy_maps.orient_sprite", real=12, derived="2026-07-29"),

    # With one escape, because a fan is not the only honest stub cue: a single broad leaf on a petiole
    # is one strand at both ends and its taper is still unmistakable, and failing it would flag the
    # very subject this fix wants the prompt to ask for.
    "axis_strong_taper": Bar(
        value=0.25, unit="narrow end RMS width / wide end",
        catches="the escape hatch: taper so strong the stub end is unmistakable without a fan",
        reader="comfy_maps.orient_sprite", real=1, derived="2026-07-29",
        provisional=True,
        note="ONE point, and the only bar here not read off a separating pair: it sits under the "
             "thinnest measured end ratio that also had a fan (0.38) and over the one solidly "
             "tapered cell in the twelve (0.17). Settled by a second atlas batch."),

    # The woody cue, and the other half of orientability: a stem to find. `excess` is green excess,
    # G - (R+B)/2, and a texel at or below this counts as woody. The separation is the green/brown
    # split a real sprig has, which is why the prompt has to ask for FRESH GREEN leaves -- a brown
    # dried specimen has no split for this to read.
    "woody_excess": Bar(
        value=2.0, unit="green excess, 0..255",
        catches="which texels are stem rather than leaf, so a stem can be found at all",
        reader="comfy_maps.orient_sprite", real=12, derived="2026-07-29"),
    "woody_separation": Bar(
        value=0.025, unit="fraction of the sprite's diagonal",
        catches="a woody patch too close to the leaf mass to be a stem",
        reader="comfy_maps.orient_sprite", real=12, derived="2026-07-29"),

    # -- The conditioning input, not the output ----------------------------------------------
    # How much of a block-out's surface may be INSIDE it. The control is read as an area-weighted
    # surface sample, so an interior face is a conditioning point describing a surface that is not
    # there. Measured: a shed built as a wall cube plus a roof prism put 125.94 m of 425.98 -- 29.6%
    # -- on a solid slab at wall height, and the generation came back an A-frame with its walls gone.
    # Built as one shell the same shape measures 2.95 of 313.98, 0.9%.
    #
    # An ASSET bar and not a code one, and the distinction is the point: the block-out gate only ever
    # sees the SHIPPED shapes, and an artist's own block-out -- the normal case, since
    # `export_control` takes any object -- was measured by nothing at all. The gate reads this same
    # bar (`judges="code"` is for a bar only a gate has), which is why there is one entry and not two:
    # they were two constants at 0.05 in two files until this registry counted them.
    "control_hidden": Bar(
        value=0.05, unit="fraction of total surface area",
        catches="a block-out conditioning generation on its own interior",
        reader="gen_receipt.control_surface_warning + the block-out gate's hidden-surface check",
        real=2, derived="2026-07-29",
        provisional=True,
        note="Two shapes -- 29.6% rejected in a render, 0.9% accepted -- so the bar sits in a very "
             "wide gap on two points. Wide enough that a third sample is unlikely to move it, but it "
             "is two points and says so."),

    # -- Bark ---------------------------------------------------------------------------------
    # Grain running across the trunk instead of along it, measured on every bark set. A tileable SDXL
    # pass has no reason to keep an axis: "rough conifer bark" with no clause came back polygonal mud
    # cracks 84 degrees off vertical; naming the direction alone measured 71.3 worst case; the shipped
    # clause ("vertical bark, deep furrows running top to bottom") holds it to 17.6 worst case across
    # species and seeds. 25 sits above every shipped figure and far below every failure.
    #
    # Gated only in `headless_foliage.py` until 2026-07-30, with `comfy_bark_set`'s own description
    # telling the artist the bar lived in a gate script -- so a set could ship with its grain running
    # sideways and a clean receipt. The gate keeps its own separate bar (`bark_shear`), and the split
    # is the rule: a gate asserts properties of the CODE, a receipt judges the ASSET.
    "grain_off_vertical": Bar(
        value=25.0, unit="degrees off vertical",
        catches="bark whose grain wraps the trunk instead of running up it",
        reader="gen_receipt.grain_warning", real=3, derived="2026-07-30",
        provisional=True,
        note="Three measured PROMPT WORDINGS (83.8 no clause, 71.3 direction only, 17.6 shipped "
             "clause), not three sets: the shipped figure is a worst case over species and seeds "
             "whose count was never recorded at the constant. The separation is enormous, so the "
             "value is not in doubt; the sample count is."),

    # -- The stylised paint route ---------------------------------------------------------------
    # How much of an asset's charts the projection actually painted from a camera, as against left
    # to the hole fill. The route exists because `mesh_texture` conditions on ONE image and invents
    # every surface that image cannot see, so "was this texel painted by a view that could see it"
    # is the question the whole route is an answer to -- and the hole fill is the honest failure
    # mode: it spreads neighbouring colour into what nothing saw, which looks like a texture and
    # carries no information.
    #
    # Two measurements, both at eight views (a six-view ring plus the two extra elevations): 92.6%
    # of chart texels painted directly on a 3,910-face generated boulder with 29 texels left for the
    # fill, and 99.9% with 0 left on a 1,280-face displaced ico-sphere. 0.9 sits under both, and the
    # gap between them is the point -- the figure tracks how much of a shape the ring can see, so it
    # is a property of the SUBJECT rather than of the code.
    "paint_coverage": Bar(
        value=0.90, unit="fraction of chart texels painted from a view",
        catches="a paint that left the charts to the hole fill: too few views, or views that could "
                "not see the surface",
        reader="gen_receipt.paint_coverage_warning", real=2, derived="2026-07-30",
        provisional=True,
        note="TWO assets, both at eight views, and the bar sits below both rather than between them "
             "-- so it is a floor read off the gate's own assertion, not a separation read off a "
             "spread. The case that would move it is a concave or enclosing shape at a low ring "
             "elevation: a 20-degree ring ALONE left 28% of a closed boulder to the fill, which is "
             "why the two extra elevations exist. Settled by a batch across shapes and ring counts."),

    # The seam, from the other side: two adjacent views have to SHARE enough texels for their
    # disagreement to be a measurement at all. Under this the pair's MAD is a statistic over a
    # handful of texels and reads as noise, so a genuine seam hides behind an unreliable number
    # rather than showing up as one.
    "view_overlap": Bar(
        value=200, unit="texels shared by an adjacent view pair",
        catches="a turntable too sparse to measure its own seam, so the seam figure means nothing",
        reader="gen_receipt.view_overlap_warning", real=2, derived="2026-07-30",
        provisional=True,
        note="Two observations, and they are a long way apart: a SIX-view ring on a displaced "
             "ico-sphere shared 83,699 to 104,750 texels per adjacent pair, and a FOUR-view ring on "
             "a UV sphere shared ZERO on one pair -- a cylindrical chart split into longitudinal "
             "bands that two views 90 degrees apart simply do not both see. So the case this fires "
             "on is not exotic, which is what the second sample changed: it is the default ring "
             "count lowered by two on an ordinary layout, and the sentence tells the artist to raise "
             "it. A count rather than a fraction, so it does not carry across texture sizes the way "
             "the coverage bar does: 200 of a 1024 square is not 200 of a 4096. Settled by one "
             "stand at two texture sizes and two ring counts."),
}


# ---------------------------------------------------------------------------------------------
# The code bars: a gate script reads each one. No receipt sentence does, and that is correct --
# these assert that the CODE still works (a prompt clause still holds, a route is still wired),
# which is not a property of any one asset.
# ---------------------------------------------------------------------------------------------
_CODE = {
    # The bark prompt clause, asserted as a property of the clause rather than of a set: a sheared
    # bark texture on the shipped species means the wording regressed. A different number from
    # `grain_off_vertical` because it measures a different thing -- the shear of the built trunk's UV
    # mapping, not the source texture's grain angle.
    "bark_shear": Bar(
        value=0.5, unit="shear ratio",
        catches="the bark prompt clause regressing, read off the built trunk",
        reader="headless_foliage.py, the bark section", judges="code", real=2,
        derived="2026-07-29",
        note="Two species, each as a before/after distribution: conifer worst case 1.713 -> 0.184, "
             "broadleaf 1.825 -> 0.159. 0.5 sits far above every correct build and far below the "
             "defect, and half a tile is also where a sideways slide stops reading as bark."),

    # The block-out route's own two bars. `hidden_area` is the same 0.05 as `control_hidden` above and
    # deliberately reads that entry rather than declaring a second copy; this one is the PROFILE half.
    # Band-by-band height profile deviation: the shipped shell holds 0.0753 against 0.10 where a
    # synthetic A-frame at the same bbox scores 0.2551.
    "profile_deviation": Bar(
        value=0.10, unit="max band deviation, fraction",
        catches="a generation that ignored the block-out's height profile",
        reader="headless_gen_blockout_control.py part C", judges="code", real=1, synthetic=1,
        derived="2026-07-29",
        note="One real generation against one synthetic counter-shape. Its intuitive sibling -- mean "
             "plan area over the lower half -- is reported and deliberately NOT gated, because it "
             "reads 1.1127 on the A-frame it was written to catch. Re-executed on 2026-07-30 with the "
             "bar read from here: 0.0214 measured against 0.1, on a shape whose own self-comparison "
             "is 0.0146 and whose counter-shape is 0.2551."),

    # Whether the geometry A/B's albedo actually carries an image. A floor rather than a separation:
    # the failure it catches measured 0.06 spread against a 59.3 pass, three distinct causes deep.
    "albedo_floor": Bar(
        value=0.02, unit="mean, 0..1",
        catches="a black albedo: the encoder handed a mesh outside its unit cube",
        reader="headless_gen_geometry_ab.py", judges="code", real=2, derived="2026-07-29",
        note="Two measurements either side of a chasm: a black albedo at 0.06 spread against 59.3 "
             "once the mesh was normalised into the encoder\'s unit cube. Deliberately the same "
             "number the asset gate uses, so both gates\' figures mean one thing."),

    # What the asset gate calls an open mesh AS IT SHIPS, before any repair. A count and not a
    # fraction on purpose: this one asserts the repair ran, and the repair's own output is compared
    # against the source count beside it.
    "open_boundary_edges": Bar(
        value=500, unit="boundary edges",
        catches="the mesh repair not running at all on a route that needs it",
        reader="headless_gen_assets.py", judges="code", real=1, derived="2026-07-28",
        note="ONE case -- the leaf measurement, which is what TRELLIS.2 exists to handle and Hunyuan "
             "structurally cannot. The constant says so in as many words."),

    # A generated mesh that came back as a slab or a needle rather than the subject asked for.
    "thin_axis_ratio": Bar(
        value=0.25, unit="shortest bbox axis / longest",
        catches="a degenerate generated mesh: flat slab or spike",
        reader="headless_gen_assets.py", judges="code", real=1, derived="2026-07-28",
        note="The same single leaf measurement as `open_boundary_edges`."),

    # The two control-route A/B bars, shared by the bbox and voxel gates. They were declared twice,
    # verbatim, in the two scripts -- which is exactly the drift this registry removes: the two gates
    # were comparing against the same numbers by coincidence rather than by construction.
    "control_draw": Bar(
        value=0.02, unit="score difference",
        catches="an A/B whose two sides are too close to call, reported as a draw not a win",
        reader="headless_gen_bbox_control.py and headless_gen_voxel_control.py", judges="code",
        real=3, derived="2026-07-29",
        note="Three block-outs. 0.02 is under the smallest gap between any two ceilings the control "
             "gate measured, and the rule was fixed BEFORE the run so the verdict could not be "
             "chosen after seeing the table."),
    "control_win": Bar(
        value=2, unit="wins out of the pairs measured",
        catches="a control mode declared better on one lucky pair",
        reader="headless_gen_bbox_control.py and headless_gen_voxel_control.py", judges="code",
        real=3, derived="2026-07-29",
        note="Win or draw on at least two of the three block-outs, and not slower."),
    "control_wired": Bar(
        value=2, unit="wins out of the pairs measured",
        catches="the voxel route's own wiring, asserted separately from its score",
        reader="headless_gen_voxel_control.py", judges="code", real=3, derived="2026-07-29",
        note="How many of the three a properly controlled run must beat its own SWAPPED NULL on. "
             "Below it the control is not reaching the model and there is no verdict to read, only a "
             "defect -- an Omni control that misses never errors."),

    # Whether a voxel-conditioned generation kept the block-out's FOOTPRINT.
    "footprint": Bar(
        value=0.5, unit="fraction of the control's plan area",
        catches="a generation that kept the silhouette and lost the plan",
        reader="headless_gen_voxel_control.py", judges="code", real=0, derived="2026-07-29",
        note="NO SAMPLE COUNT IS RECORDED at the constant, which is itself the finding: it says only "
             "that it belongs to the ADOPTED mode and to no other, because holding a challenger to "
             "the winner\'s bar records a negative result as a broken suite. True, and not evidence "
             "for 0.5."),
}

BARS = {**_ASSET, **_CODE}


def value(name):
    """One bar's value. The accessor the call sites use, so the number has exactly one home."""
    return BARS[name].value


def asset_bars():
    return {k: b for k, b in BARS.items() if b.judges == "asset"}


def code_bars():
    return {k: b for k, b in BARS.items() if b.judges == "code"}


def undeclared():
    """Bars gating shipped assets on thin evidence WITHOUT saying so. The test fails on a non-empty
    list, which is the whole mechanism: a thin bar is allowed, an undeclared one is not."""
    return sorted(k for k, b in BARS.items() if b.undeclared)


def table_rows():
    """The generated bar table's rows, as (name, value, unit, evidence, reader, catches).

    Here rather than in the doc generator so the FORMAT of a bar's evidence is decided beside the
    bars: "10 real, 1 synthetic" is a sentence about this data model, and a generator that assembled
    it would be a second place that knows what a sample count means.
    """
    rows = []
    for name, bar in sorted(BARS.items()):
        val = f"{bar.value:g}" + (f" (floor {bar.floor:g})" if bar.floor is not None else "")
        evidence = f"{bar.real} real" + (f", {bar.synthetic} synthetic" if bar.synthetic else "")
        if bar.provisional:
            evidence += " -- **provisional**"
        elif bar.thin:
            # A thin CODE bar is not required to declare itself (see `undeclared`), but it must not
            # read as settled in the table either. Every gate bar in the repo is currently thin.
            evidence += " -- thin"
        rows.append((name, val, bar.unit, evidence, bar.reader, bar.catches))
    return rows
