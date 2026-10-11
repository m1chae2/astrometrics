#!/usr/bin/env bash
set -euo pipefail

# install_backup_timer.sh
# Installs the Astrometrics backup service and timer as systemd user units
# and enables the timer. Run once; running it again just refreshes the files.
#
# Usage:
#   build/linux/install_backup_timer.sh

SOURCE_DIR="$(cd "$(dirname "$0")" && pwd)/systemd"
TARGET_DIR="$HOME/.config/systemd/user"

mkdir -p "$TARGET_DIR"
# The template names the repository as %h/Repos/astrometrics; write in where
# this copy of the repository really is.
REPO_DIR="$(cd "$SOURCE_DIR/../../.." && pwd)"
sed "s|%h/Repos/astrometrics|$REPO_DIR|g" "$SOURCE_DIR/astrometrics-backup.service" > "$TARGET_DIR/astrometrics-backup.service"
cp "$SOURCE_DIR/astrometrics-backup.timer" "$TARGET_DIR/"
systemctl --user daemon-reload
systemctl --user enable --now astrometrics-backup.timer
echo "Installed. Check with: systemctl --user list-timers astrometrics-backup.timer"
