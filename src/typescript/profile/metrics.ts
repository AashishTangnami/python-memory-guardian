/** How a line's measurements read: memory, heat, retention growth and the time split. No `vscode` import. */
import { Heat, LineEntry, NativeRun, Profile, Thresholds } from './types';

/** vscode.DiagnosticSeverity values, duplicated to stay vscode-free. */
export const Sev = { Error: 0, Warning: 1, Information: 2, Hint: 3 } as const;

/** Legacy helper kept for callers. No profile contains a native allocation trace for a Python line. */
export function nativeMb(_e: { rss_growth_mb?: number; alloc_mb?: number; profiler_mb?: number } | undefined, _run?: NativeRun): number {
  return 0;
}

/** Legacy schema-2/3 complete whole-run profiles used an exit snapshot but had no explicit flag. */
export function hasExitSnapshot(p: Profile): boolean {
  return p.memory_mode === 'precise' && p.memory_tracing_lost_s == null && !p.trace_function
    && (p.memory_snapshot_at_exit ?? p.memory_attribution == null);
}

/** Positive process-level RSS residual, never an allocation measurement or source attribution. */
export function processResidualMb(p: Profile): number | null {
  return p.memory_mode === 'precise' && !p.trace_function && p.memory_tracing_lost_s == null
    && (p.native_untraced_mb ?? 0) > 0 ? p.native_untraced_mb! : null;
}

/** Keep bounded warm-up followed by a plateau separate from recent sustained growth. */
export function retentionGrowing(points: [number, number][]): boolean {
  if (points.length < 4) return false;
  const recent = points.slice(-5), end = recent.at(-1)![1];
  if (end < 1.048576) return false;
  const changes = recent.slice(1).map((p, i) => ({ delta: p[1] - recent[i][1], tolerance: Math.max(0.001024, recent[i][1] * 0.001) }));
  return changes.filter(c => c.delta > c.tolerance).length >= 3
    && !changes.some(c => c.delta < -c.tolerance)
    && changes.slice(-2).some(c => c.delta > c.tolerance)
    && end - recent[0][1] >= Math.max(0.065536, end * 0.01);
}

export function isGrowingRetention(e: LineEntry, p: Profile): boolean {
  return !!e.leak_runs && hasExitSnapshot(p)
    && (!e.retention?.points.length || retentionGrowing(e.retention.points));
}

export function memoryMb(e: LineEntry | undefined, mode: Profile["memory_mode"], run?: NativeRun): number {
  if (!e) return 0;
  if (mode === "precise") {
    return e.peak_mb ?? 0;
  }
  return 0;
}

export function heat(e: LineEntry | undefined, mode: Profile["memory_mode"], th: Thresholds,
                     insideSampledFn = false, run?: NativeRun): Heat {
  if (!e || (e.samples === 0 && memoryMb(e, mode, run) === 0))
    // A run that sampled no time (a free-threaded build) cannot show that a line did not run.
    return insideSampledFn || !!e?.line_events || run?.stack_sampling?.active === false ? "unknown" : "cold";
  const growing = !!e.leak_runs && run?.memory_snapshot_at_exit !== false && run?.memory_tracing_lost_s == null && !run?.trace_function
    && (!e.retention?.points.length || retentionGrowing(e.retention.points));
  if (e.share >= th.hotShare || memoryMb(e, mode, run) >= th.hotMb || growing) return "hot";
  return "unknown";
}

/** Hot raises severity one step; cold lowers non-errors to Hint; otherwise unchanged. */
export function adjustSeverity(sev: number, h: Heat): number {
  if (h === "hot") return Math.max(Sev.Error, sev - 1);
  if (h === "cold" && sev !== Sev.Error) return Sev.Hint;
  return sev;
}

export const pct = (x: number) => `${(100 * x).toFixed(x >= 0.1 ? 0 : 1)}%`;

export function timeSplit(e: { time_s: number; python_s: number; native_s: number; system_s: number }): string {
  if (e.time_s <= 0) return "";
  const parts: [string, number][] = [["py", e.python_s], ["native", e.native_s], ["sys", e.system_s]];
  return parts.filter(([, v]) => v / e.time_s >= 0.05)
    .map(([k, v]) => `${k} ${pct(v / e.time_s)}`).join(" · ");
}
