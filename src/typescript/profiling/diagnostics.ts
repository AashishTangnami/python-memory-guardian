/** Severity adjustment of the static diagnostics: hot lines up, cold lines down, with measured evidence. */
import * as vscode from "vscode";
import { Freshness, ProfileIndex } from "../profile/index";
import { evidence } from "../profile/labels";
import { adjustSeverity, heat } from "../profile/metrics";
import { Thresholds } from "../profile/types";

export interface DiagnosticsContext {
  index: () => ProfileIndex | undefined;
  collection: () => vscode.DiagnosticCollection | undefined;
  thresholds: () => Thresholds;
  docState: (doc: vscode.TextDocument) => Freshness;
  /** New findings arrived for a document. */
  published: (uri: vscode.Uri) => void;
}

export class StaticDiagnostics {
  private readonly raw = new Map<string, vscode.Diagnostic[]>();

  constructor(private readonly context: DiagnosticsContext) {}

  /** Called from the language client middleware for every publishDiagnostics. */
  adjust(uri: vscode.Uri, diags: vscode.Diagnostic[]): vscode.Diagnostic[] {
    // Servers publish [] on close; dropping it keeps full refreshes proportional to files with findings.
    if (diags.length) this.raw.set(uri.toString(), diags);
    else this.raw.delete(uri.toString());
    this.context.published(uri);
    const idx = this.context.index();
    if (!idx || !diags.length) return diags;
    const doc = vscode.workspace.textDocuments.find((d) => d.uri.toString() === uri.toString());
    if (!doc || this.context.docState(doc) !== "fresh") return diags;
    const th = this.context.thresholds();
    return diags.map((d) => {
      const ln = d.range.start.line + 1;
      const e = idx.line(uri.fsPath, ln);
      const h = heat(e, idx.profile.memory_mode, th, idx.insideSampledFunction(uri.fsPath, ln), idx.profile);
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
  refresh(only?: vscode.Uri): void {
    const coll = this.context.collection();
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

  forget(key: string): void {
    this.raw.delete(key);
  }
}
