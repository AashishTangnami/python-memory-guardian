/**
 * Python Memory Guardian - thin VS Code client.
 *
 * VS Code's extension host only executes JavaScript, so this is the entry
 * point. Analysis runs in an external language server (Python:
 * src/python/guardian_server.py, or Rust: bin/guardian-server[.exe]).
 *
 * Runtime profiling (profiling/controller.ts) adds measured time/memory per line on top
 * of the static findings, using src/python/pmg_profile.py.
 *
 * Version awareness: before starting either server, the client runs
 * src/python/probe.py with the configured interpreter. The probe MEASURES object
 * sizes, the GIL state and the allocator on that interpreter, and the result
 * is passed as initializationOptions.profile so diagnostics quote numbers for
 * the Python the user actually runs. If probing fails, messages fall back to
 * version-neutral wording.
 */
import * as vscode from "vscode";
import { clientDiagnostics, prepareClient, restartClient, stopClient } from "./language/client";
import { containerConfig, interpreter } from "./language/interpreter";
import { ProfileView } from "./profiling/controller";

let profileView: ProfileView | undefined;

const RESTART_SETTINGS = ["backend", "interpreter", "container"].map((key) => `pythonMemoryGuardian.${key}`);

export async function activate(ctx: vscode.ExtensionContext): Promise<void> {
  const output = vscode.window.createOutputChannel("Python Memory Guardian");
  profileView = new ProfileView(ctx, clientDiagnostics, interpreter, containerConfig);
  prepareClient(output, (uri, diagnostics) => profileView ? profileView.adjust(uri, diagnostics) : diagnostics);
  ctx.subscriptions.push(
    output,
    profileView,
    vscode.commands.registerCommand("pythonMemoryGuardian.restart", () => restartClient(ctx)),
    // A new interpreter, backend or container needs a fresh probe and server process. profile.* settings
    // are read by the client only, and the language client applies trace.server changes itself.
    vscode.workspace.onDidChangeConfiguration((e) => {
      if (RESTART_SETTINGS.some((key) => e.affectsConfiguration(key))) {
        void restartClient(ctx);
      } else if (e.affectsConfiguration("pythonMemoryGuardian.profile")) {
        profileView?.settingsChanged();
      }
    }),
  );
  await restartClient(ctx);
}

export function deactivate(): Thenable<void> | undefined {
  return stopClient();
}
