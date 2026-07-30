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
    from . import comfy, comfy_maps, gen_bars
except ImportError:  # `core` itself on sys.path (the venv / headless route), where there is no
    import comfy  # parent package -- the same fallback `comfy` itself uses
    import comfy_maps
    import gen_bars


def _bar(name):
    """One bar's value, off the registry. Named short because it reads as part of the assignment."""
    return gen_bars.value(name)


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


# Every bar below is `core/gen_bars.py`'s, read rather than restated. The number, the evidence
# behind it and the date it was derived live there, in one auditable table; what lives HERE is
# the sentence an artist reads when a measurement crosses one. Two concerns, and the registry
# exists because the numbers used to be scattered across three files with their justification in
# prose comments -- and a comment cannot fail a test.
OPEN_SURFACE_FRACTION = _bar("open_surface")


OPEN_SURFACE_FLOOR = gen_bars.BARS["open_surface"].floor


SEETHROUGH_FRACTION = _bar("seethrough")


SEETHROUGH_OPENING_FRACTION = _bar("seethrough_opening")


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


CELL_OPAQUE_MIN = _bar("cell_opaque")


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


GRAIN_OFF_VERTICAL_MAX = _bar("grain_off_vertical")


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


CONTROL_HIDDEN_MAX = _bar("control_hidden")


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


PAINT_COVERAGE_MIN = _bar("paint_coverage")


def paint_coverage_warning(report):
    """A stylised paint that left its charts to the hole fill, as a list of zero or one sentence.

    Reads `core.gen_paint.paint_maps`'s report. The hole fill is not a defect in itself -- a closed
    shape always has texels no camera reached -- but it spreads neighbouring colour into them, so a
    low figure means an asset whose texture LOOKS painted and is invention over whatever share this
    names. That is the same failure the whole route exists to avoid on the other axis.
    """
    painted = (report or {}).get("painted")
    if painted is None or painted >= PAINT_COVERAGE_MIN:
        return []
    views = (report or {}).get("views")
    unpainted = (report or {}).get("unpainted")
    return [f"only {painted * 100:.1f}% of this asset's chart texels were painted from a view that "
            f"could see them, against a {PAINT_COVERAGE_MIN * 100:.0f}% bar"
            + (f" ({unpainted} texels left to the hole fill" if unpainted is not None else "")
            + (f" from {views} views)" if views is not None else ")")
            + ". The rest is neighbouring colour spread inwards, which reads as texture and carries "
              "no information. Raise the ring count, or add an elevation: a 20-degree ring alone "
              "left 28% of a closed boulder unseen, which is what the two extra elevations in "
              "`gen_views.turntable_views` are for"]


VIEW_OVERLAP_MIN = _bar("view_overlap")


def view_overlap_warning(report):
    """A turntable too sparse to measure its own seam, as a list of zero or one sentence.

    The pair MADs are the seam measurement, and a MAD over a handful of shared texels is noise
    wearing a number. This says so rather than letting a clean-looking seam figure stand on nothing.
    """
    pairs = (report or {}).get("pairs") or []
    thin = [pair for pair in pairs if pair.get("texels", 0) < VIEW_OVERLAP_MIN]
    if not thin:
        return []
    smallest = min(pair.get("texels", 0) for pair in thin)
    return [f"{len(thin)} of {len(pairs)} adjacent view pairs share fewer than "
            f"{int(VIEW_OVERLAP_MIN)} texels (smallest {smallest}), so the seam figures for those "
            f"pairs are statistics over too little surface to mean anything. The paint itself may "
            f"be fine -- what is unmeasured is whether neighbouring views agree. Raise the ring "
            f"count so consecutive views overlap"]


BAKE_FIDELITY_MIN = _bar("bake_fidelity")


BAKE_DIFF_MAX = _bar("bake_diff")


METALNESS_MAX = _bar("metalness")


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


MAP_SPREAD_MIN = _bar("map_spread")


MAP_DARK_MAX = _bar("map_dark")


