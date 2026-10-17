#!/usr/bin/env bash
set -euo pipefail

# install_ubuntu_deps.sh
# Installs the system-level dependencies required for Astrometrics on Ubuntu:
# Python 3.14, Node.js 24 with npm, the INDI telescope-control libraries
# (version 2 or newer), Siril, and optionally a local plate solver.
#
# Third-party package archives (PPAs) often lag behind a new Ubuntu release.
# This script adds a PPA only if it publishes packages for the release that is
# running, and it prefers Ubuntu's own packages where they are new enough.
# Run with --help for the available options.

usage() {
  cat <<'EOF'
install_ubuntu_deps.sh
Installs the system-level dependencies required for Astrometrics on Ubuntu.

Usage:
  sudo ./build/linux/install_ubuntu_deps.sh
  sudo ./build/linux/install_ubuntu_deps.sh --siril-source=flatpak --with-local-solver
  sudo ./build/linux/install_ubuntu_deps.sh --indi-series=resolute

Options:
  --siril-source=apt|flatpak    (default: apt)
      Where to install Siril, the external program that does the stacking.
      apt: Ubuntu's own package. Simplest, and needs no extra runtime, but
        it stays at whatever version Ubuntu ships.
      flatpak: installs flatpak, adds the Flathub repository, then installs
        org.siril.Siril from it. This tracks Siril's latest release much
        more closely, and is the option that supports weighted stacking
        (stack_weight in astrometrics.config.toml).

  --with-local-solver           (default: off)
      Installs astrometry.net (the solve-field program) so that plate
      solving runs on this machine instead of over the internet. Leave it
      off if you would rather use the online astrometry.net service, which
      needs an api_key under [Processing.Astrometry.Online Solver] in
      astrometrics.config.toml. Plate solving needs one or the other.
      The Tycho-2 index files this installs are sized for the camera and
      telescope in the config template (a ZWO ASI533MM Pro at 405mm focal
      length, which sees about 1.6 degrees of sky). A different camera or
      telescope sees a different amount of sky and needs index files sized
      to match, listed by field-of-view size at
      https://data.astrometry.net or via `apt-cache search astrometry-data`.

  --indi-series=<codename>      (default: this Ubuntu release)
      Which release's build of the INDI PPA (ppa:mutlaqja/ppa) to use. INDI
      is the telescope-control software, and Astrometrics needs version 2 or
      newer. A brand-new Ubuntu release often has no INDI build yet. In that
      case, run again with the codename of the previous release (for example
      --indi-series=resolute, Ubuntu 26.04, on Ubuntu 26.10). This usually works for
      neighboring releases, but it is not guaranteed. The other way out is
      to build INDI from source: https://github.com/indilib/indi

  --help, -h
      Show this help message.
EOF
}

SIRIL_SOURCE="apt"
WITH_LOCAL_SOLVER=0
INDI_SERIES=""
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
    --indi-series=?*)
      INDI_SERIES="${arg#--indi-series=}"
      if ! [[ "$INDI_SERIES" =~ ^[a-z]+$ ]]; then
        echo "Invalid value for --indi-series: '$INDI_SERIES'." >&2
        echo "Give an Ubuntu codename in lower case, such as 'resolute'." >&2
        exit 1
      fi
      ;;
    --indi-series=*)
      echo "--indi-series needs a codename, such as --indi-series=resolute." >&2
      exit 1
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

# The codename of the Ubuntu release that is running, such as "noble".
# shellcheck source=/dev/null
UBUNTU_CODENAME="$(. /etc/os-release && echo "${VERSION_CODENAME:-}")"
if [ -z "$UBUNTU_CODENAME" ]; then
  echo "Could not read VERSION_CODENAME from /etc/os-release." >&2
  exit 1
fi

# ppa_publishes_series <owner/name> <codename>
# Succeeds if the PPA has packages for that Ubuntu release. Launchpad serves
# a Release file only for releases the PPA has built for, so an HTTP error
# here means "no build for this release".
ppa_publishes_series() {
  curl -fsIL -o /dev/null \
    "https://ppa.launchpadcontent.net/$1/ubuntu/dists/$2/Release"
}

