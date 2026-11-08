# Profile format (`profile.json`)

`memory_guard` optionally records the external supervisor's initial host/container
availability, budget, reserve, root/group RSS peaks, final pressure observation,
process identities, exit code and stop reason. Linux `hard_limit` is `RLIMIT_AS`
only after the child acknowledges the applied byte limit. Scope and partial
coverage remain explicit. Raw artifacts have unverified integrity. A forced
stop has no exit snapshot or invented Python/native allocation evidence. See
[memory guard](../memory-guard.md) for policy, platform limits and exit codes.
The summary retains this section; stopped or failed guarded runs cannot produce
optimization verdicts in comparisons.

The profiler writes one JSON file for each run, by default `.pmg/profile.json`. This page tells what each field contains. The extension reads this file to show the report and the labels. Scripts can also read it. For a shorter file with the method and the limits of each value, read `.pmg/summary.json` (see the [summary schema](../pmg-summary.schema.json)).

Conventions:

- **MB** is 10<sup>6</sup> bytes. **s** is seconds of wall-clock time. The profiler rounds MB values to 3 decimals and most seconds to 4.
- **Mode** tells in which memory modes a field has a value: F = fast, P = precise, O = off (time only). In other modes, the field is `null` or is not present.
- Paths are absolute. In container mode, they are container paths; the extension changes them to host paths when it loads the file.
- The current version is `"schema": 3`. The extension also reads version 2, which has no call stacks. The extension refuses a file that does not agree with this format, and it keeps the profile that it showed before.

## Run

| Field | Type | Mode | Contents |
|---|---|---|---|
| `schema` | integer | all | Format version: 3 |
| `script` | string | all | The profiled script |
| `python` | string | all | Python version, for example `"3.13.0"` |
| `run` | object | all | `argv`: the script arguments. `started_at`: start time, UTC, ISO 8601. `platform`: `sys.platform`. New runs also include `command` (the exact profiler argument array) and `cwd` (the working directory) |
| `process_id` | integer | all | PID of the profiled interpreter. Child processes need their own traces |
| `memory_mode` | string | all | `"fast"`, `"precise"` or `"off"` |
| `wall_s` | number | all | Run duration, from the start of the script to its end |
| `cpu_s` | number | all | CPU time of the process, all threads |
| `interval_s` | number | all | Time between samples |
| `samples` | integer | all | Number of samples |
| `gil_split` | boolean | all | `true` if the Python build has a GIL, so the profiler can divide CPU time into Python and native time |
| `per_thread_cpu` | boolean | all | `true` if a CPU clock per thread was available (Linux). If `false`, only the main thread has a time split |
| `sleep_overhead_s` | number | all | The normal delay of the sampler timer, measured before the script started. The profiler removes it from each GIL wait |
| `frames` | integer | P | Traceback depth of `tracemalloc` |
| `trace_function` | object | P | Only with `--trace-function`: `name`, `calls` (traced calls), `traced_s` (traced time) |
| `monitoring` | object | all | Line events (`--monitoring lines`): `requested`, `active`, `reason` (why it is not active), `dropped_line_events` |
| `memory_tracing_lost_s` | number | P | Time at which the script stopped `tracemalloc`. Precise data stops there. `null` if it did not occur |
| `stack_sampling` | object | all | `active` and `reason`. `active` is `false` on a free-threaded build, such as 3.13t: the sampler does not read other threads' frames, so lines have no sampled time (`time_s` and `samples` are 0, `func_line` is absent), `functions` is empty, `stacks` has no samples and every `timeline_stacks` entry is -1 (no phases). Per-line memory, retention and `memory_stacks` are still recorded. Absent in older profiles, which always sampled, and in partial profiles that the memory guard writes |
| `sampler_error` | string | all | An error that stopped the sampler early. `null` if none |
| `memory_attribution` | string | all | `"process-observation"`: sampled changes have process provenance. Allocation source paths come only from retained-block tracebacks |
| `memory_snapshot_at_exit` | boolean | P | True only when a whole-program exit snapshot exists. False for lost tracing or function-scoped tracing |

## Memory totals

| Field | Type | Mode | Contents |
|---|---|---|---|
| `rss_kind` | string | all | How the profiler read RSS: `"current"` (Linux, macOS, Windows), `"peak"` (only the highest value so far is available), or `null` |
| `rss_start_mb`, `rss_end_mb`, `rss_peak_mb` | number or null | F, P | Process memory (RSS) at the start, end, and highest observation. Null in time-only mode or when the platform reader is unavailable |
| `peak_traced_mb` | number | P | Highest traced Python memory |
| `tracemalloc_peak_mb` | number | P | Largest bookkeeping of `tracemalloc` itself. This is profiler memory, not memory of your program |
| `native_untraced_mb` | number or null | P | Legacy field name for an uncertain process RSS residual: peak RSS less initial RSS, peak traced bytes and tracer bookkeeping. `null` within the uncertainty margin, with lost tracing, or with `--trace-function`. A positive value is not a native allocation measurement and has no allocating Python line |
| `native_memory` | object | all | `status: "unavailable"`, `reason`, and `rss_residual_mb` (number or null). Native allocation events require a separate allocator backend |
| `unattributed_peak_mb` | number | P | Memory at the peak snapshot whose traceback has no frame of your code |
| `snapshots`, `snapshot_cost_s` | integer, number | P | Number of snapshots and their total time. This time is in `wall_s`, but not in the time of any line |
| `peak_snapshots`, `peak_snapshot_cost_s` | integer, number | P | Number of extra snapshots taken to capture the peak (memory at a new high level, or doubled), and their time |

