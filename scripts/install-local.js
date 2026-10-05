#!/usr/bin/env node
/** Install dependencies, build the VSIX, and install it into local VS Code. */
const { spawnSync } = require('node:child_process');
const { existsSync, readFileSync } = require('node:fs');
const path = require('node:path');

const root = path.resolve(__dirname, '..');
const manifest = JSON.parse(readFileSync(path.join(root, 'package.json'), 'utf8'));
const vsix = path.join(root, `${manifest.name}-${manifest.version}.vsix`);
const npm = 'npm';
const code = process.env.PMG_CODE_CLI || 'code';
const args = process.argv.slice(2);
const dryRun = args.includes('--dry-run');

if (args.some(arg => arg !== '--dry-run')) {
  console.error('Usage: node scripts/install-local.js [--dry-run]');
  process.exit(2);
}
if (process.platform === 'win32') {
  console.error('This helper supports macOS and Linux. On Windows, use the manual steps in docs/05-build-and-release.md.');
  process.exit(1);
}

function run(command, args) {
  console.log(`\n> ${[command, ...args].join(' ')}`);
  if (dryRun) return;
  const result = spawnSync(command, args, { cwd: root, stdio: 'inherit' });
  if (result.error) {
    console.error(`Could not run ${command}: ${result.error.message}`);
    process.exit(1);
  }
  if (result.status !== 0) process.exit(result.status || 1);
}

run(npm, ['ci']);
// vsce runs vscode:prepublish, which vendors Python dependencies and compiles.
run(npm, ['run', 'package', '--', '--out', path.basename(vsix)]);
if (!dryRun && !existsSync(vsix)) {
  console.error(`Packaging succeeded but the VSIX was not found: ${vsix}`);
  process.exit(1);
}
run(code, ['--install-extension', vsix, '--force']);

console.log(dryRun ? '\nDry run complete; nothing was installed.' :
  `\nInstalled ${path.basename(vsix)}. Run “Developer: Reload Window” in VS Code.`);
