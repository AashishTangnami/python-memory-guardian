/**
 * Runtime profile integration: runs server/pmg_profile.py, watches
 * .pmg/profile.json, and shows measured data in the editor:
 *   - end-of-line labels (time split, memory) and per-function totals
 *   - a "runtime" diagnostic collection for measured leaks
 *   - severity adjustment of the static diagnostics (hot up, cold down)
 * Profiles are ignored for any file whose text changed since profiling.
 */
import * as fs from "fs";
import * as path from "path";
import * as vscode from "vscode";
import {
  adjustSeverity, evidence, funcLabel, heat, leakMessage, lineLabel, parseProfile,
  ProfileIndex, Thresholds, unattributedNote,
} from "./profileModel";
import { ContainerConfig, containerCommand, remapProfileKeys, toContainer, toLocal } from "./containerPaths";

const PROFILE_GLOB = "**/.pmg/profile.json";

export class ProfileView implements vscode.Disposable {
  private index: ProfileIndex | undefined;
  private overlay = true;
  private readonly raw = new Map<string, vscode.Diagnostic[]>();
  private readonly runtime = vscode.languages.createDiagnosticCollection("python-memory-guardian-runtime");
  private readonly status = vscode.window.createStatusBarItem(vscode.StatusBarAlignment.Left, 50);
  private readonly lineDeco = vscode.window.createTextEditorDecorationType({
    after: { margin: "0 0 0 2em", color: new vscode.ThemeColor("editorCodeLens.foreground") },
  });
  private readonly hotDeco = vscode.window.createTextEditorDecorationType({
    after: { margin: "0 0 0 2em", color: new vscode.ThemeColor("editorWarning.foreground") },
  });
  private readonly disposables: vscode.Disposable[] = [];

  constructor(
    private readonly ctx: vscode.ExtensionContext,
    private readonly diagnostics: () => vscode.DiagnosticCollection | undefined,
    private readonly interpreter: () => string,
    private readonly container: () => ContainerConfig | undefined = () => undefined,
  ) {
    const watcher = vscode.workspace.createFileSystemWatcher(PROFILE_GLOB);
    this.disposables.push(
      watcher, this.runtime, this.status, this.lineDeco, this.hotDeco,
      watcher.onDidCreate((u) => this.load(u)),
      watcher.onDidChange((u) => this.load(u)),
      watcher.onDidDelete(() => this.clear()),
      vscode.window.onDidChangeVisibleTextEditors(() => this.render()),
      vscode.workspace.onDidChangeTextDocument((e) => {
        if (e.document.languageId === "python") this.render();
      }),
      vscode.commands.registerCommand("pythonMemoryGuardian.profileFile", () => this.runProfiler()),
      vscode.commands.registerCommand("pythonMemoryGuardian.toggleProfileOverlay", () => {
        this.overlay = !this.overlay;
        this.render();
      }),
      vscode.commands.registerCommand("pythonMemoryGuardian.clearProfile", () => this.clear()),
    );
    this.status.command = "pythonMemoryGuardian.profileFile";
    void vscode.workspace.findFiles(PROFILE_GLOB, undefined, 1).then((u) => u[0] && this.load(u[0]));
  }

  private thresholds(): Thresholds {
    const c = vscode.workspace.getConfiguration("pythonMemoryGuardian.profile");
    return { hotShare: c.get<number>("hotShare", 0.05), hotMb: c.get<number>("hotMB", 50) };
  }

