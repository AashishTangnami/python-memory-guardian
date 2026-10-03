"""Static-analysis regression input; expect markers are checked over real LSP."""
import threading
from time import sleep
from concurrent.futures import ThreadPoolExecutor


def unrelated():
    values = []
    total = ""
    pool = ThreadPoolExecutor()


def numeric(total: int, values: set, pool):
    for item in range(3):
        total += 1
        if item in values:
            pass
    pool.submit(cpu_work, [1, 2])


async def shadowed_import(sleep):
    await sleep(1)


async def real_import():
    sleep(1)  # expect: single-thread-stall.async-blocking


def local_import():
    from time import sleep as pause
    pause(1)


async def unrelated_alias(pause):
    await pause(1)


def positive():
    values = []
    total = ""
    for item in range(3):
        total += str(item)  # expect: text-inflation.concat
        if item in values:  # expect: single-thread-stall.list-membership
            pass


def closure():
    values = []

    def shadow(values):
        for item in range(3):
            if item in values:
                pass

    def inherited():
        for item in range(3):
            if item in values:  # expect: single-thread-stall.list-membership
                pass

    def mutate_local():
        values = set()

    for item in range(3):
        if item in values:  # expect: single-thread-stall.list-membership
            pass


class Methods:
    values = []

    def first(self):
        names = []

    def second(self, names):
        for item in range(3):
            if item in names:
                pass

    def class_names_are_not_closures(self, values):
        for item in range(3):
            if item in values:
                pass


for i in range(3):
    def defined_in_loop():
        rows = []
        rows.append({"i": 1})
        label = ""
        label += "x"

    class ClassBodyRunsInLoop:
        rows = []
        rows.append({"i": i})  # expect: ram-fragmentation.append

        def method_is_deferred(self):
            rows = []
            rows.append({"i": 1})


def cpu_work(data):
    for x in data:
        x * x


def unrelated_same_name():
    def cpu_work(data):
        sleep(1)


threading.Thread(target=cpu_work, args=([1, 2],))  # expect-gil: single-thread-stall.cpu-thread


def with_local_io(data):
    from time import sleep as pause
    for x in data:
        pause(x)


threading.Thread(target=with_local_io, args=([1, 2],))


def with_unused_io(data):
    def never_called():
        sleep(1)
    for x in data:
        x * x


threading.Thread(target=with_unused_io, args=([1, 2],))  # expect-gil: single-thread-stall.cpu-thread


def reassignments():
    from time import sleep as pause
    pool = ThreadPoolExecutor()
    pool = object()
    pool.submit(cpu_work, [1, 2])
    pause = object()

    async def inner():
        pause(1)


def lambda_scope(values):
    values = []
    for i in range(3):
        predicate = lambda values: i in values
        if i in values:  # expect: single-thread-stall.list-membership
            pass


def inherited_decorator_alias():
    from dataclasses import dataclass as record

    def construct():
        @record(slots=True)
        class Row:
            value: int

        for i in range(3):
            Row(i)


def comprehension_does_not_bind_enclosing_scope():
    values = []

    def inner():
        discarded = [values for values in range(3)]
        for i in range(3):
            if i in values:  # expect: single-thread-stall.list-membership
                pass
