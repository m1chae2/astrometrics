#!/usr/bin/env bash
set -euo pipefail

# setup_siril_gpu.sh
# Sets up the GPU path of the Flatpak Siril on Linux from pinned pieces: the
# Flatpak permission overrides, the CUDA libraries in ~/cuda_libs and the two
# private library folders, the GPU build of ONNX Runtime in Siril's Python,
# the GraXpert and StarNet2 wrapper scripts, and the settings that name them.
# The work is done by build/linux/siril_gpu/siril_gpu_setup.py; the pins are
# in build/linux/siril_gpu/siril_gpu_pins.py.
#
# Usage:
#   ./build/linux/setup_siril_gpu.sh --check     - report each piece, change nothing
#   ./build/linux/setup_siril_gpu.sh --dry-run   - report, then print what a run would do
#   ./build/linux/setup_siril_gpu.sh             - set up what is missing
#   ./build/linux/setup_siril_gpu.sh --accept-nvidia-license
#                                                - the same, accepting NVIDIA's CUDA and cuDNN
#                                                  licenses without asking (no terminal)
#
# Run it as your own user, not with sudo: everything it changes is in your
# home folder. Without an NVIDIA graphics card or without the Flatpak Siril
# it says so and exits without error. Windows needs none of this: Siril's
# Windows installer sets up its own tools.

ROOT_DIR="$(cd "$(dirname "$0")/../.." && pwd)"
SETUP_SCRIPT="$ROOT_DIR/build/linux/siril_gpu/siril_gpu_setup.py"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

err()  { echo "[setup_siril_gpu] ERROR: $*" >&2; }
die()  { err "$*"; exit 1; }

# The setup program uses Python 3.14 syntax, like the rest of the code.
is_supported_python() {
  "$1" -c "import sys; sys.exit(0 if sys.version_info >= (3, 14) else 1)" 2>/dev/null
}

# Prefer the project's virtual environment; it has pip, which the downloads
# need. Fall back to a system Python 3.14.
find_python() {
  for candidate in "$ROOT_DIR/.venv/bin/python" python3.14 python3; do
    if command -v "$candidate" >/dev/null 2>&1 && is_supported_python "$candidate"; then
      command -v "$candidate"; return 0
    fi
  done
  return 1
}

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

if [ "$(id -u)" = "0" ]; then
  die "Run this as your own user, not with sudo. It changes only files in your home folder."
fi

[ "$(uname -s)" = "Linux" ] || die "This setup is for Linux. On Windows, Siril's installer needs none of it."

PYTHON=$(find_python) || die "No Python 3.14+ found. Run ./build/linux/setup_venv.sh first."

# --help is passed through, so the Python program prints every option.
exec "$PYTHON" "$SETUP_SCRIPT" "$@"
