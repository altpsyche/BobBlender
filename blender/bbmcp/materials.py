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
