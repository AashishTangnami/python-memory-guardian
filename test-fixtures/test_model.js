const fs = require("fs"), assert = require("assert");
const m = require("../out/profileModel");
const WL = require("path").join(__dirname, "profiler", "workload.py");
const src = fs.readFileSync(WL, "utf8");
const th = { hotShare: 0.05, hotMb: 50 };
for (const mode of ["fast", "precise"]) {
  const p = m.parseProfile(fs.readFileSync(require("path").join(__dirname, "profiler", "out", `workload_${mode}.json`), "utf8"));
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
{ const p = m.parseProfile(fs.readFileSync(require("path").join(__dirname, "profiler/out/workload_fast.json"), "utf8"));
  const file = Object.keys(p.files)[0];
  p.files[file][Object.keys(p.files[file])[0]].line_events = -1;
  assert.strictEqual(m.parseProfile(JSON.stringify(p)), undefined, "negative line-event count is rejected"); }
{ const p = m.parseProfile(fs.readFileSync(require("path").join(__dirname, "profiler/out/workload_fast.json"), "utf8"));
  const lo = { objects: [{ holder: "global BIG", type: "bytearray", mb: 20, items: null, estimated: false }], complete: true };
  assert(m.parseProfile(JSON.stringify({ ...p, largest_objects: lo })), "largest objects are accepted");
  assert.strictEqual(m.parseProfile(JSON.stringify({ ...p, largest_objects: { ...lo, objects: [{ ...lo.objects[0], mb: -1 }] } })), undefined, "negative size rejected");
  assert.strictEqual(m.parseProfile(JSON.stringify({ ...p, largest_objects: { objects: [], complete: "yes" } })), undefined, "complete must be boolean"); }
// Native estimate: process growth beyond traced Python growth, for any library, in precise mode.
{ const pp = { memory_mode: "precise", gil_split: true };
  const line = (rss, alloc) => ({ time_s: 0, share: 0, python_s: 0, native_s: 0, system_s: 0, cpu_unsplit_s: 0, samples: 1,
    rss_growth_mb: rss, rss_release_mb: 0, alloc_mb: alloc, peak_mb: 0, transient_peak_mb: 0 });
  assert.strictEqual(m.nativeMb(line(127, 0)), 127);
  assert.strictEqual(m.nativeMb(line(10, 13)), 0, "never negative");
  assert(m.lineLabel(line(127, 0), pp).includes("native ≈ +127 MB"), m.lineLabel(line(127, 0), pp));
  assert(!m.lineLabel(line(0.5, 0), pp).includes("native"), "under 1 MB: no native label");
  assert.strictEqual(m.heat(line(127, 0), "precise", { hotShare: 0.05, hotMb: 50 }), "hot", "native-heavy lines are memory-hot");
  assert(m.funcLabel({ name: "load", time_s: 1, python_s: 1, native_s: 0, system_s: 0, peak_mb: 0, transient_peak_mb: 0, alloc_mb: 2, rss_growth_mb: 60 }, pp)
    .includes("native ≈ +58.0 MB"));
  assert(!m.lineLabel(line(127, 0), { ...pp, memory_mode: "fast" }).includes("native"), "fast mode shows RSS, not the estimate"); }
{ const p = m.parseProfile(fs.readFileSync(require("path").join(__dirname, "profiler/out/workload_fast.json"), "utf8"));
  assert.deepStrictEqual(m.runNotes(p), [], "a complete run has no notes");
  const lost = m.parseProfile(JSON.stringify({ ...p, memory_tracing_lost_s: 1.25, sampler_error: "ValueError: boom" }));
  assert(lost, "tracing-lost and sampler-error fields are accepted");
  const [tracing, sampler] = m.runNotes(lost);
  assert(tracing.includes("1.25 s") && tracing.includes("leak detection was skipped"), tracing);
  assert(sampler.includes("ValueError: boom"), sampler);
  assert.strictEqual(m.parseProfile(JSON.stringify({ ...p, memory_tracing_lost_s: -1 })), undefined, "negative time is rejected");
  assert.strictEqual(m.parseProfile(JSON.stringify({ ...p, sampler_error: 3 })), undefined, "non-string error is rejected"); }
assert.strictEqual(m.normPath("C:\\Proj\\a.py", "win32"), m.normPath("c:/proj/A.py", "win32"));
const pp = m.parseProfile(fs.readFileSync(require("path").join(__dirname, "profiler/out/workload_precise.json"), "utf8"));
console.log("leak msg:", m.leakMessage(new m.ProfileIndex(pp).line(WL, 24)).slice(0, 120) + "…");
{ const p = m.parseProfile(fs.readFileSync(require("path").join(__dirname, "profiler/out/workload_fast.json"), "utf8")), idx = new m.ProfileIndex(p);
  const f = WL;
  assert.notStrictEqual(m.heat(idx.line(f, 24), p.memory_mode, th, idx.insideSampledFunction(f, 24)), "cold",
    "leak line inside a sampled function must never be demoted");
  assert.strictEqual(idx.insideSampledFunction(f, 3), false, "module-level gaps are outside functions"); }
{ const hp = m.parseProfile(fs.readFileSync(require("path").join(__dirname, "profiler/out/holders_workload_precise.json"), "utf8"));
  const hidx = new m.ProfileIndex(hp), hf = require("path").join(__dirname, "profiler", "holders_workload.py");
  const e13 = hidx.line(hf, 13), e17 = hidx.line(hf, 17);
  const msg = m.leakMessage(e13);
  console.log("leak diagnostic:", msg.slice(0, 190) + "…");
  console.log("line label L13:", m.lineLabel(e13, hp));
  console.log("line label L17:", m.lineLabel(e17, hp));
  assert(msg.startsWith("Service.handle() › `self.history.append` — "), "scope + subject prefix");
  assert(msg.includes("`Service.history` (list, 30 items)"), "holder named in message");
  assert(m.lineLabel(e13, hp).includes("held by Service.history"), "holder in inline label");
  assert(m.lineLabel(e17, hp).includes("→ scratch"), "assigned variable in inline label");
  // scratch: 5 MB allocated per call (~140 MB over the run) but at most ONE buffer alive at a
  // time (it lives during the sleep on the same line), freed by exit, never a leak.
  assert(m.lineLabel(e17, hp).includes("alloc "), "scratch: allocation shown");
  assert((e17.alloc_mb ?? 0) > 100, "scratch: ~140 MB allocated over the run");
  assert((e17.peak_mb ?? 0) <= 5.1, "scratch: at most one 5 MB buffer held at the peak");
  assert.strictEqual(e17.end_mb ?? 0, 0, "scratch: nothing held at exit");
  assert(!e17.leak_runs, "scratch: not a leak");
  assert(m.lineLabel(e13, hp).includes("held "), "leak line shows held memory");
  const fn = Object.values(hidx.functions.get(require("./../out/profileModel").normPath(hf)));
  console.log("function label:", m.funcLabel(fn.find(f => f.name === "Service.handle"), hp)); }
{ const base = { memory_mode: "precise", frames: 2 };
  assert(m.unattributedNote({ ...base, unattributed_peak_mb: 31.7 }).includes("31.7 MB"), "note shows MB");
  assert(m.unattributedNote({ ...base, unattributed_peak_mb: 31.7 }).includes("2-frame"), "note shows depth");
  assert.strictEqual(m.unattributedNote({ ...base, unattributed_peak_mb: 0.4 }), "", "no note under 1 MB");
  assert.strictEqual(m.unattributedNote({ memory_mode: "fast", unattributed_peak_mb: 50 }), "", "precise only"); }
{ // Native estimate: tracemalloc's bookkeeping (profiler_mb) is subtracted, and anything within twice it reads 0.
  assert.strictEqual(m.nativeMb({ rss_growth_mb: 100, alloc_mb: 40 }), 60, "older profiles: plain difference");
  assert.strictEqual(m.nativeMb({ rss_growth_mb: 100, alloc_mb: 40, profiler_mb: 50 }), 0, "10 MB left is within tracing overhead");
  assert.strictEqual(m.nativeMb({ rss_growth_mb: 400, alloc_mb: 40, profiler_mb: 50 }), 310, "native buffers clear the margin");
  const base = JSON.parse(fs.readFileSync(require("path").join(__dirname, "profiler/out/workload_precise.json"), "utf8"));
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
