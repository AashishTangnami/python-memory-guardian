"""Regressions for source identity and transient peaks; no third-party packages."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
PROFILER = ROOT / 'server' / 'pmg_profile.py'
spec = importlib.util.spec_from_file_location('guardian_profiler', PROFILER)
profiler = importlib.util.module_from_spec(spec)
spec.loader.exec_module(profiler)


class ProfileRegressions(unittest.TestCase):
    def run_script(self, directory, source, memory='off', interval='0.01', monitoring='off', frames='2'):
        script = directory / 'main.py'
        script.write_text(source)
        report = directory / 'profile.json'
        result = subprocess.run(
            [sys.executable, str(PROFILER), '--memory', memory, '--interval', interval,
             '--monitoring', monitoring, '--frames', frames,
             '--root', str(directory), '--out', str(report), str(script)],
            capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        return script, json.loads(report.read_text())

    def test_script_stopping_tracemalloc_keeps_profile(self):
        # probe.py-style measurement: a nested start() is a no-op, but stop() ends the profiler's tracing.
        source = ('import time, tracemalloc\n'
                  'keep = [bytearray(1 << 20) for _ in range(4)]\n'
                  'time.sleep(0.3)\n'
                  'tracemalloc.start()\n'
                  'tracemalloc.stop()\n'
                  't = time.perf_counter()\n'
                  'while time.perf_counter() - t < 0.3:\n'
                  '    pass\n')
        with tempfile.TemporaryDirectory() as tmp:
            script, report = self.run_script(Path(tmp), source, memory='precise')
            self.assertIsNone(report['sampler_error'])
            self.assertGreater(report['memory_tracing_lost_s'], 0.2)
            lines = report['files'][str(script)]
            self.assertFalse([ln for ln, e in lines.items() if e.get('leak_runs')], 'no leak claims without an exit snapshot')
            self.assertGreater(sum(lines.get(n, {}).get('samples', 0) for n in ('7', '8')), 0,
                               'timing continues after tracing stops')

    def test_timeline_memory_is_bounded_and_evenly_spaced(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = profiler.Profiler(tmp, .01, 'fast')
            for i in range(100_000):                     # ~17 min of samples at the default interval
                p._record_timeline((i * .01, 0.0, 0.0))
            self.assertLessEqual(len(p.timeline), profiler.TIMELINE_CAP)
            self.assertGreaterEqual(len(p.timeline), profiler.TIMELINE_CAP // 2)
            self.assertEqual(p.timeline[0][0], 0.0, 'the run start is kept')
            gaps = {round(b[0] - a[0], 6) for a, b in zip(p.timeline, p.timeline[1:])}
            self.assertEqual(len(gaps), 1, f'points stay evenly spaced: {sorted(gaps)[:5]}')

    def test_rss_peak_survives_timeline_thinning(self):
        # A short spike in a run that thins the timeline (cap lowered to 8); rss_peak_mb must keep it.
        def wait(seconds):
            t = time.perf_counter()
            while time.perf_counter() - t < seconds:
                pass
        with tempfile.TemporaryDirectory() as tmp, patch.object(profiler, 'TIMELINE_CAP', 8):
            p = profiler.Profiler(tmp, .005, 'fast')
            if p.rss_kind != 'current':
                self.skipTest('platform reports only a running RSS peak')
            p.start()
            wait(.3)
            spike = b'\x01' * (200 << 20)                 # written bytes, so the pages are resident
            during = p.rss()                              # baseline-independent: RSS while the spike lives
            wait(.15)
            del spike
            wait(.3)
            p.stop()
            report = p.report(str(Path(tmp) / 'main.py'))
        self.assertLessEqual(len(p.timeline), 8 + 1, 'bounded, plus the exit point')
        self.assertGreaterEqual(report['rss_peak_mb'], during / 1e6 - 1, 'peak includes the thinned-out spike')

    def test_single_walk_attributes_line_and_stack(self):
        # User code (under root) and library code (this test file, outside root) interleave.
        with tempfile.TemporaryDirectory() as tmp:
            p = profiler.Profiler(tmp, .01, 'off')
            user_file = str(Path(tmp) / 'user.py')
            ns = {}
            exec(compile('def rec(n, lib):\n    return lib(n)\n', user_file, 'exec'), ns)
            captured = {}

            def lib(n, depth=0):
                if depth:                                   # pure library recursion, no user frames
                    return lib(n, depth - 1)
                if n:
                    return ns['rec'](n - 1, lib)
                captured['frame'] = sys._getframe()
                return None

            ns['rec'](3, lib)
            loc, func, stack = p._walk(captured['frame'])
            names = [p._codes[stack[k]][1] for k in range(0, len(stack), 2)]
            users = [p._codes[stack[k]][3] for k in range(0, len(stack), 2)]
            self.assertEqual((loc, func), ((os.path.abspath(user_file), 2), ('rec', 1)), 'innermost user line')
            self.assertTrue(users[0] and not users[-1], 'stack runs from the outermost user frame to the active frame')
            self.assertEqual(names.count('rec'), 4)
            self.assertTrue(all(isinstance(v, int) for v in stack), 'sampler keys are flat ints')

            # A user frame under more than 128 library frames: attribution still finds it, the stack is empty.
            deep = {}
            def bottom(n, depth=150):
                if depth:
                    return bottom(n, depth - 1)
                deep['frame'] = sys._getframe()
            ns['rec'](0, bottom)
            loc, _, stack = p._walk(deep['frame'])
            self.assertEqual(loc, (os.path.abspath(user_file), 2))
            self.assertEqual(stack, ())
            self.assertTrue(p.stack_depth_limited)

    def test_gil_holding_call_result_is_credited_once_to_its_line(self):
        # Deterministic replacement for profiler_test's end-to-end sorted() check. A C call that
        # holds the GIL (sorted) lets the sampler in only when it returns, while its 12 MB copy is
        # still alive: the step in traced memory goes to the call's line, once. The 6 MB timsort
        # buffer freed inside the call is a spike below the threshold (the larger of 10 MiB and 25%
        # of traced memory, here 15 MB), so it is not charged.
        with tempfile.TemporaryDirectory() as tmp:
            p = profiler.Profiler(tmp, .01, 'precise')
            p.rss, p.rss_kind = lambda: 0, None
            build, call = (str(Path(tmp) / 'work.py'), 34), (str(Path(tmp) / 'work.py'), 16)
            MB = 1_000_000
            traced = iter([(48 * MB, 48 * MB),           # list built (attributed to the build line)
                           (60 * MB, 66 * MB),           # after sorted(): copy alive, buffer was the peak
                           (60 * MB, 66 * MB)])          # after the next sorted(): same level
            lines = iter([build, call, call])
            sleeps = []

            def sleep(_):
                sleeps.append(True)
                if len(sleeps) == 3:
                    p._stop.set()

            with patch.object(profiler.time, 'sleep', side_effect=sleep), \
                    patch.object(profiler.sys, '_current_frames', return_value={profiler.threading.get_ident() + 1: None}), \
                    patch.object(p, '_walk', side_effect=lambda _f: (next(lines), ('work', 1), ())), \
                    patch.object(profiler.tracemalloc, 'get_traced_memory', side_effect=lambda: next(traced)), \
                    patch.object(profiler.tracemalloc, 'reset_peak'), \
                    patch.object(profiler.tracemalloc, 'is_tracing', return_value=True), \
                    patch.object(profiler.tracemalloc, 'get_tracemalloc_memory', return_value=0), \
                    patch.object(p, '_snapshot'):
                p._run()
            self.assertIsNone(p.sampler_error)
            self.assertEqual(p.lines[build].traced_up, 48 * MB)
            self.assertEqual(p.lines[call].traced_up, 12 * MB, 'the live copy is credited once to the call line')
            self.assertEqual(p.lines[call].transient, 0, 'a 6 MB in-call buffer is below 25% of 60 MB traced')

    def test_snapshot_pause_is_not_charged_to_user_lines(self):
        # A precise snapshot holds the GIL, so the user thread only waits for it. That pause is profiler
        # overhead: the next sample must cover one interval, not interval + snapshot.
        with tempfile.TemporaryDirectory() as tmp:
            p = profiler.Profiler(tmp, .01, 'precise')
            p.rss, p.rss_kind = lambda: 0, None
            clock = [0.0]
            ident = profiler.threading.get_ident() + 1
            p._clocks[ident] = lambda: 1.0                  # blocked: no CPU time while the snapshot runs
            loc = (str(Path(tmp) / 'work.py'), 3)
            sleeps = []

            def sleep(seconds):
                clock[0] += seconds
                sleeps.append(True)
                if len(sleeps) == 3:
                    p._stop.set()

            def snapshot(_t):
                clock[0] += .5

            MB = 1_000_000
            with patch.object(profiler.time, 'sleep', side_effect=sleep), \
                    patch.object(profiler.time, 'perf_counter', side_effect=lambda: clock[0]), \
                    patch.object(profiler.time, 'process_time', return_value=0.0), \
                    patch.object(profiler.sys, '_current_frames', return_value={ident: None}), \
                    patch.object(p, '_walk', return_value=(loc, ('work', 1), ())), \
                    patch.object(profiler.tracemalloc, 'get_traced_memory', return_value=(20 * MB, 20 * MB)), \
                    patch.object(profiler.tracemalloc, 'reset_peak'), \
                    patch.object(profiler.tracemalloc, 'is_tracing', return_value=True), \
                    patch.object(profiler.tracemalloc, 'get_tracemalloc_memory', return_value=0), \
                    patch.object(p, '_snapshot', side_effect=snapshot):
                p._run()
            self.assertIsNone(p.sampler_error)
            self.assertGreater(p.snap_cost, .4, 'the first sample saw a 20 MB spike and snapshotted')
            st = p.lines[loc]
            self.assertEqual(st.samples, 3)
            self.assertAlmostEqual(st.python_s + st.native_s + st.system_s + st.cpu_s, .03, places=6)

    def test_tracemalloc_bookkeeping_is_not_counted_as_native_memory(self):
        # Scripted: each sample, traced memory +40 MB, tracemalloc bookkeeping +50 MB, RSS +100 MB, all on one
        # line. Bookkeeping is the profiler's memory, so the line's native estimate is 100 - 40 - 50 = 10 MB.
        MB = 1_000_000
        with tempfile.TemporaryDirectory() as tmp:
            p = profiler.Profiler(tmp, .01, 'precise')
            rss = iter([0, 100 * MB, 200 * MB, 300 * MB])
            p.rss, p.rss_kind = lambda: next(rss), 'current'
            traced = iter([(40 * MB, 40 * MB), (80 * MB, 80 * MB), (120 * MB, 120 * MB)])
            book = iter([50 * MB, 100 * MB, 150 * MB])
            loc, sleeps = (str(Path(tmp) / 'work.py'), 3), []

            def sleep(_):
                sleeps.append(True)
                if len(sleeps) == 3:
                    p._stop.set()
            with patch.object(profiler.time, 'sleep', side_effect=sleep), \
                    patch.object(profiler.sys, '_current_frames', return_value={profiler.threading.get_ident() + 1: None}), \
                    patch.object(p, '_walk', return_value=(loc, ('work', 1), (0, 3))), \
                    patch.object(profiler.tracemalloc, 'get_traced_memory', side_effect=lambda: next(traced)), \
                    patch.object(profiler.tracemalloc, 'get_tracemalloc_memory', side_effect=lambda: next(book)), \
                    patch.object(profiler.tracemalloc, 'reset_peak'), \
                    patch.object(profiler.tracemalloc, 'is_tracing', return_value=True), \
                    patch.object(p, '_snapshot'):
                p._run()
            self.assertIsNone(p.sampler_error)
            st = p.lines[loc]
            self.assertEqual((st.rss_up, st.traced_up, st.book_up), (300 * MB, 120 * MB, 150 * MB))
            self.assertEqual(p.book_max, 150 * MB)
            # The same sample also credits the growth to the thread's whole stack.
            key = (str(profiler.threading.get_ident() + 1), 'Thread', (0, 3))
            self.assertEqual(p.alloc_stacks, {key: 120 * MB}, 'allocation by call path, same key as the time stack')

    def test_native_estimate_excludes_tracemalloc_bookkeeping_in_a_real_run(self):
        # 300k small dicts and no C extension: before, RSS growth minus traced growth was reported as
        # ~100 MB of "native" memory, which was tracemalloc's own bookkeeping.
        source = ('def build(n):\n'
                  '    return [{"id": str(i), "n": i} for i in range(n)]\n'
                  'DATA = build(300_000)\n')
        with tempfile.TemporaryDirectory() as tmp:
            _, report = self.run_script(Path(tmp), source, memory='precise')
            self.assertGreater(report['tracemalloc_peak_mb'], 20, 'bookkeeping is measured')
            self.assertEqual(report['native_untraced_mb'], 0.0,
                             'no C extension: nothing beyond tracing overhead is reported as native')
            line = report['files'][next(iter(report['files']))]['2']
            self.assertGreater(line['profiler_mb'], 0, 'per-line bookkeeping growth is reported')

    def test_timeline_points_carry_the_main_thread_stack(self):
        source = ('import time\n'
                  'def phase_one():\n'
                  '    time.sleep(0.4)\n'
                  'def phase_two():\n'
                  '    time.sleep(0.4)\n'
                  'phase_one()\n'
                  'phase_two()\n')
        with tempfile.TemporaryDirectory() as tmp:
            _, report = self.run_script(Path(tmp), source, memory='fast')
            self.assertEqual(len(report['timeline_stacks']), len(report['timeline']))
            frames, samples = report['stacks']['frames'], report['stacks']['samples']
            names = [frames[samples[i]['frames'][-1]]['name'] if i >= 0 else None for i in report['timeline_stacks']]
            seen = [n for i, n in enumerate(names) if n and (i == 0 or names[i - 1] != n)]
            self.assertEqual(seen[:2], ['phase_one', 'phase_two'], 'phases appear in time order')
            self.assertIsNone(names[-1], 'the exit point has no stack')

    @unittest.skipUnless(hasattr(sys, 'monitoring'), 'needs Python 3.12+ sys.monitoring')
    def test_trace_function_traces_only_that_function(self):
        source = ('KEEP = []\n'
                  'def outside():\n'
                  '    return [bytearray(1000) for _ in range(8000)]\n'
                  'def traced(n):\n'
                  '    data = [bytearray(1000) for _ in range(n)]\n'
                  '    KEEP.append(len(data))\n'
                  '    return len(data)\n'
                  'def fails():\n'
                  '    x = [bytearray(1000) for _ in range(3000)]\n'
                  '    raise ValueError(len(x))\n'
                  'def rec(k):\n'
                  '    return 0 if k == 0 else rec(k - 1)\n'
                  'for _ in range(3):\n'
                  '    outside()\n'
                  '    traced(20000)\n'
                  'try:\n'
                  '    fails()\n'
                  'except ValueError:\n'
                  '    pass\n'
                  'outside()\n'
                  'rec(5)\n')
        def run(name):
            with tempfile.TemporaryDirectory() as tmp:
                script = Path(tmp) / 'main.py'
                script.write_text(source)
                out = Path(tmp) / 'profile.json'
                r = subprocess.run([sys.executable, str(PROFILER), '--memory', 'precise', '--trace-function', name,
                                    '--root', tmp, '--out', str(out), str(script)], capture_output=True, text=True, timeout=60)
                self.assertEqual(r.returncode, 0, r.stderr)
                return json.loads(out.read_text())
        report = run('traced')
        self.assertEqual(report['trace_function']['calls'], 3, 'one traced window per outermost call')
        lines = report['files'][next(iter(report['files']))]
        self.assertGreater(report['peak_traced_mb'], 15, 'the traced function\'s ~20 MB list was seen')
        self.assertEqual(lines.get('3', {}).get('alloc_mb', 0), 0, 'outside() was never traced')
        self.assertIsNone(report['memory_tracing_lost_s'], 'tracing off between calls is not "lost"')
        self.assertFalse(any(e.get('leak_runs') for e in lines.values()), 'no leak claims across separate calls')
        self.assertIsNotNone(report['memory_stacks'], 'snapshots at the end of each traced call')
        # An exception ends the traced call, and tracing stops: the outside() call after it is not traced.
        report = run('fails')
        self.assertEqual(report['trace_function']['calls'], 1)
        lines = report['files'][next(iter(report['files']))]
        self.assertEqual(lines.get('3', {}).get('alloc_mb', 0), 0, 'tracing stopped when fails() raised')
        # Recursion: one window for the outermost call.
        self.assertEqual(run('rec')['trace_function']['calls'], 1)
        self.assertEqual(run('missing')['trace_function']['calls'], 0, 'a name never called is reported as 0 calls')

    @unittest.skipUnless(hasattr(sys, 'monitoring'), 'needs Python 3.12+ sys.monitoring')
    def test_trace_function_reports_no_native_estimate_and_counts_open_calls(self):
        # Python memory outside the traced function is untraced, so RSS minus traced memory would call
        # it native (measured: 219 MB of plain str). And a traced call still running in a daemon thread
        # at exit is counted up to exit.
        source = ('import threading, time\n'
                  'def outside(n):\n'
                  '    return [str(i) * 3 for i in range(n)]\n'
                  'def traced(seconds):\n'
                  '    time.sleep(seconds)\n'
                  'KEEP = outside(500_000)\n'
                  'threading.Thread(target=traced, args=(5,), daemon=True).start()\n'
                  'time.sleep(0.6)\n')
        with tempfile.TemporaryDirectory() as tmp:
            script = Path(tmp) / 'main.py'
            script.write_text(source)
            out = Path(tmp) / 'profile.json'
            r = subprocess.run([sys.executable, str(PROFILER), '--memory', 'precise', '--trace-function', 'traced',
                                '--root', tmp, '--out', str(out), str(script)], capture_output=True, text=True, timeout=60)
            self.assertEqual(r.returncode, 0, r.stderr)
            report = json.loads(out.read_text())
            self.assertIsNone(report['native_untraced_mb'], 'no native estimate when only one function is traced')
            self.assertEqual(report['trace_function']['calls'], 1)
            self.assertGreater(report['trace_function']['traced_s'], 0.3, 'the call still open at exit is counted')

    def test_trace_function_needs_precise_mode(self):
        r = subprocess.run([sys.executable, str(PROFILER), '--memory', 'fast', '--trace-function', 'x', 'main.py'],
                           capture_output=True, text=True, timeout=30)
        self.assertEqual(r.returncode, 2)
        self.assertIn('--trace-function needs --memory precise', r.stderr)

    def test_routine_snapshots_stay_within_budget_by_measured_cost(self):
        # Each snapshot really costs 0.3 s on 10 MB of bookkeeping (30 ns/byte), above the clamped 4-20 ns
        # prediction. The cap (10% of elapsed, at least 0.5 s) must hold against what was measured.
        with tempfile.TemporaryDirectory() as tmp:
            p = profiler.Profiler(tmp, .01, 'precise')
            p.rss, p.rss_kind = lambda: 0, None
            clock, sleeps = [0.0], []

            def sleep(seconds):
                clock[0] += seconds
                sleeps.append(True)
                if len(sleeps) == 150:
                    p._stop.set()

            def snapshot(_t):
                clock[0] += .3
            with patch.object(profiler.time, 'sleep', side_effect=sleep), \
                    patch.object(profiler.time, 'perf_counter', side_effect=lambda: clock[0]), \
                    patch.object(profiler.time, 'process_time', return_value=0.0), \
                    patch.object(profiler.sys, '_current_frames', return_value={}), \
                    patch.object(profiler.tracemalloc, 'get_traced_memory', return_value=(2_000_000, 2_000_000)), \
                    patch.object(profiler.tracemalloc, 'get_tracemalloc_memory', return_value=10_000_000), \
                    patch.object(profiler.tracemalloc, 'reset_peak'), \
                    patch.object(profiler.tracemalloc, 'is_tracing', return_value=True), \
                    patch.object(p, '_snapshot', side_effect=snapshot):
                p._run()
            self.assertIsNone(p.sampler_error)
            elapsed = clock[0]
            self.assertLessEqual(p.snap_cost, max(0.10 * elapsed, 0.5) + 1e-9,
                                 f'{p.snap_cost:.2f} s of snapshots in {elapsed:.2f} s exceeds the routine cap')
            self.assertGreaterEqual(p.snap_cost, .3, 'the first snapshot is still taken')

    def test_stack_samples_keep_thread_names(self):
        source = ('import threading, time\n'
                  'def busy_worker():\n'
                  '    t = time.perf_counter()\n'
                  '    while time.perf_counter() - t < 0.3:\n'
                  '        pass\n'
                  'w = threading.Thread(target=busy_worker, name="ingest-worker")\n'
                  'w.start()\n'
                  'w.join()\n')
        with tempfile.TemporaryDirectory() as tmp:
            _, report = self.run_script(Path(tmp), source)
            names = {s['thread_name'] for s in report['stacks']['samples']}
            self.assertIn('ingest-worker', names)
            self.assertLessEqual(names, {'MainThread', 'ingest-worker'}, 'thread names, not function names')

    def test_memory_stacks_attribute_bytes_to_call_paths(self):
        source = ('import time\n'
                  'def leaf(n):\n'
                  '    return [bytearray(1000) for _ in range(n)]\n'
                  'class Svc:\n'
                  '    def handle(self):\n'
                  '        return leaf(20_000)\n'
                  'keep = Svc().handle()\n'
                  'time.sleep(0.6)\n')
        with tempfile.TemporaryDirectory() as tmp:
            _, report = self.run_script(Path(tmp), source, memory='precise', frames='4')
        ms = report['memory_stacks']
        self.assertEqual(ms['depth'], 4)
        for name in ('peak', 'exit'):
            table = ms[name]
            self.assertEqual(sum(s['bytes'] for s in table['stacks']) + table['other_bytes'], table['total_bytes'],
                             f'{name}: kept stacks plus other add up to the total')
        self.assertLessEqual(ms['peak']['t'], ms['exit']['t'])
        top = max(ms['exit']['stacks'], key=lambda s: s['bytes'])
        names = [ms['frames'][i]['name'] for i in top['frames']]
        expected = ['<module>', 'Svc.handle', 'leaf'] + (['leaf.<listcomp>'] if sys.version_info < (3, 12) else [])
        self.assertEqual(names, expected, 'outermost first, named like the code objects the sampler records')
        self.assertGreater(top['bytes'], 20_000_000)
        handle = next(ms['frames'][i] for i in top['frames'] if ms['frames'][i]['name'] == 'Svc.handle')
        self.assertEqual((handle['first_line'], handle['user']), (5, True), 'first line of the def, as co_firstlineno')

    def test_memory_frame_names_match_code_objects(self):
        source = ('import functools\n'                 # 1
                  'def outer():\n'                    # 2
                  '    def inner():\n'                # 3
                  '        return 1\n'                # 4
                  '    return inner\n'                # 5
                  'class Svc:\n'                      # 6
                  '    LIMIT = 3\n'                   # 7
                  '    @functools.cache\n'            # 8
                  '    def run(self):\n'              # 9
                  '        f = lambda x: x\n'         # 10
                  '        return f\n'                # 11
                  'X = 1\n')                          # 12
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'mod.py'
            path.write_text(source)
            p = profiler.Profiler(tmp, .01, 'precise')
            got = {line: p._function_at(str(path), line) for line in (4, 5, 7, 9, 10, 12)}
            self.assertEqual(got, {4: ('outer.inner', 3), 5: ('outer', 2), 7: ('Svc', 6),
                                   9: ('Svc.run', 8), 10: ('Svc.run.<lambda>', 10), 12: ('<module>', 1)})
            self.assertEqual(p._function_at('<frozen importlib._bootstrap>', 5), ('', 5), 'no source: no name')
            self.assertEqual(p._function_at(str(path), 10, enclosing=True), ('Svc.run', 8),
                             'the caller of a lambda on the same line is the enclosing function')

    def run_scripted_memory(self, levels_mb, book_bytes):
        """Drive the sampler with scripted traced-memory levels; return traced MB at each snapshot."""
        with tempfile.TemporaryDirectory() as tmp:
            p = profiler.Profiler(tmp, .01, 'precise')
            p.rss, p.rss_kind = lambda: 0, None
            levels = iter([(mb * 1_000_000, mb * 1_000_000) for mb in levels_mb])
            current, taken, sleeps = [0], [], []

            def traced():
                current[0] = next(levels)
                return current[0]

            def sleep(_):
                sleeps.append(True)
                if len(sleeps) == len(levels_mb):
                    p._stop.set()

            with patch.object(profiler.time, 'sleep', side_effect=sleep), \
                    patch.object(profiler.sys, '_current_frames', return_value={}), \
                    patch.object(profiler.tracemalloc, 'get_traced_memory', side_effect=traced), \
                    patch.object(profiler.tracemalloc, 'reset_peak'), \
                    patch.object(profiler.tracemalloc, 'is_tracing', return_value=True), \
                    patch.object(profiler.tracemalloc, 'get_tracemalloc_memory', return_value=book_bytes), \
                    patch.object(p, '_snapshot', side_effect=lambda _t: taken.append(current[0][0] / 1e6)):
                p._run()
            self.assertIsNone(p.sampler_error)
            return taken, p

    def test_peak_capture_snapshots_on_doubling_and_plateau(self):
        # Gradual growth (no routine spike), a plateau at 17 MB for six samples, then release.
        taken, p = self.run_scripted_memory([1, 5, 9, 13, 17, 17, 17, 17, 17, 17, 1], book_bytes=1_000_000)
        self.assertEqual(taken, [9, 17], 'doubling at 9 MB, then the plateau at 17 MB, and nothing else')
        self.assertEqual(p.peak_snapshots, 2)

    def test_peak_capture_respects_its_budget(self):
        taken, p = self.run_scripted_memory([1, 5, 9, 13, 17, 17, 17, 17, 17, 17, 1], book_bytes=10 ** 12)
        self.assertEqual((taken, p.peak_snapshots), ([], 0), 'a predicted cost far over budget takes no snapshot')

    def test_largest_objects_at_exit_by_holder(self):
        source = ('BIG = bytearray(20_000_000)\n'
                  'CACHE = {i: bytearray(100_000) for i in range(100)}\n'
                  'small = [1, 2, 3]\n'
                  'class Svc:\n'
                  '    def __init__(self):\n'
                  '        self.history = [bytearray(50_000) for _ in range(200)]\n'
                  'class Opaque:\n'                      # stands in for a library that does not report its memory
                  '    def __init__(self):\n'
                  '        self._buffer = None\n'
                  '    def __sizeof__(self):\n'
                  '        return 64\n'
                  'svc = Svc()\n'
                  'opaque = Opaque()\n')
        with tempfile.TemporaryDirectory() as tmp:
            _, report = self.run_script(Path(tmp), source, memory='fast')
        largest = report['largest_objects']
        self.assertTrue(largest['complete'])
        by = {o['holder']: o for o in largest['objects']}
        self.assertEqual(by['global BIG']['type'], 'bytearray')
        self.assertAlmostEqual(by['global BIG']['mb'], 20, delta=1)
        self.assertEqual((by['global CACHE']['type'], by['global CACHE']['items']), ('dict', 100))
        self.assertAlmostEqual(by['global CACHE']['mb'], 10, delta=1, msg='a container counts its items one level deep')
        self.assertAlmostEqual(by['Svc.history']['mb'], 10, delta=1, msg='attributes of your own class instances')
        self.assertNotIn('global small', by, 'under 1 MB is not listed')
        self.assertNotIn('global opaque', by, 'an object reporting a tiny size is not claimed to be large')
        self.assertEqual([o['holder'] for o in largest['objects']][:1], ['global BIG'], 'largest first')

    def test_container_sizes_are_extrapolated_past_the_sample(self):
        size, items, estimated = profiler.Profiler._sized([bytearray(1000) for _ in range(25_000)])
        self.assertEqual((items, estimated), (25_000, True))
        self.assertAlmostEqual(size / 25_000, 1000 + 57 + 8, delta=40)

    def test_sampler_failure_is_reported(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = profiler.Profiler(tmp, .01, 'off')
            with patch.object(p, '_sample_loop', side_effect=ValueError('boom')):
                p._run()
            self.assertEqual(p.sampler_error, 'ValueError: boom')

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

    def test_monitoring_disables_library_lines_and_counts_user_lines(self):
        mon = getattr(sys, 'monitoring', None)
        if mon is None:
            self.skipTest('sys.monitoring requires Python 3.12+')
        with tempfile.TemporaryDirectory() as tmp:
            p = profiler.Profiler(tmp, .01, 'off', monitoring='lines')
            p._monitoring_disable = mon.DISABLE
            self.assertIs(p._on_line_event(json.dumps.__code__, 1), mon.DISABLE,
                          'stdlib lines switch themselves off after one event')
            user = str(Path(tmp) / 'work.py')
            code = compile('x = 1\n', user, 'exec')
            for _ in range(3):
                self.assertIsNone(p._on_line_event(code, 1), 'user lines keep reporting')
            self.assertEqual(p.line_events, {(os.path.abspath(user), 1): 3})

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
                    patch.object(profiler.tracemalloc, 'is_tracing', return_value=True), \
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
                        patch.object(p, '_walk', return_value=(loc, ('work', 1), ())):
                    p._run()
                line = p.lines[loc]
                self.assertAlmostEqual(getattr(line, expected + '_s'), .01)
                other = 'python_s' if expected == 'native' else 'native_s'
                self.assertEqual(getattr(line, other), 0)


if __name__ == '__main__':
    unittest.main()
