"""Headless gate for the thirteen defects the redwood-scene run exposed (docs/GENERATION.md).

    ~/.steam/steam/steamapps/common/Blender/blender --background --factory-startup \
        --python tools/scripts/headless_redwood.py

Exit code 0 = every check passed. One script, two halves, because the two throwaway probes that
found these were a scene each: `main_scene` covers the pack/terrain/curve/material/world seams
(items 3 to 10) and `scatter_scene` covers the instance filter and sink (item 11).

Every check here would have FAILED before the fix it guards, which is the whole point of the file:
the run found these by rendering probe frames and reading them, and a gate that only asserts what
the code already says would not have caught any of them. Where a defect's cause was pure Python the
check lives in `tools/tests` instead (map resolution, pack-root ordering, the contract fields, the
VRAM floors, the dead-wood routing note); what is here is everything that needs a real depsgraph.

Unlike `headless_texset.py` this REGISTERS the addon. The curve and world ops read PropertyGroups
that `ui/` owns (`bbt_curve`, `bbt_scatter_layer`, `bbt_world`), so `core.dispatch` alone raises
AttributeError on `make_curve` -- see the known gap in docs/MCP.md.
"""

import os
import shutil
import sys
import tempfile

import bpy

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(REPO, "blender", "extensions"))

import bob_blender_tools  # noqa: E402

bob_blender_tools.register()

from bob_blender_tools.core import assets, materials  # noqa: E402
from bob_blender_tools.core import splines_build  # noqa: E402
from bob_blender_tools.core.dispatch import apply_op  # noqa: E402

FAILURES = []
HEIGHTMAP = os.path.join(REPO, "library", "textures", "grass", "grass_height.png")


def check(label, ok, detail=""):
    print(f"[{'PASS' if ok else 'FAIL'}] {label}" + (f" -- {detail}" if detail else ""))
    if not ok:
        FAILURES.append(label)
    return ok


def build_terrain(name="Terrain", reset=False):
    return apply_op({"op": "build_geonodes", "recipe": "heightmap_terrain", "name": name,
                     "params": {"heightmap": HEIGHTMAP, "size": 80.0, "resolution": 128,
                                "height": 18.0, "sea_level": 0.25},
                     **({"reset": True} if reset else {})})


