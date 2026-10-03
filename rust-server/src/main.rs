//! Python Memory Guardian - Rust language server (tower-lsp + tree-sitter).
//!
//! Mirrors server/rules.py rule-for-rule. Message text comes from the shared
//! server/messages.json (embedded at compile time), and interpreter facts come
//! from server/probe.py, run by the VS Code client against the user's
//! configured interpreter and passed in initializationOptions.profile.
use std::collections::{HashMap, HashSet};
use std::sync::{Arc, OnceLock};
use std::time::Duration;

use serde_json::Value;
use tokio::sync::{Mutex, RwLock};
use tower_lsp::jsonrpc::Result;
use tower_lsp::lsp_types::*;
use tower_lsp::{Client, LanguageServer, LspService, Server};
use tree_sitter::{Node, Parser, Point};

const SOURCE: &str = "Python Memory Guardian";
const SUPPRESS: &str = "memory-guardian: ignore";
const DEBOUNCE: Duration = Duration::from_millis(350);
const W: DiagnosticSeverity = DiagnosticSeverity::WARNING;
const I: DiagnosticSeverity = DiagnosticSeverity::INFORMATION;

static MESSAGES_JSON: &str = include_str!("../../server/messages.json");
static MESSAGES: OnceLock<Value> = OnceLock::new();

type Facts = HashMap<String, String>;

// ---------------------------------------------------------------- rule tables (= rules.py)
const PANDAS_LOADERS: &[&str] = &["read_csv", "read_json", "read_table", "read_sql",
    "read_sql_query", "read_sql_table", "read_excel", "read_fwf"];
const LINK_ATTRS: &[&str] = &["parent", "prev", "previous", "next", "left", "right", "sibling",
    "owner", "back", "peer", "twin", "partner", "container", "root"];
const WEAKREF_CALLS: &[&str] = &["ref", "proxy", "WeakValueDictionary", "WeakKeyDictionary", "WeakSet"];
const LITERALS: &[&str] = &["none", "integer", "float", "string", "concatenated_string", "true", "false"];
const BLOCKING_CALLS: &[&str] = &["time.sleep", "requests.get", "requests.post", "requests.put",
    "requests.patch", "requests.delete", "requests.head", "requests.request",
    "urllib.request.urlopen", "subprocess.run", "subprocess.call", "subprocess.check_call",
    "subprocess.check_output", "socket.create_connection"];
const AGGREGATORS: &[&str] = &["sum", "any", "all", "min", "max"];
const THREAD_CTORS: &[&str] = &["threading.Thread"];
const EXECUTOR_CTORS: &[&str] = &["concurrent.futures.ThreadPoolExecutor",
    "multiprocessing.pool.ThreadPool", "multiprocessing.dummy.Pool"];
const EXECUTOR_METHODS: &[&str] = &["submit", "map", "apply", "apply_async", "imap",
    "imap_unordered", "starmap"];
const IO_PREFIXES: &[&str] = &["requests.", "urllib.", "http.", "socket.", "subprocess.", "httpx.",
    "boto3.", "ftplib.", "smtplib.", "sqlite3.", "psycopg2.", "pymysql.", "shutil."];
const IO_NAMES: &[&str] = &["open", "input", "time.sleep"];
const IO_ATTRS: &[&str] = &["read", "readline", "readlines", "write", "writelines", "recv",
    "recv_into", "send", "sendall", "connect", "execute", "executemany", "fetchone", "fetchmany",
    "fetchall", "commit", "urlopen", "read_text", "read_bytes", "write_text", "write_bytes",
    "download_file", "upload_file", "get_object", "put_object"];
const NATIVE_PREFIXES: &[&str] = &["numpy.", "polars.", "pyarrow.", "hashlib.", "zlib.", "bz2.",
    "lzma.", "duckdb.", "scipy.", "numexpr."];
const SLOTS_EXEMPT_BASES: &[&str] = &["NamedTuple", "Enum", "IntEnum", "StrEnum", "Flag",
    "IntFlag", "TypedDict", "Protocol", "Exception", "BaseException"];
const COMPREHENSIONS: &[&str] = &["list_comprehension", "set_comprehension",
    "dictionary_comprehension", "generator_expression"];

// ---------------------------------------------------------------- message rendering
fn placeholders(t: &str) -> Vec<String> {
    let mut out = Vec::new();
    let b = t.as_bytes();
    let mut i = 0;
    while i < b.len() {
        if b[i] == b'{' {
            let start = i + 1;
            let mut j = start;
            while j < b.len() && (b[j].is_ascii_lowercase() || b[j].is_ascii_digit() || b[j] == b'_') {
                j += 1;
            }
            if j > start && j < b.len() && b[j] == b'}' {
                out.push(t[start..j].to_string());
                i = j + 1;
                continue;
            }
        }
        i += 1;
    }
    out
}

