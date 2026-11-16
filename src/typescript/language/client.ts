/** Start, restart and stop the language client. Restarts are serialized through one lifecycle promise. */
import * as vscode from "vscode";
import { LanguageClient, LanguageClientOptions } from "vscode-languageclient/node";
import { probeInterpreter } from "./probe";
import { serverOptions } from "./serverOptions";

type Adjust = (uri: vscode.Uri, diagnostics: vscode.Diagnostic[]) => vscode.Diagnostic[];

let client: LanguageClient | undefined;
let output: vscode.OutputChannel | undefined;
let adjust: Adjust | undefined;
let lifecycle: Promise<void> = Promise.resolve();
let shuttingDown = false;

/** Prepare for a new activation. `adjustDiagnostics` passes static findings through the runtime profile. */
export function prepareClient(channel: vscode.OutputChannel, adjustDiagnostics: Adjust): void {
  shuttingDown = false;
  output = channel;
  adjust = adjustDiagnostics;
}

/** The running client's diagnostic collection, if one is running. */
export function clientDiagnostics(): vscode.DiagnosticCollection | undefined {
  return client?.diagnostics;
}

async function startClient(ctx: vscode.ExtensionContext): Promise<void> {
  const profile = await probeInterpreter(ctx, output);
  const clientOptions: LanguageClientOptions = {
    documentSelector: [
      { scheme: "file", language: "python" },
      { scheme: "untitled", language: "python" },
    ],
    diagnosticCollectionName: "python-memory-guardian",
    initializationOptions: { profile },
    outputChannel: output,
    // Static findings pass through the runtime profile: hot lines are raised one
    // severity level with measured evidence, cold lines are lowered to hints.
    middleware: {
      handleDiagnostics: (uri, diagnostics, next) =>
        next(uri, adjust ? adjust(uri, diagnostics) : diagnostics),
    },
  };
  try {
    client = new LanguageClient(
      "pythonMemoryGuardian",
      "Python Memory Guardian",
      serverOptions(ctx),
      clientOptions,
    );
    await client.start();
  } catch (err) {
    client = undefined;
    vscode.window.showErrorMessage(`Python Memory Guardian failed to start: ${err}`);
  }
}

export function restartClient(ctx: vscode.ExtensionContext): Promise<void> {
  lifecycle = lifecycle.catch(err => output?.appendLine(`Server stop failed: ${err}`)).then(async () => {
    if (shuttingDown) return;
    await client?.stop();
    client = undefined;
    await startClient(ctx);
  });
  return lifecycle;
}

export function stopClient(): Thenable<void> {
  shuttingDown = true;
  return lifecycle.catch(() => {}).then(async () => {
    await client?.stop();
    client = undefined;
  });
}
