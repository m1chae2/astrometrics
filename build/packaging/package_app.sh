#!/usr/bin/env bash

set -euo pipefail

# Usage: ./package_app.sh [--arch x64|arm64|armv7l] [--skip-backend]
# Default arch: x64

ARCH="x64"
SKIP_BACKEND=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --arch)
      ARCH="$2"
      shift 2
      ;;
    --arch=*)
      ARCH="${1#*=}"
      shift
      ;;
    --skip-backend)
      SKIP_BACKEND=1
      shift
      ;;
    -h|--help)
      echo "Usage: $0 [--arch x64|arm64|armv7l] [--skip-backend]"
      exit 0
      ;;
    *)
      echo "Unknown arg: $1"
      echo "Usage: $0 [--arch x64|arm64|armv7l] [--skip-backend]"
      exit 1
      ;;
  esac
done

echo "Packaging Astrometrics for linux/$ARCH"

ROOT_DIR="$(cd "$(dirname "$0")/../.." && pwd)"
DIST_DIR="$ROOT_DIR/dist"

mkdir -p "$DIST_DIR"

# Preserve backend wheel if it exists (vite build will empty dist/)
TEMP_WHEEL_DIR=$(mktemp -d)
if ls "$DIST_DIR"/astrometrics-*.whl 1> /dev/null 2>&1; then
  echo "Preserving existing backend wheel before frontend build..."
  cp "$DIST_DIR"/astrometrics-*.whl "$TEMP_WHEEL_DIR/"
fi

echo "1) Installing node dependencies (will use package.json)..."
npm install --no-audit --no-fund

echo "1.5) Fetching Aladin Lite assets..."
bash "$ROOT_DIR/build/packaging/fetch_aladin.sh"

echo "2) Building frontend (Vite)..."
npm run build

# Restore backend wheel if it was preserved
if ls "$TEMP_WHEEL_DIR"/astrometrics-*.whl 1> /dev/null 2>&1; then
  echo "Restoring backend wheel after frontend build..."
  cp "$TEMP_WHEEL_DIR"/astrometrics-*.whl "$DIST_DIR/"
fi
rm -rf "$TEMP_WHEEL_DIR"

if [ "$SKIP_BACKEND" -eq 0 ]; then
  echo "3) Building backend wheel..."
  # Replaced package_backend.sh call
  (cd "$ROOT_DIR" && python3 -m build)
else
  echo "3) Skipping backend build as requested"
fi

echo "4) Running electron-forge package and make (arch=$ARCH)"
# Ensure electron-forge CLI is available via npx (config at project root)
npx electron-forge package
npx electron-forge make --platform=linux --arch="$ARCH"

echo "5) Locating generated .deb"
DEB_DIR="$ROOT_DIR/out/make/deb/$ARCH"
DEB_FILE=""
if [ -d "$DEB_DIR" ]; then
  DEB_FILE=$(find "$DEB_DIR" -maxdepth 2 -type f -name '*.deb' | head -n 1 || true)
fi

if [ -n "$DEB_FILE" ]; then
  echo "Found DEB: $DEB_FILE"
else
  echo "No DEB package found in $DEB_DIR"
  # continue — sometimes electron-forge writes to a different path
  DEB_FILE=$(find "$ROOT_DIR/out" -type f -name '*.deb' | head -n 1 || true)
  if [ -n "$DEB_FILE" ]; then
    echo "Found alternate DEB: $DEB_FILE"
  else
    echo "No DEB could be located. Exiting." >&2
    exit 1
  fi
fi

# Move original deb to dist for processing
DEB_BASENAME=$(basename "$DEB_FILE")
ORIG_DEB_PATH="$DEB_FILE"
mkdir -p "$DIST_DIR"
mv "$ORIG_DEB_PATH" "$DIST_DIR/"
ORIG_DEB_PATH="$DIST_DIR/$DEB_BASENAME"
echo "Original DEB moved to $ORIG_DEB_PATH"

