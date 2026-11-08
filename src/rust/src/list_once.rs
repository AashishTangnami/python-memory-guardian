//! Memory swell: a list used once, the counterpart of src/python/rules/list_once.py.
use std::collections::HashMap;

use tree_sitter::Node;

use crate::positions::*;
use crate::syntax::*;
use crate::visitor::*;

// ---------------------------------------------------------------- memory swell: a list used once
// Mirrors _list_once in src/python/rules/list_once.py: `name = list(x)` or `name = [comprehension]`, then `name` used
// exactly once, only to iterate it. Not reported when the list is used again, indexed, sliced, measured
// with len(), or rebound.
pub(crate) const ITERATING_CALLS: &[&str] = &["sum", "any", "all", "min", "max", "iter", "enumerate", "zip"];

pub(crate) fn same(a: Option<Node>, b: Node) -> bool {
    a.is_some_and(|a| a.id() == b.id())
}

/// An identifier that names a variable: not an attribute name (`obj.name`) or a keyword name (`f(name=1)`).
pub(crate) fn is_variable(n: Node) -> bool {
    match n.parent() {
        Some(p) if p.kind() == "attribute" => !same(field(p, "attribute"), n),
        Some(p) if p.kind() == "keyword_argument" => !same(field(p, "name"), n),
        _ => true,
    }
}

/// Whether a variable identifier binds the name here: assignment, for and comprehension targets, walrus,
/// with/except/match `as`, del, global/nonlocal, parameters, def/class names, and any name in an import.
pub(crate) fn is_binding(n: Node) -> bool {
    let mut cur = n;
    while let Some(p) = cur.parent() {
        match p.kind() {
            "pattern_list" | "tuple_pattern" | "list_pattern" | "list_splat_pattern" | "dictionary_splat_pattern"
            | "tuple" | "list" | "parenthesized_expression" | "as_pattern_target" | "dotted_name" => { cur = p; }
            "assignment" | "augmented_assignment" | "for_statement" | "for_in_clause" => return same(field(p, "left"), cur),
            "named_expression" => return same(field(p, "name"), cur),
            "as_pattern" | "except_clause" => return same(field(p, "alias"), cur),
            "default_parameter" | "typed_default_parameter" => return same(field(p, "name"), cur),
            "typed_parameter" => return !same(field(p, "type"), cur),
            "function_definition" | "class_definition" => return same(field(p, "name"), cur),
            "delete_statement" | "global_statement" | "nonlocal_statement" | "parameters" | "lambda_parameters"
            | "import_statement" | "import_from_statement" | "aliased_import" => return true,
            _ => return false,
        }
    }
    false
}

/// Loads of `name` in a scope's body (nested scopes included, so closures count) and how often it is bound.
pub(crate) fn occurrences<'t>(scope: Node<'t>, name: &str, src: &[u8]) -> (Vec<Node<'t>>, usize) {
    fn walk<'t>(n: Node<'t>, name: &str, src: &[u8], loads: &mut Vec<Node<'t>>, binds: &mut usize) {
        if n.kind() == "identifier" && txt(n, src) == name && is_variable(n) {
            if is_binding(n) { *binds += 1; } else { loads.push(n); }
        }
        for c in named_children(n) { walk(c, name, src, loads, binds); }
    }
    let (mut loads, mut binds) = (Vec::new(), 0);
    let body = if scope.kind() == "module" { Some(scope) } else { field(scope, "body") };
    if let Some(b) = body { walk(b, name, src, &mut loads, &mut binds); }
    (loads, binds)
}

pub(crate) fn call_name(call: Node, src: &[u8]) -> Option<String> {
    field(call, "function").filter(|f| f.kind() == "identifier").map(|f| txt(f, src).to_string())
}

/// Positional arguments of a call (keyword arguments and comments left out); a generator-expression argument
/// (`f(x for x in y)`) is the single argument.
pub(crate) fn positional_args(call: Node) -> Vec<Node> {
    match field(call, "arguments") {
        Some(a) if a.kind() == "generator_expression" => vec![a],
        Some(a) => named_children(a).into_iter().filter(|c| !["keyword_argument", "comment"].contains(&c.kind())).collect(),
        None => vec![],
    }
}

pub(crate) fn direct_iteration(n: Node, src: &[u8]) -> Option<String> {
    let p = n.parent()?;
    match p.kind() {
        "for_statement" if same(field(p, "right"), n) => Some("a for loop".into()),
        "for_in_clause" if same(field(p, "right"), n) => Some("a comprehension".into()),
        "argument_list" => {
            let call = p.parent().filter(|c| c.kind() == "call")?;
            let f = call_name(call, src)?;
            ITERATING_CALLS.contains(&f.as_str()).then(|| format!("{f}()"))
        }
        _ => None,
    }
}

