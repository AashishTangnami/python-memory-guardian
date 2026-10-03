"""
Python Memory Guardian - runtime profiler (standard library only).

    python server/pmg_profile.py [--out .pmg/profile.json] [--root DIR] [--interval 0.01]
                          [--memory fast|precise|off] script.py [args...]

Per line of *your* code (files under --root) it reports:

TIME (sampling, ~1% overhead), split three ways for each sample:
  system  wall time the thread was off-CPU (I/O, sleep, lock waits) - from the
          thread's own CPU clock vs wall clock.
  native  CPU time in C code. Signal: how long this sampler thread waited for the
          GIL, after subtracting a calibrated idle timer delay and excluding the
          sampler's own work. Python bytecode yields the GIL at the switch interval
          (sys.getswitchinterval(), 5 ms default), so a wait of ~1 interval means
          bytecode was running; ~0 while the thread burned CPU means C code had
          released the GIL; >> 1 interval means C code held it ("the actual value
          can be higher, especially if long-running internal functions or methods
          are used" - sys docs). Measured on CPython 3.12: 5.18 / 0.10 / 421 ms.
  python  the rest of the CPU time.
On free-threaded builds there is no GIL signal; CPU time is reported unsplit.

MEMORY
  fast     (default) RSS growth attributed to the line running when it happened.
           ~free; counts Python *and* native memory; coarse (RSS can lag frees).
  precise  tracemalloc: bytes still held per allocating line at the peak and at exit,
           untraced ("native") growth per line, and leak detection (held memory grew
           across >= 3 consecutive snapshots and was still held at exit).
           Costs ~2-5x runtime (measured: 2.9x at the default 2-frame depth), because
           tracemalloc hooks every allocation. Snapshot cost is predicted from
           tracemalloc's bookkeeping size and capped at 10% of elapsed time.
"""
from __future__ import annotations

import argparse
import ast
import atexit
import gc
import hashlib
import io
import json
import os
import runpy
import statistics
import sys
import sysconfig
import threading
import time
import tokenize
import tracemalloc

SCHEMA = 3
THIS_FILE = os.path.normcase(os.path.abspath(__file__))


