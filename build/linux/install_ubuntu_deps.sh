#!/usr/bin/env bash
set -euo pipefail

# install_ubuntu_deps.sh
# Installs the system-level dependencies required for Astrometrics on Ubuntu.
#
# Usage:
#   sudo ./build/linux/install_ubuntu_deps.sh
#   sudo ./build/linux/install_ubuntu_deps.sh --siril-source=flatpak --with-local-solver
#
# Options:
#   --siril-source=apt|flatpak
#       Where to install Siril (the external stacking engine) from.
#       apt (default): Ubuntu's archive package -- simplest, no extra
#         runtime, but frozen at whatever version the archive carries.
#       flatpak: installs flatpak itself, adds the Flathub remote, and
#         installs org.siril.Siril from there, which tracks upstream's
#         latest release far more closely than Ubuntu's archive.
#   --with-local-solver
#       Off by default -- skip this if you're using the online
#       astrometry.net API instead (set api_key under
#       [Processing.Astrometry.Online Solver] in astrometrics.config).
#       Installs astrometry.net (the solve-field binary) plus Tycho-2
#       index files sized for the config template's default rig (a
#       ZWO ASI533MM Pro at 405mm focal length, ~1.6 degree field of
#       view). A different camera/telescope needs different index
#       scales -- see index files sized by field-of-view at
#       https://data.astrometry.net or `apt-cache search astrometry-data`.
#   --help, -h
#       Show this help message.

SIRIL_SOURCE="apt"
WITH_LOCAL_SOLVER=0
for arg in "$@"; do
  case "$arg" in
    --siril-source=apt|--siril-source=flatpak)
      SIRIL_SOURCE="${arg#--siril-source=}"
      ;;
    --with-local-solver)
      WITH_LOCAL_SOLVER=1
      ;;
    --help|-h)
      sed -n '4,30p' "$0" | sed 's/^# \{0,1\}//'
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

if [ "$WITH_LOCAL_SOLVER" = "1" ]; then
  echo "=== Installing local astrometry.net solver ==="
  # Tycho-2 index scales 07-19 span quad diameters from 22' up to 2000',
  # covering the config template's default ~96'-wide field of view (and
  # everything smaller solve-field would want to try below it).
  sudo apt-get install -y \
    astrometry.net \
    astrometry-data-tycho2-07 \
    astrometry-data-tycho2-08 \
    astrometry-data-tycho2-09 \
    astrometry-data-tycho2-10-19
fi

echo "=== Dependencies installed successfully! ==="
echo "You can now run ./build/linux/setup_venv.sh to create the virtual environment."
echo ""
echo "Set siril_executable under [Processing.Siril] in astrometrics.config to:"
echo "  $SIRIL_EXECUTABLE_HINT"
echo "(the plain 'siril'/'flatpak run org.siril.Siril' GUI entry point needs a"
echo "display even for headless stacking; the -cli command does not.)"
if [ "$WITH_LOCAL_SOLVER" = "1" ]; then
  echo ""
  echo "Local plate-solving is ready via [Processing.Astrometry.Local Solver]"
  echo "(index_path = /usr/share/astrometry, already the default). Sized for"
  echo "the default camera/telescope in astrometrics.config.example -- if"
  echo "yours differs, install index files matching your actual field of"
  echo "view instead (apt-cache search astrometry-data)."
fi
