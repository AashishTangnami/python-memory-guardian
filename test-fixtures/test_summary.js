// Profile summary (pmg-summary/1): validates against docs/pmg-summary.schema.json, every number traces to the
// profile, and every section states its method and limits. Uses profiles written earlier in the test chain.
const assert = require('assert');
const fs = require('fs');
const path = require('path');
const m = require('../out/profileModel');
const c = require('../out/compareModel');
const s = require('../out/summaryModel');
const schema = JSON.parse(fs.readFileSync(path.join(__dirname, '..', 'docs', 'pmg-summary.schema.json'), 'utf8'));

/** The JSON Schema keywords docs/pmg-summary.schema.json uses; an unknown keyword fails, so the test cannot pass vacuously. */
const KNOWN = new Set(['$schema', '$id', 'title', 'description', 'type', 'required', 'properties', 'additionalProperties',
  'items', 'enum', 'const', 'anyOf', 'allOf', '$ref', 'minimum', '$defs']);
function validate(value, sch, at = '$') {
  const errors = [];
  for (const k of Object.keys(sch)) if (!KNOWN.has(k)) throw new Error(`validator does not support ${k} at ${at}`);
  if (sch.$ref) return validate(value, sch.$ref.slice(2).split('/').reduce((o, k) => o[k], schema), at);
  const kind = v => v === null ? 'null' : Array.isArray(v) ? 'array' : Number.isInteger(v) ? 'integer' : typeof v;
  if (sch.type) {
    const types = [].concat(sch.type), k = kind(value);
    if (!types.includes(k) && !(k === 'integer' && types.includes('number'))) return [`${at}: ${k} is not ${types}`];
  }
  if ('const' in sch && value !== sch.const) errors.push(`${at}: not ${sch.const}`);
  if (sch.enum && !sch.enum.includes(value)) errors.push(`${at}: ${value} not in ${sch.enum}`);
  if (sch.minimum != null && value < sch.minimum) errors.push(`${at}: below ${sch.minimum}`);
  if (sch.anyOf && sch.anyOf.every(x => validate(value, x, at).length)) errors.push(`${at}: matches no anyOf branch`);
  for (const x of sch.allOf ?? []) errors.push(...validate(value, x, at));
  if (kind(value) === 'object') {
    for (const r of sch.required ?? []) if (!(r in value)) errors.push(`${at}: missing ${r}`);
    for (const [k, v] of Object.entries(value)) {
      if (sch.properties?.[k]) errors.push(...validate(v, sch.properties[k], `${at}.${k}`));
      else if (sch.additionalProperties === false) errors.push(`${at}: unexpected ${k}`);
      else if (typeof sch.additionalProperties === 'object') errors.push(...validate(v, sch.additionalProperties, `${at}.${k}`));
    }
  }
  if (kind(value) === 'array' && sch.items) value.forEach((v, i) => errors.push(...validate(v, sch.items, `${at}[${i}]`)));
  return errors;
}

const load = f => m.parseProfile(fs.readFileSync(path.join(__dirname, 'profiler', 'out', f), 'utf8'));
const build = (p, extra = {}) => s.buildSummary({ profile: p, profilePath: path.join(__dirname, 'profiler', '.pmg', 'profile.json'),
  root: path.join(__dirname, 'profiler'), generatedAt: '2026-10-04T12:00:00Z', generator: 'python-memory-guardian test',
  freshness: Object.fromEntries(Object.keys(p.files).map(f => [f, 'fresh'])), staticDiagnostics: {},
  thresholds: { hotShare: 0.05, hotMb: 50 }, comparison: null, ...extra });

/** Every section with evidence carries a method and a limits array. */
function assertMethods(sum) {
  for (const [k, v] of Object.entries(sum.totals)) assert(v.method && Array.isArray(v.limits), `totals.${k} has method and limits`);
  for (const k of ['memory_stacks', 'retention', 'largest_objects', 'static_diagnostics']) {
    if (sum[k]) assert(sum[k].method && Array.isArray(sum[k].limits), `${k} has method and limits`);
  }
  assert(sum.methods.time.method && sum.methods.time.limits.length, 'time method and limits');
  assert(sum.methods.time.limits.some(l => l.startsWith('sampled')), 'sampled time says so');
}

