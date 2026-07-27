"""Headless gate for BobFoliage F1: the procedural tree skeleton and its sweep (docs/FOLIAGE.md).

    ~/.steam/steam/steamapps/common/Blender/blender --background --factory-startup \
        --python tools/scripts/headless_foliage.py

Exit code 0 = every check passed.

It MEASURES the structure rather than asserting the graph was built, because every way this recipe
goes wrong still renders something tree-shaped:

- a branch whose base was displaced off its parent leaves a floating stick,
- a level whose rotation order is wrong collapses into a flat fan,
- a level that reads its own length instead of its parent's makes twigs as long as the trunk,
- a seed that reaches nothing makes a stand of identical clones,

and none of those raises. The per-level spline count, the base-offset invariant and the seed
divergence are the three numbers that separate a tree from a tree-shaped accident.

The recipe writes the attributes this reads (`bbt_fol_level`, `bbt_fol_t`, `bbt_fol_off`,
`bbt_fol_tip`) for the shader and for F2's leaf cards; the gate is a second consumer, not the reason
they exist.
"""

import os
import sys
from collections import Counter

import bpy

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(REPO, "blender", "extensions"))

from bob_blender_tools.core.dispatch import apply_op  # noqa: E402

FAILURES = []

# One explicit set of params, so every expected count below is arithmetic rather than a default that
# can drift underneath the gate.
BASE = {"levels": 3, "height": 20.0, "seed": 3, "segments": 14, "branch_segments": 6,
        "profile_segments": 6, "l1_branches": 9, "l2_branches": 5, "l3_branches": 4}


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


def main():
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

    print()
    if FAILURES:
        print(f"{len(FAILURES)} FAILED: " + "; ".join(FAILURES))
    else:
        print("all checks passed")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
