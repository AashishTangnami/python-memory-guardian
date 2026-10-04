import * as fs from 'fs';
import * as path from 'path';
import * as vscode from 'vscode';
import { randomBytes } from 'crypto';
import { Profile, ProfileIndex, runNotes } from './profileModel';
import { baselineMeta, BaselineMeta, compareProfiles, Comparison } from './compareModel';
import { diagnose, callTree, overview, neighbors, topFunctions, isMemoryMetric, CallMetric, FrameView, TimeWindow } from './reportModel';
import { reportHtml } from './reportWebview';

/** What the webview needs to explain the memory-stack measures, without sending the tables twice. */
function memorySummary(p: ProfileIndex['profile']) {
  const ms = p.memory_stacks;
  if (!ms) return null;
  const table = (t: NonNullable<typeof ms.peak>) => ({ t: t.t, totalBytes: t.total_bytes, otherBytes: t.other_bytes,
    truncated: t.stacks.some(s => s.truncated) });
  return { depth: ms.depth, peak: ms.peak ? table(ms.peak) : null, exit: table(ms.exit), peakTracedMb: p.peak_traced_mb };
}

/** Where baselines live and how profile JSON is validated (with container path mapping); owned by ProfileView. */
export interface BaselineSource {
  dir(): string | undefined;
  parse(text: string): Profile | undefined;
  save(): Promise<void>;
  /** The selected baseline changed, so anything derived from the comparison (the run summary) is out of date. */
  changed(): void;
}

interface LoadedBaseline { file: string; mtime: number; profile: Profile; meta: BaselineMeta | null; label: string; }

/** Keep the webview payload bounded; rows are already ordered by how far each change exceeds run-to-run variation. */
function bounded(c: Comparison) {
  return { ...c, functions: c.functions.slice(0, 300), lines: c.lines.slice(0, 300), sites: c.sites.slice(0, 100),
    functionCount: c.functions.length, lineCount: c.lines.length, siteCount: c.sites.length };
}

export class GuardianReport implements vscode.Disposable {
  private panel?: vscode.WebviewPanel;
  private index?: ProfileIndex;
  private metric: CallMetric = 'elapsed';
  private thread = '';
  private frames: FrameView = 'grouped';
  private inverted = false;
  private window: TimeWindow | undefined;

  /** The profile the Compare tab compares against: a saved baseline, or any profile file the user picked. */
  private compareWith: { file: string; label: string } | undefined;
  private baseline: LoadedBaseline | undefined;
  private comparison: { index: ProfileIndex; baseline: LoadedBaseline; result: ReturnType<typeof bounded> } | undefined;
  private compareError = '';

  /** docState lets open documents reuse the controller's per-version hash instead of rehashing their text. */
  constructor(private readonly docState?: (doc: vscode.TextDocument) => string,
              private readonly baselines?: BaselineSource) {}

  /** A baseline was saved: select it, so the next run's report compares against it. */
  baselinesChanged(name: string): void {
    const dir = this.baselines?.dir();
    if (dir) this.compareWith = { file: path.join(dir, `${name}.json`), label: name };
    this.refresh();
    this.baselines?.changed();
  }

  /** The comparison for this profile against the selected baseline, if the report shows that profile. */
  comparisonFor(index: ProfileIndex): { label: string; result: Comparison } | null {
    if (index !== this.index || !this.compareWith) return null;
    const result = this.compare();
    return result ? { label: this.compareWith.label, result } : null;
  }

  /** Saved baselines, newest first. Reading only the directory listing keeps refreshes cheap. */
  private listBaselines(): { name: string; file: string; mtime: number }[] {
    const dir = this.baselines?.dir();
    if (!dir) return [];
    try {
      return fs.readdirSync(dir).filter(f => f.endsWith('.json')).map(f => {
        const file = path.join(dir, f);
        return { name: f.slice(0, -5), file, mtime: fs.statSync(file).mtimeMs };
      }).sort((a, b) => b.mtime - a.mtime);
    } catch { return []; }
  }

