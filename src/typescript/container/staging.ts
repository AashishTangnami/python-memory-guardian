/**
 * Copy the Python helpers into <project>/.pmg/ so a container that bind-mounts the project can run them.
 * No `vscode` import: it needs only the extension's asAbsolutePath, so tests can stage with plain Node.
 */
import * as fs from "fs";
import * as path from "path";
import { checkRealFolder, notAFolder } from "./pmgFolder";

/** The Python helpers and language server, relative to the installed extension. */
export const PYTHON_DIR = "src/python";

/** The part of the extension context that staging needs. */
export interface ExtensionPaths { asAbsolutePath(relativePath: string): string; }

/** The profiler's package folder, staged next to `pmg_profile.py` so that it can import it. */
const PROFILER_PACKAGE = "pmg_profiler";
/** Helpers that earlier versions staged next to `pmg_profile.py` and the package now replaces. */
const STALE_HELPERS = ["pmg_allocations.py", "pmg_guard.py"];

/**
 * Copy a helper script (probe.py / pmg_profile.py) into <root>/.pmg/ so a container
 * that bind-mounts the project can run it. Returns the host path of the copy.
 * A symbolic link (or a file) at .pmg is refused: staging through it would write outside the project.
 * Anything but a plain file with one link in place of the helper (a symbolic link, a hard link, a pipe
 * that would block the read) is removed, never read or written through.
 */
export function stageHelper(ctx: ExtensionPaths, root: string, name: string): string {
  const src = ctx.asAbsolutePath(path.join(PYTHON_DIR, name));
  const dir = path.join(root, ".pmg");
  const dst = path.join(dir, name);
  checkRealFolder(dir);
  fs.mkdirSync(dir, { recursive: true });
  if (!plainFile(dst)) fs.rmSync(dst, { force: true });
  const fresh = fs.existsSync(dst) && fs.readFileSync(dst).equals(fs.readFileSync(src));
  if (!fresh) fs.copyFileSync(src, dst);
  return dst;
}

/** Best effort: a stale file that cannot be removed is never imported, so it must not stop a run. */
function removeStale(file: string): void {
  try { fs.rmSync(file, { force: true }); } catch { /* ignored */ }
}

/** True when `entry` is missing or a regular file that no other path shares (no hard link). */
function plainFile(entry: string): boolean {
  try {
    const st = fs.lstatSync(entry);
    return st.isFile() && st.nlink === 1;
  } catch { return true; }
}

/**
 * Stage the profiler and everything it loads. Returns the host path of the staged `pmg_profile.py`.
 * Only staged `.py` files are replaced or removed. A root-owned `__pycache__` that the container wrote
 * stays: the host user cannot delete it, and Python ignores a compiled file whose source changed.
 */
export function stageProfiler(ctx: ExtensionPaths, root: string): string {
  const staged = stageHelper(ctx, root, "pmg_profile.py");
  stageHelper(ctx, root, "pmg_diagnostics.py");
  for (const helper of STALE_HELPERS) removeStale(path.join(root, ".pmg", helper));
  const package_ = path.join(PYTHON_DIR, PROFILER_PACKAGE);
  const modules = fs.readdirSync(ctx.asAbsolutePath(package_)).filter((name) => name.endsWith(".py"));
  const target = path.join(root, ".pmg", PROFILER_PACKAGE);
  // A symbolic link (or a file) in place of the package folder is removed, never followed. stageHelper
  // does the same for a link in place of a module.
  if (notAFolder(target)) fs.unlinkSync(target);
  fs.mkdirSync(target, { recursive: true });
  for (const name of fs.readdirSync(target)) {
    if (name.endsWith(".py") && !modules.includes(name)) removeStale(path.join(target, name));
  }
  for (const name of modules) stageHelper(ctx, root, path.join(PROFILER_PACKAGE, name));
  return staged;
}
