/** Schema validation for profile JSON. No `vscode` import. */
import { validAllocationBackend } from './allocation';
import { validMemoryGuard } from './memoryGuard';
import { DiagnosticEvent, MemoryObservation, Profile, RuntimeFinding } from './types';

export function parseProfile(json: string): Profile | undefined {
  try {
    const p = JSON.parse(json);
    const record = (v: unknown): v is Record<string, any> => !!v && typeof v === 'object' && !Array.isArray(v);
    const number = (v: unknown) => typeof v === 'number' && Number.isFinite(v) && v >= 0;
    const numeric = (v: Record<string, any>, fields: string[]) => fields.every(k => number(v[k]));
    if (!record(p) || ![2, 3].includes(p.schema) || typeof p.script !== 'string'
      || typeof p.python !== 'string' || typeof p.gil_split !== 'boolean'
      || !number(p.wall_s) || !['fast', 'precise', 'off'].includes(p.memory_mode)
      || !record(p.files) || !record(p.functions ?? {}) || !record(p.file_hashes ?? {})) return undefined;
    if (![p.peak_traced_mb, p.rss_peak_mb, p.unattributed_peak_mb, p.native_untraced_mb].every(v => v == null || number(v))) return undefined;
    if (!['cpu_s', 'samples', 'interval_s', 'snapshots', 'rss_start_mb', 'rss_end_mb']
      .every(k => p[k] == null || number(p[k]))) return undefined;
    if (p.rss_kind != null && !['current', 'peak'].includes(p.rss_kind)) return undefined;
    if (!(p.memory_tracing_lost_s == null || number(p.memory_tracing_lost_s))) return undefined;
    if (p.sampler_error != null && typeof p.sampler_error !== 'string') return undefined;
    if (p.stack_sampling != null && (!record(p.stack_sampling) || typeof p.stack_sampling.active !== 'boolean'
      || (p.stack_sampling.reason != null && typeof p.stack_sampling.reason !== 'string'))) return undefined;
    if (p.timeline != null && (!Array.isArray(p.timeline) || p.timeline.length > 10000
      || !p.timeline.every((point: unknown) => Array.isArray(point) && point.length === 3
        && point.every(number))
      || p.timeline.some((point: [number, number, number], i: number) => i > 0 && point[0] < p.timeline[i - 1][0]))) return undefined;
    if (!Object.values(p.file_hashes ?? {}).every(v => typeof v === 'string')) return undefined;
    for (const entries of Object.values(p.files)) {
      if (!record(entries)) return undefined;
      for (const [line, e] of Object.entries(entries)) {
        if (!/^[1-9]\d*$/.test(line) || !record(e) || !numeric(e,
          ['time_s', 'share', 'python_s', 'native_s', 'system_s', 'samples', 'rss_growth_mb', 'rss_release_mb'])) return undefined;
        for (const k of ['alloc_mb', 'transient_peak_mb', 'peak_mb', 'end_mb', 'leak_runs', 'func_line', 'profiler_mb', 'cpu_unsplit_s']) {
          if (e[k] != null && !number(e[k])) return undefined;
        }
        if (e.line_events != null && (!Number.isSafeInteger(e.line_events) || e.line_events < 0)) return undefined;
        if (e.scope != null && typeof e.scope !== 'string') return undefined;
        for (const k of ['assigns', 'calls']) {
          if (e[k] != null && (!Array.isArray(e[k]) || !e[k].every((v: unknown) => typeof v === 'string'))) return undefined;
        }
        if (e.held_by != null && (!Array.isArray(e.held_by) || !e.held_by.every((h: unknown) =>
          record(h) && typeof h.holder === 'string' && typeof h.type === 'string' && numeric(h, ['items', 'matching'])))) return undefined;
        if (e.retention != null) {
          const r = e.retention;
          if (!record(r) || !numeric(r, ['snapshots', 'rises', 'releases', 'observed_s'])
            || (r.peak_mb != null && !number(r.peak_mb))
            || typeof r.growth_mb !== 'number' || !Number.isFinite(r.growth_mb)
            || !Array.isArray(r.points) || !r.points.every((v: unknown) =>
              Array.isArray(v) && v.length === 2 && v.every(number))) return undefined;
        }
      }
    }
    for (const entries of Object.values(p.functions ?? {})) {
      if (!record(entries) || !Object.values(entries).every(f => record(f) && typeof f.name === 'string'
        && numeric(f, ['time_s', 'python_s', 'native_s', 'system_s', 'peak_mb'])
        && ['alloc_mb', 'rss_growth_mb', 'profiler_mb', 'transient_peak_mb'].every(k => f[k] == null || number(f[k]))
        && (f.end_line == null || number(f.end_line)))) return undefined;
    }
    if (p.stacks != null) {
      const s = p.stacks;
      if (!record(s) || !number(s.dropped_s) || typeof s.depth_limited !== 'boolean'
        || !Array.isArray(s.frames) || !s.frames.every((f: unknown) => record(f)
          && typeof f.file === 'string' && typeof f.name === 'string' && typeof f.user === 'boolean'
          && Number.isInteger(f.line) && f.line >= 0 && Number.isInteger(f.first_line) && f.first_line >= 0)
        || !Array.isArray(s.samples) || !s.samples.every((v: unknown) => record(v)
          && typeof v.thread === 'string' && typeof v.thread_name === 'string'
          && numeric(v, ['python_s', 'native_s', 'system_s', 'unsplit_s', 'samples']) && (v.alloc_bytes == null || number(v.alloc_bytes))
          && Array.isArray(v.frames) && v.frames.length <= 128
          && v.frames.every((id: unknown) => Number.isInteger(id) && Number(id) >= 0 && Number(id) < s.frames.length))) return undefined;
    }
    if (p.memory_stacks != null) {
      const ms = p.memory_stacks;
      const table = (t: unknown) => record(t) && numeric(t, ['t', 'total_bytes', 'other_bytes']) && Array.isArray(t.stacks)
        && t.stacks.every((s: unknown) => record(s) && number(s.bytes) && typeof s.truncated === 'boolean'
          && Array.isArray(s.frames) && s.frames.length <= 256
          && s.frames.every((id: unknown) => Number.isInteger(id) && Number(id) >= 0 && Number(id) < ms.frames.length));
      if (!record(ms) || !Number.isInteger(ms.depth) || ms.depth < 1 || !Array.isArray(ms.frames)
        || !ms.frames.every((f: unknown) => record(f) && typeof f.file === 'string' && typeof f.name === 'string'
          && typeof f.user === 'boolean' && Number.isInteger(f.line) && f.line >= 0 && Number.isInteger(f.first_line) && f.first_line >= 0)
        || !table(ms.exit) || (ms.peak != null && !table(ms.peak))) return undefined;
    }
    if (p.largest_objects != null) {
      const lo = p.largest_objects;
      if (!record(lo) || typeof lo.complete !== 'boolean' || !Array.isArray(lo.objects) || lo.objects.length > 100
        || !lo.objects.every((o: unknown) => record(o) && typeof o.holder === 'string' && typeof o.type === 'string'
          && number(o.mb) && typeof o.estimated === 'boolean' && (o.items == null || (Number.isSafeInteger(o.items) && o.items >= 0)))) return undefined;
      if (!['max_depth', 'max_nodes', 'visited'].every(k => lo[k] == null || Number.isSafeInteger(lo[k]) && lo[k] >= 0)
        || !['scope', 'sizing'].every(k => lo[k] == null || typeof lo[k] === 'string')
        || (lo.truncated != null && typeof lo.truncated !== 'boolean')) return undefined;
    }
    if (p.monitoring != null && (!record(p.monitoring)
      || !['off', 'lines'].includes(p.monitoring.requested)
      || typeof p.monitoring.active !== 'boolean'
      || (p.monitoring.reason != null && typeof p.monitoring.reason !== 'string')
      || !Number.isSafeInteger(p.monitoring.dropped_line_events)
      || p.monitoring.dropped_line_events < 0)) return undefined;
    if (p.timeline_stacks != null && (!Array.isArray(p.timeline_stacks) || p.timeline_stacks.length !== (p.timeline ?? []).length
      || !p.timeline_stacks.every((i: unknown) => Number.isInteger(i) && Number(i) >= -1 && Number(i) < (p.stacks?.samples.length ?? 0)))) return undefined;
    if (!(p.tracemalloc_peak_mb == null || number(p.tracemalloc_peak_mb))) return undefined;
    if (p.trace_function != null && (!record(p.trace_function) || typeof p.trace_function.name !== 'string'
      || !Number.isSafeInteger(p.trace_function.calls) || p.trace_function.calls < 0 || !number(p.trace_function.traced_s))) return undefined;
    if (p.run != null && (!record(p.run) || !Array.isArray(p.run.argv) || !p.run.argv.every((a: unknown) => typeof a === 'string')
      || (p.run.started_at != null && typeof p.run.started_at !== 'string') || typeof p.run.platform !== 'string'
      || (p.run.command != null && (!Array.isArray(p.run.command) || !p.run.command.every((a: unknown) => typeof a === 'string')))
      || (p.run.cwd != null && typeof p.run.cwd !== 'string'))) return undefined;
    const count = (v: unknown) => Number.isSafeInteger(v) && Number(v) >= 0;
    const identifier = (v: unknown) => typeof v === 'string' || count(v);
    const source = (v: unknown) => record(v) && typeof v.file === 'string' && count(v.line) && typeof v.function === 'string';
    if (p.process_id != null && !count(p.process_id)) return undefined;
    if (p.memory_attribution != null && p.memory_attribution !== 'process-observation') return undefined;
    if (p.native_memory != null && (!record(p.native_memory) || p.native_memory.status !== 'unavailable'
      || typeof p.native_memory.reason !== 'string' || !(p.native_memory.rss_residual_mb === null || number(p.native_memory.rss_residual_mb)))) return undefined;
    if (p.memory_snapshot_at_exit != null && typeof p.memory_snapshot_at_exit !== 'boolean') return undefined;
    if (p.memory_snapshot_at_exit === true && (p.memory_tracing_lost_s != null || p.trace_function != null)) return undefined;
    if (p.memory_observations != null && (!Array.isArray(p.memory_observations) || p.memory_observations.length > 600
      || !p.memory_observations.every((v: unknown) => record(v) && number(v.t)
        && ['traced_bytes', 'traced_growth_bytes', 'transient_bytes', 'rss_bytes', 'bookkeeping_bytes'].every(k => v[k] === null || number(v[k]))
        && (v.rss_change_bytes === null || typeof v.rss_change_bytes === 'number' && Number.isFinite(v.rss_change_bytes)))
      || p.memory_observations.some((v: MemoryObservation, i: number) => i > 0 && v.t < p.memory_observations[i - 1].t))) return undefined;
    if (p.gc != null && (!record(p.gc) || typeof p.gc.supported !== 'boolean'
      || !numeric(p.gc, ['total_pause_s']) || !['dropped_events', 'total_collections', 'collected', 'uncollectable'].every(k => count(p.gc[k]))
      || !Array.isArray(p.gc.events) || p.gc.events.length > 10000
      || !p.gc.events.every((v: unknown) => record(v) && numeric(v, ['t', 'duration_s'])
        && ['generation', 'collected', 'uncollectable'].every(k => count(v[k])) && identifier(v.thread)
        && (v.process == null || count(v.process)) && (v.traced_bytes == null || number(v.traced_bytes))))) return undefined;
    if (p.diagnostics != null) {
      const d = p.diagnostics;
      if (!record(d) || d.schema !== 1 || d.instrumentation !== 'cooperative'
        || !(d.trace_path === null || typeof d.trace_path === 'string') || !count(d.dropped_events)
        || !record(d.scan_limits) || !count(d.scan_limits.max_nodes) || !count(d.scan_limits.max_depth)
        || !record(d.capabilities) || !Object.values(d.capabilities).every(v => record(v)
          && ['supported', 'partial', 'unavailable'].includes(v.status) && typeof v.reason === 'string')
        || !Array.isArray(d.events) || d.events.length > 100000
        || !d.events.every((v: unknown) => record(v) && identifier(v.id) && number(v.t) && count(v.pid) && identifier(v.thread)
          && (v.task == null || identifier(v.task)) && typeof v.actor_kind === 'string' && identifier(v.actor_id)
          && (v.source === null || source(v.source)) && Array.isArray(v.stack) && v.stack.length <= 256 && v.stack.every(source)
          && typeof v.category === 'string' && record(v.payload))
        || !Array.isArray(d.findings) || d.findings.length > 10000
        || !d.findings.every((v: unknown) => record(v) && typeof v.id === 'string' && typeof v.category === 'string'
          && ['error', 'warning', 'information', 'hint'].includes(v.severity) && ['high', 'medium', 'low'].includes(v.confidence)
          && ['measured', 'confirmed', 'potential', 'projected'].includes(v.status) && (v.source === null || source(v.source))
          && 'workload' in v && record(v.evidence) && record(v.reproduction)
          && (v.reproduction.command === null || typeof v.reproduction.command === 'string' || Array.isArray(v.reproduction.command) && v.reproduction.command.every((c: unknown) => typeof c === 'string'))
          && (v.reproduction.trace_path === null || typeof v.reproduction.trace_path === 'string') && typeof v.summary === 'string')) return undefined;
      if (!(d.trace_sha256 == null || typeof d.trace_sha256 === 'string' && /^[a-f0-9]{64}$/.test(d.trace_sha256))) return undefined;
      if (!['dropped_findings', 'dropped_labels'].every(k => d[k] == null || count(d[k]))) return undefined;
      const eventIds = new Set(d.events.map((e: DiagnosticEvent) => String(e.id)));
      if (eventIds.size !== d.events.length || !d.findings.every((f: RuntimeFinding) =>
        f.evidence.event_ids == null || Array.isArray(f.evidence.event_ids)
          && f.evidence.event_ids.every(id => identifier(id) && eventIds.has(String(id))))) return undefined;
      // Confirmed concurrency means an observed logical conflict, never merely
      // a shared-access candidate or a claim of native race instrumentation.
      const eventsById = new Map<string, DiagnosticEvent>(d.events.map((e: DiagnosticEvent) => [String(e.id), e]));
      if (!d.findings.every((f: RuntimeFinding) => {
        if (f.category !== 'concurrency_hazard' || f.status !== 'confirmed') return true;
        if (f.evidence.conflict_type !== 'logical_lost_update' || f.evidence.native_data_race_confirmed !== false
          || typeof f.evidence.key !== 'string' || !Array.isArray(f.evidence.event_ids)
          || !Array.isArray(f.evidence.final_outcomes)) return false;
        const observed = f.evidence.event_ids.map(id => eventsById.get(String(id))!)
          .filter(e => e.payload.key === f.evidence.key);
        const actors = (category: string) => new Set(observed.filter(e => e.category === category)
          .map(e => JSON.stringify([e.pid, e.actor_kind, e.actor_id])));
        const readers = actors('shared_read'), writers = actors('shared_write');
        return [...readers].filter(actor => writers.has(actor)).length >= 2
          && observed.some(e => e.category === 'shared_outcome' && 'expected' in e.payload && 'observed' in e.payload
            && JSON.stringify(e.payload.expected) !== JSON.stringify(e.payload.observed)
            && (f.evidence.final_outcomes as unknown[]).some(o => record(o) && o.key === f.evidence.key
              && JSON.stringify(o.expected) === JSON.stringify(e.payload.expected)
              && JSON.stringify(o.observed) === JSON.stringify(e.payload.observed)));
      })) return undefined;
    }
    if (p.allocation_backend != null && !validAllocationBackend(p.allocation_backend)) return undefined;
    if (p.memory_guard != null && !validMemoryGuard(p.memory_guard)) return undefined;
    return p as unknown as Profile;
  } catch {
    return undefined;
  }
}
