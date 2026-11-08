"""Turn a finished session's measurements into the profile JSON (docs/reference/profile-format.md)."""
from __future__ import annotations

import os
import sys

from .common import SCHEMA
from .sampler import LineStats


def build_report(p, script: str) -> dict:
    memory, sampler, monitor = p.memory, p.sampler, p.monitor
    residual = (memory.native_estimate(sampler.rss_max, p.rss0) if p.mode == 'precise'
                and sampler.rss_kind == 'current' and not p.trace_function
                and memory.memory_tracing_lost_s is None else None)
    snapshots = memory.snapshots
    peak_idx = max(range(len(snapshots)), key=lambda i: sum(snapshots[i][1].values()),
                   default=None)
    peak = snapshots[peak_idx][1] if peak_idx is not None else {}
    end = snapshots[-1][1] if snapshots else {}
    # Leak claims need an exit snapshot; after tracing was lost the last one is not at exit.
    # Leak claims need an exit snapshot of the whole run: not after tracing was lost, and not when only
    # one function was traced (each call starts tracing afresh, so held memory cannot be followed).
    leaks = memory.leaks() if memory.memory_tracing_lost_s is None and not p.trace_function else {}
    total = sum(s.python_s + s.native_s + s.system_s + s.cpu_s for s in sampler.lines.values()) or 1.0
    files: dict[str, dict] = {}
    funcs: dict[str, dict] = {}
    symbols: dict[str, dict] = {}
    hashes = {}
    retained_locations = {loc for _, held, _ in snapshots for loc, size in held.items()
                          if size >= 1 << 20}
    for loc in set(sampler.lines) | set(peak) | set(end) | retained_locations | set(monitor.events):
        st = sampler.lines.get(loc) or LineStats()
        if loc[0] not in symbols:
            source = p.sources.unchanged(loc[0])
            symbols[loc[0]] = p.sources.symbols(source) if source is not None else {}
            if source is not None:
                # VS Code hashes decoded document text, without a UTF-8 BOM.
                hashes[loc[0]] = p.sources.text_hash(source)
        e = _line_entry(p, loc, st, total, peak, end, leaks, symbols[loc[0]])
        if (st.samples == 0 and loc not in leaks and loc not in p.holders
                and loc not in monitor.events
                and max(e.get("peak_mb", 0), e.get("end_mb", 0), e.get("alloc_mb", 0),
                        e["rss_growth_mb"], e.get('retention', {}).get('peak_mb', 0)) < 0.01):
            continue          # nothing measured on this line: don't pad the report
        files.setdefault(loc[0], {})[str(loc[1])] = e
        if st.func is not None:   # per-function totals: robust to line-level skew
            _add_to_function(funcs, loc, st, e, symbols[loc[0]])
    hashes = {path: digest for path, digest in hashes.items() if path in files}
    timeline_points, timeline_keys = p.timeline.points, p.timeline.keys
    step = max(1, (len(timeline_points) + 298) // 299)
    timeline = timeline_points[::step]
    kept_keys = timeline_keys[::step]
    if timeline and timeline[-1] != timeline_points[-1]:
        timeline.append(timeline_points[-1])
        kept_keys.append(timeline_keys[-1])
    stack_frames, stack_samples, sample_index = _stack_tables(sampler)
    # Caller-only files still need a verified hash for source navigation.
    for frame in stack_frames:
        path = frame['file']
        if frame['user'] and path not in files:
            files[path] = {}
            source = p.sources.unchanged(path)
            if source is not None:
                hashes[path] = p.sources.text_hash(source)
    return {
        **_run_metadata(p, script),
        **_memory_metadata(p, peak_idx, residual),
        "timeline": [[round(a, 3), round(b, 3), round(c, 3)] for a, b, c in timeline],
        # Per timeline point: index into stacks.samples of the main thread's stack then (-1: none).
        "timeline_stacks": [sample_index.get(k, -1) if k else -1 for k in kept_keys],
        "tracemalloc_peak_mb": round(memory.book_max / 1e6, 3) if p.mode == "precise" else None,
        "file_hashes": hashes, "files": files, "functions": funcs,
        'memory_stacks': memory.memory_stacks(p.sources.function_at),
        'largest_objects': p.largest,
        'stacks': {'frames': stack_frames, 'samples': stack_samples,
                   'dropped_s': round(sampler.stack_dropped_s, 6),
                   'depth_limited': sampler.stack_depth_limited},
    }


def _line_entry(p, loc, st, total, peak, end, leaks, line_symbols) -> dict:
    """One line's row in the report's `files` table."""
    memory, monitor = p.memory, p.monitor
    t = st.python_s + st.native_s + st.system_s + st.cpu_s
    e = {"time_s": round(t, 4), "share": round(t / total, 4),
         "python_s": round(st.python_s, 4), "native_s": round(st.native_s, 4),
         "system_s": round(st.system_s, 4), "cpu_unsplit_s": round(st.cpu_s, 4),
         "samples": st.samples,
         "rss_growth_mb": round(st.rss_up / 1e6, 3), "rss_release_mb": round(st.rss_down / 1e6, 3)}
    if loc in monitor.events:
        e['line_events'] = monitor.events[loc]
    if st.func is not None:
        e["func_line"] = st.func[1]
    sym = line_symbols.get(loc[1])
    if sym:
        if sym["scope"]:
            e["scope"] = sym["scope"]
        if sym["assigns"]:
            e["assigns"] = sym["assigns"]
        if sym["calls"]:
            e["calls"] = sym["calls"]
    if loc in p.holders:
        e["held_by"] = sorted(p.holders[loc], key=lambda r: -r["matching"])[:3]
    if p.mode == "precise":
        e["peak_mb"] = round(peak.get(loc, 0) / 1e6, 3)
        e["end_mb"] = round(end.get(loc, 0) / 1e6, 3)
        if loc in leaks:
            e["leak_runs"] = leaks[loc]
        if max((held.get(loc, 0) for _, held, _ in memory.snapshots), default=0) >= 1 << 20:
            trend = [(t, held.get(loc, 0)) for t, held, _ in memory.snapshots]
            # Keep both ends and representative intermediate points.
            indices = sorted({round(i * (len(trend) - 1) / min(59, len(trend) - 1))
                              for i in range(min(60, len(trend)))}) if len(trend) > 1 else [0]
            e['retention'] = {
                'peak_mb': round(max(size for _, size in trend) / 1e6, 3),
                'snapshots': len(trend),
                'rises': sum(b[1] > a[1] for a, b in zip(trend, trend[1:])),
                'releases': sum(b[1] < a[1] for a, b in zip(trend, trend[1:])),
                'growth_mb': round((trend[-1][1] - trend[0][1]) / 1e6, 3),
                'observed_s': round(trend[-1][0] - trend[0][0], 4),
                'points': [[round(trend[i][0], 4), round(trend[i][1] / 1e6, 3)] for i in indices],
            }
    return e


def _add_to_function(funcs, loc, st, e, syms) -> None:
    """Add one line's row to its enclosing function's totals."""
    fname = st.func[0]
    if "." not in fname:
        # Python < 3.11 has no co_qualname: recover "Class.method" from the source.
        # co_firstlineno points at the first decorator, so scan forward to the def.
        for ln in range(st.func[1], st.func[1] + 20):
            sc = (syms.get(ln) or {}).get("scope") or ""
            if sc.endswith("()") and sc[:-2].split(".")[-1] == fname:
                fname = sc[:-2]
                break
    fn = funcs.setdefault(loc[0], {}).setdefault(str(st.func[1]), {
        "name": fname, "time_s": 0.0, "python_s": 0.0, "native_s": 0.0,
        "system_s": 0.0, "peak_mb": 0.0, "transient_peak_mb": 0.0})
    fn['end_line'] = syms.get(st.func[1], {}).get('end_line')
    for k in ("time_s", "python_s", "native_s", "system_s"):
        fn[k] = round(fn[k] + e[k], 4)
    fn["peak_mb"] = round(fn["peak_mb"] + e.get("peak_mb", 0), 3)
    fn["transient_peak_mb"] = max(fn["transient_peak_mb"], e.get("transient_peak_mb", 0))


def _stack_tables(sampler):
    """Turn the sampler's flat stack keys back into frame records."""
    stack_frames: list[dict] = []
    frame_ids: dict[tuple, int] = {}
    stack_samples: list[dict] = []
    sample_index: dict[tuple, int] = {}
    for (ident, name, stack), values in sampler.stack_totals.items():
        sample_index[(ident, name, stack)] = len(stack_samples)
        ids = []
        for i in range(0, len(stack), 2):
            file, func, first_line, user = sampler._codes[stack[i]][:4]   # not `name`: the thread's
            # Merge by content, not code object: a reloaded function is still one frame.
            key = (file, stack[i + 1], func, first_line, user)
            if key not in frame_ids:
                frame_ids[key] = len(stack_frames)
                stack_frames.append(dict(zip(('file', 'line', 'name', 'first_line', 'user'), key)))
            ids.append(frame_ids[key])
        stack_samples.append({'thread': ident, 'thread_name': name, 'frames': ids,
                              'python_s': round(values[0], 6), 'native_s': round(values[1], 6),
                              'system_s': round(values[2], 6), 'unsplit_s': round(values[3], 6),
                              'samples': values[4],
                              })
    return stack_frames, stack_samples, sample_index


def _run_metadata(p, script: str) -> dict:
    """How the run was made and what it could measure."""
    sampler, monitor, memory = p.sampler, p.monitor, p.memory
    return {
        "schema": SCHEMA, "script": os.path.abspath(script),
        "python": "%d.%d.%d" % sys.version_info[:3], "gil_split": sampler.split,
        "wall_s": round(p.wall, 4), "cpu_s": round(p.cpu, 4),
        "interval_s": p.interval, "samples": sampler.samples, "memory_mode": p.mode,
        "stack_sampling": {"active": sampler.stacks_reason is None, "reason": sampler.stacks_reason},
        "monitoring": {"requested": monitor.requested,
                       "active": monitor.enabled,
                       "reason": monitor.reason,
                       "dropped_line_events": monitor.dropped},
        "run": {"argv": p.argv, "started_at": p.started_at, "platform": sys.platform,
                "command": p.command, "cwd": p.cwd},
        "process_id": os.getpid(),
        "memory_attribution": "process-observation",
        "memory_snapshot_at_exit": p.memory_snapshot_at_exit,
        "memory_observations": p.timeline.memory_observations,
        "gc": p.gc_recorder.report(),
        "diagnostics": p.diagnostics,
        "sleep_overhead_s": round(sampler.sleep_overhead, 6),
        "rss_kind": sampler.rss_kind,
        "per_thread_cpu": any(c is not None for c in sampler._clocks.values()),
        "snapshot_cost_s": round(memory.snap_cost, 3), "snapshots": len(memory.snapshots),
        "peak_snapshots": memory.peak_snapshots, "peak_snapshot_cost_s": round(memory.peak_snap_cost, 3),
    }


def _memory_metadata(p, peak_idx, residual) -> dict:
    """Process-level memory totals."""
    sampler, memory = p.sampler, p.memory
    snapshots = memory.snapshots
    return {
        # Held at the peak by allocations whose traceback never reached your code
        # (deep library internals, e.g. `import pandas`). Raise --frames to attribute more.
        "unattributed_peak_mb": (round((snapshots[peak_idx][2] or 0) / 1e6, 3)
                                 if snapshots and peak_idx is not None else None),
        "frames": p.frames if p.mode == "precise" else None,
        "memory_tracing_lost_s": (round(memory.memory_tracing_lost_s, 4)
                                  if memory.memory_tracing_lost_s is not None else None),
        "sampler_error": sampler.sampler_error,
        "trace_function": ({"name": p.trace_function, "calls": p.trace.calls,
                            "traced_s": round(memory.traced_s, 4)} if p.trace_function else None),
        "peak_traced_mb": round(memory.peak_traced / 1e6, 3) if p.mode == "precise" else None,
        "rss_start_mb": round(p.rss0 / 1e6, 3) if p.rss0 else None,
        "rss_end_mb": round(p.rss1 / 1e6, 3) if p.rss1 else None,
        "rss_peak_mb": round(max(sampler.rss_max, p.rss1 or 0) / 1e6, 3) if sampler.rss_kind else None,
        # Process-level native estimate: memory the Python allocator never saw
        # (C extensions, NumPy/Arrow buffers, interpreter). Not attributed per line.
        # Not with --trace-function: Python memory allocated outside the traced calls is untraced,
        # so RSS minus traced memory would count it as native (measured: 219 MB of plain str).
        "native_untraced_mb": round(residual / 1e6, 3) if residual is not None else None,
        "native_memory": {'status': 'unavailable',
                          'reason': 'no native allocator interception; RSS residual is not native allocation evidence',
                          'rss_residual_mb': round(residual / 1e6, 3) if residual is not None else None},
    }
