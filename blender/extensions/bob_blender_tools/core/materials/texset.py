"""The texture-set sampler (BobShaders, the texture-set sampler): one shared S_TexSet group that turns
a `<pack>/textures/<set>/` set into the map values S_TerrainMaster and S_SurfaceMaster already
accept, plus the wrapper-side plumbing that instances it per terrain layer.

Why one shared group. A set is up to four images and a terrain carries six layer slots, so the naive
graph is thirty image nodes plus six copies of the same fold maths, against EEVEE's sampler budget
(docs/GENERATION.md the sampler-budget rule). Here the fold maths lives once in S_TexSet and is
INSTANCED per layer (one node in the material, whatever the maths costs), and image texture nodes
are created only for the layers that actually carry a set.

What lands where. The terrain master carries per-layer Albedo Map / Roughness Map / Detail Height;
the surface master carries Albedo Map / Roughness Map / Metallic Map / AO Map. So:

- AO is FOLDED into the albedo rather than routed to its own socket. That is the convention
  surface.py's AO Map socket already documents (the convert path owns that socket, the texture-set
  path folds, and nothing double-darkens), and it is the only option on terrain, which has no
  per-layer AO socket at all.
- Metallic is left alone. No set on disk ships a metallic map and nature surfaces are dielectric;
  both masters already carry a Metallic scalar for the case that is not true.
- The height map is not a master input at all. It comes back out of the master's blended Height
  output (terrain) or straight off the sampler (surface) and drives a Bump into the wrapper's
  Principled Normal. That gives a set real surface relief without either master gaining a normal
  socket, and therefore without a shared-group version bump, which would reset every tuned terrain
  in the file.
- The normal map on disk is consequently unused. Using it would need a per-layer vector
  socket on the master (the version-bump cost above) and a tangent space that box projection does
  not have.

Projection. Blender's own box projection on the image node IS the triplanar option: one node
property, no hand-rolled three-sample graph, and identical in EEVEE and Cycles. It is per-material
rather than per-layer, because six independent projection toggles on one ground material is knob
sprawl with no case behind it. Terrain projects from OBJECT coordinates in both modes: it is a
GN-generated grid with no UV layer, so a UV projection would sample nothing, and flat there means a
top-down planar projection, which is the right default for ground. A surface prop has UVs, so its
flat mode uses them.
"""

import bpy

from .shared import (
    S_GROUP_VER,
    TEXSET_NODE_PREFIX,
    _GROUP_VER_OVERRIDE,
    _cached_group,
    _gin,
    _gout,
    _lerp,
    _mmath,
    _vscale,
)

TEXSET_SAMPLER = "S_TexSet"

# Where a wrapper records what it was built from, so a rebuild (adding a layer, re-Build from
# the panel) carries the assignment forward instead of dropping back to solid tints. The
# material is the identity, matching the panel's native-identity rule: no panel-local state.
TEXSETS_PROP = "bbt_texsets"
TEXSET_BOX_PROP = "bbt_texset_box"

# (set role on disk, S_TexSet input socket, is the image data rather than colour). Order is the
# vertical node layout order too. `normal` and `metallic` are deliberately absent; see the
# module docstring.
_ROLES = (("basecolor", "Albedo", False),
          ("roughness", "Roughness", True),
          ("ao", "AO", True),
          ("height", "Height", True))



def sampler_ver():
    """The version S_TexSet is currently stamped with. Folded into every wrapper signature that
    instances the group (see sig_part)."""
    return _GROUP_VER_OVERRIDE.get(TEXSET_SAMPLER, S_GROUP_VER)



def texset_sampler_group():
    """The shared sampler: sampled maps in, master-socket values out.

    Albedo Map is the basecolor with AO folded in; Roughness Map is the roughness map faded
    toward its 1.0 identity by Roughness Amount; Detail Height is the height map recentred on
    0.5 (so a flat grey map is zero relief, not a constant lift) and scaled. All three amounts
    default to a full-strength, no-surprises read of the set."""
    g, fresh = _cached_group(TEXSET_SAMPLER)
    if not fresh:
        return g
    _gin(g, "Albedo", "NodeSocketColor", (1.0, 1.0, 1.0, 1.0))
    _gin(g, "Roughness", "NodeSocketFloat", 1.0, 0.0, 1.0)
    _gin(g, "AO", "NodeSocketFloat", 1.0, 0.0, 1.0)
    _gin(g, "Height", "NodeSocketFloat", 0.5, 0.0, 1.0)
    _gin(g, "AO Amount", "NodeSocketFloat", 1.0, 0.0, 1.0)
    _gin(g, "Roughness Amount", "NodeSocketFloat", 1.0, 0.0, 1.0)
    _gin(g, "Detail Height", "NodeSocketFloat", 1.0, 0.0, 4.0)
    _gout(g, "Albedo Map", "NodeSocketColor")
    _gout(g, "Roughness Map", "NodeSocketFloat")
    _gout(g, "Detail Height", "NodeSocketFloat")

    gi = g.nodes.new("NodeGroupInput")
    gi.location = (-620, 0)
    go = g.nodes.new("NodeGroupOutput")
    go.location = (620, 0)
    I = gi.outputs

    # Albedo times AO, and it STAYS times AO. The forest-barn gate raised this as a suspected
    # double-count -- a generated albedo carries baked light, `comfy_maps.ao_from` derives occlusion
    # from that same luminance, and the product would then count the shading twice -- and the
    # measurement says otherwise, so nothing here changes and `AO_STRENGTH` stays where it is.
    #
    # The reason is a cutoff. `comfy_maps.relief`, which the AO is derived from, is a high-pass at a
    # thirty-second of the image, so it never contained the low-frequency lighting in the first
    # place; `comfy_maps.delight` corrects at an eighth. The two act on different scales and cannot
    # overlap. Measured on the forest-floor set either side of delighting, AO against basecolor
    # luminance went 0.656 to 0.6665 -- it rose, because removing the ramp makes the surviving
    # detail a larger share of the albedo's variance. Across all ten shipped sets it rose or held
    # every time. What the AO agrees with is cavity and sub-thirty-second shading, which is what an
    # AO map is FOR.
    ao = _lerp(g, 1.0, I["AO"], I["AO Amount"], (-360, 260))
    g.links.new(_vscale(g, I["Albedo"], ao, (60, 320)), go.inputs["Albedo Map"])
    g.links.new(_lerp(g, 1.0, I["Roughness"], I["Roughness Amount"], (-360, -20)),
                go.inputs["Roughness Map"])
    centred = _mmath(g, "SUBTRACT", I["Height"], 0.5, (-360, -320))
    g.links.new(_mmath(g, "MULTIPLY", centred, I["Detail Height"], (-180, -320)),
                go.inputs["Detail Height"])
    return g



