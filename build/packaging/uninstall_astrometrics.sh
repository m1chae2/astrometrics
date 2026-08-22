#!/bin/bash

# Check if the script is run as root
if [ "$EUID" -ne 0 ]; then
    echo "Please run as root or use sudo"
    exit 1
fi

# Remove the astrometrics directory
echo "Removing /opt/astrometrics directory"
sudo rm -rf /opt/astrometrics

# Remove the desktop entry
echo "Removing desktop entry"
sudo rm /usr/share/applications/astrometrics.desktop || true

# Remove the symbolic link
echo "Removing symbolic link for astrometrics command"
sudo rm /usr/local/bin/astrometrics || true

echo "Astrometrics has been uninstalled."
