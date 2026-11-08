//! Tree helpers, the counterpart of src/python/rules/syntax.py.
use std::collections::HashMap;

use tree_sitter::Node;

use crate::tables::*;

// ---------------------------------------------------------------- tree helpers
pub(crate) fn txt<'a>(n: Node, src: &'a [u8]) -> &'a str {
    n.utf8_text(src).unwrap_or("")
}

pub(crate) fn field<'t>(n: Node<'t>, f: &str) -> Option<Node<'t>> {
    n.child_by_field_name(f)
}

/// Strip redundant parentheses, as CPython's ast does.
pub(crate) fn unwrap(mut n: Node) -> Node {
    while n.kind() == "parenthesized_expression" {
        match n.named_child(0) {
            Some(c) => n = c,
            None => break,
        }
    }
    n
}

pub(crate) fn for_each_node<'t>(root: Node<'t>, mut f: impl FnMut(Node<'t>)) {
    let mut c = root.walk();
    'outer: loop {
        f(c.node());
        if c.goto_first_child() {
            continue;
        }
        loop {
            if c.goto_next_sibling() {
                continue 'outer;
            }
            if !c.goto_parent() {
                break 'outer;
            }
        }
    }
}

/// identifier used as a variable reference (not `obj.<attr>` or `kw=` names), = ast.Name.
pub(crate) fn is_name_ref(n: Node) -> bool {
    if n.kind() != "identifier" {
        return false;
    }
    match n.parent() {
        Some(p) if p.kind() == "attribute" => field(p, "attribute").map(|a| a.id()) != Some(n.id()),
        Some(p) if p.kind() == "keyword_argument" => field(p, "name").map(|a| a.id()) != Some(n.id()),
        _ => true,
    }
}

pub(crate) fn is_self(n: Option<Node>, src: &[u8]) -> bool {
    n.is_some_and(|n| n.kind() == "identifier" && txt(n, src) == "self")
}

pub(crate) fn named_children(n: Node) -> Vec<Node> {
    let mut c = n.walk();
    n.named_children(&mut c).filter(|x| x.kind() != "comment").collect()
}

/// Positional args (= ast Call.args) and keyword args of a call.
pub(crate) fn call_args(call: Node) -> (Vec<Node>, Vec<Node>) {
    match field(call, "arguments") {
        Some(a) if a.kind() == "generator_expression" => (vec![a], vec![]),
        Some(a) => named_children(a).into_iter().partition(|x| x.kind() != "keyword_argument"),
        None => (vec![], vec![]),
    }
}

pub(crate) fn kwarg<'t>(kws: &[Node<'t>], name: &str, src: &[u8]) -> Option<Node<'t>> {
    kws.iter()
        .find(|k| field(**k, "name").is_some_and(|n| txt(n, src) == name))
        .and_then(|k| field(*k, "value"))
}

pub(crate) fn qualname(n: Node, src: &[u8], imports: &HashMap<String, String>) -> Option<String> {
    let mut parts = Vec::new();
    let mut cur = unwrap(n);
    while cur.kind() == "attribute" {
        parts.push(txt(field(cur, "attribute")?, src).to_string());
        cur = unwrap(field(cur, "object")?);
    }
    if cur.kind() != "identifier" {
        return None;
    }
    let base = txt(cur, src);
    let mut out = vec![imports.get(base).cloned().unwrap_or_else(|| base.to_string())];
    out.extend(parts.into_iter().rev());
    Some(out.join("."))
}

pub(crate) fn attr_name<'a>(f: Node, src: &'a [u8]) -> &'a str {
    if f.kind() == "attribute" { field(f, "attribute").map_or("", |a| txt(a, src)) } else { "" }
}

pub(crate) fn is_bytes_literal(n: Node, src: &[u8]) -> bool {
    n.child(0).is_some_and(|s| txt(s, src).to_ascii_lowercase().contains('b'))
}

pub(crate) fn is_strish(n: Node, src: &[u8]) -> bool {
    let n = unwrap(n);
    match n.kind() {
        "string" => !is_bytes_literal(n, src),
        "concatenated_string" => n.named_child(0).is_some_and(|s| !is_bytes_literal(s, src)),
        "call" => field(n, "function").is_some_and(|f| f.kind() == "identifier" && txt(f, src) == "str"),
        "binary_operator" => {
            field(n, "operator").is_some_and(|o| txt(o, src) == "+")
                && (field(n, "left").is_some_and(|l| is_strish(l, src))
                    || field(n, "right").is_some_and(|r| is_strish(r, src)))
        }
        _ => false,
    }
}

pub(crate) fn is_listish(n: Node, src: &[u8]) -> bool {
    let n = unwrap(n);
    matches!(n.kind(), "list" | "list_comprehension")
        || (n.kind() == "call"
            && field(n, "function").is_some_and(|f| f.kind() == "identifier" && txt(f, src) == "list"))
}

/// Inferred input dtypes are unknown. Require an explicit object dtype.
pub(crate) fn object_dtype(n: Node, src: &[u8], imports: &HashMap<String, String>) -> bool {
    let n = unwrap(n);
    match n.kind() {
        "identifier" | "attribute" => qualname(n, src, imports)
            .is_some_and(|q| q == "object" || q == "builtins.object"),
        "string" => !is_bytes_literal(n, src) && named_children(n).iter()
            .any(|c| c.kind() == "string_content" && txt(*c, src) == "object"),
        "dictionary" => named_children(n).into_iter().any(|pair|
            field(pair, "value").is_some_and(|v| object_dtype(v, src, imports))),
        _ => false,
    }
}

