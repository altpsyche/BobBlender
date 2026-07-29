"""S_SurfaceMaster, the surface wrapper, the convert-in-place path (bobshade_material),
and new_bobshader -- the factory that dispatches surface/terrain/water master kinds onto an
object."""

import os

import bpy

from .. import assets
from . import texset
from .shared import SURFACE_MASTER, SURFACE_WRAPPER_PREFIX, _build_wrapper, _cached_group, _gin, _gout, _macro_break, _mmath, _vscale, _wrapper_name, assign_material, group_version
from .weather import _WEATHER_EXTRA, LEAF_SEASON, weather_group
from .water import water_material
from .terrain import _autoconfig_riverbed, _terrain_maps, terrain_material




def surface_master_group():
    """The single-surface master for props, rocks, vegetation: a solid base colour plus
    scalar roughness and metallic, per-instance variation (Object Info Random jitters the brightness
    so scattered copies differ), ending in S_Weather. Outputs the weathered Base Color, Roughness,
    Metallic for the wrapper's Principled BSDF.

    The master is solid-colour only until a texture set is assigned. Base Color is authored as
    the TINT a texture set multiplies into: today it is the albedo directly; when a texture set
    lands the albedo is Base Color * map, so the same colour drives both looks and switching
    solid<->textured loses no tuned value. Triplanar, anti-tiling, and the texture-set loader all
    arrived once library/textures/ has real maps to project (the plan's recommended texture
    timing)."""
    g, _fresh = _cached_group(SURFACE_MASTER)
    if not _fresh:
        return g
    _gin(g, "Base Color", "NodeSocketColor", (0.5, 0.5, 0.5, 1.0))
    _gin(g, "Roughness", "NodeSocketFloat", 0.5, 0.0, 1.0)
    _gin(g, "Metallic", "NodeSocketFloat", 0.0, 0.0, 1.0)
    _gin(g, "Variation", "NodeSocketFloat", 0.0, 0.0, 1.0)
    # Texture-set maps. Default to the multiplicative identity so a solid-colour surface
    # is unchanged: white albedo (tint * white = tint) and 1.0 scalars. The wrapper links a
    # texture set into these when one is assigned; the same colour drives both looks.
    _gin(g, "Albedo Map", "NodeSocketColor", (1.0, 1.0, 1.0, 1.0))
    _gin(g, "Roughness Map", "NodeSocketFloat", 1.0, 0.0, 1.0)
    _gin(g, "Metallic Map", "NodeSocketFloat", 1.0, 0.0, 1.0)
    # AO Map: a scalar occlusion map multiplied into the albedo, identity 1.0 = off. The
    # convert path feeds this with the arm map's AO (R) channel (glTF drops occlusionTexture, so
    # the packed AO would otherwise go unused); the texture-set path folds AO into its albedo
    # instead, so it leaves this at 1.0 (no double-darkening). A solid-colour surface keeps 1.0.
    _gin(g, "AO Map", "NodeSocketFloat", 1.0, 0.0, 1.0)
    # Macro break-up (anti-tiling): a low-frequency world noise modulating albedo brightness
    # so a repeating texture stops reading as a tile at distance. Amount 0 = off.
    _gin(g, "Macro Amount", "NodeSocketFloat", 0.0, 0.0, 1.0)
    _gin(g, "Macro Scale", "NodeSocketFloat", 0.2, 0.0)
    _gin(g, "Snow Strength", "NodeSocketFloat", 1.0, 0.0, 1.0)
    _gin(g, "Slope Threshold", "NodeSocketFloat", 0.5, 0.0, 1.0)
    _gin(g, "Slope Falloff", "NodeSocketFloat", 0.2, 0.0, 1.0)
    _gin(g, "Canopy Snow", "NodeSocketFloat", 0.0, 0.0, 1.0)  # scattered assets raise it (S_Weather)
    for _wn, _wd in _WEATHER_EXTRA:
        _gin(g, _wn, "NodeSocketFloat", _wd, 0.0, 1.0)
    _gout(g, "Base Color", "NodeSocketColor")
    _gout(g, "Roughness", "NodeSocketFloat")
    _gout(g, "Metallic", "NodeSocketFloat")

    gi = g.nodes.new("NodeGroupInput")
    gi.location = (-900, 0)
    go = g.nodes.new("NodeGroupOutput")
    go.location = (700, 0)
    I = gi.outputs

    # Per-instance variation: Object Info Random (0..1) jitters brightness by +/- Variation
    # via the HSV Value, so two instances of the same wrapper differ. Object Info Random is
    # the free per-GN-instance / per-object random (Phase-0 mechanism A).
    obj = g.nodes.new("ShaderNodeObjectInfo")
    obj.location = (-900, -320)
    r05 = _mmath(g, "SUBTRACT", obj.outputs["Random"], 0.5, (-700, -320))
    rv = _mmath(g, "MULTIPLY", r05, I["Variation"], (-540, -320))
    vfac = g.nodes.new("ShaderNodeMath")
    vfac.operation = "ADD"
    vfac.use_clamp = False
    vfac.location = (-380, -320)
    vfac.inputs[1].default_value = 1.0
    g.links.new(rv, vfac.inputs[0])
    hsv = g.nodes.new("ShaderNodeHueSaturation")
    hsv.location = (-200, -160)
    g.links.new(I["Base Color"], hsv.inputs["Color"])
    g.links.new(vfac.outputs["Value"], hsv.inputs["Value"])

    # Colour as tint: albedo = (varied base colour) * Albedo Map, then macro break-up.
    tinted = g.nodes.new("ShaderNodeVectorMath")
    tinted.operation = "MULTIPLY"
    tinted.location = (0, 60)
    g.links.new(hsv.outputs["Color"], tinted.inputs[0])
    g.links.new(I["Albedo Map"], tinted.inputs[1])
    albedo = _macro_break(g, tinted.outputs["Vector"], I["Macro Amount"], I["Macro Scale"], (200, 120))
    albedo = _vscale(g, albedo, I["AO Map"], (390, 120))  # fold occlusion (1.0 = off)
    rough = _mmath(g, "MULTIPLY", I["Roughness"], I["Roughness Map"], (0, -60))
    metal = _mmath(g, "MULTIPLY", I["Metallic"], I["Metallic Map"], (0, -140))

    weather = g.nodes.new("ShaderNodeGroup")
    weather.node_tree = weather_group()
    weather.location = (400, 0)
    g.links.new(albedo, weather.inputs["Base Color"])
    g.links.new(rough, weather.inputs["Roughness"])
    g.links.new(metal, weather.inputs["Metallic"])
    for name in ("Snow Strength", "Slope Threshold", "Slope Falloff",
                 "Canopy Snow", *[n for n, _ in _WEATHER_EXTRA]):
        g.links.new(I[name], weather.inputs[name])
    for name in ("Base Color", "Roughness", "Metallic"):
        g.links.new(weather.outputs[name], go.inputs[name])
    return g



