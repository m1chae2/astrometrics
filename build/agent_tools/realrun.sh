#!/bin/bash
# Run a real-data script against COPIES of the library only. Usage:
#   realrun.sh <checkout> <script.py> [args...]
#
# The script must be a .py file inside ASTROMETRICS_SCRATCH and should have an
# `if __name__ == "__main__":` guard (the frame job's worker processes need it). It runs with
# ASTROMETRICS_CONFIG_PATH set to ASTROMETRICS_COPY_CONFIG and PYTHONPATH set to the checkout, in
# a heavy-work slot. The run is refused when the copy config's library or stacks folder lies
# outside ASTROMETRICS_SCRATCH, so a mistaken config cannot reach the real library.
#
# Required environment variables:
#   ASTROMETRICS_SCRATCH       the folder that holds the copies and the scripts
#   ASTROMETRICS_COPY_CONFIG   the copy's config file, inside ASTROMETRICS_SCRATCH
source "$(dirname "${BASH_SOURCE[0]}")/common.sh" "$1" || exit 2
: "${ASTROMETRICS_SCRATCH:?set ASTROMETRICS_SCRATCH to the folder that holds the copies}"
: "${ASTROMETRICS_COPY_CONFIG:?set ASTROMETRICS_COPY_CONFIG to the copy config file}"
SCRATCH="$(realpath "$ASTROMETRICS_SCRATCH")"
CONFIG="$(realpath "$ASTROMETRICS_COPY_CONFIG")"
script="$(realpath "$2")"
shift 2

case "$script" in "$SCRATCH"/*.py) ;; *) echo "the script must be a .py file in $SCRATCH" >&2; exit 2 ;; esac
case "$CONFIG" in "$SCRATCH"/*) ;; *) echo "the copy config must be in $SCRATCH" >&2; exit 2 ;; esac
for key in path stacks_path; do
  value=$(grep -m1 "^$key = " "$CONFIG" | cut -d'"' -f2)
  case "$value" in "$SCRATCH"/*) ;; *) echo "the copy config's $key is not in $SCRATCH: $value" >&2; exit 2 ;; esac
done

ASTROMETRICS_CONFIG_PATH="$CONFIG" PYTHONPATH="$WT" heavy "$PY/python" "$script" "$@"
