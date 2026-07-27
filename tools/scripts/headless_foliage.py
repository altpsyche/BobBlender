"""Headless gate for BobFoliage F1 to F6: the tree skeleton, its sweep, its leaf cards, the two
texture jobs that dress them, the wind and season that move and colour them, and the panel that
authors them (docs/FOLIAGE.md).

    ~/.steam/steam/steamapps/common/Blender/blender --background --factory-startup \
        --python tools/scripts/headless_foliage.py -- [--no-gen] [--cols 2 --rows 2] [--size 1024]

Exit code 0 = every check passed.

F3 is the first foliage phase that can use a ComfyUI server, and everything before `check_generation`
still runs without one: the geometry is procedural and the placeholder atlas ships, so no server means
that one function prints SKIP and the gate still exits 0. `--no-gen` skips it with a server present.

It MEASURES the structure rather than asserting the graph was built, because every way this recipe
goes wrong still renders something tree-shaped:

- a branch whose base was displaced off its parent leaves a floating stick,
- a level whose rotation order is wrong collapses into a flat fan,
- a level that reads its own length instead of its parent's makes twigs as long as the trunk,
- a seed that reaches nothing makes a stand of identical clones,
- a radius that never reaches the sweep makes every twig as thick as the trunk,
- an atlas cell that never varies makes one leaf repeated ten thousand times,
- a card whose base misses its tip leaves leaves hanging in the air,
- a wind that reaches the trunk but not the cards leaves a canopy hanging in mid-air,
- a season that reaches nothing leaves a summer tree in November,

and none of those raises. Two of them were found by writing these checks rather than by looking:
`Curve to Mesh` stopped applying the curve radius in Blender 4.0 and takes an explicit Scale now, so
every F1 tree was a uniform 1 m tube; and the bend amplitude was in metres, so the same params that
grew a tree tore a grass tuft apart. Both rendered perfectly and both were wrong.

The recipe writes the attributes this reads (`bbt_fol_level`, `bbt_fol_t`, `bbt_fol_off`,
`bbt_fol_tip`, `bbt_fol_rad`, `bbt_fol_leaf`, `bbt_fol_cell`) for the shader and for the cards; the
gate is a second consumer, not the reason they exist.
"""

import argparse
import math
import os
import shutil
import sys
import tempfile
import time
from collections import Counter

import bpy
import numpy as np

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(REPO, "blender", "extensions"))

from bob_blender_tools.core import assets, materials  # noqa: E402
from bob_blender_tools.core.dispatch import apply_op  # noqa: E402

FAILURES = []
OUT = os.path.join(REPO, "_generated", "foliage_check")

# One explicit set of params, so every expected count below is arithmetic rather than a default that
# can drift underneath the gate. Cards off by default: the F1 checks below count vertices per level
# and a card would be extra geometry carrying no level, so the card checks build their own trees.
BASE = {"levels": 3, "height": 20.0, "seed": 3, "segments": 14, "branch_segments": 6,
        "profile_segments": 6, "l1_branches": 9, "l2_branches": 5, "l3_branches": 4, "cards": 0}


def check(label, ok, detail=""):
    print(f"[{'PASS' if ok else 'FAIL'}] {label}" + (f" -- {detail}" if detail else ""))
    if not ok:
        FAILURES.append(label)
    return ok


def build(name, **overrides):
    """Build a foliage object and return its evaluated mesh plus the params used."""
    params = dict(BASE, **overrides)
    apply_op({"op": "build_geonodes", "recipe": "foliage", "name": name, "params": params,
              "reset": True})
    obj = bpy.data.objects[name]
    evaluated = obj.evaluated_get(bpy.context.evaluated_depsgraph_get())
    return evaluated, evaluated.to_mesh(), params


def attr(mesh, name):
    found = mesh.attributes.get(name)
    return [d.value for d in found.data] if found else []


def splines_per_level(mesh, params):
    """{level: spline count}, derived from the vertex count each level contributes.

    Curve to Mesh emits one ring of `profile_segments` verts per curve point, so a level's vertex
    count divided by (its points per curve x the profile) is its curve count exactly. This is how the
    gate reads structure off a mesh: the evaluated CURVES of a mesh object are not reachable through
    the Python API, and adding an output just for the test would be worse than arithmetic.
    """
    profile = params["profile_segments"]
    out = {}
    for level, verts in Counter(attr(mesh, "bbt_fol_level")).items():
        segments = params["segments"] if level == 0 else params["branch_segments"]
        exact, remainder = divmod(verts, segments * profile)
        out[level] = exact if remainder == 0 else verts / (segments * profile)
    return out


def tip_points(mesh, params):
    """The world position of every branch TIP, from the swept mesh's tip rings.

    `bbt_fol_tip` is 1 on the last ring of every curve, and Curve to Mesh emits a curve's rings in
    order, so the flagged vertices arrive as consecutive runs of exactly `profile_segments`. Each
    run's centroid is the curve's tip -- which is the point a card was instanced on, so this is what
    "the card is still attached to its twig" gets measured against. The run length is checked here
    rather than assumed: if the ordering ever changes, this returns nothing and the caller fails
    loudly instead of comparing against an empty set.
    """
    profile = params["profile_segments"]
    # Card vertices are excluded explicitly rather than trusted to be unflagged. They grew ON the
    # tips, so every point attribute of a tip rides onto them; the recipe clears the tip flag for
    # exactly that reason, and this keeps the helper true whether or not it does.
    on_cards = {v for f in mesh.polygons if f.index in set(card_faces(mesh)) for v in f.vertices}
    flagged = [i for i, v in enumerate(attr(mesh, "bbt_fol_tip"))
               if v > 0.5 and i not in on_cards]
    runs, run = [], []
    for i in flagged:
        if run and i != run[-1] + 1:
            runs.append(run)
            run = []
        run.append(i)
    if run:
        runs.append(run)
    if any(len(r) != profile for r in runs):
        return []
    return [tuple(sum(mesh.vertices[i].co[axis] for i in r) / profile for axis in range(3))
            for r in runs]


def card_faces(mesh):
    """Indices of the faces flagged `bbt_fol_leaf`."""
    flag = mesh.attributes.get("bbt_fol_leaf")
    return [] if flag is None else [i for i, d in enumerate(flag.data) if d.value]


def nearest(points, targets):
    """(distance, target index) to the closest of `targets`, per point, as numpy arrays.

    numpy because a real tree is a few thousand card corners against a few hundred tips, and the
    Python loop is seconds. Chunked, because the full outer product is len(points) x len(targets).
    """
    import numpy as np

    p = np.asarray(points, dtype=np.float64)
    t = np.asarray(targets, dtype=np.float64)
    dist, idx = [], []
    for start in range(0, len(p), 2048):
        d = np.linalg.norm(p[start:start + 2048, None, :] - t[None, :, :], axis=2)
        dist.append(d.min(axis=1))
        idx.append(d.argmin(axis=1))
    return np.concatenate(dist), np.concatenate(idx)


def card_base_gaps(mesh, faces, tips):
    """Per card, how far its base-edge MIDPOINT sits from the tip it grew on.

    Not the corner distance: the quad is `Card Size * Card Width` wide and stands on its base edge,
    so its two base CORNERS are half a width either side of the tip and are supposed to be. The
    midpoint between them is the instance origin, which is the tip exactly. (Measuring the corners
    instead is what this check did first, and it reported a 0.15 m gap on a perfectly attached card
    -- 0.15 m being exactly half the card width.)
    """
    import numpy as np

    if not faces or not tips or not mesh.uv_layers:
        return []
    uv = mesh.uv_layers[0].data
    midpoints = []
    for i in faces:
        face = mesh.polygons[i]
        # Which two corners are the BASE is read off the UV, not off proximity. The card's own v
        # runs 0 at the base to 1 at the free end within its atlas cell, so the two lowest-v
        # corners are the base pair. Picking them by "closest to a tip" instead is wrong on a dense
        # crown: a card's free corner is regularly nearer a NEIGHBOURING tip than its own base is
        # to its own, and the check then measures the midpoint of two corners of different edges.
        pairs = sorted(zip((uv[c].uv[1] for c in face.loop_indices), face.vertices))
        base = [mesh.vertices[v].co for _v, v in pairs[:2]]
        midpoints.append([(base[0][a] + base[1][a]) / 2.0 for a in range(3)])
    return [float(d) for d in nearest(midpoints, tips)[0]]


def live_grid(obj):
    """(Atlas Columns, Atlas Rows) as the object's LIVE modifier knobs.

    Read off `mod.properties.inputs.<identifier>.value`, which is where a Blender 5.2 Nodes modifier
    keeps its input values. Not `mod[identifier]` (a Nodes modifier has no IDProperties and raises)
    and not the interface `default_value` (that only seeds a fresh bind, so reading it would pass
    whether or not the value reached the modifier at all).
    """
    mod = next(m for m in obj.modifiers if m.type == "NODES")
    ident = {s.name: s.identifier for s in mod.node_group.interface.items_tree
             if getattr(s, "in_out", "") == "INPUT"}
    inputs = mod.properties.inputs
    return tuple(getattr(inputs, ident[name]).value
                 for name in ("Atlas Columns", "Atlas Rows"))


def live_value(obj, socket_name):
    """One live modifier knob by socket name, off `mod.properties.inputs.<identifier>.value`.

    The same rule `live_grid` follows and for the same reason: the interface `default_value` only
    seeds a fresh bind, so reading it would pass whether or not the value reached the modifier.
    """
    mod = next(m for m in obj.modifiers if m.type == "NODES")
    ident = next((s.identifier for s in mod.node_group.interface.items_tree
                  if getattr(s, "in_out", "") == "INPUT" and s.name == socket_name), None)
    inp = getattr(mod.properties.inputs, ident, None) if ident else None
    return None if inp is None else inp.value


def uv_bounds(mesh, faces):
    """(umin, umax, vmin, vmax) over the UV corners of the given faces, or None."""
    if not mesh.uv_layers or not faces:
        return None
    uv = mesh.uv_layers[0].data
    keep = set(faces)
    vals = [tuple(uv[c].uv) for f in mesh.polygons if f.index in keep for c in f.loop_indices]
    if not vals:
        return None
    return (min(v[0] for v in vals), max(v[0] for v in vals),
            min(v[1] for v in vals), max(v[1] for v in vals))


def render_variance(engine, path):
    """Render a small frame; return (max - min) over its pixel luminance, or None if the engine
    could not render here. The shape headless_texset.py uses, for the same reason: a wired-but-dead
    shader graph passes every structural check and comes back a single flat colour."""
    scene = bpy.context.scene
    try:
        scene.render.engine = engine
    except TypeError:
        return None  # an engine whose addon was enabled after startup is absent from the enum
    scene.render.resolution_x = scene.render.resolution_y = 160
    scene.render.resolution_percentage = 100
    scene.render.image_settings.file_format = "PNG"
    scene.render.filepath = path
    if engine == "CYCLES":
        scene.cycles.samples = 12
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


def check_radius():
    """The radius knobs must reach the SWEPT MESH, and a branch must read its parent's local one.

    Two separate failures, both invisible in a viewport. Blender 4.0 gave Curve to Mesh an explicit
    `Scale` and stopped applying the curve's radius attribute, so F1 -- which only ever called Set
    Curve Radius -- swept every tree as a uniform 1 m tube: Trunk Radius, Taper and every per-level
    ratio were inert, and the tree still looked like a tree. And the base radius used to be a running
    product of the per-level ratios, which agrees with the parent's real thickness only while the
    ratios are uniform and the parent does not taper.
    """
    def trunk_width(**overrides):
        ev, mesh, params = build("Rad", **overrides)
        level = attr(mesh, "bbt_fol_level")
        low = [math.hypot(v.co.x, v.co.y) for i, v in enumerate(mesh.vertices)
               if level[i] == 0 and v.co.z < 1.0]
        ev.to_mesh_clear()
        return max(low) if low else 0.0

    thin, thick = trunk_width(trunk_radius=0.25), trunk_width(trunk_radius=0.50)
    check("Trunk Radius reaches the swept mesh", thin > 0.0 and abs(thick / thin - 2.0) < 0.05,
          f"{thin:.3f} m at r=0.25, {thick:.3f} m at r=0.50")
    check("and it is the radius asked for, not the profile's own", abs(thin - 0.25) < 0.02,
          f"{thin:.3f} m against 0.25")

    # The taper is what makes the parent's radius vary ALONG it, so a level-1 branch low on the
    # trunk has to come out thicker than one near the top. A running product cannot tell them apart.
    ev, mesh, params = build("Attach", taper=0.9, l1_start=0.05)
    level = attr(mesh, "bbt_fol_level")
    rad = attr(mesh, "bbt_fol_rad")
    zs = [v.co.z for v in mesh.vertices]
    l1 = [(zs[i], rad[i]) for i in range(len(rad)) if level[i] == 1]
    low = [r for z, r in l1 if z < 0.35 * params["height"]]
    high = [r for z, r in l1 if z > 0.75 * params["height"]]
    ev.to_mesh_clear()
    check("a branch low on the trunk is thicker than one near the top",
          low and high and max(low) > 1.4 * max(high),
          f"max radius {max(low):.4f} low against {max(high):.4f} high"
          if low and high else f"{len(low)} low, {len(high)} high")


def check_scale_invariance():
    """The same params at two sizes must give geometrically SIMILAR trees.

    This is what a species preset means: "the same shrub, waist high" has to be the same shrub. The
    bend amplitude used to be in metres, so it was invisible at 20 m and catastrophic at 0.1 m --
    measured, a grass tuft came back 1.7 m tall and 2 m wide from a 0.10 m stem, which is a plausible
    bush and not a tuft.
    """
    def proportions(height):
        ev, mesh, _ = build("Scale", height=height, trunk_radius=0.02 * height, cards=3,
                            card_size=0.03 * height)
        zs = [v.co.z for v in mesh.vertices]
        span = max(max(v.co[a] for v in mesh.vertices) - min(v.co[a] for v in mesh.vertices)
                   for a in (0, 1))
        out = (span / max(zs), max(zs) / height)
        ev.to_mesh_clear()
        return out

    big, small = proportions(20.0), proportions(0.5)
    check("the same tree at 1/40 the size keeps its proportions",
          abs(big[0] - small[0]) < 0.05 and abs(big[1] - small[1]) < 0.05,
          f"width/height {big[0]:.3f} at 20 m against {small[0]:.3f} at 0.5 m; "
          f"reach {big[1]:.3f} against {small[1]:.3f}")


def check_crown():
    """The shipped defaults must be the narrow conifer this track was started by, not a broadleaf.

    F1's defaults grew a 13 m crown on a 20 m trunk. Recorded as a check rather than a comment
    because it is a number that drifts silently the moment anyone tunes a level default.
    """
    ev, mesh, params = build("Crown", cards=0)
    span = max(max(v.co[a] for v in mesh.vertices) - min(v.co[a] for v in mesh.vertices)
               for a in (0, 1))
    ev.to_mesh_clear()
    ratio = span / params["height"]
    check("the shipped defaults grow a narrow conifer", ratio < 0.45,
          f"crown {span:.2f} m on a {params['height']:.0f} m trunk, width/height {ratio:.2f}")


def rings(mesh, level, profile):
    """Per ring of one level: (mean z, mean radius about the ring's own axis, per-vertex radii).

    Curve to Mesh emits a curve's rings in order and one ring per curve point, so a level's flagged
    vertices arrive as consecutive runs of exactly `profile`. This is how a radius is read back off a
    swept mesh -- `bbt_fol_rad` says what the recipe MEANT, and the geometry is what it did.
    """
    levels = attr(mesh, "bbt_fol_level")
    idx = [i for i, v in enumerate(levels) if v == level]
    out = []
    for start in range(0, len(idx), profile):
        run = idx[start:start + profile]
        if len(run) != profile:
            break
        cx = sum(mesh.vertices[i].co.x for i in run) / profile
        cy = sum(mesh.vertices[i].co.y for i in run) / profile
        cz = sum(mesh.vertices[i].co.z for i in run) / profile
        radii = [math.hypot(mesh.vertices[i].co.x - cx, mesh.vertices[i].co.y - cy) for i in run]
        out.append((cz, sum(radii) / profile, radii))
    return out


def radius_deviation(mesh):
    """(peak, rms) of |measured radius / bbt_fol_rad - 1| over the trunk. What `Lobe` is measured in.

    Against the recipe's own stored radius rather than against a second build, so it answers the
    question the knob's NAME asks -- how far from its nominal radius is this ring pushed -- and does
    it per vertex, which is where the lobing lives.
    """
    rad = attr(mesh, "bbt_fol_rad")
    levels = attr(mesh, "bbt_fol_level")
    idx = [i for i, v in enumerate(levels) if v == 0]
    devs = []
    for start in range(0, len(idx), 6):
        run = idx[start:start + 6]
        if len(run) != 6:
            break
        cx = sum(mesh.vertices[i].co.x for i in run) / 6.0
        cy = sum(mesh.vertices[i].co.y for i in run) / 6.0
        for i in run:
            if rad[i] > 1e-4:
                r = math.hypot(mesh.vertices[i].co.x - cx, mesh.vertices[i].co.y - cy)
                devs.append(abs(r / rad[i] - 1.0))
    if not devs:
        return 0.0, 0.0
    return max(devs), (sum(d * d for d in devs) / len(devs)) ** 0.5


