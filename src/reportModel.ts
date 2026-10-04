/** Native report data: profile overview, retention diagnosis and weighted Python call trees. */
import { LineEntry, nativeMb, Profile, StackFrame, StackSample } from './profileModel';

export interface OverviewLine {
  file: string; line: number; timeS: number; share: number; memory: string;
}

export interface ReportOverview {
  cpuS: number | null; samples: number | null;
  /** precise mode: process memory the Python allocator never saw (C extensions), run-level estimate. */
  nativeUntracedMb: number | null;
  rssKind: 'current' | 'peak' | null;
  rssStartMb: number | null; rssEndMb: number | null; rssPeakMb: number | null;
  tracedPeakMb: number | null; timeline: [number, number, number][];
  topLines: OverviewLine[];
}

/** Keep webview payloads bounded; every value comes from the validated profile. */
export function overview(profile: Profile): ReportOverview {
  const topLines: OverviewLine[] = [];
  for (const [file, lines] of Object.entries(profile.files)) {
    for (const [line, e] of Object.entries(lines)) {
      if (e.time_s <= 0) continue;
      let memory = '';
      if (profile.memory_mode === 'precise') {
        if (e.leak_runs) memory = `${(e.end_mb ?? 0).toFixed(1)} MB held`;
        else if ((e.peak_mb ?? 0) >= 1) memory = `${e.peak_mb!.toFixed(1)} MB at peak`;
        else if ((e.alloc_mb ?? 0) >= nativeMb(e, profile) && (e.alloc_mb ?? 0) >= 1) memory = `${e.alloc_mb!.toFixed(1)} MB allocated`;
        else if (nativeMb(e, profile) >= 1) memory = `native ≈ +${nativeMb(e, profile).toFixed(1)} MB`;
      } else if (profile.memory_mode === 'fast' && e.rss_growth_mb >= 1) {
        memory = `RSS +${e.rss_growth_mb.toFixed(1)} MB`;
      }
      topLines.push({ file, line: Number(line), timeS: e.time_s, share: e.share, memory });
    }
  }
  topLines.sort((a, b) => b.timeS - a.timeS || a.file.localeCompare(b.file) || a.line - b.line);
  const points = profile.timeline ?? [];
  const stride = Math.max(1, Math.ceil(points.length / 299));
  return {
    cpuS: profile.cpu_s ?? null, samples: profile.samples ?? null, rssKind: profile.rss_kind ?? null,
    // Not when only one function was traced: untraced Python memory would read as native.
    nativeUntracedMb: profile.memory_mode === 'precise' && !profile.trace_function ? profile.native_untraced_mb ?? null : null,
    rssStartMb: profile.rss_start_mb ?? null, rssEndMb: profile.rss_end_mb ?? null,
    rssPeakMb: profile.rss_peak_mb ?? null, tracedPeakMb: profile.peak_traced_mb ?? null,
    timeline: points.filter((_, i) => i % stride === 0 || i === points.length - 1),
    topLines: topLines.slice(0, 10),
  };
}

export interface Diagnosis {
  file: string; line: number; scope: string; status: 'growing' | 'retained' | 'released';
  endMb: number; peakMb: number; evidence: string[]; recommendations: string[];
  points: [number, number][]; holders: string[];
}

/** A time range on the run, in elapsed seconds. */
export interface TimeWindow { from: number; to: number; }

/**
 * Memory diagnosis, for the whole run or one time window. A window uses only each line's retention
 * snapshots inside it: the peak there, what was held at its end, and rises and releases within it.
 * Holders are only collected at exit, so they are cited only when the window reaches the last snapshot.
 */
