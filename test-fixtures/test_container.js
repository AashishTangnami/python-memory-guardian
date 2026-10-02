/**
 * Container-mode tests. Part 1: pure path rules. Part 2: an end-to-end run where the
 * "container" sees the project at a different path (a symlink alias) and commands go
 * through an exec prefix - the same shape as `docker compose exec -T app python3 ...`.
 * Real Docker is not required (and not exercised) by this test.
 */
const assert = require("assert"), fs = require("fs"), os = require("os"), path = require("path");
const { spawnSync } = require("child_process");
const c = require("../out/containerPaths");
const m = require("../out/profileModel");

// ---- Part 1: path rules -------------------------------------------------------------
const win = c.resolveMappings([{ local: "${workspaceFolder}", container: "/app" },
                               { local: "${workspaceFolder}\\vendor", container: "/opt/vendor" }],
                              "C:\\Users\\me\\proj");
assert.strictEqual(c.toContainer("C:\\Users\\me\\proj\\src\\x.py", win, true), "/app/src/x.py");
assert.strictEqual(c.toContainer("c:\\users\\ME\\proj\\a.py", win, true), "/app/a.py", "drive/case-insensitive on Windows");
assert.strictEqual(c.toContainer("C:\\Users\\me\\proj\\vendor\\lib.py", win, true), "/opt/vendor/lib.py", "longest mapping wins");
assert.strictEqual(c.toLocal("/app/src/x.py", win, true), "C:\\Users\\me\\proj\\src\\x.py");
assert.strictEqual(c.toLocal("/application/x.py", win, true), "/application/x.py", "prefix must end at a path boundary");
assert.throws(() => c.toContainer("D:\\other\\y.py", win, true), /pathMappings/, "unmapped path fails loudly");
const cmd = c.containerCommand({ execPrefix: ["docker", "compose", "exec", "-T", "app"], interpreter: "python3", mappings: win },
                               "/app/.pmg/pmg_profile.py", ["--out", "/app/.pmg/profile.json"]);
assert.deepStrictEqual([cmd.command, ...cmd.args],
  ["docker", "compose", "exec", "-T", "app", "python3", "/app/.pmg/pmg_profile.py", "--out", "/app/.pmg/profile.json"]);
console.log("PASS path rules (Windows host, longest match, boundaries, unmapped, argv)");

// ---- Part 2: simulated container run ------------------------------------------------
const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "pmg-ctr-"));
const host = path.join(tmp, "host-project");          // what the editor sees
const alias = path.join(tmp, "app");                  // what the "container" sees
fs.mkdirSync(host);
fs.symlinkSync(host, alias);
fs.copyFileSync(path.join(__dirname, "profiler", "holders_workload.py"), path.join(host, "service.py"));
const cfg = { execPrefix: ["env", "PMG_IN_CONTAINER=1"], interpreter: "python3",
              mappings: c.resolveMappings([{ local: "${workspaceFolder}", container: alias }], host) };
// stage helpers into <project>/.pmg like the extension does
fs.mkdirSync(path.join(host, ".pmg"));
for (const f of ["pmg_profile.py", "probe.py"]) fs.copyFileSync(path.join(__dirname, "..", "server", f), path.join(host, ".pmg", f));
const toC = (p) => c.toContainer(p, cfg.mappings, false);

const probe = c.containerCommand(cfg, toC(path.join(host, ".pmg", "probe.py")), []);
const pr = spawnSync(probe.command, probe.args, { encoding: "utf8" });
const facts = JSON.parse(pr.stdout);
assert(facts.py_version && facts.int_size, "probe ran through the exec prefix");
console.log(`PASS probe through exec prefix -> Python ${facts.py_version}, GIL ${facts.gil_state}`);

const out = path.join(host, ".pmg", "profile.json");
const run = c.containerCommand(cfg, toC(path.join(host, ".pmg", "pmg_profile.py")),
  ["--memory", "precise", "--root", toC(host), "--out", toC(out), toC(path.join(host, "service.py"))]);
const r = spawnSync(run.command, run.args, { encoding: "utf8" });
assert.strictEqual(r.status, 0, r.stderr);
const raw = m.parseProfile(fs.readFileSync(out, "utf8"));
assert(Object.keys(raw.files).every((k) => k.startsWith(alias)), "profile is written with container paths");
const p = c.remapProfileKeys(raw, (k) => c.toLocal(k, cfg.mappings, false));
const idx = new m.ProfileIndex(p);
const hostFile = path.join(host, "service.py");
assert.strictEqual(idx.state(hostFile, fs.readFileSync(hostFile, "utf8")), "fresh", "remapped to the host file");
const e13 = idx.line(hostFile, 13);
assert(e13 && e13.leak_runs && e13.held_by[0].holder === "Service.history", "leak + holder survive the round trip");
console.log("PASS profile ran 'in the container', mapped back to the host file:", m.lineLabel(e13, p));
fs.rmSync(tmp, { recursive: true, force: true });
console.log("ALL CONTAINER TESTS PASSED");