def bobshade_material(mat, variation=0.15):
    """Convert an existing (imported) material into a BobShader in place: route its Principled
    Base Color / Roughness / Metallic through S_SurfaceMaster so the asset's OWN textures gain
    per-instance variation, macro break-up, and the full weather layer (snow / wet / frost /
    dust / moss), while its Alpha, Normal, and Emission stay untouched.

    The asset keeps its native UV-mapped maps (triplanar and the texture-set loader are for
    un-UV'd/solid surfaces and are left off here): the captured albedo/roughness/metallic feed
    the master's *map* inputs, the tint is white and the scalars 1 so the maps read at face
    value, and coverage is computed (Use Attribute 0, since scattered assets carry no snow_cover
    pass). Idempotent (skips a material that already has a Master node). Returns True if shaded."""
    if mat is None or not mat.use_nodes or mat.node_tree is None:
        return False
    nt = mat.node_tree
    if nt.nodes.get("Master") is not None:
        return False  # already a BobShader
    bsdf = next((n for n in nt.nodes if n.bl_idname == "ShaderNodeBsdfPrincipled"), None)
    if bsdf is None:
        return False

    def capture(sock_name):
        s = bsdf.inputs.get(sock_name)
        if s is None:
            return None, None
        if s.links:
            return s.links[0].from_socket, None
        v = s.default_value
        if hasattr(v, "__len__") and not isinstance(v, str):
            v = tuple(v)
        return None, v

    alb_src, alb_val = capture("Base Color")
    rgh_src, rgh_val = capture("Roughness")
    met_src, met_val = capture("Metallic")

    # AO from the packed arm map: glTF splits metallicRoughness through a Separate Color (G ->
    # roughness, B -> metallic) and drops the R (AO) channel. When Roughness comes from that
    # Separate Color, its Red output is the unused occlusion; route it into the AO Map socket so the
    # crevices read. No Separate Color (a plain roughness map, or a value) -> no AO, stays 1.0.
    #
    # DELIBERATE ASSUMPTION (a deliberate audit finding, kept as-is): this treats the
    # metallicRoughness R channel as occlusion, which is true for ORM/"arm" packs (Poly Haven, our
    # shipped biome assets -- verified to render correctly) but UNDEFINED per the glTF spec for a
    # plain metallicRoughness texture. A non-ORM asset whose R is 0 would multiply albedo to black,
    # and an asset with AO already baked into its albedo would double-darken. We keep the heuristic
    # because every asset the library ships is ORM; revisit with importer-aware occlusion-source
    # detection (only route AO when the arm image is also the material's occlusionTexture) if a
    # non-ORM asset is ever imported. Legacy ShaderNodeSeparateRGB exposes this channel as "R", not
    # "Red", so AO is silently skipped for those older imports (harmless: falls back to 1.0).
    ao_src = None
    if rgh_src is not None and rgh_src.node.bl_idname in ("ShaderNodeSeparateColor", "ShaderNodeSeparateRGB"):
        ao_src = rgh_src.node.outputs.get("Red")

    grp = nt.nodes.new("ShaderNodeGroup")
    grp.name = "Master"
    grp.node_tree = surface_master_group()
    grp.location = (bsdf.location.x - 360, bsdf.location.y + 260)
    grp.inputs["Base Color"].default_value = (1.0, 1.0, 1.0, 1.0)  # white tint: maps at face value
    grp.inputs["Roughness"].default_value = 1.0
    grp.inputs["Metallic"].default_value = 0.0
    # Scattered assets are near-vertical (trunks, cone-canopy sides), so the up-facing snow model
    # misses them; raise Canopy Snow so snow/frost/dust also hold on the instance's upper bbox.
    grp.inputs["Canopy Snow"].default_value = 1.0
    grp.inputs["Variation"].default_value = variation

    def feed(map_name, src, val):
        inp = grp.inputs.get(map_name)
        if inp is None:
            return
        if src is not None:
            nt.links.new(src, inp)
        elif val is not None:
            try:
                inp.default_value = val
            except (TypeError, ValueError):
                pass

    feed("Albedo Map", alb_src, alb_val)
    feed("Roughness Map", rgh_src, rgh_val)
    feed("Metallic Map", met_src, met_val)
    feed("AO Map", ao_src, None)  # only when the arm map exposed an AO (R) channel
    nt.links.new(grp.outputs["Base Color"], bsdf.inputs["Base Color"])
    nt.links.new(grp.outputs["Roughness"], bsdf.inputs["Roughness"])
    nt.links.new(grp.outputs["Metallic"], bsdf.inputs["Metallic"])
    mat["bbt_shaded"] = True
    return True



