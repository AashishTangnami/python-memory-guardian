"""Opt-in, cooperative runtime evidence. No implicit allocation or race tracing.

The APIs run the operation supplied by the caller once. They retain event data,
never frames or inspected objects. Requested payload bytes, object footprints,
tracemalloc bytes, and process memory are distinct quantities.
"""
from collections import deque
from contextlib import contextmanager, asynccontextmanager
import asyncio
import contextvars
import gc
import hashlib
import json
import math
import os
import stat
import sys
import threading
import types
import time
import tracemalloc
import weakref

SCHEMA = 1
MAX_NODES = 10000
MAX_DEPTH = 12
MAX_LABELS = 256
_active = None
_actor_override = contextvars.ContextVar('pmg_diagnostic_actor', default=None)
_critical = contextvars.ContextVar('pmg_diagnostic_critical', default=())


def _create_fresh(path):
    """Open a new file at `path` for writing, never through a link planted there (for example in a cloned
    repository). An existing file or link at `path` is removed first, so an existing trace is still replaced;
    the exclusive create then fails instead of following anything placed there meanwhile."""
    try:
        mode = os.lstat(path).st_mode
    except FileNotFoundError:
        pass
    else:
        if stat.S_ISLNK(mode) or stat.S_ISREG(mode):
            os.unlink(path)
    return open(path, 'x', encoding='utf-8')


def _identity():
    override = _actor_override.get()
    task_id = None
    task_name = None
    try:
        task = asyncio.current_task()
    except RuntimeError:
        task = None
    if task is not None:
        task_id, task_name = str(id(task)), task.get_name()
    del task
    kind = 'async_task' if task_id else 'thread'
    ident = task_id or str(threading.get_ident())
    if override:
        kind, ident = override
    return {'pid': os.getpid(), 'thread': threading.get_ident(),
            'thread_name': threading.current_thread().name,
            'task': task_id, 'task_name': task_name,
            'actor_kind': kind, 'actor_id': str(ident)}


def _location():
    """Copy frame metadata then release all frame references before return."""
    stack = []
    frame = sys._getframe(1)
    try:
        while frame is not None and len(stack) < 32:
            filename = os.path.abspath(frame.f_code.co_filename)
            if filename != os.path.abspath(__file__):
                stack.append({'file': filename, 'line': frame.f_lineno,
                              'function': frame.f_code.co_name})
            frame = frame.f_back
    finally:
        del frame
    return (stack[0] if stack else None), list(reversed(stack))


def _value(value):
    # No repr() calls: user repr functions can mutate state or hold large objects.
    if isinstance(value, float) and not math.isfinite(value):
        return {'kind': 'nonfinite', 'value': str(value)}
    if value is None or isinstance(value, (bool, int, float)):
        return {'kind': 'literal', 'value': value}
    if isinstance(value, str) and len(value) <= 256:
        return {'kind': 'literal', 'value': value}
    return {'kind': 'identity', 'type': type(value).__name__, 'id': id(value)}


def _children(obj, path):
    """Inspect built-ins, instance dictionaries and native slot descriptors.

    Never execute a property or user descriptor to discover an owner.
    """
    if type(obj) is dict:
        for index, (key, value) in enumerate(obj.items()):
            if index >= MAX_NODES:
                break
            # Subclasses can override repr; large keys can also allocate large
            # temporary strings. Preserve a bounded label without calling them.
            if type(key) is str:
                name = repr(key[:80])
            elif type(key) is int and key.bit_length() > 256:
                name = '<large integer key>'
            else:
                name = repr(key) if type(key) in (int, float, bool, type(None)) else '<key>'
            yield key, path + '.<key>'
            yield value, path + '[' + name[:80] + ']'
    elif type(obj) in (list, tuple, set, frozenset, deque):
        for index, item in enumerate(obj):
            if index >= MAX_NODES:
                break
            yield item, path + '[' + str(index) + ']'
    else:
        namespace = None
        for cls in type(obj).__mro__:
            descriptor = vars(cls).get('__dict__')
            if descriptor is not None:
                if type(descriptor) is types.GetSetDescriptorType:
                    try:
                        namespace = descriptor.__get__(obj, type(obj))
                    except (AttributeError, TypeError):
                        pass
                break
        if type(namespace) is dict:
            yield namespace, path + '.<attributes>'
            for index, (key, value) in enumerate(namespace.items()):
                if index >= MAX_NODES:
                    break
                yield value, path + '.' + (key[:80] if type(key) is str else '<attribute>')
        del namespace
        for cls in type(obj).__mro__:
            for name, descriptor in vars(cls).items():
                if type(descriptor) is types.MemberDescriptorType:
                    try:
                        value = descriptor.__get__(obj, type(obj))
                    except AttributeError:
                        continue
                    yield value, path + '.' + name


