//! LSP wiring: document state, debounced publishing and the tower-lsp backend.
use std::collections::HashMap;
use std::sync::Arc;
use std::time::Duration;

use serde_json::Value;
use tokio::sync::{Mutex, RwLock};
use tower_lsp::jsonrpc::Result;
use tower_lsp::lsp_types::*;
use tower_lsp::{Client, LanguageServer};

use crate::analyze::*;
use crate::messages::*;

const DEBOUNCE: Duration = Duration::from_millis(350);

// ---------------------------------------------------------------- LSP wiring
pub(crate) type Docs = Arc<Mutex<HashMap<Url, (i32, String)>>>;

pub(crate) struct Backend {
    pub(crate) client: Client,
    pub(crate) docs: Docs,
    pub(crate) facts: Arc<RwLock<Facts>>,
}

async fn publish(client: &Client, docs: &Docs, facts: &Arc<RwLock<Facts>>, uri: Url, expected: Option<i32>) {
    let Some((version, text)) = docs.lock().await.get(&uri).cloned() else { return };
    if expected.is_some_and(|v| v != version) {
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
            server_info: Some(ServerInfo { name: "python-memory-guardian".into(), version: Some(env!("CARGO_PKG_VERSION").into()) }),
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
