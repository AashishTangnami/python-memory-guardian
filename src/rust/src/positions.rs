//! Positions and output ranges, the counterpart of src/python/rules/positions.py.
use tower_lsp::lsp_types::{Position, Range};
use tree_sitter::{Node, Point};

use crate::syntax::*;

// ---------------------------------------------------------------- positions & output
pub(crate) fn to_utf16(line: &str, byte_col: usize) -> u32 {
    let end = byte_col.min(line.len());
    let end = (0..=end).rev().find(|&i| line.is_char_boundary(i)).unwrap_or(0);
    line[..end].encode_utf16().count() as u32
}

pub(crate) fn lsp_range(lines: &[&str], s: Point, e: Point) -> Range {
    let line = |r: usize| lines.get(r).copied().unwrap_or("");
    let (er, ec) = if e.row != s.row { (s.row, line(s.row).len()) } else { (e.row, e.column) };
    Range::new(
        Position::new(s.row as u32, to_utf16(line(s.row), s.column)),
        Position::new(er as u32, to_utf16(line(er), ec)),
    )
}


/// Qualified enclosing scope, e.g. "Repo.lookup()" or "Repo" (= rules.scope_label).
pub(crate) fn scope_label(n: Node, src: &[u8]) -> Option<String> {
    let mut names = Vec::new();
    let mut innermost_is_fn = None;
    let mut cur = n;
    while let Some(p) = cur.parent() {
        if matches!(p.kind(), "function_definition" | "class_definition") {
            if let Some(name) = field(p, "name") {
                names.push(txt(name, src).to_string());
                innermost_is_fn.get_or_insert(p.kind() == "function_definition");
            }
        }
        cur = p;
    }
    if names.is_empty() {
        return None;
    }
    names.reverse();
    Some(names.join(".") + if innermost_is_fn == Some(true) { "()" } else { "" })
}

/// "Repo.lookup() › `rows` — " (each part optional), = rules.prefix.
pub(crate) fn prefix(scope: &Option<String>, subject: &Option<String>) -> String {
    let subj = subject.as_ref().map(|s| format!("`{s}`"));
    let parts: Vec<&str> = [scope.as_deref(), subj.as_deref()].into_iter().flatten().collect();
    if parts.is_empty() { String::new() } else { format!("{} — ", parts.join(" › ")) }
}
