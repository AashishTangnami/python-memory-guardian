"""
Put the bundled packages (server/libs/, built from requirements.txt by
`npm run vendor:python`) first on sys.path.

Imported before any third-party import by guardian_server.py and rules.py, so the
server finds its own pinned pygls/lsprotocol however it is launched: by the VS Code
client, by the tests, or by hand. It deliberately goes *first*, ahead of anything in
the interpreter's site-packages, so a different pygls installed by the user can never
replace the tested versions.
"""
import os
import sys

LIBS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "libs")

if os.path.isdir(LIBS):
    if LIBS in sys.path:
        sys.path.remove(LIBS)
    sys.path.insert(0, LIBS)
