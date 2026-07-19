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
)

# Snow shading constants (the surface-snow look): a slightly cool near-white albedo and a
# soft, high roughness. Kept here so the shader and any future accumulation-shell tint agree.
_SNOW_ALBEDO = (0.90, 0.93, 0.97, 1.0)
_SNOW_ROUGHNESS = 0.6


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


def env_state_group():
    """The world-to-shader bridge: one shared group whose internal Value nodes hold the
    live env fields, driven once from scene.bbt_env by the panel. Because a node group is
    a single datablock shared by every material that instances it, driving it once feeds
    every surface (Phase-0). No inputs; outputs Snow, Wetness, Temperature. When Firmament
    is absent no driver is installed and the Value defaults stand (no snow), so a material
    still renders standalone."""
    g = bpy.data.node_groups.get(ENV_STATE)
    if g is not None:
        return g
    g = bpy.data.node_groups.new(ENV_STATE, "ShaderNodeTree")
    _gout(g, "Snow", "NodeSocketFloat")
    _gout(g, "Wetness", "NodeSocketFloat")
    _gout(g, "Temperature", "NodeSocketFloat")
    go = g.nodes.new("NodeGroupOutput")
    go.location = (300, 0)
    for i, (node_name, _field, default) in enumerate(ENV_STATE_DRIVERS):
        v = g.nodes.new("ShaderNodeValue")
        v.name = v.label = node_name
        v.location = (0, -i * 160)
        v.outputs[0].default_value = default
        g.links.new(v.outputs[0], go.inputs[i])
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
    g = bpy.data.node_groups.get(WEATHER)
    if g is not None:
        return g
    g = bpy.data.node_groups.new(WEATHER, "ShaderNodeTree")
    _gin(g, "Base Color", "NodeSocketColor", (0.5, 0.5, 0.5, 1.0))
    _gin(g, "Roughness", "NodeSocketFloat", 0.5, 0.0, 1.0)
    _gin(g, "Metallic", "NodeSocketFloat", 0.0, 0.0, 1.0)
    _gin(g, "Snow Strength", "NodeSocketFloat", 1.0, 0.0, 1.0)
    _gin(g, "Use Attribute", "NodeSocketFloat", 0.0, 0.0, 1.0)
    _gin(g, "Slope Threshold", "NodeSocketFloat", 0.5, 0.0, 1.0)
    _gin(g, "Slope Falloff", "NodeSocketFloat", 0.2, 0.0, 1.0)
    _gin(g, "Altitude", "NodeSocketFloat", 0.0)
    _gin(g, "Altitude Falloff", "NodeSocketFloat", 5.0, 0.0)
    _gout(g, "Base Color", "NodeSocketColor")
    _gout(g, "Roughness", "NodeSocketFloat")
    _gout(g, "Metallic", "NodeSocketFloat")

    gi = g.nodes.new("NodeGroupInput")
    gi.location = (-1200, 200)
    go = g.nodes.new("NodeGroupOutput")
    go.location = (900, 0)
    I, O = gi.outputs, go.inputs

    env = g.nodes.new("ShaderNodeGroup")
    env.node_tree = env_state_group()
    env.location = (-1200, -520)
    snow_amount = env.outputs["Snow"]

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

    # Shade: whiten albedo, soften roughness toward the snow value, drop metallic.
    out_color = _mixcol(g, sf, I["Base Color"], _SNOW_ALBEDO, (520, 120))
    inv_sf = _mmath(g, "SUBTRACT", 1.0, sf, (300, 20))
    r_base = _mmath(g, "MULTIPLY", I["Roughness"], inv_sf, (480, 40))
    r_snow = _mmath(g, "MULTIPLY", _SNOW_ROUGHNESS, sf, (480, -100))
    out_rough = _mmath(g, "ADD", r_base, r_snow, (660, -20))
    out_metal = _mmath(g, "MULTIPLY", I["Metallic"], inv_sf, (660, -180))

    g.links.new(out_color, O["Base Color"])
    g.links.new(out_rough, O["Roughness"])
    g.links.new(out_metal, O["Metallic"])
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
    g = bpy.data.node_groups.get(SURFACE_MASTER)
    if g is not None:
        return g
    g = bpy.data.node_groups.new(SURFACE_MASTER, "ShaderNodeTree")
    _gin(g, "Base Color", "NodeSocketColor", (0.5, 0.5, 0.5, 1.0))
    _gin(g, "Roughness", "NodeSocketFloat", 0.5, 0.0, 1.0)
    _gin(g, "Metallic", "NodeSocketFloat", 0.0, 0.0, 1.0)
    _gin(g, "Variation", "NodeSocketFloat", 0.0, 0.0, 1.0)
    _gin(g, "Snow Strength", "NodeSocketFloat", 1.0, 0.0, 1.0)
    _gin(g, "Use Attribute", "NodeSocketFloat", 0.0, 0.0, 1.0)
    _gin(g, "Slope Threshold", "NodeSocketFloat", 0.5, 0.0, 1.0)
    _gin(g, "Slope Falloff", "NodeSocketFloat", 0.2, 0.0, 1.0)
    _gin(g, "Altitude", "NodeSocketFloat", 0.0)
    _gin(g, "Altitude Falloff", "NodeSocketFloat", 5.0, 0.0)
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

    weather = g.nodes.new("ShaderNodeGroup")
    weather.node_tree = weather_group()
    weather.location = (100, 0)
    g.links.new(hsv.outputs["Color"], weather.inputs["Base Color"])
    for name in ("Roughness", "Metallic", "Snow Strength", "Use Attribute",
                 "Slope Threshold", "Slope Falloff", "Altitude", "Altitude Falloff"):
        g.links.new(I[name], weather.inputs[name])
    for name in ("Base Color", "Roughness", "Metallic"):
        g.links.new(weather.outputs[name], go.inputs[name])
    return g


