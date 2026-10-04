# Changelog

## Unreleased
- Fixed: precise mode's native memory estimate (`native ≈` and the Overview card) included `tracemalloc`'s own bookkeeping, so pure-Python code showed hundreds of MB of "native" memory (492 MB on `generators.py`). The bookkeeping is now subtracted, and an estimate within twice it counts as none, because `tracemalloc` misstates its own cost in both directions.
- Profile Current File asks for the script's arguments (remembered per file), and offers **precise, only while `f()` runs** when the cursor is inside a function (Python 3.12+): `tracemalloc` runs only during that function, so the rest of the program runs at full speed (4.3× instead of 10.7× on `generators.py`). The profiler CLI has the same option as `--trace-function NAME`.
- Overview adds **Phases**: the run split by what the main thread was doing, with each phase's peak traced memory, peak RSS and the new RSS it needed beyond earlier phases, so variants run in one process can be compared even though RSS stays high. Phases can narrow Memory diagnosis or focus the Stack Explorer.
- Stack Explorer adds **Allocated (sampled, full call paths)** in precise mode, showing which call paths allocated (including the caller that keeps the data), and a focus filter that shows only stacks through one function, to separate a helper shared by several callers.
- New static rule `memory-swell.list-once`: a list built with `list(...)` or a comprehension and then used only once to iterate it (both language servers).
- Fixed: with only one function traced, Python memory allocated outside it was reported as native memory (219 MB of plain strings in one test). Scoped runs now show no native estimate, the Overview shows leak detection as off instead of "0 suspected growing lines", the Compare tab warns when two runs traced different parts of the program, and a traced call still running at exit counts toward the traced time.
- Fixed: deleting another folder's `.pmg/profile.json` in a multi-root workspace cleared the loaded report.
- Routine precise-mode snapshots now keep to their 10% budget by measured cost, and with `--trace-function` all snapshot budgets count only the traced time. Docs state the measured precise-mode overhead (about 3× to over 10×) instead of "2-5×".
- Profiles record the main thread's stack at each timeline point, allocation per call stack, `tracemalloc`'s peak bookkeeping and each line's bookkeeping growth; the run summary includes phases, the traced function and the bookkeeping.

