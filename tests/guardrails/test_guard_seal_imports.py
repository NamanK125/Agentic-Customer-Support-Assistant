"""Seal confinement, checked by walking the source tree.

This complements the import-linter contract: it does not depend on a hand-maintained list of
source packages, so a NEW package (say, voice/) that imports the seal fails here even if nobody
added it to pyproject.
"""

import ast
from pathlib import Path

SRC = Path(__file__).resolve().parents[2] / "src" / "support_assistant"
ALLOWED = {SRC / "contracts.py", SRC / "_seal.py"}


def _imports_seal(tree: ast.AST) -> bool:
    for node in ast.walk(tree):
        if isinstance(node, ast.Import) and any(
            a.name.endswith("._seal") or a.name == "_seal" for a in node.names
        ):
            return True
        if isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            if mod.endswith("_seal") or any(a.name == "_seal" for a in node.names):
                return True
        if isinstance(node, ast.Call) and getattr(node.func, "id", "") in ("__import__",):
            if any(isinstance(a, ast.Constant) and "_seal" in str(a.value) for a in node.args):
                return True
        if isinstance(node, ast.Call) and getattr(node.func, "attr", "") == "import_module":
            if any(isinstance(a, ast.Constant) and "_seal" in str(a.value) for a in node.args):
                return True
    return False


def test_only_guardrails_and_contracts_import_the_seal():
    offenders = []
    for path in SRC.rglob("*.py"):
        if path in ALLOWED or (SRC / "guardrails") in path.parents:
            continue
        if _imports_seal(ast.parse(path.read_text(), filename=str(path))):
            offenders.append(str(path.relative_to(SRC)))
    assert offenders == []


def test_seal_is_used_in_guardrails_only_by_the_input_guard():
    users = [
        p.name
        for p in (SRC / "guardrails").rglob("*.py")
        if _imports_seal(ast.parse(p.read_text()))
    ]
    assert users == ["input_guard.py"]
