#!/bin/bash
# Checks an agent runs in a checkout while it works. Usage:
#   check.sh <checkout> tests <pytest targets...>   targeted pytest, -n 3, in a heavy-work slot
#   check.sh <checkout> lint                        ruff check and format check of changed files, lint-imports
#   check.sh <checkout> format                      ruff format and safe fixes on changed .py files
#   check.sh <checkout> doctests                    the documentation tests only (quick, no slot)
#   check.sh <checkout> docs                        the documentation tests and lint_docs.sh (Vale, markdownlint)
#   check.sh <checkout> golden                      the M 13 golden suite
# The full suites are left to continuous integration (see QUALITY_METRICS_PLAN.md section 9.1).
source "$(dirname "${BASH_SOURCE[0]}")/common.sh" "$1" || exit 2
mode=$2
shift 2

DOC_TESTS=(
  astrometricslib/test/test_documentation_rules.py
  astrometricslib/test/test_documentation_style.py
  astrometricslib/test/test_documentation_notation.py
  astrometricslib/test/test_documentation_site.py
  wayfindinglib/test/test_documentation_papers.py
  astrometricslib/test/test_architecture_doc_claims.py
  astrometricslib/test/test_validation_register.py
  astrometricslib/pipelines/test/test_pipeline_doc_parity.py
)

changed_python() {
  git diff --name-only HEAD -- '*.py'
  git ls-files --others --exclude-standard -- '*.py'
}

case "$mode" in
  tests)
    [ $# -gt 0 ] || { echo "give pytest targets" >&2; exit 2; }
    heavy "$PY/pytest" "$@" -n 3 -q -p no:cacheprovider ;;
  lint)
    files=$(changed_python)
    rc=0
    if [ -n "$files" ]; then
      "$PY/ruff" check $files || rc=1
      "$PY/ruff" format --check $files || rc=1
    fi
    "$PY/lint-imports" || rc=1
    exit $rc ;;
  format)
    files=$(changed_python)
    [ -n "$files" ] && "$PY/ruff" format $files && "$PY/ruff" check --fix $files
    exit 0 ;;
  doctests)
    "$PY/pytest" "${DOC_TESTS[@]}" -q -p no:cacheprovider ;;
  docs)
    heavy "$PY/pytest" "${DOC_TESTS[@]}" -n 3 -q -p no:cacheprovider || exit 1
    build/documentation/lint_docs.sh ;;
  golden)
    heavy "$PY/pytest" -m golden -q -p no:cacheprovider astrometricslib ;;
  *)
    echo "mode must be tests, lint, format, doctests, docs or golden" >&2
    exit 2 ;;
esac
