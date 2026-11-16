"""Regression cases for high-confidence memory and resource diagnostics."""
import asyncio
import copy as cp
import io
import weakref
from pathlib import Path
import re as regex


def copies(rows):
    for row in rows:
        cp.deepcopy(row)  # expect: memory-swell.deepcopy-loop
    cp.deepcopy(rows)


def patterns(rows):
    for row in rows:
        regex.compile(r"\w+")  # expect: memory-swell.recompile-loop
        regex.match(r"\w+", row)  # cached by the re module
        regex.compile(row)  # dynamic pattern may need compilation here


async def tasks(rows):
    for row in rows:
        asyncio.create_task(work(row))  # expect: task-retention.asyncio-task
        task = asyncio.create_task(work(row))
        await task
    async with asyncio.TaskGroup() as group:
        group.create_task(work(1))


async def work(value):
    return value


def files():
    f = open("data.txt")  # expect: resource-leak.file-handle
    g = open("other.txt")
    g.close()
    with open("safe.txt") as h:
        h.read()
    Path("path.txt").open()  # expect: resource-leak.file-handle
    with Path("safe_path.txt").open() as h:
        h.read()
    io.open("legacy.txt")  # expect: resource-leak.file-handle
    open("read-once.txt").read()  # expect: resource-leak.file-handle


def transferred():
    return open("owned-by-caller.txt")


def handed_off(consumer):
    consumer(open("owned-by-consumer.txt"))


def ordinary_growth(rows):
    grouped = {}
    values = []
    for row in rows:
        grouped.setdefault(row, []).append(row)
        values.extend(row)


def avoidable_growth(rows):
    grouped = {}
    values = []
    for row in rows:
        grouped.setdefault(row, [])  # expect: memory-swell.setdefault-loop
        values.extend([item for item in row])  # expect: memory-swell.list-extend-loop


class Finalizer:
    def __del__(self):
        pass


class FinalizerWithBackPointer:  # expect: gc-cycle-risk
    def __init__(self, parent):
        self.parent = parent

    def __del__(self):
        pass


class WeakFinalizer:
    def __init__(self, parent):
        self.parent = weakref.ref(parent)

    def __del__(self):
        pass


# Unbounded functools caches created by a call rather than a decorator.
import functools
from functools import cache as memo, lru_cache


def _source(x):
    return x


called_cache = functools.cache(_source)  # expect: memory-swell.unbounded-cache
aliased_cache = memo(_source)  # expect: memory-swell.unbounded-cache
applied_keyword = lru_cache(maxsize=None)(_source)  # expect: memory-swell.unbounded-cache
applied_positional = functools.lru_cache(None)(_source)  # expect: memory-swell.unbounded-cache
unbounded_factory = functools.lru_cache(maxsize=None)  # expect: memory-swell.unbounded-cache
default_bound = lru_cache(_source)
explicit_bound = lru_cache(maxsize=128)(_source)
empty_bound = lru_cache()(_source)


class InstanceCache:
    def __init__(self):
        self.lookup = lru_cache(maxsize=None)(self._lookup)  # expect: memory-swell.unbounded-cache

    def _lookup(self, key):
        return key


@functools.lru_cache(maxsize=None)  # expect: memory-swell.unbounded-cache
def decorated(x):
    return x


def shadowed():
    def memo(f):
        return f
    return memo(_source)


# An exception stored beyond its handler keeps its traceback, frames and their locals alive.
import traceback


