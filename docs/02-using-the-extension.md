# Using the extension

Python Memory Guardian combines static warnings with runtime measurements. Use the warnings to choose what to inspect, then profile a representative workload to see where time and memory actually go. For how each measurement works and its limits, see the [technical description](07-technical-design.md).

**Contents:** [Interpreter and backend](#interpreter-and-backend) · [Static warnings](#static-warnings) · [Profile a script](#profile-a-script) · [The report](#the-report) · [Inline labels](#inline-labels) · [How a profile changes warnings](#how-a-profile-changes-warnings) · [Run summary](#run-summary) · [Commands and settings](#commands-and-settings) · [Troubleshooting](#troubleshooting)

## Interpreter and backend

Set `pythonMemoryGuardian.interpreter` to the interpreter your project uses, for example `/usr/bin/python3`, `C:\Python312\python.exe`, or the full path to `.venv/bin/python`. An empty setting uses `python3` (or `python` on Windows) from VS Code's PATH. The default Python backend runs its language server and profiler with that interpreter. The optional Rust backend uses a packaged native server for static analysis; profiling still uses Python. Select it with `pythonMemoryGuardian.backend: "rust"` only if the installed package contains the matching Rust binary.

The interpreter probe measures details such as object sizes and GIL state, and warnings quote them. If it fails, warnings use wording without those measurements. Check **View → Output → Python Memory Guardian** for probe and server errors.

## Static warnings

Open a Python file to receive warnings while editing and on save. The [rules reference](06-rules.md) lists every code, what triggers it, and how to silence one finding (`# memory-guardian: ignore` on its line). A warning describes a likely cost in a syntax pattern; profile to see whether it costs anything in your program.

## Profile a script

To practice, profile one of the scripts in [`examples/profiling-workloads/`](../examples/profiling-workloads/), which compare memory-heavy and lighter ways to do the same job.

1. Save the Python file you want to run. It must be runnable as a script (`python yourfile.py`).
2. Click the **pulse icon** in the editor title bar, or run **Python Memory Guardian: Profile Current File** from the Command Palette.
3. Choose a memory mode:

   | Mode | What it records | Cost |
   |---|---|---|
   | **fast** (default) | Sampled time, call stacks, and process memory (RSS) growth | Low (+4% to +36% measured on `generators.py`); RSS cannot identify retained objects |
   | **precise** | Adds per-line allocations, retained memory, suspected growth, holder evidence, and allocation by call path | `tracemalloc` hooks every allocation: about 3× on light code, 10× or more with millions of small objects |
   | **precise, only while `f()` runs** | The same, for one function; offered when the cursor is inside it (Python 3.12+) | Full speed outside `f`; no leak detection and no native estimate |
   | **time only** | Sampled time and call stacks | No per-line memory display; process RSS fields are still recorded |

4. Enter the script's arguments, if any (for example `--records 20000 --mode both`). They are remembered per file; press Enter for none, or Escape to cancel. A smaller input usually shows the same memory pattern in a fraction of the time.
5. Your program runs in a terminal panel. To stop it early, press **Ctrl+C**; a partial profile is still saved.
6. When it finishes, the **Memory Guardian Report** opens and measured lines get inline labels. The data is stored in `.pmg/profile.json` under the workspace folder; add `.pmg/` to your `.gitignore`.

Use **fast** for timing comparisons and **precise** when investigating who retains memory. Memory growth alone does not establish a leak.

On Python 3.12+, set `pythonMemoryGuardian.profile.monitoring` to `lines` to also record which of your lines executed. It can add overhead. It prevents an executed line that the sampler missed from being treated as cold; it does not replace sampled timing. If Python lacks `sys.monitoring` or another tool owns its profiler ID, the report says line-event coverage is unavailable.

## The report

Reopen the loaded report with **Python Memory Guardian: Open Profile Report** or by clicking the status bar item (`PMG 7.01 s · RSS peak 400 MB (fast)`). For a profile JSON open in the editor, click the **graph icon** in its title bar; the extension validates it and opens the report beside it (unsaved edits are shown as they are). **Open Saved Profile Report** selects any schema-2 or schema-3 profile, including output from the standalone CLI. Older schema-2 profiles load without caller stacks and memory trends.

Source navigation from the report works only while the file still matches the profile.

### Overview

Run duration, CPU and sample counts, the top sampled lines, and a chart of process RSS over time (plus traced Python memory in precise mode; the chart is hidden in time-only mode). Click a top line to open it. RSS can stay high after memory is released; use Memory diagnosis to see what is still held. In precise mode, drag across the chart to narrow **Memory diagnosis** to that time window.

**Phases** splits the run by what the main thread was doing: contiguous stretches in one function at a chosen depth of its call stack (picked automatically; **Shallower** and **Deeper** change it). Each phase shows its duration, peak traced memory, peak RSS and **new RSS**: process memory the phase needed beyond what earlier phases had already made resident. That is how to compare two variants run in one process: on `generators.py`, both eager phases needed over 300 MB of new RSS and every streaming phase needed none, although total RSS stayed high. A phase's **Memory diagnosis** button narrows the diagnosis to it, and **Focus stacks** limits the Stack Explorer and Top functions to stacks through that function.

### Memory diagnosis

In precise mode, findings are grouped as **suspected growing retention**, **retained at end**, and **released during the run**. Each shows recorded memory trends, any named holders, and recommendations for lists, dictionaries and caches, or global ownership. These are observations and investigation steps, not proof of an unintended leak. In both memory modes, the tab also lists the **largest objects still alive at exit** and who holds them.

### Stack Explorer

Aggregated Python call stacks, including library frames. Boxes are colored by origin (your code, each installed package, the standard library, Python internals such as the import system) and described in plain words when you hover, for example `_find_and_load · Python import system — loading a module (import)`, with total time, share of the run, and whether the time was spent in the function itself or in what it calls.

- **Measure:** sampled time (all, Python, native, waiting, unclassified), or in precise mode *Memory at peak snapshot*, *Memory held at exit*, and *Allocated (sampled, full call paths)*, which shows which call paths allocated memory, including the caller that keeps it.
- **Direction:** top-down (callers above callees) or bottom-up, where each top box is a function in which time was spent or memory allocated, merged across its call lines, with its callers below.
- **Frames:** group consecutive library or internal frames into one box (default), show every frame, or show only your code with library time counted in your calling function.
- **Thread** and **Find** filter the chart. Click a frame to zoom; Ctrl/Cmd-click opens your source. Calls too narrow to read merge into one striped `+N smaller calls` box, a breadcrumb shows where you are, and **Hottest path** zooms to the most expensive chain.
- **Top functions** below the chart ranks functions by their time summed across all call paths (self, total, memory for your own functions, callers). Select one to see its callers and callees, or to focus the chart on its stacks.

Native timing is an estimate at the Python call site; C/C++ frames are not captured. Across threads, sampled time can exceed the run duration.

### Compare

Answers "did my change help?":

1. Run **Python Memory Guardian: Save Profile as Baseline** (or the tab's **Save this run as a baseline** button). A copy of the loaded profile goes to `.pmg/baselines/<name>.json`, with the git commit and whether the tree had uncommitted changes.
2. Change your code, then profile again with the same workload and mode.
3. In **Compare**, pick the baseline (or any other profile file).

It shows the whole run, each function, memory stacks by allocating function, and lines, as baseline → this run. Functions match by file and name, so moved lines still match. Only changes beyond run-to-run variation are marked better or worse; changes caused by another part of the run (the memory peak moving elsewhere, or memory no longer reused) are marked *context*. The tab warns when the runs differ in Python version, memory mode, traceback depth, sampling interval, platform, script arguments, or traced function. The thresholds are in the [technical description](07-technical-design.md#510-comparison-of-two-runs).

## Inline labels

Labels appear at the end of measured lines while the file still matches the profile:

| Label | Meaning |
|---|---|
| `⏱ 0.61 s 8.8% (native 96%)` | time on this line, its share of the run, and the Python / native / system split |
| `~` after the time | the run used precise mode, where tracing slows allocation-heavy code; read timings from a fast run |
| `▲ alloc 140 MB → scratch` | net growth of traced memory while this line ran, summed over the run, and the variable it assigns. Memory allocated and freed between two samples is not counted |
| `▲ held 45 MB` | memory from this line still alive at the peak snapshot |
| `▲ spike 160 MB` | a short-lived peak that came and went between two samples |
| `▲ native ≈ +127 MB` | precise mode: process memory growth beyond Python objects and `tracemalloc`'s own bookkeeping on this line, an estimate of native memory from any C extension (NumPy, PyArrow, Polars and others). Not shown when the run as a whole has none beyond tracing overhead, or when only one function was traced |
| `▲ RSS +22 MB` | fast mode: process memory growth while this line ran. It is charged where memory is first written, not where it was allocated |
| `⚠ leak: 45 MB held by Service.history` | suspected growing retention and an observed holder |
| `Σ Service.handle(): 3.7 s …` | totals for the whole function, shown on its `def` line |

**Suspected leaks** also appear in the Problems panel as warnings:

```
Service.handle() › `self.history.append` — ⚠️ Suspected memory leak: … 45.0 MB is still
referenced by `Service.history` (list, 30 items) when profiling ended.
```

Run **Toggle Profile Overlay** to hide or show labels, and **Clear Profile** to clear the active report and overlays.

## How a profile changes warnings

- A **hot** line (at least 5% of runtime, at least 50 MB, or leaking; see the `hotShare` and `hotMB` settings) is raised one severity level, and its message starts with `🔥 Measured in last profile: …`.
- A **cold** line is lowered to a hint, but only if it sits outside every function that was sampled. A line can run between samples, so "no samples" alone is never treated as cheap.
- If you **edit a file** after profiling it, its profile is ignored for that file (the status bar shows *PMG profile stale — re-run*), because line numbers would be wrong. Undoing the edit restores it. Re-run the profile after changing code.

## Run summary

Every profiling run, in any memory mode, also writes **`.pmg/summary.json`** next to `.pmg/profile.json`: a machine-readable summary for scripts and AI agents working in the project, so they do not need to read the report. It holds the run details, totals, the top functions and lines by time and by memory, memory stacks and retention findings (precise mode), the largest objects at exit, the phases, whether precise mode traced only one function, the static warnings for profiled files that are open in the editor (with whether each line was hot in this run), each file's hash and whether it still matches the profile, the comparison with the selected baseline, and suggested next steps. Every section says how it was measured and what it cannot show, and every value is copied from the profile so it can be traced back. The summary is rewritten when those inputs change (new static warnings, an edited file, another baseline) and removed with the profile. The format is `pmg-summary/1`, described by [pmg-summary.schema.json](pmg-summary.schema.json). The standalone profiler does not write a summary.

## Commands and settings

| Command | What it does |
|---|---|
| **Profile Current File** | Runs the profiler on the current file (also the pulse icon in the editor title bar) |
| **Open Profile Report** | Opens the report of the loaded profile (also the status bar item) |
| **Open Saved Profile Report** | Opens a profile JSON file that you select |
| **Visualize This Report** | Opens the report of the profile JSON open in the editor (also the graph icon in its title bar) |
| **Save Profile as Baseline** | Keeps a copy of the loaded profile for the Compare tab |
| **Toggle Profile Overlay** | Shows or hides the inline labels |
| **Clear Profile** | Removes the loaded profile from the editor and the report |
| **Restart Server** | Starts the language server again |

All commands start with **Python Memory Guardian:** in the Command Palette.

| Setting | Default | What it does |
|---|---|---|
| `pythonMemoryGuardian.interpreter` | `""` | Interpreter to probe and profile with (and to run the Python backend). Empty = `python3`/`python` on PATH |
| `pythonMemoryGuardian.backend` | `python` | `python` or `rust`. Same rules and messages; Rust needs no Python on the machine running the server |
| `pythonMemoryGuardian.profile.memoryMode` | `fast` | Memory mode offered first when profiling |
| `pythonMemoryGuardian.profile.hotShare` | `0.05` | Runtime share that makes a line hot |
| `pythonMemoryGuardian.profile.hotMB` | `50` | Memory (MB) that makes a line hot |
| `pythonMemoryGuardian.profile.frames` | `2` | Precise mode: traceback depth. Higher ties more library-internal memory back to your lines, but is slower (on `import pandas`: 2 frames 1.3 s, 8 frames 2.9 s, 32 frames 7.5 s) |
| `pythonMemoryGuardian.profile.monitoring` | `off` | Optional Python 3.12+ line-event coverage (`lines`); may add overhead, with sampling retained for timing |
| `pythonMemoryGuardian.container.execPrefix` | `[]` | Container mode only, see [container setup](03-container-setup.md#docker-or-compose-with-vs-code-on-the-host) |
| `pythonMemoryGuardian.container.interpreter` | `python3` | Container mode only: Python inside the container |
| `pythonMemoryGuardian.container.pathMappings` | `[]` | Container mode only: host ↔ container folders |
| `pythonMemoryGuardian.trace.server` | `off` | Log LSP traffic to the output channel (`messages` / `verbose`) |

## Troubleshooting

| Symptom | Fix |
|---|---|
| No warnings at all | Check **View → Output → Python Memory Guardian** for startup errors. Make sure the file's language mode (bottom-right of the window) is *Python* |
| `Interpreter probe failed` in the output | `pythonMemoryGuardian.interpreter` doesn't point at a working Python; warnings fall back to version-neutral wording |
| `Rust server binary not found` | the installed package has no Rust binary for this platform; switch `pythonMemoryGuardian.backend` back to `python` |
| Profile command does nothing | Save the file and make sure it runs as a script |
| Status bar says *profile stale* | you edited the file after profiling it; profile again |
| No inline labels after a run | the profile is stale, or only files under the opened folder count as your code (library and standard-library frames are excluded). For containers, check the path mappings |
| Precise mode is slow | expected for allocation-heavy code. Use fast mode for timing, a smaller input, or precise mode for one function |

For container and path issues, see [container and remote setup](03-container-setup.md#troubleshooting).
