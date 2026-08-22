#!/usr/bin/env bash
#
# fetch_aladin.sh
# Download Aladin Lite v3 offline assets into the local public directory.
# REQ: PLN-2.1
#

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/../.." && pwd)"
OUT_DIR="$ROOT_DIR/public/aladin"
ALADIN_BASE="https://aladin.cds.unistra.fr/AladinLite/api/v3/latest"

mkdir -p "$OUT_DIR"
echo "Fetching Aladin Lite v3 assets into $OUT_DIR..."
curl -fSL "$ALADIN_BASE/aladin.js" -o "$OUT_DIR/aladin.js"
echo "Aladin Lite offline assets downloaded successfully."
