const assert = require('assert');
const m = require('../out/profileModel');
const { diagnose, callTree, overview, frameOrigin, topFunctions, neighbors, functionKey } = require('../out/reportModel');
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
const capped = callTree(p, 'elapsed', '', 2);
assert.strictEqual(capped.nodes[0].value, 3, 'the node limit never drops time from upper levels');
assert.deepStrictEqual([capped.nodes[1].value, capped.nodes[1].omitted], [3, 3], 'cut-off calls are counted on the deepest node shown');
for (const t of [capped])
  for (const n of t.nodes) assert.strictEqual(n.value, n.self + n.omitted + n.children.reduce((sum, id) => sum + t.nodes[id].value, 0), 'value = self + omitted + children');
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
const origin = (file, user = false) => frameOrigin({ file, line: 152, name: 'f', first_line: 1, user });
assert.deepStrictEqual(origin('/app/service.py', true), { kind: 'user', label: 'service.py:152', detail: 'your code' });
assert.strictEqual(origin('/app/billing/__init__.py', true).label, 'billing/__init__.py:152', 'packages name their folder');
assert.deepStrictEqual(origin('<frozen importlib._bootstrap_external>'),
  { kind: 'internal', label: 'Python import system', detail: 'finding and reading module files (import)' });
assert.strictEqual(origin('<frozen codecs>').detail, 'built into the interpreter (codecs)');
assert.strictEqual(origin('<string>').label, 'code run with exec() or eval()');
assert.strictEqual(origin('<attrs generated methods pkg.Cls>').label, 'generated code');
assert.deepStrictEqual(origin('/venv/lib/python3.13/site-packages/pandas/core/frame.py'),
  { kind: 'library', label: 'pandas (installed package)', detail: '' });
assert.strictEqual(origin('/usr/lib/python3/dist-packages/six.py').label, 'six (installed package)');
assert.strictEqual(origin('C:\\venv\\Lib\\site-packages\\numpy\\core.py').label, 'numpy (installed package)', 'Windows site-packages');
assert.deepStrictEqual(origin('/Library/Frameworks/Python.framework/Versions/3.13/lib/python3.13/json/encoder.py'),
  { kind: 'stdlib', label: 'json (standard library)', detail: 'module json.encoder' });
assert.strictEqual(origin('/usr/lib/python3.13t/collections/__init__.py').detail, '', 'no detail when it would repeat the label');
assert.strictEqual(origin('C:\\Python313\\Lib\\asyncio\\events.py').kind, 'stdlib', 'Windows stdlib');
assert.deepStrictEqual(origin('/opt/tools/helper.py'), { kind: 'library', label: 'helper.py (outside your project)', detail: '' });
assert(callTree(p, 'elapsed').frames.every(f => f.origin && f.origin.kind), 'every sent frame carries its origin');
// Frame views: grouped merges runs of non-user frames with one origin; mine keeps user frames only.
const fr = (name, file, user = false) => ({ name, file, line: 1, first_line: 1, user });
const mixed = { ...p, stacks: { frames: [
    fr('main', '/app/main.py', true), fr('handler', '/app/svc.py', true),
    fr('_find_and_load', '<frozen importlib._bootstrap>'), fr('_load_unlocked', '<frozen importlib._bootstrap>'),
    fr('exec_module', '<frozen importlib._bootstrap_external>'),
    fr('dumps', '/usr/lib/python3.12/json/__init__.py'), fr('encode', '/usr/lib/python3.12/json/encoder.py'),
    fr('read_csv', '/venv/lib/python3.12/site-packages/pandas/io.py')],
  samples: [[0, 2, 3, 4], [0, 1, 5, 6], [0, 1, 7], [0, 2, 7, 3]].map((frames, i) =>
    ({ thread: '1', thread_name: 'MainThread', frames, python_s: i === 1 ? 2 : 1, native_s: 0, system_s: 0, unsplit_s: 0, samples: 1 })),
  dropped_s: 0, depth_limited: false } };
