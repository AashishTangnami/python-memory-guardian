# Memory guard

Profiling uses an external supervisor by default. It watches the instrumented
process even if the sampler cannot obtain the GIL. The RSS budget includes the
target, in-process profiler, and capture-analysis memory. It is not a measurement
of target-owned Python objects alone.
`supervisor_peak_rss_bytes` separately records the supervisor's OS peak RSS,
including report decode. It is not subtracted from target-process RSS. An
unavailable counter is null.

The automatic budget is 25% of initially available memory. Linux also considers
finite visible cgroup v2 limits and ancestor usage. The reserve defaults to
512 MiB, bounded to half initial availability. `--memory-limit-mb` can make the
budget smaller; it cannot enlarge the automatic budget. A budget below 64 MiB
refuses execution. Available memory is checked again during the run.

```sh
python src/python/pmg_profile.py --memory-limit-mb 512 app.py
python src/python/pmg_profile.py --memory-guard off app.py
```

The matching settings are `pythonMemoryGuardian.profile.memoryGuard`,
`memoryLimitMiB`, and `memoryReserveMiB`. Container runs stage the `pmg_profiler`
folder, which holds `guard.py`, beside `pmg_profile.py` and use container-side memory observations.
The container-path test stages these files with the extension's own `stageProfiler`.

The supervisor waits 100 ms between checks of RSS and remaining memory; OS
observation time adds to that interval. It stops
the family when RSS reaches the budget or available memory reaches the reserve.
POSIX runs have a separate process group; descendants in that group remain
monitored after the root exits. Group RSS may count shared pages more than once.
Windows observations currently cover only the root process. Processes that
escape the group are not covered.

Linux additionally sets `RLIMIT_AS` before target execution. Its acknowledgement
records the applied byte limit. The child skips the limit when its address
space already maps half the budget or more (a free-threaded build's mimalloc
reserves about 1 GiB at startup): such a limit would refuse new mappings, the
sampler's thread stack first, long before RSS reaches the budget. `hard_limit`
is then `null` and only the polled RSS budget applies. This limits each process's virtual address
space, not combined family RSS. It can refuse a virtual mapping before RSS
reaches the budget. macOS and Windows have no equivalent hard limit in this
implementation. RSS polling can overshoot, especially for sudden native
allocation bursts. This is not a universal guarantee against system OOM.

The guard exits with code 3 on a pressure or observation stop, or 130 on user
interruption. It saves `<out>.guard.json` and a profile with `memory_guard`.
Without a completed profile, source attribution, Python allocation metrics and
exit retention are unavailable. The remaining RSS evidence has process scope.
An arbitrary nonzero exit does not prove an OOM. A budget stop does not prove a
leak or that the program's behavior is intrinsically unsafe. Comparisons of
incomplete guarded runs show context, not optimization verdicts.
Profiles and cooperative findings record the public invocation as their
reproduction command. Internal child arguments stay out of that command; the
child receives the public command before it installs the diagnostic recorder.

Raw capture paths are preserved with unverified integrity. Abrupt termination
can leave incomplete binary captures. The supervisor limits report decoding to
8 MiB and preserves larger raw reports instead of loading them into memory.
An unavailable memory reader refuses to start the target; a reader failure
during execution stops the family. cgroup v1 automatic budgeting is currently
unsupported and refuses host-only capacity estimates.

`memory_guard_test.py` first measures retained growth and a corrected workload
independently, then checks protection and corrected execution. It also tests GIL
starvation, native mapping growth, child-process pressure, invalid budgets, abrupt exit and unavailable
observations. `test_memory_guard.js` checks evidence validation, comparison
semantics and container artifact paths. Skipped platform cases are incomplete.
On free-threaded builds the corrected-run ceiling is 96 MiB per process instead
of 64 MiB: mimalloc keeps freed pages resident, but the amount stays flat as the
work grows, so the run is still bounded and stays below the 256 MiB test budget.
Controlled-counter tests verify reserve and reader-loss policy with real child
cleanup. These tests do not claim to exhaust the host's available memory.
Pressure witnesses fill retained buffers with data: payload size alone does not
establish physical RSS growth for zero-filled pages. The tests verify OS peak
RSS before treating a workload as a positive pressure case. Growth witnesses
require the small input below the budget, the large input above it, three times
the retained payload, and a fixed variant below the budget. They do not require
a fixed payload-to-RSS conversion: residency is an OS observation.
The full-trace accuracy workloads and the 300,000-dictionary bookkeeping regression explicitly disable the guard to
obtain complete attribution traces. Their workloads and memory assertions remain
unchanged. Separate guard fixtures verify resource-budget stops, including
profiler costs; a budget stop is not a failure of allocation attribution.
