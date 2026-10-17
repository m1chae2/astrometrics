#!/usr/bin/env bash
set -euo pipefail

# ==============================================================================
# install_desktop_icon.sh
# Purpose:
#   Installs the Astrometrics XDG desktop launcher (.desktop file) and icon into
#   the current user's local desktop environment (~/.local/share/applications).
#   This allows Astrometrics (in development mode) to show up in the Ubuntu 26.10
#   application menu and enables pinning directly to the dock / favorites tray.
# ==============================================================================

# ------------------------------------------------------------------------------
# resolve_directories: locates repository root, icons, and target XDG locations.
# ------------------------------------------------------------------------------
resolve_directories() {
  # The app's freedesktop ID. The AppStream ID, the desktop file name, and the
  # window's Wayland app_id (electron/platforms/linux.js) must all use it.
  APP_ID="io.github.m1chae2.Astrometrics"
  OLD_DESKTOP_NAME="astrometrics.desktop"
  OLD_METAINFO_NAME="astrometrics.metainfo.xml"
  ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
  LAUNCHER_SCRIPT="$ROOT_DIR/astrometrics.sh"
  ICON_SOURCE="$ROOT_DIR/assets/orbit.png"
  MIME_SOURCE="$ROOT_DIR/build/linux/astrometrics-mime.xml"
  THUMBNAILER_SOURCE="$ROOT_DIR/build/linux/astrometrics-fits.thumbnailer"
  METAINFO_SOURCE="$ROOT_DIR/assets/metainfo/$APP_ID.metainfo.xml"
  APPS_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
  ICONS_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/icons/hicolor/512x512/apps"
  MIME_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/mime/packages"
  THUMBNAILERS_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/thumbnailers"
  METAINFO_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/metainfo"
  DESKTOP_TARGET="$APPS_DIR/$APP_ID.desktop"
}

# ------------------------------------------------------------------------------
# verify_prerequisites: ensures the target launcher script and icon exist.
# ------------------------------------------------------------------------------
verify_prerequisites() {
  if [ ! -f "$LAUNCHER_SCRIPT" ]; then
    echo "ERROR: Launcher script not found at $LAUNCHER_SCRIPT" >&2
    exit 1
  fi
  chmod +x "$LAUNCHER_SCRIPT"

  if [ ! -f "$ICON_SOURCE" ]; then
    echo "ERROR: Icon file not found at $ICON_SOURCE" >&2
    exit 1
  fi
}

# ------------------------------------------------------------------------------
# remove_old_launcher_files: deletes the files earlier versions of this script
# installed under the old "astrometrics" names, so the app does not show twice.
# ------------------------------------------------------------------------------
remove_old_launcher_files() {
  if [ -f "$APPS_DIR/$OLD_DESKTOP_NAME" ]; then
    echo "Removing old launcher $APPS_DIR/$OLD_DESKTOP_NAME..."
    rm -f "$APPS_DIR/$OLD_DESKTOP_NAME"
  fi
  if [ -f "$METAINFO_DIR/$OLD_METAINFO_NAME" ]; then
    echo "Removing old metadata $METAINFO_DIR/$OLD_METAINFO_NAME..."
    rm -f "$METAINFO_DIR/$OLD_METAINFO_NAME"
  fi
}

# ------------------------------------------------------------------------------
# install_icon_asset: copies high-resolution app icon to standard user icon theme.
# ------------------------------------------------------------------------------
install_icon_asset() {
  echo "Installing icon to $ICONS_DIR..."
  mkdir -p "$ICONS_DIR"
  cp "$ICON_SOURCE" "$ICONS_DIR/astrometrics.png"
}

# ------------------------------------------------------------------------------
# install_mime_definition: copies FreeDesktop XML and updates user mime database.
# ------------------------------------------------------------------------------
install_mime_definition() {
  if [ -f "$MIME_SOURCE" ]; then
    echo "Installing FITS MIME definition to $MIME_DIR..."
    mkdir -p "$MIME_DIR"
    cp "$MIME_SOURCE" "$MIME_DIR/astrometrics.xml"
    if command -v update-mime-database >/dev/null 2>&1; then
      echo "Updating MIME database..."
      update-mime-database "${XDG_DATA_HOME:-$HOME/.local/share}/mime"
    fi
  fi
}

# ------------------------------------------------------------------------------
# install_thumbnailer: installs GNOME Files / Nautilus FITS thumbnailer.
# ------------------------------------------------------------------------------
install_thumbnailer() {
  if [ -f "$THUMBNAILER_SOURCE" ]; then
    echo "Installing Nautilus FITS thumbnailer to $THUMBNAILERS_DIR..."
    mkdir -p "$THUMBNAILERS_DIR"
    cp "$THUMBNAILER_SOURCE" "$THUMBNAILERS_DIR/astrometrics-fits.thumbnailer"
  fi
}