pub(crate) fn is_weakref_call(n: Node, src: &[u8]) -> bool {
    let n = unwrap(n);
    if n.kind() != "call" {
        return false;
    }
    let Some(f) = field(n, "function") else { return false };
    let name = match f.kind() {
        "attribute" => attr_name(f, src),
        "identifier" => txt(f, src),
        _ => "",
    };
    WEAKREF_CALLS.contains(&name)
}

pub(crate) fn is_async(fn_node: Node) -> bool {
    fn_node.child(0).is_some_and(|c| c.kind() == "async")
}

pub(crate) fn param_names(fn_node: Node, src: &[u8]) -> Vec<String> {
    let mut out = Vec::new();
    if let Some(ps) = field(fn_node, "parameters") {
        for p in named_children(ps) {
            let id = match p.kind() {
                "identifier" => Some(p),
                "default_parameter" | "typed_default_parameter" => field(p, "name"),
                "typed_parameter" | "list_splat_pattern" | "dictionary_splat_pattern" => p.named_child(0),
                _ => None,
            };
            if let Some(id) = id.filter(|i| i.kind() == "identifier") {
                out.push(txt(id, src).to_string());
            }
        }
    }
    out
}

pub(crate) fn decorators(def: Node) -> Vec<Node> {
    match def.parent() {
        Some(p) if p.kind() == "decorated_definition" => named_children(p)
            .into_iter()
            .filter(|d| d.kind() == "decorator")
            .filter_map(|d| d.named_child(0))
            .collect(),
        _ => vec![],
    }
}

/// Function defined directly in a class body (= rules/visitor.py `_pmg_method`).
pub(crate) fn is_method(def: Node) -> bool {
    let mut p = def.parent();
    if p.is_some_and(|x| x.kind() == "decorated_definition") {
        p = p.and_then(|x| x.parent());
    }
    p.filter(|x| x.kind() == "block")
        .and_then(|b| b.parent())
        .is_some_and(|c| c.kind() == "class_definition")
}

/// `self.buf` / `cur` / `pd.read_csv` as written (= rules.dotted); None otherwise.
pub(crate) fn dotted(n: Node, src: &[u8]) -> Option<String> {
    let n = unwrap(n);
    match n.kind() {
        "identifier" => Some(txt(n, src).to_string()),
        "attribute" => {
            let base = dotted(field(n, "object")?, src)?;
            Some(format!("{base}.{}", txt(field(n, "attribute")?, src)))
        }
        _ => None,
    }
}

pub(crate) fn file_open(n: Node, f: Node, q: &str, src: &[u8], imports: &HashMap<String, String>) -> bool {
    if ["open", "builtins.open", "io.open"].contains(&q) { return true; }
    if attr_name(f, src) != "open" { return false; }
    field(f, "object").map(unwrap).is_some_and(|o| {
        o.kind() == "call" && field(o, "function")
            .and_then(|of| qualname(of, src, imports)).as_deref() == Some("pathlib.Path")
    }) && n.kind() == "call"
}

pub(crate) fn unmanaged_open(n: Node, src: &[u8]) -> bool {
    let mut cur = n;
    while let Some(parent) = cur.parent() {
        match parent.kind() {
            "with_item" => return false,
            "argument_list" | "keyword_argument" => return false, // callee may take ownership
            "return_statement" | "yield" => return false,
            "assignment" if field(parent, "right").map(|r| r.id()) == Some(cur.id()) => {
                let names: Vec<String> = field(parent, "left").into_iter()
                    .filter(|l| l.kind() == "identifier")
                    .map(|l| txt(l, src).to_string()).collect();
                if !names.is_empty() {
                    let mut scope = parent;
                    while let Some(p) = scope.parent() {
                        scope = p;
                        if matches!(scope.kind(), "module" | "function_definition" | "lambda") { break; }
                    }
                    if scope_nodes(scope).into_iter().any(|x| {
                        x.kind() == "call" && field(x, "function").is_some_and(|f| {
                            attr_name(f, src) == "close" && field(f, "object").is_some_and(|o| {
                                o.kind() == "identifier" && names.iter().any(|name| name == txt(o, src))
                            })
                        })
                    }) { return false; }
                }
                return true;
            }
            "expression_statement" | "call" | "attribute" | "await" | "subscript"
                | "parenthesized_expression" | "as_pattern" => {
                cur = parent;
            }
            _ => return true,
        }
    }
    true
}

pub(crate) fn scope_nodes(root: Node) -> Vec<Node> {
    fn walk<'t>(n: Node<'t>, out: &mut Vec<Node<'t>>) {
        out.push(n);
        if !["function_definition", "class_definition", "lambda"].contains(&n.kind()) {
            for c in named_children(n) { walk(c, out); }
        }
    }
    let mut out = Vec::new();
    if let Some(body) = field(root, "body") {
        walk(body, &mut out);
    } else {
        for c in named_children(root) { walk(c, &mut out); }
    }
    out
}
