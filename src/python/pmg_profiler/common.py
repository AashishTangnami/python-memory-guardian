"""Code that at least two profiler modules use. Standard library only, no profiling state,
and on import only the path constants below."""
from __future__ import annotations

import functools
import json
import os
from pathlib import Path
import sysconfig
import tempfile

SCHEMA = 3

_PACKAGE = Path(__file__).parent
_HOME = _PACKAGE.parent


def _normalize(path) -> str:
    return os.path.normcase(os.path.abspath(path))


# The profiler's own source, as written and with symbolic links resolved: a launched script keeps the
# path it was given, and modules found through sys.path[0] have that path resolved.
_PACKAGE_DIRS = tuple({_normalize(p) + os.sep for p in (_PACKAGE, _PACKAGE.resolve())})
# Code that runs as the profiler: its tracemalloc allocations are the profiler's own memory.
_ENTRY_FILES = {_normalize(folder / 'pmg_profile.py') for folder in (_HOME, _HOME.resolve())}
# The cooperative helper: never the user's code, but the user's own calls allocate through it, so
# memory snapshots look past its frames to the calling line (see own_memory_paths).
_HELPER_FILES = {_normalize(folder / 'pmg_diagnostics.py') for folder in (_HOME, _HOME.resolve())}


def register_entry_point(path) -> None:
    """Record the path pmg_profile.py runs from, if that differs from the paths above."""
    _ENTRY_FILES.add(_normalize(path))
    is_profiler_file.cache_clear()


def register_helper(path) -> None:
    """Record the path pmg_diagnostics.py was imported from, if that differs from the paths above."""
    _HELPER_FILES.add(_normalize(path))
    is_profiler_file.cache_clear()


@functools.lru_cache(maxsize=4096)
def is_profiler_file(filename) -> bool:
    """The profiler package, its entry point or its diagnostics helper: never the user's code.
    Cached, because a symbolic link makes the last check read the disk."""
    path = _normalize(filename)
    if path in _ENTRY_FILES or path in _HELPER_FILES or path.startswith(_PACKAGE_DIRS):
        return True
    real = os.path.normcase(os.path.realpath(path))
    return real in _ENTRY_FILES or real in _HELPER_FILES or real.startswith(_PACKAGE_DIRS)


def own_memory_paths() -> tuple[frozenset, tuple]:
    """(files, folder prefixes) whose allocations are the profiler's own memory: the package and the
    entry point, normalised as `normcase(abspath(...))`. The diagnostics helper is not included, so
    its allocations stay with the user line that called it, as before the package split."""
    return frozenset(_ENTRY_FILES), _PACKAGE_DIRS


class UserFiles:
    """Which source files are the user's code: under the root, and not the standard library,
    an installed package or the profiler. One answer per file name, computed once."""

    def __init__(self, root: str):
        self.root = _normalize(root).rstrip(os.sep) + os.sep
        paths = sysconfig.get_paths()
        self.excluded = tuple({os.path.normcase(p).rstrip(os.sep) + os.sep for p in (
            paths.get("stdlib"), paths.get("platstdlib"), paths.get("purelib"), paths.get("platlib"))
            if p})
        self._cache: dict[str, bool] = {}

    def is_user(self, filename: str) -> bool:
        hit = self._cache.get(filename)
        if hit is None:
            if filename.startswith("<"):         # <frozen ...>, <string>, <stdin>
                hit = False
            else:
                p = os.path.normcase(os.path.abspath(filename))
                hit = (p.startswith(self.root) and not is_profiler_file(p)
                       and not p.startswith(self.excluded) and "site-packages" not in p)
            self._cache[filename] = hit
        return hit


def check_output_folder(out) -> None:
    """Refuse an output folder named .pmg that is a symbolic link or a file.

    A cloned repository can contain such a link, and the profile, its guard files and capture files would
    then land wherever it points. Other folders are the user's own choice (on macOS /tmp is itself a link),
    so only the project's .pmg folder is checked."""
    folder = os.path.dirname(os.path.abspath(out))
    if os.path.basename(folder) == '.pmg' and (os.path.islink(folder) or os.path.lexists(folder) and not os.path.isdir(folder)):
        raise ValueError(f'{folder} is a symbolic link or a file, not a folder; the profiler writes only into a real '
                         '.pmg folder there, so remove it (it is created again)')


def write_json_atomic(path, value) -> None:
    """Write through a temporary file, so a reader never sees half a report.

    The temporary file has a random name and is created exclusively, so a link planted in the output
    folder (for example in a cloned repository) cannot redirect the write. os.replace then replaces
    a link at `path` itself, never its target. mkstemp creates the file readable by its owner only; the
    report gets the usual 0644, so an extension running as another user (a root container) can read it."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(dir=path.parent, prefix=path.name + '.', suffix='.tmp')
    try:
        os.chmod(temporary, 0o644)
        with open(handle, 'w', encoding='utf-8') as stream:
            json.dump(value, stream)
        os.replace(temporary, path)
    except BaseException:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise
