"""Renders that carry their own geometry: a beauty frame plus TRUE depth and normal passes.

This is the half of tracks D and B that only Blender can do (docs/COMFYUI.md, the retopology tier rule/the projection-route finding). A depth
estimator guesses a relative depth from pixels; Blender already knows the metric one, and it knows
the surface normal exactly rather than inferring it from shading. Handing ComfyUI those two passes
is the entire reason track D exists, and `mesh_paint_views`'s per-view restyle rests on the same three files.

**Not the compositor.** Blender 5.2 replaced `Scene.node_tree` with a compositing NODE GROUP, and
its `CompositorNodeOutputFile` writes one multilayer EXR whatever the per-item format says, which
`bpy.data.images.load` then refuses to open. So the passes come out through a **view-layer material
override** instead: one emission material per pass, rendered as the beauty image, which is
engine-native, exact, works on an arbitrary scene, and writes an ordinary PNG through the render
path Bob already uses. Measured identical in EEVEE and Cycles (depth 0.6455 either way).

Two conventions are decided here rather than assumed:

- **Depth** is near-WHITE over a Bob-computed near and far, which is the Depth Anything V2
  convention the SDXL depth ControlNet was trained against. The range comes from the geometry, not
  from the camera clip planes: a clip range of 0.1 to 1000 m would quantise a 2 m rock into two
  values.
- **Normal** is CAMERA space, encoded 0.5 + 0.5n, with a per-channel sign flip (`NORMAL_FLIP`)
  that lands it in the ordinary normal-map convention the ControlNet was trained on: red rising to
  the right, green rising upward, blue saturating where a surface faces the viewer. Verified on a
  sphere rather than argued from the docs, because Blender's camera-space Z comes out reversed.

Every entry point restores the scene state it touched, because a render that leaves the engine on
CYCLES or the view transform on Raw has broken the artist's file to make a picture.
"""

import math
import os

import bpy
from mathutils import Matrix, Vector

# Per-channel sign on the camera-space normal before it is encoded, so the result reads the way a
# normal map is expected to: +X bright to the RIGHT, +Y bright UP, +Z bright toward the viewer.
#
# Only Z needs flipping, and that is a measurement rather than a reading of the docs:
# `ShaderNodeVectorTransform`'s WORLD-to-CAMERA gives a surface facing the camera a Z of about -1,
# so without the flip a front-facing surface encodes as BLACK in blue. X and Y come out already
# right. Verified on a sphere (`headless_gen_stylise_paint_multiview.py`, part A): red rises left to right, green rises
# bottom to top, blue saturates in the middle.
NORMAL_FLIP = (1.0, 1.0, -1.0)

# The flat normal an estimator returns where there is nothing to shade. Background texels get this
# rather than black, because black decodes to the direction (-1, -1, -1) and the ControlNet then
# reads the empty half of the frame as a surface facing away from everything.
FLAT_NORMAL = (0.5, 0.5, 1.0)

# How much room to leave around the geometry when the depth range is computed from it. A hair of
# margin costs a hair of contrast and keeps a near-plane surface off the clipped white.
DEPTH_MARGIN = 0.05

_PASS_MATERIALS = ("BOB_PassDepth", "BOB_PassNormal")


# -- Scene state -------------------------------------------------------------------------------
_SAVED = ("engine", "samples", "override", "transparent", "view_transform", "look",
          "filepath", "file_format", "color_mode", "color_depth", "color_management",
          "res_x", "res_y", "res_pct", "world", "compositing")


def _snapshot(scene):
    view_layer = scene.view_layers[0]
    img = scene.render.image_settings
    return {"engine": scene.render.engine,
            "samples": (scene.cycles.samples if hasattr(scene, "cycles") else 0,
                        scene.eevee.taa_render_samples),
            "override": view_layer.material_override,
            "transparent": scene.render.film_transparent,
            "view_transform": scene.view_settings.view_transform,
            "look": scene.view_settings.look,
            "filepath": scene.render.filepath,
            "file_format": img.file_format,
            "color_mode": img.color_mode,
            "color_depth": img.color_depth,
            "color_management": img.color_management,
            "res_x": scene.render.resolution_x,
            "res_y": scene.render.resolution_y,
            "res_pct": scene.render.resolution_percentage,
            "world": scene.world,
            "compositing": getattr(scene, "use_nodes", False)}


