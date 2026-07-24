"""Low-level Geometry Nodes group plumbing shared by every recipe."""

import bpy


def new_group(name: str):
    """A fresh GeometryNodeTree with a Geometry output and a Group Output node."""
    ng = bpy.data.node_groups.new(name, "GeometryNodeTree")
    ng.interface.new_socket("Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")
    out = ng.nodes.new("NodeGroupOutput")
    out.location = (900, 0)
    return ng, out


def add_input(ng, name, socket_type, default=None, min_value=None, max_value=None):
    """Add a group input socket and return the interface item.

    default is None for datablock sockets (Object, Collection), which have no
    meaningful interface default; those are bound on the modifier instead.
    min_value / max_value clamp the knob's slider when given.
    """
    socket = ng.interface.new_socket(name, in_out="INPUT", socket_type=socket_type)
    if default is not None:
        socket.default_value = default
    if min_value is not None:
        socket.min_value = min_value
    if max_value is not None:
        socket.max_value = max_value
    return socket