## Timeline

| Field | Type | Mode | Contents |
|---|---|---|---|
| `timeline` | array | all | Up to 300 points `[t_s, traced_mb, rss_mb]`, evenly spaced, in time order. The last point is after the script ended. `traced_mb` is 0 outside precise mode |
| `timeline_stacks` | array | all | One value for each timeline point. It is the index in `stacks.samples` of the call stack of the main thread at that time. It is `-1` if the main thread had no frame of your code. The report uses it for phases |
| `memory_observations` | array | all | Up to 600 process observations. Each has `t`, `traced_bytes`, `traced_growth_bytes`, `transient_bytes`, `rss_bytes`, signed `rss_change_bytes`, and `bookkeeping_bytes`. Unavailable byte values are null. These observations have no allocating line, thread or object identity |

## Files and lines

| Field | Type | Contents |
|---|---|---|
| `file_hashes` | object | Path → SHA-1 of the file text (CRLF changed to LF), for each file that did not change during the run. The extension compares this hash with the current text. Labels show only when they agree |
| `files` | object | Path → line number (as a string) → line entry. A line that the profiler did not measure is not present |

Line entry:

| Field | Mode | Contents |
|---|---|---|
| `time_s`, `share` | all | Sampled time on this line, and its part of all sampled time (0 to 1) |
| `python_s`, `native_s`, `system_s` | all | The time split: Python bytecode, C code, and time off the CPU (waits for I/O, sleep, locks) |
| `cpu_unsplit_s` | all | CPU time that the profiler could not split (no GIL signal, several threads on the CPU, or no CPU clock for the thread) |
| `samples` | all | Number of samples on this line |
| `rss_growth_mb`, `rss_release_mb` | all | Compatibility fields: new runs write zero. Earlier runs charged sampled process changes to a line; that does not establish allocating-source provenance, and source labels and summaries no longer use those values |
| `alloc_mb` | legacy | Earlier runs charged sampled net traced growth to the busiest line. New runs omit it. It never measured total allocation volume and is suppressed in source labels, heat and comparisons |
| `peak_mb` | P | Memory from this line that was in use at the peak snapshot (`held` in the labels) |
| `end_mb` | P | Memory from this line that was in use at the last snapshot |
| `transient_peak_mb`, `profiler_mb` | legacy | Earlier sampled process peak/bookkeeping values. New runs omit them; the extension does not assign these process observations to source lines |
| `leak_runs` | P | Candidate for suspected growing retention: at least three meaningful rises in the last five snapshots, continued growth in the last two transitions, no meaningful decline, and an exit snapshot. A stable warm-up plateau is excluded. Growth alone does not establish a leak or an OOM |
| `held_by` | P | Possible holders of leaked memory: `holder` (for example `"global LEAK"` or `"Service.history"`), `type`, `items`, `matching` (items that came from this line) |
| `retention` | P | Held memory across snapshots, for lines that held at least 1 MiB in a snapshot. Fields: `peak_mb`, `snapshots`, `rises`, `releases`, `growth_mb`, `observed_s`, and up to 60 `points` `[t_s, mb]` |
| `func_line` | all | First line of the function that contains this line |
| `scope`, `assigns`, `calls` | all | From the source: the function or class that contains the line (for example `"Service.handle()"`), the names that the line assigns, and up to 4 functions that it calls |
| `line_events` | all | With `--monitoring lines`: how many times the line ran |

## Functions

`functions` maps path → first line of the function (as a string) → function entry. The values are the sums of the function's lines.

| Field | Contents |
|---|---|
| `name` | Qualified name, for example `"Service.handle"`. A generator expression is a separate function, for example `"work.<genexpr>"` |
| `end_line` | Last line of the function, from the source |
| `time_s`, `python_s`, `native_s`, `system_s` | Sums of the line values |
| `peak_mb` | Sum of the retained bytes attributed to this function at the peak snapshot |
| `alloc_mb`, `rss_growth_mb`, `profiler_mb`, `transient_peak_mb` | Legacy process changes without function provenance. The extension suppresses them; new runs omit them |

## Call stacks (time)

`stacks` has the sampled call stacks of all threads.

