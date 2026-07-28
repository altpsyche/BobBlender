"""Firmament cached volume and particulate shaders: cloud/fog/ground-fog volumes and
rain/mote materials. Independent of the S_ BobShader system; only shares the node helpers."""

import os

import bpy

from .shared import _mmath, _mrange



CLOUD_MATERIAL = "BOB_CloudVolume"

FOG_MATERIAL = "BOB_FogVolume"


# The Density knob is an artist-friendly scale, not the raw Cycles extinction. Fog
# fills the whole box (unlike clouds, which the Coverage threshold mostly empties),
# so a large box gives high optical depth from a small extinction: raw density ~0.05
# already reads as a thick sea of fog. This factor maps the knob to extinction so the
# usable range sits in friendly single digits (knob ~1 light, ~3 moderate, ~6 thick)
# instead of a cramped 0.01-0.1 band. Measured on a 700 m vista, 2026-07-19.
FOG_DENSITY_SCALE = 0.02



def _attr(nt, name, location):
    node = nt.nodes.new("ShaderNodeAttribute")
    node.attribute_type = "INSTANCER"
    node.attribute_name = name
    node.location = location
    return node.outputs["Fac"]



def cloud_volume_material():
    """A cached thin Principled Volume for a single GN-instanced cloud domain."""
    mat = bpy.data.materials.get(CLOUD_MATERIAL)
    if mat is not None:
        return mat
    mat = bpy.data.materials.new(CLOUD_MATERIAL)
    mat.use_nodes = True
    nt = mat.node_tree
    nt.nodes.clear()
    L = nt.links

    out = nt.nodes.new("ShaderNodeOutputMaterial")
    out.location = (900, 0)
    vol = nt.nodes.new("ShaderNodeVolumePrincipled")
    vol.location = (680, 0)
    vol.inputs["Color"].default_value = (1.0, 1.0, 1.0, 1.0)
    vol.inputs["Anisotropy"].default_value = 0.3

    density_a = _attr(nt, "cloud_density", (-1200, 40))
    detail_a = _attr(nt, "cloud_detail", (-1200, -60))
    soft_a = _attr(nt, "cloud_softness", (-1200, -160))
    cov_a = _attr(nt, "cloud_coverage", (-1200, -260))
    scale_a = _attr(nt, "cloud_scale", (-1200, -360))
    seed_a = _attr(nt, "cloud_seed", (-1200, -460))
    warp_a = _attr(nt, "cloud_warp", (-1200, -560))

    # Cloud shapes: world-space fractal noise, thresholded by Coverage. The sample
    # position is offset by cloud_wind (a per-instance vector the GN recipe advances
    # by wind * scene time), so the pattern drifts through the stationary box: clouds
    # move across the sky while the domain, and its face envelope, stay put.
    geo = nt.nodes.new("ShaderNodeNewGeometry")
    geo.location = (-1200, 320)
    wind_node = nt.nodes.new("ShaderNodeAttribute")
    wind_node.attribute_type = "INSTANCER"
    wind_node.attribute_name = "cloud_wind"
    wind_node.location = (-1200, 460)
    drifted = nt.nodes.new("ShaderNodeVectorMath")
    drifted.operation = "SUBTRACT"
    drifted.location = (-1000, 380)
    L.new(geo.outputs["Position"], drifted.inputs[0])
    L.new(wind_node.outputs["Vector"], drifted.inputs[1])

    # Domain warp: push the sample position around by a lower-frequency noise so the
    # clouds billow organically instead of reading as round blobs. Warp scales the
    # displacement (0 = off). A separate W keeps the warp pattern off the main noise.
    warp_scale = nt.nodes.new("ShaderNodeMath")
    warp_scale.operation = "MULTIPLY"
    warp_scale.location = (-940, -20)
    warp_scale.inputs[1].default_value = 0.45  # warp noise is lower frequency
    L.new(scale_a, warp_scale.inputs[0])
    warp_noise = nt.nodes.new("ShaderNodeTexNoise")
    warp_noise.noise_dimensions = "4D"
    warp_noise.location = (-760, -40)
    warp_noise.inputs["Detail"].default_value = 2.0
    warp_noise.inputs["W"].default_value = 11.0
    L.new(drifted.outputs["Vector"], warp_noise.inputs["Vector"])
    L.new(warp_scale.outputs["Value"], warp_noise.inputs["Scale"])
    warp_centre = nt.nodes.new("ShaderNodeVectorMath")
    warp_centre.operation = "SUBTRACT"
    warp_centre.location = (-560, -40)
    warp_centre.inputs[1].default_value = (0.5, 0.5, 0.5)
    L.new(warp_noise.outputs["Color"], warp_centre.inputs[0])
    warp_amt = nt.nodes.new("ShaderNodeMath")
    warp_amt.operation = "MULTIPLY"
    warp_amt.location = (-560, -200)
    warp_amt.inputs[1].default_value = 90.0  # metres of displacement at Warp 1
    L.new(warp_a, warp_amt.inputs[0])
    warp_off = nt.nodes.new("ShaderNodeVectorMath")
    warp_off.operation = "SCALE"
    warp_off.location = (-380, -40)
    L.new(warp_centre.outputs["Vector"], warp_off.inputs[0])
    L.new(warp_amt.outputs["Value"], warp_off.inputs["Scale"])
    warped = nt.nodes.new("ShaderNodeVectorMath")
    warped.operation = "ADD"
    warped.location = (-380, 220)
    L.new(drifted.outputs["Vector"], warped.inputs[0])
    L.new(warp_off.outputs["Vector"], warped.inputs[1])

    noise = nt.nodes.new("ShaderNodeTexNoise")
    noise.noise_dimensions = "4D"
    noise.location = (-200, 300)
    noise.inputs["Roughness"].default_value = 0.6
    L.new(warped.outputs["Vector"], noise.inputs["Vector"])
    L.new(scale_a, noise.inputs["Scale"])
    L.new(detail_a, noise.inputs["Detail"])
    L.new(seed_a, noise.inputs["W"])

    # Threshold from Coverage. The raw noise clusters near 0.5, so thresholding on
    # (1 - Coverage) crowds the whole useful range into a narrow band. Map Coverage
    # across the band the noise actually occupies instead: threshold = 0.66 at
    # Coverage 0 (almost clear) down to 0.34 at Coverage 1 (mostly filled), so the
    # knob reads roughly linearly over 0..1. Softness widens the transition.
    from_min = nt.nodes.new("ShaderNodeMath")
    from_min.operation = "MULTIPLY_ADD"
    from_min.location = (-620, -180)
    from_min.inputs[1].default_value = -0.36  # slope: overcast near Coverage 0.85
    from_min.inputs[2].default_value = 0.74    # threshold at Coverage 0 (sparse)
    L.new(cov_a, from_min.inputs[0])
    soft_span = nt.nodes.new("ShaderNodeMath")
    soft_span.operation = "ADD"
    soft_span.location = (-620, -320)
    soft_span.inputs[1].default_value = 0.02  # keep the range non-degenerate
    L.new(soft_a, soft_span.inputs[0])
    from_max = nt.nodes.new("ShaderNodeMath")
    from_max.operation = "ADD"
    from_max.location = (-440, -240)
    L.new(from_min.outputs["Value"], from_max.inputs[0])
    L.new(soft_span.outputs["Value"], from_max.inputs[1])

    shape = nt.nodes.new("ShaderNodeMapRange")
    shape.interpolation_type = "SMOOTHSTEP"
    shape.location = (-240, 120)
    L.new(noise.outputs["Fac"], shape.inputs["Value"])
    L.new(from_min.outputs["Value"], shape.inputs["From Min"])
    L.new(from_max.outputs["Value"], shape.inputs["From Max"])

    # Box envelope: fade to zero toward every face so the cloud never cuts off at
    # the domain bound. Generated is the box's own 0..1 space; the Chebyshev
    # distance from the centre is 0.5 at any face, so 0.5 - that is the distance to
    # the nearest face, faded in over a margin.
    texco = nt.nodes.new("ShaderNodeTexCoord")
    texco.location = (-1200, -640)
    centred = nt.nodes.new("ShaderNodeVectorMath")
    centred.operation = "SUBTRACT"
    centred.location = (-1000, -640)
    centred.inputs[1].default_value = (0.5, 0.5, 0.5)
    L.new(texco.outputs["Generated"], centred.inputs[0])
    absv = nt.nodes.new("ShaderNodeVectorMath")
    absv.operation = "ABSOLUTE"
    absv.location = (-820, -640)
    L.new(centred.outputs["Vector"], absv.inputs[0])
    sep = nt.nodes.new("ShaderNodeSeparateXYZ")
    sep.location = (-640, -640)
    L.new(absv.outputs["Vector"], sep.inputs[0])
    mxy = nt.nodes.new("ShaderNodeMath")
    mxy.operation = "MAXIMUM"
    mxy.location = (-460, -640)
    L.new(sep.outputs["X"], mxy.inputs[0])
    L.new(sep.outputs["Y"], mxy.inputs[1])
    mxyz = nt.nodes.new("ShaderNodeMath")
    mxyz.operation = "MAXIMUM"
    mxyz.location = (-280, -640)
    L.new(mxy.outputs["Value"], mxyz.inputs[0])
    L.new(sep.outputs["Z"], mxyz.inputs[1])
    face_dist = nt.nodes.new("ShaderNodeMath")
    face_dist.operation = "SUBTRACT"
    face_dist.location = (-100, -640)
    face_dist.inputs[0].default_value = 0.5
    L.new(mxyz.outputs["Value"], face_dist.inputs[1])
    env = nt.nodes.new("ShaderNodeMapRange")
    env.interpolation_type = "SMOOTHSTEP"
    env.location = (80, -640)
    env.inputs["From Min"].default_value = 0.0
    env.inputs["From Max"].default_value = 0.12  # fade over 12% of the half-extent
    L.new(face_dist.outputs["Value"], env.inputs["Value"])

    shaped = nt.nodes.new("ShaderNodeMath")
    shaped.operation = "MULTIPLY"
    shaped.location = (200, 40)
    L.new(shape.outputs["Result"], shaped.inputs[0])
    L.new(env.outputs["Result"], shaped.inputs[1])
    density = nt.nodes.new("ShaderNodeMath")
    density.operation = "MULTIPLY"
    density.location = (420, 40)
    L.new(shaped.outputs["Value"], density.inputs[0])
    L.new(density_a, density.inputs[1])
    L.new(density.outputs["Value"], vol.inputs["Density"])
    L.new(vol.outputs["Volume"], out.inputs["Volume"])
    return mat



