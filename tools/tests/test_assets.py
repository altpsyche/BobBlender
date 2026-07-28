"""Asset-pack resolver tests. The `core.assets` module is bpy-free, so it runs in the
venv directly. Imported by path (inserting the core dir) to avoid the extension package's
bpy-importing __init__. Covers: the bundled block-out floor, resolution from a pack root
OUTSIDE the repo (the shippable-install case), first-hit-wins ordering, the pack.json reader,
preference roots, and texture-set resolution."""

import importlib
import json
import os
import pathlib
import sys

import pytest

CORE = pathlib.Path(__file__).resolve().parents[2] / "blender" / "extensions" / "bob_blender_tools" / "core"


@pytest.fixture
def assets(monkeypatch):
    """A fresh import of the bpy-free assets module with env + pref roots cleared each test."""
    sys.path.insert(0, str(CORE))
    monkeypatch.delenv("BOB_ASSET_PACKS", raising=False)
    mod = importlib.import_module("assets")
    mod.set_pref_roots([])
    mod.set_generated_root(None)
    mod._OP_ROOTS = []
    yield mod
    mod.set_pref_roots([])
    mod.set_generated_root(None)
    mod._OP_ROOTS = []
    sys.path.remove(str(CORE))


def _make_set(root, name, roles=("basecolor", "roughness", "ao", "height")):
    """Write a texture set's map files, named the way a real set on disk is."""
    d = root / "textures" / name
    d.mkdir(parents=True, exist_ok=True)
    for role in roles:
        ext = ".png" if role in ("normal", "height") else ".jpg"
        (d / f"{name}_{role}{ext}").write_bytes(b"")
    return d


def _make_pack(root, biome, *, layer_texture=None, texture_sets=(), pack_json=True):
    """Write a minimal valid pack at `root`: one proxy biome plus any texture-set folders."""
    bdir = root / "models" / biome
    bdir.mkdir(parents=True)
    manifest = {"meta": {"name": biome, "proxy": True},
                "terrain": {"layers": [{"layer": "soil", **({"texture": layer_texture} if layer_texture else {})}]},
                "scatter": {"trees": {"density": 1.0}}}
    (bdir / "manifest.json").write_text(json.dumps(manifest))
    for ts in texture_sets:
        (root / "textures" / ts).mkdir(parents=True)
    if pack_json:
        (root / "pack.json").write_text(json.dumps({"schema": 1, "id": root.name, "name": root.name}))


def test_bundled_blockout_is_the_floor(assets):
    # No env, no prefs: the block-out pack bundled inside the extension is always present.
    assert "blockout" in assets.list_biomes()
    assert assets.biome_manifest("blockout")["meta"].get("proxy") is True


def test_resolves_from_pack_outside_repo(assets, monkeypatch, tmp_path):
    pack = tmp_path / "forest-scandinavia"
    _make_pack(pack, "birch_glade")
    monkeypatch.setenv("BOB_ASSET_PACKS", str(pack))
    assert "birch_glade" in assets.list_biomes()
    assert assets.biome_dir("birch_glade") == str(pack / "models" / "birch_glade")
    assert assets.biome_manifest("birch_glade")["meta"].get("proxy") is True


def test_first_hit_wins(assets, monkeypatch, tmp_path):
    p1, p2 = tmp_path / "p1", tmp_path / "p2"
    _make_pack(p1, "dup")
    _make_pack(p2, "dup")
    monkeypatch.setenv("BOB_ASSET_PACKS", os.pathsep.join([str(p1), str(p2)]))
    # p1 precedes p2 in the search path, so it wins the name.
    assert assets.biome_dir("dup") == str(p1 / "models" / "dup")


def test_pref_roots_resolve(assets, tmp_path):
    pack = tmp_path / "prefpack"
    _make_pack(pack, "meadow")
    assets.set_pref_roots([str(pack)])
    assert "meadow" in assets.list_biomes()


