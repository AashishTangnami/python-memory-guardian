"""Pass 2: the visitor, the counterpart of src/rust/src/visitor.rs."""
from __future__ import annotations

import ast
from dataclasses import dataclass, field

from lsprotocol import types as lsp

from .index import FileIndex, _cycle_edge, bound_names, scoped_index
from .syntax import _is_listish, _is_strish, _object_dtype, dotted, qualname, scope_nodes
from .tables import (
    AGGREGATORS, BLOCKING_CALLS, EXECUTOR_CTORS, EXECUTOR_METHODS, PANDAS_LOADERS, THREAD_CTORS,
)
from .threads import classify

W, I = lsp.DiagnosticSeverity.Warning, lsp.DiagnosticSeverity.Information


@dataclass
class Finding:
    node: ast.AST
    key: str
    severity: lsp.DiagnosticSeverity
    local: dict = field(default_factory=dict)
    span: tuple[int, int, int] | None = None
    related: list[tuple[ast.AST, str]] = field(default_factory=list)
    subject: str | None = None      # the variable / callable the finding is about


def _file_open(node: ast.Call, q: str, idx: FileIndex) -> bool:
    if q in {"open", "builtins.open", "io.open"}:
        return True
    f = node.func
    return (isinstance(f, ast.Attribute) and f.attr == "open"
            and isinstance(f.value, ast.Call)
            and qualname(f.value.func, idx.imports) == "pathlib.Path")