def _restore(scene, saved):
    view_layer = scene.view_layers[0]
    img = scene.render.image_settings
    scene.render.engine = saved["engine"]
    if hasattr(scene, "cycles"):
        scene.cycles.samples = saved["samples"][0]
    scene.eevee.taa_render_samples = saved["samples"][1]
    view_layer.material_override = saved["override"]
    scene.render.film_transparent = saved["transparent"]
    scene.view_settings.view_transform = saved["view_transform"]
    scene.view_settings.look = saved["look"]
    scene.render.filepath = saved["filepath"]
    img.file_format = saved["file_format"]
    img.color_mode = saved["color_mode"]
    img.color_depth = saved["color_depth"]
    img.color_management = saved["color_management"]
    scene.render.resolution_x = saved["res_x"]
    scene.render.resolution_y = saved["res_y"]
    scene.render.resolution_percentage = saved["res_pct"]
    scene.world = saved["world"]
    scene.use_nodes = saved["compositing"]


# -- The two pass materials --------------------------------------------------------------------
def _new_material(name):
    old = bpy.data.materials.get(name)
    if old is not None:
        bpy.data.materials.remove(old)
    mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    tree = mat.node_tree
    tree.nodes.clear()
    out = tree.nodes.new("ShaderNodeOutputMaterial")
    emit = tree.nodes.new("ShaderNodeEmission")
    emit.inputs["Strength"].default_value = 1.0
    tree.links.new(emit.outputs[0], out.inputs["Surface"])
    return mat, tree, emit


def depth_material(near, far):
    """An emission material whose colour IS the normalised view depth: 1 at `near`, 0 at `far`.

    `ShaderNodeCameraData`'s View Z Depth is the metric distance along the camera axis, so this is
    the real depth buffer and not a shading trick. Clamped, so geometry outside the range saturates
    instead of wrapping.
    """
    mat, tree, emit = _new_material("BOB_PassDepth")
    cam = tree.nodes.new("ShaderNodeCameraData")
    rng = tree.nodes.new("ShaderNodeMapRange")
    rng.clamp = True
    rng.inputs["From Min"].default_value = float(near)
    rng.inputs["From Max"].default_value = float(far)
    rng.inputs["To Min"].default_value = 1.0
    rng.inputs["To Max"].default_value = 0.0
    tree.links.new(cam.outputs["View Z Depth"], rng.inputs["Value"])
    tree.links.new(rng.outputs[0], emit.inputs["Color"])
    return mat


def normal_material(flip=NORMAL_FLIP):
    """An emission material whose colour is the CAMERA-space normal, encoded 0.5 + 0.5n.

    `ShaderNodeVectorTransform` does the world-to-camera rotation in the engine, so there is no
    matrix to keep in sync with the camera and no chance of reading a stale `matrix_world`.
    """
    mat, tree, emit = _new_material("BOB_PassNormal")
    geo = tree.nodes.new("ShaderNodeNewGeometry")
    xform = tree.nodes.new("ShaderNodeVectorTransform")
    xform.vector_type = "NORMAL"
    xform.convert_from = "WORLD"
    xform.convert_to = "CAMERA"
    sign = tree.nodes.new("ShaderNodeVectorMath")
    sign.operation = "MULTIPLY"
    sign.inputs[1].default_value = tuple(flip)
    enc = tree.nodes.new("ShaderNodeVectorMath")
    enc.operation = "MULTIPLY_ADD"
    enc.inputs[1].default_value = (0.5, 0.5, 0.5)
    enc.inputs[2].default_value = (0.5, 0.5, 0.5)
    tree.links.new(geo.outputs["Normal"], xform.inputs[0])
    tree.links.new(xform.outputs[0], sign.inputs[0])
    tree.links.new(sign.outputs[0], enc.inputs[0])
    tree.links.new(enc.outputs[0], emit.inputs["Color"])
    return mat


def _drop_pass_materials():
    for name in _PASS_MATERIALS:
        mat = bpy.data.materials.get(name)
        if mat is not None:
            bpy.data.materials.remove(mat)


# -- Camera maths ------------------------------------------------------------------------------
def camera_info(camera, resolution):
    """Everything the projection needs about one camera, as plain floats.

    Read from `matrix_world` AFTER a view-layer update by the caller: a matrix read straight after
    setting `location` still describes the old transform, which is the same trap the asset gate hit with
    `bound_box`.
    """
    data = camera.data
    tan_half = 0.5 * data.sensor_width / data.lens
    return {"matrix_world": [list(row) for row in camera.matrix_world],
            "lens": data.lens, "sensor_width": data.sensor_width,
            "type": data.type, "ortho_scale": data.ortho_scale,
            "tan_half": tan_half, "resolution": int(resolution)}


