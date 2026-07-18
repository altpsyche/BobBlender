"""make_path: author a curve object for the scatter and terrain path inputs.

A path is just a curve in the scene; the scatter and heightmap_terrain recipes
reference it by name to clear and grade a trail. This op exists so a path can be
authored through the pipeline (headless or live) rather than only by hand, since
the live bridge runs bbmcp ops and cannot create arbitrary datablocks.

With a heightmap given, the control points are draped onto the terrain surface:
each point's Z is set to (sample - sea_level) * height, matching how
heightmap_terrain displaces its grid. Because a NURBS curve has few control
points, the draped profile is smooth, so the graded trail follows the ground's
gentle rise and fall without inheriting its fine erosion detail.
"""

import bpy


def _drape_z(points, image, size, height, sea_level):
    """Return points with Z replaced by the heightmap surface height at each XY."""
    width_px, height_px = image.size
    pixels = image.pixels[:]  # one copy; per-pixel indexing on the raw array is slow
    draped = []
    for x, y, _ in points:
        u = x / size + 0.5
        v = y / size + 0.5
        px = min(max(int(u * width_px), 0), width_px - 1)
        py = min(max(int(v * height_px), 0), height_px - 1)
        raw = pixels[(py * width_px + px) * 4]  # grayscale: red channel
        draped.append((x, y, (raw - sea_level) * height))
    return draped


def make_path(op: dict) -> dict:
    name = op.get("name", "Path")
    points = op.get("points") or []
    resolution = int(op.get("resolution", 12))

    heightmap = op.get("heightmap")
    if heightmap and points:
        image = bpy.data.images.load(heightmap, check_existing=True)
        points = _drape_z(
            points, image,
            float(op.get("size", 60.0)),
            float(op.get("height", 14.0)),
            float(op.get("sea_level", 0.3)),
        )

    # Idempotent: drop an existing curve of the same name so live re-runs do not
    # stack duplicates. Removing the object leaves the old curve data orphaned,
    # which Blender frees on the next file cleanup.
    existing = bpy.data.objects.get(name)
    if existing is not None:
        bpy.data.objects.remove(existing, do_unlink=True)

    curve = bpy.data.curves.new(name, "CURVE")
    curve.dimensions = "3D"
    curve.resolution_u = resolution
    spline = curve.splines.new("NURBS")
    if points:
        spline.points.add(len(points) - 1)
        for point, (x, y, z) in zip(spline.points, points):
            point.co = (x, y, z, 1.0)
    spline.order_u = min(3, len(points)) if points else 2
    spline.use_endpoint_u = True

    obj = bpy.data.objects.new(name, curve)
    bpy.context.scene.collection.objects.link(obj)
    drape = "draped" if heightmap else "flat"
    return {"op": "make_path", "created": [obj.name], "info": f"{len(points)} points, {drape}"}
