"""External memory-budget supervisor. RSS observations are not line allocations."""
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import time

from .common import SCHEMA, write_json_atomic

MIB = 1 << 20
MAX_REPORT_BYTES = 8 * MIB


def linux_cgroup_available(root=Path('/sys/fs/cgroup'), membership=Path('/proc/self/cgroup')):
    """Read finite v2 limits at the current group and its visible ancestors."""
    groups = membership.read_text().splitlines()
    relative = next((row.split(':', 2)[2] for row in groups if row.startswith('0::')), None)
    if relative is None:
        if any('memory' in row.split(':', 2)[1].split(',') for row in groups):
            raise OSError('cgroup v1 memory budgets are unsupported; automatic guard refuses host-only capacity')
        return None
    current = root / relative.lstrip('/')
    remaining = []
    while current == root or root in current.parents:
        maximum = current / 'memory.max'
        usage = current / 'memory.current'
        if maximum.is_file():
            limit = maximum.read_text().strip()
            if limit != 'max':
                remaining.append(max(0, int(limit) - int(usage.read_text())))
        if current == root:
            break
        current = current.parent
    return min(remaining) if remaining else None


def system_memory():
    """Return current host availability and visible container headroom, in bytes."""
    cgroup = None
    container_status = 'not_applicable'
    if sys.platform.startswith('linux'):
        values = {}
        for row in Path('/proc/meminfo').read_text().splitlines():
            key, value = row.split(':', 1)
            values[key] = int(value.split()[0]) * 1024
        total, available = values['MemTotal'], values['MemAvailable']
        cgroup = linux_cgroup_available()
        container_status = 'v2_visible_limits' if cgroup is not None else 'no_finite_v2_limit; v1_unavailable'
    elif sys.platform == 'darwin':
        total = int(subprocess.check_output(['/usr/sbin/sysctl', '-n', 'hw.memsize'], timeout=2))
        text = subprocess.check_output(['/usr/bin/vm_stat'], text=True, timeout=2)
        page = int(re.search(r'page size of (\d+) bytes', text).group(1))
        pages = {key: int(value) for key, value in re.findall(r'^(Pages [^:]+):\s+(\d+)', text, re.M)}
        available = page * sum(pages[key] for key in ('Pages free', 'Pages inactive', 'Pages speculative'))
    elif sys.platform == 'win32':
        import ctypes
        from ctypes import wintypes

        class MemoryStatus(ctypes.Structure):
            _fields_ = [('length', wintypes.DWORD), ('load', wintypes.DWORD)] + [
                (name, ctypes.c_ulonglong) for name in ('total', 'available', 'page_total',
                 'page_available', 'virtual_total', 'virtual_available', 'extended')]
        value = MemoryStatus()
        value.length = ctypes.sizeof(value)
        if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(value)):
            raise OSError('GlobalMemoryStatusEx failed')
        total, available = int(value.total), int(value.available)
    else:
        raise OSError('system memory observation is unavailable on this platform')
    return {'total_bytes': total, 'host_available_bytes': available,
            'container_available_bytes': cgroup, 'container_status': container_status,
            'available_bytes': min(available, cgroup) if cgroup is not None else available}


