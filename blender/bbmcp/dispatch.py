"""Op dispatch: map an op dict to its builder. One registry, grows over time."""

from . import geonodes, mesh, proxies, util

_HANDLERS = {
    "add_mesh": mesh.add_mesh,
    "build_geonodes": geonodes.build_geonodes,
    "make_proxies": proxies.make_proxies,
    # "make_material": materials.make,    # later
}


def apply_op(op: dict) -> dict:
    handler = _HANDLERS.get(op.get("op"))
    if handler is None:
        raise ValueError(f"no handler for op: {op.get('op')!r}")
    util.ensure_object_mode()  # guard: predictable state regardless of user's mode
    return handler(op)