class Visitor(ast.NodeVisitor):
    def __init__(self, idx: FileIndex, facts: dict, tree: ast.Module):
        self.idx, self.facts = idx, facts
        self.parents = {child: parent for parent in ast.walk(tree)
                        for child in ast.iter_child_nodes(parent)}
        self.out: list[Finding] = []
        self.loop_depth = 0
        self.async_stack: list[bool] = [False]
        self.reported_cycles: set[str] = set()
        self.str_names: set[str] = set()
        self.list_names: set[str] = set()
        self.executors: set[str] = set()
        self.class_parent = None

    def add(self, *a, **k):
        self.out.append(Finding(*a, **k))

    def q(self, node):
        return qualname(node, self.idx.imports)

    def _unmanaged_open(self, node: ast.Call) -> bool:
        cur = node
        while cur in self.parents:
            parent = self.parents[cur]
            if isinstance(parent, ast.withitem):
                return False
            if isinstance(parent, ast.Call) and (cur in parent.args or
                    any(k.value is cur for k in parent.keywords)):
                return False  # the callee may take ownership
            if isinstance(parent, (ast.Return, ast.Yield, ast.YieldFrom)):
                return False  # ownership may be passed to the caller
            if isinstance(parent, (ast.Assign, ast.AnnAssign)) and parent.value is cur:
                targets = parent.targets if isinstance(parent, ast.Assign) else [parent.target]
                names = {t.id for t in targets if isinstance(t, ast.Name)}
                if names:
                    scope = parent
                    while scope in self.parents and not isinstance(scope, (ast.Module, ast.FunctionDef,
                                                                           ast.AsyncFunctionDef, ast.Lambda)):
                        scope = self.parents[scope]
                    for n in scope_nodes(scope):
                        if (isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                                and n.func.attr == "close" and isinstance(n.func.value, ast.Name)
                                and n.func.value.id in names):
                            return False
                break
            if isinstance(parent, (ast.Expr, ast.Call, ast.Attribute, ast.Await, ast.Subscript)):
                cur = parent
                continue
            break
        return True

    # ---- scopes
    def _state(self):
        return self.idx, self.str_names, self.list_names, self.executors, self.loop_depth, self.class_parent

    def _restore(self, state):
        self.idx, self.str_names, self.list_names, self.executors, self.loop_depth, self.class_parent = state

    def _enter_scope(self, node, base):
        idx, strings, lists, executors, _, _ = base
        bound = bound_names(node)
        self.idx = scoped_index(node, idx)
        self.str_names, self.list_names = strings - bound, lists - bound
        self.executors = executors - bound
        self.loop_depth = 0
        self.class_parent = None

    def _visit_func(self, node, is_async: bool):
        self._check_cache_decorators(node)
        # Defaults and decorators execute in the enclosing scope.
        for expr in node.decorator_list + node.args.defaults + [x for x in node.args.kw_defaults if x]:
            self.visit(expr)
        saved = self._state()
        self._enter_scope(node, self.class_parent or saved)
        self.async_stack.append(is_async)
        for stmt in node.body:
            self.visit(stmt)
        self.async_stack.pop()
        self._restore(saved)

    def visit_FunctionDef(self, node):
        self._visit_func(node, False)

    def visit_AsyncFunctionDef(self, node):
        self._visit_func(node, True)

    def visit_Lambda(self, node):
        for expr in node.args.defaults + [x for x in node.args.kw_defaults if x]:
            self.visit(expr)
        saved = self._state()
        self._enter_scope(node, self.class_parent or saved)
        self.async_stack.append(False)
        self.visit(node.body)
        self.async_stack.pop()
        self._restore(saved)

    def visit_ClassDef(self, node):
        if (_cycle_edge(node) is not None and any(
                isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)) and stmt.name == "__del__"
                for stmt in node.body)):
            self.add(node, "gc-cycle-risk", I, subject=node.name)
        for expr in node.decorator_list + node.bases + [k.value for k in node.keywords]:
            self.visit(expr)
        saved = self._state()
        self._enter_scope(node, saved)
        self.loop_depth = saved[4]  # Class bodies execute immediately in the surrounding loop.
        self.class_parent = saved[-1] or saved
        for stmt in node.body:
            if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
                stmt._pmg_method = True  # noqa: SLF001 - marker for cache rule
            self.visit(stmt)
        self._restore(saved)

    # ---- loops
    def _visit_loop(self, node):
        if isinstance(node, (ast.For, ast.AsyncFor)):
            it = node.iter
            if isinstance(it, ast.Call) and isinstance(it.func, ast.Name) and it.func.id == "list" \
                    and it.args:
                self.add(it, "memory-swell.list-copy", W, {"call": "list(...)"},
                         subject=dotted(node.target))
            elif isinstance(it, ast.ListComp):
                self.add(it, "memory-swell.list-copy", W, {"call": "a list comprehension"},
                         subject=dotted(node.target))
            self.visit(node.target)
            self.visit(node.iter)
        else:
            self.visit(node.test)
        self.loop_depth += 1
        for s in node.body:
            self.visit(s)
        self.loop_depth -= 1
        for s in node.orelse:
            self.visit(s)

    visit_For = visit_AsyncFor = visit_While = _visit_loop

    def _visit_comp(self, node):
        self.loop_depth += 1
        self.generic_visit(node)
        self.loop_depth -= 1

    visit_ListComp = visit_SetComp = visit_DictComp = visit_GeneratorExp = _visit_comp

    # ---- name tracking (str / list variables, executors)
    def visit_Assign(self, node):
        self.visit(node.value)
        for t in node.targets:
            if isinstance(t, ast.Name):
                (self.str_names.add if _is_strish(node.value) else self.str_names.discard)(t.id)
                (self.list_names.add if _is_listish(node.value) else self.list_names.discard)(t.id)
                if isinstance(node.value, ast.Call) and self.q(node.value.func) in EXECUTOR_CTORS:
                    self.executors.add(t.id)
                else:
                    self.executors.discard(t.id)
                self.idx.imports[t.id] = '<local>'
                self.idx.functions.pop(t.id, None)
                self.idx.classes.pop(t.id, None)
            self.visit(t)

    def visit_With(self, node):
        for item in node.items:
            ce = item.context_expr
            if isinstance(ce, ast.Call) and self.q(ce.func) in EXECUTOR_CTORS \
                    and isinstance(item.optional_vars, ast.Name):
                self.executors.add(item.optional_vars.id)
        self.generic_visit(node)

    visit_AsyncWith = visit_With

    # ---- text inflation: str += in loops
    def visit_AugAssign(self, node):
        if self.loop_depth and isinstance(node.op, ast.Add):
            t = node.target
            if (isinstance(t, ast.Name) and t.id in self.str_names) or _is_strish(node.value):
                self.add(node, "text-inflation.concat", W, subject=dotted(t))
        self.generic_visit(node)

    # ---- single-thread stall: `x in some_list` in loops
    def visit_Compare(self, node):
        if self.loop_depth:
            for op, right in zip(node.ops, node.comparators):
                if isinstance(op, (ast.In, ast.NotIn)) and isinstance(right, ast.Name) \
                        and right.id in self.list_names:
                    self.add(node, "single-thread-stall.list-membership", W, {"name": right.id},
                             subject=right.id)
        self.generic_visit(node)

    # ---- imports alone do not establish any data layout
    def visit_Import(self, node):
        pass

    def visit_ImportFrom(self, node):
        pass

    # ---- memory swell: unbounded caches
    def _check_cache_decorators(self, fn):
        for d in fn.decorator_list:
            unbounded = self.q(d) == "functools.cache" or (
                isinstance(d, ast.Call) and self.q(d.func) == "functools.lru_cache" and (
                    (d.args and isinstance(d.args[0], ast.Constant) and d.args[0].value is None)
                    or any(k.arg == "maxsize" and isinstance(k.value, ast.Constant)
                           and k.value.value is None for k in d.keywords)))
            if not unbounded:
                continue
            a = fn.args.posonlyargs + fn.args.args
            is_method = getattr(fn, "_pmg_method", False) and a and a[0].arg == "self"
            if is_method:
                self.add(d, "memory-swell.method-cache", W, subject=fn.name)
            else:
                self.add(d, "memory-swell.unbounded-cache", I, subject=fn.name)

    # ---- calls
    def visit_Call(self, node):
        f = node.func
        q = self.q(f) or ""
        if self.loop_depth and q == "copy.deepcopy":
            self.add(node, "memory-swell.deepcopy-loop", W)
        if (self.loop_depth and q == "re.compile" and node.args
                and isinstance(node.args[0], ast.Constant)
                and isinstance(node.args[0].value, (str, bytes))):
            self.add(node, "memory-swell.recompile-loop", I)
        if q == "asyncio.create_task" and isinstance(self.parents.get(node), ast.Expr):
            self.add(node, "task-retention.asyncio-task", W)
        if _file_open(node, q, self.idx) and self._unmanaged_open(node):
            self.add(node, "resource-leak.file-handle", W)
        if isinstance(f, ast.Attribute):
            span = (f.end_lineno, f.end_col_offset - len(f.attr), f.end_col_offset)
            if (f.attr == "setdefault" and self.loop_depth and len(node.args) >= 2
                    and isinstance(node.args[1], ast.List) and not node.args[1].elts
                    and isinstance(self.parents.get(node), ast.Expr)):
                self.add(node, "memory-swell.setdefault-loop", I, subject=dotted(f.value))
            if (f.attr == "extend" and self.loop_depth and node.args
                    and isinstance(node.args[0], ast.ListComp)):
                self.add(node.args[0], "memory-swell.list-extend-loop", I,
                         subject=dotted(f.value))
            # heap inflation
            if f.attr == "fetchall":
                self.add(node, "heap-inflation", W, span=span, subject=dotted(f.value))
            # text inflation: whole-file reads
            if f.attr == "readlines" and not node.args:
                self.add(node, "text-inflation.whole-file", W, {"call": "readlines()"}, span=span,
                         subject=dotted(f.value))
            if f.attr in {"split", "splitlines", "rsplit"} and isinstance(f.value, ast.Call) \
                    and isinstance(f.value.func, ast.Attribute) and f.value.func.attr == "read" \
                    and not f.value.args:
                self.add(node, "text-inflation.whole-file", W, {"call": f"read().{f.attr}()"},
                         span=span, subject=dotted(f.value.func.value))
            # ram fragmentation: per-row container appends
            if f.attr == "append" and self.loop_depth and len(node.args) == 1:
                arg = node.args[0]
                if isinstance(arg, (ast.Dict, ast.Tuple, ast.List)) or (
                        isinstance(arg, ast.Call) and isinstance(arg.func, ast.Name)
                        and arg.func.id == "dict"):
                    self.add(arg, "ram-fragmentation.append", W, subject=dotted(f.value))
            # threads: executor.submit(fn, ...) / ThreadPoolExecutor().map(fn, ...)
            if f.attr in EXECUTOR_METHODS and node.args and (
                    (isinstance(f.value, ast.Name) and f.value.id in self.executors)
                    or (isinstance(f.value, ast.Call) and self.q(f.value.func) in EXECUTOR_CTORS)):
                self._check_thread_target(node.args[0])
        # Unknown/numeric pandas inputs can use compact arrays. Require dtype evidence.
        if (q.startswith("pandas.") and q.split(".")[-1] in
                PANDAS_LOADERS | {"DataFrame", "Series"} and any(
                    k.arg == "dtype" and _object_dtype(k.value, self.idx.imports)
                    for k in node.keywords)):
            span = None
            if isinstance(f, ast.Attribute):
                span = (f.end_lineno, f.end_col_offset - len(f.attr), f.end_col_offset)
            self.add(f, "pointer-chasing", W, span=span, subject=dotted(f))
        # stall: blocking call in async def
        if self.async_stack[-1] and q in BLOCKING_CALLS:
            self.add(f, "single-thread-stall.async-blocking", W, {"call": f"{q}()"})
        # threads: threading.Thread(target=fn)
        if q in THREAD_CTORS:
            for k in node.keywords:
                if k.arg == "target":
                    self._check_thread_target(k.value)
        if isinstance(f, ast.Name):
            # memory swell: sum([...]) etc.
            if f.id in AGGREGATORS and len(node.args) == 1 and isinstance(node.args[0], ast.ListComp):
                self.add(node.args[0], "memory-swell.list-arg", W, {"call": f.id})
            info = self.idx.classes.get(f.id)
            if info and self.loop_depth:
                if info.needs_slots:
                    self.add(f, "ram-fragmentation.no-slots", I, {"name": f.id}, subject=f.id)
                if info.cycle_edge is not None:
                    self.add(f, "cyclic-reference", I, subject=f.id,
                             related=[(info.cycle_edge, f"Cycle edge created here in class {f.id}")])
                    if f.id not in self.reported_cycles:
                        self.reported_cycles.add(f.id)
                        self.add(info.cycle_edge, "cyclic-reference", I, subject=f.id,
                                 related=[(f, f"{f.id} is instantiated inside a loop here")])
        self.generic_visit(node)

    def _check_thread_target(self, target: ast.AST):
        if self.facts.get("gil_state") == "disabled":
            return  # free-threaded runtime: threads do run Python code in parallel
        if not isinstance(target, ast.Name) or target.id not in self.idx.functions:
            return  # unknown callable: we don't guess what it does
        prof = classify(self.idx.functions[target.id], self.idx)
        if prof.data_loop is not None and not prof.io and not prof.native:
            self.add(target, "single-thread-stall.cpu-thread", I, {"func": target.id}, subject=target.id,
                     related=[(prof.data_loop, f"{target.id}() iterates its input in Python here")])
