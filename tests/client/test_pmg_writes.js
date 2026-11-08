/**
 * The extension's own writes into <project>/.pmg go through the safe helpers at every call site: a profile run,
 * a saved baseline and summary.json refuse a linked .pmg, and a link planted at a target name is replaced,
 * never written through. test_staging.js tests the helpers; this test tests that the callers use them.
 */
const assert = require('assert'), fs = require('fs'), os = require('os'), path = require('path');
const Module = require('module'), originalLoad = Module._load;

const errors = [], warnings = [], tasks = [];
let editor, answers = {};
const disposable = () => ({ dispose() {} });
const uri = (p) => ({ scheme: 'file', fsPath: p, toString: () => `file://${p}` });
let root;
const vscode = {
  Uri: { file: uri },
  StatusBarAlignment: { Left: 1 },
  ThemeColor: class {},
  TaskScope: { Workspace: 1 },
  TaskRevealKind: { Always: 1 },
  Task: class { constructor(...args) { this.args = args; } },
  ProcessExecution: class { constructor(command, args) { this.command = command; this.args = args; } },
  languages: { createDiagnosticCollection: () => ({ clear() {}, dispose() {}, get: () => [] }) },
  tasks: { executeTask: async (task) => { tasks.push(task); } },
  window: {
    get activeTextEditor() { return editor; }, visibleTextEditors: [],
    createStatusBarItem: () => ({ show() {}, hide() {}, dispose() {} }),
    createTextEditorDecorationType: disposable,
    onDidChangeVisibleTextEditors: disposable,
    showQuickPick: async (items) => items[0],
    showInputBox: async () => answers.input,
    showWarningMessage: async (text) => { warnings.push(text); return answers.warning; },
    showErrorMessage: async (text) => { errors.push(text); },
    showInformationMessage: async () => undefined,
  },
  workspace: {
    textDocuments: [],
    workspaceFolders: [],
    getWorkspaceFolder: () => ({ uri: uri(root) }),
    createFileSystemWatcher: () => ({ ...disposable(), onDidCreate: disposable, onDidChange: disposable, onDidDelete: disposable }),
    onDidChangeTextDocument: disposable,
    onDidCloseTextDocument: disposable,
    findFiles: async () => [],
    getConfiguration: () => ({ get: (_key, fallback) => fallback }),
  },
  commands: { registerCommand: disposable },
};
class GuardianReport {
  update() {} show() {} dispose() {} baselinesChanged() {}
  comparisonFor() { return null; }
}
Module._load = function(name, ...args) {
  if (name === 'vscode') return vscode;
  if (name === '../report/view') return { GuardianReport };
  return originalLoad.call(this, name, ...args);
};

const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'pmg-writes-'));
const victim = path.join(tmp, 'outside', 'victim.txt');
fs.mkdirSync(path.dirname(victim));
const resetVictim = () => fs.writeFileSync(victim, 'USER DATA');
const untouched = (what) => assert.strictEqual(fs.readFileSync(victim, 'utf8'), 'USER DATA', `${what}: the link target is not written`);
/** A project whose .pmg is a link to another folder. */
const linkedProject = (name) => {
  const project = path.join(tmp, name), elsewhere = path.join(tmp, `${name}-elsewhere`);
  fs.mkdirSync(project); fs.mkdirSync(elsewhere);
  fs.symlinkSync(elsewhere, path.join(project, '.pmg'), 'dir');
  return { project, elsewhere };
};

