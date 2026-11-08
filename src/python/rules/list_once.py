"""Memory swell: a list used once, the counterpart of src/rust/src/list_once.rs."""
from __future__ import annotations

import ast

from .syntax import scope_nodes
from .tables import AGGREGATORS
from .visitor import Finding, I


ITERATING_CALLS = AGGREGATORS | {"iter", "enumerate", "zip"}


def _occurrences(scope, name: str) -> tuple[list[ast.Name], int]:
    """Loads of `name` anywhere in `scope` (nested scopes included, so closures count), and how many
    times it is bound or rebound there: assignment targets, deletes, parameters, def/class names,
    import aliases, except-as names and global/nonlocal declarations."""
    loads, binds = [], 0
    for stmt in scope.body:
        for n in ast.walk(stmt):
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
    return loads, binds


def _direct_iteration(n: ast.AST, parents: dict) -> str | None:
    """How a load only iterates its value once: a for loop, a comprehension, or an iterating builtin."""
    p = parents.get(n)
    if isinstance(p, (ast.For, ast.AsyncFor)) and p.iter is n:
        return "a for loop"
    if isinstance(p, ast.comprehension) and p.iter is n:
        return "a comprehension"
    if (isinstance(p, ast.Call) and isinstance(p.func, ast.Name) and p.func.id in ITERATING_CALLS
            and any(x is n for x in p.args)):
        return f"{p.func.id}()"
    return None


def _single_use(n: ast.Name, parents: dict, funcs: dict) -> str | None:
    """A direct iteration, or a positional argument to a module-level function of this file whose
    parameter is itself only iterated once."""
    use = _direct_iteration(n, parents)
    if use:
        return use
    p = parents.get(n)
    if not (isinstance(p, ast.Call) and isinstance(p.func, ast.Name) and p.func.id in funcs):
        return None
    if any(isinstance(x, ast.Starred) for x in p.args):
        return None
    i = next((k for k, x in enumerate(p.args) if x is n), None)
    fn = funcs[p.func.id]
    params = fn.args.posonlyargs + fn.args.args
    if i is None or i >= len(params):
        return None
    loads, binds = _occurrences(fn, params[i].arg)
    if binds == 0 and len(loads) == 1 and _direct_iteration(loads[0], parents):
        return f"{p.func.id}(), which only iterates it"
    return None


def _list_once(tree: ast.Module, parents: dict) -> list[Finding]:
    """`name = list(x)` or `name = [comprehension]`, then `name` used exactly once, only to iterate it.
    The whole list is built and held although each item is needed once: pass a generator instead.
    Not reported when the list is used again, indexed, sliced, measured with len(), or rebound."""
    funcs = {n.name: n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
    scopes = [tree] + [n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
    out = []
    for scope in scopes:
        for node in scope_nodes(scope):
            if not (isinstance(node, ast.Assign) and len(node.targets) == 1
                    and isinstance(node.targets[0], ast.Name)):
                continue
            v = node.value
            if isinstance(v, ast.ListComp):
                call = "a list comprehension"
            elif (isinstance(v, ast.Call) and isinstance(v.func, ast.Name) and v.func.id == "list"
                  and len(v.args) == 1 and not v.keywords and not isinstance(v.args[0], ast.Starred)):
                call = "list(...)"
            else:
                continue
            name = node.targets[0].id
            loads, binds = _occurrences(scope, name)
            if binds != 1 or len(loads) != 1:
                continue
            load = loads[0]
            if (load.lineno, load.col_offset) <= (node.lineno, node.col_offset):
                continue
            if not any(n is load for n in scope_nodes(scope)):
                continue        # used inside a nested function, which may run many times
            use = _single_use(load, parents, funcs)
            if use:
                out.append(Finding(v, "memory-swell.list-once", I,
                                   {"name": name, "call": call, "use": use}, subject=name))
    return out