class Collector:
    def __init__(self):
        self.errors = []
        self.by_key = {}
        self.last_error = None

    def keep(self, key):
        try:
            work(key)
        except ValueError as exc:
            self.last_error = exc  # expect: frame-retention.stored-exception
            self.errors.append(exc)  # expect: frame-retention.stored-exception
            self.by_key[key] = exc  # expect: frame-retention.stored-exception
            self.errors.append((key, exc))  # expect: frame-retention.stored-exception
            self.last_error = key, (exc)  # expect: frame-retention.stored-exception
            (self.last_error) = exc  # expect: frame-retention.stored-exception
            self.errors.append(  # a comment between the call and its argument
                exc)  # expect: frame-retention.stored-exception
        except (KeyError, OSError) as exc:
            self.last_error = str(exc)
            self.errors.append(repr(exc))
            self.by_key[key] = traceback.format_exc()
            self.last_error = exc.with_traceback(None)
            last = exc
            raise RuntimeError(key) from last

    def outside_the_rule(self, key, pending, future):
        try:
            work(key)
        except ValueError as exc:
            self.last_error = self.first_error = exc
            self.typed: Exception = exc
            self.errors.extend([exc])
            self.errors.append(exc, *key)
            pending.put(exc)
            future.set_exception(exc)
            self.callbacks.append(lambda: self.errors.append(exc))
        except Exception:
            self.errors.append(key)

    def rebound(self, key):
        try:
            work(key)
        except ValueError as exc:
            exc = str(exc)
            self.errors.append(exc)

    def nested_handlers(self, key):
        try:
            work(key)
        except ValueError as outer:
            try:
                work(key)
            except KeyError as inner:
                self.errors.append(outer)
                self.last_error = inner  # expect: frame-retention.stored-exception
            except OSError:
                self.errors.append(outer)  # expect: frame-retention.stored-exception
            self.last_error = outer  # expect: frame-retention.stored-exception
        except OSError as same:
            try:
                work(key)
            except KeyError as same:
                self.last_error = same  # expect: frame-retention.stored-exception
            self.errors.append(same)


FAILED = {}
try:
    work(0)
except RuntimeError as startup_error:
    FAILED["startup"] = startup_error  # expect: frame-retention.stored-exception


# Queues created without a size limit.
import multiprocessing
import queue
from queue import Queue, SimpleQueue

LIMIT = 100
OPTIONS = {"maxsize": LIMIT}

jobs = queue.Queue()  # expect: memory-swell.unbounded-queue
results = Queue(maxsize=0)  # expect: memory-swell.unbounded-queue
events = asyncio.Queue(0)  # expect: memory-swell.unbounded-queue
shared = multiprocessing.Queue()  # expect: memory-swell.unbounded-queue
ordered = queue.PriorityQueue(maxsize=0x0)  # expect: memory-swell.unbounded-queue
stacked: queue.LifoQueue = (queue.LifoQueue())  # expect: memory-swell.unbounded-queue
bounded = queue.Queue(maxsize=100)
bounded_positional = Queue(100)
sized_by_name = Queue(maxsize=LIMIT)
sized_by_options = Queue(**OPTIONS)
sized_by_args = Queue(*[LIMIT])
negative_size = Queue(-1)
unbounded_by_design = SimpleQueue()


def shadowed_queue():
    def Queue():
        return []
    return Queue()


# The fork start method chosen by name.
METHOD = "fork"

multiprocessing.set_start_method("fork")  # expect: memory-swell.fork-start-method
fork_context = multiprocessing.get_context(method='fork')  # expect: memory-swell.fork-start-method
multiprocessing.set_start_method("fork", force=True)  # expect: memory-swell.fork-start-method
multiprocessing.get_context("fo" "rk")  # expect: memory-swell.fork-start-method
multiprocessing.set_start_method("spawn")
multiprocessing.get_context("forkserver")
multiprocessing.get_context()
multiprocessing.get_context(METHOD)
multiprocessing.get_context(f"fork")
multiprocessing.get_context(b"fork")
multiprocessing.set_start_method(*["fork"])


# A DataFrame or array rebuilt from itself on every iteration.
import numpy as np
import pandas as pd


def grow_frame(chunks):
    frame = pd.DataFrame()
    for chunk in chunks:
        frame = pd.concat([frame, chunk], ignore_index=True)  # expect: memory-swell.concat-loop
    return frame


def grow_arrays(rows, state):
    values = np.empty(0)
    for row in rows:
        values = np.append(values, row)  # expect: memory-swell.concat-loop
        state.matrix = np.vstack((state.matrix, row))  # expect: memory-swell.concat-loop
        values = (np.concatenate([row, (values)]))  # expect: memory-swell.concat-loop
        (values) = np.hstack([values, row])  # expect: memory-swell.concat-loop
    return values


def build_once(chunks, stacked, joined):
    parts = []
    second = None
    for chunk in chunks:
        parts.append(chunk)
        pair = pd.concat([chunk, chunk])
        other = np.append(parts, chunk)
        np.concatenate([parts, chunk])
        first = second = np.hstack([second, chunk])
        stacked: np.ndarray = np.vstack([stacked, chunk])
        joined = np.concatenate(joined)
    whole = pd.concat(parts)
    whole = pd.concat([whole, whole])
    return whole, pair, other, first, joined
