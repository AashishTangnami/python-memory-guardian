// Cross-run comparison: matching across edits, exact deltas, run-to-run variation and comparability warnings.
const assert = require('assert');
const m = require('../out/profileModel');
const c = require('../out/compareModel');

const line = (o) => ({ time_s: 0, share: 0, python_s: 0, native_s: 0, system_s: 0, cpu_unsplit_s: 0, samples: 0,
  rss_growth_mb: 0, rss_release_mb: 0, alloc_mb: 0, peak_mb: 0, end_mb: 0, transient_peak_mb: 0, ...o });
const fn = (name) => ({ name, time_s: 0, python_s: 0, native_s: 0, system_s: 0, peak_mb: 0, transient_peak_mb: 0 });
function profile(dir, { lines, functions, hash = 'h1', ...rest }) {
  const file = `${dir}/app.py`;
  const p = { schema: 3, script: file, python: '3.13.0', gil_split: true, wall_s: 2, memory_mode: 'precise',
    interval_s: 0.01, samples: 200, peak_traced_mb: 60, rss_peak_mb: 200, rss_kind: 'current', native_untraced_mb: 40, frames: 2,
    file_hashes: { [file]: hash }, files: { [file]: lines }, functions: { [file]: functions },
    run: { argv: ['--rows', '10'], started_at: '2026-10-04T10:00:00Z', platform: 'darwin' }, ...rest };
  assert(m.parseProfile(JSON.stringify(p)), 'test profile is valid');
  return p;
}

// Baseline: work() at line 10. Current: five lines inserted above it (work() at 15), one line inserted
// inside it above `total = sum(rows)`, a different checkout folder, and the load line fixed (50 -> 10 MB).
const base = profile('/ci/checkout', {
  lines: {
    11: line({ func_line: 10, samples: 100, time_s: 1.0, alloc_mb: 50, peak_mb: 50, assigns: ['rows'], calls: ['load'] }),
    12: line({ func_line: 10, samples: 50, time_s: 0.5, alloc_mb: 2 }),                  // no names: matched by offset
    13: line({ func_line: 13, samples: 20, time_s: 0.2, alloc_mb: 8, assigns: ['total'], calls: ['sum'] }),
    3: line({ samples: 1, time_s: 0.01 }),                                               // module level
  },
  functions: { 10: fn('work'), 13: fn('work.<genexpr>') },
});
const cur = profile('/home/me/proj', {
  hash: 'h2',
  lines: {
    16: line({ func_line: 15, samples: 100, time_s: 1.0, alloc_mb: 10, peak_mb: 10, assigns: ['rows'], calls: ['load'] }),
    17: line({ func_line: 15, samples: 50, time_s: 0.5, alloc_mb: 2 }),
    19: line({ func_line: 19, samples: 20, time_s: 0.2, alloc_mb: 8, assigns: ['total'], calls: ['sum'] }),
    8: line({ samples: 1, time_s: 0.01 }),
  },
  functions: { 15: fn('work'), 19: fn('work.<genexpr>') },
  peak_traced_mb: 20,
});
const r = c.compareProfiles(base, cur, null, 'linux');
assert.deepStrictEqual(r.warnings, [], 'same environment and workload: no warnings');

// Functions: matched by file (relative to the script folder) and name; the genexpr is merged into work().
assert.deepStrictEqual(r.functions.map(f => [f.name, f.significant]), [['work', true], ['<module>', false]],
  'work() includes its generator expression; module-level time is unchanged');
const work = r.functions[0];
assert.deepStrictEqual([work.baseLine, work.line], [10, 15], 'matched across the 5-line shift');
assert.strictEqual(work.values.alloc.base, 60);
assert.strictEqual(work.values.alloc.cur, 20);
assert.strictEqual(work.values.alloc.delta, -40, 'exact delta: 50 -> 10 MB on the load line');
assert.strictEqual(work.values.alloc.noise, 9, 'noise: 15% of the larger value');
assert.strictEqual(work.values.alloc.verdict, 'better');
assert.strictEqual(work.time.verdict, 'same', 'unchanged time');
assert(work.significant);

// Lines: the named lines match by their assignments and calls, the unnamed one by its offset from the def.
const byBase = Object.fromEntries(r.lines.map(l => [l.baseLine, l]));
assert.strictEqual(byBase[11].line, 16, 'load line: unique names, matched despite the shift');
assert.strictEqual(byBase[11].values.alloc.delta, -40);
assert.strictEqual(byBase[12].line, 17, 'unnamed line: offset +2 from the def in both runs');
assert.strictEqual(byBase[13].line, 19, 'line moved within the function (+3 -> +4) still matches by names');
assert.strictEqual(byBase[3], undefined, 'module-level line in a changed file has no stable identity');
assert.deepStrictEqual(r.unmatchedLines, { baseline: 1, current: 1 });

// Run level.
const runRow = (label) => r.run.find(x => x.label === label).value;
assert.strictEqual(runRow('Peak traced allocations').delta, -40);
assert.strictEqual(runRow('Peak traced allocations').verdict, 'better');
assert.strictEqual(runRow('Run duration').verdict, 'same');

// Identical file contents: lines match by number, including module level.
const same = c.compareProfiles(base, profile('/ci/checkout', { lines: base.files['/ci/checkout/app.py'], functions: base.functions['/ci/checkout/app.py'] }), null, 'linux');
assert.strictEqual(same.lines.length, 4, 'every line matched by number when the file hash is equal');
assert(same.lines.every(l => !l.significant) && same.functions.every(f => !f.significant), 'a profile against itself changes nothing');
assert.deepStrictEqual(same.unmatchedLines, { baseline: 0, current: 0 });