def new_bobshader(obj, master="surface"):
    """Create (or get) a per-object BobShader wrapper auto-named M_<object>, wire the chosen
    master, assign it GN-aware, and return the material. Identity is the datablock on the object's
    slot, not a stored name; get-or-create keeps a re-New from wiping tuned inputs."""
    if master == "terrain":
        fresh = SURFACE_WRAPPER_PREFIX + obj.name not in bpy.data.materials
        flow, wet, size = _terrain_maps(obj)
        mat = terrain_material(obj.name, flow_image=flow, wetness_image=wet, terrain_size=size)
        # Only auto-configure a riverbed on a genuinely fresh material (never clobber a re-New's
        # tuned inputs), and only when the drainage maps are present to key off.
        if fresh and (flow is not None or wet is not None):
            _autoconfig_riverbed(mat)
    elif master == "water":
        mat = water_material(obj.name)
    else:
        mat = surface_material(obj.name)
    assign_material(obj, mat)
    return mat



def surface_material(mat_name, texset_name=None, box=None, alpha=False, leaf=False):
    """A single-surface wrapper (S_SurfaceMaster): a solid-tint BobShader whose look comes from
    the master's procedural terms (colour/roughness/metallic + the weather layer).

    With a texture set assigned, the set's albedo, with AO folded in, and its roughness feed
    the master's map inputs, and its height drives a Bump into the Principled Normal. Base Color
    stays the TINT it was authored as, so switching solid <-> textured loses no tuned value.
    texset_name "" clears the set; both arguments default to what the material already records.

    `alpha` adds the cutout path a leaf card needs (BobFoliage, docs/FOLIAGE.md 2.7): the set's
    `opacity` map if it ships one, else the basecolor image's OWN alpha channel, straight into the
    Principled Alpha. It deliberately does NOT go through the master. Alpha is a matte -- it says
    which texels are leaf and which are the gap between leaves -- and every term the master adds is
    about how a surface looks where it EXISTS (tint, per-instance variation, macro break-up, snow,
    wet, frost). Routing a matte through them would let a wet leaf turn semi-transparent. Keeping it
    outside also means no new master output, so S_SurfaceMaster's interface is untouched and no
    tuned terrain in the file gets reset by a shared-group version bump.

    `leaf` adds the two terms a CARD needs on top of that cutout (BobFoliage): the season layer
    (`_wire_season`) and the translucency (`_wire_translucency`). Both stay outside the master for
    the reasons in `foliage_card_material`.
    """
    master = surface_master_group()
    prev = bpy.data.materials.get(_wrapper_name(mat_name))
    sets, prev_box = texset.stored_sets(prev, 1)
    if texset_name is not None:
        sets = [str(texset_name or "")]
    box = prev_box if box is None else bool(box)
    maps = assets.texture_set_maps(sets[0]) if sets[0] else {}
    # The leaf part of the signature carries S_LeafSeason's VERSION, not a flag: a bump there
    # changes that group's interface, which leaves this wrapper's instance sockets at type-zero, so
    # the wrapper has to rebuild with it. Same reason `texset.sig_part` carries S_TexSet's.
    sig = "surface|" + texset.sig_part(sets, box) + (f"|alpha:{int(bool(alpha))}") \
        + (f"|leaf:{group_version(LEAF_SEASON) if leaf else 0}")

    def wire(nt, grp, bsdf, old_sig):
        cutout = None
        if maps:
            coord = nt.nodes.new("ShaderNodeTexCoord")
            coord.name = texset.TEXSET_NODE_PREFIX + "Coord"
            coord.location = (-1100, 400)
            # A prop carries UVs, so flat projection uses them; box projection is the un-UV'd case
            # (and the default here), and needs a 3D coordinate instead.
            src = coord.outputs["Object"] if box else coord.outputs["UV"]
            node = texset.texset_sample(nt, "S", maps, src, box=box, loc=(-800, 400))
            if node is not None:
                nt.links.new(node.outputs["Albedo Map"], grp.inputs["Albedo Map"])
                nt.links.new(node.outputs["Roughness Map"], grp.inputs["Roughness Map"])
                texset.texset_bump(nt, node.outputs["Detail Height"], bsdf, loc=(100, -340))
                if alpha:
                    cutout = _wire_cutout(nt, maps, src, bsdf, box)
        # The leaf terms do NOT depend on a texture set resolving: a block-out card with no atlas
        # still turns in autumn and still lets light through, which is the whole point of the
        # solid-tint fallback everywhere else in the suite.
        if leaf:
            base = _wire_season(nt, grp, bsdf)
            _wire_translucency(nt, bsdf, base, cutout)

    mat = _build_wrapper(mat_name, master, sig, wire)
    texset.store_sets(mat, sets, box)
    return mat



