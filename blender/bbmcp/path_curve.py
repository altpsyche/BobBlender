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

import math

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


def _ordered_polyline_xy(obj):
    """The curve's evaluated shape as an ordered list of (x, y), walking the tessellated wire.

    A river's Z is solved from the terrain sampled ALONG the curve; sampling only the few control
    points gives a smooth centreline that ignores the terrain's relief (it cuts through hills and
    floats over dips). This reads the DENSE evaluated polyline (resolution_u subdivisions) so the
    solve can track the real valley. Edges are walked from an endpoint so the points come out in
    curve order regardless of how to_mesh emits them."""
    deps = bpy.context.evaluated_depsgraph_get()
    ev = obj.evaluated_get(deps)
    mesh = ev.to_mesh()
    verts = [(v.co.x, v.co.y) for v in mesh.vertices]
    edges = [tuple(e.vertices) for e in mesh.edges]
    ev.to_mesh_clear()
    if len(verts) < 2 or not edges:
        return verts
    adj = {}
    for a, b in edges:
        adj.setdefault(a, []).append(b)
        adj.setdefault(b, []).append(a)
    start = next((i for i in adj if len(adj[i]) == 1), edges[0][0])  # an endpoint, else anywhere
    order, prev, cur = [start], -1, start
    while len(order) < len(verts):
        nxt = [n for n in adj.get(cur, []) if n != prev]
        if not nxt:
            break
        prev, cur = cur, nxt[0]
        if cur == start:
            break
        order.append(cur)
    return [verts[i] for i in order]


def _resample_xy(pts, n):
    """Resample an ordered (x, y) polyline to n points evenly spaced by arc length."""
    if len(pts) < 2 or n < 2:
        return list(pts)
    cum = [0.0]
    for i in range(1, len(pts)):
        cum.append(cum[-1] + math.hypot(pts[i][0] - pts[i - 1][0], pts[i][1] - pts[i - 1][1]))
    total = cum[-1] or 1.0
    out = []
    for k in range(n):
        target = total * k / (n - 1)
        j = 1
        while j < len(cum) and cum[j] < target:
            j += 1
        j = min(j, len(pts) - 1)
        seg = cum[j] - cum[j - 1] or 1.0
        f = (target - cum[j - 1]) / seg
        out.append((pts[j - 1][0] * (1 - f) + pts[j][0] * f,
                    pts[j - 1][1] * (1 - f) + pts[j][1] * f))
    return out


def _rebuild_nurbs(obj, points):
    """Replace obj's curve data with a single NURBS spline through points [(x, y, z), ...].

    The points are already dense (a river densify), so a low resolution_u keeps the evaluated curve
    smooth without exploding the vertex count the overlay's proximity solve walks per terrain point."""
    curve = obj.data
    curve.splines.clear()
    spline = curve.splines.new("NURBS")
    spline.points.add(len(points) - 1)
    for p, (x, y, z) in zip(spline.points, points):
        p.co = (x, y, z, 1.0)
    spline.order_u = min(3, len(points))
    spline.use_endpoint_u = True
    curve.resolution_u = 12


# A river runs monotonically downhill (docs/SPLINES.md 9 #1, the IMPOSE family): unlike a path,
# which follows whatever Z the terrain has, a river's centreline must never rise from source to
# mouth, and the terrain later conforms DOWN to it (the overlay's impose mode). Sea level sits at
# absolute Z 0 because heightmap_terrain displaces by (raw - sea_level) * height, so raw == sea_level
# maps to Z 0; pulling the mouth "to the sea" therefore pulls it toward 0.
_SEA_Z = 0.0


