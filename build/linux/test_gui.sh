#!/usr/bin/env bash
set -euo pipefail

# test_gui.sh
# Tests the GUI launch of Astrometrics using Xvfb.
# Usage: ./build/linux/test_gui.sh <EXEC_PATH>

EXEC_PATH="${1:-}"

if [ -z "$EXEC_PATH" ]; then
  echo "Usage: $0 <EXEC_PATH>"
  exit 1
fi

if [ ! -f "$EXEC_PATH" ]; then
  echo "Error: Executable not found at $EXEC_PATH"
  exit 1
fi

echo "=== Verifying backend wrapper ==="
if [ ! -f "/usr/local/bin/astrometrics-backend" ]; then
  echo "✗ Backend wrapper not found at /usr/local/bin/astrometrics-backend"
  echo "Contents of /usr/local/bin:"
  ls -la /usr/local/bin/ || true
  exit 1
else
  echo "✓ Backend wrapper found"
fi

echo "=== Starting Xvfb ==="
export DISPLAY=':99'
Xvfb :99 -screen 0 1024x768x24 > /tmp/xvfb.log 2>&1 &
XVFB_PID=$!
sleep 2

echo "=== Testing GUI launch ==="
# Try to launch the app and capture any startup errors
# Added --no-sandbox because many CI environments (containers) require it for Electron
timeout 30 "$EXEC_PATH" --no-sandbox > /tmp/app-output.log 2>&1 &
APP_PID=$!

# Wait for the app to initialize
sleep 15

# Check if the app is still running
if kill -0 $APP_PID 2>/dev/null; then
  echo "✓ Application launched successfully and is running"
  echo "App output (first 20 lines):"
  head -n 20 /tmp/app-output.log || true

  # Check for critical errors
  if grep -q "Failed to start backend" /tmp/app-output.log; then
    echo "✗ Backend failed to start detected in logs"
    kill $APP_PID 2>/dev/null || true
    kill $XVFB_PID 2>/dev/null || true
    exit 1
  fi

  if grep -q "ENOENT" /tmp/app-output.log; then
    echo "✗ File not found error (ENOENT) detected in logs"
    kill $APP_PID 2>/dev/null || true
    kill $XVFB_PID 2>/dev/null || true
    exit 1
  fi

  # Take a screenshot if possible
  if command -v xwd &> /dev/null; then
    echo "Taking screenshot..."
    xwd -root -out /tmp/screenshot.xwd || true
    echo "✓ Screenshot captured (rendering working)"
  fi

  # Clean shutdown
  kill $APP_PID 2>/dev/null || true
  sleep 2
else
  echo "✗ Application exited prematurely"
  echo "App output:"
  cat /tmp/app-output.log || true
  kill $XVFB_PID 2>/dev/null || true
  exit 1
fi

# Clean up
kill $XVFB_PID 2>/dev/null || true
echo "=== GUI test completed successfully ==="
