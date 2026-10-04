const assert = require('assert');
const m = require('../out/profileModel');
const { diagnose, callTree, overview } = require('../out/reportModel');
const { remapProfileKeys, toLocal } = require('../out/containerPaths');
const { reportHtml } = require('../out/reportWebview');
const file = '/app/work.py';
const base = { time_s: 1, share: 1, python_s: 1, native_s: 0, system_s: 0, cpu_unsplit_s: 0,
  samples: 10, rss_growth_mb: 0, rss_release_mb: 0 };
const p = { schema: 3, script: file, python: '3.13', gil_split: true, memory_mode: 'precise', wall_s: 3,
  peak_traced_mb: 20, rss_peak_mb: 40, file_hashes: { [file]: m.textHash('source') },
  files: { [file]: {
    2: { ...base, scope: 'Service.run()', func_line: 1, end_mb: 12, peak_mb: 12, leak_runs: 3,
      retention: { snapshots: 4, rises: 3, releases: 0, growth_mb: 9, observed_s: 3, points: [[0, 3], [1, 6], [2, 9], [3, 12]] },
      held_by: [{ holder: 'global HISTORY', type: 'list', items: 6, matching: 6 }] },
    4: { ...base, end_mb: 4, peak_mb: 4, held_by: [{ holder: 'Service.cache', type: 'dict', items: 2, matching: 2 }] },
    6: { ...base, end_mb: 0, peak_mb: 8 },
  } },
  functions: { [file]: { 1: { name: 'Service.run', time_s: 1, python_s: 1, native_s: 0,
    system_s: 0, peak_mb: 12, transient_peak_mb: 0, end_line: 10 } } },
  stacks: { frames: ['main', 'left', 'right', 'leaf'].map((name, i) =>
    ({ name, file, line: i + 1, first_line: i + 1, user: true })),
    samples: [
      { thread: '1', thread_name: 'MainThread', frames: [0, 1, 3], python_s: 1, native_s: 0, system_s: 0, unsplit_s: 0, samples: 10 },
      { thread: '2', thread_name: 'Worker', frames: [0, 2, 3], python_s: 0, native_s: 0, system_s: 2, unsplit_s: 0, samples: 20 },
    ], dropped_s: 0, depth_limited: false }
};
assert(m.parseProfile(JSON.stringify(p)));
const d = diagnose(p);
assert.deepStrictEqual(d.map(x => x.status), ['growing', 'retained', 'released']);
assert(d[0].recommendations.some(r => r.includes('deque(maxlen')));
assert(d[0].recommendations.some(r => r.includes('module scope')));
assert(d[0].recommendations.some(r => r.includes('warm-up')));
assert(d[1].recommendations.some(r => r.includes('TTL')));
assert(!d[1].evidence.some(r => r.includes('trailing')));
assert(d[2].recommendations[0].includes('released'));
assert.deepStrictEqual(diagnose({ ...p, memory_mode: 'fast' }), []);
const visual = { ...p, cpu_s: 2.4, samples: 30, rss_kind: 'current', rss_start_mb: 10, rss_end_mb: 30,
  timeline: [[0, 0, 10], [1, 12, 24], [3, 20, 30]],
  files: { [file]: { ...p.files[file], 2: { ...p.files[file][2], time_s: 2, share: .5 } } } };
assert(m.parseProfile(JSON.stringify(visual)), 'profile timeline is accepted');
const summary = overview(visual);
assert.deepStrictEqual(summary.timeline, visual.timeline);
assert.strictEqual(summary.cpuS, 2.4);
assert.strictEqual(summary.rssKind, 'current');
assert.strictEqual(summary.topLines[0].line, 2, 'hotspots sort by sampled time');
assert.strictEqual(summary.topLines[0].memory, '12.0 MB held');
const longTimeline = Array.from({ length: 1001 }, (_, i) => [i / 10, i / 100, 10 + i / 100]);
const bounded = overview({ ...visual, timeline: longTimeline }).timeline;
assert(bounded.length <= 300 && bounded.at(-1)[0] === 100, 'chart payload remains bounded and keeps the final sample');
assert.strictEqual(overview({ ...visual, memory_mode: 'off' }).topLines[0].memory, '');
assert.strictEqual(m.parseProfile(JSON.stringify({ ...visual, timeline: [[0, null, 10]] })), undefined);
assert.strictEqual(m.parseProfile(JSON.stringify({ ...visual, timeline: [[1, 1, 10], [0, 2, 11]] })), undefined);
assert.strictEqual(m.parseProfile(JSON.stringify({ ...visual, rss_kind: 'unknown' })), undefined);
const idx = new m.ProfileIndex(p);
assert(idx.insideSampledFunction(file, 10), 'unsampled function tail remains inside sampled function');
assert(!idx.insideSampledFunction(file, 11));
assert.strictEqual(m.heat(undefined, 'precise', { hotShare: .05, hotMb: 50 }, idx.insideSampledFunction(file, 9)), 'unknown');
const tree = callTree(p, 'elapsed');
assert.strictEqual(tree.nodes[0].value, 3);
const named = (t, name) => t.nodes.filter(n => t.frames[n.frame]?.name === name);
assert.strictEqual(named(tree, 'leaf').length, 2, 'same leaf keeps distinct callers');
assert.deepStrictEqual(callTree(p, 'elapsed', '1').frames.map(f => f.name), ['main', 'left', 'leaf'], 'payload carries only frames the filtered tree uses');
assert.strictEqual(tree.nodes.reduce((sum, n) => sum + n.self, 0), 3, 'self time conserved');
for (const n of tree.nodes) assert.strictEqual(n.value, n.self + n.children.reduce((sum, id) => sum + tree.nodes[id].value, 0));
assert.strictEqual(callTree(p, 'python').nodes[0].value, 1);
assert.strictEqual(callTree(p, 'elapsed', '2').nodes[0].value, 2);
assert.strictEqual(callTree(p, 'native').nodes[0].value, 0);
assert.strictEqual(callTree(p, 'elapsed', '', 2).omitted, 3, 'display cap reports omissions');
const recursive = structuredClone(p); recursive.stacks.samples[0].frames = [0, 1, 1, 3];
assert.strictEqual(named(callTree(recursive, 'python'), 'left').length, 2);
const mapped = remapProfileKeys(p, s => toLocal(s, [{ local: '/host', container: '/app' }]));
assert.strictEqual(mapped.stacks.frames[0].file, '/host/work.py');
const old = { ...p, schema: 2 }; delete old.stacks;
assert(m.parseProfile(JSON.stringify(old)), 'existing reports remain readable');
assert.strictEqual(callTree(old, 'elapsed').nodes[0].value, 0);
for (const bad of [{ schema: 3, files: {} }, { ...p, wall_s: 'oops' }, { ...p, schema: 99 },
  { ...p, files: { [file]: { 1: null } } }, { ...p, stacks: { ...p.stacks, samples: [{ ...p.stacks.samples[0], frames: [999] }] } }]) {
  assert.strictEqual(m.parseProfile(JSON.stringify(bad)), undefined, 'reject malformed report');
}
const html = reportHtml('abcdef');
assert(html.includes("default-src 'none'"));
assert(html.includes('id="overviewTab"') && html.includes('id="memoryTimeline"') && html.includes('id="hotspots"'));
assert(!html.includes('innerHTML'), 'profile strings must not be interpreted as HTML');
new (require('vm').Script)(html.match(/<script nonce="abcdef">([\s\S]*?)<\/script>/)[1]);
console.log('PASS overview data, memory diagnoses, call-path accounting, filters, report validation, and webview script syntax');
