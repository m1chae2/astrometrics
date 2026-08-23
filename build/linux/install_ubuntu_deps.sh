#!/usr/bin/env bash
set -euo pipefail

# install_ubuntu_deps.sh
# Installs the system-level dependencies required for Astrometrics on Ubuntu.
# Usage: sudo ./build/linux/install_ubuntu_deps.sh

echo "=== Installing prerequisites ==="
# add-apt-repository lives in software-properties-common, which is not
# guaranteed to be present on a minimal/fresh Ubuntu install.
sudo apt-get update
sudo apt-get install -y software-properties-common

echo "=== Adding package repositories ==="
# Add deadsnakes PPA for Python 3.14
sudo add-apt-repository -y ppa:deadsnakes/ppa

# Add INDI PPA for observatory control
sudo add-apt-repository -y ppa:mutlaqja/ppa

echo "=== Updating package lists ==="
sudo apt-get update

echo "=== Installing dependencies ==="
sudo apt-get install -y \
  python3.14 \
  python3.14-venv \
  build-essential \
  swig \
  libcfitsio-dev \
  libnova-dev \
  libdbus-1-dev \
  libglib2.0-dev \
  indi-full \
  libindi-dev

echo "=== Dependencies installed successfully! ==="
echo "You can now run ./build/linux/setup_venv.sh to create the virtual environment."