def test_read_pack(assets, tmp_path):
    pack = tmp_path / "named"
    _make_pack(pack, "b", pack_json=True)
    assert assets.read_pack(str(pack))["id"] == "named"
    # No pack.json: a minimal manifest is synthesized from the folder name.
    bare = tmp_path / "bare"
    (bare / "models").mkdir(parents=True)
    got = assets.read_pack(str(bare))
    assert got["id"] == "bare" and got["schema"] == 1


def test_texture_set_dir(assets, monkeypatch, tmp_path):
    pack = tmp_path / "tex"
    _make_pack(pack, "b", texture_sets=("grass",))
    monkeypatch.setenv("BOB_ASSET_PACKS", str(pack))
    assert assets.texture_set_dir("grass") == str(pack / "textures" / "grass")
    assert assets.texture_set_dir("nope") is None


def test_texture_set_maps_only_lists_files_on_disk(assets, monkeypatch, tmp_path):
    pack = tmp_path / "tex2"
    _make_pack(pack, "b")
    _make_set(pack, "gravel", roles=("basecolor", "roughness"))
    monkeypatch.setenv("BOB_ASSET_PACKS", str(pack))
    maps = assets.texture_set_maps("gravel")
    assert set(maps) == {"basecolor", "roughness"}
    assert maps["basecolor"].endswith("gravel_basecolor.jpg")
    assert assets.texture_set_maps("nope") == {}


def test_list_texture_sets_needs_a_basecolor(assets, monkeypatch, tmp_path):
    pack = tmp_path / "tex3"
    _make_pack(pack, "b")
    _make_set(pack, "good")
    _make_set(pack, "roughness_only", roles=("roughness",))
    (pack / "textures" / "empty").mkdir(parents=True)
    monkeypatch.setenv("BOB_ASSET_PACKS", str(pack))
    listed = assets.list_texture_sets()
    assert "good" in listed
    assert "roughness_only" not in listed and "empty" not in listed
    # The shipped dev library sets come along too, so the picker is never empty in-repo.
    assert {"grass", "rock", "soil"} <= set(listed)


def test_generated_root_is_a_search_root(assets, tmp_path):
    gen = tmp_path / "packs" / "generated"
    _make_set(gen, "ai_moss")
    assert "ai_moss" not in assets.list_texture_sets()
    assets.set_generated_root(str(gen))
    assert str(gen) in assets.asset_roots()
    assert "ai_moss" in assets.list_texture_sets()
    assert assets.texture_set_dir("ai_moss") == str(gen / "textures" / "ai_moss")
    # Ordered after the preference folders, so a curated pack of the same name still wins.
    pref = tmp_path / "curated"
    _make_set(pref, "ai_moss")
    assets.set_pref_roots([str(pref)])
    assert assets.texture_set_dir("ai_moss") == str(pref / "textures" / "ai_moss")


# -- Op pack roots and stem-tolerant map resolution (the redwood run, items 3 and 4) -------------
# Both halves of "a generated texture set is invisible to Blender". The pack the generator wrote
# into was not on the search path, and the workaround for THAT (a renamed symlink) tripped a second
# bug in map resolution which reported success and rendered a solid tint.
def test_add_pack_root_outranks_the_preferences_and_is_ordered(assets, tmp_path):
    """A `pack_dir` an op carried in is more specific than a folder list configured once, so it wins
    the name; `$BOB_ASSET_PACKS` still beats both, because that is the user's explicit override."""
    pref, op1, op2 = tmp_path / "pref", tmp_path / "op1", tmp_path / "op2"
    for root in (pref, op1, op2):
        _make_set(root, "duff")
    assets.set_pref_roots([str(pref)])
    assert assets.texture_set_dir("duff") == str(pref / "textures" / "duff")

    assert assets.add_pack_root(str(op1)) == str(op1)
    assert assets.texture_set_dir("duff") == str(op1 / "textures" / "duff")
    # Most recent first, so the pack the LAST op wrote into is the one a bare name resolves to.
    assets.add_pack_root(str(op2))
    assert assets.op_roots() == [str(op2), str(op1)]
    assert assets.texture_set_dir("duff") == str(op2 / "textures" / "duff")
    # Idempotent: re-adding moves a root to the front rather than duplicating it.
    assets.add_pack_root(str(op1))
    assert assets.op_roots() == [str(op1), str(op2)]
    assert assets.add_pack_root("") is None and assets.add_pack_root(None) is None


