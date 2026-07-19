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

# The instance attributes the volumetrics recipe stores, and the socket each maps
# from. Read here as INSTANCER-domain float attributes.
_KNOBS = ("cloud_density", "cloud_detail", "cloud_softness",
          "cloud_coverage", "cloud_scale", "cloud_seed")


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


# The fog instance attributes the volumetrics recipe stores (fog modes), and the
# socket each maps from. Read here as INSTANCER-domain float attributes.
_FOG_KNOBS = ("fog_density", "fog_top", "fog_noise", "fog_scale",
              "fog_detail", "fog_seed", "fog_softness")


def fog_volume_material():
    """A cached thin Principled Volume for a single GN-instanced fog domain.

    Fog is a different primitive from cloud, so it gets its own material rather
    than reusing BOB_CloudVolume. Where the cloud material carves shapes out of a
    box with a Coverage threshold and a symmetric all-face envelope, fog is a
    continuous medium with a vertical density gradient: densest at the bottom of
    the box, fading to zero at Fog Top (a fraction of the box height). That Z
    gradient IS the height-fog / aerial-perspective look, and it doubles as the
    top-face fade, so no separate top envelope is needed. Because the gradient is
    anchored to the box (which sits at a fixed world Z), valleys below the fog top
    fill and hills above it poke out: crude terrain-aware pooling with no emitter
    sampling.

    Both fog modes share this material. height_fog sets Fog Noise low (a nearly
    uniform slab); noise_fog sets it high, so the density is broken into soft
    patchy banks. The noise sample is offset by fog_wind (a per-instance vector the
    recipe advances by wind * scene time), so the banks drift like the clouds do.

    Knobs (fog_density, fog_top, fog_noise, fog_scale, fog_detail, fog_seed,
    fog_softness) arrive as INSTANCER attributes, the same linchpin path clouds use.
    """
    mat = bpy.data.materials.get(FOG_MATERIAL)
    if mat is not None:
        return mat
    mat = bpy.data.materials.new(FOG_MATERIAL)
    mat.use_nodes = True
    nt = mat.node_tree
    nt.nodes.clear()
    L = nt.links

    out = nt.nodes.new("ShaderNodeOutputMaterial")
    out.location = (900, 0)
    vol = nt.nodes.new("ShaderNodeVolumePrincipled")
    vol.location = (680, 0)
    vol.inputs["Color"].default_value = (1.0, 1.0, 1.0, 1.0)
    vol.inputs["Anisotropy"].default_value = 0.0  # fog scatters near-uniformly

    density_a = _attr(nt, "fog_density", (-1200, 40))
    top_a = _attr(nt, "fog_top", (-1200, -60))
    noise_a = _attr(nt, "fog_noise", (-1200, -160))
    scale_a = _attr(nt, "fog_scale", (-1200, -260))
    detail_a = _attr(nt, "fog_detail", (-1200, -360))
    seed_a = _attr(nt, "fog_seed", (-1200, -460))
    soft_a = _attr(nt, "fog_softness", (-1200, -560))

    # Patchiness: world-space fractal noise, drifted by the per-instance wind. For
    # noise_fog this breaks the slab into banks; for height_fog Fog Noise is low so
    # the slab stays nearly uniform.
    geo = nt.nodes.new("ShaderNodeNewGeometry")
    geo.location = (-1200, 320)
    wind_node = nt.nodes.new("ShaderNodeAttribute")
    wind_node.attribute_type = "INSTANCER"
    wind_node.attribute_name = "fog_wind"
    wind_node.location = (-1200, 460)
    drifted = nt.nodes.new("ShaderNodeVectorMath")
    drifted.operation = "SUBTRACT"
    drifted.location = (-1000, 380)
    L.new(geo.outputs["Position"], drifted.inputs[0])
    L.new(wind_node.outputs["Vector"], drifted.inputs[1])

    noise = nt.nodes.new("ShaderNodeTexNoise")
    noise.noise_dimensions = "4D"
    noise.location = (-800, 300)
    noise.inputs["Roughness"].default_value = 0.55
    L.new(drifted.outputs["Vector"], noise.inputs["Vector"])
    L.new(scale_a, noise.inputs["Scale"])
    L.new(detail_a, noise.inputs["Detail"])
    L.new(seed_a, noise.inputs["W"])

    # Soft banks: threshold the noise at 0.5 with a width from Softness, so gaps
    # open between the banks. noise_mod = mix(1, bank, Fog Noise): at Fog Noise 0
    # the slab is uniform, at 1 it is fully the banked pattern.
    soft_w = nt.nodes.new("ShaderNodeMath")
    soft_w.operation = "MULTIPLY_ADD"
    soft_w.location = (-620, 60)
    soft_w.inputs[1].default_value = 0.5   # Softness scales the half-width
    soft_w.inputs[2].default_value = 0.05  # keep the range non-degenerate
    L.new(soft_a, soft_w.inputs[0])
    bank_min = nt.nodes.new("ShaderNodeMath")
    bank_min.operation = "SUBTRACT"
    bank_min.location = (-440, 120)
    bank_min.inputs[0].default_value = 0.5
    L.new(soft_w.outputs["Value"], bank_min.inputs[1])
    bank_max = nt.nodes.new("ShaderNodeMath")
    bank_max.operation = "ADD"
    bank_max.location = (-440, 20)
    bank_max.inputs[0].default_value = 0.5
    L.new(soft_w.outputs["Value"], bank_max.inputs[1])
    bank = nt.nodes.new("ShaderNodeMapRange")
    bank.interpolation_type = "SMOOTHSTEP"
    bank.location = (-260, 120)
    L.new(noise.outputs["Fac"], bank.inputs["Value"])
    L.new(bank_min.outputs["Value"], bank.inputs["From Min"])
    L.new(bank_max.outputs["Value"], bank.inputs["From Max"])
    banked = nt.nodes.new("ShaderNodeMath")
    banked.operation = "MULTIPLY"
    banked.location = (-80, 120)
    L.new(noise_a, banked.inputs[0])
    L.new(bank.outputs["Result"], banked.inputs[1])
    uniform = nt.nodes.new("ShaderNodeMath")
    uniform.operation = "SUBTRACT"
    uniform.location = (-80, 220)
    uniform.inputs[0].default_value = 1.0
    L.new(noise_a, uniform.inputs[1])
    noise_mod = nt.nodes.new("ShaderNodeMath")
    noise_mod.operation = "ADD"
    noise_mod.location = (100, 180)
    L.new(banked.outputs["Value"], noise_mod.inputs[0])
    L.new(uniform.outputs["Value"], noise_mod.inputs[1])

    # Height gradient + XY-wall envelope from the box's own Generated 0..1 coords.
    # Generated Z is 0 at the box bottom and 1 at the top (the box is not rotated,
    # so this tracks world Z). Fog is densest at z = 0 and fades to zero at Fog Top,
    # so density falls with height (aerial perspective) and the top face fades on
    # its own. The XY walls fade over a small margin so fog does not cut off there.
    texco = nt.nodes.new("ShaderNodeTexCoord")
    texco.location = (-1200, -720)
    sep = nt.nodes.new("ShaderNodeSeparateXYZ")
    sep.location = (-1000, -720)
    L.new(texco.outputs["Generated"], sep.inputs[0])

    profile = nt.nodes.new("ShaderNodeMapRange")
    profile.interpolation_type = "SMOOTHSTEP"
    profile.location = (-800, -620)
    profile.inputs["From Min"].default_value = 0.0
    profile.inputs["To Min"].default_value = 1.0  # dense at the bottom
    profile.inputs["To Max"].default_value = 0.0  # clear at Fog Top and above
    L.new(sep.outputs["Z"], profile.inputs["Value"])
    L.new(top_a, profile.inputs["From Max"])

    # XY distance to the nearest side wall: 0.5 - max(|x-0.5|, |y-0.5|).
    cx = nt.nodes.new("ShaderNodeMath")
    cx.operation = "SUBTRACT"
    cx.location = (-800, -820)
    L.new(sep.outputs["X"], cx.inputs[0])
    cx.inputs[1].default_value = 0.5
    ax = nt.nodes.new("ShaderNodeMath")
    ax.operation = "ABSOLUTE"
    ax.location = (-620, -820)
    L.new(cx.outputs["Value"], ax.inputs[0])
    cy = nt.nodes.new("ShaderNodeMath")
    cy.operation = "SUBTRACT"
    cy.location = (-800, -920)
    L.new(sep.outputs["Y"], cy.inputs[0])
    cy.inputs[1].default_value = 0.5
    ay = nt.nodes.new("ShaderNodeMath")
    ay.operation = "ABSOLUTE"
    ay.location = (-620, -920)
    L.new(cy.outputs["Value"], ay.inputs[0])
    mxy = nt.nodes.new("ShaderNodeMath")
    mxy.operation = "MAXIMUM"
    mxy.location = (-440, -860)
    L.new(ax.outputs["Value"], mxy.inputs[0])
    L.new(ay.outputs["Value"], mxy.inputs[1])
    wall_dist = nt.nodes.new("ShaderNodeMath")
    wall_dist.operation = "SUBTRACT"
    wall_dist.location = (-260, -860)
    wall_dist.inputs[0].default_value = 0.5
    L.new(mxy.outputs["Value"], wall_dist.inputs[1])
    env = nt.nodes.new("ShaderNodeMapRange")
    env.interpolation_type = "SMOOTHSTEP"
    env.location = (-80, -860)
    env.inputs["From Min"].default_value = 0.0
    env.inputs["From Max"].default_value = 0.1  # fade over 10% of the half-extent
    L.new(wall_dist.outputs["Value"], env.inputs["Value"])

    # density = fog_density * height profile * noise_mod * XY envelope.
    d1 = nt.nodes.new("ShaderNodeMath")
    d1.operation = "MULTIPLY"
    d1.location = (300, 0)
    L.new(profile.outputs["Result"], d1.inputs[0])
    L.new(noise_mod.outputs["Value"], d1.inputs[1])
    d2 = nt.nodes.new("ShaderNodeMath")
    d2.operation = "MULTIPLY"
    d2.location = (460, 0)
    L.new(d1.outputs["Value"], d2.inputs[0])
    L.new(env.outputs["Result"], d2.inputs[1])
    density = nt.nodes.new("ShaderNodeMath")
    density.operation = "MULTIPLY"
    density.location = (620, -140)
    L.new(d2.outputs["Value"], density.inputs[0])
    L.new(density_a, density.inputs[1])
    L.new(density.outputs["Value"], vol.inputs["Density"])
    L.new(vol.outputs["Volume"], out.inputs["Volume"])
    return mat
