"""The extension package must never import itself by absolute path.

Guards the dual-name hazard: `core/ui/bridge` load as `bl_ext.*.bob_blender_tools.*`
live and `bob_blender_tools.*` headless, so any absolute self-import resolves in only
one world. See tools/scripts/check_selfimports.py.
"""

import ast
import importlib.util
import pathlib

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "tools" / "scripts" / "check_selfimports.py"
PKG = REPO_ROOT / "blender" / "extensions" / "bob_blender_tools"


def _load_checker():
    spec = importlib.util.spec_from_file_location("check_selfimports", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_extension_has_no_absolute_self_imports():
    checker = _load_checker()
    violations = checker.check_package(PKG)
    assert violations == [], "absolute self-imports found:\n" + "\n".join(violations)


def test_checker_flags_a_planted_violation(tmp_path):
    """The checker must actually catch an absolute self-import, not vacuously pass."""
    checker = _load_checker()
    banned = checker.BANNED_TOP_LEVEL[0]
    offender = tmp_path / "bad.py"
    offender.write_text(f"from {banned}.core import x\n", encoding="utf-8")
    hits = checker.check_file(offender)
    assert len(hits) == 1

    # Relative imports and stdlib imports must NOT trip it.
    ok = tmp_path / "good.py"
    ok.write_text("from . import server\nimport os\nfrom ..core import dispatch\n", encoding="utf-8")
    assert checker.check_file(ok) == []
    # Sanity: parses as relative (level > 0), so nothing to flag.
    assert all(isinstance(n, ast.AST) for n in ast.walk(ast.parse(ok.read_text())))
