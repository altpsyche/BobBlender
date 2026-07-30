"""Headless check for the BobShaders texture-set sampler (docs/SHADERS.md, docs/GENERATION.md).

Measures rather than asserts-by-inspection: it builds a real terrain material with the shipped
`grass` set on a layer, walks the graph to prove the image nodes actually reach the master's map
sockets, renders the plane in EEVEE and in Cycles, and fails if either render comes back flat
(one colour = the sampler is wired but not sampling).

    ~/.steam/steam/steamapps/common/Blender/blender --background --factory-startup \
        --python tools/scripts/headless_texset.py

Exit code 0 = every check passed. The node counts it prints are the sampler's node budget: one
shared sampler group per layer and image nodes only for the maps a layer actually enables, because
six layers times five maps would otherwise be thirty image nodes in one material.
"""

import os
import sys

import bpy

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(REPO, "blender", "extensions"))

from bob_blender_tools.core import assets, materials, shading  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))  # for `_gate`
from _gate import Gate  # noqa: E402

# The shared gate harness (`_gate.py`): one implementation of the verdict (`check` / `note` /
# `skip` / the exit code) AND of what every gate needs around it -- the section banner, the scene
# wipe, the VRAM sampler, the cached-artifact sidecar. Bound to module-level names so the call sites
# below read as plain assertions. `FAILURES` is the Gate's own list, not a copy.
GATE = Gate("texture-set check")
check, note, skip = GATE.check, GATE.note, GATE.skip
FAILURES = GATE.failures
OUT = os.path.join(REPO, "_generated", "texset_check")


def upstream(socket):
    """The node feeding a socket, or None. One hop: enough to tell a link from a default."""
    return socket.links[0].from_node if socket.links else None


def feeds(socket, node):
    """True when `node` feeds `socket`. Compares by name, not identity: bpy hands out a fresh
    Python wrapper per access, so `is` on two views of the same node is False."""
    src = upstream(socket)
    return src is not None and node is not None and src.name == node.name


def make_plane(name, size=20.0):
    """A subdivided plane standing in for the terrain grid. Subdivided because the bump and the
    slope masks read geometry, and a two-triangle quad gives the shader nothing to vary over."""
    bpy.ops.mesh.primitive_grid_add(x_subdivisions=32, y_subdivisions=32, size=size)
    obj = bpy.context.active_object
    obj.name = name
    return obj


def render_variance(engine, path):
    """Render a small frame and return (max - min) over the luminance of its pixels, or None if
    the engine could not render here. A textured surface varies; a solid tint does not."""
    scene = bpy.context.scene
    # Assign rather than consult the enum: the engine enum on the RNA type is resolved once, so
    # an engine whose addon was enabled after startup (Cycles) is missing from it while still
    # being perfectly assignable.
    try:
        scene.render.engine = engine
    except TypeError:
        return None
    scene.render.resolution_x = scene.render.resolution_y = 128
    scene.render.resolution_percentage = 100
    scene.render.image_settings.file_format = "PNG"
    scene.render.filepath = path
    if engine == "CYCLES":
        scene.cycles.samples = 16
    try:
        bpy.ops.render.render(write_still=True)
    except Exception as exc:
        print(f"    {engine} render raised: {exc}")
        return None
    if not os.path.isfile(path + ".png"):
        return None
    img = bpy.data.images.load(path + ".png")
    px = list(img.pixels)
    lum = [(px[i] + px[i + 1] + px[i + 2]) / 3.0 for i in range(0, len(px), 4)]
    bpy.data.images.remove(img)
    return max(lum) - min(lum)