# The version apt would install for a package, or nothing if there is none.
apt_candidate() {
  local candidate
  candidate="$(apt-cache policy "$1" | awk '/Candidate:/ {print $2}')"
  if [ "$candidate" != "(none)" ]; then
    echo "$candidate"
  fi
}

# The version of a package that is installed, or nothing if it is not.
installed_version() {
  dpkg-query -W -f='${Version}' "$1" 2>/dev/null || true
}

echo "=== Installing prerequisites ==="
# add-apt-repository lives in software-properties-common, which is not
# guaranteed to be present on a minimal/fresh Ubuntu install. curl is used
# to check which releases a PPA supports. INDI's packages call adduser while
# they install, and a minimal system may not have it.
sudo apt-get update
sudo apt-get install -y software-properties-common curl ca-certificates adduser

echo "=== Adding package repositories ==="
# Python 3.14: Ubuntu 26.04 and later ship it themselves. Only older releases
# need the deadsnakes PPA.
PYTHON_CANDIDATE="$(apt_candidate python3.14)"
if [ -n "$PYTHON_CANDIDATE" ]; then
  echo "python3.14 ($PYTHON_CANDIDATE) is in Ubuntu's own archive; not adding deadsnakes."
elif ppa_publishes_series deadsnakes/ppa "$UBUNTU_CODENAME"; then
  sudo add-apt-repository -y -n ppa:deadsnakes/ppa
else
  echo "Python 3.14 is not in Ubuntu's archive for '$UBUNTU_CODENAME', and the" >&2
  echo "deadsnakes PPA has no build for it either. Install Python 3.14 another" >&2
  echo "way (for example from source), then run this script again." >&2
  exit 1
fi

# INDI for observatory control. Astrometrics needs INDI 2 or newer, and
# Ubuntu's own archive can carry INDI 1.x, so the PPA is the normal source.
INDI_SERIES="${INDI_SERIES:-$UBUNTU_CODENAME}"
INDI_FROM_PPA=0
INDI_KEPT_VERSION=""
if ppa_publishes_series mutlaqja/ppa "$INDI_SERIES"; then
  # -n skips the package-list update, which we do once below. The file it
  # writes is named after the running release, so point that one file at
  # INDI_SERIES. Older files from earlier releases are left as they are.
  sudo add-apt-repository -y -n ppa:mutlaqja/ppa
  sudo sed -i "s/^Suites: .*/Suites: $INDI_SERIES/" \
    "/etc/apt/sources.list.d/mutlaqja-ubuntu-ppa-$UBUNTU_CODENAME.sources"
  INDI_FROM_PPA=1
  if [ "$INDI_SERIES" != "$UBUNTU_CODENAME" ]; then
    echo "Using the INDI PPA build for '$INDI_SERIES' on '$UBUNTU_CODENAME'."
    echo "This usually works for neighboring releases but is not guaranteed."
  fi
else
  INDI_KEPT_VERSION="$(installed_version libindi-dev)"
  if [ -n "$INDI_KEPT_VERSION" ] && dpkg --compare-versions "$INDI_KEPT_VERSION" ge 2; then
    echo "The INDI PPA has no build for '$INDI_SERIES'. Keeping the installed"
    echo "libindi-dev $INDI_KEPT_VERSION (INDI 2 or newer)."
  else
    INDI_KEPT_VERSION=""
    echo "The INDI PPA (ppa:mutlaqja/ppa) has no build for '$INDI_SERIES'." >&2
  fi
fi

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
  nodejs \
  npm

# Ubuntu's nodejs is only new enough on recent releases. The app needs 24.
NODE_MAJOR="$(node -v | sed 's/^v//; s/\..*//')"
if [ "$NODE_MAJOR" -lt 24 ]; then
  echo "Installed Node.js is version $(node -v), but the app needs 24 or newer." >&2
  echo "Install Node.js 24 from NodeSource (https://github.com/nodesource/distributions)" >&2
  echo "or see build/linux/setup_wsl.sh, then run the rest of the setup." >&2
  exit 1
