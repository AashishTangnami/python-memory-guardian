"""Reproducible native-profiler benchmark; no py-spy/Memray dependency.

Reports process elapsed time (including interpreter and profiler startup), and
checks a growing history against an intentionally bounded working set. Timing
ratios are observations, not pass/fail targets. Run on an otherwise idle machine.
"""
import argparse
import json
from pathlib import Path
import statistics
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
WORK = ROOT / 'test-fixtures' / 'profiler' / 'retention_workload.py'


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--runs', type=int, default=3)
    ap.add_argument('--out', default='.pmg/benchmark.json')
    args = ap.parse_args()
    if args.runs < 1:
        ap.error('--runs must be positive')
    rows = []
    with tempfile.TemporaryDirectory(prefix='pmg-benchmark-') as tmp:
        for bounded in (False, True):
            baseline = None
            for mode in ('baseline', 'off', 'fast', 'precise'):
                times, reports = [], []
                for _ in range(args.runs):
                    out = Path(tmp) / 'profile.json'
                    command = [sys.executable]
                    if mode != 'baseline':
                        command += [str(ROOT / 'server' / 'pmg_profile.py'), '--memory', mode,
                                    '--root', str(WORK.parent), '--out', str(out)]
                    command += [str(WORK)] + (['--bounded'] if bounded else [])
                    started = time.perf_counter()
                    subprocess.run(command, capture_output=True, text=True, check=True, timeout=60)
                    times.append(time.perf_counter() - started)
                    if mode != 'baseline':
                        reports.append(json.loads(out.read_text()))
                elapsed = statistics.median(times)
                if mode == 'baseline':
                    baseline = elapsed
                flags = [sum(bool(e.get('leak_runs')) for entries in p['files'].values()
                             for e in entries.values()) for p in reports]
                holders = [any(e.get('held_by') for entries in p['files'].values()
                               for e in entries.values() if e.get('leak_runs')) for p in reports]
                passed = mode != 'precise' or (all(n == 0 for n in flags) if bounded
                                              else all(n > 0 for n in flags) and all(holders))
                row = {'scenario': 'bounded' if bounded else 'growing', 'mode': mode,
                       'median_process_s': round(elapsed, 4), 'relative_to_baseline': round(elapsed / baseline, 3),
                       'suspected_lines': flags, 'named_holder': holders, 'expectation_passed': passed}
                rows.append(row)
                print(json.dumps(row), flush=True)
    destination = Path(args.out)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps({'runs': args.runs, 'python': sys.version, 'results': rows}, indent=2))
    return 0 if all(r['expectation_passed'] for r in rows) else 1


if __name__ == '__main__':
    sys.exit(main())