def _wire_cutout(nt, maps, coord, bsdf, box):
    """Drive the Principled Alpha from a set's cutout. Returns the socket wired, or None.

    Two sources, in order: a dedicated `opacity` map (what the atlas job emits), else the basecolor
    image's own alpha channel (what a matted `mesh_subject` subject already carries, measured as a
    real 0.000-1.000 range). Preferring the dedicated map means a generated set can carry one
    without touching this, and falling back means the card work's placeholder RGBA atlas works with
    no extra file.
    """
    src = None
    if maps.get("opacity"):
        try:
            img = bpy.data.images.load(maps["opacity"], check_existing=True)
        except RuntimeError:
            img = None
        if img is not None:
            img.colorspace_settings.name = "Non-Color"
            tex = nt.nodes.new("ShaderNodeTexImage")
            tex.name = texset.TEXSET_NODE_PREFIX + "S opacity"
            tex.image = img
            tex.projection = "BOX" if box else "FLAT"
            tex.extension = "REPEAT"
            tex.location = (-480, -600)
            nt.links.new(coord, tex.inputs["Vector"])
            src = tex.outputs["Color"]
    if src is None:
        base = nt.nodes.get(texset.TEXSET_NODE_PREFIX + "S basecolor")
        src = base.outputs["Alpha"] if base is not None else None
    if src is not None:
        nt.links.new(src, bsdf.inputs["Alpha"])
    return src