GROUND_FOG_PREFIX = "BOB_GroundFog_"



def _build_fog_material(mat, ground_image):
    """Build the fog volume node tree into mat. If ground_image is given, the height
    profile is terrain-relative (mist hugging the sampled ground); otherwise it is
    box-relative (a slab, densest low, fading to Fog Top).

    Density = fog_density * height_profile^Falloff * noise_mod * XY-wall envelope,
    with Fog Color and Anisotropy driving the Principled Volume directly. The noise
    gets a domain warp (a lower-frequency noise pushing the sample) so banks billow
    organically, lifted from the cloud material. All knobs arrive as INSTANCER
    attributes, the same linchpin path clouds use.
    """
    mat.use_nodes = True
    nt = mat.node_tree
    nt.nodes.clear()
    L = nt.links

    out = nt.nodes.new("ShaderNodeOutputMaterial")
    out.location = (1100, 0)
    vol = nt.nodes.new("ShaderNodeVolumePrincipled")
    vol.location = (880, 0)

    density_a = _attr(nt, "fog_density", (-1500, 40))
    top_a = _attr(nt, "fog_top", (-1500, -60))
    falloff_a = _attr(nt, "fog_falloff", (-1500, -160))
    noise_a = _attr(nt, "fog_noise", (-1500, -260))
    scale_a = _attr(nt, "fog_scale", (-1500, -360))
    detail_a = _attr(nt, "fog_detail", (-1500, -460))
    seed_a = _attr(nt, "fog_seed", (-1500, -560))
    soft_a = _attr(nt, "fog_softness", (-1500, -660))
    warp_a = _attr(nt, "fog_warp", (-1500, -760))
    aniso_a = _attr(nt, "fog_aniso", (-1500, -860))

    # Colour tint and forward scattering straight onto the volume. Anisotropy > 0
    # is forward scattering, which gives the sun-side glow and readable light shafts
    # in mist; the Fog Color tints the scattering albedo (cool shadow / warm dawn).
    color_node = nt.nodes.new("ShaderNodeAttribute")
    color_node.attribute_type = "INSTANCER"
    color_node.attribute_name = "fog_color"
    color_node.location = (600, 240)
    L.new(color_node.outputs["Color"], vol.inputs["Color"])
    L.new(aniso_a, vol.inputs["Anisotropy"])

    # World position; a wind-drifted copy feeds the noise so banks drift while the
    # terrain sampling (below) stays put.
    geo = nt.nodes.new("ShaderNodeNewGeometry")
    geo.location = (-1500, 320)
    wind_node = nt.nodes.new("ShaderNodeAttribute")
    wind_node.attribute_type = "INSTANCER"
    wind_node.attribute_name = "fog_wind"
    wind_node.location = (-1500, 460)
    drifted = nt.nodes.new("ShaderNodeVectorMath")
    drifted.operation = "SUBTRACT"
    drifted.location = (-1300, 380)
    L.new(geo.outputs["Position"], drifted.inputs[0])
    L.new(wind_node.outputs["Vector"], drifted.inputs[1])

    # Domain warp: push the noise sample by a lower-frequency noise so banks billow
    # organically instead of reading as round patches (lifted from the clouds).
    warp_scale = _mmath(nt, "MULTIPLY", scale_a, 0.45, (-1120, -40))
    warp_noise = nt.nodes.new("ShaderNodeTexNoise")
    warp_noise.noise_dimensions = "4D"
    warp_noise.location = (-940, -40)
    warp_noise.inputs["Detail"].default_value = 2.0
    warp_noise.inputs["W"].default_value = 7.0
    L.new(drifted.outputs["Vector"], warp_noise.inputs["Vector"])
    L.new(warp_scale, warp_noise.inputs["Scale"])
    warp_centre = nt.nodes.new("ShaderNodeVectorMath")
    warp_centre.operation = "SUBTRACT"
    warp_centre.location = (-760, -40)
    warp_centre.inputs[1].default_value = (0.5, 0.5, 0.5)
    L.new(warp_noise.outputs["Color"], warp_centre.inputs[0])
    warp_amt = _mmath(nt, "MULTIPLY", warp_a, 90.0, (-760, -220))  # metres at Warp 1
    warp_off = nt.nodes.new("ShaderNodeVectorMath")
    warp_off.operation = "SCALE"
    warp_off.location = (-580, -40)
    L.new(warp_centre.outputs["Vector"], warp_off.inputs[0])
    L.new(warp_amt, warp_off.inputs["Scale"])
    warped = nt.nodes.new("ShaderNodeVectorMath")
    warped.operation = "ADD"
    warped.location = (-580, 300)
    L.new(drifted.outputs["Vector"], warped.inputs[0])
    L.new(warp_off.outputs["Vector"], warped.inputs[1])

    noise = nt.nodes.new("ShaderNodeTexNoise")
    noise.noise_dimensions = "4D"
    noise.location = (-380, 300)
    noise.inputs["Roughness"].default_value = 0.55
    L.new(warped.outputs["Vector"], noise.inputs["Vector"])
    L.new(scale_a, noise.inputs["Scale"])
    L.new(detail_a, noise.inputs["Detail"])
    L.new(seed_a, noise.inputs["W"])

    # Soft banks: threshold the noise at 0.5 with a width from Softness. noise_mod =
    # mix(1, bank, Fog Noise): Fog Noise 0 is a uniform slab, 1 is the banked pattern.
    soft_w = nt.nodes.new("ShaderNodeMath")
    soft_w.operation = "MULTIPLY_ADD"
    soft_w.location = (-200, 60)
    soft_w.inputs[1].default_value = 0.5
    soft_w.inputs[2].default_value = 0.05
    L.new(soft_a, soft_w.inputs[0])
    bank_min = _mmath(nt, "SUBTRACT", 0.5, soft_w.outputs["Value"], (-20, 120))
    bank_max = _mmath(nt, "ADD", 0.5, soft_w.outputs["Value"], (-20, 20))
    bank = _mrange(nt, noise.outputs["Fac"], bank_min, bank_max, 0.0, 1.0, (160, 120))
    banked = _mmath(nt, "MULTIPLY", noise_a, bank, (340, 120))
    uniform = _mmath(nt, "SUBTRACT", 1.0, noise_a, (340, 220))
    noise_mod = _mmath(nt, "ADD", banked, uniform, (520, 180))

    # Generated 0..1 box coords, for the box-relative height profile and the XY-wall
    # envelope (both modes fade at the side walls).
    texco = nt.nodes.new("ShaderNodeTexCoord")
    texco.location = (-1500, -1040)
    sepg = nt.nodes.new("ShaderNodeSeparateXYZ")
    sepg.location = (-1300, -1040)
    L.new(texco.outputs["Generated"], sepg.inputs[0])

    if ground_image is None:
        # Box-relative: dense at the box bottom (Generated Z 0), clear at Fog Top.
        profile_lin = _mrange(nt, sepg.outputs["Z"], 0.0, top_a, 1.0, 0.0, (-1100, -620))
    else:
        # Terrain-relative: sample the heightmap by world XY (UV = xy/size + 0.5),
        # reconstruct terrain Z = (sample - Sea Level) * Terrain Height (matching
        # heightmap_terrain), and fade density with height above that ground.
        size_a = _attr(nt, "fog_terrain_size", (-1500, -960))
        theight_a = _attr(nt, "fog_terrain_height", (-1500, -1060))
        sea_a = _attr(nt, "fog_sea_level", (-1500, -1160))
        gthick_a = _attr(nt, "fog_ground_thickness", (-1500, -1260))
        possep = nt.nodes.new("ShaderNodeSeparateXYZ")
        possep.location = (-1300, -620)
        L.new(geo.outputs["Position"], possep.inputs[0])
        u = _mmath(nt, "ADD", _mmath(nt, "DIVIDE", possep.outputs["X"], size_a, (-1120, -560)), 0.5, (-940, -560))
        v = _mmath(nt, "ADD", _mmath(nt, "DIVIDE", possep.outputs["Y"], size_a, (-1120, -720)), 0.5, (-940, -720))
        uvw = nt.nodes.new("ShaderNodeCombineXYZ")
        uvw.location = (-760, -620)
        L.new(u, uvw.inputs["X"])
        L.new(v, uvw.inputs["Y"])
        tex = nt.nodes.new("ShaderNodeTexImage")
        tex.interpolation = "Linear"
        tex.extension = "EXTEND"
        tex.location = (-580, -620)
        ground_image.colorspace_settings.name = "Non-Color"
        tex.image = ground_image
        L.new(uvw.outputs["Vector"], tex.inputs["Vector"])
        pix = nt.nodes.new("ShaderNodeSeparateColor")
        pix.location = (-380, -620)
        L.new(tex.outputs["Color"], pix.inputs["Color"])
        terrain_z = _mmath(nt, "MULTIPLY",
                           _mmath(nt, "SUBTRACT", pix.outputs["Red"], sea_a, (-200, -560)),
                           theight_a, (-20, -560))
        height_above = _mmath(nt, "SUBTRACT", possep.outputs["Z"], terrain_z, (160, -560))
        profile_lin = _mrange(nt, height_above, 0.0, gthick_a, 1.0, 0.0, (340, -620))

    # Falloff shapes the height curve: >1 hugs the ground more tightly (thins faster
    # with height), <1 fills more evenly. profile in 0..1, so pow keeps it in range.
    profile = _mmath(nt, "POWER", profile_lin, falloff_a, (540, -620))

    # XY distance to the nearest side wall: 0.5 - max(|x-0.5|, |y-0.5|), faded in.
    ax = _mmath(nt, "ABSOLUTE", _mmath(nt, "SUBTRACT", sepg.outputs["X"], 0.5, (-1100, -1120)), loc=(-920, -1120))
    ay = _mmath(nt, "ABSOLUTE", _mmath(nt, "SUBTRACT", sepg.outputs["Y"], 0.5, (-1100, -1240)), loc=(-920, -1240))
    mxy = _mmath(nt, "MAXIMUM", ax, ay, (-740, -1180))
    wall_dist = _mmath(nt, "SUBTRACT", 0.5, mxy, (-560, -1180))
    env = _mrange(nt, wall_dist, 0.0, 0.1, 0.0, 1.0, (-380, -1180))

    # density = fog_density * FOG_DENSITY_SCALE * profile * noise_mod * XY envelope.
    d1 = _mmath(nt, "MULTIPLY", profile, noise_mod, (700, -100))
    d2 = _mmath(nt, "MULTIPLY", d1, env, (700, -260))
    scaled = _mmath(nt, "MULTIPLY", density_a, FOG_DENSITY_SCALE, (700, -420))
    density = _mmath(nt, "MULTIPLY", d2, scaled, (860, -340))
    L.new(density, vol.inputs["Density"])
    L.new(vol.outputs["Volume"], out.inputs["Volume"])
    return mat



