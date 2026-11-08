/** Memory-budget stops describe measured pressure, not a confirmed leak/OOM. */
export interface MemoryGuard {
  status: 'completed' | 'stopped'; budget_bytes: number; reserve_bytes: number;
  stop_reason: string | null; scope: 'process_group' | 'profiled_process';
  supervisor_pid: number; profiled_pid: number; child_exit_code: number;
  peak_root_rss_bytes: number | null; peak_group_rss_bytes: number | null;
  last_available_bytes: number; last_group_rss_bytes: number;
  poll_interval_s: number; coverage: 'partial'; hard_limit: 'RLIMIT_AS' | null;
  hard_limit_bytes?: number | null;
  supervisor_peak_rss_bytes?: number | null;
  initial_memory: { available_bytes: number; total_bytes: number; host_available_bytes: number;
    container_available_bytes: number | null; container_status: string };
  notes: string[];
  raw_artifacts?: { path: string; bytes: number; integrity: 'unverified' }[];
}
export function validMemoryGuard(value: unknown): value is MemoryGuard {
  const obj = (v: any) => v != null && typeof v === 'object' && !Array.isArray(v);
  const count = (v: any) => Number.isSafeInteger(v) && v >= 0;
  const g: any = value;
  if (!obj(g) || !['completed', 'stopped'].includes(g.status)
    || !['process_group', 'profiled_process'].includes(g.scope) || g.coverage !== 'partial'
    || !['budget_bytes', 'reserve_bytes', 'supervisor_pid', 'profiled_pid', 'last_available_bytes', 'last_group_rss_bytes'].every(k => count(g[k]))
    || g.budget_bytes < 64 * 2 ** 20 || g.supervisor_pid === g.profiled_pid
    || !Number.isSafeInteger(g.child_exit_code) || !(g.poll_interval_s > 0 && Number.isFinite(g.poll_interval_s))
    || ![g.peak_root_rss_bytes, g.peak_group_rss_bytes].every(v => v === null || count(v))
    || !(g.hard_limit === null || g.hard_limit === 'RLIMIT_AS')
    || !obj(g.initial_memory) || !['available_bytes', 'total_bytes', 'host_available_bytes'].every(k => count(g.initial_memory[k]))
    || !(g.initial_memory.container_available_bytes === null || count(g.initial_memory.container_available_bytes))
    || typeof g.initial_memory.container_status !== 'string' || !Array.isArray(g.notes) || g.notes.length > 20
    || !g.notes.every((v: unknown) => typeof v === 'string')) return false;
  if (g.budget_bytes > g.initial_memory.available_bytes / 4) return false;
  if (g.hard_limit_bytes != null && (!count(g.hard_limit_bytes) || g.hard_limit !== 'RLIMIT_AS')) return false;
  if (g.supervisor_peak_rss_bytes != null && !count(g.supervisor_peak_rss_bytes)) return false;
  if (g.raw_artifacts != null && (!Array.isArray(g.raw_artifacts) || g.raw_artifacts.length > 64
    || !g.raw_artifacts.every((v: any) => obj(v) && typeof v.path === 'string' && count(v.bytes) && v.integrity === 'unverified'))) return false;
  if (g.status === 'completed') return g.stop_reason === null;
  if (typeof g.stop_reason !== 'string' || !g.stop_reason.length) return false;
  if (g.stop_reason === 'rss_budget') return g.peak_group_rss_bytes !== null && g.peak_group_rss_bytes >= g.budget_bytes;
  if (g.stop_reason === 'system_reserve') return g.last_available_bytes <= g.reserve_bytes;
  return g.stop_reason === 'interrupted' || g.stop_reason.startsWith('observation_unavailable: ');
}
