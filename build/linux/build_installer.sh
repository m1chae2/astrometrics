#!/usr/bin/env bash
# Bash script to fully build and package Astrometrics for Ubuntu/Linux
# This script performs a clean build of both frontend and backend, then creates a .deb installer.
# Usage: ./build/linux/build_installer.sh

set -e # Exit on error

# Get the script directory and project root
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
cd "$PROJECT_ROOT"

echo "========================================"
echo "Astrometrics: Linux Build & Package (.deb)"
echo "========================================"

# 1. Prerequisite Checks
echo "[1/8] Checking prerequisites..."
if ! command -v node &> /dev/null; then
    echo "ERROR: Node.js not found. Please install Node.js."
    exit 1
fi
if ! command -v python3 &> /dev/null; then
    echo "ERROR: Python 3 not found. Please install Python 3."
    exit 1
fi

# 2. Node Dependencies
echo "[2/8] Installing Node.js dependencies..."
npm install
if [ $? -ne 0 ]; then
    echo "ERROR: npm install failed."
    exit 1
fi

# 3. Python Environment & PyInstaller
echo "[3/8] Ensuring Python environment and PyInstaller..."
if [ ! -d ".venv" ]; then
    echo "Virtual environment not found. Creating..."
    python3 -m venv .venv
fi

source .venv/bin/activate
python3 -m pip install --upgrade pip
pip install -e .
pip install pyinstaller  # Ensure pyinstaller is available for building the backend

# 4. Generate/Convert Icon (Ensure PNG is ready)
echo "[4/8] Ensuring application icons are ready..."
if [ ! -f "assets/orbit.png" ]; then
    echo "WARNING: assets/orbit.png not found. Please ensure it exists for Linux builds."
fi

# 5. Frontend Build (Vite)
echo "[5/8] Building frontend (Vite)..."
npm run build
if [ $? -ne 0 ]; then
    echo "ERROR: Frontend build failed."
    exit 1
fi

# 6. Backend Build
echo "[6/8] Building backend binary (PyInstaller)..."
# We reuse the backend.spec which we fixed for Windows, it's mostly cross-platform.
pyinstaller backend/backend.spec --noconfirm --clean
if [ $? -ne 0 ]; then

    echo "ERROR: Backend build failed."
    exit 1
fi

# 7. Backend Sanity Check
echo "[7/8] Verifying backend binary..."
BACKEND_EXE="dist/backend/backend"
if [ ! -f "$BACKEND_EXE" ]; then
    echo "ERROR: Backend binary not found at $BACKEND_EXE"
    exit 1
fi

# Start the backend in the background
echo "Starting backend sanity check on port 5005..."
export ASTROMETRICS_PORT=5005
$BACKEND_EXE &
BACKEND_PID=$!

# Wait for backend to initialize
MAX_RETRIES=60
STARTED=0
for ((i=1; i<=MAX_RETRIES; i++)); do
    if curl -s http://127.0.0.1:5005/ > /dev/null; then
        echo "SUCCESS: Backend verified and responding!"
        STARTED=1
        break
    fi
    sleep 1
done

# Cleanup the test process
kill $BACKEND_PID || true

if [ $STARTED -eq 0 ]; then
    echo "ERROR: Backend sanity check failed. The binary was created but failed to start or respond."
    exit 1
fi

# 8. Final Packaging (Electron Forge)
echo "[8/8] Creating .deb installer (Electron Forge)..."
npx electron-forge make --platform=linux --arch=x64

if [ $? -eq 0 ]; then
    echo ""
    echo "========================================"
    echo "SUCCESS: Packaging complete!"
    echo "Installers are located in: $PROJECT_ROOT/out/make/deb/x64/"
    echo "========================================"
else
    echo ""
    echo "ERROR: Packaging failed during the final step."
    exit 1
fi