# Try to embed backend wheel and maintainer scripts into the .deb so installing
# the package also stages and configures the backend under /opt/astrometrics.
WHEEL_FILE=$(ls "$ROOT_DIR"/dist/astrometrics-*.whl 2>/dev/null || true)
if [ -n "$WHEEL_FILE" ]; then
  echo "Embedding backend wheel and maintainer scripts into DEB"
  TMPDIR=$(mktemp -d)
  # extract data and control
  dpkg-deb -x "$ORIG_DEB_PATH" "$TMPDIR"
  mkdir -p "$TMPDIR/DEBIAN"
  dpkg-deb -e "$ORIG_DEB_PATH" "$TMPDIR/DEBIAN"

  # ensure /opt/astrometrics is present and copy wheel there
  echo "Creating directory structure in $TMPDIR/opt/astrometrics"
  mkdir -p "$TMPDIR/opt/astrometrics"

  echo "Copying wheel file: $WHEEL_FILE"
  cp -v "$WHEEL_FILE" "$TMPDIR/opt/astrometrics/"
  echo "Copied wheel to /opt/astrometrics/"

  # copy start script if it exists in backend/
  if [ -f "$ROOT_DIR/backend/start_astrometrics.sh" ]; then
    echo "Copying start script from $ROOT_DIR/backend/start_astrometrics.sh"
    cp -v "$ROOT_DIR/backend/start_astrometrics.sh" "$TMPDIR/opt/astrometrics/"
    chmod +x "$TMPDIR/opt/astrometrics/start_astrometrics.sh"
    echo "Copied start script to /opt/astrometrics/start_astrometrics.sh"
  else
    echo "Warning: start script not found at $ROOT_DIR/backend/start_astrometrics.sh"
  fi

  # List contents to verify
  echo "Contents of $TMPDIR/opt/astrometrics before packaging:"
  ls -la "$TMPDIR/opt/astrometrics/" || true
  echo "Verifying directory exists:"
  test -d "$TMPDIR/opt/astrometrics" && echo "✓ Directory exists" || echo "✗ Directory missing!"
  echo "Verifying files exist:"
  test -f "$TMPDIR/opt/astrometrics/"*.whl && echo "✓ Wheel file exists" || echo "✗ Wheel file missing!"
  test -f "$TMPDIR/opt/astrometrics/start_astrometrics.sh" && echo "✓ Start script exists" || echo "✗ Start script missing"

  # copy maintainer scripts from our scripts/ folder if present
  if [ -f "$ROOT_DIR/build/packaging/postinst.sh" ]; then
    cp "$ROOT_DIR/build/packaging/postinst.sh" "$TMPDIR/DEBIAN/postinst"
    chmod 755 "$TMPDIR/DEBIAN/postinst"
  fi
  if [ -f "$ROOT_DIR/build/packaging/postrm.sh" ]; then
    cp "$ROOT_DIR/build/packaging/postrm.sh" "$TMPDIR/DEBIAN/postrm"
    chmod 755 "$TMPDIR/DEBIAN/postrm"
  fi

  # Ensure DEBIAN/control has a Maintainer field to avoid dpkg-deb warnings
  if ! grep -q "^Maintainer:" "$TMPDIR/DEBIAN/control" 2>/dev/null; then
    echo "Maintainer: Astrometrics Team <noreply@astrometrics.app>" >> "$TMPDIR/DEBIAN/control" || true
  fi

  # Modify the desktop file to launch in terminal for debugging
  DESKTOP_FILE=$(find "$TMPDIR" -name "*.desktop" -type f | head -n 1)
  if [ -n "$DESKTOP_FILE" ] && [ -f "$DESKTOP_FILE" ]; then
    echo "Modifying desktop file to run in terminal: $DESKTOP_FILE"
    # Add Terminal=true if not present, or change it to true
    if grep -q "^Terminal=" "$DESKTOP_FILE"; then
      sed -i 's/^Terminal=.*/Terminal=true/' "$DESKTOP_FILE"
    else
      # Add Terminal=true after the Exec line
      sed -i '/^Exec=/a Terminal=true' "$DESKTOP_FILE"
    fi
    echo "Desktop file modified to launch in terminal"
  fi

  # Ensure chrome-sandbox has correct permissions (root:root, 4755)
  # This is critical for Electron to launch with sandbox enabled.
  CHROME_SANDBOX=$(find "$TMPDIR" -name "chrome-sandbox" -type f | head -n 1)
  if [ -n "$CHROME_SANDBOX" ]; then
    echo "Fixing permissions for $CHROME_SANDBOX in package"
    # Use chown/chmod within the TMPDIR - fakeroot will ensure these are preserved in the .deb
    chown root:root "$CHROME_SANDBOX" 2>/dev/null || true
    chmod 4755 "$CHROME_SANDBOX"
  fi

  # Final verification before packaging
  echo "=== Final check of directory structure before dpkg-deb ==="
  echo "TMPDIR structure (top level):"
  ls -R "$TMPDIR" | head -n 50 || true
  echo "Files in /opt/astrometrics:"
  ls -la "$TMPDIR/opt/astrometrics/" || echo "ERROR: /opt/astrometrics not found in TMPDIR!"

  # Rebuild a new deb that includes the wheel and maintainer scripts.
  # Use fakeroot to ensure proper ownership without requiring root privileges.
  MOD_DEB_NAME="${DEB_BASENAME%.deb}-with-backend.deb"
  NEW_DEB_PATH="$DIST_DIR/$MOD_DEB_NAME"

  echo "Building new deb package: $NEW_DEB_PATH"
  fakeroot dpkg-deb -b "$TMPDIR" "$NEW_DEB_PATH"

  echo "=== Verifying /opt/astrometrics is in the rebuilt package ==="
  set +o pipefail
  if dpkg-deb -c "$NEW_DEB_PATH" | grep -q "/opt/astrometrics"; then
    set -o pipefail
    echo "✓ /opt/astrometrics found in rebuilt package"
    echo "Files in /opt/astrometrics:"
    dpkg-deb -c "$NEW_DEB_PATH" | grep "/opt/astrometrics" | head -n 20 || true
  else
    set -o pipefail
    echo "✗ ERROR: /opt/astrometrics NOT found in rebuilt package!"
    echo "This is a critical error that will cause installation failures."
    echo "Full package contents:"
    dpkg-deb -c "$NEW_DEB_PATH" | head -n 100 || true
    exit 1
  fi

  rm -rf "$TMPDIR"
  echo "Created modified DEB with backend at $NEW_DEB_PATH"
  # Update DEB_BASENAME to the modified package
  DEB_BASENAME="$MOD_DEB_NAME"
