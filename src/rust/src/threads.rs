//! Thread target classification, the counterpart of src/python/rules/threads.py.
use std::collections::HashSet;

use tree_sitter::Node;

use crate::index::*;
use crate::syntax::*;
use crate::tables::*;

// ---------------------------------------------------------------- thread target classification
pub(crate) fn scoped_index<'t>(root: Node<'t>, src: &[u8], parent: &Index<'t>) -> Index<'t> {
    let mut idx = parent.clone();
    for name in bound_names(root, src) {
        idx.imports.insert(name.clone(), "<local>".into());
        idx.functions.remove(&name);
        idx.classes.remove(&name);
    }
    let local = index_with_imports(root, src, idx.imports.clone());
    idx.imports.extend(local.imports);
    idx.functions.extend(local.functions);
    idx.classes.extend(local.classes);
    idx
}

#[derive(Default)]
pub(crate) struct WorkProfile<'t> {
    pub(crate) io: bool,
    pub(crate) native: bool,
    pub(crate) data_loop: Option<Node<'t>>,
}

pub(crate) fn classify<'t>(fn_node: Node<'t>, idx: &Index<'t>, src: &[u8], seen: &mut HashSet<String>) -> WorkProfile<'t> {
    let idx = scoped_index(fn_node, src, idx);
    if let Some(n) = field(fn_node, "name") {
        seen.insert(txt(n, src).to_string());
    }
    let params: HashSet<String> = param_names(fn_node, src).into_iter().filter(|p| p != "self").collect();
    let uses_param = |e: Node| {
        let mut hit = false;
        for_each_node(e, |x| hit |= is_name_ref(x) && params.contains(txt(x, src)));
        hit
    };
    let mut prof = WorkProfile::default();
    let mut calls = Vec::new();
    // Pre-order DFS visits nodes in source order, so the first match is the earliest loop.
    for n in scope_nodes(fn_node) {
        if prof.data_loop.is_none() {
            prof.data_loop = match n.kind() {
                "for_statement" => field(n, "right").filter(|r| uses_param(*r)).map(|_| n),
                "while_statement" => field(n, "condition").filter(|c| uses_param(*c)).map(|_| n),
                "for_in_clause" => field(n, "right").filter(|r| uses_param(*r)),
                _ => None,
            };
        }
        if n.kind() == "call" {
            calls.push(n);
        }
    }
    for call in calls {
        let Some(f) = field(call, "function") else { continue };
        let q = qualname(f, src, &idx.imports).unwrap_or_default();
        if IO_NAMES.contains(&q.as_str()) || IO_PREFIXES.iter().any(|p| q.starts_with(p))
            || IO_ATTRS.contains(&attr_name(f, src))
        {
            prof.io = true;
        }
        if NATIVE_PREFIXES.iter().any(|p| q.starts_with(p)) {
            prof.native = true;
        }
        if f.kind() == "identifier" {
            let name = txt(f, src);
            if let Some(sub_fn) = idx.functions.get(name) {
                if !seen.contains(name) {
                    let sub = classify(*sub_fn, &idx, src, seen);
                    prof.io |= sub.io;
                    prof.native |= sub.native;
                }
            }
        }
    }
    prof
}
