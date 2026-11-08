# Using the extension

Profiling enables an external [memory guard](memory-guard.md) by default. It
derives a budget from available system/container memory, includes in-process
profiler costs, and preserves incomplete evidence on a budget stop. Configure
`pythonMemoryGuardian.profile.memoryGuard`, `memoryLimitMiB` and
`memoryReserveMiB`. A stopped run does not establish a leak or an optimization.

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
   | **fast** (default) | Sampled time, call stacks, process memory (RSS) and Python GC callbacks | Cost depends on the workload and process startup; RSS cannot identify allocating lines or retained objects |
   | **precise** | Adds retained Python-block allocation tracebacks, recent growth and bounded holder evidence | `tracemalloc` adds substantial allocation overhead; snapshot profiling does not measure total allocation traffic |
   | **precise, only while `f()` runs** | Retained tracebacks for one function; offered when the cursor is inside it (Python 3.12+) | Lower cost outside `f`; exit retention and whole-run growth/native residuals are unavailable |
   | **time only** | Sampled time, call stacks and Python GC callbacks | Process RSS and Python allocation observations are unavailable |

4. Enter the script's arguments, if any (for example `--records 20000 --mode both`). They are remembered per file; press Enter for none, or Escape to cancel. A smaller input usually shows the same memory pattern in a fraction of the time.
5. Your program runs in a terminal panel. To stop it early, press **Ctrl+C**; a partial profile is still saved.
6. When it finishes, the **Memory Guardian Report** opens and measured lines get inline labels. The data is stored in `.pmg/profile.json` under the workspace folder; add `.pmg/` to your `.gitignore`.

Use **fast** for timing comparisons and **precise** when investigating who retains memory. Memory growth alone does not establish a leak.

On Python 3.12+, set `pythonMemoryGuardian.profile.monitoring` to `lines` to also record which of your lines executed. It can add overhead. It prevents an executed line that the sampler missed from being treated as cold; it does not replace sampled timing. If Python lacks `sys.monitoring` or another tool owns its profiler ID, the report says line-event coverage is unavailable.

## The report

Reopen the loaded report with **Python Memory Guardian: Open Profile Report** or by clicking the status bar item (`PMG 7.01 s · RSS peak 400 MB (fast)`). For a profile JSON open in the editor, click the **graph icon** in its title bar; the extension validates it and opens the report beside it (unsaved edits are shown as they are). **Open Saved Profile Report** selects any schema-2 or schema-3 profile, including output from the standalone CLI. Older schema-2 profiles load without caller stacks and memory trends.

Source navigation from the report works only while the file still matches the profile.

### Overview

Run duration, CPU and sample counts, Python GC collection counts and pauses, top sampled lines, and process RSS over time (plus retained Python bytes in precise mode; the chart is hidden in time-only mode). Unavailable GC evidence is labelled unavailable. A positive **Process RSS residual** is an uncertain process-level difference, not measured native allocations. RSS can stay high after memory is released and has no allocating-line provenance. Click a verified source line to open it. In precise mode, drag across the chart to narrow **Memory diagnosis** to that time window.

**Phases** splits the run by what the main thread was doing: contiguous stretches in one function at a chosen depth of its call stack (picked automatically; **Shallower** and **Deeper** change it). Each phase shows its duration, peak traced memory, peak RSS and **new RSS**: process memory the phase needed beyond what earlier phases had already made resident. That is how to compare two variants run in one process: on `generators.py`, both eager phases needed over 300 MB of new RSS and every streaming phase needed none, although total RSS stayed high. A phase's **Memory diagnosis** button narrows the diagnosis to it, and **Focus stacks** limits the Stack Explorer and Top functions to stacks through that function.

### Memory diagnosis

In precise mode, findings are grouped as **suspected growing retention**, **retained at the last observed snapshot**, and **released during the run**. A warm-up followed by a stable plateau does not count as continued growth. Each shows memory trends, named holders where found, and investigation steps. Growth and cycles alone do not prove a leak. After tracing loss or scoped tracing, exit retention is unavailable. Both memory modes list objects found by a bounded ownership scan; a completed scan does not imply a complete ownership graph.