fi

echo "=== Installing INDI ==="
if [ "$INDI_FROM_PPA" = "1" ]; then
  # Newer PPA builds have no meta-package called indi-full. They split it
  # into indi-bin (the server and core drivers, which the app needs) and
  # indi-3rdparty-drivers (drivers for real hardware, which are optional).
  INDI_DRIVER_PACKAGE=""
  if [ -n "$(apt_candidate indi-full)" ]; then
    INDI_PACKAGES="indi-full libindi-dev"
  else
    INDI_PACKAGES="indi-bin libindi-dev"
    INDI_DRIVER_PACKAGE="indi-3rdparty-drivers"
  fi
  # On the amd64v3 flavor of Ubuntu, a plain package name picks that flavor,
  # but the PPA ships its development files only for plain amd64.
  if [ "$(dpkg --print-architecture)" = "amd64v3" ]; then
    INDI_PACKAGES="${INDI_PACKAGES// /:amd64 }:amd64"
  fi
  # The series may have a Release file but no INDI 2 packages in it.
  INDI_CANDIDATE="$(apt_candidate libindi-dev)"
  if [ -z "$INDI_CANDIDATE" ] || ! dpkg --compare-versions "$INDI_CANDIDATE" ge 2; then
    echo "The INDI PPA's '$INDI_SERIES' build offers no INDI 2 packages for this computer" >&2
    echo "(libindi-dev candidate: '${INDI_CANDIDATE:-none}')." >&2
    echo "Try another series, such as --indi-series=resolute, or build INDI from" >&2
    echo "source: https://github.com/indilib/indi" >&2
    exit 1
  fi
  # shellcheck disable=SC2086  # the list is meant to split into words
  sudo apt-get install -y $INDI_PACKAGES
  # A PPA build made for an older Ubuntu may want libraries the newer one no
  # longer has. That should not stop the setup, so only warn.
  if [ -n "$INDI_DRIVER_PACKAGE" ] && ! sudo apt-get install -y "$INDI_DRIVER_PACKAGE"; then
    echo "WARNING: $INDI_DRIVER_PACKAGE could not be installed, so the drivers for" >&2
    echo "real hardware are missing. The INDI server, the simulators and the" >&2
    echo "development files are installed, which is enough to build the app." >&2
  fi
elif [ -z "$INDI_KEPT_VERSION" ]; then
  echo "INDI 2 or newer is required, and none could be installed:" >&2
  echo "  - Run this script again with --indi-series=<codename> to use the PPA's" >&2
  echo "    build for an earlier release, for example --indi-series=resolute." >&2
  echo "    This usually works for adjacent releases but is not guaranteed." >&2
  echo "  - Or build INDI from source: https://github.com/indilib/indi" >&2
  exit 1
fi

# apt can quietly fall back to Ubuntu's older INDI 1.x. Check what we got,
# because the failure would otherwise show up much later, when pyindi-client
# cannot load against the wrong library.
INDI_VERSION="$(installed_version libindi-dev)"
if [ -z "$INDI_VERSION" ] || ! dpkg --compare-versions "$INDI_VERSION" ge 2; then
  echo "libindi-dev is version '${INDI_VERSION:-not installed}', but pyindi-client needs INDI 2 or newer." >&2
  echo "Use --indi-series=<codename> for a PPA build that has INDI 2, or build" >&2
  echo "INDI from source: https://github.com/indilib/indi" >&2
  exit 1
fi
echo "libindi-dev $INDI_VERSION (INDI 2 or newer, as required)."

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
echo "This script does not edit astrometrics.config.toml for you. After"
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
  echo "  astrometrics.config.example.toml. If yours differ, install index files"
  echo "  matching how much sky your setup actually sees"
  echo "  (apt-cache search astrometry-data)."
else
  echo "  No local solver was installed. Set api_key under"
  echo "  [Processing.Astrometry.Online Solver] to use the online"
  echo "  astrometry.net service, or re-run this script with"
  echo "  --with-local-solver to solve on this machine instead."
fi
