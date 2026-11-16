//! Pass 1: the file index, the counterpart of src/python/rules/index.py.
use std::collections::{HashMap, HashSet};

use tree_sitter::Node;

use crate::syntax::*;
use crate::tables::*;

// ---------------------------------------------------------------- pass 1: index
#[derive(Clone, Copy)]
pub(crate) struct ClassInfo<'t> {
    pub(crate) needs_slots: bool,
    pub(crate) cycle_edge: Option<Node<'t>>,
}

#[derive(Clone)]
pub(crate) struct Index<'t> {
    pub(crate) imports: HashMap<String, String>,
    pub(crate) functions: HashMap<String, Node<'t>>,
    pub(crate) classes: HashMap<String, ClassInfo<'t>>,
}

pub(crate) fn cycle_edge<'t>(cls: Node<'t>, src: &[u8]) -> Option<Node<'t>> {
    let mut found = None;
    for_each_node(cls, |n| {
        if found.is_some() {
            return;
        }
        let edge = match n.kind() {
            "assignment" => {
                let (Some(left), Some(right)) = (field(n, "left"), field(n, "right")) else { return };
                if left.kind() != "attribute" || is_weakref_call(right, src) {
                    return;
                }
                let obj = field(left, "object");
                let attr = attr_name(left, src);
                let back_edge = is_self(Some(right), src) && !is_self(obj, src);
                let link_field = is_self(obj, src) && LINK_ATTRS.contains(&attr)
                    && !LITERALS.contains(&right.kind());
                back_edge || link_field
            }
            "call" => {
                let a = field(n, "function").map_or("", |f| attr_name(f, src));
                ["append", "add", "insert"].contains(&a)
                    && call_args(n).0.iter().any(|x| is_self(Some(*x), src))
            }
            _ => false,
        };
        if edge {
            found = Some(n);
        }
    });
    found
}

pub(crate) fn needs_slots(cls: Node, src: &[u8], imports: &HashMap<String, String>) -> bool {
    let mut dynamic_attributes = false;
    for_each_node(cls, |n| {
        if n.kind() == "attribute" && attr_name(n, src) == "__dict__"
            && is_self(field(n, "object"), src)
        {
            dynamic_attributes = true;
        }
        if n.kind() == "call" && field(n, "function")
            .and_then(|f| qualname(f, src, imports))
            .is_some_and(|q| q == "setattr" || q == "builtins.setattr")
            && call_args(n).0.first().is_some_and(|a| is_self(Some(*a), src))
        {
            dynamic_attributes = true;
        }
    });
    if dynamic_attributes { return false; }
    if let Some(body) = field(cls, "body") {
        for stmt in named_children(body) {
            if stmt.kind() == "expression_statement" {
                if let Some(a) = stmt.named_child(0).filter(|a| a.kind() == "assignment") {
                    if field(a, "left").is_some_and(|l| l.kind() == "identifier" && txt(l, src) == "__slots__") {
                        return false;
                    }
                }
            }
        }
    }
    for d in decorators(cls) {
        if d.kind() == "call"
            && field(d, "function")
                .and_then(|f| qualname(f, src, imports))
                .is_some_and(|q| q.ends_with("dataclass"))
        {
            let (_, kws) = call_args(d);
            if kwarg(&kws, "slots", src).is_some_and(|v| v.kind() == "true") {
                return false;
            }
        }
    }
    if let Some(sup) = field(cls, "superclasses") {
        for b in named_children(sup).into_iter().filter(|b| b.kind() != "keyword_argument") {
            let name = match b.kind() {
                "attribute" => attr_name(b, src),
                "identifier" => txt(b, src),
                _ => "",
            };
            if SLOTS_EXEMPT_BASES.contains(&name)
                || name.ends_with("Error") || name.ends_with("Exception") || name.ends_with("Warning")
            {
                return false;
            }
        }
    }
    true
}

pub(crate) fn target_names(n: Node, src: &[u8], names: &mut HashSet<String>) {
    if n.kind() == "identifier" {
        names.insert(txt(n, src).to_string());
    } else if ["pattern_list", "tuple_pattern", "list_pattern", "list_splat_pattern",
               "as_pattern_target", "tuple", "list"].contains(&n.kind()) {
        for c in named_children(n) { target_names(c, src, names); }
    }
}

pub(crate) fn bound_names(root: Node, src: &[u8]) -> HashSet<String> {
    let mut names: HashSet<String> = param_names(root, src).into_iter().collect();
    let mut external = HashSet::new();
    for n in scope_nodes(root) {
        match n.kind() {
            "function_definition" | "class_definition" => {
                if let Some(name) = field(n, "name") { names.insert(txt(name, src).to_string()); }
            }
            "assignment" | "augmented_assignment" | "for_statement" => {
                if let Some(left) = field(n, "left") { target_names(left, src, &mut names); }
            }
            "named_expression" => {
                if let Some(name) = field(n, "name") { target_names(name, src, &mut names); }
            }
            "as_pattern" => {
                if let Some(alias) = field(n, "alias") { target_names(alias, src, &mut names); }
            }
            "delete_statement" => {
                for c in named_children(n) { target_names(c, src, &mut names); }
            }
            "global_statement" | "nonlocal_statement" => {
                for c in named_children(n) { target_names(c, src, &mut external); }
            }
            _ => {}
        }
    }
    names.extend(index(root, src).imports.into_keys());
    names.retain(|name| !external.contains(name));
    names
}

pub(crate) fn index<'t>(root: Node<'t>, src: &[u8]) -> Index<'t> {
    index_with_imports(root, src, HashMap::new())
}

pub(crate) fn index_with_imports<'t>(root: Node<'t>, src: &[u8], mut imports: HashMap<String, String>) -> Index<'t> {
    for n in scope_nodes(root) { match n.kind() {
        "import_statement" => {
            for c in named_children(n) {
                match c.kind() {
                    "dotted_name" => {
                        let top = txt(c, src).split('.').next().unwrap_or("").to_string();
                        imports.insert(top.clone(), top);
                    }
                    "aliased_import" => {
                        if let (Some(name), Some(alias)) = (field(c, "name"), field(c, "alias")) {
                            imports.insert(txt(alias, src).to_string(), txt(name, src).to_string());
                        }
                    }
                    _ => {}
                }
            }
        }
        "import_from_statement" => {
            let Some(m) = field(n, "module_name").filter(|m| m.kind() == "dotted_name") else { continue };
            let module = txt(m, src);
            let mut c = n.walk();
            for name in n.children_by_field_name("name", &mut c) {
                let (orig, local) = match name.kind() {
                    "aliased_import" => (
                        field(name, "name").map_or("", |x| txt(x, src)),
                        field(name, "alias").map_or("", |x| txt(x, src)),
                    ),
                    _ => (txt(name, src), txt(name, src)),
                };
                imports.insert(local.to_string(), format!("{module}.{orig}"));
            }
        }
        _ => {}
    }}
    let mut functions = HashMap::new();
    let mut classes = HashMap::new();
    for n in scope_nodes(root) { match n.kind() {
        "function_definition" => {
            if let Some(name) = field(n, "name") {
                functions.insert(txt(name, src).to_string(), n);
            }
        }
        "class_definition" => {
            if let Some(name) = field(n, "name") {
                classes.insert(txt(name, src).to_string(), ClassInfo {
                    needs_slots: needs_slots(n, src, &imports),
                    cycle_edge: cycle_edge(n, src),
                });
            }
        }
        _ => {}
    }}
    Index { imports, functions, classes }
}
