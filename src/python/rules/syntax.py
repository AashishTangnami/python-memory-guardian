"""AST helpers, the counterpart of src/rust/src/syntax.rs."""
from __future__ import annotations

import ast

from .tables import STORE_METHODS, WEAKREF_CALLS


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


def _occurrences(scope, name: str, skip_handlers: bool = False) -> tuple[list[ast.Name], int]:
    """Loads of `name` anywhere in `scope` (nested scopes included, so closures count), and how many
    times it is bound or rebound there: assignment targets, deletes, parameters, def/class names,
    import aliases, except-as names and global/nonlocal declarations. With `skip_handlers`, a nested
    `except ... as` block is not searched; only its own name is counted."""
    loads, binds = [], 0
    pending = list(scope.body)
    while pending:
        n = pending.pop()
        if isinstance(n, ast.Name) and n.id == name:
            if isinstance(n.ctx, ast.Load):
                loads.append(n)
            else:
                binds += 1
        elif isinstance(n, ast.arg) and n.arg == name:
            binds += 1
        elif isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and n.name == name:
            binds += 1
        elif isinstance(n, ast.alias) and (name == n.asname or name in n.name.split(".")):
            binds += 1      # any name in an import counts, as in the Rust server
        elif isinstance(n, ast.ImportFrom) and name in (n.module or "").split("."):
            binds += 1
        elif isinstance(n, ast.ExceptHandler) and n.name == name:
            binds += 1
        elif isinstance(n, (ast.Global, ast.Nonlocal)) and name in n.names:
            binds += 1
        if not (skip_handlers and isinstance(n, ast.ExceptHandler) and n.name):
            pending.extend(ast.iter_child_nodes(n))
    return loads, binds


def in_nested_scope(n: ast.AST, root: ast.AST, parents: dict) -> bool:
    """Whether `n` sits in a function, lambda or class nested inside `root`."""
    p = parents.get(n)
    while p is not None and p is not root:
        if isinstance(p, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef)):
            return True
        p = parents.get(p)
    return False


def stored_into(n: ast.AST, parents: dict) -> ast.AST | None:
    """Where the value `n` is kept: `obj.attr` for `obj.attr = n`, `obj` for `obj[key] = n` and for
    `obj.append(n)` (STORE_METHODS). `n` may be one element of a tuple or list display. None for
    any other use, and for a chained or annotated assignment."""
    value, p = n, parents.get(n)
    if isinstance(p, (ast.Tuple, ast.List)) and isinstance(p.ctx, ast.Load):
        value, p = p, parents.get(p)
    if isinstance(p, ast.Assign) and p.value is value and len(p.targets) == 1:
        target = p.targets[0]
        if isinstance(target, ast.Attribute):
            return target
        if isinstance(target, ast.Subscript):
            return target.value
    if (isinstance(p, ast.Call) and isinstance(p.func, ast.Attribute) and p.func.attr in STORE_METHODS
            and not p.keywords and len(p.args) == 1 and p.args[0] is value):
        return p.func.value
    return None
