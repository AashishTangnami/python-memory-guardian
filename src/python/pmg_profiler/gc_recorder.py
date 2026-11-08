"""Garbage collector pauses and counts during the run."""
from __future__ import annotations

import gc
import os
import threading
import time
import tracemalloc

GC_EVENT_CAP = 2048


class GCRecorder:
    """Bounded GC callback measurements. Never retain frames or collected objects."""

    def __init__(self):
        self.events = []
        self.pending = {}
        self.total_collections = self.collected = self.uncollectable = 0
        self.total_pause_s = 0.0
        self.dropped_events = 0
        self.started = None

    def start(self, t0):
        self.t0 = t0
        self.started = self.callback
        gc.callbacks.append(self.started)

    def callback(self, phase, info):
        now = time.perf_counter()
        key = (threading.get_ident(), info['generation'])
        if phase == 'start':
            self.pending[key] = now
            return
        before = self.pending.pop(key, None)
        if before is None:
            return
        pause = now - before
        self.total_collections += 1
        self.total_pause_s += pause
        self.collected += info['collected']
        self.uncollectable += info['uncollectable']
        if len(self.events) >= GC_EVENT_CAP:
            self.dropped_events += 1
            return
        self.events.append({'t': round(now - self.t0, 6), 'duration_s': round(pause, 6),
                            'generation': info['generation'], 'collected': info['collected'],
                            'uncollectable': info['uncollectable'], 'thread': str(key[0]),
                            'process': os.getpid(),
                            'traced_bytes': tracemalloc.get_traced_memory()[0]
                            if tracemalloc.is_tracing() else None})

    def stop(self):
        if self.started in gc.callbacks:
            gc.callbacks.remove(self.started)
        self.started = None
        self.pending.clear()

    def report(self):
        return {'supported': True, 'events': self.events, 'dropped_events': self.dropped_events,
                'total_collections': self.total_collections,
                'total_pause_s': round(self.total_pause_s, 6), 'collected': self.collected,
                'uncollectable': self.uncollectable}