def empty_map_warning(kind, map_stats):
    """A basecolor that shipped with no picture in it, as a list of zero or one sentence.

    Reads `gen_assets.map_stats`, measured on the file that was written rather than on anything the
    bake reported about itself -- or `gen_paint.chart_stats` for the paint route, which measures the
    same figures inside the charts because a projected atlas is black everywhere else. The check the
    block-out route needed and no existing one could give: every other figure in that receipt was
    healthy -- 13,302 faces, uv_overlap 0.0, no boundary edges, metalness 0.0, `bake_fidelity`
    correlation 0.9996 -- and the asset was a black box.

    The REMEDY differs by route and the sentence has to say which, because the same flat map means
    two different things: on a bake it is a texture pass that returned nothing, and on a paint it is
    usually Strength -- at the shipped paint denoise the render dominates by design, so an untextured
    mesh under flat light comes back the grey it went in as. Measured on the same ico-sphere and seed:
    spread 1.31 at denoise 0.40, spread 14.83 at 0.75.
    """
    stats = (map_stats or {}).get("basecolor")
    if not stats:
        return []
    spread, mean = stats.get("std"), stats.get("mean")
    if spread is None or spread >= MAP_SPREAD_MIN:
        return []
    what = ("is entirely black" if mean is not None and mean <= MAP_DARK_MAX
            else f"is one flat tone (mean {mean})")
    if kind == "painted":
        remedy = ("Raise Strength (the paint denoise): the shipped default keeps the real render "
                  "dominant, which is right for a mesh whose render already carries an albedo and "
                  "leaves an untextured one exactly as it was. Measured on one ico-sphere, same "
                  "seed and prompt: spread 1.31 at 0.40 against 14.83 at 0.75")
    else:
        remedy = ("Check the texture pass: a graph that painted nothing still returns a file, and "
                  "the bake carries the emptiness through faithfully")
    return [f"the basecolor that shipped carries no picture: it {what}, spread {spread} against a "
            f"{MAP_SPREAD_MIN} bar, where every honest map in this pack measures 33 to 58. Nothing "
            f"upstream reports this -- `bake_fidelity` compares the bake to its source and returns "
            f"nothing at all when either side is flat, so an empty bake reaches the receipt as an "
            f"empty warning list. {remedy}"]


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


FLATNESS_MAX = _bar("flatness")


LEAF_RAMP_STOPS_MAX = _bar("leaf_ramp_stops")


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


# The same declaration for a PAINTED asset, the third bridge: `core.gen_paint.paint_stylised`, the
# stylised texture route. Its own vocabulary rather than the mesh one because it judges a different
# object -- the mesh came from somewhere else and is not touched, and what this route produces is a
# projection into charts the mesh already had.
PAINT_GATED = {
    "painted": "paint_coverage_warning",
    "pairs": "view_overlap_warning",
    # The same reader the mesh receipt uses, on in-chart figures rather than whole-file ones
    # (`gen_paint.chart_stats` says why). It is the check that catches a paint which RAN, reported
    # 99.9% coverage and changed nothing: at the shipped paint denoise the render dominates by
    # design, so an untextured mesh under flat light comes back the grey it went in as.
    "map_stats": "empty_map_warning",
}

PAINT_INFORMATIONAL = {
    "name": "the asset's name; the reply is about it rather than gated on it",
    "object": "the object painted, for an agent's next call",
    "material": "the material wired onto it, a routing fact like `master_type`",
    "maps": "the map files written; their COVERAGE is gated through `painted`",
    "route": "which texture route ran, a routing fact",
    "prompt": "the style prompt, provenance (the provenance rule)",
    "seed": "provenance: what makes this paint reproducible",
    "lora": "which style LoRA was applied, or empty; provenance rather than a property to judge",
    "coverage": "how much of the atlas is chart at all, a property of the UV layout this route "
                "inherited rather than of the paint; the paint's own figure is `painted`",
    "unpainted": "the texel count behind `painted`, named in that sentence so the fix is checkable",
    "views": "how many views were rendered, context for both gates",
    "ring": "how many of them were the measurable ring, the denominator `pairs` is read against",
    "size": "the texture resolution asked for, never in doubt",
    "drift": "the front-against-180-degrees disagreement. NOT gated, and that is uncomfortable: it "
             "is the cross-view consistency limit the route is known to have (30.1 of 255 measured) "
             "and the defect MV-Adapter would fix. There is no measured pass line, and inventing "
             "one would be the magic number this vocabulary exists to stop. Named in docs/ROADMAP.md",
    "seconds": "per-stage wall clock, for cost, not correctness",
    "pack_dir": "which pack the maps were written into (the agent-surface gate)",
    "warnings": "the output of the gates above",
}

PAINT_RECEIPT_KEYS = (
    "name", "object", "material", "maps", "route", "prompt", "seed", "lora",
    "coverage", "painted", "unpainted", "views", "ring", "size", "pairs", "drift",
    "map_stats", "pack_dir", "warnings", "seconds",
)

# The three bridges, so `unreviewed` takes a NAME rather than a boolean and a fourth is one entry
# rather than a second flag. Two booleans would already have been unreadable at the call site.
VOCABULARIES = {
    "mesh": (MESH_GATED, MESH_INFORMATIONAL),
    "texture": (TEXTURE_GATED, TEXTURE_INFORMATIONAL),
    "paint": (PAINT_GATED, PAINT_INFORMATIONAL),
}


def unreviewed(keys, kind="mesh"):
    """The keys in `keys` that are neither gated nor declared informational, sorted.

    Empty is the contract. A non-empty return is a measurement that would reach a receipt with no
    reader and no decision behind it, which is the exact shape of the four defects this module
    exists for -- so the test that calls this fails rather than reporting.
    """
    gated, known = VOCABULARIES[kind]
    return sorted(set(keys) - set(gated) - set(known))
