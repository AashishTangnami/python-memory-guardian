const fs = require("fs"), assert = require("assert");
const m = { ...require("../../out/profile/parse"), ...require("../../out/profile/index"), ...require("../../out/profile/metrics"), ...require("../../out/profile/labels") };
const WL = require("path").join(__dirname, "..", "fixtures", "profiler", "workload.py");
const src = fs.readFileSync(WL, "utf8");
const th = { hotShare: 0.05, hotMb: 50 };
// The checks below need fixtures with sampled time. A free-threaded Python writes none (stack_sampling.active
// is false), so there only the build-specific part runs: its real output must parse and say why time is missing.
const OUT = require("path").join(__dirname, "..", "profiler", "out");
if (JSON.parse(fs.readFileSync(require("path").join(OUT, "workload_fast.json"), "utf8")).stack_sampling?.active === false) {
  for (const f of fs.readdirSync(OUT).filter((n) => n.endsWith(".json"))) {
    const p = m.parseProfile(fs.readFileSync(require("path").join(OUT, f), "utf8"));
    assert(p, `${f}: free-threaded output parses`);
    assert(m.runNotes(p).some((n) => n.includes("were not sampled")), `${f}: the missing time is explained`);
  }
  console.log("PASS free-threaded profiles parse and explain the missing time");
  console.log("SKIP sampled-time checks: fixtures come from a free-threaded build");
  process.exit(0);
}
for (const mode of ["fast", "precise"]) {
  const p = m.parseProfile(fs.readFileSync(require("path").join(__dirname, "..", "profiler", "out", `workload_${mode}.json`), "utf8"));
  assert(p, "profile parses");
  const idx = new m.ProfileIndex(p);
  const file = WL;
  assert.strictEqual(idx.state(file, src), "fresh");
  assert.strictEqual(idx.state(file, src.replace(/\n/g, "\r\n")), "fresh", "CRLF-insensitive");
  assert.strictEqual(idx.state(file, src + "\n# edit"), "stale", "edits invalidate");
  assert.strictEqual(idx.state("/elsewhere.py", src), "absent");
  const invalidated = new m.ProfileIndex({ ...p, file_hashes: {} });
  assert.strictEqual(invalidated.state(file, src), "stale",
    "source changed during profiling: omitted hash must never count as fresh");
  console.log(`--- ${mode} mode`);
  for (const ln of [7, 12, 16, 19, 24, 29, 34]) {
    const e = idx.line(file, ln), h = m.heat(e, p.memory_mode, th, idx.insideSampledFunction(file, ln));
    console.log(`L${ln} [${h.padEnd(7)}] ${e ? m.lineLabel(e, p) : "(no data)"}`);
  }
  const f = Object.values(idx.functions.get(file)).find(f => f.name === "temporary");
  if (f) console.log("func:", m.funcLabel(f, p));
}
// severity rules
assert.strictEqual(m.adjustSeverity(m.Sev.Warning, "hot"), m.Sev.Error);
assert.strictEqual(m.adjustSeverity(m.Sev.Information, "hot"), m.Sev.Warning);
assert.strictEqual(m.adjustSeverity(m.Sev.Warning, "cold"), m.Sev.Hint);
assert.strictEqual(m.adjustSeverity(m.Sev.Error, "cold"), m.Sev.Error);
assert.strictEqual(m.adjustSeverity(m.Sev.Warning, "unknown"), m.Sev.Warning);
assert.strictEqual(m.heat({ samples: 0, line_events: 4, rss_growth_mb: 0 }, "off", th), "unknown",
  "an executed but unsampled line must not be demoted");
