"""Optional allocator-event capture. No sampled stack is used as allocation evidence.

The Memray trace records allocator requests, not Python objects or resident pages.
Python allocator, system allocator and interpreter arena layers remain separate.
"""
from __future__ import annotations

import hashlib
import importlib
import json
import os
from pathlib import Path
import time

from .common import is_profiler_file

MAX_SITES = 2048
MAX_FRAMES = 64
MAX_SNAPSHOTS = 2048
MAX_CAPTURE_FILES = 64
# Serialized site records across all capture files. The memory guard reads a child report of at most
# 8 MiB (guard.MAX_REPORT_BYTES); 2048 sites with two 64-frame symbolized stacks can reach about
# 30 MiB (9.2 MiB for a two-line script on a CI Python with debug symbols), so the site list is
# bounded by size too. Sites past the budget move to the omitted totals, like the site-count overflow.
MAX_SITE_SUMMARY_BYTES = 4 << 20
_FREES = {"PYMALLOC_FREE", "FREE", "MUNMAP"}
_ARENA_SYMBOLS = ("new_arena", "_PyObject_Arena", "pymalloc_alloc", "allocateArena")


class AllocationBackendUnavailable(RuntimeError):
    """The user selected a backend that cannot provide this observation."""


def _memray():
    try:
        module = importlib.import_module("memray")
    except (ImportError, OSError) as exc:
        raise AllocationBackendUnavailable(
            "The memray allocation backend is unavailable in this interpreter. "
            "Install the optional memray dependency in the target environment, "
            "or select the sampled backend; allocation events remain unavailable there."
        ) from exc
    if not hasattr(module, "Tracker") or not hasattr(module, "FileReader"):
        raise AllocationBackendUnavailable("The installed memray lacks Tracker/FileReader support.")
    return module


def availability():
    try:
        module = _memray()
    except AllocationBackendUnavailable as exc:
        return {"available": False, "backend": "memray", "reason": str(exc)}
    return {"available": True, "backend": "memray", "version": module.__version__}


def _sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _frames(record, native, limit):
    try:
        method = record.native_stack_trace if native else record.stack_trace
        return [{"function": str(name), "file": str(filename),
                 "line": int(line) if line and line > 0 else None}
                for name, filename, line in method(limit)]
    except (NotImplementedError, RuntimeError, OSError):
        return None


def _under_root(filename, root):
    if not filename or filename.startswith("<"):
        return False
    try:
        return os.path.commonpath((os.path.abspath(filename), root)) == root
    except ValueError:
        return False


def _site_source(frames, root, domain):
    for index, frame in enumerate(frames or []):
        filename = frame["file"]
        # Instrumentation is still instrumentation when installed outside root.
        if is_profiler_file(filename):
            return "profiler", None
        if _under_root(filename, root):
            if any(part in ('site-packages', 'dist-packages', '.venv', 'node_modules')
                   for part in Path(filename).parts):
                continue
            return "target", {**frame, "role": "allocation_frame" if domain == "python_allocator" and index == 0
                              else "traced_user_callsite"}
    return "external", None


def _domain(allocator, native_frames):
    if allocator.startswith("PYMALLOC_"):
        return "python_allocator"
    if any(any(symbol in frame["function"] for symbol in _ARENA_SYMBOLS)
           for frame in native_frames or []):
        return "interpreter_arena"
    return "system_allocator"


