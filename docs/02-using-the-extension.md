# Using the extension

Python Memory Guardian combines static warnings with runtime measurements. Use the warnings to choose what to inspect, then profile a representative workload to see where time and memory actually go.

## Interpreter and backend

Set `pythonMemoryGuardian.interpreter` to the interpreter your project uses. The default Python backend runs its language server and profiler with that interpreter. The optional Rust backend uses a packaged native server for static analysis; profiling still uses Python. Select it with `pythonMemoryGuardian.backend: "rust"` only if the installed package contains the matching Rust binary.

The interpreter probe measures details such as object sizes and GIL state. If it fails, diagnostics use wording without those measurements. Check **View → Output → Python Memory Guardian** for probe and server errors.

## Static warnings

Open a Python file to receive diagnostics while editing. The [rules table](../README.md#41-rules) lists their codes and triggers. They cover whole-result reads, per-row objects, string growth, cache and collection patterns, reference cycles, unmanaged file handles, discarded asyncio tasks, and blocking calls in async functions.

A diagnostic describes a likely cost or risk in a particular syntax pattern. It cannot know input size, ownership across modules, or whether a growing collection is intentional. To suppress one finding, put `# memory-guardian: ignore` on its line:

```python
rows = cursor.fetchall()  # memory-guardian: ignore
```

## Profile a saved script

Run **Python Memory Guardian: Profile Current File** or use the editor's pulse icon. The file must be saved and runnable as a script. The extension saves changes before starting, runs the program in a terminal, and writes `.pmg/profile.json` under the workspace folder.

| Mode | What it records | Tradeoff |
|---|---|---|
| **fast** | Sampled time, call stacks, and RSS growth | Lower overhead; RSS cannot identify individual retained objects |
| **precise** | Per-line allocations, retained memory, suspected growth, and holder evidence | Tracing can slow allocation-heavy programs |
| **time only** | Sampled time and call stacks | No per-line memory display; the profiler may still record process RSS fields |

Use **fast** for timing comparisons and **precise** when investigating who retains memory. The profiler reports evidence and likely causes; memory growth alone does not establish a leak. You can stop a run with **Ctrl+C** and inspect the partial profile if one was saved.

## Read the results

The **Memory Guardian Report** opens on **Overview**, with run totals, a chart of process RSS over time (plus traced Python memory in precise mode), and bars for the top sampled lines. Click a line to open its source when the file still matches the profile. The chart is hidden in time-only mode. A high RSS value at exit does not by itself mean objects are still held; compare it with the **Memory diagnosis** tab's exit snapshots. That tab separates suspected growing retention, memory held at the end, and memory released during the run. **Stack Explorer** shows sampled call stacks and function totals, colored by origin (your code, installed packages, the standard library, Python internals) and described in plain words when you hover a box. Use **Frames** to group library and Python-internal frames (the default), show all frames, or show only your code. Inline labels mark measured time, allocations or RSS growth, and suspected holders. Open the loaded report again with **Python Memory Guardian: Open Profile Report** or the status bar item. For a saved JSON report already open in the editor, click the graph icon in its title bar. The extension validates it and opens the report beside the JSON; unsaved edits are visualized as shown. **Python Memory Guardian: Open Saved Profile Report** remains available to select a JSON file that is not open yet.

Profiling can change a static diagnostic's severity: measured hot lines become more prominent, while eligible cold lines become hints. After a source edit, the old profile is marked stale so measurements are not attached to the wrong lines. Re-run the profile after changing code.

Use **Python Memory Guardian: Toggle Profile Overlay** to hide or show inline labels and **Python Memory Guardian: Clear Profile** to clear the active profile.

## Useful settings

| Setting | Default | Purpose |
|---|---|---|
| `pythonMemoryGuardian.interpreter` | empty | Interpreter used for the Python server, probe, and profiler |
| `pythonMemoryGuardian.backend` | `python` | Static-analysis server: `python` or packaged `rust` |
| `pythonMemoryGuardian.profile.memoryMode` | `fast` | Mode shown as the default in the profile picker |
| `pythonMemoryGuardian.profile.frames` | `2` | Traceback depth in precise mode; higher values cost more |
| `pythonMemoryGuardian.profile.monitoring` | `off` | Optional Python 3.12+ line-event coverage when set to `lines` |
| `pythonMemoryGuardian.trace.server` | `off` | LSP traffic in the extension's Output channel |

See the [full settings reference](../README.md#17-settings-reference) for heat thresholds and container settings.

## Troubleshooting

| Symptom | Check |
|---|---|
| No warnings | Open a Python file, confirm the interpreter, then inspect the extension's Output channel |
| Profile command does nothing | Save the file and make sure it can run as a script |
| No inline labels after a run | Check whether the profile is stale or its paths match the workspace; for containers, check path mappings |
| Precise mode appears slow | Compare timing with a fast run; allocation tracing has overhead |

For container path issues, see [container and remote setup](03-container-setup.md). For detailed behavior and limits, see the [README](../README.md).
