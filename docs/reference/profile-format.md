# Profile format (`profile.json`)

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
| `run` | object | all | `argv`: the script arguments. `started_at`: start time, UTC, ISO 8601. `platform`: `sys.platform` |
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
| `sampler_error` | string | all | An error that stopped the sampler early. `null` if none |

## Memory totals

| Field | Type | Mode | Contents |
|---|---|---|---|
| `rss_kind` | string | all | How the profiler read RSS: `"current"` (Linux, macOS, Windows), `"peak"` (only the highest value so far is available), or `null` |
| `rss_start_mb`, `rss_end_mb`, `rss_peak_mb` | number | all | Process memory (RSS) at the start, at the end, and its highest value at a sample or at the end |
| `peak_traced_mb` | number | P | Highest traced Python memory |
| `tracemalloc_peak_mb` | number | P | Largest bookkeeping of `tracemalloc` itself. This is profiler memory, not memory of your program |
| `native_untraced_mb` | number | P | Estimate of native memory: RSS growth to the peak, less the traced peak and the bookkeeping. `0` if it is not more than two times the bookkeeping. `null` with `--trace-function` |
| `unattributed_peak_mb` | number | P | Memory at the peak snapshot whose traceback has no frame of your code |
| `snapshots`, `snapshot_cost_s` | integer, number | P | Number of snapshots and their total time. This time is in `wall_s`, but not in the time of any line |
| `peak_snapshots`, `peak_snapshot_cost_s` | integer, number | P | Number of extra snapshots taken to capture the peak (memory at a new high level, or doubled), and their time |

## Timeline

| Field | Type | Mode | Contents |
|---|---|---|---|
| `timeline` | array | all | Up to 300 points `[t_s, traced_mb, rss_mb]`, evenly spaced, in time order. The last point is after the script ended. `traced_mb` is 0 outside precise mode |
| `timeline_stacks` | array | all | One value for each timeline point. It is the index in `stacks.samples` of the call stack of the main thread at that time. It is `-1` if the main thread had no frame of your code. The report uses it for phases |

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
| `rss_growth_mb`, `rss_release_mb` | all | Increase and decrease of RSS while this line ran (the busiest thread's line at each sample) |
| `alloc_mb` | P | Net increase of traced memory while this line ran, added over the run |
| `peak_mb` | P | Memory from this line that was in use at the peak snapshot (`held` in the labels) |
| `end_mb` | P | Memory from this line that was in use at the last snapshot |
| `transient_peak_mb` | P | Largest short peak between two samples (`spike` in the labels) |
| `profiler_mb` | P | Increase of `tracemalloc` bookkeeping while this line ran |
| `leak_runs` | P | Present for a suspected leak: the number of increases in the last snapshots |
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
| `alloc_mb`, `peak_mb`, `rss_growth_mb`, `profiler_mb` | Sums of the line values |
| `transient_peak_mb` | The largest short peak of its lines |

## Call stacks (time)

`stacks` has the sampled call stacks of all threads.

| Field | Contents |
|---|---|
| `frames` | Array of frames. Each frame has `file`, `line`, `name`, `first_line`, and `user` (`true` for your code) |
| `samples` | One entry for each different stack and thread. Fields: `thread` (thread ID), `thread_name`, `frames`, `python_s`, `native_s`, `system_s`, `unsplit_s`, `samples`. `frames` has indexes in `frames`, outermost first, from the outermost frame of your code. In precise mode, `alloc_bytes` is the traced growth while this was the stack of the busiest thread |
| `dropped_s` | Time of samples that the profiler did not keep, because it already had 50,000 different stacks |
| `depth_limited` | `true` if a stack had more than 128 frames |

## Memory stacks

`memory_stacks` (precise mode) has the allocation stacks of two snapshots. `peak` is the snapshot where your code held the most memory (or `null`). `exit` is the last snapshot. With `--trace-function`, `exit` is the end of the last traced call.

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
| `complete` | `false` if the search stopped at its time limit (1 s) |