def _bound_site_bytes(files, budget):
    """Keep the report's largest sites, across all capture files, within budget bytes of JSON.

    Sites are ranked by requested bytes over every file, so one large capture cannot crowd out the
    sites of a fork child. Selection stops at the first site that does not fit: every kept site has
    at least the requested bytes of every omitted one.
    """
    order = sorted(((i, j) for i, file in enumerate(files) for j in range(len(file["sites"]))),
                   key=lambda ij: -files[ij[0]]["sites"][ij[1]]["requested_bytes"])   # stable: file order breaks ties
    keep, used = set(), 0
    for i, j in order:
        size = len(json.dumps(files[i]["sites"][j])) + 2   # with its ", " list separator
        if used + size > budget:
            break
        used += size
        keep.add((i, j))
    for i, file in enumerate(files):
        kept = []
        for j, site in enumerate(file["sites"]):
            if (i, j) in keep:
                kept.append(site)
            else:
                file["omitted_site_allocation_count"] += site["allocation_count"]
                file["omitted_site_requested_bytes"] += site["requested_bytes"]
                file["summary_complete"] = False
        file["sites"] = kept


def _empty_totals():
    return {"requested_bytes": 0, "allocation_count": 0,
            "outstanding_at_capture_end_bytes": 0, "outstanding_at_capture_end_blocks": 0}


