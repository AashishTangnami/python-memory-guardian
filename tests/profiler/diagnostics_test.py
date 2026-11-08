"""Independent red/green checks for cooperative evidence (stdlib only).

Each test first runs the problematic fixture without PMG, verifies the real
behavior, then verifies PMG evidence, and repeats the corrected fixture.
"""
import asyncio
from collections import deque
import gc
import json
import multiprocessing
import os
from pathlib import Path
import sys
import subprocess
import tempfile
import threading
import unittest
import weakref

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src' / 'python'))
import pmg_diagnostics as diagnostics


class Owner:
    pass


class SlotOwner:
    __slots__ = ('child', '__weakref__')


class TraceFileSafety(unittest.TestCase):
    def test_trace_replaces_a_planted_link_and_an_old_trace(self):
        with tempfile.TemporaryDirectory() as tmp:
            victim = Path(tmp) / 'victim.txt'
            victim.write_text('USER DATA')
            trace = Path(tmp) / '.pmg' / 'trace.events.jsonl'
            trace.parent.mkdir()
            trace.symlink_to(victim)
            recorder = diagnostics.Recorder(trace_path=str(trace))
            recorder.event('workload', {'phase': 'start'})
            recorder.close()
            self.assertEqual(victim.read_text(), 'USER DATA', 'the link target is not written')
            self.assertFalse(trace.is_symlink())
            self.assertIn('workload', trace.read_text())
            recorder = diagnostics.Recorder(trace_path=str(trace))   # an existing trace is still replaced
            recorder.close()
            self.assertNotIn('workload', trace.read_text())


class OwnerGraphContracts(unittest.TestCase):
    def test_slot_owner_cleanup_red_green(self):
        owner, child = SlotOwner(), Owner()
        owner.child = child
        reference = weakref.ref(child)
        del child
        self.assertIsNotNone(reference(), 'independent retained witness')
        graph = diagnostics.inspect_roots({'owner': owner}, [id(reference())])
        self.assertEqual(graph['reference_paths'][str(id(reference()))], 'owner.child')
        positive = diagnostics.Recorder(workload={'command': [sys.executable, __file__]})
        positive.watch(reference(), 'child')
        positive.operation_cleanup('cleanup', {'owner': owner}, expected_released=['child'])
        finding = next(f for f in positive.close()['findings'] if f['category'] == 'retained_reference')
        self.assertEqual(finding['evidence']['owner_reference_path'], 'owner.child')
        del owner.child
        gc.collect()
        self.assertIsNone(reference(), 'corrected cleanup releases child')
        self.assertFalse(diagnostics.inspect_roots({'owner': owner})['reference_paths'])
        corrected = diagnostics.Recorder(workload={'command': [sys.executable, __file__]})
        owner.child = Owner()
        corrected.watch(owner.child, 'child')
        del owner.child
        gc.collect()
        corrected.operation_cleanup('cleanup', {'owner': owner}, expected_released=['child'])
        self.assertFalse(any(f['category'] == 'retained_reference' for f in corrected.close()['findings']))

    def test_properties_are_not_executed(self):
        class HasProperty:
            @property
            def __dict__(self):
                raise AssertionError('observer invoked namespace property')
            @property
            def dangerous(self):
                raise AssertionError('observer invoked property')
        diagnostics.inspect_roots({'owner': HasProperty()})

    def test_bounded_graph_reports_incomplete(self):
        graph = diagnostics.inspect_roots({'wide': [Owner() for _ in range(diagnostics.MAX_NODES + 10)]})
        self.assertFalse(graph['complete'])
        self.assertLessEqual(graph['object_count'], diagnostics.MAX_NODES)

    def test_key_repr_and_size_overrides_are_not_called(self):
        class Key(str):
            def __repr__(self):
                raise AssertionError('observer called user repr')
        class Sized:
            __module__ = 'builtins'
            def __sizeof__(self):
                raise AssertionError('observer called user sizeof')
        graph = diagnostics.inspect_roots({'data': {Key('key'): Sized(), 1 << 20000: None}})
        self.assertTrue(graph['complete'])


