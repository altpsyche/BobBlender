"""Contract tests: the op vocabulary the agent/Blender boundary shares.

These validate the Pydantic models in the extension's mcp_agent/contracts.py without importing
bpy (the models are pure pydantic). They guard the discriminated Operation union: every op tag
round-trips to its model, and a BuildRequest of mixed ops validates. Run in the tools venv.
"""

import sys
from pathlib import Path

import pytest

# The contract lives inside the extension (mcp_agent/). Import it as a top-level package without
# touching the bpy-bound addon __init__ -- the same trick gen_api_docs.py and the server launcher use.
EXT = Path(__file__).resolve().parents[2] / "blender" / "extensions" / "bob_blender_tools"
sys.path.insert(0, str(EXT))

from mcp_agent import contracts  # noqa: E402

# Every op tag added for the full-scene MCP work, with a minimal valid payload.
_OP_SAMPLES = {
    "add_mesh": {"op": "add_mesh", "kind": "cube"},
    "build_geonodes": {"op": "build_geonodes", "recipe": "wave_grid"},
    "make_proxies": {"op": "make_proxies"},
    "make_path": {"op": "make_path", "name": "P"},
    "drape_curve": {"op": "drape_curve", "name": "P"},
    "reload_image": {"op": "reload_image"},
    "build_sky": {"op": "build_sky", "params": {"time_of_day": 9.0}},
    "add_camera": {"op": "add_camera", "location": [10, -10, 7], "look_at": [0, 0, 0]},
    "render": {"op": "render", "output": "/tmp/x.png", "engine": "BLENDER_EEVEE"},
    "delete": {"op": "delete", "names": ["Rock"]},
    "clear_scene": {"op": "clear_scene", "keep": ["Terrain"]},
    "set_env": {"op": "set_env", "params": {"season": "winter"}},
    "shade_terrain": {"op": "shade_terrain", "object": "Terrain", "stack": "alpine"},
    "apply_shader": {"op": "apply_shader", "object": "Rock", "preset": "rock"},
    "snow_shell": {"op": "snow_shell", "object": "Terrain"},
    "apply_biome": {"op": "apply_biome", "object": "Terrain", "biome": "blockout"},
    "world_biome": {"op": "world_biome", "biome": "blockout"},
    "build_clouds": {"op": "build_clouds"},
    "build_fog": {"op": "build_fog", "mode": "height_fog"},
    "build_rain": {"op": "build_rain", "preset": "rain"},
    "build_motes": {"op": "build_motes", "preset": "dust"},
    "build_snow_cover": {"op": "build_snow_cover", "object": "Terrain"},
    "apply_season": {"op": "apply_season", "season": "winter"},
    "scene_preset": {"op": "scene_preset", "look": "golden_hour"},
    "make_curve": {"op": "make_curve", "name": "River", "role": "river"},
    "curve_build": {"op": "curve_build", "curve": "River"},
    "bake_erode": {"op": "bake_erode", "terrain": "Terrain"},
    "revert_erode": {"op": "revert_erode", "terrain": "Terrain"},
    # Generation, the Blender half (G6). The ComfyUI half is tools, not ops.
    "apply_texture_set": {"op": "apply_texture_set", "object": "Terrain", "set": "grass",
                          "index": 1},
    "import_generated": {"op": "import_generated", "kind": "rocks", "name": "boulder",
                         "height_m": 1.8},
    "export_control": {"op": "export_control", "object": "BOB_Rock_A"},
}


@pytest.mark.parametrize("op_tag,payload", sorted(_OP_SAMPLES.items()))
def test_each_op_validates_through_the_union(op_tag, payload):
    req = contracts.BuildRequest(output_file="_generated/x.blend", ops=[payload])
    assert len(req.ops) == 1
    assert req.ops[0].op == op_tag


def test_mixed_op_list_validates():
    req = contracts.BuildRequest(
        output_file="_generated/scene.blend",
        ops=list(_OP_SAMPLES.values()),
    )
    assert [o.op for o in req.ops] == list(_OP_SAMPLES.keys())


def test_unknown_op_is_rejected():
    with pytest.raises(Exception):
        contracts.BuildRequest(output_file="x.blend", ops=[{"op": "no_such_op"}])


def test_render_requires_output():
    with pytest.raises(Exception):
        contracts.BuildRequest(output_file="x.blend", ops=[{"op": "render"}])


def test_shade_terrain_requires_object():
    with pytest.raises(Exception):
        contracts.BuildRequest(output_file="x.blend", ops=[{"op": "shade_terrain"}])


# -- The generation ops (G6) ------------------------------------------------------------------
# Rejection is the half that matters here: an agent gets these wrong before it gets them right, and
# the difference between a readable rejection and a traceback is the difference between a retry and
# a stuck agent. Each case below is a DIFFERENT failure mode, not the same one four times.
def test_export_control_requires_an_object():
    with pytest.raises(Exception):
        contracts.BuildRequest(output_file="x.blend", ops=[{"op": "export_control"}])


@pytest.mark.parametrize("payload,bad_field", [
    ({"op": "import_generated", "kind": "rocks", "faces": "lots"}, "faces"),
    ({"op": "import_generated", "kind": "rocks", "height_m": "tall"}, "height_m"),
    ({"op": "import_generated", "kind": "rocks", "staged": "a path"}, "staged"),
    ({"op": "apply_texture_set", "set": "grass", "index": "second"}, "index"),
    ({"op": "export_control", "object": "P", "points": "many"}, "points"),
])
def test_generation_ops_reject_the_wrong_type(payload, bad_field):
    with pytest.raises(Exception) as exc:
        contracts.BuildRequest(output_file="x.blend", ops=[payload])
    assert bad_field in str(exc.value)


def test_apply_texture_set_clears_by_default():
    """An empty `set` is meaningful: it clears the slot back to a solid tint, so it is the default
    rather than a required field, and a caller that omits it is not making a mistake."""
    req = contracts.BuildRequest(output_file="x.blend",
                                 ops=[{"op": "apply_texture_set", "object": "Terrain"}])
    assert req.ops[0].set == ""
    assert req.ops[0].index == 0


def test_import_generated_takes_either_shape():
    """`staged` (finish then import) and `name` (import only) are both valid, and the choice between
    them is the handler's, not the contract's: an agent should not have to declare which mode it is
    in, and a contract that forbade one shape would make the two-call panel flow unrepresentable."""
    staged = {"op": "import_generated", "kind": "trees",
              "staged": {"raw_mesh": "/tmp/x.glb", "dir": "/tmp"}}
    named = {"op": "import_generated", "kind": "trees", "name": "oak_01"}
    for payload in (staged, named):
        assert contracts.BuildRequest(output_file="x.blend", ops=[payload]).ops[0].kind == "trees"


def test_op_result_carries_machine_readable_data():
    """The ops whose output the NEXT call needs return it in `data`, not only in the `info` sentence:
    export_control's path and height, import_generated's face count and UV overlap. Without this an
    agent has to parse prose to find out what it just made."""
    result = contracts.OpResult(op="export_control", info="Rock: /tmp/c.glb (1.800 m)",
                                data={"path": "/tmp/c.glb", "height_m": 1.8})
    assert result.data["height_m"] == 1.8
    assert contracts.OpResult(op="delete").data == {}
