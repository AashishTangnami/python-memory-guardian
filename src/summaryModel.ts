/**
 * Machine-readable profile summary (format "pmg-summary/1"), written as .pmg/summary.json after each profiling run
 * so scripts and agents can use the results without reading the report. Every number comes from the profile (or
 * from the report models that the report itself uses), and each section states how it was measured and its limits.
 * No `vscode` import, so it is unit-testable with plain Node. Schema: docs/pmg-summary.schema.json.
 */
import { FuncEntry, heat, LineEntry, nativeMb, normPath, Profile, runNotes, Thresholds } from './profileModel';
import { diagnose } from './reportModel';
import { Comparison } from './compareModel';

export const SUMMARY_FORMAT = 'pmg-summary/1';
/** Comparison measures under the same names the rest of the summary uses. */
const COMPARE_FIELDS: Record<string, string> = { alloc: 'allocated_mb', held: 'held_at_peak_mb', exit: 'held_at_exit_mb',
  native: 'native_estimate_mb', rss: 'rss_growth_mb' };

export type Freshness = 'fresh' | 'stale' | 'unverified';
export interface StaticFinding { line: number; code: string; severity: 'error' | 'warning' | 'information' | 'hint'; message: string; }

export interface SummaryInputs {
  profile: Profile;
  /** Absolute path of the profile JSON this summary describes. */
  profilePath: string;
  /** Project folder: paths in the summary are relative to it when inside it. */
  root: string;
  generatedAt: string;
  generator: string;
  /** Per profiled file: does the current source still match what was profiled? */
  freshness: Record<string, Freshness>;
  /** Static findings per profiled file the language server analyzed (open in the editor); others are absent. */
  staticDiagnostics: Record<string, StaticFinding[]>;
  thresholds: Thresholds;
  comparison: { label: string; result: Comparison } | null;
  platform?: NodeJS.Platform;
}

/** A value with its unit, how it was obtained, and what it cannot show. */
export interface Measured { value: number; unit: 's' | 'MB' | 'count'; method: string; limits: string[]; }

const r3 = (x: number) => Math.round(x * 1000) / 1000;

function relPath(file: string, root: string, platform: NodeJS.Platform): string {
  const f = file.replace(/\\/g, '/'), base = root.replace(/\\/g, '/').replace(/\/?$/, '/');
  return normPath(f, platform).startsWith(normPath(base, platform)) ? f.slice(base.length) : f;
}

/** How each kind of evidence was collected, and its limits, for this run's mode and settings. */
function methods(p: Profile) {
  const interval = p.interval_s ?? 0.01;
  const time = {
    method: `Sampled every ${interval} s from a background thread; each sample's elapsed time is charged to the innermost line of your code on each thread.` +
      (p.gil_split ? ' Python, native and waiting time are estimated from the thread CPU clock and how long the sampler waited for the GIL.' : ' No GIL signal (free-threaded build or per-thread clock unavailable): CPU time is reported unsplit.'),
    limits: ['sampled: values from few samples are rough (each sample is worth about one interval, more in precise mode)',
      'native time is estimated at the Python call site; C/C++ stacks are not captured',
      'across threads, sampled time can exceed the run duration',
      ...(p.memory_mode === 'precise' ? ['precise mode slows allocation-heavy code (tracemalloc), so times are inflated'] : [])],
  };
  const rss = {
    method: 'Process resident memory (RSS) read at each sample; growth is charged to the busiest line of your code at that sample.',
    limits: ['charged where memory is first written, which may not be the allocating line',
      'freed memory often stays resident (allocator arenas), so RSS rarely falls and identical runs can differ',
      ...(p.rss_kind === 'peak' ? ['this platform only provides a running RSS peak, so decreases are invisible'] : [])],
  };
  const traced = {
    method: `tracemalloc: every Python object allocation at its requested size, with ${p.frames ?? 2}-frame tracebacks; snapshots of what is still held, taken periodically, when memory plateaus at a new high or doubles, and at exit.`,
    limits: ['memory C extensions take from the system directly is not traced (see native estimate)',
      '"allocated" is net traced growth between samples; allocate-and-free churn between two samples is not counted',
      '"held at peak" is what each line held at the snapshot holding the most memory, not each line\'s own maximum',
      `tracebacks are cut at ${p.frames ?? 2} frames; allocations deeper inside libraries may not reach your code`],
  };
  const native = {
    method: 'Estimate: process memory growth beyond traced Python growth on the same line (precise mode).',
    limits: ['estimate with the limits of RSS: charged where memory is first written, rarely falls'],
  };
  return { time, rss, traced, native };
}

/** Profile values pass through unchanged, so each one can be found in the profile; derived values are rounded by the caller. */
function measured(value: number | null | undefined, unit: Measured['unit'], m: { method: string; limits: string[] }): Measured | null {
  return value == null ? null : { value, unit, method: m.method, limits: m.limits };
}