def fog_volume_material():
    """The cached box-relative fog material (height_fog and noise_fog share it)."""
    mat = bpy.data.materials.get(FOG_MATERIAL)
    if mat is not None:
        return mat
    return _build_fog_material(bpy.data.materials.new(FOG_MATERIAL), None)



def ground_fog_volume_material(image):
    """A terrain-draped fog material for a given heightmap, cached per image so two
    fogs over the same terrain share it. Density hugs the sampled ground surface, so
    a thin mist follows hills up and over instead of a fixed-Z slab. The image is a
    node property (not a socket), so this material is per-heightmap, unlike the one
    shared box material; the terrain mapping (size, height, sea level, thickness)
    stays live through INSTANCER knobs."""
    if image is None:
        return fog_volume_material()
    name = GROUND_FOG_PREFIX + image.name
    mat = bpy.data.materials.get(name)
    if mat is not None:
        return mat
    return _build_fog_material(bpy.data.materials.new(name), image)



# Particulate surface materials. Unlike the volume materials above these shade
# real instanced geometry (rain streaks, dust/amber/snow motes), not a volume box.
# They read the live Colour and Emission knobs the particulates recipe stores on the
# instance domain, through Attribute nodes of type INSTANCER, the same per-instance
# path clouds and fog use for their knobs. Both are cheap on purpose: rain never uses
# a glass/transmission BSDF (Cycles refraction cost), and motes are scene-lit with
# emission off by default.
RAIN_MATERIAL = "BOB_Rain"