(async () => {
  const { runProfiler } = require('../../out/profiling/runner');
  const { saveBaseline } = require('../../out/profiling/baselines');
  const { ProfileView } = require('../../out/profiling/controller');
  const ctx = { asAbsolutePath: (p) => path.join(__dirname, '..', '..', p),
    workspaceState: { get: (_k, fallback) => fallback, update: async () => {} } };

  // 1. Profile Current File, host mode: a linked .pmg stops the run before the profiler writes anything.
  const runIn = async (project) => {
    root = project;
    const file = path.join(project, 'main.py');
    fs.writeFileSync(file, 'print(1)\n');
    editor = { document: { languageId: 'python', isUntitled: false, isDirty: false, uri: uri(file), getText: () => 'print(1)\n' },
      selection: { active: { line: 0 } } };
    answers = { input: '' };
    await runProfiler({ ctx, interpreter: () => 'python3', container: () => undefined, started: () => {} });
  };
  const linked = linkedProject('run-linked');
  await runIn(linked.project);
  assert.strictEqual(tasks.length, 0, 'no profiler task starts with a linked .pmg');
  assert(errors.at(-1)?.includes('symbolic link or a file'), errors.at(-1));
  const real = path.join(tmp, 'run-real');
  fs.mkdirSync(real);
  await runIn(real);
  assert.strictEqual(tasks.length, 1, 'a real (or missing) .pmg runs as before');
  console.log('PASS a profile run refuses a linked .pmg and runs with a real one');

  // 2. Save as baseline: a linked .pmg is refused, and a link planted at the baseline's name is replaced.
  const profile = { script: path.join(tmp, 'work.py') };
  const save = (dir) => saveBaseline({ text: '{"schema": 3}' }, dir, profile, () => {});
  const linkedBase = linkedProject('baseline-linked');
  root = linkedBase.project;
  answers = { input: 'b1', warning: 'Replace' };
  const errorsBefore = errors.length;
  await save(path.join(linkedBase.project, '.pmg', 'baselines'));
  assert.strictEqual(errors.length, errorsBefore + 1, 'the save reports an error');
  assert(errors.at(-1).includes('symbolic link or a file'), errors.at(-1));
  assert.deepStrictEqual(fs.readdirSync(linkedBase.elsewhere), [], 'nothing is written through the linked .pmg');
  const plain = path.join(tmp, 'baseline-plain'), dir = path.join(plain, '.pmg', 'baselines');
  fs.mkdirSync(dir, { recursive: true });
  root = plain;
  resetVictim();
  fs.symlinkSync(victim, path.join(dir, 'b1.json'));
  await save(dir);
  untouched('baseline');
  assert(!fs.lstatSync(path.join(dir, 'b1.json')).isSymbolicLink(), 'the planted link is replaced');
  assert.strictEqual(JSON.parse(fs.readFileSync(path.join(dir, 'b1.json'), 'utf8')).baseline.name, 'b1');
  console.log('PASS a baseline save refuses a linked .pmg and replaces a planted link');

  // 3. summary.json beside a run's .pmg/profile.json.
  const runProfile = JSON.stringify({ schema: 3, script: path.join(tmp, 'work.py'), python: '3.13', gil_split: true,
    wall_s: 1, memory_mode: 'fast', rss_peak_mb: 2, files: {}, functions: {}, file_hashes: {} });
  const summaryIn = (project) => {
    root = project;
    const profilePath = path.join(project, '.pmg', 'profile.json');
    fs.writeFileSync(profilePath, runProfile);
    const view = new ProfileView(ctx, () => undefined, () => 'python3');
    view.load(uri(profilePath));
    clearTimeout(view.summaryTimer);
    view.writeSummary();
    view.dispose();
  };
  const project = path.join(tmp, 'summary-plain');
  fs.mkdirSync(path.join(project, '.pmg'), { recursive: true });
  resetVictim();
  fs.symlinkSync(victim, path.join(project, '.pmg', 'summary.json'));
  summaryIn(project);
  untouched('summary');
  const written = path.join(project, '.pmg', 'summary.json');
  assert(!fs.lstatSync(written).isSymbolicLink(), 'the planted link is replaced');
  assert(JSON.parse(fs.readFileSync(written, 'utf8')).format, 'the summary is written');
  const linkedSummary = linkedProject('summary-linked');
  const warningsBefore = warnings.length;
  summaryIn(linkedSummary.project);
  assert(warnings.slice(warningsBefore).some((w) => w.includes('symbolic link or a file')), warnings.slice(warningsBefore).join('\n'));
  assert.deepStrictEqual(fs.readdirSync(linkedSummary.elsewhere).sort(), ['profile.json'], 'no summary is written through the linked .pmg');
  console.log('PASS summary.json refuses a linked .pmg and replaces a planted link');
})().catch((e) => { console.error(e); process.exitCode = 1; }).finally(() => {
  Module._load = originalLoad;
  fs.rmSync(tmp, { recursive: true, force: true });
});