def project(cam_info, points):
    """World points to (pixel x, pixel y, camera depth) arrays, for a SQUARE render.

    Square on purpose: an aspect ratio would be a second convention to keep in sync between the
    render, the projection and the ControlNet hint, and every view Bob renders for a mesh is square
    because that is also what SDXL wants.
    """
    import numpy as np

    view = np.array(Matrix(cam_info["matrix_world"]).inverted(), dtype="float64")
    pts = np.asarray(points, dtype="float64")
    cam = pts @ view[:3, :3].T + view[:3, 3]
    depth = -cam[:, 2]  # Blender's camera looks down -Z, so this is positive in front
    res = cam_info["resolution"]
    if cam_info["type"] == "ORTHO":
        half = 0.5 * cam_info["ortho_scale"]
        ndc_x, ndc_y = cam[:, 0] / half, cam[:, 1] / half
    else:
        safe = np.where(depth > 1e-6, depth, 1e-6)
        ndc_x = cam[:, 0] / (safe * cam_info["tan_half"])
        ndc_y = cam[:, 1] / (safe * cam_info["tan_half"])
    px = (ndc_x * 0.5 + 0.5) * res
    py = (0.5 - ndc_y * 0.5) * res  # image rows run top-down
    return px, py, depth


def depth_range(camera, objects, margin=DEPTH_MARGIN):
    """(near, far) along the camera axis over `objects`' world bounding boxes.

    From the GEOMETRY, not from the camera clip planes: a 0.1 to 1000 m clip range would quantise a
    2 m rock into two of the 256 values an 8-bit hint carries.
    """
    depths = []
    matrix = camera.matrix_world.inverted()
    for obj in objects:
        for corner in obj.bound_box:
            local = matrix @ (obj.matrix_world @ Vector(corner))
            # In FRONT of the camera only. A ground plane large enough to reach behind the camera
            # otherwise pulls `near` to zero and spends most of the 256 available values on empty
            # space, which is the quantisation the geometry-derived range exists to avoid.
            if -local.z > 1e-4:
                depths.append(-local.z)
    if not depths:
        return camera.data.clip_start, camera.data.clip_end
    near, far = min(depths), max(depths)
    span = max(far - near, 1e-3)
    return max(near - span * margin, 1e-4), far + span * margin


# -- Rendering ---------------------------------------------------------------------------------
def _write_pass(scene, path, raw):
    """Point the render at `path` and shoot. `raw` writes the emission values as BYTES rather than
    as a display image, because a pass is data: an sRGB transfer curve on a depth ramp is a
    monotonic lie the ControlNet then has to undo.

    Measured, and it is not the obvious API. `image_settings.linear_colorspace_settings` does NOT
    control the output transfer, so setting it alone leaves the view transform in place and a linear
    0.5 lands on byte 187 (sRGB) instead of 128: a sphere facing the camera then encodes its normal
    as (187, 187, 255) rather than (128, 128, 255). A per-output OVERRIDE plus a Raw view transform
    is what writes values, and `_LINEAR_ENCODE` records whether that worked so `_finish_pass` can
    undo the curve numerically on a build where it did not.
    """
    img = scene.render.image_settings
    img.file_format = "PNG"
    img.color_mode = "RGBA"
    img.color_depth = "8"
    encoded = False
    if raw:
        img.color_management = "OVERRIDE"
        for name in ("Raw", "Standard"):
            try:
                img.view_settings.view_transform = name
            except TypeError:
                continue
            encoded = name != "Raw"
            break
        else:
            encoded = True
        img.view_settings.look = "None"
        img.view_settings.exposure = 0.0
        img.view_settings.gamma = 1.0
    else:
        img.color_management = "FOLLOW_SCENE"
    scene.render.filepath = os.path.splitext(path)[0]
    bpy.ops.render.render(write_still=True)
    return os.path.splitext(path)[0] + ".png", encoded


def _srgb_to_linear(bytes_array):
    """The inverse sRGB transfer, on 0-255 floats. Needed only when the Raw view transform was
    unavailable and the render came out display-encoded."""
    import numpy as np

    value = np.clip(bytes_array / 255.0, 0.0, 1.0)
    low = value / 12.92
    high = ((value + 0.055) / 1.055) ** 2.4
    return np.where(value <= 0.04045, low, high) * 255.0


