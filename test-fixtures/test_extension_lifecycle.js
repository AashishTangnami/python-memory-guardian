const assert = require('assert');
const Module = require('module'), original = Module._load;
const commands = {}, probes = [], active = new Set();
let nextId = 0, onConfig, settingsChanged = 0;
// Mirrors ConfigurationChangeEvent: a section is affected when it is the changed key or one of its parents.
const changed = key => ({ affectsConfiguration: section => key === section || key.startsWith(section + '.') });
const vscode = {
  workspace: { getConfiguration: () => ({ get: (_key, fallback) => fallback }),
    onDidChangeConfiguration: handler => { onConfig = handler; return { dispose() {} }; } },
  window: { createOutputChannel: () => ({ appendLine() {}, dispose() {} }), showErrorMessage: assert.fail },
  commands: { registerCommand: (key, fn) => { commands[key] = fn; return { dispose() {} }; } },
};
class Client {
  constructor() { this.id = nextId++; }
  async start() { active.add(this.id); assert(active.size <= 1, 'only one language client may run'); }
  async stop() { active.delete(this.id); }
}
Module._load = function(name, ...args) {
  if (name === 'vscode') return vscode;
  if (name === 'vscode-languageclient/node') return { LanguageClient: Client, TransportKind: { stdio: 0 } };
  if (name === './profileView') return { ProfileView: class { settingsChanged() { settingsChanged++; } } };
  if (name === 'child_process') return { execFile: (_command, _args, _options, callback) => probes.push(callback) };
  return original.call(this, name, ...args);
};
const flush = () => new Promise(resolve => setImmediate(resolve));
(async () => {
  const extension = require('../out/extension');
  const activation = extension.activate({ subscriptions: [], asAbsolutePath: p => '/extension/' + p });
  await flush(); probes.shift()(null, '{}'); await activation;
  assert.strictEqual(active.size, 1);
  const a = commands['pythonMemoryGuardian.restart']();
  const b = commands['pythonMemoryGuardian.restart']();
  await flush(); assert.strictEqual(probes.length, 1);
  probes.shift()(null, '{}'); await flush(); assert.strictEqual(probes.length, 1);
  probes.shift()(null, '{}'); await Promise.all([a, b]);

  for (const key of ['pythonMemoryGuardian.profile.hotShare', 'pythonMemoryGuardian.trace.server']) {
    onConfig(changed(key)); await flush();
    assert.strictEqual(probes.length, 0, `${key} must not restart the server`);
  }
  assert.strictEqual(settingsChanged, 1, 'profile settings re-render the profile view');
  onConfig(changed('pythonMemoryGuardian.interpreter')); await flush();
  assert.strictEqual(probes.length, 1, 'an interpreter change re-probes and restarts');
  probes.shift()(null, '{}'); await flush();
  console.log('PASS client-only settings re-render without restarting the server');
  const restart = commands['pythonMemoryGuardian.restart']();
  await flush();
  const shutdown = extension.deactivate();
  probes.shift()(null, '{}');
  await Promise.all([restart, shutdown]);
  assert.strictEqual(active.size, 0, 'deactivation must stop a client whose probe was still pending');
  console.log('PASS serialized restarts and shutdown during an interpreter probe');
})().catch(e => { console.error(e); process.exitCode = 1; }).finally(() => { Module._load = original; });
