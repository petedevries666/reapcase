import ast
from pathlib import Path


SOURCE_ROOT = Path(__file__).parents[1] / "src"


def _annotations(tree):
    for node in ast.walk(tree):
        if isinstance(node, ast.AnnAssign):
            yield node.annotation
        elif isinstance(node, ast.arg) and node.annotation is not None:
            yield node.annotation
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.returns is not None:
            yield node.returns


def test_source_uses_python_39_compatible_syntax_and_annotations():
    """Keep imports safe on 3.9, where evaluated PEP 604 unions crash."""
    for path in SOURCE_ROOT.rglob("*.py"):
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(path), feature_version=(3, 9))
        for annotation in _annotations(tree):
            pep_604 = [
                node for node in ast.walk(annotation)
                if isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr)
            ]
            assert not pep_604, f"PEP 604 annotation is not Python 3.9 compatible: {path}"
