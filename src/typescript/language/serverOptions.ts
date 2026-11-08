/** How to launch the selected language server. */
import * as fs from "fs";
import * as path from "path";
import * as vscode from "vscode";
import { ServerOptions, TransportKind } from "vscode-languageclient/node";
import { PYTHON_DIR } from "../container/staging";
import { interpreter } from "./interpreter";

export function serverOptions(ctx: vscode.ExtensionContext): ServerOptions {
  const backend = vscode.workspace
    .getConfiguration("pythonMemoryGuardian")
    .get<string>("backend", "python");

  if (backend === "rust") {
    const exe = process.platform === "win32" ? "guardian-server.exe" : "guardian-server";
    const command = ctx.asAbsolutePath(path.join("bin", exe));
    if (!fs.existsSync(command)) {
      throw new Error(`Rust server binary not found at ${command}`);
    }
    if (process.platform !== "win32") {
      // A VSIX packaged on Windows loses the executable bit; restore it if needed.
      try {
        fs.accessSync(command, fs.constants.X_OK);
      } catch {
        fs.chmodSync(command, 0o755);
      }
    }
    return { command, transport: TransportKind.stdio };
  }

  // Python backend: pygls is vendored into src/python/libs at package time.
  const serverDir = ctx.asAbsolutePath(PYTHON_DIR);
  return {
    command: interpreter(),
    args: [path.join(serverDir, "guardian_server.py")],
    transport: TransportKind.stdio,
    options: {
      env: { ...process.env, PYTHONPATH: path.join(serverDir, "libs"), PYTHONUTF8: "1" },
    },
  };
}
