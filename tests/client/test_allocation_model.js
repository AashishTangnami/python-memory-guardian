const assert = require('assert');
const fs = require('fs');
const { validAllocationBackend } = require('../../out/profile/allocation');
const { parseProfile } = require('../../out/profile/parse');
const p = JSON.parse(fs.readFileSync(require('path').join(__dirname, '../profiler/out/workload_fast.json')));
const site = { process_id: 10, thread_id: 1, domain: 'system_allocator', scope: 'target', requested_bytes: 4096,
  allocation_count: 1, outstanding_at_capture_end_bytes: 0, outstanding_at_capture_end_blocks: 0,
  allocation_timestamp_ns: null, source: { file: '/work/main.py', line: 3, function: 'allocate', role: 'traced_user_callsite' },
  python_stack: [{ file: '/work/main.py', line: 3, function: 'allocate' }], native_stack: null };
const backend = { backend: 'memray', available: true, domain_totals_additive: false,
  availability: { allocation_timestamps: 'unavailable', native_coverage: 'partial' },
  files: [{ process_id: 10, artifact: { path: '/work/trace.bin', sha256: 'a'.repeat(64), bytes: 123, format: 'memray_all_allocations' }, sites: [site] }] };
assert(validAllocationBackend(backend));
assert(parseProfile(JSON.stringify({ ...p, allocation_backend: backend })));
for (const mutate of [b => b.domain_totals_additive = true, b => b.files[0].artifact.sha256 = 'bad',
  b => b.files[0].sites[0].allocation_timestamp_ns = 0, b => b.files[0].sites[0].requested_bytes = -1,
  b => b.files[0].sites[0].process_id = 11, b => b.files[0].sites[0].source.role = 'RSS allocation line']) {
  const bad = JSON.parse(JSON.stringify(backend)); mutate(bad);
  assert(!validAllocationBackend(bad));
  assert(!parseProfile(JSON.stringify({ ...p, allocation_backend: bad })));
}
assert(validAllocationBackend({ backend: 'sampled', available: false, reason: 'allocator events unavailable' }));
assert(!validAllocationBackend({ backend: 'sampled', available: true }));
console.log('PASS allocator evidence contract: provenance, units, unavailable timestamps, artifact integrity and non-additive layers');

const events = [1, 2, 3, 4, 5].map(id => ({ id: String(id), t: id, pid: 10, thread: id % 2,
  task: null, actor_kind: 'thread', actor_id: String(id % 2), source: null, stack: [],
  category: id < 3 ? 'shared_read' : id < 5 ? 'shared_write' : 'shared_outcome',
  payload: id === 5 ? { key: 'counter', expected: 2, observed: 1 } : { key: 'counter', value: id < 3 ? 0 : 1 } }));
const finding = { id: 'conflict', category: 'concurrency_hazard', severity: 'warning', confidence: 'high',
  status: 'confirmed', source: null, workload: {}, summary: 'logical conflict',
  reproduction: { command: ['python', 'fixture.py'], trace_path: '/work/events.jsonl' },
  evidence: { key: 'counter', event_ids: events.map(e => e.id), conflict_type: 'logical_lost_update', native_data_race_confirmed: false,
    final_outcomes: [{ key: 'counter', expected: 2, observed: 1 }] } };
const diagnostics = { schema: 1, instrumentation: 'cooperative', trace_path: '/work/events.jsonl',
  events, findings: [finding], dropped_events: 0, scan_limits: { max_nodes: 10, max_depth: 2 }, capabilities: {} };
assert(parseProfile(JSON.stringify({ ...p, diagnostics })));
for (const mutate of [f => f.evidence.final_outcomes = [], f => f.evidence.native_data_race_confirmed = true,
  f => f.evidence.final_outcomes[0].observed = 2, f => f.evidence.event_ids = ['unknown'], f => f.confidence = '100%']) {
  const bad = JSON.parse(JSON.stringify(diagnostics)); mutate(bad.findings[0]);
  assert(!parseProfile(JSON.stringify({ ...p, diagnostics: bad })), 'unsupported confirmation cannot become a trusted report finding');
}
console.log('PASS confirmed-concurrency consumer contract: conflict outcome, event references and no native race claim');
for (const mutate of [d => d.events.forEach(e => e.actor_id = 'one'),
  d => d.events.forEach(e => e.category = 'shared_access'),
  d => d.events[4].payload.observed = 2, d => d.events[0].payload.key = 'other']) {
  const bad = JSON.parse(JSON.stringify(diagnostics)); mutate(bad);
  assert(!parseProfile(JSON.stringify({ ...p, diagnostics: bad })), 'confirmation requires reads and writes by two actors and its recorded outcome');
}
