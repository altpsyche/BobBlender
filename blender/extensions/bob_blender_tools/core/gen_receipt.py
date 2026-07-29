"""The receipt-warning vocabulary: a generated asset's measurements, turned into sentences.

Seven pure functions over the report dicts `core.gen_assets` builds, each returning a list of zero
or one sentence. They exist because of one lesson this pipeline learned four separate times: **a
measurement that reaches no caller is not a check.** `prepare_low` counted boundary edges,
`source_opacity` classified the alpha, `map_fidelity` scored the bake and `flatness_report` measured
the light -- every one of them computed, dropped before the bridge, and read by nobody, while assets
shipped with `warnings: []` and the defect was found in a render.

`REPORT_KEYS` is what stops there being a fifth time. Every key a receipt carries is declared there
as either GATED (some function here reads it and can produce a sentence) or INFORMATIONAL (it is
context a reader wants and nothing can fail on), and `tools/tests/test_gen_receipt.py` fails when a
key appears in a receipt without being in one of the two. A new measurement therefore cannot be
added silently: it either gets a warning or it gets an explicit decision that it does not need one.

Split out of `core.comfy`, which hosted these only because it is bpy-free and so testable in the
plain venv. That is a hosting reason rather than a cohesion one: `comfy` is the ComfyUI client and
the generation recipes, and this is the vocabulary for judging what they produced. This module is
bpy-free for the same reason and imports only `comfy` (for `is_foliage`) and `comfy_maps` (for the
orientation thresholds it quotes back to the artist), so nothing here can import bpy and `comfy`
does not import this -- the dependency runs one way only.
"""

try:
    from . import comfy, comfy_maps
except ImportError:  # `core` itself on sys.path (the venv / headless route), where there is no
    import comfy  # parent package -- the same fallback `comfy` itself uses
    import comfy_maps


# Which kinds READ as leaves, which is a wider set than `comfy.FOLIAGE_KINDS` and a different
# question. That one is about geometry processing (keep the holes open) and excludes trees, which
# are solids; this one is about the finished LOOK, and a tree is in it because its crown is the
# whole reason an artist generated it. The two deliberately disagree and the names have to say so.
LEAFY_KINDS = ("trees", "plants", "grass")


def leaf_opacity_warning(kind, opacity):
    """The dead-wood routing rule's receipt warning, as a list of zero or one sentence
    (docs/FOLIAGE.md).

    A leaf is a cutout or it is not a leaf. `gen_assets.source_opacity` already measures which of
    the three cases a generated texture is in and refuses to wire an implausible channel; what was
    missing is that the refusal never reached the caller, so a tree landed with a clean receipt and
    the artist found out by looking at a render. Over six generated foliage assets every one came
    back `opaque` or `implausible`, and three tree attempts were spent before anyone said so.

    Here rather than in `gen_assets` because it is a pure function of the report and this module is
    bpy-free, so it is testable in the venv beside the rest of the generation vocabulary.
    """
    if str(kind or "") not in LEAFY_KINDS:
        return []
    verdict = (opacity or {}).get("verdict")
    if verdict == "cutout":
        return []
    return [f"no usable opacity channel (verdict: {verdict or 'none'}): this asset reads as solid "
            f"geometry, not leaf cards. Image-to-3D makes dead wood (stumps, logs, snags) and "
            f"ground clumps; standing trees and crowns come from the foliage generator "
            f"(docs/FOLIAGE.md)"]


# When a SOLID kind's residual openness is worth saying out loud, as a fraction of its face count
# plus a floor so a small mesh is not warned for two edges. Both numbers come off five generated
# solids, measured after the weld and the pinhole fill: stump 73 of 3,840 faces (1.9%) and rock slab
# 72 of 2,900 (2.5%) are the two the artist could see through, against fallen log 17 of 3,908
# (0.4%), a gabled structure 11 of 7,502 (0.15%) and boulder 9 of 3,807 (0.24%) which read as
# closed. A fraction rather than a count because a 500-face rock and a 40,000-face building are not
# the same claim.
OPEN_SURFACE_FRACTION = 0.01
OPEN_SURFACE_FLOOR = 12

# The same two bars asked of the SEE-THROUGH edges alone, once `gen_assets.openness_report` can tell a
# hole from a pit. Both are needed because they catch opposite shapes and each is blind to the other:
#
#   a sieve    hundreds of tiny holes, so a large share of the faces and no single big opening. The
#              generated boulder arrived like this (19,623 boundary edges before the pinhole fill)
#              and the fraction is what catches it.
#   one wedge  a gabled structure's missing corner under the left eave: ONE loop, 5 edges of 8,839
#              faces, 1.59 m across. 0.06% of the faces, so no edge fraction will ever fire on it,
#              and the artist saw it immediately. The opening's own size is what catches that.
#
# The opening bar is over the object's longest dimension and sits between the two measured cases: the
# ground rock's biggest see-through opening is 0.07 of the rock, which the artist accepts as
# vesicular stone, and the gabled structure's is 0.18, which the artist rejected in a render.
#
# TWO POINTS, one batch of generated solids, and the bar is where it is because they are the only
# two: recalibrate when a second batch of structures and rocks exists (docs/ROADMAP.md).
SEETHROUGH_FRACTION = 0.01
SEETHROUGH_OPENING_FRACTION = 0.10


