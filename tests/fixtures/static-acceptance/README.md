# Static cost and compatibility witnesses

Build the current Rust source and run the red/green suite from the repository root:

```sh
npm run build:rust
python3 tests/static/static_regression_test.py --report /tmp/pmg-static-results.json
python3 tests/static/parity_test.py
```

The suite executes each positive fixture at 1,000 and 10,000 records before it checks either analyzer. It then executes the corrected fixture at both sizes, verifies the result and the relevant access contract, and checks that the corresponding diagnostic disappears. The report records command order, timestamps, complete stdout, the source and its SHA-256, measured interpreter probes and both backends' exact diagnostic rows. Skipped pandas witnesses are incomplete and cause a nonzero validation exit. The optional witness dependency is pandas; it is separate from production server dependencies.

| Pair | Independent witness | Tool category |
| --- | --- | --- |
| `sqlite_cursor`, `sqlite_fetchone` | Traced Python peak grows with eager retained rows; streamed peak stays bounded with the same sum | `heap-inflation` |
| `pandas_numeric`, `pandas_loader` | Object dtype and `memory_usage(deep=True)` versus numeric dtype, 8 bytes per value, same sum | `pointer-chasing` |
| `slots` | Traced Python storage for fixed-field instances; values, mutation, nulls and order remain compatible | `ram-fragmentation.no-slots` |
| `membership` | Count of equality calls with a list versus a separate set index; the original ordered list remains available | `single-thread-stall.list-membership` |

Run any fixture independently, for example:

```sh
python3 tests/fixtures/static-acceptance/sqlite_cursor_positive.py 10000
python3 tests/fixtures/static-acceptance/sqlite_cursor_fixed.py 10000
```

The SQL and slots tracemalloc figures do not measure native allocations or RSS. Pandas deep storage estimates do not measure hardware cache misses. Membership equality counts are operation witnesses; elapsed time is printed but has no pass threshold. None of these static warnings claims measured target volume: the measurements belong to this validation artifact.

The harness separately demonstrates that a set rejects unhashable values, a mutated hash key can disappear from lookup, and slots reject required dynamic attributes. Those counterexamples constrain the recommendation text. They do not imply those alternatives are unsafe for the compatible fixed fixtures above.
