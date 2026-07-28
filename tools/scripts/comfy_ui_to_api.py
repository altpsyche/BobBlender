"""Convert a ComfyUI GUI-format workflow into the API format Bob's shipped graphs use.

Bob's workflows are DERIVED from shipped templates rather than authored, so an upstream change is a
diff rather than archaeology (docs/GENERATION.md, the derivation rule). And
the templates ship in GUI format: a `nodes` / `links` graph carrying positions, sizes, and an
ordered `widgets_values` list per node. The API format the `/prompt` endpoint takes is a flat
`{node_id: {"class_type", "inputs", "_meta"}}` dict with every widget named. This script is that
conversion, so deriving a graph is a repeatable step instead of a hand-transcription.

Widget names are NOT in the GUI file. A GUI node's `inputs` array lists only its LINK sockets
(plus any widget the author converted into a socket, which carries a `widget` key). So the names
come from the server's `/object_info`, in `input_order`, filtered to the widget types, with three
rules, and the second and third exist because the TRELLIS.2 nodes break the first:

1. A widget declaring `control_after_generate` is followed in `widgets_values` by an extra control
   entry that has no API counterpart and is skipped.
2. `control_after_generate` is NOT always declared. The frontend adds the control widget to any
   INT named `seed`, whether or not the schema says so, and none of the TRELLIS.2 samplers
   declares it. So the extra entry is detected by VALUE (`fixed` / `increment` / `decrement` /
   `randomize`) as well as by declaration; without this, every widget after a seed is off by one
   and the graph queues with a string where a float belongs.
3. A `COMFY_DYNAMICCOMBO_V3` widget is a key plus the inputs of the branch that key selects. Its
   value is the key; the selected branch's sub-widgets follow it inline in `widgets_values` and
   are named `<field>.<sub>` in the API. The branch's own `input_order` gives their order. The
   flat `<field>.<sub>` entries `/object_info` lists at the END of `input_order` are the union of
   every branch, so reading them in that order is wrong twice over.

    python3 tools/scripts/comfy_ui_to_api.py <workflow.json|workflow.png> [--out api.json]
        [--url http://127.0.0.1:8188] [--object-info cached.json]

A PNG input is read for its embedded `workflow` text chunk, which is how a pack ships a reference
graph (the seamless-tiling pack's `tiled_workflow.png` is where `tex_tileable` comes from).

The result is the bare API prompt dict. Bob's shipped files wrap it as
`{"_bob": {provenance}, "prompt": {...}}`; adding that wrapper, the `_meta.title` templating
markers, and any node swap is a deliberate edit on top of this conversion, recorded in `_bob`.
"""

import argparse
import json
import os
import struct
import sys
import urllib.request
import zlib

# Input types that are widgets rather than link sockets. A COMBO arrives as a list of options (or,
# on newer servers, as the literal string "COMBO"); everything else that is not in this set is a
# link type (MODEL, CLIP, VAE, LATENT, IMAGE, CONDITIONING, ...).
_WIDGET_TYPES = {"INT", "FLOAT", "STRING", "BOOLEAN", "COMBO"}

# GUI-only nodes with no API counterpart: they are annotations or reroutes, not work.
_SKIP_TYPES = {"Note", "MarkdownNote", "Reroute", "PrimitiveNode"}

# A dynamic combo: one key widget whose selected option contributes further widgets inline.
_DYNAMIC_COMBO = "COMFY_DYNAMICCOMBO_V3"

# The values a `control_after_generate` widget can hold. Matching on these is how an UNDECLARED
# control entry is found, which is the only way to stay aligned on the TRELLIS.2 samplers.
_CONTROL_VALUES = {"fixed", "increment", "decrement", "randomize"}


def png_workflow(path):
    """The `workflow` text chunk embedded in a ComfyUI-saved PNG, as a dict."""
    data = open(path, "rb").read()
    i = 8
    while i + 12 <= len(data):
        length = struct.unpack(">I", data[i:i + 4])[0]
        kind = data[i + 4:i + 8]
        body = data[i + 8:i + 8 + length]
        i += 12 + length
        if kind not in (b"tEXt", b"zTXt", b"iTXt"):
            continue
        key, _, rest = body.partition(b"\0")
        if key.decode("latin1") != "workflow":
            continue
        if kind == b"zTXt":
            rest = zlib.decompress(rest[1:])
        return json.loads(rest.decode("utf-8", "replace"))
    raise ValueError(f"no embedded workflow chunk in {path}")


def load_graph(path):
    """The GUI graph dict from a .json or a ComfyUI-saved .png."""
    if path.lower().endswith(".png"):
        return png_workflow(path)
    with open(path) as fh:
        return json.load(fh)


def load_object_info(url, cached):
    if cached:
        with open(cached) as fh:
            return json.load(fh)
    with urllib.request.urlopen(url.rstrip("/") + "/object_info", timeout=60) as resp:
        return json.load(resp)


