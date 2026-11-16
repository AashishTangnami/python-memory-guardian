"""Positions and scope labels, the counterpart of src/rust/src/positions.rs."""
from __future__ import annotations

import ast

from lsprotocol import types as lsp


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