def card_anchor_gaps(mesh, faces):
    """Per card, how far its base-edge MIDPOINT sits from ITS OWN anchor point.

    The F6 replacement for `card_base_gaps`, and strictly better than what it replaced. That helper
    found the nearest TIP, which was exact while a card could only grow on a tip and is meaningless
    now that cards grow along a limb: the nearest tip to a mid-twig card is not the point it grew on.
    `bbt_fol_anchor` is that point by construction -- the skeleton position the card was instanced
    on, written in `_tag` and inherited through the duplicate -- so this needs no nearest-neighbour
    search and cannot be fooled by a dense crown, which is the trap `card_base_gaps` documents.
    """
    if not faces or not mesh.uv_layers:
        return []
    anchor = mesh.attributes.get("bbt_fol_anchor")
    if anchor is None:
        return []
    uv = mesh.uv_layers[0].data
    gaps = []
    for i in faces:
        face = mesh.polygons[i]
        # The base pair by UV, for the reason `card_base_gaps` records: the card's own v runs 0 at
        # its base, and picking corners by proximity is wrong in a dense crown.
        pairs = sorted(zip((uv[c].uv[1] for c in face.loop_indices), face.vertices))
        base = [mesh.vertices[v].co for _v, v in pairs[:2]]
        mid = [(base[0][a] + base[1][a]) / 2.0 for a in range(3)]
        at = anchor.data[pairs[0][1]].vector
        gaps.append(math.dist(mid, (at.x, at.y, at.z)))
    return gaps


def check_shape():
    """F6's four wood terms: the power taper, the root flare, the branch collar and the lobing.

    Every one of them is INERT at its default, and the first check is that claim: F1 through F5
    measured a tree with none of these terms and all of those numbers are still the contract, so a
    default build has to come back at F1's 8,508 verts / 7,098 faces with a perfectly circular
    cross-section. The shape they describe arrives through the species presets.

    What each buys is measured against the geometry rather than against the stored radius, because
    "the recipe set a radius" and "the sweep used it" are the two different things F2 found apart
    (Curve to Mesh stopped applying the radius attribute implicitly in 4.0, and every F1 tree was a
    uniform 1 m tube because of it).
    """
    ev, mesh, params = build("ShapeOff", cards=0)
    profile = params["profile_segments"]
    check("with every F6 term at its default the tree is F1's tree exactly",
          (len(mesh.vertices), len(mesh.polygons)) == (8508, 7098),
          f"{len(mesh.vertices)} verts, {len(mesh.polygons)} faces against 8508 / 7098")
    peak, _rms = radius_deviation(mesh)
    check("and its cross-section is a circle to a ten-thousandth", peak < 1e-3,
          f"peak radius deviation {peak:.2e} of the local radius")
    flat = rings(mesh, 0, profile)
    ev.to_mesh_clear()

    # 1. The power taper. Above 1 the radius falls slowly at first, so the bole stays near
    #    cylindrical and then tapers into the crown -- the difference between a tree and a cone. The
    #    TIP must not move: `Taper` still says where the taper ends up, `Taper Curve` only says how
    #    it gets there, and a knob that changed both would be two knobs.
    ev, mesh, _ = build("TaperCurve", cards=0, taper_curve=2.0)
    curved = rings(mesh, 0, profile)
    ev.to_mesh_clear()
    mid = len(flat) // 2
    check("Taper Curve above 1 keeps the bole thick at mid-height",
          curved[mid][1] > flat[mid][1] * 1.3,
          f"mid radius {flat[mid][1]:.4f} m linear against {curved[mid][1]:.4f} m at 2.0")
    check("and leaves the tip radius exactly where Taper put it",
          abs(curved[-1][1] - flat[-1][1]) < 1e-4,
          f"tip {flat[-1][1]:.5f} m against {curved[-1][1]:.5f} m")

    # 2. The root flare, on the trunk only, over FLARE_SPAN. Local by construction: a flare that
    #    reached a quarter of the way up the trunk would just be a different Taper.
    ev, mesh, _ = build("Flare", cards=0, flare=0.8)
    flared = rings(mesh, 0, profile)
    ev.to_mesh_clear()
    check("Flare swells the base by exactly what it says",
          abs(flared[0][1] / flat[0][1] - 1.8) < 0.02,
          f"base {flat[0][1]:.4f} m to {flared[0][1]:.4f} m, ratio {flared[0][1] / flat[0][1]:.3f}")
    quarter = len(flat) // 4
    check("and reaches nowhere near a quarter of the way up",
          abs(flared[quarter][1] - flat[quarter][1]) < 1e-4,
          f"at 25% height {flat[quarter][1]:.5f} m against {flared[quarter][1]:.5f} m")

    # 3. The branch collar: the same term on a branch, over a longer span. The invariant that matters
    #    is that a collar CANNOT poke through the limb it grows from -- the base radius is the
    #    parent's own radius times a ratio well under 1, so there is headroom, and this is the number
    #    that says how much.
    ev, mesh, _ = build("Collar", cards=0, collar=1.0)
    l1_plain = rings(mesh, 1, profile)
    parent = rings(mesh, 0, profile)
    ev.to_mesh_clear()
    ev2, mesh2, _ = build("CollarOff", cards=0)
    l1_off = rings(mesh2, 1, profile)
    ev2.to_mesh_clear()
    check("Collar swells a branch where it leaves its parent",
          l1_plain[0][1] > l1_off[0][1] * 1.5,
          f"L1 base {l1_off[0][1]:.4f} m to {l1_plain[0][1]:.4f} m")
    check("and a swollen collar is still thinner than the trunk it grows out of",
          max(r[1] for r in l1_plain[:2]) < max(p[1] for p in parent),
          f"widest collar {max(r[1] for r in l1_plain[:2]):.4f} m against trunk "
          f"{max(p[1] for p in parent):.4f} m")

    # 4. The lobing. Three properties, and the third is the one that makes it affordable.
    ev, mesh, _ = build("Lobe", cards=0, lobe=0.25)
    peak, rms = radius_deviation(mesh)
    lobed_verts = len(mesh.vertices)
    ev.to_mesh_clear()
    check("Lobe means the PEAK deviation it says, as a fraction of the local radius",
          abs(peak - 0.25) < 0.03, f"peak {peak:.4f} at Lobe 0.25, rms {rms:.4f}")
    check("and it costs no vertices at all", lobed_verts == 8508,
          f"{lobed_verts} verts against 8508 with the lobing off")

    # And it must not disturb the two things measured on the ring positions it moves: the bark UV
    # (written before the displacement, on the corner domain, so it cannot change) and the cards
    # (joined after it). Byte-equal UVs is the strongest form of the first.
    ev, mesh, params_c = build("LobeUV", cards=4, lobe=0.35)
    uvs_on = [tuple(d.uv) for d in mesh.uv_layers[0].data]
    gaps_on = card_anchor_gaps(mesh, card_faces(mesh))
    ev.to_mesh_clear()
    ev2, mesh2, _ = build("LobeUVOff", cards=4)
    uvs_off = [tuple(d.uv) for d in mesh2.uv_layers[0].data]
    ev2.to_mesh_clear()
    check("the lobing leaves every UV byte-identical", uvs_on == uvs_off,
          f"{len(uvs_on)} corners, {sum(1 for a, b in zip(uvs_on, uvs_off) if a != b)} differ")
    check("and every card still sits on its own anchor under a lobed sweep",
          gaps_on and max(gaps_on) < 1e-5,
          f"worst {max(gaps_on):.2e} m over {len(gaps_on)} cards")

    # 5. The sag, which adds a term in Z to the one place a term in Z is dangerous. The attached-base
    #    invariant is the whole of F1's discipline and a cantilever weighted anything but 0 at the
    #    base breaks it silently -- a tree of floating boughs renders perfectly.
    ev, mesh, _ = build("Sag", cards=0, l1_sag=0.4, l2_sag=0.4, l3_sag=0.4)
    off = attr(mesh, "bbt_fol_off")
    t = attr(mesh, "bbt_fol_t")
    bases = [o for o, f in zip(off, t) if f < 1e-4]
    l1_sagged = [z for z, _r, _rr in rings(mesh, 1, profile)]
    ev.to_mesh_clear()
    check("a sagging limb's base still does not move, at all",
          bases and max(bases) == 0.0, f"{len(bases)} base verts, max offset {max(bases)}")
    ev, mesh, _ = build("SagOff", cards=0)
    l1_level = [z for z, _r, _rr in rings(mesh, 1, profile)]
    ev.to_mesh_clear()
    ev, mesh, _ = build("SagUp", cards=0, l1_sag=-0.4, l2_sag=-0.4, l3_sag=-0.4)
    l1_lifted = [z for z, _r, _rr in rings(mesh, 1, profile)]
    ev.to_mesh_clear()
    mean = lambda xs: sum(xs) / len(xs)
    check("Sag drops the limbs and a NEGATIVE Sag lifts them",
          mean(l1_sagged) < mean(l1_level) - 0.05 < mean(l1_level) + 0.05 < mean(l1_lifted),
          f"mean L1 z: {mean(l1_sagged):.3f} sagging, {mean(l1_level):.3f} level, "
          f"{mean(l1_lifted):.3f} lifted")

    # 6. The lobe's foot. A flared base widens DOWNWARD, so its lowest ring's normal tilts down and a
    #    displacement along it pushes vertices under the ground -- measured at -0.031 m on the shipped
    #    conifer, which is the pack writer's origin-at-the-base invariant broken. LOBE_FOOT fades the
    #    lobing in over the bottom 1.5%, and this is the number that says it worked.
    ev, mesh, _ = build("Foot", cards=0, flare=1.0, lobe=0.3)
    lowest = min(v.co.z for v in mesh.vertices)
    ev.to_mesh_clear()
    check("a flared, lobed base does not sink below the ground plane", abs(lowest) < 0.005,
          f"lowest vertex {lowest:+.5f} m")


def check_leaves():
    """F6's leaf placement: cards ALONG the young wood rather than only on its tips.

    The tip-only rule is why the review read these trees as bare sticks with pom-poms: a 3 m bough
    carried its whole leaf allowance in one cluster at the far end, and the grass tuft came back as
    fourteen woody dowels with a sprig glued to each. Two knobs replace it and BOTH are inert at
    their defaults, so F2's 940-cards-on-235-tips is still the contract at the recipe's floor.
    """
    # 1. The defaults reproduce the old selection exactly, which is the only reason F2's card
    #    measurements are still quotable. `Leaf Start` 1 selects a limb's last point, and a limb's
    #    last point IS its tip; `Leaf Level` 0 is every level, including the trunk's own tip.
    ev, mesh, params = build("LeafDefault", cards=4, card_size=0.5)
    tip_cards = len(card_faces(mesh))
    ev.to_mesh_clear()
    check("at the default Leaf Start and Leaf Level the cards are still tip-only",
          tip_cards == (1 + 9 + 45 + 180) * 4, f"{tip_cards} cards against 235 tips x 4")

    # 2. Leaf Start below 1 distributes them, and the count is the arithmetic rather than a surprise:
    #    `branch_segments` points per twig, of which those past Leaf Start qualify.
    ev, mesh, _ = build("LeafAlong", cards=4, card_size=0.5, leaf_level=3, leaf_start=0.4)
    along = card_faces(mesh)
    gaps = card_anchor_gaps(mesh, along)
    anchors = {tuple(round(c, 5) for c in mesh.attributes["bbt_fol_anchor"].data[v].vector)
               for f in along for v in mesh.polygons[f].vertices}
    ev.to_mesh_clear()
    check("Leaf Start below 1 puts cards along the twigs and not only on their ends",
          len(along) > tip_cards, f"{len(along)} cards against {tip_cards} tip-only")
    check("on more than one point per twig", len(anchors) > 180,
          f"{len(anchors)} distinct anchor points over 180 level-3 twigs")
    check("and every one of them still sits on the point it grew on",
          gaps and max(gaps) < 1e-5, f"worst {max(gaps):.2e} m over {len(gaps)} cards")

    # 3. Leaf Level excludes the wood that should carry nothing. A card on a bole is the failure the
    #    knob exists to prevent, and it is invisible in a crown -- so it is measured on the trunk's
    #    own level, which has exactly one curve and cannot hide anything.
    ev, mesh, _ = build("LeafDeep", cards=4, card_size=0.5, leaf_level=3, leaf_start=0.2)
    levels_with_cards = {mesh.attributes["bbt_fol_level"].data[v].value
                         for f in card_faces(mesh) for v in mesh.polygons[f].vertices}
    ev.to_mesh_clear()
    check("Leaf Level keeps leaves off the trunk and the boughs entirely",
          levels_with_cards == {3}, f"cards found on levels {sorted(levels_with_cards)}")

    # 4. The clamp, which is the one that would have shipped a silent defect. A LOD rung rebuilds at
    #    `levels - 1` (docs/FOLIAGE.md 2.6), so a species asking for leaves on level 3 asks LOD1 for a
    #    level that does not exist. Unclamped the selection matches nothing and the rung comes back as
    #    bare wood with its canopy gone -- and a rung is exactly the thing nobody looks at closely.
    ev, mesh, _ = build("LeafClamp", levels=2, cards=4, card_size=0.5, leaf_level=3, leaf_start=0.4)
    clamped = card_faces(mesh)
    clamped_levels = {mesh.attributes["bbt_fol_level"].data[v].value
                      for f in clamped for v in mesh.polygons[f].vertices}
    ev.to_mesh_clear()
    check("Leaf Level is clamped to the build's depth, so a shallower LOD rung is not bald",
          clamped and clamped_levels == {2}, f"{len(clamped)} cards, on levels "
          f"{sorted(clamped_levels)}")

    # 5. The atlas fallback. A card whose set does not resolve has no cutout AND no albedo, and its
    #    tint is white, so the canopy renders as opaque white rectangles -- measured on the first F6
    #    run against a generated atlas the resolver could not see. Bark has no such cliff, which is
    #    why only the atlas falls back (`_atlas_set`).
    apply_op({"op": "build_geonodes", "recipe": "foliage", "name": "Fallback",
              "params": dict(BASE, cards=4, atlas="no_such_atlas_anywhere"), "reset": True})
    card = bpy.data.materials.get("M_Fallback Leaf")
    bsdf = next((n for n in card.node_tree.nodes if n.bl_idname == "ShaderNodeBsdfPrincipled"),
                None) if card and card.node_tree else None
    check("a species naming an atlas no pack provides still gets a cutout, not white rectangles",
          bsdf is not None and bsdf.inputs["Alpha"].is_linked,
          "Alpha is linked" if bsdf is not None and bsdf.inputs["Alpha"].is_linked
          else "Alpha is NOT linked")


def check_cards():
    """The leaf cards: one per tip per Cards, two triangles each, attached, and cell-varied."""
    ev, mesh, params = build("Cards", cards=4, card_size=0.5)
    faces = card_faces(mesh)
    tips = tip_points(mesh, params)
    expected_tips = 1 + 9 + 45 + 180
    check("the tip rings resolve to one point per curve", len(tips) == expected_tips,
          f"{len(tips)} tips against {expected_tips} curves")
    check("one card per tip per Cards", len(faces) == expected_tips * 4,
          f"{len(faces)} cards against {expected_tips} tips x 4")
    check("a card is two triangles, i.e. one quad",
          all(len(mesh.polygons[i].vertices) == 4 for i in faces))

    # Attached, the card analogue of F1's base-offset invariant. The quad stands ON the origin
    # before it is instanced, so its base edge straddles the tip; a card that detached would still
    # render, hanging in the air beside its twig, which is exactly the kind of failure that reads
    # as "a bit sparse" rather than as a bug.
    gaps = card_base_gaps(mesh, faces, tips)
    check("every card's base sits exactly on a tip", gaps and max(gaps) < 1e-4,
          f"worst base gap {max(gaps):.2e} m over {len(gaps)} cards" if gaps else "no cards")
    # ... and the card has real extent, so "attached" was not achieved by collapsing it to a point.
    corners = [tuple(mesh.vertices[v].co) for i in faces for v in mesh.polygons[i].vertices]
    reach = float(nearest(corners, tips)[0].max()) if corners else None
    check("but a card reaches away from its tip", reach is not None and reach > 0.2,
          f"furthest corner {reach:.3f} m from a tip" if reach else "none")

    areas = [mesh.polygons[i].area for i in faces]
    check("cards are all one size", areas and max(areas) - min(areas) < 1e-5,
          f"{min(areas):.4f}..{max(areas):.4f} m2" if areas else "none")
    normals = {tuple(round(c, 2) for c in mesh.polygons[i].normal) for i in faces}
    check("cards face many directions, so a spray is not a stack",
          len(normals) > 0.8 * len(faces), f"{len(normals)} distinct normals over {len(faces)} cards")
    ev.to_mesh_clear()

    # Cards 0 is the off switch: a bare skeleton, not a crash and not a hidden card.
    ev0, mesh0, _ = build("Bare", cards=0)
    check("Cards 0 leaves a bare skeleton", not card_faces(mesh0), f"{len(card_faces(mesh0))} cards")
    ev0.to_mesh_clear()


