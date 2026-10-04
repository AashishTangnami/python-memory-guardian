# Changelog

## Unreleased
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
