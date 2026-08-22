#!/usr/bin/env bash
set -euo pipefail

# build_astrometrics.sh
# Lightweight wrapper to produce a .deb for Astrometrics.
# Delegates to scripts/package_app.sh which performs the full packaging flow.
# Usage: ./build_astrometrics.sh [--arch x64|arm64|armv7l] [--skip-backend]

ROOT_DIR="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT_DIR"

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
      echo "Unknown arg: $1" >&2
      echo "Usage: $0 [--arch x64|arm64|armv7l] [--skip-backend]" >&2
      exit 1
      ;;
  esac
done

echo "Building Astrometrics (.deb) for arch=$ARCH skip-backend=$SKIP_BACKEND"

if [ ! -x "$ROOT_DIR/build/packaging/package_app.sh" ]; then
  echo "Missing scripts/packaging/package_app.sh (expected to drive electron-forge packaging)" >&2
  exit 1
fi

if [ "$SKIP_BACKEND" -eq 1 ]; then
  "$ROOT_DIR/build/packaging/package_app.sh" --arch "$ARCH" --skip-backend
else
  "$ROOT_DIR/build/packaging/package_app.sh" --arch "$ARCH"
fi

echo "build_astrometrics: done. Check $ROOT_DIR/dist for the generated .deb and zip artifacts."
