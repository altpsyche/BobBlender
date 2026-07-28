#!/usr/bin/env python3
"""Generate the op-vocabulary table in docs/API.md from the source of truth.

The op contract is the Pydantic models in the extension's `mcp_agent/contracts.py`; the handlers
are the `_HANDLERS` registry in `core/dispatch.py`. This introspects the models (fields, types, defaults)
and parses the dispatch registry (op -> handler), then rewrites ONLY the region of docs/API.md
between the GENERATED markers, leaving the authored sections untouched.

Run: uv run --project tools python tools/scripts/gen_api_docs.py  (no new deps; Pydantic is there)
Dispatch is parsed with ast (importing it would pull in bpy, which the venv lacks).
"""

from __future__ import annotations

import ast
import sys
import typing
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
API_DOC = REPO / "docs" / "API.md"
EXT = REPO / "blender" / "extensions" / "bob_blender_tools"
DISPATCH = EXT / "core" / "dispatch.py"

# The contract now lives inside the extension (mcp_agent/); import it as a top-level package
# without touching the bpy-bound addon __init__ (same trick the server launcher uses).
sys.path.insert(0, str(EXT))
from mcp_agent import contracts  # noqa: E402

BEGIN = "<!-- BEGIN GENERATED: op-vocabulary (tools/scripts/gen_api_docs.py) -->"
END = "<!-- END GENERATED -->"


def _handlers() -> dict[str, str]:
    """op string -> 'module.func' by ast-parsing dispatch.py's _HANDLERS dict."""
    tree = ast.parse(DISPATCH.read_text(encoding="utf-8"))
    out: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == "_HANDLERS" for t in node.targets
        ):
            for key, val in zip(node.value.keys, node.value.values):
                if isinstance(key, ast.Constant) and isinstance(val, ast.Attribute):
                    out[key.value] = f"{val.value.id}.{val.attr}"
    return out


def _type_name(ann) -> str:
    """A short, readable type name for a Pydantic field annotation. Unions (incl. Optional) render
    as 'A | B'; Literals as their values. Pipes are escaped for markdown tables at assembly."""
    import types

    origin = typing.get_origin(ann)
    if origin is typing.Literal:
        return " | ".join(repr(a) for a in typing.get_args(ann))
    if origin is typing.Union or origin is getattr(types, "UnionType", ()):
        args = typing.get_args(ann)
        parts = [("None" if a is type(None) else _type_name(a)) for a in args]
        return " | ".join(parts)
    if hasattr(ann, "__name__"):
        return ann.__name__
    return str(ann).replace("typing.", "")


def _default(field) -> str:
    """The field's default, distinguishing a required field (PydanticUndefined sentinel) from an
    explicit `= None`."""
    from pydantic_core import PydanticUndefined

    if field.default is not PydanticUndefined:
        return f"`{field.default!r}`"
    if field.default_factory is not None:
        try:
            return f"`{field.default_factory()!r}`"
        except Exception:
            return "(factory)"
    return "**required**"


def _models():
    """The op models in the Operation union, in declared order."""
    return list(typing.get_args(typing.get_args(contracts.Operation)[0]))


def _table() -> str:
    handlers = _handlers()
    lines = [
        "| Op | Handler | Fields (type, default) |",
        "| --- | --- | --- |",
    ]
    for model in _models():
        op = model.model_fields["op"].default
        handler = handlers.get(op, "-")
        parts = []
        for name, field in model.model_fields.items():
            if name == "op":
                continue
            parts.append(f"`{name}`: {_type_name(field.annotation)} = {_default(field)}")
        fields = ("<br>".join(parts) if parts else "(none)").replace("|", "\\|")
        lines.append(f"| `{op}` | `{handler}` | {fields} |")
    # Registry-only ops (a handler with no contract model, e.g. read-only diagnostics).
    modelled = {m.model_fields["op"].default for m in _models()}
    extras = [op for op in handlers if op not in modelled]
    note = ""
    if extras:
        note = ("\n\n_Registry-only (dispatch handlers with no contract model, "
                "not exposed to the MCP op union): " + ", ".join(f"`{o}`" for o in extras) + "._")
    return "\n".join(lines) + note


def main() -> int:
    doc = API_DOC.read_text(encoding="utf-8")
    if BEGIN not in doc or END not in doc:
        raise SystemExit(f"markers not found in {API_DOC}; add {BEGIN} / {END}")
    head, rest = doc.split(BEGIN, 1)
    _, tail = rest.split(END, 1)
    new = f"{head}{BEGIN}\n\n{_table()}\n\n{END}{tail}"
    API_DOC.write_text(new, encoding="utf-8")
    print(f"updated op-vocabulary table in {API_DOC.relative_to(REPO)} "
          f"({len(_models())} ops)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
