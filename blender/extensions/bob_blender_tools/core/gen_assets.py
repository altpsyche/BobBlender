"""Pipeline steps 6 to 8: what Blender does to a generated mesh to make it a Bob asset.

ComfyUI generates. Blender retopologises, unwraps, bakes, LODs, scales, and packs (docs/COMFYUI.md
R3). The division is not a preference: a generated mesh is a dense unit-cube-normalised triangle
soup, and every property that makes it usable in a world -- a real height, an origin on the ground,
a face budget, a UV layout something can be baked into, a BobShader -- is added here.

Three of the steps are load-bearing in a way that is easy to miss:

- **Scale.** Every image-to-3D model emits a unit-cube-normalised mesh, so without a per-asset real
  height the scatter looks like a toy set. `height_m` is mandatory in the manifest and this module
  is what enforces it.
- **Normalise on the way OUT.** `Trellis2EncodeMesh` voxelises in unit-cube space, so handing it a
  metre-scale block-out proxy returns a silently BLACK albedo -- no error, just black (measured at
  G0.5: 172 s for a black texture against 10 s for a correct one). `unit_normalise_export` and
  `rescale_to_height` are that round trip, and the headless gate asserts a generated texture is not
  near-constant.
- **Origin at the base.** Scatter places instances on a surface, so an origin at the mesh centre
  buries half of every asset.

Nothing here talks to ComfyUI. `core.comfy` gets a raw GLB into `<pack>/_staging/`; this module
turns it into a finished asset in `<pack>/models/generated/`, and `import_generated` links it into
the `BOB_Assets_<Kind>` collection a scatter layer already instances.
"""

import json
import math
import os
import time

import bpy
import mathutils

from . import assets, materials, proxies

# The generated pack's one biome-shaped manifest. Kinds live INSIDE it (`models: {tree: [...]}`),
# rather than one biome folder per kind, because `assets.list_biomes()` treats every
# `models/<name>/manifest.json` as a biome and a folder per kind would put "tree" and "rock" in the
# biome picker.
GENERATED_BIOME = "generated"

# Scatter-grade defaults. A background rock instanced four thousand times is never deformed, so
# Decimate-collapse plus a UV unwrap is genuinely adequate and quads do not matter (R19).
DEFAULT_FACES = 4000
DEFAULT_LODS = (0.5, 0.15)
DEFAULT_BAKE_SIZE = 1024

# Cycles samples per bake type. Only AO integrates anything, so only AO pays for samples; a normal
# or a colour transfer is a lookup and one sample is the whole answer.
_BAKE_SAMPLES = {"NORMAL": 1, "DIFFUSE": 1, "ROUGHNESS": 1, "AO": 24}

# What a finished asset's baked maps are called, and which Principled socket each drives.
_BAKE_ROLES = (("basecolor", "DIFFUSE", "Base Color", False),
               ("roughness", "ROUGHNESS", "Roughness", True),
               ("normal", "NORMAL", None, True),
               ("ao", "AO", None, True))


# -- Small scene helpers ---------------------------------------------------------------------
def _select_only(objs, active=None):
    # The operator, not a loop over `view_layer.objects`: that collection can still hand back a
    # freed object right after an import removed the glTF root, and deselecting it segfaults or,
    # here, comes back as None.
    bpy.ops.object.select_all(action="DESELECT")
    for obj in objs:
        obj.select_set(True)
    bpy.context.view_layer.objects.active = active or (objs[0] if objs else None)


def _link(obj, collection=None):
    (collection or bpy.context.scene.collection).objects.link(obj)
    return obj


def _unlink(obj):
    for coll in list(obj.users_collection):
        coll.objects.unlink(obj)


def face_count(obj):
    return len(obj.data.polygons)


def bbox_world(obj):
    """(min, max) of the object's world-space bounding box, as two 3-tuples.

    From the vertices, NOT `obj.bound_box`, which is cached and only refreshed on a depsgraph
    evaluation: right after `mesh.transform()` it still describes the mesh as it was, which is how
    an origin-to-base that worked measured as if it had not.
    """
    import numpy as np

    mesh = obj.data
    if not len(mesh.vertices):
        origin = tuple(obj.matrix_world.translation)
        return origin, origin
    co = np.empty(len(mesh.vertices) * 3, dtype=np.float64)
    mesh.vertices.foreach_get("co", co)
    co = co.reshape(-1, 3)
    matrix = np.array(obj.matrix_world)
    world = co @ matrix[:3, :3].T + matrix[:3, 3]
    return tuple(world.min(axis=0)), tuple(world.max(axis=0))


def dimensions(obj):
    lo, hi = bbox_world(obj)
    return tuple(hi[i] - lo[i] for i in range(3))


def boundary_edges(obj):
    """Edges with exactly one adjacent face: nonzero means a genuinely open surface, which is the
    whole reason TRELLIS.2 is primary and the case a watertight-only pipeline breaks on.

    Counted on the MESH's own edges, which is only meaningful after `weld()`. glTF stores UVs and
    normals per vertex, so the importer splits a vertex at every UV seam and every sharp edge, and
    an unwelded import reports a closed boulder as roughly half boundary. Measured here: 233,812
    of 489,781 before welding, 0 after. That artefact is worth knowing about before quoting any
    open-surface figure, including G0.5's.
    """
    counts = {}
    for poly in obj.data.polygons:
        for key in poly.edge_keys:
            counts[key] = counts.get(key, 0) + 1
    return sum(1 for n in counts.values() if n == 1)


def weld(obj, distance=1e-5):
    """Merge coincident vertices, and return how many went. Undoes the glTF importer's per-vertex
    split, which is not cosmetic: an unwelded mesh is topologically shredded, so Decimate cannot
    collapse across a seam and a 4,000-face budget lands at 7,263."""
    before = len(obj.data.vertices)
    _select_only([obj], obj)
    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.mesh.select_all(action="SELECT")
    bpy.ops.mesh.remove_doubles(threshold=distance)
    bpy.ops.object.mode_set(mode="OBJECT")
    return before - len(obj.data.vertices)


