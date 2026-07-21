"""Volume material builders (shaders). The only non-GN, non-world shading code in
BobFirmament, cached by name.

The cloud volume is a thin Principled Volume filling ONE domain box (the whole
cloud layer, not a field of small cubes, which showed their box seams and clipped
at each face). The cloud shapes come entirely from analytic 3D noise sampled in
WORLD space: where the noise clears a Coverage threshold there is cloud, elsewhere
there is open sky, so the layer is continuous with no seams. Two things keep the
box itself invisible:

- The density fades to zero toward every face of the box (a soft envelope on the
  box's own Generated 0..1 coordinates), so the cloud never cuts off at the bound.
- The noise is world-space, so the interior detail does not tile or repeat.

The live knobs (Density, Detail, Softness, Coverage, Cloud Scale, Cloud Seed) arrive
as instance attributes the volumetrics recipe stores on the single instanced box,
read here through Attribute nodes of type INSTANCER, the mechanism the Phase-0
linchpin confirmed carries a GN value into a volume shader.
"""

import os

import bpy

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


def _mplug(nt, socket, val):
    if isinstance(val, (int, float)):
        socket.default_value = val
    else:
        nt.links.new(val, socket)


def _mmath(nt, op, a, b=None, loc=(0, 0)):
    n = nt.nodes.new("ShaderNodeMath")
    n.operation = op
    n.location = loc
    _mplug(nt, n.inputs[0], a)
    if b is not None:
        _mplug(nt, n.inputs[1], b)
    return n.outputs["Value"]


def _mrange(nt, val, fmin, fmax, tmin, tmax, loc):
    n = nt.nodes.new("ShaderNodeMapRange")
    n.interpolation_type = "SMOOTHSTEP"
    n.location = loc
    _mplug(nt, n.inputs["Value"], val)
    _mplug(nt, n.inputs["From Min"], fmin)
    _mplug(nt, n.inputs["From Max"], fmax)
    _mplug(nt, n.inputs["To Min"], tmin)
    _mplug(nt, n.inputs["To Max"], tmax)
    return n.outputs["Result"]


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


# Particulate surface materials (S4). Unlike the volume materials above these shade
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


# BobShaders surface masters (S1). Unlike everything above (Firmament's volume and
# particulate materials, which shade one effect) these are the artist-facing surface
# framework: shared shader NODE GROUPS (S_<Effect>) that a thin per-object wrapper
# material (M_<Surface>) instances, the Blender-native equivalent of an Unreal master
# material plus instances. One graph to maintain, many looks. Cached by name like the
# volume materials, and get-or-create so a re-Build never wipes a wrapper's tuned inputs.
#
# The stack, top to bottom:
#   M_<Surface>       one S_SurfaceMaster group node -> one Principled BSDF -> Output
#   S_SurfaceMaster   solid base colour + per-instance variation, ending in S_Weather
#   S_Weather         the shared weather layer (S1: the snow term only)
#   S_EnvState        the world-to-shader bridge: internal Value nodes driven once from
#                     scene.bbt_env and shared by every material that instances the group
ENV_STATE = "S_EnvState"
WEATHER = "S_Weather"
SURFACE_MASTER = "S_SurfaceMaster"
SURFACE_WRAPPER_PREFIX = "M_"

# The S_EnvState internal Value nodes the panel drives from bbt_env: (node name, field,
# default). One driver per node on the single shared group feeds every material (the
# Phase-0 finding). The panel installs and reinstalls these; per-material drivers on the
# same fields remain the known-good fallback if the shared-drive path ever regresses.
ENV_STATE_DRIVERS = (
    ("env_snow", "snow", 0.0),
    ("env_wetness", "wetness", 0.0),
    ("env_temperature", "temperature", 15.0),
    ("env_weather", "weather", 0.0),  # enum index; mapped to effective wetness in S_EnvState
)

# Snow shading constants (the surface-snow look): a slightly cool near-white albedo and a
# soft, high roughness. Kept here so the shader and any future accumulation-shell tint agree.
_SNOW_ALBEDO = (0.90, 0.93, 0.97, 1.0)
_SNOW_ROUGHNESS = 0.6
# S4 weather-term tints: warm dust, dark moss, cool frost.
_DUST_COLOR = (0.55, 0.47, 0.33, 1.0)
_MOSS_COLOR = (0.12, 0.22, 0.06, 1.0)
_FROST_COLOR = (0.82, 0.87, 0.96, 1.0)
# The extra S_Weather inputs the masters must expose and pass through (name, default).
_WEATHER_EXTRA = (
    ("Wetness Strength", 1.0), ("Wet Pooling", 0.0), ("Frost Strength", 1.0),
    ("Dust Amount", 0.0), ("Moss Amount", 0.0),
)
# env.weather enum indices (must match env.py WEATHER order): rain and storm wet the ground.
_WEATHER_RAIN, _WEATHER_STORM = 3, 4


def _gin(g, name, stype, default=None, mn=None, mx=None):
    """Add an INPUT socket to a shader node group interface, with optional clamp."""
    s = g.interface.new_socket(name, in_out="INPUT", socket_type=stype)
    if default is not None:
        s.default_value = default
    if mn is not None:
        s.min_value = mn
    if mx is not None:
        s.max_value = mx
    return s


def _gout(g, name, stype):
    return g.interface.new_socket(name, in_out="OUTPUT", socket_type=stype)


def _cplug(g, socket, val):
    """Plug a socket, a scalar, or a colour/vector tuple into a group socket."""
    if isinstance(val, (tuple, list)):
        socket.default_value = val
    elif isinstance(val, (int, float)):
        socket.default_value = val
    else:
        g.links.new(val, socket)


def _mixcol(g, fac, col_a, col_b, loc):
    """Colour mix by fac (ShaderNodeMix, RGBA). Index sockets, not names: the Mix node
    carries Float/Vector/Color A+B sockets all named 'A'/'B', so a name lookup is
    ambiguous. For RGBA the layout is Factor=inputs[0], A=inputs[6], B=inputs[7],
    Result=outputs[2] (stable across 4.x/5.x)."""
    n = g.nodes.new("ShaderNodeMix")
    n.data_type = "RGBA"
    n.location = loc
    _cplug(g, n.inputs[0], fac)
    _cplug(g, n.inputs[6], col_a)
    _cplug(g, n.inputs[7], col_b)
    return n.outputs[2]


def _vscale(g, vec, scalar, loc):
    """Scale a colour/vector by a scalar (VectorMath SCALE)."""
    n = g.nodes.new("ShaderNodeVectorMath")
    n.operation = "SCALE"
    n.location = loc
    _cplug(g, n.inputs[0], vec)
    _mplug(g, n.inputs["Scale"], scalar)
    return n.outputs["Vector"]


# Bump when any shared S_* group's interface (sockets) changes. A group cached in an
# older .blend carries a stale (or absent) stamp and is rebuilt in place on first
# access, so an addon upgrade refreshes the interface instead of reusing a group that
# lacks new sockets -- which otherwise KeyErrors when the caller wires them, or
# silently renders the old behaviour.
# v2 (BobSplines C5): the water master (S_WaterMaster) landed and S_TerrainMaster gained the
# bbt_curve_wet damp-bed read, so existing cached groups must rebuild to pick both up.
# v3 (water look pass W1-W6): S_WaterMaster gained multi-scale flow waves, crisp/shore foam, and a
# manual Frozen -> ice path (new Shore Foam / Foam Crispness / Wave Detail / Frozen sockets), so a
# cached v2 water group must rebuild to expose them.
S_GROUP_VER = 3

# Per-group version overrides. A rebuild clears the interface, which RESETS the tuned inputs of every
# material instancing the group (the new sockets get fresh identifiers, verified), so a global
# S_GROUP_VER bump wipes terrain/surface tuning too. When a change is scoped to ONE group, version it
# here instead so only that group rebuilds and the rest keep their tuned values. (The string keys are
# the group names, == the WATER_MASTER etc. constants defined below.)
#   S_WaterMaster v4: geometry Gerstner waves in curve_water made the shader's low-frequency flow
#   bump redundant -- it now carries only a subtle high-frequency detail normal (the old one combed
#   into hair-like streaks). Graph + default change, same interface, so water-only rebuild.
_GROUP_VER_OVERRIDE = {"S_WaterMaster": 4}


def _cached_group(name):
    """Get-or-create a version-stamped shared shader group. Returns (group, needs_build);
    when needs_build is False the cached group is current and the caller returns it as-is.
    A stale group is rebuilt in place (datablock kept, nodes + interface cleared) so
    materials already referencing it pick up the fresh interface rather than dangling. The
    expected version is the per-group override if present, else the shared S_GROUP_VER."""
    want = _GROUP_VER_OVERRIDE.get(name, S_GROUP_VER)
    g = bpy.data.node_groups.get(name)
    if g is not None and g.get("bbt_ver") == want:
        return g, False
    if g is not None:
        g.nodes.clear()
        for item in list(g.interface.items_tree):
            g.interface.remove(item)
    else:
        g = bpy.data.node_groups.new(name, "ShaderNodeTree")
    g["bbt_ver"] = want
    return g, True


def env_state_group():
    """The world-to-shader bridge: one shared group whose internal Value nodes hold the
    live env fields, driven once from scene.bbt_env by the panel. Because a node group is
    a single datablock shared by every material that instances it, driving it once feeds
    every surface (Phase-0). No inputs; outputs Snow, Wetness, Temperature. When Firmament
    is absent no driver is installed and the Value defaults stand (no snow), so a material
    still renders standalone."""
    g, _fresh = _cached_group(ENV_STATE)
    if not _fresh:
        return g
    _gout(g, "Snow", "NodeSocketFloat")
    _gout(g, "Wetness", "NodeSocketFloat")
    _gout(g, "Temperature", "NodeSocketFloat")
    go = g.nodes.new("NodeGroupOutput")
    go.location = (600, 0)
    O = go.inputs

    # The driven raw fields (one driver each on these Value nodes, installed by the panel).
    val = {}
    for i, (node_name, _field, default) in enumerate(ENV_STATE_DRIVERS):
        v = g.nodes.new("ShaderNodeValue")
        v.name = v.label = node_name
        v.location = (-500, -i * 160)
        v.outputs[0].default_value = default
        val[node_name] = v.outputs[0]

    g.links.new(val["env_snow"], O["Snow"])
    g.links.new(val["env_temperature"], O["Temperature"])

    # The weather -> wetness mapping (the one convergence spot, documented in SHADERS.md):
    # effective wetness = max(env.wetness, weather contribution), where weather in {rain,
    # storm} raises it (rain 0.6, storm 1.0) and clear/cloud/fog do not wet the ground.
    w = val["env_weather"]
    ge = _mmath(g, "GREATER_THAN", w, _WEATHER_RAIN - 0.5, (-260, -420))
    le = _mmath(g, "LESS_THAN", w, _WEATHER_STORM + 0.5, (-260, -560))
    band = _mmath(g, "MULTIPLY", ge, le, (-90, -480))       # 1 for weather in {rain, storm}
    lvl = _mmath(g, "MULTIPLY", _mmath(g, "SUBTRACT", w, float(_WEATHER_RAIN), (-90, -620)),
                 0.4, (80, -620))                            # rain 0.0, storm 0.4
    lvl = _mmath(g, "ADD", 0.6, lvl, (250, -620))            # rain 0.6, storm 1.0
    wwet = _mmath(g, "MULTIPLY", band, lvl, (250, -480))
    eff = _mmath(g, "MAXIMUM", val["env_wetness"], wwet, (420, -360))
    g.links.new(eff, O["Wetness"])
    return g


