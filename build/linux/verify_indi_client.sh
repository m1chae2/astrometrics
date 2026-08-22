#!/usr/bin/env bash
# ==============================================================================
# Purpose: Verify that the real PyIndi binding (pyindi-client) is installed
# and active, not wayfindinglib's no-op stub fallback.
#
# Without pyindi-client, wayfindinglib.drivers.indi.pyindi_compatibility
# silently substitutes a stub whose connectServer() always returns False --
# so INDI hardware control quietly never connects to a real indiserver, with
# no error anywhere. That's exactly what happened when
# build/linux/setup_venv.sh forgot to install it: everything else worked, so
# nothing caught it until someone noticed the telescope just never showed as
# connected. Run this after setup_venv.sh, locally or in CI, to catch that
# class of bug immediately instead of by observation.
#
# Usage: ./build/linux/verify_indi_client.sh
# ==============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

if [ -x "${REPO_ROOT}/.venv/bin/python" ]; then
    PYTHON="${REPO_ROOT}/.venv/bin/python"
elif command -v python3 >/dev/null 2>&1; then
    PYTHON="$(command -v python3)"
else
    echo "Error: python not found in ${REPO_ROOT}/.venv/bin/ or on PATH."
    exit 1
fi

echo "Verifying pyindi-client is installed and active..."
"${PYTHON}" - <<'EOF'
import sys

try:
    import PyIndi as real_check  # noqa: F401  -- import error is the real signal, not the binding
except ImportError:
    print(
        "FAIL: pyindi-client is not installed -- 'import PyIndi' failed.\n"
        "  Install it with: pip install pyindi-client\n"
        "  (requires swig, libindi-dev, libcfitsio-dev, libnova-dev,\n"
        "  libdbus-1-dev, libglib2.0-dev; see build/linux/setup_venv.sh)"
    )
    sys.exit(1)

from wayfindinglib.drivers.indi.pyindi_compatibility import PyIndi, PyIndiStub

if isinstance(PyIndi, PyIndiStub):
    print(
        "FAIL: wayfindinglib is using the no-op PyIndiStub fallback, not the\n"
        "  real pyindi-client binding, even though pyindi-client imports\n"
        "  cleanly on its own. INDI hardware control will silently never\n"
        "  connect to a real indiserver."
    )
    sys.exit(1)

print("OK: pyindi-client is installed and wayfindinglib is using the real PyIndi binding.")
EOF