def inspect_roots(roots=None, target_ids=()):
    """A bounded owner graph. Sizes are shallow, deduplicated Python sizes.

    Native buffers, custom containers and arbitrary GC edges are outside
    this graph. The returned values contain no inspected objects.
    """
    roots = roots or {}
    pending = deque((obj, str(name), 0, ()) for name, obj in roots.items())
    seen = set()
    target_ids = set(target_ids)
    paths = {}
    sizes = {}
    counts = {}
    cycles = []
    total = 0
    complete = True
    while pending:
        if len(seen) >= MAX_NODES:
            complete = False
            break
        obj, path, depth, ancestors = pending.popleft()
        oid = id(obj)
        if oid in target_ids:
            paths.setdefault(str(oid), path)
        if oid in ancestors:
            if len(cycles) < 100:
                cycles.append({'path': path, 'object_id': oid, 'type': type(obj).__name__,
                               'gc_tracked': gc.is_tracked(obj),
                               'collectibility': 'conditional_on_removal_of_roots',
                               'leak': False})
            del obj
            continue
        if oid in seen:
            del obj
            continue
        seen.add(oid)
        name = type(obj).__name__
        # Do not invoke a user-defined __sizeof__ implementation.
        builtin = type(obj) in (dict, list, tuple, set, frozenset, deque, str, bytes,
                               bytearray, int, float, bool, complex, type(None), range, slice, memoryview)
        size = sys.getsizeof(obj) if builtin else object.__sizeof__(obj)
        total += size
        sizes[name] = sizes.get(name, 0) + size
        counts[name] = counts.get(name, 0) + 1
        children = _children(obj, path)
        if depth >= MAX_DEPTH:
            if next(children, None) is not None:
                complete = False
        else:
            for child, child_path in children:
                if len(pending) + len(seen) >= MAX_NODES:
                    complete = False
                    break
                pending.append((child, child_path, depth + 1, ancestors + (oid,)))
            # A loop local otherwise holds the final inspected child.
            child = None
        del children, obj
    pending.clear()
    return {'python_footprint_bytes': total, 'object_count': len(seen),
            'type_counts': counts, 'type_bytes': sizes, 'reference_paths': paths,
            'cycles': cycles[:100], 'complete': complete,
            'limits': {'max_nodes': MAX_NODES, 'max_depth': MAX_DEPTH},
            'coverage': 'explicit roots; built-in containers, instance dictionaries and native slots'}