function memoryFields(e: LineEntry | FuncEntry, mode: Profile['memory_mode']) {
  if (mode === 'precise') return { allocated_mb: e.alloc_mb ?? 0, held_at_peak_mb: e.peak_mb ?? 0,
    spike_mb: e.transient_peak_mb ?? 0, native_estimate_mb: r3(nativeMb(e)) };
  if (mode === 'fast') return { rss_growth_mb: e.rss_growth_mb ?? 0 };
  return {};
}
const memoryOf = (e: LineEntry | FuncEntry, mode: Profile['memory_mode']) => mode === 'precise'
  ? Math.max(e.alloc_mb ?? 0, e.peak_mb ?? 0, e.transient_peak_mb ?? 0, nativeMb(e)) : mode === 'fast' ? e.rss_growth_mb ?? 0 : 0;

/** The union of the top `n` by time and the top `n` by memory, ordered by time. */
function topBoth<T>(rows: T[], time: (r: T) => number, mem: (r: T) => number, n: number): T[] {
  const byTime = [...rows].sort((a, b) => time(b) - time(a)).slice(0, n);
  const byMem = [...rows].filter(r => mem(r) > 0).sort((a, b) => mem(b) - mem(a)).slice(0, n);
  return [...new Set([...byTime, ...byMem])].sort((a, b) => time(b) - time(a));
}

function memoryStacks(p: Profile, rel: (f: string) => string) {
  const ms = p.memory_stacks;
  if (!ms) return null;
  const table = (t: NonNullable<typeof ms.peak>) => {
    const sites = new Map<string, { function: string; file: string; line: number; mb: number }>();
    for (const s of t.stacks) {
      let inner = -1;
      for (const id of s.frames) if (ms.frames[id].user) inner = id;
      const f = inner >= 0 ? ms.frames[inner] : null;
      const key = f ? `${f.file}\0${f.name}\0${f.first_line}` : '';
      const row = sites.get(key) ?? (f ? { function: f.name || `line ${f.line}`, file: rel(f.file), line: f.first_line, mb: 0 }
        : { function: 'outside your code', file: '', line: 0, mb: 0 });
      row.mb += s.bytes / 1e6;
      sites.set(key, row);
    }
    const top = [...sites.values()].sort((a, b) => b.mb - a.mb).slice(0, 15).map(x => ({ ...x, mb: r3(x.mb) }));
    const stacks = [...t.stacks].sort((a, b) => b.bytes - a.bytes).slice(0, 10).map(s => ({
      mb: r3(s.bytes / 1e6), truncated: s.truncated,
      frames: s.frames.map(id => { const f = ms.frames[id]; return `${f.user ? rel(f.file) : f.file}:${f.line} ${f.name || '?'}`; }) }));
    return { t_s: t.t, total_mb: r3(t.total_bytes / 1e6), other_mb: r3(t.other_bytes / 1e6),
      truncated: t.stacks.some(s => s.truncated), by_function: top, top_stacks: stacks };
  };
  return {
    method: `tracemalloc tracebacks (at most ${ms.depth} frames) at two snapshots: the one where your code held the most, and the last (at exit). Sites are the innermost of your functions on each stack; library allocations count in the function that called them.`,
    limits: [`stacks are cut at ${ms.depth} frames; deeper callers are not visible`,
      'only two moments are covered; memory that peaked between snapshots is not in these tables'],
    peak: ms.peak ? table(ms.peak) : null, exit: table(ms.exit),
  };
}

function nextSteps(p: Profile, s: { stale: string[]; growing: { file: string; line: number }[]; hotStatic: { file: string; line: number; code: string }[];
  comparison: SummaryInputs['comparison']; fewSamples: boolean }): string[] {
  const out: string[] = [];
  if (s.stale.length) out.push(`Re-run profiling: ${s.stale.join(', ')} changed since this profile, so its line numbers may not match the current source.`);
  if (p.memory_mode === 'off') out.push('Re-run in fast or precise mode to measure memory; this run measured time only.');
  if (p.memory_mode === 'fast') out.push('To see which allocations are still held, by whom, and whether memory keeps growing, re-run in precise mode (fast mode measures process memory, charged where it is first written).');
  for (const g of s.growing.slice(0, 3)) out.push(`Verify suspected growing retention at ${g.file}:${g.line} with a fixed workload after warm-up, then a longer one: a leak keeps growing instead of reaching a plateau.`);
  for (const h of s.hotStatic.slice(0, 3)) out.push(`Static warning ${h.code} at ${h.file}:${h.line} is on a measured hot line; fix it first and re-profile.`);
  // Truncation alone is not actionable: at the default depth almost every stack is cut. Memory that never reached
  // your code is.
  if ((p.unattributed_peak_mb ?? 0) >= 1) out.push(`${p.unattributed_peak_mb} MB held at the peak never reached your code within ${p.frames ?? 2} frames; raise pythonMemoryGuardian.profile.frames to attribute it (slower).`);
  if (s.fewSamples) out.push('Some time values come from fewer than 10 samples; repeat the run or lengthen the workload before relying on them.');
  if (!s.comparison) out.push('Before changing code, save this run as a baseline (Save Profile as Baseline); after the change, profile with the same workload and mode and compare.');
  else if (s.comparison.result.warnings.length) out.push('The compared runs differ in environment or workload (see comparison.warnings); confirm under the same conditions before trusting the changes.');
  return out;
}

