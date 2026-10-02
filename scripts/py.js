#!/usr/bin/env node
/**
 * Cross-platform Python launcher for npm scripts: `node scripts/py.js <args...>`.
 * Uses $PMG_PYTHON if set, else the first of python3 / python / py -3 that runs.
 */
const { spawnSync } = require("child_process");
const candidates = process.env.PMG_PYTHON
  ? [[process.env.PMG_PYTHON]]
  : [["python3"], ["python"], ["py", "-3"]];
for (const [cmd, ...pre] of candidates) {
  const ok = spawnSync(cmd, [...pre, "-c", "import sys; sys.exit(sys.version_info < (3, 9))"], { stdio: "ignore" });
  if (ok.status === 0) {
    const r = spawnSync(cmd, [...pre, ...process.argv.slice(2)], { stdio: "inherit" });
    process.exit(r.status ?? 1);
  }
}
console.error("No Python 3.9+ found. Install it or set PMG_PYTHON to its path.");
process.exit(1);