class Recorder:
    def __init__(self, trace_path=None, workload=None, max_events=20000):
        if max_events < 1:
            raise ValueError('max_events must be positive')
        self.trace_path = os.path.abspath(trace_path) if trace_path else None
        self.workload = dict(workload or {})
        self.max_events = max_events
        self.events = []
        self.findings = []
        self.dropped_events = 0
        self.dropped_findings = 0
        self.dropped_labels = 0
        self._saved_ids = set()
        self._counter = 0
        self._lock = threading.RLock()
        self._started = time.perf_counter()
        self._watched = {}
        self._checkpoints = {}
        self._copies = {}
        self._allocations = {}
        self._closed = False
        self._stream = None
        if self.trace_path:
            os.makedirs(os.path.dirname(self.trace_path), exist_ok=True)
            self._stream = _create_fresh(self.trace_path)

    def event(self, category, payload):
        source, stack = _location()
        timestamp = time.perf_counter()
        identity = _identity()
        with self._lock:
            self._counter += 1
            event = {'id': str(os.getpid()) + ':' + str(self._counter),
                     'timestamp_s': timestamp, 'elapsed_s': timestamp - self._started,
                     't': timestamp - self._started,
                     **identity, 'source': source, 'stack': stack,
                     'category': category, 'payload': payload}
            if self._closed or len(self.events) >= self.max_events:
                self.dropped_events += 1
                return event
            self.events.append(event)
            self._saved_ids.add(event['id'])
            if self._stream:
                self._stream.write(json.dumps(event, allow_nan=False) + '\n')
                self._stream.flush()
            return event

    def _label(self, mapping, label):
        if label not in mapping and len(mapping) >= MAX_LABELS:
            self.dropped_labels += 1
            return False
        return True

    def finding(self, category, summary, events, evidence=None, severity='warning',
                confidence='high', status='measured'):
        event = events[-1]
        if len(self.findings) >= 2000 or any(e['id'] not in self._saved_ids for e in events):
            self.dropped_findings += 1
            return None
        finding = {'id': 'finding:' + str(len(self.findings) + 1),
                   'category': category, 'severity': severity,
                   'confidence': confidence, 'status': status,
                   'summary': summary, 'source': event['source'],
                   'pid': event['pid'], 'thread': event['thread'],
                   'actor_kind': event['actor_kind'], 'actor_id': event['actor_id'],
                   'workload': dict(self.workload),
                   'evidence': {'event_ids': [e['id'] for e in events], **(evidence or {})},
                   'reproduction': {'command': self.workload.get('command'),
                                    'script': self.workload.get('script'),
                                    'argv': self.workload.get('argv'),
                                    'trace_path': self.trace_path}}
        self.findings.append(finding)
        return finding

    def checkpoint(self, label, workload=None, roots=None, memory_budget_bytes=None):
        graph = inspect_roots(roots)
        current, peak = tracemalloc.get_traced_memory() if tracemalloc.is_tracing() else (None, None)
        event = self.event('checkpoint', {'label': label, 'workload': dict(workload or {}),
                                         'python_traced_current_bytes': current,
                                         'python_traced_peak_bytes': peak,
                                         'owner_graph': graph})
        previous = self._checkpoints.get(label)
        if self._label(self._checkpoints, label):
            self._checkpoints[label] = event
        if previous:
            before = previous['payload']['owner_graph']['python_footprint_bytes']
            after = graph['python_footprint_bytes']
            condition = {'before': previous['payload']['workload'], 'after': dict(workload or {})}
            if after > before and after - before >= 65536:
                self.finding('memory_growth', 'Explicit owner graph grew between observation points.',
                             [previous, event], {'before_bytes': before, 'after_bytes': after,
                                                 'growth_bytes': after - before,
                                                 'workload_condition': condition,
                                                 'measurement': 'deduplicated shallow Python object footprint',
                                                 'bounded_graph': not graph['complete']})
                if memory_budget_bytes is not None and after > memory_budget_bytes:
                    self.finding('oom_risk', 'Observed Python footprint exceeded the declared memory budget; continued growth poses an OOM risk.',
                                 [previous, event], {'memory_budget_bytes': memory_budget_bytes,
                                                     'after_bytes': after, 'workload_condition': condition,
                                                     'oom_observed': False}, status='projected')
        if graph['cycles']:
            self.finding('reference_cycle', 'Reachable reference cycles exist; a cycle alone is not a leak.',
                         [event], {'cycles': graph['cycles']}, severity='information')
        return event

    def watch(self, obj, label):
        if not self._label(self._watched, label):
            return False
        try:
            reference = weakref.ref(obj)
        except TypeError:
            self.event('retention_unavailable', {'label': label, 'reason': 'Object does not support weak references.'})
            return False
        self._watched[label] = {'reference': reference, 'object_id': id(obj),
                                'type': type(obj).__name__, 'retentions': 0,
                                'gc_tracked': gc.is_tracked(obj)}
        self.event('retention_watch', {'label': label, 'object_id': id(obj), 'type': type(obj).__name__,
                                      'gc_tracked': gc.is_tracked(obj)})
        return True

    def operation_cleanup(self, label, roots=None, expected_released=()):
        watched = [(name, self._watched[name]) for name in expected_released if name in self._watched]
        graph = inspect_roots(roots, [item['object_id'] for _, item in watched])
        event = self.event('operation_cleanup', {'label': label, 'owner_graph': graph,
                                                'expected_released': list(expected_released)})
        for name, item in watched:
            obj = item['reference']()
            alive = obj is not None
            del obj
            if alive:
                item['retentions'] += 1
                self.finding('retained_reference', 'A watched object remains alive after its declared cleanup point.',
                             [event], {'label': name, 'object_id': item['object_id'],
                                       'retention_observations': item['retentions'],
                                       'owner_reference_path': graph['reference_paths'].get(str(item['object_id'])),
                                       'weak_reference_alive': True,
                                       'leak_confirmed': False})
        return event

    def confirm_collection(self, label, collected):
        """Call after the fixture itself ran gc.collect(); do not force GC here."""
        watched_ids = {item['object_id'] for item in self._watched.values()}
        remaining = set()
        objects = gc.get_objects()
        try:
            for obj in objects:
                if id(obj) in watched_ids:
                    remaining.add(id(obj))
        finally:
            obj = None
            del objects
        observations = []
        for name, item in self._watched.items():
            value = item['reference']()
            alive = value is not None
            del value
            observations.append({'label': name, 'object_id': item['object_id'],
                                 'weak_reference_alive': alive,
                                 'gc_object_present': item['object_id'] in remaining,
                                 'gc_registry_available': item['gc_tracked'],
                                 'collected_confirmed': item['gc_tracked'] and not alive and item['object_id'] not in remaining})
        garbage = [{'object_id': id(obj), 'type': type(obj).__name__} for obj in gc.garbage[:100]]
        event = self.event('collection_result', {'label': label, 'collected': collected,
                                                 'watched': observations, 'gc_garbage': garbage,
                                                 'gc_debug_saveall': bool(gc.get_debug() & gc.DEBUG_SAVEALL)})
        if garbage and not (gc.get_debug() & gc.DEBUG_SAVEALL):
            self.finding('uncollectable_objects', 'Objects remain in gc.garbage after the declared collection.',
                         [event], {'objects': garbage, 'garbage_count': len(gc.garbage)}, severity='warning')
        return event

    def allocate(self, size, factory=bytearray, label='allocation'):
        value = factory(size)
        event = self.event('explicit_allocation', {'label': label, 'requested_payload_bytes': size,
                                                   'factory': getattr(factory, '__name__', type(factory).__name__),
                                                   'returned_object_reported_bytes': sys.getsizeof(value),
                                                   'allocation_domain': 'explicit factory request',
                                                   'native_allocation_bytes': None})
        if self._label(self._allocations, label):
            total = self._allocations.setdefault(label, {'requests': 0, 'requested_payload_bytes': 0})
            total['requests'] += 1
            total['requested_payload_bytes'] += size
        return value

    def copy_value(self, source, producer, label='copy', requirements=None):
        value = producer(source)
        graph = inspect_roots({'copy': value})
        event = self.event('explicit_copy', {'label': label, 'footprint': graph,
                                            'requirements': dict(requirements or {}),
                                            'distinct_result': value is not source})
        if not self._label(self._copies, label):
            return value
        copies = self._copies.setdefault(label, [])
        if len(copies) < 10:
            copies.append(event)
        if len(copies) == 2 and all(e['payload']['distinct_result'] for e in copies) and graph['python_footprint_bytes'] >= 65536:
            self.finding('repeated_copy', 'The declared operation materialized multiple distinct copies.',
                         copies, {'copy_count_observed': 2,
                                  'observed_copy_footprints_bytes': [e['payload']['footprint']['python_footprint_bytes'] for e in copies],
                                  'requirements': dict(requirements or {}),
                                  'alternatives': []})
        return value

    def structure(self, value, label='structure', requirements=None, traversal_s=None):
        graph = inspect_roots({'structure': value})
        requirements = dict(requirements or {})
        event = self.event('structure_cost', {'label': label, 'footprint': graph,
                                             'requirements': requirements,
                                             'traversal_s': traversal_s})
        dictionaries = graph['type_counts'].get('dict', 0)
        if dictionaries >= 1000 and graph['type_bytes'].get('dict', 0) >= 100000:
            self.finding('inefficient_structure', 'The observed structure has at least 1,000 Python dictionaries and substantial dictionary overhead.',
                         [event], {'object_count': graph['object_count'],
                                   'dictionary_count': dictionaries,
                                   'dictionary_shallow_bytes': graph['type_bytes']['dict'],
                                   'python_footprint_bytes': graph['python_footprint_bytes'],
                                   'traversal_s': traversal_s,
                                   'requirements': requirements, 'alternatives': []})
        return event

    def close(self):
        with self._lock:
            if not self._closed:
                self._closed = True
                if self._stream:
                    self._stream.close()
                    self._stream = None
                candidates = analyze_concurrency(self.events, self.workload, self.trace_path)
                remaining = 2000 - len(self.findings)
                self.findings.extend(candidates[:remaining])
                self.dropped_findings += max(0, len(candidates) - remaining)
        trace_hash = getattr(self, '_trace_sha256', None)
        if self.trace_path and not hasattr(self, '_trace_sha256'):
            digest = hashlib.sha256()
            try:
                with open(self.trace_path, 'rb') as stream:
                    for chunk in iter(lambda: stream.read(1 << 20), b''):
                        digest.update(chunk)
                trace_hash = digest.hexdigest()
            except OSError:
                pass  # Target code can remove the artifact; hash remains unavailable.
            self._trace_sha256 = trace_hash
        return {'schema': SCHEMA, 'instrumentation': 'cooperative', 'trace_sha256': trace_hash,
                'trace_path': self.trace_path, 'events': self.events,
                'findings': self.findings, 'dropped_events': self.dropped_events,
                'dropped_findings': self.dropped_findings, 'dropped_labels': self.dropped_labels,
                'scan_limits': {'max_nodes': MAX_NODES, 'max_depth': MAX_DEPTH,
                                'max_labels': MAX_LABELS, 'max_findings': 2000},
                'explicit_allocation_totals': self._allocations,
                'capabilities': {
                    'python_allocation_events': {'status': 'partial', 'reason': 'Only explicit allocate() requests; requested payload bytes are not allocator events.'},
                    'native_allocation_events': {'status': 'unavailable', 'reason': 'No native allocator hooks.'},
                    'owner_paths': {'status': 'partial', 'reason': 'Bounded traversal of caller-supplied roots.'},
                    'cycles': {'status': 'partial', 'reason': 'Explicit owner graph; collection can be checked with confirm_collection().'},
                    'concurrency': {'status': 'partial', 'reason': 'Explicit shared-access events; child traces require merge_traces().'},
                    'structures': {'status': 'partial', 'reason': 'Explicit structure observations and optional traversal timing.'}}}