def _finish_pass(path, flat=None, encoded=False):
    """Turn a rendered pass into the file a ControlNet reads: values in the bytes, no alpha.

    Two jobs. It linearises when the render came out display-encoded (see `_write_pass`), so the
    bytes are the pass and not a curve of it. And it rewrites fully transparent pixels to `flat`,
    because black in a normal map decodes to the direction (-1, -1, -1) and an empty frame half then
    reads as a surface facing away from everything. Depth keeps black, which IS the far plane.
    """
    import numpy as np

    try:
        from . import comfy_maps
    except ImportError:
        import comfy_maps

    with open(path, "rb") as fh:
        data = comfy_maps.read_png(fh.read())
    if data.ndim != 3 or data.shape[2] < 3:
        return path
    rgb = data[..., :3].astype("float32")
    if encoded:
        rgb = _srgb_to_linear(rgb)
    if flat is not None and data.shape[2] >= 4:
        rgb[data[..., 3] == 0] = np.array(flat, dtype="float32") * 255.0
    comfy_maps.write_png(path, np.clip(np.rint(rgb), 0, 255).astype("uint8"))
    return path


def render_passes(out_dir, stem, *, camera=None, objects=None, resolution=1024, samples=32,
                  engine=None, beauty=True, near=None, far=None, flip=NORMAL_FLIP,
                  transparent=True):
    """Render `stem_beauty.png`, `stem_depth.png` and `stem_normal.png` into `out_dir`.

    Returns the paths plus the camera and the depth range, which is what the projection bake and
    the provenance both need. THREE renders, not one: the two passes are constant-per-pixel
    emission, so they run at one sample and cost a fraction of the beauty frame.

    `objects` scopes the depth range; None means every visible mesh, which is the right default for
    a whole-scene stylise and the wrong one for a single asset (pass the asset).
    """
    scene = bpy.context.scene
    camera = camera or scene.camera
    if camera is None:
        raise RuntimeError("render_passes: the scene has no camera")
    os.makedirs(out_dir, exist_ok=True)
    saved = _snapshot(scene)
    scene.camera = camera
    if engine:
        scene.render.engine = engine
    scene.render.resolution_x = scene.render.resolution_y = int(resolution)
    scene.render.resolution_percentage = 100
    scene.render.film_transparent = bool(transparent)
    bpy.context.view_layer.update()

    scope = list(objects) if objects else [o for o in scene.objects
                                           if o.type == "MESH" and o.visible_get()]
    if near is None or far is None:
        near, far = depth_range(camera, scope)
    info = camera_info(camera, resolution)
    view_layer = scene.view_layers[0]
    out = {"camera": info, "near": float(near), "far": float(far), "flip": list(flip),
           "resolution": int(resolution), "engine": scene.render.engine}
    try:
        if beauty:
            scene.view_settings.view_transform = saved["view_transform"]
            if scene.render.engine == "CYCLES":
                scene.cycles.samples = int(samples)
            else:
                scene.eevee.taa_render_samples = int(samples)
            view_layer.material_override = None
            out["beauty"] = _write_pass(scene, os.path.join(out_dir, stem + "_beauty.png"),
                                        raw=False)[0]
        # The passes are data, so: one sample, no view transform, bytes as values, no world and no
        # compositor. The last two are not tidiness: an emission override does not replace the
        # WORLD, so a sky would print itself into the depth map, and a compositor group with a
        # glare node in it would smear the normal map into something no ControlNet should read.
        scene.world = None
        scene.use_nodes = False
        scene.render.film_transparent = True
        scene.view_settings.view_transform = "Standard"
        scene.view_settings.look = "None"
        if scene.render.engine == "CYCLES":
            scene.cycles.samples = 1
        else:
            scene.eevee.taa_render_samples = 1
        view_layer.material_override = depth_material(near, far)
        path, encoded = _write_pass(scene, os.path.join(out_dir, stem + "_depth.png"), raw=True)
        out["depth"] = _finish_pass(path, encoded=encoded)
        out["display_encoded"] = bool(encoded)
        view_layer.material_override = normal_material(flip)
        path, encoded = _write_pass(scene, os.path.join(out_dir, stem + "_normal.png"), raw=True)
        out["normal"] = _finish_pass(path, flat=FLAT_NORMAL, encoded=encoded)
    finally:
        view_layer.material_override = None
        _drop_pass_materials()
        _restore(scene, saved)
    return out


# -- Turntable ---------------------------------------------------------------------------------
def frame_radius(obj, lens, sensor_width, fill=0.82):
    """How far a camera with this lens has to sit from `obj` for it to fill `fill` of the frame."""
    size = max(max(obj.dimensions), 1e-4)
    tan_half = 0.5 * sensor_width / lens
    return 0.5 * size / (tan_half * fill)