def _wire_season(nt, grp, bsdf):
    """Insert S_LeafSeason between the master's Base Color and the Principled. Returns its output.

    After the master, not inside it: the season reads the WEATHERED colour, so a snowed leaf turns
    under its snow rather than over it, and a wet autumn canopy is a wet autumn canopy. Before the
    Principled, so the translucency below transmits the turned colour and an autumn tree glows
    amber from behind, which is the single most recognisable thing a backlit autumn tree does.
    """
    from .weather import leaf_season_group

    node = nt.nodes.new("ShaderNodeGroup")
    node.name = texset.TEXSET_NODE_PREFIX + "Leaf Season"
    node.node_tree = leaf_season_group()
    node.location = (60, 260)
    nt.links.new(grp.outputs["Base Color"], node.inputs["Base Color"])
    nt.links.new(node.outputs["Base Color"], bsdf.inputs["Base Color"])
    return node.outputs["Base Color"]


# How much of a leaf's light comes through it rather than off it, and the hue it comes through as.
# 0.25 is a leaf, not a sheet of paper; the transmit tint is warm because chlorophyll passes green
# and red and holds blue back, which is why a backlit canopy is warmer than a lit one.
LEAF_TRANSLUCENCY = 0.25

_LEAF_TRANSMIT = (1.0, 0.72, 0.28, 1.0)


def _wire_translucency(nt, bsdf, base_color, cutout):
    """Mix a Translucent BSDF into the card's surface, gated by the same cutout the Principled uses.

    **Why this is not a term on S_SurfaceMaster** (the deferral, answered by the wind pass). Three
    reasons, and the last one is decisive:

    - The master is SHARED. Terrain and water embed it, a rebuild reassigns every socket identifier,
      and the cost of adding one socket to it is a revert-to-default on every tuned terrain in the
      file (the item-3 and snow-line bumps both paid exactly that). Translucency reaches leaves.
    - It is the alpha argument again (docs/FOLIAGE.md 2.7). Every term the master adds says how a
      surface looks WHERE IT EXISTS; a matte says which texels exist, and translucency says what
      happens to light that does not stop there. Both are about the leaf as a thin OBJECT rather
      than as a surface, and routing them through the weather layer would let a wet leaf turn
      transparent and a snowed one glow.
    - **The master's contract cannot express it.** It outputs Base Color, Roughness and Metallic --
      three scalars into one Principled. Translucency is a second BSDF lobe. There is no socket
      shape that carries it, so putting it "on the master" would mean widening the wrapper for
      every master, and the wrapper is what the terrain and water masters share too.

    The cutout gate is the part that has to be right. A plain Mix Shader between the Principled and
    a Translucent would light the whole quad: the Principled's Alpha only mattes the Principled, so
    the translucent branch would fill in every texel the atlas cut away and a spray would render as
    a glowing rectangle. So the translucent branch is mixed against a Transparent BSDF by the SAME
    cutout first. Each branch is matted exactly once, so the edges do not thin the way an alpha
    applied twice would.
    """
    out = next((n for n in nt.nodes if n.bl_idname == "ShaderNodeOutputMaterial"), None)
    if out is None:
        return None
    trans = nt.nodes.new("ShaderNodeBsdfTranslucent")
    trans.location = (300, -560)
    tint = nt.nodes.new("ShaderNodeMix")
    tint.name = texset.TEXSET_NODE_PREFIX + "Leaf Transmit"
    tint.data_type = "RGBA"
    tint.location = (100, -560)
    tint.inputs[0].default_value = 0.55          # how far toward the transmit hue
    tint.inputs[7].default_value = _LEAF_TRANSMIT
    if base_color is not None:
        nt.links.new(base_color, tint.inputs[6])
    else:
        tint.inputs[6].default_value = (1.0, 1.0, 1.0, 1.0)
    nt.links.new(tint.outputs[2], trans.inputs["Color"])

    clear = nt.nodes.new("ShaderNodeBsdfTransparent")
    clear.location = (300, -720)
    # Both Mix Shader inputs are named "Shader", so they are reached POSITIONALLY (1 and 2).
    gate = nt.nodes.new("ShaderNodeMixShader")
    gate.name = texset.TEXSET_NODE_PREFIX + "Leaf Cutout Gate"
    gate.location = (480, -600)
    gate.inputs[0].default_value = 1.0   # no cutout map: the branch is simply not matted
    if cutout is not None:
        nt.links.new(cutout, gate.inputs[0])
    nt.links.new(clear.outputs["BSDF"], gate.inputs[1])
    nt.links.new(trans.outputs["BSDF"], gate.inputs[2])

    mix = nt.nodes.new("ShaderNodeMixShader")
    mix.name = texset.TEXSET_NODE_PREFIX + "Leaf Translucency"
    mix.location = (660, -300)
    mix.inputs[0].default_value = LEAF_TRANSLUCENCY
    nt.links.new(bsdf.outputs["BSDF"], mix.inputs[1])
    nt.links.new(gate.outputs["Shader"], mix.inputs[2])
    nt.links.new(mix.outputs["Shader"], out.inputs["Surface"])
    return mix


