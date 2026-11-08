"""
Interpreter probe for Python Memory Guardian.

Run by the VS Code client with the user's configured interpreter:
    <python> probe.py   ->  prints one JSON object of "facts" to stdout.

Every number is MEASURED on that interpreter (sys.getsizeof / tracemalloc),
so diagnostics never quote byte counts from a different Python version.
Anything that cannot be determined is simply left out; the language servers
then fall back to version-neutral wording. Must stay compatible with old
interpreters (no walrus, no match), and must never raise.
"""
import json
import os
import platform
import struct
import sys


def _measure(make, n=5000):
    """Average traced bytes per object for n objects (excludes the holding list)."""
    import tracemalloc
    holder = [None] * n          # allocated before tracing starts
    tracemalloc.start()
    before = tracemalloc.get_traced_memory()[0]
    for i in range(n):
        holder[i] = make()
    after = tracemalloc.get_traced_memory()[0]
    tracemalloc.stop()
    return int(round((after - before) / float(n)))


class _Plain(object):
    def __init__(self):
        z = 0
        self.a = z
        self.b = z
        self.c = z


class _Slotted(object):
    __slots__ = ("a", "b", "c")

    def __init__(self):
        z = 0
        self.a = z
        self.b = z
        self.c = z


def probe():
    facts = {}
    impl = platform.python_implementation()
    v = sys.version_info
    facts["implementation"] = impl
    facts["py_version"] = "%d.%d.%d" % (v[0], v[1], v[2])

    # GIL state. Free threading exists from 3.13 (docs: howto/free-threading-python).
    # sys._is_gil_enabled() reports the *runtime* state (PYTHON_GIL=1 can re-enable it).
    try:
        if hasattr(sys, "_is_gil_enabled"):
            facts["gil_state"] = "enabled" if sys._is_gil_enabled() else "disabled"
        elif impl == "CPython":
            facts["gil_state"] = "enabled"
    except Exception:
        pass

    if impl != "CPython":
        return facts  # sizes and allocators below are CPython-specific

    try:
        facts["ptr_size"] = struct.calcsize("P")
        facts["int_size"] = sys.getsizeof(1)
        facts["str_empty"] = sys.getsizeof("")
        facts["str_ascii_1000"] = sys.getsizeof("a" * 1000)
        facts["str_wide_1000"] = sys.getsizeof("a" * 999 + "\U0001F600")
    except Exception:
        pass

    try:
        z = 0
        facts["dict3"] = _measure(lambda: {"a": z, "b": z, "c": z})
        facts["tuple3"] = _measure(lambda: (z, z, z))
        facts["instance3"] = _measure(_Plain)
        facts["slots3"] = _measure(_Slotted)
    except Exception:
        pass

    # Allocator, per docs.python.org c-api/memory + howto/free-threading-python.
    allocator = None
    try:
        import sysconfig
        env = os.environ.get("PYTHONMALLOC", "")
        with_pymalloc = sysconfig.get_config_var("WITH_PYMALLOC")
        if env in ("malloc", "malloc_debug"):
            allocator = "the system malloc (PYTHONMALLOC=%s)" % env
        elif sysconfig.get_config_var("Py_GIL_DISABLED") == 1:
            allocator = "mimalloc heaps (free-threaded build)"
        elif with_pymalloc == 1 or env in ("pymalloc", "pymalloc_debug"):
            if v >= (3, 10) and facts.get("ptr_size") == 8:
                allocator = "pymalloc's 1 MiB arenas"
            else:
                allocator = "pymalloc's 256 KiB arenas"
        elif with_pymalloc == 0:
            allocator = "the system malloc (built without pymalloc)"
    except Exception:
        pass
    if allocator:
        facts["allocator"] = allocator
    return facts


if __name__ == "__main__":
    sys.stdout.write(json.dumps(probe()))