class AllocationRecorder:
    """Activate Memray around the target, then stream a bounded PMG summary.

    Memray's own trace writer and FileReader have additional memory costs. This
    class's site and snapshot limits do not bound those backend costs.
    """

    def __init__(self, path, root, *, overwrite=False, max_sites=MAX_SITES,
                 max_frames=MAX_FRAMES, memory_interval_ms=10, trace_python_allocators=True):
        if max_sites < 1 or max_frames < 1 or memory_interval_ms < 1:
            raise ValueError("Allocation summary limits and memory interval must be positive.")
        self.memray = _memray()
        # Resolve the folder only: resolving the file name itself would follow a link planted at it.
        path = Path(path)
        self.path = path.parent.resolve() / path.name
        self.root = os.path.abspath(root)
        self.overwrite = overwrite
        self.max_sites = int(max_sites)
        self.max_frames = int(max_frames)
        self.memory_interval_ms = int(memory_interval_ms)
        self.trace_python_allocators = bool(trace_python_allocators)
        self._tracker = None
        self._started = False
        self._stopped = False
        self._summary = None

    def start(self):
        if self._started:
            raise RuntimeError("AllocationRecorder can only start once.")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.is_symlink():
            self.path.unlink()          # replace a planted link instead of letting Memray write through it
        self._tracker = self.memray.Tracker(
            destination=self.memray.FileDestination(self.path, overwrite=self.overwrite),
            native_traces=True, trace_python_allocators=self.trace_python_allocators, follow_fork=True,
            memory_interval_ms=self.memory_interval_ms,
            file_format=self.memray.FileFormat.ALL_ALLOCATIONS)
        self.started_at_ns = time.time_ns()
        self._tracker.__enter__()
        self._started = True
        return self

    def stop(self):
        if not self._started:
            raise RuntimeError("AllocationRecorder has not started.")
        if not self._stopped:
            self._tracker.__exit__(None, None, None)
            self.stopped_at_ns = time.time_ns()
            self._stopped = True
        return self

    def __enter__(self):
        return self.start()

    def __exit__(self, exc_type, exc, traceback):
        self.stop()
        return False

    def _read_capture(self, path):
        sites = {}
        totals = {}
        scope_totals = {}
        unknown = 0
        summarized = 0
        omitted_allocations = 0
        omitted_bytes = 0
        native_cache = {}
        allocator_names = {int(value): value.name for value in self.memray.AllocatorType}
        free_ids = {int(getattr(self.memray.AllocatorType, name)) for name in _FREES}
        overflow = {}
        with self.memray.FileReader(path) as reader:
            metadata = reader.metadata
            for sequence, record in enumerate(reader.get_allocation_records()):
                allocator_id = record.allocator
                if allocator_id in free_ids:
                    continue
                allocator = allocator_names[allocator_id]
                # Key identity comes from the allocating thread and captured stack IDs.
                key = (record.tid, allocator, record.stack_id, record.native_stack_id,
                       record.native_segment_generation)
                site = sites.get(key)
                if site is None:
                    # Read stacks once for a stored site; an overflow still contributes to totals.
                    python_frames = _frames(record, False, self.max_frames)
                    native_key = (record.native_stack_id, record.native_segment_generation)
                    native_frames = native_cache.get(native_key)
                    if native_key not in native_cache:
                        native_frames = _frames(record, True, self.max_frames)
                        if len(native_cache) < self.max_sites:
                            native_cache[native_key] = native_frames
                    domain = _domain(allocator, native_frames)
                    scope, source = _site_source(python_frames, self.root, domain)
                    if len(sites) < self.max_sites:
                        site = {"process_id": metadata.pid, "thread_id": record.tid,
                                "thread_id_kind": "memray_capture", "thread_name": record.thread_name,
                                "os_thread_id": None, "allocator": allocator, "domain": domain,
                                "scope": scope, "source": source, "python_stack": python_frames,
                                "native_stack": native_frames, "first_event_sequence": sequence,
                                "last_event_sequence": sequence, "allocation_timestamp_ns": None,
                                **_empty_totals()}
                        sites[key] = site
                else:
                    domain, scope = site["domain"], site["scope"]
                count = int(record.n_allocations)
                size = int(record.size)
                summarized += count
                if site is not None:
                    site["requested_bytes"] += size
                    site["allocation_count"] += count
                    site["last_event_sequence"] = sequence
                    if site["native_stack"] is None:
                        unknown += count
                else:
                    entry = overflow.setdefault((domain, scope), _empty_totals())
                    entry['requested_bytes'] += size
                    entry['allocation_count'] += count
                    omitted_allocations += count
                    omitted_bytes += size
            # Sum stored sites once, rather than updating two nested dictionaries
            # for every allocator request. Overflow still contributes exact totals.
            for domain, scope, entry in [
                    (site['domain'], site['scope'], site) for site in sites.values()
                ] + [(domain, scope, entry) for (domain, scope), entry in overflow.items()]:
                for target in (totals.setdefault(domain, _empty_totals()),
                               scope_totals.setdefault(scope, {}).setdefault(domain, _empty_totals())):
                    target['requested_bytes'] += entry['requested_bytes']
                    target['allocation_count'] += entry['allocation_count']
            # Memray calls these "leaked" records; they only prove outstanding allocator blocks.
            for record in reader.get_leaked_allocation_records(merge_threads=False):
                allocator = self.memray.AllocatorType(record.allocator).name
                key = (record.tid, allocator, record.stack_id, record.native_stack_id,
                       record.native_segment_generation)
                site = sites.get(key)
                if site:
                    domain, scope = site["domain"], site["scope"]
                else:
                    native_frames = _frames(record, True, self.max_frames)
                    domain = _domain(allocator, native_frames)
                    scope, _ = _site_source(_frames(record, False, self.max_frames), self.root, domain)
                domain_totals = totals.setdefault(domain, _empty_totals())
                scoped = scope_totals.setdefault(scope, {}).setdefault(domain, _empty_totals())
                for entry in (domain_totals, scoped, site) if site else (domain_totals, scoped):
                    entry["outstanding_at_capture_end_bytes"] += int(record.size)
                    entry["outstanding_at_capture_end_blocks"] += int(record.n_allocations)
            snapshots, stride, seen = [], 1, 0
            last_snapshot = None
            for point in reader.get_memory_snapshots():
                last_snapshot = {"timestamp_ms": int(point.time), "rss_bytes": int(point.rss),
                                 "tracked_heap_bytes": int(point.heap)}
                if seen % stride == 0:
                    snapshots.append(last_snapshot)
                    if len(snapshots) >= MAX_SNAPSHOTS:
                        snapshots = snapshots[::2]
                        stride *= 2
                seen += 1
            if last_snapshot and (not snapshots or snapshots[-1] != last_snapshot):
                snapshots.append(last_snapshot)
            return {"artifact": {"path": str(path), "sha256": _sha256(path),
                                  "bytes": path.stat().st_size, "format": "memray_all_allocations"},
                    "process_id": metadata.pid,
                    "capture_started_at": metadata.start_time.isoformat(),
                    "capture_ended_at": metadata.end_time.isoformat(),
                    "python_allocator": metadata.python_allocator,
                    "python_allocator_requests_traced": bool(metadata.trace_python_allocators),
                    "allocator_heap_peak_bytes": int(metadata.peak_memory),
                    "allocation_count": summarized, "domain_totals": totals,
                    "scope_totals": scope_totals, "sites": sorted(sites.values(),
                        key=lambda x: x["requested_bytes"], reverse=True),
                    "summary_complete": omitted_allocations == 0,
                    "omitted_site_allocation_count": omitted_allocations,
                    "omitted_site_requested_bytes": omitted_bytes,
                    "native_stack_unavailable_count": unknown,
                    "memory_snapshots": snapshots, "memory_snapshot_count": seen,
                    "memory_snapshot_stride": stride}

    def report(self):
        if not self._stopped:
            raise RuntimeError("Stop allocation capture before reading its report.")
        if self._summary is not None:
            return self._summary
        candidates = [self.path] + sorted(
            path for path in self.path.parent.glob(self.path.name + ".*")
            if path.name[len(self.path.name) + 1:].isdigit())
        files, errors = [], []
        for path in candidates[:MAX_CAPTURE_FILES]:
            try:
                files.append(self._read_capture(path))
            except (OSError, RuntimeError, ValueError) as exc:
                errors.append({"artifact": str(path), "available": False,
                               "reason": f"Cannot read allocation trace: {exc}"})
        _bound_site_bytes(files, MAX_SITE_SUMMARY_BYTES)
        self._summary = {
            "backend": "memray", "version": self.memray.__version__, "available": bool(files),
            "started_at_ns": self.started_at_ns, "stopped_at_ns": self.stopped_at_ns,
            "availability": {"allocator_requests": "measured" if files else "unavailable",
                "python_allocator_requests": "measured" if any(f['python_allocator_requests_traced'] for f in files) else "unavailable",
                "native_coverage": "partial", "allocation_timestamps": "unavailable",
                "os_thread_ids": "unavailable", "object_counts": "unavailable",
                "object_reachability": "unavailable", "spawned_processes": "unavailable",
                "process_rss": "measured_snapshots" if any(f["memory_snapshots"] for f in files)
                               else "unavailable"},
            "domain_totals_additive": False, "files": files, "errors": errors,
            "omitted_capture_files": max(0, len(candidates) - MAX_CAPTURE_FILES),
            "limits": {"max_sites_per_capture": self.max_sites, "max_stack_frames": self.max_frames,
                       "max_memory_snapshots": MAX_SNAPSHOTS, "max_capture_files": MAX_CAPTURE_FILES,
                       "max_site_summary_bytes": MAX_SITE_SUMMARY_BYTES},
            "notes": [
                "Counts are allocator requests or outstanding blocks, not Python object counts.",
                "Requested volume includes repeated allocations/reallocations; it is not live memory or RSS.",
                "Python requests and system/arena layers can overlap; do not add domain totals.",
                "Native coverage is incomplete: direct function pointers/custom allocators may bypass hooks.",
                "Outstanding blocks at capture end are not evidence of an unexpected retention or leak.",
                "Allocation records preserve event order but expose no per-event timestamps.",
                "Thread IDs belong to this Memray capture; they are not OS IDs or sampler IDs.",
                "Fork child traces are separate; spawned interpreters require their own capture.",
                "PMG summaries are bounded; Memray trace files and reader internals have additional costs.",
                "Native symbol resolution depends on the libraries on the capture machine."
            ]}
        return self._summary


def start(path, root, **options):
    """Convenience entry point for callers that own the target's execution scope."""
    return AllocationRecorder(path, root, **options).start()