def thread_counter(fixed=False, annotate=False, partial_lock=False):
    shared = {'counter': 0}
    barrier = threading.Barrier(2)
    lock = threading.Lock()

    def operation():
        for _ in range(20):
            if fixed:
                context = diagnostics.critical_section(lock, 'counter')
                with context:
                    value = shared['counter']
                    if annotate:
                        diagnostics.shared_read('counter', value)
                    shared['counter'] = value + 1
                    if annotate:
                        diagnostics.shared_write('counter', shared['counter'])
            else:
                if partial_lock:
                    with diagnostics.critical_section(lock, 'counter'):
                        value = shared['counter']
                        if annotate:
                            diagnostics.shared_read('counter', value)
                else:
                    value = shared['counter']
                    if annotate:
                        diagnostics.shared_read('counter', value)
                barrier.wait()
                if partial_lock:
                    with diagnostics.critical_section(lock, 'counter'):
                        shared['counter'] = value + 1
                        if annotate:
                            diagnostics.shared_write('counter', shared['counter'])
                else:
                    shared['counter'] = value + 1
                    if annotate:
                        diagnostics.shared_write('counter', shared['counter'])
                barrier.wait()

    workers = [threading.Thread(target=operation) for _ in range(2)]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(timeout=10)
        if worker.is_alive():
            raise AssertionError('fixture thread did not finish')
    if annotate:
        diagnostics.shared_outcome('counter', shared['counter'], 40)
    return shared['counter']


class AsyncBarrier:
    """Reusable rendezvous for asyncio tasks. asyncio.Barrier needs Python 3.11; this suite runs on 3.9."""

    def __init__(self, parties):
        self.parties, self.arrived, self.generation = parties, 0, 0
        self.condition = asyncio.Condition()

    async def wait(self):
        async with self.condition:
            generation = self.generation
            self.arrived += 1
            if self.arrived == self.parties:
                self.arrived, self.generation = 0, generation + 1
                self.condition.notify_all()
            else:
                await self.condition.wait_for(lambda: self.generation != generation)


async def async_counter(fixed=False, annotate=False):
    shared = {'counter': 0}
    lock = asyncio.Lock()
    barrier = AsyncBarrier(2)

    async def operation():
        for _ in range(20):
            if fixed:
                async with diagnostics.async_critical_section(lock, 'counter'):
                    value = shared['counter']
                    if annotate:
                        diagnostics.shared_read('counter', value)
                    shared['counter'] = value + 1
                    if annotate:
                        diagnostics.shared_write('counter', shared['counter'])
            else:
                value = shared['counter']
                if annotate:
                    diagnostics.shared_read('counter', value)
                await barrier.wait()
                shared['counter'] = value + 1
                if annotate:
                    diagnostics.shared_write('counter', shared['counter'])
                await barrier.wait()

    await asyncio.gather(*(operation() for _ in range(2)))
    if annotate:
        diagnostics.shared_outcome('counter', shared['counter'], 40)
    return shared['counter']


def process_worker(value, barrier, lock, fixed, annotate, trace_path):
    if annotate:
        diagnostics.install(trace_path, {'script': __file__, 'command': [sys.executable, __file__]})
    with diagnostics.actor('process', str(__import__('os').getpid())):
        for _ in range(20):
            if fixed:
                with diagnostics.critical_section(lock, 'counter'):
                    before = value.value
                    if annotate:
                        diagnostics.shared_read('counter', before)
                    value.value = before + 1
                    if annotate:
                        diagnostics.shared_write('counter', value.value)
            else:
                before = value.value
                if annotate:
                    diagnostics.shared_read('counter', before)
                barrier.wait(timeout=10)
                value.value = before + 1
                if annotate:
                    diagnostics.shared_write('counter', value.value)
                barrier.wait(timeout=10)
    if annotate:
        diagnostics.uninstall()


def process_counter(directory, fixed=False, annotate=False):
    context = multiprocessing.get_context('spawn')
    value = context.Value('i', 0, lock=False)
    barrier, lock = context.Barrier(2), context.Lock()
    paths = [str(directory / ('worker-' + str(n) + '.jsonl')) for n in range(2)]
    workers = [context.Process(target=process_worker, args=(value, barrier, lock, fixed, annotate, path)) for path in paths]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(timeout=20)
        if worker.is_alive():
            worker.terminate()
            worker.join()
            raise AssertionError('fixture process did not finish')
        if worker.exitcode != 0:
            raise AssertionError('fixture process failed: ' + str(worker.exitcode))
    if annotate:
        final_path = str(directory / 'outcome.jsonl')
        diagnostics.install(final_path)
        diagnostics.shared_outcome('counter', value.value, 40)
        diagnostics.uninstall()
        paths.append(final_path)
        return value.value, diagnostics.merge_traces(paths)
    return value.value, None