def check_atlas():
    """The atlas cell pick: every cell used, roughly evenly, and the grid size actually read."""
    ev, mesh, params = build("Atlas", cards=4, atlas_cols=2, atlas_rows=2)
    faces = set(card_faces(mesh))
    cells = mesh.attributes.get("bbt_fol_cell")
    card_verts = {v for f in mesh.polygons if f.index in faces for v in f.vertices}
    drawn = Counter(cells.data[v].value for v in card_verts) if cells else Counter()
    check("every atlas cell is drawn", sorted(drawn) == [0, 1, 2, 3], f"{sorted(drawn)}")
    if drawn:
        share = max(drawn.values()) / min(drawn.values())
        # A hash can clump; 2x tells a working draw from one that keys off something constant.
        check("the cells are drawn roughly evenly", share < 2.0,
              f"most-used / least-used = {share:.2f} over {sum(drawn.values())} card verts")
    # All four corners of one card must share its cell, or a card straddles two atlas cells.
    per_card = [{cells.data[v].value for v in mesh.polygons[i].vertices} for i in list(faces)[:200]]
    check("all four corners of a card draw the SAME cell", all(len(s) == 1 for s in per_card),
          f"{sum(1 for s in per_card if len(s) != 1)} split cards")
    ev.to_mesh_clear()

    # A 4x1 grid must use cells 0..3 and no more, so the grid params are read rather than baked in.
    ev4, mesh4, _ = build("Atlas41", cards=4, atlas_cols=4, atlas_rows=1)
    f4 = set(card_faces(mesh4))
    c4 = mesh4.attributes.get("bbt_fol_cell")
    verts4 = {v for f in mesh4.polygons if f.index in f4 for v in f.vertices}
    drawn4 = sorted({c4.data[v].value for v in verts4}) if c4 else []
    check("the atlas grid params are read, not baked in", drawn4 == [0, 1, 2, 3], f"{drawn4}")
    bounds = uv_bounds(mesh4, list(f4))
    # 4 columns, 1 row: v spans the whole cell height, u only a quarter of the atlas per card.
    check("a 4x1 atlas puts each card in a quarter-width cell",
          bounds is not None and bounds[1] <= 1.0 + 1e-5 and bounds[3] <= 1.0 + 1e-5,
          f"u {bounds[0]:.3f}..{bounds[1]:.3f}, v {bounds[2]:.3f}..{bounds[3]:.3f}"
          if bounds else "no UVs")
    ev4.to_mesh_clear()


def check_uvs():
    """UVs exist as a real UV LAYER, cards land inside the atlas, and bark is scaled in metres."""
    ev, mesh, params = build("UV", cards=4)
    check("the mesh carries a UV layer", [u.name for u in mesh.uv_layers] == ["UVMap"],
          f"{[u.name for u in mesh.uv_layers]}")
    leaf = set(card_faces(mesh))
    wood = [f.index for f in mesh.polygons if f.index not in leaf]
    cards = uv_bounds(mesh, list(leaf))
    check("card UVs sit inside the atlas (0..1)",
          cards is not None and cards[0] >= -1e-5 and cards[1] <= 1.0 + 1e-5
          and cards[2] >= -1e-5 and cards[3] <= 1.0 + 1e-5,
          f"u {cards[0]:.3f}..{cards[1]:.3f}, v {cards[2]:.3f}..{cards[3]:.3f}" if cards else "none")
    # The bark UV is deliberately NOT 0..1: it is metres / Bark Scale, so a twig and a trunk carry
    # the same grain. What must hold is that it tiles at the rate asked for.
    bark = uv_bounds(mesh, wood)
    check("bark UVs are finite and non-negative",
          bark is not None and all(math.isfinite(b) and b >= -1e-5 for b in bark),
          f"u {bark[0]:.2f}..{bark[1]:.2f}, v {bark[2]:.2f}..{bark[3]:.2f}" if bark else "none")
    ev.to_mesh_clear()

    ev2, mesh2, _ = build("UVScale", cards=0, bark_scale=1.0)
    coarse = uv_bounds(mesh2, [f.index for f in mesh2.polygons])
    ev2.to_mesh_clear()
    ev3, mesh3, _ = build("UVScale2", cards=0, bark_scale=0.5)
    fine = uv_bounds(mesh3, [f.index for f in mesh3.polygons])
    ev3.to_mesh_clear()
    check("halving Bark Scale doubles the tiling", coarse and fine
          and abs(fine[3] / coarse[3] - 2.0) < 0.02,
          f"v max {coarse[3]:.2f} at 1.0 m/tile, {fine[3]:.2f} at 0.5 m/tile")
    # V is metres along the limb, so on a 20 m trunk at 1 m per tile it has to reach about 20.
    check("bark V is metres along the limb, not a 0..1 factor",
          coarse is not None and coarse[3] > 15.0,
          f"v max {coarse[3]:.2f} on a 20 m trunk at 1 m per tile" if coarse else "none")


def check_materials():
    """Two materials, bark then cards, every face carrying one, and the cutout reaching the BSDF."""
    ev, mesh, _ = build("Mats", cards=4)
    names = [m.name if m else None for m in mesh.materials]
    # Measured on Blender 5.2: slot 0 is the base mesh's own empty slot, which Set Material appends
    # after and which no face references. What matters is that both real materials are there, in a
    # deterministic order, and that every face points at one of them.
    real = [n for n in names if n]
    check("the tree carries exactly two materials", len(real) == 2, f"{names}")
    check("bark comes before the cards", real == ["M_Mats Bark", "M_Mats Leaf"], f"{real}")
    used = Counter(f.material_index for f in mesh.polygons)
    leaf = set(card_faces(mesh))
    bark_slot, card_slot = names.index(real[0]), names.index(real[1])
    check("every face is shaded", set(used) == {bark_slot, card_slot}, f"{dict(used)}")
    check("the card faces are the ones on the card material",
          all(mesh.polygons[i].material_index == card_slot for i in leaf)
          and all(f.material_index == bark_slot for f in mesh.polygons if f.index not in leaf),
          f"{used[card_slot]} card faces, {used[bark_slot]} bark faces")
    ev.to_mesh_clear()

    bark = bpy.data.materials["M_Mats Bark"]
    card = bpy.data.materials["M_Mats Leaf"]
    for name, mat in (("bark", bark), ("card", card)):
        check(f"the {name} material is a BobShader", materials.master_type(mat) == "surface",
              str(materials.master_type(mat)))
    bsdf = next(n for n in card.node_tree.nodes if n.bl_idname == "ShaderNodeBsdfPrincipled")
    alpha = bsdf.inputs["Alpha"]
    src = alpha.links[0] if alpha.is_linked else None
    check("alpha reaches the card's Principled",
          src is not None and src.from_node.bl_idname == "ShaderNodeTexImage",
          f"{src.from_node.bl_idname}.{src.from_socket.name}" if src else "not linked")
    check("and it is the atlas image's own cutout channel",
          src is not None and src.from_node.image is not None
          and os.path.basename(src.from_node.image.filepath).startswith("leaf_atlas"),
          os.path.basename(src.from_node.image.filepath) if src and src.from_node.image else "none")
    check("the card is two-sided", not card.use_backface_culling)
    if hasattr(card, "surface_render_method"):
        check("the card renders as a cutout", card.surface_render_method == "DITHERED",
              card.surface_render_method)
    # The bark keeps its alpha alone: a trunk with a cutout would be a hole in the tree.
    bark_bsdf = next(n for n in bark.node_tree.nodes if n.bl_idname == "ShaderNodeBsdfPrincipled")
    check("the bark material has no cutout", not bark_bsdf.inputs["Alpha"].is_linked)


def check_atlas_ships():
    """The placeholder atlas is IN the block-out pack and carries real cutout alpha, so the phase
    never waits on a ComfyUI server (docs/FOLIAGE.md 4.4)."""
    maps = assets.texture_set_maps("leaf_atlas_blockout")
    check("the block-out leaf atlas resolves off the pack search path", bool(maps.get("basecolor")),
          maps.get("basecolor", "missing"))
    if not maps.get("basecolor"):
        return
    img = bpy.data.images.load(maps["basecolor"], check_existing=True)
    check("the atlas has an alpha channel", img.channels == 4 and img.depth in (32, 64),
          f"{img.channels} channels, depth {img.depth}")
    px = list(img.pixels)
    alpha = px[3::4]
    clear = sum(1 for a in alpha if a < 0.05) / len(alpha)
    opaque = sum(1 for a in alpha if a > 0.95) / len(alpha)
    # The redwood run's whole finding was that generated meshes come back opaque. A leaf atlas that
    # is mostly opaque is the same failure in a texture, and it renders as green rectangles.
    check("the atlas is mostly cutout, not a filled square", clear > 0.5,
          f"{clear * 100:.1f}% transparent, {opaque * 100:.1f}% opaque")
    check("and it is not blank either", opaque > 0.02, f"{opaque * 100:.1f}% opaque")
    bpy.data.images.remove(img)


def check_bark_uv():
    """The bark U must be uniform around every ring, and the bark material must READ the UVs.

    Two F3 fixes, both of which were invisible at F2 because nothing was textured yet.

    The seam: the profile circle is cyclic, so its spline parameter ran 0 .. 1-1/n and jumped back to
    0 on the last quad of every ring, giving one column per limb that carried the whole texture
    reversed and squeezed. Measured before the fix on a 6-sided profile, the worst face spanned 3.927
    of a tile in U against a 0.035 median -- a factor of 112.

    Measured on the RAW profile parameter, recovered from the written UV by dividing out the metres
    term (U is `u * 2*pi*radius / Bark Scale`, all of which the gate can read back). Measuring the UV
    directly instead does not work, and finding that out mattered: the U of a TAPERING limb spans a
    genuinely large range on any face near the wrap, because the circumference it is scaled by differs
    between the quad's two rings. That is shear, it is inherent to a metres-based cylindrical UV, and
    it is not the seam -- a check that cannot tell them apart reported a 5x miss on a fixed graph.
    In the raw parameter every face is entitled to exactly 1/n and the seam is unmistakable.
    """
    ev, mesh, params = build("BarkUV", cards=0, bark_scale=1.0)
    profile = params["profile_segments"]
    rad = attr(mesh, "bbt_fol_rad")
    uv = mesh.uv_layers[0].data
    share = 1.0 / profile
    worst, over, measured = 0.0, 0, 0
    for f in mesh.polygons:
        raw = []
        for c in f.loop_indices:
            r = rad[mesh.loops[c].vertex_index]
            if r > 1e-6:
                raw.append(uv[c].uv[0] / (2.0 * math.pi * r))   # Bark Scale is 1.0 in this build
        if len(raw) != len(f.loop_indices):
            continue
        measured += 1
        span = max(raw) - min(raw)
        worst = max(worst, span)
        if span > 1.6 * share:
            over += 1
    ev.to_mesh_clear()
    check("no face carries more of the profile than its 1/n share of the ring",
          measured and worst < 1.6 * share,
          f"worst face spans {worst:.4f} of the profile against a {share:.4f} share, "
          f"{over}/{measured} face(s) over 1.6x")
    # ... and the share is real rather than zero, or the check above passes on a dead UV.
    check("and the U does go round the tube", worst > 0.5 * share,
          f"worst span {worst:.4f}, share {share:.4f}")

    # The bark material has to sample the UV it was given. F2 assigned the bark set with box
    # projection, so the metres-based UV reached nothing and the grain followed the world axes.
    bark = bpy.data.materials["M_BarkUV Bark"]
    _sets, box = materials.stored_sets(bark, 1)
    check("bark is UV-projected, not projected through world space", not box, f"box={box}")


def check_grain():
    """The bark grain-direction measure, on images whose answer is known. No server needed.

    The measure earns its own check because it is the one F3 added and because the thing it replaces
    (the seam ratio) cannot see direction at all. Both failure modes are covered: a stripe pattern in
    the wrong direction, and no direction whatsoever.
    """
    import numpy as np

    from bob_blender_tools.core import comfy_maps

    x = np.arange(256, dtype=np.float32)
    vertical = np.tile((np.sin(x / 6.0) * 60 + 128).astype(np.uint8), (256, 1))
    horizontal = vertical.T.copy()
    noise = (np.random.default_rng(0).random((256, 256)) * 255).astype(np.uint8)

    v = comfy_maps.grain_report(vertical)
    check("vertical grain measures as vertical", v["off_vertical_deg"] < 2.0
          and v["coherence"] > 0.9,
          f"{v['off_vertical_deg']:.1f} deg off vertical, coherence {v['coherence']:.3f}")
    h = comfy_maps.grain_report(horizontal)
    check("horizontal grain measures as horizontal", h["off_vertical_deg"] > 88.0,
          f"{h['off_vertical_deg']:.1f} deg off vertical")
    n = comfy_maps.grain_report(noise)
    # Coherence is the half that catches "no grain at all"; the angle of noise is meaningless.
    check("an isotropic image has no coherent grain", n["coherence"] < 0.05,
          f"coherence {n['coherence']:.4f}")
    check("and its per-block axes disagree", n["block_spread_deg"] > 20.0,
          f"block spread {n['block_spread_deg']:.1f} deg")

    # The atlas composer, on synthetic sprites: a wedge narrow at one end, at four orientations. What
    # must hold is that all four come out upright with the NARROW end at the bottom, because that is
    # the end a card attaches by. A bounding box cannot see this -- `base_taper` is what does.
    sprites = []
    for angle in (0.0, 45.0, 130.0, 260.0):
        sprite = np.zeros((128, 128, 4), np.uint8)
        ux, uy = math.cos(math.radians(angle)), math.sin(math.radians(angle))
        for s in range(-45, 45):
            half = int(2 + (s + 45) * 0.22)
            cy, cx = 64 + uy * s, 64 + ux * s
            for j in range(-half, half + 1):
                yy, xx = int(cy - ux * j), int(cx + uy * j)
                if 0 <= yy < 128 and 0 <= xx < 128:
                    sprite[yy, xx, :3] = (40, 120, 40)
                    sprite[yy, xx, 3] = 255
        sprites.append(sprite)
    base, opacity = comfy_maps.atlas_compose(sprites, 2, 2, 512)
    cells = comfy_maps.atlas_cells(opacity, 2, 2)
    check("every composed cell carries a sprite", all(c["opaque"] > 0.02 for c in cells),
          ", ".join(f"{c['opaque'] * 100:.1f}%" for c in cells))
    check("every composed sprite reaches its cell's bottom edge",
          all(c["reaches_base"] for c in cells),
          str([c["cell"] for c in cells if not c["reaches_base"]]))
    check("and stands on its NARROW end, whatever angle it was generated at",
          all(c["base_taper"] < 0.6 for c in cells),
          ", ".join(f"{c['base_taper']:.2f}" for c in cells))
    # The transparent region's colour is not "don't care": bilinear filtering blends it into every
    # silhouette, so a studio-white background is a white rim on every needle.
    alpha = opacity.astype(np.float32) / 255.0
    leaf = base[alpha > 0.9].astype(np.float32).mean(axis=0)
    clear = base[alpha < 0.02].astype(np.float32).mean(axis=0)
    check("the transparent region carries leaf colour, so a silhouette has no fringe",
          float(np.abs(leaf - clear).max()) < 12.0,
          f"leaf {leaf.round(1)} against clear {clear.round(1)}")


def check_atlas_sidecar():
    """A generated atlas SET declares its own grid, and the recipe reads it. The [F3] answer.

    F2's interim answer was the two live params alone, which does not scale: an artist assigning a
    4x4 atlas has to know to change two numbers, and a card reading 2x2 off a 4x4 samples a quarter
    of the cell it wanted plus slices of three neighbours -- which renders as foliage, so nothing
    catches it. The params stay as the override, which is the other half of this check.
    """
    import json as _json

    from bob_blender_tools.core import comfy_maps

    pack = os.path.join(OUT, "sidecar_pack")
    if os.path.isdir(pack):
        shutil.rmtree(pack)
    set_dir = os.path.join(pack, "textures", "atlas_4x4_probe")
    os.makedirs(set_dir)
    # A real set, because the point is that the resolver and the recipe read the same folder the
    # generator writes. Sixteen visibly different sprites, so a wrong grid is measurable downstream.
    sprites = []
    for i in range(16):
        s = np.zeros((64, 64, 4), "uint8")
        s[48 - i:60, 28:36, :3] = (30, 100 + i * 8, 40)
        s[48 - i:60, 28:36, 3] = 255
        sprites.append(s)
    base, opacity = comfy_maps.atlas_compose(sprites, 4, 4, 512)
    comfy_maps.write_png(os.path.join(set_dir, "atlas_4x4_probe_basecolor.png"), base)
    comfy_maps.write_png(os.path.join(set_dir, "atlas_4x4_probe_opacity.png"), opacity)
    with open(os.path.join(set_dir, "meta.json"), "w") as fh:
        _json.dump({"atlas": {"cols": 4, "rows": 4}}, fh)
    assets.add_pack_root(pack)

    check("the probe atlas resolves off the search path",
          bool(assets.texture_set_maps("atlas_4x4_probe").get("opacity")),
          str(sorted(assets.texture_set_maps("atlas_4x4_probe"))))
    check("the set declares its own grid", assets.atlas_grid("atlas_4x4_probe") == (4, 4),
          str(assets.atlas_grid("atlas_4x4_probe")))
    check("a set with no sidecar declares nothing rather than guessing",
          assets.atlas_grid("leaf_atlas_blockout") is None,
          str(assets.atlas_grid("leaf_atlas_blockout")))

    # The recipe defaults to the declared grid with no atlas_cols/atlas_rows given at all.
    ev, mesh, _ = build("Sidecar", cards=2, atlas="atlas_4x4_probe")
    got = live_grid(bpy.data.objects["Sidecar"])
    check("the recipe defaults its grid to what the set declared", got == (4, 4), f"{got}")
    cells = mesh.attributes.get("bbt_fol_cell")
    faces = set(card_faces(mesh))
    verts = {v for f in mesh.polygons if f.index in faces for v in f.vertices}
    drawn = sorted({cells.data[v].value for v in verts}) if cells else []
    check("and draws cells across the whole 4x4 grid", len(drawn) > 8 and max(drawn) <= 15,
          f"{len(drawn)} distinct cells, max {max(drawn) if drawn else 'none'}")
    ev.to_mesh_clear()

    # ... and an explicit param still overrides it, which is what the brief asked to keep.
    ev2, mesh2, _ = build("SidecarOverride", cards=2, atlas="atlas_4x4_probe",
                          atlas_cols=2, atlas_rows=1)
    got2 = live_grid(bpy.data.objects["SidecarOverride"])
    check("an explicit grid param still overrides the sidecar", got2 == (2, 1), f"{got2}")
    ev2.to_mesh_clear()