def weather_group():
    """The shared weather layer, ending every master. S1 carries the snow term only.

    Coverage has a single authority: read the snow_cover attribute (Geometry Attribute
    node, POINT) where the Firmament GN pass ran (the terrain), and compute a shader-side
    fallback with the SAME formula everywhere else (scattered assets, plain meshes carry
    no pass). Use Attribute picks between them (0 computed, the default, since most
    surfaces have no pass; 1 attribute, for the terrain).

    The fallback MUST match the GN pass (snow.py), pinned in SYSTEMS.md:
      slope_mask = smoothstep(normalZ, from Slope Threshold - Slope Falloff to Slope
                   Threshold)   -- eases on the LOW side, snow holds on up-facing ground
      altitude_mask = smoothstep(worldZ, from Altitude to Altitude + Altitude Falloff)
                   -- eases on the HIGH side, snow holds on high ground
      coverage = Snow * slope_mask * altitude_mask
    Occlusion is a GN-only raycast term (default 0, and fallback meshes have no overhangs),
    so omitting it here is exact when occlusion is off, which is the fallback's whole domain.
    The attribute path's snow_cover already includes its own Snow multiply, so both paths
    scale with env.snow identically.
    """
    g, _fresh = _cached_group(WEATHER)
    if not _fresh:
        return g
    _gin(g, "Base Color", "NodeSocketColor", (0.5, 0.5, 0.5, 1.0))
    _gin(g, "Roughness", "NodeSocketFloat", 0.5, 0.0, 1.0)
    _gin(g, "Metallic", "NodeSocketFloat", 0.0, 0.0, 1.0)
    _gin(g, "Snow Strength", "NodeSocketFloat", 1.0, 0.0, 1.0)
    _gin(g, "Use Attribute", "NodeSocketFloat", 0.0, 0.0, 1.0)
    _gin(g, "Slope Threshold", "NodeSocketFloat", 0.5, 0.0, 1.0)
    _gin(g, "Slope Falloff", "NodeSocketFloat", 0.2, 0.0, 1.0)
    _gin(g, "Altitude", "NodeSocketFloat", 0.0)
    _gin(g, "Altitude Falloff", "NodeSocketFloat", 5.0, 0.0)
    # S4 weather terms, each gated by a strength/amount (0 = off).
    _gin(g, "Wetness Strength", "NodeSocketFloat", 1.0, 0.0, 1.0)
    _gin(g, "Wet Pooling", "NodeSocketFloat", 0.0, 0.0, 1.0)
    # Terrain-driven wetness (a baked flow/wetness map, fed only by the terrain master; 0 for
    # surfaces and legacy terrains). Lets channels and low ground read damp independent of the
    # weather, in EEVEE too (the map is baked, unlike the Cycles-only Wet Pooling cavity term).
    _gin(g, "Wetness Map", "NodeSocketFloat", 0.0, 0.0, 1.0)
    _gin(g, "Terrain Wetness", "NodeSocketFloat", 0.0, 0.0, 1.0)
    _gin(g, "Frost Strength", "NodeSocketFloat", 1.0, 0.0, 1.0)
    _gin(g, "Dust Amount", "NodeSocketFloat", 0.0, 0.0, 1.0)
    _gin(g, "Moss Amount", "NodeSocketFloat", 0.0, 0.0, 1.0)
    _gout(g, "Base Color", "NodeSocketColor")
    _gout(g, "Roughness", "NodeSocketFloat")
    _gout(g, "Metallic", "NodeSocketFloat")

    gi = g.nodes.new("NodeGroupInput")
    gi.location = (-1200, 200)
    go = g.nodes.new("NodeGroupOutput")
    go.location = (2100, 0)
    I, O = gi.outputs, go.inputs

    env = g.nodes.new("ShaderNodeGroup")
    env.node_tree = env_state_group()
    env.location = (-1200, -520)
    snow_amount = env.outputs["Snow"]
    env_wet = env.outputs["Wetness"]
    env_temp = env.outputs["Temperature"]

    # Shader Geometry: world-space normal Z (slope) and position Z (altitude), the same
    # quantities the GN pass reads, so the fallback reproduces it.
    geo = g.nodes.new("ShaderNodeNewGeometry")
    geo.location = (-1200, -120)
    nsep = g.nodes.new("ShaderNodeSeparateXYZ")
    nsep.location = (-1000, -80)
    g.links.new(geo.outputs["Normal"], nsep.inputs[0])
    psep = g.nodes.new("ShaderNodeSeparateXYZ")
    psep.location = (-1000, -280)
    g.links.new(geo.outputs["Position"], psep.inputs[0])

    slope_lo = _mmath(g, "SUBTRACT", I["Slope Threshold"], I["Slope Falloff"], (-800, -60))
    slope_mask = _mrange(g, nsep.outputs["Z"], slope_lo, I["Slope Threshold"], 0.0, 1.0, (-620, -20))
    alt_hi = _mmath(g, "ADD", I["Altitude"], I["Altitude Falloff"], (-800, -320))
    alt_mask = _mrange(g, psep.outputs["Z"], I["Altitude"], alt_hi, 0.0, 1.0, (-620, -280))
    computed = _mmath(g, "MULTIPLY", snow_amount, slope_mask, (-420, -120))
    computed = _mmath(g, "MULTIPLY", computed, alt_mask, (-240, -120))

    attr = g.nodes.new("ShaderNodeAttribute")
    attr.attribute_type = "GEOMETRY"
    attr.attribute_name = "snow_cover"
    attr.location = (-420, -400)

    # coverage = computed*(1 - Use Attribute) + snow_cover*Use Attribute. Done with math,
    # not a Mix node, so the float sockets are unambiguous.
    inv_use = _mmath(g, "SUBTRACT", 1.0, I["Use Attribute"], (-240, -320))
    c_comp = _mmath(g, "MULTIPLY", computed, inv_use, (-60, -220))
    c_attr = _mmath(g, "MULTIPLY", attr.outputs["Fac"], I["Use Attribute"], (-60, -400))
    coverage = _mmath(g, "ADD", c_comp, c_attr, (120, -300))

    snow_factor = g.nodes.new("ShaderNodeMath")
    snow_factor.operation = "MULTIPLY"
    snow_factor.use_clamp = True  # coverage * strength, clamped to 0..1
    snow_factor.location = (300, -220)
    g.links.new(coverage, snow_factor.inputs[0])
    g.links.new(I["Snow Strength"], snow_factor.inputs[1])
    sf = snow_factor.outputs["Value"]

    # Up/down-facing and cavity terms, from the shader Geometry: dust/frost hold on up-facing
    # faces, moss on shaded down-facing ones, and wetness pools in concave cavities.
    upface = _mrange(g, nsep.outputs["Z"], 0.2, 0.8, 0.0, 1.0, (300, 260))
    downface = _mmath(g, "SUBTRACT", 1.0, upface, (480, 300))
    cavity = _mrange(g, geo.outputs["Pointiness"], 0.5, 0.35, 0.0, 1.0, (300, 420))

    # The weather stack, applied in order on (albedo, roughness, metallic):
    #   dust/moss (aging) -> wetness (darken + gloss) -> snow (whiten) -> frost (cool sheen).
    col, rough, metal = I["Base Color"], I["Roughness"], I["Metallic"]

    # Dust on up-facing, moss on down-facing (continuous amounts, set by season on Apply).
    dust = _mmath(g, "MULTIPLY", I["Dust Amount"], upface, (660, 320))
    col = _mixcol(g, dust, col, _DUST_COLOR, (840, 360))
    moss = _mmath(g, "MULTIPLY", I["Moss Amount"], downface, (660, 200))
    col = _mixcol(g, moss, col, _MOSS_COLOR, (1000, 300))

    # Wetness: darken albedo and drop roughness for a wet sheen. The wet factor is the MAX of
    # three sources: uniform weather wetness, Cycles cavity pooling (Wet Pooling), and the baked
    # terrain map (Wetness Map * Terrain Wetness) so drainage channels read damp on their own.
    wet = _mmath(g, "MULTIPLY", env_wet, I["Wetness Strength"], (660, 60))
    pool = _mmath(g, "MULTIPLY", cavity, I["Wet Pooling"], (660, -60))
    terr = _mmath(g, "MULTIPLY", I["Wetness Map"], I["Terrain Wetness"], (660, -160))
    wp = _mmath(g, "MAXIMUM", wet, pool, (840, 0))
    wetf = g.nodes.new("ShaderNodeMath")
    wetf.operation = "MAXIMUM"
    wetf.use_clamp = True
    wetf.location = (1010, 0)
    g.links.new(wp, wetf.inputs[0])
    g.links.new(terr, wetf.inputs[1])
    wf = wetf.outputs["Value"]
    darkf = _mmath(g, "SUBTRACT", 1.0, _mmath(g, "MULTIPLY", wf, 0.45, (1000, -80)), (1180, -40))
    col = _vscale(g, col, darkf, (1180, 220))
    rough = _lerp(g, rough, 0.08, wf, (1180, -220))

    # Snow on top: whiten albedo, soften roughness, drop metallic by the coverage factor.
    col = _mixcol(g, sf, col, _SNOW_ALBEDO, (1360, 240))
    rough = _lerp(g, rough, _SNOW_ROUGHNESS, sf, (1360, -220))
    metal = _mmath(g, "MULTIPLY", metal, _mmath(g, "SUBTRACT", 1.0, sf, (1360, -360)), (1540, -320))

    # Frost: below freezing, on up-facing exposed faces, a cool blue-white sheen.
    cold = _mrange(g, env_temp, 0.0, -6.0, 0.0, 1.0, (1360, 460))
    frost = _mmath(g, "MULTIPLY", _mmath(g, "MULTIPLY", cold, I["Frost Strength"], (1540, 460)),
                   upface, (1720, 460))
    col = _mixcol(g, _mmath(g, "MULTIPLY", frost, 0.6, (1720, 300)), col, _FROST_COLOR, (1900, 340))
    rough = _lerp(g, rough, 0.25, _mmath(g, "MULTIPLY", frost, 0.5, (1720, -160)), (1900, -160))

    g.links.new(col, O["Base Color"])
    g.links.new(rough, O["Roughness"])
    g.links.new(metal, O["Metallic"])
    return g


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
    _gin(g, "Use Attribute", "NodeSocketFloat", 0.0, 0.0, 1.0)
    _gin(g, "Slope Threshold", "NodeSocketFloat", 0.5, 0.0, 1.0)
    _gin(g, "Slope Falloff", "NodeSocketFloat", 0.2, 0.0, 1.0)
    _gin(g, "Altitude", "NodeSocketFloat", 0.0)
    _gin(g, "Altitude Falloff", "NodeSocketFloat", 5.0, 0.0)
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
    for name in ("Snow Strength", "Use Attribute", "Slope Threshold", "Slope Falloff",
                 "Altitude", "Altitude Falloff", *[n for n, _ in _WEATHER_EXTRA]):
        g.links.new(I[name], weather.inputs[name])
    for name in ("Base Color", "Roughness", "Metallic"):
        g.links.new(weather.outputs[name], go.inputs[name])
    return g


# Principled inputs a master group may drive BEYOND Base Color / Roughness / Metallic (the water
# master, C5.3). Wired only when the master exposes the matching OUTPUT, so surface and terrain
# masters (which do not) are byte-identical. The Principled transmission socket was renamed across
# Blender versions (Transmission -> Transmission Weight in 4.x), so each master output maps to a
# list of candidate BSDF socket names and the first that exists wins.
_WRAPPER_EXTRA_OUTPUTS = (
    ("Transmission", ("Transmission Weight", "Transmission")),
    ("IOR", ("IOR",)),
    ("Alpha", ("Alpha",)),
    ("Normal", ("Normal",)),
)


def _build_wrapper(mat_name, master, sig, wire):
    """Build (or rebuild) a thin wrapper material: one master group node ("Master") feeding
    one Principled BSDF and the Output. `sig` is a structure signature stored on the material;
    if it is unchanged and the Master is wired, the wrapper is returned untouched so tuned
    inputs survive. On a structural change (a texture set assigned or changed) it rebuilds,
    snapshotting and restoring the Master's tuned inputs by socket name (the shader analogue
    of the GN modifier snapshot). `wire(nt, grp, bsdf, old_sig)` adds any texture-set nodes."""
    name = mat_name if mat_name.startswith(SURFACE_WRAPPER_PREFIX) else SURFACE_WRAPPER_PREFIX + mat_name
    mat = bpy.data.materials.get(name)
    old_node = None
    if mat is not None and mat.use_nodes and mat.node_tree is not None:
        old_node = mat.node_tree.nodes.get("Master")
        if old_node is not None and old_node.type == "GROUP" \
                and old_node.node_tree is master and mat.get("bbt_sig") == sig:
            return mat  # unchanged; keep tuned inputs
    old_sig = mat.get("bbt_sig") if mat is not None else None
    snap = _snapshot_group_inputs(old_node)
    if mat is None:
        mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    nt = mat.node_tree
    nt.nodes.clear()
    out = nt.nodes.new("ShaderNodeOutputMaterial")
    out.location = (600, 0)
    bsdf = nt.nodes.new("ShaderNodeBsdfPrincipled")
    bsdf.location = (300, 0)
    grp = nt.nodes.new("ShaderNodeGroup")
    grp.name = "Master"
    grp.node_tree = master
    grp.location = (-150, 0)
    nt.links.new(grp.outputs["Base Color"], bsdf.inputs["Base Color"])
    nt.links.new(grp.outputs["Roughness"], bsdf.inputs["Roughness"])
    nt.links.new(grp.outputs["Metallic"], bsdf.inputs["Metallic"])
    # Water master (C5.3): also drive Transmission / IOR / Alpha / Normal when the master exposes
    # them. A no-op for surface / terrain masters (their groups carry no such outputs).
    for out_name, candidates in _WRAPPER_EXTRA_OUTPUTS:
        src = grp.outputs.get(out_name)
        if src is None:
            continue
        target = next((bsdf.inputs.get(c) for c in candidates if bsdf.inputs.get(c) is not None), None)
        if target is not None:
            nt.links.new(src, target)
    nt.links.new(bsdf.outputs["BSDF"], out.inputs["Surface"])
    _restore_group_inputs(grp, snap)
    if wire is not None:
        wire(nt, grp, bsdf, old_sig)
    mat["bbt_sig"] = sig
    return mat


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
    grp.inputs["Use Attribute"].default_value = 0.0  # computed coverage (no pass on assets)
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


