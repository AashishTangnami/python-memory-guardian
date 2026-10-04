"""
Python Memory Guardian - language server (pygls 2.x, stdio).

Analysis lives in rules.py; message text in messages.json; interpreter facts
come from probe.py. The VS Code client runs probe.py with the user's
configured interpreter and passes the result as initializationOptions.profile.
Because this server itself runs on that same interpreter, it can also probe
in-process when the client sends nothing.
"""
from __future__ import annotations

import asyncio
import os

import _vendor  # noqa: F401  (must come first: puts server/libs on sys.path)
from lsprotocol import types as lsp
from pygls.lsp.server import LanguageServer

import probe
import rules

DEBOUNCE_SECONDS = 0.35

server = LanguageServer("python-memory-guardian", "1.4.1")  # keep in step with package.json
FACTS: dict = {}
_pending: dict[str, asyncio.TimerHandle] = {}


@server.feature(lsp.INITIALIZE)
def on_initialize(ls: LanguageServer, params: lsp.InitializeParams):
    opts = params.initialization_options or {}
    profile = opts.get("profile") if isinstance(opts, dict) else None
    FACTS.clear()
    try:
        # An explicit empty profile means the configured target probe failed.
        # Do not replace container facts with measurements of the host runtime.
        FACTS.update(profile if isinstance(profile, dict) else probe.probe())
    except Exception:  # never fail startup over the probe; messages go neutral
        pass


def _publish(ls: LanguageServer, uri: str) -> None:
    if (h := _pending.pop(uri, None)) is not None:
        h.cancel()
    doc = ls.workspace.get_text_document(uri)
    diags = rules.analyze(doc.source, uri, FACTS)
    if diags is None:
        return  # syntax error while typing: keep last good diagnostics
    ls.text_document_publish_diagnostics(
        lsp.PublishDiagnosticsParams(uri=uri, diagnostics=diags, version=doc.version))


def _schedule(ls: LanguageServer, uri: str) -> None:
    if (h := _pending.pop(uri, None)) is not None:
        h.cancel()
    _pending[uri] = asyncio.get_event_loop().call_later(DEBOUNCE_SECONDS, _publish, ls, uri)


@server.feature(lsp.TEXT_DOCUMENT_DID_OPEN)
def did_open(ls: LanguageServer, params: lsp.DidOpenTextDocumentParams):
    _publish(ls, params.text_document.uri)


@server.feature(lsp.TEXT_DOCUMENT_DID_CHANGE)
def did_change(ls: LanguageServer, params: lsp.DidChangeTextDocumentParams):
    _schedule(ls, params.text_document.uri)


@server.feature(lsp.TEXT_DOCUMENT_DID_SAVE)
def did_save(ls: LanguageServer, params: lsp.DidSaveTextDocumentParams):
    _publish(ls, params.text_document.uri)


@server.feature(lsp.TEXT_DOCUMENT_DID_CLOSE)
def did_close(ls: LanguageServer, params: lsp.DidCloseTextDocumentParams):
    if (h := _pending.pop(params.text_document.uri, None)) is not None:
        h.cancel()
    ls.text_document_publish_diagnostics(
        lsp.PublishDiagnosticsParams(uri=params.text_document.uri, diagnostics=[]))


if __name__ == "__main__":
    # Developer hook: PMG_DEBUGPY=5678 makes the server wait for a debugger to attach
    # (VS Code: "Python Debugger: Remote Attach" on localhost:5678). Requires debugpy.
    port = os.environ.get("PMG_DEBUGPY")
    if port:
        import debugpy
        debugpy.listen(("127.0.0.1", int(port)))
        debugpy.wait_for_client()
    server.start_io()
