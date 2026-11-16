#!/bin/bash
# Lay a clean tree from the read-only checkout at /repo over the image's installed dependencies:
# tracked and new files with uncommitted edits, nothing git ignores (no host node_modules, libs,
# tests/profiler/out/ or Rust build). Then run one job script: runtime or acceptance.
set -euo pipefail
git config --global --add safe.directory /repo
(cd /repo && git ls-files -z --cached --others --exclude-standard \
  | tar --null --ignore-failed-read -T - -cf -) | tar -xf - -C /work/src
exec bash "scripts/ci/$1.sh"
