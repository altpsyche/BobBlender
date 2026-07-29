"""Headless measurement: look-dev stylise, the stylised paint route, and multi-view conditioning
(docs/GENERATION.md).

Four questions, each answered with a number rather than a screenshot:

  A. **Does a Bob render come back stylised with the silhouette preserved, and is Blender's TRUE
     depth and normal pass worth the export code?** `stylize_render` (real passes) against `stylize_render_est` (Depth Anything
     V2 plus NormalBAE on the same frame), same seed, same prompt, same denoise. Preservation is
     measured two ways: the IoU of the thresholded silhouette against the depth pass, and the
     agreement between Blender's true depth and Depth Anything V2's reading of each stylised output,
     after the affine alignment a scale-invariant estimator requires.
  B. **Does a mesh come back stylised, with the seam and the drift measured ACROSS a turntable?**
     Six views through `mesh_paint_views` under both ControlNets with the front view as the
     IPAdapter reference, then Blender projection-bakes them: per-view mean absolute difference in
     the overlap regions, and the front-against-180-degrees drift. Plus the LoRA control the route
     exists for, measured as the difference one makes on a fixed seed.
  D. **Does the Advanced-panel button work through the real operator and the real job queue?**
     One press: the render happens on the main thread (it has to), the stylise on the worker, and
     the longest main-thread tick while it ran is measured against one frame at 60 Hz.
  C. **Does multi-view geometry beat single-view on a back-facing test?** A ground-truth object
     whose back is not inferable from the front, through `mesh_geom_trellis` (front only),
     `mesh_geom_mv_trellis` (Trellis2MultiViewImageToShape) and `mesh_geom_mv` (Hunyuan multi-view),
     scored against the ground truth by voxel IoU and Chamfer distance, whole and BACK HALF only.

    ~/.steam/steam/steamapps/common/Blender/blender --background --factory-startup \\
        --python tools/scripts/headless_gen_stylise_paint_multiview.py -- [--part a,b,c] [--fresh] [--views 6]

Reachability-gated: with no server it prints SKIP for the generation half and exits 0, which is
itself the check that ComfyUI is never required. Every generated file is cached under
`_generated/stylise_paint_check/`, so re-measuring costs seconds. Exit 0 = nothing failed.
"""

import argparse
import importlib
import json
import math
import os
import subprocess
import sys
import threading
import time

import bpy
import numpy as np

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(REPO, "blender", "extensions"))

from bob_blender_tools.core import (  # noqa: E402
    comfy,
    comfy_maps,
    gen_assets,
    gen_paint,
    gen_views,
)

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))  # for `_gate`
from _gate import Gate  # noqa: E402

# The shared gate harness (`_gate.py`): one `check` / `note` / exit-code implementation for every
# gate, bound to module-level names so the call sites below read as plain assertions. `FAILURES` is
# the Gate's own list, not a copy, so anything already reading it keeps working.
GATE = Gate("stylise/paint gate")
check, note, skip = GATE.check, GATE.note, GATE.skip
FAILURES = GATE.failures
OUT = os.path.join(REPO, "_generated", "stylise_paint_check")
GEN = os.path.join(OUT, "gen")
DUMP = os.path.join(REPO, "tools", "tests", "data", "object_info_min.json")
G3B_GEN = os.path.join(REPO, "_generated", "route_ab_check", "gen")

STYLE_PROMPT = "painted concept art, warm evening light, loose brushwork"
PAINT_PROMPT = "mossy granite boulder, hand painted stylised game texture, saturated moss"
SEED = 4242
RESOLUTION = 1024


def section(title):
    print()
    print(f"-- {title} " + "-" * max(0, 76 - len(title)))


def empty_scene():
    bpy.ops.wm.read_factory_settings(use_empty=True)


# -- VRAM, per process and summed over the ComfyUI family -----------------------------------------
_OURS = {os.getpid()}


def _gpu_sample():
    try:
        card = subprocess.run(["nvidia-smi", "--query-gpu=memory.used",
                               "--format=csv,noheader,nounits"],
                              capture_output=True, text=True, timeout=10).stdout.strip()
        apps = subprocess.run(["nvidia-smi", "--query-compute-apps=pid,used_memory",
                               "--format=csv,noheader,nounits"],
                              capture_output=True, text=True, timeout=10).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None, {}
    procs = {}
    for line in apps.splitlines():
        bits = [b.strip() for b in line.split(",")]
        if len(bits) == 2 and bits[0].isdigit() and bits[1].isdigit():
            procs[int(bits[0])] = int(bits[1])
    return (int(card.splitlines()[0]) if card else None), procs


class Vram:
    """Peak VRAM across a job, sampled from a thread. Copied from `headless_gen_oneshot_vs_staged.py`,
    because the rule it encodes is the point: read PER PROCESS and sum over the ComfyUI family,
    and report the RISE over the stage's own baseline as well as the absolute peak, which is
    order-dependent (`mesh_subject` and `stylize_render` both leave SDXL resident at roughly 6.6
    GB)."""

    def __init__(self, interval=0.5):
        self.interval = interval
        self.card_peak = self.comfy_peak = 0
        self.card_start = self.comfy_start = 0
        self._stop = threading.Event()
        self._thread = None

    def _family(self, procs):
        return sum(mib for pid, mib in procs.items() if pid not in _OURS)

    def _loop(self):
        while not self._stop.is_set():
            card, procs = _gpu_sample()
            if card is not None:
                self.card_peak = max(self.card_peak, card)
                self.comfy_peak = max(self.comfy_peak, self._family(procs))
            self._stop.wait(self.interval)

    def __enter__(self):
        card, procs = _gpu_sample()
        self.card_start = self.card_peak = card or 0
        self.comfy_start = self.comfy_peak = self._family(procs)
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *exc):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)
        return False

    def report(self):
        return {"card_start": self.card_start, "card_peak": self.card_peak,
                "comfy_start": self.comfy_start, "comfy_peak": self.comfy_peak,
                "rise": self.comfy_peak - self.comfy_start}


# -- Image maths ---------------------------------------------------------------------------------
def read_rgb(path):
    with open(path, "rb") as fh:
        data = np.asarray(comfy_maps.read_png(fh.read()))
    if data.ndim == 2:
        data = np.repeat(data[:, :, None], 3, axis=2)
    return data[:, :, :3].astype("float32")


def read_grey(path):
    return read_rgb(path).mean(axis=2)


def resize_to(array, size):
    """Nearest-neighbour resize, which is all a comparison of masks and low-frequency depth needs."""
    h, w = array.shape[:2]
    ys = (np.arange(size) * h / size).astype("int32")
    xs = (np.arange(size) * w / size).astype("int32")
    return array[ys][:, xs]


