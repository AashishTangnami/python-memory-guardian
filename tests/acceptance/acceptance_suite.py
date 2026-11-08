"""Persist witness-first acceptance evidence; unsupported cases never pass.

Run from the repository root. Exit 0 means all nine full criteria passed; 1
means a measured check failed; 2 means coverage or a required check is incomplete.
"""
import argparse
import datetime
import hashlib
import importlib.util
import inspect
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
MODULE_DIRS = {'runtime_acceptance_test.py': 'profiler', 'diagnostics_test.py': 'profiler',
               'static_regression_test.py': 'static', 'allocation_backend_test.py': 'profiler',
               'memory_guard_test.py': 'guard'}
MODULES = tuple(MODULE_DIRS)
PARITY = ROOT / 'tests' / 'static' / 'parity_test.py'


def module_path(module_name):
    return ROOT / 'tests' / MODULE_DIRS[module_name] / module_name

LABELS = {
    1: 'Execution and allocation trace', 2: 'Out-of-memory risk',
    3: 'Retained objects and reference cycles', 4: 'Garbage collection behavior',
    5: 'Concurrency hazards', 6: 'Data structure costs',
    7: 'Evidence and reproducibility', 8: 'Validation fixtures', 9: 'Tool impact',
}
# These are coverage limits, not excuses to promote a partial result to pass.
LIMITS = {
    1: 'Native allocator hooks are not complete across platforms. Allocation events lack individual timestamps and arbitrary native allocation paths.',
    2: 'Growth and explicit copies are measured on declared workloads. Automatic proof of unbounded growth and general OOM prediction remain unavailable.',
    3: 'Owner paths and cycles cover bounded explicit roots including native slots; arbitrary native and custom-container reference graphs remain unavailable.',
    4: '',
    5: 'Confirmed logical lost updates require cooperative access and outcome annotations. Unannotated paths and native data races are unavailable.',
    6: 'Costs are measured for declared structures. Automatic hot-path structure discovery and generally safe alternative selection remain incomplete.',
    7: 'The suite preserves commands, source, profile and trace artifacts. Some static findings and allocation records lack the full per-finding evidence contract.',
    8: '',
    9: 'Run --include-overhead to measure preregistered budgets on this host. Coverage lost in faster modes is part of the benchmark result.',
}
SELECTORS = {
    1: ('worker_growth', 'lost_tracing', 'requested_allocation', 'trace_evidence', 'cli_diagnostics', 'allocation_backend_test'),
    2: ('growth_and_budget', 'plateau', 'repeated_copy', 'cursor_streaming', 'fetchone_streaming', 'memory_guard_test'),
    3: ('retained_reference', 'collectible_cycle', 'finalizer_frames', 'nested_owner', 'slot_owner_cleanup', 'uncollectable'),
    4: ('gc_records', 'gc_pressure', 'uncollectable'),
    5: ('threads_red_green', 'processes_red_green', 'async_tasks_red_green', 'potential_without', 'separate_read_and_write', 'late_worker_main'),
    6: ('structure_cost', 'static_regression_test'),
    7: ('trace_evidence', 'cli_diagnostics', 'lost_tracing', 'allocation_backend_test'),
    8: ('runtime_acceptance_test', 'diagnostics_test', 'static_regression_test', 'allocation_backend_test', 'memory_guard_test', 'parity'),
}


def dump(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2) + '\n', encoding='utf-8')


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_process(out, name, command, timeout=240, env=None):
    start = time.perf_counter()
    try:
        result = subprocess.run(command, cwd=ROOT, env=env, capture_output=True,
                                text=True, timeout=timeout)
        row = {'command': command, 'cwd': str(ROOT), 'returncode': result.returncode,
               'duration_s': time.perf_counter() - start,
               'stdout': result.stdout, 'stderr': result.stderr}
    except (OSError, subprocess.TimeoutExpired) as error:
        row = {'command': command, 'cwd': str(ROOT), 'returncode': None,
               'duration_s': time.perf_counter() - start, 'stdout': '',
               'stderr': str(error), 'unavailable': True}
    (out / (name + '.stdout.txt')).write_text(row['stdout'], encoding='utf-8')
    (out / (name + '.stderr.txt')).write_text(row['stderr'], encoding='utf-8')
    dump(out / (name + '.command.json'), row)
    return row


