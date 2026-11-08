//! One analysis run, the counterpart of src/python/rules/analyze.py.
use std::collections::HashSet;

use tower_lsp::lsp_types::*;
use tree_sitter::Parser;

use crate::index::*;
use crate::list_once::*;
use crate::messages::*;
use crate::positions::*;
use crate::visitor::*;

const SOURCE: &str = "Python Memory Guardian";
const SUPPRESS: &str = "memory-guardian: ignore";

pub(crate) fn analyze(text: &str, uri: &Url, facts: &Facts) -> Option<Vec<Diagnostic>> {
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
    v.out.extend(list_once(root, src));

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
            data: Some(serde_json::json!({
                "schema_version": 1, "analysis": "static", "category": fd.key,
                "severity": fd.sev, "status": "potential", "confidence": "static",
                "workload": null, "impact": null, "trace_artifact": null,
                "availability": {"workload": "unavailable", "impact": "unavailable",
                                 "trace_artifact": "unavailable"},
                "evidence": {"kind": "syntax", "measured": false,
                    "source": {"uri": uri, "line": range.start.line + 1,
                               "column": range.start.character,
                               "end_line": range.end.line + 1,
                               "end_column": range.end.character},
                    "scope": fd.scope, "subject": fd.subject},
                "reproduction": {"source_uri": uri,
                    "steps": ["Analyze the same source document with the saved interpreter probe facts."]}
            })),
            ..Default::default()
        });
    }
    diags.sort_by_key(|d| (d.range.start.line, d.range.start.character));
    Some(diags)
}
