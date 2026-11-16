/** Native report data: weighted Python call trees, function rankings and neighbors. */
import { Profile, StackFrame, StackSample } from '../../profile/types';

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
/** Measures in bytes. mem_exit is a legacy selector for the last observed snapshot. */
export const isMemoryMetric = (metric: CallMetric) => metric === 'mem_peak' || metric === 'mem_exit' || metric === 'mem_alloc';
/** Whether a profile can show a measure: memory-stack tables, or allocation recorded on time stacks. */
export function hasMetric(profile: Profile, metric: CallMetric): boolean {
  if (metric === 'mem_peak' || metric === 'mem_exit') return !!profile.memory_stacks;
  if (metric === 'mem_alloc') return false; // Legacy alloc_bytes was sampled process growth, not an allocation trace.
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
  if (metric === 'mem_alloc') return 0;
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
  /** Always null: native allocation provenance is unavailable. */
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
  if (!f.user || profile.memory_mode !== 'precise') return null;
  const entry = profile.functions?.[f.file]?.[String(f.first_line)];
  if (!entry) return null;
  return entry.peak_mb;
}

function functionNative(_profile: Profile, _f: StackFrame): number | null {
  return null;
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
