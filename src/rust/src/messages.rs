//! Message rendering from the shared src/python/messages.json (embedded at compile time).
use std::collections::HashMap;
use std::sync::OnceLock;

use serde_json::Value;

static MESSAGES_JSON: &str = include_str!("../../python/messages.json");
static MESSAGES: OnceLock<Value> = OnceLock::new();

pub(crate) type Facts = HashMap<String, String>;

// ---------------------------------------------------------------- message rendering
pub(crate) fn placeholders(t: &str) -> Vec<String> {
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

pub(crate) fn fill(t: &str, vals: &HashMap<String, String>) -> String {
    let mut s = t.to_string();
    for p in placeholders(t) {
        if let Some(v) = vals.get(&p) {
            s = s.replace(&format!("{{{p}}}"), v);
        }
    }
    s
}

/// 'sized' text only when every placeholder is known, otherwise 'neutral' (= rules.render).
pub(crate) fn render(key: &str, facts: &Facts, local: &[(&str, String)]) -> String {
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
