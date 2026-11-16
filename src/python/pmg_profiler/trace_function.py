"""Precise mode for one function: tracemalloc runs only while that function does (--trace-function)."""
from __future__ import annotations

import sys
import time
import tracemalloc

from .common import UserFiles
from .memory import MemoryTracker


class TraceFunction:
    def __init__(self, name: str | None, users: UserFiles, memory: MemoryTracker):
        self.name = name
        self.users = users
        self.memory = memory
        self.t0 = 0.0
        self._tool = None
        self._disable = None
        self._codes: set = set()          # code objects matching the name; their PY_START stays enabled
        self.depth = 0                    # calls in progress (recursion, several threads)
        self.calls = 0                    # traced windows opened

    def enable(self, t0):
        """PY_START for every function once: a non-matching one returns DISABLE, which turns the event
        off at that code location, so other code runs at full speed after its first call. Matching code
        keeps PY_START and gets a local PY_RETURN; PY_UNWIND (global only) is on while a window is open."""
        self.t0 = t0
        mon = sys.monitoring
        tool = next((t for t in (4, 3) if mon.get_tool(t) is None), None)
        if tool is None:
            raise RuntimeError('no free sys.monitoring tool ID for --trace-function')
        mon.use_tool_id(tool, 'python-memory-guardian-trace')
        self._tool, self._disable = tool, mon.DISABLE
        mon.register_callback(tool, mon.events.PY_START, self._on_start)
        mon.register_callback(tool, mon.events.PY_RETURN, self._on_return)
        mon.register_callback(tool, mon.events.PY_UNWIND, self._on_unwind)
        mon.set_events(tool, mon.events.PY_START)

    def _matches(self, code) -> bool:
        name = self.name
        return (code.co_name == name or getattr(code, 'co_qualname', '').replace('.<locals>', '') == name) \
            and self.users.is_user(code.co_filename)

    def _on_start(self, code, _offset):
        if code not in self._codes:
            if not self._matches(code):
                return self._disable
            self._codes.add(code)
            sys.monitoring.set_local_events(self._tool, code, sys.monitoring.events.PY_RETURN)
        memory = self.memory
        with memory.trace_lock:
            self.depth += 1
            if self.depth == 1:
                self.calls += 1
                tracemalloc.start(memory.frames)
                memory.window_open_t = time.perf_counter()
                mon = sys.monitoring
                mon.set_events(self._tool, mon.events.PY_START | mon.events.PY_UNWIND)
        return None

    def _on_return(self, code, _offset, _retval):
        if code in self._codes:
            self._exit()

    def _on_unwind(self, code, _offset, _exc):
        if code in self._codes:
            self._exit()

    def _exit(self):
        """The outermost traced call ended: snapshot what it still holds, then stop tracing (which
        clears every trace, so the snapshot has to come first)."""
        memory = self.memory
        with memory.trace_lock:
            self.depth = max(0, self.depth - 1)
            if self.depth or not tracemalloc.is_tracing():
                return
            now = time.perf_counter()
            traced, peak = tracemalloc.get_traced_memory()
            memory.peak_traced = max(memory.peak_traced, traced, peak)
            memory.snapshot(now - self.t0)
            memory.snap_cost += time.perf_counter() - now
            tracemalloc.stop()
            memory.traced_s += now - memory.window_open_t
            sys.monitoring.set_events(self._tool, sys.monitoring.events.PY_START)

    def disable(self):
        tool, self._tool = self._tool, None
        if tool is None:
            return
        mon = sys.monitoring
        try:
            mon.set_events(tool, 0)
            for code in self._codes:
                mon.set_local_events(tool, code, 0)
        finally:
            for event in (mon.events.PY_START, mon.events.PY_RETURN, mon.events.PY_UNWIND):
                mon.register_callback(tool, event, None)
            mon.free_tool_id(tool)
