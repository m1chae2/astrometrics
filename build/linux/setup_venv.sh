#!/usr/bin/env bash
set -euo pipefail

# setup_venv.sh
# Creates a Python virtual environment at <repo-root>/.venv and installs the
# Astrometrics backend package (and its dependencies) from backend/pyproject.toml.
#
# Usage:
#   ./build/linux/setup_venv.sh              - create/update .venv then exit
#   ./build/linux/setup_venv.sh --force      - delete existing .venv first then recreate
#
# The script is idempotent when run without --force: it will skip venv creation
# if .venv already exists and only re-run pip install.

ROOT_DIR="$(cd "$(dirname "$0")/../.." && pwd)"
VENV_DIR="$ROOT_DIR/.venv"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

log()  { echo "[setup_venv] $*"; }
err()  { echo "[setup_venv] ERROR: $*" >&2; }
die()  { err "$*"; exit 1; }

# ---------------------------------------------------------------------------
# Find a suitable Python 3 interpreter (not the venv itself)
# ---------------------------------------------------------------------------

# Accept only 3.14+. The codebase uses PEP 758 parenthesis-less
# multi-exception `except A, B:`, which is a SyntaxError on 3.13 and
# earlier, so an older interpreter builds a venv that cannot import the
# package at all -- a confusing failure well after setup appears to succeed.
is_supported_python() {
  "$1" -c "import sys; sys.exit(0 if sys.version_info >= (3, 14) else 1)" 2>/dev/null
}

find_python() {
  # Prefer an explicit override from the environment.
  if [ -n "${PYTHON_BIN:-}" ] && [ -x "$PYTHON_BIN" ]; then
    if is_supported_python "$PYTHON_BIN"; then
      echo "$PYTHON_BIN"; return 0
    fi
    return 1
  fi

  for candidate in python3.14 python3 python; do
    if command -v "$candidate" >/dev/null 2>&1; then
      if is_supported_python "$candidate"; then
        echo "$(command -v "$candidate")"; return 0
      fi
    fi
  done

  return 1
}

# ---------------------------------------------------------------------------
# Parse arguments
# ---------------------------------------------------------------------------

FORCE=0
for arg in "$@"; do
  case "$arg" in
    --force|-f) FORCE=1 ;;
    --help|-h)
      cat <<'EOF'
Usage: ./build/linux/setup_venv.sh [OPTIONS]

Creates the Python virtual environment at <repo-root>/.venv and installs
the Astrometrics backend package from backend/pyproject.toml.

Options:
  --force, -f    Delete the existing .venv before recreating it
  --help,  -h    Show this help message
EOF
      exit 0
      ;;
    *)
      die "Unknown argument: $arg"
      ;;
  esac
done

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

log "Repository root: $ROOT_DIR"

# Seed the local configuration from the tracked template. astrometrics.config
# holds machine-specific paths and an API key, so it is gitignored; without
# this a fresh clone (and CI) would start with no configuration at all.
CONFIG_FILE="$ROOT_DIR/astrometricslib/astrometrics.config"
CONFIG_TEMPLATE="$ROOT_DIR/astrometricslib/astrometrics.config.example"
if [ ! -f "$CONFIG_FILE" ] && [ -f "$CONFIG_TEMPLATE" ]; then
  log "No astrometrics.config found; seeding one from astrometrics.config.example"
  cp "$CONFIG_TEMPLATE" "$CONFIG_FILE"
  log "Edit $CONFIG_FILE to set frames_path and, if using the online solver, api_key"
fi

# Locate system Python.
SYSTEM_PYTHON=$(find_python) || die "No Python 3.14+ interpreter found (required: PEP 758 except syntax). Install python3.14, or set PYTHON_BIN to one, and try again."
log "Using Python: $SYSTEM_PYTHON ($($SYSTEM_PYTHON --version 2>&1))"

