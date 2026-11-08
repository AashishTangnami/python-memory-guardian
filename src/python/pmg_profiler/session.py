"""One profiling session: it creates the parts and owns start() and stop()."""
from __future__ import annotations

import os
import time
import tracemalloc

from .common import UserFiles
from .gc_recorder import GCRecorder
from .line_monitor import LineMonitor
from .memory import MemoryTracker
from .report import build_report
from .retention import Retention
from .sampler import Sampler
from .sources import SourceFiles
from .system import sleep_overhead
from .timeline import Timeline
from .trace_function import TraceFunction


class Profiler:
    def __init__(self, root: str, interval: float, memory: str, frames: int = 2,
                 monitoring: str = 'off', trace_function: str | None = None):
        self.interval = interval
        self.mode = memory
        self.frames = frames
        # Precise mode scoped to one function: tracemalloc runs only while it does (--trace-function).
        self.trace_function = trace_function if memory == 'precise' else None
        self.users = UserFiles(root)
        self.sources = SourceFiles(self.users)
        self.retention = Retention(self.users)
        self.timeline = Timeline()
        self.memory = MemoryTracker(memory, frames, self.users, self.timeline, bool(self.trace_function))
        self.sampler = Sampler(interval, memory, self.users, self.memory)
        self.monitor = LineMonitor(monitoring, self.users)
        self.trace = TraceFunction(self.trace_function, self.users, self.memory)
        self.gc_recorder = GCRecorder()
        self.largest: dict | None = None      # largest objects still held at exit (fast and precise modes)
        self.argv: list[str] = []          # the script's own arguments, so compared runs can check the workload
        self.started_at: str | None = None
        self.memory_snapshot_at_exit = False
        self.diagnostics = None
        self.command = []
        self.cwd = os.getcwd()

    def start(self):
        sampler = self.sampler
        self.sources.remember()
        sampler.sleep_overhead = sleep_overhead(self.interval) if sampler.split else 0.0
        if self.mode == "precise" and not self.trace_function:
            tracemalloc.start(self.frames)
        self.rss0 = sampler.rss() if sampler.rss_kind else None
        self.started_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        self.t0 = time.perf_counter()
        self.cpu0 = time.process_time()
        self.gc_recorder.start(self.t0)
        sampler.start()
        self.monitor.enable()
        if self.trace_function:
            self.trace.enable(self.t0)

    def stop(self, main_globals: dict | None = None):
        sampler, memory = self.sampler, self.memory
        sampler.stop()
        self.gc_recorder.stop()
        self.monitor.disable()
        self.trace.disable()
        if self.trace_function and self.trace.depth and tracemalloc.is_tracing():
            # A traced call still running at exit (a background thread): count its time so far.
            memory.traced_s += time.perf_counter() - memory.window_open_t
        self.wall = time.perf_counter() - self.t0
        self.cpu = time.process_time() - self.cpu0
        self.holders: dict = {}
        self.largest: dict | None = None
        self.rss1 = sampler.rss() if sampler.rss_kind else None    # before our own snapshot work
        traced_now = 0
        precise = memory.precise(self.wall)
        if precise:
            traced_now, traced_peak = tracemalloc.get_traced_memory()
            memory.peak_traced = max(memory.peak_traced, traced_peak)
        # Capture the process after user code exits; a short-lived allocation can
        # otherwise be the timeline's last point even though the exit snapshot frees it.
        self.timeline.close((self.wall, traced_now / 1e6, (self.rss1 or 0) / 1e6))
        if precise:
            memory.snapshot(self.wall)          # what is still held at exit
            self.memory_snapshot_at_exit = not bool(self.trace_function)
            leaks = memory.leaks() if not self.trace_function else {}
            if leaks:                          # needs tracemalloc still running
                self.holders = self.retention.find_holders(set(leaks), main_globals)
            tracemalloc.stop()
        if self.mode != "off":
            self.largest = self.retention.largest_objects(main_globals)

    def report(self, script: str) -> dict:
        return build_report(self, script)
