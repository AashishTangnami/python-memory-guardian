/**
 * Compare two runtime profiles (a saved baseline and the current one): per-function, per-line and
 * allocation-site deltas, with run-to-run variation thresholds and comparability warnings.
 * No `vscode` import, so it is unit-testable with plain Node.
 */
import { LineEntry, nativeMb, normPath, Profile } from './profileModel';

/** Written by "Save Profile as Baseline" next to the profile fields, so a baseline is still a valid profile. */
export interface BaselineMeta {
  name: string; saved_at: string; git_commit: string | null; git_dirty: boolean | null;
}

/** context: changed, but because another part of the run changed (see Comparison.notes), not this code. */
export type Verdict = 'worse' | 'better' | 'same' | 'new' | 'gone' | 'context';
export interface Delta { base: number | null; cur: number | null; delta: number; noise: number; verdict: Verdict; }
export type Unit = 's' | 'MB';

export interface RunRow { label: string; unit: Unit; value: Delta; }
export interface FunctionDelta {
  key: string; name: string; file: string; line: number | null; baseLine: number | null;
  time: Delta; values: Record<string, Delta>;
  leak: 'new' | 'gone' | 'both' | null;
  /** A measure got better or worse, a leak appeared or went, or a new/removed row has a value beyond noise. */
  significant: boolean;
}
export interface LineDelta {
  key: string; name: string; file: string; line: number | null; baseLine: number | null;
  time: Delta; values: Record<string, Delta>; leak: FunctionDelta['leak'];
  /** Measures that changed on this line while its function's total did not: attribution moved between its lines. */
  shifted: string[];
  significant: boolean;
}
export interface SiteDelta { key: string; name: string; file: string; peak: Delta | null; exit: Delta | null; }
export interface Comparison {
  baseline: { script: string; python: string; mode: Profile['memory_mode']; wall: number; meta: BaselineMeta | null };
  current: { script: string; python: string; mode: Profile['memory_mode']; wall: number };
  warnings: string[];
  /** Why some changes are marked 'context' rather than better or worse. */
  notes: string[];
  /** Memory columns both runs measured, in display order. */
  columns: { key: string; label: string }[];
  run: RunRow[]; functions: FunctionDelta[]; lines: LineDelta[]; sites: SiteDelta[];
  unmatchedLines: { baseline: number; current: number };
}

/**
 * Run-to-run variation, measured on three identical runs each of examples/profiling-workloads/generators.py in
 * fast and precise mode: a sampled time is a count of samples, each worth about its run's seconds per sample, so
 * its error follows the count (one line had 0.54 s from 6 samples and 0.61 s from 2: precise mode slows the
 * sampler, so samples there are far apart; a short function in fast mode moved 0.015-0.053 s); traced memory
 * per function moved at most 10% (held at the peak snapshot 47.3-52.0 MB, with snapshot timing); RSS-based values
 * moved up to 8.8 MB on small functions and 14% on large ones (pages are charged where first written).
 * A change within this is "same".
 */
export function timeNoise(a: number, na: number, b: number, nb: number): number {
  const v = (t: number, n: number) => t > 0 ? t * t / Math.max(1, n) : 0;   // (t / n)^2 per sample, times n
  return Math.max(2 * Math.sqrt(v(a, na) + v(b, nb)), 0.1 * Math.max(a, b));
}
export function memoryNoise(a: number, b: number, rss = false): number {
  return rss ? Math.max(10, 0.2 * Math.max(a, b)) : Math.max(1, 0.15 * Math.max(a, b));
}

function delta(base: number | null, cur: number | null, noise: number): Delta {
  const d = (cur ?? 0) - (base ?? 0);
  const verdict: Verdict = base == null ? 'new' : cur == null ? 'gone'
    : Math.abs(d) <= noise ? 'same' : d > 0 ? 'worse' : 'better';
  return { base, cur, delta: d, noise, verdict };
}

/**
 * The function a line belongs to, for comparison: generator expressions, lambdas and comprehensions are
 * merged into their enclosing function, because samples and allocations move between the two from run
 * to run (measured: 1.2 vs 25.4 MB on one, the reverse on the other, the same 27.1 MB in total).
 */
