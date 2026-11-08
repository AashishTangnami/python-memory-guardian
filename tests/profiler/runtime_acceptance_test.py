"""Witness-first regressions for the memory acceptance audit.

Each subprocess first runs independently; the instrumented run must preserve its
observable result. Unsupported platform observations are incomplete, never green.
"""
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import sysconfig
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
PROFILER = ROOT / 'src' / 'python' / 'pmg_profile.py'
FREE_THREADED = bool(sysconfig.get_config_var('Py_GIL_DISABLED'))


class RuntimeAcceptance(unittest.TestCase):
    def test_late_worker_main_namespace_and_pickle_match_baseline(self):
        source = '''import json, pickle, sys, threading, time
class Payload: pass
def worker():
    time.sleep(.1)
    result = {'module_is_target': getattr(sys.modules['__main__'], 'Payload', None) is Payload}
    try:
        pickle.dumps(Payload())
        result['pickled'] = True
    except Exception:
        result['pickled'] = False
    print(json.dumps(result))
thread = threading.Thread(target=worker)
thread.start()
if '--join' in sys.argv:
    thread.join()
'''
        for mode in ('off', 'fast', 'precise'):
            for arguments in ((), ('--join',)):
                with self.subTest(mode=mode, arguments=arguments):
                    truth, _ = self.witness_then_profile(source, mode, arguments)
                    self.assertEqual(truth, {'module_is_target': True, 'pickled': True})

    def witness_then_profile(self, source, mode='precise', arguments=()):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            fixture = directory / 'fixture.py'
            fixture.write_text(source)
            baseline_command = [sys.executable, str(fixture), *arguments]
            baseline = subprocess.run(baseline_command, capture_output=True, text=True, timeout=30)
            self.assertEqual(baseline.returncode, 0, baseline.stderr)
            truth = json.loads(baseline.stdout.splitlines()[-1])
            output = directory / 'profile.json'
            command = [sys.executable, str(PROFILER), '--memory', mode, '--root', temporary,
                       '--interval', '.005', '--out', str(output), str(fixture), *arguments]
            measured = subprocess.run(command, capture_output=True, text=True, timeout=30)
            self.assertEqual(measured.returncode, 0, measured.stderr)
            self.assertEqual([json.loads(line) for line in measured.stdout.splitlines()],
                             [json.loads(line) for line in baseline.stdout.splitlines()],
                             f'instrumentation changed the witness: {command}')
            profile = json.loads(output.read_text())
            self.assertIsNone(profile['sampler_error'])
            return truth, profile

    def test_finalizer_frames_do_not_resurrect_collectible_cycles(self):
        source = (ROOT / 'tests/fixtures/acceptance/cycle_finalizer.py').read_text()
        for mode in ('off', 'fast', 'precise'):
            with self.subTest(mode=mode):
                truth, profile = self.witness_then_profile(source, mode)
                self.assertEqual(truth['survivors'], 0)
                # The first cleanup point must also match, not just the second collection.
                self.assertGreaterEqual(profile['gc']['collected'], 6)
        truth, _ = self.witness_then_profile(source, arguments=('--break-links',))
        self.assertEqual(truth['survivors'], 0)

    def test_plateau_removes_warmup_growth_candidate(self):
        source = '''import collections, json, sys, time
bounded = '--bounded' in sys.argv
history = collections.deque(maxlen=4) if bounded else []
observed = []
for operation in range(16):
    history.append(bytearray(1250000))
    observed.append(sum(len(x) for x in history))
    time.sleep(.14)
print(json.dumps({'bytes': observed, 'retained': len(history)}))
'''
        truth, growing = self.witness_then_profile(source)
        self.assertEqual(truth['bytes'][-1], 20_000_000)
        self.assertTrue(any(e.get('leak_runs') for f in growing['files'].values() for e in f.values()))
        fixed_truth, fixed = self.witness_then_profile(source, arguments=('--bounded',))
        self.assertEqual(fixed_truth['bytes'][-8:], [5_000_000] * 8)
        self.assertFalse(any(e.get('leak_runs') for f in fixed['files'].values() for e in f.values()))

    def test_worker_growth_is_not_attributed_to_waiting_main_thread(self):
        truth, profile = self.witness_then_profile('''import json, threading, time
held = []
def allocate():
    time.sleep(.06)
    held.append(bytearray(8000000))
    time.sleep(.12)
worker = threading.Thread(target=allocate)
worker.start()
while worker.is_alive():
    time.sleep(.01)
worker.join()
print(json.dumps({'payload_bytes': sum(len(x) for x in held)}))
''')
        self.assertEqual(truth['payload_bytes'], 8_000_000)
        lines = next(iter(profile['files'].values()))
        self.assertGreater(lines['5']['end_mb'], 7.9, 'the actual allocation traceback is preserved')
        self.assertFalse(any('alloc_mb' in e for e in lines.values()))
        self.assertFalse(any(s.get('alloc_bytes') for s in profile['stacks']['samples']))
        self.assertGreater(max(p['traced_bytes'] or 0 for p in profile['memory_observations']), 8_000_000)

    def test_lost_tracing_has_no_exit_snapshot_or_native_measurement(self):
        _, profile = self.witness_then_profile('''import json, time, tracemalloc
before = bytearray(3000000)
time.sleep(.32)
tracemalloc.stop()
after = bytearray(7000000)
time.sleep(.12)
print(json.dumps({'payload_bytes': len(before) + len(after)}))
''')
        self.assertIsNotNone(profile['memory_tracing_lost_s'])
        self.assertFalse(profile['memory_snapshot_at_exit'])
        self.assertIsNone(profile['native_untraced_mb'])
        self.assertFalse(any(e.get('leak_runs') for f in profile['files'].values() for e in f.values()))

    def test_gc_records_collection_objects_and_pause_without_leak_claim(self):
        source = '''import gc, json, sys, time
gc.collect(); gc.disable()
cycles = []
for _ in range(200):
    cycle = []
    cycle.append(cycle)
    cycles.append(cycle)
del cycle
if '--fixed' in sys.argv:
    for cycle in cycles:
        cycle.clear()
    del cycle
cycles.clear()
collected = gc.collect()
time.sleep(.12)
print(json.dumps({'collected': collected}))
'''
        truth, profile = self.witness_then_profile(source)
        self.assertGreaterEqual(truth['collected'], 200)
        self.assertGreaterEqual(profile['gc']['collected'], 200)
        self.assertGreater(profile['gc']['total_pause_s'], 0)
        self.assertGreater(profile['gc']['total_collections'], 0)
        self.assertEqual(profile['gc']['uncollectable'], 0)
        fixed_truth, _ = self.witness_then_profile(source, arguments=('--fixed',))
        self.assertEqual(fixed_truth['collected'], 0)

    def test_nested_owner_paths_and_alias_size_limits_are_explicit(self):
        truth, profile = self.witness_then_profile('''import json, time
class Owner: pass
owner = Owner()
owner.state = {'storage': {'history': []}}
for _ in range(12):
    owner.state['storage']['history'].append(bytearray(1250000))
    time.sleep(.14)
print(json.dumps({'payload_bytes': sum(map(len, owner.state['storage']['history']))}))
''')
        self.assertEqual(truth['payload_bytes'], 15_000_000)
        path = "global owner.state['storage']['history']"
        holders = [h['holder'] for f in profile['files'].values() for e in f.values()
                   for h in e.get('held_by', [])]
        self.assertIn(path, holders)
        self.assertIn(path, [o['holder'] for o in profile['largest_objects']['objects']])
        self.assertIn('overlapping', profile['largest_objects']['sizing'])

    def test_real_uncollectable_cycles_are_reported(self):
        if importlib.util.find_spec('_testcapi') is None:
            self.skipTest('incomplete: CPython _testcapi extension unavailable')
        import _testcapi
        if not hasattr(_testcapi, 'with_tp_del'):
            self.skipTest('incomplete: legacy tp_del fixture unavailable')
        source = '''import _testcapi, gc, json, sys, time
@_testcapi.with_tp_del
class Legacy:
    def __del__(self): pass
gc.collect(); gc.disable()
owned = [Legacy() for _ in range(6)]
for value in owned:
    value.link = value
if '--fixed' in sys.argv:
    for value in owned:
        value.link = None
del value
owned.clear()
gc.collect()
print(json.dumps({'uncollectable': sum(type(v) is Legacy for v in gc.garbage), 'debug': gc.get_debug()}))
time.sleep(.1)
'''
        truth, profile = self.witness_then_profile(source)
        self.assertEqual(truth, {'uncollectable': 6, 'debug': 0})
        self.assertGreaterEqual(profile['gc']['uncollectable'], 6)
        fixed_truth, fixed = self.witness_then_profile(source, arguments=('--fixed',))
        self.assertEqual(fixed_truth['uncollectable'], 0)
        self.assertEqual(fixed['gc']['uncollectable'], 0)

    def test_gc_pressure_is_measured_without_a_leak_diagnosis(self):
        source = '''import gc, json, sys
gc.collect()
observed = []
def witness(phase, info):
    if phase == 'stop': observed.append(info['collected'])
gc.callbacks.append(witness)
gc.set_threshold(100, 5, 5)
if '--fixed' in sys.argv:
    for _ in range(3000):
        value = []; value.append(value); value.clear()
else:
    for _ in range(3000):
        value = []; value.append(value)
del value
gc.collect()
gc.callbacks.remove(witness)
print(json.dumps({'collections': len(observed), 'collected': sum(observed)}))
'''
        truth, measured = self.witness_then_profile(source)
        fixed_truth, fixed = self.witness_then_profile(source, arguments=('--fixed',))
        # The free-threaded collector is not generational and runs far less often (2 runs against 1 on
        # 3.13.3t, 18 against 1 with the GIL), so there the pressure shows in the cycles collected.
        factor = 1 if FREE_THREADED else 3
        self.assertGreater(truth['collections'], fixed_truth['collections'] * factor)
        self.assertGreater(measured['gc']['total_collections'], fixed['gc']['total_collections'] * factor)
        self.assertGreater(measured['gc']['collected'], 2500)
        self.assertFalse(any(e.get('leak_runs') for f in measured['files'].values() for e in f.values()))


if __name__ == '__main__':
    result = unittest.TextTestRunner().run(unittest.defaultTestLoader.loadTestsFromTestCase(RuntimeAcceptance))
    if result.skipped:
        print('INCOMPLETE: unsupported or skipped cases:', result.skipped)
    sys.exit(0 if result.wasSuccessful() and not result.skipped else 2 if result.skipped else 1)
