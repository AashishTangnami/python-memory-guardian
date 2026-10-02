/**
 * Pure logic for runtime profiles written by server/pmg_profile.py.
 * No `vscode` import, so it is unit-testable with plain Node.
 */
import { createHash } from "crypto";

export interface LineEntry {
  time_s: number; share: number;
  python_s: number; native_s: number; system_s: number; cpu_unsplit_s: number;
  samples: number; rss_growth_mb: number; rss_release_mb: number;
  alloc_mb?: number; transient_peak_mb?: number; peak_mb?: number; end_mb?: number;
  leak_runs?: number; func_line?: number;
  /** Names from the source line (via ast): enclosing scope, assigned variables, called functions. */
  scope?: string; assigns?: string[]; calls?: string[];
  /** For leaks: the variables still referencing the leaked objects at exit. */
  held_by?: { holder: string; type: string; items: number; matching: number }[];
}

export interface FuncEntry {
  name: string; time_s: number; python_s: number; native_s: number; system_s: number;
  peak_mb: number; transient_peak_mb: number; alloc_mb?: number; rss_growth_mb?: number;
}

export interface Profile {
  schema: number; script: string; python: string; gil_split: boolean;
  wall_s: number; cpu_s: number; memory_mode: "fast" | "precise" | "off";
  peak_traced_mb: number | null; rss_peak_mb: number; native_untraced_mb: number | null;
  /** precise mode: MB held at the peak whose tracebacks never reached the user's code. */
  unattributed_peak_mb?: number | null; frames?: number | null;
  file_hashes: Record<string, string>;
  files: Record<string, Record<string, LineEntry>>;
  functions: Record<string, Record<string, FuncEntry>>;
}

export interface Thresholds { hotShare: number; hotMb: number; }

/** vscode.DiagnosticSeverity values, duplicated to stay vscode-free. */
export const Sev = { Error: 0, Warning: 1, Information: 2, Hint: 3 } as const;

export type Heat = "hot" | "cold" | "unknown";

export function normPath(p: string, platform = process.platform): string {
  const s = p.replace(/\\/g, "/");
  return platform === "win32" ? s.toLowerCase() : s;
}

/** Same hash the profiler stores: sha1 of the file with CRLF normalised to LF. */
export function textHash(text: string): string {
  return createHash("sha1").update(Buffer.from(text.replace(/\r\n/g, "\n"), "utf8")).digest("hex");
}

