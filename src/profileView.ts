/**
 * Runtime profile integration: runs server/pmg_profile.py, watches
 * .pmg/profile.json, and shows measured data in the editor:
 *   - end-of-line labels (time split, memory) and per-function totals
 *   - a "runtime" diagnostic collection for measured leaks
 *   - severity adjustment of the static diagnostics (hot up, cold down)
 * Profiles are ignored for any file whose text changed since profiling.
 */
import { execFile } from "child_process";
import * as fs from "fs";
import * as path from "path";
import * as vscode from "vscode";
import {
  adjustSeverity, evidence, funcLabel, heat, leakMessage, lineLabel, parseProfile,
  ProfileIndex, runNotes, textHash, Thresholds, unattributedNote,
} from "./profileModel";
import { ContainerConfig, containerCommand, remapProfileKeys, toContainer, toLocal } from "./containerPaths";
import { baselineFileName } from "./compareModel";
import { buildSummary, Freshness as SummaryFreshness, StaticFinding } from "./summaryModel";
import { GuardianReport } from "./reportView";

const PROFILE_GLOB = "**/.pmg/profile.json";

type Freshness = "fresh" | "stale" | "absent";

export class ProfileView implements vscode.Disposable {
  private index: ProfileIndex | undefined;
  private overlay = true;
  private readonly raw = new Map<string, vscode.Diagnostic[]>();
  /** One source hash per document version; every freshness check on a keystroke shares it. */
  private readonly hashCache = new Map<string, { version: number; hash: string }>();
  /** Last freshness seen per profiled document, so edits that cannot change evidence do no work. */
  private readonly lastState = new Map<string, Freshness>();
  private readonly runtime = vscode.languages.createDiagnosticCollection("python-memory-guardian-runtime");
  private readonly status = vscode.window.createStatusBarItem(vscode.StatusBarAlignment.Left, 50);
  private readonly lineDeco = vscode.window.createTextEditorDecorationType({
    after: { margin: "0 0 0 2em", color: new vscode.ThemeColor("editorCodeLens.foreground") },
  });
  private readonly hotDeco = vscode.window.createTextEditorDecorationType({
    after: { margin: "0 0 0 2em", color: new vscode.ThemeColor("editorWarning.foreground") },
  });
  private readonly disposables: vscode.Disposable[] = [];
  private readonly report: GuardianReport;
  private showNextReport = false;
  /** The loaded profile as written (before path remapping), so a baseline is an exact copy of it. */
  private loaded: { uri: vscode.Uri; text: string } | undefined;
  /** The last profiling run's profile (a .pmg/profile.json), whose summary is kept in .pmg/summary.json beside it. */
  private run: { index: ProfileIndex; profilePath: string; summaryPath: string } | undefined;
  private summaryTimer: ReturnType<typeof setTimeout> | undefined;
  private summaryFailed = false;

