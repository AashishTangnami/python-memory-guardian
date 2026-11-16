/** Which Python the extension targets: the configured interpreter or the configured container. */
import * as vscode from "vscode";
import { ContainerConfig, PathMapping, resolveMappings } from "../container/paths";

export function interpreter(): string {
  const configured = vscode.workspace
    .getConfiguration("pythonMemoryGuardian")
    .get<string>("interpreter", "")
    .trim();
  return configured || (process.platform === "win32" ? "python" : "python3");
}

/**
 * Container mode (plain Docker/Compose, editor on the host): set when
 * pythonMemoryGuardian.container.execPrefix is non-empty. Not needed for Dev
 * Containers, Codespaces, WSL or Remote-SSH, where the extension itself runs remotely.
 */
export function containerConfig(): ContainerConfig | undefined {
  const c = vscode.workspace.getConfiguration("pythonMemoryGuardian.container");
  const execPrefix = c.get<string[]>("execPrefix", []).filter((x) => typeof x === "string" && x);
  if (!execPrefix.length) return undefined;
  const ws = vscode.workspace.workspaceFolders?.[0]?.uri.fsPath ?? "";
  return {
    execPrefix,
    interpreter: c.get<string>("interpreter", "python3") || "python3",
    mappings: resolveMappings(c.get<PathMapping[]>("pathMappings", []), ws),
  };
}