class EvidenceFixtures(unittest.TestCase):
    def tearDown(self):
        diagnostics.uninstall()

    def start(self, directory=None, max_events=20000):
        return diagnostics.install(str(directory / 'events.jsonl') if directory else None,
                                   {'script': __file__, 'command': [sys.executable, __file__], 'case': self.id()},
                                   max_events)

    def categories(self, summary):
        return {f['category'] for f in summary['findings']}

    def test_growth_and_budget_red_green(self):
        # Baseline: growing history versus a bounded history at two input sizes.
        baseline = [bytearray(100000) for _ in range(2)]
        before = sum(sys.getsizeof(v) for v in baseline)
        baseline.extend(bytearray(100000) for _ in range(2))
        self.assertGreater(sum(sys.getsizeof(v) for v in baseline), before * 1.9)
        self.start()
        history = [bytearray(100000) for _ in range(2)]
        diagnostics.checkpoint('history', {'operations': 2}, {'history': history})
        history.extend(bytearray(100000) for _ in range(2))
        diagnostics.checkpoint('history', {'operations': 4}, {'history': history}, memory_budget_bytes=300000)
        report = diagnostics.uninstall()
        self.assertEqual(self.categories(report), {'memory_growth', 'oom_risk'})
        growth = next(f for f in report['findings'] if f['category'] == 'memory_growth')
        self.assertEqual(len(growth['evidence']['event_ids']), 2)
        self.assertEqual(growth['evidence']['workload_condition']['after']['operations'], 4)
        risk = next(f for f in report['findings'] if f['category'] == 'oom_risk')
        self.assertEqual(risk['status'], 'projected')
        self.assertFalse(risk['evidence']['oom_observed'])
        fixed = deque((bytearray(100000) for _ in range(2)), maxlen=2)
        fixed_before = sum(sys.getsizeof(v) for v in fixed)
        fixed.extend(bytearray(100000) for _ in range(2))
        self.assertEqual(sum(sys.getsizeof(v) for v in fixed), fixed_before)
        self.start()
        diagnostics.checkpoint('history', {'operations': 2}, {'history': fixed})
        fixed.extend(bytearray(100000) for _ in range(2))
        diagnostics.checkpoint('history', {'operations': 4}, {'history': fixed}, memory_budget_bytes=300000)
        self.assertFalse(diagnostics.uninstall()['findings'])

    def test_requested_allocation_churn_red_green(self):
        baseline_requested = sum(len(bytearray(4096)) for _ in range(100))
        self.assertEqual(baseline_requested, 409600)
        self.start()
        for _ in range(100):
            diagnostics.allocate(4096, label='scratch')
        report = diagnostics.uninstall()
        self.assertEqual(report['explicit_allocation_totals']['scratch']['requested_payload_bytes'], baseline_requested)
        self.assertEqual(report['explicit_allocation_totals']['scratch']['requests'], 100)
        # A reused buffer makes one factory request; no allocator volume claim.
        baseline_fixed = bytearray(4096)
        self.assertEqual(sum(len(baseline_fixed) for _ in range(100)), baseline_requested)
        self.start()
        fixed = diagnostics.allocate(4096, label='scratch')
        for _ in range(100):
            self.assertEqual(len(fixed), 4096)
        fixed_report = diagnostics.uninstall()
        self.assertEqual(fixed_report['explicit_allocation_totals']['scratch']['requests'], 1)
        self.assertEqual(fixed_report['capabilities']['native_allocation_events']['status'], 'unavailable')

    def test_repeated_copy_red_green(self):
        original = bytearray(100000)
        first, second = bytearray(original), bytearray(original)
        self.assertIsNot(first, second)
        self.assertEqual(first, second)
        self.start()
        diagnostics.copy_value(original, bytearray, label='snapshot')
        diagnostics.copy_value(original, bytearray, label='snapshot')
        report = diagnostics.uninstall()
        self.assertIn('repeated_copy', self.categories(report))
        self.assertEqual(report['findings'][0]['evidence']['alternatives'], [])
        fixed = memoryview(original)
        self.assertIs(fixed.obj, original)
        self.start()
        diagnostics.copy_value(original, memoryview, label='view')
        diagnostics.copy_value(original, memoryview, label='view')
        self.assertNotIn('repeated_copy', self.categories(diagnostics.uninstall()))

    def test_retained_reference_red_green(self):
        owner = Owner()
        owner.state = {'history': []}
        obj = Owner()
        weak = weakref.ref(obj)
        owner.state['history'].append(obj)
        del obj
        gc.collect()
        self.assertIsNotNone(weak())
        self.start()
        diagnostics.watch(weak(), 'request')
        diagnostics.operation_cleanup('end request', {'owner': owner}, ('request',))
        report = diagnostics.uninstall()
        retained = next(f for f in report['findings'] if f['category'] == 'retained_reference')
        self.assertEqual(retained['evidence']['owner_reference_path'], "owner.state['history'][0]")
        self.assertFalse(retained['evidence']['leak_confirmed'])
        owner.state['history'].clear()
        gc.collect()
        self.assertIsNone(weak())
        self.start()
        obj = Owner()
        diagnostics.watch(obj, 'request')
        owner.state['history'].append(obj)
        del obj
        owner.state['history'].clear()
        gc.collect()
        diagnostics.operation_cleanup('end request', {'owner': owner}, ('request',))
        self.assertFalse(diagnostics.uninstall()['findings'])

    def test_collectible_cycle_red_green(self):
        obj = Owner()
        obj.self = obj
        weak = weakref.ref(obj)
        self.assertIs(obj.self, obj)
        del obj
        gc.collect()
        self.assertIsNone(weak())
        self.start()
        obj = Owner()
        obj.self = obj
        diagnostics.watch(obj, 'cycle')
        diagnostics.checkpoint('cycle', roots={'owner': obj})
        del obj
        event = diagnostics.confirm_collection('after roots dropped', gc.collect())
        self.assertTrue(event['payload']['watched'][0]['collected_confirmed'])
        report = diagnostics.uninstall()
        self.assertIn('reference_cycle', self.categories(report))
        self.assertNotIn('retained_reference', self.categories(report))
        self.assertFalse(report['findings'][0]['evidence']['cycles'][0]['leak'])
        fixed = Owner()
        fixed.child = None
        self.assertIsNone(fixed.child)
        self.start()
        diagnostics.checkpoint('cycle', roots={'owner': fixed})
        self.assertFalse(diagnostics.uninstall()['findings'])

    def test_legacy_uncollectable_red_green_where_supported(self):
        try:
            import _testcapi
        except ImportError:
            self.skipTest('UNSUPPORTED: CPython test C API is unavailable; this case is incomplete.')
        if not hasattr(_testcapi, 'with_tp_del'):
            self.skipTest('UNSUPPORTED: legacy tp_del fixture API is unavailable; this case is incomplete.')
        if gc.get_debug() & gc.DEBUG_SAVEALL:
            self.skipTest('INCOMPLETE: DEBUG_SAVEALL changes gc.garbage semantics.')

        @_testcapi.with_tp_del
        class Legacy:
            def __init__(self):
                self.link = self

            def __tp_del__(self):
                pass

        def fixture(fixed, annotate):
            references = []
            watched_ids = []
            for i in range(3):
                obj = Legacy()
                references.append(weakref.ref(obj))
                watched_ids.append(id(obj))
                if annotate:
                    diagnostics.watch(obj, 'legacy-' + str(i))
                if fixed:
                    obj.link = None
            del obj
            collected = gc.collect()
            alive = sum(ref() is not None for ref in references)
            garbage_ids = {id(obj) for obj in gc.garbage}
            if annotate:
                diagnostics.confirm_collection('legacy collection', collected)
            # Release only this fixture's uncollectable objects after proof.
            for obj in gc.garbage:
                if id(obj) in watched_ids:
                    obj.link = None
            obj = None
            gc.garbage[:] = [obj for obj in gc.garbage if id(obj) not in watched_ids]
            gc.collect()
            return alive, len(set(watched_ids) & garbage_ids)

        self.assertEqual(fixture(False, False), (3, 3))
        self.start()
        self.assertEqual(fixture(False, True), (3, 3))
        self.assertIn('uncollectable_objects', self.categories(diagnostics.uninstall()))
        self.assertEqual(fixture(True, False), (0, 0))
        self.start()
        self.assertEqual(fixture(True, True), (0, 0))
        self.assertNotIn('uncollectable_objects', self.categories(diagnostics.uninstall()))

    def test_structure_cost_red_green(self):
        rows = [{'x': i, 'y': None} for i in range(1000)]
        fixed = [(i, None) for i in range(1000)]
        self.assertGreater(sum(map(sys.getsizeof, rows)), sum(map(sys.getsizeof, fixed)))
        self.assertEqual([(r['x'], r['y']) for r in rows], fixed)
        requirements = {'ordering': 'preserve', 'mutable': False, 'null_handling': 'None', 'access': 'sequential'}
        self.start()
        result = diagnostics.measure_traversal(rows, lambda values: sum(v['x'] for v in values), requirements=requirements)
        self.assertEqual(result, 499500)
        report = diagnostics.uninstall()
        issue = next(f for f in report['findings'] if f['category'] == 'inefficient_structure')
        self.assertEqual(issue['evidence']['dictionary_count'], 1000)
        self.assertGreater(issue['evidence']['traversal_s'], 0)
        self.assertEqual(issue['evidence']['alternatives'], [])
        self.start()
        diagnostics.structure(fixed, requirements=requirements)
        self.assertFalse(diagnostics.uninstall()['findings'])

    def assert_counter_report(self, runner, kind):
        self.assertEqual(runner(False, False), 20, 'independent conflicting execution')
        self.start()
        self.assertEqual(runner(False, True), 20)
        report = diagnostics.uninstall()
        conflicts = [f for f in report['findings'] if f['category'] == 'concurrency_hazard']
        self.assertTrue(conflicts)
        self.assertTrue(all(f['status'] == 'confirmed' for f in conflicts))
        self.assertTrue(all(f['evidence']['execution_kinds'] == [kind] for f in conflicts))
        self.assertEqual(runner(True, False), 40, 'independent corrected execution')
        self.start()
        self.assertEqual(runner(True, True), 40)
        self.assertFalse(diagnostics.uninstall()['findings'])

    def test_threads_red_green(self):
        self.assert_counter_report(thread_counter, 'thread')

    def test_separate_read_and_write_locks_do_not_hide_conflicts(self):
        self.assertEqual(thread_counter(partial_lock=True), 20)
        self.start()
        self.assertEqual(thread_counter(annotate=True, partial_lock=True), 20)
        conflicts = diagnostics.uninstall()['findings']
        self.assertTrue(conflicts)
        self.assertTrue(all(f['status'] == 'confirmed' for f in conflicts))
        self.assertEqual(thread_counter(fixed=True), 40)
        self.start()
        self.assertEqual(thread_counter(fixed=True, annotate=True), 40)
        self.assertFalse(diagnostics.uninstall()['findings'])

    def test_async_tasks_red_green(self):
        self.assert_counter_report(lambda fixed, annotate: asyncio.run(async_counter(fixed, annotate)), 'async_task')

    def test_processes_red_green(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            baseline, _ = process_counter(directory)
            self.assertEqual(baseline, 20)
            actual, report = process_counter(directory, annotate=True)
            self.assertEqual(actual, 20)
            self.assertTrue(report['findings'])
            self.assertTrue(all(f['status'] == 'confirmed' for f in report['findings']))
            self.assertTrue(all(f['evidence']['execution_kinds'] == ['process'] for f in report['findings']))
            self.assertTrue(all(len(f['evidence']['trace_paths']) == 3 for f in report['findings']))
            corrected, _ = process_counter(directory, fixed=True)
            self.assertEqual(corrected, 40)
            corrected, report = process_counter(directory, fixed=True, annotate=True)
            self.assertEqual(corrected, 40)
            self.assertFalse(report['findings'])

    def test_potential_without_conflicting_outcome(self):
        self.start()
        with diagnostics.actor('thread', 'one'):
            diagnostics.shared_read('counter', 0)
        with diagnostics.actor('thread', 'two'):
            diagnostics.shared_read('counter', 0)
            diagnostics.shared_write('counter', 1)
        with diagnostics.actor('thread', 'one'):
            diagnostics.shared_write('counter', 1)
        # No independent final result: never label this a confirmed conflict.
        report = diagnostics.uninstall()
        self.assertEqual([f['status'] for f in report['findings']], ['potential'])

    def test_trace_evidence_and_bounded_object_lifetimes(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            recorder = self.start(directory)
            obj = Owner()
            weak = weakref.ref(obj)
            diagnostics.checkpoint('owner', roots={'owner': obj})
            del obj
            gc.collect()
            self.assertIsNone(weak(), 'recorder must never keep inspected objects alive')
            summary = diagnostics.uninstall()
            saved = [json.loads(line) for line in (directory / 'events.jsonl').read_text().splitlines()]
            self.assertEqual(saved, summary['events'])
            self.assertEqual(saved[0]['source']['file'], __file__)
            self.assertEqual(saved[0]['source']['function'], 'test_trace_evidence_and_bounded_object_lifetimes')
            self.assertGreaterEqual(saved[0]['t'], 0)
            self.assertGreater(saved[0]['pid'], 0)
        self.start(max_events=2)
        for i in range(5):
            diagnostics.checkpoint('bounded', {'i': i})
        summary = diagnostics.uninstall()
        self.assertEqual(len(summary['events']), 2)
        self.assertEqual(summary['dropped_events'], 3)
        self.assertFalse(summary['findings'], 'a finding must never reference an unsaved dropped event')

    def test_cli_diagnostics_saved_trace_red_green(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            script, profile = directory / 'workload.py', directory / 'profile.json'
            script.write_text('import pmg_diagnostics as evidence\n'
                              'history = [bytearray(100000) for _ in range(2)]\n'
                              'evidence.checkpoint("history", {"operations": 2}, {"history": history})\n'
                              'history.extend(bytearray(100000) for _ in range(2))\n'
                              'evidence.checkpoint("history", {"operations": 4}, {"history": history})\n'
                              'print(sum(len(value) for value in history))\n')
            env = dict(os.environ, PYTHONPATH=str(ROOT / 'src' / 'python'))
            baseline = subprocess.run([sys.executable, str(script)], env=env, capture_output=True, text=True, timeout=10)
            self.assertEqual(baseline.returncode, 0, baseline.stderr)
            self.assertEqual(baseline.stdout.strip(), '400000')
            command = [sys.executable, str(ROOT / 'src' / 'python' / 'pmg_profile.py'), '--memory', 'off',
                       '--diagnostics', '--out', str(profile), str(script)]
            result = subprocess.run(command, env=env, capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout, baseline.stdout)
            report = json.loads(profile.read_text())['diagnostics']
            self.assertIn('memory_growth', self.categories(report))
            self.assertEqual(report['findings'][0]['reproduction']['command'], command)
            self.assertEqual(report['findings'][0]['source']['file'], str(script))
            trace = [json.loads(line) for line in Path(report['trace_path']).read_text().splitlines()]
            self.assertEqual(trace, report['events'])
            self.assertIsNone(trace[0]['payload']['python_traced_current_bytes'])
            fixed_source = script.read_text().replace('history.extend(bytearray(100000) for _ in range(2))',
                                                       'history[:] = [bytearray(100000) for _ in range(2)]')
            script.write_text(fixed_source)
            fixed = subprocess.run([sys.executable, str(script)], env=env, capture_output=True, text=True, timeout=10)
            self.assertEqual(fixed.returncode, 0, fixed.stderr)
            self.assertEqual(fixed.stdout.strip(), '200000')
            fixed = subprocess.run(command, env=env, capture_output=True, text=True, timeout=10)
            self.assertEqual(fixed.returncode, 0, fixed.stderr)
            self.assertEqual(fixed.stdout.strip(), '200000')
            self.assertNotIn('memory_growth', self.categories(json.loads(profile.read_text())['diagnostics']))


if __name__ == '__main__':
    unittest.main(verbosity=2)
