"""Projection painting: N stylised views back into one UV texture, and the numbers that say so.

The Blender half of the `mesh_paint_views` style-control paint route (docs/COMFYUI.md family 3, R19/R20). ComfyUI
restyles each turntable view; everything here is Bob's, because Blender already rasterises and
projects better than a bundled CUDA rasteriser would, which is R20's whole argument.

Three steps, all numpy:

1. `uv_gbuffer` rasterises the mesh in UV space and interpolates a world POSITION and a world
   NORMAL per texel. That is the same triangle raster `gen_assets.uv_counts` uses to measure
   coverage, carrying data instead of counting hits, so the texels this writes are exactly the
   texels a chart-masked statistic reads.
2. `project_views` pushes every texel through each view's camera, keeps the ones that view could
   actually see, and weights them by how face-on the surface was to it.
3. `blend_views` accumulates the weighted samples and fills whatever no view saw.

**Visibility is a z-buffer over the texels themselves, not a ray cast.** Casting a ray per texel
would be a million `scene.ray_cast` calls; scattering the texels' own camera depths into a coarse
buffer with `np.minimum.at` and rejecting anything behind the nearest hit costs one pass and is
correct wherever texel density reaches pixel density, which is the case whenever the texture is not
much smaller than the render.

**Why the drift number matters more than a screenshot.** Per-view SDXL img2img has no cross-view
consistency (R20): the front and the back are two independent renders of the same prompt. The
honest gate is therefore not "does one angle look right" but how far apart two views land where
they overlap, and how far the front view has drifted from the same texels seen from 180 degrees.
Both come out of `project_views` as numbers in 0-255.
"""

import math
import os

import bpy
from mathutils import Matrix, Vector

try:
    from . import comfy_maps, gen_views
except ImportError:  # `core` itself on sys.path (the venv / headless route)
    import comfy_maps
    import gen_views

# How sharply a face-on view outranks a grazing one. 3.0 is firmer than a cosine lobe: a texel seen
# at 20 degrees carries a tenth of the weight of one seen head-on, which is what keeps a stretched
# grazing sample from smearing across the texels a later view sees properly.
FACING_POWER = 3.0

# Below this the sample is discarded rather than blended: at 25 degrees off the silhouette a
# projected texel is several pixels of the same colour and it is not information.
FACING_FLOOR = 0.15

# Depth test tolerance, as a fraction of the view's own depth span. Generous on purpose: the
# z-buffer is built from the texels themselves, so a texel is being compared against a neighbour's
# depth rather than against a true surface, and a tight tolerance rejects the very texels it should
# keep.
DEPTH_TOLERANCE = 0.02

# Texel bin size for that z-buffer, in render pixels. 2 keeps the buffer dense (four texels per bin
# at matched resolutions) without letting a foreground surface hide behind its own gaps.
DEPTH_BIN = 2


# -- Step 1: the UV g-buffer --------------------------------------------------------------------
def _corner_normals(mesh):
    """Per-loop world-space-ready normals, using split normals when the mesh carries them."""
    import numpy as np

    count = len(mesh.loops)
    try:
        flat = np.empty(count * 3, dtype="float64")
        mesh.corner_normals.foreach_get("vector", flat)
        return flat.reshape(-1, 3)
    except (AttributeError, RuntimeError, ValueError):
        vert = np.empty(len(mesh.vertices) * 3, dtype="float64")
        mesh.vertices.foreach_get("normal", vert)
        idx = np.empty(count, dtype="int32")
        mesh.loops.foreach_get("vertex_index", idx)
        return vert.reshape(-1, 3)[idx]


