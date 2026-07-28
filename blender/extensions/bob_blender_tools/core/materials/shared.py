"""Shared shader primitives for the BobShader system: node-graph helpers, the cached
group-versioning infra, the thin wrapper builder, the master-identity readers, and the
Set-Material GN glue. The bottom layer -- imports nothing from the other materials
submodules."""

import os

import bpy




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

SURFACE_MASTER = "S_SurfaceMaster"

SURFACE_WRAPPER_PREFIX = "M_"



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



# Bump when any shared S_* group's interface (sockets) changes. A group cached in an older .blend
# carries a stale (or absent) stamp and is rebuilt in place on first access, so an addon upgrade
# refreshes the interface instead of reusing a group that lacks new sockets -- which otherwise
# KeyErrors when the caller wires them, or silently renders the old behaviour. v2 (BobSplines, the
# water channel): the water master (S_WaterMaster) landed and S_TerrainMaster gained the
# bbt_curve_wet damp-bed read, so existing cached groups must rebuild to pick both up. v3 (water
# look pass the water look pass): S_WaterMaster gained multi-scale flow waves, crisp/shore foam, and
# a manual Frozen -> ice path (new Shore Foam / Foam Crispness / Wave Detail / Frozen sockets), so a
# cached v2 water group must rebuild to expose them. v4 (item-3 weather fix): S_Weather gained a
# Canopy Snow input and S_SurfaceMaster exposes it, so the shared weather interface changed. Every
# group that EMBEDS S_Weather (S_SurfaceMaster, S_TerrainMaster, and S_WaterMaster via its own
# override) must rebuild together: an in-place rebuild gives the weather sockets new identifiers,
# and an embedder left un-rebuilt keeps stale links to them (verified: terrain's 16 weather links
# drop to 0). A global bump rebuilds all of them consistently, at the known cost of resetting tuned
# terrain/surface inputs on upgrade. v5 (snow-line unify): S_Weather dropped the Use Attribute
# switch AND the per-material Altitude / Altitude Falloff knobs; it now computes coverage from a
# normalized env snow line, scaled to the terrain's Z bounds, on every surface. S_EnvState gained
# Snow Line + Snow Line Top outputs (world Z) and its Snow output is temperature-driven (no amount
# slider; below freezing snows, colder is thicker). The material reads a snow_occlusion attribute
# instead of snow_cover. The weather and env-state interfaces both changed, so every embedder
# (S_SurfaceMaster, S_TerrainMaster, S_WaterMaster) must rebuild in lockstep.
S_GROUP_VER = 6


# Per-group version overrides. A rebuild clears the interface, which RESETS the tuned inputs of
# every material instancing the group (the new sockets get fresh identifiers, verified), so a global
# S_GROUP_VER bump wipes terrain/surface tuning too. When a change is scoped to ONE group, version
# it here instead so only that group rebuilds and the rest keep their tuned values. (The string keys
# are the group names, == the WATER_MASTER etc. constants defined below.)
#   S_WaterMaster v4: geometry Gerstner waves in curve_water made the shader's low-frequency flow
#   bump redundant -- it now carries only a subtle high-frequency detail normal (the old one combed
#   into hair-like streaks). Graph + default change, same interface, so water-only rebuild.
#   S_WaterMaster v5: depth interaction. New Depth Absorption / Depth Opacity / Shoreline Fade
#   sockets; reads the ribbon's bbt_depth (water-column metres) for Beer-Lambert colour + opacity and
#   a soft shoreline. New interface, so rebuild the water group (terrain/surface tuning untouched).
#   S_WaterMaster v7: it embeds S_Weather, whose interface changed in the item-3 weather fix (the
#   Canopy Snow term). Bumped in lockstep with the global S_GROUP_VER v4 so the water group rebuilds
#   and re-links the weather node rather than keeping stale socket links. (v6 was the depth pass.)
#   S_WaterMaster v8: S_Weather's interface changed again in the snow-line unify (Use Attribute
#   dropped). Bumped in lockstep with the global S_GROUP_VER v5 so the water group rebuilds and
#   re-links the weather node rather than keeping stale links.
#   S_WaterMaster v9: S_EnvState gained a Frost output (the artist frost dial). The water master
#   embeds S_EnvState directly (its freeze path reads Temperature/Cloud/Wind/Frost), so its internal
#   links to that group's outputs go stale when the group interface is rebuilt. Bumped in lockstep
#   with the global S_GROUP_VER v6 so the water group rebuilds and re-links.
#   S_LeafSeason v1 (BobFoliage, the season layer): the leaf-card season layer. Versioned on its own from the
#   start for the same reason S_TexSet is -- it embeds neither S_Weather nor S_EnvState, so it never
#   needs the lockstep rebuild the masters do, and a future change to it must not cost every tuned
#   terrain in the file a revert-to-default. It EXISTS at all rather than being a Season output on
#   S_EnvState precisely to keep that cost off the masters (materials.LEAF_SEASON). The leaf wrapper
#   folds this number into its signature, so bumping it here still rebuilds the cards that use it.
#   S_TexSet v1 (BobShaders, the texture-set sampler): the texture-set sampler. Versioned on its own from the start,
#   because it embeds neither S_Weather nor S_EnvState, so it never needs the lockstep rebuild the
#   masters do -- and a future change to its interface must not cost every tuned terrain in the
#   file a revert-to-default. The wrappers that instance it fold this number into their signature
#   (texset.sig_part), so bumping it here still rebuilds them.
_GROUP_VER_OVERRIDE = {"S_WaterMaster": 9, "S_TexSet": 1, "S_LeafSeason": 1}



