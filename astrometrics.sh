#!/usr/bin/env bash
set -euo pipefail

# astrometrics.sh
# Root-level convenience wrapper so the app can be launched with
# `./astrometrics.sh` instead of the full path to the real script.
# Forwards all arguments unchanged; see build/linux/run_astrometrics.sh
# for the supported subcommands (start, stop, restart, status, foreground).

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec "$ROOT_DIR/build/linux/run_astrometrics.sh" "$@"