Profiles with cooperative diagnostics also show capability states and evidence cards with workload, source, confidence, saved trace and reproduction command. A confirmed concurrency conflict requires observed conflicting access; static candidates remain potential. Cooperative coverage only includes explicitly instrumented operations. See the [CLI](reference/cli.md) for this opt-in mode.

### Stack Explorer

Aggregated Python call stacks, including library frames. Boxes are colored by origin (your code, each installed package, the standard library, Python internals such as the import system) and described in plain words when you hover, for example `_find_and_load · Python import system — loading a module (import)`, with total time, share of the run, and whether the time was spent in the function itself or in what it calls.

- **Measure:** sampled time (all, Python, native, waiting, unclassified), or precise retained bytes at the peak and last observed snapshots. The last table is labelled *Memory held at exit* only when an exit snapshot exists. Total allocation-event volume is unavailable; legacy sampled-growth stacks are suppressed.
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
| `▲ held 45 MB` | memory from this line still alive at the peak snapshot |
| `⚠ suspected growing retention: 45 MB held by Service.history` | recent continued growth and a possible owner; this does not confirm an unintended leak |
| `Σ Service.handle(): 3.7 s …` | totals for the whole function, shown on its `def` line |

**Suspected leaks** also appear in the Problems panel as warnings:

```
Service.handle() › `self.history.append` — ⚠️ Suspected memory leak: … 45.0 MB is still
referenced by `Service.history` (list, 30 items) when profiling ended.
```

Run **Toggle Profile Overlay** to hide or show labels, and **Clear Profile** to clear the active report and overlays.

## How a profile changes warnings

- A **hot** line (at least 5% of sampled runtime, at least 50 MB retained at the peak snapshot, or suspected growing retention; see `hotShare` and `hotMB`) is raised one severity level. A measured hotspot does not change a static prediction into a confirmed issue. Process RSS, native residuals and legacy sampled allocation growth do not determine source memory heat.
- A **cold** line is lowered to a hint, but only if it sits outside every function that was sampled. A line can run between samples, so "no samples" alone is never treated as cheap.
- If you **edit a file** after profiling it, its profile is ignored for that file (the status bar shows *PMG profile stale — re-run*), because line numbers would be wrong. Undoing the edit restores it. Re-run the profile after changing code.

## Run summary

The extension writes **`.pmg/summary.json`** next to `.pmg/profile.json` for each profiling run. It contains run details, process observations, retained snapshot evidence, GC metrics, cooperative diagnostics when enabled, source freshness, comparisons and next steps. Findings carry category, severity, confidence, workload, source and reproduction metadata. Each section states its method and limits. Unsupported observations remain null and capability states explain why. Last observed bytes are separate from exit bytes; exit comparisons are omitted without an exit snapshot. The summary follows `pmg-summary/1`, described by the [summary schema](pmg-summary.schema.json) and [profile contract](reference/profile-format.md#summary-contract). It is refreshed with editor inputs and removed with its profile. The standalone profiler writes the profile and diagnostic trace; the extension writes the summary.

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
| Error says `….pmg is a symbolic link or a file, not a folder` | the extension writes profiles, summaries and baselines only into a real `.pmg` folder in your project, so it never writes through a link to another place. Remove the link (or file) named `.pmg`; the next profile creates the folder again |
| Report note says time was not sampled (free-threaded Python) | on a free-threaded build, such as 3.13t, the profiler cannot read other threads' state safely, so it records memory but no time per line, stacks or phases. Profile with a standard (GIL) build to see time |
| `Rust server binary not found` | the installed package has no Rust binary for this platform; switch `pythonMemoryGuardian.backend` back to `python` |
| Profile command does nothing | Save the file and make sure it runs as a script |
| Status bar says *profile stale* | you edited the file after profiling it; profile again |
| No inline labels after a run | the profile is stale, or only files under the opened folder count as your code (library and standard-library frames are excluded). For containers, check the path mappings |
| Precise mode is slow | expected for allocation-heavy code. Use fast mode for timing, a smaller input, or precise mode for one function |

For container and path issues, see [container and remote setup](03-container-setup.md#troubleshooting).