MOTE_MATERIAL = "BOB_Mote"



def _color_attr(nt, name, location):
    node = nt.nodes.new("ShaderNodeAttribute")
    node.attribute_type = "INSTANCER"
    node.attribute_name = name
    node.location = location
    return node



def rain_material():
    """A cheap translucent streak material for rain. A Principled surface mixed with
    a Transparent BSDF so the streaks read as lit, semi-transparent water without the
    refraction cost of a glass/transmission shader. Base colour is the live rain_color
    INSTANCER knob.

    The opacity tapers to zero at both ends of the streak (a soft window along the
    cone's Generated Z), so a streak dissolves into the air instead of reading as
    a hard-capped tube. Generated is the per-instance mesh's own 0..1 local space, so
    the taper follows the streak length whatever its world rotation."""
    mat = bpy.data.materials.get(RAIN_MATERIAL)
    if mat is not None:
        return mat
    mat = bpy.data.materials.new(RAIN_MATERIAL)
    mat.use_nodes = True
    nt = mat.node_tree
    nt.nodes.clear()
    L = nt.links

    out = nt.nodes.new("ShaderNodeOutputMaterial")
    out.location = (600, 0)
    mix = nt.nodes.new("ShaderNodeMixShader")
    mix.location = (400, 0)
    transp = nt.nodes.new("ShaderNodeBsdfTransparent")
    transp.location = (200, 120)
    bsdf = nt.nodes.new("ShaderNodeBsdfPrincipled")
    bsdf.location = (200, -160)
    bsdf.inputs["Roughness"].default_value = 0.1
    color = _color_attr(nt, "rain_color", (-60, -160))
    L.new(color.outputs["Color"], bsdf.inputs["Base Color"])
    L.new(transp.outputs["BSDF"], mix.inputs[1])
    L.new(bsdf.outputs["BSDF"], mix.inputs[2])

    # Lengthwise opacity window: a solid core with soft tips, so the streak core reads
    # legibly and only the ends dissolve (no hard caps). dist = |2z-1| is 0 at the
    # centre and 1 at either end; a smoothstep keeps opacity full out to 60% of the
    # half-length, then eases to 0 by the tip. Generated is the per-instance mesh's own
    # 0..1 space, so the taper follows the streak whatever its world rotation.
    texco = nt.nodes.new("ShaderNodeTexCoord")
    texco.location = (-360, 240)
    gsep = nt.nodes.new("ShaderNodeSeparateXYZ")
    gsep.location = (-180, 240)
    L.new(texco.outputs["Generated"], gsep.inputs[0])
    doubled = _mmath(nt, "MULTIPLY", gsep.outputs["Z"], 2.0, (0, 300))
    dist = _mmath(nt, "ABSOLUTE", _mmath(nt, "SUBTRACT", doubled, 1.0, (160, 300)), loc=(320, 300))
    # plateau: 1.0 for dist <= 0.6, easing to 0 by dist 1.0
    window = _mrange(nt, dist, 0.6, 1.0, 1.0, 0.0, (480, 320))
    fac = _mmath(nt, "MULTIPLY", window, 0.95, (660, 360))
    L.new(fac, mix.inputs["Fac"])
    L.new(mix.outputs["Shader"], out.inputs["Surface"])
    return mat