def check_species():
    """Every shipped species preset loads, validates clean, and builds at a believable size."""
    names = assets.list_foliage_species()
    check("the block-out pack ships species presets",
          set(names) >= {"conifer", "broadleaf", "shrub", "grass_tuft"}, f"{names}")
    # A preset is DATA in a pack, not a dict in the recipe, so a pack can ship a species. Which
    # makes an unreadable or mistyped one a live failure mode the reader has to handle.
    check("an unknown species reads as nothing rather than raising",
          assets.foliage_species("no_such_species") == {})

    # F3: the tree species NAME the bark they want, and no placeholder bark set ships (a hand-made
    # one would hide the grain-direction problem generation actually has). So a bark set that does
    # not resolve is the ordinary pre-generation state and not an authoring mistake, which is why
    # `foliage_missing_sets` is separate from the rest of the validator.
    for name in ("conifer", "broadleaf"):
        declared = assets.foliage_species(name)["params"].get("bark_set")
        check(f"species '{name}' names the bark set it wants", bool(declared), str(declared))
        reported = any(k == "bark_set" for k, _l, _v in assets.foliage_missing_sets(name))
        resolves = assets.texture_set_dir(str(declared)) is not None
        check(f"'{name}' reports its bark missing exactly when it is",
              reported != resolves, f"resolves={resolves}, reported missing={reported}")
    for kind in ("trees", "plants", "grass"):
        check(f"a species is routable from the '{kind}' scatter kind",
              assets.foliage_species_for_kind(kind) is not None,
              str(assets.foliage_species_for_kind(kind)))

    # Sizes, because a preset that builds is not a preset that is right: F1's own defaults built
    # perfectly and grew the wrong tree, and the same numbers at plant scale grew a 1.7 m tuft.
    expected = {"conifer": (14.0, 30.0, 0.45), "broadleaf": (8.0, 20.0, 1.10),
                "shrub": (0.5, 2.0, 1.40), "grass_tuft": (0.15, 0.8, 1.60)}
    for name in sorted(names):
        spec = assets.foliage_species(name)
        # Missing generatable sets are filtered out and checked above on their own terms: they are a
        # state, not a mistake. Everything else the validator says is an authoring bug.
        warn = [w for w in assets.validate_foliage_species(name) if "missing: textures/" not in w]
        check(f"species '{name}' validates clean", not warn, "; ".join(warn))
        apply_op({"op": "build_geonodes", "recipe": "foliage", "name": f"sp_{name}",
                  "params": dict(spec["params"], seed=11), "reset": True})
        ev = bpy.data.objects[f"sp_{name}"].evaluated_get(bpy.context.evaluated_depsgraph_get())
        mesh = ev.to_mesh()
        zs = [v.co.z for v in mesh.vertices]
        span = max(max(v.co[a] for v in mesh.vertices) - min(v.co[a] for v in mesh.vertices)
                   for a in (0, 1))
        height = max(zs) - min(zs)
        cards = len(card_faces(mesh))
        lo, hi, wide = expected.get(name, (0.0, 1e9, 1e9))
        check(f"species '{name}' is the size it claims to be",
              lo <= height <= hi and span / height <= wide,
              f"{height:.2f} m tall, {span:.2f} m wide (w/h {span / height:.2f}), "
              f"{len(mesh.vertices)} verts, {cards} cards")
        check(f"species '{name}' grows leaves", cards > 0, f"{cards} cards")
        ev.to_mesh_clear()
        # F4: how stiff a plant is belongs to its species the way its taper does, and a preset
        # value that never reaches the modifier is the inert-radius failure again in a new knob.
        for knob, key in (("Sway", "sway"), ("Leaf Flutter", "leaf_flutter")):
            want = spec["params"].get(key)
            got = live_value(bpy.data.objects[f"sp_{name}"], knob)
            check(f"species '{name}' sets its own {knob} and it reaches the modifier",
                  want is not None and got is not None and abs(got - want) < 1e-5,
                  f"preset {want}, modifier {got}")


def check_render():
    """A render that is not flat, in whichever engines this machine has (headless_texset's shape).

    Every structural check above can pass on a tree whose shader graph is wired and dead, and the
    one thing that cannot be faked is a frame with variation in it. Rendered against a plain
    background with a sun, so the variation is the tree.
    """
    os.makedirs(OUT, exist_ok=True)
    apply_op({"op": "build_geonodes", "recipe": "foliage", "name": "Shot",
              "params": dict(assets.foliage_species("broadleaf")["params"], seed=4), "reset": True})
    for obj in list(bpy.data.objects):
        if obj.name != "Shot":
            bpy.data.objects.remove(obj, do_unlink=True)
    tree = bpy.data.objects["Shot"]
    height = max(v.co.z for v in tree.evaluated_get(
        bpy.context.evaluated_depsgraph_get()).to_mesh().vertices)
    bpy.ops.object.light_add(type="SUN", location=(0, 0, height * 2))
    bpy.context.active_object.data.energy = 5.0
    bpy.ops.object.camera_add(location=(0, -height * 1.8, height * 0.5),
                              rotation=(math.pi / 2, 0, 0))
    bpy.context.scene.camera = bpy.context.active_object
    try:
        bpy.ops.preferences.addon_enable(module="cycles")
    except Exception as exc:
        print(f"    could not enable Cycles: {exc}")
    done = set()
    for engine, label in (("BLENDER_EEVEE", "EEVEE"), ("BLENDER_EEVEE_NEXT", "EEVEE"),
                          ("CYCLES", "Cycles")):
        if label in done:
            continue
        var = render_variance(engine, os.path.join(OUT, label.lower()))
        if var is None:
            continue
        done.add(label)
        check(f"{label} renders a tree that is not flat", var > 0.05,
              f"luminance range {var:.4f}")
    for label in ("EEVEE", "Cycles"):
        if label not in done:
            print(f"[SKIP] {label} could not render in this environment")


def check_routing():
    """The Scatter panel's routing (docs/FOLIAGE.md 4.5), which is copy and therefore drifts.

    Held until F2 on purpose: until the cards existed, a panel that sent someone to the Foliage panel for
    plants would have been recommending bare sticks. headless_redwood.py owns the D16 half of this
    (that every noted kind is a real kind, and that trees names dead wood); this owns the half that
    only became true with the cards -- that each note now points somewhere, and that the affordance
    it points at exists and resolves a species for every kind that carries the note.
    """
    from bob_blender_tools.ui import scatter as ui_scatter

    notes = ui_scatter._GEN_KIND_NOTE
    check("every noted kind is a real kind",
          set(notes) <= {"trees", "rocks", "plants", "grass"}, f"{sorted(notes)}")
    # "the Foliage panel", matching that panel's actual HEADER rather than the track's name. The note
    # is a pointer, and a pointer naming something the artist cannot find on screen is the dead end
    # this copy replaced. Checked against the panel's own bl_label below, not against a literal here,
    # or the two drift apart and the check certifies the drift.
    from bob_blender_tools.ui import foliage as ui_foliage

    header = ui_foliage.BBT_PT_foliage.bl_label
    for kind in ("trees", "plants", "grass"):
        check(f"the '{kind}' note points at the {header} panel by its own header",
              f"the {header} panel" in notes.get(kind, ""), notes.get(kind, "(no note)"))
    check("and that header is a plain noun, like every other top-level panel in the category",
          header == "Foliage", header)
    check("the trees note still names dead wood rather than refusing outright",
          "stumps" in notes.get("trees", ""), notes.get("trees", ""))
    check("generated ground clumps are still allowed as filler",
          all("2 m" in notes.get(k, "") for k in ("plants", "grass")),
          f"{notes.get('plants', '')} / {notes.get('grass', '')}")
    check("Grow in Foliage exists as an operator",
          hasattr(bpy.types, "BBT_OT_scatter_grow_foliage")
          or hasattr(ui_scatter, "BBT_OT_scatter_grow_foliage"))
    # The button is only drawn when the kind resolves a species, so a note pointing at the Foliage
    # panel for a kind nothing grows would be a dead end with an affordance-shaped hole where it says so.
    check("every kind the notes route to Foliage can actually be grown",
          all(ui_scatter._foliage_species_for(k) for k in ("trees", "plants", "grass")),
          str({k: ui_scatter._foliage_species_for(k) for k in ("trees", "plants", "grass")}))


def vertices(name, **overrides):
    """(positions, card flags) of a freshly built tree, as plain lists. The shape every wind check
    compares against another: the wind is a vertex displacement, so the vertices are the evidence."""
    ev, mesh, _ = build(name, **overrides)
    pos = [tuple(v.co) for v in mesh.vertices]
    flag = mesh.attributes.get("bbt_fol_card")
    card = [d.value for d in flag.data] if flag else []
    ev.to_mesh_clear()
    return pos, card


def moved(a, b):
    """Per-vertex distance between two builds of the same tree."""
    return [math.dist(p, q) for p, q in zip(a, b)]


def check_wind():
    """The F4 sway: off by default, pinned at the base, and one pass for the wood and the cards.

    Wind is the first thing in this recipe that moves geometry AFTER it is built, which makes it the
    first thing that can quietly take the tree apart. The failures it has to rule out, and none of
    them raises:

    - a default that is not zero, which would move every F1-F3 measurement out from under the gate,
    - a base that is not pinned, which is F1's detached-branch failure at the root of the trunk,
    - a card weighted by its own height instead of its twig's, which parts the canopy from the tree
      by a fraction of a millimetre (measured at 7.7e-05 m before the anchor fix: invisible, and
      exactly the size of defect this track keeps shipping),
    - a flutter that is not gated to the cards, which shakes the trunk at leaf frequency,
    - a clock that is not Scene Time, which renders a different frame every time.
    """
    # Built with no wind param at all, so this is the shipped DEFAULT and not a value the gate
    # chose. Two things have to hold for the F1-F3 numbers above to still mean anything: the knob
    # reads zero, and the tree does not move between frames.
    still, card = vertices("WindOff", cards=4)
    knob = live_value(bpy.data.objects["WindOff"], "Wind")
    ev = bpy.data.objects["WindOff"].evaluated_get(bpy.context.evaluated_depsgraph_get())
    bpy.context.scene.frame_set(41)
    later = [tuple(v.co) for v in bpy.data.objects["WindOff"].evaluated_get(
        bpy.context.evaluated_depsgraph_get()).to_mesh().vertices]
    bpy.context.scene.frame_set(1)
    ev.to_mesh_clear()
    check("Wind defaults to zero", knob == 0.0, f"Wind knob {knob}")
    check("so a tree with no weather is still, frame after frame",
          len(later) == len(still) and max(moved(still, later)) == 0.0,
          f"max movement over 40 frames {max(moved(still, later)) if len(later) == len(still) else 'budget differs'}")
    gale, _ = vertices("WindOn", cards=4, wind=6.0, wind_direction=35.0)
    check("a tree in a gale is the same vertex budget", len(still) == len(gale),
          f"{len(still)} against {len(gale)}")
    delta = moved(still, gale)
    zs = [p[2] for p in still]
    base = max(d for d, z in zip(delta, zs) if z < 0.05)
    top = max(d for d, z in zip(delta, zs) if z > 0.8 * 20.0)
    check("the trunk base does not move at all in a gale", base == 0.0,
          f"max base displacement {base}")
    check("but the crown does", top > 0.1, f"{top:.3f} m at the top")

    # Linear in Wind, so the knob means something and a storm is not a shrug.
    soft, _ = vertices("WindSoft", cards=4, wind=1.0)
    hard, _ = vertices("WindHard", cards=4, wind=2.0)
    ds, dh = max(moved(still, soft)), max(moved(still, hard))
    check("displacement is linear in Wind", ds > 0.0 and abs(dh / ds - 2.0) < 0.05,
          f"{ds:.4f} m at Wind 1, {dh:.4f} m at Wind 2")

    # The anchor invariant: a card rides its twig, so the F2 attachment residual is unchanged.
    ev, mesh, params = build("WindCards", cards=4, wind=6.0, wind_direction=35.0)
    gaps = card_base_gaps(mesh, card_faces(mesh), tip_points(mesh, params))
    ev.to_mesh_clear()
    check("every card's base still sits on its tip in a gale", gaps and max(gaps) < 1e-4,
          f"worst base gap {max(gaps):.2e} m over {len(gaps)} cards" if gaps else "no cards")

    # The flutter is the cards' own term, and Sway 0 is how you prove it reaches nothing else.
    only_leaves, flags = vertices("WindLeaf", cards=4, wind=6.0, sway=0.0, leaf_flutter=2.0)
    d = moved(still, only_leaves)
    wood = max(v for v, c in zip(d, flags) if c < 0.5)
    leaf = max(v for v, c in zip(d, flags) if c > 0.5)
    check("at Sway 0 the flutter moves the cards and NOTHING else", wood == 0.0 and leaf > 0.01,
          f"wood {wood}, leaves {leaf:.3f} m")

    # Scene Time is the clock: two frames of the same tree differ, and re-reading one repeats.
    obj = bpy.data.objects["WindOn"]

    def at_frame(frame):
        bpy.context.scene.frame_set(frame)
        ev = obj.evaluated_get(bpy.context.evaluated_depsgraph_get())
        m = ev.to_mesh()
        out = [tuple(v.co) for v in m.vertices]
        ev.to_mesh_clear()
        return out

    f1, f31 = at_frame(1), at_frame(31)
    again = at_frame(1)
    bpy.context.scene.frame_set(1)
    check("the tree moves between frames", max(moved(f1, f31)) > 0.01,
          f"max {max(moved(f1, f31)):.3f} m between frame 1 and 31")
    check("and the same frame renders the same, so an animation is deterministic",
          max(moved(f1, again)) == 0.0, f"max {max(moved(f1, again))}")


def check_wind_phase():
    """A stand must not pulse in unison, and the honest limit of that claim.

    Two claims, and the second corrects the plan rather than confirming it. Per-TREE phase comes
    from the object's own world location, so two trees placed apart are out of step. Per-INSTANCE
    phase does not exist and cannot: an instanced object is evaluated once and the result copied,
    which is the same property docs/FOLIAGE.md 2.5 already records for the seed. Measured here
    rather than argued, because "F4 adds per-instance wind phase" was written before anyone tried.
    """
    a, _ = vertices("PhaseA", cards=4, wind=4.0)
    obj_b = build("PhaseB", cards=4, wind=4.0)[0]
    bpy.data.objects["PhaseB"].location = (11.0, 7.0, 0.0)
    bpy.context.view_layer.update()
    ev = bpy.data.objects["PhaseB"].evaluated_get(bpy.context.evaluated_depsgraph_get())
    mesh = ev.to_mesh()
    b = [tuple(v.co) for v in mesh.vertices]
    ev.to_mesh_clear()
    check("two identical trees standing apart do not sway in unison",
          len(a) == len(b) and max(moved(a, b)) > 0.05,
          f"max local difference {max(moved(a, b)):.3f} m" if len(a) == len(b) else "budget differs")

    # ... and the same tree moved back is back in phase, so the phase is the PLACE and not a
    # hidden per-object random that would make a bake unreproducible.
    bpy.data.objects["PhaseB"].location = (0.0, 0.0, 0.0)
    bpy.context.view_layer.update()
    ev = bpy.data.objects["PhaseB"].evaluated_get(bpy.context.evaluated_depsgraph_get())
    mesh = ev.to_mesh()
    home = [tuple(v.co) for v in mesh.vertices]
    ev.to_mesh_clear()
    check("and moving it back puts it back in phase", max(moved(a, home)) < 1e-6,
          f"max difference back at the origin {max(moved(a, home)):.2e} m")

    # The per-card phase: every card carries its own, spread over the range, so a spray shimmers.
    ev, mesh, _ = build("PhaseCards", cards=4)
    faces = set(card_faces(mesh))
    attr_phase = mesh.attributes.get("bbt_fol_phase")
    per_card = [{round(attr_phase.data[v].value, 6) for v in mesh.polygons[i].vertices}
                for i in list(faces)[:400]] if attr_phase else []
    values = sorted({next(iter(s)) for s in per_card if len(s) == 1})
    ev.to_mesh_clear()
    check("all four corners of a card share one phase", per_card and all(len(s) == 1 for s in per_card),
          f"{sum(1 for s in per_card if len(s) != 1)} split cards")
    check("and cards carry many different phases", len(values) > 0.5 * len(per_card),
          f"{len(values)} distinct phases over {len(per_card)} cards")
    check("spread over the whole 0..1 range", values and min(values) < 0.1 and max(values) > 0.9,
          f"{min(values):.3f}..{max(values):.3f}" if values else "none")

    # The limit, measured. An instanced tree is evaluated once, so every copy shares one phase.
    src = bpy.data.objects["PhaseA"]
    src.location = (0.0, 0.0, 0.0)
    scatter_ng = bpy.data.node_groups.new("PhaseScatter", "GeometryNodeTree")
    scatter_ng.interface.new_socket("Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")
    out = scatter_ng.nodes.new("NodeGroupOutput")
    line = scatter_ng.nodes.new("GeometryNodeMeshLine")
    line.inputs["Count"].default_value = 2
    line.inputs["Offset"].default_value = (25.0, 0.0, 0.0)
    info = scatter_ng.nodes.new("GeometryNodeObjectInfo")
    info.inputs["Object"].default_value = src
    info.transform_space = "ORIGINAL"
    iop = scatter_ng.nodes.new("GeometryNodeInstanceOnPoints")
    scatter_ng.links.new(line.outputs["Mesh"], iop.inputs["Points"])
    scatter_ng.links.new(info.outputs["Geometry"], iop.inputs["Instance"])
    realize = scatter_ng.nodes.new("GeometryNodeRealizeInstances")
    scatter_ng.links.new(iop.outputs["Instances"], realize.inputs["Geometry"])
    scatter_ng.links.new(realize.outputs["Geometry"], out.inputs["Geometry"])
    host = bpy.data.objects.new("PhaseStand", bpy.data.meshes.new("PhaseStand"))
    bpy.context.collection.objects.link(host)
    mod = host.modifiers.new("GeometryNodes", "NODES")
    mod.node_group = scatter_ng
    ev = host.evaluated_get(bpy.context.evaluated_depsgraph_get())
    mesh = ev.to_mesh()
    half = len(mesh.vertices) // 2
    first = [tuple(v.co) for v in mesh.vertices[:half]]
    second = [(v.co[0] - 25.0, v.co[1], v.co[2]) for v in mesh.vertices[half:]]
    ev.to_mesh_clear()
    same = max(moved(first, second)) if len(first) == len(second) else None
    check("two INSTANCES of one tree share a phase, which is why F5 bakes N variants",
          same is not None and same < 1e-5,
          f"max difference between instances {same:.2e} m -- an instanced object is evaluated once"
          if same is not None else "instance halves differ in size")