def master_type(mat):
    """The BobShader master kind of a material: 'surface', 'terrain', 'water', or None when it is
    not a BobShader. A BobShader is any material whose node tree carries a "Master" group node whose
    tree is S_SurfaceMaster, S_TerrainMaster, or S_WaterMaster (the identity the redesign keys off,
    replacing the old stored material_name). Covers wrapper materials (surface_material /
    terrain_material / water_material) and converted asset materials (bobshade_material), which all
    add that Master node."""
    if mat is None or not mat.use_nodes or mat.node_tree is None:
        return None
    node = mat.node_tree.nodes.get("Master")
    if node is None or node.type != "GROUP" or node.node_tree is None:
        return None
    if node.node_tree.name == SURFACE_MASTER:
        return "surface"
    if node.node_tree.name == TERRAIN_MASTER:
        return "terrain"
    if node.node_tree.name == WATER_MASTER:
        return "water"
    return None


def is_bobshader(mat):
    """True when the material is a BobShader (has a surface or terrain Master group node)."""
    return master_type(mat) is not None


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


def _terrain_maps(obj):
    """(flow_image, wetness_image, size) for a built terrain. Loads the baked drainage maps
    (siblings of the heightmap: <base>_flow.png / <base>_wetness.png) when their files exist.
    The heightmap path is the object's bbt_heightmap prop (set by the bake), else the image
    datablock on the terrain's GN modifier; size is the bbt_terrain_size prop (fallback 90)."""
    hm = obj.get("bbt_heightmap")
    if not hm:
        for mod in obj.modifiers:
            ng = getattr(mod, "node_group", None)
            if ng is None:
                continue
            for node in ng.nodes:
                if node.bl_idname == "GeometryNodeImageTexture":
                    img = node.inputs["Image"].default_value
                    if img is not None and img.filepath:
                        hm = img.filepath
                        break
            if hm:
                break
    size = float(obj.get("bbt_terrain_size", 90.0))
    flow = wet = None
    if hm:
        base, ext = os.path.splitext(bpy.path.abspath(hm))
        ext = ext or ".png"
        fp, wp = base + "_flow" + ext, base + "_wetness" + ext
        if os.path.exists(fp):
            flow = bpy.data.images.load(fp, check_existing=True)
        if os.path.exists(wp):
            wet = bpy.data.images.load(wp, check_existing=True)
    return flow, wet, size


def terrain_material_for(obj, layer_sets=None, mat_name=None):
    """terrain_material for a built terrain OBJECT: gathers its baked flow/wetness maps and size
    so a rebuild (assigning a texture set, adding a layer) keeps the drainage wiring instead of
    dropping it. mat_name preserves the existing material's identity when rebuilding in place."""
    flow, wet, size = _terrain_maps(obj)
    return terrain_material(mat_name or obj.name, layer_sets=layer_sets,
                            flow_image=flow, wetness_image=wet, terrain_size=size)


def _autoconfig_riverbed(mat):
    """On a fresh terrain BobShader with drainage maps, wire a sensible riverbed look: enable a
    wet-gravel layer keyed to high flow, and a baseline terrain wetness in the channels. Editable
    and disablable afterward."""
    grp = mat.node_tree.nodes.get("Master")
    if grp is None:
        return
    def setv(name, val):
        sock = grp.inputs.get(name)
        if sock is not None:
            sock.default_value = val
    setv("L1 Enable", 1.0)
    setv("L1 Base Color", (0.20, 0.17, 0.14, 1.0))  # damp gravel / silt
    setv("L1 Roughness", 0.7)
    setv("L1 Flow Strength", 1.0)
    setv("L1 Flow Threshold", 0.55)
    setv("Terrain Wetness", 0.6)


# Masks other than the curve band, cleared on the curve-surface layer so it keys ONLY off the
# curve (a road/dirt surface follows the path, not a slope or altitude).
_CURVE_OTHER_MASKS = ("Slope Strength", "Height Strength", "Noise Strength",
                      "Paint Strength", "Curvature Strength", "Flow Strength")


def apply_curve_surface(mat, base_color, roughness=0.85, height_bias=0.3, hard_edge=0.0,
                        channel="a"):
    """Configure a terrain BobShader layer as a curve surface band (BobSplines C3/R5): a layer
    keyed to a curve overlay mask, so a road/dirt surface reads only along a path. Mirrors
    _autoconfig_riverbed but for the curve mask. Idempotent: reuses the slot already keyed to THIS
    channel on a re-apply, else the highest free (disabled) slot, else the top slot.

    channel selects which curve mask keys the layer (R5): "a" -> bbt_curve_mask (the shared band,
    dirt/trail), "b" -> bbt_curve_mask_b (a distinct class, e.g. a paved road). Two roles on
    different channels therefore key two DIFFERENT layers and read as different surfaces; the slot
    keys off exactly one channel (the other is cleared).

    height_bias is kept modest on purpose (docs/SPLINES.md 9 #7): with a SOFT edge the layer's
    height field is weight (= the curve mask here) + Height Bias + macro, so off the curve weight
    -> 0 and H -> Height Bias; a small bias wins the height-lerp ON the curve (mask 1 -> H ~ 1 +
    bias) but loses to a full base layer (H ~ 1) OFF it, so the surface does not bleed past the path.

    hard_edge (0..1, BobSplines R2) mixes in a crisp edge: at 1 the layer's H is gated straight off
    the curve mask (a step at the band boundary) so the surface edges sharply regardless of Blend
    Softness -- a road wants ~1, a worn dirt path wants 0 (the soft feathered edge above).

    Returns the configured slot index, or None when mat is not a terrain BobShader (or is an older
    master group without this curve channel -- rebuild the material to get it).
    """
    grp = mat.node_tree.nodes.get("Master") if mat and mat.use_nodes and mat.node_tree else None
    if grp is None or master_type(mat) != "terrain":
        return None
    strength_key = "Curve Strength" if channel == "a" else "Curve B Strength"
    hard_key = "Curve Hard" if channel == "a" else "Curve B Hard"
    other_strength = "Curve B Strength" if channel == "a" else "Curve Strength"
    if grp.inputs.get("L0 " + strength_key) is None:
        return None  # an older master group without this curve channel; rebuild the material

    slot, free = None, None
    for i in range(MAX_TERRAIN_LAYERS):
        cs = grp.inputs.get(f"L{i} {strength_key}")
        en = grp.inputs.get(f"L{i} Enable")
        if cs is not None and cs.default_value > 0.0:
            slot = i  # reuse the slot already keyed to this channel
            break
        if free is None and i > 0 and en is not None and en.default_value == 0.0:
            free = i
    if slot is None:
        slot = free if free is not None else MAX_TERRAIN_LAYERS - 1
    p = f"L{slot} "

    def setv(name, val):
        sock = grp.inputs.get(p + name)
        if sock is not None:
            sock.default_value = val

    setv("Enable", 1.0)
    setv(strength_key, 1.0)
    setv(hard_key, float(hard_edge))
    setv(other_strength, 0.0)  # this slot keys off exactly one curve channel
    setv("Base Color", base_color)
    setv("Roughness", roughness)
    setv("Height Bias", height_bias)
    for m in _CURVE_OTHER_MASKS:
        setv(m, 0.0)  # a reused slot might carry another mask; the curve band keys off the curve alone
    return slot


def apply_curve_wet(mat, wetness=0.6):
    """Make a terrain BobShader read damp along a river's channel (BobSplines C5.4). The curve
    overlay writes bbt_curve_wet into the terrain's Wetness Map (MAX-accumulated inside
    terrain_master_group); this raises Terrain Wetness -- the multiplier that wetness path is gated
    by -- so the bed and banks read wet and glossy, weather-amplified. Idempotent and non-lowering:
    it never drops an existing higher Terrain Wetness (a riverbed autoconfig or a wetter role wins),
    so re-Build is safe. Returns True when applied, or None when mat is not a terrain BobShader (or
    an older master group without the wet path -- rebuild the material)."""
    grp = mat.node_tree.nodes.get("Master") if mat and mat.use_nodes and mat.node_tree else None
    if grp is None or master_type(mat) != "terrain":
        return None
    sock = grp.inputs.get("Terrain Wetness")
    if sock is None:
        return None
    sock.default_value = max(sock.default_value, float(wetness))
    return True


def surface_material(mat_name, texture_set=None):
    """A single-surface wrapper (S_SurfaceMaster), optionally with a texture set assigned.

    Solid colour by default; when a set is given the wrapper links its triplanar maps into the
    master's Albedo/Roughness/Metallic map inputs (colour still tints: albedo = Base Color *
    map) and the bump normal into the Principled. On the transition solid->textured the tint
    defaults to white and Roughness to 1 so the map reads at face value; a retexture keeps the
    tuned values."""
    master = surface_master_group()
    sig = "surface|" + (texture_set or "")

    def wire(nt, grp, bsdf, old_sig):
        if not texture_set:
            return
        ts = nt.nodes.new("ShaderNodeGroup")
        ts.name = "TexSet"
        ts.node_tree = texture_set_group(texture_set)
        ts.location = (-500, -260)
        nt.links.new(ts.outputs["Base Color"], grp.inputs["Albedo Map"])
        nt.links.new(ts.outputs["Roughness"], grp.inputs["Roughness Map"])
        nt.links.new(ts.outputs["Metallic"], grp.inputs["Metallic Map"])
        nt.links.new(ts.outputs["Normal"], bsdf.inputs["Normal"])
        if old_sig is None or old_sig.endswith("|"):  # was solid -> show the map at face value
            grp.inputs["Base Color"].default_value = (1.0, 1.0, 1.0, 1.0)
            grp.inputs["Roughness"].default_value = 1.0
            grp.inputs["Metallic"].default_value = 0.0

    return _build_wrapper(mat_name, master, sig, wire)


# BobShaders water master (S_WaterMaster, BobSplines C5.3). The third BobShader kind, for the river
# ribbons curve_water lays in a carved channel. It reads the ribbon's baked bbt_flow / bbt_foam /
# bbt_shore attributes and produces a flowing, depth-tinted, foaming, transparent surface that
# freezes to ice below 0 C. Like the other masters it ends in S_Weather, so it inherits the shared
# wetness/frost/snow layer and the live env feed; the freeze reuses that below-freezing frost path
# rather than adding a new system. Beyond Base Color/Roughness/Metallic it also outputs Transmission,
# IOR, Normal, and Alpha, which the widened _build_wrapper drives into the Principled BSDF.
WATER_MASTER = "S_WaterMaster"
_WATER_SHALLOW = (0.16, 0.34, 0.36, 1.0)  # near-shore tint
_WATER_DEEP = (0.02, 0.09, 0.13, 1.0)     # deep body
_WATER_FOAM = (0.92, 0.95, 0.97, 1.0)
_WATER_ICE = (0.66, 0.77, 0.83, 1.0)      # pale blue-white frozen tint
_ICE_ROUGHNESS = 0.32                     # frosted-glassy ice (vs the near-mirror liquid roughness)


def set_water_render_flags(mat):
    """Make a water material actually read as water in EEVEE-Next (W1): raytraced refraction so the
    0.92 Transmission bends what is behind the surface instead of reading flat grey. These are 5.2
    material datablock flags (probed live, not guessed): use_raytrace_refraction is the EEVEE-Next
    name (use_screen_refraction is the retained 4.x alias, set too for safety); show_transparent_back
    off drops the doubled back face; DITHERED keeps the surface refraction-capable (BLENDED alpha
    cannot refract). No-op / harmless in Cycles, which refracts from the Transmission weight alone.
    The scene's eevee.use_raytracing must also be on -- see enable_eevee_refraction, called from the
    addon build path (a scene setting, out of a material builder's remit)."""
    if mat is None:
        return
    for flag in ("use_raytrace_refraction", "use_screen_refraction"):
        if hasattr(mat, flag):
            setattr(mat, flag, True)
    if hasattr(mat, "show_transparent_back"):
        mat.show_transparent_back = False
    if hasattr(mat, "use_transparent_shadow"):
        mat.use_transparent_shadow = True
    if hasattr(mat, "surface_render_method"):
        mat.surface_render_method = "DITHERED"  # DITHERED refracts; BLENDED (alpha) does not