def open_surface_warning(kind, report):
    """A solid kind shipping with holes in it, as a list of zero or one sentence.

    The companion to `leaf_opacity_warning`, and it exists for the same reason: a measurement that
    reaches no caller is not a check. `prepare_low` has always counted the shipped mesh's boundary
    edges, and five generated meshes still shipped carrying 48 to 229 of them with
    `warnings: []` -- because the count was computed, dropped before the bridge, and read by nobody.
    The stump's holes were then found in a render.

    Foliage is exempt and that is the whole point of the openness split: a leaf blade IS an open
    surface (`comfy.FOLIAGE_KINDS`), and the pinhole fill is off for it deliberately, so warning
    about it would train an artist to ignore the one warning that means something.

    Reads `low_openness` first, which says what KIND of openness it is, and that changed both of the
    verdicts this check got wrong. The ground rock was warned about twice for openness that is real
    vesicular stone (50 of its 60 boundary edges are pit mouths), and the gabled structure passed at
    0.2% while carrying a 1.59 m hole under one eave. `SEETHROUGH_FRACTION` has the two bars and
    `gen_assets.openness_report` has how a pit is told from a hole.

    Falls back to the plain `low_boundary_edges` count when that block is absent -- an older asset, or
    a route that did not run it -- and THERE the weld matters: glTF splits a vertex at every UV seam,
    so an unwelded import reads several times its real openness (the gate stump measured 3,646
    one-face edges against a real 229) and the count stays quiet rather than crying wolf about charts.
    `openness_report` welds a copy of its own and says so in `welded`, so it needs no such guard.
    """
    if comfy.is_foliage(kind):
        return []
    faces = report.get("faces") or 0
    if not faces:
        return []
    closed = report.get("pinholes_closed")
    filled = (f", {closed} closed by the pinhole fill" if closed
              else ", and the pinhole fill did not run")

    openness = report.get("low_openness")
    if isinstance(openness, dict) and openness.get("welded") and openness.get("loops"):
        holes = int(openness.get("seethrough_edges") or 0)
        opening = float(openness.get("largest_seethrough_fraction") or 0.0)
        sieve = holes > max(OPEN_SURFACE_FLOOR, int(faces * SEETHROUGH_FRACTION))
        gaping = opening > SEETHROUGH_OPENING_FRACTION
        if not sieve and not gaping:
            return []
        pits = int(openness.get("pit_edges") or 0)
        metres = float(openness.get("largest_seethrough_m") or 0.0)
        figures = []
        if sieve:
            figures.append(f"{holes} of them over {faces} faces ({holes / faces:.1%})")
        if gaping:
            figures.append(f"the largest {metres:.2f} m across, {opening:.0%} of the object")
        return [f"ships with {holes} boundary edges you can see through, in "
                f"{openness.get('seethrough_loops')} opening(s): {'; '.join(figures)}{filled}. A "
                f"backface behind an opening is what a hole looks like in a render, at any camera. "
                f"Regenerate, raise the face budget so the remesh has somewhere to put the detail, "
                f"or condition the geometry on a block-out so the shell has a silhouette to close "
                f"against. The other {pits} boundary edges are pit mouths with surface behind "
                f"them, and are not the problem"]

    open_edges = report.get("low_boundary_edges")
    if not open_edges:
        return []
    if report.get("simplify_source") == "trellis2" and "low_welded_verts" not in report:
        return []  # unwelded, so the figure is UV seams and not holes
    threshold = max(OPEN_SURFACE_FLOOR, int(faces * OPEN_SURFACE_FRACTION))
    if open_edges <= threshold:
        return []
    return [f"ships with {open_edges} boundary edges over {faces} faces "
            f"({open_edges / faces:.1%}{filled}): this is an open surface, and a solid kind should "
            f"not be one. Holes read through the shell at any close camera. Regenerate, or raise "
            f"the face budget so the remesh has somewhere to put the detail"]


