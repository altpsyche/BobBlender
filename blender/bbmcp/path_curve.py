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


def _surface_z(pixels, width_px, height_px, x, y, size, height, sea_level):
    """The heightmap surface height at a world XY, matching heightmap_terrain's displace.

    UV = pos.xy / size + 0.5, BILINEARLY sampled from the grayscale (red) channel, then displaced
    by (raw - sea_level) * height. Bilinear (not nearest-pixel) matches the recipe's Linear
    GeometryNodeImageTexture, so the draped curve sits exactly on the surface and its Z grades
    smoothly instead of stepping at heightmap texel boundaries. Same origin (bottom-left, v = 0 at
    the bottom row, texel centres at (i + 0.5)/N) as the recipe samples with.
    """
    u = x / size + 0.5
    v = y / size + 0.5
    fx = min(max(u * width_px - 0.5, 0.0), width_px - 1.0)
    fy = min(max(v * height_px - 0.5, 0.0), height_px - 1.0)
    x0, y0 = int(fx), int(fy)
    x1, y1 = min(x0 + 1, width_px - 1), min(y0 + 1, height_px - 1)
    tx, ty = fx - x0, fy - y0

    def red(px, py):
        return pixels[(py * width_px + px) * 4]  # grayscale: red channel

    top = red(x0, y0) * (1.0 - tx) + red(x1, y0) * tx
    bot = red(x0, y1) * (1.0 - tx) + red(x1, y1) * tx
    raw = top * (1.0 - ty) + bot * ty
    return (raw - sea_level) * height


def _drape_z(points, image, size, height, sea_level):
    """Return points with Z replaced by the heightmap surface height at each XY."""
    width_px, height_px = image.size
    pixels = image.pixels[:]  # one copy; per-pixel indexing on the raw array is slow
    return [(x, y, _surface_z(pixels, width_px, height_px, x, y, size, height, sea_level))
            for x, y, _ in points]


def drape_curve(op: dict) -> dict:
    """Drape an existing curve object's control points onto a terrain heightmap, in place.

    The counterpart to make_path's drape for a hand-drawn or panel-added curve: make_path
    bakes Z into points it creates, but a curve the artist drew (or moved) needs its Z
    re-sampled against the current terrain so its smooth profile follows the ground. This is
    the C1 stand-in for the live re-drape the GN overlay does in C2 (docs/SPLINES.md 4.2): the
    follow-terrain roles grade a bench to the curve's own Z, so that Z must track the surface.

    Points are read/written in the curve's local space, so the curve object is assumed to sit
    at the origin (make_path and the Paths panel create it there).
    """
    name = op.get("name", "")
    obj = bpy.data.objects.get(name)
    if obj is None or obj.type != "CURVE":
        return {"op": "drape_curve", "info": f"no curve object {name!r}"}
    heightmap = op.get("heightmap")
    if not heightmap:
        return {"op": "drape_curve", "info": "no heightmap"}

    image = bpy.data.images.load(heightmap, check_existing=True)
    width_px, height_px = image.size
    pixels = image.pixels[:]
    size = float(op.get("size", 60.0))
    height = float(op.get("height", 14.0))
    sea_level = float(op.get("sea_level", 0.3))

    n = 0
    for spline in obj.data.splines:
        if spline.type == "BEZIER":
            for p in spline.bezier_points:
                x, y = p.co[0], p.co[1]
                p.co = (x, y, _surface_z(pixels, width_px, height_px, x, y, size, height, sea_level))
                n += 1
        else:  # NURBS / POLY: co is a 4D (x, y, z, w) vector, preserve w
            for p in spline.points:
                x, y, _, w = p.co
                z = _surface_z(pixels, width_px, height_px, x, y, size, height, sea_level)
                p.co = (x, y, z, w)
                n += 1
    # Mutating control points in Python does not auto-tag the datablock, so a modifier that reads
    # this curve (the terrain's curve overlay, via Object Info) can keep evaluating the pre-drape
    # geometry. Tag it so dependents re-evaluate against the new draped points.
    obj.data.update_tag()
    return {"op": "drape_curve", "created": [obj.name], "info": f"draped {n} points"}


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
