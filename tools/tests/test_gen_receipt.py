"""The receipt-warning vocabulary: the sentences a generated asset's measurements turn into.

Split out of `test_comfy.py` with the code, and it carries one test the old file could not have had.
`test_no_measurement_can_reach_a_receipt_without_a_reader` is the mechanism, not a case: the four
defects `core/gen_receipt.py` exists for were all one shape -- a key that was computed, crossed the
bridge, and had nothing that could ever read it -- and each was found in a render rather than by a
check. This asserts the shape instead of the four instances, so a fifth cannot be added silently.
"""

import importlib
import pathlib
import sys

import numpy as np
import pytest

CORE = (pathlib.Path(__file__).resolve().parents[2] / "blender" / "extensions"
        / "bob_blender_tools" / "core")


@pytest.fixture(scope="module")
def mods():
    sys.path.insert(0, str(CORE))
    return importlib.import_module("gen_receipt"), importlib.import_module("comfy_maps")


@pytest.fixture(scope="module")
def comfy():
    """`core.comfy` itself, for the two facts the vocabulary reads from it rather than owns:
    `FOLIAGE_KINDS` (which kinds keep their holes, a geometry-processing decision) and the prompt
    suffixes. The split is deliberate -- see `LEAFY_KINDS`, which is the same question asked about
    the finished LOOK and deliberately disagrees."""
    sys.path.insert(0, str(CORE))
    return importlib.import_module("comfy")


def _fan(size=128, strands=5, stem=True, lean=0.16):
    """A spray: several separate strands out of one solid stub. The shape both cues read, and what a
    "sprig on one short twig" prompt returns.

    Longer than wide and splaying gently, because that is what the generated sprigs measured --
    anisotropy 2.28 to 4.49 on the conifer atlas. A spray that splays as wide as it is tall has no
    principal axis at all, which is a different test case (`_pressed`).
    """
    sprite = np.zeros((size, size, 4), np.uint8)
    mid = size // 2
    base, top = int(size * 0.90), int(size * 0.74)
    sprite[top:base, mid - 2:mid + 3, :3] = (110, 70, 40) if stem else (40, 120, 40)
    sprite[top:base, mid - 2:mid + 3, 3] = 255
    for i in range(strands):
        drift = (i - (strands - 1) / 2.0) * lean
        for step in range(int(size * 0.68)):
            yy, xx = top - step, int(mid + drift * step)
            if 0 <= yy < size and 1 <= xx < size - 1:
                sprite[yy, xx - 1:xx + 2, :3] = (40, 120, 40)
                sprite[yy, xx - 1:xx + 2, 3] = 255
    return sprite


def _pressed(size=96):
    """A round, all-green, flat specimen: no stem to find and no long axis either. What "pressed
    flat like a herbarium specimen" returned, and the shape with no orientation cue at all."""
    sprite = np.zeros((size, size, 4), np.uint8)
    yy, xx = np.mgrid[0:size, 0:size]
    disc = ((yy - size / 2) ** 2 + (xx - size / 2) ** 2) < (size * 0.34) ** 2
    sprite[disc, :3] = (40, 120, 40)
    sprite[disc, 3] = 255
    return sprite

