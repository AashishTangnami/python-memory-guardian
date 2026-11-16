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

The diagnostic code is the key in `src/python/messages.json`. Both language servers (Python and Rust) report the same codes with the same text.

| Code | Severity | Fires on |
|---|---|---|
| `heap-inflation` | Warning | `fetchall()` materializes remaining rows. Cursor iteration, `fetchone()` and bounded `fetchmany()` do not trigger it |
| `pointer-chasing` | Warning | pandas loaders, `Series` or `DataFrame` with explicit `dtype=object`, `dtype="object"` or an object entry in a dtype mapping, however aliased. Imports, unknown input dtypes and explicit numeric dtypes do not trigger it |
| `cyclic-reference` | Info | back-pointer classes instantiated in loops |
| `gc-cycle-risk` | Info | class with a back reference and `__del__` |
| `ram-fragmentation.append` | Warning | `rows.append({...})` / tuple / list per iteration |
| `ram-fragmentation.no-slots` | Info | class without `__slots__` instantiated in a loop. Exceptions, Enums, NamedTuples, `dataclass(slots=True)`, and classes with explicit `setattr(self, ...)` or `self.__dict__` use are exempt |
| `text-inflation.concat` | Warning | `s += "..."` on a str inside a loop (bytes excluded) |
| `text-inflation.whole-file` | Warning | `f.read().split()` / `.splitlines()`, `f.readlines()` |
| `memory-swell.list-arg` | Warning | `sum/any/all/min/max([...])` |
| `memory-swell.list-copy` | Warning | `for x in list(...)`, or over a list comprehension |
| `memory-swell.list-once` | Info | `rows = list(gen())` or `rows = [...]`, then `rows` used once, only to iterate it: a `for` loop, a comprehension, `sum`/`any`/`all`/`min`/`max`/`iter`/`enumerate`/`zip`, or a function in the same file that only iterates that argument. Quiet when the list is used again, indexed, sliced, measured, rebound, or used inside a nested function |
| `memory-swell.method-cache` | Warning | `@cache` / `lru_cache(maxsize=None)` on a method |
| `memory-swell.unbounded-cache` | Info | the same on a plain function, or the cache built by a call: `functools.cache(g)`, `lru_cache(maxsize=None)(g)`, `lru_cache(None)` (including `self.f = lru_cache(None)(self.m)`). A bounded `lru_cache`, `lru_cache(g)` and `lru_cache()` are fine; a `maxsize` held in a variable or a large literal is not checked |
| `memory-swell.deepcopy-loop` | Warning | `copy.deepcopy(...)` inside a loop |
| `memory-swell.recompile-loop` | Info | `re.compile("literal")` inside a loop |
| `memory-swell.setdefault-loop` | Info | bare `d.setdefault(key, [])` inside a loop; creates a default list each time |
| `memory-swell.list-extend-loop` | Info | `items.extend([x for ...])` inside an outer loop |
| `memory-swell.concat-loop` | Warning | inside a loop, a name rebuilt from itself: `df = pandas.concat([df, part])`, `arr = numpy.append(arr, x)`, or `numpy.concatenate` / `vstack` / `hstack` with the assigned name among the pieces (details below) |
| `memory-swell.unbounded-queue` | Info | `queue.Queue()`, `LifoQueue`, `PriorityQueue`, their `asyncio` versions, `multiprocessing.Queue` or `JoinableQueue` created with no `maxsize`, or with a literal `0` (details below) |
| `memory-swell.fork-start-method` | Info | `multiprocessing.set_start_method("fork")` or `multiprocessing.get_context("fork")` with the method as a string literal (details below) |
| `task-retention.asyncio-task` | Warning | bare `asyncio.create_task(...)` whose result is discarded |
| `frame-retention.stored-exception` | Info | inside `except ... as e`, the exception stored where it outlives the block: `obj.attr = e`, `obj[key] = e`, or `obj.append(e)` / `appendleft` / `add`, also as a direct element of a tuple or list (details below) |
| `resource-leak.file-handle` | Warning | `open(...)`, `io.open(...)`, or `Path(...).open()` without a visible `with`, `close()`, or returned ownership |
| `single-thread-stall.async-blocking` | Warning | `time.sleep`, `requests.*`, `subprocess.*`, `urlopen` inside `async def` |
| `single-thread-stall.list-membership` | Warning | `x in some_list` inside a loop |
| `single-thread-stall.cpu-thread` | Info | CPU-bound function handed to a thread (below) |