def check_no_master_change():
    """This phase must not have widened a SHARED group. The one real hazard in F4.

    Translucency and the season colour are both new terms on a leaf card, and the obvious place for
    either is S_SurfaceMaster -- which terrain and water embed. A rebuild there reassigns every
    socket identifier, so one socket costs a revert-to-default on every tuned terrain in the file
    (the item-3 and snow-line bumps each paid exactly that). Both terms went outside the master
    instead, and this is the check that says so in numbers rather than in a comment.
    """
    from bob_blender_tools.core.materials import shared

    check("the global shared-group version is untouched by this phase", shared.S_GROUP_VER == 6,
          f"S_GROUP_VER {shared.S_GROUP_VER}")
    check("the leaf season layer carries its OWN version instead",
          shared.group_version(materials.LEAF_SEASON) == 1
          and materials.LEAF_SEASON in shared._GROUP_VER_OVERRIDE,
          f"{materials.LEAF_SEASON} v{shared.group_version(materials.LEAF_SEASON)}")
    master = materials.surface_master_group()
    names = {s.name for s in master.interface.items_tree if getattr(s, "in_out", "") == "INPUT"}
    outs = {s.name for s in master.interface.items_tree if getattr(s, "in_out", "") == "OUTPUT"}
    check("S_SurfaceMaster gained no leaf sockets", not (names | outs) & {
        "Translucency", "Season", "Autumn", "Autumn Tint", "Transmit"},
          f"{sorted((names | outs) & {'Translucency', 'Season', 'Autumn'})}")
    check("and it still outputs exactly the three the wrapper drives",
          outs == {"Base Color", "Roughness", "Metallic"}, str(sorted(outs)))
    # The other half of 2.7's argument, re-measured: alpha never became a texture-set role, so no
    # sampler version bump and no weather layer modulating a matte.
    from bob_blender_tools.core.materials import texset
    check("opacity is still not a texture-set sampler role", "opacity" not in texset._ROLES,
          str(sorted(texset._ROLES)))


def check_translucency():
    """A leaf lets light through, and the cutout still cuts.

    The failure this exists for is specific and would render: a Mix Shader between the Principled
    and a Translucent lights the WHOLE quad, because the Principled's Alpha mattes only the
    Principled. Every texel the atlas cut away comes back as glowing translucent card, and a spray
    renders as a bright rectangle -- which reads as a lighting problem, not a wiring one. So the
    translucent branch is matted by the same cutout first, and that gate is what is measured here.
    """
    ev, _mesh, _ = build("Trans", cards=4)
    ev.to_mesh_clear()
    card = bpy.data.materials["M_Trans Leaf"]
    nt = card.node_tree
    out = next(n for n in nt.nodes if n.bl_idname == "ShaderNodeOutputMaterial")
    surface = out.inputs["Surface"].links[0].from_node if out.inputs["Surface"].is_linked else None
    check("the card's surface is a mix, not the Principled alone",
          surface is not None and surface.bl_idname == "ShaderNodeMixShader",
          surface.bl_idname if surface else "not linked")
    if surface is None:
        return
    check("and its amount is a leaf's, not a sheet of paper's",
          0.05 <= surface.inputs[0].default_value <= 0.6,
          f"translucency {surface.inputs[0].default_value:.2f}")
    branches = [i.links[0].from_node for i in (surface.inputs[1], surface.inputs[2]) if i.is_linked]
    kinds = {n.bl_idname for n in branches}
    check("it mixes the Principled with a second lobe",
          kinds == {"ShaderNodeBsdfPrincipled", "ShaderNodeMixShader"}, str(sorted(kinds)))
    gate = next((n for n in branches if n.bl_idname == "ShaderNodeMixShader"), None)
    if gate is None:
        return
    inner = {n.links[0].from_node.bl_idname for n in (gate.inputs[1], gate.inputs[2]) if n.is_linked}
    check("whose translucent half is matted against a Transparent BSDF",
          inner == {"ShaderNodeBsdfTransparent", "ShaderNodeBsdfTranslucent"}, str(sorted(inner)))
    bsdf = next(n for n in nt.nodes if n.bl_idname == "ShaderNodeBsdfPrincipled")
    alpha_src = bsdf.inputs["Alpha"].links[0].from_socket if bsdf.inputs["Alpha"].is_linked else None
    gate_src = gate.inputs[0].links[0].from_socket if gate.inputs[0].is_linked else None
    check("gated by the SAME cutout the Principled uses, so neither branch fills the gaps",
          alpha_src is not None and alpha_src == gate_src,
          f"{alpha_src.node.name if alpha_src else None} against "
          f"{gate_src.node.name if gate_src else None}")
    # The bark must not have picked any of this up: a translucent trunk is a lamp.
    bark = bpy.data.materials["M_Trans Bark"]
    bark_out = next(n for n in bark.node_tree.nodes if n.bl_idname == "ShaderNodeOutputMaterial")
    src = bark_out.inputs["Surface"].links[0].from_node
    check("the bark is still a plain Principled", src.bl_idname == "ShaderNodeBsdfPrincipled",
          src.bl_idname)


# -- F5: variants, the LOD ladder, the pack writer and a real stand ----------------------------
def pool_verts(obj):
    """A pooled variant's evaluated vertices, through `evaluable`.

    Not a convenience. `BOB_Assets_<Kind>` is not linked to the scene, and an object outside the
    view layer is not evaluated at all, so a direct `evaluated_get(...).to_mesh()` on a baked
    variant returns an EMPTY mesh and says nothing -- which is how the first run of the baker
    reported four variants of 0 verts and exported four meshes with no primitives. Reading them any
    other way is a check that passes on a pool that does not exist.
    """
    from bob_blender_tools.core import foliage_variants

    with foliage_variants.evaluable(obj):
        ev = obj.evaluated_get(bpy.context.evaluated_depsgraph_get())
        mesh = ev.to_mesh()
        out = [tuple(round(c, 5) for c in v.co) for v in mesh.vertices]
        ev.to_mesh_clear()
    return out


def instance_stand(name, sources, count, spacing=6.0, realize=True, pick_by_index=False):
    """A host object instancing `sources` on a grid, the way a scatter layer does. Returns it."""
    coll = bpy.data.collections.new(name + "Pool")
    for obj in sources:
        for c in list(obj.users_collection):
            c.objects.unlink(obj)
        coll.objects.link(obj)
    ng = bpy.data.node_groups.new(name, "GeometryNodeTree")
    ng.interface.new_socket("Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")
    out = ng.nodes.new("NodeGroupOutput")
    line = ng.nodes.new("GeometryNodeMeshLine")
    line.inputs["Count"].default_value = count
    line.inputs["Offset"].default_value = (spacing, 0.0, 0.0)
    info = ng.nodes.new("GeometryNodeCollectionInfo")
    info.inputs["Collection"].default_value = coll
    info.inputs["Separate Children"].default_value = True
    info.inputs["Reset Children"].default_value = True
    iop = ng.nodes.new("GeometryNodeInstanceOnPoints")
    iop.inputs["Pick Instance"].default_value = True
    if pick_by_index:
        idx = ng.nodes.new("GeometryNodeInputIndex")
        ng.links.new(idx.outputs["Index"], iop.inputs["Instance Index"])
    ng.links.new(line.outputs["Mesh"], iop.inputs["Points"])
    ng.links.new(info.outputs["Instances"], iop.inputs["Instance"])
    tail = iop.outputs["Instances"]
    if realize:
        r = ng.nodes.new("GeometryNodeRealizeInstances")
        ng.links.new(tail, r.inputs["Geometry"])
        tail = r.outputs["Geometry"]
    ng.links.new(tail, out.inputs["Geometry"])
    host = bpy.data.objects.new(name, bpy.data.meshes.new(name))
    bpy.context.scene.collection.objects.link(host)
    host.modifiers.new("GeometryNodes", "NODES").node_group = ng
    return host


def host_verts(host, frame=None):
    if frame is not None:
        bpy.context.scene.frame_set(frame)
    ev = host.evaluated_get(bpy.context.evaluated_depsgraph_get())
    mesh = ev.to_mesh()
    out = [tuple(v.co) for v in mesh.vertices]
    ev.to_mesh_clear()
    return out


def frame_ms(frames=6):
    """Milliseconds of DEPSGRAPH re-evaluation per frame, which is what `frame_set` forces.

    Not `to_mesh` time. The first version of this measured the loop body and reported 0.00 ms for a
    stand of applied meshes -- true, and measuring nothing: a mesh with no time dependency is never
    re-evaluated, so the loop did no work at all. That reading is the answer, but only once the
    thing being timed is the re-evaluation itself.
    """
    bpy.context.scene.frame_set(1)
    bpy.context.view_layer.update()
    t0 = time.perf_counter()
    for f in range(2, 2 + frames):
        bpy.context.scene.frame_set(f)
        bpy.context.evaluated_depsgraph_get()
    bpy.context.scene.frame_set(1)
    return (time.perf_counter() - t0) / frames * 1000.0


def wipe_scene(keep=()):
    for obj in list(bpy.data.objects):
        if obj.name not in keep:
            bpy.data.objects.remove(obj, do_unlink=True)


def check_variants():
    """Make Variants: N seeds in the pool, still alive, still the tree that was authored.

    The failures this rules out, and every one of them produces a pool that looks right:

    - a bake that reads the SPECIES PRESET instead of the tree loses every slider the artist moved,
      so the stand is eight copies of a tree nobody authored,
    - a bake whose seed does not reach the geometry is eight clones (the same failure the seed check
      at the top of this gate exists for, one level up),
    - a bake that FREEZES the variants silently deletes the whole of docs/FOLIAGE.md 2.4: an applied
      mesh is a tree stopped at the frame it was baked on, and a still forest under a moving sky
      reads as a render setting rather than as a missing feature,
    - a bake that leaves each variant its own materials makes retuning a species twenty-four edits,
      so a stand drifts away from the tree it came from,
    - an origin that is not at the base buries or floats every scattered instance.
    """
    from bob_blender_tools.core import foliage_build, foliage_variants

    wipe_scene()
    hero = foliage_build.grow("Hero", dict(assets.foliage_species("conifer")["params"], seed=4),
                              species="conifer", scene=bpy.context.scene)
    # Two knobs moved off the preset, so the bake has something to lose if it reads the wrong thing.
    foliage_build.live_input(hero, "Card Size").value = 1.55
    foliage_build.live_input(hero, "Droop").value = 0.77
    mats_before = len(bpy.data.materials)

    report = foliage_variants.make_variants(hero, count=4, levels=(0,), scene=bpy.context.scene)
    pool = bpy.data.collections.get(report["collection"])
    variants = sorted((o for o in pool.objects if foliage_build.is_foliage(o)),
                      key=lambda o: o.name) if pool else []
    check("Make Variants fills BOB_Assets_<Kind> from the species' own kind",
          report["collection"] == "BOB_Assets_Trees" and len(variants) == 4,
          f"{report['collection']}, {len(variants)} variants")
    check("and the authored tree is not one of them: the panel still lists one tree",
          [o.name for o in foliage_build.foliage_objects(bpy.context.scene)] == ["Hero"],
          str([o.name for o in foliage_build.foliage_objects(bpy.context.scene)]))

    verts = {o.name: pool_verts(o) for o in variants}
    budgets = sorted({len(v) for v in verts.values()})
    # WITHIN a per-mille of each other, not identical, and F6 is why. Cards are selected along a limb
    # by `bbt_fol_t`, which is Spline Parameter's factor -- an ARC-LENGTH fraction, measured after the
    # bend and the sag have moved the points. So a different seed genuinely puts a handful of interior
    # points on the other side of `Leaf Start` and the budget moves by a few cards. Measured on the
    # shipped conifer: 17,240 / 17,248 / 17,256 verts over four seeds, a spread of 16 in 17,000.
    # Exact equality was the right check while cards grew only on tips, where the selection is an
    # index and cannot drift; asserting it now would be asserting that the seed does nothing.
    spread = (budgets[-1] - budgets[0]) / max(1, budgets[0])
    check("every variant built, and to within a per-mille of the same vertex budget",
          all(verts.values()) and spread < 0.001, f"budgets {budgets}, spread {spread * 100:.3f}%")
    worst = 0
    names = sorted(verts)
    for i, a in enumerate(names):
        for b in names[i + 1:]:
            worst = max(worst, sum(1 for p, q in zip(verts[a], verts[b]) if p == q))
    check("no two variants share a vertex set", worst == 0,
          f"worst pair shares {worst} of {len(verts[names[0]])} verts")

    tuned = {round(foliage_build.live_input(o, "Card Size").value, 3) for o in variants}
    droop = {round(foliage_build.live_input(o, "Droop").value, 3) for o in variants}
    check("the bake carries the tree's TUNED knobs, not its species preset",
          tuned == {1.55} and droop == {0.77}, f"Card Size {tuned}, Droop {droop}")

    wanted = {m.name for m in foliage_variants._tree_materials(hero) if m}
    got = {m.name for o in variants for m in foliage_variants._tree_materials(o) if m}
    check("every variant wears the source tree's two materials", got == wanted and len(got) == 2,
          f"{sorted(got)} against {sorted(wanted)}")
    check("so baking four variants added no materials at all",
          len(bpy.data.materials) == mats_before,
          f"{mats_before} before, {len(bpy.data.materials)} after, "
          f"{report['materials_shared']} duplicates binned")

    bases = [min(v[2] for v in verts[n]) for n in names]
    check("the origin sits at the base of every variant, so a scattered tree is not buried",
          max(abs(b) for b in bases) < 0.01,
          "lowest vertex " + ", ".join(f"{b:+.5f}" for b in bases))

    # Re-pressing must refresh rather than double what a scatter layer instances.
    again = foliage_variants.make_variants(hero, count=4, levels=(0,), scene=bpy.context.scene)
    check("a second press replaces the pool rather than doubling it",
          again["replaced"] == 4 and len([o for o in pool.objects
                                          if foliage_build.is_foliage(o)]) == 4,
          f"replaced {again['replaced']}, pool now "
          f"{len([o for o in pool.objects if foliage_build.is_foliage(o)])}")

    # ... but a SECOND tree of the same species is a second tree, and baking it must not wipe the
    # first's pool. It would if the variant stem came from the species name, which reads better and
    # is what this reached for first: eight variants a scatter layer was already instancing, gone
    # with no warning and no error.
    other = foliage_build.grow("Hero Two", dict(assets.foliage_species("conifer")["params"], seed=9),
                               species="conifer", scene=bpy.context.scene)
    foliage_variants.make_variants(other, count=2, levels=(0,), scene=bpy.context.scene)
    stems = sorted({o.name.split("_v")[0] for o in pool.objects if foliage_build.is_foliage(o)})
    check("and baking a SECOND tree of the same species does not wipe the first's variants",
          len([o for o in pool.objects if foliage_build.is_foliage(o)]) == 6 and len(stems) == 2,
          f"{len([o for o in pool.objects if foliage_build.is_foliage(o)])} in the pool "
          f"under stems {stems}")


