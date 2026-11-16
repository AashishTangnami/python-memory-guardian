//! Python Memory Guardian - Rust language server (tower-lsp + tree-sitter).
//!
//! Mirrors src/python/rules/ rule-for-rule, module for module. Message text comes from the shared
//! src/python/messages.json (embedded at compile time), and interpreter facts come
//! from src/python/probe.py, run by the VS Code client against the user's
//! configured interpreter and passed in initializationOptions.profile.
mod analyze;
mod index;
mod list_once;
mod lsp;
mod messages;
mod positions;
mod syntax;
mod tables;
mod threads;
mod visitor;

use tower_lsp::{LspService, Server};

use crate::lsp::Backend;

#[tokio::main]
async fn main() {
    let (service, socket) = LspService::new(|client| Backend {
        client, docs: Default::default(), facts: Default::default(),
    });
    Server::new(tokio::io::stdin(), tokio::io::stdout(), socket).serve(service).await;
}