def group_version(name):
    """The version a shared group is stamped with: its own override if it has one, else the global.
    The one reader of the override table besides `_cached_group`, so a wrapper that must rebuild
    when a group it instances changes can fold the number into its signature."""
    return _GROUP_VER_OVERRIDE.get(name, S_GROUP_VER)



def _cached_group(name):
    """Get-or-create a version-stamped shared shader group. Returns (group, needs_build);
    when needs_build is False the cached group is current and the caller returns it as-is.
    A stale group is rebuilt in place (datablock kept, nodes + interface cleared) so
    materials already referencing it pick up the fresh interface rather than dangling. The
    expected version is the per-group override if present, else the shared S_GROUP_VER."""
    want = group_version(name)
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



# Principled inputs a master group may drive BEYOND Base Color / Roughness / Metallic (the water
# master, the water look). Wired only when the master exposes the matching OUTPUT, so surface and
# terrain masters (which do not) are byte-identical. The Principled transmission socket was renamed
# across Blender versions (Transmission -> Transmission Weight in 4.x), so each master output maps
# to a list of candidate BSDF socket names and the first that exists wins.
_WRAPPER_EXTRA_OUTPUTS = (
    ("Transmission", ("Transmission Weight", "Transmission")),
    ("IOR", ("IOR",)),
    ("Alpha", ("Alpha",)),
    ("Normal", ("Normal",)),
)



def _wrapper_name(mat_name):
    """The wrapper material datablock name for a base name: M_ prefixed, idempotent. The one
    place the prefix rule lives, so a caller that needs to READ a wrapper before rebuilding it
    (the texture-set assignment, which carries forward what the material already had) resolves
    the same name _build_wrapper will write."""
    return mat_name if mat_name.startswith(SURFACE_WRAPPER_PREFIX) else SURFACE_WRAPPER_PREFIX + mat_name


# Nodes a wire() adds are named with this prefix so their tuned inputs survive a structural
# rebuild. Assigning a texture set to a SECOND terrain layer changes the signature and therefore
# rebuilds the whole wrapper, which would otherwise reset the FIRST layer's tiling scale, AO
# amount, and bump strength. The Master node keeps its own snapshot path (below), which carries
# the extra version-bump re-seed semantics; this is the plain one.
TEXSET_NODE_PREFIX = "TexSet "


def _snapshot_wire_nodes(nt):
    """Snapshot the unlinked input default_values of the TEXSET_NODE_PREFIX nodes in a wrapper
    tree, as {node name: {socket name: value}}. Linked sockets are skipped: their value is the
    upstream node, which wire() rebuilds anyway."""
    out = {}
    if nt is None:
        return out
    for n in nt.nodes:
        if not n.name.startswith(TEXSET_NODE_PREFIX):
            continue
        vals = {}
        for s in n.inputs:
            if s.is_linked:
                continue
            try:
                v = s.default_value
            except (AttributeError, TypeError):
                continue
            if hasattr(v, "__len__") and not isinstance(v, str):
                v = tuple(v)
            vals[s.name] = v
        if vals:
            out[n.name] = vals
    return out


def _restore_wire_nodes(nt, snap):
    """Restore a _snapshot_wire_nodes snapshot onto the freshly built nodes, by node and socket
    name. A node the rebuild did not recreate (its texture set was cleared) is simply absent."""
    for node_name, vals in snap.items():
        node = nt.nodes.get(node_name)
        if node is None:
            continue
        for s in node.inputs:
            if s.name not in vals or s.is_linked:
                continue
            try:
                s.default_value = vals[s.name]
            except (AttributeError, TypeError, ValueError):
                pass