def enable_eevee_refraction(scene):
    """Turn on the scene-level EEVEE-Next ray tracing that raytraced refraction needs to show (W1).
    A no-op unless the active engine is EEVEE, and it only ever switches the toggle ON (never off, so
    it will not fight a user who wants it off). Cycles needs nothing here. Kept out of the material
    builder because it mutates the scene; the addon calls it when it builds/assigns water."""
    if scene is None:
        return
    eng = getattr(getattr(scene, "render", None), "engine", "")
    ee = getattr(scene, "eevee", None)
    if ee is not None and eng.startswith("BLENDER_EEVEE") and hasattr(ee, "use_raytracing"):
        ee.use_raytracing = True


def water_master_group():
    """The water-surface master (see the section comment). The surface animates live off a
    frame-driven Value node, no bake:
      - Waves (W2): three flow-advected noise octaves (a slow swell, the main ripple, a fine chop)
        chained into one normal. Each octave is sampled at TWO phases half a cycle apart and blended
        by a triangle wave, the flow-map trick, so the advection resets without a visible pop even
        where bbt_flow diverges (banks vs mid-channel, rapids). All three fade out as it freezes.
      - Foam (W3): bbt_foam (banks + rapids) is lifted by a shore term (shallow water near the banks,
        from bbt_shore), broken up by a flow-scrolled noise, and thresholded to crisp lines whose
        sharpness is Foam Crispness -- not a flat white wash. Foam also roughens the surface.
      - Freeze (W4): a manual Frozen input OR the env below-0 term freezes the water: the flow normal
        collapses to glassy-flat, cracked-ice detail fades in (a Voronoi distance-to-edge bump),
        transmission drops to opaque, and (manual path) the albedo tints icy blue-white. The shared
        S_Weather frost term adds the winter sheen on the env-cold path.
    Beyond Base Color / Roughness / Metallic it outputs Transmission / IOR / Alpha / Normal, which
    the widened _build_wrapper drives into the Principled BSDF."""
    g, _fresh = _cached_group(WATER_MASTER)
    if not _fresh:
        return g
    _gin(g, "Shallow Color", "NodeSocketColor", _WATER_SHALLOW)
    _gin(g, "Deep Color", "NodeSocketColor", _WATER_DEEP)
    _gin(g, "Depth", "NodeSocketFloat", 1.5, 0.0)
    _gin(g, "Water Roughness", "NodeSocketFloat", 0.04, 0.0, 1.0)
    _gin(g, "IOR", "NodeSocketFloat", 1.33, 1.0, 2.0)
    _gin(g, "Transmission", "NodeSocketFloat", 0.92, 0.0, 1.0)
    _gin(g, "Flow Speed", "NodeSocketFloat", 0.08, 0.0)
    # Ripple Strength is the SHADER micro-detail normal only (fine surface texture); the visible
    # waves are geometry Gerstner in curve_water. Kept low so it never combs into streaks.
    _gin(g, "Ripple Strength", "NodeSocketFloat", 0.10, 0.0, 2.0)
    _gin(g, "Ripple Scale", "NodeSocketFloat", 1.8, 0.0)
    _gin(g, "Wave Detail", "NodeSocketFloat", 0.5, 0.0, 1.0)
    _gin(g, "Foam Color", "NodeSocketColor", _WATER_FOAM)
    _gin(g, "Foam Amount", "NodeSocketFloat", 1.2, 0.0, 2.0)
    _gin(g, "Shore Foam", "NodeSocketFloat", 0.6, 0.0, 1.0)
    _gin(g, "Foam Crispness", "NodeSocketFloat", 0.5, 0.0, 1.0)
    _gin(g, "Edge Fade", "NodeSocketFloat", 0.0, 0.0, 1.0)
    _gin(g, "Frozen", "NodeSocketFloat", 0.0, 0.0, 1.0)
    # Weather passthrough (as surface_master), so the shared S_Weather layer works. Snow defaults
    # OFF for water: flowing water sheds snow, and the winter look comes from the frost/freeze path.
    _gin(g, "Snow Strength", "NodeSocketFloat", 0.0, 0.0, 1.0)
    _gin(g, "Use Attribute", "NodeSocketFloat", 0.0, 0.0, 1.0)
    _gin(g, "Slope Threshold", "NodeSocketFloat", 0.5, 0.0, 1.0)
    _gin(g, "Slope Falloff", "NodeSocketFloat", 0.2, 0.0, 1.0)
    _gin(g, "Altitude", "NodeSocketFloat", 0.0)
    _gin(g, "Altitude Falloff", "NodeSocketFloat", 5.0, 0.0)
    for _wn, _wd in _WEATHER_EXTRA:
        _gin(g, _wn, "NodeSocketFloat", _wd, 0.0, 1.0)
    _gout(g, "Base Color", "NodeSocketColor")
    _gout(g, "Roughness", "NodeSocketFloat")
    _gout(g, "Metallic", "NodeSocketFloat")
    _gout(g, "Transmission", "NodeSocketFloat")
    _gout(g, "IOR", "NodeSocketFloat")
    _gout(g, "Alpha", "NodeSocketFloat")
    _gout(g, "Normal", "NodeSocketVector")

    gi = g.nodes.new("NodeGroupInput")
    gi.location = (-1900, 0)
    go = g.nodes.new("NodeGroupOutput")
    go.location = (1500, 0)
    I, O = gi.outputs, go.inputs

    # Live env: Temperature drives the freeze term (shared group, driven from bbt_env by the panel;
    # 15 C default when Firmament is absent, so env cold -> 0 and the water never freezes standalone
    # -- which is why the manual Frozen input exists). frozen = max(manual, env cold); liquid = 1 - it.
    env = g.nodes.new("ShaderNodeGroup")
    env.node_tree = env_state_group()
    env.location = (-1900, -900)
    cold = _mrange(g, env.outputs["Temperature"], 0.0, -6.0, 0.0, 1.0, (-1700, -900))  # 0 warm, 1 icy
    frozen = _mmath(g, "MAXIMUM", cold, I["Frozen"], (-1520, -900))
    liquid = _mmath(g, "SUBTRACT", 1.0, frozen, (-1340, -900))

    # Ribbon attributes (curve_water stores these on the water mesh).
    def _geo_attr(name, y):
        n = g.nodes.new("ShaderNodeAttribute")
        n.attribute_type = "GEOMETRY"
        n.attribute_name = name
        n.location = (-1900, y)
        return n
    flow = _geo_attr("bbt_flow", 380)
    foam_a = _geo_attr("bbt_foam", 240)
    shore_a = _geo_attr("bbt_shore", 100)

    # Frame-driven time (mirrors _install_env_drivers, but `frame` is a built-in driver variable so
    # no scene target is needed): waves scroll without a bake. Installed once on the fresh group.
    tval = g.nodes.new("ShaderNodeValue")
    tval.name = tval.label = "water_time"
    tval.location = (-1900, -260)
    try:
        fc = tval.outputs[0].driver_add("default_value")
        fc = fc[0] if isinstance(fc, list) else fc
        fc.driver.type = "SCRIPTED"
        fc.driver.expression = "frame"
    except (RuntimeError, TypeError):
        pass  # no anim context (headless build): waves hold at frame 0, the surface still renders
    time = _mmath(g, "MULTIPLY", tval.outputs[0], I["Flow Speed"], (-1700, -260))
    geo = g.nodes.new("ShaderNodeNewGeometry")
    geo.location = (-1900, 560)
    pos = geo.outputs["Position"]

    def _vsub(a, b, loc):
        n = g.nodes.new("ShaderNodeVectorMath")
        n.operation = "SUBTRACT"
        n.location = loc
        g.links.new(a, n.inputs[0])
        g.links.new(b, n.inputs[1])
        return n.outputs["Vector"]

    def _noise(vec, scale, w, detail, loc):
        n = g.nodes.new("ShaderNodeTexNoise")
        n.noise_dimensions = "4D"
        n.location = loc
        n.inputs["Detail"].default_value = detail
        n.inputs["Roughness"].default_value = 0.5
        g.links.new(vec, n.inputs["Vector"])
        _mplug(g, n.inputs["Scale"], scale)
        _mplug(g, n.inputs["W"], w)
        return n.outputs["Fac"]

    def _flow_octave(scale, scroll, wspeed, detail, y):
        """One flow-advected noise height, sampled at two phases 0.5 apart and cross-faded by a
        triangle wave (weight 1 at a reset seam, 0 mid-cycle) so the scroll never pops. `scroll`
        is the advection distance in metres per cycle, `scale` the noise frequency."""
        p0 = _mmath(g, "FRACT", time, None, (-1500, y))
        p1 = _mmath(g, "FRACT", _mmath(g, "ADD", time, 0.5, (-1500, y - 130)), None, (-1330, y - 130))
        d0 = _mmath(g, "MULTIPLY", p0, scroll, (-1330, y))
        d1 = _mmath(g, "MULTIPLY", p1, scroll, (-1330, y - 260))
        s0 = _noise(_vsub(pos, _vscale(g, flow.outputs["Vector"], d0, (-1150, y)), (-980, y)),
                    scale, _mmath(g, "MULTIPLY", time, wspeed, (-1150, y - 400)), detail, (-800, y))
        s1 = _noise(_vsub(pos, _vscale(g, flow.outputs["Vector"], d1, (-1150, y - 260)),
                          (-980, y - 260)),
                    scale, _mmath(g, "MULTIPLY", _mmath(g, "ADD", time, 0.5, (-1330, y - 520)),
                                  wspeed, (-1150, y - 520)), detail, (-800, y - 260))
        w = _mmath(g, "ABSOLUTE",
                   _mmath(g, "SUBTRACT", _mmath(g, "MULTIPLY", p0, 2.0, (-800, y - 400)), 1.0,
                          (-620, y - 400)), None, (-440, y - 400))
        return _lerp(g, s0, s1, w, (-440, y - 160))

    def _wave_bump(height, strength, normal_in, loc):
        b = g.nodes.new("ShaderNodeBump")
        b.location = loc
        _mplug(g, b.inputs["Strength"], strength)
        g.links.new(height, b.inputs["Height"])
        if normal_in is not None:
            g.links.new(normal_in, b.inputs["Normal"])
        return b.outputs["Normal"]

    # Fine surface detail only. The VISIBLE waves are geometry Gerstner (curve_water); a
    # low-frequency bump here combed into hair-like streaks under a grazing view, so this is now two
    # HIGH-frequency, LOW-strength flow-scrolled octaves that read as micro-texture riding on the
    # geometry waves. Chained through the Bump Normal input; both fade out as it freezes.
    rs = _mmath(g, "MULTIPLY", I["Ripple Strength"], liquid, (-260, 620))
    fine_str = _mmath(g, "MULTIPLY", _mmath(g, "MULTIPLY", rs, 0.6, (-260, 140)),
                      I["Wave Detail"], (-80, 140))
    mid_h = _flow_octave(_mmath(g, "MULTIPLY", I["Ripple Scale"], 2.0, (-1700, 620)),
                         0.5, 0.35, 3.0, 620)
    fine_h = _flow_octave(_mmath(g, "MULTIPLY", I["Ripple Scale"], 5.0, (-1700, 140)),
                          0.3, 0.60, 4.0, 140)
    n_mid = _wave_bump(mid_h, rs, None, (140, 560))
    n_fine = _wave_bump(fine_h, fine_str, n_mid, (400, 320))

    # Cracked-ice normal: a Voronoi distance-to-edge bump that fades in with `frozen` and chains on
    # top of the (now flat) wave normal. Strength 0 when liquid, so it is a pass-through.
    voro = g.nodes.new("ShaderNodeTexVoronoi")
    voro.voronoi_dimensions = "3D"
    voro.feature = "DISTANCE_TO_EDGE"
    voro.location = (320, 40)
    voro.inputs["Scale"].default_value = 0.6  # world-scale cells, ~1.5 m ice plates
    g.links.new(pos, voro.inputs["Vector"])
    ice_str = _mmath(g, "MULTIPLY", frozen, 0.35, (500, 40))
    normal = _wave_bump(voro.outputs["Distance"], ice_str, n_fine, (680, 200))

    # Depth colour: deep mid-channel (shore 0), shallow near the banks (shore 1). deepness * Depth,
    # clamped, so a higher Depth pushes the deep colour further out toward the banks.
    deepness = _mmath(g, "SUBTRACT", 1.0, shore_a.outputs["Fac"], (-1000, -260))
    dt = g.nodes.new("ShaderNodeMath")
    dt.operation = "MULTIPLY"
    dt.use_clamp = True
    dt.location = (-820, -260)
    g.links.new(deepness, dt.inputs[0])
    g.links.new(I["Depth"], dt.inputs[1])
    col = _mixcol(g, dt.outputs["Value"], I["Shallow Color"], I["Deep Color"], (-620, -300))

    # Foam (W3). Base = max(bbt_foam*Foam Amount, shallow-shore foam). A flow-scrolled noise breaks
    # it up, then a Map Range thresholds it to crisp lines whose width narrows with Foam Crispness.
    foam_wash = _mmath(g, "MULTIPLY", foam_a.outputs["Fac"], I["Foam Amount"], (-1000, -480))
    shore_shallow = _mrange(g, shore_a.outputs["Fac"], 0.6, 1.0, 0.0, 1.0, (-1000, -640))
    shore_foam = _mmath(g, "MULTIPLY", shore_shallow, I["Shore Foam"], (-820, -640))
    foam_base = _mmath(g, "MAXIMUM", foam_wash, shore_foam, (-640, -540))
    foam_scroll = _vscale(g, flow.outputs["Vector"], _mmath(g, "MULTIPLY", time, 0.6, (-1000, -800)),
                          (-820, -800))
    foam_n = _noise(_vsub(pos, foam_scroll, (-640, -800)),
                    _mmath(g, "MULTIPLY", I["Ripple Scale"], 1.5, (-820, -940)),
                    _mmath(g, "MULTIPLY", time, 0.4, (-820, -1060)), 2.0, (-460, -800))
    # foam_base * (0.5 + noise): streaks where the noise is high, gaps where low.
    foam_mod = _mmath(g, "MULTIPLY", foam_base,
                      _mmath(g, "ADD", 0.5, foam_n, (-280, -800)), (-100, -560))
    half_w = _mmath(g, "MULTIPLY", _mmath(g, "SUBTRACT", 1.0, I["Foam Crispness"], (-280, -940)),
                    0.5, (-100, -940))
    foam_lo = _mmath(g, "SUBTRACT", 0.5, half_w, (80, -900))
    foam_hi = _mmath(g, "ADD", 0.55, half_w, (80, -1020))
    foam = _mmath(g, "MULTIPLY",
                  _mrange(g, foam_mod, foam_lo, foam_hi, 0.0, 1.0, (260, -740)), liquid, (440, -740))
    col = _mixcol(g, foam, col, I["Foam Color"], (620, -560))
    # Base roughness: near-mirror water, rougher where foam churns, frosted where frozen.
    rough = _lerp(g, I["Water Roughness"], 0.6, foam, (620, -820))
    rough = _lerp(g, rough, _ICE_ROUGHNESS, frozen, (620, -980))
    # Ice tint on the manual freeze path (the env-cold path is tinted by the S_Weather frost term,
    # so tinting there too would double up); icy blue-white as Frozen rises.
    col = _mixcol(g, _mmath(g, "MULTIPLY", I["Frozen"], 0.8, (620, -360)), col, _WATER_ICE, (800, -420))

    # Weather layer: inherit wetness/frost/snow. Its below-freezing frost term whitens + roughens
    # the albedo on the env-cold path; metallic stays 0.
    weather = g.nodes.new("ShaderNodeGroup")
    weather.node_tree = weather_group()
    weather.location = (980, -560)
    g.links.new(col, weather.inputs["Base Color"])
    g.links.new(rough, weather.inputs["Roughness"])
    weather.inputs["Metallic"].default_value = 0.0
    for name in ("Snow Strength", "Use Attribute", "Slope Threshold", "Slope Falloff",
                 "Altitude", "Altitude Falloff", *[n for n, _ in _WEATHER_EXTRA]):
        g.links.new(I[name], weather.inputs[name])

    # Transmission collapses to opaque as it freezes; IOR passes through; Alpha optionally fades the
    # ribbon toward the banks (Edge Fade, default off), but frozen ice is fully opaque.
    trans = _mmath(g, "MULTIPLY", I["Transmission"], liquid, (980, -1120))
    edge = _mmath(g, "SUBTRACT", 1.0,
                  _mmath(g, "MULTIPLY", shore_a.outputs["Fac"], I["Edge Fade"], (980, -1300)),
                  (1160, -1300))
    alpha = _lerp(g, edge, 1.0, frozen, (1160, -1180))

    g.links.new(weather.outputs["Base Color"], O["Base Color"])
    g.links.new(weather.outputs["Roughness"], O["Roughness"])
    g.links.new(weather.outputs["Metallic"], O["Metallic"])
    g.links.new(trans, O["Transmission"])
    g.links.new(I["IOR"], O["IOR"])
    g.links.new(alpha, O["Alpha"])
    g.links.new(normal, O["Normal"])
    return g