export function enclosingName(qualname: string): string {
  const parts = qualname.split('.');
  while (parts.length && parts[parts.length - 1].startsWith('<')) parts.pop();
  return parts.join('.') || '<module>';
}

/** Files are identified relative to the profiled script's folder, so runs from another checkout still match. */
function fileKey(file: string, script: string, platform: NodeJS.Platform): string {
  const f = normPath(file, platform), dir = normPath(script, platform).replace(/\/[^/]*$/, '/');
  return f.startsWith(dir) ? f.slice(dir.length) : f;
}

interface Agg {
  name: string; file: string; line: number | null; samples: number; time: number;
  alloc: number; held: number; exit: number; rss: number; prof: number; native: number; leak: boolean;
}

/** Each line's enclosing function name, and each function's first line (from any record that names it). */
function functionIndex(p: Profile) {
  const anchors = new Map<string, number>();          // file + name -> first line of the def
  const nameAt = (file: string, first: number) => p.functions?.[file]?.[String(first)]?.name;
  const note = (file: string, name: string, first: number) => {
    const k = `${file}\0${name}`;
    if (!anchors.has(k) || first < anchors.get(k)!) anchors.set(k, first);
  };
  for (const [file, fns] of Object.entries(p.functions ?? {})) {
    for (const [first, f] of Object.entries(fns)) note(file, f.name, Number(first));
  }
  for (const f of [...(p.stacks?.frames ?? []), ...(p.memory_stacks?.frames ?? [])]) {
    if (f.user && f.name) note(f.file, f.name, f.first_line);
  }
  const owner = (file: string, e: LineEntry): string => {
    const named = e.func_line != null ? nameAt(file, e.func_line) : undefined;
    if (named) return enclosingName(named);
    // Lines that were never sampled (memory only) carry the enclosing def from the source instead.
    if (e.scope?.endsWith('()')) return enclosingName(e.scope.slice(0, -2));
    return '<module>';
  };
  return { owner, anchor: (file: string, name: string) => anchors.get(`${file}\0${name}`) ?? null };
}

function aggregate(p: Profile, platform: NodeJS.Platform) {
  const { owner, anchor } = functionIndex(p);
  const fns = new Map<string, Agg>(), lines = new Map<string, Agg & { sig: string; fn: string }>();
  for (const [file, entries] of Object.entries(p.files)) {
    const fk = fileKey(file, p.script, platform);
    for (const [ln, e] of Object.entries(entries)) {
      const name = owner(file, e), key = `${fk}\0${name}`;
      let f = fns.get(key);
      if (!f) {
        f = { name, file, line: name === '<module>' ? null : anchor(file, name), samples: 0, time: 0,
          alloc: 0, held: 0, exit: 0, rss: 0, prof: 0, native: 0, leak: false };
        fns.set(key, f);
      }
      const one: Agg = { name, file, line: Number(ln), samples: e.samples, time: e.time_s, alloc: e.alloc_mb ?? 0,
        held: e.peak_mb ?? 0, exit: e.end_mb ?? 0, rss: e.rss_growth_mb, prof: e.profiler_mb ?? 0, native: nativeMb(e, p), leak: !!e.leak_runs };
      for (const k of ['samples', 'time', 'alloc', 'held', 'exit', 'rss', 'prof'] as const) f[k] += one[k];
      f.leak ||= one.leak;
      lines.set(`${fk}\0${ln}`, { ...one, sig: JSON.stringify([e.assigns ?? [], e.calls ?? []]), fn: key });
    }
  }
  // Native is estimated per function from its summed growth, as funcLabel does, not summed per line.
  for (const f of fns.values()) f.native = nativeMb({ rss_growth_mb: f.rss, alloc_mb: f.alloc, profiler_mb: f.prof }, p);
  return { fns, lines, anchor };
}

/** Memory held at a snapshot, by the innermost of your functions on each allocation stack. */
function sites(p: Profile, which: 'peak' | 'exit', platform: NodeJS.Platform): Map<string, { name: string; file: string; mb: number }> {
  const ms = p.memory_stacks, table = ms?.[which], out = new Map<string, { name: string; file: string; mb: number }>();
  if (!ms || !table) return out;
  for (const s of table.stacks) {
    let inner = -1;
    for (const id of s.frames) if (ms.frames[id].user) inner = id;
    const f = inner >= 0 ? ms.frames[inner] : null;
    const name = f ? enclosingName(f.name || `line ${f.line}`) : 'outside your code';
    const key = f ? `${fileKey(f.file, p.script, platform)}\0${name}` : '\0outside';
    const row = out.get(key) ?? { name, file: f?.file ?? '', mb: 0 };
    row.mb += s.bytes / 1e6;
    out.set(key, row);
  }
  return out;
}

