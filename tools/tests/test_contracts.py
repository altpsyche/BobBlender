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
