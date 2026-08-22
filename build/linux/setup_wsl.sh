#!/usr/bin/env bash
set -euo pipefail

# setup_wsl.sh
# Installs required system packages for Astrometrics in WSL Ubuntu
# Run as root: sudo ./build/linux/setup_wsl.sh

echo "=========================================="
echo "Astrometrics WSL Setup"
echo "=========================================="
echo ""

# Update package list
echo "Updating package list..."
apt update

# Install required packages
echo "Installing required packages..."
apt install -y \
    nodejs \
    npm \
    python3-pip \
    dos2unix \
    build-essential \
    python3-dev

echo ""
echo "WSL setup complete!"
echo "You can now run the Astrometrics scripts."
