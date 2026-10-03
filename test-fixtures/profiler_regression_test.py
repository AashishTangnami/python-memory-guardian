"""Regressions for source identity and transient peaks; no third-party packages."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
PROFILER = ROOT / 'server' / 'pmg_profile.py'
spec = importlib.util.spec_from_file_location('guardian_profiler', PROFILER)
profiler = importlib.util.module_from_spec(spec)
spec.loader.exec_module(profiler)


class ProfileRegressions(unittest.TestCase):
    def run_script(self, directory, source, memory='off', interval='0.01'):
        script = directory / 'main.py'
        script.write_text(source)
        report = directory / 'profile.json'
        result = subprocess.run(
            [sys.executable, str(PROFILER), '--memory', memory, '--interval', interval,
             '--root', str(directory), '--out', str(report), str(script)],
            capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        return script, json.loads(report.read_text())

    def test_changed_source_is_not_certified_fresh(self):
        for restore in (False, True):
            with self.subTest(restore=restore), tempfile.TemporaryDirectory() as tmp:
                script, report = self.run_script(Path(tmp),
                    'from pathlib import Path\nimport time\n'
                    'source = Path(__file__).read_bytes()\n'
                    'Path(__file__).write_text("# replacement source\\n")\n'
                    + ('Path(__file__).write_bytes(source)\n' if restore else '')
                    + 'time.sleep(0.12)\n')
                self.assertIn(str(script), report['files'])
                self.assertNotIn(str(script), report['file_hashes'])
                self.assertTrue(all('scope' not in e and 'assigns' not in e and 'calls' not in e
                                    for e in report['files'][str(script)].values()))

    def test_import_changed_before_first_sample_is_stale(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            module = directory / 'helper.py'
            module.write_text('import time\ndef work():\n    time.sleep(0.12)\n')
            _, report = self.run_script(directory,
                'import helper\nfrom pathlib import Path\n'
                'Path(helper.__file__).write_text("# changed before helper is sampled\\n")\n'
                'helper.work()\n')
            self.assertIn(str(module), report['files'])
            self.assertNotIn(str(module), report['file_hashes'])

    def test_unchanged_import_and_crlf_source_have_matching_hashes(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            module = directory / 'helper.py'
            source = b'import time\r\ndef work():\r\n    time.sleep(0.12)\r\n'
            module.write_bytes(source)
            _, report = self.run_script(directory, 'import helper\nhelper.work()\n')
            self.assertEqual(report['file_hashes'][str(module)],
                             hashlib.sha1(source.replace(b'\r\n', b'\n')).hexdigest())

    def test_created_and_deleted_sources_cannot_become_fresh(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            _, report = self.run_script(directory,
                'from pathlib import Path\n'
                'Path(__file__).with_name("helper.py").write_text("import time\\ndef work():\\n    time.sleep(0.12)\\n")\n'
                'import helper\nhelper.work()\nPath(__file__).unlink()\n')
            self.assertTrue(report['files'])
            self.assertEqual(report['file_hashes'], {})

    def test_peak_survives_sampler_reset(self):
        with tempfile.TemporaryDirectory() as tmp:
            _, report = self.run_script(Path(tmp),
                'import time\ntime.sleep(0.1)\n'
                'data = bytearray(64_000_000)\ndel data\ntime.sleep(0.3)\n',
                memory='precise', interval='0.05')
            self.assertGreaterEqual(report['peak_traced_mb'], 64)
            spikes = [e['transient_peak_mb'] for entries in report['files'].values()
                      for e in entries.values()]
            self.assertGreaterEqual(report['peak_traced_mb'], max(spikes))

    def test_interval_peak_is_saved_before_reset(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = profiler.Profiler(tmp, .01, 'precise')
            p.rss, p.rss_kind = lambda: 0, None
            # One sample after an allocation has already been freed. The loop
            # must preserve its peak even though current traced memory is small.
            with patch.object(profiler.time, 'sleep', side_effect=lambda _: p._stop.set()), \
                    patch.object(profiler.sys, '_current_frames', return_value={}), \
                    patch.object(profiler.tracemalloc, 'get_traced_memory', return_value=(1000, 64_000_000)), \
                    patch.object(profiler.tracemalloc, 'reset_peak'), \
                    patch.object(profiler.tracemalloc, 'get_tracemalloc_memory', return_value=0), \
                    patch.object(p, '_snapshot'):
                p._run()
            self.assertEqual(p.peak_traced, 64_000_000)

    def test_metadata_change_invalidates_even_restored_mtime(self):
        with tempfile.TemporaryDirectory() as tmp:
            script = Path(tmp) / 'file.py'
            script.write_text('old = 1\n')
            p = profiler.Profiler(tmp, .01, 'off')
            p._remember_sources()
            version = p._source_version(str(script))
            before = script.stat()
            script.write_text('new = 2\n')
            os.utime(script, ns=(before.st_atime_ns, before.st_mtime_ns))
            self.assertIsNone(p._unchanged_source(str(script)))
            # Simulate metadata that cannot distinguish the two contents (e.g.
            # restored mtime on a platform reporting creation time as ctime).
            with patch.object(p, '_source_version', return_value=version):
                self.assertIsNone(p._unchanged_source(str(script)))

    def test_source_is_parsed_once_per_reported_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            script = Path(tmp) / 'file.py'
            script.write_text('def work():\n    pass\n')
            p = profiler.Profiler(tmp, .01, 'off')
            p.start()
            p.stop()
            for line in (1, 2):
                st = profiler.LineStats()
                st.samples, st.func = 1, ('work', 1)
                p.lines[(str(script), line)] = st
            with patch.object(p, '_symbols', wraps=p._symbols) as symbols:
                p.report(str(script))
                self.assertEqual(symbols.call_count, 1)

    def test_standalone_copy_matches(self):
        self.assertEqual(PROFILER.read_bytes(), (ROOT / 'pmg_profile.py').read_bytes())

    def test_idle_timer_calibration_ignores_one_scheduling_pause(self):
        elapsed = [.0025] * 8 + [.1]
        ticks = [t for i, extra in enumerate(elapsed) for t in (float(i), i + .01 + extra)]
        with patch.object(profiler.time, 'sleep'), \
                patch.object(profiler.time, 'perf_counter', side_effect=ticks):
            self.assertAlmostEqual(profiler._sleep_overhead(.01), .0025)

    def test_time_split_excludes_timer_and_sampler_overhead(self):
        cases = [('native', .0125, 0), ('native', .0125, .005), ('python', .0175, .005)]
        for expected, sleep_duration, sample_work in cases:
            with self.subTest(expected=expected, sample_work=sample_work), tempfile.TemporaryDirectory() as tmp:
                p = profiler.Profiler(tmp, .01, 'off')
                p.split, p.switch, p.sleep_overhead = True, .005, .0025
                p.rss, p.rss_kind = lambda: 0, None
                ident = profiler.threading.get_ident() + 1
                cpu = iter([1.0, 1.01])
                p._clocks[ident] = lambda: next(cpu)
                loc = (str(Path(tmp) / 'work.py'), 1)
                sleeps = []

                def sleep(_):
                    sleeps.append(True)
                    if len(sleeps) == 2:
                        p._stop.set()

                ticks = [0, 0, .0125, .0125 + sample_work, .0125 + sample_work + sleep_duration]
                with patch.object(profiler.time, 'sleep', side_effect=sleep), \
                        patch.object(profiler.time, 'perf_counter', side_effect=ticks), \
                        patch.object(profiler.sys, '_current_frames', return_value={ident: None}), \
                        patch.object(p, '_user_line', return_value=(loc, ('work', 1))):
                    p._run()
                line = p.lines[loc]
                self.assertAlmostEqual(getattr(line, expected + '_s'), .01)
                other = 'python_s' if expected == 'native' else 'native_s'
                self.assertEqual(getattr(line, other), 0)


if __name__ == '__main__':
    unittest.main()
