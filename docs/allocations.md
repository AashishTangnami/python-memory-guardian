# Allocator capture

The default `sampled` backend uses no optional dependency. It records process
observations and, in precise mode, tracemalloc snapshots. Allocator request
events are unavailable in this backend.

Install `memray==1.20.0` in the target Python on Linux or macOS to use:

```bash
python src/python/pmg_profile.py --allocation-backend memray --memory fast --out .pmg/profile.json app.py
```

The editor has `pythonMemoryGuardian.profile.allocationBackend` and
`pythonMemoryGuardian.profile.diagnostics` settings. Container runs stage both
helper modules beside the profiler. Install Memray inside the container.
An unavailable selected backend fails before target execution; it does not
silently fall back.

`allocation_backend` contains capture metadata, hashed artifact paths, bounded
sites, Python and native stacks, thread identity, requested bytes, outstanding
blocks and timestamped memory snapshots. Source roles distinguish an allocating
Python frame from a traced user call site. RSS and tracemalloc data remain
separate. The report and summary preserve these distinctions.

Python allocator, system allocator and interpreter arena totals can overlap.
Do not add them. Requests are not object counts. Outstanding blocks at capture
end do not prove a leak. Memray thread IDs are capture IDs, not OS thread IDs.
Individual allocation timestamps are unavailable; event sequence and capture
times are available. Native hooks have partial coverage. Direct function
pointers and custom allocators can bypass them. Fork traces are separate;
spawned interpreters need their own capture. Native symbol lookup depends on
libraries on the capture machine.

A symbolic link at the capture file's name is removed before capture starts, so Memray never writes through it. Fork traces use names that Memray chooses, which the profiler does not check.

The summary limits sites, frames, snapshots and capture files. The backend trace
and reader internals have additional costs. Full capture can produce large
artifacts; measure its cost on the intended workload. See the
[Memray API](https://bloomberg.github.io/memray/api.html) for backend semantics.

Instrumentation frames have profiler scope even when the helper lives outside
the target root. Installed-package and virtual-environment frames inside a
workspace do not become target frames; the next captured user call site can
provide provenance. Conservative profiler scope can include a factory called
through the diagnostic helper. Its captured stack remains available for review.

`tests/profiler/allocation_backend_test.py` proves independent positive and
corrected witnesses before capture assertions. Missing optional dependencies
are incomplete, never passing acceptance cases.

`--allocation-detail system` disables individual Python allocator interception.
It preserves system allocator requests and native call stacks. Python allocator
event coverage becomes unavailable. The default `full` mode retains both layers.
The editor uses `pythonMemoryGuardian.profile.allocationDetail`. This tradeoff
does not change the timing/memory mode or turn outstanding blocks into leaks.

The reader skips free-event enumeration and aggregates domain/scope request
totals from stored sites once. Overflow still contributes exact request totals,
but `summary_complete` is false. Backend read costs remain outside these bounds.

Report notes identify allocator and cooperative-observer overhead. Cross-run
comparisons warn when capture availability, Python allocator detail, or
cooperative diagnostics differ. Verify timing changes with an uninstrumented run.
