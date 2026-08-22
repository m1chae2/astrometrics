#!/bin/bash
# Simple wrapper to start astrometrics backend
# This script is copied to /opt/astrometrics/ during packaging
# and invoked by the wrapper at /usr/local/bin/astrometrics-backend

# Determine the directory where this script lives
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

# Activate virtual environment if it exists
if [ -f "$SCRIPT_DIR/venv/bin/activate" ]; then
    source "$SCRIPT_DIR/venv/bin/activate"
elif [ -f "$SCRIPT_DIR/.venv/bin/activate" ]; then
    source "$SCRIPT_DIR/.venv/bin/activate"
elif [ -f "$SCRIPT_DIR/../.venv/bin/activate" ]; then
    source "$SCRIPT_DIR/../.venv/bin/activate"
fi

# Run the high-level interface module
exec python3 -m backend.main_backend "$@"
