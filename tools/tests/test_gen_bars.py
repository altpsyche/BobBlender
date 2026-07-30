"""The bar registry's teeth: `core/gen_bars.py` is only worth having if something fails on it.

`test_gen_receipt.py` asks "does this number have a READER". These ask "does this number have
EVIDENCE" -- the same shape of question one level down, and the reason the registry exists at all: a
bar's justification used to be a prose comment beside it, and a comment cannot fail.

The one that matters is `test_no_thin_bar_gates_a_shipped_asset_silently`. It does not forbid a thin
bar, deliberately: three of them catch defects that were found in renders, and deleting a bar that
works because its sample count is small would trade a real gate for a tidy table. What it forbids is a
thin bar that does not SAY it is thin, because that is the state `leaf_ramp_stops` was in for weeks
while it rejected eight of every ten leaf atlases.
"""

import importlib
import pathlib
import re
import sys

import pytest

REPO = pathlib.Path(__file__).resolve().parents[2]
CORE = REPO / "blender" / "extensions" / "bob_blender_tools" / "core"


@pytest.fixture(scope="module")
def bars():
    sys.path.insert(0, str(CORE))
    return importlib.import_module("gen_bars")


@pytest.fixture(scope="module")
def mods():
    sys.path.insert(0, str(CORE))
    return importlib.import_module("gen_receipt"), importlib.import_module("comfy_maps")


# -- The mechanism ----------------------------------------------------------------------------
def test_no_thin_bar_gates_a_shipped_asset_silently(bars):
    """A bar with fewer than five real samples that judges an ASSET has to declare itself.

    The failure mode in full, because it is the reason this file exists: `leaf_ramp_stops` was
    calibrated on three atlases generated with wording that was itself defective, rejected 8 of 10
    ordinary atlases, took the whole atlas class to a 0% pass rate on the class-rate harness, and read
    as a settled threshold the entire time. Nothing could have failed, because the only thing that
    knew the evidence was thin was a comment.
    """
    assert bars.undeclared() == [], (
        "these bars gate shipped assets on fewer than "
        f"{bars.MIN_REAL_SAMPLES} real samples without declaring `provisional=True`: "
        f"{bars.undeclared()}")


def test_every_provisional_bar_says_what_would_settle_it(bars):
    """`provisional=True` with no note is an admission with no next step, which is how a known-thin
    bar sits untouched for a month. The note has to name what would settle it."""
    silent = sorted(k for k, b in bars.BARS.items() if b.provisional and len(b.note) < 40)
    assert silent == [], f"provisional with no useful note: {silent}"


def test_every_bar_names_a_reader(bars):
    """A bar nothing reads is not protecting anything. The counterpart of
    `test_gen_receipt.py`'s "no measurement reaches a receipt without a reader"."""
    unread = sorted(k for k, b in bars.BARS.items() if not b.reader.strip())
    assert unread == [], f"bars with no reader: {unread}"


def test_every_asset_bar_reader_is_a_real_function(bars, mods):
    """An asset bar's reader names a `module.function` that EXISTS. A renamed warning function used to
    leave the bar pointing at nothing, and the pointer is the only thing tying a number to the
    sentence an artist gets."""
    receipt, maps = mods
    modules = {"gen_receipt": receipt, "comfy_maps": maps}
    missing = []
    for name, bar in bars.asset_bars().items():
        # A reader may name more than one site (`control_hidden` is read by a receipt sentence AND by
        # the block-out gate); every dotted module.function in it has to resolve.
        for token in bar.reader.replace("+", " ").split():
            if token.count(".") != 1:
                continue
            mod, _, fn = token.partition(".")
            if mod in modules and not hasattr(modules[mod], fn):
                missing.append(f"{name} -> {token}")
    assert missing == [], f"asset bars pointing at functions that do not exist: {missing}"


# -- The registry is the one home of each number ----------------------------------------------
def test_the_modules_read_their_bars_from_the_registry(bars, mods):
    """Each rewired constant IS its registry entry, not a copy that happens to agree. The check that
    would have caught the 0.05 hidden-surface bar being declared in two files under two names."""
    receipt, maps = mods
    pairs = [
        (receipt, "OPEN_SURFACE_FRACTION", "open_surface"),
        (receipt, "SEETHROUGH_OPENING_FRACTION", "seethrough_opening"),
        (receipt, "CELL_OPAQUE_MIN", "cell_opaque"),
        (receipt, "GRAIN_OFF_VERTICAL_MAX", "grain_off_vertical"),
        (receipt, "CONTROL_HIDDEN_MAX", "control_hidden"),
        (receipt, "BAKE_FIDELITY_MIN", "bake_fidelity"),
        (receipt, "BAKE_DIFF_MAX", "bake_diff"),
        (receipt, "METALNESS_MAX", "metalness"),
        (receipt, "MAP_SPREAD_MIN", "map_spread"),
        (receipt, "MAP_DARK_MAX", "map_dark"),
        (receipt, "FLATNESS_MAX", "flatness"),
        (receipt, "LEAF_RAMP_STOPS_MAX", "leaf_ramp_stops"),
        (maps, "ATLAS_OPAQUE", "atlas_opaque"),
        (maps, "AXIS_ANISOTROPY_MIN", "axis_anisotropy"),
        (maps, "AXIS_TAPER_MAX", "axis_taper"),
        (maps, "AXIS_STRAND_CONTRAST_MIN", "axis_strand_contrast"),
        (maps, "AXIS_STRONG_TAPER_MAX", "axis_strong_taper"),
        (maps, "WOODY_EXCESS", "woody_excess"),
        (maps, "WOODY_SEPARATION", "woody_separation"),
    ]
    for module, const, key in pairs:
        assert getattr(module, const) == bars.BARS[key].value, f"{const} != BARS[{key!r}]"
    assert receipt.OPEN_SURFACE_FLOOR == bars.BARS["open_surface"].floor


