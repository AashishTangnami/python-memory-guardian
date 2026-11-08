"""AST helpers, the counterpart of src/rust/src/syntax.rs."""
from __future__ import annotations

import ast

from .tables import WEAKREF_CALLS


def qualname(node: ast.AST, imports: dict[str, str]) -> str | None:
    """Resolve `pd.read_csv` -> 'pandas.read_csv' using the file's imports."""
    parts = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if not isinstance(node, ast.Name):
        return None
    return ".".join([imports.get(node.id, node.id)] + parts[::-1])


def _is_self(n: ast.AST) -> bool:
    return isinstance(n, ast.Name) and n.id == "self"


def _is_weakref_wrapped(v: ast.AST) -> bool:
    if isinstance(v, ast.Call):
        f = v.func
        return (f.attr if isinstance(f, ast.Attribute) else getattr(f, "id", None)) in WEAKREF_CALLS
    return False


def _is_strish(v: ast.AST) -> bool:
    if isinstance(v, ast.Constant):
        return isinstance(v.value, str)
    if isinstance(v, ast.JoinedStr):
        return True
    if isinstance(v, ast.Call) and isinstance(v.func, ast.Name) and v.func.id == "str":
        return True
    if isinstance(v, ast.BinOp) and isinstance(v.op, ast.Add):
        return _is_strish(v.left) or _is_strish(v.right)
    return False


def _is_listish(v: ast.AST) -> bool:
    return isinstance(v, (ast.List, ast.ListComp)) or (
        isinstance(v, ast.Call) and isinstance(v.func, ast.Name) and v.func.id == "list"
    )


def _object_dtype(v: ast.AST, imports: dict[str, str]) -> bool:
    """Only explicit object dtype is evidence; inferred input types are unknown."""
    if isinstance(v, ast.Constant):
        return v.value == "object"
    if isinstance(v, (ast.Name, ast.Attribute)):
        return qualname(v, imports) in {"object", "builtins.object"}
    if isinstance(v, ast.Dict):
        return any(_object_dtype(value, imports) for value in v.values)
    return False


def _base_name(b: ast.AST) -> str:
    return b.attr if isinstance(b, ast.Attribute) else getattr(b, "id", "")


def scope_nodes(tree):
    """Walk one lexical body, yielding nested definitions but not their bodies."""
    pending = list(reversed(tree.body if isinstance(tree.body, list) else [tree.body]))
    while pending:
        node = pending.pop()
        yield node
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
            pending.extend(reversed(list(ast.iter_child_nodes(node))))


def dotted(n: ast.AST) -> str | None:
    """`self.buf` / `cur` / `pd.read_csv` as written; None for anything else."""
    if isinstance(n, ast.Name):
        return n.id
    if isinstance(n, ast.Attribute):
        base = dotted(n.value)
        return f"{base}.{n.attr}" if base else None
    return None
