const assert = require('assert');
const Module = require('module');
const originalLoad = Module._load;

const commands = new Map();
const warnings = [];
const shown = [];
const disposable = () => ({ dispose() {} });
const uri = path => ({ scheme: 'file', fsPath: path, toString: () => `file://${path}` });
const selected = uri('/tmp/guardian-profile.json');
const other = uri('/tmp/other.json');
const profile = JSON.stringify({ schema: 3, script: '/tmp/work.py', python: '3.13', gil_split: true,
  wall_s: 1, memory_mode: 'precise', peak_traced_mb: 1, rss_peak_mb: 2,
  files: {}, functions: {}, file_hashes: {} });
const documents = [
  { uri: other, languageId: 'json', getText: () => '{"not": "a profile"}' },
  { uri: selected, languageId: 'json', getText: () => profile },
];
const vscode = {
  StatusBarAlignment: { Left: 1 },
  ThemeColor: class {},
  languages: { createDiagnosticCollection: () => ({ clear() {}, dispose() {} }) },
  window: {
    activeTextEditor: { document: documents[0] }, visibleTextEditors: [],
    createStatusBarItem: () => ({ show() {}, hide() {}, dispose() {} }),
    createTextEditorDecorationType: disposable,
    onDidChangeVisibleTextEditors: disposable,
    showWarningMessage: text => warnings.push(text),
  },
  workspace: {
    textDocuments: documents,
    createFileSystemWatcher: () => ({ ...disposable(), onDidCreate: disposable,
      onDidChange: disposable, onDidDelete: disposable }),
    onDidChangeTextDocument: disposable,
    findFiles: async () => [],
    getConfiguration: () => ({ get: (_key, fallback) => fallback }),
  },
  commands: { registerCommand: (name, handler) => { commands.set(name, handler); return disposable(); } },
};
class GuardianReport {
  update(index) { this.index = index; }
  show() { shown.push(this.index?.profile.script); }
  dispose() {}
}
Module._load = function(name, ...args) {
  if (name === 'vscode') return vscode;
  if (name === './reportView') return { GuardianReport };
  return originalLoad.call(this, name, ...args);
};

try {
  const { ProfileView } = require('../out/profileView');
  const view = new ProfileView({}, () => undefined, () => 'python3');
  const manifest = require('../package.json');
  assert(manifest.contributes.menus['editor/title'].some(item =>
    item.command === 'pythonMemoryGuardian.visualizeReport' && item.when.includes('resourceExtname == .json')));
  const visualize = commands.get('pythonMemoryGuardian.visualizeReport');
  assert(visualize, 'the editor action is registered');
  visualize(selected);
  assert.deepStrictEqual(shown, ['/tmp/work.py'], 'the clicked JSON URI wins over the active editor');
  visualize(other);
  assert.strictEqual(shown.length, 1, 'invalid JSON does not replace or reopen the report');
  assert(warnings.at(-1).includes('unreadable profile JSON'));
  view.dispose();
  console.log('PASS JSON editor action opens valid profile and rejects unrelated JSON');
} finally {
  Module._load = originalLoad;
}