def uv_gbuffer(obj, size=1024, uv_name=None):
    """{"position", "normal", "mask"} over a `size` square UV raster, in WORLD space.

    None when the mesh has no UV layer, which is the honest answer: a paint route cannot invent a
    layout, and every mesh Bob paints has been through `Trellis2ProcessMesh` or Smart UV Project.
    """
    import numpy as np

    mesh = obj.data
    layer = mesh.uv_layers.get(uv_name) if uv_name else mesh.uv_layers.active
    if layer is None:
        return None

    uvs = np.empty(len(layer.data) * 2, dtype="float64")
    layer.data.foreach_get("uv", uvs)
    uvs = uvs.reshape(-1, 2)
    verts = np.empty(len(mesh.vertices) * 3, dtype="float64")
    mesh.vertices.foreach_get("co", verts)
    verts = verts.reshape(-1, 3)
    loop_vert = np.empty(len(mesh.loops), dtype="int32")
    mesh.loops.foreach_get("vertex_index", loop_vert)
    normals = _corner_normals(mesh)

    world = np.array(obj.matrix_world, dtype="float64")
    positions_w = verts @ world[:3, :3].T + world[:3, 3]
    # A normal transforms by the inverse transpose, which matters the moment an asset is scaled
    # non-uniformly, and a generated asset is scaled to `height_m` on one axis at a time.
    normal_matrix = np.linalg.inv(world[:3, :3]).T
    normals_w = normals @ normal_matrix.T
    lengths = np.linalg.norm(normals_w, axis=1, keepdims=True)
    normals_w = normals_w / np.where(lengths > 1e-12, lengths, 1.0)

    position = np.zeros((size, size, 3), dtype="float32")
    normal = np.zeros((size, size, 3), dtype="float32")
    mask = np.zeros((size, size), dtype=bool)
    ys, xs = np.mgrid[0:size, 0:size]
    centres = np.stack(((xs + 0.5) / size, (ys + 0.5) / size), axis=-1)

    for poly in mesh.polygons:
        loops = list(poly.loop_indices)
        for i in range(1, len(loops) - 1):
            corner = [loops[0], loops[i], loops[i + 1]]
            tri = uvs[corner] % 1.0
            lo = np.clip((tri.min(axis=0) * size).astype(int) - 1, 0, size - 1)
            hi = np.clip((tri.max(axis=0) * size).astype(int) + 2, 0, size)
            if hi[0] <= lo[0] or hi[1] <= lo[1]:
                continue
            box = centres[lo[1]:hi[1], lo[0]:hi[0]]
            a, b, c = tri
            det = (b[1] - c[1]) * (a[0] - c[0]) + (c[0] - b[0]) * (a[1] - c[1])
            if abs(det) < 1e-12:
                continue
            w0 = ((b[1] - c[1]) * (box[..., 0] - c[0])
                  + (c[0] - b[0]) * (box[..., 1] - c[1])) / det
            w1 = ((c[1] - a[1]) * (box[..., 0] - c[0])
                  + (a[0] - c[0]) * (box[..., 1] - c[1])) / det
            w2 = 1.0 - w0 - w1
            inside = (w0 >= 0) & (w1 >= 0) & (w2 >= 0)
            if not inside.any():
                continue
            bary = np.stack((w0, w1, w2), axis=-1)[inside]
            tri_pos = positions_w[loop_vert[corner]]
            tri_nrm = normals_w[corner]
            sub_pos = position[lo[1]:hi[1], lo[0]:hi[0]]
            sub_nrm = normal[lo[1]:hi[1], lo[0]:hi[0]]
            sub_msk = mask[lo[1]:hi[1], lo[0]:hi[0]]
            sub_pos[inside] = (bary @ tri_pos).astype("float32")
            sub_nrm[inside] = (bary @ tri_nrm).astype("float32")
            sub_msk[inside] = True
    return {"position": position, "normal": normal, "mask": mask}


# -- Step 2: one view's contribution -----------------------------------------------------------
def _sample_bilinear(image, px, py):
    """Bilinear sample of a (h, w, 3) float image at pixel coordinates, edge-clamped."""
    import numpy as np

    h, w = image.shape[:2]
    x = np.clip(px - 0.5, 0, w - 1)
    y = np.clip(py - 0.5, 0, h - 1)
    x0 = np.floor(x).astype("int32")
    y0 = np.floor(y).astype("int32")
    x1 = np.minimum(x0 + 1, w - 1)
    y1 = np.minimum(y0 + 1, h - 1)
    fx = (x - x0)[:, None]
    fy = (y - y0)[:, None]
    top = image[y0, x0] * (1 - fx) + image[y0, x1] * fx
    bottom = image[y1, x0] * (1 - fx) + image[y1, x1] * fx
    return top * (1 - fy) + bottom * fy


def _read_rgb(path):
    import numpy as np

    with open(path, "rb") as fh:
        data = comfy_maps.read_png(fh.read())
    data = np.asarray(data)
    if data.ndim == 2:
        data = np.repeat(data[:, :, None], 3, axis=2)
    return data[:, :, :3].astype("float32")


