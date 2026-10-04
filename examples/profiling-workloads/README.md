# Profiling workloads

Small scripts to try Python Memory Guardian on. Each one does the same job two ways, a memory-heavy way and a better way, and checks that both give the same answer. Open a script, run **Python Memory Guardian: Profile Current File**, and compare the labels on the two versions.

| Script | Needs | Compares |
|---|---|---|
| [generators.py](generators.py) | standard library | lists vs `yield` generator pipelines; `readlines()` vs streaming a file; `+=` vs `"".join()` |
| [pyarrow_example.py](pyarrow_example.py) | `pyarrow` | one dict per row vs Arrow columns; Python vs Arrow aggregation; reading Parquet back as Python objects vs selected columns |
| [polars_example.py](polars_example.py) | `polars` | eager `read_csv` vs lazy `scan_csv`; a Python row loop vs Polars expressions |

```bash
pip install -r examples/profiling-workloads/requirements.txt   # for the pyarrow and polars scripts
```

Install the packages into the interpreter the extension profiles with (`pythonMemoryGuardian.interpreter`). Each script creates its data in a temporary folder and runs in a few seconds; precise mode takes several times longer.

## Which mode to use

- **Precise** shows Python allocations per line: the net traced growth while a line ran (`alloc`), what was still referenced at the peak snapshot (`held`), and short-lived peaks (`spike`). In the Stack Explorer, set **Measure** to *Memory at peak snapshot* to see which call paths held the memory. Use it for `generators.py` and `pyarrow_example.py`.
- **Fast** shows growth in process memory (RSS) per line. Use it for `polars_example.py`: Polars and Arrow allocate their buffers outside Python, so precise mode cannot see them on your lines. Precise mode reports that memory only as a run-level "native untraced" estimate.

## What to look for

Measured on one machine (macOS, Python 3.13, pyarrow 25, polars 1.44). Your numbers will differ; the contrasts should not.

**generators.py, precise mode**

The streaming versions are generator functions: `squares()` and `evens()` each `yield` one value and pause, and `sum()` pulls values through both stages, so only one value exists at a time. `read_amounts()` does the same for the file, line by line.

- `eager_total` allocates about 81 MB for `squares` and 8 MB for `evens`. `streaming_total`'s stages have no memory label at all, and each stage appears as its own function in the timings and the Stack Explorer.
- `eager_log_sum` adds about 20 MB in fast mode and about 27 MB in precise mode, mostly the line strings `readlines()` creates. Static analysis warns about `readlines()` (`text-inflation.whole-file`) before you run. Precise mode can charge part of that memory to the next line, because it credits memory where a sample lands. `read_amounts()` has no memory label.
- `+=` is flagged by static analysis (`text-inflation.concat`), but here it used *less* memory than `"".join()` (about 2 MB vs 11 MB): CPython can often extend a string in place, while `join()` first collects every part into a list. `join()` was still faster (about 0.35 s vs 0.9 s). This is why the profile matters: it shows which warnings cost something in your actual code.

**pyarrow_example.py, precise mode**

- Building one dict per row grows traced memory by about 290 MB (`rows_as_dicts`), and static analysis flags the per-row append (`ram-fragmentation.append`). With **Measure** set to *Memory at peak snapshot*, `rows_as_dicts` holds about 292 MB of a 296 MB snapshot.
- Building the same table as Arrow columns allocates far less, mostly short-lived Python values while converting; the columns themselves live in Arrow's native buffers.
- `group_by(...).aggregate(...)` shows mostly native time: the work runs in Arrow's C++ code.
- `to_pylist()` turns the table back into a dict per row: a spike of about 320 MB. Reading only the needed columns stays columnar and costs almost nothing in Python memory.

**polars_example.py, fast mode**

- `pl.read_csv(path)` loads every column, including `note`, which the summary never uses; together with the filter, RSS grows by about 195 MB. `scan_csv(...).collect()` reads only `region` and `amount`: about 107 MB.
- Polars work shows as native time, because it runs in Polars' own threads. The `iter_rows()` loop in `python_loop_summary` takes about 10× longer than the expressions and shows mostly as Python time on that line.
- Static analysis has no Polars-specific rules, so this script has no warnings: the profile is the only evidence here.

Use the Stack Explorer's **Frames** control to switch between your functions only and the library frames underneath them.