def worker(module_name, out):
    """Run one real unittest suite and preserve its otherwise temporary traces."""
    out.mkdir(parents=True, exist_ok=True)
    fixtures = out / 'fixtures'
    fixtures.mkdir(exist_ok=True)
    current = {'test': None}
    executions = []
    observations = []
    original_run = subprocess.run

    class SavedTemporaryDirectory:
        def __init__(self, suffix=None, prefix=None, dir=None, **kwargs):
            self.name = tempfile.mkdtemp(suffix=suffix or '', prefix=prefix or 'fixture-', dir=fixtures)

        def __enter__(self):
            return self.name

        def __exit__(self, *args):
            return False

        def cleanup(self):
            pass  # Acceptance artifacts are deliberately permanent.

    tempfile.TemporaryDirectory = SavedTemporaryDirectory

    def saved_run(*args, **kwargs):
        command = args[0] if args else kwargs.get('args')
        started = time.perf_counter()
        try:
            result = original_run(*args, **kwargs)
        except BaseException as error:
            executions.append({'case': current['test'], 'command': command,
                               'duration_s': time.perf_counter() - started,
                               'error': str(error)})
            raise
        row = {'case': current['test'], 'command': command,
               'duration_s': time.perf_counter() - started,
               'returncode': result.returncode,
               'stdout': result.stdout.decode(errors='replace') if isinstance(result.stdout, bytes) else result.stdout,
               'stderr': result.stderr.decode(errors='replace') if isinstance(result.stderr, bytes) else result.stderr}
        executions.append(row)
        return result

    subprocess.run = saved_run
    sys.path.insert(0, str(module_path(module_name).parent))
    sys.path.insert(0, str(ROOT / 'src' / 'python'))
    import pmg_diagnostics
    original_install, original_uninstall = pmg_diagnostics.install, pmg_diagnostics.uninstall
    observation_counter = [0]

    def saved_install(trace_path=None, workload=None, max_events=20000):
        observation_counter[0] += 1
        if trace_path is None:
            trace_path = str(out / ('observation-' + str(observation_counter[0]) + '.events.jsonl'))
        return original_install(trace_path, workload, max_events)

    def saved_uninstall():
        result = original_uninstall()
        if result is not None:
            path = out / ('observation-' + str(len(observations) + 1) + '.diagnostics.json')
            dump(path, result)
            observations.append({'case': current['test'], 'path': str(path),
                                 'trace_path': result['trace_path'],
                                 'findings': len(result['findings'])})
        return result

    pmg_diagnostics.install, pmg_diagnostics.uninstall = saved_install, saved_uninstall
    spec = importlib.util.spec_from_file_location(Path(module_name).stem, module_path(module_name))
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    copied_source = out / module_name
    shutil.copyfile(module_path(module_name), copied_source)
    cases = []
    rows = {}

    class Results(unittest.TextTestResult):
        def startTest(self, test):
            super().startTest(test)
            current['test'] = test.id()
            method = getattr(test, test._testMethodName)
            try:
                source_line = inspect.getsourcelines(method)[1]
            except (TypeError, OSError):
                source_line = None
            row = {'id': test.id(), 'status': 'running',
                   'fixture': str(module_path(module_name)), 'source_line': source_line,
                   'source_sha256': digest(module_path(module_name)),
                   'command': [sys.executable, str(module_path(module_name))],
                   'started': time.perf_counter(), 'details': []}
            rows[test.id()] = row
            cases.append(row)

        def addSuccess(self, test):
            super().addSuccess(test)
            rows[test.id()]['status'] = 'passed'

        def addFailure(self, test, error):
            super().addFailure(test, error)
            rows[test.id()]['status'] = 'failed'
            rows[test.id()]['details'].append(self._exc_info_to_string(error, test))

        def addError(self, test, error):
            super().addError(test, error)
            # An import/dependency observation is unavailable, never a pass.
            dependency = isinstance(error[1], (ImportError, ModuleNotFoundError))
            if isinstance(error[1], RuntimeError) and 'Build the Rust backend' in str(error[1]):
                dependency = True
            if test.id() not in rows:
                row = {'id': test.id(), 'status': 'unsupported' if dependency else 'failed',
                       'fixture': str(module_path(module_name)), 'source_line': None,
                       'command': [sys.executable, str(module_path(module_name))],
                       'details': [self._exc_info_to_string(error, test)]}
                cases.append(row)
            else:
                rows[test.id()]['status'] = 'unsupported' if dependency else 'failed'
                rows[test.id()]['details'].append(self._exc_info_to_string(error, test))

        def addSkip(self, test, reason):
            super().addSkip(test, reason)
            unavailable = any(word in reason.lower() for word in ('unsupported', 'unavailable', 'not installed'))
            rows[test.id()]['status'] = 'unsupported' if unavailable else 'skipped'
            rows[test.id()]['details'].append(reason)

        def addSubTest(self, test, subtest, error):
            super().addSubTest(test, subtest, error)
            if error:
                rows[test.id()]['status'] = 'failed'
                rows[test.id()]['details'].append(self._exc_info_to_string(error, subtest))

        def stopTest(self, test):
            rows[test.id()]['duration_s'] = time.perf_counter() - rows[test.id()].pop('started')
            current['test'] = None
            super().stopTest(test)

    suite = unittest.defaultTestLoader.loadTestsFromModule(module)
    runner = unittest.TextTestRunner(verbosity=2, resultclass=Results)
    result = runner.run(suite)
    pmg_diagnostics.uninstall()
    for case in cases:
        case['executions'] = [i for i, row in enumerate(executions) if row['case'] == case['id']]
        case['observations'] = [row for row in observations if row['case'] == case['id']]
    if hasattr(module, 'RECORDS'):
        dump(out / 'static-witness-records.json', module.RECORDS)
    dump(out / 'cases.json', {'module': module_name, 'cases': cases,
                              'tests_run': result.testsRun, 'executions': executions,
                              'diagnostic_observations': observations})
    return 1 if any(c['status'] == 'failed' for c in cases) else 2 if any(c['status'] != 'passed' for c in cases) else 0