def orientation_warning(cells):
    """Cells whose up was a guess, as a list of zero or one sentence.

    The fourth of these receipt warnings and the one the second artist rejection asked for. The
    other three carry a measurement that existed and reached nobody; this one carries a measurement
    that did not exist. `comfy_maps.orient_sprite` decides which end of a sprite attaches from
    either the woody/green split or the mask's principal axis, and when NEITHER is readable it
    rotates by the axis anyway. Nothing downstream can see that: the sprite is still
    bottom-anchored, its cell is still full, `cell_distinctness` still passes, and `base_taper`
    still reads narrow-at-the-base, because a compact leaf is narrow at whichever edge it was set
    down on. The gate shipped three atlases with the petiole pointing sideways and a clean receipt.

    Deliberately about the DECISION and not the result. There is no way to measure "this leaf is the
    right way up" from the matte alone -- if there were, the orienter would use it and be right --
    so what is reported is whether the cue the rotation rests on could be read, which is a fact
    about the sprite the generator returned and is exactly the property the prompt controls.

    Reads the `orient` block `leaf_atlas` folds into each cell. Cells without one (the grid route, or
    a set written before this existed) are not judged.
    """
    judged = [c for c in (cells or []) if isinstance(c.get("orient"), dict)]
    guessed = [c for c in judged if not c["orient"].get("resolved")]
    if not judged or not guessed:
        return []

    def figures(cell):
        o = cell["orient"]
        ratio = o.get("end_ratio")
        strands = o.get("strands") or (0, 0)
        return (f"cell {cell['cell']} turned {o.get('angle_deg') or 0.0:+.0f} degrees on the "
                f"{o.get('cue', 'none')} cue (woody fraction {o.get('woody_fraction') or 0.0:.3f}, "
                f"anisotropy {o.get('anisotropy') or 0.0:.2f}, end ratio "
                f"{'none' if ratio is None else format(ratio, '.2f')}, strands "
                f"{strands[0]} to {strands[1]})")

    detail = "; ".join(figures(c) for c in guessed)
    return [f"{len(guessed)} of {len(judged)} sprites were turned upright on a cue that could not be "
            f"read, so their rotation is a guess and a card may hang by its tips: {detail}. A "
            f"sprite is orientable when it has a woody stem to find (woody fraction over "
            f"{comfy_maps.WOODY_MIN}) or a long axis whose narrow end is a solid stub against a "
            f"fanned tip (anisotropy over {comfy_maps.AXIS_ANISOTROPY_MIN}, end ratio under "
            f"{comfy_maps.AXIS_TAPER_MAX}, and either {comfy_maps.AXIS_STRAND_CONTRAST_MIN} more "
            f"strands at the wide end or an end ratio under {comfy_maps.AXIS_STRONG_TAPER_MAX}). "
            f"Ask the prompt for a sprig ON ONE SHORT TWIG rather than a pressed or isolated "
            f"specimen: the twig is what both cues read"]


# A cell with no sprite in it. `leaf_atlas` measures `opaque` per cell and the bar has existed since
# the foliage gate -- `all(c["opaque"] > 0.02)` in `headless_foliage.py` -- which is the problem: an
# atlas that ships a blank cell has a clean RECEIPT, and the card built on that cell renders as
# nothing. The brief calls it out in words ("a cell with `opaque: 0.0` is a card that renders as
# nothing") and nothing turned those words into a sentence the artist gets. Same bar, moved to where
# it reaches somebody.
CELL_OPAQUE_MIN = 0.02


def blank_cell_warning(cells):
    """Atlas cells that carry no sprite, as a list of zero or one sentence.

    Separate from `orientation_warning` because they fail differently and are fixed differently: a
    misoriented cell is a card hanging by its tips, a blank cell is a card that is not there. A cell
    can be blank and perfectly "oriented", so one function cannot carry both.
    """
    judged = [c for c in (cells or []) if c.get("opaque") is not None]
    blank = [c for c in judged if c["opaque"] < CELL_OPAQUE_MIN]
    if not judged or not blank:
        return []
    figures = "; ".join(f"cell {c['cell']} at {c['opaque'] * 100:.1f}% opaque" for c in blank)
    return [f"{len(blank)} of {len(judged)} cells carry no sprite ({figures}), against a "
            f"{CELL_OPAQUE_MIN * 100:.0f}% bar. A card whose cell is empty renders as NOTHING -- not "
            f"as a smaller leaf -- so a tree carding onto it loses that share of its canopy with "
            f"nothing in the frame to say so. Reroll the set, or use a smaller grid: fewer cells that "
            f"are all filled beats a grid with a hole in it"]


# Bark grain that runs across the trunk instead of along it. The bar is 25 degrees off vertical and it
# is measured on every bark set -- and until now it was gated only in `headless_foliage.py`, with
# `comfy_bark_set`'s own description telling the artist so. A tileable SDXL pass has no reason to keep
# an axis: measured, "rough conifer bark" with no clause came back polygonal mud cracks 84 degrees off
# vertical, and the shipped clause holds it inside 18 across species and seeds. The gate script is the
# right place to assert the CLAUSE still works; it is the wrong place to tell somebody their bark is
# sideways.
GRAIN_OFF_VERTICAL_MAX = 25.0