# ------------------------------------------------------------------------------
# install_metainfo: installs AppStream software catalog metadata.
# ------------------------------------------------------------------------------
install_metainfo() {
  if [ -f "$METAINFO_SOURCE" ]; then
    echo "Installing AppStream metadata to $METAINFO_DIR..."
    mkdir -p "$METAINFO_DIR"
    cp "$METAINFO_SOURCE" "$METAINFO_DIR/$APP_ID.metainfo.xml"
  fi
}

# ------------------------------------------------------------------------------
# write_desktop_entry: generates the .desktop file with dev mode launch actions.
# ------------------------------------------------------------------------------
write_desktop_entry() {
  echo "Generating desktop launcher at $DESKTOP_TARGET..."
  mkdir -p "$APPS_DIR"

  cat > "$DESKTOP_TARGET" <<EOF
[Desktop Entry]
Version=1.0
Type=Application
Name=Astrometrics
GenericName=Astronomy Application
Comment=Astrometrics Astronomy Image Viewer and Analyzer (Development Mode)
Exec=${LAUNCHER_SCRIPT} %F
Path=${ROOT_DIR}
Icon=${ICON_SOURCE}
Terminal=false
Categories=Science;Astronomy;
Keywords=astronomy;astrophotography;fits;planetarium;telescope;spectroscopy;imaging;
StartupWMClass=${APP_ID}
StartupNotify=true
MimeType=image/fits;application/fits;application/x-fits;
Actions=Planetarium;ImageProcessing;Observatory;LAN;Stop;Restart;

[Desktop Action Planetarium]
Name=Open Planetarium
Exec=${LAUNCHER_SCRIPT} --mode=Planetarium

[Desktop Action ImageProcessing]
Name=Open Image Processing
Exec=${LAUNCHER_SCRIPT} --mode="Image Processing"

[Desktop Action Observatory]
Name=Open Observatory Manager
Exec=${LAUNCHER_SCRIPT} --mode="Observatory Manager"

[Desktop Action LAN]
Name=Start with Mobile / LAN Access
Exec=${LAUNCHER_SCRIPT} --lan

[Desktop Action Stop]
Name=Stop Astrometrics Services
Exec=${LAUNCHER_SCRIPT} stop

[Desktop Action Restart]
Name=Restart Astrometrics Services
Exec=${LAUNCHER_SCRIPT} restart
EOF

  chmod +x "$DESKTOP_TARGET"
}

# ------------------------------------------------------------------------------
# validate_and_update_database: verifies desktop syntax and refreshes desktop cache.
# ------------------------------------------------------------------------------
validate_and_update_database() {
  if command -v desktop-file-validate >/dev/null 2>&1; then
    echo "Validating desktop file syntax..."
    desktop-file-validate "$DESKTOP_TARGET"
    echo "Desktop file validation: PASSED"
  fi

  if command -v update-desktop-database >/dev/null 2>&1; then
    echo "Updating desktop application database..."
    update-desktop-database "$APPS_DIR"
  fi
}

# ------------------------------------------------------------------------------
# pin_to_gnome_favorites: puts the launcher into GNOME Shell favorite-apps. An
# old astrometrics.desktop entry is replaced in place so it keeps its position.
# Otherwise the new entry is added at the end.
# ------------------------------------------------------------------------------
pin_to_gnome_favorites() {
  if ! command -v gsettings >/dev/null 2>&1; then
    return 0
  fi

  local current_favorites
  current_favorites=$(gsettings get org.gnome.shell favorite-apps 2>/dev/null || echo "")
  if [ -z "$current_favorites" ] || [[ "$current_favorites" == *"'$APP_ID.desktop'"* ]]; then
    return 0
  fi

  local updated_favorites
  if [[ "$current_favorites" == *"'$OLD_DESKTOP_NAME'"* ]]; then
    echo "Replacing the old Astrometrics dock entry with $APP_ID.desktop..."
    updated_favorites="${current_favorites//\'$OLD_DESKTOP_NAME\'/\'$APP_ID.desktop\'}"
  else
    echo "Pinning Astrometrics to GNOME Shell favorite-apps dock..."
    updated_favorites=$(echo "$current_favorites" | sed "s/]/, '$APP_ID.desktop']/")
  fi
  gsettings set org.gnome.shell favorite-apps "$updated_favorites" 2>/dev/null || true
  echo "Successfully updated the Astrometrics dock entry."
}

# ------------------------------------------------------------------------------
# main: entry point for desktop icon installer.
# ------------------------------------------------------------------------------
main() {
  echo "=== Astrometrics Desktop Icon Installation ==="
  resolve_directories
  verify_prerequisites
  remove_old_launcher_files
  install_icon_asset
  install_mime_definition
  install_thumbnailer
  install_metainfo
  write_desktop_entry
  validate_and_update_database
  pin_to_gnome_favorites
  echo "=== Installation Complete ==="
  echo "Astrometrics is now available in your Application Menu."
}

main "$@"