def affine_agreement(truth, estimate):
    """MAE and Pearson r between two depth images after fitting `estimate` onto `truth` affinely.

    A monocular depth estimator is scale and shift invariant by construction, so comparing its
    output to metres without the fit measures the units and not the geometry.
    """
    a = estimate.astype("float64").ravel()
    b = truth.astype("float64").ravel()
    ok = np.isfinite(a) & np.isfinite(b)
    a, b = a[ok], b[ok]
    if a.size < 16 or a.std() < 1e-9:
        return {"mae": None, "r": None}
    slope, offset = np.polyfit(a, b, 1)
    fitted = slope * a + offset
    r = float(np.corrcoef(a, b)[0, 1])
    scale = max(b.max() - b.min(), 1e-9)
    return {"mae": float(np.abs(fitted - b).mean() / scale), "r": r}


def silhouette_iou(mask_a, mask_b):
    union = np.logical_or(mask_a, mask_b).sum()
    return float(np.logical_and(mask_a, mask_b).sum() / union) if union else None


def luminance_mask(image, floor=12.0):
    """Where the frame has content, for a subject rendered over an empty background."""
    return image.mean(axis=2) > floor


def area_matched_mask(estimate, reference_mask):
    """`estimate` thresholded so its area equals `reference_mask`'s, which is how a silhouette is
    compared against a scale-free estimator without inventing a threshold.

    Area matching removes exactly the freedom a monocular estimator has (an arbitrary offset) and
    leaves the one thing the comparison is about: whether the boundary lands in the same place.
    """
    import numpy as np

    wanted = int(reference_mask.sum())
    if not wanted or wanted >= estimate.size:
        return np.ones_like(reference_mask)
    # Exactly `wanted` pixels by RANK, not by a threshold. A threshold breaks on ties, and ties are
    # the normal case here: a monocular estimator returns a hard 0 across a whole sky, so
    # `estimate >= cut` with cut == 0 selects the entire frame and the IoU collapses to the
    # reference's own area fraction. Found the hard way, on a run that read 0.544.
    flat = estimate.ravel()
    top = np.argpartition(flat, flat.size - wanted)[flat.size - wanted:]
    mask = np.zeros(flat.size, dtype=bool)
    mask[top] = True
    return mask.reshape(estimate.shape)


def edge_iou(truth, estimate, quantile=0.92):
    """IoU of the strong depth-discontinuity edges of two depth images.

    The local half of "is the silhouette preserved": an area-matched region test says whether the
    same amount of the frame is near, and this says whether the BOUNDARY between near and far is in
    the same place. Both masks take the same number of pixels, so neither route can win by being
    blurrier.
    """
    import numpy as np

    def edges(image, count=None):
        gy, gx = np.gradient(image.astype("float64"))
        mag = np.hypot(gx, gy)
        wanted = count if count is not None else int(mag.size * (1.0 - quantile))
        flat = mag.ravel()
        top = np.argpartition(flat, flat.size - wanted)[flat.size - wanted:]
        mask = np.zeros(flat.size, dtype=bool)
        mask[top] = True
        return mask.reshape(image.shape), wanted

    truth_mask, count = edges(truth)
    est_mask, _ = edges(estimate, count)
    return silhouette_iou(truth_mask, est_mask)


# -- Mesh maths ----------------------------------------------------------------------------------
def mesh_points(obj, count=8000, seed=0):
    """Area-weighted samples on `obj`'s surface, in the unit cube its own bbox defines.

    Unit-normalised on purpose: every image-to-3D model returns a unit-cube mesh, so a comparison
    against a metre-scale ground truth has to normalise both or it measures the scale.
    """
    mesh = obj.data
    mesh.calc_loop_triangles()
    verts = np.empty(len(mesh.vertices) * 3, dtype="float64")
    mesh.vertices.foreach_get("co", verts)
    verts = verts.reshape(-1, 3)
    world = np.array(obj.matrix_world, dtype="float64")
    verts = verts @ world[:3, :3].T + world[:3, 3]
    tris = np.array([list(t.vertices) for t in mesh.loop_triangles], dtype="int64")
    if not len(tris):
        return np.zeros((0, 3))
    a, b, c = verts[tris[:, 0]], verts[tris[:, 1]], verts[tris[:, 2]]
    areas = 0.5 * np.linalg.norm(np.cross(b - a, c - a), axis=1)
    total = areas.sum()
    if total <= 0:
        return np.zeros((0, 3))
    rng = np.random.default_rng(seed)
    pick = rng.choice(len(tris), size=count, p=areas / total)
    u = rng.random((count, 1))
    v = rng.random((count, 1))
    over = (u + v) > 1
    u[over], v[over] = 1 - u[over], 1 - v[over]
    pts = a[pick] + u * (b[pick] - a[pick]) + v * (c[pick] - a[pick])
    centre = (pts.min(axis=0) + pts.max(axis=0)) * 0.5
    scale = max((pts.max(axis=0) - pts.min(axis=0)).max(), 1e-9)
    return (pts - centre) / scale


def voxelise(points, grid=48):
    """Surface-occupancy voxels of unit-cube points. Surface, not solid: an open mesh has no
    inside, and TRELLIS.2 is chosen precisely because it makes open meshes."""
    idx = np.clip(((points + 0.5) * grid).astype("int32"), 0, grid - 1)
    vol = np.zeros((grid, grid, grid), dtype=bool)
    vol[idx[:, 0], idx[:, 1], idx[:, 2]] = True
    return vol


def rotate_y(points, turns):
    """`turns` quarter turns about the up axis (Z in Blender, which is what the import leaves)."""
    angle = math.pi * 0.5 * turns
    cos, sin = math.cos(angle), math.sin(angle)
    out = points.copy()
    out[:, 0] = points[:, 0] * cos - points[:, 1] * sin
    out[:, 1] = points[:, 0] * sin + points[:, 1] * cos
    return out


def chamfer(a, b, chunk=512):
    """Symmetric mean nearest-neighbour distance between two unit-cube point sets."""
    def one_way(src, dst):
        total = 0.0
        for start in range(0, len(src), chunk):
            block = src[start:start + chunk]
            d = np.linalg.norm(block[:, None, :] - dst[None, :, :], axis=2)
            total += d.min(axis=1).sum()
        return total / max(len(src), 1)
    return float(0.5 * (one_way(a, b) + one_way(b, a)))


