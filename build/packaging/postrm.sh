#!/usr/bin/env bash
set -euo pipefail

# postrm maintainer script
# Called with one argument: remove|purge|upgrade|failed-upgrade
# We will stop/disable the service and during purge remove data and the system user.

echo "[postrm] running postrm with args: $*"

ACTION="${1-}"

SERVICE_NAME="astrometrics-backend.service"
UNIT_PATH="/lib/systemd/system/$SERVICE_NAME"
WRAPPER="/usr/local/bin/astrometrics-backend"
ASTRO_DIR="/opt/astrometrics"

stop_and_disable() {
  if command -v systemctl >/dev/null 2>&1; then
    systemctl stop "$SERVICE_NAME" || true
    systemctl disable "$SERVICE_NAME" || true
    systemctl daemon-reload || true
  fi
}

case "$ACTION" in
  remove|upgrade|failed-upgrade)
    echo "[postrm] remove/upgrade: stopping and disabling service"
    stop_and_disable
    echo "[postrm] removing wrapper $WRAPPER"
    rm -f "$WRAPPER" || true
    echo "[postrm] removing systemd unit $UNIT_PATH"
    rm -f "$UNIT_PATH" || true
    ;;
  purge)
    echo "[postrm] purge: stopping service, removing all files and user"
    stop_and_disable
    echo "[postrm] removing wrapper $WRAPPER"
    rm -f "$WRAPPER" || true
    echo "[postrm] removing systemd unit $UNIT_PATH"
    rm -f "$UNIT_PATH" || true
    echo "[postrm] removing installation dir $ASTRO_DIR"
    rm -rf "$ASTRO_DIR" || true
    # attempt to remove system user if present
    if id -u astrometrics >/dev/null 2>&1; then
      userdel astrometrics || true
    fi
    ;;
  *)
    echo "[postrm] unknown action '$ACTION' — performing basic cleanup"
    stop_and_disable
    rm -f "$WRAPPER" || true
    rm -f "$UNIT_PATH" || true
    ;;
esac

exit 0