## 1.4.2
- Compare runs: **Save Profile as Baseline** keeps a copy of the loaded profile (with the git commit) in `.pmg/baselines/`, and the report's new **Compare** tab shows what changed per run, function, allocating function and line. Functions match by file and name, so edits that move lines still compare. Only changes beyond run-to-run variation are marked better or worse (thresholds measured on repeated identical runs); changes caused by the memory peak moving elsewhere are marked as context; differences in Python, mode, traceback depth, interval or script arguments are warned about.
- Every profiling run writes `.pmg/summary.json` (`pmg-summary/1`, schema in `docs/pmg-summary.schema.json`): totals, top functions and lines, memory stacks, retention findings, largest objects, static warnings for open profiled files, source freshness, the selected baseline comparison and next steps, each section with its measurement method and limits, for scripts and AI agents to read without the report.
- Profiles record how the run was started (`run`: the script's arguments, UTC start time and platform), so compared runs can be checked for the same workload.
- Fixed: in precise mode, the time a memory snapshot paused your program was charged to whichever line ran next (measured: 0.5 s on a generator that takes 0.04 s). Snapshot pauses are now excluded from line and stack times.
- Fixed: in container mode, the Stack Explorer's memory measures kept container paths for their frames, so their source could not be opened.
- Precise mode estimates native memory per line for any C extension (`native ≈`: process growth beyond traced Python growth), in labels, the Top functions table and an Overview card.
- Memory diagnosis lists the largest objects still alive at exit and who holds them (globals and your instances' attributes), sized by what each object reports; works in fast and precise modes.
- Stack Explorer adds a **Direction** control: *Bottom-up* puts each function where time was spent or memory allocated at the top, merged across its call lines, with the callers that led there below.
- In precise mode, drag across the Overview memory chart to narrow **Memory diagnosis** to a time window: each line's highest snapshot in the window and what it held at the window's end.
- Stack Explorer adds **Memory at peak snapshot** and **Memory held at exit** measures in precise mode: the chart, Top functions and callers/callees are sized by bytes allocated along each call path, from `tracemalloc` tracebacks (depth set by `profile.frames`).
- Precise mode captures the memory peak much more often: snapshots are taken when memory plateaus at a new high and each time it doubles, each with its own time budget. On the bundled examples, the peak snapshot now holds 91% of the PyArrow example's traced peak (previously 3%).
- Precise-mode snapshots are about 9x faster, the cost estimate no longer blocks large snapshots, and the profiler's own snapshot objects no longer inflate the reported traced peak.
- Labels and docs now say what each memory number means: `alloc` is net traced growth (churn between samples is not counted), and fast-mode RSS growth is charged where memory is first written.
- Stack Explorer stays readable with thousands of call paths: calls too narrow to read merge into one `+N smaller calls` box, labels appear only where they fit, depth is capped with a breadcrumb trail, and **Hottest path** zooms to the most expensive chain. A new **Top functions** table sums each function's time across call paths (self, total, memory for your functions, callers), and selecting one shows its callers and callees. The 25,000-node display limit no longer drops time from upper levels.
- Add `examples/profiling-workloads/`: scripts that compare lists and generators, Python rows and PyArrow columns, and eager and lazy Polars, with notes on what each profile shows.

## 1.4.1
- Stack Explorer explains where each frame comes from: boxes are colored by origin (your code, one color per installed package, standard library, Python internals) with a legend, frames such as `<frozen importlib._bootstrap_external>` read as "Python import system", durations show in ms, and the details line gives each frame's share of the run and whether time was spent in it or in its callees.
- Stack Explorer adds a **Frames** control: group consecutive library or Python-internal frames into one box (default), show all frames, or show only your code with library time counted in the calling function. Totals are identical in every view.
- The sampler walks each thread's stack once per sample instead of twice and caches per-function facts, about 2.8× less sampler work per sample on deep multi-threaded stacks. Less time holding the GIL also means less distortion of the native/Python time split. Report output is unchanged.
- Line-event coverage (`profile.monitoring: lines`) now switches itself off for library and standard-library lines after their first event, instead of running a Python callback on every library line. On a library-heavy workload, its overhead fell from 3.2× to 1.25× while user-line counts stay exact.
- The profiler's memory timeline is now bounded during the run (at most 600 evenly spaced points), so long runs no longer grow profiler memory that was charged to your busiest line as RSS or allocation growth. RSS is read once per sample, and the reported RSS peak still includes spikes between kept points.
- Precise mode no longer loses the whole profile when the profiled script stops `tracemalloc`: memory evidence ends at that point, leak detection is skipped, timing continues, and the status tooltip and report say when tracing stopped. A sampler failure is also recorded instead of silently ending sampling.
- Reduce editor work per keystroke while a profile is loaded: only edits that change a profiled file's freshness re-adjust its diagnostics and refresh the report; source hashes are cached per document version; the report sends only referenced stack frames and skips drawing a hidden Stack Explorer.
- Changing `profile.*` or `trace.server` settings no longer restarts the language server or re-runs the interpreter probe.
- Add a macOS/Linux `scripts/install-local.js` helper to install locked npm packages, build the VSIX, and force-install it into local VS Code with one command.
- Add a one-click graph action to JSON editor tabs for opening valid Memory Guardian profiles in the visual report.

## 1.4.0
- Open generated or selected saved JSON profiles in an Overview with run metrics, an interactive process-memory timeline, and top sampled source lines. New timelines retain a post-script memory sample.
- Add opt-in Python 3.12+ `sys.monitoring` line-event coverage with unavailable-tool fallback; keep sampled timing and stacks unchanged, and avoid marking observed but unsampled lines cold.
- Keep one profiler implementation at `server/pmg_profile.py`; remove the duplicate root script and its synchronization check.
- Add a native Memory Guardian Report, with retained-memory trends, observed holders, and targeted investigation recommendations before the interactive Stack Explorer view.
- Preserve caller stacks in schema-3 reports; add thread/time-category filters, zoom, search, and verified source navigation. Continue reading schema-2 profiles.
- Distinguish suspected growing retention from intentional/unconfirmed retention and released allocations; retain evidence from intermediate snapshots.
- Preserve target arguments and relative output destinations, normalize encoded source text for freshness, and finalize threaded runs after normal Python thread/executor shutdown.
- Use complete source function ranges for unsampled-line heat, restore diagnostic severities immediately on edits, serialize language-client restarts, cancel closed-document analysis, and keep failed container probes neutral.
- Add native retention benchmarks and regression checks for diagnosis, bounded caches, stack accounting, source ranges, CLI behavior, and editor/server lifecycle.
- Calibrate idle timer oversleep before profiling and exclude sampler work from the GIL-delay estimate, fixing native hashing misclassified as Python on macOS.
- Pin cattrs to 25.3.0 so the vendored runtime resolves for Python 3.9, and add a VSIX content/server smoke test.
- Add the missing `.vscodeignore` to exclude development dependencies, caches, bytecode, and build/test artifacts from packaged extensions.
- Invalidate runtime profiles for source files changed, replaced, deleted, or created during profiling, including edits before the first sample. Only unchanged files receive freshness hashes and source-derived labels.
- Preserve transient traced-memory peaks before resetting each sampling interval, so the reported run peak includes short-lived allocations.
- Isolate variable, import, and callable tracking across function, class, and lambda scopes in both backends, respecting parameter/local shadowing and enclosing function bindings.
- Add explicit scope expectations to Python/Rust parity tests and regression tests for source freshness and transient peaks.

## 1.3.0
- Profiler (precise mode) fixes found from a real profile of a pandas import:
  - Snapshots were ~5x too slow (3.05 s → 0.56 s on 208,595 traces; identical results), so short programs got none. They now work on tracemalloc's raw traces, with a 0.5 s minimum snapshot budget.
  - `rss_end_mb` included the profiler's own final snapshot and could exceed `rss_peak_mb`; it's now measured first.
  - Held memory that can't be traced to your code (deep library internals) is reported as `unattributed_peak_mb` instead of a silent zero, and the depth is configurable (`pythonMemoryGuardian.profile.frames`).
  - Lines with nothing measured are no longer listed in the profile.
- Fix: `npm test` failed with `KeyError: 'Content-Length'` on machines without a system-wide `pygls` (the test started the Python server without its bundled libraries). The server now locates `server/libs/` itself (`server/_vendor.py`), so it works the same from the extension, the tests, or by hand. The parity test now shows the server's real error if it crashes, and skips the Rust comparison with a clear message when the Rust binary isn't built (CI sets `PMG_REQUIRE_RUST=1` to require it).
- Fix: the Python backend failed to start on Python 3.9 and 3.10 when the extension was packaged on Python 3.11+ (`cattrs` 26 needs 3.10+, and `exceptiongroup` was omitted). Runtime packages are version-pinned in `requirements.txt` and resolved for Python 3.9. The incompatible cattrs pin in this version is corrected in Unreleased above.
- Profiler: qualified function names (`Service.handle`) on Python 3.9/3.10 too.
- Container support: Dev Containers / Codespaces (extension runs in the container) and plain Docker/Compose ("container mode" with exec prefix and path mappings).
- Every diagnostic names its scope and subject (e.g. `Service.handle() › \`rows\``).
- Runtime profiler: per-line time split (Python / native / system), fast and precise memory modes, leak detection that names the holding variable.
- Static diagnostics adjust severity from measured profiles (hot raised, cold lowered).

## 1.1.0
- Native-Python rules: RAM fragmentation, text inflation, memory swell, single-threaded stall.
- Version-aware messages measured on the configured interpreter.

## 1.0.0
- Initial release: heap inflation, pointer chasing and cyclic reference rules; Python and Rust language servers.