assert(m.parseProfile(JSON.stringify(mixed)), 'mixed fixture is a valid profile');
const views = Object.fromEntries(['all', 'grouped', 'mine'].map(v => [v, callTree(mixed, 'python', '', 25000, v)]));
for (const [v, t] of Object.entries(views)) {
  assert.strictEqual(t.nodes[0].value, 5, `${v}: total time is the same in every view`);
  assert.strictEqual(t.nodes.reduce((sum, n) => sum + n.self, 0), 5, `${v}: self time conserved`);
  for (const n of t.nodes) assert.strictEqual(n.value, n.self + n.children.reduce((sum, id) => sum + t.nodes[id].value, 0), v);
}
for (const v of ['all', 'grouped', 'mine']) {
  const small = callTree(mixed, 'python', '', 4, v);
  assert.strictEqual(small.nodes[0].value, 5, `${v}: a tight node limit keeps the full total`);
  for (const n of small.nodes) assert.strictEqual(n.value, n.self + n.omitted + n.children.reduce((sum, id) => sum + small.nodes[id].value, 0), v);
}
const box = (t, n) => t.frames[n.frame];
const kids = (t, n) => n.children.map(id => t.nodes[id]);
const g = views.grouped, gMain = kids(g, g.nodes[0])[0];
const imports = kids(g, gMain).find(n => box(g, n).origin.label === 'Python import system');
assert.deepStrictEqual(box(g, imports).group, { count: 3, names: ['_find_and_load', '_load_unlocked', 'exec_module'] },
  'two frozen modules with one label become one box');
assert.strictEqual(imports.value, 2, 'the same group under the same caller merges across samples');
assert.deepStrictEqual(kids(g, imports).map(n => box(g, n).name), ['read_csv'], 'a different origin starts a new step');
assert.deepStrictEqual(kids(g, kids(g, imports)[0]).map(n => box(g, n).name), ['_load_unlocked'],
  'a later run holding one function is shown as that function');
const handler = kids(g, gMain).find(n => box(g, n).name === 'handler');
const json = kids(g, handler).find(n => box(g, n).group);
assert.deepStrictEqual([box(g, json).name, box(g, json).group.count, json.value], ['json (standard library)', 2, 2]);
assert.deepStrictEqual(kids(g, handler).filter(n => !box(g, n).group).map(n => box(g, n).name), ['read_csv'],
  'a single library frame keeps its function name');
const mine = views.mine;
assert(mine.frames.every(f => f.user), 'only my code: no library or internal boxes');
const mMain = kids(mine, mine.nodes[0])[0], mHandler = kids(mine, mMain)[0];
assert.deepStrictEqual([mMain.self, mHandler.self], [2, 3], 'library time is counted in the calling user function');
assert.strictEqual(views.all.frames.filter(f => f.group).length, 0, 'all frames: nothing grouped');
assert.strictEqual(views.all.nodes.length, callTree(mixed, 'python').nodes.length, 'all is the default');
// Top functions: summed across call paths; total counted once per stack; memory only for your functions.
const withMemory = { ...mixed, memory_mode: 'precise',
  functions: { '/app/main.py': { 1: { name: 'main', time_s: 5, python_s: 5, native_s: 0, system_s: 0, peak_mb: 3, transient_peak_mb: 7, alloc_mb: 5 } } } };
const top = topFunctions(withMemory, 'python');
const row = name => top.find(r => r.name === name);
assert.strictEqual(top[0].name, 'encode', 'ranked by self time');
assert.deepStrictEqual([row('main').self, row('main').total, row('main').callers, row('main').memoryMb], [0, 5, 0, 7]);
assert.deepStrictEqual([row('read_csv').self, row('read_csv').total, row('read_csv').callers], [1, 2, 2], 'called from handler and the import system');
assert.deepStrictEqual([row('_load_unlocked').self, row('_load_unlocked').callers], [1, 2]);
assert.strictEqual(row('read_csv').memoryMb, null, 'no memory claim for library code');
assert.strictEqual(top.reduce((sum, r) => sum + r.self, 0), 5, 'self time adds up to the total');
assert(topFunctions(withMemory, 'python', '', 'mine').every(r => r.user), 'only my code: your functions only');
const recursive2 = { ...mixed, stacks: { ...mixed.stacks, samples: [{ ...mixed.stacks.samples[0], frames: [0, 1, 1, 1, 6], python_s: 3 }] } };
const rec = topFunctions(recursive2, 'python');
assert.deepStrictEqual([rec.find(r => r.name === 'handler').total, rec.find(r => r.name === 'handler').callers], [3, 1],
  'recursion is counted once, and a function does not count as its own caller');
const key = name => functionKey(mixed.stacks.frames.find(f => f.name === name));
const csv = neighbors(mixed, 'python', key('read_csv'));
assert.deepStrictEqual([csv.value, csv.self], [2, 1]);
assert.deepStrictEqual(csv.callers.map(n => [n.name, n.value]).sort(), [['_find_and_load', 1], ['handler', 1]]);
assert.deepStrictEqual(csv.callees.map(n => [n.name, n.value]), [['_load_unlocked', 1]]);
const h = neighbors(mixed, 'python', key('handler'));
assert.deepStrictEqual([h.value, h.self, h.callers.map(n => n.name), h.callees.map(n => [n.name, n.value])],
  [3, 0, ['main'], [['dumps', 2], ['read_csv', 1]]]);
