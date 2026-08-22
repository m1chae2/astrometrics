#!/bin/bash

# Manual uninstaller for machines that installed the .deb directly rather
# than through `apt remove`/`dpkg --purge`. Mirrors what
# build/packaging/postrm.sh does automatically on a dpkg purge, plus the
# Electron frontend directory, which dpkg would otherwise clean up itself.

# Check if the script is run as root
if [ "$EUID" -ne 0 ]; then
    echo "Please run as root or use sudo"
    exit 1
fi

SERVICE_NAME="astrometrics-backend.service"
UNIT_PATH="/lib/systemd/system/$SERVICE_NAME"
WRAPPER="/usr/local/bin/astrometrics-backend"

# Stop and disable the backend systemd service installed by postinst.sh, if present
if command -v systemctl >/dev/null 2>&1; then
    echo "Stopping and disabling $SERVICE_NAME"
    systemctl stop "$SERVICE_NAME" || true
    systemctl disable "$SERVICE_NAME" || true
fi
echo "Removing systemd unit $UNIT_PATH"
rm -f "$UNIT_PATH" || true
command -v systemctl >/dev/null 2>&1 && systemctl daemon-reload || true

# Remove the backend wrapper installed by postinst.sh
echo "Removing backend wrapper $WRAPPER"
rm -f "$WRAPPER" || true

# Remove the backend staging directory (wheel, venv, start script)
echo "Removing /opt/astrometrics directory"
rm -rf /opt/astrometrics

# Remove the Electron frontend app, installed as /opt/Astrometrics by the
# electron-forge deb maker (see forge.config.js productName)
echo "Removing /opt/Astrometrics directory"
rm -rf /opt/Astrometrics

# Remove the desktop entry
echo "Removing desktop entry"
rm -f /usr/share/applications/astrometrics.desktop || true

# Remove the system user created for the backend service, if present
if id -u astrometrics >/dev/null 2>&1; then
    echo "Removing system user 'astrometrics'"
    userdel astrometrics || true
fi

echo "Astrometrics has been uninstalled."