def _monotonic_descend(pts, min_slope, to_sea, sea_z=_SEA_Z):
    """Clamp a spline's per-point terrain Z into a monotonic downhill profile from source to mouth.

    pts is a list of (x, y, terrain_z) in spline order. The SOURCE is the higher end; the walk
    runs toward the lower MOUTH. Each point's Z is the MINIMUM of three ceilings, so the profile is
    guaranteed downhill and (optionally) reaches the sea:

    - the terrain sample itself (so the river hugs the valley floor where the ground already falls);
    - a running min-slope ceiling `running - min_slope * segment_length` (monotonic descent, and a
      gentle continuous fall through flats/pools when min_slope > 0, cutting a gorge where the ground
      would rise);
    - when to_sea, a straight source->sea_z ceiling over the arc length, so the mouth lands at sea
      level even if the terrain there is high.

    Returns a new list of (x, y, z). Curves shorter than two points are returned unchanged.
    """
    n = len(pts)
    if n < 2:
        return list(pts)
    z = [p[2] for p in pts]
    xy = [(p[0], p[1]) for p in pts]
    forward = z[0] >= z[-1]
    order = list(range(n)) if forward else list(range(n - 1, -1, -1))
    src, mouth = order[0], order[-1]

    # Arc length from the source along the ordered points, for the linear-to-sea ceiling.
    arc = [0.0] * n
    acc, prev = 0.0, xy[src]
    for k in order[1:]:
        acc += math.hypot(xy[k][0] - prev[0], xy[k][1] - prev[1])
        arc[k] = acc
        prev = xy[k]
    total = acc if acc > 1e-6 else 1.0

    z_src = z[src]
    z_mouth = min(z[mouth], sea_z) if to_sea else z[mouth]
    out = list(z)
    running, prev = z_src, xy[src]
    out[src] = z_src
    for k in order[1:]:
        seg = math.hypot(xy[k][0] - prev[0], xy[k][1] - prev[1])
        ceil_slope = running - min_slope * seg
        target = z[k]
        if to_sea:
            target = min(target, z_src - (z_src - z_mouth) * (arc[k] / total))
        out[k] = min(target, ceil_slope)
        running, prev = out[k], xy[k]
    return [(xy[i][0], xy[i][1], out[i]) for i in range(n)]


