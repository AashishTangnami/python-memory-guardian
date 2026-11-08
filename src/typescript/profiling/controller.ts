/**
 * Runtime profile integration: runs src/python/pmg_profile.py, watches
 * .pmg/profile.json, and shows measured data in the editor:
 *   - end-of-line labels (time split, memory) and per-function totals
 *   - a "runtime" diagnostic collection for measured leaks
 *   - severity adjustment of the static diagnostics (hot up, cold down)
 * Profiles are ignored for any file whose text changed since profiling.
 *
 * This class coordinates: it owns the loaded profile and freshness state, and
 * runner.ts, baselines.ts, diagnostics.ts and annotations.ts do the work.
 */
import * as fs from "fs";
import * as path from "path";
import * as vscode from "vscode";
import { ContainerConfig, remapProfileKeys, toLocal } from "../container/paths";
import { checkRealFolder, writeFileAtomic } from "../container/pmgFolder";
import { Freshness, ProfileIndex, textHash } from "../profile/index";
import { parseProfile } from "../profile/parse";
import { Thresholds } from "../profile/types";
import { GuardianReport } from "../report/view";
import { buildSummary, Freshness as SummaryFreshness, StaticFinding } from "../summary/summary";
import { Annotations } from "./annotations";
import { baselineDir, saveBaseline } from "./baselines";
import { StaticDiagnostics } from "./diagnostics";
import { runProfiler } from "./runner";

const PROFILE_GLOB = "**/.pmg/profile.json";

export class ProfileView implements vscode.Disposable {
  private index: ProfileIndex | undefined;
  private overlay = true;
  /** One source hash per document version; every freshness check on a keystroke shares it. */
  private readonly hashCache = new Map<string, { version: number; hash: string }>();
  /** Last freshness seen per profiled document, so edits that cannot change evidence do no work. */
  private readonly lastState = new Map<string, Freshness>();
  private readonly annotations = new Annotations();
  private readonly staticDiagnostics: StaticDiagnostics;
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
    this.staticDiagnostics = new StaticDiagnostics({
      index: () => this.index,
      collection: this.diagnostics,
      thresholds: () => this.thresholds(),
      docState: (doc) => this.docState(doc),
      published: (uri) => { if (this.run?.index.has(uri.fsPath)) this.scheduleSummary(); },
    });
    this.report = new GuardianReport((doc) => this.docState(doc), {
      dir: () => baselineDir(this.loaded?.uri),
      parse: (text) => this.parse(text),
      save: () => this.saveBaseline(),
      changed: () => this.scheduleSummary(),
    });
    const watcher = vscode.workspace.createFileSystemWatcher(PROFILE_GLOB);
    this.disposables.push(
      watcher, this.annotations, this.report,
      watcher.onDidCreate((u) => this.load(u)),
      watcher.onDidChange((u) => this.load(u)),
      watcher.onDidDelete((u) => this.profileDeleted(u)),
      vscode.window.onDidChangeVisibleTextEditors(() => this.render()),
      vscode.workspace.onDidChangeTextDocument((e) => this.onEdit(e.document)),
      vscode.workspace.onDidCloseTextDocument((d) => this.forget(d.uri)),
      vscode.commands.registerCommand("pythonMemoryGuardian.showReport", () => this.report.show()),
      vscode.commands.registerCommand("pythonMemoryGuardian.openSavedReport", () => this.openSavedReport()),
      vscode.commands.registerCommand("pythonMemoryGuardian.visualizeReport", (uri?: vscode.Uri) => this.visualizeReport(uri)),
      vscode.commands.registerCommand("pythonMemoryGuardian.profileFile", () => runProfiler({
        ctx: this.ctx, interpreter: this.interpreter, container: this.container,
        started: () => { this.showNextReport = true; },
      })),
      vscode.commands.registerCommand("pythonMemoryGuardian.toggleProfileOverlay", () => {
        this.overlay = !this.overlay;
        this.render();
      }),
      vscode.commands.registerCommand("pythonMemoryGuardian.clearProfile", () => this.clear()),
      vscode.commands.registerCommand("pythonMemoryGuardian.saveBaseline", () => this.saveBaseline()),
    );
    void vscode.workspace.findFiles(PROFILE_GLOB, undefined, 1).then((u) => u[0] && this.load(u[0]));
  }

  /** Re-apply thresholds after a profile.* setting change; the language server is unaffected. */
  settingsChanged(): void {
    this.staticDiagnostics.refresh();
    this.render();
  }

  private thresholds(): Thresholds {
    const c = vscode.workspace.getConfiguration("pythonMemoryGuardian.profile");
    return { hotShare: c.get<number>("hotShare", 0.05), hotMb: c.get<number>("hotMB", 50) };
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
    this.annotations.clearWarnings();
    this.report.update(this.index);
    if (this.showNextReport || reveal) { this.showNextReport = false; this.report.show(); }
    this.staticDiagnostics.refresh();
    this.render();
  }

  /** Validate profile JSON and map container paths to host paths. */
  private parse(text: string) {
    const p = parseProfile(text);
    const cc = this.container();
    return p && cc ? remapProfileKeys(p, (k) => toLocal(k, cc.mappings)) : p;   // /app/x.py -> host path
  }

  private saveBaseline(): Promise<void> {
    return saveBaseline(this.loaded, baselineDir(this.loaded?.uri), this.index?.profile, (name) => this.report.baselinesChanged(name));
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
      checkRealFolder(path.dirname(run.summaryPath));
      writeFileAtomic(run.summaryPath, JSON.stringify(summary, null, 1));
      this.summaryFailed = false;
    } catch (e) {
      if (!this.summaryFailed) void vscode.window.showWarningMessage(`Python Memory Guardian: could not write ${run.summaryPath}: ${e}`);
      this.summaryFailed = true;
    }
  }

  /**
   * A watched profile was deleted: its summary would describe a profile that no longer exists. The report is
   * cleared only when it shows that profile; another folder's profile.json (multi-root workspace) leaves it.
   */
  private profileDeleted(uri: vscode.Uri): void {
    if (this.run && this.run.profilePath === uri.fsPath) {
      if (this.summaryTimer) clearTimeout(this.summaryTimer);
      try { fs.rmSync(this.run.summaryPath, { force: true }); } catch { /* already gone */ }
      this.run = undefined;
    }
    if (this.loaded?.uri.fsPath === uri.fsPath) this.clear();
  }

  private clear(): void {
    this.index = undefined;
    this.loaded = undefined;
    this.lastState.clear();
    this.report.update(undefined);
    this.annotations.clearWarnings();
    this.staticDiagnostics.refresh();
    this.render();
  }

  // ---------------------------------------------------------------- static diagnostics middleware
  /** Called from the language client middleware for every publishDiagnostics. */
  adjust(uri: vscode.Uri, diags: vscode.Diagnostic[]): vscode.Diagnostic[] {
    return this.staticDiagnostics.adjust(uri, diags);
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
    if (state !== "fresh") this.annotations.deleteWarnings(doc.uri);
    this.staticDiagnostics.refresh(doc.uri);
    this.report.refresh();
    this.render();
  }

  private forget(uri: vscode.Uri): void {
    const key = uri.toString();
    this.hashCache.delete(key);
    this.lastState.delete(key);
    this.staticDiagnostics.forget(key);
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

  private render(): void {
    this.annotations.render(this.index, this.overlay, this.thresholds(), (doc) => this.docState(doc));
  }

  dispose(): void {
    if (this.summaryTimer) clearTimeout(this.summaryTimer);
    for (const d of this.disposables) d.dispose();
  }
}
