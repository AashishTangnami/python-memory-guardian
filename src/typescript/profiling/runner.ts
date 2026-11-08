/** The "Profile Current File" command: ask for its options, then start the profiler as a task. */
import * as path from "path";
import * as vscode from "vscode";
import { ContainerConfig, containerCommand, toContainer } from "../container/paths";
import { checkRealFolder } from "../container/pmgFolder";
import { PYTHON_DIR, stageProfiler } from "../container/staging";
import { enclosingFunction, splitArgs } from "./runOptions";

export interface RunnerContext {
  ctx: vscode.ExtensionContext;
  interpreter: () => string;
  container: () => ContainerConfig | undefined;
  /** Called just before the task starts. */
  started: () => void;
}

export async function runProfiler({ ctx, interpreter, container, started }: RunnerContext): Promise<void> {
  const editor = vscode.window.activeTextEditor;
  if (!editor || editor.document.languageId !== "python" || editor.document.isUntitled) {
    vscode.window.showWarningMessage("Open a saved Python file to profile it.");
    return;
  }
  if (editor.document.isDirty && !await editor.document.save()) return;
  const cfg = vscode.workspace.getConfiguration("pythonMemoryGuardian.profile");
  // With the cursor in a function, precise mode can trace only that function: the rest of the program runs
  // at full speed (tracemalloc slowed one allocation-heavy function 7.7x; scoping cannot avoid that part).
  const fn = enclosingFunction(editor.document.getText().split(/\r?\n/), editor.selection.active.line);
  const picked = await vscode.window.showQuickPick([
    { label: "fast", description: "sampled timing and process RSS; overhead depends on workload", mode: "fast" },
    { label: "precise", description: "tracemalloc: per-line memory, leaks; 3-10x+ slower on allocation-heavy code", mode: "precise" },
    ...(fn ? [{ label: `precise, only while ${fn}() runs`, mode: "precise", traceFunction: fn,
      description: "Python 3.12+; everything else at full speed" }] : []),
    { label: "time only", description: "no traced memory; the memory guard still monitors RSS", mode: "off" },
  ], { placeHolder: `Memory mode (default: ${cfg.get("memoryMode", "fast")})` });
  if (!picked) return;

  // Script arguments, remembered per file; Escape cancels the run.
  const file = editor.document.uri.fsPath;
  const argsKey = `pmg.args:${file}`;
  const typed = await vscode.window.showInputBox({
    prompt: `Arguments for ${path.basename(file)} (optional)`,
    placeHolder: "e.g. --records 20000 --mode both", value: ctx.workspaceState.get<string>(argsKey, ""),
    validateInput: (v) => { const r = splitArgs(v); return Array.isArray(r) ? undefined : r.error; },
  });
  if (typed === undefined) return;
  const scriptArgs = splitArgs(typed);
  if (!Array.isArray(scriptArgs)) return;
  await ctx.workspaceState.update(argsKey, typed);
  const scope = "traceFunction" in picked && picked.traceFunction ? ["--trace-function", picked.traceFunction] : [];

  const folder = vscode.workspace.getWorkspaceFolder(editor.document.uri);
  const root = folder?.uri.fsPath ?? path.dirname(file);
  const out = path.join(root, ".pmg", "profile.json");
  const frames = Math.max(1, Math.min(64, cfg.get<number>("frames", 2)));
  const monitoring = cfg.get<string>("monitoring", "off") === "lines" ? "lines" : "off";
  const evidenceArgs = ['--allocation-backend', cfg.get<string>('allocationBackend', 'sampled')];
  evidenceArgs.push('--allocation-detail', cfg.get<string>('allocationDetail', 'full'));
  evidenceArgs.push('--memory-guard', cfg.get<string>('memoryGuard', 'auto'),
    '--memory-limit-mb', String(cfg.get<number>('memoryLimitMiB', 0)),
    '--memory-reserve-mb', String(cfg.get<number>('memoryReserveMiB', 512)));
  if (cfg.get<boolean>('diagnostics', false)) evidenceArgs.push('--diagnostics');
  let exec: vscode.ProcessExecution;
  const cc = container();
  if (cc) {
    // Editor on the host, Python in a container: stage the profiler into the
    // bind-mounted project and pass container-side paths.
    try {
      const staged = stageProfiler(ctx, root);
      const c = (p: string) => toContainer(p, cc.mappings);
      const cmd = containerCommand(cc, c(staged),
        ["--memory", picked.mode, "--frames", String(frames), "--monitoring", monitoring, ...evidenceArgs, ...scope,
          "--root", c(root), "--out", c(out), c(file), ...scriptArgs]);
      exec = new vscode.ProcessExecution(cmd.command, cmd.args, { cwd: root });
    } catch (e) {
      vscode.window.showErrorMessage(`Python Memory Guardian (container mode): ${e}`);
      return;
    }
  } else {
    try {
      checkRealFolder(path.dirname(out));        // the profiler writes profile.json and its evidence there
    } catch (e) {
      vscode.window.showErrorMessage(`Python Memory Guardian: ${e}`);
      return;
    }
    const args = [ctx.asAbsolutePath(path.join(PYTHON_DIR, "pmg_profile.py")),
      "--memory", picked.mode, "--frames", String(frames), "--monitoring", monitoring, ...evidenceArgs, ...scope,
      "--root", root, "--out", out, file, ...scriptArgs];
    // ProcessExecution: no shell, so paths with spaces need no quoting on any OS.
    exec = new vscode.ProcessExecution(interpreter(), args, { cwd: path.dirname(file) });
  }
  const task = new vscode.Task({ type: "pmg-profile" }, folder ?? vscode.TaskScope.Workspace,
    `Profile ${path.basename(file)}`, "Python Memory Guardian", exec);
  task.presentationOptions = { reveal: vscode.TaskRevealKind.Always, clear: true };
  started();
  await vscode.tasks.executeTask(task);
}