  // ---------------------------------------------------------------- run
  private async runProfiler(): Promise<void> {
    const editor = vscode.window.activeTextEditor;
    if (!editor || editor.document.languageId !== "python" || editor.document.isUntitled) {
      vscode.window.showWarningMessage("Open a saved Python file to profile it.");
      return;
    }
    if (editor.document.isDirty) await editor.document.save();
    const cfg = vscode.workspace.getConfiguration("pythonMemoryGuardian.profile");
    const picked = await vscode.window.showQuickPick([
      { label: "fast", description: "time split + RSS memory, ~5% overhead", mode: "fast" },
      { label: "precise", description: "tracemalloc: per-line memory, leaks; ~2-5x slower", mode: "precise" },
      { label: "time only", description: "no memory measurement", mode: "off" },
    ], { placeHolder: `Memory mode (default: ${cfg.get("memoryMode", "fast")})` });
    if (!picked) return;

    const file = editor.document.uri.fsPath;
    const folder = vscode.workspace.getWorkspaceFolder(editor.document.uri);
    const root = folder?.uri.fsPath ?? path.dirname(file);
    const out = path.join(root, ".pmg", "profile.json");
    const frames = Math.max(1, Math.min(64, cfg.get<number>("frames", 2)));
    let exec: vscode.ProcessExecution;
    const cc = this.container();
    if (cc) {
      // Editor on the host, Python in a container: stage the profiler into the
      // bind-mounted project and pass container-side paths.
      try {
        const staged = path.join(root, ".pmg", "pmg_profile.py");
        fs.mkdirSync(path.dirname(staged), { recursive: true });
        fs.copyFileSync(this.ctx.asAbsolutePath(path.join("server", "pmg_profile.py")), staged);
        const c = (p: string) => toContainer(p, cc.mappings);
        const cmd = containerCommand(cc, c(staged),
          ["--memory", picked.mode, "--frames", String(frames), "--root", c(root), "--out", c(out), c(file)]);
        exec = new vscode.ProcessExecution(cmd.command, cmd.args, { cwd: root });
      } catch (e) {
        vscode.window.showErrorMessage(`Python Memory Guardian (container mode): ${e}`);
        return;
      }
    } else {
      const args = [this.ctx.asAbsolutePath(path.join("server", "pmg_profile.py")),
        "--memory", picked.mode, "--frames", String(frames), "--root", root, "--out", out, file];
      // ProcessExecution: no shell, so paths with spaces need no quoting on any OS.
      exec = new vscode.ProcessExecution(this.interpreter(), args, { cwd: path.dirname(file) });
    }
    const task = new vscode.Task({ type: "pmg-profile" }, folder ?? vscode.TaskScope.Workspace,
      `Profile ${path.basename(file)}`, "Python Memory Guardian", exec);
    task.presentationOptions = { reveal: vscode.TaskRevealKind.Always, clear: true };
    await vscode.tasks.executeTask(task);
  }

  // ---------------------------------------------------------------- load / clear
  private load(uri: vscode.Uri): void {
    let text: string;
    try {
      text = fs.readFileSync(uri.fsPath, "utf8");
    } catch {
      return;
    }
    let p = parseProfile(text);
    const cc = this.container();
    if (p && cc) p = remapProfileKeys(p, (k) => toLocal(k, cc.mappings));   // /app/x.py -> host path
    if (!p) {
      vscode.window.showWarningMessage("Python Memory Guardian: unreadable profile (re-run the profiler).");
      return;
    }
    this.index = new ProfileIndex(p);
    this.refreshDiagnostics();
    this.render();
  }

  private clear(): void {
    this.index = undefined;
    this.runtime.clear();
    this.refreshDiagnostics();
    this.render();
  }

  // ---------------------------------------------------------------- static diagnostics middleware
  /** Called from the language client middleware for every publishDiagnostics. */
  adjust(uri: vscode.Uri, diags: vscode.Diagnostic[]): vscode.Diagnostic[] {
    this.raw.set(uri.toString(), diags);
    const idx = this.index;
    const doc = vscode.workspace.textDocuments.find((d) => d.uri.toString() === uri.toString());
    if (!idx || !doc || idx.state(uri.fsPath, doc.getText()) !== "fresh") return diags;
    const th = this.thresholds();
    return diags.map((d) => {
      const ln = d.range.start.line + 1;
      const e = idx.line(uri.fsPath, ln);
      const h = heat(e, idx.profile.memory_mode, th, idx.insideSampledFunction(uri.fsPath, ln));
      const out = new vscode.Diagnostic(d.range, evidence(e, h, idx.profile) + d.message,
        adjustSeverity(d.severity, h) as vscode.DiagnosticSeverity);
      out.code = d.code;
      out.source = d.source;
      out.relatedInformation = d.relatedInformation;
      out.tags = d.tags;
      return out;
    });
  }