def test_env_packs_still_beat_an_op_root(assets, monkeypatch, tmp_path):
    env, op = tmp_path / "env", tmp_path / "op"
    _make_set(env, "duff")
    _make_set(op, "duff")
    monkeypatch.setenv("BOB_ASSET_PACKS", str(env))
    assets.add_pack_root(str(op))
    assert assets.texture_set_dir("duff") == str(env / "textures" / "duff")


def test_texture_set_maps_tolerates_a_renamed_folder(assets, monkeypatch, tmp_path):
    """The silent one. Maps used to resolve as `<folder>_<role>.<ext>`, so a set whose folder was
    renamed or symlinked under a friendlier name resolved to ZERO maps -- and every check upstream
    passed, because the folder existed. The layer then rendered as a solid tint with success in
    every receipt."""
    pack = tmp_path / "gen"
    _make_set(pack, "sdxl_output_0007")
    (pack / "textures" / "sdxl_output_0007").rename(pack / "textures" / "roadside_duff")
    monkeypatch.setenv("BOB_ASSET_PACKS", str(pack))
    maps = assets.texture_set_maps("roadside_duff")
    assert set(maps) == {"basecolor", "roughness", "ao", "height"}
    assert maps["basecolor"].endswith("sdxl_output_0007_basecolor.jpg")
    # And the set is now offerable, which is the other half: the picker needs a base colour.
    assert "roadside_duff" in assets.list_texture_sets()


def test_texture_set_maps_prefers_the_exact_stem_over_a_stray_file(assets, monkeypatch, tmp_path):
    """The fallback must not be able to hijack a correctly named set. A pack that happens to carry
    another set's map beside the right one still resolves the one whose stem matches the folder."""
    pack = tmp_path / "mixed"
    _make_set(pack, "gravel", roles=("basecolor",))
    (pack / "textures" / "gravel" / "leftover_basecolor.jpg").write_bytes(b"")
    monkeypatch.setenv("BOB_ASSET_PACKS", str(pack))
    assert assets.texture_set_maps("gravel")["basecolor"].endswith("gravel_basecolor.jpg")


def test_texture_set_maps_reads_a_bare_role_filename(assets, monkeypatch, tmp_path):
    """The third naming a generator emits: no stem at all, just `basecolor.png`."""
    pack = tmp_path / "bare"
    d = pack / "textures" / "moss"
    d.mkdir(parents=True)
    for role in ("basecolor", "roughness"):
        (d / f"{role}.png").write_bytes(b"")
    monkeypatch.setenv("BOB_ASSET_PACKS", str(pack))
    maps = assets.texture_set_maps("moss")
    assert set(maps) == {"basecolor", "roughness"}
    assert maps["basecolor"].endswith("moss/basecolor.png")


def test_validate_biome_flags_missing_texture(assets, monkeypatch, tmp_path):
    pack = tmp_path / "vpack"
    _make_pack(pack, "b", layer_texture="absent_set")
    monkeypatch.setenv("BOB_ASSET_PACKS", str(pack))
    warnings = assets.validate_biome("b")
    assert any("absent_set" in w for w in warnings)


