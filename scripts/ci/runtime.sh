#!/bin/bash
# Runtime job: lint, build, backend parity, every suite of `npm test`, then the package check.
# Expects dependencies installed (the CI image does this: npm ci, src/python/libs, acceptance and
# quality requirements, uv).
cd "$(dirname "$0")/../.." || exit 2
source scripts/ci/lib.sh
step npm run lint
step npm run compile
step npm run test:parity
npm_script_steps test:runtime test:guard
step npm run package -- --out guardian.vsix
step python tests/packaging/package_test.py guardian.vsix
evidence guardian.vsix tests/profiler/out
finish