# -- Half one: packs, the terrain stamp, curves, the material band, the world --------------------
def main_scene(tmp):
    # 1. A pack an op carried in is reachable, and a RENAMED set still resolves its maps. The run
    #    symlinked generated sets into library/textures/ under friendlier names, which resolved zero
    #    maps and rendered a solid tint with success in every receipt.
    pack = os.path.join(tmp, "pack")
    os.makedirs(os.path.join(pack, "textures"), exist_ok=True)
    renamed = os.path.join(pack, "textures", "roadside_duff")
    shutil.copytree(os.path.join(REPO, "library", "textures", "grass"), renamed)
    assets.add_pack_root(pack)
    maps = assets.texture_set_maps("roadside_duff")
    check("a renamed set resolves its maps", bool(maps.get("basecolor")), f"{sorted(maps)}")
    check("the shipped sets still resolve", bool(assets.texture_set_maps("grass").get("basecolor")))

    # 2. The terrain records what it was built from. Nothing stamped these outside the Terrain
    #    panel's bake, so `_has_bake` read False over MCP and a curve had nothing to drape onto.
    build_terrain()
    terr = bpy.data.objects["Terrain"]
    check("the terrain stamps its heightmap", terr.get("bbt_heightmap") == HEIGHTMAP)
    stamped = (terr.get("bbt_terrain_size"), terr.get("bbt_terrain_height"),
               terr.get("bbt_terrain_sea"))
    check("the terrain stamps size/height/sea_level", stamped == (80.0, 18.0, 0.25), f"{stamped}")

    # 3. A reset rebuild keeps its material. The rebuild moves the fresh recipe modifier back to its
    #    old index, which on a shaded terrain sits BEHIND Set Material, and the terrain came back
    #    default grey.
    apply_op({"op": "shade_terrain", "object": "Terrain", "stack": "temperate"})
    before = [m.name for m in terr.modifiers]
    build_terrain(reset=True)
    after = [m.name for m in terr.modifiers]
    check("Set Material stays last after a reset rebuild", after[-1] == materials.SET_MATERIAL_MOD,
          f"{before} -> {after}")
    evaluated = terr.evaluated_get(bpy.context.evaluated_depsgraph_get())
    mesh = evaluated.to_mesh()
    mat_names = [m.name for m in mesh.materials if m is not None]
    evaluated.to_mesh_clear()
    check("and the evaluated terrain still carries it", bool(mat_names), f"{mat_names}")

    # 4. A curve takes an explicit shape without changing role. Narrowing a road used to mean
    #    switching to dirt_path, which moves the mask channel and invalidates every scatter layer's
    #    curve_attr.
    apply_op({"op": "make_curve", "name": "Road", "role": "road", "terrain": "Terrain",
              "points": [[-30, 0, 0], [-10, 4, 0], [10, -4, 0], [30, 0, 0]],
              "shape": {"width": 5.0, "depth": 0.35}})
    road = bpy.data.objects["Road"]
    check("the shape override reached the curve", abs(road.bbt_curve.width - 5.0) < 1e-4,
          f"{road.bbt_curve.width}")
    check("the role survived it, so the mask channel did not move", road.bbt_curve.role == "road")

    # And a None means "not asked for": the contract dumps every shape key, so a set_shape that
    # wrote them all would zero the width on any call that did not restate it.
    width_before, depth_before = road.bbt_curve.width, road.bbt_curve.depth
    applied, unknown = splines_build.set_shape(road, terr, {"depth": 0.5, "width": None,
                                                            "not_a_param": 1.0})
    check("set_shape skips None and reports the unknown", applied == ["depth"]
          and unknown == ["not_a_param"], f"{applied} / {unknown}")
    check("and the untouched param kept its value",
          abs(road.bbt_curve.width - width_before) < 1e-4 and road.bbt_curve.depth > depth_before,
          f"{road.bbt_curve.width} / {road.bbt_curve.depth}")

    # 5. Draping reads the terrain's own numbers. The run had to restate the four values and nothing
    #    checked that they matched.
    res = apply_op({"op": "drape_curve", "name": "Road", "terrain": "Terrain"})
    zs = [p.co[2] for s in road.data.splines for p in s.points]
    check("drape from the terrain name alone moved Z", any(abs(z) > 1e-6 for z in zs), res["info"])
    bad = apply_op({"op": "drape_curve", "name": "Road", "terrain": "Terrain", "size": 40.0})
    check("a size that disagrees with the terrain is reported",
          bool(bad.get("data", {}).get("warnings")), bad["info"])

    # 6. curve_build reports the layer slot its band took. The run guessed the index and rendered
    #    probe frames to read it back.
    built = apply_op({"op": "curve_build", "curve": "Road", "terrain": "Terrain",
                      "do_terrain": True, "do_material": True, "do_water": False})
    slot = built["data"]["slot"]
    check("curve_build reports its band slot", slot is not None, built["info"])
    check("and says whether it draped", built["data"]["draped"] is True, built["data"]["note"])
    master = terr.active_material.node_tree.nodes["Master"]
    check("the reported slot really is the curve band",
          master.inputs[f"L{slot} Curve B Strength"].default_value > 0.5)

    # 7. A texture set assigned to that slot reaches it, with the pack the op carried in. Both
    #    halves of the defect: an undeclared pack_dir, and maps that resolved to nothing.
    res = apply_op({"op": "apply_texture_set", "object": "Terrain", "set": "roadside_duff",
                    "index": slot, "pack_dir": pack})
    master = terr.active_material.node_tree.nodes["Master"]
    albedo = master.inputs[f"L{slot} Albedo Map"]
    check("the band slot's Albedo Map is fed by a sampler", bool(albedo.links), res["info"])
    images = [n.image.name for n in terr.active_material.node_tree.nodes
              if n.bl_idname == "ShaderNodeTexImage" and n.image is not None]
    check("and real images are loaded behind it", bool(images), f"{images[:3]}")

    # 8. A set that resolves no base colour is REFUSED. It used to report success and render a tint.
    os.makedirs(os.path.join(pack, "textures", "hollow"), exist_ok=True)
    try:
        apply_op({"op": "apply_texture_set", "object": "Terrain", "set": "hollow", "index": 1,
                  "pack_dir": pack})
        check("an empty set folder is refused", False, "it was accepted")
    except ValueError as exc:
        check("an empty set folder is refused", "no readable base-colour map" in str(exc), str(exc))

    # 9. describe_scene reads the whole thing back. There was no introspection op at all.
    d = apply_op({"op": "describe_scene"})["data"]
    terrain_rep = next(o for o in d["objects"] if o["name"] == "Terrain")
    curve_rep = next(o for o in d["objects"] if o["name"] == "Road")
    mat_rep = next(m for m in d["materials"] if m["master"] == "terrain")
    band = next((L for L in mat_rep["layers"] if "Curve B Strength" in L["masks"]), None)
    check("describe_scene reports the terrain's build params",
          terrain_rep.get("terrain", {}).get("size") == 80.0)
    check("describe_scene reports the modifier stack IN ORDER",
          terrain_rep["modifiers"][-1]["name"] == materials.SET_MATERIAL_MOD,
          f"{[m['name'] for m in terrain_rep['modifiers']]}")
    check("describe_scene finds the curve band layer", band is not None and band["index"] == slot,
          f"{band}")
    check("and reports which of that layer's maps resolve ON DISK", bool(band and band["maps"]),
          f"{band['maps'] if band else None}")
    check("describe_scene reports the curve's mask attribute",
          curve_rep["curve"]["mask_attr"] == "bbt_curve_mask_b", f"{curve_rep['curve']}")

    # 10. set_env reaches materials. Season, wetness and snow-line changes produced no pixel change,
    #     because writing bbt_env is not the same as applying it.
    res = apply_op({"op": "set_env", "params": {"wetness": 0.8, "season": "winter"}})
    group = bpy.data.node_groups.get(materials.ENV_STATE)
    drivers = len(group.animation_data.drivers) if group and group.animation_data else 0
    check("set_env installed the shared env drivers", drivers > 0, f"{drivers} drivers")
    check("set_env names season as structural",
          res["data"]["structural"].get("season") == "apply_season", res["info"])
    check("apply_world re-applies with no value change",
          apply_op({"op": "apply_world"}).get("op") == "apply_world")


