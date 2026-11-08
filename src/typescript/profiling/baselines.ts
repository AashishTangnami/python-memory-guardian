/** Save the loaded profile as a named baseline, so later runs can be compared to it. */
import { execFile } from "child_process";
import * as fs from "fs";
import * as path from "path";
import * as vscode from "vscode";
import { baselineFileName } from "../compare/compare";
import { checkRealFolder, writeFileAtomic } from "../container/pmgFolder";
import { Profile } from "../profile/types";

/** <workspace folder>/.pmg/baselines for the loaded profile (or, outside a workspace, next to its .pmg folder). */
export function baselineDir(loaded: vscode.Uri | undefined): string | undefined {
  const uri = loaded;
  if (!uri) return vscode.workspace.workspaceFolders?.[0] && path.join(vscode.workspace.workspaceFolders[0].uri.fsPath, ".pmg", "baselines");
  const root = vscode.workspace.getWorkspaceFolder(uri)?.uri.fsPath
    ?? (path.basename(path.dirname(uri.fsPath)) === ".pmg" ? path.dirname(path.dirname(uri.fsPath)) : path.dirname(uri.fsPath));
  return path.join(root, ".pmg", "baselines");
}

/** Copy the loaded profile to .pmg/baselines/<name>.json with the git commit, so later runs can be compared to it. */
export async function saveBaseline(loaded: { text: string } | undefined, dir: string | undefined, p: Profile | undefined,
                                   saved: (name: string) => void): Promise<void> {
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
    checkRealFolder(path.dirname(dir));
    checkRealFolder(dir);
    fs.mkdirSync(dir, { recursive: true });
    writeFileAtomic(target, JSON.stringify(json));
  } catch (e) {
    void vscode.window.showErrorMessage(`Could not save the baseline: ${e}`);
    return;
  }
  saved(name!.trim());
  void vscode.window.showInformationMessage(`Saved baseline "${name!.trim()}". Change your code, profile again, and open the report's Compare tab.`);
}