def _wrapper_material(mat_name, master):
    """Get-or-create a thin wrapper material: one master group node ("Master") feeding one
    Principled BSDF and the Output. The wrapper's group-node input values are the instance
    parameters, drawn live in the panel. Get-or-create (not rebuild) so a re-Build never
    wipes tuned inputs; iterate the master graph itself by deleting the S_ group or
    re-enabling the addon. Shared by the surface and terrain masters."""
    name = mat_name if mat_name.startswith(SURFACE_WRAPPER_PREFIX) else SURFACE_WRAPPER_PREFIX + mat_name
    mat = bpy.data.materials.get(name)
    if mat is not None:
        node = mat.node_tree.nodes.get("Master") if mat.use_nodes and mat.node_tree else None
        if node is not None and node.type == "GROUP" and node.node_tree is master:
            return mat  # already wired to the current master; keep tuned inputs
    if mat is None:
        mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    nt = mat.node_tree
    nt.nodes.clear()
    out = nt.nodes.new("ShaderNodeOutputMaterial")
    out.location = (500, 0)
    bsdf = nt.nodes.new("ShaderNodeBsdfPrincipled")
    bsdf.location = (200, 0)
    grp = nt.nodes.new("ShaderNodeGroup")
    grp.name = "Master"
    grp.node_tree = master
    grp.location = (-150, 0)
    nt.links.new(grp.outputs["Base Color"], bsdf.inputs["Base Color"])
    nt.links.new(grp.outputs["Roughness"], bsdf.inputs["Roughness"])
    nt.links.new(grp.outputs["Metallic"], bsdf.inputs["Metallic"])
    nt.links.new(bsdf.outputs["BSDF"], out.inputs["Surface"])
    return mat


def surface_material(mat_name):
    """A single-surface wrapper (S_SurfaceMaster). See _wrapper_material."""
    return _wrapper_material(mat_name, surface_master_group())


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

    # Weight = all masks, then Enable gates the layer out entirely.
    w = _mmath(g, "MULTIPLY", slope, alt, (x0 + 1040, y))
    w = _mmath(g, "MULTIPLY", w, noise, (x0 + 1210, y))
    w = _mmath(g, "MULTIPLY", w, paint, (x0 + 1380, y))
    w = _mmath(g, "MULTIPLY", w, curv, (x0 + 1550, y))

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
    return I[p + "Base Color"], I[p + "Roughness"], I[p + "Metallic"], H, I[p + "Enable"]


def terrain_master_group():
    """The multi-layer terrain blend, ending in S_Weather. One shared group, MAX_TERRAIN_LAYERS
    fixed slots; the stack is the enabled ones. See the module comment for the height-lerp."""
    g = bpy.data.node_groups.get(TERRAIN_MASTER)
    if g is not None:
        return g
    g = bpy.data.node_groups.new(TERRAIN_MASTER, "ShaderNodeTree")

    _gin(g, "Blend Softness", "NodeSocketFloat", 0.15, 0.001, 1.0)
    _gin(g, "Macro Amount", "NodeSocketFloat", 0.15, 0.0, 1.0)
    _gin(g, "Macro Scale", "NodeSocketFloat", 0.3, 0.0)
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
    # Weather passthrough (identical to S_Weather's inputs).
    _gin(g, "Snow Strength", "NodeSocketFloat", 1.0, 0.0, 1.0)
    _gin(g, "Use Attribute", "NodeSocketFloat", 1.0, 0.0, 1.0)  # terrain carries the pass
    _gin(g, "Slope Threshold", "NodeSocketFloat", 0.5, 0.0, 1.0)
    _gin(g, "Slope Falloff", "NodeSocketFloat", 0.2, 0.0, 1.0)
    _gin(g, "Altitude", "NodeSocketFloat", 0.0)
    _gin(g, "Altitude Falloff", "NodeSocketFloat", 5.0, 0.0)
    _gout(g, "Base Color", "NodeSocketColor")
    _gout(g, "Roughness", "NodeSocketFloat")
    _gout(g, "Metallic", "NodeSocketFloat")

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
    acc_col, acc_rough, acc_metal, acc_H, _e0 = layers[0]
    fx = 700
    for i in range(1, MAX_TERRAIN_LAYERS):
        col, rough, metal, H, enable = layers[i]
        fy = -i * 300
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
        acc_H = _mmath(g, "MAXIMUM", acc_H, H, (fx + 1360, fy - 400))
        fx += 1600

    # End in S_Weather: the blended base + the snow passthrough.
    weather = g.nodes.new("ShaderNodeGroup")
    weather.node_tree = weather_group()
    weather.location = (fx + 200, 0)
    g.links.new(acc_col, weather.inputs["Base Color"])
    g.links.new(acc_rough, weather.inputs["Roughness"])
    g.links.new(acc_metal, weather.inputs["Metallic"])
    for name in ("Snow Strength", "Use Attribute", "Slope Threshold", "Slope Falloff",
                 "Altitude", "Altitude Falloff"):
        g.links.new(I[name], weather.inputs[name])
    for name in ("Base Color", "Roughness", "Metallic"):
        g.links.new(weather.outputs[name], go.inputs[name])
    return g


def terrain_material(mat_name):
    """A multi-layer terrain wrapper (S_TerrainMaster). See _wrapper_material."""
    return _wrapper_material(mat_name, terrain_master_group())


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