def drape_curve(op: dict) -> dict:
    """Drape an existing curve object's control points onto a terrain heightmap, in place.

    The counterpart to make_path's drape for a hand-drawn or panel-added curve: make_path
    bakes Z into points it creates, but a curve the artist drew (or moved) needs its Z
    re-sampled against the current terrain so its smooth profile follows the ground. This is
    the C1 stand-in for the live re-drape the GN overlay does in C2 (docs/SPLINES.md 4.2): the
    follow-terrain roles grade a bench to the curve's own Z, so that Z must track the surface.

    With monotonic set (a river/stream, docs/SPLINES.md 9 #1), the sampled Z is additionally
    clamped into a downhill profile from source to mouth (_monotonic_descend), so the water
    centreline never runs uphill and the overlay's impose mode can cut the terrain DOWN to it.
    min_slope forces a gentle continuous fall through flats; to_sea pulls the mouth to sea level.

    densify (>= 2, rivers) resamples the curve to that many points along its evaluated shape BEFORE
    sampling + solving, then rebuilds it as one dense NURBS. Sampling only the few control points
    gives a smooth centreline that ignores the terrain's relief -- it cuts through hills and floats
    over dips (measured: 17% of the water surface floated above the ground with 4 points, 0% with
    48). The dense solve tracks the actual valley, so the water sits IN the terrain everywhere.

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
    monotonic = bool(op.get("monotonic", False))
    min_slope = float(op.get("min_slope", 0.0))
    to_sea = bool(op.get("to_sea", False))
    densify = int(op.get("densify", 0))

    def sz(x, y):
        return _surface_z(pixels, width_px, height_px, x, y, size, height, sea_level)

    # Densify path (rivers): resample the shape, solve at high resolution, rebuild as one NURBS.
    if monotonic and densify >= 2:
        xy = _resample_xy(_ordered_polyline_xy(obj), densify)
        draped = _monotonic_descend([(x, y, sz(x, y)) for x, y in xy], min_slope, to_sea)
        _rebuild_nurbs(obj, draped)
        obj.data.update_tag()
        return {"op": "drape_curve", "created": [obj.name],
                "info": f"draped {len(draped)} points (river, densified)"}

    n = 0
    for spline in obj.data.splines:
        if spline.type == "BEZIER":
            pts = spline.bezier_points
            draped = [(p.co[0], p.co[1], sz(p.co[0], p.co[1])) for p in pts]
            if monotonic:
                draped = _monotonic_descend(draped, min_slope, to_sea)
            for p, (x, y, z) in zip(pts, draped):
                p.co = (x, y, z)
                n += 1
        else:  # NURBS / POLY: co is a 4D (x, y, z, w) vector, preserve w
            pts = spline.points
            ws = [p.co[3] for p in pts]
            draped = [(p.co[0], p.co[1], sz(p.co[0], p.co[1])) for p in pts]
            if monotonic:
                draped = _monotonic_descend(draped, min_slope, to_sea)
            for p, (x, y, z), w in zip(pts, draped, ws):
                p.co = (x, y, z, w)
                n += 1
    # Mutating control points in Python does not auto-tag the datablock, so a modifier that reads
    # this curve (the terrain's curve overlay, via Object Info) can keep evaluating the pre-drape
    # geometry. Tag it so dependents re-evaluate against the new draped points.
    obj.data.update_tag()
    kind = "river" if monotonic else "path"
    return {"op": "drape_curve", "created": [obj.name], "info": f"draped {n} points ({kind})"}


def inspect_river(op: dict) -> dict:
    """Read-only diagnostic: measure the built water ribbon against the terrain, to see whether it
    floats. Reports water Z range, how many water verts sit ABOVE the surrounding banks (floating)
    vs inside a carved channel, and a few sample rows (water / bed-directly-below / bank at a lateral
    probe). No mutation."""
    water = bpy.data.objects.get(op.get("water", "BOB_Water_River"))
    terrain = bpy.data.objects.get(op.get("terrain", "Terrain"))
    if water is None or terrain is None:
        names = [o.name for o in bpy.data.objects]
        return {"op": "inspect_river", "info": f"missing water/terrain; objects={names}"}
    deps = bpy.context.evaluated_depsgraph_get()
    tev = terrain.evaluated_get(deps)
    probe = float(op.get("probe", 16.0))

    def down(x, y):
        hit, loc, *_ = tev.ray_cast((x, y, 1.0e5), (0.0, 0.0, -1.0), distance=2.0e5)
        return loc.z if hit else None

    wev = water.evaluated_get(deps)
    wmesh = wev.to_mesh()
    verts = wmesh.vertices
    n = len(verts)
    if n == 0:
        wev.to_mesh_clear()
        return {"op": "inspect_river", "info": "water ribbon has 0 verts (not built / empty recipe)"}
    wz = [v.co.z for v in verts]
    floating = channel = 0
    samples = []
    step = max(1, n // 8)
    for i, v in enumerate(verts):
        x, y, z = v.co.x, v.co.y, v.co.z
        bed = down(x, y)
        banks = [b for b in (down(x + probe, y), down(x - probe, y),
                             down(x, y + probe), down(x, y - probe)) if b is not None]
        bank = max(banks) if banks else None
        if bank is not None and z > bank + 0.1:
            floating += 1
        if bed is not None and bank is not None and bed < bank - 0.3:
            channel += 1
        if i % step == 0 and bed is not None and bank is not None:
            samples.append((round(x, 1), round(y, 1), round(z, 2), round(bed, 2), round(bank, 2)))
    wev.to_mesh_clear()
    tb = [round(c, 2) for c in terrain.dimensions]
    info = (f"water n={n} Z[{min(wz):.2f},{max(wz):.2f}] wloc={tuple(round(c,2) for c in water.location)} "
            f"tloc={tuple(round(c,2) for c in terrain.location)} tdim={tb} | "
            f"FLOATING_above_banks={floating} ({100*floating/n:.0f}%) | "
            f"in_carved_channel={channel} ({100*channel/n:.0f}%) | "
            f"samples[x,y,water,bed,bank@{probe:.0f}m]={samples}")
    return {"op": "inspect_river", "info": info}


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