assert.strictEqual(m.heat({ samples: 0, line_events: 0, rss_growth_mb: 0 }, "off", th), "cold");
// A free-threaded run samples no time, so a line without memory evidence may still have run: not cold.
for (const e of [undefined, { samples: 0, line_events: 0, rss_growth_mb: 0 }])
  assert.strictEqual(m.heat(e, "fast", th, false, { memory_mode: "fast", stack_sampling: { active: false } }), "unknown",
    "an unsampled run must not demote static findings");
{ const p = m.parseProfile(fs.readFileSync(require("path").join(__dirname, "../profiler/out/workload_fast.json"), "utf8"));
  const file = Object.keys(p.files)[0];
  p.files[file][Object.keys(p.files[file])[0]].line_events = -1;
  assert.strictEqual(m.parseProfile(JSON.stringify(p)), undefined, "negative line-event count is rejected"); }
{ const p = m.parseProfile(fs.readFileSync(require("path").join(__dirname, "../profiler/out/workload_fast.json"), "utf8"));
  const lo = { objects: [{ holder: "global BIG", type: "bytearray", mb: 20, items: null, estimated: false }], complete: true };
  assert(m.parseProfile(JSON.stringify({ ...p, largest_objects: lo })), "largest objects are accepted");
  assert.strictEqual(m.parseProfile(JSON.stringify({ ...p, largest_objects: { ...lo, objects: [{ ...lo.objects[0], mb: -1 }] } })), undefined, "negative size rejected");
  assert.strictEqual(m.parseProfile(JSON.stringify({ ...p, largest_objects: { objects: [], complete: "yes" } })), undefined, "complete must be boolean"); }
// A process RSS residual cannot identify a native allocation at a sampled Python line.
{ const pp = { memory_mode: "precise", gil_split: true };
  const observed = { time_s: 0, share: 0, python_s: 0, native_s: 0, system_s: 0, cpu_unsplit_s: 0, samples: 1,
    rss_growth_mb: 127, rss_release_mb: 0, alloc_mb: 0, peak_mb: 0, transient_peak_mb: 0 };
  assert.strictEqual(m.nativeMb(observed), 0, "legacy native helper no longer invents source attribution");
  assert(!m.lineLabel(observed, pp).includes("native"), "RSS observations cannot label a Python line as native allocator");
  assert.strictEqual(m.heat(observed, "precise", th), "unknown", "process RSS growth does not establish source memory heat");
  assert(!m.funcLabel({ name: "load", time_s: 1, python_s: 1, native_s: 0, system_s: 0, peak_mb: 0,
    transient_peak_mb: 0, alloc_mb: 2, rss_growth_mb: 60 }, pp).includes("native"));
  assert(!m.lineLabel(observed, { ...pp, memory_mode: "fast" }).includes("RSS"), "process RSS remains in the process report"); }
{ const p = m.parseProfile(fs.readFileSync(require("path").join(__dirname, "../profiler/out/workload_fast.json"), "utf8"));
  assert.deepStrictEqual(m.runNotes(p), [], "a complete run has no notes");
  const lost = m.parseProfile(JSON.stringify({ ...p, memory_tracing_lost_s: 1.25, sampler_error: "ValueError: boom" }));
  assert(lost, "tracing-lost and sampler-error fields are accepted");
  const [tracing, sampler] = m.runNotes(lost);
  assert(tracing.includes("1.25 s") && tracing.includes("leak detection was skipped"), tracing);
  assert(sampler.includes("ValueError: boom"), sampler);
  assert.strictEqual(m.parseProfile(JSON.stringify({ ...p, memory_tracing_lost_s: -1 })), undefined, "negative time is rejected");
  assert.strictEqual(m.parseProfile(JSON.stringify({ ...p, sampler_error: 3 })), undefined, "non-string error is rejected");
  const ft = m.parseProfile(JSON.stringify({ ...p, stack_sampling: { active: false, reason: "free-threaded Python" } }));
  assert(ft, "an inactive stack sampler is accepted");
  assert.deepStrictEqual(m.runNotes(ft).filter((n) => n.includes("not sampled (free-threaded Python)")).length, 1, "and explained");
  const { stack_sampling: _, ...older } = p;
  assert(m.parseProfile(JSON.stringify(older)), "older profiles without stack_sampling are accepted");
  assert.strictEqual(m.parseProfile(JSON.stringify({ ...p, stack_sampling: { active: "no", reason: null } })), undefined, "non-boolean active is rejected"); }