# -- Half two: the scatter sink and the per-asset filter (item 11) -------------------------------
def scatter_scene():
    """Generated trunks carry a wide root flare, and with no Z offset and no per-asset filter the
    flares float over sloped ground and fill the frame. Camera placement was the only lever."""
    bpy.ops.mesh.primitive_grid_add(x_subdivisions=16, y_subdivisions=16, size=20)
    emitter = bpy.context.active_object
    emitter.name = "Emitter"
    pool = bpy.data.collections.new("Pool")
    bpy.context.scene.collection.children.link(pool)
    for i, name in enumerate(("Alpha", "Bravo", "Charlie")):
        bpy.ops.mesh.primitive_cube_add(size=1.0 + i, location=(100 + i * 5, 0, 0))
        obj = bpy.context.active_object
        obj.name = name
        for coll in list(obj.users_collection):
            coll.objects.unlink(obj)
        pool.objects.link(obj)

    def build(name, params):
        apply_op({"op": "build_geonodes", "recipe": "scatter", "name": name, "reset": True,
                  "params": {"emitter": "Emitter", "assets": "Pool", "density": 2.0,
                             "distance_min": 0.5, **params}})
        bpy.context.view_layer.update()
        return bpy.data.objects[name]

    def instances(obj):
        depsgraph = bpy.context.evaluated_depsgraph_get()
        return [(i.object.original.name, round(i.matrix_world.translation.z, 4))
                for i in depsgraph.object_instances
                if i.is_instance and i.parent is not None and i.parent.original.name == obj.name]

    base = instances(build("S_base", {}))
    check("the scatter recipe builds and instances", len(base) > 10, f"{len(base)} instances")
    names = sorted({n for n, _ in base})
    check("all three assets are in the pick", names == ["Alpha", "Bravo", "Charlie"], f"{names}")

    sunk = instances(build("S_sunk", {"z_offset": -1.5}))
    if base and sunk:
        delta = (sum(z for _, z in sunk) / len(sunk)) - (sum(z for _, z in base) / len(base))
        # GLOBAL space, so a normal-aligned rock still sinks DOWN rather than along its own axis.
        check("Z Offset sinks instances by the amount asked for", abs(delta + 1.5) < 0.05,
              f"mean delta {delta:.4f}")

    excluded = sorted({n for n, _ in instances(build("S_excl", {"assets_exclude": ["Bravo"]}))})
    check("assets_exclude drops one asset from THIS layer", excluded == ["Alpha", "Charlie"],
          f"{excluded}")
    included = sorted({n for n, _ in instances(build("S_incl", {"assets_include": ["Charlie"]}))})
    check("assets_include keeps only what it names", included == ["Charlie"], f"{included}")
    # A name that is not in the collection must not empty the layer: the filter resolves names to
    # instance indices at build time, and a typo silently scattering nothing is worse than a no-op.
    unknown = sorted({n for n, _ in instances(build("S_bad", {"assets_exclude": ["Nope"]}))})
    check("an exclude that matches nothing leaves the pick alone",
          unknown == ["Alpha", "Bravo", "Charlie"], f"{unknown}")


# -- The dead-wood routing note, which is copy and therefore drifts ------------------------------
def foliage_note():
    """The info row under Generate Asset. Its dict is keyed by the `gen_kind` enum, so a renamed
    kind would silently lose its warning; the sentence itself is unit-tested in tools/tests."""
    from bob_blender_tools.ui import scatter as ui_scatter

    scn = bpy.context.scene.bbt_scatter
    kinds = {item.identifier for item in scn.bl_rna.properties["gen_kind"].enum_items}
    notes = set(ui_scatter._GEN_KIND_NOTE)
    check("every kind with a note is a real kind", notes <= kinds, f"{sorted(notes - kinds)}")
    check("trees points at dead wood, not standing trees",
          "stumps" in ui_scatter._GEN_KIND_NOTE.get("trees", ""),
          ui_scatter._GEN_KIND_NOTE.get("trees", ""))
    check("rocks carries no note, so the row stays a warning",
          "rocks" not in notes and notes == {"trees", "plants", "grass"}, f"{sorted(notes)}")


def main():
    tmp = tempfile.mkdtemp(prefix="bob_redwood_")
    try:
        main_scene(tmp)
        scatter_scene()
        foliage_note()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print()
    if FAILURES:
        print(f"{len(FAILURES)} FAILED: " + "; ".join(FAILURES))
    else:
        print("all checks passed")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