def _spindle(size=128):
    """A solid spindle: anisotropic, tapered at both ends, and ONE strand the whole way.

    The conifer cell that shipped upside down, in a shape. Its end ratio and anisotropy both look
    healthy, so the two width bars pass it and it is still 180 degrees out -- a fir sprig tapers at
    both ends, so "the narrow end is the cut stub" picks whichever end the tip happened to be. Only
    the absence of a fan says so.
    """
    sprite = np.zeros((size, size, 4), np.uint8)
    lo, hi = int(size * 0.12), int(size * 0.88)
    for row in range(lo, hi):
        t = (row - lo) / float(hi - lo)
        # Fatter toward the top than the bottom, so there IS a narrow end to be confident about.
        half = max(1, int(size * 0.085 * np.sin(np.pi * t) * (1.4 - 0.8 * t)))
        sprite[row, size // 2 - half:size // 2 + half + 1, :3] = (40, 120, 40)
        sprite[row, size // 2 - half:size // 2 + half + 1, 3] = 255
    return sprite


def test_no_measurement_can_reach_a_receipt_without_a_reader(mods):
    """The mechanism. Every key a receipt carries is either GATED -- some function in the module can
    turn it into a sentence -- or declared INFORMATIONAL with a reason. A key in neither is a
    measurement with no reader, which is what shipped five meshes carrying 48 to 229 boundary edges
    with `warnings: []`, a 2048 square of pure black beside a 0.9996 bake correlation, six foliage
    assets that were all solid geometry, and three atlases with the petiole pointing sideways.

    The assertion is deliberately on the DECLARATION rather than on behaviour, because the failure
    it catches is an omission: the four defects were not wrong answers, they were absent
    questions. A reason string is enough to pass, which is the point -- writing one is a decision, and leaving
    the key out entirely was never a decision at all.
    """
    receipt, _ = mods
    assert receipt.unreviewed(receipt.MESH_RECEIPT_KEYS) == [], \
        "a mesh receipt key with no warning and no informational reason"

    # Both halves of the declaration have to be real. A key cannot be in both -- that would be a
    # gate somebody decided to stop trusting without deleting -- and every gated key has to name a
    # function that exists, or the declaration is a comment rather than a check.
    for gated, known, label in ((receipt.MESH_GATED, receipt.MESH_INFORMATIONAL, "mesh"),
                                (receipt.TEXTURE_GATED, receipt.TEXTURE_INFORMATIONAL, "texture")):
        assert not set(gated) & set(known), f"{label}: a key cannot be both gated and informational"
        for key, fn in gated.items():
            assert callable(getattr(receipt, fn, None)), \
                f"{label}: {key} names {fn}, which is not a function in this module"
        for key, why in known.items():
            assert isinstance(why, str) and len(why) > 10, \
                f"{label}: {key} is declared informational with no reason worth reading"


def test_the_bridge_sends_exactly_what_is_declared(mods):
    """`gen_assets.import_generated_op` builds its reply from `MESH_RECEIPT_KEYS` rather than from a
    literal, so the list above and the thing an agent actually receives cannot drift apart. Read out
    of the source because `gen_assets` imports bpy and this suite runs in the plain venv."""
    receipt, _ = mods
    source = (CORE / "gen_assets.py").read_text()
    assert "gen_receipt.MESH_RECEIPT_KEYS" in source, \
        "the bridge builds its reply from the declared list, not from a literal beside it"
    assert len(set(receipt.MESH_RECEIPT_KEYS)) == len(receipt.MESH_RECEIPT_KEYS), \
        "a duplicate key in the receipt list"


def test_a_map_that_shipped_with_no_picture_in_it_says_so(mods):
    """The gate hole the block-out structure fell through, and the same shape as the four the
    first round found: a measurement that reaches no caller. `map_fidelity` returns None when
    either image has no variation, and `bake_fidelity_warning(None)` returns nothing, so a 2048
    square of pure black arrived at the receipt as `warnings: []` beside 13,302 faces, uv_overlap
    0.0, metalness 0.0 and a 0.9996 correlation.

    Every figure is measured, off the files in the generated pack."""
    receipt, _ = mods

    def stats(mean, std):
        return {"basecolor": {"mean": mean, "std": std}}

    # The five honest maps in the pack, over two orders of magnitude of subject.
    for mean, std in ((111.60, 37.49), (83.63, 51.35), (67.59, 39.97), (60.29, 57.51),
                      (54.09, 32.90)):
        assert not receipt.empty_map_warning("structure", stats(mean, std)), \
            f"{mean}/{std} is a picture"
    # And the two the block-out route produced.
    black = receipt.empty_map_warning("structure", stats(0.00, 0.00))
    assert black and "entirely black" in black[0]
    assert "texture pass" in black[0], "the sentence names the stage to go and look at"
    assert receipt.empty_map_warning("structure", stats(0.17, 4.13))
    # A flat map is a failure whatever tone it is flat at: no generated surface is one colour.
    grey = receipt.empty_map_warning("rocks", stats(128.0, 0.4))
    assert grey and "one flat tone" in grey[0]
    assert receipt.empty_map_warning("rocks", None) == []
    assert receipt.empty_map_warning("rocks", {}) == []


def test_orientation_warning_names_the_cells_whose_up_was_a_guess(mods):
    """The receipt half. `leaf_atlas` folds the diagnosis into each cell and this carries it to the
    caller -- the fourth of these warnings, and the first for a measurement that had to be invented
    rather than merely connected."""
    receipt, maps = mods
    orientations = []
    sprites = [_fan(), _pressed(), _fan(strands=7), _spindle()]
    _base, opacity = maps.atlas_compose(sprites, 2, 2, 256, orientations=orientations)
    cells = maps.atlas_cells(opacity, 2, 2)
    for row, found in zip(cells, orientations):
        row["orient"] = {k: v for k, v in found.items() if k != "cell"}

    assert all(c["base_taper"] < 1.0 for c in cells), \
        "every cell passes the taper check, which is why a second measurement was needed"
    warned = receipt.orientation_warning(cells)
    assert len(warned) == 1
    assert "2 of 4 sprites" in warned[0], warned[0]
    assert "cell 1" in warned[0] and "cell 3" in warned[0]
    assert "ON ONE SHORT TWIG" in warned[0], "the prompt guidance is the fix, so it is in the text"
    # An atlas whose sprites all have a twig says nothing, and neither does a set with no diagnosis
    # in it (the grid route, or a set written before this existed).
    assert receipt.orientation_warning([c for c in cells if c["orient"]["resolved"]]) == []
    assert receipt.orientation_warning(maps.atlas_cells(opacity, 2, 2)) == []
    assert receipt.orientation_warning([]) == []


def test_a_colour_bake_that_returned_something_else_says_so(mods):
    """The receipt warning for the defect that cannot fail loudly. A misaligned or jittered colour
    transfer still writes a plausible texture, so nothing downstream notices and the artist finds it
    in a hero render.

    The pairs are three generated assets re-finished through the same code either side of the
    coincident-bake fix, so the bar is measured on both sides rather than picked."""
    receipt, _ = mods

    def fid(corr, diff):
        return {"correlation": corr, "mean_abs_diff": diff, "coverage": 0.57}

    for corr, diff in ((0.9015, 6.48), (0.9524, 5.96), (0.9793, 3.21)):  # cage onto itself
        assert receipt.bake_fidelity_warning(fid(corr, diff)), f"{corr} should warn"
    for corr, diff in ((0.9980, 2.28), (0.9985, 2.57), (0.9995, 1.12)):  # self-baked
        assert not receipt.bake_fidelity_warning(fid(corr, diff)), f"{corr} should not"
    # Either half of the bar is enough on its own: a map can track the source's shape and still be
    # shifted in level, or match in level and have its detail moved about.
    assert receipt.bake_fidelity_warning(fid(0.999, receipt.BAKE_DIFF_MAX + 0.1))
    assert receipt.bake_fidelity_warning(fid(receipt.BAKE_FIDELITY_MIN - 0.01, 1.0))
    # And nothing measured is not a failure: a geometry-only asset has no colour to compare.
    assert receipt.bake_fidelity_warning(None) == []
    assert receipt.bake_fidelity_warning({"coverage": 0.4}) == []


def test_leaf_opacity_warning_fires_on_the_kinds_whose_look_is_leaves(mods, comfy):
    """LEAFY_KINDS deliberately differs from FOLIAGE_KINDS: that one is about keeping holes open
    through remesh and pinhole fill (plants, grass); this one is about the finished LOOK, and a tree
    is in it because the crown is the reason it was generated."""
    receipt, _ = mods
    assert set(receipt.LEAFY_KINDS) == {"trees", "plants", "grass"}
    assert set(comfy.FOLIAGE_KINDS) < set(receipt.LEAFY_KINDS)
    # The two verdicts measured on generated foliage, both of them.
    for verdict in ("opaque", "implausible", None):
        warns = receipt.leaf_opacity_warning("trees", {"verdict": verdict})
        assert len(warns) == 1 and "reads as solid geometry" in warns[0]
        assert str(verdict or "none") in warns[0], "the receipt names WHICH case this was"
    assert receipt.leaf_opacity_warning("trees", {"verdict": "cutout"}) == []
    # A rock has no crown to be wrong about, whatever its alpha says.
    assert receipt.leaf_opacity_warning("rocks", {"verdict": "opaque"}) == []
    assert receipt.leaf_opacity_warning("grass", {})
    assert receipt.leaf_opacity_warning("plants", None)


def test_a_lit_albedo_says_so(mods, comfy):
    """The third receipt warning, and the one with no measurement at all before that gate.
    The prompts have always asked for flat light -- `PROMPT_SUFFIX` carries "flat even lighting" --
    so the intent was right and the enforcement absent."""
    receipt, _ = mods
    assert "flat even lighting" in comfy.PROMPT_SUFFIX, "the intent, which was never the problem"

    def flat(value, **extra):
        return dict({"low_freq_variation": value}, **extra)

    # The gate's own ten sets, either side of the bar.
    for value in (0.0247, 0.0355, 0.0452, 0.0492, 0.0509, 0.0667, 0.0740, 0.0742):
        assert not receipt.flatness_warning(flat(value)), f"{value} reads as flat"
    for value in (0.0965, 0.0989):
        assert receipt.flatness_warning(flat(value)), f"{value} is lit"
    # The advice changes once delighting has been tried and did not help, because at that point the
    # lighting is not a low-frequency ramp and correcting harder will not fix it.
    assert "reroll" in receipt.flatness_warning(flat(0.2, delighted=True))[0]
    assert "delight=True" in receipt.flatness_warning(flat(0.2))[0]

    # A leaf card is the case that matters most, and it is measured in STOPS inside the cutout: an
    # atlas can have a flat sheet and still have a key ramping across a sprite.
    leafy = flat(0.02, in_mask_ramp_stops=0.9, in_mask_detail_stops=0.5)
    assert not receipt.flatness_warning(leafy), "the stops half only applies to a leafy caller"
    assert receipt.flatness_warning(leafy, leafy=True)
    assert "both sides" in receipt.flatness_warning(leafy, leafy=True)[0]

    # And the bar is on the RAMP alone. `leaf_conifer` is the case: 1.143 stops inside the mask, of
    # which 0.436 is light and the rest is one needle shadowing the next, which a flat card cannot
    # carry as geometry and so belongs in the albedo. The old total-variation bar failed it, five
    # atlases were spent chasing the number, and the prompt rewrite that finally moved it is what
    # broke the sprite orientation.
    conifer = flat(0.0452, in_mask_stops=1.143, in_mask_ramp_stops=0.436,
                   in_mask_detail_stops=1.154, delighted=True)
    assert not receipt.flatness_warning(conifer, leafy=True), \
        "per-needle self-shadowing is not baked light"
    lit_spray = dict(conifer, in_mask_ramp_stops=0.72)   # the same sprites plus a one-stop key
    assert receipt.flatness_warning(lit_spray, leafy=True)
    assert "1.154 stops of per-needle detail" in \
            receipt.flatness_warning(lit_spray, leafy=True)[0], \
        "the figure that is NOT gated is still reported, so the two are never confused again"
    assert receipt.flatness_warning(None) == []


def test_a_solid_kind_shipping_open_says_so(mods, comfy):
    """The companion to `leaf_opacity_warning`, and it exists because the boundary-edge count was
    computed since the asset gate and read by nobody: five generated meshes shipped with
    48 to 229 boundary edges and a `warnings: []` receipt, and the stump's holes were found in a
    render.

    The thresholds are the gate's own measurements, after the weld and the pinhole fill."""
    receipt, _ = mods

    def report(edges, faces, **extra):
        return dict({"low_boundary_edges": edges, "faces": faces,
                     "simplify_source": "trellis2", "low_welded_verts": 100}, **extra)

    # The two the artist could see through, and the three that read as closed.
    assert receipt.open_surface_warning("stump", report(73, 3840)), "1.9%"
    assert receipt.open_surface_warning("slab", report(72, 2865)), "2.5%"
    assert not receipt.open_surface_warning("log", report(17, 3911)), "0.4%"
    assert not receipt.open_surface_warning("structure", report(11, 7502)), "0.15%"
    assert not receipt.open_surface_warning("boulder", report(9, 3832)), "0.24%"
    # A count alone is not the claim: the same 30 edges is a sieve on a small rock and nothing on a
    # building.
    assert receipt.open_surface_warning("rocks", report(30, 500))
    assert not receipt.open_surface_warning("rocks", report(30, 40000))
    # The floor, so a tiny mesh is not warned about two edges.
    assert not receipt.open_surface_warning("rocks", report(receipt.OPEN_SURFACE_FLOOR, 10))
    # Foliage is exempt: a leaf blade IS an open surface and the pinhole fill is off for it, so
    # warning here would teach an artist to ignore the warning that means something.
    for kind in comfy.FOLIAGE_KINDS:
        assert not receipt.open_surface_warning(kind, report(5000, 4000))
    # And the figure is only honest on a welded mesh, so an unwelded one stays quiet rather than
    # reporting its UV seams as holes (stump: 3,646 one-face edges unwelded against a real 229).
    assert not receipt.open_surface_warning("stump", {"low_boundary_edges": 3646, "faces": 3832,
                                                    "simplify_source": "trellis2"})
    assert receipt.open_surface_warning("stump", {"low_boundary_edges": 300, "faces": 3832,
                                                "simplify_source": "decimate"}), \
        "Bob's own retopo welds, so its count is honest with no marker"
    assert not receipt.open_surface_warning("stump", report(0, 3832)), "closed says nothing"


def test_a_pitted_rock_is_not_a_hole_and_one_big_hole_is_not_a_fraction(mods, comfy):
    """The count above cannot say WHAT the openness is, and that got both of the gate's
    rock-and-structure verdicts wrong. `gen_assets.openness_report` casts a ray in through each
    opening: a pit's floor is a front face and still the outside of the solid, a hole's far wall is
    a BACKFACE, and a backface behind an opening is what a hole looks like in a render.

    Every number below is measured, on the meshes the gate shipped."""
    receipt, _ = mods

    def report(faces, edges, **openness):
        full = dict({"loops": 1, "pit_edges": 0, "seethrough_edges": 0, "open_edges": 0,
                     "seethrough_loops": 0, "largest_seethrough_m": 0.0,
                     "largest_seethrough_fraction": 0.0, "welded": True}, **openness)
        return {"faces": faces, "low_boundary_edges": edges, "low_openness": full,
                "simplify_source": "trellis2", "low_welded_verts": 100}

    # The small rock: 1.7% open, warned about twice, and 50 of its 60 boundary edges are the mouths
    # of real gas pockets. Its worst see-through opening is 4.2 cm on a 60 cm rock.
    rock = report(3493, 60, loops=28, pit_edges=50, seethrough_edges=10, seethrough_loops=6,
                  largest_seethrough_m=0.042, largest_seethrough_fraction=0.070)
    assert not receipt.open_surface_warning("groundrock", rock), \
        "vesicular stone is surface, not damage"
    assert receipt.open_surface_warning("groundrock",
                                      {k: v for k, v in rock.items() if k != "low_openness"}), \
        "and the old count is what cried wolf about it"

    # The structure: 0.21% open, clean under any edge fraction, and one see-through loop 1.59 m
    # across -- the black wedge under the left eave the artist saw in a render. Five edges of 8,839
    # faces.
    structure = report(8839, 19, loops=9, pit_edges=11, seethrough_edges=8, seethrough_loops=2,
                  largest_seethrough_m=1.5917, largest_seethrough_fraction=0.1822)
    warned = receipt.open_surface_warning("structure", structure)
    assert warned, "a 1.59 m hole is a hole whatever fraction of the faces it touches"
    assert "1.59 m" in warned[0] and "18%" in warned[0]
    assert "11 boundary edges are pit mouths" in warned[0], \
        "and the pits are named as not-the-problem, so the artist is not sent after them"
    assert not receipt.open_surface_warning(
        "structure", {k: v for k, v in structure.items() if k != "low_openness"}), \
        "which the old fraction missed entirely"

    # The sieve half still fires: hundreds of small holes is a large share of the faces and no
    # single big opening, which is how a pre-fill remesh arrives.
    assert receipt.open_surface_warning("stump", report(3840, 400, loops=200, pit_edges=40,
                                                      seethrough_edges=360, seethrough_loops=180,
                                                      largest_seethrough_m=0.03,
                                                      largest_seethrough_fraction=0.02))
    # The ones that read as closed stay quiet, and so does a mesh whose openness is all pits.
    assert not receipt.open_surface_warning("boulder", report(3901, 2, pit_edges=2))
    assert not receipt.open_surface_warning("log", report(3979, 4, loops=2, pit_edges=1,
                                                        seethrough_edges=3, seethrough_loops=1,
                                                        largest_seethrough_m=0.0295,
                                                        largest_seethrough_fraction=0.0137))
    # Foliage is still exempt, and a block with no loops falls back to the count.
    for kind in comfy.FOLIAGE_KINDS:
        assert not receipt.open_surface_warning(kind, report(4000, 5000, seethrough_edges=5000))
    assert receipt.open_surface_warning("stump", {"faces": 3840, "low_boundary_edges": 400,
                                                "low_openness": {"loops": 0, "welded": True},
                                                "simplify_source": "decimate"})
    # An UNWELDED block is not a measurement of holes at all -- every UV seam reads as a boundary --
    # so it falls back to the count, which has the same guard. Measured on the control-conditioned
    # structure: 1,187 boundary edges in 14 "see-through" loops unwelded, 24 in 4 after the weld.
    assert not receipt.open_surface_warning("structure", {
        "faces": 13846, "low_boundary_edges": 1187, "simplify_source": "trellis2",
        "low_openness": {"loops": 14, "seethrough_edges": 1187, "seethrough_loops": 14,
                         "pit_edges": 0, "open_edges": 0, "largest_seethrough_m": 1.47,
                         "largest_seethrough_fraction": 0.74, "welded": False}})


def test_a_generated_material_claiming_to_be_metal_says_so(mods):
    """Nothing in the pipeline looked at what image-to-3D decided about metalness, and the first
    generated structure came back fully metal because silvered siding reads as metal. Two failures
    follow and the receipt named neither: the asset renders as a mirror, and a diffuse bake of a
    metal surface is black so `bake_fidelity` fires with the wrong explanation."""
    receipt, _ = mods
    # Measured on the staged GLBs the gate kept. Every one declares metallicFactor 1.0 with a
    # metallicRoughness texture wired, so the map's own mean is the claim.
    structure = {"material": "Material_0", "factor": 1.0, "map": "Image_1", "map_mean": 0.8286,
                 "effective": 0.8286, "linked": True}
    warned = receipt.metalness_warning("structure", structure)
    assert warned and "0.8286" in warned[0]
    assert "dielectric" in warned[0] and "mirror" in warned[0]
    # The nine that are honest, spread over two orders of magnitude and all far under the bar.
    for value in (0.0191, 0.0027, 0.0012, 0.0011, 0.0007, 0.0004, 0.0003, 0.0002):
        assert not receipt.metalness_warning(
            "rocks", {"effective": value, "linked": True, "map_mean": value}), \
            f"{value} is not metal"
    assert receipt.metalness_warning("rocks", None) == []
    assert not receipt.metalness_warning(
        "rocks", {"effective": receipt.METALNESS_MAX, "linked": False})

    # A map that is wired and unreadable is UNKNOWN, not zero. This is the shape of the bug that hid
    # it: an image packed in a GLB reports `has_data` False until its pixels are touched, and the
    # first version of the reader turned that into a confident 0.0.
    unknown = receipt.metalness_warning("structure", {"effective": None, "linked": True,
                                                    "map": "Image_1"})
    assert unknown and "could not be read" in unknown[0]
    assert receipt.metalness_warning("structure", {"effective": None, "linked": False}) == []

    # And the map's mean is NOT the claim on its own: `metalness_report` folds in the factor the
    # glTF importer carries as a Math MULTIPLY. Every FINISHED asset in the pack declares
    # metallicFactor 0 with an ORM texture whose blue channel is full white, so reading the map
    # alone reported all five of them as fully metal.
    finished = {"material": "M_Stump", "factor": 0.0, "map": "Stump_roughness", "map_mean": 1.0,
                "effective": 0.0, "linked": True}
    assert receipt.metalness_warning("stump", finished) == [], \
        "a full-white ORM blue channel times a zero factor is not metal"

    # And the fidelity warning stops naming the transfer once the metal claim explains the figure.
    # The structure measured 0.9108 with an albedo mean of 14.9 against the source's 47.3.
    low = {"correlation": 0.9108, "mean_abs_diff": 12.0, "coverage": 0.7}
    alone = receipt.bake_fidelity_warning(low)[0]
    assert "resampled the surface" in alone
    assert "claims to be metal" in alone, "the other mechanism is named even with nothing measured"
    withmetal = receipt.bake_fidelity_warning(low, metalness=structure)[0]
    assert "read the metalness warning first" in withmetal
    assert "resampled" not in withmetal, \
        "the wrong cause is not offered once the right one is known"
    assert receipt.bake_fidelity_warning({"correlation": 0.9974, "mean_abs_diff": 2.3},
                                       metalness=structure) == []
