"""The sampler thread: where every thread is, and how its time splits, once per interval."""
from __future__ import annotations

import os
import sys
import threading
import time
from typing import Callable

from .common import UserFiles
from .memory import MemoryTracker
from .system import free_threaded_build, gil_enabled, make_rss_reader, thread_cpu_clock


class LineStats:
    __slots__ = ("python_s", "native_s", "system_s", "cpu_s", "samples", "rss_up", "rss_down",
                 "transient", "traced_up", "book_up", "func")

    def __init__(self):
        self.python_s = self.native_s = self.system_s = self.cpu_s = 0.0
        self.samples = 0
        self.rss_up = self.rss_down = 0
        self.transient = 0      # largest short-lived peak seen between two samples
        self.traced_up = 0      # Python-allocator growth observed while this line ran
        self.book_up = 0        # growth of tracemalloc's own bookkeeping while this line ran (profiler memory)
        self.func = None        # (name, first line) of the enclosing function


class Sampler:
    def __init__(self, interval: float, memory: str, users: UserFiles, tracker: MemoryTracker):
        self.interval = interval
        self.users = users
        self.memory = tracker
        self.lines: dict[tuple[str, int], LineStats] = {}
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="pmg-sampler", daemon=True)
        self._clocks: dict[int, Callable[[], float] | None] = {}   # None: no per-thread CPU clock
        self.rss, self.rss_kind = make_rss_reader() if memory != 'off' else (lambda: 0, None)
        self.split = gil_enabled()
        self.switch = sys.getswitchinterval()
        self.sleep_overhead = 0.0
        self.rss_max = 0            # every sample's RSS, so thinning the timeline never hides the peak
        self.samples = 0
        self.stack_totals: dict = {}
        self._code_info: dict[int, tuple] = {}
        self._codes: list[tuple] = []
        self.stack_dropped_s = 0.0
        self.stack_depth_limited = False
        self.sampler_error: str | None = None
        # Reading another thread's frames is safe only while this thread holds the GIL, because the
        # owner cannot run and change them. A free-threaded build gives no such guarantee: the owner
        # keeps running, and f_lineno/f_back read memory it changes or frees (measured: crashes in
        # 31 of 480 stress runs on 3.13.3t and 3.14.0a6t; none in 480 with this rule). The GIL state
        # can also change during a run (an extension module can enable it, temporarily), so the
        # decision is per build, never per sample.
        self.stacks_reason: str | None = (
            "free-threaded Python: other threads' frames cannot be read safely while they run"
            if free_threaded_build() else None)

    def start(self):
        self._thread.start()

    def stop(self):
        self._stop.set()
        self._thread.join()

    def _code(self, co):
        """Per-code facts, computed once: (display file, qualname, first line, is_user, id).

        Keyed by id(co); the entry holds co itself so the id cannot be reused by another code
        object. co_qualname (3.11+) gives "Service.handle"; older versions fall back to co_name.
        """
        info = self._code_info.get(id(co))
        if info is None:
            filename = co.co_filename
            info = (filename if filename.startswith('<') else os.path.abspath(filename),
                    getattr(co, 'co_qualname', co.co_name).replace('.<locals>', ''),
                    co.co_firstlineno, self.users.is_user(filename), len(self._codes), co)
            self._code_info[id(co)] = info
            self._codes.append(info)
        return info

    def _walk(self, frame):
        """One pass over a thread's frames -> (line, func, stack).

        line/func: the innermost user frame ((file, line), (qualname, first line)), used for
        attribution. stack: Python frames from the outermost user call through the active
        frame, at most 128 deep, as a flat tuple (code id, line, code id, line, ...) so the
        sampler hashes small ints. report() turns it back into frame records. Native C
        frames are not visible through sys._current_frames(); no frame references are kept.
        """
        flat = []
        loc = func = None
        outer = -1                      # index of the outermost user frame seen
        depth = 0
        while frame is not None and depth < 128:
            info = self._code(frame.f_code)
            line = frame.f_lineno
            if info[3]:
                if loc is None:
                    loc, func = (info[0], line), (info[1], info[2])
                outer = depth
            flat.append(info[4])
            flat.append(line)
            depth += 1
            frame = frame.f_back
        self.stack_depth_limited |= frame is not None
        while loc is None and frame is not None:      # user code below a >128-frame library stack
            info = self._code(frame.f_code)
            if info[3]:
                loc, func = (info[0], frame.f_lineno), (info[1], info[2])
            frame = frame.f_back
        if outer < 0:
            return loc, func, ()
        stack = []
        for k in range(outer, -1, -1):                 # outermost user frame first
            stack.append(flat[2 * k])
            stack.append(flat[2 * k + 1])
        return loc, func, tuple(stack)

    def _record_stack(self, ident, name, stack, values):
        if not stack:
            return
        key = (str(ident), name, stack)
        totals = self.stack_totals.get(key)
        if totals is None:
            if len(self.stack_totals) >= 50_000:
                self.stack_dropped_s += sum(values)
                return
            totals = self.stack_totals[key] = [0.0, 0.0, 0.0, 0.0, 0]
        for i, value in enumerate(values):
            totals[i] += value
        totals[4] += 1

    def _stats(self, loc):
        st = self.lines.get(loc)
        if st is None:
            st = self.lines[loc] = LineStats()
        return st

    def _run(self) -> None:
        """Sampler thread. A failure ends sampling but is reported, never a lost profile."""
        try:
            self._sample_loop()
        except Exception as e:
            self.sampler_error = f"{type(e).__name__}: {e}"

    def _sample_loop(self) -> None:
        me = threading.get_ident()
        main = threading.main_thread().ident
        last_wall = time.perf_counter()
        last_proc = time.process_time()
        last_cpu: dict[int, float] = {}
        last_rss = self.rss()
        t0 = last_wall
        last_snap_t, last_snap_traced, prev_traced, prev_book = 0.0, 0, 0, 0
        rss_change = 0
        # Tuned loop: one bound name per collaborator, so the split adds no call per sample.
        memory = self.memory
        memory_sample, trace_lock = memory.sample, memory.trace_lock
        while not self._stop.is_set():
            sleep_started = time.perf_counter()
            time.sleep(self.interval)
            now = time.perf_counter()          # first thing after re-acquiring the GIL
            proc = time.process_time()
            dw, dproc = now - last_wall, proc - last_proc
            last_wall, last_proc = now, proc
            # Only sleep overshoot is a possible GIL signal. The preceding
            # sample/snapshot work and the idle timer baseline are not GIL waits.
            wait = max(0.0, now - sleep_started - self.interval - self.sleep_overhead)
            self.samples += 1

            per_thread = []
            stacks = {}
            if self.stacks_reason is None:
                names = {t.ident: t.name for t in threading.enumerate()}
                current_frames = sys._current_frames()
            else:
                names, current_frames = {}, {}
            for ident, frame in current_frames.items():
                if ident == me:
                    continue
                if ident not in self._clocks:
                    self._clocks[ident] = thread_cpu_clock(ident)
                clock = self._clocks[ident]
                if clock is not None:
                    try:
                        c = clock()
                        dcpu = c - last_cpu.get(ident, c)
                        last_cpu[ident] = c
                    except (OSError, ValueError):
                        # A short-lived worker may exit between frame/clock reads.
                        self._clocks.pop(ident, None)
                        last_cpu.pop(ident, None)
                        dcpu = None
                else:
                    # A process clock cannot identify the CPU time of a worker.
                    dcpu = dproc if ident == main and len(current_frames) <= 2 else None
                loc, func, stack = self._walk(frame)
                if loc is not None:
                    self._stats(loc).func = func
                    stacks[ident] = stack
                per_thread.append((ident, loc, dcpu))
            # A loop variable otherwise keeps the last live frame across sleep,
            # which can resurrect a cyclic object whose finalizer releases the GIL.
            frame = None
            del current_frames

            running = [t for t in per_thread if t[2] is not None and t[2] > 0.2 * dw]
            for ident, loc, dcpu in per_thread:
                if loc is None:
                    continue
                st = self._stats(loc)
                before = (st.python_s, st.native_s, st.system_s, st.cpu_s)
                st.samples += 1
                if dcpu is None:                     # no CPU clock for this thread
                    st.cpu_s += dw
                    self._record_stack(ident, names.get(ident, 'Thread'), stacks.get(ident), (0, 0, 0, dw))
                    continue
                cpu = min(max(dcpu, 0.0), dw)
                st.system_s += dw - cpu
                if not self.split or len(running) > 1:
                    st.cpu_s += cpu                  # GIL signal ambiguous: report unsplit
                elif wait > 2 * self.switch:         # C code held the GIL
                    native = min(cpu, wait - self.switch)
                    st.native_s += native
                    st.python_s += cpu - native
                elif wait < 0.5 * self.switch:       # GIL was free while this thread ran
                    st.native_s += cpu
                else:                                # yielded at the switch interval
                    st.python_s += cpu
                after = (st.python_s, st.native_s, st.system_s, st.cpu_s)
                self._record_stack(ident, names.get(ident, 'Thread'), stacks.get(ident),
                                   tuple(b - a for a, b in zip(before, after)))

            # RSS is a process observation, never an allocating thread or line.
            rss = 0
            if self.rss_kind:
                rss = self.rss()                     # one read per sample, shared with the timeline
                self.rss_max = max(self.rss_max, rss)
                rss_change = rss - last_rss
                last_rss = rss
            with trace_lock:                         # a traced-function window cannot open or close meanwhile
                prev_traced, prev_book, last_snap_t, last_snap_traced = memory_sample(
                    now, t0, stacks, names, main, rss, self.rss_kind, rss_change,
                    prev_traced, prev_book, last_snap_t, last_snap_traced)
            if memory.snapped:
                # The snapshot held the GIL, so user threads only waited for it. Restart the interval
                # here, or the next sample charges that pause to whichever user line runs next
                # (measured: 0.5 s of "waiting" on a 0.04 s generator, from 2.1 s of snapshots).
                last_wall, last_proc = time.perf_counter(), time.process_time()
