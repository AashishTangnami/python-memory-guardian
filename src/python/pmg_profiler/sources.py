"""Source files as they were when the run started: their identity, their hashes and their names.

The SHA-1 here is a source identity, not a digest of an artifact: it normalises line endings and must
match textHash in src/typescript/profile/index.ts.
"""
from __future__ import annotations

import ast
import hashlib
import io
import os
import sys
import time
import tokenize

from .common import UserFiles

# Total time report() may spend parsing source files to name memory-stack frames.
NAME_PARSE_BUDGET_S = 2.0


class SourceFiles:
    def __init__(self, users: UserFiles):
        self.users = users
        self._versions: dict[str, tuple[tuple, str]] = {}
        self._scopes_cache: dict[str, list] = {}
        self._parse_spent = 0.0

    @staticmethod
    def source_version(path: str) -> tuple:
        st = os.stat(path)
        return (st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns, st.st_ctime_ns)

    def remember(self):
        """Record identities before execution, including files imported later.

        A file created or changed during the run must not be certified fresh from
        its exit-time contents. Retain hashes rather than source buffers. This
        inventory runs before timing/tracing starts; hashes also protect platforms
        whose stat change-time field is actually a creation timestamp.
        """
        for directory, dirs, names in os.walk(self.users.root):
            dirs[:] = [d for d in dirs if not d.startswith('.') and d != 'node_modules'
                       and self.users.is_user(os.path.join(directory, d, '_source.py'))]
            for name in names:
                path = os.path.abspath(os.path.join(directory, name))
                if name.endswith(('.py', '.pyw')) and self.users.is_user(path):
                    try:
                        version = self.source_version(path)
                        with open(path, 'rb') as f:
                            digest = hashlib.sha1(f.read().replace(b"\r\n", b"\n")).hexdigest()
                        if self.source_version(path) == version:
                            self._versions[os.path.normcase(path)] = (version, digest)
                    except OSError:
                        pass

    def unchanged(self, path: str) -> bytes | None:
        """Return source only if its identity stayed stable throughout this run."""
        expected = self._versions.get(os.path.normcase(path))
        try:
            if expected is None or self.source_version(path) != expected[0]:
                return None
            with open(path, 'rb') as f:
                source = f.read()
            digest = hashlib.sha1(source.replace(b"\r\n", b"\n")).hexdigest()
            return source if self.source_version(path) == expected[0] and digest == expected[1] else None
        except OSError:
            return None

    @staticmethod
    def text_hash(source: bytes) -> str:
        encoding, _ = tokenize.detect_encoding(io.BytesIO(source).readline)
        text = source.decode(encoding).replace("\r\n", "\n")
        return hashlib.sha1(text.encode("utf-8")).hexdigest()

    @staticmethod
    def symbols(source: bytes) -> dict:
        """line -> {"scope": "Service.handle()", "assigns": [...], "calls": [...]} via ast."""
        try:
            tree = ast.parse(source)
        except (OSError, SyntaxError, ValueError):
            return {}
        out: dict = {}

        def dotted(n):
            if isinstance(n, ast.Name):
                return n.id
            if isinstance(n, ast.Attribute):
                b = dotted(n.value)
                return f"{b}.{n.attr}" if b else None
            return None

        def walk(node, scope):
            for child in ast.iter_child_nodes(node):
                sc = scope
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    sc = scope + [(child.name, not isinstance(child, ast.ClassDef))]
                ln = getattr(child, "lineno", None)
                if ln is not None:
                    e = out.setdefault(ln, {"scope": None, "assigns": [], "calls": []})
                    if sc:
                        e["scope"] = ".".join(n for n, _ in sc) + ("()" if sc[-1][1] else "")
                    targets = []
                    if isinstance(child, ast.Assign):
                        targets = child.targets
                    elif isinstance(child, (ast.AugAssign, ast.AnnAssign)):
                        targets = [child.target]
                    elif isinstance(child, (ast.For, ast.AsyncFor)):
                        targets = [child.target]
                    elif isinstance(child, ast.withitem) and child.optional_vars is not None:
                        targets = [child.optional_vars]
                    for t in targets:
                        for n in ast.walk(t):
                            d = None
                            if isinstance(n, (ast.Name, ast.Attribute)) and isinstance(n.ctx, ast.Store):
                                d = dotted(n)
                            elif isinstance(n, ast.Subscript) and isinstance(n.ctx, ast.Store):
                                base = dotted(n.value)
                                d = f"{base}[…]" if base else None   # self.index[i] = ...
                            if d and d not in e["assigns"]:
                                e["assigns"].append(d)
                    if isinstance(child, ast.Call):
                        d = dotted(child.func)
                        if d and d not in e["calls"] and len(e["calls"]) < 4:
                            e["calls"].append(d)
                walk(child, sc)
        walk(tree, [])
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                first = min([node.lineno] + [d.lineno for d in node.decorator_list])
                out.setdefault(first, {"scope": None, "assigns": [], "calls": []})['end_line'] = node.end_lineno
        return out

    def function_at(self, file: str, line: int, enclosing: bool = False) -> tuple[str, int]:
        """(qualified name, first line) of the code object containing a line, matching what the
        sampler records from co_qualname/co_firstlineno, so memory and time frames share an identity.
        enclosing=True skips a lambda/comprehension that starts on this line: tracemalloc gives only
        file and line, so the caller of an inline scope looks identical to the scope itself.
        Returns ('', line) when the source is unavailable or the parse budget is spent."""
        scopes = self._scopes_cache.get(file)
        if scopes is None:
            scopes = []
            if not file.startswith('<') and self._parse_spent < NAME_PARSE_BUDGET_S:
                started = time.perf_counter()
                try:
                    if os.path.getsize(file) <= 4 << 20:
                        with open(file, 'rb') as f:
                            tree = ast.parse(f.read())
                        scopes = [(1, 10 ** 9, '<module>', 1, False)]

                        def walk(node, prefix):
                            for child in ast.iter_child_nodes(node):
                                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                                    first = min([child.lineno] + [d.lineno for d in child.decorator_list])
                                    qual = prefix + child.name
                                    scopes.append((first, child.end_lineno or child.lineno, qual, first, False))
                                    walk(child, qual + '.')
                                elif isinstance(child, (ast.Lambda, ast.GeneratorExp)) or (
                                        isinstance(child, (ast.ListComp, ast.SetComp, ast.DictComp))
                                        and sys.version_info < (3, 12)):   # inlined since 3.12 (PEP 709)
                                    name = {ast.Lambda: '<lambda>', ast.GeneratorExp: '<genexpr>', ast.ListComp: '<listcomp>',
                                            ast.SetComp: '<setcomp>', ast.DictComp: '<dictcomp>'}[type(child)]
                                    scopes.append((child.lineno, child.end_lineno or child.lineno, prefix + name, child.lineno, True))
                                    walk(child, prefix)
                                else:
                                    walk(child, prefix)
                        walk(tree, '')
                except (OSError, SyntaxError, ValueError):
                    scopes = []
                self._parse_spent += time.perf_counter() - started
            self._scopes_cache[file] = scopes
        best = None
        for start, end, qual, first, inline in scopes:        # innermost: latest start that still contains the line
            if enclosing and inline and start == line:
                continue
            if start <= line <= end and (best is None or start >= best[0]):
                best = (start, qual, first)
        return (best[1], best[2]) if best else ('', line)
