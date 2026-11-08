"""Precise memory evidence: tracemalloc readings at each sample, snapshots, and what they show.

This module and trace_function.py are one tight pair. They share `trace_lock`, closing a traced window
takes a snapshot here, and `sample` reads the window's start. trace_function calls into this module,
which holds that state, so the dependency goes one way.
"""
from __future__ import annotations

import os
import threading
import time
import tracemalloc
from typing import Literal

from .common import UserFiles, own_memory_paths
from .timeline import Timeline

# Distinct allocation stacks kept per memory-stack table; smaller ones are summed as other_bytes.
MEMORY_STACK_CAP = 20_000
# Peak-capture snapshot budgets, as shares of the traced time (see sample).
PLATEAU_BUDGET = 1.0
DOUBLING_BUDGET = 0.5


class MemoryTracker:
    def __init__(self, mode: str, frames: int, users: UserFiles, timeline: Timeline, scoped: bool = False):
        self.mode = mode
        self.frames = frames
        self.users = users
        self.timeline = timeline
        self.scoped = scoped                # --trace-function: tracemalloc runs only inside its windows
        # s per byte of tracemalloc bookkeeping: measured at 5-8 ns on macOS/CPython 3.13 at 2 and 6 frames.
        # Refined only from snapshots with at least 8 MB of bookkeeping: small snapshots are dominated by
        # fixed costs, and learning from them inflated predictions 20x, blocking every large snapshot.
        self.snap_rate = 8e-9
        # (elapsed s, bytes held per allocating user line, bytes held with no user frame)
        self.snapshots: list[tuple[float, dict[tuple[str, int], int], int]] = []
        self.snap_cost = 0.0
        self._snap_high = 0          # highest traced memory any snapshot has captured
        self._run_high = 0           # running maximum of traced memory at samples
        self._stalled = 0            # consecutive samples without the running maximum rising by 1%
        self.peak_snapshots = 0      # snapshots taken by the plateau trigger, reported for transparency
        self.peak_snap_cost = 0.0    # their cost, budgeted separately from routine snapshots
        self._plateau_cost = 0.0     # plateau and doubling snapshots have their own budgets, so doubling
        self._doubling_cost = 0.0    # snapshots on the way up cannot use up the plateau's
        # Traced growth per stack key (the busiest thread's stack at the sample that saw it): allocation
        # weighted by the full call path, as deep as time stacks, independent of the traceback depth.
        self.alloc_stacks: dict[tuple, int] = {}
        self.book_max = 0            # largest tracemalloc bookkeeping seen (profiler memory, not the program's)
        self._last_rate = 0.0        # measured cost per bookkeeping byte of the last large snapshot, unclamped
        self.trace_lock = threading.Lock()   # sampler memory work vs. a traced-function window opening/closing
        self.traced_s = 0.0          # --trace-function: time inside closed windows
        self.window_open_t = 0.0     # --trace-function: when the current window opened
        self.snapped = False
        # Bytes per allocation traceback, kept only for the snapshot where user lines held the most
        # (the same snapshot as the per-line "held" labels) and for the latest one: (t, bytes, truncated).
        self._peak_stacks: tuple[float, dict, set] | None = None
        self._peak_stacks_held = -1
        self._last_stacks: tuple[float, dict, set] | None = None
        self.peak_traced = 0
        self.memory_tracing_lost_s: float | None = None   # elapsed s when the script stopped tracemalloc

    def precise(self, t: float) -> bool:
        """Whether precise memory evidence is still being collected at elapsed time t.

        The profiled script can call tracemalloc.stop() (interpreter probes and memory tests
        do). After that, snapshots raise and get_traced_memory() reads (0, 0), so precise
        collection ends for the rest of the run and the report says when. A stop() followed
        by start() between two samples cannot be detected.
        """
        if self.mode != "precise" or self.memory_tracing_lost_s is not None:
            return False
        if self.scoped:
            return tracemalloc.is_tracing()    # off between calls of the traced function, by design
        if tracemalloc.is_tracing():
            return True
        self.memory_tracing_lost_s = t
        return False

    def sample(self, now, t0, stacks, names, main, rss, rss_kind, rss_change,
               prev_traced, prev_book, last_snap_t, last_snap_traced):
        """Memory work for one sample: traced memory and its attribution, the timeline point, and
        snapshots. Returns the carried state: previous traced and bookkeeping bytes, last snapshot."""
        self.snapped = False
        traced = book = 0
        precise = self.precise(now - t0)
        if precise:
            traced, interval_peak = tracemalloc.get_traced_memory()
            # tracemalloc's own bookkeeping (one record per live allocation) is process memory the
            # profiler adds; it is subtracted from the native estimate (measured: 294 MB for 1M dicts).
            book = tracemalloc.get_tracemalloc_memory()
            self.book_max = max(self.book_max, book)
            self.peak_traced = max(self.peak_traced, interval_peak, traced)
            if hasattr(tracemalloc, "reset_peak"):          # 3.9+
                tracemalloc.reset_peak()
                # A peak that rose and fell between samples (e.g. inside a C call that
                # held the GIL) - charge it to the line where the thread resumed.
                transient = interval_peak - max(traced, prev_traced)
            else:
                transient = 0
        self.timeline.observe({
            't': round(now - t0, 6), 'traced_bytes': traced if precise else None,
            'traced_growth_bytes': max(0, traced - prev_traced) if precise else None,
            'transient_bytes': max(0, transient) if precise else None,
            'rss_bytes': rss if rss_kind else None,
            'rss_change_bytes': rss_change if rss_kind else None,
            'bookkeeping_bytes': book if precise else None,
        })
        main_stack = stacks.get(main)
        self.timeline.record((now - t0, traced / 1e6, rss / 1e6),
                              (str(main), names.get(main, 'Thread'), main_stack) if main_stack else None)
        if not precise:
            # Tracing is off (a traced-function window is closed): the next window starts from zero.
            return 0, 0, last_snap_t, last_snap_traced
        elapsed = now - t0
        # spike: sudden jump since the previous *sample* -> snapshot now, so
        # short-lived peaks (temporary copies) are attributed to their line.
        spike = traced - prev_traced > max(prev_traced * 0.5, 10 << 20)
        # periodic snapshots scale with runtime (~10 per run, 0.25-2 s apart) so even
        # short programs get enough points for a leak trend
        period = min(2.0, max(0.25, elapsed / 10))
        due = spike or (traced > max(last_snap_traced * 1.25, 1 << 20)
                        and elapsed - last_snap_t > 0.25) or elapsed - last_snap_t > period
        # Peak capture: the snapshot holding the most memory feeds the peak memory stacks and the
        # "held" labels, but routine snapshots are rationed and cost grows with traced memory, so
        # allocation-heavy runs rarely got one near the peak. Memory that grows until exit is
        # captured by the exit snapshot. Two triggers cover the rest, for memory 25% and 5 MB above
        # anything a snapshot has captured:
        #   plateau: the running maximum stopped rising (under 1% for 5 samples) and traced is still
        #            within 10% of it, so memory that rises, stays, then falls is caught where it stays;
        #   doubling: traced is twice the captured high, so a peak too brief for the sampler to see
        #            (one C call holding the GIL) is still captured at half or more of its size.
        # Doubling bounds their total cost at about twice the last snapshot.
        if traced > self._run_high * 1.01:
            self._run_high, self._stalled = traced, 0
        else:
            self._stalled += 1
        above = self._run_high > 1.25 * self._snap_high and self._run_high - self._snap_high > (5 << 20)
        plateau_due = above and self._stalled >= 5 and traced >= 0.9 * self._run_high
        doubling_due = above and traced >= 2 * self._snap_high
        # Predict the snapshot's cost from tracemalloc's bookkeeping size. Routine snapshots stay
        # within 10% of elapsed runtime (at least 0.5 s in total, so short programs still get a few).
        # Their prediction also uses the last large snapshot's measured rate, unclamped, so the cap
        # holds against actual cost (at 6 frames the clamped rate under-predicted and routine
        # snapshots reached 11.3%). Peak-capture snapshots keep the clamped rate, so one slow snapshot
        # cannot block them, and have their own budgets: plateau up to the elapsed runtime (at least
        # 2 s), doubling up to half of it (at least 1 s). The report states their count and cost.
        one = book * self.snap_rate
        # Budgets are shares of the time memory has been traced: the whole run, or with --trace-function
        # only the traced calls (based on the whole run, one 45 s-in call spent 17.8 s on peak snapshots).
        base = elapsed if not self.scoped else self.traced_s + (now - self.window_open_t)
        routine_ok = due and (self.snap_cost - self.peak_snap_cost
                              + book * max(self.snap_rate, self._last_rate) <= max(0.10 * base, 0.5))
        plateau_ok = plateau_due and self._plateau_cost + one <= max(PLATEAU_BUDGET * base, 2.0)
        doubling_ok = not plateau_ok and doubling_due and self._doubling_cost + one <= max(DOUBLING_BUDGET * base, 1.0)
        peak_ok = plateau_ok or doubling_ok
        if routine_ok or peak_ok:
            s = time.perf_counter()
            self.snapshot(elapsed)
            cost = time.perf_counter() - s
            # The snapshot's own temporary objects are traced; without a reset they would raise the
            # next interval's peak, inflating peak_traced and charging a "spike" to a user line.
            # This interval's real peak was already read above.
            if hasattr(tracemalloc, "reset_peak"):
                tracemalloc.reset_peak()
            self.snap_cost += cost
            if peak_ok and not routine_ok:
                self.peak_snap_cost += cost
                self.peak_snapshots += 1
                if plateau_ok:
                    self._plateau_cost += cost
                else:
                    self._doubling_cost += cost
            if book >= 8 << 20:
                self._last_rate = cost / book
                # A slow average clamped to 2-3x the measured range: one snapshot slowed by GC or
                # first-time path resolution must not block the next (plateau) snapshot.
                self.snap_rate = min(20e-9, max(4e-9, 0.8 * self.snap_rate + 0.2 * (cost / book)))
            last_snap_t, last_snap_traced = elapsed, traced
            self._snap_high = max(self._snap_high, traced)
            self.snapped = True
        return traced, book, last_snap_t, last_snap_traced

    def snapshot(self, t: float):
        """Bytes still held per allocating user line, plus held bytes with no user frame.

        Works on tracemalloc's raw trace tuples (domain, size, frames, total_nframe) and
        caches one answer per distinct traceback. Measured on a pandas import (208,595
        traces): the previous filter_traces() + statistics() path took 2.9 s, almost all
        of it Python-level object building. The raw frames are stored most-recent-first
        (verified), the reverse of the Traceback API, so the first user frame found is
        the innermost one."""
        is_user = self.users.is_user
        snap = tracemalloc.take_snapshot()
        raw = getattr(getattr(snap, "traces", None), "_traces", None)
        held: dict[tuple[str, int], int] = {}
        unattributed = 0
        # A traceback resolves to a user line, False (the profiler's own memory) or None (unattributed).
        loc: tuple[str, int] | Literal[False] | None
        if raw is None:                      # private layout changed: slow, public path
            for stat in snap.statistics("traceback"):
                for fr in reversed(stat.traceback):
                    if is_user(fr.filename):
                        loc = (os.path.abspath(fr.filename), fr.lineno)
                        held[loc] = held.get(loc, 0) + stat.size
                        break
                else:
                    unattributed += stat.size
            self.snapshots.append((t, held, unattributed))
            return
        # Inline is_profiler_file without its realpath fallback: this test runs once per frame of each new
        # traceback, so it must not read the disk. The data comes from common, so the two cannot drift.
        # pmg_diagnostics frames are not own memory: like library frames, they pass to the calling line.
        own_files, own_dirs = own_memory_paths()
        own = {*own_files, os.path.normcase(tracemalloc.__file__)}
        # The loop runs while tracemalloc is still tracing, so every object it creates is itself traced,
        # and an integer addition creates one (about 1 us per trace, 96% of the loop). Each trace therefore
        # only appends a reference to its existing size object; sums run in C (sum()) once per traceback,
        # and per-line "held" totals and the memory-stack table are derived from those. Measured 9x faster.
        cells: dict[tuple, list] = {}                       # traceback -> [sizes, truncated, user line | False | None]
        for trace in raw:
            frames = trace[2]
            cell = cells.get(frames)
            if cell is None:
                loc = None
                for fn, ln in frames:                       # most recent first
                    if fn.startswith("<"):
                        continue
                    n = os.path.normcase(os.path.abspath(fn))
                    if n in own or n.startswith(own_dirs):     # the profiler's own memory
                        loc = False
                        break
                    if is_user(fn):
                        loc = (os.path.abspath(fn), ln)
                        break
                cell = cells[frames] = [[], False, loc]
            cell[0].append(trace[1])
            if not cell[1] and trace[3] > len(frames):
                cell[1] = True
        by_stack: dict[tuple, int] = {}
        truncated: set = set()
        for frames, (sizes, cut, loc) in cells.items():
            size = sum(sizes)
            if loc:
                held[loc] = held.get(loc, 0) + size
            elif loc is None:
                unattributed += size
            if loc is not False:                            # every allocation but the profiler's own
                by_stack[frames] = size
                if cut:
                    truncated.add(frames)
        self.snapshots.append((t, held, unattributed))
        total_held = sum(held.values())
        self._last_stacks = (t, by_stack, truncated)
        if total_held > self._peak_stacks_held:
            self._peak_stacks_held, self._peak_stacks = total_held, self._last_stacks

    def leaks(self):
        """Recent sustained-growth candidates. A plateau erases old warmup rises."""
        out = {}
        if len(self.snapshots) < 4:
            return out
        for loc, size_end in self.snapshots[-1][1].items():
            if size_end < 1 << 20:
                continue
            series = [s.get(loc, 0) for _, s, _ in self.snapshots[-5:]]
            changes = [b - a for a, b in zip(series, series[1:])]
            limits = [max(1024, .001 * a) for a in series[:-1]]
            meaningful = [change > limit for change, limit in zip(changes, limits)]
            rises = sum(meaningful)
            if (rises >= 3 and any(meaningful[-2:])
                    and not any(change < -limit for change, limit in zip(changes, limits))
                    and series[-1] - series[0] >= max(64 << 10, .01 * size_end)):
                out[loc] = rises
        return out

    def memory_stacks(self, function_at) -> dict | None:
        """Peak and exit allocation stacks for the report, outermost frame first."""
        if self.mode != 'precise' or self._last_stacks is None:
            return None
        frames: list[dict] = []
        frame_ids: dict[tuple, int] = {}

        def table(entry):
            t, by_stack, truncated = entry
            ranked = sorted(by_stack.items(), key=lambda kv: -kv[1])
            out = []
            for tb, size in ranked[:MEMORY_STACK_CAP]:
                ids = []
                outer_first = tb[::-1]                         # tracemalloc stores most recent first
                for i, (filename, line) in enumerate(outer_first):
                    # The same file and line as the next (inner) frame: this frame called an inline scope there.
                    enclosing = i + 1 < len(outer_first) and outer_first[i + 1] == (filename, line)
                    key = (filename, line, enclosing)
                    if key not in frame_ids:
                        file = filename if filename.startswith('<') else os.path.abspath(filename)
                        name, first_line = function_at(file, line, enclosing)
                        frame_ids[key] = len(frames)
                        frames.append({'file': file, 'line': line, 'name': name, 'first_line': first_line,
                                       'user': not filename.startswith('<') and self.users.is_user(filename)})
                    ids.append(frame_ids[key])
                # Like time stacks, start at the outermost user frame: the launcher and profiler frames
                # above the script are not part of the program. Stacks without user code stay whole.
                first_user = next((i for i, fid in enumerate(ids) if frames[fid]['user']), 0)
                out.append({'frames': ids[first_user:], 'bytes': size, 'truncated': tb in truncated})
            return {'t': round(t, 4), 'stacks': out, 'total_bytes': sum(by_stack.values()),
                    'other_bytes': sum(size for _, size in ranked[MEMORY_STACK_CAP:])}
        peak = table(self._peak_stacks) if self._peak_stacks else None
        return {'depth': self.frames, 'frames': frames, 'peak': peak, 'exit': table(self._last_stacks)}

    def native_estimate(self, rss_max, rss0) -> float | None:
        """Process memory beyond the traced peak and tracemalloc's largest bookkeeping, in bytes.
        get_tracemalloc_memory() misstates tracing's real RSS cost in both directions (measured with 2
        frames: +52 MB unreported on 300k dicts with 72 MB of bookkeeping; -125 MB on 1M dicts), and in a
        profiled 300k-dict run 102 MB remained. So an estimate within twice the bookkeeping cannot be told
        apart from tracing overhead and is reported as 0; native buffers (PyArrow, Polars) clear it."""
        # The largest bookkeeping, like the largest traced memory: RSS rarely falls, so its peak reflects the
        # highest of both even when it is reached later (with the bookkeeping at the RSS peak instead, a
        # pure-Python run kept 492 MB of "native" memory).
        book = self.book_max
        native = rss_max - self.peak_traced - book - (rss0 or 0)
        return native if native > 2 * book else None