# -- Generated model entries (the manifest origin rule, the asset gate)
# -----------------------------------------------------------
def _make_generated_pack(root, *, height_m=1.8, on_disk=True):
    """The pack `core.gen_assets` writes: ONE biome-shaped manifest named `generated`, with the
    kinds inside it. One per kind would put "rocks" and "trees" in the biome picker."""
    d = root / "models" / "generated" / "rocks"
    d.mkdir(parents=True)
    if on_disk:
        (d / "boulder.glb").write_bytes(b"glTF")
    manifest = {"meta": {"name": "Generated", "generated": True},
                "models": {"rocks": [{"file": "rocks/boulder.glb", "height_m": height_m,
                                      "lod": [0.5, 0.15], "origin": "base", "faces": 3376,
                                      "prompt": "a mossy granite boulder", "seed": 1234}]}}
    (root / "models" / "generated" / "manifest.json").write_text(json.dumps(manifest))
    (root / "pack.json").write_text(json.dumps({"schema": 1, "id": "generated"}))
    return root


def test_norm_entries_defaults_the_generated_fields(assets, monkeypatch, tmp_path):
    """One reader, still (the manifest origin rule): a v1 bare string and a v2 object both come back
    with height_m, lod, origin and faces present, so a caller never has to ask which schema it is
    holding."""
    pack = tmp_path / "p"
    bdir = pack / "models" / "b"
    bdir.mkdir(parents=True)
    (bdir / "manifest.json").write_text(json.dumps({"trees": ["oak.glb"],
                                                    "models": {"rocks": [{"file": "r.glb",
                                                                          "height_m": 2.5}]}}))
    monkeypatch.setenv("BOB_ASSET_PACKS", str(pack))
    man = assets.biome_manifest("b")
    assert man["models"]["trees"][0] == {"file": "oak.glb", "height_m": 1.0, "lod": [],
                                         "origin": "base", "faces": None}
    rock = man["models"]["rocks"][0]
    assert rock["height_m"] == 2.5 and rock["origin"] == "base" and rock["lod"] == []


def test_generated_manifest_validates_its_entries(assets, monkeypatch, tmp_path):
    pack = _make_generated_pack(tmp_path / "gen")
    monkeypatch.setenv("BOB_ASSET_PACKS", str(pack))
    assert assets.validate_biome("generated") == []


def test_generated_manifest_flags_a_missing_file_and_a_defaulted_height(assets, monkeypatch,
                                                                        tmp_path):
    """The two ways a generated entry is wrong: the GLB is gone, or nobody set a real height and
    the asset would scatter at 1 m."""
    pack = _make_generated_pack(tmp_path / "gen", height_m=1.0, on_disk=False)
    monkeypatch.setenv("BOB_ASSET_PACKS", str(pack))
    warnings = assets.validate_biome("generated")
    assert any("missing on disk" in w for w in warnings)
    assert any("no real height_m" in w for w in warnings)


def test_a_hand_authored_models_block_is_still_inert(assets, monkeypatch, tmp_path):
    """Only a manifest a generator wrote gets a real importer; a hand-authored biome's models
    block is still ignored at build time and the warning has to keep saying so."""
    pack = tmp_path / "p"
    bdir = pack / "models" / "b"
    bdir.mkdir(parents=True)
    (bdir / "manifest.json").write_text(json.dumps({"meta": {"name": "b"},
                                                    "models": {"trees": ["oak.glb"]}}))
    monkeypatch.setenv("BOB_ASSET_PACKS", str(pack))
    assert any("models block is ignored" in w for w in assets.validate_biome("b"))


# -- Foliage species presets (BobFoliage, docs/FOLIAGE.md 6) ------------------------------
# Presets are DATA in a pack rather than a dict in the recipe, which is the whole point of these
# tests: a pack can ship a species, so the reader has to survive whatever a pack ships.

def _make_species(root, name, params=None, meta=None):
    d = root / "foliage"
    d.mkdir(parents=True, exist_ok=True)
    body = {"meta": meta if meta is not None else {"name": name, "kind": "trees"},
            "params": params if params is not None else {"levels": 2, "height": 4.0}}
    (d / f"{name}.json").write_text(json.dumps(body))
    return d / f"{name}.json"


