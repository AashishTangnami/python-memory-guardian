// Keystroke cost: edits must only do work when they change a profiled file's freshness.
const assert = require('assert');
const Module = require('module');
const originalLoad = Module._load;

const disposable = () => ({ dispose() {} });
const uris = new Map();
const uri = path => { const u = { scheme: 'file', fsPath: path, toString: () => `file://${path}` }; uris.set(u.toString(), u); return u; };
const handlers = {};
const sets = [];
let refreshes = 0, hashes = 0;

const original = 'def work():\n    return 1\n';
const doc = (path, text, languageId = 'python') => ({ uri: uri(path), languageId, version: 1, text,
  getText() { if (languageId === 'python') hashes++; return this.text; } });
const work = doc('/tmp/work.py', original);
const other = doc('/tmp/other.py', 'x = 1\n');

const vscode = {
  StatusBarAlignment: { Left: 1 },
  ThemeColor: class {},
  DiagnosticSeverity: { Error: 0, Warning: 1, Information: 2, Hint: 3 },
  Diagnostic: class { constructor(range, message, severity) { Object.assign(this, { range, message, severity }); } },
  Uri: { parse: key => uris.get(key) },
  languages: { createDiagnosticCollection: () => ({ clear() {}, delete() {}, set() {}, dispose() {} }) },
  window: {
    activeTextEditor: undefined, visibleTextEditors: [],
    createStatusBarItem: () => ({ show() {}, hide() {}, dispose() {} }),
    createTextEditorDecorationType: disposable,
    onDidChangeVisibleTextEditors: disposable,
    showWarningMessage: assert.fail,
  },
  workspace: {
    textDocuments: [work, other],
    createFileSystemWatcher: () => ({ ...disposable(), onDidCreate: disposable, onDidChange: disposable, onDidDelete: disposable }),
    onDidChangeTextDocument: h => { handlers.change = h; return disposable(); },
    onDidCloseTextDocument: h => { handlers.close = h; return disposable(); },
    findFiles: async () => [],
    getConfiguration: () => ({ get: (_key, fallback) => fallback }),
  },
  commands: { registerCommand: disposable },
};
class GuardianReport { update() {} show() {} refresh() { refreshes++; } dispose() {} }
Module._load = function(name, ...args) {
  if (name === 'vscode') return vscode;
  if (name === './reportView') return { GuardianReport };
  return originalLoad.call(this, name, ...args);
};

try {
  const { textHash } = require('../out/profileModel');
  const { ProfileView } = require('../out/profileView');
  const collection = { set: (u, d) => sets.push([u.toString(), d]) };
  const view = new ProfileView({}, () => collection, () => 'python3');
  const edit = (d, text) => { if (text != null) { d.text = text; d.version++; } handlers.change({ document: d }); };
  const staticFinding = () => ({ range: { start: { line: 0 } }, message: 'static', severity: 1, code: 'x' });

  // The language server publishes findings for both files before any profile exists.
  view.adjust(work.uri, [staticFinding()]);
  view.adjust(other.uri, [staticFinding()]);
  edit(other, 'x = 2\n');
  assert.strictEqual(sets.length + refreshes + hashes, 0, 'no profile: keystrokes do no profile work');

  // Load a profile that covers work.py only (load() is private; the saved-report path uses it).
  const entry = { time_s: 1, share: 0.5, python_s: 1, native_s: 0, system_s: 0, cpu_unsplit_s: 0,
    samples: 10, rss_growth_mb: 0, rss_release_mb: 0 };
  const json = doc('/tmp/profile.json', JSON.stringify({ schema: 3, script: work.uri.fsPath, python: '3.13',
    gil_split: true, wall_s: 2, memory_mode: 'fast', peak_traced_mb: null, rss_peak_mb: 1,
    files: { [work.uri.fsPath]: { 1: entry } }, functions: {}, file_hashes: { [work.uri.fsPath]: textHash(original) } }), 'json');
  vscode.workspace.textDocuments.push(json);
  view['load'](json.uri, true);
  const loaded = new Map(sets.map(([k, d]) => [k, d]));
  assert(loaded.get(work.uri.toString())[0].message.startsWith('🔥'), 'fresh hot line gets runtime evidence');
  assert.strictEqual(hashes, 1, 'load hashes the profiled file once; the unprofiled file is not hashed');
  sets.length = 0; refreshes = 0;

  edit(other, 'x = 3\n');
  assert.strictEqual(sets.length + refreshes, 0, 'unprofiled file: no diagnostics or report work');
  assert.strictEqual(hashes, 1, 'unprofiled file: no hashing');

  edit(work, original + '# edited\n');
  assert.deepStrictEqual(sets.map(([k]) => k), [work.uri.toString()], 'fresh -> stale re-adjusts only the edited file');
  assert.strictEqual(sets[0][1][0].message, 'static', 'stale file falls back to the raw finding');
  assert.strictEqual(refreshes, 1, 'the freshness change refreshes the report once');
  assert.strictEqual(hashes, 2, 'one hash for the new version');

  for (let i = 0; i < 5; i++) edit(work, original + `# edited ${i}\n`);
  assert.strictEqual(sets.length, 1, 'typing in an already-stale file republishes nothing');
  assert.strictEqual(refreshes, 1, 'typing in an already-stale file does not refresh the report');
  assert.strictEqual(hashes, 7, 'exactly one hash per document version');

  edit(work, original);
  assert.strictEqual(refreshes, 2, 'undo back to the profiled text restores freshness');
  assert(sets.at(-1)[1][0].message.startsWith('🔥'), 'fresh again: evidence returns');

  // Servers publish [] on close: stored findings for that file are dropped.
  view.adjust(other.uri, []);
  sets.length = 0;
  view.settingsChanged();
  assert.deepStrictEqual(sets.map(([k]) => k), [work.uri.toString()], 'full refresh covers only files with findings');
  handlers.close(work);
  sets.length = 0;
  view.settingsChanged();
  assert.strictEqual(sets.length, 0, 'closing a document forgets its stored findings and cached hash');
  console.log('PASS keystrokes only re-adjust, refresh and hash when a profiled file changes freshness');
} catch (e) {
  console.error(e); process.exitCode = 1;
} finally {
  Module._load = originalLoad;
}
