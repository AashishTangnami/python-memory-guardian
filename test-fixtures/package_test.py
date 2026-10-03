"""Check the built VSIX contents and start its bundled server on this Python.

Usage: python test-fixtures/package_test.py path/to/extension.vsix
Run with the oldest and newest supported interpreters after packaging.
"""
from pathlib import Path
import sys
import tempfile
import zipfile

from parity_test import run


def check_package(archive):
    with zipfile.ZipFile(archive) as package, tempfile.TemporaryDirectory() as temp:
        names = set(package.namelist())
        required = {
            'extension/dist/extension.js', 'extension/server/guardian_server.py',
            'extension/server/rules.py', 'extension/server/messages.json',
            'extension/server/probe.py', 'extension/server/pmg_profile.py',
            'extension/server/_vendor.py', 'extension/server/libs/pygls/__init__.py',
            'extension/server/libs/cattrs/__init__.py', 'extension/server/libs/lsprotocol/types.py',
            'extension/server/libs/exceptiongroup/__init__.py',
            'extension/server/libs/typing_extensions.py',
        }
        assert required <= names, 'Missing runtime files: ' + str(required - names)
        forbidden = {'node_modules', '__pycache__', '.mypy_cache', 'test-fixtures', 'rust-server'}
        leaked = [name for name in names if forbidden.intersection(Path(name).parts)]
        assert not leaked, 'Development files in VSIX: ' + str(leaked[:10])
        package.extractall(temp)
        server = Path(temp) / 'extension' / 'server' / 'guardian_server.py'
        diagnostics = run([sys.executable, str(server)], {}, 'rows = cursor.fetchall()\n')
        assert [row[4] for row in diagnostics] == ['heap-inflation'], diagnostics
        print('PASS packaged runtime files and exclusions')
        print('PASS packaged Python server on Python ' + sys.version.split()[0])


if __name__ == '__main__':
    if len(sys.argv) != 2:
        sys.exit('Usage: python test-fixtures/package_test.py path/to/extension.vsix')
    check_package(sys.argv[1])