# Optionally blow away the existing venv.
if [ "$FORCE" = "1" ] && [ -d "$VENV_DIR" ]; then
  log "--force specified: removing existing $VENV_DIR"
  rm -rf "$VENV_DIR"
fi

# Create the virtual environment if it does not already exist.
if [ ! -d "$VENV_DIR" ]; then
  log "Creating virtual environment at $VENV_DIR ..."
  "$SYSTEM_PYTHON" -m venv --system-site-packages "$VENV_DIR"
  log "Virtual environment created."
else
  log "Virtual environment already exists at $VENV_DIR — skipping creation."
fi

VENV_PYTHON="$VENV_DIR/bin/python"

# Verify the venv python is functional.
"$VENV_PYTHON" -c "import sys; assert sys.prefix != sys.base_prefix, 'Not inside a venv!'" \
  || die "Virtual environment at $VENV_DIR appears broken. Run with --force to recreate it."

# Upgrade pip and install build backend dependencies inside the venv before installing editable package.
log "Upgrading pip inside venv and installing hatchling, editables & build dependencies..."
"$VENV_PYTHON" -m pip install --quiet --upgrade pip hatchling editables setuptools_scm Cython numpy

# Install the project in editable mode so local source is used at runtime.
# pyproject.toml declares all dependencies.
PYPROJECT="$ROOT_DIR/pyproject.toml"
if [ ! -f "$PYPROJECT" ]; then
  die "pyproject.toml not found at $PYPROJECT"
fi

log "Installing project from $ROOT_DIR (editable)..."
"$VENV_PYTHON" -m pip install --no-build-isolation -e "$ROOT_DIR"

# pyindi-client is the real SWIG binding to the INDI client C++ library.
# Without it, wayfindinglib.drivers.indi.pyindi_compatibility silently
# substitutes a stub whose connectServer() always returns False, so
# telescope control quietly never connects to a real indiserver -- no
# error, just permanent "Disconnected" in the UI. It needs system
# packages (swig, libindi-dev, libcfitsio-dev, libnova-dev, libdbus-1-dev,
# libglib2.0-dev; see .github/actions/setup-python-env/action.yml) that
# aren't guaranteed on every dev machine, so a failed install here is a
# warning, not a fatal error -- the stub keeps everything else working.
log "Installing pyindi-client (real INDI hardware control)..."
"$VENV_PYTHON" -m pip install pyindi-client || log "WARNING: pyindi-client install failed; INDI hardware control will use the no-op stub (see wayfindinglib/drivers/indi/pyindi_compatibility.py)."

if [ -f "$ROOT_DIR/astrometricslib/pipelines/spectroscopy/_extractor_c.c" ]; then
  log "Compiling CPython C extension (_extractor_c.c)..."
  PYTHON_INC="$("$VENV_PYTHON" -c "import sysconfig; print(sysconfig.get_path('include'))")"
  NUMPY_INC="$("$VENV_PYTHON" -c "import numpy; print(numpy.get_include())")"
  gcc -O3 -shared -fPIC -I"$PYTHON_INC" -I"$NUMPY_INC" \
      "$ROOT_DIR/astrometricslib/pipelines/spectroscopy/_extractor_c.c" \
      -o "$ROOT_DIR/astrometricslib/pipelines/spectroscopy/_extractor_c.so" || {
        log "WARNING: C extension compilation failed; pure-Python fallback will be used."
      }
fi

log "Verifying package installation..."
"$VENV_PYTHON" - <<'EOF'
import sys
try:
    import astrometricslib
    import wayfindinglib
    print(f"  ✓ astrometricslib {astrometricslib.__version__}")
    print(f"  ✓ wayfindinglib {wayfindinglib.__version__}")
except Exception as e:
    print(f"  ✗ Verification failed: {e}", file=sys.stderr)
    sys.exit(1)
EOF

log ""
log "Setup complete. Virtual environment is ready at:"
log "  $VENV_DIR"
log ""
log "To activate manually:"
log "  source $VENV_DIR/bin/activate"
