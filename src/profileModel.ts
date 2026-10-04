/**
 * Pure logic for runtime profiles written by server/pmg_profile.py.
 * No `vscode` import, so it is unit-testable with plain Node.
 */
import { createHash } from "crypto";

export interface LineEntry {
  time_s: number; share: number;
  python_s: number; native_s: number; system_s: number; cpu_unsplit_s: number;
  samples: number; rss_growth_mb: number; rss_release_mb: number;
  line_events?: number;
  alloc_mb?: number; transient_peak_mb?: number; peak_mb?: number; end_mb?: number;
  /** precise mode: growth of tracemalloc's own bookkeeping while this line ran (profiler memory inside rss_growth_mb). */
  profiler_mb?: number;
  leak_runs?: number; func_line?: number;
  /** Names from the source line (via ast): enclosing scope, assigned variables, called functions. */
  scope?: string; assigns?: string[]; calls?: string[];
  /** For leaks: the variables still referencing the leaked objects at exit. */
  held_by?: { holder: string; type: string; items: number; matching: number }[];
  retention?: { snapshots: number; rises: number; releases: number; growth_mb: number;
    observed_s: number; peak_mb?: number; points: [number, number][] };
}

export interface FuncEntry {
  name: string; time_s: number; python_s: number; native_s: number; system_s: number;
  peak_mb: number; transient_peak_mb: number; alloc_mb?: number; rss_growth_mb?: number; profiler_mb?: number;
  end_line?: number | null;
}

export interface StackFrame { file: string; line: number; name: string; first_line: number; user: boolean; }
export interface StackSample { thread: string; thread_name: string; frames: number[];
  python_s: number; native_s: number; system_s: number; unsplit_s: number; samples: number;
  /** precise mode: traced growth seen while this was the busiest thread's stack (sampled, net between samples). */
  alloc_bytes?: number; }

export interface Profile {
  schema: number; script: string; python: string; gil_split: boolean;
  wall_s: number; cpu_s?: number; memory_mode: "fast" | "precise" | "off";
  samples?: number; interval_s?: number; snapshots?: number;
  rss_kind?: "current" | "peak" | null;
  rss_start_mb?: number | null; rss_end_mb?: number | null;
  timeline?: [number, number, number][]; // elapsed seconds, traced MB, process RSS MB
  peak_traced_mb: number | null; rss_peak_mb: number; native_untraced_mb: number | null;
  /** precise mode: MB held at the peak whose tracebacks never reached the user's code. */
  unattributed_peak_mb?: number | null; frames?: number | null;
  /** precise mode: elapsed seconds at which the script stopped tracemalloc; memory evidence ends there. */
  memory_tracing_lost_s?: number | null;
  /** The sampler stopped early with this error; timing after it is missing. */
  sampler_error?: string | null;
  file_hashes: Record<string, string>;
  files: Record<string, Record<string, LineEntry>>;
  functions: Record<string, Record<string, FuncEntry>>;
  stacks?: { frames: StackFrame[]; samples: StackSample[]; dropped_s: number; depth_limited: boolean };
  /** precise mode: bytes per allocation traceback at the peak and exit snapshots, outermost frame first. */
  memory_stacks?: MemoryStacks | null;
  /** fast/precise: objects still held at exit by globals or your instances' attributes, sized by sys.getsizeof. */
  largest_objects?: { objects: LargestObject[]; complete: boolean } | null;
  monitoring?: { requested: 'off' | 'lines'; active: boolean; reason: string | null;
    dropped_line_events: number };
  /** Per timeline point: index into stacks.samples of the main thread's stack then; -1 when it had none. */
  timeline_stacks?: number[];
  /** precise mode: largest tracemalloc bookkeeping seen (profiler memory, not the program's). */
  tracemalloc_peak_mb?: number | null;
  /** precise mode scoped to one function (--trace-function): its name, traced calls, traced seconds. */
  trace_function?: { name: string; calls: number; traced_s: number } | null;
  /** How the run was started: the script's own arguments, UTC start time, sys.platform. Used to compare runs. */
  run?: { argv: string[]; started_at: string | null; platform: string };
}

export interface MemoryStackTable {
  t: number; total_bytes: number; other_bytes: number;
  stacks: { frames: number[]; bytes: number; truncated: boolean }[];
}
export interface MemoryStacks { depth: number; frames: StackFrame[]; peak: MemoryStackTable | null; exit: MemoryStackTable; }