def grain_warning(grain):
    """Bark whose grain is not running along the trunk, as a list of zero or one sentence."""
    off = (grain or {}).get("off_vertical_deg")
    if off is None or off <= GRAIN_OFF_VERTICAL_MAX:
        return []
    coherence = (grain or {}).get("coherence")
    return [f"the bark grain runs {off:.1f} degrees off vertical against a "
            f"{GRAIN_OFF_VERTICAL_MAX:.0f} bar"
            + (f" (coherence {coherence:.3f})" if coherence is not None else "")
            + ": on a trunk this reads as bark wrapping the wrong way round, and no bark scale hides "
              "it. Name the FEATURE and its direction in the prompt -- \"vertical bark, deep furrows "
              "running top to bottom\" is the wording that measured 17.6 degrees worst case where "
              "naming the direction alone measured 71.3 and no clause at all 83.8 -- or reroll"]


# How much of a block-out's surface may be INSIDE it. The control is read as an area-weighted surface
# sample (`Hy3DOmniPointGenerate`), so an interior face is a conditioning point describing a surface
# that is not there. Measured: a shed built as a wall cube plus a roof prism put 125.94 m of 425.98,
# 29.6%, on a solid slab at wall height, and the generation came back an A-frame with its walls gone.
# Built as one shell the same shape measures 2.95 of 313.98, 0.9%.
#
# The bar lives here rather than in the block-out gate for the reason the whole module exists: the
# gate only ever sees the SHIPPED shapes, and an artist's own block-out -- which is the normal case,
# since `export_control` takes any object -- was measured by nothing at all.
CONTROL_HIDDEN_MAX = 0.05


def control_surface_warning(control):
    """A block-out conditioning on its own interior, as a list of zero or one sentence.

    Reads the `hidden_surface` block `gen_assets.export_control` folds into its result. Deliberately
    about the CONTROL rather than about the generated mesh: by the time the mesh exists the points
    have already been sampled, and no figure on the asset says why it came back the wrong shape.
    """
    fraction = (control or {}).get("hidden_fraction")
    if fraction is None or fraction <= CONTROL_HIDDEN_MAX:
        return []
    area = (control or {}).get("hidden_area_m2")
    total = (control or {}).get("surface_area_m2")
    return [f"{fraction * 100:.1f}% of this block-out's surface is INSIDE it"
            + (f" ({area:.2f} m of {total:.2f})" if area is not None and total is not None else "")
            + f", against a {CONTROL_HIDDEN_MAX * 100:.0f}% bar. The control is sampled area-weighted, "
              f"so those faces are conditioning points describing surfaces that are not there, and "
              f"nothing about the render, the bounding box or the footprint shows it. Build the shape "
              f"as a SHELL rather than as overlapping solids -- boolean-union the parts, or delete the "
              f"faces that end up enclosed. Measured on the shipped shed: 29.6% built as a wall cube "
              f"plus a roof prism, which generated as an A-frame with no walls, and 0.9% built as one "
              f"shell at the same silhouette"]


# How faithfully a baked basecolor has to reproduce the texture it came from before the difference
# is worth saying out loud. Set from both sides of the coincident-bake fix, on three generated
# assets re-finished through the same code twice, `gen_assets.map_fidelity` in-chart over the whole
# atlas:
#
#   asset       cage projection onto itself   self-bake
#   structure   0.9015 / 6.48                 0.9980 / 2.28
#   slab        0.9524 / 5.96                 0.9985 / 2.57
#   stump       0.9793 / 3.21                 0.9995 / 1.12
#
# So 0.99 and 3.0 separate every pair with room on both sides: 0.008 of margin under the worst good
# bake and 0.011 over the best bad one. A self-bake is still a resample through Cycles and the bake
# margin, so the bar is not 1.0, and it is nowhere near what a shredded map scores. On the
# structure's roof charts alone, where the artist saw it, the same two runs measured 0.817 against
# 0.991.
BAKE_FIDELITY_MIN = 0.99
BAKE_DIFF_MAX = 3.0


