"""Asset-pack resolver tests (P3). The `core.assets` module is bpy-free, so it runs in the
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
    yield mod
    mod.set_pref_roots([])
    mod.set_generated_root(None)
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


def test_validate_biome_flags_missing_texture(assets, monkeypatch, tmp_path):
    pack = tmp_path / "vpack"
    _make_pack(pack, "b", layer_texture="absent_set")
    monkeypatch.setenv("BOB_ASSET_PACKS", str(pack))
    warnings = assets.validate_biome("b")
    assert any("absent_set" in w for w in warnings)


# -- Generated model entries (R11, G3) -----------------------------------------------------------
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
    """One reader, still (R11): a v1 bare string and a v2 object both come back with height_m,
    lod, origin and faces present, so a caller never has to ask which schema it is holding."""
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
