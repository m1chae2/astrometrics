#!/usr/bin/env bash
# ==============================================================================
# Purpose: Build script for Astrometrics Sphinx documentation suite.
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

echo "Documentation built successfully!"
echo "Main portal: file://${BUILD_DIR}/index.html"