# When a generated material's metalness claim is worth saying out loud. Nothing else in the pipeline
# looks at what an image-to-3D model decided about metalness, and one generated asset came back
# fully metal. Measured with `gen_assets.metalness_report` over ten staged GLBs, every one of which
# declares `metallicFactor` 1.0 with a metallicRoughness texture wired, so the map's mean IS the
# claim:
#
#   0.8286  a silvered-timber structure   the rejected one: silvered siding read as metal
#   0.0191  a dark-timber structure       the one that shipped
#   0.0027  a mossy granite boulder
#   0.0012  a broad flat mossy rock slab
#   0.0011  a fallen mossy log
#   0.0007  a small grey ground rock
#   0.0004  a weathered tree stump with roots
#   0.0003  a low wide mossy rock, a second weathered stump
#   0.0002  a second low wide mossy rock
#
# Nothing a Bob scene ships is a metal -- rocks, bark, timber, litter, thatch and leaves are all
# dielectric -- so the bar is not a judgement about how much metal is too much. It sits over the
# noisiest honest answer by a factor of five and under the one real failure by a factor of eight.
METALNESS_MAX = 0.1


def metalness_warning(kind, metalness):
    """A generated material claiming to be metal, as a list of zero or one sentence.

    Its own check rather than a clause in `bake_fidelity_warning`, because it is a different fact with
    a different fix, and because the two failures it causes look nothing alike. A diffuse bake of a
    metal surface is black, so the albedo comes back dark and `bake_fidelity` fires with the wrong
    explanation; and if the bake happens to survive, the asset still renders as a mirror in the scene,
    which no fidelity figure would ever mention.
    """
    if not metalness:
        return []
    value = metalness.get("effective")
    if value is None:
        if not metalness.get("linked"):
            return []
        # A map is wired and could not be read, which is not the same answer as "not metal" and is
        # the shape of the bug that hid this: an image packed inside a GLB reports `has_data` False
        # until its pixels are touched, and treating that as zero read a fully metal structure as
        # dielectric.
        return [f"the generated material wires a metalness map "
                f"({metalness.get('map') or 'unnamed'}) that could not be read, so whether this "
                f"asset claims to be metal is unknown. Check it before trusting the bake: a "
                f"diffuse bake of a metal surface is black"]
    if value <= METALNESS_MAX:
        return []
    where = (f"map {metalness.get('map')} averaging {metalness.get('map_mean')}"
             if metalness.get("map_mean") is not None
             else f"metallicFactor {metalness.get('factor')}")
    return [f"the generated material claims to be metal (effective metalness {value}, {where}): a "
            f"{kind or 'scene'} asset is dielectric, and this is the image-to-3D model reading a "
            f"silvered or wet surface in the reference as metal. It renders as a mirror, and a "
            f"diffuse bake of a metal surface is BLACK, so expect the basecolor to come back dark "
            f"and `bake_fidelity` to fire as well. Zero the metalness, or reroll with a reference "
            f"that does not read as bare metal"]


# When a SHIPPED map carries no picture at all. Measured over every basecolor in the generated pack,
# in 0-255:
#
#   mean    std    map
#   111.60  37.49  a vesicular ground rock    the five honest ones, over two orders of magnitude of
#    83.63  51.35  a fallen log               subject
#    67.59  39.97  a stump
#    60.29  57.51  a boulder
#    54.09  32.90  a gabled structure
#     0.17   4.13  the same structure,        the block-out route: `mesh_texture` returned a black
#                  control-conditioned        texture set
#     0.00   0.00  the same again             at 2048 square, entirely zero
#
# This exists because `bake_fidelity` cannot catch it, and the reason is worth stating:
# `gen_assets.map_fidelity` returns None when either side has no variation, and
# `bake_fidelity_warning(None)` returns nothing, so "the measurement declined to answer" and "the
# bake is fine" reach the receipt as the same empty list. The block-out structure shipped a 2048
# square of pure black with `warnings: []`.
#
# So this gates what SHIPPED rather than a comparison between two things that can both be empty. A
# uniform map is a failure whatever it is uniform at -- black, white or mid grey -- because no
# generated surface is one colour, which is why the bar is on the spread; the darkness bar only picks
# which sentence to write.
MAP_SPREAD_MIN = 6.0
MAP_DARK_MAX = 4.0


def empty_map_warning(kind, map_stats):
    """A basecolor that shipped with no picture in it, as a list of zero or one sentence.

    Reads `gen_assets.map_stats`, measured on the file that was written rather than on anything the
    bake reported about itself. The check the block-out route needed and no existing one could give:
    every other figure in that receipt was healthy -- 13,302 faces, uv_overlap 0.0, no boundary edges,
    metalness 0.0, `bake_fidelity` correlation 0.9996 -- and the asset was a black box.
    """
    stats = (map_stats or {}).get("basecolor")
    if not stats:
        return []
    spread, mean = stats.get("std"), stats.get("mean")
    if spread is None or spread >= MAP_SPREAD_MIN:
        return []
    what = ("is entirely black" if mean is not None and mean <= MAP_DARK_MAX
            else f"is one flat tone (mean {mean})")
    return [f"the basecolor that shipped carries no picture: it {what}, spread {spread} against a "
            f"{MAP_SPREAD_MIN} bar, where every honest map in this pack measures 33 to 58. Nothing "
            f"upstream reports this -- `bake_fidelity` compares the bake to its source and returns "
            f"nothing at all when either side is flat, so an empty bake reaches the receipt as an "
            f"empty warning list. Check the texture pass: a graph that painted nothing still "
            f"returns a file, and the bake carries the emptiness through faithfully"]