assert.strictEqual(m.normPath("C:\\Proj\\a.py", "win32"), m.normPath("c:/proj/A.py", "win32"));
const pp = m.parseProfile(fs.readFileSync(require("path").join(__dirname, "../profiler/out/workload_precise.json"), "utf8"));
console.log("leak msg:", m.leakMessage(new m.ProfileIndex(pp).line(WL, 24)).slice(0, 120) + "…");
{ const p = m.parseProfile(fs.readFileSync(require("path").join(__dirname, "../profiler/out/workload_fast.json"), "utf8")), idx = new m.ProfileIndex(p);
  const f = WL;
  assert.notStrictEqual(m.heat(idx.line(f, 24), p.memory_mode, th, idx.insideSampledFunction(f, 24)), "cold",
    "leak line inside a sampled function must never be demoted");
  assert.strictEqual(idx.insideSampledFunction(f, 3), false, "module-level gaps are outside functions"); }
{ const hp = m.parseProfile(fs.readFileSync(require("path").join(__dirname, "../profiler/out/holders_workload_precise.json"), "utf8"));
  const hidx = new m.ProfileIndex(hp), hf = require("path").join(__dirname, "..", "fixtures", "profiler", "holders_workload.py");
  const e13 = hidx.line(hf, 13), e17 = hidx.line(hf, 17);
  const msg = m.leakMessage(e13);
  console.log("leak diagnostic:", msg.slice(0, 190) + "…");
  console.log("line label L13:", m.lineLabel(e13, hp));
  console.log("line label L17:", m.lineLabel(e17, hp));
  assert(msg.startsWith("Service.handle() › `self.history.append` — "), "scope + subject prefix");
  assert(msg.includes("`global svc.history` (list, 30 items)"), "rooted owner path named in message");
  assert(m.lineLabel(e13, hp).includes("held by global svc.history"), "rooted owner path in inline label");
  assert(e17.assigns.includes("scratch"), "assigned variable extracted from source");
  assert(m.lineLabel({ ...e17, peak_mb: 5 }, hp).includes("→ scratch"), "assigned variable in label with measured memory");
  assert(!m.lineLabel({ ...e17, peak_mb: 0 }, hp).includes("held "), "no memory label without a measured peak");
  // scratch: one 5 MB buffer alive at a time, freed by exit. Snapshot retention
  // supports that observation, but cannot measure total allocate-and-free traffic.
  assert(!m.lineLabel(e17, hp).includes("alloc "), "scratch: allocation volume is unavailable from snapshots");
  assert((e17.peak_mb ?? 0) <= 5.1, "scratch: at most one 5 MB buffer held at the peak");
  assert.strictEqual(e17.end_mb ?? 0, 0, "scratch: nothing held at exit");
  assert(!e17.leak_runs, "scratch: not a leak");
  assert(m.lineLabel(e13, hp).includes("held "), "leak line shows held memory");
  const fn = Object.values(hidx.functions.get(m.normPath(hf)));
  console.log("function label:", m.funcLabel(fn.find(f => f.name === "Service.handle"), hp)); }
{ const base = { memory_mode: "precise", frames: 2 };
  assert(m.unattributedNote({ ...base, unattributed_peak_mb: 31.7 }).includes("31.7 MB"), "note shows MB");
  assert(m.unattributedNote({ ...base, unattributed_peak_mb: 31.7 }).includes("2-frame"), "note shows depth");
  assert.strictEqual(m.unattributedNote({ ...base, unattributed_peak_mb: 0.4 }), "", "no note under 1 MB");
  assert.strictEqual(m.unattributedNote({ memory_mode: "fast", unattributed_peak_mb: 50 }), "", "precise only"); }
{ // Old residual fields remain parseable but never create source-level native attribution.
  assert.strictEqual(m.nativeMb({ rss_growth_mb: 100, alloc_mb: 40 }), 0, "older profiles cannot establish native allocation provenance");
  assert.strictEqual(m.nativeMb({ rss_growth_mb: 100, alloc_mb: 40, profiler_mb: 50 }), 0, "10 MB left is within tracing overhead");
  assert.strictEqual(m.nativeMb({ rss_growth_mb: 400, alloc_mb: 40, profiler_mb: 50 }), 0, "even a large residual has no source allocation path");
  const base = JSON.parse(fs.readFileSync(require("path").join(__dirname, "../profiler/out/workload_precise.json"), "utf8"));
  assert(m.parseProfile(JSON.stringify(base)), "a fresh precise profile validates with the new fields");
  assert(Array.isArray(base.timeline_stacks) && base.timeline_stacks.length === base.timeline.length, "timeline stacks recorded");
  assert.strictEqual(m.parseProfile(JSON.stringify({ ...base, timeline_stacks: [0] })), undefined, "timeline stacks must match the timeline");
  assert.strictEqual(m.parseProfile(JSON.stringify({ ...base, trace_function: { name: "f", calls: -1, traced_s: 0 } })), undefined);
  const f = Object.keys(base.functions)[0], k = Object.keys(base.functions[f])[0];
  for (const field of ["alloc_mb", "rss_growth_mb", "profiler_mb"]) {
    const bad = structuredClone(base); bad.functions[f][k][field] = "12";
    assert.strictEqual(m.parseProfile(JSON.stringify(bad)), undefined, `function ${field} must be a number`);
  } }
