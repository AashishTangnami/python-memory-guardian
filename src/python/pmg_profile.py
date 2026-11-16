"""
Python Memory Guardian - runtime profiler (standard library only). The code lives in pmg_profiler/.

    python src/python/pmg_profile.py [--out .pmg/profile.json] [--root DIR] [--interval 0.01]
                          [--memory fast|precise|off] [--frames N] [--monitoring off|lines]
                          [--trace-function NAME] script.py [args...]

Per line of *your* code (files under --root) it reports:

TIME (sampling; overhead depends on workload and configuration), split three ways for each sample:
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
  fast     (default) process RSS observations, with no allocating-line attribution.
           Counts Python *and* native memory; coarse. The OS assigns pages when
           memory is first written; process growth has no allocating-line provenance.
           Freed memory often stays resident (pymalloc keeps a
           1 MiB arena until every block in it is free), so RSS rarely falls.
  precise  tracemalloc: every Python object allocation at its requested size, whether
           pymalloc or malloc serves it; not memory C extensions take from malloc
           directly. Reports net traced growth at process scope (churn between two
           samples is not counted), bytes still held per traceback at peak and exit,
           allocation stacks at those two snapshots and process-level growth observations.
           Recent sustained retention is a candidate, not proof of a leak. Tracemalloc
           snapshots cannot measure allocation churn or allocating threads. Cost follows the
           allocation rate: 2.9x measured on a light workload, 7.7x on an allocation-heavy
           function (millions of small dicts), at the default 2-frame depth. Both were measured
           before the code moved to pmg_profiler/: an allocation whose traceback reaches the
           launcher frame now costs less, because tracemalloc reads the line of a shorter frame.
           At the default 2 frames that is module level only; a larger --frames also covers
           functions near module level. Snapshots add more when much memory is traced (about 1.6 s each at 400 MB). --trace-function NAME
           (Python 3.12+) traces only while that function runs, so the rest of the program
           runs at full speed; leak detection is then off, because each call starts tracing
           afresh. The native estimate excludes tracemalloc's own bookkeeping. Routine snapshots
           are capped at 10% of elapsed time; peak-capture snapshots (plateau, doubling)
           have their own budgets. Snapshot cost is predicted from tracemalloc's
           bookkeeping size.

Optional Python 3.12+ LINE events record execution coverage. They do not replace
the interval sampler or measure elapsed time, native stacks, or allocations.
"""
from __future__ import annotations

import os
import sys

from pmg_profiler.cli import parse_args
from pmg_profiler.common import register_entry_point
from pmg_profiler.runner import run
from pmg_profiler.session import Profiler

__all__ = ["Profiler", "main"]

# The guard relaunches this file, and the run records it, so it must stay the entry point.
ENTRY_POINT = os.path.abspath(__file__)
register_entry_point(ENTRY_POINT)


def main(argv=None) -> int:
    return run(parse_args(argv), ENTRY_POINT)


if __name__ == "__main__":
    # Call run() from here, not through main(): launcher frames count against the tracemalloc traceback
    # depth and each stack's truncated flag, so a profiled stack keeps exactly two of them.
    sys.exit(run(parse_args(), ENTRY_POINT))
