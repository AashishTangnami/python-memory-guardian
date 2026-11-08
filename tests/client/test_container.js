/**
 * Container-mode tests. Part 1: pure path rules. Part 2: an end-to-end run where the
 * "container" sees the project at a different path (a symlink alias) and commands go
 * through an exec prefix - the same shape as `docker compose exec -T app python3 ...`.
 * Real Docker is not required by default. With PMG_CONTAINER_TEST=docker, Part 2 runs in a real
 * container instead (image from PMG_CONTAINER_IMAGE, default python:3.12-slim), with the project
 * bind-mounted at /app as in examples/containerized-app.
 */
const assert = require("assert"), fs = require("fs"), os = require("os"), path = require("path");
const { spawnSync } = require("child_process");
const c = require("../../out/container/paths");
const { stageHelper, stageProfiler } = require("../../out/container/staging");
const m = { ...require("../../out/profile/parse"), ...require("../../out/profile/index"), ...require("../../out/profile/metrics"), ...require("../../out/profile/labels") };

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

{ const original = { script: '/app/main.py', files: {},
    run: { cwd: '/app', command: ['python', '/app/main.py'], argv: ['/app/input'] },
    diagnostics: { trace_path: '/app/events.jsonl', findings: [{ source: { file: '/app/main.py' },
      reproduction: { trace_path: '/app/events.jsonl', command: ['python', '/app/main.py'] } }] },
    allocation_backend: { files: [{ artifact: { path: '/app/profile.memray' },
      sites: [{ source: { file: '/app/main.py' }, python_stack: [{ file: '/app/helper.py' }] }] }] } };
  const mapped = c.remapProfileKeys(original, p => p.replace(/^\/app(?=\/|$)/, '/host'));
  assert.strictEqual(mapped.diagnostics.findings[0].source.file, '/host/main.py');
  assert.strictEqual(mapped.diagnostics.findings[0].reproduction.trace_path, '/host/events.jsonl');
  assert.strictEqual(mapped.allocation_backend.files[0].artifact.path, '/host/profile.memray');
  assert.strictEqual(mapped.allocation_backend.files[0].sites[0].python_stack[0].file, '/host/helper.py');
  assert.deepStrictEqual(mapped.run.command, original.run.command, 'capture commands retain container provenance');
  assert.deepStrictEqual(mapped.run.argv, original.run.argv, 'target argv is not path metadata');
  assert.strictEqual(original.diagnostics.trace_path, '/app/events.jsonl', 'mapping does not mutate original');
}

// ---- Part 2: simulated container run ------------------------------------------------
const docker = process.env.PMG_CONTAINER_TEST === "docker";
// On CI the runner's temp folder is one the Docker daemon can bind-mount; os.tmpdir() may not be.
const tmp = fs.mkdtempSync(path.join(process.env.RUNNER_TEMP || os.tmpdir(), "pmg-ctr-"));
const host = path.join(tmp, "host-project");          // what the editor sees
const alias = docker ? "/app" : path.join(tmp, "app"); // what the container sees
fs.mkdirSync(host);
if (!docker) fs.symlinkSync(host, alias);
fs.copyFileSync(path.join(__dirname, "..", "fixtures", "profiler", "holders_workload.py"), path.join(host, "service.py"));
// The container runs as the host user, so the host can read and remove what it writes.
const user = docker ? ["--user", `${process.getuid()}:${process.getgid()}`] : [];
const cfg = { execPrefix: docker
                ? ["docker", "run", "--rm", ...user, "-v", `${host}:/app`, "-w", "/app", process.env.PMG_CONTAINER_IMAGE || "python:3.12-slim"]
                : ["env", "PMG_IN_CONTAINER=1"],
              interpreter: docker ? "python" : "python3",
              mappings: c.resolveMappings([{ local: "${workspaceFolder}", container: alias }], host) };
// stage helpers into <project>/.pmg with the extension's own code
const extension = { asAbsolutePath: (p) => path.join(__dirname, "..", "..", p) };
stageHelper(extension, host, "probe.py");
stageProfiler(extension, host);
const toC = (p) => c.toContainer(p, cfg.mappings, false);

const probe = c.containerCommand(cfg, toC(path.join(host, ".pmg", "probe.py")), []);
const pr = spawnSync(probe.command, probe.args, { encoding: "utf8" });
// Report the exec prefix's own failure (docker not running, image or mount missing), not a JSON error.
const failed = (what, cmd, res) => `${what} failed: ${[cmd.command, ...cmd.args].join(" ")}\n` +
  `status ${res.status}${res.error ? `, ${res.error.message}` : ""}\nstderr: ${res.stderr}\nstdout: ${res.stdout}`;
assert.strictEqual(pr.status, 0, failed("probe", probe, pr));
const facts = JSON.parse(pr.stdout);
assert(facts.py_version && facts.int_size, "probe ran through the exec prefix");
console.log(`PASS probe through exec prefix -> Python ${facts.py_version}, GIL ${facts.gil_state}`);

const out = path.join(host, ".pmg", "profile.json");
const run = c.containerCommand(cfg, toC(path.join(host, ".pmg", "pmg_profile.py")),
  ["--memory", "precise", "--root", toC(host), "--out", toC(out), toC(path.join(host, "service.py"))]);
const r = spawnSync(run.command, run.args, { encoding: "utf8" });
assert.strictEqual(r.status, 0, failed("profile run", run, r));
const raw = m.parseProfile(fs.readFileSync(out, "utf8"));
assert(Object.keys(raw.files).every((k) => k.startsWith(alias)), "profile is written with container paths");
const p = c.remapProfileKeys(raw, (k) => c.toLocal(k, cfg.mappings, false));
const idx = new m.ProfileIndex(p);
const hostFile = path.join(host, "service.py");
assert.strictEqual(idx.state(hostFile, fs.readFileSync(hostFile, "utf8")), "fresh", "remapped to the host file");
const e13 = idx.line(hostFile, 13);
assert(e13 && e13.leak_runs && e13.held_by[0].holder === "global svc.history", "growth candidate + rooted owner path survive the round trip");
assert(p.memory_stacks.frames.filter((f) => f.user).every((f) => f.file === hostFile), "memory-stack frames are remapped too");
console.log("PASS profile ran 'in the container', mapped back to the host file:", m.lineLabel(e13, p));
fs.rmSync(tmp, { recursive: true, force: true });
console.log("ALL CONTAINER TESTS PASSED");
