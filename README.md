# Python Memory Guardian

A VS Code extension for Python that does two things:

1. **Static analysis as you type.** It flags CPython memory and concurrency anti-patterns (heap inflation, RAM fragmentation, text inflation, memory swell, single-threaded stalls). Every warning names the function, class and variable involved and suggests a Data-Oriented alternative.
2. **A built-in runtime profiler.** It measures per-line time (split into Python / native / system), memory, and leaks, including **which variable is holding leaked memory**, and shows the results right in the editor.

It works on Linux, macOS and Windows, and inside containers (Dev Containers, Codespaces, plain Docker/Compose, WSL, Remote-SSH).

---

## Contents

1. [Using the extension](#1-using-the-extension)
2. [Containerized projects](#2-containerized-projects)
3. [Developing the extension](#3-developing-the-extension)
4. [Reference](#4-reference)
5. [Troubleshooting](#5-troubleshooting)

**Deploying?** Installing your own build locally and publishing to the VS Code Marketplace are covered separately in **[guide-deploy.md](guide-deploy.md)**.

---

## 1. Using the extension

### 1.1 Requirements

| What | Version | Needed for |
|---|---|---|
| VS Code | 1.82 or newer | everything |
| Python | 3.9 or newer | the default Python backend, the profiler and version detection. It must be the interpreter your code runs on |

Nothing else needs installing: the extension bundles its own copy of `pygls` (pinned in `requirements.txt` and tested on CPython 3.9, 3.10, 3.12, 3.13 and 3.14), and the probe and profiler use only the Python standard library. End users do not install separate Python dependencies.

### 1.2 Install

1. Get the `` file: install from the Marketplace if it's published there, or build one with [guide-deploy.md](guide-deploy.md) (Part 1).
2. In VS Code, open the Command Palette (**Ctrl+Shift+P**, or **Cmd+Shift+P** on macOS).
3. Run **Extensions: Install from VSIX…** and pick the file.
4. Reload the window when prompted.

### 1.3 First run

1. Open the **folder** that contains your project (**File → Open Folder…**), not just a single file.
2. Point the extension at the interpreter your code runs on. Open **Settings** (**Ctrl+,**), search for `pythonMemoryGuardian.interpreter`, and enter its path, for example:
   - `/usr/bin/python3`
   - `C:\Python312\python.exe`
   - `${workspaceFolder}/.venv/bin/python` (write the full path)

   If you leave it empty, `python3` (or `python` on Windows) from your PATH is used.
3. Open any `.py` file. Squiggles appear within about half a second of typing, and again on save.
4. Optional: check what was detected. Open **View → Output** and choose **Python Memory Guardian** in the dropdown. You'll see a line like this:
   ```
   Interpreter profile: {"py_version": "3.12.3", "gil_state": "enabled", "int_size": 28, ...}
   ```
   These numbers were **measured on your interpreter**, and the warning messages quote them.

### 1.4 Profile your code

1. Save the Python file you want to run. It must be runnable as a script (`python yourfile.py`).
2. Click the **pulse icon** in the editor title bar, or run **Python Memory Guardian: Profile Current File** from the Command Palette.
3. Choose a memory mode:

   | Mode | Measures | Typical overhead |
   |---|---|---|
   | **fast** (default) | time split + RSS memory growth per line and function | about 1–5% |
   | **precise** | adds per-line allocated / held / spike memory, **leaks and the variable holding them** | 5% (I/O-heavy) to about 3x (allocation-heavy) |
   | **time only** | time split only | about 1% |

4. Your program runs in a terminal panel. To stop it early, press **Ctrl+C**; a partial profile is still saved.
5. When it finishes, the results appear automatically (stored in `.pmg/profile.json` in your project).

> **Tip:** add `.pmg/` to your `.gitignore`.

### 1.5 Reading the results

**Static warnings** start with *where* and *what*:

```
Service.handle() › `self.history` — ⚠️ RAM Fragmentation …
Repo › `lookup` — ⚠️ Memory Swell: an unbounded cache …
handler() — ⚠️ Single-Threaded Stall: time.sleep() blocks inside `async def` …
```

**Inline labels** appear at the end of measured lines:

| Label | Meaning |
|---|---|
| `⏱ 0.61 s 8.8% (native 96%)` | time on this line, its share of the run, and the Python / native / system split |
| `~` after the time | the run used precise mode, where tracing slows allocation-heavy code; read timings from a fast run |
| `▲ alloc 140 MB → scratch` | total memory allocated by this line over the run, and the variable it assigns |
| `▲ held 45 MB` | memory from this line still alive at the peak |
| `▲ spike 160 MB` | a short-lived peak that came and went between two samples |
| `▲ RSS +22 MB` | fast mode: process memory growth while this line ran |
| `⚠ leak: 45 MB held by Service.history` | measured leak, and the variable that still holds it |
| `Σ Service.handle(): 3.7 s …` | totals for the whole function, shown on its `def` line |

**Measured leaks** also appear in the Problems panel as warnings:

```
Service.handle() › `self.history.append` — ⚠️ Runtime leak (measured): … 45.0 MB is still
referenced by `Service.history` (list, 30 items) when the program exited.
```

**The profile changes static warning severity:**
- A **hot** line (at least 5% of runtime, at least 50 MB, or leaking) is raised one level, and the message starts with `🔥 Measured in last profile: …`.
- A **cold** line is lowered to a hint, but only if it sits outside every function that was sampled. A line can run between samples, so "no samples" alone is never treated as cheap.
- If you **edit a file** after profiling it, its profile is ignored (the status bar shows *PMG profile stale — re-run*). Line numbers would be wrong otherwise.

The status bar item (`PMG 7.01 s · RSS peak 400 MB (fast)`) re-runs the profiler when clicked. Run **Toggle Profile Overlay** to hide or show the labels, and **Clear Profile** to remove them.

### 1.6 Silence a warning on one line

```python
rows = cur.fetchall()  # memory-guardian: ignore
```

### 1.7 Settings reference

| Setting | Default | What it does |
|---|---|---|
| `pythonMemoryGuardian.interpreter` | `""` | Interpreter to probe and profile with (and to run the Python backend). Empty = `python3`/`python` on PATH |
| `pythonMemoryGuardian.backend` | `python` | `python` or `rust`. Same rules and messages; Rust needs no Python on the machine running the server |
| `pythonMemoryGuardian.profile.memoryMode` | `fast` | Memory mode offered first when profiling |
| `pythonMemoryGuardian.profile.hotShare` | `0.05` | Runtime share that makes a line hot |
| `pythonMemoryGuardian.profile.hotMB` | `50` | Memory (MB) that makes a line hot |
| `pythonMemoryGuardian.profile.frames` | `2` | Precise mode: traceback depth. Higher ties more library-internal memory back to your lines, but is slower (on `import pandas`: 2 frames 1.3 s, 8 frames 2.9 s, 32 frames 7.5 s) |
| `pythonMemoryGuardian.container.execPrefix` | `[]` | Container mode only, see [2.2](#22-plain-docker--compose-editor-on-the-host) |
| `pythonMemoryGuardian.container.interpreter` | `python3` | Container mode only: Python inside the container |
| `pythonMemoryGuardian.container.pathMappings` | `[]` | Container mode only: host ↔ container folders |
| `pythonMemoryGuardian.trace.server` | `off` | Log LSP traffic to the output channel (`messages` / `verbose`) |

---

## 2. Containerized projects

### Which setup do you have?

| Your setup | Where the extension runs | What to do |
|---|---|---|
| **Dev Containers** ("Reopen in Container") or **GitHub Codespaces** | inside the container (automatic) | [2.1](#21-dev-containers-and-codespaces-recommended): no special settings |
| **WSL** or **Remote-SSH** | on the remote machine (automatic) | [2.3](#23-wsl-and-remote-ssh): same as local |
| **Docker / Compose**, but you edit on the host and the app runs in a container | on the host | [2.2](#22-plain-docker--compose-editor-on-the-host): set *container mode* |

The extension declares `"extensionKind": ["workspace"]`, so for remote setups VS Code installs and runs it **next to your code and interpreter**, not on your laptop.

### 2.1 Dev Containers and Codespaces (recommended)

There's a working example in `examples/containerized-app/`.

1. Install the **Dev Containers** extension (`ms-vscode-remote.remote-containers`).
2. Add the extension to your project's `.devcontainer/devcontainer.json`, and point it at the container's Python:
   ```jsonc
   {
     "dockerComposeFile": "../compose.yaml",   // or "image": "python:3.12-slim"
     "service": "app",
     "workspaceFolder": "/app",
     "customizations": {
       "vscode": {
         "extensions": ["your-publisher-id.python-memory-guardian"],
         "settings": { "pythonMemoryGuardian.interpreter": "/usr/local/bin/python" }
       }
     }
   }
   ```
   Using a `.vsix` file instead of the Marketplace? See [guide-deploy.md, section 1.5](guide-deploy.md#15-install-into-containers-and-remote-machines) for installing it inside the container.
3. Run **Dev Containers: Reopen in Container**.
4. Everything now works exactly as in [section 1](#1-using-the-extension). Static analysis, version detection and the profiler all use the container's Python, and paths need no translation.

**Container-specific notes:**
- The profiler reads memory from `/proc/self/statm` and per-thread CPU clocks, both of which work in standard Linux containers without extra privileges or `--cap-add`.
- **Rust backend in a container:** the bundled binary must match the *container's* OS and CPU, not your laptop's. For example, Docker on an Apple Silicon Mac runs `linux-arm64` containers, and Alpine images need the `alpine-*` build. If in doubt, keep the default Python backend, which only needs Python in the container. (Building binaries for each platform is covered in [guide-deploy.md](guide-deploy.md#35-building-rust-packages-for-other-platforms).)

### 2.2 Plain Docker / Compose (editor on the host)

Use this when you edit files on your machine and the code runs in a container, typically one started by `docker compose up` with your project bind-mounted.

In this mode:
- **Static analysis** runs on the host; it only needs the source files.
- **Version detection and the profiler** run **inside the container**, through a command prefix you configure.
- **Profile paths** come back as container paths (`/app/...`) and are translated to your host paths.

Step by step, using `examples/containerized-app/`:

1. Make sure the project is bind-mounted into the container, for example in `compose.yaml`:
   ```yaml
   services:
     app:
       build: .
       volumes:
         - .:/app          # host folder  <->  /app inside the container
       command: sleep infinity
   ```
2. Start the container: `docker compose up -d`
3. Create `.vscode/settings.json` in the project (the example ships `settings.example.json`):
   ```jsonc
   {
     "pythonMemoryGuardian.container.execPrefix": ["docker", "compose", "exec", "-T", "app"],
     "pythonMemoryGuardian.container.interpreter": "python",
     "pythonMemoryGuardian.container.pathMappings": [
       { "local": "${workspaceFolder}", "container": "/app" }
     ],
     "pythonMemoryGuardian.backend": "rust"   // if the HOST has no Python 3.9+
   }
   ```
   - `execPrefix` is the command that runs a program in your container. Use `["docker", "exec", "-i", "my-container"]` for plain Docker. Add `"-f", "path/to/compose.yaml"` after `"compose"` if the compose file isn't at the project root. Keep `-T` with compose: no TTY is needed.
   - `pathMappings` must match the bind mount: `local` is the host folder, `container` is where it appears inside. With several mounts, list each one; the longest match wins.
4. Reload the window (**Developer: Reload Window**). The Output channel should show:
   ```
   Probing container interpreter: docker compose exec -T app python /app/.pmg/probe.py
   Interpreter profile: {"py_version": "3.12.x", ...}
   ```
5. Open `src/service.py` and run **Profile Current File**. The terminal shows the command running inside the container, and results appear on your host files as usual.

**How it works:** the extension copies its two helper scripts (`probe.py`, `pmg_profile.py`, standard library only) into `<project>/.pmg/`. The container already sees that folder through the bind mount, so nothing needs installing in your image. The profiler writes `.pmg/profile.json` there, and the extension maps paths back.

### 2.3 WSL and Remote-SSH

Open the folder through **WSL: Connect to WSL** or **Remote-SSH: Connect to Host**. The extension runs on that machine. Set `pythonMemoryGuardian.interpreter` to the remote interpreter if needed; nothing else is required.

### 2.4 Container troubleshooting

| Symptom | Cause / fix |
|---|---|
| Output says `Container probe not possible (No pythonMemoryGuardian.container.pathMappings entry covers …)` | `pathMappings` doesn't cover the project folder; check `local` against your bind mount |
| `docker: command not found` in the profile terminal | `docker` isn't on the PATH VS Code sees; use the absolute path in `execPrefix` |
| `service "app" is not running` | start it first: `docker compose up -d` |
| Profile runs but no labels appear | the container path in `pathMappings` doesn't match where the container sees the files. Run `docker compose exec app ls /app` to check |
| Language server fails to start in container mode | the server runs on the **host**: install Python 3.9+ there, or set `"pythonMemoryGuardian.backend": "rust"` |
| Rust backend fails inside a Dev Container | binary built for the wrong OS/CPU (see [2.1](#21-dev-containers-and-codespaces-recommended) notes); switch to the Python backend |

---

## 3. Developing the extension

### 3.1 Prerequisites

| Tool | Version | Why |
|---|---|---|
| Node.js | 20+ | build the TypeScript client, run model tests |
| Python | 3.9+ | Python backend, profiler, tests |
| uv | latest | install and vendor Python dependencies |
| Rust | 1.80+ (optional) | the Rust backend |
| VS Code | 1.82+ | run and debug the extension |
| VS Code extensions | Python + Python Debugger (`ms-python.debugpy`); rust-analyzer (optional) | debugging |

**Or skip all of this:** open the repo in VS Code and run **Dev Containers: Reopen in Container**. The included `.devcontainer/devcontainer.json` installs Node 20, Python 3.12, Rust and debugpy, then builds everything.

### 3.2 Set up

```bash
git clone <repo-url> python-memory-guardian
cd python-memory-guardian
npm ci                           # exact dependency versions from package-lock.json
npm run vendor:python            # bundles the pinned packages from requirements.txt into server/libs/
npm run compile                  # type-check src/*.ts -> out/ (used by tests), bundle -> dist/extension.js (what VS Code runs)
uv pip install --system -r requirements-dev.txt # debugpy + tree-sitter: only for debugging / porting rules
```

Optional, for the Rust backend:

```bash
npm run build:rust               # cargo build --release
mkdir -p bin && cp rust-server/target/release/guardian-server bin/
# Windows: copy rust-server\target\release\guardian-server.exe bin\
```

Or run the VS Code task **build Rust server**, which does both steps on any OS.

The npm scripts find Python through `scripts/py.js`, which tries `python3`, then `python`, then `py -3`. Set `PMG_PYTHON=/path/to/python` to force a specific one.

### 3.3 Run it

1. Open the repo folder in VS Code.
2. Press **F5** (or pick **Run Extension** in the Run and Debug view).
3. A second VS Code window opens: the *Extension Development Host*, with `test-fixtures/` loaded.
4. Open `test-fixtures/native_patterns.py` and you'll see the diagnostics.
5. Open `test-fixtures/profiler/holders_workload.py` and run **Profile Current File** (choose *precise*) to see leak holders.

After editing TypeScript, run **Developer: Reload Window** in the development window. Run the **npm: watch** task for automatic recompiling.

### 3.4 Debug each part

| Part | How |
|---|---|
| **TypeScript client** (`src/*.ts`) | set breakpoints, press **F5** (*Run Extension*) |
| **Python language server** (`server/guardian_server.py`, `rules.py`) | pick **Extension + Python server (both debuggers)** and press F5. The server starts with `PMG_DEBUGPY=5678`, waits, and the debugger attaches. Breakpoints in `rules.py` hit when you open or edit a Python file in the development window |
| **Rules without VS Code** | `python3 -c "import sys; sys.path.insert(0,'server'); import rules; [print(d.range.start.line+1, d.message[:80]) for d in rules.analyze(open('test-fixtures/native_patterns.py').read())]"` |
| **Profiler** (`server/pmg_profile.py`) | **Debug profiler on a workload** launch config, or run it directly: `python3 server/pmg_profile.py --memory precise --out .pmg/profile.json test-fixtures/profiler/workload.py` |
| **Rust server** | set `"pythonMemoryGuardian.backend": "rust"` in the development window. To step through it, attach a native debugger (CodeLLDB or gdb) to the running `guardian-server` process |
| **LSP messages** | set `"pythonMemoryGuardian.trace.server": "verbose"` and read the *Python Memory Guardian* output channel |

### 3.5 Run the tests

```bash
npm test            # everything below, in order
```

| Command | What it checks |
|---|---|
| `npm run test:parity` | the Python and Rust servers produce **identical** diagnostics over real LSP (3 fixtures × 3 interpreter profiles). If the Rust binary isn't built, it prints `SKIP` and checks the Python server on its own (set `PMG_REQUIRE_RUST=1` to make that a failure, as CI does) |
| `node scripts/py.js test-fixtures/profiler_test.py` | profiler accuracy on workloads with known answers: time classification, leak detection, holder naming, snapshot budget (21 checks) |
| `node test-fixtures/test_model.js` | editor-side logic: staleness, hot/cold rules, labels, messages. Uses the profiles written by the previous test, so run it after |
| `node test-fixtures/test_container.js` | container path mapping, plus a simulated container run through an exec prefix |

### 3.6 Add or change a rule

Both backends must stay identical, and the parity test enforces it.

1. Add or edit the message text in `server/messages.json`. Use a `sized` variant (with `{placeholders}` from `probe.py`) and a `neutral` variant.
2. Implement the detection in `server/rules.py` (Python `ast`).
3. Implement the same detection in `rust-server/src/main.rs` (tree-sitter). To check node shapes, install the pinned development tools with `uv pip install --system -r requirements-dev.txt` and print the parse tree; don't guess them.
4. Add positive **and** negative cases to a fixture in `test-fixtures/`.
5. Run `npm run build:rust && npm run test:parity`. Both servers must produce the same output.

---

## 4. Reference

### 4.1 Rules

The diagnostic code is the key in `server/messages.json`.

| Code | Severity | Fires on |
|---|---|---|
| `heap-inflation` | Warning | `fetchall()`, `fetchone()` in loops, `for row in cur.execute(...)` |
| `pointer-chasing` | Warning | pandas imports and row-oriented loaders, however aliased |
| `cyclic-reference` | Info | back-pointer classes instantiated in loops |
| `ram-fragmentation.append` | Warning | `rows.append({...})` / tuple / list per iteration |
| `ram-fragmentation.no-slots` | Info | class without `__slots__` instantiated in a loop. Exceptions, Enums, NamedTuples and `dataclass(slots=True)` are exempt |
| `text-inflation.concat` | Warning | `s += "..."` on a str inside a loop (bytes excluded) |
| `text-inflation.whole-file` | Warning | `f.read().split()` / `.splitlines()`, `f.readlines()` |
| `memory-swell.list-arg` | Warning | `sum/any/all/min/max([...])` |
| `memory-swell.list-copy` | Warning | `for x in list(...)`, or over a list comprehension |
| `memory-swell.method-cache` | Warning | `@cache` / `lru_cache(maxsize=None)` on a method |
| `memory-swell.unbounded-cache` | Info | the same on a plain function. A bounded `lru_cache` is fine |
| `single-thread-stall.async-blocking` | Warning | `time.sleep`, `requests.*`, `subprocess.*`, `urlopen` inside `async def` |
| `single-thread-stall.list-membership` | Warning | `x in some_list` inside a loop |
| `single-thread-stall.cpu-thread` | Info | CPU-bound function handed to a thread (below) |

**When the thread rule fires, and when threads are fine.** The rule looks at what the target function does with the data it's given:
- It **fires** when the function (defined in the same file) runs a Python-level loop over its own inputs, makes no I/O or GIL-releasing native calls, and the interpreter has the GIL.
- It **stays silent** in these cases:
  - I/O-bound work: network, files, database, subprocesses.
  - Native libraries that release the GIL: hashlib, zlib, NumPy, Polars, PyArrow, DuckDB, SciPy.
  - Loops that don't depend on the input.
  - Lambdas or functions defined elsewhere, since it won't guess what they do.
  - Free-threaded interpreters.

### 4.2 Version awareness

At startup the extension runs `server/probe.py` on your interpreter (inside the container in container mode). The probe **measures** object sizes, reads the runtime GIL state, and identifies the memory allocator. Messages quote those numbers; anything the probe can't determine falls back to wording without numbers. Nothing is hard-coded per Python version.

### 4.3 How the profiler works, and its limits

**Time.** The profiler samples every 10 ms from a background thread. For each sample:
- *system* time is wall time the thread spent off the CPU, read from the thread's own CPU clock;
- the rest is split between *python* and *native* by how long the sampler waited for the GIL. Measured on CPython 3.12: about 5 ms (the switch interval) while bytecode runs, about 0.1 ms when C code released the GIL, and hundreds of ms when C code held it;
- free-threaded builds have no GIL signal, so CPU time is reported unsplit.

**Memory.**
- *fast* mode attributes RSS growth to the running line.
- *precise* mode uses `tracemalloc` for allocations per line, the memory still alive at the peak, short-lived spikes (captured with `tracemalloc.reset_peak()`), leaks, and their holders. Snapshot cost is predicted from `tracemalloc`'s own bookkeeping size and capped at 10% of runtime.

**Leaks.** A line is reported as leaking when the memory it allocated never went down over the trailing snapshots, rose in at least 3 of them, and at least 1 MB was still alive at exit. The holder is then found by searching garbage-collector-tracked objects, module globals, and attributes of your own classes' objects. The last two matter because CPython can stop tracking dicts that hold only plain values (verified: a dict of `bytearray`s isn't tracked).

**Known limits.**
- A single C call that holds the GIL (such as `[0] * 20_000_000`) can be credited to the *next* line, because the sampler can only run once the call returns. Function totals are unaffected.
- Memory allocated deep inside library code (for example during `import pandas`) often can't be traced back to your line, because the import chain is deeper than the recorded traceback. That line then shows `alloc` but little `held` memory. The profile reports the total as `unattributed_peak_mb`, shown in the status-bar tooltip. Raising `profile.frames` recovers some of it (32 frames recovered 8 of 39 MB on a pandas import) at a large speed cost.
- Not covered: child processes (`multiprocessing`), GPU time, copy volume.

### 4.4 Head-to-head with Scalene 2.3.0

Same machine, same workloads with known per-line behavior (`test-fixtures/profiler/`):

| | Guardian | Scalene |
|---|---|---|
| Overhead, time only | 1.6% | 15% (`--cpu-only`) |
| Overhead, time + memory | 1.2% (fast) / 3.1x (precise) | 37% |
| Python / native / system classification | correct on all test lines | correct on all test lines |
| 48 MB leak in `workload.py` | found, holder `global LEAK` named | not reported |
| 2 MB-per-call leak in `leak_workload.py` | found | not reported |
| 160 MB short-lived list | correct at function level; line-level lands on the next line | exact line |

These results come from one machine and a few workloads, not a general benchmark. Scalene remains more mature, covers GPU, copy volume and multiprocessing, and its malloc hooks attribute short-lived native allocations more precisely.

### 4.5 Project layout

```
.
├── .devcontainer/
│   └── devcontainer.json          Extension development container
├── .github/workflows/
│   └── release.yml                CI, packaging, and publishing
├── .vscode/
│   ├── launch.json                Extension and profiler launch configs
│   └── tasks.json                 Build tasks
├── examples/containerized-app/
│   ├── .devcontainer/devcontainer.json
│   ├── .vscode/settings.example.json
│   ├── src/service.py             Example Python service
│   ├── Dockerfile
│   └── compose.yaml
├── images/icon.png                Marketplace icon
├── rust-server/
│   ├── src/main.rs                Rust language server
│   └── Cargo.toml
├── scripts/py.js                  Cross-platform Python launcher for npm scripts
├── server/
│   ├── guardian_server.py         Python language server (pygls)
│   ├── rules.py                   Static rules (CPython ast)
│   ├── messages.json              Messages shared by both servers
│   ├── probe.py                   Interpreter probe
│   ├── pmg_profile.py             Runtime profiler
│   └── _vendor.py                 Loads bundled server dependencies
├── src/                            VS Code extension client
│   ├── extension.ts               Server startup, interpreter probing, container mode
│   ├── profileView.ts             Profiler UI and runtime diagnostics
│   ├── profileModel.ts            Profile data and logic
│   └── containerPaths.ts          Host/container path mapping
├── test-fixtures/
│   ├── profiler/                  Profiler workloads
│   ├── parity_test.py             Python/Rust diagnostics parity
│   ├── profiler_test.py           Profiler accuracy tests
│   ├── test_model.js               Profile model tests
│   └── test_container.js           Container path tests
├── package.json                    Extension manifest and npm scripts
├── package-lock.json               Locked npm dependencies
├── requirements.txt                Version-pinned runtime dependencies, bundled for users
├── requirements-dev.txt            Developer tools (debugpy, tree-sitter)
├── tsconfig.json                   TypeScript configuration
└── guide-deploy.md                 Local deployment and Marketplace publishing
```

Generated build output (`out/`, `dist/`, `bin/`, and `rust-server/target/`) and bundled Python dependencies (`server/libs/`) are created locally and are not shown.

### 4.6 Verification status

Everything in this README was run while building it, except the following. **Real Docker, Dev Containers and Codespaces were not available in the build environment.**
- Container mode was verified with a simulated container: commands went through an exec prefix, and the project was reached through a different path.
- The `devcontainer.json`, `compose.yaml` and settings files were validated for syntax, not launched.

Report anything that differs from these instructions.

### 4.7 Sources

- docs.python.org:
  - What's New 3.12 (PEP 623)
  - C-API Memory Management (pymalloc arenas)
  - `tracemalloc`, `sys` (switch interval, `_current_frames`), `threading`
  - Developing with asyncio
  - Programming FAQ ("How do I cache method calls?")
  - Free-threading HOWTO
- peps.python.org: PEP 393, 623, 703, 779.
- CPython Misc/NEWS 2.5a1 (arena release).

---

## 5. Troubleshooting

| Symptom | Fix |
|---|---|
| No squiggles at all | Check **View → Output → Python Memory Guardian** for start-up errors. Make sure the file's language mode (bottom-right of the window) is *Python* |
| `Interpreter probe failed` in the output | `pythonMemoryGuardian.interpreter` doesn't point at a working Python; messages fall back to version-neutral wording |
| `Rust server binary not found` | build it ([3.2](#32-set-up)) or switch `pythonMemoryGuardian.backend` back to `python` |
| Status bar says *profile stale* | you edited the file after profiling it; profile again |
| Profile shows nothing for your file | only files under the opened folder count as "your code"; library and standard-library frames are excluded |
| Precise mode is slow | expected for allocation-heavy code (`tracemalloc` hooks every allocation). Use fast mode for timing |
| Warnings look different in another window | each window probes its own interpreter, so sizes quoted in messages follow that interpreter |