export function diagnose(profile: Profile, window?: TimeWindow): Diagnosis[] {
  if (profile.memory_mode !== 'precise') return [];
  if (window) return diagnoseWindow(profile, window);
  const out: Diagnosis[] = [];
  for (const [file, lines] of Object.entries(profile.files)) {
    for (const [line, e] of Object.entries(lines)) {
      const endMb = e.end_mb ?? 0;
      const peakMb = Math.max(e.peak_mb ?? 0, e.retention?.peak_mb ?? 0, ...(e.retention?.points.map(p => p[1]) ?? []));
      if (Math.max(endMb, peakMb) < 1) continue;
      const status = e.leak_runs ? 'growing' : endMb >= 1 ? 'retained' : 'released';
      const evidence = [`${endMb.toFixed(1)} MB held at the end; ${peakMb.toFixed(1)} MB largest recorded snapshot for this line.`];
      if (e.retention) {
        const r = e.retention;
        evidence.push(`${r.snapshots} snapshots over ${r.observed_s.toFixed(2)} s: ${r.rises} increases, ${r.releases} decreases.`);
        evidence.push(`${r.growth_mb >= 0 ? '+' : ''}${r.growth_mb.toFixed(1)} MB net change between the first and last snapshot.`);
      }
      if (e.leak_runs) evidence.push(`${e.leak_runs} increases in the trailing period without an observed decrease.`);
      const holders = (e.held_by ?? []).map(h => `${h.holder} (${h.type}, ${h.items} items; ${h.matching} sampled matches)`);
      if (holders.length) evidence.push('Matching allocations were found in the holders below. The scan is bounded and is not a complete ownership graph.');
      else if (status !== 'released') evidence.push('No named holder was found; ownership is not established.');
      out.push({ file, line: Number(line), scope: e.scope ?? 'Module', status, endMb, peakMb,
        evidence, holders, recommendations: recommendations(e, status), points: e.retention?.points ?? [] });
    }
  }
  const order = { growing: 0, retained: 1, released: 2 };
  return out.sort((a, b) => order[a.status] - order[b.status] || b.endMb - a.endMb || b.peakMb - a.peakMb);
}

function diagnoseWindow(profile: Profile, w: TimeWindow): Diagnosis[] {
  const out: Diagnosis[] = [];
  const s = (t: number) => `${t.toFixed(2)} s`;
  for (const [file, lines] of Object.entries(profile.files)) {
    for (const [line, e] of Object.entries(lines)) {
      const all = e.retention?.points ?? [];
      const inside = all.filter(([t]) => t >= w.from && t <= w.to);
      if (!inside.length) continue;
      const peakMb = Math.max(...inside.map(([, mb]) => mb));
      const endMb = inside[inside.length - 1][1];
      if (Math.max(peakMb, endMb) < 1) continue;
      let rises = 0, releases = 0, trailing = 0;
      for (let i = 1; i < inside.length; i++) {
        if (inside[i][1] > inside[i - 1][1]) { rises++; trailing++; }
        else if (inside[i][1] < inside[i - 1][1]) { releases++; trailing = 0; }
      }
      const status: Diagnosis['status'] = trailing >= 3 && endMb >= 1 ? 'growing' : endMb >= 1 ? 'retained' : 'released';
      const reachesEnd = inside[inside.length - 1][0] >= all[all.length - 1][0];
      const evidence = [
        `Between ${s(w.from)} and ${s(w.to)}: ${peakMb.toFixed(1)} MB at the highest snapshot, ${endMb.toFixed(1)} MB held at the last one (${s(inside[inside.length - 1][0])}).`,
        `${inside.length} snapshots in this window: ${rises} increases, ${releases} decreases.`];
      if (status === 'growing') evidence.push(`${trailing} increases at the end of the window without a decrease.`);
      const holders = reachesEnd ? (e.held_by ?? []).map(h => `${h.holder} (${h.type}, ${h.items} items; ${h.matching} sampled matches)`) : [];
      if (holders.length) evidence.push('Holders below were found at exit, which this window includes. The scan is bounded and is not a complete ownership graph.');
      else if (!reachesEnd && status !== 'released') evidence.push('Holders are only searched at exit, which is outside this window.');
      out.push({ file, line: Number(line), scope: e.scope ?? 'Module', status, endMb, peakMb, evidence, holders,
        recommendations: recommendations(e, status), points: inside });
    }
  }
  const order = { growing: 0, retained: 1, released: 2 };
  return out.sort((a, b) => order[a.status] - order[b.status] || b.peakMb - a.peakMb || b.endMb - a.endMb);
}