| Field | Contents |
|---|---|
| `frames` | Array of frames. Each frame has `file`, `line`, `name`, `first_line`, and `user` (`true` for your code) |
| `samples` | One entry for each different stack and thread. Fields: `thread` (thread ID), `thread_name`, `frames`, `python_s`, `native_s`, `system_s`, `unsplit_s`, `samples`. `frames` has indexes in `frames`, outermost first, from the outermost frame of your code. Legacy `alloc_bytes` was sampled process growth; the extension suppresses it because it does not identify an allocating thread or stack |
| `dropped_s` | Time of samples that the profiler did not keep, because it already had 50,000 different stacks |
| `depth_limited` | `true` if a stack had more than 128 frames |

## Memory stacks

`memory_stacks` (precise mode) has retained-block allocation stacks at two snapshots. `peak` is the snapshot where your code held the most memory (or `null`). The legacy key `exit` contains the **last observed** snapshot. Check `memory_snapshot_at_exit` before describing it as exit retention. With `--trace-function`, it covers the last traced call; after tracing loss, it precedes loss. Allocation tracebacks do not carry allocating PID, thread or allocation-event timestamps.

| Field | Contents |
|---|---|
| `depth` | Traceback depth |
| `frames` | Array of frames, as in `stacks.frames` |
| `peak`, `exit` | Fields: `t` (snapshot time), `total_bytes`, `other_bytes`, `stacks`. `other_bytes` is the memory in small stacks after the first 20,000. `stacks` has up to 20,000 entries with `frames` (outermost first), `bytes`, and `truncated` (`true` if `tracemalloc` cut the traceback at `depth`) |

## Largest objects

`largest_objects` (fast and precise mode) lists up to 20 objects of 1 MB or more at the end of the run. Global variables or attributes of your objects kept these objects.

| Field | Contents |
|---|---|
| `objects` | Fields: `holder` (for example `"global LEAK"`), `type`, `mb`, `items`, `estimated`. `mb` is the size that the object reports with `sys.getsizeof`, with container items one level deep. `estimated` is `true` if PMG calculated the size of a large container from 10,000 of its items |
| `complete` | Whether the bounded scan finished. This is not a complete Python ownership graph |
| `scope`, `max_depth`, `max_nodes`, `visited`, `truncated`, `sizing` | Additive scan scope, budgets, observed nodes, truncation and size-estimate method. Shared referents can appear under multiple owners; do not sum the rows as process memory |

## Garbage collection and cooperative diagnostics

`gc` contains Python callback measurements: `supported`, bounded `events`, `dropped_events`, `total_collections`, `total_pause_s`, `collected`, and `uncollectable`. Each event has elapsed `t`, `duration_s`, `generation`, `collected`, `uncollectable`, `thread`, `process`, and `traced_bytes` (null without active tracing). Counts cover observed collections, not reference-count releases. A high collection rate alone is not a leak finding.

The optional `diagnostics` object has `schema: 1`, `instrumentation: "cooperative"`, `trace_path`, bounded `events` and `findings`, `dropped_events`, `scan_limits`, and per-category `capabilities` with `status` and `reason`. This mode requires explicit workload instrumentation; it does not observe every operation in arbitrary Python code. Events identify elapsed time, process, thread/task actor, source, stack, category and payload. Findings carry category, severity, confidence, evidence status (`measured`, `confirmed`, `potential`, or `projected`), source, workload, evidence, saved trace and reproduction command. Missing commands or source data remain null. Native allocator events are explicitly unavailable.

## Summary contract

`summary.json` retains the `pmg-summary/1` format and adds process observations, capability states, GC measurements and cooperative diagnostics. Unsupported measurements are **null**, not zero. Source-level `allocated_mb`, `spike_mb`, `native_estimate_mb` and fast-mode `rss_growth_mb` are null because sampling provides no such provenance. Retained snapshot bytes remain source-attributed via tracemalloc.

`memory_stacks.last_snapshot` always preserves the last observed table; `snapshot_at_exit` states its scope. `memory_stacks.exit`, `totals.held_at_exit_mb` and retention finding exit values are null without whole-program exit evidence. Use `held_at_last_snapshot_mb` and its timestamp for incomplete/scoped runs. Retention and static findings include category, severity, confidence, workload, source, evidence and reproduction metadata. Static predictions remain potential even when a measured hotspot raises their severity. See the [summary schema](../pmg-summary.schema.json).

## Optional allocator backend

`allocation_backend` is additive in schema 3. Sampled captures have
`available: false` and a reason. Memray provides bounded sites, process and
capture-thread provenance, stack frames with nullable lines, requested bytes,
outstanding blocks, memory snapshots, availability reasons and hashed artifact
metadata. Domain totals are not additive. Per-event timestamps and object counts
are unavailable. See [allocator capture](../allocations.md). The summary preserves
the section and marks event coverage as partial. Container mapping changes
structured paths while commands and argv retain the capture environment. CLI
runs give binary and JSONL artifacts unique names to preserve earlier evidence.

When sampler RSS is unavailable but Memray records process snapshots, the summary marks RSS coverage partial. Those snapshots remain in the allocator section; sampler totals remain unavailable.