def test_blockout_ships_the_shipped_species(assets):
    """The block-out pack is the floor, so a bare install can grow all three scatter kinds with
    no pack configured and no ComfyUI server."""
    names = assets.list_foliage_species()
    assert {"conifer", "broadleaf", "shrub", "grass_tuft"} <= set(names)
    kinds = {assets.foliage_species(n)["meta"]["kind"] for n in names}
    assert {"trees", "plants", "grass"} <= kinds
    for kind in ("trees", "plants", "grass"):
        assert assets.foliage_species_for_kind(kind) in names


def test_a_species_owns_its_stiffness_but_not_the_weather(assets):
    """Sway and Leaf Flutter are species traits; Wind and Wind Direction are the world's.

    BobFoliage. A spruce barely moves and grass is nearly all motion, so how stiff a plant is
    belongs to its preset the way its taper does -- and every shipped species sets both, or the
    knob is a knob nothing ever turns. The world's wind is the other half and must NOT be settable
    here: the world applier writes it onto every tree on every change, so a preset that carried one
    would be overwritten on the next slider drag, which reads as a preset that did not take.
    """
    assert {"sway", "leaf_flutter"} <= set(assets.FOLIAGE_PARAM_KEYS)
    assert not {"wind", "wind_direction"} & set(assets.FOLIAGE_PARAM_KEYS)
    stiffness = {n: assets.foliage_species(n)["params"]
                 for n in ("conifer", "broadleaf", "shrub", "grass_tuft")}
    for name, params in stiffness.items():
        assert "sway" in params and "leaf_flutter" in params, name
    # And the shipped values say something rather than all being 1.0: a conifer is the stiffest
    # thing in the set and grass is the loosest, which is the claim a stand has to read as.
    assert stiffness["conifer"]["sway"] < stiffness["broadleaf"]["sway"]
    assert stiffness["conifer"]["leaf_flutter"] < stiffness["grass_tuft"]["leaf_flutter"]


def test_a_species_may_not_smuggle_the_weather_in(assets, monkeypatch, tmp_path):
    """A preset naming `wind` has it dropped by the reader and reported by the validator, rather
    than silently ignored downstream -- the same treatment any other unknown key gets."""
    _make_species(tmp_path / "p", "windy", params={"height": 5.0, "wind": 9.0})
    monkeypatch.setenv("BOB_ASSET_PACKS", str(tmp_path / "p"))
    assert "wind" not in assets.foliage_species("windy")["params"]
    assert any("unknown param 'wind'" in w for w in assets.validate_foliage_species("windy"))


def test_species_resolves_from_a_pack_and_first_hit_wins(assets, monkeypatch, tmp_path):
    p1, p2 = tmp_path / "p1", tmp_path / "p2"
    _make_species(p1, "spruce", params={"height": 30.0})
    _make_species(p2, "spruce", params={"height": 3.0})
    monkeypatch.setenv("BOB_ASSET_PACKS", os.pathsep.join([str(p1), str(p2)]))
    assert assets.foliage_species("spruce")["params"]["height"] == 30.0


def test_species_meta_defaults(assets, monkeypatch, tmp_path):
    """A preset with no meta still reads: the name is titled from the filename and the kind
    defaults to trees, so a minimal hand-written file is valid."""
    pack = tmp_path / "p"
    (pack / "foliage").mkdir(parents=True)
    (pack / "foliage" / "black_pine.json").write_text(json.dumps({"params": {"levels": 3}}))
    monkeypatch.setenv("BOB_ASSET_PACKS", str(pack))
    spec = assets.foliage_species("black_pine")
    assert spec["meta"] == {"name": "Black Pine", "kind": "trees"}
    assert spec["params"] == {"levels": 3}