for (const f of ['workload_precise.json', 'workload_fast.json', 'holders_workload_precise.json']) {
  const p = load(f), sum = build(p);
  assert.deepStrictEqual(validate(JSON.parse(JSON.stringify(sum)), schema), [], `${f}: summary matches the schema`);
  assertMethods(sum);
  assert.strictEqual(sum.run.memory_mode, p.memory_mode);
  assert.strictEqual(sum.totals.run_duration_s.value, p.wall_s, 'duration is the profile value');
  // Every function and line value is the profile's own.
  const files = Object.keys(p.files), byRel = rel => files.find(x => x.endsWith(rel));
  for (const fn of sum.functions) {
    const src = p.functions[byRel(fn.file)][String(fn.line)];
    assert.strictEqual(fn.name, src.name);
    assert.strictEqual(fn.time_s, src.time_s, `${fn.name} time traces to the profile`);
    if (p.memory_mode === 'precise') assert.strictEqual(fn.allocated_mb, src.alloc_mb);
    if (p.memory_mode === 'fast') assert.strictEqual(fn.rss_growth_mb, src.rss_growth_mb);
  }
  for (const ln of sum.lines) {
    const e = p.files[byRel(ln.file)][String(ln.line)];
    assert.strictEqual(ln.time_s, e.time_s);
    assert.strictEqual(ln.samples, e.samples);
    if (ln.suspected_leak) assert(e.leak_runs > 0 && ln.held_at_exit_mb === e.end_mb);
  }
  assert(sum.functions.every(fn => !fn.file.startsWith('/')), 'paths are relative to the project folder');
  if (p.memory_mode === 'precise') {
    assert.strictEqual(sum.totals.peak_traced_mb.value, p.peak_traced_mb);
    const bytes = p.memory_stacks.exit.total_bytes;
    assert.strictEqual(sum.memory_stacks.exit.total_mb, Math.round(bytes / 1e3) / 1e3, 'exit table total traces to the profile');
    const sites = sum.memory_stacks.exit.by_function.reduce((t, x) => t + x.mb, 0);
    assert(sites <= sum.memory_stacks.exit.total_mb + 0.01, 'sites never exceed the snapshot total');
    assert(sum.retention.findings.length, `${f}: precise run has retention findings`);
  } else {
    assert.strictEqual(sum.memory_stacks, null);
    assert.strictEqual(sum.retention, null);
    assert(sum.next_steps.some(x => x.includes('precise mode')), 'fast mode suggests precise mode');
  }
  console.log(`PASS ${f}: ${JSON.stringify(sum).length} bytes, ${sum.functions.length} functions, ${sum.lines.length} lines`);
}

// The holders workload leaks: the summary says so, with holder and next step, and the editor's verdict.
{ const p = load('holders_workload_precise.json'), sum = build(p);
  const growing = sum.retention.findings.filter(r => r.status === 'growing');
  assert(growing.some(r => r.holders.some(h => h.startsWith('Service.history'))), 'growing finding names its holder');
  assert(sum.lines.some(l => l.suspected_leak), 'leaking line flagged');
  assert(sum.next_steps.some(x => x.startsWith('Verify suspected growing retention')), 'next step for growing retention'); }

// Static findings: included only for analyzed files, with this run's heat; unanalyzed files are named.
{ const p = load('workload_precise.json'), file = Object.keys(p.files)[0];
  const hot = Object.entries(p.files[file]).sort((a, b) => b[1].time_s - a[1].time_s)[0][0];
  const sum = build(p, { staticDiagnostics: { [file]: [{ line: Number(hot), code: 'heap-inflation.x', severity: 'warning', message: 'm' }] } });
  assert.strictEqual(sum.static_diagnostics.findings[0].measured, 'hot');
  assert(sum.next_steps.some(x => x.includes('measured hot line')));
  const stale = build(p, { freshness: { [file]: 'stale' }, staticDiagnostics: { [file]: [{ line: 1, code: 'x', severity: 'hint', message: 'm' }] } });
  assert.strictEqual(stale.static_diagnostics.findings[0].measured, 'source changed', 'no heat claims for changed source');
  assert(stale.next_steps[0].startsWith('Re-run profiling'), 'stale source: re-run first');
  const none = build(p);
  assert(none.static_diagnostics.limits.some(l => l.startsWith('not analyzed')), 'files without static analysis are named'); }

// Comparison: present when a baseline is selected, with the summary's field names, and valid.
{ const p = load('workload_precise.json');
  const result = c.compareProfiles(p, p);
  const sum = build(p, { comparison: { label: 'before', result } });
  assert.deepStrictEqual(validate(JSON.parse(JSON.stringify(sum)), schema), []);
  assert.strictEqual(sum.comparison.baseline, 'before');
  assert.deepStrictEqual(sum.comparison.functions, [], 'a run against itself has no significant changes');
  assert(sum.comparison.run.every(r => r.verdict === 'same'));
  assert(!sum.next_steps.some(x => x.includes('save this run as a baseline')), 'no baseline advice when comparing'); }

// The validator rejects what the schema forbids.
assert(validate({ ...build(load('workload_fast.json')), extra: 1 }, schema).some(e => e.includes('unexpected extra')));
assert(validate({ ...build(load('workload_fast.json')), format: 'pmg-summary/2' }, schema).length);
console.log('PASS profile summary: schema, traceability to the profile, methods and limits, static findings, comparison');