// Memory measures: memory-stack tables feed the same tree, table and callers code, weighted by bytes.
const memFrames = [fr('<module>', '/app/main.py', true), fr('Svc.handle', '/app/svc.py', true), fr('leaf', '/app/svc.py', true),
  { name: '', file: '<frozen importlib._bootstrap>', line: 5, first_line: 5, user: false }];
const memTable = (scale) => ({ t: 1, total_bytes: 4500 * scale, other_bytes: 0, stacks: [
  { frames: [0, 1, 2], bytes: 3000 * scale, truncated: true }, { frames: [0, 1], bytes: 1000 * scale, truncated: false },
  { frames: [3], bytes: 500 * scale, truncated: true }] });
const withStacks = { ...mixed, memory_mode: 'precise', memory_stacks: { depth: 2, frames: memFrames, peak: memTable(2), exit: memTable(1) } };
assert(m.parseProfile(JSON.stringify(withStacks)), 'memory stacks are accepted');
const badStacks = structuredClone(withStacks); badStacks.memory_stacks.exit.stacks[0].frames = [99];
assert.strictEqual(m.parseProfile(JSON.stringify(badStacks)), undefined, 'a frame id outside the table is rejected');
const memExit = callTree(withStacks, 'mem_exit');
assert.strictEqual(memExit.nodes[0].value, 4500, 'exit tree totals the exit table');
assert.strictEqual(callTree(withStacks, 'mem_peak').nodes[0].value, 9000, 'peak tree uses the peak table');
assert.strictEqual(callTree(withStacks, 'mem_exit', '1').nodes[0].value, 4500, 'memory stacks have no thread, so the thread filter does not hide them');
assert(memExit.frames.some(f => f.name === 'line 5' && f.origin.label === 'Python import system'), 'nameless frames show their line and origin');
const memTop = topFunctions(withStacks, 'mem_exit');
assert.deepStrictEqual(memTop.slice(0, 3).map(r => [r.name, r.self, r.total]),
  [['leaf', 3000, 3000], ['Svc.handle', 1000, 4000], ['line 5', 500, 500]], 'ranked by bytes allocated directly');
assert.strictEqual(memTop.find(r => r.name === '<module>').total, 4000);
assert.deepStrictEqual(neighbors(withStacks, 'mem_exit', functionKey(memFrames[1])).callees.map(n => [n.name, n.value]), [['leaf', 3000]]);
assert.strictEqual(callTree(withStacks, 'python').nodes[0].value, 5, 'time measures are unchanged');
assert.strictEqual(callTree(mixed, 'mem_exit').nodes[0].value, 0, 'no memory stacks: an empty memory tree');
// Inverted (bottom-up) view: functions where value is spent on top, callers below, merged by function.
for (const [label, prof, metric] of [['time', mixed, 'python'], ['memory', withStacks, 'mem_exit']]) {
  const inv = callTree(prof, metric, '', 25000, 'all', true);
  const id = (file, line, name) => `${file}|${line}|${name}`;   // displayed identity: nameless frames show as "line N"
  const tops = new Map(inv.nodes[0].children.map(c => { const f = inv.frames[inv.nodes[c].frame]; return [id(f.file, f.first_line, f.name), inv.nodes[c].value]; }));
  for (const r of topFunctions(prof, metric)) assert.strictEqual(tops.get(id(r.file, r.line, r.name)) ?? 0, r.self, `${label}: inverted top level = self value of ${r.name}`);
  assert.strictEqual(inv.nodes[0].value, callTree(prof, metric).nodes[0].value, `${label}: same total as top-down`);
  for (const n of inv.nodes) assert.strictEqual(n.value, n.self + n.omitted + n.children.reduce((sum, id) => sum + inv.nodes[id].value, 0), label);
}
const inv = callTree(mixed, 'python', '', 25000, 'all', true);
const chain = [];
for (let n = inv.nodes[inv.nodes[0].children.find(id => inv.frames[inv.nodes[id].frame].name === 'encode')]; n; n = inv.nodes[n.children[0]]) chain.push(inv.frames[n.frame].name);
assert.deepStrictEqual(chain, ['encode', 'dumps', 'handler', 'main'], 'below a function: its callers, innermost first');
const twoLines = { ...mixed, stacks: { ...mixed.stacks,
  frames: [fr('main', '/app/main.py', true), { name: 'work', file: '/app/w.py', line: 12, first_line: 10, user: true }, { name: 'work', file: '/app/w.py', line: 15, first_line: 10, user: true }],
  samples: [[0, 1], [0, 2]].map(frames => ({ thread: '1', thread_name: 'MainThread', frames, python_s: 1, native_s: 0, system_s: 0, unsplit_s: 0, samples: 1 })) } };