else
  echo "No backend wheel found to embed; leaving DEB as-is"
fi

# Find backend wheel (if produced)
WHEEL_FILE=$(ls "$ROOT_DIR"/dist/astrometrics-*.whl 2>/dev/null || true)
if [ -n "$WHEEL_FILE" ]; then
  echo "Found backend wheel: $WHEEL_FILE"
  # Already in dist/, no need to copy
  VERSION=$(basename "$WHEEL_FILE" | sed -n 's/.*astrometrics-\(.*\)-py3-none-any.whl/\1/p')
else
  echo "No backend wheel found in dist/ — continuing without it"
  VERSION="unknown"
fi

echo "6) Creating release zip"
ZIP_NAME="$DIST_DIR/astrometrics-${VERSION}-${ARCH}.zip"
pushd "$DIST_DIR" >/dev/null
ZIP_ARGS=()
if [ -n "$WHEEL_FILE" ]; then
  ZIP_ARGS+=("$(basename "$WHEEL_FILE")")
fi
ZIP_ARGS+=("$DEB_BASENAME" "../build/packaging/uninstall_astrometrics.sh" "../assets/orbit.png" "../backend/start_astrometrics.sh" "../pyproject.toml")
# Filter which files actually exist before zipping
VALID_ZIP_ARGS=()
for arg in "${ZIP_ARGS[@]}"; do
  if [ -e "$DIST_DIR/$arg" ] || [ -e "$DIST_DIR/../$arg" ]; then
    VALID_ZIP_ARGS+=("$arg")
  else
    echo "Warning: Skipping missing zip argument: $arg"
  fi
done

echo "Creating $ZIP_NAME containing: ${VALID_ZIP_ARGS[*]}"
zip -r "$ZIP_NAME" "${VALID_ZIP_ARGS[@]}"
popd >/dev/null

echo "Packaging complete. Output in $DIST_DIR"

exit 0