export function buildSummary(inp: SummaryInputs) {
  const p = inp.profile, platform = inp.platform ?? process.platform, rel = (f: string) => relPath(f, inp.root, platform);
  const m = methods(p), mode = p.memory_mode;
  const attributed = Object.values(p.files).reduce((t, ls) => t + Object.values(ls).reduce((u, e) => u + e.time_s, 0), 0) || 1;

  const fnRows = Object.entries(p.functions ?? {}).flatMap(([file, fs]) => Object.entries(fs).map(([first, f]) => ({ file, first: Number(first), f })));
  const samplesByFn = new Map<string, number>();
  for (const [file, ls] of Object.entries(p.files)) for (const e of Object.values(ls)) {
    if (e.func_line != null) samplesByFn.set(`${file}\0${e.func_line}`, (samplesByFn.get(`${file}\0${e.func_line}`) ?? 0) + e.samples);
  }
  const functions = topBoth(fnRows, r => r.f.time_s, r => memoryOf(r.f, mode), 20).map(({ file, first, f }) => ({
    name: f.name, file: rel(file), line: first, end_line: f.end_line ?? null, samples: samplesByFn.get(`${file}\0${first}`) ?? 0,
    time_s: f.time_s, share: r3(f.time_s / attributed), python_s: f.python_s, native_s: f.native_s, system_s: f.system_s,
    ...memoryFields(f, mode) }));

  const lineRows = Object.entries(p.files).flatMap(([file, ls]) => Object.entries(ls).map(([ln, e]) => ({ file, line: Number(ln), e })));
  const lines = topBoth(lineRows, r => r.e.time_s, r => memoryOf(r.e, mode), 20).map(({ file, line, e }) => ({
    file: rel(file), line, scope: e.scope ?? null, samples: e.samples, time_s: e.time_s, share: e.share,
    ...memoryFields(e, mode), ...(e.leak_runs ? { suspected_leak: true, held_at_exit_mb: e.end_mb ?? 0 } : {}),
    ...(e.line_events != null ? { line_events: e.line_events } : {}) }));

  const retention = diagnose(p).slice(0, 20).map(d => ({ file: rel(d.file), line: d.line, scope: d.scope, status: d.status,
    held_at_exit_mb: r3(d.endMb), largest_snapshot_mb: r3(d.peakMb), evidence: d.evidence, holders: d.holders,
    recommendations: d.recommendations }));

  const files = Object.keys(p.files).map(file => ({ file: rel(file), sha1: p.file_hashes?.[file] ?? null,
    freshness: inp.freshness[file] ?? 'unverified' }));

  const analyzed = Object.keys(inp.staticDiagnostics).filter(f => f in p.files);
  const findings = analyzed.flatMap(file => inp.staticDiagnostics[file].map(d => {
    const e = p.files[file]?.[String(d.line)];
    return { file: rel(file), ...d, measured: inp.freshness[file] === 'fresh' ? heat(e, mode, inp.thresholds) : 'source changed' };
  }));

  const totals = {
    run_duration_s: measured(p.wall_s, 's', { method: 'Wall-clock time from the start of your script to its end, including profiler overhead.', limits: [] }),
    process_cpu_s: measured(p.cpu_s, 's', { method: 'Process CPU time (all threads) over the run.', limits: [] }),
    peak_traced_mb: mode === 'precise' ? measured(p.peak_traced_mb, 'MB', { method: 'Highest traced Python memory (tracemalloc peak, read at every sample).', limits: m.traced.limits.slice(0, 1) }) : null,
    held_at_exit_mb: mode === 'precise' && p.memory_stacks ? measured(r3(p.memory_stacks.exit.total_bytes / 1e6), 'MB',
      { method: 'Traced Python memory still allocated at the exit snapshot, excluding the profiler\'s own.', limits: m.traced.limits.slice(0, 1) }) : null,
    native_estimate_mb: mode === 'precise' ? measured(p.native_untraced_mb, 'MB', { method: 'Estimate: peak RSS minus RSS at start minus peak traced memory.', limits: m.native.limits }) : null,
    rss_peak_mb: mode !== 'off' && p.rss_kind ? measured(p.rss_peak_mb, 'MB', { method: 'Highest process RSS read at any sample or at exit.', limits: m.rss.limits.slice(1) }) : null,
    unattributed_peak_mb: mode === 'precise' ? measured(p.unattributed_peak_mb, 'MB', { method: 'Memory held at the peak snapshot whose traceback never reached your code.', limits: m.traced.limits.slice(3) }) : null,
  };

  const comparison = inp.comparison && (() => {
    const c = inp.comparison.result;
    const strip = (d: { base: number | null; cur: number | null; delta: number; noise: number; verdict: string }) =>
      ({ baseline: d.base, current: d.cur, change: r3(d.delta), variation: r3(d.noise), verdict: d.verdict });
    return {
      baseline: inp.comparison.label, baseline_meta: c.baseline.meta, warnings: c.warnings, notes: c.notes,
      method: 'Functions matched by file and name (generator expressions, lambdas and comprehensions counted in their enclosing function). A change is better or worse only beyond run-to-run variation: time, the counting error of its samples (at least 10%); traced memory, 1 MB or 15%; RSS-based values, 10 MB or 20%. "context": changed because another part of the run changed (see notes).',
      run: c.run.map(r => ({ measure: r.label, unit: r.unit, ...strip(r.value) })),
      functions: c.functions.filter(f => f.significant).slice(0, 20).map(f => ({ name: f.name, file: rel(f.file), line: f.line, baseline_line: f.baseLine,
        time_s: strip(f.time), ...Object.fromEntries(Object.entries(f.values).map(([k, v]) => [COMPARE_FIELDS[k] ?? k, strip(v)])), leak: f.leak })),
    };
  })();

  const stale = files.filter(f => f.freshness === 'stale').map(f => f.file);
  return {
    format: SUMMARY_FORMAT,
    generated_at: inp.generatedAt, generator: inp.generator,
    profile: { path: rel(inp.profilePath), schema: p.schema },
    run: { script: rel(p.script), python: p.python, platform: p.run?.platform ?? null, argv: p.run?.argv ?? null,
      started_at: p.run?.started_at ?? null, memory_mode: mode, interval_s: p.interval_s ?? null, samples: p.samples ?? null,
      traceback_frames: mode === 'precise' ? p.frames ?? null : null, gil_split: p.gil_split, incomplete: runNotes(p) },
    methods: { time: m.time, memory: mode === 'precise' ? { traced: m.traced, process: m.rss, native_estimate: m.native } : mode === 'fast' ? { process: m.rss } : null },
    totals: Object.fromEntries(Object.entries(totals).filter(([, v]) => v != null)),
    functions, lines, memory_stacks: memoryStacks(p, rel),
    retention: mode === 'precise' ? { method: 'Each line\'s held memory across snapshots: growing = held memory rose in at least 3 trailing snapshots without a decrease and at least 1 MB was held at exit; retained = held at exit; released = freed during the run. Holders come from a bounded search of globals, your instances\' attributes and garbage-collector-tracked containers at exit.',
      limits: ['growth alone does not prove an unintended leak', 'the holder search is bounded and is not a complete ownership graph', 'needs at least four snapshots; very short runs report no growing lines'],
      findings: retention } : null,
    largest_objects: p.largest_objects ? { method: 'Objects still referenced at exit by module globals or attributes of your class instances, sized by sys.getsizeof (containers include their items one level deep).',
      limits: ['libraries that do not report their memory (Polars, for one) show only their Python wrapper', ...(p.largest_objects.complete ? [] : ['the scan hit its time limit; the list may be incomplete'])],
      objects: p.largest_objects.objects } : null,
    static_diagnostics: { method: 'Findings from the language server (static analysis) for profiled files, with severity adjusted by this profile; "measured" says whether the line was hot, cold or unknown in this run.',
      limits: ['only files open in the editor are analyzed', ...(analyzed.length < files.length ? [`not analyzed (not open): ${Object.keys(p.files).filter(f => !analyzed.includes(f)).map(rel).join(', ')}`] : [])],
      findings },
    source: { method: 'SHA-1 of each file\'s text (CRLF normalized to LF) when profiled, compared with the current text. Line numbers are only valid for "fresh" files.', files },
    comparison,
    next_steps: nextSteps(p, { stale, growing: retention.filter(r => r.status === 'growing'),
      hotStatic: findings.filter(f => f.measured === 'hot'), comparison: inp.comparison,
      fewSamples: functions.some(f => f.time_s > 0 && f.samples > 0 && f.samples < 10) }),
  };
}
export type Summary = ReturnType<typeof buildSummary>;
