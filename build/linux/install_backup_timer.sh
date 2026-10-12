#!/usr/bin/env bash
set -euo pipefail

# install_backup_timer.sh
# Installs the Astrometrics backup units as systemd user units and enables
# them. Run once; running it again just refreshes the files.
#
#   astrometrics-backup.timer          database backup + Drive integrity
#                                      check, 10 min after login, then every 4 h
#   astrometrics-library-watch.service uploads library changes to Drive once
#                                      they settle (needs inotify-tools)
#   astrometrics-library-upload.timer  library upload safety net, 5 min after
#                                      login, then every 4 h
#
# Usage:
#   build/linux/install_backup_timer.sh

SOURCE_DIR="$(cd "$(dirname "$0")" && pwd)/systemd"
TARGET_DIR="$HOME/.config/systemd/user"

if ! command -v inotifywait >/dev/null 2>&1; then
  echo "inotifywait is missing. Install it first: sudo apt install inotify-tools" >&2
  exit 1
fi

mkdir -p "$TARGET_DIR"
# The templates name the repository as %h/Repos/astrometrics; write in where
# this copy of the repository really is.
REPO_DIR="$(cd "$SOURCE_DIR/../../.." && pwd)"
for unit in astrometrics-backup.service astrometrics-library-upload.service astrometrics-library-watch.service; do
  sed "s|%h/Repos/astrometrics|$REPO_DIR|g" "$SOURCE_DIR/$unit" > "$TARGET_DIR/$unit"
done
for unit in astrometrics-backup.timer astrometrics-library-upload.timer; do
  cp "$SOURCE_DIR/$unit" "$TARGET_DIR/"
done
systemctl --user daemon-reload
systemctl --user enable --now astrometrics-backup.timer astrometrics-library-upload.timer astrometrics-library-watch.service
echo "Installed. Check with:"
echo "  systemctl --user list-timers 'astrometrics-*'"
echo "  systemctl --user status astrometrics-library-watch.service"