const merged = callTree(twoLines, 'python', '', 25000, 'all', true);
assert.deepStrictEqual(merged.nodes[0].children.map(id => [merged.frames[merged.nodes[id].frame].name, merged.frames[merged.nodes[id].frame].line, merged.nodes[id].value]),
  [['work', 10, 2]], 'one box per function across call lines, at its first line');
assert.strictEqual(callTree(twoLines, 'python').nodes[1].children.length, 2, 'top-down keeps the two call lines apart');
// Time windows: diagnosis from each line's retention snapshots inside the window only.
const windowed = structuredClone(p);
const wl = Object.values(windowed.files)[0], wkey = Object.keys(wl)[0];
wl[wkey] = { ...wl[wkey], leak_runs: undefined, end_mb: 2, held_by: [{ holder: 'global CACHE', type: 'dict', items: 9, matching: 4 }],
  retention: { snapshots: 7, rises: 4, releases: 2, growth_mb: 2, observed_s: 6, peak_mb: 40,
    points: [[0, 1], [1, 10], [2, 20], [3, 40], [4, 5], [5, 3], [6, 2]] } };
const whole = diagnose(windowed).find(d => d.line === Number(wkey));
const early = diagnose(windowed, { from: 0, to: 3 }).find(d => d.line === Number(wkey));
assert.deepStrictEqual([early.status, early.peakMb, early.endMb, early.points.length], ['growing', 40, 40, 4], 'rising window: growing, peak and end at 40 MB');
assert.deepStrictEqual(early.holders, [], 'holders are found at exit, outside this window');
assert(early.evidence.some(e => e.includes('only searched at exit')));
const late = diagnose(windowed, { from: 3.5, to: 6 }).find(d => d.line === Number(wkey));
assert.deepStrictEqual([late.status, late.peakMb, late.endMb], ['retained', 5, 2], 'falling window: retained, with its own peak');
assert.strictEqual(late.holders.length, 1, 'a window that reaches exit cites the exit holders');
assert.strictEqual(diagnose(windowed, { from: 7, to: 9 }).find(d => d.line === Number(wkey)), undefined, 'no snapshots in the window: no card');
assert.strictEqual(whole.peakMb, 40, 'no window: whole-run diagnosis unchanged');
assert.deepStrictEqual(diagnose({ ...windowed, memory_mode: 'fast' }, { from: 0, to: 3 }), [], 'fast mode has no retention evidence');
// Native estimate in the report: overview card value, top-line memory text, Top functions column.
const nat = structuredClone(withMemory); nat.rss_kind = 'current'; nat.native_untraced_mb = 363;
nat.functions['/app/main.py'][1].rss_growth_mb = 130; nat.functions['/app/main.py'][1].alloc_mb = 3;
const natFile = Object.keys(nat.files)[0], natLine = Object.keys(nat.files[natFile])[0];
Object.assign(nat.files[natFile][natLine], { time_s: 1, rss_growth_mb: 127, alloc_mb: 0, peak_mb: 0, transient_peak_mb: 0, leak_runs: undefined });
assert.strictEqual(overview(nat).nativeUntracedMb, 363);
assert.strictEqual(overview({ ...nat, memory_mode: 'fast' }).nativeUntracedMb, null, 'fast mode: no estimate card');
assert.strictEqual(overview(nat).topLines.find(l => l.line === Number(natLine)).memory, 'native ≈ +127.0 MB');
const natTop = topFunctions(nat, 'python');
assert.strictEqual(natTop.find(r => r.name === 'main').nativeMb, 127);
assert.strictEqual(natTop.find(r => r.name === 'read_csv').nativeMb, null, 'no estimate for library code');
assert(topFunctions({ ...nat, memory_mode: 'fast' }, 'python').every(r => r.nativeMb === null), 'precise mode only');
const html = reportHtml('abcdef');
assert(html.includes("default-src 'none'"));
assert(html.includes('id="overviewTab"') && html.includes('id="memoryTimeline"') && html.includes('id="hotspots"'));
assert(!html.includes('innerHTML'), 'profile strings must not be interpreted as HTML');
new (require('vm').Script)(html.match(/<script nonce="abcdef">([\s\S]*?)<\/script>/)[1]);
console.log('PASS overview data, memory diagnoses, call-path accounting, filters, report validation, and webview script syntax');