def choose_budget(memory, requested_mib=0, reserve_mib=512):
    available = memory['available_bytes']
    reserve = int(min(reserve_mib, available / (2 * MIB)) * MIB)
    budget = min(available // 4, available - reserve)
    if requested_mib:
        budget = int(min(requested_mib, budget / MIB) * MIB)
    if budget < 64 * MIB:
        raise OSError('insufficient available memory for the 64 MiB minimum profiling budget')
    return budget, reserve


def process_memory(pid, peak=False):
    """Return (root RSS, group RSS). Shared pages may be counted more than once."""
    if sys.platform == 'win32':
        import ctypes
        from ctypes import wintypes

        class Counters(ctypes.Structure):
            _fields_ = [('cb', wintypes.DWORD), ('faults', wintypes.DWORD)] + [
                (name, ctypes.c_size_t) for name in ('peak', 'rss', 'peak_paged',
                 'paged', 'peak_nonpaged', 'nonpaged', 'pagefile', 'peak_pagefile')]
        kernel = ctypes.windll.kernel32
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        api = ctypes.windll.psapi.GetProcessMemoryInfo
        api.argtypes = [wintypes.HANDLE, ctypes.POINTER(Counters), wintypes.DWORD]
        handle = kernel.OpenProcess(0x410, False, pid)
        if not handle:
            raise OSError('OpenProcess failed')
        try:
            value = Counters()
            value.cb = ctypes.sizeof(value)
            if not api(handle, ctypes.byref(value), value.cb):
                raise OSError('GetProcessMemoryInfo failed')
            measured = int(value.peak if peak else value.rss)
            return measured, measured
        finally:
            kernel.CloseHandle(handle)
    if sys.platform.startswith('linux'):
        page = os.sysconf('SC_PAGE_SIZE')
        rows = []
        for entry in Path('/proc').iterdir():
            if not entry.name.isdigit():
                continue
            try:
                fields = (entry / 'stat').read_text().rsplit(')', 1)[1].split()
                if int(fields[2]) == pid:
                    rss = int((entry / 'statm').read_text().split()[1]) * page
                    rows.append((int(entry.name), rss))
            except (FileNotFoundError, ProcessLookupError):
                continue  # A process exited during the observation.
        return next((rss for ident, rss in rows if ident == pid), 0), sum(rss for _, rss in rows)
    if sys.platform == 'darwin':
        output = subprocess.check_output(['/bin/ps', '-axo', 'pid=,pgid=,rss='], text=True, timeout=2)
        rows = [tuple(map(int, line.split())) for line in output.splitlines() if line.strip()]
        return next((rss * 1024 for ident, _, rss in rows if ident == pid), 0), sum(
            rss * 1024 for _, group, rss in rows if group == pid)
    raise OSError('external current RSS observation is unavailable')


def terminate_family(child):
    if os.name == 'posix':
        try:
            os.killpg(child.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    else:
        # Windows RSS coverage is root-only; /T also stops its remaining descendants.
        subprocess.run(['taskkill', '/PID', str(child.pid), '/T', '/F'],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5)
        if child.poll() is None:
            child.kill()
    child.wait(timeout=5)


def supervisor_peak_rss():
    try:
        if sys.platform == 'win32':
            return process_memory(os.getpid(), peak=True)[0]
        import resource
        peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        return int(peak) if sys.platform == 'darwin' else int(peak) * 1024
    except (ImportError, OSError):
        return None


def partial_profile(ns, script, command, elapsed, observations, root_peak, reason):
    return {'schema': SCHEMA, 'script': script, 'python': '.'.join(map(str, sys.version_info[:3])),
            'gil_split': False, 'wall_s': elapsed, 'memory_mode': ns.memory,
            'files': {}, 'functions': {}, 'file_hashes': {},
            'run': {'argv': ns.script_args[1:], 'command': command, 'platform': sys.platform,
                    'started_at': None, 'cwd': os.getcwd()},
            'memory_attribution': 'process-observation', 'memory_snapshot_at_exit': False,
            'rss_kind': 'current' if root_peak else None,
            'rss_peak_mb': root_peak / 1e6 if root_peak else None,
            'peak_traced_mb': None, 'memory_stacks': None, 'native_untraced_mb': None,
            'native_memory': {'status': 'unavailable', 'reason': 'no completed allocator analysis', 'rss_residual_mb': None},
            'memory_observations': observations,
            'sampler_error': reason + '; no completed execution profile is available',
            'allocation_backend': {'backend': ns.allocation_backend, 'available': False,
                                   'reason': 'capture analysis did not complete'}}


def supervise(ns, arguments, profiler_path, script, out):
    """Run the instrumented target separately, stop its family on measured pressure."""
    command = [sys.executable, profiler_path, *arguments]
    memory = system_memory()
    budget, reserve = choose_budget(memory, ns.memory_limit_mb, ns.memory_reserve_mb)
    # Probe observation before executing any target code. Failure is fail-closed.
    process_memory(os.getpid())
    child_out = out + '.' + str(time.time_ns()) + '.guard-child.json'
    started = time.perf_counter()
    observations = []
    root_peak = group_peak = 0
    reason = None
    remaining = memory['available_bytes']
    group = 0
    child = subprocess.Popen([sys.executable, profiler_path, '--_guard-child',
        '--_guard-output', child_out, '--_guard-budget', str(budget),
        '--_guard-command', json.dumps(command), *arguments],
        start_new_session=os.name == 'posix')
    def interrupt(_signal, _frame):
        raise KeyboardInterrupt
    previous_term = signal.signal(signal.SIGTERM, interrupt)
    try:
        while True:
            code = child.poll()
            if os.name != 'posix' and code is not None:
                break
            try:
                root, group = process_memory(child.pid)
            except OSError:
                if os.name != 'posix' and child.poll() is not None:
                    break  # Windows handle lookup can race a normal process exit.
                raise
            if code is not None and group == 0:
                break
            root_peak, group_peak = max(root_peak, root), max(group_peak, group)
            observations.append({'t': time.perf_counter() - started, 'rss_bytes': root or None,
                'traced_bytes': None, 'traced_growth_bytes': None, 'transient_bytes': None,
                'rss_change_bytes': None, 'bookkeeping_bytes': None})
            if len(observations) >= 256:
                observations = observations[::2]
            remaining = system_memory()['available_bytes']
            if group >= budget:
                reason = 'rss_budget'
            elif remaining <= reserve:
                reason = 'system_reserve'
            if reason:
                terminate_family(child)
                break
            time.sleep(.1)
    except KeyboardInterrupt:
        reason = 'interrupted'
        terminate_family(child)
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        reason = 'observation_unavailable: ' + str(error)
        terminate_family(child)
    finally:
        signal.signal(signal.SIGTERM, previous_term)
    code = child.wait()
    elapsed = time.perf_counter() - started
    applied_limit = None
    limit_path = Path(child_out + '.limit.json')
    if limit_path.is_file() and limit_path.stat().st_size < 4096:
        try:
            applied_limit = json.loads(limit_path.read_text())
        except (OSError, ValueError):
            pass
    guard = {'status': 'stopped' if reason else 'completed', 'budget_bytes': budget,
        'reserve_bytes': reserve, 'initial_memory': memory, 'stop_reason': reason,
        'scope': 'process_group' if os.name == 'posix' else 'profiled_process',
        'supervisor_pid': os.getpid(), 'profiled_pid': child.pid, 'child_exit_code': code,
        'peak_root_rss_bytes': root_peak or None,
        'peak_group_rss_bytes': group_peak if observations else None,
        'last_available_bytes': remaining, 'last_group_rss_bytes': group,
        'poll_interval_s': .1, 'coverage': 'partial',
        'hard_limit': applied_limit.get('hard_limit') if isinstance(applied_limit, dict) else None,
        'hard_limit_bytes': applied_limit.get('bytes') if isinstance(applied_limit, dict) else None,
        'notes': ['The budget includes in-process profiler and capture-analysis memory.',
                  'RSS is polled and can overshoot; escaped process groups are not covered.',
                  'Group RSS can count shared pages more than once.',
                  'RLIMIT_AS limits each Linux process virtual address space, not group RSS.',
                  'Windows watchdog RSS covers the root process only; descendant RSS is unavailable.',
                  'A budget stop is not evidence of a leak or an OOM.']}
    guard['raw_artifacts'] = []
    for artifact in Path(child_out).parent.glob(Path(child_out).name + '*'):
        if artifact.is_file():
            guard['raw_artifacts'].append({'path': str(artifact), 'bytes': artifact.stat().st_size,
                                           'integrity': 'unverified'})
        if len(guard['raw_artifacts']) >= 64:
            break
    # Persist pressure evidence before any potentially expensive JSON decode.
    write_json_atomic(out + '.guard.json', guard)
    profile = None
    child_path = Path(child_out)
    if not reason and child_path.is_file() and child_path.stat().st_size <= MAX_REPORT_BYTES:
        try:
            value = json.loads(child_path.read_text(encoding='utf-8'))
            if isinstance(value, dict) and isinstance(value.get('run'), dict):
                profile = value
        except (OSError, ValueError):
            pass
    if profile is None:
        profile = partial_profile(ns, script, command, elapsed, observations, root_peak,
                                  reason or 'child report unavailable or exceeds the 8 MiB reader limit')
    profile['run']['command'] = command
    profile.setdefault('process_id', child.pid)
    guard['supervisor_peak_rss_bytes'] = supervisor_peak_rss()
    profile['memory_guard'] = guard
    write_json_atomic(out, profile)
    write_json_atomic(out + '.guard.json', guard)
    if reason:
        sys.stderr.write('[pmg] memory guard stopped the run: ' + reason + '\n')
    sys.stderr.write('[pmg] guarded profile written to ' + out + '\n')
    return 130 if reason == 'interrupted' else 3 if reason else code if code >= 0 else 1
