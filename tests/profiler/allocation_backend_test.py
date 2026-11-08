"""Witness first, allocator capture second, corrected program last.

Optional dependency absence is a skip and acceptance marks it incomplete.
"""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
WORK = ROOT / 'tests' / 'fixtures' / 'profiler' / 'allocation_events_workload.py'


class AllocationFixtures(unittest.TestCase):
    def test_capture_file_never_follows_a_planted_link(self):
        # Memray is optional; a stand-in records where the capture would be written.
        sys.path.insert(0, str(ROOT / 'src' / 'python'))
        from unittest.mock import patch
        from pmg_profiler import allocations
        written = []

        class Memray:
            class FileFormat:
                ALL_ALLOCATIONS = 1

            @staticmethod
            def FileDestination(path, overwrite):
                written.append(Path(path))
                return path

            class Tracker:
                def __init__(self, destination, **_options):
                    Path(destination).write_text('capture')

                def __enter__(self):
                    return self
        with tempfile.TemporaryDirectory() as tmp, patch.object(allocations, '_memray', return_value=Memray):
            victim = Path(tmp) / 'victim.txt'
            victim.write_text('USER DATA')
            capture = Path(tmp) / '.pmg' / 'profile.json.1.memray'
            capture.parent.mkdir()
            capture.symlink_to(victim)
            allocations.AllocationRecorder(capture, tmp).start()
            self.assertEqual(victim.read_text(), 'USER DATA', 'the link target is not written')
            self.assertEqual(written[0].name, capture.name, 'the capture keeps its own name, not the link target')
            self.assertFalse(capture.is_symlink())

    def test_instrumentation_and_library_scope_are_not_target_allocations(self):
        sys.path.insert(0, str(ROOT / 'src' / 'python'))
        from pmg_profiler.allocations import _site_source
        target = {'file': str(HERE / 'app.py'), 'function': 'main', 'line': 2}
        helper = {'file': str(ROOT / 'src/python/pmg_diagnostics.py'), 'function': 'event', 'line': 10}
        self.assertEqual(_site_source([helper, target], str(HERE), 'python_allocator'), ('profiler', None))
        library = {'file': str(HERE / '.venv/site-packages/library.py'), 'function': 'allocate', 'line': 4}
        scope, source = _site_source([library, target], str(HERE), 'system_allocator')
        self.assertEqual(scope, 'target')
        self.assertEqual(source['file'], target['file'])
        self.assertEqual(source['role'], 'traced_user_callsite')

    def execute(self, kind, fixed=False, capture=False, count=128):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        base = Path(directory.name)
        cmd = [sys.executable, str(WORK), '--kind', kind, '--count', str(count)]
        if fixed:
            cmd.append('--fixed')
        if capture:
            if importlib.util.find_spec('memray') is None:
                self.skipTest('optional memray dependency unavailable')
            cmd += ['--capture', str(base / 'trace.bin'), '--summary', str(base / 'summary.json')]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stderr)
        witness = json.loads(result.stdout)
        if not capture:
            return witness, None
        summary = json.loads((base / 'summary.json').read_text())
        self.assertTrue(summary['available'])
        for file in summary['files']:
            artifact = file['artifact']
            self.assertEqual(hashlib.sha256(Path(artifact['path']).read_bytes()).hexdigest(), artifact['sha256'])
        return witness, summary

    def volume(self, summary, function, domain='python_allocator', outstanding=False):
        key = 'outstanding_at_capture_end_bytes' if outstanding else 'requested_bytes'
        return sum(site[key] for file in summary['files'] for site in file['sites']
                   if site['domain'] == domain and any(frame['function'] == function
                     for frame in site['python_stack'] or []))

    def test_churn_red_green(self):
        positive, _ = self.execute('churn')
        corrected, _ = self.execute('churn', True)
        self.assertEqual(positive['allocation_operations'], 128)
        self.assertEqual(corrected['allocation_operations'], 1)
        self.assertEqual(positive['checksum'], corrected['checksum'])
        _, trace = self.execute('churn', capture=True)
        _, small = self.execute('churn', capture=True, count=32)
        _, fixed = self.execute('churn', True, True)
        self.assertGreater(self.volume(trace, 'churn'), 4_000_000)
        self.assertGreater(self.volume(trace, 'churn'), self.volume(small, 'churn') * 2)
        self.assertLess(self.volume(fixed, 'churn'), self.volume(trace, 'churn') / 4)
        self.assertEqual(trace['availability']['allocation_timestamps'], 'unavailable')

    def test_copies_red_green(self):
        positive, _ = self.execute('copies')
        corrected, _ = self.execute('copies', True)
        self.assertGreater(positive['copied_slot_bytes'], 4_000_000)
        self.assertEqual(corrected['copy_operations'], 0)
        self.assertEqual(positive['checksum'], corrected['checksum'])
        _, trace = self.execute('copies', capture=True)
        _, fixed = self.execute('copies', True, True)
        self.assertGreater(self.volume(trace, 'copies'), self.volume(fixed, 'copies') * 4)

    def test_native_mapping_red_green(self):
        positive, _ = self.execute('native')
        corrected, _ = self.execute('native', True)
        self.assertEqual(positive['native_held_payload_bytes'], 128 * 65536)
        self.assertEqual(corrected['native_held_payload_bytes'], 0)
        self.assertLess(positive['python_traced_current_bytes'], positive['native_held_payload_bytes'] / 10)
        _, trace = self.execute('native', capture=True)
        _, fixed = self.execute('native', True, True)
        self.assertGreater(self.volume(trace, 'native', 'system_allocator', True), 4_000_000)
        self.assertLess(self.volume(fixed, 'native', 'system_allocator', True), 65536)

    def test_worker_attribution_red_green(self):
        positive, _ = self.execute('threads')
        corrected, _ = self.execute('threads', True)
        self.assertEqual(positive['allocation_operations'], 128)
        self.assertEqual(corrected['allocation_operations'], 1)
        _, trace = self.execute('threads', capture=True)
        sites = [site for file in trace['files'] for site in file['sites']
                 if site['domain'] == 'python_allocator' and any(frame['function'] == 'allocating_worker'
                   for frame in site['python_stack'] or []) and site['requested_bytes'] >= 65536]
        self.assertTrue(sites)
        self.assertEqual(len({site['thread_id'] for site in sites}), 1)
        self.assertTrue(all(site['thread_id_kind'] == 'memray_capture' for site in sites))
        _, fixed = self.execute('threads', True, True)
        self.assertLess(self.volume(fixed, 'allocating_worker'), self.volume(trace, 'allocating_worker') / 4)

    def test_unhooked_native_is_not_claimed_complete(self):
        witness, _ = self.execute('unhooked_native')
        self.assertTrue(witness['native_malloc_and_free_observed'])
        _, trace = self.execute('unhooked_native', capture=True)
        self.assertEqual(trace['availability']['native_coverage'], 'partial')
        self.assertEqual(trace['availability']['object_counts'], 'unavailable')

    def test_cli_capture_and_exception_artifact(self):
        if importlib.util.find_spec('memray') is None:
            self.skipTest('optional memray dependency unavailable')
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            script = base / 'broken.py'
            script.write_text('value = bytearray(100000)\nraise RuntimeError("fixture")\n')
            out = base / 'profile.json'
            command = [sys.executable, str(ROOT / 'src/python/pmg_profile.py'), '--memory', 'off',
                       '--allocation-backend', 'memray', '--out', str(out), str(script)]
            result = subprocess.run(command, capture_output=True, text=True, timeout=60)
            self.assertEqual(result.returncode, 1)
            profile = json.loads(out.read_text())
            self.assertTrue(profile['allocation_backend']['available'])
            self.assertEqual(profile['native_memory']['status'], 'unavailable')
            self.assertTrue(Path(profile['allocation_backend']['files'][0]['artifact']['path']).is_file())
            first = Path(profile['allocation_backend']['files'][0]['artifact']['path'])
            first_hash = hashlib.sha256(first.read_bytes()).hexdigest()
            repeated = subprocess.run(command, capture_output=True, text=True, timeout=60)
            self.assertEqual(repeated.returncode, 1)
            second = Path(json.loads(out.read_text())['allocation_backend']['files'][0]['artifact']['path'])
            self.assertNotEqual(first, second)
            self.assertEqual(hashlib.sha256(first.read_bytes()).hexdigest(), first_hash)

    def test_backend_unavailable_fails_before_target(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            # A deliberately unavailable backend must not silently fall back.
            (base / 'memray.py').write_text('raise ImportError("fixture unavailable")\n')
            marker = base / 'ran'
            script = base / 'target.py'
            script.write_text(f'open({str(marker)!r}, "w").close()\n')
            env = dict(os.environ, PYTHONPATH=str(base))
            result = subprocess.run([sys.executable, str(ROOT / 'src/python/pmg_profile.py'),
                '--allocation-backend', 'memray', str(script)], env=env, capture_output=True, text=True)
            self.assertEqual(result.returncode, 2)
            self.assertIn('unavailable', result.stderr)
            self.assertFalse(marker.exists())

    def test_system_detail_reports_lost_python_coverage(self):
        if importlib.util.find_spec('memray') is None:
            self.skipTest('optional memray dependency unavailable')
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            script = base / 'work.py'
            script.write_text('owners = [bytearray(65536) for _ in range(16)]\n')
            out = base / 'profile.json'
            result = subprocess.run([sys.executable, str(ROOT / 'src/python/pmg_profile.py'),
                '--memory', 'off', '--allocation-backend', 'memray', '--allocation-detail', 'system',
                '--out', str(out), str(script)], capture_output=True, text=True, timeout=60)
            self.assertEqual(result.returncode, 0, result.stderr)
            capture = json.loads(out.read_text())['allocation_backend']
            self.assertTrue(capture['available'])
            self.assertEqual(capture['availability']['python_allocator_requests'], 'unavailable')
            self.assertTrue(all(not file['python_allocator_requests_traced'] for file in capture['files']))
            self.assertTrue(all(site['domain'] != 'python_allocator' for file in capture['files'] for site in file['sites']))

    def test_site_overflow_keeps_volume_and_reports_incomplete(self):
        witness, _ = self.execute('churn')
        self.assertGreater(witness['requested_payload_bytes'], 4_000_000)
        if importlib.util.find_spec('memray') is None:
            self.skipTest('optional memray dependency unavailable')
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            result = subprocess.run([sys.executable, str(WORK), '--kind', 'churn',
                '--max-sites', '1', '--capture', str(base / 'trace.bin'), '--summary', str(base / 'summary.json')],
                capture_output=True, text=True, timeout=60)
            self.assertEqual(result.returncode, 0, result.stderr)
            file = json.loads((base / 'summary.json').read_text())['files'][0]
            self.assertFalse(file['summary_complete'])
            self.assertEqual(len(file['sites']), 1)
            self.assertGreater(file['omitted_site_requested_bytes'], witness['requested_payload_bytes'])
            self.assertEqual(file['allocation_count'], sum(site['allocation_count'] for site in file['sites']) + file['omitted_site_allocation_count'])


if __name__ == '__main__':
    unittest.main()