export interface LargestObject { holder: string; type: string; mb: number; items: number | null; estimated: boolean; }

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
    const record = (v: unknown): v is Record<string, any> => !!v && typeof v === 'object' && !Array.isArray(v);
    const number = (v: unknown) => typeof v === 'number' && Number.isFinite(v) && v >= 0;
    const numeric = (v: Record<string, any>, fields: string[]) => fields.every(k => number(v[k]));
    if (!record(p) || ![2, 3].includes(p.schema) || typeof p.script !== 'string'
      || typeof p.python !== 'string' || typeof p.gil_split !== 'boolean'
      || !number(p.wall_s) || !['fast', 'precise', 'off'].includes(p.memory_mode)
      || !record(p.files) || !record(p.functions ?? {}) || !record(p.file_hashes ?? {})) return undefined;
    if (![p.peak_traced_mb, p.rss_peak_mb, p.unattributed_peak_mb].every(v => v == null || number(v))) return undefined;
    if (!['cpu_s', 'samples', 'interval_s', 'snapshots', 'rss_start_mb', 'rss_end_mb']
      .every(k => p[k] == null || number(p[k]))) return undefined;
    if (p.rss_kind != null && !['current', 'peak'].includes(p.rss_kind)) return undefined;
    if (!(p.memory_tracing_lost_s == null || number(p.memory_tracing_lost_s))) return undefined;
    if (p.sampler_error != null && typeof p.sampler_error !== 'string') return undefined;
    if (p.timeline != null && (!Array.isArray(p.timeline) || p.timeline.length > 10000
      || !p.timeline.every((point: unknown) => Array.isArray(point) && point.length === 3
        && point.every(number))
      || p.timeline.some((point: [number, number, number], i: number) => i > 0 && point[0] < p.timeline[i - 1][0]))) return undefined;
    if (!Object.values(p.file_hashes ?? {}).every(v => typeof v === 'string')) return undefined;
    for (const entries of Object.values(p.files)) {
      if (!record(entries)) return undefined;
      for (const [line, e] of Object.entries(entries)) {
        if (!/^[1-9]\d*$/.test(line) || !record(e) || !numeric(e,
          ['time_s', 'share', 'python_s', 'native_s', 'system_s', 'samples', 'rss_growth_mb', 'rss_release_mb'])) return undefined;
        for (const k of ['alloc_mb', 'transient_peak_mb', 'peak_mb', 'end_mb', 'leak_runs', 'func_line', 'profiler_mb']) {
          if (e[k] != null && !number(e[k])) return undefined;
        }
        if (e.line_events != null && (!Number.isSafeInteger(e.line_events) || e.line_events < 0)) return undefined;
        if (e.scope != null && typeof e.scope !== 'string') return undefined;
        for (const k of ['assigns', 'calls']) {
          if (e[k] != null && (!Array.isArray(e[k]) || !e[k].every((v: unknown) => typeof v === 'string'))) return undefined;
        }
        if (e.held_by != null && (!Array.isArray(e.held_by) || !e.held_by.every((h: unknown) =>
          record(h) && typeof h.holder === 'string' && typeof h.type === 'string' && numeric(h, ['items', 'matching'])))) return undefined;
        if (e.retention != null) {
          const r = e.retention;
          if (!record(r) || !numeric(r, ['snapshots', 'rises', 'releases', 'observed_s'])
            || (r.peak_mb != null && !number(r.peak_mb))
            || typeof r.growth_mb !== 'number' || !Number.isFinite(r.growth_mb)
            || !Array.isArray(r.points) || !r.points.every((v: unknown) =>
              Array.isArray(v) && v.length === 2 && v.every(number))) return undefined;
        }
      }
    }
    for (const entries of Object.values(p.functions ?? {})) {
      if (!record(entries) || !Object.values(entries).every(f => record(f) && typeof f.name === 'string'
        && numeric(f, ['time_s', 'python_s', 'native_s', 'system_s', 'peak_mb', 'transient_peak_mb'])
        && ['alloc_mb', 'rss_growth_mb', 'profiler_mb'].every(k => f[k] == null || number(f[k]))
        && (f.end_line == null || number(f.end_line)))) return undefined;
    }
    if (p.stacks != null) {
      const s = p.stacks;
      if (!record(s) || !number(s.dropped_s) || typeof s.depth_limited !== 'boolean'
        || !Array.isArray(s.frames) || !s.frames.every((f: unknown) => record(f)
          && typeof f.file === 'string' && typeof f.name === 'string' && typeof f.user === 'boolean'
          && Number.isInteger(f.line) && f.line >= 0 && Number.isInteger(f.first_line) && f.first_line >= 0)
        || !Array.isArray(s.samples) || !s.samples.every((v: unknown) => record(v)
          && typeof v.thread === 'string' && typeof v.thread_name === 'string'
          && numeric(v, ['python_s', 'native_s', 'system_s', 'unsplit_s', 'samples']) && (v.alloc_bytes == null || number(v.alloc_bytes))
          && Array.isArray(v.frames) && v.frames.length <= 128
          && v.frames.every((id: unknown) => Number.isInteger(id) && Number(id) >= 0 && Number(id) < s.frames.length))) return undefined;
    }
    if (p.memory_stacks != null) {
      const ms = p.memory_stacks;
      const table = (t: unknown) => record(t) && numeric(t, ['t', 'total_bytes', 'other_bytes']) && Array.isArray(t.stacks)
        && t.stacks.every((s: unknown) => record(s) && number(s.bytes) && typeof s.truncated === 'boolean'
          && Array.isArray(s.frames) && s.frames.length <= 256
          && s.frames.every((id: unknown) => Number.isInteger(id) && Number(id) >= 0 && Number(id) < ms.frames.length));
      if (!record(ms) || !Number.isInteger(ms.depth) || ms.depth < 1 || !Array.isArray(ms.frames)
        || !ms.frames.every((f: unknown) => record(f) && typeof f.file === 'string' && typeof f.name === 'string'
          && typeof f.user === 'boolean' && Number.isInteger(f.line) && f.line >= 0 && Number.isInteger(f.first_line) && f.first_line >= 0)
        || !table(ms.exit) || (ms.peak != null && !table(ms.peak))) return undefined;
    }
    if (p.largest_objects != null) {
      const lo = p.largest_objects;
      if (!record(lo) || typeof lo.complete !== 'boolean' || !Array.isArray(lo.objects) || lo.objects.length > 100
        || !lo.objects.every((o: unknown) => record(o) && typeof o.holder === 'string' && typeof o.type === 'string'
          && number(o.mb) && typeof o.estimated === 'boolean' && (o.items == null || (Number.isSafeInteger(o.items) && o.items >= 0)))) return undefined;
    }
    if (p.monitoring != null && (!record(p.monitoring)
      || !['off', 'lines'].includes(p.monitoring.requested)
      || typeof p.monitoring.active !== 'boolean'
      || (p.monitoring.reason != null && typeof p.monitoring.reason !== 'string')
      || !Number.isSafeInteger(p.monitoring.dropped_line_events)
      || p.monitoring.dropped_line_events < 0)) return undefined;
    if (p.timeline_stacks != null && (!Array.isArray(p.timeline_stacks) || p.timeline_stacks.length !== (p.timeline ?? []).length
      || !p.timeline_stacks.every((i: unknown) => Number.isInteger(i) && Number(i) >= -1 && Number(i) < (p.stacks?.samples.length ?? 0)))) return undefined;
    if (!(p.tracemalloc_peak_mb == null || number(p.tracemalloc_peak_mb))) return undefined;
    if (p.trace_function != null && (!record(p.trace_function) || typeof p.trace_function.name !== 'string'
      || !Number.isSafeInteger(p.trace_function.calls) || p.trace_function.calls < 0 || !number(p.trace_function.traced_s))) return undefined;
    if (p.run != null && (!record(p.run) || !Array.isArray(p.run.argv) || !p.run.argv.every((a: unknown) => typeof a === 'string')
      || (p.run.started_at != null && typeof p.run.started_at !== 'string') || typeof p.run.platform !== 'string')) return undefined;
    return p as unknown as Profile;
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

  /** True if the profile has measurements for this file; callers can skip hashing otherwise. */
  has(path: string, platform = process.platform): boolean {
    return this.files.has(normPath(path, platform));
  }

  /** "fresh" only if the document text is exactly what was profiled. */
  state(path: string, text: string, platform = process.platform): "fresh" | "stale" | "absent" {
    return this.has(path, platform) ? this.stateOfHash(path, textHash(text), platform) : "absent";
  }

  /** Same as state() for a precomputed textHash(), so callers can hash once per document version. */
  stateOfHash(path: string, hash: string, platform = process.platform): "fresh" | "stale" | "absent" {
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

/**
 * Precise mode: process-memory growth beyond traced Python growth and tracemalloc's own bookkeeping growth
 * (profiler_mb) on the same line, for any library. All are charged to the line running at each sample; RSS
 * grows where memory is first written and rarely shrinks, so this is an estimate of native memory (C
 * extensions, their own allocators). The reported bookkeeping misstates tracing's real cost in both
 * directions (by up to about 1.4x of itself, measured), so an estimate within twice the bookkeeping is
 * indistinguishable from profiler overhead and reads 0. Profiles without profiler_mb keep the plain difference.
 * run: when the profile itself found no native memory beyond tracing overhead (native_untraced_mb 0 with
 * tracemalloc_peak_mb recorded), every line reads 0 too: RSS is charged where pages are first touched and
 * bookkeeping where tables grow, so per-line differences do not line up (measured: 372 MB "native" on a
 * pure-Python function with 0.08 MB of bookkeeping growth).
 */
export type NativeRun = Pick<Profile, 'memory_mode'> & { tracemalloc_peak_mb?: number | null; native_untraced_mb?: number | null;
  trace_function?: { name: string } | null };
export function nativeMb(e: { rss_growth_mb?: number; alloc_mb?: number; profiler_mb?: number } | undefined, run?: NativeRun): number {
  if (!e) return 0;
  if (run && run.memory_mode === 'precise' && run.tracemalloc_peak_mb != null && !((run.native_untraced_mb ?? 0) > 0)) return 0;
  // With --trace-function, Python memory allocated outside the traced calls is untraced: RSS growth there is not native.
  if (run?.trace_function) return 0;
  const prof = e.profiler_mb ?? 0, native = (e.rss_growth_mb ?? 0) - (e.alloc_mb ?? 0) - prof;
  return native > 2 * prof ? native : 0;
}

export function memoryMb(e: LineEntry | undefined, mode: Profile["memory_mode"], run?: NativeRun): number {
  if (!e) return 0;
  if (mode === "precise") {
    return Math.max(e.alloc_mb ?? 0, e.transient_peak_mb ?? 0, e.peak_mb ?? 0, nativeMb(e, run));
  }
  return mode === "fast" ? e.rss_growth_mb : 0;
}

export function heat(e: LineEntry | undefined, mode: Profile["memory_mode"], th: Thresholds,
                     insideSampledFn = false, run?: NativeRun): Heat {
  if (!e || (e.samples === 0 && memoryMb(e, mode, run) === 0))
    return insideSampledFn || !!e?.line_events ? "unknown" : "cold";
  if (e.share >= th.hotShare || memoryMb(e, mode, run) >= th.hotMb || e.leak_runs) return "hot";
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
    //   alloc = net growth of traced memory while this line ran, summed over the run (can exceed what
    //           is alive; allocate-and-free churn between two samples is not counted)
    //   held  = still referenced at the memory peak
    //   spike = short-lived peak that rose and fell between two samples
    const mem: string[] = [];
    if ((e.alloc_mb ?? 0) >= 1) mem.push(`alloc ${mb(e.alloc_mb!)}`);
    if ((e.peak_mb ?? 0) >= 1) mem.push(`held ${mb(e.peak_mb!)}`);
    if ((e.transient_peak_mb ?? 0) >= 1) mem.push(`spike ${mb(e.transient_peak_mb!)}`);
    if (nativeMb(e, p) >= 1) mem.push(`native ≈ +${mb(nativeMb(e, p))}`);
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
  if (p.memory_mode === "precise" && nativeMb(f, p) >= 1) out.push(`· native ≈ +${mb(nativeMb(f, p))}`);
  return out.join(" ");
}

/** Prefix for a static diagnostic, given measured data. */
export function evidence(e: LineEntry | undefined, h: Heat, p: Profile): string {
  if (h === "hot" && e) {
    const bits = [`${pct(e.share)} of runtime`];
    const m = memoryMb(e, p.memory_mode, p);
    if (m >= 1) bits.push(mb(m));
    if (e.leak_runs) bits.push("leaking");
    return `🔥 Measured in last profile: ${bits.join(", ")}. `;
  }
  if (h === "cold") return "❄ No runtime samples recorded here; execution is unconfirmed. ";
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

/** Conditions that make part of a run's evidence incomplete; shown in the status tooltip and report. */
export function runNotes(p: Profile): string[] {
  const notes: string[] = [];
  if (p.memory_tracing_lost_s != null) {
    notes.push(`The script stopped tracemalloc at ${secs(p.memory_tracing_lost_s)}; precise memory evidence ` +
      `covers only the run before that, and leak detection was skipped.`);
  }
  if (p.sampler_error) notes.push(`Sampling stopped early (${p.sampler_error}); later timing is missing.`);
  return notes;
}

export function leakMessage(e: LineEntry): string {
  const holders = (e.held_by ?? []).map((h) =>
    `\`${h.holder}\` (${h.type}, ${h.items} item${h.items === 1 ? "" : "s"})`);
  const held = holders.length
    ? `is still referenced by ${holders.join(" and ")}`
    : "was still held (holder not found)";
  return `${runtimePrefix(e)}⚠️ Suspected memory leak: retained allocations grew across ${e.leak_runs} ` +
    `trailing snapshot increases, and ${mb(e.end_mb ?? 0)} ${held} when profiling ended. ` +
    (holders.length ? "Bound it, evict old entries, or stop storing per-call data there."
                    : "Check for containers that only grow (globals, caches without a bound).");
}