def bake_fidelity_warning(fidelity, metalness=None):
    """A colour bake that ran, succeeded and returned something else, as a list of zero or one
    sentence.

    The third of these receipt warnings, and the one whose absence cost the most. A bake cannot fail
    loudly: a misaligned or jittered transfer still writes a plausible-looking texture, so every
    check downstream passes and an artist finds the chevrons in a hero render.
    `gen_assets.map_fidelity` measures it against the source at the one moment both images exist;
    this is what carries the verdict to the caller.

    The claim being tested is "the colour that was already in the low mesh's own UVs came through
    them intact", which holds on every route today: the one-shot route's texture is the low mesh's
    own, and on the staged and block-out routes `mesh_texture` painted the low mesh Bob exported. A
    future route whose DENSE mesh carries a different texture in a different layout would score low
    for a legitimate reason, and would want the comparison to be against the dense mesh instead.

    `metalness` names the OTHER mechanism, and it is here because the single-cause text cost a
    diagnosis. A resample and a metal surface both trip this bar and their fixes have nothing in
    common: the first is a bake problem, the second is the material claiming to be a mirror, and a
    diffuse bake of a mirror is black whatever the transfer does. The first generated structure was
    the second case at correlation 0.9108, and this text sent the diagnosis after the transfer
    instead (`gen_assets.metalness_report`).
    """
    if not fidelity:
        return []
    corr = fidelity.get("correlation")
    diff = fidelity.get("mean_abs_diff")
    if corr is None or (corr >= BAKE_FIDELITY_MIN and (diff or 0.0) <= BAKE_DIFF_MAX):
        return []
    metal = (metalness or {}).get("effective")
    if metal is not None and metal > METALNESS_MAX:
        cause = (f"the material claims to be metal (effective metalness {metal}) and a diffuse bake "
                 f"of a metal surface is black, so read the metalness warning first -- this figure "
                 f"is downstream of it, not a second problem")
    else:
        cause = ("the colour pass resampled the surface rather than carrying it, which reads as "
                 "smeared or hatched detail at any close camera. If the material also claims to be "
                 "metal, that is the likelier cause: a diffuse bake of a metal surface is black")
    return [f"the baked basecolor does not match the texture it came from (correlation {corr}, "
            f"mean absolute difference {diff} of 255 over {fidelity.get('coverage')} of the sheet, "
            f"against a {BAKE_FIDELITY_MIN} / {BAKE_DIFF_MAX} bar): {cause}"]


# When a generated albedo is carrying enough LIGHT to say so. Measured over ten generated texture
# sets, `comfy_maps.flatness_report`'s `low_freq_variation`:
#
#   0.0247  bark_conifer                    flat; the bark clause did its job
#   0.0355  very_dark_green_damp_forest_moss
#   0.0452  leaf_conifer
#   0.0492  very_dark_wet_bare_earth_footpath
#   0.0509  leaf_grass
#   0.0667  bark_broadleaf
#   0.0740  very_dark_wet_grey_granite_bedrock
#   0.0742  weathered_silvered_grey_barn_siding
#   0.0965  leaf_broadleaf                  lit: the sprite's own key and shadow
#   0.0989  very_dark_damp_forest_floor      lit: a raking light across the litter
#
# 0.075 sits above everything that reads as flat and below the two the artist could see were lit,
# and delighting takes both of those under it (0.0355 and 0.0662). A threshold rather than a hard
# failure because a lit albedo is usable -- it just cannot be relit, and on a hero surface that
# shows.
FLATNESS_MAX = 0.075

# And the same question asked the way a leaf CARD asks it. The first version of this bar was one
# stop of TOTAL variation inside the opacity mask (`comfy_maps.mask_stops`), and it was wrong in a
# way that cost a gate: `leaf_conifer` sat at 1.143 against it, five atlases were generated chasing
# it, the thing that finally moved the number was describing a flatter SUBJECT -- and that rewrite
# is what broke the sprite orientation, because a pressed specimen has no twig for `orient_sprite`
# to read. An unmeasured property regressed while a measured one improved, one level up from the
# four defects the first round found. The bar was measuring two things at once.
# `comfy_maps.mask_light_split` separates them: a needle spray's variation is overwhelmingly one
# needle shadowing the next, which is real geometry a flat card cannot carry and so belongs in the
# albedo, and only the RAMP across a sprite is light that cannot be relit. Measured per cell over
# three generated atlases the worst ramp is 0.48 of a stop, and a synthetic half-stop key across
# the same sprites takes it to 0.60. So the bar is on the ramp, it sits between those two, and the
# detail figure is reported beside it and deliberately not gated. That is also the answer to the
# artist's open question about a per-species bar: there does not need to be one. A needle spray and
# an oak leaf differ in the DETAIL column, which nothing gates.
#
# TWO POINTS, and one of them is synthetic: 0.48 is the worst real cell and 0.60 is that same cell
# with a key painted onto it. Recalibrate when a second batch of atlases exists (docs/ROADMAP.md).
LEAF_RAMP_STOPS_MAX = 0.55


