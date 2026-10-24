#!/usr/bin/env bash
# ==============================================================================
# Purpose: Lint the library documentation for style and Markdown mechanics.
#
# Runs Vale (prose: spelling, terms, units, status labels, voice) and
# markdownlint-cli2 (Markdown mechanics) over the files listed in
# .markdownlint-cli2.jsonc and the same set for Vale. The rules are explained
# in documentation/Documentation_Conventions.md.
#
# Usage: build/documentation/lint_docs.sh [--fix]
#   --fix   let markdownlint repair what it can (list markers, blank lines).
#
# Needs: vale (in the dev extra: pip install -e ".[dev]") and npx (Node.js).
# CI installs both.
# ==============================================================================
set -euo pipefail
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "${REPO_ROOT}"

VALE="${VALE:-$(command -v vale || echo "${REPO_ROOT}/.venv/bin/vale")}"
MARKDOWNLINT="${MARKDOWNLINT:-npx --yes markdownlint-cli2@0.23.3}"

mapfile -t FILES < <(git ls-files \
    'astrometricslib/**/README.md' \
    ':(glob)documentation/*.md' \
    ':(glob)documentation/how_to/*.md' \
    'documentation/library_design/*_Library_*.md' \
    'documentation/library_design/Astrometrics_Quality_Measurements.md' \
    'documentation/library_design/Wayfinding_Quality_Judgment.md' \
    'documentation/user_interface/**/*.md' \
    'documentation/library_design/pipelines/*.md' \
    'documentation/library_design/validation/*.md')

status=0
echo "== markdownlint =="
if [[ "${1:-}" == "--fix" ]]; then
    ${MARKDOWNLINT} --fix || status=1
else
    ${MARKDOWNLINT} || status=1
fi

echo "== vale =="
if [[ -x "${VALE}" ]]; then
    "${VALE}" --no-wrap "${FILES[@]}" || status=1
else
    echo "vale not found; install it with: .venv/bin/pip install vale==3.22.0.0 (or the dev extra)" >&2
    status=1
fi
exit "${status}"