def test_no_literal_bar_survives_in_the_modules_that_moved(bars, mods):
    """Every rewired constant reads the registry, so `gen_receipt` and `comfy_maps` hold no bar value
    of their own. Checked on the SOURCE, because a constant re-assigned to the same literal would pass
    the equality test above while being a second home for the number."""
    for filename in ("gen_receipt.py", "comfy_maps.py"):
        text = (CORE / filename).read_text()
        for line in text.splitlines():
            name, _, rhs = line.partition(" = ")
            if not name or not name.isupper() or not rhs:
                continue
            if any(k.upper() in name for k in ("BAR", "MIN_REAL")):
                continue
            # A bar constant is one the registry names; those must be lookups, never literals.
            if name in _BAR_CONSTANTS:
                assert "_bar(" in rhs or "gen_bars." in rhs, (
                    f"{filename}: {name} is a bar and holds a literal ({rhs.strip()})")


_BAR_CONSTANTS = {
    "OPEN_SURFACE_FRACTION", "OPEN_SURFACE_FLOOR", "SEETHROUGH_FRACTION",
    "SEETHROUGH_OPENING_FRACTION", "CELL_OPAQUE_MIN", "GRAIN_OFF_VERTICAL_MAX",
    "CONTROL_HIDDEN_MAX", "BAKE_FIDELITY_MIN", "BAKE_DIFF_MAX", "METALNESS_MAX",
    "MAP_SPREAD_MIN", "MAP_DARK_MAX", "FLATNESS_MAX", "LEAF_RAMP_STOPS_MAX",
    "ATLAS_OPAQUE", "AXIS_ANISOTROPY_MIN", "AXIS_TAPER_MAX", "AXIS_STRAND_CONTRAST_MIN",
    "AXIS_STRONG_TAPER_MAX", "WOODY_EXCESS", "WOODY_SEPARATION",
}


def test_every_bar_is_actually_read_by_a_call_site(bars):
    """Scan the SOURCE for `_bar("x")` / `gen_bars.value("x")` and require the two sets to match.

    Two failures in one check, and each is a different kind of rot:

    - a bar in the registry that no call site reads is a number that gates nothing while looking like
      it does, which is worse than a magic number because the table lends it authority;
    - a lookup naming a bar that does not exist is a `KeyError` at import time in a gate that may only
      run on a GPU box, so it would be found late and by the wrong person.

    The `reader` FIELD is prose for a human. This is the mechanical half, and it covers the code bars
    too -- their readers are gate scripts rather than functions, so nothing else can check them.
    """
    pattern = re.compile(r'(?:gen_bars\.value|_bar)\("([a-z_]+)"\)')
    scanned = list((REPO / "tools" / "scripts").glob("*.py")) + list(CORE.glob("*.py"))
    used = set()
    for path in scanned:
        used.update(pattern.findall(path.read_text(encoding="utf-8")))
    assert sorted(used - set(bars.BARS)) == [], "lookups naming no such bar"
    assert sorted(set(bars.BARS) - used) == [], "bars in the registry that nothing reads"


# -- The data model itself --------------------------------------------------------------------
def test_a_bar_is_immutable(bars):
    """Frozen on purpose: a bar is a calibration, and code that wanted to vary one per call would be
    asking for a different bar."""
    with pytest.raises(Exception):
        bars.BARS["metalness"].value = 0.5


@pytest.mark.parametrize("field", ["unit", "catches", "reader", "derived"])
def test_every_bar_fills_in_the_fields_that_make_it_auditable(bars, field):
    """An entry with a value and nothing else is the magic number it replaced."""
    blank = sorted(k for k, b in bars.BARS.items() if not str(getattr(b, field)).strip())
    assert blank == [], f"bars with no {field}: {blank}"


def test_judges_is_one_of_the_two_things_a_bar_can_judge(bars):
    """The split the whole vocabulary rests on: a gate asserts properties of the CODE, a receipt
    judges the ASSET. A third value would mean a bar nobody knows where to read."""
    assert {b.judges for b in bars.BARS.values()} <= {"asset", "code"}


def test_the_generated_table_covers_every_bar(bars):
    """The docs table is generated from `table_rows`, so a bar missing from it is a bar an artist
    cannot audit -- the exact failure the hand-written table had (it listed three of twenty-nine)."""
    rows = bars.table_rows()
    assert len(rows) == len(bars.BARS)
    assert [r[0] for r in rows] == sorted(bars.BARS)


def test_the_registry_reports_what_it_knows_is_weak(bars):
    """Not a bar's own correctness -- the registry's ability to ANSWER the question that could not be
    asked before. These three are the state of the calibration on 2026-07-30, and if a re-derivation
    moves one this test is the thing that says the table needs re-reading."""
    provisional = sorted(k for k, b in bars.BARS.items() if b.provisional)
    assert "leaf_ramp_stops" in provisional      # 8 of its 10 real samples FAIL it
    assert "seethrough_opening" in provisional   # 2 points, bar sits between them
    assert "axis_strong_taper" in provisional    # 1 point
    assert bars.BARS["footprint"].real == 0      # no sample count was ever recorded