def uv_counts(obj, uv_name=None, grid=1024):
    """How many face triangles cover each texel of a `grid` square UV raster, or None with no UVs.

    One rasteriser, two readers: `uv_overlap` divides it and `uv_coverage` thresholds it. The
    obvious cheap version -- collect each face's corner CELLS and count cells more than one face
    touches -- measures adjacency, not overlap, because two neighbouring faces share their corners
    by definition; it reported 0.26 on a layout Smart UV Project had just made clean.
    """
    import numpy as np

    mesh = obj.data
    layer = mesh.uv_layers.get(uv_name) if uv_name else mesh.uv_layers.active
    if layer is None:
        return None
    counts = np.zeros((grid, grid), dtype=np.int32)
    uvs = np.empty(len(layer.data) * 2, dtype=np.float64)
    layer.data.foreach_get("uv", uvs)
    uvs = uvs.reshape(-1, 2)
    ys, xs = np.mgrid[0:grid, 0:grid]
    centres = np.stack(((xs + 0.5) / grid, (ys + 0.5) / grid), axis=-1)
    for poly in mesh.polygons:
        loops = list(poly.loop_indices)
        for i in range(1, len(loops) - 1):
            tri = uvs[[loops[0], loops[i], loops[i + 1]]] % 1.0
            lo = np.clip((tri.min(axis=0) * grid).astype(int) - 1, 0, grid - 1)
            hi = np.clip((tri.max(axis=0) * grid).astype(int) + 2, 0, grid)
            if hi[0] <= lo[0] or hi[1] <= lo[1]:
                continue
            box = centres[lo[1]:hi[1], lo[0]:hi[0]]
            a, b, c = tri
            d = (b[1] - c[1]) * (a[0] - c[0]) + (c[0] - b[0]) * (a[1] - c[1])
            if abs(d) < 1e-12:
                continue
            w0 = ((b[1] - c[1]) * (box[..., 0] - c[0])
                  + (c[0] - b[0]) * (box[..., 1] - c[1])) / d
            w1 = ((c[1] - a[1]) * (box[..., 0] - c[0])
                  + (a[0] - c[0]) * (box[..., 1] - c[1])) / d
            inside = (w0 >= 0) & (w1 >= 0) & (w0 + w1 <= 1)
            counts[lo[1]:hi[1], lo[0]:hi[0]] += inside
    return counts


def uv_overlap(obj, uv_name=None, grid=1024):
    """The fraction of UV area covered more than once: 0.0 is a clean layout."""
    counts = uv_counts(obj, uv_name=uv_name, grid=grid)
    if counts is None:
        return None
    covered = int((counts > 0).sum())
    if not covered:
        return None
    return float(int(counts.sum()) - covered) / covered


def uv_coverage(obj, uv_name=None, grid=1024):
    """A boolean (grid, grid) mask of the texels the UV charts actually reach, or None with no UVs.

    What makes a texture statistic honest. `Trellis2RasterizePBR` inpaints its PBR channels only one
    to three pixels past the chart edge, so on a layout using 13% of the sheet (measured, the G3
    stump) 87% of every map is untouched black and a whole-image mean says more about the packing
    than about the surface.
    """
    counts = uv_counts(obj, uv_name=uv_name, grid=grid)
    return None if counts is None else (counts > 0)


# -- Step 6a: import -------------------------------------------------------------------------
def turn(obj, axis="X", degrees=90.0):
    """Rotate `obj`'s mesh data by `degrees` about a world axis, in place. Data, not the object
    transform, so a later `matrix_world` reset cannot lose it."""
    matrix = mathutils.Matrix.Rotation(math.radians(float(degrees)), 4, axis.upper())
    obj.data.transform(matrix)
    obj.data.update()
    return obj


def import_glb(path, name=None, orient=None):
    """Import a GLB and return ONE joined mesh object, linked to the scene collection.

    A generated GLB is one mesh in practice, but the importer also brings an empty root, and a
    textured export can arrive split by material, so this joins whatever meshes it finds rather
    than assuming. Raises ValueError when the file has no mesh at all, which is what a failed or
    truncated download looks like.

    `orient` is an (axis, degrees) turn applied to the mesh data before anything measures it, which
    is how a file that has been through a `Trellis2ExportTrimesh` write gets put back: see
    `undo_exports`.
    """
    before = set(bpy.data.objects)
    bpy.ops.import_scene.gltf(filepath=str(path))
    fresh = [o for o in bpy.data.objects if o not in before]
    imported = [o for o in fresh if o.type == "MESH"]
    for obj in fresh:
        if obj.type != "MESH":
            bpy.data.objects.remove(obj, do_unlink=True)
    if not imported:
        raise ValueError(f"no mesh in {os.path.basename(str(path))}")
    if len(imported) > 1:
        _select_only(imported, imported[0])
        bpy.ops.object.join()
        imported = [bpy.context.view_layer.objects.active]
    obj = imported[0]
    # The importer parents to its scene root and bakes the Y-up to Z-up conversion into that
    # parent's matrix, so the transform has to be applied before anything measures the object.
    obj.parent = None
    _select_only([obj], obj)
    bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)
    if orient:
        turn(obj, *orient)
    if name:
        obj.name = name
        obj.data.name = name
    return obj


# -- The normalise round trip ------------------------------------------------------------------
def unit_normalise_export(obj, path):
    """Export a COPY of `obj` normalised into the unit cube, and return what the trip back needs.

    This is the mandatory half of track B that G0.5 found the hard way. `Trellis2EncodeMesh`
    voxelises in unit-cube space; a 4.7 m block-out proxy lands entirely outside the grid, the
    encoder sees nothing, and the result is a fully black albedo with no error anywhere.

    Returns {"path", "height_m", "scale", "centre"}: `height_m` is the object's real Z extent, so
    `rescale_to_height` can put the textured mesh back at the size it was authored.
    """
    dims = dimensions(obj)
    longest = max(dims) or 1.0
    copy = obj.copy()
    copy.data = obj.data.copy()
    _link(copy)
    lo, hi = bbox_world(obj)
    centre = tuple((lo[i] + hi[i]) / 2.0 for i in range(3))
    copy.matrix_world = mathutils.Matrix.Identity(4)
    copy.location = tuple(-c / longest for c in centre)
    copy.scale = (1.0 / longest,) * 3
    _select_only([copy], copy)
    bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)
    bpy.ops.export_scene.gltf(filepath=str(path), export_format="GLB", use_selection=True)
    bpy.data.objects.remove(copy, do_unlink=True)
    return {"path": str(path), "height_m": dims[2], "scale": longest, "centre": centre}


