"""Headless gate for BobFoliage F1 to F3: the tree skeleton, its sweep, its leaf cards, and the two
texture jobs that dress them (docs/FOLIAGE.md).

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

    Held until F2 on purpose: until the cards existed, a panel that sent someone to BobFoliage for
    plants would have been recommending bare sticks. headless_redwood.py owns the D16 half of this
    (that every noted kind is a real kind, and that trees names dead wood); this owns the half that
    only became true with the cards -- that each note now points somewhere, and that the affordance
    it points at exists and resolves a species for every kind that carries the note.
    """
    from bob_blender_tools.ui import scatter as ui_scatter

    notes = ui_scatter._GEN_KIND_NOTE
    check("every noted kind is a real kind",
          set(notes) <= {"trees", "rocks", "plants", "grass"}, f"{sorted(notes)}")
    for kind in ("trees", "plants", "grass"):
        check(f"the '{kind}' note points at BobFoliage", "BobFoliage" in notes.get(kind, ""),
              notes.get(kind, "(no note)"))
    check("the trees note still names dead wood rather than refusing outright",
          "stumps" in notes.get("trees", ""), notes.get("trees", ""))
    check("generated ground clumps are still allowed as filler",
          all("2 m" in notes.get(k, "") for k in ("plants", "grass")),
          f"{notes.get('plants', '')} / {notes.get('grass', '')}")
    check("Grow in BobFoliage exists as an operator",
          hasattr(bpy.types, "BBT_OT_scatter_grow_foliage")
          or hasattr(ui_scatter, "BBT_OT_scatter_grow_foliage"))
    # The button is only drawn when the kind resolves a species, so a note pointing at BobFoliage
    # for a kind nothing grows would be a dead end with an affordance-shaped hole where it says so.
    check("every kind the notes route to BobFoliage can actually be grown",
          all(ui_scatter._foliage_species_for(k) for k in ("trees", "plants", "grass")),
          str({k: ui_scatter._foliage_species_for(k) for k in ("trees", "plants", "grass")}))


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
    check_render()      # deletes everything but its own tree to get a clean frame
    check_generation(args)  # last: needs a server, and renders its own frame the same way

    print()
    if FAILURES:
        print(f"{len(FAILURES)} FAILED: " + "; ".join(FAILURES))
    else:
        print("all checks passed")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
