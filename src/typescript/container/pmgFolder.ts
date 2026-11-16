/**
 * Safe writes into the project's .pmg folder. A cloned repository can contain links there, so every
 * write checks that the folder is real and never writes through a link it did not create.
 * No `vscode` import, so tests can use it with plain Node.
 */
import { randomBytes } from "crypto";
import * as fs from "fs";

/** True for a symbolic link or any other non-folder entry; reads the entry itself, never its target. */
export function notAFolder(entry: string): boolean {
  try { return !fs.lstatSync(entry).isDirectory(); } catch { return false; }
}

/** Throw unless `dir` is missing or a real folder, so nothing is written through a linked folder. */
export function checkRealFolder(dir: string): void {
  if (notAFolder(dir)) {
    throw new Error(`${dir} is a symbolic link or a file, not a folder; Python Memory Guardian writes only into a real folder there, so remove it (it is created again)`);
  }
}

/**
 * Write through a new temporary file with a random name (created exclusively, so no planted link is
 * followed), then rename it over `target`. A rename replaces a link at `target` itself, never its target.
 */
export function writeFileAtomic(target: string, data: string): void {
  const temporary = `${target}.${process.pid}.${randomBytes(6).toString("hex")}.tmp`;
  try {
    fs.writeFileSync(temporary, data, { flag: "wx" });
    fs.renameSync(temporary, target);
  } catch (e) {
    try { fs.rmSync(temporary, { force: true }); } catch { /* ignored */ }
    throw e;
  }
}
