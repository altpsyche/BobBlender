"""Op dispatch: map an op dict to its builder. One registry, grows over time."""

from . import (
    atmosphere,
    biome,
    camera,
    describe,
    foliage_build,
    gen_assets,
    gen_paint,
    geonodes,
    images,
    mesh,
    path_curve,
    proxies,
    render,
    scatter_build,
    scene,
    shading,
    splines_build,
    util,
    world,
)

_HANDLERS = {
    "add_mesh": mesh.add_mesh,
    "build_geonodes": geonodes.build_geonodes,
    "make_proxies": proxies.make_proxies,
    "make_path": path_curve.make_path,
    "drape_curve": path_curve.drape_curve,
    "inspect_river": path_curve.inspect_river,  # read-only diagnostic (float check)
    "reload_image": images.reload_image,
    "build_sky": world.build_sky,
    # Scene control (core/camera.py, core/render.py, core/scene.py)
    "add_camera": camera.add_camera,
    "render": render.render,
    "delete": scene.delete,
    "clear_scene": scene.clear_scene,
    "set_env": scene.set_env,
    "apply_world": scene.apply_world,
    # Read-only introspection (core/describe.py): the one op that mutates nothing.
    "describe_scene": describe.describe_scene,
    # Shading (core/shading.py)
    "shade_terrain": shading.shade_terrain,
    "apply_shader": shading.apply_shader,
    "snow_shell": shading.snow_shell,
    # Biome (core/biome.py): one call shades terrain + scatters + sets world for a named biome
    "apply_biome": biome.apply_biome,
    "world_biome": biome.world_biome,
    # Atmosphere (core/atmosphere.py)
    "build_clouds": atmosphere.build_clouds,
    "build_fog": atmosphere.build_fog,
    "build_rain": atmosphere.build_rain,
    "build_motes": atmosphere.build_motes,
    "build_snow_cover": atmosphere.build_snow_cover,
    "apply_season": atmosphere.apply_season,
    "scene_preset": atmosphere.scene_preset,
    # Generation, the Blender half (docs/GENERATION.md, Ops and MCP). Everything that talks to
    # ComfyUI runs in the MCP process with no bpy; these are the steps that need Blender.
    "apply_texture_set": shading.apply_texture_set,
    "import_generated": gen_assets.import_generated_op,
    "make_blockout": proxies.make_blockout,
    "export_control": gen_assets.export_control_op,
    # The stylised texture route. An op rather than a comfy_* tool because Blender renders the
    # turntable and projects the restyled views back, so half of it cannot leave this process.
    "paint_stylised": gen_paint.paint_stylised_op,
    # The two recipes an owning builder is responsible for (geonodes.OWNED_RECIPES), which is why
    # build_geonodes refuses them: these record what the object IS -- a species, a kind, an emitter --
    # so the result is listed by the panels and rebuildable, which a raw recipe build was not.
    "grow_foliage": foliage_build.grow_foliage_op,
    "scatter_layer": scatter_build.scatter_layer_op,
    # Typed paths + water + erosion (core/splines_build.py)
    "make_curve": splines_build.make_curve,
    "curve_build": splines_build.curve_build,
    "bake_erode": splines_build.bake_erode,
    "revert_erode": splines_build.revert_erode,
}


def apply_op(op: dict) -> dict:
    handler = _HANDLERS.get(op.get("op"))
    if handler is None:
        raise ValueError(f"no handler for op: {op.get('op')!r}")
    util.ensure_object_mode()  # guard: predictable state regardless of user's mode
    return handler(op)