  constructor(
    private readonly ctx: vscode.ExtensionContext,
    private readonly diagnostics: () => vscode.DiagnosticCollection | undefined,
    private readonly interpreter: () => string,
    private readonly container: () => ContainerConfig | undefined = () => undefined,
  ) {
    this.report = new GuardianReport((doc) => this.docState(doc), {
      dir: () => this.baselineDir(),
      parse: (text) => this.parse(text),
      save: () => this.saveBaseline(),
      changed: () => this.scheduleSummary(),
    });
    const watcher = vscode.workspace.createFileSystemWatcher(PROFILE_GLOB);
    this.disposables.push(
      watcher, this.runtime, this.status, this.lineDeco, this.hotDeco, this.report,
      watcher.onDidCreate((u) => this.load(u)),
      watcher.onDidChange((u) => this.load(u)),
      watcher.onDidDelete((u) => this.profileDeleted(u)),
      vscode.window.onDidChangeVisibleTextEditors(() => this.render()),
      vscode.workspace.onDidChangeTextDocument((e) => this.onEdit(e.document)),
      vscode.workspace.onDidCloseTextDocument((d) => this.forget(d.uri)),
      vscode.commands.registerCommand("pythonMemoryGuardian.showReport", () => this.report.show()),
      vscode.commands.registerCommand("pythonMemoryGuardian.openSavedReport", () => this.openSavedReport()),
      vscode.commands.registerCommand("pythonMemoryGuardian.visualizeReport", (uri?: vscode.Uri) => this.visualizeReport(uri)),
      vscode.commands.registerCommand("pythonMemoryGuardian.profileFile", () => this.runProfiler()),
      vscode.commands.registerCommand("pythonMemoryGuardian.toggleProfileOverlay", () => {
        this.overlay = !this.overlay;
        this.render();
      }),
      vscode.commands.registerCommand("pythonMemoryGuardian.clearProfile", () => this.clear()),
      vscode.commands.registerCommand("pythonMemoryGuardian.saveBaseline", () => this.saveBaseline()),
    );
    this.status.command = "pythonMemoryGuardian.showReport";
    void vscode.workspace.findFiles(PROFILE_GLOB, undefined, 1).then((u) => u[0] && this.load(u[0]));
  }

