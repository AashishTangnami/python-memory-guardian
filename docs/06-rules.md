# Static analysis rules

Python Memory Guardian checks Python source as you type and reports memory and concurrency patterns that cost more than they appear to. This page lists every rule. For how the analysis works, see the [technical description](07-technical-design.md#4-static-analysis); for adding or changing a rule, see [developer setup](04-developer-setup.md#change-a-static-rule).

## Reading a warning

Each warning starts with *where* and *what*:

```
Service.handle() › `self.history` — ⚠️ RAM Fragmentation …
Repo › `lookup` — ⚠️ Memory Swell: an unbounded cache …
handler() — ⚠️ Single-Threaded Stall: time.sleep() blocks inside `async def` …
```

A warning describes a likely cost or risk in one syntax pattern. It cannot know the input size, ownership across modules, or whether a growing collection is intentional. Profile a representative workload to see whether it costs anything in your program; a profile also raises or lowers its severity (see [using the extension](02-using-the-extension.md#how-a-profile-changes-warnings)).

To silence one finding, put `# memory-guardian: ignore` on its line:

```python
rows = cur.fetchall()  # memory-guardian: ignore
```

## Rules

The diagnostic code is the key in `server/messages.json`. Both language servers (Python and Rust) report the same codes with the same text.

| Code | Severity | Fires on |
|---|---|---|
| `heap-inflation` | Warning | `fetchall()`, `fetchone()` in loops, `for row in cur.execute(...)` |
| `pointer-chasing` | Warning | pandas imports and row-oriented loaders, however aliased |
| `cyclic-reference` | Info | back-pointer classes instantiated in loops |
| `gc-cycle-risk` | Info | class with a back reference and `__del__` |
| `ram-fragmentation.append` | Warning | `rows.append({...})` / tuple / list per iteration |
| `ram-fragmentation.no-slots` | Info | class without `__slots__` instantiated in a loop. Exceptions, Enums, NamedTuples and `dataclass(slots=True)` are exempt |
| `text-inflation.concat` | Warning | `s += "..."` on a str inside a loop (bytes excluded) |
| `text-inflation.whole-file` | Warning | `f.read().split()` / `.splitlines()`, `f.readlines()` |
| `memory-swell.list-arg` | Warning | `sum/any/all/min/max([...])` |
| `memory-swell.list-copy` | Warning | `for x in list(...)`, or over a list comprehension |
| `memory-swell.list-once` | Info | `rows = list(gen())` or `rows = [...]`, then `rows` used once, only to iterate it: a `for` loop, a comprehension, `sum`/`any`/`all`/`min`/`max`/`iter`/`enumerate`/`zip`, or a function in the same file that only iterates that argument. Quiet when the list is used again, indexed, sliced, measured, rebound, or used inside a nested function |
| `memory-swell.method-cache` | Warning | `@cache` / `lru_cache(maxsize=None)` on a method |
| `memory-swell.unbounded-cache` | Info | the same on a plain function. A bounded `lru_cache` is fine |
| `memory-swell.deepcopy-loop` | Warning | `copy.deepcopy(...)` inside a loop |
| `memory-swell.recompile-loop` | Info | `re.compile("literal")` inside a loop |
| `memory-swell.setdefault-loop` | Info | bare `d.setdefault(key, [])` inside a loop; creates a default list each time |
| `memory-swell.list-extend-loop` | Info | `items.extend([x for ...])` inside an outer loop |
| `task-retention.asyncio-task` | Warning | bare `asyncio.create_task(...)` whose result is discarded |
| `resource-leak.file-handle` | Warning | `open(...)`, `io.open(...)`, or `Path(...).open()` without a visible `with`, `close()`, or returned ownership |
| `single-thread-stall.async-blocking` | Warning | `time.sleep`, `requests.*`, `subprocess.*`, `urlopen` inside `async def` |
| `single-thread-stall.list-membership` | Warning | `x in some_list` inside a loop |
| `single-thread-stall.cpu-thread` | Info | CPU-bound function handed to a thread (below) |

These rules follow the [functools](https://docs.python.org/3/library/functools.html), [copy](https://docs.python.org/3/library/copy.html), [re](https://docs.python.org/3/library/re.html), [asyncio task](https://docs.python.org/3/library/asyncio-task.html), and [gc](https://docs.python.org/3/library/gc.html) documentation. The regex rule is limited to literal `re.compile` calls: Python already caches recent patterns passed to `re.match` and related functions. Ordinary `setdefault` grouping, `list.append`/`extend`, and a `__del__` method alone are not treated as leaks; the added rules require a specific extra allocation or back reference. Modern Python generally collects cycles with finalizers, so `gc-cycle-risk` describes delayed cleanup rather than an inevitable leak.

### When the thread rule fires, and when threads are fine

The rule looks at what the target function does with the data it's given:

- It **fires** when the function (defined in the same file) runs a Python-level loop over its own inputs, makes no I/O or GIL-releasing native calls, and the interpreter has the GIL.
- It **stays silent** in these cases:
  - I/O-bound work: network, files, database, subprocesses.
  - Native libraries that release the GIL: hashlib, zlib, NumPy, Polars, PyArrow, DuckDB, SciPy.
  - Loops that don't depend on the input.
  - Lambdas or functions defined elsewhere, since it won't guess what they do.
  - Free-threaded interpreters.

## Version awareness

At startup the extension runs `server/probe.py` on your interpreter (inside the container in container mode). The probe **measures** object sizes, reads the runtime GIL state, and identifies the memory allocator. Messages quote those numbers; anything the probe can't determine falls back to wording without numbers. Nothing is hard-coded per Python version. To see what was detected, open **View → Output → Python Memory Guardian**:

```
Interpreter profile: {"py_version": "3.12.3", "gil_state": "enabled", "int_size": 28, ...}
```

Each VS Code window probes its own interpreter, so sizes quoted in messages follow that interpreter.

## Sources

- docs.python.org:
  - What's New 3.12 (PEP 623)
  - C-API Memory Management (pymalloc arenas)
  - `tracemalloc`, `sys` (switch interval, `_current_frames`), `threading`
  - Developing with asyncio
  - Programming FAQ ("How do I cache method calls?")
  - Free-threading HOWTO
- peps.python.org: PEP 393, 623, 703, 779.
- CPython Misc/NEWS 2.5a1 (arena release).