console.log("ALL MODEL TESTS PASSED");

// Corrupt residuals, unavailable metrics and historical warm-up cannot become confirmed source evidence.
{ const p = m.parseProfile(fs.readFileSync(require('path').join(__dirname, '../profiler/out/workload_precise.json'), 'utf8'));
  for (const bad of ['bogus', -1, {}, []]) {
    assert.strictEqual(m.parseProfile(JSON.stringify({ ...p, native_untraced_mb: bad })), undefined,
      'every consumed native residual must be a valid numeric value or null');
  }
  assert.strictEqual(m.processResidualMb({ ...p, native_untraced_mb: 0 }), null,
    'within-margin residual is unavailable, not proof of zero native allocations');
  assert.strictEqual(m.processResidualMb({ ...p, native_untraced_mb: 32, memory_tracing_lost_s: 1 }), null);
  assert.strictEqual(m.processResidualMb({ ...p, native_untraced_mb: 32, trace_function: { name: 'work' } }), null);
  assert.strictEqual(m.processResidualMb({ ...p, native_untraced_mb: 32 }), 32, 'positive residual remains process-level context');
  const plateau = { time_s: 0, share: 0, samples: 1, python_s: 0, native_s: 0, system_s: 0,
    rss_growth_mb: 0, leak_runs: 4, peak_mb: 10, end_mb: 10,
    retention: { points: [[0, 2], [1, 4], [2, 6], [3, 10], [4, 10], [5, 10], [6, 10], [7, 10]] } };
  assert(!m.isGrowingRetention(plateau, p), 'bounded plateau rejects a historical leak flag');
  assert(!m.lineLabel(plateau, p).includes('⚠'), 'warm-up does not create a leak overlay');
  assert(!m.evidence(plateau, 'hot', p).includes('leaking'), 'static severity prefixes cannot confirm a leak');
  const observations = [{ t: 0, traced_bytes: 10, traced_growth_bytes: 0, transient_bytes: 0,
    rss_bytes: 50, rss_change_bytes: -4, bookkeeping_bytes: 3 }];
  assert(m.parseProfile(JSON.stringify({ ...p, memory_observations: observations })), 'RSS releases can be negative process changes');
  assert.strictEqual(m.parseProfile(JSON.stringify({ ...p, memory_observations: [{ ...observations[0], traced_bytes: '10' }] })), undefined);
  assert.strictEqual(m.parseProfile(JSON.stringify({ ...p, memory_snapshot_at_exit: true, memory_tracing_lost_s: 1 })), undefined,
    'contradictory exit evidence is rejected');
  const gc = { supported: true, events: [{ t: 0.1, duration_s: 0.002, generation: 0, collected: 4, uncollectable: 0, thread: 12 }],
    dropped_events: 0, total_collections: 1, total_pause_s: 0.002, collected: 4, uncollectable: 0 };
  assert(m.parseProfile(JSON.stringify({ ...p, gc })), 'GC callback measurements parse');
  assert.strictEqual(m.parseProfile(JSON.stringify({ ...p, gc: { ...gc, events: [{ ...gc.events[0], duration_s: -1 }] } })), undefined,
    'negative GC pauses cannot become a measured zero');
}
console.log('PASS profile evidence contracts: residual validation, unavailable observations, recent retention and GC');
