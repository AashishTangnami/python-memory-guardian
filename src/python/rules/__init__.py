"""
Python Memory Guardian - analysis rules (CPython `ast`).

Mirrored rule-for-rule by src/rust/src/, module for module; both render their text
from the shared messages.json using the facts that probe.py measured on the
user's interpreter.

Rule families (diagnostic `code` = messages.json key):
  heap-inflation          fetchall() (eager result materialization)
  pointer-chasing         explicit pandas object dtype
  cyclic-reference        back-pointer classes instantiated in loops
  gc-cycle-risk           back-pointer classes with finalizers
  ram-fragmentation.*     per-row dict/tuple/list appends; no-__slots__ classes in loops
  text-inflation.*        str += in loops; read().split*() / readlines()
  memory-swell.*          list materialization, unbounded caches, deepcopy/re.compile in loops
  resource-leak.*         file opens without visible cleanup
  task-retention.*        discarded asyncio tasks
  single-thread-stall.*   blocking calls in async def; `x in list` in loops;
                          CPU-bound pure-Python functions handed to threads
"""
from __future__ import annotations

import _vendor  # noqa: F401  (must come first: puts src/python/libs on sys.path)

from .analyze import analyze
from .messages import render

__all__ = ["analyze", "render"]