def check_variants_alive():
    """The phase's one real hazard, measured rather than argued: an instanced variant still MOVES.

    F4 established that two instances of one tree AGREE at one frame (9.54e-07 m apart), which is a
    statement about phase and says nothing about motion, and the obvious reading of "bake" -- apply
    the modifier -- would have made every stand static while passing every other check in this file.
    So both halves are measured here: the live variant across frames, and the applied copy across
    the same frames, because a number that moves means nothing without the one that does not.
    """
    from bob_blender_tools.core import foliage_build, foliage_variants

    wipe_scene()
    hero = foliage_build.grow("Gale", dict(assets.foliage_species("conifer")["params"],
                                           seed=4, wind=5.0, wind_direction=30.0),
                              species="conifer", scene=bpy.context.scene)
    foliage_variants.make_variants(hero, count=2, levels=(0,), scene=bpy.context.scene)
    pool = bpy.data.collections["BOB_Assets_Trees"]
    variants = sorted((o for o in pool.objects if foliage_build.is_foliage(o)),
                      key=lambda o: o.name)
    bpy.data.objects.remove(hero, do_unlink=True)

    host = instance_stand("LiveStand", variants, 2, spacing=30.0, pick_by_index=True)
    f1, f31 = host_verts(host, 1), host_verts(host, 31)
    moved_live = max(moved(f1, f31)) if len(f1) == len(f31) else None
    check("an INSTANCED variant still sways: the bake keeps the wind",
          moved_live is not None and moved_live > 0.1,
          f"max {moved_live:.4f} m between frame 1 and 31 over {len(f1)} instanced verts"
          if moved_live is not None else "instance budgets differ")

    # The other half: freeze one and instance that, so the number above has something to mean.
    bpy.context.scene.frame_set(7)
    with foliage_variants.evaluable(variants[0]):
        dg = bpy.context.evaluated_depsgraph_get()
        frozen = bpy.data.objects.new(
            "Frozen", bpy.data.meshes.new_from_object(variants[0].evaluated_get(dg)))
    bpy.context.scene.collection.objects.link(frozen)
    fhost = instance_stand("FrozenStand", [frozen], 2, spacing=30.0)
    g1, g31 = host_verts(fhost, 1), host_verts(fhost, 31)
    moved_frozen = max(moved(g1, g31)) if len(g1) == len(g31) else None
    check("and an APPLIED copy does not, which is what the live variant is worth",
          moved_frozen == 0.0, f"max {moved_frozen} m over the same 30 frames")

    # The cost of that, and its shape: flat in instance count, so a forest is not more expensive
    # than a copse. Timed rather than asserted, because the number is the decision's whole basis.
    bpy.data.objects.remove(fhost, do_unlink=True)
    bpy.data.objects.remove(frozen, do_unlink=True)
    bpy.data.objects.remove(host, do_unlink=True)
    small = instance_stand("Small", variants, 25, spacing=8.0, realize=False)
    ms_small = frame_ms()
    bpy.data.objects.remove(small, do_unlink=True)
    big = instance_stand("Big", variants, 400, spacing=8.0, realize=False)
    ms_big = frame_ms()
    check("the per-frame cost is per VARIANT, not per instance",
          ms_big < ms_small * 1.5 + 1.0,
          f"{ms_small:.2f} ms/frame at 25 instances, {ms_big:.2f} ms at 400")
    bpy.data.objects.remove(big, do_unlink=True)


def check_variant_phase():
    """Variants are spread out in the pool, and stacking them would cost the stand its shimmer.

    A tree's phase is its own world location (F4, `_tree_phase`), and a pool is authored at the
    origin, so this is the one thing about the bake that is easy to leave out and impossible to see:
    eight variants at (0,0,0) sway in perfect unison and the forest breathes as one object. Both
    sides are measured at the SAME seed, so the only thing that can differ is the phase.

    **Over a gust CYCLE and not at one frame**, which is the part that had to be got right. Two
    sinusoids of the same frequency and different phase are equal twice per cycle, so a single-frame
    reading is a coin toss: measured at frame 1 the 40 m spread differs by 0.0087 m (the two phases
    happen to land on the same point of the gust, 0.657 against 0.652) and at frame 9 by 1.1618 m.
    The first version of this check read frame 1 and failed on a recipe that was working perfectly
    -- F3's bark-seam check in the other direction. Out of step means "not always together", and the
    frames are spread over one period of `SWAY_FREQ` so that is what gets measured.
    """
    from bob_blender_tools.core.geonodes.recipes import foliage as fol

    from bob_blender_tools.core import foliage_variants

    wipe_scene()
    period = bpy.context.scene.render.fps / fol.SWAY_FREQ      # frames in one gust
    frames = [1 + round(i * period / 5.0) for i in range(5)]
    build("PoolA", cards=4, wind=4.0)
    build("PoolB", cards=4, wind=4.0)
    a_obj, b_obj = bpy.data.objects["PoolA"], bpy.data.objects["PoolB"]

    def spread(dx):
        b_obj.location = (dx, 0.0, 0.0)
        bpy.context.view_layer.update()
        worst = 0.0
        for frame in frames:
            bpy.context.scene.frame_set(frame)
            # LOCAL coordinates on both sides. `to_mesh` is object space, so the offset goes nowhere
            # near these numbers, and subtracting it back out would BE the difference -- which an
            # earlier version did, reporting exactly 40.0000 m and passing on anything at all.
            pa, pb = pool_verts(a_obj), pool_verts(b_obj)
            if len(pa) != len(pb):
                return None
            worst = max(worst, max(moved(pa, pb)))
        bpy.context.scene.frame_set(1)
        return worst

    stacked = spread(0.0)
    check("two same-seed variants stacked at the origin are identical at every frame -- the failure",
          stacked is not None and stacked < 1e-4,
          f"worst over {len(frames)} frames of a gust: {stacked:.2e} m" if stacked is not None
          else "budgets differ")
    apart = spread(foliage_variants.VARIANT_SPACING)
    check(f"and {foliage_variants.VARIANT_SPACING:.0f} m apart -- the spread the baker uses -- "
          f"they are not", apart is not None and apart > 0.05,
          f"worst over the same gust: {apart:.4f} m of phase difference" if apart is not None
          else "budgets differ")

    # ... and the spread must not move the instances, or a stand would be laid out by the pool.
    host = instance_stand("SpreadStand", [b_obj], 1, spacing=0.0)
    xs = [v[0] for v in host_verts(host, 1)]
    check("but an instance still lands on its POINT, because Reset Children resets the transform",
          xs and abs((min(xs) + max(xs)) / 2.0) < 1.0,
          f"instance x-range {min(xs):.2f} .. {max(xs):.2f} for a source authored at "
          f"x={foliage_variants.VARIANT_SPACING:.0f}")


def check_lods():
    """The LOD ladder: a rebuild, budgeted, and not wider than the tree it replaces.

    Three things have to hold and each fails differently. The rung has to be genuinely cheaper, or
    it is a second copy of LOD0 under a name that promises otherwise. It has to keep the CANOPY, or
    a distant stand thins out and pops as the camera closes. And it has to keep the SILHOUETTE, or
    the tree grows as it recedes -- which the area rule alone does: preserving the conifer's 447 m2
    of canopy on 18 cards makes each card 7.4 m across and the tree comes back 253% as wide.
    """
    from bob_blender_tools.core import foliage_variants

    wipe_scene()
    print("    the ladder, per species")
    print(f"      {'species':11} {'rung':4} {'verts':>7} {'cards':>6} {'canopy':>7} {'width':>7}")
    for species in ("conifer", "broadleaf", "shrub", "grass_tuft"):
        base = dict(assets.foliage_species(species)["params"], seed=3)
        ladder = foliage_variants.fit_ladder(base)
        shots = []
        for level, rung in ladder:
            apply_op({"op": "build_geonodes", "recipe": "foliage", "name": "Rung",
                      "params": dict(rung), "reset": True})
            shots.append((level, foliage_variants.measure(bpy.data.objects["Rung"])))
        zero = shots[0][1]
        for level, got in shots:
            print(f"      {species:11} {level:4} {got['verts']:7} {got['cards']:6} "
                  f"{got['area'] / zero['area'] * 100:6.1f}% {got['width'] / zero['width'] * 100:6.1f}%")
        verts = [got["verts"] for _l, got in shots]
        check(f"{species}: every rung is cheaper than the one above it",
              all(a > b for a, b in zip(verts, verts[1:])), " -> ".join(str(v) for v in verts))
        widest = max(got["width"] / zero["width"] for _l, got in shots)
        check(f"{species}: no rung is wider than the tree it stands in for",
              widest <= foliage_variants.WIDTH_TOLERANCE + 0.02, f"widest {widest * 100:.1f}%")
        # A rebuild, not a decimate: a Decimate triangulates, and the card quads are the evidence.
        last = bpy.data.objects["Rung"].evaluated_get(bpy.context.evaluated_depsgraph_get())
        mesh = last.to_mesh()
        cards = card_faces(mesh)
        check(f"{species}: the cheapest rung still has whole card QUADS, so it was rebuilt",
              cards and all(len(mesh.polygons[i].vertices) == 4 for i in cards),
              f"{len(cards)} card faces, "
              f"{sorted({len(mesh.polygons[i].vertices) for i in cards})} verts each")
        check(f"{species}: and it still carries a UV layer and both materials",
              len(mesh.uv_layers) == 1 and len([m for m in mesh.materials if m]) == 2,
              f"{len(mesh.uv_layers)} UV layers, "
              f"{[m.name for m in mesh.materials if m]}")
        last.to_mesh_clear()
        if species == "grass_tuft":
            check("a species already at the floor gets ONE rung, not a duplicate of LOD0",
                  [level for level, _r in ladder] == [0, 2],
                  str([level for level, _r in ladder]))

    # The budgets, re-derived rather than defended: docs/FOLIAGE.md 2.6's 8 k target predates cards.
    conifer = dict(assets.foliage_species("conifer")["params"], seed=3)
    ladder = dict(foliage_variants.fit_ladder(conifer))
    got = {}
    for level, rung in sorted(ladder.items()):
        apply_op({"op": "build_geonodes", "recipe": "foliage", "name": "Budget",
                  "params": dict(rung), "reset": True})
        got[level] = foliage_variants.measure(bpy.data.objects["Budget"])
    check("LOD1 costs about a quarter of LOD0", got[1]["verts"] < 0.30 * got[0]["verts"],
          f"{got[1]['verts']} of {got[0]['verts']} ({got[1]['verts'] / got[0]['verts'] * 100:.1f}%)")
    check("LOD2 costs under a twentieth", got[2]["verts"] < 0.05 * got[0]["verts"],
          f"{got[2]['verts']} of {got[0]['verts']} ({got[2]['verts'] / got[0]['verts'] * 100:.1f}%)")
    check("LOD1 keeps the canopy it is standing in for",
          got[1]["area"] > 0.9 * got[0]["area"],
          f"{got[1]['area'] / got[0]['area'] * 100:.1f}% of LOD0's card area")
    # LOD2 cannot, and the number is recorded rather than asserted away: a narrow crown of 1,228
    # small cards has no 76-card equivalent that is not a sphere, so the width wins and the coverage
    # is the price. Held above the first rule's 7.5%, which was a stick with leaves on it.
    check("LOD2 keeps as much of it as a billboard rung can, and more than a stick",
          got[2]["area"] > 0.2 * got[0]["area"],
          f"{got[2]['area'] / got[0]['area'] * 100:.1f}% of LOD0's card area on "
          f"{got[2]['cards']} cards")


def check_variant_pack(tmp):
    """The narrow pack writer, and the two things it cannot carry.

    `gen_assets.finish_asset` is the wrong tool -- it bakes dense-to-low, decimates, unwraps and
    converts to a BobShader, and a procedural tree needs none of it -- so this reuses three helpers
    and nothing else. What it must prove is that the reduction is HONEST: a packed variant is a
    frozen mesh with a plain PBR material, and its manifest entry carries the species and seed that
    regrow it alive, so the mesh is the fallback and the two numbers are the record.
    """
    from bob_blender_tools.core import foliage_build, foliage_variants

    wipe_scene()
    pack = os.path.join(tmp, "variant_pack")
    assets.ensure_generated_pack(pack)
    assets.add_pack_root(pack)
    hero = foliage_build.grow("PackHero", dict(assets.foliage_species("conifer")["params"], seed=4),
                              species="conifer", scene=bpy.context.scene)
    report = foliage_variants.make_variants(hero, count=2, scene=bpy.context.scene, pack_dir=pack)
    written = report.get("pack") or []
    check("the writer wrote a file per variant",
          len(written) == 2 and all(os.path.getsize(f) > 10000 for _n, f in written),
          ", ".join(f"{n} {os.path.getsize(f) // 1024} KB" for n, f in written))

    entries = foliage_variants.manifest_variants(pack, "trees")
    check("each is a manifest entry the ordinary reader normalises",
          len(entries) == 2 and all(e.get("origin") == "base" and e.get("faces") for e in entries),
          str([(e["file"], e["faces"]) for e in entries]))
    check("carrying the species and seed that regrow it, so the frozen mesh is the FALLBACK",
          all(e["foliage"].get("species") == "conifer" and "seed" in e["foliage"]
              for e in entries),
          str([e["foliage"]["seed"] for e in entries]))
    check("and the rungs it holds", all(e.get("lod") == [1, 2] for e in entries),
          str([e.get("lod") for e in entries]))

    # The regrown tree must BE the variant, not merely resemble it. Same wind on both sides, or the
    # check measures the weather: an earlier version of this compared a still capture against a
    # regrow into a blowing scene and reported a 0.93 m miss on an exact match.
    pool = bpy.data.collections["BOB_Assets_Trees"]
    first = sorted((o for o in pool.objects if foliage_build.is_foliage(o)),
                   key=lambda o: o.name)[0]
    was = pool_verts(first)
    back = foliage_variants.regrow(entries[0], "Regrown", scene=bpy.context.scene,
                                   location=tuple(first.location))
    foliage_build.live_input(back, "Wind").value = foliage_build.live_input(first, "Wind").value
    bpy.context.view_layer.update()
    now = pool_verts(back)
    gap = max(moved(was, now)) if len(was) == len(now) else None
    check("regrowing from species and seed reproduces the variant exactly, and alive",
          gap is not None and gap < 1e-6,
          f"max {gap:.2e} m over {len(now)} verts" if gap is not None else
          f"budgets differ: {len(was)} against {len(now)}")

    # The export material has to be a plain Principled. Not a preference: the glTF exporter
    # segfaults at teardown on the card material's Mix Shader chain (isolated -- bark alone exits 0,
    # the card alone exits 139), and a gate that crashes AFTER printing its verdict reads as a clean
    # run to an exit code, which is how the G2 gate hid a crash for two phases.
    exported = [m for m in bpy.data.materials if "_export_" in m.name]
    check("the packed variant's materials are plain Principleds, which is all glTF carries",
          len(exported) == 2 and all(
              next(n for n in m.node_tree.nodes
                   if n.bl_idname == "ShaderNodeOutputMaterial").inputs["Surface"]
              .links[0].from_node.bl_idname == "ShaderNodeBsdfPrincipled" for m in exported),
          str(sorted(m.name for m in exported)))
    leaf = next((m for m in exported if m.name.endswith("Leaf")), None)
    if leaf is not None:
        bsdf = next(n for n in leaf.node_tree.nodes if n.bl_idname == "ShaderNodeBsdfPrincipled")
        check("and the leaf one still cuts out, so a packed card is not a rectangle",
              bsdf.inputs["Alpha"].is_linked, "Alpha is linked" if bsdf.inputs["Alpha"].is_linked
              else "Alpha is not linked")
    # The live tree keeps everything: the export materials are a copy, not a conversion.
    live = {m.name for m in foliage_variants._tree_materials(first) if m}
    check("while the LIVE variants keep their BobShaders untouched",
          live == {"M_PackHero Bark", "M_PackHero Leaf"}, str(sorted(live)))


def _season_render(season, path):
    """Render the scene with the world set to a season; return (mean R - mean G, mean luminance).

    Rendered rather than read off the graph, for the reason every other shading check in this suite
    is: a wired-and-dead season layer passes every structural test. Red minus green because that is
    what turning is -- a leaf loses chlorophyll and keeps its carotenoids -- and it is measured as a
    DIFFERENCE between two renders of the same frame, so the background cancels out.
    """
    from bob_blender_tools.core import shading

    scene = bpy.context.scene
    scene.bbt_env.season = season
    shading.install_env_drivers(scene)          # re-installed so the value is current, as Apply does
    scene.frame_set(scene.frame_current)
    var = render_variance("BLENDER_EEVEE_NEXT", path)
    if var is None:
        var = render_variance("BLENDER_EEVEE", path)
    if var is None or not os.path.isfile(path + ".png"):
        return None
    img = bpy.data.images.load(path + ".png")
    px = np.asarray(img.pixels[:], dtype=np.float32).reshape(-1, 4)
    bpy.data.images.remove(img)
    return float((px[:, 0] - px[:, 1]).mean()), float(px[:, :3].mean())