// Attribution moving between lines of one function is not a line-level change.
const swapped = structuredClone(base), sl = swapped.files['/ci/checkout/app.py'];
sl[11] = { ...sl[11], alloc_mb: 30 }; sl[12] = { ...sl[12], alloc_mb: 22 };
const sw = c.compareProfiles(base, swapped, null, 'linux');
const l11 = sw.lines.find(l => l.line === 11);
assert.strictEqual(l11.values.alloc.verdict, 'same', '20 MB moved to the next line; the function total is unchanged');
assert.deepStrictEqual(l11.shifted, ['alloc']);

// Run-to-run variation (measured): a line with 0.54 s from 6 samples vs 0.61 s from 2 is not a change.
assert(c.timeNoise(0.54, 6, 0.61, 2) > 0.07);
assert.strictEqual(c.timeNoise(2, 2000, 2, 2000), 0.2, 'many samples: the 10% floor applies');
assert.strictEqual(c.memoryNoise(4, 5), 1);
assert.strictEqual(c.memoryNoise(4, 5, true), 10, 'RSS-based values: 10 MB floor');
assert.strictEqual(c.enclosingName('Service.handle.<lambda>'), 'Service.handle');
assert.strictEqual(c.enclosingName('<genexpr>'), '<module>');

// Comparability warnings.
const other = { ...cur, python: '3.12.4', memory_mode: 'fast', run: { ...cur.run, argv: ['--rows', '99'] }, interval_s: 0.005 };
const w = c.compareProfiles(base, other, null, 'linux').warnings.join('\n');
for (const s of ['Python versions', 'memory modes', 'arguments', 'sampling intervals']) assert(w.includes(s), `warns about ${s}`);
assert(!w.includes('traceback depths'), 'depth is only compared within precise mode');
const deeper = c.compareProfiles(base, { ...cur, frames: 8 }, null, 'linux').warnings.join('\n');
assert(deeper.includes('traceback depths'));
const mixed = c.compareProfiles(base, other, null, 'linux');
assert.deepStrictEqual(mixed.columns.map(x => x.key), ['rss'], 'precise vs fast: only RSS growth is common');

// Different tracing scopes (--trace-function in one run): warned, and every change is context.
const scoped = c.compareProfiles(base, { ...cur, trace_function: { name: 'work', calls: 1, traced_s: 1 } }, null, 'linux');
assert(scoped.warnings.some(w => w.includes('different parts of the runs') && w.includes('only work()')), 'scope warning');
assert.strictEqual(scoped.functions.find(f => f.name === 'work').values.alloc.verdict, 'context', 'no better/worse across scopes');
assert(scoped.run.every(r => r.value.verdict !== 'better' && r.value.verdict !== 'worse'));
assert(!c.compareProfiles(base, cur, null, 'linux').warnings.some(w => w.includes('different parts')), 'same scope: no warning');

// Peak moved: held-at-peak changes are context, not regressions.
const ms = (name, mb, first) => ({ depth: 2, frames: [{ file: '/ci/checkout/app.py', line: first + 1, name, first_line: first, user: true }],
  peak: { t: 1, total_bytes: mb * 1e6, other_bytes: 0, stacks: [{ frames: [0], bytes: mb * 1e6, truncated: false }] },
  exit: { t: 2, total_bytes: 0, other_bytes: 0, stacks: [] } });
const pBase = { ...base, memory_stacks: ms('work', 50, 10) };
const pCur = structuredClone(base);
pCur.memory_stacks = ms('other', 30, 40);
pCur.files['/ci/checkout/app.py'][41] = line({ func_line: 40, samples: 5, time_s: .05, peak_mb: 30 });
pCur.functions['/ci/checkout/app.py'][40] = fn('other');
const pm = c.compareProfiles(pBase, pCur, null, 'linux');
assert(pm.notes.some(n => n.includes('moved from work to other')));
assert.strictEqual(pm.functions.find(f => f.name === 'other').values.held.verdict, 'new', 'a new function stays new');
assert.deepStrictEqual(pm.sites.find(s => s.name === 'other').peak, { base: 0, cur: 30, delta: 30, noise: 4.5, verdict: 'context' },
  'a site missing from a snapshot held 0 bytes there; the rise is where the peak moved');
assert.strictEqual(pm.sites.find(s => s.name === 'work').peak.verdict, 'context');

// Baseline metadata and file names.
assert.deepStrictEqual(c.baselineMeta({ baseline: { name: 'before-fix', saved_at: 't', git_commit: 'abc', git_dirty: false } }),
  { name: 'before-fix', saved_at: 't', git_commit: 'abc', git_dirty: false });
assert.strictEqual(c.baselineMeta({ baseline: { name: 3 } }), null);
assert.strictEqual(c.baselineFileName('before-fix_1.2'), 'before-fix_1.2.json');
for (const bad of ['../x', 'a/b', '', '.hidden', 'x.', 'a b']) assert.strictEqual(c.baselineFileName(bad), null, bad);
assert.strictEqual(m.parseProfile(JSON.stringify({ ...base, run: { argv: [1], platform: 'x' } })), undefined, 'run.argv must be strings');

console.log('PASS cross-run comparison: matching across edits, exact deltas, variation thresholds, context verdicts and warnings');