# -- The control export, and the one convention it has to get right --------------------------
# Every `Trellis2ExportTrimesh` glb write turns the subject by -90 degrees about X, and the turns
# ACCUMULATE along a chain. Measured at G4c on an asymmetric block-out, per exporter rather than
# assumed, at every hop of the staged chain:
#
#   file                    turns to undo   why
#   the control Bob sent    0               Bob's own glTF export and import round trip is exact
#   W7 output               1               one export
#   W9c output              2               W9c loads W7's file and exports again
#   W9t output              3               and again
#
# The cause is in the exporter's source rather than in any model: `Trellis2ExportTrimesh` converts
# internal Z-up to Y-up when it writes glb or gltf (`verts[1], verts[2] = verts[2], -verts[1]`) while
# `Trellis2LoadMesh` converts NOTHING on the way in, so a graph that loads a mesh and exports one is
# asymmetric by one turn. Hunyuan's `SaveGLB` (W5, W6) adds no turn, which is the other half of G4's
# finding that the two exporters disagree.
#
# Two consequences, and the second one is a bug this constant fixes rather than a new feature:
#
# 1. A control-conditioned asset only "drops into a layout already composed" if the turns are undone,
#    and a footprint gate cannot search for the right one, because an asset that fits only after being
#    turned does not fit.
# 2. On the STAGED chain the dense mesh and the low mesh come from different hops (1 and 2, or 1 and
#    3 once W9t has run), so `bake_high_to_low` has been transferring from a cage rotated 90 or 180
#    degrees away from its target. Nothing errors: a misaligned bake still writes a non-flat normal
#    map, which is why the G3 asset checks passed over it. `prepare_low` now brings both into one
#    frame, and G3b's "the dense mesh bought no measurable normal detail" is a conclusion that has to
#    be re-measured before it is trusted (docs/COMFYUI.md, G4c).
EXPORT_TURN = ("X", 90.0)


def undo_exports(count):
    """The turn that undoes `count` `Trellis2ExportTrimesh` glb writes, or None for zero."""
    count = int(count or 0) % 4
    return None if count == 0 else (EXPORT_TURN[0], EXPORT_TURN[1] * count)


# One export undone: the block-out route's raw mesh, and the constant the G4c gate pins by
# measurement over all 24 axis-aligned rotations.
CONTROL_RETURN_TURN = undo_exports(1)

# How many points Omni's control encoder gets. The wrapper's own default is the proxy's raw
# VERTICES, which for a Bob block-out is a few dozen: a six-sided cone has 13. That is not a point
# cloud, so Bob always names a budget and lets the node area-sample the surface.
CONTROL_POINTS = 8192


def export_control(obj, path, *, points=CONTROL_POINTS):
    """Export a block-out proxy as the control signal a control-conditioned graph (W7) takes.

    The point worth writing down is that this needed NO new exporter. Omni normalises its control
    mesh into the unit cube exactly as `Trellis2EncodeMesh` does, so a metre-scale proxy fails the
    same way and the fix is the same round trip `unit_normalise_export` already owns (the G0.5
    black-albedo trap, same cause, different symptom: a control the model cannot see comes back as
    an unconditioned generation rather than as an error).

    The control the graph consumes is a MESH, not a point-cloud file: `Hy3DOmniPointGenerate` takes
    a `TRIMESH` and samples it, so `Trellis2LoadMesh` reads whatever `unit_normalise_export` wrote
    and `points` decides the density. Returns that dict plus the control metadata.
    """
    info = unit_normalise_export(obj, path)
    info.update(points=int(points), footprint=[round(d, 5) for d in dimensions(obj)],
                footprint_ratio=[round(r, 5) for r in footprint_ratio(obj)])
    return info


def footprint_ratio(obj):
    """The proxy's XY extent as (width, depth, height) over its longest axis: the shape a layout was
    composed around, scale removed. What "drops into a layout" reduces to, and what the G4c gate
    compares a generated asset against."""
    dims = dimensions(obj)
    longest = max(dims) or 1.0
    return tuple(float(d) / float(longest) for d in dims)


def rescale_to_height(obj, height_m):
    """Scale `obj` uniformly so its Z extent is `height_m`, and apply. Returns the factor used."""
    dims = dimensions(obj)
    if dims[2] <= 0 or not height_m:
        return 1.0
    factor = float(height_m) / dims[2]
    obj.scale = tuple(s * factor for s in obj.scale)
    _select_only([obj], obj)
    bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)
    return factor


# -- Step 7: finish --------------------------------------------------------------------------
def origin_to_base(obj):
    """Put the origin at the bottom centre of the bounding box, so a scattered instance sits ON
    the ground rather than half in it."""
    lo, hi = bbox_world(obj)
    target = mathutils.Vector(((lo[0] + hi[0]) / 2.0, (lo[1] + hi[1]) / 2.0, lo[2]))
    offset = target - obj.matrix_world.translation
    obj.data.transform(mathutils.Matrix.Translation(-offset))
    obj.matrix_world.translation = target
    obj.data.update()
    return tuple(target)


def weighted_normals(obj):
    """Shade smooth plus an applied Weighted Normal, which is what stops a decimated soup reading
    as faceted without adding a subdivision it does not deserve."""
    _select_only([obj], obj)
    bpy.ops.object.shade_smooth()
    mod = obj.modifiers.new("BOB_WeightedNormal", "WEIGHTED_NORMAL")
    mod.keep_sharp = True
    bpy.ops.object.modifier_apply(modifier=mod.name)
    return True


def close_pinholes(obj, sides=256):
    """Fill boundary loops of at most `sides` edges, and return how many boundary edges went.

    A `Trellis2ProcessMesh` result is peppered with small holes: the boulder measured here arrives
    with 19,623 boundary edges, which is not an open surface, it is a sieve. They matter because
    Decimate treats every boundary loop as a constraint, so the holes set a hard floor on the face
    count (measured: 12,017 faces against a 4,000 budget, dropping to 6,760 once the pinholes are
    closed).

    `sides` is what keeps this safe for foliage. A leaf's silhouette boundary is one loop of tens
    of thousands of edges, so it is far outside the limit and stays open; a remesh pinhole is a
    handful of edges and gets closed.
    """
    before = boundary_edges(obj)
    _select_only([obj], obj)
    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.mesh.select_all(action="SELECT")
    bpy.ops.mesh.fill_holes(sides=int(sides))
    bpy.ops.object.mode_set(mode="OBJECT")
    return before - boundary_edges(obj)


def decimate_to(obj, faces, passes=3, tolerance=0.1):
    """Collapse-decimate to roughly `faces` triangles. The scatter-grade retopology tier.

    Iterated, because one pass overshoots: Decimate's ratio is a target the collapse cannot always
    reach in a single evaluation, and it is a ratio of the CURRENT face count, so a mesh that lands
    at 1.8x the budget stays there unless it is asked again. Two or three passes converge, and the
    loop exits as soon as it is inside `tolerance`.
    """
    for _ in range(max(1, passes)):
        have = face_count(obj)
        if have <= faces * (1.0 + tolerance):
            break
        _select_only([obj], obj)
        mod = obj.modifiers.new("BOB_Decimate", "DECIMATE")
        mod.decimate_type = "COLLAPSE"
        mod.ratio = max(1e-4, float(faces) / have)
        bpy.ops.object.modifier_apply(modifier=mod.name)
        if face_count(obj) >= have:
            # A pass that removed nothing means Decimate has hit a topological floor, and every
            # further pass costs the same tens of seconds to do the same nothing. Measured on the
            # G3 meshes, that floor is well above a 4,000-face budget, which is most of why the
            # steps 3 and 4 A/B went the way it did.
            break
    return face_count(obj)