def water_material(mat_name):
    """A water-surface wrapper (S_WaterMaster) for a river/stream ribbon (BobSplines C5.3). The
    widened _build_wrapper drives the master's Transmission / IOR / Alpha / Normal into the
    Principled BSDF on top of the usual Base Color / Roughness / Metallic. get-or-create, so a
    re-Build keeps tuned inputs. The material's EEVEE refraction/transparency flags are set here so
    the Transmission actually refracts (W1); the scene-level ray tracing toggle is the addon's job."""
    mat = _build_wrapper(mat_name, water_master_group(), "water", None)
    set_water_render_flags(mat)
    return mat


# BobShaders terrain master (S2). S_TerrainMaster blends an ordered stack of surface layers
# across the ground by the SAME mask vocabulary Scatter uses (slope band, altitude band,
# noise clumping, paint), plus a Cycles Pointiness curvature term, then hands the blended
# base to S_Weather. Reusing the scatter masks is the deliberate glue: rock texture and rock
# scatter agree on scree slopes by construction (same thresholds, and the noise mask is the
# identical ShaderNodeTexNoise at world position the scatter recipe uses, so the clumping
# patterns coincide for a shared scale/seed).
#
# The blend is HEIGHT-AWARE (a height-lerp), not a linear cross-fade: each layer builds a
# height field H = weight + Height Bias + macro noise, and the layers composite by picking
# the higher H per texel within a soft Blend Softness band, so layers interlock (rock breaks
# through grass at a natural edge) instead of dissolving. This is the single feature that
# separates a strong terrain material from a weak one (Phase-0 confirmed the height-lerp).
#
# One shared group with a FIXED layer count (the master + instances model): the stack is the
# enabled slots, all knobs live on the wrapper node, so add/remove/tune never rebuild the
# graph. Layers beyond the stack sit disabled (Enable 0 -> never blended in).
TERRAIN_MASTER = "S_TerrainMaster"
MAX_TERRAIN_LAYERS = 6

# Default per-slot base colours, a sensible soil/grass/rock/cliff/scree/sand ramp so a fresh
# terrain reads as distinct layers before any preset. Presets and the panel override these.
_TERRAIN_LAYER_COLORS = (
    (0.16, 0.11, 0.07, 1.0),  # 0 soil
    (0.15, 0.26, 0.09, 1.0),  # 1 grass
    (0.34, 0.33, 0.31, 1.0),  # 2 rock
    (0.20, 0.19, 0.18, 1.0),  # 3 cliff
    (0.45, 0.40, 0.32, 1.0),  # 4 scree
    (0.68, 0.62, 0.48, 1.0),  # 5 sand
)
_SLOPE_SOFT = 0.05  # internal slope-band easing width (scatter's band is hard; a texture
#                     reads better with a soft edge, same thresholds)


def _gated(g, mask, strength, loc):
    """mix(1, mask, strength): strength 0 leaves the mask off (returns 1), 1 applies it
    fully. The exact gate scatter uses for its altitude/noise/paint masks."""
    d = _mmath(g, "SUBTRACT", mask, 1.0, loc)
    ds = _mmath(g, "MULTIPLY", d, strength, (loc[0], loc[1] - 150))
    return _mmath(g, "ADD", 1.0, ds, (loc[0] + 170, loc[1] - 70))


def _lerp(g, a, b, f, loc):
    """a + (b - a) * f."""
    d = _mmath(g, "SUBTRACT", b, a, loc)
    df = _mmath(g, "MULTIPLY", d, f, (loc[0], loc[1] - 150))
    return _mmath(g, "ADD", a, df, (loc[0] + 170, loc[1] - 70))