These rules follow the [functools](https://docs.python.org/3/library/functools.html), [copy](https://docs.python.org/3/library/copy.html), [re](https://docs.python.org/3/library/re.html), [asyncio task](https://docs.python.org/3/library/asyncio-task.html), and [gc](https://docs.python.org/3/library/gc.html) documentation. The regex rule is limited to literal `re.compile` calls: Python already caches recent patterns passed to `re.match` and related functions. Ordinary `setdefault` grouping, `list.append`/`extend`, and a `__del__` method alone are not treated as leaks; the added rules require a specific extra allocation or back reference. Modern Python generally collects cycles with finalizers, so `gc-cycle-risk` describes delayed cleanup rather than an inevitable leak.

### Evidence strength and fix safety

Severity says how much the pattern costs if the finding is right. Two more values in each diagnostic's `data` say how far to trust it and its advice. They do not change what the editor shows; tools that read the diagnostics can use them.

- `evidence.strength`: `syntactic` means one call or expression decided the finding. `structural` means the rule also checked the code around it in the same file: an enclosing loop, class, `except` block or `async def`, or how a name is bound and used in its scope. Neither is a measurement; `confidence` stays `static` for both.
- `fix_safety`: the servers apply no edits, so this describes the alternative that the message names. `maybe-incorrect` means a local rewrite of the reported code that usually keeps its behaviour, but can change it in a case the rule cannot see (for example a list that is iterated twice, or elements that cannot be hashed). `manual` means the alternative changes what the code returns, when it runs, or its design, so only the developer can decide.

| Evidence strength | Fix safety | Rules |
|---|---|---|
| structural | manual | `cyclic-reference`, `gc-cycle-risk`, `ram-fragmentation.append`, `memory-swell.method-cache`, `memory-swell.deepcopy-loop`, `frame-retention.stored-exception`, `single-thread-stall.cpu-thread` |
| structural | maybe-incorrect | `ram-fragmentation.no-slots`, `text-inflation.concat`, `memory-swell.list-once`, `memory-swell.recompile-loop`, `memory-swell.setdefault-loop`, `memory-swell.list-extend-loop`, `memory-swell.concat-loop`, `resource-leak.file-handle`, `single-thread-stall.async-blocking`, `single-thread-stall.list-membership` |
| syntactic | manual | `heap-inflation`, `pointer-chasing`, `memory-swell.unbounded-cache`, `memory-swell.unbounded-queue`, `memory-swell.fork-start-method` |
| syntactic | maybe-incorrect | `text-inflation.whole-file`, `memory-swell.list-arg`, `memory-swell.list-copy`, `task-retention.asyncio-task` |

The table is `RULE_TRAITS` in `src/python/rules/tables.py`, mirrored in `src/rust/src/tables.rs`. A code that is missing from it reports `syntactic` and `manual`, the weaker claim in both fields.

### What the newer rules report, and what they leave alone

Each rule reports one pattern with a known cost. Where the source does not show enough to decide, the rule stays quiet.

**`frame-retention.stored-exception`.** An exception keeps its traceback, and the traceback keeps every frame the error passed through, with their local variables. The language reference gives this as the reason Python deletes the `as` name at the end of the `except` block. A stored exception therefore keeps those objects alive: in `tests/fixtures/static-acceptance/stored_exception_positive.py`, 1,000 stored exceptions hold 2.6 MB (each frame has one 2,000-byte local), and storing `str(exc)` instead holds 57 KB.

- Reported: the name stored in an attribute, in a subscript, or with `append`, `appendleft` or `add`, alone or as a direct element of a tuple or list (`errors.append((row, exc))`).
- Quiet: a local name (`last = exc`), `raise ... from exc`, `return exc`, anything derived from the exception (`str(exc)`, `exc.args`, `exc.with_traceback(None)`), a handler that rebinds the name, and a use inside a nested function, lambda or class.
- Not checked: `queue.put(exc)` and `future.set_exception(exc)` (a hand-over, usually short), a keyword argument such as `Result(error=exc)`, a chained or annotated assignment, `sys.exc_info()`, and an outer handler's exception stored from inside a nested `except ... as` block. Each nested `except ... as` block is searched only for its own name, so analysis time does not grow with nesting.
- It can report an exception that is stored only briefly, for example a field that a `finally` block clears. The rule cannot see how long the holder lives, so it is an Info. It can also report a name that only a nested `except ... as` block rebinds, because that block is not searched.

**`memory-swell.unbounded-queue`.** `maxsize` defaults to `0`, which means no limit, for the `queue`, `asyncio` and `multiprocessing` queues. `put()` then never waits, so a producer that is faster than its consumers fills memory.

- Quiet: any other `maxsize`, including a variable, `*args` or `**kwargs`, because the size is unknown; a negative literal (also unbounded, not checked); and `queue.SimpleQueue`, which is unbounded by design.
- It reports every such queue, including one that only ever holds a few items. That case is fine, and the message says so.

**`memory-swell.fork-start-method`.** A forked worker shares the parent's pages until one side writes to them. CPython writes an object's reference count into the object, so the pages that hold shared Python objects are copied as workers use them. Since Python 3.14, `fork` is not the default start method on any platform.

- Quiet: `"spawn"`, `"forkserver"`, no argument, and a method held in a variable.
- Not checked: code that depends on the platform default, and how much data the workers inherit. Neither is visible in the source.

**`memory-swell.concat-loop`.** `pandas.concat` and `numpy.append` return a new object and copy their inputs. When the result replaces one of the inputs on every iteration, the copying grows with the square of the final size. The pandas documentation says to build a list and concatenate once. Measured with pandas 2.3 on 2,000 chunks of 1,000 rows: 10.8 s in the loop, 0.5 s with one `concat`.

- Reported: only when the assigned name (a plain name or an attribute such as `self.df`) is itself one of the pieces, inside a loop.
- Quiet: `pandas.concat(parts)` after the loop, a result assigned to a different name, `polars.concat` and `pyarrow.concat_tables` (they do not have to copy), and the call outside a loop.
- Not checked: a result that is changed before it is assigned (`df = pandas.concat([df, part]).reset_index()`), keyword pieces (`objs=[...]`), a subscript target, and a chained or annotated assignment.

### Structure warnings and compatible alternatives

Structure warnings are static candidates. They do not measure target object counts, allocation volume, traversal time or hardware cache misses. Sized messages quote synthetic interpreter probes; those sizes do not describe the target query or class. The object-dtype rule requires explicit dtype evidence because a pandas import or loader can produce compact numeric or extension arrays. It does not infer dtypes from a file or follow subsequent dtype changes across modules.

Every LSP diagnostic includes `data` metadata (`schema_version` 2) with its category, severity, `status="potential"`, `confidence="static"`, source-span syntax evidence, and two values that severity alone used to carry: `evidence.strength` and `fix_safety` (see [Evidence strength and fix safety](#evidence-strength-and-fix-safety)). Workload, measured impact and trace artifact are `null` with explicit `unavailable` markers. Static analysis cannot provide runtime evidence from an editor buffer. Both backends use the same metadata contract, and parity tests compare it as well as the message and source range.

A cursor creates Python row objects as it advances, but iteration alone does not retain the whole result. The eager-result warning therefore applies to `fetchall()` only. Cursor iteration or bounded `fetchmany()` is a compatible alternative only when one-pass access meets the program's contract.

A set index requires hashable values with stable hash/equality, compatible duplicate semantics, and synchronization with list mutations. Keep the original list when order or unhashable values are required. Slots require fixed attributes and compatible inheritance, serialization, `__dict__` access and weak references. Columnar alternatives must preserve ordering, mutability, mixed values, null handling and the required access pattern. The analyzer cannot prove these contracts from syntax; the messages state the conditions rather than promise a safe automatic replacement.

`tests/fixtures/static/static_semantics_cases.py` supplies exact positive and negative syntax expectations to both language servers. `tests/static/static_regression_test.py` executes eager/streamed SQL and object/numeric pandas fixtures before it checks diagnostics, and exercises set/slots compatibility failures independently. Build the Rust server before the parity checks so changes to the embedded message catalog are tested.

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

At startup the extension runs `src/python/probe.py` on your interpreter (inside the container in container mode). The probe **measures** object sizes, reads the runtime GIL state, and identifies the memory allocator. Messages quote those numbers; anything the probe can't determine falls back to wording without numbers. Nothing is hard-coded per Python version. To see what was detected, open **View → Output → Python Memory Guardian**:

```
Interpreter profile: {"py_version": "3.12.3", "gil_state": "enabled", "int_size": 28, ...}
```

Each VS Code window probes its own interpreter, so sizes quoted in messages follow that interpreter.

## Sources

- docs.python.org:
  - What's New 3.12 (PEP 623); What's New 3.14 and `multiprocessing` (start methods)
  - Language reference, the `except` clause (why the `as` name is deleted); `traceback.clear_frames`
  - `queue`, `asyncio` queues (`maxsize`)
  - C-API Memory Management (pymalloc arenas)
  - `tracemalloc`, `sys` (switch interval, `_current_frames`), `threading`
  - Developing with asyncio
  - Programming FAQ ("How do I cache method calls?")
  - Free-threading HOWTO
- peps.python.org: PEP 393, 623, 683, 703, 779.
- pandas API reference, `pandas.concat` (notes); NumPy API reference, `numpy.append`.
- CPython Misc/NEWS 2.5a1 (arena release).