fn fill(t: &str, vals: &HashMap<String, String>) -> String {
    let mut s = t.to_string();
    for p in placeholders(t) {
        if let Some(v) = vals.get(&p) {
            s = s.replace(&format!("{{{p}}}"), v);
        }
    }
    s
}

/// 'sized' text only when every placeholder is known, otherwise 'neutral' (= rules.render).
fn render(key: &str, facts: &Facts, local: &[(&str, String)]) -> String {
    let msgs = MESSAGES.get_or_init(|| serde_json::from_str(MESSAGES_JSON).expect("messages.json"));
    let mut vals: HashMap<String, String> = facts.clone();
    for (k, v) in local {
        vals.insert((*k).to_string(), v.clone());
    }
    let m = &msgs[key];
    if let Some(sized) = m["sized"].as_str() {
        if placeholders(sized).iter().all(|p| vals.contains_key(p)) {
            return fill(sized, &vals);
        }
    }
    fill(m["neutral"].as_str().unwrap_or(key), &vals)
}

// ---------------------------------------------------------------- tree helpers
fn txt<'a>(n: Node, src: &'a [u8]) -> &'a str {
    n.utf8_text(src).unwrap_or("")
}

fn field<'t>(n: Node<'t>, f: &str) -> Option<Node<'t>> {
    n.child_by_field_name(f)
}

/// Strip redundant parentheses, as CPython's ast does.
fn unwrap(mut n: Node) -> Node {
    while n.kind() == "parenthesized_expression" {
        match n.named_child(0) {
            Some(c) => n = c,
            None => break,
        }
    }
    n
}

fn for_each_node<'t>(root: Node<'t>, mut f: impl FnMut(Node<'t>)) {
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
fn is_name_ref(n: Node) -> bool {
    if n.kind() != "identifier" {
        return false;
    }
    match n.parent() {
        Some(p) if p.kind() == "attribute" => field(p, "attribute").map(|a| a.id()) != Some(n.id()),
        Some(p) if p.kind() == "keyword_argument" => field(p, "name").map(|a| a.id()) != Some(n.id()),
        _ => true,
    }
}

fn is_self(n: Option<Node>, src: &[u8]) -> bool {
    n.map_or(false, |n| n.kind() == "identifier" && txt(n, src) == "self")
}

fn named_children(n: Node) -> Vec<Node> {
    let mut c = n.walk();
    n.named_children(&mut c).filter(|x| x.kind() != "comment").collect()
}

/// Positional args (= ast Call.args) and keyword args of a call.
fn call_args(call: Node) -> (Vec<Node>, Vec<Node>) {
    match field(call, "arguments") {
        Some(a) if a.kind() == "generator_expression" => (vec![a], vec![]),
        Some(a) => named_children(a).into_iter().partition(|x| x.kind() != "keyword_argument"),
        None => (vec![], vec![]),
    }
}

fn kwarg<'t>(kws: &[Node<'t>], name: &str, src: &[u8]) -> Option<Node<'t>> {
    kws.iter()
        .find(|k| field(**k, "name").map_or(false, |n| txt(n, src) == name))
        .and_then(|k| field(*k, "value"))
}

fn qualname(n: Node, src: &[u8], imports: &HashMap<String, String>) -> Option<String> {
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

fn attr_name<'a>(f: Node, src: &'a [u8]) -> &'a str {
    if f.kind() == "attribute" { field(f, "attribute").map_or("", |a| txt(a, src)) } else { "" }
}

fn is_bytes_literal(n: Node, src: &[u8]) -> bool {
    n.child(0).map_or(false, |s| txt(s, src).to_ascii_lowercase().contains('b'))
}

fn is_strish(n: Node, src: &[u8]) -> bool {
    let n = unwrap(n);
    match n.kind() {
        "string" => !is_bytes_literal(n, src),
        "concatenated_string" => n.named_child(0).map_or(false, |s| !is_bytes_literal(s, src)),
        "call" => field(n, "function").map_or(false, |f| f.kind() == "identifier" && txt(f, src) == "str"),
        "binary_operator" => {
            field(n, "operator").map_or(false, |o| txt(o, src) == "+")
                && (field(n, "left").map_or(false, |l| is_strish(l, src))
                    || field(n, "right").map_or(false, |r| is_strish(r, src)))
        }
        _ => false,
    }
}

