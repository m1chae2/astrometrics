#!/usr/bin/env bash
set -euo pipefail

# install_ubuntu_deps.sh
# Installs the system-level dependencies required for Astrometrics on Ubuntu.
# Run with --help for the available options.

usage() {
  cat <<'EOF'
install_ubuntu_deps.sh
Installs the system-level dependencies required for Astrometrics on Ubuntu.

Usage:
  sudo ./build/linux/install_ubuntu_deps.sh
  sudo ./build/linux/install_ubuntu_deps.sh --siril-source=flatpak --with-local-solver

Options:
  --siril-source=apt|flatpak    (default: apt)
      Where to install Siril, the external program that does the stacking.
      apt: Ubuntu's own package. Simplest, and needs no extra runtime, but
        it stays at whatever version Ubuntu ships.
      flatpak: installs flatpak, adds the Flathub repository, then installs
        org.siril.Siril from it. This tracks Siril's latest release much
        more closely, and is the option that supports weighted stacking
        (stack_weight in astrometrics.config).

  --with-local-solver           (default: off)
      Installs astrometry.net (the solve-field program) so that plate
      solving runs on this machine instead of over the internet. Leave it
      off if you would rather use the online astrometry.net service, which
      needs an api_key under [Processing.Astrometry.Online Solver] in
      astrometrics.config. Plate solving needs one or the other.
      The Tycho-2 index files this installs are sized for the camera and
      telescope in the config template (a ZWO ASI533MM Pro at 405mm focal
      length, which sees about 1.6 degrees of sky). A different camera or
      telescope sees a different amount of sky and needs index files sized
      to match, listed by field-of-view size at
      https://data.astrometry.net or via `apt-cache search astrometry-data`.

  --help, -h
      Show this help message.
EOF
}

SIRIL_SOURCE="apt"
WITH_LOCAL_SOLVER=0
for arg in "$@"; do
  case "$arg" in
    --siril-source=apt|--siril-source=flatpak)
      SIRIL_SOURCE="${arg#--siril-source=}"
      ;;
    --siril-source=*)
      echo "Invalid value for --siril-source: '${arg#--siril-source=}'." >&2
      echo "Valid values are 'apt' (default) or 'flatpak'." >&2
      exit 1
      ;;
    --with-local-solver)
      WITH_LOCAL_SOLVER=1
      ;;
    --help|-h)
      usage
      exit 0
      ;;
    *)
      echo "Unknown argument: $arg" >&2
      echo "Run with --help to see the available options." >&2
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
  # Tycho-2 index scales 07-19 span quad diameters from 22 arcminutes up to
  # 2000 arcminutes, covering the roughly 1.6 degree (96 arcminute) field of
  # view of the config template's camera and telescope, plus everything
  # smaller that solve-field would want to try below it.
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
echo "This script does not edit astrometrics.config for you. After"
echo "setup_venv.sh creates it, apply the settings below."
echo ""
echo "Siril (source: $SIRIL_SOURCE), under [Processing.Siril]:"
if [ "$SIRIL_SOURCE" = "flatpak" ]; then
  echo "  siril_executable = $SIRIL_EXECUTABLE_HINT"
  echo "      (the template ships the apt value, so this one needs changing)"
  echo "  stack_weight = wfwhm"
  echo "      (optional; this Siril is new enough to weight frames by star"
  echo "      sharpness, so you can turn it on)"
else
  echo "  siril_executable = $SIRIL_EXECUTABLE_HINT"
  echo "      (already the template default, nothing to change)"
  echo "  stack_weight ="
  echo "      (already blank in the template; leave it blank, because this"
  echo "      Siril is too old to accept the -weight= argument)"
fi
echo ""
echo "The plain 'siril' / 'flatpak run org.siril.Siril' command starts the"
echo "graphical build, which needs a display even when stacking headlessly."
echo "The -cli command does not, which is why it is the one to use."
echo ""
echo "Plate solving, under [Processing.Astrometry...]:"
if [ "$WITH_LOCAL_SOLVER" = "1" ]; then
  echo "  Ready to use via [Processing.Astrometry.Local Solver]"
  echo "  (index_path = /usr/share/astrometry, already the template default)."
  echo "  The index files installed are sized for the camera and telescope in"
  echo "  astrometrics.config.example. If yours differ, install index files"
  echo "  matching how much sky your setup actually sees"
  echo "  (apt-cache search astrometry-data)."
else
  echo "  No local solver was installed. Set api_key under"
  echo "  [Processing.Astrometry.Online Solver] to use the online"
  echo "  astrometry.net service, or re-run this script with"
  echo "  --with-local-solver to solve on this machine instead."
fi
