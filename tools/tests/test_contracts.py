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
    "apply_world": {"op": "apply_world"},
    "describe_scene": {"op": "describe_scene"},
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
    # Generation, the Blender half (the agent-surface gate). The ComfyUI half is tools, not ops.
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


# -- The generation ops (the agent-surface gate) ------------------------------------------------------------------
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


# -- Every field an op actually reads has to be DECLARED (the redwood run, item 3) ---------------
# The bug this class of test exists for: `comfy_texture_set` returned `pack_dir` in its `apply_op`,
# `ApplyTextureSet` did not declare the field, and `model_dump` dropped it silently. The tool was
# right, the op was right, and the value never crossed between them -- so a freshly generated
# texture set was unreachable from Blender with no error anywhere. Round-tripping is the check,
# because validating the payload alone would have passed: pydantic IGNORES an undeclared key rather
# than rejecting it, and that is exactly what made the failure quiet.
def _round_trip(payload):
    """The payload as the Blender side receives it: validated, then dumped back to a plain dict the
    way `bridge`/`headless_build` hand it to `apply_op`."""
    req = contracts.BuildRequest(output_file="x.blend", ops=[payload])
    return req.ops[0].model_dump()


@pytest.mark.parametrize("payload,field,value", [
    # The one that broke. Without the declaration this dump has no `pack_dir` key at all.
    ({"op": "apply_texture_set", "object": "Terrain", "set": "duff", "index": 3,
      "pack_dir": "/packs/generated"}, "pack_dir", "/packs/generated"),
    # drape_curve reads the four terrain numbers off the object, so `terrain` has to arrive.
    ({"op": "drape_curve", "name": "Road", "terrain": "Terrain"}, "terrain", "Terrain"),
    # render's the VRAM-handback rule buffer release, whose default is the behaviour change.
    ({"op": "render", "output": "/tmp/x.png"}, "release_gpu", True),
    # set_env's applier switch: writing the fields without applying them was the whole defect.
    ({"op": "set_env", "params": {"season": "winter"}}, "apply", True),
    ({"op": "describe_scene", "include": ["objects"]}, "include", ["objects"]),
])
def test_a_declared_field_survives_the_dump(payload, field, value):
    assert _round_trip(payload)[field] == value


def test_drape_curve_no_longer_defaults_the_terrain_numbers():
    """They are None now, not 60/14/0.3. A default that LOOKED like a terrain meant a caller who
    omitted them draped against numbers no terrain in the scene was built with, and the op could not
    tell "not asked for" from "asked for the default"."""
    dumped = _round_trip({"op": "drape_curve", "name": "Road"})
    assert dumped["size"] is dumped["height"] is dumped["sea_level"] is None


def test_curve_shape_dumps_every_key_so_none_means_not_asked_for():
    """`set_shape` skips None for this reason: the model carries every shape param, so the dict the
    Blender side receives names all of them whatever the caller set. Reading it without the None
    skip would reset an untouched width to zero on every call."""
    dumped = _round_trip({"op": "make_curve", "name": "Road", "role": "road",
                          "shape": {"width": 5.0, "depth": 0.35}})
    shape = dumped["shape"]
    assert shape["width"] == 5.0 and shape["depth"] == 0.35
    assert len(shape) > 2 and all(v is None for k, v in shape.items()
                                  if k not in ("width", "depth"))
    # And an op with no shape at all says so with None, not with an all-None dict.
    assert _round_trip({"op": "curve_build", "curve": "Road"})["shape"] is None


def test_curve_shape_rejects_a_non_numeric_param():
    with pytest.raises(Exception) as exc:
        contracts.BuildRequest(output_file="x.blend",
                               ops=[{"op": "make_curve", "shape": {"width": "wide"}}])
    assert "width" in str(exc.value)


def test_build_result_can_report_a_batch_still_running():
    """The bridge's idempotency contract, at the contract layer: "running" is not a failure and the
    ops must not be re-sent. Before this there was no way to tell a timeout from a failure, so the
    safe retry risked duplicate objects."""
    res = contracts.BuildResult(ok=False, output_file="x.blend", batch="b-7", status="running")
    assert res.batch == "b-7" and res.status == "running" and res.error is None
    # And a plain result still validates with neither field, since headless has no batches.
    assert contracts.BuildResult(ok=True, output_file="x.blend").batch is None


def test_op_result_carries_machine_readable_data():
    """The ops whose output the NEXT call needs return it in `data`, not only in the `info` sentence:
    export_control's path and height, import_generated's face count and UV overlap. Without this an
    agent has to parse prose to find out what it just made."""
    result = contracts.OpResult(op="export_control", info="Rock: /tmp/c.glb (1.800 m)",
                                data={"path": "/tmp/c.glb", "height_m": 1.8})
    assert result.data["height_m"] == 1.8
    assert contracts.OpResult(op="delete").data == {}