def mote_material():
    """A scene-lit mote material for dust, amber motes, and falling snow, with the
    plan's golden-hour catch: a Translucent BSDF is mixed with the diffuse Principled
    base, so light passing through a mote from behind scatters forward and the specks
    glow / rim-light when backlit by a low sun (the amber-dust look), instead of reading
    as flat diffuse dots. Translucent is cheap (no refraction), consistent with the
    never-glass rule. Both surfaces are tinted by the live mote_color knob, and an
    Emission term scaled by mote_emission (default 0) is added on top, leaving room for
    fireflies or embers. All three are INSTANCER knobs."""
    mat = bpy.data.materials.get(MOTE_MATERIAL)
    if mat is not None:
        return mat
    mat = bpy.data.materials.new(MOTE_MATERIAL)
    mat.use_nodes = True
    nt = mat.node_tree
    nt.nodes.clear()
    L = nt.links

    out = nt.nodes.new("ShaderNodeOutputMaterial")
    out.location = (600, 0)
    add = nt.nodes.new("ShaderNodeAddShader")
    add.location = (400, 0)
    lit = nt.nodes.new("ShaderNodeMixShader")
    lit.location = (200, 160)
    lit.inputs["Fac"].default_value = 0.5  # half diffuse, half forward-scatter glow
    bsdf = nt.nodes.new("ShaderNodeBsdfPrincipled")
    bsdf.location = (0, 240)
    bsdf.inputs["Roughness"].default_value = 0.9
    trans = nt.nodes.new("ShaderNodeBsdfTranslucent")
    trans.location = (0, 40)
    emit = nt.nodes.new("ShaderNodeEmission")
    emit.location = (200, -220)

    color = _color_attr(nt, "mote_color", (-260, 40))
    emission_a = _attr(nt, "mote_emission", (-260, -260))
    L.new(color.outputs["Color"], bsdf.inputs["Base Color"])
    L.new(color.outputs["Color"], trans.inputs["Color"])
    L.new(color.outputs["Color"], emit.inputs["Color"])
    L.new(emission_a, emit.inputs["Strength"])
    L.new(bsdf.outputs["BSDF"], lit.inputs[1])
    L.new(trans.outputs["BSDF"], lit.inputs[2])
    L.new(lit.outputs["Shader"], add.inputs[0])
    L.new(emit.outputs["Emission"], add.inputs[1])
    L.new(add.outputs["Shader"], out.inputs["Surface"])
    return mat