def check_season():
    """Autumn has to reach the canopy, and only the canopy.

    The season path is the shared one -- a driven Value node in a shared group, read live -- so the
    thing to prove is that it arrives: `set_env`, Apply Season and the panel all move `bbt_env`, and
    a canopy that does not follow is the "season swap changed nothing" failure the env-driver work
    was built to end.
    """
    from bob_blender_tools.core import env as bbt_env

    if getattr(bpy.context.scene, "bbt_env", None) is None:
        bbt_env.register()
    os.makedirs(OUT, exist_ok=True)
    apply_op({"op": "build_geonodes", "recipe": "foliage", "name": "Season",
              "params": dict(assets.foliage_species("broadleaf")["params"], seed=4, wind=0.0),
              "reset": True})
    for obj in list(bpy.data.objects):
        if obj.name != "Season":
            bpy.data.objects.remove(obj, do_unlink=True)
    tree = bpy.data.objects["Season"]
    height = max(v.co.z for v in tree.evaluated_get(
        bpy.context.evaluated_depsgraph_get()).to_mesh().vertices)
    bpy.ops.object.light_add(type="SUN", location=(0, 0, height * 2))
    bpy.context.active_object.data.energy = 5.0
    bpy.ops.object.camera_add(location=(0, -height * 1.5, height * 0.5),
                              rotation=(math.pi / 2, 0, 0))
    bpy.context.scene.camera = bpy.context.active_object

    summer = _season_render("summer", os.path.join(OUT, "season_summer"))
    autumn = _season_render("autumn", os.path.join(OUT, "season_autumn"))
    winter = _season_render("winter", os.path.join(OUT, "season_winter"))
    if summer is None or autumn is None:
        print("[SKIP] EEVEE could not render the season frames in this environment")
        return
    check("autumn turns the canopy warmer than summer does", autumn[0] > summer[0] + 0.002,
          f"red-minus-green {summer[0]:+.4f} in summer against {autumn[0]:+.4f} in autumn")
    if winter is not None:
        check("winter is a dead autumn leaf, part-way between the two",
              summer[0] < winter[0] < autumn[0] + 1e-4,
              f"winter {winter[0]:+.4f} between summer {summer[0]:+.4f} and autumn {autumn[0]:+.4f}")
    # The season must not be a global tint: with no cards at all, nothing may change.
    apply_op({"op": "build_geonodes", "recipe": "foliage", "name": "Season",
              "params": dict(assets.foliage_species("broadleaf")["params"], seed=4, wind=0.0,
                             cards=0), "reset": True})
    bare_summer = _season_render("summer", os.path.join(OUT, "season_bare_summer"))
    bare_autumn = _season_render("autumn", os.path.join(OUT, "season_bare_autumn"))
    if bare_summer is not None and bare_autumn is not None:
        check("a bare trunk does not turn: the season reaches the CARDS, not the tree",
              abs(bare_autumn[0] - bare_summer[0]) < 0.0005,
              f"bark red-minus-green {bare_summer[0]:+.5f} against {bare_autumn[0]:+.5f}")
    bpy.context.scene.bbt_env.season = "summer"


HEIGHTMAP = os.path.join(REPO, "library", "textures", "grass", "grass_height.png")


def check_stand():
    """A real stand: baked variants, a scatter layer on a terrain, and a frame.

    The end of the track and the only check here that exercises all of it at once. Everything above
    measures one hop; this is the hop nobody writes a check for, which is that the hops connect --
    a pool a scatter layer can actually read, a random pick that spans it, instances that sit ON the
    ground rather than through it, and a frame that is a forest.
    """
    from bob_blender_tools.core import foliage_build, foliage_variants

    wipe_scene()
    os.makedirs(OUT, exist_ok=True)
    hero = foliage_build.grow("StandHero", dict(assets.foliage_species("conifer")["params"],
                                                seed=4, wind=2.0, wind_direction=40.0),
                              species="conifer", scene=bpy.context.scene)
    foliage_variants.make_variants(hero, count=8, scene=bpy.context.scene)
    bpy.data.objects.remove(hero, do_unlink=True)
    pool = bpy.data.collections["BOB_Assets_Trees"]
    check("eight variants in the pool, at LOD0", len([o for o in pool.objects
                                                      if foliage_build.is_foliage(o)]) == 8,
          str(len(pool.objects)))

    apply_op({"op": "build_geonodes", "recipe": "heightmap_terrain", "name": "Ground",
              "params": {"heightmap": HEIGHTMAP, "size": 220.0, "resolution": 128,
                         "height": 24.0, "sea_level": 0.2}, "reset": True})
    apply_op({"op": "build_geonodes", "recipe": "scatter", "name": "Stand", "reset": True,
              "params": {"emitter": "Ground", "assets": pool.name, "align": "up",
                         "density": 0.02, "distance_min": 6.0, "min_normal_z": 0.75,
                         "min_scale": 0.85, "max_scale": 1.25}})
    bpy.context.view_layer.update()
    host = bpy.data.objects["Stand"]
    count, sources = foliage_variants.stand_report(host)
    check("the scatter layer instances the baked pool", count > 60, f"{count} trees on 220 m")
    check("and its random pick spans every variant, so the stand is not one tree repeated",
          len(sources) == 8, f"{len(sources)} of 8 variants used")

    # A tree must stand ON the terrain. The origin is at the base of every variant (checked above),
    # so an instance's own Z is the ground's, and the mesh under it is what says whether that held.
    dg = bpy.context.evaluated_depsgraph_get()
    ground = bpy.data.objects["Ground"].evaluated_get(dg)
    gmesh = ground.to_mesh()
    zs = [v.co.z for v in gmesh.vertices]
    ground.to_mesh_clear()
    inst_z = [i.matrix_world.translation.z for i in dg.object_instances
              if i.is_instance and i.parent is not None and i.parent.original.name == "Stand"]
    check("every instance sits within the terrain's own height range, not under it",
          inst_z and min(inst_z) >= min(zs) - 0.5 and max(inst_z) <= max(zs) + 0.5,
          f"instances {min(inst_z):.2f}..{max(inst_z):.2f} m, terrain {min(zs):.2f}..{max(zs):.2f} m"
          if inst_z else "no instances")

    # The same stand off each rung in turn, which is the number a distant layer is chosen on.
    # One collection per rung, so the comparison is eight variants against eight variants rather
    # than eight against the sixteen the mixed LOD collection holds.
    lods = bpy.data.collections.get(foliage_build.LOD_COLL)
    print(f"    {'stand':34} {'trees':>6} {'verts each':>11} {'ms/frame':>9}")
    for level, source in ((0, pool),) + tuple(
            (level, [o for o in (lods.objects if lods else []) if o.name.endswith(f"_LOD{level}")])
            for level in (1, 2)):
        members = list(source.objects) if hasattr(source, "objects") else list(source)
        if not members:
            continue
        rung_coll = bpy.data.collections.get(f"RungPool{level}")
        if rung_coll is None:
            rung_coll = bpy.data.collections.new(f"RungPool{level}")
        for obj in members:
            if obj.name not in rung_coll.objects:
                rung_coll.objects.link(obj)
        apply_op({"op": "build_geonodes", "recipe": "scatter", "name": "Stand", "reset": True,
                  "params": {"emitter": "Ground", "assets": rung_coll.name, "align": "up",
                             "density": 0.02, "distance_min": 6.0, "min_normal_z": 0.75,
                             "min_scale": 0.85, "max_scale": 1.25}})
        bpy.context.view_layer.update()
        rung_count, _src = foliage_variants.stand_report(bpy.data.objects["Stand"])
        per = foliage_variants.measure(members[0])["verts"]
        print(f"    {f'LOD{level}, {len(members)} live variants':34} {rung_count:6} "
              f"{per:11,} {frame_ms():9.2f}")

    # Back to LOD0 for the frame.
    apply_op({"op": "build_geonodes", "recipe": "scatter", "name": "Stand", "reset": True,
              "params": {"emitter": "Ground", "assets": pool.name, "align": "up",
                         "density": 0.02, "distance_min": 6.0, "min_normal_z": 0.75,
                         "min_scale": 0.85, "max_scale": 1.25}})
    bpy.context.view_layer.update()

    # The frame. Every check above passes on a stand of invisible trees.
    bpy.ops.object.light_add(type="SUN", location=(0, 0, 120))
    bpy.context.active_object.data.energy = 4.0
    bpy.context.active_object.rotation_euler = (0.75, 0.0, 0.6)
    bpy.ops.object.camera_add(location=(0.0, -135.0, 34.0), rotation=(math.pi / 2.35, 0, 0))
    bpy.context.scene.camera = bpy.context.active_object
    bpy.context.scene.render.resolution_x = 960
    bpy.context.scene.render.resolution_y = 540
    var = None
    for engine in ("BLENDER_EEVEE_NEXT", "BLENDER_EEVEE"):
        var = render_variance(engine, os.path.join(OUT, "stand"))
        if var is not None:
            break
    if var is None:
        print("[SKIP] EEVEE could not render the stand in this environment")
    else:
        check("the stand renders, and is not flat", var > 0.05,
              f"luminance range {var:.4f} at {os.path.join(OUT, 'stand.png')}")


def check_panel():
    """The BobFoliage panel (docs/FOLIAGE.md 4.2), which is as gateable as a recipe.

    Registers the addon, which everything above deliberately does not: this is the only section
    that needs the ui module, and `headless_redwood.py` does the same for the curve ops. It runs
    LAST for that reason -- once `bbt_env` exists, a build seeds its wind from the world, and every
    measurement above was taken on a still tree.

    What it measures is the three properties the phase promised, plus the one that matters most and
    is the easiest to lose: that the tree-building layer needs no PropertyGroup at all.
    """
    import bob_blender_tools
    from bob_blender_tools.core import env as bbt_env
    from bob_blender_tools.core import foliage_build

    # `check_season` registers the shared world on its own (it is core, so it can); the addon's
    # own register does it again through Firmament and raises on the duplicate class. Hand it back
    # first, so the addon owns it from here as it does in a real session.
    if getattr(bpy.context.scene, "bbt_env", None) is not None:
        try:
            bbt_env.unregister()
        except (RuntimeError, AttributeError):
            pass
    bob_blender_tools.register()
    scene = bpy.context.scene

    # 1. The build layer is bpy-only, not ui-only: core/foliage_build.py imports no ui module and
    #    reads no panel state, which is what keeps BobFoliage off the live-bridge-only list every
    #    curve op is on (docs/MCP.md, known gap). A source check, because an import is a fact.
    src = open(os.path.join(REPO, "blender", "extensions", "bob_blender_tools", "core",
                            "foliage_build.py")).read()
    bad = [tok for tok in ("from ..ui", "from .ui", "import ui", "bbt_foliage.", "bbt_scatter",
                           "bbt_curves") if tok in src]
    check("the tree-building layer imports no ui and reads no panel state", not bad, str(bad))

    # 2. A species preset reaches a tree as plain params, with no panel in the loop at all.
    grown = foliage_build.grow("GateTree", dict(assets.foliage_species("conifer")["params"],
                                                seed=5), species="conifer", scene=scene)
    check("core grows a tree with no panel involved", grown is not None and
          foliage_build.is_foliage(grown), grown.name if grown else "none")
    check("and stamps it, which is how the panel and the wind applier find it",
          foliage_build.species_of(grown) == "conifer", foliage_build.species_of(grown))
    check("it lands in the authoring collection",
          foliage_build.FOLIAGE_COLL in [c.name for c in grown.users_collection],
          str([c.name for c in grown.users_collection]))

    # 3. Adding a second tree does not disturb the first one's tuned knobs.
    tuned = foliage_build.live_input(grown, "Card Size")
    tuned.value = 1.37
    second = foliage_build.grow("GateTree2", dict(assets.foliage_species("broadleaf")["params"]),
                                species="broadleaf", scene=scene)
    scene.bbt_foliage.active = 1
    check("adding a tree leaves another tree's tuned knobs alone",
          abs(foliage_build.live_input(grown, "Card Size").value - 1.37) < 1e-6,
          f"Card Size {foliage_build.live_input(grown, 'Card Size').value:.3f} after a second tree")
    check("and the panel lists both", len(foliage_build.foliage_objects(scene)) == 2,
          str([o.name for o in foliage_build.foliage_objects(scene)]))

    # 4. Loading a species keeps the tree's transform AND its object identity: a preset is params
    #    applied to a tree, not a new tree, so anything pointing at it still does.
    grown.location = (3.0, -4.0, 5.0)
    before = grown.name
    reloaded = foliage_build.load_species(grown, "shrub", scene=scene)
    check("loading a species keeps the same object", reloaded is grown and grown.name == before,
          f"{before} -> {reloaded.name if reloaded else None}")
    check("and its transform", tuple(round(v, 5) for v in grown.location) == (3.0, -4.0, 5.0),
          str(tuple(grown.location)))
    check("and it is now that species", foliage_build.species_of(grown) == "shrub",
          foliage_build.species_of(grown))

    # 5. A structural rebuild keeps tuned live knobs (the reason Build is a press and not a callback).
    foliage_build.live_input(second, "Droop").value = 0.81
    foliage_build.rebuild(second, overrides={"profile_segments": 4}, scene=scene)
    check("a structural rebuild keeps the tuned live knobs",
          abs(foliage_build.live_input(second, "Droop").value - 0.81) < 1e-6,
          f"Droop {foliage_build.live_input(second, 'Droop').value:.3f} after a rebuild")

    # 6. The world feed: the world's wind reaches every tree, with no rebuild and no per-tree press.
    scene.bbt_env.wind_strength = 4.25
    scene.bbt_env.wind_direction = 210.0
    reached = foliage_build.apply_wind(scene)
    winds = [foliage_build.live_input(o, "Wind").value
             for o in foliage_build.foliage_objects(scene)]
    dirs = [foliage_build.live_input(o, "Wind Direction").value
            for o in foliage_build.foliage_objects(scene)]
    check("the world's wind reaches every tree", reached == len(winds)
          and all(abs(w - 4.25) < 1e-5 for w in winds), f"{reached} trees, winds {winds}")
    check("direction too", all(abs(d - 210.0) < 1e-4 for d in dirs), str(dirs))
    # And it is the WORLD applier that does it, so a slider in the World panel is enough.
    from bob_blender_tools.ui import world as ui_world
    scene.bbt_env.wind_strength = 0.75
    ui_world.apply_all(scene)
    check("and the World applier is what runs it, so no BobFoliage press is needed",
          all(abs(foliage_build.live_input(o, "Wind").value - 0.75) < 1e-5
              for o in foliage_build.foliage_objects(scene)),
          str([foliage_build.live_input(o, "Wind").value
               for o in foliage_build.foliage_objects(scene)]))

    # 7. The two texture pickers exist, their Generate buttons are real operators, and Make Variants
    #    is HERE now: F4 kept it off the panel rather than shipping it greyed, and F5 brings it with
    #    the thing it does (docs/FOLIAGE.md 6).
    from bob_blender_tools.ui import foliage as ui_foliage
    props = {p for p in ui_foliage.BBT_FoliageProps.__annotations__}
    check("the panel carries both texture-set pickers", {"bark_set", "atlas"} <= props,
          str(sorted(props & {"bark_set", "atlas"})))
    for op in ("foliage_generate_bark", "foliage_generate_atlas", "foliage_add",
               "foliage_load_species", "foliage_build", "foliage_make_variants"):
        check(f"{op} is a registered operator", hasattr(bpy.ops.bob_blender_tools, op))
    check("and the panel has the knobs Make Variants needs",
          {"variant_count", "variant_lods", "variant_pack"} <= props,
          str(sorted(props & {"variant_count", "variant_lods", "variant_pack"})))

    # 8. F5's own panel checks. The operator has to bake through core and nothing else, and the
    #    baked pool has to feel the world -- which is the half that is easy to lose, because
    #    BOB_Assets_<Kind> is not in the scene and `scene.objects` walks straight past it.
    scene.bbt_foliage.variant_count = 3
    scene.bbt_foliage.variant_lods = False
    scene.bbt_foliage.variant_pack = False
    for other in bpy.context.selected_objects:
        other.select_set(False)
    second.select_set(True)
    bpy.context.view_layer.objects.active = second
    bpy.ops.bob_blender_tools.foliage_make_variants()
    from bob_blender_tools.core import foliage_variants
    kind = foliage_variants.variant_kind(second)
    pooled = foliage_variants.variant_summary(kind)
    check("the panel's Make Variants bakes the active tree through core",
          len(pooled) == 3, f"{len(pooled)} in BOB_Assets_{kind.capitalize()}")
    check("and its variants are measurable, which a pooled object is not by default",
          all(v > 0 for _n, v in pooled), str(pooled))

    scene.bbt_env.wind_strength = 5.5
    ui_world.apply_all(scene)
    winds = {round(foliage_build.live_input(bpy.data.objects[n], "Wind").value, 3)
             for n, _v in pooled}
    check("the World applier reaches the BAKED POOL too, so a stand feels the weather",
          winds == {5.5}, f"pooled winds {sorted(winds)}")
    check("and the pool is still not listed as an authored tree",
          not ({n for n, _v in pooled} & {o.name
                                          for o in foliage_build.foliage_objects(scene)}),
          str([o.name for o in foliage_build.foliage_objects(scene)]))

    # 9. A rebuild keeps the STRUCTURE it was last built with, which is what makes a bake reproduce
    #    the tree rather than its species. Without the build stamp the panel's staged levels were
    #    the only record and a second rebuild silently went back to the preset's depth.
    foliage_build.rebuild(second, overrides={"levels": 2}, scene=scene)
    foliage_build.rebuild(second, scene=scene)
    depth = max((int(it.name[1]) for it in next(m for m in second.modifiers if m.type == "NODES")
                 .node_group.interface.items_tree
                 if it.name.startswith("L") and it.name[1:2].isdigit()), default=0)
    check("a rebuild keeps the structural depth the last one set", depth == 2,
          f"{depth} branch levels after a plain rebuild")


