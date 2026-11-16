/** Native report data: the retention diagnosis. */
import { hasExitSnapshot, isGrowingRetention, retentionGrowing } from '../../profile/metrics';
import { LineEntry, Profile } from '../../profile/types';

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
      const status = isGrowingRetention(e, profile) ? 'growing' : endMb >= 1 ? 'retained' : 'released';
      const endpoint = hasExitSnapshot(profile) ? 'exit' : 'the last observed snapshot';
      const evidence = [`${endMb.toFixed(1)} MB held at ${endpoint}; ${peakMb.toFixed(1)} MB largest recorded snapshot for this line.`];
      if (e.retention) {
        const r = e.retention;
        evidence.push(`${r.snapshots} snapshots over ${r.observed_s.toFixed(2)} s: ${r.rises} increases, ${r.releases} decreases.`);
        evidence.push(`${r.growth_mb >= 0 ? '+' : ''}${r.growth_mb.toFixed(1)} MB net change between the first and last snapshot.`);
      }
      if (status === 'growing') evidence.push(`${e.leak_runs} meaningful increases in the recent observation period; growth is a measured pattern, not proof of a leak or an OOM.`);
      if (!hasExitSnapshot(profile)) evidence.push('Whole-program exit retention is unavailable: the final snapshot covers only the observed tracing interval.');
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
      const status: Diagnosis['status'] = retentionGrowing(inside) ? 'growing' : endMb >= 1 ? 'retained' : 'released';
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
