/** Look up a profile's measurements by source path, and check source freshness. No `vscode` import. */
import { createHash } from "crypto";
import { FuncEntry, LineEntry, Profile } from './types';

export type Freshness = "fresh" | "stale" | "absent";

export function normPath(p: string, platform = process.platform): string {
  const s = p.replace(/\\/g, "/");
  return platform === "win32" ? s.toLowerCase() : s;
}

/** Same hash the profiler stores: sha1 of the file with CRLF normalised to LF. */
export function textHash(text: string): string {
  return createHash("sha1").update(Buffer.from(text.replace(/\r\n/g, "\n"), "utf8")).digest("hex");
}

/** Index a profile by normalised path so lookups survive drive-letter / slash differences. */
export class ProfileIndex {
  readonly files = new Map<string, Record<string, LineEntry>>();
  readonly functions = new Map<string, Record<string, FuncEntry>>();
  readonly hashes = new Map<string, string>();

  constructor(readonly profile: Profile, platform = process.platform) {
    for (const [k, v] of Object.entries(profile.files)) this.files.set(normPath(k, platform), v);
    for (const [k, v] of Object.entries(profile.functions ?? {})) this.functions.set(normPath(k, platform), v);
    for (const [k, v] of Object.entries(profile.file_hashes ?? {})) this.hashes.set(normPath(k, platform), v);
  }

  /** True if the profile has measurements for this file; callers can skip hashing otherwise. */
  has(path: string, platform = process.platform): boolean {
    return this.files.has(normPath(path, platform));
  }

  /** "fresh" only if the document text is exactly what was profiled. */
  state(path: string, text: string, platform = process.platform): Freshness {
    return this.has(path, platform) ? this.stateOfHash(path, textHash(text), platform) : "absent";
  }

  /** Same as state() for a precomputed textHash(), so callers can hash once per document version. */
  stateOfHash(path: string, hash: string, platform = process.platform): Freshness {
    const key = normPath(path, platform);
    if (!this.files.has(key)) return "absent";
    return this.hashes.get(key) === hash ? "fresh" : "stale";
  }

  line(path: string, line1: number, platform = process.platform): LineEntry | undefined {
    return this.files.get(normPath(path, platform))?.[String(line1)];
  }

  /**
   * True if line1 lies inside a function that was sampled. Such lines are never "cold": a line can run between samples
   * (or have its memory attributed to a neighbour) without appearing in the profile.
   */
  insideSampledFunction(path: string, line1: number, platform = process.platform): boolean {
    const key = normPath(path, platform);
    let spans = this.spans.get(key);
    if (!spans) {
      const last = new Map<number, number>();
      for (const [ln, e] of Object.entries(this.files.get(key) ?? {})) {
        if (e.func_line == null || e.samples === 0) continue;
        last.set(e.func_line, Math.max(last.get(e.func_line) ?? e.func_line, Number(ln)));
      }
      const fn = this.functions.get(key) ?? {};
      spans = [...last].filter(([first]) => fn[String(first)]?.name !== "<module>")
        .map(([first, end]) => [first, fn[String(first)]?.end_line ?? end]);
      this.spans.set(key, spans);
    }
    return spans.some(([a, b]) => line1 >= a && line1 <= b);
  }
  private readonly spans = new Map<string, [number, number][]>();
}
