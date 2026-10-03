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

export type CallMetric = 'elapsed' | 'python' | 'native' | 'system' | 'unsplit';
export interface CallNode { id: number; parent: number; frame: number; value: number; self: number; children: number[]; }
export interface CallTree { nodes: CallNode[]; frames: StackFrame[]; omitted: number; }

/** Add each sampled stack once; parent time includes descendants, self time does not. */
export function callTree(profile: Profile, metric: CallMetric, thread = '', limit = 25000): CallTree {
  const nodes: CallNode[] = [{ id: 0, parent: -1, frame: -1, value: 0, self: 0, children: [] }];
  const edges = new Map<string, number>();
  let omitted = 0;
  for (const sample of profile.stacks?.samples ?? []) {
    if (thread && sample.thread !== thread) continue;
    const weight = metric === 'elapsed' ? sample.python_s + sample.native_s + sample.system_s + sample.unsplit_s
      : sample[`${metric}_s`];
    if (weight <= 0 || !sample.frames.length) continue;
    // Refuse the complete sample if its missing suffix would exceed the limit.
    let probe = 0, missing = 0;
    for (const frame of sample.frames) {
      const child = edges.get(`${probe}:${frame}`);
      if (missing || child == null) missing++;
      else probe = child;
    }
    if (nodes.length + missing > limit) { omitted += weight; continue; }
    let parent = 0;
    nodes[0].value += weight;
    for (const frame of sample.frames) {
      const key = `${parent}:${frame}`;
      let child = edges.get(key);
      if (child == null) {
        child = nodes.length;
        edges.set(key, child);
        nodes.push({ id: child, parent, frame, value: 0, self: 0, children: [] });
        nodes[parent].children.push(child);
      }
      nodes[child].value += weight;
      parent = child;
    }
    nodes[parent].self += weight;
  }
  for (const n of nodes) n.children.sort((a, b) => nodes[b].value - nodes[a].value);
  return { nodes, frames: profile.stacks?.frames ?? [], omitted };
}