const env = (p: Profile) => ({ argv: p.run?.argv ?? null, python: p.python, mode: p.memory_mode,
  frames: p.memory_mode === 'precise' ? p.frames ?? 2 : null, interval: p.interval_s ?? 0.01, gil: p.gil_split,
  platform: p.run?.platform ?? null });

function warnings(b: Profile, c: Profile, platform: NodeJS.Platform): string[] {
  const out: string[] = [], eb = env(b), ec = env(c);
  const sb = fileKey(b.script, b.script, platform), sc = fileKey(c.script, c.script, platform);
  if (sb !== sc) out.push(`Different scripts: ${sb} in the baseline, ${sc} now. Only functions with the same file and name are compared.`);
  if (eb.argv && ec.argv && JSON.stringify(eb.argv) !== JSON.stringify(ec.argv))
    out.push(`Different script arguments (${JSON.stringify(eb.argv)} vs ${JSON.stringify(ec.argv)}): the workload may differ.`);
  if (eb.python !== ec.python) out.push(`Different Python versions (${eb.python} vs ${ec.python}): object sizes, allocator behavior and timing can change on their own.`);
  if (eb.platform && ec.platform && eb.platform !== ec.platform) out.push(`Different platforms (${eb.platform} vs ${ec.platform}): process memory (RSS) is measured differently.`);
  if (eb.mode !== ec.mode) out.push(`Different memory modes (${eb.mode} vs ${ec.mode}): only measures both runs recorded are compared, and precise mode slows the program, so times are not comparable.`);
  else if (eb.frames !== ec.frames) out.push(`Different traceback depths (${eb.frames} vs ${ec.frames} frames): allocation sites can move to other functions.`);
  if (eb.interval !== ec.interval) out.push(`Different sampling intervals (${eb.interval} vs ${ec.interval} s): sampled times have different precision.`);
  if (eb.gil !== ec.gil) out.push('One run had a GIL and the other did not: the Python/native time split is not comparable.');
  const tb = b.trace_function?.name ?? null, tc = c.trace_function?.name ?? null;
  if (eb.mode === 'precise' && ec.mode === 'precise' && tb !== tc)
    out.push(`Memory was traced over different parts of the runs (${tb ? `only ${tb}()` : 'the whole run'} vs ${tc ? `only ${tc}()` : 'the whole run'}): code traced in one run and not the other shows memory in one only, and untraced code runs faster. Changes are marked as context.`);
  return out;
}

/** Columns for the memory measures both runs recorded. */
function columnsFor(b: Profile, c: Profile): { key: keyof Agg; label: string; rss?: boolean }[] {
  const both = (m: Profile['memory_mode']) => b.memory_mode === m && c.memory_mode === m;
  if (both('precise')) return [{ key: 'alloc', label: 'Allocated' }, { key: 'held', label: 'Held at peak' },
    { key: 'exit', label: 'Held at exit' }, ...(b.rss_kind && c.rss_kind ? [{ key: 'native' as const, label: 'Native ≈', rss: true }] : [])];
  // RSS growth is recorded in every mode, but in time-only runs nobody looks at it; compare it when both measured memory.
  if (b.memory_mode !== 'off' && c.memory_mode !== 'off' && b.rss_kind && c.rss_kind) return [{ key: 'rss', label: 'RSS growth', rss: true }];
  return [];
}