def shape_agreement(truth_points, candidate_points, grid=48):
    """Best-over-rotation voxel IoU and Chamfer, whole and on the BACK HALF only.

    The rotation search is not a fudge: TRELLIS.2 and Hunyuan write different up and front axes and
    the point of the test is the geometry, not the exporter's convention. The winning turn is
    reported so the number can be read honestly. The back half is `y > 0` in Blender's frame, the
    half the front view cannot see.
    """
    best = None
    truth_vox = voxelise(truth_points, grid)
    for turns in range(4):
        rotated = rotate_y(candidate_points, turns)
        vox = voxelise(rotated, grid)
        union = np.logical_or(truth_vox, vox).sum()
        iou = float(np.logical_and(truth_vox, vox).sum() / union) if union else 0.0
        if best is None or iou > best["iou"]:
            best = {"iou": iou, "turns": turns, "points": rotated}
    rotated = best.pop("points")
    back_truth = truth_points[truth_points[:, 1] > 0]
    back_cand = rotated[rotated[:, 1] > 0]
    if len(back_truth) > 32 and len(back_cand) > 32:
        bt, bc = voxelise(back_truth, grid), voxelise(back_cand, grid)
        union = np.logical_or(bt, bc).sum()
        best["iou_back"] = float(np.logical_and(bt, bc).sum() / union) if union else 0.0
        best["chamfer_back"] = chamfer(back_truth[:2500], back_cand[:2500])
    else:
        best["iou_back"] = None
        best["chamfer_back"] = None
    best["chamfer"] = chamfer(truth_points[:2500], rotated[:2500])
    return best


def _stamp(target, data=None):
    """Read or write the timing and VRAM beside a cached artifact.

    Without this a re-measured table is a table of zeros, and these numbers go into the plan: a
    cached run has to report what the generating run measured, not what the cache cost.
    """
    path = target + ".json"
    if data is None:
        try:
            with open(path) as fh:
                return json.load(fh) or {}
        except (OSError, ValueError):
            return {}
    with open(path, "w") as fh:
        json.dump(data, fh, indent=2, sort_keys=True, default=str)
    return data


# -- Scenes --------------------------------------------------------------------------------------
def _sun_and_world(scene, strength=3.0):
    light = bpy.data.objects.new("Sun", bpy.data.lights.new("Sun", "SUN"))
    light.data.energy = strength
    light.rotation_euler = (math.radians(55), 0, math.radians(35))
    scene.collection.objects.link(light)
    world = bpy.data.worlds.new("BOB_TestWorld")
    world.use_nodes = True
    bg = world.node_tree.nodes.get("Background")
    if bg:
        bg.inputs[0].default_value = (0.28, 0.42, 0.62, 1.0)
        bg.inputs[1].default_value = 1.0
    scene.world = world
    return light


def build_render_scene():
    """A small Bob-shaped scene: a displaced ground, a few props, a camera and a sun.

    Real generated assets when an earlier one-shot-against-staged run has left them on disk, and
    primitives otherwise, so the test
    works on a fresh clone and gets better on a machine that has already generated.
    """
    empty_scene()
    scene = bpy.context.scene
    bpy.ops.mesh.primitive_grid_add(size=30, x_subdivisions=64, y_subdivisions=64)
    ground = bpy.context.active_object
    ground.name = "Ground"
    tex = bpy.data.textures.new("BOB_GroundNoise", "CLOUDS")
    tex.noise_scale = 6.0
    mod = ground.modifiers.new("Displace", "DISPLACE")
    mod.texture = tex
    mod.strength = 1.6
    ground_mat = bpy.data.materials.new("M_Ground")
    ground_mat.use_nodes = True
    ground_mat.node_tree.nodes["Principled BSDF"].inputs["Base Color"].default_value = \
        (0.16, 0.22, 0.10, 1.0)
    ground.data.materials.append(ground_mat)

    props, cached = [], sorted(f for f in os.listdir(G3B_GEN) if f.endswith("_b_tex.glb")) \
        if os.path.isdir(G3B_GEN) else []
    for i, name in enumerate(cached[:3]):
        try:
            obj = gen_assets.import_glb(os.path.join(G3B_GEN, name), name=f"Prop{i}")
        except Exception:
            continue
        size = max(obj.dimensions)
        if size > 0:
            obj.scale = (1.5 / size,) * 3
        obj.location = (-3.0 + 3.0 * i, 1.0 + 0.8 * i, 0.6)
        props.append(obj)
    while len(props) < 3:
        i = len(props)
        bpy.ops.mesh.primitive_ico_sphere_add(subdivisions=3, radius=0.8,
                                             location=(-3.0 + 3.0 * i, 1.0 + 0.8 * i, 0.6))
        obj = bpy.context.active_object
        obj.name = f"Prop{i}"
        mat = bpy.data.materials.new(f"M_Prop{i}")
        mat.use_nodes = True
        mat.node_tree.nodes["Principled BSDF"].inputs["Base Color"].default_value = \
            (0.35, 0.32, 0.30, 1.0)
        obj.data.materials.append(mat)
        props.append(obj)

    cam_data = bpy.data.cameras.new("Camera")
    cam_data.lens = 50.0
    cam = bpy.data.objects.new("Camera", cam_data)
    scene.collection.objects.link(cam)
    cam.location = (0.0, -9.0, 2.6)
    cam.rotation_euler = (math.radians(82), 0.0, 0.0)
    scene.camera = cam
    _sun_and_world(scene)
    bpy.context.view_layer.update()
    return {"camera": cam, "ground": ground, "props": props}


def build_back_facing_object():
    """A ground-truth object whose BACK cannot be guessed from the front.

    A plain block from the front; from behind, a deep hemispherical cavity and a tall fin. That is
    the whole test: a single-view model has to invent this half, and a multi-view model is being
    handed it.
    """
    empty_scene()
    scene = bpy.context.scene
    bpy.ops.mesh.primitive_cube_add(size=1.0, location=(0, 0, 0))
    block = bpy.context.active_object
    block.name = "GroundTruth"
    block.scale = (0.6, 0.5, 0.8)
    bpy.ops.object.transform_apply(scale=True)

    bpy.ops.mesh.primitive_ico_sphere_add(subdivisions=4, radius=0.34, location=(0, 0.28, 0.05))
    cavity = bpy.context.active_object
    bpy.ops.mesh.primitive_cube_add(size=1.0, location=(0, 0.42, 0.62))
    fin = bpy.context.active_object
    fin.scale = (0.34, 0.09, 0.42)
    bpy.ops.object.transform_apply(scale=True)

    bool_cut = block.modifiers.new("Cavity", "BOOLEAN")
    bool_cut.operation = "DIFFERENCE"
    bool_cut.object = cavity
    bool_add = block.modifiers.new("Fin", "BOOLEAN")
    bool_add.operation = "UNION"
    bool_add.object = fin
    bpy.context.view_layer.objects.active = block
    bpy.ops.object.modifier_apply(modifier="Cavity")
    bpy.ops.object.modifier_apply(modifier="Fin")
    bpy.data.objects.remove(cavity, do_unlink=True)
    bpy.data.objects.remove(fin, do_unlink=True)

    mat = bpy.data.materials.new("M_GroundTruth")
    mat.use_nodes = True
    principled = mat.node_tree.nodes["Principled BSDF"]
    principled.inputs["Base Color"].default_value = (0.55, 0.50, 0.45, 1.0)
    principled.inputs["Roughness"].default_value = 0.7
    block.data.materials.append(mat)
    _sun_and_world(scene, strength=4.0)
    bpy.context.view_layer.update()
    return block


