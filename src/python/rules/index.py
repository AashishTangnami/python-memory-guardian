"""Pass 1: the file index, the counterpart of src/rust/src/index.rs."""
from __future__ import annotations

import ast
from dataclasses import dataclass, field

from .syntax import _base_name, _is_self, _is_weakref_wrapped, qualname, scope_nodes
from .tables import LINK_ATTRS, SLOTS_EXEMPT_BASES


@dataclass
class ClassInfo:
    needs_slots: bool
    cycle_edge: ast.AST | None


@dataclass
class FileIndex:
    imports: dict[str, str] = field(default_factory=dict)
    functions: dict[str, ast.AST] = field(default_factory=dict)
    classes: dict[str, ClassInfo] = field(default_factory=dict)


def _cycle_edge(cls: ast.ClassDef) -> ast.AST | None:
    for node in ast.walk(cls):
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            value = node.value
            if value is None or _is_weakref_wrapped(value):
                continue
            for t in targets:
                if not isinstance(t, ast.Attribute):
                    continue
                back_edge = _is_self(value) and not _is_self(t.value)
                link_field = (_is_self(t.value) and t.attr in LINK_ATTRS
                              and not isinstance(value, ast.Constant))
                if back_edge or link_field:
                    return node
        elif (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
              and node.func.attr in {"append", "add", "insert"}
              and any(_is_self(a) for a in node.args)):
            return node
    return None


def _needs_slots(cls: ast.ClassDef, imports: dict[str, str]) -> bool:
    for node in ast.walk(cls):
        if (isinstance(node, ast.Attribute) and _is_self(node.value)
                and node.attr == "__dict__"):
            return False
        if (isinstance(node, ast.Call) and qualname(node.func, imports) in
                {"setattr", "builtins.setattr"} and node.args and _is_self(node.args[0])):
            return False
    for stmt in cls.body:
        targets = stmt.targets if isinstance(stmt, ast.Assign) else (
            [stmt.target] if isinstance(stmt, ast.AnnAssign) else [])
        if any(isinstance(t, ast.Name) and t.id == "__slots__" for t in targets):
            return False
    for d in cls.decorator_list:
        if isinstance(d, ast.Call) and (qualname(d.func, imports) or "").endswith("dataclass"):
            if any(k.arg == "slots" and isinstance(k.value, ast.Constant) and k.value.value is True
                   for k in d.keywords):
                return False
    for b in cls.bases:
        name = _base_name(b)
        if name in SLOTS_EXEMPT_BASES or name.endswith(("Error", "Exception", "Warning")):
            return False
    return True


def bound_names(tree) -> set[str]:
    names, external = set(), set()
    nodes = list(scope_nodes(tree))
    comprehension_targets = {id(n) for c in nodes if isinstance(c, ast.comprehension)
                             for n in ast.walk(c.target)}
    for node in nodes:
        if (isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del))
                and id(node) not in comprehension_targets):
            names.add(node.id)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, ast.Import):
            names.update(a.asname or a.name.split('.')[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            names.update(a.asname or a.name for a in node.names)
        elif isinstance(node, ast.ExceptHandler) and node.name:
            names.add(node.name)
        elif isinstance(node, (ast.Global, ast.Nonlocal)):
            external.update(node.names)
    if hasattr(tree, 'args'):
        a = tree.args
        names.update(x.arg for x in a.posonlyargs + a.args + a.kwonlyargs)
        names.update(x.arg for x in (a.vararg, a.kwarg) if x)
    return names - external


def index_file(tree, imports=None) -> FileIndex:
    idx = FileIndex(imports=dict(imports or {}))
    for node in scope_nodes(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                if a.asname:
                    idx.imports[a.asname] = a.name
                else:
                    top = a.name.split(".")[0]
                    idx.imports[top] = top
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            for a in node.names:
                idx.imports[a.asname or a.name] = f"{node.module}.{a.name}"
    for node in scope_nodes(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            idx.functions[node.name] = node
        elif isinstance(node, ast.ClassDef):
            idx.classes[node.name] = ClassInfo(_needs_slots(node, idx.imports), _cycle_edge(node))
    return idx


def scoped_index(tree, parent: FileIndex) -> FileIndex:
    idx = FileIndex(dict(parent.imports), dict(parent.functions), dict(parent.classes))
    for name in bound_names(tree):
        idx.imports[name] = '<local>'
        idx.functions.pop(name, None)
        idx.classes.pop(name, None)
    local = index_file(tree, idx.imports)
    idx.imports.update(local.imports)
    idx.functions.update(local.functions)
    idx.classes.update(local.classes)
    return idx
