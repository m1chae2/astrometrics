#!/usr/bin/env bash
set -euo pipefail

# setup_siril_catalog.sh
# Sets up Siril's Gaia astrometry catalog, the star file Siril's plate solver
# reads (about 1.5 GB). The file is downloaded from Zenodo, decompressed and
# checked against a pinned size and SHA-256 checksum; a download that does not
# match is refused and deleted. It goes in ~/.local/share/astrometrics/siril.
# The work is done by astrometricslib/scripts/set_up_siril_catalog.py; the pin
# is in astrometricslib/_internal/drivers/siril/core/catalog_setup.py.
#
# Usage:
#   ./build/linux/setup_siril_catalog.sh --check   - report whether the catalog is installed and matches; change nothing
#   ./build/linux/setup_siril_catalog.sh           - download and set it up if it is missing
#
# Run it as your own user, not with sudo: everything it changes is in your
# home folder. A catalog that Siril's own installer made (in ~/.local/share/siril
# or the Flatpak's data folder) and that matches the pin is kept.

ROOT_DIR="$(cd "$(dirname "$0")/../.." && pwd)"

err()  { echo "[setup_siril_catalog] ERROR: $*" >&2; }
die()  { err "$*"; exit 1; }

if [ "$(id -u)" = "0" ]; then
  die "Run this as your own user, not with sudo. It changes only files in your home folder."
fi

PYTHON="$ROOT_DIR/.venv/bin/python"
[ -x "$PYTHON" ] || die "No virtual environment found. Run ./build/linux/setup_venv.sh first."

cd "$ROOT_DIR"
exec env PYTHONPATH="$ROOT_DIR" "$PYTHON" -m astrometricslib.scripts.set_up_siril_catalog "$@"
