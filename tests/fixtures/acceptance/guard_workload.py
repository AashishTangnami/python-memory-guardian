"""Bounded memory-pressure witness; --fixed preserves work with bounded retention."""
import argparse
import json
import os
import sys
import time

parser = argparse.ArgumentParser()
parser.add_argument('--blocks', type=int, default=96)
parser.add_argument('--fixed', action='store_true')
parser.add_argument('--starve', action='store_true')
parser.add_argument('--native', action='store_true')
args = parser.parse_args()
if args.native:
    import mmap
    import tracemalloc
    tracemalloc.start()
if args.starve:
    seed = os.urandom(4 << 20)
    sys.setswitchinterval(10)
held = []
total = 0
for _ in range(args.blocks):
    value = mmap.mmap(-1, 4 << 20) if args.native else bytearray(4 << 20)
    value[:] = seed if args.starve else os.urandom(len(value))
    total += len(value)
    if not args.fixed:
        held.append(value)
    elif args.native:
        value.close()
    if not args.starve:
        time.sleep(.005)
if args.starve:
    end = time.perf_counter() + .5
    while time.perf_counter() < end:
        pass
if sys.platform == 'win32':
    import ctypes
    from ctypes import wintypes

    class Counters(ctypes.Structure):
        _fields_ = [('cb', wintypes.DWORD), ('faults', wintypes.DWORD)] + [
            (name, ctypes.c_size_t) for name in ('peak', 'rss', 'peak_paged',
             'paged', 'peak_nonpaged', 'nonpaged', 'pagefile', 'peak_pagefile')]
    kernel = ctypes.windll.kernel32
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    api = ctypes.windll.psapi.GetProcessMemoryInfo
    api.argtypes = [wintypes.HANDLE, ctypes.POINTER(Counters), wintypes.DWORD]
    value = Counters()
    value.cb = ctypes.sizeof(value)
    if not api(kernel.GetCurrentProcess(), ctypes.byref(value), value.cb):
        raise OSError('independent peak-RSS observation unavailable')
    peak = int(value.peak)
else:
    import resource
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    if sys.platform != 'darwin':
        peak *= 1024
print(json.dumps({'bytes_processed': total, 'retained_bytes': sum(map(len, held)),
                  'peak_rss_bytes': peak,
                  'python_peak_bytes': tracemalloc.get_traced_memory()[1] if args.native else None}))