# ---------------------------------------------------------------- RSS, no third-party deps
def _make_rss_reader():
    """Return (reader, kind). kind: 'current' | 'peak' | None."""
    if sys.platform.startswith("linux"):
        page = os.sysconf("SC_PAGE_SIZE")

        def linux():
            with open("/proc/self/statm", "rb") as f:
                return int(f.read().split()[1]) * page
        try:
            linux()
            return linux, "current"
        except Exception:
            pass
    if sys.platform == "win32":
        try:
            import ctypes
            from ctypes import wintypes

            class PMC(ctypes.Structure):
                _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD)] + [
                    (n, ctypes.c_size_t) for n in (
                        "PeakWorkingSetSize", "WorkingSetSize", "QuotaPeakPagedPoolUsage",
                        "QuotaPagedPoolUsage", "QuotaPeakNonPagedPoolUsage",
                        "QuotaNonPagedPoolUsage", "PagefileUsage", "PeakPagefileUsage")]
            k32, psapi = ctypes.windll.kernel32, ctypes.windll.psapi
            k32.GetCurrentProcess.restype = wintypes.HANDLE
            proc = k32.GetCurrentProcess()
            pmc = PMC()
            pmc.cb = ctypes.sizeof(PMC)

            def windows():
                psapi.GetProcessMemoryInfo(proc, ctypes.byref(pmc), pmc.cb)
                return int(pmc.WorkingSetSize)
            windows()
            return windows, "current"
        except Exception:
            pass
    if sys.platform == "darwin":
        try:  # mach_task_basic_info via libSystem (MACH_TASK_BASIC_INFO = 20)
            import ctypes
            import ctypes.util

            class MTBI(ctypes.Structure):
                _fields_ = [("virtual_size", ctypes.c_uint64), ("resident_size", ctypes.c_uint64),
                            ("resident_size_max", ctypes.c_uint64), ("user_time", ctypes.c_uint64),
                            ("system_time", ctypes.c_uint64), ("policy", ctypes.c_int),
                            ("suspend_count", ctypes.c_int)]
            lib = ctypes.CDLL(ctypes.util.find_library("System"))
            task = lib.mach_task_self()
            info = MTBI()
            count = ctypes.c_uint(ctypes.sizeof(MTBI) // 4)

            def mac():
                count.value = ctypes.sizeof(MTBI) // 4
                if lib.task_info(task, 20, ctypes.byref(info), ctypes.byref(count)) != 0:
                    raise OSError("task_info failed")
                return int(info.resident_size)
            mac()
            return mac, "current"
        except Exception:
            pass
    try:
        import resource

        def peak():
            v = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
            return int(v) if sys.platform == "darwin" else int(v) * 1024
        peak()
        return peak, "peak"
    except Exception:
        return (lambda: 0), None


def _thread_cpu_clock(ident: int):
    """Zero-arg callable returning this thread's CPU seconds, or None if unsupported."""
    getid = getattr(time, "pthread_getcpuclockid", None)
    if getid is None:
        return None
    try:
        clk = getid(ident)
        time.clock_gettime(clk)
        return lambda: time.clock_gettime(clk)
    except Exception:
        return None


def _gil_enabled() -> bool:
    f = getattr(sys, "_is_gil_enabled", None)
    return True if f is None else bool(f())


def _sleep_overhead(interval: float) -> float:
    """Measure timer oversleep before user code starts, without GIL contention.

    OS timer coalescing can add milliseconds to sleep even when Python is idle.
    Use a median so an isolated scheduling pause does not skew the baseline.
    Bound startup cost for unusually large requested sampling intervals.
    """
    delay = min(interval, 0.01)
    overshoots = []
    for _ in range(9):
        before = time.perf_counter()
        time.sleep(delay)
        overshoots.append(max(0.0, time.perf_counter() - before - delay))
    return statistics.median(overshoots)


class LineStats:
    __slots__ = ("python_s", "native_s", "system_s", "cpu_s", "samples", "rss_up", "rss_down",
                 "transient", "traced_up", "func")

    def __init__(self):
        self.python_s = self.native_s = self.system_s = self.cpu_s = 0.0
        self.samples = 0
        self.rss_up = self.rss_down = 0
        self.transient = 0      # largest short-lived peak seen between two samples
        self.traced_up = 0      # Python-allocator growth observed while this line ran
        self.func = None        # (name, first line) of the enclosing function


class Profiler:
    def __init__(self, root: str, interval: float, memory: str, frames: int = 2):
        self.root = os.path.normcase(os.path.abspath(root)).rstrip(os.sep) + os.sep
        self.interval = interval
        self.memory = memory
        self.frames = frames
        self.snap_rate = 25e-9   # s per byte of tracemalloc bookkeeping (measured), refined live
        self.lines: dict[tuple[str, int], LineStats] = {}
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="pmg-sampler", daemon=True)
        paths = sysconfig.get_paths()
        self._excluded = tuple({os.path.normcase(p).rstrip(os.sep) + os.sep for p in (
            paths.get("stdlib"), paths.get("platstdlib"), paths.get("purelib"), paths.get("platlib"))
            if p})
        self._user_cache: dict[str, bool] = {}
        self._clocks: dict[int, object] = {}
        self.rss, self.rss_kind = _make_rss_reader()
        self.split = _gil_enabled()
        self.switch = sys.getswitchinterval()
        self.sleep_overhead = 0.0
        self.snapshots: list[tuple[float, dict, dict]] = []  # (t, held{loc:bytes}, None)
        self.snap_cost = 0.0
        self.timeline: list[tuple[float, float, float]] = []
        self.peak_traced = 0
        self.samples = 0
        self._source_versions: dict[str, tuple[tuple, str]] = {}
        self.stack_totals: dict = {}
        self.stack_dropped_s = 0.0
        self.stack_depth_limited = False

    @staticmethod
    def _source_version(path: str) -> tuple:
        st = os.stat(path)
        return (st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns, st.st_ctime_ns)

    def _remember_sources(self):
        """Record identities before execution, including files imported later.

        A file created or changed during the run must not be certified fresh from
        its exit-time contents. Retain hashes rather than source buffers. This
        inventory runs before timing/tracing starts; hashes also protect platforms
        whose stat change-time field is actually a creation timestamp.
        """
        for directory, dirs, names in os.walk(self.root):
            dirs[:] = [d for d in dirs if not d.startswith('.') and d != 'node_modules'
                       and self._is_user(os.path.join(directory, d, '_source.py'))]
            for name in names:
                path = os.path.abspath(os.path.join(directory, name))
                if name.endswith(('.py', '.pyw')) and self._is_user(path):
                    try:
                        version = self._source_version(path)
                        with open(path, 'rb') as f:
                            digest = hashlib.sha1(f.read().replace(b"\r\n", b"\n")).hexdigest()
                        if self._source_version(path) == version:
                            self._source_versions[os.path.normcase(path)] = (version, digest)
                    except OSError:
                        pass

    def _unchanged_source(self, path: str) -> bytes | None:
        """Return source only if its identity stayed stable throughout this run."""
        expected = self._source_versions.get(os.path.normcase(path))
        try:
            if expected is None or self._source_version(path) != expected[0]:
                return None
            with open(path, 'rb') as f:
                source = f.read()
            digest = hashlib.sha1(source.replace(b"\r\n", b"\n")).hexdigest()
            return source if self._source_version(path) == expected[0] and digest == expected[1] else None
        except OSError:
            return None

    @staticmethod
    def _text_hash(source: bytes) -> str:
        encoding, _ = tokenize.detect_encoding(io.BytesIO(source).readline)
        text = source.decode(encoding).replace("\r\n", "\n")
        return hashlib.sha1(text.encode("utf-8")).hexdigest()

    def _is_user(self, filename: str) -> bool:
        hit = self._user_cache.get(filename)
        if hit is None:
            if filename.startswith("<"):         # <frozen ...>, <string>, <stdin>
                hit = False
            else:
                p = os.path.normcase(os.path.abspath(filename))
                hit = (p.startswith(self.root) and p != THIS_FILE
                       and not p.startswith(self._excluded) and "site-packages" not in p)
            self._user_cache[filename] = hit
        return hit

    def _user_line(self, frame):
        """Innermost user frame -> ((file, line), (func name, first line))."""
        f = frame
        while f is not None:
            co = f.f_code
            if self._is_user(co.co_filename):
                # co_qualname (3.11+) gives "Service.handle"; older versions fall back to co_name.
                qn = getattr(co, "co_qualname", co.co_name).replace(".<locals>", "")
                return (os.path.abspath(co.co_filename), f.f_lineno), (qn, co.co_firstlineno)
            f = f.f_back
        return None, None

    def _stack(self, frame):
        """Python frames from the outermost user call through the active frame.

        Store immutable values only, never frame references that retain locals.
        Native C frames are not visible through sys._current_frames().
        """
        frames = []
        while frame is not None and len(frames) < 128:
            co = frame.f_code
            filename = co.co_filename
            user = self._is_user(filename)
            frames.append((os.path.abspath(filename) if not filename.startswith('<') else filename,
                           frame.f_lineno, getattr(co, 'co_qualname', co.co_name).replace('.<locals>', ''),
                           co.co_firstlineno, user))
            frame = frame.f_back
        self.stack_depth_limited |= frame is not None
        frames.reverse()
        first = next((i for i, fr in enumerate(frames) if fr[4]), None)
        return tuple(frames[first:]) if first is not None else ()

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

    # ------------------------------------------------------------ sampling loop
    def _run(self):
        me = threading.get_ident()
        main = threading.main_thread().ident
        last_wall = time.perf_counter()
        last_proc = time.process_time()
        last_cpu: dict[int, float] = {}
        last_rss = self.rss()
        t0 = last_wall
        last_snap_t, last_snap_traced, prev_traced = 0.0, 0, 0
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
            names = {t.ident: t.name for t in threading.enumerate()}
            for ident, frame in sys._current_frames().items():
                if ident == me:
                    continue
                clock = self._clocks.get(ident, 0)
                if clock == 0:
                    clock = self._clocks[ident] = _thread_cpu_clock(ident)
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
                    dcpu = dproc if ident == main else None
                loc, func = self._user_line(frame)
                if loc is not None:
                    self._stats(loc).func = func
                    stacks[ident] = self._stack(frame)
                per_thread.append((ident, loc, dcpu))

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

            # fast memory: attribute RSS change to the busiest thread's line
            if self.rss_kind:
                rss = self.rss()
                d = rss - last_rss
                last_rss = rss
                if d and per_thread:
                    tgt = max(per_thread, key=lambda t: (t[1] is not None, t[2] or 0))
                    if tgt[1] is not None:
                        st = self._stats(tgt[1])
                        if d > 0:
                            st.rss_up += d
                        else:
                            st.rss_down -= d
            traced = 0
            if self.memory == "precise":
                traced, interval_peak = tracemalloc.get_traced_memory()
                self.peak_traced = max(self.peak_traced, interval_peak)
                if hasattr(tracemalloc, "reset_peak"):          # 3.9+
                    tracemalloc.reset_peak()
                    # A peak that rose and fell between samples (e.g. inside a C call that
                    # held the GIL) - charge it to the line where the thread resumed.
                    transient = interval_peak - max(traced, prev_traced)
                else:
                    transient = 0
                tgt = max(per_thread, key=lambda t: (t[1] is not None, t[2] or 0)) if per_thread else None
                if tgt is not None and tgt[1] is not None:
                    st = self._stats(tgt[1])
                    if traced > prev_traced:
                        st.traced_up += traced - prev_traced     # cheap, no snapshot needed
                    if transient > max(10 << 20, 0.25 * traced):
                        st.transient = max(st.transient, transient)
            self.timeline.append((now - t0, traced / 1e6, (self.rss() if self.rss_kind else 0) / 1e6))

            if self.memory == "precise":
                self.peak_traced = max(self.peak_traced, traced)
                elapsed = now - t0
                # spike: sudden jump since the previous *sample* -> snapshot now, so
                # short-lived peaks (temporary copies) are attributed to their line.
                spike = traced - prev_traced > max(prev_traced * 0.5, 10 << 20)
                # periodic snapshots scale with runtime (~10 per run, 0.25-2 s apart) so even
                # short programs get enough points for a leak trend
                period = min(2.0, max(0.25, elapsed / 10))
                due = spike or (traced > max(last_snap_traced * 1.25, 1 << 20)
                                and elapsed - last_snap_t > 0.25) or elapsed - last_snap_t > period
                prev_traced = traced
                # Predict the snapshot's cost from tracemalloc's bookkeeping size and only
                # take it if total snapshot time stays within 10% of elapsed runtime.
                book = tracemalloc.get_tracemalloc_memory()
                # 10% of runtime, but at least 0.5 s in total, so short programs still get
                # a few snapshots (their peak and trend) instead of none.
                if due and self.snap_cost + book * self.snap_rate <= max(0.10 * elapsed, 0.5):
                    s = time.perf_counter()
                    self._snapshot(elapsed)
                    cost = time.perf_counter() - s
                    self.snap_cost += cost
                    if book:
                        self.snap_rate = 0.5 * self.snap_rate + 0.5 * (cost / book)
                    last_snap_t, last_snap_traced = elapsed, traced

    def _snapshot(self, t: float):
        """Bytes still held per allocating user line, plus held bytes with no user frame.

        Works on tracemalloc's raw trace tuples (domain, size, frames, total_nframe) and
        caches one answer per distinct traceback. Measured on a pandas import (208,595
        traces): the previous filter_traces() + statistics() path took 2.9 s, almost all
        of it Python-level object building. The raw frames are stored most-recent-first
        (verified), the reverse of the Traceback API, so the first user frame found is
        the innermost one."""
        snap = tracemalloc.take_snapshot()
        raw = getattr(getattr(snap, "traces", None), "_traces", None)
        held: dict[tuple[str, int], int] = {}
        unattributed = 0
        if raw is None:                      # private layout changed: slow, public path
            for stat in snap.statistics("traceback"):
                for fr in reversed(stat.traceback):
                    if self._is_user(fr.filename):
                        loc = (os.path.abspath(fr.filename), fr.lineno)
                        held[loc] = held.get(loc, 0) + stat.size
                        break
                else:
                    unattributed += stat.size
            self.snapshots.append((t, held, unattributed))
            return
        own = (os.path.normcase(os.path.abspath(__file__)), os.path.normcase(tracemalloc.__file__))
        cache: dict = {}
        skip = object()
        for trace in raw:
            frames = trace[2]
            loc = cache.get(frames, skip)
            if loc is skip:
                loc = None
                for fn, ln in frames:                       # most recent first
                    if fn.startswith("<"):
                        continue
                    n = os.path.normcase(os.path.abspath(fn))
                    if n in own:                            # the profiler's own memory
                        loc = False
                        break
                    if self._is_user(fn):
                        loc = (os.path.abspath(fn), ln)
                        break
                cache[frames] = loc
            if loc:
                held[loc] = held.get(loc, 0) + trace[1]
            elif loc is None:
                unattributed += trace[1]
        self.snapshots.append((t, held, unattributed))

    def start(self):
        self._remember_sources()
        self.sleep_overhead = _sleep_overhead(self.interval) if self.split else 0.0
        if self.memory == "precise":
            tracemalloc.start(self.frames)
        self.rss0 = self.rss() if self.rss_kind else None
        self.t0 = time.perf_counter()
        self.cpu0 = time.process_time()
        self._thread.start()

    def stop(self, main_globals: dict | None = None):
        self._stop.set()
        self._thread.join()
        self.wall = time.perf_counter() - self.t0
        self.cpu = time.process_time() - self.cpu0
        self.holders: dict = {}
        self.rss1 = self.rss() if self.rss_kind else None    # before our own snapshot work
        if self.memory == "precise":
            self.peak_traced = max(self.peak_traced, tracemalloc.get_traced_memory()[1])
            self._snapshot(self.wall)          # what is still held at exit
            leaks = self._leaks()
            if leaks:                          # needs tracemalloc still running
                self.holders = self._find_holders(set(leaks), main_globals)
            tracemalloc.stop()

    # ------------------------------------------------------------ who holds leaked memory
    def _origin(self, obj):
        tb = tracemalloc.get_object_traceback(obj)
        if tb is None:
            return None
        for fr in reversed(tb):                        # oldest -> newest; innermost user frame
            if self._is_user(fr.filename):
                return (os.path.abspath(fr.filename), fr.lineno)
        return None

    def _find_holders(self, want: set, main_globals: dict | None, budget_s: float = 2.0) -> dict:
        """leak line -> [{"holder": "global LEAK", "type": "list", "items": 12}, ...]

        Containers are reached three ways, because CPython may untrack dicts that hold
        only non-container values (verified: a dict of bytearrays is untracked):
        tracked objects from gc.get_objects(), module globals, and the attributes of
        tracked instances of user-defined classes."""
        deadline = time.perf_counter() + budget_s
        found: dict = {}

        def scan(container, label):
            if isinstance(container, dict):
                items = container.values()
            elif isinstance(container, (list, tuple, set, frozenset)) or type(container).__name__ == "deque":
                items = container
            else:
                return
            hits = {}
            for i, it in enumerate(items):
                if i >= 5000:
                    break
                o = self._origin(it)
                if o in want:
                    hits[o] = hits.get(o, 0) + 1
            for loc, n in hits.items():
                rows = found.setdefault(loc, [])
                if not any(r["holder"] == label for r in rows):
                    rows.append({"holder": label, "type": type(container).__name__,
                                 "items": len(container), "matching": n})

        spaces = []
        if main_globals is not None:
            spaces.append(("__main__", main_globals))
        for m in list(sys.modules.values()):
            fn = getattr(m, "__file__", None)
            if fn and self._is_user(fn) and getattr(m, "__name__", "") != "__main__":
                spaces.append((m.__name__, vars(m)))
        seen = set()
        for mod, g in spaces:                          # 1) module globals
            for k, v in list(g.items()):
                if k.startswith("__") or id(v) in seen:
                    continue
                seen.add(id(v))
                label = f"global {k}" if mod == "__main__" else f"{mod}.{k}"
                if self._origin(v) in want:            # the global *is* the leaked object
                    found.setdefault(self._origin(v), []).append(
                        {"holder": label, "type": type(v).__name__, "items": 1, "matching": 1})
                scan(v, label)
        for obj in gc.get_objects():                   # 2) instance attributes, 3) other containers
            if time.perf_counter() > deadline:
                break
            cls = type(obj)
            mod = sys.modules.get(getattr(cls, "__module__", ""), None)
            user_cls = cls.__module__ == "__main__" or (
                mod is not None and getattr(mod, "__file__", None) and self._is_user(mod.__file__))
            if user_cls and not isinstance(obj, type) and hasattr(obj, "__dict__"):
                for k, v in list(vars(obj).items()):
                    if id(v) not in seen:
                        scan(v, f"{cls.__qualname__}.{k}")
            elif id(obj) not in seen and isinstance(obj, (list, dict, set, tuple)):
                before = {loc: len(r) for loc, r in found.items()}
                scan(obj, f"unnamed {cls.__name__}")
                # an unnamed hit inside a container we already named elsewhere is noise
                for loc, rows in found.items():
                    if len(rows) > before.get(loc, 0) and len(rows) > 1:
                        rows[:] = [r for r in rows if not r["holder"].startswith("unnamed")] or rows
        return found

    def _leaks(self):
        """Leak = memory held by a line never went down over the trailing snapshots, rose
        in at least 3 of those steps, and >= 1 MB was still held at exit. Equal steps are
        allowed: snapshots triggered by *another* line's growth can show no change here."""
        out = {}
        if len(self.snapshots) < 4:
            return out
        for loc, size_end in self.snapshots[-1][1].items():
            if size_end < 1 << 20:
                continue
            series = [s.get(loc, 0) for _, s, _ in self.snapshots]
            rises = 0
            for a, b in zip(reversed(series[:-1]), reversed(series[1:])):  # walk back from exit
                if b < a:
                    break               # it was released at some point: not a steady leak
                rises += b > a
            if rises >= 3:
                out[loc] = rises
        return out

    @staticmethod
    def _symbols(source: bytes) -> dict:
        """line -> {"scope": "Service.handle()", "assigns": [...], "calls": [...]} via ast."""
        try:
            tree = ast.parse(source)
        except (OSError, SyntaxError, ValueError):
            return {}
        out: dict = {}

        def dotted(n):
            if isinstance(n, ast.Name):
                return n.id
            if isinstance(n, ast.Attribute):
                b = dotted(n.value)
                return f"{b}.{n.attr}" if b else None
            return None

        def walk(node, scope):
            for child in ast.iter_child_nodes(node):
                sc = scope
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    sc = scope + [(child.name, not isinstance(child, ast.ClassDef))]
                ln = getattr(child, "lineno", None)
                if ln is not None:
                    e = out.setdefault(ln, {"scope": None, "assigns": [], "calls": []})
                    if sc:
                        e["scope"] = ".".join(n for n, _ in sc) + ("()" if sc[-1][1] else "")
                    targets = []
                    if isinstance(child, ast.Assign):
                        targets = child.targets
                    elif isinstance(child, (ast.AugAssign, ast.AnnAssign)):
                        targets = [child.target]
                    elif isinstance(child, (ast.For, ast.AsyncFor)):
                        targets = [child.target]
                    elif isinstance(child, ast.withitem) and child.optional_vars is not None:
                        targets = [child.optional_vars]
                    for t in targets:
                        for n in ast.walk(t):
                            d = None
                            if isinstance(n, (ast.Name, ast.Attribute)) and isinstance(n.ctx, ast.Store):
                                d = dotted(n)
                            elif isinstance(n, ast.Subscript) and isinstance(n.ctx, ast.Store):
                                base = dotted(n.value)
                                d = f"{base}[…]" if base else None   # self.index[i] = ...
                            if d and d not in e["assigns"]:
                                e["assigns"].append(d)
                    if isinstance(child, ast.Call):
                        d = dotted(child.func)
                        if d and d not in e["calls"] and len(e["calls"]) < 4:
                            e["calls"].append(d)
                walk(child, sc)
        walk(tree, [])
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                first = min([node.lineno] + [d.lineno for d in node.decorator_list])
                out.setdefault(first, {"scope": None, "assigns": [], "calls": []})['end_line'] = node.end_lineno
        return out

    def report(self, script: str) -> dict:
        peak_idx = max(range(len(self.snapshots)), key=lambda i: sum(self.snapshots[i][1].values()),
                       default=None)
        peak = self.snapshots[peak_idx][1] if peak_idx is not None else {}
        end = self.snapshots[-1][1] if self.snapshots else {}
        leaks = self._leaks()
        total = sum(s.python_s + s.native_s + s.system_s + s.cpu_s for s in self.lines.values()) or 1.0
        files: dict[str, dict] = {}
        funcs: dict[str, dict] = {}
        symbols: dict[str, dict] = {}
        hashes = {}
        retained_locations = {loc for _, held, _ in self.snapshots for loc, size in held.items()
                              if size >= 1 << 20}
        for loc in set(self.lines) | set(peak) | set(end) | retained_locations:
            st = self.lines.get(loc) or LineStats()
            t = st.python_s + st.native_s + st.system_s + st.cpu_s
            e = {"time_s": round(t, 4), "share": round(t / total, 4),
                 "python_s": round(st.python_s, 4), "native_s": round(st.native_s, 4),
                 "system_s": round(st.system_s, 4), "cpu_unsplit_s": round(st.cpu_s, 4),
                 "samples": st.samples,
                 "rss_growth_mb": round(st.rss_up / 1e6, 3), "rss_release_mb": round(st.rss_down / 1e6, 3)}
            if st.func is not None:
                e["func_line"] = st.func[1]
            if loc[0] not in symbols:
                source = self._unchanged_source(loc[0])
                symbols[loc[0]] = self._symbols(source) if source is not None else {}
                if source is not None:
                    # VS Code hashes decoded document text, without a UTF-8 BOM.
                    hashes[loc[0]] = self._text_hash(source)
            sym = symbols[loc[0]].get(loc[1])
            if sym:
                if sym["scope"]:
                    e["scope"] = sym["scope"]
                if sym["assigns"]:
                    e["assigns"] = sym["assigns"]
                if sym["calls"]:
                    e["calls"] = sym["calls"]
            if loc in self.holders:
                e["held_by"] = sorted(self.holders[loc], key=lambda r: -r["matching"])[:3]
            if self.memory == "precise":
                e["transient_peak_mb"] = round(st.transient / 1e6, 3)
                e["alloc_mb"] = round(st.traced_up / 1e6, 3)
                e["peak_mb"] = round(peak.get(loc, 0) / 1e6, 3)
                e["end_mb"] = round(end.get(loc, 0) / 1e6, 3)
                if loc in leaks:
                    e["leak_runs"] = leaks[loc]
                if max((held.get(loc, 0) for _, held, _ in self.snapshots), default=0) >= 1 << 20:
                    trend = [(t, held.get(loc, 0)) for t, held, _ in self.snapshots]
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
            if (st.samples == 0 and loc not in leaks and loc not in self.holders
                    and max(e.get("peak_mb", 0), e.get("end_mb", 0), e.get("alloc_mb", 0),
                            e["rss_growth_mb"], e.get('retention', {}).get('peak_mb', 0)) < 0.01):
                continue          # nothing measured on this line: don't pad the report
            files.setdefault(loc[0], {})[str(loc[1])] = e
            if st.func is not None:   # per-function totals: robust to line-level skew
                fname = st.func[0]
                if "." not in fname:
                    # Python < 3.11 has no co_qualname: recover "Class.method" from the source.
                    # co_firstlineno points at the first decorator, so scan forward to the def.
                    syms = symbols[loc[0]]
                    for ln in range(st.func[1], st.func[1] + 20):
                        sc = (syms.get(ln) or {}).get("scope") or ""
                        if sc.endswith("()") and sc[:-2].split(".")[-1] == fname:
                            fname = sc[:-2]
                            break
                fn = funcs.setdefault(loc[0], {}).setdefault(str(st.func[1]), {
                    "name": fname, "time_s": 0.0, "python_s": 0.0, "native_s": 0.0,
                    "system_s": 0.0, "peak_mb": 0.0, "transient_peak_mb": 0.0, "alloc_mb": 0.0,
                    "rss_growth_mb": 0.0})
                fn['end_line'] = symbols[loc[0]].get(st.func[1], {}).get('end_line')
                for k in ("time_s", "python_s", "native_s", "system_s"):
                    fn[k] = round(fn[k] + e[k], 4)
                fn["peak_mb"] = round(fn["peak_mb"] + e.get("peak_mb", 0), 3)
                fn["transient_peak_mb"] = max(fn["transient_peak_mb"], e.get("transient_peak_mb", 0))
                fn["alloc_mb"] = round(fn["alloc_mb"] + e.get("alloc_mb", 0), 3)
                fn["rss_growth_mb"] = round(fn["rss_growth_mb"] + e["rss_growth_mb"], 3)
        hashes = {path: digest for path, digest in hashes.items() if path in files}
        step = max(1, len(self.timeline) // 300)
        stack_frames, frame_ids, stack_samples = [], {}, []
        for (ident, name, stack), values in self.stack_totals.items():
            ids = []
            for fr in stack:
                if fr not in frame_ids:
                    frame_ids[fr] = len(stack_frames)
                    stack_frames.append(dict(zip(('file', 'line', 'name', 'first_line', 'user'), fr)))
                ids.append(frame_ids[fr])
            stack_samples.append({'thread': ident, 'thread_name': name, 'frames': ids,
                                  'python_s': round(values[0], 6), 'native_s': round(values[1], 6),
                                  'system_s': round(values[2], 6), 'unsplit_s': round(values[3], 6),
                                  'samples': values[4]})
        # Caller-only files still need a verified hash for source navigation.
        for fr in stack_frames:
            path = fr['file']
            if fr['user'] and path not in files:
                files[path] = {}
                source = self._unchanged_source(path)
                if source is not None:
                    hashes[path] = self._text_hash(source)
        return {
            "schema": SCHEMA, "script": os.path.abspath(script),
            "python": "%d.%d.%d" % sys.version_info[:3], "gil_split": self.split,
            "wall_s": round(self.wall, 4), "cpu_s": round(self.cpu, 4),
            "interval_s": self.interval, "samples": self.samples, "memory_mode": self.memory,
            "sleep_overhead_s": round(self.sleep_overhead, 6),
            "rss_kind": self.rss_kind,
            "per_thread_cpu": any(c is not None for c in self._clocks.values()),
            "snapshot_cost_s": round(self.snap_cost, 3), "snapshots": len(self.snapshots),
            # Held at the peak by allocations whose traceback never reached your code
            # (deep library internals, e.g. `import pandas`). Raise --frames to attribute more.
            "unattributed_peak_mb": (round((self.snapshots[peak_idx][2] or 0) / 1e6, 3)
                                     if self.snapshots and peak_idx is not None else None),
            "frames": self.frames if self.memory == "precise" else None,
            "peak_traced_mb": round(self.peak_traced / 1e6, 3) if self.memory == "precise" else None,
            "rss_start_mb": round(self.rss0 / 1e6, 3) if self.rss0 else None,
            "rss_end_mb": round(self.rss1 / 1e6, 3) if self.rss1 else None,
            "rss_peak_mb": round(max([c for _, _, c in self.timeline] + [(self.rss1 or 0) / 1e6]), 3),
            # Process-level native estimate: memory the Python allocator never saw
            # (C extensions, NumPy/Arrow buffers, interpreter). Not attributed per line.
            "native_untraced_mb": (round(max(0.0, max((c for _, _, c in self.timeline), default=0)
                                             - self.peak_traced / 1e6
                                             - (self.rss0 or 0) / 1e6), 3)
                                   if self.memory == "precise" and self.rss_kind == "current" else None),
            "timeline": [[round(a, 3), round(b, 3), round(c, 3)] for a, b, c in self.timeline[::step]],
            "file_hashes": hashes, "files": files, "functions": funcs,
            'stacks': {'frames': stack_frames, 'samples': stack_samples,
                       'dropped_s': round(self.stack_dropped_s, 6),
                       'depth_limited': self.stack_depth_limited},
        }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="pmg_profile")
    ap.add_argument("--out", default=os.path.join(".pmg", "profile.json"))
    ap.add_argument("--root", default=None, help="only files under this dir count (default: script dir)")
    ap.add_argument("--interval", type=float, default=0.01)
    ap.add_argument("--memory", choices=("fast", "precise", "off"), default="fast")
    ap.add_argument("--frames", type=int, default=2, help="precise mode traceback depth (cost grows with depth)")
    # One REMAINDER positional preserves a target's own '--' separator.
    ap.add_argument("script_args", nargs=argparse.REMAINDER, metavar="script [args...]")
    ns = ap.parse_args(argv)
    script_args = ns.script_args[1:] if ns.script_args[:1] == ['--'] else ns.script_args
    if not script_args:
        ap.error('a script is required')
    if not 0 < ns.interval < float('inf'):
        ap.error('--interval must be finite and positive')
    if not 1 <= ns.frames <= 64:
        ap.error('--frames must be between 1 and 64')

    script = os.path.abspath(script_args[0])
    out = os.path.abspath(ns.out)  # Target code may change the working directory.
    prof = Profiler(ns.root or os.path.dirname(script), ns.interval, ns.memory, ns.frames)
    sys.argv = [script] + script_args[1:]
    sys.path.insert(0, os.path.dirname(script))
    code = 0
    prof.start()
    main_globals = None

    def finish():
        prof.stop(main_globals)
        os.makedirs(os.path.dirname(out), exist_ok=True)
        with open(out + ".tmp", "w", encoding="utf-8") as f:
            json.dump(prof.report(script), f)
        os.replace(out + ".tmp", out)
        sys.stderr.write(f"[pmg] profile written to {out}\n")

    try:
        # run_path returns the module's globals: used to name leak holders at exit.
        main_globals = runpy.run_path(script, run_name="__main__")
    except SystemExit as e:
        code = e.code if isinstance(e.code, int) else (0 if e.code is None else 1)
        if e.code is not None and not isinstance(e.code, int):
            print(e.code, file=sys.stderr)
    except KeyboardInterrupt:
        code = 130                                    # Ctrl+C: still write what we have
    except BaseException:
        import traceback
        traceback.print_exc()
        code = 1
    finally:
        if code != 130 and any(t is not threading.current_thread() and not t.daemon
                               for t in threading.enumerate()):
            # Let Python perform its normal executor/thread shutdown before the
            # final snapshot. Joining here would deadlock idle executor workers,
            # whose shutdown hooks have not run yet. Keep sampling meanwhile.
            atexit.register(finish)
        else:
            finish()
    return code


if __name__ == "__main__":
    sys.exit(main())