def paint_target():
    """A UV'd mesh to paint: a cached generated asset when there is one, else a UV-sphere rock."""
    empty_scene()
    cached = sorted(f for f in os.listdir(G3B_GEN) if f.endswith("_b_tex.glb")) \
        if os.path.isdir(G3B_GEN) else []
    for name in cached:
        try:
            obj = gen_assets.import_glb(os.path.join(G3B_GEN, name), name="PaintTarget")
        except Exception:
            continue
        gen_assets.weld(obj)
        if obj.data.uv_layers.active is not None:
            _sun_and_world(bpy.context.scene)
            bpy.context.view_layer.update()
            return obj, name
    bpy.ops.mesh.primitive_ico_sphere_add(subdivisions=4, radius=1.0)
    obj = bpy.context.active_object
    obj.name = "PaintTarget"
    for vert in obj.data.vertices:
        scale = 1.0 + 0.12 * math.sin(vert.co.x * 6.0) * math.cos(vert.co.y * 5.0)
        vert.co *= scale
    gen_assets.smart_uv(obj)
    mat = bpy.data.materials.new("M_PaintTarget")
    mat.use_nodes = True
    mat.node_tree.nodes["Principled BSDF"].inputs["Base Color"].default_value = (0.5, 0.5, 0.5, 1)
    obj.data.materials.append(mat)
    _sun_and_world(bpy.context.scene)
    bpy.context.view_layer.update()
    return obj, "primitive"


# -- A gate-only depth probe ---------------------------------------------------------------------
# Not a shipped graph: it exists to MEASURE, by running the same Depth Anything V2 the estimated
# route uses over an arbitrary image. It still goes through `comfy.check`, so it cannot smuggle in a
# cloud node or a missing model any more than a shipped graph can.
DEPTH_PROBE = {
    "1": {"class_type": "LoadImage", "inputs": {"image": "example.png"},
          "_meta": {"title": "BOB_IMAGE"}},
    "2": {"class_type": "DepthAnythingV2Preprocessor",
          "inputs": {"image": ["1", 0], "ckpt_name": "depth_anything_v2_vitl.pth",
                     "resolution": 1024},
          "_meta": {"title": "BOB_DEPTH_EST"}},
    "3": {"class_type": "SaveImage", "inputs": {"images": ["2", 0],
                                                "filename_prefix": "bob/depth_probe"},
          "_meta": {"title": "BOB_OUT"}},
}
NORMAL_PROBE = {
    "1": {"class_type": "LoadImage", "inputs": {"image": "example.png"},
          "_meta": {"title": "BOB_IMAGE"}},
    "2": {"class_type": "BAE-NormalMapPreprocessor",
          "inputs": {"image": ["1", 0], "resolution": 1024},
          "_meta": {"title": "BOB_NORMAL_EST"}},
    "3": {"class_type": "SaveImage", "inputs": {"images": ["2", 0],
                                                "filename_prefix": "bob/normal_probe"},
          "_meta": {"title": "BOB_OUT"}},
}


def probe_image(graph, image_path, out_path, label):
    """Run a one-node estimator over an image and write the result. Cached."""
    if os.path.isfile(out_path):
        return out_path
    values = {"BOB_IMAGE": {"image": comfy.upload_image(image_path, subfolder="bob")}}
    png, _info = comfy.generate_image((graph, {"runtime_inputs": ["BOB_IMAGE.image"]}), values,
                                      timeout=600, required_titles=("BOB_OUT",))
    with open(out_path, "wb") as fh:
        fh.write(png)
    note(f"{label} estimated", os.path.basename(out_path))
    return out_path


