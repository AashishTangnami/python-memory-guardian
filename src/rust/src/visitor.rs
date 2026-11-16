//! Pass 2: the visitor, the counterpart of src/python/rules/visitor.py.
use std::collections::HashSet;

use tower_lsp::lsp_types::DiagnosticSeverity;
use tree_sitter::{Node, Point};

use crate::index::*;
use crate::messages::*;
use crate::positions::*;
use crate::syntax::*;
use crate::tables::*;
use crate::threads::*;

pub(crate) const W: DiagnosticSeverity = DiagnosticSeverity::WARNING;
pub(crate) const I: DiagnosticSeverity = DiagnosticSeverity::INFORMATION;

/// `lru_cache(None)` / `lru_cache(maxsize=None)` (= rules/visitor.py `_maxsize_none`).
fn maxsize_none(args: &[Node], kws: &[Node], src: &[u8]) -> bool {
    args.first().is_some_and(|a| a.kind() == "none")
        || kwarg(kws, "maxsize", src).is_some_and(|v| v.kind() == "none")
}

/// `Queue()` / `Queue(0)` / `Queue(maxsize=0)` (= rules/visitor.py `_maxsize_zero`).
fn maxsize_zero(args: &[Node], kws: &[Node], src: &[u8]) -> bool {
    if args.iter().any(|a| matches!(a.kind(), "list_splat" | "dictionary_splat")) {
        return false;
    }
    match args.first().copied().or_else(|| kwarg(kws, "maxsize", src)) {
        Some(size) => is_zero_literal(size, src),
        None => true,
    }
}

/// `set_start_method("fork")` / `get_context(method="fork")` (= rules/visitor.py `_names_fork`).
fn names_fork(args: &[Node], kws: &[Node], src: &[u8]) -> bool {
    args.first().copied().or_else(|| kwarg(kws, "method", src)).is_some_and(|m| is_str_literal(m, src, "fork"))
}

// ---------------------------------------------------------------- pass 2: visitor
pub(crate) struct Finding {
    pub(crate) start: Point,
    pub(crate) end: Point,
    pub(crate) key: &'static str,
    pub(crate) sev: DiagnosticSeverity,
    pub(crate) local: Vec<(&'static str, String)>,
    pub(crate) related: Vec<(Point, Point, String)>,
    pub(crate) scope: Option<String>,
    pub(crate) subject: Option<String>,
}

#[derive(Clone)]
pub(crate) struct ScopeState<'t> {
    pub(crate) idx: Index<'t>,
    pub(crate) strings: HashSet<String>,
    pub(crate) lists: HashSet<String>,
    pub(crate) executors: HashSet<String>,
    pub(crate) loop_depth: usize,
    pub(crate) class_parent: Option<Box<ScopeState<'t>>>,
}

pub(crate) struct Visitor<'t, 's> {
    pub(crate) src: &'s [u8],
    pub(crate) idx: Index<'t>,
    pub(crate) facts: &'s Facts,
    pub(crate) out: Vec<Finding>,
    pub(crate) loop_depth: usize,
    pub(crate) async_stack: Vec<bool>,
    pub(crate) reported_cycles: HashSet<String>,
    pub(crate) str_names: HashSet<String>,
    pub(crate) list_names: HashSet<String>,
    pub(crate) executors: HashSet<String>,
    pub(crate) class_parent: Option<Box<ScopeState<'t>>>,
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

