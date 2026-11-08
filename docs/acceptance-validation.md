# Acceptance validation

Run the permanent witness suite from the repository root:

```sh
python3 tests/acceptance/acceptance_suite.py --include-overhead --out .validation/acceptance-current
```

The runner builds the current Rust backend before checks. Use `--reuse-rust-build` after a separate current build; the runner checks binary and source timestamps and refuses to treat a stale backend as validated. Python language-server checks require `src/python/libs`. Pandas witnesses require Pandas in the selected interpreter. Missing dependencies are unsupported observations, not passing cases.

The report writes nine criterion results to `report.md` and `results.json`. Each criterion is **pass**, **fail**, **partial** or **unsupported**. Separate case counts list **passed**, **failed**, **skipped** and **unsupported**. A passing test means its executed assertion succeeded. It does not prove complete support for a criterion. The full criterion remains partial when implemented coverage has a known limit.

Exit status is `0` only if all nine criteria pass completely. Status `1` means a measured fixture, required build or overhead check failed. Status `2` means required coverage or a measurement is incomplete. The default run omits overhead and therefore leaves criterion 9 unsupported with status `2`; an omitted run is not a pass.

## Saved evidence

The runner executes runtime, cooperative-diagnostics, static-regression and allocation-backend unittest modules in separate processes. Each fixture suite proves its problematic behavior without the tool before its positive check and proves its fixed behavior before the negative check. The runner preserves temporary fixture sources, profiles and JSONL traces instead of deleting them. It records actual subprocess commands, outputs, return codes and elapsed time in each module's `cases.json`. Static checks also preserve their independent cost witnesses and both backend outputs.

Direct cooperative API tests preserve their diagnostic summaries and event traces. Each case record includes its source line, source hash, command and artifact references. `manifest.json` contains a SHA-256 hash and byte size for every saved artifact. Reviewer commands use the same interpreter and source fixtures. The saved traces retain absolute paths from the original run, so another machine must regenerate them with the listed command.

The report distinguishes the actual fixture assertions and traces from static predictions. It describes confirmed concurrency findings as logical lost updates. It does not describe an unannotated static candidate as a confirmed race. The current cooperative tests check thread, process and asynchronous task execution separately.

An unavailable legacy `tp_del` fixture appears as skipped or unsupported and remains incomplete. Modern Python reference cycles have a separate collectible-cycle fixture. `DEBUG_SAVEALL` changes `gc.garbage` semantics and cannot prove uncollectability.

## Tool impact

`--include-overhead` invokes `tests/acceptance/measure_overhead.py` after the other workloads finish. It measures OS peak RSS and end-to-end runtime on 20,000 and 200,000 records. Each baseline and profiling mode has three serial repetitions. The workload's record count and checksum must match the baseline.

The thresholds in `tests/fixtures/acceptance/overhead-thresholds.json` precede each benchmark run. The runner does not adjust them after a failure. The result includes runtime ratios, RSS limits, actual mode coverage and missing observations. The faster modes lose allocation stacks, allocator observations and precise retention data; these losses must remain visible in the report.

On macOS the benchmark uses `/usr/bin/time -l`. On Linux it uses `/usr/bin/time -v`. If the host or sandbox cannot supply peak RSS, the observation is incomplete. Re-run with the required execution permission; do not report unavailable RSS as zero.

## Current coverage limits

The runner reports the current limits alongside every result. Native allocation hooks remain platform-dependent. General native allocation provenance, individual allocation timestamps, arbitrary owner graphs and unannotated concurrency instrumentation remain incomplete. Explicit factory requests are not allocator volume, Python object sizes are not native bytes, and process RSS is not a Python source-line allocation.

The [cooperative evidence guide](diagnostics.md) describes the annotation API. The [profile format](reference/profile-format.md) defines report fields. The repository feature map remains the source for implemented module relationships.

To include the optional backend in an isolated benchmark:

```bash
python tests/acceptance/measure_overhead.py --include-memray --out .validation/overhead-all-modes
python tests/acceptance/acceptance_suite.py --reuse-rust-build --overhead-results .validation/overhead-all-modes --out .validation/acceptance-final
```

Run other profiling tests after this benchmark stops. `--overhead-results` copies
its artifacts into acceptance evidence. The Memray budget precedes its first
measurement; existing mode budgets remain unchanged. A failed budget is a failed
criterion even when correctness fixtures pass.

The results prioritize a failed overhead budget as a release blocker. Preserve
the required evidence when optimizing; switching to system allocator detail
does not satisfy individual Python allocator coverage. Criterion 3 also includes
the slot-owner cleanup witness rather than counting it only as a suite check.
