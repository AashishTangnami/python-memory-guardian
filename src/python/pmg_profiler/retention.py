"""What the program still holds at exit: the largest objects, and who holds leaked memory."""
from __future__ import annotations

import os
import sys
import time
import tracemalloc
import types

from .common import UserFiles

# Largest objects still held at exit: how many to report, the smallest worth reporting, and how many
# container items to size before extrapolating.
LARGEST_OBJECTS = 20
LARGEST_MIN_BYTES = 1 << 20
CONTAINER_SAMPLE = 10_000
SKIP_TYPES = (types.ModuleType, type, types.FunctionType, types.BuiltinFunctionType, types.MethodType,
              types.CodeType, types.FrameType)


def sized(obj) -> tuple[int, int | None, bool]:
    """(bytes, items, estimated) for one object: its own sys.getsizeof (each library's __sizeof__),
    plus its items one level deep for builtin containers, extrapolated past CONTAINER_SAMPLE items."""
    try:
        size = sys.getsizeof(obj)
    except Exception:
        return 0, None, False
    if isinstance(obj, dict):
        parts = (x for kv in obj.items() for x in kv)
        count = 2 * len(obj)
    elif isinstance(obj, (list, tuple, set, frozenset)) or type(obj).__name__ == "deque":
        parts, count = iter(obj), len(obj)
    else:
        return size, None, False
    seen = 0
    sized = 0
    try:
        for x in parts:
            if seen >= CONTAINER_SAMPLE:
                break
            try:
                sized += sys.getsizeof(x)
            except Exception:
                pass
            seen += 1
    except RuntimeError:
        # Another thread changed the container meanwhile (likely at exit without a GIL, where a daemon
        # thread keeps running): keep the items read so far as a sample instead of losing the profile.
        pass
    estimated = seen < count
    if estimated and seen:
        sized = sized * count // seen
    return size + sized, len(obj), estimated