def analyze_concurrency(events, workload=None, trace_path=None):
    """Confirm an observed lost update, not a C/CPU-level data race.

    A confirmation requires two actors to read the same literal value, each to
    write the same changed value, and no declared common critical section.
    Other shared, unsynchronized accesses are potential hazards only.
    """
    findings = []
    reads = {}
    writes = {}
    reported = set()
    by_key = {}
    outcomes = {e['payload']['key']: e for e in events if e['category'] == 'shared_outcome'}
    for event in sorted(events, key=lambda e: e['timestamp_s']):
        if event['category'] not in ('shared_read', 'shared_write'):
            continue
        payload = event['payload']
        key = payload['key']
        actor_key = (event['pid'], event['actor_kind'], event['actor_id'])
        by_key.setdefault(key, []).append(event)
        if event['category'] == 'shared_read':
            reads[(key, actor_key)] = event
            continue
        read = reads.pop((key, actor_key), None)
        if read is None:
            continue
        for other_actor, other_read, other_write in writes.get(key, []):
            if other_actor == actor_key:
                continue
            ordered = (read['timestamp_s'] < other_write['timestamp_s'] and
                       other_read['timestamp_s'] < event['timestamp_s'])
            def scopes(observed):
                return {(scope['lock_id'], scope['acquisition_id'])
                        for scope in observed['payload'].get('synchronization', ())}
            # One acquisition must cover the full read-write pair for each
            # actor. Merely locking each individual access is insufficient.
            actor_locks = {lock for lock, acquisition in scopes(read) & scopes(event)}
            other_locks = {lock for lock, acquisition in scopes(other_read) & scopes(other_write)}
            protected = bool(actor_locks & other_locks)
            literal = all(e['payload']['value']['kind'] == 'literal' for e in (read, other_read, other_write, event))
            same_reads = read['payload']['value'] == other_read['payload']['value']
            same_writes = event['payload']['value'] == other_write['payload']['value']
            changed = event['payload']['value'] != read['payload']['value']
            signature = (key, tuple(sorted((read['id'], other_read['id']))))
            outcome = outcomes.get(key)
            observed_mismatch = (outcome is not None and outcome['payload']['observed'] != outcome['payload']['expected'])
            if ordered and literal and same_reads and same_writes and changed and observed_mismatch and not protected and signature not in reported:
                reported.add(signature)
                observed = [read, other_read, other_write, event, outcome]
                findings.append(_concurrency_finding('confirmed', key, observed, workload, trace_path,
                                                     'Observed overlapping unsynchronized read-modify-write operations lost an update.'))
            elif ordered and literal and same_reads and same_writes and changed and not protected and signature not in reported:
                reported.add(signature)
                findings.append(_concurrency_finding('potential', key, [read, other_read, other_write, event], workload, trace_path,
                                                     'Overlapping declared read-modify-write operations lack one critical section across each operation; final conflicting outcome is unavailable.'))
        writes.setdefault(key, []).append((actor_key, read, event))
        writes[key] = writes[key][-100:]
    for key, observed in by_key.items():
        actors = {(e['pid'], e['actor_kind'], e['actor_id']) for e in observed}
        if len(actors) >= 2 and any(e['category'] == 'shared_write' for e in observed):
            if not any(f['evidence']['key'] == key for f in findings):
                unprotected = [e for e in observed if not e['payload'].get('synchronization')]
                if unprotected:
                    findings.append(_concurrency_finding('potential', key, unprotected[:20], workload, trace_path,
                                                         'Multiple actors accessed declared shared mutable state without a declared critical section.'))
    return findings


