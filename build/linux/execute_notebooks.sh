#!/usr/bin/env bash
# ==============================================================================
# Purpose: Execute every tutorial notebook fresh, in place, against real
# local data, and save the resulting outputs. Run this locally -- wherever
# the FITS sample library actually lives -- before committing any change
# that touches a notebook or the library code it exercises. CI cannot do
# this itself (see build/documentation/check_notebook_outputs.py): the
# sample data is too large to ship to a hosted runner, so CI only verifies
# that whatever you committed here looks like a real, clean, top-to-bottom
# run -- it can't re-run it.
#
# Usage: ./build/linux/execute_notebooks.sh [path/to/one_notebook.ipynb]
#   With no argument, executes every notebook under documentation/notebooks/.
#   With a path, executes just that one notebook (faster iteration).
# ==============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
NOTEBOOKS_DIR="${REPO_ROOT}/documentation/notebooks"

if [ -x "${REPO_ROOT}/.venv/bin/jupyter" ]; then
    JUPYTER="${REPO_ROOT}/.venv/bin/jupyter"
elif command -v jupyter >/dev/null 2>&1; then
    JUPYTER="$(command -v jupyter)"
else
    echo "Error: jupyter not found in ${REPO_ROOT}/.venv/bin/ or on PATH."
    echo "Please run: pip install -e .[docs]"
    exit 1
fi

if [ $# -ge 1 ]; then
    mapfile -t NOTEBOOKS < <(printf '%s\n' "$1")
else
    mapfile -t NOTEBOOKS < <(find "${NOTEBOOKS_DIR}" -name "*.ipynb" -not -path "*/.ipynb_checkpoints/*" | sort)
fi

if [ "${#NOTEBOOKS[@]}" -eq 0 ]; then
    echo "No notebooks found under ${NOTEBOOKS_DIR}."
    exit 1
fi

echo "Executing ${#NOTEBOOKS[@]} notebook(s)..."
failed=()
for nb in "${NOTEBOOKS[@]}"; do
    echo ""
    echo "=== ${nb#"${REPO_ROOT}"/} ==="
    if PYTHONPATH="${REPO_ROOT}" "${JUPYTER}" nbconvert \
        --to notebook --execute --inplace \
        --ExecutePreprocessor.timeout=600 \
        --ExecutePreprocessor.kernel_name=python3 \
        "${nb}"; then
        echo "OK"
    else
        echo "FAILED"
        failed+=("${nb#"${REPO_ROOT}"/}")
    fi
done

echo ""
if [ "${#failed[@]}" -gt 0 ]; then
    echo "${#failed[@]} notebook(s) failed to execute cleanly:"
    printf '  - %s\n' "${failed[@]}"
    echo ""
    echo "Fix the underlying issue, re-run this script, then git add the"
    echo "updated notebook(s) before committing."
    exit 1
fi

echo "All notebooks executed successfully. Review the diff and git add the"
echo "updated notebook(s) before committing."
