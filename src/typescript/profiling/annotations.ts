/** What a profile shows in the editor: end-of-line labels, per-function totals, leak warnings and the status bar. */
import * as path from "path";
import * as vscode from "vscode";
import { Freshness, ProfileIndex } from "../profile/index";
import { funcLabel, leakMessage, lineLabel, runNotes, unattributedNote } from "../profile/labels";
import { heat, isGrowingRetention } from "../profile/metrics";
import { Thresholds } from "../profile/types";

export class Annotations implements vscode.Disposable {
  private readonly runtime = vscode.languages.createDiagnosticCollection("python-memory-guardian-runtime");
  private readonly status = vscode.window.createStatusBarItem(vscode.StatusBarAlignment.Left, 50);
  private readonly lineDeco = vscode.window.createTextEditorDecorationType({
    after: { margin: "0 0 0 2em", color: new vscode.ThemeColor("editorCodeLens.foreground") },
  });
  private readonly hotDeco = vscode.window.createTextEditorDecorationType({
    after: { margin: "0 0 0 2em", color: new vscode.ThemeColor("editorWarning.foreground") },
  });

  constructor() {
    this.status.command = "pythonMemoryGuardian.showReport";
  }

  clearWarnings(): void {
    this.runtime.clear();
  }

  deleteWarnings(uri: vscode.Uri): void {
    this.runtime.delete(uri);
  }

  render(idx: ProfileIndex | undefined, overlay: boolean, th: Thresholds, docState: (doc: vscode.TextDocument) => Freshness): void {
    if (!idx) {
      this.status.hide();
      for (const ed of vscode.window.visibleTextEditors) {
        ed.setDecorations(this.lineDeco, []);
        ed.setDecorations(this.hotDeco, []);
      }
      return;
    }
    const p = idx.profile;
    let stale = false;
    for (const ed of vscode.window.visibleTextEditors) {
      const doc = ed.document;
      if (doc.languageId !== "python") continue;
      const state = docState(doc);
      stale ||= state === "stale";
      const normal: vscode.DecorationOptions[] = [];
      const hot: vscode.DecorationOptions[] = [];
      const leaks: vscode.Diagnostic[] = [];
      if (state === "fresh") {
        for (const [ln, e] of Object.entries(idx.files.get(normKey(doc.uri.fsPath)) ?? {})) {
          const line = Number(ln) - 1;
          if (line < 0 || line >= doc.lineCount) continue;
          const range = doc.lineAt(line).range;
          if (isGrowingRetention(e, p)) {
            const d = new vscode.Diagnostic(range, leakMessage(e), vscode.DiagnosticSeverity.Warning);
            d.source = "Python Memory Guardian (runtime)";
            d.code = "runtime-leak";
            leaks.push(d);
          }
          if (!overlay) continue;
          const label = lineLabel(e, p);
          if (!label || (e.share < 0.01 && !label.includes("▲") && !isGrowingRetention(e, p))) continue;
          (heat(e, p.memory_mode, th, false, p) === "hot" ? hot : normal)
            .push({ range: new vscode.Range(range.end, range.end), renderOptions: { after: { contentText: label } } });
        }
        if (overlay) {
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
    this.runtime.dispose();
    this.status.dispose();
    this.lineDeco.dispose();
    this.hotDeco.dispose();
  }
}

function normKey(p: string): string {
  const s = p.replace(/\\/g, "/");
  return process.platform === "win32" ? s.toLowerCase() : s;
}
