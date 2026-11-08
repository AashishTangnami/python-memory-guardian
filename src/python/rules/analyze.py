"""One analysis run, the counterpart of src/rust/src/analyze.rs."""
from __future__ import annotations

import ast

from lsprotocol import types as lsp

from .index import index_file
from .list_once import _list_once
from .messages import render
from .positions import _range, _scopes, prefix, scope_label
from .visitor import Visitor

SOURCE = "Python Memory Guardian"
SUPPRESS = "memory-guardian: ignore"


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
            source=SOURCE, code=fd.key, related_information=related,
            data={"schema_version": 1, "analysis": "static", "category": fd.key,
                  "severity": int(fd.severity), "status": "potential", "confidence": "static",
                  "workload": None, "impact": None, "trace_artifact": None,
                  "availability": {"workload": "unavailable", "impact": "unavailable",
                                   "trace_artifact": "unavailable"},
                  "evidence": {"kind": "syntax", "measured": False,
                               "source": {"uri": uri, "line": rng.start.line + 1,
                                          "column": rng.start.character,
                                          "end_line": rng.end.line + 1,
                                          "end_column": rng.end.character},
                               "scope": scope_label(scopes, start), "subject": fd.subject},
                  "reproduction": {"source_uri": uri,
                                   "steps": ["Analyze the same source document with the saved interpreter probe facts."]}}))
    diags.sort(key=lambda d: (d.range.start.line, d.range.start.character))
    return diags