def _concurrency_finding(status, key, events, workload, trace_path, summary):
    event = next((e for e in reversed(events) if e['category'] == 'shared_write'), events[-1])
    return {'id': 'concurrency:' + key + ':' + event['id'], 'category': 'concurrency_hazard',
            'severity': 'warning', 'confidence': 'high' if status == 'confirmed' else 'medium',
            'status': status, 'summary': summary, 'source': event['source'],
            'pid': event['pid'], 'thread': event['thread'],
            'actor_kind': event['actor_kind'], 'actor_id': event['actor_id'],
            'workload': dict(workload or {}),
            'evidence': {'key': key, 'event_ids': [e['id'] for e in events],
                         'execution_kinds': sorted({e['actor_kind'] for e in events if e['category'] in ('shared_read', 'shared_write')}),
                         'conflict_type': 'logical_lost_update' if status == 'confirmed' else 'shared_access_candidate',
                         'native_data_race_confirmed': False,
                         'final_outcomes': [e['payload'] for e in events if e['category'] == 'shared_outcome']},
            'reproduction': {'command': (workload or {}).get('command'),
                             'script': (workload or {}).get('script'),
                             'argv': (workload or {}).get('argv'), 'trace_path': trace_path}}


def merge_traces(paths, workload=None):
    paths = list(paths)
    events = []
    for path in paths:
        with open(path, encoding='utf-8') as stream:
            for line in stream:
                events.append(json.loads(line))
    events.sort(key=lambda e: e['timestamp_s'])
    findings = analyze_concurrency(events, workload, paths[0] if paths else None)
    for finding in findings:
        finding['evidence']['trace_paths'] = paths
    return {'events': events, 'findings': findings,
            'clock': 'host monotonic clock; merge only traces from the same host'}