  private refreshDiagnostics(): void {
    const coll = this.diagnostics();
    for (const [key, diags] of this.raw) {
      const uri = vscode.Uri.parse(key);
      coll?.set(uri, this.adjust(uri, diags));
    }
  }

  // ---------------------------------------------------------------- rendering
  private render(): void {
    const idx = this.index;
    if (!idx) {
      this.status.hide();
      for (const ed of vscode.window.visibleTextEditors) {
        ed.setDecorations(this.lineDeco, []);
        ed.setDecorations(this.hotDeco, []);
      }
      return;
    }
    const p = idx.profile;
    const th = this.thresholds();
    let stale = false;
    this.runtime.clear();
    for (const ed of vscode.window.visibleTextEditors) {
      const doc = ed.document;
      if (doc.languageId !== "python") continue;
      const state = idx.state(doc.uri.fsPath, doc.getText());
      stale ||= state === "stale";
      const normal: vscode.DecorationOptions[] = [];
      const hot: vscode.DecorationOptions[] = [];
      const leaks: vscode.Diagnostic[] = [];
      if (state === "fresh") {
        for (const [ln, e] of Object.entries(idx.files.get(normKey(doc.uri.fsPath)) ?? {})) {
          const line = Number(ln) - 1;
          if (line < 0 || line >= doc.lineCount) continue;
          const range = doc.lineAt(line).range;
          if (e.leak_runs) {
            const d = new vscode.Diagnostic(range, leakMessage(e), vscode.DiagnosticSeverity.Warning);
            d.source = "Python Memory Guardian (runtime)";
            d.code = "runtime-leak";
            leaks.push(d);
          }
          if (!this.overlay) continue;
          const label = lineLabel(e, p);
          if (!label || (e.share < 0.01 && !label.includes("▲") && !e.leak_runs)) continue;
          (heat(e, p.memory_mode, th) === "hot" ? hot : normal)
            .push({ range: new vscode.Range(range.end, range.end), renderOptions: { after: { contentText: label } } });
        }
        if (this.overlay) {
          for (const [first, f] of Object.entries(idx.functions.get(normKey(doc.uri.fsPath)) ?? {})) {
            const line = Number(first) - 1;
            if (line < 0 || line >= doc.lineCount || f.name === "<module>") continue;
            const end = doc.lineAt(line).range.end;
            normal.push({ range: new vscode.Range(end, end), renderOptions: { after: { contentText: funcLabel(f, p) } } });
          }
        }
      }
      ed.setDecorations(this.lineDeco, normal);
      ed.setDecorations(this.hotDeco, hot);
      if (leaks.length) this.runtime.set(doc.uri, leaks);
    }
    const mem = p.memory_mode === "precise" && p.peak_traced_mb != null
      ? ` · peak ${p.peak_traced_mb.toFixed(0)} MB traced` : p.rss_peak_mb ? ` · RSS peak ${p.rss_peak_mb.toFixed(0)} MB` : "";
    this.status.text = stale
      ? "$(warning) PMG profile stale — re-run"
      : `$(pulse) PMG ${p.wall_s.toFixed(2)} s${mem} (${p.memory_mode})`;
    const note = unattributedNote(p);
    this.status.tooltip = `Profiled ${path.basename(p.script)} on Python ${p.python}. Click to profile again.` +
      (note ? `\n\n${note}` : "");
    this.status.show();
  }

  dispose(): void {
    for (const d of this.disposables) d.dispose();
  }
}

function normKey(p: string): string {
  const s = p.replace(/\\/g, "/");
  return process.platform === "win32" ? s.toLowerCase() : s;
}