def rust_fresh():
    binary = ROOT / 'src' / 'rust' / 'target' / 'release' / ('guardian-server.exe' if os.name == 'nt' else 'guardian-server')
    sources = list((ROOT / 'src' / 'rust' / 'src').rglob('*.rs'))
    sources += [ROOT / 'src' / 'rust' / 'Cargo.toml', ROOT / 'src' / 'rust' / 'Cargo.lock', ROOT / 'src' / 'python' / 'messages.json']
    present = [p for p in sources if p.exists()]
    return binary.is_file() and all(binary.stat().st_mtime_ns >= p.stat().st_mtime_ns for p in present)


def status_from_cases(cases, number):
    if not cases:
        return 'unsupported'
    if any(c['status'] == 'failed' for c in cases):
        return 'fail'
    if any(c['status'] != 'passed' for c in cases):
        return 'partial' if any(c['status'] == 'passed' for c in cases) else 'unsupported'
    return 'partial' if LIMITS[number] else 'pass'


def report_markdown(report):
    counts = report['case_counts']
    lines = ['# Acceptance validation', '',
             f"Recorded: {report['recorded_at']}. Python: `{report['python'].splitlines()[0]}`.", '',
             f"Cases: {counts['passed']} passed, {counts['failed']} failed, {counts['skipped']} skipped, {counts['unsupported']} unsupported.", '',
             'A passed case verifies its witness and regression check. It does not promote missing coverage to a passing full acceptance criterion.', '',
             '| Criterion | Result | Observed evidence | Remaining limitation |',
             '|---|---|---|---|']
    for item in report['criteria']:
        observation = item['observed']
        if item['number'] == 9 and item.get('artifact'):
            observation += ' [benchmark](' + item['artifact'] + ')'
        else:
            observation += ' [case details](results.json)'
        lines.append(f"| {item['number']}. {item['criterion']} | **{item['status']}** | {observation} | {item['limitation']} |")
    lines += ['', '## Case commands and source evidence', '',
              '| Case | Result | Fixture and command | Observed output and artifacts |',
              '|---|---|---|---|']
    for case in report['cases']:
        command = ' '.join(case.get('command', []))
        fixture = case.get('fixture', '')
        if case.get('source_line'):
            fixture += ':' + str(case['source_line'])
        artifacts = ', '.join(case.get('artifacts', [])) or 'No artifact available.'
        lines.append(f"| `{case['id']}` | {case['status']} | `{fixture}`<br>`{command}` | {artifacts} |")
    lines += ['', '## False positives and false negatives', '', report['finding_accuracy'], '',
              'The report separates measured fixture results from coverage predictions. Unsupported cases remain incomplete.', '',
              '## Prioritized implementation gaps', '']
    for gap in report['implementation_gaps']:
        lines.append('- ' + gap)
    return '\n'.join(lines) + '\n'


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, default=ROOT / '.validation' / 'acceptance-current')
    parser.add_argument('--include-overhead', action='store_true')
    parser.add_argument('--overhead-results', type=Path, help='reuse an isolated measured benchmark directory')
    parser.add_argument('--reuse-rust-build', action='store_true', help='check source timestamps instead of running cargo build')
    parser.add_argument('--worker', choices=MODULES, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=True)
    if args.worker:
        try:
            return worker(args.worker, out)
        except ImportError as error:
            dump(out / 'cases.json', {'module': args.worker, 'cases': [{'id': args.worker + ':dependency',
                 'status': 'unsupported', 'fixture': str(module_path(args.worker)),
                 'command': [sys.executable, str(module_path(args.worker))], 'details': [str(error)]}], 'executions': []})
            return 2
    cases = []
    prerequisites = []
    rust_available = rust_fresh()
    if not args.reuse_rust_build:
        build = write_process(out, 'rust-build', ['cargo', 'build', '--release', '--manifest-path', str(ROOT / 'src' / 'rust' / 'Cargo.toml')])
        rust_available = build['returncode'] == 0 and rust_fresh()
        prerequisites.append({'id': 'rust-build', 'status': 'passed' if rust_available else 'unsupported' if build.get('unavailable') else 'failed',
                              'command': build['command'], 'observed': build['stderr']})
    elif not rust_available:
        prerequisites.append({'id': 'rust-build', 'status': 'unsupported',
                              'command': ['cargo', 'build', '--release', '--manifest-path', str(ROOT / 'src' / 'rust' / 'Cargo.toml')],
                              'observed': 'Fresh Rust binary unavailable; stale code is not validated.'})
    for module in MODULES:
        module_out = out / Path(module).stem
        module_out.mkdir(exist_ok=True)
        if not (module_path(module)).is_file() or (module == 'static_regression_test.py' and not rust_available):
            cases.append({'id': module + ':unavailable', 'status': 'unsupported', 'fixture': str(module_path(module)),
                          'command': [sys.executable, str(module_path(module))],
                          'details': ['Fixture module or fresh Rust backend unavailable.'], 'artifacts': []})
            continue
        command = [sys.executable, str(Path(__file__).resolve()), '--worker', module, '--out', str(module_out)]
        execution = write_process(module_out, 'suite', command)
        case_file = module_out / 'cases.json'
        if case_file.exists():
            module_report = json.loads(case_file.read_text())
            for case in module_report['cases']:
                case['module'] = module
                case['artifacts'] = [str(case_file.relative_to(out)), str((module_out / 'suite.stderr.txt').relative_to(out))]
                cases.append(case)
        else:
            cases.append({'id': module + ':runner', 'status': 'unsupported' if execution.get('unavailable') else 'failed',
                          'fixture': str(module_path(module)), 'command': command, 'details': [execution['stderr']],
                          'artifacts': [str((module_out / 'suite.stderr.txt').relative_to(out))]})
    if rust_available and (ROOT / 'src' / 'python' / 'libs').is_dir():
        env = dict(os.environ, PMG_REQUIRE_RUST='1')
        parity = write_process(out, 'parity', [sys.executable, str(PARITY)], env=env)
        for line in parity['stdout'].splitlines():
            if line.startswith(('PASS ', 'FAIL ', 'SKIP ')):
                cases.append({'id': 'parity:' + line[5:].strip(), 'status': 'passed' if line.startswith('PASS ') else 'failed' if line.startswith('FAIL ') else 'skipped',
                              'fixture': str(PARITY), 'command': parity['command'],
                              'observed': line, 'artifacts': ['parity.stdout.txt', 'parity.command.json']})
        if parity['returncode'] != 0:
            cases.append({'id': 'parity:process', 'status': 'failed', 'fixture': str(PARITY),
                          'command': parity['command'], 'details': [parity['stderr']], 'artifacts': ['parity.stderr.txt']})
    else:
        cases.append({'id': 'parity:unavailable', 'status': 'unsupported', 'fixture': str(PARITY),
                      'command': [sys.executable, str(PARITY)],
                      'details': ['Fresh Rust binary or vendored Python server dependencies unavailable.'], 'artifacts': []})
    criteria = []
    for number in range(1, 9):
        selected = [case for case in cases if any(selector in case['id'] for selector in SELECTORS[number])]
        passed = sum(case['status'] == 'passed' for case in selected)
        failed = sum(case['status'] == 'failed' for case in selected)
        incomplete = sum(case['status'] in ('skipped', 'unsupported') for case in selected)
        criteria.append({'number': number, 'criterion': LABELS[number], 'status': status_from_cases(selected, number),
                         'case_ids': [case['id'] for case in selected],
                         'observed': f'{passed} passed checks; {failed} failed; {incomplete} incomplete.',
                         'limitation': LIMITS[number]})
    overhead = None
    if args.include_overhead or args.overhead_results:
        overhead_out = out / 'overhead'
        overhead_out.mkdir(exist_ok=True)
        if args.overhead_results:
            shutil.copytree(args.overhead_results.resolve(), overhead_out, dirs_exist_ok=True)
            execution = {'stderr': 'Isolated benchmark results unavailable.'}
        else:
            execution = write_process(overhead_out, 'measurement', [sys.executable, str(HERE / 'measure_overhead.py'), '--include-memray', '--out', str(overhead_out)], timeout=1200)
        if (overhead_out / 'results.json').is_file():
            overhead = json.loads((overhead_out / 'results.json').read_text())
            measured = [row for row in overhead.get('results', []) if row.get('mode') != 'baseline']
            complete = measured and all(row['status'] in ('pass', 'fail') for row in measured)
            status = 'fail' if any(row['status'] == 'fail' for row in measured) else 'pass' if complete else 'unsupported'
            observed = '; '.join(f"{row['size']} {row['mode']}: {row['status']}, wall {row.get('process_wall_ratio', 0):.2f}x, RSS {row.get('median_peak_rss_mib')} MiB" for row in measured)
        else:
            status, observed = 'unsupported' if execution.get('unavailable') else 'fail', execution['stderr']
        criteria.append({'number': 9, 'criterion': LABELS[9], 'status': status,
                         'case_ids': [], 'observed': observed, 'limitation': 'Measurements apply to this host and the saved workload only.',
                         'artifact': 'overhead/results.json'})
    else:
        criteria.append({'number': 9, 'criterion': LABELS[9], 'status': 'unsupported', 'case_ids': [],
                         'observed': 'Overhead measurement not requested; incomplete.', 'limitation': LIMITS[9]})
    counts = {status: sum(case['status'] == status for case in cases) for status in ('passed', 'failed', 'skipped', 'unsupported')}
    python_source = ROOT / 'src' / 'python'
    source_paths = [python_source / 'pmg_profile.py', python_source / 'pmg_diagnostics.py', python_source / 'messages.json',
                    *sorted((python_source / 'pmg_profiler').glob('*.py')), *sorted((python_source / 'rules').glob('*.py')),
                    *sorted((ROOT / 'src' / 'rust' / 'src').glob('*.rs'))]
    report = {'recorded_at': datetime.datetime.now(datetime.timezone.utc).isoformat(),
              'python': sys.version, 'executable': sys.executable, 'cwd': str(ROOT),
              'command': [sys.executable, str(Path(__file__).resolve()), *(sys.argv[1:] if argv is None else argv)],
              'criteria': criteria, 'case_counts': counts, 'cases': cases, 'prerequisites': prerequisites,
              'measured_facts': 'Actual executed assertions, commands, outputs and saved traces in case artifacts.',
              'static_predictions': 'Static warnings remain code-pattern candidates; fixture witnesses do not convert unrelated static warnings into runtime measurements.',
              'finding_accuracy': ('No false positive or false negative was detected in the executed positive/fixed fixture pairs. Coverage outside these pairs is not established.'
                                   if not counts['failed'] else 'One or more fixture checks failed. Their saved assertion traces need review to classify a false positive, false negative or witness failure.'),
              'implementation_gaps': ['P0: meet preregistered runtime and RSS budgets without suppressing required evidence; full allocator capture and precise tracing remain costly.',
                                      'P0: complete Python and native allocation event timestamps, thread provenance and platform-specific native hooks.',
                                      'P1: discover arbitrary owner graphs and cleanup lifetimes without a sampler observer effect.',
                                      'P1: instrument unannotated shared accesses and synchronization in threads, processes and asynchronous tasks.',
                                      'P2: infer workload growth and structure alternatives with explicit semantic requirements.'],
              'source_evidence': [{'path': str(path), 'sha256': digest(path)} for path in source_paths if path.is_file()]}
    dump(out / 'results.json', report)
    (out / 'report.md').write_text(report_markdown(report), encoding='utf-8')
    manifest = [{'path': str(path.relative_to(out)), 'bytes': path.stat().st_size, 'sha256': digest(path)}
                for path in sorted(out.rglob('*')) if path.is_file() and path.name != 'manifest.json']
    dump(out / 'manifest.json', {'artifacts': manifest})
    for item in criteria:
        print(f"{item['number']}. {item['criterion']}: {item['status']} ({item['observed']})")
    print(json.dumps({'case_counts': counts, 'report': str(out / 'report.md')}))
    if counts['failed'] or any(item['status'] == 'fail' for item in criteria) or any(p['status'] == 'failed' for p in prerequisites):
        return 1
    return 2 if any(item['status'] != 'pass' for item in criteria) else 0


if __name__ == '__main__':
    sys.exit(main())