def main():
    os.makedirs(OUT, exist_ok=True)

    # 1. The set resolves off the search path with the maps the sampler consumes.
    maps = assets.texture_set_maps("grass")
    check("grass set resolves", bool(maps), ", ".join(sorted(maps)))
    for role in ("basecolor", "roughness", "ao", "height"):
        check(f"grass has {role}", role in maps)
    check("grass is listed by the picker", "grass" in assets.list_texture_sets(),
          str(assets.list_texture_sets()))

    # 2. Build a terrain BobShader with the set on layer 0.
    obj = make_plane("Terrain")
    mat = materials.terrain_material("Terrain", terrain_size=20.0,
                                     texsets=["grass", "", "", "", "", ""])
    materials.assign_material(obj, mat)
    nt = mat.node_tree
    master = nt.nodes.get("Master")
    check("terrain BobShader built", materials.master_type(mat) == "terrain")

    # 3. The image nodes actually reach the master's per-layer map sockets, through the one
    #    shared S_TexSet instance. This is the check that a wired-but-dead graph fails.
    sampler = nt.nodes.get(materials.TEXSET_NODE_PREFIX + "L0")
    check("L0 sampler instance exists", sampler is not None)
    check("sampler is the shared group",
          sampler is not None and sampler.node_tree.name == materials.TEXSET_SAMPLER)
    for sock_name in ("Albedo Map", "Roughness Map", "Detail Height"):
        check(f"master L0 {sock_name} <- sampler",
              feeds(master.inputs[f"L0 {sock_name}"], sampler))
    for sock_name, role in (("Albedo", "basecolor"), ("Roughness", "roughness"),
                            ("AO", "ao"), ("Height", "height")):
        src = upstream(sampler.inputs[sock_name]) if sampler else None
        ok = src is not None and src.bl_idname == "ShaderNodeTexImage" and src.image is not None
        check(f"sampler {sock_name} <- {role} image",
              ok and os.path.basename(src.image.filepath).startswith(f"grass_{role}"),
              os.path.basename(src.image.filepath) if ok else "not an image node")

    # 4. Projection and colour management.
    tex = nt.nodes.get(materials.TEXSET_NODE_PREFIX + "L0 basecolor")
    check("box (triplanar) projection by default", tex is not None and tex.projection == "BOX")
    check("basecolor is sRGB",
          tex is not None and tex.image.colorspace_settings.name == "sRGB")
    rough = nt.nodes.get(materials.TEXSET_NODE_PREFIX + "L0 roughness")
    check("roughness is Non-Color",
          rough is not None and rough.image.colorspace_settings.name == "Non-Color")
    coord = upstream(nt.nodes[materials.TEXSET_NODE_PREFIX + "L0 Mapping"].inputs["Vector"])
    check("mapping reads Object coordinates",
          coord is not None and coord.bl_idname == "ShaderNodeTexCoord"
          and nt.nodes[materials.TEXSET_NODE_PREFIX + "L0 Mapping"]
          .inputs["Vector"].links[0].from_socket.name == "Object")

    # 5. The height path: master Height -> Bump -> Principled Normal.
    bsdf = next(n for n in nt.nodes if n.bl_idname == "ShaderNodeBsdfPrincipled")
    bump = upstream(bsdf.inputs["Normal"])
    check("bump drives the Principled Normal",
          bump is not None and bump.bl_idname == "ShaderNodeBump")
    check("bump reads the master's blended Height",
          bump is not None and feeds(bump.inputs["Height"], master))

    # 6. The node budget: one shared sampler group per layer, nodes only for enabled maps.
    imgs = [n for n in nt.nodes if n.bl_idname == "ShaderNodeTexImage"]
    print(f"    nodes in M_Terrain: {len(nt.nodes)} ({len(imgs)} image textures, "
          f"1 textured layer of {materials.MAX_TERRAIN_LAYERS})")
    check("image nodes only for the textured layer", len(imgs) == 4, f"{len(imgs)} images")

    # 7. Tuned sampler knobs survive a structural rebuild (assigning a second layer's set).
    sampler.inputs["AO Amount"].default_value = 0.25
    nt.nodes[materials.TEXSET_NODE_PREFIX + "Bump"].inputs["Strength"].default_value = 0.8
    master.inputs["L1 Enable"].default_value = 1.0
    mat2 = shading.set_terrain_texture(obj, mat, 1, "rock")
    nt2 = mat2.node_tree
    check("rebuild kept the same material datablock", mat2.name == mat.name)
    check("tuned AO Amount survived the rebuild",
          abs(nt2.nodes[materials.TEXSET_NODE_PREFIX + "L0"]
              .inputs["AO Amount"].default_value - 0.25) < 1e-5)
    check("tuned Bump Strength survived the rebuild",
          abs(nt2.nodes[materials.TEXSET_NODE_PREFIX + "Bump"]
              .inputs["Strength"].default_value - 0.8) < 1e-5)
    check("tuned master input survived the rebuild",
          nt2.nodes["Master"].inputs["L1 Enable"].default_value > 0.5)
    sets, box = materials.stored_sets(mat2, materials.MAX_TERRAIN_LAYERS)
    check("assignment recorded on the material", sets[:2] == ["grass", "rock"] and box, str(sets))
    imgs2 = [n for n in nt2.nodes if n.bl_idname == "ShaderNodeTexImage"]
    print(f"    nodes after a second set: {len(nt2.nodes)} ({len(imgs2)} image textures, "
          f"2 textured layers)")

    # 8. Clearing a set drops back to a solid tint with no leftover sampler.
    shading.set_terrain_texture(obj, mat, 1, "")
    check("clearing a set removes its sampler",
          mat.node_tree.nodes.get(materials.TEXSET_NODE_PREFIX + "L1") is None)
    check("clearing one set keeps the other",
          mat.node_tree.nodes.get(materials.TEXSET_NODE_PREFIX + "L0") is not None)

    # 9. Render: the surface must not come back flat, in either engine.
    materials.stored_sets(mat, materials.MAX_TERRAIN_LAYERS)
    nt.nodes[materials.TEXSET_NODE_PREFIX + "L0"].inputs["AO Amount"].default_value = 1.0
    bpy.ops.object.light_add(type="SUN", location=(0, 0, 10))
    bpy.context.active_object.data.energy = 5.0
    bpy.ops.object.camera_add(location=(0, -6, 6), rotation=(0.9, 0, 0))
    bpy.context.scene.camera = bpy.context.active_object
    # Cycles ships as an addon, which --factory-startup leaves off, so its engine identifier is
    # absent from the enum until it is enabled.
    try:
        bpy.ops.preferences.addon_enable(module="cycles")
    except Exception as exc:
        print(f"    could not enable Cycles: {exc}")
    done = set()
    # EEVEE's identifier moved across versions (BLENDER_EEVEE_NEXT in 4.2, back to
    # BLENDER_EEVEE after), so offer both and take whichever this build accepts.
    for engine, label in (("BLENDER_EEVEE", "EEVEE"), ("BLENDER_EEVEE_NEXT", "EEVEE"),
                          ("CYCLES", "Cycles")):
        if label in done:
            continue
        var = render_variance(engine, os.path.join(OUT, label.lower()))
        if var is None:
            continue
        done.add(label)
        check(f"{label} render is not flat", var > 0.02, f"luminance range {var:.4f}")
    for label in ("EEVEE", "Cycles"):
        if label not in done:
            print(f"[SKIP] {label} could not render in this environment")

    print()
    return GATE.exit_code()


if __name__ == "__main__":
    sys.exit(main())
