# Developer setup

This guide is for working on the extension, its Python and Rust language servers, and its profiler. For product behavior, start with [using the extension](02-using-the-extension.md); for how the parts work, see the [technical description](07-technical-design.md); for where each feature lives in the code, see the [feature map](feature-map.md). Packaging and publishing are in [build, install and release](05-build-and-release.md).

## Prerequisites

| Tool | Version | Purpose |
|---|---|---|
| Node.js and npm | 22+ | TypeScript build, bundle, tests, packaging (`@vscode/vsce@4.0.0` requires Node 22+) |
| Python | 3.9+ | Python server, profiler, tests |
| `uv` | latest | Vendor the pinned Python server dependencies |
| Rust | 1.80+ (optional) | Rust server and full backend parity |
| VS Code | 1.82+ | Extension Development Host |
| VS Code extensions | Python + Python Debugger (`ms-python.debugpy`); rust-analyzer (optional) | Debugging |

The example under `examples/containerized-app/` has a Dev Container configuration. This checkout has no root `.devcontainer/`, `.vscode/` (launch or tasks) or `.github/` folder; set up the tools above on your machine.

## Install and build

```bash
git clone <repo-url> python-memory-guardian
cd python-memory-guardian
npm ci                           # exact dependency versions from package-lock.json
npm run vendor:python            # bundles the pinned packages from requirements.txt into server/libs/
npm run compile                  # type-check src/*.ts -> out/ (used by tests), bundle -> dist/extension.js (what VS Code runs)
uv pip install --system -r requirements-dev.txt   # debugpy + tree-sitter: only for debugging / porting rules
```

- `vendor:python` installs the explicitly version-pinned runtime requirements into `server/libs/`, using Python 3.9 as the resolver target. End users do not install these separately. The requirements are version-pinned, not hash-pinned.
- `compile` runs TypeScript compilation and the esbuild bundle. `npm run bundle` bundles separately; `npm run watch` watches TypeScript compilation but does not rebuild the bundle.
- The npm scripts find Python through `scripts/py.js`, which tries `python3`, then `python`, then `py -3`. Set `PMG_PYTHON=/path/to/python` to force one.
- Keep `@types/vscode` pinned to the minimum supported `engines.vscode` minor version in `package.json`; `vsce package` validates this match. Upgrade the editor minimum only when the extension uses an API that requires it.

