const assert = require('assert');
const { parseProfile } = require('../../out/profile/parse');
const { runNotes } = require('../../out/profile/labels');
const { validMemoryGuard } = require('../../out/profile/memoryGuard');
const { compareProfiles } = require('../../out/compare/compare');
const { remapProfileKeys } = require('../../out/container/paths');
const M = 2 ** 20;
const guard = { status: 'stopped', budget_bytes: 256 * M, reserve_bytes: 512 * M,
  stop_reason: 'rss_budget', scope: 'process_group', supervisor_pid: 1, profiled_pid: 2,
  child_exit_code: -9, peak_root_rss_bytes: 40 * M, peak_group_rss_bytes: 280 * M,
  last_available_bytes: 600 * M, last_group_rss_bytes: 280 * M, poll_interval_s: .1,
  coverage: 'partial', hard_limit: null, notes: [],
  initial_memory: { available_bytes: 1024 * M, total_bytes: 4096 * M, host_available_bytes: 1024 * M,
    container_available_bytes: null, container_status: 'not_applicable' },
  raw_artifacts: [{ path: '/app/capture.memray', bytes: 123, integrity: 'unverified' }] };
const profile = { schema: 3, script: '/app/target.py', python: '3.13', gil_split: false,
  wall_s: .2, memory_mode: 'precise', files: {}, functions: {}, file_hashes: {},
  memory_snapshot_at_exit: false, memory_stacks: null, peak_traced_mb: null, memory_guard: guard };
assert(validMemoryGuard(guard));
assert(parseProfile(JSON.stringify(profile)));
assert(runNotes(profile).some(n => n.includes('not a confirmed leak or OOM')));
assert(compareProfiles(profile, profile, null, 'linux').warnings.some(n => n.includes('did not complete')));
assert.strictEqual(compareProfiles(profile, { ...profile, wall_s: 10 }, null, 'linux').run[0].value.verdict, 'context',
  'a stopped workload cannot establish a runtime regression or improvement');
for (const mutate of [g => g.peak_group_rss_bytes = 128 * M,
  g => g.budget_bytes = 2048 * M, g => g.supervisor_pid = 2,
  g => g.stop_reason = 'OOM', g => g.coverage = 'complete',
  g => g.status = 'completed', g => g.stop_reason = 'system_reserve']) {
  const bad = JSON.parse(JSON.stringify(guard)); mutate(bad);
  assert(!validMemoryGuard(bad), 'budget finding requires matching measured evidence');
  assert(!parseProfile(JSON.stringify({ ...profile, memory_guard: bad })));
}
const mapped = remapProfileKeys(profile, p => p.replace('/app', '/host'));
assert.strictEqual(mapped.memory_guard.raw_artifacts[0].path, '/host/capture.memray');
assert.strictEqual(profile.memory_guard.raw_artifacts[0].path, '/app/capture.memray');
console.log('PASS memory guard consumer: measured pressure, incomplete execution, no OOM inference and artifact mapping');
