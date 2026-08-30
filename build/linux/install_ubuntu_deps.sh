#!/usr/bin/env bash
set -euo pipefail

# install_ubuntu_deps.sh
# Installs the system-level dependencies required for Astrometrics on Ubuntu.
#
# Usage:
#   sudo ./build/linux/install_ubuntu_deps.sh
#   sudo ./build/linux/install_ubuntu_deps.sh --siril-source=flatpak
#
# Options:
#   --siril-source=apt|flatpak
#       Where to install Siril (the external stacking engine) from.
#       apt (default): Ubuntu's archive package -- simplest, no extra
#         runtime, but frozen at whatever version the archive carries.
#       flatpak: installs flatpak itself, adds the Flathub remote, and
#         installs org.siril.Siril from there, which tracks upstream's
#         latest release far more closely than Ubuntu's archive.
#   --help, -h
#       Show this help message.

SIRIL_SOURCE="apt"
for arg in "$@"; do
  case "$arg" in
    --siril-source=apt|--siril-source=flatpak)
      SIRIL_SOURCE="${arg#--siril-source=}"
      ;;
    --help|-h)
      sed -n '2,17p' "$0" | sed 's/^# \{0,1\}//'
      exit 0
      ;;
    *)
      echo "Unknown argument: $arg" >&2
      exit 1
      ;;
  esac
done

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

echo "=== Installing Siril (source: $SIRIL_SOURCE) ==="
if [ "$SIRIL_SOURCE" = "flatpak" ]; then
  sudo apt-get install -y flatpak
  sudo flatpak remote-add --if-not-exists flathub https://flathub.org/repo/flathub.flatpakrepo
  sudo flatpak install -y flathub org.siril.Siril
  SIRIL_EXECUTABLE_HINT="flatpak run --command=siril-cli org.siril.Siril"
else
  sudo apt-get install -y siril
  SIRIL_EXECUTABLE_HINT="siril-cli"
fi

echo "=== Dependencies installed successfully! ==="
echo "You can now run ./build/linux/setup_venv.sh to create the virtual environment."
echo ""
echo "Set siril_executable under [Processing.Siril] in astrometrics.config to:"
echo "  $SIRIL_EXECUTABLE_HINT"
echo "(the plain 'siril'/'flatpak run org.siril.Siril' GUI entry point needs a"
echo "display even for headless stacking; the -cli command does not.)"
