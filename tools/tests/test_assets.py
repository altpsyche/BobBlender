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
    yield mod
    mod.set_pref_roots([])
    sys.path.remove(str(CORE))


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


def test_validate_biome_flags_missing_texture(assets, monkeypatch, tmp_path):
    pack = tmp_path / "vpack"
    _make_pack(pack, "b", layer_texture="absent_set")
    monkeypatch.setenv("BOB_ASSET_PACKS", str(pack))
    warnings = assets.validate_biome("b")
    assert any("absent_set" in w for w in warnings)
