/** Allocator requests are distinct from objects, live bytes and RSS. */
export interface AllocationBackend {
  backend: 'sampled' | 'memray'; available: boolean; reason?: string;
  files?: AllocationCapture[]; availability?: Record<string, string>;
  domain_totals_additive?: false; notes?: string[];
}
export interface AllocationCapture {
  process_id: number;
  artifact: { path: string; sha256: string; bytes: number; format: string };
  sites: AllocationSite[];
  [key: string]: unknown;
}
export interface AllocationSite {
  process_id: number; thread_id: number; domain: string; scope: string;
  requested_bytes: number; allocation_count: number;
  outstanding_at_capture_end_bytes: number; outstanding_at_capture_end_blocks: number;
  allocation_timestamp_ns: null;
  source: { file: string; line: number | null; function: string; role: string } | null;
  python_stack: { file: string; line: number | null; function: string }[] | null;
  native_stack: { file: string; line: number | null; function: string }[] | null;
  [key: string]: unknown;
}
export function validAllocationBackend(value: unknown): value is AllocationBackend {
  const obj = (v: any): boolean => v !== null && typeof v === 'object' && !Array.isArray(v);
  const count = (v: unknown): boolean => typeof v === 'number' && Number.isSafeInteger(v) && v >= 0;
  const frame = (v: any): boolean => obj(v) && typeof v.file === 'string' && typeof v.function === 'string'
    && (v.line === null || count(v.line));
  const a: any = value;
  if (!obj(a) || !['sampled', 'memray'].includes(a.backend) || typeof a.available !== 'boolean') return false;
  if (!a.available) return typeof a.reason === 'string' || a.backend === 'memray' && Array.isArray(a.errors) && a.errors.length > 0;
  if (a.backend !== 'memray' || a.domain_totals_additive !== false || !obj(a.availability)
    || !Object.values(a.availability).every(v => typeof v === 'string') || !Array.isArray(a.files) || !a.files.length || a.files.length > 64) return false;
  return a.files.every((f: any) => obj(f) && count(f.process_id) && obj(f.artifact)
    && typeof f.artifact.path === 'string' && /^[a-f0-9]{64}$/.test(f.artifact.sha256)
    && count(f.artifact.bytes) && f.artifact.format === 'memray_all_allocations'
    && Array.isArray(f.sites) && f.sites.length <= 2048 && f.sites.every((s: any) => obj(s)
      && s.process_id === f.process_id && count(s.thread_id)
      && ['python_allocator', 'system_allocator', 'interpreter_arena'].includes(s.domain)
      && ['target', 'profiler', 'external'].includes(s.scope)
      && ['requested_bytes', 'allocation_count', 'outstanding_at_capture_end_bytes', 'outstanding_at_capture_end_blocks'].every(k => count(s[k]))
      && s.allocation_timestamp_ns === null && (s.source === null || frame(s.source)
        && ['allocation_frame', 'traced_user_callsite'].includes(s.source.role))
      && ['python_stack', 'native_stack'].every(k => s[k] === null || Array.isArray(s[k]) && s[k].length <= 64 && s[k].every(frame))));
}
