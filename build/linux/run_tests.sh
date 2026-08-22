#!/usr/bin/env bash
# =============================================================================
# Purpose: Single definition of this repo's Python test suites.
#
# Both CI and local runs invoke this script, so "it passed on my machine" and
# "it passed in CI" mean the same thing. Previously the workflows ran whole
# packages (`pytest astrometricslib/`) while local runs habitually targeted
# only `astrometricslib/test/` -- a much narrower selection that silently
# skipped entire directories, and let a broken test reach CI unnoticed.
#
# Usage:
#   ./build/linux/run_tests.sh                 # every suite, no coverage
#   ./build/linux/run_tests.sh astrometricslib # one suite
#   COVERAGE=1 ./build/linux/run_tests.sh backend
#
# Any additional arguments are forwarded to pytest, e.g.
#   ./build/linux/run_tests.sh wayfindinglib -k visibility
# =============================================================================

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT_DIR"

PYTEST="${PYTEST:-$ROOT_DIR/.venv/bin/pytest}"
if [ ! -x "$PYTEST" ]; then
  PYTEST="python -m pytest"
fi

# The test harness must never touch the real image library or the network.
export PYTHONPATH="${PYTHONPATH:-.}"
export ASTROMETRICS_TESTING="${ASTROMETRICS_TESTING:-1}"
export ASTROMETRICS_FAST_TEST="${ASTROMETRICS_FAST_TEST:-1}"

SUITE="${1:-all}"
[ $# -gt 0 ] && shift

# Per-suite pytest targets. Keep these in step with the paths the workflows
# report coverage for; they are the authoritative definition of each suite.
suite_targets() {
  case "$1" in
    astrometricslib) echo "astrometricslib/" ;;
    wayfindinglib)   echo "wayfindinglib/ --ignore=wayfindinglib/scripts/commissioning/test" ;;
    backend)         echo "backend/tests/" ;;
    *) echo "unknown suite: $1" >&2; return 1 ;;
  esac
}

run_suite() {
  local suite="$1"; shift
  local targets
  targets="$(suite_targets "$suite")"

  local coverage_args=()
  if [ "${COVERAGE:-0}" = "1" ]; then
    coverage_args=(
      "--cov=$suite"
      "--cov-report=term-missing"
      "--cov-report=xml:coverage.xml"
    )
  fi

  echo "=== $suite ==="
  # shellcheck disable=SC2086 -- targets intentionally word-splits into args.
  $PYTEST ${coverage_args[@]+"${coverage_args[@]}"} $targets "$@"
}

case "$SUITE" in
  all)
    # Coverage is per-package and each run overwrites coverage.xml, so the
    # combined run is for local use and leaves coverage to the CI jobs.
    for suite in astrometricslib wayfindinglib backend; do
      COVERAGE=0 run_suite "$suite" "$@"
    done
    ;;
  astrometricslib|wayfindinglib|backend)
    run_suite "$SUITE" "$@"
    ;;
  *)
    echo "Usage: $0 [all|astrometricslib|wayfindinglib|backend] [pytest args...]" >&2
    exit 2
    ;;
esac
