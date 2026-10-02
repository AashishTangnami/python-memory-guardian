# Changelog

## 1.3.0
- Profiler (precise mode) fixes found from a real profile of a pandas import:
  - Snapshots were ~5x too slow (3.05 s → 0.56 s on 208,595 traces; identical results), so short programs got none. They now work on tracemalloc's raw traces, with a 0.5 s minimum snapshot budget.
  - `rss_end_mb` included the profiler's own final snapshot and could exceed `rss_peak_mb`; it's now measured first.
  - Held memory that can't be traced to your code (deep library internals) is reported as `unattributed_peak_mb` instead of a silent zero, and the depth is configurable (`pythonMemoryGuardian.profile.frames`).
  - Lines with nothing measured are no longer listed in the profile.
- Fix: `npm test` failed with `KeyError: 'Content-Length'` on machines without a system-wide `pygls` (the test started the Python server without its bundled libraries). The server now locates `server/libs/` itself (`server/_vendor.py`), so it works the same from the extension, the tests, or by hand. The parity test now shows the server's real error if it crashes, and skips the Rust comparison with a clear message when the Rust binary isn't built (CI sets `PMG_REQUIRE_RUST=1` to require it).
- Fix: the Python backend failed to start on Python 3.9 and 3.10 when the extension was packaged on Python 3.11+ (`cattrs` 26 needs 3.10+, and `exceptiongroup` was omitted). Runtime packages are now hash-pinned in `requirements.txt`, resolved for Python 3.9, and verified on CPython 3.9, 3.10, 3.12, 3.13 and 3.14.
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