def test_unknown_params_are_dropped_and_flagged(assets, monkeypatch, tmp_path):
    """An unknown key would be silently ignored by build_geonodes, so the tree would build at
    defaults and look merely wrong. The reader drops it and the validator says so."""
    pack = tmp_path / "p"
    _make_species(pack, "typo", params={"levels": 2, "trunk_radius": 0.3, "trunkRadius": 0.9})
    monkeypatch.setenv("BOB_ASSET_PACKS", str(pack))
    assert "trunkRadius" not in assets.foliage_species("typo")["params"]
    assert assets.foliage_species("typo")["params"]["trunk_radius"] == 0.3
    assert any("unknown param 'trunkRadius'" in w for w in assets.validate_foliage_species("typo"))


def test_inert_level_params_are_flagged(assets, monkeypatch, tmp_path):
    """l3_* on a two-level species does nothing, which reads as "the preset did not take"."""
    pack = tmp_path / "p"
    _make_species(pack, "bush", params={"levels": 2, "l3_angle": 40.0, "l3_length": 0.5})
    monkeypatch.setenv("BOB_ASSET_PACKS", str(pack))
    assert any("l3_* params are inert" in w for w in assets.validate_foliage_species("bush"))


def test_bad_species_files_are_warnings_not_crashes(assets, monkeypatch, tmp_path):
    pack = tmp_path / "p"
    (pack / "foliage").mkdir(parents=True)
    (pack / "foliage" / "broken.json").write_text("{not json")
    _make_species(pack, "empty", params={})
    _make_species(pack, "odd_kind", meta={"kind": "rocks"})
    _make_species(pack, "no_atlas", params={"levels": 1, "atlas": "nope"})
    monkeypatch.setenv("BOB_ASSET_PACKS", str(pack))
    assert assets.foliage_species("broken") == {}
    assert any("unreadable" in w for w in assets.validate_foliage_species("broken"))
    assert any("no params block" in w for w in assets.validate_foliage_species("empty"))
    assert any("meta.kind 'rocks' unknown" in w for w in assets.validate_foliage_species("odd_kind"))
    assert any("leaf atlas set missing" in w for w in assets.validate_foliage_species("no_atlas"))
    assert assets.validate_foliage_species("nothing_here") == [
        "nothing_here: no foliage/nothing_here.json in any pack"]


def test_missing_generatable_sets_are_reported_apart_from_authoring_mistakes(
        assets, monkeypatch, tmp_path):
    """A named-but-absent bark set is a STATE, not a mistake, and the texture sets is what made that
    matter.

    No placeholder bark set ships, deliberately -- a hand-made one would hide the grain-direction
    problem generation actually has -- so the shipped tree presets name the bark they want and pick
    it up the moment it is generated. A caller asking "is this preset well authored" has to be able
    to filter that out, which is what splitting the report is for.
    """
    pack = tmp_path / "p"
    _make_species(pack, "pine", params={"levels": 2, "bark_set": "bark_pine",
                                        "atlas": "atlas_pine", "trunkRadius": 0.5})
    monkeypatch.setenv("BOB_ASSET_PACKS", str(pack))
    missing = assets.foliage_missing_sets("pine")
    assert {(k, v) for k, _label, v in missing} == {("bark_set", "bark_pine"),
                                                   ("atlas", "atlas_pine")}
    warnings = assets.validate_foliage_species("pine")
    # Both kinds of finding are reported, and the generatable ones name the tool that makes them.
    assert any("comfy_bark_set" in w for w in warnings)
    assert any("comfy_leaf_atlas" in w for w in warnings)
    assert any("unknown param 'trunkRadius'" in w for w in warnings)
    real = [w for w in warnings if "missing: textures/" not in w]
    assert len(real) == 1 and "trunkRadius" in real[0]


def test_missing_sets_report_nothing_once_the_set_exists(assets, monkeypatch, tmp_path):
    pack = tmp_path / "p"
    _make_species(pack, "pine", params={"levels": 2, "bark_set": "bark_pine"})
    bark = pack / "textures" / "bark_pine"
    bark.mkdir(parents=True)
    (bark / "bark_pine_basecolor.png").write_bytes(b"")
    monkeypatch.setenv("BOB_ASSET_PACKS", str(pack))
    assert assets.foliage_missing_sets("pine") == []
    assert assets.validate_foliage_species("pine") == []