class Retention:
    def __init__(self, users: UserFiles):
        self.users = users
        self.holder_scan = None

    def root_walk(self, main_globals, budget_s, max_depth=5, max_nodes=10_000):
        """Yield bounded owner paths. Do not invoke descriptors or inspect frames."""
        deadline = time.perf_counter() + budget_s
        status = {'complete': True, 'scope': 'user module roots', 'max_depth': max_depth,
                  'max_nodes': max_nodes, 'visited': 0, 'truncated': False}
        self.holder_scan = status
        queue = []
        spaces = [('global', main_globals)] if main_globals is not None else []
        for module in list(sys.modules.values()):
            path = getattr(module, '__file__', None)
            if path and self.users.is_user(path) and getattr(module, '__name__', '') != '__main__':
                spaces.append((module.__name__, vars(module)))
        for prefix, space in spaces:
            for name, value in list(space.items()):
                if not name.startswith('__'):
                    label = f'global {name}' if prefix == 'global' else f'{prefix}.{name}'
                    queue.append((label, value, 0))
        seen = set()
        position = 0
        try:
            while position < len(queue):
                if status['visited'] >= max_nodes or time.perf_counter() > deadline:
                    status['truncated'] = True
                    break
                label, value, depth = queue[position]
                queue[position] = None
                position += 1
                if id(value) in seen or isinstance(value, SKIP_TYPES):
                    continue
                seen.add(id(value))
                status['visited'] += 1
                yield label, value
                children = []
                cls = type(value)
                if cls is dict:
                    for i, (key, item) in enumerate(list(value.items())):
                        if i >= 1000:
                            status['truncated'] = True
                            break
                        token = repr(key)[:80] if type(key) in (str, int, float, bool, type(None)) else '<key>'
                        children.append((f'{label}[{token}]', item, depth + 1))
                elif cls in (list, tuple, set, frozenset) or cls.__name__ == 'deque':
                    try:
                        for i, item in enumerate(value):
                            if i >= 1000:
                                status['truncated'] = True
                                break
                            children.append((f'{label}[{i}]', item, depth + 1))
                    except RuntimeError:
                        # Changed by another thread meanwhile (a set or deque raises): keep what was read.
                        status['truncated'] = True
                else:
                    module = sys.modules.get(getattr(cls, '__module__', ''))
                    path = getattr(module, '__file__', None)
                    if cls.__module__ == '__main__' or (path and self.users.is_user(path)):
                        try:
                            attributes = object.__getattribute__(value, '__dict__')
                        except (AttributeError, TypeError):
                            attributes = {}
                        if type(attributes) is dict:
                            children = [(f'{label}.{key}', item, depth + 1)
                                        for key, item in list(attributes.items())[:1000]]
                if depth < max_depth:
                    available = max(0, max_nodes - len(queue))
                    queue.extend(children[:available])
                    if len(children) > available:
                        status['truncated'] = True
                elif children:
                    status['truncated'] = True
                children.clear()
                value = None
        finally:
            queue.clear()
            spaces.clear()
            status['complete'] = not status['truncated']

    def largest_objects(self, main_globals: dict | None, budget_s: float = 1.0) -> dict:
        """Bounded user-root __sizeof__ footprints; container totals overlap children."""
        found: dict[int, dict] = {}
        for label, value in self.root_walk(main_globals, budget_s):
            if isinstance(value, SKIP_TYPES) or id(value) in found:
                continue
            size, items, estimated = sized(value)
            cls = type(value)
            module = getattr(cls, "__module__", "") or ""
            name = cls.__qualname__ if module in ("builtins", "__main__") else f"{module.split('.')[0]}.{cls.__qualname__}"
            found[id(value)] = {"holder": label, "type": name, "mb": round(size / 1e6, 3),
                                "items": items, "estimated": estimated}
        objects = sorted((o for o in found.values() if o["mb"] * 1e6 >= LARGEST_MIN_BYTES), key=lambda o: -o["mb"])
        return {"objects": objects[:LARGEST_OBJECTS], **self.holder_scan,
                'sizing': 'shallow plus immediate items; overlapping; sampled past item limit'}

    def origin(self, obj):
        tb = tracemalloc.get_object_traceback(obj)
        if tb is None:
            return None
        for fr in reversed(tb):                        # oldest -> newest; innermost user frame
            if self.users.is_user(fr.filename):
                return (os.path.abspath(fr.filename), fr.lineno)
        return None

    def find_holders(self, want: set, main_globals: dict | None, budget_s: float = 2.0) -> dict:
        """leak line -> [{"holder": "global LEAK", "type": "list", "items": 12}, ...]

        Containers are reached three ways, because CPython may untrack dicts that hold
        only non-container values (verified: a dict of bytearrays is untracked):
        tracked objects from gc.get_objects(), module globals, and the attributes of
        tracked instances of user-defined classes."""
        deadline = time.perf_counter() + budget_s
        found: dict = {}

        def scan(container, label):
            if isinstance(container, dict):
                items = container.values()
            elif isinstance(container, (list, tuple, set, frozenset)) or type(container).__name__ == "deque":
                items = container
            else:
                return
            hits = {}
            try:
                for i, it in enumerate(items):
                    if i >= 5000:
                        break
                    o = self.origin(it)
                    if o in want:
                        hits[o] = hits.get(o, 0) + 1
            except RuntimeError:
                pass                 # changed by another thread meanwhile: keep the hits found so far
            for loc, n in hits.items():
                rows = found.setdefault(loc, [])
                if not any(r["holder"] == label for r in rows):
                    rows.append({"holder": label, "type": type(container).__name__,
                                 "items": len(container), "matching": n})

        for label, value in self.root_walk(main_globals, budget_s):
            origin = self.origin(value)
            if origin in want and not isinstance(value, (list, dict, tuple, set, frozenset)):
                if not any(row['holder'] in label for row in found.get(origin, [])):
                    found.setdefault(origin, []).append({'holder': label, 'type': type(value).__name__,
                                                         'items': 1, 'matching': 1})
            scan(value, label)
        return found
