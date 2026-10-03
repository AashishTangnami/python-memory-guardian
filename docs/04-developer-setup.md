# Developer setup

This guide is for working on the extension, its Python and Rust language servers, and its profiler. For product behavior, start with [using the extension](02-using-the-extension.md).

## Prerequisites

| Tool | Purpose |
|---|---|
| Node.js 20+ and npm | TypeScript build, bundle, tests, packaging |
| Python 3.9+ | Python server, profiler, tests |
| `uv` | Vendor the pinned Python server dependencies |
| Rust 1.80+ | Optional Rust server and full backend parity |
| VS Code 1.82+ | Extension Development Host |

## Install and build

From the repository root:

```bash
npm ci
npm run vendor:python
npm run compile
```

`vendor:python` installs the pinned runtime packages into `server/libs/`. `compile` type-checks the TypeScript and bundles `dist/extension.js`. The packaging command also invokes these steps through `vscode:prepublish`.

To work on the Rust backend, build it separately:

```bash
npm run build:rust
```

Copy the release binary from `rust-server/target/release/` into `bin/` if you want a locally run extension to select `pythonMemoryGuardian.backend: "rust"`. On Windows the binary is `guardian-server.exe`.

## Run a development build

This checkout has no root `.vscode/launch.json`, so **F5 has no ready-made launch configuration**. After compiling, launch an Extension Development Host from a shell with VS Code's `code` command:

```bash
code --extensionDevelopmentPath=. test-fixtures
```

Open `native_patterns.py` there to inspect diagnostics. The VS Code CLI must be on PATH; on macOS, VS Code offers **Shell Command: Install 'code' command in PATH**. You can also add your own extension launch configuration and then use F5.

For profiler behavior, open a runnable script under `test-fixtures/profiler/` and run **Python Memory Guardian: Profile Current File**. Set the desired interpreter and backend in the development window, not just the source window.

## Run tests

```bash
npm test
```

This compiles the client, runs LSP parity, and runs profiler, model, lifecycle, and container tests. The parity test uses the Python server alone if the Rust binary has not been built. For a rule change, build Rust first and require comparison:

```bash
npm run build:rust
PMG_REQUIRE_RUST=1 npm run test:parity
```

The Python launcher in `scripts/py.js` tries `python3`, `python`, then `py -3`; set `PMG_PYTHON` to use a particular interpreter.

## Change a static rule

The Python and Rust servers must produce the same diagnostic code, range, severity, message, and related information.

1. Add or edit its wording in `server/messages.json`.
2. Implement the Python AST detection in `server/rules.py`.
3. Mirror it in the tree-sitter visitor in `rust-server/src/main.rs`.
4. Add positive and negative cases to `test-fixtures/`.
5. Run the required parity comparison above.

The detailed [rules reference](../README.md#41-rules) lists the current codes. Runtime profiling lives separately in `server/pmg_profile.py`.

## Debugging

The default workflow is to reproduce a problem with a fixture and inspect the extension's **Python Memory Guardian** Output channel. Set `pythonMemoryGuardian.trace.server` to `verbose` to see LSP traffic. For Python server rules, you can call `rules.analyze()` from a local Python session after vendoring dependencies. To attach a debugger to the Extension Development Host or Python server, create a local launch configuration; this checkout does not ship one.