def view_contribution(gbuf, view, image_path, *, facing_power=FACING_POWER,
                      facing_floor=FACING_FLOOR, depth_bin=DEPTH_BIN,
                      depth_tolerance=DEPTH_TOLERANCE):
    """One view's (colour, weight) over the g-buffer's texels, as flat arrays over the UV mask.

    The weight is `dot(normal, direction to camera) ** facing_power`, zero where the surface faces
    away, where the projection falls outside the frame, or where the texel-space z-buffer says
    something else was in front. Nothing here decides how the views combine; that is `blend_views`.
    """
    import numpy as np

    mask = gbuf["mask"]
    pos = gbuf["position"][mask]
    nrm = gbuf["normal"][mask]
    n_texels = pos.shape[0]
    colour = np.zeros((n_texels, 3), dtype="float32")
    weight = np.zeros(n_texels, dtype="float32")
    if not n_texels:
        return colour, weight

    cam = view["camera"]
    eye = np.array(Matrix(cam["matrix_world"]).translation, dtype="float64")
    to_eye = eye - pos
    dist = np.linalg.norm(to_eye, axis=1, keepdims=True)
    to_eye = to_eye / np.where(dist > 1e-12, dist, 1.0)
    facing = np.einsum("ij,ij->i", nrm.astype("float64"), to_eye)

    px, py, depth = gen_views.project(cam, pos)
    res = cam["resolution"]
    on_screen = (px >= 0) & (px < res) & (py >= 0) & (py < res) & (depth > 1e-6)
    candidate = on_screen & (facing > facing_floor)
    if not candidate.any():
        return colour, weight

    # Texel-space z-buffer: the nearest depth any texel projected into each bin.
    bins = max(1, int(math.ceil(res / float(depth_bin))))
    zbuf = np.full((bins, bins), np.inf, dtype="float64")
    bx = np.clip((px[candidate] / depth_bin).astype("int32"), 0, bins - 1)
    by = np.clip((py[candidate] / depth_bin).astype("int32"), 0, bins - 1)
    np.minimum.at(zbuf, (by, bx), depth[candidate])
    span = max(float(view.get("far", 1.0)) - float(view.get("near", 0.0)), 1e-4)
    visible = np.zeros(n_texels, dtype=bool)
    visible[candidate] = depth[candidate] <= zbuf[by, bx] + span * depth_tolerance

    take = candidate & visible
    if not take.any():
        return colour, weight
    image = _read_rgb(image_path)
    colour[take] = _sample_bilinear(image, px[take], py[take])
    weight[take] = np.power(np.clip(facing[take], 0.0, 1.0), facing_power).astype("float32")
    return colour, weight


# -- Step 3: blending, filling, and the numbers ------------------------------------------------
def _fill_holes(flat_rgb, written, gbuf, passes=24):
    """Spread written texels into unwritten in-chart texels, so no chart texel stays black.

    A texel no view saw is a real hole (a deep crevice, or a face pointing at nothing), and black is
    the one value it must not keep: `gen_assets` folds AO into the albedo and a black patch then
    reads as a hole in the surface rather than an unpainted one.
    """
    import numpy as np

    mask = gbuf["mask"]
    size = mask.shape[0]
    image = np.zeros((size, size, 3), dtype="float32")
    have = np.zeros((size, size), dtype=bool)
    image[mask] = flat_rgb
    have[mask] = written
    todo = mask & ~have
    for _ in range(passes):
        if not todo.any():
            break
        total = np.zeros_like(image)
        count = np.zeros((size, size), dtype="float32")
        for dy, dx in ((0, 1), (0, -1), (1, 0), (-1, 0)):
            shifted = np.roll(image, (dy, dx), axis=(0, 1))
            shifted_have = np.roll(have, (dy, dx), axis=(0, 1))
            total += shifted * shifted_have[..., None]
            count += shifted_have
        grow = todo & (count > 0)
        image[grow] = total[grow] / count[grow][:, None]
        have |= grow
        todo &= ~grow
    return image, have


