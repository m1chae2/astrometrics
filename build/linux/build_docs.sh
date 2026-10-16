#!/usr/bin/env bash
# ==============================================================================
# Purpose: Build script for Astrometrics Sphinx documentation suite.
# Builds the HTML site, then checks that MathJax can draw every equation on
# it (build/documentation/math_check/), since the browser, not Sphinx, draws
# them. The check needs Node.js; without it the script warns and skips it.
# Usage: ./build/linux/build_docs.sh [--clean]
# ==============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

DOCS_DIR="${REPO_ROOT}/documentation"
BUILD_DIR="${DOCS_DIR}/_build/html"

# Prefer the local dev venv; fall back to PATH so this also runs unmodified
# in CI, which installs docs deps directly into the runner's Python rather
# than a ./.venv (see .github/workflows/docs.yml).
if [ -x "${REPO_ROOT}/.venv/bin/sphinx-build" ]; then
    SPHINX_BUILD="${REPO_ROOT}/.venv/bin/sphinx-build"
elif command -v sphinx-build >/dev/null 2>&1; then
    SPHINX_BUILD="$(command -v sphinx-build)"
else
    echo "Error: sphinx-build not found in ${REPO_ROOT}/.venv/bin/ or on PATH."
    echo "Please run: pip install -e .[docs]"
    exit 1
fi

if [ "${1:-}" == "--clean" ]; then
    echo "Cleaning previous documentation build artifacts..."
    rm -rf "${DOCS_DIR}/_build" "${DOCS_DIR}/api/generated"
fi

echo "Building Astrometrics Sphinx Documentation..."
# -W turns warnings into errors, matching CI. This keeps broken
# cross-references, missing autosummary stubs, and malformed directives from
# silently accumulating and only failing once pushed.
"${SPHINX_BUILD}" -W -b html "${DOCS_DIR}" "${BUILD_DIR}"

# Sphinx only copies each equation into the page; MathJax draws it in the
# reader's browser, so a broken equation never fails the build above.
MATH_CHECK_DIR="${REPO_ROOT}/build/documentation/math_check"
if command -v node >/dev/null 2>&1; then
    echo "Checking that MathJax can draw every equation..."
    if [ ! -d "${MATH_CHECK_DIR}/node_modules" ]; then
        npm ci --prefix "${MATH_CHECK_DIR}" --no-audit --no-fund
    fi
    node "${MATH_CHECK_DIR}/check_math.mjs" "${BUILD_DIR}"
else
    echo "Warning: Node.js not found; skipped the equation check (CI runs it)."
fi

echo "Documentation built successfully!"
echo "Main portal: file://${BUILD_DIR}/index.html"
