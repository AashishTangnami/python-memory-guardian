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
