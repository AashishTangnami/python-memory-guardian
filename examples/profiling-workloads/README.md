# Profiling workloads

Three realistic pipelines to try Python Memory Guardian on. Each fetches records from a fake paginated JSON API (no network), stores the raw pages, normalizes them to a fixed schema and writes compressed output, in a memory-heavy variant and a lighter one, and checks that both produce the same output. Each script's docstring describes its variants and options.

| Script | Needs | Variants (`--mode`) |
|---|---|---|
| [generators.py](generators.py) | standard library | `eager` (whole-dataset lists) vs `streaming` (generator pipeline); `--basics` adds lists vs generators, `readlines()` vs streaming a file, `+=` vs `"".join()` |
| [pyarrow_example.py](pyarrow_example.py) | `pyarrow` | `python` (row dicts) vs `eager` (whole Arrow tables) vs `streaming` (Arrow batches) |
| [polars_example.py](polars_example.py) | `polars` | `python` (row loops) vs `eager` (`read_json`) vs `lazy` (`scan` + streaming sink) |

```bash
pip install -r examples/profiling-workloads/requirements.txt   # for the pyarrow and polars scripts
```

Install the packages into the interpreter the extension profiles with (`pythonMemoryGuardian.interpreter`). Each run writes its output under a new folder in `--output` (default `pipeline_output/` in the working directory) and keeps earlier runs. Folders named `*_output/` here are ignored by git.

## Profiling them from the editor

Open a script and run **Python Memory Guardian: Profile Current File**. After choosing a memory mode, enter the script's arguments; they are remembered for that file. Without arguments every script processes 2,000,000 records with one variant, which takes minutes in precise mode and compares nothing. Start with:

| Script | Arguments | Mode |
|---|---|---|
| `generators.py` | `--records 200000 --mode both --basics` | fast first, then precise |
| `pyarrow_example.py` | `--records 100000 --mode all --in-process` | fast (Arrow buffers are native memory) |
| `polars_example.py` | `--records 100000 --mode all --in-process` | fast (Polars allocates in native threads) |

- **`--in-process`** (PyArrow and Polars): by default these scripts run each variant in a separate process, which the profiler does not follow, so its lines would show no time or memory. With `--in-process` everything runs in the profiled process; the scripts' own printed RSS numbers then become cumulative, so read the profile instead.
- **`--trace-memory`** starts and stops `tracemalloc` inside the script. Do not combine it with precise mode: when the script stops tracing, the profiler's precise evidence ends there (the report says when).
- **Precise mode for one function**: with the cursor inside a function (for example `process_eager`), the mode list offers *precise, only while `process_eager()` runs* (Python 3.12+). Everything else runs at full speed.

## What to look for

Measured on one machine (macOS, Python 3.13) for `generators.py --records 200000 --mode both`; your numbers will differ, the contrasts should not.

**Time.** Eager and streaming take the same time per phase (ingest 1.9 vs 1.8 s, process 3.1 vs 3.0 s in fast mode). The difference is memory.

**Phases** (Overview, below the memory chart). The run splits into ingest / process / verify for each variant:

| Phase | Peak traced (precise) | New RSS (fast) |
|---|---|---|
| `ingest_eager` | 130 MB | +147 MB |
| `process_eager` | 403 MB | +262 MB |
| `ingest_streaming` | 15 MB | 0 |
| `process_streaming` | 3 MB | 0 |

Total RSS stays high through the streaming phases, because memory freed after the eager variant stays resident for reuse. *New RSS*, the memory a phase needed beyond what earlier phases had made resident, shows that streaming needed none.

**Who keeps the memory.** Select **Focus stacks** on `process_eager` and set the Stack Explorer's **Measure** to *Allocated (sampled, full call paths)*. The 403 MB splits by line of `process_eager`: about 197 MB under `list(iter_raw_records(...))` (line 321), 147 MB under `normalize`, and 46 MB under `json_line`. Focused on `process_streaming`, `normalize` shows no net allocation growth: each record is released before the next. The plain per-line `alloc` labels point into the generators instead (`for number, line in enumerate(f, 1)`), because allocation is charged to the line running when it happens; the measure above also shows the consumer.

**Deeper tracebacks.** With the default 2-frame tracebacks, 195 MB held at the peak was allocated inside `json.loads` and never reached your code. The run summary (`.pmg/summary.json`) and the status tooltip say so. With `pythonMemoryGuardian.profile.frames` at 6 it lands on line 224, `yield json.loads(line)`, at about twice the run time.

**Cost.** 11.4 s without the profiler, 11.9 s in fast mode, 122 s in precise mode (tracing millions of small dicts), and 48.6 s in precise mode traced only while `process_eager` runs.

**Native memory.** `generators.py` uses no C-extension memory to speak of, and precise mode reports "none detected" beyond `tracemalloc`'s own bookkeeping (about 300 MB here).

**Static warnings**, before running anything: `memory-swell.list-once` on lines 49-50 (`eager_total`) and 321-323 (`process_eager`), where each list is built and then only iterated once; `text-inflation.whole-file` on `readlines()` (line 71); `text-inflation.concat` on `+=` (line 88). `ingest_eager` (line 204) is not flagged: its list is sliced and measured with `len()`, so it really is needed. In the `--basics` section, `+=` used *less* memory than `"".join()` here, because CPython can often extend a string in place while `join()` first collects every part. The profile shows which warnings cost something in your actual code.

**PyArrow and Polars.** Use fast mode: their buffers are native memory, which RSS sees and `tracemalloc` does not. In precise mode they appear as `native ≈` on the lines that create them. These two scripts were not re-measured for this README.