export function parseProfile(json: string): Profile | undefined {
  try {
    const p = JSON.parse(json);
    return p && p.schema >= 2 && p.files ? (p as Profile) : undefined;
  } catch {
    return undefined;
  }
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

  /** "fresh" only if the document text is exactly what was profiled. */
  state(path: string, text: string, platform = process.platform): "fresh" | "stale" | "absent" {
    const key = normPath(path, platform);
    if (!this.files.has(key)) return "absent";
    return this.hashes.get(key) === textHash(text) ? "fresh" : "stale";
  }

  line(path: string, line1: number, platform = process.platform): LineEntry | undefined {
    return this.files.get(normPath(path, platform))?.[String(line1)];
  }

  /**
   * True if line1 lies inside a function that was sampled (from its `def` line to its
   * last sampled line). Such lines are never "cold": a line can run between samples
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
      spans = [...last].filter(([first]) => fn[String(first)]?.name !== "<module>");
      this.spans.set(key, spans);
    }
    return spans.some(([a, b]) => line1 >= a && line1 <= b);
  }
  private readonly spans = new Map<string, [number, number][]>();
}

export function memoryMb(e: LineEntry | undefined, mode: Profile["memory_mode"]): number {
  if (!e) return 0;
  if (mode === "precise") {
    return Math.max(e.alloc_mb ?? 0, e.transient_peak_mb ?? 0, e.peak_mb ?? 0);
  }
  return mode === "fast" ? e.rss_growth_mb : 0;
}

export function heat(e: LineEntry | undefined, mode: Profile["memory_mode"], th: Thresholds,
                     insideSampledFn = false): Heat {
  if (!e || (e.samples === 0 && memoryMb(e, mode) === 0)) return insideSampledFn ? "unknown" : "cold";
  if (e.share >= th.hotShare || memoryMb(e, mode) >= th.hotMb || e.leak_runs) return "hot";
  return "unknown";
}

/** Hot raises severity one step; cold lowers non-errors to Hint; otherwise unchanged. */
export function adjustSeverity(sev: number, h: Heat): number {
  if (h === "hot") return Math.max(Sev.Error, sev - 1);
  if (h === "cold" && sev !== Sev.Error) return Sev.Hint;
  return sev;
}

const pct = (x: number) => `${(100 * x).toFixed(x >= 0.1 ? 0 : 1)}%`;
const mb = (x: number) => (x >= 100 ? `${x.toFixed(0)} MB` : `${x.toFixed(1)} MB`);
const secs = (x: number) => (x >= 10 ? `${x.toFixed(1)} s` : `${x.toFixed(2)} s`);

export function timeSplit(e: { time_s: number; python_s: number; native_s: number; system_s: number }): string {
  if (e.time_s <= 0) return "";
  const parts: [string, number][] = [["py", e.python_s], ["native", e.native_s], ["sys", e.system_s]];
  return parts.filter(([, v]) => v / e.time_s >= 0.05)
    .map(([k, v]) => `${k} ${pct(v / e.time_s)}`).join(" · ");
}

/** Inline end-of-line label, e.g. "⏱ 0.61 s 8.8% (native 97%)  ▲ 160 MB". */
export function lineLabel(e: LineEntry, p: Profile): string {
  const out: string[] = [];
  if (e.time_s > 0) {
    const split = p.gil_split ? timeSplit(e) : "";
    const skew = p.memory_mode === "precise" ? " ~" : "";   // tracing inflates timings
    out.push(`⏱ ${secs(e.time_s)}${skew} ${pct(e.share)}${split ? ` (${split})` : ""}`);
  }
  const into = e.assigns?.length ? ` → ${e.assigns.join(", ")}` : "";
  if (p.memory_mode === "fast") {
    if (e.rss_growth_mb >= 1) out.push(`▲ RSS +${mb(e.rss_growth_mb)}${into}`);
  } else if (p.memory_mode === "precise") {
    // Three different quantities - never shown as one number:
    //   alloc = total allocated by this line over the run (can far exceed what is alive)
    //   held  = still referenced at the memory peak
    //   spike = short-lived peak that rose and fell between two samples
    const mem: string[] = [];
    if ((e.alloc_mb ?? 0) >= 1) mem.push(`alloc ${mb(e.alloc_mb!)}`);
    if ((e.peak_mb ?? 0) >= 1) mem.push(`held ${mb(e.peak_mb!)}`);
    if ((e.transient_peak_mb ?? 0) >= 1) mem.push(`spike ${mb(e.transient_peak_mb!)}`);
    if (mem.length) out.push(`▲ ${mem.join(" · ")}${into}`);
  }
  if (e.leak_runs) {
    const who = e.held_by?.length ? ` by ${e.held_by.map((h) => h.holder).join(", ")}` : "";
    out.push(`⚠ leak: ${mb(e.end_mb ?? 0)} held${who}`);
  }
  return out.join("   ");
}

export function funcLabel(f: FuncEntry, p: Profile): string {
  const out = [`Σ ${f.name}(): ${secs(f.time_s)}`];
  const split = p.gil_split ? timeSplit(f) : "";
  if (split) out.push(`(${split})`);
  const m = p.memory_mode === "precise"
    ? Math.max(f.peak_mb, f.transient_peak_mb, f.alloc_mb ?? 0)
    : p.memory_mode === "fast" ? (f.rss_growth_mb ?? 0) : 0;
  if (m >= 1) out.push(p.memory_mode === "fast" ? `· RSS +${mb(m)}` : `· ${mb(m)}`);
  return out.join(" ");
}

/** Prefix for a static diagnostic, given measured data. */
export function evidence(e: LineEntry | undefined, h: Heat, p: Profile): string {
  if (h === "hot" && e) {
    const bits = [`${pct(e.share)} of runtime`];
    const m = memoryMb(e, p.memory_mode);
    if (m >= 1) bits.push(mb(m));
    if (e.leak_runs) bits.push("leaking");
    return `🔥 Measured in last profile: ${bits.join(", ")}. `;
  }
  if (h === "cold") return "❄ Not reached in the last profile (its code never showed up in any sample). ";
  return "";
}

/** "Service.handle() › `self.history.append` — " for runtime diagnostics (same style as static ones). */
export function runtimePrefix(e: LineEntry): string {
  const subject = e.calls?.[0] ?? e.assigns?.[0];
  const parts = [e.scope, subject ? `\`${subject}\`` : undefined].filter(Boolean);
  return parts.length ? `${parts.join(" › ")} — ` : "";
}

/** Status-bar tooltip note when much of the held memory couldn't be tied to a line. */
export function unattributedNote(p: Profile): string {
  const u = p.unattributed_peak_mb ?? 0;
  if (p.memory_mode !== "precise" || u < 1) return "";
  return `${mb(u)} held at the peak was allocated deep inside library code (e.g. an import) and ` +
    `couldn't be traced back to your lines with ${p.frames ?? 2}-frame tracebacks. Raise ` +
    `pythonMemoryGuardian.profile.frames to attribute more (slower).`;
}

export function leakMessage(e: LineEntry): string {
  const holders = (e.held_by ?? []).map((h) =>
    `\`${h.holder}\` (${h.type}, ${h.items} item${h.items === 1 ? "" : "s"})`);
  const held = holders.length
    ? `is still referenced by ${holders.join(" and ")}`
    : "was still held (holder not found)";
  return `${runtimePrefix(e)}⚠️ Runtime leak (measured): memory allocated on this line never went down and ` +
    `grew in ${e.leak_runs} snapshots, and ${mb(e.end_mb ?? 0)} ${held} when the program exited. ` +
    (holders.length ? "Bound it, evict old entries, or stop storing per-call data there."
                    : "Check for containers that only grow (globals, caches without a bound).");
}