def test_shipped_tree_presets_name_the_bark_they_want(assets):
    """The the texture sets wiring, as data: a species names its bark set, so generating it once
    dresses every tree of that species with no assignment step anywhere."""
    assert assets.foliage_species("conifer")["params"]["bark_set"] == "bark_conifer"
    assert assets.foliage_species("broadleaf")["params"]["bark_set"] == "bark_broadleaf"


def test_atlas_grid_comes_from_the_sets_own_sidecar(assets, monkeypatch, tmp_path):
    """The [the texture sets] open question, answered: the SET carries its layout.

    A card reading a 2x2 grid off a 4x4 atlas samples a quarter of the cell it wanted plus slices of
    three neighbours, which renders as foliage -- so nothing downstream catches it and the default
    has to come from the artifact rather than from a knob nobody knew to change.
    """
    pack = tmp_path / "p"
    for name, meta in (("four", {"atlas": {"cols": 4, "rows": 4}}),
                       ("wide", {"atlas": {"cols": 8, "rows": 1}}),
                       ("nometa", None),
                       ("junk", {"atlas": {"cols": "lots"}}),
                       ("zero", {"atlas": {"cols": 0, "rows": 2}})):
        d = pack / "textures" / name
        d.mkdir(parents=True)
        (d / f"{name}_basecolor.png").write_bytes(b"")
        if meta is not None:
            (d / "meta.json").write_text(json.dumps(meta))
    monkeypatch.setenv("BOB_ASSET_PACKS", str(pack))
    assert assets.atlas_grid("four") == (4, 4)
    assert assets.atlas_grid("wide") == (8, 1)
    # A set that declares nothing says so, rather than being guessed at.
    assert assets.atlas_grid("nometa") is None
    assert assets.atlas_grid("junk") is None
    assert assets.atlas_grid("zero") is None
    assert assets.atlas_grid("no_such_set_anywhere") is None


def test_texture_set_meta_is_optional_and_never_raises(assets, monkeypatch, tmp_path):
    pack = tmp_path / "p"
    d = pack / "textures" / "broken_meta"
    d.mkdir(parents=True)
    (d / "broken_meta_basecolor.png").write_bytes(b"")
    (d / "meta.json").write_text("{not json")
    monkeypatch.setenv("BOB_ASSET_PACKS", str(pack))
    assert assets.texture_set_meta("broken_meta") == {}
    assert assets.texture_set_meta("absent") == {}
    # The shipped placeholder atlas has no sidecar, so the recipe's own default has to stand.
    assert assets.atlas_grid("leaf_atlas_blockout") is None


def test_species_for_kind_is_none_when_nothing_grows_it(assets, monkeypatch, tmp_path):
    """The Scatter panel only draws Grow in BobFoliage when this resolves, so a pack that
    overrides the search path with no grass species must report that rather than guess."""
    pack = tmp_path / "only"
    _make_species(pack, "pine", meta={"kind": "trees"})
    monkeypatch.setenv("BOB_ASSET_PACKS", str(pack))
    monkeypatch.setattr(assets, "_bundled_root", lambda: str(tmp_path / "absent"))
    assert assets.foliage_species_for_kind("trees") == "pine"
    assert assets.foliage_species_for_kind("grass") is None


def test_opacity_is_a_resolvable_texture_role(assets, monkeypatch, tmp_path):
    """A leaf atlas's cutout can ship as its own map. It is resolved here but never reaches the
    S_TexSet sampler, which is what keeps the role free of a shared-group version bump."""
    pack = tmp_path / "p"
    _make_set(pack, "leafy", roles=("basecolor", "opacity"))
    monkeypatch.setenv("BOB_ASSET_PACKS", str(pack))
    assert "opacity" in assets.TEXTURE_MAP_ROLES
    assert os.path.basename(assets.texture_set_maps("leafy")["opacity"]) == "leafy_opacity.jpg"
