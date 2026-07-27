"""Headless check for the G1 spike: prompt to a rendered terrain layer (docs/COMFYUI.md).

Measures the gate rather than asserting it. Generates one seamless set through ComfyUI, writes it
into a throwaway generated pack, proves it resolves through the same `assets.texture_set_maps()`
the picker uses, assigns it to a terrain layer, renders in EEVEE, and reports the wall clock split
across generation, map derivation, write, and apply plus render.

    ~/.steam/steam/steamapps/common/Blender/blender --background --factory-startup \
        --python tools/scripts/headless_comfy_texset.py

The generation half needs a live server, so it is gated on reachability and SKIPS cleanly when
there is none: no server means nothing else in the suite behaves differently, which is the
property being checked (`ComfyUI is never required`). Exit code 0 = nothing failed.
"""

import json
import os
import shutil
import sys
import time

import bpy

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(REPO, "blender", "extensions"))

from bob_blender_tools.core import assets, comfy, comfy_maps, materials, shading  # noqa: E402

FAILURES = []
OUT = os.path.join(REPO, "_generated", "comfy_texset_check")
PACK = os.path.join(OUT, "pack")
PROMPT = "mossy forest floor with small twigs and damp earth"
SEED = 4242


def check(label, ok, detail=""):
    print(f"[{'PASS' if ok else 'FAIL'}] {label}" + (f" -- {detail}" if detail else ""))
    if not ok:
        FAILURES.append(label)
    return ok


def render_variance(engine, path):
    """(max - min) over a small render's luminance, or None when the engine cannot render here.
    A textured surface varies; a solid tint does not."""
    scene = bpy.context.scene
    try:
        scene.render.engine = engine
    except TypeError:
        return None
    scene.render.resolution_x = scene.render.resolution_y = 128
    scene.render.resolution_percentage = 100
    scene.render.image_settings.file_format = "PNG"
    scene.render.filepath = path
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
    if os.path.isdir(PACK):
        shutil.rmtree(PACK)
    os.makedirs(PACK, exist_ok=True)
    with open(os.path.join(PACK, "pack.json"), "w") as fh:
        json.dump({"schema": 1, "id": "generated", "name": "Generated"}, fh)
    # The same hand-off the addon makes on register, so the set is found by the real resolver.
    assets.set_generated_root(PACK)

    ok, detail = comfy.reachable()
    print(f"    ComfyUI: {detail}")
    if not ok:
        print("[SKIP] no ComfyUI server, so the generation half cannot run")
        print("    the suite is unaffected: this is the 'ComfyUI is never required' path")
        # The runner reads a verdict wording to tell a clean finish from a silent crash (Blender
        # exits 0 after a traceback), and this gate's every check is behind the server, so the skip
        # path would otherwise reach the end having printed none. Say it explicitly: without this
        # line headless_comfy_all.py reports FAIL "no verdict printed" on a machine with no server,
        # which is the exact opposite of the property this path exists to demonstrate.
        print("no failures")
        return 0

    # 1. Generate.
    name, info = comfy.texture_set_from_prompt(PROMPT, PACK, seed=SEED, size=1024)
    secs = info["seconds"]
    check("generation produced a set", bool(name), name)

    # 2. The set is a texture set by the contract the sampler and the picker already use.
    maps = assets.texture_set_maps(name)
    for role in ("basecolor", "roughness", "height", "ao", "normal"):
        check(f"set carries {role}", role in maps, os.path.basename(maps.get(role, "")))
    check("set is listed by the picker", name in assets.list_texture_sets())

    # 3. Seam, measured on what was written rather than on what was in memory.
    with open(maps["basecolor"], "rb") as fh:
        albedo = comfy_maps.read_png(fh.read())
    seam = comfy_maps.seam_report(albedo)
    comfy_maps.write_png(os.path.join(OUT, f"{name}_tile3x3.png"), comfy_maps.tile3x3(albedo))
    check("seam is no worse than the interior detail", seam["ratio"] < 1.25,
          f"seam {seam['seam']:.3f} vs interior {seam['interior']:.3f}, "
          f"ratio {seam['ratio']:.3f}")

    # 4. Apply it to a terrain layer and render.
    t_apply = time.time()
    bpy.ops.mesh.primitive_grid_add(x_subdivisions=32, y_subdivisions=32, size=20.0)
    obj = bpy.context.active_object
    obj.name = "Terrain"
    mat = materials.terrain_material("Terrain", terrain_size=20.0)
    materials.assign_material(obj, mat)
    mat = shading.set_terrain_texture(obj, mat, 0, name)
    apply_s = time.time() - t_apply
    sampler = mat.node_tree.nodes.get(materials.TEXSET_NODE_PREFIX + "L0")
    check("sampler instanced on layer 0", sampler is not None)
    # basecolor, roughness, ao, height: the four roles the sampler consumes. The normal map is
    # written too but no master carries a normal socket, so it is deliberately not instanced.
    imgs = [n for n in mat.node_tree.nodes if n.bl_idname == "ShaderNodeTexImage"]
    check("image nodes wired for the generated maps", len(imgs) == 4, f"{len(imgs)} images")
    stored, _ = materials.stored_sets(mat, materials.MAX_TERRAIN_LAYERS)
    check("assignment recorded on the material", stored[0] == name, str(stored[:2]))

    bpy.ops.object.light_add(type="SUN", location=(0, 0, 10))
    bpy.context.active_object.data.energy = 5.0
    bpy.ops.object.camera_add(location=(0, -6, 6), rotation=(0.9, 0, 0))
    bpy.context.scene.camera = bpy.context.active_object
    t_render = time.time()
    var = None
    for engine in ("BLENDER_EEVEE", "BLENDER_EEVEE_NEXT"):
        var = render_variance(engine, os.path.join(OUT, "eevee"))
        if var is not None:
            break
    render_s = time.time() - t_render
    if var is None:
        print("[SKIP] EEVEE could not render in this environment")
    else:
        check("EEVEE render is not flat", var > 0.02, f"luminance range {var:.4f}")

    total = secs["total"] + apply_s + render_s
    print()
    print("    wall clock, warm server")
    print(f"      generate (queue + sample + fetch)  {secs['generate']:6.2f} s")
    print(f"      derive maps (numpy)                {secs['derive']:6.2f} s")
    print(f"      write set                          {secs['write']:6.2f} s")
    print(f"      apply to the terrain layer         {apply_s:6.2f} s")
    print(f"      EEVEE render                       {render_s:6.2f} s")
    print(f"      TOTAL                              {total:6.2f} s")
    check("G1 gate: prompt to rendered layer under 60 s", total < 60.0, f"{total:.2f} s")

    print()
    if FAILURES:
        print(f"{len(FAILURES)} FAILED: " + "; ".join(FAILURES))
    else:
        print("all checks passed")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
