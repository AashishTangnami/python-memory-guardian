"""Run the target: guard and allocation setup, the profiling session, and the final report."""
from __future__ import annotations

import atexit
from importlib.machinery import SourceFileLoader
import os
import sys
import threading
import time
import tokenize
import types

from .cli import Options
from .common import check_output_folder, register_helper, write_json_atomic
from .session import Profiler


def address_space_bytes() -> int:
    """Linux: this process's mapped virtual address space (/proc/self/statm), or 0 if unreadable."""
    try:
        with open('/proc/self/statm') as statm:
            return int(statm.readline().split()[0]) * os.sysconf('SC_PAGE_SIZE')
    except (OSError, ValueError, IndexError):
        return 0


def run(options: Options, entry_point: str) -> int:
    """entry_point: the absolute path of pmg_profile.py, which the guard relaunches and the run records."""
    ns, script_args, arguments, fail = options
    script = os.path.abspath(script_args[0])
    out = os.path.abspath(ns.out)  # Target code may change the working directory.
    try:
        check_output_folder(out if not ns._guard_child else ns._guard_output)
    except ValueError as error:
        fail(str(error))
    if ns.memory_guard == 'auto' and not ns._guard_child:
        import subprocess
        from .guard import supervise
        ns.script_args = script_args
        try:
            return supervise(ns, arguments, entry_point, script, out)
        except (OSError, ValueError, subprocess.SubprocessError) as error:
            fail('memory guard failed: ' + str(error))
    if ns._guard_child:
        out = os.path.abspath(ns._guard_output)
        if sys.platform.startswith('linux'):
            import resource
            _, hard = resource.getrlimit(resource.RLIMIT_AS)
            limit = min(ns._guard_budget, hard) if hard != resource.RLIM_INFINITY else ns._guard_budget
            # A free-threaded build's mimalloc reserves about 1 GiB of address space at startup. A limit
            # at or near the space already mapped refuses new mappings (the sampler's thread stack first)
            # long before RSS reaches the budget. Below twice that space it is not applied; the polled
            # RSS budget still holds and hard_limit stays null.
            if address_space_bytes() * 2 <= limit:
                resource.setrlimit(resource.RLIMIT_AS, (limit, limit))
                write_json_atomic(out + '.limit.json', {'hard_limit': 'RLIMIT_AS', 'bytes': limit})
    capture_id = str(time.time_ns())
    allocations = None
    if ns.allocation_backend == 'memray':
        from .allocations import AllocationRecorder, AllocationBackendUnavailable
        try:
            # Distinct artifacts preserve saved baselines and avoid stale fork
            # captures from a previous invocation with the same output path.
            allocations = AllocationRecorder(out + '.' + capture_id + '.memray',
                                             ns.root or os.path.dirname(script),
                                             trace_python_allocators=ns.allocation_detail == 'full')
        except AllocationBackendUnavailable as error:
            fail(str(error))
    prof = Profiler(ns.root or os.path.dirname(script), ns.interval, ns.memory, ns.frames,
                    ns.monitoring, ns.trace_function.strip() if ns.trace_function else None)
    if ns.diagnostics:
        import pmg_diagnostics  # Load the helper before adding the target's import directory.
        register_helper(pmg_diagnostics.__file__)
    prof.argv = list(script_args[1:])
    prof.command = [sys.executable, entry_point] + arguments
    if ns._guard_child:
        prof.command = ns._guard_command
    sys.argv = [script] + script_args[1:]
    sys.path.insert(0, os.path.dirname(script))
    code = 0
    if allocations is not None:
        allocations.start()
    prof.start()
    diagnostics = None
    if ns.diagnostics:
        os.makedirs(os.path.dirname(out), exist_ok=True)
        diagnostics = pmg_diagnostics.install(trace_path=out + '.' + capture_id + '.events.jsonl',
                                             workload={'argv': prof.argv, 'script': script,
                                                       'command': prof.command, 'cwd': prof.cwd})
    main_globals = None

    def finish():
        if diagnostics is not None:
            prof.diagnostics = pmg_diagnostics.uninstall()
        prof.stop(main_globals)
        if allocations is not None:
            allocations.stop()
        os.makedirs(os.path.dirname(out), exist_ok=True)
        report = prof.report(script)
        report['allocation_backend'] = (allocations.report() if allocations is not None else
            {'backend': 'sampled', 'available': False,
             'reason': 'sampling and tracemalloc snapshots do not record allocator request events'})
        if allocations is not None:
            report['native_memory']['reason'] = (
                'RSS residual is not native allocation evidence; optional allocator capture '
                'is reported separately with its coverage and availability.')
        write_json_atomic(out, report)
        sys.stderr.write(f"[pmg] profile written to {out}\n")

    try:
        # Keep the target namespace alive through non-daemon worker shutdown.
        # runpy restores __main__ too early for late worker pickle/module access.
        target_module = types.ModuleType('__main__')
        target_module.__file__ = script
        target_module.__cached__ = None
        target_module.__loader__ = SourceFileLoader('__main__', script)
        main_globals = target_module.__dict__
        sys.modules['__main__'] = target_module
        with tokenize.open(script) as source:
            target_code = compile(source.read(), script, 'exec')
        exec(target_code, main_globals)
    except SystemExit as e:
        code = e.code if isinstance(e.code, int) else (0 if e.code is None else 1)
        if e.code is not None and not isinstance(e.code, int):
            print(e.code, file=sys.stderr)
    except KeyboardInterrupt:
        code = 130                                    # Ctrl+C: still write what we have
    except BaseException:
        import traceback
        traceback.print_exc()
        code = 1
    finally:
        # CPython removes these temporary script attributes after top-level code.
        if main_globals is not None:
            main_globals.pop('__file__', None)
            main_globals.pop('__cached__', None)
        if code != 130 and any(t is not threading.current_thread() and not t.daemon
                               for t in threading.enumerate()):
            # Let Python perform its normal executor/thread shutdown before the
            # final snapshot. Joining here would deadlock idle executor workers,
            # whose shutdown hooks have not run yet. Keep sampling meanwhile.
            atexit.register(finish)
        else:
            finish()
    return code