def install(trace_path=None, workload=None, max_events=20000):
    global _active
    if _active is not None:
        raise RuntimeError('A diagnostics recorder is already active')
    _active = Recorder(trace_path, workload, max_events)
    return _active


def uninstall():
    global _active
    if _active is None:
        return None
    recorder, _active = _active, None
    return recorder.close()


def checkpoint(label, workload=None, roots=None, memory_budget_bytes=None):
    return _active.checkpoint(label, workload, roots, memory_budget_bytes) if _active else None


def watch(obj, label):
    return _active.watch(obj, label) if _active else False


def operation_cleanup(label, roots=None, expected_released=()):
    return _active.operation_cleanup(label, roots, expected_released) if _active else None


def confirm_collection(label, collected):
    return _active.confirm_collection(label, collected) if _active else None


def allocate(size, factory=bytearray, label='allocation'):
    return _active.allocate(size, factory, label) if _active else factory(size)


def copy_value(source, producer, label='copy', requirements=None):
    return _active.copy_value(source, producer, label, requirements) if _active else producer(source)


def structure(value, label='structure', requirements=None):
    return _active.structure(value, label, requirements) if _active else None


def measure_traversal(value, visitor, label='structure', requirements=None):
    start = time.perf_counter()
    result = visitor(value)
    elapsed = time.perf_counter() - start
    if _active:
        _active.structure(value, label, requirements, elapsed)
    return result


