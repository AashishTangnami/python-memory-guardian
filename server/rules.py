"""
Python Memory Guardian - analysis rules (CPython `ast`).

Mirrored rule-for-rule by rust-server/src/main.rs; both render their text
from the shared messages.json using the facts that probe.py measured on the
user's interpreter.

Rule families (diagnostic `code` = messages.json key):
  heap-inflation          fetchall(), fetchone() in loops, `for row in cursor`
  pointer-chasing         pandas imports / row-oriented loaders
  cyclic-reference        back-pointer classes instantiated in loops
  gc-cycle-risk           back-pointer classes with finalizers
  ram-fragmentation.*     per-row dict/tuple/list appends; no-__slots__ classes in loops
  text-inflation.*        str += in loops; read().split*() / readlines()
  memory-swell.*          list materialization, unbounded caches, deepcopy/re.compile in loops
  resource-leak.*         file opens without visible cleanup
  task-retention.*        discarded asyncio tasks
  single-thread-stall.*   blocking calls in async def; `x in list` in loops;
                          CPU-bound pure-Python functions handed to threads
"""
from __future__ import annotations

import ast
import json
import os
import re
from dataclasses import dataclass, field

import _vendor  # noqa: F401  (must come first: puts server/libs on sys.path)
from lsprotocol import types as lsp

SOURCE = "Python Memory Guardian"
SUPPRESS = "memory-guardian: ignore"
W, I = lsp.DiagnosticSeverity.Warning, lsp.DiagnosticSeverity.Information

with open(os.path.join(os.path.dirname(__file__), "messages.json"), encoding="utf-8") as _f:
    MESSAGES: dict = json.load(_f)

_PH = re.compile(r"\{([a-z0-9_]+)\}")


def render(key: str, facts: dict, local: dict) -> str:
    """Use the 'sized' text only if every placeholder is known; else 'neutral'."""
    values = {k: str(v) for k, v in facts.items()}
    values.update(local)
    msg = MESSAGES[key]
    sized = msg.get("sized")
    if sized and all(p in values for p in _PH.findall(sized)):
        return _PH.sub(lambda m: values[m.group(1)], sized)
    return _PH.sub(lambda m: values.get(m.group(1), m.group(0)), msg["neutral"])


# ---------------------------------------------------------------- rule tables
PANDAS_LOADERS = {
    "read_csv", "read_json", "read_table", "read_sql", "read_sql_query",
    "read_sql_table", "read_excel", "read_fwf",
}
LINK_ATTRS = {
    "parent", "prev", "previous", "next", "left", "right", "sibling", "owner",
    "back", "peer", "twin", "partner", "container", "root",
}
WEAKREF_CALLS = {"ref", "proxy", "WeakValueDictionary", "WeakKeyDictionary", "WeakSet"}

# Blocking calls that freeze an asyncio event loop (docs: library/asyncio-dev).
BLOCKING_CALLS = {
    "time.sleep", "requests.get", "requests.post", "requests.put", "requests.patch",
    "requests.delete", "requests.head", "requests.request", "urllib.request.urlopen",
    "subprocess.run", "subprocess.call", "subprocess.check_call", "subprocess.check_output",
    "socket.create_connection",
}
AGGREGATORS = {"sum", "any", "all", "min", "max"}

# Thread classification. docs.python.org/3/library/threading: only one thread runs
# Python code at a time, but threading "is still an appropriate model" for I/O-bound
# tasks; the glossary notes some native modules release the GIL (compression, hashing).
THREAD_CTORS = {"threading.Thread"}
EXECUTOR_CTORS = {
    "concurrent.futures.ThreadPoolExecutor", "multiprocessing.pool.ThreadPool",
    "multiprocessing.dummy.Pool",
}
EXECUTOR_METHODS = {"submit", "map", "apply", "apply_async", "imap", "imap_unordered", "starmap"}
IO_PREFIXES = (
    "requests.", "urllib.", "http.", "socket.", "subprocess.", "httpx.", "boto3.",
    "ftplib.", "smtplib.", "sqlite3.", "psycopg2.", "pymysql.", "shutil.",
)
IO_NAMES = {"open", "input", "time.sleep"}
IO_ATTRS = {
    "read", "readline", "readlines", "write", "writelines", "recv", "recv_into", "send",
    "sendall", "connect", "execute", "executemany", "fetchone", "fetchmany", "fetchall",
    "commit", "urlopen", "read_text", "read_bytes", "write_text", "write_bytes",
    "download_file", "upload_file", "get_object", "put_object",
}
NATIVE_PREFIXES = (
    "numpy.", "polars.", "pyarrow.", "hashlib.", "zlib.", "bz2.", "lzma.", "duckdb.",
    "scipy.", "numexpr.",
)
SLOTS_EXEMPT_BASES = {
    "NamedTuple", "Enum", "IntEnum", "StrEnum", "Flag", "IntFlag", "TypedDict",
    "Protocol", "Exception", "BaseException",
}


# ---------------------------------------------------------------- helpers
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


def _base_name(b: ast.AST) -> str:
    return b.attr if isinstance(b, ast.Attribute) else getattr(b, "id", "")


# ---------------------------------------------------------------- pass 1
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


