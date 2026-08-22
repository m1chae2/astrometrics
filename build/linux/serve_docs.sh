#!/usr/bin/env bash
# ==============================================================================
# Purpose: Serve the built Astrometrics Sphinx documentation locally.
# Usage: ./build/linux/serve_docs.sh [--port PORT]
# ==============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

VENV_PYTHON="${REPO_ROOT}/.venv/bin/python"
DOCS_DIR="${REPO_ROOT}/documentation"
BUILD_DIR="${DOCS_DIR}/_build/html"
PORT=8000

while [ $# -gt 0 ]; do
    case "$1" in
        --port)
            PORT="$2"
            shift 2
            ;;
        *)
            echo "Unknown argument: $1"
            exit 1
            ;;
    esac
done

if [ ! -f "${BUILD_DIR}/index.html" ]; then
    echo "Error: no built documentation found at ${BUILD_DIR}."
    echo "Please run: ./build/linux/build_docs.sh"
    exit 1
fi

echo "Serving Astrometrics documentation at http://localhost:${PORT}"
"${VENV_PYTHON}" -m http.server --directory "${BUILD_DIR}" "${PORT}"