function recommendations(e: LineEntry, status: Diagnosis['status']): string[] {
  if (status === 'released') return ['These allocations were released. Investigate copy/buffer reuse only if the temporary peak is a problem.'];
  const out: string[] = [];
  const holders = e.held_by ?? [];
  if (holders.some(h => ['list', 'deque', 'set', 'tuple', 'frozenset'].includes(h.type))) {
    out.push('If this is rolling history or a work queue, set a retention limit and evict consumed items. For rolling lists, consider collections.deque(maxlen=limit). Stream records to storage if every record must be preserved.');
  }
  if (holders.some(h => h.type === 'dict')) {
    out.push('Check whether new keys accumulate. Remove entries when their lifecycle ends; for a cache, define a maximum size or TTL and test eviction.');
  }
  if (holders.some(h => h.holder.startsWith('global '))) {
    out.push('This reference lives at module scope. If the data belongs to one request or batch, move ownership to that scope or release it when that work completes.');
  }
  if (!out.length) out.push('Trace references from this allocation to its owner. Check caches, instance attributes, pending tasks, callbacks, and containers whose lifetime exceeds the data they hold.');
  out.push(status === 'growing'
    ? 'Verify with a fixed workload after warm-up, then repeat for longer. A suspected leak should keep growing instead of reaching a stable plateau; confirm the same holder before changing retention.'
    : 'Retention alone does not establish a leak. Repeat a steady workload and check whether held memory plateaus at the intended working-set size.');
  return out;
}

export type FrameKind = 'user' | 'library' | 'stdlib' | 'internal';
/** Where a stack frame's code comes from, in words a user can act on. */
export interface FrameOrigin { kind: FrameKind; label: string; detail: string; }
/** A box in the Stack Explorer: one frame, or (grouped view) a run of frames from the same origin. */
export interface ReportFrame extends StackFrame { origin: FrameOrigin; group?: { count: number; names: string[] }; }
/** all: every frame; grouped: merge runs of non-user frames with one origin; mine: user frames only. */
export type FrameView = 'all' | 'grouped' | 'mine';

// Frozen modules are compiled into the interpreter, so their "file" is a placeholder like
// <frozen importlib._bootstrap_external>. Name the job they do instead.
const FROZEN: Record<string, [string, string]> = {
  'importlib._bootstrap': ['Python import system', 'loading a module (import)'],
  'importlib._bootstrap_external': ['Python import system', 'finding and reading module files (import)'],
  'zipimport': ['Python import system', 'importing from a zip archive'],
  'runpy': ['Python script launcher', 'starting your script'],
};