def _build_wrapper(mat_name, master, sig, wire):
    """Build (or rebuild) a thin wrapper material: one master group node ("Master") feeding
    one Principled BSDF and the Output. `sig` is a structure signature stored on the material;
    if it is unchanged and the Master is wired, the wrapper is returned untouched so tuned
    inputs survive. On a structural change (a texture set assigned or changed) it rebuilds,
    snapshotting and restoring the Master's tuned inputs by socket name (the shader analogue
    of the GN modifier snapshot). `wire(nt, grp, bsdf, old_sig)` adds any texture-set nodes."""
    name = _wrapper_name(mat_name)
    mat = bpy.data.materials.get(name)
    master_ver = master.get("bbt_ver")
    old_node = None
    if mat is not None and mat.use_nodes and mat.node_tree is not None:
        old_node = mat.node_tree.nodes.get("Master")
        if old_node is not None and old_node.type == "GROUP" \
                and old_node.node_tree is master and mat.get("bbt_sig") == sig:
            # Structure unchanged, so tuned inputs are kept -- but if the shared master group was
# rebuilt in place (version bump), this instance's sockets were left at type-zero.
# Re-seed them to the new interface defaults so an upgrade costs a revert-to-default,
# not a black base layer / silently-off snow. No-op when the master version is
# unchanged.
            if mat.get("bbt_master_ver") != master_ver:
                _seed_inputs_from_interface(old_node)
                mat["bbt_master_ver"] = master_ver
            return mat  # unchanged; keep tuned inputs
    old_sig = mat.get("bbt_sig") if mat is not None else None
    prev_master_ver = mat.get("bbt_master_ver") if mat is not None else None
    snap = _snapshot_group_inputs(old_node)
    wire_snap = _snapshot_wire_nodes(mat.node_tree) if mat is not None and mat.use_nodes else {}
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
    # Water master (the water look): also drive Transmission / IOR / Alpha / Normal when the master
# exposes them. A no-op for surface / terrain masters (their groups carry no such outputs).
    for out_name, candidates in _WRAPPER_EXTRA_OUTPUTS:
        src = grp.outputs.get(out_name)
        if src is None:
            continue
        target = next((bsdf.inputs.get(c) for c in candidates if bsdf.inputs.get(c) is not None), None)
        if target is not None:
            nt.links.new(src, target)
    nt.links.new(bsdf.outputs["BSDF"], out.inputs["Surface"])
    _restore_group_inputs(grp, snap)
    # A version bump zeroed the old instance sockets before this rebuild, so the snapshot restored
    # zeros, not tuned values. Fall back to the fresh interface defaults in that case (defaults beat
    # type-zero). On a plain structural change (same master version) the snapshot holds real values,
    # so leave them alone.
    if prev_master_ver is not None and prev_master_ver != master_ver:
        _seed_inputs_from_interface(grp)
    if wire is not None:
        wire(nt, grp, bsdf, old_sig)
        _restore_wire_nodes(nt, wire_snap)
    mat["bbt_sig"] = sig
    mat["bbt_master_ver"] = master_ver
    return mat



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



# BobShaders water master (S_WaterMaster, BobSplines, the water channel.3). The third BobShader
# kind, for the river ribbons curve_water lays in a carved channel. It reads the ribbon's baked
# bbt_flow / bbt_foam / bbt_shore attributes and produces a flowing, depth-tinted, foaming,
# transparent surface that freezes to ice below 0 C. Like the other masters it ends in S_Weather, so
# it inherits the shared wetness/frost/snow layer and the live env feed; the freeze reuses that
# below-freezing frost path rather than adding a new system. Beyond Base Color/Roughness/Metallic it
# also outputs Transmission, IOR, Normal, and Alpha, which the widened _build_wrapper drives into
# the Principled BSDF.
WATER_MASTER = "S_WaterMaster"



# The BobShaders terrain master. S_TerrainMaster blends an ordered stack of surface layers
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



def _lerp(g, a, b, f, loc):
    """a + (b - a) * f."""
    d = _mmath(g, "SUBTRACT", b, a, loc)
    df = _mmath(g, "MULTIPLY", d, f, (loc[0], loc[1] - 150))
    return _mmath(g, "ADD", a, df, (loc[0] + 170, loc[1] - 70))



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



def _seed_inputs_from_interface(node):
    """Reset a group node's instance input sockets to the group interface's own default_value.

    A version-stamped rebuild (_cached_group) clears and repopulates the group interface, which
    leaves the sockets of every EXISTING instance node at type-zero (0.0 / black), not at the new
    interface default. Type-zero silently disables terms whose real default is nonzero (Snow
    Strength 1.0 -> 0.0 turns snow off, an Enable 1.0 -> 0.0 blanks a base layer). Re-seeding from
    the interface restores the documented upgrade cost (tuned values revert to DEFAULT) instead of
    the undocumented one (they revert to zero)."""
    if node is None or node.node_tree is None:
        return
    for item in node.node_tree.interface.items_tree:
        if getattr(item, "item_type", "") != "SOCKET" or getattr(item, "in_out", "") != "INPUT":
            continue
        if not hasattr(item, "default_value"):
            continue
        sock = node.inputs.get(item.name)
        if sock is None or not hasattr(sock, "default_value"):
            continue
        try:
            sock.default_value = item.default_value
        except (AttributeError, TypeError, ValueError):
            pass
