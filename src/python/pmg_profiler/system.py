"""What the process and the interpreter can measure, with no third-party dependencies."""
from __future__ import annotations

import os
import statistics
import sys
import sysconfig
import time
from typing import Callable


# RSS, no third-party deps
def make_rss_reader():
    """Return (reader, kind). kind: 'current' | 'peak' | None."""
    if sys.platform.startswith("linux"):
        page = os.sysconf("SC_PAGE_SIZE")

        def linux():
            with open("/proc/self/statm", "rb") as f:
                return int(f.read().split()[1]) * page  # memory-guardian: ignore (statm is ~30 bytes)
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

            class MTBI(ctypes.Structure):
                _fields_ = [("virtual_size", ctypes.c_uint64), ("resident_size", ctypes.c_uint64),
                            ("resident_size_max", ctypes.c_uint64), ("user_time", ctypes.c_uint64),
                            ("system_time", ctypes.c_uint64), ("policy", ctypes.c_int),
                            ("suspend_count", ctypes.c_int)]
            lib = ctypes.CDLL('/usr/lib/libSystem.B.dylib')
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


def thread_cpu_clock(ident: int) -> Callable[[], float] | None:
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


def free_threaded_build() -> bool:
    """True on a build that can run without the GIL (3.13t and later), whatever the GIL state now.

    A GIL that is off now proves such a build, so it is the fallback when the build configuration
    cannot be read (a damaged or relocated installation)."""
    try:
        if sysconfig.get_config_var("Py_GIL_DISABLED"):
            return True
    except Exception:
        pass
    return not gil_enabled()


def gil_enabled() -> bool:
    f = getattr(sys, "_is_gil_enabled", None)
    return True if f is None else bool(f())


def sleep_overhead(interval: float) -> float:
    """Measure timer oversleep before user code starts, without GIL contention.

    OS timer coalescing can add milliseconds to sleep even when Python is idle.
    Use a median so an isolated scheduling pause does not skew the baseline.
    Bound startup cost for unusually large requested sampling intervals.
    """
    delay = min(interval, 0.01)
    overshoots = []
    for _ in range(3):
        before = time.perf_counter()
        time.sleep(delay)
        overshoots.append(max(0.0, time.perf_counter() - before - delay))
    return statistics.median(overshoots)