/** Classify a frame from its path alone, so reports from any profiler version get the same labels. */
export function frameOrigin(f: StackFrame): FrameOrigin {
  const file = f.file.replace(/\\/g, '/');
  const base = file.slice(file.lastIndexOf('/') + 1);
  // A package's __init__.py says nothing alone; name its folder too.
  const shown = base === '__init__.py' ? file.split('/').slice(-2).join('/') : base;
  if (f.user) return { kind: 'user', label: `${shown}:${f.line}`, detail: 'your code' };
  const frozen = /^<frozen ([\w.]+)>$/.exec(file);
  if (frozen) {
    const [label, detail] = FROZEN[frozen[1]] ?? ['Python internals', `built into the interpreter (${frozen[1]})`];
    return { kind: 'internal', label, detail };
  }
  if (file === '<string>') return { kind: 'internal', label: 'code run with exec() or eval()', detail: 'compiled at runtime, so there is no file to open' };
  if (file.startsWith('<')) return { kind: 'internal', label: 'generated code', detail: `created at runtime (${file.slice(1, -1)})` };
  const pkg = /\/(?:site|dist)-packages\/([^/]+)/.exec(file);
  if (pkg) {
    const name = pkg[1].replace(/\.py$/, '');
    return { kind: 'library', label: `${name} (installed package)`, detail: '' };
  }
  const std = /\/(?:lib\/python\d+(?:\.\d+)?t?|Lib)\/(.+)\.py$/i.exec(file);
  if (std) {
    const mod = std[1].replace(/\/__init__$/, '').replace(/\//g, '.');
    const top = mod.split('.')[0];
    return { kind: 'stdlib', label: `${top} (standard library)`, detail: mod === top ? '' : `module ${mod}` };
  }
  return { kind: 'library', label: `${base} (outside your project)`, detail: '' };
}

export type CallMetric = 'elapsed' | 'python' | 'native' | 'system' | 'unsplit' | 'mem_peak' | 'mem_exit' | 'mem_alloc';
/** Measures in bytes. mem_peak/mem_exit come from snapshot tracebacks; mem_alloc from sampled time stacks. */
export const isMemoryMetric = (metric: CallMetric) => metric === 'mem_peak' || metric === 'mem_exit' || metric === 'mem_alloc';
/** Whether a profile can show a measure: memory-stack tables, or allocation recorded on time stacks. */
export function hasMetric(profile: Profile, metric: CallMetric): boolean {
  if (metric === 'mem_peak' || metric === 'mem_exit') return !!profile.memory_stacks;
  if (metric === 'mem_alloc') return (profile.stacks?.samples ?? []).some(s => s.alloc_bytes != null);
  return true;
}

/**
 * The stacks a measure weighs: time samples (optionally one thread), allocation on time samples, or a
 * memory-stack table (bytes, no thread). focus: a functionKey; only stacks passing through it are kept, so a
 * helper shared by several callers can be seen under one of them.
 */
function stackSource(profile: Profile, metric: CallMetric, thread: string, focus = ''): { frames: StackFrame[]; samples: { frames: number[]; weight: number }[] } {
  let out: { frames: StackFrame[]; samples: { frames: number[]; weight: number }[] };
  if (metric === 'mem_peak' || metric === 'mem_exit') {
    const ms = profile.memory_stacks, table = metric === 'mem_peak' ? ms?.peak : ms?.exit;
    out = { frames: ms?.frames ?? [], samples: (table?.stacks ?? []).map(s => ({ frames: s.frames, weight: s.bytes })) };
  } else {
    out = { frames: profile.stacks?.frames ?? [], samples: (profile.stacks?.samples ?? [])
      .filter(s => !thread || s.thread === thread).map(s => ({ frames: s.frames, weight: sampleWeight(s, metric) })) };
  }
  if (!focus) return out;
  const through = new Set(out.frames.flatMap((f, id) => functionKey(f) === focus ? [id] : []));
  return { frames: out.frames, samples: out.samples.filter(s => s.frames.some(id => through.has(id))) };
}

/** Frames without a recoverable function name (frozen or compiled code) are shown by line. */
const shownName = (f: StackFrame) => f.name || `line ${f.line}`;

function sampleWeight(sample: StackSample, metric: CallMetric): number {
  if (metric === 'elapsed') return sample.python_s + sample.native_s + sample.system_s + sample.unsplit_s;
  if (metric === 'mem_alloc') return sample.alloc_bytes ?? 0;
  return metric === 'mem_peak' || metric === 'mem_exit' ? 0 : sample[`${metric}_s`];
}
/** value = self + omitted + children's values. omitted: deeper calls cut off by the node limit, still counted here. */
export interface CallNode { id: number; parent: number; frame: number; value: number; self: number; omitted: number; children: number[]; }
export interface CallTree { nodes: CallNode[]; frames: ReportFrame[]; omitted: number; }

/** Add each sampled stack once; parent time includes descendants, self time does not. */
export function callTree(profile: Profile, metric: CallMetric, thread = '', limit = 25000,
                         view: FrameView = 'all', inverted = false, focus = ''): CallTree {
  const source = stackSource(profile, metric, thread, focus), all = source.frames;
  // Inverted (bottom-up): each function where time or memory is spent sits at the top, its callers below.
  // Frames are merged by function first, so a function reached from several lines is one box and the top
  // level equals each function's self value in topFunctions.
  const canon: number[] = [];
  if (inverted) {
    const first = new Map<string, number>();
    all.forEach((f, id) => { const k = functionKey(f); if (!first.has(k)) first.set(k, id); canon[id] = first.get(k)!; });
  }
  const origins = new Map<number, FrameOrigin>();
  const originOf = (id: number) => {
    let o = origins.get(id);
    if (!o) { o = frameOrigin(all[id]); origins.set(id, o); }
    return o;
  };
  // A sample's display path. Steps that share a key under the same parent merge into one node;
  // a group step carries the frames it stands for.
  const path = (ids: number[]): { key: string; ids: number[] }[] => {
    const steps = pathOf(inverted ? ids.map(id => canon[id]) : ids);
    return inverted ? steps.reverse() : steps;
  };
  const pathOf = (ids: number[]): { key: string; ids: number[] }[] => {
    if (view === 'mine') ids = ids.filter(id => all[id].user);   // callee time stays in the nearest user frame
    if (view !== 'grouped') return ids.map(id => ({ key: `f${id}`, ids: [id] }));
    const steps: { key: string; ids: number[] }[] = [];
    for (let i = 0; i < ids.length;) {
      const o = originOf(ids[i]);
      let j = i + 1;
      if (o.kind !== 'user') while (j < ids.length && originOf(ids[j]).kind === o.kind && originOf(ids[j]).label === o.label) j++;
      steps.push(o.kind === 'user' ? { key: `f${ids[i]}`, ids: [ids[i]] } : { key: `g${o.kind}|${o.label}`, ids: ids.slice(i, j) });
      i = j;
    }
    return steps;
  };
  const nodes: CallNode[] = [{ id: 0, parent: -1, frame: -1, value: 0, self: 0, omitted: 0, children: [] }];
  const members: Set<number>[] = [new Set()];          // profile frame ids each node stands for
  const edges = new Map<string, number>();
  let omitted = 0;
  for (const sample of source.samples) {
    const weight = sample.weight;
    if (weight <= 0 || !sample.frames.length) continue;
    const steps = path(sample.frames);
    // At the node limit, keep the part of the stack already in the tree and count the rest as omitted
    // on the deepest node shown, so upper levels never lose time.
    let parent = 0, cut = false;
    nodes[0].value += weight;
    for (const step of steps) {
      const key = `${parent}:${step.key}`;
      let child = edges.get(key);
      if (child == null) {
        if (nodes.length >= limit) { cut = true; break; }
        child = nodes.length;
        edges.set(key, child);
        nodes.push({ id: child, parent, frame: step.ids[0], value: 0, self: 0, omitted: 0, children: [] });
        members.push(new Set());
        nodes[parent].children.push(child);
      }
      for (const id of step.ids) members[child].add(id);
      nodes[child].value += weight;
      parent = child;
    }
    if (cut) { nodes[parent].omitted += weight; omitted += weight; }
    else nodes[parent].self += weight;                // includes 'mine' samples whose leaf was library code
  }
  for (const n of nodes) n.children.sort((a, b) => nodes[b].value - nodes[a].value);
  // Send only frames the tree references: a thread/metric filter or the node cap can leave most unused.
  // A group that turned out to hold a single function is shown as that function.
  const frames: ReportFrame[] = [], remap = new Map<number, number>();
  for (const n of nodes) {
    if (n.frame < 0) continue;
    const ids = members[n.id];
    if (ids.size > 1) {
      const origin = originOf(n.frame), names = [...new Set([...ids].map(id => shownName(all[id])))];
      n.frame = frames.length;
      frames.push({ name: origin.label, file: '', line: 0, first_line: 0, user: false, origin,
        group: { count: ids.size, names: names.slice(0, 8) } });
      continue;
    }
    let id = remap.get(n.frame);
    if (id == null) {
      id = frames.length; remap.set(n.frame, id);
      const f = all[n.frame];   // inverted boxes stand for the whole function, so they point at its first line
      frames.push({ ...f, name: shownName(f), origin: originOf(n.frame), ...(inverted ? { line: f.first_line } : {}) });
    }
    n.frame = id;
  }
  return { nodes, frames, omitted };
}

/** One function, summed across every call path it appears in. */
export interface FunctionRow {
  key: string; name: string; file: string; line: number; user: boolean; origin: FrameOrigin;
  /** Time with this function as the innermost frame. */
  self: number;
  /** Time with this function anywhere in the stack, counted once per stack so recursion is not doubled. */
  total: number;
  /** Distinct functions that call it. */
  callers: number;
  /** Function-level memory from the profile, for your own functions only; null when not measured. */
  memoryMb: number | null;
  /** precise mode: native estimate (process growth beyond traced growth), your functions only. */
  nativeMb: number | null;
}
export interface Neighbor { key: string; name: string; origin: FrameOrigin; value: number; }
export interface Neighbors { key: string; value: number; self: number; callers: Neighbor[]; callees: Neighbor[]; }

/** A function's identity across call sites: the same code object reached from different lines. */
export function functionKey(f: StackFrame): string {
  return JSON.stringify([f.file, f.first_line, f.name]);
}

/** Weighted samples as function keys, outermost first; the 'mine' view keeps only user frames. */
function weightedStacks(profile: Profile, metric: CallMetric, thread: string, view: FrameView, focus = '') {
  const { frames, samples } = stackSource(profile, metric, thread, focus);
  const keys = frames.map(functionKey), out: { keys: string[]; ids: number[]; weight: number }[] = [];
  for (const sample of samples) {
    const ids = view === 'mine' ? sample.frames.filter(id => frames[id].user) : sample.frames;
    if (sample.weight > 0 && ids.length) out.push({ keys: ids.map(id => keys[id]), ids, weight: sample.weight });
  }
  return { frames, out };
}

function functionMemory(profile: Profile, f: StackFrame): number | null {
  if (!f.user || profile.memory_mode === 'off') return null;
  const entry = profile.functions?.[f.file]?.[String(f.first_line)];
  if (!entry) return null;
  return profile.memory_mode === 'precise'
    ? Math.max(entry.peak_mb, entry.transient_peak_mb, entry.alloc_mb ?? 0)
    : entry.rss_growth_mb ?? 0;
}

function functionNative(profile: Profile, f: StackFrame): number | null {
  if (!f.user || profile.memory_mode !== 'precise' || !profile.rss_kind) return null;
  const entry = profile.functions?.[f.file]?.[String(f.first_line)];
  return entry ? nativeMb(entry, profile) : null;
}

/** Functions ranked by self time: the answer to "where is the time spent" that a chart splits across paths. */
export function topFunctions(profile: Profile, metric: CallMetric, thread = '', view: FrameView = 'all', limit = 200, focus = ''): FunctionRow[] {
  const { frames, out: stacks } = weightedStacks(profile, metric, thread, view, focus);
  const rows = new Map<string, FunctionRow>(), callers = new Map<string, Set<string>>();
  for (const { keys, ids, weight } of stacks) {
    const counted = new Set<string>();
    keys.forEach((key, i) => {
      let row = rows.get(key);
      if (!row) {
        const f = frames[ids[i]];
        row = { key, name: shownName(f), file: f.file, line: f.first_line, user: f.user, origin: frameOrigin(f),
          self: 0, total: 0, callers: 0, memoryMb: functionMemory(profile, f), nativeMb: functionNative(profile, f) };
        rows.set(key, row);
        callers.set(key, new Set());
      }
      if (!counted.has(key)) { counted.add(key); row.total += weight; }
      if (i > 0 && keys[i - 1] !== key) callers.get(key)!.add(keys[i - 1]);
    });
    rows.get(keys[keys.length - 1])!.self += weight;
  }
  for (const row of rows.values()) row.callers = callers.get(row.key)!.size;
  return [...rows.values()].sort((a, b) => b.self - a.self || b.total - a.total).slice(0, limit);
}

/** Who calls a function and what it calls, merged across all its call paths. */
export function neighbors(profile: Profile, metric: CallMetric, key: string, thread = '', view: FrameView = 'all', limit = 20, focus = ''): Neighbors {
  const { frames, out: stacks } = weightedStacks(profile, metric, thread, view, focus);
  const byKey = new Map(frames.map(f => [functionKey(f), f] as const));
  const callerTime = new Map<string, number>(), calleeTime = new Map<string, number>();
  let value = 0, self = 0;
  for (const { keys, weight } of stacks) {
    if (!keys.includes(key)) continue;
    value += weight;
    if (keys[keys.length - 1] === key) self += weight;
    const up = new Set<string>(), down = new Set<string>();
    keys.forEach((k, i) => {
      if (k !== key) return;
      if (i > 0 && keys[i - 1] !== key) up.add(keys[i - 1]);
      if (i + 1 < keys.length && keys[i + 1] !== key) down.add(keys[i + 1]);
    });
    for (const k of up) callerTime.set(k, (callerTime.get(k) ?? 0) + weight);
    for (const k of down) calleeTime.set(k, (calleeTime.get(k) ?? 0) + weight);
  }
  const list = (m: Map<string, number>) => [...m].sort((a, b) => b[1] - a[1]).slice(0, limit).map(([k, v]) => {
    const f = byKey.get(k)!;
    return { key: k, name: shownName(f), origin: frameOrigin(f), value: v };
  });
  return { key, value, self, callers: list(callerTime), callees: list(calleeTime) };
}

/** A stretch of the run spent in one function at a chosen depth of the main thread's stack. */
export interface Phase {
  /** functionKey of the function at this depth, and of the functions above it, outermost first. */
  key: string; path: string[]; name: string; file: string; line: number;
  from: number; to: number; seconds: number;
  /** Highest values at timeline points inside the phase; null when the run did not measure them. */
  peakTracedMb: number | null; peakRssMb: number | null;
  /** RSS above anything earlier phases reached: memory this phase needed that was not already resident. */
  newRssMb: number | null;
}
export interface Phases { depth: number; auto: boolean; maxDepth: number; phases: Phase[]; }

/**
 * Split the run into phases: contiguous timeline stretches where the main thread was in the same function at
 * `depth` user frames below its outermost one (0 = the script itself). Stretches are by time, so a function
 * called once per pipeline (verify after eager, then after streaming) stays two phases when something else ran
 * between. Points whose stack is shallower than the depth (or that had no user frame) belong to no phase.
 * Resolution is the timeline's: at most 300 points.
 */
export function phases(profile: Profile, depth?: number): Phases {
  const tl = profile.timeline ?? [], ts = profile.timeline_stacks ?? [], st = profile.stacks;
  if (!st || ts.length !== tl.length || tl.length < 2) return { depth: 0, auto: depth == null, maxDepth: 0, phases: [] };
  const paths = ts.map(i => i < 0 ? [] : st.samples[i].frames.filter(id => st.frames[id].user));
  const maxDepth = Math.max(0, ...paths.map(p => p.length - 1));
  const wall = Math.max(tl[tl.length - 1][0], profile.wall_s, 1e-9);
  const precise = profile.memory_mode === 'precise', rss = !!profile.rss_kind && profile.memory_mode !== 'off';
  const build = (d: number): Phase[] => {
    const out: Phase[] = [];
    let runningRss = profile.rss_start_mb ?? tl[0][2];   // RSS already resident before the first phase
    for (let i = 0; i < tl.length;) {
      const ids = paths[i].slice(0, d + 1);
      if (ids.length <= d) { runningRss = Math.max(runningRss, tl[i][2]); i++; continue; }
      const keys = ids.map(id => functionKey(st.frames[id])), key = keys.join('>');
      const keyAt = (k: number) => paths[k].length > d ? paths[k].slice(0, d + 1).map(id => functionKey(st.frames[id])).join('>') : null;
      // A stretch continues across up to two points with a shallower stack (a sample that landed in the caller
      // between two calls), so one phase is not split in two.
      let j = i;
      for (;;) {
        if (j + 1 < tl.length && keyAt(j + 1) === key) { j++; continue; }
        const resume = [2, 3].find(k => j + k < tl.length && keyAt(j + k) === key
          && Array.from({ length: k - 1 }, (_, g) => keyAt(j + 1 + g) === null).every(Boolean));
        if (resume) { j += resume; continue; }
        break;
      }
      const pts = tl.slice(i, j + 1), f = st.frames[ids[d]];
      const peakRss = Math.max(...pts.map(p => p[2]));
      const to = j + 1 < tl.length ? tl[j + 1][0] : tl[j][0];
      out.push({ key: keys[d], path: keys, name: f.name || `line ${f.line}`, file: f.file, line: f.first_line,
        from: tl[i][0], to, seconds: to - tl[i][0],
        peakTracedMb: precise ? Math.max(...pts.map(p => p[1])) : null,
        peakRssMb: rss ? peakRss : null, newRssMb: rss ? Math.max(0, peakRss - runningRss) : null });
      runningRss = Math.max(runningRss, peakRss);
      i = j + 1;
    }
    return out;
  };
  if (depth != null) {
    const d = Math.max(0, Math.min(maxDepth, Math.floor(depth)));
    return { depth: d, auto: false, maxDepth, phases: build(d) };
  }
  let best = 0;
  // The shallowest level that splits the run into 3 to 12 phases of at least 1%, covering at least 90% of it,
  // with none taking more than half; otherwise the deepest level still covering 90%. Measured on generators.py:
  // the ingest/process/verify level qualifies (largest phase 26-30%); the level above is one `measure` phase
  // (93%), and the levels below are helper calls covering 80% or less.
  let found = -1;
  for (let d = 0; d <= maxDepth; d++) {
    const long = build(d).filter(p => p.seconds >= 0.01 * wall), covered = long.reduce((t, p) => t + p.seconds, 0);
    if (covered < 0.9 * wall) continue;
    best = d;
    if (long.length >= 3 && long.length <= 12 && long.every(p => p.seconds <= 0.5 * wall)) { found = d; break; }
  }
  if (found >= 0) best = found;
  return { depth: best, auto: true, maxDepth, phases: build(best) };
}
