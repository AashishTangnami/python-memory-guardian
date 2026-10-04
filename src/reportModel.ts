/** Native report data: profile overview, retention diagnosis and weighted Python call trees. */
import { LineEntry, Profile, StackFrame } from './profileModel';

export interface OverviewLine {
  file: string; line: number; timeS: number; share: number; memory: string;
}

export interface ReportOverview {
  cpuS: number | null; samples: number | null;
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
        else if ((e.alloc_mb ?? 0) >= 1) memory = `${e.alloc_mb!.toFixed(1)} MB allocated`;
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

export function diagnose(profile: Profile): Diagnosis[] {
  if (profile.memory_mode !== 'precise') return [];
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

export type CallMetric = 'elapsed' | 'python' | 'native' | 'system' | 'unsplit';
export interface CallNode { id: number; parent: number; frame: number; value: number; self: number; children: number[]; }
export interface CallTree { nodes: CallNode[]; frames: ReportFrame[]; omitted: number; }

/** Add each sampled stack once; parent time includes descendants, self time does not. */
export function callTree(profile: Profile, metric: CallMetric, thread = '', limit = 25000,
                         view: FrameView = 'all'): CallTree {
  const all = profile.stacks?.frames ?? [];
  const origins = new Map<number, FrameOrigin>();
  const originOf = (id: number) => {
    let o = origins.get(id);
    if (!o) { o = frameOrigin(all[id]); origins.set(id, o); }
    return o;
  };
  // A sample's display path. Steps that share a key under the same parent merge into one node;
  // a group step carries the frames it stands for.
  const path = (ids: number[]): { key: string; ids: number[] }[] => {
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
  const nodes: CallNode[] = [{ id: 0, parent: -1, frame: -1, value: 0, self: 0, children: [] }];
  const members: Set<number>[] = [new Set()];          // profile frame ids each node stands for
  const edges = new Map<string, number>();
  let omitted = 0;
  for (const sample of profile.stacks?.samples ?? []) {
    if (thread && sample.thread !== thread) continue;
    const weight = metric === 'elapsed' ? sample.python_s + sample.native_s + sample.system_s + sample.unsplit_s
      : sample[`${metric}_s`];
    if (weight <= 0 || !sample.frames.length) continue;
    const steps = path(sample.frames);
    // Refuse the complete sample if its missing suffix would exceed the limit.
    let probe = 0, missing = 0;
    for (const step of steps) {
      const child = edges.get(`${probe}:${step.key}`);
      if (missing || child == null) missing++;
      else probe = child;
    }
    if (nodes.length + missing > limit) { omitted += weight; continue; }
    let parent = 0;
    nodes[0].value += weight;
    for (const step of steps) {
      const key = `${parent}:${step.key}`;
      let child = edges.get(key);
      if (child == null) {
        child = nodes.length;
        edges.set(key, child);
        nodes.push({ id: child, parent, frame: step.ids[0], value: 0, self: 0, children: [] });
        members.push(new Set());
        nodes[parent].children.push(child);
      }
      for (const id of step.ids) members[child].add(id);
      nodes[child].value += weight;
      parent = child;
    }
    nodes[parent].self += weight;                     // includes 'mine' samples whose leaf was library code
  }
  for (const n of nodes) n.children.sort((a, b) => nodes[b].value - nodes[a].value);
  // Send only frames the tree references: a thread/metric filter or the node cap can leave most unused.
  // A group that turned out to hold a single function is shown as that function.
  const frames: ReportFrame[] = [], remap = new Map<number, number>();
  for (const n of nodes) {
    if (n.frame < 0) continue;
    const ids = members[n.id];
    if (ids.size > 1) {
      const origin = originOf(n.frame), names = [...new Set([...ids].map(id => all[id].name))];
      n.frame = frames.length;
      frames.push({ name: origin.label, file: '', line: 0, first_line: 0, user: false, origin,
        group: { count: ids.size, names: names.slice(0, 8) } });
      continue;
    }
    let id = remap.get(n.frame);
    if (id == null) { id = frames.length; remap.set(n.frame, id); frames.push({ ...all[n.frame], origin: originOf(n.frame) }); }
    n.frame = id;
  }
  return { nodes, frames, omitted };
}
