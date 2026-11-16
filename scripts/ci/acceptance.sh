#!/bin/bash
# Acceptance job: saved-artifact acceptance gate on a fresh Rust build. A failed, skipped or
# unsupported case blocks; criteria partial only because of a documented coverage limit do not.
# Overhead budgets assume a serial, isolated host, so they are recorded but do not block.
cd "$(dirname "$0")/../.." || exit 2
source scripts/ci/lib.sh
step npm run compile
step npm run test:parity
step python tests/acceptance/acceptance_suite.py --reuse-rust-build --gate regression
optional_step timeout 30m python tests/acceptance/measure_overhead.py --include-memray \
  --out .validation/acceptance-current/overhead
evidence .validation/acceptance-current
finish
