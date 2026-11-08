/**
 * Container staging into <project>/.pmg: stale profiler files go, the user's files stay, a symbolic
 * link in place of a staged file or the package is replaced, never followed, and a linked .pmg is refused.
 */
const assert = require('assert'), fs = require('fs'), os = require('os'), path = require('path');
const { checkRealFolder, writeFileAtomic } = require('../../out/container/pmgFolder');
const { stageHelper, stageProfiler, PYTHON_DIR } = require('../../out/container/staging');

const repo = path.join(__dirname, '..', '..');
const extension = { asAbsolutePath: (p) => path.join(repo, p) };
const modules = fs.readdirSync(path.join(repo, PYTHON_DIR, 'pmg_profiler')).filter((n) => n.endsWith('.py')).sort();
const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'pmg-stage-'));
const write = (file, text = 'stale') => { fs.mkdirSync(path.dirname(file), { recursive: true }); fs.writeFileSync(file, text); };

try {
  // Part 1: an earlier version's flat helpers and a removed module are cleaned up; user files stay.
  const project = path.join(tmp, 'project'), pmg = path.join(project, '.pmg');
  const kept = ['profile.json', 'summary.json', 'baselines/before.json', 'notes.txt', 'pmg_profiler/README.txt'];
  for (const f of ['pmg_guard.py', 'pmg_allocations.py', 'pmg_profiler/gone.py', 'pmg_profiler/session.py', ...kept]) write(path.join(pmg, f));
  const staged = stageProfiler(extension, project);
  assert.strictEqual(staged, path.join(pmg, 'pmg_profile.py'));
  for (const f of ['pmg_guard.py', 'pmg_allocations.py', 'pmg_profiler/gone.py']) assert(!fs.existsSync(path.join(pmg, f)), `stale ${f} removed`);
  for (const f of kept) assert.strictEqual(fs.readFileSync(path.join(pmg, f), 'utf8'), 'stale', `${f} kept`);
  assert.deepStrictEqual(fs.readdirSync(path.join(pmg, 'pmg_profiler')).filter((n) => n.endsWith('.py')).sort(), modules);
  assert(fs.readFileSync(path.join(pmg, 'pmg_profiler', 'session.py'), 'utf8').includes('class Profiler'), 'stale module replaced');
  assert(fs.existsSync(path.join(pmg, 'pmg_diagnostics.py')), 'diagnostics helper staged');
  console.log('PASS staging removes old helpers and stale modules, keeps profile, summary, baselines and other files');

  // Part 2: a symbolic link in place of the package folder, and one in place of a module.
  const outside = path.join(tmp, 'outside');
  write(path.join(outside, 'gone.py'), 'keep me');
  write(path.join(outside, 'session.py'), 'keep me');
  const linked = path.join(tmp, 'linked'), linkedPmg = path.join(linked, '.pmg');
  fs.mkdirSync(linkedPmg, { recursive: true });
  fs.symlinkSync(outside, path.join(linkedPmg, 'pmg_profiler'), 'dir');
  stageProfiler(extension, linked);
  assert(!fs.lstatSync(path.join(linkedPmg, 'pmg_profiler')).isSymbolicLink(), 'the link is replaced by a real folder');
  for (const f of ['gone.py', 'session.py']) assert.strictEqual(fs.readFileSync(path.join(outside, f), 'utf8'), 'keep me', `link target ${f} untouched`);
  const moduleLink = path.join(linkedPmg, 'pmg_profiler', 'session.py');
  fs.rmSync(moduleLink);
  fs.symlinkSync(path.join(outside, 'session.py'), moduleLink);
  stageProfiler(extension, linked);
  assert(!fs.lstatSync(moduleLink).isSymbolicLink(), 'a linked module is replaced by a copy');
  assert.strictEqual(fs.readFileSync(path.join(outside, 'session.py'), 'utf8'), 'keep me', 'its target is not overwritten');
  console.log('PASS staging replaces symbolic links in the staged package without following them');

  // Part 3: a symbolic link in place of a top-level helper, and a linked .pmg folder.
  const helperLink = path.join(linkedPmg, 'pmg_diagnostics.py');
  write(path.join(outside, 'mine.py'), 'USER DATA');
  fs.rmSync(helperLink);
  fs.symlinkSync(path.join(outside, 'mine.py'), helperLink);
  stageProfiler(extension, linked);
  assert(!fs.lstatSync(helperLink).isSymbolicLink(), 'a linked helper is replaced by a copy');
  assert.strictEqual(fs.readFileSync(path.join(outside, 'mine.py'), 'utf8'), 'USER DATA', 'its target is not overwritten');
  const elsewhere = path.join(tmp, 'elsewhere'), redirected = path.join(tmp, 'redirected');
  fs.mkdirSync(elsewhere);
  fs.mkdirSync(redirected);
  fs.symlinkSync(elsewhere, path.join(redirected, '.pmg'), 'dir');
  assert.throws(() => stageProfiler(extension, redirected), /symbolic link or a file/, 'profiler staging refuses a linked .pmg');
  assert.throws(() => stageHelper(extension, redirected, 'probe.py'), /symbolic link or a file/, 'probe staging refuses a linked .pmg');
  assert.deepStrictEqual(fs.readdirSync(elsewhere), [], 'nothing is written through the linked .pmg');
  console.log('PASS staging replaces a linked helper and refuses a linked .pmg folder');

  // Part 4: a hard link or a pipe in place of a helper. A hard link shares the file, and reading a pipe
  // blocks the editor, so neither may be read or written through.
  const plain = path.join(tmp, 'plain'), plainPmg = path.join(plain, '.pmg');
  fs.mkdirSync(plainPmg, { recursive: true });
  write(path.join(outside, 'shared.py'), 'USER DATA');
  fs.linkSync(path.join(outside, 'shared.py'), path.join(plainPmg, 'pmg_diagnostics.py'));
  stageProfiler(extension, plain);
  assert.strictEqual(fs.readFileSync(path.join(outside, 'shared.py'), 'utf8'), 'USER DATA', 'a hard-linked file is not overwritten');
  assert.strictEqual(fs.lstatSync(path.join(plainPmg, 'pmg_diagnostics.py')).nlink, 1, 'the helper is a file of its own');
  if (process.platform !== 'win32') {
    const fifo = path.join(plainPmg, 'probe.py');
    require('child_process').execFileSync('mkfifo', [fifo]);
    // In a child process with a timeout: a broken guard blocks on the pipe, which must fail this test, not hang it.
    const child = `const s = require(${JSON.stringify(require.resolve('../../out/container/staging'))});
      s.stageHelper({ asAbsolutePath: (p) => require('path').join(${JSON.stringify(repo)}, p) }, ${JSON.stringify(plain)}, 'probe.py');`;
    require('child_process').execFileSync(process.execPath, ['-e', child], { timeout: 10000, stdio: 'pipe' });
    assert(fs.lstatSync(fifo).isFile(), 'the pipe is replaced by the helper');
  }
  console.log('PASS staging never reads or writes through a hard link or a pipe');

  // Part 5: the extension's own writes into .pmg (summary.json, baselines).
  const target = path.join(plainPmg, 'summary.json');
  fs.symlinkSync(path.join(outside, 'mine.py'), target);
  writeFileAtomic(target, '{}');
  assert.strictEqual(fs.readFileSync(path.join(outside, 'mine.py'), 'utf8'), 'USER DATA', 'a link at the target is replaced, not written through');
  assert.strictEqual(fs.readFileSync(target, 'utf8'), '{}');
  assert.deepStrictEqual(fs.readdirSync(plainPmg).filter((n) => n.endsWith('.tmp')), [], 'no temporary file is left');
  assert.throws(() => checkRealFolder(path.join(redirected, '.pmg')), /symbolic link or a file/, 'a linked .pmg is refused for writes');
  checkRealFolder(path.join(plain, 'missing'));                // a missing folder is fine: it is created later
  console.log('PASS writes into .pmg replace links instead of following them');
} finally {
  fs.rmSync(tmp, { recursive: true, force: true });
}
