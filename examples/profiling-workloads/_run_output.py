"""Run-output lifecycle shared by the profiling workloads.

The data these scripts write is scratch: the result of a run is its profile, not
its files. Each run gets a fresh `run-*` folder in its script's `*_output/`
folder, which is deleted when the script exits (normally, after an error, or on
Ctrl+C). A run killed by a signal (for example by the memory guard) cannot clean
up, so every run first deletes the `run-*` folders whose process has ended; runs
still in progress are left alone. The `*_output/` folder is removed once no run
folder is left in it.
"""
import atexit
import os
import shutil
import tempfile
import time
from pathlib import Path

PID_FILE = ".pmg-run-pid"     # the owning process; a dead owner means a stale run
OUTPUT_MARKER = ".pmg-run-output"  # in an output folder a workload created
NEW_RUN_GRACE_SECONDS = 60    # a folder without a PID file may be a run starting up


def _alive(pid):
    if pid == os.getpid():
        return True
    if os.name == "nt":
        import ctypes
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        handle = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return ctypes.get_last_error() == 5  # ERROR_ACCESS_DENIED: exists, not ours
        code = ctypes.c_ulong()
        ok = kernel32.GetExitCodeProcess(handle, ctypes.byref(code))
        kernel32.CloseHandle(handle)
        return bool(ok) and code.value == 259  # STILL_ACTIVE
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # exists, owned by another user
    return True


def _stale(folder):
    try:
        pid = int((folder / PID_FILE).read_text(encoding="ascii"))
    except (OSError, ValueError):
        try:  # no PID: a run from before this file existed, or one starting right now
            return time.time() - folder.stat().st_mtime > NEW_RUN_GRACE_SECONDS
        except OSError:
            return False
    return not _alive(pid)


def remove_stale_runs(output):
    for folder in sorted(Path(output).glob("run-*")):
        if folder.is_dir() and not folder.is_symlink() and _stale(folder):
            shutil.rmtree(folder, ignore_errors=True)
            print(f"Removed stale run output: {folder}", flush=True)


def _remove_unused_output(output):
    """Remove `output` only if a workload created it (marker) and nothing else is in it."""
    marker = output / OUTPUT_MARKER
    try:
        if marker.is_file() and [p.name for p in output.iterdir()] == [OUTPUT_MARKER]:
            marker.unlink()
            output.rmdir()
    except OSError:
        pass  # another run started in the meantime, or the folder is not ours


def _remove_run(root, output):
    shutil.rmtree(root, ignore_errors=True)
    print(f"Removed run output: {root}", flush=True)
    _remove_unused_output(output)


def create_run_output(output):
    """Make this run's folder under `output` and delete it when the script exits."""
    output = Path(output)
    if not output.exists():
        output.mkdir(parents=True)
        (output / OUTPUT_MARKER).write_text("created by a profiling workload\n", encoding="ascii")
    remove_stale_runs(output)
    root = Path(tempfile.mkdtemp(prefix="run-", dir=output)).resolve()
    (root / PID_FILE).write_text(str(os.getpid()), encoding="ascii")
    atexit.register(_remove_run, root, output)
    return root