To install a packaged development build into local VS Code in one step (macOS or Linux), run `node scripts/install-local.js`; see [build, install and release](05-build-and-release.md#build-and-install-in-one-step-macos-linux).

### Rust backend

```bash
npm run build:rust               # cargo build --release
mkdir -p bin && cp rust-server/target/release/guardian-server bin/
# Windows: copy rust-server\target\release\guardian-server.exe bin\
```

The extension loads the server from `bin/`; nothing copies it there automatically. Copy it if you want a locally run extension to use `pythonMemoryGuardian.backend: "rust"`.

## Run a development build

There is no ready-made F5 launch configuration. After compiling, launch an Extension Development Host from a shell with VS Code's `code` command:

```bash
code --extensionDevelopmentPath=. test-fixtures
```

1. Open `native_patterns.py` to see diagnostics.
2. Open `profiler/holders_workload.py` and run **Profile Current File** (choose *precise*) to see leak holders.

The VS Code CLI must be on PATH; on macOS, VS Code offers **Shell Command: Install 'code' command in PATH**. You can also add your own extension launch configuration and then use F5. Set the desired interpreter and backend in the development window, not just the source window.

After editing TypeScript, run **Developer: Reload Window** in the development window. Run `npm run watch` in a shell for automatic TypeScript recompilation, then `npm run bundle` to refresh the extension bundle.

## Debug each part

| Part | How |
|---|---|
| **TypeScript client** (`src/*.ts`) | add a local extension-host launch configuration to use breakpoints; none is included |
| **Python language server** (`server/guardian_server.py`, `rules.py`) | add a local attach configuration; none is included |
| **Rules without VS Code** | `python3 -c "import sys; sys.path.insert(0,'server'); import rules; [print(d.range.start.line+1, d.message[:80]) for d in rules.analyze(open('test-fixtures/native_patterns.py').read())]"` |
| **Profiler** (`server/pmg_profile.py`) | run it directly: `python3 server/pmg_profile.py --memory precise --out .pmg/profile.json test-fixtures/profiler/workload.py` |
| **Rust server** | set `"pythonMemoryGuardian.backend": "rust"` in the development window. To step through it, attach a native debugger (CodeLLDB or gdb) to the running `guardian-server` process |
| **LSP messages** | set `"pythonMemoryGuardian.trace.server": "verbose"` and read the *Python Memory Guardian* output channel |

## Run tests

```bash
npm test            # compile, parity, then all runtime tests
```

| Command | What it checks |
|---|---|
| `npm run test:parity` | the Python and Rust servers produce **identical** diagnostics over real LSP, on every rule fixture × 4 interpreter profiles, with explicit `# expect:` markers. If the Rust binary isn't built, it prints `SKIP` and checks the Python server alone; set `PMG_REQUIRE_RUST=1` to make that a failure |
| `node scripts/py.js test-fixtures/profiler_regression_test.py` | profiler regressions with scripted samples: attribution, snapshots and their budgets, bookkeeping, timeline stacks, source hashes, one-function tracing |
| `node scripts/py.js test-fixtures/profiler_test.py` | profiler accuracy on workloads with known answers: time classification, leak detection, holder naming, snapshot budget |
| `node test-fixtures/test_model.js` | editor-side logic: staleness, hot/cold rules, labels, messages, validation. Uses the profiles written by the profiler tests, so run it after them |
| `node test-fixtures/test_report.js`, `test_compare.js`, `test_summary.js` | report models, run comparison, and the run summary against its schema |
| `node test-fixtures/test_editor_events.js`, `test_extension_lifecycle.js`, `test_report_entry.js`, `test_run_options.js` | editor events, lifecycle, the JSON-tab entry and Profile Current File options, with a simulated VS Code |
| `node test-fixtures/test_container.js` | container path mapping, plus a simulated container run through an exec prefix |
| `python test-fixtures/package_test.py path/to/extension.vsix` | a packaged VSIX: runtime files, development-file exclusions, and real LSP diagnostics from the extracted server. Not part of `npm test`; see [release steps](05-build-and-release.md#release-steps) |
| `npm run benchmark:native` | a native-retention benchmark: baseline vs time-only, fast and precise (three runs each), with diagnosis checks. Writes `.pmg/benchmark.json`; not part of `npm test` |

For a rule change, build Rust first and require the comparison:

```bash
npm run build:rust
PMG_REQUIRE_RUST=1 npm run test:parity
```

A failing check in `profiler_test.py` stops the later JavaScript tests, because `npm test` chains them with `&&`.

## Change a static rule

The Python and Rust servers must produce the same diagnostic code, range, severity, message, and related information.

1. Add or edit its wording in `server/messages.json`. Use a `sized` variant (with `{placeholders}` from `probe.py`) and a `neutral` variant.
2. Implement the detection in `server/rules.py` (Python `ast`).
3. Mirror it in `rust-server/src/main.rs` (tree-sitter). To check node shapes, install the development tools with `uv pip install --system -r requirements-dev.txt` and print the parse tree; don't guess them.
4. Add positive **and** negative cases to a fixture in `test-fixtures/`, marked with `# expect: <code>`.
5. Run `npm run build:rust && PMG_REQUIRE_RUST=1 npm run test:parity`. Both servers must produce the same output.

The [rules reference](06-rules.md) lists the current codes. Runtime profiling lives separately in `server/pmg_profile.py`.

## Project layout

```
.
├── docs/                           Guides, rules, technical description, feature map, summary schema
├── examples/profiling-workloads/  Scripts to profile: generators, PyArrow, Polars, PySpark
├── examples/containerized-app/    Example service with Dev Container and Compose files
├── images/icon.png                Marketplace icon
├── rust-server/                   Rust language server (src/main.rs, Cargo.toml)
├── scripts/
│   ├── py.js                      Cross-platform Python launcher for npm scripts
│   └── install-local.js           Package and install a local build (macOS, Linux)
├── server/
│   ├── guardian_server.py         Python language server (pygls)
│   ├── rules.py                   Static rules (CPython ast)
│   ├── messages.json              Messages shared by both servers
│   ├── probe.py                   Interpreter probe
│   ├── pmg_profile.py             Runtime profiler (standard library only)
│   └── _vendor.py                 Loads bundled server dependencies
├── src/                            VS Code extension client
│   ├── extension.ts               Server startup, interpreter probing, container mode
│   ├── profileView.ts             Profiler UI, runtime diagnostics, run summary, baselines
│   ├── profileModel.ts            Profile validation, labels, heat
│   ├── reportView.ts              Report controller
│   ├── reportModel.ts             Report data: overview, diagnosis, call trees, phases
│   ├── reportWebview.ts           Report page
│   ├── compareModel.ts            Run comparison
│   ├── summaryModel.ts            .pmg/summary.json
│   ├── runOptions.ts              Script arguments, function under the cursor
│   └── containerPaths.ts          Host/container path mapping
├── test-fixtures/                 Rule fixtures, profiler workloads, all tests
├── package.json                    Extension manifest and npm scripts
├── requirements.txt                Version-pinned runtime dependencies, bundled for users
└── requirements-dev.txt            Developer tools (debugpy, tree-sitter)
```

Generated build output (`out/`, `dist/`, `bin/`, `rust-server/target/`) and bundled Python dependencies (`server/libs/`) are created locally and are not shown.
