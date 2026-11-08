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
npm run vendor:python            # bundles the pinned packages from requirements.txt into src/python/libs/
npm run compile                  # type-check src/typescript -> out/ (used by tests), bundle -> dist/extension.js (what VS Code runs)
uv pip install --system debugpy==1.8.22 tree-sitter==0.26.0 tree-sitter-python==0.25.0   # optional: only for debugging / porting rules
```

- The development tools are optional and are not tracked in the repository (`requirements-dev.txt` is git-ignored, so you can keep your own local list). Keep `tree-sitter-python` in step with the grammar version in `src/rust/Cargo.toml`.
- `vendor:python` installs the explicitly version-pinned runtime requirements into `src/python/libs/`, using Python 3.9 as the resolver target. End users do not install these separately. The requirements are version-pinned, not hash-pinned.
- `compile` runs TypeScript compilation and the esbuild bundle. `npm run bundle` bundles separately; `npm run watch` watches TypeScript compilation but does not rebuild the bundle.
- The npm scripts find Python through `scripts/py.js`, which tries `python3`, then `python`, then `py -3`. Set `PMG_PYTHON=/path/to/python` to force one.
- Keep `@types/vscode` pinned to the minimum supported `engines.vscode` minor version in `package.json`; `vsce package` validates this match. Upgrade the editor minimum only when the extension uses an API that requires it.

To install a packaged development build into local VS Code in one step (macOS or Linux), run `node scripts/install-local.js`; see [build, install and release](05-build-and-release.md#build-and-install-in-one-step-macos-linux).

### Rust backend

```bash
npm run build:rust               # cargo build --release
mkdir -p bin && cp src/rust/target/release/guardian-server bin/
# Windows: copy src\rust\target\release\guardian-server.exe bin\
```

The extension loads the server from `bin/`; nothing copies it there automatically. Copy it if you want a locally run extension to use `pythonMemoryGuardian.backend: "rust"`.

## Run a development build

There is no ready-made F5 launch configuration. After compiling, launch an Extension Development Host from a shell with VS Code's `code` command:

```bash
code --extensionDevelopmentPath=. tests/fixtures/static
```

1. Open `native_patterns.py` to see diagnostics.
2. Open `profiler/holders_workload.py` and run **Profile Current File** (choose *precise*) to see leak holders.

The VS Code CLI must be on PATH; on macOS, VS Code offers **Shell Command: Install 'code' command in PATH**. You can also add your own extension launch configuration and then use F5. Set the desired interpreter and backend in the development window, not just the source window.

After editing TypeScript, run **Developer: Reload Window** in the development window. Run `npm run watch` in a shell for automatic TypeScript recompilation, then `npm run bundle` to refresh the extension bundle.

## Debug each part

| Part | How |
|---|---|
| **TypeScript client** (`src/typescript/`) | add a local extension-host launch configuration to use breakpoints; none is included |
| **Python language server** (`src/python/guardian_server.py`, `src/python/rules/`) | add a local attach configuration; none is included |
| **Rules without VS Code** | `python3 -c "import sys; sys.path.insert(0,'src/python'); import rules; [print(d.range.start.line+1, d.message[:80]) for d in rules.analyze(open('tests/fixtures/static/native_patterns.py').read())]"` |
| **Profiler** (`src/python/pmg_profile.py`) | run it directly: `python3 src/python/pmg_profile.py --memory precise --out .pmg/profile.json tests/fixtures/profiler/workload.py` |
| **Rust server** | set `"pythonMemoryGuardian.backend": "rust"` in the development window. To step through it, attach a native debugger (CodeLLDB or gdb) to the running `guardian-server` process |
| **LSP messages** | set `"pythonMemoryGuardian.trace.server": "verbose"` and read the *Python Memory Guardian* output channel |

## Run tests

```bash
npm test            # compile, parity, then all runtime tests
```

| Command | What it checks |
|---|---|
| `npm run test:parity` | the Python and Rust servers produce **identical** diagnostics over real LSP, on every rule fixture × 4 interpreter profiles, with explicit `# expect:` markers. This npm command builds Rust first; a build failure stops the comparison |
| `node scripts/py.js tests/profiler/profiler_regression_test.py` | profiler regressions with scripted samples: attribution, snapshots and their budgets, bookkeeping, timeline stacks, source hashes, one-function tracing |
| `node scripts/py.js tests/profiler/profiler_test.py` | profiler accuracy on workloads with known answers: time classification, leak detection, holder naming, snapshot budget |
| `node tests/client/test_model.js` | editor-side logic: staleness, hot/cold rules, labels, messages, validation. Uses the profiles written by the profiler tests, so run it after them. Transient-memory label assertions use explicit peak values because sampling can miss short-lived buffers; measured retention checks still use the actual profile |
| `node tests/client/test_report.js`, `test_compare.js`, `test_summary.js` | report models, run comparison, and the run summary against its schema |
| `node tests/client/test_editor_events.js`, `test_extension_lifecycle.js`, `test_report_entry.js`, `test_run_options.js` | editor events, lifecycle, the JSON-tab entry and Profile Current File options, with a simulated VS Code |
| `node tests/client/test_container.js` | container path mapping, plus a simulated container run through an exec prefix |
| `node tests/client/test_staging.js` | writes into `.pmg/`: stale profiler files removed, user files kept, symbolic links, hard links and pipes at a helper's path replaced and never followed or read (the pipe case runs in a child process with a timeout, so a regression fails instead of hanging), a linked `.pmg` refused, `writeFileAtomic` replacing a link at its target |
| `node tests/client/test_pmg_writes.js` | the callers use those helpers: a profile run (host mode), a baseline save and the `summary.json` write each refuse a linked `.pmg` and replace a link planted at the target name |
| `python tests/packaging/package_test.py path/to/extension.vsix` | a packaged VSIX: runtime files, development-file exclusions, and real LSP diagnostics from the extracted server. Not part of `npm test`; see [release steps](05-build-and-release.md#release-steps) |
| `npm run test:acceptance` | compile, fresh Rust build and parity, then saved-artifact acceptance checks and overhead measurements. The runner verifies the reused Rust build is current. Exit 1 means failed cases; exit 2 means incomplete coverage |
| `npm run test:guard` | independent growth/fixed and GIL/descendant-pressure witnesses, memory-budget enforcement and report validation. Unsupported OS cases exit 2 and remain incomplete; OS RSS observations require process visibility |
| `npm run benchmark:native` | a native-retention benchmark: baseline vs time-only, fast and precise (three runs each), with diagnosis checks. Writes `.pmg/benchmark.json`; not part of `npm test` |

For a rule change, build Rust first and require the comparison:

```bash
npm run build:rust
PMG_REQUIRE_RUST=1 npm run test:parity
```

The [acceptance validation suite](acceptance-validation.md) adds independent positive and corrected-negative witnesses for memory, GC, structure costs and concurrent conflicts. It preserves commands, outputs and profile artifacts, separates unsupported observations from passes, and measures runtime and peak RSS against a baseline with budgets set before the run. These fixtures assess measurement limits as well as rule recognition; they do not add capabilities to PMG. Each run writes its report, with the recorded environment and results, to `.validation/` (local, not tracked by git).

A failing check in `profiler_test.py` stops the later JavaScript tests, because `npm test` chains them with `&&`.

## Change a static rule

The Python and Rust servers must produce the same diagnostic code, range, severity, message, and related information.

1. Add or edit its wording in `src/python/messages.json`. Use a `sized` variant (with `{placeholders}` from `probe.py`) and a `neutral` variant.
2. Implement the detection in `src/python/rules/` (Python `ast`), usually in `visitor.py`.
3. Mirror it in the module of the same name in `src/rust/src/` (tree-sitter). To check node shapes, install the optional development tools (see [Install and build](#install-and-build)) and print the parse tree; don't guess them.
4. Add positive **and** negative cases to a fixture in `tests/fixtures/static/`, marked with `# expect: <code>`.
5. Run `npm run build:rust && PMG_REQUIRE_RUST=1 npm run test:parity`. Both servers must produce the same output.

The [rules reference](06-rules.md) lists the current codes. Runtime profiling lives separately in `src/python/pmg_profile.py` and `src/python/pmg_profiler/`.

## Project layout

```
.
├── docs/                           Guides, rules, technical description, feature map, summary schema
├── examples/profiling-workloads/  Scripts to profile: generators, PyArrow, Polars, PySpark
├── examples/containerized-app/    Example service with Dev Container and Compose files
├── images/icon.png                Marketplace icon
├── scripts/
│   ├── py.js                      Cross-platform Python launcher for npm scripts
│   ├── check-format.js            Layout check for `npm run format:check`
│   └── install-local.js           Package and install a local build (macOS, Linux)
├── src/
│   ├── typescript/                VS Code extension client
│   │   ├── extension.ts           activate and deactivate, wiring only
│   │   ├── language/              Interpreter, probe, server options, client start and restart
│   │   ├── container/             Host/container path mapping, staging into .pmg/
│   │   ├── profile/               Profile types, validation, index, metrics, labels (no vscode)
│   │   ├── profiling/             Editor workflow: controller, runner, annotations, diagnostics, baselines
│   │   ├── report/                Report controller and page; report/model/ holds its data (no vscode)
│   │   ├── compare/               Run comparison (no vscode)
│   │   └── summary/               .pmg/summary.json (no vscode)
│   ├── python/
│   │   ├── guardian_server.py     Python language server (pygls)
│   │   ├── rules/                 Static rules (CPython ast); modules match src/rust/src by name
│   │   ├── messages.json          Messages shared by both servers
│   │   ├── probe.py               Interpreter probe (standalone)
│   │   ├── pmg_profile.py         Profiler entry point: the documented CLI path
│   │   ├── pmg_diagnostics.py     Public helper that users import
│   │   ├── pmg_profiler/          The runtime profiler (standard library only)
│   │   └── _vendor.py             Loads bundled server dependencies
│   └── rust/                      Rust language server (Cargo.toml; src/main.rs starts it)
├── tests/
│   ├── client/                    Node tests of the TypeScript client
│   ├── static/                    Rule regression, parity and server lifecycle tests
│   ├── profiler/                  Profiler, diagnostics and allocation tests; out/ is generated
│   ├── guard/                     Memory guard tests
│   ├── packaging/                 VSIX content test
│   ├── acceptance/                Acceptance suite and overhead measurement
│   └── fixtures/                  Rule fixtures and profiler, acceptance and static workloads
├── package.json                    Extension manifest and npm scripts
└── requirements.txt                Version-pinned runtime dependencies, bundled for users
```

Generated build output (`out/`, `dist/`, `bin/`, `src/rust/target/`, `tests/profiler/out/`) and bundled Python dependencies (`src/python/libs/`) are created locally and are not shown.

Python rules and Rust modules share file names, so a rule change has an obvious counterpart. The profiler modules load before the target starts, because the target's folder goes on `sys.path`: do not add an import that first runs after the target starts. The tuned loops in `pmg_profiler/sampler.py` and `memory.py` must not gain a call per sample; compare `tests/acceptance/measure_overhead.py` results before and after a change there.

Holder assertions use rooted reference paths, such as `global svc.history`, in editor messages, summaries and container profile round trips. The class name alone does not identify the retained owner.

## Optional-backend and release checks

Install `requirements-quality.txt` and npm development dependencies, then run
`npm run lint`. It checks source layout and JSON syntax, TypeScript types,
ESLint correctness rules, Ruff import/executable-code rules and Rust Clippy
with warnings as errors. The layout check preserves this repository's compact
style and rejects trailing whitespace, tab indentation and missing final
newlines. It does not reflow whole files. Ruff exceptions apply only to four
intentional static-analysis inputs; runtime checks remain linted. The platform
workflow runs these checks before tests.

Install `requirements-acceptance.txt` in the test interpreter for full fixtures.
`allocation_backend_test.py` runs independent witnesses before capture and
corrected variants. Missing Memray remains incomplete in acceptance.
`test_allocation_model.js` checks the consumer contract.

`.github/workflows/validate.yml` defines runtime/package checks on Python
3.9 and 3.13 across Linux, macOS and Windows. A free-threaded 3.13t job on
Linux reports its result without failing the workflow. It runs each suite as
its own step, so one known failure does not hide the result of later suites.
Checks of sampled time print `SKIP` there (the profiler samples no time per
line on free-threaded builds); on such fixtures `test_model.js` and
`test_summary.js` check only that the profiles parse, the summaries match the
schema and both explain the missing time. Memray is not installed (no 3.13t
wheel). Known failures, seen on macOS 3.13.3t: the snapshot budget in
`profiler_test.py`, the GC-pressure check in `runtime_acceptance_test.py`, and
three limits in `memory_guard_test.py`. A container job runs `test_container.js` against real Docker with
`PMG_CONTAINER_TEST=docker` on `python:3.9-slim` and `python:3.13-slim`; set
the same variable locally (and optionally `PMG_CONTAINER_IMAGE`) to run it
with a local Docker. A separate acceptance job
requires complete coverage and original overhead budgets and saves evidence on
failure. Local validation does not establish matrix success.