  /** Read and validate the selected baseline once per file version; then compare once per loaded profile. */
  private compare(): ReturnType<typeof bounded> | null {
    const target = this.compareWith, index = this.index;
    this.compareError = '';
    if (!target || !index || !this.baselines) return null;
    try {
      const mtime = fs.statSync(target.file).mtimeMs;
      if (!this.baseline || this.baseline.file !== target.file || this.baseline.mtime !== mtime) {
        const text = fs.readFileSync(target.file, 'utf8'), profile = this.baselines.parse(text);
        if (!profile) { this.compareError = `${target.label} is not a readable profile (schema 2 or 3 required).`; return null; }
        this.baseline = { file: target.file, mtime, profile, meta: baselineMeta(JSON.parse(text)), label: target.label };
        this.comparison = undefined;
      }
    } catch {
      this.compareError = `Could not read ${target.label}.`;
      return null;
    }
    if (!this.comparison || this.comparison.index !== index || this.comparison.baseline !== this.baseline) {
      this.comparison = { index, baseline: this.baseline, result: bounded(compareProfiles(this.baseline.profile, index.profile, this.baseline.meta)) };
    }
    return this.comparison.result;
  }

  update(index: ProfileIndex | undefined): void {
    this.index = index;
    this.thread = '';
    this.window = undefined;
    if (isMemoryMetric(this.metric) && !index?.profile.memory_stacks) this.metric = 'elapsed';
    this.refresh();
  }

  show(): void {
    if (!this.index) {
      void vscode.window.showInformationMessage('Run Profile Current File to create a Memory Guardian report.');
      return;
    }
    if (this.panel) { this.panel.reveal(vscode.ViewColumn.Beside, true); return; }
    const panel = this.panel = vscode.window.createWebviewPanel('memoryGuardian.report',
      'Memory Guardian Report', vscode.ViewColumn.Beside, { enableScripts: true, localResourceRoots: [] });
    panel.webview.html = reportHtml(randomBytes(18).toString('hex'));
    panel.onDidDispose(() => { if (this.panel === panel) this.panel = undefined; });
    panel.webview.onDidReceiveMessage(async m => {
      if (!m || typeof m !== 'object') return;
      if (m.type === 'ready') this.refresh();
      // Compare tab: a listed baseline by name, '' to stop comparing, any profile file, or save this run.
      if (m.type === 'compare' && typeof m.name === 'string') {
        const hit = this.listBaselines().find(b => b.name === m.name);
        this.compareWith = hit ? { file: hit.file, label: hit.name } : undefined;
        this.refresh();
        this.baselines?.changed();
      }
      if (m.type === 'compareFile') {
        const picked = await vscode.window.showOpenDialog({ canSelectMany: false, filters: { 'Memory Guardian profile': ['json'] },
          openLabel: 'Compare with this profile' });
        if (picked?.[0]) {
          this.compareWith = { file: picked[0].fsPath, label: path.basename(picked[0].fsPath) };
          this.refresh();
          this.baselines?.changed();
        }
      }
      if (m.type === 'saveBaseline') void this.baselines?.save();
      if (m.type === 'filter' && ['elapsed', 'python', 'native', 'system', 'unsplit', 'mem_peak', 'mem_exit'].includes(m.metric)
        && typeof m.thread === 'string' && ['grouped', 'all', 'mine'].includes(m.frames) && typeof m.inverted === 'boolean') {
        this.metric = m.metric;
        this.thread = m.thread;
        this.frames = m.frames;
        this.inverted = m.inverted;
        this.refresh();
      }
      // A time range dragged on the Overview memory chart narrows the memory diagnosis to it.
      if (m.type === 'window') {
        const ok = (v: unknown): v is number => typeof v === 'number' && Number.isFinite(v) && v >= 0;
        if (m.clear === true) this.window = undefined;
        else if (ok(m.from) && ok(m.to) && m.from < m.to) this.window = { from: m.from, to: m.to };
        else return;
        this.refresh();
      }
      // Callers and callees of one function, computed on request so the report payload stays small.
      if (m.type === 'neighbors' && typeof m.key === 'string' && m.key.length < 8192 && this.index?.profile) {
        void this.panel?.webview.postMessage({ type: 'neighbors',
          ...neighbors(this.index.profile, this.metric, m.key, this.thread, this.frames) });
      }
      if (m.type === 'open' && typeof m.file === 'string' && Number.isInteger(m.line) && m.line >= 1) {
        const p = this.index?.profile;
        // Never treat an arbitrary webview message as a path to open.
        const allowed = p && (p.files[m.file]?.[String(m.line)] || p.stacks?.frames.some(f =>
          f.user && f.file === m.file && (f.line === m.line || f.first_line === m.line)));
        if (!allowed || !this.fresh(m.file)) {
          void vscode.window.showWarningMessage('Source changed or was not verified. Re-run profiling before navigating from this report.');
          return;
        }
        try {
          const doc = await vscode.workspace.openTextDocument(vscode.Uri.file(m.file));
          if (this.index?.state(m.file, doc.getText()) !== 'fresh') return;
          const line = Math.min(m.line - 1, doc.lineCount - 1);
          await vscode.window.showTextDocument(doc, { viewColumn: vscode.ViewColumn.One,
            selection: new vscode.Range(line, 0, line, 0) });
        } catch (err) { void vscode.window.showWarningMessage(`Could not open source: ${err}`); }
      }
    });
  }

