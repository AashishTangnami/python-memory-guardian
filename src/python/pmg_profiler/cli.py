"""Command-line parsing and validation. No profiling happens here."""
from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Callable, NamedTuple


class Options(NamedTuple):
    namespace: argparse.Namespace
    script_args: list          # the target script and its own arguments
    arguments: list            # this process's arguments, as given; the guard relaunches with them
    error: Callable[[str], None]


def parse_args(argv=None) -> Options:
    ap = argparse.ArgumentParser(prog="pmg_profile")
    ap.add_argument("--out", default=os.path.join(".pmg", "profile.json"))
    ap.add_argument("--root", default=None, help="only files under this dir count (default: script dir)")
    ap.add_argument("--interval", type=float, default=0.01)
    ap.add_argument("--memory", choices=("fast", "precise", "off"), default="fast")
    ap.add_argument("--frames", type=int, default=2, help="precise mode traceback depth (cost grows with depth)")
    ap.add_argument("--monitoring", choices=("off", "lines"), default="off",
                    help="optional Python 3.12+ line-event coverage; timing still uses sampling")
    ap.add_argument('--diagnostics', action='store_true',
                    help='enable explicit pmg_diagnostics workload/access evidence and a JSONL trace')
    ap.add_argument('--allocation-backend', choices=('sampled', 'memray'), default='sampled',
                    help='optional allocator request capture; memray must be installed in the target interpreter')
    ap.add_argument('--allocation-detail', choices=('full', 'system'), default='full',
                    help='system omits individual Python allocator requests to reduce capture cost')
    ap.add_argument('--memory-guard', choices=('auto', 'off'), default='auto',
                    help='external RSS watchdog and system-memory reserve (default: auto)')
    ap.add_argument('--memory-limit-mb', type=float, default=0,
                    help='optional smaller process-family memory budget in MiB; zero uses automatic headroom')
    ap.add_argument('--memory-reserve-mb', type=float, default=512,
                    help='system headroom in MiB, bounded to half initial available memory')
    ap.add_argument('--_guard-child', action='store_true', help=argparse.SUPPRESS)
    ap.add_argument('--_guard-output', help=argparse.SUPPRESS)
    ap.add_argument('--_guard-budget', type=int, help=argparse.SUPPRESS)
    ap.add_argument('--_guard-command', type=json.loads, help=argparse.SUPPRESS)
    ap.add_argument("--trace-function", default=None, metavar="NAME",
                    help="precise mode only while functions with this name (or qualified name) run; Python 3.12+")
    # One REMAINDER positional preserves a target's own '--' separator.
    ap.add_argument("script_args", nargs=argparse.REMAINDER, metavar="script [args...]")
    ns = ap.parse_args(argv)
    script_args = ns.script_args[1:] if ns.script_args[:1] == ['--'] else ns.script_args
    if not script_args:
        ap.error('a script is required')
    if not 0 < ns.interval < float('inf'):
        ap.error('--interval must be finite and positive')
    if not 1 <= ns.frames <= 64:
        ap.error('--frames must be between 1 and 64')
    if not 0 <= ns.memory_limit_mb < float('inf') or not 0 < ns.memory_reserve_mb < float('inf'):
        ap.error('memory limit must be finite and nonnegative; memory reserve must be finite and positive')
    if ns.trace_function is not None:
        if ns.memory != 'precise':
            ap.error('--trace-function needs --memory precise')
        if not hasattr(sys, 'monitoring'):
            ap.error('--trace-function needs Python 3.12+ (sys.monitoring)')
        if not ns.trace_function.strip():
            ap.error('--trace-function needs a function name')
    if ns._guard_child:
        if not ns._guard_output or ns._guard_budget is None or ns._guard_budget < 64 * (1 << 20):
            ap.error('invalid internal guard arguments')
        if not isinstance(ns._guard_command, list) or not all(isinstance(arg, str) for arg in ns._guard_command):
            ap.error('invalid internal guard command')
    return Options(ns, script_args, list(sys.argv[1:] if argv is None else argv), ap.error)