def scope_nodes(tree):
    """Walk one lexical body, yielding nested definitions but not their bodies."""
    pending = list(reversed(tree.body if isinstance(tree.body, list) else [tree.body]))
    while pending:
        node = pending.pop()
        yield node
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
            pending.extend(reversed(list(ast.iter_child_nodes(node))))


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


# ---------------------------------------------------------------- thread target classification
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


# ---------------------------------------------------------------- pass 2
@dataclass
class Finding:
    node: ast.AST
    key: str
    severity: lsp.DiagnosticSeverity
    local: dict = field(default_factory=dict)
    span: tuple[int, int, int] | None = None
    related: list[tuple[ast.AST, str]] = field(default_factory=list)
    subject: str | None = None      # the variable / callable the finding is about


def dotted(n: ast.AST) -> str | None:
    """`self.buf` / `cur` / `pd.read_csv` as written; None for anything else."""
    if isinstance(n, ast.Name):
        return n.id
    if isinstance(n, ast.Attribute):
        base = dotted(n.value)
        return f"{base}.{n.attr}" if base else None
    return None


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
            if (isinstance(it, ast.Call) and isinstance(it.func, ast.Attribute)
                    and it.func.attr == "execute") or (
                    isinstance(it, ast.Name) and it.id.lower() in {"cursor", "cur", "rows_cursor"}):
                subj = dotted(it.func.value) if isinstance(it, ast.Call) else it.id
                self.add(it, "heap-inflation", W, subject=subj)
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

    # ---- imports (pointer chasing)
    def visit_Import(self, node):
        if any(a.name == "pandas" for a in node.names):
            self.add(node, "pointer-chasing", W)

    def visit_ImportFrom(self, node):
        if node.module == "pandas":
            self.add(node, "pointer-chasing", W)

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
            if f.attr == "fetchall" or (f.attr == "fetchone" and self.loop_depth):
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
        # pointer chasing: pandas loaders however imported
        if q.startswith("pandas.") and q.split(".")[-1] in PANDAS_LOADERS:
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


# ---------------------------------------------------------------- positions & output
def _utf16(line: str, byte_col: int) -> int:
    prefix = line.encode("utf-8")[:byte_col].decode("utf-8", errors="ignore")
    return len(prefix.encode("utf-16-le")) // 2


def _range(lines: list[str], node: ast.AST, span=None) -> lsp.Range:
    if span:
        sl = el = span[0]
        c0, c1 = span[1], span[2]
    else:
        sl, c0 = node.lineno, node.col_offset
        el, c1 = node.end_lineno or sl, node.end_col_offset or c0
        if el != sl:
            el, c1 = sl, len(lines[sl - 1].encode("utf-8"))
    return lsp.Range(
        start=lsp.Position(line=sl - 1, character=_utf16(lines[sl - 1], c0)),
        end=lsp.Position(line=el - 1, character=_utf16(lines[el - 1], c1)),
    )


def _scopes(tree: ast.Module) -> list[tuple[tuple[int, int], tuple[int, int], str, bool]]:
    """(start, end, name, is_function) for every class/def, used to name a finding's scope."""
    out = []
    for n in ast.walk(tree):
        if isinstance(n, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            out.append(((n.lineno, n.col_offset), (n.end_lineno, n.end_col_offset), n.name,
                        not isinstance(n, ast.ClassDef)))
    return out


def scope_label(scopes, pos: tuple[int, int]) -> str | None:
    """Qualified enclosing scope, e.g. 'Repo.lookup()' or 'Repo'; None at module level."""
    inside = sorted((s for s in scopes if s[0] <= pos <= s[1]), key=lambda s: s[0])
    if not inside:
        return None
    return ".".join(s[2] for s in inside) + ("()" if inside[-1][3] else "")


def prefix(scope: str | None, subject: str | None) -> str:
    """'Repo.lookup() › `rows` — ' (each part optional). Shared format with the Rust server."""
    parts = [p for p in (scope, f"`{subject}`" if subject else None) if p]
    return (" › ".join(parts) + " — ") if parts else ""


# ---------------------------------------------------------------- memory swell: a list used once
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


def analyze(source: str, uri: str = "file:///untitled.py",
            facts: dict | None = None) -> list[lsp.Diagnostic] | None:
    """Diagnostics for `source`, or None if it does not parse (mid-typing)."""
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError):
        return None
    facts = facts or {}
    lines = source.splitlines() or [""]
    v = Visitor(index_file(tree), facts, tree)
    v.visit(tree)
    v.out.extend(_list_once(tree, v.parents))
    scopes = _scopes(tree)

    diags, seen = [], set()
    for fd in v.out:
        rng = _range(lines, fd.node, fd.span)
        key = (rng.start.line, rng.start.character, fd.key)
        if key in seen or SUPPRESS in lines[rng.start.line]:
            continue
        seen.add(key)
        related = [lsp.DiagnosticRelatedInformation(
            location=lsp.Location(uri=uri, range=_range(lines, n)), message=m)
            for n, m in fd.related] or None
        start = (fd.span[0], fd.span[1]) if fd.span else (fd.node.lineno, fd.node.col_offset)
        head = prefix(scope_label(scopes, start), fd.subject)
        diags.append(lsp.Diagnostic(
            range=rng, message=head + render(fd.key, facts, fd.local), severity=fd.severity,
            source=SOURCE, code=fd.key, related_information=related))
    diags.sort(key=lambda d: (d.range.start.line, d.range.start.character))
    return diags