def _terrain_layer(g, I, i, pos, nz, wz, pointiness, x0):
    """One layer's (colour, roughness, metallic, height-field, enable). Weight is the product
    of the scatter masks, each gated by its strength (0 = off). The height field drives the
    height-lerp: where a layer's weight is high its H rises and it wins the per-texel pick."""
    p = f"L{i} "
    y = -i * 900

    # Slope band: soft version of scatter's [Min Normal Z, Max Normal Z] keep, same thresholds.
    s_lo = _mmath(g, "SUBTRACT", I[p + "Min Normal Z"], _SLOPE_SOFT, (x0, y))
    rising = _mrange(g, nz, s_lo, I[p + "Min Normal Z"], 0.0, 1.0, (x0 + 170, y))
    s_hi = _mmath(g, "ADD", I[p + "Max Normal Z"], _SLOPE_SOFT, (x0, y - 200))
    over = _mrange(g, nz, I[p + "Max Normal Z"], s_hi, 0.0, 1.0, (x0 + 170, y - 200))
    falling = _mmath(g, "SUBTRACT", 1.0, over, (x0 + 350, y - 200))
    slope_band = _mmath(g, "MULTIPLY", rising, falling, (x0 + 520, y - 100))
    slope = _gated(g, slope_band, I[p + "Slope Strength"], (x0 + 700, y))

    # Altitude band: reproduce scatter's _height_mask (rising * falling), gated by strength.
    a_lo = _mmath(g, "SUBTRACT", I[p + "Height Min"], I[p + "Height Falloff"], (x0, y - 400))
    a_rise = _mrange(g, wz, a_lo, I[p + "Height Min"], 0.0, 1.0, (x0 + 170, y - 400))
    a_hi = _mmath(g, "ADD", I[p + "Height Max"], I[p + "Height Falloff"], (x0, y - 560))
    a_over = _mrange(g, wz, I[p + "Height Max"], a_hi, 0.0, 1.0, (x0 + 170, y - 560))
    a_fall = _mmath(g, "SUBTRACT", 1.0, a_over, (x0 + 350, y - 560))
    alt_band = _mmath(g, "MULTIPLY", a_rise, a_fall, (x0 + 520, y - 480))
    alt = _gated(g, alt_band, I[p + "Height Strength"], (x0 + 700, y - 420))

    # Noise clumping: the identical ShaderNodeTexNoise the scatter recipe uses (4D, Detail
    # 2, Roughness 0.5, W = seed) at world position, so texture and scatter clump together.
    ntex = g.nodes.new("ShaderNodeTexNoise")
    ntex.noise_dimensions = "4D"
    ntex.location = (x0, y - 720)
    ntex.inputs["Detail"].default_value = 2.0
    ntex.inputs["Roughness"].default_value = 0.5
    g.links.new(pos, ntex.inputs["Vector"])
    g.links.new(I[p + "Noise Scale"], ntex.inputs["Scale"])
    g.links.new(I[p + "Noise Seed"], ntex.inputs["W"])
    inv_c = _mmath(g, "SUBTRACT", 1.0, I[p + "Noise Contrast"], (x0 + 170, y - 760))
    half = _mmath(g, "MULTIPLY", 0.5, inv_c, (x0 + 340, y - 760))
    n_lo = _mmath(g, "SUBTRACT", 0.5, half, (x0 + 510, y - 700))
    n_hi = _mmath(g, "ADD", 0.5, half, (x0 + 510, y - 820))
    patch = _mrange(g, ntex.outputs["Fac"], n_lo, n_hi, 0.0, 1.0, (x0 + 680, y - 720))
    noise = _gated(g, patch, I[p + "Noise Strength"], (x0 + 860, y - 700))

    # Paint: a per-layer named FLOAT attribute (bbt_paint_L{i}); paint that attribute on the
    # terrain to weight the layer. Absent attribute reads 0, and Paint Strength 0 = off.
    pa = g.nodes.new("ShaderNodeAttribute")
    pa.attribute_type = "GEOMETRY"
    pa.attribute_name = f"bbt_paint_L{i}"
    pa.location = (x0, y - 900)
    paint = _gated(g, pa.outputs["Fac"], I[p + "Paint Strength"], (x0 + 200, y - 900))

    # Curvature: Cycles Pointiness (0.5 flat, >0.5 convex ridges). Favours convex ground
    # (scree on ridges); gated by strength (0 = off). Cycles-only; EEVEE reads ~flat, so the
    # term degrades rather than breaks (a baked curvature mask is the EEVEE-safe path, S3+).
    curv_mask = _mrange(g, pointiness, 0.5, 0.75, 0.0, 1.0, (x0, y - 1060))
    curv = _gated(g, curv_mask, I[p + "Curvature Strength"], (x0 + 200, y - 1060))

    # Flow: rising smoothstep on the shared Flow Map around Flow Threshold (high flow -> 1, so
    # the layer keeps to channels/riverbeds), gated by strength. A fixed soft width keeps it to
    # two knobs. With no map wired (Flow Map = 0) a Flow-Strength layer correctly vanishes -- it
    # lives only in channels, which read as absent here.
    f_lo = _mmath(g, "SUBTRACT", I[p + "Flow Threshold"], _SLOPE_SOFT, (x0, y - 1200))
    flow_band = _mrange(g, I["Flow Map"], f_lo, I[p + "Flow Threshold"], 0.0, 1.0, (x0 + 200, y - 1200))
    flow = _gated(g, flow_band, I[p + "Flow Strength"], (x0 + 380, y - 1200))

    # Curve: the curve overlay's mask attribute (bbt_curve_mask, 1 on a path band; BobSplines C3).
    # Keeps the layer to a path/road, gated by strength. Absent attribute reads 0, so a
    # Curve-Strength layer correctly vanishes off every curve.
    ca = g.nodes.new("ShaderNodeAttribute")
    ca.attribute_type = "GEOMETRY"
    ca.attribute_name = "bbt_curve_mask"
    ca.location = (x0, y - 1340)
    curve = _gated(g, ca.outputs["Fac"], I[p + "Curve Strength"], (x0 + 200, y - 1340))

    # Curve B: a second curve channel off bbt_curve_mask_b, so a distinct role keys its own layer
    # (BobSplines R5). Same shape as Curve; gated by Curve B Strength (0 = off, the default).
    cb = g.nodes.new("ShaderNodeAttribute")
    cb.attribute_type = "GEOMETRY"
    cb.attribute_name = "bbt_curve_mask_b"
    cb.location = (x0, y - 1480)
    curve_b = _gated(g, cb.outputs["Fac"], I[p + "Curve B Strength"], (x0 + 200, y - 1480))

    # Weight = all masks, then Enable gates the layer out entirely.
    w = _mmath(g, "MULTIPLY", slope, alt, (x0 + 1040, y))
    w = _mmath(g, "MULTIPLY", w, noise, (x0 + 1210, y))
    w = _mmath(g, "MULTIPLY", w, paint, (x0 + 1380, y))
    w = _mmath(g, "MULTIPLY", w, curv, (x0 + 1550, y))
    w = _mmath(g, "MULTIPLY", w, flow, (x0 + 1720, y))
    w = _mmath(g, "MULTIPLY", w, curve, (x0 + 1890, y))
    w = _mmath(g, "MULTIPLY", w, curve_b, (x0 + 2060, y))

    # Height field for the height-lerp: presence (weight) + a per-layer macro noise + bias.
    mtex = g.nodes.new("ShaderNodeTexNoise")
    mtex.noise_dimensions = "4D"
    mtex.location = (x0 + 1040, y - 300)
    mtex.inputs["Detail"].default_value = 2.0
    mtex.inputs["W"].default_value = float(i) * 7.0
    g.links.new(pos, mtex.inputs["Vector"])
    g.links.new(I["Macro Scale"], mtex.inputs["Scale"])
    m_c = _mmath(g, "SUBTRACT", mtex.outputs["Fac"], 0.5, (x0 + 1220, y - 300))
    m_a = _mmath(g, "MULTIPLY", m_c, I["Macro Amount"], (x0 + 1390, y - 300))
    H = _mmath(g, "ADD", w, I[p + "Height Bias"], (x0 + 1560, y - 200))
    H = _mmath(g, "ADD", H, m_a, (x0 + 1730, y - 200))
    # Hard curve edge (R2): a steep remap of the raw curve mask swings H from a floor off the band
    # to well above every other layer on it, so the height-lerp pick flips within the mask's own
    # falloff (a crisp road edge) rather than over Blend Softness. Curve Hard 0 keeps the soft H, so
    # every layer that does not opt in (all of them by default) is byte-identical to before.
    hard_H = _mrange(g, ca.outputs["Fac"], 0.45, 0.55, -1.0, 2.0, (x0 + 1560, y - 1620))
    H = _lerp(g, H, hard_H, I[p + "Curve Hard"], (x0 + 1900, y - 200))
    hard_H_b = _mrange(g, cb.outputs["Fac"], 0.45, 0.55, -1.0, 2.0, (x0 + 1560, y - 1760))
    H = _lerp(g, H, hard_H_b, I[p + "Curve B Hard"], (x0 + 2080, y - 200))
    # Colour as tint and scalar-multiply the maps (identity when no texture set: white / 1).
    col = _vmul(g, I[p + "Base Color"], I[p + "Albedo Map"], (x0 + 1560, y - 380))
    rough = _mmath(g, "MULTIPLY", I[p + "Roughness"], I[p + "Roughness Map"], (x0 + 1560, y - 500))
    return col, rough, I[p + "Metallic"], H, I[p + "Enable"], I[p + "Detail Height"]


def terrain_master_group():
    """The multi-layer terrain blend, ending in S_Weather. One shared group, MAX_TERRAIN_LAYERS
    fixed slots; the stack is the enabled ones. See the module comment for the height-lerp."""
    g, _fresh = _cached_group(TERRAIN_MASTER)
    if not _fresh:
        return g

    _gin(g, "Blend Softness", "NodeSocketFloat", 0.15, 0.001, 1.0)
    _gin(g, "Macro Amount", "NodeSocketFloat", 0.15, 0.0, 1.0)
    _gin(g, "Macro Scale", "NodeSocketFloat", 0.3, 0.0)
    # The baked drainage-flow map, sampled per-terrain in the wrapper and fed here (0 = none).
    # A layer's Flow mask keys off this, so a sediment/gravel layer can live in the channels.
    _gin(g, "Flow Map", "NodeSocketFloat", 0.0, 0.0, 1.0)
    for i in range(MAX_TERRAIN_LAYERS):
        p = f"L{i} "
        _gin(g, p + "Enable", "NodeSocketFloat", 1.0 if i == 0 else 0.0, 0.0, 1.0)
        _gin(g, p + "Base Color", "NodeSocketColor", _TERRAIN_LAYER_COLORS[i])
        _gin(g, p + "Roughness", "NodeSocketFloat", 0.85, 0.0, 1.0)
        _gin(g, p + "Metallic", "NodeSocketFloat", 0.0, 0.0, 1.0)
        _gin(g, p + "Height Bias", "NodeSocketFloat", float(i) * 0.05)
        _gin(g, p + "Min Normal Z", "NodeSocketFloat", 0.0, 0.0, 1.0)
        _gin(g, p + "Max Normal Z", "NodeSocketFloat", 1.0, 0.0, 1.0)
        _gin(g, p + "Slope Strength", "NodeSocketFloat", 0.0, 0.0, 1.0)
        _gin(g, p + "Height Min", "NodeSocketFloat", -1000.0)
        _gin(g, p + "Height Max", "NodeSocketFloat", 1000.0)
        _gin(g, p + "Height Falloff", "NodeSocketFloat", 5.0, 0.0)
        _gin(g, p + "Height Strength", "NodeSocketFloat", 0.0, 0.0, 1.0)
        _gin(g, p + "Noise Scale", "NodeSocketFloat", 0.15, 0.0)
        _gin(g, p + "Noise Contrast", "NodeSocketFloat", 0.5, 0.0, 1.0)
        _gin(g, p + "Noise Seed", "NodeSocketFloat", float(i))
        _gin(g, p + "Noise Strength", "NodeSocketFloat", 0.0, 0.0, 1.0)
        _gin(g, p + "Paint Strength", "NodeSocketFloat", 0.0, 0.0, 1.0)
        _gin(g, p + "Curvature Strength", "NodeSocketFloat", 0.0, 0.0, 1.0)
        # Flow band: keep this layer where the drainage-flow map is above Flow Threshold
        # (channels/riverbeds). Gated by Flow Strength (0 = off, the default).
        _gin(g, p + "Flow Strength", "NodeSocketFloat", 0.0, 0.0, 1.0)
        _gin(g, p + "Flow Threshold", "NodeSocketFloat", 0.6, 0.0, 1.0)
        # Curve band: keep this layer to a path/road, keyed off the curve overlay's baked
        # bbt_curve_mask attribute (BobSplines C3, docs/SPLINES.md 4.4). Gated by Curve Strength
        # (0 = off, the default), the same shape as the Flow mask keying a riverbed layer.
        _gin(g, p + "Curve Strength", "NodeSocketFloat", 0.0, 0.0, 1.0)
        # Curve Hard (BobSplines R2, docs/SPLINES.md 9 #7): 0 = the soft height-lerp edge (default,
        # unchanged), 1 = a crisp edge gated straight off the curve mask, so a road surface stops
        # sharply at the band regardless of Blend Softness rather than feathering over it.
        _gin(g, p + "Curve Hard", "NodeSocketFloat", 0.0, 0.0, 1.0)
        # Curve B (BobSplines R5): a SECOND curve channel keyed off bbt_curve_mask_b, so a distinct
        # role (a paved road) keys its own surface layer without sharing the dirt-path look. Same
        # shape as Curve; both default off, so a layer opts into at most one.
        _gin(g, p + "Curve B Strength", "NodeSocketFloat", 0.0, 0.0, 1.0)
        _gin(g, p + "Curve B Hard", "NodeSocketFloat", 0.0, 0.0, 1.0)
        # Texture-set maps (S3), identity defaults so an untextured layer is unchanged.
        _gin(g, p + "Albedo Map", "NodeSocketColor", (1.0, 1.0, 1.0, 1.0))
        _gin(g, p + "Roughness Map", "NodeSocketFloat", 1.0, 0.0, 1.0)
        _gin(g, p + "Detail Height", "NodeSocketFloat", 0.0)
    # Weather passthrough (identical to S_Weather's inputs).
    _gin(g, "Snow Strength", "NodeSocketFloat", 1.0, 0.0, 1.0)
    _gin(g, "Use Attribute", "NodeSocketFloat", 1.0, 0.0, 1.0)  # terrain carries the pass
    _gin(g, "Slope Threshold", "NodeSocketFloat", 0.5, 0.0, 1.0)
    _gin(g, "Slope Falloff", "NodeSocketFloat", 0.2, 0.0, 1.0)
    _gin(g, "Altitude", "NodeSocketFloat", 0.0)
    _gin(g, "Altitude Falloff", "NodeSocketFloat", 5.0, 0.0)
    for _wn, _wd in _WEATHER_EXTRA:
        _gin(g, _wn, "NodeSocketFloat", _wd, 0.0, 1.0)
    # Terrain-only wetness passthrough (fed from the wrapper's wetness-map sample). Declared
    # here rather than in _WEATHER_EXTRA so surface materials do not carry these unused inputs.
    _gin(g, "Wetness Map", "NodeSocketFloat", 0.0, 0.0, 1.0)
    _gin(g, "Terrain Wetness", "NodeSocketFloat", 0.0, 0.0, 1.0)
    _gout(g, "Base Color", "NodeSocketColor")
    _gout(g, "Roughness", "NodeSocketFloat")
    _gout(g, "Metallic", "NodeSocketFloat")
    _gout(g, "Height", "NodeSocketFloat")  # blended detail height, the wrapper bumps it

    gi = g.nodes.new("NodeGroupInput")
    gi.location = (-3000, 0)
    go = g.nodes.new("NodeGroupOutput")
    go.location = (2200, 0)
    I = gi.outputs

    geo = g.nodes.new("ShaderNodeNewGeometry")
    geo.location = (-3000, -1600)
    nsep = g.nodes.new("ShaderNodeSeparateXYZ")
    nsep.location = (-2820, -1560)
    g.links.new(geo.outputs["Normal"], nsep.inputs[0])
    psep = g.nodes.new("ShaderNodeSeparateXYZ")
    psep.location = (-2820, -1720)
    g.links.new(geo.outputs["Position"], psep.inputs[0])
    pos, nz, wz = geo.outputs["Position"], nsep.outputs["Z"], psep.outputs["Z"]
    pointiness = geo.outputs["Pointiness"]

    layers = [_terrain_layer(g, I, i, pos, nz, wz, pointiness, -2600)
              for i in range(MAX_TERRAIN_LAYERS)]

    # Height-lerp fold: acc starts at layer 0 (the base fill); each further layer blends in
    # by fac = enable * b2/(b1+b2), where b1/b2 are the heights above (max(H) - Blend Softness),
    # so the higher-H layer wins per texel within the soft band. Enable gates a layer out.
    soft = I["Blend Softness"]
    acc_col, acc_rough, acc_metal, acc_H, _e0, acc_dh = layers[0]
    fx = 700
    for i in range(1, MAX_TERRAIN_LAYERS):
        col, rough, metal, H, enable, dh = layers[i]
        fy = -i * 300
        # Gate the height by Enable before it enters the fold. Enable only zeroing the colour
        # blend factor (below) let a DISABLED layer still push its height into acc_H, raising the
        # bar for later layers and suppressing them (a disabled slot's masks default to strength 0,
        # so its height is high). Fold H toward a floor when disabled so it never wins and never
        # pollutes acc_H; an enabled layer (Enable 1) is unchanged, so enabled terrains are identical.
        H = _lerp(g, -1000.0, H, enable, (fx - 200, fy))
        hmax = _mmath(g, "MAXIMUM", acc_H, H, (fx, fy))
        ma = _mmath(g, "SUBTRACT", hmax, soft, (fx + 170, fy))
        b1 = _mmath(g, "MAXIMUM", _mmath(g, "SUBTRACT", acc_H, ma, (fx + 340, fy + 80)), 0.0, (fx + 510, fy + 80))
        b2 = _mmath(g, "MAXIMUM", _mmath(g, "SUBTRACT", H, ma, (fx + 340, fy - 80)), 0.0, (fx + 510, fy - 80))
        bsum = _mmath(g, "ADD", _mmath(g, "ADD", b1, b2, (fx + 680, fy)), 1e-6, (fx + 850, fy))
        fac = _mmath(g, "DIVIDE", b2, bsum, (fx + 1020, fy))
        fac = _mmath(g, "MULTIPLY", fac, enable, (fx + 1190, fy))  # disabled layer never wins
        acc_col = _mixcol(g, fac, acc_col, col, (fx + 1360, fy + 200))
        acc_rough = _lerp(g, acc_rough, rough, fac, (fx + 1360, fy))
        acc_metal = _lerp(g, acc_metal, metal, fac, (fx + 1360, fy - 220))
        acc_dh = _lerp(g, acc_dh, dh, fac, (fx + 1360, fy - 560))
        acc_H = _mmath(g, "MAXIMUM", acc_H, H, (fx + 1360, fy - 400))
        fx += 1600

    # End in S_Weather: the blended base + the snow passthrough.
    weather = g.nodes.new("ShaderNodeGroup")
    weather.node_tree = weather_group()
    weather.location = (fx + 200, 0)
    g.links.new(acc_col, weather.inputs["Base Color"])
    g.links.new(acc_rough, weather.inputs["Roughness"])
    g.links.new(acc_metal, weather.inputs["Metallic"])
    # Damp bed (BobSplines C5.4): a river/stream overlay writes bbt_curve_wet along its channel; MAX
    # it into the Wetness Map so the bed and banks read damp (materials.apply_curve_wet raises
    # Terrain Wetness, the multiplier that path is gated by, so it shows). An absent attribute reads
    # 0, so a terrain with no river is byte-identical; weather still amplifies it (rain raises env
    # wetness in S_Weather, and wetf takes the MAX of the terrain map and the weather wetness).
    cwet = g.nodes.new("ShaderNodeAttribute")
    cwet.attribute_type = "GEOMETRY"
    cwet.attribute_name = "bbt_curve_wet"
    cwet.location = (fx + 40, -900)
    wetmap = g.nodes.new("ShaderNodeMath")
    wetmap.operation = "MAXIMUM"
    wetmap.use_clamp = True
    wetmap.location = (fx + 220, -900)
    g.links.new(I["Wetness Map"], wetmap.inputs[0])
    g.links.new(cwet.outputs["Fac"], wetmap.inputs[1])
    g.links.new(wetmap.outputs["Value"], weather.inputs["Wetness Map"])
    for name in ("Snow Strength", "Use Attribute", "Slope Threshold", "Slope Falloff",
                 "Altitude", "Altitude Falloff", "Terrain Wetness",
                 *[n for n, _ in _WEATHER_EXTRA]):
        g.links.new(I[name], weather.inputs[name])
    for name in ("Base Color", "Roughness", "Metallic"):
        g.links.new(weather.outputs[name], go.inputs[name])
    g.links.new(acc_dh, go.inputs["Height"])
    return g


