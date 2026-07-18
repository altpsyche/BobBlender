"""scatter: distribute a collection of assets across an emitter surface.

A GScatter-style layer. Reads the Emitter object's geometry, distributes points
with Poisson disk sampling, filters by slope, and instances a random pick from
the Assets collection with per-instance random scale and Z rotation, aligned to
the surface normal.

Modifier inputs (editable knobs): Density, Distance Min, Seed, Min Scale,
Max Scale, Min Normal Z (slope cutoff, 1 = flat only).

The emitter object and the asset collection are set on the nodes (Blender 5.x
GN modifiers no longer store object or collection inputs). Params: emitter (an
object name) and assets (a collection name). Replace assets by editing that
collection's contents or repointing the Collection Info node.
"""

import bpy

from ..blocks import math_node, object_geometry, random_value
from ..scaffold import add_input
from . import recipe

TAU = 6.283185307179586


@recipe("scatter")
def build(ng, out, params: dict):
    emitter = bpy.data.objects.get(params.get("emitter", ""))
    assets = bpy.data.collections.get(params.get("assets", ""))

    gi = ng.nodes.new("NodeGroupInput")
    gi.location = (-1100, 0)

    add_input(ng, "Density", "NodeSocketFloat", float(params.get("density", 5.0)), 0.0)
    add_input(ng, "Distance Min", "NodeSocketFloat", float(params.get("distance_min", 0.3)), 0.0)
    add_input(ng, "Seed", "NodeSocketInt", int(params.get("seed", 0)))
    add_input(ng, "Min Scale", "NodeSocketFloat", float(params.get("min_scale", 0.8)), 0.0)
    add_input(ng, "Max Scale", "NodeSocketFloat", float(params.get("max_scale", 1.2)), 0.0)
    add_input(ng, "Min Normal Z", "NodeSocketFloat", float(params.get("min_normal_z", 0.5)))

    nodes, links = ng.nodes, ng.links
    seed = gi.outputs["Seed"]

    geometry = object_geometry(ng, emitter, (-900, 200))

    # Slope filter: keep faces whose upward normal exceeds Min Normal Z.
    normal = nodes.new("GeometryNodeInputNormal")
    normal.location = (-900, -200)
    sep = nodes.new("ShaderNodeSeparateXYZ")
    sep.location = (-720, -200)
    links.new(normal.outputs["Normal"], sep.inputs[0])
    selection = math_node(ng, "GREATER_THAN", sep.outputs["Z"], gi.outputs["Min Normal Z"], (-540, -200))

    # Poisson distribution.
    dist = nodes.new("GeometryNodeDistributePointsOnFaces")
    dist.distribute_method = "POISSON"
    dist.location = (-320, 100)
    links.new(geometry, dist.inputs["Mesh"])
    links.new(selection, dist.inputs["Selection"])
    links.new(gi.outputs["Distance Min"], dist.inputs["Distance Min"])
    links.new(gi.outputs["Density"], dist.inputs["Density Max"])
    links.new(seed, dist.inputs["Seed"])

    # Asset instances from the collection, one separate child per instance.
    coll = nodes.new("GeometryNodeCollectionInfo")
    coll.location = (-320, -200)
    if assets is not None:
        coll.inputs["Collection"].default_value = assets
    coll.inputs["Separate Children"].default_value = True
    coll.inputs["Reset Children"].default_value = True

    # Random pick index in [0, instance_count - 1].
    domain = nodes.new("GeometryNodeAttributeDomainSize")
    domain.component = "INSTANCES"
    domain.location = (-140, -360)
    links.new(coll.outputs["Instances"], domain.inputs["Geometry"])
    max_index = math_node(ng, "SUBTRACT", domain.outputs["Instance Count"], 1, (40, -360))
    index = random_value(ng, "INT", 0, max_index, seed, (220, -360))

    scale = random_value(ng, "FLOAT", gi.outputs["Min Scale"], gi.outputs["Max Scale"], seed, (0, 300))

    instance = nodes.new("GeometryNodeInstanceOnPoints")
    instance.location = (120, 100)
    links.new(dist.outputs["Points"], instance.inputs["Points"])
    links.new(coll.outputs["Instances"], instance.inputs["Instance"])
    instance.inputs["Pick Instance"].default_value = True
    links.new(index, instance.inputs["Instance Index"])
    # align "normal" tilts instances to the surface (rocks, grass); "up" leaves
    # them standing (trees). Random Z spin is added below either way.
    if params.get("align", "up") == "normal":
        links.new(dist.outputs["Rotation"], instance.inputs["Rotation"])
    links.new(scale, instance.inputs["Scale"])

    # Random spin about Z, in the instance's local space.
    spin = random_value(ng, "FLOAT", 0.0, TAU, seed, (120, 380))
    spin_vec = nodes.new("ShaderNodeCombineXYZ")
    spin_vec.location = (300, 380)
    links.new(spin, spin_vec.inputs["Z"])
    rotate = nodes.new("GeometryNodeRotateInstances")
    rotate.location = (480, 100)
    links.new(instance.outputs["Instances"], rotate.inputs["Instances"])
    links.new(spin_vec.outputs["Vector"], rotate.inputs["Rotation"])
    rotate.inputs["Local Space"].default_value = True

    links.new(rotate.outputs["Instances"], out.inputs["Geometry"])
