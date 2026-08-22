#!/usr/bin/env bash
set -euo pipefail

# start_full.sh
# Convenience script to start both backend and frontend for Astrometrics
# Usage:
#   ./scripts/start_full.sh              - Start backend and GUI
#   ./scripts/start_full.sh --install    - Install dependencies, then start
#   ./scripts/start_full.sh help         - Show help

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"

echo "=========================================="
echo "Astrometrics - Full Stack Launcher"
echo "=========================================="
echo ""

# Parse arguments
INSTALL=0
if [ $# -gt 0 ]; then
  case "$1" in
    --install)
      INSTALL=1
      ;;
    help|--help|-h)
      cat <<'EOF'
Usage: ./scripts/start_full.sh [OPTIONS]

Options:
  --install    Install/reinstall dependencies before starting
  help         Show this help message

This script starts both the backend and frontend for Astrometrics.
The backend runs in the background, and the frontend (Electron + Vite) runs in the foreground.

To stop the application, press Ctrl+C.
To check logs, see: .run_logs/

EOF
      exit 0
      ;;
  esac
fi

# Install if requested or if .venv doesn't exist
if [ $INSTALL -eq 1 ] || [ ! -d "$ROOT_DIR/.venv" ]; then
  echo "[1/3] Installing dependencies..."
  "$ROOT_DIR/build/linux/run_backend.sh" install
  echo ""
fi

# Start backend in background
echo "[2/3] Starting backend..."
"$ROOT_DIR/build/linux/run_backend.sh" start
sleep 2  # Give backend time to start
echo ""

# Start frontend in foreground
echo "[3/3] Starting frontend (Electron + Vite)..."
echo ""
echo "=========================================="
echo "Application is starting..."
echo "Backend: http://127.0.0.1:5000"
echo "Frontend: http://127.0.0.1:5173"
echo ""
echo "Press Ctrl+C to stop the application"
echo "=========================================="
echo ""

# Trap Ctrl+C to gracefully stop both backend and frontend
trap 'echo ""; echo "Stopping Astrometrics..."; "$ROOT_DIR/build/linux/run_backend.sh" stop; exit 0' INT TERM

# Start frontend in foreground (this blocks)
"$ROOT_DIR/build/linux/run_astrometrics.sh" foreground