def quadriflow_to(obj, faces):
    """Quadriflow remesh to roughly `faces` quads. The hero tier (R19).

    Falls back to Decimate and says so, because Quadriflow refuses non-manifold and open input,
    which is exactly the foliage TRELLIS.2 exists to produce.
    """
    _select_only([obj], obj)
    result = {"CANCELLED"}
    try:
        result = bpy.ops.object.quadriflow_remesh(target_faces=int(faces),
                                                  use_preserve_sharp=False,
                                                  use_preserve_boundary=True)
    except RuntimeError as exc:
        result, reason = {"CANCELLED"}, str(exc)
    else:
        reason = "the mesh must be manifold with consistent face normals"
    # It CANCELS, it does not raise. Blender's Quadriflow reports "the mesh needs to be manifold
    # and have face normals that point in a consistent direction" as a warning and returns
    # CANCELLED, so a try/except alone leaves the mesh at its original half a million faces and
    # the caller never learns. Measured on all five G3 meshes: Quadriflow refused every one.
    if "FINISHED" not in result:
        decimate_to(obj, faces)
        return face_count(obj), f"quadriflow refused this mesh ({reason}); decimated instead"
    return face_count(obj), ""


def smart_uv(obj, angle_limit=math.radians(66.0), island_margin=0.02):
    """Smart UV Project on the whole mesh, replacing whatever UVs it arrived with."""
    _select_only([obj], obj)
    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.mesh.select_all(action="SELECT")
    bpy.ops.uv.smart_project(angle_limit=angle_limit, island_margin=island_margin)
    bpy.ops.object.mode_set(mode="OBJECT")
    return obj.data.uv_layers.active.name if obj.data.uv_layers.active else None


# -- Step 6b: bake dense to low ----------------------------------------------------------------
def has_textures(obj):
    """True when the object carries at least one image texture, i.e. there is a surface worth
    transferring. False for a geometry-only generation, which has no material at all."""
    for slot in obj.material_slots:
        mat = slot.material
        if mat is None or not mat.use_nodes:
            continue
        if any(n.bl_idname == "ShaderNodeTexImage" and n.image for n in mat.node_tree.nodes):
            return True
    return False


def basecolor_image(obj):
    """The image feeding the object's Principled Base Color, or the first image on it. None when
    there is no texture at all, which is what a geometry-only generation looks like."""
    for slot in obj.material_slots:
        mat = slot.material
        if mat is None or not mat.use_nodes:
            continue
        nodes = [n for n in mat.node_tree.nodes
                 if n.bl_idname == "ShaderNodeTexImage" and n.image]
        bsdf = next((n for n in mat.node_tree.nodes
                     if n.bl_idname == "ShaderNodeBsdfPrincipled"), None)
        if bsdf is not None and bsdf.inputs["Base Color"].links:
            src = bsdf.inputs["Base Color"].links[0].from_node
            if src.bl_idname == "ShaderNodeTexImage" and src.image:
                return src.image
        if nodes:
            return nodes[0].image
    return None


# What counts as an opacity channel worth wiring, measured at G3b rather than assumed.
#
# A texel below OPACITY_FLOOR is transparent enough to matter. Wiring then needs BOTH:
#
# - at least OPACITY_MIN_CUT of the in-chart texels below the floor, or the channel is simply
#   "opaque" and the alpha link, the sixth channel and the transparent render method all buy
#   nothing. Measured: every one-shot W9b asset comes back at a mean of 0.998 with 0.00% below the
#   floor, so this is the normal case, not the corner one.
# - an in-chart MEAN of at least OPACITY_MIN_MEAN, i.e. the surface is mostly there. This is the
#   guard the G3b measurements forced: the W9t texture pass returns an alpha whose in-chart mean
#   runs 0.07 to 0.77 with 42% to 93% of the surface below the floor, which is not a cutout, it is
#   an unusable channel -- wiring it made a tree stump 60% transparent and a leaf 93% transparent.
#   Only the one-shot route, where the voxel grid comes from the generation that made the mesh,
#   returns a channel that means what it says.
OPACITY_FLOOR = 0.98
OPACITY_MIN_CUT = 0.005
OPACITY_MIN_MEAN = 0.9


