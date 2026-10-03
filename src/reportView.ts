import * as fs from 'fs';
import * as vscode from 'vscode';
import { randomBytes } from 'crypto';
import { ProfileIndex } from './profileModel';
import { diagnose, callTree, CallMetric } from './reportModel';
import { reportHtml } from './reportWebview';

export class GuardianReport implements vscode.Disposable {
  private panel?: vscode.WebviewPanel;
  private index?: ProfileIndex;
  private metric: CallMetric = 'elapsed';
  private thread = '';

  update(index: ProfileIndex | undefined): void {
    this.index = index;
    this.thread = '';
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
      if (m.type === 'filter' && ['elapsed', 'python', 'native', 'system', 'unsplit'].includes(m.metric)
        && typeof m.thread === 'string') {
        this.metric = m.metric;
        this.thread = m.thread;
        this.refresh();
      }
      if (m.type === 'open' && typeof m.file === 'string' && Number.isInteger(m.line) && m.line >= 1) {
        const p = this.index?.profile;
        // Never treat an arbitrary webview message as a path to open.
        const allowed = p && (p.files[m.file]?.[String(m.line)] || p.stacks?.frames.some(f =>
          f.user && f.file === m.file && f.line === m.line));
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
    const diagnoses = diagnose(p);
    const threads = [...new Map((p.stacks?.samples ?? []).map(s => [s.thread, s.thread_name])).entries()];
    void this.panel.webview.postMessage({ type: 'report', script: p.script, wall: p.wall_s,
      mode: p.memory_mode, diagnoses: diagnoses.slice(0, 200), diagnosisCount: diagnoses.length,
      freshness, tree: callTree(p, this.metric, this.thread), metric: this.metric, thread: this.thread,
      threads, stacksAvailable: !!p.stacks, dropped: p.stacks?.dropped_s ?? 0,
      depthLimited: p.stacks?.depth_limited ?? false, monitoring: p.monitoring });
  }

  dispose(): void { this.panel?.dispose(); }
}