def check_generation(args):
    """F3's two ComfyUI jobs, end to end: generate, resolve, wear, render. SKIPS with no server.

    This is the first foliage phase with a ComfyUI dependency at all, and the property every other
    generation gate has is kept: no server means SKIP and exit 0, because the geometry is procedural
    and both texture sets have a block-out fallback (the placeholder atlas ships; a bark-less trunk is
    a solid tint, which is the block-out convention everywhere else in the suite).

    What it measures, in the order the failures matter:

    - the atlas has real cutout alpha, and EVERY CELL carries a sprite. An empty cell is a card that
      renders as nothing, and a stuck cell index is one leaf repeated ten thousand times.
    - each sprite stands on its cell's bottom edge and on its NARROW end, because that is where the
      card's v is 0. A generation left unoriented attaches by its needle tips.
    - bark tiles AND runs the right way. The seam ratio is the old measure and cannot see direction;
      `grain_report` is the new one, and F3 measured both failures it has to separate -- bark 84 deg
      off vertical (mud cracks, strongly coherent) and bark with no axis at all (coherence 0.018).
    - the round trip: both sets resolve through the same `assets.texture_set_maps` the picker uses,
      reach a tree built from a species preset, put the atlas on a card's Principled Alpha, and
      render not flat.
    """
    from bob_blender_tools.core import comfy, comfy_maps

    ok, detail = comfy.reachable()
    print(f"    ComfyUI: {detail}")
    if not ok:
        print("[SKIP] no ComfyUI server, so F3's two texture jobs cannot run")
        print("    the tree is unaffected: its geometry is procedural and the placeholder atlas "
              "ships, which is the 'ComfyUI is never required' path")
        return
    if args.no_gen:
        print("[SKIP] --no-gen, so F3's two texture jobs were not run")
        return

    pack = os.path.join(OUT, "gen_pack")
    if os.path.isdir(pack):
        shutil.rmtree(pack)
    assets.ensure_generated_pack(pack)
    assets.add_pack_root(pack)

    # 1. The leaf atlas.
    cols, rows = args.cols, args.rows
    atlas_name, info = comfy.leaf_atlas("spruce needle spray", pack, cols=cols, rows=rows,
                                        name="leaf_atlas_gen", seed=1201, size=args.size)
    check("the atlas generated as a texture set", bool(atlas_name), atlas_name)
    maps = assets.texture_set_maps(atlas_name)
    check("the atlas resolves through the ordinary pack resolver",
          bool(maps.get("basecolor")) and bool(maps.get("opacity")), str(sorted(maps)))
    check("the atlas is listed by the picker", atlas_name in assets.list_texture_sets())
    check("the atlas records its own grid", assets.atlas_grid(atlas_name) == (cols, rows),
          str(assets.atlas_grid(atlas_name)))

    with open(maps["opacity"], "rb") as fh:
        opacity = comfy_maps.read_png(fh.read())
    alpha = opacity.astype(np.float32) / 255.0
    clear = float((alpha < 0.05).mean())
    # The redwood run's whole finding was that generated meshes come back opaque (mean alpha 0.998).
    # W4's matte is the thing that is not, and this is where that claim gets re-measured per run.
    check("the generated atlas is a real cutout, not a filled square", clear > 0.35,
          f"{clear * 100:.1f}% clear, {float((alpha > 0.95).mean()) * 100:.1f}% opaque")
    cells = comfy_maps.atlas_cells(opacity, cols, rows)
    empty = [c["cell"] for c in cells if c["opaque"] < 0.01]
    check("every atlas cell carries a sprite", not empty,
          "coverage " + ", ".join(f"{c['opaque'] * 100:.1f}%" for c in cells)
          + (f"; EMPTY {empty}" if empty else ""))
    floating = [c["cell"] for c in cells if not c["reaches_base"]]
    check("every sprite reaches its cell's bottom edge", not floating,
          f"floating cells {floating}" if floating else f"{len(cells)} cells")
    upside = [c["cell"] for c in cells if not c["base_taper"] < 0.6]
    check("every sprite stands on its narrow end, not on its fan", not upside,
          "base/middle width " + ", ".join(f"{c['base_taper']:.2f}" for c in cells))
    distinct = comfy_maps.cell_distinctness(opacity, cols, rows)
    check("the cells are different sprites, not one repeated", distinct > 5.0,
          f"most-similar pair differs by {distinct:.2f}/255 mean alpha")

    # 2. Bark, and the direction the seam ratio cannot see.
    bark_name, bark_info = comfy.bark_set("rough conifer bark", pack, name="bark_conifer",
                                          seed=1301, size=args.size)
    check("the bark set generated", bool(bark_name), bark_name)
    bark_maps = assets.texture_set_maps(bark_name)
    for role in ("basecolor", "roughness", "height", "ao", "normal"):
        check(f"the bark set carries {role}", role in bark_maps,
              os.path.basename(bark_maps.get(role, "")))
    seam = bark_info["seam"]
    check("bark tiles: the seam is no worse than the interior detail", seam["ratio"] < 1.35,
          f"seam {seam['seam']:.3f} vs interior {seam['interior']:.3f}, ratio {seam['ratio']:.3f}")
    grain = bark_info["grain"]
    # Measured across two species and two seeds, the shipped clause held inside 17.6 deg and every
    # rejected wording exceeded 71. 25 is that result with headroom, not a hopeful number.
    check("bark grain runs along the trunk, not across it",
          grain["off_vertical_deg"] < 25.0,
          f"{grain['off_vertical_deg']:.1f} deg off vertical "
          f"(grain axis {grain['grain_deg']:.1f} deg)")
    check("bark has a grain at all, rather than being isotropic", grain["coherence"] > 0.15,
          f"coherence {grain['coherence']:.3f}")
    check("and the axis holds across the tile rather than wandering",
          grain["block_spread_deg"] < 20.0,
          f"block spread {grain['block_spread_deg']:.1f} deg over {len(grain['block_axes'])} blocks")

    # 3. The round trip. A species preset already NAMES bark_conifer, so this is the wiring the
    #    presets were pointed at: generate it, rebuild, and the tree is wearing it with no assignment
    #    step anywhere. That is the check that the preset edit was more than a string.
    check("the conifer preset's bark set now resolves",
          not [k for k, _l, _v in assets.foliage_missing_sets("conifer") if k == "bark_set"],
          str(assets.foliage_missing_sets("conifer")))
    params = dict(assets.foliage_species("conifer")["params"], atlas=atlas_name, seed=9)
    apply_op({"op": "build_geonodes", "recipe": "foliage", "name": "Gen", "params": params,
              "reset": True})
    ev = bpy.data.objects["Gen"].evaluated_get(bpy.context.evaluated_depsgraph_get())
    gmesh = ev.to_mesh()
    check("the generated tree still builds", len(gmesh.polygons) > 0,
          f"{len(gmesh.vertices)} verts, {len(card_faces(gmesh))} cards")
    ev.to_mesh_clear()

    bark_mat = bpy.data.materials["M_Gen Bark"]
    imgs = [n for n in bark_mat.node_tree.nodes if n.bl_idname == "ShaderNodeTexImage"]
    check("the generated bark reached the bark material", len(imgs) >= 3, f"{len(imgs)} images")
    check("and it samples the tree's own UVs, not world space",
          all(n.projection == "FLAT" for n in imgs),
          str(sorted({n.projection for n in imgs})))
    card_mat = bpy.data.materials["M_Gen Leaf"]
    bsdf = next(n for n in card_mat.node_tree.nodes
                if n.bl_idname == "ShaderNodeBsdfPrincipled")
    src = bsdf.inputs["Alpha"].links[0] if bsdf.inputs["Alpha"].is_linked else None
    check("the generated atlas reaches a card's Principled Alpha",
          src is not None and src.from_node.bl_idname == "ShaderNodeTexImage",
          f"{src.from_node.bl_idname}" if src else "not linked")
    check("and it is the generated OPACITY map, not the basecolor's alpha channel",
          src is not None and src.from_node.image is not None
          and os.path.basename(src.from_node.image.filepath).endswith("_opacity.png"),
          os.path.basename(src.from_node.image.filepath)
          if src and src.from_node.image else "none")

    # 4. A frame. Every check above passes on a wired-and-dead shader graph.
    os.makedirs(OUT, exist_ok=True)
    for obj in list(bpy.data.objects):
        if obj.name != "Gen":
            bpy.data.objects.remove(obj, do_unlink=True)
    tree = bpy.data.objects["Gen"]
    height = max(v.co.z for v in tree.evaluated_get(
        bpy.context.evaluated_depsgraph_get()).to_mesh().vertices)
    bpy.ops.object.light_add(type="SUN", location=(0, 0, height * 2))
    bpy.context.active_object.data.energy = 5.0
    bpy.ops.object.camera_add(location=(0, -height * 1.4, height * 0.45),
                              rotation=(math.pi / 2, 0, 0))
    bpy.context.scene.camera = bpy.context.active_object
    var = None
    for engine in ("BLENDER_EEVEE", "BLENDER_EEVEE_NEXT"):
        var = render_variance(engine, os.path.join(OUT, "generated"))
        if var is not None:
            break
    if var is None:
        print("[SKIP] EEVEE could not render the generated tree in this environment")
    else:
        check("a tree wearing both generated sets renders and is not flat", var > 0.05,
              f"luminance range {var:.4f}")

    secs = info["seconds"]
    print()
    print("    wall clock, warm server")
    print(f"      leaf atlas, {cols}x{rows} cells        {secs['generate']:6.2f} s")
    print(f"      compose + derive its maps          {secs['derive']:6.2f} s")
    print(f"      bark set                           "
          f"{bark_info['seconds']['generate']:6.2f} s")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--no-gen", action="store_true",
                    help="skip F3's ComfyUI jobs even when a server is reachable")
    ap.add_argument("--cols", type=int, default=2, help="atlas columns to generate")
    ap.add_argument("--rows", type=int, default=2, help="atlas rows to generate")
    ap.add_argument("--size", type=int, default=1024, help="atlas / bark resolution")
    args = ap.parse_args(argv if argv is not None
                         else (sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []))

    evaluated, mesh, params = build("Tree")

    # 1. It builds something, and the trunk stands on the ground at the height asked for.
    check("the recipe builds a mesh", len(mesh.vertices) > 0 and len(mesh.polygons) > 0,
          f"{len(mesh.vertices)} verts, {len(mesh.polygons)} faces")
    zs = [v.co.z for v in mesh.vertices]
    check("the base sits on the origin plane", abs(min(zs)) < 0.05, f"z min {min(zs):.4f}")
    check("the crown reaches above the trunk top", max(zs) > params["height"],
          f"z max {max(zs):.2f} against height {params['height']}")

    # 2. The level stack multiplies. This is the check that a level is really growing off the
    #    previous one: 9 branches on the trunk, 5 on each of those, 4 on each of THOSE.
    counts = splines_per_level(mesh, params)
    expected = {0: 1, 1: 9, 2: 45, 3: 180}
    check("every level has a whole number of curves", all(isinstance(v, int) for v in counts.values()),
          f"{counts}")
    check("the level stack multiplies through three levels", counts == expected,
          f"{counts} against {expected}")

    # 3. The attached-base invariant, which is the one that silently ruins a tree. The bend offset
    #    is weighted by the branch's own spline factor, so a point at factor 0 must not have moved --
    #    it is the point that coincides with the parent point it was instanced on.
    t = attr(mesh, "bbt_fol_t")
    off = attr(mesh, "bbt_fol_off")
    base = [o for o, factor in zip(off, t) if factor < 1e-4]
    upper = [o for o, factor in zip(off, t) if factor > 0.9]
    check("branch bases were not displaced at all", base and max(base) == 0.0,
          f"{len(base)} base verts, max offset {max(base) if base else 'none'}")
    # Without this the check above passes on a recipe whose bend does nothing whatsoever.
    check("but the bend does move the rest", upper and max(upper) > 0.01,
          f"max offset {max(upper):.4f} at the tips")

    # 4. The tip flag reaches exactly one ring per curve, which is what F2 instances cards on.
    tips = sum(1 for v in attr(mesh, "bbt_fol_tip") if v > 0.5)
    total_curves = sum(expected.values())
    check("one tip ring per curve", tips == total_curves * params["profile_segments"],
          f"{tips} tip verts against {total_curves} curves x {params['profile_segments']}")

    # 5. Parent length reaches the children: a level-3 twig must be far shorter than the trunk. The
    #    failure this catches is a level scaling by its OWN length, which makes every branch trunk-sized.
    plen = attr(mesh, "bbt_fol_plen")
    levels = attr(mesh, "bbt_fol_level")
    by_level = {}
    for level, length in zip(levels, plen):
        by_level.setdefault(level, []).append(length)
    means = {k: sum(v) / len(v) for k, v in sorted(by_level.items())}
    ordered = [means[k] for k in sorted(means)]
    check("each level is shorter than its parent", all(a > b for a, b in zip(ordered, ordered[1:])),
          ", ".join(f"L{k} {v:.2f} m" for k, v in means.items()))
    evaluated.to_mesh_clear()

    # 6. The seed actually reaches the geometry. If it does not, a stand of scattered variants is a
    #    stand of clones, which is the whole reason the scatter route bakes N seeds.
    ev_a, mesh_a, _ = build("SeedA", seed=1)
    pos_a = [tuple(round(c, 5) for c in v.co) for v in mesh_a.vertices]
    ev_a.to_mesh_clear()
    ev_b, mesh_b, _ = build("SeedB", seed=2)
    pos_b = [tuple(round(c, 5) for c in v.co) for v in mesh_b.vertices]
    ev_b.to_mesh_clear()
    check("two seeds give the same vertex budget", len(pos_a) == len(pos_b),
          f"{len(pos_a)} against {len(pos_b)}")
    differing = sum(1 for a, b in zip(pos_a, pos_b) if a != b)
    check("two seeds give different trees", differing > 0.5 * len(pos_a),
          f"{differing}/{len(pos_a)} verts differ")

    # 7. `levels` changes depth rather than being decorative.
    ev_one, mesh_one, params_one = build("Shrub", levels=1)
    check("levels=1 builds a trunk and one branch level",
          set(Counter(attr(mesh_one, "bbt_fol_level"))) == {0, 1},
          f"{sorted(Counter(attr(mesh_one, 'bbt_fol_level')))}")
    ev_one.to_mesh_clear()

    # 8. Skeleton Only emits curves, so the sweep is skipped entirely rather than hidden.
    apply_op({"op": "build_geonodes", "recipe": "foliage", "name": "Skel",
              "params": dict(BASE, skeleton=True), "reset": True})
    skel = bpy.data.objects["Skel"].evaluated_get(bpy.context.evaluated_depsgraph_get())
    skel_mesh = skel.to_mesh()
    check("Skeleton Only produces no swept mesh", len(skel_mesh.polygons) == 0,
          f"{len(skel_mesh.polygons)} faces")
    skel.to_mesh_clear()

    # 9. The profile is the mesh's cost knob, and it should be linear in it.
    ev_c, mesh_c, _ = build("Coarse", profile_segments=3)
    coarse = len(mesh_c.vertices)
    ev_c.to_mesh_clear()
    ev_f, mesh_f, _ = build("Fine", profile_segments=12)
    fine = len(mesh_f.vertices)
    ev_f.to_mesh_clear()
    check("vertex count is linear in the profile", fine == coarse * 4,
          f"profile 3 -> {coarse} verts, profile 12 -> {fine}")

    # -- F2: cards, atlas, UVs, materials, and the routing that waited on them -------------------
    check_radius()
    check_scale_invariance()
    check_crown()

    # -- F6: what stops a limb being a pipe, and where the leaves sit on it -----------------------
    # Before the card checks, because `check_leaves` establishes that the default selection is still
    # the tip-only one those checks measure.
    check_shape()
    check_leaves()

    check_cards()
    check_atlas()
    check_uvs()
    check_materials()
    check_atlas_ships()
    check_species()
    check_routing()

    # -- F3: the bark UV seam, the grain measure, the atlas sidecar, and the two jobs --------------
    check_bark_uv()
    check_grain()
    check_atlas_sidecar()

    # -- F4: the wind, what it costs the shared groups, the translucency, the season, the panel ----
    check_wind()
    check_wind_phase()
    check_no_master_change()
    check_translucency()

    check_render()      # deletes everything but its own tree to get a clean frame

    # -- F5: the variants, the ladder, the pack writer and the stand ------------------------------
    # After the render, because each of these wipes the scene to bake into a clean pool.
    check_variants()
    check_variants_alive()
    check_variant_phase()
    check_lods()
    tmp = tempfile.mkdtemp(prefix="bbt_foliage_")
    try:
        check_variant_pack(tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    check_stand()       # a terrain, a real density, and the frame beside the redwood reference

    check_season()      # renders its own frames the same way, one per season
    check_generation(args)  # needs a server; renders its own frame
    # Last, because it registers the addon: once bbt_env exists a build seeds its wind from the
    # world, and every measurement above was taken on a still tree.
    check_panel()

    print()
    if FAILURES:
        print(f"{len(FAILURES)} FAILED: " + "; ".join(FAILURES))
    else:
        print("all checks passed")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
