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
    def run_script(self, directory, source, memory='off', interval='0.01', monitoring='off'):
        script = directory / 'main.py'
        script.write_text(source)
        report = directory / 'profile.json'
        result = subprocess.run(
            [sys.executable, str(PROFILER), '--memory', memory, '--interval', interval,
             '--monitoring', monitoring,
             '--root', str(directory), '--out', str(report), str(script)],
            capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        return script, json.loads(report.read_text())

    def test_optional_monitoring_reports_executed_lines(self):
        with tempfile.TemporaryDirectory() as tmp:
            script, report = self.run_script(Path(tmp),
                'total = 0\nfor i in range(200):\n    total += i\n', monitoring='lines')
            status = report['monitoring']
            self.assertEqual(status['requested'], 'lines')
            if hasattr(sys, 'monitoring'):
                self.assertTrue(status['active'], status)
                self.assertGreater(report['files'][str(script)]['3']['line_events'], 1)
            else:
                self.assertFalse(status['active'])
                self.assertEqual(status['reason'], 'requires Python 3.12+')

    def test_monitoring_busy_tool_falls_back_without_claiming_coverage(self):
        mon = getattr(sys, 'monitoring', None)
        if mon is None:
            self.skipTest('sys.monitoring requires Python 3.12+')
        with tempfile.TemporaryDirectory() as tmp, patch.object(mon, 'get_tool', return_value='another profiler'):
            p = profiler.Profiler(tmp, .01, 'off', monitoring='lines')
            p._enable_monitoring()
            self.assertFalse(p.monitoring_enabled)
            self.assertIn('already in use', p.monitoring_reason)
            self.assertIsNone(p._monitoring_tool)

    def test_monitoring_unavailable_falls_back(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(profiler.sys, 'monitoring', None,
                                                               create=True):
            p = profiler.Profiler(tmp, .01, 'off', monitoring='lines')
            p._enable_monitoring()
            self.assertFalse(p.monitoring_enabled)
            self.assertEqual(p.monitoring_reason, 'requires Python 3.12+')

    def test_monitoring_releases_tool_and_callback(self):
        mon = getattr(sys, 'monitoring', None)
        if mon is None:
            self.skipTest('sys.monitoring requires Python 3.12+')
        tool = mon.PROFILER_ID
        if mon.get_tool(tool) is not None:
            self.skipTest('profiler tool ID already in use')
        with tempfile.TemporaryDirectory() as tmp:
            p = profiler.Profiler(tmp, .01, 'off', monitoring='lines')
            p._enable_monitoring()
            try:
                self.assertTrue(p.monitoring_enabled)
                self.assertNotEqual(mon.get_events(tool), 0)
            finally:
                p._disable_monitoring()
            self.assertIsNone(mon.get_tool(tool))
            self.assertEqual(mon.get_events(tool), 0)

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

    def test_worker_shutdown_is_included_without_joining_idle_executors(self):
        for start in ('threading.Thread(target=work).start()',
                      'pool = ThreadPoolExecutor()\npool.submit(work)'):
            with self.subTest(start=start), tempfile.TemporaryDirectory() as tmp:
                _, report = self.run_script(Path(tmp),
                    'import threading, time\nfrom concurrent.futures import ThreadPoolExecutor\n'
                    'def work():\n    time.sleep(.25)\n' + start + '\n')
                self.assertGreaterEqual(report['wall_s'], .24)
                self.assertTrue(any(f['name'] == 'work' for f in report['stacks']['frames']))

    def test_cli_preserves_arguments_and_output_after_chdir(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'nested').mkdir()
            script = root / 'args.py'
            script.write_text('import sys, os\nassert sys.argv[1:] == ["--", "-input.txt"]\nos.chdir("nested")\n')
            r = subprocess.run([sys.executable, str(PROFILER), '--out', 'result.json',
                                str(script), '--', '-input.txt'], cwd=tmp, capture_output=True, text=True, timeout=15)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertTrue((root / 'result.json').exists())
            self.assertFalse((root / 'nested' / 'result.json').exists())

    def test_source_hash_uses_decoded_text(self):
        for encoding in ('utf-8-sig', 'latin-1'):
            text = '# coding: ' + encoding + '\nname = "café"\n'
            self.assertEqual(profiler.Profiler._text_hash(text.encode(encoding)),
                             hashlib.sha1(text.encode('utf-8')).hexdigest())

    def test_stacks_preserve_call_paths_and_full_function_extent(self):
        with tempfile.TemporaryDirectory() as tmp:
            script, report = self.run_script(Path(tmp),
                'import time\ndef leaf():\n    time.sleep(.08)\n    return 42\n'
                'def left():\n    leaf()\ndef right():\n    leaf()\nleft()\nright()\n')
            frames = report['stacks']['frames']
            paths = [[frames[i]['name'] for i in row['frames']] for row in report['stacks']['samples']]
            self.assertTrue(any('left' in p and 'leaf' in p for p in paths), paths)
            self.assertTrue(any('right' in p and 'leaf' in p for p in paths), paths)
            self.assertEqual(report['functions'][str(script)]['2']['end_line'], 4)
            weights = sum(sum(s[k] for k in ('python_s', 'native_s', 'system_s', 'unsplit_s'))
                          for s in report['stacks']['samples'])
            lines = sum(e['time_s'] for values in report['files'].values() for e in values.values())
            self.assertAlmostEqual(weights, lines, delta=.002)

    def test_retention_evidence_distinguishes_growth_from_release(self):
        with tempfile.TemporaryDirectory() as tmp:
            script = Path(tmp) / 'case.py'
            script.write_text('growth = []\nscratch = []\n')
            p = profiler.Profiler(tmp, .01, 'precise')
            p.start()
            p.stop()
            growth, scratch = (str(script), 1), (str(script), 2)
            p.snapshots = [(i * .5, {growth: (i + 1) * 2_000_000,
                                    scratch: 10_000_000 if i < 2 else 0}, 0) for i in range(4)]
            for loc in (growth, scratch):
                p._stats(loc).samples = 1
            rows = p.report(str(script))['files'][str(script)]
            self.assertEqual(rows['1']['leak_runs'], 3)
            self.assertEqual(rows['1']['retention']['growth_mb'], 6)
            self.assertEqual(rows['2']['retention']['releases'], 1)
            self.assertNotIn('leak_runs', rows['2'])

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