def foliage_card_material(mat_name, atlas="leaf_atlas_blockout"):
    """The leaf-card BobShader (BobFoliage): the `surface` master with a cutout, not a fourth
    master. Answers the open question in docs/FOLIAGE.md 2.7 the way that section preferred.

    What a card needs is alpha cutout, two-sided shading and some translucency. Two of the three
    were already free at the time: Blender shades both faces unless `use_backface_culling` is set,
    and the cutout is one link (see `surface_material`'s `alpha`). **The wind pass added the third,
    and kept it outside the master too** -- see `_wire_translucency` for the argument, which turned
    out to be stronger than the one that deferred it: the master's contract is three scalars into
    one Principled, and translucency is a second BSDF lobe, so there is no socket shape on the
    master that could carry it. The season colour (`_wire_season`) sits in the same place for the
    same kind of reason.

    So no fourth master, and S_SurfaceMaster's interface is still untouched by this whole track.

    Flat (UV) projection, not box: a card's whole point is that it reads a specific ATLAS CELL, and
    a box projection would sample by world position and put a different part of the atlas on every
    card at the same tip. The recipe writes the UVs that pick the cell.
    """
    fresh = _wrapper_name(mat_name) not in bpy.data.materials
    mat = surface_material(mat_name, texset_name=atlas, box=False, alpha=True, leaf=True)
    if fresh:
        node = mat.node_tree.nodes.get("Master")
        if node is not None:
            # A white tint reads the atlas at face value, the same convention bobshade_material
            # uses. Only on a fresh material: re-running a build must not clobber a tuned colour.
            node.inputs["Base Color"].default_value = (1.0, 1.0, 1.0, 1.0)
            node.inputs["Roughness"].default_value = 0.62
            node.inputs["Canopy Snow"].default_value = 1.0  # a leaf card is not an up-facing plane
    # EEVEE Next has no blend_method any more: DITHERED is its cutout mode, and it is already the
    # default. Set it anyway so a card stays a cutout if a caller changed the material, and let the
    # shadow follow the alpha or a canopy casts a solid rectangle.
    if hasattr(mat, "surface_render_method"):
        mat.surface_render_method = "DITHERED"
    mat.use_backface_culling = False  # two-sided, which is what makes a card read from behind
    if hasattr(mat, "use_transparent_shadow"):
        mat.use_transparent_shadow = True
    return mat
