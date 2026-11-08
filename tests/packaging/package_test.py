"""Check the built VSIX contents and start its bundled server on this Python.

Usage: python tests/packaging/package_test.py path/to/extension.vsix
Run with the oldest and newest supported interpreters after packaging.
"""
from pathlib import Path
import sys
import json
import subprocess
import tempfile
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'static'))
from parity_test import run  # noqa: E402


def check_package(archive):
    with zipfile.ZipFile(archive) as package, tempfile.TemporaryDirectory() as temp:
        names = set(package.namelist())
        python = 'extension/src/python/'
        required = {'extension/dist/extension.js'} | {python + name for name in (
            'guardian_server.py', 'messages.json', 'probe.py', '_vendor.py',
            'pmg_profile.py', 'pmg_diagnostics.py',
            'rules/__init__.py', 'rules/tables.py', 'rules/messages.py', 'rules/syntax.py', 'rules/index.py',
            'rules/threads.py', 'rules/visitor.py', 'rules/list_once.py', 'rules/positions.py', 'rules/analyze.py',
            'pmg_profiler/__init__.py', 'pmg_profiler/common.py', 'pmg_profiler/cli.py', 'pmg_profiler/runner.py',
            'pmg_profiler/session.py', 'pmg_profiler/system.py', 'pmg_profiler/sampler.py', 'pmg_profiler/memory.py',
            'pmg_profiler/timeline.py', 'pmg_profiler/retention.py', 'pmg_profiler/sources.py',
            'pmg_profiler/line_monitor.py', 'pmg_profiler/trace_function.py', 'pmg_profiler/gc_recorder.py',
            'pmg_profiler/report.py', 'pmg_profiler/allocations.py', 'pmg_profiler/guard.py',
            'libs/pygls/__init__.py', 'libs/cattrs/__init__.py', 'libs/lsprotocol/types.py',
            'libs/exceptiongroup/__init__.py', 'libs/typing_extensions.py',
        )}
        assert required <= names, 'Missing runtime files: ' + str(required - names)
        forbidden = {'node_modules', '__pycache__', '.mypy_cache',
                     'eslint.config.mjs', 'pyproject.toml', 'requirements-quality.txt', 'requirements-acceptance.txt'}
        forbidden_roots = ('extension/src/typescript/', 'extension/src/rust/', 'extension/tests/', 'extension/scripts/', 'extension/examples/')
        leaked = [name for name in names if forbidden.intersection(Path(name).parts) or name.startswith(forbidden_roots)]
        assert not leaked, 'Development files in VSIX: ' + str(leaked[:10])
        package.extractall(temp)
        server = Path(temp) / 'extension' / 'src' / 'python' / 'guardian_server.py'
        diagnostics = run([sys.executable, str(server)], {}, 'rows = cursor.fetchall()\n')
        assert [row[4] for row in diagnostics] == ['heap-inflation'], diagnostics
        print('PASS packaged runtime files and exclusions')
        print('PASS packaged Python server on Python ' + sys.version.split()[0])
        target = Path(temp) / 'fixture.py'
        target.write_text('print("packaged fixture")\n')
        output = Path(temp) / 'profile.json'
        result = subprocess.run([sys.executable, str(server.with_name('pmg_profile.py')),
            '--memory', 'off', '--diagnostics', '--out', str(output), str(target)],
            capture_output=True, text=True, timeout=30)
        assert result.returncode == 0, result.stderr
        profile = json.loads(output.read_text())
        assert profile['allocation_backend']['backend'] == 'sampled'
        assert profile['diagnostics']['instrumentation'] == 'cooperative'
        print('PASS packaged profiler and diagnostic helper imports')


if __name__ == '__main__':
    if len(sys.argv) != 2:
        sys.exit('Usage: python tests/packaging/package_test.py path/to/extension.vsix')
    check_package(sys.argv[1])