def terrain_material(mat_name, layer_sets=None, flow_image=None, wetness_image=None,
                     terrain_size=None):
    """A multi-layer terrain wrapper (S_TerrainMaster), optionally with per-layer texture sets
    and the baked flow/wetness maps.

    layer_sets maps a layer index to a texture-set name. Each set's triplanar maps feed that
    layer's Albedo/Roughness map inputs and its detail height; the blended detail height across
    all layers drives one Bump node into the Principled normal (a single terrain normal, no
    per-layer tangent problem). Colour still tints; a newly textured layer defaults to a white
    tint and Roughness 1 so its map reads at face value.

    flow_image / wetness_image are the baked drainage maps (siblings of the heightmap). When
    given, they are sampled per-terrain by object-space XY (UV = pos.xy/size + 0.5, matching the
    heightmap_terrain displacement) and fed into the master's Flow Map / Wetness Map inputs, so a
    layer's Flow mask and the terrain wetness key off the terrain's own drainage."""
    master = terrain_master_group()
    ls = layer_sets or {}
    size = float(terrain_size or 90.0)
    sig = ("terrain|" + ",".join(f"{i}:{ls[i]}" for i in sorted(ls))
           + ("|flow" if flow_image is not None else "")
           + ("|wet" if wetness_image is not None else ""))

    def _map_uv(nt):
        """Object-space XY -> heightmap UV, shared by the flow/wetness samples. Object coords
        match the grid's local space (where the heightmap was sampled), so the maps line up even
        if the terrain object is moved."""
        tc = nt.nodes.new("ShaderNodeTexCoord")
        tc.location = (-1000, -520)
        sep = nt.nodes.new("ShaderNodeSeparateXYZ")
        sep.location = (-820, -520)
        nt.links.new(tc.outputs["Object"], sep.inputs[0])
        u = _mmath(nt, "ADD", _mmath(nt, "DIVIDE", sep.outputs["X"], size, (-640, -480)), 0.5, (-460, -480))
        v = _mmath(nt, "ADD", _mmath(nt, "DIVIDE", sep.outputs["Y"], size, (-640, -620)), 0.5, (-460, -620))
        uvw = nt.nodes.new("ShaderNodeCombineXYZ")
        uvw.location = (-300, -540)
        nt.links.new(u, uvw.inputs["X"])
        nt.links.new(v, uvw.inputs["Y"])
        return uvw.outputs["Vector"]

    def wire(nt, grp, bsdf, old_sig):
        prev = old_sig or ""
        for i in sorted(ls):
            ts = nt.nodes.new("ShaderNodeGroup")
            ts.name = f"TexSet{i}"
            ts.node_tree = texture_set_group(ls[i])
            ts.location = (-520, -180 - i * 240)
            nt.links.new(ts.outputs["Base Color"], grp.inputs[f"L{i} Albedo Map"])
            nt.links.new(ts.outputs["Roughness"], grp.inputs[f"L{i} Roughness Map"])
            nt.links.new(ts.outputs["Height"], grp.inputs[f"L{i} Detail Height"])
            if f"{i}:{ls[i]}" not in prev:  # newly textured -> show the map at face value
                grp.inputs[f"L{i} Base Color"].default_value = (1.0, 1.0, 1.0, 1.0)
                grp.inputs[f"L{i} Roughness"].default_value = 1.0
        if ls:  # one bump from the blended detail height drives the terrain normal
            bump = nt.nodes.new("ShaderNodeBump")
            bump.location = (80, -260)
            bump.inputs["Strength"].default_value = 0.3
            nt.links.new(grp.outputs["Height"], bump.inputs["Height"])
            nt.links.new(bump.outputs["Normal"], bsdf.inputs["Normal"])
        # Baked drainage maps: sample by the shared object-space UV and feed the master.
        if flow_image is not None or wetness_image is not None:
            uv = _map_uv(nt)
            for img, socket, yy in ((flow_image, "Flow Map", -520),
                                    (wetness_image, "Wetness Map", -760)):
                if img is None:
                    continue
                img.colorspace_settings.name = "Non-Color"
                tex = nt.nodes.new("ShaderNodeTexImage")
                tex.image = img
                tex.interpolation = "Linear"
                tex.extension = "EXTEND"
                tex.location = (-120, yy)
                nt.links.new(uv, tex.inputs["Vector"])
                nt.links.new(tex.outputs["Color"], grp.inputs[socket])

    return _build_wrapper(mat_name, master, sig, wire)


# Assigning a material to a GEOMETRY-NODES-generated mesh (the terrain, heightmap_terrain)
# does not work through the object's material slots: the GN grid output carries no material,
# so obj.active_material is ignored and the default shader renders (confirmed empirically,
# and the heightmap_terrain recipe notes the same). The reliable path is a Set Material node
# inside the node stack. So BobShaders assigns via a small per-material Set-Material group
# appended as its own modifier, which shades the GN output, survives the terrain's
# non-destructive rebuild (a separate modifier), and passes snow_cover through untouched.
SET_MATERIAL_MOD = "BBT_Material"
_SET_MATERIAL_PREFIX = "BBT_SetMat_"


def _set_material_group(mat):
    """A trivial GN group: Geometry -> Set Material(mat) -> Geometry, cached per material."""
    name = _SET_MATERIAL_PREFIX + mat.name
    g = bpy.data.node_groups.get(name)
    if g is not None:
        for n in g.nodes:
            if n.bl_idname == "GeometryNodeSetMaterial":
                n.inputs["Material"].default_value = mat
        return g
    g = bpy.data.node_groups.new(name, "GeometryNodeTree")
    g.interface.new_socket("Geometry", in_out="INPUT", socket_type="NodeSocketGeometry")
    g.interface.new_socket("Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")
    gi = g.nodes.new("NodeGroupInput")
    gi.location = (-200, 0)
    go = g.nodes.new("NodeGroupOutput")
    go.location = (200, 0)
    sm = g.nodes.new("GeometryNodeSetMaterial")
    sm.inputs["Material"].default_value = mat
    g.links.new(gi.outputs["Geometry"], sm.inputs["Geometry"])
    g.links.new(sm.outputs["Geometry"], go.inputs["Geometry"])
    return g


def assign_material(obj, mat):
    """Assign a material to an object so it actually shades, GN-generated meshes included.

    Normal meshes and scatter instances shade through the object slot; a GN-generated mesh
    ignores the slot, so for any object carrying a Nodes modifier we also drive a Set Material
    modifier at the end of the stack. Returns True on a mesh, False otherwise."""
    if obj is None or obj.type != "MESH":
        return False
    obj.active_material = mat
    if any(m.type == "NODES" for m in obj.modifiers):
        mod = next((m for m in obj.modifiers if m.name == SET_MATERIAL_MOD), None)
        if mod is None:
            mod = obj.modifiers.new(SET_MATERIAL_MOD, "NODES")
        mod.node_group = _set_material_group(mat)
        # Keep it last so it runs after the terrain and the snow-coverage pass; Set Material
        # leaves snow_cover and the geometry untouched, it only tags the faces.
        obj.modifiers.move(list(obj.modifiers).index(mod), len(obj.modifiers) - 1)
    return True


# BobShaders texture sets (S3). A texture set is a folder under library/textures/<name>/ with
# conventionally named maps (*_basecolor, *_roughness, *_normal, *_height, *_ao, *_metallic).
# The default surface is still a solid tint; a set is optional and layers on top. Triplanar
# is Blender's built-in BOX projection on the Image Texture node (world-projected, no UVs, so
# it works on terrain), and the surface normal comes from a Bump node fed by the height map
# (robust on un-UV'd meshes, unlike a tangent-space normal map). Colour is a tint: the master
# multiplies Base Color into the albedo map, so white leaves the texture unchanged.
TEXTURE_SET_PREFIX = "S_TextureSet_"
_TRIPLANAR_BLEND = 0.3  # BOX projection_blend: 0 sharp seams, 1 soft (build-time property)
# Anti-tiling (F1): a triplanar repeat betrays itself two ways over a big terrain - the pattern
# repeats at a distance (far), and the exact same tile reads up close (near). Two build-time
# frequencies fix both: a DETAIL sample of every map at a lower frequency (bigger features) is
# blended with the base sample (Detail Blend), so no single repeat dominates near or mid; and a
# low-frequency world MACRO noise modulates the albedo brightness (Macro Amount) so the far field
# stops reading as one flat tiled sheet. Both are live knobs, default on but gentle.
_DETAIL_SCALE = 0.28  # the detail sample's relative frequency (lower = bigger, slower-repeating)
_MACRO_SCALE = 0.08   # the macro brightness noise frequency (low = large patches)
_FAR_SCALE = 0.008    # the distant sample's relative frequency (very low: ~no repeat far off)