def _fields_in_order(spec, order, sections=("required", "optional")):
    """[(name, type, opts), ...] over an input spec, in the declared widget order."""
    out = []
    for section in sections:
        fields = spec.get(section) or {}
        for name in (order.get(section) or list(fields)):
            entry = fields.get(name)
            if isinstance(entry, (list, tuple)) and entry:
                out.append((name, entry[0], entry[1] if len(entry) > 1 else {}))
    return out


def widget_names(schema):
    """[(name, type, opts), ...] for a class's widgets, in widgets_values order.

    A dynamic combo is returned as-is; `assign_widgets` expands it, because which sub-widgets
    follow depends on the VALUE the combo holds and the schema alone cannot say.
    """
    order = schema.get("input_order") or {}
    out = []
    for name, typ, opts in _fields_in_order(schema.get("input", {}), order):
        if typ == _DYNAMIC_COMBO:
            out.append((name, typ, opts))
            continue
        if "." in name:
            continue  # a dynamic combo's flattened sub-widget: reached through its parent instead
        if isinstance(typ, list) or (isinstance(typ, str) and typ in _WIDGET_TYPES):
            out.append((name, typ, opts))
    return out


def _dynamic_sub_widgets(opts, key):
    """[(name, type, opts), ...] for the branch a dynamic combo's `key` selects, in order."""
    for option in (opts.get("options") or []):
        if not isinstance(option, dict) or option.get("key") != key:
            continue
        spec = option.get("inputs") or {}
        return _fields_in_order(spec, spec.get("input_order") or {})
    return []


def assign_widgets(schema, values, inputs, socket_names):
    """Name every entry of one node's `widgets_values`, writing into `inputs`.

    Returns the number of values consumed. A value left over at the end is ignored: the frontend
    keeps stale sub-widget values from a dynamic combo's previously selected branch, which is
    harmless where it lands (after every named widget) and is why this returns rather than raises.
    """
    pos = 0

    def take(name, typ, opts):
        nonlocal pos
        if pos >= len(values):
            return None
        value = values[pos]
        pos += 1
        # A widget the author converted into a socket keeps its slot in widgets_values on some
        # frontend versions, so consume the value but let the link win.
        if name not in inputs or name not in socket_names:
            inputs[name] = value
        declared = bool(isinstance(opts, dict) and opts.get("control_after_generate"))
        if pos < len(values) and (declared or values[pos] in _CONTROL_VALUES) \
                and isinstance(values[pos], str) and values[pos] in _CONTROL_VALUES:
            pos += 1  # the control_after_generate entry, GUI-only (declared or not)
        return value

    for name, typ, opts in widget_names(schema):
        value = take(name, typ, opts)
        if typ == _DYNAMIC_COMBO and value is not None:
            for sub, sub_typ, sub_opts in _dynamic_sub_widgets(opts, value):
                take(f"{name}.{sub}", sub_typ, sub_opts)
    return pos


def convert(graph, object_info):
    """The GUI graph as an API prompt dict. Raises on an unknown class or a subgraph node."""
    # link id -> (from_node_id, from_slot), read from the flat `links` array.
    links = {}
    for link in graph.get("links") or []:
        if isinstance(link, (list, tuple)) and len(link) >= 5:
            links[link[0]] = (str(link[1]), link[2])

    prompt = {}
    for node in graph.get("nodes") or []:
        ntype = node.get("type") or ""
        if ntype in _SKIP_TYPES:
            continue
        if node.get("mode") in (2, 4):  # muted or bypassed in the GUI
            continue
        if ntype not in object_info:
            # A UUID type is a subgraph, whose real nodes live under definitions.subgraphs; Bob
# ships flattened graphs only (docs/GENERATION.md, "Deriving from the shipped
# templates").
            raise ValueError(f"node {node.get('id')}: unknown class_type {ntype!r} "
                             f"({'subgraph, flatten it first' if '-' in ntype else 'pack missing'})")
        inputs = {}
        socket_names = set()
        for sock in node.get("inputs") or []:
            name = sock.get("name")
            socket_names.add(name)
            src = links.get(sock.get("link"))
            if src is not None:
                inputs[name] = [src[0], src[1]]
        assign_widgets(object_info[ntype], list(node.get("widgets_values") or []),
                       inputs, socket_names)
        prompt[str(node["id"])] = {"class_type": ntype, "inputs": inputs,
                                   "_meta": {"title": node.get("title") or ntype}}
    return prompt


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("workflow", help="GUI-format .json, or a ComfyUI-saved .png")
    ap.add_argument("--out", help="write here instead of stdout")
    ap.add_argument("--url", default=os.environ.get("BOB_COMFY_URL", "http://127.0.0.1:8188"))
    ap.add_argument("--object-info", help="a cached /object_info dump, instead of the server")
    args = ap.parse_args()

    prompt = convert(load_graph(args.workflow),
                     load_object_info(args.url, args.object_info))
    text = json.dumps(prompt, indent=2)
    if args.out:
        with open(args.out, "w") as fh:
            fh.write(text + "\n")
        print(f"{len(prompt)} nodes -> {args.out}")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