    pub(crate) fn visit(&mut self, n: Node<'t>) {
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
                let finalizer = field(n, "body").is_some_and(|body| named_children(body).into_iter().any(|stmt| {
                    let def = if stmt.kind() == "decorated_definition" {
                        named_children(stmt).into_iter().find(|c| c.kind() == "function_definition")
                    } else { Some(stmt) };
                    def.is_some_and(|d| d.kind() == "function_definition"
                        && field(d, "name").is_some_and(|name| txt(name, src) == "__del__"))
                }));
                if finalizer && cycle_edge(n, src).is_some() {
                    let name = field(n, "name").map(|x| txt(x, src).to_string());
                    self.add_s(n, "gc-cycle-risk", I, vec![], name);
                    if let (Some(name), Some(last)) = (field(n, "name"), self.out.last_mut()) {
                        let name = txt(name, src);
                        last.scope = Some(last.scope.as_ref()
                            .map(|parent| format!("{parent}.{name}"))
                            .unwrap_or_else(|| name.to_string()));
                    }
                }
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
                    let loop_var = field(n, "left").and_then(|l| dotted(l, src));
                    let list_call = it.kind() == "call"
                        && field(it, "function").is_some_and(|f| f.kind() == "identifier" && txt(f, src) == "list")
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
                            && field(r, "function").is_some_and(|f| EXECUTOR_CTORS.contains(&self.q(f).as_str()))
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
                        if field(call, "function").is_some_and(|f| EXECUTOR_CTORS.contains(&self.q(f).as_str())) {
                            self.executors.insert(txt(alias, src).to_string());
                        }
                    }
                }
                self.children(n);
            }
            "except_clause" => {
                self.check_stored_exception(n);
                self.children(n);
            }
            "augmented_assignment" => {
                let plus = field(n, "operator").is_some_and(|o| txt(o, src) == "+=");
                if self.loop_depth > 0 && plus {
                    let l = field(n, "left");
                    let named_str = l.is_some_and(|l| l.kind() == "identifier" && self.str_names.contains(txt(l, src)));
                    if named_str || field(n, "right").is_some_and(|r| is_strish(r, src)) {
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
            "import_statement" | "import_from_statement" => {}
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

    /// `self.err = e` / `d[k] = e` / `errors.append(e)` inside `except ... as e` (= rules/visitor.py
    /// `_check_stored_exception`). A nested `except ... as` block is left to its own check, so every node
    /// is searched once.
    fn check_stored_exception(&mut self, clause: Node<'t>) {
        let src = self.src;
        let body = named_children(clause).into_iter().find(|c| c.kind() == "block");
        let (Some(alias), Some(body)) = (caught_alias(clause), body) else { return };
        let name = txt(alias, src).to_string();
        let (loads, binds) = occurrences(body, &name, src, true);
        if binds > 0 {
            return; // rebound inside the handler: the name may no longer be the exception
        }
        for load in loads {
            if let Some(place) = stored_into(load, src).filter(|_| !in_nested_scope(load, body)) {
                self.add_s(load, "frame-retention.stored-exception", I, vec![("name", name.clone())],
                           dotted(place, src));
            }
        }
    }

    /// `df = pandas.concat([df, part])` / `arr = numpy.append(arr, x)` (= rules/visitor.py `_check_concat_loop`).
    fn check_concat_loop(&mut self, n: Node<'t>, q: &str, args: &[Node<'t>]) {
        let src = self.src;
        let (Some(assign), Some(first)) = (sole_assignment(n), args.first().map(|a| unwrap(*a))) else { return };
        let Some(name) = field(assign, "left").and_then(|l| dotted(unwrap_target(l), src)) else { return };
        let inputs = if CONCAT_FIRST_ARG_CALLS.contains(&q) {
            vec![first]
        } else if matches!(first.kind(), "list" | "tuple") {
            named_children(first)
        } else {
            vec![]
        };
        if inputs.into_iter().any(|x| dotted(x, src).as_deref() == Some(name.as_str())) {
            self.add_s(n, "memory-swell.concat-loop", W, vec![("call", format!("{q}()")), ("name", name.clone())],
                       Some(name));
        }
    }

    fn check_cache_decorators(&mut self, def: Node<'t>) {
        let src = self.src;
        for d in decorators(def) {
            let unbounded = self.q(d) == "functools.cache"
                || (d.kind() == "call"
                    && field(d, "function").is_some_and(|f| self.q(f) == "functools.lru_cache")
                    && {
                        let (args, kws) = call_args(d);
                        maxsize_none(&args, &kws, src)
                    });
            if !unbounded {
                continue;
            }
            let first_self = param_names(def, src).first().is_some_and(|p| p == "self");
            let fname = field(def, "name").map(|x| txt(x, src).to_string());
            if is_method(def) && first_self {
                self.add_s(d, "memory-swell.method-cache", W, vec![], fname);
            } else {
                self.add_s(d, "memory-swell.unbounded-cache", I, vec![], fname);
            }
        }
    }

    /// `functools.cache(g)` / `lru_cache(maxsize=None)(g)` (= rules/visitor.py `_check_cache_call`).
    fn check_cache_call(&mut self, n: Node<'t>, q: &str, args: &[Node<'t>], kws: &[Node<'t>]) {
        let src = self.src;
        let parent = n.parent();
        if parent.is_some_and(|p| p.kind() == "decorator") {
            return; // reported by check_cache_decorators
        }
        let subject = if q == "functools.cache" {
            if args.len() != 1 {
                return;
            }
            dotted(args[0], src)
        } else if q == "functools.lru_cache" && maxsize_none(args, kws, src) {
            match parent {
                Some(p) if p.kind() == "call" && field(p, "function") == Some(n) => {
                    call_args(p).0.first().and_then(|a| dotted(*a, src))
                }
                Some(p) if p.kind() == "assignment" && field(p, "right") == Some(n) => {
                    field(p, "left").and_then(|l| dotted(l, src))
                }
                _ => None,
            }
        } else {
            return;
        };
        self.add_s(n, "memory-swell.unbounded-cache", I, vec![], subject);
    }

    fn visit_call(&mut self, n: Node<'t>) {
        let src = self.src;
        let Some(f) = field(n, "function") else { return };
        let q = self.q(f);
        let (args, kws) = call_args(n);
        self.check_cache_call(n, &q, &args, &kws);
        if QUEUE_CTORS.contains(&q.as_str()) && maxsize_zero(&args, &kws, src) {
            self.add(n, "memory-swell.unbounded-queue", I, vec![("call", format!("{q}()"))]);
        }
        if START_METHOD_CALLS.contains(&q.as_str()) && names_fork(&args, &kws, src) {
            self.add(n, "memory-swell.fork-start-method", I, vec![("call", q.clone())]);
        }
        if self.loop_depth > 0 && (CONCAT_SEQUENCE_CALLS.contains(&q.as_str()) || CONCAT_FIRST_ARG_CALLS.contains(&q.as_str())) {
            self.check_concat_loop(n, &q, &args);
        }
        if self.loop_depth > 0 && q == "copy.deepcopy" {
            self.add(n, "memory-swell.deepcopy-loop", W, vec![]);
        }
        if self.loop_depth > 0 && q == "re.compile"
            && args.first().is_some_and(|a| matches!(unwrap(*a).kind(), "string" | "concatenated_string"))
        {
            self.add(n, "memory-swell.recompile-loop", I, vec![]);
        }
        if q == "asyncio.create_task" && n.parent().is_some_and(|p| p.kind() == "expression_statement") {
            self.add(n, "task-retention.asyncio-task", W, vec![]);
        }
        if file_open(n, f, &q, src, &self.idx.imports) && unmanaged_open(n, src) {
            self.add(n, "resource-leak.file-handle", W, vec![]);
        }
        if f.kind() == "attribute" {
            let a = attr_name(f, src);
            if a == "setdefault" && self.loop_depth > 0 && args.len() >= 2
                && args[1].kind() == "list" && named_children(args[1]).is_empty()
                && n.parent().is_some_and(|p| p.kind() == "expression_statement")
            {
                let subj = field(f, "object").and_then(|o| dotted(o, src));
                self.add_s(n, "memory-swell.setdefault-loop", I, vec![], subj);
            }
            if a == "extend" && self.loop_depth > 0
                && args.first().is_some_and(|arg| arg.kind() == "list_comprehension")
            {
                let subj = field(f, "object").and_then(|o| dotted(o, src));
                self.add_s(args[0], "memory-swell.list-extend-loop", I, vec![], subj);
            }
            if let Some(an) = field(f, "attribute") {
                let recv = field(f, "object").and_then(|o| dotted(o, src));
                if a == "fetchall" {
                    self.add_s(an, "heap-inflation", W, vec![], recv.clone());
                }
                if a == "readlines" && args.is_empty() {
                    self.add_s(an, "text-inflation.whole-file", W, vec![("call", "readlines()".into())], recv.clone());
                }
                if ["split", "splitlines", "rsplit"].contains(&a) {
                    let inner = field(f, "object").map(unwrap).filter(|o| o.kind() == "call");
                    let is_read = inner.is_some_and(|c| {
                        field(c, "function").is_some_and(|cf| attr_name(cf, src) == "read")
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
                        && field(arg, "function").is_some_and(|x| x.kind() == "identifier" && txt(x, src) == "dict"));
                if container {
                    let subj = field(f, "object").and_then(|o| dotted(o, src));
                    self.add_s(arg, "ram-fragmentation.append", W, vec![], subj);
                }
            }
            if EXECUTOR_METHODS.contains(&a) && !args.is_empty() {
                let obj = field(f, "object").map(unwrap);
                let is_exec = obj.is_some_and(|o| {
                    (o.kind() == "identifier" && self.executors.contains(txt(o, src)))
                        || (o.kind() == "call"
                            && field(o, "function").is_some_and(|of| EXECUTOR_CTORS.contains(&self.q(of).as_str())))
                });
                if is_exec {
                    self.check_thread_target(args[0]);
                }
            }
        }
        let pandas_call = q.rsplit('.').next().unwrap_or("");
        if q.starts_with("pandas.")
            && (PANDAS_LOADERS.contains(&pandas_call) || ["DataFrame", "Series"].contains(&pandas_call))
            && kwarg(&kws, "dtype", src).is_some_and(|v| object_dtype(v, src, &self.idx.imports))
        {
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