def texset_images(maps):
    """{role: image datablock} for a texture_set_maps() result, colour-managed by role.
    Get-or-create by path (check_existing), so re-assigning the same set across materials shares
    one datablock instead of stacking duplicates. A file Blender cannot read is skipped."""
    out = {}
    for role, _sock, data in _ROLES:
        path = maps.get(role)
        if not path:
            continue
        try:
            img = bpy.data.images.load(path, check_existing=True)
        except RuntimeError:
            continue
        img.colorspace_settings.name = "Non-Color" if data else "sRGB"
        out[role] = img
    return out



def texset_sample(nt, key, maps, coord, box=True, loc=(0, 0)):
    """Add one set's sampler to a wrapper node tree and return its S_TexSet group node, or None
    when the set yielded no usable image.

    `key` names the nodes ("L0" for a terrain layer, "S" for a surface), so everything added is
    prefixed TEXSET_NODE_PREFIX and _build_wrapper restores its tuned inputs across a structural
    rebuild. One Mapping node per set carries the tiling; the image nodes share it."""
    imgs = texset_images(maps)
    if not imgs:
        return None
    x, y = loc
    p = TEXSET_NODE_PREFIX + key

    mapping = nt.nodes.new("ShaderNodeMapping")
    mapping.name = p + " Mapping"
    mapping.label = "Tiling"
    mapping.location = (x, y)
    nt.links.new(coord, mapping.inputs["Vector"])

    grp = nt.nodes.new("ShaderNodeGroup")
    grp.name = p
    grp.node_tree = texset_sampler_group()
    grp.location = (x + 620, y)

    for n, (role, sock, _data) in enumerate(_ROLES):
        img = imgs.get(role)
        if img is None:
            continue
        tex = nt.nodes.new("ShaderNodeTexImage")
        tex.name = f"{p} {role}"
        tex.image = img
        tex.projection = "BOX" if box else "FLAT"
        tex.projection_blend = 0.3
        tex.extension = "REPEAT"
        tex.location = (x + 320, y - n * 180)
        nt.links.new(mapping.outputs["Vector"], tex.inputs["Vector"])
        # A data map's Color output is (v, v, v); linking it to a Float socket takes the
        # average, which is v. One link per role rather than a Separate Color per map.
        nt.links.new(tex.outputs["Color"], grp.inputs[sock])
    return grp



def texset_bump(nt, height, bsdf, loc=(0, 0)):
    """Drive the wrapper's Principled Normal from a height signal: the master's blended Detail
    Height on terrain (so the relief follows whichever layer won the height-lerp per texel), or
    the single sampler's on a surface. Returns the Bump node, whose Strength is the live knob."""
    bump = nt.nodes.new("ShaderNodeBump")
    bump.name = TEXSET_NODE_PREFIX + "Bump"
    bump.location = loc
    bump.inputs["Strength"].default_value = 0.5
    nt.links.new(height, bump.inputs["Height"])
    nt.links.new(bump.outputs["Normal"], bsdf.inputs["Normal"])
    return bump



def stored_sets(mat, count):
    """(sets, box) as recorded on a wrapper material: `count` set names ("" = none, padded and
    truncated to length) and the box-projection flag. A material with nothing recorded (a fresh
    one, or one built before the sampler existed) reads as no sets and box projection on."""
    raw = mat.get(TEXSETS_PROP) if mat is not None else None
    sets = [str(s) for s in raw] if raw is not None else []
    sets = (sets + [""] * count)[:count]
    box = mat.get(TEXSET_BOX_PROP) if mat is not None else None
    return sets, True if box is None else bool(box)



def store_sets(mat, sets, box):
    """Record what a wrapper was built from, for the next rebuild to carry forward."""
    mat[TEXSETS_PROP] = list(sets)
    mat[TEXSET_BOX_PROP] = int(bool(box))



def sig_part(sets, box):
    """The wrapper-signature fragment for a texture-set assignment. It carries the shared
    sampler's version as well, so bumping S_TexSet alone still rebuilds the wrappers that
    instance it: an in-place group rebuild leaves existing instance sockets at type-zero, and
    _build_wrapper's re-seed path only covers the Master node."""
    return "|tex:" + ",".join(sets) + f"|box:{int(bool(box))}|tsv:{sampler_ver()}"
