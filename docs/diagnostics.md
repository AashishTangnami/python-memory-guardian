# Cooperative runtime evidence

`src/python/pmg_diagnostics.py` supplies optional explicit observations for a profiling run. It uses only the standard library. The profiler starts a recorder with `--diagnostics` and writes its saved events to `<profile path>.events.jsonl`. If a file or a symbolic link is already at that path, the recorder removes it and creates a new file, so it never writes through a link. The target program must call the APIs for paths that need these observations. An absent call does not prove that a program has no issue.

The module retains immutable event data and weak references. It does not retain inspected objects or Python frames. It bounds saved events, findings, labels, graph nodes and graph depth. Dropped observations appear in the result. A finding cannot refer to a dropped event.

Each event has a monotonic timestamp, elapsed time, process ID, thread ID, asynchronous task identity where available, source location and stack. Each finding includes a category, severity, confidence, measured or projected status, workload, source, saved event IDs and reproduction data. The profiler supplies the command and script arguments. If a caller starts its own recorder, it must supply that reproduction data in `workload`.

## Workload growth and retention

```python
import pmg_diagnostics as evidence

evidence.checkpoint("history", {"rows": 1000}, {"service": service})
# The program runs another declared workload here.
evidence.checkpoint("history", {"rows": 2000}, {"service": service},
                    memory_budget_bytes=64_000_000)
```

Two checkpoints with the same label can produce a measured growth finding. The evidence gives both workload conditions and both observed Python footprints. The footprint is the sum of shallow sizes of distinct objects in the bounded graph. The graph includes built-in containers, ordinary instance dictionaries and native slot descriptors. It excludes native buffers and custom container internals. The observer does not call Python properties to discover owners. Both visited nodes and pending nodes are bounded. `complete` describes this graph search; it does not assert that all Python references are visible.

A declared budget can produce a **projected** OOM risk when the observed footprint exceeds it after growth. The module does not report that an OOM occurred. It does not infer unbounded growth from two points.

```python
evidence.watch(request, "request")
# The program drops its expected references here.
evidence.operation_cleanup("request completed", {"service": service},
                           expected_released=("request",))
```

`watch()` supports objects with weak references. It reports an unavailable observation for other objects. A cleanup finding says that the object remains alive after the caller's declared cleanup point. Its reference path comes from the supplied owner roots. An unavailable path is `null`. A retained object is not automatically a confirmed leak. The finding records repeated retention observations when the caller repeats cleanup.

The root graph can identify reachable cycles. A cycle finding has informational severity and `leak: false`. Its collectibility is conditional on removal of roots. After the program runs `gc.collect()`, `confirm_collection(label, collected)` can check watched weak references and the current GC object registry. A dead weak reference alone does not prove that a finalizer released its object. Confirmation requires a previously GC-tracked object and checks that the object ID is absent from the GC registry. The registry check is unavailable for objects that were not GC-tracked. Identity reuse can affect an extremely long observation interval; keep checks close to the cleanup point.

`confirm_collection()` also records `gc.garbage`. It reports uncollectable objects only when `DEBUG_SAVEALL` is off. The profiler's GC event recorder owns automatic collection frequency, pause duration, collected count and uncollectable count. This module does not force collection.

## Allocation, copies and structures

`allocate(size, factory=bytearray, label="allocation")` calls the factory once. It records the requested payload and the returned object's shallow size. Totals preserve repeated requests even if each result is immediately freed. **Requested payload is not allocator volume.** This API does not intercept Python allocation events, native allocator calls or process RSS.

`copy_value(source, producer, label="copy", requirements=None)` calls the producer once and returns its result. It records the result's bounded Python footprint. Repeated distinct results of at least 64 KiB can produce a copy finding. The footprint excludes buffers outside Python's size reports. A view does not count as a materialized payload copy merely because it is a separate Python object.

`structure(value, label="structure", requirements=None)` records object counts and shallow bytes. It reports a cost finding for at least 1,000 dictionaries that together use at least 100,000 shallow bytes. This threshold is an observation rule, not proof that the structure is unnecessary. `measure_traversal(value, visitor, ...)` runs the caller's visitor once, returns its result and records its wall time. It measures the actual visitor; it does not use graph scan time as traversal cost.

Callers can document required ordering, mutability, null handling and access patterns with `requirements`. The APIs do not suggest an alternative without an equivalence proof. The current findings leave `alternatives` empty.

## Thread, process and asynchronous conflicts

Record `shared_read(key, value)` immediately after an actual shared read. Record `shared_write(key, value)` immediately after an actual committed write. After all actors finish, call `shared_outcome(key, observed, expected)` with independently checked final values. The declared key must identify the same shared state in each actor.

The analyzer can confirm a **logical lost update** only when two different actors read the same literal value, their unsynchronized operations overlap, both write the same changed value, and the final observed value differs from the independently declared expected value. The evidence contains both reads, both writes and the final outcome. This is not a native C or CPU data race claim. Other observed shared unsynchronized access remains **potential**.

`critical_section(lock, key)` acquires the supplied lock and releases it on exit. It records actual lock identity. `async_critical_section(lock, key)` does the same for `asyncio.Lock`. The whole read-modify-write sequence must lie inside the context. Locks that cover only a read or only a write do not protect the full operation.

Events distinguish threads from asynchronous tasks. Process workers must use `actor("process", identity)`, start their own recorder and save a distinct trace path. `merge_traces(paths, workload=None)` analyzes parent and child traces together. Merge only traces from the same host: their timestamps use the host monotonic clock. The merged findings identify every source trace. The profiler does not automatically instrument child processes.

## Validation and limits

Run `python3 tests/profiler/diagnostics_test.py`. The tests first demonstrate each problematic program without a recorder, then verify its finding, then demonstrate the corrected program and verify that the same finding disappears. The concurrency fixtures reproduce 20 completed increments from 40 attempted increments in threads, spawned processes and asynchronous tasks; the corrected critical sections produce 40.

The legacy uncollectable fixture requires CPython's `_testcapi.with_tp_del`. Its test is explicitly skipped as unsupported when that API is absent. Such a result is incomplete and does not count as a pass. The modern collectible-cycle fixture uses ordinary Python objects and is separate.

Coverage remains partial. Native allocation events, implicit allocation churn, arbitrary owner graphs and unannotated concurrent reads or writes are unavailable. Fast mode does not recover these observations. The report exposes capability status and the reason for each limit.

CLI JSONL traces use unique capture names and carry `trace_sha256` when the artifact is readable at close. A missing or removed artifact has an unavailable hash. Consumer validation requires referenced reads and writes by at least two actors for the same key, a logical-lost-update category, and a mismatching outcome that matches the recorded event. Native data races are never confirmed by this API. The producer checks overlapping access and synchronization; consumer validation checks the saved evidence contract.

Graph scans also avoid user `repr` and `__sizeof__` overrides and bound temporary key labels. An opaque key may have an unnamed path. These limits preserve observation safety rather than asserting a complete owner graph.
