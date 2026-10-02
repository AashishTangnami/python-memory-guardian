/**
 * Container support for setups where the editor runs on the host but Python runs in a
 * container (plain Docker / Compose, no Dev Containers). Pure functions, no `vscode`.
 *
 *   execPrefix   e.g. ["docker", "compose", "exec", "-T", "app"]
 *   pathMappings e.g. [{ "local": "${workspaceFolder}", "container": "/app" }]
 *
 * Dev Containers / Codespaces / WSL / Remote-SSH do NOT need this: there the whole
 * extension runs next to the interpreter (extensionKind: workspace).
 */

export interface PathMapping { local: string; container: string; }

export interface ContainerConfig {
  execPrefix: string[];
  interpreter: string;          // Python inside the container, e.g. "python3"
  mappings: PathMapping[];
}

const fwd = (p: string) => p.replace(/\\/g, "/");
const trimSlash = (p: string) => (p.length > 1 ? p.replace(/\/+$/, "") : p);

/** Expand ${workspaceFolder} in configured mappings. */
export function resolveMappings(raw: PathMapping[], workspaceFolder: string): PathMapping[] {
  return raw
    .filter((m) => m && typeof m.local === "string" && typeof m.container === "string")
    .map((m) => ({
      local: trimSlash(fwd(m.local.replace("${workspaceFolder}", workspaceFolder))),
      container: trimSlash(fwd(m.container)),
    }));
}

function rebase(p: string, from: string, to: string, caseInsensitive: boolean): string | undefined {
  const a = caseInsensitive ? p.toLowerCase() : p;
  const b = caseInsensitive ? from.toLowerCase() : from;
  if (a === b) return to;
  if (a.startsWith(b + "/")) return to + p.slice(from.length);
  return undefined;
}

/** Host path -> container path (longest matching mapping wins). Throws if unmapped. */
export function toContainer(localPath: string, mappings: PathMapping[], hostIsWindows = process.platform === "win32"): string {
  const p = fwd(localPath);
  const sorted = [...mappings].sort((x, y) => y.local.length - x.local.length);
  for (const m of sorted) {
    const r = rebase(p, m.local, m.container, hostIsWindows);
    if (r !== undefined) return r;
  }
  throw new Error(`No pythonMemoryGuardian.container.pathMappings entry covers ${localPath}`);
}

/** Container path -> host path; returns the input unchanged if no mapping covers it. */
export function toLocal(containerPath: string, mappings: PathMapping[], hostIsWindows = process.platform === "win32"): string {
  const p = fwd(containerPath);
  const sorted = [...mappings].sort((x, y) => y.container.length - x.container.length);
  for (const m of sorted) {
    const r = rebase(p, m.container, m.local, false);   // container side is Linux: case-sensitive
    if (r !== undefined) return hostIsWindows ? r.replace(/\//g, "\\") : r;
  }
  return containerPath;
}

/** argv for running a Python script inside the container: prefix + python + script + args. */
export function containerCommand(cfg: ContainerConfig, scriptInContainer: string, args: string[]):
  { command: string; args: string[] } {
  if (!cfg.execPrefix.length) throw new Error("container.execPrefix is empty");
  const [command, ...rest] = cfg.execPrefix;
  return { command, args: [...rest, cfg.interpreter, scriptInContainer, ...args] };
}

/** Rewrite every path key in a profile from container paths to host paths. */
export function remapProfileKeys<T extends { script: string; files: Record<string, unknown>;
  functions?: Record<string, unknown>; file_hashes?: Record<string, string> }>(
  p: T, map: (containerPath: string) => string): T {
  const re = <V>(o: Record<string, V> | undefined) =>
    o && Object.fromEntries(Object.entries(o).map(([k, v]) => [map(k), v]));
  return { ...p, script: map(p.script), files: re(p.files)!, functions: re(p.functions),
    file_hashes: re(p.file_hashes) };
}