def shared_read(key, value):
    if _active:
        _active.event('shared_read', {'key': str(key), 'value': _value(value),
                                     'synchronization': list(_critical.get())})
    return value


def shared_write(key, value):
    if _active:
        _active.event('shared_write', {'key': str(key), 'value': _value(value),
                                      'synchronization': list(_critical.get())})
    return value


def shared_outcome(key, observed, expected):
    """Declare an independently checked final value after actors complete."""
    if _active:
        return _active.event('shared_outcome', {'key': str(key), 'observed': _value(observed),
                                               'expected': _value(expected)})
    return None


def synchronization(key, action):
    if _active:
        return _active.event('synchronization', {'key': str(key), 'action': str(action)})
    return None


@contextmanager
def critical_section(lock, key):
    synchronization(key, 'acquire_requested')
    lock.acquire()
    scope = {'lock_id': _lock_identity(lock, key),
             'acquisition_id': str(os.getpid()) + ':' + str(time.perf_counter_ns())}
    token = _critical.set(_critical.get() + (scope,))
    synchronization(key, 'acquired')
    try:
        yield
    finally:
        synchronization(key, 'release')
        _critical.reset(token)
        lock.release()


def _lock_identity(lock, key):
    semaphore = getattr(lock, '_semlock', None)
    if semaphore is not None:
        identity = semaphore.name or str(semaphore.handle)
        return 'process_lock:' + str(identity) + ':' + str(key)
    return 'local_lock:' + str(os.getpid()) + ':' + str(id(lock)) + ':' + str(key)


@asynccontextmanager
async def async_critical_section(lock, key):
    synchronization(key, 'acquire_requested')
    await lock.acquire()
    scope = {'lock_id': _lock_identity(lock, key),
             'acquisition_id': str(os.getpid()) + ':' + str(time.perf_counter_ns())}
    token = _critical.set(_critical.get() + (scope,))
    synchronization(key, 'acquired')
    try:
        yield
    finally:
        synchronization(key, 'release')
        _critical.reset(token)
        lock.release()


@contextmanager
def actor(kind, identity):
    if kind not in ('thread', 'process', 'async_task'):
        raise ValueError('actor kind must be thread, process, or async_task')
    token = _actor_override.set((kind, str(identity)))
    try:
        yield
    finally:
        _actor_override.reset(token)
