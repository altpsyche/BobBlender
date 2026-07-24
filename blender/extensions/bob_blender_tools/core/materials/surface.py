"""S_SurfaceMaster, the surface wrapper, the convert-in-place path (bobshade_material),
and new_bobshader -- the factory that dispatches surface/terrain/water master kinds onto an
object."""

import os

import bpy

from .shared import SURFACE_MASTER, SURFACE_WRAPPER_PREFIX, _build_wrapper, _cached_group, _gin, _gout, _macro_break, _mmath, _vscale, assign_material
from .weather import _WEATHER_EXTRA, weather_group
from .water import water_material
from .terrain import _autoconfig_riverbed, _terrain_maps, terrain_material




def surface_master_group():
    """The single-surface master for props, rocks, vegetation: a solid base colour plus
    scalar roughness and metallic, per-instance variation (Object Info Random jitters the
    brightness so scattered copies differ), ending in S_Weather. Outputs the weathered
    Base Color, Roughness, Metallic for the wrapper's Principled BSDF.

    S1 is solid-colour only. Base Color is authored as the TINT it will become at S3: today
    it is the albedo directly; when a texture set lands the albedo is Base Color * map, so
    the same colour drives both looks and switching solid<->textured loses no tuned value.
    Triplanar, anti-tiling, and the texture-set loader are S3, once library/textures/ has
    real maps to project (the plan's recommended texture timing)."""
    g, _fresh = _cached_group(SURFACE_MASTER)
    if not _fresh:
        return g
    _gin(g, "Base Color", "NodeSocketColor", (0.5, 0.5, 0.5, 1.0))
    _gin(g, "Roughness", "NodeSocketFloat", 0.5, 0.0, 1.0)
    _gin(g, "Metallic", "NodeSocketFloat", 0.0, 0.0, 1.0)
    _gin(g, "Variation", "NodeSocketFloat", 0.0, 0.0, 1.0)
    # Texture-set maps (S3). Default to the multiplicative identity so a solid-colour surface
    # is unchanged: white albedo (tint * white = tint) and 1.0 scalars. The wrapper links a
    # texture set into these when one is assigned; the same colour drives both looks.
    _gin(g, "Albedo Map", "NodeSocketColor", (1.0, 1.0, 1.0, 1.0))
    _gin(g, "Roughness Map", "NodeSocketFloat", 1.0, 0.0, 1.0)
    _gin(g, "Metallic Map", "NodeSocketFloat", 1.0, 0.0, 1.0)
    # AO Map (S4/F3): a scalar occlusion map multiplied into the albedo, identity 1.0 = off. The
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
    # Separate Color, its Red output is the unused occlusion; route it into the AO Map socket so
    # the crevices read. No Separate Color (a plain roughness map, or a value) -> no AO, stays 1.0.
    #
    # DELIBERATE ASSUMPTION (audit finding C2, kept as-is): this treats the metallicRoughness R
    # channel as occlusion, which is true for ORM/"arm" packs (Poly Haven, our shipped biome
    # assets -- verified to render correctly) but UNDEFINED per the glTF spec for a plain
    # metallicRoughness texture. A non-ORM asset whose R is 0 would multiply albedo to black, and
    # an asset with AO already baked into its albedo would double-darken. We keep the heuristic
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



def surface_material(mat_name):
    """A single-surface wrapper (S_SurfaceMaster): a solid-tint BobShader whose look comes from
    the master's procedural terms (colour/roughness/metallic + the weather layer). No image
    texture path."""
    master = surface_master_group()

    def wire(nt, grp, bsdf, old_sig):
        return

    return _build_wrapper(mat_name, master, "surface|", wire)
