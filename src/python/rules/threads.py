"""Thread target classification, the counterpart of src/rust/src/threads.rs."""
from __future__ import annotations

import ast
from dataclasses import dataclass

from .index import FileIndex, scoped_index
from .syntax import qualname, scope_nodes
from .tables import IO_ATTRS, IO_NAMES, IO_PREFIXES, NATIVE_PREFIXES


@dataclass
class WorkProfile:
    io: bool = False
    native: bool = False
    data_loop: ast.AST | None = None   # a Python loop iterating the function's own inputs


def classify(fn: ast.AST, idx: FileIndex, seen: set[str] | None = None) -> WorkProfile:
    """What does this function do with the data it is handed?"""
    seen = seen or set()
    seen.add(fn.name)
    idx = scoped_index(fn, idx)
    prof = WorkProfile()
    a = fn.args
    params = {x.arg for x in a.posonlyargs + a.args + a.kwonlyargs}
    params |= {x.arg for x in (a.vararg, a.kwarg) if x}
    params.discard("self")

    def uses_param(expr: ast.AST) -> bool:
        return any(isinstance(n, ast.Name) and n.id in params for n in ast.walk(expr))

    loops = []
    for node in scope_nodes(fn):
        if isinstance(node, (ast.For, ast.AsyncFor)) and uses_param(node.iter):
            loops.append(node)
        elif isinstance(node, ast.While) and uses_param(node.test):
            loops.append(node)
        elif isinstance(node, ast.comprehension) and uses_param(node.iter):
            loops.append(node.iter)
        if isinstance(node, ast.Call):
            q = qualname(node.func, idx.imports) or ""
            attr = node.func.attr if isinstance(node.func, ast.Attribute) else ""
            if q in IO_NAMES or q.startswith(IO_PREFIXES) or attr in IO_ATTRS:
                prof.io = True
            if q.startswith(NATIVE_PREFIXES):
                prof.native = True
            # Follow calls into other functions defined in this file.
            if isinstance(node.func, ast.Name) and node.func.id in idx.functions \
                    and node.func.id not in seen:
                sub = classify(idx.functions[node.func.id], idx, seen)
                prof.io |= sub.io
                prof.native |= sub.native
    if loops:  # earliest in source order (ast.walk is breadth-first)
        prof.data_loop = min(loops, key=lambda n: (n.lineno, n.col_offset))
    return prof