  private fresh(file: string): boolean {
    if (!this.index) return false;
    const uri = vscode.Uri.file(file);
    const doc = vscode.workspace.textDocuments.find(d => d.uri.toString() === uri.toString());
    if (doc && this.docState) return this.docState(doc) === 'fresh';
    try {
      // Open documents handle their configured encoding. Closed non-UTF8 files
      // remain conservatively stale until opened in the editor.
      return this.index.state(file, doc?.getText() ?? fs.readFileSync(file, 'utf8').replace(/^\uFEFF/, '')) === 'fresh';
    } catch { return false; }
  }

  refresh(): void {
    if (!this.panel) return;
    const p = this.index?.profile;
    if (!p) { void this.panel.webview.postMessage({ type: 'clear' }); return; }
    const freshness = Object.fromEntries(Object.keys(p.files).map(file => [file, this.fresh(file)]));
    const diagnoses = diagnose(p, this.window);
    const threads = [...new Map((p.stacks?.samples ?? []).map(s => [s.thread, s.thread_name])).entries()];
    void this.panel.webview.postMessage({ type: 'report', script: p.script, wall: p.wall_s,
      mode: p.memory_mode, overview: overview(p), diagnoses: diagnoses.slice(0, 200),
      diagnosisCount: diagnoses.length, growingCount: diagnoses.filter(d => d.status === 'growing').length,
      freshness, tree: callTree(p, this.metric, this.thread, 25000, this.frames, this.inverted), metric: this.metric, thread: this.thread,
      inverted: this.inverted, window: this.window ?? null, largest: p.largest_objects ?? null,
      frames: this.frames, functions: topFunctions(p, this.metric, this.thread, this.frames),
      unit: isMemoryMetric(this.metric) ? 'bytes' : 'seconds', memoryStacks: memorySummary(p),
      threads, stacksAvailable: !!p.stacks, dropped: p.stacks?.dropped_s ?? 0,
      depthLimited: p.stacks?.depth_limited ?? false, monitoring: p.monitoring,
      notes: runNotes(p), tracingLostS: p.memory_tracing_lost_s ?? null,
      baselines: this.baselines ? this.listBaselines().map(b => ({ name: b.name, mtime: b.mtime })) : null,
      compareWith: this.compareWith ?? null, comparison: this.compare(), compareError: this.compareError });
  }

  dispose(): void { this.panel?.dispose(); }
}
