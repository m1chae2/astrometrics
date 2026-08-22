#!/usr/bin/env bash
set -euo pipefail

# verify_deb.sh
# Verifies the contents of the generated .deb package.
# Usage: ./build/linux/verify_deb.sh

ROOT_DIR="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT_DIR"

DEB_FILE=$(ls dist/*with-backend.deb 2>/dev/null || ls dist/*.deb 2>/dev/null | head -n 1)

if [ -z "$DEB_FILE" ]; then
  echo "Error: No .deb file found in dist/"
  exit 1
fi

echo "=== Verifying contents of $DEB_FILE ==="
echo "Full package listing (first 50 lines):"
dpkg-deb -c "$DEB_FILE" | head -n 50 || true

echo ""
echo "=== Checking for /opt/astrometrics ==="
set +o pipefail
if dpkg-deb -c "$DEB_FILE" | grep -q "/opt/astrometrics"; then
  set -o pipefail
  echo "✓ /opt/astrometrics found in package"
  dpkg-deb -c "$DEB_FILE" | grep "/opt/astrometrics" | head -n 20 || true
else
  set -o pipefail
  echo "✗ ERROR: /opt/astrometrics NOT found in package!"
  echo "This will cause installation failures."
  exit 1
fi

echo "✓ Verification complete."