def blend_views(gbuf, contributions, *, fill=True):
    """Weighted average of every view's contribution, plus the coverage it reached.

    Returns {"basecolor": uint8 (size, size, 3), "coverage", "filled", "unpainted"}.
    """
    import numpy as np

    mask = gbuf["mask"]
    n_texels = int(mask.sum())
    total = np.zeros((n_texels, 3), dtype="float64")
    weights = np.zeros(n_texels, dtype="float64")
    for colour, weight in contributions:
        total += colour * weight[:, None]
        weights += weight
    written = weights > 0
    flat = np.zeros((n_texels, 3), dtype="float32")
    flat[written] = (total[written] / weights[written][:, None]).astype("float32")

    if fill:
        image, have = _fill_holes(flat, written, gbuf)
    else:
        image = np.zeros(mask.shape + (3,), dtype="float32")
        image[mask] = flat
        have = np.zeros(mask.shape, dtype=bool)
        have[mask] = written
    return {"basecolor": np.clip(image, 0, 255).astype("uint8"),
            "coverage": float(mask.mean()),
            "painted": float(written.mean()) if n_texels else 0.0,
            "unpainted": int((mask & ~have).sum())}


def cross_view_report(contributions, *, ring=None, opposite=None):
    """Seam and drift, measured ACROSS the turntable rather than eyeballed on one angle.

    - `pairs`: for each pair of ADJACENT views in the RING, the mean absolute difference (0-255)
      over the texels both of them wrote, plus how many texels that was. This is the seam: two
      neighbouring views disagreeing over shared texels is exactly what a visible seam is made of.
    - `drift`: the same figure for the front view against its 180 degree opposite. Far fewer texels
      (only the silhouette band is seen from both), and the number that says whether the IPAdapter
      reference held the palette or the two halves of the object went their own way.

    `ring` excludes the top and underside views a turntable adds for coverage: they are adjacent to
    everything and to nothing, so folding them into a ring of neighbours would report a number that
    is not a seam.
    """
    import numpy as np

    count = int(ring or len(contributions))
    if count < 2:
        return {"pairs": [], "drift": None}

    def compare(i, j):
        ci, wi = contributions[i]
        cj, wj = contributions[j]
        both = (wi > 0) & (wj > 0)
        shared = int(both.sum())
        if not shared:
            return {"views": [i, j], "texels": 0, "mad": None}
        diff = np.abs(ci[both].astype("float64") - cj[both].astype("float64")).mean()
        return {"views": [i, j], "texels": shared, "mad": float(diff)}

    pairs = [compare(i, (i + 1) % count) for i in range(count)]
    back = count // 2 if opposite is None else int(opposite)
    return {"pairs": pairs, "drift": compare(0, back)}


# -- The whole route ---------------------------------------------------------------------------
def paint_maps(obj, views, images, out_dir, stem, *, size=1024, derive=True):
    """Project `images` (one per view) onto `obj`'s UVs and write a texture set into `out_dir`.

    Returns {"maps": {role: path}, "report": {...}}. Roughness, normal, height and AO come from the
    same numpy derivation track A uses, which is honest for a stylised nature asset: the paint model
    returns a colour, not a PBR set, and metallic is zero on everything this makes.
    """
    gbuf = uv_gbuffer(obj, size=size)
    if gbuf is None:
        raise RuntimeError(f"paint_maps: {obj.name} has no UV layer to project into")
    contributions = [view_contribution(gbuf, view, path) for view, path in zip(views, images)]
    blend = blend_views(gbuf, contributions)
    report = dict(blend)
    report.pop("basecolor", None)
    ring = sum(1 for view in views if view.get("ring", True))
    report.update(cross_view_report(contributions, ring=ring), views=len(views), ring=ring,
                  size=int(size))

    os.makedirs(out_dir, exist_ok=True)
    maps = {}
    if derive:
        for role, array in comfy_maps.derive(blend["basecolor"]).items():
            maps[role] = comfy_maps.write_png(os.path.join(out_dir, f"{stem}_{role}.png"), array)
    else:
        maps["basecolor"] = comfy_maps.write_png(os.path.join(out_dir, f"{stem}_basecolor.png"),
                                                 blend["basecolor"])
    return {"maps": maps, "report": report}


def paint_object(obj, views, images, out_dir, stem, *, size=1024, material_name=None):
    """`paint_maps`, wired onto the object as a material. The whole Blender half of the `mesh_paint_views` route.

    Reuses `gen_assets.apply_baked_material`, so a painted asset carries the same graph shape a
    baked one does and stays a BobShader candidate rather than a special case.
    """
    try:
        from . import gen_assets
    except ImportError:
        import gen_assets

    out = paint_maps(obj, views, images, out_dir, stem, size=size)
    out["material"] = gen_assets.apply_baked_material(obj, out["maps"],
                                                      material_name or f"M_{stem}_Painted").name
    return out
