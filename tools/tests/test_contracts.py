"""Contract tests: the op vocabulary the agent/Blender boundary shares.

These validate the Pydantic models in the extension's mcp_agent/contracts.py without importing
bpy (the models are pure pydantic). They guard the discriminated Operation union: every op tag
round-trips to its model, and a BuildRequest of mixed ops validates. Run in the tools venv.
"""

import sys
from pathlib import Path

import pytest

# The contract lives inside the extension (mcp_agent/). Import it as a top-level package without
# touching the bpy-bound addon __init__ -- the same trick gen_api_docs.py and the server launcher
# use.
EXT = Path(__file__).resolve().parents[2] / "blender" / "extensions" / "bob_blender_tools"
sys.path.insert(0, str(EXT))

from mcp_agent import contracts  # noqa: E402

# Every op tag added for the full-scene MCP work, with a minimal valid payload.
_OP_SAMPLES = {
    "add_mesh": {"op": "add_mesh", "kind": "cube"},
    "build_geonodes": {"op": "build_geonodes", "recipe": "wave_grid"},
    "grow_foliage": {"op": "grow_foliage", "species": "conifer"},
    "scatter_layer": {"op": "scatter_layer", "emitter": "Terrain", "kind": "trees"},
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
    "make_blockout": {"op": "make_blockout", "shape": "shed"},
    "paint_stylised": {"op": "paint_stylised", "object": "BOB_Rock_A",
                       "prompt": "hand-painted stylised granite"},
}

# The one handler with no contract model, named rather than filtered by a pattern: `inspect_river`
# is a read-only float check the live bridge exposes for debugging, and it is deliberately not part
# of the op vocabulary an agent composes a scene from.
_UNTYPED_HANDLERS = {"inspect_river"}


def _union_ops():
    """Every op tag in the discriminated union, read off the models themselves."""
    return {model.model_fields["op"].default
            for model in contracts.Operation.__origin__.__args__}


def _dispatch_ops():
    """Every op tag `core/dispatch.py` has a handler for, by ast-parsing it.

    Parsed rather than imported for `gen_api_docs.py`'s reason: dispatch pulls in bpy, and this
    suite runs in the plain venv.
    """
    import ast

    tree = ast.parse((EXT / "core" / "dispatch.py").read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id == "_HANDLERS" for t in node.targets):
            return {k.value for k in node.value.keys if isinstance(k, ast.Constant)}
    raise AssertionError("no _HANDLERS registry in dispatch.py")


def test_every_typed_op_has_a_builder_and_every_builder_a_contract():
    """The two halves of the op vocabulary, checked against each other rather than by intention.

    Both failures are silent otherwise. A contract with no handler validates, crosses the bridge and
    dies with a KeyError in Blender; a handler with no contract is a capability an agent cannot name
    -- which is the shape of every parity failure this round has been about.
    """
    union, handlers = _union_ops(), _dispatch_ops()
    assert sorted(union - handlers) == [], "typed ops with no builder in dispatch"
    assert sorted(handlers - union - _UNTYPED_HANDLERS) == [], \
        "builders an agent cannot reach, because no contract names them"


def test_every_typed_op_is_exercised_by_a_sample():
    """A model added to the union with no sample here is a model nothing validates. Cheap to write,
    and the omission is exactly what the samples exist to prevent."""
    assert sorted(_union_ops() - set(_OP_SAMPLES)) == [], "union ops with no sample payload"


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


# -- The generation ops (the agent-surface gate)
# ------------------------------------------------------------------ Rejection is the half that
# matters here: an agent gets these wrong before it gets them right, and the difference between a
# readable rejection and a traceback is the difference between a retry and a stuck agent. Each case
# below is a DIFFERENT failure mode, not the same one four times.
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


# -- Every field an op actually reads has to be DECLARED (a whole-scene run's finding) -----------
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


# -- The two owned recipes' ops (the vocabulary an agent's work becomes visible through) ---------
# These exist because `build_geonodes(recipe="foliage")` built a tree that recorded no species, so the
# foliage panel had nothing to show. The contract half of that fix is that the species is a FIELD --
# checked here -- and the Blender half is the ownership guard, which the `scene seams` gate asserts
# because it needs bpy.
def test_grow_foliage_carries_the_species_as_a_field():
    dumped = _round_trip({"op": "grow_foliage", "species": "conifer", "params": {"levels": 2}})
    assert dumped["species"] == "conifer" and dumped["params"] == {"levels": 2}
    # No seed asked for is None, not 0: the handler randomises a new tree and keeps an existing
    # tree's seed, and it can only tell those apart from "not asked for".
    assert dumped["seed"] is None


def test_grow_foliage_needs_no_species_at_all():
    """A tree tuned from the recipe defaults belongs to no species, so "" is a state and not a
    missing argument."""
    assert _round_trip({"op": "grow_foliage"})["species"] == ""


def test_scatter_layer_requires_an_emitter():
    """The field the whole op is about: a layer with no surface to scatter on builds an empty mesh
    and reports success, and the emitter is recorded ON the layer so a rebuild cannot re-bind it."""
    with pytest.raises(Exception) as exc:
        contracts.BuildRequest(output_file="x.blend", ops=[{"op": "scatter_layer"}])
    assert "emitter" in str(exc.value)


def test_scatter_layer_reuses_by_default_because_op_lists_replay():
    """The one default that differs from the panel's Add Layer button, deliberately: a replayed op
    list that adds another layer every run is not idempotent."""
    dumped = _round_trip({"op": "scatter_layer", "emitter": "Terrain"})
    assert dumped["reuse"] is True and dumped["kind"] == "trees"
    assert dumped["curve"] is None and dumped["curve_mode"] is None and dumped["curve_align"] is None


@pytest.mark.parametrize("payload,bad_field", [
    ({"op": "scatter_layer", "emitter": "T", "kind": "boulders"}, "kind"),
    ({"op": "scatter_layer", "emitter": "T", "curve_mode": "beside"}, "curve_mode"),
    ({"op": "scatter_layer", "emitter": "T", "align": "sideways"}, "align"),
    ({"op": "grow_foliage", "seed": "random"}, "seed"),
])
def test_the_owned_recipe_ops_reject_the_wrong_value(payload, bad_field):
    with pytest.raises(Exception) as exc:
        contracts.BuildRequest(output_file="x.blend", ops=[payload])
    assert bad_field in str(exc.value)


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
