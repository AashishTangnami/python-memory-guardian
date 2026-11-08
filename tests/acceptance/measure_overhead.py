"""Run serially, after the other audit workloads have stopped. macOS time -l RSS."""
import datetime
import argparse
import json
from pathlib import Path
import platform
import random
import re
import statistics
import subprocess
import sys
import time

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
FIXTURES = ROOT / 'tests' / 'fixtures' / 'acceptance'
OUT = ROOT / '.validation' / 'overhead-current'
THRESHOLDS = {
    "off": {"wall_ratio_max": 1.25, "rss_extra_floor_mib": 20, "rss_extra_baseline_ratio": .25},
    "fast": {"wall_ratio_max": 1.25, "rss_extra_floor_mib": 20, "rss_extra_baseline_ratio": .25},
    "precise": {"wall_ratio_max": 10, "rss_extra_floor_mib": 128, "rss_extra_baseline_ratio": 2},
    "memray": {"wall_ratio_max": 10, "rss_extra_floor_mib": 128, "rss_extra_baseline_ratio": 2},
    "memray_system": {"wall_ratio_max": 10, "rss_extra_floor_mib": 128, "rss_extra_baseline_ratio": 2},
}


def main():
    global OUT
    parser = argparse.ArgumentParser()
    parser.add_argument('--out', type=Path, default=OUT)
    parser.add_argument('--thresholds', type=Path, default=FIXTURES / 'overhead-thresholds.json')
    parser.add_argument('--include-memray', action='store_true', help='measure optional allocator capture in fast mode')
    parser.add_argument('--memray-detail', choices=('full', 'system', 'both'), default='both')
    args = parser.parse_args()
    OUT = args.out.resolve()
    OUT.mkdir(parents=True, exist_ok=True)
    preregistered = json.loads(args.thresholds.read_text())
    assert preregistered["thresholds"] == THRESHOLDS
    # Save the budget before the first child process. Do not tune it after a failure.
    (OUT / 'thresholds.json').write_text(json.dumps(preregistered, indent=2))
    if platform.system() not in ('Darwin', 'Linux'):
        (OUT / 'results.json').write_text(json.dumps({'status': 'unsupported',
            'reason': 'OS time RSS parser is available only on macOS and Linux',
            'results': [], 'thresholds': preregistered}))
        return 2
    # Linux needs GNU time (`time` package) for -v; a missing tool is an incomplete measurement, not a crash.
    if not Path('/usr/bin/time').is_file():
        (OUT / 'results.json').write_text(json.dumps({'status': 'unsupported',
            'reason': '/usr/bin/time is unavailable; install GNU time on Linux to record peak RSS',
            'results': [], 'thresholds': preregistered}))
        print('/usr/bin/time is unavailable; overhead not measured', file=sys.stderr)
        return 2
    runs = []
    for size in (20000, 200000):
        for repeat in range(3):
            modes = ["off", "fast", "precise"]
            if args.include_memray:
                modes += (['memray', 'memray_system'] if args.memray_detail == 'both' else
                          ['memray_system'] if args.memray_detail == 'system' else ['memray'])
            random.Random(20261007 + repeat).shuffle(modes)
            for mode in ["baseline", *modes]:
                name = f"{size}-{repeat}-{mode}"
                cmd = [sys.executable]
                profile = OUT / f"{name}.profile.json"
                if mode != "baseline":
                    cmd += [str(ROOT / "src/python/pmg_profile.py"), "--memory", 'fast' if mode.startswith('memray') else mode,
                            "--root", str(FIXTURES), "--frames", "2", "--out", str(profile)]
                    if mode.startswith('memray'):
                        cmd += ['--allocation-backend', 'memray']
                        if mode == 'memray_system':
                            cmd += ['--allocation-detail', 'system']
                cmd += [str(FIXTURES / "benchmark_workload.py"), "--size", str(size)]
                timed = (["/usr/bin/time", "-l"] if platform.system() == 'Darwin'
                         else ["/usr/bin/time", "-v"]) + cmd
                start = time.perf_counter()
                try:
                    proc = subprocess.run(timed, cwd=ROOT, capture_output=True, text=True, timeout=300 if mode.startswith('memray') else 180)
                except subprocess.TimeoutExpired as error:
                    decode = lambda value: value.decode(errors='replace') if isinstance(value, bytes) else value or ''
                    proc = subprocess.CompletedProcess(timed, 124, decode(error.stdout),
                        decode(error.stderr) + '\nBenchmark process exceeded its timeout.\n')
                wall = time.perf_counter() - start
                (OUT / f"{name}.stdout.txt").write_text(proc.stdout)
                (OUT / f"{name}.stderr.txt").write_text(proc.stderr)
                m = (re.search(r"(\d+)\s+maximum resident set size", proc.stderr)
                     if platform.system() == 'Darwin' else
                     re.search(r"Maximum resident set size \(kbytes\):\s*(\d+)", proc.stderr))
                row = {"size": size, "repeat": repeat, "mode": mode, "command": timed,
                       "cwd": str(ROOT), "exit_code": proc.returncode, "process_wall_s": wall,
                       "peak_rss_bytes": int(m.group(1)) * (1 if platform.system() == 'Darwin' else 1024) if m else None,
                       "profile": str(profile) if mode != "baseline" else None}
                try:
                    row["output"] = json.loads(proc.stdout.strip().splitlines()[-1])
                except (ValueError, IndexError):
                    row["output"] = None
                runs.append(row)
                (OUT / "runs.json").write_text(json.dumps(runs, indent=2) + "\n")
                print(json.dumps({k: row[k] for k in ("size", "repeat", "mode", "exit_code", "process_wall_s", "peak_rss_bytes")}), flush=True)
    results = []
    for size in (20000, 200000):
        base = [r for r in runs if r["size"] == size and r["mode"] == "baseline"]
        complete_base = all(r["exit_code"] == 0 and r["output"] and r["peak_rss_bytes"] is not None for r in base)
        base_wall = statistics.median(r["process_wall_s"] for r in base)
        base_rss = statistics.median(r["peak_rss_bytes"] for r in base) if complete_base else None
        for mode in dict.fromkeys(row['mode'] for row in runs):
            selected = [r for r in runs if r["size"] == size and r["mode"] == mode]
            complete = complete_base and all(r["exit_code"] == 0 and r["output"] and r["peak_rss_bytes"] is not None for r in selected)
            identical = complete and all(r["output"]["records"] == size and r["output"]["checksum"] == base[0]["output"]["checksum"] for r in selected)
            wall = statistics.median(r["process_wall_s"] for r in selected)
            rss = statistics.median(r["peak_rss_bytes"] for r in selected) if complete else None
            threshold = THRESHOLDS.get(mode)
            rss_budget = base_rss + max(threshold["rss_extra_floor_mib"] * 2**20, threshold["rss_extra_baseline_ratio"] * base_rss) if threshold and complete else None
            wall_pass = complete and (not threshold or wall / base_wall <= threshold["wall_ratio_max"])
            rss_pass = complete and (not threshold or rss <= rss_budget)
            coverage = None
            if mode != "baseline" and complete:
                p = json.loads((ROOT / selected[0]["profile"]).read_text())
                coverage = {"memory_stacks_available": p.get("memory_stacks") is not None,
                            "allocation_events_available": p.get('allocation_backend', {}).get('available', False),
                            "python_allocator_requests_available": p.get('allocation_backend', {}).get('availability', {}).get('python_allocator_requests') == 'measured',
                            "native_allocator_available": p.get('allocation_backend', {}).get('backend') == 'memray' and p.get('allocation_backend', {}).get('available', False),
                            "gc_metrics_available": p.get('gc', {}).get('supported', False),
                            "concurrency_access_available": p.get('diagnostics') is not None,
                            "retention_lines": sum("retention" in e for ls in p["files"].values() for e in ls.values()),
                            "leak_lines": sum(bool(e.get("leak_runs")) for ls in p["files"].values() for e in ls.values()),
                            "timeline_points": len(p.get("timeline", [])), "samples": p.get("samples"),
                            "sampler_error": p.get("sampler_error"), "memory_tracing_lost_s": p.get("memory_tracing_lost_s")}
            results.append({"size": size, "mode": mode, "median_process_s": wall,
                            "median_workload_s": statistics.median(r["output"]["workload_wall_s"] for r in selected) if complete else None,
                            "process_wall_ratio": wall / base_wall, "median_peak_rss_mib": rss / 2**20 if complete else None,
                            "peak_rss_ratio": rss / base_rss if complete else None,
                            "peak_rss_budget_mib": rss_budget / 2**20 if rss_budget is not None else None,
                            "identical_result": identical, "runtime_pass": wall_pass, "rss_pass": rss_pass,
                            "status": "pass" if identical and wall_pass and rss_pass else "fail" if complete or any(r['exit_code'] != 0 for r in selected + base) else "incomplete",
                            "coverage": coverage})
    report = {"recorded_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
              "python": sys.version, "executable": sys.executable, "platform": platform.platform(),
              "repetitions": 3, "thresholds": THRESHOLDS, "results": results, "runs": runs,
              "limitations": ["End-to-end wall includes Python/profiler startup and report writing.",
                              "RSS is OS-reported process high-water mark, in bytes on macOS; MiB uses 2**20.",
                              "Agent workloads were stopped; unrelated host activity was not controlled.",
                              "Three repetitions are descriptive and do not establish a population confidence interval."]}
    (OUT / "results.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({"results": results}), flush=True)
    return 1 if any(r["status"] != "pass" for r in results) else 0


if __name__ == "__main__":
    sys.exit(main())