fn is_listish(n: Node, src: &[u8]) -> bool {
    let n = unwrap(n);
    matches!(n.kind(), "list" | "list_comprehension")
        || (n.kind() == "call"
            && field(n, "function").map_or(false, |f| f.kind() == "identifier" && txt(f, src) == "list"))
}

fn is_weakref_call(n: Node, src: &[u8]) -> bool {
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

fn is_async(fn_node: Node) -> bool {
    fn_node.child(0).map_or(false, |c| c.kind() == "async")
}

fn param_names(fn_node: Node, src: &[u8]) -> Vec<String> {
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

fn decorators(def: Node) -> Vec<Node> {
    match def.parent() {
        Some(p) if p.kind() == "decorated_definition" => named_children(p)
            .into_iter()
            .filter(|d| d.kind() == "decorator")
            .filter_map(|d| d.named_child(0))
            .collect(),
        _ => vec![],
    }
}

/// Function defined directly in a class body (= rules.py `_pmg_method`).
fn is_method(def: Node) -> bool {
    let mut p = def.parent();
    if p.map_or(false, |x| x.kind() == "decorated_definition") {
        p = p.and_then(|x| x.parent());
    }
    p.filter(|x| x.kind() == "block")
        .and_then(|b| b.parent())
        .map_or(false, |c| c.kind() == "class_definition")
}

/// `self.buf` / `cur` / `pd.read_csv` as written (= rules.dotted); None otherwise.
fn dotted(n: Node, src: &[u8]) -> Option<String> {
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

/// Qualified enclosing scope, e.g. "Repo.lookup()" or "Repo" (= rules.scope_label).
fn scope_label(n: Node, src: &[u8]) -> Option<String> {
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
fn prefix(scope: &Option<String>, subject: &Option<String>) -> String {
    let subj = subject.as_ref().map(|s| format!("`{s}`"));
    let parts: Vec<&str> = [scope.as_deref(), subj.as_deref()].into_iter().flatten().collect();
    if parts.is_empty() { String::new() } else { format!("{} — ", parts.join(" › ")) }
}

// ---------------------------------------------------------------- pass 1: index
#[derive(Clone, Copy)]
struct ClassInfo<'t> {
    needs_slots: bool,
    cycle_edge: Option<Node<'t>>,
}

#[derive(Clone)]
struct Index<'t> {
    imports: HashMap<String, String>,
    functions: HashMap<String, Node<'t>>,
    classes: HashMap<String, ClassInfo<'t>>,
}

fn cycle_edge<'t>(cls: Node<'t>, src: &[u8]) -> Option<Node<'t>> {
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

fn needs_slots(cls: Node, src: &[u8], imports: &HashMap<String, String>) -> bool {
    if let Some(body) = field(cls, "body") {
        for stmt in named_children(body) {
            if stmt.kind() == "expression_statement" {
                if let Some(a) = stmt.named_child(0).filter(|a| a.kind() == "assignment") {
                    if field(a, "left").map_or(false, |l| l.kind() == "identifier" && txt(l, src) == "__slots__") {
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
                .map_or(false, |q| q.ends_with("dataclass"))
        {
            let (_, kws) = call_args(d);
            if kwarg(&kws, "slots", src).map_or(false, |v| v.kind() == "true") {
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

fn scope_nodes(root: Node) -> Vec<Node> {
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

fn target_names(n: Node, src: &[u8], names: &mut HashSet<String>) {
    if n.kind() == "identifier" {
        names.insert(txt(n, src).to_string());
    } else if ["pattern_list", "tuple_pattern", "list_pattern", "list_splat_pattern",
               "as_pattern_target", "tuple", "list"].contains(&n.kind()) {
        for c in named_children(n) { target_names(c, src, names); }
    }
}

fn bound_names(root: Node, src: &[u8]) -> HashSet<String> {
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

fn index<'t>(root: Node<'t>, src: &[u8]) -> Index<'t> {
    index_with_imports(root, src, HashMap::new())
}

fn index_with_imports<'t>(root: Node<'t>, src: &[u8], mut imports: HashMap<String, String>) -> Index<'t> {
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

// ---------------------------------------------------------------- thread target classification
fn scoped_index<'t>(root: Node<'t>, src: &[u8], parent: &Index<'t>) -> Index<'t> {
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
struct WorkProfile<'t> {
    io: bool,
    native: bool,
    data_loop: Option<Node<'t>>,
}

fn classify<'t>(fn_node: Node<'t>, idx: &Index<'t>, src: &[u8], seen: &mut HashSet<String>) -> WorkProfile<'t> {
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

// ---------------------------------------------------------------- pass 2: visitor
struct Finding {
    start: Point,
    end: Point,
    key: &'static str,
    sev: DiagnosticSeverity,
    local: Vec<(&'static str, String)>,
    related: Vec<(Point, Point, String)>,
    scope: Option<String>,
    subject: Option<String>,
}

#[derive(Clone)]
struct ScopeState<'t> {
    idx: Index<'t>,
    strings: HashSet<String>,
    lists: HashSet<String>,
    executors: HashSet<String>,
    loop_depth: usize,
    class_parent: Option<Box<ScopeState<'t>>>,
}

struct Visitor<'t, 's> {
    src: &'s [u8],
    idx: Index<'t>,
    facts: &'s Facts,
    out: Vec<Finding>,
    loop_depth: usize,
    async_stack: Vec<bool>,
    reported_cycles: HashSet<String>,
    str_names: HashSet<String>,
    list_names: HashSet<String>,
    executors: HashSet<String>,
    class_parent: Option<Box<ScopeState<'t>>>,
}

impl<'t, 's> Visitor<'t, 's> {
    fn state(&self) -> ScopeState<'t> {
        ScopeState { idx: self.idx.clone(), strings: self.str_names.clone(),
            lists: self.list_names.clone(), executors: self.executors.clone(),
            loop_depth: self.loop_depth, class_parent: self.class_parent.clone() }
    }

    fn restore(&mut self, s: ScopeState<'t>) {
        self.idx = s.idx;
        self.str_names = s.strings;
        self.list_names = s.lists;
        self.executors = s.executors;
        self.loop_depth = s.loop_depth;
        self.class_parent = s.class_parent;
    }

    fn enter_scope(&mut self, n: Node<'t>, base: ScopeState<'t>) {
        self.restore(base);
        let bound = bound_names(n, self.src);
        self.idx = scoped_index(n, self.src, &self.idx);
        for name in &bound {
            self.str_names.remove(name);
            self.list_names.remove(name);
            self.executors.remove(name);
        }
        self.loop_depth = 0;
        self.class_parent = None;
    }

    fn add(&mut self, n: Node, key: &'static str, sev: DiagnosticSeverity, local: Vec<(&'static str, String)>) {
        self.add_s(n, key, sev, local, None);
    }

    fn add_s(&mut self, n: Node, key: &'static str, sev: DiagnosticSeverity,
             local: Vec<(&'static str, String)>, subject: Option<String>) {
        let scope = scope_label(n, self.src);
        self.out.push(Finding { start: n.start_position(), end: n.end_position(), key, sev, local,
            related: vec![], scope, subject });
    }

    fn q(&self, n: Node) -> String {
        qualname(n, self.src, &self.idx.imports).unwrap_or_default()
    }

    fn children(&mut self, n: Node<'t>) {
        for c in named_children(n) {
            self.visit(c);
        }
    }

    fn visit(&mut self, n: Node<'t>) {
        let src = self.src;
        match n.kind() {
            "function_definition" => {
                self.check_cache_decorators(n);
                if let Some(params) = field(n, "parameters") { self.visit(params); }
                let saved = self.state();
                let base = self.class_parent.as_deref().cloned().unwrap_or_else(|| saved.clone());
                self.enter_scope(n, base);
                self.async_stack.push(is_async(n));
                if let Some(body) = field(n, "body") { self.visit(body); }
                self.async_stack.pop();
                self.restore(saved);
            }
            "lambda" => {
                if let Some(params) = field(n, "parameters") { self.visit(params); }
                let saved = self.state();
                let base = self.class_parent.as_deref().cloned().unwrap_or_else(|| saved.clone());
                self.enter_scope(n, base);
                self.async_stack.push(false);
                if let Some(body) = field(n, "body") { self.visit(body); }
                self.async_stack.pop();
                self.restore(saved);
            }
            "class_definition" => {
                if let Some(bases) = field(n, "superclasses") { self.visit(bases); }
                let saved = self.state();
                self.enter_scope(n, saved.clone());
                self.loop_depth = saved.loop_depth; // Class bodies execute immediately.
                self.class_parent = Some(saved.class_parent.clone().unwrap_or_else(|| Box::new(saved.clone())));
                if let Some(body) = field(n, "body") { self.visit(body); }
                self.restore(saved);
            }
            "for_statement" => {
                if let Some(it) = field(n, "right") {
                    let it = unwrap(it);
                    let execute = it.kind() == "call"
                        && field(it, "function").map_or(false, |f| attr_name(f, src) == "execute");
                    let cursor = it.kind() == "identifier"
                        && ["cursor", "cur", "rows_cursor"].contains(&txt(it, src).to_lowercase().as_str());
                    if execute || cursor {
                        let subj = if it.kind() == "call" {
                            field(it, "function").and_then(|f| field(f, "object")).and_then(|o| dotted(o, src))
                        } else {
                            Some(txt(it, src).to_string())
                        };
                        self.add_s(it, "heap-inflation", W, vec![], subj);
                    }
                    let loop_var = field(n, "left").and_then(|l| dotted(l, src));
                    let list_call = it.kind() == "call"
                        && field(it, "function").map_or(false, |f| f.kind() == "identifier" && txt(f, src) == "list")
                        && !call_args(it).0.is_empty();
                    if list_call {
                        self.add_s(it, "memory-swell.list-copy", W, vec![("call", "list(...)".into())], loop_var);
                    } else if it.kind() == "list_comprehension" {
                        self.add_s(it, "memory-swell.list-copy", W, vec![("call", "a list comprehension".into())], loop_var);
                    }
                }
                for f in ["left", "right"] {
                    if let Some(c) = field(n, f) {
                        self.visit(c);
                    }
                }
                self.loop_body(n);
            }
            "while_statement" => {
                if let Some(c) = field(n, "condition") {
                    self.visit(c);
                }
                self.loop_body(n);
            }
            k if COMPREHENSIONS.contains(&k) => {
                self.loop_depth += 1;
                self.children(n);
                self.loop_depth -= 1;
            }
            "assignment" => {
                if let Some(r) = field(n, "right") { self.visit(r); }
                if let (Some(l), Some(r), None) = (field(n, "left"), field(n, "right"), field(n, "type")) {
                    if l.kind() == "identifier" {
                        let name = txt(l, src).to_string();
                        if is_strish(r, src) { self.str_names.insert(name.clone()); } else { self.str_names.remove(&name); }
                        if is_listish(r, src) { self.list_names.insert(name.clone()); } else { self.list_names.remove(&name); }
                        let r = unwrap(r);
                        if r.kind() == "call"
                            && field(r, "function").map_or(false, |f| EXECUTOR_CTORS.contains(&self.q(f).as_str()))
                        {
                            self.executors.insert(name.clone());
                        } else {
                            self.executors.remove(&name);
                        }
                        self.idx.imports.insert(name.clone(), "<local>".into());
                        self.idx.functions.remove(&name);
                        self.idx.classes.remove(&name);
                    }
                }
                if let Some(l) = field(n, "left") { self.visit(l); }
            }
            "with_item" => {
                if let Some(v) = field(n, "value").filter(|v| v.kind() == "as_pattern") {
                    let call = v.named_child(0).filter(|c| c.kind() == "call");
                    let alias = field(v, "alias").and_then(|a| a.named_child(0)).filter(|a| a.kind() == "identifier");
                    if let (Some(call), Some(alias)) = (call, alias) {
                        if field(call, "function").map_or(false, |f| EXECUTOR_CTORS.contains(&self.q(f).as_str())) {
                            self.executors.insert(txt(alias, src).to_string());
                        }
                    }
                }
                self.children(n);
            }
            "augmented_assignment" => {
                let plus = field(n, "operator").map_or(false, |o| txt(o, src) == "+=");
                if self.loop_depth > 0 && plus {
                    let l = field(n, "left");
                    let named_str = l.map_or(false, |l| l.kind() == "identifier" && self.str_names.contains(txt(l, src)));
                    if named_str || field(n, "right").map_or(false, |r| is_strish(r, src)) {
                        let subj = l.and_then(|l| dotted(l, src));
                        self.add_s(n, "text-inflation.concat", W, vec![], subj);
                    }
                }
                self.children(n);
            }
            "comparison_operator" => {
                if self.loop_depth > 0 {
                    let kids: Vec<Node> = (0..n.child_count()).filter_map(|i| n.child(i)).collect();
                    for (i, k) in kids.iter().enumerate() {
                        if k.kind() == "in" || k.kind() == "not in" {
                            if let Some(r) = kids[i + 1..].iter().find(|x| x.is_named() && x.kind() != "comment") {
                                let r = unwrap(*r);
                                if r.kind() == "identifier" && self.list_names.contains(txt(r, src)) {
                                    let name = txt(r, src).to_string();
                                    self.add_s(n, "single-thread-stall.list-membership", W,
                                               vec![("name", name.clone())], Some(name));
                                }
                            }
                        }
                    }
                }
                self.children(n);
            }
            "import_statement" => {
                let hit = named_children(n).iter().any(|c| {
                    let name = if c.kind() == "aliased_import" { field(*c, "name") } else { Some(*c) };
                    name.map_or(false, |x| txt(x, src) == "pandas")
                });
                if hit {
                    self.add(n, "pointer-chasing", W, vec![]);
                }
            }
            "import_from_statement" => {
                if field(n, "module_name").map_or(false, |m| txt(m, src) == "pandas") {
                    self.add(n, "pointer-chasing", W, vec![]);
                }
            }
            "call" => {
                self.visit_call(n);
                self.children(n);
            }
            _ => self.children(n),
        }
    }

    fn loop_body(&mut self, n: Node<'t>) {
        self.loop_depth += 1;
        if let Some(b) = field(n, "body") {
            self.visit(b);
        }
        self.loop_depth -= 1;
        if let Some(a) = field(n, "alternative") {
            self.visit(a);
        }
    }

    fn check_cache_decorators(&mut self, def: Node<'t>) {
        let src = self.src;
        for d in decorators(def) {
            let unbounded = self.q(d) == "functools.cache"
                || (d.kind() == "call"
                    && field(d, "function").map_or(false, |f| self.q(f) == "functools.lru_cache")
                    && {
                        let (args, kws) = call_args(d);
                        args.first().map_or(false, |a| a.kind() == "none")
                            || kwarg(&kws, "maxsize", src).map_or(false, |v| v.kind() == "none")
                    });
            if !unbounded {
                continue;
            }
            let first_self = param_names(def, src).first().map_or(false, |p| p == "self");
            let fname = field(def, "name").map(|x| txt(x, src).to_string());
            if is_method(def) && first_self {
                self.add_s(d, "memory-swell.method-cache", W, vec![], fname);
            } else {
                self.add_s(d, "memory-swell.unbounded-cache", I, vec![], fname);
            }
        }
    }

    fn visit_call(&mut self, n: Node<'t>) {
        let src = self.src;
        let Some(f) = field(n, "function") else { return };
        let q = self.q(f);
        let (args, kws) = call_args(n);
        if f.kind() == "attribute" {
            let a = attr_name(f, src);
            if let Some(an) = field(f, "attribute") {
                let recv = field(f, "object").and_then(|o| dotted(o, src));
                if a == "fetchall" || (a == "fetchone" && self.loop_depth > 0) {
                    self.add_s(an, "heap-inflation", W, vec![], recv.clone());
                }
                if a == "readlines" && args.is_empty() {
                    self.add_s(an, "text-inflation.whole-file", W, vec![("call", "readlines()".into())], recv.clone());
                }
                if ["split", "splitlines", "rsplit"].contains(&a) {
                    let inner = field(f, "object").map(unwrap).filter(|o| o.kind() == "call");
                    let is_read = inner.map_or(false, |c| {
                        field(c, "function").map_or(false, |cf| attr_name(cf, src) == "read")
                            && call_args(c).0.is_empty()
                    });
                    if is_read {
                        let subj = inner.and_then(|c| field(c, "function"))
                            .and_then(|cf| field(cf, "object")).and_then(|o| dotted(o, src));
                        self.add_s(an, "text-inflation.whole-file", W, vec![("call", format!("read().{a}()"))], subj);
                    }
                }
            }
            if a == "append" && self.loop_depth > 0 && args.len() == 1 {
                let arg = unwrap(args[0]);
                let container = matches!(arg.kind(), "dictionary" | "tuple" | "list")
                    || (arg.kind() == "call"
                        && field(arg, "function").map_or(false, |x| x.kind() == "identifier" && txt(x, src) == "dict"));
                if container {
                    let subj = field(f, "object").and_then(|o| dotted(o, src));
                    self.add_s(arg, "ram-fragmentation.append", W, vec![], subj);
                }
            }
            if EXECUTOR_METHODS.contains(&a) && !args.is_empty() {
                let obj = field(f, "object").map(unwrap);
                let is_exec = obj.map_or(false, |o| {
                    (o.kind() == "identifier" && self.executors.contains(txt(o, src)))
                        || (o.kind() == "call"
                            && field(o, "function").map_or(false, |of| EXECUTOR_CTORS.contains(&self.q(of).as_str())))
                });
                if is_exec {
                    self.check_thread_target(args[0]);
                }
            }
        }
        if q.starts_with("pandas.") && PANDAS_LOADERS.contains(&q.rsplit('.').next().unwrap_or("")) {
            let at = if f.kind() == "attribute" { field(f, "attribute").unwrap_or(f) } else { f };
            self.add_s(at, "pointer-chasing", W, vec![], dotted(f, src));
        }
        if *self.async_stack.last().unwrap_or(&false) && BLOCKING_CALLS.contains(&q.as_str()) {
            self.add(f, "single-thread-stall.async-blocking", W, vec![("call", format!("{q}()"))]);
        }
        if THREAD_CTORS.contains(&q.as_str()) {
            if let Some(t) = kwarg(&kws, "target", src) {
                self.check_thread_target(t);
            }
        }
        if f.kind() == "identifier" {
            let name = txt(f, src).to_string();
            if AGGREGATORS.contains(&name.as_str()) && args.len() == 1 && args[0].kind() == "list_comprehension" {
                self.add(args[0], "memory-swell.list-arg", W, vec![("call", name.clone())]);
            }
            if let Some(info) = self.idx.classes.get(&name).copied() {
                if self.loop_depth > 0 {
                    if info.needs_slots {
                        self.add_s(f, "ram-fragmentation.no-slots", I, vec![("name", name.clone())], Some(name.clone()));
                    }
                    if let Some(edge) = info.cycle_edge {
                        let (es, ee) = (edge.start_position(), edge.end_position());
                        self.out.push(Finding {
                            start: f.start_position(), end: f.end_position(), key: "cyclic-reference", sev: I,
                            local: vec![], related: vec![(es, ee, format!("Cycle edge created here in class {name}"))],
                            scope: scope_label(f, src), subject: Some(name.clone()),
                        });
                        if self.reported_cycles.insert(name.clone()) {
                            self.out.push(Finding {
                                start: es, end: ee, key: "cyclic-reference", sev: I, local: vec![],
                                related: vec![(f.start_position(), f.end_position(),
                                    format!("{name} is instantiated inside a loop here"))],
                                scope: scope_label(edge, src), subject: Some(name.clone()),
                            });
                        }
                    }
                }
            }
        }
    }

    fn check_thread_target(&mut self, target: Node<'t>) {
        if self.facts.get("gil_state").map(String::as_str) == Some("disabled") {
            return; // free-threaded runtime: threads do run Python code in parallel
        }
        let target = unwrap(target);
        if target.kind() != "identifier" {
            return; // unknown callable: we don't guess what it does
        }
        let name = txt(target, self.src).to_string();
        let Some(fn_node) = self.idx.functions.get(&name).copied() else { return };
        let prof = classify(fn_node, &self.idx, self.src, &mut HashSet::new());
        if let (Some(lp), false, false) = (prof.data_loop, prof.io, prof.native) {
            self.out.push(Finding {
                start: target.start_position(), end: target.end_position(),
                key: "single-thread-stall.cpu-thread", sev: I, local: vec![("func", name.clone())],
                related: vec![(lp.start_position(), lp.end_position(),
                    format!("{name}() iterates its input in Python here"))],
                scope: scope_label(target, self.src), subject: Some(name.clone()),
            });
        }
    }
}

// ---------------------------------------------------------------- positions & output
fn to_utf16(line: &str, byte_col: usize) -> u32 {
    let end = byte_col.min(line.len());
    let end = (0..=end).rev().find(|&i| line.is_char_boundary(i)).unwrap_or(0);
    line[..end].encode_utf16().count() as u32
}

fn lsp_range(lines: &[&str], s: Point, e: Point) -> Range {
    let line = |r: usize| lines.get(r).copied().unwrap_or("");
    let (er, ec) = if e.row != s.row { (s.row, line(s.row).len()) } else { (e.row, e.column) };
    Range::new(
        Position::new(s.row as u32, to_utf16(line(s.row), s.column)),
        Position::new(er as u32, to_utf16(line(er), ec)),
    )
}

fn analyze(text: &str, uri: &Url, facts: &Facts) -> Option<Vec<Diagnostic>> {
    let mut parser = Parser::new();
    parser.set_language(&tree_sitter_python::LANGUAGE.into()).ok()?;
    let tree = parser.parse(text, None)?;
    let root = tree.root_node();
    if root.has_error() {
        return None; // mid-typing: keep last good diagnostics
    }
    let src = text.as_bytes();
    let lines: Vec<&str> = text.lines().collect();
    let mut v = Visitor {
        src, idx: index(root, src), facts, out: vec![], loop_depth: 0, async_stack: vec![false],
        reported_cycles: HashSet::new(), str_names: HashSet::new(), list_names: HashSet::new(),
        executors: HashSet::new(), class_parent: None,
    };
    v.visit(root);

    let mut seen = HashSet::new();
    let mut diags = Vec::new();
    for fd in v.out {
        let range = lsp_range(&lines, fd.start, fd.end);
        let line = lines.get(range.start.line as usize).copied().unwrap_or("");
        if line.contains(SUPPRESS) || !seen.insert((range.start.line, range.start.character, fd.key)) {
            continue;
        }
        let related: Vec<DiagnosticRelatedInformation> = fd.related.into_iter()
            .map(|(s, e, m)| DiagnosticRelatedInformation {
                location: Location::new(uri.clone(), lsp_range(&lines, s, e)), message: m })
            .collect();
        diags.push(Diagnostic {
            range,
            severity: Some(fd.sev),
            code: Some(NumberOrString::String(fd.key.into())),
            source: Some(SOURCE.into()),
            message: prefix(&fd.scope, &fd.subject) + &render(fd.key, facts, &fd.local),
            related_information: if related.is_empty() { None } else { Some(related) },
            ..Default::default()
        });
    }
    diags.sort_by_key(|d| (d.range.start.line, d.range.start.character));
    Some(diags)
}

// ---------------------------------------------------------------- LSP wiring
type Docs = Arc<Mutex<HashMap<Url, (i32, String)>>>;

struct Backend {
    client: Client,
    docs: Docs,
    facts: Arc<RwLock<Facts>>,
}

async fn publish(client: &Client, docs: &Docs, facts: &Arc<RwLock<Facts>>, uri: Url, expected: Option<i32>) {
    let Some((version, text)) = docs.lock().await.get(&uri).cloned() else { return };
    if expected.map_or(false, |v| v != version) {
        return; // superseded by a newer edit
    }
    let facts = facts.read().await.clone();
    if let Some(d) = analyze(&text, &uri, &facts) {
        client.publish_diagnostics(uri, d, Some(version)).await;
    }
}

#[tower_lsp::async_trait]
impl LanguageServer for Backend {
    async fn initialize(&self, p: InitializeParams) -> Result<InitializeResult> {
        // initializationOptions.profile = probe.py output; anything missing -> neutral text.
        if let Some(Value::Object(profile)) = p.initialization_options.as_ref().and_then(|o| o.get("profile")) {
            let mut f = self.facts.write().await;
            for (k, v) in profile {
                match v {
                    Value::String(s) => { f.insert(k.clone(), s.clone()); }
                    Value::Number(n) => { f.insert(k.clone(), n.to_string()); }
                    _ => {}
                }
            }
        }
        Ok(InitializeResult {
            capabilities: ServerCapabilities {
                text_document_sync: Some(TextDocumentSyncCapability::Options(TextDocumentSyncOptions {
                    open_close: Some(true),
                    change: Some(TextDocumentSyncKind::FULL),
                    save: Some(TextDocumentSyncSaveOptions::Supported(true)),
                    ..Default::default()
                })),
                ..Default::default()
            },
            server_info: Some(ServerInfo { name: "python-memory-guardian".into(), version: Some("1.1.0".into()) }),
        })
    }

    async fn shutdown(&self) -> Result<()> {
        Ok(())
    }

    async fn did_open(&self, p: DidOpenTextDocumentParams) {
        let d = p.text_document;
        self.docs.lock().await.insert(d.uri.clone(), (d.version, d.text));
        publish(&self.client, &self.docs, &self.facts, d.uri, None).await;
    }

    async fn did_change(&self, p: DidChangeTextDocumentParams) {
        let Some(change) = p.content_changes.into_iter().last() else { return };
        let (uri, version) = (p.text_document.uri, p.text_document.version);
        self.docs.lock().await.insert(uri.clone(), (version, change.text));
        let (client, docs, facts) = (self.client.clone(), self.docs.clone(), self.facts.clone());
        tokio::spawn(async move {
            tokio::time::sleep(DEBOUNCE).await;
            publish(&client, &docs, &facts, uri, Some(version)).await;
        });
    }

    async fn did_save(&self, p: DidSaveTextDocumentParams) {
        publish(&self.client, &self.docs, &self.facts, p.text_document.uri, None).await;
    }

    async fn did_close(&self, p: DidCloseTextDocumentParams) {
        self.docs.lock().await.remove(&p.text_document.uri);
        self.client.publish_diagnostics(p.text_document.uri, vec![], None).await;
    }
}

#[tokio::main]
async fn main() {
    let (service, socket) = LspService::new(|client| Backend {
        client, docs: Default::default(), facts: Default::default(),
    });
    Server::new(tokio::io::stdin(), tokio::io::stdout(), socket).serve(service).await;
}