/// Positional parameter names, in order, up to `*`, `*args` or `**kwargs`.
pub(crate) fn positional_params(func: Node, src: &[u8]) -> Vec<String> {
    let mut out = Vec::new();
    let Some(params) = field(func, "parameters") else { return out };
    for c in named_children(params) {
        match c.kind() {
            "identifier" => out.push(txt(c, src).to_string()),
            "default_parameter" | "typed_default_parameter" => {
                if let Some(nm) = field(c, "name").filter(|x| x.kind() == "identifier") { out.push(txt(nm, src).to_string()); } else { break; }
            }
            "typed_parameter" => match named_children(c).into_iter().find(|x| x.kind() == "identifier") {
                Some(nm) if !same(field(c, "type"), nm) => out.push(txt(nm, src).to_string()),
                _ => break,
            },
            "positional_separator" | "comment" => {}
            _ => break,
        }
    }
    out
}

pub(crate) fn single_use(n: Node, src: &[u8], funcs: &HashMap<String, Node>) -> Option<String> {
    if let Some(u) = direct_iteration(n, src) { return Some(u); }
    let args = n.parent().filter(|p| p.kind() == "argument_list")?;
    let call = args.parent().filter(|c| c.kind() == "call")?;
    let name = call_name(call, src)?;
    let func = *funcs.get(&name)?;
    let positional = positional_args(call);
    if positional.iter().any(|a| a.kind() == "list_splat") { return None; }
    let i = positional.iter().position(|a| a.id() == n.id())?;
    let param = positional_params(func, src).into_iter().nth(i)?;
    let (loads, binds) = occurrences(func, &param, src);
    if binds == 0 && loads.len() == 1 && direct_iteration(loads[0], src).is_some() {
        return Some(format!("{name}(), which only iterates it"));
    }
    None
}

pub(crate) fn list_once(root: Node, src: &[u8]) -> Vec<Finding> {
    let mut funcs: HashMap<String, Node> = HashMap::new();
    for c in named_children(root) {
        let def = if c.kind() == "decorated_definition" { field(c, "definition") } else { Some(c) };
        if let Some(d) = def.filter(|d| d.kind() == "function_definition") {
            if let Some(nm) = field(d, "name") { funcs.entry(txt(nm, src).to_string()).or_insert(d); }
        }
    }
    let mut scopes = vec![root];
    fn defs<'t>(n: Node<'t>, out: &mut Vec<Node<'t>>) {
        if n.kind() == "function_definition" { out.push(n); }
        for c in named_children(n) { defs(c, out); }
    }
    defs(root, &mut scopes);
    let mut out = Vec::new();
    for scope in scopes {
        for node in scope_nodes(scope) {
            if node.kind() != "assignment" || field(node, "type").is_some()
                || node.parent().is_some_and(|p| p.kind() == "assignment") { continue; }
            let (Some(left), Some(right)) = (field(node, "left"), field(node, "right")) else { continue };
            if left.kind() != "identifier" { continue; }
            let call = match right.kind() {
                "list_comprehension" => "a list comprehension",
                "call" if call_name(right, src).as_deref() == Some("list") => {
                    let a = positional_args(right);
                    let keywords = field(right, "arguments").is_some_and(|x| named_children(x).iter().any(|c| c.kind() == "keyword_argument"));
                    if a.len() != 1 || keywords || ["list_splat", "dictionary_splat"].contains(&a[0].kind()) { continue; }
                    "list(...)"
                }
                _ => continue,
            };
            let name = txt(left, src).to_string();
            let (loads, binds) = occurrences(scope, &name, src);
            if binds != 1 || loads.len() != 1 || loads[0].start_byte() <= node.start_byte() { continue; }
            // Used inside a nested function, which may run many times.
            let mut up = loads[0].parent();
            let mut nested = false;
            while let Some(p) = up {
                if p.id() == scope.id() { break; }
                if ["function_definition", "lambda", "class_definition"].contains(&p.kind()) { nested = true; break; }
                up = p.parent();
            }
            if nested { continue; }
            if let Some(use_) = single_use(loads[0], src, &funcs) {
                out.push(Finding { start: right.start_position(), end: right.end_position(), key: "memory-swell.list-once",
                    sev: I, local: vec![("name", name.clone()), ("call", call.into()), ("use", use_)], related: vec![],
                    scope: scope_label(right, src), subject: Some(name) });
            }
        }
    }
    out
}
