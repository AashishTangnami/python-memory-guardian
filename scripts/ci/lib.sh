#!/bin/bash
# Step runner for the CI job scripts. Every step runs even after an earlier failure, so one failing
# suite does not hide the others; the summary at the end lists each step and the job fails if any
# required step failed. PMG_CI_OUT (optional) receives the job's evidence files.
results=()
failed=0

_run() {   # _run <optional 0|1> <command...>
  local optional=$1; shift
  echo "::group::$*"
  local start=$SECONDS code=0
  "$@" || code=$?
  echo "::endgroup:: exit $code in $((SECONDS - start))s"
  if [ "$code" -eq 0 ]; then results+=("pass  $*")
  elif [ "$optional" -eq 1 ]; then results+=("warn  $*  (exit $code, not blocking)")
  else results+=("FAIL  $*  (exit $code)"); failed=1; fi
}
step() { _run 0 "$@"; }
optional_step() { _run 1 "$@"; }

# package.json scripts chained with &&, one step per command (the script stays the single list).
npm_script_steps() {
  local name cmd
  for name in "$@"; do
    while IFS= read -r cmd; do
      step bash -c "$cmd"
    done < <(node -p "require('./package.json').scripts['$name'].split('&&').map(s => s.trim()).join('\n')")
  done
}

evidence() {   # evidence <path...>: copied to $PMG_CI_OUT, keeping relative paths
  [ -n "${PMG_CI_OUT:-}" ] || return 0
  local path
  for path in "$@"; do
    [ -e "$path" ] || continue
    mkdir -p "$PMG_CI_OUT/$(dirname "$path")" && cp -R "$path" "$PMG_CI_OUT/$(dirname "$path")/"
  done
}

finish() {
  local summary
  summary=$(printf '%s\n' "== $(python --version 2>&1) $(uname -sm)" "${results[@]}")
  echo; echo "$summary"
  [ -z "${GITHUB_STEP_SUMMARY:-}" ] || printf '```\n%s\n```\n' "$summary" >> "$GITHUB_STEP_SUMMARY"
  exit "$failed"
}