  /** Re-apply thresholds after a profile.* setting change; the language server is unaffected. */
  settingsChanged(): void {
    this.refreshDiagnostics();
    this.render();
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
    if (editor.document.isDirty && !await editor.document.save()) return;
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
    const monitoring = cfg.get<string>("monitoring", "off") === "lines" ? "lines" : "off";
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
          ["--memory", picked.mode, "--frames", String(frames), "--monitoring", monitoring,
            "--root", c(root), "--out", c(out), c(file)]);
        exec = new vscode.ProcessExecution(cmd.command, cmd.args, { cwd: root });
      } catch (e) {
        vscode.window.showErrorMessage(`Python Memory Guardian (container mode): ${e}`);
        return;
      }
    } else {
      const args = [this.ctx.asAbsolutePath(path.join("server", "pmg_profile.py")),
        "--memory", picked.mode, "--frames", String(frames), "--monitoring", monitoring,
        "--root", root, "--out", out, file];
      // ProcessExecution: no shell, so paths with spaces need no quoting on any OS.
      exec = new vscode.ProcessExecution(this.interpreter(), args, { cwd: path.dirname(file) });
    }
    const task = new vscode.Task({ type: "pmg-profile" }, folder ?? vscode.TaskScope.Workspace,
      `Profile ${path.basename(file)}`, "Python Memory Guardian", exec);
    task.presentationOptions = { reveal: vscode.TaskRevealKind.Always, clear: true };
    this.showNextReport = true;
    await vscode.tasks.executeTask(task);
  }

  // ---------------------------------------------------------------- load / clear
  private async openSavedReport(): Promise<void> {
    const selected = await vscode.window.showOpenDialog({ canSelectMany: false,
      filters: { "Memory Guardian profile": ["json"] }, openLabel: "Open Profile Report" });
    if (selected?.[0]) this.load(selected[0], true);
  }

  /** The JSON editor title passes its URI; the Command Palette uses the active editor. */
  private visualizeReport(uri?: vscode.Uri): void {
    const target = uri ?? vscode.window.activeTextEditor?.document.uri;
    if (!target || target.scheme !== "file" || path.extname(target.fsPath).toLowerCase() !== ".json") {
      void vscode.window.showWarningMessage("Open a saved Memory Guardian profile JSON to visualize it.");
      return;
    }
    this.load(target, true);
  }

  private load(uri: vscode.Uri, reveal = false): void {
    let text: string;
    try {
      // A user may click the action while editing the JSON. Render the text they
      // can see, including unsaved changes; watcher loads still read the disk.
      const open = reveal && vscode.workspace.textDocuments.find(d => d.uri.toString() === uri.toString());
      text = open ? open.getText() : fs.readFileSync(uri.fsPath, "utf8");
    } catch {
      if (reveal) vscode.window.showWarningMessage("Python Memory Guardian: could not read the selected profile JSON.");
      return;
    }
    const p = this.parse(text);
    if (!p) {
      vscode.window.showWarningMessage("Python Memory Guardian: unreadable profile JSON (schema 2 or 3 required).");
      return;
    }
    this.index = new ProfileIndex(p);
    this.loaded = { uri, text };
    // A profiling run's output (any .pmg/profile.json) gets a summary; other opened JSON files do not replace it.
    if (path.basename(uri.fsPath) === "profile.json" && path.basename(path.dirname(uri.fsPath)) === ".pmg") {
      this.run = { index: this.index, profilePath: uri.fsPath, summaryPath: path.join(path.dirname(uri.fsPath), "summary.json") };
      this.scheduleSummary();
    }
    this.lastState.clear();
    this.runtime.clear();
    this.report.update(this.index);
    if (this.showNextReport || reveal) { this.showNextReport = false; this.report.show(); }
    this.refreshDiagnostics();
    this.render();
  }

  /** Validate profile JSON and map container paths to host paths. */
  private parse(text: string) {
    const p = parseProfile(text);
    const cc = this.container();
    return p && cc ? remapProfileKeys(p, (k) => toLocal(k, cc.mappings)) : p;   // /app/x.py -> host path
  }

  // ---------------------------------------------------------------- baselines
  /** <workspace folder>/.pmg/baselines for the loaded profile (or, outside a workspace, next to its .pmg folder). */
  private baselineDir(): string | undefined {
    const uri = this.loaded?.uri;
    if (!uri) return vscode.workspace.workspaceFolders?.[0] && path.join(vscode.workspace.workspaceFolders[0].uri.fsPath, ".pmg", "baselines");
    const root = vscode.workspace.getWorkspaceFolder(uri)?.uri.fsPath
      ?? (path.basename(path.dirname(uri.fsPath)) === ".pmg" ? path.dirname(path.dirname(uri.fsPath)) : path.dirname(uri.fsPath));
    return path.join(root, ".pmg", "baselines");
  }

  /** Copy the loaded profile to .pmg/baselines/<name>.json with the git commit, so later runs can be compared to it. */
  private async saveBaseline(): Promise<void> {
    const loaded = this.loaded, dir = this.baselineDir(), p = this.index?.profile;
    if (!loaded || !dir || !p) {
      void vscode.window.showInformationMessage("Run Profile Current File first, then save the run as a baseline.");
      return;
    }
    const stamp = new Date().toISOString().slice(0, 16).replace(/[-:]/g, "").replace("T", "-");
    const name = await vscode.window.showInputBox({
      prompt: "Baseline name (letters, digits, dot, dash, underscore)",
      value: `${path.basename(p.script).replace(/\.pyw?$/, "")}-${stamp}`,
      validateInput: (v) => baselineFileName(v) ? undefined : "Use 1-80 letters, digits, dots, dashes or underscores, starting with a letter or digit.",
    });
    const file = name && baselineFileName(name);
    if (!file) return;
    const target = path.join(dir, file);
    if (fs.existsSync(target) && await vscode.window.showWarningMessage(`Baseline "${name!.trim()}" exists. Replace it?`,
      { modal: true }, "Replace") !== "Replace") return;
    const root = path.dirname(path.dirname(dir));
    const git = (args: string[]) => new Promise<string | null>((resolve) =>
      execFile("git", args, { cwd: root, timeout: 5000, windowsHide: true }, (err, out) => resolve(err ? null : out.trim())));
    const [commit, status] = await Promise.all([git(["rev-parse", "HEAD"]), git(["status", "--porcelain", "--untracked-files=no"])]);
    try {
      const json = JSON.parse(loaded.text);
      json.baseline = { name: name!.trim(), saved_at: new Date().toISOString(), git_commit: commit || null,
        git_dirty: status == null ? null : status.length > 0 };
      fs.mkdirSync(dir, { recursive: true });
      fs.writeFileSync(target + ".tmp", JSON.stringify(json));
      fs.renameSync(target + ".tmp", target);
    } catch (e) {
      void vscode.window.showErrorMessage(`Could not save the baseline: ${e}`);
      return;
    }
    this.report.baselinesChanged(name!.trim());
    void vscode.window.showInformationMessage(`Saved baseline "${name!.trim()}". Change your code, profile again, and open the report's Compare tab.`);
  }

  // ---------------------------------------------------------------- run summary
  /** Rewrite the summary shortly after its inputs change: static findings arrive, freshness changes, a baseline is picked. */
  private scheduleSummary(): void {
    if (!this.run) return;
    if (this.summaryTimer) clearTimeout(this.summaryTimer);
    this.summaryTimer = setTimeout(() => this.writeSummary(), 500);
  }

  private writeSummary(): void {
    this.summaryTimer = undefined;
    const run = this.run;
    if (!run) return;
    try {
      const p = run.index.profile;
      const freshness: Record<string, SummaryFreshness> = {};
      const staticDiagnostics: Record<string, StaticFinding[]> = {};
      const coll = this.diagnostics();
      const severity = ["error", "warning", "information", "hint"] as const;
      for (const file of Object.keys(p.files)) {
        const uri = vscode.Uri.file(file);
        const doc = vscode.workspace.textDocuments.find((d) => d.uri.toString() === uri.toString());
        if (doc) {
          freshness[file] = run.index === this.index ? this.docState(doc) === "fresh" ? "fresh" : "stale"
            : run.index.state(file, doc.getText()) === "fresh" ? "fresh" : "stale";
          // Both language servers analyze open documents only.
          staticDiagnostics[file] = (coll?.get(uri) ?? []).map((d) => ({ line: d.range.start.line + 1,
            code: String(typeof d.code === "object" ? d.code.value : d.code ?? ""), severity: severity[d.severity] ?? "hint", message: d.message }));
        } else {
          try {
            freshness[file] = run.index.state(file, fs.readFileSync(file, "utf8").replace(/^\uFEFF/, "")) === "fresh" ? "fresh" : "stale";
          } catch { freshness[file] = "unverified"; }
        }
      }
      const root = vscode.workspace.getWorkspaceFolder(vscode.Uri.file(run.profilePath))?.uri.fsPath ?? path.dirname(path.dirname(run.profilePath));
      const summary = buildSummary({ profile: p, profilePath: run.profilePath, root, generatedAt: new Date().toISOString(),
        generator: `python-memory-guardian ${this.ctx.extension?.packageJSON?.version ?? ""}`.trim(), freshness, staticDiagnostics,
        thresholds: this.thresholds(), comparison: this.report.comparisonFor(run.index) });
      fs.writeFileSync(run.summaryPath + ".tmp", JSON.stringify(summary, null, 1));
      fs.renameSync(run.summaryPath + ".tmp", run.summaryPath);
      this.summaryFailed = false;
    } catch (e) {
      if (!this.summaryFailed) void vscode.window.showWarningMessage(`Python Memory Guardian: could not write ${run.summaryPath}: ${e}`);
      this.summaryFailed = true;
    }
  }

  /** The profile was deleted: its summary would describe a profile that no longer exists. */
  private profileDeleted(uri: vscode.Uri): void {
    if (this.run && this.run.profilePath === uri.fsPath) {
      if (this.summaryTimer) clearTimeout(this.summaryTimer);
      try { fs.rmSync(this.run.summaryPath, { force: true }); } catch { /* already gone */ }
      this.run = undefined;
    }
    this.clear();
  }

  private clear(): void {
    this.index = undefined;
    this.loaded = undefined;
    this.lastState.clear();
    this.report.update(undefined);
    this.runtime.clear();
    this.refreshDiagnostics();
    this.render();
  }

  // ---------------------------------------------------------------- static diagnostics middleware
  /** Called from the language client middleware for every publishDiagnostics. */
  adjust(uri: vscode.Uri, diags: vscode.Diagnostic[]): vscode.Diagnostic[] {
    // Servers publish [] on close; dropping it keeps full refreshes proportional to files with findings.
    if (diags.length) this.raw.set(uri.toString(), diags);
    else this.raw.delete(uri.toString());
    if (this.run?.index.has(uri.fsPath)) this.scheduleSummary();
    const idx = this.index;
    if (!idx || !diags.length) return diags;
    const doc = vscode.workspace.textDocuments.find((d) => d.uri.toString() === uri.toString());
    if (!doc || this.docState(doc) !== "fresh") return diags;
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

  /** Re-adjust stored static findings: every file after a profile/setting change, or one edited file. */
  private refreshDiagnostics(only?: vscode.Uri): void {
    const coll = this.diagnostics();
    if (!coll) return;
    if (only) {
      const diags = this.raw.get(only.toString());
      if (diags) coll.set(only, this.adjust(only, diags));
      return;
    }
    for (const [key, diags] of this.raw) {
      const uri = vscode.Uri.parse(key);
      coll.set(uri, this.adjust(uri, diags));
    }
  }

  // ---------------------------------------------------------------- editor events
  /**
   * An edit matters only when it changes a profiled file's freshness (fresh -> stale, or an undo back to
   * fresh). Typing in an unprofiled or already-stale file leaves diagnostics, report and decorations as they are.
   */
  private onEdit(doc: vscode.TextDocument): void {
    if (doc.languageId !== "python" || !this.index) return;
    const state = this.docState(doc);
    if (state === "absent") return;
    const key = doc.uri.toString();
    if (this.lastState.get(key) === state) return;
    this.lastState.set(key, state);
    this.scheduleSummary();
    if (state !== "fresh") this.runtime.delete(doc.uri);
    this.refreshDiagnostics(doc.uri);
    this.report.refresh();
    this.render();
  }

  private forget(uri: vscode.Uri): void {
    const key = uri.toString();
    this.hashCache.delete(key);
    this.lastState.delete(key);
    this.raw.delete(key);
  }

  /** Freshness of an open document, hashing its text at most once per version. */
  private docState(doc: vscode.TextDocument): Freshness {
    const idx = this.index;
    if (!idx || !idx.has(doc.uri.fsPath)) return "absent";
    const key = doc.uri.toString();
    let cached = this.hashCache.get(key);
    if (!cached || cached.version !== doc.version) {
      cached = { version: doc.version, hash: textHash(doc.getText()) };
      this.hashCache.set(key, cached);
    }
    return idx.stateOfHash(doc.uri.fsPath, cached.hash);
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
    for (const ed of vscode.window.visibleTextEditors) {
      const doc = ed.document;
      if (doc.languageId !== "python") continue;
      const state = this.docState(doc);
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
      this.runtime.set(doc.uri, leaks);
    }
    const mem = p.memory_mode === "precise" && p.peak_traced_mb != null
      ? ` · peak ${p.peak_traced_mb.toFixed(0)} MB traced` : p.rss_peak_mb ? ` · RSS peak ${p.rss_peak_mb.toFixed(0)} MB` : "";
    this.status.text = stale
      ? "$(warning) PMG profile stale — re-run"
      : `$(pulse) PMG ${p.wall_s.toFixed(2)} s${mem} (${p.memory_mode})`;
    const notes = [...runNotes(p), unattributedNote(p)].filter(Boolean);
    this.status.tooltip = `Profiled ${path.basename(p.script)} on Python ${p.python}. Click to open the report.` +
      notes.map((n) => `\n\n${n}`).join("");
    this.status.show();
  }

  dispose(): void {
    if (this.summaryTimer) clearTimeout(this.summaryTimer);
    for (const d of this.disposables) d.dispose();
  }
}

function normKey(p: string): string {
  const s = p.replace(/\\/g, "/");
  return process.platform === "win32" ? s.toLowerCase() : s;
}