export function compareProfiles(base: Profile, cur: Profile, meta: BaselineMeta | null = null,
                                platform: NodeJS.Platform = process.platform): Comparison {
  const A = aggregate(base, platform), B = aggregate(cur, platform);
  const interval = Math.max(base.interval_s ?? 0.01, cur.interval_s ?? 0.01);
  const cols = columnsFor(base, cur);
  const sameMode = base.memory_mode === cur.memory_mode;
  const timed = (a: Agg | undefined, b: Agg | undefined) => {
    return delta(a ? a.time : null, b ? b.time : null, timeNoise(a?.time ?? 0, a?.samples ?? 0, b?.time ?? 0, b?.samples ?? 0));
  };
  const values = (a: Agg | undefined, b: Agg | undefined) => Object.fromEntries(cols.map(c => [c.key,
    delta(a ? a[c.key] as number : null, b ? b[c.key] as number : null,
      memoryNoise(a ? a[c.key] as number : 0, b ? b[c.key] as number : 0, c.rss))]));
  const leak = (a: Agg | undefined, b: Agg | undefined): FunctionDelta['leak'] =>
    a?.leak && b?.leak ? 'both' : b?.leak ? 'new' : a?.leak ? 'gone' : null;

  const functions: FunctionDelta[] = [];
  for (const key of new Set([...A.fns.keys(), ...B.fns.keys()])) {
    const a = A.fns.get(key), b = B.fns.get(key);
    functions.push({ key, name: (b ?? a)!.name, file: (b ?? a)!.file, line: b?.line ?? null, baseLine: a?.line ?? null,
      time: timed(a, b), values: values(a, b), leak: leak(a, b), significant: false });
  }

  // Lines: identical files match by line number. Otherwise a line matches by its function and either its
  // assignments and calls (when unique in that function in both runs) or its offset from the def line,
  // so inserting code above a function, or above a distinctive line inside it, keeps the match.
  // Module-level lines in a changed file have no stable identity and stay unmatched.
  const sameFiles = new Set<string>();
  for (const [file, h] of Object.entries(cur.file_hashes ?? {})) {
    const fk = fileKey(file, cur.script, platform);
    const other = Object.entries(base.file_hashes ?? {}).find(([f]) => fileKey(f, base.script, platform) === fk);
    if (other && other[1] === h) sameFiles.add(fk);
  }
  type LineAgg = Agg & { sig: string; fn: string };
  const NO_SIG = '[[],[]]';
  const uniqueSigs = (agg: ReturnType<typeof aggregate>) => {
    const count = new Map<string, number>();
    for (const l of agg.lines.values()) if (l.sig !== NO_SIG) count.set(`${l.fn}\0${l.sig}`, (count.get(`${l.fn}\0${l.sig}`) ?? 0) + 1);
    return new Set([...count].filter(([, n]) => n === 1).map(([k]) => k));
  };
  const ua = uniqueSigs(A), ub = uniqueSigs(B);
  const trusted = new Set([...ua].filter(k => ub.has(k)));      // unique in this function in both runs
  const lineKeys = (agg: ReturnType<typeof aggregate>) => {
    const out = new Map<string, LineAgg>();
    for (const [k, l] of agg.lines) {
      const fk = k.slice(0, k.lastIndexOf('\0'));
      const first = l.name === '<module>' ? null : agg.fns.get(l.fn)?.line ?? null;
      const id = sameFiles.has(fk) ? `${fk}\0@${l.line}`
        : trusted.has(`${l.fn}\0${l.sig}`) ? `${l.fn}\0sig${l.sig}`
        : first != null ? `${l.fn}\0+${l.line! - first}` : null;
      if (id) out.set(id, l);
    }
    return out;
  };
  const LA = lineKeys(A), LB = lineKeys(B);
  // Samples and allocations move between lines of one function from run to run (measured: 24 MB between two
  // adjacent lines of identical runs), so a line's change counts only if its function's total changed too.
  const byFn = new Map(functions.map(f => [f.key, f]));
  const lines: LineDelta[] = [];
  for (const key of new Set([...LA.keys(), ...LB.keys()])) {
    const a = LA.get(key), b = LB.get(key);
    if (!a || !b) continue;                                    // counted below, not listed
    const row: LineDelta = { key, name: b.name, file: b.file, line: b.line, baseLine: a.line, time: timed(a, b),
      values: values(a, b), leak: leak(a, b), shifted: [], significant: false };
    const fn = byFn.get(b.fn);
    for (const [k, d] of [['time', row.time], ...Object.entries(row.values)] as [string, Delta][]) {
      const total = k === 'time' ? fn?.time : fn?.values[k];
      if ((d.verdict === 'worse' || d.verdict === 'better') && total?.verdict === 'same') {
        d.verdict = 'same';
        row.shifted.push(k);
      }
    }
    lines.push(row);
  }
  const matchedA = new Set(lines.map(l => LA.get(l.key))), matchedB = new Set(lines.map(l => LB.get(l.key)));
  const unmatchedLines = { baseline: [...A.lines.values()].filter(l => !matchedA.has(l)).length,
    current: [...B.lines.values()].filter(l => !matchedB.has(l)).length };

  const siteRows: SiteDelta[] = [];
  if (base.memory_stacks && cur.memory_stacks) {
    const pa = sites(base, 'peak', platform), pb = sites(cur, 'peak', platform);
    const xa = sites(base, 'exit', platform), xb = sites(cur, 'exit', platform);
    for (const key of new Set([...pa.keys(), ...pb.keys(), ...xa.keys(), ...xb.keys()])) {
      const r = pb.get(key) ?? xb.get(key) ?? pa.get(key) ?? xa.get(key)!;
      // The tables keep every stack (up to 20,000), so a site missing from one held nothing at that snapshot.
      const d = (x?: { mb: number }, y?: { mb: number }) => x || y ? delta(x?.mb ?? 0, y?.mb ?? 0, memoryNoise(x?.mb ?? 0, y?.mb ?? 0)) : null;
      siteRows.push({ key, name: r.name, file: r.file,
        peak: base.memory_stacks.peak && cur.memory_stacks.peak ? d(pa.get(key), pb.get(key)) : null, exit: d(xa.get(key), xb.get(key)) });
    }
  }

  // Two measures depend on the rest of the run, not only on the code they are shown on:
  //  - held at peak is what each function held at the moment of the run's peak; when the peak moves to other
  //    code, functions there rise only because the moment changed (measured: 0 -> 27 MB on an unchanged function
  //    after the code that used to peak was fixed);
  //  - RSS growth is charged where memory is first written, and memory freed earlier stays resident for reuse,
  //    so when the run's peak RSS falls, later code can show growth it did not show before (measured: 0 -> 73 MB).
  const notes: string[] = [];
  const top = (rows: Map<string, { name: string; mb: number }>) => [...rows.values()].sort((x, y) => y.mb - x.mb)[0];
  const pa = base.memory_stacks?.peak ? top(sites(base, 'peak', platform)) : undefined;
  const pb = cur.memory_stacks?.peak ? top(sites(cur, 'peak', platform)) : undefined;
  const contextual = (keys: string[], when: (d: Delta) => boolean) => {
    for (const r of [...functions, ...lines]) for (const k of keys) {
      const d = r.values[k];
      if (d && when(d)) d.verdict = 'context';
    }
  };
  const moved = (d: Delta) => d.verdict === 'worse' || d.verdict === 'better';
  // Different tracing scopes (--trace-function): neither memory nor time is measured the same way in the two runs.
  const scopes = [base, cur].map(p => p.memory_mode === 'precise' ? p.trace_function?.name ?? null : undefined);
  const scopeDiffers = scopes[0] !== undefined && scopes[1] !== undefined && scopes[0] !== scopes[1];
  if (scopeDiffers) {
    for (const r of [...functions, ...lines]) for (const d of [r.time, ...Object.values(r.values)]) if (moved(d)) d.verdict = 'context';
    for (const r of siteRows) for (const d of [r.peak, r.exit]) if (d && moved(d)) d.verdict = 'context';
  }
  if (pa && pb && pa.name !== pb.name) {
    notes.push(`The memory peak moved from ${pa.name} to ${pb.name}. "Held at peak" is what each function held at that moment, so it changes wherever the peak moved; judge memory by the run's peak and by Allocated.`);
    contextual(['held'], moved);
    for (const r of siteRows) if (r.peak && moved(r.peak)) r.peak.verdict = 'context';
  }
  const rssPeak = base.rss_kind && cur.rss_kind && base.memory_mode !== 'off' && cur.memory_mode !== 'off'
    ? delta(base.rss_peak_mb, cur.rss_peak_mb, memoryNoise(base.rss_peak_mb, cur.rss_peak_mb, true)) : null;
  if (rssPeak && (rssPeak.verdict === 'better' || rssPeak.verdict === 'worse')) {
    const fell = rssPeak.verdict === 'better';
    notes.push(`Peak process memory ${fell ? 'fell' : 'rose'} from ${rssPeak.base!.toFixed(0)} to ${rssPeak.cur!.toFixed(0)} MB. Process memory is charged where it is first written and freed memory stays resident for reuse, so code that runs later can show ${fell ? 'more' : 'less'} RSS growth without changing; those RSS and native changes are marked as context.`);
    contextual(['rss', 'native'], d => d.verdict === (fell ? 'worse' : 'better'));
  }

  // A row appears or disappears whenever a sample happens to land on it; only a value beyond noise counts.
  const counts = (d: Delta) => d.verdict === 'worse' || d.verdict === 'better'
    || ((d.verdict === 'new' || d.verdict === 'gone') && Math.abs(d.delta) > d.noise);
  for (const r of [...functions, ...lines]) {
    r.significant = r.leak === 'new' || r.leak === 'gone' || [r.time, ...Object.values(r.values)].some(counts);
  }

  const run: RunRow[] = [];
  // Run duration is measured, not sampled.
  run.push({ label: 'Run duration', unit: 's', value: delta(base.wall_s, cur.wall_s,
    Math.max(2 * interval, 0.1 * Math.max(base.wall_s, cur.wall_s))) });
  const contextIfScoped = () => { if (scopeDiffers) for (const r of run) if (moved(r.value)) r.value.verdict = 'context'; };
  const mem = (label: string, a: number | null | undefined, b: number | null | undefined, rss = false) => {
    if (a != null && b != null) run.push({ label, unit: 'MB', value: delta(a, b, memoryNoise(a, b, rss)) });
  };
  if (sameMode && base.memory_mode === 'precise') {
    mem('Peak traced allocations', base.peak_traced_mb, cur.peak_traced_mb);
    mem('Traced memory held at exit', base.memory_stacks ? base.memory_stacks.exit.total_bytes / 1e6 : null,
      cur.memory_stacks ? cur.memory_stacks.exit.total_bytes / 1e6 : null);
    mem('Native memory (estimate)', base.native_untraced_mb, cur.native_untraced_mb, true);
  }
  if (base.memory_mode !== 'off' && cur.memory_mode !== 'off' && base.rss_kind && cur.rss_kind)
    mem('Peak process RSS', base.rss_peak_mb, cur.rss_peak_mb, true);

  contextIfScoped();
  const weight = (r: { time: Delta; values: Record<string, Delta> }) => Math.max(
    Math.abs(r.time.delta) / Math.max(r.time.noise, 1e-9), ...Object.values(r.values).map(v => Math.abs(v.delta) / Math.max(v.noise, 1e-9)));
  functions.sort((x, y) => weight(y) - weight(x) || x.name.localeCompare(y.name));
  lines.sort((x, y) => weight(y) - weight(x));
  siteRows.sort((x, y) => Math.max(Math.abs(y.peak?.delta ?? 0), Math.abs(y.exit?.delta ?? 0))
    - Math.max(Math.abs(x.peak?.delta ?? 0), Math.abs(x.exit?.delta ?? 0)));

  return {
    baseline: { script: base.script, python: base.python, mode: base.memory_mode, wall: base.wall_s, meta },
    current: { script: cur.script, python: cur.python, mode: cur.memory_mode, wall: cur.wall_s },
    warnings: warnings(base, cur, platform), notes,
    columns: cols.map(c => ({ key: c.key, label: c.label })),
    run, functions, lines, sites: siteRows, unmatchedLines,
  };
}

/** Validate the `baseline` block of a saved baseline file; anything malformed is treated as absent. */
export function baselineMeta(json: unknown): BaselineMeta | null {
  const b = (json as { baseline?: Record<string, unknown> } | null)?.baseline;
  if (!b || typeof b !== 'object' || typeof b.name !== 'string' || typeof b.saved_at !== 'string') return null;
  return { name: b.name, saved_at: b.saved_at,
    git_commit: typeof b.git_commit === 'string' ? b.git_commit : null,
    git_dirty: typeof b.git_dirty === 'boolean' ? b.git_dirty : null };
}

/** Baseline names become file names: letters, digits, dot, dash and underscore only. */
export function baselineFileName(name: string): string | null {
  const n = name.trim();
  return /^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$/.test(n) && !n.endsWith('.') ? `${n}.json` : null;
}