# role -> filename suffix aliases (matched case-insensitively against the file stem).
_ROLE_SUFFIX = {
    "basecolor": ("basecolor", "albedo", "diffuse", "diff", "color", "col"),
    "roughness": ("roughness", "rough"),
    "metallic": ("metallic", "metalness", "metal"),
    "normal": ("normal", "nor_gl", "nor"),
    "height": ("height", "displacement", "disp"),
    "ao": ("ao", "ambientocclusion", "occlusion"),
}
_IMG_EXT = (".png", ".jpg", ".jpeg", ".exr", ".tif", ".tiff")
_DATA_ROLES = ("roughness", "metallic", "normal", "height")  # Non-Color


def _find_maps(directory):
    """Map role -> absolute file path for the maps present in a texture-set folder."""
    found = {}
    if not directory or not os.path.isdir(directory):
        return found
    files = [f for f in os.listdir(directory)
             if os.path.splitext(f)[1].lower() in _IMG_EXT]
    for role, suffixes in _ROLE_SUFFIX.items():
        for f in files:
            stem = os.path.splitext(f)[0].lower()
            # Require the documented `<name>_<map>` separator (or the bare map name), so a short
            # alias like "col"/"nor"/"ao" can't mid-match an unrelated filename.
            if any(stem.endswith("_" + s) or stem == s for s in suffixes):
                found[role] = os.path.join(directory, f)
                break
    return found


def texture_set_dir(name):
    """The library/textures/<name> folder, resolved from this file's repo location."""
    repo = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    return os.path.join(repo, "library", "textures", name)


def texture_set_group(name, directory=None):
    """A cached per-set group that triplanar-samples the set's maps and outputs Base Color
    (albedo * AO), Roughness, Metallic, Normal (bump from height), and Height. Cached by name
    (the images are node properties, not sockets, so the group is per set, like the ground-fog
    material). Scale is a live knob (Mapping scale); the triplanar blend is a build property."""
    gname = TEXTURE_SET_PREFIX + name
    g, _fresh = _cached_group(gname)
    if not _fresh:
        return g
    maps = _find_maps(directory if directory is not None else texture_set_dir(name))
    _gin(g, "Scale", "NodeSocketFloat", 1.0, 0.0)
    _gin(g, "Bump Strength", "NodeSocketFloat", 0.3, 0.0, 10.0)
    _gin(g, "Detail Blend", "NodeSocketFloat", 0.4, 0.0, 1.0)  # anti-tiling detail-scale mix
    _gin(g, "Macro Amount", "NodeSocketFloat", 0.3, 0.0, 1.0)  # anti-tiling macro brightness
    # Near-far anti-tiling: ramp the de-tile with camera distance (0 = off).
    _gin(g, "Distance Fade", "NodeSocketFloat", 0.7, 0.0, 1.0)
    _gin(g, "Fade Near", "NodeSocketFloat", 15.0, 0.0)
    _gin(g, "Fade Far", "NodeSocketFloat", 120.0, 0.0)
    _gout(g, "Base Color", "NodeSocketColor")
    _gout(g, "Roughness", "NodeSocketFloat")
    _gout(g, "Metallic", "NodeSocketFloat")
    _gout(g, "Normal", "NodeSocketVector")
    _gout(g, "Height", "NodeSocketFloat")
    gi = g.nodes.new("NodeGroupInput")
    gi.location = (-1000, 0)
    go = g.nodes.new("NodeGroupOutput")
    go.location = (700, 0)
    I, O = gi.outputs, go.inputs

    geo = g.nodes.new("ShaderNodeNewGeometry")
    geo.location = (-1000, 300)
    cs = g.nodes.new("ShaderNodeCombineXYZ")
    cs.location = (-800, 120)
    for ax in ("X", "Y", "Z"):
        g.links.new(I["Scale"], cs.inputs[ax])
    mapping = g.nodes.new("ShaderNodeMapping")
    mapping.location = (-620, 260)
    g.links.new(geo.outputs["Position"], mapping.inputs["Vector"])
    g.links.new(cs.outputs["Vector"], mapping.inputs["Scale"])

    # A second, lower-frequency mapping for the anti-tiling detail sample (Scale * _DETAIL_SCALE).
    dscale = _mmath(g, "MULTIPLY", I["Scale"], _DETAIL_SCALE, (-800, -220))
    dcs = g.nodes.new("ShaderNodeCombineXYZ")
    dcs.location = (-800, -320)
    for ax in ("X", "Y", "Z"):
        g.links.new(dscale, dcs.inputs[ax])
    detail_mapping = g.nodes.new("ShaderNodeMapping")
    detail_mapping.location = (-620, -320)
    g.links.new(geo.outputs["Position"], detail_mapping.inputs["Vector"])
    g.links.new(dcs.outputs["Vector"], detail_mapping.inputs["Scale"])

    # A third, very-low-frequency mapping for the distant sample (Scale * _FAR_SCALE). Its repeat
    # period is so large it does not read as a tile across the far field; the near/mid de-tile
    # still tiles, so we blend TO this only with distance.
    fscale = _mmath(g, "MULTIPLY", I["Scale"], _FAR_SCALE, (-800, -420))
    fcs = g.nodes.new("ShaderNodeCombineXYZ")
    fcs.location = (-800, -460)
    for ax in ("X", "Y", "Z"):
        g.links.new(fscale, fcs.inputs[ax])
    far_mapping = g.nodes.new("ShaderNodeMapping")
    far_mapping.location = (-620, -460)
    g.links.new(geo.outputs["Position"], far_mapping.inputs["Vector"])
    g.links.new(fcs.outputs["Vector"], far_mapping.inputs["Scale"])

    # Near-far de-tiling: a triplanar repeat reads fine up close but betrays itself as an obvious
    # tiled sheet in the distance. df ramps 0 (near) -> 1 (far) over Fade Near..Fade Far by camera
    # View Distance; box2 blends the near/mid de-tile toward the very-low-frequency far sample by
    # df, so the repeat washes out with distance. df = 0 near, so the near field is unchanged.
    cam = g.nodes.new("ShaderNodeCameraData")
    cam.location = (-1000, -560)
    df = _mmath(g, "MULTIPLY",
                _mrange(g, cam.outputs["View Distance"], I["Fade Near"], I["Fade Far"],
                        0.0, 1.0, (-800, -560)),
                I["Distance Fade"], (-620, -560))
    eff_macro = _lerp(g, I["Macro Amount"], 0.85, df, (-440, -640))

    def _sample(path, role, loc, map_node):
        img = bpy.data.images.load(path, check_existing=True)
        if role in _DATA_ROLES:
            img.colorspace_settings.name = "Non-Color"
        t = g.nodes.new("ShaderNodeTexImage")
        t.image = img
        t.projection = "BOX"
        t.projection_blend = _TRIPLANAR_BLEND
        t.extension = "REPEAT"
        t.location = loc
        g.links.new(map_node.outputs["Vector"], t.inputs["Vector"])
        return t

    def box(path, role, loc):
        """A single base-scale triplanar sample (node)."""
        return _sample(path, role, loc, mapping)

    def box2(path, role, loc):
        """A de-tiled sample (socket): the base-scale sample blended with a lower-frequency
        detail-scale sample by Detail Blend (near/mid de-tile), then blended toward a very-low-
        frequency far sample by the camera-distance factor df (so the far field stops tiling)."""
        base = _sample(path, role, loc, mapping).outputs["Color"]
        detail = _sample(path, role, (loc[0], loc[1] + 150), detail_mapping).outputs["Color"]
        near = _mixcol(g, I["Detail Blend"], base, detail, (loc[0] + 200, loc[1] + 40))
        far = _sample(path, role, (loc[0], loc[1] + 300), far_mapping).outputs["Color"]
        return _mixcol(g, df, near, far, (loc[0] + 380, loc[1] + 20))

    def const_col(rgb, loc):
        n = g.nodes.new("ShaderNodeRGB")
        n.outputs[0].default_value = (*rgb, 1.0)
        n.location = loc
        return n.outputs[0]

    def const_val(v, loc):
        n = g.nodes.new("ShaderNodeValue")
        n.outputs[0].default_value = v
        n.location = loc
        return n.outputs[0]

    # Albedo * AO, de-tiled, then macro brightness break-up (AO folded in here; anti-tiling F1).
    if "basecolor" in maps:
        albedo = box2(maps["basecolor"], "basecolor", (-300, 400))
        if "ao" in maps:
            ao = box(maps["ao"], "ao", (-300, 200)).outputs["Color"]
            albedo = _vmul(g, albedo, ao, (-60, 380))
        albedo = _macro_break(g, albedo, eff_macro, _MACRO_SCALE, (140, 400))
    else:
        albedo = const_col((1.0, 1.0, 1.0), (-60, 400))
    g.links.new(albedo, O["Base Color"])

    # Roughness / Metallic: a Color->Float link auto-converts (the maps are greyscale). Roughness
    # is de-tiled like the albedo; metallic is usually flat, so a single sample is enough.
    rough = box2(maps["roughness"], "roughness", (-300, 40)) \
        if "roughness" in maps else const_val(1.0, (-60, 40))
    g.links.new(rough, O["Roughness"])
    metal = box(maps["metallic"], "metallic", (-300, -140)).outputs["Color"] \
        if "metallic" in maps else const_val(1.0, (-60, -140))
    g.links.new(metal, O["Metallic"])

    # Height and the bump-derived normal (works without UVs/tangents). De-tiling the height also
    # de-tiles the bump normal, so the surface relief stops repeating in step with the albedo.
    if "height" in maps:
        h = box2(maps["height"], "height", (-300, -320))
        g.links.new(h, O["Height"])
        bump = g.nodes.new("ShaderNodeBump")
        bump.location = (300, -260)
        g.links.new(I["Bump Strength"], bump.inputs["Strength"])
        g.links.new(h, bump.inputs["Height"])
        g.links.new(bump.outputs["Normal"], O["Normal"])
    else:
        g.links.new(const_val(0.0, (-60, -320)), O["Height"])
        g.links.new(geo.outputs["Normal"], O["Normal"])
    return g


def _vmul(g, a, b, loc):
    """Component-wise colour/vector multiply (RGB; alpha dropped)."""
    n = g.nodes.new("ShaderNodeVectorMath")
    n.operation = "MULTIPLY"
    n.location = loc
    g.links.new(a, n.inputs[0])
    g.links.new(b, n.inputs[1])
    return n.outputs["Vector"]


def _macro_break(g, color_vec, amount, scale, loc):
    """Modulate an albedo vector by a low-frequency world noise (anti-tiling). Amount 0 = off:
    factor = 1 + (noise - 0.5) * Amount, applied as a scale on the colour."""
    ng = g.nodes.new("ShaderNodeNewGeometry")
    ng.location = (loc[0] - 400, loc[1] + 220)
    noise = g.nodes.new("ShaderNodeTexNoise")
    noise.location = (loc[0] - 220, loc[1] + 140)
    noise.inputs["Detail"].default_value = 2.0
    g.links.new(ng.outputs["Position"], noise.inputs["Vector"])
    _mplug(g, noise.inputs["Scale"], scale)
    d = _mmath(g, "SUBTRACT", noise.outputs["Fac"], 0.5, (loc[0] - 40, loc[1]))
    da = _mmath(g, "MULTIPLY", d, amount, (loc[0] + 120, loc[1]))
    fac = g.nodes.new("ShaderNodeMath")
    fac.operation = "ADD"
    fac.location = (loc[0] + 280, loc[1])
    fac.inputs[1].default_value = 1.0
    g.links.new(da, fac.inputs[0])
    out = g.nodes.new("ShaderNodeVectorMath")
    out.operation = "SCALE"
    out.location = (loc[0] + 460, loc[1] + 60)
    g.links.new(color_vec, out.inputs[0])
    g.links.new(fac.outputs["Value"], out.inputs["Scale"])
    return out.outputs["Vector"]


def _snapshot_group_inputs(node):
    """Snapshot a wrapper's Master node input default_values by socket name (to survive a
    structural rebuild, the shader analogue of the GN modifier snapshot)."""
    snap = {}
    if node is None:
        return snap
    for s in node.inputs:
        try:
            v = s.default_value
        except (AttributeError, TypeError):
            continue
        if hasattr(v, "__len__") and not isinstance(v, str):
            v = tuple(v)
        snap[s.name] = v
    return snap


def _restore_group_inputs(node, snap):
    for s in node.inputs:
        if s.name in snap:
            try:
                s.default_value = snap[s.name]
            except (AttributeError, TypeError, ValueError):
                pass
