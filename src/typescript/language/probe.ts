/** Measure the target interpreter's facts before either language server starts. */
import { execFile } from "child_process";
import * as path from "path";
import * as vscode from "vscode";
import { containerCommand, toContainer } from "../container/paths";
import { PYTHON_DIR, stageHelper } from "../container/staging";
import { containerConfig, interpreter } from "./interpreter";

/** Run probe.py on the configured interpreter (or inside the container); {} on failure. */
export function probeInterpreter(ctx: vscode.ExtensionContext, output: vscode.OutputChannel | undefined): Promise<Record<string, unknown>> {
  let command = interpreter();
  let args = [ctx.asAbsolutePath(path.join(PYTHON_DIR, "probe.py"))];
  const cc = containerConfig();
  if (cc) {
    const ws = vscode.workspace.workspaceFolders?.[0]?.uri.fsPath;
    try {
      if (!ws) throw new Error("open a folder to use container mode");
      const staged = stageHelper(ctx, ws, "probe.py");
      ({ command, args } = containerCommand(cc, toContainer(staged, cc.mappings), []));
      output?.appendLine(`Probing container interpreter: ${command} ${args.join(" ")}`);
    } catch (e) {
      output?.appendLine(`Container probe not possible (${e}); using version-neutral messages.`);
      return Promise.resolve({});
    }
  }
  return new Promise((resolve) => {
    execFile(command, args, { timeout: 20_000, windowsHide: true,
      cwd: cc ? vscode.workspace.workspaceFolders?.[0]?.uri.fsPath : undefined }, (err, stdout) => {
      if (err) {
        output?.appendLine(`Interpreter probe failed (${err.message}); using version-neutral messages.`);
        return resolve({});
      }
      try {
        const facts = JSON.parse(stdout);
        output?.appendLine(`Interpreter profile: ${JSON.stringify(facts)}`);
        resolve(facts && typeof facts === "object" ? facts : {});
      } catch {
        output?.appendLine("Interpreter probe returned invalid JSON; using version-neutral messages.");
        resolve({});
      }
    });
  });
}