def flatness_warning(flatness, *, leafy=False):
    """A generated albedo carrying baked lighting, as a list of zero or one sentence.

    The largest thing this round of generated sets found, and the one with no measurement at all
    before it.
    The prompts have always asked for flat light -- `PROMPT_SUFFIX` carries "flat even lighting" and
    `SUBJECT_SUFFIX` "even diffuse studio lighting" -- so the intent was right and the enforcement
    absent: the structure's reference came back an overcast outdoor photograph with a sky gradient
    and an eave shadow in it, and nothing said so until the hero render.

    `leafy` adds the card's own question and explains more: a leaf card is lit from BOTH sides, so
    shading baked into a sprite fights the renderer on whichever side is currently dark, and there is
    no camera angle that hides it. What it reads is `in_mask_ramp_stops`, the worst cell's LIGHT ramp
    (`comfy_maps.mask_light_split`), and not the total variation inside the mask -- see
    `LEAF_RAMP_STOPS_MAX` for the gate that cost, and for why the per-needle detail beside it is
    reported and not gated.
    """
    if not flatness:
        return []
    value = flatness.get("low_freq_variation")
    stops = flatness.get("in_mask_ramp_stops") if leafy else None
    over_ramp = value is not None and value > FLATNESS_MAX
    over_stops = stops is not None and stops > LEAF_RAMP_STOPS_MAX
    if not over_ramp and not over_stops:
        return []
    fix = ("pass `delight=True` to divide the lighting out, or reroll with a flatter reference"
           if not flatness.get("delighted")
           else "delighting ran and did not bring it under the bar, so reroll: the lighting in "
                "this generation is not a low-frequency ramp")
    figures = []
    if over_ramp:
        figures.append(f"low-frequency variation {value} against a {FLATNESS_MAX} bar")
    if over_stops:
        detail = flatness.get("in_mask_detail_stops")
        beside = f", against {detail} stops of per-needle detail" if detail is not None else ""
        figures.append(f"{stops} stops of light ramping across a sprite against a "
                       f"{LEAF_RAMP_STOPS_MAX} bar{beside}")
    card = (" A leaf card is lit from both sides, so no camera angle hides it."
            if leafy else "")
    return [f"the albedo carries baked lighting ({'; '.join(figures)}): a basecolor is "
            f"reflectance, and light baked into one cannot be relit.{card} {fix}"]



# -- The receipt's own shape, and the check that a measurement cannot go quiet
# ---------------------- The keys `gen_assets.import_generated_op` sends across the bridge, declared
# HERE rather than as a literal at the call site, so the list and the vocabulary that judges it live
# together. That is the whole mechanism: `MESH_GATED` names the keys some function above can turn
# into a sentence and `MESH_INFORMATIONAL` names the keys nothing can fail on and why,
# `tools/tests/test_gen_receipt.py` fails when a receipt key is in neither, and the four defects at
# the top of this module were all exactly that -- a key that existed, carried a real number, and had
# no reader.
#
# Adding a measurement is therefore a two-line decision instead of an omission: give it a warning,
# or say out loud that it is context. Neither is hard; leaving it out used to be easier than both.
MESH_GATED = {
    "opacity": "leaf_opacity_warning",
    "faces": "open_surface_warning",             # the denominator of both openness fractions
    "low_openness": "open_surface_warning",      # pit / see-through split, the primary reading
    "low_boundary_edges": "open_surface_warning",  # the fallback when the split is absent
    "low_welded_verts": "open_surface_warning",  # decides whether the fallback count means holes
    "simplify_source": "open_surface_warning",   # the other half of that decision
    "pinholes_closed": "open_surface_warning",   # named in the sentence, so the fix is checkable
    "metalness": "metalness_warning",            # and the cause clause in bake_fidelity_warning
    "bake_fidelity": "bake_fidelity_warning",
    "map_stats": "empty_map_warning",
}

