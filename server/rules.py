"""
Python Memory Guardian - analysis rules (CPython `ast`).

Mirrored rule-for-rule by rust-server/src/main.rs; both render their text
from the shared messages.json using the facts that probe.py measured on the
user's interpreter.

Rule families (diagnostic `code` = messages.json key):
  heap-inflation          fetchall(), fetchone() in loops, `for row in cursor`
  pointer-chasing         pandas imports / row-oriented loaders
  cyclic-reference        back-pointer classes instantiated in loops
  ram-fragmentation.*     per-row dict/tuple/list appends; no-__slots__ classes in loops
  text-inflation.*        str += in loops; read().split*() / readlines()
  memory-swell.*          sum/any/all/min/max([...]); for x in list(...); unbounded caches
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


def index_file(tree: ast.Module) -> FileIndex:
    idx = FileIndex()
    for node in ast.walk(tree):
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
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            idx.functions[node.name] = node
        elif isinstance(node, ast.ClassDef):
            idx.classes[node.name] = ClassInfo(_needs_slots(node, idx.imports), _cycle_edge(node))
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
    prof = WorkProfile()
    a = fn.args
    params = {x.arg for x in a.posonlyargs + a.args + a.kwonlyargs}
    params |= {x.arg for x in (a.vararg, a.kwarg) if x}
    params.discard("self")

    def uses_param(expr: ast.AST) -> bool:
        return any(isinstance(n, ast.Name) and n.id in params for n in ast.walk(expr))

    loops = []
    for node in ast.walk(fn):
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


class Visitor(ast.NodeVisitor):
    def __init__(self, idx: FileIndex, facts: dict):
        self.idx, self.facts = idx, facts
        self.out: list[Finding] = []
        self.loop_depth = 0
        self.async_stack: list[bool] = [False]
        self.reported_cycles: set[str] = set()
        self.str_names: set[str] = set()
        self.list_names: set[str] = set()
        self.executors: set[str] = set()

    def add(self, *a, **k):
        self.out.append(Finding(*a, **k))

    def q(self, node):
        return qualname(node, self.idx.imports)

    # ---- scopes
    def _visit_func(self, node, is_async: bool):
        self._check_cache_decorators(node)
        self.async_stack.append(is_async)
        self.generic_visit(node)
        self.async_stack.pop()

    def visit_FunctionDef(self, node):
        self._visit_func(node, False)

    def visit_AsyncFunctionDef(self, node):
        self._visit_func(node, True)

    def visit_Lambda(self, node):
        self.async_stack.append(False)
        self.generic_visit(node)
        self.async_stack.pop()

    def visit_ClassDef(self, node):
        for stmt in node.body:
            if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
                stmt._pmg_method = True  # noqa: SLF001 - marker for cache rule
        self.generic_visit(node)

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
        for t in node.targets:
            if isinstance(t, ast.Name):
                (self.str_names.add if _is_strish(node.value) else self.str_names.discard)(t.id)
                (self.list_names.add if _is_listish(node.value) else self.list_names.discard)(t.id)
                if isinstance(node.value, ast.Call) and self.q(node.value.func) in EXECUTOR_CTORS:
                    self.executors.add(t.id)
        self.generic_visit(node)

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
        if isinstance(f, ast.Attribute):
            span = (f.end_lineno, f.end_col_offset - len(f.attr), f.end_col_offset)
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


def analyze(source: str, uri: str = "file:///untitled.py",
            facts: dict | None = None) -> list[lsp.Diagnostic] | None:
    """Diagnostics for `source`, or None if it does not parse (mid-typing)."""
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError):
        return None
    facts = facts or {}
    lines = source.splitlines() or [""]
    v = Visitor(index_file(tree), facts)
    v.visit(tree)
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
