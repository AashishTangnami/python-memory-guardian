"""Budget-policy and independent positive/fixed execution witnesses."""
import json
import os
from pathlib import Path
import subprocess
import sys
import sysconfig
import tempfile
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src' / 'python'))
from pmg_profiler.guard import choose_budget, linux_cgroup_available, system_memory

PROFILER = ROOT / 'src' / 'python' / 'pmg_profile.py'
FIXTURE = ROOT / 'tests' / 'fixtures' / 'acceptance' / 'guard_workload.py'
MIB = 1 << 20
# Free-threaded builds allocate with mimalloc, which keeps about 50 MiB of freed pages resident. The
# cache stays flat as work grows (70.8 MiB after 64 MiB or 1.5 GiB processed on 3.13.3t), so a
# corrected run is still bounded, only above the 64 MiB the default allocator reaches. Both ceilings
# stay below the 256 MiB budget, so the red and green runs remain separated.
FREE_THREADED = bool(sysconfig.get_config_var('Py_GIL_DISABLED'))
FIXED_PEAK = (96 if FREE_THREADED else 64) * MIB         # one corrected process
FIXED_FAMILY_PEAK = (192 if FREE_THREADED else 64) * MIB  # two corrected workers together


class BudgetPolicy(unittest.TestCase):
    def test_budget_uses_available_memory_and_reserve(self):
        small = {'available_bytes': 1024 * MIB}
        large = {'available_bytes': 4096 * MIB}
        self.assertEqual(choose_budget(small), (256 * MIB, 512 * MIB))
        self.assertEqual(choose_budget(large), (1024 * MIB, 512 * MIB))
        self.assertEqual(choose_budget(large, 128)[0], 128 * MIB)
        self.assertEqual(choose_budget(small, 900)[0], 256 * MIB)
        self.assertEqual(choose_budget(small, 1e308, 1e308), (256 * MIB, 512 * MIB))
        with self.assertRaises(OSError):
            choose_budget({'available_bytes': 128 * MIB})

    def test_cgroup_uses_smallest_visible_ancestor_headroom(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            group = root / 'parent' / 'child'
            group.mkdir(parents=True)
            membership = root / 'membership'
            membership.write_text('0::/parent/child\n')
            for directory, maximum, current in [(root, 'max', 0),
                    (group.parent, str(512 * MIB), 400 * MIB), (group, str(256 * MIB), 32 * MIB)]:
                (directory / 'memory.max').write_text(maximum)
                (directory / 'memory.current').write_text(str(current))
            self.assertEqual(linux_cgroup_available(root, membership), 112 * MIB)
            (group.parent / 'memory.max').write_text('max')
            self.assertEqual(linux_cgroup_available(root, membership), 224 * MIB)
            membership.write_text('1:memory:/parent/child\n')
            with self.assertRaisesRegex(OSError, 'v1'):
                linux_cgroup_available(root, membership)

    def test_observation_failure_never_starts_target(self):
        from argparse import Namespace
        from pmg_profiler.guard import supervise
        with patch('pmg_profiler.guard.system_memory', side_effect=OSError('unavailable')), patch('pmg_profiler.guard.subprocess.Popen') as launch:
            with self.assertRaises(OSError):
                supervise(Namespace(), [], 'profiler', 'target', 'out')
            launch.assert_not_called()

    @unittest.skipUnless(os.name == 'posix', 'process-group cleanup witness requires POSIX; incomplete')
    def test_simulated_reserve_and_reader_loss_stop_real_child(self):
        from argparse import Namespace
        from pmg_profiler.guard import supervise
        initial = {'available_bytes': 1024 * MIB, 'total_bytes': 2048 * MIB}
        for observation, expected in [({'available_bytes': 400 * MIB}, 'system_reserve'),
                                      (OSError('reader lost'), 'observation_unavailable: reader lost')]:
            with self.subTest(reason=expected), tempfile.TemporaryDirectory() as directory:
                target = Path(directory) / 'wait.py'
                target.write_text('import time\ntime.sleep(30)\n')
                output = str(Path(directory) / 'profile.json')
                ns = Namespace(memory_limit_mb=256, memory_reserve_mb=512,
                               memory='fast', allocation_backend='off', script_args=[str(target)])
                # Controlled counters exercise policy; they do not simulate an actual host OOM.
                with patch('pmg_profiler.guard.system_memory', side_effect=[initial, observation]), \
                        patch('pmg_profiler.guard.process_memory', return_value=(16 * MIB, 16 * MIB)):
                    code = supervise(ns, [str(target)], str(PROFILER), str(target), output)
                self.assertEqual(code, 3)
                guard = json.loads(Path(output).read_text())['memory_guard']
                self.assertEqual(guard['stop_reason'], expected)
                self.assertEqual(guard['status'], 'stopped')
                with self.assertRaises(ProcessLookupError):
                    os.kill(guard['profiled_pid'], 0)


@unittest.skipUnless(sys.platform.startswith('linux') or sys.platform in ('darwin', 'win32'),
                     'independent peak-RSS fixture is unavailable on this host; incomplete')
class GuardExecutions(unittest.TestCase):
    def run_case(self, directory, arguments, guard=True, fixture=FIXTURE):
        output = Path(directory) / ('guarded.json' if guard else 'off.json')
        command = [sys.executable, str(PROFILER), '--memory', 'fast', '--memory-guard', 'auto' if guard else 'off',
                   '--memory-limit-mb', '256', '--out', str(output), str(fixture), *arguments]
        result = subprocess.run(command, capture_output=True, text=True, timeout=30)
        return result, json.loads(output.read_text()), command

    def test_growth_red_green_and_fixed_cleanup(self):
        if system_memory()['available_bytes'] < 1024 * MIB:
            self.skipTest('less than 1 GiB available; bounded 384 MiB pressure fixture is incomplete')
        # Independent executions establish growth and equivalent corrected work.
        witnesses = []
        for arguments in (['--blocks', '32'], ['--blocks', '96'], ['--blocks', '96', '--fixed']):
            result = subprocess.run([sys.executable, str(FIXTURE), *arguments], capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stderr)
            witnesses.append(json.loads(result.stdout))
        small, bad, fixed = witnesses
        self.assertEqual(bad['retained_bytes'], 3 * small['retained_bytes'])
        self.assertGreater(bad['peak_rss_bytes'], small['peak_rss_bytes'])
        self.assertLess(small['peak_rss_bytes'], 256 * MIB)
        self.assertGreater(bad['peak_rss_bytes'], 256 * MIB)
        self.assertLess(fixed['peak_rss_bytes'], FIXED_PEAK)
        self.assertEqual(fixed['retained_bytes'], 0)
        self.assertEqual(bad['bytes_processed'], fixed['bytes_processed'])
        with tempfile.TemporaryDirectory() as directory:
            red, profile, _ = self.run_case(directory, ['--blocks', '96'])
            guard = profile['memory_guard']
            if sys.platform.startswith('linux') and red.returncode == 1:
                self.assertIn('MemoryError', red.stderr, 'OS address-space refusal must be observed, not inferred from exit 1')
                self.assertEqual(guard['hard_limit'], 'RLIMIT_AS')
            else:
                self.assertEqual(red.returncode, 3, red.stderr)
                self.assertEqual(guard['stop_reason'], 'rss_budget')
                self.assertGreaterEqual(guard['peak_group_rss_bytes'], guard['budget_bytes'])
                self.assertFalse(profile['memory_snapshot_at_exit'])
                self.assertIsNone(profile['memory_stacks'])
                self.assertEqual(profile['files'], {})
            self.assertNotEqual(guard['profiled_pid'], guard['supervisor_pid'])
            green, corrected, _ = self.run_case(directory, ['--blocks', '96', '--fixed'])
            self.assertEqual(green.returncode, 0, green.stderr)
            self.assertEqual(corrected['memory_guard']['status'], 'completed')
            self.assertIsNone(corrected['memory_guard']['stop_reason'])
            if sys.platform.startswith('linux') and not FREE_THREADED:
                # Free-threaded mimalloc reserves about 1 GiB at startup, so the runner skips RLIMIT_AS there.
                self.assertEqual(corrected['memory_guard']['hard_limit'], 'RLIMIT_AS')
            self.assertEqual(json.loads(green.stdout)['bytes_processed'], bad['bytes_processed'])
            self.assertFalse(any(e.get('leak_runs') for lines in corrected['files'].values() for e in lines.values()))

    def test_abrupt_exit_replaces_stale_report_with_incomplete_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / 'exit.py'
            target.write_text('import os\nos._exit(7)\n')
            (Path(directory) / 'guarded.json').write_text('{"stale": true}')
            result, profile, _ = self.run_case(directory, [], fixture=target)
            self.assertEqual(result.returncode, 7, result.stderr)
            self.assertNotIn('stale', profile)
            self.assertEqual(profile['memory_guard']['child_exit_code'], 7)
            self.assertFalse(profile['memory_snapshot_at_exit'])
            self.assertIsNone(profile['peak_traced_mb'])
            self.assertIn('unavailable', profile['sampler_error'])

    def test_guard_does_not_depend_on_sampler_gil_progress(self):
        if system_memory()['available_bytes'] < 1024 * MIB:
            self.skipTest('insufficient headroom for bounded pressure witness; incomplete')
        witness = subprocess.run([sys.executable, str(FIXTURE), '--starve'], capture_output=True, text=True, timeout=10)
        self.assertEqual(witness.returncode, 0, witness.stderr)
        self.assertGreater(json.loads(witness.stdout)['peak_rss_bytes'], 256 * MIB)
        with tempfile.TemporaryDirectory() as directory:
            result, profile, _ = self.run_case(directory, ['--starve'])
            if sys.platform.startswith('linux') and result.returncode == 1:
                self.assertIn('MemoryError', result.stderr)
                self.assertEqual(profile['memory_guard']['hard_limit'], 'RLIMIT_AS')
            else:
                self.assertEqual(result.returncode, 3, result.stderr)
                self.assertEqual(profile['memory_guard']['stop_reason'], 'rss_budget')
            fixed, corrected, _ = self.run_case(directory, ['--starve', '--fixed'])
            self.assertEqual(fixed.returncode, 0, fixed.stderr)
            self.assertEqual(corrected['memory_guard']['status'], 'completed')

    def test_native_pressure_red_green(self):
        if system_memory()['available_bytes'] < 1024 * MIB:
            self.skipTest('insufficient headroom for native pressure witness; incomplete')
        bad = subprocess.run([sys.executable, str(FIXTURE), '--native'], capture_output=True, text=True, timeout=10)
        fixed = subprocess.run([sys.executable, str(FIXTURE), '--native', '--fixed'], capture_output=True, text=True, timeout=10)
        self.assertEqual(bad.returncode, 0, bad.stderr)
        self.assertEqual(fixed.returncode, 0, fixed.stderr)
        bad_value, fixed_value = json.loads(bad.stdout), json.loads(fixed.stdout)
        self.assertGreater(bad_value['peak_rss_bytes'], 256 * MIB)
        self.assertLess(bad_value['python_peak_bytes'], 16 * MIB)
        self.assertLess(fixed_value['peak_rss_bytes'], FIXED_PEAK)
        self.assertEqual(bad_value['bytes_processed'], fixed_value['bytes_processed'])
        with tempfile.TemporaryDirectory() as directory:
            result, profile, _ = self.run_case(directory, ['--native'])
            if sys.platform.startswith('linux') and result.returncode == 1:
                self.assertRegex(result.stderr, r'MemoryError|\[Errno 12\]', 'native allocation refusal must be observed')
                self.assertEqual(profile['memory_guard']['hard_limit'], 'RLIMIT_AS')
            else:
                self.assertEqual(result.returncode, 3, result.stderr)
                self.assertEqual(profile['memory_guard']['stop_reason'], 'rss_budget')
            green, corrected, _ = self.run_case(directory, ['--native', '--fixed'])
            self.assertEqual(green.returncode, 0, green.stderr)
            self.assertEqual(corrected['memory_guard']['status'], 'completed')
            self.assertGreater(corrected['memory_guard']['supervisor_peak_rss_bytes'], 0)

    def test_invalid_budget_fails_before_target(self):
        with tempfile.TemporaryDirectory() as directory:
            marker = Path(directory) / 'marker'
            target = Path(directory) / 'target.py'
            target.write_text('from pathlib import Path\nPath(' + repr(str(marker)) + ').write_text("started")\n')
            for value in ('-1', 'nan', 'inf', '1'):
                result = subprocess.run([sys.executable, str(PROFILER), '--memory-limit-mb', value, str(target)],
                                        capture_output=True, text=True, timeout=10)
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertFalse(marker.exists())

    @unittest.skipUnless(os.name == 'posix', 'Windows abrupt supervisor termination needs job-object cleanup; incomplete')
    def test_task_termination_cleans_up_child(self):
        with tempfile.TemporaryDirectory() as directory:
            marker = Path(directory) / 'pid'
            target = Path(directory) / 'wait.py'
            target.write_text('import os, time\nfrom pathlib import Path\nPath(' + repr(str(marker))
                              + ').write_text(str(os.getpid()))\ntime.sleep(20)\n')
            output = Path(directory) / 'profile.json'
            process = subprocess.Popen([sys.executable, str(PROFILER), '--memory', 'off', '--out', str(output), str(target)],
                                       stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            try:
                deadline = time.monotonic() + 5
                while not marker.exists() and process.poll() is None and time.monotonic() < deadline:
                    time.sleep(.02)
                self.assertTrue(marker.exists(), 'target must start before cancellation')
                pid = int(marker.read_text())
                process.terminate()
                _, stderr = process.communicate(timeout=5)
                self.assertEqual(process.returncode, 130, stderr)
                self.assertEqual(json.loads(output.read_text())['memory_guard']['stop_reason'], 'interrupted')
                with self.assertRaises(ProcessLookupError):
                    os.kill(pid, 0)
            finally:
                if process.poll() is None:
                    process.kill()
                    process.communicate(timeout=5)

    @unittest.skipUnless(os.name == 'posix', 'Windows descendant RSS monitoring is unavailable; incomplete')
    def test_descendant_pressure_after_root_exit_red_green(self):
        if system_memory()['available_bytes'] < 1024 * MIB:
            self.skipTest('insufficient headroom for bounded family-pressure witness; incomplete')
        with tempfile.TemporaryDirectory() as directory:
            worker = Path(directory) / 'worker.py'
            worker.write_text('''import json, os, resource, sys, time
fixed = '--fixed' in sys.argv
held = []
total = 0
for _ in range(40):
    value = bytearray(4 << 20)
    value[:] = os.urandom(len(value))
    total += len(value)
    if not fixed: held.append(value)
peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
print(json.dumps({'bytes_processed': total, 't': time.time(),
    'peak': peak if sys.platform == 'darwin' else peak * 1024}), flush=True)
time.sleep(1.5)
''')
            target = Path(directory) / 'family.py'
            target.write_text('''import subprocess, sys
workers = [subprocess.Popen([sys.executable, sys.argv[1], *sys.argv[2:]]) for _ in range(2)]
if '--detach' not in sys.argv:
    for worker in workers: worker.wait()
''')
            bad = subprocess.run([sys.executable, str(target), str(worker)], capture_output=True, text=True, timeout=10)
            fixed = subprocess.run([sys.executable, str(target), str(worker), '--fixed'], capture_output=True, text=True, timeout=10)
            self.assertEqual(bad.returncode, 0, bad.stderr)
            self.assertEqual(fixed.returncode, 0, fixed.stderr)
            bad_rows, fixed_rows = [json.loads(line) for line in bad.stdout.splitlines()], [json.loads(line) for line in fixed.stdout.splitlines()]
            self.assertEqual(len(bad_rows), 2)
            self.assertLess(abs(bad_rows[0]['t'] - bad_rows[1]['t']), 1)
            self.assertGreater(sum(row['peak'] for row in bad_rows), 256 * MIB)
            self.assertEqual(sum(row['bytes_processed'] for row in bad_rows), sum(row['bytes_processed'] for row in fixed_rows))
            self.assertLess(sum(row['peak'] for row in fixed_rows), FIXED_FAMILY_PEAK)
            result, profile, _ = self.run_case(directory, [str(worker), '--detach'], fixture=target)
            self.assertEqual(result.returncode, 3, result.stderr)
            self.assertEqual(profile['memory_guard']['stop_reason'], 'rss_budget')
            self.assertLess(profile['memory_guard']['peak_root_rss_bytes'], 128 * MIB)
            green, corrected, _ = self.run_case(directory, [str(worker), '--detach', '--fixed'], fixture=target)
            self.assertEqual(green.returncode, 0, green.stderr)
            self.assertEqual(corrected['memory_guard']['status'], 'completed')


if __name__ == '__main__':
    result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromModule(sys.modules[__name__]))
    sys.exit(2 if result.skipped else 0 if result.wasSuccessful() else 1)