MESH_INFORMATIONAL = {
    "name": "the asset's name; the reply is about it rather than gated on it",
    "file": "where it landed, which an agent needs to do anything next",
    "object": "the object created in the scene, for the same reason",
    "collection": "which scatter pool it joined",
    "kind": "the scatter kind, which selected the gates rather than being gated",
    "pack_dir": "which pack it was written into (the agent-surface gate)",
    "lod_faces": "the LOD ladder; the budget it was asked for is `faces`, which IS gated",
    "height_m": "the height it was asked for, never in doubt and never a defect",
    "uv_overlap": "reported because a reader wants it; no bar exists that is not arbitrary",
    "origin_above_base": "the manifest origin rule, enforced at build rather than warned about",
    "master_type": "which BobShader master it wears, a routing fact",
    "maps": "the map files written; their CONTENT is gated through `map_stats`",
    "seconds": "per-stage wall clock, for cost, not correctness",
    "warnings": "the output of every gate here, so gating it on itself is meaningless",
    "source_faces": "the generated mesh before Bob touched it, the baseline the repair is read "
                    "against",
    "source_boundary_edges": "the same baseline for openness; what SHIPS is `low_boundary_edges`, "
                             "which is gated",
    "welded_verts": "how many the weld merged on the dense mesh, context for the figure above",
    "uv_source": "whose charts shipped, a routing fact like `master_type`",
    "textured_faces": "the textured mesh's own count, context for a route comparison",
    "bake_rescale": "the normalise-and-rescale round trip's factor, provenance",
}

# The same declaration for a TEXTURE receipt, which crosses a different bridge (`mcp_agent.server`'s
# texture, bark and atlas tools) and is judged by the two functions the mesh receipt never reaches.
# A value may name ONE reader or several. `cells` needs two because its measurements fail in ways that
# are fixed differently and can happen independently: a cell can be blank and perfectly "oriented",
# and a cell can be full and upside down. One sentence covering both would name the wrong remedy half
# the time.
TEXTURE_GATED = {
    "flatness": "flatness_warning",
    "cells": ("orientation_warning", "blank_cell_warning"),
    "grain": "grain_warning",
}

TEXTURE_INFORMATIONAL = {
    "name": "the set's folder name, which is how it is picked up again",
    "dir": "where it landed",
    "maps": "the map files written",
    "seam": "the wrap measurement; `seam_report` has no bar because a set is used tiled or not",
    "atlas": "the grid the set carries, read by `assets.atlas_grid()` rather than judged",
    "cell_distinctness": "how different the cells are; a stuck index is visible, not measurable",
    "clear_fraction": "how much of the sheet is cut away, context for `cells`",
    "opaque_fraction": "the same, from the other side",
    "route": "which atlas route ran, a routing fact",
    "seed": "provenance: what makes this set reproducible, not a property to judge",
    "size": "provenance: the resolution asked for, never in doubt",
    "cols": "the atlas grid asked for; whether the CELLS are usable is `cells`, which is gated",
    "rows": "the same, on the other axis",
    "workflow": "which shipped graph ran, a routing fact (the provenance rule)",
    "prompt": "the full prompt including the clauses Bob appended (the provenance rule)",
    "artist_prompt": "what the artist actually typed, kept apart from the appended clauses",
    "prompt_id": "the ComfyUI job id, so a generation can be traced on the server",
    "prompt_ids": "the same for a multi-frame atlas, one per cell",
    "checkpoint": "which model produced it, which decides what its licence is",
    "derived_from": "which upstream template the graph came from (the provenance rule)",
    "reference": "the reference image, when one was used; its LIGHT is judged by `flatness`",
    "seconds": "per-stage wall clock, for cost, not correctness",
    "warnings": "the output of the two gates above",
    "pack_dir": "which pack it was written into",
}

# The receipt `gen_assets.import_generated_op` sends, in the order it reads best.
# `import_generated_op` imports this rather than repeating it, so the bridge and the coverage check
# cannot drift apart.
MESH_RECEIPT_KEYS = (
    "name", "faces", "lod_faces", "height_m", "uv_overlap", "origin_above_base",
    "master_type", "opacity", "maps", "file", "warnings", "seconds",
    "source_faces", "source_boundary_edges", "low_boundary_edges", "low_openness",
    "pinholes_closed", "simplify_source", "uv_source", "welded_verts", "low_welded_verts",
    "textured_faces", "bake_rescale", "bake_fidelity", "metalness", "map_stats",
)


def unreviewed(keys, *, texture=False):
    """The keys in `keys` that are neither gated nor declared informational, sorted.

    Empty is the contract. A non-empty return is a measurement that would reach a receipt with no
    reader and no decision behind it, which is the exact shape of the four defects this module
    exists for -- so the test that calls this fails rather than reporting.
    """
    gated = TEXTURE_GATED if texture else MESH_GATED
    known = TEXTURE_INFORMATIONAL if texture else MESH_INFORMATIONAL
    return sorted(set(keys) - set(gated) - set(known))