# -- Part A: the look-dev stylise family
# ----------------------------------------------------------------------------
def normal_convention(args):
    """The normal channel convention, checked on a sphere where the right answer is known.

    A sphere is the one shape whose normal map is unambiguous: red has to rise left to right, green
    bottom to top, and blue has to saturate in the middle where the surface faces the camera. This
    is a better test than correlating against NormalBAE, which was measured to pick the
    WRONG sign on a scene whose channels barely vary (a mostly flat ground gave an argmin that would
    have encoded a front-facing surface as black).
    """
    empty_scene()
    bpy.ops.mesh.primitive_ico_sphere_add(subdivisions=5, radius=1.0)
    sphere = bpy.context.active_object
    bpy.ops.object.shade_smooth()
    cam = bpy.data.objects.new("cam", bpy.data.cameras.new("cam"))
    bpy.context.scene.collection.objects.link(cam)
    cam.location = (0, -5, 0)
    cam.rotation_euler = (math.radians(90), 0, 0)
    bpy.context.scene.camera = cam
    _sun_and_world(bpy.context.scene)
    bpy.context.view_layer.update()
    shot = gen_views.render_passes(GEN, "sphere", camera=cam, objects=[sphere], resolution=256,
                                  samples=8, engine="BLENDER_EEVEE")
    normal = read_rgb(shot["normal"])
    depth = read_grey(shot["depth"])
    mid_y, mid_x = normal.shape[0] // 2, normal.shape[1] // 2
    # Along the centre row and column of the SPHERE, found from the depth pass. Sampling the frame's
    # extremes instead reads the background, which is exactly the flat value the test looks for.
    row = np.nonzero(depth[mid_y] > 8)[0]
    col = np.nonzero(depth[:, mid_x] > 8)[0]
    inset = max(2, len(row) // 12)
    left = normal[mid_y, row.min() + inset, 0]
    right = normal[mid_y, row.max() - inset, 0]
    top = normal[col.min() + inset, mid_x, 1]
    bottom = normal[col.max() - inset, mid_x, 1]
    centre = normal[mid_y, mid_x]
    note("normal convention on a sphere",
         f"red left {left:.0f} right {right:.0f}, green top {top:.0f} bottom {bottom:.0f}, "
         f"centre {centre.round(0).tolist()}")
    check("red rises to the right", right > left + 60, f"{left:.0f} to {right:.0f}")
    check("green rises upward", top > bottom + 60, f"{bottom:.0f} to {top:.0f}")
    check("blue saturates where the surface faces the camera", centre[2] > 240,
          f"centre blue {centre[2]:.0f}")
    check("a front-facing surface is neutral in red and green",
          abs(centre[0] - 128) < 6 and abs(centre[1] - 128) < 6,
          f"centre {centre[:2].round(0).tolist()}")
    # And the depth bytes have to BE the depth: an sRGB curve on the ramp is a monotonic lie. The
    # sphere's nearest point is exactly one radius in front of its centre, so the answer is known.
    near, far = shot["near"], shot["far"]
    expected = (far - 4.0) / (far - near) * 255.0
    got = float(depth.max())
    note("depth linearity", f"nearest surface byte {got:.0f}, analytic {expected:.0f} "
                            f"(range {near:.2f} to {far:.2f} m)")
    check("the depth pass is linear in metres, not display encoded", abs(got - expected) < 4,
          f"{got:.0f} against {expected:.0f}; sRGB encoding would read "
          f"{(((expected / 255) ** (1 / 2.4) * 1.055 - 0.055) * 255):.0f}")


def part_a(args, reachable):
    section("A. The look-dev stylise family: a Bob render, stylised under real passes against estimated ones")
    normal_convention(args)
    scene = build_render_scene()
    started = time.time()
    # transparent=False on purpose: the look-dev stylise family makes a PITCH frame, so the sky
    # belongs in it, and a silhouette test against an empty background would be trivially passed by
    # black staying black.
    shot = gen_views.render_passes(GEN, "scene", camera=scene["camera"], resolution=RESOLUTION,
                                  samples=48, engine="BLENDER_EEVEE", transparent=False)
    render_seconds = time.time() - started
    note("render, beauty plus both passes", f"{render_seconds:.1f} s at {RESOLUTION} square")
    for role in ("beauty", "depth", "normal"):
        check(f"the {role} pass was written", os.path.isfile(shot[role]),
              os.path.basename(shot[role]))

    depth = read_grey(shot["depth"])
    normal = read_rgb(shot["normal"])
    subject = depth > 2.0
    note("depth pass", f"range {depth.min():.0f}..{depth.max():.0f} of 255, "
                       f"geometry over {subject.mean() * 100:.1f}% of the frame, "
                       f"{shot['near']:.2f} to {shot['far']:.2f} m")
    check("the depth pass carries real range inside the geometry", depth[subject].std() > 8.0,
          f"in-geometry std {depth[subject].std():.1f} of 255")

    flat = normal[~subject]
    if len(flat):
        check("background normals are flat, not black",
              abs(flat[:, 2].mean() - 255) < 6 and abs(flat[:, 0].mean() - 128) < 8,
              f"mean {flat.mean(axis=0).round(1)}")

    if not reachable:
        print("[SKIP] no ComfyUI server: the stylise half of part A needs one")
        return
    comfy.check(comfy.load_workflow("stylize_render")[0],
                runtime_inputs=comfy.load_workflow("stylize_render")[1]["runtime_inputs"])

    # NormalBAE on the same frame, reported rather than trusted: the union ControlNet's normal head
    # was trained on this estimator's output, so the agreement is worth knowing, but the convention
    # itself is settled analytically on the sphere above.
    bae = probe_image(NORMAL_PROBE, shot["beauty"], os.path.join(GEN, "scene_normal_bae.png"),
                      "normal")
    est_normal = resize_to(read_rgb(bae), RESOLUTION)
    per_channel = []
    for i, name in enumerate("xyz"):
        ours = normal[subject][:, i].astype("float64")
        theirs = est_normal[subject][:, i].astype("float64")
        r = float(np.corrcoef(ours, theirs)[0, 1]) if ours.std() > 1e-6 else float("nan")
        per_channel.append(f"{name}: mean {ours.mean():.0f} against {theirs.mean():.0f}, r {r:+.2f}")
    note("Blender's normal pass against NormalBAE, per channel", "; ".join(per_channel))
    note("mean absolute difference against NormalBAE",
         f"{np.abs(normal[subject] - est_normal[subject]).mean():.1f} of 255, which is the size of "
         f"the estimator's own error on a surface Blender knows exactly")

    truth = depth  # near-bright, the same convention Depth Anything V2 returns
    results = {}
    # Two denoise levels, because the whole question is what the hints buy and a low denoise answers
    # it badly: at 0.55 the real render still dominates the result, so a better hint has little left
    # to decide. 0.75 is the regime where the ControlNet is carrying the structure.
    for denoise in (0.55, 0.75):
        for route, kwargs in (("passes", {"depth": shot["depth"], "normal": shot["normal"]}),
                              ("estimated", {})):
            label = f"{route}_d{int(denoise * 100)}"
            target = os.path.join(GEN, f"scene_styled_{label}.png")
            if args.fresh and os.path.isfile(target):
                os.remove(target)
            if os.path.isfile(target):
                stamp = _stamp(target)
                info = {"path": target, "seconds": stamp.get("seconds", 0.0), "cached": True}
                vram = stamp.get("vram", {})
            else:
                with Vram() as sampler:
                    info = comfy.stylize_render(shot["beauty"], target, STYLE_PROMPT, seed=SEED,
                                               denoise=denoise, size=RESOLUTION, **kwargs)
                vram = sampler.report()
                _stamp(target, {"seconds": info["seconds"], "vram": vram})
            styled = read_rgb(info["path"])
            source = read_rgb(shot["beauty"])
            est = resize_to(read_grey(probe_image(DEPTH_PROBE, info["path"],
                                                 os.path.join(GEN, f"scene_depth_{label}.png"),
                                                 f"stylised {label}")), RESOLUTION)
            results[label] = {"info": info, "vram": vram, "route": route, "denoise": denoise,
                              "agreement": affine_agreement(truth[subject], est[subject]),
                              "silhouette": silhouette_iou(subject,
                                                           area_matched_mask(est, subject)),
                              "edges": edge_iou(truth, est),
                              # Inside the geometry, because a frame with a sky in it is half
                              # untouched background and a whole-image mean would report the sky's
                              # stability as the restyle's timidity.
                              "changed": float(np.abs(styled[subject] - source[subject]).mean())}

    baseline = resize_to(read_grey(probe_image(DEPTH_PROBE, shot["beauty"],
                                              os.path.join(GEN, "scene_depth_source.png"),
                                              "source")), RESOLUTION)
    base_agree = affine_agreement(truth[subject], baseline[subject])
    note("Depth Anything V2 against Blender's true depth, on the SOURCE frame",
         f"r {base_agree['r']:.4f}, MAE {base_agree['mae']:.4f} of range. This is the estimator's "
         f"own error before any stylising, and the reason a real pass is not the same input.")

    print()
    print("| route | denoise | wall s | peak MiB | rise MiB | silhouette IoU | edge IoU "
          "| depth r | depth MAE | changed |")
    print("|---|---|---|---|---|---|---|---|---|---|")
    for label, res in results.items():
        vram = res["vram"]
        print(f"| {res['route']} | {res['denoise']:.2f} | {res['info']['seconds']:.1f} | "
              f"{vram.get('comfy_peak', 0)} | {vram.get('rise', 0)} | {res['silhouette']:.4f} | "
              f"{res['edges']:.4f} | {res['agreement']['r']:.4f} | {res['agreement']['mae']:.4f} | "
              f"{res['changed']:.1f} |")

    for label, res in results.items():
        check(f"the {label} run returned a stylised frame that changed the render",
              res["changed"] > 6.0, f"mean abs difference {res['changed']:.1f} of 255")
        check(f"the {label} run preserved the silhouette", res["silhouette"] > 0.9,
              f"IoU {res['silhouette']:.4f}")

    source_edges = edge_iou(truth, baseline)
    note("edge IoU of the SOURCE frame's estimated depth against the true depth",
         f"{source_edges:.4f}, the estimator's own boundary error before any stylising")
    for denoise in (0.55, 0.75):
        real, est_run = results[f"passes_d{int(denoise * 100)}"], \
            results[f"estimated_d{int(denoise * 100)}"]
        note(f"real passes against estimated at denoise {denoise:.2f}",
             f"depth r {real['agreement']['r']:.4f} against {est_run['agreement']['r']:.4f} "
             f"({real['agreement']['r'] - est_run['agreement']['r']:+.4f}); silhouette IoU "
             f"{real['silhouette']:.4f} against {est_run['silhouette']:.4f}; edge IoU "
             f"{real['edges']:.4f} against {est_run['edges']:.4f}; wall "
             f"{real['info']['seconds']:.1f} s against {est_run['info']['seconds']:.1f} s")

    with open(os.path.join(OUT, "part_a.json"), "w") as fh:
        json.dump({k: {kk: vv for kk, vv in v.items() if kk != "info"} for k, v in results.items()},
                  fh, indent=2, sort_keys=True, default=str)


# -- Part B: the mesh-texturing family stylised
# --------------------------------------------------------------------
def part_b(args, reachable):
    section("B. The mesh-texturing family stylised: a turntable painted through mesh_paint_views, with seam and drift measured")
    obj, source = paint_target()
    note("paint target", f"{obj.name} from {source}, {gen_assets.face_count(obj)} faces")
    views_dir = os.path.join(GEN, "views")
    started = time.time()
    views = gen_views.turntable_views(obj, views_dir, count=args.views, resolution=RESOLUTION,
                                     samples=32, engine="BLENDER_EEVEE")
    note("turntable render", f"{len(views)} views with depth and normal in "
                             f"{time.time() - started:.1f} s")
    check("every view carries a beauty, a depth and a normal",
          all(os.path.isfile(v[k]) for v in views for k in ("beauty", "depth", "normal")),
          f"{len(views)} views")

    gbuf = gen_paint.uv_gbuffer(obj, size=1024)
    check("the mesh rasterises into a UV g-buffer", gbuf is not None
          and bool(gbuf["mask"].any()),
          f"chart coverage {gbuf['mask'].mean():.3f}" if gbuf else "no UV layer")

    if not reachable:
        print("[SKIP] no ComfyUI server: the restyle half of part B needs one")
        return

    loras = comfy.combo_options("LoraLoader", "lora_name")
    lora = next((name for name in loras if "Hyperreal" in name or "detailer" in name),
                loras[0] if loras else None)
    note("style LoRA", lora or "none installed")

    runs = {}
    for label, use_lora in (("no_lora", None), ("lora", lora)):
        out_dir = os.path.join(GEN, "styled_" + label)
        cached = sorted(f for f in os.listdir(out_dir) if f.endswith("_styled.png")) \
            if os.path.isdir(out_dir) else []
        if len(cached) == len(views) and not args.fresh:
            images = [os.path.join(out_dir, f) for f in cached]
            stamp = _stamp(out_dir)
            painted = {"images": images, "cached": True,
                       "total_seconds": stamp.get("total_seconds", 0.0),
                       "vram": stamp.get("vram", {})}
        else:
            with Vram() as sampler:
                painted = comfy.paint_views(views, out_dir, PAINT_PROMPT, seed=SEED,
                                          denoise=comfy.PAINT_DENOISE, size=RESOLUTION,
                                          lora=use_lora, lora_strength=1.0)
            painted["vram"] = sampler.report()
            _stamp(out_dir, {"total_seconds": painted["total_seconds"],
                             "vram": painted["vram"]})
        runs[label] = painted
        note(f"`mesh_paint_views` restyle, {label}",
             f"{len(painted['images'])} views in {painted.get('total_seconds', 0):.1f} s, peak "
             f"{painted.get('vram', {}).get('comfy_peak', 0)} MiB"
             + (", cached" if painted.get("cached") else ""))

    front_plain = read_rgb(runs["no_lora"]["images"][0])
    front_lora = read_rgb(runs["lora"]["images"][0])
    lora_delta = float(np.abs(front_plain - front_lora).mean())
    note("LoRA at PAINT settings", f"the same seed and prompt with and against the LoRA differ by "
                                  f"{lora_delta:.1f} of 255 on the front view, at denoise "
                                  f"{comfy.PAINT_DENOISE} under both ControlNets and the IPAdapter")
    # The mechanism, separated from the setting. At paint denoise the render dominates by design
    # band, so a small delta there says the route is working as specified rather than that the LoRA
    # is not wired. This pair says whether it is wired at all.
    free_delta = None
    if lora:
        pair = []
        for label, kwargs in (("free_none", {"lora": None}),
                              ("free_lora", {"lora": lora, "lora_strength": 1.0})):
            target = os.path.join(GEN, f"lora_probe_{label}.png")
            if not os.path.isfile(target) or args.fresh:
                comfy.stylize_render(views[0]["beauty"], target, PAINT_PROMPT,
                                    depth=views[0]["depth"], normal=views[0]["normal"],
                                    seed=SEED, size=RESOLUTION, denoise=0.75,
                                    workflow="mesh_paint_views", reference=views[0]["beauty"],
                                    **kwargs)
            pair.append(read_rgb(target))
        free_delta = float(np.abs(pair[0] - pair[1]).mean())
        note("LoRA at denoise 0.75, strength 1.0",
             f"{free_delta:.1f} of 255, which is the mechanism rather than the paint setting")
    check("the LoRA reaches the sampler at all", lora is None or (free_delta or 0) > 2.0,
          f"denoise 0.75 delta {free_delta if free_delta is None else round(free_delta, 1)}")

    out = gen_paint.paint_object(obj, views, runs["lora"]["images"],
                                os.path.join(GEN, "painted"), "painted", size=1024)
    report = out["report"]
    note("projection bake", f"chart coverage {report['coverage']:.3f}, "
                            f"painted {report['painted'] * 100:.1f}% of chart texels directly from "
                            f"{report['views']} views ({report['ring']} in the ring), "
                            f"{report['unpainted']} texels left for the hole fill")
    print()
    print("| view pair | shared texels | overlap MAD of 255 |")
    print("|---|---|---|")
    for pair in report["pairs"]:
        mad = "n/a" if pair["mad"] is None else f"{pair['mad']:.1f}"
        print(f"| {pair['views'][0]} to {pair['views'][1]} | {pair['texels']} | {mad} |")
    drift = report["drift"]
    drift_mad = "n/a" if drift["mad"] is None else f"{drift['mad']:.1f}"
    print(f"| front to {drift['views'][1]} (180 deg) | {drift['texels']} | {drift_mad} |")

    mads = [p["mad"] for p in report["pairs"] if p["mad"] is not None]
    check("every adjacent view pair overlaps enough to measure",
          all(p["texels"] > 200 for p in report["pairs"]),
          f"smallest overlap {min(p['texels'] for p in report['pairs'])} texels")
    check("the projection bake reached the charts", report["painted"] > 0.9,
          f"{report['painted'] * 100:.1f}% of chart texels painted directly")
    check("a texture came out of it", os.path.isfile(out["maps"]["basecolor"]),
          os.path.basename(out["maps"]["basecolor"]))
    note("VERDICT, cross-view consistency",
         f"adjacent-view seam MAD {min(mads):.1f} to {max(mads):.1f} (mean "
         f"{sum(mads) / len(mads):.1f}) of 255; front against 180 degrees {drift_mad} "
         f"over {drift['texels']} shared texels")
    with open(os.path.join(OUT, "part_b.json"), "w") as fh:
        json.dump({"report": report, "lora": lora, "lora_delta": lora_delta}, fh, indent=2,
                  sort_keys=True, default=str)


# -- Part C: multi-view against single-view ------------------------------------------------------
def part_c(args, reachable):
    section("C. Multi-view geometry against single-view, on a back-facing test")
    truth_obj = build_back_facing_object()
    truth_points = mesh_points(truth_obj, seed=1)
    note("ground truth", f"{gen_assets.face_count(truth_obj)} faces, "
                         f"dimensions {tuple(round(v, 3) for v in truth_obj.dimensions)}")
    # The ceiling, so the IoU figures below can be read. A surface-voxel IoU is strict: two point
    # samples of the SAME mesh do not score 1.0, and without this number 0.25 looks like a failure
    # when it may be most of what is achievable.
    ceiling = shape_agreement(truth_points, mesh_points(truth_obj, seed=99))
    note("self-agreement ceiling (the ground truth against another sample of itself)",
         f"IoU {ceiling['iou']:.4f} whole, {ceiling['iou_back']:.4f} back, "
         f"Chamfer {ceiling['chamfer']:.4f}")
    views_dir = os.path.join(GEN, "mv_views")
    # Four cardinal views at a low elevation, in the order Hunyuan's own sockets name.
    views = gen_views.turntable_views(truth_obj, views_dir, count=4, elevation=10.0,
                                     resolution=RESOLUTION, samples=32, engine="BLENDER_EEVEE",
                                     stem="mv")
    order = [views[0], views[1], views[2], views[3]]  # front, left, back, right
    paths = [v["beauty"] for v in order]
    check("four cardinal views rendered with alpha",
          all(os.path.isfile(p) for p in paths), f"{len(paths)} views")

    if not reachable:
        print("[SKIP] no ComfyUI server: part C needs one")
        return

    candidates = {}
    runs = (("single_view_trellis", "mesh_geom_trellis", None),
            ("multi_view_trellis", "mesh_geom_mv_trellis", None),
            ("multi_view_hunyuan", "mesh_geom_mv", None))
    for label, workflow, _unused in runs:
        target = os.path.join(GEN, f"mv_{label}.glb")
        stamp_path = target + ".json"
        if args.fresh and os.path.isfile(target):
            os.remove(target)
        seconds, vram = 0.0, {}
        # A cached mesh keeps its timing and VRAM beside it, so a re-measured table is not a table
        # of zeros: these numbers go into the plan and have to survive a rerun.
        if os.path.isfile(target) and os.path.isfile(stamp_path):
            with open(stamp_path) as fh:
                stamp = json.load(fh) or {}
            seconds, vram = stamp.get("seconds", 0.0), stamp.get("vram", {})
        if not os.path.isfile(target):
            try:
                with Vram() as sampler:
                    if workflow == "mesh_geom_trellis":
                        info = comfy.mesh_geometry(paths[0], target, seed=SEED, remesh=True)
                    elif workflow == "mesh_geom_mv_trellis":
                        info = comfy.mesh_geom_mv_trellis(paths, target, seed=SEED, remesh=True)
                    else:
                        info = comfy.mesh_geom_mv(paths, target, seed=SEED)
                vram = sampler.report()
                seconds = info["seconds"]
                with open(stamp_path, "w") as fh:
                    json.dump({"seconds": seconds, "vram": vram}, fh, indent=2, sort_keys=True)
            except comfy.ComfyError as exc:
                check(f"{label} generated a mesh", False, str(exc)[:160])
                continue
        candidates[label] = {"path": target, "seconds": seconds, "vram": vram}

    print()
    print("| route | wall s | peak MiB | faces | turns | IoU whole | IoU back | Chamfer "
          "| Chamfer back |")
    print("|---|---|---|---|---|---|---|---|---|")
    scores = {}
    for label, entry in candidates.items():
        empty_scene()
        obj = gen_assets.import_glb(entry["path"], name=label)
        gen_assets.weld(obj)
        points = mesh_points(obj, seed=2)
        agree = shape_agreement(truth_points, points)
        scores[label] = dict(agree, faces=gen_assets.face_count(obj),
                             seconds=entry["seconds"],
                             peak=entry["vram"].get("comfy_peak", 0))
        back = "n/a" if agree["iou_back"] is None else f"{agree['iou_back']:.4f}"
        cback = "n/a" if agree["chamfer_back"] is None else f"{agree['chamfer_back']:.4f}"
        print(f"| {label} | {entry['seconds']:.1f} | {entry['vram'].get('comfy_peak', 0)} | "
              f"{scores[label]['faces']} | {agree['turns']} | {agree['iou']:.4f} | {back} | "
              f"{agree['chamfer']:.4f} | {cback} |")

    single = scores.get("single_view_trellis")
    if single:
        check("the single view really does get the back wrong",
              single["iou_back"] is not None and single["iou_back"] < 0.6,
              f"back-half IoU {single['iou_back']:.4f} against whole-mesh {single['iou']:.4f}")
    for label in ("multi_view_trellis", "multi_view_hunyuan"):
        if label in scores and single:
            gain = scores[label]["iou_back"] - single["iou_back"]
            note(f"{label} against single view",
                 f"back-half IoU {scores[label]['iou_back']:.4f} against "
                 f"{single['iou_back']:.4f} ({gain:+.4f}), Chamfer back "
                 f"{scores[label]['chamfer_back']:.4f} against {single['chamfer_back']:.4f}")
    if {"multi_view_trellis", "multi_view_hunyuan"} <= set(scores):
        t, h = scores["multi_view_trellis"], scores["multi_view_hunyuan"]
        note("VERDICT, mesh_geom_mv Hunyuan against Trellis2MultiViewImageToShape",
             f"back-half IoU {h['iou_back']:.4f} against {t['iou_back']:.4f}, whole "
             f"{h['iou']:.4f} against {t['iou']:.4f}, wall clock {h['seconds']:.1f} s against "
             f"{t['seconds']:.1f} s")
    check("at least one multi-view route beat the single view on the back half",
          bool(single) and any(scores[k]["iou_back"] > single["iou_back"]
                               for k in scores if k != "single_view_trellis"),
          "; ".join(f"{k} {scores[k]['iou_back']:.4f}" for k in scores))
    with open(os.path.join(OUT, "part_c.json"), "w") as fh:
        json.dump({"ceiling": ceiling, "routes": scores}, fh, indent=2, sort_keys=True, default=str)


# -- Part D: the Advanced-panel button ------------------------------------------------------------
ADDON = "bl_ext.user_default.bob_blender_tools"


def part_d(args, reachable):
    section("D. The Advanced-panel button, through the real operator and the real job queue")
    # Scene FIRST, addon second: `read_factory_settings` re-reads preferences from the factory
    # defaults, which disables an addon enabled at runtime and takes `Scene.bbt_stylise` with it.
    scene = build_render_scene()["camera"] and bpy.context.scene
    try:
        bpy.ops.preferences.addon_enable(module=ADDON)
    except (RuntimeError, KeyError) as exc:
        print(f"[SKIP] extension not installed for this Blender ({exc})")
        return
    # The ADDON's comfy_jobs, not this script's: the extension is imported as
    # `bl_ext.user_default.bob_blender_tools`, so importing `bob_blender_tools.core.comfy_jobs` by
    # path gives a SECOND module object with its own registry, and the operator's job would be
    # invisible here. A real double-import, and exactly the kind of thing a gate exists to catch.
    comfy_jobs = importlib.import_module(ADDON + ".core.comfy_jobs")

    props = scene.bbt_stylise
    props.prompt = STYLE_PROMPT
    props.denoise = 0.6
    props.samples = 24
    scene.render.resolution_x = scene.render.resolution_y = 768
    if not reachable:
        print("[SKIP] no ComfyUI server: the operator would report 'not connected'")
        return

    comfy_jobs.max_tick_seconds(reset=True)
    started = time.time()
    result = bpy.ops.bob_blender_tools.comfy_stylise()
    render_and_submit = time.time() - started
    check("the operator finished and queued a job", result == {"FINISHED"}, str(result))
    check("exactly one job is in flight", len(comfy_jobs.active()) == 1,
          f"{len(comfy_jobs.active())} active")
    note("main thread cost of the press", f"{render_and_submit:.2f} s, which is the RENDER: the "
                                          f"stylise itself is on the worker")

    # Drive the tick the way Blender's timer does, and measure what it costs the main thread.
    ticks, deadline = 0, time.time() + 600
    while comfy_jobs.active() and time.time() < deadline:
        comfy_jobs.tick()
        ticks += 1
        time.sleep(0.01)
    comfy_jobs.tick()
    jobs = comfy_jobs.jobs()
    job = jobs[-1] if jobs else None
    check("the job finished without an error", job is not None and job.state == "done",
          f"state {getattr(job, 'state', 'none')}, {getattr(job, 'error', None)}")
    note("worker job", f"{getattr(job, 'seconds', 0):.1f} s over {ticks} main-thread ticks")
    note("longest main-thread tick while it ran",
         f"{comfy_jobs.max_tick_seconds() * 1000:.2f} ms")
    check("the tick stays under one frame at 60 Hz", comfy_jobs.max_tick_seconds() < 0.016,
          f"{comfy_jobs.max_tick_seconds() * 1000:.2f} ms")
    if job is not None and job.result:
        check("the stylised frame is on disk", os.path.isfile(job.result["path"]),
              os.path.basename(job.result["path"]))
        note("it went through the passes route", job.result.get("hints"))
    # And the render it did must not have left the scene on a pass material or a Raw view transform.
    check("the scene came back as it was", scene.view_layers[0].material_override is None
          and scene.view_settings.view_transform != "Raw"
          and not any(m.name.startswith("BOB_Pass") for m in bpy.data.materials),
          f"view transform {scene.view_settings.view_transform}, "
          f"override {scene.view_layers[0].material_override}")


# -- main ----------------------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--part", default="a,b,c,d")
    parser.add_argument("--views", type=int, default=6)
    parser.add_argument("--fresh", action="store_true")
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    args = parser.parse_args(argv)

    os.makedirs(GEN, exist_ok=True)
    ok, detail = comfy.reachable()
    note("ComfyUI", detail if ok else f"not reachable ({detail[:70]})")
    parts = {p.strip() for p in args.part.split(",") if p.strip()}
    if "a" in parts:
        part_a(args, ok)
    if "b" in parts:
        part_b(args, ok)
    if "c" in parts:
        part_c(args, ok)
    if "d" in parts:
        part_d(args, ok)

    section("Summary")
    sys.exit(GATE.exit_code())


if __name__ == "__main__":
    main()
