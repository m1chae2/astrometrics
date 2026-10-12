#!/usr/bin/env bash
# watch_library_uploads.sh
#
# Purpose: start an upload of the local image library to Google Drive soon
# after the library changes.
#
# It watches the library folder (the frames_path setting) for new, changed,
# moved and deleted files. It does not upload the moment a file appears:
# frames arrive over minutes or hours and Siril writes large files bit by bit.
# Instead it waits until the library has been quiet for QUIET_SECONDS
# (default 300), or until changes have been waiting MAX_WAIT_SECONDS
# (default 1800) during a long ingest, then starts the upload service. Hidden
# files and half-written temporary files are ignored.
#
# Run by the systemd user service astrometrics-library-watch.service. A
# timer also runs the upload every 4 hours, which catches anything this
# watcher missed (for example changes made while it was not running).
#
# Env overrides (mostly for testing): LIBRARY_PATH, QUIET_SECONDS,
# MAX_WAIT_SECONDS, UPLOAD_COMMAND, UPLOAD_BUSY_COMMAND.
set -uo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/../.." && pwd)"
PYTHON="$ROOT_DIR/.venv/bin/python"
QUIET_SECONDS="${QUIET_SECONDS:-300}"
MAX_WAIT_SECONDS="${MAX_WAIT_SECONDS:-1800}"
UPLOAD_COMMAND="${UPLOAD_COMMAND:-systemctl --user start --no-block astrometrics-library-upload.service}"
UPLOAD_BUSY_COMMAND="${UPLOAD_BUSY_COMMAND:-systemctl --user is-active --quiet astrometrics-library-upload.service}"

if [ -z "${LIBRARY_PATH:-}" ]; then
  LIBRARY_PATH="$(cd "$ROOT_DIR" && "$PYTHON" -c \
    'from astrometricslib.foundation.config import get_configuration; print(get_configuration().get_frames_path())' \
    2>/dev/null | tail -n 1)"
fi
if [ -z "$LIBRARY_PATH" ] || [ ! -d "$LIBRARY_PATH" ]; then
  echo "Library folder not found: '${LIBRARY_PATH}'. Not watching." >&2
  exit 1
fi
if ! command -v inotifywait >/dev/null 2>&1; then
  echo "inotifywait is not installed (sudo apt install inotify-tools)." >&2
  exit 1
fi

echo "Watching $LIBRARY_PATH (quiet ${QUIET_SECONDS}s, at most ${MAX_WAIT_SECONDS}s between uploads while busy)."

# Hidden files and folders, and temporary names used while a file is written.
IGNORED_PATH_PATTERN='(/\.|\.partial$|~tmp~)'

pending=0
first_change=0
last_change=0

while true; do
  if IFS= read -r -t 15 event_line; then
    # inotifywait's --exclude still reports the creation of a hidden folder
    # itself, so filter the path again here.
    changed_path="${event_line#* }"
    if ! [[ "$changed_path" =~ $IGNORED_PATH_PATTERN ]]; then
      now="$(date +%s)"
      if [ "$pending" -eq 0 ]; then first_change="$now"; fi
      pending=1
      last_change="$now"
    fi
  else
    read_status=$?
    # A status above 128 is the 15-second timeout. Anything else means
    # inotifywait stopped; exit so systemd restarts the watcher.
    if [ "$read_status" -le 128 ]; then
      echo "The file watcher stopped. Exiting so the service restarts." >&2
      exit 1
    fi
  fi
  # Checked after every event as well as every timeout, so a steady stream of
  # new frames still uploads at least every MAX_WAIT_SECONDS.
  if [ "$pending" -eq 1 ]; then
    now="$(date +%s)"
    if [ $((now - last_change)) -ge "$QUIET_SECONDS" ] || [ $((now - first_change)) -ge "$MAX_WAIT_SECONDS" ]; then
      if eval "$UPLOAD_BUSY_COMMAND"; then
        : # An upload is still running; try again after it finishes.
      else
        echo "Library changed; starting an upload."
        eval "$UPLOAD_COMMAND" || echo "Could not start the upload." >&2
        pending=0
      fi
    fi
  fi
done < <(inotifywait -m -r -q \
  -e close_write -e moved_to -e moved_from -e create -e delete \
  --exclude "$IGNORED_PATH_PATTERN" \
  --format '%e %w%f' "$LIBRARY_PATH")
