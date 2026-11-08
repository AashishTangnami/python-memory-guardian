/** Native report data: the run split into phases by the main thread's stack. */
import { Profile } from '../../profile/types';
import { functionKey } from './callTree';

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
