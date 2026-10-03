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
console.log("ALL MODEL TESTS PASSED");
