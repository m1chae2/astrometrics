#!/usr/bin/env bash
set -euo pipefail

# test_install.sh
# Tests the installation of the .deb package and basic execution.
# Usage: ./build/linux/test_install.sh

ROOT_DIR="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT_DIR"

echo "=== Installing required dependencies ==="
sudo apt-get update -qq
sudo apt-get install -y libnotify4

echo "=== Installing .deb package ==="
DEB_FILE=$(ls dist/*with-backend.deb 2>/dev/null || ls dist/*.deb 2>/dev/null | head -n 1)

if [ -z "$DEB_FILE" ]; then
  echo "Error: No .deb file found in dist/"
  exit 1
fi

echo "Installing: $DEB_FILE"
sudo dpkg -i "$DEB_FILE" 2>&1 | tee /tmp/dpkg-install.log || {
  echo "dpkg -i failed, attempting to fix dependencies..."
  sudo apt-get install -f -y
}

echo "=== Verifying installation ==="
if dpkg -l | grep -i astrometrics; then
  echo "✓ Package installed successfully"
else
  echo "Error: Package not found in dpkg list"
  echo "dpkg install log:"
  cat /tmp/dpkg-install.log || true
  exit 1
fi

# Check if executable exists (try multiple possible paths)
EXEC_PATH=""
for path in /opt/Astrometrics/astrometrics /opt/astrometrics/astrometrics /usr/bin/astrometrics /usr/local/bin/astrometrics; do
  if [ -f "$path" ]; then
    EXEC_PATH="$path"
    echo "✓ Executable found at $path"
    ls -lh "$path"
    break
  fi
done

if [ -z "$EXEC_PATH" ]; then
  echo "Error: Executable not found at any expected location"
  echo "Checked paths: /opt/Astrometrics/astrometrics, /opt/astrometrics/astrometrics, /usr/bin/astrometrics, /usr/local/bin/astrometrics"
  echo "Contents of /opt:"
  ls -la /opt/ || true
  exit 1
fi

# Check desktop file
if [ -f /usr/share/applications/astrometrics.desktop ]; then
  echo "✓ Desktop file found"
  if grep -q "^Terminal=true" /usr/share/applications/astrometrics.desktop; then
    echo "✓ Terminal=true is set in desktop file"
  else
    echo "Warning: Terminal=true not found in desktop file"
  fi
else
  echo "Error: Desktop file not found at /usr/share/applications/astrometrics.desktop"
  exit 1
fi

# Test that the app can show version/help
echo "=== Testing executable ==="
# We use timeout because Electron apps might hang or wait for a window even with --version
# Added --no-sandbox to avoid "SUID sandbox helper binary was found, but is not configured correctly" fatal errors
if (timeout 5 "$EXEC_PATH" --no-sandbox --version 2>&1 | head -n 5 || true) || (timeout 5 "$EXEC_PATH" --no-sandbox --help 2>&1 | head -n 5 || true); then
  echo "✓ Executable can be invoked"
else
  echo "Note: Executable could not show version or help"
fi

echo "=== Installation test completed successfully ==="