def _flat_world(strength=1.6):
    """A uniform white environment: the closest a render gets to lit-by-nothing.

    The same argument `tex_tileable`'s flat-lighting prompt suffix makes (docs/COMFYUI.md, family 1): a colour
    map with baked lighting is unusable and no amount of Bob-side maths removes it. A projection
    paint takes the render's PIXELS as albedo, so a sun's terminator would be painted into the
    texture and lit a second time at render.
    """
    world = bpy.data.worlds.new("BOB_FlatWorld")
    world.use_nodes = True
    background = world.node_tree.nodes.get("Background")
    if background is not None:
        background.inputs[0].default_value = (1.0, 1.0, 1.0, 1.0)
        background.inputs[1].default_value = float(strength)
    return world


def turntable_views(obj, out_dir, *, count=6, elevation=20.0, extra_elevations=(72.0, -55.0),
                    resolution=1024, samples=32, engine=None, lens=80.0, fill=0.82,
                    flip=NORMAL_FLIP, stem="view", isolate=True, flat_light=True):
    """Render views around `obj`, each with its own depth and normal pass.

    `count` views form a RING at `elevation`, evenly spaced from the object's front (-Y, Blender's
    front view), so view 0 is the canonical front and view `count // 2` is its 180 degree opposite:
    the pair the cross-view drift is measured on. `extra_elevations` then adds one view per entry
    from the front azimuth, which is how the top and the underside get painted at all -- a ring at
    20 degrees left 28% of a closed boulder's charts to the hole fill.

    `isolate` hides every other object from the render, because a paint view has to be of the mesh
    and not of the scene it sits in. `flat_light` swaps in a uniform white world for the same reason
    `tex_tileable`'s prompt suffix asks for flat lighting: these pixels become an albedo map.

    Returns a list of dicts, each carrying the three paths, the camera info, the angle, and `ring`.
    Every scene setting this touches is restored, including the per-object render visibility.
    """
    scene = bpy.context.scene
    os.makedirs(out_dir, exist_ok=True)
    centre = sum((obj.matrix_world @ Vector(c) for c in obj.bound_box), Vector()) / 8.0
    radius = frame_radius(obj, lens, 36.0, fill=fill)

    data = bpy.data.cameras.new("BOB_TurntableCam")
    data.lens = lens
    data.sensor_width = 36.0
    data.clip_start = max(radius * 0.01, 1e-3)
    data.clip_end = radius * 10.0
    cam = bpy.data.objects.new("BOB_TurntableCam", data)
    scene.collection.objects.link(cam)
    saved_camera = scene.camera
    hidden = []
    saved_world = scene.world
    flat = None
    if isolate:
        for other in scene.objects:
            if other is not obj and other is not cam and not other.hide_render:
                hidden.append(other)
                other.hide_render = True
    if flat_light:
        flat = _flat_world()
        scene.world = flat

    angles = [(2.0 * math.pi * i / int(count), elevation, True) for i in range(int(count))]
    angles += [(0.0, float(elev), False) for elev in (extra_elevations or ())]
    views = []
    try:
        for i, (angle, elev, ring) in enumerate(angles):
            rad_elev = math.radians(elev)
            offset = Vector((math.sin(angle) * math.cos(rad_elev),
                             -math.cos(angle) * math.cos(rad_elev),
                             math.sin(rad_elev))) * radius
            cam.location = centre + offset
            # Track the centre without a constraint: a constraint would need a depsgraph
            # evaluation before matrix_world is true, and this is one rotation to compute.
            direction = (centre - cam.location).normalized()
            cam.rotation_euler = direction.to_track_quat("-Z", "Y").to_euler()
            bpy.context.view_layer.update()
            shot = render_passes(out_dir, f"{stem}_{i:02d}", camera=cam, objects=[obj],
                                 resolution=resolution, samples=samples, engine=engine, flip=flip)
            shot.update(index=i, angle_deg=math.degrees(angle), elevation_deg=float(elev),
                        ring=bool(ring))
            views.append(shot)
    finally:
        scene.camera = saved_camera
        scene.world = saved_world
        for other in hidden:
            other.hide_render = False
        bpy.data.objects.remove(cam, do_unlink=True)
        bpy.data.cameras.remove(data)
        if flat is not None:
            bpy.data.worlds.remove(flat)
    return views