def source_opacity(obj, size=None, grid=1024, force=False):
    """The generated opacity channel of `obj`'s own texture, ready to become an alpha map.

    Returns (array or None, stats). `Trellis2RasterizePBR` writes TRELLIS.2's opacity output into
    the alpha of the base-colour texture it bakes (voxel attribute channel 5, `nodes_unwrap.py`), so
    the data is in the GLB. Two things then hide it, and G3 saw the result rather than the cause:
    the node declares `alphaMode: "OPAQUE"`, which per the glTF spec tells every importer to IGNORE
    that alpha, and Bob's own bake wrote an alpha-less basecolor over it.

    The array comes back at the BAKE size with everything outside the UV charts forced OPAQUE.
    Off-chart texels are only ever reached by bilinear filtering at a chart edge, and there they are
    inpainted black, so leaving them would ring every island with a transparent rim -- the same
    failure the bake margin exists to prevent.

    `stats` is measured INSIDE the charts only, and is returned even when the array is None, so a
    caller can report "present and fully opaque" rather than "absent". `stats["verdict"]` says which
    of the three cases this texture is: `cutout`, `opaque`, or `implausible`.

    `force` wires the channel whatever it says. Only the headless gate uses it, to prove the wiring
    reaches a rendered material rather than asserting that it would.
    """
    import numpy as np

    img = basecolor_image(obj)
    stats = {"source": None, "in_chart": None}
    if img is None or img.channels < 4:
        return None, stats
    width, height = img.size
    px = np.empty(width * height * img.channels, dtype=np.float32)
    img.pixels.foreach_get(px)
    alpha = px.reshape(height, width, img.channels)[..., 3]
    stats["source"] = os.path.basename(img.filepath_raw or img.name)

    mask = uv_coverage(obj, grid=grid)
    if mask is not None and mask.shape != alpha.shape:
        ys = (np.arange(alpha.shape[0]) * mask.shape[0] // alpha.shape[0])
        xs = (np.arange(alpha.shape[1]) * mask.shape[1] // alpha.shape[1])
        mask = mask[np.ix_(ys, xs)]
    inside = alpha if mask is None else alpha[mask]
    if not inside.size:
        return None, stats
    mean = float(inside.mean())
    cut = float((inside < OPACITY_FLOOR).mean())
    stats["in_chart"] = {"min": round(float(inside.min()), 4), "mean": round(mean, 4),
                         "max": round(float(inside.max()), 4),
                         "coverage": round(float(mask.mean()) if mask is not None else 1.0, 4),
                         "below_floor": round(cut, 4)}
    stats["verdict"] = ("opaque" if cut < OPACITY_MIN_CUT
                        else "implausible" if mean < OPACITY_MIN_MEAN else "cutout")
    if stats["verdict"] != "cutout" and not force:
        return None, stats

    out = alpha if mask is None else np.where(mask, alpha, 1.0)
    if size and size != out.shape[0]:
        ys = (np.arange(size) * out.shape[0] // size)
        xs = (np.arange(size) * out.shape[1] // size)
        out = out[np.ix_(ys, xs)]
    return out.astype(np.float32), stats


def _bake_image(name, size, is_data, alpha=False):
    img = bpy.data.images.new(name, size, size, alpha=bool(alpha), float_buffer=False,
                              is_data=is_data)
    return img


def _bake_target_node(mat, img):
    """Add and select an image node, which is how `bpy.ops.object.bake` is told where to write."""
    node = mat.node_tree.nodes.new("ShaderNodeTexImage")
    node.name = "BOB_BakeTarget"
    node.image = img
    node.select = True
    mat.node_tree.nodes.active = node
    return node


def _write_alpha(img, alpha):
    """Set an image's alpha channel from a (size, size) float array, in place.

    Both `Image.pixels` and `uv_counts`' raster are bottom-row-first, so no flip is needed; getting
    that wrong would mirror the cutout vertically, which reads as a plausible-looking wrong result.
    """
    import numpy as np

    width, height = img.size
    if alpha.shape != (height, width):
        ys = (np.arange(height) * alpha.shape[0] // height)
        xs = (np.arange(width) * alpha.shape[1] // width)
        alpha = alpha[np.ix_(ys, xs)]
    px = np.empty(width * height * 4, dtype=np.float32)
    img.pixels.foreach_get(px)
    px = px.reshape(-1, 4)
    px[:, 3] = np.clip(alpha.reshape(-1), 0.0, 1.0)
    img.pixels.foreach_set(px.reshape(-1))
    img.update()
    return img


def bake_high_to_low(high, low, out_dir, stem, *, size=DEFAULT_BAKE_SIZE, device="CPU",
                     roles=None, alpha=None):
    """Bake the dense mesh's surface into the low mesh's UVs, writing PNGs to `out_dir`.

    A bake is a TRANSFER, so the dense mesh's own UV layout is irrelevant and the paint model is
    free to have owned it (R4). What comes back is basecolor and roughness carried over from the
    generated PBR, plus a normal and an AO that describe the detail the decimation just removed --
    which is the entire point of decimating rather than shipping 500k triangles.

    Returns {role: path}. Roles with no result are simply absent, the same contract
    `assets.texture_set_maps()` uses. Which mesh each role reads FROM is decided per role rather than
    once: see the comment on `colour_from_low`, which is a G4c fix.

    `alpha` is TRELLIS.2's opacity channel from `source_opacity`, written into the basecolor's
    fourth channel rather than saved as a sixth file. Two reasons, and the second is the one that
    matters: the source alpha is already in the low mesh's own UV layout, which is the layout being
    baked into, so no transfer is needed; and an RGBA basecolor is the one alpha route that survives
    the glTF export and re-import that `import_generated` does, because glTF carries opacity in
    `baseColorTexture`'s alpha and nowhere else.
    """
    scene = bpy.context.scene
    prev_engine, prev_samples = scene.render.engine, getattr(scene.cycles, "samples", None)
    scene.render.engine = "CYCLES"
    scene.cycles.device = device
    scene.render.bake.cage_extrusion = max(0.02, max(dimensions(low)) * 0.02)
    scene.render.bake.max_ray_distance = scene.render.bake.cage_extrusion * 4.0
    scene.render.bake.use_pass_direct = False
    scene.render.bake.use_pass_indirect = False
    scene.render.bake.use_pass_color = True
    # Without a margin the bake stops at the island edge, so bilinear filtering pulls the black
    # background in and every island gets a dark rim at any mip level.
    scene.render.bake.margin = max(4, size // 64)

    mat = low.data.materials[0] if low.data.materials else None
    if mat is None:
        mat = bpy.data.materials.new(f"M_{stem}")
        mat.use_nodes = True
        low.data.materials.append(mat)

    os.makedirs(out_dir, exist_ok=True)
    written = {}
    wanted = roles or _BAKE_ROLES
    # Where each kind of map comes from, and it is not one answer. Normal and AO are a TRANSFER and
    # need the dense mesh; colour and roughness are the generated PBR and live wherever the texture
    # landed. Three cases, all of them real:
    #
    #  - the dense mesh is textured (W9b, whose one file is both meshes): transfer everything.
    #  - only the LOW mesh is textured (W7 then W9t, and any chain whose geometry graph is
    #    geometry-only): the colour is ALREADY in the low mesh's own UVs, so it is a self-bake with
    #    no cage. Measured at G4c: Omni returns geometry with no material at all, so without this the
    #    block-out route shipped an asset with W9t's albedo silently dropped.
    #  - neither is textured (W5t alone): skip the colour roles. A DIFFUSE bake would write a solid
    #    black basecolor and a ROUGHNESS bake the Principled default, and both are worse than absent
    #    because the material wiring would then read a map that says "this object is black".
    colour_from_low = has_textures(low) and not has_textures(high)
    if not has_textures(high) and not colour_from_low:
        wanted = [r for r in wanted if r[1] in ("NORMAL", "AO")]
    for role, bake_type, _socket, is_data in wanted:
        with_alpha = alpha is not None and role == "basecolor"
        self_bake = colour_from_low and bake_type in ("DIFFUSE", "ROUGHNESS")
        img = _bake_image(f"{stem}_{role}", size, is_data, alpha=with_alpha)
        node = _bake_target_node(mat, img)
        scene.cycles.samples = _BAKE_SAMPLES.get(bake_type, 1)
        scene.render.bake.use_selected_to_active = not self_bake
        _select_only([low] if self_bake else [high, low], low)
        try:
            bpy.ops.object.bake(type=bake_type)
        except RuntimeError as exc:
            print(f"[bob_blender_tools] bake {role} failed: {exc}")
            mat.node_tree.nodes.remove(node)
            bpy.data.images.remove(img)
            continue
        if with_alpha:
            _write_alpha(img, alpha)
        path = os.path.join(out_dir, f"{stem}_{role}.png")
        img.filepath_raw = path
        img.file_format = "PNG"
        img.save()
        written[role] = path
        mat.node_tree.nodes.remove(node)
        bpy.data.images.remove(img)

    scene.render.engine = prev_engine
    if prev_samples is not None:
        scene.cycles.samples = prev_samples
    return written


def cutout_render_method(mat):
    """Make a material with a wired Alpha actually cut out, and say whether it could.

    Setting the Alpha socket is not enough on its own: EEVEE renders an opaque pass unless the
    material's render method says otherwise, so an alpha map with the default method is a texture
    lookup that changes nothing on screen. DITHERED rather than BLENDED, because a foliage cutout
    wants a stochastic clip that casts a real shadow and needs no per-surface sort, which is exactly
    what BLENDED cannot give. `surface_render_method` replaced `blend_method` in Blender 4.2, so
    both names are tried; `water.py` guards the same attribute the same way.
    """
    if hasattr(mat, "surface_render_method"):
        mat.surface_render_method = "DITHERED"
        return "DITHERED"
    if hasattr(mat, "blend_method"):
        mat.blend_method = "CLIP"
        return "CLIP"
    return None


def apply_baked_material(obj, maps, name):
    """Wire the baked maps into a fresh Principled material on `obj`.

    Backface culling stays OFF and the alpha channel is honoured, because a generated leaf is a
    single-sided open surface and culling it would make it invisible from behind. Both are
    asserted in the headless gate rather than left to the default.

    The alpha comes from the basecolor's own fourth channel, which is where `bake_high_to_low` puts
    TRELLIS.2's opacity output, and it drags the render method with it (`cutout_render_method`).
    """
    mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    mat.use_backface_culling = False
    nt = mat.node_tree
    bsdf = next(n for n in nt.nodes if n.bl_idname == "ShaderNodeBsdfPrincipled")

    def image_node(path, is_data, y):
        node = nt.nodes.new("ShaderNodeTexImage")
        node.image = bpy.data.images.load(path, check_existing=True)
        node.image.colorspace_settings.name = "Non-Color" if is_data else "sRGB"
        node.location = (bsdf.location.x - 700, bsdf.location.y + y)
        return node

    if "basecolor" in maps:
        albedo = image_node(maps["basecolor"], False, 300)
        if "ao" in maps:
            # AO folds into the albedo, the same convention the texture-set sampler uses: neither
            # master carries an AO socket and a multiply is what an AO map means anyway.
            ao = image_node(maps["ao"], True, 60)
            mix = nt.nodes.new("ShaderNodeMix")
            mix.data_type = "RGBA"
            mix.blend_type = "MULTIPLY"
            mix.inputs["Factor"].default_value = 1.0
            mix.location = (bsdf.location.x - 380, bsdf.location.y + 220)
            nt.links.new(albedo.outputs["Color"], mix.inputs[6])
            nt.links.new(ao.outputs["Color"], mix.inputs[7])
            nt.links.new(mix.outputs[2], bsdf.inputs["Base Color"])
        else:
            nt.links.new(albedo.outputs["Color"], bsdf.inputs["Base Color"])
        if albedo.image.depth in (32, 64):
            nt.links.new(albedo.outputs["Alpha"], bsdf.inputs["Alpha"])
            cutout_render_method(mat)
    if "roughness" in maps:
        nt.links.new(image_node(maps["roughness"], True, -120).outputs["Color"],
                     bsdf.inputs["Roughness"])
    if "normal" in maps:
        nmap = nt.nodes.new("ShaderNodeNormalMap")
        nmap.location = (bsdf.location.x - 300, bsdf.location.y - 320)
        nt.links.new(image_node(maps["normal"], True, -320).outputs["Color"], nmap.inputs["Color"])
        nt.links.new(nmap.outputs["Normal"], bsdf.inputs["Normal"])

    obj.data.materials.clear()
    obj.data.materials.append(mat)
    return mat


# -- Step 7b: LOD chain ------------------------------------------------------------------------
def build_lods(obj, ratios=DEFAULT_LODS):
    """`obj` renamed to `<name>_LOD0`, plus one decimated copy per ratio. Returns the chain."""
    base_name = obj.name
    obj.name = f"{base_name}_LOD0"
    obj.data.name = obj.name
    chain = [obj]
    for i, ratio in enumerate(ratios, start=1):
        copy = obj.copy()
        copy.data = obj.data.copy()
        copy.name = f"{base_name}_LOD{i}"
        copy.data.name = copy.name
        _link(copy)
        decimate_to(copy, max(16, int(face_count(obj) * float(ratio))))
        chain.append(copy)
    return chain


# -- Step 8: the generated pack ------------------------------------------------------------------
def generated_dir(pack_dir, kind=None):
    """`<pack>/models/generated[/<kind>]`, created."""
    path = os.path.join(pack_dir, "models", GENERATED_BIOME)
    if kind:
        path = os.path.join(path, kind)
    os.makedirs(path, exist_ok=True)
    return path


def unique_asset_name(pack_dir, kind, stem):
    """`stem`, or `stem_02`, ... -- the first name no generated asset already occupies. Never an
    implicit overwrite (R16): a second "mossy rock" is a new asset, not a replaced one."""
    base = generated_dir(pack_dir, kind)
    if not os.path.isfile(os.path.join(base, stem + ".glb")):
        return stem
    for n in range(2, 1000):
        cand = f"{stem}_{n:02d}"
        if not os.path.isfile(os.path.join(base, cand + ".glb")):
            return cand
    raise ValueError(f"too many generated assets named {stem}")


def write_manifest_entry(pack_dir, kind, entry):
    """Add one entry to the generated pack's manifest, rewriting it in place.

    ONE reader, still (R11): this writes the v2 shape `assets.biome_manifest()` already normalises,
    with the generated fields (`height_m`, `lod`, `origin`, `faces`, `prompt`, `seed`) that
    `_norm_entries` now defaults. No second schema and no second loader.
    """
    path = os.path.join(generated_dir(pack_dir), "manifest.json")
    try:
        with open(path) as fh:
            data = json.load(fh)
        if not isinstance(data, dict):
            data = {}
    except (OSError, ValueError):
        data = {}
    meta = data.setdefault("meta", {})
    meta.setdefault("name", "Generated")
    meta.setdefault("description", "Assets generated by BobBlenderTools through ComfyUI")
    meta["generated"] = True
    models = data.setdefault("models", {})
    entries = [e for e in models.get(kind, []) if not (isinstance(e, dict)
                                                       and e.get("file") == entry["file"])]
    entries.append(entry)
    models[kind] = entries
    with open(path, "w") as fh:
        json.dump(data, fh, indent=2, sort_keys=True)
    return path


def write_sidecar(path, provenance):
    """The per-asset provenance JSON beside the GLB (R10): workflow, model, seed, prompt, and the
    license of the model that made it, so the terms travel with the asset."""
    with open(path, "w") as fh:
        json.dump(provenance, fh, indent=2, sort_keys=True)
    return path


# -- The whole of steps 6 to 8 --------------------------------------------------------------------
def _resolve_pass(spec, argument):
    """A ComfyUI stage given either as a finished file or as a callable that produces one.

    Both forms are real. The panel runs the whole ComfyUI chain on the worker thread FIRST and
    hands `finish_asset` the resulting paths, because a callable here would run its HTTP inside a
    main-thread bpy call and block the UI for the length of a mesh job, which is the one thing
    Bob-side constraint 2 forbids. A headless script owns its own thread and passes callables.
    """
    return spec(argument) if callable(spec) else str(spec)


def prepare_low(raw_glb, *, name="generated_asset", faces=DEFAULT_FACES, hero=False,
                keep_source_uv=False, low_glb=None, report=None, on_progress=None,
                fill_pinholes=True, simplified_glb=None, exports=None):
    """Steps 3 and 4 on the main thread: import the dense mesh, make the low one, unwrap it, and
    optionally export the low as a unit-scale GLB for the W9t texture pass to consume.

    Split out of `finish_asset` because the texture pass is an HTTP call that has to happen on the
    worker thread while every `bpy` touch stays here. The operator chain is
    prepare_low -> submit W9t -> finish_asset(textured_glb=...), and a headless script that owns
    the whole process just calls `finish_asset` with a `texture_pass` callback instead.

    Returns (high, low). The low mesh is still unit-scale, which is exactly what W9t needs.

    `exports` names how many `Trellis2ExportTrimesh` writes each incoming file has been through, so
    both meshes land in ONE frame before the bake reads across them: see `undo_exports`.
    """
    report = report if report is not None else {}
    if on_progress:
        on_progress("import")
    exports = exports or {}
    high = import_glb(raw_glb, name="BOB_GenHigh", orient=undo_exports(exports.get("raw")))
    report["welded_verts"] = weld(high)
    report["source_faces"] = face_count(high)
    report["source_boundary_edges"] = boundary_edges(high)
    report["source_dimensions"] = [round(d, 4) for d in dimensions(high)]

    if simplified_glb:
        # Steps 3 and 4 already happened, on the ComfyUI side (W9c). Nothing to decimate and
        # nothing to unwrap; the mesh arrives at its budget with a chart layout already on it.
        low = import_glb(simplified_glb, name=name,
                         orient=undo_exports(exports.get("simplified")))
        report["faces"] = face_count(low)
        report["simplify_source"] = "trellis2"
        report["uv_source"] = "trellis2_uvunwrap"
    else:
        low = high.copy()
        low.data = high.data.copy()
        low.name = name
        low.data.name = name
        _link(low)

        if on_progress:
            on_progress("simplify")
        if fill_pinholes:
            report["pinholes_closed"] = close_pinholes(low)
        if hero:
            got, warn = quadriflow_to(low, faces)
            if warn:
                report.setdefault("warnings", []).append(warn)
        else:
            got = decimate_to(low, faces)
        report["faces"] = got
        report["simplify_source"] = "quadriflow" if hero else "decimate"

        if on_progress:
            on_progress("uv")
        if keep_source_uv and low.data.uv_layers.active is not None:
            report["uv_source"] = "generator"
        else:
            smart_uv(low)
            report["uv_source"] = "smart_uv_project"
    report["uv_overlap"] = uv_overlap(low)
    report["low_boundary_edges"] = boundary_edges(low)

    if low_glb:
        _select_only([low], low)
        bpy.ops.export_scene.gltf(filepath=str(low_glb), export_format="GLB", use_selection=True)
        report["low_glb"] = str(low_glb)
    return high, low


def finish_asset(raw_glb, pack_dir, *, kind="rocks", name=None, height_m=2.0,
                 faces=DEFAULT_FACES, lods=DEFAULT_LODS, bake_size=DEFAULT_BAKE_SIZE,
                 hero=False, bake_device="CPU", keep_source_uv=False, provenance=None,
                 simplify_pass=None, texture_pass=None, fill_pinholes=True, on_progress=None,
                 force_opacity=False, exports=None):
    """A raw generated GLB to a finished, packed, BobShaded asset. Returns a report dict.

    The order is the pinned pipeline's, and each step is here because leaving it out breaks
    something specific:

      import -> simplify -> UV -> [PBR texture] -> bake dense to low -> scale to height_m
      -> origin to base -> weighted normals -> LOD chain -> BobShader convert
      -> write pack + sidecar + manifest

    Two of the stages are callbacks, because they are HTTP calls and this function is main-thread
    bpy code:

    `simplify_pass(raw_glb) -> simplified_glb` is pipeline steps 3 and 4 done by W9c
    (`Trellis2Simplify` plus `Trellis2UVUnwrap`). Passing None does them in Blender instead, which
    is the other side of the A/B.

    `texture_pass(low_glb) -> textured_glb` is pipeline step 5, W9t. Passing None gives a
    geometry-only asset whose basecolor and roughness are simply absent rather than baked black,
    since a mesh with no material has no surface to transfer.

    `keep_source_uv` skips Smart UV Project and bakes into whatever UVs the generator supplied.

    `exports` is `comfy.stage_exports(staged)`: how many `Trellis2ExportTrimesh` writes to undo on
    each incoming file. It does two jobs at once, and both are load-bearing. It puts the dense and
    the low mesh in one frame so the bake reads across them correctly, on every route; and on the
    block-out route it additionally puts the finished asset back in the block-out's own orientation,
    which is the whole point of conditioning on a block-out. See `undo_exports`.

    Omitting it reproduces the pre-G4c behaviour exactly, which is why it has no default: how many
    exports a file has been through is a property of the GRAPHS that made it, and this function
    cannot see them.
    """
    report = {"seconds": {}, "warnings": []}
    stamp = time.time

    def step(key):
        report["seconds"][key] = round(stamp() - step.t0, 3)
        step.t0 = stamp()
    step.t0 = stamp()

    stem = name or "generated_asset"
    work = os.path.dirname(os.path.abspath(str(raw_glb)))
    simplified = None
    if simplify_pass:
        if on_progress:
            on_progress("simplify")
        simplified = _resolve_pass(simplify_pass, raw_glb)
    step("simplify_remote")

    low_glb = os.path.join(work, stem + "_low.glb")
    high, low = prepare_low(raw_glb, name=stem, faces=faces, hero=hero,
                            keep_source_uv=keep_source_uv, fill_pinholes=fill_pinholes,
                            simplified_glb=simplified,
                            # Only export the low mesh when a CALLABLE texture pass needs a file
                            # to send; a finished path means it already ran.
                            low_glb=low_glb if callable(texture_pass) else None,
                            report=report, on_progress=on_progress, exports=exports)
    step("prepare")

    if texture_pass:
        if on_progress:
            on_progress("texture")
        textured = _resolve_pass(texture_pass, low_glb)
        if textured:
            bpy.data.objects.remove(low, do_unlink=True)
            low = import_glb(textured, name=stem,
                             orient=undo_exports((exports or {}).get("textured")))
            report["textured_glb"] = str(textured)
            report["textured_faces"] = face_count(low)
        else:
            report["warnings"].append("texture pass returned nothing; geometry only")
    step("texture")

    if on_progress:
        on_progress("bake")
    tex_dir = generated_dir(pack_dir, kind)
    # Read the opacity BEFORE the bake, which is the last moment the generated texture is still on
    # the mesh: `apply_baked_material` replaces the material outright.
    alpha, report["opacity"] = source_opacity(low, size=bake_size, force=force_opacity)
    report["opacity"]["wired"] = alpha is not None
    maps = bake_high_to_low(high, low, tex_dir, stem, size=bake_size, device=bake_device,
                            alpha=alpha)
    report["maps"] = maps
    if not maps:
        report["warnings"].append("no map baked; the asset ships with a flat material")
    step("bake")

    if on_progress:
        on_progress("finish")
    apply_baked_material(low, maps, f"M_{stem}")
    report["scale_factor"] = rescale_to_height(low, height_m)
    origin_to_base(low)
    # The number worth reporting is not where the origin ended up in the scene, it is how far the
    # origin sits above the mesh's lowest point. Zero means a scattered instance rests on the
    # ground; anything else is the asset half-buried or floating.
    report["origin_above_base"] = round(low.location.z - bbox_world(low)[0][2], 6)
    weighted_normals(low)
    chain = build_lods(low, lods)
    report["lods"] = [o.name for o in chain]
    report["lod_faces"] = [face_count(o) for o in chain]
    for obj in chain:
        if obj.data.materials:
            materials.bobshade_material(obj.data.materials[0])
    report["master_type"] = materials.master_type(chain[0].data.materials[0]) \
        if chain[0].data.materials else None
    report["height_m"] = round(dimensions(chain[0])[2], 4)

    out_name = unique_asset_name(pack_dir, kind, stem)
    out_glb = os.path.join(generated_dir(pack_dir, kind), out_name + ".glb")
    _select_only(chain, chain[0])
    bpy.ops.export_scene.gltf(filepath=out_glb, export_format="GLB", use_selection=True)
    report["file"] = out_glb

    entry = {"file": f"{kind}/{out_name}.glb", "height_m": round(height_m, 4),
             "lod": list(lods), "origin": "base", "faces": report["lod_faces"][0]}
    prov = dict(provenance or {})
    entry.update({k: prov[k] for k in ("prompt", "seed") if k in prov})
    report["manifest"] = write_manifest_entry(pack_dir, kind, entry)
    prov.update({"file": entry["file"], "kind": kind, "height_m": entry["height_m"],
                 "faces": entry["faces"], "lod": list(lods), "origin": "base",
                 "uv_source": report["uv_source"], "maps": sorted(maps)})
    prov.setdefault("model", "TRELLIS.2-4B")
    prov.setdefault("license", "MIT")
    prov.setdefault("license_note", "Output licensing follows the model that produced it.")
    report["sidecar"] = write_sidecar(os.path.join(generated_dir(pack_dir, kind),
                                                   out_name + ".json"), prov)
    report["name"] = out_name
    step("finish")

    bpy.data.objects.remove(high, do_unlink=True)
    for obj in chain:
        bpy.data.objects.remove(obj, do_unlink=True)
    report["seconds"]["total"] = round(sum(report["seconds"].values()), 3)
    return report


# -- Step 8: consume ------------------------------------------------------------------------------
# LOD1 and up are real and exported, but they must not reach a scatter layer: a GN instancer takes
# the whole collection, so linking the chain would scatter three copies of every asset at three
# densities. LOD0 goes in the BOB_Assets_<Kind> collection scatter already reads; the rest go here.
LOD_COLLECTION = "BOB_Generated_LODs"


def _lod_collection():
    coll = bpy.data.collections.get(LOD_COLLECTION)
    if coll is None:
        coll = bpy.data.collections.new(LOD_COLLECTION)
        bpy.context.scene.collection.children.link(coll)
        layer = bpy.context.view_layer.layer_collection.children.get(LOD_COLLECTION)
        if layer is not None:
            layer.exclude = True
    return coll


def import_generated(name, kind="rocks", pack_dir=None):
    """Import a finished generated asset into `BOB_Assets_<Kind>`, ready for a scatter layer.

    Returns the LOD0 object. The asset arrives already scaled, origin-at-base, and BobShaded, so
    there is nothing for the caller to fix up: this is a link, not a second finishing pass.
    """
    if pack_dir is None:
        pack_dir = assets.generated_root()
    if not pack_dir:
        raise ValueError("no generated pack root registered")
    path = name if os.path.isabs(name) else os.path.join(generated_dir(pack_dir, kind),
                                                         name + ".glb")
    if not os.path.isfile(path):
        raise ValueError(f"no generated asset at {path}")

    before = set(bpy.data.objects)
    bpy.ops.import_scene.gltf(filepath=path)
    fresh = [o for o in bpy.data.objects if o not in before]
    for obj in list(fresh):
        if obj.type != "MESH":
            bpy.data.objects.remove(obj, do_unlink=True)
            fresh.remove(obj)
    if not fresh:
        raise ValueError(f"no mesh in {os.path.basename(path)}")

    target = proxies.ensure_collection(kind)
    lods = _lod_collection()
    lod0 = None
    for obj in fresh:
        obj.parent = None
        _unlink(obj)
        # glTF carries PBR, not Blender node groups, so the BobShader applied before export does
        # NOT survive the round trip and the imported material is a plain Principled. Re-applying
        # here is the fix, and it is where the convert belongs anyway: `bobshade_material` routes
        # an asset's OWN maps through S_SurfaceMaster, and the maps only exist on disk once the
        # bake has run. Idempotent, so a material that somehow kept its Master is left alone.
        for slot in obj.material_slots:
            if slot.material is None:
                continue
            # The glTF importer reads the exported alphaMode and wires Alpha again, but it picks
            # BLENDED for a BLEND material; a cutout wants DITHERED. `bobshade_material` leaves
            # Alpha alone, so the link survives the convert either way.
            if slot.material.use_nodes:
                bsdf = next((n for n in slot.material.node_tree.nodes
                             if n.bl_idname == "ShaderNodeBsdfPrincipled"), None)
                if bsdf is not None and bsdf.inputs["Alpha"].links:
                    cutout_render_method(slot.material)
            materials.bobshade_material(slot.material)
        if obj.name.split(".")[0].endswith("_LOD0") or len(fresh) == 1:
            target.objects.link(obj)
            lod0 = lod0 or obj
        else:
            lods.objects.link(obj)
    return lod0
